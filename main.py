# Vistazo RomM: an add-on for Vistazo. It holds its own RomM key, paired by a short code, to read a
# player's achievements again after a game and, for those who turn it on, to look after RomM
# games itself: what RomM knows of each, its file in RetroDECK's folders and how Steam starts it.
# Vistazo draws what this hands over; nothing here draws anything.
import asyncio
import base64
import concurrent.futures
import datetime
import hashlib
import http.client
import io
import json
import os
import re
import shutil
import sqlite3
import ssl
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import decky


# ── RomM: this add-on's own key ───────────────────────────────────────────────────
# RomM only reads a player's RetroAchievements progress again when asked (or at night), and asking
# needs "modify your profile". Tender's key leaves that out on purpose, so this add-on gets its own,
# with only "view your profile" and "modify your profile", through RomM's pairing code: the user
# makes the key in RomM, presses Pair and types the short code here. The key never leaves this file.
# Looking after the games needs more: to see them, their platforms, their collections and the
# BIOS files they need and, for save games, to read and write the player's own files. A key is
# kept when it has at least the first pair and nothing beyond the whole set; what it lacks of the
# set only decides what it is good for (achievements, or the games too).
KEY_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "romm.json")
SCOPES = {"me.read", "me.write"}
SCOPES_GAMES = SCOPES | {"roms.read", "platforms.read", "collections.read", "firmware.read", "assets.read", "assets.write"}
# Optional on top of either set: the player's own data of each game, where RomM keeps play time.
SCOPES_PLAY = {"roms.user.read", "roms.user.write"}
# Optional too: "modify devices", to register this Deck once and name it on each save and play session.
SCOPES_DEVICE = {"devices.write"}
CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # RomM's pairing alphabet
TIMEOUT = 10
# Decky's Python brings no certificates of its own: the system's are used to check RomM's.
CA_FILES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/ca-certificates/extracted/tls-ca-bundle.pem", "/etc/pki/tls/certs/ca-bundle.crt")


# RomM's address and whether its certificate is checked: the add-on's own, in its settings folder.
# The first time there is none it takes Tender's (same server), and keeps it, so it goes on
# working once Tender is gone. A key is only ever sent to the address it was made for.
SERVER_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "server.json")


def _clean_url(url):
    """The address as kept: http or https, a host, no user or password, no query; no trailing
    slash. None when it is not one."""
    url = str(url or "").strip().rstrip("/")
    try:
        parts = urllib.parse.urlsplit(url)
        parts.port  # a port that is not a number raises here
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    if "?" in url or "#" in url or any(c.isspace() for c in url):
        return None
    # The scheme as every check here reads it: "HTTPS://" is "https://".
    return parts.scheme + url[len(parts.scheme):]


def _own_server():
    try:
        with open(SERVER_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    url = _clean_url(data.get("url")) if isinstance(data, dict) else None
    return {"url": url, "insecure": data.get("insecure") is True and url.startswith("https:")} if url else None


def _server():
    """The add-on's own address; the first time, Tender's, kept from then on."""
    own = _own_server()
    if own:
        return own
    tender = _tender_server()
    if tender and _write_private(SERVER_FILE, tender):
        decky.logger.info("RomM address taken from Tender")
    return tender


def _tender_server():
    """RomM's address and TLS choice as Tender has them (Vistazo reaches the same server). Only
    those two settings are read; Tender's key and headers are left alone."""
    # Tender keeps them in ~/.config/romm-tender; older layouts used Decky's settings folders.
    home = getattr(decky, "DECKY_USER_HOME", os.path.expanduser("~"))
    files = [os.path.join(home, ".config", "romm-tender", "settings.json")]
    root = os.path.join(decky.DECKY_HOME, "settings")
    try:
        files += [os.path.join(root, name, "settings.json") for name in sorted(os.listdir(root))]
    except OSError:
        pass
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "romm_url" not in data or "romm_api_token" not in data:
            continue
        url = _clean_url(data.get("romm_url"))
        if not url:
            return None
        return {"url": url, "insecure": bool(data.get("romm_allow_insecure_ssl", False))}
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """RomM never redirects its API: a redirect is refused, so the key is never sent anywhere else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener(ctx):
    return urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ctx))


def _read_key():
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("token") and data.get("url"):
            return data
    except (OSError, ValueError):
        pass
    return None


def _write_private(path, data):
    """True when the file is safely on disk, for this user only; a half-written copy never stays."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except OSError as e:
        decky.logger.error("%s not saved: %s", os.path.basename(path), e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _write_key(data):
    return _write_private(KEY_FILE, data)


def _key_scopes(key):
    """What a stored key may do. Keys from before the wider set say nothing: they are the small one."""
    scopes = key.get("scopes") if isinstance(key, dict) else None
    return {str(x) for x in scopes} if isinstance(scopes, list) else set(SCOPES)


def _games_key():
    """(server, key) when the stored key is for this server and may look after games, else
    (None, why): "unlinked", "moved" or "limited" (a key for achievements only)."""
    key = _read_key()
    server = _server()
    if not key:
        return None, "unlinked"
    if not server or key["url"] != server["url"]:
        return None, "moved"
    if not _key_scopes(key) >= SCOPES_GAMES:
        return None, "limited"
    return server, key


def _forget_key():
    try:
        os.remove(KEY_FILE)
    except OSError:
        pass


def _prepare(server, method, path, token=None, body=None, accept="application/json", raw=None):
    """The request, its body and the TLS setup for one call to RomM. `raw` is a body already
    made, with its content type: (bytes, type)."""
    req = urllib.request.Request(server["url"] + path, method=method)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", "Vistazo")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    elif raw is not None:
        data = raw[0]
        req.add_header("Content-Type", raw[1])
    ctx = None
    if server["url"].startswith("https:"):
        ctx = ssl.create_default_context(cafile=next((f for f in CA_FILES if os.path.isfile(f)), None))
        if server.get("insecure"):
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
    return req, data, ctx


def _request(server, method, path, token=None, body=None, raw=None):
    """One call to RomM. Returns (status, parsed JSON or None); status 0 when it could not be reached."""
    req, data, ctx = _prepare(server, method, path, token, body, raw=raw)
    try:
        with _opener(ctx).open(req, data=data, timeout=TIMEOUT) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError) as e:
        # The reason only (it never carries the key): enough to tell a certificate from a timeout.
        decky.logger.warning("RomM %s %s not reached: %s", method, path.split("?")[0], getattr(e, "reason", e))
        return 0, None


def _number(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _game(me, ra_id):
    """This game's progress in a /api/users/me answer; zeros for anything unexpected."""
    progression = me.get("ra_progression") if isinstance(me, dict) else None
    results = progression.get("results") if isinstance(progression, dict) else None
    results = [g for g in results if isinstance(g, dict)] if isinstance(results, list) else []
    entry = next((g for g in results if str(g.get("rom_ra_id")) == str(ra_id)), None) or {}
    earned = entry.get("earned_achievements")
    earned = [a for a in earned if isinstance(a, dict)] if isinstance(earned, list) else []
    return {
        "earned": _number(entry.get("num_awarded")),
        "hardcore": _number(entry.get("num_awarded_hardcore")),
        "total": _number(entry.get("max_possible")),
        "ids": [str(a.get("id")) for a in earned if a.get("id") is not None],
        "hardcore_ids": [str(a.get("id")) for a in earned if a.get("id") is not None and a.get("date_hardcore")],
    }


# ── Vistazo's own games ─────────────────────────────────────────────────────────────────────────
# For those who turn it on, Vistazo looks after RomM games itself: what RomM knows of each, its
# file in RetroDECK's folders and how Steam starts it. What it keeps of each game is in one private
# file, by the game's number in Steam. A game starts through the launcher shipped in bin/: Steam
# hands it the game's number in RomM and nothing else, and the launcher finds the file in a private
# list written here (the file's name never goes through Steam's launch options or a shell).
GAMES_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "games.json")
RUNTIME = decky.DECKY_PLUGIN_RUNTIME_DIR
LAUNCHER = os.path.join(RUNTIME, "vistazo-launch")
LAUNCH_FILE = os.path.join(RUNTIME, "launch.json")
LAUNCHER_SOURCE = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "vistazo-launch")
# Where the launcher asks, right before a game starts, for its save to be brought in step with
# RomM (another device may have played since): a socket beside it, for this user only. The
# launcher waits a little longer than LAUNCH_WAIT, after which no save is replaced any more.
LAUNCH_SOCKET = os.path.join(RUNTIME, "launch.sock")
LAUNCH_WAIT = 20
_launch_server = [None]
_released = {}  # appid -> when (time.monotonic()) its launcher was last told to go on and start it
# RomM's platform -> RetroDECK's folder for it. Only the systems in use; more as they are needed.
SYSTEMS = {
    "nds": "nds", "nintendo-ds": "nds",
    "wii": "wii",
    "ps2": "ps2", "playstation-2": "ps2",
    "psp": "psp", "playstation-portable": "psp",
    "3ds": "n3ds", "n3ds": "n3ds", "nintendo-3ds": "n3ds", "new-nintendo-3ds": "n3ds",
}
LIBRARY_PAGE = 250  # games asked for at a time when listing RomM's library
LIBRARY_PAGES = 40
CHUNK = 1024 * 1024
STALL = 60  # seconds without a byte before a download gives up
SPARE = 64 * 1024 * 1024  # room left on the disk after a download

_lock = threading.RLock()  # the games file, between the plugin and its downloads
_saving = set()  # games whose save is being brought in step right now
_jobs = {}  # appid -> {"state", "done", "total", "cancel"}: downloads since the plugin started
# Downloads go one at a time, in the order they were asked for; the others wait as "queued".
_queue = concurrent.futures.ThreadPoolExecutor(max_workers=1)


def _active(job):
    """Whether a download is under way or waiting its turn."""
    return bool(job) and job["state"] in ("queued", "downloading")


def _home():
    return getattr(decky, "DECKY_USER_HOME", os.path.expanduser("~"))


def _roms_root():
    """RetroDECK's games folder: where its own settings say, or its usual place."""
    config = os.path.join(_home(), ".var", "app", "net.retrodeck.retrodeck", "config", "retrodeck", "retrodeck.json")
    try:
        with open(config, encoding="utf-8") as f:
            path = json.load(f).get("paths", {}).get("roms_path", "")
        if isinstance(path, str) and os.path.isabs(path):
            return os.path.realpath(path)
    except (OSError, ValueError, AttributeError):
        pass
    return os.path.realpath(os.path.join(_home(), "retrodeck", "roms"))


def _within(path, folder):
    """The real path when it lies inside the folder (links followed), else None."""
    real = os.path.realpath(path)
    return real if real.startswith(os.path.realpath(folder) + os.sep) else None


def _one_name(name):
    """A file name as a single component, or None when nothing safe is left of it."""
    name = os.path.basename(str(name or "").replace("\\", "/"))
    if not name or name in (".", "..") or "\0" in name or len(name.encode("utf-8")) > 240:
        return None
    return name


def _read_games():
    try:
        with open(GAMES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("games"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "games": {}}


def _start_with(game):
    """RetroDECK's command for the emulator this game starts with, or None to let RetroDECK pick.
    The player's choice first; with none, a console whose saves are kept in step starts with the
    emulator whose saves those are: RetroDECK would otherwise take the first of ES-DE's list (on a
    fresh RetroDECK, DeSmuME for the DS, whose saves are not those)."""
    found = _emulators(_text(game.get("system")))
    chosen = _text(game.get("emulator"))
    if chosen and chosen in found:
        return found[chosen]
    return found.get(SAVE_EMULATORS.get(_text(game.get("system")), ""))


def _write_games(data):
    """The games file and, from it, the launcher's list: game number in RomM -> its file, or
    its file and the emulator it starts with (RetroDECK's own command for that emulator)."""
    if not _write_private(GAMES_FILE, data):
        return False
    table = {}
    for game in data["games"].values():
        if isinstance(game, dict) and game.get("installed") and isinstance(game.get("path"), str):
            command = _start_with(game)
            table[str(game.get("rom_id"))] = {"path": game["path"], "emulator": command} if command else game["path"]
            disc = _disc_path(game)  # another disc chosen: the launcher also checks it is in the game's folder
            if disc:
                table[str(game.get("rom_id"))] = {"path": disc[0], "folder": disc[1], **({"emulator": command} if command else {})}
    return _write_private(LAUNCH_FILE, table)


# ── Emulators ─────────────────────────────────────────────────────────────────────────────────
# RetroDECK starts a game with the emulator ES-DE has for its console, unless told another one:
# the choices are ES-DE's own list (its name for each, and the command RetroDECK runs).
ES_SYSTEMS = os.path.join("retrodeck", "components", "es-de", "share", "es-de", "resources", "systems", "linux", "es_systems.xml")
FLATPAK_FILES = ("/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files",
                 os.path.join(".local", "share", "flatpak", "app", "net.retrodeck.retrodeck", "current", "active", "files"))
_es_cache = {}


_XML_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


def _xml_text(text):
    """Text or an attribute as written in ES-DE's file, with its entities read back.
    (Decky's own Python comes without Python's XML reader.)"""
    def one(m):
        name = m.group(1)
        if name.startswith("#"):
            code = int(name[2:], 16) if name[1:2] in ("x", "X") else int(name[1:])
            return chr(code) if 0 < code < 0x110000 else ""
        return _XML_ENTITIES.get(name, m.group(0))
    return re.sub(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z]+);", one, text)


def _es_systems(text):
    """{console: {name: command}} from ES-DE's es_systems.xml."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    found = {}
    for block in re.findall(r"<system\b[^>]*>(.*?)</system>", text, flags=re.S):
        name = re.search(r"<name>([^<]*)</name>", block)
        if not name:
            continue
        commands = {}
        for attrs, body in re.findall(r"<command\b([^>]*)>([^<]*)</command>", block):
            label = re.search(r"""\blabel\s*=\s*(?:"([^"]*)"|'([^']*)')""", attrs)
            label = _xml_text((label.group(1) if label.group(1) is not None else label.group(2)) if label else "").strip()
            body = _xml_text(body).strip()
            if label and body and len(label) <= 80 and len(body) <= 1000 and "\0" not in body and "\n" not in body:
                commands.setdefault(label, body)
        found.setdefault(_xml_text(name.group(1)).strip(), commands)
    return found


def _emulators(system):
    """ES-DE's emulators for one of RetroDECK's consoles: {name: command}, in its own order."""
    if not system:
        return {}
    for base in FLATPAK_FILES:
        path = os.path.join(_home(), base, ES_SYSTEMS) if not os.path.isabs(base) else os.path.join(base, ES_SYSTEMS)
        try:
            seen = os.stat(path).st_mtime_ns
        except OSError:
            continue
        cached = _es_cache.get(path)
        if not cached or cached[0] != seen:
            try:
                with open(path, encoding="utf-8") as f:
                    found = _es_systems(f.read(4 << 20))
            except (OSError, ValueError) as e:
                decky.logger.warning("ES-DE's list not read: %s", e)
                continue
            cached = _es_cache[path] = (seen, found)
        return dict(cached[1].get(system, {}))
    return {}


def _install_program(source, target):
    """One of the add-on's programs in its own data folder: the plugin's folder is replaced on
    every update, and its files may not be runnable."""
    try:
        with open(source, "rb") as f:
            wanted = f.read()
        try:
            with open(target, "rb") as f:
                same = f.read() == wanted and os.stat(target).st_mode & 0o777 == 0o700
        except OSError:
            same = False
        if not same:
            os.makedirs(RUNTIME, exist_ok=True)
            tmp = target + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700)
            with os.fdopen(fd, "wb") as f:
                f.write(wanted)
            os.chmod(tmp, 0o700)
            os.replace(tmp, target)
        return True
    except OSError as e:
        decky.logger.error("%s not installed: %s", os.path.basename(target), e)
        return False


def _install_launcher():
    """The launcher beside its list (and, where Punktfunk is, the program it starts games with)."""
    if _pf_on():
        _install_program(PLAYER_SOURCE, PLAYER)
    return _install_program(LAUNCHER_SOURCE, LAUNCHER)


def _text(v):
    return v if isinstance(v, str) else ""


# What RomM last said of each game Vistazo looks after and of the player's achievements in it,
# kept so its page still has something to show while RomM is asleep or out of reach.
KEPT_FILE = os.path.join(RUNTIME, "romm-kept.json")
KEPT_MOST = 300
# How long what was kept is good enough to show without asking RomM again: what a game is hardly
# ever changes; a player's achievements change when they play, and then they are asked for anew.
GAME_GOOD = 7 * 24 * 3600
PROGRESS_GOOD = 6 * 3600


def _kept(kind, number):
    try:
        with open(KEPT_FILE, encoding="utf-8") as f:
            entry = json.load(f).get(kind, {}).get(str(number))
        return entry if isinstance(entry, dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def _still_good(kind, number, seconds):
    """What was kept of this, without its date, when it is recent enough; None otherwise."""
    entry = _kept(kind, number)
    at = entry.get("_at") if entry else None
    if not isinstance(at, (int, float)) or not 0 <= time.time() - at < seconds:
        return None
    return {k: v for k, v in entry.items() if k != "_at"}


def _keep(kind, number, entry):
    with _lock:
        try:
            with open(KEPT_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        group = data.get(kind) if isinstance(data.get(kind), dict) else {}
        group.pop(str(number), None)
        group[str(number)] = {**entry, "_at": int(time.time())}  # the newest last
        for old in list(group)[: max(0, len(group) - KEPT_MOST)]:
            del group[old]
        data[kind] = group
        _write_private(KEPT_FILE, data)


def _detail(rom):
    """What the game page shows of RomM's answer for one game; nothing else leaves the backend."""
    meta = rom.get("metadatum") if isinstance(rom.get("metadatum"), dict) else {}
    ra = rom.get("merged_ra_metadata") if isinstance(rom.get("merged_ra_metadata"), dict) else {}
    genres = meta.get("genres") if isinstance(meta.get("genres"), list) else []
    listed = ra.get("achievements") if isinstance(ra.get("achievements"), list) else []
    slug = _text(rom.get("platform_slug"))
    return {
        "rom_id": _number(rom.get("id")),
        "name": _text(rom.get("name")) or _text(rom.get("fs_name_no_ext")),
        "platform": _text(rom.get("platform_display_name")),
        "system": SYSTEMS.get(slug) or SYSTEMS.get(_text(rom.get("platform_fs_slug"))) or "",
        "released": _number(meta.get("first_release_date")),
        "genres": [g for g in genres if isinstance(g, str)][:4],
        "rating": float(meta.get("average_rating") or 0) if isinstance(meta.get("average_rating"), (int, float)) else 0,
        "summary": _text(rom.get("summary")),
        "ra_id": _number(rom.get("ra_id")),
        "size": _number(rom.get("fs_size_bytes")),
        "achievements": [
            {
                "badge_id": str(a.get("badge_id") or ""),
                "title": _text(a.get("title")),
                "description": _text(a.get("description")),
                "points": _number(a.get("points")),
                "num_awarded": _number(a.get("num_awarded")),
                "badge_url": _text(a.get("badge_url")),
                "badge_url_lock": _text(a.get("badge_url_lock")),
                "order": _number(a.get("display_order")),
            }
            for a in listed
            if isinstance(a, dict)
        ],
    }


def _rom(server, key, rom_id):
    """RomM's answer for one game: (the game, None) or (None, why)."""
    status, rom = _request(server, "GET", "/api/roms/%d" % rom_id, key["token"])
    if status == 401:
        # Revoked or expired in RomM: forgotten here, so the settings ask to connect again.
        _forget_key()
        return None, "refused"
    if status == 403:
        return None, "refused"
    if status == 404:
        return None, "game"
    if status != 200 or not isinstance(rom, dict) or _number(rom.get("id")) != rom_id:
        return None, "unreachable"
    return rom, None


COVER_MAX = 6 * 1024 * 1024
COVERS = "/assets/romm/resources/"  # where RomM serves its covers from
COVER_KINDS = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpg"))


# The other pictures of a Steam entry, from what RomM keeps of ScreenScraper's media (when RomM
# is set to keep them): the wide picture behind the page, the logo over it and the wide capsule.
# Only RomM's own copies are used, never ScreenScraper's addresses (they carry RomM's account).
# ScreenScraper's fanart is the hero only when SteamGridDB has none: its fanart is often of another
# game of the series. The player can pick another on the game's settings (hero_choices).
PICTURES = {"hero": ("fanart_path",), "logo": ("logo_path",), "wide": ("steamgrid_path",)}
FANART = ("fanart_path",)
HERO_CHOICES = 12  # SteamGridDB's heroes offered to pick from
STEAM_ROOTS = (os.path.join(".local", "share", "Steam"), os.path.join(".steam", "steam"))
PICTURE_MAX = 8 * 1024 * 1024


def _cover(server, key, rom):
    """The game's cover as RomM keeps it: (bytes, "png" or "jpg"), or None."""
    return _resource(server, key, _text(rom.get("path_cover_large")).split("?")[0], COVER_MAX)


def _picture(server, key, rom, kind):
    """One of the entry's other pictures as RomM keeps it: (bytes, "png" or "jpg"), or None."""
    ss = rom.get("ss_metadata") if isinstance(rom.get("ss_metadata"), dict) else {}
    for field in FANART if kind == "fanart" else PICTURES[kind]:
        path = _text(ss.get(field)).split("?")[0].lstrip("/")
        if path:
            return _resource(server, key, COVERS + path, PICTURE_MAX)
    return None


def _steam_heroes(appid):
    """The bytes of every hero Steam keeps for this entry, one per Steam account on the device."""
    seen, out = set(), []
    for root in STEAM_ROOTS:
        users = os.path.join(_home(), root, "userdata")
        try:
            accounts = os.listdir(users)
        except OSError:
            continue
        for account in accounts:
            for ext in ("jpg", "png"):
                path = os.path.realpath(os.path.join(users, account, "config", "grid", "%d_hero.%s" % (appid, ext)))
                if path in seen or not os.path.isfile(path):
                    continue
                seen.add(path)
                try:
                    with open(path, "rb") as f:
                        out.append(f.read(PICTURE_MAX + 1))
                except OSError:
                    pass
    return out


def _resource(server, key, path, most):
    """A picture of a sensible size from RomM's own folder of resources, or None."""
    if not path.startswith(COVERS) or any(p in ("", ".", "..") for p in path[len(COVERS):].split("/")):
        return None
    req, _, ctx = _prepare(server, "GET", urllib.parse.quote(path), key["token"], accept="image/*")
    try:
        with _opener(ctx).open(req, timeout=TIMEOUT) as r:
            raw = r.read(most + 1)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if len(raw) > most:
        return None
    kind = next((name for magic, name in COVER_KINDS if raw.startswith(magic)), None)
    return (raw, kind) if kind else None


# ── SteamGridDB: the pictures RomM lacks, for those who give their own key ───────────
# Off until the player pastes their own SteamGridDB key. It only fills gaps: a kind RomM keeps is
# never asked for, and the page asks only for what the Steam entry lacks. Only the game's name
# goes out, and only to SteamGridDB's API over HTTPS; the key goes nowhere else, is kept for this
# user only and is never logged or handed back. A name that matches no game, or more than one,
# brings nothing: no picture is better than another game's.
SGDB_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "steamgriddb.json")
SGDB_API = "https://www.steamgriddb.com/api/v2"
# Where its pictures are served from; they are fetched without the key.
SGDB_IMAGES = ("https://cdn2.steamgriddb.com/", "https://cdn.steamgriddb.com/")
SGDB_ICONS = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "icons")
SGDB_KEY_SHAPE = re.compile(r"[A-Za-z0-9]{16,128}")
SGDB_PLAIN = {"types": "static", "nsfw": "false", "humor": "false", "epilepsy": "false", "limit": "5"}
# Each kind of the entry: the API's list and its filters (sizes as Steam shows them).
SGDB_KINDS = {
    "cover": ("grids", {"dimensions": "600x900", "mimes": "image/png,image/jpeg"}),
    "wide": ("grids", {"dimensions": "920x430", "mimes": "image/png,image/jpeg"}),
    "hero": ("heroes", {"dimensions": "1920x620,3840x1240,1600x650", "mimes": "image/png,image/jpeg"}),
    "logo": ("logos", {"mimes": "image/png"}),
    "icon": ("icons", {"mimes": "image/png"}),
}
_sgdb_ids = {}  # name as compared -> SteamGridDB's game id, or None when unsure
_sgdb_refused = [False]  # whether SteamGridDB turned the key down since it was kept
_hero_urls = {}  # RomM game -> {SteamGridDB hero id: its picture}, as last offered to pick from


def _sgdb_key():
    try:
        with open(SGDB_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    key = data.get("key") if isinstance(data, dict) else None
    return key if isinstance(key, str) and SGDB_KEY_SHAPE.fullmatch(key) else None


# The key also lives in the player's RomM account, in its UI settings (only they and RomM's admins
# can read them), so every device linked to that account picks it up without typing it again.
# RomM's own key never leaves the server: it only finds covers, so Vistazo keeps one of its own.
# "" there means it was taken away on some device: then every device forgets it too.
SGDB_SHARED = "vistazo_sgdb_key"
SGDB_SYNC_EVERY = 600  # seconds between looks at the account
_sgdb_sync = {"at": 0.0, "shared": False, "lock": None}


def _romm_me(server, key):
    status, me = _request(server, "GET", "/api/users/me", key["token"])
    return me if status == 200 and isinstance(me, dict) and _number(me.get("id")) > 0 else None


def _ui_settings(me):
    ui = me.get("ui_settings")
    return dict(ui) if isinstance(ui, dict) else {}


def _sgdb_put(server, key, me, value):
    """Writes the key ("" once taken away) into the account's UI settings, keeping the rest of them."""
    ui = _ui_settings(me)
    if ui.get(SGDB_SHARED) == value:
        return True
    ui[SGDB_SHARED] = value
    form = urllib.parse.urlencode({"ui_settings": json.dumps(ui)}).encode("utf-8")
    status, _ = _request(server, "PUT", "/api/users/%d" % _number(me["id"]), key["token"],
                         raw=(form, "application/x-www-form-urlencoded"))
    return status == 200


def _sgdb_linked():
    """(server, RomM key) when this device is linked to the server it uses, else (None, None)."""
    key, server = _read_key(), _server()
    return (server, key) if key and server and key["url"] == server["url"] else (None, None)


def _sgdb_share(value):
    """Hands the key to the account (or "" to take it away): True when RomM took it."""
    server, key = _sgdb_linked()
    me = _romm_me(server, key) if server else None
    done = bool(me) and _sgdb_put(server, key, me, value)
    _sgdb_sync["shared"] = done and bool(value)
    _sgdb_sync["at"] = time.monotonic()
    return done


def _sgdb_pull():
    """Brings the account's key here; puts this device's back when the account lost it (RomM's
    web page may rewrite its UI settings); forgets it here when it was taken away elsewhere."""
    _sgdb_sync["at"] = time.monotonic()
    server, key = _sgdb_linked()
    me = _romm_me(server, key) if server else None
    if not me:
        return
    shared, local = _ui_settings(me).get(SGDB_SHARED), _sgdb_key()
    if isinstance(shared, str) and SGDB_KEY_SHAPE.fullmatch(shared):
        if shared != local and _write_private(SGDB_FILE, {"key": shared}):
            _sgdb_refused[0] = False
            _sgdb_ids.clear()
            decky.logger.info("SteamGridDB key taken from RomM")
        _sgdb_sync["shared"] = True
    elif shared == "" and local:
        try:
            os.remove(SGDB_FILE)
        except OSError:
            pass
        _sgdb_refused[0] = False
        _sgdb_ids.clear()
        _sgdb_sync["shared"] = False
        decky.logger.info("SteamGridDB key taken away on another device")
    elif local:
        _sgdb_sync["shared"] = _sgdb_put(server, key, me, local)
    else:
        _sgdb_sync["shared"] = False


def _sgdb_name(name):
    """A name as compared: no accents, case or punctuation."""
    import unicodedata
    text = unicodedata.normalize("NFKD", _text(name)).replace("&", " and ")
    return "".join(c for c in text.casefold() if c.isalnum())


def _sgdb_match(name, results):
    """SteamGridDB's id of the one game named exactly `name`; None when there is none or more than one."""
    want = _sgdb_name(name)
    ids = {_number(r.get("id")) for r in results if isinstance(r, dict) and _sgdb_name(r.get("name")) == want} if want else set()
    ids.discard(0)
    return ids.pop() if len(ids) == 1 else None


def _romm_has(rom, kind):
    """Whether RomM keeps a picture of this kind for the game: then SteamGridDB is never asked for it.
    The hero is asked of SteamGridDB first anyway: RomM's (ScreenScraper's fanart) is the fallback."""
    if kind == "hero":
        return False
    if kind == "cover":
        path = _text(rom.get("path_cover_large")).split("?")[0]
        rest = path[len(COVERS):] if path.startswith(COVERS) else ""
    else:
        ss = rom.get("ss_metadata") if isinstance(rom.get("ss_metadata"), dict) else {}
        rest = next((_text(ss.get(f)).split("?")[0].lstrip("/") for f in PICTURES.get(kind, ()) if _text(ss.get(f))), "")
    return bool(rest) and all(p not in ("", ".", "..") for p in rest.split("/"))


def _sgdb_get(key, path, query=None):
    """One call to SteamGridDB's API: (status, parsed JSON or None); 0 when it could not be reached."""
    req, _, ctx = _prepare({"url": SGDB_API}, "GET", path + ("?" + urllib.parse.urlencode(query) if query else ""), key)
    try:
        with _opener(ctx).open(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read(2 * 1024 * 1024) or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def _sgdb_image(url):
    """A picture from SteamGridDB's own image servers, fetched without the key: (bytes, kind) or None."""
    if not isinstance(url, str) or not url.startswith(SGDB_IMAGES) or any(c.isspace() for c in url):
        return None
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "image/*")
    req.add_header("User-Agent", "Vistazo")
    ctx = ssl.create_default_context(cafile=next((f for f in CA_FILES if os.path.isfile(f)), None)) if url.startswith("https:") else None
    try:
        with _opener(ctx).open(req, timeout=TIMEOUT) as r:
            raw = r.read(PICTURE_MAX + 1)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if len(raw) > PICTURE_MAX:
        return None
    kind = next((name for magic, name in COVER_KINDS if raw.startswith(magic)), None)
    return (raw, kind) if kind else None


def _sgdb_game(key, name):
    """SteamGridDB's id of the game named `name` (None when unsure), or "refused" / "unreachable"."""
    want = _sgdb_name(name)
    if want not in _sgdb_ids:
        status, found = _sgdb_get(key, "/search/autocomplete/" + urllib.parse.quote(name, safe=""))
        if status in (401, 403):
            return "refused"
        if status != 200 or not isinstance(found, dict):
            return "unreachable"
        _sgdb_ids[want] = _sgdb_match(name, found.get("data") if isinstance(found.get("data"), list) else [])
    return _sgdb_ids[want]


def _sgdb_heroes(key, name):
    """SteamGridDB's heroes for the game named `name`, to pick from: [(id, url, thumb url)], or
    "refused" / "unreachable"."""
    game = _sgdb_game(key, name)
    if game in ("refused", "unreachable") or not game:
        return game or []
    endpoint, filters = SGDB_KINDS["hero"]
    status, found = _sgdb_get(key, "/%s/game/%d" % (endpoint, game), {**filters, **SGDB_PLAIN, "limit": str(HERO_CHOICES)})
    if status in (401, 403):
        return "refused"
    if status != 200 or not isinstance(found, dict):
        return "unreachable"
    out = []
    for item in found.get("data") if isinstance(found.get("data"), list) else []:
        if not isinstance(item, dict):
            continue
        url, thumb, number = item.get("url"), item.get("thumb") or item.get("url"), _number(item.get("id"))
        if number > 0 and isinstance(url, str) and url.startswith(SGDB_IMAGES) and isinstance(thumb, str) and thumb.startswith(SGDB_IMAGES):
            out.append((number, url, thumb))
    return out[:HERO_CHOICES]


def _sgdb_icon_file(rom_id, picture):
    """The icon as a file of its own (Steam takes an entry's icon by path); its path, or None."""
    path = os.path.join(SGDB_ICONS, "%d.%s" % (rom_id, picture[1]))
    tmp = path + ".tmp"
    try:
        os.makedirs(SGDB_ICONS, exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(picture[0])
        os.replace(tmp, path)
        return path
    except OSError:
        return None


def _sgdb_pictures(key, rom_id, name, kinds):
    """The pictures of `kinds` SteamGridDB has for the game named `name`: {kind: (bytes, kind)} with
    "icon" as a path, or "refused" / "unreachable"."""
    game = _sgdb_game(key, name)
    if game in ("refused", "unreachable"):
        return game
    out = {}
    if not game:
        return out
    for kind in kinds:
        endpoint, filters = SGDB_KINDS[kind]
        status, found = _sgdb_get(key, "/%s/game/%d" % (endpoint, game), {**filters, **SGDB_PLAIN})
        if status in (401, 403):
            return "refused"
        listed = found.get("data") if status == 200 and isinstance(found, dict) else None
        for item in (listed if isinstance(listed, list) else [])[:3]:
            got = _sgdb_image(item.get("url")) if isinstance(item, dict) else None
            if got:
                out[kind] = _sgdb_icon_file(rom_id, got) if kind == "icon" else got
                break
        if kind in out and out[kind] is None:
            del out[kind]
    return out


def _rom_files(rom):
    """The names and sizes of a game's files, as RomM lists them."""
    files = rom.get("files") if isinstance(rom.get("files"), list) else []
    return [(_text(f.get("file_name")), _number(f.get("file_size_bytes"))) for f in files if isinstance(f, dict)]


# RomM's sums of a packed file are of what is inside it, not of the file as it is sent.
PACKED = (".zip", ".7z", ".rar", ".gz", ".bz2", ".xz", ".tar")


def _expected_sum(rom):
    """(the sum's kind, its value) RomM keeps of a game's only file, or None when there is
    nothing to compare a download with."""
    files = [f for f in rom.get("files") or [] if isinstance(f, dict)]
    if len(files) != 1 or _text(files[0].get("file_name")).lower().endswith(PACKED):
        return None
    for kind, field in (("sha1", "sha1_hash"), ("md5", "md5_hash")):
        value = _text(files[0].get(field)).strip().lower()
        if value and all(c in "0123456789abcdef" for c in value):
            return kind, value
    return None


def _state(appid, game):
    """One game as the page needs it: whether Vistazo starts it, has its file, and any download."""
    job = _jobs.get(appid)
    out = {
        "managed": bool(game),
        "rom_id": _number((game or {}).get("rom_id")),
        "installed": bool(game and game.get("installed") and os.path.isfile(str(game.get("path") or ""))),
        # Only what Vistazo itself put on the disk is its to remove.
        "removable": bool(game and game.get("installed") and game.get("files")),
        "exe": LAUNCHER,
        "start_dir": RUNTIME,
    }
    if job:
        out["job"] = {"state": job["state"], "done": job["done"], "total": job["total"]}
    elif _kept_part(game):
        # Paused before a restart: what was brought waits on the disk.
        out["job"] = {"state": "paused", "done": os.path.getsize(game["part"]), "total": _number(game.get("size"))}
    sw = (game or {}).get("switching")
    if isinstance(sw, dict):
        out["switching"] = _number(sw.get("rom_id"))
    return out


def _note_part(appid, part, kept=False):
    """Writes down the half-done file of a download (or that there is none any more), so one
    cut off by a restart can be cleared away later. A `kept` one (paused, or cut off by RomM)
    stays for the download to go on from where it stopped."""
    with _lock:
        data = _read_games()
        game = data["games"].get(str(appid))
        if game is None or (game.get("part") == part and bool(game.get("paused")) == kept):
            return
        game.pop("paused", None)
        if part:
            game["part"] = part
            if kept:
                game["paused"] = True
        else:
            game.pop("part", None)
        _write_games(data)


def _kept_part(game):
    """The half-done file a paused download left, when it is still there as the add-on left it."""
    part = game.get("part") if isinstance(game, dict) and game.get("paused") else None
    if not isinstance(part, str) or not part.endswith(".part"):
        return None
    system_dir = os.path.join(_roms_root(), _text(game.get("system")))
    return part if _within(part, system_dir) and os.path.isfile(part) and not os.path.islink(part) else None


def _drop_part(game):
    """Removes a game's half-done file and forgets it (the games file is written by the caller)."""
    if not isinstance(game, dict):
        return False
    part = game.pop("part", None)
    game.pop("paused", None)
    if not isinstance(part, str):
        return False
    system_dir = os.path.join(_roms_root(), _text(game.get("system")))
    try:
        if part.endswith(".part") and _within(part, system_dir) and os.path.isfile(part) and not os.path.islink(part):
            os.remove(part)
    except OSError as e:
        decky.logger.warning("Half-done download not removed: %s", e)
    return True


def _sweep_parts():
    """Clears away the half-done files of downloads a restart cut off; a paused one stays."""
    with _lock:
        data = _read_games()
        left = False
        for game in data["games"].values():
            if not isinstance(game, dict) or _kept_part(game):
                continue
            left = _drop_part(game) or left
        if left:
            _write_games(data)


def _move_in():
    """This used to live inside Vistazo itself: the key and the games it kept there move here,
    once, and leave nothing behind (a key is never left lying in two places)."""
    old = os.path.join(decky.DECKY_HOME, "settings", "Vistazo")
    for name, mine in (("romm.json", KEY_FILE), ("games.json", GAMES_FILE)):
        theirs = os.path.join(old, name)
        try:
            if os.path.islink(theirs) or not os.path.isfile(theirs):
                continue
            if not os.path.exists(mine):
                with open(theirs, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict) or not _write_private(mine, data):
                    continue
            os.remove(theirs)
        except (OSError, ValueError) as e:
            decky.logger.warning("Not moved from Vistazo: %s", e)
    # RomM's last answers come along too, for when it is asleep.
    kept = os.path.join(decky.DECKY_HOME, "data", "Vistazo", "romm-kept.json")
    try:
        if os.path.isfile(kept) and not os.path.islink(kept) and not os.path.exists(KEPT_FILE):
            with open(kept, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _write_private(KEPT_FILE, data)
    except (OSError, ValueError) as e:
        decky.logger.warning("Last answers not moved from Vistazo: %s", e)


class _Stop(Exception):
    """A download that ends early, with the reason the page shows."""


def _fetch(server, token, path, target, job, expected=0, check=None, resume=False):
    """Streams one answer of RomM's into `target` (made new, private). Returns the bytes written;
    raises _Stop when cancelled or paused, stalled, refused, cut short or, with `check` (a sum's
    kind and value), not the file RomM has. With `resume`, a `target` already there is gone on
    from where it stops, when RomM sends just the rest; otherwise it is started again."""
    summed = hashlib.new(check[0]) if check else None
    have = os.path.getsize(target) if resume and os.path.isfile(target) and not os.path.islink(target) else 0
    req, _, ctx = _prepare(server, "GET", path, token, accept="*/*")
    if have:
        req.add_header("Range", "bytes=%d-" % have)
    try:
        r = _opener(ctx).open(req, timeout=STALL)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:
            # Nothing left to send after what is here (or RomM will not say): started again.
            return _fetch(server, token, path, target, job, expected, check)
        raise _Stop("refused" if e.code in (401, 403) else "game" if e.code == 404 else "unreachable")
    except (urllib.error.URLError, OSError, ValueError) as e:
        decky.logger.warning("RomM download not reached: %s", getattr(e, "reason", e))
        raise _Stop("unreachable")
    with r:
        length = _number(r.headers.get("Content-Length"))
        # Only the rest of this very file, starting right where the half here ends, is added to it.
        rest = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", _text(r.headers.get("Content-Range")).strip())
        if have and r.status == 206 and not (rest and int(rest.group(1)) == have):
            r.close()
            return _fetch(server, token, path, target, job, expected, check)
        if r.status != 206:
            have = 0  # the whole file again, from the start
        flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | (os.O_APPEND if have else os.O_CREAT | os.O_TRUNC)
        written = have
        if length:
            length += have
        job["total"] = length or expected
        job["done"] = written
        try:
            fd = os.open(target, flags, 0o644)
            with os.fdopen(fd, "ab" if have else "wb") as f:
                if summed and have:
                    with open(target, "rb") as before:
                        for block in iter(lambda: before.read(CHUNK), b""):
                            summed.update(block)
                while True:
                    if job["cancel"].is_set():
                        raise _Stop("paused" if job.get("pause") else "cancelled")
                    try:
                        block = r.read(CHUNK)
                    except (OSError, ValueError) as e:
                        decky.logger.warning("RomM download cut short: %s", e)
                        raise _Stop("unreachable")
                    if not block:
                        break
                    f.write(block)
                    if summed:
                        summed.update(block)
                    written += len(block)
                    job["done"] = written
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            decky.logger.error("Download not written: %s", e)
            raise _Stop("disk")
    # Every byte RomM announced, and when it announced none, every byte the game has there.
    if (length and written != length) or (not length and expected and written != expected):
        raise _Stop("unreachable")
    if summed and summed.hexdigest() != check[1]:
        decky.logger.error("Download does not match RomM's %s", check[0])
        raise _Stop("damaged")
    return written


def _unzip(archive, folder, system_dir, job):
    """Takes a RomM archive of a game with several files apart into `folder`, one member at a
    time, each checked to land inside the system's folder. Returns the files made."""
    import zipfile

    made = []
    try:
        with zipfile.ZipFile(archive) as z:
            members = [m for m in z.infolist() if not m.is_dir()]
            if sum(m.file_size for m in members) + SPARE > shutil.disk_usage(system_dir).free:
                raise _Stop("space")
            for m in members:
                parts = [p for p in m.filename.replace("\\", "/").split("/") if p not in ("", ".")]
                if not parts or ".." in parts or any("\0" in p for p in parts) or m.filename.startswith("/"):
                    raise _Stop("archive")
                target = os.path.join(folder, *parts)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if not _within(os.path.dirname(target), system_dir) or os.path.lexists(target):
                    raise _Stop("archive")
                made.append(target)
                with z.open(m) as src, open(target, "xb") as out:
                    while True:
                        if job["cancel"].is_set():
                            raise _Stop("cancelled")
                        block = src.read(CHUNK)
                        if not block:
                            break
                        out.write(block)
    except (zipfile.BadZipFile, OSError) as e:
        decky.logger.error("Archive not unpacked: %s", e)
        _discard(made, [folder], system_dir)
        raise _Stop("archive")
    except _Stop:
        _discard(made, [folder], system_dir)
        raise
    return made


def _mark(path):
    """A file as Vistazo left it (size and time of writing): to know later it is still that one."""
    info = os.stat(path)
    return [info.st_size, info.st_mtime_ns]


def _discard(files, folders, system_dir, marks=None):
    """Removes files Vistazo made and, when left empty, the folders it made for them. With
    `marks`, a file that is no longer as Vistazo left it (another program put its own there
    since) is not Vistazo's any more and stays."""
    for path in files:
        try:
            if _within(path, system_dir) and os.path.isfile(path) and not os.path.islink(path):
                if marks is not None and marks.get(path) != _mark(path):
                    decky.logger.info("A file changed since Vistazo brought it was left in place")
                    continue
                os.remove(path)
        except OSError as e:
            decky.logger.warning("Not removed: %s", e)
    for folder in folders:
        for root, _, _ in os.walk(folder, topdown=False):
            try:
                if _within(root, system_dir):
                    os.rmdir(root)  # only when empty
            except OSError:
                pass


def _download(server, token, rom, job, started=lambda part: None, resume=None):
    """Brings one game's file from RomM into RetroDECK's folder for its system. Returns what
    the games file keeps of it: {"path", "files", "folders", "size"}; raises _Stop otherwise.
    `started` is told the half-done file's name before it is made. `resume` is the half-done
    file a paused download left: when it is this game's, the download goes on from there. When
    paused (or cut off by RomM, when `resume` is not None), the half-done file stays."""
    keep = ("paused", "unreachable") if resume is not None else ()
    system = _detail(rom)["system"]
    if not system:
        raise _Stop("system")
    roms = _roms_root()
    if not os.path.isdir(roms):
        raise _Stop("retrodeck")
    system_dir = os.path.join(roms, system)
    os.makedirs(system_dir, exist_ok=True)
    if not _within(system_dir, roms):
        raise _Stop("retrodeck")
    name = _one_name(rom.get("fs_name"))
    files = _rom_files(rom)
    size = _number(rom.get("fs_size_bytes"))
    if not name or not files:
        raise _Stop("game")
    have = os.path.getsize(resume) if resume and os.path.isfile(resume) else 0
    if size - have + SPARE > shutil.disk_usage(system_dir).free:
        raise _Stop("space")
    path = "/api/roms/%d/content/%s" % (_number(rom.get("id")), urllib.parse.quote(name, safe=""))
    if len(files) == 1:
        # One file: RomM sends it as it is.
        target = os.path.join(system_dir, _one_name(files[0][0]) or name)
        if os.path.lexists(target):
            # Already there (another program's, or an earlier copy): used as it is when it is
            # whole, and never Vistazo's to remove.
            if os.path.isfile(target) and not os.path.islink(target) and os.path.getsize(target) == files[0][1]:
                return {"path": target, "files": [], "folders": [], "size": files[0][1]}
            raise _Stop("exists")
        part = target + ".part"
        started(part)
        stopped = None
        try:
            written = _fetch(server, token, path, part, job, files[0][1], _expected_sum(rom), part == resume)
            if files[0][1] and written != files[0][1]:
                os.remove(part)  # whole, but not the game's size: nothing to go on from
                raise _Stop("unreachable")
            os.rename(part, target)
        except _Stop as stop:
            stopped = str(stop)
            raise
        except OSError as e:
            decky.logger.error("Download not kept: %s", e)
            raise _Stop("disk")
        finally:
            if stopped not in keep and os.path.lexists(part):
                os.remove(part)
        return {"path": target, "files": [target], "folders": [], "size": written}
    # Several files: RomM sends an archive, with a playlist when the game has discs. They go into
    # a folder of the game's name; what starts is the playlist, else the first disc's sheet (never
    # one of the tracks it stands for), else the largest file.
    folder = os.path.join(system_dir, name)
    if os.path.lexists(folder):
        raise _Stop("exists")
    part = folder + ".zip.part"
    started(part)
    stopped = None
    try:
        _fetch(server, token, path, part, job, resume=part == resume)
        with _lock:
            # Unpacking is not paused: a pause asked for just now stops here, with the archive whole.
            job["unpacking"] = True
            if job.get("pause"):
                raise _Stop("paused")
        os.mkdir(folder)
        made = _unzip(part, folder, system_dir, job)
    except _Stop as stop:
        stopped = str(stop)
        raise
    except OSError as e:
        decky.logger.error("Download not kept: %s", e)
        raise _Stop("disk")
    finally:
        if stopped not in keep and os.path.lexists(part):
            os.remove(part)
    if not made:
        _discard([], [folder], system_dir)
        raise _Stop("archive")
    lists = [f for f in made if f.lower().endswith(".m3u")]
    sheets = sorted((f for f in made if f.lower().endswith((".cue", ".gdi", ".ccd"))),
                    key=lambda f: [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", os.path.basename(f))])
    start = lists[0] if len(lists) == 1 else sheets[0] if sheets else max(made, key=os.path.getsize)
    return {"path": start, "files": made, "folders": [folder], "size": sum(os.path.getsize(f) for f in made)}


# ── Discs ─────────────────────────────────────────────────────────────────────────────────────
# A game of several discs comes as a folder, and one file of it starts. The player may choose
# another disc, or every disc through a playlist (the emulator then changes discs itself). The
# choice is the file's path, kept in the game's record as "disc"; it is believed only while it is
# one of the files the add-on recorded for the game, inside the game's recorded folder.
DISC_KINDS = (".cue", ".chd", ".iso", ".gdi", ".rvz", ".cso", ".zso", ".pbp", ".ccd", ".mds", ".cdi",
              ".gcm", ".gcz", ".wbfs", ".wia", ".ciso", ".nrg", ".img")
PLAYLIST = ".m3u"


def _game_folder(game):
    """The folder the add-on made for a game of several files (the one its file is in), or None."""
    path, system = _text(game.get("path")), _text(game.get("system"))
    if not path or not system or not game.get("installed"):
        return None
    system_dir = os.path.join(_roms_root(), system)
    for folder in game.get("folders") or []:
        if isinstance(folder, str) and _within(path, folder) and _within(folder, system_dir):
            return folder
    return None


def _game_owned(game, folder):
    """The add-on's own files of a game that are inside its folder and still plain files there."""
    return [f for f in game.get("files") or [] if isinstance(f, str) and _within(f, folder)
            and os.path.isfile(f) and not os.path.islink(f)]


def _tracks(sheet):
    """The files a .cue, .gdi or .ccd sheet stands for (its tracks), as full paths."""
    here, low = os.path.dirname(sheet), sheet.lower()
    if low.endswith(".ccd"):
        return {os.path.splitext(sheet)[0] + ext for ext in (".img", ".IMG", ".sub", ".SUB")}
    try:
        with open(sheet, encoding="utf-8", errors="replace") as f:
            text = f.read(64 * 1024)
    except OSError:
        return set()
    if low.endswith(".cue"):
        names = [m[0] or m[1] for m in re.findall(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))', text, flags=re.M | re.I)]
    else:  # .gdi: "track start type sector name offset", the name quoted when it has spaces
        names = [m[0] or m[1] for m in re.findall(r'^\s*\d+\s+\d+\s+\d+\s+\d+\s+(?:"([^"]+)"|(\S+))', text, flags=re.M)]
    return {os.path.normpath(os.path.join(here, n.replace("\\", "/"))) for n in names}


def _discs(game):
    """(the game's folder, its discs in name order, its playlist or None): the discs are the files
    that start a game on their own, never the tracks a sheet stands for. None for a game without
    a folder of the add-on's."""
    folder = _game_folder(game)
    if not folder:
        return None
    owned = _game_owned(game, folder)
    tracks = set()
    for f in owned:
        if f.lower().endswith((".cue", ".gdi", ".ccd")):
            tracks |= _tracks(f)
    discs = sorted((f for f in owned if f.lower().endswith(DISC_KINDS) and f not in tracks),
                   key=lambda f: [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", os.path.basename(f))])
    lists = sorted(f for f in owned if f.lower().endswith(PLAYLIST))
    return folder, discs, lists[0] if lists else None


def _disc_path(game):
    """(the disc chosen for a game, its folder) while that choice is still good, else None."""
    disc = game.get("disc") if isinstance(game, dict) else None
    found = _discs(game) if isinstance(disc, str) else None
    if not found or (disc not in found[1] and disc != found[2]):
        return None
    return disc, found[0]


def _make_playlist(game, folder, discs):
    """A playlist of the discs in the game's folder, recorded as the add-on's file (so removing
    the game removes it). Returns its path, or raises _Stop("exists") when a file of that name is
    already there and not the add-on's: nothing is ever written over."""
    name = _one_name(os.path.basename(folder) + PLAYLIST)
    if not name:
        raise _Stop("disc")
    path = os.path.join(folder, name)
    if os.path.lexists(path) or not _within(folder, os.path.join(_roms_root(), _text(game.get("system")))):
        raise _Stop("exists")
    lines = "".join(os.path.relpath(d, folder) + "\n" for d in discs)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(lines)
    except OSError as e:
        decky.logger.error("Playlist not written: %s", e)
        raise _Stop("disk")
    game["files"] = [f for f in game.get("files") or [] if isinstance(f, str)] + [path]
    marks = game.get("marks") if isinstance(game.get("marks"), dict) else {}
    marks[path] = _mark(path)
    game["marks"] = marks
    return path


def _library(server, key):
    """Every game RomM has, by its number: ({rom_id: game}, None), or (None, why) when RomM did not
    answer every page. Past the most pages ever asked for, what came so far, with "partial"."""
    every, offset = {}, 0
    for _ in range(LIBRARY_PAGES):
        path = ("/api/roms?limit=%d&offset=%d&order_by=name&order_dir=asc&with_char_index=false"
                "&with_filter_values=false&with_rom_id_index=false&with_total=false" % (LIBRARY_PAGE, offset))
        status, reply = _request(server, "GET", path, key["token"])
        if status == 401:
            _forget_key()
        if status in (401, 403):
            return None, "refused"
        items = reply.get("items") if isinstance(reply, dict) else None
        if status != 200 or not isinstance(items, list):
            return None, "unreachable"
        for rom in items:
            if isinstance(rom, dict) and _number(rom.get("id")) > 0:
                every[_number(rom["id"])] = rom
        if len(items) < LIBRARY_PAGE:
            return every, None
        offset += LIBRARY_PAGE
    return every, "partial"


def _gone(server, key, games):
    """The entries whose game RomM no longer has: ([appid], None), or (None, why). A game missing
    from the list is asked for by itself too, and only RomM saying it is not there counts."""
    every, why = _library(server, key)
    if why:
        return None, "unreachable" if why == "partial" else why
    gone = []
    for appid, game in games.items():
        rom_id = _number(game.get("rom_id")) if isinstance(game, dict) else 0
        if not rom_id or rom_id in every:
            continue
        status, _ = _request(server, "GET", "/api/roms/%d" % rom_id, key["token"])
        if status == 404:
            gone.append(_number(appid))
        elif status != 200:
            return None, "refused" if status in (401, 403) else "unreachable"
    return sorted(gone), None


def _stands_for_its_versions(rom):
    """Whether this is the one to list of a game kept in several versions: the one marked as
    the main one, or, when none is marked, the first of them."""
    siblings = [x for x in rom.get("sibling_roms") or [] if isinstance(x, dict)]
    if not siblings:
        return True
    mine = rom.get("rom_user") if isinstance(rom.get("rom_user"), dict) else {}
    if mine.get("is_main_sibling") is True:
        return True
    if any(x.get("is_main_sibling") is True for x in siblings):
        return False
    return _number(rom.get("id")) < min(_number(x.get("id")) for x in siblings)


def _sibling_ids(rom):
    return {_number(x.get("id")) for x in rom.get("sibling_roms") or [] if isinstance(x, dict) and _number(x.get("id")) > 0}


def _languages(rom):
    found = rom.get("languages")
    return {_text(x).strip().lower() for x in found if isinstance(x, str)} if isinstance(found, list) else set()


def _version_groups(every):
    """The games of a listing, each as the sorted numbers of all its versions that are listed:
    versions are linked by RomM's siblings, either way round."""
    links = {n: set() for n in every}
    for n, rom in every.items():
        for m in _sibling_ids(rom):
            if m in links:
                links[n].add(m)
                links[m].add(n)
    seen, groups = set(), []
    for start in sorted(every):
        if start in seen:
            continue
        group, todo = set(), [start]
        while todo:
            n = todo.pop()
            if n in group or n not in every:
                continue
            group.add(n)
            todo += links[n]
        seen |= group
        groups.append(sorted(group))
    return groups


def _version_row(rom, current):
    return {
        "rom_id": _number(rom.get("id")),
        "file": _text(rom.get("fs_name"))[:160],
        "languages": [_text(x)[:40] for x in rom.get("languages") or [] if isinstance(x, str)][:8],
        "regions": [_text(x)[:40] for x in rom.get("regions") or [] if isinstance(x, str)][:8],
        "achievements": _number(rom.get("ra_id")) > 0,
        "current": _number(rom.get("id")) == current,
    }


def _carry_save(old, new):
    """The old version's save given to the new one when the new one has none yet; the old one
    stays as it is. Returns "carried", "kept" (the new one has its own) or why not."""
    old_path, kind = _save_file(old)
    new_path, _ = _save_file(new)
    if not old_path or not new_path or old_path == new_path:
        return kind if not old_path else "same"
    if os.path.lexists(new_path):
        return "kept"
    if not os.path.isfile(old_path) or os.path.islink(old_path):
        return "none"
    with open(old_path, "rb") as f:
        raw = f.read(SAVE_MOST + 1)
    if len(raw) > SAVE_MOST:
        return "save"
    _put_save(new_path, raw)
    return "carried"


def _drop_switch(game):
    """Takes away a switch not done: the new version's files the add-on brought for it."""
    sw = game.pop("switching", None)
    if isinstance(sw, dict) and sw.get("files"):
        _discard([f for f in sw["files"] if isinstance(f, str)], [f for f in sw.get("folders") or [] if isinstance(f, str)],
                 os.path.join(_roms_root(), _text(game.get("system"))), sw.get("marks") if isinstance(sw.get("marks"), dict) else None)


def _finish_switch(data, appid):
    """Under the lock: the entry starts the version waiting in its "switching", with the save
    carried over when the new version has none (the old one stays), and the old version's
    files the add-on brought taken away. On any failure the entry stays as it was and what was
    brought for the switch goes. Returns {"ok": True, "options", "save"} or {"ok": False, "why"}."""
    old = data["games"][str(appid)]
    sw = old.get("switching")
    system_dir = os.path.join(_roms_root(), _text(old.get("system")))
    rom_id = _number(sw.get("rom_id"))
    path = sw.get("path") if isinstance(sw.get("path"), str) else None
    files = [f for f in sw.get("files") or [] if isinstance(f, str)]
    folders = [f for f in sw.get("folders") or [] if isinstance(f, str)]
    marks = sw.get("marks") if isinstance(sw.get("marks"), dict) else {}
    if path and not os.path.isfile(path):
        _drop_switch(old)
        _write_games(data)
        return {"ok": False, "why": "error"}
    new = {**old, "rom_id": rom_id, "platform": _text(sw.get("platform")), "size": _number(sw.get("size")),
           "path": path, "installed": bool(path), "files": files, "folders": folders, "marks": marks, "saves": {}}
    new.pop("switching", None)
    new.pop("part", None)
    save = "none"
    try:
        if old.get("installed") and path:
            save = _carry_save(old, new)
    except (_Stop, OSError) as e:
        decky.logger.error("Save not carried: %s", e)
        _drop_switch(old)
        _write_games(data)
        return {"ok": False, "why": "disk"}
    gone = ([f for f in old.get("files") or [] if isinstance(f, str)], [f for f in old.get("folders") or [] if isinstance(f, str)],
            old.get("marks") if isinstance(old.get("marks"), dict) else None)
    data["games"][str(appid)] = new
    if not _write_games(data):
        data["games"][str(appid)] = old
        return {"ok": False, "why": "disk"}
    # The new version's files are never among the old one's to take away.
    mine = set(files) | {path}
    _discard([f for f in gone[0] if f not in mine], [f for f in gone[1] if f not in folders], system_dir, gone[2])
    return {"ok": True, "options": str(rom_id), "save": save}


def _already_there(rom):
    """The game's only file, when it is already whole in RetroDECK's folder for its console
    (another program brought it, or an earlier time). None otherwise."""
    system = _detail(rom)["system"]
    files = _rom_files(rom)
    name = _one_name(files[0][0]) if len(files) == 1 else None
    if not system or not name:
        return None
    path = os.path.join(_roms_root(), system, name)
    try:
        if os.path.isfile(path) and not os.path.islink(path) and _within(path, _roms_root()) and os.path.getsize(path) == files[0][1]:
            return os.path.realpath(path)
    except OSError:
        pass
    return None


# ── Taking over Tender's entries ───────────────────────────────────────────────────
# Once Tender is off, its Steam entries (with their play time, pictures and collections) can be
# the add-on's: each keeps its Steam number and is pointed at the add-on's launcher. Which entry
# is which game, and which files Tender brought, are read from Tender's own database, read only.
def _tender_db():
    return os.path.join(_home(), ".local", "share", "romm-tender", "romm_sync.db")


def _tender_games():
    """{Steam number: {"rom_id", "name", "file", "dir"}} as Tender's database has them; {} when
    there is none or it cannot be read."""
    path = _tender_db()
    if not os.path.isfile(path):
        return {}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(path), uri=True, timeout=2)
        try:
            rows = con.execute(
                "SELECT r.rom_id, r.shortcut_app_id, r.name, i.file_path, i.rom_dir FROM roms r "
                "LEFT JOIN rom_installs i ON i.rom_id = r.rom_id WHERE r.shortcut_app_id IS NOT NULL"
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        decky.logger.warning("Tender's database not read: %s", e)
        return {}
    out = {}
    for rom_id, appid, name, file, folder in rows:
        appid, rom_id = _number(appid), _number(rom_id)
        if appid > 0 and rom_id > 0:
            out[appid] = {"rom_id": rom_id, "name": _text(name), "file": _text(file), "dir": _text(folder)}
    return out


def _tender_files(tender, system_dir, rom, taken=()):
    """What Tender brought for a game, inside RetroDECK's folder for its console: (the file
    that starts it or None, the files, the folders). Only a file or folder carrying the game's
    own name in RomM (a folder of other games is not this game's), nothing another of the
    add-on's games already holds (`taken`), nothing outside that folder, no links."""
    path, files, folders = None, [], []
    names = {n for n, _ in _rom_files(rom)} | {_text(rom.get("fs_name"))}
    names.discard("")
    taken = {os.path.realpath(t) for t in taken}
    folder = tender.get("dir")
    if (folder and os.path.basename(os.path.normpath(folder)) in names and os.path.isdir(folder)
            and not os.path.islink(folder) and _within(folder, system_dir)
            and not any(t == os.path.realpath(folder) or t.startswith(os.path.realpath(folder) + os.sep) for t in taken)):
        for root, dirs, found in os.walk(folder):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            for name in found:
                f = os.path.join(root, name)
                if os.path.isfile(f) and not os.path.islink(f) and _within(f, system_dir):
                    files.append(os.path.realpath(f))
        folders = [os.path.realpath(folder)]
    file = tender.get("file")
    if file and os.path.isfile(file) and not os.path.islink(file) and _within(file, system_dir):
        real = os.path.realpath(file)
        own_name = os.path.basename(real) in names or os.path.basename(os.path.dirname(real)) in names
        if real in files or (own_name and real not in taken):
            path = real
            if real not in files:
                files.append(real)
    return path, files, folders


def _installed_path(options, rom):
    """The file another program already put on the disk for this game, read from the launch
    options its shortcut had (…"<file>"), when it is one of the game's own files in RetroDECK's
    folders. None otherwise."""
    found = re.search(r'"((?:[^"\\]|\\.)*)"\s*$', str(options or ""))
    if not found:
        return None
    path = re.sub(r"\\(.)", r"\1", found.group(1))
    names = {n for n, _ in _rom_files(rom)} | {_text(rom.get("fs_name"))}
    if not os.path.isabs(path) or not os.path.isfile(path) or not _within(path, _roms_root()):
        return None
    # The file itself, or the folder it is in, carries the game's name in RomM.
    if os.path.basename(path) in names or os.path.basename(os.path.dirname(path)) in names:
        return os.path.realpath(path)
    return None


# ── Save games ──────────────────────────────────────────────────────────────────────────────────
# For those who turn it on, a game's save is kept in step with RomM: sent there after playing,
# brought from there when RomM has a newer one and the one here has not changed. One rule above
# all: a save on the Deck is never replaced or removed without a copy of it made first, and when
# both sides changed nothing is touched until the player says which one to keep.
# RomM keeps the saves of a game in a named slot, each upload a new version; this uses the same
# slot and the same name for the emulator as Tender does, so both see the same saves.
SAVE_SLOT = "autosave"
SAVE_VERSIONS = 10  # how many RomM keeps in the slot
SAVE_COPIES = 10  # how many copies of a replaced save stay on the Deck, per game
SAVE_COPIES_DIR = ".vistazo-backup"
SAVE_MOST = 64 * 1024 * 1024
# Console -> how the saves of the emulator RetroDECK starts by default are filed: the name RomM
# knows that emulator by, the ending of what goes to RomM, RetroArch's own name for the core (its
# folder when saves are sorted by core), and whether the save is a folder (sent as a zip of it).
# Only what is in use; more as they are needed.
# A game started with another emulator keeps its saves elsewhere: not kept in step then.
SAVE_EMULATORS = {"nds": "melonDS DS", "wii": "Dolphin (Standalone)"}
SAVE_KINDS = {
    "nds": ("retroarch-melondsds", ".srm", "melonDS DS", False),
    # Dolphin keeps each Wii game's save as a folder of the console's memory (its NAND).
    "wii": ("dolphin", ".zip", "Dolphin", True),
}
RETRODECK_HOME = os.path.join(".var", "app", "net.retrodeck.retrodeck")
RETROARCH_CFG = os.path.join(RETRODECK_HOME, "config", "retroarch", "retroarch.cfg")
DOLPHIN_INI = os.path.join(RETRODECK_HOME, "config", "dolphin-emu", "Dolphin.ini")
WII_GAMES = "00010000"  # the NAND's folder of disc games' titles


def _retrodeck_path(key, fallback):
    config = os.path.join(_home(), ".var", "app", "net.retrodeck.retrodeck", "config", "retrodeck", "retrodeck.json")
    try:
        with open(config, encoding="utf-8") as f:
            path = json.load(f).get("paths", {}).get(key, "")
        if isinstance(path, str) and os.path.isabs(path):
            return os.path.realpath(path)
    except (OSError, ValueError, AttributeError):
        pass
    return os.path.realpath(os.path.join(_home(), "retrodeck", fallback))


def _retroarch_flags():
    """RetroArch's choices about where saves go, as its settings file has them."""
    flags = {}
    try:
        with open(os.path.join(_home(), RETROARCH_CFG), encoding="utf-8", errors="replace") as f:
            for line in f:
                key, _, value = line.partition("=")
                key = key.strip()
                if key in ("savefiles_in_content_dir", "sort_savefiles_by_content_enable", "sort_savefiles_enable"):
                    flags[key] = value.strip().strip('"').lower() == "true"
    except OSError:
        return None
    return flags


def _wii_nand():
    """Dolphin's Wii memory (NAND) as RetroDECK's Dolphin has it set, read the way its sandbox
    sees it; None when it is somewhere this cannot tell."""
    home = _home()
    path = os.path.join(home, RETRODECK_HOME, "data", "dolphin-emu", "Wii")
    try:
        with open(os.path.join(home, DOLPHIN_INI), encoding="utf-8", errors="replace") as f:
            section = ""
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    section = line
                elif section == "[General]" and line.partition("=")[0].strip() == "NANDRootPath":
                    value = line.partition("=")[2].strip()
                    for inside, outside in (("/var/data/", "data"), ("/var/config/", "config")):
                        if value.startswith(inside):
                            value = os.path.join(home, RETRODECK_HOME, outside, value[len(inside):])
                    if value and not _within(value, home):
                        return None  # a place only the sandbox knows: not guessed
                    path = value or path
    except OSError:
        pass
    path = os.path.realpath(path)
    # In the home, or wherever RetroDECK keeps its saves (an SD card, say).
    return path if _within(path, home) or _within(path, _retrodeck_path("saves_path", "saves")) else None


def _disc_id(path):
    """The six letters a Wii or GameCube disc is known by ("RMGP01"), read from the game's file
    (RVZ, WIA, WBFS or a plain image), or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(0x1000)
    except OSError:
        return None
    if head[:4] in (b"RVZ\x01", b"WIA\x01"):
        if head[0x48:0x4C] != b"\x00\x00\x00\x02":
            return None  # a GameCube disc: its saves are not kept this way
        found = head[0x58:0x5E]  # the disc's own header, kept whole after the file's
    elif head[:4] == b"WBFS" and len(head) > 8 and 9 <= head[8] <= 11:
        found = head[1 << head[8]:(1 << head[8]) + 6]
    elif head[0x18:0x1C] == b"\x5d\x1c\x9e\xa3":
        found = head[:6]
    else:
        return None
    found = found.decode("ascii", "replace")
    return found if re.fullmatch(r"[A-Z0-9]{6}", found) else None


def _save_file(game):
    """Where this game's save is on the Deck: (path, kind) or (None, why). The game's file must
    be here (the save is named after it, or found through it) and its console one whose saves
    are known. A save kept as a folder is that folder."""
    kind = SAVE_KINDS.get(_text(game.get("system")))
    rom = game.get("path")
    if not kind:
        return None, "unsupported"
    if not isinstance(rom, str) or not game.get("installed"):
        return None, "not-installed"
    chosen, system = _text(game.get("emulator")), _text(game.get("system"))
    found = _emulators(system)
    # Only when the game really starts with another emulator (a choice RetroDECK no longer has falls
    # back to the default), or when ES-DE's list lacks the one whose saves are kept, so RetroDECK
    # picks. ES-DE's list unreadable: what it starts with is not known, the saves go on as before.
    if chosen and chosen != SAVE_EMULATORS.get(system) and chosen in found:
        return None, "emulator"
    if found and SAVE_EMULATORS.get(system) not in found and not (chosen and chosen in found):
        return None, "emulator"
    if kind[3]:
        nand = _wii_nand()
        found = _disc_id(rom)
        if not nand or not found:
            return None, "layout"
        return os.path.join(nand, "title", WII_GAMES, found[:4].encode("ascii").hex()), kind
    flags = _retroarch_flags()
    if flags is None or flags.get("savefiles_in_content_dir"):
        return None, "layout"  # saves beside the games, or no settings to read: not handled
    root = _retrodeck_path("saves_path", "saves")
    folder = root
    if flags.get("sort_savefiles_by_content_enable"):
        folder = os.path.join(folder, os.path.basename(os.path.dirname(rom)))
    if flags.get("sort_savefiles_enable"):
        folder = os.path.join(folder, kind[2])
    stem = os.path.splitext(os.path.basename(rom))[0]
    if not _one_name(stem):
        return None, "layout"
    path = os.path.join(folder, stem + kind[1])
    if os.path.realpath(os.path.dirname(path)) != os.path.realpath(root) and not _within(os.path.dirname(path), root):
        return None, "layout"
    return path, kind


def _md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _zip_hash(z):
    """RomM's sum of a zip: of each file inside by its name, not of the zip's own bytes."""
    lines = []
    for name in sorted(z.namelist()):
        if not name.endswith("/"):
            with z.open(name) as f:
                digest = hashlib.md5()
                for block in iter(lambda: f.read(CHUNK), b""):
                    digest.update(block)
            lines.append("%s:%s" % (name, digest.hexdigest()))
    return hashlib.md5("\n".join(lines).encode()).hexdigest()


def _folder_files(path):
    """The files of a save kept as a folder, by their names inside it ("data/x.bin"). A link
    anywhere in it is not followed: the save is then left alone."""
    out = {}
    if os.path.islink(path):
        raise _Stop("layout")
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            full = os.path.join(root, name)
            if os.path.islink(full):
                raise _Stop("layout")
            if name in files:
                out[os.path.relpath(full, path).replace(os.sep, "/")] = full
    return out


def _save_here(path, kind):
    if kind[3]:
        return os.path.isdir(path) and not os.path.islink(path) and bool(_folder_files(path))
    return os.path.isfile(path)


def _save_hash(path, kind):
    """The save's sum the way RomM sums what it is sent (a folder as the zip of it)."""
    if not kind[3]:
        return _md5(path)
    lines = ["%s:%s" % (name, _md5(full)) for name, full in sorted(_folder_files(path).items())]
    return hashlib.md5("\n".join(lines).encode()).hexdigest()


def _save_mtime(path, kind):
    """When the save last changed: for a folder, its newest file."""
    if not kind[3]:
        return os.stat(path).st_mtime_ns
    return max((os.stat(f).st_mtime_ns for f in _folder_files(path).values()), default=0)


# ── RetroDECK's folders moved ─────────────────────────────────────────────────────────────────
# RetroDECK can move its folders (to an SD card, say). The games file then still names the old
# places: a game whose file is now in RetroDECK's current folder, at the same place inside it and
# of the same size, has its records follow it. Nothing on the disk is moved, copied or removed.

def _moved_from(game, path, system_dir):
    """The game's system folders it may have been recorded under, other than today's: the
    games folder it was installed under when recorded, then each `<system>` step of its path."""
    system = os.path.basename(system_dir)
    out = []
    root = game.get("roms_root")
    if isinstance(root, str) and os.path.isabs(root):
        out.append(os.path.join(root, system))
    parts = path.split(os.sep)
    out += [os.sep.join(parts[:i + 1]) for i in range(len(parts) - 2, 0, -1) if parts[i] == system]
    return [d for d in dict.fromkeys(out) if d and os.path.realpath(d) != os.path.realpath(system_dir)]


def _moved_path(path, old_dir, system_dir):
    """Where a path recorded under `old_dir` is under `system_dir`, as a real path inside it;
    the path itself when it was not under `old_dir`; None when it would lead out of it."""
    if not isinstance(path, str) or not path.startswith(old_dir + os.sep):
        return path
    rest = path[len(old_dir) + 1:].split(os.sep)
    if not rest or any(p in ("", ".", "..") for p in rest):
        return None
    return _within(os.path.join(system_dir, *rest), system_dir)


def _plain_file(path):
    return isinstance(path, str) and os.path.isfile(path) and not os.path.islink(path)


def _moved_record(rec, old_dir, system_dir):
    """A copy of a record ({"path", "files", "folders", "marks", "part", "disc"}) with its paths
    under `system_dir`, or None when one of them would lead out of it. Marks follow as they were,
    never made anew: a file that kept its size and time is still the add-on's, any other (a copy
    that lost its time, or the player's own file of the same size) is never removed by it."""
    new = dict(rec)
    for key in ("path", "part", "disc"):
        if isinstance(rec.get(key), str):
            new[key] = _moved_path(rec[key], old_dir, system_dir)
            if new[key] is None:
                return None
    for key in ("files", "folders"):
        if isinstance(rec.get(key), list):
            new[key] = [_moved_path(p, old_dir, system_dir) for p in rec[key]]
            if None in new[key]:
                return None
    if isinstance(rec.get("marks"), dict):
        marks = {}
        for path, mark in rec["marks"].items():
            there = _moved_path(path, old_dir, system_dir)
            if there is None:
                return None
            marks[there] = mark
        new["marks"] = marks
    return new


def _moved_whole(rec, new):
    """Whether the moved record's game is all there: its file, of the size it had."""
    path = new.get("path")
    if not _plain_file(path):
        return False
    mark = (rec.get("marks") or {}).get(rec.get("path")) if isinstance(rec.get("marks"), dict) else None
    if isinstance(mark, list) and mark:
        return os.path.getsize(path) == mark[0]
    size = _number(rec.get("size"))
    files = [f for f in new.get("files") or [] if isinstance(f, str)]
    if not size:
        return False
    if len(files) > 1:
        return all(_plain_file(f) for f in files) and sum(os.path.getsize(f) for f in files) == size
    return os.path.getsize(path) == size


def _relocate(game, roms=None):
    """Has the game's records follow RetroDECK's moved games folder, when its file (or a paused
    download's half) is no longer where recorded but is in the same place inside today's folder.
    Changes `game` in place; True when it did."""
    if not isinstance(game, dict):
        return False
    system = _one_name(_text(game.get("system")))
    if not system:
        return False
    roms = roms or _roms_root()
    system_dir = os.path.join(roms, system)
    if not os.path.isdir(system_dir) or _within(system_dir, roms) != os.path.realpath(system_dir):
        return False
    system_dir = os.path.realpath(system_dir)
    installed = bool(game.get("installed")) and isinstance(game.get("path"), str)
    anchor = game.get("path") if installed else game.get("part") if game.get("paused") else None
    if not isinstance(anchor, str) or not os.path.isabs(anchor) or os.path.lexists(anchor):
        return False
    for old_dir in _moved_from(game, anchor, system_dir):
        new = _moved_record(game, old_dir, system_dir)
        if new is None:
            continue
        if installed and not _moved_whole(game, new):
            continue
        if not installed and not (_plain_file(new.get("part")) and new["part"].endswith(".part")):
            continue
        sw = game.get("switching")
        if isinstance(sw, dict):
            new["switching"] = _moved_record(sw, old_dir, system_dir) or sw
        game.clear()
        game.update(new)
        return True
    return False


def _relocate_save(game):
    """Has the save's record follow RetroDECK's moved saves folder: when the save remembered is
    gone from where it was, and the one where saves now go is the very same (its sum)."""
    saves = game.get("saves") if isinstance(game, dict) else None
    last = saves.get("main") if isinstance(saves, dict) else None
    if not isinstance(last, dict) or not isinstance(last.get("path"), str) or not last.get("hash"):
        return False
    if os.path.lexists(last["path"]):
        return False
    try:
        path, kind = _save_file(game)
        if path is None or path == last["path"] or os.path.islink(path) or not _save_here(path, kind):
            return False
        if _save_hash(path, kind) != last["hash"]:
            return False
        last.update(path=path, mtime=_save_mtime(path, kind))
    except (OSError, _Stop):
        return False
    return True


def _relocate_all(data):
    """Under the lock: every game's records follow RetroDECK's moved folders. How many moved."""
    roms = _roms_root()
    moved = 0
    for game in data["games"].values():
        if _relocate(game, roms):
            moved += 1
        _relocate_save(game)
    return moved


def _relocated(appid):
    """Under the lock: the game's record, after following RetroDECK's moved folders when its
    file is not where recorded (the games file and the launcher's list written then)."""
    data = _read_games()
    game = data["games"].get(str(appid))
    if isinstance(game, dict) and ((game.get("installed") and not os.path.isfile(_text(game.get("path"))))
                                   or (game.get("paused") and not os.path.isfile(_text(game.get("part"))))):
        if _relocate(game):
            _write_games(data)
    return game


def _zip_folder(path):
    """A save kept as a folder, as one zip to send or keep (the same files give the same sum)."""
    out = io.BytesIO()
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, full in sorted(_folder_files(path).items()):
            total += os.path.getsize(full)
            if total > SAVE_MOST:
                raise _Stop("save")
            info = zipfile.ZipInfo(name, date_time=time.localtime(os.path.getmtime(full))[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(full, "rb") as f:
                z.writestr(info, f.read())
    return out.getvalue()


def _when(text):
    """A date as RomM writes it, as a number to compare; 0 when it cannot be read."""
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0


def _romm_save(server, key, rom_id, kind, stem):
    """RomM's newest save of this game for this emulator: (the save or None, None) or (None, why)."""
    status, saves = _request(server, "GET", "/api/saves?rom_id=%d&slot=%s" % (rom_id, SAVE_SLOT), key["token"])
    if status == 401:
        _forget_key()
    if status in (401, 403):
        return None, "refused"
    if status != 200 or not isinstance(saves, list):
        return None, "unreachable"
    mine = [
        x for x in saves
        if isinstance(x, dict) and _number(x.get("id")) > 0 and _number(x.get("rom_id")) == rom_id
        # Older uploads carry no emulator at all: the slot and the extension still tell them apart.
        and _text(x.get("emulator")) in (kind[0], "") and _text(x.get("slot")) == SAVE_SLOT
        and "." + _text(x.get("file_extension")).lstrip(".").lower() == kind[1]
        and not x.get("missing_from_fs")
    ]
    mine.sort(key=lambda x: (_when(x.get("updated_at")), _number(x.get("id"))), reverse=True)
    return (mine[0] if mine else None), None


def _copy_aside(path, kind=None):
    """A copy of a save about to be replaced, in a folder beside it; the oldest copies beyond a
    few go, never the one just made. Raises OSError when the copy could not be made: then
    nothing may be replaced. A save kept as a folder is copied as a zip of it, beside the
    console's memory rather than inside it (where the emulator looks for games)."""
    whole = bool(kind and kind[3])
    beside = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(path)))) if whole else os.path.dirname(path)
    folder = os.path.join(beside, SAVE_COPIES_DIR)
    if os.path.islink(folder):
        raise OSError("the copies folder is a link")  # copies must stay beside the save
    os.makedirs(folder, exist_ok=True)
    stem, ext = (os.path.basename(path), ".zip") if whole else os.path.splitext(os.path.basename(path))
    # Named by when it is made, to the millionth of a second: a later copy always sorts later.
    now = time.time()
    stamp, n = time.strftime("%Y%m%d_%H%M%S", time.localtime(now)), int(now % 1 * 1000000)
    copy = os.path.join(folder, "%s_%s_%06d%s" % (stem, stamp, n, ext))
    while os.path.lexists(copy):
        n += 1
        copy = os.path.join(folder, "%s_%s_%06d%s" % (stem, stamp, n, ext))
    if whole:
        raw = _zip_folder(path)
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        with zipfile.ZipFile(copy) as z:
            same = _zip_hash(z) == _save_hash(path, kind)
    else:
        shutil.copy2(path, copy)
        same = os.path.getsize(copy) == os.path.getsize(path) and _md5(copy) == _md5(path)
    if not same:
        os.remove(copy)
        raise OSError("the copy is not the same as the save")
    # Only this game's copies (its name, the date and time, and nothing else), by when they were
    # made: that is in their names, whatever date the save itself carried.
    own = re.compile(re.escape(stem) + r"_(\d{8}_\d{6})(?:_(\d+))?" + re.escape(ext) + r"\Z")
    mine = sorted(
        (m.group(1), int(m.group(2) or 0), f)
        for f in os.listdir(folder)
        for m in [own.match(f)] if m
    )
    for _, _, old in mine[:-SAVE_COPIES]:
        if os.path.join(folder, old) == copy:
            continue
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return copy


def _get_save(server, key, save, whole=False):
    """RomM's save, whole and checked: its bytes. Raises _Stop when it did not come whole or
    there is nothing to check it against. `whole`: a folder's zip, summed by what is inside."""
    req, _, ctx = _prepare(server, "GET", "/api/saves/%d/content" % _number(save.get("id")), key["token"], accept="*/*")
    try:
        with _opener(ctx).open(req, timeout=STALL) as r:
            raw = r.read(SAVE_MOST + 1)
    except urllib.error.HTTPError as e:
        raise _Stop("refused" if e.code in (401, 403) else "unreachable")
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
        raise _Stop("unreachable")
    size = _number(save.get("file_size_bytes"))
    told = _text(save.get("content_hash")).strip().lower()
    md5 = told if len(told) == 32 and all(c in "0123456789abcdef" for c in told) else ""
    if len(raw) > SAVE_MOST or not raw:
        raise _Stop("unreachable")
    # What RomM said of it must hold: its size, and its sum when RomM gives one. With neither
    # there is no telling a whole save from a cut one, and it is not taken.
    if not size and not md5:
        raise _Stop("unreachable")
    if size and len(raw) != size:
        raise _Stop("unreachable")
    if md5:
        try:
            got = _zip_hash(zipfile.ZipFile(io.BytesIO(raw))) if whole else hashlib.md5(raw).hexdigest()
        except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
            raise _Stop("unreachable")
        if got != md5:
            raise _Stop("unreachable")
    return raw


def _put_save(path, raw):
    """Writes a save where the game's save goes, whole or not at all."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.vistazo-part-%d" % (path, os.getpid())
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        decky.logger.error("Save not written: %s", e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise _Stop("disk")


def _put_folder(path, raw):
    """Puts a save kept as a folder in place from its zip, whole or not at all: unpacked beside
    it first, then swapped in."""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
        members = [m for m in z.infolist() if not m.is_dir()]
    except (zipfile.BadZipFile, ValueError):
        raise _Stop("save")
    if not members or sum(m.file_size for m in members) > SAVE_MOST:
        raise _Stop("save")
    tmp = "%s.vistazo-part-%d" % (path, os.getpid())
    old = "%s.vistazo-old-%d" % (path, os.getpid())
    try:
        for leftover in (tmp, old):
            if os.path.lexists(leftover):
                shutil.rmtree(leftover)
        os.makedirs(tmp)
        for m in members:
            parts = m.filename.split("/")
            if m.filename.startswith("/") or "\\" in m.filename or any(p in ("", ".", "..") or "\0" in p for p in parts):
                raise _Stop("save")
            target = os.path.join(tmp, *parts)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(m) as src, open(target, "xb") as out:
                shutil.copyfileobj(src, out)
                out.flush()
                os.fsync(out.fileno())
        if os.path.lexists(path):
            os.rename(path, old)
        try:
            os.rename(tmp, path)
        except OSError:
            if os.path.lexists(old):
                os.rename(old, path)  # the save that was there goes back where it was
            raise
        # The new save is in place: the old folder going is tidying, never a failure.
        shutil.rmtree(old, ignore_errors=True)
    except (OSError, zipfile.BadZipFile, RuntimeError) as e:
        decky.logger.error("Save not written: %s", e)
        if os.path.lexists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        raise _Stop("disk")
    except _Stop:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _send_save(server, key, rom_id, kind, path, name=""):
    """Sends the save to RomM as a new version in the slot. Returns RomM's record of it."""
    if kind[3]:
        content = _zip_folder(path)
    else:
        if not 0 < os.path.getsize(path) <= SAVE_MOST:
            raise _Stop("save")
        with open(path, "rb") as f:
            content = f.read()
    if not 0 < len(content) <= SAVE_MOST:
        raise _Stop("save")
    name = (name or os.path.basename(path)).replace('"', "").replace("\r", "").replace("\n", "")
    edge = "vistazo" + hashlib.sha256(os.urandom(16)).hexdigest()[:24]
    body = (
        ("--%s\r\nContent-Disposition: form-data; name=\"saveFile\"; filename=\"%s\"\r\n"
         "Content-Type: application/octet-stream\r\n\r\n" % (edge, name)).encode("utf-8")
        + content + ("\r\n--%s--\r\n" % edge).encode("ascii")
    )
    query = "/api/saves?rom_id=%d&emulator=%s&slot=%s&autocleanup=true&autocleanup_limit=%d" % (
        rom_id, urllib.parse.quote(kind[0], safe=""), SAVE_SLOT, SAVE_VERSIONS)
    status, reply = _device_save_post(server, key, query, (body, "multipart/form-data; boundary=" + edge))
    if status == 401:
        _forget_key()
    if status in (401, 403):
        raise _Stop("refused")
    if status not in (200, 201) or not isinstance(reply, dict) or _number(reply.get("id")) <= 0:
        raise _Stop("unreachable")
    return reply


def _sync_save(server, key, game, choice="", deadline=None, late=None):
    """Brings one game's save in step with RomM. Returns ({"state": ...}, what to remember).
    States: "synced" (nothing to do), "sent", "brought", "none" (no save anywhere yet),
    "conflict" (both changed: nothing touched; `choice` "deck" or "romm" settles it), or why it
    could not be done ("unsupported", "not-installed", "layout", "unreachable", "refused",
    "disk", "save"). A save is not replaced after `deadline` (time.monotonic()), nor once `late()`
    says so: the game is being started then, and its emulator may already be reading the save
    (the answer is "late")."""
    path, kind = _save_file(game)
    if path is None:
        return {"state": kind}, None
    rom_id = _number(game.get("rom_id"))
    stem = os.path.splitext(os.path.basename(path))[0]
    theirs, why = _romm_save(server, key, rom_id, kind, stem)
    if why:
        return {"state": why}, None
    last = (game.get("saves") or {}).get("main") if isinstance(game.get("saves"), dict) else None
    # What was remembered is about one file: with the save now looked for elsewhere (RetroArch
    # sorts them another way), it says nothing about this one.
    last = last if isinstance(last, dict) and last.get("path") == path else {}
    if os.path.islink(path) or (os.path.lexists(path) and os.path.isdir(path) != kind[3]):
        return {"state": "layout"}, None  # a link, or not the shape the save should have: left alone
    # A folder's save goes to RomM named after the game.
    name = os.path.splitext(os.path.basename(_text(game.get("path"))))[0] + kind[1] if kind[3] else ""

    def remember(save, state):
        return {"state": state}, {
            "path": path, "hash": _save_hash(path, kind), "mtime": _save_mtime(path, kind),
            "server_id": _number(save.get("id")), "server_updated_at": _text(save.get("updated_at")),
            "server_hash": _text(save.get("content_hash")), "synced_at": int(time.time()),
        }

    try:
        here = _save_here(path, kind)
        mine = _save_hash(path, kind) if here else ""

        def bring(state="brought"):
            # RomM's save first, whole and checked: a failed try costs nothing here.
            raw = _get_save(server, key, theirs, kind[3])
            # Then the save here once more: if it changed meanwhile (the game was started), or
            # one appeared, it is not what was decided about, and nothing is replaced.
            now_here = _save_here(path, kind) and not os.path.islink(path)
            if now_here != here or (now_here and _save_hash(path, kind) != mine):
                return {"state": "busy"}, None
            if (deadline is not None and time.monotonic() > deadline) or (late is not None and late()):
                return {"state": "late"}, None
            if here:
                _copy_aside(path, kind)  # raises when the copy cannot be made: then nothing is replaced
            if kind[3]:
                _put_folder(path, raw)
            else:
                _put_save(path, raw)
            _device_brought(server, key, theirs)
            return remember(theirs, state)

        def send(state="sent"):
            return remember(_send_save(server, key, rom_id, kind, path, name), state)

        if not here and not theirs:
            return {"state": "none"}, None
        if not here:
            return bring()
        if not theirs:
            return send()
        same_as_theirs = mine == _text(theirs.get("content_hash")).lower() or (
            mine == last.get("hash") and _number(theirs.get("id")) == last.get("server_id")
            and _text(theirs.get("updated_at")) == last.get("server_updated_at"))
        if same_as_theirs:
            return remember(theirs, "synced")
        if choice == "romm":
            return bring()
        if choice == "deck":
            return send()
        mine_changed = mine != last.get("hash")
        theirs_changed = _number(theirs.get("id")) != last.get("server_id") or _text(theirs.get("updated_at")) != last.get("server_updated_at")
        if last and not mine_changed and theirs_changed:
            return bring()
        if last and mine_changed and not theirs_changed:
            return send()
        return {"state": "conflict", "deck": int(_save_mtime(path, kind) / 1e9), "romm": int(_when(theirs.get("updated_at")))}, None
    except _Stop as stop:
        return {"state": str(stop)}, None
    except OSError as e:
        decky.logger.error("Save not synced: %s", e)
        return {"state": "disk"}, None


# ── BIOS ────────────────────────────────────────────────────────────────────────────────────────
# Some consoles need files of their own to run (their BIOS). RomM keeps them by console; this
# says which of them are already in RetroDECK's BIOS folder and brings the missing ones, each
# checked against RomM's own sum, never over a file that is already there.
BIOS_MOST = 256 * 1024 * 1024
BIOS_DEPTH = 3  # how deep in RetroDECK's BIOS folder a file is looked for by name
# Console (RetroDECK's name for it) -> the folder inside RetroDECK's BIOS folder where its files go
# ("" is the folder itself). Taken from RetroDECK's own list of BIOS files, in each component's
# component_manifest.json: nearly all go in the folder itself, Flycast's in "dc" and PICO-8's in
# "pico-8". PS2's go wherever PCSX2 is told to look (_bios_folder). Left out on purpose: the
# GameCube's (Dolphin wants them among the saves, one folder per region) and MAME's arcade sets.
BIOS_FOLDERS = {
    "nds": "", "wii": "", "ps2": "", "psp": "", "n3ds": "",
    "psx": "", "saturn": "", "segacd": "", "sega32x": "", "megadrive": "", "mastersystem": "", "gamegear": "",
    "dreamcast": "dc", "naomi": "dc", "atomiswave": "dc",
    "pcengine": "", "pcenginecd": "", "pcfx": "", "3do": "", "neogeo": "", "neogeocd": "", "ngp": "", "ngpc": "",
    "gb": "", "gbc": "", "gba": "", "nes": "", "fds": "", "snes": "", "satellaview": "", "sufami": "", "n64dd": "",
    "pokemini": "", "atari5200": "", "atari7800": "", "atari800": "", "atarilynx": "", "atarijaguar": "",
    "atarist": "", "colecovision": "", "intellivision": "", "odyssey2": "", "videopac": "", "vectrex": "",
    "channelf": "", "scv": "", "vsmile": "", "cdimono1": "", "amiga": "", "c64": "", "amstradcpc": "", "msx": "",
    "pc88": "", "pc98": "", "x68000": "", "zxspectrum": "", "apple2": "", "apple2gs": "", "macintosh": "",
    "enterprise": "", "j2me": "", "xbox": "", "pico8": "pico-8",
}
# RomM's platform -> RetroDECK's console, for the BIOS files only: wider than SYSTEMS, since a
# console's BIOS is worth having before Vistazo knows where its games go.
BIOS_SYSTEMS = {
    **SYSTEMS,
    "psx": "psx", "ps": "psx", "saturn": "saturn", "segacd": "segacd", "sega32": "sega32x", "genesis": "megadrive",
    "sms": "mastersystem", "gamegear": "gamegear", "dc": "dreamcast", "naomi": "naomi", "naomi2": "naomi",
    "naomigd": "naomi", "atomiswave": "atomiswave", "tg16": "pcengine", "turbografx-cd": "pcenginecd",
    "pc-fx": "pcfx", "3do": "3do", "neogeoaes": "neogeo", "neogeomvs": "neogeo", "neo-geo-cd": "neogeocd",
    "neo-geo-pocket": "ngp", "neo-geo-pocket-color": "ngpc", "gb": "gb", "gbc": "gbc", "gba": "gba", "nes": "nes",
    "fds": "fds", "snes": "snes", "satellaview": "satellaview", "sufami-turbo": "sufami", "64dd": "n64dd",
    "pokemon-mini": "pokemini", "atari5200": "atari5200", "atari7800": "atari7800", "atari800": "atari800",
    "atari8bit": "atari800", "lynx": "atarilynx", "jaguar": "atarijaguar", "atari-st": "atarist",
    "colecovision": "colecovision", "intellivision": "intellivision", "odyssey-2": "odyssey2",
    "videopac-g7400": "videopac", "vectrex": "vectrex", "fairchild-channel-f": "channelf",
    "epoch-super-cassette-vision": "scv", "vsmile": "vsmile", "philips-cd-i": "cdimono1", "amiga": "amiga",
    "c64": "c64", "acpc": "amstradcpc", "msx": "msx", "msx2": "msx", "msx2plus": "msx",
    "pc-8800-series": "pc88", "pc-9800-series": "pc98", "sharp-x68000": "x68000", "zxs": "zxspectrum",
    "appleii": "apple2", "apple-iigs": "apple2gs", "mac": "macintosh", "enterprise": "enterprise",
    "j2me": "j2me", "xbox": "xbox", "pico": "pico8",
}
PCSX2_INI = os.path.join(".var", "app", "net.retrodeck.retrodeck", "config", "PCSX2", "inis", "PCSX2.ini")
_bios_all_job = {"state": "idle"}  # bringing every console's BIOS: see bios_all


def _bios_names(root):
    """The names of the files already in RetroDECK's BIOS folder (not far down), with their sizes."""
    found = {}
    base = root.rstrip(os.sep).count(os.sep)
    for folder, dirs, files in os.walk(root):
        if folder.count(os.sep) - base >= BIOS_DEPTH:
            dirs[:] = []
        for name in files:
            try:
                found.setdefault(name.lower(), set()).add(os.path.getsize(os.path.join(folder, name)))
            except OSError:
                pass
    return found


def _bios_list(server, key, platform_id):
    """RomM's BIOS files for one console: (list, None) or (None, why)."""
    status, files = _request(server, "GET", "/api/firmware?platform_id=%d" % platform_id, key["token"])
    if status == 401:
        _forget_key()
    if status in (401, 403):
        return None, "refused"
    if status != 200 or not isinstance(files, list):
        return None, "unreachable"
    out = []
    for f in files:
        name = _one_name(f.get("file_name")) if isinstance(f, dict) else None
        if not name or _number(f.get("id")) <= 0 or f.get("missing_from_fs") or _number(f.get("platform_id")) != platform_id:
            continue
        md5 = _text(f.get("md5_hash")).strip().lower()
        out.append({"id": _number(f["id"]), "name": name, "size": _number(f.get("file_size_bytes")),
                    "md5": md5 if len(md5) == 32 and all(c in "0123456789abcdef" for c in md5) else ""})
    return out, None


def _bios_check(server, key, game):
    """What RomM has for this game's console and what of it is not on the Deck yet:
    {"state": "ok" | "missing" | "none" (RomM has nothing for it), "missing": [...], "files": n}
    or {"state": why}."""
    system = _text(game.get("system"))
    if system not in BIOS_FOLDERS:
        return {"state": "unsupported"}, []
    rom, why = _rom(server, key, _number(game.get("rom_id")))
    if not rom:
        return {"state": why}, []
    if _number(rom.get("platform_id")) <= 0:
        return {"state": "unsupported"}, []
    files, why = _bios_list(server, key, _number(rom.get("platform_id")))
    if files is None:
        return {"state": why}, []
    root = _retrodeck_path("bios_path", "bios")
    here = _bios_names(root) if os.path.isdir(root) else {}
    missing = [f for f in files if f["size"] not in here.get(f["name"].lower(), ())]
    state = "none" if not files else "missing" if missing else "ok"
    return {"state": state, "files": len(files), "missing": [f["name"] for f in missing]}, missing


def _bios_folder(root, system):
    """Where one console's BIOS files go. PS2's: the folder PCSX2's settings name, when it is
    RetroDECK's BIOS folder or inside it (RetroDECK tells PCSX2 the folder itself)."""
    if system == "ps2":
        try:
            with open(os.path.join(_home(), PCSX2_INI), encoding="utf-8", errors="replace") as f:
                for line in f:
                    name, _, value = line.partition("=")
                    if name.strip() == "Bios" and os.path.isabs(value.strip()):
                        told = os.path.realpath(value.strip())
                        if told == root or _within(told, root):
                            return told
                        break
        except OSError:
            pass
    return os.path.join(root, *[p for p in BIOS_FOLDERS[system].split("/") if p])


def _bios_platforms(server, key):
    """RomM's consoles with games and BIOS files: ([{"id", "system", "name"}], [names of the ones
    whose BIOS folder in RetroDECK is not known], None) or (None, None, why)."""
    status, platforms = _request(server, "GET", "/api/platforms", key["token"])
    if status == 401:
        _forget_key()
    if status in (401, 403):
        return None, None, "refused"
    if status != 200 or not isinstance(platforms, list):
        return None, None, "unreachable"
    known, unknown = [], []
    for p in platforms:
        if not isinstance(p, dict) or _number(p.get("id")) <= 0 or _number(p.get("rom_count")) <= 0:
            continue
        # RomM says how many BIOS files each console has; one that says none is not asked.
        if ("firmware" in p or "firmware_count" in p) and not p.get("firmware") and _number(p.get("firmware_count")) <= 0:
            continue
        name = _text(p.get("display_name")) or _text(p.get("name")) or _text(p.get("slug"))
        system = BIOS_SYSTEMS.get(_text(p.get("slug"))) or BIOS_SYSTEMS.get(_text(p.get("fs_slug")))
        if system in BIOS_FOLDERS:
            known.append({"id": _number(p["id"]), "system": system, "name": name})
        else:
            unknown.append(name)
    return known, unknown, None


def _bios_all(server, key, job):
    """Every BIOS file RomM has for its consoles with games that RetroDECK lacks, one at a time,
    each as _bios_bring brings it. Keeps `job` up to date for bios_all_state."""
    platforms, unknown, why = _bios_platforms(server, key)
    if platforms is None:
        return {"state": why}
    root = _retrodeck_path("bios_path", "bios")
    wanted = []
    for p in platforms:
        files, why = _bios_list(server, key, p["id"])
        if files is None:
            return {"state": why}
        wanted += [(p, f) for f in files]

    def here():
        return _bios_names(root) if os.path.isdir(root) else {}

    known = here()
    missing = [(p, f) for p, f in wanted if f["size"] not in known.get(f["name"].lower(), ())]
    job.update(done=0, total=len(missing), file="")
    brought, failed, why = 0, [], None
    for p, f in missing:
        job["file"] = f["name"]
        # The same file for two consoles, brought for the first one, counts for the second.
        if f["size"] in here().get(f["name"].lower(), ()):
            brought += 1
        else:
            try:
                _bios_bring(server, key, p["system"], f)
                brought += 1
            except _Stop as stop:
                why = str(stop)
                failed.append(f["name"])
        job["done"] += 1
    return {"state": "missing" if failed else "ok", "consoles": len(platforms), "files": len(wanted),
            "brought": brought, "failed": failed, "unknown": unknown, **({"why": why} if why else {})}


def _bios_bring(server, key, system, f):
    """One BIOS file into its folder, whole and checked, or not at all."""
    root = _retrodeck_path("bios_path", "bios")
    folder = _bios_folder(root, system)
    # Checked before anything is made: a link on the way must not lead outside.
    probe = folder
    while not os.path.lexists(probe) and probe != os.path.dirname(probe):
        probe = os.path.dirname(probe)
    if os.path.realpath(probe) != os.path.realpath(root) and not _within(probe, root):
        raise _Stop("retrodeck")
    if not f["md5"]:
        raise _Stop("damaged")  # nothing to check it against
    target = os.path.join(folder, f["name"])
    part = "%s.vistazo-part-%d" % (target, os.getpid())
    job = {"state": "downloading", "done": 0, "total": f["size"], "cancel": threading.Event()}
    try:
        os.makedirs(folder, exist_ok=True)
        if os.path.lexists(target) or os.path.lexists(part):
            raise _Stop("exists")
        if not 0 < f["size"] <= BIOS_MOST or f["size"] + SPARE > shutil.disk_usage(folder).free:
            raise _Stop("space")
        _fetch(server, key["token"], "/api/firmware/%d/content/%s" % (f["id"], urllib.parse.quote(f["name"], safe="")),
               part, job, f["size"], ("md5", f["md5"]))
        if os.path.getsize(part) != f["size"]:
            raise _Stop("damaged")
        os.link(part, target)  # fails when something took the name meanwhile: never written over
    except FileExistsError:
        raise _Stop("exists")
    except OSError as e:
        decky.logger.error("BIOS file not kept: %s", e)
        raise _Stop("disk")
    finally:
        try:
            if os.path.lexists(part):
                os.remove(part)
        except OSError:
            pass


def _probe(server):
    """Whether RomM answers at this address, without any key: "ok", "certificate" (its
    certificate could not be checked), "not-romm" (something else answers) or "unreachable"."""
    req, _, ctx = _prepare(server, "GET", "/api/heartbeat")
    try:
        with _opener(ctx).open(req, timeout=TIMEOUT) as r:
            raw = r.read(65536)
    except urllib.error.HTTPError:
        return "not-romm"
    except urllib.error.URLError as e:
        return "certificate" if isinstance(e.reason, ssl.SSLCertVerificationError) else "unreachable"
    except ssl.SSLCertVerificationError:
        return "certificate"
    except http.client.HTTPException:
        return "not-romm"  # something that does not speak HTTP answers on that port
    except (OSError, ValueError):
        return "unreachable"
    # Something answered: RomM only if it is RomM's own answer (a web page there is not).
    try:
        body = json.loads(raw or b"null")
    except ValueError:
        return "not-romm"
    return "ok" if isinstance(body, dict) and isinstance(body.get("SYSTEM"), dict) else "not-romm"


# RomM's collections, each of which the player may show in Steam as a collection of its own. RomM
# has three sorts: the ones people make, smart ones (a saved search) and the ones it makes itself
# from what it knows of each game (series, franchise, genre, company, way of playing). Which are
# shown is kept here, by sort and number: "standard:3", "smart:1", "virtual:<RomM's id>".
COLLECTIONS_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "collections.json")
VIRTUAL_KINDS = ("collection", "franchise", "genre", "company", "mode")
COLLECTIONS_MAX = 500  # collections listed or kept at most
COLLECTION_ROMS_MAX = 20000


def _shown_collections():
    try:
        with open(COLLECTIONS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        keys = data.get("on") if isinstance(data, dict) else None
        if isinstance(keys, list):
            return [k for k in keys if _collection_key_ok(k)][:COLLECTIONS_MAX]
    except (OSError, ValueError):
        pass
    return []


def _collection_key_ok(key):
    if not isinstance(key, str) or len(key) > 200 or any(c.isspace() and c != " " for c in key):
        return False
    kind, _, cid = key.partition(":")
    if kind in ("standard", "smart"):
        return cid.isdigit() and int(cid) > 0
    return kind == "virtual" and bool(cid.strip())


def _collection_row(kind, c, sort=""):
    """What the settings need of one of RomM's collections, or None for anything unexpected."""
    if not isinstance(c, dict):
        return None
    # RomM numbers the ones people make and the smart ones; its own sorts carry a text id.
    cid = c.get("id")
    whole = isinstance(cid, int) and not isinstance(cid, bool)
    cid = (cid if isinstance(cid, str) else "") if kind == "virtual" else (str(cid) if whole else "")
    key = "%s:%s" % (kind, cid)
    if not cid or not _collection_key_ok(key):
        return None
    name = " ".join(_text(c.get("name")).split())[:80]
    roms = c.get("rom_ids")
    roms = sorted({r for r in roms if isinstance(r, int) and not isinstance(r, bool) and r > 0}) if isinstance(roms, list) else []
    if not name:
        return None
    return {"key": key, "kind": kind, "sort": sort, "name": name, "roms": roms[:COLLECTION_ROMS_MAX]}


def _collections(server, key):
    """RomM's collections as rows, or (None, why). The ones people make must come; smart and
    RomM's own sorts are added when they come, so one that fails leaves the rest."""
    token = key["token"]
    status, reply = _request(server, "GET", "/api/collections", token)
    if status == 401:
        _forget_key()
    if status in (401, 403):
        return None, "refused"
    if status != 200 or not isinstance(reply, list):
        return None, "unreachable"
    rows = [_collection_row("standard", c) for c in reply]
    status, reply = _request(server, "GET", "/api/collections/smart", token)
    if status == 200 and isinstance(reply, list):
        rows += [_collection_row("smart", c) for c in reply]
    for sort in VIRTUAL_KINDS:
        status, reply = _request(server, "GET", "/api/collections/virtual?type=" + sort, token)
        if status == 200 and isinstance(reply, list):
            rows += [_collection_row("virtual", c, sort) for c in reply]
    seen, out = set(), []
    for r in rows:
        if r and r["key"] not in seen:
            seen.add(r["key"])
            out.append(r)
    return out[:COLLECTIONS_MAX], None


# ── Play time ─────────────────────────────────────────────────────────────────────
# Each time a game is played from its entry, the session goes to RomM (its start, its end and the
# time played, which leaves out the Deck's sleep). Sessions wait on the Deck while RomM does not
# answer or the key may not write them, and go once it does.
PLAY_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "play.json")
PLAY_MOST = 1000  # sessions kept waiting at most: the oldest go first
PLAY_BATCH = 100  # RomM takes at most this many at a time
PLAY_SHORTEST = 60  # seconds: a shorter one (a start that failed) is not kept
_playing = {}  # appid -> (rom_id, when it started, monotonic clock then)
_play_lock = threading.Lock()


def _iso(seconds):
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_play():
    try:
        with open(PLAY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        sessions = data.get("sessions") if isinstance(data, dict) else None
        return [s for s in sessions if isinstance(s, dict)] if isinstance(sessions, list) else []
    except (OSError, ValueError):
        return []


def _play_key():
    """(server, key) when the stored key is for this server and may write play time, else (None, why)."""
    key = _read_key()
    server = _server()
    if not key:
        return None, "unlinked"
    if not server or key["url"] != server["url"]:
        return None, "moved"
    if "roms.user.write" not in _key_scopes(key):
        return None, "limited"
    return server, key


def _play_keep(session):
    with _play_lock:
        waiting = (_read_play() + [session])[-PLAY_MOST:]
        return _write_private(PLAY_FILE, {"sessions": waiting})


def _play_send():
    """Sends the sessions waiting, when the key may. Returns how many still wait."""
    server, key = _play_key()
    with _play_lock:
        waiting = _read_play()
        if not server:
            return len(waiting)
        device = _device(server, key) if waiting else None
        while waiting:
            batch = waiting[:PLAY_BATCH]
            body = {"sessions": batch, "device_id": device} if device else {"sessions": batch}
            status, reply = _request(server, "POST", "/api/play-sessions", key["token"], body)
            if status in (400, 422):
                # Never going to be taken: not kept waiting for ever.
                decky.logger.warning("RomM refused %d play sessions", len(batch))
            elif status not in (200, 201) or not isinstance(reply, dict):
                break  # asleep, unreachable or not allowed: they wait
            waiting = waiting[PLAY_BATCH:]
            if not _write_private(PLAY_FILE, {"sessions": waiting}):
                break
        return len(waiting)


# ── This Deck as one of the player's devices in RomM ────────────────────────────────────────────
# RomM keeps a list of each player's devices and marks the saves and play sessions with the one
# they came from. With a key that may modify devices, the add-on registers this device once, as a
# Steam Deck or as a PC (with its system's name), and keeps the id RomM gives it beside the address it is for. Nothing that tells who
# or where goes with it: no host name, user name or network address (RomM matches devices by
# those, so without them it always makes a new one: the id kept is what stops a second). The id is
# checked once a run, and when RomM no longer knows it (deleted there) the Deck is registered
# again. Without the scope, or with RomM not answering, saves and play time go as before.
DEVICE_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "device.json")
DEVICE_CLIENT = "Vistazo RomM"
# Valve's own board names for the Steam Deck (LCD and OLED), as the firmware reports them.
DECK_BOARDS = {"Jupiter", "Galileo"}


def _device_about():
    """What the device is, without who or where: a Steam Deck, or a PC with its system's name."""
    try:
        with open("/sys/devices/virtual/dmi/id/product_name", encoding="utf-8") as f:
            board = f.read().strip()
    except OSError:
        board = ""
    if board in DECK_BOARDS:
        return {"name": "Steam Deck", "platform": "SteamOS", "client": DEVICE_CLIENT}
    system = "Linux"
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                if key == "NAME" and value.strip('"'):
                    system = value.strip('"')[:40]
    except OSError:
        pass
    return {"name": "PC", "platform": system, "client": DEVICE_CLIENT}
_device_lock = threading.Lock()
_device_seen = set()  # (address, key id, device id) RomM confirmed during this run


def _device_id_ok(v):
    return isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,255}", v) is not None


def _read_device():
    try:
        with open(DEVICE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("url"), str) and _device_id_ok(data.get("id")):
        return data
    return None


def _device_allowed(key):
    return isinstance(key, dict) and _key_scopes(key) >= SCOPES_DEVICE


def _device(server, key, gone=None):
    """This Deck's id in RomM, registering it when there is none for this address; None when the
    key may not, or RomM did not register it. `gone`: an id RomM just refused, checked again now."""
    if not server or not _device_allowed(key):
        return None
    with _device_lock:
        kept = _read_device()
        known = kept["id"] if kept and kept["url"] == server["url"] else None
        seen = (server["url"], key.get("id"), known)
        if known and gone == known:
            _device_seen.discard(seen)
        if known and seen in _device_seen:
            return known
        if known:
            # RomM has no way to only look a device up with this scope: an update that changes
            # nothing answers whether it is still there.
            status, _ = _request(server, "PUT", "/api/devices/" + urllib.parse.quote(known, safe=""), key["token"], {})
            if status == 200:
                _device_seen.add(seen)
                return known
            if status in (401, 403):
                return None
            if status != 404:
                return known  # not answering now: still the one to name
        status, reply = _request(server, "POST", "/api/devices", key["token"], _device_about())
        new = reply.get("device_id") if status in (200, 201) and isinstance(reply, dict) else None
        if not _device_id_ok(new):
            return None
        if not _write_private(DEVICE_FILE, {"url": server["url"], "id": new}):
            return None
        decky.logger.info("Registered in RomM as a device")
        _device_seen.add((server["url"], key.get("id"), new))
        return new


def _device_save_post(server, key, query, raw):
    """Posts a save to RomM, as this Deck when it may. Returns _request's (status, reply)."""
    def post(device):
        if not device:
            return _request(server, "POST", query, key["token"], raw=raw)
        mine = query + "&device_id=" + urllib.parse.quote(device, safe="")
        status, reply = _request(server, "POST", mine, key["token"], raw=raw)
        if status == 409:
            # RomM refuses a device's save when it has not seen the slot's newest from that device
            # (saves sent before it was one, or brought without telling). The add-on compared
            # before sending: the save goes as a new version, as it does from no device.
            status, reply = _request(server, "POST", mine + "&overwrite=true", key["token"], raw=raw)
        return status, reply

    device = _device(server, key)
    status, reply = post(device)
    if device and status == 404:
        # The device, or the game: the device is checked, and registered again when it is gone.
        again = _device(server, key, gone=device)
        if again != device:
            status, reply = post(again)
    elif device and status == 403:
        status, reply = post(None)  # RomM no longer lets the key name devices: as before
    return status, reply


def _device_brought(server, key, save):
    """Tells RomM this Deck now has `save` (just brought, and checked), so RomM shows it and
    takes the next save from here as following it. Best effort: nothing depends on it."""
    device = _device(server, key)
    if device and _number(save.get("id")) > 0:
        _request(server, "POST", "/api/saves/%d/downloaded" % _number(save.get("id")), key["token"],
                 {"device_id": device, "content_hash": _text(save.get("content_hash")) or None})
# ── Steam Input layout per console ────────────────────────────────────────────────────────────
# The player may pick, per console, the Steam Input layout its entries use on the Deck's own
# controls. Only the choice is kept here; Steam is asked to select it by the frontend (src/input.ts)
# through SteamClient.Input, as Steam's own layout picker does. No Steam file is written.
LAYOUTS_FILE = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "layouts.json")
# Each choice -> the Steam Deck template it selects, in the order offered. Valve's ship with
# Steam; RetroDECK's is there once RetroDECK installed its templates (its own Steam entries use it).
INPUT_LAYOUTS = (
    ("retrodeck", "RetroDECK_controller_steamdeck_neptune_full.vdf"),
    ("gamepad", "controller_neptune_gamepad_joystick.vdf"),
    ("mouse", "controller_neptune_gamepad+mouse.vdf"),
)
STEAM_TEMPLATES = (
    os.path.join(".steam", "steam", "controller_base", "templates"),
    os.path.join(".local", "share", "Steam", "controller_base", "templates"),
)


def _input_layouts():
    """The choices whose template Steam has: [(choice, template file)]."""
    folders = [os.path.join(_home(), f) for f in STEAM_TEMPLATES]
    return [(k, name) for k, name in INPUT_LAYOUTS if any(os.path.isfile(os.path.join(d, name)) for d in folders)]


def _read_layouts():
    """{console: choice} as the player left it; anything unreadable reads as no choice."""
    try:
        with open(LAYOUTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        chosen = data.get("systems") if isinstance(data, dict) else None
        if isinstance(chosen, dict):
            return {str(s): str(k) for s, k in chosen.items() if isinstance(k, str)}
    except (OSError, ValueError):
        pass
    return {}


def _layout_url(choice):
    """The url Steam selects a template by, or "" when there is nothing to apply."""
    name = dict(_input_layouts()).get(choice)
    return "template://" + name if name else ""


def _entries_by_system():
    """{console: [appid]} of the add-on's entries."""
    out = {}
    for appid, game in _read_games()["games"].items():
        system = _text(game.get("system")) if isinstance(game, dict) else ""
        if system and _number(appid) > 0:
            out.setdefault(system, []).append(_number(appid))
    return out


# ── Punktfunk ─────────────────────────────────────────────────────────────────────────────────
# Punktfunk (a game-streaming host) shows RomM's games on a TV through a plugin of its own, which
# lists them from a catalog written here and asks over a socket for a game to be brought and how
# its download goes. The download is this add-on's own: the same file in RetroDECK's folder, the
# same Steam entry and launcher, the same saves kept in step. Only where Punktfunk is set up
# (its folder in ~/.config): on a Deck without it nothing here runs.
PF_DIR = os.path.join(RUNTIME, "punktfunk")
PF_SOCKET = os.path.join(PF_DIR, "vistazo.sock")
PF_CATALOG = os.path.join(PF_DIR, "catalog.json")
PF_COVERS = os.path.join(PF_DIR, "covers")
# What Punktfunk runs to start a game whose Steam entry did not exist yet when it was chosen.
PLAYER = os.path.join(RUNTIME, "vistazo-play")
PLAYER_SOURCE = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "vistazo-play")
PF_LIBRARY_EVERY = 600  # seconds between two readings of RomM's library for the catalog
PF_LOOK_EVERY = 3  # seconds between two looks at whether the catalog changed
PF_ADD_WAIT = 60  # seconds Steam has to make the entry of a game asked for from the TV
PF_COVERS_AT_ONCE = 8  # covers of games without an entry fetched in one round
_pf = {"groups": None, "read": 0.0, "adding": {}, "written": None, "server": None, "task": None}
# How a download of the add-on's reads on Punktfunk's screens: its words for what went wrong.
PF_WHY = {
    "unlinked": "Vistazo RomM no está conectado con RomM.",
    "moved": "La dirección de RomM cambió: conecta Vistazo RomM otra vez.",
    "limited": "La llave de Vistazo RomM no permite descargar juegos.",
    "refused": "RomM no dejó descargarlo: conecta Vistazo RomM otra vez.",
    "unreachable": "RomM no contesta. Elígelo de nuevo para seguir donde se quedó.",
    "space": "No hay sitio en el disco para este juego.",
    "disk": "No se pudo escribir en el disco.",
    "damaged": "El archivo llegó dañado: no coincide con el de RomM.",
    "game": "RomM ya no tiene este juego.",
    "system": "Vistazo RomM todavía no sabe en qué carpeta de RetroDECK va esta consola.",
    "retrodeck": "No se encontró la carpeta de juegos de RetroDECK.",
    "exists": "Ya hay otro archivo con ese nombre en la carpeta de la consola.",
    "archive": "El archivo de RomM no se pudo descomprimir.",
    "steam": "Steam no pudo crear la entrada del juego.",
    "late": "Steam no creó la entrada del juego a tiempo. ¿Está abierto?",
    "busy": "El juego se está descargando: páralo antes.",
    "not-ours": "Ese archivo no lo trajo Vistazo RomM, así que no lo borra.",
}


def _pf_on():
    return os.path.isdir(os.path.join(_home(), ".config", "punktfunk"))


def _pf_say(why):
    return PF_WHY.get(_text(why), "La descarga falló.")


def _pf_groups_kept():
    """The games and their versions as the catalog last listed them, so a game keeps its number
    on Punktfunk while RomM is asleep: [{"key", "versions", "rom_id", "name", "platform",
    "system", "size"}]."""
    try:
        with open(PF_CATALOG, encoding="utf-8") as f:
            listed = json.load(f).get("library")
        return [g for g in listed if isinstance(g, dict) and _number(g.get("key")) > 0] if isinstance(listed, list) else None
    except (OSError, ValueError, AttributeError):
        return None


def _pf_groups(every):
    """RomM's games, one per game whatever its versions, numbered by its lowest version: that
    number stays when the entry switches versions. The version listed is the main one (the
    frontend picks by language when it makes the entry)."""
    out = []
    for group in _version_groups(every):
        roms = [every[i] for i in group]
        pick = next((r for r in roms if _stands_for_its_versions(r)), roms[0])
        d = _detail(pick)
        out.append({"key": group[0], "versions": group, "rom_id": d["rom_id"], "name": d["name"][:120],
                    "platform": d["platform"][:60], "system": d["system"], "size": d["size"]})
    return out


def _pf_catalog(groups):
    """Every game Punktfunk lists: RomM's (those whose console's folder is known) and the
    entries of the add-on's, whether or not their file is here."""
    with _lock:
        games = _read_games()["games"]
    entry = {}  # RomM number -> (appid, game)
    for appid, game in games.items():
        if isinstance(game, dict) and _number(appid) > 0 and _number(game.get("rom_id")) > 0:
            entry[_number(game["rom_id"])] = (_number(appid), game)
    out, taken = [], set()

    def row(key, appid, game, listed):
        installed = bool(game and game.get("installed") and os.path.isfile(str(game.get("path") or "")))
        cover = ""
        if not appid:
            for kind in ("png", "jpg"):
                path = os.path.join(PF_COVERS, "%d.%s" % (key, kind))
                if os.path.isfile(path):
                    cover = path
        return {
            "key": key,
            "appid": appid,
            "rom_id": _number(game.get("rom_id")) if game else _number(listed.get("rom_id")),
            "versions": listed.get("versions") if listed else [_number(game.get("rom_id"))],
            "name": _text(listed.get("name")) if listed else "",
            "platform": _text(listed.get("platform")) if listed else "",
            "system": _text(game.get("system")) if game else _text(listed.get("system")),
            "size": _number(game.get("size")) if game and installed else _number((listed or game or {}).get("size")),
            "installed": installed,
            "cover": cover,
        }

    for listed in groups or []:
        versions = [_number(v) for v in listed.get("versions") or [] if _number(v) > 0]
        mine = next((entry[v] for v in versions if v in entry), None)
        if mine:
            taken.add(mine[0])
            out.append(row(_number(listed["key"]), mine[0], mine[1], listed))
        elif listed.get("system"):
            out.append(row(_number(listed["key"]), 0, None, listed))
    # Entries RomM no longer lists (or before RomM was ever read): still the player's, by their number.
    for rom_id, (appid, game) in sorted(entry.items()):
        if appid not in taken:
            out.append(row(rom_id, appid, game, None))
    return out


def _pf_covers(server, key, catalog):
    """Covers of the games that have no Steam entry yet (the others have Steam's pictures)."""
    os.makedirs(PF_COVERS, exist_ok=True)
    fetched = 0
    for game in catalog:
        if game["appid"] or game["cover"] or fetched >= PF_COVERS_AT_ONCE:
            continue
        fetched += 1
        rom, _why = _rom(server, key, game["rom_id"])
        cover = _cover(server, key, rom) if rom else None
        if not cover:
            continue
        path = os.path.join(PF_COVERS, "%d.%s" % (game["key"], cover[1]))
        try:
            with open(path + ".tmp", "wb") as f:
                f.write(cover[0])
            os.replace(path + ".tmp", path)
            game["cover"] = path
        except OSError:
            pass


def _pf_refresh():
    """One look: RomM's library when it is due, then the catalog, written only when it changed."""
    if _pf["groups"] is None:
        _pf["groups"] = _pf_groups_kept()
    server, key = _games_key()
    if server and time.monotonic() - _pf["read"] > PF_LIBRARY_EVERY:
        _pf["read"] = time.monotonic()
        every, why = _library(server, key)
        if every is not None and not why:
            _pf["groups"] = _pf_groups(every)
    catalog = _pf_catalog(_pf["groups"])
    if server:
        _pf_covers(server, key, catalog)
    data = {"version": 1, "launcher": LAUNCHER, "player": PLAYER, "games": catalog, "library": _pf["groups"] or []}
    text = json.dumps(data, sort_keys=True)
    if text != _pf["written"] and _write_private(PF_CATALOG, data):
        _pf["written"] = text
    return catalog


def _pf_row(key, job):
    """One download as Punktfunk reads it."""
    state = job["state"]
    row = {"key": key, "done": _number(job.get("done")), "total": _number(job.get("total"))}
    if state == "queued":
        row["state"] = "queued"
    elif state == "downloading":
        row["state"] = "installing" if job.get("unpacking") else "downloading"
        if job.get("unpacking"):
            row["phase"] = "Descomprimiendo"
    elif state == "installed":
        row["state"] = "done"
    elif state in ("paused", "cancelled"):
        row["state"] = state
    else:
        row["state"] = "failed"
        row["error"] = _pf_say(state)
    return row


def _pf_downloads(catalog):
    """Every download of the add-on's Punktfunk may show, by the game's number on Punktfunk."""
    rows = []
    with _lock:
        games = _read_games()["games"]
        for game in catalog:
            appid = game["appid"]
            if appid:
                _pf["adding"].pop(game["key"], None)
                job = _jobs.get(appid)
                part = None if job else _kept_part(games.get(str(appid)))
                if job and not job.get("to"):  # a version being brought is not a download of the game
                    rows.append(_pf_row(game["key"], job))
                elif part:  # paused before a restart
                    rows.append({"key": game["key"], "state": "paused", "done": os.path.getsize(part),
                                 "total": game["size"]})
    now = time.monotonic()
    for key, asked in list(_pf["adding"].items()):
        why = asked.get("why") or ("late" if now - asked["since"] > PF_ADD_WAIT else "")
        if why:
            rows.append({"key": key, "state": "failed", "done": 0, "total": asked["size"], "error": _pf_say(why)})
            if now - asked["since"] > PF_ADD_WAIT * 2:
                _pf["adding"].pop(key, None)
        else:
            rows.append({"key": key, "state": "queued", "done": 0, "total": asked["size"],
                         "phase": "Creando la entrada en Steam"})
    return rows


class Plugin:
    async def romm_status(self):
        """"linked" (a key for RomM's address that may look after games), "limited" (the same,
        for achievements only), "unlinked", "moved" (the address changed: pair again) or
        "no-server" (no address yet)."""
        server = _server()
        key = _read_key()
        if not server:
            return "no-server" if not key else "moved"
        if not key:
            return "unlinked"
        if key["url"] != server["url"]:
            return "moved"
        return "linked" if _key_scopes(key) >= SCOPES_GAMES else "limited"

    async def romm_server(self):
        """RomM's address as the add-on has it, and whether its certificate is checked."""
        server = _server()
        return {"url": server["url"] if server else "", "insecure": bool(server and server.get("insecure"))}

    async def romm_set_server(self, url, insecure=False):
        """Sets RomM's address. Answers {"result"}: "ok" (RomM answered there), "unreachable"
        (kept anyway: it may be asleep), "certificate" (not kept: its certificate could not be
        checked; asking again with `insecure` keeps it), "not-romm" (not kept: something else
        answers there) or "invalid" (not an address). A key made for another address is not
        sent here: the settings ask to connect again."""
        url = _clean_url(url)
        if not url:
            return {"result": "invalid"}
        server = {"url": url, "insecure": bool(insecure) and url.startswith("https:")}
        result = await asyncio.get_running_loop().run_in_executor(None, _probe, server)
        if result in ("certificate", "not-romm"):
            return {"result": result}
        if not _write_private(SERVER_FILE, server):
            return {"result": "error"}
        decky.logger.info("RomM address set")
        return {"result": result}

    async def romm_pair(self, code, games=False):
        """Swaps RomM's pairing code for a key. Answers {"result", "missing", "extra"}; the result
        is "ok", "limited" (kept, but with `games` it lacks `missing` to look after them), "code"
        (not a code), "invalid" (wrong or expired), "scopes" (thrown away: it lacks `missing` or
        can do `extra`, more than Vistazo should ever hold), "no-server", "unreachable", "busy"
        (RomM asks to wait) or "error"."""
        result, missing, extra = await self._pair(code, games)
        return {"result": result, "missing": sorted(missing), "extra": sorted(extra)}

    async def romm_check(self):
        """Asks RomM whether the stored key still works: "ok", "refused" (forgotten here),
        "unreachable", or romm_status's answer when there is no key for this server."""
        key = _read_key()
        server = _server()
        if not key or not server or key["url"] != server["url"]:
            return await self.romm_status()
        status, me = await asyncio.get_running_loop().run_in_executor(None, _request, server, "GET", "/api/users/me", key["token"])
        if status in (401, 403):
            _forget_key()
            return "refused"
        return "ok" if status == 200 and isinstance(me, dict) else "unreachable"

    async def _pair(self, code, games):
        none = set()
        code = "".join(c for c in str(code or "").upper() if c.isalnum())
        if len(code) != 8 or any(c not in CODE_CHARS for c in code):
            return "code", none, none
        server = _server()
        if not server:
            return "no-server", none, none
        loop = asyncio.get_running_loop()
        status, reply = await loop.run_in_executor(None, _request, server, "POST", "/api/client-tokens/exchange", None, {"code": code})
        if status == 0:
            return "unreachable", none, none
        if status in (400, 404, 422):
            return "invalid", none, none
        if status == 429:
            return "busy", none, none
        if status not in (200, 201) or not isinstance(reply, dict) or not reply.get("raw_token"):
            return "error", none, none
        token = str(reply["raw_token"])
        scopes = reply.get("scopes")
        scopes = {str(x) for x in scopes} if isinstance(scopes, list) else set()
        token_id = reply.get("id") if isinstance(reply.get("id"), int) else None

        async def revoke():
            # A key that is not kept is thrown away at RomM too (a key may always delete itself).
            if token_id is not None:
                await loop.run_in_executor(None, _request, server, "DELETE", "/api/client-tokens/%d" % token_id, token)

        # Less than the pair it always needs, or more than Vistazo should ever hold: not kept.
        extra = scopes - SCOPES_GAMES - SCOPES_PLAY - SCOPES_DEVICE
        if extra or not scopes >= SCOPES:
            await revoke()
            return "scopes", SCOPES - scopes, extra
        old = _read_key()
        if not _write_key({"url": server["url"], "token": token, "id": token_id, "scopes": sorted(scopes)}):
            await revoke()
            return "error", none, none
        # The key this one replaces is of no use any more: thrown away at RomM too.
        if old and old["url"] == server["url"] and isinstance(old.get("id"), int) and old["token"] != token:
            await loop.run_in_executor(None, _request, server, "DELETE", "/api/client-tokens/%d" % old["id"], old["token"])
        decky.logger.info("RomM key paired")
        missing = SCOPES_GAMES - scopes
        return ("limited" if games and missing else "ok"), (missing if games else none), none

    async def romm_unlink(self):
        """Forgets the key, and removes it at RomM when RomM answers."""
        key = _read_key()
        server = _server()
        if key and server and key["url"] == server["url"] and isinstance(key.get("id"), int):
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _request, server, "DELETE", "/api/client-tokens/%d" % int(key["id"]), key["token"])
        _forget_key()
        return "unlinked"

    async def romm_progress(self, ra_id, refresh=False, kept=False):
        """This game's achievements as RomM has them; with `refresh`, RomM reads them from
        RetroAchievements first and this waits (up to about a minute) for the new figures.
        {"ok": False, "why": ...} when there is no key, RomM is not answering or refuses it;
        with `kept`, RomM not answering gives what it said last time instead ("kept": True)."""
        if kept and not refresh and _read_key():
            good = _still_good("progress", _number(ra_id), PROGRESS_GOOD)
            if good:
                return {"ok": True, "changed": False, **good}
        got = await self._progress(ra_id, refresh)
        if got.get("ok"):
            _keep("progress", _number(ra_id), {k: v for k, v in got.items() if k not in ("ok", "changed")})
        elif kept and got.get("why") == "unreachable":
            last = _kept("progress", _number(ra_id))
            if last:
                return {"ok": True, "changed": False, "kept": True, **{k: v for k, v in last.items() if k != "_at"}}
        return got

    async def _progress(self, ra_id, refresh):
        try:
            ra_id = int(ra_id)
        except (TypeError, ValueError):
            return {"ok": False, "why": "game"}
        key = _read_key()
        server = _server()
        if not key:
            return {"ok": False, "why": "unlinked"}
        if not server or key["url"] != server["url"]:
            return {"ok": False, "why": "moved"}
        loop = asyncio.get_running_loop()
        get = lambda *a: loop.run_in_executor(None, _request, server, *a)
        status, me = await get("GET", "/api/users/me", key["token"])
        if status in (401, 403):
            # Revoked or expired in RomM: forgotten here, so the settings ask to connect again.
            _forget_key()
            return {"ok": False, "why": "refused"}
        if status != 200 or not isinstance(me, dict):
            return {"ok": False, "why": "unreachable"}
        before = _game(me, ra_id)
        if refresh and isinstance(me.get("id"), int):
            status, _ = await get("POST", "/api/users/%d/ra/refresh" % me["id"], key["token"], {"incremental": True})
            if status in (401, 403):
                _forget_key()
                return {"ok": False, "why": "refused"}
            if status in (200, 202, 204):
                # RomM does it in the background: its answer changes when it is done.
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    await asyncio.sleep(4)
                    status, now = await get("GET", "/api/users/me", key["token"])
                    if status == 200 and isinstance(now, dict):
                        game = _game(now, ra_id)
                        if game != before or now.get("ra_progression") != me.get("ra_progression"):
                            return {"ok": True, "changed": True, **game}
                # RomM did not finish in time: these figures are not newer than anyone else's.
                return {"ok": True, "changed": False, **before}
        return {"ok": True, "changed": False, **before}

    async def romm_game(self, rom_id, anew=False):
        """One game as RomM has it, for its page. {"ok": False, "why": ...} without a key that
        may see the games, or when RomM is not answering or refuses. What a game is hardly
        changes: a recent answer is given again without asking RomM, unless `anew`."""
        rom_id = _number(rom_id)
        if rom_id <= 0:
            return {"ok": False, "why": "game"}
        server, key = _games_key()
        if not server:
            return {"ok": False, "why": key}
        if not anew:
            good = _still_good("games", rom_id, GAME_GOOD)
            if good:
                return {"ok": True, **good}
        rom, why = await asyncio.get_running_loop().run_in_executor(None, _rom, server, key, rom_id)
        if not rom:
            # RomM asleep or out of reach: what it said last time, marked as such.
            kept = _kept("games", rom_id) if why == "unreachable" else None
            if kept:
                return {"ok": True, "kept": True, **{k: v for k, v in kept.items() if k != "_at"}}
            return {"ok": False, "why": why}
        detail = _detail(rom)
        _keep("games", rom_id, detail)
        return {"ok": True, **detail}

    async def romm_library(self, language=""):
        """Every game RomM has, to make each an entry in Steam: {"ok": True, "games": [{"rom_id",
        "name", "platform", "system" ("" when its folder in RetroDECK is not known yet), "size",
        "versions" (RomM's numbers of all its versions)}]}. Of a game kept in several versions one
        is listed: the first in `language` (as RomM names languages: "Spanish"), else the main one."""
        server, key = _games_key()
        if not server:
            return {"ok": False, "why": key}
        language = _text(language).strip().lower()[:40]
        every, why = await asyncio.get_running_loop().run_in_executor(None, _library, server, key)
        if every is None:
            return {"ok": False, "why": why}
        games = []
        for group in _version_groups(every):
            roms = [every[i] for i in group]
            pick = next((r for r in roms if language and language in _languages(r)), None)
            pick = pick or next((r for r in roms if _stands_for_its_versions(r)), roms[0])
            d = _detail(pick)
            games.append({"rom_id": d["rom_id"], "name": d["name"][:120], "platform": d["platform"][:60],
                          "system": d["system"], "size": d["size"], "versions": group})
        games.sort(key=lambda g: (g["name"].lower(), g["rom_id"]))
        return {"ok": True, "games": games}

    async def romm_collections(self):
        """RomM's collections, each with its games' RomM numbers and whether it is shown in
        Steam: {"ok": True, "collections": [{"key", "kind", "sort", "name", "roms", "on"}]}."""
        server, key = _games_key()
        if not server:
            return {"ok": False, "why": key}
        rows, why = await asyncio.get_running_loop().run_in_executor(None, _collections, server, key)
        if rows is None:
            return {"ok": False, "why": why}
        with _lock:
            shown = set(_shown_collections())
        return {"ok": True, "collections": [{**r, "on": r["key"] in shown} for r in rows]}

    async def romm_collection_set(self, key, on):
        """Shows one of RomM's collections in Steam, or stops showing it. Only what is kept here
        changes; the entries and Steam's collection are the front end's."""
        if not _collection_key_ok(key):
            return {"ok": False, "why": "collection"}
        with _lock:
            shown = [k for k in _shown_collections() if k != key]
            if on is True:
                if len(shown) >= COLLECTIONS_MAX:
                    return {"ok": False, "why": "collection"}
                shown.append(key)
            if not _write_private(COLLECTIONS_FILE, {"on": shown}):
                return {"ok": False, "why": "error"}
        return {"ok": True}

    async def bios_state(self, appid):
        """Whether the BIOS files RomM has for this game's console are on the Deck."""
        appid = _number(appid)
        server, key = _games_key()
        if not server:
            return {"state": key}
        with _lock:
            game = _read_games()["games"].get(str(appid))
        if not game:
            return {"state": "game"}
        out, _ = await asyncio.get_running_loop().run_in_executor(None, _bios_check, server, key, game)
        return out

    async def bios_fetch(self, appid):
        """Brings the BIOS files this game's console lacks. Answers as bios_state, after."""
        appid = _number(appid)
        server, key = _games_key()
        if not server:
            return {"state": key}
        with _lock:
            game = _read_games()["games"].get(str(appid))
        if not game:
            return {"state": "game"}
        if appid in _saving:
            return {"state": "busy"}
        _saving.add(appid)

        def run():
            out, missing = _bios_check(server, key, game)
            why = None
            for f in missing:
                try:
                    _bios_bring(server, key, _text(game.get("system")), f)
                except _Stop as stop:
                    why = str(stop)
            out, _ = _bios_check(server, key, game)
            return {**out, "why": why} if why else out

        try:
            return await asyncio.get_running_loop().run_in_executor(None, run)
        finally:
            _saving.discard(appid)

    async def bios_all(self):
        """Brings, for every console RomM has games of, the BIOS files RetroDECK lacks (see
        _bios_all): {"state": "ok" | "missing", "consoles", "files", "brought", "failed": [names],
        "unknown": [consoles whose BIOS folder is not known], "why"?} or {"state": why}.
        bios_all_state says how far it has got meanwhile."""
        server, key = _games_key()
        if not server:
            return {"state": key}
        with _lock:
            if _bios_all_job.get("state") == "running":
                return {"state": "busy"}
            _bios_all_job.clear()
            _bios_all_job.update(state="running", done=0, total=0, file="")
        out = {"state": "error"}
        try:
            out = await asyncio.get_running_loop().run_in_executor(None, _bios_all, server, key, _bios_all_job)
            return out
        finally:
            with _lock:
                _bios_all_job["state"] = out.get("state", "error")

    async def bios_all_state(self):
        """How far bios_all has got: {"state", "done", "total", "file"} (state "running" meanwhile)."""
        with _lock:
            return {k: _bios_all_job.get(k, d) for k, d in (("state", "idle"), ("done", 0), ("total", 0), ("file", ""))}

    async def save_sync(self, appid, choice=""):
        """Brings this game's save in step with RomM (see _sync_save). Never while a download of
        the game is under way; the frontend never asks while the game is running."""
        return await self._save_sync(_number(appid), choice if choice in ("deck", "romm") else "")

    async def _save_sync(self, appid, choice="", deadline=None):
        server, key = _games_key()
        if not server:
            return {"state": key}
        with _lock:
            game = _read_games()["games"].get(str(appid))
        if not game:
            return {"state": "game"}
        job = _jobs.get(appid)
        if _active(job):
            return {"state": "busy"}
        if appid in _saving:
            return {"state": "busy"}
        _saving.add(appid)
        # A game let start after this began may be reading its save: then nothing is replaced.
        began = time.monotonic()
        late = lambda: _released.get(appid, 0.0) > began
        try:
            out, record = await asyncio.get_running_loop().run_in_executor(None, _sync_save, server, key, game, choice, deadline, late)
        finally:
            _saving.discard(appid)
        with _lock:
            data = _read_games()
            kept = data["games"].get(str(appid))
            if kept is not None:
                saves = kept.get("saves") if isinstance(kept.get("saves"), dict) else {}
                if record:
                    saves["main"] = record
                saves["state"] = out["state"]
                kept["saves"] = saves
                _write_games(data)
        return out

    async def save_state(self, appid):
        """What is known of this game's save without asking RomM: the last state, and whether
        the save here changed since."""
        appid = _number(appid)
        with _lock:
            game = _read_games()["games"].get(str(appid))
        saves = game.get("saves") if game and isinstance(game.get("saves"), dict) else {}
        last = saves.get("main") if isinstance(saves.get("main"), dict) else None
        path, kind = _save_file(game) if game else (None, "game")
        if path is None:
            return {"state": kind}
        changed = False
        try:
            if _save_here(path, kind):
                changed = not last or _save_mtime(path, kind) != last.get("mtime")
            elif last:
                changed = True
        except (OSError, _Stop):
            pass
        state = _text(saves.get("state"))
        # A state that said why the save was left alone (another emulator then, say) no longer holds.
        if state in ("unsupported", "not-installed", "emulator", "layout", "game"):
            state = ""
        return {"state": state or "unknown", "changed": changed}

    async def game_list(self):
        """The Steam games Vistazo looks after: {Steam number: RomM number}."""
        with _lock:
            return {appid: _number(g.get("rom_id")) for appid, g in _read_games()["games"].items() if isinstance(g, dict)}

    async def game_launcher(self):
        """Where the launcher is, for a new Steam shortcut: {"ok": True, "exe", "start_dir"}."""
        if not _install_launcher():
            return {"ok": False, "why": "disk"}
        return {"ok": True, "exe": LAUNCHER, "start_dir": RUNTIME}

    async def game_cover(self, rom_id):
        """The game's cover from RomM, for its Steam shortcut: {"ok": True, "data" (base64),
        "kind"}. A game without one, or a RomM that does not hand it over, is {"ok": False}."""
        rom_id = _number(rom_id)
        server, key = _games_key()
        if not server or rom_id <= 0:
            return {"ok": False, "why": key if not server else "game"}
        loop = asyncio.get_running_loop()
        rom, why = await loop.run_in_executor(None, _rom, server, key, rom_id)
        if not rom:
            return {"ok": False, "why": why}
        cover = await loop.run_in_executor(None, _cover, server, key, rom)
        if not cover:
            return {"ok": False, "why": "cover"}
        return {"ok": True, "data": base64.b64encode(cover[0]).decode("ascii"), "kind": cover[1]}

    async def game_pictures(self, rom_id, kinds=("hero", "logo", "wide")):
        """The entry's other pictures that RomM keeps: {"ok": True, kind: {"data", "kind"}}
        for each of `kinds` RomM has; the ones it lacks are left out."""
        rom_id = _number(rom_id)
        server, key = _games_key()
        if not server or rom_id <= 0:
            return {"ok": False, "why": key if not server else "game"}
        kinds = [k for k in (kinds if isinstance(kinds, (list, tuple)) else []) if k in PICTURES]
        loop = asyncio.get_running_loop()
        rom, why = await loop.run_in_executor(None, _rom, server, key, rom_id)
        if not rom:
            return {"ok": False, "why": why}
        out = {"ok": True}
        for kind in kinds:
            got = await loop.run_in_executor(None, _picture, server, key, rom, kind)
            if got:
                out[kind] = {"data": base64.b64encode(got[0]).decode("ascii"), "kind": got[1]}
        return out

    async def fanart_hero(self, appid, rom_id):
        """Whether the entry's hero is ScreenScraper's fanart RomM keeps for the game (given by an
        earlier version, or copied from Tender's entry): {"ok": True, "same": bool}. With a
        SteamGridDB key the frontend then takes it off so SteamGridDB's comes first. Only the bytes
        are compared: a hero the player chose stays."""
        appid, rom_id = _number(appid), _number(rom_id)
        server, key = _games_key()
        if not server or rom_id <= 0 or appid <= 0:
            return {"ok": False, "why": key if not server else "game"}
        loop = asyncio.get_running_loop()
        heroes = await loop.run_in_executor(None, _steam_heroes, appid)
        if not heroes:
            return {"ok": True, "same": False}
        rom, why = await loop.run_in_executor(None, _rom, server, key, rom_id)
        if not rom:
            return {"ok": False, "why": why}
        fanart = await loop.run_in_executor(None, _picture, server, key, rom, "fanart")
        return {"ok": True, "same": bool(fanart) and fanart[0] in heroes}

    async def hero_choices(self, rom_id):
        """The backgrounds the player may pick for a game, small: {"ok": True, "choices": [{"id",
        "source", "data", "kind"}], "sgdb": "on" | "off" | "refused" | "unreachable"}. RomM's copy of
        ScreenScraper's fanart first (id "romm"), then SteamGridDB's heroes ("sgdb:<id>") with the
        player's key. hero_picture hands over the chosen one whole."""
        rom_id = _number(rom_id)
        server, key = _games_key()
        if not server or rom_id <= 0:
            return {"ok": False, "why": key if not server else "game"}
        loop = asyncio.get_running_loop()
        rom, why = await loop.run_in_executor(None, _rom, server, key, rom_id)
        if not rom:
            return {"ok": False, "why": why}
        choices = []
        fanart = await loop.run_in_executor(None, _picture, server, key, rom, "fanart")
        if fanart:
            choices.append({"id": "romm", "source": "romm", "data": base64.b64encode(fanart[0]).decode("ascii"), "kind": fanart[1]})
        sgdb_key, name = _sgdb_key(), _text(rom.get("name")).strip()
        sgdb = "on" if sgdb_key else "off"
        if sgdb_key and name:
            heroes = await loop.run_in_executor(None, _sgdb_heroes, sgdb_key, name)
            if isinstance(heroes, str):
                sgdb = heroes
                if heroes == "refused":
                    _sgdb_refused[0] = True
            else:
                _hero_urls[rom_id] = {number: url for number, url, _ in heroes}
                for number, _, thumb in heroes:
                    got = await loop.run_in_executor(None, _sgdb_image, thumb)
                    if got:
                        choices.append({"id": "sgdb:%d" % number, "source": "sgdb", "data": base64.b64encode(got[0]).decode("ascii"), "kind": got[1]})
        return {"ok": True, "choices": choices, "sgdb": sgdb}

    async def hero_picture(self, rom_id, choice):
        """The whole picture of one of hero_choices: {"ok": True, "data", "kind"}. Only what
        hero_choices offered for this game can be asked for."""
        rom_id, choice = _number(rom_id), _text(choice)
        server, key = _games_key()
        if not server or rom_id <= 0:
            return {"ok": False, "why": key if not server else "game"}
        loop = asyncio.get_running_loop()
        got = None
        if choice == "romm":
            rom, why = await loop.run_in_executor(None, _rom, server, key, rom_id)
            if not rom:
                return {"ok": False, "why": why}
            got = await loop.run_in_executor(None, _picture, server, key, rom, "fanart")
        elif choice.startswith("sgdb:"):
            url = _hero_urls.get(rom_id, {}).get(_number(choice[5:]))
            got = await loop.run_in_executor(None, _sgdb_image, url) if url else None
        if not got:
            return {"ok": False, "why": "picture"}
        return {"ok": True, "data": base64.b64encode(got[0]).decode("ascii"), "kind": got[1]}

    async def sgdb_status(self):
        """Whether pictures may come from SteamGridDB (a key is kept), "refused" when it turned the
        key down and "shared" when the RomM account keeps it too. Now and then it first looks at
        the account (see _sgdb_pull). The key itself is never handed back."""
        if _sgdb_sync["lock"] is None:
            _sgdb_sync["lock"] = asyncio.Lock()
        async with _sgdb_sync["lock"]:
            if not _sgdb_sync["at"] or time.monotonic() - _sgdb_sync["at"] > SGDB_SYNC_EVERY:
                await asyncio.get_running_loop().run_in_executor(None, _sgdb_pull)
        on = _sgdb_key() is not None
        out = {"on": True, "refused": True} if on and _sgdb_refused[0] else {"on": on}
        if on and _sgdb_sync["shared"]:
            out["shared"] = True
        return out

    async def sgdb_set_key(self, key):
        """Keeps the player's own SteamGridDB key, here and in the RomM account for the other
        devices: {"result", "shared"}; the result is "ok", "invalid" (not shaped like one) or
        "error" (not saved), and "shared" whether RomM took it too."""
        key = key.strip() if isinstance(key, str) else ""
        if not SGDB_KEY_SHAPE.fullmatch(key):
            return {"result": "invalid"}
        if not _write_private(SGDB_FILE, {"key": key}):
            return {"result": "error"}
        _sgdb_refused[0] = False
        _sgdb_ids.clear()
        shared = await asyncio.get_running_loop().run_in_executor(None, _sgdb_share, key)
        decky.logger.info("SteamGridDB key kept%s", " and handed to RomM" if shared else "")
        return {"result": "ok", "shared": shared}

    async def sgdb_forget(self):
        """Forgets the SteamGridDB key here and in the RomM account, so the other devices forget
        it too: nothing more is asked of it."""
        try:
            os.remove(SGDB_FILE)
        except OSError:
            pass
        _sgdb_refused[0] = False
        _sgdb_ids.clear()
        await asyncio.get_running_loop().run_in_executor(None, _sgdb_share, "")
        decky.logger.info("SteamGridDB key forgotten")
        return {"ok": True}

    async def sgdb_pictures(self, rom_id, kinds=("cover", "wide", "hero", "logo", "icon")):
        """The pictures SteamGridDB has of `kinds` for a game, only those RomM does not keep:
        {"ok": True, kind: {"data", "kind"}} and "icon": {"path"}. Without a key: {"ok": False,
        "why": "off"}, and nothing is asked of anyone."""
        key = _sgdb_key()
        if not key:
            return {"ok": False, "why": "off"}
        rom_id = _number(rom_id)
        server, romm_key = _games_key()
        if not server or rom_id <= 0:
            return {"ok": False, "why": romm_key if not server else "game"}
        kinds = [k for k in (kinds if isinstance(kinds, (list, tuple)) else []) if k in SGDB_KINDS]
        loop = asyncio.get_running_loop()
        rom, why = await loop.run_in_executor(None, _rom, server, romm_key, rom_id)
        if not rom:
            return {"ok": False, "why": why}
        kinds = [k for k in kinds if not _romm_has(rom, k)]
        name = _text(rom.get("name")).strip()
        if not kinds or not name:
            return {"ok": True}
        got = await loop.run_in_executor(None, _sgdb_pictures, key, rom_id, name, kinds)
        if got == "refused":
            if not _sgdb_refused[0]:
                decky.logger.warning("SteamGridDB turned the key down")
            _sgdb_refused[0] = True
            return {"ok": False, "why": "refused"}
        if got == "unreachable":
            return {"ok": False, "why": "unreachable"}
        _sgdb_refused[0] = False
        out = {"ok": True}
        for kind, picture in got.items():
            out[kind] = {"path": picture} if kind == "icon" else {"data": base64.b64encode(picture[0]).decode("ascii"), "kind": picture[1]}
        return out

    async def game_tidy(self):
        """Clears what this code left in Vistazo's own data folder when it lived there (its
        launcher, the launcher's list, RomM's last answers). Asked for once the entries in Steam
        point at the launcher here."""
        old = os.path.join(decky.DECKY_HOME, "data", "Vistazo")
        for name in ("vistazo-launch", "launch.json", "romm-kept.json"):
            path = os.path.join(old, name)
            try:
                if os.path.isfile(path) and not os.path.islink(path):
                    os.remove(path)
            except OSError as e:
                decky.logger.warning("Not cleared at Vistazo's: %s", e)
        return {"ok": True}

    async def game_state(self, appid):
        """Whether Vistazo looks after this Steam game, has its file, and how a download goes."""
        appid = _number(appid)
        with _lock:
            return _state(appid, _relocated(appid))

    async def game_take(self, appid, rom_id, exe="", start_dir="", options=""):
        """Vistazo starts this game from now on. `exe`, `start_dir` and `options` are what its
        Steam shortcut had, kept to give it back (game_release). Answers with what to put in
        the shortcut: {"ok": True, "exe", "start_dir", "options", ...game_state}."""
        appid, rom_id = _number(appid), _number(rom_id)
        if appid <= 0 or rom_id <= 0:
            return {"ok": False, "why": "game"}
        server, key = _games_key()
        if not server:
            return {"ok": False, "why": key}
        if not _install_launcher():
            return {"ok": False, "why": "disk"}
        rom, why = await asyncio.get_running_loop().run_in_executor(None, _rom, server, key, rom_id)
        if not rom:
            return {"ok": False, "why": why}
        detail = _detail(rom)
        with _lock:
            data = _read_games()
            game = data["games"].get(str(appid))
            if not game:
                # A file the shortcut already started stays where it is, and is not Vistazo's;
                # so does the game's file when it is already whole in its console's folder.
                path = _installed_path(options, rom) or _already_there(rom)
                game = {
                    "rom_id": rom_id,
                    "platform": _text(rom.get("platform_slug")),
                    "system": detail["system"],
                    "path": path,
                    "size": detail["size"],
                    "installed": bool(path),
                    "files": [],
                    "folders": [],
                    "before": {"exe": str(exe or ""), "start_dir": str(start_dir or ""), "options": str(options or "")},
                    "saves": {},
                }
                data["games"][str(appid)] = game
                if not _write_games(data):
                    return {"ok": False, "why": "disk"}
            elif _number(game.get("rom_id")) != rom_id:
                return {"ok": False, "why": "game"}
            return {"ok": True, "options": str(rom_id), **_state(appid, game)}

    async def tender_games(self):
        """Tender's entries as its database has them: [{"appid", "rom_id", "name", "mine"}],
        "mine" when the add-on already starts that entry."""
        games = _tender_games()
        with _lock:
            mine = _read_games()["games"]
        return [{"appid": a, "rom_id": g["rom_id"], "name": g["name"], "mine": str(a) in mine} for a, g in sorted(games.items())]

    async def game_adopt(self, appid, rom_id, exe="", start_dir="", options="", own=0):
        """Takes over one of Tender's entries (Tender must be off: the frontend checks). `exe`,
        `start_dir` and `options` are what it has now, kept to give it back (game_release). `own`
        is the add-on's own entry of the same game, if any: its record moves here with its files
        and the frontend removes it from Steam. The files Tender brought become the add-on's to
        remove. Answers as game_take, plus "dropped" (the own entry merged in, or 0)."""
        appid, rom_id, own = _number(appid), _number(rom_id), _number(own)
        tender = _tender_games().get(appid)
        if not tender or tender["rom_id"] != rom_id:
            return {"ok": False, "why": "not-tender"}
        server, key = _games_key()
        if not server:
            return {"ok": False, "why": key}
        if not _install_launcher():
            return {"ok": False, "why": "disk"}
        rom, why = await asyncio.get_running_loop().run_in_executor(None, _rom, server, key, rom_id)
        if not rom:
            return {"ok": False, "why": why}
        detail = _detail(rom)
        with _lock:
            data = _read_games()
            games = data["games"]
            if str(appid) in games:
                if _number(games[str(appid)].get("rom_id")) != rom_id:
                    return {"ok": False, "why": "game"}
                return {"ok": True, "options": str(rom_id), "dropped": 0, **_state(appid, games[str(appid)])}
            dup = games.get(str(own)) if own and own != appid else None
            if dup is not None and _number(dup.get("rom_id")) != rom_id:
                dup = None
            for n in ([own] if dup is not None else []):
                job = _jobs.get(n)
                if _active(job):
                    return {"ok": False, "why": "busy"}
            t_path, t_files, t_folders = None, [], []
            if detail["system"]:
                # What the add-on's other games hold stays theirs.
                taken = [f for n, g in games.items() if n != str(own) and isinstance(g, dict)
                         for f in (g.get("files") or []) + [g.get("path")] if isinstance(f, str) and f]
                t_path, t_files, t_folders = _tender_files(tender, os.path.join(_roms_root(), detail["system"]), rom, taken)
            d = dup or {}
            d_path = _text(d.get("path")) if d.get("installed") and os.path.isfile(_text(d.get("path"))) else None
            path = d_path or t_path or _already_there(rom)
            files = list(dict.fromkeys([f for f in (d.get("files") or []) if isinstance(f, str)] + t_files))
            folders = list(dict.fromkeys([f for f in (d.get("folders") or []) if isinstance(f, str)] + t_folders))
            marks = dict(d.get("marks") if isinstance(d.get("marks"), dict) else {})
            for f in t_files:
                if f not in marks:
                    marks[f] = _mark(f)
            games[str(appid)] = {
                "rom_id": rom_id,
                "platform": _text(rom.get("platform_slug")),
                "system": detail["system"],
                "path": path,
                "size": detail["size"],
                "installed": bool(path and os.path.isfile(path)),
                "files": files,
                "folders": folders,
                "marks": marks,
                "before": {"exe": str(exe or ""), "start_dir": str(start_dir or ""), "options": str(options or "")},
                # The save is the same file, so what is known of it carries over.
                "saves": d.get("saves") if isinstance(d.get("saves"), dict) else {},
                "from": "tender",
            }
            if dup is not None:
                _drop_part(games.pop(str(own), None))  # a paused download of it goes too
                _jobs.pop(own, None)
            if not _write_games(data):
                return {"ok": False, "why": "disk"}
            return {"ok": True, "options": str(rom_id), "dropped": own if dup is not None else 0, **_state(appid, games[str(appid)])}

    async def game_release(self, appid):
        """Vistazo lets go of this game. Answers with what its shortcut had before, to put back:
        {"ok": True, "exe", "start_dir", "options"}. The game's file stays on the disk."""
        appid = _number(appid)
        with _lock:
            job = _jobs.get(appid)
            if _active(job):
                return {"ok": False, "why": "busy"}
            data = _read_games()
            game = data["games"].pop(str(appid), None)
            if not game:
                return {"ok": False, "why": "game"}
            if not _write_games(data):
                return {"ok": False, "why": "disk"}
            # A paused download's half-done file would be nobody's once the entry is gone.
            _drop_part(game)
            _jobs.pop(appid, None)
            before = game.get("before") if isinstance(game.get("before"), dict) else {}
            return {"ok": True, **{k: _text(before.get(k)) for k in ("exe", "start_dir", "options")}}

    async def game_download(self, appid):
        """Starts bringing this game's file from RomM, or puts it in the queue while another one
        comes; a paused one goes on from where it stopped. game_state tells how it goes."""
        appid = _number(appid)
        server, key = _games_key()
        if not server:
            return {"ok": False, "why": key}
        with _lock:
            game = _relocated(appid)
            if not game:
                return {"ok": False, "why": "game"}
            job = _jobs.get(appid)
            if _active(job):
                return {"ok": True, **_state(appid, game)}
            if game.get("installed") and os.path.isfile(str(game.get("path") or "")):
                return {"ok": True, **_state(appid, game)}
            resume = _kept_part(game) or ""
            done = os.path.getsize(resume) if resume else 0
            job = {"state": "queued", "done": done, "total": _number(game.get("size")), "cancel": threading.Event()}
            _jobs[appid] = job
        rom_id = _number(game.get("rom_id"))

        def started(part):
            job["part"] = part
            _note_part(appid, part)

        def run():
            with _lock:
                if job["cancel"].is_set():  # paused or cancelled while it waited its turn
                    job["state"] = "paused" if job.get("pause") else "cancelled"
                    return
                job["state"] = "downloading"
            try:
                rom, why = _rom(server, key, rom_id)
                if not rom:
                    raise _Stop(why)
                got = _download(server, key["token"], rom, job, started, resume)
            except _Stop as stop:
                part = job.get("part")
                if not (part and str(stop) in ("paused", "unreachable") and os.path.isfile(part)):
                    part = None
                if part or job.get("part"):
                    _note_part(appid, part, kept=True)
                job["state"] = str(stop)
                return
            except Exception as e:  # never leave a download hanging in "downloading"
                decky.logger.error("Download failed: %s", e)
                _note_part(appid, None)
                job["state"] = "error"
                return
            with _lock:
                data = _read_games()
                kept = data["games"].get(str(appid))
                if kept is None or _number(kept.get("rom_id")) != rom_id:
                    _discard(got["files"], got["folders"], os.path.join(_roms_root(), _detail(rom)["system"]))
                    job["state"] = "cancelled"
                    return
                kept.pop("part", None)
                kept.pop("paused", None)
                kept.update(path=got["path"], files=got["files"], folders=got["folders"], size=got["size"], installed=True,
                            marks={f: _mark(f) for f in got["files"] if os.path.isfile(f)})
                job["state"] = "installed" if _write_games(data) else "disk"

        _queue.submit(run)
        return {"ok": True, **_state(appid, game)}

    async def game_pause(self, appid):
        """Pauses this game's download (or takes it out of the queue): what was brought stays on
        the disk, and game_download goes on from there. A version being brought and a download
        being unpacked are not paused."""
        appid = _number(appid)
        with _lock:
            job = _jobs.get(appid)
            game = _read_games()["games"].get(str(appid))
            if not _active(job) or job.get("to") or job.get("unpacking"):
                return {"ok": False, "why": "busy" if _active(job) else "game", **_state(appid, game)}
            job["pause"] = True
            job["cancel"].set()
            if job["state"] == "queued":
                job["state"] = "paused"
            return {"ok": True, **_state(appid, game)}

    async def game_cancel(self, appid):
        """Stops this game's download and throws away what it brought, paused or not."""
        appid = _number(appid)
        with _lock:
            job = _jobs.get(appid)
            if _active(job):
                job["pause"] = False
                job["cancel"].set()
                if job["state"] == "queued":
                    job["state"] = "cancelled"  # it never starts
            data = _read_games()
            game = data["games"].get(str(appid))
            changed = False
            # A new version already here and waiting: taken away, the entry stays as it was.
            if game and isinstance(game.get("switching"), dict):
                _drop_switch(game)
                changed = True
                if job and job["state"] == "ready":
                    job["state"] = "cancelled"
            # A paused one: what it brought goes.
            if game and not _active(job) and game.get("part"):
                _drop_part(game)
                changed = True
                if job and job["state"] in ("paused", "unreachable"):
                    job["state"] = "cancelled"
            elif job and job["state"] == "paused":
                job["state"] = "cancelled"  # paused while it waited its turn: nothing came yet
            if changed:
                _write_games(data)
        return await self.game_state(appid)

    async def game_remove(self, appid):
        """Takes this game's file off the disk: only files Vistazo itself brought."""
        appid = _number(appid)
        with _lock:
            job = _jobs.get(appid)
            if _active(job):
                return {"ok": False, "why": "busy"}
            data = _read_games()
            game = data["games"].get(str(appid))
            if not game:
                return {"ok": False, "why": "game"}
            if isinstance(game.get("switching"), dict):
                _drop_switch(game)
                _write_games(data)
            files = [f for f in game.get("files") or [] if isinstance(f, str)]
            if not files:
                return {"ok": False, "why": "not-ours"}
            system_dir = os.path.join(_roms_root(), _text(game.get("system")))
            # Forgotten first: a game without its file must never be left marked as installed.
            folders = [f for f in game.get("folders") or [] if isinstance(f, str)]
            marks = game.get("marks") if isinstance(game.get("marks"), dict) else None
            game.update(installed=False, path=None, files=[], folders=[], marks={})
            if not _write_games(data):
                return {"ok": False, "why": "disk"}
            _discard(files, folders, system_dir, marks)
            _jobs.pop(appid, None)
            return {"ok": True, **_state(appid, game)}

    async def game_versions(self, appid):
        """The versions RomM keeps of this game (its languages and regions), the one this entry
        starts marked "current": {"ok": True, "versions": [{"rom_id", "file", "languages",
        "regions", "achievements", "current"}]}."""
        appid = _number(appid)
        server, key = _games_key()
        if not server:
            return {"ok": False, "why": key}
        with _lock:
            game = _read_games()["games"].get(str(appid))
        if not game:
            return {"ok": False, "why": "game"}
        current = _number(game.get("rom_id"))

        def ask():
            rom, why = _rom(server, key, current)
            if not rom:
                return None, why
            found = [rom]
            for n in sorted(_sibling_ids(rom))[:12]:
                other, why = _rom(server, key, n)
                if other is None and why != "game":
                    return None, why
                if other is not None:
                    found.append(other)
            return found, None

        roms, why = await asyncio.get_running_loop().run_in_executor(None, ask)
        if roms is None:
            return {"ok": False, "why": why}
        system = _text(game.get("system"))
        same = [r for r in roms if _number(r.get("id")) == current or _detail(r)["system"] == system]
        return {"ok": True, "versions": sorted((_version_row(r, current) for r in same), key=lambda v: v["rom_id"])}

    async def game_switch(self, appid, rom_id):
        """This entry starts another version of its game from now on. Not installed: at once.
        Installed: the other version is brought first (the one there stays playable meanwhile),
        then the save goes with it when it has none of its own (the old one stays as it is), and
        the old file goes only if the add-on brought it. Answers as game_state, plus "options"
        (what Steam's launch options must say) once the switch is done, and "pending" while the
        new version is on its way."""
        appid, rom_id = _number(appid), _number(rom_id)
        server, key = _games_key()
        if not server:
            return {"ok": False, "why": key}
        with _lock:
            game = _read_games()["games"].get(str(appid))
        if not game:
            return {"ok": False, "why": "game"}
        current = _number(game.get("rom_id"))
        if rom_id == current:
            return {"ok": True, "options": str(rom_id), **_state(appid, game)}
        loop = asyncio.get_running_loop()
        rom, why = await loop.run_in_executor(None, _rom, server, key, current)
        if not rom:
            return {"ok": False, "why": why}
        if rom_id not in _sibling_ids(rom):
            return {"ok": False, "why": "game"}
        other, why = await loop.run_in_executor(None, _rom, server, key, rom_id)
        if not other:
            return {"ok": False, "why": why}
        detail = _detail(other)
        if detail["system"] != _text(game.get("system")):
            return {"ok": False, "why": "game"}

        with _lock:
            data = _read_games()
            kept = data["games"].get(str(appid))
            job = _jobs.get(appid)
            if not kept or _number(kept.get("rom_id")) != current:
                return {"ok": False, "why": "game"}
            if _active(job) or appid in _saving or isinstance(kept.get("switching"), dict):
                return {"ok": False, "why": "busy"}
            # A paused download of the version it starts now is of no use after the switch.
            _drop_part(kept)
            if job and job["state"] in ("paused", "unreachable"):
                _jobs.pop(appid, None)
            installed = bool(kept.get("installed") and os.path.isfile(_text(kept.get("path"))))
            there = _already_there(other)
            target = {"rom_id": rom_id, "platform": _text(other.get("platform_slug")), "size": detail["size"]}
            if not installed or there:
                # Nothing to bring: the other version is not wanted on the disk yet, or it is already
                # there. The frontend asks only while the game is not running.
                kept["switching"] = {**target, "path": there, "files": [], "folders": [], "marks": {}}
                out = _finish_switch(data, appid)
                if not out.get("ok"):
                    return out
                _jobs.pop(appid, None)
                return {**out, **_state(appid, data["games"][str(appid)])}
            kept.pop("switching", None)
            if not _write_games(data):
                return {"ok": False, "why": "disk"}
            job = {"state": "queued", "done": 0, "total": detail["size"], "cancel": threading.Event(), "to": rom_id}
            _jobs[appid] = job

        def run():
            with _lock:
                if job["cancel"].is_set():  # cancelled while it waited its turn
                    job["state"] = "cancelled"
                    return
                job["state"] = "downloading"
            try:
                got = _download(server, key["token"], other, job, lambda part: _note_part(appid, part))
            except _Stop as stop:
                _note_part(appid, None)
                job["state"] = str(stop)
                return
            except Exception as e:  # never leave a download hanging in "downloading"
                decky.logger.error("Version not brought: %s", e)
                _note_part(appid, None)
                job["state"] = "error"
                return
            system_dir = os.path.join(_roms_root(), detail["system"])
            with _lock:
                data = _read_games()
                kept = data["games"].get(str(appid))
                if kept is None or _number(kept.get("rom_id")) != current:
                    _discard(got["files"], got["folders"], system_dir)
                    job["state"] = "cancelled"
                    return
                kept.pop("part", None)
                # Here, but not switched to yet: the game may still be running the old version,
                # and its save is only final once it closes (game_switch_finish).
                marks = {f: _mark(f) for f in got["files"] if os.path.isfile(f)}
                kept["switching"] = {**target, "path": got["path"], "files": got["files"], "folders": got["folders"], "marks": marks}
                if not _write_games(data):
                    _discard(got["files"], got["folders"], system_dir, marks)
                    job["state"] = "disk"
                    return
                job["state"] = "ready"

        _queue.submit(run)
        with _lock:
            return {"ok": True, "pending": rom_id, **_state(appid, _read_games()["games"].get(str(appid)))}

    async def game_switch_finish(self, appid):
        """Ends a switch whose new version is here: the save goes along (when the new version has
        none), the entry starts the new version, the old files the add-on brought go. The
        frontend calls it only while the game is not running."""
        appid = _number(appid)
        with _lock:
            data = _read_games()
            game = data["games"].get(str(appid))
            if not game or not isinstance(game.get("switching"), dict):
                return {"ok": False, "why": "game"}
            if appid in _saving:
                return {"ok": False, "why": "busy"}
            out = _finish_switch(data, appid)
            job = _jobs.get(appid)
            if job and job["state"] == "ready":
                job["state"] = "installed" if out.get("ok") else out.get("why", "error")
            return {**out, **_state(appid, data["games"].get(str(appid)))}

    async def game_pending(self):
        """The entries whose new version is here and waits to be switched to: {appid: rom_id}."""
        with _lock:
            games = _read_games()["games"]
        return {a: _number(g["switching"].get("rom_id")) for a, g in games.items()
                if isinstance(g, dict) and isinstance(g.get("switching"), dict)}

    async def game_emulators(self, appid):
        """The emulators RetroDECK has for this game's console: {"ok": True, "emulators": [name],
        "chosen": name ("" for the default: the one whose saves are kept in step when ES-DE lists
        it, else RetroDECK's pick), "saves": the one whose saves are kept in step, when listed}."""
        appid = _number(appid)
        with _lock:
            game = _read_games()["games"].get(str(appid))
        if not isinstance(game, dict):
            return {"ok": False, "why": "game"}
        system = _text(game.get("system"))
        names = list(_emulators(system))
        if not names:
            return {"ok": False, "why": "system"}
        chosen = _text(game.get("emulator"))
        saves = SAVE_EMULATORS.get(system, "")
        return {"ok": True, "emulators": names, "chosen": chosen if chosen in names else "",
                "saves": saves if saves in names else ""}

    async def game_set_emulator(self, appid, name):
        """Starts this game with that emulator from now on ("": as RetroDECK decides)."""
        appid, name = _number(appid), _text(name)
        with _lock:
            data = _read_games()
            game = data["games"].get(str(appid))
            if not isinstance(game, dict):
                return {"ok": False, "why": "game"}
            if name and name not in _emulators(_text(game.get("system"))):
                return {"ok": False, "why": "emulator"}
            if name:
                game["emulator"] = name
            else:
                game.pop("emulator", None)
            if not _write_games(data):
                return {"ok": False, "why": "disk"}
        return {"ok": True, "chosen": name}

    async def game_discs(self, appid):
        """The discs of a game of several: {"ok": True, "discs": [{"file", "name"}], "chosen":
        "" (nothing chosen), "playlist" or a disc's file, "starts": what starts now, the same way
        ("" when it is none of them)}. A game of one disc is {"ok": False, "why": "discs"}."""
        appid = _number(appid)
        with _lock:
            game = _read_games()["games"].get(str(appid))
            if not isinstance(game, dict):
                return {"ok": False, "why": "game"}
            found = _discs(game)
            if not found or len(found[1]) < 2:
                return {"ok": False, "why": "discs"}
            chosen = _disc_path(game)

        def said(path):
            return "playlist" if path and path == found[2] else os.path.basename(path) if path in found[1] else ""
        return {"ok": True, "discs": [{"file": os.path.basename(d), "name": os.path.splitext(os.path.basename(d))[0]} for d in found[1]],
                "chosen": said(chosen[0]) if chosen else "", "starts": said(chosen[0] if chosen else _text(game.get("path")))}

    async def game_set_disc(self, appid, choice):
        """Starts this game from that disc from now on: a disc's file, "playlist" for every disc
        (RomM's playlist, or one made in the game's folder), or "" for the add-on's own choice."""
        appid = _number(appid)
        with _lock:
            data = _read_games()
            game = data["games"].get(str(appid))
            if not isinstance(game, dict):
                return {"ok": False, "why": "game"}
            if _active(_jobs.get(appid)) or isinstance(game.get("switching"), dict):
                return {"ok": False, "why": "busy"}
            found = _discs(game)
            if not found or len(found[1]) < 2:
                return {"ok": False, "why": "discs"}
            folder, discs, playlist = found
            if not isinstance(choice, str):
                return {"ok": False, "why": "disc"}
            made = None
            if choice == "playlist":
                try:
                    made = None if playlist else _make_playlist(game, folder, discs)
                except _Stop as stop:
                    return {"ok": False, "why": str(stop)}
                game["disc"] = playlist or made
            elif choice:
                picked = [d for d in discs if os.path.basename(d) == choice]
                if not picked:
                    return {"ok": False, "why": "disc"}
                game["disc"] = picked[0]
            else:
                game.pop("disc", None)
            if not _write_games(data):
                if made:  # not recorded, so not left behind either
                    _discard([made], [], os.path.join(_roms_root(), _text(game.get("system"))))
                return {"ok": False, "why": "disk"}
        return {"ok": True, "chosen": choice}

    async def game_gone(self):
        """The add-on's entries whose game was deleted in RomM: {"ok": True, "apps": [appid]}."""
        server, key = _games_key()
        if not server:
            return {"ok": False, "why": key}
        with _lock:
            games = dict(_read_games()["games"])
        gone, why = await asyncio.get_running_loop().run_in_executor(None, _gone, server, key, games)
        return {"ok": True, "apps": gone} if gone is not None else {"ok": False, "why": why}

    async def play_started(self, appid):
        """One of the add-on's games started: its session begins now."""
        appid = _number(appid)
        with _lock:
            game = _read_games()["games"].get(str(appid))
        rom_id = _number((game or {}).get("rom_id")) if isinstance(game, dict) else 0
        if not rom_id:
            return {"ok": False, "why": "game"}
        _playing[appid] = (rom_id, time.time(), time.monotonic())
        return {"ok": True}

    async def play_ended(self, appid):
        """The game closed: its session is kept and sent to RomM when it may be. Answers {"ok",
        "kept" (False for one too short to count), "waiting" (sessions not sent yet)}."""
        started = _playing.pop(_number(appid), None)
        if not started:
            return {"ok": False, "why": "game"}
        rom_id, began, clock = started
        played = time.monotonic() - clock  # the Deck's sleep is not counted
        if played < PLAY_SHORTEST:
            return {"ok": True, "kept": False}
        # The clock may have been set meanwhile: the end never comes before the start.
        ended = max(time.time(), began + played)
        session = {"rom_id": rom_id, "start_time": _iso(began), "end_time": _iso(ended), "duration_ms": int(played * 1000)}
        loop = asyncio.get_running_loop()
        if not await loop.run_in_executor(None, _play_keep, session):
            return {"ok": False, "why": "disk"}
        return {"ok": True, "kept": True, "waiting": await loop.run_in_executor(None, _play_send)}

    async def play_state(self):
        """Whether play time reaches RomM with this key, and how many sessions wait: {"allowed", "waiting"}."""
        server, _ = _play_key()
        waiting = await asyncio.get_running_loop().run_in_executor(None, _play_send)
        return {"allowed": bool(server), "waiting": waiting}

    async def device_state(self):
        """Whether RomM is told which saves and play time come from this Deck: {"allowed"}."""
        key = _read_key()
        server = _server()
        return {"allowed": bool(key and server and key["url"] == server["url"] and _device_allowed(key))}
    async def input_layouts(self):
        """The consoles with entries and the Steam Input layout chosen for each: {"ok": True,
        "layouts": [choice Steam has a template for], "consoles": [{"system", "count", "layout"}]}
        ("layout" is "" when Steam's own is left as it is)."""
        with _lock:
            systems = _entries_by_system()
            chosen = _read_layouts()
        offered = [k for k, _ in _input_layouts()]
        consoles = [{"system": s, "count": len(apps), "layout": chosen.get(s) if chosen.get(s) in offered else ""}
                    for s, apps in sorted(systems.items())]
        return {"ok": True, "layouts": offered, "consoles": consoles}

    async def input_set_layout(self, system, layout):
        """Keeps the layout for this console's entries ("": leave Steam's as it is). Answers with
        what the frontend selects in Steam: {"ok": True, "url" ("" for nothing), "apps": [appid]}."""
        system, layout = _text(system), _text(layout)
        with _lock:
            apps = _entries_by_system().get(system)
            if not apps:
                return {"ok": False, "why": "system"}
            url = _layout_url(layout) if layout else ""
            if layout and not url:
                return {"ok": False, "why": "layout"}
            chosen = _read_layouts()
            if layout:
                chosen[system] = layout
            else:
                chosen.pop(system, None)
            if not _write_private(LAYOUTS_FILE, {"version": 1, "systems": chosen}):
                return {"ok": False, "why": "disk"}
        return {"ok": True, "url": url, "apps": sorted(apps)}

    async def input_layout(self, appid):
        """The layout a new entry of the add-on's gets: {"ok": True, "url" ("" for nothing)}."""
        with _lock:
            game = _read_games()["games"].get(str(_number(appid)))
            if not isinstance(game, dict):
                return {"ok": False, "why": "game"}
            choice = _read_layouts().get(_text(game.get("system")), "")
        return {"ok": True, "url": _layout_url(choice) if choice else ""}

    async def save_before_start(self, rom_id):
        """The launcher asks this right before it starts a game (see _launch_asked): the save is
        brought in step with RomM while the emulator is not yet running. Answers the state, and
        tells the frontend, which says on screen what the player should know (a newer save was
        brought; RomM could not be asked; both changed)."""
        rom_id = _number(rom_id)
        with _lock:
            games = _read_games()["games"]
        appid = next((_number(a) for a, g in games.items() if isinstance(g, dict) and _number(g.get("rom_id")) == rom_id), 0)
        if rom_id <= 0 or not appid:
            return {"state": "game"}
        deadline = time.monotonic() + LAUNCH_WAIT
        try:
            # Another look at this save may be under way (its page was just opened): waited for,
            # so the game starts on what it leaves. Past the wait it must not replace the save.
            while appid in _saving and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            out = await self._save_sync(appid, "", deadline)
        finally:
            # From here the launcher starts the emulator: whatever began before may replace nothing.
            _released[appid] = time.monotonic()
        try:
            await decky.emit("vistazo_romm_start_save", appid, out["state"])
        except Exception as e:  # an older Decky without events: the game starts all the same
            decky.logger.warning("Start notice not sent: %s", e)
        return out

    async def _launch_asked(self, reader, writer):
        """One question from the launcher over its socket: a game's number in RomM, answered with
        the state of its save once it is in step (or could not be)."""
        try:
            line = await asyncio.wait_for(reader.readline(), 2)
            out = await self.save_before_start(line.decode("ascii", "ignore").strip())
            writer.write((_text(out.get("state")) + "\n").encode("ascii", "ignore"))
            await writer.drain()
        except Exception as e:
            decky.logger.warning("Launcher not answered: %s", e)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    # ── Punktfunk ──────────────────────────────────────────────────────────────────────────────
    async def punktfunk_added(self, key, why=""):
        """The frontend could not make the Steam entry of a game asked for from the TV: why, which
        Punktfunk then shows as the download's end."""
        asked = _pf["adding"].get(_number(key))
        if asked is not None:
            asked["why"] = _text(why)[:40] or "steam"
        return {"ok": True}

    async def _pf_game(self, key):
        catalog = await asyncio.get_running_loop().run_in_executor(None, _pf_catalog, _pf["groups"])
        return next((g for g in catalog if g["key"] == key), None)

    async def _pf_install(self, key):
        """Brings a game Punktfunk asked for: its entry's file, or first its entry, which only the
        frontend (in Steam) can make. Answers at once; the download is told by `downloads`."""
        game = await self._pf_game(key)
        if not game:
            return {"ok": False, "say": _pf_say("game")}
        if game["appid"]:
            out = await self.game_download(game["appid"])
            if not out.get("ok"):
                return {"ok": False, "say": _pf_say(out.get("why"))}
            return {"ok": True, "installed": bool(out.get("installed")) and not _active(_jobs.get(game["appid"]))}
        server, why = _games_key()
        if not server:
            return {"ok": False, "say": _pf_say(why)}
        if not game["system"]:
            return {"ok": False, "say": _pf_say("system")}
        asked = _pf["adding"].get(key)
        if asked and not asked.get("why") and time.monotonic() - asked["since"] <= PF_ADD_WAIT:
            return {"ok": True}
        _pf["adding"][key] = {"since": time.monotonic(), "why": "", "size": game["size"]}
        try:
            await decky.emit("vistazo_romm_add", key, game["versions"], game["name"])
        except Exception as e:
            decky.logger.warning("Entry for Punktfunk not asked for: %s", e)
            _pf["adding"].pop(key, None)
            return {"ok": False, "say": _pf_say("late")}
        return {"ok": True}

    async def _pf_act(self, verb, key):
        """Pause, cancel or remove, asked from Punktfunk."""
        game = await self._pf_game(key)
        appid = game["appid"] if game else 0
        if not appid:
            if verb == "cancel" or key in _pf["adding"]:
                _pf["adding"].pop(key, None)  # the entry, if Steam makes it after all, waits on its page
                return {"ok": True}
            return {"ok": False, "say": _pf_say("game")}
        if verb == "pause":
            out = await self.game_pause(appid)
            if not out.get("ok") and out.get("why") == "busy":
                return {"ok": False, "say": "Ahora no se puede pausar: se está descomprimiendo."}
            return {"ok": True}
        if verb == "cancel":
            await self.game_cancel(appid)
            return {"ok": True}
        out = await self.game_remove(appid)
        return {"ok": True} if out.get("ok") else {"ok": False, "say": _pf_say(out.get("why"))}

    async def _pf_asked(self, reader, writer):
        """One question from Punktfunk's plugin over its socket, as a line of JSON: {"ask": "install",
        "pause", "cancel", "uninstall", "downloads" or "appid", "key": the game's number}."""
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            ask = json.loads(line.decode("utf-8", "ignore") or "{}")
            ask = ask if isinstance(ask, dict) else {}
            verb, key = _text(ask.get("ask")), _number(ask.get("key"))
            loop = asyncio.get_running_loop()
            if verb == "downloads":
                rows = await loop.run_in_executor(None, lambda: _pf_downloads(_pf_catalog(_pf["groups"])))
                out = {"ok": True, "rows": rows}
            elif verb == "install":
                out = await self._pf_install(key)
            elif verb in ("pause", "cancel", "uninstall"):
                out = await self._pf_act(verb, key)
            elif verb == "appid":
                game = await self._pf_game(key)
                out = {"ok": bool(game and game["appid"]), "appid": game["appid"] if game else 0}
            else:
                out = {"ok": False, "say": "?"}
            writer.write((json.dumps(out) + "\n").encode("utf-8"))
            await writer.drain()
        except Exception as e:
            decky.logger.warning("Punktfunk not answered: %s", e)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _pf_loop(self):
        """Keeps the catalog Punktfunk reads up to date."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                await loop.run_in_executor(None, _pf_refresh)
            except Exception as e:
                decky.logger.warning("Catalog for Punktfunk not written: %s", e)
            await asyncio.sleep(PF_LOOK_EVERY)

    async def _main(self):
        try:
            os.makedirs(RUNTIME, exist_ok=True)
            try:
                os.remove(LAUNCH_SOCKET)
            except OSError:
                pass
            _launch_server[0] = await asyncio.start_unix_server(self._launch_asked, path=LAUNCH_SOCKET)
            os.chmod(LAUNCH_SOCKET, 0o600)
        except Exception as e:  # without it games start as before, unchecked
            decky.logger.warning("Launch socket not opened: %s", e)
        if _pf_on():
            try:
                os.makedirs(PF_DIR, mode=0o700, exist_ok=True)
                try:
                    os.remove(PF_SOCKET)
                except OSError:
                    pass
                _pf["server"] = await asyncio.start_unix_server(self._pf_asked, path=PF_SOCKET)
                os.chmod(PF_SOCKET, 0o600)
                _install_program(PLAYER_SOURCE, PLAYER)
                _pf["task"] = asyncio.get_running_loop().create_task(self._pf_loop())
            except Exception as e:  # Punktfunk then lists only what Steam has, as before
                decky.logger.warning("Punktfunk socket not opened: %s", e)
        _move_in()
        with _lock:
            data = _read_games()
            if data["games"]:
                _install_launcher()
                moved = _relocate_all(data)
                if moved:
                    decky.logger.info("%d games found in RetroDECK's new folder", moved)
                _write_games(data)
                _sweep_parts()

    async def _unload(self):
        if _launch_server[0] is not None:
            _launch_server[0].close()
            _launch_server[0] = None
        try:
            os.remove(LAUNCH_SOCKET)
        except OSError:
            pass
        if _pf["task"] is not None:
            _pf["task"].cancel()
            _pf["task"] = None
        if _pf["server"] is not None:
            _pf["server"].close()
            _pf["server"] = None
            try:
                os.remove(PF_SOCKET)
            except OSError:
                pass
        for job in list(_jobs.values()):
            job["cancel"].set()

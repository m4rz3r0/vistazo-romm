#!/usr/bin/env python3
"""Backend checks against a pretend RomM, all inside a throwaway folder: `python3 tools/test_backend.py`.

Nothing here talks to a real server or touches the real home: `decky` is a stand-in pointing at
the temporary folder, and "flatpak" is a script that only writes down what it was started with."""
import asyncio
import io
import datetime
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from unittest import mock
import threading
import time
import types
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="vistazo-test-")
HOME = os.path.join(TMP, "home")
ROMS = os.path.join(HOME, "retrodeck", "roms")
PLUGIN = os.path.join(TMP, "plugin")
os.makedirs(os.path.join(HOME, ".config", "romm-tender"))
os.makedirs(os.path.join(ROMS, "nds"))
os.makedirs(os.path.join(PLUGIN, "bin"))
shutil.copy(os.path.join(REPO, "bin", "vistazo-launch"), os.path.join(PLUGIN, "bin", "vistazo-launch"))

decky = types.ModuleType("decky")
decky.DECKY_PLUGIN_DIR = PLUGIN
decky.DECKY_HOME = os.path.join(TMP, "homebrew")
decky.DECKY_USER_HOME = HOME
decky.DECKY_PLUGIN_SETTINGS_DIR = os.path.join(TMP, "settings")
decky.DECKY_PLUGIN_RUNTIME_DIR = os.path.join(TMP, "runtime")
decky.logger = types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None)
sys.modules["decky"] = decky
sys.dont_write_bytecode = True
sys.path.insert(0, REPO)
import main  # noqa: E402

BASIC = ["me.read", "me.write"]
GAMES = BASIC + ["roms.read", "platforms.read", "collections.read", "firmware.read", "assets.read", "assets.write"]
NASTY = 'Po ké "mon" $(touch pwned); `id` \\ & .nds'
DATA = os.urandom(300_000)


class RomM:
    """What the pretend server knows, and what it was asked."""

    def __init__(self):
        self.calls = []  # (method, path, bearer)
        self.codes = {}  # pairing code -> (token id, scopes)
        self.tokens = {}  # raw token -> id
        self.deleted = []
        self.roms = {}
        self.content = {}  # rom id -> bytes, or a callable(handler)
        self.ranges = True  # whether the rest of a file is sent when asked for
        self.ranges_asked = []  # the Range header of each download (None: the whole file)
        self.redirect_to = None
        self.refuse = set()  # rom ids answered with 401
        self.covers = {}  # file name -> bytes
        self.searches = []  # what the games list was asked for
        self.saves = []  # RomM's saves: dicts with "data"
        self.uploads = []  # (query, file name) of each save sent
        self.short_save = False  # send less of a save than announced
        self.firmware = []  # RomM's BIOS files: dicts with "data"
        self.platforms = []  # RomM's consoles, as /api/platforms answers them
        self.platforms_down = 0  # a status to answer /api/platforms with instead
        self.meanwhile = None  # called while a save is on its way
        self.collections = {"standard": [], "smart": [], "virtual": {}}  # virtual: sort -> list
        self.collections_down = set()  # "standard", "smart" or a sort: answered with 500
        self.play = []  # each batch of play sessions sent
        self.play_down = 0  # a status to answer play sessions with instead
        self.scopes = {}  # raw token -> its scopes
        self.devices = {}  # device id -> what it was registered with
        self.devices_down = 0  # a status to answer the devices endpoints with instead
        self.ui_settings = None  # the account's UI settings, as RomM keeps them
        self.ui_puts = []  # each ui_settings written, as sent
        self.synced = set()  # (device id, save id): the versions RomM knows each device has
        self.downloaded = []  # (save id, body) of each download a device confirmed
        self.play_devices = []  # the device id each batch of play sessions came with
        self.upload_down = 0  # a status to answer save uploads with, once the device is checked


romm = RomM()


def rom(rom_id, name, files=None, slug="nds", **more):
    files = files if files is not None else [(name, len(DATA))]
    return {
        "id": rom_id, "name": "Game %d" % rom_id, "fs_name": name, "fs_name_no_ext": name.rsplit(".", 1)[0],
        "platform_slug": slug, "platform_fs_slug": slug, "platform_display_name": "Nintendo DS",
        "fs_size_bytes": sum(s for _, s in files), "summary": "A game.", "ra_id": 77,
        "metadatum": {"genres": ["RPG", "Adventure"], "first_release_date": 1252713600, "average_rating": 87.5},
        "merged_ra_metadata": {"achievements": [{"badge_id": "100", "title": "First", "num_awarded": 9, "points": 5,
                                                 "badge_url": "https://media.retroachievements.org/Badge/100.png"}]},
        "files": [{"id": i, "file_name": n, "file_size_bytes": s} for i, (n, s) in enumerate(files, 1)],
        "user_saves": [{"secret": "never leaves the backend"}],
        **more,
    }


SAVE_CLOCK = [0]


def romm_hash(data):
    """RomM's content hash of a save: a zip by what is inside it, anything else by its bytes."""
    import hashlib
    import io
    import zipfile
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            lines = ["%s:%s" % (n, hashlib.md5(z.read(n)).hexdigest()) for n in sorted(z.namelist()) if not n.endswith("/")]
        return hashlib.md5("\n".join(lines).encode()).hexdigest()
    return hashlib.md5(data).hexdigest()


def add_save(rom_id, data, name, emulator="retroarch-melondsds", slot="autosave", **more):
    """A save as RomM keeps it; each one a little newer than the last."""
    SAVE_CLOCK[0] += 1
    stem, ext = os.path.splitext(name)
    save = {
        "id": 100 + SAVE_CLOCK[0], "rom_id": rom_id, "file_name": "%s [2026-11-13 %02d]%s" % (stem, SAVE_CLOCK[0], ext),
        "file_name_no_tags": name, "file_extension": ext.lstrip("."), "file_size_bytes": len(data),
        "emulator": emulator, "slot": slot, "content_hash": romm_hash(data),
        "updated_at": "2026-11-13T10:%02d:00Z" % SAVE_CLOCK[0], "missing_from_fs": False, "data": data, **more,
    }
    romm.saves.append(save)
    return {k: v for k, v in save.items() if k != "data"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, status, body=None, headers=()):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def handle_one(self, method):
        bearer = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path.endswith("/api/heartbeat") and self.path != "/api/heartbeat":
            # A web page for any other path, as RomM's own front end does.
            raw = b"<!doctype html><title>RomM</title>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            return self.wfile.write(raw)
        if self.path == "/api/heartbeat":
            romm.calls.append((method, self.path, bearer))
            return self.reply(200, {"SYSTEM": {"VERSION": "5.4.1"}}) if self.server is server else self.reply(404)
        if method == "POST" and self.path.startswith("/api/saves?"):
            from urllib.parse import parse_qs, urlsplit
            romm.calls.append((method, self.path, bearer))
            if bearer not in romm.tokens:
                return self.reply(401)
            q = parse_qs(urlsplit(self.path).query)
            edge = self.headers["Content-Type"].split("boundary=")[1].encode()
            part = raw.split(b"--" + edge)[1]
            head, _, data = part.partition(b"\r\n\r\n")
            data = data[:-2] if data.endswith(b"\r\n") else data
            name = head.split(b'filename="')[1].split(b'"')[0].decode()
            romm.uploads.append((q, name))
            # As RomM 5.4.1: a device must be the player's, and may only add to a slot whose newest
            # version it has seen, unless told to overwrite.
            device = q.get("device_id", [None])[0]
            if device is not None:
                if "devices.write" not in romm.scopes.get(bearer, ()):
                    return self.reply(403)
                if device not in romm.devices:
                    return self.reply(404)
                slot_saves = [s for s in romm.saves if s["rom_id"] == int(q["rom_id"][0]) and s["slot"] == q.get("slot", [None])[0]]
                newest = max(slot_saves, key=lambda s: s["updated_at"]) if slot_saves else None
                if newest and q.get("overwrite") != ["true"] and (device, newest["id"]) not in romm.synced:
                    return self.reply(409)
            if romm.upload_down:
                return self.reply(romm.upload_down)
            saved = add_save(int(q["rom_id"][0]), data, name, q.get("emulator", [None])[0], q.get("slot", [None])[0])
            if device is not None:
                romm.saves[-1]["origin_device_id"] = device
                romm.synced.add((device, saved["id"]))
            return self.reply(201, saved)
        if method == "PUT" and self.path == "/api/users/1":
            from urllib.parse import parse_qs
            romm.calls.append((method, self.path, bearer))
            if bearer not in romm.tokens:
                return self.reply(401)
            assert self.headers["Content-Type"] == "application/x-www-form-urlencoded"
            form = parse_qs(raw.decode())
            romm.ui_puts.append(form["ui_settings"][0])
            romm.ui_settings = json.loads(form["ui_settings"][0])
            return self.reply(200, {"id": 1, "ui_settings": romm.ui_settings})
        body = json.loads(raw) if raw else None
        romm.calls.append((method, self.path, bearer))
        parts = self.path.split("?")[0].strip("/").split("/")
        if self.path == "/api/client-tokens/exchange":
            hit = romm.codes.get((body or {}).get("code"))
            if not hit:
                return self.reply(404)
            token = "tok-%d" % hit[0]
            romm.tokens[token] = hit[0]
            romm.scopes[token] = list(hit[1])
            return self.reply(200, {"id": hit[0], "raw_token": token, "scopes": hit[1]})
        if method == "DELETE" and parts[:2] == ["api", "client-tokens"]:
            romm.deleted.append((int(parts[2]), bearer))
            return self.reply(204)
        if bearer not in romm.tokens:
            return self.reply(401)
        if method == "POST" and self.path == "/api/play-sessions":
            if romm.play_down:
                return self.reply(romm.play_down)
            sessions = body["sessions"]
            romm.play.append(sessions)
            romm.play_devices.append(body.get("device_id"))
            return self.reply(201, {"results": [{"index": i, "status": "created", "id": i + 1} for i in range(len(sessions))],
                                    "created_count": len(sessions), "skipped_count": 0})
        if parts[:2] == ["api", "devices"] or (parts[:2] == ["api", "saves"] and parts[3:] == ["downloaded"]):
            if "devices.write" not in romm.scopes.get(bearer, ()):
                return self.reply(403)
            if romm.devices_down:
                return self.reply(romm.devices_down)
            if method == "POST" and parts == ["api", "devices"]:
                import uuid
                device = str(uuid.uuid4())
                romm.devices[device] = body
                return self.reply(201, {"device_id": device, "name": body.get("name"), "created_at": "2026-11-14T10:00:00Z"})
            if method == "PUT" and len(parts) == 3:
                return self.reply(200, {"id": parts[2], **romm.devices[parts[2]]}) if parts[2] in romm.devices else self.reply(404)
            if method == "POST" and parts[3:] == ["downloaded"]:
                if body.get("device_id") not in romm.devices:
                    return self.reply(404)
                romm.downloaded.append((int(parts[2]), body))
                romm.synced.add((body["device_id"], int(parts[2])))
                return self.reply(200, {"id": int(parts[2])})
            return self.reply(405)
        if self.path == "/api/users/me":
            return self.reply(200, {"id": 1, "ui_settings": romm.ui_settings, "ra_progression": {"results": [
                {"rom_ra_id": 77, "num_awarded": 2, "num_awarded_hardcore": 1, "max_possible": 130,
                 "earned_achievements": [{"id": "100", "date_hardcore": "x"}, {"id": "101"}]}]}})
        if parts[:4] == ["assets", "romm", "resources", "roms"]:
            raw = romm.covers.get(parts[-1])
            if raw is None:
                return self.reply(404)
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if parts == ["api", "saves"] and method == "GET":
            from urllib.parse import parse_qs, urlsplit
            q = parse_qs(urlsplit(self.path).query)
            rom_id = int(q["rom_id"][0])
            return self.reply(200, [{k: v for k, v in s.items() if k != "data"} for s in romm.saves if s["rom_id"] == rom_id])
        if parts[:2] == ["api", "saves"] and len(parts) == 4 and parts[3] == "content":
            save = next((s for s in romm.saves if s["id"] == int(parts[2])), None)
            if not save:
                return self.reply(404)
            if romm.meanwhile:
                romm.meanwhile()
            data = save["data"][:-3] if romm.short_save else save["data"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parts == ["api", "platforms"]:
            return self.reply(romm.platforms_down, {}) if romm.platforms_down else self.reply(200, romm.platforms)
        if parts == ["api", "firmware"]:
            from urllib.parse import parse_qs, urlsplit
            pid = int(parse_qs(urlsplit(self.path).query)["platform_id"][0])
            return self.reply(200, [{k: v for k, v in f.items() if k != "data"} for f in romm.firmware if f["platform_id"] == pid or f.get("stray")])
        if parts[:2] == ["api", "firmware"] and len(parts) == 5 and parts[3] == "content":
            f = next((x for x in romm.firmware if x["id"] == int(parts[2])), None)
            if not f:
                return self.reply(404)
            self.send_response(200)
            self.send_header("Content-Length", str(len(f["data"])))
            self.end_headers()
            self.wfile.write(f["data"])
            return
        if parts[:2] == ["api", "collections"] and method == "GET":
            from urllib.parse import parse_qs, urlsplit
            what = parts[2] if len(parts) > 2 else "standard"
            if what == "virtual":
                what = parse_qs(urlsplit(self.path).query)["type"][0]
                found = romm.collections["virtual"].get(what, [])
            else:
                found = romm.collections.get(what)
            if what in romm.collections_down or found is None:
                return self.reply(500)
            return self.reply(200, found)
        if parts == ["api", "roms"]:
            from urllib.parse import parse_qs, urlsplit
            q = parse_qs(urlsplit(self.path).query)
            romm.searches.append(q)
            every = [romm.roms[k] for k in sorted(romm.roms)]
            start, size = int(q.get("offset", ["0"])[0]), int(q.get("limit", ["50"])[0])
            return self.reply(200, {"items": every[start:start + size], "total": None})
        if parts[:2] == ["api", "roms"] and len(parts) == 3:
            rom_id = int(parts[2])
            if romm.redirect_to:
                return self.reply(302, headers=[("Location", romm.redirect_to + self.path)])
            if rom_id in romm.refuse:
                return self.reply(401)
            return self.reply(200, romm.roms[rom_id]) if rom_id in romm.roms else self.reply(404)
        if parts[:2] == ["api", "roms"] and len(parts) == 5 and parts[3] == "content":
            content = romm.content.get(int(parts[2]))
            if content is None:
                return self.reply(404)
            if callable(content):
                return content(self)
            asked = self.headers.get("Range")
            romm.ranges_asked.append(asked)
            if asked and romm.ranges:
                start = int(asked[len("bytes="):-1])
                if start >= len(content):
                    return self.reply(416)
                self.send_response(206)
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, len(content) - 1, len(content)))
                self.send_header("Content-Length", str(len(content) - start))
                self.end_headers()
                self.wfile.write(content[start:])
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self.reply(404)

    def do_GET(self):
        self.handle_one("GET")

    def do_POST(self):
        self.handle_one("POST")

    def do_DELETE(self):
        self.handle_one("DELETE")

    def do_PUT(self):
        self.handle_one("PUT")


class Quiet(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # a download cut on purpose


server = Quiet(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d" % server.server_port
# A second address, standing for "somewhere else": a redirect there must never be followed.
other = Quiet(("127.0.0.1", 0), Handler)
threading.Thread(target=other.serve_forever, daemon=True).start()
OTHER = "http://127.0.0.1:%d" % other.server_port


def tender(url):
    with open(os.path.join(HOME, ".config", "romm-tender", "settings.json"), "w") as f:
        json.dump({"romm_url": url, "romm_api_token": "tender-own-key"}, f)


plugin = main.Plugin()
# One loop for the whole run, as in Decky: a download goes on after the call that started it.
loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()


def run(call):
    return asyncio.run_coroutine_threadsafe(call, loop).result(30)


checks = 0


def check(what, got, want=True):
    global checks
    checks += 1
    if got != want:
        raise AssertionError("%s: got %r, wanted %r" % (what, got, want))


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def wait(appid, timeout=10):
    """The state a download ends in."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        state = run(plugin.game_state(appid))
        if state.get("job", {}).get("state") not in ("downloading", "queued"):
            return state
        time.sleep(0.02)
    raise AssertionError("download did not end")


def leftovers():
    return sorted(n for _, _, names in os.walk(ROMS) for n in names if n.endswith(".part"))


def pair(code, games=False):
    return run(plugin.romm_pair(code, games))


def test_key():
    tender(URL)
    check("no key yet", run(plugin.romm_status()), "unlinked")
    check("nothing to check", run(plugin.romm_check()), "unlinked")
    check("games need a key", run(plugin.romm_game(5)), {"ok": False, "why": "unlinked"})
    check("not a code", pair("abc")["result"], "code")
    check("unknown code", pair("ZZZZZZZZ")["result"], "invalid")

    # A key for achievements only is still taken, as before, and says what it is.
    romm.codes["ABCDEFGH"] = (1, BASIC)
    check("small key paired", pair("abcd-efgh"), {"result": "ok", "missing": [], "extra": []})
    check("small key status", run(plugin.romm_status()), "limited")
    check("small key works", run(plugin.romm_check()), "ok")
    check("key file private", mode(main.KEY_FILE), 0o600)
    check("small key reads achievements", run(plugin.romm_progress(77))["earned"], 2)
    check("small key sees no games", run(plugin.romm_game(5)), {"ok": False, "why": "limited"})
    check("small key takes no game", run(plugin.game_take(900, 5))["why"], "limited")
    check("small key downloads nothing", run(plugin.game_download(900))["why"], "limited")
    check("no game was asked for", [c for c in romm.calls if "/api/roms" in c[1]], [])

    # A key stored before the wider set says nothing of what it may do: the small one.
    with open(main.KEY_FILE) as f:
        old = json.load(f)
    old.pop("scopes")
    main._write_key(old)
    check("old stored key", run(plugin.romm_status()), "limited")
    check("old stored key still reads achievements", run(plugin.romm_progress(77))["ok"], True)

    # Asked to look after the games, a key short of the set is kept for what it can do, and the
    # answer names what it lacks.
    romm.codes["JKMNPQRS"] = (2, BASIC + ["roms.read", "platforms.read", "assets.read", "assets.write"])
    check("short key kept, with what it lacks", pair("JKMNPQRS", True),
          {"result": "limited", "missing": ["collections.read", "firmware.read"], "extra": []})
    check("short key status", run(plugin.romm_status()), "limited")
    check("the key it replaced is revoked", romm.deleted[-1], (1, "tok-1"))

    # A key that can do more than Vistazo should ever hold, or less than it always needs, is
    # thrown away at RomM, and the answer names why. The stored one stays.
    romm.codes["TUVWXYZ2"] = (3, GAMES + ["roms.write", "users.write"])
    check("wider key refused", pair("TUVWXYZ2", True), {"result": "scopes", "missing": [], "extra": ["roms.write", "users.write"]})
    check("wider key revoked", romm.deleted[-1], (3, "tok-3"))
    romm.codes["23456789"] = (4, ["me.read", "roms.read"])
    check("key without the basics refused", pair("23456789"), {"result": "scopes", "missing": ["me.write"], "extra": []})
    check("it is revoked too", romm.deleted[-1], (4, "tok-4"))
    check("kept key untouched", run(plugin.romm_status()), "limited")

    # The whole set, whichever way it was asked for: good for everything.
    romm.codes["HGFEDCBA"] = (5, list(reversed(GAMES)))
    check("wide key paired", pair("HGFEDCBA"), {"result": "ok", "missing": [], "extra": []})
    check("wide key status", run(plugin.romm_status()), "linked")
    check("key file private", mode(main.KEY_FILE), 0o600)
    check("the key it replaced is revoked", romm.deleted[-1], (2, "tok-2"))
    check("wide key still reads achievements", run(plugin.romm_progress(77))["hardcore"], 1)

    # A key RomM no longer accepts is forgotten when asked about.
    kept = dict(romm.tokens)
    romm.tokens.clear()
    check("revoked at RomM", run(plugin.romm_check()), "refused")
    check("forgotten", run(plugin.romm_status()), "unlinked")
    romm.tokens.update(kept)
    romm.codes["HGFEDCBA"] = (5, GAMES)
    check("paired again", pair("HGFEDCBA", True)["result"], "ok")


def test_server():
    tender_file = os.path.join(HOME, ".config", "romm-tender", "settings.json")
    for f in (tender_file, main.SERVER_FILE):
        if os.path.exists(f):
            os.remove(f)
    check("no address anywhere", (run(plugin.romm_status()), run(plugin.romm_server())), ("no-server", {"url": "", "insecure": False}))

    # The first time, Tender's address is taken and kept: it stays once Tender is gone.
    tender(URL + "/")
    check("Tender's address taken", run(plugin.romm_server()), {"url": URL, "insecure": False})
    check("kept, private", mode(main.SERVER_FILE), 0o600)
    os.remove(tender_file)
    check("still there without Tender", run(plugin.romm_server())["url"], URL)

    # Setting it: only addresses, and only where RomM answers or nothing answers yet.
    for bad in ("", "romm.local", "ftp://romm.local", "http://user:pw@romm.local", "http://romm.local/?a=1",
                "http://romm.local:port", "http://rom m.local", "javascript:alert(1)"):
        check("not an address: %r" % bad, run(plugin.romm_set_server(bad)), {"result": "invalid"})
    check("not changed by them", run(plugin.romm_server())["url"], URL)
    before = len(romm.calls)
    check("something else answers", run(plugin.romm_set_server(OTHER)), {"result": "not-romm"})
    check("not kept", run(plugin.romm_server())["url"], URL)
    check("a web page where RomM's answer should be", run(plugin.romm_set_server(URL + "/nope")), {"result": "not-romm"})
    check("not kept either", run(plugin.romm_server())["url"], URL)
    check("no key sent to see", [c[2] for c in romm.calls[before:]], [""])
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    asleep = "http://127.0.0.1:%d" % sock.getsockname()[1]
    sock.close()
    check("nobody there yet: kept", run(plugin.romm_set_server(asleep)), {"result": "unreachable"})
    check("asleep address kept", run(plugin.romm_server())["url"], asleep)
    check("RomM there", run(plugin.romm_set_server(URL + "/", True)), {"result": "ok"})
    check("plain http never skips checks", run(plugin.romm_server()), {"url": URL, "insecure": False})
    check("scheme as checks read it", main._clean_url("HTTPS://Romm.example"), "https://Romm.example")
    for bad in ("http://romm.local?", "http://romm.local/#"):
        check("empty query or fragment: %r" % bad, main._clean_url(bad), None)
    import socket as sk
    talker = sk.socket()
    talker.bind(("127.0.0.1", 0))
    talker.listen(1)

    def speak():
        c, _ = talker.accept()
        c.recv(1024)
        c.sendall(b"SSH-2.0-OpenSSH\r\n")
        c.close()
    threading.Thread(target=speak, daemon=True).start()
    check("not HTTP on that port", run(plugin.romm_set_server("http://127.0.0.1:%d" % talker.getsockname()[1])), {"result": "not-romm"})
    talker.close()
    main._write_private(main.SERVER_FILE, {"url": URL, "insecure": "no"})
    check("only a true choice skips the check", run(plugin.romm_server())["insecure"], False)
    main._write_private(main.SERVER_FILE, {"url": URL, "insecure": False})
    check("an https address can skip the check", main._clean_url("https://romm.example:8443/sub/"), "https://romm.example:8443/sub")


def tender_db(rows):
    """Tender's database as far as the add-on reads it: (rom_id, appid, name, file, folder)."""
    import sqlite3
    folder = os.path.join(HOME, ".local", "share", "romm-tender")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "romm_sync.db")
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE roms (rom_id INTEGER PRIMARY KEY, platform_slug TEXT, name TEXT NOT NULL, fs_name TEXT, shortcut_app_id INTEGER)")
    con.execute("CREATE TABLE rom_installs (rom_id INTEGER PRIMARY KEY, file_path TEXT NOT NULL, rom_dir TEXT, platform_slug TEXT, installed_at TEXT)")
    for rom_id, appid, name, file, folder in rows:
        con.execute("INSERT INTO roms (rom_id, platform_slug, name, fs_name, shortcut_app_id) VALUES (?, 'nds', ?, ?, ?)", (rom_id, name, name, appid))
        if file:
            con.execute("INSERT INTO rom_installs (rom_id, file_path, rom_dir) VALUES (?, ?, ?)", (rom_id, file, folder))
    con.commit()
    con.close()
    return path


def test_adopt():
    def write(data, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    nds = os.path.join(ROMS, "nds")
    os.makedirs(nds, exist_ok=True)
    # Game 60: Tender brought one file. Game 61: Tender brought a folder of files. Game 62: only
    # in Tender's list, not on the disk. Game 63: Tender's file outside RetroDECK's folder.
    one = os.path.join(nds, "Sixty.nds")
    write(DATA, one)
    multi = os.path.join(nds, "Sixty-one")
    write(b"a", os.path.join(multi, "disc1.bin"))
    write(b"b", os.path.join(multi, "disc2.bin"))
    write(b"cue", os.path.join(multi, "game.m3u"))
    outside = os.path.join(TMP, "elsewhere.nds")
    write(DATA, outside)
    for n, name in ((60, "Sixty.nds"), (61, "Sixty-one"), (62, "Sixty-two.nds"), (63, "Sixty-three.nds")):
        romm.roms[n] = rom(n, name)
    tender_db([
        (60, 6000, "Sixty", one, None),
        (61, 6100, "Sixty-one", os.path.join(multi, "game.m3u"), multi),
        (62, 6200, "Sixty-two", None, None),
        (63, 6300, "Sixty-three", outside, None),
        (64, None, "Unbound", None, None),
    ])
    listed = run(plugin.tender_games())
    check("Tender's entries", [(g["appid"], g["rom_id"], g["mine"]) for g in listed],
          [(6000, 60, False), (6100, 61, False), (6200, 62, False), (6300, 63, False)])

    # Only Tender's own entry of that game, with that number.
    check("not Tender's", run(plugin.game_adopt(7000, 60))["why"], "not-tender")
    check("another game", run(plugin.game_adopt(6000, 61))["why"], "not-tender")

    # The add-on's own entry of game 60 merges in; Tender's file becomes the add-on's.
    run(plugin.game_take(6001, 60))
    got = run(plugin.game_adopt(6000, 60, "/t/bin/rom-launcher", "/t/bin", "", 6001))
    check("taken over", (got["ok"], got["options"], got["dropped"], got["installed"], got["removable"], got["exe"]),
          (True, "60", 6001, True, True, main.LAUNCHER))
    games = run(plugin.game_list())
    check("own entry folded in", (games.get("6000"), "6001" in games), (60, False))
    check("again: nothing changes", run(plugin.game_adopt(6000, 60))["dropped"], 0)
    check("listed as mine", [g["mine"] for g in run(plugin.tender_games()) if g["appid"] == 6000], [True])

    # A folder of files: all of them, and the folder, are the add-on's.
    got = run(plugin.game_adopt(6100, 61))
    check("folder taken over", (got["installed"], got["removable"]), (True, True))
    removed = run(plugin.game_remove(6100))
    check("folder removed", (removed["ok"], os.path.exists(multi)), (True, False))

    # Nothing on the disk: taken over, not installed, downloadable as any other.
    got = run(plugin.game_adopt(6200, 62))
    check("nothing on disk", (got["ok"], got["installed"], got["removable"]), (True, False, False))

    # Outside RetroDECK's folder: never the add-on's to remove.
    got = run(plugin.game_adopt(6300, 63))
    check("outside the folder", (got["ok"], got["removable"]), (True, False))
    check("still there", os.path.isfile(outside), True)

    # A file changed since the takeover is not removed.
    with open(one, "ab") as f:
        f.write(b"played")
    run(plugin.game_remove(6000))
    check("changed file kept", os.path.isfile(one), True)

    # Given back: what the entry had before, so Tender can start it again.
    back = run(plugin.game_release(6000))
    check("given back", (back["exe"], back["start_dir"]), ("/t/bin/rom-launcher", "/t/bin"))
    for appid in (6100, 6200, 6300):
        run(plugin.game_release(appid))
    os.remove(one)
    os.remove(outside)

    # Never another game's files: a folder holding other games, a file of another game, a file
    # another of the add-on's games already holds.
    shelf = os.path.join(nds, "Collection")
    write(DATA, os.path.join(shelf, "Seventy.nds"))
    write(DATA, os.path.join(shelf, "Other.nds"))
    stranger = os.path.join(nds, "Stranger.nds")
    write(DATA, stranger)
    shared = os.path.join(nds, "Shared.nds")
    write(DATA, shared)
    for n, name in ((70, "Seventy.nds"), (71, "Seventy-one.nds"), (72, "Shared.nds"), (73, "Shared.nds")):
        romm.roms[n] = rom(n, name)
    tender_db([
        (70, 7000, "Seventy", os.path.join(shelf, "Seventy.nds"), shelf),
        (71, 7100, "Seventy-one", stranger, None),
        (72, 7200, "Shared", shared, None),
        (73, 7300, "Shared again", shared, None),
    ])
    got = run(plugin.game_adopt(7000, 70))
    check("own file in a shared folder", (got["installed"], got["removable"]), (True, True))
    run(plugin.game_remove(7000))
    check("only its own file went", (os.path.exists(os.path.join(shelf, "Seventy.nds")), os.path.isfile(os.path.join(shelf, "Other.nds"))), (False, True))
    got = run(plugin.game_adopt(7100, 71))
    check("another game's file is not taken", (got["installed"], got["removable"]), (False, False))
    check("first holder", run(plugin.game_adopt(7200, 72))["removable"], True)
    check("second holder does not get it", run(plugin.game_adopt(7300, 73))["removable"], False)
    run(plugin.game_remove(7300))
    check("the first holder's file stays", os.path.isfile(shared), True)
    for appid in (7000, 7100, 7200, 7300):
        run(plugin.game_release(appid))
    for f in (os.path.join(shelf, "Other.nds"), stranger, shared):
        os.remove(f)

    # The game's folder being walked never changes which names count as the game's: a stray file
    # named in the same row is still not taken.
    gamel = os.path.join(nds, "GameL")
    write(DATA, os.path.join(gamel, "GameL.nds"))
    write(DATA, os.path.join(gamel, "Victim.nds"))
    victim = os.path.join(nds, "Victim.nds")
    write(DATA, victim)
    romm.roms[113] = rom(113, "GameL.nds", fs_name="GameL")
    tender_db([(113, 11300, "GameL", victim, gamel)])
    run(plugin.game_adopt(11300, 113))
    run(plugin.game_remove(11300))
    check("a stray file in the row stays", os.path.isfile(victim), True)
    run(plugin.game_release(11300))
    os.remove(victim)

    # A database that cannot be read is no list, and no takeover.
    with open(tender_db([]), "wb") as f:
        f.write(b"not a database")
    check("unreadable database", (run(plugin.tender_games()), run(plugin.game_adopt(6000, 60))["why"]), ([], "not-tender"))


def test_detail():
    romm.roms[5] = rom(5, "Game.nds")
    got = run(plugin.romm_game(5))
    check("detail ok", got["ok"], True)
    check("detail fields", (got["rom_id"], got["platform"], got["system"], got["ra_id"], got["rating"], got["released"]),
          (5, "Nintendo DS", "nds", 77, 87.5, 1252713600))
    check("achievements listed", got["achievements"][0]["badge_id"], "100")
    check("nothing else leaves the backend", "user_saves" in got or "files" in got, False)
    check("unknown game", run(plugin.romm_game(404)), {"ok": False, "why": "game"})
    check("not a number", run(plugin.romm_game("x")), {"ok": False, "why": "game"})

    # The whole library, page by page, with only what a list needs; of several versions, the main one.
    before = dict(romm.roms)
    main.LIBRARY_PAGE = 2
    romm.roms[30] = rom(30, "A.nds")
    romm.roms[31] = rom(31, "B.iso", slug="ngc")
    romm.roms[32] = rom(32, "C.nds", sibling_roms=[{"id": 33, "is_main_sibling": True}], rom_user={"is_main_sibling": False})
    romm.roms[33] = rom(33, "D.nds", sibling_roms=[{"id": 32, "is_main_sibling": False}], rom_user={"is_main_sibling": True})
    # Two versions with none marked as the main one: the first stands for both.
    romm.roms[34] = rom(34, "E.nds", sibling_roms=[{"id": 35, "is_main_sibling": False}], rom_user={"is_main_sibling": False})
    romm.roms[35] = rom(35, "F.nds", sibling_roms=[{"id": 34, "is_main_sibling": False}], rom_user={"is_main_sibling": False})
    got = run(plugin.romm_library())
    ids = [g["rom_id"] for g in got["games"]]
    check("first of unmarked versions", (34 in ids, 35 in ids), (True, False))
    check("library ok", (got["ok"], 32 in ids, {30, 31, 33} <= set(ids)), (True, False, True))
    check("library row", next(g for g in got["games"] if g["rom_id"] == 30), {"rom_id": 30, "name": "Game 30", "platform": "Nintendo DS", "system": "nds", "size": 300000, "versions": [30]})
    check("unknown system says so", next(g for g in got["games"] if g["rom_id"] == 31)["system"], "")
    check("asked in pages", [q["offset"][0] for q in romm.searches[-((len(romm.roms) + 2) // 2):]][:2], ["0", "2"])
    check("asked small", romm.searches[-1]["with_filter_values"], ["false"])
    main.LIBRARY_PAGE = 250
    romm.roms.clear()
    romm.roms.update(before)

    # A redirect is never followed: the key reaches no other address.
    romm.redirect_to = OTHER
    before = len(romm.calls)
    check("redirect refused: only what was kept comes back", run(plugin.romm_game(5, True)).get("kept"), True)
    check("one call only, none to the other address", len(romm.calls) - before, 1)
    romm.redirect_to = None

    # What a game is hardly changes: asked once, then given from what was kept, unless asked anew.
    before = len(romm.calls)
    again = run(plugin.romm_game(5))
    check("given again without asking RomM", (again["ok"], again["name"], "kept" in again, "_at" in again, len(romm.calls) - before), (True, "Game 5", False, False, 0))
    check("asked anew", (run(plugin.romm_game(5, True))["ok"], len(romm.calls) - before), (True, 1))
    old_good = main.GAME_GOOD
    main.GAME_GOOD = 0
    run(plugin.romm_game(5))
    check("too old: asked again", len(romm.calls) - before, 2)
    main.GAME_GOOD = old_good
    # A player's achievements: a recent reading is given again; after a game they are read anew.
    run(plugin.romm_progress(77, False, True))
    before = len(romm.calls)
    check("recent achievements given again", (run(plugin.romm_progress(77, False, True))["earned"], len(romm.calls) - before), (2, 0))
    check("the plain call always asks", (run(plugin.romm_progress(77))["earned"], len(romm.calls) - before), (2, 1))

    # RomM asleep: the page still gets what RomM said last time, marked as kept.
    check("progress read once", run(plugin.romm_progress(77, False, True))["earned"], 2)
    good = main.PROGRESS_GOOD
    main.PROGRESS_GOOD = 0  # as when the last reading is old
    server.shutdown()
    asleep = run(plugin.romm_game(5, True))
    check("kept game", (asleep["ok"], asleep.get("kept"), asleep["name"], len(asleep["achievements"])), (True, True, "Game 5", 1))
    check("never seen game", run(plugin.romm_game(404)), {"ok": False, "why": "unreachable"})
    check("kept progress", [run(plugin.romm_progress(77, False, True)).get(k) for k in ("ok", "kept", "earned", "total")], [True, True, 2, 130])
    check("only when asked for", run(plugin.romm_progress(77)), {"ok": False, "why": "unreachable"})
    check("asleep says so", run(plugin.romm_check()), "unreachable")
    check("kept file private", mode(main.KEPT_FILE), 0o600)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    main.PROGRESS_GOOD = good
    check("awake again", "kept" in run(plugin.romm_game(5, True)), False)

    # Tender pointing elsewhere changes nothing: the add-on keeps its own address.
    tender(OTHER)
    check("Tender moved, the add-on stays", run(plugin.romm_game(5))["ok"], True)
    tender(URL)

    # The add-on's own address changed: the key is for the server it was paired with, and stays home.
    main._write_private(main.SERVER_FILE, {"url": OTHER, "insecure": False})
    before = len(romm.calls)
    check("moved", run(plugin.romm_game(5)), {"ok": False, "why": "moved"})
    check("moved status", run(plugin.romm_status()), "moved")
    check("moved: nothing sent", len(romm.calls), before)
    main._write_private(main.SERVER_FILE, {"url": URL, "insecure": False})
    check("back again", run(plugin.romm_status()), "linked")


def test_versions():
    cfg = os.path.join(HOME, main.RETROARCH_CFG)
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    with open(cfg, "w") as f:
        f.write('savefiles_in_content_dir = "false"\nsort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n')
    saves = os.path.join(HOME, "retrodeck", "saves", "nds")
    en, es = os.path.join(ROMS, "nds", "White (En).nds"), os.path.join(ROMS, "nds", "Blanca (Es).nds")
    en_save, es_save = os.path.join(saves, "White (En).srm"), os.path.join(saves, "Blanca (Es).srm")
    before = dict(romm.roms)
    romm.roms.clear()
    romm.roms[60] = rom(60, "White (En).nds", languages=["English"], regions=["USA", "Europe"],
                        sibling_roms=[{"id": 61, "is_main_sibling": False}], rom_user={"is_main_sibling": False})
    romm.roms[61] = rom(61, "Blanca (Es).nds", languages=["Spanish"], regions=["Spain"], ra_id=None,
                        sibling_roms=[{"id": 60, "is_main_sibling": False}], rom_user={"is_main_sibling": False})
    romm.roms[62] = rom(62, "Other.nds", languages=["English"])
    romm.roms[63] = rom(63, "Cube.iso", slug="ngc", sibling_roms=[{"id": 60}])  # another console, wrongly linked
    romm.content[60] = romm.content[61] = DATA

    # The library: one row per game with all its versions; the preferred language picks which.
    rows = {g["rom_id"]: g["versions"] for g in run(plugin.romm_library())["games"]}
    check("main version by default", (60 in rows, 61 in rows, rows.get(60), rows.get(62)), (True, False, [60, 61, 63], [62]))
    rows = {g["rom_id"]: g["versions"] for g in run(plugin.romm_library("Spanish"))["games"]}
    check("Spanish first", (61 in rows, 60 in rows, rows.get(61)), (True, False, [60, 61, 63]))
    check("a language none has", 60 in {g["rom_id"] for g in run(plugin.romm_library("Klingon"))["games"]}, True)

    # Its versions, as the panel lists them.
    check("take", run(plugin.game_take(700, 60))["installed"], False)
    got = run(plugin.game_versions(700))
    check("versions", [(v["rom_id"], v["languages"], v["regions"], v["achievements"], v["current"]) for v in got["versions"]],
          [(60, ["English"], ["USA", "Europe"], True, True), (61, ["Spanish"], ["Spain"], False, False)])
    check("versions of nothing", run(plugin.game_versions(999)), {"ok": False, "why": "game"})

    # Not installed: switched at once. Only a version of the same game on the same console.
    check("not a version of it", run(plugin.game_switch(700, 62)), {"ok": False, "why": "game"})
    romm.roms[60]["sibling_roms"].append({"id": 63})
    check("not on the same console", run(plugin.game_switch(700, 63)), {"ok": False, "why": "game"})
    check("nor listed", [v["rom_id"] for v in run(plugin.game_versions(700))["versions"]], [60, 61])
    romm.roms[60]["sibling_roms"].pop()
    done = run(plugin.game_switch(700, 61))
    check("switched at once", (done["ok"], done["options"], done["rom_id"], done["installed"], "pending" in done), (True, "61", 61, False, False))
    check("back", run(plugin.game_switch(700, 60))["options"], "60")

    # Installed with a save: the other version comes first; the save goes with it; the old file goes.
    run(plugin.game_download(700))
    check("English here", (wait(700)["installed"], os.path.isfile(en)), (True, True))
    os.makedirs(saves, exist_ok=True)
    with open(en_save, "wb") as f:
        f.write(b"my team")
    started = run(plugin.game_switch(700, 61))
    check("on its way", (started["ok"], started["pending"], started["rom_id"]), (True, 61, 60))
    state = wait(700)
    check("here, waiting", (state["job"]["state"], state["rom_id"], state["switching"], os.path.isfile(es), os.path.isfile(en)), ("ready", 60, 61, True, True))
    with open(main.LAUNCH_FILE) as f:
        check("still starts the old one", json.load(f), {"60": en})
    check("listed as waiting", run(plugin.game_pending()), {"700": 61})
    check("no second switch meanwhile", run(plugin.game_switch(700, 61)).get("why"), "busy")
    # Still playing the old version when it arrived: its save is what goes, once the game closes.
    with open(en_save, "wb") as f:
        f.write(b"my team")
    done = run(plugin.game_switch_finish(700))
    check("finished", (done["ok"], done["options"], done["save"], done["rom_id"], "switching" in done), (True, "61", "carried", 61, False))
    state = run(plugin.game_state(700))
    check("switched", (state["job"]["state"], state["rom_id"], state["installed"]), ("installed", 61, True))
    check("nothing waiting", run(plugin.game_pending()), {})
    check("finish twice", run(plugin.game_switch_finish(700))["why"], "game")
    check("files", (os.path.isfile(es), os.path.exists(en)), (True, False))
    with open(es_save, "rb") as f, open(en_save, "rb") as g:
        check("save carried, old one kept", (f.read(), g.read()), (b"my team", b"my team"))
    with open(main.LAUNCH_FILE) as f:
        check("launch list", json.load(f), {"61": es})
    check("what is known of the old save is not the new one's", main._read_games()["games"]["700"]["saves"], {})

    # Back again: the new version has its own save now, so neither is touched.
    with open(es_save, "wb") as f:
        f.write(b"played in Spanish")
    with open(en_save, "wb") as f:
        f.write(b"english later")
    run(plugin.game_switch(700, 60))
    wait(700)
    check("back, installed", run(plugin.game_switch_finish(700))["rom_id"], 60)
    with open(es_save, "rb") as f, open(en_save, "rb") as g:
        check("both saves as they were", (f.read(), g.read()), (b"played in Spanish", b"english later"))
    check("files back", (os.path.isfile(en), os.path.exists(es)), (True, False))

    # A file the add-on did not bring stays, and one already whole on the disk is used as it is.
    with open(es, "wb") as f:
        f.write(DATA)
    calls = len(romm.calls)
    now = run(plugin.game_switch(700, 61))
    check("already there: at once", (now["options"], now["installed"], now["removable"]), ("61", True, False))
    check("nothing downloaded", [c for c in romm.calls[calls:] if "/content/" in c[1]], [])
    check("old one ours: gone", os.path.exists(en), False)
    run(plugin.game_switch(700, 60))
    wait(700)
    check("back: brought", run(plugin.game_switch_finish(700))["rom_id"], 60)
    check("the one not ours stays", os.path.isfile(es), True)

    # Cancelled on the way: nothing changes.
    def slow(h):
        time.sleep(0.6)
        h.reply(500)
    romm.content[61] = slow
    os.remove(es)
    run(plugin.game_switch(700, 61))
    check("no second switch meanwhile", run(plugin.game_switch(700, 61)).get("why"), "busy")
    check("nor a save sync", run(plugin.save_sync(700))["state"], "busy")
    run(plugin.game_cancel(700))
    state = wait(700)
    check("stopped: still the old one", (state["rom_id"], state["installed"], os.path.isfile(en), os.path.exists(es)), (60, True, True, False))
    check("nothing pending", "switching" in main._read_games()["games"]["700"], False)
    romm.content[61] = DATA

    # Brought, then given up while waiting: the new file goes, the entry stays as it was.
    run(plugin.game_switch(700, 61))
    check("waiting again", wait(700)["job"]["state"], "ready")
    state = run(plugin.game_cancel(700))
    check("given up", (state["rom_id"], state.get("switching"), os.path.exists(es), os.path.isfile(en)), (60, None, False, True))
    # The save cannot be written: nothing changes and what was brought goes.
    run(plugin.game_switch(700, 61))
    wait(700)
    os.remove(es_save)  # so the save has to be written
    real = main._put_save
    main._put_save = lambda path, raw: (_ for _ in ()).throw(OSError("full"))
    try:
        failed = run(plugin.game_switch_finish(700))
    finally:
        main._put_save = real
    state = run(plugin.game_state(700))
    check("save not written: unchanged", (failed["ok"], failed["why"], state["rom_id"], state.get("switching"), os.path.exists(es), os.path.isfile(en)),
          (False, "disk", 60, None, False, True))

    for path in (en, es, en_save, es_save):
        if os.path.exists(path):
            os.remove(path)
    run(plugin.game_release(700))
    romm.roms.clear()
    romm.roms.update(before)


def test_collections():
    g = "eyJuYW1lIjogIlBsYXRmb3JtIn0="
    romm.collections = {
        "standard": [{"id": 3, "name": "  My   picks ", "rom_ids": [5, 30, 5, -1, "x", True]},
                     {"id": "4", "name": "id as text"}, {"id": 6, "name": ""}, "junk"],
        "smart": [{"id": 1, "name": "Unplayed", "rom_ids": [31]}],
        "virtual": {"genre": [{"id": g, "name": "Platform", "rom_ids": [5]}],
                    "franchise": [{"id": "bad\nid", "name": "Line"}, {"id": 9, "name": "number"}]},
    }
    got = run(plugin.romm_collections())
    check("collections ok", got["ok"], True)
    check("rows", [(c["key"], c["kind"], c["sort"], c["name"], c["roms"], c["on"]) for c in got["collections"]], [
        ("standard:3", "standard", "", "My picks", [5, 30], False),
        ("smart:1", "smart", "", "Unplayed", [31], False),
        ("virtual:" + g, "virtual", "genre", "Platform", [5], False)])
    check("asked each sort", sum(1 for c in romm.calls if c[1].startswith("/api/collections/virtual?type=")) >= 5, True)

    # Shown or not, kept on the Deck for this user only; a key that is not one of RomM's sorts is refused.
    check("shown", run(plugin.romm_collection_set("virtual:" + g, True)), {"ok": True})
    check("shown twice: once", run(plugin.romm_collection_set("virtual:" + g, True)), {"ok": True})
    check("another", run(plugin.romm_collection_set("standard:3", True)), {"ok": True})
    check("file private", mode(main.COLLECTIONS_FILE), 0o600)
    check("kept once", main._shown_collections(), ["virtual:" + g, "standard:3"])
    on = {c["key"]: c["on"] for c in run(plugin.romm_collections())["collections"]}
    check("on as kept", (on["standard:3"], on["smart:1"], on["virtual:" + g]), (True, False, True))
    check("hidden", run(plugin.romm_collection_set("standard:3", False)), {"ok": True})
    check("only that one", main._shown_collections(), ["virtual:" + g])
    for bad in ("standard:x", "smart:0", "virtual:", "virtual: \n", "other:1", 3, None, "standard:1" + "1" * 300):
        check("refused key %r" % (bad,), run(plugin.romm_collection_set(bad, True)), {"ok": False, "why": "collection"})
    check("not a real true", (run(plugin.romm_collection_set("smart:1", "yes")), "smart:1" in main._shown_collections()), ({"ok": True}, False))

    # One sort failing leaves the rest; the ones people make must come.
    romm.collections_down = {"smart", "genre"}
    got = run(plugin.romm_collections())
    check("partial", [c["key"] for c in got["collections"]], ["standard:3"])
    romm.collections_down = {"standard"}
    check("main list down", run(plugin.romm_collections()), {"ok": False, "why": "unreachable"})
    romm.collections_down = set()
    with open(main.COLLECTIONS_FILE, "w") as f:
        f.write("{not json")
    check("unreadable file: none shown", any(c["on"] for c in run(plugin.romm_collections())["collections"]), False)
    os.remove(main.COLLECTIONS_FILE)
    romm.collections = {"standard": [], "smart": [], "virtual": {}}


def test_cover():
    png = b"\x89PNG\r\n\x1a\n" + b"picture"
    romm.covers = {"big.png": png, "page.png": b"<html>sign in</html>", "huge.png": b"\x89PNG\r\n\x1a\n" + b"x" * main.COVER_MAX}
    romm.roms[20] = rom(20, "Cover.nds", path_cover_large="/assets/romm/resources/roms/20/cover/big.png?ts=1")
    got = run(plugin.game_cover(20))
    check("cover", (got["ok"], got["kind"], __import__("base64").b64decode(got["data"])), (True, "png", png))
    for name, path in (("not a picture", "/assets/romm/resources/roms/20/cover/page.png"), ("too big", "/assets/romm/resources/roms/20/cover/huge.png"),
                       ("missing", "/assets/romm/resources/roms/20/cover/none.png"), ("outside the covers", "/assets/romm/resources/roms/../../../api/users/me"), ("elsewhere", "/api/users/me"), ("another site", "https://example.org/x.png"), ("none", None)):
        romm.roms[20]["path_cover_large"] = path
        before = len(romm.calls)
        check("cover %s" % name, run(plugin.game_cover(20)), {"ok": False, "why": "cover"})
        if name in ("outside the covers", "elsewhere", "another site", "none"):
            check("cover %s: only the game was asked for" % name, len(romm.calls) - before, 1)
    # The other pictures: only RomM's own copies of ScreenScraper's media, and only those it has.
    jpg = b"\xff\xd8\xff" + b"fanart"
    romm.covers.update({"fanart.jpg": jpg, "logo.png": png})
    romm.roms[20]["ss_metadata"] = {
        "fanart_path": "roms/1/20/fanart/fanart.jpg", "logo_path": "/roms/1/20/logo/logo.png",
        "steamgrid_path": "roms/1/20/../../../../api/users/me",
        "fanart_url": "https://screenscraper.example/api2/mediaJeu.php?devid=x&devpassword=y",
    }
    before = len(romm.calls)
    got = run(plugin.game_pictures(20, ["hero", "logo", "wide", "nonsense"]))
    b64 = __import__("base64").b64decode
    check("pictures RomM keeps", (got["ok"], b64(got["hero"]["data"]), got["hero"]["kind"], b64(got["logo"]["data"]), got["logo"]["kind"], "wide" in got),
          (True, jpg, "jpg", png, "png", False))
    check("pictures: only RomM's own folder asked", sorted(c[1].split("?")[0] for c in romm.calls[before:]),
          ["/api/roms/20", "/assets/romm/resources/roms/1/20/fanart/fanart.jpg", "/assets/romm/resources/roms/1/20/logo/logo.png"])
    check("only the kinds asked for", sorted(k for k in run(plugin.game_pictures(20, ["logo"])) if k != "ok"), ["logo"])

    # The backgrounds to pick from without a SteamGridDB key: RomM's fanart only.
    got = run(plugin.hero_choices(20))
    check("hero choices without a key", (got["ok"], got["sgdb"], [(c["id"], c["source"], b64(c["data"]), c["kind"]) for c in got["choices"]]),
          (True, "off", [("romm", "romm", jpg, "jpg")]))
    got = run(plugin.hero_picture(20, "romm"))
    check("hero picked: RomM's", (got["ok"], b64(got["data"]), got["kind"]), (True, jpg, "jpg"))
    check("hero picked: one never offered", run(plugin.hero_picture(20, "sgdb:99")), {"ok": False, "why": "picture"})
    check("hero picked: nonsense", run(plugin.hero_picture(20, "../etc")), {"ok": False, "why": "picture"})
    check("hero choices: unknown game", run(plugin.hero_choices(999))["ok"], False)

    # A hero given from that fanart by an earlier version is told apart by its bytes, in every
    # Steam account and either Steam folder; any other hero is the player's and stays.
    grid = os.path.join(HOME, ".local", "share", "Steam", "userdata", "111", "config", "grid")
    other = os.path.join(HOME, ".steam", "steam", "userdata", "222", "config", "grid")
    os.makedirs(grid, exist_ok=True)
    os.makedirs(other, exist_ok=True)
    check("fanart hero: no hero", run(plugin.fanart_hero(777, 20)), {"ok": True, "same": False})
    with open(os.path.join(grid, "777_hero.jpg"), "wb") as f:
        f.write(b"\xff\xd8\xff" + b"the player's own")
    check("fanart hero: the player's", run(plugin.fanart_hero(777, 20)), {"ok": True, "same": False})
    with open(os.path.join(other, "777_hero.jpg"), "wb") as f:
        f.write(jpg)
    check("fanart hero: RomM's fanart", run(plugin.fanart_hero(777, 20)), {"ok": True, "same": True})
    check("fanart hero: another entry", run(plugin.fanart_hero(778, 20)), {"ok": True, "same": False})
    check("fanart hero: unknown game", run(plugin.fanart_hero(777, 999))["ok"], False)
    check("fanart hero: no game", run(plugin.fanart_hero(777, 0))["ok"], False)
    romm.roms[20].pop("ss_metadata")
    check("no media kept", run(plugin.game_pictures(20)), {"ok": True})
    check("unknown game", run(plugin.game_pictures(999))["ok"], False)
    got = run(plugin.game_launcher())
    check("launcher ready", (got["ok"], got["exe"], mode(main.LAUNCHER)), (True, main.LAUNCHER, 0o700))
    check("no games yet", run(plugin.game_list()), {})


# ── A pretend SteamGridDB ─────────────────────────────────────────────────────────
class SteamGridDB:
    """What the pretend SteamGridDB knows, and what it was asked."""

    def __init__(self):
        self.calls = []  # (path with query, bearer or None)
        self.found = {}  # search term -> autocomplete results
        self.art = {}  # (kind as in the path, game id) -> list of results
        self.images = {}  # file name -> bytes
        self.refuse = False  # answer 401 to every API call


sgdb = SteamGridDB()


class SgdbHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, status, raw, kind="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        from urllib.parse import unquote, urlsplit
        auth = self.headers.get("Authorization")
        sgdb.calls.append((self.path, auth))
        parts = urlsplit(self.path).path.strip("/").split("/")
        if parts[0] == "img":
            raw = sgdb.images.get(parts[-1])
            return self.send(200, raw, "image/png") if raw is not None else self.send(404, b"")
        if parts[:2] != ["api", "v2"]:
            return self.send(404, b"")
        if sgdb.refuse or auth != "Bearer " + SGDB_KEY:
            return self.send(401, json.dumps({"success": False, "errors": ["Unauthorized"]}).encode())
        if parts[2:4] == ["search", "autocomplete"]:
            return self.send(200, json.dumps({"success": True, "data": sgdb.found.get(unquote(parts[4]), [])}).encode())
        if len(parts) == 5 and parts[3] == "game":
            return self.send(200, json.dumps({"success": True, "page": 0, "total": 0, "limit": 50,
                                              "data": sgdb.art.get((parts[2], int(parts[4])), [])}).encode())
        self.send(404, b"")


SGDB_KEY = "0123456789abcdef0123456789abcdef"
sgdb_server = Quiet(("127.0.0.1", 0), SgdbHandler)
threading.Thread(target=sgdb_server.serve_forever, daemon=True).start()
SGDB_URL = "http://127.0.0.1:%d" % sgdb_server.server_port


def test_sgdb_shared():
    # The key travels through the RomM account's UI settings: typed once, picked up elsewhere.
    other = "Bb" * 16
    try:
        romm.ui_settings = {"theme": "dark"}
        romm.ui_puts.clear()
        main._sgdb_sync["at"] = 0.0
        check("typed: handed to RomM", run(plugin.sgdb_set_key(SGDB_KEY)), {"result": "ok", "shared": True})
        check("the rest of the settings kept", romm.ui_settings, {"theme": "dark", "vistazo_sgdb_key": SGDB_KEY})
        check("shared status", run(plugin.sgdb_status()), {"on": True, "shared": True})

        # Another device: no key of its own, it takes the account's on its next look.
        os.remove(main.SGDB_FILE)
        main._sgdb_sync["at"] = 0.0
        check("picked up from RomM", run(plugin.sgdb_status()), {"on": True, "shared": True})
        check("kept here for this user only", (main._sgdb_key(), oct(os.stat(main.SGDB_FILE).st_mode & 0o777)), (SGDB_KEY, "0o600"))

        # Not asked again until a while later.
        before = len(romm.calls)
        _ = run(plugin.sgdb_status())
        check("no look at the account each time", len(romm.calls) - before, 0)

        # A new key in the account replaces this one.
        romm.ui_settings["vistazo_sgdb_key"] = other
        main._sgdb_sync["at"] = 0.0
        _ = run(plugin.sgdb_status())
        check("new account key taken", main._sgdb_key(), other)

        # RomM's web page rewrote its settings without it: this device puts it back.
        romm.ui_settings = {"theme": "light"}
        main._sgdb_sync["at"] = 0.0
        _ = run(plugin.sgdb_status())
        check("put back", romm.ui_settings, {"theme": "light", "vistazo_sgdb_key": other})

        # Not shaped like a key: left alone and replaced by this device's.
        romm.ui_settings["vistazo_sgdb_key"] = "../../etc"
        main._sgdb_sync["at"] = 0.0
        _ = run(plugin.sgdb_status())
        check("odd value not taken", (main._sgdb_key(), romm.ui_settings["vistazo_sgdb_key"]), (other, other))

        # Taken away here: "" in the account, and other devices forget it on their next look.
        check("forget", run(plugin.sgdb_forget()), {"ok": True})
        check("taken away in RomM", romm.ui_settings, {"theme": "light", "vistazo_sgdb_key": ""})
        main._write_private(main.SGDB_FILE, {"key": other})
        main._sgdb_sync["at"] = 0.0
        check("forgotten on the other device", (run(plugin.sgdb_status()), os.path.exists(main.SGDB_FILE)), ({"on": False}, False))

        # RomM unreachable or refusing: kept here all the same.
        romm.ui_settings = None
        tokens = dict(romm.tokens)
        romm.tokens.clear()
        check("RomM refuses: kept here only", run(plugin.sgdb_set_key(SGDB_KEY)), {"result": "ok", "shared": False})
        check("status: not shared", run(plugin.sgdb_status()), {"on": True})
        romm.tokens.update(tokens)
        main._sgdb_sync["at"] = 0.0
        _ = run(plugin.sgdb_status())
        check("handed over once RomM takes it", romm.ui_settings, {"vistazo_sgdb_key": SGDB_KEY})
        check("the key only ever in the settings form", all("vistazo_sgdb_key" in put for put in romm.ui_puts), True)
    finally:
        romm.ui_settings = None
        try:
            os.remove(main.SGDB_FILE)
        except OSError:
            pass
        main._sgdb_sync.update(at=0.0, shared=False)


def test_steamgriddb():
    import base64
    # Pointed at the pretend one, as the pretend RomM is: the add-on never reaches the real site here.
    main.SGDB_API = SGDB_URL + "/api/v2"
    main.SGDB_IMAGES = (SGDB_URL + "/img/",)
    logged = []
    quiet = decky.logger
    decky.logger = types.SimpleNamespace(**{n: (lambda *a, **k: logged.append(repr((a, k)))) for n in ("info", "warning", "error", "debug")})
    try:
        png = b"\x89PNG\r\n\x1a\n" + b"sgdb"
        jpg = b"\xff\xd8\xff" + b"sgdb"
        sgdb.images = {"cover.png": png, "wide.jpg": jpg, "hero.png": png, "logo.png": png, "icon.png": png, "page.png": b"<html>"}
        romm.roms[21] = rom(21, "Grid.nds")
        romm.roms[22] = rom(22, "Twins.nds")
        romm.roms[23] = rom(23, "Kept.nds", path_cover_large="/assets/romm/resources/roms/23/cover/c.png",
                            ss_metadata={"fanart_path": "roms/1/23/fanart/f.jpg", "logo_path": "roms/1/23/logo/l.png"})
        img = lambda name: {"id": 1, "score": 0, "url": SGDB_URL + "/img/" + name, "thumb": SGDB_URL + "/img/" + name}
        sgdb.found = {
            "Game 21": [{"id": 5, "name": "Game 21: Deluxe", "verified": True}, {"id": 4, "name": "Game 21", "verified": True}],
            "Game 22": [{"id": 7, "name": "Game 22", "verified": True}, {"id": 8, "name": "GAME 22", "verified": False}],
            "Game 23": [{"id": 9, "name": "Game 23", "verified": True}],
        }
        for gid in (4, 7, 8, 9):
            sgdb.art.update({("grids", gid): [img("cover.png")], ("heroes", gid): [img("hero.png")],
                             ("logos", gid): [img("logo.png")], ("icons", gid): [img("icon.png")]})
        every = ["cover", "wide", "hero", "logo", "icon"]

        # Without a key nothing changes and nothing is asked, not even RomM.
        check("off without a key", run(plugin.sgdb_status()), {"on": False})
        before = len(romm.calls)
        check("no pictures without a key", run(plugin.sgdb_pictures(21, every)), {"ok": False, "why": "off"})
        check("no request without a key", (sgdb.calls, len(romm.calls) - before), ([], 0))

        # The key: kept for this user only, never handed back.
        for bad in ("", "short", "has spaces in it 0123456789abcdef", "x" * 300, None):
            check("not a key: %r" % (bad,), run(plugin.sgdb_set_key(bad)), {"result": "invalid"})
        check("still off", run(plugin.sgdb_status()), {"on": False})
        check("key kept", run(plugin.sgdb_set_key("  " + SGDB_KEY + "\n")), {"result": "ok", "shared": True})
        check("key file private", mode(main.SGDB_FILE), 0o600)
        check("on", run(plugin.sgdb_status()), {"on": True, "shared": True})

        # An exact name among others: that game, with its pictures; only the name went out.
        got = run(plugin.sgdb_pictures(21, every))
        check("all the pictures", (got["ok"], *((base64.b64decode(got[k]["data"]), got[k]["kind"]) for k in ("cover", "wide", "hero", "logo"))),
              (True, (png, "png"), (png, "png"), (png, "png"), (png, "png")))
        with open(got["icon"]["path"], "rb") as f:
            check("icon as a file", (f.read(), got["icon"]["path"].startswith(decky.DECKY_PLUGIN_RUNTIME_DIR)), (png, True))
        api = [p for p, _ in sgdb.calls if p.startswith("/api/")]
        check("searched by the name only", api[0], "/api/v2/search/autocomplete/Game%2021")
        check("asked the game found", sorted(p.split("?")[0] for p in api[1:]),
              ["/api/v2/grids/game/4", "/api/v2/grids/game/4", "/api/v2/heroes/game/4", "/api/v2/icons/game/4", "/api/v2/logos/game/4"])
        check("cover size asked", any("dimensions=600x900" in p for p in api), True)
        check("wide size asked", any("dimensions=920x430" in p for p in api), True)
        check("no adult or joke pictures", all("nsfw=false" in p and "humor=false" in p for p in api[1:]), True)
        check("key only to the API", {a for p, a in sgdb.calls if p.startswith("/api/")} == {"Bearer " + SGDB_KEY}
              and all(a is None for p, a in sgdb.calls if p.startswith("/img/")), True)
        check("key never to RomM", any(SGDB_KEY in str(c) for c in romm.calls), False)

        # Only the kinds asked for, and never one RomM keeps.
        sgdb.calls.clear()
        got = run(plugin.sgdb_pictures(21, ["hero", "nonsense"]))
        check("only the kinds asked for", sorted(k for k in got if k != "ok"), ["hero"])
        check("only its pictures asked", [p.split("?")[0] for p, _ in sgdb.calls if p.startswith("/api/v2/") and "search" not in p], ["/api/v2/heroes/game/4"])
        sgdb.calls.clear()
        check("RomM's pictures kept", run(plugin.sgdb_pictures(23, ["cover", "logo"])), {"ok": True})
        check("nothing asked for them", sgdb.calls, [])
        check("the hero comes from SteamGridDB even with RomM's fanart", sorted(k for k in run(plugin.sgdb_pictures(23, ["hero"])) if k != "ok"), ["hero"])
        check("what RomM lacks still comes", sorted(k for k in run(plugin.sgdb_pictures(23, ["cover", "wide"])) if k != "ok"), ["wide"])

        # Backgrounds to pick from: RomM's fanart first, then SteamGridDB's heroes, small; the one
        # picked comes whole, and only one that was offered for that game.
        fanart = b"\xff\xd8\xff" + b"fanart 23"
        romm.covers["f.jpg"] = fanart
        sgdb.images.update({"h1.jpg": jpg + b"1", "h1t.jpg": jpg + b"t1", "h2.png": png + b"2", "h2t.png": png + b"t2"})
        sgdb.art[("heroes", 9)] = [
            {"id": 31, "url": SGDB_URL + "/img/h1.jpg", "thumb": SGDB_URL + "/img/h1t.jpg"},
            {"id": 32, "url": SGDB_URL + "/img/h2.png", "thumb": SGDB_URL + "/img/h2t.png"},
            {"id": 33, "url": OTHER + "/img/h1.jpg", "thumb": OTHER + "/img/h1t.jpg"},
            {"id": 34, "url": SGDB_URL + "/img/page.png", "thumb": SGDB_URL + "/img/page.png"},
        ]
        sgdb.calls.clear()
        got = run(plugin.hero_choices(23))
        check("hero choices", (got["ok"], got["sgdb"], [(c["id"], base64.b64decode(c["data"]), c["kind"]) for c in got["choices"]]),
              (True, "on", [("romm", fanart, "jpg"), ("sgdb:31", jpg + b"t1", "jpg"), ("sgdb:32", png + b"t2", "png")]))
        check("hero choices: as many as offered", any("limit=%d" % main.HERO_CHOICES in p for p, _ in sgdb.calls), True)
        check("hero choices: only thumbnails fetched", sorted(p for p, _ in sgdb.calls if p.startswith("/img/")), ["/img/h1t.jpg", "/img/h2t.png", "/img/page.png"])
        got = run(plugin.hero_picture(23, "sgdb:32"))
        check("hero picked: SteamGridDB's, whole", (got["ok"], base64.b64decode(got["data"]), got["kind"]), (True, png + b"2", "png"))
        check("hero picked: from elsewhere never", run(plugin.hero_picture(23, "sgdb:33")), {"ok": False, "why": "picture"})
        check("hero picked: another game's", run(plugin.hero_picture(21, "sgdb:32")), {"ok": False, "why": "picture"})
        sgdb.refuse = True
        got = run(plugin.hero_choices(23))
        check("hero choices: key refused", (got["sgdb"], [c["id"] for c in got["choices"]]), ("refused", ["romm"]))
        sgdb.refuse = False
        _ = run(plugin.sgdb_set_key(SGDB_KEY))

        # When unsure, nothing: two games with that name, or none with it exactly.
        sgdb.calls.clear()
        check("two with the name: skipped", run(plugin.sgdb_pictures(22, every)), {"ok": True})
        check("no pictures asked for either", [p for p, _ in sgdb.calls if "/game/" in p], [])
        sgdb.found["Game 22"] = [{"id": 7, "name": "Game 22 II"}, {"id": 8, "name": "Game 2"}]
        check("no exact name: skipped", run(plugin.sgdb_pictures(22, every)), {"ok": True})
        check("names compared loosely", main._sgdb_match("Pokémon: Platinum", [{"id": 3, "name": "Pokemon Platinum"}, {"id": 2, "name": "Pokemon Pearl"}]), 3)
        check("nothing found", main._sgdb_match("Game", []), None)

        # Pictures that are not pictures, or from elsewhere, are left out.
        sgdb.art[("heroes", 4)] = [{"id": 1, "url": SGDB_URL + "/img/page.png"}]
        sgdb.art[("logos", 4)] = [{"id": 1, "url": OTHER + "/img/logo.png"}]
        before = len(romm.calls)
        check("bad pictures left out", run(plugin.sgdb_pictures(21, ["hero", "logo"])), {"ok": True})
        check("nothing fetched elsewhere", len(romm.calls) - before, 1)

        # A key SteamGridDB no longer takes: said so, and still never written anywhere.
        sgdb.refuse = True
        main._sgdb_ids.clear()
        check("refused", run(plugin.sgdb_pictures(21, every)), {"ok": False, "why": "refused"})
        check("refused status", run(plugin.sgdb_status()), {"on": True, "refused": True, "shared": True})
        sgdb.refuse = False
        check("a new key clears it", (run(plugin.sgdb_set_key(SGDB_KEY)), run(plugin.sgdb_status())), ({"result": "ok", "shared": True}, {"on": True, "shared": True}))

        # Forgotten: off again, and nothing asked.
        check("forget", run(plugin.sgdb_forget()), {"ok": True})
        check("key file gone", os.path.exists(main.SGDB_FILE), False)
        sgdb.calls.clear()
        check("off after forgetting", (run(plugin.sgdb_status()), run(plugin.sgdb_pictures(21, every)), sgdb.calls),
              ({"on": False}, {"ok": False, "why": "off"}, []))
        check("something was logged", bool(logged), True)
        check("key never in the logs", any(SGDB_KEY in line for line in logged), False)
    finally:
        decky.logger = quiet
        for rom_id in (21, 22, 23):
            romm.roms.pop(rom_id, None)


def test_launcher():
    nasty = os.path.join(ROMS, "nds", NASTY)
    with open(nasty, "wb") as f:
        f.write(b"rom")
    romm.roms[6] = rom(6, NASTY, files=[(NASTY, 3)])
    # What Tender's shortcut holds: its launcher, and the file inside quotes with \ and " escaped.
    escaped = nasty.replace("\\", "\\\\").replace('"', '\\"')
    before = {"exe": "/tender/bin/rom-launcher", "start_dir": "/tender/bin",
              "options": 'flatpak run net.retrodeck.retrodeck -e "%%EMULATOR%% %%ROM%%" "%s"' % escaped}
    got = run(plugin.game_take(600, 6, **before))
    check("taken", (got["ok"], got["options"], got["exe"], got["installed"], got["removable"]),
          (True, "6", main.LAUNCHER, True, False))
    check("launcher runnable, private", mode(main.LAUNCHER), 0o700)
    check("games file private", mode(main.GAMES_FILE), 0o600)
    check("launch list private", mode(main.LAUNCH_FILE), 0o600)
    with open(main.LAUNCH_FILE) as f:
        check("launch list", json.load(f), {"6": os.path.realpath(nasty)})
    check("listed", run(plugin.game_list()), {"600": 6})
    check("taking it twice changes nothing", run(plugin.game_take(600, 6, "x", "y", "z"))["ok"], True)

    # The launcher hands the file to flatpak as one argument, whatever its name holds.
    fake_bin = os.path.join(TMP, "bin")
    os.makedirs(fake_bin)
    seen = os.path.join(TMP, "flatpak-args.json")
    with open(os.path.join(fake_bin, "flatpak"), "w") as f:
        f.write("#!/usr/bin/python3\nimport json, sys\njson.dump(sys.argv[1:], open(%r, 'w'))\n" % seen)
    os.chmod(os.path.join(fake_bin, "flatpak"), 0o755)
    env = {"PATH": fake_bin + ":/usr/bin:/bin", "HOME": HOME}
    work = os.path.join(TMP, "cwd")
    os.makedirs(work)

    def launch(*args):
        return subprocess.run([main.LAUNCHER, *args], env=env, cwd=work, capture_output=True, text=True)

    done = launch("6")
    check("launcher ran", done.returncode, 0)
    with open(seen) as f:
        check("flatpak got the file as one argument", json.load(f),
              ["run", "net.retrodeck.retrodeck", os.path.realpath(nasty)])
    check("the name ran nothing", os.listdir(work), [])
    os.remove(seen)
    for bad in (["7"], ["6", "extra"], [], ["6; touch x"], ["../6"], ["-6"], ["٦"]):
        check("refused %r" % bad, launch(*bad).returncode, 2)
    # A list anyone else could write is not believed.
    os.chmod(main.LAUNCH_FILE, 0o666)
    check("loose list refused", launch("6").returncode, 2)
    os.chmod(main.LAUNCH_FILE, 0o600)
    check("flatpak never started for those", os.path.exists(seen), False)

    # A file outside RetroDECK's folders, or one that is not this game's, is not taken as installed.
    outside = os.path.join(TMP, "outside.nds")
    open(outside, "wb").close()
    romm.roms[7] = rom(7, "outside.nds")
    check("outside file ignored", run(plugin.game_take(700, 7, "e", "d", 'x "%s"' % outside))["installed"], False)
    romm.roms[8] = rom(8, "Another.nds")
    check("other game's file ignored", run(plugin.game_take(800, 8, "e", "d", 'x "%s"' % escaped))["installed"], False)
    check("same Steam game, another RomM game", run(plugin.game_take(800, 6))["why"], "game")

    # Undo: the shortcut's old values come back whole and the game leaves the launcher's list.
    check("given back", run(plugin.game_release(600)), {"ok": True, **before})
    with open(main.LAUNCH_FILE) as f:
        check("gone from the launch list", json.load(f), {})
    check("its file stays", os.path.isfile(nasty), True)
    check("no longer Vistazo's", run(plugin.game_state(600))["managed"], False)
    check("nothing to give back twice", run(plugin.game_release(600))["why"], "game")
    for appid in (700, 800):
        run(plugin.game_release(appid))
    os.remove(nasty)


def test_download():
    target = os.path.join(ROMS, "nds", "Game.nds")
    romm.content[5] = DATA
    check("take", run(plugin.game_take(500, 5))["installed"], False)
    check("download starts", run(plugin.game_download(500))["ok"], True)
    state = wait(500)
    check("downloaded", (state["job"]["state"], state["installed"], state["removable"]), ("installed", True, True))
    with open(target, "rb") as f:
        check("file whole", f.read() == DATA, True)
    check("nothing half-done left", leftovers(), [])
    with open(main.LAUNCH_FILE) as f:
        check("in the launch list", json.load(f), {"5": target})
    asked = [c for c in romm.calls if "/content/" in c[1]][-1]
    check("asked with Vistazo's key", asked, ("GET", "/api/roms/5/content/Game.nds", "tok-5"))

    # Removing takes only what Vistazo brought.
    neighbour = os.path.join(ROMS, "nds", "Neighbour.nds")
    open(neighbour, "wb").close()
    removed = run(plugin.game_remove(500))
    check("removed", (removed["ok"], removed["installed"], os.path.exists(target)), (True, False, False))
    check("neighbour stays", os.path.isfile(neighbour), True)
    with open(main.LAUNCH_FILE) as f:
        check("out of the launch list", json.load(f), {})
    check("nothing to remove twice", run(plugin.game_remove(500))["why"], "not-ours")

    # Cut short: RomM announces more than it sends.
    def short(h):
        h.send_response(200)
        h.send_header("Content-Length", str(len(DATA)))
        h.end_headers()
        h.wfile.write(DATA[:1000])
        h.wfile.flush()
        h.connection.close()

    romm.content[5] = short
    run(plugin.game_download(500))
    state = wait(500)
    check("cut short", (state["job"]["state"], state["installed"]), ("unreachable", False))
    check("cut short keeps what came", (os.path.exists(target), leftovers(), os.path.getsize(target + ".part")),
          (False, ["Game.nds.part"], 1000))
    # Asked again, it goes on from there.
    romm.content[5] = DATA
    romm.ranges_asked.clear()
    run(plugin.game_download(500))
    check("went on", wait(500)["job"]["state"], "installed")
    check("only the rest asked for", romm.ranges_asked, ["bytes=1000-"])
    with open(target, "rb") as f:
        check("went on whole", f.read() == DATA, True)
    check("nothing half-done after", leftovers(), [])
    run(plugin.game_remove(500))

    # A whole answer of the wrong size is not the game either.
    romm.content[5] = DATA[:-1]
    run(plugin.game_download(500))
    check("wrong size", wait(500)["job"]["state"], "unreachable")
    check("wrong size leaves nothing", (os.path.exists(target), leftovers()), (False, []))

    # Cancelled half way.
    going = threading.Event()

    def slow(h):
        h.send_response(200)
        h.send_header("Content-Length", str(len(DATA)))
        h.end_headers()
        try:
            for i in range(0, len(DATA), 1000):
                h.wfile.write(DATA[i:i + 1000])
                h.wfile.flush()
                going.set()
                time.sleep(0.005)
        except OSError:
            pass

    main.CHUNK = 4096
    romm.content[5] = slow
    run(plugin.game_download(500))
    going.wait(5)
    check("busy while downloading", run(plugin.game_release(500))["why"], "busy")
    check("no removing while downloading", run(plugin.game_remove(500))["why"], "busy")
    run(plugin.game_cancel(500))
    check("cancelled", wait(500)["job"]["state"], "cancelled")
    check("cancelled leaves nothing", (os.path.exists(target), leftovers()), (False, []))
    main.CHUNK = 1024 * 1024

    # RomM's own sum of the file is checked: the right size with the wrong bytes is not the game.
    import hashlib
    romm.roms[5]["files"][0]["sha1_hash"] = hashlib.sha1(DATA).hexdigest().upper()
    romm.content[5] = bytes(len(DATA))
    run(plugin.game_download(500))
    check("wrong bytes", wait(500)["job"]["state"], "damaged")
    check("wrong bytes leave nothing", (os.path.exists(target), leftovers()), (False, []))
    romm.content[5] = DATA
    run(plugin.game_download(500))
    check("right bytes", wait(500)["job"]["state"], "installed")
    run(plugin.game_remove(500))
    # A packed file's sum is of what is inside it: not compared.
    romm.roms[12] = rom(12, "Packed.zip", files=[("Packed.zip", len(DATA))])
    romm.roms[12]["files"][0]["md5_hash"] = "0" * 32
    romm.content[12] = DATA
    run(plugin.game_take(1200, 12))
    run(plugin.game_download(1200))
    check("packed file kept as it is", wait(1200)["job"]["state"], "installed")
    run(plugin.game_remove(1200))
    run(plugin.game_release(1200))

    # A download cut off by a restart leaves a half-done file: written down, and cleared at the next start.
    going.clear()
    main.CHUNK = 4096
    romm.content[5] = slow
    run(plugin.game_download(500))
    going.wait(5)
    with open(main.GAMES_FILE) as f:
        check("half-done file written down", json.load(f)["games"]["500"].get("part"), target + ".part")
    stale = os.path.join(TMP, "stale.part")
    shutil.copy(target + ".part", stale)
    run(plugin.game_cancel(500))
    wait(500)
    with open(main.GAMES_FILE) as f:
        check("nothing half-done written down", "part" in json.load(f)["games"]["500"], False)
    main.CHUNK = 1024 * 1024
    # As a restart leaves it: the file there and the note of it.
    shutil.move(stale, target + ".part")
    main._note_part(500, target + ".part")
    main._jobs.clear()
    run(plugin._main())
    check("cleared at the start", (leftovers(), "part" in main._read_games()["games"]["500"]), ([], False))
    # A note pointing anywhere else removes nothing.
    main._note_part(500, neighbour)
    run(plugin._main())
    check("only a half-done file of its own", os.path.isfile(neighbour), True)

    # Another program put its own copy where Vistazo's was: not Vistazo's to remove any more.
    run(plugin.game_download(500))
    check("downloaded again", wait(500)["job"]["state"], "installed")
    os.remove(target)
    with open(target, "wb") as f:
        f.write(DATA)
    os.utime(target, ns=(1, 1))
    check("removal goes through", run(plugin.game_remove(500))["installed"], False)
    check("the other program's copy stays", os.path.isfile(target), True)
    os.remove(target)

    # Taking a game whose file is already whole in its console's folder: installed from the start,
    # and never Vistazo's to remove.
    romm.roms[13] = rom(13, "Here.nds")
    here = os.path.join(ROMS, "nds", "Here.nds")
    with open(here, "wb") as f:
        f.write(DATA)
    got = run(plugin.game_take(1300, 13))
    check("already there", (got["installed"], got["removable"]), (True, False))
    check("not removable", run(plugin.game_remove(1300))["why"], "not-ours")
    run(plugin.game_release(1300))
    with open(here, "wb") as f:
        f.write(DATA[:-5])
    check("a partial file does not count", run(plugin.game_take(1300, 13))["installed"], False)
    run(plugin.game_release(1300))
    os.remove(here)

    # A file already there: used when whole (and never Vistazo's to remove), refused otherwise.
    romm.content[5] = DATA
    with open(target, "wb") as f:
        f.write(b"someone else's")
    run(plugin.game_download(500))
    check("in the way", wait(500)["job"]["state"], "exists")
    with open(target, "rb") as f:
        check("left as it was", f.read(), b"someone else's")
    with open(target, "wb") as f:
        f.write(DATA)
    run(plugin.game_download(500))
    state = wait(500)
    check("already whole", (state["job"]["state"], state["installed"], state["removable"]), ("installed", True, False))
    check("not Vistazo's to remove", run(plugin.game_remove(500))["why"], "not-ours")
    check("still there", os.path.isfile(target), True)
    os.remove(target)
    run(plugin.game_release(500))

    # A name with folders in it lands as one name inside the system's folder.
    romm.roms[9] = rom(9, "../../evil.nds", files=[("../../../evil.nds", len(DATA))])
    romm.content[9] = DATA
    run(plugin.game_take(900, 9))
    run(plugin.game_download(900))
    check("odd name downloaded", wait(900)["job"]["state"], "installed")
    check("kept inside", os.path.isfile(os.path.join(ROMS, "nds", "evil.nds")), True)
    check("nothing outside", [n for n in os.listdir(HOME) + os.listdir(os.path.dirname(ROMS)) if "evil" in n], [])
    run(plugin.game_remove(900))
    run(plugin.game_release(900))

    # A system Vistazo has no folder for, and a full disk.
    romm.roms[10] = rom(10, "Game.bin", slug="atari2600")
    run(plugin.game_take(1000, 10))
    run(plugin.game_download(1000))
    check("unknown system", wait(1000)["job"]["state"], "system")
    run(plugin.game_release(1000))
    usage = shutil.disk_usage
    main.shutil.disk_usage = lambda p: types.SimpleNamespace(free=1000)
    run(plugin.game_take(500, 5))
    run(plugin.game_download(500))
    check("no room", wait(500)["job"]["state"], "space")
    main.shutil.disk_usage = usage
    check("no room leaves nothing", (os.path.exists(target), leftovers()), (False, []))

    # The key refused half way through: forgotten, and the page is told.
    romm.refuse.add(5)
    run(plugin.game_download(500))
    check("refused", wait(500)["job"]["state"], "refused")
    check("refused key forgotten", run(plugin.romm_status()), "unlinked")
    romm.refuse.clear()
    romm.codes["ABCDEFGH"] = (6, GAMES)
    check("paired again", pair("ABCDEFGH", True)["result"], "ok")
    run(plugin.game_release(500))


def archive(members):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        for name, data in members:
            z.writestr(name, data)
    return raw.getvalue()


def test_archive():
    os.makedirs(os.path.join(ROMS, "ps2"), exist_ok=True)
    files = [("Disc 1.iso", 4000), ("Disc 2.iso", 6000)]
    romm.roms[11] = rom(11, "Two Discs", files=files, slug="ps2")
    romm.content[11] = archive([("Disc 1.iso", b"a" * 4000), ("Disc 2.iso", b"b" * 6000), ("Two Discs.m3u", b"Disc 1.iso\nDisc 2.iso\n")])
    run(plugin.game_take(1100, 11))
    run(plugin.game_download(1100))
    state = wait(1100)
    folder = os.path.join(ROMS, "ps2", "Two Discs")
    check("archive unpacked", (state["job"]["state"], sorted(os.listdir(folder))), ("installed", ["Disc 1.iso", "Disc 2.iso", "Two Discs.m3u"]))
    with open(main.LAUNCH_FILE) as f:
        check("starts from the playlist", json.load(f), {"11": os.path.join(folder, "Two Discs.m3u")})
    check("archive itself gone", [n for n in os.listdir(os.path.join(ROMS, "ps2")) if n != "Two Discs"], [])
    stray = os.path.join(folder, "notes.txt")
    open(stray, "wb").close()
    run(plugin.game_remove(1100))
    check("only Vistazo's files removed", os.listdir(folder), ["notes.txt"])
    os.remove(stray)
    os.rmdir(folder)

    # Members that would land outside are refused, and nothing of the archive stays.
    for bad in ("../escape.iso", "/abs.iso", "a/../../escape.iso"):
        romm.content[11] = archive([("Disc 1.iso", b"a" * 10), (bad, b"x")])
        run(plugin.game_download(1100))
        check("bad member %s" % bad, wait(1100)["job"]["state"], "archive")
        check("nothing kept", os.listdir(os.path.join(ROMS, "ps2")), [])
    check("nothing escaped", [n for n in os.listdir(ROMS) + os.listdir(os.path.dirname(ROMS)) if "escape" in n], [])
    romm.content[11] = b"not an archive"
    run(plugin.game_download(1100))
    check("not an archive", wait(1100)["job"]["state"], "archive")
    check("nothing kept", os.listdir(os.path.join(ROMS, "ps2")), [])
    run(plugin.game_release(1100))


def test_move_in():
    # What Vistazo itself used to keep moves here once, and nothing of it stays behind.
    old = os.path.join(decky.DECKY_HOME, "settings", "Vistazo")
    os.makedirs(old)
    with open(os.path.join(old, "romm.json"), "w") as f:
        json.dump({"url": "http://127.0.0.1:1", "token": "from-vistazo", "id": 9}, f)
    with open(os.path.join(old, "games.json"), "w") as f:
        json.dump({"version": 1, "games": {}}, f)
    run(plugin._main())
    with open(main.KEY_FILE) as f:
        check("key moved in", json.load(f)["token"], "from-vistazo")
    check("key private", mode(main.KEY_FILE), 0o600)
    check("nothing left at Vistazo's", os.listdir(old), [])
    # A key already here is never replaced by one left over there.
    with open(os.path.join(old, "romm.json"), "w") as f:
        json.dump({"url": "x", "token": "stale", "id": 1}, f)
    run(plugin._main())
    with open(main.KEY_FILE) as f:
        check("own key kept", json.load(f)["token"], "from-vistazo")
    check("the leftover is removed", os.listdir(old), [])
    os.remove(main.KEY_FILE)
    os.remove(main.GAMES_FILE)
    # And what it left in Vistazo's data folder goes when asked, and nothing else there.
    data = os.path.join(decky.DECKY_HOME, "data", "Vistazo")
    os.makedirs(data)
    for name in ("vistazo-launch", "launch.json", "romm-kept.json", "something-of-vistazo"):
        open(os.path.join(data, name), "w").close()
    check("tidied", run(plugin.game_tidy()), {"ok": True})
    check("only its own leftovers", os.listdir(data), ["something-of-vistazo"])


def test_bios():
    import hashlib
    bios = os.path.join(HOME, "retrodeck", "bios")
    os.makedirs(bios, exist_ok=True)

    def firmware(fid, name, data, platform_id=1, **more):
        f = {"id": fid, "platform_id": platform_id, "file_name": name, "file_size_bytes": len(data),
             "md5_hash": hashlib.md5(data).hexdigest().upper(), "missing_from_fs": False, "data": data, **more}
        romm.firmware.append(f)
        return f

    romm.roms[50] = rom(50, "Bios.nds", platform_id=1)
    with open(os.path.join(ROMS, "nds", "Bios.nds"), "wb") as f:
        f.write(DATA)
    run(plugin.game_take(5000, 50))
    check("RomM has none for it", run(plugin.bios_state(5000)), {"state": "none", "files": 0, "missing": []})
    firmware(1, "bios7.bin", b"seven" * 100)
    firmware(2, "bios9.bin", b"nine" * 100)
    firmware(3, "firmware.bin", b"fw" * 100)
    firmware(4, "other-console.bin", b"x", platform_id=2)
    firmware(5, "gone.bin", b"y", missing_from_fs=True)
    firmware(6, "../../escape.bin", b"z" * 10)
    firmware(7, "stray.bin", b"s", platform_id=9, stray=True)
    with open(os.path.join(bios, "BIOS7.BIN"), "wb") as f:
        f.write(b"seven" * 100)
    os.makedirs(os.path.join(bios, "melonDS DS"))
    with open(os.path.join(bios, "melonDS DS", "firmware.bin"), "wb") as f:
        f.write(b"fw" * 100)
    got = run(plugin.bios_state(5000))
    check("what is missing", (got["state"], got["files"], sorted(got["missing"])), ("missing", 4, ["bios9.bin", "escape.bin"]))
    # A file of the right name and the wrong size is not the one RomM has; it is never written over.
    with open(os.path.join(bios, "bios9.bin"), "wb") as f:
        f.write(b"someone's own")
    got = run(plugin.bios_fetch(5000))
    check("in the way: left alone", (got["state"], got["why"], open(os.path.join(bios, "bios9.bin"), "rb").read()), ("missing", "exists", b"someone's own"))
    check("the odd name came as one name, inside", os.path.isfile(os.path.join(bios, "escape.bin")), True)
    check("nothing outside", [n for n in os.listdir(HOME) + os.listdir(os.path.dirname(bios)) if "escape" in n], [])
    os.remove(os.path.join(bios, "bios9.bin"))
    # RomM's sum is checked: the right size with the wrong bytes is not kept.
    romm.firmware[1]["data"] = b"NINE" * 100
    got = run(plugin.bios_fetch(5000))
    check("wrong bytes", (got["state"], got["why"], os.path.exists(os.path.join(bios, "bios9.bin"))), ("missing", "damaged", False))
    check("nothing half-done", [n for n in os.listdir(bios) if n.endswith(".part")], [])
    romm.firmware[1]["data"] = b"nine" * 100
    got = run(plugin.bios_fetch(5000))
    check("brought", (got, open(os.path.join(bios, "bios9.bin"), "rb").read()), ({"state": "ok", "files": 4, "missing": []}, b"nine" * 100))
    check("asked with the key, from RomM", [c for c in romm.calls if "/api/firmware/2/content/" in c[1]][-1][2].startswith("tok-"), True)
    # A console with a folder of its own, and one whose files are not handled yet.
    os.makedirs(os.path.join(ROMS, "ps2"), exist_ok=True)
    romm.roms[51] = rom(51, "Bios.iso", slug="ps2", platform_id=3)
    with open(os.path.join(ROMS, "ps2", "Bios.iso"), "wb") as f:
        f.write(DATA)
    firmware(8, "scph.bin", b"ps2" * 50, platform_id=3)
    run(plugin.game_take(5100, 51))
    # PS2's go where PCSX2 is told to look: RetroDECK's BIOS folder unless its settings say otherwise.
    check("PS2: the BIOS folder itself", (run(plugin.bios_fetch(5100))["state"], os.path.isfile(os.path.join(bios, "scph.bin"))), ("ok", True))
    os.remove(os.path.join(bios, "scph.bin"))
    ini = os.path.join(HOME, ".var", "app", "net.retrodeck.retrodeck", "config", "PCSX2", "inis", "PCSX2.ini")
    os.makedirs(os.path.dirname(ini), exist_ok=True)
    with open(ini, "w") as f:
        f.write("[Folders]\nBios = %s\nSnapshots = /x\n" % os.path.join(bios, "pcsx2", "bios"))
    check("PS2: where PCSX2 looks", (run(plugin.bios_fetch(5100))["state"], os.path.isfile(os.path.join(bios, "pcsx2", "bios", "scph.bin"))), ("ok", True))
    os.remove(os.path.join(bios, "pcsx2", "bios", "scph.bin"))
    with open(ini, "w") as f:
        f.write("[Folders]\nBios = %s\n" % os.path.join(TMP, "pcsx2-own"))
    check("PS2: never outside RetroDECK's folder", (run(plugin.bios_fetch(5100))["state"], os.path.isfile(os.path.join(bios, "scph.bin")),
                                                     os.path.exists(os.path.join(TMP, "pcsx2-own"))), ("ok", True, False))
    os.remove(ini)
    for appid in (5000, 5100):
        run(plugin.game_release(appid))
    os.remove(os.path.join(ROMS, "nds", "Bios.nds"))
    os.remove(os.path.join(ROMS, "ps2", "Bios.iso"))



def test_bios_all():
    import hashlib
    bios = os.path.join(HOME, "retrodeck", "bios")
    shutil.rmtree(bios)
    os.makedirs(bios)
    romm.firmware = []

    def firmware(fid, name, data, platform_id):
        romm.firmware.append({"id": fid, "platform_id": platform_id, "file_name": name, "file_size_bytes": len(data),
                              "md5_hash": hashlib.md5(data).hexdigest(), "missing_from_fs": False, "data": data})

    def platform(pid, slug, name, roms, files, fs_slug=None):
        romm.platforms.append({"id": pid, "slug": slug, "fs_slug": fs_slug or slug, "display_name": name,
                               "rom_count": roms, "firmware_count": files})

    platform(10, "ps", "PlayStation", 5, 2, fs_slug="psx")
    firmware(20, "scph5501.bin", b"psx-us" * 100, 10)
    firmware(21, "scph1001.bin", b"psx-old" * 100, 10)
    platform(11, "dc", "Dreamcast", 1, 2)
    firmware(22, "dc_boot.bin", b"boot" * 100, 11)
    firmware(23, "dc_flash.bin", b"flash" * 100, 11)
    platform(12, "saturn", "Saturn", 0, 1)  # no games: not asked
    firmware(24, "sega_101.bin", b"sat" * 100, 12)
    platform(13, "ngc", "GameCube", 3, 1)  # its folder in RetroDECK is not known
    firmware(25, "IPL.bin", b"gc" * 100, 13)
    platform(14, "segacd", "Mega-CD", 2, 0)  # RomM has no BIOS for it
    platform(15, "tg16", "PC Engine", 2, 1)  # the same file as another console's, already brought
    firmware(26, "scph5501.bin", b"psx-us" * 100, 15)
    with open(os.path.join(bios, "scph1001.bin"), "wb") as f:
        f.write(b"psx-old" * 100)
    romm.firmware[3]["data"] = b"FLASH" * 100  # RomM's sum will not match
    got = run(plugin.bios_all())
    check("all consoles: one failed", got, {"state": "missing", "consoles": 3, "files": 5, "brought": 3,
                                            "failed": ["dc_flash.bin"], "unknown": ["GameCube"], "why": "damaged"})
    check("progress at the end", run(plugin.bios_all_state()), {"state": "missing", "done": 4, "total": 4, "file": "scph5501.bin"})
    check("each in RetroDECK's folder for it", (open(os.path.join(bios, "scph5501.bin"), "rb").read(),
                                                os.path.isfile(os.path.join(bios, "dc", "dc_boot.bin")),
                                                os.path.exists(os.path.join(bios, "dc", "dc_flash.bin"))), (b"psx-us" * 100, True, False))
    check("not asked: consoles without games or BIOS", [c[1] for c in romm.calls if "platform_id=12" in c[1] or "platform_id=14" in c[1]], [])
    check("nothing half-done", [n for _, _, fs in os.walk(bios) for n in fs if "vistazo-part" in n], [])
    check("the one already there, not asked for", [c for c in romm.calls if "/api/firmware/21/content/" in c[1]], [])
    romm.firmware[3]["data"] = b"flash" * 100
    check("again: the rest", run(plugin.bios_all()), {"state": "ok", "consoles": 3, "files": 5, "brought": 1, "failed": [], "unknown": ["GameCube"]})
    check("and again: nothing to bring", (run(plugin.bios_all())["brought"], run(plugin.bios_all_state())), (0, {"state": "ok", "done": 0, "total": 0, "file": ""}))
    main._bios_all_job["state"] = "running"
    check("one at a time", run(plugin.bios_all()), {"state": "busy"})
    main._bios_all_job["state"] = "ok"
    romm.platforms_down = 500
    check("RomM not answering", run(plugin.bios_all()), {"state": "unreachable"})
    romm.platforms_down = 403
    check("RomM refusing", run(plugin.bios_all()), {"state": "refused"})
    romm.platforms_down = 0
    romm.platforms, romm.firmware = [], []


def test_saves():
    cfg = os.path.join(HOME, main.RETROARCH_CFG)
    os.makedirs(os.path.dirname(cfg))

    def flags(content="true", core="false", beside="false"):
        with open(cfg, "w") as f:
            f.write('savefile_directory = "~/retrodeck/saves"\nsavefiles_in_content_dir = "%s"\n'
                    'sort_savefiles_by_content_enable = "%s"\nsort_savefiles_enable = "%s"\n' % (beside, content, core))

    saves = os.path.join(HOME, "retrodeck", "saves")
    save = os.path.join(saves, "nds", "Game.srm")
    copies = os.path.join(saves, "nds", main.SAVE_COPIES_DIR)
    rom_file = os.path.join(ROMS, "nds", "Game.nds")
    romm.roms[5] = rom(5, "Game.nds")
    with open(rom_file, "wb") as f:
        f.write(DATA)

    def write(data, path=save):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def read(path=save):
        with open(path, "rb") as f:
            return f.read()

    def sync(choice=""):
        return run(plugin.save_sync(500, choice))

    def kept_copies():
        return sorted(os.listdir(copies)) if os.path.isdir(copies) else []

    # Where the save is: nowhere to look without RetroArch's settings, or for a game not here.
    check("no game", sync()["state"], "game")
    check("taken, installed", run(plugin.game_take(500, 5))["installed"], True)
    check("no settings to read", sync()["state"], "layout")
    flags(beside="true")
    check("saves beside the games are not handled", sync()["state"], "layout")
    flags()
    check("nothing anywhere yet", (sync()["state"], romm.uploads), ("none", []))

    # A save here and none in RomM: sent, as a version in the slot Tender uses too.
    write(b"first save")
    check("sent", sync()["state"], "sent")
    q, name = romm.uploads[-1]
    check("sent how", (q["rom_id"], q["emulator"], q["slot"], q["autocleanup"], q["autocleanup_limit"], name),
          (["5"], ["retroarch-melondsds"], ["autosave"], ["true"], ["10"], "Game.srm"))
    check("RomM has it", romm.saves[-1]["data"], b"first save")
    with open(main.GAMES_FILE) as f:
        record = json.load(f)["games"]["500"]["saves"]
    check("remembered", (record["state"], record["main"]["path"], record["main"]["server_id"]), ("sent", save, romm.saves[-1]["id"]))
    check("games file still private", mode(main.GAMES_FILE), 0o600)
    check("nothing to do", (sync()["state"], len(romm.uploads)), ("synced", 1))
    check("known without asking", run(plugin.save_state(500)), {"state": "synced", "changed": False})

    # Played: the save changed here, RomM's did not. Sent as a new version.
    write(b"second save, after playing")
    check("changed here", run(plugin.save_state(500))["changed"], True)
    check("sent again", (sync()["state"], len(romm.uploads), romm.saves[-1]["data"]), ("sent", 2, b"second save, after playing"))

    # Played elsewhere: RomM has a newer one and the one here did not change. A copy first, then brought.
    add_save(5, b"from another device", "Game.srm")
    check("brought", sync()["state"], "brought")
    check("the save is RomM's", read(), b"from another device")
    check("one copy of what was here", len(kept_copies()), 1)
    check("the copy is what was here", read(os.path.join(copies, kept_copies()[0])), b"second save, after playing")
    check("no half-written file", [n for n in os.listdir(os.path.dirname(save)) if "part" in n], [])
    check("nothing sent for that", len(romm.uploads), 2)

    # Both changed: nothing is touched until the player says.
    write(b"played here")
    add_save(5, b"played there", "Game.srm")
    got = sync()
    check("conflict", (got["state"], got["deck"] > 0, got["romm"] > 0), ("conflict", True, True))
    check("conflict touches nothing", (read(), len(kept_copies()), len(romm.uploads)), (b"played here", 1, 2))
    check("still a conflict", sync()["state"], "conflict")
    check("conflict remembered", run(plugin.save_state(500))["state"], "conflict")
    check("keep RomM's", sync("romm")["state"], "brought")
    check("RomM's is here, mine is in a copy", (read(), len(kept_copies())), (b"played there", 2))
    check("my save is among the copies", b"played here" in [read(os.path.join(copies, n)) for n in kept_copies()], True)
    write(b"played here again")
    add_save(5, b"played there again", "Game.srm")
    check("conflict again", sync()["state"], "conflict")
    check("keep the Deck's", (sync("deck")["state"], romm.saves[-1]["data"], read()), ("sent", b"played here again", b"played here again"))
    check("an odd choice chooses nothing", sync("delete everything")["state"], "synced")

    # A save cut short on the way is not put in place; the one here stays (and has its copy).
    add_save(5, b"a newer one from elsewhere", "Game.srm")
    romm.short_save = True
    check("cut short", sync()["state"], "unreachable")
    check("mine untouched", read(), b"played here again")
    check("no half-written file", [n for n in os.listdir(os.path.dirname(save)) if "part" in n], [])
    romm.short_save = False

    # When no copy can be made, nothing is replaced.
    shutil.rmtree(copies)
    write(b"not a folder", copies)
    check("no copy, no replacing", sync()["state"], "disk")
    check("mine untouched", read(), b"played here again")
    os.remove(copies)
    check("with a copy it goes through", (sync()["state"], read(), len(kept_copies())), ("brought", b"a newer one from elsewhere", 1))

    # Only a few copies are kept: the oldest go.
    for i in range(main.SAVE_COPIES + 3):
        add_save(5, b"version %d" % i, "Game.srm")
        check("brought %d" % i, sync()["state"], "brought")
        time.sleep(0.01)
    check("copies kept", len(kept_copies()), main.SAVE_COPIES)

    # Another game whose name starts the same keeps its own copies.
    other = os.path.join(copies, "Game_Two_20260101_000000.srm")
    write(b"another game's copy", other)
    add_save(5, b"one more", "Game.srm")
    check("brought once more", sync()["state"], "brought")
    check("the other game's copy stays", os.path.isfile(other), True)
    os.remove(other)

    # A link where the save should be is left alone.
    real = read()
    os.remove(save)
    target = os.path.join(TMP, "linked-save.srm")
    write(b"what the link points at", target)
    os.symlink(target, save)
    add_save(5, b"newer than the link", "Game.srm")
    check("a link is not handled", (sync()["state"], read(target), os.path.islink(save)), ("layout", b"what the link points at", True))
    os.remove(save)
    romm.saves.pop()
    write(real)
    check("back in step", sync()["state"], "synced")

    # RomM files a save under a name of its own (tags, dates): it is this game's all the same.
    add_save(5, b"under RomM's own name", "Game (Spain) [2026-11-13 10-00-00].srm")
    check("another name, same game", (sync()["state"], read()), ("brought", b"under RomM's own name"))

    # A save here older than every copy (brought from elsewhere with its old date): its copy is
    # the one just made and stays, whatever its date.
    write(b"an old save put here by hand")
    os.utime(save, (946684800, 946684800))
    add_save(5, b"newer in RomM", "Game.srm")
    check("both differ", sync()["state"], "conflict")
    check("RomM's taken", (sync("romm")["state"], read()), ("brought", b"newer in RomM"))
    check("the old save is among the copies", b"an old save put here by hand" in [read(os.path.join(copies, n)) for n in kept_copies()], True)
    check("still only a few copies", len(kept_copies()), main.SAVE_COPIES)

    # The save here changes while RomM's is on its way (the game was started): nothing is replaced.
    add_save(5, b"another newer one", "Game.srm")
    romm.meanwhile = lambda: write(b"written by the game meanwhile")
    check("changed meanwhile", (sync()["state"], read()), ("busy", b"written by the game meanwhile"))
    romm.meanwhile = None
    check("then it is a conflict, untouched", (sync()["state"], read()), ("conflict", b"written by the game meanwhile"))
    check("settled", sync("deck")["state"], "sent")

    # A try that fails costs no copy: the ones kept stay as they were.
    before = kept_copies()
    add_save(5, b"will not arrive", "Game.srm")
    romm.short_save = True
    for _ in range(3):
        check("failed try", sync()["state"], "unreachable")
    check("copies as they were", kept_copies(), before)
    romm.short_save = False
    romm.saves.pop()

    # A save RomM says nothing checkable about (no size, no sum) is not taken; one whose bytes
    # do not match its sum neither.
    add_save(5, b"unchecked", "Game.srm", file_size_bytes=0, content_hash="")
    mine = read()
    check("nothing to check it against", (sync()["state"], read()), ("unreachable", mine))
    romm.saves.pop()
    add_save(5, b"right size, other bytes", "Game.srm", content_hash="0" * 32)
    check("sum does not match", (sync()["state"], read()), ("unreachable", mine))
    romm.saves.pop()

    # The copies folder as a link to somewhere else: no copy goes there, and nothing is replaced.
    elsewhere = os.path.join(TMP, "elsewhere")
    os.makedirs(elsewhere)
    write(b"not this game's", os.path.join(elsewhere, "Game_20200101_000000.srm"))
    kept_aside = os.path.join(TMP, "copies-aside")
    os.rename(copies, kept_aside)
    os.symlink(elsewhere, copies)
    add_save(5, b"newer again", "Game.srm")
    check("linked copies folder", (sync()["state"], read(), sorted(os.listdir(elsewhere))), ("disk", mine, ["Game_20200101_000000.srm"]))
    os.remove(copies)
    os.rename(kept_aside, copies)
    romm.saves.pop()
    check("in step again", sync()["state"], "synced")

    # Saves of another emulator, another slot, another game or gone from RomM's disk are not this game's.
    newest = read()
    add_save(5, b"other emulator", "Game.srm", emulator="retroarch-desmume")
    add_save(5, b"other slot", "Game.srm", slot="manual")
    add_save(5, b"gone", "Game.srm", missing_from_fs=True)
    add_save(6, b"other game", "Game.srm")
    check("not mine", (sync()["state"], read()), ("synced", newest))

    # An older upload with no emulator noted is still this game's.
    add_save(5, b"no emulator noted", "Game.srm", emulator=None)
    check("no emulator noted", (sync()["state"], read()), ("brought", b"no emulator noted"))
    newest = read()

    # No save here (a reinstall): RomM's comes, and there is nothing to copy first.
    os.remove(save)
    before = len(kept_copies())
    check("after a reinstall", (sync()["state"], read(), len(kept_copies())), ("brought", newest, before))

    # RetroArch sorting saves by core, or not by console: the save is looked for where it then is.
    flags(content="true", core="true")
    by_core = os.path.join(saves, "nds", "melonDS DS", "Game.srm")
    write(b"sorted by core", by_core)
    sent = len(romm.uploads)
    check("another file, nothing known of it: not sent unasked", (sync()["state"], len(romm.uploads)), ("conflict", sent))
    check("by-core save sent on request", (sync("deck")["state"], romm.saves[-1]["data"]), ("sent", b"sorted by core"))
    flags(content="false")
    flat = os.path.join(saves, "Game.srm")
    check("not sorted: brought to the saves folder itself", (sync()["state"], read(flat)), ("brought", b"sorted by core"))
    flags()

    # A console whose saves are not known yet, and a game not on the Deck.
    os.makedirs(os.path.join(ROMS, "psp"), exist_ok=True)
    romm.roms[40] = rom(40, "Psp.iso", slug="psp")
    write(DATA, os.path.join(ROMS, "psp", "Psp.iso"))
    run(plugin.game_take(4000, 40))
    check("console not handled yet", run(plugin.save_sync(4000))["state"], "unsupported")
    romm.roms[41] = rom(41, "Away.nds")
    run(plugin.game_take(4100, 41))
    check("game not here", run(plugin.save_sync(4100))["state"], "not-installed")
    for appid in (500, 4000, 4100):
        run(plugin.game_release(appid))
    os.remove(rom_file)
    os.remove(os.path.join(ROMS, "psp", "Psp.iso"))
    check("the game's own files were never touched by all this", os.path.isfile(save), True)


def test_play():
    romm.roms[14] = rom(14, "Played.nds")
    romm.codes["PQRSTUVW"] = (14, GAMES)
    pair("PQRSTUVW", True)
    run(plugin.game_take(1400, 14))
    check("no play time with a key that may not write it", run(plugin.play_state()), {"allowed": False, "waiting": 0})
    check("a game not the add-on's has no session", run(plugin.play_started(9999))["why"], "game")
    check("nor an end", run(plugin.play_ended(9999))["why"], "game")
    check("started", run(plugin.play_started(1400)), {"ok": True})
    check("too short to count", run(plugin.play_ended(1400)), {"ok": True, "kept": False})
    check("nothing kept for it", main._read_play(), [])

    # An hour on the clock, half of it asleep: half an hour played.
    run(plugin.play_started(1400))
    rom_id, began, clock = main._playing[1400]
    main._playing[1400] = (rom_id, began - 3600, clock - 1800)
    got = run(plugin.play_ended(1400))
    check("kept, waiting for a key that may send it", got, {"ok": True, "kept": True, "waiting": 1})
    session = main._read_play()[0]
    check("its game", session["rom_id"], 14)
    check("time played, not the sleep", 1800000 <= session["duration_ms"] < 1805000, True)
    start = datetime.datetime.strptime(session["start_time"], "%Y-%m-%dT%H:%M:%SZ")
    end = datetime.datetime.strptime(session["end_time"], "%Y-%m-%dT%H:%M:%SZ")
    check("from start to end, as the clock says", 3595 <= (end - start).total_seconds() <= 3605, True)
    check("play file private", mode(main.PLAY_FILE), 0o600)
    check("nothing sent with that key", romm.play, [])

    # A key that may write the player's data of each game: still good for everything, and it sends.
    romm.codes["WXYZ2345"] = (15, GAMES + ["roms.user.read", "roms.user.write"])
    check("key with play time paired", pair("WXYZ2345", True), {"result": "ok", "missing": [], "extra": []})
    check("still linked", run(plugin.romm_status()), "linked")
    check("sent once allowed", run(plugin.play_state()), {"allowed": True, "waiting": 0})
    check("as RomM takes it", romm.play, [[session]])
    check("with the add-on's key", [c for c in romm.calls if c[1] == "/api/play-sessions"][-1][2], "tok-15")

    # RomM asleep: they wait, and go once it answers.
    romm.play_down = 503
    run(plugin.play_started(1400))
    main._playing[1400] = (14, time.time() - 120, time.monotonic() - 120)
    check("waits while RomM does not answer", run(plugin.play_ended(1400))["waiting"], 1)
    romm.play_down = 0
    check("goes later", run(plugin.play_state())["waiting"], 0)
    check("one more sent", len(romm.play), 2)

    # Many at once: at most a hundred a time.
    main._write_private(main.PLAY_FILE, {"sessions": [dict(session, rom_id=n) for n in range(150)]})
    run(plugin.play_state())
    check("in batches", [len(b) for b in romm.play[2:]], [100, 50])
    # One RomM will never take is not kept waiting for ever.
    romm.play_down = 422
    main._write_private(main.PLAY_FILE, {"sessions": [session]})
    check("refused for good, dropped", run(plugin.play_state())["waiting"], 0)
    romm.play_down = 0
    # Kept waiting: at most so many, the oldest go first.
    romm.play_down = 503
    main.PLAY_MOST, most = 3, main.PLAY_MOST
    for n in range(5):
        main._play_keep(dict(session, rom_id=n))
    check("oldest dropped", [x["rom_id"] for x in main._read_play()], [2, 3, 4])
    main.PLAY_MOST = most
    romm.play_down = 0
    run(plugin.play_state())
    run(plugin.game_release(1400))


def test_gone():
    romm.roms[15] = rom(15, "Kept.nds")
    romm.roms[16] = rom(16, "Deleted.nds")
    run(plugin.game_take(1500, 15))
    run(plugin.game_take(1600, 16))
    check("none gone yet", run(plugin.game_gone()), {"ok": True, "apps": []})
    del romm.roms[16]
    check("the one deleted in RomM", run(plugin.game_gone()), {"ok": True, "apps": [1600]})
    asked = [c[1] for c in romm.calls if c[1].startswith("/api/roms/1")][-1:]
    check("asked for by itself before it counts", asked, ["/api/roms/16"])
    # A game RomM will not show is not taken as gone.
    romm.roms[16] = rom(16, "Deleted.nds")
    listed = romm.roms
    romm.roms = {k: v for k, v in listed.items() if k != 16}
    romm.refuse.add(16)
    check("not counted when RomM does not say so", run(plugin.game_gone())["ok"], False)
    romm.refuse.discard(16)
    romm.roms = listed
    for appid in (1500, 1600):
        run(plugin.game_release(appid))


def test_queue():
    target = os.path.join(ROMS, "nds", "Queued.nds")
    romm.roms[17] = rom(17, "Paused.nds")
    romm.roms[18] = rom(18, "Queued.nds")
    import hashlib
    romm.roms[17]["files"][0]["sha1_hash"] = hashlib.sha1(DATA).hexdigest()
    paused = os.path.join(ROMS, "nds", "Paused.nds")
    run(plugin.game_take(1700, 17))
    run(plugin.game_take(1800, 18))
    going = threading.Event()
    hold = threading.Event()

    def slow(h):
        h.send_response(200)
        h.send_header("Content-Length", str(len(DATA)))
        h.end_headers()
        try:
            for i in range(0, len(DATA), 1000):
                h.wfile.write(DATA[i:i + 1000])
                h.wfile.flush()
                if i >= 50000:
                    going.set()
                    hold.wait(5)
        except OSError:
            pass

    main.CHUNK = 4096
    romm.content[17] = slow
    romm.content[18] = DATA
    run(plugin.game_download(1700))
    going.wait(5)
    check("one at a time: the next waits", run(plugin.game_download(1800))["job"]["state"], "queued")
    check("no removing while queued", run(plugin.game_remove(1800))["why"], "busy")

    # Paused: what came stays, and the next in the queue goes.
    check("paused", run(plugin.game_pause(1700))["ok"], True)
    hold.set()
    state = wait(1700)
    check("paused state", (state["job"]["state"], state["installed"]), ("paused", False))
    check("half kept", os.path.isfile(paused + ".part"), True)
    with open(main.GAMES_FILE) as f:
        check("written down as paused", json.load(f)["games"]["1700"].get("paused"), True)
    check("the queued one came", wait(1800)["job"]["state"], "installed")
    check("nothing to pause now", run(plugin.game_pause(1700))["ok"], False)

    # A restart keeps a paused one, and the page still says how far it got.
    have = os.path.getsize(paused + ".part")
    main._jobs.clear()
    main._sweep_parts()
    check("paused one survives a restart", os.path.isfile(paused + ".part"), True)
    state = run(plugin.game_state(1700))
    check("still paused", (state["job"]["state"], state["job"]["done"]), ("paused", have))

    # Going on: only the rest is asked for, and the whole is checked against RomM's sum.
    romm.content[17] = DATA
    romm.ranges_asked.clear()
    run(plugin.game_download(1700))
    check("went on", wait(1700)["job"]["state"], "installed")
    check("from where it stopped", romm.ranges_asked, ["bytes=%d-" % have])
    with open(paused, "rb") as f:
        check("whole", f.read() == DATA, True)
    check("nothing half-done left", leftovers(), [])
    run(plugin.game_remove(1700))

    # A RomM that sends the whole file again: started again, still whole.
    with open(paused + ".part", "wb") as f:
        f.write(DATA[:5000])
    main._note_part(1700, paused + ".part", kept=True)
    romm.ranges = False
    run(plugin.game_download(1700))
    check("started again", wait(1700)["job"]["state"], "installed")
    with open(paused, "rb") as f:
        check("whole after starting again", f.read() == DATA, True)
    romm.ranges = True
    run(plugin.game_remove(1700))
    # Wrong bytes kept from before are caught by the sum, and not kept again.
    with open(paused + ".part", "wb") as f:
        f.write(bytes(5000))
    main._note_part(1700, paused + ".part", kept=True)
    run(plugin.game_download(1700))
    check("wrong half caught", wait(1700)["job"]["state"], "damaged")
    check("and thrown away", leftovers(), [])
    # Everything there already: started again rather than taken as done.
    with open(paused + ".part", "wb") as f:
        f.write(DATA)
    main._note_part(1700, paused + ".part", kept=True)
    run(plugin.game_download(1700))
    check("nothing left to send: started again", wait(1700)["job"]["state"], "installed")
    run(plugin.game_remove(1700))

    # Cancelling a paused one throws away what it brought.
    with open(paused + ".part", "wb") as f:
        f.write(DATA[:5000])
    main._note_part(1700, paused + ".part", kept=True)
    main._jobs.clear()
    state = run(plugin.game_cancel(1700))
    check("cancelled paused", ("job" in state, leftovers()), (False, []))
    with open(main.GAMES_FILE) as f:
        game = json.load(f)["games"]["1700"]
    check("forgotten", ("part" in game, "paused" in game), (False, False))

    # Let go of while paused: its half-done file goes with the entry.
    with open(paused + ".part", "wb") as f:
        f.write(DATA[:5000])
    main._note_part(1700, paused + ".part", kept=True)
    check("released while paused", run(plugin.game_release(1700))["ok"], True)
    check("its half went too", leftovers(), [])
    run(plugin.game_take(1700, 17))

    # Paused while waiting its turn: nothing asked of RomM for it.
    going.clear()
    hold.clear()
    romm.content[17] = slow
    run(plugin.game_remove(1800))
    run(plugin.game_download(1700))
    going.wait(5)
    asked = len([c for c in romm.calls if "/content/Queued" in c[1]])
    run(plugin.game_download(1800))
    check("paused in the queue", run(plugin.game_pause(1800))["job"]["state"], "paused")
    run(plugin.game_cancel(1700))
    hold.set()
    wait(1700)
    time.sleep(0.2)
    check("never asked for", len([c for c in romm.calls if "/content/Queued" in c[1]]), asked)
    check("still paused", run(plugin.game_state(1800))["job"]["state"], "paused")
    check("cancelled from the queue", run(plugin.game_cancel(1800))["job"]["state"], "cancelled")
    check("resumes from the queue", (run(plugin.game_download(1800)) and wait(1800))["job"]["state"], "installed")
    main.CHUNK = 1024 * 1024
    for appid in (1700, 1800):
        run(plugin.game_remove(appid))
        run(plugin.game_release(appid))
    check("nothing left", (os.path.exists(target), leftovers()), (False, []))


def wii_zip(files):
    import io
    import zipfile
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return out.getvalue()


def test_wii_saves():
    # As RetroDECK sets it: Dolphin's memory where its sandbox sees /var/data, a link to the saves.
    home_app = os.path.join(HOME, main.RETRODECK_HOME)
    ini = os.path.join(HOME, main.DOLPHIN_INI)
    os.makedirs(os.path.dirname(ini), exist_ok=True)
    with open(ini, "w") as f:
        f.write("[Core]\nNANDRootPath = /nope/\n[General]\nWiiSDCardPath = /x\nNANDRootPath = /var/data/dolphin-emu/Wii/\n")
    nand = os.path.join(HOME, "retrodeck", "saves", "wii", "dolphin")
    os.makedirs(nand)
    os.makedirs(os.path.join(home_app, "data", "dolphin-emu"))
    os.symlink(nand, os.path.join(home_app, "data", "dolphin-emu", "Wii"))
    check("Dolphin's memory found", main._wii_nand(), os.path.realpath(nand))

    # The disc's letters, from each kind of file.
    rvz = (b"RVZ\x01" + bytes(0x44) + b"\x00\x00\x00\x02" + bytes(0x0C) + b"RMGP01" + DATA)[:len(DATA)]
    os.makedirs(os.path.join(ROMS, "wii"), exist_ok=True)
    rom_file = os.path.join(ROMS, "wii", "Galaxy.rvz")
    with open(rom_file, "wb") as f:
        f.write(rvz)
    check("disc letters, rvz", main._disc_id(rom_file), "RMGP01")
    probe = os.path.join(TMP, "disc")
    for kind, raw in (("wbfs", b"WBFS" + bytes(4) + b"\x09" + bytes(0x200 - 9) + b"SOUP01"),
                      ("image", b"RZDP01" + bytes(0x12) + b"\x5d\x1c\x9e\xa3"),
                      ("unknown", b"hello"), ("not letters", b"RVZ\x01" + bytes(0x44) + b"\x00\x00\x00\x02" + bytes(0x0C) + b"rm/p.."),
                      ("GameCube", b"RVZ\x01" + bytes(0x44) + b"\x00\x00\x00\x01" + bytes(0x0C) + b"GALE01")):
        with open(probe, "wb") as f:
            f.write(raw)
        check("disc letters, " + kind, main._disc_id(probe), {"wbfs": "SOUP01", "image": "RZDP01"}.get(kind))

    romm.roms[42] = rom(42, "Galaxy.rvz", slug="wii")
    check("taken, installed", run(plugin.game_take(4200, 42))["installed"], True)
    folder = os.path.join(nand, "title", "00010000", "524d4750")
    copies = os.path.join(HOME, "retrodeck", "saves", "wii", main.SAVE_COPIES_DIR)

    def sync(choice=""):
        return run(plugin.save_sync(4200, choice))

    def put(files):
        shutil.rmtree(folder, ignore_errors=True)
        for name, data in files.items():
            os.makedirs(os.path.dirname(os.path.join(folder, name)), exist_ok=True)
            with open(os.path.join(folder, name), "wb") as f:
                f.write(data)

    def here():
        return {os.path.relpath(os.path.join(r, n), folder): open(os.path.join(r, n), "rb").read()
                for r, _, names in os.walk(folder) for n in names}

    check("nothing anywhere yet", sync()["state"], "none")
    first = {"data/GameData.bin": b"stars: 3", "data/banner.bin": b"banner", "content/title.tmd": b"tmd"}
    put(first)
    uploads = len(romm.uploads)
    check("sent", sync()["state"], "sent")
    q, name = romm.uploads[-1]
    check("sent as a zip named after the game", (q["emulator"], q["slot"], name, len(romm.uploads) - uploads),
          (["dolphin"], ["autosave"], "Galaxy.zip", 1))
    sent = romm.saves[-1]
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(sent["data"])) as z:
        check("the folder inside", {n: z.read(n) for n in z.namelist()}, first)
    check("same sum as RomM makes", main._save_hash(folder, main.SAVE_KINDS["wii"]), sent["content_hash"])
    check("in step", sync()["state"], "synced")
    check("no change seen", run(plugin.save_state(4200))["changed"], False)

    # A newer one in RomM, nothing changed here: brought, after a copy of this one.
    newer = {"data/GameData.bin": b"stars: 7", "data/banner.bin": b"banner", "content/title.tmd": b"tmd"}
    add_save(42, wii_zip(newer), "Galaxy.zip", emulator="dolphin")
    check("brought", sync()["state"], "brought")
    check("folder replaced", here(), newer)
    kept = os.listdir(copies)
    check("one copy, beside the memory", (len(kept), kept[0].startswith("524d4750_"), kept[0].endswith(".zip")), (1, True, True))
    with zipfile.ZipFile(os.path.join(copies, kept[0])) as z:
        check("the copy is the old save", {n: z.read(n) for n in z.namelist()}, first)
    check("nothing half-done beside it", sorted(os.listdir(os.path.dirname(folder))), ["524d4750"])

    # Changed here: seen, and sent.
    with open(os.path.join(folder, "data", "GameData.bin"), "wb") as f:
        f.write(b"stars: 9")
    os.utime(os.path.join(folder, "data", "GameData.bin"), ns=(1, 2_000_000_000_000_000_000))
    check("change seen", run(plugin.save_state(4200))["changed"], True)
    check("sent again", sync()["state"], "sent")

    # Both changed: nothing touched until chosen.
    with open(os.path.join(folder, "data", "GameData.bin"), "wb") as f:
        f.write(b"stars: 10")
    add_save(42, wii_zip({"data/GameData.bin": b"stars: 11"}), "Galaxy.zip", emulator="dolphin")
    before = here()
    check("conflict", sync()["state"], "conflict")
    check("untouched", here(), before)
    check("RomM's chosen", sync("romm")["state"], "brought")
    check("now RomM's", here(), {"data/GameData.bin": b"stars: 11"})
    check("two copies kept", len(os.listdir(copies)), 2)

    # A zip from RomM that would write outside the folder: not taken, nothing touched.
    before = here()
    add_save(42, wii_zip({"../../escape.bin": b"x"}), "Galaxy.zip", emulator="dolphin")
    check("bad zip refused", sync("romm")["state"], "save")
    check("untouched by it", (here(), os.path.exists(os.path.join(nand, "title", "escape.bin"))), (before, False))
    check("nothing half-done after it", sorted(os.listdir(os.path.dirname(folder))), ["524d4750"])
    romm.saves = [x for x in romm.saves if x["rom_id"] != 42]
    # A cut zip is not taken either.
    add_save(42, wii_zip({"data/GameData.bin": b"stars: 12"}), "Galaxy.zip", emulator="dolphin")
    romm.saves[-1]["content_hash"] = "0" * 32
    check("wrong sum refused", sync("romm")["state"], "unreachable")
    check("untouched by that", here(), before)

    # A link inside the save: left alone.
    os.symlink("/etc/passwd", os.path.join(folder, "data", "link"))
    check("link left alone", sync()["state"], "layout")
    os.remove(os.path.join(folder, "data", "link"))
    # Dolphin's memory where only its sandbox knows: not guessed.
    with open(ini, "w") as f:
        f.write("[General]\nNANDRootPath = /run/elsewhere/Wii\n")
    check("unknown memory place", sync()["state"], "layout")
    os.remove(ini)
    check("without settings, Dolphin's own place", main._wii_nand(), os.path.realpath(nand))
    # RetroDECK's saves elsewhere (an SD card): Dolphin's memory there is found too.
    card = os.path.join(TMP, "card", "retrodeck", "saves")
    os.makedirs(os.path.join(card, "wii", "dolphin"))
    rd = os.path.join(home_app, "config", "retrodeck", "retrodeck.json")
    os.makedirs(os.path.dirname(rd), exist_ok=True)
    with open(rd, "w") as f:
        json.dump({"paths": {"saves_path": card}}, f)
    link = os.path.join(home_app, "data", "dolphin-emu", "Wii")
    os.remove(link)
    os.symlink(os.path.join(card, "wii", "dolphin"), link)
    check("on the card", main._wii_nand(), os.path.realpath(os.path.join(card, "wii", "dolphin")))
    os.remove(link)
    os.symlink(nand, link)
    os.remove(rd)
    romm.saves = [x for x in romm.saves if x["rom_id"] != 42]
    run(plugin.game_release(4200))
    os.remove(rom_file)


def test_emulators():
    # Decky's own Python comes without Python's XML reader: the add-on must not need it.
    blocked = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "xml" or k.startswith("xml.")}
    sys.modules["xml"] = None
    try:
        _test_emulators()
    finally:
        del sys.modules["xml"]
        sys.modules.update(blocked)


def _test_emulators():
    main.FLATPAK_FILES = (os.path.join(".local", "share", "flatpak", "app", "net.retrodeck.retrodeck", "current", "active", "files"),)
    es = os.path.join(HOME, main.FLATPAK_FILES[0], main.ES_SYSTEMS)
    os.makedirs(os.path.dirname(es))
    with open(es, "w") as f:
        f.write("""<?xml version="1.0"?>
<systemList>
  <system><name>nds</name>
    <command label="DeSmuME">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/desmume_libretro.so %ROM%</command>
    <command label="melonDS DS">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/melondsds_libretro.so %ROM%</command>
    <!-- <command label="Hidden">gone %ROM%</command> -->
    <command label='Q &amp; A'>run &quot;%ROM%&quot; &#38; done</command>
    <command label="Broken">a
b</command>
  </system>
  <system><name>wii</name><command label="Dolphin (Standalone)">%EMULATOR_DOLPHIN% -b -e %ROM%</command></system>
</systemList>""")
    romm.roms[43] = rom(43, "Emu.nds")
    target = os.path.join(ROMS, "nds", "Emu.nds")
    with open(target, "wb") as f:
        f.write(DATA)
    run(plugin.game_take(4300, 43))
    check("the console's emulators, in ES-DE's order", run(plugin.game_emulators(4300)),
          {"ok": True, "emulators": ["DeSmuME", "melonDS DS", "Q & A"], "chosen": "", "saves": "melonDS DS"})
    check("entities read back", main._emulators("nds")["Q & A"], 'run "%ROM%" & done')
    check("not one of them", run(plugin.game_set_emulator(4300, "rm -rf /"))["why"], "emulator")
    check("a broken one is not offered", run(plugin.game_set_emulator(4300, "Broken"))["why"], "emulator")
    check("chosen", run(plugin.game_set_emulator(4300, "DeSmuME")), {"ok": True, "chosen": "DeSmuME"})
    check("kept", run(plugin.game_emulators(4300))["chosen"], "DeSmuME")
    with open(main.LAUNCH_FILE) as f:
        check("the launcher is told RetroDECK's command", json.load(f)["43"],
              {"path": target, "emulator": "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/desmume_libretro.so %ROM%"})

    # The launcher hands it to RetroDECK as its emulator, the file still one argument of its own.
    fake_bin = os.path.join(TMP, "bin-emu")
    os.makedirs(fake_bin)
    seen = os.path.join(TMP, "flatpak-emu.json")
    with open(os.path.join(fake_bin, "flatpak"), "w") as f:
        f.write("#!/usr/bin/python3\nimport json, sys\njson.dump(sys.argv[1:], open(%r, 'w'))\n" % seen)
    os.chmod(os.path.join(fake_bin, "flatpak"), 0o755)
    env = {"PATH": fake_bin + ":/usr/bin:/bin", "HOME": HOME}
    done = subprocess.run([main.LAUNCHER, "43"], env=env, capture_output=True, text=True)
    check("launched", done.returncode, 0)
    with open(seen) as f:
        check("with the emulator chosen", json.load(f), ["run", "net.retrodeck.retrodeck", "-e",
              "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/desmume_libretro.so %ROM%", target])
    # Saves are kept in step only with the emulator whose saves are known.
    check("saves not with another emulator", run(plugin.save_sync(4300))["state"], "emulator")
    check("back to the default", run(plugin.game_set_emulator(4300, "")), {"ok": True, "chosen": ""})
    # Nothing chosen: a console whose saves are kept in step starts with the emulator whose saves
    # those are, not with whatever RetroDECK lists first (DeSmuME, on a fresh RetroDECK).
    melon = "%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/melondsds_libretro.so %ROM%"
    with open(main.LAUNCH_FILE) as f:
        check("the default is the one whose saves are kept", json.load(f)["43"], {"path": target, "emulator": melon})
    os.remove(seen)
    subprocess.run([main.LAUNCHER, "43"], env=env, capture_output=True, text=True)
    with open(seen) as f:
        check("started with it", json.load(f), ["run", "net.retrodeck.retrodeck", "-e", melon, target])
    check("the old 'another emulator' state is not kept", run(plugin.save_state(4300))["state"], "unknown")
    check("saves again", run(plugin.save_sync(4300))["state"] != "emulator", True)
    # A list where the emulator is not a plain line is not believed.
    with open(main.LAUNCH_FILE) as f:
        table = json.load(f)
    table["43"] = {"path": target, "emulator": "a\nb"}
    main._write_private(main.LAUNCH_FILE, table)
    check("odd emulator refused", subprocess.run([main.LAUNCHER, "43"], env=env, capture_output=True).returncode, 2)
    check("a console ES-DE does not list", run(plugin.game_emulators(9999))["why"], "game")
    # ES-DE's file unreadable: the list is still written, the game starts with RetroDECK's choice.
    run(plugin.game_set_emulator(4300, "DeSmuME"))
    os.chmod(es, 0)
    main._es_cache.clear()
    check("list still written", main._write_games(main._read_games()), True)
    check("saves again while it starts with RetroDECK's own", run(plugin.save_sync(4300))["state"] != "emulator", True)
    with open(main.LAUNCH_FILE) as f:
        check("plain file without ES-DE's list", json.load(f)["43"], target)
    os.chmod(es, 0o644)
    main._es_cache.clear()
    # A console without saves kept in step still starts as RetroDECK decides.
    run(plugin.game_set_emulator(4300, ""))
    data = main._read_games()
    data["games"]["4300"]["system"] = "gba"
    check("list written", main._write_games(data), True)
    with open(main.LAUNCH_FILE) as f:
        check("plain for a console without saves kept", json.load(f)["43"], target)
    data["games"]["4300"]["system"] = "nds"
    main._write_games(data)
    # A RetroDECK whose list lacks that emulator: started as it decides, and saves are not touched.
    with open(es) as f:
        text = f.read()
    with open(es, "w") as f:
        f.write(text.replace('label="melonDS DS"', 'label="Old melonDS"'))
    main._es_cache.clear()
    main._write_games(main._read_games())
    with open(main.LAUNCH_FILE) as f:
        check("plain without that emulator", json.load(f)["43"], target)
    check("no saves then", run(plugin.save_sync(4300))["state"], "emulator")
    run(plugin.game_release(4300))
    os.remove(target)
    # Later checks start without ES-DE's list, as a Deck without RetroDECK would.
    os.remove(es)
    main._es_cache.clear()


def test_moved_folders():
    # RetroDECK's folders moved (to an SD card, say, with its own "move" tool): the records follow
    # the files the add-on finds there, the same and in the same place inside; nothing is moved.
    old = os.path.realpath(os.path.join(TMP, "moved-old", "roms"))
    new = os.path.realpath(os.path.join(TMP, "moved-card", "retrodeck", "roms"))
    outside = os.path.join(TMP, "moved-outside")
    for d in (os.path.join(old, "nds", "Disc"), os.path.join(new, "nds", "Disc"), outside):
        os.makedirs(d)
    rd = os.path.join(HOME, ".var", "app", "net.retrodeck.retrodeck", "config", "retrodeck", "retrodeck.json")
    os.makedirs(os.path.dirname(rd), exist_ok=True)
    with open(rd, "w") as f:
        json.dump({"paths": {"roms_path": new}}, f)
    check("the new folder is RetroDECK's", main._roms_root(), new)

    def put(path, data=DATA):
        with open(path, "wb") as f:
            f.write(data)
        return path

    def entry(rom_id, path, size=len(DATA), **more):
        return {"rom_id": rom_id, "system": "nds", "platform": "nds", "size": size, "path": path,
                "installed": True, "files": [path], "folders": [], "saves": {}, **more}

    # Brought by the add-on under the old folder: its file, and the folder of an unpacked archive.
    moved = put(os.path.join(old, "nds", "Moved.nds"))
    disc_old = os.path.join(old, "nds", "Disc")
    bin_old = put(os.path.join(disc_old, "a.bin"), b"disc")
    games = main._read_games()
    games["games"].update({
        "8101": entry(81, moved, size=len(DATA) + 4, files=[moved, bin_old], folders=[disc_old],
                      marks={moved: main._mark(moved), bin_old: main._mark(bin_old)}, disc=bin_old),
        "8102": entry(82, os.path.join(old, "nds", "Nowhere.nds")),
        "8103": entry(83, os.path.join(old, "nds", "Sized.nds")),
        "8104": entry(84, put(os.path.join(outside, "Still.nds"))),
        "8105": entry(85, os.path.join(old, "nds", "Link.nds")),
        "8106": {"rom_id": 86, "system": "nds", "platform": "nds", "size": 9, "path": None, "installed": False,
                 "files": [], "folders": [], "paused": True, "part": os.path.join(old, "nds", "Half.nds.part")},
        # The add-on's file is gone (a card taken out, say); the player's own file of that size is there.
        "8108": entry(88, os.path.join(old, "nds", "Foreign.nds"),
                      marks={os.path.join(old, "nds", "Foreign.nds"): [len(DATA), 1]}),
    })
    main._write_games(games)
    # The move: the files as they are now on the card (with their time, as a move keeps it), the old folder empty.
    time.sleep(0.01)
    shutil.copy2(moved, os.path.join(new, "nds", "Moved.nds"))
    shutil.copy2(bin_old, os.path.join(new, "nds", "Disc", "a.bin"))
    put(os.path.join(new, "nds", "Foreign.nds"), DATA[::-1])
    shutil.rmtree(os.path.join(old, "nds"))
    os.makedirs(os.path.join(old, "nds"))
    put(os.path.join(new, "nds", "Other.nds"), b"not the add-on's")
    put(os.path.join(new, "nds", "Sized.nds"), DATA[:-1])
    os.symlink(put(os.path.join(outside, "Link.nds")), os.path.join(new, "nds", "Link.nds"))
    put(os.path.join(new, "nds", "Half.nds.part"), b"half")
    before = main._read_games()["games"]

    run(plugin._main())
    after = main._read_games()["games"]
    here = os.path.join(new, "nds", "Moved.nds")
    bin_new = os.path.join(new, "nds", "Disc", "a.bin")
    check("found in the new folder", after["8101"]["path"], here)
    check("its files follow", after["8101"]["files"], [here, bin_new])
    check("its folders follow", after["8101"]["folders"], [os.path.join(new, "nds", "Disc")])
    check("its marks follow", list(after["8101"]["marks"]), [here, bin_new])
    check("the disc chosen follows", after["8101"]["disc"], bin_new)
    check("the player's file of that size: the entry follows", after["8108"]["path"], os.path.join(new, "nds", "Foreign.nds"))
    check("but it is not marked the add-on's", after["8108"]["marks"], {os.path.join(new, "nds", "Foreign.nds"): [len(DATA), 1]})
    with open(main.LAUNCH_FILE) as f:
        check("the launcher starts the moved file", json.load(f)["81"], here)
    check("shown as there", run(plugin.game_state(8101))["installed"], True)
    check("not in the new folder: unchanged", after["8102"], before["8102"])
    check("another size: unchanged", after["8103"], before["8103"])
    check("still where it was: untouched", after["8104"], before["8104"])
    check("a link out of the folder: refused", after["8105"], before["8105"])
    check("the paused download's half follows", after["8106"]["part"], os.path.join(new, "nds", "Half.nds.part"))
    check("nothing moved or removed", sorted(os.listdir(os.path.join(new, "nds"))),
          ["Disc", "Foreign.nds", "Half.nds.part", "Link.nds", "Moved.nds", "Other.nds", "Sized.nds"])
    check("the old folder is left as it was", os.listdir(os.path.join(old, "nds")), [])

    # Found when the panel asks too, not only at start.
    games = main._read_games()
    later = put(os.path.join(new, "nds", "Later.nds"))
    games["games"]["8107"] = entry(87, os.path.join(old, "nds", "Later.nds"))
    main._write_games(games)
    check("found when asked", run(plugin.game_state(8107))["installed"], True)
    check("and kept", main._read_games()["games"]["8107"]["path"], later)

    # The save's record follows RetroDECK's saves folder too, when the save there is the same one.
    cfg = os.path.join(HOME, main.RETROARCH_CFG)
    kept_cfg = None
    if os.path.isfile(cfg):
        with open(cfg) as f:
            kept_cfg = f.read()
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    with open(cfg, "w") as f:
        f.write('savefiles_in_content_dir = "false"\nsort_savefiles_by_content_enable = "true"\n'
                'sort_savefiles_enable = "false"\n')
    saves_new = os.path.realpath(os.path.join(TMP, "moved-card", "retrodeck", "saves"))
    os.makedirs(os.path.join(saves_new, "nds"))
    with open(rd, "w") as f:
        json.dump({"paths": {"roms_path": new, "saves_path": saves_new}}, f)
    srm = put(os.path.join(saves_new, "nds", "Later.srm"), b"save")
    games = main._read_games()
    games["games"]["8107"]["saves"] = {"main": {"path": os.path.join(TMP, "moved-old", "saves", "nds", "Later.srm"),
                                                "hash": main._md5(srm), "mtime": 1}, "state": "in-step"}
    main._write_games(games)
    run(plugin._main())
    check("the save's record follows", main._read_games()["games"]["8107"]["saves"]["main"]["path"], srm)
    check("not taken for a change", run(plugin.save_state(8107))["changed"], False)
    # Another save there is not taken for the one remembered.
    games = main._read_games()
    games["games"]["8107"]["saves"]["main"].update(path=os.path.join(TMP, "moved-old", "saves", "nds", "Later.srm"), hash="0" * 32)
    main._write_games(games)
    run(plugin._main())
    check("another save: unchanged", main._read_games()["games"]["8107"]["saves"]["main"]["hash"], "0" * 32)
    check("still its old place", main._read_games()["games"]["8107"]["saves"]["main"]["path"],
          os.path.join(TMP, "moved-old", "saves", "nds", "Later.srm"))

    # Removing a moved game takes away only what the add-on brought.
    check("removed", run(plugin.game_remove(8101))["ok"], True)
    check("only its files", sorted(os.listdir(os.path.join(new, "nds"))),
          ["Foreign.nds", "Half.nds.part", "Later.nds", "Link.nds", "Other.nds", "Sized.nds"])
    run(plugin.game_remove(8108))
    check("the player's file is never removed", os.path.isfile(os.path.join(new, "nds", "Foreign.nds")), True)
    for appid in (8101, 8102, 8103, 8104, 8105, 8106, 8107, 8108):
        run(plugin.game_release(appid))
    if kept_cfg is None:
        os.remove(cfg)
    else:
        with open(cfg, "w") as f:
            f.write(kept_cfg)
    os.remove(rd)


def test_discs():
    os.makedirs(os.path.join(ROMS, "ps2"), exist_ok=True)
    fake_bin = os.path.join(TMP, "bin-discs")
    os.makedirs(fake_bin)
    seen = os.path.join(TMP, "flatpak-discs.json")
    with open(os.path.join(fake_bin, "flatpak"), "w") as f:
        f.write("#!/usr/bin/python3\nimport json, sys\njson.dump(sys.argv[1:], open(%r, 'w'))\n" % seen)
    os.chmod(os.path.join(fake_bin, "flatpak"), 0o755)
    env = {"PATH": fake_bin + ":/usr/bin:/bin", "HOME": HOME}

    def launch(rom_id):
        if os.path.exists(seen):
            os.remove(seen)
        done = subprocess.run([main.LAUNCHER, str(rom_id)], env=env, capture_output=True, text=True)
        if done.returncode:
            return done.returncode
        with open(seen) as f:
            return json.load(f)

    def table():
        with open(main.LAUNCH_FILE) as f:
            return json.load(f)

    # Two discs as .cue with their .bin tracks, and no playlist from RomM.
    members = [("Two Cues (Disc 1).cue", b'FILE "Two Cues (Disc 1).bin" BINARY\n  TRACK 01 MODE2/2352\n'),
               ("Two Cues (Disc 1).bin", b"a" * 5000),
               ("Two Cues (Disc 2).cue", b'FILE "Two Cues (Disc 2).bin" BINARY\n  TRACK 01 MODE2/2352\n'),
               ("Two Cues (Disc 2).bin", b"b" * 6000)]
    romm.roms[180] = rom(180, "Two Cues", files=[(n, len(d)) for n, d in members], slug="ps2")
    romm.content[180] = archive(members)
    run(plugin.game_take(18000, 180))
    run(plugin.game_download(18000))
    check("cues brought", wait(18000)["job"]["state"], "installed")
    folder = os.path.join(ROMS, "ps2", "Two Cues")
    plain = os.path.join(folder, "Two Cues (Disc 1).cue")  # the first disc's sheet, never a track
    check("nothing chosen: the plain entry as before", table()["180"], plain)
    check("the discs offered, never their tracks", run(plugin.game_discs(18000)),
          {"ok": True, "discs": [{"file": "Two Cues (Disc 1).cue", "name": "Two Cues (Disc 1)"},
                                 {"file": "Two Cues (Disc 2).cue", "name": "Two Cues (Disc 2)"}],
           "chosen": "", "starts": "Two Cues (Disc 1).cue"})
    for bad in ("Two Cues (Disc 1).bin", "../Two Cues (Disc 1).cue", "/etc/passwd", "Nope.cue", 7):
        check("not a disc: %r" % (bad,), run(plugin.game_set_disc(18000, bad))["why"], "disc")
    check("still plain", table()["180"], plain)
    disc2 = os.path.join(folder, "Two Cues (Disc 2).cue")
    check("disc chosen", run(plugin.game_set_disc(18000, "Two Cues (Disc 2).cue")), {"ok": True, "chosen": "Two Cues (Disc 2).cue"})
    check("kept", [run(plugin.game_discs(18000))[k] for k in ("chosen", "starts")], ["Two Cues (Disc 2).cue", "Two Cues (Disc 2).cue"])
    check("the launcher is told the disc and its folder", table()["180"], {"path": disc2, "folder": folder})
    check("the disc starts", launch(180), ["run", "net.retrodeck.retrodeck", disc2])
    # A list pointing outside the game's folder is not believed.
    wrong = table()
    wrong["180"] = {"path": os.path.join(ROMS, "ps2", "elsewhere.iso"), "folder": folder}
    with open(wrong["180"]["path"], "wb") as f:
        f.write(b"x")
    main._write_private(main.LAUNCH_FILE, wrong)
    check("a file outside the folder refused", launch(180), 2)
    os.remove(wrong["180"]["path"])
    run(plugin.game_set_disc(18000, "Two Cues (Disc 2).cue"))
    # A disc that went away (or a choice the record no longer holds) falls back to the plain entry.
    data = main._read_games()
    data["games"]["18000"]["disc"] = os.path.join(ROMS, "ps2", "Two Cues (Disc 3).cue")
    main._write_games(data)
    check("an unknown disc is never started", table()["180"], plain)
    # A playlist of the player's own already there is never overwritten.
    theirs = os.path.join(folder, "Two Cues.m3u")
    with open(theirs, "wb") as f:
        f.write(b"mine\n")
    check("their playlist left alone", (run(plugin.game_set_disc(18000, "playlist"))["why"], open(theirs, "rb").read()), ("exists", b"mine\n"))
    os.remove(theirs)
    check("all discs", run(plugin.game_set_disc(18000, "playlist")), {"ok": True, "chosen": "playlist"})
    with open(theirs, "rb") as f:
        check("a playlist made of the discs", f.read(), b"Two Cues (Disc 1).cue\nTwo Cues (Disc 2).cue\n")
    check("the playlist starts", launch(180), ["run", "net.retrodeck.retrodeck", theirs])
    check("recorded as the add-on's", theirs in main._read_games()["games"]["18000"]["files"], True)
    check("chosen again: the same playlist", run(plugin.game_set_disc(18000, "playlist"))["ok"], True)
    check("back to nothing chosen", run(plugin.game_set_disc(18000, "")), {"ok": True, "chosen": ""})
    check("plain again", table()["180"], plain)
    check("started as before", launch(180), ["run", "net.retrodeck.retrodeck", plain])
    run(plugin.game_set_disc(18000, "playlist"))
    run(plugin.game_remove(18000))
    check("the playlist goes with the game", os.path.exists(folder), False)
    check("nothing to choose without the game", run(plugin.game_discs(18000))["ok"], False)
    run(plugin.game_release(18000))

    # RomM's own playlist is the one offered, and nothing new is made.
    members = [("Disc A.chd", b"a" * 3000), ("Disc B.chd", b"b" * 4000), ("Pair.m3u", b"Disc A.chd\nDisc B.chd\n")]
    romm.roms[181] = rom(181, "Pair", files=[(n, len(d)) for n, d in members], slug="ps2")
    romm.content[181] = archive(members)
    run(plugin.game_take(18100, 181))
    run(plugin.game_download(18100))
    wait(18100)
    folder = os.path.join(ROMS, "ps2", "Pair")
    check("RomM's playlist starts by default", [run(plugin.game_discs(18100))[k] for k in ("chosen", "starts")], ["", "playlist"])
    check("one disc", run(plugin.game_set_disc(18100, "Disc A.chd"))["ok"], True)
    check("that disc", table()["181"], {"path": os.path.join(folder, "Disc A.chd"), "folder": folder})
    run(plugin.game_set_disc(18100, "playlist"))
    check("RomM's playlist used", (table()["181"]["path"], sorted(os.listdir(folder))),
          (os.path.join(folder, "Pair.m3u"), ["Disc A.chd", "Disc B.chd", "Pair.m3u"]))
    run(plugin.game_remove(18100))
    run(plugin.game_release(18100))

    # A game of one file has nothing to choose, and its entry stays as it was.
    romm.roms[182] = rom(182, "Single.iso", slug="ps2")
    romm.content[182] = DATA
    run(plugin.game_take(18200, 182))
    run(plugin.game_download(18200))
    wait(18200)
    single = os.path.join(ROMS, "ps2", "Single.iso")
    check("one file: no choice", run(plugin.game_discs(18200)), {"ok": False, "why": "discs"})
    check("one file: none taken", run(plugin.game_set_disc(18200, "Single.iso"))["why"], "discs")
    check("one file: plain entry", table()["182"], single)
    run(plugin.game_remove(18200))
    run(plugin.game_release(18200))
    check("an unknown entry", run(plugin.game_discs(99999))["why"], "game")


def test_device():
    """This Deck as one of the player's devices in RomM: registered once, with nothing personal,
    and named on each save and play session it sends."""
    with open(os.path.join(HOME, main.RETROARCH_CFG), "w") as f:
        f.write('savefile_directory = "~/retrodeck/saves"\nsavefiles_in_content_dir = "false"\n'
                'sort_savefiles_by_content_enable = "true"\nsort_savefiles_enable = "false"\n')
    romm.roms[80] = rom(80, "Device.nds")
    rom_file = os.path.join(ROMS, "nds", "Device.nds")
    save = os.path.join(HOME, "retrodeck", "saves", "nds", "Device.srm")
    with open(rom_file, "wb") as f:
        f.write(DATA)

    def write(data):
        os.makedirs(os.path.dirname(save), exist_ok=True)
        with open(save, "wb") as f:
            f.write(data)

    def sync(choice=""):
        return run(plugin.save_sync(8000, choice))

    def registered():
        return [c for c in romm.calls if c[0] == "POST" and c[1] == "/api/devices"]

    def device_of(q):
        return q.get("device_id", [None])[0]

    session = {"rom_id": 80, "start_time": "2026-11-14T10:00:00Z", "end_time": "2026-11-14T11:00:00Z", "duration_ms": 3600000}

    # A key that may not modify devices: everything goes as before, from no device, and the
    # settings say so.
    romm.codes["DVCEKEYA"] = (80, GAMES + ["roms.user.read", "roms.user.write"])
    check("key without devices paired", pair("DVCEKEYA", True)["result"], "ok")
    check("no device without it", run(plugin.device_state()), {"allowed": False})
    run(plugin.game_take(8000, 80))
    write(b"first from the deck")
    check("sent", sync()["state"], "sent")
    check("from no device", device_of(romm.uploads[-1][0]), None)
    main._play_keep(session)
    run(plugin.play_state())
    check("play time from no device", romm.play_devices[-1:], [None])
    check("nothing registered", (registered(), os.path.exists(main.DEVICE_FILE)), ([], False))

    # Modifying devices is the only thing more a key may do for it: one that may also see the
    # devices, or more, is still refused and deleted at RomM.
    romm.codes["DVCEKEYB"] = (81, GAMES + ["devices.read", "devices.write"])
    check("devices read refused", pair("DVCEKEYB", True), {"result": "scopes", "missing": [], "extra": ["devices.read"]})
    check("and revoked", romm.deleted[-1], (81, "tok-81"))
    romm.codes["DVCEKEYC"] = (82, GAMES + ["devices.write", "users.write"])
    check("wider still refused", pair("DVCEKEYC", True)["extra"], ["users.write"])
    check("the kept key stays", run(plugin.device_state()), {"allowed": False})

    # A key that may: the device is registered once, as what it is and nothing that tells who or where.
    romm.codes["DVCEKEYD"] = (83, GAMES + ["roms.user.read", "roms.user.write", "devices.write"])
    check("key with devices paired", pair("DVCEKEYD", True), {"result": "ok", "missing": [], "extra": []})
    check("device allowed", run(plugin.device_state()), {"allowed": True})
    write(b"second from the deck")
    sent = len(romm.uploads)
    check("sent as the device", sync()["state"], "sent")
    check("registered once", len(registered()), 1)
    device = next(iter(romm.devices))
    check("registered with nothing personal", romm.devices[device], main._device_about())
    check("only what it is", sorted(romm.devices[device]), ["client", "name", "platform"])
    # What it is: a Steam Deck by its board's name, any other computer a PC with its system's name.
    real_open = open

    def fake(board, release):
        def opener(path, *a, **k):
            if path == "/sys/devices/virtual/dmi/id/product_name":
                return io.StringIO(board + "\n")
            if path == "/etc/os-release":
                return io.StringIO(release)
            return real_open(path, *a, **k)
        return opener

    with mock.patch("builtins.open", fake("Galileo", 'NAME="SteamOS"\n')):
        check("a Deck is a Steam Deck", main._device_about(), {"name": "Steam Deck", "platform": "SteamOS", "client": "Vistazo RomM"})
    with mock.patch("builtins.open", fake("MS-7D75", 'NAME="CachyOS Linux"\nID=cachyos\n')):
        check("another computer is a PC", main._device_about(), {"name": "PC", "platform": "CachyOS Linux", "client": "Vistazo RomM"})
    check("device file private", mode(main.DEVICE_FILE), 0o600)
    with open(main.DEVICE_FILE) as f:
        check("device kept", json.load(f), {"url": URL, "id": device})
    # RomM had not seen the slot's newest from this device (it went before there was one): it
    # refuses, and the save goes as it did without one, now marked with the device.
    tries = [q for q, _ in romm.uploads[sent:]]
    check("refused once, then sent over", [(device_of(q), q.get("overwrite")) for q in tries], [(device, None), (device, ["true"])])
    check("RomM knows where it came from", romm.saves[-1].get("origin_device_id"), device)
    write(b"third from the deck")
    sent = len(romm.uploads)
    check("next one straight", (sync()["state"], [(device_of(q), q.get("overwrite")) for q, _ in romm.uploads[sent:]]), ("sent", [(device, None)]))
    check("still registered once", len(registered()), 1)
    main._play_keep(session)
    run(plugin.play_state())
    check("play time from the device", romm.play_devices[-1], device)

    # A save brought from RomM: RomM is told this Deck has it, and the next one sent goes straight.
    theirs = add_save(80, b"played elsewhere", "Device.srm")
    check("brought", sync()["state"], "brought")
    check("RomM told", romm.downloaded[-1], (theirs["id"], {"device_id": device, "content_hash": theirs["content_hash"]}))
    write(b"after the one brought")
    sent = len(romm.uploads)
    check("sent straight after", (sync()["state"], len(romm.uploads) - sent), ("sent", 1))

    # RomM failing the save for the game itself: the device is not registered again for it.
    write(b"RomM fails this one")
    romm.upload_down = 404
    check("refused for the game", sync()["state"], "unreachable")
    check("device not registered again", len(registered()), 1)
    romm.upload_down = 0
    check("then sent", sync()["state"], "sent")

    # The device deleted in RomM: registered again, and the save goes as the new one.
    romm.devices.clear()
    write(b"after the device was deleted")
    check("sent after deletion", sync()["state"], "sent")
    check("registered again", len(registered()), 2)
    again = next(iter(romm.devices))
    check("as the new device", (again != device, device_of(romm.uploads[-1][0]), romm.saves[-1].get("origin_device_id")), (True, again, again))
    with open(main.DEVICE_FILE) as f:
        check("new device kept", json.load(f)["id"], again)
    # Deleted again while the add-on was not running: play time goes as a new device, not lost
    # to one RomM no longer knows.
    romm.devices.clear()
    main._device_seen.clear()
    main._play_keep(session)
    run(plugin.play_state())
    check("registered a third time", len(registered()), 3)
    check("play time as the new device", romm.play_devices[-1], next(iter(romm.devices)))

    # RomM not registering it: saves and play time still go, from no device.
    os.remove(main.DEVICE_FILE)
    main._device_seen.clear()
    romm.devices_down = 500
    write(b"while devices fail")
    check("sent anyway", (sync()["state"], device_of(romm.uploads[-1][0])), ("sent", None))
    main._play_keep(session)
    check("play time anyway", (run(plugin.play_state())["waiting"], romm.play_devices[-1]), (0, None))
    check("nothing kept", os.path.exists(main.DEVICE_FILE), False)
    romm.devices_down = 0

    # Back to a key that may not: the device kept is not used.
    write(b"registered again")
    sync()
    check("kept again", os.path.exists(main.DEVICE_FILE), True)
    romm.codes["DVCEKEYA"] = (84, GAMES + ["roms.user.read", "roms.user.write"])
    pair("DVCEKEYA", True)
    write(b"with the old key again")
    calls = len(romm.calls)
    check("sent from no device", (sync()["state"], device_of(romm.uploads[-1][0])), ("sent", None))
    check("no device asked about", [c for c in romm.calls[calls:] if "/api/devices" in c[1] or "downloaded" in c[1]], [])
    run(plugin.game_release(8000))
    os.remove(rom_file)


def test_input():
    # The Steam Input layout per console: kept by the add-on, offered only when Steam has its template.
    templates = os.path.join(HOME, ".steam", "steam", "controller_base", "templates")
    os.makedirs(templates, exist_ok=True)
    for name in ("controller_neptune_gamepad_joystick.vdf", "controller_neptune_gamepad+mouse.vdf"):
        with open(os.path.join(templates, name), "w") as f:
            f.write('"controller_mappings" {}')
    with main._lock:
        kept = main._read_games()
        data = json.loads(json.dumps(kept))
        data["games"].update({
            "5101": {"rom_id": 51, "system": "nds", "installed": False, "files": [], "folders": []},
            "5102": {"rom_id": 52, "system": "nds", "installed": False, "files": [], "folders": []},
            "5201": {"rom_id": 53, "system": "wii", "installed": False, "files": [], "folders": []},
        })
        check("games written", main._write_games(data), True)
    with open(main.GAMES_FILE, "rb") as f:
        games_before = f.read()
    try:
        got = run(plugin.input_layouts())
        check("layouts listed", got["ok"], True)
        check("only templates Steam has", got["layouts"], ["gamepad", "mouse"])
        consoles = {c["system"]: c for c in got["consoles"]}
        check("nds with its two entries", (consoles["nds"]["count"], consoles["nds"]["layout"]), (2, ""))
        check("wii unchanged at first", (consoles["wii"]["count"], consoles["wii"]["layout"]), (1, ""))
        check("no console without entries", "ps2" in consoles, False)
        with open(os.path.join(templates, "RetroDECK_controller_steamdeck_neptune_full.vdf"), "w") as f:
            f.write('"controller_mappings" {}')
        check("RetroDECK's once installed", run(plugin.input_layouts())["layouts"], ["retrodeck", "gamepad", "mouse"])

        got = run(plugin.input_set_layout("nds", "mouse"))
        check("layout set", got["ok"], True)
        check("its template", got["url"], "template://controller_neptune_gamepad+mouse.vdf")
        check("for every nds entry", sorted(got["apps"]), [5101, 5102])
        check("remembered", {c["system"]: c["layout"] for c in run(plugin.input_layouts())["consoles"]}["nds"], "mouse")
        check("kept on disk", mode(main.LAYOUTS_FILE), 0o600)
        check("a new nds entry gets it", run(plugin.input_layout(5101)), {"ok": True, "url": "template://controller_neptune_gamepad+mouse.vdf"})
        check("a wii entry keeps Steam's", run(plugin.input_layout(5201)), {"ok": True, "url": ""})
        check("not one of the add-on's", run(plugin.input_layout(9999))["why"], "game")

        check("unknown layout refused", run(plugin.input_set_layout("nds", "nope"))["why"], "layout")
        check("console without entries refused", run(plugin.input_set_layout("ps2", "gamepad"))["why"], "system")
        check("odd console refused", run(plugin.input_set_layout("../nds", "gamepad"))["why"], "system")
        check("refusal leaves the choice", run(plugin.input_layout(5102))["url"], "template://controller_neptune_gamepad+mouse.vdf")

        # A template Steam no longer has: nothing is applied, and the choice reads as unchanged.
        os.remove(os.path.join(templates, "controller_neptune_gamepad+mouse.vdf"))
        check("missing template not applied", run(plugin.input_layout(5101))["url"], "")
        check("missing template not offered", {c["system"]: c["layout"] for c in run(plugin.input_layouts())["consoles"]}["nds"], "")
        check("missing template refused", run(plugin.input_set_layout("wii", "mouse"))["why"], "layout")

        got = run(plugin.input_set_layout("wii", "retrodeck"))
        check("RetroDECK's for wii", (got["url"], got["apps"]), ("template://RetroDECK_controller_steamdeck_neptune_full.vdf", [5201]))
        got = run(plugin.input_set_layout("wii", ""))
        check("back to unchanged", (got["ok"], got["url"]), (True, ""))
        check("unchanged applies nothing", run(plugin.input_layout(5201))["url"], "")

        # A choice that cannot be kept on disk is said, and the one before stays.
        real = main._write_private
        main._write_private = lambda path, data: False
        try:
            check("disk refusal said", run(plugin.input_set_layout("wii", "gamepad"))["why"], "disk")
        finally:
            main._write_private = real
        check("disk refusal keeps the old", run(plugin.input_layout(5201))["url"], "")

        with open(main.LAYOUTS_FILE, "w") as f:
            f.write("not json")
        check("unreadable file reads as unchanged", run(plugin.input_layout(5102))["url"], "")
        with open(main.GAMES_FILE, "rb") as f:
            check("games file untouched", f.read(), games_before)
    finally:
        with main._lock:
            main._write_games(kept)
        shutil.rmtree(os.path.join(HOME, ".steam"), ignore_errors=True)
        if os.path.exists(main.LAYOUTS_FILE):
            os.remove(main.LAYOUTS_FILE)


try:
    test_server()
    test_move_in()
    test_key()
    test_detail()
    test_collections()
    test_cover()
    test_steamgriddb()
    test_sgdb_shared()
    test_launcher()
    test_download()
    test_archive()
    test_saves()
    test_wii_saves()
    test_emulators()
    test_versions()
    test_bios()
    test_bios_all()
    test_adopt()
    test_play()
    test_gone()
    test_queue()
    test_moved_folders()
    test_discs()
    test_device()
    test_input()
    print("ok: %d checks" % checks)
finally:
    server.shutdown()
    other.shutdown()
    sgdb_server.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)

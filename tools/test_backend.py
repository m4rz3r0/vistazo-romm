#!/usr/bin/env python3
"""Backend checks against a pretend RomM, all inside a throwaway folder: `python3 tools/test_backend.py`.

Nothing here talks to a real server or touches the real home: `decky` is a stand-in pointing at
the temporary folder, and "flatpak" is a script that only writes down what it was started with."""
import asyncio
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
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
        self.redirect_to = None
        self.refuse = set()  # rom ids answered with 401
        self.covers = {}  # file name -> bytes
        self.searches = []  # what the games list was asked for
        self.saves = []  # RomM's saves: dicts with "data"
        self.uploads = []  # (query, file name) of each save sent
        self.short_save = False  # send less of a save than announced
        self.firmware = []  # RomM's BIOS files: dicts with "data"
        self.meanwhile = None  # called while a save is on its way


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


def add_save(rom_id, data, name, emulator="retroarch-melondsds", slot="autosave", **more):
    """A save as RomM keeps it; each one a little newer than the last."""
    import hashlib
    SAVE_CLOCK[0] += 1
    stem, ext = os.path.splitext(name)
    save = {
        "id": 100 + SAVE_CLOCK[0], "rom_id": rom_id, "file_name": "%s [2026-11-13 %02d]%s" % (stem, SAVE_CLOCK[0], ext),
        "file_name_no_tags": name, "file_extension": ext.lstrip("."), "file_size_bytes": len(data),
        "emulator": emulator, "slot": slot, "content_hash": hashlib.md5(data).hexdigest(),
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
            return self.reply(201, add_save(int(q["rom_id"][0]), data, name, q.get("emulator", [None])[0], q.get("slot", [None])[0]))
        body = json.loads(raw) if raw else None
        romm.calls.append((method, self.path, bearer))
        parts = self.path.split("?")[0].strip("/").split("/")
        if self.path == "/api/client-tokens/exchange":
            hit = romm.codes.get((body or {}).get("code"))
            if not hit:
                return self.reply(404)
            token = "tok-%d" % hit[0]
            romm.tokens[token] = hit[0]
            return self.reply(200, {"id": hit[0], "raw_token": token, "scopes": hit[1]})
        if method == "DELETE" and parts[:2] == ["api", "client-tokens"]:
            romm.deleted.append((int(parts[2]), bearer))
            return self.reply(204)
        if bearer not in romm.tokens:
            return self.reply(401)
        if self.path == "/api/users/me":
            return self.reply(200, {"id": 1, "ra_progression": {"results": [
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
        if state.get("job", {}).get("state") != "downloading":
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
    check("library row", next(g for g in got["games"] if g["rom_id"] == 30), {"rom_id": 30, "name": "Game 30", "platform": "Nintendo DS", "system": "nds", "size": 300000})
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
    romm.roms[20].pop("ss_metadata")
    check("no media kept", run(plugin.game_pictures(20)), {"ok": True})
    check("unknown game", run(plugin.game_pictures(999))["ok"], False)
    got = run(plugin.game_launcher())
    check("launcher ready", (got["ok"], got["exe"], mode(main.LAUNCHER)), (True, main.LAUNCHER, 0o700))
    check("no games yet", run(plugin.game_list()), {})


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
    check("cut short leaves nothing", (os.path.exists(target), leftovers()), (False, []))

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
    check("in its own folder", (run(plugin.bios_fetch(5100))["state"], os.path.isfile(os.path.join(bios, "pcsx2", "bios", "scph.bin"))), ("ok", True))
    for appid in (5000, 5100):
        run(plugin.game_release(appid))
    os.remove(os.path.join(ROMS, "nds", "Bios.nds"))
    os.remove(os.path.join(ROMS, "ps2", "Bios.iso"))


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
    os.makedirs(os.path.join(ROMS, "wii"), exist_ok=True)
    romm.roms[40] = rom(40, "Wii.rvz", slug="wii")
    write(DATA, os.path.join(ROMS, "wii", "Wii.rvz"))
    run(plugin.game_take(4000, 40))
    check("console not handled yet", run(plugin.save_sync(4000))["state"], "unsupported")
    romm.roms[41] = rom(41, "Away.nds")
    run(plugin.game_take(4100, 41))
    check("game not here", run(plugin.save_sync(4100))["state"], "not-installed")
    for appid in (500, 4000, 4100):
        run(plugin.game_release(appid))
    os.remove(rom_file)
    os.remove(os.path.join(ROMS, "wii", "Wii.rvz"))
    check("the game's own files were never touched by all this", os.path.isfile(save), True)


try:
    test_server()
    test_move_in()
    test_key()
    test_detail()
    test_cover()
    test_launcher()
    test_download()
    test_archive()
    test_saves()
    test_bios()
    print("ok: %d checks" % checks)
finally:
    server.shutdown()
    other.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)

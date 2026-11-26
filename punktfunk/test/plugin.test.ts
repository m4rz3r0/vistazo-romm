import { expect, test } from "bun:test";
import * as fs from "node:fs";
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";
import type { DownloadRow } from "@punktfunk/plugin-kit";
import type { Shortcut } from "@punktfunk/plugin-kit/library";
import { ask, type Catalog, entriesFrom, makeTracker, readCatalog, readPlatforms } from "../src/plugin.js";

const LAUNCHER = "/home/u/homebrew/data/VistazoRomM/vistazo-launch";
const PLAYER = "/home/u/homebrew/data/VistazoRomM/vistazo-play";

const game = (key: number, more: Partial<Catalog["games"][number]> = {}) => ({
	key,
	appid: 0,
	rom_id: key,
	name: `Game ${key}`,
	platform: "Nintendo Wii",
	system: "wii",
	size: 1000,
	installed: false,
	cover: "",
	...more,
});

const shortcut = (appid: number, name: string, exe = `"${LAUNCHER}"`, hidden = false): Shortcut => ({
	appid,
	name,
	exe,
	hidden,
});

test("one entry per game, installed or not, and the other shortcuts as before", () => {
	const catalog: Catalog = {
		launcher: LAUNCHER,
		player: PLAYER,
		games: [
			game(17, { appid: 2259144028, rom_id: 17, installed: true, size: 2286070096 }),
			game(26, { appid: 3456097576, size: 724932424 }),
			game(40),
			game(41, { appid: 4000000000 }), // its entry was removed in Steam
		],
	};
	const shortcuts = new Map([
		[2259144028, shortcut(2259144028, "PokéPark Wii: Pikachu's Adventure")],
		[3456097576, shortcut(3456097576, "Wii Sports")],
		[2501829966, shortcut(2501829966, "RetroDECK", '"/usr/bin/flatpak" run net.retrodeck.retrodeck')],
		[2600000000, shortcut(2600000000, "Hidden", "/x", true)],
	]);
	const entries = entriesFrom(catalog, shortcuts, []);
	expect(entries.map((e) => e.external_id)).toEqual(["17", "26", "40", "steam-2501829966"]);
	const [installed, missing, fresh, other] = entries;
	expect(installed).toMatchObject({
		title: "PokéPark Wii: Pikachu's Adventure",
		launch: { kind: "steam_appid", value: "9702949717247262720" },
		detect: { exe: LAUNCHER },
		platform: "Nintendo Wii",
	});
	expect(installed?.install).toBeUndefined();
	expect(missing).toMatchObject({
		title: "Wii Sports",
		launch: { kind: "steam_appid" },
		install: { state: "missing", size_bytes: 724932424 },
	});
	expect(fresh).toMatchObject({
		title: "Game 40",
		launch: {
			kind: "exec",
			value: "play",
			args: [
				{ name: "player", value: PLAYER },
				{ name: "game", value: "40" },
			],
		},
		detect: { exe: LAUNCHER },
		install: { state: "missing" },
	});
	expect(other).toMatchObject({
		title: "RetroDECK",
		detect: { exe: "/usr/bin/flatpak" },
		platform: "PC",
	});
});

test("without a catalog, every shortcut is listed as Steam's plugin did", () => {
	const shortcuts = new Map([[3456097576, shortcut(3456097576, "Wii Sports")]]);
	expect(entriesFrom(undefined, shortcuts, []).map((e) => e.external_id)).toEqual(["steam-3456097576"]);
});

test("another shortcut can be given its console", () => {
	const shortcuts = new Map([
		[3075036610, shortcut(3075036610, "Canal Mii", '"/usr/bin/flatpak"')],
		[2501829966, shortcut(2501829966, "RetroDECK", '"/usr/bin/flatpak"')],
	]);
	const entries = entriesFrom(undefined, shortcuts, [], new Map([[3075036610, "Wii"]]));
	expect(entries.map((e) => [e.title, e.platform])).toEqual([
		["Canal Mii", "Wii"],
		["RetroDECK", "PC"],
	]);
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vr-pf-"));
	const file = path.join(dir, "platforms.json");
	expect(readPlatforms(file).size).toBe(0);
	fs.writeFileSync(file, "[1]");
	expect(readPlatforms(file).size).toBe(0);
	fs.writeFileSync(file, JSON.stringify({ "3075036610": " Wii ", x: "PC", "5": "", "99999999999": "PS2" }));
	expect([...readPlatforms(file)]).toEqual([[3075036610, "Wii"]]);
	fs.rmSync(dir, { recursive: true });
});

test("the catalog file, read leniently", () => {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vr-pf-"));
	const file = path.join(dir, "catalog.json");
	expect(readCatalog(file)).toBeUndefined();
	fs.writeFileSync(file, "not json");
	expect(readCatalog(file)).toBeUndefined();
	fs.writeFileSync(
		file,
		JSON.stringify({ launcher: LAUNCHER, player: PLAYER, games: [game(1), { key: -1 }, "x", game(2, { appid: 2 ** 33 })] }),
	);
	expect(readCatalog(file)?.games.map((g) => g.key)).toEqual([1]);
	fs.rmSync(dir, { recursive: true });
});

test("downloads: told when they change, finished ones once, leftovers never", () => {
	const sent: DownloadRow[] = [];
	let clock = 0;
	const t = makeTracker({ set: (r) => sent.push(r) }, () => clock);
	// Already finished before the plugin started: nothing.
	t.seen([{ key: 9, state: "done", done: 5, total: 5 }]);
	expect(sent).toEqual([]);
	t.seen([{ key: 26, state: "queued", done: 0, total: 100 }]);
	t.seen([{ key: 26, state: "queued", done: 0, total: 100 }]);
	t.seen([{ key: 26, state: "downloading", done: 50, total: 100 }]);
	t.seen([{ key: 26, state: "done", done: 100, total: 100 }]);
	t.seen([{ key: 26, state: "done", done: 100, total: 100 }]);
	expect(sent.map((r) => [r.externalId, r.state, r.doneBytes])).toEqual([
		["26", "queued", 0],
		["26", "downloading", 50],
		["26", "done", 100],
	]);
	// A failure says why; a live row that goes away is cancelled.
	sent.length = 0;
	t.seen([{ key: 40, state: "failed", done: 0, total: 1, error: "No hay sitio en el disco para este juego." }]);
	t.seen([{ key: 41, state: "queued", done: 0, phase: "Creando la entrada en Steam" }]);
	t.seen([]);
	expect(sent.map((r) => [r.externalId, r.state, r.error ?? r.phase])).toEqual([
		["40", "failed", "No hay sitio en el disco para este juego."],
		["41", "queued", "Creando la entrada en Steam"],
		["41", "cancelled", "Creando la entrada en Steam"],
	]);
	// Asked: queued at once, done at once when the game is already here.
	sent.length = 0;
	t.asked("50");
	t.done("51", 7);
	expect(sent.map((r) => [r.externalId, r.state])).toEqual([
		["50", "queued"],
		["51", "done"],
	]);
	// Just asked: a poll without its row yet does not cancel it; later, gone means cancelled.
	sent.length = 0;
	t.seen([{ key: 51, state: "done", done: 7, total: 7 }]);
	expect(sent).toEqual([]);
	clock += 6_000;
	t.seen([{ key: 51, state: "done", done: 7, total: 7 }]);
	expect(sent.map((r) => [r.externalId, r.state])).toEqual([["50", "cancelled"]]);
	// Garbage is skipped.
	sent.length = 0;
	t.seen([{ key: "x", state: "done" }, { key: 3, state: "exploded" }, null as never].filter(Boolean));
	expect(sent).toEqual([]);
});

test("a question over the socket, and no answer is an error", async () => {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vr-pf-"));
	const sock = path.join(dir, "vistazo.sock");
	const heard: string[] = [];
	const server = net.createServer((c) => {
		c.setEncoding("utf8");
		c.on("data", (d) => {
			heard.push(String(d).trim());
			c.end(`${JSON.stringify({ ok: true, appid: 7 })}\n`);
		});
	});
	await new Promise<void>((r) => server.listen(sock, r));
	expect(await ask({ ask: "appid", key: 40 }, sock)).toEqual({ ok: true, appid: 7 });
	expect(heard).toEqual(['{"ask":"appid","key":40}']);
	await new Promise<void>((r) => server.close(() => r()));
	await expect(ask({ ask: "appid", key: 40 }, sock, 500)).rejects.toBeDefined();
	fs.rmSync(dir, { recursive: true });
});

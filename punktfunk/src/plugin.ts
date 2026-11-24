// Vistazo RomM for Punktfunk: RomM's games in Punktfunk's library, downloaded from the TV.
//
// The Decky add-on does everything that touches a game: it brings the file into RetroDECK's
// folder, makes and keeps the Steam entry that starts it through `vistazo-launch`, and keeps the
// saves in step with RomM. It writes a catalog beside itself and answers on a socket; this plugin
// only turns that catalog into library entries, passes the host's install asks on, and reports
// how each download goes.
//
// One entry per RomM game, installed or not: the host's Steam plugin must not list non-Steam
// shortcuts too (its "Include non-Steam shortcuts" off), so this plugin lists the other shortcuts
// as well, the way it did.
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";
import {
	type DownloadReporter,
	type DownloadRow,
	type DownloadState,
	InstallRefused,
	NotMyTitle,
} from "@punktfunk/plugin-kit";
import {
	ART_KINDS,
	defineLibraryPlugin,
	fileUrl,
	findGridArtFile,
	isDir,
	isFile,
	listDir,
	parseShortcuts,
	readBytesCapped,
	readTextCapped,
	type Shortcut,
	shortcutGameId,
} from "@punktfunk/plugin-kit/library";
import type { ProviderEntry } from "@punktfunk/plugin-kit/wire";
import { Duration, Effect, Schema } from "effect";

const NAME = "vistazo-romm";
const home = () => os.homedir();
const dataDir = () => path.join(home(), "homebrew/data/VistazoRomM");
const pfDir = () => path.join(dataDir(), "punktfunk");
const catalogFile = () => path.join(pfDir(), "catalog.json");
const socketFile = () => path.join(pfDir(), "vistazo.sock");
const userdata = () => path.join(home(), ".local/share/Steam/userdata");
const MAX_CATALOG = 8 * 1024 * 1024;
const MAX_SHORTCUTS = 16 * 1024 * 1024;

/** One game as the add-on lists it. */
export interface CatalogGame {
	readonly key: number;
	readonly appid: number;
	readonly rom_id: number;
	readonly name: string;
	readonly platform: string;
	readonly system: string;
	readonly size: number;
	readonly installed: boolean;
	readonly cover: string;
}

export interface Catalog {
	readonly launcher: string;
	readonly player: string;
	readonly games: ReadonlyArray<CatalogGame>;
}

const num = (v: unknown) =>
	typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : 0;
const str = (v: unknown) => (typeof v === "string" ? v : "");

/** The add-on's catalog, or `undefined` while there is none to read. */
export const readCatalog = (file = catalogFile()): Catalog | undefined => {
	const text = readTextCapped(file, MAX_CATALOG);
	if (!text) return undefined;
	try {
		const data = JSON.parse(text) as Record<string, unknown>;
		if (!Array.isArray(data.games)) return undefined;
		const games = data.games
			.filter((g): g is Record<string, unknown> => !!g && typeof g === "object")
			.map((g) => ({
				key: num(g.key),
				appid: num(g.appid),
				rom_id: num(g.rom_id),
				name: str(g.name),
				platform: str(g.platform),
				system: str(g.system),
				size: num(g.size),
				installed: g.installed === true,
				cover: str(g.cover),
			}))
			.filter((g) => g.key > 0 && g.appid < 2 ** 32);
		return {
			launcher: str(data.launcher),
			player: str(data.player),
			games,
		};
	} catch {
		return undefined;
	}
};

/** Every signed-in Steam account's config folder (shortcuts.vdf and grid/ art). */
const configDirs = (root = userdata()) =>
	listDir(root)
		.map((acct) => path.join(root, acct, "config"))
		.filter(isDir);

/** Every non-Steam shortcut, the first one seen of each appid. */
const shortcutsIn = (dirs: ReadonlyArray<string>) => {
	const out = new Map<number, Shortcut>();
	for (const dir of dirs) {
		const buf = readBytesCapped(path.join(dir, "shortcuts.vdf"), MAX_SHORTCUTS);
		if (!buf) continue;
		for (const sc of parseShortcuts(buf)) if (!out.has(sc.appid)) out.set(sc.appid, sc);
	}
	return out;
};

const gridArt = (appid: number, dirs: ReadonlyArray<string>) => {
	const out: Record<string, string> = {};
	for (const kind of ART_KINDS) {
		const local = dirs.map((d) => findGridArtFile(d, appid, kind)).find((p) => !!p);
		if (local) out[kind] = fileUrl(local);
	}
	return out;
};

/** What the host watches to tell the game is running: the shortcut's program (as Steam's plugin does). */
const exeOf = (exe: string) => {
	const trimmed = exe.trim();
	const quoted = trimmed.startsWith('"') ? trimmed.slice(1).split('"')[0] : undefined;
	return quoted ?? trimmed.split(" ")[0] ?? "";
};

/** The library entries: the add-on's games, then every other non-Steam shortcut. */
export const entriesFrom = (
	catalog: Catalog | undefined,
	shortcuts: ReadonlyMap<number, Shortcut>,
	dirs: ReadonlyArray<string>,
): ProviderEntry[] => {
	const out: ProviderEntry[] = [];
	const mine = new Set<number>();
	for (const g of catalog?.games ?? []) {
		const sc = g.appid ? shortcuts.get(g.appid) : undefined;
		// An entry Steam no longer has (removed by hand) can start nothing: left out.
		if (g.appid && !sc) continue;
		if (g.appid) mine.add(g.appid);
		const exe = sc ? exeOf(sc.exe) : catalog?.launcher;
		const art = g.appid
			? gridArt(g.appid, dirs)
			: g.cover && isFile(g.cover)
				? { portrait: fileUrl(g.cover) }
				: {};
		out.push({
			external_id: String(g.key),
			title: sc?.name || g.name || `RomM ${g.key}`,
			launch: g.appid
				? { kind: "steam_appid", value: shortcutGameId(g.appid) }
				: {
						kind: "exec",
						value: "play",
						args: [
							{ name: "player", value: catalog?.player ?? "" },
							{ name: "game", value: String(g.key) },
						],
					},
			...(Object.keys(art).length ? { art } : {}),
			...(exe ? { detect: { exe } } : {}),
			...(g.platform ? { platform: g.platform } : {}),
			...(g.installed
				? {}
				: { install: { state: "missing" as const, ...(g.size ? { size_bytes: g.size } : {}) } }),
		});
	}
	for (const sc of shortcuts.values()) {
		if (mine.has(sc.appid) || sc.hidden) continue;
		const exe = exeOf(sc.exe);
		const art = gridArt(sc.appid, dirs);
		out.push({
			external_id: `steam-${sc.appid}`,
			title: sc.name,
			launch: { kind: "steam_appid", value: shortcutGameId(sc.appid) },
			...(Object.keys(art).length ? { art } : {}),
			...(exe ? { detect: { exe } } : {}),
			platform: "PC",
		});
	}
	return out;
};

// ── The add-on's socket ─────────────────────────────────────────────────────────────────────

type Answer = Record<string, unknown> & { ok?: boolean; say?: string };

/** One question to the add-on: a line of JSON there, a line of JSON back. */
export const ask = (
	question: Record<string, unknown>,
	file = socketFile(),
	timeoutMs = 10_000,
): Promise<Answer> =>
	new Promise((resolve, reject) => {
		const sock = net.createConnection(file);
		let got = "";
		const timer = setTimeout(() => {
			sock.destroy();
			reject(new Error("Vistazo RomM did not answer"));
		}, timeoutMs);
		sock.setEncoding("utf8");
		sock.on("connect", () => sock.write(`${JSON.stringify(question)}\n`));
		sock.on("data", (chunk) => {
			got += chunk;
			if (got.length > 4 * 1024 * 1024) sock.destroy(new Error("answer too long"));
		});
		sock.on("error", (e) => {
			clearTimeout(timer);
			reject(e);
		});
		sock.on("close", () => {
			clearTimeout(timer);
			const line = got.split("\n")[0] ?? "";
			try {
				const answer = JSON.parse(line) as unknown;
				if (answer && typeof answer === "object") resolve(answer as Answer);
				else reject(new Error("Vistazo RomM answered nothing"));
			} catch (e) {
				reject(e);
			}
		});
	});

const ASLEEP =
	"Vistazo RomM no contesta: Steam tiene que estar abierto con Decky en el PC.";

/** The host's ask for one of our games, passed on to the add-on. */
const act = (verb: string, externalId: string, after?: (a: Answer) => void) =>
	Effect.gen(function* () {
		if (!/^\d{1,12}$/.test(externalId)) {
			return yield* Effect.fail(new NotMyTitle({ externalId }));
		}
		const answer = yield* Effect.tryPromise({
			try: () => ask({ ask: verb, key: Number(externalId) }),
			catch: () => new InstallRefused({ message: ASLEEP }),
		});
		if (!answer.ok) {
			return yield* Effect.fail(
				new InstallRefused({ message: str(answer.say) || "Vistazo RomM no pudo hacerlo." }),
			);
		}
		after?.(answer);
	});

// ── Downloads ───────────────────────────────────────────────────────────────────────────────

const STATES: ReadonlyArray<DownloadState> = [
	"queued",
	"downloading",
	"paused",
	"installing",
	"done",
	"failed",
	"cancelled",
];
const ASK_GRACE_MS = 5_000;
const live = (s: DownloadState) => s === "queued" || s === "downloading" || s === "installing";

/**
 * Turns the add-on's rows into reports: a row is set again only when it changed, a finished one
 * only when this plugin saw it going (not one left over from before it started), and a live one
 * that disappears is reported cancelled.
 */
export const makeTracker = (reporter: Pick<DownloadReporter, "set">, now = () => Date.now()) => {
	const last = new Map<string, DownloadRow>();
	// When the host last asked for each: a poll already under way then may not have its row yet.
	const askedAt = new Map<string, number>();
	let first = true;
	const set = (row: DownloadRow) => {
		last.set(row.externalId, row);
		reporter.set(row);
	};
	return {
		/** Called with every poll's rows. */
		seen: (rows: ReadonlyArray<Record<string, unknown>>) => {
			const seenNow = new Set<string>();
			for (const r of rows) {
				const state = STATES.find((s) => s === r.state);
				const key = num(r.key);
				if (!state || !key) continue;
				const row: DownloadRow = {
					externalId: String(key),
					state,
					doneBytes: num(r.done),
					...(num(r.total) ? { totalBytes: num(r.total) } : {}),
					...(str(r.phase) ? { phase: str(r.phase).slice(0, 80) } : {}),
					...(str(r.error) ? { error: str(r.error) } : {}),
				};
				seenNow.add(row.externalId);
				const before = last.get(row.externalId);
				const finished = !live(state) && state !== "paused";
				// Finished before this plugin started, or already told: nothing to tell.
				if (finished && ((first && !before) || before?.state === state)) {
					last.set(row.externalId, row);
					continue;
				}
				if (
					before &&
					before.state === row.state &&
					before.doneBytes === row.doneBytes &&
					before.totalBytes === row.totalBytes &&
					before.phase === row.phase &&
					before.error === row.error
				) {
					continue;
				}
				set(row);
			}
			for (const [id, before] of last) {
				if (seenNow.has(id) || now() - (askedAt.get(id) ?? -Infinity) < ASK_GRACE_MS) continue;
				if (live(before.state)) set({ ...before, state: "cancelled" });
				last.delete(id);
			}
			first = false;
		},
		/** The add-on said the game is already here: done at once. */
		done: (externalId: string, bytes: number) =>
			set({ externalId, state: "done", doneBytes: bytes, ...(bytes ? { totalBytes: bytes } : {}) }),
		/** Asked and not reported yet: queued, so the host's wait has news. */
		asked: (externalId: string) => {
			askedAt.set(externalId, now());
			const before = last.get(externalId);
			if (!before || !live(before.state)) set({ externalId, state: "queued", doneBytes: before?.doneBytes ?? 0 });
		},
	};
};

let tracker: ReturnType<typeof makeTracker> | undefined;

const sizeOf = (externalId: string) =>
	readCatalog()?.games.find((g) => String(g.key) === externalId)?.size ?? 0;

const poll = (reporter: DownloadReporter) =>
	Effect.gen(function* () {
		const t = makeTracker(reporter);
		tracker = t;
		let warned = false;
		for (;;) {
			const answer = yield* Effect.promise(() =>
				ask({ ask: "downloads" }, socketFile(), 5_000).catch(() => undefined),
			);
			if (answer?.ok && Array.isArray(answer.rows)) {
				t.seen(answer.rows as Record<string, unknown>[]);
				warned = false;
			} else if (!warned && isDir(pfDir())) {
				warned = true;
				yield* Effect.logWarning("Vistazo RomM is not answering on its socket");
			}
			yield* Effect.sleep(Duration.seconds(1));
		}
	});

export const plugin = defineLibraryPlugin({
	name: NAME,
	title: "Vistazo RomM",
	configSchema: Schema.Struct({}),
	detect: () => Effect.sync(() => isDir(userdata()) || isFile(catalogFile())),
	scan: () =>
		Effect.sync(() => {
			const dirs = configDirs();
			return entriesFrom(readCatalog(), shortcutsIn(dirs), dirs);
		}),
	watchDirs: () => [pfDir(), ...configDirs()].filter(isDir),
	debounce: Duration.seconds(1),
	minInterval: Duration.seconds(4),
	pollInterval: Duration.minutes(5),
	install: {
		start: ({ externalId }) =>
			act("install", externalId, (a) =>
				a.installed === true
					? tracker?.done(externalId, sizeOf(externalId))
					: tracker?.asked(externalId),
			),
		pause: ({ externalId }) => act("pause", externalId),
		cancel: ({ externalId }) => act("cancel", externalId),
		uninstall: ({ externalId }) => act("uninstall", externalId),
	},
	downloads: (reporter) => poll(reporter),
});

export const _internals = { exeOf, configDirs, shortcutsIn };

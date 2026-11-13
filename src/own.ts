import { callable } from "@decky/api";
import { safe } from "./safe";

// RomM games this add-on looks after itself (for those who turn it on). While Tender is installed
// it keeps every game it has added to Steam for itself: it stops their launch to run its own
// checks and writes its own launch options back. So those are left alone. The add-on makes a
// second entry of its own for a game, which Tender neither knows nor changes, and that one is
// its own from start to finish: its file comes from RomM with the add-on's key and it starts
// through the add-on's launcher. Taking it away removes that entry and only the files it brought.
declare const SteamClient: any;
declare const appStore: any;
declare const SteamUIStore: any;

export interface GameState {
  ok?: boolean;
  why?: string;
  managed: boolean;
  rom_id: number;
  installed: boolean;
  removable: boolean;
  options?: string;
  job?: { state: string; done: number; total: number };
}
type Reply = { ok: boolean; why?: string };

const list = callable<[], Record<string, number>>("game_list");
const launcher = callable<[], Reply & { exe: string; start_dir: string }>("game_launcher");
const cover = callable<[romId: number], Reply & { data: string; kind: string }>("game_cover");
const take = callable<[appid: number, romId: number, exe: string, dir: string, options: string], GameState>("game_take");
const release = callable<[appid: number], Reply>("game_release");
const download = callable<[appid: number], GameState>("game_download");
const cancel = callable<[appid: number], GameState>("game_cancel");
const remove = callable<[appid: number], GameState>("game_remove");
export const gameState = callable<[appid: number], GameState>("game_state");
const tidy = callable<[], Reply>("game_tidy");

// Steam number -> RomM number of the games that are the add-on's. The backend's record is the truth;
// this copy is read from it and told about every change, so whatever shows a game follows it.
const owned = new Map<number, number>();
const watchers = new Set<() => void>();
const changed = () => {
  watchers.forEach((fn) => fn());
  // Vistazo draws these games: it is told too.
  safe(() => (window as any).Vistazo?.changed(), undefined);
};
export function onOwnChange(fn: () => void): () => void {
  watchers.add(fn);
  return () => watchers.delete(fn);
}
let loaded: Promise<void> | null = null;
export function loadOwned(again = false): Promise<void> {
  if (again) loaded = null;
  return (loaded ??= list()
    .then((all) => {
      owned.clear();
      for (const [appid, romId] of Object.entries(all ?? {})) owned.set(Number(appid), Number(romId));
      changed();
      mend();
    })
    .catch((e) => {
      loaded = null;
      console.warn("[Vistazo RomM] own games", e);
    }));
}
export const ownedRom = (appid: number) => owned.get(appid) ?? 0;
export const ownedApps = () => [...owned.keys()];
export const ownedApp = (romId: number) => {
  for (const [appid, rom] of owned) if (rom === romId) return appid;
  return 0;
};
const isRunning = (appid: number) =>
  safe(() => (SteamUIStore.RunningApps as any[]).some((a) => Number(a.appid) === appid), false);

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
async function untilKnown(appid: number) {
  for (let i = 0; i < 20 && !safe(() => appStore.GetAppOverviewByAppID(appid), null); i++) await wait(100);
}

// The pictures Steam shows for an entry, by Steam's own numbers: cover, wide picture behind the
// page, and logo.
const COVER = 0;
const ART: [number, string][] = [
  [1, "GetCustomHeroImageURLs"],
  [2, "GetCustomLogoImageURLs"],
  [3, "GetCustomLandscapeImageURLs"],
];

// One of an entry's pictures as Steam wants it handed over: [base64, kind], or null.
async function picture(urls: string[]): Promise<[string, string] | null> {
  for (const url of urls) {
    try {
      const blob = await (await fetch(url)).blob();
      const kind = blob.type === "image/png" ? "png" : blob.type === "image/jpeg" ? "jpg" : "";
      if (!kind || blob.size > 8 * 1024 * 1024) continue;
      const data = await new Promise<string>((done, fail) => {
        const reader = new FileReader();
        reader.onload = () => done(String(reader.result).split(",")[1] ?? "");
        reader.onerror = () => fail(reader.error);
        reader.readAsDataURL(blob);
      });
      if (data) return [data, kind];
    } catch {
      /* the next one */
    }
  }
  return null;
}

// Which Steam entry each of the add-on's came from, kept on the Deck: its pictures may arrive later.
const FROM = "vistazo:own-from";
const sources = (): Record<string, number> => safe(() => JSON.parse(localStorage.getItem(FROM) ?? "{}") ?? {}, {});
function setSource(appid: number, from: number | null) {
  const all = sources();
  if (from) all[appid] = from;
  else delete all[appid];
  safe(() => localStorage.setItem(FROM, JSON.stringify(all)), undefined);
}

// The wide picture and the logo of the entry the add-on's came from, for the ones its own lacks
// (they are on the Deck already).
async function borrowArt(appid: number, from: number) {
  const mine = safe(() => appStore.GetAppOverviewByAppID(appid), null);
  const source = safe(() => appStore.GetAppOverviewByAppID(from), null);
  if (!mine || !source) return;
  for (const [type, method] of ART) {
    if (safe(() => (appStore[method](mine) as string[]).length, 0)) continue;
    const got = await picture(safe(() => appStore[method](source) as string[], []));
    if (got) safe(() => SteamClient.Apps.SetCustomArtworkForApp(appid, got[0], got[1], type), undefined);
  }
}
// Asked again when the entry's page shows: the other entry may have got its pictures since.
export const refreshArt = (appid: number) => {
  const from = sources()[appid];
  if (from) borrowArt(appid, from).catch(() => undefined);
};

// The new entry looks like the one it comes from; its cover comes from RomM.
async function dress(appid: number, romId: number, from: number) {
  if (from) {
    setSource(appid, from);
    await borrowArt(appid, from);
  }
  const c = await cover(romId).catch(() => null);
  if (c?.ok) safe(() => SteamClient.Apps.SetCustomArtworkForApp(appid, c.data, c.kind, COVER), undefined);
}

// Makes the add-on's own entry for a RomM game and starts bringing its file. `from` is the Steam
// entry it was asked from (Tender's). Returns why it could not, or null when it is under way.
// Nothing is left in Steam when it fails.
export async function addOwn(romId: number, name: string, from = 0, fetchFile = true): Promise<string | null> {
  await loadOwned();
  if (ownedApp(romId)) return null;
  const l = await launcher();
  if (!l.ok) return l.why ?? "error";
  // Steam takes little from this call: the name, the program and its folder are set after it.
  const appid = Number(await SteamClient.Apps.AddShortcut(name, l.exe, "", "")) || 0;
  if (!appid) return "steam";
  await untilKnown(appid);
  SteamClient.Apps.SetShortcutName(appid, name);
  SteamClient.Apps.SetShortcutExe(appid, l.exe);
  SteamClient.Apps.SetShortcutStartDir(appid, l.start_dir);
  const taken = await take(appid, romId, "", "", "").catch(() => null);
  if (!taken?.ok || !taken.options) {
    SteamClient.Apps.RemoveShortcut(appid);
    return taken?.why ?? "error";
  }
  // All Steam is told about the game: its number in RomM.
  SteamClient.Apps.SetAppLaunchOptions(appid, taken.options);
  owned.set(appid, romId);
  changed();
  dress(appid, romId, from).catch((e) => console.warn("[Vistazo RomM] pictures", e));
  if (fetchFile && !taken.installed) await download(appid).catch(() => null);
  return null;
}

// Games of the add-on's whose Steam entry is gone (removed by hand in Steam): what the add-on keeps of
// them is of no use any more. Clearing them is the player's call, never done unasked.
export const lostOwn = () => ownedApps().filter((appid) => !safe(() => appStore.GetAppOverviewByAppID(appid), null));
export async function clearLostOwn(): Promise<string | null> {
  let why: string | null = null;
  for (const appid of lostOwn()) {
    const state = await gameState(appid).catch(() => null);
    if (state?.removable) {
      const gone = await remove(appid).catch(() => null);
      if (!gone?.ok) {
        why = gone?.why ?? "error";
        continue;
      }
    }
    const freed = await release(appid).catch(() => null);
    if (!freed?.ok && freed?.why !== "game") {
      why = freed?.why ?? "error";
      continue;
    }
    owned.delete(appid);
  }
  changed();
  return why;
}

// Entries made before the launcher moved (it used to live in Vistazo's own folder) are pointed
// at where it is now. Setting the same values again changes nothing.
let mended = false;
async function mend() {
  if (mended) return;
  mended = true;
  const l = await launcher().catch(() => null);
  let all = !!l?.ok;
  for (const appid of l?.ok ? owned.keys() : []) {
    // Steam may not know its entries yet right after it starts: tried again the next time.
    if (!safe(() => appStore.GetAppOverviewByAppID(appid), null)) {
      all = false;
      continue;
    }
    safe(() => SteamClient.Apps.SetShortcutExe(appid, l!.exe), undefined);
    safe(() => SteamClient.Apps.SetShortcutStartDir(appid, l!.start_dir), undefined);
  }
  // Only when nothing starts through the old launcher any more can what was left beside it go.
  if (all) tidy().catch(() => undefined);
  else {
    mended = false;
    if (l?.ok && ++mendTries < 30) setTimeout(mend, 10000);
  }
}
let mendTries = 0;

// RomM's whole library as entries in Steam, the way Steam's own games sit there: each game an
// entry (its file is brought when the player asks for it on its page), gathered in one Steam
// collection per console. Run again, it adds what is new in RomM and leaves the rest as it is.
declare const collectionStore: any;
const library = callable<[], Reply & { games: { rom_id: number; name: string; platform: string; system: string; size: number }[] }>("romm_library");
const COLLECTION = "RomM: "; // a collection's name: "RomM: Nintendo DS"

async function gather(byPlatform: Map<string, number[]>) {
  if (typeof collectionStore === "undefined") return;
  for (const [platform, appids] of byPlatform) {
    const overviews = appids.map((id) => safe(() => appStore.GetAppOverviewByAppID(id), null)).filter(Boolean);
    if (!overviews.length) continue;
    const name = COLLECTION + platform;
    const existing = (collectionStore.userCollections as any[]).find((c) => String(c.displayName).toLowerCase() === name.toLowerCase());
    const collection = existing ?? collectionStore.NewUnsavedCollection(name, undefined, []);
    const have = new Set<number>((collection.allApps ?? []).map((o: any) => Number(o.appid)));
    const missing = overviews.filter((o: any) => !have.has(Number(o.appid)));
    if (!existing || missing.length) {
      collection.AsDragDropCollection().AddApps(missing);
      await collection.Save();
    }
  }
}

// A collection of the add-on's left without games goes too (never one of another program's:
// Tender's carry the device's name in brackets).
async function tidyCollections() {
  if (typeof collectionStore === "undefined") return;
  for (const c of [...(collectionStore.userCollections as any[])]) {
    const name = String(c.displayName ?? "");
    if (name.startsWith(COLLECTION) && !name.includes("(") && !(c.allApps ?? []).length) await c.Delete();
  }
}

// Which of Tender's entries is which RomM game, as Tender itself says (while it is installed):
// the add-on's entries take their wide picture and logo from them.
declare const DeckyBackend: any;
async function tenderEntries(): Promise<Map<number, number>> {
  const out = new Map<number, number>();
  try {
    const map = await DeckyBackend.call("loader/call_plugin_method", "Tender", "get_app_id_rom_id_map");
    for (const [appid, romId] of Object.entries(map ?? {})) out.set(Number(romId), Number(appid));
  } catch {
    /* no Tender: nothing to borrow from */
  }
  return out;
}

let syncing = false;
export async function syncLibrary(step: (done: number, total: number) => void): Promise<string | null> {
  if (syncing) return "busy";
  syncing = true;
  try {
    const lib = await library().catch(() => null);
    if (!lib?.ok) return lib?.why ?? "error";
    await loadOwned(true);
    // Only consoles whose folder in RetroDECK is known: an entry that could never start helps nobody.
    const games = lib.games.filter((g) => g.system);
    let done = 0;
    let why: string | null = null;
    for (const g of games) {
      if (!ownedApp(g.rom_id)) why = (await addOwn(g.rom_id, g.name, 0, false)) ?? why;
      step(++done, games.length);
    }
    const byPlatform = new Map<string, number[]>();
    for (const g of games) {
      const appid = ownedApp(g.rom_id);
      if (appid && g.platform) byPlatform.set(g.platform, [...(byPlatform.get(g.platform) ?? []), appid]);
    }
    await gather(byPlatform).catch((e) => console.warn("[Vistazo RomM] collections", e));
    // Pictures from Tender's entries of the same games, for the ones that have none yet.
    const theirs = await tenderEntries();
    for (const g of games) {
      const [appid, from] = [ownedApp(g.rom_id), theirs.get(g.rom_id) ?? 0];
      if (!appid || !from) continue;
      if (!sources()[appid]) setSource(appid, from);
      await borrowArt(appid, sources()[appid]).catch(() => undefined);
    }
    changed();
    return why;
  } finally {
    syncing = false;
  }
}

// Takes away the entries whose game is not on the Deck (nothing is deleted from the disk): what
// stays is what the player has installed.
export async function dropNotInstalled(): Promise<string | null> {
  let why: string | null = null;
  for (const appid of ownedApps()) {
    const state = await gameState(appid).catch(() => null);
    if (!state || state.installed || state.job?.state === "downloading") continue;
    why = (await removeOwn(appid)) ?? why;
  }
  // Steam takes a moment to drop the entries from their collections.
  await wait(1500);
  await tidyCollections().catch((e) => console.warn("[Vistazo RomM] collections", e));
  return why;
}

// Takes one game's file off the Deck, keeping its entry: as uninstalling a Steam game does.
export async function uninstallOwn(appid: number): Promise<string | null> {
  if (isRunning(appid)) return "running";
  const gone = await remove(appid).catch(() => null);
  changed();
  return gone?.ok ? null : gone?.why ?? "error";
}

export const retryOwn = (appid: number) => download(appid).catch(() => null);
export const cancelOwn = (appid: number) => cancel(appid).catch(() => null);

// Takes the add-on's entry away: the files it brought, its record and the Steam entry. A file
// that was already there (another program's) stays.
export async function removeOwn(appid: number): Promise<string | null> {
  // Never from under a game that is being played.
  if (isRunning(appid)) return "running";
  let state = await cancel(appid).catch(() => null);
  for (let i = 0; i < 50 && state?.job?.state === "downloading"; i++) {
    await wait(100);
    state = await gameState(appid).catch(() => null);
  }
  if (state?.removable) {
    const gone = await remove(appid).catch(() => null);
    if (!gone?.ok) return gone?.why ?? "error";
  }
  const freed = await release(appid).catch(() => null);
  if (!freed?.ok && freed?.why !== "game") return freed?.why ?? "error";
  // The pictures the add-on set go with the entry.
  for (const type of [COVER, ...ART.map(([n]) => n)])
    safe(() => SteamClient.Apps.ClearCustomArtworkForApp(appid, type), undefined);
  SteamClient.Apps.RemoveShortcut(appid);
  setSource(appid, null);
  owned.delete(appid);
  changed();
  return null;
}

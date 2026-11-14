import { callable } from "@decky/api";
import { safe } from "./safe";
import { rommGame } from "./romm";
import { preferredLanguage } from "./settings";

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
  switching?: number; // the version brought and waiting for the game to close
}
type Reply = { ok: boolean; why?: string };

const list = callable<[], Record<string, number>>("game_list");
const launcher = callable<[], Reply & { exe: string; start_dir: string }>("game_launcher");
const cover = callable<[romId: number], Reply & { data: string; kind: string }>("game_cover");
type Picture = { data: string; kind: string };
const pictures = callable<[romId: number, kinds: string[]], Reply & { hero?: Picture; logo?: Picture; wide?: Picture }>("game_pictures");
const take = callable<[appid: number, romId: number, exe: string, dir: string, options: string], GameState>("game_take");
const release = callable<[appid: number], Reply & { exe?: string; start_dir?: string; options?: string }>("game_release");
export interface TenderGame {
  appid: number;
  rom_id: number;
  name: string;
  mine: boolean;
}
export const tenderGames = callable<[], TenderGame[]>("tender_games");
const adopt = callable<[appid: number, romId: number, exe: string, dir: string, options: string, own: number], GameState & { dropped?: number }>("game_adopt");
const download = callable<[appid: number], GameState>("game_download");
const cancel = callable<[appid: number], GameState>("game_cancel");
const pause = callable<[appid: number], GameState>("game_pause");
// A download under way or waiting its turn.
export const busyJob = (s: GameState | null | undefined) => ["queued", "downloading"].includes(s?.job?.state ?? "");
const remove = callable<[appid: number], GameState>("game_remove");
export const gameState = callable<[appid: number], GameState>("game_state");
// A game's save against RomM's, and the files its console needs.
export interface SaveAnswer {
  state: string;
  changed?: boolean;
}
export const saveSync = callable<[appid: number, choice: string], SaveAnswer>("save_sync");
export const saveState = callable<[appid: number], SaveAnswer>("save_state");
export const biosState = callable<[appid: number], { state: string; missing?: string[] }>("bios_state");
export const biosFetch = callable<[appid: number], { state: string; why?: string }>("bios_fetch");
export const tellChanged = () => changed();
export const running = (appid: number) => isRunning(appid);
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
      finishPending();
      // Play time that could not go before (RomM asleep) goes now.
      playState().catch(() => undefined);
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
// The entry of a game kept in several versions, whichever of them it starts.
const ownedAny = (versions: number[]) => versions.map(ownedApp).find(Boolean) ?? 0;
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
  [3, "GetCustomLandcapeImageURLs"], // sic: Steam's own spelling
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

// The pictures RomM keeps for a game (its copies of ScreenScraper's wide picture and logo), for
// the ones the entry lacks. RomM is asked at most once a day per game: a game without them is not
// asked about at every page. (RomM 5.4 keeps no wide capsule of its own: that one comes from
// Tender's entry while there is one.)
const FROM_ROMM: [number, "hero" | "logo" | "wide", string][] = [
  [1, "hero", "GetCustomHeroImageURLs"],
  [2, "logo", "GetCustomLogoImageURLs"],
];
const ASKED = "vistazo-romm:pictures-asked";
const DAY = 24 * 60 * 60 * 1000;
async function rommArt(appid: number, romId: number) {
  const mine = safe(() => appStore.GetAppOverviewByAppID(appid), null);
  if (!mine || !romId) return;
  const lacking = FROM_ROMM.filter(([, , method]) => !safe(() => (appStore[method](mine) as string[]).length, 0));
  if (!lacking.length) return;
  const asked: Record<string, number> = safe(() => JSON.parse(localStorage.getItem(ASKED) ?? "{}") ?? {}, {});
  if (Date.now() - (asked[romId] ?? 0) < DAY) return;
  const got = await pictures(romId, lacking.map(([, kind]) => kind)).catch(() => null);
  if (!got?.ok) return;
  asked[romId] = Date.now();
  safe(() => localStorage.setItem(ASKED, JSON.stringify(asked)), undefined);
  for (const [type, kind] of lacking) {
    const p = got[kind];
    if (p) safe(() => SteamClient.Apps.SetCustomArtworkForApp(appid, p.data, p.kind, type), undefined);
  }
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
// RomM's own first; what it lacks, from Tender's entry while there is one.
export const refreshArt = (appid: number) => {
  const from = sources()[appid];
  rommArt(appid, ownedRom(appid))
    .catch(() => undefined)
    .then(() => (from ? borrowArt(appid, from) : undefined))
    .catch(() => undefined);
};

// The new entry looks like the one it comes from; its cover comes from RomM.
async function dress(appid: number, romId: number, from: number) {
  await rommArt(appid, romId).catch(() => undefined);
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

// Entries whose game was deleted in RomM: shown in the settings, and taken away only when the
// player says so (as removing an entry does: the files the add-on brought go, saves stay).
const gone = callable<[], Reply & { apps: number[] }>("game_gone");
// Asking means reading RomM's whole list: not more than once in a while.
const GONE_GOOD = 10 * 60 * 1000;
let goneAsked: { at: number; apps: Promise<number[]> } | null = null;
export function goneOwn(anew = false): Promise<number[]> {
  if (anew || !goneAsked || Date.now() - goneAsked.at > GONE_GOOD)
    goneAsked = { at: Date.now(), apps: gone().then((g) => (g?.ok ? g.apps : [])).catch(() => [] as number[]) };
  return goneAsked.apps.then((apps) => apps.filter((a) => owned.has(a)));
}
export async function clearGone(appids: number[]): Promise<string | null> {
  let why: string | null = null;
  for (const appid of appids) why = (await removeOwn(appid)) ?? why;
  await wait(1500);
  await tidyCollections().catch((e) => console.warn("[Vistazo RomM] collections", e));
  return why;
}

// Play time: each session of one of its games goes to RomM (the backend keeps it until it can).
export const playStarted = callable<[appid: number], Reply>("play_started");
export const playEnded = callable<[appid: number], Reply & { kept?: boolean; waiting?: number }>("play_ended");
export const playState = callable<[], { allowed: boolean; waiting: number }>("play_state");

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
    // What Steam hands the launcher: the game's number in RomM (it changes with the version).
    const romId = owned.get(appid);
    if (romId) safe(() => SteamClient.Apps.SetAppLaunchOptions(appid, String(romId)), undefined);
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
type LibraryGame = { rom_id: number; name: string; platform: string; system: string; size: number; versions: number[] };
const library = callable<[language: string], Reply & { games: LibraryGame[] }>("romm_library");
const versionsOf = (g: LibraryGame) => (g.versions?.length ? g.versions : [g.rom_id]);
const COLLECTION = "RomM: "; // a collection's name: "RomM: Nintendo DS"

const steamCollection = (name: string) =>
  (collectionStore.userCollections as any[]).find((c) => String(c.displayName).toLowerCase() === name.toLowerCase());

// Puts these entries in the Steam collection of that name, made when it is not there yet.
async function putIn(name: string, appids: number[]) {
  const overviews = appids.map((id) => safe(() => appStore.GetAppOverviewByAppID(id), null)).filter(Boolean);
  if (!overviews.length) return;
  const existing = steamCollection(name);
  const collection = existing ?? collectionStore.NewUnsavedCollection(name, undefined, []);
  const have = new Set<number>((collection.allApps ?? []).map((o: any) => Number(o.appid)));
  const missing = overviews.filter((o: any) => !have.has(Number(o.appid)));
  if (!existing || missing.length) {
    collection.AsDragDropCollection().AddApps(missing);
    await collection.Save();
  }
}

async function gather(byPlatform: Map<string, number[]>) {
  if (typeof collectionStore === "undefined") return;
  for (const [platform, appids] of byPlatform) await putIn(COLLECTION + platform, appids);
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
    const lib = await library(preferredLanguage()).catch(() => null);
    if (!lib?.ok) return lib?.why ?? "error";
    await loadOwned(true);
    // Only consoles whose folder in RetroDECK is known: an entry that could never start helps nobody.
    const games = lib.games.filter((g) => g.system);
    let done = 0;
    let why: string | null = null;
    for (const g of games) {
      // A game with an entry in any of its versions keeps that one.
      if (!ownedAny(versionsOf(g))) why = (await addOwn(g.rom_id, g.name, 0, false)) ?? why;
      step(++done, games.length);
    }
    const byPlatform = new Map<string, number[]>();
    for (const g of games) {
      const appid = ownedAny(versionsOf(g));
      if (appid && g.platform) byPlatform.set(g.platform, [...(byPlatform.get(g.platform) ?? []), appid]);
    }
    await gather(byPlatform).catch((e) => console.warn("[Vistazo RomM] collections", e));
    await fillShown().catch((e) => console.warn("[Vistazo RomM] collections", e));
    // Pictures from RomM first; then from Tender's entries of the same games, for what is still missing.
    for (const g of games) {
      const appid = ownedAny(versionsOf(g));
      if (appid) await rommArt(appid, ownedRom(appid)).catch(() => undefined);
    }
    const theirs = await tenderEntries();
    for (const g of games) {
      const [appid, from] = [ownedAny(versionsOf(g)), theirs.get(g.rom_id) ?? 0];
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

// RomM's collections shown in Steam, one Steam collection each: "RomM: [Pokémon]" (the brackets keep
// them apart from the consoles'). Showing one brings the entries of its games that are missing, as
// bringing the library does; no longer showing it takes away only Steam's collection.
export interface RommCollection {
  key: string;
  kind: "standard" | "smart" | "virtual";
  sort: string;
  name: string;
  roms: number[];
  on: boolean;
}
export const rommCollections = callable<[], Reply & { collections: RommCollection[] }>("romm_collections");
const showCollection = callable<[key: string, on: boolean], Reply>("romm_collection_set");
const shownName = (c: RommCollection) => `${COLLECTION}[${c.name}]`;

// Fills the shown collections with the entries already there (after the library was brought).
async function fillShown() {
  if (typeof collectionStore === "undefined") return;
  const all = await rommCollections().catch(() => null);
  if (!all?.ok) return;
  await loadOwned();
  for (const c of all.collections.filter((c) => c.on))
    await putIn(shownName(c), c.roms.map(ownedApp).filter(Boolean));
}

export async function turnCollection(c: RommCollection, on: boolean, step: (done: number, total: number) => void): Promise<string | null> {
  if (syncing) return "busy";
  const kept = await showCollection(c.key, on).catch(() => null);
  if (!kept?.ok) return kept?.why ?? "error";
  if (typeof collectionStore === "undefined") return null;
  if (!on) {
    const existing = steamCollection(shownName(c));
    if (existing) await existing.Delete();
    return null;
  }
  syncing = true;
  try {
    const lib = await library(preferredLanguage()).catch(() => null);
    if (!lib?.ok) return lib?.why ?? "error";
    await loadOwned(true);
    const wanted = new Set(c.roms);
    // As with the library: only games whose console's folder in RetroDECK is known; a game is in
    // the collection whichever of its versions RomM put there.
    const games = lib.games.filter((g) => g.system && versionsOf(g).some((v) => wanted.has(v)));
    let done = 0;
    let why: string | null = null;
    for (const g of games) {
      if (!ownedAny(versionsOf(g))) why = (await addOwn(g.rom_id, g.name, 0, false)) ?? why;
      step(++done, games.length);
    }
    await putIn(shownName(c), games.map((g) => ownedAny(versionsOf(g))).filter(Boolean));
    // The new entries go into their console's collection too.
    const byPlatform = new Map<string, number[]>();
    for (const g of games) {
      const appid = ownedAny(versionsOf(g));
      if (appid && g.platform) byPlatform.set(g.platform, [...(byPlatform.get(g.platform) ?? []), appid]);
    }
    await gather(byPlatform).catch((e) => console.warn("[Vistazo RomM] collections", e));
    changed();
    return why;
  } finally {
    syncing = false;
  }
}

// The versions RomM keeps of a game (its languages and regions), and switching the one an entry
// starts. An installed game gets the other version brought first; its save goes with it when the
// new version has none, and the old save stays as it is.
export interface Version {
  rom_id: number;
  file: string;
  languages: string[];
  regions: string[];
  achievements: boolean;
  current: boolean;
}
export const gameVersions = callable<[appid: number], Reply & { versions: Version[] }>("game_versions");
type Switched = GameState & { options?: string; pending?: number; save?: string };
const gameSwitch = callable<[appid: number, romId: number], Switched>("game_switch");
const switchFinish = callable<[appid: number], Switched>("game_switch_finish");
const pending = callable<[], Record<string, number>>("game_pending");

function switchedTo(appid: number, romId: number) {
  safe(() => SteamClient.Apps.SetAppLaunchOptions(appid, String(romId)), undefined);
  owned.set(appid, romId);
  changed();
}

// The switch ends only once the game is closed: what it saved while the other version was on
// its way is what goes with it.
async function finishWhenClosed(appid: number): Promise<string | null> {
  while (isRunning(appid)) await wait(2000);
  const done = await switchFinish(appid).catch(() => null);
  if (!done?.ok) return done?.why ?? "error";
  switchedTo(appid, done.rom_id);
  return null;
}

export async function switchVersion(appid: number, romId: number): Promise<string | null> {
  if (isRunning(appid)) return "running";
  const got = await gameSwitch(appid, romId).catch(() => null);
  if (!got?.ok) return got?.why ?? "error";
  if (!got.pending) {
    switchedTo(appid, romId);
    return null;
  }
  // The other version is on its way: the entry keeps starting the old one meanwhile.
  for (let tries = 0; ; tries++) {
    await wait(1000);
    const s = await gameState(appid).catch(() => null);
    if (!s && tries > 60) return "unreachable";
    if (!s || busyJob(s)) continue;
    if (s.switching !== romId) return s.job?.state && !["installed", "ready"].includes(s.job.state) ? s.job.state : "error";
    return finishWhenClosed(appid);
  }
}

// Switches left waiting (Steam or the add-on restarted meanwhile) end when the game is closed.
async function finishPending() {
  const waiting = await pending().catch(() => ({}) as Record<string, number>);
  for (const appid of Object.keys(waiting).map(Number)) finishWhenClosed(appid).catch(() => undefined);
}

// Takes away the entries whose game is not on the Deck (nothing is deleted from the disk): what
// stays is what the player has installed.
export async function dropNotInstalled(): Promise<string | null> {
  let why: string | null = null;
  for (const appid of ownedApps()) {
    const state = await gameState(appid).catch(() => null);
    if (!state || state.installed || busyJob(state)) continue;
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
// Pauses a download: what came stays, and downloading again goes on from there. One that cannot
// be paused (a version being brought, or being unpacked) is stopped instead.
export async function pauseOwn(appid: number) {
  const s = await pause(appid).catch(() => null);
  if (!s?.ok && busyJob(s)) await cancelOwn(appid);
  return s;
}

// Takes the add-on's entry away: the files it brought, its record and the Steam entry. A file
// that was already there (another program's) stays.
export async function removeOwn(appid: number): Promise<string | null> {
  // Never from under a game that is being played.
  if (isRunning(appid)) return "running";
  let state = await cancel(appid).catch(() => null);
  for (let i = 0; i < 50 && busyJob(state); i++) {
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

// ── Tender's entries ──────────────────────────────────────────────────────────────
// While Tender is on it holds its entries (it stops their launches and writes them back), so they
// can only be taken over once it is turned off or uninstalled.
declare const DeckyPluginLoader: any;
export const tenderOn = () => !!safe(() => (DeckyPluginLoader?.plugins ?? []).some((p: any) => p?.name === "Tender"), false);

function details(appid: number): Promise<any> {
  return new Promise((done) => {
    let reg: any = null;
    let got: any = undefined;
    const timer = setTimeout(() => {
      safe(() => reg?.unregister(), undefined);
      done(null);
    }, 3000);
    reg = safe(
      () =>
        SteamClient.Apps.RegisterForAppDetails(appid, (d: any) => {
          got = d;
          clearTimeout(timer);
          safe(() => reg?.unregister(), undefined);
          done(d);
        }),
      null,
    );
    // Steam may answer before the registration is returned: let go of it then.
    if (got !== undefined) safe(() => reg?.unregister(), undefined);
  });
}

// Each of Tender's entries becomes the add-on's under its own Steam number (play time, pictures and
// collections stay): pointed at the add-on's launcher, with what it had kept to give it back. The
// add-on's own entry of the same game is folded in and taken out of Steam; no game file is removed.
export async function takeOverTender(step: (done: number, total: number) => void): Promise<string | null> {
  if (tenderOn()) return "tender-on";
  await loadOwned(true);
  const l = await launcher().catch(() => null);
  if (!l?.ok) return l?.why ?? "error";
  const all = (await tenderGames().catch(() => [])).filter((g) => !g.mine);
  let why: string | null = null;
  let done = 0;
  const byPlatform = new Map<string, number[]>();
  for (const g of all) {
    step(done++, all.length);
    if (!safe(() => appStore.GetAppOverviewByAppID(g.appid), null)) continue; // gone from Steam
    if (isRunning(g.appid)) {
      why = "running";
      continue;
    }
    const d = await details(g.appid);
    // Without what the entry runs now, it could not be given back: left as it is.
    if (!d?.strShortcutExe) {
      why = "steam";
      continue;
    }
    const mine = ownedApp(g.rom_id);
    const own = mine && mine !== g.appid && !isRunning(mine) ? mine : 0;
    const got = await adopt(g.appid, g.rom_id, d.strShortcutExe ?? "", d.strShortcutStartDir ?? "", d.strLaunchOptions ?? "", own).catch(() => null);
    if (!got?.ok || !got.options) {
      why = got?.why ?? "error";
      continue;
    }
    SteamClient.Apps.SetShortcutExe(g.appid, l.exe);
    SteamClient.Apps.SetShortcutStartDir(g.appid, l.start_dir);
    SteamClient.Apps.SetAppLaunchOptions(g.appid, got.options);
    owned.set(g.appid, g.rom_id);
    if (got.dropped) {
      for (const type of [COVER, ...ART.map(([n]) => n)])
        safe(() => SteamClient.Apps.ClearCustomArtworkForApp(got.dropped, type), undefined);
      safe(() => SteamClient.Apps.RemoveShortcut(got.dropped), undefined);
      setSource(got.dropped!, null);
      owned.delete(got.dropped!);
    }
    const platform = (await rommGame(g.rom_id).catch(() => null))?.platform;
    if (platform) byPlatform.set(platform, [...(byPlatform.get(platform) ?? []), g.appid]);
  }
  step(all.length, all.length);
  await gather(byPlatform).catch((e) => console.warn("[Vistazo RomM] collections", e));
  await tidyCollections().catch(() => undefined);
  changed();
  return why;
}

// Undoes it: each taken-over entry gets back what it had, for Tender to start it again.
export async function giveBackTender(): Promise<string | null> {
  await loadOwned(true);
  const theirs = new Set((await tenderGames().catch(() => [])).filter((g) => g.mine).map((g) => g.appid));
  let why: string | null = null;
  for (const appid of theirs) {
    if (isRunning(appid)) {
      why = "running";
      continue;
    }
    const back = await release(appid).catch(() => null);
    if (!back?.ok) {
      why = back?.why ?? "error";
      continue;
    }
    if (back.exe) SteamClient.Apps.SetShortcutExe(appid, back.exe);
    if (back.start_dir) SteamClient.Apps.SetShortcutStartDir(appid, back.start_dir);
    SteamClient.Apps.SetAppLaunchOptions(appid, back.options ?? "");
    owned.delete(appid);
  }
  changed();
  return why;
}

import { definePlugin } from "@decky/api";
import { PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";
import { Addon, AddonAction, AddonFile, AddonGame, AddonProgress, CONTRACT, VistazoApi } from "./contract";
import { t } from "./i18n";
import {
  biosFetch,
  biosState,
  cancelOwn,
  gameState,
  loadOwned,
  ownedApps,
  ownedRom,
  refreshArt,
  retryOwn,
  running,
  saveState,
  saveSync,
  tellChanged,
  uninstallOwn,
} from "./own";
import { isLinked, rommGame, rommProgress, rommStatus } from "./romm";
import { safe } from "./safe";
import { Settings } from "./Rows";
import { tuckAway } from "./tuck";
import { ownGames, syncSaves } from "./settings";
import { tellBios, tellSave } from "./notify";

// Vistazo RomM hands Vistazo what it knows of RomM games; Vistazo draws it. Without Vistazo
// installed there is nothing to show them on, and the panel says so.
const vistazo = (): VistazoApi | null => (window as any).Vistazo ?? null;

declare const SteamClient: any;

// The game's save against RomM's, for its page. RomM is asked after a game, when the save here
// changed, when the last try did not end well, and otherwise only now and then: nothing changes
// in between on a Deck that is the only place the game is played.
const SAVE_GOOD = 6 * 60 * 60 * 1000;
const saveAsked = new Map<number, number>();
const saveWord = (state: string): AddonGame["save"] =>
  ["synced", "sent", "brought"].includes(state) ? "synced" : state === "conflict" ? "conflict" : state === "none" ? undefined : "pending";
async function save(appid: number, anew: boolean): Promise<AddonGame["save"]> {
  if (!syncSaves()) return undefined;
  const known = await saveState(appid).catch(() => null);
  if (!known || ["unsupported", "not-installed", "layout", "game"].includes(known.state)) return undefined;
  const recent = Date.now() - (saveAsked.get(appid) ?? 0) < SAVE_GOOD;
  if (running(appid) || (!anew && recent && !known.changed && ["synced", "sent", "brought", "conflict"].includes(known.state)))
    return saveWord(known.state);
  const now = await saveSync(appid, "").catch(() => null);
  if (now && !["unreachable", "refused", "busy"].includes(now.state)) saveAsked.set(appid, Date.now());
  // Opening a page says only what moved; failures there show on the page itself.
  if (now && ["sent", "brought"].includes(now.state)) tellSave(appid, now.state);
  return saveWord(now?.state ?? "unreachable");
}
// The files the console needs: asked once per game while Steam runs, and again after bringing them.
const biosKnown = new Map<number, AddonGame["bios"]>();
async function bios(appid: number): Promise<AddonGame["bios"]> {
  if (biosKnown.has(appid)) return biosKnown.get(appid);
  const b = await biosState(appid).catch(() => null);
  const word = b?.state === "ok" ? "ok" : b?.state === "missing" ? "missing" : undefined;
  if (b && ["ok", "missing", "none", "unsupported"].includes(b.state)) biosKnown.set(appid, word);
  return word;
}
// When one of its games closes, its save goes to RomM whether or not its page is open.
function watchSaves(): () => void {
  const reg = safe(
    () =>
      SteamClient.GameSessions.RegisterForAppLifetimeNotifications((u: any) => {
        const appid = Number(u?.unAppID);
        if (!u || u.bRunning !== false || !syncSaves() || !ownGames() || !ownedRom(appid)) return;
        // A moment for the emulator to finish writing.
        setTimeout(() => {
          saveSync(appid, "")
            .then((now) => {
              if (now && !["unreachable", "refused", "busy"].includes(now.state)) saveAsked.set(appid, Date.now());
              tellSave(appid, now?.state);
              tellChanged();
            })
            .catch(() => undefined);
        }, 3000);
      }),
    null as any,
  );
  return () => safe(() => reg?.unregister(), undefined);
}

async function game(appid: number, refresh: boolean): Promise<AddonGame | null> {
  const romId = ownedRom(appid);
  if (!romId) return null;
  refreshArt(appid);
  const g = await rommGame(romId).catch(() => null);
  if (!g?.ok) return null;
  const [p, saved, files] = await Promise.all([
    g.ra_id ? rommProgress(g.ra_id, refresh, true).catch(() => null) : null,
    save(appid, refresh).catch(() => undefined),
    bios(appid).catch(() => undefined),
  ]);
  const list = g.achievements ?? [];
  return {
    platform: g.platform || undefined,
    year: g.released ? new Date(g.released).getUTCFullYear() : undefined,
    genres: g.genres,
    rating: g.rating || undefined,
    size: g.size || undefined,
    summary: g.summary || undefined,
    stale: !!(g.kept || p?.kept),
    save: saved,
    bios: files,
    achievements: list.length
      ? {
          earned: p?.ok ? Number(p.earned) || 0 : 0,
          total: Number(p?.total) || list.length,
          hardcore: Number(p?.hardcore) || 0,
          got: p?.ids ?? [],
          list: list.map((a) => ({
            id: a.badge_id,
            title: a.title,
            description: a.description,
            points: a.points,
            players: a.num_awarded,
            badge: a.badge_url,
            badgeLocked: a.badge_url_lock || a.badge_url,
            order: a.order,
          })),
        }
      : undefined,
  };
}

async function file(appid: number): Promise<AddonFile | null> {
  if (!ownedRom(appid)) return null;
  const s = await gameState(appid).catch(() => null);
  if (!s) return null;
  const job = s.job?.state;
  const failed = !!job && !["downloading", "installed", "cancelled"].includes(job);
  return {
    here: s.installed,
    downloading: job === "downloading",
    share: s.job?.total ? Math.floor((100 * s.job.done) / s.job.total) : 0,
    problem: failed ? t.ownWhy(job) : undefined,
  };
}

async function progress(raId: number, refresh: boolean): Promise<AddonProgress | null> {
  if (!isLinked() && !["linked", "limited"].includes(String(await rommStatus()))) return null;
  const p = await rommProgress(raId, refresh).catch(() => null);
  if (p?.why === "refused") rommStatus();
  return p;
}

// On one of its own games' pages: taking the file off the Deck, when the add-on brought it.
async function actions(appid: number): Promise<AddonAction[]> {
  if (!ownGames() || !ownedRom(appid)) return [];
  const out: AddonAction[] = [];
  // A save changed on both sides: the two ways out. Either keeps a copy of what it replaces.
  if (syncSaves() && !running(appid)) {
    const known = await saveState(appid).catch(() => null);
    if (known?.state === "conflict") {
      const settle = (choice: string) => async () => {
        const now = await saveSync(appid, choice).catch(() => null);
        saveAsked.set(appid, Date.now());
        tellSave(appid, now?.state);
        tellChanged();
      };
      out.push({ id: "save-romm", label: t.saveUseRomm, run: settle("romm") }, { id: "save-deck", label: t.saveUseDeck, run: settle("deck") });
    }
  }
  if (biosKnown.get(appid) === "missing")
    out.push({
      id: "bios",
      label: t.biosGet,
      run: async () => {
        const after = await biosFetch(appid).catch(() => null);
        tellBios(appid, biosKnown.get(appid), after);
        biosKnown.delete(appid);
        tellChanged();
      },
    });
  const s = await gameState(appid).catch(() => null);
  if (s?.installed && s.removable) out.push({ id: "uninstall", label: t.uninstall, run: async () => void (await uninstallOwn(appid)) });
  return out;
}

const addon: Addon = {
  id: "romm",
  name: "RomM",
  contract: CONTRACT,
  // Its games show as Vistazo's only while the player lets the add-on look after games.
  owns: (appid) => ownGames() && ownedRom(appid) > 0,
  game,
  file,
  fetchFile: async (appid) => void (await retryOwn(appid)),
  cancelFile: async (appid) => void (await cancelOwn(appid)),
  shelf: () => (ownGames() ? ownedApps() : []),
  actions,
  progress,
  Settings,
};

// Vistazo may load after this add-on, and loads again on every update: register whenever it is
// there, and again each time it says it is ready.
function connect(): () => void {
  let leave: (() => void) | null = null;
  const join = () => {
    const api = vistazo();
    safe(() => leave?.(), undefined);
    leave = api && api.contract >= CONTRACT ? api.register(addon) : null;
  };
  join();
  window.addEventListener("vistazo:ready", join);
  return () => {
    window.removeEventListener("vistazo:ready", join);
    safe(() => leave?.(), undefined);
  };
}

// The add-on's own Quick Access panel: the same rows Vistazo shows, for when Vistazo is not there.
function Panel() {
  const [there, setThere] = useState(!!vistazo());
  useEffect(() => {
    const look = () => setThere(!!vistazo());
    window.addEventListener("vistazo:ready", look);
    const timer = setInterval(look, 2000);
    return () => {
      window.removeEventListener("vistazo:ready", look);
      clearInterval(timer);
    };
  }, []);
  return (
    <>
      {!there && (
        <PanelSection>
          <PanelSectionRow>
            <div style={{ fontSize: 13, lineHeight: 1.45, opacity: 0.8 }}>{t.needsVistazo}</div>
          </PanelSectionRow>
        </PanelSection>
      )}
      <Settings />
    </>
  );
}

function Icon() {
  return (
    <svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true">
      <rect x="3" y="7" width="18" height="11" rx="3.5" fill="none" stroke="currentColor" strokeWidth="1.9" />
      <path d="M8 10.5v4M6 12.5h4" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
      <circle cx="16.5" cy="12.5" r="1.4" fill="currentColor" />
    </svg>
  );
}

export default definePlugin(() => {
  loadOwned();
  rommStatus();
  const disconnect = connect();
  const untuck = tuckAway("Vistazo RomM", "vistazo-romm");
  const stopSaves = watchSaves();
  return {
    name: "Vistazo RomM",
    titleView: <div>Vistazo RomM</div>,
    content: <Panel />,
    icon: <Icon />,
    onDismount() {
      disconnect();
      untuck();
      stopSaves();
    },
  };
});

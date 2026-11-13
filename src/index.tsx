import { definePlugin } from "@decky/api";
import { PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";
import { Addon, AddonAction, AddonFile, AddonGame, AddonProgress, CONTRACT, VistazoApi } from "./contract";
import { t } from "./i18n";
import { cancelOwn, gameState, loadOwned, ownedApps, ownedRom, refreshArt, retryOwn, uninstallOwn } from "./own";
import { isLinked, rommGame, rommProgress, rommStatus } from "./romm";
import { safe } from "./safe";
import { Settings } from "./Rows";
import { tuckAway } from "./tuck";
import { ownGames } from "./settings";

// Vistazo RomM hands Vistazo what it knows of RomM games; Vistazo draws it. Without Vistazo
// installed there is nothing to show them on, and the panel says so.
const vistazo = (): VistazoApi | null => (window as any).Vistazo ?? null;

async function game(appid: number, refresh: boolean): Promise<AddonGame | null> {
  const romId = ownedRom(appid);
  if (!romId) return null;
  refreshArt(appid);
  const g = await rommGame(romId).catch(() => null);
  if (!g?.ok) return null;
  const p = g.ra_id ? await rommProgress(g.ra_id, refresh, true).catch(() => null) : null;
  const list = g.achievements ?? [];
  return {
    platform: g.platform || undefined,
    year: g.released ? new Date(g.released).getUTCFullYear() : undefined,
    genres: g.genres,
    rating: g.rating || undefined,
    size: g.size || undefined,
    summary: g.summary || undefined,
    stale: !!(g.kept || p?.kept),
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
  const s = await gameState(appid).catch(() => null);
  if (!s?.installed || !s.removable) return [];
  return [{ id: "uninstall", label: t.uninstall, run: async () => void (await uninstallOwn(appid)) }];
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
  return {
    name: "Vistazo RomM",
    titleView: <div>Vistazo RomM</div>,
    content: <Panel />,
    icon: <Icon />,
    onDismount() {
      disconnect();
      untuck();
    },
  };
});

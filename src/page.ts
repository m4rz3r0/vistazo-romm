import { ownedRom } from "./own";
import { safe } from "./safe";

declare const DeckyBackend: any;
declare const SteamUIStore: any;

// The Steam game whose page is open now (0 on any other screen) and which RomM game it is: one of
// the add-on's own entries, or one of Tender's (asked from Tender, once per game).
const tenderRoms = new Map<number, number>();
const asking = new Set<number>();
function askTender(appid: number) {
  if (asking.has(appid) || tenderRoms.has(appid)) return;
  asking.add(appid);
  Promise.resolve(DeckyBackend.call("loader/call_plugin_method", "Tender", "get_cached_game_detail", appid))
    .then((g: any) => tenderRoms.set(appid, g?.found ? Number(g.rom_id) || 0 : 0))
    .catch(() => undefined)
    .finally(() => asking.delete(appid));
}

export function openGame(): { appid: number; romId: number } {
  const win = safe(() => SteamUIStore.WindowStore.GamepadUIMainWindowInstance, null as any);
  const m = /^\/library\/app\/(\d+)/.exec(String(win?.m_history?.location?.pathname ?? ""));
  const appid = m ? Number(m[1]) : 0;
  if (!appid) return { appid: 0, romId: 0 };
  const mine = ownedRom(appid);
  if (!mine) askTender(appid);
  return { appid, romId: mine || tenderRoms.get(appid) || 0 };
}

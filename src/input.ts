import { callable } from "@decky/api";
import { safe } from "./safe";

// The Steam Input layout of each console's entries. The backend keeps the player's choice and says
// which Steam template it is; Steam is asked to select it here, as its own layout picker does. Only
// the Deck's built-in controls are changed: each controller type has templates of its own.
declare const SteamClient: any;
declare const appStore: any;

type Reply = { ok: boolean; why?: string };
export interface ConsoleLayout {
  system: string;
  count: number;
  layout: string; // "" when Steam's own is left as it is
}
export const inputLayouts = callable<[], Reply & { layouts: string[]; consoles: ConsoleLayout[] }>("input_layouts");
const setLayout = callable<[system: string, layout: string], Reply & { url?: string; apps?: number[] }>("input_set_layout");
const layoutOf = callable<[appid: number], Reply & { url?: string }>("input_layout");

// RetroDECK's folder for a console -> its name; the folder itself for any other.
const NAMES: Record<string, string> = { nds: "Nintendo DS", n3ds: "Nintendo 3DS", wii: "Wii", ps2: "PlayStation 2", psp: "PSP" };
export const consoleName = (system: string) => NAMES[system] ?? system;

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

// k_eControllerType_SteamControllerNeptune: the Deck's own controls.
const DECK_TYPE = 4;
const DECK_TYPE_NAME = "controller_steamcontroller_neptune";

// The index Steam gave the Deck's own controls (not 0: it is assigned by Steam), or null.
function deckIndex(): number | null {
  const store = (window as any).ControllerStore;
  const list: any[] = safe(() => store.GetControllers?.() ?? store.m_controllerList, []);
  const deck = list.find(
    (c) => c?.eControllerType === DECK_TYPE || safe(() => store.GetControllerTypeString(c?.eControllerType), "") === DECK_TYPE_NAME,
  );
  return typeof deck?.nControllerIndex === "number" ? deck.nControllerIndex : null;
}

export type Selected = "ok" | "nodeck" | "refused";

// THE Steam-facing part: selects the template `url` ("template://<file>.vdf") for this entry on the
// Deck's own controls and reads it back until Steam reports it. Steam keeps a shortcut's layout
// under its name, and a new shortcut is keyed on its program for a moment before that: it is
// waited out, or the layout would be filed where no start ever reads it.
export async function selectLayout(appid: number, url: string): Promise<Selected> {
  const input = safe(() => SteamClient.Input, null);
  if (!input?.SetSelectedConfigForApp || !input?.GetConfigForAppAndController) return "refused";
  const index = deckIndex();
  if (index === null) return "nodeck";
  const current = async () => String((await input.GetConfigForAppAndController(appid, index))?.URL ?? "");
  try {
    for (let i = 0; i < 12 && (await current()).includes("vistazo-launch"); i++) await wait(250);
    if ((await current()) === url) return "ok";
    // The last two: not a preview, and the player's own choice (with fewer, Steam selects nothing).
    await input.SetSelectedConfigForApp(appid, index, url, false, 1);
    for (let i = 0; i < 12; i++) {
      if ((await current()) === url) return "ok";
      await wait(250);
    }
  } catch (e) {
    console.warn("[Vistazo RomM] layout", appid, e);
  }
  return "refused";
}

// The player picked a layout for a console: kept, then selected on each of its entries in Steam
// ("" keeps it from then on and changes nothing). Answers what to say: null when all took.
export async function chooseLayout(system: string, layout: string): Promise<{ why?: string; done?: number; of?: number }> {
  const r = await setLayout(system, layout).catch(() => null);
  if (!r?.ok) return { why: r?.why ?? "error" };
  if (!r.url) return {};
  const apps = (r.apps ?? []).filter((appid) => safe(() => appStore.GetAppOverviewByAppID(appid), null));
  let done = 0;
  for (const appid of apps) {
    const got = await selectLayout(appid, r.url);
    if (got === "nodeck") return { why: "nodeck" };
    if (got === "ok") done++;
  }
  return { done, of: apps.length };
}

// A new entry of the add-on's gets its console's layout, when one was picked.
export async function layoutNew(appid: number): Promise<void> {
  const r = await layoutOf(appid).catch(() => null);
  if (r?.ok && r.url) await selectLayout(appid, r.url);
}

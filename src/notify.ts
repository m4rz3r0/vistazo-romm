import { toaster } from "@decky/api";
import { t } from "./i18n";
import { safe } from "./safe";

declare const appStore: any;

// Short notices for what happened to a game's save or BIOS files out of sight: after the game
// closed, or after a choice on its page. A save already in step says nothing.
const name = (appid: number): string =>
  safe(() => String(appStore.GetAppOverviewByAppID(appid)?.display_name ?? ""), "").replace(/\s*·\s*Vistazo$/, "");

function show(appid: number, body: string) {
  safe(() => toaster.toast({ title: name(appid) || "RomM", body, duration: 5000 }), undefined);
}

export function tellSave(appid: number, state: string | undefined) {
  const s = state ?? "unreachable";
  if (["synced", "none", "busy", "unsupported", "not-installed", "game"].includes(s)) return;
  const body =
    s === "sent" ? t.noteSent
    : s === "brought" ? t.noteBrought
    : s === "conflict" ? t.noteConflict
    : s === "unreachable" ? t.noteUnreachable
    : s === "refused" ? t.noteRefused
    : s === "layout" ? t.noteLayout
    : t.noteSaveFailed;
  show(appid, body);
}

export function tellBios(appid: number, before: string | undefined, after: { state: string; missing?: string[] } | null) {
  if (after?.state === "ok") return show(appid, t.noteBiosOk);
  if (after?.state === "unreachable" || !after) return show(appid, t.noteUnreachable);
  if (after.state === "refused") return show(appid, t.noteRefused);
  if (before === "missing") show(appid, t.noteBiosFailed);
}

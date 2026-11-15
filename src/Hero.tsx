import { ConfirmModal, DialogButton, Focusable } from "@decky/ui";
import { useEffect, useState } from "react";
import { t } from "./i18n";
import { HeroChoice, heroChoices, pickHero } from "./own";

// A game's background, picked on its page ("Change background"): what there is to pick from, as
// thumbnails shaped like the page's background (RomM's copy of ScreenScraper's fanart, SteamGridDB's
// heroes with the player's key) and "only the cover". The one picked stays: nothing gives another
// or takes it off.
const HERO_STYLE = `
.vr-heroes { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 12px; }
.vr-hero.DialogButton { padding: 0 !important; min-width: 0 !important; background: transparent !important;
  border-radius: 8px; overflow: visible; outline: 3px solid transparent; outline-offset: 3px;
  display: flex; flex-direction: column; box-shadow: none !important;
  background-color: transparent !important; background-image: none !important; }
.vr-hero.DialogButton.gpfocus, .vr-hero.DialogButton:hover { outline-color: #fff; background: transparent !important;
  background-color: transparent !important; background-image: none !important; }
.vr-hero img, .vr-hero .vr-hero-cover { width: 100%; aspect-ratio: 96 / 31; object-fit: cover; display: block; border-radius: 8px; }
.vr-hero .vr-hero-cover { display: flex; align-items: center; justify-content: center; background: #000;
  box-shadow: inset 0 0 0 1px rgba(255,255,255,0.18); color: rgba(255,255,255,0.85); font-size: 15px; }
.vr-hero span { font-size: 12px; text-align: left; padding: 6px 2px 0; color: rgba(255,255,255,0.6); }
.vr-hero.gpfocus span { color: #fff; }
`;

export function HeroModal({ appid, romId, closeModal }: { appid: number; romId: number; closeModal?: () => void }) {
  const [list, setList] = useState<HeroChoice[] | null>(null);
  const [sgdb, setSgdb] = useState("off");
  const [why, setWhy] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    heroChoices(romId)
      .then((r) => {
        if (!alive) return;
        if (!r.ok) return setWhy(t.ownWhy(r.why ?? "error"));
        setList(r.choices ?? []);
        setSgdb(r.sgdb ?? "off");
      })
      .catch(() => alive && setWhy(t.ownWhy("unreachable")));
    return () => {
      alive = false;
    };
  }, [romId]);
  const pick = (choice: string | null) => {
    if (busy) return;
    setBusy(true);
    setWhy(null);
    pickHero(appid, romId, choice).then((ok) => {
      setBusy(false);
      if (ok) closeModal?.();
      else setWhy(t.heroFailed);
    });
  };
  const sgdbNote = sgdb === "off" ? t.heroSgdbOff : sgdb === "refused" ? t.heroSgdbRefused : sgdb === "unreachable" ? t.heroSgdbUnreachable : null;
  const note = why ?? (!list ? t.heroLoading : !list.length ? (sgdbNote ?? t.heroNone) : sgdbNote);
  const source = (c: HeroChoice) => (c.source === "sgdb" ? "SteamGridDB" : "ScreenScraper");
  return (
    <ConfirmModal strTitle={t.heroTitle} strOKButtonText={t.heroClose} bAlertDialog closeModal={closeModal} onOK={() => closeModal?.()}>
      <style>{HERO_STYLE}</style>
      {note && <div style={{ margin: "6px 0", lineHeight: 1.45, color: "rgba(255,255,255,0.7)" }}>{note}</div>}
      {list && (
        <Focusable className="vr-heroes">
          {list.map((c) => (
            <DialogButton key={c.id} className="vr-hero" disabled={busy} onClick={() => pick(c.id)}>
              <img src={`data:image/${c.kind === "jpg" ? "jpeg" : c.kind};base64,${c.data}`} alt="" />
              <span>{source(c)}</span>
            </DialogButton>
          ))}
          <DialogButton className="vr-hero" disabled={busy} onClick={() => pick(null)}>
            <div className="vr-hero-cover">{t.heroCover}</div>
          </DialogButton>
        </Focusable>
      )}
    </ConfirmModal>
  );
}


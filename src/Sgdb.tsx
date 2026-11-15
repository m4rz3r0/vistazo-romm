import { ButtonItem, ConfirmModal, PanelSectionRow, TextField, showModal } from "@decky/ui";
import { callable } from "@decky/api";
import { useCallback, useEffect, useState } from "react";
import { t } from "./i18n";

// SteamGridDB, for the pictures RomM lacks: off until the player pastes their own key. One row
// with its state and the one thing to do about it; the key is never shown or handed back. It is
// kept in the RomM account too, so it is typed once and the other devices pick it up.
export type SgdbStatus = { on: boolean; refused?: boolean; shared?: boolean };
export const sgdbStatus = callable<[], SgdbStatus>("sgdb_status");
const setKey = callable<[key: string], { result: string }>("sgdb_set_key");
const forget = callable<[], { ok: boolean }>("sgdb_forget");

export function SgdbRow() {
  const [status, setStatus] = useState<SgdbStatus | null>(null);
  const read = useCallback(() => {
    sgdbStatus()
      .then(setStatus)
      .catch(() => setStatus({ on: false }));
  }, []);
  useEffect(read, [read]);
  if (!status) return null;
  const description = !status.on ? t.sgdbOff : status.refused ? t.sgdbRefused : status.shared ? t.sgdbShared : t.sgdbOn;
  return (
    <PanelSectionRow>
      <ButtonItem
        layout="below"
        label={t.sgdbLabel}
        description={description}
        onClick={() => {
          if (status.on && !status.refused) forget().catch(() => null).then(read);
          else showModal(<KeyModal onDone={read} />);
        }}
      >
        {!status.on ? t.sgdbAdd : status.refused ? t.sgdbAgain : t.sgdbRemove}
      </ButtonItem>
    </PanelSectionRow>
  );
}

function KeyModal({ onDone, closeModal }: { onDone: () => void; closeModal?: () => void }) {
  const [key, setKeyText] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState("");
  const submit = () => {
    setBusy(true);
    setAnswer("");
    setKey(key.trim())
      .catch(() => ({ result: "error" }))
      .then((a) => {
        setBusy(false);
        if (a.result === "ok") {
          onDone();
          closeModal?.();
        } else setAnswer(a.result);
      });
  };
  return (
    <ConfirmModal
      strTitle={t.sgdbLabel}
      strDescription={t.sgdbSteps}
      strOKButtonText={t.sgdbKeep}
      bOKDisabled={busy || !key.trim()}
      bCancelDisabled={busy}
      bHideCloseIcon
      closeModal={() => undefined}
      onCancel={closeModal}
      onOK={submit}
    >
      <TextField
        label={t.sgdbField}
        value={key}
        bIsPassword
        disabled={busy}
        focusOnMount
        onChange={(e) => {
          setKeyText(e.target.value);
          setAnswer("");
        }}
      />
      {answer && <div style={{ marginTop: 14, lineHeight: 1.45, color: "var(--obs-warn, #ffb040)" }}>{t.sgdbAnswer(answer)}</div>}
    </ConfirmModal>
  );
}

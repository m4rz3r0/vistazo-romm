import { ButtonItem, ConfirmModal, PanelSection, PanelSectionRow, TextField, ToggleField, showModal } from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { t } from "./i18n";
import { GameState, addOwn, clearLostOwn, dropNotInstalled, gameState, loadOwned, lostOwn, onOwnChange, ownedApp, ownedRom, removeOwn, retryOwn, syncLibrary } from "./own";
import { openGame } from "./page";
import { PairAnswer, RommHealth, ServerAnswer, checkRomm, pairRomm, rommServer, setRommServer, unlinkRomm, useRomm } from "./romm";
import { setOwnGames, setSyncSaves, useOwnGames, useSyncSaves } from "./settings";

declare const appStore: any;

// The add-on's rows, shown inside Vistazo's settings and in its own Quick Access panel: the RomM
// connection, the switch that lets it look after games, and what to do with the game whose page
// is open.
export function Settings() {
  const games = useOwnGames();
  const saves = useSyncSaves();
  return (
    <PanelSection title={t.section}>
      <PanelSectionRow>
        <ToggleField label={t.ownGamesLabel} description={t.ownGamesDesc} checked={games} onChange={setOwnGames} />
      </PanelSectionRow>
      <ServerRow />
      <RommRow games={games} />
      {games && (
        <PanelSectionRow>
          <ToggleField label={t.savesLabel} description={t.savesDesc} checked={saves} onChange={setSyncSaves} />
        </PanelSectionRow>
      )}
      {games && <LibraryRow />}
      {games && <OwnRow />}
      {games && <LostRow />}
    </PanelSection>
  );
}

const useOwnChanges = (fn: () => void) => useEffect(() => onOwnChange(fn), [fn]);

declare const DeckyPluginLoader: any;
// RomM's whole library as entries in Steam, in one collection per console; each game is then
// installed from its own page. One row: bring it (again later, for what is new), and a way to
// take away the entries of games that are not installed.
function LibraryRow() {
  const status = useRomm();
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  if (status !== "linked") return null;
  // Tender has its own entries of the same games: bringing them all would show each one twice.
  const tender = !!(DeckyPluginLoader?.plugins ?? []).some((p: any) => p?.name === "Tender");
  const run = (what: () => Promise<string | null>, done: string) => {
    setBusy(true);
    setSaid(null);
    what().then((why) => {
      setSaid(why ? t.ownWhy(why) : done);
      setBusy(false);
    });
  };
  return (
    <>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          label={t.libraryLabel}
          description={said ?? (tender ? t.libraryTender : t.libraryDesc)}
          disabled={busy}
          onClick={() => run(() => syncLibrary((n, of) => setSaid(t.libraryStep(n, of))), t.libraryDone)}
        >
          {t.libraryBring}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={() => run(dropNotInstalled, t.libraryDropped)}>
          {t.libraryDrop}
        </ButtonItem>
      </PanelSectionRow>
    </>
  );
}

// RomM's address: the add-on's own (taken from Tender's the first time). One row with the address
// and a button to change it; the dialog checks that RomM answers there before keeping it.
function ServerRow() {
  const status = useRomm();
  const [url, setUrl] = useState<string | null>(null);
  const read = useCallback(() => {
    rommServer().then((s) => setUrl(s.url));
  }, []);
  useEffect(read, [read, status]);
  if (url === null) return null;
  return (
    <PanelSectionRow>
      <ButtonItem
        layout="below"
        label={t.serverLabel}
        description={url ? hostOf(url) : t.serverNone}
        onClick={() => showModal(<ServerModal onDone={read} />)}
      >
        {url ? t.serverChange : t.serverSet}
      </ButtonItem>
    </PanelSectionRow>
  );
}

// What the row shows of an address: without "https://", so it fits.
const hostOf = (url: string) => url.replace(/^https?:\/\//, "");

function ServerModal({ onDone, closeModal }: { onDone: () => void; closeModal?: () => void }) {
  const [url, setUrl] = useState("");
  const [insecure, setInsecure] = useState(false);
  const [askCert, setAskCert] = useState(false);
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<ServerAnswer | null>(null);
  const [kept, setKept] = useState({ url: "", insecure: false });
  useEffect(() => {
    rommServer().then((s) => {
      setKept(s);
      setUrl(s.url);
      setInsecure(s.insecure);
      setAskCert(s.insecure);
    });
  }, []);
  const done = answer === "ok" || answer === "unreachable";
  const submit = () => {
    setBusy(true);
    setAnswer(null);
    setRommServer(url.trim(), insecure).then((a) => {
      setAnswer(a);
      setBusy(false);
      if (a === "certificate") setAskCert(true);
      if (a === "ok" || a === "unreachable") onDone();
    });
  };
  const note = busy ? t.serverAsking : answer ? t.serverAnswer(answer) : "";
  return (
    <ConfirmModal
      strTitle={t.serverLabel}
      strDescription={t.serverSteps}
      strOKButtonText={done ? t.rommDone : t.serverKeep}
      bOKDisabled={busy || (!done && !url.trim())}
      bCancelDisabled={busy}
      bHideCloseIcon
      closeModal={() => undefined}
      onCancel={closeModal}
      onOK={() => (done ? closeModal?.() : submit())}
    >
      {!done && (
        <TextField
          label={t.serverField}
          value={url}
          disabled={busy}
          focusOnMount
          onChange={(e) => {
            setUrl(e.target.value);
            setAnswer(null);
            // Accepting a certificate is for the address it was asked about, not a new one.
            if (e.target.value.trim() !== kept.url) {
              setInsecure(false);
              setAskCert(false);
            } else {
              setInsecure(kept.insecure);
              setAskCert(kept.insecure);
            }
          }}
        />
      )}
      {!done && askCert && /^https:/i.test(url.trim()) && (
        <ToggleField label={t.serverInsecure} description={t.serverInsecureDesc} checked={insecure} disabled={busy} onChange={setInsecure} />
      )}
      {note && (
        <div style={{ marginTop: 14, lineHeight: 1.45, color: busy ? "rgba(255,255,255,0.6)" : done ? "#7fe0a8" : "var(--obs-warn, #ffb040)" }}>
          {note}
        </div>
      )}
    </ConfirmModal>
  );
}

// Vistazo's own RomM key, to see a retro game's achievements as soon as it closes and, with
// `games`, to look after the games themselves. One row: what state it is in and the one thing to
// do about it. A key for achievements only is enough until the games are Vistazo's: then the row
// asks to connect again, with a key that can see them. Opening the settings asks RomM whether the
// key still works, so the row never says "connected" about a key RomM has dropped.
// What came of the last try, said on the row too: it stays there after the dialog is gone.
let lastAnswer: PairAnswer | null = null;
function RommRow({ games }: { games: boolean }) {
  const status = useRomm();
  const [health, setHealth] = useState<RommHealth | null>(null);
  const [answer, setAnswer] = useState<PairAnswer | null>(lastAnswer);
  const heard = (a: PairAnswer | null) => {
    lastAnswer = a;
    setAnswer(a);
  };
  const has = status === "linked" || status === "limited";
  useEffect(() => {
    let alive = true;
    if (has) checkRomm().then((h) => alive && setHealth(h));
    else setHealth(null);
    return () => {
      alive = false;
    };
  }, [has]);
  if (!status || status === "no-server") return null;
  const short = games && status === "limited";
  const linked = has && !short;
  const failed = answer && answer.result !== "ok" && answer.result !== "limited" ? t.rommAnswer(answer, games) : null;
  const description = failed
    ? failed
    : short
    ? t.rommLimited
    : linked
      ? health === "unreachable"
        ? t.rommAsleep
        : games ? t.rommLinkedGames : t.rommLinked
      : status === "moved"
        ? t.rommMoved
        : health === "refused"
          ? t.rommDropped
          : games ? t.rommUnlinkedGames : t.rommUnlinked;
  return (
    <PanelSectionRow>
      <ButtonItem
        layout="below"
        label={games ? t.rommLabelGames : t.rommLabel}
        description={description}
        onClick={() => {
          heard(null);
          if (linked) unlinkRomm();
          else showModal(<PairModal games={games} onAnswer={heard} />);
        }}
      >
        {linked ? t.rommUnlink : short ? t.rommAgain : t.rommPair}
      </ButtonItem>
    </PanelSectionRow>
  );
}

// Where RomM's pairing code is typed: 8 letters and numbers, dashes and spaces ignored. It stays
// open while RomM is asked and says what came of it: connected, or exactly what is wrong (the
// code, RomM not answering, which permissions the key lacks or has too many of), ready for
// another code.
function PairModal({
  games,
  onAnswer,
  closeModal,
}: {
  games: boolean;
  onAnswer: (a: PairAnswer) => void;
  closeModal?: () => void;
}) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<PairAnswer | null>(null);
  const clean = code.toUpperCase().replace(/[^A-Z0-9]/g, "");
  const done = answer?.result === "ok";
  const submit = () => {
    setBusy(true);
    setAnswer(null);
    pairRomm(clean, games).then((a) => {
      setAnswer(a);
      onAnswer(a);
      setBusy(false);
      // A code is good for one try.
      if (a.result !== "ok" && a.result !== "code") setCode("");
    });
  };
  const note = busy ? t.rommAsking : answer ? t.rommAnswer(answer, games) : "";
  return (
    <ConfirmModal
      strTitle={t.rommPair}
      strDescription={games ? t.rommPairStepsGames : t.rommPairSteps}
      strOKButtonText={done ? t.rommDone : t.rommPair}
      bOKDisabled={busy || (!done && clean.length !== 8)}
      bCancelDisabled={busy}
      bHideCloseIcon
      // The dialog closes itself on either button: it only does when there is nothing left to say.
      closeModal={() => undefined}
      onCancel={closeModal}
      onOK={() => (done ? closeModal?.() : submit())}
    >
      {!done && (
        <TextField
          label={t.rommCode}
          value={code}
          disabled={busy}
          focusOnMount
          onChange={(e) => setCode(e.target.value.toUpperCase().slice(0, 12))}
        />
      )}
      {note && (
        <div
          style={{
            marginTop: 14,
            lineHeight: 1.45,
            color: busy ? "rgba(255,255,255,0.6)" : done ? "#7fe0a8" : "var(--obs-warn, #ffb040)",
          }}
        >
          {note}
        </div>
      )}
    </ConfirmModal>
  );
}

// Games of Vistazo's whose entry was removed in Steam itself: one row, only while there are any,
// to clear what Vistazo still keeps of them (its record, and the files it brought).
function LostRow() {
  const [lost, setLost] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [why, setWhy] = useState<string | null>(null);
  const read = useCallback(() => setLost(lostOwn()), []);
  useOwnChanges(read);
  // Read afresh every time the settings open.
  useEffect(() => {
    loadOwned(true).then(read);
  }, [read]);
  if (!lost.length) return null;
  return (
    <PanelSectionRow>
      <ButtonItem
        layout="below"
        label={t.ownLost(lost.length)}
        description={why ? t.ownWhy(why) : t.ownLostDesc}
        disabled={busy}
        onClick={() => {
          setBusy(true);
          clearLostOwn().then((w) => {
            setWhy(w);
            setBusy(false);
          });
        }}
      >
        {t.ownLostClear}
      </ButtonItem>
    </PanelSectionRow>
  );
}

// The game whose page is open, handed to Vistazo or taken back. Vistazo makes a second entry of
// its own in Steam for it (Tender keeps the one it made) and brings its file; one row says how
// that goes and holds the one thing to do next.
function OwnRow() {
  const status = useRomm();
  // The page's game is read again while the settings are open: its data may still be on the way.
  const [page, setPage] = useState(openGame);
  useEffect(() => {
    const timer = setInterval(() => {
      const now = openGame();
      setPage((was) => (was.appid === now.appid && was.romId === now.romId ? was : now));
    }, 1000);
    return () => clearInterval(timer);
  }, []);
  const [ready, setReady] = useState(false);
  const [state, setState] = useState<GameState | null>(null);
  const [busy, setBusy] = useState(false);
  const [why, setWhy] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    loadOwned().then(() => setReady(true));
  }, []);
  // The open page is Vistazo's own entry, or a game of Tender's that may have one.
  const romId = ownedRom(page.appid) || page.romId;
  const appid = ownedRom(page.appid) ? page.appid : ownedApp(romId);
  useEffect(() => {
    if (!ready || !appid) return setState(null);
    let alive = true;
    const read = () => gameState(appid).then((s) => alive && setState(s)).catch(() => undefined);
    read();
    const timer = setInterval(read, 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [ready, appid, tick]);
  if (status !== "linked" || !ready || !romId) return null;
  const name = String(appStore.GetAppOverviewByAppID(page.appid)?.display_name ?? "");
  if (!name) return null;
  const act = (what: () => Promise<string | null>) => {
    setBusy(true);
    setWhy(null);
    what().then((w) => {
      setWhy(w);
      setBusy(false);
      setTick((n) => n + 1);
    });
  };
  const job = state?.job?.state;
  const failed = !!job && !["downloading", "installed", "cancelled"].includes(job);
  const description = why
    ? t.ownWhy(why)
    : !appid
      ? t.ownOffer
      : job === "downloading"
        ? t.ownDownloading(state?.job?.total ? Math.floor((100 * state.job.done) / state.job.total) : 0)
        : state?.installed
          ? t.ownReady
          : failed
            ? t.ownWhy(job)
            : t.ownNoFile;
  return (
    <>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          label={name}
          description={description}
          disabled={busy}
          onClick={() => (appid ? act(() => removeOwn(appid)) : act(() => addOwn(romId, t.ownName(name), page.appid)))}
        >
          {appid ? t.ownRemove : t.ownAdd}
        </ButtonItem>
      </PanelSectionRow>
      {!!appid && !!state && !state.installed && job !== "downloading" && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => act(() => retryOwn(appid).then(() => null))}>
            {t.ownDownload}
          </ButtonItem>
        </PanelSectionRow>
      )}
    </>
  );
}

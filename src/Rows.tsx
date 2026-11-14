import { ButtonItem, ConfirmModal, DropdownItem, PanelSection, PanelSectionRow, TextField, ToggleField, showModal } from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { t } from "./i18n";
import { GameState, RommCollection, Version, busyJob, gameEmulators, setEmulator, cancelOwn, clearGone, goneOwn, pauseOwn, playState, gameVersions, running, switchVersion, TenderGame, addOwn, rommCollections, turnCollection, giveBackTender, takeOverTender, tenderGames, tenderOn, clearLostOwn, dropNotInstalled, gameState, loadOwned, lostOwn, onOwnChange, ownedApp, ownedRom, removeOwn, retryOwn, syncLibrary } from "./own";
import { openGame } from "./page";
import { PairAnswer, RommHealth, ServerAnswer, checkRomm, pairRomm, rommServer, setRommServer, unlinkRomm, useRomm } from "./romm";
import { LANGUAGES, setOwnGames, setPreferredLanguage, setSyncSaves, useOwnGames, usePreferredLanguage, useSyncSaves } from "./settings";

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
      {games && <TenderRow />}
      {games && <LanguageRow />}
      {games && <LibraryRow />}
      {games && <CollectionsRow />}
      {games && <OwnRow />}
      {games && <LostRow />}
      {games && <GoneRow />}
      {games && <PlayRow />}
    </PanelSection>
  );
}

const useOwnChanges = (fn: () => void) => useEffect(() => onOwnChange(fn), [fn]);

declare const DeckyPluginLoader: any;
// Tender's entries: one row while Tender has any. With Tender on, it says to turn it off first;
// off, it takes them over (or gives them back). Taking over asks first: it changes Steam entries.
function TenderRow() {
  const status = useRomm();
  const [list, setList] = useState<TenderGame[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const read = useCallback(() => {
    tenderGames()
      .then(setList)
      .catch(() => setList([]));
  }, []);
  useEffect(read, [read]);
  useOwnChanges(read);
  const on = tenderOn();
  const theirs = (list ?? []).filter((g) => !g.mine).length;
  // Once Tender is gone and its games are all here, there is nothing left to do with them.
  const installed = on || !!(DeckyPluginLoader?.deckyState?.publicState?.()?.installedPlugins ?? []).some((p: any) => p?.name === "Tender");
  if (status !== "linked" || !list?.length || (!theirs && !installed)) return null;
  const mine = list.length - theirs;
  const run = (what: () => Promise<string | null>, done: string) => {
    setBusy(true);
    setSaid(null);
    what().then((why) => {
      setSaid(why ? t.ownWhy(why) : done);
      setBusy(false);
      read();
    });
  };
  const take = () =>
    showModal(
      <ConfirmModal
        strTitle={t.tenderTitle}
        strDescription={t.tenderConfirm(theirs)}
        strOKButtonText={t.tenderTake}
        onOK={() => run(() => takeOverTender((n, of) => setSaid(t.tenderStep(n, of))), t.tenderTaken)}
      />,
    );
  return (
    <>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          label={t.tenderLabel}
          description={said ?? (on ? (mine ? t.tenderOnMine : t.tenderOn(theirs)) : theirs ? t.tenderOff(theirs) : t.tenderAllMine(mine))}
          disabled={busy || on || !theirs}
          onClick={take}
        >
          {t.tenderTake}
        </ButtonItem>
      </PanelSectionRow>
      {mine > 0 && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => run(giveBackTender, t.tenderGiven)}>
            {t.tenderGive}
          </ButtonItem>
        </PanelSectionRow>
      )}
    </>
  );
}

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

// Which version of a game new entries use, when RomM keeps several (languages, regions).
function LanguageRow() {
  const status = useRomm();
  const language = usePreferredLanguage();
  if (status !== "linked") return null;
  return (
    <PanelSectionRow>
      <DropdownItem
        label={t.languageLabel}
        description={t.languageDesc}
        rgOptions={LANGUAGES.map((l) => ({ data: l, label: l ? t.languageName(l) : t.languageMain }))}
        selectedOption={language}
        onChange={(o) => setPreferredLanguage(String(o.data))}
      />
    </PanelSectionRow>
  );
}

// What one version is called in the list, short enough to fit: its languages, with its regions
// only when another version has the same languages.
const langsOf = (v: Version) => v.languages.map(t.languageName).join(", ");
const versionName = (v: Version, all: Version[]) => {
  const langs = langsOf(v);
  const twin = all.some((o) => o !== v && langsOf(o) === langs);
  const regions = v.regions.map(t.regionName).join(", ");
  return (twin || !langs ? [langs, regions].filter(Boolean).join(" · ") : langs) || v.file;
};

// The open game's version, when RomM keeps more than one.
function VersionRow({ appid, state, onDone }: { appid: number; state: GameState | null; onDone: () => void }) {
  const [list, setList] = useState<Version[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const read = useCallback(() => {
    gameVersions(appid)
      .then((r) => setList(r.ok ? r.versions : null))
      .catch(() => setList(null));
  }, [appid]);
  useEffect(read, [read]);
  if (!list || list.length < 2) return null;
  const mine = list.find((v) => v.current);
  const current = mine?.rom_id ?? 0;
  const job = state?.job;
  const bringing = busy && busyJob(state);
  const change = (romId: number) => {
    if (romId === current) return;
    if (running(appid)) return setSaid(t.ownWhy("running"));
    setBusy(true);
    setSaid(null);
    switchVersion(appid, romId).then((why) => {
      setSaid(why ? t.ownWhy(why) : t.versionDone);
      setBusy(false);
      read();
      onDone();
    });
  };
  return (
    <PanelSectionRow>
      <DropdownItem
        label={t.versionLabel}
        description={
          bringing
            ? t.versionBringing(job?.total ? Math.floor((100 * job.done) / job.total) : 0)
            : state?.switching
              ? t.versionWaiting
            : (said ?? (mine && !mine.achievements ? `${t.versionNoAchievements} ${t.versionDesc}` : t.versionDesc))
        }
        disabled={busy || busyJob(state) || !!state?.switching}
        rgOptions={list.map((v) => ({ data: v.rom_id, label: versionName(v, list) }))}
        selectedOption={current}
        onChange={(o) => change(Number(o.data))}
      />
    </PanelSectionRow>
  );
}

// RomM's collections: one row saying how many are shown in Steam, and a dialog to choose them.
function CollectionsRow() {
  const status = useRomm();
  const [shown, setShown] = useState<number | null>(null);
  const read = useCallback(() => {
    rommCollections()
      .then((r) => setShown(r.ok ? r.collections.filter((c) => c.on).length : null))
      .catch(() => setShown(null));
  }, []);
  useEffect(read, [read, status]);
  if (status !== "linked") return null;
  return (
    <PanelSectionRow>
      <ButtonItem
        layout="below"
        label={t.collectionsLabel}
        description={shown ? t.collectionsOn(shown) : t.collectionsDesc}
        onClick={() => showModal(<CollectionsModal onDone={read} />)}
      >
        {t.collectionsChoose}
      </ButtonItem>
    </PanelSectionRow>
  );
}

const KIND_ORDER = ["standard", "smart", "collection", "franchise", "genre", "company", "mode"];
const groupOf = (c: RommCollection) => (c.kind === "virtual" ? c.sort : c.kind);

function CollectionsModal({ onDone, closeModal }: { onDone: () => void; closeModal?: () => void }) {
  const [list, setList] = useState<RommCollection[] | null>(null);
  const [why, setWhy] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  useEffect(() => {
    rommCollections()
      .then((r) => (r.ok ? setList(r.collections) : setWhy(r.why ?? "error")))
      .catch(() => setWhy("unreachable"));
  }, []);
  const turn = (c: RommCollection, on: boolean) => {
    setBusy(true);
    setSaid(null);
    turnCollection(c, on, (n, of) => setSaid(t.libraryStep(n, of))).then((w) => {
      // What is kept decides how each switch stands, whatever came of bringing the games.
      rommCollections()
        .then((r) => r.ok && setList(r.collections))
        .catch(() => undefined);
      setSaid(w ? t.ownWhy(w) : on ? t.collectionsDone(c.name) : t.collectionsOff(c.name));
      setBusy(false);
      onDone();
    });
  };
  const groups = KIND_ORDER.map((k) => [k, (list ?? []).filter((c) => groupOf(c) === k)] as const).filter(([, cs]) => cs.length);
  const note = why ? t.ownWhy(why) : !list ? t.collectionsLoading : !list.length ? t.collectionsNone : said;
  return (
    <ConfirmModal
      strTitle={t.collectionsLabel}
      strDescription={t.collectionsSteps}
      strOKButtonText={t.rommDone}
      bAlertDialog
      bOKDisabled={busy}
      closeModal={() => undefined}
      onOK={() => closeModal?.()}
    >
      {note && <div style={{ margin: "10px 0", lineHeight: 1.45, color: "rgba(255,255,255,0.7)" }}>{note}</div>}
      {groups.map(([k, cs]) => (
        <div key={k} style={{ marginTop: 12 }}>
          <div style={{ fontSize: 12, letterSpacing: 0.4, textTransform: "uppercase", color: "rgba(255,255,255,0.55)" }}>{t.collectionsKind(k)}</div>
          {cs.map((c) => (
            <ToggleField
              key={c.key}
              label={c.name}
              description={t.collectionsGames(c.roms.length)}
              checked={c.on}
              disabled={busy}
              onChange={(on) => turn(c, on)}
            />
          ))}
        </div>
      ))}
    </ConfirmModal>
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

// The emulator the open game starts with: RetroDECK's choice, or one of ES-DE's for its console.
// Saves are kept in step only with the one whose saves the add-on knows: said when another is picked.
function EmulatorRow({ appid }: { appid: number }) {
  const [info, setInfo] = useState<{ emulators: string[]; chosen: string; saves: string } | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    gameEmulators(appid)
      .then((e) => alive && setInfo(e?.ok ? e : null))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [appid]);
  if (!info || info.emulators.length < 2) return null;
  const noSaves = !!info.chosen && !!info.saves && info.chosen !== info.saves;
  return (
    <PanelSectionRow>
      <DropdownItem
        label={t.emulatorLabel}
        description={said ?? (noSaves ? t.emulatorNoSaves(info.saves) : t.emulatorDesc)}
        rgOptions={[{ data: "", label: t.emulatorDefault }, ...info.emulators.map((e) => ({ data: e, label: e }))]}
        selectedOption={info.chosen}
        onChange={(o) => {
          if (running(appid)) return setSaid(t.ownWhy("running"));
          setEmulator(appid, String(o.data))
            .then((r) => {
              setSaid(r?.ok ? null : t.ownWhy(r?.why ?? "error"));
              if (r?.ok) setInfo({ ...info, chosen: String(o.data) });
            })
            .catch(() => setSaid(t.ownWhy("error")));
        }}
      />
    </PanelSectionRow>
  );
}

// Games of Vistazo's that were deleted in RomM: one row, only while there are any. RomM is asked
// each time the settings open; removing the entries asks first, as it takes them out of Steam.
function GoneRow() {
  const [gone, setGone] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [why, setWhy] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    loadOwned()
      .then(() => goneOwn())
      .then((apps) => alive && setGone(apps));
    return () => {
      alive = false;
    };
  }, []);
  if (!gone.length) return null;
  const names = gone.map((a) => String(appStore.GetAppOverviewByAppID(a)?.display_name ?? a)).join(", ");
  return (
    <PanelSectionRow>
      <ButtonItem
        layout="below"
        label={t.ownGone(gone.length)}
        description={why ? t.ownWhy(why) : t.ownGoneDesc}
        disabled={busy}
        onClick={() =>
          showModal(
            <ConfirmModal
              strTitle={t.ownGoneAsk}
              strDescription={t.ownGoneAskDesc(names)}
              strOKButtonText={t.ownGoneClear}
              onOK={() => {
                setBusy(true);
                clearGone(gone).then((w) => {
                  setWhy(w);
                  setBusy(false);
                  goneOwn(true).then(setGone);
                });
              }}
            />,
          )
        }
      >
        {t.ownGoneClear}
      </ButtonItem>
    </PanelSectionRow>
  );
}

// Play time goes to RomM only with a key that may write it: said once, while it may not.
function PlayRow() {
  const status = useRomm();
  const [allowed, setAllowed] = useState(true);
  useEffect(() => {
    if (status === "linked") playState().then((p) => setAllowed(!!p?.allowed)).catch(() => undefined);
  }, [status]);
  if (status !== "linked" || allowed) return null;
  return (
    <PanelSectionRow>
      <div style={{ fontSize: 12, lineHeight: 1.45, opacity: 0.7 }}>{t.playLimited}</div>
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
  const share = state?.job?.total ? Math.floor((100 * state.job.done) / state.job.total) : 0;
  const failed = !!job && !["queued", "downloading", "paused", "installed", "cancelled"].includes(job);
  // What a stopped download left on the disk, to go on from or throw away.
  const half = !!state && !state.installed && (job === "paused" || (failed && !!state.job?.done));
  const description = why
    ? t.ownWhy(why)
    : !appid
      ? t.ownOffer
      : job === "queued"
        ? t.ownQueued
        : job === "downloading"
          ? t.ownDownloading(share)
          : state?.installed
            ? t.ownReady
            : job === "paused"
              ? t.ownPaused(share)
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
      {!!appid && <VersionRow appid={appid} state={state} onDone={() => setTick((n) => n + 1)} />}
      {!!appid && <EmulatorRow appid={appid} />}
      {!!appid && busyJob(state) && !state?.switching && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => act(() => pauseOwn(appid).then(() => null))}>
            {t.ownPause}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {!!appid && !!state && !state.installed && !busyJob(state) && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => act(() => retryOwn(appid).then(() => null))}>
            {half ? t.ownResume : t.ownDownload}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {!!appid && half && (
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => act(() => cancelOwn(appid).then(() => null))}>
            {t.ownDiscard}
          </ButtonItem>
        </PanelSectionRow>
      )}
    </>
  );
}

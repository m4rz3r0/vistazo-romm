import { DropdownItem, PanelSectionRow } from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { t } from "./i18n";
import { ConsoleLayout, chooseLayout, consoleName, inputLayouts } from "./input";
import { onOwnChange } from "./own";

// The Steam Input layout per console: one dropdown for each console with entries. "Leave as is"
// changes nothing; a layout is put on all that console's entries, and on the ones added later.
export function InputRows() {
  const [info, setInfo] = useState<{ layouts: string[]; consoles: ConsoleLayout[] } | null>(null);
  const read = useCallback(() => {
    inputLayouts()
      .then((r) => setInfo(r?.ok ? r : null))
      .catch(() => setInfo(null));
  }, []);
  useEffect(read, [read]);
  useEffect(() => onOwnChange(read), [read]);
  if (!info?.layouts.length) return null;
  return (
    <>
      {info.consoles.map((c) => (
        <ConsoleRow key={c.system} entry={c} layouts={info.layouts} onDone={read} />
      ))}
    </>
  );
}

function ConsoleRow({ entry, layouts, onDone }: { entry: ConsoleLayout; layouts: string[]; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const [chosen, setChosen] = useState(entry.layout);
  useEffect(() => setChosen(entry.layout), [entry.layout]);
  const pick = (layout: string) => {
    setBusy(true);
    setSaid(t.inputBusy);
    setChosen(layout);
    chooseLayout(entry.system, layout).then((r) => {
      setBusy(false);
      if (r.why === "nodeck") setSaid(t.inputNoDeck);
      else if (r.why) {
        setChosen(entry.layout); // not kept: the choice before stands
        setSaid(t.ownWhy(r.why));
      } else if (!layout) setSaid(t.inputKept);
      else if (!r.of) setSaid(null);
      else if (!r.done) setSaid(t.inputNone);
      else if (r.done !== r.of) setSaid(t.inputSome(r.done ?? 0, r.of));
      else setSaid(t.inputDone(r.of));
      onDone();
    });
  };
  return (
    <PanelSectionRow>
      <DropdownItem
        label={t.inputLabel(consoleName(entry.system))}
        description={said ?? t.inputDesc}
        disabled={busy}
        rgOptions={[{ data: "", label: t.inputKeep }, ...layouts.map((l) => ({ data: l, label: t.inputLayout(l) }))]}
        selectedOption={chosen}
        onChange={(o) => pick(String(o.data))}
      />
    </PanelSectionRow>
  );
}

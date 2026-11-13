import { useEffect, useState } from "react";
import { safe } from "./safe";

// The one choice this add-on keeps (on the Deck, in the Steam interface's own storage): whether it
// looks after RomM games itself, or only keeps their achievements fresh. Off until turned on.
const KEY = "vistazo-romm:games";
// It used to be one of Vistazo's own settings: taken from there the first time.
function first(): boolean {
  const mine = safe(() => localStorage.getItem(KEY), null);
  if (mine !== null) return mine === "1";
  const was = safe(() => JSON.parse(localStorage.getItem("vistazo:settings") ?? "null")?.ownGames === true, false);
  safe(() => localStorage.setItem(KEY, was ? "1" : "0"), undefined);
  return was;
}
let on = first();
const listeners = new Set<(v: boolean) => void>();

export const ownGames = () => on;
export function setOwnGames(value: boolean) {
  if (on === value) return;
  on = value;
  safe(() => localStorage.setItem(KEY, value ? "1" : "0"), undefined);
  listeners.forEach((fn) => fn(on));
  // What Vistazo shows depends on it.
  safe(() => (window as any).Vistazo?.changed(), undefined);
}
export function useOwnGames(): boolean {
  const [v, set] = useState(on);
  useEffect(() => {
    listeners.add(set);
    return () => {
      listeners.delete(set);
    };
  }, []);
  return v;
}

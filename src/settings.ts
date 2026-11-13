import { useEffect, useState } from "react";
import { safe } from "./safe";

// The choices this add-on keeps (on the Deck, in the Steam interface's own storage): whether it
// looks after RomM games itself, and whether it keeps their saves in step with RomM. Both off
// until turned on.
const listeners = new Set<() => void>();
function flag(key: string, first: () => boolean) {
  let on = first();
  return {
    get: () => on,
    set(value: boolean) {
      if (on === value) return;
      on = value;
      safe(() => localStorage.setItem(key, value ? "1" : "0"), undefined);
      listeners.forEach((fn) => fn());
      // What Vistazo shows depends on it.
      safe(() => (window as any).Vistazo?.changed(), undefined);
    },
  };
}
const stored = (key: string) => safe(() => localStorage.getItem(key), null);

const games = flag("vistazo-romm:games", () => {
  const mine = stored("vistazo-romm:games");
  if (mine !== null) return mine === "1";
  // It used to be one of Vistazo's own settings: taken from there the first time.
  const was = safe(() => JSON.parse(localStorage.getItem("vistazo:settings") ?? "null")?.ownGames === true, false);
  safe(() => localStorage.setItem("vistazo-romm:games", was ? "1" : "0"), undefined);
  return was;
});
const saves = flag("vistazo-romm:saves", () => stored("vistazo-romm:saves") === "1");

export const ownGames = games.get;
export const setOwnGames = games.set;
export const syncSaves = saves.get;
export const setSyncSaves = saves.set;

function useFlag(get: () => boolean): boolean {
  const [v, set] = useState(get());
  useEffect(() => {
    const fn = () => set(get());
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  }, [get]);
  return v;
}
export const useOwnGames = () => useFlag(games.get);
export const useSyncSaves = () => useFlag(saves.get);

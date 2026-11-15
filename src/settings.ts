import { useEffect, useState } from "react";
import { safe } from "./safe";

// The choices this add-on keeps (on the Deck, in the Steam interface's own storage). Looking after
// RomM games itself and keeping their saves in step with RomM used to be switches; both are always on
// now, and the old stored choices are ignored.
const listeners = new Set<() => void>();
const stored = (key: string) => safe(() => localStorage.getItem(key), null);

// The language whose version of a game new entries start (as RomM names it: "Spanish"); empty
// for the one RomM marks as the main version.
let language = stored("vistazo-romm:language") ?? "";
export const LANGUAGES = ["", "Spanish", "English"];
export const preferredLanguage = () => language;
export function setPreferredLanguage(value: string) {
  if (language === value) return;
  language = value;
  safe(() => localStorage.setItem("vistazo-romm:language", value), undefined);
  listeners.forEach((fn) => fn());
}
export function usePreferredLanguage(): string {
  const [v, set] = useState(language);
  useEffect(() => {
    const fn = () => set(language);
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  }, []);
  return v;
}

export const ownGames = () => true;
export const syncSaves = () => true;

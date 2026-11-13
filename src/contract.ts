import type { ComponentType } from "react";

// The contract between Vistazo and its add-ons (version 1), as Vistazo's own src/addons.ts has it.
// An add-on hands over facts and things to do; Vistazo draws them.
export const CONTRACT = 1;

export interface AddonAchievement {
  id: string;
  title: string;
  description: string;
  points: number;
  players: number; // how many players have it
  badge: string; // picture when earned (https)
  badgeLocked: string; // picture while pending (https)
  order: number; // the game's own order
}

// What an add-on knows of one game, for its page. Everything is optional: what is missing is
// simply not shown. Texts come already in the player's language.
export interface AddonGame {
  platform?: string;
  year?: number;
  genres?: string[];
  rating?: number; // 0-100
  size?: number; // bytes
  summary?: string;
  achievements?: { earned: number; total: number; hardcore: number; got: string[]; list: AddonAchievement[] };
  stale?: boolean; // the source did not answer: this is from the last time it did
}

// The game's file, when the add-on is what brings it: whether it is on the Deck, and how a
// download goes (`share` 0-100; `problem` in the player's language when one failed).
export interface AddonFile {
  here: boolean;
  downloading: boolean;
  share: number;
  problem?: string;
}

// A shelf an add-on brings to the home: Steam entries it made, under its own name.
export interface AddonShelf {
  id: string;
  label: string;
  apps: number[];
}

// Something to do with a game, shown as a quiet button on its page.
export interface AddonAction {
  id: string;
  label: string;
  run(): void | Promise<void>;
}

// A player's achievements in one game, by its RetroAchievements number.
export interface AddonProgress {
  ok: boolean;
  why?: string;
  changed?: boolean;
  earned?: number;
  hardcore?: number;
  total?: number;
  ids?: string[];
  hardcore_ids?: string[];
}

export interface Addon {
  id: string; // short and fixed: "romm", "geforcenow"
  name: string;
  contract: number; // the contract it was written for
  // Whether this non-Steam entry is the add-on's (its page is then Vistazo's, with `game`).
  owns?(appid: number): boolean;
  // The game behind one of its entries; `refresh` when it was just played.
  game?(appid: number, refresh: boolean): Promise<AddonGame | null>;
  // The game's file (asked often while a download runs: keep it light). None: nothing to bring.
  file?(appid: number): Promise<AddonFile | null>;
  // Bringing the game's file, and stopping that.
  fetchFile?(appid: number): Promise<void>;
  cancelFile?(appid: number): Promise<void>;
  // Its entries for the home's Retro shelf (marked with the eye).
  shelf?(): number[];
  // Shelves of its own on the home (cloud games, say): the player turns each on in the settings.
  shelves?(): AddonShelf[];
  // Things to do on a game's page: a Steam game's ("play on …") or one of its own ("uninstall").
  // None: an empty list.
  actions?(appid: number): Promise<AddonAction[]>;
  // Achievements straight from the add-on's source, for pages another program draws.
  progress?(raId: number, refresh: boolean): Promise<AddonProgress | null>;
  // Its own section in Vistazo's settings (plain Decky rows).
  Settings?: ComponentType;
}

export interface VistazoApi {
  contract: number;
  register(addon: Addon): () => void;
  changed(): void; // an add-on's games changed: whatever shows them reads again
}

# Vistazo RomM

An add-on for [Vistazo](https://github.com/m4rz3r0/vistazo-decky) on the Steam Deck, for those who keep their retro
games in [RomM](https://github.com/rommapp/romm). Vistazo draws everything; this plugin only brings what RomM knows.
It does nothing without Vistazo installed.

## What it does

- **Fresh achievements.** When a retro game closes, RomM reads your RetroAchievements again and Vistazo's page shows
  them at once.
- **Games by Vistazo.** From a game's page, the settings add a second entry
  of it to Steam that this add-on downloads from RomM and starts in RetroDECK, with Vistazo's own page. Removing it
  takes away that entry and only the files the add-on brought. It works next to
  [Tender](https://github.com/danielcopper/romm-tender), which keeps the entries it made.
- **Downloads.** One game comes at a time; the others wait their turn. A download can be paused (on the game's page,
  stopping it pauses it) and goes on from where it stopped, even after a restart, still checked against RomM's sum.
- **Play time.** Each session of a game played from its entry goes to RomM, with the time played (the Deck's sleep
  left out). Sessions wait on the Deck while RomM is asleep and go once it answers. It needs a token that can also
  write Roms user.
- **This device in RomM.** With a token that can also write Devices, the add-on registers the device once in RomM as
  a "Steam Deck", or as a "PC" with its system's name (no host name, user name or address goes with it), so RomM
  shows which saves and play sessions came from it. If the device is deleted in RomM, it is registered again.
- **Games deleted in RomM.** The settings list the entries whose game RomM no longer has, and remove them when you
  say so: only the files the add-on brought go, saves stay.
- **RetroDECK's folders moved.** When RetroDECK's games or saves folder moves (to the SD card, say, with RetroDECK's
  own tool), the entries follow the games and saves found at the same place in the new folder, with the same size
  (saves: the same content). The add-on never moves files itself; a game not found there is left as it was.
- **Versions of a game.** When RomM keeps a game in several versions (languages, regions), *Language of the games*
  picks which one new entries use, and the panel, with the game's page open, switches the one an entry starts. An
  installed game gets the other version brought first (the old one plays meanwhile); its save goes with it when the
  new version has none, the old save stays as it is, and the old file goes only if the add-on brought it.
- **Emulator of a game.** The panel, with the game's page open, picks which of RetroDECK's emulators for its console
  the entry starts (RetroDECK's own list and commands). Save games are kept in step only with one emulator per
  console (melonDS DS for DS, Dolphin standalone for Wii), so with nothing picked those consoles start with it, even
  where RetroDECK would take another: a fresh RetroDECK starts DS games with DeSmuME, whose saves are not RomM's.
  An alternative emulator set in ES-DE does not apply to these entries; pick it here instead. Other consoles start
  as RetroDECK decides. The panel says when the emulator picked does not keep its saves in step.
- **Disc of a game.** For a game of several discs, the panel, with the game's page open, picks which disc the entry
  starts, or all of them through a playlist so the emulator changes discs itself (RomM's playlist, or one the add-on
  makes in the game's folder and removes with the game). Nothing chosen starts the game as before.
- **Controls per console.** The settings pick, per console with entries, the Steam Input layout of the Deck's own
  controls: *Leave as is* (the default, changes nothing), RetroDECK's layout with hotkeys (once RetroDECK installed its
  templates), a plain gamepad, or a gamepad with the right trackpad as a mouse (handy for the DS screen or the Wii
  pointer). It is put on all that console's entries and on the ones added later, through Steam's own layout picker; no
  Steam file is written. Steam keeps a non-Steam entry's layout under its name, so another entry with the same name
  shares it.
- **RomM's collections in Steam.** The settings list RomM's collections: the ones people make, smart ones, and the
  ones RomM makes itself (series, franchises, genres, companies, game modes). Each one turned on shows in Steam as
  "RomM: [name]" and brings the entries of its games that are missing; turning it off removes only that collection.
  Bringing the library again keeps the ones turned on up to date.
- **Taking over from Tender.** Once Tender is turned off in Decky, the add-on's settings can keep Tender's entries:
  they keep their play time, pictures and collections, the games Tender downloaded can be removed from their page, and
  the add-on's own second entries of the same games go away. It can be given back while Tender is still installed.
  Uninstall Tender afterwards from Decky, never with its option to remove all games.
- **Save games** (DS and Wii for now). When a game played from its Vistazo entry closes, its save goes
  to RomM; a newer one on RomM is brought only when the Deck's has not changed, and only after keeping a copy of it.
  When both changed, nothing is touched until you choose on the game's page. A short notice says what happened.
  The save is also checked right as a game starts, before the emulator opens it: a newer one played on another device
  is brought first (the start waits a few seconds for it). When RomM cannot be asked, or both changed, the game starts
  as it is and a notice warns not to save over it. A save RomM did not take when the game closed is sent again later,
  and when Steam next starts.
- **Pictures.** Each entry gets its cover from RomM and, when RomM keeps them, ScreenScraper's logo and fanart
  (in RomM's `config.yml`, add `fanart` and `logo` to `scan.media`, then rescan with "update").
  The logo and wide picture RomM lacks are copied from Tender's entry of the same game while Tender is installed.
- **Background.** SteamGridDB's first (with your key, see below), then ScreenScraper's fanart, which is sometimes of
  another game of the series; with neither, Vistazo shows the cover. **Change background**, in the game's settings,
  shows thumbnails of every background there is for it (ScreenScraper's and, with your key, up to 12 of
  SteamGridDB's) and *Only the cover*. The one you pick stays: nothing replaces it or takes it off.
- **SteamGridDB.** With your own [SteamGridDB](https://www.steamgriddb.com) key (off until you paste one in **Pictures from
  SteamGridDB**; it is on steamgriddb.com under Preferences › API), what is still missing comes from there: cover, wide
  picture, background and logo, and the icon of a new entry. A picture RomM or you already gave is never replaced (with
  your key, a background an earlier version took from ScreenScraper's fanart is taken off once so SteamGridDB's can
  come first, only when it is that very picture), and a game
  whose name matches none or several games on SteamGridDB gets nothing rather than another game's pictures.
  You type the key once: it is also kept in your RomM account (its UI settings, which only you and RomM's admins can
  read), and every other device linked to that account picks it up within minutes. **Remove** takes it off on all of
  them. RomM's own SteamGridDB key never leaves the server and only finds covers, so it cannot be used for this.
- **BIOS files.** The game's page says whether the BIOS files RomM has for its console are on the Deck, and brings the
  missing ones, checked against RomM's sums. *Bring all BIOS files*, in the settings, does the same at once for every
  console RomM has games of, with how far it has got. Each file goes where RetroDECK's own list of BIOS files puts it
  (most in its BIOS folder, the Dreamcast's in `dc`, PICO-8's in `pico-8`, the PS2's where PCSX2 is told to look) and is
  never written over another. The GameCube's and MAME's arcade sets are left alone.

## Settings

In Vistazo's settings, under RomM, or in the add-on's own panel, by topic: **Connection**, **This game** (with a game's
page open), **Library**, **Saves and BIOS**, **Controls**, **Pictures** and **Upkeep** (shown only when there is
something to tidy up). Games by Vistazo and keeping saves in step are always on.

## Connecting to RomM

The add-on keeps RomM's address in its own settings (**RomM address**). With Tender installed it takes Tender's the
first time and keeps it, so it goes on working once Tender is gone. A new address is kept only where RomM answers or
nothing answers yet; for a server with a certificate of your own you can choose to accept it. A key is only ever sent
to the address it was made for: after changing it, connect again.

In RomM, under Settings › Client API Tokens, create a token and press **Pair device**; type the short code on the
Deck. For achievements only, the token needs just Me (read and write). To look after games it also needs Assets
(read and write), Collections (read), Firmware (read), Platforms (read) and Roms (read); to send play time, Roms user
(read and write) too; for RomM to show the Deck as the device saves and play time come from, Devices (write). A token that can do more than that is refused and deleted at RomM.

## Privacy and safety

The add-on talks only to your own RomM server, at the address in its settings, with its own token kept on the Deck for
your user only. It never follows a redirect, checks every download against RomM's own sum, and removes only files
it brought itself. A game starts through a small launcher that Steam hands nothing but the game's number: no file
name goes through Steam's launch options. No telemetry.

Only when you give it a SteamGridDB key does the add-on talk to anyone else: it sends SteamGridDB the name of a game
whose pictures are missing, over HTTPS, with that key. The key is kept on the device for your user only, is never logged
and goes nowhere but SteamGridDB and your own RomM account, which hands it to your other devices; the pictures
themselves are fetched from SteamGridDB's image servers without it.

## Building

`sh pack.sh` builds `out/VistazoRomM.zip`. `python3 tools/test_backend.py` checks the backend against a pretend RomM
in a throwaway folder.

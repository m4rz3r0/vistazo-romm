# Vistazo RomM

An add-on for [Vistazo](https://github.com/m4rz3r0/vistazo-decky) on the Steam Deck, for those who keep their retro
games in [RomM](https://github.com/rommapp/romm). Vistazo draws everything; this plugin only brings what RomM knows.
It does nothing without Vistazo installed.

## What it does

- **Fresh achievements.** When a retro game closes, RomM reads your RetroAchievements again and Vistazo's page shows
  them at once.
- **Games by Vistazo** (experimental, off until you turn it on). From a game's page, the settings add a second entry
  of it to Steam that this add-on downloads from RomM and starts in RetroDECK, with Vistazo's own page. Removing it
  takes away that entry and only the files the add-on brought. It works next to
  [Tender](https://github.com/danielcopper/romm-tender), which keeps the entries it made.
- **Downloads.** One game comes at a time; the others wait their turn. A download can be paused (on the game's page,
  stopping it pauses it) and goes on from where it stopped, even after a restart, still checked against RomM's sum.
- **Play time.** Each session of a game played from its entry goes to RomM, with the time played (the Deck's sleep
  left out). Sessions wait on the Deck while RomM is asleep and go once it answers. It needs a token that can also
  write Roms user.
- **Games deleted in RomM.** The settings list the entries whose game RomM no longer has, and remove them when you
  say so: only the files the add-on brought go, saves stay.
- **Versions of a game.** When RomM keeps a game in several versions (languages, regions), *Language of the games*
  picks which one new entries use, and the panel, with the game's page open, switches the one an entry starts. An
  installed game gets the other version brought first (the old one plays meanwhile); its save goes with it when the
  new version has none, the old save stays as it is, and the old file goes only if the add-on brought it.
- **Emulator of a game.** The panel, with the game's page open, picks which of RetroDECK's emulators for its console
  the entry starts (RetroDECK's own list and commands), or leaves it to RetroDECK. Save games are kept in step only
  with the usual one (melonDS DS for DS, Dolphin standalone for Wii); the panel says so when another is picked.
- **Disc of a game.** For a game of several discs, the panel, with the game's page open, picks which disc the entry
  starts, or all of them through a playlist so the emulator changes discs itself (RomM's playlist, or one the add-on
  makes in the game's folder and removes with the game). Nothing chosen starts the game as before.
- **RomM's collections in Steam.** The settings list RomM's collections: the ones people make, smart ones, and the
  ones RomM makes itself (series, franchises, genres, companies, game modes). Each one turned on shows in Steam as
  "RomM: [name]" and brings the entries of its games that are missing; turning it off removes only that collection.
  Bringing the library again keeps the ones turned on up to date.
- **Taking over from Tender.** Once Tender is turned off in Decky, the add-on's settings can keep Tender's entries:
  they keep their play time, pictures and collections, the games Tender downloaded can be removed from their page, and
  the add-on's own second entries of the same games go away. It can be given back while Tender is still installed.
  Uninstall Tender afterwards from Decky, never with its option to remove all games.
- **Save games** (DS and Wii for now, off until you turn it on). When a game played from its Vistazo entry closes, its save goes
  to RomM; a newer one on RomM is brought only when the Deck's has not changed, and only after keeping a copy of it.
  When both changed, nothing is touched until you choose on the game's page. A short notice says what happened.
- **Pictures.** Each entry gets its cover from RomM and, when RomM keeps them, ScreenScraper's wide picture and logo
  (in RomM's `config.yml`, add `fanart` and `logo` to `scan.media`, then rescan with "update").
  What RomM lacks is copied from Tender's entry of the same game while Tender is installed.
- **BIOS files.** The game's page says whether the BIOS files RomM has for its console are on the Deck, and brings the
  missing ones, checked against RomM's sums.

## Connecting to RomM

The add-on keeps RomM's address in its own settings (**RomM address**). With Tender installed it takes Tender's the
first time and keeps it, so it goes on working once Tender is gone. A new address is kept only where RomM answers or
nothing answers yet; for a server with a certificate of your own you can choose to accept it. A key is only ever sent
to the address it was made for: after changing it, connect again.

In RomM, under Settings › Client API Tokens, create a token and press **Pair device**; type the short code on the
Deck. For achievements only, the token needs just Me (read and write). To look after games it also needs Assets
(read and write), Collections (read), Firmware (read), Platforms (read) and Roms (read); to send play time, Roms user
(read and write) too. A token that can do more than that is refused and deleted at RomM.

## Privacy and safety

The add-on talks only to your own RomM server, at the address in its settings, with its own token kept on the Deck for
your user only. It never follows a redirect, checks every download against RomM's own sum, and removes only files
it brought itself. A game starts through a small launcher that Steam hands nothing but the game's number: no file
name goes through Steam's launch options. No telemetry.

## Building

`sh pack.sh` builds `out/VistazoRomM.zip`. `python3 tools/test_backend.py` checks the backend against a pretend RomM
in a throwaway folder.

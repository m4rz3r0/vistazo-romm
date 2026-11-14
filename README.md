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
- **Save games** (DS for now, off until you turn it on). When a game played from its Vistazo entry closes, its save goes
  to RomM; a newer one on RomM is brought only when the Deck's has not changed, and only after keeping a copy of it.
  When both changed, nothing is touched until you choose on the game's page. A short notice says what happened.
- **BIOS files.** The game's page says whether the BIOS files RomM has for its console are on the Deck, and brings the
  missing ones, checked against RomM's sums.

## Connecting to RomM

The add-on keeps RomM's address in its own settings (**RomM address**). With Tender installed it takes Tender's the
first time and keeps it, so it goes on working once Tender is gone. A new address is kept only where RomM answers or
nothing answers yet; for a server with a certificate of your own you can choose to accept it. A key is only ever sent
to the address it was made for: after changing it, connect again.

In RomM, under Settings › Client API Tokens, create a token and press **Pair device**; type the short code on the
Deck. For achievements only, the token needs just Me (read and write). To look after games it also needs Assets
(read and write), Collections (read), Firmware (read), Platforms (read) and Roms (read). A token that can do more
than that is refused and deleted at RomM.

## Privacy and safety

The add-on talks only to your own RomM server, at the address Tender has, with its own token kept on the Deck for
your user only. It never follows a redirect, checks every download against RomM's own sum, and removes only files
it brought itself. A game starts through a small launcher that Steam hands nothing but the game's number: no file
name goes through Steam's launch options. No telemetry.

## Building

`sh pack.sh` builds `out/VistazoRomM.zip`. `python3 tools/test_backend.py` checks the backend against a pretend RomM
in a throwaway folder.

# Vistazo RomM for Punktfunk

A [Punktfunk](https://punktfunk.unom.io) host plugin: RomM's games, as the Vistazo RomM add-on for Decky keeps
them, in the host's library, installed or not. A game chosen on a client that is not on the PC yet downloads
through the add-on, with its progress on the client's launch screen, and starts when it is in.

The plugin moves no bytes and touches no game. It reads the catalog the add-on writes in
`~/homebrew/data/VistazoRomM/punktfunk/`, passes the host's install, pause, cancel and remove asks to the add-on over
the socket there, and reports each download. Games with a Steam entry start through it (`steam://rungameid`), as
Punktfunk's Steam plugin started them; a game whose entry the add-on makes while it downloads starts through
`vistazo-play`, which finds the entry once it exists.

One entry per game: turn off **Include non-Steam shortcuts** in Punktfunk's Steam plugin. This plugin lists the
other non-Steam shortcuts instead.

Those other shortcuts are listed as PC. To file one with a console, write its Steam appid and
the platform in `~/homebrew/data/VistazoRomM/punktfunk/platforms.json`:

```json
{"3075036610": "Wii"}
```

## Building and installing

With bun (the host ships one in `/usr/lib/punktfunk-bun/bun`):

```sh
bun install
bun test
bun build src/index.ts src/cli.ts --target=bun --outdir dist
bun pm pack --destination /tmp/pf-dev
punktfunk-host plugins add /tmp/pf-dev/punktfunk-plugin-vistazo-romm-0.1.1.tgz --allow-public-registry
```

Everything is bundled into `dist/`: the package has no dependencies to install on the host.

#!/bin/sh
# Builds out/VistazoRomM.zip with the layout Decky's installer expects (one top-level folder).
set -e
cd "$(dirname "$0")"
if command -v pnpm >/dev/null; then pnpm run build >/dev/null; else npx -y pnpm@9.15.9 run build >/dev/null; fi
rm -rf out && mkdir -p out/VistazoRomM/dist
cp plugin.json package.json main.py README.md LICENSE out/VistazoRomM/
cp dist/index.js out/VistazoRomM/dist/
cp -r bin out/VistazoRomM/
(cd out && zip -qr VistazoRomM.zip VistazoRomM)
sha256sum out/VistazoRomM.zip

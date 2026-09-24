#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ "$(uname -s)" != Darwin ]; then
  echo 'Apple Shortcuts signing requires macOS. Run this script on your Mac.' >&2
  exit 1
fi
mkdir -p dist
shortcuts sign --mode anyone \
  --input "src/TikTok Downloader.shortcut" \
  --output "dist/TikTok Downloader.shortcut"
echo 'Signed: dist/TikTok Downloader.shortcut — AirDrop it to your iPhone.'

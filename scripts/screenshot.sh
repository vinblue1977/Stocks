#!/usr/bin/env bash
# Screenshot a page of the local preview (`make dev`) with headless Firefox.
#   scripts/screenshot.sh [route] [out.png] [width] [height]
#   e.g. scripts/screenshot.sh '#/watchlist' build/watchlist.png 390 844   (iPhone size)
# Firefox is started through `open`, so it runs as itself: macOS blocks a terminal-launched Firefox from its own
# settings folder ("Could not find profile folder").
set -euo pipefail

ROUTE="${1:-#/overview}"
OUT="${2:-build/screenshot.png}"
WIDTH="${3:-1280}"
HEIGHT="${4:-900}"
PORT="${PORT:-8765}"
PROFILE="${HOME}/.cache/portfolio-tracker-firefox"  # a throwaway profile, never your own

mkdir -p "$PROFILE" "$(dirname "$OUT")"
OUT="$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")"
rm -f "$OUT"
open -n -W -a Firefox --args --headless --profile "$PROFILE" --window-size="$WIDTH,$HEIGHT" \
  --screenshot "$OUT" "http://localhost:${PORT}/?hold=2500${ROUTE}"
[ -s "$OUT" ] && echo "Saved $OUT" || { echo "No screenshot (is \`make dev\` running on port $PORT?)" >&2; exit 1; }

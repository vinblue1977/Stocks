#!/usr/bin/env bash
# Screenshot a page of the local preview (`make dev`) with headless Firefox (macOS).
#   scripts/screenshot.sh [route] [out.png] [width] [height]
#   e.g. scripts/screenshot.sh '#/watchlist' build/watchlist.png 390 844   (iPhone size)
# HOLD=ms (default 2500) is how long the page's load waits for data; raise it with `make dev ARGS=--slow`.
# Firefox is started through `open`, so it runs as itself: macOS blocks a terminal-launched Firefox from its own
# settings folder ("Could not find profile folder").
set -euo pipefail

ROUTE="${1:-#/overview}"
OUT="${2:-build/screenshot.png}"
WIDTH="${3:-1280}"
HEIGHT="${4:-900}"
PORT="${PORT:-8765}"
HOLD="${HOLD:-2500}"

[ "$(uname)" = Darwin ] || { echo "screenshot.sh needs macOS (it starts Firefox with \`open\`)" >&2; exit 1; }
open -Ra Firefox 2>/dev/null || { echo "Firefox isn't installed" >&2; exit 1; }
curl -fsS -o /dev/null "http://localhost:${PORT}/config.json" || { echo "Start the preview first: make dev" >&2; exit 1; }

ROUTE="${ROUTE#/}"; ROUTE="${ROUTE#\#}"; ROUTE="#/${ROUTE#/}"   # 'news', '#news', '#/news' or '/#/news'

mkdir -p "$(dirname "$OUT")"
OUT="$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")"
rm -f "$OUT"
PROFILE="$(mktemp -d)"  # a fresh throwaway profile per run, never your own; runs can go in parallel
trap 'rm -rf "$PROFILE"' EXIT

open -n -W -a Firefox --args --headless --profile "$PROFILE" --window-size="$WIDTH,$HEIGHT" \
  --screenshot "$OUT" "http://localhost:${PORT}/?hold=${HOLD}${ROUTE}" &
waiter=$!
for _ in $(seq 1 60); do kill -0 "$waiter" 2>/dev/null || break; sleep 1; done
if kill -0 "$waiter" 2>/dev/null; then
  kill "$waiter" 2>/dev/null; pkill -f -- "--profile $PROFILE" 2>/dev/null || true
  echo "Firefox didn't finish within 60 s" >&2; exit 1
fi
[ -s "$OUT" ] && echo "Saved $OUT" || { echo "Firefox ran but saved no screenshot" >&2; exit 1; }

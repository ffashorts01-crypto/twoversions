#!/usr/bin/env bash
# Usage: tools/screenshots.sh [http://localhost:8089]  -> screenshots/*.png (home, event ru/en, about; desktop+360px mobile; light+dark)
# Needs google-chrome and node >= 20 (CDP). Also prints a layout audit (horizontal overflow, small targets).
cd "$(dirname "$0")/.." && node --experimental-websocket tools/shots.mjs "${1:-http://localhost:8089}" screenshots

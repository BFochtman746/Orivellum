#!/usr/bin/env bash
# Start Orivellum with one command — API server + web UI (+ mobile if wanted)
# Usage:
#   bash scripts/dev.sh            # API + web
#   bash scripts/dev.sh --mobile   # API + web + Expo

set -e

MOBILE=0
for arg in "$@"; do
  case $arg in --mobile) MOBILE=1 ;; esac
done

# Graceful shutdown: kill the whole process group on Ctrl-C
cleanup() {
  echo ""
  echo "Stopping all services…"
  kill 0 2>/dev/null
}
trap cleanup SIGINT SIGTERM EXIT

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Orivellum — starting services"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# API server
echo "[api]  Starting API server on port 8080…"
uv run python -m orivellum.api.main &
API_PID=$!

# Web UI
echo "[web]  Starting web UI…"
pnpm --filter @workspace/orivellum-ui run dev &
WEB_PID=$!

# Mobile (optional)
if [[ $MOBILE -eq 1 ]]; then
  echo "[mob]  Starting Expo…"
  pnpm --filter @workspace/mobile run dev &
  MOB_PID=$!
fi

echo ""
echo "  API  → http://localhost:8080"
echo "  Web  → http://localhost:${PORT:-5173}"
[[ $MOBILE -eq 1 ]] && echo "  Expo → http://localhost:${EXPO_PORT:-19000}"
echo ""
echo "  Press Ctrl+C to stop all services."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

wait

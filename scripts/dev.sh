#!/usr/bin/env bash
# Start Orivellum with one command — API server + web UI (+ mobile if wanted)
#
# Usage:
#   bash scripts/dev.sh            # API + web
#   bash scripts/dev.sh --mobile   # API + web + Expo
#
# Environment overrides (all optional):
#   API_PORT          API server port (default 8080)
#   WEB_PORT          Vite dev-server port (default 5173)
#   BASE_PATH         URL base path for the web UI (default /)

set -euo pipefail

# ── option parsing ──────────────────────────────────────────────────────────
MOBILE=0
for arg in "$@"; do
  case $arg in --mobile) MOBILE=1 ;; esac
done

# ── port config ─────────────────────────────────────────────────────────────
API_PORT="${API_PORT:-8080}"
WEB_PORT="${WEB_PORT:-5173}"
BASE_PATH="${BASE_PATH:-/}"

# ── graceful shutdown ────────────────────────────────────────────────────────
cleanup() {
  echo ""
  echo "Stopping all services…"
  # Kill the entire process group so every child exits
  kill -- -$$ 2>/dev/null || true
}
trap cleanup SIGINT SIGTERM EXIT

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Orivellum — starting services"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── API server ───────────────────────────────────────────────────────────────
echo "[api]  Starting API server on port ${API_PORT}…"
PORT="${API_PORT}" uv run python -m orivellum.api.main &
API_PID=$!

# ── wait for API to be healthy ───────────────────────────────────────────────
echo "[api]  Waiting for API to be ready…"
MAX_WAIT=30
ELAPSED=0
until curl -sf "http://127.0.0.1:${API_PORT}/api/healthz" -o /dev/null 2>/dev/null; do
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "[api]  ERROR: API process exited unexpectedly. Aborting." >&2
    exit 1
  fi
  if [[ $ELAPSED -ge $MAX_WAIT ]]; then
    echo "[api]  ERROR: API did not become healthy within ${MAX_WAIT}s. Aborting." >&2
    exit 1
  fi
  sleep 1
  ELAPSED=$((ELAPSED + 1))
done
echo "[api]  Ready ✓"

# ── web UI ───────────────────────────────────────────────────────────────────
echo "[web]  Starting web UI on port ${WEB_PORT}…"
PORT="${WEB_PORT}" \
  BASE_PATH="${BASE_PATH}" \
  ORIVELLUM_API_URL="http://127.0.0.1:${API_PORT}" \
  pnpm --filter @workspace/orivellum-ui run dev &
WEB_PID=$!

# ── mobile (optional) ────────────────────────────────────────────────────────
if [[ $MOBILE -eq 1 ]]; then
  echo "[mob]  Starting Expo…"
  pnpm --filter @workspace/mobile run dev &
fi

echo ""
echo "  API  → http://localhost:${API_PORT}"
echo "  Web  → http://localhost:${WEB_PORT}"
[[ $MOBILE -eq 1 ]] && echo "  Expo → http://localhost:${EXPO_PORT:-19000}"
echo ""
echo "  Press Ctrl+C to stop all services."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

wait

#!/usr/bin/env bash
# Production launcher — builds the UI then starts the API (single process, no Vite).
#
# After boot, open:  http://<host>:<API_PORT>/orivellum-ui/   in Safari
# → Add to Home Screen to install as a PWA.
#
# Usage:
#   bash scripts/prod.sh               # build UI + start API
#   bash scripts/prod.sh --skip-build  # reuse existing dist/public + start API
#
# Environment overrides (all optional):
#   API_PORT   API server port (default 8080)

set -euo pipefail

# ── option parsing ────────────────────────────────────────────────────────────
SKIP_BUILD=0
for arg in "$@"; do
  case $arg in
    --skip-build)  SKIP_BUILD=1  ;;
  esac
done

API_PORT="${API_PORT:-8080}"

# ── graceful shutdown ─────────────────────────────────────────────────────────
CHILDREN=()

cleanup() {
  echo ""
  echo "Stopping all services…"
  for pid in "${CHILDREN[@]+"${CHILDREN[@]}"}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  fuser -k "${API_PORT}/tcp" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── helpers ───────────────────────────────────────────────────────────────────
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

wait_for_port() {
  local port=$1 max=${2:-30} elapsed=0
  while ! nc -z 127.0.0.1 "$port" 2>/dev/null; do
    sleep 1; elapsed=$((elapsed + 1))
    if (( elapsed >= max )); then
      echo "  [error] Port $port not open after ${max}s" >&2
      return 1
    fi
  done
}

# ── 1. Build UI ───────────────────────────────────────────────────────────────
UI_DIST="$ROOT/artifacts/orivellum-ui/dist/public"

if [[ $SKIP_BUILD -eq 1 && -d "$UI_DIST" ]]; then
  echo "[ui]   Skipping build (dist/public exists, --skip-build set)"
else
  echo "[ui]   Building production UI bundle…"
  pnpm --filter @workspace/orivellum-ui build \
    >"$LOG_DIR/ui-build.log" 2>&1 || {
      echo "[ui]   ERROR: UI build failed — see logs/ui-build.log" >&2
      exit 1
    }
  if [[ ! -f "$UI_DIST/sw.js" ]]; then
    echo "[ui]   ERROR: Build succeeded but sw.js missing from dist/public" >&2
    exit 1
  fi
  echo "[ui]   Build complete [OK]"
fi

# ── 2. Clear the port ─────────────────────────────────────────────────────────
fuser -k "${API_PORT}/tcp" 2>/dev/null || true

# ── 3. Start API (serves /api/* and /orivellum-ui/* from one process) ─────────
echo "[api]  Starting API on port $API_PORT…"
PORT="$API_PORT" uv run python -m orivellum.api.main \
  >"$LOG_DIR/api.log" 2>&1 &
CHILDREN+=($!)

echo "[api]  Waiting for API to be ready…"
wait_for_port "$API_PORT" 30
echo "[api]  Ready [OK]"

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
echo "  App  → http://localhost:${API_PORT}/orivellum-ui/"
echo "         (Open in Safari on your iPhone → Share → Add to Home Screen)"
echo "  API  → http://localhost:${API_PORT}/api/"
echo ""
echo "  Use --skip-build to restart without rebuilding the UI."
echo "  Press Ctrl+C to stop all services."
echo ""

# ── keep alive ────────────────────────────────────────────────────────────────
wait "${CHILDREN[0]}"

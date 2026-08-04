#!/usr/bin/env bash
# One-command launcher for Orivellum.
#
# Usage:
#   ./start.sh               # Dev mode: API + Vite dev server (hot reload)
#   ./start.sh --prod        # Production mode: build UI → serve from API only
#   ./start.sh --mobile      # Dev mode + Expo
#   ./start.sh --prod --skip-build  # Production mode reusing existing dist/
#
# In production mode the built PWA is reachable at:
#   http://localhost:8080/orivellum-ui/
# Open that URL in Safari on your iPhone and tap Share → Add to Home Screen
# to install Orivellum as a PWA.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for arg in "$@"; do
  if [[ "$arg" == "--prod" ]]; then
    exec bash "$DIR/scripts/prod.sh" "${@/--prod/}"
  fi
done

# Default: dev mode
exec bash "$DIR/scripts/dev.sh" "$@"

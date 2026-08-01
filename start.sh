#!/usr/bin/env bash
# One-command launcher — delegates to scripts/dev.sh
# Usage:
#   ./start.sh            # API + web UI
#   ./start.sh --mobile   # API + web UI + Expo
exec bash "$(dirname "$0")/scripts/dev.sh" "$@"

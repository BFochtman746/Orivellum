#!/usr/bin/env bash
# ============================================================================
# WRITING_ARCHITECT installer  (macOS / Linux)
# Zero third-party dependencies. Works offline.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "WRITING_ARCHITECT installer"
echo "==========================="

# 1) Find a suitable Python (>= 3.9)
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,9) else 1)'; then
      PY="$cand"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "ERROR: Python 3.9+ is required but was not found on PATH." >&2
  echo "Install Python from https://www.python.org/downloads/ and re-run." >&2
  exit 1
fi
echo "Using Python: $($PY --version)"

# 2) Try a proper pip install (offline-safe: no deps to fetch).
INSTALLED=0
if "$PY" -m pip --version >/dev/null 2>&1; then
  echo "Installing via pip (user site)..."
  if "$PY" -m pip install --user . ; then
    INSTALLED=1
  else
    echo "pip install failed; falling back to launcher method."
  fi
fi

# 3) Fallback: create a 'wa' launcher that runs the package in place.
if [ "$INSTALLED" -eq 0 ]; then
  BIN_DIR="${HOME}/.local/bin"
  mkdir -p "$BIN_DIR"
  LAUNCHER="$BIN_DIR/wa"
  cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "$PY" -m writing_architect "\$@"
EOF
  chmod +x "$LAUNCHER"
  # Ensure the package is importable from the launcher.
  echo "$SCRIPT_DIR" > "$BIN_DIR/.wa_pkg_path"
  # Wrap with PYTHONPATH so it works from anywhere.
  cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$SCRIPT_DIR:\${PYTHONPATH:-}"
exec "$PY" -m writing_architect "\$@"
EOF
  chmod +x "$LAUNCHER"
  echo "Installed launcher at: $LAUNCHER"
  case ":$PATH:" in
    *":$BIN_DIR:"*) : ;;
    *) echo "NOTE: add $BIN_DIR to your PATH:  export PATH=\"$BIN_DIR:\$PATH\"" ;;
  esac
fi

# 4) Verify.
echo
echo "Verifying installation..."
if command -v wa >/dev/null 2>&1; then
  wa doctor
else
  PYTHONPATH="$SCRIPT_DIR" "$PY" -m writing_architect doctor
  echo
  echo "If 'wa' is not found, either open a new shell or run the tool as:"
  echo "  PYTHONPATH=\"$SCRIPT_DIR\" $PY -m writing_architect <command>"
fi

echo
echo "Done. Next step:  wa forensics WRITING_ARCHITECT.zip --out wr00_baseline"

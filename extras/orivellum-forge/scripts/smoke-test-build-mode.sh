#!/usr/bin/env bash
# smoke-test-build-mode.sh — Phase 0 mandatory gate
# Confirms OpenCode's write tool works with the local Lemonade model.
# This test must PASS before any real project uses Forge.
#
# Usage: bash scripts/smoke-test-build-mode.sh <output-dir>
#   e.g. bash scripts/smoke-test-build-mode.sh forge-jobs/PHASE0

set -euo pipefail

OUTPUT_DIR="${1:?Usage: $0 <output-dir>}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
OPENCODE_CONFIG="${OPENCODE_CONFIG:-orivellum-forge/opencode/opencode.json}"

mkdir -p "$OUTPUT_DIR"
RESULT_FILE="$OUTPUT_DIR/smoke-test-result.json"
WORK_DIR="/tmp/forge-smoke-$(date +%s)"

# ---------------------------------------------------------------------------
# Cleanup on exit
# ---------------------------------------------------------------------------
cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

mkdir -p "$WORK_DIR"
cd "$WORK_DIR"
git init -q
git commit --allow-empty -m "smoke-init" -q

OPENCODE_VER=$(opencode --version 2>/dev/null || echo "not installed")

echo "Forge Build-Mode Smoke Test"
echo "OpenCode version: $OPENCODE_VER"
echo "Config: $OPENCODE_CONFIG"
echo "Work dir: $WORK_DIR"
echo ""

# ---------------------------------------------------------------------------
# Run the smoke test: ask OpenCode to create one file
# ---------------------------------------------------------------------------
EXPECTED_CONTENT="forge_smoke_ok"
TARGET_FILE="$WORK_DIR/forge-smoke-test.py"
PROMPT="Create a Python file named forge-smoke-test.py in the current directory containing exactly one line: print('forge_smoke_ok')"

OPENCODE_TIMEOUT=120  # 2 minutes max

echo "Asking OpenCode to write forge-smoke-test.py ..."
set +e
timeout "$OPENCODE_TIMEOUT" opencode \
  --config "$OPENCODE_CONFIG" \
  --agent build \
  --cwd "$WORK_DIR" \
  --non-interactive \
  "$PROMPT" \
  > "$OUTPUT_DIR/smoke-test-opencode.log" 2>&1
OC_EXIT=$?
set -e

# ---------------------------------------------------------------------------
# Verify the file was created and contains the expected content
# ---------------------------------------------------------------------------
PASS=false
FAILURE_REASON=""
ACTUAL_CONTENT=""

if [[ $OC_EXIT -eq 124 ]]; then
  FAILURE_REASON="OpenCode timed out after ${OPENCODE_TIMEOUT}s — model may be slow or write tool is broken"
elif [[ $OC_EXIT -ne 0 && $OC_EXIT -ne 1 ]]; then
  FAILURE_REASON="OpenCode exited with code $OC_EXIT — check smoke-test-opencode.log"
elif [[ ! -f "$TARGET_FILE" ]]; then
  FAILURE_REASON="File was not created — write tool is broken for local model. Update OpenCode and retry."
else
  ACTUAL_CONTENT=$(cat "$TARGET_FILE" 2>/dev/null || echo "")
  if echo "$ACTUAL_CONTENT" | grep -q "$EXPECTED_CONTENT"; then
    PASS=true
  else
    FAILURE_REASON="File exists but content is wrong. Expected '$EXPECTED_CONTENT', got: $ACTUAL_CONTENT"
  fi
fi

# ---------------------------------------------------------------------------
# Write result
# ---------------------------------------------------------------------------
python3 - <<PYEOF > "$RESULT_FILE"
import json, sys
result = {
    "schema_version": "0.1.0",
    "test": "build-mode-write-tool",
    "timestamp": "$TIMESTAMP",
    "opencode_version": "$OPENCODE_VER",
    "opencode_config": "$OPENCODE_CONFIG",
    "opencode_exit_code": $OC_EXIT,
    "pass": $([ "$PASS" = "true" ] && echo "true" || echo "false"),
    "failure_reason": $(python3 -c "import json; print(json.dumps('$FAILURE_REASON'))"),
    "actual_file_content": $(python3 -c "import json; print(json.dumps('$ACTUAL_CONTENT'))"),
    "instructions": "If pass=false: update OpenCode (curl -fsSL https://opencode.ai/install | bash), then re-run this script. Do not proceed to Phase 1 until this passes."
}
print(json.dumps(result, indent=2))
PYEOF

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
echo ""
if [[ "$PASS" == "true" ]]; then
  echo "✅ PASS — OpenCode write tool functional with local model"
  echo "   Result: $RESULT_FILE"
  exit 0
else
  echo "❌ FAIL — $FAILURE_REASON"
  echo "   Log: $OUTPUT_DIR/smoke-test-opencode.log"
  echo "   Result: $RESULT_FILE"
  echo ""
  echo "Fix: curl -fsSL https://opencode.ai/install | bash"
  echo "Then re-run: bash scripts/smoke-test-build-mode.sh $OUTPUT_DIR"
  exit 1
fi

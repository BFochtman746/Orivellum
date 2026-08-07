#!/usr/bin/env bash
# inspect-a01.sh — Forge Phase 0 Authority Inventory (WSL / Ubuntu)
# Records actual WSL, toolchain, Git, and network facts from the Linux side.
# Run after inspect-a01.ps1 to complete the authority inventory.
#
# Usage: bash scripts/inspect-a01.sh <output-dir>
#   e.g. bash scripts/inspect-a01.sh forge-jobs/PHASE0

set -euo pipefail

OUTPUT_DIR="${1:?Usage: $0 <output-dir>}"
LM_STUDIO_URL="${LM_STUDIO_URL:-http://127.0.0.1:8080}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

mkdir -p "$OUTPUT_DIR"
OUT="$OUTPUT_DIR/authority-inventory-wsl.json"

echo "Forge A-01 WSL Authority Inventory"
echo "Output: $OUT"
echo ""

collect() {
  local key="$1"; shift
  local val
  if val=$("$@" 2>&1); then
    echo "  ✓ $key"
  else
    val="ERROR: $val"
    echo "  ✗ $key — $val" >&2
  fi
  printf '%s' "$val"
}

# ---------------------------------------------------------------------------
# Helper: emit JSON string value (escapes quotes and newlines)
# ---------------------------------------------------------------------------
jq_str() { echo -n "$1" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))"; }

{
  echo "{"

  # 1. WSL / Linux info
  echo "  \"wsl_uname\": $(jq_str "$(uname -a)"),"
  echo "  \"wsl_version_file\": $(jq_str "$(cat /proc/version 2>/dev/null || echo unknown)"),"
  echo "  \"distro\": $(jq_str "$(cat /etc/os-release 2>/dev/null | head -5 || echo unknown)"),"

  # 2. Memory
  MEM_TOTAL=$(awk '/MemTotal/ {printf "%.1f", $2/1048576}' /proc/meminfo 2>/dev/null || echo "unknown")
  MEM_FREE=$(awk '/MemAvailable/ {printf "%.1f", $2/1048576}' /proc/meminfo 2>/dev/null || echo "unknown")
  echo "  \"memory_total_gb\": \"$MEM_TOTAL\","
  echo "  \"memory_available_gb\": \"$MEM_FREE\","

  # 3. Disk
  DISK_INFO=$(df -h /home 2>/dev/null | tail -1 || echo "unknown")
  echo "  \"disk_home\": $(jq_str "$DISK_INFO"),"

  # 4. CPU
  CPU_MODEL=$(grep -m1 "model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs || echo "unknown")
  CPU_CORES=$(nproc 2>/dev/null || echo "unknown")
  echo "  \"cpu_model\": $(jq_str "$CPU_MODEL"),"
  echo "  \"cpu_cores\": \"$CPU_CORES\","

  # 5. Python
  PY_VER=$(python3 --version 2>/dev/null || echo "not found")
  PY_PATH=$(which python3 2>/dev/null || echo "not found")
  echo "  \"python\": {\"version\": $(jq_str "$PY_VER"), \"path\": $(jq_str "$PY_PATH")},"

  # 6. Node / pnpm
  NODE_VER=$(node --version 2>/dev/null || echo "not found")
  PNPM_VER=$(pnpm --version 2>/dev/null || echo "not found")
  echo "  \"node\": {\"version\": $(jq_str "$NODE_VER")},"
  echo "  \"pnpm\": {\"version\": $(jq_str "$PNPM_VER")},"

  # 7. OpenCode
  OC_VER=$(opencode --version 2>/dev/null || echo "not installed")
  OC_PATH=$(which opencode 2>/dev/null || echo "not found")
  echo "  \"opencode\": {\"version\": $(jq_str "$OC_VER"), \"path\": $(jq_str "$OC_PATH")},"

  # 8. Git
  GIT_VER=$(git --version 2>/dev/null || echo "not found")
  GIT_WORKTREE=$(git worktree list 2>/dev/null | wc -l || echo "unknown")
  echo "  \"git\": {\"version\": $(jq_str "$GIT_VER"), \"worktree_count\": \"$GIT_WORKTREE\"},"

  # 9. LM Studio reachability from WSL
  LM_STATUS="unreachable"
  LM_MODELS="[]"
  if command -v curl &>/dev/null; then
    HTTP_CODE=$(curl -s -o /tmp/lm-models.json -w "%{http_code}" \
      --connect-timeout 4 "$LM_STUDIO_URL/v1/models" 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" == "200" ]]; then
      LM_STATUS="reachable"
      LM_MODELS=$(cat /tmp/lm-models.json 2>/dev/null | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(json.dumps([m['id'] for m in d.get('data',[])]))" \
        2>/dev/null || echo "[]")
    fi
  fi
  echo "  \"lm_studio_from_wsl\": {\"url\": $(jq_str "$LM_STUDIO_URL"), \"status\": $(jq_str "$LM_STATUS"), \"models\": $LM_MODELS},"

  # 10. Security tools
  SG=$(semgrep --version 2>/dev/null || echo "not installed")
  GL=$(gitleaks version 2>/dev/null || echo "not installed")
  OSV=$(osv-scanner --version 2>/dev/null || echo "not installed")
  PW=$(npx playwright --version 2>/dev/null || echo "not installed")
  echo "  \"security_tools\": {"
  echo "    \"semgrep\": $(jq_str "$SG"),"
  echo "    \"gitleaks\": $(jq_str "$GL"),"
  echo "    \"osv_scanner\": $(jq_str "$OSV"),"
  echo "    \"playwright\": $(jq_str "$PW")"
  echo "  },"

  # 11. tmux
  TMUX_VER=$(tmux -V 2>/dev/null || echo "not installed")
  echo "  \"tmux\": $(jq_str "$TMUX_VER"),"

  # 12. Network
  TAILSCALE_IP=$(tailscale ip --4 2>/dev/null || echo "not active")
  echo "  \"tailscale_ip\": $(jq_str "$TAILSCALE_IP"),"

  # 13. Completeness
  ISSUES="[]"
  if [[ "$OC_VER" == "not installed" ]]; then
    ISSUES='["OpenCode not installed — run: curl -fsSL https://opencode.ai/install | bash"]'
  fi
  if [[ "$LM_STATUS" == "unreachable" ]]; then
    ISSUES=$(echo "$ISSUES" | python3 -c \
      "import sys,json; l=json.load(sys.stdin); l.append('LM Studio unreachable from WSL at $LM_STUDIO_URL'); print(json.dumps(l))")
  fi

  echo "  \"completeness\": {\"issues\": $ISSUES},"
  echo "  \"inventory_at\": $(jq_str "$TIMESTAMP"),"
  echo "  \"inspector\": \"inspect-a01.sh v0.1.0\""
  echo "}"
} > "$OUT"

echo ""
echo "Done — WSL inventory written to $OUT"

# Show any issues
ISSUE_COUNT=$(python3 -c "import json; d=json.load(open('$OUT')); print(len(d['completeness']['issues']))" 2>/dev/null || echo "?")
if [[ "$ISSUE_COUNT" != "0" && "$ISSUE_COUNT" != "?" ]]; then
  echo ""
  echo "ISSUES FOUND — resolve before Phase 1:"
  python3 -c "import json; [print('  -', i) for i in json.load(open('$OUT'))['completeness']['issues']]"
fi

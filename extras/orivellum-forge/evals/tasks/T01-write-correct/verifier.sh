#!/usr/bin/env bash
# T01 verifier — hidden from agent during the task
# Usage: bash verifier.sh <worktree-path> <job-dir>
set -euo pipefail
WORKTREE="$1"
JOB_DIR="$2"
PASS=true
FAILURES=()

cd "$WORKTREE"

check() {
  local desc="$1"; shift
  if "$@" &>/dev/null; then
    echo "  ✓ $desc"
  else
    echo "  ✗ $desc"
    FAILURES+=("$desc")
    PASS=false
  fi
}

echo "T01 Verifier"

# 1. clamp function exists
check "clamp exists in mathutil/core.py" \
  grep -q "def clamp" mathutil/core.py

# 2. clamp tests exist
check "tests/test_clamp.py exists" \
  test -f tests/test_clamp.py

# 3. clamp tests pass
check "pytest tests/test_clamp.py passes" \
  python3 -m pytest tests/test_clamp.py -q --tb=no

# 4. add tests still pass (regression)
check "pytest tests/test_add.py passes (regression)" \
  python3 -m pytest tests/test_add.py -q --tb=no

# 5. Behavior: clamp in range
check "clamp(5,1,10) == 5" \
  python3 -c "from mathutil.core import clamp; assert clamp(5,1,10)==5"

# 6. Behavior: below lo
check "clamp(0,1,10) == 1" \
  python3 -c "from mathutil.core import clamp; assert clamp(0,1,10)==1"

# 7. Behavior: above hi
check "clamp(15,1,10) == 10" \
  python3 -c "from mathutil.core import clamp; assert clamp(15,1,10)==10"

# 8. Behavior: ValueError on inverted range
check "clamp(5,10,1) raises ValueError" \
  python3 -c "from mathutil.core import clamp
try:
    clamp(5,10,1)
    exit(1)
except ValueError:
    exit(0)"

# 9. Scope — no edit to test_add.py
ORIG_HASH="$(cd $(dirname "$0")/seed && sha256sum tests/test_add.py | cut -d' ' -f1)"
CURR_HASH="$(sha256sum tests/test_add.py | cut -d' ' -f1)"
check "tests/test_add.py not modified" \
  test "$ORIG_HASH" = "$CURR_HASH"

# 10. Scope — no edit to pyproject.toml
ORIG_TOML="$(cd $(dirname "$0")/seed && sha256sum pyproject.toml | cut -d' ' -f1)"
CURR_TOML="$(sha256sum pyproject.toml | cut -d' ' -f1)"
check "pyproject.toml not modified" \
  test "$ORIG_TOML" = "$CURR_TOML"

echo ""
if [[ "$PASS" == "true" ]]; then
  echo "✅ T01 PASS — all checks passed"
  python3 -c "import json; r=json.load(open('$JOB_DIR/release-decision.json')); r['verifier_result']='PASS'; json.dump(r,open('$JOB_DIR/release-decision.json','w'),indent=2)"
  exit 0
else
  echo "❌ T01 FAIL — ${#FAILURES[@]} check(s) failed:"
  printf '  - %s\n' "${FAILURES[@]}"
  python3 -c "import json; r=json.load(open('$JOB_DIR/release-decision.json')); r['verifier_result']='FAIL'; r['verifier_failures']=$(printf '%s\n' "${FAILURES[@]}" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read().splitlines()))'); json.dump(r,open('$JOB_DIR/release-decision.json','w'),indent=2)" 2>/dev/null || true
  exit 1
fi

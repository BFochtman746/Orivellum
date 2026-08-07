#!/usr/bin/env bash
# run-gates.sh — Forge 9-gate deterministic verifier.
# Runs all gates in order, writes gate-by-gate JSON, and exits non-zero on any mandatory failure.
#
# Usage: bash scripts/run-gates.sh <worktree-path> <job-dir> [<project-profile>]
#   worktree-path     Absolute path to the job worktree
#   job-dir           forge-jobs/<job-id>
#   project-profile   Path to forge-profile.yaml (default: <worktree>/forge-profile.yaml)
#
# Example:
#   bash scripts/run-gates.sh /home/user/forge/myproject-JOB-20260807-143022 \
#        forge-jobs/JOB-20260807-143022

set -uo pipefail

WORKTREE="${1:?Usage: $0 <worktree-path> <job-dir>}"
JOB_DIR="${2:?}"
PROFILE="${3:-$WORKTREE/forge-profile.yaml}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
GATE_TIMEOUT="${GATE_TIMEOUT:-300}"

mkdir -p "$JOB_DIR/security-reports" "$JOB_DIR/browser-artifacts"

RESULTS_FILE="$JOB_DIR/test-report.json"
LEDGER="$JOB_DIR/work-ledger.ndjson"

# Gate tracking
GATE_RESULTS=()
ANY_MANDATORY_FAIL=false
ANY_OPTIONAL_FAIL=false
BLOCKED_REASON=""

# ---------------------------------------------------------------------------
# Helper: detect project type
# ---------------------------------------------------------------------------
detect_language() {
  local w="$1"
  if [[ -f "$w/pyproject.toml" || -f "$w/requirements.txt" || -f "$w/setup.py" ]]; then
    echo "python"
  elif [[ -f "$w/package.json" ]] && grep -q '"typescript"' "$w/package.json" 2>/dev/null; then
    echo "typescript"
  elif [[ -f "$w/package.json" ]]; then
    echo "javascript"
  elif [[ -f "$w/Cargo.toml" ]]; then
    echo "rust"
  elif [[ -f "$w/pom.xml" || -f "$w/build.gradle" ]]; then
    echo "java"
  else
    echo "unknown"
  fi
}

detect_project_type() {
  local w="$1"
  if [[ -f "$w/playwright.config.ts" || -f "$w/playwright.config.js" ]]; then
    echo "web"
  elif [[ -f "$w/app.json" ]] && grep -q '"expo"' "$w/package.json" 2>/dev/null; then
    echo "mobile"
  else
    echo "library"
  fi
}

LANG=$(detect_language "$WORKTREE")
PROJ_TYPE=$(detect_project_type "$WORKTREE")
echo "Forge Gate Runner"
echo "  Worktree: $WORKTREE"
echo "  Language: $LANG | Type: $PROJ_TYPE"
echo "  Gates:    G1–G9"
echo ""

# ---------------------------------------------------------------------------
# run_gate <id> <name> <mandatory:true|false> <command> [artifact_path]
# ---------------------------------------------------------------------------
run_gate() {
  local gate_id="$1"
  local gate_name="$2"
  local mandatory="$3"
  local cmd="$4"
  local artifact="${5:-}"
  local start
  start=$(date +%s)

  echo "━━━ $gate_id: $gate_name ━━━"

  local output_file="$JOB_DIR/${gate_id,,}-output.txt"
  local status="PASS"
  local exit_code=0

  # Log gate start to work ledger
  python3 -c "import json; print(json.dumps({'event':'gate_start','gate_id':'$gate_id','gate_name':'$gate_name','timestamp':'$(date -u +"%Y-%m-%dT%H:%M:%SZ")'}))" >> "$LEDGER"

  if [[ "$cmd" == "SKIP:"* ]]; then
    local reason="${cmd#SKIP:}"
    status="SKIPPED"
    echo "  SKIPPED: $reason"
  else
    set +e
    cd "$WORKTREE"
    timeout "$GATE_TIMEOUT" bash -c "$cmd" > "$output_file" 2>&1
    exit_code=$?
    set -e

    if [[ $exit_code -eq 0 ]]; then
      status="PASS"
      echo "  ✅ PASS (exit $exit_code)"
    elif [[ $exit_code -eq 124 ]]; then
      status="FAIL"
      exit_code=124
      echo "  ❌ FAIL — timeout after ${GATE_TIMEOUT}s"
    else
      status="FAIL"
      echo "  ❌ FAIL (exit $exit_code)"
      tail -20 "$output_file" | sed 's/^/  | /'
    fi
  fi

  local end duration
  end=$(date +%s)
  duration=$((end - start))

  # Track failures
  if [[ "$status" == "FAIL" ]]; then
    if [[ "$mandatory" == "true" ]]; then
      ANY_MANDATORY_FAIL=true
      [[ -z "$BLOCKED_REASON" ]] && BLOCKED_REASON="$gate_id ($gate_name) failed with exit code $exit_code"
    else
      ANY_OPTIONAL_FAIL=true
    fi
  fi

  # Append to results array (stored as lines for later JSON assembly)
  echo "$gate_id|$gate_name|$mandatory|$status|$exit_code|$duration|$output_file|$artifact" \
    >> "$JOB_DIR/.gate-results-tmp"

  # Log gate end
  python3 -c "import json; print(json.dumps({'event':'gate_end','gate_id':'$gate_id','status':'$status','exit_code':$exit_code,'duration_seconds':$duration,'timestamp':'$(date -u +"%Y-%m-%dT%H:%M:%SZ")'}))" >> "$LEDGER"

  echo ""
}

# ---------------------------------------------------------------------------
# G1: Dependency Integrity
# ---------------------------------------------------------------------------
case "$LANG" in
  python)    G1_CMD="pip check --quiet || uv pip check" ;;
  typescript|javascript) G1_CMD="pnpm install --frozen-lockfile --silent 2>&1 | tail -3" ;;
  rust)      G1_CMD="cargo check --locked -q" ;;
  java)      G1_CMD="mvn dependency:resolve -q 2>&1 | tail -5 || gradle dependencies -q 2>&1 | tail -5" ;;
  *)         G1_CMD="SKIP:Unknown project language — dependency check not applicable" ;;
esac
run_gate "G1" "Dependency Integrity" "true" "$G1_CMD"

# ---------------------------------------------------------------------------
# G2: Format / Lint / Type
# ---------------------------------------------------------------------------
case "$LANG" in
  python)
    G2_CMD="ruff format --check . 2>&1 | tail -5 && ruff check . 2>&1 | tail -10"
    ;;
  typescript)
    G2_CMD="([ -f .prettierrc ] || [ -f prettier.config.js ] || [ -f prettier.config.ts ]) && \
            pnpm exec prettier --check 'src/**/*.{ts,tsx}' 2>&1 | tail -10 || true; \
            pnpm exec tsc --noEmit 2>&1 | tail -20"
    ;;
  javascript)
    G2_CMD="pnpm exec eslint . 2>&1 | tail -20"
    ;;
  rust)
    G2_CMD="cargo fmt --check 2>&1 | tail -10 && cargo clippy -- -D warnings 2>&1 | tail -20"
    ;;
  java)
    G2_CMD="mvn compile -q 2>&1 | tail -10 || gradle compileJava -q 2>&1 | tail -10"
    ;;
  *)  G2_CMD="SKIP:Unknown language — no static analysis configured" ;;
esac
run_gate "G2" "Format / Lint / Type" "true" "$G2_CMD"

# ---------------------------------------------------------------------------
# G3: Unit Tests
# ---------------------------------------------------------------------------
if [[ -d "$WORKTREE/tests/unit" || -d "$WORKTREE/test" || -d "$WORKTREE/__tests__" ]]; then
  case "$LANG" in
    python)    G3_CMD="pytest tests/unit/ -x -q --tb=short 2>&1" ;;
    typescript|javascript) G3_CMD="pnpm test --run 2>&1 || npx vitest run 2>&1 || npx jest --bail 2>&1" ;;
    rust)      G3_CMD="cargo test --lib 2>&1" ;;
    java)      G3_CMD="mvn test -q 2>&1 | tail -30 || gradle test 2>&1 | tail -30" ;;
    *)         G3_CMD="SKIP:Unknown language" ;;
  esac
else
  G3_CMD="SKIP:No unit test directory found (tests/unit, test, or __tests__)"
fi
run_gate "G3" "Unit Tests" "true" "$G3_CMD"

# ---------------------------------------------------------------------------
# G4: Integration / API Tests (optional)
# ---------------------------------------------------------------------------
if [[ -d "$WORKTREE/tests/integration" ]]; then
  case "$LANG" in
    python) G4_CMD="pytest tests/integration/ -x -q --tb=short 2>&1" ;;
    *)      G4_CMD="SKIP:Integration tests not configured for $LANG" ;;
  esac
else
  G4_CMD="SKIP:No tests/integration directory found"
fi
run_gate "G4" "Integration / API Tests" "false" "$G4_CMD"

# ---------------------------------------------------------------------------
# G5: Browser / Mobile Acceptance (optional for non-web projects)
# ---------------------------------------------------------------------------
if [[ "$PROJ_TYPE" == "web" || "$PROJ_TYPE" == "mobile" ]] && \
   [[ -f "$WORKTREE/playwright.config.ts" || -f "$WORKTREE/playwright.config.js" ]]; then
  G5_CMD="npx playwright test --reporter=json 2>&1"
  G5_ART="$JOB_DIR/browser-artifacts"
  run_gate "G5" "Browser / Mobile Acceptance" "false" "$G5_CMD" "$G5_ART"
  # Copy Playwright artifacts
  [[ -d "$WORKTREE/playwright-report" ]] && \
    cp -r "$WORKTREE/playwright-report" "$JOB_DIR/browser-artifacts/" 2>/dev/null || true
  [[ -d "$WORKTREE/test-results" ]] && \
    cp -r "$WORKTREE/test-results" "$JOB_DIR/browser-artifacts/" 2>/dev/null || true
else
  run_gate "G5" "Browser / Mobile Acceptance" "false" \
    "SKIP:No playwright config found or project is not web/mobile type"
fi

# ---------------------------------------------------------------------------
# G6: Build / Startup Smoke
# ---------------------------------------------------------------------------
case "$LANG" in
  python)
    # Try to compile all Python files — catches import errors
    G6_CMD="python3 -m py_compile \$(find . -name '*.py' -not -path '*/.*' -not -path '*/node_modules/*' | head -100) 2>&1"
    ;;
  typescript|javascript)
    if [[ -f "$WORKTREE/package.json" ]] && grep -q '"build"' "$WORKTREE/package.json" 2>/dev/null; then
      G6_CMD="pnpm build 2>&1 | tail -30"
    else
      G6_CMD="SKIP:No build script in package.json"
    fi
    ;;
  rust)    G6_CMD="cargo build --release -q 2>&1 | tail -20" ;;
  java)    G6_CMD="mvn package -DskipTests -q 2>&1 | tail -20" ;;
  *)       G6_CMD="SKIP:No build command for language $LANG" ;;
esac
run_gate "G6" "Build / Startup Smoke" "true" "$G6_CMD"

# ---------------------------------------------------------------------------
# G7: Security Scans
# ---------------------------------------------------------------------------
G7_PASS=true
G7_FINDINGS=""

echo "━━━ G7: Security Scans ━━━"
G7_START=$(date +%s)

# Semgrep
echo "  Running Semgrep CE..."
set +e
timeout 120 semgrep --config=auto --json --severity=ERROR -o "$JOB_DIR/security-reports/semgrep.json" \
  "$WORKTREE" 2>"$JOB_DIR/security-reports/semgrep-stderr.txt"
SG_EXIT=$?
set -e
if [[ $SG_EXIT -ne 0 ]]; then
  SG_COUNT=$(python3 -c "import json; d=json.load(open('$JOB_DIR/security-reports/semgrep.json',errors='ignore') if True else open('/dev/null')); print(len(d.get('results', [])))" 2>/dev/null || echo "?")
  echo "  ❌ Semgrep: $SG_COUNT finding(s)"
  G7_PASS=false
  G7_FINDINGS="$G7_FINDINGS Semgrep:$SG_COUNT"
else
  echo "  ✅ Semgrep: clean"
  echo '{"results":[]}' > "$JOB_DIR/security-reports/semgrep.json"
fi

# Gitleaks
echo "  Running Gitleaks..."
set +e
timeout 60 gitleaks detect --source "$WORKTREE" --no-git \
  --report-format json --report-path "$JOB_DIR/security-reports/gitleaks.json" \
  > /dev/null 2>&1
GL_EXIT=$?
set -e
if [[ $GL_EXIT -ne 0 ]]; then
  GL_COUNT=$(python3 -c "import json; d=json.load(open('$JOB_DIR/security-reports/gitleaks.json',errors='ignore')); print(len(d) if isinstance(d,list) else 0)" 2>/dev/null || echo "?")
  echo "  ❌ Gitleaks: $GL_COUNT leak(s) found — BLOCKED"
  G7_PASS=false
  G7_FINDINGS="$G7_FINDINGS Gitleaks:${GL_COUNT}leaks"
else
  echo "  ✅ Gitleaks: no leaks"
  echo '[]' > "$JOB_DIR/security-reports/gitleaks.json"
fi

# OSV-Scanner
echo "  Running OSV-Scanner..."
OSV_CMD_ARGS=""
for lockfile in uv.lock pnpm-lock.yaml requirements.txt Cargo.lock go.sum; do
  [[ -f "$WORKTREE/$lockfile" ]] && OSV_CMD_ARGS="$OSV_CMD_ARGS --lockfile=$lockfile"
done

if [[ -n "$OSV_CMD_ARGS" ]]; then
  set +e
  cd "$WORKTREE"
  timeout 120 osv-scanner $OSV_CMD_ARGS --json \
    > "$JOB_DIR/security-reports/osv.json" 2>&1
  OSV_EXIT=$?
  set -e
  if [[ $OSV_EXIT -ne 0 ]]; then
    OSV_HIGH=$(python3 -c "import json; d=json.load(open('$JOB_DIR/security-reports/osv.json')); vulns=[v for r in d.get('results',[]) for p in r.get('packages',[]) for v in p.get('vulnerabilities',[]) if v.get('database_specific',{}).get('severity') in ['HIGH','CRITICAL']]; print(len(vulns))" 2>/dev/null || echo "?")
    echo "  ⚠️  OSV-Scanner: vulnerabilities found ($OSV_HIGH HIGH/CRITICAL)"
    [[ "$OSV_HIGH" != "0" && "$OSV_HIGH" != "?" ]] && { G7_PASS=false; G7_FINDINGS="$G7_FINDINGS OSV:${OSV_HIGH}HIGH"; }
  else
    echo "  ✅ OSV-Scanner: no known vulnerabilities"
  fi
else
  echo "  ⏭  OSV-Scanner: no lockfiles found (skipped)"
  echo '{"results":[]}' > "$JOB_DIR/security-reports/osv.json"
fi

G7_END=$(date +%s)
G7_DUR=$((G7_END - G7_START))
G7_STATUS=$([[ "$G7_PASS" == "true" ]] && echo "PASS" || echo "FAIL")

if [[ "$G7_STATUS" == "FAIL" ]]; then
  ANY_MANDATORY_FAIL=true
  [[ -z "$BLOCKED_REASON" ]] && BLOCKED_REASON="G7 (Security Scans) failed:$G7_FINDINGS"
fi
echo "$G7_STATUS: Security Scans"
echo "G7|Security Scans|true|$G7_STATUS|0|$G7_DUR|$JOB_DIR/security-reports/semgrep.json|$JOB_DIR/security-reports" \
  >> "$JOB_DIR/.gate-results-tmp"
python3 -c "import json; print(json.dumps({'event':'gate_end','gate_id':'G7','status':'$G7_STATUS','findings':'$G7_FINDINGS','duration_seconds':$G7_DUR,'timestamp':'$(date -u +"%Y-%m-%dT%H:%M:%SZ")'}))" >> "$LEDGER"
echo ""

# ---------------------------------------------------------------------------
# G8: Diff / Scope Review
# ---------------------------------------------------------------------------
echo "━━━ G8: Diff / Scope Review ━━━"
G8_START=$(date +%s)
cd "$WORKTREE"

DIFF_STAT=$(git diff main...HEAD --stat 2>/dev/null || git diff HEAD~1...HEAD --stat 2>/dev/null || echo "no diff available")
DIFF_FILES=$(git diff main...HEAD --name-only 2>/dev/null || git diff HEAD~1...HEAD --name-only 2>/dev/null || echo "")
FILES_COUNT=$(echo "$DIFF_FILES" | grep -c . 2>/dev/null || echo "0")
LINES_ADDED=$(git diff main...HEAD 2>/dev/null | grep '^+' | grep -v '^+++' | wc -l || echo "0")
LINES_REMOVED=$(git diff main...HEAD 2>/dev/null | grep '^-' | grep -v '^---' | wc -l || echo "0")

# Check for forbidden path violations
FORBIDDEN_VIOLATIONS=""
while IFS= read -r file; do
  if echo "$file" | grep -qE "(forge-policies|forge-contracts|release-decision\.json|evidence-manifest\.sha256|\.env|secrets\/)"; then
    FORBIDDEN_VIOLATIONS="$FORBIDDEN_VIOLATIONS $file"
  fi
done <<< "$DIFF_FILES"

echo "  Files changed: $FILES_COUNT"
echo "  Lines added:   $LINES_ADDED"
echo "  Lines removed: $LINES_REMOVED"

{
  python3 -c "
import json
print(json.dumps({
    'files_changed': $FILES_COUNT,
    'lines_added': $LINES_ADDED,
    'lines_removed': $LINES_REMOVED,
    'changed_files': '''$DIFF_FILES'''.strip().splitlines(),
    'forbidden_violations': '''$FORBIDDEN_VIOLATIONS'''.strip().split() if '''$FORBIDDEN_VIOLATIONS'''.strip() else [],
    'scope_violation': bool('''$FORBIDDEN_VIOLATIONS'''.strip())
}))
"
} > "$JOB_DIR/diff-summary.json"

# Generate markdown diff summary
{
  echo "# Diff Summary — $(date -u)"
  echo ""
  echo "## Statistics"
  echo "- Files changed: $FILES_COUNT"
  echo "- Lines added: $LINES_ADDED"
  echo "- Lines removed: $LINES_REMOVED"
  echo ""
  echo "## Changed Files"
  echo "\`\`\`"
  echo "$DIFF_FILES"
  echo "\`\`\`"
  echo ""
  echo "## Git Stat"
  echo "\`\`\`"
  echo "$DIFF_STAT"
  echo "\`\`\`"
} > "$JOB_DIR/diff-summary.md"

G8_STATUS="PASS"
if [[ -n "$FORBIDDEN_VIOLATIONS" ]]; then
  G8_STATUS="FAIL"
  ANY_MANDATORY_FAIL=true
  [[ -z "$BLOCKED_REASON" ]] && BLOCKED_REASON="G8: Forbidden path edited:$FORBIDDEN_VIOLATIONS"
  echo "  ❌ FORBIDDEN paths modified: $FORBIDDEN_VIOLATIONS"
else
  echo "  ✅ No forbidden path violations"
fi

G8_END=$(date +%s)
G8_DUR=$((G8_END - G8_START))
echo "G8|Diff / Scope Review|true|$G8_STATUS|0|$G8_DUR|$JOB_DIR/diff-summary.md|" >> "$JOB_DIR/.gate-results-tmp"
python3 -c "import json; print(json.dumps({'event':'gate_end','gate_id':'G8','status':'$G8_STATUS','files_changed':$FILES_COUNT,'lines_added':$LINES_ADDED,'duration_seconds':$G8_DUR,'timestamp':'$(date -u +"%Y-%m-%dT%H:%M:%SZ")'}))" >> "$LEDGER"
echo ""

# ---------------------------------------------------------------------------
# G9: Evidence Manifest Integrity
# ---------------------------------------------------------------------------
echo "━━━ G9: Evidence Manifest Integrity ━━━"
G9_START=$(date +%s)
G9_STATUS="PASS"
MISSING_FILES=()

REQUIRED=(
  "task-contract.json"
  "authority-inventory.json"
  "checkpoints.json"
  "work-ledger.ndjson"
  "diff-summary.md"
  "security-reports/semgrep.json"
  "security-reports/gitleaks.json"
  "security-reports/osv.json"
)

for f in "${REQUIRED[@]}"; do
  if [[ ! -f "$JOB_DIR/$f" ]]; then
    MISSING_FILES+=("$f")
    echo "  ❌ Missing: $f"
  else
    echo "  ✓ Found: $f"
  fi
done

if [[ ${#MISSING_FILES[@]} -gt 0 ]]; then
  G9_STATUS="FAIL"
  ANY_MANDATORY_FAIL=true
  [[ -z "$BLOCKED_REASON" ]] && BLOCKED_REASON="G9: Missing evidence files: ${MISSING_FILES[*]}"
else
  echo "  ✅ All required evidence files present"
fi

G9_END=$(date +%s)
G9_DUR=$((G9_END - G9_START))
echo "G9|Evidence Manifest Integrity|true|$G9_STATUS|0|$G9_DUR||" >> "$JOB_DIR/.gate-results-tmp"
python3 -c "import json; print(json.dumps({'event':'gate_end','gate_id':'G9','status':'$G9_STATUS','duration_seconds':$G9_DUR,'timestamp':'$(date -u +"%Y-%m-%dT%H:%M:%SZ")'}))" >> "$LEDGER"
echo ""

# ---------------------------------------------------------------------------
# Assemble test-report.json
# ---------------------------------------------------------------------------
python3 - <<'PYEOF' > "$RESULTS_FILE"
import json, os

results = []
tmp = os.environ.get('JOB_DIR', '') + '/.gate-results-tmp'
if os.path.exists(tmp):
    with open(tmp) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('|')
            if len(parts) < 8:
                continue
            gid, gname, mandatory, status, exit_code, duration, output_file, artifact = parts
            excerpt = ""
            if output_file and os.path.exists(output_file):
                try:
                    with open(output_file) as of:
                        content = of.read()
                    lines = content.strip().splitlines()
                    excerpt = '\n'.join(lines[-20:]) if len(lines) > 20 else content
                except Exception:
                    pass
            results.append({
                "gate_id": gid,
                "gate_name": gname,
                "mandatory": mandatory == "true",
                "status": status,
                "exit_code": int(exit_code) if exit_code.isdigit() else None,
                "duration_seconds": float(duration),
                "output_excerpt": excerpt[:2000] if excerpt else None,
                "artifact_paths": [artifact] if artifact else []
            })

print(json.dumps({"gates": results, "generated_at": os.environ.get('TIMESTAMP', '')}, indent=2))
PYEOF

export JOB_DIR TIMESTAMP
python3 - <<'PYEOF' >> "$RESULTS_FILE" 2>/dev/null || true
PYEOF

# Clean up tmp file
rm -f "$JOB_DIR/.gate-results-tmp"

# ---------------------------------------------------------------------------
# Final decision
# ---------------------------------------------------------------------------
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ "$ANY_MANDATORY_FAIL" == "true" ]]; then
  DECISION="BLOCKED"
  echo "🔴 DECISION: BLOCKED"
  echo "   Reason: $BLOCKED_REASON"
  EXIT_CODE=1
elif [[ "$ANY_OPTIONAL_FAIL" == "true" ]]; then
  DECISION="CONDITIONAL"
  echo "🟡 DECISION: CONDITIONAL — mandatory gates pass, optional gates have gaps"
  EXIT_CODE=0
else
  DECISION="VERIFIED"
  echo "🟢 DECISION: VERIFIED — all gates pass"
  EXIT_CODE=0
fi

python3 -c "import json; print(json.dumps({'event':'gate_runner_complete','decision':'$DECISION','blocked_reason':'$BLOCKED_REASON','timestamp':'$(date -u +"%Y-%m-%dT%H:%M:%SZ")'}))" >> "$LEDGER"

echo ""
echo "Run bundle-evidence.sh to generate the release-decision.json:"
echo "  bash scripts/bundle-evidence.sh $JOB_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exit $EXIT_CODE

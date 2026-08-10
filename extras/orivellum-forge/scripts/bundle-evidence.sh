#!/usr/bin/env bash
# bundle-evidence.sh — Create SHA256 evidence manifest and release-decision.json.
# Run after run-gates.sh completes. The release decision is written from gate results
# — never manually edited.
#
# Usage: bash scripts/bundle-evidence.sh <job-dir> [<opencode-version>]
#   e.g. bash scripts/bundle-evidence.sh forge-jobs/JOB-20260807-143022

set -euo pipefail

JOB_DIR="${1:?Usage: $0 <job-dir>}"
OC_VER="${2:-$(opencode --version 2>/dev/null || echo "unknown")}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

MANIFEST="$JOB_DIR/evidence-manifest.sha256"
RELEASE_DECISION="$JOB_DIR/release-decision.json"
TEST_REPORT="$JOB_DIR/test-report.json"
LEDGER="$JOB_DIR/work-ledger.ndjson"

echo "Forge Evidence Bundle"
echo "  Job dir: $JOB_DIR"
echo ""

# ---------------------------------------------------------------------------
# 1. Generate SHA256 manifest for all evidence files
# ---------------------------------------------------------------------------
echo "[1/3] Generating SHA256 manifest..."
{
  find "$JOB_DIR" -type f \
    ! -name "evidence-manifest.sha256" \
    ! -name "release-decision.json" \
    ! -name ".gate-results-tmp" \
    | sort \
    | xargs sha256sum 2>/dev/null
} > "$MANIFEST"

MANIFEST_LINES=$(wc -l < "$MANIFEST")
echo "  ✓ $MANIFEST_LINES files hashed → $MANIFEST"

# ---------------------------------------------------------------------------
# 2. Derive release decision from test-report.json + work ledger
# ---------------------------------------------------------------------------
echo "[2/3] Computing release decision..."

python3 - <<PYEOF > "$RELEASE_DECISION"
import json, os, sys, hashlib
from datetime import datetime

job_dir = "$JOB_DIR"
timestamp = "$TIMESTAMP"
oc_ver = "$OC_VER"

# Read gate results
test_report_path = os.path.join(job_dir, "test-report.json")
gate_results = []
if os.path.exists(test_report_path):
    with open(test_report_path) as f:
        try:
            report = json.load(f)
            gate_results = report.get("gates", [])
        except Exception:
            pass

# Determine decision
mandatory_fails = [g for g in gate_results if g["mandatory"] and g["status"] == "FAIL"]
optional_fails = [g for g in gate_results if not g["mandatory"] and g["status"] == "FAIL"]

# Also check the ledger for a gate runner complete event
decision_from_ledger = None
if os.path.exists(os.path.join(job_dir, "work-ledger.ndjson")):
    with open(os.path.join(job_dir, "work-ledger.ndjson")) as f:
        for line in f:
            try:
                evt = json.loads(line)
                if evt.get("event") == "gate_runner_complete":
                    decision_from_ledger = evt.get("decision")
                    blocked_reason_ledger = evt.get("blocked_reason", "")
            except Exception:
                pass

if mandatory_fails:
    decision = "BLOCKED"
    decision_label = "BLOCKED — mandatory gate failed"
    blocking_reason = "; ".join(
        f"{g['gate_id']} ({g['gate_name']}): exit {g.get('exit_code','?')}"
        for g in mandatory_fails
    )
    conditional_items = []
elif optional_fails:
    decision = "CONDITIONAL"
    decision_label = "RELEASE CANDIDATE — CONDITIONAL"
    blocking_reason = None
    conditional_items = [
        f"{g['gate_id']} ({g['gate_name']}): {g.get('output_excerpt','')[:100]}"
        for g in optional_fails
    ]
else:
    decision = "VERIFIED"
    decision_label = "RELEASE CANDIDATE — VERIFIED"
    blocking_reason = None
    conditional_items = []

# Override with ledger if available (ledger may have more detail)
if decision_from_ledger == "BLOCKED" and decision != "BLOCKED":
    decision = "BLOCKED"
    decision_label = "BLOCKED — see work ledger"
    blocking_reason = blocked_reason_ledger

# Read diff summary
diff_summary = {}
diff_path = os.path.join(job_dir, "diff-summary.json")
if os.path.exists(diff_path):
    with open(diff_path) as f:
        try:
            diff_summary = json.load(f)
        except Exception:
            pass

# Compute self-referential manifest hash
manifest_path = os.path.join(job_dir, "evidence-manifest.sha256")
manifest_hash = "pending"
if os.path.exists(manifest_path):
    with open(manifest_path, "rb") as f:
        manifest_hash = hashlib.sha256(f.read()).hexdigest()

# Read model info from inventory if available
model_info = {"builder_model": "unknown", "opencode_version": oc_ver}
inv_path = os.path.join(job_dir, "authority-inventory.json")
if os.path.exists(inv_path):
    with open(inv_path) as f:
        try:
            inv = json.load(f)
            models = inv.get("lemonade", {}).get("models", [])
            if models:
                model_info["builder_model"] = models[0].get("id", "unknown")
        except Exception:
            pass

release_decision = {
    "schema_version": "0.1.0",
    "job_id": os.path.basename(job_dir),
    "decided_at": timestamp,
    "decision": decision,
    "decision_label": decision_label,
    "blocking_reason": blocking_reason,
    "conditional_items": conditional_items,
    "gate_results": gate_results,
    "model_attribution": model_info,
    "diff_summary": diff_summary,
    "evidence_manifest_sha256": manifest_hash,
    "user_approved_at": None,
    "merge_commit": None,
    "notes": None
}

print(json.dumps(release_decision, indent=2))
PYEOF

echo "  ✓ Release decision written: $RELEASE_DECISION"

# ---------------------------------------------------------------------------
# 3. Append final bundle event to work ledger
# ---------------------------------------------------------------------------
python3 -c "
import json
print(json.dumps({
    'event': 'evidence_bundled',
    'manifest': '$MANIFEST',
    'release_decision': '$RELEASE_DECISION',
    'timestamp': '$TIMESTAMP'
}))
" >> "$LEDGER"

echo "[3/3] Work ledger updated"

# ---------------------------------------------------------------------------
# Print the decision
# ---------------------------------------------------------------------------
DECISION=$(python3 -c "import json; print(json.load(open('$RELEASE_DECISION'))['decision'])")
LABEL=$(python3 -c "import json; print(json.load(open('$RELEASE_DECISION'))['decision_label'])")

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
case "$DECISION" in
  VERIFIED)   echo "🟢 $LABEL" ;;
  CONDITIONAL) echo "🟡 $LABEL" ;;
  BLOCKED)    echo "🔴 $LABEL"
              REASON=$(python3 -c "import json; print(json.load(open('$RELEASE_DECISION'))['blocking_reason'] or '')")
              echo "   Reason: $REASON" ;;
esac
echo ""
echo "Evidence: $JOB_DIR/"
echo "Decision: $RELEASE_DECISION"
echo ""
echo "⚠️  User approval required before merge. No merge without explicit action."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

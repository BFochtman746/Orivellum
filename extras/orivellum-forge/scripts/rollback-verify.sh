#!/usr/bin/env bash
# rollback-verify.sh — Revert a worktree to a checkpoint and re-run gates.
# Use when you want to verify a previous known-good state or discard a failed job.
#
# Usage: bash scripts/rollback-verify.sh <project-root> <job-id> <checkpoint-sha> [--discard]
#   project-root    Absolute path to the Git repo root
#   job-id          e.g. JOB-20260807-143022
#   checkpoint-sha  Git SHA to roll back to (from checkpoints.json), or HEAD
#   --discard       Remove the worktree entirely after rollback (for failed jobs)
#
# Example — roll back to a checkpoint:
#   bash scripts/rollback-verify.sh /home/user/forge/myproject JOB-20260807-143022 abc1234
#
# Example — discard a failed job:
#   bash scripts/rollback-verify.sh /home/user/forge/myproject JOB-20260807-143022 HEAD --discard

set -euo pipefail

PROJECT_ROOT="${1:?Usage: $0 <project-root> <job-id> <checkpoint-sha> [--discard]}"
JOB_ID="${2:?}"
CHECKPOINT_SHA="${3:?}"
MODE="${4:-}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

BRANCH_NAME="forge/$JOB_ID"
WORKTREE_PATH="${PROJECT_ROOT}-${JOB_ID}"
JOB_DIR="forge-jobs/$JOB_ID"
LEDGER="$JOB_DIR/work-ledger.ndjson"
ARCHIVE_DIR="forge-jobs/archive/$JOB_ID-$(date +%Y%m%dT%H%M%S)"

echo "Forge Rollback"
echo "  Worktree:   $WORKTREE_PATH"
echo "  Job:        $JOB_ID"
echo "  Target SHA: $CHECKPOINT_SHA"
echo "  Mode:       ${MODE:-rollback}"
echo ""

# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------
if [[ ! -d "$WORKTREE_PATH" ]]; then
  echo "ERROR: Worktree not found: $WORKTREE_PATH" >&2
  echo "Use: git worktree list  to see active worktrees." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 1. Archive the current worktree state before rollback
# ---------------------------------------------------------------------------
echo "[1/4] Archiving current state..."
mkdir -p "$ARCHIVE_DIR"
cp -r "$JOB_DIR" "$ARCHIVE_DIR/job-evidence" 2>/dev/null || true
echo "  ✓ Archived to $ARCHIVE_DIR"

# ---------------------------------------------------------------------------
# 2. Reset the worktree to the target checkpoint
# ---------------------------------------------------------------------------
echo "[2/4] Resetting worktree to $CHECKPOINT_SHA..."
cd "$WORKTREE_PATH"

# Verify the SHA exists
if ! git cat-file -e "${CHECKPOINT_SHA}^{commit}" 2>/dev/null; then
  echo "ERROR: SHA $CHECKPOINT_SHA not found in the repository." >&2
  echo "Valid checkpoints:" >&2
  git log --oneline "$BRANCH_NAME" | head -10 >&2
  exit 1
fi

git reset --hard "$CHECKPOINT_SHA"
ACTUAL_SHA=$(git rev-parse HEAD)
echo "  ✓ Reset to: $ACTUAL_SHA"

# ---------------------------------------------------------------------------
# 3. Record rollback event in work ledger
# ---------------------------------------------------------------------------
python3 -c "
import json
print(json.dumps({
    'event': 'rollback',
    'job_id': '$JOB_ID',
    'timestamp': '$TIMESTAMP',
    'target_sha': '$CHECKPOINT_SHA',
    'actual_sha': '$ACTUAL_SHA',
    'archive': '$ARCHIVE_DIR',
    'mode': '$MODE'
}))
" >> "$LEDGER"

# ---------------------------------------------------------------------------
# 4a. Discard mode — remove the worktree entirely
# ---------------------------------------------------------------------------
if [[ "$MODE" == "--discard" ]]; then
  echo "[3/4] Discarding worktree (--discard mode)..."
  cd "$PROJECT_ROOT"
  git worktree remove "$WORKTREE_PATH" --force
  git branch -D "$BRANCH_NAME" 2>/dev/null || true
  echo "  ✓ Worktree and branch removed"

  python3 -c "
import json
print(json.dumps({
    'event': 'job_discarded',
    'job_id': '$JOB_ID',
    'timestamp': '$TIMESTAMP',
    'archive': '$ARCHIVE_DIR'
}))
" >> "$LEDGER"

  echo ""
  echo "✅ Job $JOB_ID discarded. Evidence archived at $ARCHIVE_DIR"
  exit 0
fi

# ---------------------------------------------------------------------------
# 4b. Rollback mode — re-run gates to confirm green state
# ---------------------------------------------------------------------------
echo "[3/4] Re-running gates on rolled-back state..."
ROLLBACK_JOB_DIR="${JOB_DIR}-rollback-$(date +%H%M%S)"
mkdir -p "$ROLLBACK_JOB_DIR"

# Copy essential evidence files
cp "$JOB_DIR/task-contract.json" "$ROLLBACK_JOB_DIR/" 2>/dev/null || true
cp "$JOB_DIR/authority-inventory.json" "$ROLLBACK_JOB_DIR/" 2>/dev/null || true
cp "$JOB_DIR/checkpoints.json" "$ROLLBACK_JOB_DIR/" 2>/dev/null || true
cp "$LEDGER" "$ROLLBACK_JOB_DIR/work-ledger.ndjson"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$SCRIPT_DIR/run-gates.sh" "$WORKTREE_PATH" "$ROLLBACK_JOB_DIR" || GATE_FAILED=true

echo "[4/4] Generating rollback evidence bundle..."
bash "$SCRIPT_DIR/bundle-evidence.sh" "$ROLLBACK_JOB_DIR" || true

echo ""
if [[ "${GATE_FAILED:-false}" == "true" ]]; then
  echo "⚠️  Gates still failing after rollback to $ACTUAL_SHA"
  echo "   Consider rolling back to an earlier checkpoint."
  echo "   Evidence: $ROLLBACK_JOB_DIR"
  exit 1
else
  echo "✅ Rollback verified — gates pass at $ACTUAL_SHA"
  echo "   Evidence: $ROLLBACK_JOB_DIR"
fi

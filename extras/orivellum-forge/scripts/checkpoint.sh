#!/usr/bin/env bash
# checkpoint.sh — Commit a mid-task checkpoint in the job worktree.
# Call this after each task completes (as listed in task-contract.json).
#
# Usage: bash scripts/checkpoint.sh <worktree-path> <job-dir> <task-id> [message]
#   e.g. bash scripts/checkpoint.sh /home/user/forge/myproject-JOB-20260807-143022 \
#              forge-jobs/JOB-20260807-143022 T1 "Implement endpoint"

set -euo pipefail

WORKTREE="${1:?Usage: $0 <worktree-path> <job-dir> <task-id> [message]}"
JOB_DIR="${2:?}"
TASK_ID="${3:?}"
MSG="${4:-checkpoint: $TASK_ID}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if [[ ! -d "$WORKTREE/.git" && ! -f "$WORKTREE/.git" ]]; then
  echo "ERROR: $WORKTREE is not a Git worktree." >&2
  exit 1
fi

cd "$WORKTREE"

# Stage all changes in the worktree
git add -A

# Check if there's anything to commit
if git diff --cached --quiet; then
  echo "No changes to checkpoint for task $TASK_ID (working tree is clean)."
  SHA="$(git rev-parse HEAD)"
else
  git commit -m "forge/$TASK_ID: $MSG"
  SHA="$(git rev-parse HEAD)"
  echo "✓ Checkpoint committed: $SHA"
fi

# ---------------------------------------------------------------------------
# Append to checkpoints.json
# ---------------------------------------------------------------------------
DIFF_STATS=$(git show --stat HEAD 2>/dev/null | tail -5 | head -4 || echo "")

python3 - <<PYEOF
import json, os

checkpoints_file = "$JOB_DIR/checkpoints.json"
if os.path.exists(checkpoints_file):
    with open(checkpoints_file) as f:
        checkpoints = json.load(f)
else:
    checkpoints = []

checkpoints.append({
    "id": "$TASK_ID",
    "type": "task",
    "sha": "$SHA",
    "timestamp": "$TIMESTAMP",
    "message": "$MSG",
    "diff_stats": """$DIFF_STATS"""
})

with open(checkpoints_file, "w") as f:
    json.dump(checkpoints, f, indent=2)
print(f"Checkpoint appended to $JOB_DIR/checkpoints.json")
PYEOF

# ---------------------------------------------------------------------------
# Append to work ledger
# ---------------------------------------------------------------------------
python3 - <<PYEOF >> "$JOB_DIR/work-ledger.ndjson"
import json
print(json.dumps({
    "event": "checkpoint",
    "task_id": "$TASK_ID",
    "sha": "$SHA",
    "timestamp": "$TIMESTAMP",
    "message": "$MSG"
}))
PYEOF

echo "✓ Task $TASK_ID checkpoint: $SHA"

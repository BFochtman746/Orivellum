#!/usr/bin/env bash
# create-worktree.sh — Isolate a Forge job in a Git worktree.
# Creates a branch and worktree for a job. Records the start checkpoint.
#
# Usage: bash scripts/create-worktree.sh <project-root> <job-id> [<job-dir>]
#   project-root  Absolute path to the Git repository root in WSL
#   job-id        e.g. JOB-20260807-143022
#   job-dir       Directory for forge-jobs output (default: forge-jobs/<job-id>)
#
# Example:
#   bash scripts/create-worktree.sh /home/user/forge/myproject JOB-20260807-143022

set -euo pipefail

PROJECT_ROOT="${1:?Usage: $0 <project-root> <job-id> [<job-dir>]}"
JOB_ID="${2:?}"
JOB_DIR="${3:-forge-jobs/$JOB_ID}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

BRANCH_NAME="forge/$JOB_ID"
WORKTREE_PATH="${PROJECT_ROOT}-${JOB_ID}"

# Validate project root
if [[ ! -d "$PROJECT_ROOT/.git" ]]; then
  echo "ERROR: $PROJECT_ROOT is not a Git repository." >&2
  echo "Initialize it first: cd $PROJECT_ROOT && git init && git commit --allow-empty -m 'init'" >&2
  exit 1
fi

# Validate job ID format
if ! [[ "$JOB_ID" =~ ^JOB-[0-9]{8}-[0-9]{6}$ ]]; then
  echo "ERROR: Job ID must match JOB-YYYYMMDD-HHMMSS, got: $JOB_ID" >&2
  exit 1
fi

# Check for existing worktree
if [[ -d "$WORKTREE_PATH" ]]; then
  echo "ERROR: Worktree already exists at $WORKTREE_PATH" >&2
  echo "Use rollback-verify.sh to remove it first." >&2
  exit 1
fi

mkdir -p "$JOB_DIR"

echo "Creating Forge worktree..."
echo "  Project: $PROJECT_ROOT"
echo "  Branch:  $BRANCH_NAME"
echo "  Path:    $WORKTREE_PATH"
echo "  Job dir: $JOB_DIR"
echo ""

# ---------------------------------------------------------------------------
# 1. Create the branch and worktree
# ---------------------------------------------------------------------------
cd "$PROJECT_ROOT"

# Capture the start SHA before creating worktree
START_SHA=$(git rev-parse HEAD)
START_BRANCH=$(git rev-parse --abbrev-ref HEAD)

git worktree add -b "$BRANCH_NAME" "$WORKTREE_PATH" HEAD
echo "  ✓ Worktree created"

# ---------------------------------------------------------------------------
# 2. Record the start checkpoint
# ---------------------------------------------------------------------------
python3 - <<PYEOF > "$JOB_DIR/checkpoints.json"
import json, os
checkpoints = [
    {
        "id": "START",
        "type": "start",
        "sha": "$START_SHA",
        "branch": "$BRANCH_NAME",
        "base_branch": "$START_BRANCH",
        "worktree_path": "$WORKTREE_PATH",
        "timestamp": "$TIMESTAMP",
        "description": "Worktree created from HEAD of $START_BRANCH"
    }
]
print(json.dumps(checkpoints, indent=2))
PYEOF
echo "  ✓ Start checkpoint recorded: $START_SHA"

# ---------------------------------------------------------------------------
# 3. Write the work ledger opening event
# ---------------------------------------------------------------------------
python3 - <<PYEOF >> "$JOB_DIR/work-ledger.ndjson"
import json
print(json.dumps({
    "event": "job_started",
    "job_id": "$JOB_ID",
    "timestamp": "$TIMESTAMP",
    "worktree_path": "$WORKTREE_PATH",
    "branch": "$BRANCH_NAME",
    "base_sha": "$START_SHA"
}))
PYEOF
echo "  ✓ Work ledger started"

# ---------------------------------------------------------------------------
# 4. Copy policy into the job dir for reference
# ---------------------------------------------------------------------------
cp "orivellum-forge/policies/execution-policy.yaml" "$JOB_DIR/policy-decision.json.template" 2>/dev/null || true

echo ""
echo "✅ Worktree ready: $WORKTREE_PATH"
echo ""
echo "Next steps:"
echo "  1. Load task-contract.json into $JOB_DIR/"
echo "  2. Start OpenCode in the worktree:"
echo "     opencode --config orivellum-forge/opencode/opencode.json \\"
echo "              --agent build \\"
echo "              --cwd $WORKTREE_PATH \\"
echo "              \"Implement the task contract at $JOB_DIR/task-contract.json\""
echo "  3. After build: bash scripts/checkpoint.sh $WORKTREE_PATH $JOB_DIR T1"
echo "  4. After all tasks: bash scripts/run-gates.sh $WORKTREE_PATH $JOB_DIR"

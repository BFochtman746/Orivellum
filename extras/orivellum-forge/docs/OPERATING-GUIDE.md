# Forge Operating Guide

**Audience:** A-01 operator (you)  
**Assumes:** Phase 0 and Phase 1 setup complete, smoke test passed

---

## Daily startup

```bash
# 1. Start Lemonade on Windows — load Qwen3-Coder-30B-A3B-Instruct
#    Server must be running on 127.0.0.1:8080

# 2. In WSL — start OpenCode server in a persistent tmux session
tmux new-session -d -s forge \
  "opencode serve --hostname 127.0.0.1 --port 4096 2>&1 | tee /tmp/opencode-forge.log"

# Verify it's running
tmux ls                  # should show: forge: 1 windows
curl -s http://127.0.0.1:4096/api/v1/app | python3 -m json.tool
```

---

## Starting a new job

### Step 1 — Plan

```bash
JOB_ID="JOB-$(date +%Y%m%d-%H%M%S)"
mkdir -p forge-jobs/$JOB_ID

# Copy authority inventory into the job dir (or re-run inspect)
cp forge-jobs/PHASE0/authority-inventory.json forge-jobs/$JOB_ID/
cp forge-jobs/PHASE0/authority-inventory-wsl.json forge-jobs/$JOB_ID/ 2>/dev/null || true

# Run the planner against your project
cd /path/to/your/project
opencode --config /path/to/orivellum-forge/opencode/opencode.json \
  --agent plan \
  --cwd . \
  "JOB_ID: $JOB_ID. <your task description here>"
```

The planner writes `forge-jobs/$JOB_ID/task-contract.json` with status `AWAITING_APPROVAL`.

**Review the contract.** Check:
- `outcome` matches your intent
- `affected_files` is not too broad
- `non_goals` captures what you don't want
- No HIGH/CRITICAL risks you don't understand

### Step 2 — Approve

```bash
# After reviewing the contract, approve it
python3 - <<'EOF'
import json, sys
from datetime import datetime, timezone

path = f"forge-jobs/{sys.argv[1]}/task-contract.json"
with open(path) as f:
    contract = json.load(f)

contract["status"] = "APPROVED"
contract["approved_at"] = datetime.now(timezone.utc).isoformat()

with open(path, "w") as f:
    json.dump(contract, f, indent=2)

print(f"Contract approved: {path}")
EOF
"$JOB_ID"
```

### Step 3 — Build

```bash
PROJECT_ROOT="/home/$USER/forge/myproject"   # absolute WSL path

# Create the isolated worktree
bash orivellum-forge/scripts/create-worktree.sh "$PROJECT_ROOT" "$JOB_ID"

WORKTREE="${PROJECT_ROOT}-${JOB_ID}"

# Run the builder
opencode --config orivellum-forge/opencode/opencode.json \
  --agent build \
  --cwd "$WORKTREE" \
  "JOB_ID: $JOB_ID. Task contract: forge-jobs/$JOB_ID/task-contract.json"
```

The builder creates checkpoints after each task. You can watch progress:

```bash
# Watch the work ledger in real time
tail -f forge-jobs/$JOB_ID/work-ledger.ndjson | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        e = json.loads(line)
        print(f\"[{e['event']}] {e.get('task_id','') or e.get('gate_id','')} {e.get('status','')}\")
    except: pass
"
```

### Step 4 — Checkpoint (after each task)

The builder should call `checkpoint.sh` automatically. If it doesn't:

```bash
bash orivellum-forge/scripts/checkpoint.sh "$WORKTREE" "forge-jobs/$JOB_ID" T1
```

### Step 5 — Verify

```bash
bash orivellum-forge/scripts/run-gates.sh "$WORKTREE" "forge-jobs/$JOB_ID"
```

Output ends with one of:
- `🟢 VERIFIED` — proceed to bundle and release
- `🟡 CONDITIONAL` — named manual checks remain
- `🔴 BLOCKED` — start a repair cycle

### Step 6 — Repair (if needed)

```bash
# Provide the exact failure to the repairer
FAILURE=$(python3 -c "
import json
report = json.load(open('forge-jobs/$JOB_ID/test-report.json'))
failed = [g for g in report['gates'] if g['status'] == 'FAIL']
for g in failed:
    print(f\"Gate: {g['gate_id']} ({g['gate_name']})\")
    print(f\"Exit: {g['exit_code']}\")
    print(f\"Output:\\n{g.get('output_excerpt','')}\")
")

opencode --config orivellum-forge/opencode/opencode.json \
  --agent repair \
  --cwd "$WORKTREE" \
  "JOB_ID: $JOB_ID. Repair cycle 1/3. Failure evidence:\\n$FAILURE"

# After repair, re-checkpoint and re-verify
bash orivellum-forge/scripts/checkpoint.sh "$WORKTREE" "forge-jobs/$JOB_ID" REPAIR-1
bash orivellum-forge/scripts/run-gates.sh "$WORKTREE" "forge-jobs/$JOB_ID"
```

### Step 7 — Bundle evidence

```bash
bash orivellum-forge/scripts/bundle-evidence.sh "forge-jobs/$JOB_ID"
```

### Step 8 — Review (optional but recommended for large changes)

```bash
opencode --config orivellum-forge/opencode/opencode.json \
  --agent review \
  --cwd "$WORKTREE" \
  "JOB_ID: $JOB_ID. Review the candidate. Contract: forge-jobs/$JOB_ID/task-contract.json, Diff: forge-jobs/$JOB_ID/diff-summary.md, Gates: forge-jobs/$JOB_ID/test-report.json"
```

### Step 9 — Release (user approval)

If the decision is VERIFIED or CONDITIONAL (with accepted gaps):

```bash
# Option A: merge the worktree branch into your main branch
cd "$PROJECT_ROOT"
git merge "forge/$JOB_ID" --no-ff -m "Release $JOB_ID: <outcome from contract>"

# Record the merge in the release decision
python3 - <<'EOF'
import json, sys
from datetime import datetime, timezone

path = f"forge-jobs/{sys.argv[1]}/release-decision.json"
with open(path) as f:
    decision = json.load(f)

decision["user_approved_at"] = datetime.now(timezone.utc).isoformat()
decision["merge_commit"] = sys.argv[2]

with open(path, "w") as f:
    json.dump(decision, f, indent=2)
print("Merge recorded")
EOF
"$JOB_ID" "$(git -C $PROJECT_ROOT rev-parse HEAD)"

# Option B: discard the job if you change your mind
bash orivellum-forge/scripts/rollback-verify.sh "$PROJECT_ROOT" "$JOB_ID" HEAD --discard
```

---

## Monitoring OpenCode

```bash
# View OpenCode server logs
tmux attach -t forge
# Ctrl+B, D to detach without stopping

# View the log file
tail -100 /tmp/opencode-forge.log

# Restart OpenCode server (if it crashes)
tmux kill-session -t forge
tmux new-session -d -s forge \
  "opencode serve --hostname 127.0.0.1 --port 4096 2>&1 | tee /tmp/opencode-forge.log"
```

---

## Viewing all jobs

```bash
# List all jobs with their decisions
for dir in forge-jobs/JOB-*/; do
  if [[ -f "$dir/release-decision.json" ]]; then
    DECISION=$(python3 -c "import json; print(json.load(open('$dir/release-decision.json'))['decision'])")
    echo "$dir → $DECISION"
  else
    echo "$dir → in progress"
  fi
done

# Show the evidence for a specific job
ls -la forge-jobs/$JOB_ID/
```

---

## Safe worktree cleanup

```bash
# List all active worktrees
git worktree list

# Remove a completed worktree (after merge)
git worktree remove /path/to/project-JOB-YYYYMMDD-HHMMSS
git branch -D forge/JOB-YYYYMMDD-HHMMSS

# Remove all completed worktrees that have been merged
git worktree prune
```

---

## Updating OpenCode

```bash
# Check current version
opencode --version

# Update to latest
curl -fsSL https://opencode.ai/install | bash

# Re-run the smoke test after updating
bash orivellum-forge/scripts/smoke-test-build-mode.sh forge-jobs/UPDATE-$(date +%Y%m%d)
```

---

## Updating the model in Lemonade

1. Load the new model in Lemonade (do not start the server yet)
2. Update `opencode/opencode.json` — change the `id` under `providers.lemonade.models`
3. Start the Lemonade server with the new model
4. Re-run the smoke test: `bash scripts/smoke-test-build-mode.sh forge-jobs/MODEL-UPDATE-$(date +%Y%m%d)`
5. Run the eval mini-corpus (T01–T10) before using the new model for real jobs

---

## Checklist before any real job

- [ ] Lemonade server is running with a model loaded
- [ ] `curl http://127.0.0.1:8080/v1/models` returns at least one model
- [ ] OpenCode server is running in tmux (`tmux ls` shows `forge`)
- [ ] Phase 0 smoke test was PASS for the current OpenCode version
- [ ] Project has a `forge-profile.yaml` or the planner will use defaults
- [ ] Project root is in the WSL filesystem (not `/mnt/c/`)
- [ ] Project root has a git history (`git log --oneline | head -3`)

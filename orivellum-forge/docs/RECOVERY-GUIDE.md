# Forge Recovery Guide

**Audience:** A-01 operator  
**Use when:** A job is stuck, a worktree is corrupted, OpenCode crashes, or a merge needs to be undone.

---

## Scenario 1 — OpenCode crashed mid-build

**Symptom:** OpenCode process died; the worktree has uncommitted changes.

```bash
# 1. Check what's in the worktree
cd /path/to/project-JOB-YYYYMMDD-HHMMSS
git status
git diff --stat

# 2. Option A: Discard uncommitted work and restart the task from the last checkpoint
git stash
# or
git checkout -- .

# 3. Option B: Commit what was done and continue from there
git add -A
git commit -m "forge/partial: partial progress before OpenCode crash"
bash ../../orivellum-forge/scripts/checkpoint.sh . forge-jobs/$JOB_ID PARTIAL-$(date +%H%M)

# 4. Restart OpenCode server
tmux kill-session -t forge 2>/dev/null || true
tmux new-session -d -s forge \
  "opencode serve --hostname 127.0.0.1 --port 4096 2>&1 | tee /tmp/opencode-forge.log"

# 5. Resume the build — give the builder the current state
opencode --config orivellum-forge/opencode/opencode.json \
  --agent build \
  --cwd /path/to/project-JOB-YYYYMMDD-HHMMSS \
  "JOB_ID: $JOB_ID. Resume from last checkpoint. Current git status shows uncommitted changes. Task contract: forge-jobs/$JOB_ID/task-contract.json"
```

---

## Scenario 2 — Repair loop hit its limit (BLOCKED_WITH_EVIDENCE)

**Symptom:** Gate still failing after 3 repair cycles; decision is BLOCKED.

**Do not start a 4th repair cycle.** Options:

### Option A: Amend the task contract and start a new job

```bash
# 1. Review what failed
cat forge-jobs/$JOB_ID/test-report.json | python3 -c "
import json, sys
report = json.load(sys.stdin)
for g in report['gates']:
    if g['status'] == 'FAIL':
        print(f\"{g['gate_id']}: {g.get('output_excerpt','')[:300]}\")
"

# 2. Identify whether the failure is:
#    (a) A scope issue — the task was too large, split it
#    (b) A model limitation — the model cannot solve this type of problem
#    (c) A wrong approach in the contract — the plan needs revision

# 3. Archive the blocked job
bash orivellum-forge/scripts/rollback-verify.sh \
  /path/to/project $JOB_ID HEAD --discard

# 4. Create a new narrower job from the failure evidence
NEW_JOB_ID="JOB-$(date +%Y%m%d-%H%M%S)"
mkdir -p forge-jobs/$NEW_JOB_ID
echo "Previous job $JOB_ID blocked on: $(cat forge-jobs/$JOB_ID/release-decision.json | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"blocking_reason\"])')" \
  > forge-jobs/$NEW_JOB_ID/predecessor-note.txt
```

### Option B: Escalate to Aider for recovery

Only use Aider if OpenCode's repair loop has been exhausted:

```bash
# Install Aider (if not already installed)
pip install aider-chat

# Point Aider at LM Studio
aider --openai-api-base http://127.0.0.1:8080/v1 \
      --openai-api-key lm-studio \
      --model qwen3-coder-30b-a3b-instruct \
      --no-auto-commit \
      /path/to/project-JOB-YYYYMMDD-HHMMSS/src/failing_file.py

# Aider works interactively — give it the failure output, let it propose a fix
# Then run gates again:
bash orivellum-forge/scripts/checkpoint.sh \
  /path/to/project-$JOB_ID forge-jobs/$JOB_ID AIDER-REPAIR
bash orivellum-forge/scripts/run-gates.sh \
  /path/to/project-$JOB_ID forge-jobs/$JOB_ID
```

---

## Scenario 3 — Rollback: the released change broke something

**Symptom:** A merged change caused a regression in production.

```bash
# 1. Find the merge commit and the prior safe SHA
cd /path/to/project
git log --oneline -10

# 2. Revert the merge commit (creates a new revert commit — clean history)
git revert -m 1 <merge-commit-sha>

# 3. Verify the revert didn't break anything
bash orivellum-forge/scripts/run-gates.sh . forge-jobs/REVERT-$(date +%Y%m%d-%H%M%S)

# 4. Record the revert in the original job's evidence
python3 - <<'EOF'
import json
from datetime import datetime, timezone
path = f"forge-jobs/{job_id}/release-decision.json"
with open(path) as f:
    d = json.load(f)
d["notes"] = f"REVERTED at {datetime.now(timezone.utc).isoformat()} — regression detected post-merge"
with open(path, "w") as f:
    json.dump(d, f, indent=2)
EOF
```

---

## Scenario 4 — Worktree is orphaned (project moved or deleted)

**Symptom:** `git worktree list` shows a worktree at a path that no longer exists.

```bash
# Clean up orphaned worktrees
git worktree prune --verbose

# If prune doesn't remove it (e.g., force-deleted dir):
git worktree list --porcelain | grep "worktree" | while read _ path; do
  [[ ! -d "$path" ]] && git worktree remove "$path" --force 2>/dev/null || true
done
```

---

## Scenario 5 — LM Studio is not responding

**Symptom:** OpenCode hangs or returns errors; LM Studio health check fails.

```bash
# Check LM Studio from WSL
curl -s --connect-timeout 5 http://127.0.0.1:8080/v1/models | python3 -m json.tool

# If not responding:
# 1. Restart LM Studio on Windows
# 2. Verify the server is enabled (in LM Studio UI: Developer → Start Server)
# 3. Verify the model is loaded (not just downloaded)
# 4. Re-run smoke test after restart
bash orivellum-forge/scripts/smoke-test-build-mode.sh forge-jobs/LM-RESTART-$(date +%Y%m%d)
```

---

## Scenario 6 — Evidence manifest integrity failure (G9)

**Symptom:** `bundle-evidence.sh` reports hash mismatch or G9 fails.

This usually means a file was edited manually after gates ran. Do not work around it — regenerate everything:

```bash
# Re-run gates from scratch (this overwrites existing gate results)
bash orivellum-forge/scripts/run-gates.sh "$WORKTREE" "forge-jobs/$JOB_ID"

# Re-bundle
bash orivellum-forge/scripts/bundle-evidence.sh "forge-jobs/$JOB_ID"
```

If the hash still fails after re-running gates, a file in `forge-jobs/$JOB_ID/` was modified manually after bundling. Archive the job and start a new one:

```bash
cp -r forge-jobs/$JOB_ID forge-jobs/archive/$JOB_ID-INTEGRITY-FAILURE-$(date +%Y%m%d)
```

---

## Scenario 7 — Security scanner finds a legitimate issue in old code

**Symptom:** Semgrep or Gitleaks fails on code that was there before the job started.

Pre-existing findings that the job did not introduce are acceptable to except, with documentation:

```bash
# Add an exception to policy-decision.json
python3 - <<'EOF'
import json
from datetime import datetime, timezone

path = f"forge-jobs/{job_id}/policy-decision.json"
exceptions = []
if __import__('os').path.exists(path):
    with open(path) as f:
        try:
            exceptions = json.load(f).get("security_exceptions", [])
        except Exception:
            pass

exceptions.append({
    "finding_id": "semgrep-<rule-id>-<file>:<line>",  # fill in
    "severity": "HIGH",
    "owner": "operator",
    "risk_acceptance_reason": "Pre-existing in codebase before this job. Not introduced by this change. Tracked in backlog.",
    "expiry_date": "2027-01-01",
    "approved_by": "operator"
})

with open(path, "w") as f:
    json.dump({"security_exceptions": exceptions}, f, indent=2)
print("Exception recorded")
EOF

# Re-run gates (Semgrep will still flag it, but the exception is now on record)
# The reviewer must note the exception in their report
```

---

## Recovery checklist

Before any recovery action:
- [ ] Archive the current job state: `cp -r forge-jobs/$JOB_ID forge-jobs/archive/$JOB_ID-$(date +%Y%m%d)`
- [ ] Record the recovery action in the work ledger (manual append)
- [ ] After recovery, re-run `bundle-evidence.sh` to update the manifest
- [ ] Never edit `release-decision.json` or `evidence-manifest.sha256` manually

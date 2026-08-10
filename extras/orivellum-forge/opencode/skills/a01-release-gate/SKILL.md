---
name: a01-release-gate
description: >
  Invoke the Forge release gate from within an OpenCode session.
  Runs run-gates.sh and bundle-evidence.sh, then returns the
  structured release decision to the calling agent.
---

# A-01 Release Gate Skill

Use this skill at the end of a build or repair session to trigger formal gate evaluation.

## When to invoke

- After all tasks in the task contract are complete
- After each repair cycle completes
- When the builder reports "All tasks complete, ready for gate evaluation"

## Steps

### 1. Ensure the job directory is populated

The following files must exist in `forge-jobs/<JOB_ID>/` before the gate runs:

```
task-contract.json          ← required (G9 gate)
authority-inventory.json    ← required (G9 gate)
checkpoints.json            ← required (G9 gate)
work-ledger.ndjson          ← required (G9 gate)
```

If any are missing, state which are missing and stop.

### 2. Run the gates

```bash
bash orivellum-forge/scripts/run-gates.sh \
  <worktree-path> \
  forge-jobs/<JOB_ID>
```

Capture the output. The gate runner exits 0 on VERIFIED/CONDITIONAL, non-zero on BLOCKED.

### 3. Bundle evidence

```bash
bash orivellum-forge/scripts/bundle-evidence.sh \
  forge-jobs/<JOB_ID>
```

This writes `release-decision.json` and `evidence-manifest.sha256`.

### 4. Read the release decision

```bash
python3 -c "import json; d=json.load(open('forge-jobs/<JOB_ID>/release-decision.json')); print(d['decision'], '-', d.get('blocking_reason') or ', '.join(d.get('conditional_items',[])) or 'all gates pass')"
```

### 5. Report to the user

Format your report as:

```
━━━ Release Gate Result — <JOB_ID> ━━━

Decision: VERIFIED | CONDITIONAL | BLOCKED

Gate summary:
  G1 Dependency Integrity:  PASS
  G2 Format/Lint/Type:      PASS
  G3 Unit Tests:            PASS | FAIL
  G4 Integration Tests:     PASS | SKIPPED
  G5 Browser Acceptance:    PASS | SKIPPED | FAIL
  G6 Build/Startup:         PASS
  G7 Security:              PASS | FAIL
  G8 Diff/Scope:            PASS | FAIL
  G9 Evidence Manifest:     PASS | FAIL

[If BLOCKED]
Blocking reason: <exact reason from release-decision.json>
Next: Start a repair cycle with the exact failure evidence above.

[If CONDITIONAL]
Manual checks remaining:
  - <item 1>
The candidate may not be labeled complete until these are resolved or explicitly deferred.

[If VERIFIED]
The candidate is ready for your approval.
⚠️ No merge or deployment will occur without your explicit approval.
Evidence: forge-jobs/<JOB_ID>/
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### 6. What NOT to do

- Do not interpret VERIFIED as "merged." The user must approve merge explicitly.
- Do not re-run gates more than once without a code change between runs.
- Do not modify `release-decision.json` or `evidence-manifest.sha256` manually.
- Do not report VERIFIED if the gate runner exited non-zero.

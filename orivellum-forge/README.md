# Orivellum Forge — A-01 Local Coding Factory

**Version:** 0.1.0 (Phase 1 — Local Quality Factory)  
**Host:** A-01 · AMD Ryzen AI Max+ 395 · 128 GB unified memory · Windows 11 + WSL  
**Inference:** LM Studio on `127.0.0.1:8080` · Model: Qwen3-Coder-30B-A3B-Instruct  
**Engine:** OpenCode in Ubuntu WSL  
**Mobile UI:** Through existing Orivellum gateway over WireGuard/Tailscale only

---

## What this is

Forge is a disciplined local coding agent built into Orivellum. It reproduces what makes Replit Agent effective — read-only planning, isolated worktrees, deterministic test gates, bounded repair loops, immutable checkpoints, and an evidence-based release decision — using only free, private, locally-running components.

**The authority for "complete" is deterministic test results and recorded evidence, never the model saying it is done.**

---

## Quick start (Phase 0 — must complete before Phase 1)

Run these steps once on A-01. They verify the environment before any real project uses Forge.

### 1. Record the authority inventory

```powershell
# Windows PowerShell
.\scripts\inspect-a01.ps1 -OutputDir forge-jobs\PHASE0
```

```bash
# WSL (Ubuntu)
bash scripts/inspect-a01.sh forge-jobs/PHASE0
```

### 2. Confirm LM Studio is reachable

```bash
# In WSL — LM Studio must be running with a model loaded
curl -s http://127.0.0.1:8080/v1/models | python3 -m json.tool
```

### 3. Run the OpenCode build-mode smoke test

**This is mandatory before Phase 1.** It confirms the write tool works with your local model.

```bash
bash scripts/smoke-test-build-mode.sh forge-jobs/PHASE0
```

Expected output: `PASS — OpenCode write tool functional with local model`

If it fails, update OpenCode (`curl -fsSL https://opencode.ai/install | bash`) and re-test.

### 4. Verify Git worktree and rollback

```bash
mkdir -p /home/$USER/forge/sample-project
cd /home/$USER/forge/sample-project
git init && git commit --allow-empty -m "forge-init"

bash /path/to/orivellum-forge/scripts/create-worktree.sh \
  /home/$USER/forge/sample-project \
  PHASE0-TEST-001

bash /path/to/orivellum-forge/scripts/rollback-verify.sh \
  /home/$USER/forge/sample-project \
  PHASE0-TEST-001 \
  HEAD
```

**Phase 0 is complete when:** inventory exists, smoke test passes, worktree round-trip succeeds.

---

## Running a job

### Step 1 — Plan

```bash
# Start OpenCode server in WSL (keep tmux session alive)
tmux new-session -d -s forge "opencode serve --hostname 127.0.0.1 --port 4096"

# In a new terminal: submit a plan job
JOB_ID="JOB-$(date +%Y%m%d-%H%M%S)"
mkdir -p forge-jobs/$JOB_ID
# OpenCode connects to LM Studio via opencode/opencode.json
opencode --config opencode/opencode.json --agent plan \
  "Inspect the project at /path/to/project and produce a task contract for: <your request>"
```

Review `forge-jobs/$JOB_ID/task-contract.json`. Approve it before proceeding.

### Step 2 — Build (after approval)

```bash
bash scripts/create-worktree.sh /path/to/project $JOB_ID
# OpenCode builds in the worktree only
opencode --config opencode/opencode.json --agent build \
  --cwd /path/to/project-$JOB_ID \
  "Implement the approved task contract at ../forge-jobs/$JOB_ID/task-contract.json"
```

### Step 3 — Verify

```bash
bash scripts/run-gates.sh /path/to/project-$JOB_ID forge-jobs/$JOB_ID
```

Gates either pass (continue) or fail (Repair loop, max 3 cycles).

### Step 4 — Review and Release

```bash
bash scripts/bundle-evidence.sh forge-jobs/$JOB_ID
# Review release-decision.json — VERIFIED / CONDITIONAL / BLOCKED
# Only merge after user approval
```

---

## Directory structure

```
orivellum-forge/
  README.md                        ← this file
  policies/
    execution-policy.yaml          ← what the agent may and may not do
    release-policy.yaml            ← gate rules and release conditions
  contracts/
    task-contract.schema.json      ← JSON Schema for task contracts
    release-decision.schema.json   ← JSON Schema for release decisions
  templates/
    project-profile.yaml           ← fill in per project
    playwright.config.template.ts  ← browser test config
  scripts/
    inspect-a01.ps1                ← Windows authority inventory
    inspect-a01.sh                 ← WSL authority inventory
    smoke-test-build-mode.sh       ← Phase 0 OpenCode write test
    create-worktree.sh             ← isolate a job in a worktree
    checkpoint.sh                  ← commit a mid-task checkpoint
    run-gates.sh                   ← 9-gate deterministic verifier
    bundle-evidence.sh             ← SHA256 manifest + release decision
    rollback-verify.sh             ← revert and re-run gates
  opencode/
    opencode.json                  ← provider + agent config
    agents/
      planner.md                   ← read-only plan agent system prompt
      builder.md                   ← build agent system prompt
      repairer.md                  ← repair agent system prompt
      reviewer.md                  ← read-only review agent prompt
    skills/
      a01-release-gate/SKILL.md    ← release gate skill for OpenCode
  evals/
    README.md                      ← qualification lab instructions
    tasks/                         ← 10-task initial mini-corpus
  docs/
    OPERATING-GUIDE.md             ← day-to-day usage
    RECOVERY-GUIDE.md              ← when things go wrong
    SECURITY-BOUNDARY.md           ← what Forge can and cannot touch

forge-jobs/                        ← created at runtime, one dir per job
  JOB-YYYYMMDD-HHMMSS/
    task-contract.json
    authority-inventory.json
    policy-decision.json
    checkpoints.json
    work-ledger.ndjson
    diff-summary.md
    test-report.json
    smoke-test-result.json
    browser-artifacts/
    security-reports/
    reviewer-report.md
    release-decision.json
    evidence-manifest.sha256
    opencode-version.txt
```

---

## Component versions (Phase 1 baseline)

| Component | Version | Source |
|---|---|---|
| OpenCode | ≥ 1.17.9 | `curl -fsSL https://opencode.ai/install \| bash` |
| LM Studio | Existing | Already installed on A-01 |
| Qwen3-Coder-30B-A3B-Instruct | Latest GGUF | Load in LM Studio |
| Playwright | ≥ 1.45 | `npm install -D @playwright/test && npx playwright install` |
| Semgrep CE | ≥ 1.75 | `pip install semgrep` |
| Gitleaks | ≥ 8.18 | Download from github.com/gitleaks/gitleaks/releases |
| OSV-Scanner | ≥ 1.8 | `go install github.com/google/osv-scanner/cmd/osv-scanner@latest` |
| tmux | Any | `sudo apt install tmux` (WSL) |

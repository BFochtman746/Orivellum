# Orivellum Forge A-01 — Research Evaluation & Perfected Specification

**Evaluation date:** 2026-08-07  
**Research basis:** Live fetches from OpenCode docs, HuggingFace model card, GitHub issue tracker, independent 2026 agent benchmarks, and the wetheflywheel agent landscape report.

---

## Verdict in one paragraph

The Forge A-01 architecture document is **substantially correct** and strategically sound. Every major component it names — OpenCode, LM Studio, Playwright, Semgrep CE, Gitleaks, OSV-Scanner, Qwen3-Coder-30B-A3B — is confirmed by live sources as real, functional, and the right choice for the stated constraints. Three issues need to be addressed before implementation begins: (1) a local-model file-write bug in OpenCode that was only recently resolved, (2) the doc's silence on Cline as an evaluated alternative, and (3) clarification that "works off local drive" is already true — git requires no remote — plus a concrete no-git fallback for any project that truly has no git history.

---

## What the research confirms as correct

| Claim in the document | Research verdict |
|---|---|
| OpenCode is MIT, model-agnostic, WSL-recommended | ✅ Confirmed. 195K GitHub stars (was 178K in June), MIT license, official WSL docs at opencode.ai/docs/windows-wsl/ |
| OpenCode has Plan (read-only) and Build agents | ✅ Confirmed. These are the two named primary agents; permissions are tool-scoped per agent |
| OpenCode exposes an HTTP server (`opencode serve`) | ✅ Confirmed. Port 4096, OpenAPI 3.1, supports password auth via `OPENCODE_SERVER_PASSWORD`. Java gateway can call it directly |
| OpenCode Web client + WSL server is a supported topology | ✅ Confirmed. `opencode serve --hostname 0.0.0.0 --port 4096` in WSL, desktop connects to `http://localhost:4096` |
| LM Studio provides OpenAI-compatible endpoints | ✅ Confirmed. OpenCode's custom provider config accepts any OpenAI-compatible base URL |
| Qwen3-Coder-30B-A3B is an MoE: 30.5B total, 3.3B active | ✅ Confirmed by HuggingFace model card. 256K native context, 1M with YaRN |
| Qwen3-Coder-30B is designed for agentic use with tool call format | ✅ Confirmed. Model card lists Qwen Code, CLINE, and function-call format as supported |
| Qwen3-Coder-30B does not produce `<think>` blocks | ✅ Confirmed. Non-thinking only; `enable_thinking=False` no longer needed |
| Aider works against any OpenAI-compatible local endpoint | ✅ Confirmed by Aider docs |
| Playwright covers Chromium, Firefox, and WebKit | ✅ Confirmed. WebKit = iPhone Safari-class acceptance tests |
| OpenHands is deferred to Phase 3 | ✅ Strategically correct (see below). Its current capability is much higher than the doc implies, which makes Phase 3 comparison even more worthwhile |
| One model loaded at a time through LM Studio | ✅ Correct memory discipline for 128 GB unified memory |

---

## Issues found — must address before implementation

### Issue 1 (CRITICAL): OpenCode local-model file-write bug

**What it is:** GitHub issue #29940 (filed 2026-05-29, closed 2026-07-29 via PR #29943). When local models write large files, they generate the `content` field before `filePath` in the write tool schema, causing token truncation before the file path is ever produced. Related issues #29996, #29757, and #18454 affected Ollama+Qwen, Ollama+Gemma4, and other local model combos. One benchmark site (aicoderscope.com, June 2026) called this "broken for every local model tested."

**Current status:** Issue is closed and PR #29943 merged as of 2026-07-29 — nine days before this evaluation. The bug was in the write tool schema field ordering, which OpenCode corrected. The fix is present in current releases (v1.17.9+).

**What this means for Forge:**  
The document treats Build mode as a known-good capability. The bug was real and affected Qwen models over Ollama. LM Studio's OpenAI-compatible layer may behave differently (it has stricter response formatting), but this **cannot be assumed** — it must be tested.

**Required addition to Phase 0:**  
Add a build-mode smoke test to the Phase 0 authority baseline:

```bash
# Phase 0 gate — must pass before Phase 1 begins
opencode "Create a file named forge-write-test.py containing: print('forge ok')"
# Verify: forge-write-test.py exists on disk and contains the expected content
# If it fails: install/update OpenCode to latest release; re-test before proceeding
```

This is a five-minute test. If it fails, the entire Build pipeline is broken before it starts.

---

### Issue 2 (MODERATE): Cline was not evaluated and is the closest governance match

**What it is:** Cline (57.9K GitHub stars, 4M+ developers) is a VS Code extension coding agent with "best-in-class governance with step-by-step approval" and full audit trails. It predates OpenCode as an agent and is widely deployed in regulatory/compliance environments.

**Why it matters for Forge:** The Forge philosophy is governance-first — every action approved, every gate deterministic. Cline was designed with this exact mindset (every file write is shown to the user before execution). OpenCode's Plan/Build separation is similar but less granular.

**Verdict:** OpenCode remains the correct choice for Forge. Its server mode (`opencode serve`) is how the Java gateway drives it headlessly from an iPhone — Cline has no headless API server. Cline requires VS Code and a human watching the screen. For Forge's mobile-controlled, evidence-first design, OpenCode is the only viable pick.

**Action required:** Add a "Deliberately excluded" row for Cline in the architecture document:

> **Cline is not the engine.** It has excellent step-by-step governance but no headless HTTP server — it cannot be driven programmatically from a mobile gateway. Its governance philosophy informed the Forge Work Ledger design.

---

### Issue 3 (MODERATE): Model quality gap — A-01's hardware changes the calculus

A DEV Community benchmark (June 2026) ran Qwen3-Coder-30B on an RTX 3090 (24 GB VRAM) against Claude 3.5: Claude scored 89.4/100, Qwen 22.8/100 on 27 real agent tasks. That sounds alarming. But:

- The RTX 3090 forces aggressive quantization (Q4 or smaller) and limits context to ~32K
- A-01's 128 GB unified memory runs the model at Q8 or even BF16 for the full 30.5B
- At full precision, the 30B MoE activates 3.3B parameters per token — equivalent VRAM to a ~7B dense model, but with the knowledge of 30.5B
- The Contra Collective M5 Max test (same A3B, June 2026) rated it "the first open weight coder that holds together inside a real agentic loop on a single device"
- The A-01 Ryzen AI Max+ 395 (128 GB unified) is significantly faster than M5 Max for inference

**Conclusion:** The 22.8/100 result is a constrained-hardware artifact, not a model quality verdict. The document's model-qualification laboratory (25-task corpus) is the right way to measure performance on A-01 specifically. The document already handles this correctly; this note is to prevent the alarming benchmark from causing unnecessary doubt.

---

### Issue 4 (LOW): OpenHands capability is higher than the document implies

OpenHands is described as a "capable" alternative deferred to Phase 3. As of mid-2026 it has:
- 75.8K GitHub stars, v1.7.0 (May 2026)
- **72.8% SWE-bench Verified** — the highest autonomous score of any open-source agent
- $18.8M Series A from Gradient Ventures
- Docker-sandboxed runtime with browser use and a planning agent
- An SDK for building custom agents

The Phase 3 evaluation is still correct — introducing it now would create a second platform. But the framing should acknowledge that Phase 3 is not "evaluate a minor alternative" but "evaluate the most capable open autonomous agent." This strengthens the Phase 3 decision, not weakens it.

---

## Local drive instead of git — three modes explained

**Short answer: the document already runs fully off your local drive.** Git requires no remote, no GitHub, no network connection. `git init` on a local folder is a local-drive-only operation. Every worktree, branch, and checkpoint in the architecture lives in `.git/` on your SSD.

Here are the three modes in order of increasing simplicity:

### Mode A — Git local-only (recommended, already in the document)

```bash
# On A-01 in WSL — purely local, no remote, no push ever needed
mkdir -p /home/user/forge-projects/myproject
cd /home/user/forge-projects/myproject
git init                          # creates .git/ locally, no remote needed
git commit --allow-empty -m "init"

# Forge creates a worktree for each job — no network involved
git worktree add ../forge-JOB-20260807-001 -b forge/JOB-20260807-001

# Checkpoint mid-task
git -C ../forge-JOB-20260807-001 commit -am "checkpoint: task 3 complete"

# Rollback
git -C ../forge-JOB-20260807-001 reset --hard CHECKPOINT_SHA

# Discard a failed job entirely
git worktree remove ../forge-JOB-20260807-001
git branch -D forge/JOB-20260807-001
```

Everything stays on disk. Git is just a versioning engine, not a sync tool. The remote push in the document's constraints (`git push` is prohibited during build) is a safety restriction, not an architecture requirement.

### Mode B — Tar/rsync snapshots (no git at all)

For projects that have no git history and you want zero git dependency:

```bash
# snapshot before a job starts
SNAP="forge-jobs/JOB-20260807-001/snapshots"
mkdir -p "$SNAP"
rsync -a --link-dest="$SNAP/latest" myproject/ "$SNAP/$(date +%Y%m%dT%H%M%S)/"
ln -sfn "$SNAP/$(date +%Y%m%dT%H%M%S)" "$SNAP/latest"

# rollback: copy latest snapshot back
rsync -a "$SNAP/pre-job/" myproject/
```

`--link-dest` makes each snapshot space-efficient (hardlinks to unchanged files). A 200 MB project with 5 checkpoints uses ~205 MB, not 1 GB.

**Trade-offs vs. git:**  
- ✅ Works on any directory, including binary-heavy projects  
- ✅ No git knowledge required  
- ❌ No meaningful diff output (the Work Ledger loses its diff summary)  
- ❌ Cannot cherry-pick individual changes  
- ❌ Slower than git for large repositories

### Mode C — WSL btrfs subvolume snapshots (instant, zero-copy)

If WSL is configured with btrfs (not the default ext4 virtual disk):

```bash
# Creates an instant, copy-on-write snapshot
btrfs subvolume snapshot myproject myproject-snap-$(date +%Y%m%dT%H%M%S)

# Rollback: swap directories
mv myproject myproject-failed
btrfs subvolume snapshot myproject-snap-20260807T143000 myproject
```

Zero overhead at snapshot time. Not worth configuring WSL for btrfs unless you have other reasons; Mode A (git local-only) is simpler and more powerful.

**Recommendation:** Keep Mode A (git local-only). The document is already correct. If a specific project type has no existing git history, `git init` it at project creation — this is standard practice and adds nothing except a `.git/` folder.

---

## Architecture corrections and additions

### Add to Phase 0 — Build-mode smoke test

```yaml
# authority-inventory.json — add this section
"opencode_build_test": {
  "version": "<output of: opencode --version>",
  "write_tool_functional": "<true|false — verified by forge-write-test.py>",
  "lm_studio_provider": "custom openai-compatible at http://127.0.0.1:8080/v1",
  "test_model": "<model identifier loaded in LM Studio at test time>",
  "test_result": "<pass|fail|timeout>"
}
```

### Add to the "Recommended free stack" table

| Layer | Addition | Reason |
|---|---|---|
| Session management | **tmux** in WSL | OpenCode TUI needs a persistent terminal session; `opencode serve` must survive SSH disconnects from the gateway. tmux keeps the server alive when the gateway reconnects. |
| Path bridging | `/mnt/d/` or Windows symlinks | Project worktrees should live in the WSL filesystem (`/home/user/forge/`), not under `/mnt/c/`. Cross-filesystem operations are slower and cause git permission issues. Document should state this explicitly. |

### Correction to the job lifecycle — add "Smoke" sub-state

```
Verify → Repair           (existing)
Verify → Smoke → Review   (add this)
```

The "Smoke" step runs a quick startup/sanity test (e.g., `npm start` or `python -m myapp` for 5 seconds) before the full Playwright browser suite. This catches import errors and port-binding failures that Playwright would otherwise report as "browser could not connect" with no useful trace.

### Add to the iPhone/Orivellum experience table

| Mode | Addition |
|---|---|
| **Inspect** | New mode: read-only authority snapshot on demand. Shows current A-01 facts (LM Studio model loaded, disk, WSL state) without starting a job. Useful before approving a plan. |

### Add to the evidence record structure

```text
forge-jobs/JOB-YYYYMMDD-NNN/
  ...existing files...
  opencode-session.log       ← raw OpenCode server stdout (filtered for evidence, not private reasoning)
  smoke-test-result.json     ← new
  opencode-version.txt       ← exact version string, reproducibility
```

---

## Updated model-role table

| Role | Candidate | Context on A-01 | Notes |
|---|---|---|---|
| Builder | Qwen3-Coder-30B-A3B-Instruct | ~256K native at Q8 | Non-thinking mode only. Best local agentic coding MoE. Explicitly designed for CLINE/tool-call format, which maps to OpenCode's tool schema. |
| Builder benchmark | Qwen3-Coder-480B (cloud only) | N/A — reference only | The 480B cloud model serves as the ceiling to measure local 30B quality against on the same 25-task corpus. Never loaded locally. |
| Reviewer | Qwen3-32B | ~32K–128K practical | Thinking mode available if needed for deep review passes. Independent of builder. |
| Judge | gpt-oss-20B or gpt-oss-120B | Load only when needed | Never concurrent with other large models. 120B requires exclusive load. |
| Tie-breaker | Gemma 3 27B | ~32K practical | Second opinion for high-value disagreements. |

---

## Perfected summary: what to change, what to keep

### Keep exactly as written
- All six Replit capability equivalents (Plan, Build, Ledger, Browser, Optimization, Checkpoint)
- The 9-gate verification order
- The repair loop hard boundaries (3 cycles, no test weakening, blocked-with-evidence termination)
- The six job states (PLAN, BUILD, VERIFY, REPAIR, REVIEW, RELEASE) and stateDiagram
- The evidence manifest format and sha256 integrity check
- The "free" cost definition
- The release gate triple output (VERIFIED / CONDITIONAL / BLOCKED)
- The Phase 0→1→2→3 staged sequence
- The qualification laboratory (25-task corpus, promotion rules)
- The first build package skeleton — exactly right

### Change or add
1. **Phase 0** — add the 5-minute OpenCode build-mode smoke test as an exit gate requirement
2. **"Deliberately excluded"** — add Cline with the reason (no headless HTTP server)
3. **Workspace control row** — add "Git is used local-only; no remote is required or created"
4. **Local drive appendix** — add the Mode A / Mode B / Mode C section above (or a condensed version)
5. **tmux** — add to recommended stack as the process manager for `opencode serve` in WSL
6. **Worktree path note** — state explicitly that worktrees must live in the WSL filesystem, not under `/mnt/c/`
7. **Model table** — add the 480B cloud-only benchmark reference row
8. **OpenHands note** — update from "capable" to "72.8% SWE-bench Verified, the most capable open autonomous agent as of mid-2026; Phase 3 evaluation is the right time"

### Nothing needs to be removed
Every exclusion decision (Ollama, second model server, cloud API, autonomous merge) remains correct.

---

## Final score

| Dimension | Rating | Notes |
|---|---|---|
| Component selection | ✅ Excellent | Every tool confirmed current, free, and appropriate |
| Architecture soundness | ✅ Excellent | Evidence-first, no AI authority on "done" — uncommon discipline |
| Risk identification | ⚠️ Good, one gap | OpenCode local file-write bug not called out; must be Phase 0 gate |
| Model qualification plan | ✅ Excellent | 25-task corpus with promotion rules is the correct pattern |
| Local drive compatibility | ✅ Already true | Git is local-only by default; Mode B/C documented as fallback |
| iPhone/mobile UX | ✅ Solid | Six-mode UI, Work Ledger, WireGuard — all consistent with existing Orivellum patterns |
| Staged build discipline | ✅ Excellent | Phase 0 infrastructure before Phase 1 wiring is correct ordering |

**Overall: Architecture approved with three additions.** Add the Phase 0 build-mode smoke test, the Cline exclusion note, and the local-drive appendix. Everything else ships as written.

# Forge Security Boundary

**Version:** 0.1.0  
**Status:** Binding — all Forge operations must respect these boundaries.

This document defines what Forge can access, what it cannot access, and why. It is a reference for reviewing any proposed change to the Forge architecture.

---

## The boundary in one diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│ A-01 MACHINE                                                        │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ PERMITTED ZONE (Forge may operate here)                       │  │
│  │                                                               │  │
│  │  • /home/user/forge/projects/  (WSL, coding projects only)   │  │
│  │  • /tmp/forge-*/               (temp build artifacts)        │  │
│  │  • forge-jobs/                 (evidence, within repo)       │  │
│  │  • 127.0.0.1:8080              (LM Studio — read only)       │  │
│  │  • 127.0.0.1:4096              (OpenCode server — self)      │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ FORBIDDEN ZONE (Forge may never access)                       │  │
│  │                                                               │  │
│  │  • /mnt/d/Orivellum/           (knowledge vault)             │  │
│  │  • /mnt/c/Users/               (Windows user profile)        │  │
│  │  • ~/.ssh/, ~/.gnupg/          (credentials)                 │  │
│  │  • ~/.config/lmstudio/         (LM Studio config)            │  │
│  │  • Any file matching .env, .env.*, secrets.*                 │  │
│  │  • Production databases        (never direct access)         │  │
│  │  • External internet           (except explicitly approved)  │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ GATEWAY (iPhone access only via VPN)                          │  │
│  │                                                               │  │
│  │  • Orivellum Java gateway — receives job requests from phone │  │
│  │  • Gateway calls OpenCode server on 127.0.0.1:4096 only     │  │
│  │  • OpenCode server is NOT reachable from the public internet │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## What Forge agents may access

### Files
| Access | Scope | Notes |
|---|---|---|
| Read | Job worktree (`/home/user/forge/<project>-<job-id>/`) | Primary workspace |
| Read | `forge-jobs/<job-id>/task-contract.json` | Plan reference |
| Read | `forge-jobs/<job-id>/authority-inventory.json` | Environment facts |
| Read | `orivellum-forge/policies/` | Policy reference (read only) |
| Read | `orivellum-forge/opencode/agents/` | Own prompts |
| Write | Job worktree (builder/repairer only) | Scoped to worktree |
| Write | `forge-jobs/<job-id>/` (evidence files only) | Scripts, not agents |

### Network
| Access | Endpoint | Purpose |
|---|---|---|
| Read | `127.0.0.1:8080/v1/*` | LM Studio inference — loopback only |
| Self | `127.0.0.1:4096` | OpenCode server API |
| Approved | Package registries (npm, PyPI) | Only with explicit job approval |

### Git
| Permitted | Prohibited |
|---|---|
| `git status`, `git diff`, `git log` | `git push` |
| `git add`, `git commit` (worktree only) | `git reset --hard` (without rollback script) |
| `git stash` | `git merge` (user-approved only) |
| | `git clean -fdx` (destructive) |

---

## What Forge agents may never access

### Absolute prohibitions (no exception, no override)

1. **The Orivellum knowledge vault** (`/mnt/d/Orivellum/` or wherever it is stored). Forge is a code factory; it does not read, write, or process the user's personal knowledge library. The vault contains private notes, book extracts, and research that have nothing to do with coding tasks.

2. **Secrets and credentials.** Any file matching `.env`, `.env.*`, `secrets.*`, `credentials.*`, or any environment variable whose name contains `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `AUTH`, or `CREDENTIAL`. Forge never reads these. If a task requires an API key, it must be passed as an environment variable at runtime — Forge does not retrieve, store, or log it.

3. **The Windows user profile** (`/mnt/c/Users/`). Personal documents, browser data, and Windows application settings are out of scope.

4. **LM Studio's own configuration** (`~/.config/lmstudio/`). Forge uses LM Studio as a black-box inference endpoint. It does not configure, reconfigure, or inspect LM Studio internals.

5. **The public internet, except with explicit approval.** All AI work (plan, build, repair, review) runs offline. Web search is a separately approved capability gated per job, not automatic. No AI-generated code may open an outbound connection during build or test.

6. **Production databases and production data.** Forge tests against test databases created in the worktree. It never connects to, reads from, or writes to a production database without a recorded, user-approved exception.

7. **Other users' home directories** or any path requiring `sudo` or root privilege.

---

## Docker container policy

When a Docker Engine is in use (for test isolation):

| Rule | Detail |
|---|---|
| No `--privileged` | Containers run with default, non-privileged security profile |
| No `--net=host` | Containers have their own network namespace |
| Mounts are read-only except the worktree | `--volume /worktree:/workspace:rw`, everything else `:ro` |
| Containers are ephemeral | Removed after the gate step completes |
| No access to host's LM Studio port from within container | The model server is never exposed to containers |

Docker Engine in WSL is treated as **privileged infrastructure**, not an ordinary agent tool. The docker group on Linux grants effective root-level access. Only the gate runner script invokes Docker, never the AI agent directly.

---

## Network security

### What is exposed to the private VPN (Tailscale/WireGuard)
- The existing Orivellum Java gateway (same as before Forge)
- Nothing else

### What is NOT exposed to the VPN
- OpenCode server (`127.0.0.1:4096`) — loopback only
- LM Studio server (`127.0.0.1:8080`) — loopback only
- Any test service started by the build/gate process

### What is NOT exposed to the public internet
- Everything. Forge operates entirely offline by default.

---

## Evidence file integrity

Evidence files in `forge-jobs/<job-id>/` are written by scripts and scripts only:

| File | Written by |
|---|---|
| `task-contract.json` | Planner agent |
| `authority-inventory.json` | `inspect-a01.sh` / `inspect-a01.ps1` |
| `checkpoints.json` | `checkpoint.sh` |
| `work-ledger.ndjson` | All scripts (append-only) |
| `diff-summary.md` | `run-gates.sh` |
| `test-report.json` | `run-gates.sh` |
| `security-reports/` | `run-gates.sh` |
| `reviewer-report.md` | Reviewer agent |
| **`release-decision.json`** | **`bundle-evidence.sh` only** |
| **`evidence-manifest.sha256`** | **`bundle-evidence.sh` only** |

`release-decision.json` and `evidence-manifest.sha256` must never be hand-edited. Any manual edit invalidates the evidence chain. If they need to be regenerated, re-run `run-gates.sh` and `bundle-evidence.sh`.

---

## Model trust boundary

Models are not trusted to:
- Decide what is "complete" (gates decide)
- Approve their own output (user or gate engine approves)
- Read secrets or credentials (prohibited)
- Access paths outside the worktree (tool permissions restrict this)
- Self-modify their own prompt files (read-only for agents)
- Invoke `git push` or merge to production branches (prohibited)

The model is trusted to:
- Generate and edit code within the worktree
- Run formatters, linters, and test commands within the worktree
- Read the task contract and authority inventory
- Report what it did (observable events only, no private reasoning in evidence)

---

## Change control for this document

Changes to this document require:
1. User review and explicit approval
2. Version number increment
3. All active job policies remain governed by the version in effect when the job was created

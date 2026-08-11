---
name: Pipeline context compiler
description: Per-stage context recipes for book pipeline workers; budget honesty and acceptance-gate doctrine
---

# Pipeline context compiler (capabilities/context_compiler.py)

- Every B-stage worker gets context ONLY via `compile_context(pipeline_id, stage, db)`; `STAGE_RECIPES` declares per-source char budgets per stage.
- **Budget honesty rule:** budgets apply to the EXACT rendered block delivered to the prompt (`ctx["blocks"][source]`), hard-clipped after rendering; `context_report[source].chars == len(block)` always. Never budget an estimate of item text — labels/metadata count too.
- Workers' `_*_block()` helpers only substitute a placeholder when a block is empty; they must never re-render or re-clip.
- Registered prompt templates are rendered by `render_registered_prompt` (regex over known placeholder names), NOT `str.format` — legacy templates contain literal JSON braces that KeyError under format.
- **Acceptance gates are deterministic code, never a model:** B0 must cite real G-stage codes when a seal exists; B1 must match blueprint chapter count, then the worker deterministically fills missing seqs from scaffolded chapters (`from_blueprint: true`) so the stored outline always covers 1..N; B3's dependency graph is validated by `check_architecture_dag` (forward/self refs, cycles via Kahn, unresolvable deps) plus full blueprint coverage.
- **Why:** a gate that accepts `{}` or an empty chapters list stores a "successful" artifact that guarantees nothing — always reject missing/non-list/empty payloads explicitly (lesson from architect review of this feature).
- B1/B3 use a larger LLM completion budget (`_STAGE_MAX_TOKENS`) since they must enumerate all chapters.

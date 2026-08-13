# Release blockers — UI convergence baseline (WP0)

Frozen at the `ui-baseline-wp0` tag. These four defects are tracked as
**explicit release blockers** for the UI completion / personal lockdown
project. None may be closed silently: D1, D2, and D4 are pinned by
behavioral characterization tests in `tests/test_wp0_release_blockers.py`
that PASS while the defect exists and FAIL the moment the behavior changes —
forcing the fix to update both the test and this document. D3 is an
operational runbook item; its test only guards that this document stays
accurate about the toggle's default.

Baseline artifacts in this directory:

- `metrics.json` — route count (50), hex-literal count outside token files
  (94), gzip bundle sizes. Recollect/check with
  `uv run python scripts/ui_baseline_metrics.py {collect|check}` (needs a
  production build in `artifacts/orivellum-ui/dist/`).
- `screenshots/` — Home, Chat, Works, Library, Work detail at 320px,
  390×844 (iPhone portrait), and 1440px. Recapture with
  `CHROMIUM_BIN=$(which chromium) node artifacts/orivellum-ui/scripts/capture-baseline.mjs`
  (both dev workflows running; login key from `ORIVELLUM_LOGIN_KEY`/`SESSION_SECRET`).

---

## D1 — Failed readiness calculation must BLOCK advancement (fail-open gates)

**Status: OPEN (confirmed)**

`_check_stage_gate()` in `src/orivellum/api/routes/works.py` deliberately
fails open at every evaluation point:

- The docstring states gates are "silently skipped so a transient dependency
  failure never permanently blocks a user".
- Artifact-required check (`B0/B1/B2/B3/B6/B7`): `except Exception` → skip.
- B0→B1 doc-count check: bare `except Exception: pass`.
- Completeness fetch: `except Exception: return None  # data unavailable —
  skip gate rather than block`.
- Gap detection for B3→B4 / B16→B17: fail-open with only a warning log.

**Consequence:** any transient failure in `build_book_intelligence`, the
artifact store, or gap detection lets a pipeline advance past a gate it
would otherwise fail.

**Healthy contrast (keep):** the promote-to-book path is predicate-based and
fail-closed — `promotion_eligibility()` in
`src/orivellum/capabilities/readiness.py` plus
`db.create_book_pipeline(require_ready=True)` raising `PromotionRefused`.

**Pinned by:** `test_d1_completeness_gate_fails_open_when_readiness_raises`,
`test_d1_artifact_gate_fails_open_when_artifact_check_raises`.

## D2 — Publication gates must use ratified predicates, not percentages

**Status: OPEN (confirmed)**

`_COMPLETENESS_GATES` in `_check_stage_gate()`
(`src/orivellum/api/routes/works.py`) gates B1→B2 … B16→B17 on
`structural_pct` / `research_pct` / `content_pct` / `editorial_pct`
thresholds (0/40/60/80/50/30/80%) computed by `build_book_intelligence()` —
percentages over assumed denominators, not ratified predicates.

**Consequence:** a gate can pass because the denominator is wrong (e.g. too
few expected chapters), not because the work is actually ready.

**Pinned by:** `test_d2_stage_gates_use_percentage_thresholds`.

## D3 — Prove AI extraction on a small import before corpus-scale import

**Status: OPEN (operational runbook item)**

`ai_extraction_enabled` defaults to `"false"`
(`src/orivellum/capabilities/pipeline.py`, step 5 of `process_document`).
The toggle's behavior is already covered by
`tests/test_knowledge_harvest.py` (skipped when false, invoked when true).

**What remains:** an operational proof, not a code change — before any
corpus-scale import:

1. Enable AI extraction (System page or `ai_extraction_enabled=true`).
2. Import ONE small representative document.
3. Verify knowledge nodes appear (Work detail → Knowledge tab, or
   `GET /api/library/{doc_id}/knowledge`).
4. Only then run the corpus-scale import.

**Pinned by:** `test_d3_ai_extraction_defaults_off` (guards the default and
the gate location so this runbook stays accurate).

## D4 — Mail sending must stay bounded while the trusted-domain list is empty

**Status: OPEN (confirmed)**

`gate_send_reply()` and `gate_send_mail()` in
`src/orivellum/capabilities/shield.py` begin with `if not domains: return` —
an empty `mail_trusted_domains` setting silently disables the domain gate
AND the outbound injection screen. With `mail_steward.send_enabled=true`,
sends then bypass the boundary with no visible indication.

**Consequence:** the documented "inactive until configured" back-compat
choice means the safety boundary defaults to OFF; any bypass is silent,
violating the lockdown requirement that bypasses be explicit and visible.

**Mitigations already in place:** `send_enabled` defaults to false and every
send requires a nonce (`tests/test_mail_steward_gates.py`).

**Pinned by:** `test_d4_empty_trusted_domain_boundary_disables_send_gates`
(+ `test_d4_configured_boundary_does_refuse` proving the configured path
works).

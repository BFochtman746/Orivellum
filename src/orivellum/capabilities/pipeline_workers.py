"""Book pipeline AI stage workers — B0–B3 planning + B6/B7 review stages.

Each worker:
  1. Compiles a bounded context package from the Work's documents, knowledge,
     and prior-stage artifacts.
  2. Calls the LLM with a stage-specific prompt (from the prompt registry or
     a hardcoded default).
  3. Stores the structured result as a pipeline artifact.
  4. For B6/B7 also creates governance findings on the pipeline so the
     state-machine blocker check prevents advancing past broken stages.

Stage semantics follow ``BOOK_STAGE_LABELS`` in ``state_machine.py`` — B4 is
Chapter Extraction (handled by ``chapters.py``, no LLM worker) and B5 is
Chapter Drafting (no worker yet); the continuity and fact-check reviews run
at B6 and B7.  ``_assert_stage_alignment()`` enforces this at import time so
the two tables can never silently drift again (audit defect D-01).

Entry point: ``run_stage_worker(pipeline_id, stage, db, cfg)``
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.pipeline_workers")

# ── Stage configuration ────────────────────────────────────────────────────────

# artifact_type, prompt_slot, display label
_STAGE_CFG: dict[str, tuple[str, str, str]] = {
    "B0": ("project_brief", "pipeline.b0.brief", "Project Brief"),
    "B1": ("chapter_outline", "pipeline.b1.outline", "Chapter Outline"),
    "B2": ("research_agenda", "pipeline.b2.research", "Research Agenda"),
    "B3": ("architecture", "pipeline.b3.architecture", "Architecture"),
    "B6": ("continuity_report", "pipeline.b6.continuity", "Continuity Review"),
    "B7": ("fact_check_report", "pipeline.b7.factcheck", "Fact Check"),
}


def _assert_stage_alignment() -> None:
    """Fail loudly at import if worker stages drift from the canonical labels.

    Two invariants (audit D-01 — the off-by-two bug this prevents):
    1. Every worker stage key must be a declared B-stage.
    2. If a worker's display label IS one of the canonical stage labels, it
       must be the label of the SAME stage key — a "Continuity Review" worker
       registered under B4 (whose canonical label is "Chapter Extraction")
       is exactly the drift that shipped broken findings.
    """
    from orivellum.capabilities.state_machine import BOOK_STAGE_LABELS

    canonical_by_label = {v: k for k, v in BOOK_STAGE_LABELS.items()}
    for stage, (artifact_type, _slot, label) in _STAGE_CFG.items():
        if stage not in BOOK_STAGE_LABELS:
            raise RuntimeError(
                f"pipeline_workers._STAGE_CFG defines unknown stage {stage!r} "
                f"(artifact {artifact_type!r}) — not in BOOK_STAGE_LABELS"
            )
        owner = canonical_by_label.get(label)
        if owner is not None and owner != stage:
            raise RuntimeError(
                f"pipeline_workers._STAGE_CFG stage {stage!r} is labelled "
                f"{label!r}, but that is the canonical label of stage {owner!r} "
                f"— stage mapping has drifted (D-01)"
            )


_assert_stage_alignment()

# ── JSON extraction helper ─────────────────────────────────────────────────────


def _parse_json(text: str | None, fallback: Any = None) -> Any:
    """Extract the first JSON object or array from an LLM response."""
    if not text:
        return fallback
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Extract first {...} or [...] block
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return fallback


# ── Context compiler ──────────────────────────────────────────────────────────


def compile_stage_context(pipeline_id: str, stage: str, db: OrivellumDB) -> dict:
    """Assemble the per-stage declared context package (see context_compiler)."""
    from orivellum.capabilities.context_compiler import compile_context

    return compile_context(pipeline_id, stage, db)


# ── Default prompt templates ──────────────────────────────────────────────────


# All source material is rendered and budget-clipped by the context compiler;
# the helpers below only substitute a placeholder when a block is empty, so
# what reaches the prompt is exactly what the context_report accounted for.


def _block(ctx: dict, name: str, empty: str) -> str:
    return ctx.get("blocks", {}).get(name) or empty


def _docs_block(ctx: dict) -> str:
    return _block(ctx, "documents", "(no documents)")


def _knowledge_block(ctx: dict) -> str:
    return _block(ctx, "knowledge", "(no knowledge items)")


def _prior_block(ctx: dict) -> str:
    return _block(ctx, "prior", "(no prior stage outputs)")


def _genesis_block(ctx: dict) -> str:
    return _block(ctx, "genesis", "(no sealed origination package)")


def _canon_block(ctx: dict) -> str:
    return _block(ctx, "canon", "(no canon facts)")


def _contracts_block(ctx: dict) -> str:
    return _block(ctx, "contracts", "(no chapter contracts)")


def _chapters_block(ctx: dict) -> str:
    return _block(ctx, "chapter_text", "(no chapter text)")


# Registered prompt templates may contain literal JSON braces; str.format()
# would raise KeyError on them, so substitution is limited to the known
# placeholder names and everything else is left untouched.
_TEMPLATE_PLACEHOLDER_RE = re.compile(
    r"\{(work_title|work_description|documents|knowledge|prior_stages"
    r"|genesis|canon|contracts|chapters)\}"
)


def render_registered_prompt(template: str, values: dict[str, str]) -> str:
    return _TEMPLATE_PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


_PROMPT_SYSTEM = (
    "You are a book production AI assistant for the Orivellum platform. "
    "Always respond with valid JSON only — no prose, no markdown fences. "
    "Output exactly the schema specified in the user message."
)


def _b0_prompt(ctx: dict) -> str:
    if ctx["genesis"]["sealed"]:
        codes = ", ".join(sorted(ctx["genesis"]["artifacts"].keys()))
        return f"""Work: {ctx["work_title"]}
Description: {ctx["work_description"] or "(none)"}

SEALED ORIGINATION PACKAGE (GENESIS). The project brief must be DERIVED from
these sealed artifacts — do not re-imagine the premise, scope, or themes.
Every field must trace to the artifacts below.

{_genesis_block(ctx)}

Canon facts:
{_canon_block(ctx)}

Produce a JSON project brief with these exact keys:
{{
  "title": "final book title (from the seal)",
  "premise": "2-3 sentence description derived from the sealed premise (G1)",
  "audience": "primary readership in one sentence",
  "scope": "what the book covers and does NOT cover, per the sealed package",
  "goals": ["goal 1", "goal 2", "goal 3"],
  "key_themes": ["theme 1", "theme 2", "theme 3"],
  "source_citations": ["the G-stage codes this brief derives from — choose from: {codes}"]
}}"""
    return f"""Work: {ctx["work_title"]}
Description: {ctx["work_description"] or "(none)"}

Documents ({len(ctx["documents"])}):
{_docs_block(ctx)}

Knowledge highlights:
{_knowledge_block(ctx)}

Produce a JSON project brief with these exact keys:
{{
  "title": "final book title",
  "premise": "2-3 sentence description of the book's core argument or story",
  "audience": "primary readership in one sentence",
  "scope": "what the book covers and does NOT cover",
  "goals": ["goal 1", "goal 2", "goal 3"],
  "key_themes": ["theme 1", "theme 2", "theme 3"]
}}"""


def _b1_prompt(ctx: dict) -> str:
    brief = ctx["prior_artifacts"].get("B0", {})
    bp_count = ctx["genesis"]["blueprint_chapter_count"]
    if bp_count:
        return f"""Work: {ctx["work_title"]}
Project brief: {json.dumps(brief, ensure_ascii=False)[:400]}

SEALED BLUEPRINT (GENESIS). The blueprint declares EXACTLY {bp_count} chapters.
You must RECONCILE the outline to the blueprint — total_chapters MUST be
{bp_count}. Do not invent a different chapter count. If you believe material
suggests a different structure, report that as a delta, never as a changed count.

{_genesis_block(ctx)}

Chapter contracts (if scaffolded):
{_contracts_block(ctx)}

Produce a JSON chapter outline:
{{
  "total_chapters": {bp_count},
  "chapters": [
    {{
      "seq": 1,
      "title": "working chapter title",
      "description": "1-2 sentence description of what this chapter covers",
      "key_questions": ["question the chapter must answer"]
    }}
  ],
  "blueprint_deltas": ["where source material diverges from the blueprint — empty if none"]
}}
List as many chapter entries as fit; seq values must be between 1 and {bp_count}."""
    return f"""Work: {ctx["work_title"]}
Project brief: {json.dumps(brief, ensure_ascii=False)[:400]}

Documents:
{_docs_block(ctx)}

Produce a JSON chapter outline:
{{
  "total_chapters": <integer>,
  "chapters": [
    {{
      "seq": 1,
      "title": "working chapter title",
      "description": "1-2 sentence description of what this chapter covers",
      "key_questions": ["question the chapter must answer"]
    }}
  ]
}}"""


def _b2_prompt(ctx: dict) -> str:
    outline = ctx["prior_artifacts"].get("B1", {})
    return f"""Work: {ctx["work_title"]}
Chapter outline: {json.dumps(outline, ensure_ascii=False)[:600]}

Canon facts (ground truth already established):
{_canon_block(ctx)}

Existing knowledge:
{_knowledge_block(ctx)}

Identify open research questions and knowledge gaps. Produce JSON:
{{
  "open_questions": [
    {{"question": "...", "chapter_hint": "chapter title or number", "priority": "high|medium|low"}}
  ],
  "knowledge_gaps": [
    {{"topic": "...", "description": "what is missing", "severity": "high|medium|low"}}
  ],
  "coverage_assessment": "one-paragraph assessment of overall research readiness"
}}"""


def _b3_prompt(ctx: dict) -> str:
    outline = ctx["prior_artifacts"].get("B1", {})
    research = ctx["prior_artifacts"].get("B2", {})
    return f"""Work: {ctx["work_title"]}
Chapter outline: {json.dumps(outline, ensure_ascii=False)[:400]}
Research agenda summary: {json.dumps(research, ensure_ascii=False)[:400]}

Blueprint (GENESIS, if sealed):
{_genesis_block(ctx)}

Chapter contracts:
{_contracts_block(ctx)}

Design the book's structural architecture. Every chapter's "depends_on" list
may reference ONLY earlier chapters (by seq number). No forward references,
no cycles — the output is rejected automatically otherwise. Produce JSON:
{{
  "arc_type": "e.g. chronological|thematic|problem-solution|case-study",
  "structure": "one paragraph describing the narrative arc and flow",
  "chapters": [
    {{
      "seq": 1,
      "title": "chapter title",
      "role": "what structural role this chapter plays",
      "themes": ["theme 1"],
      "depends_on": []
    }}
  ],
  "rationale": "why this structure serves the work's goals"
}}"""


def _b6_prompt(ctx: dict) -> str:
    arch = ctx["prior_artifacts"].get("B3", {})
    return f"""Work: {ctx["work_title"]}
Architecture: {json.dumps(arch, ensure_ascii=False)[:800]}

Chapter contracts:
{_contracts_block(ctx)}

Chapter prose (excerpts):
{_chapters_block(ctx)}

Check whether the chapters and architecture are internally consistent. Specifically:
- Does any chapter reference knowledge or conclusions that are only established in a later chapter?
- Are there circular dependencies?
- Are there any chapters whose 'depends_on' references a later sequence number?

Produce JSON:
{{
  "is_consistent": true|false,
  "issues": [
    {{
      "chapter_a": "chapter title",
      "chapter_b": "chapter title it depends on",
      "description": "what is inconsistent and why it matters",
      "severity": "high|medium|low"
    }}
  ],
  "summary": "one-sentence overall verdict"
}}"""


def _b7_prompt(ctx: dict) -> str:
    arch = ctx["prior_artifacts"].get("B3", {})
    return f"""Work: {ctx["work_title"]}
Architecture: {json.dumps(arch, ensure_ascii=False)[:600]}

Canon facts (the record — HISTORICAL facts carry sources):
{_canon_block(ctx)}

Chapter prose (excerpts):
{_chapters_block(ctx)}

Knowledge base:
{_knowledge_block(ctx)}

Cross-check the factual claims in the prose and architecture against the canon
record and knowledge base. Flag any claim, theme, or stated role that is NOT
supported by canon facts or existing knowledge items.

Produce JSON:
{{
  "verified_count": <integer>,
  "unverified_claims": [
    {{
      "claim": "the factual claim or theme",
      "chapter": "chapter title",
      "reason": "why it is unverified or lacks evidence",
      "severity": "high|medium|low"
    }}
  ],
  "overall_confidence": "high|medium|low",
  "summary": "one-sentence verdict"
}}"""


_PROMPT_BUILDERS = {
    "B0": _b0_prompt,
    "B1": _b1_prompt,
    "B2": _b2_prompt,
    "B3": _b3_prompt,
    "B6": _b6_prompt,
    "B7": _b7_prompt,
}


# ── Deterministic acceptance checks ───────────────────────────────────────────


def _index_chapters(
    chapters: list[dict], problems: list[str]
) -> tuple[dict[int, dict], dict[str, int]]:
    """Index B3 chapters by seq and title, collecting seq problems."""
    by_seq: dict[int, dict] = {}
    by_title: dict[str, int] = {}
    for ch in chapters:
        try:
            seq = int(ch.get("seq"))
        except (TypeError, ValueError):
            problems.append(f"Chapter with invalid seq: {ch.get('seq')!r}")
            continue
        if seq < 1:
            problems.append(f"Chapter seq must be >= 1, got {seq}")
            continue
        if seq in by_seq:
            problems.append(f"Duplicate chapter seq {seq}")
            continue
        by_seq[seq] = ch
        title = str(ch.get("title") or "").strip().lower()
        if title:
            by_title[title] = seq
    return by_seq, by_title


def _resolve_dep(dep: Any, by_title: dict[str, int]) -> int | None:
    """Resolve a depends_on entry (seq int, digit-string, or title) to a seq."""
    if isinstance(dep, bool):
        return None
    if isinstance(dep, int):
        return dep
    if isinstance(dep, str):
        s = dep.strip()
        if s.lstrip("-").isdigit():
            return int(s)
        return by_title.get(s.lower())
    return None


def _collect_edges(
    by_seq: dict[int, dict], by_title: dict[str, int], problems: list[str]
) -> list[tuple[int, int]]:
    """Resolve depends_on into (dep_seq, seq) edges, flagging bad references."""
    edges: list[tuple[int, int]] = []
    for seq, ch in by_seq.items():
        deps = ch.get("depends_on") or []
        if not isinstance(deps, list):
            problems.append(f"Chapter {seq}: depends_on must be a list")
            continue
        for dep in deps:
            dep_seq = _resolve_dep(dep, by_title)
            if dep_seq is None or dep_seq not in by_seq:
                problems.append(f"Chapter {seq}: unresolvable dependency {dep!r}")
            elif dep_seq >= seq:
                kind = "self" if dep_seq == seq else "forward"
                problems.append(f"Chapter {seq}: {kind} reference to chapter {dep_seq}")
            else:
                edges.append((dep_seq, seq))
    return edges


def _kahn_cycle_check(by_seq: dict[int, dict], edges: list[tuple[int, int]]) -> str | None:
    """Return a cycle problem string, or None if the resolved edges are acyclic."""
    indeg = dict.fromkeys(by_seq, 0)
    adj: dict[int, list[int]] = {s: [] for s in by_seq}
    for dep, seq in edges:
        adj[dep].append(seq)
        indeg[seq] += 1
    queue = [s for s, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if seen != len(by_seq):
        cyclic = sorted(s for s, d in indeg.items() if d > 0)
        return f"Dependency cycle involving chapters {cyclic}"
    return None


def check_architecture_dag(chapters: list[dict]) -> list[str]:
    """Deterministically validate B3 chapter dependencies — never ask a model.

    Returns a list of problems (empty = valid):
    - duplicate or non-positive seq numbers
    - unresolvable ``depends_on`` references
    - forward or self references (a chapter may depend only on EARLIER seqs)
    - cycles (Kahn's algorithm, belt-and-braces on top of the forward check)
    """
    problems: list[str] = []
    by_seq, by_title = _index_chapters(chapters, problems)
    edges = _collect_edges(by_seq, by_title, problems)
    cycle = _kahn_cycle_check(by_seq, edges)
    if cycle:
        problems.append(cycle)
    return problems


def _accept_b0(content: dict, ctx: dict) -> None:
    """B0 must derive the brief FROM the seal, citing real G-stage artifacts."""
    if not ctx["genesis"]["sealed"]:
        return
    available = set(ctx["genesis"]["artifacts"].keys())
    cites = content.get("source_citations")
    if not isinstance(cites, list) or not cites:
        raise RuntimeError(
            "B0 rejected: sealed origination package present but the brief "
            "cites no G-stage artifacts (source_citations missing/empty)"
        )
    bogus = [c for c in cites if not isinstance(c, str) or c.upper() not in available]
    if bogus:
        raise RuntimeError(
            f"B0 rejected: brief cites artifacts not in the sealed package: {bogus} "
            f"(available: {sorted(available)})"
        )
    content["source_citations"] = [c.upper() for c in cites]


def _validated_chapter_entries(content: dict, bp_count: int, stage: str) -> dict[int, dict]:
    """Strictly validate a stage's chapters payload; return entries keyed by seq.

    Rejects missing/non-list/empty chapters, non-dict entries, invalid or
    out-of-range seqs, and duplicates — an empty or malformed payload must
    never be stored as a successful stage.
    """
    raw = content.get("chapters")
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(f"{stage} rejected: chapters must be a non-empty list")
    seen: dict[int, dict] = {}
    for ch in raw:
        if not isinstance(ch, dict):
            raise RuntimeError(f"{stage} rejected: chapter entry is not an object: {ch!r}")
        seq = ch.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1 or seq > bp_count:
            raise RuntimeError(
                f"{stage} rejected: chapter seq {seq!r} outside blueprint range 1..{bp_count}"
            )
        if seq in seen:
            raise RuntimeError(f"{stage} rejected: duplicate chapter seq {seq}")
        seen[seq] = ch
    return seen


def _accept_b1(content: dict, ctx: dict) -> None:
    """B1 must reconcile to the blueprint chapter count, never invent one.

    Reconciliation is deterministic: after validating the model's entries,
    the worker fills any missing seqs from the scaffolded chapter records so
    the stored outline ALWAYS covers exactly 1..blueprint_count.
    """
    bp_count = ctx["genesis"]["blueprint_chapter_count"]
    if not bp_count:
        return
    total = content.get("total_chapters")
    if total != bp_count:
        raise RuntimeError(
            f"B1 rejected: outline invented a chapter count ({total!r}) that "
            f"differs from the sealed blueprint ({bp_count}) — reconcile and "
            f"report deltas instead"
        )
    if not isinstance(content.get("blueprint_deltas"), list):
        raise RuntimeError(
            "B1 rejected: blueprint present but blueprint_deltas is missing "
            "(must be a list, empty when there are no divergences)"
        )
    seen = _validated_chapter_entries(content, bp_count, "B1")
    titles = {c["seq"]: c["title"] for c in ctx.get("chapter_contracts", [])}
    full: list[dict] = []
    for seq in range(1, bp_count + 1):
        if seq in seen:
            full.append(seen[seq])
        else:
            full.append(
                {
                    "seq": seq,
                    "title": titles.get(seq) or f"Chapter {seq}",
                    "from_blueprint": True,
                }
            )
    content["chapters"] = full


def _accept_b3(content: dict, ctx: dict) -> None:
    """B3 dependency output is validated by code, never by a model."""
    raw = content.get("chapters")
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("B3 rejected: chapters must be a non-empty list")
    problems = check_architecture_dag(raw)
    bp_count = ctx["genesis"]["blueprint_chapter_count"]
    if bp_count and not problems:
        seqs = {ch.get("seq") for ch in raw if isinstance(ch, dict)}
        missing = sorted(set(range(1, bp_count + 1)) - seqs)
        if missing:
            head = ", ".join(str(m) for m in missing[:10])
            extra = f" (+{len(missing) - 10} more)" if len(missing) > 10 else ""
            problems.append(
                f"architecture covers {len(seqs)} of {bp_count} blueprint "
                f"chapters; missing seqs: {head}{extra}"
            )
        extra_seqs = sorted(s for s in seqs if isinstance(s, int) and s > bp_count)
        if extra_seqs:
            problems.append(f"architecture has chapters beyond the blueprint: {extra_seqs[:10]}")
    if problems:
        joined = "; ".join(problems[:10])
        extra = f" (+{len(problems) - 10} more)" if len(problems) > 10 else ""
        raise RuntimeError(f"B3 rejected — dependency graph invalid: {joined}{extra}")


_ACCEPTANCE_CHECKS = {
    "B0": _accept_b0,
    "B1": _accept_b1,
    "B3": _accept_b3,
}


# ── Stage workers ─────────────────────────────────────────────────────────────


# B1 must be able to list every blueprint chapter and B3 the full dependency
# graph — those stages get a larger completion budget than the default.
_STAGE_MAX_TOKENS: dict[str, int] = {"B1": 6000, "B3": 6000}


def _call_llm(
    user_prompt: str,
    db: OrivellumDB,
    cfg: OrivellumConfig,
    purpose: str,
    timeout: float = 45.0,
    max_tokens: int = 1800,
) -> dict | None:
    """Call the LLM and return parsed JSON, or None on failure."""
    from orivellum.capabilities.llm import llm_call

    result = llm_call(
        [
            {"role": "system", "content": _PROMPT_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        cfg=cfg,
        db=db,
        purpose=purpose,
        timeout=timeout,
        temperature=0.3,
        max_tokens=max_tokens,
    )
    if not result.ok or not result.text:
        raise RuntimeError(result.error or "LLM returned no text")
    parsed = _parse_json(result.text)
    if parsed is None:
        raise RuntimeError(f"LLM response was not valid JSON: {result.text[:200]}")
    return parsed


def _post_b6(pipeline_id: str, content: dict, db: OrivellumDB) -> None:
    """Create high-severity findings for each B6 continuity issue."""
    for issue in content.get("issues", []):
        sev = issue.get("severity", "medium").lower()
        if sev not in ("high", "critical", "medium", "low"):
            sev = "medium"
        db.create_finding(
            object_id=pipeline_id,
            object_type="book_pipeline",
            description=(
                f"[B6 Continuity] {issue.get('chapter_a', '?')} ↔ "
                f"{issue.get('chapter_b', '?')}: {issue.get('description', '')[:200]}"
            ),
            kind="continuity",
            severity=sev,
        )


def _post_b7(pipeline_id: str, content: dict, db: OrivellumDB) -> None:
    """Create findings for each B7 unverified claim."""
    for claim in content.get("unverified_claims", []):
        sev = claim.get("severity", "medium").lower()
        if sev not in ("high", "critical", "medium", "low"):
            sev = "medium"
        db.create_finding(
            object_id=pipeline_id,
            object_type="book_pipeline",
            description=(
                f"[B7 Fact Check] {claim.get('chapter', '?')}: "
                f"{claim.get('claim', '')[:150]} — {claim.get('reason', '')[:150]}"
            ),
            kind="fact_check",
            severity=sev,
        )


# ── Main entry point ─────────────────────────────────────────────────────────


def run_stage_worker(
    pipeline_id: str,
    stage: str,
    db: OrivellumDB,
    cfg: OrivellumConfig,
) -> dict:
    """Compile context, call LLM, store artifact, return artifact dict.

    Raises RuntimeError on any failure (caller stores status='failed').
    """
    if stage not in _STAGE_CFG:
        raise ValueError(f"No worker defined for stage {stage!r}")

    artifact_type, prompt_slot, label = _STAGE_CFG[stage]

    # Mark as running immediately so the UI can show a spinner
    db.upsert_pipeline_artifact(pipeline_id, stage, artifact_type, {}, status="running")

    try:
        ctx = compile_stage_context(pipeline_id, stage, db)

        # Prefer prompt from registry, fall back to built-in template
        registered = db.get_active_prompt(prompt_slot)
        if registered:
            user_prompt = render_registered_prompt(
                registered,
                {
                    "work_title": ctx["work_title"],
                    "work_description": ctx["work_description"],
                    "documents": _docs_block(ctx),
                    "knowledge": _knowledge_block(ctx),
                    "prior_stages": _prior_block(ctx),
                    "genesis": _genesis_block(ctx),
                    "canon": _canon_block(ctx),
                    "contracts": _contracts_block(ctx),
                    "chapters": _chapters_block(ctx),
                },
            )
        else:
            builder = _PROMPT_BUILDERS[stage]
            user_prompt = builder(ctx)

        logger.info("Running pipeline worker stage=%s pipeline=%s", stage, pipeline_id[:8])
        content = _call_llm(
            user_prompt,
            db,
            cfg,
            purpose=f"pipeline.{stage.lower()}.worker",
            max_tokens=_STAGE_MAX_TOKENS.get(stage, 1800),
        )

        # Deterministic acceptance checks — a stage whose output violates the
        # sealed blueprint/seal contract FAILS; we never ask a model to judge.
        acceptor = _ACCEPTANCE_CHECKS.get(stage)
        if acceptor:
            acceptor(content, ctx)

        # Post-processing: create findings for continuity / fact-check stages
        if stage == "B6":
            _post_b6(pipeline_id, content, db)
        elif stage == "B7":
            _post_b7(pipeline_id, content, db)

        db.upsert_pipeline_artifact(pipeline_id, stage, artifact_type, content, status="done")
        logger.info("Pipeline worker done stage=%s pipeline=%s", stage, pipeline_id[:8])
        return content

    except Exception as exc:
        error_msg = str(exc)[:500]
        logger.warning("Pipeline worker failed stage=%s: %s", stage, error_msg)
        db.upsert_pipeline_artifact(
            pipeline_id, stage, artifact_type, {}, status="failed", error=error_msg
        )
        raise

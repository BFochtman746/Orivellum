"""Book pipeline AI stage workers — B0 through B5.

Each worker:
  1. Compiles a bounded context package from the Work's documents, knowledge,
     and prior-stage artifacts.
  2. Calls the LLM with a stage-specific prompt (from the prompt registry or
     a hardcoded default).
  3. Stores the structured result as a pipeline artifact.
  4. For B4/B5 also creates governance findings on the pipeline so the
     state-machine blocker check prevents advancing past broken stages.

Entry point: ``run_stage_worker(pipeline_id, stage, db, cfg)``
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB
    from orivellum.configuration.config import OrivellumConfig

logger = logging.getLogger("orivellum.pipeline_workers")

# ── Stage configuration ────────────────────────────────────────────────────────

# artifact_type, prompt_slot, display label
_STAGE_CFG: dict[str, tuple[str, str, str]] = {
    "B0": ("project_brief",    "pipeline.b0.brief",        "Project Brief"),
    "B1": ("chapter_outline",  "pipeline.b1.outline",      "Chapter Outline"),
    "B2": ("research_agenda",  "pipeline.b2.research",     "Research Agenda"),
    "B3": ("architecture",     "pipeline.b3.architecture", "Architecture"),
    "B4": ("continuity_report","pipeline.b4.continuity",   "Continuity Review"),
    "B5": ("fact_check_report","pipeline.b5.factcheck",    "Fact Check"),
}

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

def compile_stage_context(pipeline_id: str, stage: str, db: "OrivellumDB") -> dict:
    """Assemble a bounded context package for a stage worker.

    Returns a dict with:
      work_id, work_title, work_description,
      documents (list of {title, summary}),
      knowledge  (list of {kind, text, subject}),
      prior_artifacts (dict of stage → content)
    """
    with db._lock:
        # Work info via pipeline
        row = db._conn.execute(
            """SELECT bp.work_id, w.title, w.description
               FROM book_pipelines bp JOIN works w ON w.id=bp.work_id
               WHERE bp.id=?""",
            (pipeline_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"Pipeline {pipeline_id!r} not found")
    work_id = row["work_id"]
    work_title = row["title"] or "Untitled"
    work_desc = row["description"] or ""

    # Top-5 active documents with their extracted text as summary
    with db._lock:
        doc_rows = db._conn.execute(
            """SELECT d.title, d.source,
                      substr(coalesce(d.extracted_text,''), 1, 600) as summary
               FROM documents d JOIN objects o ON o.id=d.id
               WHERE d.work_id=? AND o.lifecycle != 'deleted'
               ORDER BY d.created_at DESC LIMIT 5""",
            (work_id,),
        ).fetchall()
    documents = []
    for r in doc_rows:
        title = r["title"] or (r["source"] or "").split("/")[-1] or "Document"
        documents.append({"title": title, "summary": (r["summary"] or "").strip()})

    # Top-20 approved/auto knowledge items
    with db._lock:
        k_rows = db._conn.execute(
            """SELECT kind, text, subject FROM knowledge
               WHERE work_id=? AND review_status != 'rejected'
               ORDER BY confidence DESC LIMIT 20""",
            (work_id,),
        ).fetchall()
    knowledge = [{"kind": r["kind"], "text": r["text"],
                  "subject": r["subject"] or ""} for r in k_rows]

    # Prior artifacts for all stages before this one
    stage_order = ["B0", "B1", "B2", "B3", "B4", "B5"]
    prior_artifacts: dict[str, Any] = {}
    if stage in stage_order:
        idx = stage_order.index(stage)
        for s in stage_order[:idx]:
            art = db.get_pipeline_artifact(pipeline_id, s)
            if art and art.get("status") == "done":
                prior_artifacts[s] = art["content"]

    return {
        "work_id": work_id,
        "work_title": work_title,
        "work_description": work_desc,
        "documents": documents,
        "knowledge": knowledge,
        "prior_artifacts": prior_artifacts,
    }


# ── Default prompt templates ──────────────────────────────────────────────────

def _docs_block(ctx: dict) -> str:
    lines = []
    for i, d in enumerate(ctx["documents"], 1):
        summary = d["summary"][:400].replace("\n", " ") if d["summary"] else "(no text extracted)"
        lines.append(f"{i}. {d['title']}: {summary}")
    return "\n".join(lines) if lines else "(no documents)"


def _knowledge_block(ctx: dict) -> str:
    lines = []
    for k in ctx["knowledge"][:15]:
        subj = f"[{k['subject']}] " if k["subject"] else ""
        lines.append(f"- {k['kind'].upper()}: {subj}{k['text'][:200]}")
    return "\n".join(lines) if lines else "(no knowledge items)"


def _prior_block(ctx: dict) -> str:
    if not ctx["prior_artifacts"]:
        return "(no prior stage outputs)"
    parts = []
    for stage, content in ctx["prior_artifacts"].items():
        parts.append(f"[{stage}] " + json.dumps(content, ensure_ascii=False)[:600])
    return "\n\n".join(parts)


_PROMPT_SYSTEM = (
    "You are a book production AI assistant for the Orivellum platform. "
    "Always respond with valid JSON only — no prose, no markdown fences. "
    "Output exactly the schema specified in the user message."
)


def _b0_prompt(ctx: dict) -> str:
    return f"""Work: {ctx['work_title']}
Description: {ctx['work_description'] or '(none)'}

Documents ({len(ctx['documents'])}):
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
    return f"""Work: {ctx['work_title']}
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
    return f"""Work: {ctx['work_title']}
Chapter outline: {json.dumps(outline, ensure_ascii=False)[:600]}

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
    return f"""Work: {ctx['work_title']}
Chapter outline: {json.dumps(outline, ensure_ascii=False)[:400]}
Research agenda summary: {json.dumps(research, ensure_ascii=False)[:400]}

Design the book's structural architecture. Produce JSON:
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


def _b4_prompt(ctx: dict) -> str:
    arch = ctx["prior_artifacts"].get("B3", {})
    return f"""Work: {ctx['work_title']}
Architecture: {json.dumps(arch, ensure_ascii=False)[:800]}

Check whether the chapter architecture is internally consistent. Specifically:
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


def _b5_prompt(ctx: dict) -> str:
    arch = ctx["prior_artifacts"].get("B3", {})
    return f"""Work: {ctx['work_title']}
Architecture: {json.dumps(arch, ensure_ascii=False)[:600]}

Knowledge base:
{_knowledge_block(ctx)}

Cross-check the factual claims implied by the architecture against the knowledge base.
Flag any chapter themes or stated roles that are NOT supported by existing knowledge items.

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
    "B4": _b4_prompt,
    "B5": _b5_prompt,
}


# ── Stage workers ─────────────────────────────────────────────────────────────

def _call_llm(user_prompt: str, db: "OrivellumDB", cfg: "OrivellumConfig",
              purpose: str, timeout: float = 45.0) -> dict | None:
    """Call the LLM and return parsed JSON, or None on failure."""
    from orivellum.capabilities.llm import llm_call
    result = llm_call(
        [
            {"role": "system", "content": _PROMPT_SYSTEM},
            {"role": "user",   "content": user_prompt},
        ],
        cfg=cfg,
        db=db,
        purpose=purpose,
        timeout=timeout,
        temperature=0.3,
        max_tokens=1800,
    )
    if not result.ok or not result.text:
        raise RuntimeError(result.error or "LLM returned no text")
    parsed = _parse_json(result.text)
    if parsed is None:
        raise RuntimeError(f"LLM response was not valid JSON: {result.text[:200]}")
    return parsed


def _post_b4(pipeline_id: str, content: dict, db: "OrivellumDB") -> None:
    """Create high-severity findings for each B4 continuity issue."""
    for issue in content.get("issues", []):
        sev = issue.get("severity", "medium").lower()
        if sev not in ("high", "critical", "medium", "low"):
            sev = "medium"
        db.create_finding(
            object_id=pipeline_id,
            object_type="book_pipeline",
            description=(
                f"[B4 Continuity] {issue.get('chapter_a','?')} ↔ "
                f"{issue.get('chapter_b','?')}: {issue.get('description','')[:200]}"
            ),
            kind="continuity",
            severity=sev,
        )


def _post_b5(pipeline_id: str, content: dict, db: "OrivellumDB") -> None:
    """Create findings for each B5 unverified claim."""
    for claim in content.get("unverified_claims", []):
        sev = claim.get("severity", "medium").lower()
        if sev not in ("high", "critical", "medium", "low"):
            sev = "medium"
        db.create_finding(
            object_id=pipeline_id,
            object_type="book_pipeline",
            description=(
                f"[B5 Fact Check] {claim.get('chapter','?')}: "
                f"{claim.get('claim','')[:150]} — {claim.get('reason','')[:150]}"
            ),
            kind="fact_check",
            severity=sev,
        )


# ── Main entry point ─────────────────────────────────────────────────────────

def run_stage_worker(
    pipeline_id: str,
    stage: str,
    db: "OrivellumDB",
    cfg: "OrivellumConfig",
) -> dict:
    """Compile context, call LLM, store artifact, return artifact dict.

    Raises RuntimeError on any failure (caller stores status='failed').
    """
    if stage not in _STAGE_CFG:
        raise ValueError(f"No worker defined for stage {stage!r}")

    artifact_type, prompt_slot, label = _STAGE_CFG[stage]

    # Mark as running immediately so the UI can show a spinner
    db.upsert_pipeline_artifact(
        pipeline_id, stage, artifact_type, {}, status="running"
    )

    try:
        ctx = compile_stage_context(pipeline_id, stage, db)

        # Prefer prompt from registry, fall back to built-in template
        registered = db.get_active_prompt(prompt_slot)
        if registered:
            user_prompt = registered.format(
                work_title=ctx["work_title"],
                work_description=ctx["work_description"],
                documents=_docs_block(ctx),
                knowledge=_knowledge_block(ctx),
                prior_stages=_prior_block(ctx),
            )
        else:
            builder = _PROMPT_BUILDERS[stage]
            user_prompt = builder(ctx)

        logger.info("Running pipeline worker stage=%s pipeline=%s", stage, pipeline_id[:8])
        content = _call_llm(user_prompt, db, cfg, purpose=f"pipeline.{stage.lower()}.worker")

        # Post-processing: create findings for continuity / fact-check stages
        if stage == "B4":
            _post_b4(pipeline_id, content, db)
        elif stage == "B5":
            _post_b5(pipeline_id, content, db)

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

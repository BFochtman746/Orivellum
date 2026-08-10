"""Topic profile generation via LLM.

For each topic cluster that has no profile yet, fetches the top-10 chunk
excerpts from its member documents and asks the LLM to produce:

  • what_it_is  — one plain-English sentence describing what the cluster is
  • purpose     — one sentence on why this material matters / how it is used
  • connected   — up to 3 short strings naming related concepts / themes
  • gaps        — up to 3 short strings naming obvious missing material

Results are upserted into ``topic_profiles``.  The function is idempotent:
topics that already have a profile are skipped unless ``force=True``.

Gate: requires ``ai_extraction_enabled=true``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.topic_profile")

_MAX_TOPICS_PER_RUN = 20  # cap so a single pass is bounded
_EXCERPT_CHUNKS = 10  # chunks to sample per topic
_EXCERPT_CHARS = 300  # max chars per chunk excerpt in the prompt
_LLM_TIMEOUT = 45  # seconds per LLM call


# ── DB helpers ────────────────────────────────────────────────────────────────


def _topics_needing_profiles(db: OrivellumDB, limit: int, force: bool) -> list[dict]:
    """Return topics that have no profile row (or all topics if force=True)."""
    with db._lock:
        if force:
            rows = db._conn.execute(
                "SELECT id, name FROM topics ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = db._conn.execute(
                """SELECT t.id, t.name
                   FROM topics t
                   LEFT JOIN topic_profiles tp ON tp.topic_id = t.id
                   WHERE tp.topic_id IS NULL
                   ORDER BY t.created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def _sample_chunks(db: OrivellumDB, topic_id: str, n: int) -> list[str]:
    """Return up to *n* chunk text excerpts from documents in a topic."""
    with db._lock:
        rows = db._conn.execute(
            """SELECT c.text
               FROM topic_members tm
               JOIN chunks c ON c.doc_id = tm.object_id
               WHERE tm.topic_id = ?
                 AND tm.object_type = 'document'
                 AND c.text IS NOT NULL
                 AND length(trim(c.text)) > 40
               ORDER BY RANDOM()
               LIMIT ?""",
            (topic_id, n),
        ).fetchall()
    return [r["text"] for r in rows]


def _upsert_profile(
    db: OrivellumDB,
    topic_id: str,
    what_it_is: str,
    purpose: str,
    connected: list[str],
    gaps: list[str],
) -> None:
    now = datetime.now(UTC).isoformat()
    with db._lock:
        db._conn.execute(
            """INSERT INTO topic_profiles
                   (topic_id, what_it_is, purpose, connected, gaps, generated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(topic_id) DO UPDATE SET
                   what_it_is   = excluded.what_it_is,
                   purpose      = excluded.purpose,
                   connected    = excluded.connected,
                   gaps         = excluded.gaps,
                   generated_at = excluded.generated_at""",
            (topic_id, what_it_is, purpose, json.dumps(connected), json.dumps(gaps), now),
        )
        db._conn.commit()


# ── LLM call ─────────────────────────────────────────────────────────────────

_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _parse_llm_json(text: str) -> dict:
    """Extract the first JSON object from an LLM reply."""
    # Try fenced code block first
    m = _JSON_BLOCK.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try bare JSON anywhere in the text
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return {}


def _build_prompt(topic_name: str, excerpts: list[str]) -> list[dict]:
    excerpt_block = "\n\n".join(
        f"[Excerpt {i + 1}]\n{e[:_EXCERPT_CHARS]}" for i, e in enumerate(excerpts)
    )
    system = (
        "You are a librarian helping users understand clusters of related documents. "
        "Given a topic cluster name and sample text excerpts from its documents, "
        "produce a concise plain-English profile in JSON.\n\n"
        "Return ONLY a JSON object with these exact keys:\n"
        '  "what_it_is"  — one sentence (≤20 words) describing what this cluster is\n'
        '  "purpose"     — one sentence (≤20 words) on why this material matters\n'
        '  "connected"   — list of 1–3 short strings naming related themes\n'
        '  "gaps"        — list of 1–3 short strings naming obvious missing topics\n\n'
        "No markdown, no extra keys, no explanation."
    )
    user = (
        f'Topic cluster: "{topic_name}"\n\n'
        f"Sample excerpts:\n{excerpt_block}\n\n"
        "Respond with only the JSON object."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ── Public entry point ────────────────────────────────────────────────────────


def generate_topic_profiles(
    db: OrivellumDB,
    cfg: OrivellumConfig | None = None,
    *,
    max_topics: int = _MAX_TOPICS_PER_RUN,
    force: bool = False,
) -> dict:
    """Generate plain-English profiles for topics that have none yet.

    Args:
        db:         OrivellumDB instance.
        cfg:        OrivellumConfig (provides base_url / model).  If None the
                    function reads them from db settings (best-effort).
        max_topics: Maximum number of topics to process in one call.
        force:      Re-generate profiles for topics that already have one.

    Returns:
        {"generated": int, "skipped": int, "errors": int}
    """
    # Hard gate: respect the global AI opt-in even if the caller passes force=True.
    # This prevents document excerpts from being sent to the LLM when the user has
    # disabled AI extraction, regardless of how the function is called.
    if db.get_setting("ai_extraction_enabled", "false").lower() != "true":
        logger.info("topic_profile: skipped — ai_extraction_enabled is not true")
        return {"generated": 0, "skipped": 0, "errors": 0}

    from orivellum.capabilities.llm import llm_call

    generated = skipped = errors = 0

    topics = _topics_needing_profiles(db, max_topics, force)
    if not topics:
        logger.info("topic_profile: no topics need profiles")
        return {"generated": 0, "skipped": 0, "errors": 0}

    for topic in topics:
        topic_id = topic["id"]
        topic_name = topic["name"]

        excerpts = _sample_chunks(db, topic_id, _EXCERPT_CHUNKS)
        if not excerpts:
            logger.debug("topic_profile: topic %s has no chunks — skipping", topic_id[:8])
            skipped += 1
            continue

        messages = _build_prompt(topic_name, excerpts)
        result = llm_call(
            messages,
            cfg=cfg,
            db=db,
            purpose="topic_profile",
            timeout=_LLM_TIMEOUT,
            temperature=0.3,
            max_tokens=256,
        )

        if not result.ok or not result.text:
            logger.warning(
                "topic_profile: LLM call failed for topic %s: %s",
                topic_id[:8],
                result.error,
            )
            errors += 1
            continue

        parsed = _parse_llm_json(result.text)
        what_it_is = str(parsed.get("what_it_is") or "").strip()[:400]
        purpose = str(parsed.get("purpose") or "").strip()[:400]
        connected = [str(x)[:100] for x in (parsed.get("connected") or []) if x][:3]
        gaps = [str(x)[:100] for x in (parsed.get("gaps") or []) if x][:3]

        if not what_it_is:
            logger.warning(
                "topic_profile: empty what_it_is from LLM for topic %s — raw: %r",
                topic_id[:8],
                result.text[:200],
            )
            errors += 1
            continue

        try:
            _upsert_profile(db, topic_id, what_it_is, purpose, connected, gaps)
            generated += 1
            logger.info(
                "topic_profile: generated profile for '%s': %s",
                topic_name[:40],
                what_it_is[:60],
            )
        except Exception as exc:
            logger.warning("topic_profile: DB write failed for %s: %s", topic_id[:8], exc)
            errors += 1

    logger.info(
        "topic_profile complete: generated=%d skipped=%d errors=%d",
        generated,
        skipped,
        errors,
    )
    return {"generated": generated, "skipped": skipped, "errors": errors}

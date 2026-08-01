"""LLM-powered knowledge harvester.

Calls the configured OpenAI-compatible endpoint (Lemonade / Ollama /
any compatible server) and asks it to extract structured knowledge items
from the document text.

Falls back gracefully — returns 0 — if the AI endpoint is unavailable,
times out, or returns unparseable output. The pipeline then runs the
rule-based harvester instead.

Items created here carry review_status='ai_auto' so the UI can
distinguish them from the rule-based 'auto' items.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.capabilities.extraction import ExtractionResult
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# ── Limits ────────────────────────────────────────────────────────────────────

_MAX_TEXT_CHARS = 6_000   # chars sent to the LLM; leaves room for prompt + response
_TIMEOUT_SEC    = 45.0    # background task — can afford to wait
_MAX_ITEMS      = 50      # cap items per document

# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM = """\
You are a knowledge extraction engine. Given document text, extract a JSON \
array of knowledge items.

Each item must have:
  "kind"       : "summary" | "concept" | "claim" | "entity" | "excerpt"
  "text"       : the knowledge as a clear, self-contained statement (required)
  "subject"    : the main entity or topic (string or null)
  "predicate"  : relationship verb (string or null)
  "object"     : what subject relates to (string or null)
  "confidence" : float 0.0–1.0

Guidelines:
  summary  — 1–2 sentence document overview; include exactly one
  concept  — key term, idea, technique, or methodology
  claim    — specific factual or causal assertion
  entity   — person, place, organisation, algorithm, product, or dataset name
  excerpt  — verbatim significant sentence from the source text

Return ONLY a valid JSON array. No markdown fences, no explanation.\
"""

_USER_TMPL = """\
Document title: {title}

Text:
{text}

Return a JSON array of knowledge items.\
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_text_sample(result: "ExtractionResult") -> str:
    """Select the most information-dense portion of the document."""
    full = result.full_text
    if len(full) <= _MAX_TEXT_CHARS:
        return full
    # Head + tail for long docs so we capture intro and conclusions
    head = full[:4_000]
    tail = full[-2_000:]
    return f"{head}\n\n[…document continues…]\n\n{tail}"


def _parse_json_array(raw: str) -> list[dict]:
    """Leniently extract a JSON array from LLM output.

    Handles: extra prose before/after, markdown fences, trailing commas.
    """
    # Strip code fences
    raw = re.sub(r"```(?:json)?|```", "", raw).strip()
    # Find outermost array
    start = raw.find("[")
    end   = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON array delimiters found")
    # Remove trailing commas before ] or } (common LLM mistake)
    fragment = re.sub(r",\s*([}\]])", r"\1", raw[start : end + 1])
    return json.loads(fragment)


def _validate_item(item: object) -> dict | None:
    """Return a clean dict or None if the item is malformed."""
    if not isinstance(item, dict):
        return None
    text = str(item.get("text", "")).strip()
    kind = str(item.get("kind", "excerpt")).strip().lower()
    if not text:
        return None
    if kind not in {"summary", "concept", "claim", "entity", "excerpt"}:
        kind = "excerpt"
    try:
        confidence = float(item.get("confidence", 0.75))
        confidence = round(max(0.0, min(1.0, confidence)), 3)
    except (TypeError, ValueError):
        confidence = 0.75

    def _str_or_none(v: object) -> str | None:
        s = str(v).strip() if v else None
        return s if s else None

    return {
        "kind":       kind,
        "text":       text,
        "subject":    _str_or_none(item.get("subject")),
        "predicate":  _str_or_none(item.get("predicate")),
        "object":     _str_or_none(item.get("object")),
        "confidence": confidence,
    }


# ── Public entry point ────────────────────────────────────────────────────────

def try_ai_harvest(
    result: "ExtractionResult",
    doc_id: str,
    work_id: str | None,
    doc_title: str,
    db: "OrivellumDB",
) -> int:
    """Attempt LLM-based knowledge extraction.

    Returns the number of knowledge items created (≥ 1) on success,
    or 0 if the AI endpoint is unavailable / the response is unusable.
    The caller should run the rule-based harvester when 0 is returned.
    """
    try:
        from orivellum.api._deps import get_config
        cfg = get_config()
    except Exception:
        return 0

    text_sample = _build_text_sample(result)
    if not text_sample.strip():
        return 0

    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": _USER_TMPL.format(title=doc_title, text=text_sample),
        },
    ]

    # ── Call the AI endpoint (synchronous — runs in a background thread) ──
    try:
        import httpx
        resp = httpx.post(
            f"{cfg.serving.base_url}/chat/completions",
            json={
                "model":       cfg.serving.workhorse_model,
                "messages":    messages,
                "stream":      False,
                "temperature": 0.15,   # low temp for consistent structured output
            },
            timeout=_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        content: str = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.info(
            "AI harvest unavailable for doc %s — %s: %s",
            doc_id[:8], type(exc).__name__, exc,
        )
        return 0

    # ── Parse + validate ──────────────────────────────────────────────────
    try:
        raw_items = _parse_json_array(content)
    except Exception as exc:
        logger.warning(
            "AI harvest parse error for doc %s: %s | preview: %.200s",
            doc_id[:8], exc, content,
        )
        return 0

    created = 0
    seen_summaries = 0

    for raw in raw_items[:_MAX_ITEMS]:
        item = _validate_item(raw)
        if item is None:
            continue
        # Enforce single summary per document
        if item["kind"] == "summary":
            if seen_summaries >= 1:
                item["kind"] = "excerpt"
            else:
                seen_summaries += 1

        db.create_knowledge_item(
            work_id=work_id,
            kind=item["kind"],
            text=item["text"],
            subject=item["subject"],
            predicate=item["predicate"],
            obj=item["object"],
            confidence=item["confidence"],
            source_doc_id=doc_id,
            review_status="ai_auto",
        )
        created += 1

    if created:
        logger.info(
            "AI harvest: created %d items for doc %s (work=%s)",
            created, doc_id[:8], work_id,
        )
    else:
        logger.warning(
            "AI harvest returned 0 valid items for doc %s — falling back to rules",
            doc_id[:8],
        )

    return created

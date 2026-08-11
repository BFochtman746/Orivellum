"""ASSAY hierarchical judge — Tier 3, advisory FOREVER.

Three levels, following MAGNET's evaluation design: story, chapter,
sentence.  Pairwise rubric scoring: compare revision N against revision
N−1 of the same chapter, 0–100 per category, because pairwise comparison
is what improves agreement with human judgment on subjective tasks.

Hard rules enforced here:

* The judge produces annotations and preferences.  It NEVER produces a
  gate decision — verdicts from this module are always 'advisory'.
* The judge model must differ from the drafting model ("never let the
  model that wrote the prose grade it").  Enforced in judge_model().
* Every call goes through the llm_call gateway (logged to llm_calls).
* A revision that scores lower than its predecessor is surfaced as a
  finding, never silently accepted.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class JudgeModelError(RuntimeError):
    """Raised when no judge model distinct from the drafting model exists."""


def _drafting_models(db: Any, cfg: Any) -> set[str]:
    models = {cfg.serving.workhorse_model}
    try:
        override = db.get_setting("workhorse_model_override", "") or ""
    except Exception:
        override = ""
    if override:
        models.add(override)
    return models


def judge_model(db: Any, cfg: Any) -> str:
    """Pick the judge model: DB override, else the reasoner. Never the drafter."""
    try:
        override = db.get_setting("judge_model_override", "") or ""
    except Exception:
        override = ""
    candidate = override or cfg.serving.reasoner_model
    drafters = _drafting_models(db, cfg)
    if candidate in drafters:
        raise JudgeModelError(
            "judge model equals the drafting model; set judge_model_override "
            "to a different model — the model that wrote the prose must not grade it"
        )
    return candidate


_JUDGE_SYSTEM = (
    "You are an editorial judge. You annotate and compare; you NEVER decide "
    "whether work passes. Respond ONLY with the requested JSON."
)


def _call(
    db: Any, cfg: Any, model: str, purpose: str, user: str, max_tokens: int = 900
) -> dict | None:
    from ..llm import llm_call

    result = llm_call(
        [{"role": "system", "content": _JUDGE_SYSTEM}, {"role": "user", "content": user}],
        cfg=cfg,
        model=model,
        db=db,
        purpose=purpose,
        temperature=0.1,
        max_tokens=max_tokens,
        timeout=180,
    )
    if not result.ok or not result.text:
        return None
    raw = result.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        return None


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


STORY_CATEGORIES = ["logical_consistency", "thematic_coherence", "character_arc_completion"]
CHAPTER_CATEGORIES = ["goal_conflict_outcome", "hook_and_close", "chapter_necessity"]
SENTENCE_CATEGORIES = ["rhythm", "clarity", "syntax_variety"]


def judge_story(db: Any, cfg: Any, model: str, outline: str) -> dict | None:
    prompt = (
        "Story-level review of this book outline (chapter sequence with "
        "excerpts). For each category give annotations (list of short strings, "
        "each citing a chapter number).\n"
        f"Categories: {STORY_CATEGORIES}.\n"
        'JSON: {"annotations": {"<category>": ["..."]}}\n\n' + outline[:24000]
    )
    return _call(db, cfg, model, "assay.judge.story", prompt, max_tokens=1200)


def judge_chapter(db: Any, cfg: Any, model: str, chapter_label: str, text: str) -> dict | None:
    prompt = (
        f"Chapter-level review of {chapter_label}. For each category give "
        "annotations (short strings quoting the relevant passage).\n"
        f"Categories: {CHAPTER_CATEGORIES}.\n"
        'JSON: {"annotations": {"<category>": ["..."]}}\n\n' + text[:16000]
    )
    return _call(db, cfg, model, "assay.judge.chapter", prompt, max_tokens=1000)


def judge_sentences(db: Any, cfg: Any, model: str, sampled: list[str]) -> dict | None:
    prompt = (
        "Sentence-level review of these sampled sentences. For each category "
        "give annotations naming the sentence index.\n"
        f"Categories: {SENTENCE_CATEGORIES}.\n"
        'JSON: {"annotations": {"<category>": ["..."]}}\n\n'
        + json.dumps(sampled[:12], ensure_ascii=False)
    )
    return _call(db, cfg, model, "assay.judge.sentence", prompt, max_tokens=800)


def judge_pairwise(
    db: Any, cfg: Any, model: str, chapter_label: str, previous: str, current: str
) -> dict | None:
    """Pairwise rubric: revision N vs N−1, 0–100 per category + preference."""
    categories = CHAPTER_CATEGORIES + SENTENCE_CATEGORIES
    prompt = (
        f"Pairwise comparison of two revisions of {chapter_label}. REVISION_A "
        "is the previous revision, REVISION_B the current one. Score each "
        "0-100 per category and state which you prefer overall.\n"
        f"Categories: {categories}.\n"
        'JSON: {"scores_a": {"<category>": n}, "scores_b": {"<category>": n}, '
        '"preference": "A"|"B", "reason": "..."}\n\n'
        f"REVISION_A:\n{previous[:9000]}\n\nREVISION_B:\n{current[:9000]}"
    )
    return _call(db, cfg, model, "assay.judge.pairwise", prompt, max_tokens=700)

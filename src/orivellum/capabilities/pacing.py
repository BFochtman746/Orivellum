"""Scene-level pacing and immersion engine (§6 of the series-continuity spec).

Design rules
────────────
- Scene segmentation proposes, author corrects — extraction is NEVER authoritative.
- All seven detectors are deterministic code over scene metrics.  The LLM only
  extracts per-scene data; it never decides findings or severities.
- Every finding is advisory, evidence-linked, and author-resolvable.
- Severity is always code-computed from profile thresholds, never model output.
- The decision rule (more-scenes / another-book / no-expansion) must be stated
  explicitly in every expansion recommendation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from orivellum.capabilities.atlas import _fence, _parse_json, ground_quote_span

if TYPE_CHECKING:
    from orivellum.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.pacing")

_TIMEOUT_SEC = 90
_FLOAT_FIELDS = {
    "tension_before", "tension_after", "emotional_intensity",
    "revelation_density", "action_ratio", "reflection_ratio", "sensory_grounding",
}
_INT_FIELDS = {
    "irreversible_turns", "reader_questions_created",
    "reader_questions_answered",
}
_BOOL_FIELDS = {
    "has_aftermath", "has_orientation", "consequence_present", "purpose_clear",
}

# ── Error class ───────────────────────────────────────────────────────────────

class PacingError(ValueError):
    pass


# ── Pacing profiles ───────────────────────────────────────────────────────────

PROFILES: dict[str, dict] = {
    "deep_immersive": {
        "label": "Deep Immersive (Historical / Biblical)",
        "min_sensory_grounding": 0.6,
        "max_consecutive_high_tension": 2,
        "require_aftermath_after_major": True,
        "drift_window_scenes": 4,
        "drift_max_avg_tension": 0.3,
        "compression_max_irreversible": 1,
        "tension_variation_min": 0.15,
        "high_tension_threshold": 0.7,
        "low_grounding_threshold": 0.4,
        "whiplash_tension_jump": 0.5,
    },
    "epic": {
        "label": "Epic / Quest",
        "min_sensory_grounding": 0.4,
        "max_consecutive_high_tension": 4,
        "require_aftermath_after_major": False,
        "drift_window_scenes": 6,
        "drift_max_avg_tension": 0.25,
        "compression_max_irreversible": 2,
        "tension_variation_min": 0.1,
        "high_tension_threshold": 0.7,
        "low_grounding_threshold": 0.3,
        "whiplash_tension_jump": 0.6,
    },
    "literary": {
        "label": "Literary Fiction",
        "min_sensory_grounding": 0.65,
        "max_consecutive_high_tension": 1,
        "require_aftermath_after_major": True,
        "drift_window_scenes": 5,
        "drift_max_avg_tension": 0.2,
        "compression_max_irreversible": 1,
        "tension_variation_min": 0.08,
        "high_tension_threshold": 0.65,
        "low_grounding_threshold": 0.5,
        "whiplash_tension_jump": 0.4,
    },
    "thriller": {
        "label": "Thriller",
        "min_sensory_grounding": 0.3,
        "max_consecutive_high_tension": 6,
        "require_aftermath_after_major": False,
        "drift_window_scenes": 3,
        "drift_max_avg_tension": 0.25,
        "compression_max_irreversible": 3,
        "tension_variation_min": 0.2,
        "high_tension_threshold": 0.75,
        "low_grounding_threshold": 0.2,
        "whiplash_tension_jump": 0.7,
    },
    "mystery": {
        "label": "Mystery",
        "min_sensory_grounding": 0.5,
        "max_consecutive_high_tension": 3,
        "require_aftermath_after_major": False,
        "drift_window_scenes": 4,
        "drift_max_avg_tension": 0.25,
        "compression_max_irreversible": 2,
        "tension_variation_min": 0.12,
        "high_tension_threshold": 0.7,
        "low_grounding_threshold": 0.35,
        "whiplash_tension_jump": 0.55,
    },
    "romance": {
        "label": "Romance",
        "min_sensory_grounding": 0.55,
        "max_consecutive_high_tension": 2,
        "require_aftermath_after_major": True,
        "drift_window_scenes": 4,
        "drift_max_avg_tension": 0.2,
        "compression_max_irreversible": 1,
        "tension_variation_min": 0.1,
        "high_tension_threshold": 0.65,
        "low_grounding_threshold": 0.4,
        "whiplash_tension_jump": 0.45,
    },
    "ya": {
        "label": "Young Adult",
        "min_sensory_grounding": 0.4,
        "max_consecutive_high_tension": 3,
        "require_aftermath_after_major": False,
        "drift_window_scenes": 4,
        "drift_max_avg_tension": 0.25,
        "compression_max_irreversible": 2,
        "tension_variation_min": 0.15,
        "high_tension_threshold": 0.7,
        "low_grounding_threshold": 0.3,
        "whiplash_tension_jump": 0.5,
    },
    "custom": {
        "label": "Custom Profile",
        "min_sensory_grounding": 0.5,
        "max_consecutive_high_tension": 3,
        "require_aftermath_after_major": True,
        "drift_window_scenes": 4,
        "drift_max_avg_tension": 0.3,
        "compression_max_irreversible": 1,
        "tension_variation_min": 0.12,
        "high_tension_threshold": 0.7,
        "low_grounding_threshold": 0.35,
        "whiplash_tension_jump": 0.5,
    },
}


def _profile_for(db: "OrivellumDB", work_id: str, override: str | None = None) -> dict:
    """Return the resolved pacing profile thresholds for a work."""
    name = override
    if name is None:
        with db._lock:
            row = db._conn.execute(
                "SELECT profile_name, thresholds FROM pacing_profiles WHERE work_id=?",
                (work_id,),
            ).fetchone()
        if row:
            name = row["profile_name"]
            base = dict(PROFILES.get(name, PROFILES["custom"]))
            try:
                overrides = json.loads(row["thresholds"] or "{}")
                base.update(overrides)
            except Exception:
                pass
            return base
    return dict(PROFILES.get(name or "deep_immersive", PROFILES["deep_immersive"]))


# ── LLM call helper ───────────────────────────────────────────────────────────

def _call(prompt: str, *, purpose: str, cfg: "OrivellumConfig",
          db: "OrivellumDB") -> Any:
    from orivellum.capabilities.llm import llm_call  # noqa: PLC0415

    result = llm_call(
        [{"role": "user", "content": prompt}],
        cfg=cfg,
        db=db,
        purpose=purpose,
        timeout=_TIMEOUT_SEC,
        temperature=0.0,
    )
    if not result.ok:
        raise PacingError(f"LLM call failed ({purpose}): {result.error}")
    parsed = _parse_json(result.text)
    if parsed is None:
        raise PacingError(f"LLM returned non-JSON for {purpose!r}: {result.text[:200]}")
    return parsed


# ── Scene extraction ──────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """\
You are segmenting a chapter into its constituent scenes for a pacing analysis.

Chapter title: {chapter_title}
Chapter text:
{fence}
{text}
{fence}

Return a JSON object with this exact shape:
{{
  "scenes": [
    {{
      "title": "Short scene title (≤8 words)",
      "start_quote": "First 8–15 verbatim words that begin this scene",
      "end_quote": "Last 8–15 verbatim words that end this scene",
      "purpose": "One of: action|confrontation|revelation|aftermath|reflection|transition|exposition|dialogue|decision|opening|closing",
      "pov": "POV character name or 'omniscient'",
      "setting": "Brief setting label",
      "time_elapsed_mins": null
    }}
  ]
}}

Rules:
- Identify real scene breaks — location change, POV shift, significant time jump, or thematic pivot.
- Every start_quote and end_quote MUST appear verbatim in the chapter text.
- Do not invent text.
- Return at least 1 scene (the whole chapter if it is one scene).
"""

_SCENE_FENCE = "```"


def extract_scenes(
    db: "OrivellumDB",
    cfg: "OrivellumConfig",
    work_id: str,
    *,
    chapter_id: str | None = None,
) -> list[dict]:
    """Propose scenes from chapter text.  Proposals are stored with status='proposed'.

    All scenes are advisory — the author can correct or dismiss them.
    Scenes whose start/end quotes cannot be grounded verbatim are discarded silently.
    """
    with db._lock:
        if chapter_id:
            ch_rows = db._conn.execute(
                "SELECT id, work_id, title, text FROM book_chapters WHERE id=? AND work_id=?",
                (chapter_id, work_id),
            ).fetchall()
        else:
            ch_rows = db._conn.execute(
                "SELECT id, work_id, title, text FROM book_chapters "
                "WHERE work_id=? ORDER BY seq",
                (work_id,),
            ).fetchall()

    if not ch_rows:
        raise PacingError("No chapters found for work")

    created: list[dict] = []
    now = datetime.now(UTC).isoformat()

    for ch in ch_rows:
        cid = ch["id"]
        text = ch["text"] or ""
        if not text.strip():
            continue

        # Truncate very long chapters to stay within context
        excerpt = text[:8000]

        try:
            payload = _call(
                _EXTRACT_PROMPT.format(
                    chapter_title=ch["title"] or "Untitled",
                    fence=_SCENE_FENCE,
                    text=excerpt,
                ),
                purpose="pacing.extract_scenes",
                cfg=cfg,
                db=db,
            )
        except PacingError as exc:
            logger.warning("Scene extraction failed for chapter %s: %s", cid, exc)
            continue

        proposals = payload.get("scenes", [])
        if not isinstance(proposals, list):
            continue

        with db._lock:
            max_seq = db._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) FROM scenes WHERE chapter_id=?", (cid,)
            ).fetchone()[0]

        seq = max_seq + 1
        for prop in proposals:
            if not isinstance(prop, dict):
                continue
            start_quote = (prop.get("start_quote") or "").strip()
            end_quote = (prop.get("end_quote") or "").strip()
            if not start_quote:
                continue

            # Ground the start quote — discard any proposal whose start is not
            # verifiable verbatim in the chapter text.  Storing offset 0 as a
            # fallback contaminates segmentation with fabricated boundaries.
            start_result = ground_quote_span(start_quote, text)
            if start_result is None:
                logger.debug(
                    "Scene proposal discarded: start_quote not grounded in chapter %s: %r",
                    cid, start_quote[:40],
                )
                continue
            start_offset = start_result[0]

            # Ground the end quote.  Search only in text[start_offset:] so that
            # repeated phrases cannot produce an inverted/zero-length span.
            # Use the verbatim span length returned by ground_quote_span — never
            # the model's quote length, which may differ after normalisation.
            if end_quote:
                end_result = ground_quote_span(end_quote, text[start_offset:])
                if end_result is not None:
                    end_span_start, end_span = end_result
                    end_offset = start_offset + end_span_start + len(end_span)
                else:
                    # Ungrounded end: store the scene as open-ended (extends to
                    # chapter boundary) rather than discarding it outright.
                    logger.debug(
                        "end_quote not grounded after start in chapter %s: %r",
                        cid, end_quote[:40],
                    )
                    end_offset = None
            else:
                end_offset = None

            # Compute word count from text slice
            snippet = text[start_offset:end_offset] if end_offset else text[start_offset:]
            word_count = len(snippet.split())

            sid = str(uuid.uuid4())
            meta = {
                "extraction_source": "llm",
                "start_quote": start_quote,
                "end_quote": end_quote,
            }
            with db._lock:
                db._conn.execute(
                    """INSERT OR IGNORE INTO scenes
                       (id, chapter_id, work_id, seq, title, source_offset_start,
                        source_offset_end, word_count, purpose, pov, setting,
                        time_elapsed_mins, status, meta, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        sid, cid, work_id, seq,
                        (prop.get("title") or "")[:120],
                        start_offset, end_offset, word_count,
                        (prop.get("purpose") or "")[:50],
                        (prop.get("pov") or "")[:80],
                        (prop.get("setting") or "")[:120],
                        prop.get("time_elapsed_mins"),
                        "proposed",
                        json.dumps(meta),
                        now, now,
                    ),
                )
                db._conn.commit()
            created.append(get_scene(db, sid))
            seq += 1

    return created


def get_scene(db: "OrivellumDB", scene_id: str) -> dict:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM scenes WHERE id=?", (scene_id,)
        ).fetchone()
    if not row:
        raise PacingError(f"Scene {scene_id!r} not found")
    return dict(row)


def list_scenes(db: "OrivellumDB", work_id: str, *, chapter_id: str | None = None) -> list[dict]:
    """Return scenes in narrative order.

    For single-chapter queries: ordered by scene.seq within that chapter.
    For full-work queries: ordered by (chapter.seq, scene.seq) so scenes
    from chapter 2 always follow chapter 1 regardless of the per-chapter seq values.
    """
    with db._lock:
        if chapter_id:
            rows = db._conn.execute(
                """SELECT s.* FROM scenes s
                   WHERE s.work_id=? AND s.chapter_id=?
                   ORDER BY s.seq""",
                (work_id, chapter_id),
            ).fetchall()
        else:
            rows = db._conn.execute(
                """SELECT s.* FROM scenes s
                   JOIN book_chapters bc ON bc.id = s.chapter_id
                   WHERE s.work_id=?
                   ORDER BY bc.seq, s.seq""",
                (work_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def update_scene(db: "OrivellumDB", scene_id: str, **kwargs: Any) -> dict:
    """Author correction of a proposed scene.  Only whitelisted fields accepted."""
    allowed = {"title", "purpose", "pov", "setting", "time_elapsed_mins", "status"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        raise PacingError("No valid fields to update")
    now = datetime.now(UTC).isoformat()
    updates["updated_at"] = now
    cols = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [scene_id]
    with db._lock:
        db._conn.execute(f"UPDATE scenes SET {cols} WHERE id=?", vals)
        db._conn.commit()
    return get_scene(db, scene_id)


# ── Scene metric analysis ─────────────────────────────────────────────────────

_ANALYZE_PROMPT = """\
You are a developmental editor analyzing a scene for pacing and immersion.

Scene title: {title}
Scene text:
```
{text}
```

Return a JSON object with EXACTLY this shape (all float fields must be 0.0–1.0):
{{
  "tension_before": 0.0,
  "tension_after": 0.0,
  "emotional_intensity": 0.0,
  "revelation_density": 0.0,
  "action_ratio": 0.0,
  "reflection_ratio": 0.0,
  "sensory_grounding": 0.0,
  "has_aftermath": false,
  "has_orientation": false,
  "irreversible_turns": 0,
  "reader_questions_created": 0,
  "reader_questions_answered": 0,
  "consequence_present": false,
  "purpose_clear": false,
  "evidence": [
    {{"field": "tension_after", "quote": "exact verbatim text evidence", "reasoning": "why"}}
  ]
}}

Definitions:
- tension_before/after: reader-felt story tension at start/end of scene (0=calm, 1=maximum dread/danger)
- emotional_intensity: peak emotional charge of any character in the scene
- revelation_density: fraction of scene that delivers plot/character information the reader didn't have
- action_ratio: fraction external action (physical, dialogue-as-event) vs total scene length
- reflection_ratio: fraction interiority, internal processing, or deliberate quiet
- sensory_grounding: richness of sensory detail (touch, sound, smell, texture, temperature)
- has_aftermath: scene includes processing of prior event consequences
- has_orientation: scene grounds reader in time/place/POV clearly
- irreversible_turns: count of major events from which characters cannot return (death, betrayal, discovery, decision)
- consequence_present: scene shows believable consequence of prior cause
- purpose_clear: scene has a clear dramatic function recognizable from the text

Every evidence quote MUST appear verbatim in the scene text. Omit evidence entries whose quotes cannot be verified.
"""


def _validate_metrics(raw: dict, text: str) -> dict:
    """Coerce and validate model output.  Invalid fields are set to None."""
    out: dict = {}
    for f in _FLOAT_FIELDS:
        try:
            v = float(raw.get(f, 0.0))
            out[f] = max(0.0, min(1.0, v))
        except (TypeError, ValueError):
            out[f] = 0.0

    for f in _INT_FIELDS:
        try:
            v = int(raw.get(f, 0))
            out[f] = max(0, v)
        except (TypeError, ValueError):
            out[f] = 0

    for f in _BOOL_FIELDS:
        out[f] = int(bool(raw.get(f, False)))

    # Validate evidence: every quoted span must appear verbatim
    raw_ev = raw.get("evidence", [])
    if not isinstance(raw_ev, list):
        raw_ev = []
    validated_ev = []
    for ev in raw_ev:
        if not isinstance(ev, dict):
            continue
        quote = (ev.get("quote") or "").strip()
        if not quote:
            continue
        if quote not in text:
            continue  # discard ungrounded evidence
        validated_ev.append({
            "field": ev.get("field", ""),
            "quote": quote,
            "reasoning": ev.get("reasoning", ""),
        })
    out["evidence"] = validated_ev
    return out


def analyze_scene(
    db: "OrivellumDB",
    cfg: "OrivellumConfig",
    scene_id: str,
) -> dict:
    """Extract per-scene pacing metrics.  Returns stored scene_metrics row."""
    with db._lock:
        row = db._conn.execute("SELECT * FROM scenes WHERE id=?", (scene_id,)).fetchone()
    if not row:
        raise PacingError(f"Scene {scene_id!r} not found")
    scene = dict(row)

    # Load scene text from chapter
    with db._lock:
        ch = db._conn.execute(
            "SELECT text FROM book_chapters WHERE id=?", (scene["chapter_id"],)
        ).fetchone()
    chapter_text = (ch["text"] if ch else None) or ""
    start = scene.get("source_offset_start") or 0
    end = scene.get("source_offset_end")
    scene_text = chapter_text[start:end] if end else chapter_text[start:]
    if not scene_text.strip():
        raise PacingError("Scene has no text to analyze")

    raw = _call(
        _ANALYZE_PROMPT.format(title=scene.get("title") or "Untitled", text=scene_text[:6000]),
        purpose="pacing.analyze_scene",
        cfg=cfg,
        db=db,
    )

    metrics = _validate_metrics(raw, scene_text)

    # Determine next version
    with db._lock:
        ver_row = db._conn.execute(
            "SELECT COALESCE(MAX(version),0) FROM scene_metrics WHERE scene_id=?",
            (scene_id,),
        ).fetchone()
    version = int(ver_row[0]) + 1

    mid = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    with db._lock:
        db._conn.execute(
            """INSERT INTO scene_metrics
               (id, scene_id, work_id, version,
                tension_before, tension_after, emotional_intensity,
                revelation_density, action_ratio, reflection_ratio,
                sensory_grounding, has_aftermath, has_orientation,
                irreversible_turns, reader_questions_created,
                reader_questions_answered, consequence_present,
                purpose_clear, evidence, model_output, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mid, scene_id, scene["work_id"], version,
                metrics["tension_before"], metrics["tension_after"],
                metrics["emotional_intensity"], metrics["revelation_density"],
                metrics["action_ratio"], metrics["reflection_ratio"],
                metrics["sensory_grounding"],
                metrics["has_aftermath"], metrics["has_orientation"],
                metrics["irreversible_turns"],
                metrics["reader_questions_created"],
                metrics["reader_questions_answered"],
                metrics["consequence_present"],
                metrics["purpose_clear"],
                json.dumps(metrics["evidence"]),
                json.dumps(raw),
                now,
            ),
        )
        db._conn.commit()

    metrics["id"] = mid
    metrics["scene_id"] = scene_id
    metrics["work_id"] = scene["work_id"]
    metrics["version"] = version
    metrics["created_at"] = now
    return metrics


def get_scene_metrics(db: "OrivellumDB", scene_id: str) -> dict | None:
    """Return the latest scene_metrics row for a scene, or None."""
    with db._lock:
        row = db._conn.execute(
            """SELECT * FROM scene_metrics WHERE scene_id=?
               ORDER BY version DESC LIMIT 1""",
            (scene_id,),
        ).fetchone()
    if not row:
        return None
    m = dict(row)
    try:
        m["evidence"] = json.loads(m.get("evidence") or "[]")
    except Exception:
        m["evidence"] = []
    return m


# ── Seven detectors (deterministic, no LLM) ───────────────────────────────────

def _dedupe(run_id: str, detector: str, finding_type: str, subject: str) -> str:
    key = f"{run_id}:{detector}:{finding_type}:{subject}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _store_finding(
    db: "OrivellumDB",
    run_id: str,
    work_id: str,
    detector: str,
    finding_type: str,
    severity: str,
    subject: str,
    explanation: str,
    evidence: list,
    recommendation: dict,
    now: str,
) -> None:
    fid = str(uuid.uuid4())
    dk = _dedupe(run_id, detector, finding_type, subject)
    with db._lock:
        db._conn.execute(
            """INSERT OR IGNORE INTO pacing_findings
               (id, run_id, work_id, detector, finding_type, severity,
                subject, explanation, evidence, recommendation, status,
                resolution_note, dedupe_key, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                fid, run_id, work_id, detector, finding_type, severity,
                subject, explanation,
                json.dumps(evidence), json.dumps(recommendation),
                "open", "", dk, now,
            ),
        )
        db._conn.commit()


def _scenes_with_metrics(db: "OrivellumDB", work_id: str) -> list[dict]:
    """Load all confirmed/proposed scenes with their latest metrics."""
    scenes = list_scenes(db, work_id)
    result = []
    for sc in scenes:
        if sc["status"] == "dismissed":
            continue
        m = get_scene_metrics(db, sc["id"])
        result.append({"scene": sc, "metrics": m})
    return result


# 1. Pacing map — flat or incoherent tension arc
def _detect_pacing_map(
    items: list[dict], profile: dict, run_id: str, work_id: str, db: "OrivellumDB", now: str
) -> None:
    measured = [(i, it) for i, it in enumerate(items) if it["metrics"]]
    if len(measured) < 3:
        return

    tensions = [it["metrics"]["tension_after"] for _, it in measured if it["metrics"]["tension_after"] is not None]
    if len(tensions) < 3:
        return

    variation = max(tensions) - min(tensions)
    if variation < profile["tension_variation_min"]:
        _store_finding(
            db, run_id, work_id,
            "pacing_map", "flat_tension_arc", "medium",
            "Full book tension arc",
            f"Tension varies only {variation:.2f} across {len(tensions)} scenes "
            f"(minimum variation for this profile: {profile['tension_variation_min']:.2f}). "
            f"Readers may lose the sense of escalation and release.",
            [{"field": "tension_after", "value_range": f"{min(tensions):.2f}–{max(tensions):.2f}",
              "scene_count": len(tensions)}],
            {
                "recommendation_type": "more_scenes",
                "explanation": (
                    "The existing dramatic question would be better served by scenes that "
                    "vary tension more deliberately — building toward, and recovering from, "
                    "high-stakes moments. This is not an expansion for word-count; it is "
                    "causally required by the arc."
                ),
                "placement": "Identify the intended climax scene and build approach/aftermath scenes around it.",
                "alternatives": [
                    {"option": "intentional_restraint",
                     "tradeoff": "If this is a deliberately meditative profile, confirm via profile selection."}
                ],
            },
            now,
        )


# 2. Breath map — aftermath/orientation missing around major events
def _detect_breath_map(
    items: list[dict], profile: dict, run_id: str, work_id: str, db: "OrivellumDB", now: str
) -> None:
    high_t = profile["high_tension_threshold"]
    for idx, it in enumerate(items):
        m = it["metrics"]
        if not m:
            continue
        ta = m.get("tension_after") or 0.0
        irrev = m.get("irreversible_turns") or 0

        is_major = ta >= high_t or irrev >= 1

        if not is_major:
            continue

        # Check: does any scene immediately after have has_aftermath?
        next_aftermath = False
        for j in range(idx + 1, min(idx + 3, len(items))):
            nm = items[j]["metrics"]
            if nm and nm.get("has_aftermath"):
                next_aftermath = True
                break

        if not next_aftermath and profile.get("require_aftermath_after_major"):
            sc = it["scene"]
            _store_finding(
                db, run_id, work_id,
                "breath_map", "missing_aftermath", "high",
                sc.get("title") or f"Scene {sc.get('seq', '?')}",
                (
                    f"Scene '{sc.get('title')}' has tension {ta:.2f} and "
                    f"{irrev} irreversible turn(s) but no aftermath scene follows within 2 scenes. "
                    f"In {profile.get('label','this profile')}, major events require "
                    "emotional processing before the next pressure escalation."
                ),
                [{"scene_id": sc["id"], "title": sc.get("title"),
                  "tension_after": ta, "irreversible_turns": irrev}],
                {
                    "recommendation_type": "more_scenes",
                    "explanation": (
                        "An aftermath scene here serves the existing book's dramatic question by "
                        "grounding the character's response to the irreversible change. "
                        "Without it, subsequent tension feels disconnected."
                    ),
                    "placement": f"Insert an aftermath / reflection scene after '{sc.get('title')}'.",
                    "what_changes": "character emotional state, relationship processing, physical consequence",
                    "alternatives": [
                        {"option": "intentional_restraint",
                         "tradeoff": "If the breathlessness is intentional (e.g. thriller climax sequence), "
                                     "dismiss with rationale and select a matching profile."}
                    ],
                },
                now,
            )

        # Missing orientation
        m2 = it["metrics"]
        if m2 and not m2.get("has_orientation"):
            # Check previous scene's tension — if there's a big jump, orientation matters more
            if idx > 0:
                prev_m = items[idx - 1]["metrics"]
                if prev_m:
                    jump = abs((m2.get("tension_before") or 0.0) - (prev_m.get("tension_after") or 0.0))
                    if jump > profile.get("whiplash_tension_jump", 0.5):
                        sc = it["scene"]
                        _store_finding(
                            db, run_id, work_id,
                            "breath_map", "missing_orientation", "medium",
                            sc.get("title") or f"Scene {sc.get('seq', '?')}",
                            (
                                f"Scene '{sc.get('title')}' follows a tension jump of {jump:.2f} "
                                "but has no clear reader orientation (time, place, POV). "
                                "Readers may feel disoriented entering this scene."
                            ),
                            [{"scene_id": sc["id"], "tension_jump": round(jump, 3)}],
                            {
                                "recommendation_type": "more_scenes",
                                "explanation": "A brief orientation beat at the start of this scene would "
                                               "anchor the reader without requiring a separate chapter.",
                                "placement": "Opening beat of this scene.",
                                "alternatives": [
                                    {"option": "intentional_restraint",
                                     "tradeoff": "In-medias-res openings are valid if the genre supports it."}
                                ],
                            },
                            now,
                        )


# 3. Compression detector — multiple irreversible turns in one scene
def _detect_compression(
    items: list[dict], profile: dict, run_id: str, work_id: str, db: "OrivellumDB", now: str
) -> None:
    max_irrev = profile["compression_max_irreversible"]
    for it in items:
        m = it["metrics"]
        if not m:
            continue
        irrev = m.get("irreversible_turns") or 0
        if irrev > max_irrev:
            sc = it["scene"]
            consequence = bool(m.get("consequence_present"))
            _store_finding(
                db, run_id, work_id,
                "compression", "compressed_irreversible_events", "high",
                sc.get("title") or f"Scene {sc.get('seq', '?')}",
                (
                    f"Scene '{sc.get('title')}' contains {irrev} irreversible turn(s) "
                    f"(profile limit: {max_irrev}). "
                    + ("Consequence is present but the density is still too high for this profile."
                       if consequence else
                       "No consequence is shown for the accumulated events.")
                ),
                [{"scene_id": sc["id"], "title": sc.get("title"),
                  "irreversible_turns": irrev, "consequence_present": consequence,
                  "word_count": sc.get("word_count", 0)}],
                {
                    "recommendation_type": "more_scenes",
                    "explanation": (
                        "Each irreversible turn needs enough space for causality, "
                        "character reaction, and consequence. Compressing multiple turns "
                        "into one scene removes the reader's ability to process meaning. "
                        "These scenes serve the existing dramatic question — separating "
                        "them does not require a new book."
                    ),
                    "placement": (
                        f"Identify the primary irreversible event and give it its own scene; "
                        f"the secondary event(s) become an aftermath/consequence scene."
                    ),
                    "what_changes": "causality chain, character processing time, stakes legibility",
                    "alternatives": [
                        {"option": "no_expansion",
                         "tradeoff": f"If {irrev} turns in one scene is intentional for pacing "
                                     "tempo (e.g. thriller), select a matching genre profile."}
                    ],
                },
                now,
            )


# 4. Drift detector — extended low-pressure stretch with no purpose
def _detect_drift(
    items: list[dict], profile: dict, run_id: str, work_id: str, db: "OrivellumDB", now: str
) -> None:
    window = profile["drift_window_scenes"]
    max_avg_t = profile["drift_max_avg_tension"]

    if len(items) < window:
        return

    for start in range(len(items) - window + 1):
        window_items = items[start:start + window]
        measured = [it for it in window_items if it["metrics"]]
        if len(measured) < window:
            continue

        tensions = [it["metrics"]["tension_after"] or 0.0 for it in measured]
        avg_t = sum(tensions) / len(tensions)
        purpose_scores = [it["metrics"]["purpose_clear"] for it in measured]
        avg_purpose = sum(purpose_scores) / len(purpose_scores)
        revelation_scores = [it["metrics"]["revelation_density"] or 0.0 for it in measured]
        avg_rev = sum(revelation_scores) / len(revelation_scores)

        if avg_t <= max_avg_t and avg_purpose < 0.5 and avg_rev < 0.15:
            titles = [it["scene"].get("title") or f"Scene {it['scene'].get('seq','?')}"
                      for it in measured[:3]]
            _store_finding(
                db, run_id, work_id,
                "drift", "low_pressure_drift", "medium",
                f"Scenes {start+1}–{start+window}",
                (
                    f"{window} consecutive scenes (starting at scene {start+1}) show "
                    f"average tension {avg_t:.2f} (≤{max_avg_t:.2f}), "
                    f"average revelation density {avg_rev:.2f}, "
                    f"and unclear purpose in {sum(1 for p in purpose_scores if p == 0)} of {window} scenes. "
                    f"Titles: {', '.join(titles)}."
                ),
                [{"scene_range": f"{start+1}–{start+window}",
                  "avg_tension": round(avg_t, 3),
                  "avg_revelation": round(avg_rev, 3),
                  "scene_count": window}],
                {
                    "recommendation_type": "no_expansion",
                    "explanation": (
                        "The question is not whether to add more scenes but whether these existing "
                        "scenes have a clear intentional purpose. Each should contribute: "
                        "escalating a question, showing character, deepening world, or earning "
                        "the reader's investment. If they do, dismiss this finding with rationale."
                    ),
                    "alternatives": [
                        {"option": "more_scenes",
                         "tradeoff": "If these scenes are setup for an upcoming revelation, "
                                     "strengthen the setup to make its purpose visible."},
                        {"option": "no_expansion",
                         "tradeoff": "If deliberate breathing room, confirm with genre profile and dismiss."},
                    ],
                },
                now,
            )
            break  # one drift finding per run is sufficient


# 5. Book-boundary detector — multiple full dramatic arc cycles
def _detect_book_boundary(
    items: list[dict], profile: dict, run_id: str, work_id: str, db: "OrivellumDB", now: str
) -> None:
    """Identify whether the book contains what appear to be two independent dramatic arcs."""
    measured = [it for it in items if it["metrics"]]
    if len(measured) < 8:
        return  # not enough scenes to distinguish two arcs

    tensions = [it["metrics"]["tension_after"] or 0.0 for it in measured]
    half = len(tensions) // 2
    first_half = tensions[:half]
    second_half = tensions[half:]

    first_peak = max(first_half) if first_half else 0.0
    second_peak = max(second_half) if second_half else 0.0
    first_trough_after_peak = min(first_half[first_half.index(first_peak):]) if first_half else 1.0
    high_t = profile["high_tension_threshold"]

    # Two arcs: first half peaks and drops to near-calm; second half peaks again
    both_peaks_high = first_peak >= high_t and second_peak >= high_t
    first_arc_resolves = first_trough_after_peak < (high_t * 0.5)

    if both_peaks_high and first_arc_resolves:
        first_climax_idx = first_half.index(first_peak)
        sc_mid = measured[half]["scene"] if half < len(measured) else {}
        _store_finding(
            db, run_id, work_id,
            "book_boundary", "potential_two_arc_book", "medium",
            "Book-level dramatic arc",
            (
                f"The tension map shows two independent peak-and-resolution cycles: "
                f"first half peaks at {first_peak:.2f} then falls to {first_trough_after_peak:.2f}, "
                f"second half peaks at {second_peak:.2f}. "
                f"This pattern can indicate two books compressed into one, or one book "
                f"with a strong midpoint reversal. Evaluation required."
            ),
            [{"first_half_peak": round(first_peak, 3),
              "first_half_post_peak_trough": round(first_trough_after_peak, 3),
              "second_half_peak": round(second_peak, 3),
              "midpoint_scene": sc_mid.get("title", "")}],
            {
                "recommendation_type": "another_book",
                "explanation": (
                    "Consider whether the second arc has its own distinct central dramatic question, "
                    "meaningful setup-escalation-climax-resolution cycle, durable reader promise, "
                    "and enough character transformation to justify a separate installment. "
                    "If it does, this is a candidate for two books. "
                    "If both arcs serve one overarching question, this is a strong midpoint reversal "
                    "and no expansion is needed — dismiss with rationale."
                ),
                "distinct_question_test": (
                    "Ask: 'What is the central dramatic question of the first arc, "
                    "and what is the central dramatic question of the second arc?' "
                    "If they are meaningfully different and each has a complete cycle, "
                    "two books is justified."
                ),
                "tradeoffs": {
                    "two_books": "Greater depth, each book earns its own promise/resolution.",
                    "one_book": "Stronger midpoint turn, but reader may feel two climaxes compete.",
                },
                "alternatives": [
                    {"option": "no_expansion",
                     "tradeoff": "A midpoint reversal is a valid structural device. "
                                 "Dismiss if the overarching question is unified."}
                ],
            },
            now,
        )


# 6. Immersion integrity — grounding, whiplash, abrupt POV
def _detect_immersion_integrity(
    items: list[dict], profile: dict, run_id: str, work_id: str, db: "OrivellumDB", now: str
) -> None:
    low_g = profile["low_grounding_threshold"]
    whiplash = profile["whiplash_tension_jump"]

    for idx, it in enumerate(items):
        m = it["metrics"]
        if not m:
            continue
        sc = it["scene"]

        # Low sensory grounding
        sg = m.get("sensory_grounding") or 0.0
        if sg < low_g and m.get("emotional_intensity", 0) >= 0.5:
            _store_finding(
                db, run_id, work_id,
                "immersion_integrity", "low_sensory_grounding", "medium",
                sc.get("title") or f"Scene {sc.get('seq','?')}",
                (
                    f"Scene '{sc.get('title')}' has emotional intensity {m.get('emotional_intensity', 0):.2f} "
                    f"but sensory grounding only {sg:.2f} "
                    f"(profile minimum: {profile['min_sensory_grounding']:.2f}). "
                    "High-emotion scenes without sensory anchoring can feel abstract or unearned."
                ),
                [{"scene_id": sc["id"], "sensory_grounding": sg,
                  "emotional_intensity": m.get("emotional_intensity", 0)}],
                {
                    "recommendation_type": "no_expansion",
                    "explanation": (
                        "Sensory grounding does not require new scenes — it requires adding "
                        "texture within this scene: sound, touch, temperature, physical detail. "
                        "The dramatic question is unchanged."
                    ),
                    "alternatives": [{"option": "more_scenes",
                                      "tradeoff": "If the setting needs more establishment, "
                                                  "a brief establishing beat before this scene works."}],
                },
                now,
            )

        # Tension whiplash between scenes
        if idx > 0:
            prev_m = items[idx - 1]["metrics"]
            if prev_m and prev_m.get("tension_after") is not None:
                prev_ta = prev_m["tension_after"] or 0.0
                cur_tb = m.get("tension_before") or 0.0
                jump = abs(cur_tb - prev_ta)
                if jump > whiplash and not m.get("has_orientation"):
                    _store_finding(
                        db, run_id, work_id,
                        "immersion_integrity", "tension_whiplash", "high",
                        sc.get("title") or f"Scene {sc.get('seq','?')}",
                        (
                            f"Tension jumps {jump:.2f} between scene {idx} and scene {idx+1} "
                            f"('{sc.get('title')}') without orientation. "
                            "Readers may feel disoriented or emotionally manipulated."
                        ),
                        [{"from_scene_tension": round(prev_ta, 3),
                          "to_scene_tension": round(cur_tb, 3),
                          "jump": round(jump, 3)}],
                        {
                            "recommendation_type": "no_expansion",
                            "explanation": (
                                "A brief transition or orientation beat — even one paragraph — "
                                "grounds the reader before the tension shift. "
                                "This does not require a new scene if the transition fits naturally "
                                "at the end of the prior scene or start of this one."
                            ),
                            "alternatives": [
                                {"option": "more_scenes",
                                 "tradeoff": "A full transition scene is warranted if the time gap is large."}
                            ],
                        },
                        now,
                    )


# 7. Series rhythm — mechanical repetition across consecutive series books
def _detect_series_rhythm(
    db: "OrivellumDB", work_id: str,
    items: list[dict], profile: dict,
    run_id: str, now: str,
) -> None:
    """Compare this book's opening/closing tension pattern to sibling works in the series."""
    # Find series membership
    with db._lock:
        members = db._conn.execute(
            """SELECT sm.series_id, sm.volume, w.id AS work_id, w.title AS work_title
               FROM series_member sm JOIN works w ON w.id = sm.work_id
               WHERE sm.series_id IN (
                   SELECT series_id FROM series_member WHERE work_id=?)
               ORDER BY sm.series_id, sm.volume""",
            (work_id,),
        ).fetchall()

    if not members:
        return

    # Find predecessor and successor in reading order
    this_entry = next((m for m in members if m["work_id"] == work_id), None)
    if not this_entry:
        return
    series_id = this_entry["series_id"]
    this_vol = this_entry["volume"]

    siblings = [m for m in members if m["series_id"] == series_id and m["work_id"] != work_id]

    measured = [it for it in items if it["metrics"]]
    if not measured:
        return

    this_opening_tension = measured[0]["metrics"]["tension_before"] or 0.0 if measured else 0.0
    this_closing_tension = measured[-1]["metrics"]["tension_after"] or 0.0 if measured else 0.0

    SAME_PATTERN_THRESHOLD = 0.15

    matching_siblings = 0
    checked_siblings = 0
    for sib in siblings:
        sib_scenes = list_scenes(db, sib["work_id"])
        sib_measured = []
        for sc in sib_scenes[:3]:
            m = get_scene_metrics(db, sc["id"])
            if m:
                sib_measured.append(m)
        if not sib_measured:
            continue
        checked_siblings += 1
        sib_open = sib_measured[0]["tension_before"] or 0.0

        # Last 3 scenes
        last_scenes = sib_scenes[-3:] if len(sib_scenes) >= 3 else sib_scenes
        sib_close_metrics = []
        for sc in last_scenes:
            m = get_scene_metrics(db, sc["id"])
            if m:
                sib_close_metrics.append(m)
        sib_close = sib_close_metrics[-1]["tension_after"] if sib_close_metrics else 0.0

        open_diff = abs(this_opening_tension - sib_open)
        close_diff = abs(this_closing_tension - (sib_close or 0.0))

        if open_diff < SAME_PATTERN_THRESHOLD and close_diff < SAME_PATTERN_THRESHOLD:
            matching_siblings += 1

    if checked_siblings >= 2 and matching_siblings >= 2:
        _store_finding(
            db, run_id, work_id,
            "series_rhythm", "mechanical_arc_repetition", "medium",
            f"Series arc pattern",
            (
                f"This book's opening tension ({this_opening_tension:.2f}) and closing tension "
                f"({this_closing_tension:.2f}) are very similar to {matching_siblings} other "
                f"books in the series. Readers may perceive the series as mechanically formulaic."
            ),
            [{"this_opening": round(this_opening_tension, 3),
              "this_closing": round(this_closing_tension, 3),
              "matching_siblings": matching_siblings}],
            {
                "recommendation_type": "no_expansion",
                "explanation": (
                    "This is not an expansion recommendation — it is an invitation to vary "
                    "the opening or closing emotional register intentionally. "
                    "Starting a book at a different tension level or resolving at a different "
                    "emotional note differentiates the reader experience across the series."
                ),
                "alternatives": [
                    {"option": "intentional_restraint",
                     "tradeoff": "Series conventions often benefit from consistent rhythm. "
                                 "Dismiss if the pattern is deliberate."}
                ],
            },
            now,
        )


# ── Full diagnostics run ──────────────────────────────────────────────────────

def run_pacing_diagnostics(
    db: "OrivellumDB",
    cfg: "OrivellumConfig",
    work_id: str,
    *,
    profile: str | None = None,
) -> dict:
    """Run all seven pacing detectors over all analyzed scenes for a work.

    Returns the run dict.  Findings are stored in pacing_findings.
    """
    with db._lock:
        wrow = db._conn.execute("SELECT id FROM works WHERE id=?", (work_id,)).fetchone()
    if not wrow:
        raise PacingError(f"Work {work_id!r} not found")

    resolved_profile = _profile_for(db, work_id, profile)
    profile_name = profile or "deep_immersive"

    run_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    with db._lock:
        db._conn.execute(
            """INSERT INTO pacing_runs
               (id, work_id, profile_name, status, coverage, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (run_id, work_id, profile_name, "running", "{}", now, now),
        )
        db._conn.commit()

    try:
        items = _scenes_with_metrics(db, work_id)
        analyzed = sum(1 for it in items if it["metrics"])
        coverage = {
            "total_scenes": len(items),
            "analyzed_scenes": analyzed,
            "profile": profile_name,
            "partial": analyzed < len(items),
        }

        if analyzed == 0:
            with db._lock:
                db._conn.execute(
                    """UPDATE pacing_runs SET status='done', coverage=?, updated_at=?
                       WHERE id=?""",
                    (json.dumps({**coverage,
                                 "note": "No scene metrics available. Run scene analysis first."}),
                     now, run_id),
                )
                db._conn.commit()
            return get_pacing_run(db, run_id)

        _detect_pacing_map(items, resolved_profile, run_id, work_id, db, now)
        _detect_breath_map(items, resolved_profile, run_id, work_id, db, now)
        _detect_compression(items, resolved_profile, run_id, work_id, db, now)
        _detect_drift(items, resolved_profile, run_id, work_id, db, now)
        _detect_book_boundary(items, resolved_profile, run_id, work_id, db, now)
        _detect_immersion_integrity(items, resolved_profile, run_id, work_id, db, now)
        _detect_series_rhythm(db, work_id, items, resolved_profile, run_id, now)

        with db._lock:
            db._conn.execute(
                """UPDATE pacing_runs SET status='done', coverage=?, updated_at=?
                   WHERE id=?""",
                (json.dumps(coverage), now, run_id),
            )
            db._conn.commit()

    except Exception as exc:
        logger.exception("Pacing diagnostics failed for work %s: %s", work_id, exc)
        with db._lock:
            db._conn.execute(
                """UPDATE pacing_runs SET status='failed', error=?, updated_at=? WHERE id=?""",
                (str(exc), datetime.now(UTC).isoformat(), run_id),
            )
            db._conn.commit()
        raise

    return get_pacing_run(db, run_id)


def get_pacing_run(db: "OrivellumDB", run_id: str) -> dict:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM pacing_runs WHERE id=?", (run_id,)
        ).fetchone()
    if not row:
        raise PacingError(f"Pacing run {run_id!r} not found")
    r = dict(row)
    try:
        r["coverage"] = json.loads(r.get("coverage") or "{}")
    except Exception:
        r["coverage"] = {}
    return r


def list_pacing_runs(db: "OrivellumDB", work_id: str) -> list[dict]:
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM pacing_runs WHERE work_id=? ORDER BY created_at DESC LIMIT 20",
            (work_id,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["coverage"] = json.loads(d.get("coverage") or "{}")
        except Exception:
            d["coverage"] = {}
        result.append(d)
    return result


def list_pacing_findings(db: "OrivellumDB", run_id: str) -> list[dict]:
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM pacing_findings WHERE run_id=? ORDER BY severity DESC, detector, created_at",
            (run_id,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        for field, default in [("evidence", "[]"), ("recommendation", "{}")]:
            try:
                d[field] = json.loads(d.get(field) or default)
            except Exception:
                d[field] = json.loads(default)
        result.append(d)
    return result


def resolve_pacing_finding(
    db: "OrivellumDB", finding_id: str, status: str, note: str = ""
) -> dict:
    VALID = {"open", "accepted", "intentional", "dismissed"}
    if status not in VALID:
        raise PacingError(f"Invalid status {status!r}; must be one of {sorted(VALID)}")
    now = datetime.now(UTC).isoformat()
    with db._lock:
        db._conn.execute(
            """UPDATE pacing_findings
               SET status=?, resolution_note=?, resolved_at=? WHERE id=?""",
            (status, note, now if status != "open" else None, finding_id),
        )
        db._conn.commit()
        row = db._conn.execute(
            "SELECT * FROM pacing_findings WHERE id=?", (finding_id,)
        ).fetchone()
    if not row:
        raise PacingError(f"Finding {finding_id!r} not found")
    d = dict(row)
    for field, default in [("evidence", "[]"), ("recommendation", "{}")]:
        try:
            d[field] = json.loads(d.get(field) or default)
        except Exception:
            d[field] = json.loads(default)
    return d


# ── Profile management ────────────────────────────────────────────────────────

def get_pacing_profile(db: "OrivellumDB", work_id: str) -> dict:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM pacing_profiles WHERE work_id=?", (work_id,)
        ).fetchone()
    if row:
        r = dict(row)
        try:
            r["thresholds"] = json.loads(r.get("thresholds") or "{}")
        except Exception:
            r["thresholds"] = {}
        r["available_profiles"] = {k: v["label"] for k, v in PROFILES.items()}
        return r
    # Return default
    return {
        "work_id": work_id,
        "profile_name": "deep_immersive",
        "thresholds": {},
        "available_profiles": {k: v["label"] for k, v in PROFILES.items()},
    }


def update_pacing_profile(
    db: "OrivellumDB", work_id: str, profile_name: str, thresholds: dict | None = None
) -> dict:
    if profile_name not in PROFILES:
        raise PacingError(f"Unknown profile {profile_name!r}; valid: {sorted(PROFILES)}")
    now = datetime.now(UTC).isoformat()
    pid = str(uuid.uuid4())
    t_json = json.dumps(thresholds or {})
    with db._lock:
        db._conn.execute(
            """INSERT INTO pacing_profiles (id, work_id, profile_name, thresholds, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(work_id) DO UPDATE SET
                 profile_name=excluded.profile_name,
                 thresholds=excluded.thresholds,
                 updated_at=excluded.updated_at""",
            (pid, work_id, profile_name, t_json, now),
        )
        db._conn.commit()
    return get_pacing_profile(db, work_id)

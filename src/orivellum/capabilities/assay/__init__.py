"""ASSAY — every quality check registered as a governed instrument (E4).

The instrument registry holds one Engine Contract record per check
(name, purpose, allowed/forbidden operations, authority relationship,
required output schema) with an honest three-tier authority level:

* **Tier 1** — deterministic, computable.  May block once CERTIFIED.
* **Tier 2** — evidence-verified.  May block once CERTIFIED; every
  finding is dispositionable.
* **Tier 3** — judge.  Advisory FOREVER; no accumulation of Tier-3
  scores ever adds up to permission to publish.

THE RULE: no instrument may block at Tier 1/2 until its certification
status is 'certified' — new instruments start 'advisory'.  Blocking is
computed (``is_blocking``), never stored, so a registry row can't lie.

Every instrument emits the standard finding schema:
Unit | Force Check | Issue Type | Severity | Classification | Action
(plus evidence JSON with verbatim quotes/offsets/metrics).

Certification/shadow-mode promotion is a separate milestone (PROMOTION)
and deliberately not implemented here.
"""

from __future__ import annotations

import logging
from typing import Any

from . import drift, gates, judge, metrics

logger = logging.getLogger(__name__)


class AssayError(RuntimeError):
    """A run failed in a way the caller must see (never silently)."""


_FINDING_SCHEMA = {
    "unit": "chapter N | story | act N",
    "force_check": "instrument key",
    "issue_type": "instrument-specific issue type",
    "severity": "critical|high|medium|low|info",
    "classification": "deterministic|confirmed|unconfirmed_signature|perspectival|structural",
    "action": "block_when_certified|author_review|advisory|review_regression",
    "evidence": "JSON: verbatim quotes with offsets, measures, scores",
}

_DETECTOR_CONTRACT = {
    "tier": 1,
    "variance": "deterministic",
    "allowed_ops": [
        "read chapter text",
        "compute prose-signature measures",
        "emit findings with verbatim quoted evidence",
    ],
    "forbidden_ops": [
        "modify prose",
        "block any transition while certification != certified",
        "assert a detection without quoted evidence",
    ],
    "authority_relationship": (
        "Tier 1 signature only — D14 pairs it with Tier 2 confirmation; "
        "a signature match alone never fails a chapter."
    ),
    "output_schema": _FINDING_SCHEMA,
    "origin": "Standards Concordance / A5 (G7)",
}

# ── The registry: one Engine Contract per instrument ─────────────────────────

CONTRACTS: list[dict] = [
    {
        "key": "voice.envelope",
        "name": "Voice Envelope Conformance",
        "tier": 2,
        "variance": "deterministic",
        "purpose": (
            "Compute the measurable voice metrics (sentence-length "
            "distribution, register bands, imagery density, per-character "
            "diction fingerprints) per chapter and compare against the "
            "stored target envelope."
        ),
        "allowed_ops": [
            "read chapter text and the stored voice baseline",
            "compute deterministic voice metrics",
            "emit per-chapter deviation findings",
        ],
        "forbidden_ops": [
            "invent a target envelope when none is stored",
            "block any transition while certification != certified",
        ],
        "authority_relationship": (
            "Tier 2: deviations are evidence-backed and dispositionable via "
            "author-accepted exceptions; blocks only once certified."
        ),
        "output_schema": _FINDING_SCHEMA,
        "scope": {},
        "thresholds": {"max_deviation_pct": 35.0},
        "origin": "A4 (G6) measurable baseline",
    },
    {
        "key": "drift.theology_lecture",
        "name": "Drift: Theology Lecture",
        "purpose": (
            "Detect dialogue collapsing into argued theological exposition: "
            "dialogue-to-exposition ratio below floor plus argumentative-"
            "register marker density above ceiling."
        ),
        "scope": {"prohibited": "entire book"},
        "thresholds": {
            "min_dialogue_ratio": 0.08,
            "max_argument_markers_per_1000_words": 6.0,
        },
        **_DETECTOR_CONTRACT,
    },
    {
        "key": "drift.catalog",
        "name": "Drift: Catalog",
        "purpose": (
            "Detect prose degrading into enumeration: long comma-series "
            "runs and list density above ceiling."
        ),
        "scope": {"prohibited": "entire book"},
        "thresholds": {"min_series_items": 4, "max_series_runs_per_1000_words": 3.0},
        **_DETECTOR_CONTRACT,
    },
    {
        "key": "drift.elihu",
        "name": "Drift: Elihu",
        "purpose": (
            "Detect the Elihu register: sustained assertive second-person "
            "monologue paragraphs. The signature is author-tunable via "
            "thresholds."
        ),
        "scope": {"prohibited": "entire book"},
        "thresholds": {
            "min_second_person_per_100_words": 4.0,
            "min_consecutive_paragraphs": 3,
        },
        **_DETECTOR_CONTRACT,
    },
    {
        "key": "drift.restoration",
        "name": "Drift: Restoration",
        "purpose": (
            "Detect resolution/restoration language appearing before its "
            "permitted chapter range."
        ),
        "scope": {"prohibited_before_chapter": 71},
        "thresholds": {"prohibited_before_chapter": 71},
        **_DETECTOR_CONTRACT,
    },
    {
        "key": "gate.d13",
        "name": "D13 Macro-Pacing",
        "tier": 1,
        "variance": "deterministic",
        "purpose": (
            "Act word shares computed from chapter positions and word "
            "distribution against per-act targets."
        ),
        "allowed_ops": [
            "read chapter word counts and the stored act targets",
            "compute per-act shares and deltas",
        ],
        "forbidden_ops": [
            "block any transition while certification != certified",
            "ask a model to judge pacing (this gate is arithmetic)",
        ],
        "authority_relationship": "Tier 1 deterministic gate; blocks once certified.",
        "output_schema": _FINDING_SCHEMA,
        "scope": {"acts": 4},
        "thresholds": {"share_tolerance": 0.30},
        "origin": "A3 (G5) macro-pacing targets",
    },
    {
        "key": "gate.d14",
        "name": "D14 Drift Detection",
        "tier": 2,
        "variance": "evidence",
        "purpose": (
            "Run the four drift detectors (Tier 1 signatures), then confirm "
            "each raw match with a Tier 2 evidence-check through the LLM "
            "gateway. Unconfirmed signatures stay advisory."
        ),
        "allowed_ops": [
            "run the four registered drift detectors",
            "confirm signature matches via the gateway at temperature 0",
            "emit confirmed findings (dispositionable) and unconfirmed advisories",
        ],
        "forbidden_ops": [
            "fail a chapter on an unconfirmed signature",
            "block any transition while certification != certified",
            "confirm without the original quoted evidence",
        ],
        "authority_relationship": (
            "Tier 1 signature + Tier 2 confirmation; blocks once certified."
        ),
        "output_schema": _FINDING_SCHEMA,
        "scope": {},
        "thresholds": {},
        "origin": "Standards Concordance D14",
    },
    {
        "key": "gate.d15",
        "name": "D15 Augmented Argument (ch. 45\u201355)",
        "tier": 2,
        "variance": "evidence",
        "purpose": (
            "Gather evidence on whether chapters 45\u201355 argue through "
            "dramatized experience. Evidence gathering opens ONLY on an "
            "author signature; go/no-go is the author's signature."
        ),
        "allowed_ops": [
            "check for an author signature before doing anything",
            "gather rubric-guided evidence annotations for chapters in range",
        ],
        "forbidden_ops": [
            "run any model call without an author signature",
            "emit a go/no-go verdict (only the author signs that)",
        ],
        "authority_relationship": (
            "Tier 2 with Tier 3 input; the gate opens on the author's "
            "signature and the signature is what enters the chain."
        ),
        "output_schema": _FINDING_SCHEMA,
        "scope": {"chapter_range": [45, 55]},
        "thresholds": {"max_chapters_per_run": 6},
        "origin": "Standards Concordance D15",
    },
    {
        "key": "gate.d16",
        "name": "D16 Theological Vertigo (ch. 55\u201370)",
        "tier": 2,
        "variance": "evidence",
        "purpose": (
            "Gather evidence on whether chapters 55\u201370 destabilize without "
            "premature resolution. Signature-gated exactly like D15."
        ),
        "allowed_ops": [
            "check for an author signature before doing anything",
            "gather rubric-guided evidence annotations for chapters in range",
        ],
        "forbidden_ops": [
            "run any model call without an author signature",
            "emit a go/no-go verdict (only the author signs that)",
        ],
        "authority_relationship": "Tier 2/3; go/no-go on the author's signature.",
        "output_schema": _FINDING_SCHEMA,
        "scope": {"chapter_range": [55, 70]},
        "thresholds": {"max_chapters_per_run": 6},
        "origin": "Standards Concordance D16",
    },
    {
        "key": "gate.d17",
        "name": "D17 Restoration Without Erasure (ch. 71\u201380)",
        "tier": 2,
        "variance": "evidence",
        "purpose": (
            "Tier 1 structural conditions (no resolution language before "
            "chapter 71; chapters 71\u201380 present) plus signature-gated "
            "evidence gathering on restoration preserving loss."
        ),
        "allowed_ops": [
            "run the deterministic structural conditions unconditionally",
            "gather rubric evidence only after an author signature",
        ],
        "forbidden_ops": [
            "emit a go/no-go verdict (only the author signs that)",
            "skip the structural conditions when unsigned",
        ],
        "authority_relationship": (
            "Tier 1 structural + Tier 3 judgment; go/no-go on the author's "
            "signature."
        ),
        "output_schema": _FINDING_SCHEMA,
        "scope": {"chapter_range": [71, 80]},
        "thresholds": {"max_chapters_per_run": 6, "prohibited_before_chapter": 71},
        "origin": "Standards Concordance D17",
    },
    {
        "key": "judge.hierarchical",
        "name": "Hierarchical Editorial Judge",
        "tier": 3,
        "variance": "perspectival",
        "purpose": (
            "Story/chapter/sentence editorial annotations plus pairwise "
            "rubric scoring of revision N against N\u22121. Produces annotations "
            "and preferences only — never a gate decision."
        ),
        "allowed_ops": [
            "annotate at story, chapter, and sentence level",
            "score revisions pairwise 0\u2013100 per rubric category",
            "surface revisions that score below their predecessor",
        ],
        "forbidden_ops": [
            "produce a gate decision or pass/fail verdict",
            "use the model that drafted the prose",
            "silently accept a scoring regression",
        ],
        "authority_relationship": (
            "Tier 3 — advisory forever. No accumulation of judge scores ever "
            "adds up to permission to publish."
        ),
        "output_schema": _FINDING_SCHEMA,
        "scope": {},
        "thresholds": {"max_chapters_per_run": 8},
        "origin": "B9 editorial review (MAGNET evaluation design)",
    },
]

INSTRUMENT_KEYS = [c["key"] for c in CONTRACTS]


def is_blocking(instrument: dict) -> bool:
    """THE rule, computed: Tier 1/2 blocks only once certified. Tier 3 never."""
    return int(instrument["tier"]) in (1, 2) and instrument["certification"] == "certified"


def seed_instruments(db: Any) -> int:
    """Idempotently register every contract. Preserves certification status."""
    count = 0
    for contract in CONTRACTS:
        db.upsert_assay_instrument(contract)
        count += 1
    return count


# ── Chapter access ───────────────────────────────────────────────────────────


def _load_chapters(db: Any, work_id: str, chapter_id: str | None = None) -> list[dict]:
    with db._lock:
        if chapter_id:
            rows = db._conn.execute(
                "SELECT id, seq, title, text FROM book_chapters WHERE work_id=? AND id=?",
                (work_id, chapter_id),
            ).fetchall()
        else:
            rows = db._conn.execute(
                "SELECT id, seq, title, text FROM book_chapters WHERE work_id=? ORDER BY seq",
                (work_id,),
            ).fetchall()
    chapters = [dict(r) for r in rows]
    if chapter_id and not chapters:
        raise AssayError(f"chapter {chapter_id!r} not found in work {work_id!r}")
    return chapters


def _reasoner_model(db: Any, cfg: Any) -> str:
    override = db.get_setting("reasoner_model_override", "") or ""
    return override or cfg.serving.reasoner_model


# ── The runner ───────────────────────────────────────────────────────────────


def run_instrument(
    db: Any,
    cfg: Any,
    *,
    key: str,
    work_id: str,
    chapter_id: str | None = None,
    run_id: str | None = None,
) -> dict:
    """Execute one registered instrument and record the run + findings.

    Returns the finished run dict.  Raises AssayError on failure (the run
    row is marked 'error' first — failures are recorded, never swallowed).
    """
    instrument = db.get_assay_instrument(key)
    if instrument is None:
        raise AssayError(f"instrument {key!r} is not registered")
    if instrument["certification"] == "retired":
        raise AssayError(f"instrument {key!r} is retired")
    if run_id is None:
        run_id = db.create_assay_run(
            instrument_id=instrument["id"], work_id=work_id, chapter_id=chapter_id
        )
    try:
        result = _dispatch(db, cfg, instrument, work_id, chapter_id)
    except Exception as exc:
        db.finish_assay_run(run_id, status="error", error=str(exc)[:500])
        raise
    findings = result.pop("findings", [])
    # Stamp the computed authority on every run: blocking is derived from
    # (tier, certification) at execution time — an advisory instrument's
    # verdict is a measurement, never a gate decision.
    evidence = result.get("evidence") or {}
    evidence["authority"] = {
        "tier": instrument["tier"],
        "certification": instrument["certification"],
        "blocking": is_blocking(instrument),
    }
    result["evidence"] = evidence
    for f in findings:
        db.create_assay_finding(
            run_id=run_id,
            instrument_id=instrument["id"],
            work_id=work_id,
            chapter_id=f.pop("chapter_id", None),
            unit=f["unit"],
            force_check=key,
            issue_type=f["issue_type"],
            severity=f["severity"],
            classification=f.get("classification", ""),
            action=f.get("action", ""),
            evidence=f.get("evidence", {}),
        )
    db.finish_assay_run(
        run_id,
        status="done",
        verdict=result.get("verdict"),
        score=result.get("score"),
        evidence=result.get("evidence", {}),
        findings_count=len(findings),
    )
    logger.info(
        "assay: instrument=%s work=%s verdict=%s findings=%d",
        key, work_id, result.get("verdict"), len(findings),
    )
    return db.get_assay_run(run_id)


def _dispatch(
    db: Any, cfg: Any, instrument: dict, work_id: str, chapter_id: str | None
) -> dict:
    key = instrument["key"]
    thresholds = instrument["thresholds"]
    scope = instrument["scope"]
    if key == "voice.envelope":
        return _run_voice_envelope(db, work_id, chapter_id, thresholds)
    if key in drift.DETECTORS or key == "drift.restoration":
        return _run_drift(db, key, work_id, chapter_id, thresholds)
    if key == "gate.d13":
        return _run_d13(db, work_id, scope, thresholds)
    if key == "gate.d14":
        return _run_d14(db, cfg, work_id)
    if key in ("gate.d15", "gate.d16", "gate.d17"):
        return _run_signature_gate(db, cfg, key, work_id, scope, thresholds)
    if key == "judge.hierarchical":
        return _run_judge(db, cfg, work_id, chapter_id, thresholds)
    raise AssayError(f"instrument {key!r} has no runner")


# ── Instrument implementations ───────────────────────────────────────────────


def _run_voice_envelope(
    db: Any, work_id: str, chapter_id: str | None, thresholds: dict
) -> dict:
    envelope = db.get_assay_baseline(work_id, "voice_envelope")
    chapters = _load_chapters(db, work_id, chapter_id)
    if not chapters:
        return {"verdict": "no_chapters", "score": None, "evidence": {}, "findings": []}
    names = (envelope or {}).get("character_names") or []
    max_dev = float(thresholds.get("max_deviation_pct", 35.0))
    per_chapter: list[dict] = []
    findings: list[dict] = []
    inside = 0
    for ch in chapters:
        m = metrics.compute_voice_metrics(
            ch["text"] or "", character_names=names, thresholds=thresholds
        )
        deviations = (
            metrics.compare_to_envelope(m, envelope["metrics"], max_deviation_pct=max_dev)
            if envelope and envelope.get("metrics")
            else []
        )
        per_chapter.append({"chapter": ch["seq"], "metrics": m, "deviations": deviations})
        if deviations:
            findings.append(
                {
                    "chapter_id": ch["id"],
                    "unit": f"chapter {ch['seq']}",
                    "issue_type": "voice_envelope_deviation",
                    "severity": "medium",
                    "classification": "deterministic",
                    "action": "author_review",
                    "evidence": {"deviations": deviations, "metrics": m},
                }
            )
        else:
            inside += 1
    if envelope is None or not envelope.get("metrics"):
        return {
            "verdict": "no_baseline",
            "score": None,
            "evidence": {"per_chapter": per_chapter, "note": "no stored target envelope"},
            "findings": [],
        }
    return {
        "verdict": "pass" if not findings else "deviations",
        "score": round(inside / len(chapters), 3),
        "evidence": {"per_chapter": per_chapter},
        "findings": findings,
    }


def _run_drift(
    db: Any, key: str, work_id: str, chapter_id: str | None, thresholds: dict
) -> dict:
    chapters = _load_chapters(db, work_id, chapter_id)
    findings: list[dict] = []
    flagged = 0
    for ch in chapters:
        if key == "drift.restoration":
            detections = drift.detect_restoration(ch["text"] or "", ch["seq"], thresholds)
        else:
            detections = drift.DETECTORS[key](ch["text"] or "", thresholds)
        if detections:
            flagged += 1
        for d in detections:
            findings.append(
                {
                    "chapter_id": ch["id"],
                    "unit": f"chapter {ch['seq']}",
                    "issue_type": d["issue_type"],
                    "severity": "medium",
                    "classification": "deterministic",
                    "action": "author_review",
                    "evidence": {"measures": d["measures"], "quotes": d["quotes"]},
                }
            )
    total = len(chapters) or 1
    return {
        "verdict": "clean" if not findings else "detected",
        "score": round(1 - flagged / total, 3),
        "evidence": {"chapters_checked": len(chapters), "chapters_flagged": flagged},
        "findings": findings,
    }


def _run_d13(db: Any, work_id: str, scope: dict, thresholds: dict) -> dict:
    chapters = [
        {"seq": c["seq"], "words": len(metrics.words_of(c["text"] or ""))}
        for c in _load_chapters(db, work_id)
    ]
    baseline = db.get_assay_baseline(work_id, "d13_targets")
    result = gates.run_d13(chapters, scope, thresholds, baseline)
    findings = [
        {
            "unit": f"act {a['act']}",
            "issue_type": "macro_pacing_off_target",
            "severity": "medium",
            "classification": "deterministic",
            "action": "block_when_certified",
            "evidence": a,
        }
        for a in result["acts"]
        if not a["within_tolerance"]
    ]
    return {
        "verdict": result["verdict"],
        "score": result["score"],
        "evidence": {k: v for k, v in result.items() if k != "verdict"},
        "findings": findings,
    }


def _run_d14(db: Any, cfg: Any, work_id: str) -> dict:
    chapters = _load_chapters(db, work_id)
    model = _reasoner_model(db, cfg)
    findings: list[dict] = []
    confirmed = unconfirmed = 0
    detector_keys = ["drift.theology_lecture", "drift.catalog", "drift.elihu", "drift.restoration"]
    for det_key in detector_keys:
        det = db.get_assay_instrument(det_key)
        th = det["thresholds"] if det else {}
        for ch in chapters:
            if det_key == "drift.restoration":
                detections = drift.detect_restoration(ch["text"] or "", ch["seq"], th)
            else:
                detections = drift.DETECTORS[det_key](ch["text"] or "", th)
            for d in detections:
                check = gates.confirm_detection(db, cfg, model, d, f"chapter {ch['seq']}")
                if check["confirmed"] is True:
                    confirmed += 1
                    sev, cls, action = "high", "confirmed", "block_when_certified"
                else:
                    unconfirmed += 1
                    sev, cls, action = "info", "unconfirmed_signature", "author_review"
                findings.append(
                    {
                        "chapter_id": ch["id"],
                        "unit": f"chapter {ch['seq']}",
                        "issue_type": d["issue_type"],
                        "severity": sev,
                        "classification": cls,
                        "action": action,
                        "evidence": {
                            "measures": d["measures"],
                            "quotes": d["quotes"],
                            "confirmation": check,
                        },
                    }
                )
    return {
        "verdict": "confirmed_drift" if confirmed else "clean",
        "score": None,
        "evidence": {
            "chapters_checked": len(chapters),
            "confirmed": confirmed,
            "unconfirmed": unconfirmed,
            "confirmation_model": model,
        },
        "findings": findings,
    }


def _d17_structural(
    db: Any,
    work_id: str,
    lo: int,
    hi: int,
    thresholds: dict,
    evidence: dict,
    findings: list[dict],
) -> bool:
    """D17's Tier-1 structural conditions (deterministic, run unsigned).

    Appends violations to ``findings``/``evidence``; returns True on failure.
    """
    prohibited_before = int(thresholds.get("prohibited_before_chapter", 71))
    violations: list[tuple[dict, dict]] = []
    present: set[int] = set()
    for ch in _load_chapters(db, work_id):
        present.add(ch["seq"])
        if ch["seq"] < prohibited_before:
            for d in drift.detect_restoration(
                ch["text"] or "", ch["seq"], {"prohibited_before_chapter": prohibited_before}
            ):
                violations.append((ch, d))
    missing = [s for s in range(lo, hi + 1) if s not in present]
    evidence["structural"] = {
        "early_restoration_chapters": [ch["seq"] for ch, _ in violations],
        "missing_chapters_in_range": missing,
    }
    for ch, d in violations:
        findings.append(
            {
                "chapter_id": ch["id"],
                "unit": f"chapter {ch['seq']}",
                "issue_type": "restoration_before_permitted",
                "severity": "high",
                "classification": "structural",
                "action": "block_when_certified",
                "evidence": {"measures": d["measures"], "quotes": d["quotes"]},
            }
        )
    return bool(violations or missing)


def _run_signature_gate(
    db: Any, cfg: Any, key: str, work_id: str, scope: dict, thresholds: dict
) -> dict:
    lo, hi = scope.get("chapter_range", gates.GATE_RANGES[key])
    signature = db.latest_assay_signature(work_id, key)
    evidence: dict = {"chapter_range": [lo, hi], "signature": signature}
    findings: list[dict] = []
    # D17 Tier-1 structural conditions run unconditionally.
    if key == "gate.d17":
        failed = _d17_structural(db, work_id, lo, hi, thresholds, evidence, findings)
        if failed:
            # A measurement, not a gate decision: the go/no-go on D17 is the
            # author's signature, always.
            return {
                "verdict": "structural_violations",
                "score": None,
                "evidence": evidence,
                "findings": findings,
            }
    if signature is None or signature["decision"] not in ("open", "go"):
        return {
            "verdict": "locked",
            "score": None,
            "evidence": {**evidence, "note": "evidence gathering opens only on author signature"},
            "findings": findings,
        }
    # Signed: gather rubric evidence via the judge model (never the drafter).
    model = judge.judge_model(db, cfg)
    rubric = gates.GATE_RUBRICS[key]
    cap = int(thresholds.get("max_chapters_per_run", 6))
    in_range = [c for c in _load_chapters(db, work_id) if lo <= c["seq"] <= hi][:cap]
    if not in_range:
        return {
            "verdict": "no_chapters_in_range",
            "score": None,
            "evidence": evidence,
            "findings": findings,
        }
    gathered = 0
    for ch in in_range:
        parsed = judge._call(
            db, cfg, model,
            f"assay.{key.split('.')[1]}.evidence",
            (
                f"{rubric}\n\nRespond ONLY with JSON: "
                '{"annotations": ["short annotation quoting the passage", ...]}\n\n'
                f"CHAPTER {ch['seq']}:\n{(ch['text'] or '')[:16000]}"
            ),
            max_tokens=800,
        )
        if parsed is None:
            raise AssayError(f"{key}: gateway evidence call failed for chapter {ch['seq']}")
        notes = parsed.get("annotations")
        if not isinstance(notes, list):
            raise AssayError(
                f"{key}: malformed evidence response for chapter {ch['seq']} "
                "(annotations must be a list)"
            )
        gathered += 1
        for note in notes[:8]:
            findings.append(
                {
                    "chapter_id": ch["id"],
                    "unit": f"chapter {ch['seq']}",
                    "issue_type": f"{key.split('.')[1]}_evidence",
                    "severity": "info",
                    "classification": "perspectival",
                    "action": "author_review",
                    "evidence": {"annotation": str(note)[:600], "model": model},
                }
            )
    evidence["chapters_reviewed"] = gathered
    evidence["model"] = model
    return {
        "verdict": "evidence_gathered",
        "score": None,
        "evidence": evidence,
        "findings": findings,
    }


def _validated_scores(raw: object, label: str, field: str) -> dict[str, float]:
    """Pairwise scores must be a map of numeric 0-100 values — fail loud."""
    if not isinstance(raw, dict) or not raw:
        raise AssayError(f"judge: malformed pairwise response for {label} ({field})")
    out: dict[str, float] = {}
    for cat, val in raw.items():
        if not isinstance(val, (int, float)) or isinstance(val, bool) or not 0 <= val <= 100:
            raise AssayError(
                f"judge: malformed pairwise score for {label} ({field}.{cat})"
            )
        out[str(cat)] = float(val)
    return out


def _judge_pairwise_step(
    db: Any,
    cfg: Any,
    model: str,
    work_id: str,
    ch: dict,
    text: str,
    pairwise_results: list[dict],
    findings: list[dict],
) -> None:
    """Pairwise-compare revision N against the stored N−1 snapshot.

    A revision that scores below its predecessor is surfaced as a
    'pairwise_regression' finding — never silently accepted.  The snapshot
    is then advanced to the current revision.
    """
    label = f"chapter {ch['seq']}"
    snap = db.get_assay_baseline(work_id, f"judge_snapshot:{ch['id']}")
    cur_hash = judge.text_hash(text)
    if snap and snap.get("hash") and snap["hash"] != cur_hash and snap.get("text"):
        pw = judge.judge_pairwise(db, cfg, model, label, snap["text"], text)
        if pw is None:
            raise AssayError(f"judge: pairwise gateway call failed for {label}")
        scores_prev = _validated_scores(pw.get("scores_a"), label, "scores_a")
        scores_cur = _validated_scores(pw.get("scores_b"), label, "scores_b")
        decreased = sorted(
            cat
            for cat, prev_score in scores_prev.items()
            if cat in scores_cur and scores_cur[cat] < prev_score
        )
        pw_row = {
            "chapter": ch["seq"],
            "preference": pw.get("preference"),
            "scores_previous": scores_prev,
            "scores_current": scores_cur,
            "decreased_categories": decreased,
            "reason": str(pw.get("reason", ""))[:400],
        }
        pairwise_results.append(pw_row)
        # Regression when the judge prefers the PREVIOUS revision OR any
        # rubric category scored below its predecessor — surfaced either way.
        if str(pw.get("preference", "")).upper() == "A" or decreased:
            findings.append(
                {
                    "chapter_id": ch["id"],
                    "unit": label,
                    "issue_type": "pairwise_regression",
                    "severity": "low",
                    "classification": "perspectival",
                    "action": "review_regression",
                    "evidence": pw_row,
                }
            )
    db.set_assay_baseline(
        work_id, f"judge_snapshot:{ch['id']}", {"hash": cur_hash, "text": text}
    )


def _judge_annotations_to_findings(
    ch: dict | None, level: str, parsed: dict, findings: list[dict]
) -> None:
    """Validate a judge response's annotations and convert them to findings.

    Malformed shapes raise (fail loud) — never stored unvalidated.
    """
    annotations = parsed.get("annotations")
    if not isinstance(annotations, dict):
        raise AssayError(f"judge: malformed {level}-level response (annotations must map)")
    for category, notes in annotations.items():
        if not isinstance(notes, list):
            raise AssayError(f"judge: malformed {level}-level response (category {category!r})")
        for note in notes[:6]:
            findings.append(
                {
                    "chapter_id": ch["id"] if ch else None,
                    "unit": f"chapter {ch['seq']}" if ch else "story",
                    "issue_type": f"{level}.{category}",
                    "severity": "info",
                    "classification": "perspectival",
                    "action": "advisory",
                    "evidence": {"annotation": str(note)[:600]},
                }
            )


def _run_judge(
    db: Any, cfg: Any, work_id: str, chapter_id: str | None, thresholds: dict
) -> dict:
    model = judge.judge_model(db, cfg)
    chapters = _load_chapters(db, work_id, chapter_id)
    if not chapters:
        return {"verdict": "advisory", "score": None, "evidence": {}, "findings": []}
    cap = int(thresholds.get("max_chapters_per_run", 8))
    findings: list[dict] = []
    evidence: dict = {"model": model, "levels": {}}

    def _annotations_to_findings(ch: dict | None, level: str, parsed: dict) -> None:
        _judge_annotations_to_findings(ch, level, parsed, findings)

    # Story level (whole-work runs only).
    if chapter_id is None:
        outline = "\n\n".join(
            f"CH {c['seq']} — {c['title'] or ''}\n{(c['text'] or '')[:400]}"
            for c in chapters
        )
        parsed = judge.judge_story(db, cfg, model, outline)
        if parsed is None:
            raise AssayError("judge: story-level gateway call failed")
        _annotations_to_findings(None, "story", parsed)
        evidence["levels"]["story"] = "done"

    # Chapter + sentence level + pairwise, per chapter (capped).
    pairwise_results: list[dict] = []
    for ch in chapters[:cap]:
        label = f"chapter {ch['seq']}"
        text = ch["text"] or ""
        parsed = judge.judge_chapter(db, cfg, model, label, text)
        if parsed is None:
            raise AssayError(f"judge: chapter-level gateway call failed for {label}")
        _annotations_to_findings(ch, "chapter", parsed)
        sampled = metrics.split_sentences(text)
        step = max(len(sampled) // 10, 1)
        parsed = judge.judge_sentences(db, cfg, model, sampled[::step][:12])
        if parsed is None:
            raise AssayError(f"judge: sentence-level gateway call failed for {label}")
        _annotations_to_findings(ch, "sentence", parsed)

        # Pairwise: revision N vs the stored previous revision snapshot.
        _judge_pairwise_step(
            db, cfg, model, work_id, ch, text, pairwise_results, findings
        )
    evidence["levels"]["chapter"] = evidence["levels"]["sentence"] = "done"
    evidence["pairwise"] = pairwise_results
    # Tier 3: the verdict is ALWAYS advisory — never pass/fail.
    return {"verdict": "advisory", "score": None, "evidence": evidence, "findings": findings}


def build_voice_baseline(
    db: Any,
    work_id: str,
    *,
    reference_text: str | None = None,
    character_names: list[str] | None = None,
) -> dict:
    """Compute + store the target voice envelope for a work.

    Uses the supplied reference passages, or (explicitly, never silently)
    raises when no reference text is given and the work has no chapters.
    """
    text = reference_text or ""
    source = "reference_text"
    if not text.strip():
        chapters = _load_chapters(db, work_id)
        text = "\n\n".join(c["text"] or "" for c in chapters)
        source = f"{len(chapters)} chapters"
    if not text.strip():
        raise AssayError("no reference text and no chapter text to build a baseline from")
    m = metrics.compute_voice_metrics(text, character_names=character_names or [])
    payload = {
        "metrics": m,
        "character_names": character_names or [],
        "source": source,
    }
    db.set_assay_baseline(work_id, "voice_envelope", payload)
    return payload


def contract_public(instrument: dict) -> dict:
    """Registry row shaped for the API, with the computed blocking flag."""
    return {**instrument, "blocking": is_blocking(instrument)}


__all__ = [
    "AssayError",
    "CONTRACTS",
    "INSTRUMENT_KEYS",
    "build_voice_baseline",
    "contract_public",
    "is_blocking",
    "run_instrument",
    "seed_instruments",
]

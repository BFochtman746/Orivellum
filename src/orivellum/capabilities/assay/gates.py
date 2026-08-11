"""ASSAY hard gates D13–D17 (registered, chapter-range-scoped).

Honest tiering, exactly as bound in the Standards Concordance:

* **D13** — macro-pacing per act.  Tier 1, fully deterministic: act word
  shares computed from chapter positions and word distribution against the
  per-act targets (stored per work, defaults to equal shares).
* **D14** — drift detection.  Tier 1 prose-signature detectors + Tier 2
  confirmation.  Raw signature matches are confirmed by an evidence-check
  call through the LLM gateway (temperature 0); unconfirmed matches stay
  advisory and are reported as such — a signature alone never fails a
  chapter.
* **D15/D16/D17** — the dimensions where a machine cannot rule and must
  not pretend to.  Evidence gathering opens ONLY on an author signature
  ('open'); the go/no-go is the author's signature, never a machine
  verdict.  D17 additionally has Tier-1 structural conditions (no
  resolution language before chapter 71; chapters 71–80 present).
"""

from __future__ import annotations

import json
from typing import Any

# Default chapter-range scopes for the signature gates.
GATE_RANGES = {
    "gate.d15": (45, 55),
    "gate.d16": (55, 70),
    "gate.d17": (71, 80),
}

GATE_RUBRICS = {
    "gate.d15": (
        "Augmented Argument: does this chapter make its argument through "
        "dramatized experience rather than stated proposition? Identify the "
        "strongest argumentative move and quote the passage that carries it. "
        "Note any passage where the argument is merely asserted."
    ),
    "gate.d16": (
        "Theological Vertigo: does this chapter destabilize the reader's "
        "settled frame without resolving it? Quote the passage that produces "
        "the vertigo, and any passage that prematurely re-stabilizes."
    ),
    "gate.d17": (
        "Restoration Without Erasure: does the restoration in this chapter "
        "preserve the memory and cost of what was lost? Quote passages where "
        "loss remains present inside restoration, and any passage where "
        "restoration erases or cheapens the loss."
    ),
}

_CONFIRM_SYSTEM = (
    "You are a strict evidence verifier for a prose quality check. You are "
    "given a suspected drift detection with quoted evidence from a chapter. "
    "Answer ONLY with JSON: {\"confirmed\": true|false, \"reason\": \"...\"}. "
    "Confirm ONLY if the quoted evidence genuinely exhibits the described "
    "failure mode in context. When in doubt, answer false."
)

_DRIFT_DESCRIPTIONS = {
    "theology_lecture": "dialogue collapsing into argued theological exposition",
    "catalog": "prose degrading into list-like enumeration",
    "elihu": "a sudden sustained assertive second-person monologue register",
    "restoration": "resolution/restoration language appearing before it is permitted",
}


def run_d13(chapters: list[dict], scope: dict, thresholds: dict, baseline: dict | None) -> dict:
    """Deterministic macro-pacing: act word shares vs per-act targets."""
    acts = int((baseline or {}).get("acts") or scope.get("acts") or 4)
    total_chapters = int(
        (baseline or {}).get("planned_chapters") or scope.get("planned_chapters") or 0
    ) or (max((c["seq"] for c in chapters), default=0))
    if not chapters or total_chapters <= 0:
        return {"verdict": "fail", "score": 0.0, "acts": [], "reason": "no chapters"}
    boundaries = (baseline or {}).get("act_boundaries") or [
        round(total_chapters * (i + 1) / acts) for i in range(acts)
    ]
    targets = (baseline or {}).get("act_word_shares") or [1.0 / acts] * acts
    tolerance = float(thresholds.get("share_tolerance", 0.30))  # relative
    total_words = sum(c["words"] for c in chapters) or 1
    act_rows: list[dict] = []
    ok = True
    prev = 0
    for i, bound in enumerate(boundaries):
        act_chaps = [c for c in chapters if prev < c["seq"] <= bound]
        words = sum(c["words"] for c in act_chaps)
        share = words / total_words
        target = float(targets[i]) if i < len(targets) else 1.0 / acts
        delta = abs(share - target) / target if target > 0 else 1.0
        within = delta <= tolerance
        ok = ok and within
        act_rows.append(
            {
                "act": i + 1,
                "chapters": f"{prev + 1}-{bound}",
                "chapter_count": len(act_chaps),
                "words": words,
                "share": round(share, 4),
                "target_share": round(target, 4),
                "relative_delta": round(delta, 3),
                "within_tolerance": within,
            }
        )
        prev = bound
    score = sum(1 for a in act_rows if a["within_tolerance"]) / max(len(act_rows), 1)
    return {
        "verdict": "pass" if ok else "fail",
        "score": round(score, 3),
        "acts": act_rows,
        "boundaries": boundaries,
        "tolerance": tolerance,
    }


def confirm_detection(
    db: Any, cfg: Any, model: str, detection: dict, chapter_label: str
) -> dict:
    """Tier-2 confirmation of one Tier-1 signature match (temperature 0)."""
    from ..llm import llm_call  # noqa: PLC0415 — late-bound for the gateway rule

    desc = _DRIFT_DESCRIPTIONS.get(detection.get("issue_type", ""), "prose drift")
    payload = {
        "failure_mode": detection.get("issue_type"),
        "description": desc,
        "chapter": chapter_label,
        "measures": detection.get("measures", {}),
        "quoted_evidence": [q["quote"] for q in detection.get("quotes", [])][:5],
    }
    result = llm_call(
        [
            {"role": "system", "content": _CONFIRM_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        cfg=cfg,
        model=model,
        db=db,
        purpose="assay.d14.confirm",
        temperature=0.0,
        max_tokens=300,
        timeout=90,
    )
    if not result.ok or not result.text:
        return {"confirmed": None, "reason": f"gateway unavailable: {result.error or 'no text'}"}
    try:
        raw = result.text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        parsed = json.loads(raw)
        return {
            "confirmed": bool(parsed.get("confirmed")),
            "reason": str(parsed.get("reason", ""))[:400],
        }
    except (ValueError, AttributeError):
        return {"confirmed": None, "reason": "unparseable confirmation response"}

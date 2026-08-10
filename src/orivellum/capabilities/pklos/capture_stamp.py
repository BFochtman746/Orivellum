"""Capture stamp — boundary provenance stamping + factual assertion detection.

CAP-REQ-001: Every factual input is stamped at the system boundary with:
  - channel (chat / file_ingest / api / user_assertion)
  - source_type (maps to authority tier)
  - raw text (for audit trail)
  - detected claims (structured subject/predicate/value tuples)

This module:
  1. Detects when a user message contains factual assertions about their system
  2. Extracts structured claims from those assertions (via LLM, background thread)
  3. Writes capture_stamp + claim records to the database

The detection is pattern-based (fast, zero latency on the hot path).
The extraction uses a lightweight LLM call in a background thread.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from .authority import SUBJECT_USER_SYSTEM, AuthorityTier, TTLClass

logger = logging.getLogger("orivellum.pklos.capture_stamp")

# ── Detection patterns ─────────────────────────────────────────────────────────

# Hardware nouns that signal a factual assertion when paired with "I have / my X is / running"
_HW_NOUN = (
    r"(?:ram|memory|vram|gb ram|gb vram|gb memory|"
    r"cpu|processor|cores?|threads?|"
    r"gpu|graphics card|video card|graphics adapter|"
    r"ssd|nvme|hdd|hard drive|storage|tb ssd|tb nvme|gb ssd|"
    r"motherboard|mobo|case|chassis|psu|power supply|"
    r"display|monitor|screen|resolution|refresh rate|hz|"
    r"os|operating system|windows|ubuntu|linux|macos|"
    r"ram stick|dimm|ddr[345]|"
    r"machine|system|pc|computer|rig|build|laptop|desktop|server)"
)

# Pattern 1 — "I have [quantity/model] [hw noun]"
_HAVE_PAT = re.compile(
    r"\bi (?:have|got|own|use)\s+(?:a |an |[0-9]+\s*)?"
    r"(?:[0-9]+\s*(?:gb|tb|mhz|ghz|watts?|w)\s+)?" + _HW_NOUN,
    re.IGNORECASE,
)

# Pattern 2 — "my [hw noun] is/has [value]"
_MY_IS_PAT = re.compile(
    r"\bmy\s+(?:\w+\s+)?" + _HW_NOUN + r"\s+(?:is|are|has|have|runs?|supports?)\b",
    re.IGNORECASE,
)

# Pattern 3 — "I'm running [OS/software version]"
_RUNNING_PAT = re.compile(
    r"\bi(?:'m| am) running\s+" + _HW_NOUN,
    re.IGNORECASE,
)

# Pattern 4 — direct quantity+unit statements about hardware
_QUANTITY_PAT = re.compile(
    r"\b([0-9]+(?:\.[0-9]+)?)\s*(gb|tb|mhz|ghz|watts?|w|cores?|threads?)\s+"
    r"(?:of\s+)?" + _HW_NOUN,
    re.IGNORECASE,
)

# Pattern 5 — "my specs are / my build is / my setup is"
_SPECS_PAT = re.compile(
    r"\bmy\s+(?:specs?|build|setup|rig|system|config(?:uration)?)\s*(?:is|are|:)\b",
    re.IGNORECASE,
)

_ASSERTION_PATTERNS = [
    _HAVE_PAT, _MY_IS_PAT, _RUNNING_PAT, _QUANTITY_PAT, _SPECS_PAT
]


def detect_factual_assertions(text: str) -> bool:
    """Return True if the message appears to contain factual system assertions.

    This is a fast pattern check — zero LLM calls.  A True result triggers
    the background capture thread; False means no extraction attempt.
    """
    for pat in _ASSERTION_PATTERNS:
        if pat.search(text):
            return True
    return False


# ── Extraction via LLM ─────────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """\
Extract all factual claims about the user's system, hardware, software, or \
personal configuration from the following message.

Return a JSON array of objects with these exact fields:
  subject    — always "user_system" for hardware/OS, "user_self" for personal facts
  predicate  — snake_case descriptor (e.g. "ram_gb", "cpu_model", "gpu_model",
                "os_name", "os_version", "storage_total_tb", "vram_gb")
  value      — the value as stated (string)
  unit       — optional unit string (e.g. "GB", "GHz", "W") or null
  ttl_class  — one of: PERMANENT, DURABLE, VOLATILE, EPHEMERAL

Rules:
- Only include things the user is asserting as true about their own system.
- Do NOT include aspirational or hypothetical statements.
- Do NOT include questions.
- If nothing qualifies, return an empty array [].
- Return ONLY valid JSON — no markdown, no explanation.

Message: {message}
"""


def extract_claims_from_text(
    text: str,
    base_url: str,
    model: str,
) -> list[dict]:
    """Use a lightweight LLM call to extract structured claims from text.

    Returns a list of {subject, predicate, value, unit, ttl_class} dicts.
    Returns [] on any failure — never raises.
    """
    try:
        import httpx
        prompt = _EXTRACTION_PROMPT.format(message=text[:1500])
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content":
                 "You extract structured facts from text. Return only valid JSON arrays."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 512,
            "stream": False,
        }
        resp = httpx.post(
            f"{base_url}/chat/completions",
            json=payload,
            timeout=8.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\n?", "", content)
            content = re.sub(r"\n?```$", "", content)
        claims = json.loads(content)
        if not isinstance(claims, list):
            return []
        return [c for c in claims if isinstance(c, dict)
                and "subject" in c and "predicate" in c and "value" in c]
    except Exception as exc:
        logger.debug("Claim extraction failed (non-fatal): %s", exc)
        return []


# ── Capture stamp writer ───────────────────────────────────────────────────────

class CaptureStamp:
    """Records a boundary provenance stamp and writes extracted claims."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def stamp_and_capture(
        self,
        text: str,
        *,
        channel: str = "chat",
        conv_id: str | None = None,
        base_url: str = "",
        model: str = "",
    ) -> list[str]:
        """Detect assertions in text, extract claims, stamp + write all.

        Returns a list of claim_ids that were created or updated.
        Always safe to call — any failure is logged and swallowed.
        """
        if not detect_factual_assertions(text):
            return []

        stamp_id = str(uuid.uuid4())
        claim_ids: list[str] = []

        try:
            # 1. Write the capture stamp (raw provenance record)
            self._db.create_capture_stamp(
                stamp_id=stamp_id,
                channel=channel,
                source_type="A7",  # user assertion
                raw_text=text[:2000],
                meta={"conv_id": conv_id} if conv_id else {},
            )

            # 2. Extract structured claims via LLM (if a model is available)
            extracted: list[dict] = []
            if base_url and model:
                extracted = extract_claims_from_text(text, base_url, model)

            # 3. Write each claim to the ledger
            for claim in extracted:
                subject   = str(claim.get("subject") or SUBJECT_USER_SYSTEM)
                predicate = str(claim.get("predicate") or "").strip()
                value     = str(claim.get("value") or "").strip()
                unit      = claim.get("unit")
                ttl       = str(claim.get("ttl_class") or TTLClass.DURABLE.value)

                if not predicate or not value:
                    continue

                cid = self._db.upsert_claim(
                    subject, predicate, value,
                    unit=unit,
                    authority_tier=AuthorityTier.A7.value,
                    source_id=stamp_id,
                    conv_id=conv_id,
                    ttl_class=ttl,
                    evidence_text=text[:500],
                )
                claim_ids.append(cid)
                logger.info(
                    "Claim captured from chat: %s.%s = %r (id=%s)",
                    subject, predicate, value, cid[:8]
                )

        except Exception as exc:
            logger.warning("capture_stamp.stamp_and_capture failed (non-fatal): %s", exc)

        return claim_ids

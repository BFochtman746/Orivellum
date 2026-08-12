"""Security floor tests for the mail Lemonade analyzer.

Pins the declared model-safety contract:
  * strict schema — excess fields, missing fields, or invalid enum values
    are rejected and replaced by the safe fallback (never a partial parse)
  * failure of ANY kind (HTTP error, oversized body, no JSON, connection
    refused, timeout) yields the neutral fallback, never a synthesised
    "safe" or "high-risk" verdict
  * signals are derived from the validated verdict + supplied threat
    evidence, and confidence is clamped to [0, 1]

No network — httpx.Client is monkeypatched.
"""

from __future__ import annotations

import httpx
import pytest

from orivellum.capabilities.mail import lemonade_analyzer as la
from orivellum.capabilities.mail.models import ThreatEvidence

_GOOD = {
    "attention_level": "high",
    "needs_reply": True,
    "rationale": "Invoice request from a known contact",
    "suggested_reply": "Thanks — attached.",
    "recommended_action": "CREATE_DRAFT",
    "confidence": 0.9,
    "is_high_risk": False,
    "injection_flagged": False,
}


# ── Schema validation ─────────────────────────────────────────────────────────


def test_valid_response_passes():
    assert la._validate_response(dict(_GOOD)) == _GOOD


def test_excess_fields_rejected():
    """The model must not be able to smuggle action tokens in extra keys."""
    assert la._validate_response({**_GOOD, "tool_call": "delete_all"}) is None


def test_missing_fields_rejected():
    bad = dict(_GOOD)
    del bad["confidence"]
    assert la._validate_response(bad) is None


def test_invalid_enum_values_rejected():
    assert la._validate_response({**_GOOD, "attention_level": "urgent!!"}) is None
    assert la._validate_response({**_GOOD, "recommended_action": "SEND_NOW"}) is None
    assert la._validate_response({**_GOOD, "confidence": "very"}) is None
    assert la._validate_response(["not", "a", "dict"]) is None


# ── Safe fallback shape ───────────────────────────────────────────────────────


def test_safe_fallback_is_neutral_defer():
    fb = la._safe_fallback("model down", "m1")
    assert fb.recommended_action == "DEFER"
    assert fb.needs_reply is False
    assert fb.confidence == 0.0
    assert fb.is_high_risk is False
    assert "assessment_failed" in fb.signals


# ── analyze() end-to-end with a faked endpoint ────────────────────────────────


class _Resp:
    def __init__(self, status_code=200, payload=None, content=b"{}"):
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload


def _client_returning(resp_or_exc):
    class _C:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **kw):
            if isinstance(resp_or_exc, Exception):
                raise resp_or_exc
            return resp_or_exc

    return _C


def _chat_payload(text):
    return {"choices": [{"message": {"content": text}}]}


_MSG = {
    "subject": "Hello",
    "from": {"emailAddress": {"address": "a@example.com", "name": "A"}},
    "body": {"content": "<p>Hi there</p>"},
}


def test_analyze_happy_path_with_threat_signal(monkeypatch):
    import json as _json

    resp = _Resp(payload=_chat_payload("```json\n" + _json.dumps(_GOOD) + "\n```"),
                 content=b"x" * 100)
    monkeypatch.setattr(la.httpx, "Client", _client_returning(resp))
    evidence = [ThreatEvidence(feed="openphish", indicator="bad.example.com",
                               indicator_type="domain", confidence=0.85, description="")]
    out = la.analyze(_MSG, evidence, model_id="m1")
    assert out.attention_level == "high"
    assert out.recommended_action == "CREATE_DRAFT"
    assert "threat_feed_hit" in out.signals
    assert out.model_id == "m1"


def test_analyze_clamps_confidence(monkeypatch):
    import json as _json

    resp = _Resp(payload=_chat_payload(_json.dumps({**_GOOD, "confidence": 7.5})),
                 content=b"x" * 100)
    monkeypatch.setattr(la.httpx, "Client", _client_returning(resp))
    assert la.analyze(_MSG, []).confidence == 1.0


@pytest.mark.parametrize(
    "resp_or_exc, reason_fragment",
    [
        (_Resp(status_code=500), "returned 500"),
        (_Resp(payload=_chat_payload("no json at all"), content=b"x" * 100), "No JSON"),
        (
            _Resp(payload=_chat_payload('{"attention_level": "high"}'), content=b"x" * 100),
            "schema validation",
        ),
        (
            _Resp(payload=_chat_payload("{}"), content=b"x" * (la._MAX_RESPONSE_BYTES + 1)),
            "too large",
        ),
        (httpx.ConnectError("refused"), "not running"),
        (httpx.ReadTimeout("slow"), "timed out"),
    ],
)
def test_analyze_every_failure_mode_falls_back(monkeypatch, resp_or_exc, reason_fragment):
    monkeypatch.setattr(la.httpx, "Client", _client_returning(resp_or_exc))
    out = la.analyze(_MSG, [])
    assert out.recommended_action == "DEFER"
    assert out.confidence == 0.0
    assert "assessment_failed" in out.signals
    assert reason_fragment.lower() in out.rationale.lower()

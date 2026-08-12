"""Tests for the ASSAY hard gates (D13 deterministic pacing + D14 confirmation).

gates.py is imported by an unattended path, so it sits under the security
floor.  Pins the honest-tiering rules:

  * run_d13 is fully deterministic — act shares vs targets with tolerance
  * confirm_detection requires a STRICT JSON boolean — "false"/"yes"/1
    never confirm; gateway failure yields confirmed=None (advisory), never
    a synthesised verdict
"""

from __future__ import annotations

from orivellum.capabilities.assay import gates


def _chapters(words_by_seq: dict[int, int]) -> list[dict]:
    return [{"seq": s, "words": w} for s, w in sorted(words_by_seq.items())]


# ── D13 macro-pacing ─────────────────────────────────────────────────────────


def test_d13_no_chapters_fails_honestly():
    out = gates.run_d13([], {}, {}, None)
    assert out["verdict"] == "fail"
    assert out["reason"] == "no chapters"


def test_d13_even_distribution_passes():
    # 4 acts over 8 chapters, 100 words each → every act share == target 0.25.
    chapters = _chapters({s: 100 for s in range(1, 9)})
    out = gates.run_d13(chapters, {"planned_chapters": 8, "acts": 4}, {}, None)
    assert out["verdict"] == "pass"
    assert out["score"] == 1.0
    assert all(a["within_tolerance"] for a in out["acts"])


def test_d13_lopsided_distribution_fails_the_right_act():
    # Act 1 (ch 1-2) hoards almost all the words.
    words = {1: 5000, 2: 5000, 3: 10, 4: 10, 5: 10, 6: 10, 7: 10, 8: 10}
    out = gates.run_d13(
        _chapters(words), {"planned_chapters": 8, "acts": 4}, {"share_tolerance": 0.3}, None
    )
    assert out["verdict"] == "fail"
    assert out["acts"][0]["within_tolerance"] is False
    assert out["score"] < 1.0


def test_d13_baseline_boundaries_and_targets_override_defaults():
    baseline = {
        "acts": 2,
        "planned_chapters": 4,
        "act_boundaries": [1, 4],
        "act_word_shares": [0.25, 0.75],
    }
    words = {1: 250, 2: 250, 3: 250, 4: 250}
    out = gates.run_d13(_chapters(words), {}, {"share_tolerance": 0.05}, baseline)
    # Act 1 share 0.25 == target; act 2 share 0.75 == target.
    assert out["verdict"] == "pass"
    assert out["boundaries"] == [1, 4]


# ── D14 confirmation strictness ───────────────────────────────────────────────


class _Result:
    def __init__(self, ok=True, text="", error=""):
        self.ok = ok
        self.text = text
        self.error = error


def _with_llm(monkeypatch, result):
    from orivellum.capabilities import llm

    captured = {}

    def _fake(messages, **kw):
        captured.update(kw)
        captured["messages"] = messages
        return result

    monkeypatch.setattr(llm, "llm_call", _fake)
    return captured


_DETECTION = {
    "issue_type": "catalog",
    "measures": {"list_ratio": 0.8},
    "quotes": [{"quote": "one, two, three, four"}],
}


def test_confirm_detection_true_boolean_confirms(monkeypatch):
    captured = _with_llm(
        monkeypatch, _Result(text='{"confirmed": true, "reason": "genuine enumeration"}')
    )
    out = gates.confirm_detection(None, None, "m", _DETECTION, "Ch 3")
    assert out["confirmed"] is True
    assert captured["temperature"] == 0.0, "Tier-2 confirmation must run at temperature 0"


def test_confirm_detection_rejects_non_boolean_verdicts(monkeypatch):
    for text in ['{"confirmed": "true"}', '{"confirmed": 1}', '{"confirmed": "yes"}', "[true]"]:
        _with_llm(monkeypatch, _Result(text=text))
        out = gates.confirm_detection(None, None, "m", _DETECTION, "Ch 3")
        assert out["confirmed"] is None, f"non-boolean {text!r} must never confirm"


def test_confirm_detection_gateway_failure_is_advisory_not_a_verdict(monkeypatch):
    _with_llm(monkeypatch, _Result(ok=False, error="down"))
    out = gates.confirm_detection(None, None, "m", _DETECTION, "Ch 3")
    assert out["confirmed"] is None
    assert "unavailable" in out["reason"]


def test_confirm_detection_unparseable_and_fenced_json(monkeypatch):
    _with_llm(monkeypatch, _Result(text="the model rambles"))
    assert gates.confirm_detection(None, None, "m", _DETECTION, "Ch 3")["confirmed"] is None

    _with_llm(monkeypatch, _Result(text='```json\n{"confirmed": false, "reason": "ok"}\n```'))
    out = gates.confirm_detection(None, None, "m", _DETECTION, "Ch 3")
    assert out["confirmed"] is False

"""Verify the Voice Designer keyword-fallback path when the LLM is offline.

Task: Confirm POST /api/studio/voices/design returns sensible results when the
AI server is unreachable or returns unusable output, and that the response
shape is fully compatible with the mobile VoiceDesignerCard UI.

Import errors are intentionally NOT suppressed — if the project environment is
missing dependencies this file will error at collection time (visible failure)
rather than silently skip all 35 assertions.

Test phases
-----------
A  LLM offline  → keyword fallback fires and returns 200 with 3 match cards.
B  LLM returns bad JSON / empty text / unknown voice IDs → falls through to keyword fallback.
C  Keyword scoring correctness  → relevant descriptions rank relevant voices.
D  Response shape  → every field the mobile UI reads is present and non-empty.
E  Input validation  → 400 errors still surface without an LLM.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Bootstrap — direct imports; no try/except so failures are collection errors.
# ---------------------------------------------------------------------------
os.environ.setdefault("SESSION_SECRET", "test-orivellum-api-key-12345")
_AUTH_HEADERS = {"X-Api-Key": os.environ["SESSION_SECRET"]}

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "artifacts" / "api-server" / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from orivellum.api import _deps  # noqa: E402
from orivellum.api.app import create_app  # noqa: E402
from orivellum.capabilities.llm import LLMResult  # noqa: E402
from orivellum.configuration.config import (  # noqa: E402
    OrivellumConfig,
    ServingConfig,
)
from orivellum.database.db import OrivellumDB  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(tmp_path: Path) -> TestClient:
    """Wire a throwaway DB + unreachable-AI config, return a TestClient."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db = OrivellumDB(str(data_dir / "test.db"))
    cfg = OrivellumConfig(
        data_dir=str(data_dir),
        # Port 99999 is never reachable → forces the keyword-fallback path.
        serving=ServingConfig(base_url="http://localhost:99999/api/v1"),
    )
    _deps.init(db=db, cfg=cfg)
    app = create_app()
    return TestClient(app, raise_server_exceptions=False, headers=_AUTH_HEADERS)


def _llm_offline() -> LLMResult:
    return LLMResult(None, False, "", 0, error="connection refused")


def _llm_bad_json() -> LLMResult:
    return LLMResult("this is not json {{{", True, "workhorse", 120)


def _llm_empty_text() -> LLMResult:
    return LLMResult("", True, "workhorse", 50)


def _llm_unknown_ids() -> LLMResult:
    """LLM returns parseable JSON but every voice_id is not in the catalog."""
    payload = json.dumps(
        {
            "target_dimensions": {
                "warmth": 7,
                "authority": 5,
                "gravitas": 8,
                "pace": 5,
                "brightness": 4,
                "age": 7,
            },
            "interpretation": "Rich, authoritative narrator",
            "matches": [
                {"voice_id": "nonexistent_voice_xyz", "match_score": 91, "why": "Deep gravitas"},
                {"voice_id": "also_not_real_abc", "match_score": 88, "why": "Strong authority"},
                {"voice_id": "still_fake_123", "match_score": 82, "why": "Mature tone"},
            ],
        }
    )
    return LLMResult(payload, True, "workhorse", 200)


# ── shared assertion helpers ─────────────────────────────────────────────────


def _assert_fallback_contract(test: unittest.TestCase, data: dict) -> None:
    """Assert the three-card keyword-fallback contract that the mobile UI requires."""
    from orivellum.api.routes.studio import _VOICE_BY_ID

    matches = data.get("matches", [])
    test.assertEqual(
        len(matches), 3, f"Keyword fallback must return exactly 3 cards, got {len(matches)}"
    )

    test.assertIsInstance(data.get("target_dimensions"), dict)
    test.assertEqual(data["target_dimensions"], {}, "Keyword fallback sets target_dimensions={}")

    interp = data.get("interpretation", "")
    lower = interp.lower()
    test.assertTrue(
        "keyword" in lower or "unavailable" in lower,
        f"Fallback interpretation should mention keyword/unavailable: {interp!r}",
    )

    ids = [m["voice_id"] for m in matches]
    test.assertEqual(len(ids), len(set(ids)), f"Duplicate voice IDs: {ids}")

    for i, m in enumerate(matches):
        test.assertIn(m["voice_id"], _VOICE_BY_ID, f"match[{i}].voice_id not in catalog")
        test.assertEqual(m["match_score"], 75, f"match[{i}].match_score expected 75")
        test.assertGreater(len(m.get("why", "")), 0, f"match[{i}].why is empty")
        voice = m.get("voice", {})
        test.assertGreater(len(voice.get("name", "")), 0, f"match[{i}].voice.name empty")
        test.assertEqual(voice.get("id"), m["voice_id"], f"match[{i}] voice.id != voice_id")


# ---------------------------------------------------------------------------
# Phase A — LLM offline → keyword fallback
# ---------------------------------------------------------------------------


class TestKeywordFallbackOfflineLLM(unittest.TestCase):
    """Keyword fallback fires correctly when LLM returns ok=False."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _design(self, description: str = "deep, ancient male voice with gravitas") -> dict:
        with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_offline()):
            resp = self.client.post(
                "/api/studio/voices/design",
                json={"description": description},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_returns_200_not_5xx(self):
        """Keyword fallback must return 200, never a 500 or 503."""
        with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_offline()):
            resp = self.client.post(
                "/api/studio/voices/design",
                json={"description": "deep gravitas male"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_full_fallback_contract(self):
        """Three cards, valid IDs, non-empty why, score=75, keyword interpretation."""
        data = self._design()
        _assert_fallback_contract(self, data)

    def test_interpretation_mentions_keyword_or_unavailable(self):
        data = self._design()
        interp = data.get("interpretation", "")
        self.assertGreater(len(interp.strip()), 0, "interpretation must not be empty")
        lower = interp.lower()
        self.assertTrue(
            "keyword" in lower or "unavailable" in lower,
            f"Expected 'keyword' or 'unavailable' in: {interp!r}",
        )

    def test_target_dimensions_empty_in_fallback(self):
        data = self._design()
        self.assertEqual(data.get("target_dimensions"), {})

    def test_description_echoed_in_response(self):
        desc = "calm, contemplative, slow-paced"
        data = self._design(desc)
        self.assertEqual(data.get("description"), desc)

    def test_why_equals_voice_catalog_description(self):
        """In fallback, 'why' is the voice's catalog description, not an LLM rationale."""
        from orivellum.api.routes.studio import _VOICE_BY_ID

        data = self._design()
        for i, m in enumerate(data["matches"]):
            expected = _VOICE_BY_ID[m["voice_id"]]["description"]
            self.assertEqual(m["why"], expected, f"match[{i}].why should equal catalog description")

    def test_first_match_is_best_match_candidate(self):
        """BEST MATCH badge renders on index 0 — first match must always exist."""
        data = self._design()
        matches = data.get("matches", [])
        self.assertGreater(len(matches), 0)
        first = matches[0]
        for field in ("voice_id", "why", "match_score", "voice"):
            self.assertIn(field, first, f"First match missing '{field}'")

    def test_all_required_top_level_fields_present(self):
        data = self._design()
        for field in ("description", "interpretation", "matches", "target_dimensions"):
            self.assertIn(field, data, f"Top-level field '{field}' missing")

    def test_voice_name_present_for_use_button(self):
        """'Use {v.name}' button — voice.name must be non-empty."""
        data = self._design()
        for i, m in enumerate(data["matches"]):
            name = m.get("voice", {}).get("name", "")
            self.assertGreater(
                len(name), 0, f"match[{i}].voice.name empty — Use button shows 'Use '"
            )

    def test_score_in_amber_band_not_grey(self):
        """score >= 85 → green, >= 70 → amber, else grey.
        Fallback score of 75 must be in the amber (≥70) band."""
        data = self._design()
        for i, m in enumerate(data["matches"]):
            self.assertGreaterEqual(
                m.get("match_score", 0),
                70,
                f"match[{i}] score in grey band — UI shows muted colour",
            )

    def test_gender_field_present_for_gender_symbol(self):
        data = self._design()
        for i, m in enumerate(data["matches"]):
            self.assertIn("gender", m.get("voice", {}), f"match[{i}].voice missing 'gender'")

    def test_accent_field_present_in_voice_object(self):
        data = self._design()
        for i, m in enumerate(data["matches"]):
            self.assertIn("accent", m.get("voice", {}), f"match[{i}].voice missing 'accent' key")


# ---------------------------------------------------------------------------
# Phase B — LLM returns bad output → falls through to keyword fallback
# ---------------------------------------------------------------------------


class TestKeywordFallbackBadLLMOutput(unittest.TestCase):
    """Keyword fallback fires when the LLM returns ok=True but unusable output."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _design(
        self, llm_result: LLMResult, description: str = "warm intimate British female"
    ) -> dict:
        with patch("orivellum.capabilities.llm.llm_call", return_value=llm_result):
            resp = self.client.post(
                "/api/studio/voices/design",
                json={"description": description},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_invalid_json_triggers_fallback(self):
        """Garbled JSON → keyword fallback; 3 catalog cards returned."""
        data = self._design(_llm_bad_json())
        _assert_fallback_contract(self, data)

    def test_empty_text_triggers_fallback(self):
        """Empty LLM text → keyword fallback; 3 catalog cards returned."""
        data = self._design(_llm_empty_text())
        _assert_fallback_contract(self, data)

    def test_unknown_voice_ids_triggers_fallback(self):
        """LLM returns valid JSON but every voice_id is unknown.

        The server must fall through to keyword fallback and return 3 real
        catalog cards — not an empty matches list that leaves the UI blank.
        """
        data = self._design(_llm_unknown_ids(), description="deep gravitas ancient male")
        _assert_fallback_contract(self, data)

    def test_unknown_ids_fallback_uses_keyword_scores(self):
        """When unknown-ID fallback fires, scores are 75 and why is catalog description."""
        from orivellum.api.routes.studio import _VOICE_BY_ID

        data = self._design(_llm_unknown_ids(), description="deep gravitas male ancient")
        for i, m in enumerate(data["matches"]):
            self.assertEqual(
                m["match_score"], 75, f"match[{i}].match_score should be 75 (keyword fallback)"
            )
            expected_why = _VOICE_BY_ID[m["voice_id"]]["description"]
            self.assertEqual(
                m["why"], expected_why, f"match[{i}].why should be catalog description in fallback"
            )

    def test_partial_unknown_ids_returns_valid_subset(self):
        """LLM returns 3 matches; 2 unknown, 1 valid → valid ones used, rest keyword-filled."""
        from orivellum.api.routes.studio import _VOICE_BY_ID

        # Pick a real voice ID from the catalog
        real_id = next(iter(_VOICE_BY_ID))
        payload = json.dumps(
            {
                "target_dimensions": {"warmth": 6},
                "interpretation": "Suitable narrator",
                "matches": [
                    {"voice_id": "fake_aaa", "match_score": 95, "why": "Best pick"},
                    {"voice_id": real_id, "match_score": 88, "why": "Good fit"},
                    {"voice_id": "fake_bbb", "match_score": 80, "why": "Also good"},
                ],
            }
        )
        llm_result = LLMResult(payload, True, "workhorse", 150)
        with patch("orivellum.capabilities.llm.llm_call", return_value=llm_result):
            resp = self.client.post(
                "/api/studio/voices/design",
                json={"description": "warm friendly narrator"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        ids = [m["voice_id"] for m in data.get("matches", [])]
        # Every returned ID must be in the catalog
        for vid in ids:
            self.assertIn(vid, _VOICE_BY_ID, f"{vid!r} returned but not in catalog")
        # At least one match should exist (the real_id from LLM succeeded)
        self.assertGreater(len(ids), 0)


# ---------------------------------------------------------------------------
# Phase C — Keyword scoring correctness
# ---------------------------------------------------------------------------


class TestKeywordScoringRelevance(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _fallback(self, description: str) -> dict:
        with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_offline()):
            resp = self.client.post(
                "/api/studio/voices/design",
                json={"description": description},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_masculine_keyword_surfaces_masculine_voices(self):
        from orivellum.api.routes.studio import _VOICE_BY_ID

        data = self._fallback("deep commanding male authoritative voice")
        genders = [_VOICE_BY_ID.get(m["voice_id"], {}).get("gender", "") for m in data["matches"]]
        self.assertGreaterEqual(
            genders.count("masculine"), 1, f"Expected ≥1 masculine voice, got {genders}"
        )

    def test_feminine_keyword_surfaces_feminine_voices(self):
        from orivellum.api.routes.studio import _VOICE_BY_ID

        data = self._fallback("intimate warm feminine woman narrator")
        genders = [_VOICE_BY_ID.get(m["voice_id"], {}).get("gender", "") for m in data["matches"]]
        self.assertGreaterEqual(
            genders.count("feminine"), 1, f"Expected ≥1 feminine voice, got {genders}"
        )

    def test_british_keyword_surfaces_british_accent(self):
        from orivellum.api.routes.studio import _VOICE_BY_ID

        data = self._fallback("british BBC documentary narrator classic english")
        accents = [_VOICE_BY_ID.get(m["voice_id"], {}).get("accent", "") for m in data["matches"]]
        self.assertGreaterEqual(
            accents.count("british"), 1, f"Expected ≥1 british voice, got {accents}"
        )

    def test_all_descriptions_return_three_matches(self):
        for desc in [
            "warm friendly intimate",
            "deep ancient gravitas solemn biblical",
            "bright energetic young adventure",
            "calm contemplative meditative wise elder",
            "authoritative powerful strong",
        ]:
            data = self._fallback(desc)
            self.assertEqual(len(data["matches"]), 3, f"Expected 3 matches for {desc!r}")

    def test_no_duplicate_voice_ids(self):
        data = self._fallback("deep gravitas male ancient wise prophet")
        ids = [m["voice_id"] for m in data["matches"]]
        self.assertEqual(len(ids), len(set(ids)), f"Duplicates: {ids}")


# ---------------------------------------------------------------------------
# Phase D — Mobile UI field compatibility
# ---------------------------------------------------------------------------


class TestMobileUIFieldCompatibility(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _fallback_data(self, description: str = "warm intimate personal") -> dict:
        with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_offline()):
            return self.client.post(
                "/api/studio/voices/design",
                json={"description": description},
            ).json()

    def test_match_score_is_numeric(self):
        for i, m in enumerate(self._fallback_data()["matches"]):
            self.assertIsInstance(
                m.get("match_score"), (int, float), f"match[{i}].match_score must be numeric"
            )

    def test_voice_id_matches_voice_object_id(self):
        for i, m in enumerate(self._fallback_data()["matches"]):
            self.assertEqual(m["voice_id"], m["voice"]["id"], f"match[{i}]: voice_id != voice.id")

    def test_interpretation_is_string(self):
        interp = self._fallback_data().get("interpretation")
        self.assertIsNotNone(interp)
        self.assertIsInstance(interp, str)

    def test_matches_is_list(self):
        self.assertIsInstance(self._fallback_data().get("matches"), list)

    def test_why_rendered_when_truthy(self):
        for i, m in enumerate(self._fallback_data()["matches"]):
            self.assertTrue(
                bool(m.get("why", "")), f"match[{i}].why falsy — rationale row won't render"
            )


# ---------------------------------------------------------------------------
# Phase E — Input validation (fires before LLM is consulted)
# ---------------------------------------------------------------------------


class TestInputValidationWithoutLLM(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_description_returns_400(self):
        resp = self.client.post("/api/studio/voices/design", json={"description": ""})
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_whitespace_only_returns_400(self):
        resp = self.client.post("/api/studio/voices/design", json={"description": "   \t\n"})
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_too_long_returns_400(self):
        resp = self.client.post("/api/studio/voices/design", json={"description": "x" * 501})
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_exactly_500_chars_accepted(self):
        with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_offline()):
            resp = self.client.post("/api/studio/voices/design", json={"description": "a" * 500})
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_missing_description_field_returns_422(self):
        resp = self.client.post("/api/studio/voices/design", json={})
        self.assertEqual(resp.status_code, 422, resp.text)


# ---------------------------------------------------------------------------
# Phase F — LLM online → success path
# ---------------------------------------------------------------------------


def _real_voice_ids(n: int = 3) -> list[str]:
    """Return the first *n* voice IDs from the catalog (always real, always ordered)."""
    from orivellum.api.routes.studio import _VOICE_BY_ID

    return list(_VOICE_BY_ID.keys())[:n]


def _llm_success(voice_ids: list[str]) -> LLMResult:
    """Return a well-formed LLM response that uses real catalog IDs.

    Scores, rationales, and interpretation are all deliberately distinct from
    the keyword-fallback values so tests can tell the two paths apart.
    """
    payload = json.dumps(
        {
            "target_dimensions": {
                "warmth": 8,
                "authority": 6,
                "gravitas": 9,
                "pace": 4,
                "brightness": 3,
                "age": 8,
            },
            "interpretation": (
                "A rich, sonorous voice with deep gravitas suited to epic historical narration."
            ),
            "matches": [
                {
                    "voice_id": voice_ids[0],
                    "match_score": 94,
                    "why": "Exceptional warmth and gravitas alignment with the requested tone.",
                },
                {
                    "voice_id": voice_ids[1],
                    "match_score": 87,
                    "why": "Strong authority dimensions complement the historical register.",
                },
                {
                    "voice_id": voice_ids[2],
                    "match_score": 81,
                    "why": "Measured pace and elevated age score match the elder narrator profile.",
                },
            ],
        }
    )
    return LLMResult(payload, True, "workhorse", 300)


def _llm_success_fenced(voice_ids: list[str]) -> LLMResult:
    """Same payload as _llm_success but wrapped in markdown code fences."""
    inner = _llm_success(voice_ids)
    fenced = f"```json\n{inner.text}\n```"
    return LLMResult(fenced, True, "workhorse", 310)


# Expected LLM rationales (match order must align with _llm_success)
_LLM_WHYS = [
    "Exceptional warmth and gravitas alignment with the requested tone.",
    "Strong authority dimensions complement the historical register.",
    "Measured pace and elevated age score match the elder narrator profile.",
]
_LLM_INTERPRETATION = (
    "A rich, sonorous voice with deep gravitas suited to epic historical narration."
)
_LLM_DIMS = {"warmth": 8, "authority": 6, "gravitas": 9, "pace": 4, "brightness": 3, "age": 8}


class TestLLMSuccessPath(unittest.TestCase):
    """Phase F — LLM online path: well-formed JSON drives the full response.

    Every test patches llm_call to return a correct, well-formed response so the
    LLM-success branch (lines ~1026–1051 in studio.py) is exercised exclusively.

    Assertions mirror the five acceptance criteria from the task brief:
      1. match_score > 75  (LLM value, not the keyword-fallback constant)
      2. why is the LLM rationale, not the voice catalog description
      3. voice objects carry name, gender, accent from the catalog
      4. target_dimensions holds numeric values (not {})
      5. interpretation echoes the LLM's "interpretation" field
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.client = _make_client(Path(self._tmp.name))
        self._ids = _real_voice_ids(3)

    def tearDown(self):
        self._tmp.cleanup()

    def _design(
        self,
        description: str = "deep gravitas historical ancient male",
        llm_result: LLMResult | None = None,
    ) -> dict:
        result = llm_result if llm_result is not None else _llm_success(self._ids)
        with patch("orivellum.capabilities.llm.llm_call", return_value=result):
            resp = self.client.post(
                "/api/studio/voices/design",
                json={"description": description},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    # ── Criterion 1: match_score > 75 ────────────────────────────────────────

    def test_match_scores_exceed_fallback_constant(self):
        """LLM-path match_score values (94, 87, 81) must all exceed the keyword-
        fallback constant (75) so the UI renders in the correct colour band."""
        data = self._design()
        expected = [94, 87, 81]
        for i, m in enumerate(data["matches"]):
            self.assertGreater(
                m["match_score"],
                75,
                f"match[{i}].match_score={m['match_score']} ≤ 75 — keyword fallback fired unexpectedly",
            )
            self.assertEqual(
                m["match_score"],
                expected[i],
                f"match[{i}].match_score should be {expected[i]} (LLM value)",
            )

    def test_all_scores_in_green_band(self):
        """Scores ≥85 → green badge; all three LLM scores (94, 87, 81) should clear 80."""
        data = self._design()
        for i, m in enumerate(data["matches"]):
            self.assertGreaterEqual(
                m["match_score"],
                80,
                f"match[{i}].match_score={m['match_score']} below expected LLM range",
            )

    # ── Criterion 2: why is LLM rationale, not catalog description ───────────

    def test_why_is_llm_rationale_not_catalog_description(self):
        """LLM-path 'why' must be the model's rationale string, not the catalog entry."""
        from orivellum.api.routes.studio import _VOICE_BY_ID

        data = self._design()
        for i, m in enumerate(data["matches"]):
            catalog_desc = _VOICE_BY_ID[m["voice_id"]]["description"]
            self.assertNotEqual(
                m["why"],
                catalog_desc,
                f"match[{i}].why equals catalog description — fallback 'why' was used instead of LLM",
            )
            self.assertEqual(
                m["why"],
                _LLM_WHYS[i],
                f"match[{i}].why does not match LLM rationale.\n"
                f"  expected: {_LLM_WHYS[i]!r}\n"
                f"  got:      {m['why']!r}",
            )

    def test_why_fields_are_non_empty(self):
        data = self._design()
        for i, m in enumerate(data["matches"]):
            self.assertTrue(
                bool(m.get("why", "").strip()),
                f"match[{i}].why is empty — rationale row won't render on mobile",
            )

    # ── Criterion 3: voice objects enriched ──────────────────────────────────

    def test_voice_objects_have_name(self):
        """'Use {v.name}' action button requires voice.name to be non-empty."""
        data = self._design()
        for i, m in enumerate(data["matches"]):
            self.assertGreater(
                len(m.get("voice", {}).get("name", "")),
                0,
                f"match[{i}].voice.name empty — Use button shows 'Use '",
            )

    def test_voice_objects_have_gender(self):
        """Gender symbol (♀ / ♂) requires voice.gender to be present."""
        data = self._design()
        for i, m in enumerate(data["matches"]):
            self.assertIn("gender", m.get("voice", {}), f"match[{i}].voice missing 'gender'")

    def test_voice_objects_have_accent(self):
        """Accent badge colour requires voice.accent to be present."""
        data = self._design()
        for i, m in enumerate(data["matches"]):
            self.assertIn("accent", m.get("voice", {}), f"match[{i}].voice missing 'accent'")

    def test_voice_id_matches_voice_object_id(self):
        """Mobile card uses both m.voice_id and m.voice.id — they must agree."""
        data = self._design()
        for i, m in enumerate(data["matches"]):
            self.assertEqual(
                m["voice_id"],
                m["voice"]["id"],
                f"match[{i}]: voice_id={m['voice_id']!r} != voice.id={m['voice']['id']!r}",
            )

    def test_three_enriched_matches_returned(self):
        """LLM provided 3 valid IDs → 3 enriched matches, not fewer."""
        data = self._design()
        self.assertEqual(
            len(data["matches"]),
            3,
            f"Expected 3 matches from LLM success path, got {len(data['matches'])}",
        )

    # ── Criterion 4: target_dimensions is numeric, not {} ────────────────────

    def test_target_dimensions_not_empty(self):
        """Keyword fallback returns {}; LLM path must return the model's scores."""
        data = self._design()
        dims = data.get("target_dimensions", {})
        self.assertNotEqual(
            dims, {}, "target_dimensions is {} — LLM path returned fallback empty dict"
        )

    def test_target_dimensions_all_six_keys(self):
        data = self._design()
        dims = data.get("target_dimensions", {})
        for key in ("warmth", "authority", "gravitas", "pace", "brightness", "age"):
            self.assertIn(key, dims, f"target_dimensions missing '{key}'")

    def test_target_dimensions_values_are_numeric(self):
        data = self._design()
        dims = data.get("target_dimensions", {})
        for key, val in dims.items():
            self.assertIsInstance(
                val, (int, float), f"target_dimensions['{key}']={val!r} is not numeric"
            )

    def test_target_dimensions_match_llm_values(self):
        """Exact LLM dimension values must survive the parse path unchanged."""
        data = self._design()
        dims = data.get("target_dimensions", {})
        for key, expected in _LLM_DIMS.items():
            self.assertEqual(
                dims.get(key),
                expected,
                f"target_dimensions['{key}'] expected {expected}, got {dims.get(key)!r}",
            )

    # ── Criterion 5: interpretation echoes the LLM field ─────────────────────

    def test_interpretation_echoes_llm_field(self):
        data = self._design()
        self.assertEqual(
            data.get("interpretation"),
            _LLM_INTERPRETATION,
            f"interpretation does not match LLM value.\n"
            f"  expected: {_LLM_INTERPRETATION!r}\n"
            f"  got:      {data.get('interpretation')!r}",
        )

    def test_interpretation_is_string(self):
        data = self._design()
        self.assertIsInstance(data.get("interpretation"), str)

    def test_interpretation_is_not_keyword_fallback_text(self):
        """Fallback sets interpretation to a 'keyword scoring' message; LLM path must not."""
        data = self._design()
        interp = data.get("interpretation", "").lower()
        self.assertNotIn(
            "keyword",
            interp,
            "interpretation contains 'keyword' — keyword fallback fired instead of LLM path",
        )
        self.assertNotIn(
            "unavailable",
            interp,
            "interpretation contains 'unavailable' — keyword fallback fired instead of LLM path",
        )

    # ── Other top-level contract fields ──────────────────────────────────────

    def test_description_is_echoed(self):
        desc = "deep gravitas historical ancient male"
        data = self._design(desc)
        self.assertEqual(
            data.get("description"), desc, "description field must echo the input unchanged"
        )

    def test_all_top_level_fields_present(self):
        data = self._design()
        for field in ("description", "interpretation", "matches", "target_dimensions"):
            self.assertIn(field, data, f"Top-level field '{field}' missing on LLM-success path")

    # ── Markdown code-fence stripping ────────────────────────────────────────

    def test_markdown_fenced_json_is_parsed_correctly(self):
        """LLMs sometimes wrap JSON in ```json … ``` fences — the endpoint must strip these."""
        data = self._design(llm_result=_llm_success_fenced(self._ids))
        # Fenced response must still take the LLM path, not fall through to keyword
        self.assertNotEqual(
            data.get("target_dimensions"),
            {},
            "Fenced-JSON response fell through to keyword fallback (target_dimensions={})",
        )
        self.assertGreater(
            data["matches"][0]["match_score"],
            75,
            "Fenced-JSON response gave fallback match_score — fence stripping may have failed",
        )
        self.assertEqual(
            data.get("interpretation"),
            _LLM_INTERPRETATION,
            "interpretation does not match after fence stripping",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

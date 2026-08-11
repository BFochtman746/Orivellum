"""LemonadeGateway (real gateway) contract tests.

The abstain-over-guess contract must hold even now that the gateway performs
real LLM calls: quoted epigraphs are refused without touching a model, and
model failure abstains instead of emitting placeholder text.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from orivellum.capabilities.finishing.gateway import (
    LemonadeGateway,
    MockGateway,
    get_gateway,
)


class _FakeLLMResult:
    def __init__(self, ok: bool, text: str = "", error: str = ""):
        self.ok = ok
        self.text = text
        self.error = error


class LemonadeGatewayTests(unittest.TestCase):
    def test_get_gateway_routing(self):
        self.assertIsInstance(get_gateway("mock"), MockGateway)
        self.assertIsInstance(get_gateway("lemonade"), LemonadeGateway)
        self.assertIsInstance(get_gateway("unknown"), MockGateway)

    def test_quote_request_abstains_without_calling_model(self):
        with patch("orivellum.capabilities.llm.llm_call") as mock_llm:
            res = LemonadeGateway().original_epigraph({"want_quote": True})
            mock_llm.assert_not_called()
        self.assertEqual(res.status, "ABSTAINED")
        self.assertEqual(res.text, "")
        self.assertEqual(res.attribution, "")

    def test_model_failure_abstains_instead_of_fabricating(self):
        with (
            patch(
                "orivellum.capabilities.llm.llm_call",
                return_value=_FakeLLMResult(ok=False, error="endpoint down"),
            ),
            patch("orivellum.api._deps.get_db", return_value=None),
            patch("orivellum.api._deps.get_config", return_value=None),
        ):
            res = LemonadeGateway().original_epigraph({"soul": "grief"})
        self.assertEqual(res.status, "ABSTAINED")
        self.assertEqual(res.text, "")
        self.assertIn("endpoint down", res.reason)

    def test_successful_epigraph_is_unverified_draft(self):
        with (
            patch(
                "orivellum.capabilities.llm.llm_call",
                return_value=_FakeLLMResult(
                    ok=True, text='{"text": "Ash remembers what fire forgets.", "attribution": ""}'
                ),
            ),
            patch("orivellum.api._deps.get_db", return_value=None),
            patch("orivellum.api._deps.get_config", return_value=None),
        ):
            res = LemonadeGateway().original_epigraph({"soul": "memory"})
        self.assertEqual(res.status, "UNVERIFIED_DRAFT")
        self.assertEqual(res.text, "Ash remembers what fire forgets.")
        # No in-world source given → attribution must stay empty (never invented)
        self.assertEqual(res.attribution, "")

    def test_cover_versions_abstain_when_image_backend_unavailable(self):
        with patch(
            "orivellum.capabilities.finishing.gateway._generate_cover_asset",
            side_effect=RuntimeError("no backend"),
        ):
            versions = LemonadeGateway().cover_versions({"title": "T"}, n=2)
        self.assertEqual(len(versions), 2)
        for v in versions:
            self.assertEqual(v.status, "ABSTAINED")
            self.assertEqual(v.asset_ref, "")
            self.assertIn("no backend", v.notes)

    def test_cover_versions_carry_asset_ref_on_success(self):
        with patch(
            "orivellum.capabilities.finishing.gateway._generate_cover_asset",
            return_value="image_abc123.png",
        ):
            versions = LemonadeGateway().cover_versions({"title": "T"}, n=1)
        self.assertEqual(versions[0].status, "DRAFT")
        self.assertEqual(versions[0].asset_ref, "image_abc123.png")


if __name__ == "__main__":
    unittest.main()

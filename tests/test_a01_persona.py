"""Tests for the A-01 copilot persona feature.

Verifies:
  1. The persona text lands in the system prompt for chat paths.
  2. Task personas (story partner, etc.) layer on top of A-01 rather than
     replacing it.
  3. Edits to chat.persona take effect on the next message without a restart.
  4. Reset via POST /system/persona/reset restores the seeded default.
  5. Non-chat prompts (harvest, workbench, mcos.judge) contain none of the
     persona text.
"""

import tempfile
import unittest
from datetime import UTC
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS as _AUTH_HEADERS


def _make_app(tmp):
    """Bootstrap a minimal Orivellum app in *tmp* and return (app, db, cfg)."""
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


class TestA01PersonaInSystemPrompt(unittest.TestCase):
    """Persona text lands in the system prompt for chat (both paths share _build_system_prompt)."""

    def test_persona_present_without_seeding(self):
        """Hardcoded fallback is injected when chat.persona slot is not seeded."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.api.routes.conversations import (
                _CHAT_PERSONA_PROMPT,
                _build_system_prompt,
            )
            sp = _build_system_prompt(db, {"work_id": None})
            self.assertIn("COPILOT IDENTITY", sp,
                          "A-01 persona header must appear in system prompt even without seeding")
            self.assertIn("Brian", sp,
                          "Persona must mention Brian by name")
            self.assertIn("EPISTEMIC HONESTY", sp,
                          "Epistemic-honesty section must be present")
            # Base capabilities must still be present
            self.assertIn("You are Orivellum", sp,
                          "chat.base capabilities block must still be in the prompt")

    def test_persona_from_active_slot(self):
        """Active chat.persona slot content overrides the hardcoded default."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            import uuid as _uuid
            from datetime import datetime
            from orivellum.api.routes.conversations import _build_system_prompt

            custom = "MY-CUSTOM-PERSONA-MARKER-XYZ"
            with db._lock:
                db._conn.execute(
                    "INSERT INTO prompts(id,slot,name,content,version,active,created_at)"
                    " VALUES(?,?,?,?,1,1,?)",
                    (str(_uuid.uuid4()), "chat.persona", "custom", custom,
                     datetime.now(UTC).isoformat()),
                )
                db._conn.commit()
            sp = _build_system_prompt(db, {"work_id": None})
            self.assertIn(custom, sp,
                          "Active chat.persona content must appear in system prompt")

    def test_base_capabilities_still_present_with_persona(self):
        """chat.base capabilities block must survive alongside the persona."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.capabilities.mcos import seed_default_prompts
            from orivellum.api.routes.conversations import _build_system_prompt

            seed_default_prompts(db)
            sp = _build_system_prompt(db, {"work_id": None})
            self.assertIn("You are Orivellum", sp,
                          "Base capabilities block must still be present when persona is injected")
            self.assertIn("COPILOT IDENTITY", sp,
                          "Persona must also be present")


class TestPersonaLayering(unittest.TestCase):
    """Task persona role layers sit on top of A-01, not replacing it."""

    def test_story_partner_layers_on_a01(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.api.routes.conversations import _build_system_prompt

            conv = {"work_id": None, "persona_id": "story_partner"}
            sp = _build_system_prompt(db, conv)
            self.assertIn("COPILOT IDENTITY", sp,
                          "A-01 persona must be present")
            self.assertIn("STORY PARTNER", sp,
                          "Role layer directive must be present")
            self.assertIn("ROLE LAYER", sp,
                          "Directive must explicitly say ROLE LAYER, not replace A-01")

    def test_devils_advocate_layers_on_a01(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.api.routes.conversations import _build_system_prompt

            conv = {"work_id": None, "persona_id": "devils_advocate"}
            sp = _build_system_prompt(db, conv)
            self.assertIn("COPILOT IDENTITY", sp)
            self.assertIn("DEVIL'S ADVOCATE", sp)
            self.assertIn("ROLE LAYER", sp)

    def test_technical_editor_layers_on_a01(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.api.routes.conversations import _build_system_prompt

            conv = {"work_id": None, "persona_id": "technical_editor"}
            sp = _build_system_prompt(db, conv)
            self.assertIn("COPILOT IDENTITY", sp)
            self.assertIn("TECHNICAL EDITOR", sp)
            self.assertIn("ROLE LAYER", sp)

    def test_research_assistant_layers_on_a01(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.api.routes.conversations import _build_system_prompt

            conv = {"work_id": None, "persona_id": "research_assistant"}
            sp = _build_system_prompt(db, conv)
            self.assertIn("COPILOT IDENTITY", sp)
            self.assertIn("RESEARCH ASSISTANT", sp)
            self.assertIn("ROLE LAYER", sp)

    def test_default_persona_contains_only_a01(self):
        """Default persona_id must not add any role-layer text on top of A-01."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.api.routes.conversations import _build_system_prompt

            sp = _build_system_prompt(db, {"work_id": None, "persona_id": "default"})
            self.assertNotIn("ROLE LAYER", sp,
                             "Default persona must not append any role layer")
            self.assertIn("COPILOT IDENTITY", sp)


class TestPersonaEditTakesEffect(unittest.TestCase):
    """Edits to chat.persona take effect on the next _build_system_prompt call."""

    def test_edit_active_slot_updates_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            import uuid as _uuid
            from datetime import datetime
            from orivellum.api.routes.conversations import _build_system_prompt
            from orivellum.capabilities.mcos import seed_default_prompts

            seed_default_prompts(db)
            # Verify default is present
            sp_before = _build_system_prompt(db, {"work_id": None})
            self.assertIn("COPILOT IDENTITY", sp_before)

            # Swap in a custom version
            new_id = str(_uuid.uuid4())
            with db._lock:
                db._conn.execute("UPDATE prompts SET active=0 WHERE slot='chat.persona'")
                db._conn.execute(
                    "INSERT INTO prompts(id,slot,name,content,version,active,created_at)"
                    " VALUES(?,?,?,?,2,1,?)",
                    (new_id, "chat.persona", "custom", "UPDATED-PERSONA-CONTENT-V2",
                     datetime.now(UTC).isoformat()),
                )
                db._conn.commit()

            # Next call should pick up the new version
            sp_after = _build_system_prompt(db, {"work_id": None})
            self.assertIn("UPDATED-PERSONA-CONTENT-V2", sp_after,
                          "Updated active persona must be reflected without restart")


class TestPersonaApiRoutes(unittest.TestCase):
    """GET / PATCH / POST /system/persona routes behave correctly."""

    def test_get_returns_default_when_unseeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _db, _cfg = _make_app(tmp)
            from starlette.testclient import TestClient
            from orivellum.api.routes.conversations import _CHAT_PERSONA_PROMPT

            client = TestClient(app, raise_server_exceptions=True, headers=_AUTH_HEADERS)
            r = client.get("/api/system/persona")
            self.assertEqual(r.status_code, 200)
            d = r.json()
            self.assertTrue(d["is_default"])
            self.assertIn("COPILOT IDENTITY", d["content"])
            self.assertEqual(d["content"].strip(), _CHAT_PERSONA_PROMPT.strip())

    def test_patch_saves_custom_and_takes_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            from starlette.testclient import TestClient
            from orivellum.api.routes.conversations import _build_system_prompt
            from orivellum.capabilities.mcos import seed_default_prompts

            seed_default_prompts(db)
            client = TestClient(app, raise_server_exceptions=True, headers=_AUTH_HEADERS)

            custom = "CUSTOM-PERSONA-VIA-PATCH-API"
            r = client.patch("/api/system/persona", json={"content": custom, "name": "Test"})
            self.assertEqual(r.status_code, 200)
            d = r.json()
            self.assertEqual(d["content"], custom)
            self.assertFalse(d["is_default"])

            # System prompt must reflect the edit immediately
            sp = _build_system_prompt(db, {"work_id": None})
            self.assertIn(custom, sp)

    def test_reset_restores_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            from starlette.testclient import TestClient
            from orivellum.api.routes.conversations import (
                _CHAT_PERSONA_PROMPT,
                _build_system_prompt,
            )
            from orivellum.capabilities.mcos import seed_default_prompts

            seed_default_prompts(db)
            client = TestClient(app, raise_server_exceptions=True, headers=_AUTH_HEADERS)

            # First save a custom version
            client.patch("/api/system/persona", json={"content": "MY-CUSTOM-OVERRIDE"})
            sp_mid = _build_system_prompt(db, {"work_id": None})
            self.assertIn("MY-CUSTOM-OVERRIDE", sp_mid)

            # Now reset
            r = client.post("/api/system/persona/reset")
            self.assertEqual(r.status_code, 200)
            d = r.json()
            self.assertTrue(d["is_default"])
            self.assertEqual(d["content"].strip(), _CHAT_PERSONA_PROMPT.strip())

            # System prompt must now contain the default again
            sp_after = _build_system_prompt(db, {"work_id": None})
            self.assertIn("COPILOT IDENTITY", sp_after)
            self.assertNotIn("MY-CUSTOM-OVERRIDE", sp_after)

    def test_patch_rejects_empty_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _db, _cfg = _make_app(tmp)
            from starlette.testclient import TestClient

            client = TestClient(app, raise_server_exceptions=True, headers=_AUTH_HEADERS)
            r = client.patch("/api/system/persona", json={"content": "   "})
            self.assertEqual(r.status_code, 400)


class TestNonChatPromptsPersonaFree(unittest.TestCase):
    """Harvest, workbench, and judge prompts must not contain the A-01 persona text."""

    def test_harvest_extract_prompt_free_of_persona(self):
        from orivellum.capabilities.knowledge_harvest import _EXTRACT_PROMPT
        self.assertNotIn("COPILOT IDENTITY", _EXTRACT_PROMPT)
        self.assertNotIn("Brian", _EXTRACT_PROMPT)
        self.assertNotIn("EPISTEMIC HONESTY", _EXTRACT_PROMPT)

    def test_judge_rubric_free_of_persona(self):
        from orivellum.capabilities.mcos import _JUDGE_RUBRIC
        self.assertNotIn("COPILOT IDENTITY", _JUDGE_RUBRIC)
        self.assertNotIn("A-01", _JUDGE_RUBRIC)

    def test_persona_slot_in_prompt_slots_registry(self):
        """chat.persona must be registered in PROMPT_SLOTS as benchmarkable."""
        from orivellum.capabilities.mcos import PROMPT_SLOTS
        self.assertIn("chat.persona", PROMPT_SLOTS,
                      "chat.persona must be a registered MCOS slot")
        self.assertTrue(PROMPT_SLOTS["chat.persona"]["benchmarkable"],
                        "chat.persona must be benchmarkable so nightly health checks cover it")

    def test_seed_creates_chat_persona_slot(self):
        """seed_default_prompts must create an active chat.persona row."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.capabilities.mcos import seed_default_prompts
            from orivellum.api.routes.conversations import _CHAT_PERSONA_PROMPT

            seed_default_prompts(db)
            # Idempotent — second call must not duplicate
            seed_default_prompts(db)

            with db._lock:
                rows = db._conn.execute(
                    "SELECT * FROM prompts WHERE slot='chat.persona' AND active=1"
                ).fetchall()
            self.assertEqual(len(rows), 1,
                             "Exactly one active chat.persona row must exist after seeding")
            self.assertEqual(rows[0]["content"].strip(), _CHAT_PERSONA_PROMPT.strip(),
                             "Seeded content must match the hardcoded default")

    def test_comm_style_is_supplementary_when_persona_active(self):
        """Communication style must appear as SUPPLEMENTARY STYLE HINT, not COMMUNICATION STYLE."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.api.routes.conversations import _build_system_prompt

            db.set_setting("communication_style", "direct", actor="user")
            sp = _build_system_prompt(db, {"work_id": None})
            self.assertIn("SUPPLEMENTARY STYLE HINT", sp,
                          "Comm style must be demoted to a supplementary hint when persona is active")
            self.assertNotIn("COMMUNICATION STYLE:", sp,
                             "Old COMMUNICATION STYLE: label must not appear alongside A-01 persona")


if __name__ == "__main__":
    unittest.main()

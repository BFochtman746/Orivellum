"""Reindex resilience tests (task: a bad document or slow server must never
silently stop the semantic re-index).

Covers _embed_batch_resilient:
- order + cardinality preserved through recursive splits
- a pathological first input can't masquerade as an endpoint outage
  (health canary is a fixed safe string, never batch content)
- endpoint-down terminates promptly with a bounded number of attempts
- singleton truncation retry for one bad text
- failure budget stops a flaky-endpoint loop

And run_full_reindex error persistence:
- reindex_error set when the endpoint dies mid-run (early stop)
- reindex_error set when the worker raises mid-run
- reindex_error cleared on success

The embeddings endpoint is always mocked — no network calls.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orivellum.capabilities import embeddings as emb


def _vec_for(text: str) -> list[float]:
    """Deterministic per-text vector so alignment is verifiable."""
    return [float(len(text)), 1.0, 0.0]


class EmbedBatchResilientTests(unittest.TestCase):
    def setUp(self):
        emb._reset_circuit_breaker()
        emb.invalidate_vector_cache()

    def tearDown(self):
        emb._reset_circuit_breaker()

    # ── happy path ────────────────────────────────────────────────────────

    def test_whole_batch_success_single_call(self):
        calls = []

        def fake(texts, timeout=None, bypass_cooldown=False):
            calls.append(list(texts))
            return [_vec_for(t) for t in texts]

        texts = [f"text-{i}" for i in range(8)]
        with patch.object(emb, "embed_texts", side_effect=fake):
            out = emb._embed_batch_resilient(texts)
        self.assertEqual(out, [_vec_for(t) for t in texts])
        self.assertEqual(len(calls), 1)

    def test_timeout_scales_with_batch_size(self):
        seen = {}

        def fake(texts, timeout=None, bypass_cooldown=False):
            seen["timeout"] = timeout
            return [_vec_for(t) for t in texts]

        with patch.object(emb, "embed_texts", side_effect=fake):
            emb._embed_batch_resilient([f"t{i}" for i in range(16)])
        self.assertEqual(seen["timeout"], emb._EMBED_TIMEOUT + 5 * 16)

    # ── recursive splitting ───────────────────────────────────────────────

    def test_split_preserves_order_and_cardinality(self):
        """Full batch too slow, halves succeed → output aligned with input."""

        def fake(texts, timeout=None, bypass_cooldown=False):
            if texts == [emb._CANARY_TEXT]:
                return [[0.0, 0.0, 1.0]]
            if len(texts) > 4:
                return None  # "too big/slow"
            return [_vec_for(t) for t in texts]

        texts = [f"unique-{i:02d}" for i in range(9)]  # odd length exercises uneven bisect
        with patch.object(emb, "embed_texts", side_effect=fake):
            out = emb._embed_batch_resilient(texts)
        self.assertIsNotNone(out)
        self.assertEqual(len(out), len(texts))
        self.assertEqual(out, [_vec_for(t) for t in texts])

    def test_pathological_first_input_does_not_look_like_outage(self):
        """A text that fails even short must not make the canary report the
        endpoint as down — canary is a fixed safe string, and the bad item is
        recovered via singleton truncation."""
        BAD = "BAD" + "x" * 3000

        def fake(texts, timeout=None, bypass_cooldown=False):
            if texts == [emb._CANARY_TEXT]:
                return [[0.0, 0.0, 1.0]]
            # Any request containing untruncated BAD content fails; the
            # 1500-char truncated retry succeeds.
            if any(t.startswith("BAD") and len(t) > 1500 for t in texts):
                return None
            return [_vec_for(t) for t in texts]

        texts = [BAD, "good-1", "good-2", "good-3"]
        with patch.object(emb, "embed_texts", side_effect=fake):
            out = emb._embed_batch_resilient(texts)
        self.assertIsNotNone(out)
        self.assertEqual(len(out), len(texts))
        # Good texts aligned in their positions
        self.assertEqual(out[1:], [_vec_for(t) for t in texts[1:]])
        # Bad text embedded via its truncated form
        self.assertEqual(out[0], _vec_for(BAD[:1500]))

    def test_canary_is_never_batch_content(self):
        """Every bypass_cooldown health check must use the fixed canary or a
        truncated singleton retry — never raw batch content."""
        canary_calls = []

        def fake(texts, timeout=None, bypass_cooldown=False):
            if bypass_cooldown:
                canary_calls.append(list(texts))
            if texts == [emb._CANARY_TEXT]:
                return [[0.0, 0.0, 1.0]]
            if len(texts) > 1:
                return None
            return [_vec_for(t) for t in texts]

        texts = [f"secret-{i}" for i in range(4)]
        with patch.object(emb, "embed_texts", side_effect=fake):
            out = emb._embed_batch_resilient(texts)
        self.assertIsNotNone(out)
        for call in canary_calls:
            self.assertEqual(call, [emb._CANARY_TEXT])

    # ── endpoint down / flaky ─────────────────────────────────────────────

    def test_endpoint_down_terminates_promptly(self):
        calls = {"n": 0}

        def fake(texts, timeout=None, bypass_cooldown=False):
            calls["n"] += 1
            return

        with patch.object(emb, "embed_texts", side_effect=fake):
            out = emb._embed_batch_resilient([f"t{i}" for i in range(16)])
        self.assertIsNone(out)
        # Batch attempt + one canary — no bisection storm on a dead endpoint
        self.assertEqual(calls["n"], 2)

    def test_failure_budget_stops_flaky_endpoint_loop(self):
        """Canary always succeeds but every content batch fails: the shared
        budget must stop the run instead of retrying indefinitely."""
        calls = {"n": 0}

        def fake(texts, timeout=None, bypass_cooldown=False):
            calls["n"] += 1
            if texts == [emb._CANARY_TEXT]:
                return [[0.0, 0.0, 1.0]]
            return None  # every real batch/singleton/truncation fails

        with patch.object(emb, "embed_texts", side_effect=fake):
            out = emb._embed_batch_resilient([f"t{i}" for i in range(16)])
        self.assertIsNone(out)
        # Bounded: content failures are capped by the budget; total calls
        # (content + canaries) stay in the same order of magnitude.
        self.assertLessEqual(calls["n"], 3 * emb._RESILIENT_FAIL_BUDGET + 2)

    def test_singleton_truncation_failure_gives_up_cleanly(self):
        def fake(texts, timeout=None, bypass_cooldown=False):
            if texts == [emb._CANARY_TEXT]:
                return [[0.0, 0.0, 1.0]]
            return None

        with patch.object(emb, "embed_texts", side_effect=fake):
            out = emb._embed_batch_resilient(["one bad text"])
        self.assertIsNone(out)


class ReindexErrorPersistenceTests(unittest.TestCase):
    def setUp(self):
        from orivellum.api import _deps
        from orivellum.configuration.config import OrivellumConfig
        from orivellum.database.db import OrivellumDB

        self._tmp = tempfile.TemporaryDirectory()
        cfg = OrivellumConfig(data_dir=self._tmp.name)
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        _deps.init(db=self.db, cfg=cfg)
        emb._reset_circuit_breaker()
        emb.invalidate_vector_cache()

        # Seed one document with an embeddable chunk (> 40 chars)
        doc = self.db.create_document(title="seed.txt", kind="text", work_id=None)
        text = "This chunk is comfortably longer than forty characters for embedding."
        self.db.add_chunk(doc_id=doc["id"], text=text, page=0)
        self.db.update_document_extracted(doc["id"], text, len(text.split()), readiness="ready")

    def tearDown(self):
        emb._reset_circuit_breaker()
        self._tmp.cleanup()

    def test_success_clears_reindex_error(self):
        self.db.set_setting("reindex_error", "stale error from a previous run")

        def fake(texts, timeout=None, bypass_cooldown=False):
            return [[1.0, 0.0, 0.0] for _ in texts]

        with patch.object(emb, "embed_texts", side_effect=fake):
            n = emb.run_full_reindex(self.db)
        self.assertGreater(n, 0)
        self.assertEqual(self.db.get_setting("reindex_error", ""), "")
        self.assertEqual(self.db.get_setting("reindex_running", ""), "false")

    def test_early_stop_persists_reindex_error(self):
        """Endpoint dead from the start: reindex must not claim success."""
        with patch.object(emb, "embed_texts", return_value=None):
            n = emb.run_full_reindex(self.db)
        self.assertEqual(n, 0)
        err = self.db.get_setting("reindex_error", "")
        self.assertIn("stopped early", err)
        self.assertIn("Re-index All", err)
        self.assertEqual(self.db.get_setting("reindex_running", ""), "false")

    def test_midrun_exception_persists_reindex_error(self):
        with patch.object(emb, "backfill_embeddings", side_effect=RuntimeError("disk exploded")):
            with self.assertRaises(RuntimeError):
                emb.run_full_reindex(self.db)
        err = self.db.get_setting("reindex_error", "")
        self.assertIn("failed mid-run", err)
        self.assertIn("disk exploded", err)
        self.assertEqual(self.db.get_setting("reindex_running", ""), "false")


if __name__ == "__main__":
    unittest.main()

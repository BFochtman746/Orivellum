"""Book pipeline stage-mapping and B0→B17 walk tests (audit D-01 / D-08).

Guards the invariant that the state machine's canonical stage labels and the
AI worker stage config can never silently drift apart again — the off-by-two
bug shipped a "Continuity Review" worker at B4 (Chapter Extraction).
"""

from __future__ import annotations

import unittest

from orivellum.capabilities.enums import BookState
from orivellum.capabilities.pipeline_workers import _STAGE_CFG, _assert_stage_alignment
from orivellum.capabilities.state_machine import BOOK_SM, BOOK_STAGE_LABELS


class StageAlignmentTests(unittest.TestCase):
    def test_every_worker_stage_is_a_declared_b_stage(self):
        for stage in _STAGE_CFG:
            self.assertIn(stage, BOOK_STAGE_LABELS)

    def test_worker_labels_do_not_claim_another_stages_canonical_label(self):
        """The exact D-01 failure: a worker whose label is the canonical label
        of a DIFFERENT stage (e.g. 'Continuity Review' registered at B4)."""
        canonical_by_label = {v: k for k, v in BOOK_STAGE_LABELS.items()}
        for stage, (_atype, _slot, label) in _STAGE_CFG.items():
            owner = canonical_by_label.get(label)
            if owner is not None:
                self.assertEqual(
                    owner,
                    stage,
                    f"Worker at {stage} is labelled {label!r}, which is the "
                    f"canonical label of {owner} — stage mapping drifted",
                )

    def test_continuity_and_fact_check_are_b6_b7(self):
        self.assertEqual(_STAGE_CFG["B6"][0], "continuity_report")
        self.assertEqual(_STAGE_CFG["B7"][0], "fact_check_report")
        self.assertNotIn("B4", _STAGE_CFG)  # Chapter Extraction — no LLM worker
        self.assertNotIn("B5", _STAGE_CFG)  # Chapter Drafting — no worker yet

    def test_import_time_assertion_rejects_drift(self):
        """Simulate the original bug and assert the guard raises."""
        drifted = dict(_STAGE_CFG)
        drifted["B4"] = ("continuity_report", "pipeline.b4.continuity", "Continuity Review")
        del drifted["B6"]
        import orivellum.capabilities.pipeline_workers as pw

        original = pw._STAGE_CFG
        try:
            pw._STAGE_CFG = drifted
            with self.assertRaises(RuntimeError):
                _assert_stage_alignment()
        finally:
            pw._STAGE_CFG = original
        # Sanity: the real config passes
        _assert_stage_alignment()


class PipelineWalkTests(unittest.TestCase):
    """Walk B0→B17 through the declared state machine."""

    ORDER = [s for s in BookState]

    def test_walk_b0_to_b17_single_step_forward(self):
        for i, state in enumerate(self.ORDER[:-1]):
            nxt = self.ORDER[i + 1]
            allowed = BOOK_SM.allowed_from(state)
            self.assertEqual(
                allowed,
                frozenset({nxt}),
                f"{state} must allow exactly one forward step to {nxt}",
            )
        # B17 is terminal
        self.assertEqual(BOOK_SM.allowed_from(self.ORDER[-1]), frozenset())

    def test_no_stage_skipping(self):
        for i, state in enumerate(self.ORDER):
            for j, other in enumerate(self.ORDER):
                if j != i + 1:
                    self.assertFalse(
                        BOOK_SM.can_transition(state, other),
                        f"{state} → {other} must not be a declared transition",
                    )

    def test_which_stages_have_workers(self):
        """The exact worker coverage map — this test is what would have
        caught D-01: it documents which stages have AI workers and fails
        the moment coverage changes without the test changing with it."""
        with_workers = sorted(_STAGE_CFG)
        self.assertEqual(with_workers, ["B0", "B1", "B2", "B3", "B6", "B7"])
        without = sorted(set(BOOK_STAGE_LABELS) - set(_STAGE_CFG))
        self.assertEqual(
            without,
            ["B10", "B11", "B12", "B13", "B14", "B15", "B16", "B17", "B4", "B5", "B8", "B9"],
        )

    def test_labels_cover_all_states(self):
        self.assertEqual(set(BOOK_STAGE_LABELS), {s.value for s in self.ORDER})


if __name__ == "__main__":
    unittest.main()

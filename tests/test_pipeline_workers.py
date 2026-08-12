"""Tests for the book-pipeline stage workers.

pipeline_workers gates pipeline advancement (its B6/B7 findings feed the
state-machine blocker check), so its dispatch and deterministic acceptance
checks get a test floor:

  * stage/label alignment guard (audit D-01) rejects drifted tables
  * JSON extraction from messy LLM output
  * architecture DAG validation (forward refs, cycles, duplicates, titles)
  * acceptance checks: B0 seal citations, B1 blueprint reconciliation, B3 DAG
  * run_stage_worker dispatch: unknown stage refused; success stores a done
    artifact; failure stores a failed artifact and re-raises
"""

from __future__ import annotations

import pytest

from orivellum.capabilities import pipeline_workers as pw

# ── Stage alignment guard ─────────────────────────────────────────────────────


def test_stage_alignment_accepts_current_table():
    pw._assert_stage_alignment()  # must not raise


def test_stage_alignment_rejects_unknown_stage(monkeypatch):
    monkeypatch.setitem(pw._STAGE_CFG, "B99", ("x", "slot", "Nonsense"))
    with pytest.raises(RuntimeError, match="unknown stage"):
        pw._assert_stage_alignment()


def test_stage_alignment_rejects_label_drift(monkeypatch):
    from orivellum.capabilities.state_machine import BOOK_STAGE_LABELS

    # Register a worker under B6 carrying ANOTHER stage's canonical label.
    other_label = BOOK_STAGE_LABELS["B7"]
    monkeypatch.setitem(pw._STAGE_CFG, "B6", ("continuity_report", "slot", other_label))
    with pytest.raises(RuntimeError, match="drifted"):
        pw._assert_stage_alignment()


# ── JSON extraction ───────────────────────────────────────────────────────────


def test_parse_json_variants():
    assert pw._parse_json('{"a": 1}') == {"a": 1}
    assert pw._parse_json('Sure! Here it is:\n{"a": 1}\nHope that helps.') == {"a": 1}
    assert pw._parse_json("[1, 2]") == [1, 2]
    assert pw._parse_json("no json here", fallback={"d": True}) == {"d": True}
    assert pw._parse_json(None) is None
    assert pw._parse_json("") is None


# ── Prompt template substitution ──────────────────────────────────────────────


def test_registered_prompt_leaves_literal_json_braces():
    template = 'Title: {work_title}\nOutput: {"key": "value", "n": {"x": 1}}'
    out = pw.render_registered_prompt(template, {"work_title": "T"})
    assert out.startswith("Title: T")
    assert '{"key": "value"' in out  # untouched


# ── Architecture DAG validation ───────────────────────────────────────────────


def _ch(seq, deps=None, title=None):
    return {"seq": seq, "title": title or f"Ch{seq}", "depends_on": deps or []}


def test_dag_valid_graph_passes():
    assert pw.check_architecture_dag([_ch(1), _ch(2, [1]), _ch(3, [1, 2])]) == []


def test_dag_forward_self_and_duplicate_refs_fail():
    assert any("forward" in p for p in pw.check_architecture_dag([_ch(1, [2]), _ch(2)]))
    assert any("self" in p for p in pw.check_architecture_dag([_ch(1, [1])]))
    assert any("Duplicate" in p for p in pw.check_architecture_dag([_ch(1), _ch(1)]))


def test_dag_unresolvable_and_title_refs():
    assert any("unresolvable" in p for p in pw.check_architecture_dag([_ch(1), _ch(2, ["Ghost"])]))
    # Title references resolve to their seq.
    assert pw.check_architecture_dag([_ch(1, title="Intro"), _ch(2, ["Intro"])]) == []


def test_dag_invalid_seq_values():
    assert any("invalid seq" in p for p in pw.check_architecture_dag([{"seq": "x"}]))
    assert any(">= 1" in p for p in pw.check_architecture_dag([_ch(0)]))


# ── Acceptance checks ─────────────────────────────────────────────────────────


def _ctx(sealed=False, artifacts=None, bp_count=0, contracts=None):
    return {
        "genesis": {
            "sealed": sealed,
            "artifacts": artifacts or {},
            "blueprint_chapter_count": bp_count,
        },
        "chapter_contracts": contracts or [],
    }


def test_b0_unsealed_skips_citation_check():
    pw._accept_b0({"title": "T"}, _ctx(sealed=False))  # must not raise


def test_b0_sealed_requires_real_citations():
    ctx = _ctx(sealed=True, artifacts={"G1": {}, "G2": {}})
    with pytest.raises(RuntimeError, match="cites no"):
        pw._accept_b0({"title": "T"}, ctx)
    with pytest.raises(RuntimeError, match="not in the sealed package"):
        pw._accept_b0({"source_citations": ["G9"]}, ctx)
    content = {"source_citations": ["g1"]}
    pw._accept_b0(content, ctx)
    assert content["source_citations"] == ["G1"]  # normalised


def test_b1_rejects_invented_chapter_count():
    ctx = _ctx(sealed=True, bp_count=5)
    with pytest.raises(RuntimeError, match="invented a chapter count"):
        pw._accept_b1({"total_chapters": 7, "chapters": [], "blueprint_deltas": []}, ctx)


def test_b1_fills_missing_chapters_from_blueprint():
    ctx = _ctx(sealed=True, bp_count=3, contracts=[{"seq": 2, "title": "Contracted"}])
    content = {
        "total_chapters": 3,
        "blueprint_deltas": [],
        "chapters": [{"seq": 1, "title": "One"}],
    }
    pw._accept_b1(content, ctx)
    assert [c["seq"] for c in content["chapters"]] == [1, 2, 3]
    assert content["chapters"][1]["title"] == "Contracted"
    assert content["chapters"][1]["from_blueprint"] is True


def test_b1_rejects_out_of_range_or_duplicate_seqs():
    ctx = _ctx(sealed=True, bp_count=2)
    base = {"total_chapters": 2, "blueprint_deltas": []}
    with pytest.raises(RuntimeError, match="outside blueprint range"):
        pw._accept_b1({**base, "chapters": [{"seq": 9, "title": "X"}]}, ctx)
    with pytest.raises(RuntimeError, match="duplicate"):
        pw._accept_b1(
            {**base, "chapters": [{"seq": 1, "title": "A"}, {"seq": 1, "title": "B"}]}, ctx
        )


def test_b3_rejects_bad_dag_and_blueprint_shortfall():
    with pytest.raises(RuntimeError, match="dependency graph invalid"):
        pw._accept_b3({"chapters": [_ch(1, [2]), _ch(2)]}, _ctx())
    with pytest.raises(RuntimeError, match="missing seqs"):
        pw._accept_b3({"chapters": [_ch(1)]}, _ctx(sealed=True, bp_count=3))


# ── run_stage_worker dispatch ─────────────────────────────────────────────────


class _StubDB:
    def __init__(self, prompt=None):
        self.artifacts: list[tuple] = []
        self.findings: list[dict] = []
        self._prompt = prompt

    def upsert_pipeline_artifact(self, pipeline_id, stage, artifact_type, content, **kw):
        self.artifacts.append((stage, artifact_type, content, kw))

    def get_active_prompt(self, slot):
        return self._prompt

    def create_finding(self, **kw):
        self.findings.append(kw)


_CTX_STUB = {
    "work_title": "W",
    "work_description": "",
    "documents": [],
    "blocks": {},
    "prior_artifacts": {},
    "genesis": {"sealed": False, "artifacts": {}, "blueprint_chapter_count": 0},
    "chapter_contracts": [],
}


def test_run_stage_worker_unknown_stage_refused():
    with pytest.raises(ValueError, match="No worker"):
        pw.run_stage_worker("p1", "B4", _StubDB(), cfg=None)


def test_run_stage_worker_success_stores_done_artifact(monkeypatch):
    db = _StubDB()
    monkeypatch.setattr(pw, "compile_stage_context", lambda *a, **k: dict(_CTX_STUB))
    monkeypatch.setattr(pw, "_call_llm", lambda *a, **k: {"title": "Brief"})
    out = pw.run_stage_worker("p1", "B0", db, cfg=None)
    assert out == {"title": "Brief"}
    assert db.artifacts[0][3]["status"] == "running"
    assert db.artifacts[-1] == ("B0", "project_brief", {"title": "Brief"}, {"status": "done"})


def test_run_stage_worker_failure_stores_failed_and_reraises(monkeypatch):
    db = _StubDB()
    monkeypatch.setattr(pw, "compile_stage_context", lambda *a, **k: dict(_CTX_STUB))

    def _boom(*a, **k):
        raise RuntimeError("LLM exploded")

    monkeypatch.setattr(pw, "_call_llm", _boom)
    with pytest.raises(RuntimeError, match="LLM exploded"):
        pw.run_stage_worker("p1", "B0", db, cfg=None)
    stage, artifact_type, content, kw = db.artifacts[-1]
    assert kw["status"] == "failed"
    assert "LLM exploded" in kw["error"]


def test_run_stage_worker_b6_issues_become_findings(monkeypatch):
    db = _StubDB()
    monkeypatch.setattr(pw, "compile_stage_context", lambda *a, **k: dict(_CTX_STUB))
    monkeypatch.setattr(
        pw,
        "_call_llm",
        lambda *a, **k: {
            "is_consistent": False,
            "issues": [
                {"chapter_a": "A", "chapter_b": "B", "description": "clash", "severity": "high"},
                {"chapter_a": "C", "chapter_b": "D", "description": "odd", "severity": "bogus"},
            ],
        },
    )
    pw.run_stage_worker("p1", "B6", db, cfg=None)
    assert len(db.findings) == 2
    assert db.findings[0]["severity"] == "high"
    assert db.findings[1]["severity"] == "medium"  # unknown severity clamped
    assert db.findings[0]["object_type"] == "book_pipeline"


def test_run_stage_worker_uses_registered_prompt(monkeypatch):
    db = _StubDB(prompt="Custom for {work_title}")
    seen = {}

    def _capture(user_prompt, *a, **k):
        seen["prompt"] = user_prompt
        return {"ok": True}

    monkeypatch.setattr(pw, "compile_stage_context", lambda *a, **k: dict(_CTX_STUB))
    monkeypatch.setattr(pw, "_call_llm", _capture)
    pw.run_stage_worker("p1", "B0", db, cfg=None)
    assert seen["prompt"] == "Custom for W"

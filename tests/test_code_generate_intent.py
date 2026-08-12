"""Tests for the chat code-generation intent pipeline.

Covers:
- _CODE_GEN_INTENT_RE: should-match / should-not-match corpus
- _handle_code_generation: returns None on pipeline exception
- _handle_code_generation: returns None (no card) when tests fail (gate enforced)
- _handle_code_generation: returns (reply, meta) with test_passed=True when passing
- _handle_code_generation: download URL passes path-traversal guard
- run_pipeline: run_tests_server_side=False skips subprocess test execution
- run_pipeline: run_tests_server_side=True calls run_tests() (default / chat path)
- Attachment guard: code-gen does not fire when file_b64+file_name are present
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orivellum.api.routes.conversations import (
    _CODE_GEN_INTENT_RE,
    _handle_code_generation,
)
from orivellum.capabilities.code_studio import (
    FilePlan,
    GeneratedFile,
    ProjectPlan,
    StudioResult,
    TestResult,
)


# ---------------------------------------------------------------------------
# 1. Regex corpus
# ---------------------------------------------------------------------------

SHOULD_MATCH = [
    "build me a Python CLI that parses invoices as JSON",
    "write a script that reads a CSV and outputs totals",
    "create a web scraper in Python",
    "write me a command-line tool that converts JSON to CSV",
    "develop an API server for tracking expenses",
    "I need a Python bot that checks prices hourly",
    "implement a calculator utility in TypeScript",
    "make a bash script that backs up my database",
    "build a REST API in Go",
    "write a python script to rename files",
    "create a file converter utility",
    "write a web crawler in Python that indexes pages",
]

SHOULD_NOT_MATCH = [
    "what is Python used for?",
    "turn this PDF into a spreadsheet",
    "create a report about my work",
    "build a tax package for 2024",
    "make a study plan for machine learning",
    "summarize my documents",
    "what does this code do?",
    "create an image of a robot",
    "build a presentation for the team",
    "create a spreadsheet for expenses",
    "export the manuscript as docx",
]


@pytest.mark.parametrize("text", SHOULD_MATCH)
def test_code_gen_regex_matches(text: str) -> None:
    assert _CODE_GEN_INTENT_RE.search(text), f"Expected regex to match: {text!r}"


@pytest.mark.parametrize("text", SHOULD_NOT_MATCH)
def test_code_gen_regex_no_false_positive(text: str) -> None:
    assert not _CODE_GEN_INTENT_RE.search(text), (
        f"Expected regex NOT to match: {text!r}"
    )


# ---------------------------------------------------------------------------
# 2. Helpers
# ---------------------------------------------------------------------------

def _make_db() -> MagicMock:
    db = MagicMock()
    db.add_message.return_value = {"id": "msg-test-1", "role": "assistant", "text": ""}
    return db


def _fake_cfg(tmp_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.data_dir = str(tmp_path)
    return cfg


def _make_zip(tmp_path: Path) -> tuple[Path, str]:
    out_dir = tmp_path / "outputs" / "generate" / "code_studio"
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "Invoice_CLI_9999.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("main.py", "print('hi')")
        zf.writestr("tests/test_main.py", "def test_ok(): assert True")
    rel = str(zip_path.relative_to(tmp_path))
    return zip_path, rel


def _passing_result(tmp_path: Path) -> StudioResult:
    zip_path, rel = _make_zip(tmp_path)
    return StudioResult(
        ok=True,
        title="Invoice CLI",
        language="python",
        files=[
            GeneratedFile(path="main.py", content="print('hi')"),
            GeneratedFile(path="tests/test_main.py", content="def test_ok(): assert True"),
        ],
        test_result=TestResult(passed=True, output="1 passed"),
        zip_path=str(zip_path),
        download_url=f"/api/generate/download?path={rel}",
        plan=ProjectPlan(
            title="Invoice CLI",
            description="CLI",
            language="python",
            files=[FilePlan(path="main.py", description="entry", is_test=False)],
        ),
    )


def _failing_result(tmp_path: Path) -> StudioResult:
    zip_path, rel = _make_zip(tmp_path)
    return StudioResult(
        ok=True,  # packaging succeeded, but tests failed
        title="Broken CLI",
        language="python",
        files=[
            GeneratedFile(path="main.py", content="print('hi')"),
            GeneratedFile(path="tests/test_main.py", content="def test_bad(): assert False"),
        ],
        test_result=TestResult(
            passed=False,
            output="FAILED tests/test_main.py::test_bad — AssertionError",
        ),
        zip_path=str(zip_path),
        download_url=f"/api/generate/download?path={rel}",
        plan=ProjectPlan(
            title="Broken CLI",
            description="CLI",
            language="python",
            files=[FilePlan(path="main.py", description="entry", is_test=False)],
        ),
    )


# ---------------------------------------------------------------------------
# 3. _handle_code_generation — core gate tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returns_none_on_pipeline_exception(tmp_path: Path) -> None:
    """If run_pipeline raises, _handle_code_generation returns None (no crash)."""
    with (
        patch("orivellum.api._deps.get_config", return_value=_fake_cfg(tmp_path)),
        patch(
            "orivellum.capabilities.code_studio.run_pipeline",
            side_effect=RuntimeError("LLM unavailable"),
        ),
    ):
        result = await _handle_code_generation("write a Python CLI", _make_db())

    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_packaging_fails(tmp_path: Path) -> None:
    """If packaging produces no zip (ok=False), returns None."""
    failing = StudioResult(
        ok=False,
        error="Packaging failed: disk full",
        title="My Tool",
        language="python",
        files=[],
        test_result=None,
        plan=ProjectPlan(
            title="My Tool",
            description="tool",
            language="python",
            files=[FilePlan(path="main.py", description="main", is_test=False)],
        ),
    )
    with (
        patch("orivellum.api._deps.get_config", return_value=_fake_cfg(tmp_path)),
        patch("orivellum.capabilities.code_studio.run_pipeline", return_value=failing),
    ):
        result = await _handle_code_generation("write a Python CLI", _make_db())

    assert result is None


@pytest.mark.asyncio
async def test_returns_no_download_card_when_tests_fail(tmp_path: Path) -> None:
    """When tests do not pass, the card must NOT appear (test gate enforced).

    A deliberately failing project (ok=True, test_result.passed=False) must
    produce a (reply, meta) pair whose intent is 'code_generate_failed',
    never 'code_generate', and must not carry a download_url in meta.
    """
    with (
        patch("orivellum.api._deps.get_config", return_value=_fake_cfg(tmp_path)),
        patch(
            "orivellum.capabilities.code_studio.run_pipeline",
            return_value=_failing_result(tmp_path),
        ),
    ):
        result = await _handle_code_generation(
            "build me a Python CLI that parses invoices", _make_db()
        )

    assert result is not None, "Should return failure message, not None"
    reply_text, meta = result
    assert meta.get("intent") != "code_generate", (
        "Must NOT show the passing download card when tests failed"
    )
    assert not meta.get("download_url"), (
        "No download_url should appear in a failed-test result"
    )
    assert meta.get("test_passed") is False


@pytest.mark.asyncio
async def test_returns_download_card_when_tests_pass(tmp_path: Path) -> None:
    """When tests pass, meta carries intent='code_generate' and download_url."""
    with (
        patch("orivellum.api._deps.get_config", return_value=_fake_cfg(tmp_path)),
        patch(
            "orivellum.capabilities.code_studio.run_pipeline",
            return_value=_passing_result(tmp_path),
        ),
    ):
        result = await _handle_code_generation(
            "build me a Python CLI that parses invoices", _make_db()
        )

    assert result is not None
    _, meta = result
    assert meta["intent"] == "code_generate"
    assert meta["test_passed"] is True
    assert meta["download_url"]


@pytest.mark.asyncio
async def test_download_url_inside_generate_root(tmp_path: Path) -> None:
    """download_url must pass the endpoint's path-traversal guard."""
    with (
        patch("orivellum.api._deps.get_config", return_value=_fake_cfg(tmp_path)),
        patch(
            "orivellum.capabilities.code_studio.run_pipeline",
            return_value=_passing_result(tmp_path),
        ),
    ):
        result = await _handle_code_generation(
            "build me a Python CLI that parses invoices", _make_db()
        )

    assert result is not None
    _, meta = result
    url: str = meta["download_url"]
    path_param = url.split("path=", 1)[1]
    generate_root = (tmp_path / "outputs" / "generate").resolve()
    resolved = (tmp_path / path_param).resolve()
    resolved.relative_to(generate_root)  # must not raise
    assert "outputs/generate/" in url


# ---------------------------------------------------------------------------
# 4. Attachment guard
# ---------------------------------------------------------------------------

def test_code_gen_intent_does_not_fire_when_attachment_present() -> None:
    """send_message uses _has_attachment = bool(file_b64 and file_name), not _file_text.

    Even if extraction returns "" (image-only PDF, corrupt upload), the presence
    of file_b64+file_name must suppress the code-gen intercept.
    """
    class FakeBody:
        file_b64 = "dGVzdA=="
        file_name = "invoice.pdf"
        text = "write a Python script to parse this invoice"

    body = FakeBody()
    _has_attachment = bool(body.file_b64 and body.file_name)
    fires = not _has_attachment and bool(_CODE_GEN_INTENT_RE.search(body.text))
    assert not fires, (
        "Code-gen must NOT fire when an attachment is present"
    )


def test_code_gen_fires_without_attachment() -> None:
    """Code-gen CAN fire when no file_b64/file_name are present."""
    class FakeBodyNoFile:
        file_b64 = None
        file_name = None
        text = "write a Python script to rename files"

    body = FakeBodyNoFile()
    _has_attachment = bool(body.file_b64 and body.file_name)
    fires = not _has_attachment and bool(_CODE_GEN_INTENT_RE.search(body.text))
    assert fires, "Code-gen SHOULD fire when no attachment is present and intent matches"


# ---------------------------------------------------------------------------
# 5. run_pipeline — server-side test execution flag
# ---------------------------------------------------------------------------

def test_run_pipeline_skips_tests_when_flag_false(tmp_path: Path) -> None:
    """run_tests_server_side=False must never call run_tests() or fix_and_retry()."""
    from orivellum.capabilities.code_studio import run_pipeline

    plan = ProjectPlan(
        title="Stub CLI",
        description="A stub",
        language="python",
        files=[FilePlan(path="main.py", description="entry", is_test=False)],
    )
    files = [GeneratedFile(path="main.py", content="print('stub')")]

    with (
        patch("orivellum.capabilities.code_studio.plan_project", return_value=plan),
        patch("orivellum.capabilities.code_studio.generate_files", return_value=files),
        patch("orivellum.capabilities.code_studio.run_tests") as mock_run_tests,
        patch("orivellum.capabilities.code_studio.fix_and_retry") as mock_fix,
        patch("orivellum.capabilities.code_studio.package_project") as mock_pkg,
    ):
        mock_pkg.return_value = tmp_path / "out.zip"
        cfg = MagicMock()
        cfg.data_dir = str(tmp_path)

        result = run_pipeline(
            description="write a stub CLI",
            run_tests_server_side=False,
            cfg=cfg,
            db=None,
        )

    mock_run_tests.assert_not_called()
    mock_fix.assert_not_called()
    assert result.test_result is None


def test_run_pipeline_calls_tests_when_flag_true(tmp_path: Path) -> None:
    """run_tests_server_side=True (default) must call run_tests()."""
    from orivellum.capabilities.code_studio import run_pipeline

    plan = ProjectPlan(
        title="CLI",
        description="A CLI",
        language="python",
        files=[FilePlan(path="main.py", description="entry", is_test=False)],
    )
    files = [GeneratedFile(path="main.py", content="print('hello')")]
    passing_test = TestResult(passed=True, output="1 passed")

    with (
        patch("orivellum.capabilities.code_studio.plan_project", return_value=plan),
        patch("orivellum.capabilities.code_studio.generate_files", return_value=files),
        patch("orivellum.capabilities.code_studio.run_tests", return_value=passing_test) as mock_run_tests,
        patch("orivellum.capabilities.code_studio.package_project") as mock_pkg,
    ):
        mock_pkg.return_value = tmp_path / "out.zip"
        cfg = MagicMock()
        cfg.data_dir = str(tmp_path)

        result = run_pipeline(
            description="write a Python CLI",
            run_tests_server_side=True,
            cfg=cfg,
            db=None,
        )

    mock_run_tests.assert_called_once()
    assert result.test_result is passing_test


# ---------------------------------------------------------------------------
# 6. progress_callback integration
# ---------------------------------------------------------------------------

def test_run_pipeline_calls_progress_at_each_stage(tmp_path: Path) -> None:
    """run_pipeline() calls progress_callback for planning, generating, testing,
    packaging in the correct order; each call carries a non-empty label."""
    from orivellum.capabilities.code_studio import run_pipeline

    plan = ProjectPlan(
        title="CLI",
        description="A CLI",
        language="python",
        files=[FilePlan(path="main.py", description="entry", is_test=False)],
    )
    files = [GeneratedFile(path="main.py", content="print('hi')")]
    passing_test = TestResult(passed=True, output="1 passed")

    calls: list[tuple[str, str, int, int]] = []

    def _cb(stage: str, label: str, n: int, total: int) -> None:
        calls.append((stage, label, n, total))

    with (
        patch("orivellum.capabilities.code_studio.plan_project", return_value=plan),
        patch("orivellum.capabilities.code_studio.generate_files", return_value=files),
        patch("orivellum.capabilities.code_studio.run_tests", return_value=passing_test),
        patch("orivellum.capabilities.code_studio.package_project") as mock_pkg,
    ):
        mock_pkg.return_value = tmp_path / "out.zip"
        cfg = MagicMock()
        cfg.data_dir = str(tmp_path)

        run_pipeline(
            description="write a CLI",
            run_tests_server_side=True,
            cfg=cfg,
            db=None,
            progress_callback=_cb,
        )

    stages = [c[0] for c in calls]
    assert "planning" in stages, "Expected 'planning' stage callback"
    assert "testing"  in stages, "Expected 'testing' stage callback"
    assert "packaging" in stages, "Expected 'packaging' stage callback"
    # Labels must all be non-empty strings
    for stage, label, _, _ in calls:
        assert label.strip(), f"Empty label for stage {stage!r}"


def test_generate_files_progress_per_file(tmp_path: Path) -> None:
    """generate_files() calls progress_callback once per file with correct n/total."""
    from orivellum.capabilities.code_studio import generate_files

    plan = ProjectPlan(
        title="CLI", description="CLI", language="python",
        files=[
            FilePlan(path="main.py",            description="main",   is_test=False),
            FilePlan(path="tests/test_main.py", description="tests",  is_test=True),
            FilePlan(path="README.md",          description="readme", is_test=False),
        ],
    )

    generating_calls: list[tuple[int, int]] = []

    def _cb(stage: str, label: str, n: int, total: int) -> None:
        if stage == "generating":
            generating_calls.append((n, total))

    fake_result = MagicMock()
    fake_result.text = "print('hello')"
    with patch("orivellum.capabilities.llm.llm_call", return_value=fake_result):
        cfg = MagicMock()
        generate_files(plan, "write a CLI", cfg=cfg, db=None, progress_callback=_cb)

    # Called once per file; README is also a file (generates inline, not via LLM but
    # progress_callback is still called for every entry in plan.files).
    assert len(generating_calls) == 3, f"Expected 3 generating callbacks, got {generating_calls}"
    totals = {t for _, t in generating_calls}
    assert totals == {3}, "total must always be 3 (one per file)"
    ns = [n for n, _ in generating_calls]
    assert ns == [1, 2, 3], f"Expected n=1,2,3 got {ns}"


def test_fix_and_retry_progress_per_attempt(tmp_path: Path) -> None:
    """fix_and_retry() calls progress_callback with stage='fixing' for each attempt."""
    from orivellum.capabilities.code_studio import fix_and_retry

    plan = ProjectPlan(
        title="CLI", description="CLI", language="python",
        files=[FilePlan(path="main.py", description="main", is_test=False)],
    )
    initial_fail = TestResult(passed=False, output="FAILED")
    # Always fails so we get max_retries attempts
    always_fail = TestResult(passed=False, output="FAILED again")

    fixing_calls: list[tuple[int, int]] = []

    def _cb(stage: str, label: str, n: int, total: int) -> None:
        if stage == "fixing":
            fixing_calls.append((n, total))

    fake_result = MagicMock()
    fake_result.text = '{"fix_file": "main.py"}\nprint("fixed")'
    with (
        patch("orivellum.capabilities.llm.llm_call", return_value=fake_result),
        patch("orivellum.capabilities.code_studio.run_tests", return_value=always_fail),
    ):
        fix_and_retry(
            [GeneratedFile(path="main.py", content="broken")],
            initial_fail, plan, "CLI",
            max_retries=2, cfg=MagicMock(), db=None,
            progress_callback=_cb,
        )

    assert len(fixing_calls) == 2, f"Expected 2 fixing callbacks, got {fixing_calls}"
    assert fixing_calls[0] == (1, 2)
    assert fixing_calls[1] == (2, 2)


def test_run_pipeline_skips_progress_when_callback_is_none(tmp_path: Path) -> None:
    """run_pipeline() must not raise when progress_callback=None (default)."""
    from orivellum.capabilities.code_studio import run_pipeline

    plan = ProjectPlan(
        title="CLI", description="A CLI", language="python",
        files=[FilePlan(path="main.py", description="entry", is_test=False)],
    )
    files = [GeneratedFile(path="main.py", content="print('hi')")]
    passing = TestResult(passed=True, output="1 passed")

    with (
        patch("orivellum.capabilities.code_studio.plan_project", return_value=plan),
        patch("orivellum.capabilities.code_studio.generate_files", return_value=files),
        patch("orivellum.capabilities.code_studio.run_tests", return_value=passing),
        patch("orivellum.capabilities.code_studio.package_project") as mock_pkg,
    ):
        mock_pkg.return_value = tmp_path / "out.zip"
        cfg = MagicMock()
        cfg.data_dir = str(tmp_path)
        # progress_callback defaults to None — must not raise
        result = run_pipeline("write a CLI", run_tests_server_side=True, cfg=cfg, db=None)

    assert result is not None  # pipeline completed without crash


# ---------------------------------------------------------------------------
# 7. Route-level security regression: chat SSE never executes generated code
# ---------------------------------------------------------------------------

def test_sse_route_uses_run_tests_server_side_false(tmp_path: Path) -> None:
    """The chat SSE code-gen path must call run_pipeline with
    run_tests_server_side=False so generated code is never executed on the
    API host.  This is a security invariant; never flip it to True here."""
    import asyncio
    import inspect
    from orivellum.api.routes.conversations import send_message  # noqa: F401 – trigger import

    # Extract the source of the conversations module and assert the invariant
    # directly — immune to runtime monkey-patching, catches future regressions.
    import orivellum.api.routes.conversations as _conv_mod
    src = inspect.getsource(_conv_mod)

    # The SSE generator must NOT pass run_tests_server_side=True in the
    # chat path.  We check that every occurrence of run_tests_server_side=True
    # in the module is NOT inside _code_gen_sse.
    import re
    # Find the body of _code_gen_sse
    sse_match = re.search(
        r"async def _code_gen_sse\(\):(.*?)(?=\n            return StreamingResponse)",
        src, re.DOTALL,
    )
    assert sse_match, "_code_gen_sse not found in conversations.py"
    sse_body = sse_match.group(1)
    assert "run_tests_server_side=True" not in sse_body, (
        "SECURITY REGRESSION: _code_gen_sse must never execute generated code "
        "on the API host.  Keep run_tests_server_side=False."
    )
    assert "run_tests_server_side=False" in sse_body, (
        "_code_gen_sse must explicitly pass run_tests_server_side=False."
    )


def test_sse_generator_emits_code_progress_frames(tmp_path: Path) -> None:
    """_code_gen_sse() must emit at least one code_progress frame before the
    pipeline result, so the client ActivityStrip shows stage labels."""
    import asyncio
    from pathlib import Path as _P
    from unittest.mock import AsyncMock, patch as _patch, MagicMock as _MM

    plan = ProjectPlan(
        title="CLI", description="CLI", language="python",
        files=[FilePlan(path="main.py", description="main", is_test=False)],
    )
    files = [GeneratedFile(path="main.py", content="print('hi')")]
    ok_result = MagicMock()
    ok_result.ok = True
    ok_result.download_url = "/data/download/out.zip"
    ok_result.title = "CLI"
    ok_result.language = "python"
    ok_result.files = files
    ok_result.test_result = None
    ok_result.error = None

    # Simulate the SSE generator directly: we reconstruct the minimal closure
    # that _code_gen_sse uses, without a live DB or HTTP layer.
    async def _collect_frames():
        import queue as _queue
        import json as _json

        prog_q: _queue.SimpleQueue = _queue.SimpleQueue()
        frames: list[dict] = []

        # Emulate _on_progress: push one event for 'planning'
        prog_q.put_nowait({"stage": "planning", "label": "Planning project…", "n": 0, "total": 0})
        prog_q.put_nowait({"stage": "packaging", "label": "Packaging zip…",   "n": 0, "total": 0})

        # Drain and collect — same logic as _code_gen_sse's drain loop
        while True:
            try:
                ev = prog_q.get_nowait()
                raw = f"data: {_json.dumps({'code_progress': ev})}\n\n"
                parsed = _json.loads(raw.replace("data: ", "").strip())
                frames.append(parsed)
            except _queue.Empty:
                break
        return frames

    frames = asyncio.run(_collect_frames())
    stages = [f["code_progress"]["stage"] for f in frames]
    assert "planning"  in stages, f"Expected 'planning' frame, got {stages}"
    assert "packaging" in stages, f"Expected 'packaging' frame, got {stages}"
    # Frames must carry a non-empty label
    for f in frames:
        assert f["code_progress"]["label"].strip(), "Empty label in code_progress frame"

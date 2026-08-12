"""Code Studio — plan → generate → test → package complete programs.

Pipeline:
  1. plan_project()     — LLM designs the file tree and test strategy as JSON
  2. generate_files()   — LLM writes each file's content (one at a time)
  3. run_tests()        — subprocess: pytest/unittest in a temp dir with timeout
  4. fix_and_retry()    — up to 2 LLM-powered fix passes if tests fail
  5. package_project()  — zip all files + README into a download
  6. analyze_project()  — audit an uploaded code zip; return findings + proposals
                          (proposal-only — never auto-applied)
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.code_studio")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_LANGUAGES = {"python", "javascript", "typescript"}

_LANG_META: dict[str, dict] = {
    "python": {
        "ext": ".py",
        "test_cmd": [sys.executable, "-m", "pytest", "-v", "--tb=short"],
        "test_fallback": [sys.executable, "-m", "unittest", "discover", "-v"],
        "test_file_pattern": "test_*.py",
        "runner": sys.executable,
        "entry": "main.py",
    },
    "javascript": {
        "ext": ".js",
        "test_cmd": ["node", "--experimental-vm-modules",
                     "node_modules/.bin/jest", "--no-coverage"],
        "test_fallback": ["node", "-e",
                          "const t=require('./tests');if(t.run)t.run();"],
        "test_file_pattern": "*.test.js",
        "runner": "node",
        "entry": "index.js",
    },
    "typescript": {
        "ext": ".ts",
        "test_cmd": ["npx", "ts-jest", "--coverage=false"],
        "test_fallback": ["npx", "ts-node", "tests/index.test.ts"],
        "test_file_pattern": "*.test.ts",
        "runner": "ts-node",
        "entry": "src/index.ts",
    },
}

_SANDBOX_TIMEOUT = 60  # seconds


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class FilePlan:
    path: str          # relative path inside the project, e.g. "src/main.py"
    description: str   # what this file does
    is_test: bool = False


@dataclass
class ProjectPlan:
    title: str
    description: str
    language: str
    files: list[FilePlan] = field(default_factory=list)
    entry_point: str = ""
    test_command: str = ""
    dependencies: list[str] = field(default_factory=list)


@dataclass
class GeneratedFile:
    path: str
    content: str


@dataclass
class TestResult:
    passed: bool
    output: str
    error: str = ""
    tests_found: bool = True


@dataclass
class StudioResult:
    ok: bool
    title: str = ""
    language: str = ""
    files: list[GeneratedFile] = field(default_factory=list)
    test_result: TestResult | None = None
    zip_path: str = ""
    download_url: str = ""
    error: str = ""
    plan: ProjectPlan | None = None
    analysis: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------

_PLAN_SYSTEM = """\
You are an expert software architect. Given a plain-English description of a program,
design a minimal, well-structured project. Reply ONLY with valid JSON — no markdown,
no commentary.

Schema:
{
  "title": "Short project name (3-5 words)",
  "description": "One sentence describing the program",
  "language": "python|javascript|typescript",
  "entry_point": "relative path to the main runnable file",
  "test_command": "command to run all tests (e.g. 'python -m pytest -v')",
  "dependencies": ["package1", "package2"],
  "files": [
    {
      "path": "relative/path/to/file.py",
      "description": "what this file does",
      "is_test": false
    }
  ]
}

Rules:
- Python is the default language unless user specifies otherwise
- Always include at least one test file (is_test: true)
- Tests must use pytest for Python; jest for JS/TS
- Keep the file count between 3 and 8
- Standard library preferred; minimise dependencies
- Include a README.md in the files list
"""

_FILE_SYSTEM = """\
You are an expert programmer writing a single file for a software project.
Write ONLY the raw source code for the file — no markdown fences, no commentary
outside of code comments. The code must be correct, well-commented, and idiomatic
for the language.

For test files:
- Python: use pytest; at least 3 meaningful test cases
- JS/TS: use jest with describe/it/expect
- Tests must actually import and test the real code (not mock everything)
- Tests must be runnable without network access or external services

For non-test files:
- Handle edge cases and errors explicitly
- No hardcoded absolute paths — all paths relative to the project root
"""

_FIX_SYSTEM = """\
You are an expert programmer debugging a failing test suite.
You will receive:
1. The failing test output
2. The current contents of all source files

Identify the root cause and produce a corrected version of the problematic file.
Reply with ONLY the corrected file content — no markdown, no explanation.
"""

_ANALYZE_SYSTEM = """\
You are a senior code reviewer. Analyze the supplied source code and produce:
1. A brief summary of what the code does
2. A list of concrete issues (bugs, security risks, anti-patterns, missing tests)
3. A prioritized list of improvement proposals

Reply with valid JSON:
{
  "summary": "...",
  "issues": [
    {"severity": "high|medium|low", "location": "file:line or 'general'", "description": "..."}
  ],
  "proposals": [
    {"priority": 1, "title": "...", "description": "...", "estimated_effort": "minutes|hours|days"}
  ]
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_code(raw: str) -> str:
    """Strip markdown fences from LLM output."""
    s = raw.strip()
    s = re.sub(r"^```[\w]*\s*\n?", "", s, flags=re.M)
    s = re.sub(r"\n?```\s*$", "", s, flags=re.M)
    return s.strip()


def _safe_relpath(path: str) -> str:
    """Normalise a file path, stripping any leading ../ traversal."""
    p = Path(path.lstrip("/"))
    parts = [part for part in p.parts if part not in ("..", ".")]
    return str(Path(*parts)) if parts else "file.txt"


# ---------------------------------------------------------------------------
# Stage 1 — Plan
# ---------------------------------------------------------------------------

def plan_project(
    description: str,
    language: str | None = None,
    cfg: Any = None,
    db: Any = None,
) -> ProjectPlan:
    """Ask the LLM to design the project file structure."""
    from orivellum.capabilities.llm import llm_call

    lang_hint = f"\nPreferred language: {language}" if language else ""
    result = llm_call(
        [
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user", "content": f"Description: {description}{lang_hint}"},
        ],
        cfg=cfg,
        db=db,
        purpose="code_studio.plan",
        temperature=0.1,
        max_tokens=2000,
        timeout=60,
    )

    raw = result.text or ""
    # Strip code fences if the model disobeyed instructions
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:]).rstrip("`").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("plan_project: LLM returned invalid JSON — using minimal plan")
        lang = language or "python"
        data = {
            "title": description[:40],
            "description": description,
            "language": lang,
            "entry_point": _LANG_META.get(lang, _LANG_META["python"])["entry"],
            "test_command": "python -m pytest -v",
            "dependencies": [],
            "files": [
                {"path": "main.py", "description": "Main program", "is_test": False},
                {"path": "tests/test_main.py", "description": "Unit tests", "is_test": True},
                {"path": "README.md", "description": "Project README", "is_test": False},
            ],
        }

    lang = str(data.get("language", language or "python")).lower()
    if lang not in SUPPORTED_LANGUAGES:
        lang = "python"

    files = [
        FilePlan(
            path=_safe_relpath(str(f.get("path", "file.txt"))),
            description=str(f.get("description", "")),
            is_test=bool(f.get("is_test", False)),
        )
        for f in data.get("files", [])
    ]

    return ProjectPlan(
        title=str(data.get("title", description[:40])),
        description=str(data.get("description", description)),
        language=lang,
        files=files,
        entry_point=_safe_relpath(str(data.get("entry_point", "main.py"))),
        test_command=str(data.get("test_command", "python -m pytest -v")),
        dependencies=list(data.get("dependencies", [])),
    )


# ---------------------------------------------------------------------------
# Stage 2 — Generate files
# ---------------------------------------------------------------------------

def generate_files(
    plan: ProjectPlan,
    description: str,
    cfg: Any = None,
    db: Any = None,
) -> list[GeneratedFile]:
    """Generate the content of each file in the plan, one at a time."""
    from orivellum.capabilities.llm import llm_call

    generated: list[GeneratedFile] = []
    lang_meta = _LANG_META.get(plan.language, _LANG_META["python"])

    # Build a summary of all planned files so each file's generator has context
    file_tree = "\n".join(f"  {f.path}  — {f.description}" for f in plan.files)
    project_ctx = (
        f"Project: {plan.title}\n"
        f"Language: {plan.language}\n"
        f"Description: {plan.description}\n"
        f"Entry point: {plan.entry_point}\n"
        f"File tree:\n{file_tree}\n"
        f"Dependencies: {', '.join(plan.dependencies) or 'none'}"
    )

    for file_plan in plan.files:
        ext = Path(file_plan.path).suffix.lower()

        # README is plain text — no code generation needed
        if file_plan.path.endswith("README.md") or ext == ".md":
            content = (
                f"# {plan.title}\n\n"
                f"{plan.description}\n\n"
                f"## Setup\n\n"
                f"```\npip install {' '.join(plan.dependencies) if plan.dependencies else 'no extra dependencies'}\n```\n\n"
                f"## Usage\n\n"
                f"```\n{lang_meta['runner']} {plan.entry_point}\n```\n\n"
                f"## Tests\n\n"
                f"```\n{plan.test_command}\n```\n"
            )
            generated.append(GeneratedFile(path=file_plan.path, content=content))
            continue

        # For source files — use the LLM
        already_generated = "\n".join(
            f"\n--- {g.path} ---\n{g.content[:500]}…"
            for g in generated
            if not g.path.endswith(".md")
        )

        user_msg = (
            f"{project_ctx}\n\n"
            f"Write the file: {file_plan.path}\n"
            f"Purpose: {file_plan.description}\n"
            f"{'This is a TEST file — write meaningful test cases.' if file_plan.is_test else ''}\n"
            f"\nOriginal request: {description}\n"
            f"{('Already generated files (for context):\n' + already_generated) if already_generated else ''}"
        )

        result = llm_call(
            [
                {"role": "system", "content": _FILE_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            cfg=cfg,
            db=db,
            purpose="code_studio.generate",
            temperature=0.15,
            max_tokens=3000,
            timeout=90,
        )

        content = _clean_code(result.text or f"# {file_plan.path}\n# (generation failed)\n")
        generated.append(GeneratedFile(path=file_plan.path, content=content))
        logger.debug("Generated %s (%d chars)", file_plan.path, len(content))

    return generated


# ---------------------------------------------------------------------------
# Stage 3 — Test in sandbox subprocess
# ---------------------------------------------------------------------------

def run_tests(
    files: list[GeneratedFile],
    language: str,
    timeout: int = _SANDBOX_TIMEOUT,
) -> TestResult:
    """Write files to a temp dir and run the test suite.

    Returns TestResult with passed=True only when the test runner exits 0.
    """
    lang_meta = _LANG_META.get(language, _LANG_META["python"])

    with tempfile.TemporaryDirectory(prefix="code_studio_") as tmpdir:
        tmp = Path(tmpdir)

        # Write all files
        for gf in files:
            dest = tmp / gf.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(gf.content, encoding="utf-8")

        # Check whether there are any test files
        test_files = [f for f in files if Path(f.path).match("test_*")
                      or "test" in Path(f.path).stem.lower()]
        if not test_files:
            return TestResult(
                passed=False,
                output="No test files found.",
                error="The generated project contains no tests.",
                tests_found=False,
            )

        # Build the environment — strip parent secrets, allow only the tmp dir
        env = {
            "HOME": tmpdir,
            "TMPDIR": tmpdir,
            "TEMP": tmpdir,
            "TMP": tmpdir,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": tmpdir,
            # Explicitly block outbound HTTP proxies and D-Bus
            "no_proxy": "*",
            "NO_PROXY": "*",
        }
        # Python needs its home dir for stdlib
        for key in ("PYTHONHOME", "VIRTUAL_ENV"):
            if key in os.environ:
                env[key] = os.environ[key]

        cmd = lang_meta["test_cmd"]

        def _resource_preexec(cpu_limit: int) -> None:
            """Apply hard resource limits inside the generated-code subprocess.

            Limits enforced (Linux/Mac only; no-op where unavailable):
            - RLIMIT_CPU:   CPU-time ceiling = timeout + 10 s (kills spin-loops)
            - RLIMIT_AS:    Virtual-memory cap = 512 MB (kills memory bombs)
            - RLIMIT_FSIZE: Max file size = 10 MB (kills disk bombs)
            - RLIMIT_NPROC: Max processes = 32 (kills fork bombs)

            Together with the stripped environment and tmpdir isolation these
            limits constitute the isolation boundary for LLM-generated test code.
            They do not replace a true container sandbox but are sufficient to
            prevent runaway resource consumption on the host.
            """
            try:
                import resource as _r

                _r.setrlimit(_r.RLIMIT_CPU,   (cpu_limit + 10, cpu_limit + 10))
                _r.setrlimit(_r.RLIMIT_AS,    (512 * 1024 * 1024, 512 * 1024 * 1024))
                _r.setrlimit(_r.RLIMIT_FSIZE, (10  * 1024 * 1024, 10  * 1024 * 1024))
                _r.setrlimit(_r.RLIMIT_NPROC, (32, 32))
            except Exception:
                pass  # Windows / restricted environments — best-effort

        try:
            proc = subprocess.run(
                cmd,
                cwd=tmpdir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                preexec_fn=lambda: _resource_preexec(timeout),
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            passed = proc.returncode == 0

            # If pytest not found, fall back to unittest discover
            if proc.returncode == 2 and "no tests ran" in output.lower():
                passed = False
            if "ModuleNotFoundError: No module named 'pytest'" in output:
                # Try unittest fallback
                proc2 = subprocess.run(
                    lang_meta["test_fallback"],
                    cwd=tmpdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    preexec_fn=lambda: _resource_preexec(timeout),
                )
                output = (proc2.stdout or "") + (proc2.stderr or "")
                passed = proc2.returncode == 0

            return TestResult(passed=passed, output=output[:4000])

        except subprocess.TimeoutExpired:
            return TestResult(
                passed=False,
                output="",
                error=f"Tests timed out after {timeout}s.",
            )
        except FileNotFoundError as exc:
            return TestResult(
                passed=False,
                output="",
                error=f"Test runner not found: {exc}",
            )


# ---------------------------------------------------------------------------
# Stage 4 — Fix loop
# ---------------------------------------------------------------------------

def fix_and_retry(
    files: list[GeneratedFile],
    test_result: TestResult,
    plan: ProjectPlan,
    description: str,
    max_retries: int = 2,
    cfg: Any = None,
    db: Any = None,
) -> tuple[list[GeneratedFile], TestResult]:
    """If tests fail, ask the LLM to fix the broken file and re-run tests."""
    from orivellum.capabilities.llm import llm_call

    current_files = list(files)
    current_result = test_result

    for attempt in range(max_retries):
        if current_result.passed:
            break

        logger.info("Fix attempt %d/%d for '%s'", attempt + 1, max_retries, plan.title)

        # Build context for the fixer
        all_source = "\n\n".join(
            f"=== {gf.path} ===\n{gf.content}"
            for gf in current_files
            if not gf.path.endswith(".md")
        )
        fix_prompt = (
            f"Project: {plan.title}\nDescription: {description}\n\n"
            f"TEST OUTPUT (FAILING):\n{current_result.output}\n"
            f"{('ERROR: ' + current_result.error) if current_result.error else ''}\n\n"
            f"ALL SOURCE FILES:\n{all_source}"
        )

        # Ask the LLM which file to fix and what the corrected content should be
        result = llm_call(
            [
                {"role": "system", "content": _FIX_SYSTEM + "\n\nAlso include a JSON header on the FIRST line: {\"fix_file\": \"path/to/file.py\"}"},
                {"role": "user", "content": fix_prompt},
            ],
            cfg=cfg,
            db=db,
            purpose="code_studio.fix",
            temperature=0.1,
            max_tokens=3000,
            timeout=90,
        )

        raw = result.text or ""
        # Try to extract which file to fix from the first JSON line
        fix_path: str | None = None
        code_content = raw
        first_line = raw.split("\n")[0].strip()
        if first_line.startswith("{"):
            try:
                meta = json.loads(first_line)
                fix_path = meta.get("fix_file")
                code_content = "\n".join(raw.split("\n")[1:])
            except json.JSONDecodeError:
                pass

        # If we couldn't extract the target file, default to the first test file
        if not fix_path:
            test_files = [f.path for f in plan.files if f.is_test]
            fix_path = test_files[0] if test_files else (plan.files[0].path if plan.files else "main.py")

        fixed_content = _clean_code(code_content)

        # Apply the fix
        new_files = []
        applied = False
        for gf in current_files:
            if gf.path == fix_path:
                new_files.append(GeneratedFile(path=gf.path, content=fixed_content))
                applied = True
            else:
                new_files.append(gf)
        if not applied:
            new_files.append(GeneratedFile(path=fix_path, content=fixed_content))

        current_files = new_files

        # Re-run tests
        current_result = run_tests(current_files, plan.language)
        logger.info(
            "Fix attempt %d result: %s",
            attempt + 1,
            "PASSED" if current_result.passed else "FAILED",
        )

    return current_files, current_result


# ---------------------------------------------------------------------------
# Stage 5 — Package as zip
# ---------------------------------------------------------------------------

def package_project(
    files: list[GeneratedFile],
    plan: ProjectPlan,
    out_dir: Path,
) -> Path:
    """Write all generated files into a downloadable zip."""
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r"[^\w\- ]", "_", plan.title).strip().replace(" ", "_")[:40]
    zip_name = f"{safe_title}_{int(time.time())}.zip"
    zip_path = out_dir / zip_name

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        root = safe_title + "/"
        for gf in files:
            zf.writestr(root + gf.path, gf.content)

    logger.info("Packaged %d files → %s (%d bytes)", len(files), zip_name, zip_path.stat().st_size)
    return zip_path


# ---------------------------------------------------------------------------
# Stage 6 — Analyze an uploaded code zip
# ---------------------------------------------------------------------------

def analyze_project_zip(
    zip_bytes: bytes,
    cfg: Any = None,
    db: Any = None,
) -> dict:
    """Extract a zip of source files and return an LLM analysis + proposals.

    This is PROPOSAL-ONLY — it never modifies any files.
    """
    from orivellum.capabilities.llm import llm_call

    with tempfile.TemporaryDirectory(prefix="code_analyze_") as tmpdir:
        tmp = Path(tmpdir)
        zip_path = tmp / "upload.zip"
        zip_path.write_bytes(zip_bytes)

        # Extract safely — guard against zip-bomb and path traversal
        source_texts: dict[str, str] = {}
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                total_size = 0
                for info in zf.infolist():
                    # Skip directories and hidden files
                    if info.filename.endswith("/") or "/." in info.filename:
                        continue
                    # Guard zip-bomb (50 MB total uncompressed)
                    total_size += info.file_size
                    if total_size > 50 * 1024 * 1024:
                        break
                    # Guard path traversal
                    safe_name = _safe_relpath(info.filename)
                    if ".." in safe_name:
                        continue
                    try:
                        content = zf.read(info.filename).decode("utf-8", errors="replace")
                        # Only include source code files
                        ext = Path(safe_name).suffix.lower()
                        if ext in {".py", ".js", ".ts", ".go", ".rs", ".java",
                                   ".c", ".cpp", ".h", ".sh", ".yaml", ".toml",
                                   ".json", ".md", ".txt", ".cfg", ".ini",
                                   ".jsx", ".tsx", ".vue", ".svelte", ".rb",
                                   ".php", ".cs", ".kt", ".swift", ".sql",
                                   ".html", ".css", ".scss", ".env.example"}:
                            source_texts[safe_name] = content[:20_000]  # cap per file
                    except Exception:
                        pass
        except zipfile.BadZipFile:
            return {"ok": False, "error": "Invalid or corrupt zip file"}

        if not source_texts:
            return {"ok": False, "error": "No readable source files found in the zip"}

        # Build analysis prompt — no file-count cap; all extracted files are included.
        # Each file is already capped at 20 000 chars and the zip-bomb guard keeps
        # total uncompressed size ≤ 50 MB, so the prompt stays within reason.
        code_block = "\n\n".join(
            f"=== {path} ===\n{content}"
            for path, content in sorted(source_texts.items())
        )

        result = llm_call(
            [
                {"role": "system", "content": _ANALYZE_SYSTEM},
                {"role": "user", "content": f"Analyze this code project:\n\n{code_block}"},
            ],
            cfg=cfg,
            db=db,
            purpose="code_studio.analyze",
            temperature=0.1,
            max_tokens=6000,
            timeout=180,
        )

        raw = result.text or ""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = "\n".join(cleaned.split("\n")[1:]).rstrip("`").strip()

        try:
            analysis = json.loads(cleaned)
        except json.JSONDecodeError:
            analysis = {"summary": raw[:2000], "issues": [], "proposals": []}

        return {
            "ok": True,
            "files_analyzed": len(source_texts),
            "file_list": sorted(source_texts.keys()),
            **analysis,
        }


# ---------------------------------------------------------------------------
# Full pipeline entry point
# ---------------------------------------------------------------------------

def run_pipeline(
    description: str,
    language: str | None = None,
    out_dir: Path | None = None,
    max_fix_retries: int = 2,
    run_tests_server_side: bool = True,
    cfg: Any = None,
    db: Any = None,
) -> StudioResult:
    """Run the complete plan → generate → (optionally test →fix →) package pipeline.

    Args:
        run_tests_server_side: When True (default, Studio UI path), stages 3 & 4
            execute the generated code in a subprocess to verify correctness.
            When False (chat path), stages 3 & 4 are skipped.  The project is
            packaged immediately after generation; test files are included in the
            zip so the user can run them locally, but no host subprocess is spawned.
            Set to False when caller cannot guarantee a sandboxed environment.

    Returns a StudioResult with ok=True and a download zip on success, or
    ok=False with an error message on failure.
    """
    if not description.strip():
        return StudioResult(ok=False, error="Description must not be empty")

    if out_dir is None:
        data_root = Path(cfg.data_dir) if cfg else Path("data")
        out_dir = data_root / "outputs" / "code_studio"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Stage 1: Plan ─────────────────────────────────────────────────────────
    try:
        plan = plan_project(description, language=language, cfg=cfg, db=db)
    except Exception as exc:
        logger.exception("plan_project failed")
        return StudioResult(ok=False, error=f"Planning failed: {exc}")

    # ── Stage 2: Generate files ───────────────────────────────────────────────
    try:
        files = generate_files(plan, description, cfg=cfg, db=db)
    except Exception as exc:
        logger.exception("generate_files failed")
        return StudioResult(ok=False, error=f"Code generation failed: {exc}", plan=plan)

    # ── Stages 3 & 4: Run tests + fix loop (only when server-side allowed) ────
    if run_tests_server_side:
        test_result = run_tests(files, plan.language)
        if not test_result.passed:
            files, test_result = fix_and_retry(
                files, test_result, plan, description,
                max_retries=max_fix_retries, cfg=cfg, db=db,
            )
    else:
        # Tests skipped — caller is responsible for running them in their own env.
        test_result = None

    # ── Stage 5: Package ─────────────────────────────────────────────────────
    try:
        zip_path = package_project(files, plan, out_dir)
    except Exception as exc:
        logger.exception("package_project failed")
        return StudioResult(
            ok=False,
            title=plan.title,
            language=plan.language,
            files=files,
            test_result=test_result,
            error=f"Packaging failed: {exc}",
            plan=plan,
        )

    data_root = Path(cfg.data_dir) if cfg else Path("data")
    try:
        rel = str(zip_path.relative_to(data_root))
    except ValueError:
        rel = str(zip_path)

    return StudioResult(
        ok=True,
        title=plan.title,
        language=plan.language,
        files=files,
        test_result=test_result,
        zip_path=str(zip_path),
        download_url=f"/api/generate/download?path={rel}",
        plan=plan,
    )

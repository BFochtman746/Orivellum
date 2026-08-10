"""Document Workshop — self-prompting, AI code-generated, critique-looped documents.

Pipeline:
  1. plan_document()   — LLM generates clarifying questions based on user request
  2. execute_workshop() — LLM writes Python script → safe sandbox execution →
                          write.critic critique → registered output

Supports: xlsx, docx, pdf, pptx
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.workshop")

# ── In-memory session store (ephemeral, cleared on restart) ────────────────────
_SESSIONS: dict[str, dict] = {}

ALLOWED_FORMATS = {"xlsx", "docx", "pdf", "pptx"}
_FORMAT_LABELS = {
    "xlsx": "Excel Workbook",
    "docx": "Word Document",
    "pdf":  "PDF Report",
    "pptx": "PowerPoint Presentation",
}
_FORMAT_PACKAGES = {
    "xlsx": "openpyxl",
    "docx": "python-docx (import as `from docx import Document`)",
    "pdf":  "reportlab (from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table; from reportlab.lib import colors; from reportlab.lib.styles import getSampleStyleSheet)",
    "pptx": "python-pptx (from pptx import Presentation; from pptx.util import Inches, Pt; from pptx.dml.color import RGBColor)",
}

# ── Clarification-question prompt ──────────────────────────────────────────────

_PLAN_SYSTEM = """\
You are a precision document-production planner. A user has asked for a document.
Your job is to generate the minimum set of clarifying questions that will allow
an AI to produce exactly what they need on the first attempt — no guessing.

Focus on:
- Content specifics (what data, what topics, in what order)
- Audience and tone (who will read this, how formal)
- Visual style (colors, branding, charts vs tables)
- Scope (length, number of slides/sheets/sections)
- Any constraints or must-haves

Do NOT ask about things the user already specified. Be concise and direct.

Return ONLY valid JSON — no markdown, no explanation — in this exact schema:
{
  "detected_format": "xlsx|docx|pdf|pptx",
  "detected_intent": "one-sentence summary of what the user wants",
  "questions": [
    {
      "id": "q1",
      "question": "...",
      "type": "text|choice|multiselect",
      "options": ["option a", "option b"],
      "hint": "brief hint for the user"
    }
  ]
}

Omit "options" when type is "text". Generate 4–6 questions maximum.
"""

# ── Code-generation prompt ─────────────────────────────────────────────────────

_CODEGEN_SYSTEM = """\
You are a master-quality document engineer. You write Python scripts that produce
professional, publication-ready documents using only these packages:
  - openpyxl (Excel)
  - python-docx / docx (Word)
  - reportlab (PDF)
  - python-pptx / pptx (PowerPoint)
  - matplotlib (charts — save as PNG, embed via openpyxl/pptx/docx add_picture)
  - Pillow / PIL (image processing)
  - json, datetime, pathlib, os, re, textwrap (stdlib only)

Rules you MUST follow:
1. The script saves the final file to the exact path in OUTPUT_PATH variable.
2. No network calls, no subprocess calls, no file system access outside /tmp and OUTPUT_PATH.
3. Use professional color palette unless user specified otherwise:
   primary #1E3A8A (blue), accent #7C3AED (purple), highlight #DB2777 (pink),
   neutral #1E293B (dark), background #F8FAFC (light).
4. Every sheet/slide/section must have a clear heading and be fully populated —
   no placeholder text like "add content here".
5. Excel: freeze panes on row 1, autofit columns, add charts where data warrants.
6. PPTX: title slide + content slides, consistent font sizes (title 36pt, body 20pt).
7. DOCX: proper headings hierarchy, professional margins, table of contents stub.
8. PDF: header/footer with page numbers, consistent styles via getSampleStyleSheet.
9. Handle exceptions with try/except and write errors to stderr.
10. Return ONLY the raw Python script — no markdown fences, no explanation.
"""

# ── Critique prompt ────────────────────────────────────────────────────────────

_CRITIQUE_SYSTEM = """\
You are an adversarial document quality auditor. You receive:
- The user's original request
- The answers they gave to clarifying questions
- The Python script that was executed to generate their document
- Any execution output or errors

Your job: evaluate whether the script would produce exactly what was requested,
identify gaps, and give actionable improvement suggestions.

Score on these dimensions (1–10 each):
1. COMPLETENESS — Does it cover every topic/section the user requested?
2. ACCURACY — Is the data/content faithful to the user's specifications?
3. DESIGN — Are colors, fonts, and layout professionally chosen?
4. PROFESSIONALISM — Would a domain expert be proud to share this?

Return JSON only:
{
  "scores": {"completeness": N, "accuracy": N, "design": N, "professionalism": N},
  "overall": N,
  "strengths": ["..."],
  "gaps": ["..."],
  "suggestions": ["specific improvement the user could request"],
  "verdict": "one-sentence overall assessment"
}
"""


# ── Public API ─────────────────────────────────────────────────────────────────

def plan_document(
    request: str,
    format_hint: str | None,
    work_id: str | None,
    db: OrivellumDB,
    cfg: OrivellumConfig,
) -> dict:
    """Generate clarifying questions for a document request.

    Returns a session dict with session_id, questions, detected_format, detected_intent.
    """
    from orivellum.capabilities.llm import llm_call

    # Build work context snippet
    work_ctx = ""
    if work_id:
        work = db.get_work(work_id)
        if work:
            work_ctx = f"\nWork context: \"{work.get('title', '')}\" — {(work.get('description') or '')[:200]}"

    user_msg = f"Request: {request.strip()}"
    if format_hint and format_hint in ALLOWED_FORMATS:
        user_msg += f"\nDesired format: {_FORMAT_LABELS[format_hint]}"
    if work_ctx:
        user_msg += work_ctx

    result = llm_call(
        [
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user",   "content": user_msg},
        ],
        cfg=cfg, db=db, purpose="workshop.plan",
        temperature=0.3, max_tokens=1200, timeout=45,
    )

    plan: dict[str, Any] = {}
    if result.ok and result.text:
        raw = result.text.strip()
        # Strip markdown fences if the model adds them
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.M)
        raw = re.sub(r"```\s*$", "", raw, flags=re.M)
        try:
            plan = json.loads(raw.strip())
        except json.JSONDecodeError:
            # Attempt to extract embedded JSON object
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                try:
                    plan = json.loads(m.group())
                except Exception:
                    pass

    # Fallback questions if LLM fails or returns bad JSON
    if not plan.get("questions"):
        plan = {
            "detected_format": format_hint or "docx",
            "detected_intent": request[:120],
            "questions": [
                {"id": "q1", "question": "What is the primary audience for this document?",
                 "type": "text", "hint": "e.g. executives, students, general public"},
                {"id": "q2", "question": "What tone should the writing take?",
                 "type": "choice", "options": ["Formal / Academic", "Professional / Business", "Conversational"],
                 "hint": "Affects word choice and paragraph density"},
                {"id": "q3", "question": "What sections or topics must be included?",
                 "type": "text", "hint": "List them, one per line"},
                {"id": "q4", "question": "Should charts or tables be included?",
                 "type": "choice", "options": ["Yes — as many as useful", "Only if essential", "Text only"],
                 "hint": "Visual elements add richness but require more data"},
            ],
        }

    # Normalise detected_format
    detected = plan.get("detected_format", format_hint or "docx").lower()
    if detected not in ALLOWED_FORMATS:
        detected = format_hint or "docx"
    plan["detected_format"] = detected

    session_id = str(uuid.uuid4())
    session = {
        "id": session_id,
        "request": request,
        "work_id": work_id,
        "format": detected,
        "detected_intent": plan.get("detected_intent", request[:120]),
        "questions": plan.get("questions", []),
        "created_at": datetime.now(UTC).isoformat(),
    }
    _SESSIONS[session_id] = session
    logger.info("Workshop plan created: session=%s format=%s q=%d",
                session_id, detected, len(session["questions"]))
    return session


def execute_workshop(
    session_id: str | None,
    request: str,
    format: str,
    work_id: str | None,
    answers: dict[str, str],
    db: OrivellumDB,
    cfg: OrivellumConfig,
) -> dict:
    """Generate the document: write code → execute safely → critique → register.

    Returns a result dict with ok, path, download_url, critique, doc_id.
    """
    from orivellum.capabilities.llm import llm_call

    fmt = format.lower() if format else "docx"
    if fmt not in ALLOWED_FORMATS:
        fmt = "docx"

    # Reconstruct context from session if available
    session = _SESSIONS.get(session_id or "") if session_id else None
    if session:
        request = request or session["request"]
        work_id = work_id or session.get("work_id")
        fmt = session.get("format", fmt)
        questions = session.get("questions", [])
    else:
        questions = []

    # Build work context
    work_title = "General"
    knowledge_ctx = ""
    if work_id:
        work = db.get_work(work_id)
        if work:
            work_title = work.get("title", "General")
            items = db.list_knowledge(work_id=work_id, limit=20)
            if items:
                knowledge_ctx = "\n\nAvailable knowledge from the Work:\n" + "\n".join(
                    f"- [{i.get('kind','fact')}] {(i.get('text') or '')[:200]}"
                    for i in items[:20]
                )

    # Build answers narrative
    answers_text = ""
    if answers:
        q_map = {q["id"]: q["question"] for q in questions}
        answers_text = "\n\nUser specifications:\n" + "\n".join(
            f"Q: {q_map.get(qid, qid)}\nA: {ans}"
            for qid, ans in answers.items()
            if ans and str(ans).strip()
        )

    # Prepare output path
    out_dir = Path(cfg.data_dir) / "outputs" / "generate" / (work_id or "workshop")
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w]", "_", request[:40].strip().lower())
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M")
    fname = f"{slug}_{ts}.{fmt}"
    output_path = str(out_dir / fname)

    # ── Step 1: LLM writes the generation script ───────────────────────────────
    codegen_user = (
        f"Generate a Python script to create a {_FORMAT_LABELS[fmt]}.\n\n"
        f"REQUEST: {request}\n"
        f"WORK: {work_title}\n"
        f"OUTPUT_PATH = {output_path!r}  # The script MUST save the file here\n"
        f"PACKAGE: {_FORMAT_PACKAGES[fmt]}\n"
        f"{answers_text}"
        f"{knowledge_ctx}"
        f"\n\nRemember: save to OUTPUT_PATH exactly. Return ONLY the raw Python script."
    )

    script_result = llm_call(
        [
            {"role": "system", "content": _CODEGEN_SYSTEM},
            {"role": "user",   "content": codegen_user},
        ],
        cfg=cfg, db=db, purpose="workshop.codegen",
        temperature=0.2, max_tokens=4000, timeout=90,
    )

    if not script_result.ok or not script_result.text:
        return {
            "ok": False,
            "error": f"Code generation failed: {script_result.error}",
            "critique": None,
        }

    script = _clean_script(script_result.text)

    # ── Step 2: Execute with retry loop ───────────────────────────────────────
    exec_result = _run_script_safely(script, output_path, max_retries=2,
                                     cfg=cfg, db=db, request=codegen_user)
    if not exec_result["ok"]:
        return {
            "ok": False,
            "error": exec_result.get("error", "Script execution failed"),
            "script": script,
            "critique": None,
        }

    final_path = Path(output_path)
    if not final_path.exists():
        # LLM may have saved with slightly different path — search the dir
        candidates = sorted(out_dir.glob(f"*.{fmt}"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            final_path = candidates[0]
        else:
            return {
                "ok": False,
                "error": "Script ran without errors but no output file was found.",
                "script": script,
            }

    # ── Step 3: Critique ──────────────────────────────────────────────────────
    critique = _critique_output(
        request=request,
        answers_text=answers_text,
        script=script,
        exec_output=exec_result.get("stdout", ""),
        cfg=cfg, db=db,
    )

    # ── Step 4: Register as library document ──────────────────────────────────
    from orivellum.capabilities.generate import _register_output
    title_out = f"Workshop — {request[:60]}"
    doc_id = _register_output(
        final_path, work_id, db, cfg, f"workshop/{fmt}", title_out,
        text_content=f"Generated from: {request}\n{answers_text}",
    )

    data_dir = Path(cfg.data_dir)
    try:
        rel = str(final_path.relative_to(data_dir))
    except ValueError:
        rel = str(final_path)

    logger.info("Workshop output: %s → doc %s", final_path.name, doc_id)

    return {
        "ok": True,
        "doc_id": doc_id,
        "filename": final_path.name,
        "path": rel,
        "download_url": f"/api/generate/download?path={rel}",
        "size_bytes": final_path.stat().st_size,
        "script": script,
        "critique": critique,
    }


# ── Helpers ────────────────────────────────────────────────────────────────────

def _clean_script(raw: str) -> str:
    """Strip markdown fences and leading/trailing whitespace from LLM output."""
    s = raw.strip()
    s = re.sub(r"^```(?:python)?\s*\n?", "", s, flags=re.M)
    s = re.sub(r"\n?```\s*$", "", s, flags=re.M)
    return s.strip()


# ── Sandbox runner ────────────────────────────────────────────────────────────
# The LLM-written script runs under this wrapper, which disables network
# access at the socket layer: every connection-creating entry point in both
# `socket` and the C `_socket` module is replaced with one that raises before
# user code runs. Imports stay allowed (reportlab/python-pptx legitimately
# import urllib internals without using the network), but any actual
# connection attempt fails. Combined with a scrubbed environment (no parent
# secrets), `-I` isolated mode, and POSIX resource limits, this is a strong
# guard against hallucinating or prompt-influenced generated code. It is a
# best-effort in-process guard, not an adversarial security boundary — code
# determined to escape (ctypes, re-exec) needs OS-level isolation, which is
# not available on the Windows deployment target.
_SANDBOX_RUNNER = """\
import _socket
import socket
import sys


def _deny(*_a, **_k):
    raise OSError(
        "Network access is disabled in the document-generation sandbox.")


class _DeniedSocket:
    def __init__(self, *a, **k):
        _deny()


for _mod in (socket, _socket):
    _mod.socket = _DeniedSocket
    for _name in ("create_connection", "create_server", "socketpair",
                  "getaddrinfo", "gethostbyname", "gethostbyname_ex",
                  "gethostbyaddr", "fromfd"):
        if hasattr(_mod, _name):
            setattr(_mod, _name, _deny)
socket.SocketType = _DeniedSocket

import runpy

runpy.run_path(sys.argv[1], run_name="__main__")
"""


def _sandbox_env(tmp: str) -> dict:
    """Minimal environment for the sandboxed script — never the parent's
    os.environ (which carries API keys and session secrets)."""
    env = {
        "HOME": tmp, "TMPDIR": tmp, "TEMP": tmp, "TMP": tmp,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for key in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC"):  # Windows needs these
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def _sandbox_preexec():
    """POSIX-only resource caps applied in the child before exec."""
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (100 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))


def _run_script_safely(
    script: str,
    output_path: str,
    max_retries: int,
    cfg: OrivellumConfig,
    db: OrivellumDB,
    request: str,
) -> dict:
    """Execute generated script sandboxed in a temp dir: scrubbed environment,
    isolated interpreter, network-module import blocking, and (on POSIX)
    CPU/memory/file-size caps. Retry with LLM correction on failure."""
    from orivellum.capabilities.llm import llm_call

    current_script = script
    for attempt in range(max_retries + 1):
        with tempfile.TemporaryDirectory() as tmp:
            script_path = os.path.join(tmp, "generate_doc.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(current_script)
            runner_path = os.path.join(tmp, "_sandbox_runner.py")
            with open(runner_path, "w", encoding="utf-8") as f:
                f.write(_SANDBOX_RUNNER)

            try:
                result = subprocess.run(
                    [sys.executable, "-I", runner_path, script_path],
                    capture_output=True, text=True,
                    timeout=60,
                    cwd=tmp,
                    env=_sandbox_env(tmp),
                    preexec_fn=_sandbox_preexec if sys.platform != "win32" else None,
                )
                stdout = result.stdout[:3000]
                stderr = result.stderr[:3000]

                if result.returncode == 0:
                    return {"ok": True, "stdout": stdout, "stderr": stderr,
                            "script": current_script}

                # ── Failure → LLM correction ───────────────────────────────
                if attempt >= max_retries:
                    return {
                        "ok": False,
                        "error": f"Script failed after {attempt + 1} attempt(s):\n{stderr[-1000:]}",
                        "stdout": stdout, "stderr": stderr,
                    }

                logger.warning("Workshop script attempt %d failed: %s", attempt + 1, stderr[:400])
                fix_result = llm_call(
                    [
                        {"role": "system", "content": (
                            "You are a Python debugging expert. A document-generation script failed. "
                            "Fix ONLY the errors shown. Do not add new features. "
                            "Return ONLY the corrected raw Python script."
                        )},
                        {"role": "user", "content": (
                            f"Original request:\n{request[:500]}\n\n"
                            f"Script that failed:\n```python\n{current_script}\n```\n\n"
                            f"Error:\n{stderr[-2000:]}"
                        )},
                    ],
                    cfg=cfg, db=db, purpose="workshop.fix",
                    temperature=0.1, max_tokens=4000, timeout=60,
                )
                if fix_result.ok and fix_result.text:
                    current_script = _clean_script(fix_result.text)
                else:
                    return {"ok": False, "error": f"Fix generation failed: {fix_result.error}"}

            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "Script execution timed out (60s)"}
            except Exception as exc:
                return {"ok": False, "error": f"Execution error: {exc}"}

    return {"ok": False, "error": "Max retries exceeded"}


def _critique_output(
    request: str,
    answers_text: str,
    script: str,
    exec_output: str,
    cfg: OrivellumConfig,
    db: OrivellumDB,
) -> dict | None:
    """Run the write.critic evaluation on the generated document."""
    from orivellum.capabilities.llm import llm_call

    # Try to get active write.critic prompt, fall back to built-in
    critic_system = _CRITIQUE_SYSTEM
    try:
        active = db.get_active_prompt("write.critic")
        if active:
            critic_system = active + "\n\n" + _CRITIQUE_SYSTEM
    except Exception:
        pass

    user_msg = (
        f"User request: {request}\n"
        f"{answers_text}\n\n"
        f"Generated script (abbreviated):\n{script[:3000]}\n\n"
        f"Execution output: {exec_output[:500] or '(none)'}"
    )

    result = llm_call(
        [
            {"role": "system", "content": critic_system},
            {"role": "user",   "content": user_msg},
        ],
        cfg=cfg, db=db, purpose="workshop.critique",
        temperature=0.3, max_tokens=800, timeout=45,
    )

    if not result.ok or not result.text:
        return None

    raw = result.text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.M)
    raw = re.sub(r"```\s*$", "", raw, flags=re.M)
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group()) if m else None
    except Exception:
        return {"verdict": raw[:400], "scores": {}, "suggestions": [], "gaps": [], "strengths": []}

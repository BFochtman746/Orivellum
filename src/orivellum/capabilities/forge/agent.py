"""Forge BUILD phase — LLM tool-calling agent that writes files to a build directory.

The agent receives the approved site plan + selected visual design concept and
iteratively calls write_file / read_file / list_files tools to produce a
complete static website (HTML + CSS + JS) in the build directory.

Tool loop exits when the model outputs {"tool": "done", "summary": "…"}.
"""
from __future__ import annotations

import json
import logging
import pathlib
import subprocess
import textwrap
from collections.abc import Callable

from orivellum.capabilities.llm import llm_call

logger = logging.getLogger(__name__)

MAX_ROUNDS = 30
MAX_FILE_READ = 8000   # chars
MAX_OUTPUT   = 4000    # chars of subprocess output

POLICY_ALLOWED_CMDS = {"node", "npm", "npx", "eslint"}

AGENT_SYSTEM = """You are an expert front-end engineer.
Build a complete static website (HTML + CSS + JS) in the working directory.
Follow the site plan and visual design concept exactly.

You interact with the filesystem using JSON tool calls (one per reply):

  {"tool":"write_file","path":"<rel-path>","content":"<full-file-content>"}
  {"tool":"read_file","path":"<rel-path>"}
  {"tool":"list_files","dir":"."}
  {"tool":"run","cmd":"<allowed-command>"}   -- allowed: node, npm, npx, eslint
  {"tool":"done","summary":"<what you built>"}

Rules:
- Output EXACTLY ONE JSON tool call per reply — no prose, no markdown.
- Paths are relative to the project root.
- Write index.html, styles.css, app.js, and at least one inner page.
- Use the palette colours and typography from the visual design concept.
- Include a design-tokens.css with :root variables for every colour and font.
- Respond {"tool":"done","summary":"…"} when the site is complete.
"""


def _run_cmd(cmd: str, cwd: pathlib.Path) -> str:
    """Run an allowlisted command and return combined stdout+stderr (capped)."""
    parts = cmd.split()
    if not parts or parts[0] not in POLICY_ALLOWED_CMDS:
        return f"[BLOCKED] Command not allowed: {parts[0] if parts else '(empty)'}"
    try:
        proc = subprocess.run(
            parts, cwd=str(cwd), capture_output=True, text=True, timeout=30,
        )
        out = (proc.stdout + proc.stderr)[:MAX_OUTPUT]
        return out if out else f"[exit {proc.returncode}]"
    except subprocess.TimeoutExpired:
        return "[TIMEOUT] Command exceeded 30 s"
    except Exception as exc:
        return f"[ERROR] {exc}"


def _tool_result(tool: str, result: str) -> dict:
    return {"role": "user", "content": f"[tool result — {tool}]\n{result}"}


def run_builder(
    cfg: object,
    db: object,
    build_dir: pathlib.Path,
    plan: dict,
    concept: dict,
    instruction: str = "",
    on_event: Callable | None = None,
    max_rounds: int = MAX_ROUNDS,
) -> str:
    """Run the build agent.  Returns a summary string."""
    build_dir.mkdir(parents=True, exist_ok=True)

    system_prompt = AGENT_SYSTEM
    user_primer = textwrap.dedent(f"""
        Site plan:
        {json.dumps(plan, indent=2, ensure_ascii=False)[:3000]}

        Selected visual concept:
        {json.dumps(concept, indent=2, ensure_ascii=False)[:2000]}

        {"Additional instruction: " + instruction if instruction else ""}

        Begin building.  Start with index.html.
    """).strip()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_primer},
    ]

    if on_event:
        on_event("build_start", "Build agent started — writing files…")

    for round_num in range(max_rounds):
        result = llm_call(
            messages,
            cfg=cfg,
            db=db,
            purpose="forge.build_agent",
            timeout=90,
            max_tokens=2000,
        )

        if not result.ok or not result.text:
            raise RuntimeError(f"Agent LLM call failed: {result.error}")

        raw = result.text.strip()
        messages.append({"role": "assistant", "content": raw})

        # Parse the tool call
        try:
            text = raw
            for fence in ("```json", "```"):
                if fence in text:
                    text = text.split(fence, 1)[1].rsplit("```", 1)[0]
                    break
            call = json.loads(text.strip())
        except json.JSONDecodeError:
            tool_res = "[PARSE ERROR] Reply was not valid JSON. Output exactly one JSON tool call."
            messages.append(_tool_result("parse_error", tool_res))
            continue

        tool = call.get("tool", "")

        if tool == "done":
            summary = call.get("summary", "Build complete.")
            if on_event:
                on_event("build_done", summary)
            return summary

        elif tool == "write_file":
            rel = call.get("path", "")
            content = call.get("content", "")
            if not rel:
                messages.append(_tool_result("write_file", "[ERROR] missing 'path'"))
                continue
            target = (build_dir / rel).resolve()
            # Jail check
            if not str(target).startswith(str(build_dir.resolve())):
                messages.append(_tool_result("write_file", "[BLOCKED] path outside build dir"))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            if on_event:
                on_event("file_written", f"Wrote {rel} ({len(content)} chars)")
            messages.append(_tool_result("write_file", f"OK — wrote {len(content)} chars to {rel}"))

        elif tool == "read_file":
            rel = call.get("path", "")
            target = (build_dir / rel).resolve()
            if not str(target).startswith(str(build_dir.resolve())):
                messages.append(_tool_result("read_file", "[BLOCKED] path outside build dir"))
                continue
            if target.exists():
                content = target.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_READ]
                messages.append(_tool_result("read_file", content))
            else:
                messages.append(_tool_result("read_file", "[NOT FOUND]"))

        elif tool == "list_files":
            dir_rel = call.get("dir", ".")
            target_dir = (build_dir / dir_rel).resolve()
            if not str(target_dir).startswith(str(build_dir.resolve())):
                messages.append(_tool_result("list_files", "[BLOCKED]"))
                continue
            if target_dir.is_dir():
                entries = sorted(str(p.relative_to(build_dir))
                                 for p in target_dir.rglob("*") if not p.is_dir())
                messages.append(_tool_result("list_files", "\n".join(entries[:200]) or "(empty)"))
            else:
                messages.append(_tool_result("list_files", "[DIR NOT FOUND]"))

        elif tool == "run":
            cmd = call.get("cmd", "")
            out = _run_cmd(cmd, build_dir)
            if on_event:
                on_event("cmd_run", f"$ {cmd}", {"output": out[:500]})
            messages.append(_tool_result("run", out))

        else:
            messages.append(_tool_result(tool, f"[UNKNOWN TOOL] {tool}"))

    # Exhausted rounds — still return whatever was built
    if on_event:
        on_event("build_done", f"Build agent finished after {max_rounds} rounds.")
    return f"Completed after {max_rounds} rounds."

"""Sandbox filesystem boundary for AI-built project scripts.

The shared sandbox runner (Workshop + Workbench) installs an audit hook that
restricts file access to the working directory (plus the Python installation
and any parent-granted extra dirs). These tests execute the REAL sandbox
subprocess and verify:
- a build script cannot read a file outside its working directory
- a build script cannot create symlinks (no laundering outside bytes into out/)
- legitimate builds inside the working dir still succeed
- _snapshot refuses output directories containing symlinks
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestSandboxFilesystemBoundary(unittest.TestCase):
    def _run(self, script: str, workdir: Path) -> dict:
        from orivellum.capabilities import workbench

        # No LLM repair loop — a sandbox denial must fail the build outright.
        with patch.object(workbench, "_MAX_FIX_RETRIES", 0):
            return workbench._run_build_script(script, workdir, cfg=None, db=None, request="t")

    def test_script_cannot_read_outside_working_dir(self):
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as work:
            secret = Path(outside) / "secret.txt"
            secret.write_text("api-key-do-not-leak", encoding="utf-8")
            script = (
                "import pathlib\n"
                f"data = pathlib.Path({str(secret)!r}).read_text()\n"
                "out = pathlib.Path('out'); out.mkdir(exist_ok=True)\n"
                "out.joinpath('steal.txt').write_text(data)\n"
            )
            run = self._run(script, Path(work))
            self.assertFalse(run["ok"])
            self.assertIn("outside the working directory", run["error"])
            self.assertFalse((Path(work) / "out" / "steal.txt").exists())

    def test_script_cannot_create_symlinks(self):
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as work:
            secret = Path(outside) / "secret.txt"
            secret.write_text("api-key-do-not-leak", encoding="utf-8")
            script = (
                "import os, pathlib\n"
                "out = pathlib.Path('out'); out.mkdir(exist_ok=True)\n"
                f"os.symlink({str(secret)!r}, out / 'link.txt')\n"
            )
            run = self._run(script, Path(work))
            self.assertFalse(run["ok"])
            self.assertIn("links", run["error"].lower())

    def test_legitimate_build_inside_working_dir_still_works(self):
        with tempfile.TemporaryDirectory() as work:
            (Path(work) / "inputs").mkdir()
            (Path(work) / "inputs" / "seed.txt").write_text("7", encoding="utf-8")
            script = (
                "import pathlib\n"
                "seed = pathlib.Path('inputs/seed.txt').read_text().strip()\n"
                "out = pathlib.Path('out'); out.mkdir(exist_ok=True)\n"
                "out.joinpath('result.txt').write_text(f'seed={seed}')\n"
                "print('built')\n"
            )
            run = self._run(script, Path(work))
            self.assertTrue(run["ok"], run.get("error"))
            self.assertEqual(
                (Path(work) / "out" / "result.txt").read_text(encoding="utf-8"), "seed=7"
            )

    def test_workshop_allowlist_grants_only_the_output_dir(self):
        """The Workshop grants its output dir via ORIVELLUM_SANDBOX_ALLOW —
        writes there succeed while everything else stays blocked."""
        import subprocess

        from orivellum.capabilities.workshop import (
            _SANDBOX_RUNNER,
            _sandbox_env,
            _sandbox_preexec,
        )

        with tempfile.TemporaryDirectory() as granted, tempfile.TemporaryDirectory() as work:
            runner = Path(work) / "_runner.py"
            runner.write_text(_SANDBOX_RUNNER, encoding="utf-8")
            target = Path(granted) / "doc.txt"
            script_path = Path(work) / "gen.py"
            script_path.write_text(
                "import pathlib\n"
                f"pathlib.Path({str(target)!r}).write_text('ok')\n"
                f"blocked = pathlib.Path({str(target)!r}).parent.parent / 'esc.txt'\n"
                "try:\n"
                "    blocked.write_text('escape')\n"
                "    raise SystemExit('escape write unexpectedly succeeded')\n"
                "except PermissionError:\n"
                "    pass\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, "-I", str(runner), str(script_path)],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=work,
                env=_sandbox_env(work, allow=[granted]),
                preexec_fn=_sandbox_preexec if sys.platform != "win32" else None,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), "ok")

    def test_script_cannot_write_under_python_installation(self):
        """Interpreter/dependency paths are readable (imports) but never
        writable — a script must not be able to tamper with installed
        packages."""
        import sysconfig

        target = str(Path(sysconfig.get_paths()["purelib"]) / "_sandbox_evil_marker.txt")
        with tempfile.TemporaryDirectory() as work:
            script = (
                "import openpyxl  # dependency reads still allowed\n"
                "try:\n"
                f"    open({target!r}, 'w').write('tampered')\n"
                "    raise SystemExit('write into site-packages unexpectedly succeeded')\n"
                "except PermissionError:\n"
                "    pass\n"
                "import pathlib\n"
                "out = pathlib.Path('out'); out.mkdir(exist_ok=True)\n"
                "out.joinpath('r.txt').write_text('ok')\n"
            )
            run = self._run(script, Path(work))
            self.assertTrue(run["ok"], run.get("error"))
            self.assertFalse(Path(target).exists())

    def test_script_cannot_delete_outside_files(self):
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as work:
            victim = Path(outside) / "victim.txt"
            victim.write_text("keep me", encoding="utf-8")
            script = f"import os\nos.remove({str(victim)!r})\n"
            run = self._run(script, Path(work))
            self.assertFalse(run["ok"])
            self.assertIn("outside the working directory", run["error"])
            self.assertTrue(victim.exists())

    def test_workshop_output_selection_rejects_symlinked_fallback(self):
        """When the prescribed path is missing and the fallback picks the
        newest candidate, a symlinked candidate must still be rejected."""
        from orivellum.capabilities.workshop import _select_output

        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as work:
            secret = Path(outside) / "secret.docx"
            secret.write_text("leak", encoding="utf-8")
            out_dir = Path(work)
            os.symlink(secret, out_dir / "candidate.docx")
            with self.assertRaises(ValueError):
                _select_output(str(out_dir / "missing.docx"), out_dir, "docx")
            # A regular fallback candidate is still selected normally.
            real = out_dir / "real.docx"
            real.write_text("fine", encoding="utf-8")
            (out_dir / "candidate.docx").unlink()
            self.assertEqual(_select_output(str(out_dir / "missing.docx"), out_dir, "docx"), real)

    def test_script_cannot_spawn_a_child_process(self):
        """A child process would not inherit the audit hook, so spawning is
        denied — a child interpreter must never read outside files."""
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as work:
            secret = Path(outside) / "secret.txt"
            secret.write_text("api-key-do-not-leak", encoding="utf-8")
            inner = f"print(open({str(secret)!r}).read())"
            script = (
                "import subprocess, sys, pathlib\n"
                f"r = subprocess.run([sys.executable, '-c', {inner!r}],\n"
                "    capture_output=True, text=True)\n"
                "out = pathlib.Path('out'); out.mkdir(exist_ok=True)\n"
                "out.joinpath('steal.txt').write_text(r.stdout)\n"
            )
            run = self._run(script, Path(work))
            self.assertFalse(run["ok"])
            self.assertIn("launching processes is disabled", run["error"])
            self.assertFalse((Path(work) / "out" / "steal.txt").exists())

    def test_script_cannot_use_os_system(self):
        with tempfile.TemporaryDirectory() as work:
            run = self._run("import os\nos.system('id')\n", Path(work))
            self.assertFalse(run["ok"])
            self.assertIn("launching processes is disabled", run["error"])

    def test_snapshot_rejects_symlinked_output(self):
        from orivellum.capabilities.workbench import _snapshot

        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as work:
            secret = Path(outside) / "secret.txt"
            secret.write_text("leak", encoding="utf-8")
            out = Path(work) / "out"
            out.mkdir()
            (out / "fine.txt").write_text("ok", encoding="utf-8")
            os.symlink(secret, out / "link.txt")
            with self.assertRaises(ValueError) as ctx:
                _snapshot(pathlib.Path(out))
            self.assertIn("symlink", str(ctx.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

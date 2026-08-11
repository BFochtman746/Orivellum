"""Tests for the DeepFilterNet3 live re-probe (no-restart registration).

deepfilternet is not installed in the test environment's server interpreter,
so in-process probes fail — which is part of the surface being tested: the
failure must be described (error text + interpreter path), the failed result
must be cached, and a forced re-probe must clear that cache and retry fresh.

The uv sidecar (the automatic-setup path) is mocked throughout: a passive
probe must NEVER spawn a subprocess, and the forced probe's setup/success/
failure handling is tested against fake subprocess results.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from orivellum.capabilities import enhancement
from tests.conftest import AUTH_HEADERS


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


def _reset_state():
    enhancement._df_model = None
    enhancement._last_error = None
    enhancement._sidecar_ok = None
    enhancement._sidecar_error = None
    enhancement._setup_running = False
    enhancement._setup_pending = False
    enhancement._setup_progress = None


class _FakeStream:
    """File-like stdout for _FakePopen; optional per-line delay."""

    def __init__(self, lines, delay=0.0):
        self._lines = list(lines)
        self._delay = delay

    def __iter__(self):
        import time

        for line in self._lines:
            if self._delay:
                time.sleep(self._delay)
            yield line + "\n"

    def close(self):
        pass


class _FakePopen:
    """Stands in for subprocess.Popen in the streamed setup path."""

    def __init__(self, lines=(), returncode=0, delay=0.0):
        self.stdout = _FakeStream(lines, delay)
        self.returncode = returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass


class _SidecarIsolatedTest(unittest.TestCase):
    """Base: reset caches and force the sidecar to report unavailable."""

    def setUp(self):
        _reset_state()
        self._sidecar_patch = mock.patch.object(
            enhancement, "_sidecar_probe", lambda force=False: False
        )
        self._sidecar_patch.start()

    def tearDown(self):
        self._sidecar_patch.stop()
        _reset_state()


class TestProbeCapability(_SidecarIsolatedTest):
    def test_probe_reports_error_and_interpreter(self):
        result = enhancement.probe()
        self.assertFalse(result["available"])
        self.assertIsNone(result["mode"])
        self.assertIn("ImportError", result["error"] or "")
        self.assertEqual(result["python"], sys.executable)
        self.assertIn("Check again", result["install_hint"])

    def test_failed_probe_is_cached_until_forced(self):
        enhancement.probe()
        self.assertIs(enhancement._df_model, False)
        # Non-forced probe keeps the cached failure
        enhancement.probe(force=False)
        self.assertIs(enhancement._df_model, False)
        # Forced probe clears the cache and re-attempts the import
        # (it fails again here, but the cache was reset first)
        seen = []
        orig = enhancement._get_df_model

        def _spy():
            seen.append(enhancement._df_model)
            return orig()

        enhancement._get_df_model = _spy
        try:
            enhancement.probe(force=True)
        finally:
            enhancement._get_df_model = orig
        self.assertEqual(seen, [None], "force=True must reset the cached failure")

    def test_force_never_discards_a_loaded_model(self):
        sentinel = ("model", "state")
        enhancement._df_model = sentinel
        result = enhancement.probe(force=True)
        self.assertTrue(result["available"])
        self.assertEqual(result["mode"], "in-process")
        self.assertIs(enhancement._df_model, sentinel)


class TestSidecar(unittest.TestCase):
    """The uv sidecar path, with subprocess + marker file mocked/redirected."""

    def setUp(self):
        _reset_state()
        self._tmp = tempfile.TemporaryDirectory()
        self._marker = Path(self._tmp.name) / "dfn3-sidecar-ok"
        self._marker_patch = mock.patch.object(enhancement, "_marker_path", lambda: self._marker)
        self._marker_patch.start()

    def tearDown(self):
        self._marker_patch.stop()
        self._tmp.cleanup()
        _reset_state()

    def test_passive_probe_never_spawns_a_subprocess(self):
        with (
            mock.patch.object(
                subprocess, "run", side_effect=AssertionError("passive probe spawned uv")
            ),
            mock.patch.object(
                subprocess, "Popen", side_effect=AssertionError("passive probe spawned uv")
            ),
        ):
            result = enhancement.probe(force=False)
        self.assertFalse(result["available"])
        self.assertIn("Check again", result["error"] or "")

    def test_forced_probe_success_writes_marker(self):
        fake = _FakePopen(
            lines=["Resolved 25 packages in 1.2s", "Installed 25 packages in 1.1s"], returncode=0
        )
        with mock.patch.object(subprocess, "Popen", return_value=fake) as popen:
            result = enhancement.probe(force=True)
        self.assertTrue(result["available"])
        self.assertEqual(result["mode"], "sidecar")
        self.assertIsNone(result["error"])
        popen.assert_called_once()
        self.assertEqual(self._marker.read_text(encoding="utf-8"), enhancement._marker_spec())

    def test_marker_lets_passive_probe_trust_a_prior_success(self):
        self._marker.write_text(enhancement._marker_spec(), encoding="utf-8")
        with mock.patch.object(
            subprocess, "Popen", side_effect=AssertionError("marker should be trusted")
        ):
            result = enhancement.probe(force=False)
        self.assertTrue(result["available"])
        self.assertEqual(result["mode"], "sidecar")

    def test_stale_marker_spec_is_ignored(self):
        self._marker.write_text("old-pins", encoding="utf-8")
        with mock.patch.object(
            subprocess, "Popen", side_effect=AssertionError("stale marker must not probe")
        ):
            result = enhancement.probe(force=False)
        self.assertFalse(result["available"])

    def test_forced_probe_failure_reports_output_tail(self):
        fake = _FakePopen(lines=["boom", "ModuleNotFoundError: no wheels"], returncode=1)
        with mock.patch.object(subprocess, "Popen", return_value=fake):
            result = enhancement.probe(force=True)
        self.assertFalse(result["available"])
        self.assertIn("no wheels", result["error"])
        self.assertFalse(self._marker.exists())

    def test_forced_probe_failure_prefers_uv_error_line(self):
        fake = _FakePopen(
            lines=[
                "Downloading torch (184.3MiB)",
                "error: Failed to download `torch`",
                "  Caused by: network unreachable",
            ],
            returncode=2,
        )
        with mock.patch.object(subprocess, "Popen", return_value=fake):
            result = enhancement.probe(force=True)
        self.assertFalse(result["available"])
        self.assertIn("Failed to download `torch`", result["error"])

    def test_enhance_audio_returns_original_when_sidecar_run_fails(self):
        # Pretend setup succeeded earlier (memory + marker)
        enhancement._sidecar_ok = True
        self._marker.write_text(enhancement._marker_spec(), encoding="utf-8")
        src = Path(self._tmp.name) / "a.wav"
        src.write_bytes(b"RIFF")
        fake = SimpleNamespace(returncode=1, stdout="", stderr="crash")
        with mock.patch.object(subprocess, "run", return_value=fake):
            out = enhancement.enhance_audio(src, output_dir=Path(self._tmp.name))
        self.assertEqual(out, src)
        # A failed run proves the helper is broken: ready state + marker must
        # be invalidated so the UI stops claiming "Active".
        self.assertIs(enhancement._sidecar_ok, False)
        self.assertFalse(self._marker.exists())
        self.assertIn("helper run failed", enhancement._sidecar_error or "")

    def test_start_setup_returns_immediately_and_finishes_in_background(self):
        """The one-time setup must never run inline: start_setup() returns at
        once with setting_up=True while a background worker completes it —
        this is what keeps the probe endpoint inside the server's request
        timeout on a multi-minute first-run download."""
        import time

        def _slow_ok(*args, **kwargs):
            return _FakePopen(lines=["Downloading torch (184.3MiB)"], returncode=0, delay=0.3)

        started = time.monotonic()
        with mock.patch.object(subprocess, "Popen", side_effect=_slow_ok):
            snapshot = enhancement.start_setup()
            elapsed = time.monotonic() - started
            # Immediate response, well under the simulated setup duration
            self.assertLess(elapsed, 0.25)
            self.assertFalse(snapshot["available"])
            self.assertTrue(snapshot["setting_up"])
            self.assertIsNone(snapshot["install_hint"])
            # Poll passively (as the UI does) until the background setup lands
            deadline = time.monotonic() + 5
            result = snapshot
            while time.monotonic() < deadline:
                result = enhancement.probe(force=False)
                if result["available"] or (not result["setting_up"]):
                    break
                time.sleep(0.05)
        self.assertTrue(result["available"], f"setup never settled: {result}")
        self.assertEqual(result["mode"], "sidecar")
        self.assertFalse(result["setting_up"])
        self.assertTrue(self._marker.exists())

    def test_start_setup_reports_failure_after_polling(self):
        import time

        def _slow_fail(*args, **kwargs):
            return _FakePopen(lines=["no wheels here"], returncode=1, delay=0.1)

        with mock.patch.object(subprocess, "Popen", side_effect=_slow_fail):
            snapshot = enhancement.start_setup()
            self.assertTrue(snapshot["setting_up"])
            deadline = time.monotonic() + 5
            result = snapshot
            while time.monotonic() < deadline:
                result = enhancement.probe(force=False)
                if not result["setting_up"]:
                    break
                time.sleep(0.05)
        self.assertFalse(result["available"])
        self.assertFalse(result["setting_up"])
        self.assertIn("no wheels here", result["error"] or "")
        self.assertFalse(self._marker.exists())

    def test_start_setup_is_idempotent_while_running(self):
        import time

        def _slow_ok(*args, **kwargs):
            return _FakePopen(lines=["Installed 4 packages in 0.4s"], returncode=0, delay=0.4)

        with mock.patch.object(subprocess, "Popen", side_effect=_slow_ok) as popen:
            first = enhancement.start_setup()
            second = enhancement.start_setup()  # while the first still runs
            self.assertTrue(first["setting_up"])
            self.assertTrue(second["setting_up"])
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if enhancement.probe(force=False)["available"]:
                    break
                time.sleep(0.05)
            self.assertEqual(popen.call_count, 1, "second call must not spawn another setup")

    def test_setup_progress_is_visible_while_running_and_cleared_after(self):
        """A poll during setup must see staged progress; after it settles the
        progress payload must be gone (the UI switches back to badges)."""
        import time

        def _slow(*args, **kwargs):
            return _FakePopen(
                lines=[
                    "Resolved 25 packages in 1.2s",
                    "Downloading torch (184.3MiB)",
                    "Downloading torchaudio (2.1MiB)",
                    "Prepared 25 packages in 3s",
                    "Installed 25 packages in 1.1s",
                ],
                returncode=0,
                delay=0.08,
            )

        with mock.patch.object(subprocess, "Popen", side_effect=_slow):
            enhancement.start_setup()
            saw_downloading = None
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                snap = enhancement.probe(force=False)
                prog = snap.get("setup_progress")
                if prog and prog["stage"] == "downloading" and saw_downloading is None:
                    saw_downloading = prog
                if not snap["setting_up"]:
                    break
                time.sleep(0.02)
        self.assertIsNotNone(saw_downloading, "never observed downloading stage")
        self.assertGreaterEqual(saw_downloading["packages"], 1)
        self.assertGreater(saw_downloading["total_mb"], 0)
        self.assertIn("Downloading", saw_downloading["detail"])
        self.assertIn("elapsed_s", saw_downloading)
        # Settled: progress cleared, result available
        final = enhancement.probe(force=False)
        self.assertTrue(final["available"])
        self.assertIsNone(final["setup_progress"])

    def test_timeout_kills_the_whole_process_tree(self):
        """On deadline, the setup's descendants must die too — a grandchild
        inheriting the stdout pipe would otherwise keep the streaming reader
        blocked forever and the advertised timeout would be violated."""
        import time

        # Parent spawns a long-lived child (inherits the merged stdout pipe),
        # then sleeps. Killing only the parent would leave the child holding
        # the pipe open.
        cmd = [
            sys.executable,
            "-c",
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)']); "
            "time.sleep(300)",
        ]
        start = time.monotonic()
        with mock.patch.object(enhancement, "_PROBE_TIMEOUT_S", 1.0):
            ok, err = enhancement._run_setup(cmd)
        elapsed = time.monotonic() - start
        self.assertFalse(ok)
        self.assertIn("timed out", err or "")
        self.assertLess(elapsed, 15, "reader stayed blocked past the deadline")

    def test_fast_success_is_never_misreported_as_timeout(self):
        """Even with an absurdly short deadline racing completion, a zero
        exit code must win: a killed process never exits 0."""
        cmd = [sys.executable, "-c", "print('Installed 1 package in 0s')"]
        with mock.patch.object(enhancement, "_PROBE_TIMEOUT_S", 30.0):
            ok, err = enhancement._run_setup(cmd)
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_apply_setup_line_stage_machine(self):
        prog = {
            "stage": "resolving",
            "detail": None,
            "packages": 0,
            "total_mb": 0.0,
            "last_line": None,
        }
        enhancement._apply_setup_line("Resolved 25 packages in 1.2s", prog)
        self.assertEqual(prog["stage"], "resolving")
        self.assertIn("Resolved 25", prog["detail"])
        enhancement._apply_setup_line("Downloading torch (184.3MiB)", prog)
        enhancement._apply_setup_line("Downloading cpython-3.11.9 (1.5GiB)", prog)
        enhancement._apply_setup_line("Downloading soundfile (900KiB)", prog)
        self.assertEqual(prog["stage"], "downloading")
        self.assertEqual(prog["packages"], 3)
        # 184.3 + 1536 + ~0.88 MB
        self.assertAlmostEqual(prog["total_mb"], 1721.2, delta=1.0)
        enhancement._apply_setup_line("Prepared 25 packages in 90s", prog)
        self.assertEqual(prog["stage"], "installing")
        enhancement._apply_setup_line("Installed 25 packages in 1.1s", prog)
        self.assertEqual(prog["stage"], "verifying")
        # Unrecognized output is kept for visibility
        enhancement._apply_setup_line("warning: something odd", prog)
        self.assertEqual(prog["last_line"], "warning: something odd")

    def test_apply_setup_line_real_cold_install_sequence(self):
        """The exact line sequence observed on a real cold install (piped,
        non-TTY uv): all "Downloading" lines print up front, " Downloaded"
        lines follow as each fetch completes, and there are NO Resolved or
        Prepared lines — the stage machine must still progress cleanly."""
        prog = {
            "stage": "resolving",
            "detail": None,
            "packages": 0,
            "done": 0,
            "total_mb": 0.0,
            "last_line": None,
        }
        for line in (
            "Downloading torchaudio (1.7MiB)",
            "Downloading torch (170.4MiB)",
            "Downloading soundfile (1.3MiB)",
        ):
            enhancement._apply_setup_line(line, prog)
        self.assertEqual(prog["stage"], "downloading")
        self.assertEqual(prog["packages"], 3)
        self.assertAlmostEqual(prog["total_mb"], 173.4, delta=0.5)
        # Completion lines have a leading space and advance the detail so the
        # UI doesn't sit on the last "Downloading" line during the torch fetch.
        enhancement._apply_setup_line(" Downloaded soundfile", prog)
        enhancement._apply_setup_line(" Downloaded torchaudio", prog)
        self.assertEqual(prog["stage"], "downloading")
        self.assertEqual(prog["done"], 2)
        self.assertEqual(prog["detail"], "Downloaded torchaudio (2/3)")
        self.assertIsNone(prog["last_line"])
        enhancement._apply_setup_line(" Downloaded torch", prog)
        self.assertEqual(prog["detail"], "Downloaded torch (3/3)")
        # Straight to Installed — no Prepared line in this flow.
        enhancement._apply_setup_line("Installed 24 packages in 467ms", prog)
        self.assertEqual(prog["stage"], "verifying")

    def test_enhance_audio_timeout_does_not_invalidate(self):
        enhancement._sidecar_ok = True
        self._marker.write_text(enhancement._marker_spec(), encoding="utf-8")
        src = Path(self._tmp.name) / "b.wav"
        src.write_bytes(b"RIFF")
        with mock.patch.object(
            subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="uv", timeout=1)
        ):
            out = enhancement.enhance_audio(src, output_dir=Path(self._tmp.name))
        self.assertEqual(out, src)
        # A long file timing out doesn't prove the helper is broken.
        self.assertIs(enhancement._sidecar_ok, True)
        self.assertTrue(self._marker.exists())


class TestProbeEndpoints(_SidecarIsolatedTest):
    def test_probe_endpoint_starts_background_setup(self):
        """The probe endpoint must return immediately with setting_up=True —
        never running the multi-minute setup inline (request timeout)."""
        with tempfile.TemporaryDirectory() as tmp:
            app, _db, _cfg = _make_app(tmp)
            client = TestClient(app, headers=AUTH_HEADERS)
            resp = client.post("/api/system/audio-enhance/probe")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertFalse(body["installed"])
            self.assertTrue(body["setting_up"])
            self.assertEqual(body["python"], sys.executable)
            self.assertIsNone(body["mode"])

    def test_settings_get_includes_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _db, _cfg = _make_app(tmp)
            client = TestClient(app, headers=AUTH_HEADERS)
            resp = client.get("/api/system/settings/audio-enhance")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertFalse(body["installed"])
            self.assertIn("error", body)
            self.assertIn("setup_progress", body)
            self.assertIsNone(body["setup_progress"])
            self.assertEqual(body["python"], sys.executable)

    def test_put_enable_reprobes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            client = TestClient(app, headers=AUTH_HEADERS)
            # Prime a cached failure
            enhancement.probe()
            self.assertIs(enhancement._df_model, False)
            resp = client.put("/api/system/settings/audio-enhance", json={"enabled": True})
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body["enabled"])
            self.assertFalse(body["installed"])
            self.assertEqual(db.get_setting("audio_enhance_enabled", "false"), "true")
            # Disabling never probes
            resp = client.put("/api/system/settings/audio-enhance", json={"enabled": False})
            self.assertIsNone(resp.json()["installed"])


if __name__ == "__main__":
    unittest.main()

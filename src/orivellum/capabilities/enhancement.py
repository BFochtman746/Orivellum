"""Audio enhancement — DeepFilterNet3 pre-processing for transcription.

Runs *before* the audio file is sent to Whisper (via Lemonade or faster-whisper)
so that noisy recordings are cleaned first, dramatically improving transcription
accuracy on poor-quality input (phone calls, room recordings, voice memos, etc.).

DeepFilterNet3 (MIT/Apache-2.0) is the highest-quality open-source neural speech
enhancer.  It runs on CPU at ~0.19× RTF — a single modern core handles it
comfortably while Lemonade/Whisper runs in parallel on the NPU/GPU.

Two execution modes, tried in order:

1. **In-process** — ``import df`` inside the server interpreter.  Only possible
   when a DeepFilterNet build exists for the server's Python.  As of 2026 no
   prebuilt ``DeepFilterLib`` wheels exist for Python >= 3.12 (they stop at
   cp311), so on this project (requires-python >= 3.12) this mode only works
   if someone builds the Rust extension from source.

2. **Sidecar** — a pinned Python 3.11 environment managed transparently by
   ``uv run`` (prebuilt wheels exist for 3.11 on Windows/Linux/macOS).  The
   first probe downloads ~300 MB of packages into uv's cache (one-time, a few
   minutes); afterwards it starts in ~1 s.  Enhancement runs
   ``scripts/dfn3_enhance.py`` as a subprocess per file (~3-6 s overhead,
   negligible next to transcription itself).

Without either mode the module is a safe no-op: ``enhance_audio()`` returns
the original path unchanged and ``is_available()`` returns False.

Typical integration::

    from orivellum.capabilities.enhancement import enhance_audio, is_available
    enhanced = enhance_audio(path, output_dir=tmp_dir)
    # Pass enhanced to Whisper; enhanced == path on failure so safe unconditionally.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Lazy singleton (in-process mode) ─────────────────────────────────────────
# Same double-checked-locking pattern as faster-whisper in extraction.py.
# _df_model is:
#   None  — not yet attempted
#   False — attempted and failed (package absent or load error)
#   tuple — (model, df_state) ready to use

_df_lock: threading.Lock = threading.Lock()
_df_model: object = None
_last_error: str | None = None  # why the last in-process probe failed

_NATIVE_SR = 48_000  # DeepFilterNet3 native sample rate (full-band)

# ── Sidecar mode (uv-managed Python 3.11 helper environment) ─────────────────
# Pinned versions verified working together:
#   deepfilternet 0.5.6 — last release with prebuilt cp311 wheels (all OSes)
#   torch/torchaudio 2.6.0 — newest torchaudio that still exports AudioMetaData
#   soundfile — torchaudio 2.x load/save backend
_SIDECAR_PYTHON = "3.11"
_SIDECAR_WITH = (
    "deepfilternet==0.5.6",
    "torch==2.6.0",
    "torchaudio==2.6.0",
    "soundfile",
)
_PROBE_TIMEOUT_S = 1200  # first probe downloads ~300 MB
_ENHANCE_TIMEOUT_S = 900  # generous — DFN3 runs ~0.2× realtime on one core

# None = untested, True/False = last result. The marker file lets a passing
# probe survive server restarts without re-spawning uv on every settings GET.
# _sidecar_lock guards only state reads/writes — it is NEVER held across the
# subprocess call, so a settings GET can't block behind a 20-minute setup.
# _setup_running is True while a forced setup subprocess is in flight.
_sidecar_lock = threading.Lock()
_sidecar_ok: bool | None = None
_sidecar_error: str | None = None
_setup_running: bool = False  # forced setup subprocess in flight
_setup_pending: bool = False  # setup submitted to the executor, not started yet
# Live progress of the in-flight setup, parsed from uv's streamed output.
# None when no setup is running.  Guarded by _sidecar_lock.
_setup_progress: dict | None = None

INSTALL_HINT = (
    'No install needed — click "Check again" and DeepFilterNet3 is set up '
    "automatically (first time downloads ~300 MB, may take a few minutes)."
)


def _marker_path() -> Path:
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_root / "orivellum" / "dfn3-sidecar-ok"


def _marker_spec() -> str:
    return f"{_SIDECAR_PYTHON}|{'|'.join(_SIDECAR_WITH)}"


def _sidecar_cmd(*args: str) -> list[str] | None:
    """Build the uv sidecar command, or None when uv is not on PATH."""
    uv = shutil.which("uv")
    if uv is None:
        return None
    cmd = [uv, "run", "--no-project", "--python", _SIDECAR_PYTHON]
    for spec in _SIDECAR_WITH:
        cmd += ["--with", spec]
    cmd += ["python", *args]
    return cmd


def _sidecar_env() -> dict:
    env = dict(os.environ)
    # torch wheels: the CPU index keeps Linux from pulling multi-GB CUDA
    # builds; harmless on Windows/macOS where PyPI wheels are CPU-only anyway.
    env["UV_EXTRA_INDEX_URL"] = "https://download.pytorch.org/whl/cpu"
    env["UV_INDEX_STRATEGY"] = "unsafe-best-match"
    return env


# On Windows, prevent each helper invocation from flashing a console window
# when the server runs without an inherited console.
_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _invalidate_sidecar(reason: str) -> None:
    """Mark the sidecar unavailable and drop the marker file.

    Called when an actual sidecar run proves the helper environment is broken
    (uv missing, environment corrupted) so the UI stops claiming "Active" —
    the "Check again" button is the recovery path.
    """
    global _sidecar_ok, _sidecar_error
    with _sidecar_lock:
        _sidecar_ok = False
        _sidecar_error = reason
        try:
            _marker_path().unlink()
        except OSError:
            pass


def _runner_script() -> Path | None:
    """Locate scripts/dfn3_enhance.py relative to the repo root."""
    candidate = Path(__file__).resolve().parents[3] / "scripts" / "dfn3_enhance.py"
    return candidate if candidate.exists() else None


def setup_in_progress() -> bool:
    """True while a forced sidecar setup is queued or running."""
    with _sidecar_lock:
        return _setup_running or _setup_pending


# ── Setup progress (streamed from uv's output) ───────────────────────────────
# uv prints its lifecycle to stderr in non-TTY mode, one line per event.
# Observed on a real cold install (uv 0.x, piped/non-TTY):
#   "Downloading torch (170.4MiB)"        → one line per fetch, all up front
#   " Downloaded torch"                   → one line as each fetch completes
#   "Installed 24 packages in 467ms"      → env ready; the import check runs
# "Resolved N packages" / "Prepared N packages" lines only appear in some
# uv versions/flows, so the stage machine must not depend on them.  The
# "Downloaded" completion lines matter: all "Downloading" lines print at the
# start, so without them the detail would sit on the last (often smallest)
# package for the whole multi-minute torch fetch.  Anything unrecognized is
# kept as `last_line` so a stall or error is still visible live.

_DL_RE = re.compile(r"^\s*Downloading\s+(\S+)(?:\s+\(([\d.]+)\s*(KiB|MiB|GiB)\))?")
_DONE_RE = re.compile(r"^\s*Downloaded\s+(\S+)")
_SIZE_TO_MB = {"KiB": 1.0 / 1024, "MiB": 1.0, "GiB": 1024.0}


def _apply_setup_line(line: str, prog: dict) -> None:
    """Fold one line of uv output into the progress dict (mutates *prog*)."""
    m = _DL_RE.match(line)
    if m:
        prog["stage"] = "downloading"
        prog["packages"] = prog.get("packages", 0) + 1
        size_txt = ""
        if m.group(2):
            prog["total_mb"] = round(
                prog.get("total_mb", 0.0) + float(m.group(2)) * _SIZE_TO_MB[m.group(3)], 1
            )
            size_txt = f" ({m.group(2)} {m.group(3)})"
        prog["detail"] = f"Downloading {m.group(1)}{size_txt}"
        return
    m = _DONE_RE.match(line)
    if m:
        prog["stage"] = "downloading"
        done = prog.get("done", 0) + 1
        prog["done"] = done
        total = prog.get("packages", 0)
        counter = f" ({done}/{total})" if total else ""
        prog["detail"] = f"Downloaded {m.group(1)}{counter}"
        return
    stripped = line.strip()
    if stripped.startswith("Resolved"):
        prog["detail"] = stripped
    elif stripped.startswith("Prepared"):
        prog["stage"] = "installing"
        prog["detail"] = stripped
    elif stripped.startswith(("Installed", "Audited")):
        prog["stage"] = "verifying"
        prog["detail"] = "Verifying the helper starts…"
    else:
        prog["last_line"] = stripped[:200]


def _progress_begin() -> None:
    global _setup_progress
    with _sidecar_lock:
        _setup_progress = {
            "stage": "resolving",
            "detail": "Resolving the helper environment…",
            "packages": 0,
            "done": 0,
            "total_mb": 0.0,
            "last_line": None,
            "started_at": time.time(),
        }


def _progress_apply(line: str) -> None:
    with _sidecar_lock:
        if _setup_progress is not None:
            _apply_setup_line(line, _setup_progress)


def get_setup_progress() -> dict | None:
    """Snapshot of the in-flight setup's progress, or None when idle."""
    with _sidecar_lock:
        if _setup_progress is None:
            return None
        snap = dict(_setup_progress)
    snap["elapsed_s"] = int(time.time() - snap.pop("started_at"))
    return snap


def _kill_setup_tree(proc: subprocess.Popen) -> None:
    """Kill the setup process AND all descendants.

    ``uv run`` spawns a Python child that inherits the merged stdout pipe;
    killing only uv would leave that child holding the pipe open, so the
    streaming reader would never see EOF and the deadline would be violated.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=30,
                creationflags=_CREATIONFLAGS,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        import signal

        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _run_setup(cmd: list[str]) -> tuple[bool, str | None]:
    """Run the sidecar setup subprocess, streaming output into live progress.

    Unlike a plain ``subprocess.run``, streaming means a mid-download failure
    surfaces the moment uv exits (with uv's own error line), instead of the
    caller only learning anything at the full timeout.
    """
    tail: deque[str] = deque(maxlen=12)
    timed_out = threading.Event()
    # POSIX: run the setup in its own process group so the whole tree can be
    # killed on timeout. Windows uses taskkill /T instead.
    group_kw: dict = {} if sys.platform == "win32" else {"start_new_session": True}
    try:
        proc = subprocess.Popen(
            cmd,
            env=_sidecar_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATIONFLAGS,
            **group_kw,
        )
    except OSError as exc:
        return False, f"could not launch uv: {exc}"

    def _on_deadline() -> None:
        if proc.poll() is not None:
            return  # finished before the deadline — not a timeout
        timed_out.set()
        _kill_setup_tree(proc)

    timer = threading.Timer(_PROBE_TIMEOUT_S, _on_deadline)
    timer.daemon = True
    timer.start()
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            tail.append(line.strip())
            _progress_apply(line)
        proc.wait()
    finally:
        timer.cancel()
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass
    if proc.returncode == 0:
        # A killed process never exits 0, so success always wins even if the
        # timer raced with a completion right at the deadline.
        return True, None
    if timed_out.is_set():
        return False, f"sidecar setup timed out after {_PROBE_TIMEOUT_S}s"
    # Prefer uv's explicit "error:" line over whatever happened to be last.
    err_line = next((ln for ln in tail if ln.lower().startswith("error")), None)
    return False, "sidecar setup failed: " + (err_line or (tail[-1] if tail else "unknown error"))


def _sidecar_probe(force: bool) -> bool:
    """Check (and on first use, set up) the uv sidecar environment.

    Passive calls (``force=False``) never spawn a subprocess — they trust the
    in-memory result or the on-disk marker, so a routine settings GET can't
    trigger a multi-minute download.  ``force=True`` runs the real check.

    Never holds ``_sidecar_lock`` across the subprocess call; a concurrent
    forced probe returns False immediately (the running setup will publish
    its result when it finishes — poll ``probe()`` to observe it).
    """
    global _sidecar_ok, _sidecar_error, _setup_running, _setup_pending, _setup_progress
    marker = _marker_path()

    with _sidecar_lock:
        if _setup_running:
            return False  # setup already in flight; poll for the result
        if force:
            _setup_pending = False  # this call is the pending setup starting
        if _sidecar_ok is not None and not (force and _sidecar_ok is False):
            return _sidecar_ok

        if not force:
            try:
                if marker.read_text(encoding="utf-8").strip() == _marker_spec():
                    _sidecar_ok = True
                    _sidecar_error = None
                    return True
            except OSError:
                pass
            # Untested and not forced: leave as unknown-unavailable.
            if _sidecar_ok is None:
                _sidecar_error = (
                    'Not checked yet — click "Check again" to set up '
                    "automatically (first time downloads ~300 MB)."
                )
            return False

        if _runner_script() is None:
            _sidecar_ok = False
            _sidecar_error = "scripts/dfn3_enhance.py not found next to the project"
            return False
        cmd = _sidecar_cmd("-c", "import df, libdf")
        if cmd is None:
            _sidecar_ok = False
            _sidecar_error = (
                "The 'uv' tool was not found on PATH — it is required for "
                "automatic setup (the server itself is normally started with uv)."
            )
            return False
        _setup_running = True

    # ── subprocess runs WITHOUT the lock ────────────────────────────────────
    logger.info(
        "Probing DeepFilterNet3 sidecar (Python %s via uv — first run may download ~300 MB)…",
        _SIDECAR_PYTHON,
    )
    _progress_begin()
    ok = False
    error: str | None = None
    try:
        ok, error = _run_setup(cmd)
    finally:
        with _sidecar_lock:
            _setup_running = False
            _sidecar_ok = ok
            _sidecar_error = error
            _setup_progress = None
            try:
                if ok:
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    marker.write_text(_marker_spec(), encoding="utf-8")
                else:
                    marker.unlink()
            except OSError:
                pass
    if ok:
        logger.info("DeepFilterNet3 sidecar ready (uv / Python %s).", _SIDECAR_PYTHON)
    else:
        logger.warning("DeepFilterNet3 sidecar probe failed: %s", error)
    return ok


def _get_df_model():
    """Return (model, df_state) or None when unavailable (in-process mode)."""
    global _df_model, _last_error
    if _df_model is not None:
        return None if _df_model is False else _df_model
    with _df_lock:
        if _df_model is not None:
            return None if _df_model is False else _df_model
        try:
            from df import init_df  # type: ignore[import]

            logger.info("Loading DeepFilterNet3 model (first call — may download ~7 MB)…")
            # init_df() returns 3 values on 0.5.6 and 4 on 0.5.7+ — take the
            # first two (model, df_state) and ignore the rest.
            result = init_df()
            model, df_state = result[0], result[1]
            _df_model = (model, df_state)
            _last_error = None
            logger.info("DeepFilterNet3 ready (in-process).")
        except ImportError as exc:
            _last_error = f"ImportError: {exc}"
            logger.debug("deepfilternet not importable in-process: %s", exc)
            _df_model = False
        except Exception as exc:
            _last_error = f"{exc.__class__.__name__}: {exc}"
            logger.warning("DeepFilterNet3 failed to load in-process: %s", exc)
            _df_model = False
    return None if _df_model is False else _df_model


def is_available() -> bool:
    """Return True when DeepFilterNet3 can run (in-process or via sidecar)."""
    return _get_df_model() is not None or _sidecar_probe(force=False)


def probe(force: bool = False) -> dict:
    """Report DeepFilterNet3 availability with diagnostics.

    With ``force=True`` a previously-cached FAILED result is discarded and the
    check is attempted fresh (a working setup is never thrown away).  For the
    sidecar this is also what triggers the one-time automatic setup, so the
    "Check again" button doubles as the installer — no manual command needed.

    Returns ``{available, mode, error, python, install_hint}`` — ``python`` is
    the interpreter this server runs from, so an environment mismatch is
    immediately visible; ``mode`` is ``"in-process"`` or ``"sidecar"`` when
    available.
    """
    global _df_model
    if force:
        with _df_lock:
            if _df_model is False:
                _df_model = None
        # Pick up packages installed after interpreter start
        import importlib

        importlib.invalidate_caches()

    if _get_df_model() is not None:
        mode: str | None = "in-process"
        if force:
            # Sidecar setup is moot — clear any queued-setup flag.
            global _setup_pending
            with _sidecar_lock:
                _setup_pending = False
    elif _sidecar_probe(force=force):
        mode = "sidecar"
    else:
        mode = None

    available = mode is not None
    setting_up = setup_in_progress()
    error = None
    if not available and not setting_up:
        # Sidecar diagnosis is the actionable one; the in-process import error
        # is expected on Python >= 3.12 (no wheels exist), so it goes second.
        parts = [p for p in (_sidecar_error, _last_error) if p]
        error = " | ".join(parts) or None
    return {
        "available": available,
        "mode": mode,
        "setting_up": setting_up,
        "setup_progress": get_setup_progress() if setting_up else None,
        "error": error,
        "python": sys.executable,
        "install_hint": None if available or setting_up else INSTALL_HINT,
    }


def start_setup() -> dict:
    """Kick off the one-time sidecar setup in the background (non-blocking).

    The first setup downloads ~300 MB and can run far longer than the server's
    HTTP request timeout, so the probe endpoint must never run it inline.
    Returns the current ``probe()`` snapshot; when a setup was started (or is
    already running) it reports ``setting_up=True`` and callers poll ``probe()``
    until it settles.
    """
    global _setup_pending
    snapshot = probe(force=False)
    if snapshot["available"] or snapshot["setting_up"]:
        return snapshot

    from orivellum.api import executor

    # Mark pending BEFORE submitting so a poll arriving between submit and
    # worker start still sees setting_up=True.
    with _sidecar_lock:
        _setup_pending = True
    executor.submit_bg(probe, True, kind="dfn3-setup", label="DeepFilterNet3 sidecar setup")
    return probe(force=False)


def _enhance_via_sidecar(path: Path, out_path: Path) -> Path:
    """Run the sidecar runner script on *path*; return out_path or *path*."""
    script = _runner_script()
    cmd = _sidecar_cmd(str(script), str(path), str(out_path)) if script else None
    if cmd is None:
        return path
    try:
        proc = subprocess.run(
            cmd,
            env=_sidecar_env(),
            capture_output=True,
            text=True,
            timeout=_ENHANCE_TIMEOUT_S,
            creationflags=_CREATIONFLAGS,
        )
    except subprocess.TimeoutExpired:
        # A timeout doesn't prove the helper is broken (could be a very long
        # recording) — keep availability, skip enhancement for this file.
        logger.warning(
            "DeepFilterNet3 sidecar timed out for %s after %ss — using original audio",
            path.name,
            _ENHANCE_TIMEOUT_S,
        )
        return path
    except OSError as exc:
        _invalidate_sidecar(f"helper could not be launched: {exc}")
        logger.warning(
            "DeepFilterNet3 sidecar failed for %s: %s — using original audio "
            '(marked unavailable; use "Check again" to re-set-up)',
            path.name,
            exc,
        )
        return path
    if proc.returncode != 0 or not out_path.exists():
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        reason = tail[-1] if tail else f"exit {proc.returncode}"
        _invalidate_sidecar(f"helper run failed: {reason}")
        logger.warning(
            "DeepFilterNet3 sidecar failed for %s: %s — using original audio "
            '(marked unavailable; use "Check again" to re-set-up)',
            path.name,
            reason,
        )
        return path
    logger.info("DeepFilterNet3 enhanced %s → %s (sidecar)", path.name, out_path.name)
    return out_path


def enhance_audio(path: Path, output_dir: Path | None = None) -> Path:
    """Enhance *path* with DeepFilterNet3; return the enhanced WAV path.

    On any failure (package missing, model error, IO error) the original *path*
    is returned unchanged so callers never fail silently — audio extraction
    degrades gracefully to unenhanced Whisper.

    The enhanced WAV is written next to *path* (or into *output_dir* when given)
    with the suffix ``_dfn3.wav``.  The caller is responsible for deleting it
    after use — pass a ``tempfile.TemporaryDirectory`` path as *output_dir*.

    Processing chain:
        Input (any SR/channels) → mono → resample to 48 kHz → DeepFilterNet3
        → 48 kHz WAV

    Note: Whisper / faster-whisper happily resamples from 48 kHz to 16 kHz
    internally, so no additional resampling step is needed after enhancement.
    """
    out_dir = output_dir if output_dir is not None else path.parent
    out_path = Path(out_dir) / f"{path.stem}_dfn3.wav"

    pair = _get_df_model()
    if pair is None:
        if _sidecar_probe(force=False):
            return _enhance_via_sidecar(path, out_path)
        return path  # unavailable in both modes — skip silently

    try:
        import torch  # type: ignore[import]
        import torchaudio  # type: ignore[import]
        from df import enhance  # type: ignore[import]

        model, df_state = pair

        audio, sr = torchaudio.load(str(path))

        # Convert to mono
        if audio.shape[0] > 1:
            audio = torch.mean(audio, dim=0, keepdim=True)

        # Resample to DeepFilterNet3's native rate if needed
        if sr != _NATIVE_SR:
            resampler = torchaudio.transforms.Resample(sr, _NATIVE_SR)
            audio = resampler(audio)

        enhanced = enhance(model, df_state, audio)

        torchaudio.save(str(out_path), enhanced, _NATIVE_SR)

        logger.info(
            "DeepFilterNet3 enhanced %s → %s (%.1f s audio)",
            path.name,
            out_path.name,
            enhanced.shape[-1] / _NATIVE_SR,
        )
        return out_path

    except Exception as exc:
        logger.warning(
            "DeepFilterNet3 enhancement failed for %s: %s — using original audio",
            path.name,
            exc,
        )
        return path

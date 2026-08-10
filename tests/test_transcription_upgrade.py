"""Task: upgrade transcription accuracy (large-v3-turbo default + timestamps).

Covers:
1. Config default is large-v3-turbo; DB setting overrides config.
2. Low-memory guard substitutes "base" for heavy models.
3. Load failure of a heavy model retries with "base"; size-change reloads.
4. faster-whisper path stores segment + word timestamps in meta.
5. AI-server path requests verbose_json, parses timestamps, retries plain.
6. GET/PATCH /system/settings/asr endpoints validate sizes.
"""

from __future__ import annotations

import io
import json as _json
import os
import sys
import wave

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def _reset_fw_singleton():
    """Each test starts with a clean faster-whisper singleton."""
    import orivellum.capabilities.extraction as _ext

    orig = (
        _ext._fw_instance,
        _ext._fw_loaded_size,
        _ext._fw_requested_size,
        _ext._fw_fallback_reason,
    )
    _ext._fw_instance = None
    _ext._fw_loaded_size = ""
    _ext._fw_requested_size = ""
    _ext._fw_fallback_reason = None
    yield
    (_ext._fw_instance, _ext._fw_loaded_size, _ext._fw_requested_size, _ext._fw_fallback_reason) = (
        orig
    )


def _make_wav(tmp_path, name="test.wav"):
    p = tmp_path / name
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00" * 8000)
    p.write_bytes(buf.getvalue())
    return p


# ── 1. Defaults + DB override precedence ──────────────────────────────────────


class TestModelSizeResolution:
    def test_config_default_is_large_v3_turbo(self):
        from orivellum.configuration.config import ServingConfig

        assert ServingConfig().asr_local_model == "large-v3-turbo"

    def test_db_setting_overrides_config(self, tmp_path):
        from orivellum.capabilities.extraction import _resolve_asr_local_model
        from orivellum.database.db import OrivellumDB

        db = OrivellumDB(str(tmp_path / "t.db"))
        assert _resolve_asr_local_model(db, "large-v3-turbo") == "large-v3-turbo"
        db.set_setting("asr_local_model", "small", actor="test")
        assert _resolve_asr_local_model(db, "large-v3-turbo") == "small"

    def test_invalid_db_setting_ignored(self, tmp_path):
        from orivellum.capabilities.extraction import _resolve_asr_local_model
        from orivellum.database.db import OrivellumDB

        db = OrivellumDB(str(tmp_path / "t.db"))
        db.set_setting("asr_local_model", "gigantic-v9", actor="test")
        assert _resolve_asr_local_model(db, "base") == "base"

    def test_no_db_falls_back_to_config(self):
        from orivellum.capabilities.extraction import _resolve_asr_local_model

        assert _resolve_asr_local_model(None, "medium") == "medium"


# ── 2. Low-memory guard ────────────────────────────────────────────────────────


class TestLowMemoryFallback:
    def test_heavy_model_falls_back_when_ram_low(self, monkeypatch):
        import psutil

        import orivellum.capabilities.extraction as _ext

        class _VM:  # 2 GB available — below the 6 GB threshold
            available = 2 * 1024**3

        monkeypatch.setattr(psutil, "virtual_memory", lambda: _VM())
        size, reason = _ext._fw_effective_size("large-v3-turbo")
        assert size == "base"
        assert reason and "large-v3-turbo" in reason

    def test_heavy_model_kept_when_ram_plenty(self, monkeypatch):
        import psutil

        import orivellum.capabilities.extraction as _ext

        class _VM:
            available = 64 * 1024**3

        monkeypatch.setattr(psutil, "virtual_memory", lambda: _VM())
        assert _ext._fw_effective_size("large-v3-turbo") == ("large-v3-turbo", None)

    def test_light_model_never_checked(self, monkeypatch):
        import orivellum.capabilities.extraction as _ext

        def _boom():  # psutil must not even be consulted for light models
            raise AssertionError("virtual_memory should not be called")

        import psutil

        monkeypatch.setattr(psutil, "virtual_memory", _boom)
        assert _ext._fw_effective_size("base") == ("base", None)


# ── 3. Load-failure fallback + reload on size change ─────────────────────────


class _FakeModel:
    def __init__(self, size):
        self.size = size


class TestLoaderFallback:
    def test_heavy_load_failure_retries_base(self, monkeypatch):
        import faster_whisper

        import orivellum.capabilities.extraction as _ext

        calls = []

        def _wm(size, device="auto", compute_type="int8"):
            calls.append(size)
            if size != "base":
                raise RuntimeError("out of memory")
            return _FakeModel(size)

        monkeypatch.setattr(faster_whisper, "WhisperModel", _wm)
        # Plenty of RAM so the memory guard doesn't preempt the load attempt.
        import psutil

        class _VM:
            available = 64 * 1024**3

        monkeypatch.setattr(psutil, "virtual_memory", lambda: _VM())

        model, loaded, reason = _ext._get_faster_whisper_snapshot("large-v3-turbo")
        assert isinstance(model, _FakeModel) and model.size == "base"
        assert loaded == "base" and reason
        assert calls == ["large-v3-turbo", "base"]
        st = _ext.faster_whisper_status()
        assert st["loaded"] is True and st["loaded_size"] == "base"

        # Same heavy request again — must REUSE the fallback, not re-attempt
        # the heavy download on every transcription (anti-thrashing).
        model2, loaded2, reason2 = _ext._get_faster_whisper_snapshot("large-v3-turbo")
        assert model2 is model and loaded2 == "base" and reason2
        assert calls == ["large-v3-turbo", "base"]  # no new load attempts

        # Requesting "base" directly is also satisfied by the loaded model,
        # and reports NO fallback (it got exactly what it asked for).
        model3, loaded3, reason3 = _ext._get_faster_whisper_snapshot("base")
        assert model3 is model and loaded3 == "base" and reason3 is None
        assert calls == ["large-v3-turbo", "base"]

    def test_reload_when_size_changes(self, monkeypatch):
        import faster_whisper

        import orivellum.capabilities.extraction as _ext

        calls = []

        def _wm(size, device="auto", compute_type="int8"):
            calls.append(size)
            return _FakeModel(size)

        monkeypatch.setattr(faster_whisper, "WhisperModel", _wm)
        m1 = _ext._get_faster_whisper("base")
        m2 = _ext._get_faster_whisper("base")  # cached — no reload
        assert m1 is m2 and calls == ["base"]
        m3 = _ext._get_faster_whisper("small")  # size change — reload
        assert m3.size == "small"
        assert calls == ["base", "small"]

    def test_snapshot_metadata_travels_with_model(self, monkeypatch, tmp_path):
        """Transcription meta must reflect the model USED, not later global state."""
        import orivellum.capabilities.extraction as _ext

        segs = [_FakeSeg(0.0, 1.0, " Hi.", [_FakeWord(0.0, 1.0, " Hi.")])]

        class _M:
            def transcribe(self, path, word_timestamps=False):
                # Simulate a concurrent settings change swapping the singleton
                # mid-transcription — meta must still report OUR model.
                _ext._fw_loaded_size = "small"
                _ext._fw_requested_size = "small"
                _ext._fw_fallback_reason = "someone else's reason"
                return iter(segs), _FakeInfo()

        monkeypatch.setattr(_ext, "_get_faster_whisper_snapshot", lambda size: (_M(), "base", None))
        res = _ext._transcribe_faster_whisper(_make_wav(tmp_path), "base")
        assert res.meta["model_size"] == "base"
        assert "model_fallback_reason" not in res.meta

    def test_total_failure_returns_none_and_does_not_thrash(self, monkeypatch):
        import faster_whisper

        import orivellum.capabilities.extraction as _ext

        calls = []

        def _wm(size, device="auto", compute_type="int8"):
            calls.append(size)
            raise RuntimeError("no models for you")

        monkeypatch.setattr(faster_whisper, "WhisperModel", _wm)
        assert _ext._get_faster_whisper("base") is None
        n = len(calls)
        assert _ext._get_faster_whisper("base") is None  # cached failure
        assert len(calls) == n  # no repeated load attempts for same request
        # A DIFFERENT size is a new request and may retry.
        assert _ext._get_faster_whisper("tiny") is None
        assert len(calls) > n


# ── 4. Timestamps stored in meta (local path) ─────────────────────────────────


class _FakeWord:
    def __init__(self, start, end, word):
        self.start, self.end, self.word = start, end, word


class _FakeSeg:
    def __init__(self, start, end, text, words):
        self.start, self.end, self.text, self.words = start, end, text, words


class _FakeInfo:
    language = "en"
    duration = 3.5


class TestLocalTimestamps:
    def test_segments_and_words_in_meta(self, tmp_path, monkeypatch):
        import orivellum.capabilities.extraction as _ext

        segs = [
            _FakeSeg(
                0.0,
                1.6,
                " Hello there.",
                [_FakeWord(0.0, 0.8, " Hello"), _FakeWord(0.9, 1.6, " there.")],
            ),
            _FakeSeg(
                1.7,
                3.5,
                " General Kenobi.",
                [_FakeWord(1.7, 2.6, " General"), _FakeWord(2.7, 3.5, " Kenobi.")],
            ),
        ]

        class _M:
            def transcribe(self, path, word_timestamps=False):
                assert word_timestamps is True
                return iter(segs), _FakeInfo()

        monkeypatch.setattr(
            _ext, "_get_faster_whisper_snapshot", lambda size: (_M(), "large-v3-turbo", None)
        )
        wav = _make_wav(tmp_path)
        res = _ext._transcribe_faster_whisper(wav, "large-v3-turbo")
        assert res is not None
        assert "Hello there." in res.full_text
        meta = res.meta
        assert meta["transcription"] == "faster_whisper"
        assert meta["language"] == "en"
        assert meta["duration"] == 3.5
        assert len(meta["segments"]) == 2
        assert meta["segments"][0] == {"start": 0.0, "end": 1.6, "text": "Hello there."}
        assert len(meta["words"]) == 4
        assert meta["words"][0]["word"] == "Hello"
        assert meta["words"][3]["end"] == 3.5

    def test_word_cap_sets_truncated_flag(self, tmp_path, monkeypatch):
        import orivellum.capabilities.extraction as _ext

        monkeypatch.setattr(_ext, "_FW_MAX_WORDS_META", 3)

        segs = [_FakeSeg(0, 5, " a b c d e", [_FakeWord(i, i + 1, f" w{i}") for i in range(5)])]

        class _M:
            def transcribe(self, path, word_timestamps=False):
                return iter(segs), _FakeInfo()

        monkeypatch.setattr(_ext, "_get_faster_whisper_snapshot", lambda size: (_M(), "base", None))
        res = _ext._transcribe_faster_whisper(_make_wav(tmp_path), "base")
        assert res is not None
        assert len(res.meta["words"]) == 3
        assert res.meta["words_truncated"] is True


# ── 5. AI-server path: verbose_json + fallback ────────────────────────────────


class TestAiServerTimestamps:
    def _run_extract(self, tmp_path, responses):
        """responses: list of callables(req) -> dict or raising."""
        import urllib.request

        import orivellum.capabilities.extraction as _ext
        from orivellum.api import _deps
        from orivellum.configuration.config import OrivellumConfig, ServingConfig

        wav = _make_wav(tmp_path, "note.wav")
        calls = []

        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return _json.dumps(self._payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

        it = iter(responses)

        def _urlopen(req, timeout=None):
            calls.append(req.data)
            fn = next(it)
            return _Resp(fn(req))

        orig_open = urllib.request.urlopen
        orig_cfg = _deps.get_config
        cfg = OrivellumConfig(
            data_dir=str(tmp_path),
            serving=ServingConfig(base_url="http://localhost:11434"),
        )
        _deps.get_config = lambda: cfg
        urllib.request.urlopen = _urlopen
        try:
            return _ext.extract(wav, "audio"), calls
        finally:
            urllib.request.urlopen = orig_open
            _deps.get_config = orig_cfg

    def test_verbose_json_timestamps_stored(self, tmp_path):
        payload = {
            "text": "Buy milk tomorrow.",
            "language": "en",
            "duration": 2.25,
            "segments": [{"start": 0.0, "end": 2.25, "text": " Buy milk tomorrow."}],
            "words": [
                {"start": 0.0, "end": 0.6, "word": "Buy"},
                {"start": 0.7, "end": 1.2, "word": "milk"},
                {"start": 1.3, "end": 2.25, "word": "tomorrow."},
            ],
        }
        res, calls = self._run_extract(tmp_path, [lambda req: payload])
        assert res.meta["transcription"] == "ai_server"
        assert b"verbose_json" in calls[0]  # first attempt asked for timestamps
        # BOTH granularities must be requested or servers omit word timing.
        assert calls[0].count(b'name="timestamp_granularities[]"') == 2
        assert b"\r\n\r\nword\r\n" in calls[0]
        assert b"\r\n\r\nsegment\r\n" in calls[0]
        assert res.meta["segments"] == [{"start": 0.0, "end": 2.25, "text": "Buy milk tomorrow."}]
        assert len(res.meta["words"]) == 3
        assert res.meta["language"] == "en"
        assert res.meta["duration"] == 2.25

    def test_falls_back_to_plain_json_when_verbose_rejected(self, tmp_path):
        def _reject(req):
            raise RuntimeError("400 unknown field response_format")

        res, calls = self._run_extract(tmp_path, [_reject, lambda req: {"text": "Plain works."}])
        assert res.meta["transcription"] == "ai_server"
        assert "Plain works." in res.full_text
        assert len(calls) == 2
        assert b"verbose_json" not in calls[1]  # retry was plain


# ── 6. Settings endpoints ─────────────────────────────────────────────────────


class TestAsrSettingsEndpoints:
    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient

        from orivellum.api import _deps
        from orivellum.api.app import app
        from orivellum.configuration.config import OrivellumConfig
        from orivellum.database.db import OrivellumDB
        from tests.conftest import AUTH_HEADERS

        db = OrivellumDB(str(tmp_path / "t.db"))
        cfg = OrivellumConfig(data_dir=str(tmp_path))
        _deps.init(db=db, cfg=cfg)
        return TestClient(app, headers=AUTH_HEADERS)

    def test_get_reports_default_and_allowed(self, client):
        r = client.get("/api/system/settings/asr")
        assert r.status_code == 200
        data = r.json()
        assert data["config_default"] == "large-v3-turbo"
        assert data["stored"] == ""
        assert data["effective"] == "large-v3-turbo"
        assert "large-v3-turbo" in data["allowed"]
        assert "runtime" in data

    def test_patch_sets_and_clears_override(self, client):
        r = client.patch("/api/system/settings/asr", json={"model_size": "small"})
        assert r.status_code == 200
        data = client.get("/api/system/settings/asr").json()
        assert data["stored"] == "small" and data["effective"] == "small"
        r = client.patch("/api/system/settings/asr", json={"model_size": ""})
        assert r.status_code == 200
        data = client.get("/api/system/settings/asr").json()
        assert data["stored"] == "" and data["effective"] == "large-v3-turbo"

    def test_patch_rejects_unknown_size(self, client):
        r = client.patch("/api/system/settings/asr", json={"model_size": "colossal"})
        assert r.status_code == 422

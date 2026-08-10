"""Tests for the music/SFX generation routes (/api/studio/music/*).

The generation sidecar is never reachable in the test environment — all
httpx traffic is mocked.  Coverage:

  - status endpoint: unconfigured / configured-but-unreachable / reachable
  - license acknowledgement flow (unknown model, persist, revoke)
  - generate gates in order: 503 unconfigured → 404 unknown model →
    422 bad input → 403 license not acknowledged
  - job success path: audio written to outputs dir, registered, job "done"
  - job failure paths: sidecar unreachable, sidecar 503
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from orivellum.api.routes import music as music_routes
from tests.conftest import AUTH_HEADERS


def _make_app(tmp: str, music_gen_url: str = ""):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    cfg.serving.music_gen_url = music_gen_url
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


from contextlib import contextmanager


@contextmanager
def _client(app, db, cfg):
    """TestClient whose lifespan-driven _deps re-init is overridden with the
    test's own db/cfg (the app lifespan wires the real ones on startup)."""
    from orivellum.api import _deps

    with TestClient(app) as client:
        _deps.init(db=db, cfg=cfg)
        yield client


def _reset_jobs():
    with music_routes._jobs_lock:
        music_routes._jobs.clear()


class _FakeResponse:
    def __init__(self, status_code=200, content=b"", json_data=None, headers=None, text=""):
        self.status_code = status_code
        self.content = content
        self._json = json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class MusicStatusTest(unittest.TestCase):
    def setUp(self):
        _reset_jobs()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_unconfigured_status(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="")
        with _client(app, db, cfg) as client:
            r = client.get("/api/studio/music/status", headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["configured"])
        self.assertFalse(data["reachable"])
        ids = {m["id"] for m in data["models"]}
        self.assertEqual(ids, {"stable_audio_open", "musicgen"})
        for m in data["models"]:
            self.assertFalse(m["license_acked"])

    def test_reachable_status_merges_sidecar_health(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        health = {
            "ok": True,
            "device": "cuda",
            "models": {
                "stable_audio_open": {"installed": True, "loaded": True},
                "musicgen": {"installed": False, "load_error": "ImportError: transformers"},
            },
        }
        with mock.patch("httpx.get", return_value=_FakeResponse(json_data=health)):
            with _client(app, db, cfg) as client:
                r = client.get("/api/studio/music/status", headers=AUTH_HEADERS)
        data = r.json()
        self.assertTrue(data["configured"])
        self.assertTrue(data["reachable"])
        self.assertEqual(data["device"], "cuda")
        by_id = {m["id"]: m for m in data["models"]}
        self.assertTrue(by_id["stable_audio_open"]["loaded"])
        self.assertFalse(by_id["musicgen"]["installed"])
        self.assertIn("ImportError", by_id["musicgen"]["load_error"])

    def test_configured_but_unreachable(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        with mock.patch("httpx.get", side_effect=OSError("connection refused")):
            with _client(app, db, cfg) as client:
                r = client.get("/api/studio/music/status", headers=AUTH_HEADERS)
        data = r.json()
        self.assertTrue(data["configured"])
        self.assertFalse(data["reachable"])


class LicenseAckTest(unittest.TestCase):
    def setUp(self):
        _reset_jobs()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_model_404(self):
        app, db, cfg = _make_app(self.tmp.name)
        with _client(app, db, cfg) as client:
            r = client.post(
                "/api/studio/music/licenses/colossal_audio/ack",
                json={"accepted": True},
                headers=AUTH_HEADERS,
            )
        self.assertEqual(r.status_code, 404)

    def test_ack_persists_and_revokes(self):
        app, db, cfg = _make_app(self.tmp.name)
        with _client(app, db, cfg) as client:
            r = client.post(
                "/api/studio/music/licenses/musicgen/ack",
                json={"accepted": True},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["license_acked"])
            self.assertEqual(db.get_setting("music_license_ack_musicgen"), "true")

            r = client.post(
                "/api/studio/music/licenses/musicgen/ack",
                json={"accepted": False},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(db.get_setting("music_license_ack_musicgen"), "false")


class GenerateGatesTest(unittest.TestCase):
    def setUp(self):
        _reset_jobs()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _body(self, **over):
        base = {
            "prompt": "tense orchestral rise",
            "model": "stable_audio_open",
            "kind": "music",
            "duration_s": 20,
        }
        base.update(over)
        return base

    def test_unconfigured_503(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="")
        with _client(app, db, cfg) as client:
            r = client.post("/api/studio/music/generate", json=self._body(), headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 503)

    def test_unknown_model_404(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        with _client(app, db, cfg) as client:
            r = client.post(
                "/api/studio/music/generate",
                json=self._body(model="riffusion"),
                headers=AUTH_HEADERS,
            )
        self.assertEqual(r.status_code, 404)

    def test_empty_prompt_422(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        with _client(app, db, cfg) as client:
            r = client.post(
                "/api/studio/music/generate", json=self._body(prompt="   "), headers=AUTH_HEADERS
            )
        self.assertEqual(r.status_code, 422)

    def test_bad_kind_422(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        with _client(app, db, cfg) as client:
            r = client.post(
                "/api/studio/music/generate", json=self._body(kind="jingle"), headers=AUTH_HEADERS
            )
        self.assertEqual(r.status_code, 422)

    def test_musicgen_rejects_sfx_422(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        db.set_setting("music_license_ack_musicgen", "true")
        with _client(app, db, cfg) as client:
            r = client.post(
                "/api/studio/music/generate",
                json=self._body(model="musicgen", kind="sfx", duration_s=5),
                headers=AUTH_HEADERS,
            )
        self.assertEqual(r.status_code, 422)

    def test_duration_over_model_cap_422(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        db.set_setting("music_license_ack_stable_audio_open", "true")
        with _client(app, db, cfg) as client:
            r = client.post(
                "/api/studio/music/generate", json=self._body(duration_s=300), headers=AUTH_HEADERS
            )
        self.assertEqual(r.status_code, 422)

    def test_sfx_duration_capped_tighter(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        db.set_setting("music_license_ack_stable_audio_open", "true")
        with _client(app, db, cfg) as client:
            r = client.post(
                "/api/studio/music/generate",
                json=self._body(kind="sfx", duration_s=30),
                headers=AUTH_HEADERS,
            )
        self.assertEqual(r.status_code, 422)

    def test_license_gate_403(self):
        """The core gate: a valid request without acknowledgement never generates."""
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        with _client(app, db, cfg) as client:
            r = client.post("/api/studio/music/generate", json=self._body(), headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 403)
        self.assertIn("license", r.json()["detail"].lower())

    def test_gate_order_license_checked_after_validation(self):
        # Bad duration reports 422 even when license is also missing —
        # the user should fix input first, then see the license dialog.
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        with _client(app, db, cfg) as client:
            r = client.post(
                "/api/studio/music/generate", json=self._body(duration_s=0), headers=AUTH_HEADERS
            )
        self.assertEqual(r.status_code, 422)


class GenerateJobTest(unittest.TestCase):
    """Run the worker synchronously (submit_bg mocked to call inline)."""

    def setUp(self):
        _reset_jobs()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _post_with_worker(self, client, body, post_mock):
        """POST /generate with submit_bg patched to run the worker inline."""

        def inline_submit(fn, *args, **kwargs):
            fn(*args)

        with (
            mock.patch("orivellum.api.executor.submit_bg", side_effect=inline_submit),
            mock.patch("httpx.post", post_mock),
        ):
            return client.post("/api/studio/music/generate", json=body, headers=AUTH_HEADERS)

    def test_success_writes_output_and_registers(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        db.set_setting("music_license_ack_stable_audio_open", "true")
        wav = b"RIFF" + b"\x00" * 64
        post_mock = mock.Mock(
            return_value=_FakeResponse(content=wav, headers={"X-Music-Engine": "stable_audio_open"})
        )

        registered = {}

        def fake_register(**kw):
            registered.update(kw)

        with (
            _client(app, db, cfg) as client,
            mock.patch(
                "orivellum.capabilities.persist.register_and_index", side_effect=fake_register
            ),
            mock.patch(
                "orivellum.api.routes.studio._link_output_sync", return_value="outputs/x.wav"
            ),
        ):
            r = self._post_with_worker(
                client,
                {
                    "prompt": "low braam",
                    "model": "stable_audio_open",
                    "kind": "sfx",
                    "duration_s": 4,
                    "work_id": "w1",
                },
                post_mock,
            )
        self.assertEqual(r.status_code, 200)
        job_id = r.json()["job_id"]

        with _client(app, db, cfg) as client:
            jr = client.get(f"/api/studio/music/jobs/{job_id}", headers=AUTH_HEADERS)
        job = jr.json()
        self.assertEqual(job["state"], "done")
        self.assertTrue(job["registered"])
        self.assertIsNone(job["warning"])
        self.assertTrue(job["output_path"].startswith("sfx_"))
        # File actually landed in the outputs dir with the sidecar's bytes
        out_file = Path(cfg.data_dir) / "outputs" / job["output_path"]
        self.assertEqual(out_file.read_bytes(), wav)
        # Registered as a searchable audio document, linked to the Work
        self.assertEqual(registered["kind"], "audio")
        self.assertEqual(registered["text_content"], "low braam")
        self.assertEqual(registered["work_id"], "w1")
        # Sidecar was called with the request parameters
        sent = post_mock.call_args.kwargs["json"]
        self.assertEqual(sent["model"], "stable_audio_open")
        self.assertEqual(sent["kind"], "sfx")
        self.assertEqual(sent["duration_s"], 4)

    def test_music_prefix_for_music_kind(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        db.set_setting("music_license_ack_musicgen", "true")
        post_mock = mock.Mock(return_value=_FakeResponse(content=b"RIFFxxxx"))
        with _client(app, db, cfg) as client:
            with (
                mock.patch("orivellum.capabilities.persist.register_and_index"),
                mock.patch("orivellum.api.routes.studio._link_output_sync", return_value=""),
            ):
                r = self._post_with_worker(
                    client,
                    {
                        "prompt": "warm piano bed",
                        "model": "musicgen",
                        "kind": "music",
                        "duration_s": 30,
                    },
                    post_mock,
                )
            job = client.get(
                f"/api/studio/music/jobs/{r.json()['job_id']}", headers=AUTH_HEADERS
            ).json()
        self.assertEqual(job["state"], "done")
        self.assertTrue(job["output_path"].startswith("music_"))

    def test_registration_failure_is_partial_success(self):
        """Audio saved but library registration failed → done + warning,
        never silently swallowed."""
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        db.set_setting("music_license_ack_stable_audio_open", "true")
        post_mock = mock.Mock(return_value=_FakeResponse(content=b"RIFFxxxx"))
        with _client(app, db, cfg) as client:
            with (
                mock.patch(
                    "orivellum.capabilities.persist.register_and_index",
                    side_effect=RuntimeError("db locked"),
                ),
                mock.patch("orivellum.api.routes.studio._link_output_sync", return_value=""),
            ):
                r = self._post_with_worker(
                    client,
                    {
                        "prompt": "eerie drone",
                        "model": "stable_audio_open",
                        "kind": "music",
                        "duration_s": 10,
                    },
                    post_mock,
                )
            job = client.get(
                f"/api/studio/music/jobs/{r.json()['job_id']}", headers=AUTH_HEADERS
            ).json()
        self.assertEqual(job["state"], "done")
        self.assertFalse(job["registered"])
        self.assertIn("registration failed", job["warning"])
        # The audio file itself still exists
        out_file = Path(cfg.data_dir) / "outputs" / job["output_path"]
        self.assertTrue(out_file.exists())

    def test_sidecar_unreachable_job_error(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        db.set_setting("music_license_ack_stable_audio_open", "true")
        post_mock = mock.Mock(side_effect=OSError("connection refused"))
        with _client(app, db, cfg) as client:
            r = self._post_with_worker(
                client,
                {"prompt": "x", "model": "stable_audio_open", "kind": "music", "duration_s": 10},
                post_mock,
            )
            job = client.get(
                f"/api/studio/music/jobs/{r.json()['job_id']}", headers=AUTH_HEADERS
            ).json()
        self.assertEqual(job["state"], "error")
        self.assertIn("not reachable", job["error"])

    def test_sidecar_503_surfaces_detail(self):
        app, db, cfg = _make_app(self.tmp.name, music_gen_url="http://127.0.0.1:9884")
        db.set_setting("music_license_ack_stable_audio_open", "true")
        post_mock = mock.Mock(
            return_value=_FakeResponse(
                status_code=503, json_data={"detail": "model not loaded: OOM"}
            )
        )
        with _client(app, db, cfg) as client:
            r = self._post_with_worker(
                client,
                {"prompt": "x", "model": "stable_audio_open", "kind": "music", "duration_s": 10},
                post_mock,
            )
            job = client.get(
                f"/api/studio/music/jobs/{r.json()['job_id']}", headers=AUTH_HEADERS
            ).json()
        self.assertEqual(job["state"], "error")
        self.assertIn("OOM", job["error"])

    def test_unknown_job_404(self):
        app, db, cfg = _make_app(self.tmp.name)
        with _client(app, db, cfg) as client:
            r = client.get("/api/studio/music/jobs/nope", headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 404)


class EngineSerializationTest(unittest.TestCase):
    """Regression: model load must happen INSIDE the synth lock, otherwise a
    concurrent request for the other model can unload this one between load
    and inference (single-resident-model invariant break)."""

    def test_load_is_called_under_synth_lock(self):
        from sidecars.music_gen import engine

        seen = {}

        def fake_load(model_id):
            seen["lock_held"] = engine._synth_lock.locked()
            return  # unavailable → generate_wav raises RuntimeError

        with mock.patch.object(engine, "_load", side_effect=fake_load):
            with self.assertRaises(RuntimeError):
                engine.generate_wav("musicgen", "test prompt", 10)
        self.assertTrue(seen.get("lock_held"), "_load must run while _synth_lock is held")


class _FakeTensor:
    """Minimal torch-tensor stand-in: detach().to('cpu').float().numpy()."""

    def __init__(self, arr):
        self._arr = arr

    def detach(self):
        return self

    def to(self, _device):
        return self

    def float(self):
        return self

    def numpy(self):
        return self._arr


class EngineAdapterTest(unittest.TestCase):
    """Exercise the real inference adapters with representative outputs:
    Stable Audio returns a NUMPY array by default (output_type='np'),
    MusicGen returns torch tensors.  Both must survive WAV encoding."""

    def _assert_wav(self, wav: bytes, sr: int, expect_sr: int):
        self.assertEqual(sr, expect_sr)
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertEqual(wav[8:12], b"WAVE")

    def test_stable_audio_numpy_output_encodes_wav(self):
        import numpy as np

        from sidecars.music_gen import engine

        audio_np = np.zeros((2, 4410), dtype="float32")  # stereo numpy — the default
        result = SimpleNamespace(audios=[audio_np])
        pipe = mock.Mock(return_value=result)
        pipe.vae = SimpleNamespace(sampling_rate=44100)

        with mock.patch.object(engine, "_load", return_value=pipe):
            wav, sr = engine.generate_wav("stable_audio_open", "low braam", 2)
        self._assert_wav(wav, sr, 44100)
        call = pipe.call_args.kwargs
        self.assertEqual(call["prompt"], "low braam")
        self.assertEqual(call["audio_end_in_s"], 2.0)

    def test_stable_audio_tensor_output_also_handled(self):
        import numpy as np

        from sidecars.music_gen import engine

        tensor = _FakeTensor(np.zeros((2, 2205), dtype="float32"))
        pipe = mock.Mock(return_value=SimpleNamespace(audios=[tensor]))
        pipe.vae = SimpleNamespace(sampling_rate=44100)

        with mock.patch.object(engine, "_load", return_value=pipe):
            wav, sr = engine.generate_wav("stable_audio_open", "hit", 1)
        self._assert_wav(wav, sr, 44100)

    def test_musicgen_tensor_output_encodes_wav(self):
        import numpy as np

        from sidecars.music_gen import engine

        processor = mock.Mock()
        inputs = mock.Mock()
        processor.return_value.to.return_value = inputs
        inputs.keys = mock.Mock(return_value=[])
        model = mock.Mock()
        model.device = "cpu"
        model.config = SimpleNamespace(audio_encoder=SimpleNamespace(sampling_rate=32000))
        model.generate.return_value = [_FakeTensor(np.zeros((1, 32000), dtype="float32"))]

        # model.generate(**inputs, ...) requires a mapping — use a dict shim
        processor.return_value.to.return_value = {}
        with mock.patch.object(engine, "_load", return_value=(processor, model)):
            wav, sr = engine.generate_wav("musicgen", "warm piano", 10)
        self._assert_wav(wav, sr, 32000)
        # duration → token budget (~50 tok/s)
        self.assertEqual(model.generate.call_args.kwargs["max_new_tokens"], 500)

    def test_unexpected_inference_error_maps_to_runtime_error(self):
        from sidecars.music_gen import engine

        pipe = mock.Mock(side_effect=AttributeError("'ndarray' object has no attribute 'to'"))
        pipe.vae = SimpleNamespace(sampling_rate=44100)
        with mock.patch.object(engine, "_load", return_value=pipe):
            with self.assertRaises(RuntimeError) as ctx:
                engine.generate_wav("stable_audio_open", "x", 2)
        self.assertIn("AttributeError", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

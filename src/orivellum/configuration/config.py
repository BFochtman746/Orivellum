"""Central typed configuration for Orivellum.

Single authority — loaded once at startup. All ORIVELLUM_* environment
variables override YAML values. Sensitive values are redacted in logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# Workspace root = the directory containing src/
ROOT = Path(__file__).resolve().parents[3]
_REDACTED = "***REDACTED***"
_SENSITIVE_KEYS = {"api_key", "token", "secret", "password", "key"}


@dataclass
class ServingConfig:
    base_url: str = "http://127.0.0.1:13305/api/v1"

    # ── LLM models (verified against Lemonade Server catalog, Aug 2026) ────────
    # Names must match the Lemonade catalog EXACTLY (lemonade list / GET /v1/models).
    # On Strix Halo (~256 GB/s unified memory) MoE models decode 8-15× faster than
    # dense models of similar quality — a dense 70B runs ~4-5 tok/s, these run 30-70.
    #
    # workhorse: Qwen3.6-35B-A3B — MoE ~3B active, vision + tool-calling,
    #   ~23 GB. Fast enough for every interactive task.
    workhorse_model: str = "Qwen3.6-35B-A3B-GGUF"
    # reasoner: gpt-oss-120b in native MXFP4 (~63 GB, only 5.1B active params) —
    #   best local reasoning AND fast, because so few params are active per token.
    reasoner_model: str = "gpt-oss-120b-mxfp-GGUF"
    # coder: Qwen3-Coder-30B-A3B — purpose-built for agentic loops, 256K context,
    #   ~19 GB. Bigger alternative: Qwen3-Coder-Next-GGUF (~48 GB) when the
    #   reasoner is not resident at the same time.
    coder_model: str = "Qwen3-Coder-30B-A3B-Instruct-GGUF"

    # ── Embeddings ────────────────────────────────────────────────────────────
    # Qwen3-Embedding-8B: open-source SOTA (70.6 MTEB, Apache 2.0, ~8 GB Q8).
    # Lightweight alternative: nomic-embed-text-v2-moe-GGUF (~0.5 GB) — fast CPU.
    embedder_model: str = "Qwen3-Embedding-8B-GGUF"

    # ── Vision / multimodal ────────────────────────────────────────────────────
    # The workhorse (Qwen3.6-35B-A3B) is natively multimodal, so vision reuses it —
    # no extra download, dramatically better than Tesseract on scanned documents.
    # Leave empty to fall back to Tesseract OCR + workhorse_model for image chat.
    vision_model: str = "Qwen3.6-35B-A3B-GGUF"

    # ── TTS / ASR ─────────────────────────────────────────────────────────────
    # TTS model name served by the AI server (OpenAI /v1/audio/speech endpoint).
    # "tts-1-hd" gives higher quality than "tts-1".
    # Kokoro-82M local is used as fallback when the AI server is unreachable.
    tts_model: str = "tts-1-hd"
    # ASR model name for audio transcription (OpenAI /v1/audio/transcriptions).
    # Alternatives: "faster-whisper", "whisper-large-v3", "parakeet-tdt-1.1b".
    # faster-whisper (CTranslate2) is ~4× faster than vanilla whisper.
    asr_model: str = "whisper-1"
    # Local ASR model size for faster-whisper (used when AI server is absent).
    # Options (accuracy ↑ / speed ↓): "tiny", "base", "small", "medium",
    # "large-v3", "large-v3-turbo", "distil-large-v3".
    # "large-v3-turbo" (int8, ~1.6 GB) is dramatically more accurate than "base"
    # at similar speed on capable hardware — the default for machines like Nimo.
    # Low-memory machines fall back to "base" automatically (see extraction.py),
    # and a DB setting ("asr_local_model") overrides this value at runtime.
    # Models are downloaded automatically on first transcription to the HF cache.
    asr_local_model: str = "large-v3-turbo"

    # ── Premium TTS ───────────────────────────────────────────────────────────
    # Base URL for a premium TTS engine (Fish Audio S2 / Hume TADA / IndexTTS-2 / etc.).
    # When set AND tts_premium_ack_license is True, this engine is tried FIRST
    # in the synthesis chain before the AI server, Kokoro, and espeak-ng.
    # Leave empty (default) for zero regression — the standard chain is unchanged.
    # Example: "http://127.0.0.1:9880"  (Fish Audio S2 default port)
    tts_premium_url: str = ""
    # License acknowledgment — must be True before the premium path activates.
    # Fish Audio S2 / TADA may require commercial licensing for production use.
    # Set True only after confirming the license terms for your use case.
    tts_premium_ack_license: bool = False

    # ── Music / SFX generation ────────────────────────────────────────────────
    # Base URL for the local music generation sidecar (sidecars/music_gen).
    # Leave empty (default) to disable — trailer music/SFX Generate buttons
    # stay hidden.  Unlike the premium TTS flag, license acknowledgement is
    # PER MODEL and lives in DB settings (music_license_ack_<model_id>),
    # collected through the UI before a model can generate.
    # Example: "http://127.0.0.1:9884"
    music_gen_url: str = ""

    # ── Reranker ──────────────────────────────────────────────────────────────
    # Leave empty to use RRF (reciprocal-rank fusion) only.
    # Cross-encoder reranker served by Lemonade's /rerank endpoint.
    # Pull first: lemonade pull bge-reranker-v2-m3-GGUF  (~0.6 GB).
    # A circuit breaker keeps search fast when the model isn't pulled, so a
    # non-empty default is safe.  Set to "" to disable entirely.
    reranker_model: str = "bge-reranker-v2-m3-GGUF"

    # ── Timeouts ──────────────────────────────────────────────────────────────
    timeout_sec: int = 120
    # Dedicated short timeout for background AI extraction so a slow/absent AI
    # service never blocks the pipeline for the full chat timeout.
    extraction_timeout_sec: int = 30

    # ── Context window ────────────────────────────────────────────────────────
    # Used to trim conversation history and knowledge injection so the combined
    # prompt never exceeds the model's hard limit.
    # 32768 covers 7B–32B models; increase to 131072 for 70B+ with large KV cache.
    context_window: int = 32768

    @property
    def model(self) -> str:
        """Back-compat alias. Any caller reading `.model` gets the workhorse
        model instead of raising AttributeError. New code should name the
        model it wants (workhorse_model / reasoner_model / …) explicitly."""
        return self.workhorse_model


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    api_key: str = ""
    request_timeout_sec: int = 300
    max_body_bytes: int = 50 * 1024 * 1024  # 50 MB


@dataclass
class DatabaseConfig:
    path: str = ""  # resolved from data_dir at load time


@dataclass
class OrivellumConfig:
    data_dir: str = str(ROOT / "data")
    serving: ServingConfig = field(default_factory=ServingConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        # Ensure data_dir exists
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        # Resolve DB path from data_dir if not explicitly set
        if not self.database.path:
            self.database.path = str(Path(self.data_dir) / "orivellum.db")

    @property
    def db_path(self) -> str:
        return self.database.path

    def effective(self, redact: bool = True) -> dict[str, Any]:
        """Return the effective config as a dict, optionally redacting secrets."""
        raw = {
            "data_dir": self.data_dir,
            "db_path": self.db_path,
            "log_level": self.log_level,
            "serving": {
                "base_url": self.serving.base_url,
                "workhorse_model": self.serving.workhorse_model,
                "timeout_sec": self.serving.timeout_sec,
                "extraction_timeout_sec": self.serving.extraction_timeout_sec,
            },
            "server": {
                "host": self.server.host,
                "port": self.server.port,
                "api_key": _REDACTED if (redact and self.server.api_key) else self.server.api_key,
                "request_timeout_sec": self.server.request_timeout_sec,
            },
        }
        return raw


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config(path: str | None = None) -> OrivellumConfig:
    """Load configuration from YAML + ORIVELLUM_* environment overrides.

    Startup order (correct):
      1. Load config (this function)
      2. Resolve DB path
      3. Open DB
      4. Run migrations
      5. Build services
      6. Start API
    """
    raw: dict[str, Any] = {}

    # 1. Load YAML if available
    config_path = Path(path) if path else (ROOT / "config.yaml")
    if config_path.is_file() and yaml is not None:
        with open(config_path, encoding="utf-8-sig") as f:
            file_cfg = yaml.safe_load(f) or {}
            raw = _deep_merge(raw, file_cfg)

    # 2. Environment variable overrides (ORIVELLUM_*)
    env_map = {
        "ORIVELLUM_DATA_DIR": ("data_dir", str),
        "ORIVELLUM_LOG_LEVEL": ("log_level", str),
        "ORIVELLUM_API_KEY": ("server.api_key", str),
        "ORIVELLUM_PORT": ("server.port", int),
        "ORIVELLUM_HOST": ("server.host", str),
        "ORIVELLUM_AI_URL": ("serving.base_url", str),
        "ORIVELLUM_DB_PATH": ("database.path", str),
        "ORIVELLUM_EXTRACTION_TIMEOUT": ("serving.extraction_timeout_sec", int),
        "ORIVELLUM_CONTEXT_WINDOW": ("serving.context_window", int),
        "ORIVELLUM_EMBEDDER_MODEL": ("serving.embedder_model", str),
    }
    for env_key, (cfg_path, cast) in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            parts = cfg_path.split(".")
            d = raw
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = cast(val)

    # 3. Build typed config
    serving_raw = raw.get("serving", {})
    server_raw = raw.get("server", raw.get("orchestrator", {}))
    db_raw = raw.get("database", {})

    cfg = OrivellumConfig(
        data_dir=raw.get("data_dir", str(ROOT / "data")),
        log_level=raw.get("log_level", "INFO"),
        serving=ServingConfig(
            base_url=serving_raw.get("base_url", ServingConfig.base_url),
            workhorse_model=serving_raw.get(
                "workhorse_model",
                serving_raw.get("models", {}).get("workhorse", ServingConfig.workhorse_model),
            ),
            reasoner_model=serving_raw.get(
                "reasoner_model",
                serving_raw.get("models", {}).get("reasoner", ServingConfig.reasoner_model),
            ),
            coder_model=serving_raw.get(
                "coder_model", serving_raw.get("models", {}).get("coder", ServingConfig.coder_model)
            ),
            vision_model=serving_raw.get(
                "vision_model",
                serving_raw.get("models", {}).get("vision", ServingConfig.vision_model),
            ),
            embedder_model=serving_raw.get(
                "embedder_model",
                serving_raw.get("models", {}).get("embedder", ServingConfig.embedder_model),
            ),
            tts_model=serving_raw.get(
                "tts_model", serving_raw.get("models", {}).get("tts", ServingConfig.tts_model)
            ),
            asr_model=serving_raw.get(
                "asr_model", serving_raw.get("models", {}).get("asr", ServingConfig.asr_model)
            ),
            asr_local_model=serving_raw.get("asr_local_model", ServingConfig.asr_local_model),
            tts_premium_url=str(serving_raw.get("tts_premium_url", ServingConfig.tts_premium_url)),
            tts_premium_ack_license=bool(
                serving_raw.get("tts_premium_ack_license", ServingConfig.tts_premium_ack_license)
            ),
            music_gen_url=str(serving_raw.get("music_gen_url", ServingConfig.music_gen_url)),
            reranker_model=serving_raw.get(
                "reranker_model",
                serving_raw.get("models", {}).get("reranker", ServingConfig.reranker_model),
            ),
            timeout_sec=int(serving_raw.get("timeout_sec", ServingConfig.timeout_sec)),
            extraction_timeout_sec=int(
                serving_raw.get("extraction_timeout_sec", ServingConfig.extraction_timeout_sec)
            ),
            context_window=int(serving_raw.get("context_window", ServingConfig.context_window)),
        ),
        server=ServerConfig(
            host=str(server_raw.get("host", ServerConfig.host)),
            port=int(server_raw.get("port", ServerConfig.port)),
            api_key=str(server_raw.get("api_key", "")),
            request_timeout_sec=int(
                server_raw.get("request_timeout_sec", ServerConfig.request_timeout_sec)
            ),
            max_body_bytes=int(server_raw.get("max_body_bytes", ServerConfig.max_body_bytes)),
        ),
        database=DatabaseConfig(
            path=str(db_raw.get("path", "")),
        ),
    )
    return cfg

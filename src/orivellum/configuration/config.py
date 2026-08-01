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
    workhorse_model: str = "Qwen3-30B-A3B-Instruct-2507"
    reasoner_model: str = "gpt-oss-120b"
    coder_model: str = "Qwen3-Coder"
    embedder_model: str = "Qwen3-Embedding-0.6B"
    timeout_sec: int = 120
    # Dedicated short timeout for background AI extraction so a slow/absent AI
    # service never blocks the pipeline for the full chat timeout.
    extraction_timeout_sec: int = 30


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
            workhorse_model=serving_raw.get("workhorse_model", serving_raw.get(
                "models", {}).get("workhorse", ServingConfig.workhorse_model)),
            reasoner_model=serving_raw.get("reasoner_model", serving_raw.get(
                "models", {}).get("reasoner", ServingConfig.reasoner_model)),
            coder_model=serving_raw.get("coder_model", serving_raw.get(
                "models", {}).get("coder", ServingConfig.coder_model)),
            timeout_sec=int(serving_raw.get("timeout_sec", ServingConfig.timeout_sec)),
            extraction_timeout_sec=int(serving_raw.get(
                "extraction_timeout_sec", ServingConfig.extraction_timeout_sec)),
        ),
        server=ServerConfig(
            host=str(server_raw.get("host", ServerConfig.host)),
            port=int(server_raw.get("port", ServerConfig.port)),
            api_key=str(server_raw.get("api_key", "")),
            request_timeout_sec=int(server_raw.get(
                "request_timeout_sec", ServerConfig.request_timeout_sec)),
            max_body_bytes=int(server_raw.get(
                "max_body_bytes", ServerConfig.max_body_bytes)),
        ),
        database=DatabaseConfig(
            path=str(db_raw.get("path", "")),
        ),
    )
    return cfg

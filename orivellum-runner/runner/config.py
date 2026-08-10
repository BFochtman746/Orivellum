import os
from dataclasses import dataclass, field

def _b(k, d="0"): return os.getenv(k, d).strip().lower() in ("1","true","yes","on")
def _i(k, d): return int(os.getenv(k, str(d)))

@dataclass
class Cfg:
    mock: bool = field(default_factory=lambda: _b("MOCK", "1"))
    base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL","http://127.0.0.1:13305/api/v1"))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL","Qwen3.6-35B-A3B-GGUF"))
    coder_model: str = field(default_factory=lambda: os.getenv("LLM_CODER_MODEL",""))
    timeout: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT","300")))

    # Hard stops. A run that cannot finish must STOP and say so, not drift.
    max_units: int = field(default_factory=lambda: _i("MAX_UNITS", 4000))
    max_minutes: int = field(default_factory=lambda: _i("MAX_MINUTES", 240))
    max_tokens: int = field(default_factory=lambda: _i("MAX_TOKENS", 4_000_000))
    max_unit_retries: int = field(default_factory=lambda: _i("MAX_UNIT_RETRIES", 2))

    # Cap context usage BELOW the model's real window. See __init__ docstring.
    ctx_budget_chars: int = field(default_factory=lambda: _i("CTX_BUDGET_CHARS", 24000))
    compact_every: int = field(default_factory=lambda: _i("COMPACT_EVERY", 25))

    runs_dir: str = field(default_factory=lambda: os.getenv("RUNS_DIR","runs"))
    db: str = field(default_factory=lambda: os.getenv("RUNNER_DB","runs/runner.db"))

    # Optional scanners. Absent tools are reported as unavailable, never as clean.
    use_bandit: bool = field(default_factory=lambda: _b("USE_BANDIT","1"))
    use_semgrep: bool = field(default_factory=lambda: _b("USE_SEMGREP","0"))

CFG = Cfg()

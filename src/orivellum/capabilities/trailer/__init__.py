"""
Trailer Architect — Orivellum integration.

Port of the A-01 Media Studio trailer_architect package, adapted to:
- Use Orivellum's llm_call() helper instead of raw urllib
- Pull book content from the DB rather than the filesystem
- Run inside Orivellum's thread-pool executor
- Degrade gracefully to offline-stub mode when the LLM is unavailable
"""
from .runner import run_trailer_pipeline  # noqa: F401

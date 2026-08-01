# Orivellum — Master Build Plan
**Based on:** Monarch-main forensic audit + Final_Build_Instructions blueprint  
**Approach:** Clean vertical releases, working behavior at every step  
**Stack:** Python 3.13 / FastAPI / SQLite (backend) + React / Vite (frontend)

---

## Architecture Decision: What We Keep vs. What We Rebuild

### Keep from Monarch (proven, well-designed)
- The 37-migration SQLite schema — well-designed governed object model
- Core business logic from WorkSystem, LibrarySystem, ConversationStore
- All 20 capability module implementations (PDF, DOCX, OCR, Excel, Voice, etc.)
- The Monarch UI routes as reference for feature parity

### Rebuild Clean (addresses blueprint concerns)
- Single startup path (no build_system() before DB open)
- Typed configuration with one authority
- Clean package structure `src/orivellum/`
- Governed capability registry (not monkey-patched singletons)
- Explicit migration ledger
- No legacy monarch.db created on clean boot

---

## Package Structure

```
src/orivellum/
  __init__.py
  configuration/      # Typed config loading, env overrides, redaction
  database/           # SQLite connection, migration runner, schema (37 migrations)
  domains/            # Business logic: Works, Library, Conversations, Knowledge
  capabilities/       # Registry, module discovery, health reporting
  api/                # FastAPI app, routes, middleware, lifespan
  lifecycle/          # Startup/shutdown orchestration
  jobs/               # Persistent job engine
  adapters/           # Lemonade AI, file storage, optional Qdrant
data/                 # orivellum.db, library/, outputs/, backups/
ui/                   # React + Vite frontend (react-vite artifact)
```

---

## Release Sequence (Replit Build Order)

### ✅ Release 0 — Repository Foundation (Done in this session)
- pyproject.toml with uv, all dependencies declared
- Package structure under src/orivellum/
- FastAPI app boots, serves health/version/diagnostics
- React frontend artifact created

### Release 1 — Full Runnable Platform (Target: next session)
- Typed config loading with ORIVELLUM_* env overrides
- SQLite with all 37 migrations applied
- All core domain routes wired (Works, Conversations, Library, Knowledge)
- Structured logging (no print/console.log)
- Health model reporting service states
- CLI entry points (orivellum start/doctor/version)

### Release 2 — Capability Runtime
- Clean capability registry (replacing bootstrap.py singletons)
- Module discovery via entry points or manifest
- Capability health endpoint
- Individual module enable/disable

### Release 3 — Full Document Intelligence
- PDF, DOCX, OCR, Excel, PPTX processing migrated
- Per-format qualification evidence
- Native Office bridge (Windows A-01) as optional adapter

### Release 4 — Jobs + Automation
- Persistent job model (replaces daemon threads)
- Checkpointing, retry, cancellation
- Scheduler with failure reporting

### Release 5 — Voice + Audiobook
- Unified render service (Chatterbox, F5-TTS, Kokoro, Lemonade)
- Render manifests with provenance
- ACX profile (versioned, not hardcoded)
- EBU R128 loudness via FFmpeg loudnorm

### Release 6 — Security Hardening
- Loopback bind by default
- Authenticated sessions (Bearer or cookie)
- Restricted CORS (no wildcard)
- Upload size + MIME inspection
- Audit logs

### Release 7 — UI Consolidation
- All 10 pages fully wired to real data
- PWA/mobile-first behavior
- Background job progress visible in UI

---

## Capability Status (from audit)

| Capability | Monarch Status | Orivellum Action |
|------------|----------------|------------------|
| Platform | Transitional | ✅ Rebuild clean foundation |
| Works | Strong | Migrate + clean API |
| Library | Functional | Migrate + intake pipeline |
| Conversations | Functional | Migrate + project integration |
| Knowledge | Storage ✅, reasoning partial | Add claims/conflict/review |
| PDF | Functional | Qualify + corpus tests |
| DOCX | Functional | Native Word validation |
| Excel | Functional | Formula + recalc validation |
| OCR | Functional | Benchmark + accuracy |
| Voice/Audiobook | Functional prototype | Render manifest + QC |
| Learning | Emerging | Complete UI + adaptive flow |
| Projects | Functional | Make operational context |
| Backup | Strong foundation | Verified restore drill |
| Security | Partial | Harden LAN/mobile/uploads |

---

## Critical Corrections from Blueprint

1. **Startup order fixed**: config → database path → open orivellum.db → run migrations → build services → start API
2. **No monarch.db on clean boot**: regression test required
3. **No wildcard CORS**: restricted to configured origins
4. **No daemon-thread-only shutdown**: all durable work uses persistent jobs
5. **dBFS ≠ LUFS**: ACX profile uses EBU R128 via FFmpeg loudnorm
6. **No port scanning during requests**: Lemonade discovery is explicit, cached, bounded
7. **AI outage = degraded, not crash**: all AI calls have fallback paths

---

## AI / Lemonade Config (never Ollama)
- Workhorse: Qwen3-30B-A3B-Instruct-2507
- Reasoner: gpt-oss-120b
- Coder: Qwen3-Coder
- Embedder: Qwen3-Embedding-0.6B
- Endpoint: http://127.0.0.1:13305/api/v1

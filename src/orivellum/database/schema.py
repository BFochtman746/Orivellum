"""Orivellum — Authoritative SQLite Schema.

Migrations are numbered SQL blocks applied in ascending order.
The schema version is tracked in the settings table (key=schema_version).

Rules:
- Never modify an existing migration — add a new one.
- Migration identifiers are immutable after release.
- Every migration is idempotent (IF NOT EXISTS, IF NOT columns, etc.).
"""
from __future__ import annotations

# Each entry: (version: int, description: str, sql: str)
MIGRATIONS: list[tuple[int, str, str]] = [

    # v1 — foundation: governed objects root + settings
    (1, "Governed objects root table and settings", """
        CREATE TABLE IF NOT EXISTS settings (
            id         TEXT PRIMARY KEY,
            scope      TEXT NOT NULL DEFAULT 'global',
            key        TEXT NOT NULL,
            value      TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(scope, key)
        );
        CREATE TABLE IF NOT EXISTS objects (
            id          TEXT PRIMARY KEY,
            type        TEXT NOT NULL,
            version     INTEGER NOT NULL DEFAULT 1,
            lifecycle   TEXT NOT NULL DEFAULT 'active',
            provenance  TEXT NOT NULL DEFAULT '{}',
            permissions TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            created_by  TEXT NOT NULL DEFAULT 'user',
            checksum    TEXT
        );
        CREATE INDEX IF NOT EXISTS objects_type      ON objects(type);
        CREATE INDEX IF NOT EXISTS objects_lifecycle ON objects(lifecycle);
        CREATE INDEX IF NOT EXISTS objects_created   ON objects(created_at);
    """),

    # v2 — Works
    (2, "Works table", """
        CREATE TABLE IF NOT EXISTS works (
            id          TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
            title       TEXT NOT NULL,
            work_type   TEXT NOT NULL,
            description TEXT,
            status      TEXT NOT NULL DEFAULT 'active',
            meta        TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS works_status ON works(status);
        CREATE INDEX IF NOT EXISTS works_type   ON works(work_type);
        CREATE VIRTUAL TABLE IF NOT EXISTS works_fts
            USING fts5(title, description, work_id UNINDEXED);
    """),

    # v3 — Relationships (graph edges)
    (3, "Relationships graph edges", """
        CREATE TABLE IF NOT EXISTS relationships (
            id          TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
            source_id   TEXT NOT NULL,
            target_id   TEXT NOT NULL,
            kind        TEXT NOT NULL,
            weight      REAL NOT NULL DEFAULT 1.0,
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS rel_source ON relationships(source_id);
        CREATE INDEX IF NOT EXISTS rel_target ON relationships(target_id);
        CREATE INDEX IF NOT EXISTS rel_kind   ON relationships(kind);
    """),

    # v4 — Audit log
    (4, "Audit log", """
        CREATE TABLE IF NOT EXISTS audit_log (
            id          TEXT PRIMARY KEY,
            timestamp   TEXT NOT NULL,
            actor       TEXT NOT NULL DEFAULT 'user',
            operation   TEXT NOT NULL,
            object_id   TEXT,
            object_type TEXT,
            before_hash TEXT,
            after_hash  TEXT,
            correlation_id TEXT,
            result      TEXT NOT NULL DEFAULT 'ok',
            detail      TEXT,
            app_version TEXT
        );
        CREATE INDEX IF NOT EXISTS audit_object    ON audit_log(object_id);
        CREATE INDEX IF NOT EXISTS audit_timestamp ON audit_log(timestamp);
        CREATE INDEX IF NOT EXISTS audit_actor     ON audit_log(actor);
    """),

    # v5 — Documents (Library)
    (5, "Documents and chunks", """
        CREATE TABLE IF NOT EXISTS documents (
            id             TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
            work_id        TEXT REFERENCES works(id) ON DELETE SET NULL,
            title          TEXT,
            source         TEXT,
            sha256         TEXT UNIQUE,
            kind           TEXT,
            classification TEXT,
            readiness      TEXT NOT NULL DEFAULT 'imported',
            content_path   TEXT,
            meta           TEXT NOT NULL DEFAULT '{}',
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS docs_work      ON documents(work_id);
        CREATE INDEX IF NOT EXISTS docs_sha256    ON documents(sha256);
        CREATE INDEX IF NOT EXISTS docs_kind      ON documents(kind);
        CREATE INDEX IF NOT EXISTS docs_readiness ON documents(readiness);

        CREATE TABLE IF NOT EXISTS chunks (
            id             TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
            doc_id         TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            page           INTEGER,
            text           TEXT NOT NULL,
            embedding_path TEXT,
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
            USING fts5(text, chunk_id UNINDEXED, doc_id UNINDEXED);
    """),

    # v6 — Knowledge objects
    (6, "Knowledge objects", """
        CREATE TABLE IF NOT EXISTS knowledge (
            id              TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
            work_id         TEXT REFERENCES works(id) ON DELETE SET NULL,
            kind            TEXT NOT NULL,
            text            TEXT NOT NULL,
            subject         TEXT,
            predicate       TEXT,
            object          TEXT,
            confidence      REAL NOT NULL DEFAULT 1.0,
            source_doc_id   TEXT REFERENCES documents(id) ON DELETE SET NULL,
            source_chunk_id TEXT,
            source_offset   INTEGER,
            review_status   TEXT NOT NULL DEFAULT 'unreviewed',
            meta            TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS knowledge_work   ON knowledge(work_id);
        CREATE INDEX IF NOT EXISTS knowledge_kind   ON knowledge(kind);
        CREATE INDEX IF NOT EXISTS knowledge_review ON knowledge(review_status);
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
            USING fts5(text, subject, object, knowledge_id UNINDEXED, work_id UNINDEXED);
    """),

    # v7 — Tasks
    (7, "Tasks", """
        CREATE TABLE IF NOT EXISTS tasks (
            id          TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
            work_id     TEXT REFERENCES works(id) ON DELETE CASCADE,
            text        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            priority    INTEGER NOT NULL DEFAULT 0,
            due_at      TEXT,
            completed_at TEXT,
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS tasks_work   ON tasks(work_id);
        CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status);
    """),

    # v8 — Conversations and Messages
    (8, "Conversations and messages", """
        CREATE TABLE IF NOT EXISTS conversations (
            id          TEXT PRIMARY KEY,
            work_id     TEXT REFERENCES works(id) ON DELETE SET NULL,
            title       TEXT,
            archived    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS conv_work       ON conversations(work_id);
        CREATE INDEX IF NOT EXISTS conv_updated    ON conversations(updated_at);
        CREATE INDEX IF NOT EXISTS conv_archived   ON conversations(archived);

        CREATE TABLE IF NOT EXISTS messages (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role            TEXT NOT NULL,
            text            TEXT NOT NULL,
            meta            TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS msg_conv    ON messages(conversation_id);
        CREATE INDEX IF NOT EXISTS msg_created ON messages(created_at);
    """),

    # v9 — Persistent jobs
    (9, "Persistent jobs", """
        CREATE TABLE IF NOT EXISTS jobs (
            id              TEXT PRIMARY KEY,
            job_type        TEXT NOT NULL,
            state           TEXT NOT NULL DEFAULT 'queued',
            priority        INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL,
            started_at      TEXT,
            heartbeat_at    TEXT,
            completed_at    TEXT,
            attempt         INTEGER NOT NULL DEFAULT 0,
            max_attempts    INTEGER NOT NULL DEFAULT 3,
            input           TEXT NOT NULL DEFAULT '{}',
            checkpoint      TEXT,
            result          TEXT,
            error           TEXT,
            correlation_id  TEXT
        );
        CREATE INDEX IF NOT EXISTS jobs_state   ON jobs(state);
        CREATE INDEX IF NOT EXISTS jobs_type    ON jobs(job_type);
        CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created_at);
    """),

    # v10 — Publications
    (10, "Publications", """
        CREATE TABLE IF NOT EXISTS publications (
            id          TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
            work_id     TEXT REFERENCES works(id) ON DELETE CASCADE,
            format      TEXT NOT NULL,
            path        TEXT,
            config      TEXT NOT NULL DEFAULT '{}',
            status      TEXT NOT NULL DEFAULT 'pending',
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS pub_work   ON publications(work_id);
        CREATE INDEX IF NOT EXISTS pub_format ON publications(format);
    """),

    # v11 — Knowledge text_hash dedup
    (11, "Knowledge text_hash dedup", """
        ALTER TABLE knowledge ADD COLUMN text_hash TEXT;
        CREATE INDEX IF NOT EXISTS knowledge_hash ON knowledge(text_hash);
    """),

    # v12 — Book pipelines
    (12, "Book pipelines", """
        CREATE TABLE IF NOT EXISTS book_pipelines (
            id          TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
            work_id     TEXT REFERENCES works(id) ON DELETE CASCADE,
            title       TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'idle',
            config      TEXT NOT NULL DEFAULT '{}',
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS bp_work   ON book_pipelines(work_id);
        CREATE INDEX IF NOT EXISTS bp_status ON book_pipelines(status);
    """),

    # v13 — Book chapters
    (13, "Book chapters", """
        CREATE TABLE IF NOT EXISTS book_chapters (
            id          TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
            pipeline_id TEXT REFERENCES book_pipelines(id) ON DELETE CASCADE,
            work_id     TEXT REFERENCES works(id) ON DELETE CASCADE,
            seq         INTEGER NOT NULL DEFAULT 0,
            title       TEXT,
            text        TEXT,
            source_doc_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
            citations   TEXT NOT NULL DEFAULT '[]',
            status      TEXT NOT NULL DEFAULT 'draft',
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS bc_pipeline ON book_chapters(pipeline_id);
        CREATE INDEX IF NOT EXISTS bc_work     ON book_chapters(work_id);
        CREATE INDEX IF NOT EXISTS bc_seq      ON book_chapters(seq);
    """),

    # v14 — Document extracted_text column
    (14, "Document extracted_text column", """
        ALTER TABLE documents ADD COLUMN extracted_text TEXT;
    """),

    # v15 — Document classification_source column
    (15, "Document classification_source column", """
        ALTER TABLE documents ADD COLUMN classification_source TEXT;
    """),

    # v16 — Suggestions table
    (16, "Suggestions table", """
        CREATE TABLE IF NOT EXISTS suggestions (
            id          TEXT PRIMARY KEY,
            work_id     TEXT REFERENCES works(id) ON DELETE CASCADE,
            kind        TEXT NOT NULL,
            text        TEXT NOT NULL,
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            expires_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS sug_work    ON suggestions(work_id);
        CREATE INDEX IF NOT EXISTS sug_kind    ON suggestions(kind);
        CREATE INDEX IF NOT EXISTS sug_created ON suggestions(created_at);
    """),

    # v17 — Composite indexes for work_summary counts
    (17, "Composite indexes for work_summary counts", """
        CREATE INDEX IF NOT EXISTS docs_work_readiness ON documents(work_id, readiness);
        CREATE INDEX IF NOT EXISTS tasks_work_status   ON tasks(work_id, status);
        CREATE INDEX IF NOT EXISTS knowledge_work_kind ON knowledge(work_id, kind);
    """),

    # v18 — Daily writing stats
    (18, "Daily writing stats", """
        CREATE TABLE IF NOT EXISTS daily_stats (
            id         TEXT PRIMARY KEY,
            date       TEXT NOT NULL,
            work_id    TEXT REFERENCES works(id) ON DELETE CASCADE,
            words      INTEGER NOT NULL DEFAULT 0,
            sessions   INTEGER NOT NULL DEFAULT 0,
            meta       TEXT NOT NULL DEFAULT '{}',
            UNIQUE(date, work_id)
        );
        CREATE INDEX IF NOT EXISTS ds_date    ON daily_stats(date);
        CREATE INDEX IF NOT EXISTS ds_work    ON daily_stats(work_id);
    """),

    # v19 — Graph layout per Work
    (19, "Graph layout per Work", """
        CREATE TABLE IF NOT EXISTS graph_layouts (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            layout_data TEXT NOT NULL DEFAULT '{}',
            updated_at  TEXT NOT NULL,
            UNIQUE(work_id)
        );
    """),

    # v20 — Long-term memories
    (20, "Long-term memories", """
        CREATE TABLE IF NOT EXISTS memories (
            id          TEXT PRIMARY KEY,
            scope       TEXT NOT NULL DEFAULT 'user',
            work_id     TEXT REFERENCES works(id) ON DELETE SET NULL,
            kind        TEXT NOT NULL DEFAULT 'fact',
            text        TEXT NOT NULL,
            authority   TEXT NOT NULL DEFAULT 'inferred',
            expires_at  TEXT,
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS mem_scope  ON memories(scope);
        CREATE INDEX IF NOT EXISTS mem_work   ON memories(work_id);
        CREATE INDEX IF NOT EXISTS mem_kind   ON memories(kind);
    """),

    # v21 — Audit log compound index
    (21, "Audit log compound index for prune performance", """
        CREATE INDEX IF NOT EXISTS audit_obj_ts ON audit_log(object_id, timestamp);
    """),

    # v22 — Pending reclassification queue
    (22, "Pending reclassification queue", """
        CREATE TABLE IF NOT EXISTS pending_reclassify (
            id          TEXT PRIMARY KEY,
            doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            reason      TEXT,
            created_at  TEXT NOT NULL,
            UNIQUE(doc_id)
        );
    """),

    # v23 — Chapter citation provenance columns
    (23, "Chapter citation provenance columns", """
        ALTER TABLE book_chapters ADD COLUMN citation_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE book_chapters ADD COLUMN extraction_method TEXT;
    """),

    # v24 — Chapter attribution method column
    (24, "Chapter attribution method column", """
        ALTER TABLE book_chapters ADD COLUMN attribution_meta TEXT;
    """),

    # v25 — Module lessons table
    (25, "Module lessons table", """
        CREATE TABLE IF NOT EXISTS lessons (
            id          TEXT PRIMARY KEY,
            module      TEXT NOT NULL,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'concept',
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS lessons_module ON lessons(module);
        CREATE INDEX IF NOT EXISTS lessons_kind   ON lessons(kind);
    """),

    # v26 — Archived files table
    (26, "Archived files table", """
        CREATE TABLE IF NOT EXISTS archived_files (
            id          TEXT PRIMARY KEY,
            original_path TEXT NOT NULL,
            archive_path  TEXT NOT NULL,
            sha256        TEXT,
            reason        TEXT,
            archived_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS af_sha256 ON archived_files(sha256);
    """),

    # v27 — Compass project-navigator table
    (27, "Compass project-navigator table", """
        CREATE TABLE IF NOT EXISTS compass (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            step        TEXT NOT NULL,
            state       TEXT NOT NULL DEFAULT 'pending',
            detail      TEXT,
            updated_at  TEXT NOT NULL,
            UNIQUE(work_id, step)
        );
        CREATE INDEX IF NOT EXISTS compass_work ON compass(work_id);
    """),

    # v28 — Expectation events and feedback tables
    (28, "Expectation events and feedback tables", """
        CREATE TABLE IF NOT EXISTS expectation_events (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT,
            turn_hash       TEXT NOT NULL,
            vector          TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS expectation_feedback (
            id          TEXT PRIMARY KEY,
            event_id    TEXT REFERENCES expectation_events(id) ON DELETE CASCADE,
            rating      INTEGER,
            signal      TEXT,
            detail      TEXT,
            created_at  TEXT NOT NULL
        );
    """),

    # v29 — Store entity graph and local vectors
    (29, "Store entity graph and local vectors", """
        CREATE TABLE IF NOT EXISTS entities (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            kind        TEXT NOT NULL,
            canonical   INTEGER NOT NULL DEFAULT 1,
            aliases     TEXT NOT NULL DEFAULT '[]',
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS entities_kind ON entities(kind);
        CREATE TABLE IF NOT EXISTS edges (
            id          TEXT PRIMARY KEY,
            source_id   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            target_id   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            relation    TEXT NOT NULL,
            weight      REAL NOT NULL DEFAULT 1.0,
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS edges_source ON edges(source_id);
        CREATE INDEX IF NOT EXISTS edges_target ON edges(target_id);
        CREATE TABLE IF NOT EXISTS vectors (
            id          TEXT PRIMARY KEY,
            object_id   TEXT NOT NULL,
            object_type TEXT NOT NULL,
            embedding   BLOB,
            dim         INTEGER,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS vectors_object ON vectors(object_id);
    """),

    # v30 — BookVault legacy book tables
    (30, "BookVault book tables", """
        CREATE TABLE IF NOT EXISTS books (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            author      TEXT,
            path        TEXT,
            sha256      TEXT UNIQUE,
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS book_masters (
            id          TEXT PRIMARY KEY,
            book_id     TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            format      TEXT NOT NULL,
            path        TEXT NOT NULL,
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS bm_book ON book_masters(book_id);
        CREATE TABLE IF NOT EXISTS book_eval_notes (
            id          TEXT PRIMARY KEY,
            book_id     TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            text        TEXT NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'note',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ben_book ON book_eval_notes(book_id);
    """),

    # v31 — Knowledge Fabric tables
    (31, "Knowledge Fabric tables", """
        CREATE TABLE IF NOT EXISTS fact_memory (
            id          TEXT PRIMARY KEY,
            subject     TEXT NOT NULL,
            predicate   TEXT NOT NULL,
            object      TEXT NOT NULL,
            confidence  REAL NOT NULL DEFAULT 1.0,
            source_id   TEXT,
            created_at  TEXT NOT NULL,
            UNIQUE(subject, predicate, object)
        );
        CREATE TABLE IF NOT EXISTS conflicts (
            id          TEXT PRIMARY KEY,
            claim_a_id  TEXT NOT NULL,
            claim_b_id  TEXT NOT NULL,
            conflict_type TEXT NOT NULL,
            resolution  TEXT,
            created_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS topics (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            kind        TEXT NOT NULL DEFAULT 'topic',
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS topic_members (
            topic_id   TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            object_id  TEXT NOT NULL,
            object_type TEXT NOT NULL,
            PRIMARY KEY (topic_id, object_id)
        );
        CREATE TABLE IF NOT EXISTS doc_dupes (
            id          TEXT PRIMARY KEY,
            doc_a_id    TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            doc_b_id    TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            similarity  REAL NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'near',
            created_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS minhash_sig (
            doc_id      TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
            sig         BLOB NOT NULL,
            created_at  TEXT NOT NULL
        );
    """),

    # v32 — Extraction warnings table
    (32, "Extraction warnings table", """
        CREATE TABLE IF NOT EXISTS extraction_warnings (
            id          TEXT PRIMARY KEY,
            doc_id      TEXT REFERENCES documents(id) ON DELETE CASCADE,
            kind        TEXT NOT NULL,
            detail      TEXT,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ew_doc  ON extraction_warnings(doc_id);
        CREATE INDEX IF NOT EXISTS ew_kind ON extraction_warnings(kind);
    """),

    # v33 — FORGE learning table
    (33, "FORGE learning table", """
        CREATE TABLE IF NOT EXISTS forge_learning (
            id          TEXT PRIMARY KEY,
            work_id     TEXT REFERENCES works(id) ON DELETE CASCADE,
            chapter_id  TEXT,
            score_data  TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS fl_work ON forge_learning(work_id);
    """),

    # v34 — Conversations archived flag (additive column, idempotent via DDL)
    (34, "Conversations archived flag", """
        CREATE INDEX IF NOT EXISTS conv_work_arch ON conversations(work_id, archived);
    """),

    # v39 — Conversations model column
    (39, "Conversations model column", """
        ALTER TABLE conversations ADD COLUMN model TEXT;
    """),

    # v38 — Documents error_message column
    (38, "Documents error_message column", """
        ALTER TABLE documents ADD COLUMN error_message TEXT;
    """),

    # v35 — Documents word_count; Knowledge accessed_at
    (35, "Documents word_count; Knowledge accessed_at", """
        ALTER TABLE documents ADD COLUMN word_count INTEGER;
        ALTER TABLE knowledge ADD COLUMN accessed_at TEXT;
        CREATE INDEX IF NOT EXISTS knowledge_accessed ON knowledge(accessed_at);
    """),

    # v36 — Learning module: concept DAG + mastery tracking
    (36, "Learning module concept DAG and mastery tracking", """
        CREATE TABLE IF NOT EXISTS learning_concepts (
            id          TEXT PRIMARY KEY,
            work_id     TEXT REFERENCES works(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            description TEXT,
            mastery     REAL NOT NULL DEFAULT 0.0,
            last_review TEXT,
            meta        TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS lc_work ON learning_concepts(work_id);
        CREATE TABLE IF NOT EXISTS learning_prerequisites (
            concept_id  TEXT NOT NULL REFERENCES learning_concepts(id) ON DELETE CASCADE,
            prereq_id   TEXT NOT NULL REFERENCES learning_concepts(id) ON DELETE CASCADE,
            PRIMARY KEY (concept_id, prereq_id)
        );
        CREATE TABLE IF NOT EXISTS learning_mastery (
            id          TEXT PRIMARY KEY,
            concept_id  TEXT NOT NULL REFERENCES learning_concepts(id) ON DELETE CASCADE,
            score       REAL NOT NULL,
            method      TEXT,
            evidence    TEXT,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS lm_concept ON learning_mastery(concept_id);
    """),

    # v37 — Voice narration module: voice_profiles table
    (37, "Voice narration: voice_profiles table", """
        CREATE TABLE IF NOT EXISTS voice_profiles (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            engine          TEXT NOT NULL DEFAULT 'kokoro',
            voice_id        TEXT,
            reference_path  TEXT,
            config          TEXT NOT NULL DEFAULT '{}',
            is_default      INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS vp_engine ON voice_profiles(engine);
    """),

    # v40 — User memory + nightshift tracking tables
    (40, "User memory and nightshift tracking", """
        CREATE TABLE IF NOT EXISTS user_memory (
            id              TEXT PRIMARY KEY,
            key             TEXT NOT NULL,
            value           TEXT NOT NULL,
            source_conv_id  TEXT,
            created_at      TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS um_key ON user_memory(key);
        CREATE TABLE IF NOT EXISTS nightshift_runs (
            id              TEXT PRIMARY KEY,
            ran_at          TEXT NOT NULL,
            docs_processed  INTEGER NOT NULL DEFAULT 0,
            items_added     INTEGER NOT NULL DEFAULT 0,
            report_path     TEXT
        );
    """),

    # v41 — Project Compass per-Work state for cognition system
    (41, "Project Compass state table", """
        CREATE TABLE IF NOT EXISTS project_compass (
            work_id     TEXT PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
            focus       TEXT,
            last_reasoning TEXT,
            next_step   TEXT,
            updated_at  TEXT NOT NULL
        );
    """),

    # v42 — Adaptive learning: concept graph + mastery tracking
    # NOTE: v42 used CREATE TABLE IF NOT EXISTS which silently preserved a stale
    # Monarch schema (columns: name, mastery, etc.). v43 below drops and recreates.
    (42, "Adaptive learning tables (legacy — superseded by v43)", """
        SELECT 1
    """),

    # v43 — (historically ran DROP+CREATE on learning_concepts/mastery — now a no-op at DB level;
    #          v44 restores the Projects schema and adds the correct work_concepts tables)
    (43, "Adaptive learning tables — superseded by v44", "SELECT 1"),

    # v45 — Write Desk: rich-text document drafting workspace with AI assistance.
    (45, "Add write_documents table for Write Desk", """
        CREATE TABLE IF NOT EXISTS write_documents (
            id           TEXT PRIMARY KEY,
            title        TEXT NOT NULL DEFAULT 'Untitled',
            content_json TEXT NOT NULL DEFAULT '{}',
            content_text TEXT NOT NULL DEFAULT '',
            word_count   INTEGER NOT NULL DEFAULT 0,
            work_id      TEXT REFERENCES works(id) ON DELETE SET NULL,
            is_pinned    INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS write_documents_updated ON write_documents(updated_at DESC);
        CREATE INDEX IF NOT EXISTS write_documents_work ON write_documents(work_id)
    """),

    # v44 — Add Work-scoped adaptive learning tables.
    # Projects owns learning_concepts/learning_mastery — those are NEVER touched here.
    # This migration is purely additive: CREATE TABLE IF NOT EXISTS only.
    (44, "Add work_concepts and work_mastery for Work-scoped adaptive learning", """
        CREATE TABLE IF NOT EXISTS work_concepts (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            subject     TEXT NOT NULL,
            description TEXT,
            prereq_id   TEXT REFERENCES work_concepts(id),
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS work_concepts_work ON work_concepts(work_id);
        CREATE TABLE IF NOT EXISTS work_mastery (
            id                  TEXT PRIMARY KEY,
            concept_id          TEXT NOT NULL REFERENCES work_concepts(id) ON DELETE CASCADE,
            score               REAL    NOT NULL DEFAULT 0,
            consecutive_passes  INTEGER NOT NULL DEFAULT 0,
            brief_feedback      TEXT,
            routed_to           TEXT,
            created_at          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS work_mastery_concept ON work_mastery(concept_id)
    """),

    # v46 — Document version tracking (MONARCH #146)
    (46, "Document version snapshots and canonical flag", """
        CREATE TABLE IF NOT EXISTS doc_versions (
            id            TEXT PRIMARY KEY,
            doc_id        TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            version_num   INTEGER NOT NULL DEFAULT 1,
            sha256        TEXT,
            word_count    INTEGER NOT NULL DEFAULT 0,
            notes         TEXT,
            is_canonical  INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            created_by    TEXT NOT NULL DEFAULT 'user'
        );
        CREATE INDEX IF NOT EXISTS dv_doc     ON doc_versions(doc_id);
        CREATE INDEX IF NOT EXISTS dv_canonical ON doc_versions(doc_id, is_canonical)
    """),

    # v47 — Chapter heading level (H1/H2/H3) on book_chapters (MONARCH #144)
    (47, "Add heading level column to book_chapters", """
        ALTER TABLE book_chapters ADD COLUMN level INTEGER NOT NULL DEFAULT 1
    """),

    # v48 — Document lifecycle activation (MONARCH #146)
    # Sets all existing document objects from 'active' → 'draft' so lifecycle
    # is meaningful for every document, new or old.
    (48, "Activate document lifecycle field — existing docs become draft", """
        UPDATE objects SET lifecycle='draft', updated_at=datetime('now')
        WHERE type='document' AND lifecycle='active'
    """),

    # v49 — Near-duplicate resolution tracking (MONARCH #147)
    # Adds resolved/resolution columns to doc_dupes so pairs can be dismissed
    # or acted upon without being re-detected.
    (49, "Add resolution columns to doc_dupes", """
        ALTER TABLE doc_dupes ADD COLUMN resolved INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE doc_dupes ADD COLUMN resolution TEXT;
        CREATE INDEX IF NOT EXISTS dd_resolved ON doc_dupes(resolved)
    """),

    # v50 — Gap detection cache (MONARCH #172)
    # Stores the most-recent gap detection result per Work so the dashboard
    # /gaps/top endpoint can return instantly without re-running detection on
    # every request.  Each row is updated in-place on every detection run;
    # rows older than 1 h are considered stale and trigger re-detection.
    (50, "Add work_gap_cache table for dashboard performance", """
        CREATE TABLE IF NOT EXISTS work_gap_cache (
            work_id      TEXT PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
            gaps_json    TEXT NOT NULL DEFAULT '[]',
            coverage_pct REAL,
            evaluated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS wgc_evaluated ON work_gap_cache(evaluated_at)
    """),

    # v51 — LLM call telemetry (MCOS Phase 0)
    # Every non-streaming chat-completion call routed through the central
    # gateway (capabilities/llm.py) records one row here: purpose label,
    # model, latency and token usage.  Powers the MCOS telemetry dashboard
    # and cost/latency trend analysis.
    (51, "Add llm_calls telemetry table", """
        CREATE TABLE IF NOT EXISTS llm_calls (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                TEXT    NOT NULL DEFAULT (datetime('now')),
            purpose           TEXT    NOT NULL DEFAULT '',
            model             TEXT    NOT NULL DEFAULT '',
            latency_ms        INTEGER,
            prompt_tokens     INTEGER,
            completion_tokens INTEGER,
            ok                INTEGER NOT NULL DEFAULT 1,
            error             TEXT
        );
        CREATE INDEX IF NOT EXISTS llmc_ts ON llm_calls(ts);
        CREATE INDEX IF NOT EXISTS llmc_purpose ON llm_calls(purpose)
    """),

    # v52 — MCOS benchmark repository + evaluation runs (MCOS Phase 1)
    # benchmarks: versioned suites; benchmark_cases: golden cases with
    # expected concepts + scoring rules; eval_runs/eval_results: every
    # execution with per-case scores so regressions are detectable.
    (52, "Add MCOS benchmark and evaluation tables", """
        CREATE TABLE IF NOT EXISTS benchmarks (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            category    TEXT NOT NULL DEFAULT 'general',
            kind        TEXT NOT NULL DEFAULT 'llm',
            version     INTEGER NOT NULL DEFAULT 1,
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS benchmark_cases (
            id           TEXT PRIMARY KEY,
            benchmark_id TEXT NOT NULL REFERENCES benchmarks(id) ON DELETE CASCADE,
            question     TEXT NOT NULL,
            context      TEXT NOT NULL DEFAULT '',
            expected_output   TEXT NOT NULL DEFAULT '',
            expected_concepts TEXT NOT NULL DEFAULT '[]',
            scoring      TEXT NOT NULL DEFAULT '{}',
            difficulty   TEXT NOT NULL DEFAULT 'medium',
            tags         TEXT NOT NULL DEFAULT '[]',
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS bc_benchmark ON benchmark_cases(benchmark_id);
        CREATE TABLE IF NOT EXISTS eval_runs (
            id           TEXT PRIMARY KEY,
            benchmark_id TEXT NOT NULL REFERENCES benchmarks(id) ON DELETE CASCADE,
            started_at   TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at  TEXT,
            model        TEXT NOT NULL DEFAULT '',
            status       TEXT NOT NULL DEFAULT 'running',
            total_cases  INTEGER NOT NULL DEFAULT 0,
            avg_score    REAL,
            meta         TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS er_benchmark ON eval_runs(benchmark_id, started_at);
        CREATE TABLE IF NOT EXISTS eval_results (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id     TEXT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
            case_id    TEXT NOT NULL,
            score      REAL,
            judge_scores TEXT NOT NULL DEFAULT '{}',
            response   TEXT NOT NULL DEFAULT '',
            latency_ms INTEGER,
            error      TEXT
        );
        CREATE INDEX IF NOT EXISTS evr_run ON eval_results(run_id)
    """),

    # v53 — MCOS Phase 4/5: prompt registry + RAG calibration sweeps.
    # prompts: versioned system-prompt candidates per slot; exactly one active
    # per slot (enforced by activation transaction + partial unique index).
    # rag_sweeps: in-memory chunking grid-search runs (no chunk-table writes).
    (53, "Add MCOS prompt registry and RAG sweep tables", """
        CREATE TABLE IF NOT EXISTS prompts (
            id         TEXT PRIMARY KEY,
            slot       TEXT NOT NULL,
            name       TEXT NOT NULL,
            content    TEXT NOT NULL,
            version    INTEGER NOT NULL DEFAULT 1,
            active     INTEGER NOT NULL DEFAULT 0,
            notes      TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS prompts_slot ON prompts(slot);
        CREATE UNIQUE INDEX IF NOT EXISTS prompts_active_per_slot
            ON prompts(slot) WHERE active=1;
        CREATE TABLE IF NOT EXISTS rag_sweeps (
            id           TEXT PRIMARY KEY,
            started_at   TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at  TEXT,
            status       TEXT NOT NULL DEFAULT 'running',
            docs_sampled INTEGER NOT NULL DEFAULT 0,
            results      TEXT NOT NULL DEFAULT '[]',
            meta         TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS rag_sweeps_started ON rag_sweeps(started_at)
    """),

    # v54 — Governance review queue deferrals (MONARCH #151)
    # A deferred review item is snoozed: excluded from /api/review/queue until
    # deferred_until passes. item_key is "<type>:<row id>" (e.g. "knowledge:abc").
    (54, "Add review_deferrals table for the governance review queue", """
        CREATE TABLE IF NOT EXISTS review_deferrals (
            item_key       TEXT PRIMARY KEY,
            deferred_until TEXT NOT NULL,
            reason         TEXT,
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS rd_until ON review_deferrals(deferred_until)
    """),

    # ── Sovereign Platform M0.1 — governed data foundation ──────────────────

    # v55 — Hash-chain columns on audit_log.
    # Every new audit row stores the previous row's row_hash (prev_hash) and
    # its own hash (row_hash = sha256(prev_hash | operation | object_id |
    # detail | timestamp | id)).  Rows written before v55 have NULL hashes and
    # are skipped by verify_audit_chain().
    (55, "Add hash-chain columns to audit_log (prev_hash, row_hash)", """
        ALTER TABLE audit_log ADD COLUMN prev_hash TEXT;
        ALTER TABLE audit_log ADD COLUMN row_hash  TEXT;
        CREATE INDEX IF NOT EXISTS audit_row_hash ON audit_log(row_hash)
    """),

    # v56 — Transactional outbox.
    # Every governed write emits an outbox event in the same SQLite transaction
    # as the domain change and the audit row.  A lightweight dispatcher marks
    # events dispatched_at once they have been forwarded (e.g. to SSE or a
    # background queue).
    (56, "Add transactional outbox table", """
        CREATE TABLE IF NOT EXISTS outbox (
            id            TEXT PRIMARY KEY,
            event_type    TEXT NOT NULL,
            object_id     TEXT,
            object_type   TEXT,
            payload       TEXT NOT NULL DEFAULT '{}',
            created_at    TEXT NOT NULL,
            dispatched_at TEXT
        );
        CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(created_at)
            WHERE dispatched_at IS NULL;
        CREATE INDEX IF NOT EXISTS outbox_created ON outbox(created_at)
    """),

    # v57 — Optimistic-concurrency version columns on key aggregates.
    # conversations, messages, documents, and knowledge all gain a version
    # integer (DEFAULT 1).  Works already have a version column via the
    # objects table.  Callers that want optimistic concurrency pass
    # expected_version; db helpers raise VersionConflictError on mismatch.
    (57, "Add version column to conversations, messages, documents, knowledge", """
        ALTER TABLE conversations ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE messages      ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE documents     ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE knowledge     ADD COLUMN version INTEGER NOT NULL DEFAULT 1
    """),

    # v58 — Governance findings (M0.2 Sovereign Platform).
    # A finding is a blocker that prevents forward state-machine transitions
    # on an object until a human resolves it.  Severity determines whether the
    # finding actually blocks: high/critical block all forward transitions;
    # warning/info are advisory only.
    (58, "Add findings table for governance blockers (M0.2)", """
        CREATE TABLE IF NOT EXISTS findings (
            id           TEXT PRIMARY KEY,
            object_id    TEXT NOT NULL,
            object_type  TEXT NOT NULL DEFAULT 'unknown',
            kind         TEXT NOT NULL DEFAULT 'issue',
            description  TEXT NOT NULL,
            severity     TEXT NOT NULL DEFAULT 'high',
            state        TEXT NOT NULL DEFAULT 'open',
            created_at   TEXT NOT NULL,
            resolved_at  TEXT,
            resolved_by  TEXT,
            meta         TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS findings_object_state
            ON findings(object_id, state);
        CREATE INDEX IF NOT EXISTS findings_open
            ON findings(state)
            WHERE state = 'open'
    """),

    # v59 — Add state column to messages for MessageState lifecycle (M0.2).
    # Messages start as 'done' (retroactively) since all existing rows are
    # already complete.  New messages created via the pipeline will be written
    # with the correct initial state ('queued' or 'done' as appropriate).
    (59, "Add state column to messages for MessageState lifecycle (M0.2)", """
        ALTER TABLE messages ADD COLUMN state TEXT NOT NULL DEFAULT 'done'
    """),

    # v60 — PKLOS Layer 0: claim ledger + capture stamps.
    #
    # VER-INV-001: No claim is presented above the authority its evidence supports.
    #
    # claims        — canonical claim record (subject / predicate / value)
    # claim_evidence — evidence items attached to a claim
    # claim_transitions — audit ledger for claim status changes
    # capture_stamps — boundary provenance log (every factual input stamped on entry)
    # claims_fts    — FTS5 virtual table for fast context retrieval
    (60, "PKLOS Layer 0: claim ledger and capture stamps (VER-INV-001) — see v61 for enhanced fields", """
        CREATE TABLE IF NOT EXISTS claims (
            id             TEXT PRIMARY KEY,
            subject        TEXT NOT NULL,
            predicate      TEXT NOT NULL,
            value          TEXT NOT NULL,
            unit           TEXT,
            authority_tier TEXT NOT NULL DEFAULT 'A7',
            source_id      TEXT,
            status         TEXT NOT NULL DEFAULT 'CURRENT',
            confidence     REAL NOT NULL DEFAULT 1.0,
            ttl_class      TEXT NOT NULL DEFAULT 'DURABLE',
            conv_id        TEXT,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL,
            meta           TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS claims_subject_pred
            ON claims(subject, predicate);
        CREATE INDEX IF NOT EXISTS claims_status
            ON claims(status);
        CREATE INDEX IF NOT EXISTS claims_updated
            ON claims(updated_at DESC);

        CREATE TABLE IF NOT EXISTS claim_evidence (
            id            TEXT PRIMARY KEY,
            claim_id      TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            evidence_type TEXT NOT NULL DEFAULT 'assertion',
            content       TEXT NOT NULL,
            source_id     TEXT,
            created_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ce_claim ON claim_evidence(claim_id);

        CREATE TABLE IF NOT EXISTS claim_transitions (
            id          TEXT PRIMARY KEY,
            claim_id    TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            from_status TEXT NOT NULL,
            to_status   TEXT NOT NULL,
            actor       TEXT NOT NULL DEFAULT 'system',
            reason      TEXT,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ct_claim ON claim_transitions(claim_id);

        CREATE TABLE IF NOT EXISTS capture_stamps (
            id          TEXT PRIMARY KEY,
            channel     TEXT NOT NULL DEFAULT 'chat',
            source_type TEXT NOT NULL DEFAULT 'A7',
            claim_id    TEXT REFERENCES claims(id) ON DELETE SET NULL,
            raw_text    TEXT,
            created_at  TEXT NOT NULL,
            meta        TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS cs_channel ON capture_stamps(channel);
        CREATE INDEX IF NOT EXISTS cs_created ON capture_stamps(created_at DESC);

        CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts
            USING fts5(
                claim_id UNINDEXED,
                subject,
                predicate,
                value,
                content='claims',
                content_rowid='rowid'
            )
    """),

    # v61 — PKLOS Layer 0: enhance claims table with full spec §3.2 canonical fields.
    # Spec canonical claim record fields: normalized_display_value, confidence_basis,
    # observed_at, valid_from, valid_until, verification_rule, supersedes,
    # contract_version, producer.
    # Also adds a 'verification_status' column (pending/verified/failed) separate
    # from 'status' so the state machine and verification lifecycle are distinct.
    (61, "PKLOS Layer 0: enhance claims with canonical spec fields (§3.2)", """
        ALTER TABLE claims ADD COLUMN normalized_display_value TEXT;
        ALTER TABLE claims ADD COLUMN confidence_basis TEXT;
        ALTER TABLE claims ADD COLUMN observed_at TEXT;
        ALTER TABLE claims ADD COLUMN valid_from TEXT;
        ALTER TABLE claims ADD COLUMN valid_until TEXT;
        ALTER TABLE claims ADD COLUMN verification_rule TEXT;
        ALTER TABLE claims ADD COLUMN supersedes TEXT;
        ALTER TABLE claims ADD COLUMN contract_version TEXT NOT NULL DEFAULT '1.0.0';
        ALTER TABLE claims ADD COLUMN producer TEXT
    """),

    (62, "Data-tier classifier: tier column on documents", """
        ALTER TABLE documents ADD COLUMN tier TEXT NOT NULL DEFAULT 'source';
        CREATE INDEX IF NOT EXISTS docs_tier ON documents(tier);
    """),

    # v63 — Pipeline artifacts: per-stage AI outputs for the B0-B17 book pipeline
    (63, "Pipeline artifacts table for book stage AI outputs", """
        CREATE TABLE IF NOT EXISTS pipeline_artifacts (
            id            TEXT PRIMARY KEY,
            pipeline_id   TEXT NOT NULL REFERENCES book_pipelines(id) ON DELETE CASCADE,
            stage         TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            content       TEXT NOT NULL DEFAULT '{}',
            status        TEXT NOT NULL DEFAULT 'pending',
            error         TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            UNIQUE(pipeline_id, stage)
        );
        CREATE INDEX IF NOT EXISTS pa_pipeline ON pipeline_artifacts(pipeline_id);
        CREATE INDEX IF NOT EXISTS pa_stage    ON pipeline_artifacts(stage)
    """),

    # v64 — Brainstorm sessions: divergent thinking engine outputs per Work
    (64, "Brainstorm sessions for divergent thinking engine", """
        CREATE TABLE IF NOT EXISTS brainstorm_sessions (
            id            TEXT PRIMARY KEY,
            work_id       TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            seed_prompt   TEXT NOT NULL,
            context_type  TEXT NOT NULL DEFAULT 'general',
            status        TEXT NOT NULL DEFAULT 'running',
            ideas         TEXT NOT NULL DEFAULT '[]',
            domain_count  INTEGER NOT NULL DEFAULT 5,
            created_at    TEXT NOT NULL,
            completed_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS brs_work   ON brainstorm_sessions(work_id);
        CREATE INDEX IF NOT EXISTS brs_status ON brainstorm_sessions(status);
        CREATE INDEX IF NOT EXISTS brs_created ON brainstorm_sessions(created_at)
    """),

    # v65 — Memory v2: conversation chunks for semantic recall + temporal versioning
    # of user_memory facts so old values are preserved with a superseded_at timestamp.
    (65, "Memory v2: conversation_chunks table + temporal user_memory versioning", """
        CREATE TABLE IF NOT EXISTS conversation_chunks (
            id         TEXT PRIMARY KEY,
            conv_id    TEXT NOT NULL,
            text       TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cc_conv    ON conversation_chunks(conv_id);
        CREATE INDEX IF NOT EXISTS idx_cc_created ON conversation_chunks(created_at DESC);
        ALTER TABLE user_memory ADD COLUMN superseded_at TEXT;
        ALTER TABLE user_memory ADD COLUMN prev_value TEXT
    """),

    # v66.5 — Proactive custodian: work staleness nudges
    (67, "Proactive custodian: work staleness nudges table", """
        CREATE TABLE IF NOT EXISTS work_nudges (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'stalled',
            message     TEXT NOT NULL,
            stage       TEXT,
            days_stalled INTEGER,
            priority    INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS wn_work ON work_nudges(work_id);
        CREATE INDEX IF NOT EXISTS wn_priority ON work_nudges(priority DESC, created_at DESC);
        CREATE INDEX IF NOT EXISTS wn_resolved ON work_nudges(resolved_at)
    """),

    # v69 — Actions layer: typed, auditable action runs
    (69, "Action runs ledger for the general action framework", """
        CREATE TABLE IF NOT EXISTS action_runs (
            id           TEXT PRIMARY KEY,
            action_name  TEXT NOT NULL,
            inputs       TEXT NOT NULL DEFAULT '{}',
            status       TEXT NOT NULL DEFAULT 'running',
            output_path  TEXT,
            output_label TEXT,
            output_doc_id TEXT,
            work_id      TEXT,
            error        TEXT,
            created_at   TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS ar_action ON action_runs(action_name, created_at DESC);
        CREATE INDEX IF NOT EXISTS ar_work   ON action_runs(work_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS ar_status ON action_runs(status)
    """),

    # v68 — Proactive custodian: distinguish user-dismissed from auto-resolved nudges
    (68, "Custodian nudges: user_dismissed flag to honour explicit dismissal across nightly passes", """
        ALTER TABLE work_nudges ADD COLUMN user_dismissed INTEGER NOT NULL DEFAULT 0
    """),

    # v66 — Cross-document links + topic area profiles
    (66, "Cross-document similarity links and topic area profiles", """
        CREATE TABLE IF NOT EXISTS doc_links (
            id          TEXT PRIMARY KEY,
            doc_a_id    TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            doc_b_id    TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            similarity  REAL NOT NULL,
            link_type   TEXT NOT NULL DEFAULT 'semantic',
            created_at  TEXT NOT NULL,
            UNIQUE(doc_a_id, doc_b_id)
        );
        CREATE INDEX IF NOT EXISTS dl_doc_a ON doc_links(doc_a_id);
        CREATE INDEX IF NOT EXISTS dl_doc_b ON doc_links(doc_b_id);
        CREATE INDEX IF NOT EXISTS dl_sim   ON doc_links(similarity DESC);
        CREATE TABLE IF NOT EXISTS topic_profiles (
            topic_id     TEXT PRIMARY KEY REFERENCES topics(id) ON DELETE CASCADE,
            what_it_is   TEXT NOT NULL DEFAULT '',
            purpose      TEXT NOT NULL DEFAULT '',
            connected    TEXT NOT NULL DEFAULT '[]',
            gaps         TEXT NOT NULL DEFAULT '[]',
            generated_at TEXT NOT NULL
        )
    """),

    # v70 — Save/Process/Recall invariant: object provenance ledger.
    # Every registered object (generated doc, TTS clip, image, research note) gets
    # a row here so recall queries ("find the report I made about X") can filter by
    # source, work, or origin conversation.
    # Declaration placed AFTER all lower-numbered migrations to preserve monotonic
    # ordering guarantees for both fresh and upgrade paths.
    (70, "Save/process/recall invariant: object_provenance ledger", """
        CREATE TABLE IF NOT EXISTS object_provenance (
            id         TEXT PRIMARY KEY,
            object_id  TEXT NOT NULL,
            source     TEXT NOT NULL,
            origin_id  TEXT,
            work_id    TEXT,
            topic_id   TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS op_object  ON object_provenance(object_id);
        CREATE INDEX IF NOT EXISTS op_source  ON object_provenance(source, created_at DESC);
        CREATE INDEX IF NOT EXISTS op_work    ON object_provenance(work_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS op_origin  ON object_provenance(origin_id)
    """),

    # v71 — Structured API access log.
    # Records every HTTP request so production issues can be investigated
    # without requiring real-time stdout log tailing.  Kept lightweight:
    # writes happen asynchronously via the background executor so hot paths
    # (chat streaming, library list) are never blocked.
    (71, "Structured API access log", """
        CREATE TABLE IF NOT EXISTS access_log (
            id         TEXT PRIMARY KEY,
            ts         TEXT NOT NULL,
            method     TEXT NOT NULL,
            path       TEXT NOT NULL,
            status     INTEGER NOT NULL,
            latency_ms INTEGER NOT NULL,
            ip         TEXT,
            user_agent TEXT,
            user_id    TEXT
        );
        CREATE INDEX IF NOT EXISTS al_ts   ON access_log(ts DESC);
        CREATE INDEX IF NOT EXISTS al_path ON access_log(path, ts DESC)
    """),

    # v72 — FTS5 virtual table for cross-conversation message search.
    # Enables fast full-text search across all message bodies via
    # GET /api/conversations/search?q=.  The table is kept in sync by
    # add_message(), finalize_message(), and sync_message_fts() (for
    # the continuation handlers that update messages.text directly).
    # No triggers are used because the migration runner splits SQL on ";" which
    # breaks CREATE TRIGGER … BEGIN … END blocks.  All sync is done in Python.
    (72, "FTS5 index on messages for cross-conversation search", """
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
            USING fts5(text, role UNINDEXED, msg_id UNINDEXED, conversation_id UNINDEXED)
    """),

    # v73 — Clear messages_fts before back-fill so retries are safe.
    # If v73 ran but crashed before updating schema_version, a retry would
    # re-execute v74 (the INSERT) on a partially-filled table, creating
    # duplicates.  Clearing first makes the whole backfill restart-safe.
    (73, "Clear messages_fts before back-fill (idempotent retry guard)", """
        DELETE FROM messages_fts
    """),

    # v74 — Back-fill messages_fts for databases that existed before v72.
    # Executed after the clear (v73) so this is always a clean insert.
    (74, "Back-fill messages_fts from existing messages rows", """
        INSERT INTO messages_fts(text, role, msg_id, conversation_id)
            SELECT text, role, id, conversation_id FROM messages
    """),
]

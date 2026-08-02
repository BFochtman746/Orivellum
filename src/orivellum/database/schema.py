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
]

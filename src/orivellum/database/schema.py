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
    (
        1,
        "Governed objects root table and settings",
        """
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
    """,
    ),
    # v2 — Works
    (
        2,
        "Works table",
        """
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
    """,
    ),
    # v3 — Relationships (graph edges)
    (
        3,
        "Relationships graph edges",
        """
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
    """,
    ),
    # v4 — Audit log
    (
        4,
        "Audit log",
        """
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
    """,
    ),
    # v5 — Documents (Library)
    (
        5,
        "Documents and chunks",
        """
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
    """,
    ),
    # v6 — Knowledge objects
    (
        6,
        "Knowledge objects",
        """
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
    """,
    ),
    # v7 — Tasks
    (
        7,
        "Tasks",
        """
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
    """,
    ),
    # v8 — Conversations and Messages
    (
        8,
        "Conversations and messages",
        """
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
    """,
    ),
    # v9 — Persistent jobs
    (
        9,
        "Persistent jobs",
        """
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
    """,
    ),
    # v10 — Publications
    (
        10,
        "Publications",
        """
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
    """,
    ),
    # v11 — Knowledge text_hash dedup
    (
        11,
        "Knowledge text_hash dedup",
        """
        ALTER TABLE knowledge ADD COLUMN text_hash TEXT;
        CREATE INDEX IF NOT EXISTS knowledge_hash ON knowledge(text_hash);
    """,
    ),
    # v12 — Book pipelines
    (
        12,
        "Book pipelines",
        """
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
    """,
    ),
    # v13 — Book chapters
    (
        13,
        "Book chapters",
        """
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
    """,
    ),
    # v14 — Document extracted_text column
    (
        14,
        "Document extracted_text column",
        """
        ALTER TABLE documents ADD COLUMN extracted_text TEXT;
    """,
    ),
    # v15 — Document classification_source column
    (
        15,
        "Document classification_source column",
        """
        ALTER TABLE documents ADD COLUMN classification_source TEXT;
    """,
    ),
    # v16 — Suggestions table
    (
        16,
        "Suggestions table",
        """
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
    """,
    ),
    # v17 — Composite indexes for work_summary counts
    (
        17,
        "Composite indexes for work_summary counts",
        """
        CREATE INDEX IF NOT EXISTS docs_work_readiness ON documents(work_id, readiness);
        CREATE INDEX IF NOT EXISTS tasks_work_status   ON tasks(work_id, status);
        CREATE INDEX IF NOT EXISTS knowledge_work_kind ON knowledge(work_id, kind);
    """,
    ),
    # v18 — Daily writing stats
    (
        18,
        "Daily writing stats",
        """
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
    """,
    ),
    # v19 — Graph layout per Work
    (
        19,
        "Graph layout per Work",
        """
        CREATE TABLE IF NOT EXISTS graph_layouts (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            layout_data TEXT NOT NULL DEFAULT '{}',
            updated_at  TEXT NOT NULL,
            UNIQUE(work_id)
        );
    """,
    ),
    # v20 — Long-term memories
    (
        20,
        "Long-term memories",
        """
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
    """,
    ),
    # v21 — Audit log compound index
    (
        21,
        "Audit log compound index for prune performance",
        """
        CREATE INDEX IF NOT EXISTS audit_obj_ts ON audit_log(object_id, timestamp);
    """,
    ),
    # v22 — Pending reclassification queue
    (
        22,
        "Pending reclassification queue",
        """
        CREATE TABLE IF NOT EXISTS pending_reclassify (
            id          TEXT PRIMARY KEY,
            doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            reason      TEXT,
            created_at  TEXT NOT NULL,
            UNIQUE(doc_id)
        );
    """,
    ),
    # v23 — Chapter citation provenance columns
    (
        23,
        "Chapter citation provenance columns",
        """
        ALTER TABLE book_chapters ADD COLUMN citation_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE book_chapters ADD COLUMN extraction_method TEXT;
    """,
    ),
    # v24 — Chapter attribution method column
    (
        24,
        "Chapter attribution method column",
        """
        ALTER TABLE book_chapters ADD COLUMN attribution_meta TEXT;
    """,
    ),
    # v25 — Module lessons table
    (
        25,
        "Module lessons table",
        """
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
    """,
    ),
    # v26 — Archived files table
    (
        26,
        "Archived files table",
        """
        CREATE TABLE IF NOT EXISTS archived_files (
            id          TEXT PRIMARY KEY,
            original_path TEXT NOT NULL,
            archive_path  TEXT NOT NULL,
            sha256        TEXT,
            reason        TEXT,
            archived_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS af_sha256 ON archived_files(sha256);
    """,
    ),
    # v27 — Compass project-navigator table
    (
        27,
        "Compass project-navigator table",
        """
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
    """,
    ),
    # v28 — Expectation events and feedback tables
    (
        28,
        "Expectation events and feedback tables",
        """
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
    """,
    ),
    # v29 — Store entity graph and local vectors
    (
        29,
        "Store entity graph and local vectors",
        """
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
    """,
    ),
    # v30 — BookVault legacy book tables
    (
        30,
        "BookVault book tables",
        """
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
    """,
    ),
    # v31 — Knowledge Fabric tables
    (
        31,
        "Knowledge Fabric tables",
        """
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
    """,
    ),
    # v32 — Extraction warnings table
    (
        32,
        "Extraction warnings table",
        """
        CREATE TABLE IF NOT EXISTS extraction_warnings (
            id          TEXT PRIMARY KEY,
            doc_id      TEXT REFERENCES documents(id) ON DELETE CASCADE,
            kind        TEXT NOT NULL,
            detail      TEXT,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ew_doc  ON extraction_warnings(doc_id);
        CREATE INDEX IF NOT EXISTS ew_kind ON extraction_warnings(kind);
    """,
    ),
    # v33 — FORGE learning table
    (
        33,
        "FORGE learning table",
        """
        CREATE TABLE IF NOT EXISTS forge_learning (
            id          TEXT PRIMARY KEY,
            work_id     TEXT REFERENCES works(id) ON DELETE CASCADE,
            chapter_id  TEXT,
            score_data  TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS fl_work ON forge_learning(work_id);
    """,
    ),
    # v34 — Conversations archived flag (additive column, idempotent via DDL)
    (
        34,
        "Conversations archived flag",
        """
        CREATE INDEX IF NOT EXISTS conv_work_arch ON conversations(work_id, archived);
    """,
    ),
    # v39 — Conversations model column
    (
        39,
        "Conversations model column",
        """
        ALTER TABLE conversations ADD COLUMN model TEXT;
    """,
    ),
    # v38 — Documents error_message column
    (
        38,
        "Documents error_message column",
        """
        ALTER TABLE documents ADD COLUMN error_message TEXT;
    """,
    ),
    # v35 — Documents word_count; Knowledge accessed_at
    (
        35,
        "Documents word_count; Knowledge accessed_at",
        """
        ALTER TABLE documents ADD COLUMN word_count INTEGER;
        ALTER TABLE knowledge ADD COLUMN accessed_at TEXT;
        CREATE INDEX IF NOT EXISTS knowledge_accessed ON knowledge(accessed_at);
    """,
    ),
    # v36 — Learning module: concept DAG + mastery tracking
    (
        36,
        "Learning module concept DAG and mastery tracking",
        """
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
    """,
    ),
    # v37 — Voice narration module: voice_profiles table
    (
        37,
        "Voice narration: voice_profiles table",
        """
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
    """,
    ),
    # v40 — User memory + nightshift tracking tables
    (
        40,
        "User memory and nightshift tracking",
        """
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
    """,
    ),
    # v41 — Project Compass per-Work state for cognition system
    (
        41,
        "Project Compass state table",
        """
        CREATE TABLE IF NOT EXISTS project_compass (
            work_id     TEXT PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
            focus       TEXT,
            last_reasoning TEXT,
            next_step   TEXT,
            updated_at  TEXT NOT NULL
        );
    """,
    ),
    # v42 — Adaptive learning: concept graph + mastery tracking
    # NOTE: v42 used CREATE TABLE IF NOT EXISTS which silently preserved a stale
    # Monarch schema (columns: name, mastery, etc.). v43 below drops and recreates.
    (
        42,
        "Adaptive learning tables (legacy — superseded by v43)",
        """
        SELECT 1
    """,
    ),
    # v43 — (historically ran DROP+CREATE on learning_concepts/mastery — now a no-op at DB level;
    #          v44 restores the Projects schema and adds the correct work_concepts tables)
    (43, "Adaptive learning tables — superseded by v44", "SELECT 1"),
    # v45 — Write Desk: rich-text document drafting workspace with AI assistance.
    (
        45,
        "Add write_documents table for Write Desk",
        """
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
    """,
    ),
    # v44 — Add Work-scoped adaptive learning tables.
    # Projects owns learning_concepts/learning_mastery — those are NEVER touched here.
    # This migration is purely additive: CREATE TABLE IF NOT EXISTS only.
    (
        44,
        "Add work_concepts and work_mastery for Work-scoped adaptive learning",
        """
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
    """,
    ),
    # v46 — Document version tracking (MONARCH #146)
    (
        46,
        "Document version snapshots and canonical flag",
        """
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
    """,
    ),
    # v47 — Chapter heading level (H1/H2/H3) on book_chapters (MONARCH #144)
    (
        47,
        "Add heading level column to book_chapters",
        """
        ALTER TABLE book_chapters ADD COLUMN level INTEGER NOT NULL DEFAULT 1
    """,
    ),
    # v48 — Document lifecycle activation (MONARCH #146)
    # Sets all existing document objects from 'active' → 'draft' so lifecycle
    # is meaningful for every document, new or old.
    (
        48,
        "Activate document lifecycle field — existing docs become draft",
        """
        UPDATE objects SET lifecycle='draft', updated_at=datetime('now')
        WHERE type='document' AND lifecycle='active'
    """,
    ),
    # v49 — Near-duplicate resolution tracking (MONARCH #147)
    # Adds resolved/resolution columns to doc_dupes so pairs can be dismissed
    # or acted upon without being re-detected.
    (
        49,
        "Add resolution columns to doc_dupes",
        """
        ALTER TABLE doc_dupes ADD COLUMN resolved INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE doc_dupes ADD COLUMN resolution TEXT;
        CREATE INDEX IF NOT EXISTS dd_resolved ON doc_dupes(resolved)
    """,
    ),
    # v50 — Gap detection cache (MONARCH #172)
    # Stores the most-recent gap detection result per Work so the dashboard
    # /gaps/top endpoint can return instantly without re-running detection on
    # every request.  Each row is updated in-place on every detection run;
    # rows older than 1 h are considered stale and trigger re-detection.
    (
        50,
        "Add work_gap_cache table for dashboard performance",
        """
        CREATE TABLE IF NOT EXISTS work_gap_cache (
            work_id      TEXT PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
            gaps_json    TEXT NOT NULL DEFAULT '[]',
            coverage_pct REAL,
            evaluated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS wgc_evaluated ON work_gap_cache(evaluated_at)
    """,
    ),
    # v51 — LLM call telemetry (MCOS Phase 0)
    # Every non-streaming chat-completion call routed through the central
    # gateway (capabilities/llm.py) records one row here: purpose label,
    # model, latency and token usage.  Powers the MCOS telemetry dashboard
    # and cost/latency trend analysis.
    (
        51,
        "Add llm_calls telemetry table",
        """
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
    """,
    ),
    # v52 — MCOS benchmark repository + evaluation runs (MCOS Phase 1)
    # benchmarks: versioned suites; benchmark_cases: golden cases with
    # expected concepts + scoring rules; eval_runs/eval_results: every
    # execution with per-case scores so regressions are detectable.
    (
        52,
        "Add MCOS benchmark and evaluation tables",
        """
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
    """,
    ),
    # v53 — MCOS Phase 4/5: prompt registry + RAG calibration sweeps.
    # prompts: versioned system-prompt candidates per slot; exactly one active
    # per slot (enforced by activation transaction + partial unique index).
    # rag_sweeps: in-memory chunking grid-search runs (no chunk-table writes).
    (
        53,
        "Add MCOS prompt registry and RAG sweep tables",
        """
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
    """,
    ),
    # v54 — Governance review queue deferrals (MONARCH #151)
    # A deferred review item is snoozed: excluded from /api/review/queue until
    # deferred_until passes. item_key is "<type>:<row id>" (e.g. "knowledge:abc").
    (
        54,
        "Add review_deferrals table for the governance review queue",
        """
        CREATE TABLE IF NOT EXISTS review_deferrals (
            item_key       TEXT PRIMARY KEY,
            deferred_until TEXT NOT NULL,
            reason         TEXT,
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS rd_until ON review_deferrals(deferred_until)
    """,
    ),
    # ── Sovereign Platform M0.1 — governed data foundation ──────────────────
    # v55 — Hash-chain columns on audit_log.
    # Every new audit row stores the previous row's row_hash (prev_hash) and
    # its own hash (row_hash = sha256(prev_hash | operation | object_id |
    # detail | timestamp | id)).  Rows written before v55 have NULL hashes and
    # are skipped by verify_audit_chain().
    (
        55,
        "Add hash-chain columns to audit_log (prev_hash, row_hash)",
        """
        ALTER TABLE audit_log ADD COLUMN prev_hash TEXT;
        ALTER TABLE audit_log ADD COLUMN row_hash  TEXT;
        CREATE INDEX IF NOT EXISTS audit_row_hash ON audit_log(row_hash)
    """,
    ),
    # v56 — Transactional outbox.
    # Every governed write emits an outbox event in the same SQLite transaction
    # as the domain change and the audit row.  A lightweight dispatcher marks
    # events dispatched_at once they have been forwarded (e.g. to SSE or a
    # background queue).
    (
        56,
        "Add transactional outbox table",
        """
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
    """,
    ),
    # v57 — Optimistic-concurrency version columns on key aggregates.
    # conversations, messages, documents, and knowledge all gain a version
    # integer (DEFAULT 1).  Works already have a version column via the
    # objects table.  Callers that want optimistic concurrency pass
    # expected_version; db helpers raise VersionConflictError on mismatch.
    (
        57,
        "Add version column to conversations, messages, documents, knowledge",
        """
        ALTER TABLE conversations ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE messages      ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE documents     ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE knowledge     ADD COLUMN version INTEGER NOT NULL DEFAULT 1
    """,
    ),
    # v58 — Governance findings (M0.2 Sovereign Platform).
    # A finding is a blocker that prevents forward state-machine transitions
    # on an object until a human resolves it.  Severity determines whether the
    # finding actually blocks: high/critical block all forward transitions;
    # warning/info are advisory only.
    (
        58,
        "Add findings table for governance blockers (M0.2)",
        """
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
    """,
    ),
    # v59 — Add state column to messages for MessageState lifecycle (M0.2).
    # Messages start as 'done' (retroactively) since all existing rows are
    # already complete.  New messages created via the pipeline will be written
    # with the correct initial state ('queued' or 'done' as appropriate).
    (
        59,
        "Add state column to messages for MessageState lifecycle (M0.2)",
        """
        ALTER TABLE messages ADD COLUMN state TEXT NOT NULL DEFAULT 'done'
    """,
    ),
    # v60 — PKLOS Layer 0: claim ledger + capture stamps.
    #
    # VER-INV-001: No claim is presented above the authority its evidence supports.
    #
    # claims        — canonical claim record (subject / predicate / value)
    # claim_evidence — evidence items attached to a claim
    # claim_transitions — audit ledger for claim status changes
    # capture_stamps — boundary provenance log (every factual input stamped on entry)
    # claims_fts    — FTS5 virtual table for fast context retrieval
    (
        60,
        "PKLOS Layer 0: claim ledger and capture stamps (VER-INV-001) — see v61 for enhanced fields",
        """
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
    """,
    ),
    # v61 — PKLOS Layer 0: enhance claims table with full spec §3.2 canonical fields.
    # Spec canonical claim record fields: normalized_display_value, confidence_basis,
    # observed_at, valid_from, valid_until, verification_rule, supersedes,
    # contract_version, producer.
    # Also adds a 'verification_status' column (pending/verified/failed) separate
    # from 'status' so the state machine and verification lifecycle are distinct.
    (
        61,
        "PKLOS Layer 0: enhance claims with canonical spec fields (§3.2)",
        """
        ALTER TABLE claims ADD COLUMN normalized_display_value TEXT;
        ALTER TABLE claims ADD COLUMN confidence_basis TEXT;
        ALTER TABLE claims ADD COLUMN observed_at TEXT;
        ALTER TABLE claims ADD COLUMN valid_from TEXT;
        ALTER TABLE claims ADD COLUMN valid_until TEXT;
        ALTER TABLE claims ADD COLUMN verification_rule TEXT;
        ALTER TABLE claims ADD COLUMN supersedes TEXT;
        ALTER TABLE claims ADD COLUMN contract_version TEXT NOT NULL DEFAULT '1.0.0';
        ALTER TABLE claims ADD COLUMN producer TEXT
    """,
    ),
    (
        62,
        "Data-tier classifier: tier column on documents",
        """
        ALTER TABLE documents ADD COLUMN tier TEXT NOT NULL DEFAULT 'source';
        CREATE INDEX IF NOT EXISTS docs_tier ON documents(tier);
    """,
    ),
    # v63 — Pipeline artifacts: per-stage AI outputs for the B0-B17 book pipeline
    (
        63,
        "Pipeline artifacts table for book stage AI outputs",
        """
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
    """,
    ),
    # v64 — Brainstorm sessions: divergent thinking engine outputs per Work
    (
        64,
        "Brainstorm sessions for divergent thinking engine",
        """
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
    """,
    ),
    # v65 — Memory v2: conversation chunks for semantic recall + temporal versioning
    # of user_memory facts so old values are preserved with a superseded_at timestamp.
    (
        65,
        "Memory v2: conversation_chunks table + temporal user_memory versioning",
        """
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
    """,
    ),
    # v66.5 — Proactive custodian: work staleness nudges
    (
        67,
        "Proactive custodian: work staleness nudges table",
        """
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
    """,
    ),
    # v69 — Actions layer: typed, auditable action runs
    (
        69,
        "Action runs ledger for the general action framework",
        """
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
    """,
    ),
    # v68 — Proactive custodian: distinguish user-dismissed from auto-resolved nudges
    (
        68,
        "Custodian nudges: user_dismissed flag to honour explicit dismissal across nightly passes",
        """
        ALTER TABLE work_nudges ADD COLUMN user_dismissed INTEGER NOT NULL DEFAULT 0
    """,
    ),
    # v66 — Cross-document links + topic area profiles
    (
        66,
        "Cross-document similarity links and topic area profiles",
        """
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
    """,
    ),
    # v70 — Save/Process/Recall invariant: object provenance ledger.
    # Every registered object (generated doc, TTS clip, image, research note) gets
    # a row here so recall queries ("find the report I made about X") can filter by
    # source, work, or origin conversation.
    # Declaration placed AFTER all lower-numbered migrations to preserve monotonic
    # ordering guarantees for both fresh and upgrade paths.
    (
        70,
        "Save/process/recall invariant: object_provenance ledger",
        """
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
    """,
    ),
    # v71 — Structured API access log.
    # Records every HTTP request so production issues can be investigated
    # without requiring real-time stdout log tailing.  Kept lightweight:
    # writes happen asynchronously via the background executor so hot paths
    # (chat streaming, library list) are never blocked.
    (
        71,
        "Structured API access log",
        """
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
    """,
    ),
    # v72 — FTS5 virtual table for cross-conversation message search.
    # Enables fast full-text search across all message bodies via
    # GET /api/conversations/search?q=.  The table is kept in sync by
    # add_message(), finalize_message(), and sync_message_fts() (for
    # the continuation handlers that update messages.text directly).
    # No triggers are used because the migration runner splits SQL on ";" which
    # breaks CREATE TRIGGER … BEGIN … END blocks.  All sync is done in Python.
    (
        72,
        "FTS5 index on messages for cross-conversation search",
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
            USING fts5(text, role UNINDEXED, msg_id UNINDEXED, conversation_id UNINDEXED)
    """,
    ),
    # v73 — Clear messages_fts before back-fill so retries are safe.
    # If v73 ran but crashed before updating schema_version, a retry would
    # re-execute v74 (the INSERT) on a partially-filled table, creating
    # duplicates.  Clearing first makes the whole backfill restart-safe.
    (
        73,
        "Clear messages_fts before back-fill (idempotent retry guard)",
        """
        DELETE FROM messages_fts
    """,
    ),
    # v74 — Back-fill messages_fts for databases that existed before v72.
    # Executed after the clear (v73) so this is always a clean insert.
    (
        74,
        "Back-fill messages_fts from existing messages rows",
        """
        INSERT INTO messages_fts(text, role, msg_id, conversation_id)
            SELECT text, role, id, conversation_id FROM messages
    """,
    ),
    # v75 — Explicit named work_id index on graph_layouts.
    #
    # graph_layouts (v19) has UNIQUE(work_id) which creates an implicit B-tree
    # index usable by the query planner, but no named index.  Adding gl_work
    # makes it visible to EXPLAIN QUERY PLAN and index-maintenance tooling.
    #
    # The three companion statements (ds_work, mem_work, fl_work) already exist
    # from their originating migrations (v18, v20, and forge_learning's migration
    # respectively).  They are repeated here as IF NOT EXISTS no-ops to document
    # the complete set of work_id FK indexes in one place, and to cover any edge
    # case where a partial migration left a gap.  All four are fully idempotent.
    (
        75,
        "Named work_id index on graph_layouts (+ idempotent guards for daily_stats, memories, forge_learning)",
        """
        CREATE INDEX IF NOT EXISTS gl_work  ON graph_layouts(work_id);
        CREATE INDEX IF NOT EXISTS ds_work  ON daily_stats(work_id);
        CREATE INDEX IF NOT EXISTS mem_work ON memories(work_id);
        CREATE INDEX IF NOT EXISTS fl_work  ON forge_learning(work_id)
    """,
    ),
    # v76 — Custom extraction templates: per-kind / per-work LLM prompts
    (
        76,
        "Extraction templates: per-kind/per-work custom LLM harvest prompts",
        """
        CREATE TABLE IF NOT EXISTS extraction_templates (
            id            TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            kind_label    TEXT,
            system_prompt TEXT NOT NULL,
            field_hints   TEXT NOT NULL DEFAULT '[]',
            work_id       TEXT REFERENCES works(id) ON DELETE CASCADE,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS et_kind    ON extraction_templates(kind_label);
        CREATE INDEX IF NOT EXISTS et_work    ON extraction_templates(work_id);
        CREATE INDEX IF NOT EXISTS et_kind_work ON extraction_templates(kind_label, work_id);
    """,
    ),
    # v77 — Web search enabled flag per conversation
    (
        77,
        "Add web_search_enabled column to conversations",
        """
        ALTER TABLE conversations ADD COLUMN web_search_enabled INTEGER NOT NULL DEFAULT 0;
    """,
    ),
    # v78 — Voice sample cache: paths to pre-generated sample audio per voice
    (
        78,
        "Voice sample cache table",
        """
        CREATE TABLE IF NOT EXISTS voice_samples (
            voice_id    TEXT PRIMARY KEY,
            sample_path TEXT NOT NULL,
            engine      TEXT NOT NULL DEFAULT 'kokoro',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
    """,
    ),
    # v79 — Contextual chunking: short AI-generated context sentence per chunk.
    #
    # Implements the Anthropic "Contextual Retrieval" technique: before embedding
    # each chunk we prepend a 1-2 sentence context prefix that names the document
    # and the broader topic the passage belongs to.  The prefix is stored here
    # so it can be (a) used when re-embedding existing chunks and (b) surfaced in
    # the system prompt alongside the raw chunk text.
    #
    # NULL means "not yet generated" — the nightshift backfill pass fills these in
    # for documents that existed before this migration.
    (
        79,
        "Add context_prefix column to chunks for contextual retrieval",
        """
        ALTER TABLE chunks ADD COLUMN context_prefix TEXT
    """,
    ),
    # v80 — Expo push notification tokens from mobile clients.
    #
    # Each row stores one device token.  A single user typically has at most one
    # device, but the table supports multiple tokens so tablets / device upgrades
    # are handled automatically — old tokens that Expo rejects are pruned by the
    # nightshift orphan pass (future work).
    (
        80,
        "Push notification tokens table",
        """
        CREATE TABLE IF NOT EXISTS push_tokens (
            id         TEXT PRIMARY KEY,
            token      TEXT NOT NULL UNIQUE,
            platform   TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS push_tokens_token ON push_tokens(token);
    """,
    ),
    # v81 — Scope push tokens to the authenticated identity that registered them.
    #
    # key_hash is the SHA-256 hex digest of the API key used when the device
    # called POST /api/users/push-token.  Server-side notification senders filter
    # by this hash so that — in any deployment with multiple API keys — a device
    # only receives events for resources owned by the same identity.  NULL rows
    # (pre-migration tokens) are treated as "owner unknown" and targeted only
    # when no key_hash filter is supplied.
    (
        81,
        "Add key_hash ownership column to push_tokens",
        """
        ALTER TABLE push_tokens ADD COLUMN key_hash TEXT;
        CREATE INDEX IF NOT EXISTS push_tokens_key_hash ON push_tokens(key_hash);
    """,
    ),
    # v82 — Late-chunking metadata columns on the chunks table.
    #
    # embedding_method: "standard" (independent per-chunk embedding) or "late"
    #   (full-document token pooling per Jina AI late chunking, 2024).  NULL
    #   means the chunk was stored before this migration and has not been
    #   re-embedded yet; the nightly backfill does not change the method for
    #   existing vectors.
    #
    # char_start / char_end: Unicode code-point offsets of this chunk within
    #   documents.extracted_text.  Python string indices and slices always
    #   count code-points (not UTF-8 bytes), so these are code-point offsets.
    #
    #   Offsets are bounded by the extracted_text persistence cap
    #   (_EXTRACTED_TEXT_CAP = 100_000 code-points) set in pipeline.py.
    #   Chunks beyond the cap have char_start = char_end = NULL so the
    #   late-chunking encoder skips them and the standard per-chunk path
    #   handles them instead.  This guarantees that non-NULL offsets always
    #   refer to valid positions within documents.extracted_text.
    #
    #   NULL for chunks created before this migration.
    (
        82,
        "Add embedding_method and char offsets to chunks",
        """
        ALTER TABLE chunks ADD COLUMN embedding_method TEXT;
        ALTER TABLE chunks ADD COLUMN char_start INTEGER;
        ALTER TABLE chunks ADD COLUMN char_end INTEGER;
    """,
    ),
    (
        83,
        "Conversation events table for retrieval-strategy and other diagnostic logs",
        """
        CREATE TABLE IF NOT EXISTS conversation_events (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_conv_events_conv
            ON conversation_events(conversation_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_conv_events_type
            ON conversation_events(event_type, created_at DESC)
    """,
    ),
    # v84 — chapter-scoped knowledge extraction for novels.
    # knowledge.chapter_id links every AI-extracted item to the specific
    # book chapter it was harvested from, enabling chapter-level search,
    # health dashboards, and chat scoping ("what happens in chapter 5?").
    (
        84,
        "Add chapter_id to knowledge for chapter-scoped novel extraction",
        """
        ALTER TABLE knowledge ADD COLUMN chapter_id TEXT REFERENCES book_chapters(id) ON DELETE SET NULL;
        CREATE INDEX IF NOT EXISTS idx_knowledge_chapter ON knowledge(chapter_id);
    """,
    ),
    # v85 — Persist suggested_queries in the gap cache so cached responses
    # have the same shape as fresh detection runs.  Default empty JSON array
    # keeps existing rows valid without a data migration.
    (
        85,
        "Add suggested_queries_json to work_gap_cache",
        """
        ALTER TABLE work_gap_cache ADD COLUMN suggested_queries_json TEXT NOT NULL DEFAULT '[]'
    """,
    ),
    # v86 — Client-supplied idempotency key for offline message delivery.
    # Mobile clients queue messages while offline and flush them on reconnect.
    # A stable client_msg_id prevents duplicate delivery when the server
    # processes the request but the client loses the response and retries.
    # UNIQUE per conversation: the same message re-sent to the same conversation
    # is suppressed; it can be legitimately resent to a different conversation.
    (
        86,
        "Add client_msg_id idempotency key to messages",
        """
        ALTER TABLE messages ADD COLUMN client_msg_id TEXT;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_client_msg_id
            ON messages(conversation_id, client_msg_id)
            WHERE client_msg_id IS NOT NULL
    """,
    ),
    # v87 — Generation claim lock for client_msg_id idempotency.
    # Tracks per-(conversation, client_msg_id) generation state so that
    # concurrent retries cannot both generate AI replies, and so the
    # assistant reply is linked directly to the originating request rather
    # than inferred by timestamp ordering.
    #
    # state:
    #   'processing' — generation is claimed; a request is generating
    #   'completed'  — generation finished; assistant_msg_id is set
    #
    # A 'processing' slot older than 10 minutes is considered abandoned
    # (server crashed mid-generation) and may be reclaimed.
    (
        87,
        "Add message_idempotency generation claim table",
        """
        CREATE TABLE IF NOT EXISTS message_idempotency (
            conversation_id  TEXT NOT NULL,
            client_msg_id    TEXT NOT NULL,
            state            TEXT NOT NULL DEFAULT 'processing',
            assistant_msg_id TEXT,
            created_at       TEXT NOT NULL,
            PRIMARY KEY (conversation_id, client_msg_id)
        )
    """,
    ),
    # v88 — Sliding-window context summarization.
    # Long conversations exceed the model context window; this column stores an
    # auto-generated rolling prose summary of the oldest exchanges.  The summary
    # is injected at the top of the system prompt so every reply benefits from
    # older context without paying the full token cost.
    (
        88,
        "Add context_summary column to conversations",
        """
        ALTER TABLE conversations ADD COLUMN context_summary TEXT
    """,
    ),
    # v89 — Coverage cursor for the sliding-window summarizer.
    # summary_cursor_id holds the DB id of the last message already folded into
    # context_summary.  On each background run the summarizer loads only the
    # messages AFTER this cursor that fall outside the verbatim history window,
    # ensuring every excluded exchange is captured exactly once rather than
    # repeatedly re-folding the same earliest batch.
    (
        89,
        "Add summary_cursor_id coverage cursor to conversations",
        """
        ALTER TABLE conversations ADD COLUMN summary_cursor_id TEXT
    """,
    ),
    # v90 — Conversation personas (Phase 2 personalization).
    # persona_id stores the built-in persona slug applied at conversation creation.
    # Empty string / NULL both mean the default (unmodified) assistant behavior.
    # Known values: 'default', 'story_partner', 'technical_editor',
    #               'research_assistant', 'devils_advocate'.
    (
        90,
        "Add persona_id to conversations for AI persona selection",
        """
        ALTER TABLE conversations ADD COLUMN persona_id TEXT NOT NULL DEFAULT 'default'
    """,
    ),
    # v91 — Trailer Architect production packages.
    # Each row represents one trailer generation job for a Work.
    # status: 'running' | 'ready' | 'blocked' | 'failed'
    # phase:  pipeline stage name for progress display
    #         ('loading' | 'analyze' | 'concept' | 'method' | 'plan' |
    #          'validate' | 'package' | 'done' | 'error')
    # package_json: full production package JSON (brief, concept, method, plan,
    #               validation, human-readable doc markdown, per-shot prompt strings)
    # error: last error message when status='failed', NULL otherwise
    (
        91,
        "Add trailers table for Trailer Architect production packages",
        """
        CREATE TABLE IF NOT EXISTS trailers (
            id           TEXT PRIMARY KEY,
            work_id      TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'running',
            phase        TEXT NOT NULL DEFAULT 'loading',
            package_json TEXT,
            error        TEXT,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            FOREIGN KEY (work_id) REFERENCES works(id)
        )
    """,
    ),
    # v92 — GENESIS Book Origination System.
    # genesis_books: one per Work (1:1 in practice; work_id UNIQUE).
    # genesis_stages: G0..G9 status per book (PENDING/PASSED/FAILED).
    # genesis_artifacts: markdown content per stage, stored in DB (not filesystem).
    # genesis_ledger: tamper-evident append-only hash chain per book.
    (
        92,
        "Add GENESIS book origination tables",
        """
        CREATE TABLE IF NOT EXISTS genesis_books (
            id            TEXT PRIMARY KEY,
            work_id       TEXT NOT NULL UNIQUE,
            mode          TEXT NOT NULL CHECK (mode IN ('cold','library')),
            length        INTEGER NOT NULL DEFAULT 80,
            acts          INTEGER NOT NULL DEFAULT 4,
            state         TEXT NOT NULL DEFAULT 'G0',
            manifest_json TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            FOREIGN KEY (work_id) REFERENCES works(id)
        );
        CREATE TABLE IF NOT EXISTS genesis_stages (
            id         TEXT PRIMARY KEY,
            book_id    TEXT NOT NULL,
            stage_code TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'PENDING'
                       CHECK (status IN ('PENDING','PASSED','FAILED')),
            UNIQUE (book_id, stage_code),
            FOREIGN KEY (book_id) REFERENCES genesis_books(id)
        );
        CREATE TABLE IF NOT EXISTS genesis_artifacts (
            id         TEXT PRIMARY KEY,
            book_id    TEXT NOT NULL,
            stage_code TEXT NOT NULL,
            content    TEXT NOT NULL DEFAULT '',
            sha256     TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE (book_id, stage_code),
            FOREIGN KEY (book_id) REFERENCES genesis_books(id)
        );
        CREATE TABLE IF NOT EXISTS genesis_ledger (
            id        TEXT PRIMARY KEY,
            book_id   TEXT NOT NULL,
            seq       INTEGER NOT NULL,
            kind      TEXT NOT NULL,
            payload   TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            hash      TEXT NOT NULL,
            at        TEXT NOT NULL,
            FOREIGN KEY (book_id) REFERENCES genesis_books(id)
        )
    """,
    ),
    # v93 — Spaced Repetition (HLR) columns on work_mastery.
    # Adds per-record fields needed by the Half-Life Regression student model:
    #   last_reviewed_at     — ISO-8601 timestamp of the session (backfilled from created_at)
    #   next_review_at       — predicted optimal next review time (now + half_life_days)
    #   half_life_days       — current predicted forgetting half-life in fractional days
    #   review_session_count — distinct calendar-day sessions (gate for durable mastery: ≥ 3)
    (
        93,
        "Add HLR spaced-repetition columns to work_mastery",
        """
        ALTER TABLE work_mastery ADD COLUMN last_reviewed_at TEXT;
        ALTER TABLE work_mastery ADD COLUMN next_review_at   TEXT;
        ALTER TABLE work_mastery ADD COLUMN half_life_days   REAL    NOT NULL DEFAULT 1.0;
        ALTER TABLE work_mastery ADD COLUMN review_session_count INTEGER NOT NULL DEFAULT 0;
        UPDATE work_mastery SET last_reviewed_at = created_at WHERE last_reviewed_at IS NULL;
        CREATE INDEX IF NOT EXISTS work_mastery_next_review ON work_mastery(next_review_at);
    """,
    ),
    # v94 — Many-to-many prerequisite graph for learning concepts.
    # Replaces the single work_concepts.prereq_id with a join table so a concept
    # can require multiple prerequisites.  The old column is preserved for
    # backward-compatibility but the new table is the authoritative source.
    # Backfill uses a same-Work JOIN so cross-Work prereq_id values are excluded.
    (
        94,
        "Add work_concept_prereqs join table for multi-prerequisite graph",
        """
        CREATE TABLE IF NOT EXISTS work_concept_prereqs (
            concept_id TEXT NOT NULL REFERENCES work_concepts(id) ON DELETE CASCADE,
            prereq_id  TEXT NOT NULL REFERENCES work_concepts(id) ON DELETE CASCADE,
            PRIMARY KEY (concept_id, prereq_id)
        );
        CREATE INDEX IF NOT EXISTS wcp_concept ON work_concept_prereqs(concept_id);
        CREATE INDEX IF NOT EXISTS wcp_prereq  ON work_concept_prereqs(prereq_id);
        INSERT OR IGNORE INTO work_concept_prereqs(concept_id, prereq_id)
            SELECT c.id, c.prereq_id
            FROM work_concepts c
            JOIN work_concepts p ON p.id = c.prereq_id AND p.work_id = c.work_id
            WHERE c.prereq_id IS NOT NULL;
    """,
    ),
    # v95 — Error classification columns on work_mastery.
    # Adds per-assessment diagnosis:
    #   error_type      — null (correct), careless_slip, procedural_gap,
    #                     conceptual_misconception, or knowledge_gap
    #   remediation_hint — 1-sentence targeted next-step suggestion from the LLM critic
    # These allow the UI to route wrong answers to the right remediation instead
    # of a generic "try again", and enable aggregate misconception tracking.
    (
        95,
        "Add error classification columns to work_mastery",
        """
        ALTER TABLE work_mastery ADD COLUMN error_type       TEXT;
        ALTER TABLE work_mastery ADD COLUMN remediation_hint TEXT;
        CREATE INDEX IF NOT EXISTS work_mastery_error_type ON work_mastery(concept_id, error_type);
    """,
    ),
    # v96 — Transfer-question tracking on work_mastery.
    # Stores which question mode (recall vs. transfer) was in effect for each attempt.
    # Enables analytics to compare recall vs. transfer performance and is used by
    # assess_answer to award 1.5× streak credit when a transfer question is answered
    # correctly (score ≥ 0.75), recognising that application questions are harder.
    (
        96,
        "Add question_type column to work_mastery",
        """
        ALTER TABLE work_mastery ADD COLUMN question_type TEXT NOT NULL DEFAULT 'recall';
        CREATE INDEX IF NOT EXISTS work_mastery_qtype ON work_mastery(concept_id, question_type);
    """,
    ),
    # v97 — Interleaved practice mode tracking on work_mastery.
    # Records which session mode (blocked vs. interleaved) each attempt was taken in,
    # enabling analytics to compare retention rates across practice strategies.
    # NOTE: must remain after v95 and v96 so that error_type, remediation_hint, and
    # question_type columns always exist before session_mode is added on upgrade paths.
    (
        97,
        "Add session_mode column to work_mastery",
        """
        ALTER TABLE work_mastery ADD COLUMN session_mode TEXT NOT NULL DEFAULT 'blocked';
        CREATE INDEX IF NOT EXISTS work_mastery_smode ON work_mastery(concept_id, session_mode);
    """,
    ),
    # v98 — Bi-temporal memory + five memory types on user_memory.
    # Replaces the single-row-per-key overwrite design with an append-only bi-temporal log:
    #   valid_from  — when the fact was true in the world (backfilled from created_at)
    #   valid_to    — when the fact was superseded or expired (NULL = still current)
    #   txn_time    — when the system first recorded it (backfilled from created_at)
    #   memory_type — episodic | semantic | procedural | working | zettelkasten
    # The UNIQUE index um_key is dropped — multiple rows per key are now allowed.
    # Existing rows migrate to memory_type='semantic' with valid_from = txn_time = created_at.
    (
        98,
        "Bi-temporal memory: valid_from/valid_to/txn_time + five memory types",
        """
        DROP INDEX IF EXISTS um_key;
        ALTER TABLE user_memory ADD COLUMN memory_type TEXT NOT NULL DEFAULT 'semantic';
        ALTER TABLE user_memory ADD COLUMN valid_from   TEXT;
        ALTER TABLE user_memory ADD COLUMN valid_to     TEXT;
        ALTER TABLE user_memory ADD COLUMN txn_time     TEXT;
        UPDATE user_memory SET
            valid_from = created_at,
            txn_time   = created_at
        WHERE valid_from IS NULL;
        CREATE INDEX IF NOT EXISTS um_key_current ON user_memory(key, valid_to);
        CREATE INDEX IF NOT EXISTS um_type        ON user_memory(memory_type);
        CREATE INDEX IF NOT EXISTS um_valid_from  ON user_memory(valid_from);
    """,
    ),
    # v99 — Evidence Before Belief: source evidence table + FK on user_memory.
    # Every memory fact must now have a traceable origin.  The capture pipeline
    # writes a memory_evidence row first (raw_text = the source passage that
    # triggered the inference), then writes the memory row with source_evidence_id
    # pointing back to it.  Existing facts keep source_evidence_id = NULL — they
    # are not broken, just untraced.  Future contradiction detection can join
    # conflicting memory rows back to their evidence to determine authority.
    (
        99,
        "Source evidence table for memory derivation (Evidence Before Belief)",
        """
        CREATE TABLE IF NOT EXISTS memory_evidence (
            id              TEXT PRIMARY KEY,
            raw_text        TEXT NOT NULL,
            source_type     TEXT NOT NULL DEFAULT 'conversation',
            source_id       TEXT,
            conversation_id TEXT,
            message_id      TEXT,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS me_source_type ON memory_evidence(source_type);
        CREATE INDEX IF NOT EXISTS me_conv        ON memory_evidence(conversation_id);
        ALTER TABLE user_memory ADD COLUMN source_evidence_id TEXT;
        CREATE INDEX IF NOT EXISTS um_evidence ON user_memory(source_evidence_id);
    """,
    ),
    # v100 — Memory conflict registry.
    # When the nightly dedup pass detects two memory rows that contradict each
    # other (same key or near-duplicate text but different values), it records
    # the pair here instead of silently discarding either row.  Resolution is
    # user-assisted: the UI shows unresolved conflicts and allows the user to
    # keep one side, keep both, or dismiss the conflict.
    #
    # Columns:
    #   memory_id_a / memory_id_b — the two conflicting user_memory row IDs
    #   detected_at — when the conflict was first detected (ISO-8601 UTC)
    #   resolved    — 0 = open, 1 = resolved
    #   resolution  — 'keep_a' | 'keep_b' | 'merged' | 'dismissed' (NULL until resolved)
    #   resolved_at — timestamp of resolution (NULL until resolved)
    #
    # The UNIQUE(memory_id_a, memory_id_b) constraint prevents duplicate pairs;
    # INSERT OR IGNORE is used so re-running the pass is idempotent.
    (
        100,
        "Memory conflict registry for nightly dedup + promote passes",
        """
        CREATE TABLE IF NOT EXISTS memory_conflicts (
            id           TEXT PRIMARY KEY,
            memory_id_a  TEXT NOT NULL,
            memory_id_b  TEXT NOT NULL,
            detected_at  TEXT NOT NULL,
            resolved     INTEGER NOT NULL DEFAULT 0,
            resolution   TEXT,
            resolved_at  TEXT,
            UNIQUE(memory_id_a, memory_id_b)
        );
        CREATE INDEX IF NOT EXISTS mc_resolved ON memory_conflicts(resolved);
        CREATE INDEX IF NOT EXISTS mc_detected ON memory_conflicts(detected_at);
        CREATE INDEX IF NOT EXISTS mc_mem_a    ON memory_conflicts(memory_id_a);
        CREATE INDEX IF NOT EXISTS mc_mem_b    ON memory_conflicts(memory_id_b);
    """,
    ),
    # v101 — user_memory FTS5 virtual table for lexical (BM25) recall.
    #
    # Both the CREATE and the backfill INSERT are included in this single
    # version so:
    #   • The schema_version counter advances only 100 → 101 (never to a
    #     4-digit value that would permanently skip future v102+ migrations).
    #   • No gap migration can land between table creation and backfill.
    #
    # The migration runner splits on ";" and runs each non-empty statement,
    # which handles the two-statement body correctly.  SQLite triggers cannot
    # be expressed here (BEGIN…END contains internal ";"), so FTS sync is
    # handled in Python by db._sync_memory_fts(), called from every memory
    # write path: upsert_memory_fact, update_memory_fact, and the nightly
    # promotion pass.
    (
        101,
        "user_memory FTS5 virtual table for BM25 lexical recall + backfill",
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS user_memory_fts
        USING fts5(key, value, memory_id UNINDEXED, tokenize='porter ascii');
        INSERT INTO user_memory_fts(rowid, key, value, memory_id)
        SELECT rowid, key, value, id FROM user_memory
    """,
    ),
    # v102 — Knowledge retrieval log for cold-item detection
    # Tracks every knowledge item that was actually injected into a chat context
    # (by _build_system_prompt).  Nightshift pass cold_item_detection uses this
    # to surface items that have never been retrieved, or not retrieved in the
    # last 60 days, as governance suggestions for user review.
    #
    # Design constraints:
    #   - Inserts are fire-and-forget (background thread) — never block chat.
    #   - ON DELETE CASCADE on both FKs: removing a conversation or knowledge
    #     item automatically prunes its retrieval rows.
    #   - Indexes on knowledge_id + retrieved_at support the nightshift GROUP BY
    #     query efficiently without a full table scan.
    (
        102,
        "Knowledge retrieval log for cold-item detection",
        """
        CREATE TABLE IF NOT EXISTS knowledge_retrievals (
            id           TEXT PRIMARY KEY,
            knowledge_id TEXT NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
            conv_id      TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            retrieved_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS kr_knowledge_id  ON knowledge_retrievals(knowledge_id);
        CREATE INDEX IF NOT EXISTS kr_conv_id       ON knowledge_retrievals(conv_id);
        CREATE INDEX IF NOT EXISTS kr_retrieved_at  ON knowledge_retrievals(retrieved_at);
    """,
    ),
    # ── Forge Website Factory ─────────────────────────────────────────────────
    # Integrates the Forge governed website-build pipeline into Orivellum as a
    # first-class capability.  Each ForgeProject produces one or more ForgeJobs
    # (PLAN → DESIGN → BUILD → VERIFY → REVIEW → RELEASE).  Events stream from
    # a dedicated table so SSE subscribers can tail from a cursor.
    (
        103,
        "Forge website factory — projects table",
        """
        CREATE TABLE IF NOT EXISTS forge_projects (
            id          TEXT PRIMARY KEY,
            work_id     TEXT REFERENCES works(id) ON DELETE SET NULL,
            name        TEXT NOT NULL,
            brief       TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'active',
            build_dir   TEXT,
            config      TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS fp_work_id   ON forge_projects(work_id);
        CREATE INDEX IF NOT EXISTS fp_status    ON forge_projects(status);
        CREATE INDEX IF NOT EXISTS fp_created   ON forge_projects(created_at);
    """,
    ),
    (
        104,
        "Forge website factory — jobs table",
        """
        CREATE TABLE IF NOT EXISTS forge_jobs (
            id             TEXT PRIMARY KEY,
            project_id     TEXT NOT NULL REFERENCES forge_projects(id) ON DELETE CASCADE,
            type           TEXT NOT NULL,
            status         TEXT NOT NULL DEFAULT 'pending',
            instruction    TEXT,
            plan_job_id    TEXT,
            design_job_id  TEXT,
            target_job_id  TEXT,
            build_dir      TEXT,
            created_at     TEXT NOT NULL,
            started_at     TEXT,
            completed_at   TEXT,
            meta           TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS fj_project_id ON forge_jobs(project_id);
        CREATE INDEX IF NOT EXISTS fj_status     ON forge_jobs(status);
        CREATE INDEX IF NOT EXISTS fj_type       ON forge_jobs(type);
        CREATE INDEX IF NOT EXISTS fj_created    ON forge_jobs(created_at);
    """,
    ),
    (
        105,
        "Forge website factory — events table",
        """
        CREATE TABLE IF NOT EXISTS forge_events (
            id         TEXT PRIMARY KEY,
            job_id     TEXT NOT NULL REFERENCES forge_jobs(id) ON DELETE CASCADE,
            phase      TEXT NOT NULL,
            message    TEXT NOT NULL,
            data_json  TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS fe_job_id     ON forge_events(job_id);
        CREATE INDEX IF NOT EXISTS fe_created_at ON forge_events(created_at);
    """,
    ),
    (
        106,
        "Forge website factory — artifacts table",
        """
        CREATE TABLE IF NOT EXISTS forge_artifacts (
            id            TEXT PRIMARY KEY,
            job_id        TEXT NOT NULL REFERENCES forge_jobs(id) ON DELETE CASCADE,
            artifact_type TEXT NOT NULL,
            content_json  TEXT NOT NULL,
            sha256        TEXT,
            created_at    TEXT NOT NULL,
            UNIQUE(job_id, artifact_type)
        );
        CREATE INDEX IF NOT EXISTS fa_job_id        ON forge_artifacts(job_id);
        CREATE INDEX IF NOT EXISTS fa_artifact_type ON forge_artifacts(artifact_type);
    """,
    ),
    (
        107,
        "A-01 Mail Steward tables",
        """
        CREATE TABLE IF NOT EXISTS mail_records (
            id                       TEXT PRIMARY KEY,
            graph_message_id_enc     TEXT NOT NULL,
            graph_message_id_hash    TEXT NOT NULL,
            graph_change_key_enc     TEXT NOT NULL DEFAULT '',
            graph_folder_id_enc      TEXT NOT NULL DEFAULT '',
            conversation_id          TEXT NOT NULL DEFAULT '',
            subject                  TEXT NOT NULL DEFAULT '',
            sender_name              TEXT NOT NULL DEFAULT '',
            sender_domain            TEXT NOT NULL DEFAULT '',
            received_at              TEXT NOT NULL DEFAULT '',
            has_attachments          INTEGER NOT NULL DEFAULT 0,
            attachment_count         INTEGER NOT NULL DEFAULT 0,
            importance               TEXT NOT NULL DEFAULT 'normal',
            is_read                  INTEGER NOT NULL DEFAULT 0,
            lifecycle_state          TEXT NOT NULL DEFAULT 'DISCOVERED',
            assessment_id            TEXT,
            action_request_id        TEXT,
            created_at               TEXT NOT NULL,
            updated_at               TEXT NOT NULL,
            meta                     TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS mr_hash      ON mail_records(graph_message_id_hash);
        CREATE INDEX IF NOT EXISTS mr_lifecycle ON mail_records(lifecycle_state);
        CREATE INDEX IF NOT EXISTS mr_received  ON mail_records(received_at);
        CREATE INDEX IF NOT EXISTS mr_domain    ON mail_records(sender_domain);

        CREATE TABLE IF NOT EXISTS mail_assessments (
            id                 TEXT PRIMARY KEY,
            mail_record_id     TEXT NOT NULL REFERENCES mail_records(id) ON DELETE CASCADE,
            attention_level    TEXT NOT NULL DEFAULT 'medium',
            needs_reply        INTEGER NOT NULL DEFAULT 0,
            rationale          TEXT NOT NULL DEFAULT '',
            suggested_reply    TEXT,
            recommended_action TEXT NOT NULL DEFAULT 'NONE',
            confidence         REAL NOT NULL DEFAULT 0.0,
            is_high_risk       INTEGER NOT NULL DEFAULT 0,
            injection_flagged  INTEGER NOT NULL DEFAULT 0,
            model_id           TEXT NOT NULL DEFAULT '',
            signals_json       TEXT NOT NULL DEFAULT '[]',
            created_at         TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ma_record_id ON mail_assessments(mail_record_id);
        CREATE INDEX IF NOT EXISTS ma_attention ON mail_assessments(attention_level);

        CREATE TABLE IF NOT EXISTS mail_action_requests (
            id                         TEXT PRIMARY KEY,
            mail_record_id             TEXT NOT NULL REFERENCES mail_records(id) ON DELETE CASCADE,
            assessment_id              TEXT REFERENCES mail_assessments(id),
            action_type                TEXT NOT NULL,
            destination_folder_id_enc  TEXT,
            graph_draft_id_enc         TEXT,
            nonce                      TEXT NOT NULL,
            status                     TEXT NOT NULL DEFAULT 'PENDING',
            result_message_id_enc      TEXT,
            original_folder_id_enc     TEXT,
            actor                      TEXT NOT NULL DEFAULT 'user',
            created_at                 TEXT NOT NULL,
            applied_at                 TEXT
        );
        CREATE INDEX IF NOT EXISTS mar_record   ON mail_action_requests(mail_record_id);
        CREATE INDEX IF NOT EXISTS mar_status   ON mail_action_requests(status);
        CREATE INDEX IF NOT EXISTS mar_nonce    ON mail_action_requests(nonce);

        CREATE TABLE IF NOT EXISTS mail_audit_events (
            id                TEXT PRIMARY KEY,
            mail_record_id    TEXT,
            action_request_id TEXT,
            at                TEXT NOT NULL,
            actor             TEXT NOT NULL DEFAULT 'system',
            event_type        TEXT NOT NULL,
            policy_version    TEXT NOT NULL DEFAULT '',
            model_id          TEXT NOT NULL DEFAULT '',
            signals_json      TEXT NOT NULL DEFAULT '[]',
            before_json       TEXT NOT NULL DEFAULT '{}',
            after_json        TEXT NOT NULL DEFAULT '{}',
            result            TEXT NOT NULL DEFAULT 'SUCCESS'
        );
        CREATE INDEX IF NOT EXISTS mae_record ON mail_audit_events(mail_record_id);
        CREATE INDEX IF NOT EXISTS mae_at     ON mail_audit_events(at);
        CREATE INDEX IF NOT EXISTS mae_type   ON mail_audit_events(event_type);

        CREATE TABLE IF NOT EXISTS mail_delta_links (
            folder_id   TEXT PRIMARY KEY,
            delta_link  TEXT,
            updated_at  TEXT NOT NULL
        );
    """,
    ),
    (
        108,
        "Add mail_context_enabled column to conversations",
        """
        ALTER TABLE conversations ADD COLUMN mail_context_enabled INTEGER NOT NULL DEFAULT 0;
    """,
    ),
    # v109 — Measurement layer: richer LLM telemetry, bench runs, golden queries.
    #   * llm_calls gains ttft_ms / tok_per_s / streamed so streaming paths can
    #     record time-to-first-token and decode rate (NULL when unknown — never
    #     guessed).
    #   * bench_runs stores one summary row per benchmark / eval run.
    #   * golden_queries is the curated retrieval golden set scored by
    #     capabilities/evalset.py (nDCG@k / Recall@k per channel).
    (
        109,
        "Measurement layer: llm telemetry columns, bench_runs, golden_queries",
        """
        ALTER TABLE llm_calls ADD COLUMN ttft_ms REAL;
        ALTER TABLE llm_calls ADD COLUMN tok_per_s REAL;
        ALTER TABLE llm_calls ADD COLUMN streamed INTEGER NOT NULL DEFAULT 0;

        CREATE TABLE IF NOT EXISTS bench_runs (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT NOT NULL DEFAULT (datetime('now')),
            kind    TEXT NOT NULL,
            label   TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS br_kind_ts ON bench_runs(kind, ts);

        CREATE TABLE IF NOT EXISTS golden_queries (
            id           TEXT PRIMARY KEY,
            query        TEXT NOT NULL,
            kind         TEXT NOT NULL DEFAULT 'chunk',
            relevant_ids TEXT NOT NULL DEFAULT '[]',
            work_id      TEXT,
            notes        TEXT NOT NULL DEFAULT '',
            source       TEXT NOT NULL DEFAULT 'manual',
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """,
    ),
    # v110 — Ingestion shield: document quarantine flag.
    #   0 = clean / never flagged
    #   1 = quarantined, awaiting human review (injection screen tripped at
    #       import; doc is stored + inspectable but NOT chunked, indexed,
    #       harvested, or embedded — blast-radius isolation)
    #   2 = reviewed and kept quarantined (stays isolated, leaves the queue)
    # Screen findings live in documents.meta JSON under the "shield" key.
    (
        110,
        "Ingestion shield: documents.quarantined flag",
        """
        ALTER TABLE documents ADD COLUMN quarantined INTEGER NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS doc_quarantined ON documents(quarantined)
            WHERE quarantined > 0;
    """,
    ),
    # v111 — Mobile app retired (Aug 2026): drop the Expo push-token table.
    # Nothing can register a push token anymore, so the subsystem was removed
    # (routes/users.py, capabilities/push.py, db methods, call sites).
    (
        111,
        "Drop push_tokens table (mobile push retired)",
        """
        DROP TABLE IF EXISTS push_tokens;
    """,
    ),
    (
        112,
        "Commonplace notes: note_blocks capture inbox + note_reports daily reports",
        """
        CREATE TABLE IF NOT EXISTS note_blocks (
            id          TEXT PRIMARY KEY,
            day         TEXT NOT NULL,
            text        TEXT NOT NULL,
            source      TEXT NOT NULL DEFAULT 'web',
            status      TEXT NOT NULL DEFAULT 'inbox',
            proposal    TEXT,
            error       TEXT,
            filed_paths TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS note_blocks_day    ON note_blocks(day);
        CREATE INDEX IF NOT EXISTS note_blocks_status ON note_blocks(status);
        CREATE TABLE IF NOT EXISTS note_reports (
            day        TEXT PRIMARY KEY,
            report     TEXT NOT NULL,
            block_ids  TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """,
    ),
    # v113 — Read Aloud listening positions synced per document so a spot saved
    # on one device (phone) is visible on another (desktop). One row per
    # document; `saved_at` is the client wall-clock at save time (ms) and is
    # used to pick the freshest of the local vs server position on resume.
    (
        113,
        "Read Aloud cross-device listening positions",
        """
        CREATE TABLE IF NOT EXISTS read_positions (
            doc_id     TEXT PRIMARY KEY,
            part       INTEGER NOT NULL,
            time       REAL NOT NULL,
            part_count INTEGER NOT NULL,
            saved_at   INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
    """,
    ),
    # v114 — Executor durable job records (FA-06 restart reconciliation).
    # A lean, in-process durability table for the shared background executor
    # (distinct from the richer `jobs` table used by JOB_SM). Each executor
    # job persists a minimal row (id, kind, state, attempts) so that on the
    # next startup any row left 'running'/'queued' from a crashed process can
    # be marked 'failed' ("orphaned by restart") — clients polling old IDs get
    # a truthful terminal state instead of a 404.
    (
        114,
        "Executor durable job records (restart reconciliation)",
        """
        CREATE TABLE IF NOT EXISTS bg_jobs (
            id          TEXT PRIMARY KEY,
            kind        TEXT NOT NULL,
            label       TEXT,
            state       TEXT NOT NULL DEFAULT 'running',
            attempts    INTEGER NOT NULL DEFAULT 1,
            error       TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS bg_jobs_state   ON bg_jobs(state);
        CREATE INDEX IF NOT EXISTS bg_jobs_created ON bg_jobs(created_at);
    """,
    ),
    # v115 — Project Workbench: agentic build/edit/repair projects (xlsx or
    # code) with full version history. Every accepted iteration is a new
    # wb_versions row whose files live at data/workbench/{project}/v{n}/.
    # Completing a project zips every version + a hash manifest into
    # data/workbench/archives/ and flips status to 'archived'.
    (
        115,
        "Project Workbench: versioned build/edit/repair projects",
        """
        CREATE TABLE IF NOT EXISTS wb_projects (
            id           TEXT PRIMARY KEY,
            title        TEXT NOT NULL,
            kind         TEXT NOT NULL,
            brief        TEXT NOT NULL DEFAULT '',
            status       TEXT NOT NULL DEFAULT 'active',
            building     INTEGER NOT NULL DEFAULT 0,
            last_error   TEXT,
            archive_path TEXT,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wb_versions (
            id          TEXT PRIMARY KEY,
            project_id  TEXT NOT NULL,
            version_no  INTEGER NOT NULL,
            instruction TEXT NOT NULL,
            note        TEXT,
            files_json  TEXT NOT NULL DEFAULT '[]',
            checks_json TEXT,
            verdict     TEXT,
            created_at  TEXT NOT NULL,
            UNIQUE(project_id, version_no)
        );
        CREATE INDEX IF NOT EXISTS wb_versions_project ON wb_versions(project_id, version_no);
    """,
    ),
    # v116 — Workbench portfolio: a `meta` JSON blob on each project holds the
    # AI needs assessment and the close-out record (summary + lessons). A new
    # status value 'shelved' (put away without completing) joins 'active' and
    # 'archived' — no schema change needed for the status itself.
    (
        116,
        "Workbench portfolio: project meta for needs assessment and close-out",
        """
        ALTER TABLE wb_projects ADD COLUMN meta TEXT NOT NULL DEFAULT '{}';
    """,
    ),
    # v117 — Operations: durable multi-step runs. An operation is an ordered
    # list of steps, each referencing a registered action. Every step records
    # its own started/finished/result/error so a run can be paused, survive a
    # restart, and resume by skipping steps already done. States:
    #   operations:      pending | running | paused | done | failed | cancelled
    #   operation_steps: pending | running | done | failed | cancelled
    (
        117,
        "Operations: durable multi-step runs with per-step checkpoints",
        """
        CREATE TABLE IF NOT EXISTS operations (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            playbook_id TEXT,
            work_id     TEXT,
            params      TEXT NOT NULL DEFAULT '{}',
            state       TEXT NOT NULL DEFAULT 'pending',
            error       TEXT,
            created_at  TEXT NOT NULL,
            started_at  TEXT,
            finished_at TEXT,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS operations_state ON operations(state);
        CREATE INDEX IF NOT EXISTS operations_work ON operations(work_id);
        CREATE TABLE IF NOT EXISTS operation_steps (
            id           TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL,
            step_index   INTEGER NOT NULL,
            action_id    TEXT NOT NULL,
            label        TEXT NOT NULL,
            params       TEXT NOT NULL DEFAULT '{}',
            state        TEXT NOT NULL DEFAULT 'pending',
            result       TEXT,
            error        TEXT,
            started_at   TEXT,
            finished_at  TEXT,
            UNIQUE(operation_id, step_index)
        );
        CREATE INDEX IF NOT EXISTS operation_steps_op
            ON operation_steps(operation_id, step_index);
    """,
    ),
    # v118 — Operations claim fencing: every successful claim (start/resume)
    # rotates run_token; all step/operation transitions carry the token, so a
    # stale runner that lost its claim can never mutate a newer run's state.
    (
        118,
        "Operations: run_token claim fencing",
        """
        ALTER TABLE operations ADD COLUMN run_token TEXT;
    """,
    ),
    # v119 — Custom playbooks: user-saved, one-button operation plans (usually
    # born from an AI-planned job). Steps are stored as validated JSON.
    (
        119,
        "Operations: custom playbooks",
        """
        CREATE TABLE IF NOT EXISTS custom_playbooks (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            steps       TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );
    """,
    ),
    # v120 — Scheduled automations: attach a recurring schedule to any playbook
    # (built-in or custom). Time fields are LOCAL server time (this is a
    # local-first personal server), matching the nightshift daemon's precedent;
    # next_run_at is a naive local ISO timestamp. operations.schedule_id links
    # runs back to their automation for history; failure_alerted guarantees a
    # failed scheduled run alerts exactly once.
    (
        120,
        "Operations: scheduled automations",
        """
        CREATE TABLE IF NOT EXISTS playbook_schedules (
            id           TEXT PRIMARY KEY,
            playbook_id  TEXT NOT NULL,
            title        TEXT NOT NULL DEFAULT '',
            work_id      TEXT,
            cadence      TEXT NOT NULL,
            time_of_day  TEXT NOT NULL,
            day_of_week  INTEGER,
            enabled      INTEGER NOT NULL DEFAULT 1,
            last_run_at  TEXT,
            next_run_at  TEXT NOT NULL,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS sched_next ON playbook_schedules(enabled, next_run_at);
        ALTER TABLE operations ADD COLUMN schedule_id TEXT;
        ALTER TABLE operations ADD COLUMN failure_alerted INTEGER NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS operations_schedule ON operations(schedule_id);
    """,
    ),
    # v121 — Per-Work cover image. cover_path is a data-dir-relative path
    # (e.g. "covers/<work_id>.png") set by POST /api/works/{id}/cover; NULL
    # means no cover (clients fall back to the branded Orivellum artwork).
    (
        121,
        "Works: cover image path",
        """
        ALTER TABLE works ADD COLUMN cover_path TEXT;
    """,
    ),
    # v122 — Writing Architect archive decomposition (Pipeline M0 / DECOMPOSE).
    #
    # wa_archive_docs   — coverage inventory: one row per real file in the
    #                     archive (resource forks excluded), with content hash,
    #                     duplicate resolution, and an explicit disposition
    #                     (extracted / deduped / deferred + reason) so the
    #                     coverage report can prove nothing was silently lost.
    # wa_records        — machine-readable doctrine records extracted from the
    #                     archive: engine contracts, runtime policies, record
    #                     schemas, table specs, voice envelope, POSITION spec,
    #                     provenance spec.  payload is parsed-section JSON.
    # wa_canon_proposals— BIBLE_DATA facts staged as PROPOSALS ONLY.  Nothing
    #                     from the archive writes canon authority; every row
    #                     starts status='proposed' and waits for author
    #                     ratification.  id is a deterministic content hash so
    #                     re-running the decomposer never clobbers a
    #                     ratification decision (INSERT OR IGNORE).
    (
        122,
        "Writing Architect M0: archive inventory, doctrine records, canon proposals",
        """
        CREATE TABLE IF NOT EXISTS wa_archive_docs (
            id            TEXT PRIMARY KEY,
            rel_path      TEXT NOT NULL UNIQUE,
            filename      TEXT NOT NULL,
            layer         TEXT NOT NULL,
            sha256        TEXT NOT NULL,
            size_bytes    INTEGER NOT NULL,
            duplicate_of  TEXT,
            status        TEXT NOT NULL,
            reason        TEXT,
            target_kind   TEXT,
            run_id        TEXT NOT NULL,
            created_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS wa_docs_layer ON wa_archive_docs(layer, status);
        CREATE TABLE IF NOT EXISTS wa_records (
            id           TEXT PRIMARY KEY,
            record_type  TEXT NOT NULL,
            name         TEXT NOT NULL,
            payload      TEXT NOT NULL,
            source_path  TEXT NOT NULL,
            source_note  TEXT,
            run_id       TEXT NOT NULL,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS wa_records_type ON wa_records(record_type, name);
        CREATE TABLE IF NOT EXISTS wa_canon_proposals (
            id             TEXT PRIMARY KEY,
            fact_title     TEXT NOT NULL,
            fact_text      TEXT NOT NULL,
            classification TEXT NOT NULL,
            scope          TEXT NOT NULL,
            source_path    TEXT NOT NULL,
            source_location TEXT NOT NULL,
            status         TEXT NOT NULL DEFAULT 'proposed',
            decided_at     TEXT,
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS wa_canon_status ON wa_canon_proposals(status, scope);
    """,
    ),
    # v123 — Canon authority (Masterpiece Pipeline Part 2.1).
    #
    # canon_fact — the single authority substrate for the trilogy: every fact
    # carries a mandatory classification (HISTORICAL / INFERRED / INVENTED),
    # a source reference, and an author signature.  work_id NULL means the
    # fact holds series-wide (all three books).  Revisions are explicit:
    # a new fact must name the fact it supersedes; silent overwrite is
    # impossible because there is no UPDATE path for statement/classification.
    # Insert-path guards live in database/canon_store.py.
    (
        123,
        "Canon authority: classified, sourced, signed facts (series-scoped)",
        """
        CREATE TABLE IF NOT EXISTS canon_fact (
            id                  TEXT PRIMARY KEY,
            work_id             TEXT,
            statement           TEXT NOT NULL,
            classification      TEXT NOT NULL
                CHECK (classification IN ('HISTORICAL','INFERRED','INVENTED')),
            source_ref          TEXT NOT NULL DEFAULT '',
            parent_ids          TEXT NOT NULL DEFAULT '[]',
            established_chapter INTEGER,
            established_offset  INTEGER,
            supersedes          TEXT,
            status              TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','superseded','retracted')),
            signed_by           TEXT NOT NULL DEFAULT '',
            origin              TEXT NOT NULL DEFAULT 'author',
            proposal_id         TEXT,
            retracted_by        TEXT,
            retracted_at        TEXT,
            created_at          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_canon_work_class
            ON canon_fact(work_id, classification, status);
        CREATE INDEX IF NOT EXISTS idx_canon_supersedes ON canon_fact(supersedes);
    """,
    ),
    # v124 — ATLAS-O world graph (Masterpiece Pipeline Part 2.2, LAW 2 + LAW 3).
    #
    # graph_node / graph_edge — the single typed representation of what is
    # true in the story.  Node types are a closed set of seven (per the ATLAS
    # schema); edge types are a closed set in five groups.  Every node and
    # edge carries a mandatory evidence quote and character offset into the
    # chapter text (LAW 3) — the insert path in db.py refuses anything
    # without grounded evidence, and the extractor discards (never coerces)
    # anything outside the schema.
    #
    # graph_inconsistency — cross-chapter contradictions that survived
    # propose-then-verify.  Both sides carry quote + offset so every finding
    # is auditable against the manuscript.  Unverified proposals are
    # discarded, never stored.
    (
        124,
        "ATLAS-O world graph: typed nodes/edges with evidence, verified inconsistencies",
        """
        CREATE TABLE IF NOT EXISTS graph_node (
            id              TEXT PRIMARY KEY,
            work_id         TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            chapter_id      TEXT REFERENCES book_chapters(id) ON DELETE CASCADE,
            node_type       TEXT NOT NULL CHECK (node_type IN
                ('Character','Event','Location','TimePoint','Object','Vehicle','Concept')),
            name            TEXT NOT NULL CHECK (length(trim(name)) > 0),
            description     TEXT NOT NULL DEFAULT '',
            evidence_quote  TEXT NOT NULL CHECK (length(trim(evidence_quote)) > 0),
            evidence_offset INTEGER NOT NULL CHECK (evidence_offset >= 0),
            attributes      TEXT NOT NULL DEFAULT '{}',
            canon_fact_id   TEXT REFERENCES canon_fact(id) ON DELETE SET NULL,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gnode_work    ON graph_node(work_id, node_type);
        CREATE INDEX IF NOT EXISTS idx_gnode_chapter ON graph_node(chapter_id);
        CREATE INDEX IF NOT EXISTS idx_gnode_name    ON graph_node(work_id, name);

        CREATE TABLE IF NOT EXISTS graph_edge (
            id              TEXT PRIMARY KEY,
            work_id         TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            chapter_id      TEXT REFERENCES book_chapters(id) ON DELETE CASCADE,
            src             TEXT NOT NULL REFERENCES graph_node(id) ON DELETE CASCADE,
            dst             TEXT NOT NULL REFERENCES graph_node(id) ON DELETE CASCADE,
            edge_type       TEXT NOT NULL CHECK (edge_type IN
                ('performs','undergoes','experiences',
                 'kinship_with','affinity_with','hostility_with','affiliated_with',
                 'precedes','occurs_after','causes','contrasts_with','references',
                 'occurs_at','occurs_on','located_at','present_on',
                 'possesses','uses','part_of','is_a')),
            edge_group      TEXT NOT NULL CHECK (edge_group IN
                ('event_role','social','inter_event','spatiotemporal','object')),
            evidence_quote  TEXT NOT NULL CHECK (length(trim(evidence_quote)) > 0),
            evidence_offset INTEGER NOT NULL CHECK (evidence_offset >= 0),
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gedge_work    ON graph_edge(work_id, edge_group);
        CREATE INDEX IF NOT EXISTS idx_gedge_chapter ON graph_edge(chapter_id);
        CREATE INDEX IF NOT EXISTS idx_gedge_src     ON graph_edge(src);
        CREATE INDEX IF NOT EXISTS idx_gedge_dst     ON graph_edge(dst);

        CREATE TABLE IF NOT EXISTS graph_inconsistency (
            id                   TEXT PRIMARY KEY,
            work_id              TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            chapter_id           TEXT NOT NULL REFERENCES book_chapters(id) ON DELETE CASCADE,
            description          TEXT NOT NULL CHECK (length(trim(description)) > 0),
            current_quote        TEXT NOT NULL CHECK (length(trim(current_quote)) > 0),
            current_offset       INTEGER NOT NULL CHECK (current_offset >= 0),
            prior_chapter_id     TEXT NOT NULL REFERENCES book_chapters(id) ON DELETE CASCADE,
            prior_quote          TEXT NOT NULL CHECK (length(trim(prior_quote)) > 0),
            prior_offset         INTEGER NOT NULL CHECK (prior_offset >= 0),
            reasoning            TEXT NOT NULL DEFAULT '',
            status               TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','fixed','intentional','wontfix')),
            created_at           TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ginc_work    ON graph_inconsistency(work_id, status);
        CREATE INDEX IF NOT EXISTS idx_ginc_chapter ON graph_inconsistency(chapter_id);
    """,
    ),
    # v125 — ConStory narrative findings (Masterpiece Pipeline Part 2.3 + B6).
    #
    # narrative_finding — story contradictions detected by the ConStory
    # checker.  Five categories with a closed set of 19 subtypes (the subtype
    # registry is enforced in capabilities/constory.py — anything outside it
    # is discarded, never coerced).  Every finding carries DUAL evidence:
    # the established fact's verbatim quote + chapter + character offset AND
    # the contradicting passage's quote + chapter + offset (LAW 3 on both
    # sides).  Severity is COMPUTED from (subtype, canon_class) — never
    # model-chosen.  Dispositions: open / fixed / intentional / wontfix;
    # 'intentional' requires a note (enforced in db.py + the API route).
    # dedupe_key is stable across re-runs so dispositioned findings are
    # never re-created as new 'open' rows.
    (
        125,
        "ConStory narrative findings: 19-subtype contradictions with dual evidence",
        """
        CREATE TABLE IF NOT EXISTS narrative_finding (
            id                    TEXT PRIMARY KEY,
            work_id               TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            chapter_id            TEXT NOT NULL REFERENCES book_chapters(id) ON DELETE CASCADE,
            category              TEXT NOT NULL CHECK (category IN
                ('timeline_plot','characterization','worldbuilding',
                 'factual_detail','narrative_style')),
            subtype               TEXT NOT NULL CHECK (length(trim(subtype)) > 0),
            fact_quote            TEXT NOT NULL CHECK (length(trim(fact_quote)) > 0),
            fact_chapter          INTEGER NOT NULL CHECK (fact_chapter >= 0),
            fact_offset           INTEGER NOT NULL CHECK (fact_offset >= 0),
            contradiction_quote   TEXT NOT NULL CHECK (length(trim(contradiction_quote)) > 0),
            contradiction_chapter INTEGER NOT NULL CHECK (contradiction_chapter >= 0),
            contradiction_offset  INTEGER NOT NULL CHECK (contradiction_offset >= 0),
            reasoning             TEXT NOT NULL DEFAULT '',
            severity              TEXT NOT NULL CHECK (severity IN
                ('critical','high','medium','low')),
            canon_class           TEXT CHECK (canon_class IS NULL OR canon_class IN
                ('HISTORICAL','INFERRED','INVENTED')),
            canon_fact_id         TEXT REFERENCES canon_fact(id) ON DELETE SET NULL,
            disposition           TEXT NOT NULL DEFAULT 'open' CHECK (disposition IN
                ('open','fixed','intentional','wontfix')),
            disposition_note      TEXT NOT NULL DEFAULT '',
            disposition_by        TEXT NOT NULL DEFAULT '',
            disposition_at        TEXT,
            detector              TEXT NOT NULL DEFAULT 'constory',
            dedupe_key            TEXT NOT NULL,
            created_at            TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_nf_dedupe  ON narrative_finding(work_id, dedupe_key);
        CREATE INDEX IF NOT EXISTS idx_nf_work
            ON narrative_finding(work_id, disposition, severity);
        CREATE INDEX IF NOT EXISTS idx_nf_chapter        ON narrative_finding(chapter_id);
    """,
    ),
    # v126 — ASSAY: the governed quality-instrument registry (E4).
    #
    # assay_instrument — every quality check registered as an Engine Contract
    # record (name, purpose, allowed/forbidden operations, authority
    # relationship, required output schema) with an honest three-tier
    # authority level and a certification status.  RULE: no instrument may
    # block at Tier 1/2 until certification='certified' — new instruments
    # start 'advisory' (blocking is computed, never stored).
    # assay_run — one execution of one instrument against a work (optionally
    # one chapter); records verdict/score + evidence JSON.
    # assay_finding — standard finding schema shared by ALL instruments:
    # Unit | Force Check | Issue Type | Severity | Classification | Action,
    # plus evidence JSON (quotes/offsets/metrics).
    # assay_signature — author signatures for the D14–D17 gates ('open'
    # unlocks evidence gathering; 'go'/'no_go' is the author's decision —
    # the machine never decides these gates).
    # assay_baseline — per-work stored baselines (voice target envelope,
    # D13 act targets, judge previous-revision snapshots).
    (
        126,
        "ASSAY instrument registry: engine contracts, runs, standard findings, signatures",
        """
        CREATE TABLE IF NOT EXISTS assay_instrument (
            id                     TEXT PRIMARY KEY,
            key                    TEXT NOT NULL UNIQUE,
            name                   TEXT NOT NULL,
            purpose                TEXT NOT NULL DEFAULT '',
            tier                   INTEGER NOT NULL CHECK (tier IN (1,2,3)),
            variance               TEXT NOT NULL CHECK (variance IN
                ('deterministic','evidence','perspectival')),
            certification          TEXT NOT NULL DEFAULT 'advisory' CHECK (certification IN
                ('advisory','shadow','certified','retired')),
            allowed_ops            TEXT NOT NULL DEFAULT '[]',
            forbidden_ops          TEXT NOT NULL DEFAULT '[]',
            authority_relationship TEXT NOT NULL DEFAULT '',
            output_schema          TEXT NOT NULL DEFAULT '{}',
            scope                  TEXT NOT NULL DEFAULT '{}',
            thresholds             TEXT NOT NULL DEFAULT '{}',
            origin                 TEXT NOT NULL DEFAULT '',
            created_at             TEXT NOT NULL,
            updated_at             TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS assay_run (
            id             TEXT PRIMARY KEY,
            instrument_id  TEXT NOT NULL REFERENCES assay_instrument(id) ON DELETE CASCADE,
            work_id        TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            chapter_id     TEXT REFERENCES book_chapters(id) ON DELETE SET NULL,
            status         TEXT NOT NULL DEFAULT 'running' CHECK (status IN
                ('running','done','error')),
            verdict        TEXT,
            score          REAL,
            evidence       TEXT NOT NULL DEFAULT '{}',
            findings_count INTEGER NOT NULL DEFAULT 0,
            error          TEXT,
            started_at     TEXT NOT NULL,
            finished_at    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_assay_run_work
            ON assay_run(work_id, instrument_id, started_at);
        CREATE TABLE IF NOT EXISTS assay_finding (
            id             TEXT PRIMARY KEY,
            run_id         TEXT NOT NULL REFERENCES assay_run(id) ON DELETE CASCADE,
            instrument_id  TEXT NOT NULL REFERENCES assay_instrument(id) ON DELETE CASCADE,
            work_id        TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            chapter_id     TEXT REFERENCES book_chapters(id) ON DELETE SET NULL,
            unit           TEXT NOT NULL,
            force_check    TEXT NOT NULL,
            issue_type     TEXT NOT NULL,
            severity       TEXT NOT NULL CHECK (severity IN
                ('critical','high','medium','low','info')),
            classification TEXT NOT NULL DEFAULT '',
            action         TEXT NOT NULL DEFAULT '',
            evidence       TEXT NOT NULL DEFAULT '{}',
            created_at     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_assay_finding_run  ON assay_finding(run_id);
        CREATE INDEX IF NOT EXISTS idx_assay_finding_work ON assay_finding(work_id, instrument_id);
        CREATE TABLE IF NOT EXISTS assay_signature (
            id        TEXT PRIMARY KEY,
            work_id   TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            gate_key  TEXT NOT NULL,
            author    TEXT NOT NULL CHECK (length(trim(author)) > 0),
            decision  TEXT NOT NULL DEFAULT 'open' CHECK (decision IN ('open','go','no_go')),
            note      TEXT NOT NULL DEFAULT '',
            signed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_assay_sig ON assay_signature(work_id, gate_key, signed_at);
        CREATE TABLE IF NOT EXISTS assay_baseline (
            id         TEXT PRIMARY KEY,
            work_id    TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            key        TEXT NOT NULL,
            payload    TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            UNIQUE(work_id, key)
        );
    """,
    ),
    # v127 — POSITION: derive where an inherited manuscript truly stands (E5).
    #
    # position_audit — one row per audit run.  Stage is DERIVED from evidence
    # (ten deterministic tests + instrument battery), never trusted from a
    # status field.  evidence JSON holds every test and its result; blocking
    # JSON holds the completion plan (backfill / repair / complete).  The row
    # is the claim: created 'running' under the write lock, always finished
    # as 'done' or 'error' — a leaked 'running' row is a bug.
    #
    # position_proposal — reconstruction proposals (persona / de-facto
    # blueprint / de-facto voice spec) derived from the prose.  Existing
    # prose is evidence, not authority: nothing here becomes authority until
    # the author resolves it through the review gate with a signature.
    # Canon-fact proposals go through wa_canon_proposals + CanonStore
    # ratification instead (the existing signed path).  Ids are
    # deterministic (work+kind+key hash) so re-runs never clobber rows the
    # author already resolved.
    (
        127,
        "POSITION audit: derived-stage evidence rows + review-gated reconstruction proposals",
        """
        CREATE TABLE IF NOT EXISTS position_audit (
            id            TEXT PRIMARY KEY,
            work_id       TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            derived_stage TEXT NOT NULL DEFAULT '',
            claimed_stage TEXT,
            evidence      TEXT NOT NULL DEFAULT '{}',
            blocking      TEXT NOT NULL DEFAULT '{}',
            status        TEXT NOT NULL DEFAULT 'running' CHECK (status IN
                ('running','done','error')),
            error         TEXT,
            run_at        TEXT NOT NULL,
            finished_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_position_audit_work
            ON position_audit(work_id, run_at);
        CREATE TABLE IF NOT EXISTS position_proposal (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            audit_id    TEXT NOT NULL,
            kind        TEXT NOT NULL CHECK (kind IN
                ('persona','blueprint','voice_spec')),
            title       TEXT NOT NULL,
            payload     TEXT NOT NULL DEFAULT '{}',
            evidence    TEXT NOT NULL DEFAULT '{}',
            status      TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN
                ('proposed','approved','rejected')),
            resolved_by TEXT,
            note        TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_position_proposal_status
            ON position_proposal(status, work_id);
    """,
    ),
    # v128 — LOOM (E2): the chapter drafting engine.
    #
    # loom_persona — character records used to ground drafting: diction
    # profile + knowledge horizon (which canon facts the character can know,
    # per act boundary).  Review-gated: rows are created 'proposed' and only
    # an author signature approves them; drafting uses ONLY approved rows.
    #
    # loom_world_state — the accumulated world state, one key per fact of
    # the world, overwrite semantics (new key inserts, existing replaces).
    # Rebuilt deterministically from the world graph when resuming mid-book.
    #
    # loom_chapter_revision — every draft lands as a NEW revision row;
    # approved chapters are never overwritten.
    #
    # loom_run — one row per drafting run; the row is the claim (created
    # 'running' under the write lock, always finished done/escalated/error).
    #
    # artifact_provenance — KDP-definition provenance (spec 2.4): content a
    # tool created is 'ai_generated' even after heavy editing; llm_call_ids
    # is the audit trail of every gateway call that produced the artifact.
    (
        128,
        "LOOM drafting engine: personas, world state, chapter revisions, runs, provenance",
        """
        CREATE TABLE IF NOT EXISTS loom_persona (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            payload     TEXT NOT NULL DEFAULT '{}',
            status      TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN
                ('proposed','approved','rejected')),
            resolved_by TEXT,
            resolved_at TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            UNIQUE(work_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_loom_persona_work
            ON loom_persona(work_id, status);
        CREATE TABLE IF NOT EXISTS loom_world_state (
            work_id            TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            key                TEXT NOT NULL,
            value              TEXT NOT NULL,
            source_chapter_seq INTEGER NOT NULL DEFAULT 0,
            updated_at         TEXT NOT NULL,
            PRIMARY KEY (work_id, key)
        );
        CREATE TABLE IF NOT EXISTS loom_chapter_revision (
            id         TEXT PRIMARY KEY,
            chapter_id TEXT NOT NULL REFERENCES book_chapters(id) ON DELETE CASCADE,
            work_id    TEXT NOT NULL,
            rev        INTEGER NOT NULL,
            text       TEXT NOT NULL,
            word_count INTEGER NOT NULL DEFAULT 0,
            meta       TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(chapter_id, rev)
        );
        CREATE INDEX IF NOT EXISTS idx_loom_rev_chapter
            ON loom_chapter_revision(chapter_id, rev);
        CREATE TABLE IF NOT EXISTS loom_run (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            chapter_id  TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN
                ('running','done','escalated','error')),
            evidence    TEXT NOT NULL DEFAULT '{}',
            error       TEXT,
            started_at  TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_loom_run_work
            ON loom_run(work_id, started_at);
        CREATE TABLE IF NOT EXISTS artifact_provenance (
            artifact_id         TEXT NOT NULL,
            artifact_kind       TEXT NOT NULL,
            origin              TEXT NOT NULL CHECK (origin IN
                ('human','ai_assisted','ai_generated')),
            llm_call_ids        TEXT NOT NULL DEFAULT '[]',
            human_edit_fraction REAL,
            declared_by         TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (artifact_id, artifact_kind)
        );
    """,
    ),
    # v129 — BAND + LINEAGE (E8/E9): revision lineage columns.
    #
    # Every revision now records WHERE it came from (parent_rev — the head
    # revision it was created from, NULL for a chapter's first revision),
    # WHAT produced it (origin: human / ai_assisted / ai_generated), WHO
    # (created_by: 'loom', an author signature, 'user', 'checkpoint'), and —
    # for surgical band edits — the exact edit scope (JSON: span offsets,
    # instruction, fingerprints before/after).  Nothing is ever deleted;
    # restore creates a NEW revision pointing at its source.
    (
        129,
        "BAND/LINEAGE: revision lineage (parent_rev, origin, created_by, edit_scope)",
        """
        ALTER TABLE loom_chapter_revision ADD COLUMN parent_rev INTEGER;
        ALTER TABLE loom_chapter_revision ADD COLUMN origin TEXT NOT NULL DEFAULT 'ai_generated';
        ALTER TABLE loom_chapter_revision ADD COLUMN created_by TEXT NOT NULL DEFAULT 'loom';
        ALTER TABLE loom_chapter_revision ADD COLUMN edit_scope TEXT
    """,
    ),
    # v130 — PROMOTION (E10): shadow-mode certification for quality instruments.
    #
    # assay_instrument.shadow_of — a candidate instrument may declare the
    # certified baseline instrument it shadows; when the baseline runs, the
    # candidate co-runs against the same work/chapter so parity is measurable.
    # assay_finding.disposition — the author's ratified verdict on a finding
    # ('open' | 'true_positive' | 'false_positive').  Dispositions are the
    # ground truth precision is computed against — rolling precision =
    # true_positive / (true_positive + false_positive).
    # assay_certification_event — the append-only certification ledger: every
    # promotion / demotion / shadow-entry / retirement is one row recording
    # who, from→to, and the precision evidence at the moment of the decision.
    # Certification itself stays a column on assay_instrument; this table is
    # its history, and set_assay_certification is the ONLY write path.
    (
        130,
        "PROMOTION: shadow_of, finding dispositions, certification event ledger",
        """
        ALTER TABLE assay_instrument ADD COLUMN shadow_of TEXT;
        ALTER TABLE assay_finding ADD COLUMN disposition TEXT NOT NULL DEFAULT 'open';
        ALTER TABLE assay_finding ADD COLUMN disposition_note TEXT NOT NULL DEFAULT '';
        ALTER TABLE assay_finding ADD COLUMN dispositioned_at TEXT;
        CREATE INDEX IF NOT EXISTS idx_assay_finding_dispo
            ON assay_finding(instrument_id, dispositioned_at);
        CREATE TABLE IF NOT EXISTS assay_certification_event (
            id            TEXT PRIMARY KEY,
            instrument_id TEXT NOT NULL REFERENCES assay_instrument(id) ON DELETE CASCADE,
            from_status   TEXT NOT NULL,
            to_status     TEXT NOT NULL,
            actor         TEXT NOT NULL,
            precision_val REAL,
            sample_size   INTEGER,
            note          TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_assay_cert_event
            ON assay_certification_event(instrument_id, created_at)
    """,
    ),
    # v131 — PROMOTION evidence epochs.
    #
    # assay_instrument.shadow_epoch — set every time an instrument ENTERS
    # shadow (lifecycle transition or governed re-seed demotion).  Only
    # findings created at/after this epoch may count toward certification
    # evidence: dispositions accumulated while advisory, or under a prior
    # contract, can never promote.  Backfill: instruments currently in
    # shadow get their updated_at as the epoch (best available marker).
    (
        131,
        "PROMOTION: shadow_epoch — certification evidence scoped to the current shadow entry",
        """
        ALTER TABLE assay_instrument ADD COLUMN shadow_epoch TEXT;
        UPDATE assay_instrument SET shadow_epoch = updated_at
            WHERE certification = 'shadow'
    """,
    ),
    # v132 — SERIES (M18): series as a first-class scope.
    #
    # series          — a named, ordered group of Works (e.g. a trilogy).
    # series_member   — ordered membership; volume is 1-based and unique per
    #                   series; a Work belongs to AT MOST ONE series (unique
    #                   index on work_id) so authority resolution is never
    #                   ambiguous.
    # canon_fact.series_id — a fact scoped to ONE series (work_id must be
    #                   NULL): it binds every member volume.  Legacy facts
    #                   with work_id NULL and series_id NULL stay global
    #                   (the pre-series "series-wide" semantics).
    # canon_fact.overrides — a BOOK-scoped fact may explicitly override a
    #                   series/global fact for that book only; the target
    #                   stays active for every other volume.  Overrides are
    #                   explicit records — a book can never silently shadow
    #                   series canon.
    (
        132,
        "SERIES: series + ordered members; canon facts scoped to a series with per-book overrides",
        """
        CREATE TABLE IF NOT EXISTS series (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS series_member (
            id TEXT PRIMARY KEY,
            series_id TEXT NOT NULL REFERENCES series(id),
            work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            volume INTEGER NOT NULL CHECK(volume >= 1),
            created_at TEXT NOT NULL,
            UNIQUE(series_id, work_id),
            UNIQUE(series_id, volume)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_series_member_work
            ON series_member(work_id);
        ALTER TABLE canon_fact ADD COLUMN series_id TEXT REFERENCES series(id);
        ALTER TABLE canon_fact ADD COLUMN overrides TEXT REFERENCES canon_fact(id);
        CREATE INDEX IF NOT EXISTS idx_canon_fact_series
            ON canon_fact(series_id, status);
        CREATE INDEX IF NOT EXISTS idx_canon_fact_overrides
            ON canon_fact(overrides)
    """,
    ),
    # v133 — AUTONOMY (M12): unattended draft → check → revise runner.
    #
    # autonomy_run — one row per unattended run; the 'running' row IS the
    #                claim (at most one per work, like loom_run).  budget /
    #                consumed / report are JSON snapshots so the run report
    #                survives exactly as produced.  Signatures are NEVER
    #                written by the runner — an unsigned gate halts the run
    #                and queues a review item instead.
    (
        133,
        "AUTONOMY: unattended draft-check-revise runs with budget + halt ledger",
        """
        CREATE TABLE IF NOT EXISTS autonomy_run (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN
                ('running','done','halted','stopped','error')),
            budget      TEXT NOT NULL DEFAULT '{}',
            consumed    TEXT NOT NULL DEFAULT '{}',
            report      TEXT NOT NULL DEFAULT '{}',
            stop_reason TEXT,
            started_at  TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_autonomy_run_work
            ON autonomy_run(work_id, started_at)
    """,
    ),
    # v134 — GAP ENGINE (G-M1/G-M2): gaps get identity, a lifecycle, and the
    # first true detector; hygiene findings get dismissal persistence.
    #
    # gap            — one row per research gap.  id is a content hash over
    #                  (frame_node_id, gap_class, scope) so re-detection maps
    #                  to the same row.  Insert path (db.create_or_refresh_gap)
    #                  REFUSES rows without a frame citation: frame_node_id,
    #                  frame_source_ref, and evidence_absent are all NOT NULL
    #                  and enforced non-blank in code — the same discipline as
    #                  canon_fact's source_ref rule.  Dismissed / out_of_scope
    #                  rows persist forever and are never resurrected.
    # gap_transition — append-only ledger; every status change writes a row.
    # hygiene_dismissal — dismissed corpus-hygiene findings, keyed by the
    #                  finding's stable content hash; a dismissed finding
    #                  never reappears in detect_hygiene output.
    (
        134,
        "GAP ENGINE: gap identity + lifecycle ledger + hygiene dismissals",
        """
        CREATE TABLE IF NOT EXISTS gap (
            id               TEXT PRIMARY KEY,
            work_id          TEXT REFERENCES works(id) ON DELETE CASCADE,
            gap_class        TEXT NOT NULL,
            scope            TEXT NOT NULL,
            unit             TEXT NOT NULL DEFAULT '',
            force_check      TEXT NOT NULL DEFAULT '',
            issue_type       TEXT NOT NULL DEFAULT '',
            severity         TEXT NOT NULL DEFAULT 'low',
            classification   TEXT NOT NULL DEFAULT '',
            action           TEXT NOT NULL DEFAULT '',
            frame_node_id    TEXT NOT NULL,
            frame_source_ref TEXT NOT NULL,
            evidence_absent  TEXT NOT NULL,
            status           TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN
                ('proposed','ratified','assigned','researched','covered',
                 'mastered','dismissed','out_of_scope')),
            status_reason    TEXT NOT NULL DEFAULT '',
            signed_by        TEXT NOT NULL DEFAULT '',
            meta             TEXT NOT NULL DEFAULT '{}',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gap_work ON gap(work_id, status);
        CREATE TABLE IF NOT EXISTS gap_transition (
            id          TEXT PRIMARY KEY,
            gap_id      TEXT NOT NULL REFERENCES gap(id) ON DELETE CASCADE,
            from_status TEXT NOT NULL,
            to_status   TEXT NOT NULL,
            reason      TEXT NOT NULL DEFAULT '',
            signed_by   TEXT NOT NULL DEFAULT '',
            at          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gap_transition_gap
            ON gap_transition(gap_id, at);
        CREATE TABLE IF NOT EXISTS hygiene_dismissal (
            id          TEXT PRIMARY KEY,
            work_id     TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            finding_key TEXT NOT NULL,
            reason      TEXT NOT NULL DEFAULT '',
            signed_by   TEXT NOT NULL DEFAULT '',
            at          TEXT NOT NULL,
            UNIQUE(work_id, finding_key)
        )
    """,
    ),
    # v135 — Gap Engine G-M3/G-M4: golden-oracle labels + detector measurements
    #   gap_oracle_label        — hand-annotated three-way labels (is_gap /
    #                             is_not_gap / unknown), author-signed; the
    #                             golden oracle detectors are measured against.
    #   gap_detector_measurement — persisted open-world harness runs (precision,
    #                             recall, kappa, frequency strata); a detector
    #                             may only produce blocking-severity gaps once a
    #                             measurement with enough labels exists.
    (
        135,
        "Golden oracle labels and open-world detector measurements",
        """
        CREATE TABLE IF NOT EXISTS gap_oracle_label (
            id         TEXT PRIMARY KEY,
            work_id    TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            detector   TEXT NOT NULL,
            pair_key   TEXT NOT NULL,
            label      TEXT NOT NULL CHECK (label IN
                           ('is_gap','is_not_gap','unknown')),
            frequency  INTEGER NOT NULL DEFAULT 0,
            note       TEXT NOT NULL DEFAULT '',
            signed_by  TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(work_id, detector, pair_key)
        );
        CREATE INDEX IF NOT EXISTS idx_gap_oracle_detector
            ON gap_oracle_label(detector, label);
        CREATE TABLE IF NOT EXISTS gap_detector_measurement (
            id                 TEXT PRIMARY KEY,
            detector           TEXT NOT NULL,
            n_labeled          INTEGER NOT NULL,
            n_unknown_excluded INTEGER NOT NULL DEFAULT 0,
            precision_overall  REAL,
            recall_overall     REAL,
            f1_overall         REAL,
            kappa              REAL,
            strata             TEXT NOT NULL DEFAULT '{}',
            measured_at        TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gap_measurement_detector
            ON gap_detector_measurement(detector, measured_at)
    """,
    ),
    # v136 — measurements are bound to the oracle they were computed over: a
    # fingerprint of the scoreable label set at measurement time.  Relabeling
    # changes the fingerprint, so stale measurements stop unlocking blocking
    # status automatically.
    (
        136,
        "Bind detector measurements to an oracle-label fingerprint",
        """
        ALTER TABLE gap_detector_measurement
            ADD COLUMN labels_fingerprint TEXT NOT NULL DEFAULT ''
    """,
    ),
    # v137 — Domain Model for the interpretive layer (G-M5/G-M6, rescoped).
    #   domain_source          — reference-structure documents (TOCs, syllabi,
    #                            lexica, bibliographies) registered per Work +
    #                            domain; independence = distinct documents.
    #   domain_node            — nodes harvested from those structures.
    #                            Proposal-only: nothing generates a gap until a
    #                            node is ratified with a signature.  node_class:
    #                            required (intersection across >=3 independent
    #                            sources), optional (union minus intersection),
    #                            contested (structural disagreement -> G4).
    #   domain_node_transition — ledger of every status change, signed.
    (
        137,
        "Domain Model: reference sources, triangulated nodes, signed transitions",
        """
        CREATE TABLE IF NOT EXISTS domain_source (
            id         TEXT PRIMARY KEY,
            work_id    TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            domain     TEXT NOT NULL,
            doc_id     TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            kind       TEXT NOT NULL DEFAULT 'structure' CHECK (kind IN
                           ('structure','bibliography')),
            created_at TEXT NOT NULL,
            UNIQUE(work_id, domain, doc_id)
        );
        CREATE INDEX IF NOT EXISTS idx_domain_source_work
            ON domain_source(work_id, domain);
        CREATE TABLE IF NOT EXISTS domain_node (
            id            TEXT PRIMARY KEY,
            work_id       TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            domain        TEXT NOT NULL,
            node_key      TEXT NOT NULL,
            label         TEXT NOT NULL,
            parent_key    TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN
                              ('proposed','ratified','rejected')),
            node_class    TEXT NOT NULL DEFAULT 'optional' CHECK (node_class IN
                              ('required','optional','contested')),
            agreement     INTEGER NOT NULL DEFAULT 0,
            source_count  INTEGER NOT NULL DEFAULT 0,
            sources       TEXT NOT NULL DEFAULT '[]',
            centrality    INTEGER NOT NULL DEFAULT 0,
            ratified_by   TEXT NOT NULL DEFAULT '',
            ratified_at   TEXT,
            status_reason TEXT NOT NULL DEFAULT '',
            meta          TEXT NOT NULL DEFAULT '{}',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            UNIQUE(work_id, domain, node_key)
        );
        CREATE INDEX IF NOT EXISTS idx_domain_node_work
            ON domain_node(work_id, domain, status);
        CREATE TABLE IF NOT EXISTS domain_node_transition (
            id          TEXT PRIMARY KEY,
            node_id     TEXT NOT NULL REFERENCES domain_node(id) ON DELETE CASCADE,
            from_status TEXT NOT NULL,
            to_status   TEXT NOT NULL,
            reason      TEXT NOT NULL DEFAULT '',
            signed_by   TEXT NOT NULL,
            at          TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_domain_transition_node
            ON domain_node_transition(node_id, at)
    """,
    ),
    # v138 — Concept item bank (T-M3, front half).  Stores per-concept study
    # items imported from a research run's training plan: the verification
    # question ("you know it when you can answer") plus the read/check/evidence
    # /schedule fields, preserving the six-field plan-item shape.  Question
    # generation may use these as grounded items later (depth ladder); today
    # they make an imported curriculum self-contained.
    (
        138,
        "Concept item bank: imported training-plan items per concept",
        """
        CREATE TABLE IF NOT EXISTS work_concept_items (
            id            TEXT PRIMARY KEY,
            concept_id    TEXT NOT NULL REFERENCES work_concepts(id) ON DELETE CASCADE,
            question      TEXT NOT NULL,
            why           TEXT NOT NULL DEFAULT '',
            read_text     TEXT NOT NULL DEFAULT '',
            check_text    TEXT NOT NULL DEFAULT '',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            schedule_json TEXT NOT NULL DEFAULT '{}',
            source        TEXT NOT NULL DEFAULT 'training_plan',
            created_at    TEXT NOT NULL,
            UNIQUE(concept_id, question)
        );
        CREATE INDEX IF NOT EXISTS idx_wci_concept
            ON work_concept_items(concept_id)
    """,
    ),
    # v139 — Depth ladder, teach-back, and the reverse research loop (T-M4/5/6).
    # rubric_json stores the per-criterion grading record (atomic binary
    # criteria with extractive quotes) so every assessment above recall is
    # auditable.  research_requests is the reverse loop: a concept whose
    # repeated failure is diagnosed as "corpus insufficient" emits a request
    # naming what it needs; the next research run consumes open requests.
    # The partial unique index guarantees at most ONE open request per concept.
    (
        139,
        "Depth-ladder rubric records + research_requests reverse loop",
        """
        ALTER TABLE work_mastery ADD COLUMN rubric_json TEXT;
        CREATE TABLE IF NOT EXISTS research_requests (
            id            TEXT PRIMARY KEY,
            work_id       TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            concept_id    TEXT REFERENCES work_concepts(id) ON DELETE CASCADE,
            need          TEXT NOT NULL,
            diagnosis     TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            status        TEXT NOT NULL DEFAULT 'open',
            created_at    TEXT NOT NULL,
            resolved_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_rr_work ON research_requests(work_id, status);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_rr_open_concept
            ON research_requests(concept_id) WHERE status='open'
    """,
    ),
    # v140 — Issued-question binding for the depth ladder.  Assessments must be
    # bound to a question the SERVER issued at a server-derived level: one row
    # per concept holds the latest issued question + level, consumed (deleted)
    # single-use at assessment time.  Prevents a client from submitting an
    # arbitrary self-written "question" for ladder credit, and prevents a stale
    # answer being recorded at a different level than it was issued at.
    (
        140,
        "Issued-question binding for ladder assessments",
        """
        CREATE TABLE IF NOT EXISTS learning_issued_questions (
            concept_id TEXT PRIMARY KEY REFERENCES work_concepts(id) ON DELETE CASCADE,
            level      TEXT NOT NULL,
            question   TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """,
    ),
    # v141 — Honest coverage estimates replace coverage_pct.  The old number
    # measured the corpus against itself (chapters with "enough" items ÷
    # chapters) and was removed as self-referential.  coverage_json stores the
    # Chao1 + Good–Turing report (see capabilities/coverage_estimate.py):
    # per-class richness estimates, unseen counts with 95% CIs, and sampling
    # completeness as an UPPER bound.  coverage_pct is dropped, not kept
    # alongside — nothing may fall back to the self-referential metric.
    (
        141,
        "Replace work_gap_cache.coverage_pct with Chao1/Good–Turing coverage_json",
        """
        ALTER TABLE work_gap_cache ADD COLUMN coverage_json TEXT;
        ALTER TABLE work_gap_cache DROP COLUMN coverage_pct
    """,
    ),
    # v142 — Completeness assertions (brutal review §4.1): "I have all of X"
    # is knowledge with nowhere to live.  A gap and a completeness assertion
    # are the same record with opposite sign — this table stores the positive
    # polarity with the gap table's discipline: content-hash identity over
    # the region (work|class|scope), provenance, a REQUIRED author signature,
    # and an append-only transition ledger.  ``no_value=1`` records the
    # empty-but-complete case ("this relation is genuinely empty for this
    # entity, and that is complete").  Detectors consult active assertions
    # before emitting; the research path treats them as stopping conditions.
    (
        142,
        "Completeness assertions with both polarities (region closure store)",
        """
        CREATE TABLE IF NOT EXISTS completeness_assertion (
            id               TEXT PRIMARY KEY,
            work_id          TEXT REFERENCES works(id) ON DELETE CASCADE,
            gap_class        TEXT NOT NULL,
            scope            TEXT NOT NULL,
            unit             TEXT NOT NULL DEFAULT '',
            frame_node_id    TEXT NOT NULL DEFAULT '',
            frame_source_ref TEXT NOT NULL DEFAULT '',
            basis            TEXT NOT NULL,
            no_value         INTEGER NOT NULL DEFAULT 0,
            status           TEXT NOT NULL DEFAULT 'active'
                             CHECK(status IN ('active','retracted')),
            status_reason    TEXT NOT NULL DEFAULT '',
            signed_by        TEXT NOT NULL,
            meta             TEXT NOT NULL DEFAULT '{}',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_completeness_work_status
            ON completeness_assertion(work_id, status);
        CREATE TABLE IF NOT EXISTS completeness_transition (
            id           TEXT PRIMARY KEY,
            assertion_id TEXT NOT NULL
                         REFERENCES completeness_assertion(id) ON DELETE CASCADE,
            from_status  TEXT NOT NULL,
            to_status    TEXT NOT NULL,
            reason       TEXT NOT NULL DEFAULT '',
            signed_by    TEXT NOT NULL DEFAULT '',
            at           TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_completeness_transition
            ON completeness_transition(assertion_id, at)
    """,
    ),
    # v143 — PCWA over the world graph (brutal review Part 1, build item 6).
    #
    # 1. graph_relation_meta: mined per-(class, relation) statistics —
    #    functionality, per-relation card_k, and mined max cardinality —
    #    derived from graph_edge/graph_node counts and RE-DERIVABLE: every
    #    recompute replaces the Work's rows wholesale, so the metadata tracks
    #    the graph as it grows.  Detectors read this table, never re-mine.
    #
    # 2. completeness_assertion gains a 'proposed' status: PCWA closure
    #    inferences (functional-predicate closure, card_k oracles) are
    #    MACHINE-PROPOSED completeness knowledge — they accumulate, but they
    #    never suppress gaps and never auto-ratify.  Only a human signature
    #    moves proposed→active (see assert_completeness / ratify).  SQLite
    #    cannot alter a CHECK, so both tables are rebuilt; rows are copied
    #    verbatim and the child table is rebuilt after the parent so its FK
    #    points at the new table.
    (
        143,
        "PCWA relation metadata + machine-proposed completeness assertions",
        """
        CREATE TABLE IF NOT EXISTS graph_relation_meta (
            id            TEXT PRIMARY KEY,
            work_id       TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            node_type     TEXT NOT NULL,
            edge_type     TEXT NOT NULL,
            n_subjects    INTEGER NOT NULL,
            functional    INTEGER NOT NULL DEFAULT 0,
            functional_share REAL NOT NULL DEFAULT 0,
            card_k        INTEGER,
            max_cardinality INTEGER,
            max_cardinality_share REAL,
            value_histogram TEXT NOT NULL DEFAULT '{}',
            computed_at   TEXT NOT NULL,
            UNIQUE(work_id, node_type, edge_type)
        );
        CREATE INDEX IF NOT EXISTS idx_relation_meta_work
            ON graph_relation_meta(work_id);
        ALTER TABLE completeness_assertion RENAME TO completeness_assertion_v142;
        ALTER TABLE completeness_transition RENAME TO completeness_transition_v142;
        CREATE TABLE completeness_assertion (
            id               TEXT PRIMARY KEY,
            work_id          TEXT REFERENCES works(id) ON DELETE CASCADE,
            gap_class        TEXT NOT NULL,
            scope            TEXT NOT NULL,
            unit             TEXT NOT NULL DEFAULT '',
            frame_node_id    TEXT NOT NULL DEFAULT '',
            frame_source_ref TEXT NOT NULL DEFAULT '',
            basis            TEXT NOT NULL,
            no_value         INTEGER NOT NULL DEFAULT 0,
            status           TEXT NOT NULL DEFAULT 'active'
                             CHECK(status IN ('proposed','active','retracted')),
            status_reason    TEXT NOT NULL DEFAULT '',
            signed_by        TEXT NOT NULL,
            meta             TEXT NOT NULL DEFAULT '{}',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        );
        INSERT INTO completeness_assertion
            SELECT * FROM completeness_assertion_v142;
        CREATE TABLE completeness_transition (
            id           TEXT PRIMARY KEY,
            assertion_id TEXT NOT NULL
                         REFERENCES completeness_assertion(id) ON DELETE CASCADE,
            from_status  TEXT NOT NULL,
            to_status    TEXT NOT NULL,
            reason       TEXT NOT NULL DEFAULT '',
            signed_by    TEXT NOT NULL DEFAULT '',
            at           TEXT NOT NULL
        );
        INSERT INTO completeness_transition
            SELECT * FROM completeness_transition_v142;
        DROP TABLE completeness_transition_v142;
        DROP TABLE completeness_assertion_v142;
        CREATE INDEX IF NOT EXISTS idx_completeness_work_status
            ON completeness_assertion(work_id, status);
        CREATE INDEX IF NOT EXISTS idx_completeness_transition
            ON completeness_transition(assertion_id, at)
    """,
    ),
    # v144 — THE RE-PROJECTION Phases 1+2: collections + batch-Work demotion.
    #
    # 1. collection: the missing table.  An import batch (ZIP, folder, mail)
    #    is PROVENANCE — it answers "where did this come from" and nothing
    #    else.  It is never a subject: it may never seed a curriculum, enter
    #    a book pipeline, or scope a knowledge harvest (enforced in code by
    #    OrivellumDB.assert_not_collection + route guards, tested in
    #    tests/test_collections.py).
    #
    # 2. Demotion: every A01_MIGRATION_BATCH_* Work becomes a collection row
    #    (reusing the Work's id, so old references stay resolvable as
    #    provenance).  Its documents get collection_id set and work_id NULL.
    #    Knowledge keeps every row but loses the fake Work scope.  The four
    #    Works and their objects rows are deleted — trailers/genesis rows
    #    (the only NO-ACTION FKs on works) are cleaned first so the delete
    #    cannot be blocked.  The substrate (documents, chunks, vectors) is
    #    NEVER touched: document, chunk, and vector counts must be identical
    #    before and after (tested).
    #
    #    Deleting a demoted Work INTENTIONALLY cascades away its derived
    #    Work-domain rows (tasks, curriculum concepts, graph nodes, gap
    #    caches, book pipelines/chapters, publications, ...) — that derived
    #    layer IS the fake projection this migration exists to remove; only
    #    documents/chunks/vectors/knowledge are preserved.  Object-backed
    #    cascade children (tasks, publications, book_pipelines,
    #    book_chapters — the tables whose id REFERENCES objects(id) ON
    #    DELETE CASCADE) are removed via their objects rows BEFORE the works
    #    delete, because a works-side cascade would drop the child row but
    #    orphan its objects parent (governed-object ghosts).
    #
    # Every statement is idempotent (IF NOT EXISTS / OR IGNORE / re-runnable
    # UPDATE-DELETE) so the substrate-invariant test can replay it.
    (
        144,
        "Collections table + demote A01_MIGRATION_BATCH Works to collections",
        """
        CREATE TABLE IF NOT EXISTS collection (
            id             TEXT PRIMARY KEY,
            label          TEXT NOT NULL,
            source_kind    TEXT NOT NULL,
            source_ref     TEXT NOT NULL DEFAULT '',
            domain         TEXT,
            imported_at    TEXT NOT NULL,
            document_count INTEGER NOT NULL DEFAULT 0,
            meta           TEXT NOT NULL DEFAULT '{}'
        );
        ALTER TABLE documents ADD COLUMN collection_id TEXT
            REFERENCES collection(id) ON DELETE SET NULL;
        CREATE INDEX IF NOT EXISTS docs_collection ON documents(collection_id);
        INSERT OR IGNORE INTO collection
            (id, label, source_kind, source_ref, domain, imported_at,
             document_count, meta)
        SELECT w.id, w.title, 'zip', w.title, NULL,
               COALESCE((SELECT o.created_at FROM objects o WHERE o.id = w.id),
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
               (SELECT COUNT(*) FROM documents d WHERE d.work_id = w.id),
               '{"demoted_from_work": true}'
          FROM works w
         WHERE w.title LIKE 'A01\\_MIGRATION\\_BATCH\\_%' ESCAPE '\\';
        UPDATE documents SET collection_id = work_id
         WHERE work_id IN (SELECT id FROM collection);
        UPDATE documents SET work_id = NULL
         WHERE work_id IN (SELECT id FROM collection);
        UPDATE knowledge SET work_id = NULL
         WHERE work_id IN (SELECT id FROM collection);
        UPDATE knowledge_fts SET work_id = NULL
         WHERE work_id IN (SELECT id FROM collection);
        DELETE FROM works_fts
         WHERE work_id IN (SELECT id FROM collection);
        DELETE FROM trailers
         WHERE work_id IN (SELECT id FROM collection);
        DELETE FROM genesis_stages
         WHERE book_id IN (SELECT id FROM genesis_books
                            WHERE work_id IN (SELECT id FROM collection));
        DELETE FROM genesis_artifacts
         WHERE book_id IN (SELECT id FROM genesis_books
                            WHERE work_id IN (SELECT id FROM collection));
        DELETE FROM genesis_ledger
         WHERE book_id IN (SELECT id FROM genesis_books
                            WHERE work_id IN (SELECT id FROM collection));
        DELETE FROM genesis_books
         WHERE work_id IN (SELECT id FROM collection);
        DELETE FROM objects
         WHERE id IN (SELECT id FROM tasks
                       WHERE work_id IN (SELECT id FROM collection));
        DELETE FROM objects
         WHERE id IN (SELECT id FROM publications
                       WHERE work_id IN (SELECT id FROM collection));
        DELETE FROM objects
         WHERE id IN (SELECT id FROM book_chapters
                       WHERE work_id IN (SELECT id FROM collection));
        DELETE FROM objects
         WHERE id IN (SELECT id FROM book_pipelines
                       WHERE work_id IN (SELECT id FROM collection));
        DELETE FROM works
         WHERE id IN (SELECT id FROM collection);
        DELETE FROM objects
         WHERE type = 'work' AND id IN (SELECT id FROM collection)
    """,
    ),
    # v145 — Document doc_type dimension (THE RE-PROJECTION Phase 3)
    #
    # Tier answers "may this become a Work"; doc_type answers "which ontology
    # and which pipeline apply".  doc_type_by records provenance:
    # 'rule:<name>' (deterministic), 'model' (LLM proposal), 'author' (human).
    # pending_reclassify grows proposal columns so tier/doc_type backfill runs
    # as reviewable proposals ratified in the Review Queue — never a direct
    # mutation of classification.  Provenance is PER PROPOSED FIELD (a tier
    # proposal from a rule and a doc_type proposal from the model can share
    # one row without overwriting each other's origin).
    (
        145,
        "Document doc_type + doc_type_by; pending_reclassify proposal columns",
        """
        ALTER TABLE documents ADD COLUMN doc_type TEXT;
        ALTER TABLE documents ADD COLUMN doc_type_by TEXT;
        CREATE INDEX IF NOT EXISTS docs_doc_type ON documents(doc_type);
        ALTER TABLE pending_reclassify ADD COLUMN proposed_tier TEXT;
        ALTER TABLE pending_reclassify ADD COLUMN proposed_doc_type TEXT;
        ALTER TABLE pending_reclassify ADD COLUMN proposed_tier_by TEXT;
        ALTER TABLE pending_reclassify ADD COLUMN proposed_doc_type_by TEXT
    """,
    ),
    # v146 — Work proposals + collection provenance (THE RE-PROJECTION Phase 4)
    #
    # Real subjects are DERIVED from content clusters and land in the review
    # queue as work_proposals rows.  A Work is only ever created through a
    # signed ratification of one of these proposals (or the pre-existing
    # manual creation path).  fingerprint is a deterministic hash of the
    # sorted member doc ids so re-running the clustering pass upserts the
    # same proposal instead of stacking duplicates — and a ratified or
    # rejected row is never clobbered by a re-run.
    # work_collections records which import collections contributed documents
    # to a ratified Work (provenance shown on the Work detail page).
    # works.domain is the author-chosen ontology domain picked at ratification
    # (narrative | technical | governance | reference).
    (
        146,
        "work_proposals + work_collections provenance + works.domain",
        """
        ALTER TABLE works ADD COLUMN domain TEXT;
        CREATE TABLE IF NOT EXISTS work_proposals (
            id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'proposed',
            suggested_name TEXT NOT NULL,
            name_source TEXT NOT NULL,
            size INTEGER NOT NULL,
            member_doc_ids TEXT NOT NULL,
            exemplar_doc_ids TEXT NOT NULL,
            dominant_doc_type TEXT,
            collection_spread TEXT NOT NULL,
            cluster_stats TEXT NOT NULL,
            domain TEXT,
            work_id TEXT,
            resolved_by TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS work_proposals_status ON work_proposals(status);
        CREATE TABLE IF NOT EXISTS work_collections (
            work_id TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            doc_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (work_id, collection_id)
        );
        CREATE INDEX IF NOT EXISTS work_collections_work ON work_collections(work_id)
    """,
    ),
    # v147 — Quarantine the pre-reprojection harvest (THE RE-PROJECTION Phase 6)
    #
    # The existing machine-extracted knowledge (review_status 'auto'/'ai_auto')
    # was harvested from unclassified documents under one global fiction
    # ontology, scoped to import batches.  It cannot be salvaged by
    # relabelling.  It is NOT deleted — it is evidence of what the old harvest
    # did — but it is quarantined: excluded from chat context, search, review
    # queue, and question/answer grounding.  The prior status is preserved in
    # meta.pre_quarantine_status so the evidence remains interpretable.
    #
    # Human-approved items survive and follow their documents: their work_id
    # is re-pointed to the source document's CURRENT Work (ratification may
    # have re-grouped the documents since the item was approved).
    #
    # Substrate invariant: this migration touches ONLY the knowledge
    # projection — zero document, chunk, or vector rows change.
    (
        147,
        "quarantine pre-reprojection machine knowledge; re-point approved items",
        """
        UPDATE knowledge
           SET meta = json_set(COALESCE(meta,'{}'), '$.pre_quarantine_status', review_status),
               review_status = 'quarantined_reprojection'
         WHERE review_status IN ('auto','ai_auto');
        UPDATE knowledge
           SET work_id = (SELECT d.work_id FROM documents d WHERE d.id = knowledge.source_doc_id)
         WHERE review_status = 'approved'
           AND source_doc_id IS NOT NULL
           AND EXISTS (SELECT 1 FROM documents d WHERE d.id = knowledge.source_doc_id)
    """,
    ),
    # v148 — Lifecycle designation provenance (THE RE-PROJECTION Phases 7-8)
    #
    # Canonical status is an authored act, not a computed one.  lifecycle_by
    # records WHO set the document's current lifecycle: 'author' (a human via
    # the UI) or 'system' (auto-dedup / pipeline machinery).  Legacy rows stay
    # NULL — unknown provenance is NOT author provenance, so pre-existing
    # canonical designations do not silently count as author-signed.
    (
        148,
        "add documents.lifecycle_by — provenance of the current lifecycle designation",
        """
        ALTER TABLE documents ADD COLUMN lifecycle_by TEXT
    """,
    ),
]

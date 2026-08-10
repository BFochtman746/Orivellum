-- ============================================================================
-- WR-03 SCHEMA ADDITIONS — Canon & Continuity
-- Applied by db.init_db() after schema.sql.
-- All new tables use IF NOT EXISTS; existing-table column additions are
-- handled by _apply_wr03_migrations() which guards against double-application.
-- ============================================================================

-- Entity aliases: every valid name by which a canon_entity may be referenced.
-- The continuity validator checks that chapter contracts never use an alias
-- that isn't registered here (name_drift validator).
CREATE TABLE IF NOT EXISTS entity_alias (
    id          TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL REFERENCES canon_entity(id),
    alias       TEXT NOT NULL,
    alias_type  TEXT NOT NULL DEFAULT 'name'
                  CHECK (alias_type IN
                    ('name','title','epithet','transliteration','nickname')),
    created_utc TEXT NOT NULL,
    UNIQUE (entity_id, alias)
);

-- Entity locations: where an entity is at a given date.
-- The impossible-travel validator flags an entity in two different locations
-- on the same date_ref.
CREATE TABLE IF NOT EXISTS entity_location (
    id          TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL REFERENCES canon_entity(id),
    date_ref    TEXT NOT NULL,   -- sortable date string; BCE dates use "NNNN BCE"
    location    TEXT NOT NULL,   -- place name or canon_entity.id for a place entity
    scene_ref   TEXT,            -- optional human label ("Chapter 3, Scene 2")
    created_utc TEXT NOT NULL
);

-- Knowledge states: what a character knows and the earliest scene from which
-- they can know it. The knowledge_leak validator flags a contract that accesses
-- knowledge before the character can possess it.
CREATE TABLE IF NOT EXISTS knowledge_state (
    id                  TEXT PRIMARY KEY,
    entity_id           TEXT NOT NULL REFERENCES canon_entity(id),
    fact_description    TEXT NOT NULL,
    can_know_from_scene TEXT NOT NULL,   -- human label of the earliest scene
    scene_sequence      INTEGER NOT NULL, -- numeric ordering for comparison
    source_event        TEXT,            -- what event grants this knowledge
    created_utc         TEXT NOT NULL
);

-- Join: which knowledge facts a chapter contract's POV character accesses,
-- and at what scene sequence.  Used by knowledge_leak validator.
CREATE TABLE IF NOT EXISTS contract_knowledge_access (
    id                  TEXT PRIMARY KEY,
    contract_id         TEXT NOT NULL REFERENCES chapter_contract(id),
    knowledge_state_id  TEXT NOT NULL REFERENCES knowledge_state(id),
    scene_sequence      INTEGER NOT NULL,  -- the accessing scene's own sequence
    created_utc         TEXT NOT NULL,
    UNIQUE (contract_id, knowledge_state_id)
);

-- Join: which entity names are used inside a chapter contract.
-- Used by name_drift validator to catch unregistered aliases.
CREATE TABLE IF NOT EXISTS chapter_contract_entity_ref (
    id          TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL REFERENCES chapter_contract(id),
    entity_id   TEXT NOT NULL REFERENCES canon_entity(id),
    name_used   TEXT NOT NULL,   -- the name as it appears in the chapter
    created_utc TEXT NOT NULL,
    UNIQUE (contract_id, entity_id, name_used)
);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS ix_entity_alias_entity   ON entity_alias(entity_id);
CREATE INDEX IF NOT EXISTS ix_entity_location_entity ON entity_location(entity_id, date_ref);
CREATE INDEX IF NOT EXISTS ix_knowledge_state_entity ON knowledge_state(entity_id);
CREATE INDEX IF NOT EXISTS ix_cka_contract           ON contract_knowledge_access(contract_id);
CREATE INDEX IF NOT EXISTS ix_ccer_contract          ON chapter_contract_entity_ref(contract_id);

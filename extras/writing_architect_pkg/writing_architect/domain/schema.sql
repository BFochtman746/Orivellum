-- ============================================================================
-- WRITING_ARCHITECT — Book Production Operating System
-- Canonical Data Model  (spec section 4)  +  Minimum Constraints (spec 11.3)
-- ----------------------------------------------------------------------------
-- Local-first sovereign build: SQLite with foreign keys, CHECK constraints and
-- triggers so the governance rules are enforced by the *database*, not by a
-- prompt. The spec's recommended production target is PostgreSQL; every
-- construct here is chosen to port cleanly (see docs/GUIDE for the migration
-- note). Enable enforcement per connection:  PRAGMA foreign_keys = ON;
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Lifecycle vocabulary (spec 3.2). Stored as a table so transitions can FK to
-- it and so the ordinal (position) is queryable for the "no downstream before
-- upstream" hard rule.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lifecycle_state (
    code        TEXT PRIMARY KEY,          -- B0..B13
    ordinal     INTEGER NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    exit_condition TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- BookProject — stable identity (spec 4)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS book_project (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    author        TEXT NOT NULL,
    form          TEXT,                     -- e.g. "biblical historical fiction"
    audience      TEXT,
    reader_promise TEXT,
    scope         TEXT,
    state         TEXT NOT NULL DEFAULT 'B0'
                    REFERENCES lifecycle_state(code),
    created_utc   TEXT NOT NULL,
    updated_utc   TEXT NOT NULL
);

-- Edition — version lineage (spec 4)
CREATE TABLE IF NOT EXISTS edition (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    label         TEXT NOT NULL,
    branch        TEXT,
    release_target TEXT,
    authority_status TEXT NOT NULL DEFAULT 'WORKING'
                    CHECK (authority_status IN
                      ('WORKING','CANONICAL','HISTORICAL','SUPERSEDED')),
    supersedes    TEXT REFERENCES edition(id),
    supersession_rationale TEXT,
    created_utc   TEXT NOT NULL,
    -- spec 11.3: no authority designation without supersession rationale
    CHECK (authority_status <> 'CANONICAL'
           OR supersedes IS NULL
           OR supersession_rationale IS NOT NULL)
);

-- SourceArtifact — original file with hash & authority (spec 4)
CREATE TABLE IF NOT EXISTS source_artifact (
    id            TEXT PRIMARY KEY,
    book_id       TEXT REFERENCES book_project(id),
    logical_path  TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    origin        TEXT,
    rights        TEXT,
    extraction_status TEXT DEFAULT 'PENDING',
    disposition   TEXT CHECK (disposition IN
                    ('CANONICAL','SUPPORTING','HISTORICAL','DUPLICATE',
                     'DERIVATIVE','IMPLEMENTATION','REJECTED','PACKAGING')),
    disposition_reason TEXT,
    is_released   INTEGER NOT NULL DEFAULT 0,   -- immutability flag
    created_utc   TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Research spine (spec 4, 5): Question -> Source -> Claim -> Evidence -> Conflict
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research_question (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    question      TEXT NOT NULL,
    decision_informed TEXT,                 -- spec 5.2 step 1
    scope         TEXT,
    priority      INTEGER DEFAULT 3,
    sufficiency_criteria TEXT,
    state         TEXT NOT NULL DEFAULT 'OPEN'
                    CHECK (state IN ('OPEN','SATURATED','CLOSED')),
    created_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    citation      TEXT NOT NULL,
    edition       TEXT,                     -- translation/edition/witness
    pub_date      TEXT,
    tier          TEXT NOT NULL             -- spec 5.1 source hierarchy
                    CHECK (tier IN ('T1','T2','T3','T4','T5','T6','T7')),
    reliability   TEXT,
    rights        TEXT,
    retrieval_record TEXT,
    created_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    question_id   TEXT REFERENCES research_question(id),
    proposition   TEXT NOT NULL,
    claim_type    TEXT NOT NULL DEFAULT 'fact'
                    CHECK (claim_type IN
                      ('fact','interpretation','tradition','creative_interpolation')),
    confidence    TEXT NOT NULL DEFAULT 'possible'
                    CHECK (confidence IN
                      ('confirmed','probable','possible','disputed','unknown',
                       'invented_for_fiction')),
    temporal_validity TEXT,
    -- a claim is only "accepted" into canon after the gate passes (spec 5.3)
    accepted      INTEGER NOT NULL DEFAULT 0,
    verifier      TEXT,                     -- independent reviewer (spec 8.1)
    created_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_unit (
    id            TEXT PRIMARY KEY,
    claim_id      TEXT NOT NULL REFERENCES claim(id),
    source_id     TEXT NOT NULL REFERENCES source(id),
    passage       TEXT NOT NULL,
    location_ref  TEXT NOT NULL,            -- spec 11.3: no quotation w/o location
    stance        TEXT NOT NULL DEFAULT 'supports'
                    CHECK (stance IN ('supports','qualifies','contradicts')),
    created_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conflict (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    claim_a       TEXT NOT NULL REFERENCES claim(id),
    claim_b       TEXT NOT NULL REFERENCES claim(id),
    reason        TEXT,
    adjudication  TEXT,
    unresolved_risk TEXT,
    resolved      INTEGER NOT NULL DEFAULT 0,
    created_utc   TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Canon & continuity (spec 4)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS canon_entity (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    kind          TEXT NOT NULL             -- person/place/object/institution/concept
                    CHECK (kind IN ('person','place','object','institution','concept')),
    name          TEXT NOT NULL,
    created_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canon_fact (
    id            TEXT PRIMARY KEY,
    entity_id     TEXT NOT NULL REFERENCES canon_entity(id),
    fact          TEXT NOT NULL,
    time_start    TEXT,
    time_end      TEXT,
    evidence_claim TEXT REFERENCES claim(id),
    created_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS timeline_event (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    label         TEXT NOT NULL,
    date_start    TEXT,
    date_end      TEXT,
    participants  TEXT,
    causes        TEXT,
    effects       TEXT,
    uncertainty   TEXT,
    created_utc   TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Architecture: plan tree + chapter/scene contracts (spec 4, 6)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plan_node (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    parent_id     TEXT REFERENCES plan_node(id),
    node_type     TEXT NOT NULL             -- promise/part/chapter/scene/beat/claim
                    CHECK (node_type IN
                      ('promise','part','chapter','scene','beat','claim')),
    purpose       TEXT,
    state         TEXT NOT NULL DEFAULT 'PROPOSED'
                    CHECK (state IN ('PROPOSED','APPROVED','CHANGE_REQUESTED')),
    created_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapter_contract (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    plan_node_id  TEXT REFERENCES plan_node(id),
    version       INTEGER NOT NULL DEFAULT 1,
    purpose       TEXT NOT NULL,
    reader_state_change TEXT,
    structural_role TEXT,
    required_beats TEXT,
    forbidden_content TEXT,
    pov_knowledge_state TEXT,
    time_location TEXT,
    voice_profile_id TEXT,
    dependencies  TEXT,
    target_min    INTEGER,
    target_max    INTEGER,
    acceptance_tests TEXT,
    open_decisions TEXT,
    approved      INTEGER NOT NULL DEFAULT 0,   -- gate for drafting (spec 11.3)
    created_utc   TEXT NOT NULL
);

-- Evidence packet: which approved claims a contract is allowed to use in prose
CREATE TABLE IF NOT EXISTS contract_evidence (
    contract_id   TEXT NOT NULL REFERENCES chapter_contract(id),
    claim_id      TEXT NOT NULL REFERENCES claim(id),
    PRIMARY KEY (contract_id, claim_id)
);

-- ---------------------------------------------------------------------------
-- Drafting & editorial (spec 4, 7, 8)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS draft_unit (
    id            TEXT PRIMARY KEY,
    contract_id   TEXT NOT NULL REFERENCES chapter_contract(id),
    version       INTEGER NOT NULL DEFAULT 1,
    prose         TEXT NOT NULL,
    provenance_id TEXT,                     -- -> generation_event
    is_released   INTEGER NOT NULL DEFAULT 0,
    created_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS style_rule (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    profile       TEXT NOT NULL,            -- named StyleProfile (spec 12.2 Voice)
    rule          TEXT NOT NULL,
    positive_example TEXT,
    negative_example TEXT,
    created_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS editorial_finding (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    draft_unit_id TEXT REFERENCES draft_unit(id),
    pass_type     TEXT NOT NULL             -- developmental/continuity/factual/line/copy/proof
                    CHECK (pass_type IN
                      ('developmental','continuity','factual','sensitivity',
                       'line','copy','proof')),
    severity      TEXT NOT NULL             -- spec 9.1
                    CHECK (severity IN
                      ('blocker','critical','major','minor','observation')),
    location      TEXT,
    evidence      TEXT,
    proposed_resolution TEXT,
    state         TEXT NOT NULL DEFAULT 'OPEN'
                    CHECK (state IN ('OPEN','RESOLVED','WAIVED')),
    raised_by     TEXT NOT NULL,            -- worker/reviewer id
    resolved_by   TEXT,                     -- must differ from raised_by (spec 11.3)
    waiver_rationale TEXT,
    created_utc   TEXT NOT NULL,
    -- spec 8.1 / 11.3: no worker may close its OWN blocking finding
    CHECK (state <> 'RESOLVED'
           OR severity NOT IN ('blocker','critical')
           OR (resolved_by IS NOT NULL AND resolved_by <> raised_by))
);

CREATE TABLE IF NOT EXISTS evaluation_observation (
    id            TEXT PRIMARY KEY,
    draft_unit_id TEXT REFERENCES draft_unit(id),
    dimension     TEXT NOT NULL,            -- e.g. a FORGE dimension (spec 8.2)
    result        TEXT NOT NULL,
    evidence_span TEXT,
    confidence    REAL,                     -- 0..1, atomic + calibrated
    evaluator     TEXT NOT NULL,
    rubric_version TEXT,
    created_utc   TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Approvals, release, provenance & audit (spec 4, 9, 10)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approval (
    id            TEXT PRIMARY KEY,
    object_type   TEXT NOT NULL,
    object_id     TEXT NOT NULL,
    decision      TEXT NOT NULL CHECK (decision IN ('approve','reject')),
    authority     TEXT NOT NULL,
    scope         TEXT,
    conditions    TEXT,
    decided_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS release_candidate (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    edition_id    TEXT REFERENCES edition(id),
    frozen_utc    TEXT NOT NULL,
    gates_passed  INTEGER NOT NULL DEFAULT 0,
    created_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS release_manifest (
    id            TEXT PRIMARY KEY,
    candidate_id  TEXT NOT NULL REFERENCES release_candidate(id),
    hashes        TEXT NOT NULL,
    versions      TEXT,
    tools         TEXT,
    models        TEXT,
    approvals     TEXT,
    test_results  TEXT,
    author_signoff TEXT,                    -- spec 9.2 step 10
    created_utc   TEXT NOT NULL
);

-- Provenance (spec 10)
CREATE TABLE IF NOT EXISTS generation_event (
    id            TEXT PRIMARY KEY,
    worker        TEXT NOT NULL,
    model         TEXT NOT NULL,
    parameters    TEXT,
    prompt_hash   TEXT,
    input_object_ids TEXT,
    created_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS human_edit (
    id            TEXT PRIMARY KEY,
    draft_unit_id TEXT REFERENCES draft_unit(id),
    editor        TEXT NOT NULL,
    diff          TEXT,
    rationale     TEXT,
    created_utc   TEXT NOT NULL
);

-- Append-only audit ledger. A trigger forbids UPDATE and DELETE.
CREATE TABLE IF NOT EXISTS audit_log (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc        TEXT NOT NULL,
    actor         TEXT NOT NULL,
    action        TEXT NOT NULL,
    object_type   TEXT,
    object_id     TEXT,
    detail        TEXT,
    prev_hash     TEXT,                     -- hash chain for tamper-evidence
    entry_hash    TEXT
);

-- Lifecycle transition record (spec 11.3: no transition w/o actor + record)
CREATE TABLE IF NOT EXISTS lifecycle_transition (
    id            TEXT PRIMARY KEY,
    book_id       TEXT NOT NULL REFERENCES book_project(id),
    from_state    TEXT NOT NULL REFERENCES lifecycle_state(code),
    to_state      TEXT NOT NULL REFERENCES lifecycle_state(code),
    actor         TEXT NOT NULL,
    reason        TEXT,
    ts_utc        TEXT NOT NULL
);

-- ===========================================================================
-- TRIGGERS — enforce the constraints that a single-row CHECK cannot express
-- ===========================================================================

-- 11.3: No factual Claim accepted without at least one EvidenceUnit.
CREATE TRIGGER IF NOT EXISTS trg_claim_accept_requires_evidence
BEFORE UPDATE OF accepted ON claim
WHEN NEW.accepted = 1 AND NEW.claim_type = 'fact'
     AND (SELECT COUNT(*) FROM evidence_unit
          WHERE claim_id = NEW.id AND stance = 'supports') = 0
BEGIN
    SELECT RAISE(ABORT,
      'POLICY FM-07: cannot accept a factual claim with no supporting evidence');
END;

-- 11.3: No DraftUnit without an approved ChapterContract.
CREATE TRIGGER IF NOT EXISTS trg_draft_requires_approved_contract
BEFORE INSERT ON draft_unit
WHEN (SELECT approved FROM chapter_contract WHERE id = NEW.contract_id) IS NOT 1
BEGIN
    SELECT RAISE(ABORT,
      'POLICY FM-09: cannot draft against an unapproved chapter contract');
END;

-- 11.3: No source quotation without edition/location metadata.
CREATE TRIGGER IF NOT EXISTS trg_evidence_requires_location
BEFORE INSERT ON evidence_unit
WHEN NEW.location_ref IS NULL OR TRIM(NEW.location_ref) = ''
BEGIN
    SELECT RAISE(ABORT,
      'POLICY: evidence unit requires an exact location reference');
END;

-- 11.3: No mutable overwrite of a released artifact (draft_unit).
CREATE TRIGGER IF NOT EXISTS trg_no_overwrite_released_draft
BEFORE UPDATE ON draft_unit
WHEN OLD.is_released = 1
BEGIN
    SELECT RAISE(ABORT,
      'POLICY: released draft units are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_no_overwrite_released_source
BEFORE UPDATE ON source_artifact
WHEN OLD.is_released = 1
BEGIN
    SELECT RAISE(ABORT,
      'POLICY: released source artifacts are immutable');
END;

-- Audit ledger is append-only.
CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'POLICY: audit_log is append-only (update forbidden)');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'POLICY: audit_log is append-only (delete forbidden)');
END;

-- Helpful indexes
CREATE INDEX IF NOT EXISTS ix_source_artifact_sha ON source_artifact(sha256);
CREATE INDEX IF NOT EXISTS ix_claim_book ON claim(book_id);
CREATE INDEX IF NOT EXISTS ix_evidence_claim ON evidence_unit(claim_id);
CREATE INDEX IF NOT EXISTS ix_finding_book ON editorial_finding(book_id, state, severity);
CREATE INDEX IF NOT EXISTS ix_plan_parent ON plan_node(parent_id);

-- ORIVELLUM NEXT — schema
-- Two producers, one contract:
--   clarify_request  stands in front of costly work and refuses to guess
--   next_action      turns a finished answer into one tap OR one queued unit
--
-- The same next_action row feeds the UI chips and the runner. That is the
-- whole point: the thing that decides what is next must not care whether a
-- human taps it or a worker picks it up.

PRAGMA foreign_keys = ON;

-- ─── clarify gate ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS clarify_request (
  id            TEXT PRIMARY KEY,
  thread_id     TEXT NOT NULL,
  target        TEXT NOT NULL,           -- what would run: 'reharvest:work=<id>'
  -- cost is mandatory. A gate with no stated cost is friction with no payoff.
  cost_units    INTEGER,                 -- documents / chapters / files
  cost_minutes  INTEGER,
  cost_replaces TEXT NOT NULL DEFAULT '',-- what gets overwritten, in words
  reversible    INTEGER NOT NULL,        -- 0/1. 0 => never auto-runs.
  state         TEXT NOT NULL DEFAULT 'open'
                 CHECK (state IN ('open','answered','skipped','cancelled','expired')),
  created_at    TEXT NOT NULL,
  closed_at     TEXT
);

CREATE TABLE IF NOT EXISTS clarify_facet (
  id             TEXT PRIMARY KEY,
  request_id     TEXT NOT NULL REFERENCES clarify_request(id) ON DELETE CASCADE,
  seq            INTEGER NOT NULL,       -- 1..3, hard ceiling enforced in code
  name           TEXT NOT NULL,          -- 'ontology' | 'scope' | 'authority'
  question       TEXT NOT NULL,
  why            TEXT NOT NULL,          -- anchored in a real observation
  -- THE SIGNATURE: every facet must disclose what happens if it is skipped.
  default_value  TEXT NOT NULL,
  default_source TEXT NOT NULL,          -- where that default comes from
  default_risk   TEXT NOT NULL DEFAULT '', -- plain words, '' when benign
  allow_freeform INTEGER NOT NULL DEFAULT 1,
  resolved_value TEXT,
  resolved_kind  TEXT CHECK (resolved_kind IN ('option','freeform','default')),
  resolved_at    TEXT,
  UNIQUE (request_id, seq)
);

CREATE TABLE IF NOT EXISTS clarify_option (
  id        TEXT PRIMARY KEY,
  facet_id  TEXT NOT NULL REFERENCES clarify_facet(id) ON DELETE CASCADE,
  seq       INTEGER NOT NULL,
  label     TEXT NOT NULL,
  value     TEXT NOT NULL,
  hint      TEXT NOT NULL DEFAULT '',    -- the small mono note under the label
  UNIQUE (facet_id, seq)
);

-- ─── next actions ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS next_action_set (
  id            TEXT PRIMARY KEY,
  thread_id     TEXT NOT NULL,
  from_message  TEXT NOT NULL,           -- the answer these were derived from
  -- When the recommender has nothing that earns a recommendation it says so
  -- here rather than promoting a weak option.
  no_recommendation_reason TEXT NOT NULL DEFAULT '',
  state         TEXT NOT NULL DEFAULT 'offered'
                 CHECK (state IN ('offered','spent','expired')),
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS next_action (
  id            TEXT PRIMARY KEY,
  set_id        TEXT NOT NULL REFERENCES next_action_set(id) ON DELETE CASCADE,
  seq           INTEGER NOT NULL,
  kind          TEXT NOT NULL CHECK (kind IN ('narrow','widen','act','clarify')),
  label         TEXT NOT NULL,           -- the chip text
  prompt        TEXT NOT NULL,           -- what is sent if selected (editable)
  -- Law 3 carried over from the book work: evidence or it did not happen.
  anchor        TEXT NOT NULL,           -- human-readable: 'from the 9,142 …'
  anchor_ref    TEXT NOT NULL,           -- structured: 'documents.tier=source:9142'
  recommended   INTEGER NOT NULL DEFAULT 0,
  rationale     TEXT NOT NULL DEFAULT '',-- required when recommended=1
  confidence    REAL,
  cost_units    INTEGER,
  cost_minutes  INTEGER,
  reversible    INTEGER NOT NULL DEFAULT 0,
  needs_clarify INTEGER NOT NULL DEFAULT 0,
  blocked_by    TEXT NOT NULL DEFAULT '',-- open gate/finding that must clear first
  -- COMPUTED, never supplied by a model. See nextaction.compute_auto_runnable.
  auto_runnable INTEGER NOT NULL DEFAULT 0,
  auto_reason   TEXT NOT NULL DEFAULT '',
  state         TEXT NOT NULL DEFAULT 'offered'
                 CHECK (state IN ('offered','selected','edited','dismissed',
                                  'queued','running','done','failed','expired')),
  UNIQUE (set_id, seq)
);
CREATE INDEX IF NOT EXISTS na_state ON next_action(state);

-- ─── telemetry — how else would you know the chips are any good ─────────────

CREATE TABLE IF NOT EXISTS next_event (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  action_id  TEXT,
  set_id     TEXT,
  event      TEXT NOT NULL,   -- offered|selected|edited|dismissed|queued|expired|ignored
  kind       TEXT,
  recommended INTEGER,
  detail     TEXT NOT NULL DEFAULT '',
  at         TEXT NOT NULL
);

-- ─── ledger (hash-chained, same discipline as everything else) ──────────────

CREATE TABLE IF NOT EXISTS next_ledger (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  scope     TEXT NOT NULL,
  seq       INTEGER NOT NULL,
  kind      TEXT NOT NULL,
  payload   TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash      TEXT NOT NULL,
  at        TEXT NOT NULL,
  UNIQUE (scope, seq)
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

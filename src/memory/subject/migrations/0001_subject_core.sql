CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS subject_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    subject_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    created_at REAL NOT NULL,
    causal_refs_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    correlation_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_subject_events_subject_sequence ON subject_events(subject_id, sequence);
CREATE INDEX IF NOT EXISTS idx_subject_events_correlation ON subject_events(correlation_id);

CREATE TRIGGER IF NOT EXISTS subject_events_no_update
BEFORE UPDATE ON subject_events BEGIN SELECT RAISE(ABORT, 'subject_events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS subject_events_no_delete
BEFORE DELETE ON subject_events BEGIN SELECT RAISE(ABORT, 'subject_events are append-only'); END;

CREATE TABLE IF NOT EXISTS projection_revisions (
    subject_id TEXT NOT NULL,
    projection_type TEXT NOT NULL,
    projection_key TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(subject_id, projection_type, projection_key)
);

CREATE TABLE IF NOT EXISTS projection_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id TEXT NOT NULL,
    projection_type TEXT NOT NULL,
    projection_key TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(subject_id, projection_type, projection_key, revision)
);
CREATE TRIGGER IF NOT EXISTS projection_history_no_update
BEFORE UPDATE ON projection_history BEGIN SELECT RAISE(ABORT, 'projection history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS projection_history_no_delete
BEFORE DELETE ON projection_history BEGIN SELECT RAISE(ABORT, 'projection history is append-only'); END;

CREATE TABLE IF NOT EXISTS idempotency_keys (
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(scope, idempotency_key)
);

CREATE TABLE IF NOT EXISTS authorship_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    authorship_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(authorship_id, stage)
);
CREATE INDEX IF NOT EXISTS idx_authorship_action ON authorship_events(action_id, sequence);
CREATE TRIGGER IF NOT EXISTS authorship_events_no_update
BEFORE UPDATE ON authorship_events BEGIN SELECT RAISE(ABORT, 'authorship events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS authorship_events_no_delete
BEFORE DELETE ON authorship_events BEGIN SELECT RAISE(ABORT, 'authorship events are append-only'); END;

CREATE TABLE IF NOT EXISTS world_outcomes (
    outcome_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS world_outcomes_no_update
BEFORE UPDATE ON world_outcomes BEGIN SELECT RAISE(ABORT, 'world outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS world_outcomes_no_delete
BEFORE DELETE ON world_outcomes BEGIN SELECT RAISE(ABORT, 'world outcomes are append-only'); END;

CREATE TABLE IF NOT EXISTS beliefs_projection (
    subject_id TEXT NOT NULL,
    belief_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    supersedes_id TEXT,
    status TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(subject_id, belief_id)
);

CREATE TABLE IF NOT EXISTS values_projection (
    subject_id TEXT NOT NULL,
    value_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(subject_id, value_id)
);

CREATE TABLE IF NOT EXISTS need_episodes (
    subject_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    need_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    consolidation_state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(subject_id, episode_id)
);

CREATE TABLE IF NOT EXISTS commitments_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id TEXT NOT NULL,
    commitment_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS commitments_events_no_update
BEFORE UPDATE ON commitments_events BEGIN SELECT RAISE(ABORT, 'commitment events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS commitments_events_no_delete
BEFORE DELETE ON commitments_events BEGIN SELECT RAISE(ABORT, 'commitment events are append-only'); END;

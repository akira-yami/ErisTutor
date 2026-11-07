ALTER TABLE idempotency_keys ADD COLUMN request_hash TEXT NOT NULL DEFAULT '';

CREATE TRIGGER IF NOT EXISTS idempotency_keys_no_update
BEFORE UPDATE ON idempotency_keys BEGIN SELECT RAISE(ABORT, 'idempotency keys are append-only'); END;
CREATE TRIGGER IF NOT EXISTS idempotency_keys_no_delete
BEFORE DELETE ON idempotency_keys BEGIN SELECT RAISE(ABORT, 'idempotency keys are append-only'); END;

CREATE TABLE IF NOT EXISTS subject_integrity_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    report_json TEXT NOT NULL,
    report_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS subject_integrity_checkpoints_no_update
BEFORE UPDATE ON subject_integrity_checkpoints BEGIN SELECT RAISE(ABORT, 'integrity checkpoints are append-only'); END;
CREATE TRIGGER IF NOT EXISTS subject_integrity_checkpoints_no_delete
BEFORE DELETE ON subject_integrity_checkpoints BEGIN SELECT RAISE(ABORT, 'integrity checkpoints are append-only'); END;

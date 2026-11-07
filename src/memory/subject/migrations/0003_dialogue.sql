CREATE TABLE IF NOT EXISTS discourse_projections (
    subject_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(subject_id, session_id)
);

CREATE TABLE IF NOT EXISTS delivery_receipts (
    subject_id TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    completed_at REAL NOT NULL,
    PRIMARY KEY(subject_id, delivery_id)
);
CREATE TRIGGER IF NOT EXISTS delivery_receipts_no_update
BEFORE UPDATE ON delivery_receipts BEGIN SELECT RAISE(ABORT, 'delivery receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS delivery_receipts_no_delete
BEFORE DELETE ON delivery_receipts BEGIN SELECT RAISE(ABORT, 'delivery receipts are append-only'); END;

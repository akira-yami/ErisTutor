-- V8: lưu report thí nghiệm và tombstone chỉ trong database thí nghiệm.
CREATE TABLE IF NOT EXISTS experiment_runs (
    run_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    split TEXT NOT NULL CHECK(split IN ('development', 'calibration', 'holdout')),
    algorithm_version TEXT NOT NULL,
    code_revision TEXT NOT NULL,
    scenario_fingerprint TEXT NOT NULL,
    passed INTEGER NOT NULL CHECK(passed IN (0, 1)),
    run_count INTEGER NOT NULL,
    report_json TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_experiment_runs_scenario_split
ON experiment_runs(scenario_id, split, created_at);

CREATE TABLE IF NOT EXISTS experiment_tombstones (
    event_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL DEFAULT 'causal_ablation',
    created_at REAL NOT NULL DEFAULT 0,
    FOREIGN KEY(event_id) REFERENCES subject_events(event_id)
);

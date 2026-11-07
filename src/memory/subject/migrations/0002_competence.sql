CREATE TABLE IF NOT EXISTS competence_estimates (
    subject_id TEXT NOT NULL,
    estimate_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    context_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    estimated_at REAL NOT NULL,
    PRIMARY KEY(subject_id, estimate_id)
);
CREATE INDEX IF NOT EXISTS idx_competence_skill_context ON competence_estimates(subject_id, skill_id, context_key, estimated_at);

CREATE TABLE IF NOT EXISTS practice_plans (
    subject_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(subject_id, plan_id)
);

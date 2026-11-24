-- ErisTutor Java Backend Initial Schema (PostgreSQL 16 & H2 MODE=PostgreSQL compatible)

CREATE TABLE users (
    id            VARCHAR(64) PRIMARY KEY,
    username      VARCHAR(80) NOT NULL UNIQUE,
    email         VARCHAR(160) NOT NULL UNIQUE,
    password_hash VARCHAR(200) NOT NULL,
    role          VARCHAR(32) NOT NULL DEFAULT 'LEARNER',
    created_at    DOUBLE PRECISION NOT NULL
);

CREATE TABLE refresh_tokens (
    token_hash VARCHAR(64) PRIMARY KEY,
    user_id    VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at DOUBLE PRECISION NOT NULL,
    revoked    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX idx_refresh_tokens_user ON refresh_tokens(user_id);

CREATE TABLE learning_domains (
    domain_id       VARCHAR(120) PRIMARY KEY,
    skill_namespace VARCHAR(120) NOT NULL,
    version         VARCHAR(40) NOT NULL DEFAULT '1',
    payload         TEXT NOT NULL,
    created_at      DOUBLE PRECISION NOT NULL
);

CREATE TABLE learning_paths (
    path_id    VARCHAR(120) PRIMARY KEY,
    user_id    VARCHAR(64) NOT NULL,
    title      VARCHAR(240) NOT NULL,
    goal       TEXT NOT NULL,
    domain_id  VARCHAR(120) NOT NULL DEFAULT '',
    source     VARCHAR(64) NOT NULL DEFAULT 'user',
    revision   VARCHAR(64) NOT NULL,
    version    BIGINT NOT NULL DEFAULT 0,
    payload    TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX idx_learning_paths_user ON learning_paths(user_id);

CREATE TABLE knowledge_points (
    path_id            VARCHAR(120) NOT NULL REFERENCES learning_paths(path_id) ON DELETE CASCADE,
    kp_id              VARCHAR(120) NOT NULL,
    title              VARCHAR(240) NOT NULL,
    kp_type            VARCHAR(32) NOT NULL DEFAULT 'concept',
    ordinal            INTEGER NOT NULL DEFAULT 0,
    prerequisites_json TEXT NOT NULL DEFAULT '[]',
    payload            TEXT NOT NULL,
    PRIMARY KEY (path_id, kp_id)
);

CREATE TABLE learning_path_revisions (
    path_id       VARCHAR(120) NOT NULL,
    revision      VARCHAR(64) NOT NULL,
    base_revision VARCHAR(64) NOT NULL DEFAULT '',
    reason        VARCHAR(240) NOT NULL DEFAULT '',
    payload       TEXT NOT NULL,
    created_at    DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (path_id, revision)
);

CREATE TABLE learning_evidence (
    evidence_id    VARCHAR(120) PRIMARY KEY,
    user_id        VARCHAR(64) NOT NULL,
    kp_id          VARCHAR(120) NOT NULL,
    evidence_type  VARCHAR(40) NOT NULL,
    strength       DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    correct        BOOLEAN NOT NULL DEFAULT TRUE,
    interaction_id VARCHAR(120) NOT NULL DEFAULT '',
    turn_id        VARCHAR(120) NOT NULL DEFAULT '',
    recorded_at    DOUBLE PRECISION NOT NULL,
    error_code     VARCHAR(120) NOT NULL DEFAULT '',
    metadata       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_evidence_user_kp ON learning_evidence(user_id, kp_id, recorded_at);

CREATE TABLE learning_attempts (
    attempt_id    VARCHAR(120) PRIMARY KEY,
    user_id       VARCHAR(64) NOT NULL,
    kp_id         VARCHAR(120) NOT NULL,
    evidence_id   VARCHAR(120) NOT NULL DEFAULT '',
    fingerprint   VARCHAR(64) NOT NULL,
    payload       TEXT NOT NULL,
    recorded_at   DOUBLE PRECISION NOT NULL,
    superseded_by VARCHAR(120) NOT NULL DEFAULT ''
);
CREATE INDEX idx_attempt_user_kp ON learning_attempts(user_id, kp_id, recorded_at);

CREATE TABLE learning_assistance (
    scope_id    VARCHAR(160) PRIMARY KEY,
    user_id     VARCHAR(64) NOT NULL,
    kp_id       VARCHAR(120) NOT NULL,
    assistance  VARCHAR(40) NOT NULL DEFAULT 'independent',
    hint_count  INTEGER NOT NULL DEFAULT 0,
    events_json TEXT NOT NULL DEFAULT '[]',
    updated_at  DOUBLE PRECISION NOT NULL
);

CREATE TABLE learning_forget_tombstones (
    attempt_id   VARCHAR(120) PRIMARY KEY,
    user_id      VARCHAR(64) NOT NULL,
    forgotten_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE learning_forgotten_interactions (
    interaction_id VARCHAR(120) PRIMARY KEY,
    user_id        VARCHAR(64) NOT NULL,
    forgotten_at   DOUBLE PRECISION NOT NULL
);

CREATE TABLE mastery_records (
    user_id          VARCHAR(64) NOT NULL,
    kp_id            VARCHAR(120) NOT NULL,
    mastery          DOUBLE PRECISION NOT NULL DEFAULT 0,
    confidence       DOUBLE PRECISION NOT NULL DEFAULT 0,
    stage            VARCHAR(32) NOT NULL DEFAULT 'learning',
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    correct_count    INTEGER NOT NULL DEFAULT 0,
    last_evidence_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    next_review_at   DOUBLE PRECISION NOT NULL DEFAULT 0,
    retrievability   DOUBLE PRECISION NOT NULL DEFAULT 1,
    error_codes_json TEXT NOT NULL DEFAULT '[]',
    updated_at       DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, kp_id)
);

CREATE TABLE review_items (
    user_id         VARCHAR(64) NOT NULL,
    kp_id           VARCHAR(120) NOT NULL,
    due_at          DOUBLE PRECISION NOT NULL,
    forgetting_risk DOUBLE PRECISION NOT NULL DEFAULT 0,
    interval_days   DOUBLE PRECISION NOT NULL DEFAULT 1,
    ease_factor     DOUBLE PRECISION NOT NULL DEFAULT 2.5,
    PRIMARY KEY (user_id, kp_id)
);

CREATE TABLE judge_submissions (
    job_id       VARCHAR(80) PRIMARY KEY,
    user_id      VARCHAR(64) NOT NULL,
    request_id   VARCHAR(120) NOT NULL,
    pack_id      VARCHAR(120) NOT NULL,
    node_id      VARCHAR(120) NOT NULL,
    exercise_id  VARCHAR(120) NOT NULL,
    status       VARCHAR(32) NOT NULL,
    result       VARCHAR(32) NOT NULL DEFAULT '',
    reason       VARCHAR(120) NOT NULL DEFAULT '',
    diagnostic   TEXT NOT NULL DEFAULT '',
    duration_ms  BIGINT NOT NULL DEFAULT 0,
    exit_code    INTEGER NOT NULL DEFAULT 0,
    tests_json   TEXT NOT NULL DEFAULT '[]',
    attempt_id   VARCHAR(120) NOT NULL DEFAULT '',
    created_at   DOUBLE PRECISION NOT NULL,
    completed_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    CONSTRAINT uq_judge_user_request UNIQUE (user_id, request_id)
);

CREATE TABLE chat_messages (
    id         BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    session_id VARCHAR(120) NOT NULL,
    user_id    VARCHAR(64) NOT NULL,
    role       VARCHAR(32) NOT NULL,
    content    TEXT NOT NULL,
    turn_id    VARCHAR(64) NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX idx_chat_session ON chat_messages(session_id, id);

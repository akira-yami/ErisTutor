CREATE TABLE IF NOT EXISTS sandbox_command_claims (
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('processing', 'completed', 'failed')),
    lease_expires_at REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(scope, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_sandbox_claims_lease
ON sandbox_command_claims(status, lease_expires_at);

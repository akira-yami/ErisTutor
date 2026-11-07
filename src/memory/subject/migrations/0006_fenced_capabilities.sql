ALTER TABLE sandbox_command_claims
ADD COLUMN lease_generation INTEGER NOT NULL DEFAULT 1;

ALTER TABLE sandbox_command_claims
ADD COLUMN heartbeat_at REAL NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_sandbox_claims_owner_generation
ON sandbox_command_claims(scope, idempotency_key, owner_id, lease_generation, status);

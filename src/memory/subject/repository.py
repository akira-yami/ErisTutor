# -*- coding: utf-8 -*-
"""SQLite source of truth for Subject Core events and projections.

V6 provides migration/checksum inspection, fenced cross-process idempotency claims,
consistent backup and a machine-readable integrity report.  Databases, much
like humans, benefit from being checked before they confidently insist that
nothing happened.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .unit_of_work import UnitOfWork


class StaleRevisionError(RuntimeError):
    pass


class EventIntegrityError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    status: str
    generation: int
    lease_expires_at: float

    @property
    def acquired(self) -> bool:
        return self.status == "acquired"


@dataclass(frozen=True, slots=True)
class StoredEvent:
    sequence: int
    event_id: str
    subject_id: str
    event_type: str
    created_at: float
    causal_refs: tuple[str, ...]
    payload: Mapping[str, Any]
    previous_hash: str
    payload_hash: str
    correlation_id: str


class SubjectRepository:
    """Single-writer friendly repository with deterministic replay primitives."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
        migration_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.database_path = str(database_path)
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._configure(self._connection)
        self._migration_dir = Path(migration_dir) if migration_dir else Path(__file__).with_name("migrations")
        self.apply_migrations()

    @staticmethod
    def _configure(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 5000")

    @property
    def connection(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("repository is closed")
        return self._connection

    def foreign_keys_enabled(self) -> bool:
        return bool(self.connection.execute("PRAGMA foreign_keys").fetchone()[0])

    def unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self.connection, self._lock)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def _migration_files(self) -> tuple[Path, ...]:
        return tuple(sorted(self._migration_dir.glob("*.sql")))

    def apply_migrations(self) -> None:
        """Apply each migration and its checksum row in one SQLite transaction."""
        with self._lock:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at REAL NOT NULL)"
            )
            for path in self._migration_files():
                sql = path.read_text(encoding="utf-8")
                checksum = sha256_text(sql)
                existing = self.connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = ?", (path.name,)
                ).fetchone()
                if existing:
                    if existing["checksum"] != checksum:
                        raise EventIntegrityError(f"migration checksum changed: {path.name}")
                    continue
                applied_at = float(self._clock())
                script = (
                    "BEGIN IMMEDIATE;\n"
                    + sql
                    + "\nINSERT INTO schema_migrations(version, checksum, applied_at) VALUES ("
                    + _sql_quote(path.name)
                    + ","
                    + _sql_quote(checksum)
                    + ","
                    + repr(applied_at)
                    + ");\nCOMMIT;"
                )
                try:
                    self.connection.executescript(script)
                except Exception:
                    if self.connection.in_transaction:
                        self.connection.rollback()
                    raise

    def migration_status(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            rows = {
                str(row["version"]): str(row["checksum"])
                for row in self.connection.execute("SELECT version, checksum FROM schema_migrations").fetchall()
            }
        result: list[dict[str, Any]] = []
        for path in self._migration_files():
            expected = sha256_text(path.read_text(encoding="utf-8"))
            applied = rows.pop(path.name, None)
            result.append({
                "version": path.name,
                "present": applied is not None,
                "checksum_valid": applied == expected,
                "expected_checksum": expected,
                "applied_checksum": applied or "",
            })
        for extra, checksum in sorted(rows.items()):
            result.append({
                "version": extra,
                "present": True,
                "checksum_valid": False,
                "expected_checksum": "",
                "applied_checksum": checksum,
                "unexpected": True,
            })
        return tuple(result)

    def append_subject_event(
        self,
        *,
        subject_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        causal_refs: tuple[str, ...] = (),
        correlation_id: str = "",
        event_id: str | None = None,
        created_at: float | None = None,
    ) -> StoredEvent:
        event_id = event_id or self._id_factory()
        created_at = self._clock() if created_at is None else float(created_at)
        payload_json = canonical_json(payload)
        refs_json = canonical_json(list(causal_refs))
        with self.unit_of_work():
            existing = self.connection.execute(
                "SELECT * FROM subject_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if existing:
                if (
                    existing["payload_json"] != payload_json
                    or existing["event_type"] != event_type
                    or existing["subject_id"] != subject_id
                    or existing["causal_refs_json"] != refs_json
                    or existing["correlation_id"] != correlation_id
                ):
                    raise EventIntegrityError(f"event_id {event_id} already exists with different content")
                return self._row_to_event(existing)
            previous = self.connection.execute(
                "SELECT payload_hash FROM subject_events WHERE subject_id = ? ORDER BY sequence DESC LIMIT 1",
                (subject_id,),
            ).fetchone()
            previous_hash = previous["payload_hash"] if previous else "0" * 64
            envelope = canonical_json({
                "event_id": event_id,
                "subject_id": subject_id,
                "event_type": event_type,
                "created_at": created_at,
                "causal_refs": list(causal_refs),
                "payload": json.loads(payload_json),
                "previous_hash": previous_hash,
                "correlation_id": correlation_id,
            })
            payload_hash = sha256_text(envelope)
            cursor = self.connection.execute(
                """INSERT INTO subject_events(
                    event_id, subject_id, event_type, created_at, causal_refs_json,
                    payload_json, previous_hash, payload_hash, correlation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id, subject_id, event_type, created_at, refs_json, payload_json, previous_hash, payload_hash, correlation_id),
            )
            sequence = int(cursor.lastrowid)
        return StoredEvent(sequence, event_id, subject_id, event_type, created_at, causal_refs, dict(payload), previous_hash, payload_hash, correlation_id)

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> StoredEvent:
        return StoredEvent(
            sequence=int(row["sequence"]),
            event_id=str(row["event_id"]),
            subject_id=str(row["subject_id"]),
            event_type=str(row["event_type"]),
            created_at=float(row["created_at"]),
            causal_refs=tuple(json.loads(row["causal_refs_json"])),
            payload=dict(json.loads(row["payload_json"])),
            previous_hash=str(row["previous_hash"]),
            payload_hash=str(row["payload_hash"]),
            correlation_id=str(row["correlation_id"]),
        )

    def subjects(self) -> tuple[str, ...]:
        with self._lock:
            rows = self.connection.execute("SELECT DISTINCT subject_id FROM subject_events ORDER BY subject_id").fetchall()
        return tuple(str(row[0]) for row in rows)

    def events(self, *, subject_id: str, after_sequence: int = 0) -> tuple[StoredEvent, ...]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM subject_events WHERE subject_id = ? AND sequence > ? ORDER BY sequence",
                (subject_id, after_sequence),
            ).fetchall()
        return tuple(self._row_to_event(row) for row in rows)

    def verify_event_chain(self, subject_id: str) -> bool:
        previous_hash = "0" * 64
        for event in self.events(subject_id=subject_id):
            if event.previous_hash != previous_hash:
                return False
            envelope = canonical_json({
                "event_id": event.event_id,
                "subject_id": event.subject_id,
                "event_type": event.event_type,
                "created_at": event.created_at,
                "causal_refs": list(event.causal_refs),
                "payload": dict(event.payload),
                "previous_hash": event.previous_hash,
                "correlation_id": event.correlation_id,
            })
            if sha256_text(envelope) != event.payload_hash:
                return False
            previous_hash = event.payload_hash
        return True

    def save_projection(
        self,
        *,
        subject_id: str,
        projection_type: str,
        projection_key: str,
        payload: Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> int:
        payload_json = canonical_json(payload)
        payload_hash = sha256_text(payload_json)
        now = self._clock()
        with self.unit_of_work():
            row = self.connection.execute(
                """SELECT revision FROM projection_revisions
                   WHERE subject_id = ? AND projection_type = ? AND projection_key = ?""",
                (subject_id, projection_type, projection_key),
            ).fetchone()
            current_revision = int(row["revision"]) if row else 0
            if expected_revision is not None and expected_revision != current_revision:
                raise StaleRevisionError(
                    f"expected revision {expected_revision}, found {current_revision} for {projection_type}/{projection_key}"
                )
            new_revision = current_revision + 1
            self.connection.execute(
                """INSERT INTO projection_history(
                    subject_id, projection_type, projection_key, revision, payload_json, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (subject_id, projection_type, projection_key, new_revision, payload_json, payload_hash, now),
            )
            self.connection.execute(
                """INSERT INTO projection_revisions(
                    subject_id, projection_type, projection_key, revision, payload_json, payload_hash, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subject_id, projection_type, projection_key) DO UPDATE SET
                    revision=excluded.revision, payload_json=excluded.payload_json,
                    payload_hash=excluded.payload_hash, updated_at=excluded.updated_at""",
                (subject_id, projection_type, projection_key, new_revision, payload_json, payload_hash, now),
            )
        return new_revision

    def get_projection(self, *, subject_id: str, projection_type: str, projection_key: str) -> tuple[int, dict[str, Any]] | None:
        with self._lock:
            row = self.connection.execute(
                """SELECT revision, payload_json FROM projection_revisions
                   WHERE subject_id = ? AND projection_type = ? AND projection_key = ?""",
                (subject_id, projection_type, projection_key),
            ).fetchone()
        if not row:
            return None
        return int(row["revision"]), dict(json.loads(row["payload_json"]))


    def claim_idempotency(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request_hash: str,
        owner_id: str,
        lease_seconds: float,
    ) -> IdempotencyClaim:
        """Claim a side effect with a monotonically increasing fencing generation."""
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = float(self._clock())
        with self.unit_of_work():
            completed = self.connection.execute(
                "SELECT request_hash FROM idempotency_keys WHERE scope = ? AND idempotency_key = ?",
                (scope, idempotency_key),
            ).fetchone()
            if completed is not None:
                stored_hash = str(completed["request_hash"] or "")
                if stored_hash and stored_hash != request_hash:
                    raise EventIntegrityError(
                        f"idempotency key {scope}/{idempotency_key} reused for a different request"
                    )
                return IdempotencyClaim("completed", 0, 0.0)

            row = self.connection.execute(
                "SELECT request_hash, owner_id, status, lease_expires_at, lease_generation "
                "FROM sandbox_command_claims WHERE scope = ? AND idempotency_key = ?",
                (scope, idempotency_key),
            ).fetchone()
            if row is None:
                generation = 1
                lease_expires_at = now + lease_seconds
                self.connection.execute(
                    "INSERT INTO sandbox_command_claims("
                    "scope, idempotency_key, request_hash, owner_id, status, lease_expires_at, "
                    "created_at, updated_at, lease_generation, heartbeat_at"
                    ") VALUES (?, ?, ?, ?, 'processing', ?, ?, ?, ?, ?)",
                    (
                        scope, idempotency_key, request_hash, owner_id, lease_expires_at,
                        now, now, generation, now,
                    ),
                )
                return IdempotencyClaim("acquired", generation, lease_expires_at)

            if str(row["request_hash"]) != request_hash:
                raise EventIntegrityError(
                    f"idempotency claim {scope}/{idempotency_key} reused for a different request"
                )
            generation = int(row["lease_generation"])
            if str(row["status"]) == "completed":
                return IdempotencyClaim("completed", generation, 0.0)
            if str(row["owner_id"]) == owner_id:
                lease_expires_at = now + lease_seconds
                self.connection.execute(
                    "UPDATE sandbox_command_claims SET status = 'processing', lease_expires_at = ?, "
                    "heartbeat_at = ?, updated_at = ? WHERE scope = ? AND idempotency_key = ? "
                    "AND owner_id = ? AND lease_generation = ?",
                    (lease_expires_at, now, now, scope, idempotency_key, owner_id, generation),
                )
                return IdempotencyClaim("acquired", generation, lease_expires_at)
            if float(row["lease_expires_at"]) <= now:
                generation += 1
                lease_expires_at = now + lease_seconds
                cursor = self.connection.execute(
                    "UPDATE sandbox_command_claims SET owner_id = ?, status = 'processing', "
                    "lease_expires_at = ?, heartbeat_at = ?, updated_at = ?, lease_generation = ? "
                    "WHERE scope = ? AND idempotency_key = ? AND lease_generation = ?",
                    (
                        owner_id, lease_expires_at, now, now, generation,
                        scope, idempotency_key, generation - 1,
                    ),
                )
                if cursor.rowcount == 1:
                    return IdempotencyClaim("acquired", generation, lease_expires_at)
            return IdempotencyClaim("busy", generation, float(row["lease_expires_at"]))

    def renew_idempotency_claim(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request_hash: str,
        owner_id: str,
        generation: int,
        lease_seconds: float,
    ) -> bool:
        if lease_seconds <= 0 or generation <= 0:
            raise ValueError("lease_seconds and generation must be positive")
        now = float(self._clock())
        with self.unit_of_work():
            cursor = self.connection.execute(
                "UPDATE sandbox_command_claims SET lease_expires_at = ?, heartbeat_at = ?, updated_at = ? "
                "WHERE scope = ? AND idempotency_key = ? AND request_hash = ? AND owner_id = ? "
                "AND lease_generation = ? AND status = 'processing'",
                (
                    now + lease_seconds, now, now, scope, idempotency_key, request_hash,
                    owner_id, generation,
                ),
            )
            return cursor.rowcount == 1

    def release_idempotency_claim(
        self,
        *,
        scope: str,
        idempotency_key: str,
        owner_id: str,
        generation: int,
        failed: bool = True,
    ) -> bool:
        now = float(self._clock())
        with self.unit_of_work():
            cursor = self.connection.execute(
                "UPDATE sandbox_command_claims SET status = ?, lease_expires_at = 0, heartbeat_at = ?, updated_at = ? "
                "WHERE scope = ? AND idempotency_key = ? AND owner_id = ? AND lease_generation = ? "
                "AND status = 'processing'",
                (
                    "failed" if failed else "processing", now, now,
                    scope, idempotency_key, owner_id, generation,
                ),
            )
            return cursor.rowcount == 1

    def get_idempotent(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request_hash: str = "",
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT result_json, result_hash, request_hash FROM idempotency_keys WHERE scope = ? AND idempotency_key = ?",
                (scope, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        if request_hash and row["request_hash"] and row["request_hash"] != request_hash:
            raise EventIntegrityError(f"idempotency key {scope}/{idempotency_key} reused for a different request")
        if sha256_text(str(row["result_json"])) != str(row["result_hash"]):
            raise EventIntegrityError(f"idempotency result hash mismatch: {scope}/{idempotency_key}")
        value = json.loads(row["result_json"])
        if not isinstance(value, dict):
            raise EventIntegrityError(f"idempotency result is not an object: {scope}/{idempotency_key}")
        return dict(value)

    def record_idempotent(
        self,
        *,
        scope: str,
        idempotency_key: str,
        result: Mapping[str, Any],
        request_hash: str = "",
    ) -> tuple[dict[str, Any], bool]:
        result_json = canonical_json(result)
        result_hash = sha256_text(result_json)
        with self.unit_of_work():
            row = self.connection.execute(
                "SELECT result_json, result_hash, request_hash FROM idempotency_keys WHERE scope = ? AND idempotency_key = ?",
                (scope, idempotency_key),
            ).fetchone()
            if row:
                if request_hash and row["request_hash"] and row["request_hash"] != request_hash:
                    raise EventIntegrityError(f"idempotency key {scope}/{idempotency_key} reused for a different request")
                if sha256_text(str(row["result_json"])) != str(row["result_hash"]):
                    raise EventIntegrityError(f"idempotency result hash mismatch: {scope}/{idempotency_key}")
                return dict(json.loads(row["result_json"])), False
            self.connection.execute(
                "INSERT INTO idempotency_keys(scope, idempotency_key, result_json, result_hash, created_at, request_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (scope, idempotency_key, result_json, result_hash, self._clock(), request_hash),
            )
        return dict(result), True

    def append_authorship_event(self, *, authorship_id: str, action_id: str, decision_id: str, stage: str, payload: Mapping[str, Any]) -> None:
        payload_json = canonical_json(payload)
        payload_hash = sha256_text(payload_json)
        with self.unit_of_work():
            row = self.connection.execute(
                "SELECT payload_hash FROM authorship_events WHERE authorship_id = ? AND stage = ?",
                (authorship_id, stage),
            ).fetchone()
            if row:
                if row["payload_hash"] != payload_hash:
                    raise EventIntegrityError(f"authorship event {authorship_id}/{stage} conflicts with stored content")
                return
            self.connection.execute(
                """INSERT INTO authorship_events(
                    authorship_id, action_id, decision_id, stage, payload_json, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (authorship_id, action_id, decision_id, stage, payload_json, payload_hash, self._clock()),
            )

    def append_world_outcome(self, *, outcome_id: str, action_id: str, decision_id: str, status: str, payload: Mapping[str, Any]) -> None:
        payload_json = canonical_json(payload)
        payload_hash = sha256_text(payload_json)
        with self.unit_of_work():
            row = self.connection.execute(
                "SELECT payload_hash FROM world_outcomes WHERE outcome_id = ?", (outcome_id,)
            ).fetchone()
            if row:
                if row["payload_hash"] != payload_hash:
                    raise EventIntegrityError(f"world outcome {outcome_id} conflicts with stored content")
                return
            self.connection.execute(
                """INSERT INTO world_outcomes(
                    outcome_id, action_id, decision_id, status, payload_json, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (outcome_id, action_id, decision_id, status, payload_json, payload_hash, self._clock()),
            )

    def record_sandbox_outcome(
        self,
        *,
        idempotency_key: str,
        request_hash: str,
        outcome_id: str,
        action_id: str,
        decision_id: str,
        status: str,
        payload: Mapping[str, Any],
        claim_owner_id: str = "",
        claim_generation: int = 0,
    ) -> tuple[dict[str, Any], bool]:
        """Persist idempotency result and world outcome atomically."""
        payload_json = canonical_json(payload)
        payload_hash = sha256_text(payload_json)
        with self.unit_of_work():
            if claim_owner_id:
                claim = self.connection.execute(
                    "SELECT request_hash, owner_id, status, lease_generation FROM sandbox_command_claims "
                    "WHERE scope = 'sandbox' AND idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if claim is None:
                    raise EventIntegrityError(f"missing sandbox claim for {idempotency_key}")
                if str(claim["request_hash"]) != request_hash:
                    raise EventIntegrityError(f"sandbox claim hash mismatch for {idempotency_key}")
                if (
                    str(claim["owner_id"]) != claim_owner_id
                    or str(claim["status"]) != "processing"
                    or int(claim["lease_generation"]) != int(claim_generation)
                ):
                    raise EventIntegrityError(f"sandbox claim fencing token rejected for {idempotency_key}")
            existing = self.connection.execute(
                "SELECT result_json, result_hash, request_hash FROM idempotency_keys WHERE scope = 'sandbox' AND idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                if existing["request_hash"] and existing["request_hash"] != request_hash:
                    raise EventIntegrityError(f"idempotency key sandbox/{idempotency_key} reused for a different request")
                if sha256_text(str(existing["result_json"])) != str(existing["result_hash"]):
                    raise EventIntegrityError(f"idempotency result hash mismatch: sandbox/{idempotency_key}")
                return dict(json.loads(existing["result_json"])), False
            self.connection.execute(
                "INSERT INTO idempotency_keys(scope, idempotency_key, result_json, result_hash, created_at, request_hash) VALUES ('sandbox', ?, ?, ?, ?, ?)",
                (idempotency_key, payload_json, payload_hash, self._clock(), request_hash),
            )
            self.connection.execute(
                """INSERT INTO world_outcomes(
                    outcome_id, action_id, decision_id, status, payload_json, payload_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (outcome_id, action_id, decision_id, status, payload_json, payload_hash, self._clock()),
            )
            if claim_owner_id:
                self.connection.execute(
                    "UPDATE sandbox_command_claims SET status = 'completed', lease_expires_at = 0, "
                    "heartbeat_at = ?, updated_at = ? WHERE scope = 'sandbox' AND idempotency_key = ? "
                    "AND owner_id = ? AND lease_generation = ?",
                    (self._clock(), self._clock(), idempotency_key, claim_owner_id, claim_generation),
                )
        return dict(payload), True

    def projection_hash(self, *, subject_id: str) -> str:
        with self._lock:
            rows = self.connection.execute(
                """SELECT projection_type, projection_key, revision, payload_hash
                   FROM projection_revisions WHERE subject_id = ?
                   ORDER BY projection_type, projection_key""",
                (subject_id,),
            ).fetchall()
        return sha256_text(canonical_json([dict(row) for row in rows]))

    def integrity_report(self, *, subject_ids: Sequence[str] | None = None, persist: bool = False) -> dict[str, Any]:
        with self._lock:
            quick_rows = self.connection.execute("PRAGMA quick_check").fetchall()
            fk_rows = self.connection.execute("PRAGMA foreign_key_check").fetchall()
        subjects = tuple(subject_ids) if subject_ids is not None else self.subjects()
        migration_status = self.migration_status()
        chains = {subject_id: self.verify_event_chain(subject_id) for subject_id in subjects}
        with self._lock:
            claim_rows = self.connection.execute(
                "SELECT status, COUNT(*) AS count FROM sandbox_command_claims GROUP BY status"
            ).fetchall()
            expired_processing = int(self.connection.execute(
                "SELECT COUNT(*) FROM sandbox_command_claims WHERE status = 'processing' AND lease_expires_at <= ?",
                (float(self._clock()),),
            ).fetchone()[0])
        report: dict[str, Any] = {
            "database_path": str(Path(self.database_path).resolve()),
            "quick_check": [str(row[0]) for row in quick_rows],
            "foreign_keys_enabled": self.foreign_keys_enabled(),
            "foreign_key_violations": [list(row) for row in fk_rows],
            "migrations": list(migration_status),
            "event_chains": chains,
            "projection_hashes": {subject_id: self.projection_hash(subject_id=subject_id) for subject_id in subjects},
            "sandbox_claims": {str(row["status"]): int(row["count"]) for row in claim_rows},
            "expired_processing_claims": expired_processing,
        }
        report["ok"] = (
            report["quick_check"] == ["ok"]
            and report["foreign_keys_enabled"]
            and not report["foreign_key_violations"]
            and all(item.get("present") and item.get("checksum_valid") for item in migration_status)
            and all(chains.values())
            and expired_processing == 0
        )
        if persist:
            payload_json = canonical_json(report)
            with self.unit_of_work():
                self.connection.execute(
                    "INSERT INTO subject_integrity_checkpoints(checkpoint_id, created_at, report_json, report_hash) VALUES (?, ?, ?, ?)",
                    (self._id_factory(), self._clock(), payload_json, sha256_text(payload_json)),
                )
        return report

    def backup_to(self, destination: str | os.PathLike[str]) -> Path:
        """Create a consistent SQLite backup and verify it before returning."""
        destination_path = Path(destination).resolve()
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if destination_path == Path(self.database_path).resolve():
            raise ValueError("backup destination must differ from source database")
        if destination_path.exists():
            destination_path.unlink()
        target = sqlite3.connect(str(destination_path), isolation_level=None)
        try:
            with self._lock:
                self.connection.backup(target)
            target.row_factory = sqlite3.Row
            self._configure(target)
            check = [str(row[0]) for row in target.execute("PRAGMA quick_check").fetchall()]
            if check != ["ok"]:
                raise EventIntegrityError(f"backup integrity check failed: {check}")
        finally:
            target.close()

        # A backup is not useful merely because bytes were written.  Open it as
        # a fresh repository and verify the same migrations, event chains and
        # projection hashes before returning it to the caller.
        restored = SubjectRepository(
            destination_path,
            clock=self._clock,
            id_factory=self._id_factory,
            migration_dir=self._migration_dir,
        )
        try:
            source_subjects = self.subjects()
            restored_report = restored.integrity_report(subject_ids=source_subjects)
            if not restored_report.get("ok"):
                raise EventIntegrityError("restored backup failed integrity report")
            for subject_id in source_subjects:
                if restored.projection_hash(subject_id=subject_id) != self.projection_hash(subject_id=subject_id):
                    raise EventIntegrityError(f"restored backup projection mismatch: {subject_id}")
                if len(restored.events(subject_id=subject_id)) != len(self.events(subject_id=subject_id)):
                    raise EventIntegrityError(f"restored backup event count mismatch: {subject_id}")
        finally:
            restored.close()
        return destination_path


    def save_experiment_report(self, report: Any, *, run_id: str | None = None) -> str:
        """Lưu report JSON để audit, không cho report tham gia chọn hành vi."""

        payload = report.to_dict() if hasattr(report, "to_dict") else dict(report)
        identifier = run_id or self._id_factory()
        with self.unit_of_work():
            self.connection.execute(
                "INSERT INTO experiment_runs("
                "run_id, scenario_id, split, algorithm_version, code_revision, "
                "scenario_fingerprint, passed, run_count, report_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    str(payload.get("scenario_id", "unknown")),
                    str(payload.get("split", "development")),
                    str(payload.get("algorithm_version", "unknown")),
                    str(payload.get("code_revision", "unknown")),
                    str(payload.get("scenario_fingerprint", "")),
                    1 if bool(payload.get("passed")) else 0,
                    int(payload.get("run_count", 0)),
                    canonical_json(payload),
                    float(self._clock()),
                ),
            )
        return identifier

    def experiment_reports(
        self,
        *,
        scenario_id: str | None = None,
        split: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Đọc report theo scenario/split cho dashboard nghiên cứu."""

        clauses: list[str] = []
        params: list[Any] = []
        if scenario_id is not None:
            clauses.append("scenario_id = ?")
            params.append(scenario_id)
        if split is not None:
            clauses.append("split = ?")
            params.append(split)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self.connection.execute(
                "SELECT report_json FROM experiment_runs" + where + " ORDER BY created_at",
                tuple(params),
            ).fetchall()
        return tuple(dict(json.loads(row["report_json"])) for row in rows)



class AsyncPersistenceActor:
    """A bounded single writer for async runtimes."""

    def __init__(self, repository: SubjectRepository, *, max_queue_size: int = 256) -> None:
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be positive")
        self.repository = repository
        self._queue: asyncio.Queue[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any], asyncio.Future[Any]] | None] = asyncio.Queue(maxsize=max_queue_size)
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="subject-persistence-writer")

    async def stop(self) -> None:
        if self._task is None:
            return
        await self._queue.put(None)
        await self._task
        self._task = None

    async def call(self, method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if self._task is None:
            await self.start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        await self._queue.put((method, args, kwargs, future))
        return await future

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            method, args, kwargs, future = item
            try:
                result = await asyncio.to_thread(method, *args, **kwargs)
            except Exception as exc:
                if not future.cancelled():
                    future.set_exception(exc)
            else:
                if not future.cancelled():
                    future.set_result(result)


__all__ = [
    "AsyncPersistenceActor", "EventIntegrityError", "StaleRevisionError", "StoredEvent",
    "SubjectRepository", "canonical_json", "sha256_text",
]

# -*- coding: utf-8 -*-
"""Independent append-only forensic store with a hash chain."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from memory.subject.repository import canonical_json
from .contracts import AuditEvent, AuditEventKind, CausalTrace


class ForensicIntegrityError(RuntimeError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    subject_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    payload_json TEXT NOT NULL,
    causal_refs_json TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_subject_sequence ON audit_events(subject_id, sequence);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit_events(correlation_id);
CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
"""


class ForensicStore:
    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.database_path = str(database_path)
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.database_path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._connection.executescript(_SCHEMA)

    @staticmethod
    def _hash(envelope: Mapping[str, Any]) -> str:
        return hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def append(self, event: AuditEvent | Mapping[str, Any]) -> AuditEvent:
        if not isinstance(event, AuditEvent):
            event = AuditEvent(
                event_id=str(event.get("event_id") or self._id_factory()),
                subject_id=str(event.get("subject_id") or "eris"),
                kind=str(event.get("kind") or "unknown"),
                entity_id=str(event.get("entity_id") or event.get("event_id") or "unknown"),
                created_at=float(event.get("created_at") or self._clock()),
                payload=dict(event.get("payload") or {}),
                causal_refs=tuple(str(item) for item in event.get("causal_refs", ())),
                correlation_id=str(event.get("correlation_id") or ""),
                algorithm_version=str(event.get("algorithm_version") or ""),
            )
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute("SELECT * FROM audit_events WHERE event_id = ?", (event.event_id,)).fetchone()
                if existing:
                    stored = self._row_to_event(existing)
                    if stored.to_dict() != event.to_dict():
                        raise ForensicIntegrityError(f"audit event {event.event_id} conflicts")
                    self._connection.commit()
                    return stored
                previous = self._connection.execute(
                    "SELECT event_hash FROM audit_events WHERE subject_id = ? ORDER BY sequence DESC LIMIT 1",
                    (event.subject_id,),
                ).fetchone()
                previous_hash = str(previous["event_hash"]) if previous else "0" * 64
                envelope = {
                    "event_id": event.event_id,
                    "subject_id": event.subject_id,
                    "kind": event.kind.value if isinstance(event.kind, AuditEventKind) else str(event.kind),
                    "entity_id": event.entity_id,
                    "created_at": event.created_at,
                    "payload": dict(event.payload),
                    "causal_refs": list(event.causal_refs),
                    "correlation_id": event.correlation_id,
                    "algorithm_version": event.algorithm_version,
                    "previous_hash": previous_hash,
                }
                event_hash = self._hash(envelope)
                self._connection.execute(
                    """INSERT INTO audit_events(
                        event_id, subject_id, kind, entity_id, created_at, payload_json,
                        causal_refs_json, correlation_id, algorithm_version, previous_hash, event_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.event_id, event.subject_id, envelope["kind"], event.entity_id, event.created_at,
                        canonical_json(dict(event.payload)), canonical_json(list(event.causal_refs)),
                        event.correlation_id, event.algorithm_version, previous_hash, event_hash,
                    ),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return event

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            event_id=str(row["event_id"]),
            subject_id=str(row["subject_id"]),
            kind=str(row["kind"]),
            entity_id=str(row["entity_id"]),
            created_at=float(row["created_at"]),
            payload=dict(json.loads(row["payload_json"])),
            causal_refs=tuple(json.loads(row["causal_refs_json"])),
            correlation_id=str(row["correlation_id"]),
            algorithm_version=str(row["algorithm_version"]),
        )

    def query(
        self,
        *,
        subject_id: str | None = None,
        entity_id: str | None = None,
        correlation_id: str | None = None,
        limit: int = 1000,
    ) -> tuple[AuditEvent, ...]:
        clauses: list[str] = []
        params: list[Any] = []
        if subject_id is not None:
            clauses.append("subject_id = ?")
            params.append(subject_id)
        if entity_id is not None:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        if correlation_id is not None:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM audit_events{where} ORDER BY sequence LIMIT ?",
                (*params, max(1, int(limit))),
            ).fetchall()
        return tuple(self._row_to_event(row) for row in rows)

    def verify_chain(self, subject_id: str) -> bool:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM audit_events WHERE subject_id = ? ORDER BY sequence", (subject_id,)
            ).fetchall()
        previous_hash = "0" * 64
        for row in rows:
            if str(row["previous_hash"]) != previous_hash:
                return False
            envelope = {
                "event_id": str(row["event_id"]),
                "subject_id": str(row["subject_id"]),
                "kind": str(row["kind"]),
                "entity_id": str(row["entity_id"]),
                "created_at": float(row["created_at"]),
                "payload": dict(json.loads(row["payload_json"])),
                "causal_refs": list(json.loads(row["causal_refs_json"])),
                "correlation_id": str(row["correlation_id"]),
                "algorithm_version": str(row["algorithm_version"]),
                "previous_hash": previous_hash,
            }
            calculated = self._hash(envelope)
            if calculated != str(row["event_hash"]):
                return False
            previous_hash = calculated
        return True

    def causal_trace(self, entity_id: str) -> CausalTrace:
        all_events = self.query(limit=100000)
        by_entity: dict[str, list[AuditEvent]] = {}
        for event in all_events:
            by_entity.setdefault(event.entity_id, []).append(event)
        found: dict[str, AuditEvent] = {}
        missing: set[str] = set()
        queue = [entity_id]
        while queue:
            current = queue.pop(0)
            candidates = by_entity.get(current, [])
            if not candidates:
                missing.add(current)
                continue
            for event in candidates:
                if event.event_id in found:
                    continue
                found[event.event_id] = event
                queue.extend(ref for ref in event.causal_refs if ref not in by_entity and ref not in found)
                queue.extend(event.causal_refs)
        subject_ids = {event.subject_id for event in found.values()}
        chain_valid = all(self.verify_chain(subject_id) for subject_id in subject_ids)
        return CausalTrace(
            entity_id=entity_id,
            events=tuple(sorted(found.values(), key=lambda event: (event.created_at, event.event_id))),
            missing_refs=tuple(sorted(missing - {entity_id})),
            chain_valid=chain_valid,
        )

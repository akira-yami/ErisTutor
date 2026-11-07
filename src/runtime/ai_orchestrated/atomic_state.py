# -*- coding: utf-8 -*-
"""Durable compare-and-swap state and idempotency receipts for social runtime.

The previous implementation used ``threading.RLock`` around in-memory snapshots.
That is correct for one Python process and completely decorative once two workers
or a restart enter the picture. This module keeps the same JSON-shaped state but
serializes mutations through SQLite transactions and a monotonically increasing
revision.

SQLite is deliberately the first production backend because it ships with Python,
works for a single-host deployment, and gives us WAL, crash recovery and real
process-level exclusion without introducing a Redis dependency. A distributed
multi-host deployment should implement the same small interface on a database or
Redis using conditional writes.
"""
from __future__ import annotations

import contextlib
import json
import os
import pickle
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping


class AtomicStateError(RuntimeError):
    """Base error for the durable state backend."""


class AtomicStateConflict(AtomicStateError):
    """The persisted revision changed before a conditional update committed."""


@dataclass(frozen=True, slots=True)
class AtomicStateRecord:
    key: str
    revision: int
    payload: Mapping[str, Any]
    updated_at: float


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    state: str  # acquired | busy | committed
    key: str
    owner: str = ""
    lease_until: float = 0.0
    result: Any = None


class SQLiteStateTransaction:
    """One ``BEGIN IMMEDIATE`` transaction over a single runtime state row."""

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        key: str,
        revision: int,
        payload: Mapping[str, Any],
        clock,
    ) -> None:
        self.connection = connection
        self.key = key
        self.revision = int(revision)
        self.payload = dict(payload)
        self._staged: Mapping[str, Any] | None = None
        self._clock = clock

    @property
    def dirty(self) -> bool:
        return self._staged is not None

    def stage(self, payload: Mapping[str, Any]) -> None:
        self._staged = dict(payload)

    def _commit_staged(self) -> AtomicStateRecord:
        payload = dict(self._staged if self._staged is not None else self.payload)
        updated_at = float(self._clock())
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        cursor = self.connection.execute(
            """
            UPDATE runtime_state
               SET revision = revision + 1,
                   payload_json = ?,
                   updated_at = ?
             WHERE state_key = ? AND revision = ?
            """,
            (encoded, updated_at, self.key, self.revision),
        )
        if cursor.rowcount != 1:
            raise AtomicStateConflict(f"state revision changed: {self.key}@{self.revision}")
        return AtomicStateRecord(
            key=self.key,
            revision=self.revision + 1,
            payload=payload,
            updated_at=updated_at,
        )


class SQLiteAtomicStateStore:
    """SQLite-backed CAS store and durable idempotency ledger.

    Every method opens its own connection. That is slightly more work than a
    process-global connection, but it behaves correctly across threads, workers
    and forked processes. WAL allows readers while one writer owns the short
    ``BEGIN IMMEDIATE`` section.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        timeout_seconds: float = 15.0,
        clock=None,
        logger: Any | None = None,
    ) -> None:
        self.path = Path(path)
        self.timeout_seconds = max(0.25, float(timeout_seconds))
        self._clock = clock or time.time
        self._logger = logger
        self._init_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path),
            timeout=self.timeout_seconds,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._init_lock:
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_state (
                        state_key TEXT PRIMARY KEY,
                        revision INTEGER NOT NULL,
                        payload_json TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS idempotency_receipt (
                        idempotency_key TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        owner TEXT NOT NULL,
                        lease_until REAL NOT NULL,
                        result_blob BLOB,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_idempotency_updated ON idempotency_receipt(updated_at)"
                )
            finally:
                connection.close()

    @staticmethod
    def _decode_payload(raw: Any) -> dict[str, Any]:
        try:
            value = json.loads(str(raw or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            value = {}
        return dict(value) if isinstance(value, Mapping) else {}

    def ensure(self, key: str, initial_payload: Mapping[str, Any] | None = None) -> AtomicStateRecord:
        state_key = str(key or "").strip()
        if not state_key:
            raise ValueError("state key is required")
        initial = dict(initial_payload or {})
        encoded = json.dumps(initial, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        now = float(self._clock())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO runtime_state(state_key, revision, payload_json, updated_at) VALUES (?, 0, ?, ?)",
                (state_key, encoded, now),
            )
            row = connection.execute(
                "SELECT state_key, revision, payload_json, updated_at FROM runtime_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if row is None:
            raise AtomicStateError(f"failed to initialize state row: {state_key}")
        return AtomicStateRecord(
            key=state_key,
            revision=int(row["revision"]),
            payload=self._decode_payload(row["payload_json"]),
            updated_at=float(row["updated_at"]),
        )

    def load(self, key: str) -> AtomicStateRecord | None:
        state_key = str(key or "").strip()
        if not state_key:
            return None
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT state_key, revision, payload_json, updated_at FROM runtime_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return AtomicStateRecord(
            key=state_key,
            revision=int(row["revision"]),
            payload=self._decode_payload(row["payload_json"]),
            updated_at=float(row["updated_at"]),
        )

    def compare_and_swap(
        self,
        key: str,
        *,
        expected_revision: int,
        payload: Mapping[str, Any],
    ) -> AtomicStateRecord | None:
        state_key = str(key or "").strip()
        encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        now = float(self._clock())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE runtime_state
                   SET revision = revision + 1, payload_json = ?, updated_at = ?
                 WHERE state_key = ? AND revision = ?
                """,
                (encoded, now, state_key, int(expected_revision)),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return AtomicStateRecord(
            key=state_key,
            revision=int(expected_revision) + 1,
            payload=dict(payload),
            updated_at=now,
        )

    @contextlib.contextmanager
    def transaction(
        self,
        key: str,
        initial_payload: Mapping[str, Any] | None = None,
    ) -> Iterator[SQLiteStateTransaction]:
        state_key = str(key or "").strip()
        if not state_key:
            raise ValueError("state key is required")
        initial = dict(initial_payload or {})
        encoded = json.dumps(initial, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        now = float(self._clock())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO runtime_state(state_key, revision, payload_json, updated_at) VALUES (?, 0, ?, ?)",
                (state_key, encoded, now),
            )
            row = connection.execute(
                "SELECT revision, payload_json, updated_at FROM runtime_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
            if row is None:
                raise AtomicStateError(f"missing state row inside transaction: {state_key}")
            tx = SQLiteStateTransaction(
                connection=connection,
                key=state_key,
                revision=int(row["revision"]),
                payload=self._decode_payload(row["payload_json"]),
                clock=self._clock,
            )
            yield tx
            if tx.dirty:
                tx._commit_staged()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def replace(self, key: str, payload: Mapping[str, Any]) -> AtomicStateRecord:
        with self.transaction(key, payload) as tx:
            tx.stage(payload)
            revision = tx.revision + 1
        return AtomicStateRecord(
            key=str(key), revision=revision, payload=dict(payload), updated_at=float(self._clock())
        )

    def claim_idempotency(
        self,
        key: str,
        *,
        owner: str,
        lease_seconds: float = 15.0,
        now: float | None = None,
    ) -> IdempotencyClaim:
        token = str(key or "").strip()
        if not token:
            return IdempotencyClaim(state="acquired", key="", owner=str(owner or ""))
        actor = str(owner or "anonymous").strip() or "anonymous"
        current = float(self._clock() if now is None else now)
        lease_until = current + max(0.5, min(300.0, float(lease_seconds)))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, owner, lease_until, result_blob FROM idempotency_receipt WHERE idempotency_key = ?",
                (token,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO idempotency_receipt(
                        idempotency_key, status, owner, lease_until, result_blob, created_at, updated_at
                    ) VALUES (?, 'inflight', ?, ?, NULL, ?, ?)
                    """,
                    (token, actor, lease_until, current, current),
                )
                connection.commit()
                return IdempotencyClaim("acquired", token, actor, lease_until)
            status = str(row["status"] or "")
            if status == "committed":
                result = None
                blob = row["result_blob"]
                if blob is not None:
                    try:
                        result = pickle.loads(blob)
                    except Exception:
                        result = None
                connection.commit()
                return IdempotencyClaim("committed", token, str(row["owner"] or ""), 0.0, result)
            existing_lease = float(row["lease_until"] or 0.0)
            existing_owner = str(row["owner"] or "")
            if existing_lease > current and existing_owner != actor:
                connection.commit()
                return IdempotencyClaim("busy", token, existing_owner, existing_lease)
            connection.execute(
                """
                UPDATE idempotency_receipt
                   SET status = 'inflight', owner = ?, lease_until = ?, result_blob = NULL, updated_at = ?
                 WHERE idempotency_key = ?
                """,
                (actor, lease_until, current, token),
            )
            connection.commit()
            return IdempotencyClaim("acquired", token, actor, lease_until)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_idempotency(self, key: str, *, owner: str, result: Any, now: float | None = None) -> bool:
        token = str(key or "").strip()
        if not token:
            return True
        current = float(self._clock() if now is None else now)
        blob = sqlite3.Binary(pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE idempotency_receipt
                   SET status = 'committed', lease_until = 0, result_blob = ?, updated_at = ?
                 WHERE idempotency_key = ? AND status = 'inflight' AND owner = ?
                """,
                (blob, current, token, str(owner or "")),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def abandon_idempotency(self, key: str, *, owner: str) -> bool:
        token = str(key or "").strip()
        if not token:
            return True
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM idempotency_receipt WHERE idempotency_key = ? AND status = 'inflight' AND owner = ?",
                (token, str(owner or "")),
            )
            connection.commit()
            return cursor.rowcount == 1
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def prune_idempotency(self, *, older_than_seconds: float = 604800.0, now: float | None = None) -> int:
        current = float(self._clock() if now is None else now)
        cutoff = current - max(60.0, float(older_than_seconds))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM idempotency_receipt WHERE status = 'committed' AND updated_at < ?",
                (cutoff,),
            )
            connection.commit()
            return max(0, int(cursor.rowcount))
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def snapshot(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            states = connection.execute(
                "SELECT state_key, revision, updated_at FROM runtime_state ORDER BY state_key"
            ).fetchall()
            receipts = connection.execute(
                "SELECT status, COUNT(*) AS count FROM idempotency_receipt GROUP BY status"
            ).fetchall()
        finally:
            connection.close()
        return {
            "backend": "sqlite",
            "path": str(self.path),
            "states": [
                {"key": str(row["state_key"]), "revision": int(row["revision"]), "updated_at": float(row["updated_at"])}
                for row in states
            ],
            "idempotency": {str(row["status"]): int(row["count"]) for row in receipts},
        }


_STORE_CACHE: dict[str, SQLiteAtomicStateStore] = {}
_STORE_CACHE_LOCK = threading.RLock()


def atomic_store_from_env(*, logger: Any | None = None, clock=None) -> SQLiteAtomicStateStore | None:
    backend = str(os.getenv("ERIS_SOCIAL_ATOMIC_BACKEND", "sqlite") or "sqlite").strip().lower()
    if backend in {"", "0", "off", "none", "memory", "disabled"}:
        return None
    # Unit tests remain hermetic unless they explicitly request a backend.
    if os.getenv("PYTEST_CURRENT_TEST") and "ERIS_SOCIAL_ATOMIC_DB" not in os.environ:
        return None
    if backend != "sqlite":
        raise ValueError(f"unsupported ERIS_SOCIAL_ATOMIC_BACKEND: {backend}")
    path = str(os.getenv("ERIS_SOCIAL_ATOMIC_DB", "memory/eris_social_runtime.sqlite3") or "").strip()
    if not path:
        return None
    resolved = str(Path(path).expanduser().resolve())
    with _STORE_CACHE_LOCK:
        store = _STORE_CACHE.get(resolved)
        if store is None:
            store = SQLiteAtomicStateStore(resolved, logger=logger, clock=clock)
            _STORE_CACHE[resolved] = store
        return store


def unique_worker_id(prefix: str = "worker") -> str:
    return f"{str(prefix or 'worker')}:{os.getpid()}:{threading.get_ident()}:{uuid.uuid4().hex[:8]}"


__all__ = [
    "AtomicStateConflict",
    "AtomicStateError",
    "AtomicStateRecord",
    "IdempotencyClaim",
    "SQLiteAtomicStateStore",
    "SQLiteStateTransaction",
    "atomic_store_from_env",
    "unique_worker_id",
]

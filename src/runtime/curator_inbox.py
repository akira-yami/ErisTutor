# -*- coding: utf-8 -*-
"""SQLite inbox cho curator: evidence không mất do timeout/restart.

Payload chứa text user, lưu cạnh memory core như dữ liệu memory khác. DB không
được đưa vào git/log; chỉ log envelope_id và error-kind (không log raw text).
SQLite transaction chỉ bảo đảm *at-least-once*; reducer/authority vẫn cần dedupe
bằng evidence_id khi restart bất ngờ giữa apply và ACK.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping


class CuratorInbox:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS curator_inbox (
                envelope_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                operations TEXT NOT NULL DEFAULT '[]', attempts INTEGER NOT NULL DEFAULT 0,
                next_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_curator_pending ON curator_inbox (next_at, status)")

    @contextmanager
    def _db(self):
        # sqlite3.Connection.__exit__ chỉ commit, KHÔNG đóng connection.
        # Đóng trong finally để background poll không rò FD lâu dài.
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def put(self, envelope: Any) -> None:
        payload = {
            "text": envelope.text,
            "turn_id": envelope.turn_id,
            "evidence_ids": list(envelope.evidence_ids),
            "source": envelope.source,
            "priority": envelope.priority,
            "context": dict(envelope.context),
            "created_at": envelope.created_at,
        }
        with self._lock, self._db() as db:
            db.execute("""INSERT OR IGNORE INTO curator_inbox
                (envelope_id, payload, updated_at) VALUES (?, ?, ?)""",
                (envelope.envelope_id, json.dumps(payload, ensure_ascii=False, default=str), time.time()))

    def due(self) -> tuple[str, Mapping[str, Any], str, list[dict[str, Any]]] | None:
        with self._lock, self._db() as db:
            row = db.execute("""SELECT envelope_id, payload, status, operations FROM curator_inbox
                WHERE next_at <= ? AND status IN ('pending','proposed') ORDER BY updated_at ASC LIMIT 1""", (time.time(),)).fetchone()
        if not row:
            return None
        return row[0], json.loads(row[1]), row[2], json.loads(row[3])

    def proposed(self, envelope_id: str, operations: list[dict[str, Any]]) -> None:
        with self._lock, self._db() as db:
            db.execute("""UPDATE curator_inbox SET status='proposed', operations=?,
                next_at=0, updated_at=? WHERE envelope_id=?""",
                (json.dumps(operations, ensure_ascii=False, default=str), time.time(), envelope_id))

    def retry(self, envelope_id: str, *, max_attempts: int = 3) -> None:
        with self._lock, self._db() as db:
            row = db.execute("SELECT attempts FROM curator_inbox WHERE envelope_id=?", (envelope_id,)).fetchone()
            if row is None:
                return
            attempts = row[0] + 1
            if attempts >= max_attempts:
                # Ngừng vòng lặp vô tận sau max_attempts để tránh đốt cháy proxy/account
                db.execute(
                    "UPDATE curator_inbox SET attempts=?, status='failed', next_at=0, updated_at=? WHERE envelope_id=?",
                    (attempts, time.time(), envelope_id),
                )
                return
            # Exponential backoff hữu hạn, delay tối thiểu 30s để không spam
            jitter = (sum(envelope_id.encode("utf-8")) % 13) / 10.0
            delay = max(30.0, min(300.0, float(2 ** min(attempts, 8)))) + jitter
            db.execute("UPDATE curator_inbox SET attempts=?, next_at=?, updated_at=? WHERE envelope_id=?",
                       (attempts, time.time() + delay, time.time(), envelope_id))

    def done(self, envelope_id: str) -> None:
        with self._lock, self._db() as db:
            db.execute("DELETE FROM curator_inbox WHERE envelope_id=?", (envelope_id,))

    def count(self) -> int:
        with self._lock, self._db() as db:
            return int(db.execute("SELECT COUNT(*) FROM curator_inbox").fetchone()[0])

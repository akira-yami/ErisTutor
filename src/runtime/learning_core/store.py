"""SQLite-backed durable store for the learning kernel.

Schema: learning.sqlite3 (separate from eris.sqlite3 / eris_social.sqlite3).
One DB file per Eris home dir: <eris_home>/mentor/learning.sqlite3

Tables:
  learning_paths    – LearningPath definitions (JSON)
  knowledge_points  – KnowledgePoint definitions (JSON)
  mastery_records   – Per-KP mastery snapshots
  learning_evidence – Append-only evidence ledger
  review_items      – SRS queue (one row per KP per path)
  interactions      – PendingInteraction lifecycle

This module only handles persistence and simple queries.
All mastery computation lives in mastery.py / policy.py / scheduler.py.

Thread safety: one RLock per store instance; SQLite WAL mode.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Sequence

from runtime.learning_core.belief_model import LearnerBeliefState
from runtime.learning_core.review_memory import ReviewMemory
from runtime.learning_core.assessment_store import AssessmentStoreMixin, DDL as ASSESSMENT_DDL
from runtime.learning_core.catalog import CatalogStoreMixin, DDL as CATALOG_DDL, validate_path, revision_for
from runtime.learning_core.contracts import (
    KnowledgePoint,
    LearningEvidence,
    LearningPath,
    MasteryRecord,
    PendingInteraction,
    ReviewItem,
)


_SCHEMA_VERSION = 1
_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_paths (
    path_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    goal        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'user',
    payload     TEXT NOT NULL,           -- full JSON
    created_at  REAL NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS knowledge_points (
    kp_id       TEXT PRIMARY KEY,
    path_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    kp_type     TEXT NOT NULL DEFAULT 'concept',
    payload     TEXT NOT NULL,
    FOREIGN KEY (path_id) REFERENCES learning_paths(path_id)
);

CREATE TABLE IF NOT EXISTS mastery_records (
    kp_id              TEXT PRIMARY KEY,
    mastery            REAL NOT NULL DEFAULT 0,
    confidence         REAL NOT NULL DEFAULT 0,
    stage              TEXT NOT NULL DEFAULT 'unknown',
    attempt_count      INTEGER NOT NULL DEFAULT 0,
    correct_count      INTEGER NOT NULL DEFAULT 0,
    last_evidence_at   REAL NOT NULL DEFAULT 0,
    next_review_at     REAL NOT NULL DEFAULT 0,
    retrievability     REAL NOT NULL DEFAULT 1,
    error_codes        TEXT NOT NULL DEFAULT '[]',
    updated_at         REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS learning_evidence (
    evidence_id     TEXT PRIMARY KEY,
    kp_id           TEXT NOT NULL,
    evidence_type   TEXT NOT NULL,
    strength        REAL NOT NULL DEFAULT 0.5,
    correct         INTEGER NOT NULL DEFAULT 1,
    interaction_id  TEXT NOT NULL DEFAULT '',
    turn_id         TEXT NOT NULL DEFAULT '',
    recorded_at     REAL NOT NULL,
    error_code      TEXT NOT NULL DEFAULT '',
    metadata        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_evidence_kp ON learning_evidence(kp_id, recorded_at);

-- BKT shadow state: KHÔNG thay mastery_records khi chưa được hiệu chỉnh.
CREATE TABLE IF NOT EXISTS learner_beliefs (
    kp_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    evidence_seq INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);

-- Separate heuristic memory state from mastery and old review schedule.
CREATE TABLE IF NOT EXISTS review_memory (
    kp_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS review_items (
    kp_id           TEXT PRIMARY KEY,
    due_at          REAL NOT NULL,
    forgetting_risk REAL NOT NULL DEFAULT 0,
    interval_days   REAL NOT NULL DEFAULT 1,
    ease_factor     REAL NOT NULL DEFAULT 2.5
);

CREATE TABLE IF NOT EXISTS interactions (
    interaction_id  TEXT PRIMARY KEY,
    path_id         TEXT NOT NULL,
    kp_id           TEXT NOT NULL,
    interaction_type TEXT NOT NULL,
    question_text   TEXT NOT NULL,
    expected_answer TEXT NOT NULL,
    options         TEXT NOT NULL DEFAULT '[]',
    correct_index   INTEGER NOT NULL DEFAULT -1,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      REAL NOT NULL DEFAULT 0,
    expires_at      REAL NOT NULL DEFAULT 0,
    answered_at     REAL NOT NULL DEFAULT 0,
    turn_id         TEXT NOT NULL DEFAULT '',
    review_recall INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_interaction_kp ON interactions(path_id, kp_id, status);
"""


class LearningStore(AssessmentStoreMixin, CatalogStoreMixin):
    """Thread-safe SQLite store for the learning kernel."""

    def __init__(self, path: Path | None = None) -> None:
        from runtime.workspace_manager import default_eris_home

        self._path = path or (default_eris_home() / "mentor" / "learning.sqlite3")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._connect()

    # ------------------------------------------------------------------
    # Connection / schema
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_DDL)
        conn.executescript(ASSESSMENT_DDL)
        conn.executescript(CATALOG_DDL)
        # ALTER chỉ cho DB cũ; DB mới có sẵn column trong DDL.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(interactions)")}
        if "review_recall" not in columns:
            conn.execute("ALTER TABLE interactions ADD COLUMN review_recall INTEGER NOT NULL DEFAULT 0")
        conn.commit()
        self._conn = conn

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._connect()
        assert self._conn is not None
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    # ------------------------------------------------------------------
    # Small runtime metadata
    # ------------------------------------------------------------------

    def set_meta(self, key: str, value: Any) -> None:
        """Persist một JSON value nhỏ trong ``schema_meta``.

        Dùng cho con trỏ runtime như active_path/active_kp. Không dùng bảng này
        để lưu evidence hoặc mastery; hai loại dữ liệu đó có bảng authoritative riêng.
        """
        meta_key = str(key or "").strip()[:120]
        if not meta_key:
            raise ValueError("meta key is required")
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._db().execute(
                """
                INSERT INTO schema_meta(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (meta_key, payload),
            )
            self._db().commit()

    def get_meta(self, key: str, default: Any = None) -> Any:
        meta_key = str(key or "").strip()[:120]
        if not meta_key:
            return default
        with self._lock:
            row = self._db().execute("SELECT value FROM schema_meta WHERE key = ?", (meta_key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def compare_and_set_meta_revision(self, key: str, expected_revision: int, value: dict) -> bool:
        """Atomic CAS for revisioned UI cursors, including separate connections."""
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            db = self._db()
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT value FROM schema_meta WHERE key=?", (key,)).fetchone()
                current = json.loads(row["value"]) if row else {"revision": 0}
                if current.get("revision") != expected_revision:
                    db.rollback()
                    return False
                db.execute("INSERT INTO schema_meta(key,value) VALUES (?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, payload))
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    # ------------------------------------------------------------------
    # LearningPath CRUD
    # ------------------------------------------------------------------

    def upsert_path(self, path, *, now=None, expected_revision=None, reason="register", source_refs=()):
        validate_path(path)
        ts = float(now if now is not None else time.time())
        payload = json.dumps(path.to_dict(), ensure_ascii=False)
        revision = revision_for(path)
        with self._lock:
            db = self._db()
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT payload FROM learning_paths WHERE path_id=?", (path.path_id,)).fetchone()
                base = revision_for(self.load_path(path.path_id)) if row else ""
                if expected_revision is not None and base != expected_revision:
                    raise ValueError("stale_curriculum_revision")
                if row and base != revision and expected_revision is None:
                    raise ValueError("curriculum_revision_required")
                if (
                    path.domain_id
                    and not db.execute("SELECT 1 FROM learning_domains WHERE domain_id=?", (path.domain_id,)).fetchone()
                ):
                    raise ValueError("unknown_path_domain")
                for ref in source_refs:
                    span = self.source_span(ref)
                    if span is None or span["stale"] or span["domain_id"] != path.domain_id:
                        raise ValueError("curriculum_source_stale_or_out_of_scope")
                for kp in path.knowledge_points:
                    old = db.execute("SELECT payload FROM knowledge_points WHERE kp_id=?", (kp.kp_id,)).fetchone()
                    if path.domain_id and not old:
                        namespace = json.loads(
                            db.execute(
                                "SELECT payload FROM learning_domains WHERE domain_id=?", (path.domain_id,)
                            ).fetchone()["payload"]
                        )["skill_namespace"]
                        if not kp.kp_id.startswith(namespace + ":"):
                            raise ValueError("skill_requires_domain_namespace")
                    if old and json.loads(old["payload"]) != kp.to_dict():
                        old_point = json.loads(old["payload"])
                        if (old_point["kp_type"], old_point.get("description", "")) != (kp.kp_type, kp.description):
                            raise ValueError("skill_meaning_change_requires_new_id")
                        # Titles/order may change in a single path; shared skill
                        # meaning is immutable and requires a new skill version.
                        others = db.execute(
                            "SELECT 1 FROM learning_path_skills WHERE kp_id=? AND path_id<>?", (kp.kp_id, path.path_id)
                        ).fetchone()
                        if others:
                            raise ValueError("shared_skill_identity_conflict")
                db.execute(
                    "INSERT INTO learning_paths VALUES (?,?,?,?,?,?,?) ON CONFLICT(path_id) DO UPDATE SET "
                    "title=excluded.title,goal=excluded.goal,source=excluded.source,payload=excluded.payload,updated_at=excluded.updated_at",
                    (path.path_id, path.title, path.goal, path.source, payload, ts, ts),
                )
                db.execute("DELETE FROM learning_path_skills WHERE path_id=?", (path.path_id,))
                for ordinal, kp in enumerate(path.knowledge_points):
                    db.execute(
                        "INSERT INTO knowledge_points VALUES (?,?,?,?,?) ON CONFLICT(kp_id) DO UPDATE SET "
                        "title=excluded.title,kp_type=excluded.kp_type,payload=excluded.payload",
                        (kp.kp_id, path.path_id, kp.title, kp.kp_type, json.dumps(kp.to_dict(), ensure_ascii=False)),
                    )
                    db.execute("INSERT INTO learning_path_skills VALUES (?,?,?)", (path.path_id, kp.kp_id, ordinal))
                db.execute(
                    "INSERT OR IGNORE INTO learning_path_revisions VALUES (?,?,?,?,?,?)",
                    (
                        path.path_id,
                        revision,
                        base,
                        payload,
                        json.dumps({"reason": str(reason)[:800], "source_refs": list(source_refs)}),
                        ts,
                    ),
                )
                db.commit()
                return revision
            except BaseException:
                db.rollback()
                raise

    def load_path(self, path_id: str) -> LearningPath | None:
        with self._lock:
            row = self._db().execute("SELECT payload FROM learning_paths WHERE path_id = ?", (path_id,)).fetchone()
        if row is None:
            return None
        try:
            d = json.loads(row["payload"])
            return LearningPath(
                path_id=d["path_id"],
                title=d["title"],
                goal=d["goal"],
                knowledge_points=tuple(KnowledgePoint.from_mapping(kp) for kp in d.get("knowledge_points", [])),
                source=d.get("source", "user"),
                domain_id=d.get("domain_id", ""),
            )
        except Exception:
            return None

    def list_path_ids(self) -> list[str]:
        with self._lock:
            rows = self._db().execute("SELECT path_id FROM learning_paths ORDER BY updated_at DESC").fetchall()
        return [r["path_id"] for r in rows]

    # ------------------------------------------------------------------
    # MasteryRecord CRUD
    # ------------------------------------------------------------------

    def load_mastery(self, kp_id: str) -> MasteryRecord:
        with self._lock:
            row = self._db().execute("SELECT * FROM mastery_records WHERE kp_id = ?", (kp_id,)).fetchone()
        if row is None:
            return MasteryRecord.blank(kp_id)
        return self._row_to_mastery(row)

    def load_mastery_batch(self, kp_ids: Sequence[str]) -> dict[str, MasteryRecord]:
        if not kp_ids:
            return {}
        placeholders = ",".join("?" * len(kp_ids))
        with self._lock:
            rows = (
                self._db()
                .execute(f"SELECT * FROM mastery_records WHERE kp_id IN ({placeholders})", list(kp_ids))
                .fetchall()
            )
        result = {kp_id: MasteryRecord.blank(kp_id) for kp_id in kp_ids}
        for row in rows:
            result[row["kp_id"]] = self._row_to_mastery(row)
        return result

    def save_mastery(self, record: MasteryRecord, *, now: float | None = None) -> None:
        ts = float(now if now is not None else time.time())
        with self._lock:
            self._db().execute(
                """
                INSERT INTO mastery_records
                    (kp_id, mastery, confidence, stage, attempt_count, correct_count,
                     last_evidence_at, next_review_at, retrievability, error_codes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kp_id) DO UPDATE SET
                    mastery=excluded.mastery, confidence=excluded.confidence,
                    stage=excluded.stage, attempt_count=excluded.attempt_count,
                    correct_count=excluded.correct_count,
                    last_evidence_at=excluded.last_evidence_at,
                    next_review_at=excluded.next_review_at,
                    retrievability=excluded.retrievability,
                    error_codes=excluded.error_codes,
                    updated_at=excluded.updated_at
                """,
                (
                    record.kp_id,
                    record.mastery,
                    record.confidence,
                    record.stage,
                    record.attempt_count,
                    record.correct_count,
                    record.last_evidence_at,
                    record.next_review_at,
                    record.retrievability,
                    json.dumps(list(record.error_codes)),
                    ts,
                ),
            )
            self._db().commit()

    @staticmethod
    def _row_to_mastery(row: sqlite3.Row) -> MasteryRecord:
        try:
            error_codes = tuple(json.loads(row["error_codes"]) or [])
        except Exception:
            error_codes = ()
        return MasteryRecord(
            kp_id=row["kp_id"],
            mastery=float(row["mastery"]),
            confidence=float(row["confidence"]),
            stage=row["stage"],
            attempt_count=int(row["attempt_count"]),
            correct_count=int(row["correct_count"]),
            last_evidence_at=float(row["last_evidence_at"]),
            next_review_at=float(row["next_review_at"]),
            retrievability=float(row["retrievability"]),
            error_codes=error_codes,
        )

    # ------------------------------------------------------------------
    # LearningEvidence (append-only ledger)
    # ------------------------------------------------------------------

    def append_evidence(self, evidence: LearningEvidence) -> bool:
        """Idempotent insert; returns False if evidence_id already exists."""
        with self._lock:
            existing = (
                self._db()
                .execute("SELECT 1 FROM learning_evidence WHERE evidence_id = ?", (evidence.evidence_id,))
                .fetchone()
            )
            if existing:
                return False
            self._db().execute(
                """
                INSERT INTO learning_evidence
                    (evidence_id, kp_id, evidence_type, strength, correct,
                     interaction_id, turn_id, recorded_at, error_code, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    evidence.kp_id,
                    evidence.evidence_type,
                    evidence.strength,
                    int(evidence.correct),
                    evidence.interaction_id,
                    evidence.turn_id,
                    evidence.recorded_at,
                    evidence.error_code,
                    evidence.metadata,
                ),
            )
            self._db().commit()
        return True

    def load_belief(self, kp_id: str) -> LearnerBeliefState:
        """Snapshot chỉ-đọc: xác suất BKT *chưa hiệu chỉnh*, không phải mastery chính thức."""
        with self._lock:
            row = self._db().execute("SELECT payload FROM learner_beliefs WHERE kp_id=?", (kp_id,)).fetchone()
        if row is None:
            return LearnerBeliefState(kp_id=kp_id)
        try:
            return LearnerBeliefState.from_mapping(json.loads(row["payload"]))
        except (TypeError, ValueError, KeyError):
            return LearnerBeliefState(kp_id=kp_id)

    def load_review_memory(self, kp_id: str) -> ReviewMemory | None:
        """Read-model D/S/R bền vững; không có record không đồng nghĩa đã nhớ."""
        with self._lock:
            row = self._db().execute("SELECT payload FROM review_memory WHERE kp_id=?", (kp_id,)).fetchone()
        if row is None:
            return None
        try:
            return ReviewMemory.from_mapping(json.loads(row["payload"]))
        except (TypeError, ValueError, OverflowError):
            return None

    def load_review_memory_batch(self, ids: Sequence[str]) -> dict[str, ReviewMemory]:
        # Bounded by caller. Each read is short; avoids schema change to old review_items.
        return {k: state for k in ids if (state := self.load_review_memory(k)) is not None}

    def commit_evidence_bundle(self, evidence, project, *, now, attempt=None, interaction_update=None) -> bool:
        return self.commit_assessment(attempt, evidence, project, now=now, interaction_update=interaction_update)

    def commit_assessment(self, attempt, evidence, project, *, now, interaction_update=None) -> bool:
        """One transaction: attempt, effective evidence and all learning projections."""
        with self._lock:
            db = self._db()
            db.execute("BEGIN IMMEDIATE")
            try:
                if interaction_update is not None:
                    changed = db.execute(
                        "UPDATE interactions SET status='answered',turn_id=?,answered_at=? "
                        "WHERE interaction_id=? AND status='pending' AND (expires_at=0 OR expires_at>?)",
                        (evidence.turn_id, now, interaction_update, now),
                    ).rowcount
                    if changed != 1:
                        db.rollback()
                        return False
                if attempt is not None and not self._insert_attempt(
                    db, attempt, evidence.evidence_id if evidence else ""
                ):
                    db.rollback()
                    return False
                if evidence is not None:
                    if db.execute(
                        "SELECT 1 FROM learning_evidence WHERE evidence_id=?", (evidence.evidence_id,)
                    ).fetchone():
                        db.rollback()
                        return False
                    db.execute(
                        "INSERT INTO learning_evidence VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            evidence.evidence_id,
                            evidence.kp_id,
                            evidence.evidence_type,
                            evidence.strength,
                            int(evidence.correct),
                            evidence.interaction_id,
                            evidence.turn_id,
                            evidence.recorded_at,
                            evidence.error_code,
                            evidence.metadata,
                        ),
                    )
                kp_id = attempt.kp_id if attempt else evidence.kp_id
                self._reproject(db, kp_id, project, now)
                db.commit()
                return True
            except BaseException:
                db.rollback()
                raise

    def _reproject(self, db, kp_id, project, now):
        rows = db.execute(
            "SELECT * FROM effective_learning_evidence WHERE kp_id=? ORDER BY recorded_at,evidence_id", (kp_id,)
        ).fetchall()
        ledger = [self._row_to_evidence(row) for row in rows]
        old = db.execute("SELECT * FROM review_items WHERE kp_id=?", (kp_id,)).fetchone()
        current = (
            ReviewItem(**{k: old[k] for k in ("kp_id", "due_at", "forgetting_risk", "interval_days", "ease_factor")})
            if old
            else None
        )
        old_memory = db.execute("SELECT payload FROM review_memory WHERE kp_id=?", (kp_id,)).fetchone()
        memory = ReviewMemory.from_mapping(json.loads(old_memory["payload"])) if old_memory else None
        result = project(ledger, current, memory)
        mastery, review, belief, memory = result[:4]
        if any(x is not None and x.kp_id != kp_id for x in (mastery, review, belief, memory)):
            raise ValueError("projection_kp_mismatch")
        data = {**mastery.to_dict(), "error_codes": json.dumps(list(mastery.error_codes)), "updated_at": now}
        db.execute(
            "INSERT OR REPLACE INTO mastery_records VALUES "
            "(:kp_id,:mastery,:confidence,:stage,:attempt_count,:correct_count,:last_evidence_at,"
            ":next_review_at,:retrievability,:error_codes,:updated_at)",
            data,
        )
        for table in ("review_items", "review_memory", "learning_retention_shadow"):
            db.execute("DELETE FROM " + table + " WHERE kp_id=?", (kp_id,))
        if review:
            db.execute(
                "INSERT INTO review_items VALUES (?,?,?,?,?)",
                (kp_id, review.due_at, review.forgetting_risk, review.interval_days, review.ease_factor),
            )
        if memory:
            db.execute("INSERT INTO review_memory VALUES (?,?,?)", (kp_id, json.dumps(memory.to_dict()), now))
        db.execute(
            "INSERT OR REPLACE INTO learner_beliefs VALUES (?,?,?,?)",
            (kp_id, json.dumps(belief.to_dict()), belief.evidence_seq, now),
        )
        if len(result) > 4:
            for item_id, payload in result[4].items():
                db.execute(
                    "INSERT INTO learning_retention_shadow VALUES (?,?,?)", (kp_id, item_id, json.dumps(payload))
                )

    def forget_attempt(self, attempt_id, project, *, now):
        """Purge the whole correction chain and rebuild; tombstones contain only IDs."""
        with self._lock:
            db = self._db()
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT kp_id,payload FROM learning_attempts WHERE attempt_id=?", (attempt_id,)
                ).fetchone()
                if not row:
                    db.rollback()
                    return False
                kp_id = row["kp_id"]
                rows = db.execute(
                    "SELECT attempt_id,evidence_id,payload FROM learning_attempts WHERE kp_id=?", (kp_id,)
                ).fetchall()
                ids = {attempt_id}
                changed = True
                while changed:
                    changed = False
                    for item in rows:
                        parent = json.loads(item["payload"]).get("supersedes_attempt_id", "")
                        if item["attempt_id"] in ids or parent in ids:
                            before = len(ids)
                            ids.add(item["attempt_id"])
                            if parent:
                                ids.add(parent)
                            changed |= len(ids) != before
                for item in rows:
                    if item["attempt_id"] not in ids:
                        continue
                    raw = json.loads(item["payload"])
                    db.execute("INSERT OR IGNORE INTO learning_forget_tombstones VALUES (?)", (item["attempt_id"],))
                    if raw.get("interaction_id"):
                        db.execute(
                            "INSERT OR IGNORE INTO learning_forgotten_interactions VALUES (?)", (raw["interaction_id"],)
                        )
                    db.execute("DELETE FROM learning_evidence WHERE evidence_id=?", (item["evidence_id"],))
                    db.execute("DELETE FROM interactions WHERE interaction_id=?", (raw.get("interaction_id", ""),))
                    db.execute(
                        "DELETE FROM learning_assistance WHERE interaction_id=?", (raw.get("interaction_id", ""),)
                    )
                    db.execute("DELETE FROM learning_activities WHERE activity_id=?", (raw.get("activity_id", ""),))
                    db.execute("DELETE FROM learning_attempts WHERE attempt_id=?", (item["attempt_id"],))
                self._reproject(db, kp_id, project, now)
                db.commit()
                return True
            except BaseException:
                db.rollback()
                raise

    def repeated_error_patterns(self, kp_id: str, *, min_independent: int = 2, limit: int = 4) -> list[dict[str, Any]]:
        """Các lỗi có tối thiểu N nguồn độc lập, không phải chẩn đoán tâm lý.

        Nếu retry trùng interaction ID HOẶC cùng turn user, chỉ tính một.
        Đếm trên 512 bản ghi gần nhất để không mở rộng truy vấn vô hạn.
        """
        with self._lock:
            rows = (
                self._db()
                .execute(
                    """SELECT error_code, interaction_id, turn_id, evidence_id
                   FROM effective_learning_evidence
                   WHERE kp_id = ? AND correct = 0 AND error_code <> ''
                   ORDER BY recorded_at DESC, evidence_id DESC LIMIT 512""",
                    (kp_id,),
                )
                .fetchall()
            )
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = str(row["error_code"])[:80]
            interaction = str(row["interaction_id"] or "")
            turn = str(row["turn_id"] or "")
            fallback = str(row["evidence_id"] or "")
            group = groups.setdefault(code, {"interactions": set(), "turns": set(), "legacy": set(), "count": 0})
            if (
                (interaction and interaction in group["interactions"])
                or (turn and turn in group["turns"])
                or (not interaction and not turn and fallback in group["legacy"])
            ):
                continue
            if interaction:
                group["interactions"].add(interaction)
            if turn:
                group["turns"].add(turn)
            if not interaction and not turn:
                group["legacy"].add(fallback)
            group["count"] += 1
        threshold = max(2, min(10, int(min_independent)))
        ranked = sorted(
            ((code, group["count"]) for code, group in groups.items() if group["count"] >= threshold),
            key=lambda item: (-item[1], item[0]),
        )
        return [
            {"error_code": code, "independent_count": count, "status": "repeat_observed_not_diagnosis"}
            for code, count in ranked[: max(1, min(8, int(limit)))]
        ]

    def load_recent_evidence(self, kp_id: str, *, limit: int = 16) -> list[LearningEvidence]:
        """Đọc N evidence mới nhất; không thay thứ tự API load_evidence cũ.

        Cần cho LEARN-04: load_evidence() trả N *cũ nhất* nên dùng nó để
        chọn chiến thuật dài hạn sẽ bỏ qua các bài làm sau nhiều tháng.
        """
        with self._lock:
            rows = (
                self._db()
                .execute(
                    "SELECT * FROM effective_learning_evidence WHERE kp_id = ? "
                    "ORDER BY recorded_at DESC, evidence_id DESC LIMIT ?",
                    (kp_id, max(1, min(32, int(limit)))),
                )
                .fetchall()
            )
        return [self._row_to_evidence(row) for row in rows]

    def load_evidence(self, kp_id: str, *, limit: int = 100) -> list[LearningEvidence]:
        with self._lock:
            rows = (
                self._db()
                .execute(
                    "SELECT * FROM effective_learning_evidence WHERE kp_id = ? ORDER BY recorded_at ASC LIMIT ?",
                    (kp_id, limit),
                )
                .fetchall()
            )
        return [self._row_to_evidence(r) for r in rows]

    @staticmethod
    def _row_to_evidence(row: sqlite3.Row) -> LearningEvidence:
        return LearningEvidence(
            evidence_id=row["evidence_id"],
            kp_id=row["kp_id"],
            evidence_type=row["evidence_type"],
            strength=float(row["strength"]),
            correct=bool(row["correct"]),
            interaction_id=row["interaction_id"],
            turn_id=row["turn_id"],
            recorded_at=float(row["recorded_at"]),
            error_code=row["error_code"],
            metadata=row["metadata"],
        )

    # ------------------------------------------------------------------
    # ReviewItems (SRS queue)
    # ------------------------------------------------------------------

    def upsert_review_item(self, item: ReviewItem) -> None:
        with self._lock:
            self._db().execute(
                """
                INSERT INTO review_items (kp_id, due_at, forgetting_risk, interval_days, ease_factor)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(kp_id) DO UPDATE SET
                    due_at=excluded.due_at, forgetting_risk=excluded.forgetting_risk,
                    interval_days=excluded.interval_days, ease_factor=excluded.ease_factor
                """,
                (item.kp_id, item.due_at, item.forgetting_risk, item.interval_days, item.ease_factor),
            )
            self._db().commit()

    def load_review_items(self, kp_ids: Sequence[str] | None = None) -> list[ReviewItem]:
        with self._lock:
            if kp_ids is None:
                rows = self._db().execute("SELECT * FROM review_items").fetchall()
            else:
                if not kp_ids:
                    return []
                placeholders = ",".join("?" * len(kp_ids))
                rows = (
                    self._db()
                    .execute(f"SELECT * FROM review_items WHERE kp_id IN ({placeholders})", list(kp_ids))
                    .fetchall()
                )
        return [
            ReviewItem(
                kp_id=r["kp_id"],
                due_at=float(r["due_at"]),
                forgetting_risk=float(r["forgetting_risk"]),
                interval_days=float(r["interval_days"]),
                ease_factor=float(r["ease_factor"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # PendingInteraction persistence
    # ------------------------------------------------------------------

    def upsert_interaction(self, interaction: PendingInteraction) -> bool:
        """Idempotent insert. Returns False if interaction_id already exists."""
        with self._lock:
            existing = (
                self._db()
                .execute(
                    "SELECT status FROM interactions WHERE interaction_id = ?",
                    (interaction.interaction_id,),
                )
                .fetchone()
            )
            if existing is not None:
                return False
            # Cancel prior pending interaction for same (path, kp)
            self._db().execute(
                """
                UPDATE interactions SET status = 'cancelled'
                WHERE path_id = ? AND kp_id = ? AND status = 'pending'
                """,
                (interaction.path_id, interaction.kp_id),
            )
            self._db().execute(
                """
                INSERT INTO interactions
                    (interaction_id, path_id, kp_id, interaction_type, question_text,
                     expected_answer, options, correct_index, status,
                     created_at, expires_at, answered_at, turn_id, review_recall)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    interaction.interaction_id,
                    interaction.path_id,
                    interaction.kp_id,
                    interaction.interaction_type,
                    interaction.question_text,
                    interaction.expected_answer,
                    json.dumps(list(interaction.options)),
                    interaction.correct_index,
                    interaction.status,
                    interaction.created_at,
                    interaction.expires_at,
                    interaction.answered_at,
                    interaction.turn_id,
                    int(interaction.review_recall),
                ),
            )
            self._db().commit()
        return True

    def resolve_interaction(
        self, interaction_id: str, *, status: str, turn_id: str = "", answered_at: float = 0.0
    ) -> bool:
        with self._lock:
            res = self._db().execute(
                """
                UPDATE interactions SET status = ?, turn_id = ?, answered_at = ?
                WHERE interaction_id = ? AND status = 'pending'
                """,
                (status, turn_id[:120], answered_at, interaction_id),
            )
            self._db().commit()
        return res.rowcount > 0

    def load_pending_interaction(self, path_id: str, kp_id: str) -> PendingInteraction | None:
        with self._lock:
            row = (
                self._db()
                .execute(
                    """
                SELECT * FROM interactions
                WHERE path_id = ? AND kp_id = ? AND status = 'pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                    (path_id, kp_id),
                )
                .fetchone()
            )
        if row is None:
            return None
        return self._row_to_interaction(row)

    def load_interaction(self, interaction_id: str) -> PendingInteraction | None:
        with self._lock:
            row = (
                self._db().execute("SELECT * FROM interactions WHERE interaction_id = ?", (interaction_id,)).fetchone()
            )
        return self._row_to_interaction(row) if row else None

    @staticmethod
    def _row_to_interaction(row: sqlite3.Row) -> PendingInteraction:
        try:
            options = tuple(json.loads(row["options"]) or [])
        except Exception:
            options = ()
        return PendingInteraction(
            interaction_id=row["interaction_id"],
            path_id=row["path_id"],
            kp_id=row["kp_id"],
            interaction_type=row["interaction_type"],
            question_text=row["question_text"],
            expected_answer=row["expected_answer"],
            options=options,
            correct_index=int(row["correct_index"]),
            status=row["status"],
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
            answered_at=float(row["answered_at"]),
            turn_id=row["turn_id"],
            review_recall=bool(row["review_recall"]),
        )


__all__ = ["LearningStore"]

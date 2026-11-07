"""Assessment ledger on the existing LearningStore connection and transaction."""

from __future__ import annotations

import json
from .records import AssessmentAttempt

DDL = """
CREATE TABLE IF NOT EXISTS learning_attempts (
    attempt_id TEXT PRIMARY KEY, kp_id TEXT NOT NULL, evidence_id TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL, payload TEXT NOT NULL, recorded_at REAL NOT NULL,
    superseded_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_attempt_kp ON learning_attempts(kp_id, recorded_at);
CREATE VIEW IF NOT EXISTS effective_learning_evidence AS
    SELECT e.* FROM learning_evidence e WHERE NOT EXISTS (
        SELECT 1 FROM learning_attempts a WHERE a.evidence_id=e.evidence_id AND a.superseded_by<>''
    );
CREATE TABLE IF NOT EXISTS learning_assistance (
    interaction_id TEXT PRIMARY KEY, kp_id TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_retention_shadow (
    kp_id TEXT NOT NULL, review_item_id TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY(kp_id, review_item_id)
);
CREATE TABLE IF NOT EXISTS learning_forget_tombstones (
    attempt_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS learning_forgotten_interactions (
    interaction_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS learning_activities (
    activity_id TEXT PRIMARY KEY, payload TEXT NOT NULL
);
"""


class AssessmentStoreMixin:
    def latest_activity(self, preset):
        with self._lock:
            rows = self._db().execute("SELECT payload FROM learning_activities ORDER BY rowid DESC LIMIT 64").fetchall()
        return next((item for row in rows if (item := json.loads(row["payload"])).get("preset") == preset), None)

    def save_activity(self, item):
        with self._lock:
            self._db().execute(
                "INSERT OR REPLACE INTO learning_activities VALUES (?,?)",
                (item["activity_id"], json.dumps(item, ensure_ascii=False)),
            )
            self._db().commit()

    def load_activity(self, activity_id):
        with self._lock:
            row = (
                self._db()
                .execute("SELECT payload FROM learning_activities WHERE activity_id=?", (activity_id,))
                .fetchone()
            )
        return json.loads(row["payload"]) if row else None

    def _insert_attempt(self, db, attempt: AssessmentAttempt, evidence_id: str) -> bool:
        if (
            attempt.interaction_id
            and db.execute(
                "SELECT 1 FROM learning_forgotten_interactions WHERE interaction_id=?", (attempt.interaction_id,)
            ).fetchone()
        ):
            return False
        if attempt.result != "ungraded":
            for ref in attempt.source_refs:
                span = self.source_span(ref)
                path = self.load_path(attempt.path_id) if attempt.path_id else None
                if span is None or span["stale"] or (path and path.domain_id and span["domain_id"] != path.domain_id):
                    raise ValueError("stale_or_out_of_scope_source")
        if db.execute("SELECT 1 FROM learning_forget_tombstones WHERE attempt_id=?", (attempt.attempt_id,)).fetchone():
            return False
        previous = db.execute(
            "SELECT fingerprint FROM learning_attempts WHERE attempt_id=?", (attempt.attempt_id,)
        ).fetchone()
        if previous:
            if previous["fingerprint"] != attempt.fingerprint():
                raise ValueError("attempt_conflict_use_explicit_correction")
            return False
        if attempt.supersedes_attempt_id:
            original = db.execute(
                "SELECT kp_id, superseded_by FROM learning_attempts WHERE attempt_id=?",
                (attempt.supersedes_attempt_id,),
            ).fetchone()
            if not original or original["kp_id"] != attempt.kp_id or original["superseded_by"]:
                raise ValueError("correction_target_stale_or_mismatched")
            db.execute(
                "UPDATE learning_attempts SET superseded_by=? WHERE attempt_id=?",
                (attempt.attempt_id, attempt.supersedes_attempt_id),
            )
        db.execute(
            "INSERT INTO learning_attempts VALUES (?,?,?,?,?,?, '')",
            (
                attempt.attempt_id,
                attempt.kp_id,
                evidence_id,
                attempt.fingerprint(),
                json.dumps(attempt.to_dict(), ensure_ascii=False),
                attempt.recorded_at,
            ),
        )
        return True

    def load_attempts(self, kp_id: str = "", *, limit: int = 32, include_superseded=False):
        with self._lock:
            rows = (
                self._db()
                .execute(
                    "SELECT payload, superseded_by FROM learning_attempts WHERE (?='' OR kp_id=?) "
                    "AND (? OR superseded_by='') ORDER BY recorded_at DESC, attempt_id DESC LIMIT ?",
                    (kp_id, kp_id, bool(include_superseded), max(1, min(500, int(limit)))),
                )
                .fetchall()
            )
        return [{**json.loads(row["payload"]), "superseded_by": row["superseded_by"]} for row in rows]

    def load_attempt(self, attempt_id):
        with self._lock:
            row = (
                self._db().execute("SELECT payload FROM learning_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            )
        return AssessmentAttempt.from_mapping(json.loads(row["payload"])) if row else None

    def register_assistance(self, interaction_id, kp_id):
        with self._lock:
            self._db().execute(
                "INSERT OR IGNORE INTO learning_assistance VALUES (?,?,?)",
                (interaction_id, kp_id, json.dumps({"assistance": "independent", "events": []})),
            )
            self._db().commit()

    def record_assistance(self, interaction_id, *, event_id, assistance, hint_count=0):
        from .records import ASSISTANCE, identifier

        identifier(event_id)
        if assistance not in ASSISTANCE:
            raise ValueError("invalid_assistance")
        with self._lock:
            db = self._db()
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT payload FROM learning_assistance WHERE interaction_id=?", (interaction_id,)
                ).fetchone()
                if not row:
                    raise ValueError("interaction_not_delivered")
                data = json.loads(row["payload"])
                if event_id not in data["events"]:
                    if len(data["events"]) >= 100:
                        raise ValueError("assistance_budget_exceeded")
                    data["events"].append(event_id)
                    data["assistance"] = ASSISTANCE[
                        max(ASSISTANCE.index(data["assistance"]), ASSISTANCE.index(assistance))
                    ]
                    data["hint_count"] = max(data.get("hint_count", 0), min(100, max(0, int(hint_count))))
                    db.execute(
                        "UPDATE learning_assistance SET payload=? WHERE interaction_id=?",
                        (json.dumps(data), interaction_id),
                    )
                db.commit()
                return data
            except BaseException:
                db.rollback()
                raise

    def assistance_for(self, interaction_id):
        with self._lock:
            row = (
                self._db()
                .execute("SELECT payload FROM learning_assistance WHERE interaction_id=?", (interaction_id,))
                .fetchone()
            )
        return json.loads(row["payload"]) if row else {"assistance": "independent", "hint_count": 0}

    def retention_snapshot(self, kp_id="", *, kp_ids=None):
        clause = "WHERE (?='' OR kp_id=?)"
        params = [kp_id, kp_id]
        if kp_ids is not None:
            if not kp_ids:
                return {}
            clause = "WHERE kp_id IN (" + ",".join("?" for _ in kp_ids) + ")"
            params = list(kp_ids)
        with self._lock:
            rows = (
                self._db()
                .execute("SELECT review_item_id,payload FROM learning_retention_shadow " + clause, params)
                .fetchall()
            )
        return {row["review_item_id"]: json.loads(row["payload"]) for row in rows}

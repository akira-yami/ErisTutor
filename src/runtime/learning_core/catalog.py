"""Scoped source and curriculum persistence, using the same SQLite owner."""

from __future__ import annotations

import json
import re
from .records import DomainSpec, SourceSpan, digest, identifier

DDL = """
CREATE TABLE IF NOT EXISTS learning_domains (
    domain_id TEXT PRIMARY KEY, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learning_source_versions (
    domain_id TEXT NOT NULL, source_id TEXT NOT NULL, current_hash TEXT NOT NULL,
    PRIMARY KEY(domain_id, source_id)
);
CREATE TABLE IF NOT EXISTS learning_source_spans (
    span_id TEXT PRIMARY KEY, domain_id TEXT NOT NULL, source_id TEXT NOT NULL,
    content_hash TEXT NOT NULL, search_text TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_source_scope ON learning_source_spans(domain_id, source_id, content_hash);
CREATE TABLE IF NOT EXISTS learning_path_revisions (
    path_id TEXT NOT NULL, revision TEXT NOT NULL, base_revision TEXT NOT NULL,
    payload TEXT NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY(path_id, revision)
);
CREATE TABLE IF NOT EXISTS learning_path_skills (
    path_id TEXT NOT NULL, kp_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
    PRIMARY KEY(path_id, kp_id)
);
"""


def validate_path(path):
    identifier(path.path_id)
    if len(path.knowledge_points) > 256:
        raise ValueError("curriculum_budget_exceeded")
    for kp in path.knowledge_points:
        identifier(kp.kp_id)
    points = {kp.kp_id: kp for kp in path.knowledge_points}
    if len(points) != len(path.knowledge_points):
        raise ValueError("duplicate_skill")
    visiting, visited = set(), set()

    def visit(key):
        if key in visiting:
            raise ValueError("prerequisite_cycle")
        if key in visited:
            return
        visiting.add(key)
        for prerequisite in points[key].prerequisites:
            if prerequisite not in points:
                raise ValueError("unknown_prerequisite")
            visit(prerequisite)
        visiting.remove(key)
        visited.add(key)

    for key in points:
        visit(key)


def revision_for(path):
    return digest(path.to_dict())


def exact_diff(before, after, path=""):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            pointer = path + "/" + str(key).replace("~", "~0").replace("/", "~1")
            if key not in before or key not in after:
                changes.append({"path": pointer, "before": before.get(key), "after": after.get(key)})
            else:
                changes.extend(exact_diff(before[key], after[key], pointer))
        return changes
    return [{"path": path or "/", "before": before, "after": after}]


class CatalogStoreMixin:
    def register_domain(self, domain: DomainSpec):
        with self._lock:
            db = self._db()
            previous = db.execute(
                "SELECT payload FROM learning_domains WHERE domain_id=?", (domain.domain_id,)
            ).fetchone()
            if previous:
                old = json.loads(previous["payload"])
                if (old["skill_namespace"], old["version"]) != (domain.skill_namespace, domain.version):
                    raise ValueError("domain_namespace_or_version_is_immutable")
            db.execute(
                "INSERT OR REPLACE INTO learning_domains VALUES (?,?)",
                (domain.domain_id, json.dumps(domain.to_dict(), ensure_ascii=False)),
            )
            db.commit()

    def domains(self):
        with self._lock:
            rows = self._db().execute("SELECT payload FROM learning_domains ORDER BY domain_id").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def import_spans(self, spans):
        spans = tuple(spans)
        if not spans or len(spans) > 2048 or any(not isinstance(s, SourceSpan) for s in spans):
            raise ValueError("invalid_source_batch")
        versions = {(s.domain_id, s.source_id, s.content_hash) for s in spans}
        if len({(d, s) for d, s, _ in versions}) != len(versions):
            raise ValueError("conflicting_source_versions_in_batch")
        with self._lock:
            db = self._db()
            db.execute("BEGIN IMMEDIATE")
            try:
                for span in spans:
                    if not db.execute("SELECT 1 FROM learning_domains WHERE domain_id=?", (span.domain_id,)).fetchone():
                        raise ValueError("unknown_source_domain")
                    old = db.execute(
                        "SELECT payload FROM learning_source_spans WHERE span_id=?", (span.span_id,)
                    ).fetchone()
                    if old:
                        original = json.loads(old["payload"])
                        candidate = span.to_dict()
                        original.pop("imported_at", None)
                        candidate.pop("imported_at", None)
                        if original != candidate:
                            raise ValueError("source_span_identity_is_immutable")
                    db.execute(
                        "INSERT OR IGNORE INTO learning_source_spans VALUES (?,?,?,?,?,?)",
                        (
                            span.span_id,
                            span.domain_id,
                            span.source_id,
                            span.content_hash,
                            span.excerpt.casefold(),
                            json.dumps(span.to_dict(), ensure_ascii=False),
                        ),
                    )
                for domain_id, source_id, content_hash in versions:
                    db.execute(
                        "INSERT OR REPLACE INTO learning_source_versions VALUES (?,?,?)",
                        (domain_id, source_id, content_hash),
                    )
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def source_span(self, span_id):
        with self._lock:
            row = (
                self._db()
                .execute(
                    "SELECT s.payload, v.current_hash FROM learning_source_spans s "
                    "LEFT JOIN learning_source_versions v ON v.domain_id=s.domain_id AND v.source_id=s.source_id "
                    "WHERE s.span_id=?",
                    (span_id,),
                )
                .fetchone()
            )
        if not row:
            return None
        data = json.loads(row["payload"])
        return {**data, "stale": row["current_hash"] != data["content_hash"]}

    def search_sources(self, domain_id, query, *, source_ids=(), limit=6):
        identifier(domain_id)
        tokens = tuple(dict.fromkeys(re.findall(r"\w+", str(query).casefold())))[:12]
        if not tokens:
            return []
        score = "+".join("(instr(s.search_text,?)>0)" for _ in tokens)
        params = list(tokens) + [domain_id]
        scope = ""
        if source_ids:
            ids = tuple(identifier(s) for s in source_ids)
            if len(ids) > 32:
                raise ValueError("source_scope_too_large")
            scope = " AND s.source_id IN (" + ",".join("?" for _ in ids) + ")"
            params.extend(ids)
        params.append(max(1, min(16, int(limit))))
        with self._lock:
            rows = (
                self._db()
                .execute(
                    "SELECT s.payload, (" + score + ") AS score FROM learning_source_spans s "
                    "JOIN learning_source_versions v ON v.domain_id=s.domain_id AND v.source_id=s.source_id "
                    "AND v.current_hash=s.content_hash WHERE s.domain_id=?"
                    + scope
                    + " AND score>0 ORDER BY score DESC,s.span_id LIMIT ?",
                    params,
                )
                .fetchall()
            )
        return [{**json.loads(row["payload"]), "score": row["score"], "stale": False} for row in rows]

    def remove_source(self, domain_id, source_id):
        with self._lock:
            db = self._db()
            with db:
                db.execute(
                    "DELETE FROM learning_source_spans WHERE domain_id=? AND source_id=?", (domain_id, source_id)
                )
                db.execute(
                    "DELETE FROM learning_source_versions WHERE domain_id=? AND source_id=?", (domain_id, source_id)
                )

    def path_revision(self, path_id):
        path = self.load_path(path_id)
        return revision_for(path) if path else ""

    def curriculum_history(self, path_id):
        with self._lock:
            rows = (
                self._db()
                .execute(
                    "SELECT revision,base_revision,reason,created_at FROM learning_path_revisions "
                    "WHERE path_id=? ORDER BY created_at,revision",
                    (path_id,),
                )
                .fetchall()
            )
        return [dict(row) for row in rows]

    def revise_path(self, path, *, base_revision, reason, source_refs=(), now=None):
        # Compare-and-swap is enforced inside upsert_path's BEGIN IMMEDIATE.
        return self.upsert_path(path, now=now, expected_revision=base_revision, reason=reason, source_refs=source_refs)

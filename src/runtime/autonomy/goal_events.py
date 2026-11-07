"""Durable, versioned Eris-owned goals; distinct from V94 chat open-loops.

The ledger stores committed decisions but **does not choose a goal**. Its
commit proofs are an API boundary, not cryptographic identity authentication:
callers must come from the audited Subject/Executive semantic winner.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping


class GoalConflict(RuntimeError):
    pass


class InvalidGoalTransition(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SemanticCommitProof:
    commit_id: str
    source: str       # established winner path: v103, neuro, subject, etc.
    identity_revision: int
    motive_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    episode_id: str

    def __post_init__(self):
        if not all((self.commit_id, self.source, self.episode_id)):
            raise ValueError('semantic commitment requires commit_id/source/episode_id')
        if self.identity_revision < 0 or not self.motive_ids:
            raise ValueError('identity revision and motive provenance required')

    def as_dict(self) -> dict[str, Any]:
        return {
            'commit_id': self.commit_id, 'source': self.source,
            'identity_revision': self.identity_revision,
            'motive_ids': list(self.motive_ids),
            'evidence_refs': list(self.evidence_refs), 'episode_id': self.episode_id,
        }


@dataclass(frozen=True, slots=True)
class GoalCandidate:
    goal_id: str
    title: str
    success_criteria: str
    motive_ids: tuple[str, ...]
    identity_revision: int
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self):
        if not all((self.goal_id.strip(), self.title.strip(), self.success_criteria.strip())):
            raise ValueError('goal id/title/success_criteria required')
        if self.identity_revision < 0 or not self.motive_ids:
            raise ValueError('candidate requires identity revision and motive source')


@dataclass(frozen=True, slots=True)
class GoalSnapshot:
    goal_id: str
    revision: int
    status: str
    epoch: int
    title: str
    success_criteria: str
    adoption_commit_id: str
    payload: Mapping[str, Any]


_ALLOWED: dict[str, frozenset[str]] = {
    'proposed': frozenset({'active', 'abandoned'}),
    'active': frozenset({'suspended', 'achieved', 'abandoned', 'failed'}),
    'suspended': frozenset({'active', 'abandoned', 'failed'}),
    'failed': frozenset({'active', 'abandoned'}),
    'achieved': frozenset(),
    'abandoned': frozenset(),
}


def _canonical(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class GoalEventLedger:
    """SQLite-only single-host ledger; no access to SpeechAuthority/Stage."""
    def __init__(self, path: str | Path, *, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with self._db() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS autonomy_goals (
              goal_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, epoch INTEGER NOT NULL,
              status TEXT NOT NULL, title TEXT NOT NULL, criteria TEXT NOT NULL,
              adoption_commit_id TEXT NOT NULL, data_json TEXT NOT NULL,
              updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS autonomy_goal_events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
              goal_id TEXT NOT NULL, goal_revision INTEGER NOT NULL,
              event_type TEXT NOT NULL, payload_hash TEXT NOT NULL,
              payload_json TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS autonomy_event_goal ON autonomy_goal_events(goal_id,seq);
            CREATE TABLE IF NOT EXISTS autonomy_actions (
              action_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL,
              goal_revision INTEGER NOT NULL, goal_epoch INTEGER NOT NULL,
              status TEXT NOT NULL, command_hash TEXT NOT NULL,
              command_key TEXT NOT NULL UNIQUE,
              outcome_json TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL);
            ''')

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=15.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA busy_timeout=15000')
            yield conn
        finally:
            conn.close()

    def _transaction(self, fn):
        with self._db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            try:
                result = fn(conn)
                conn.execute('COMMIT')
                return result
            except BaseException:
                conn.execute('ROLLBACK')
                raise

    @staticmethod
    def _snap(row) -> GoalSnapshot | None:
        if row is None:
            return None
        return GoalSnapshot(
            goal_id=row['goal_id'], revision=row['revision'], epoch=row['epoch'],
            status=row['status'], title=row['title'], success_criteria=row['criteria'],
            adoption_commit_id=row['adoption_commit_id'], payload=json.loads(row['data_json']),
        )

    def get(self, goal_id: str) -> GoalSnapshot | None:
        with self._db() as conn:
            return self._snap(conn.execute('SELECT * FROM autonomy_goals WHERE goal_id=?', (goal_id,)).fetchone())

    def list_goals(self, *, status: str | None = None) -> tuple[GoalSnapshot, ...]:
        with self._db() as conn:
            rows = conn.execute(
                'SELECT * FROM autonomy_goals WHERE (? IS NULL OR status=?) ORDER BY updated_at DESC,goal_id',
                (status, status),
            ).fetchall()
            return tuple(self._snap(r) for r in rows)

    @staticmethod
    def _event(conn, event_id: str, goal_id: str, rev: int, kind: str, payload: dict, now: float) -> bool:
        canonical = _canonical(payload)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        prior = conn.execute('SELECT * FROM autonomy_goal_events WHERE event_id=?', (event_id,)).fetchone()
        if prior:
            if prior['goal_id'] != goal_id or prior['event_type'] != kind or prior['payload_hash'] != digest:
                raise GoalConflict('event id replay with different payload')
            return False
        conn.execute('INSERT INTO autonomy_goal_events(event_id,goal_id,goal_revision,event_type,payload_hash,payload_json,created_at) VALUES(?,?,?,?,?,?,?)',
                     (event_id, goal_id, rev, kind, digest, canonical, now))
        return True

    def propose(self, candidate: GoalCandidate, *, event_id: str) -> GoalSnapshot:
        data = {'candidate': {
            'goal_id': candidate.goal_id, 'title': candidate.title, 'success_criteria': candidate.success_criteria,
            'identity_revision': candidate.identity_revision, 'motive_ids': list(candidate.motive_ids),
            'evidence_refs': list(candidate.evidence_refs)}, 'history': []}
        now = self.clock()
        def commit(conn):
            existing = self._snap(conn.execute('SELECT * FROM autonomy_goals WHERE goal_id=?', (candidate.goal_id,)).fetchone())
            if existing:
                same = existing.payload.get('candidate') == data['candidate']
                if not same:
                    raise GoalConflict('goal id reused with different candidate')
                # Replaying a proposal is safe, but only if the event id is the original one.
                existing_event = conn.execute('SELECT event_id FROM autonomy_goal_events WHERE goal_id=? AND event_type=?', (candidate.goal_id, 'proposed')).fetchone()
                if not existing_event or existing_event['event_id'] != event_id:
                    raise GoalConflict('different event id for existing goal proposal')
                return existing
            self._event(conn, event_id, candidate.goal_id, 1, 'proposed', data['candidate'], now)
            conn.execute('INSERT INTO autonomy_goals VALUES(?,?,?,?,?,?,?,?,?)',
                         (candidate.goal_id, 1, 0, 'proposed', candidate.title, candidate.success_criteria, '', _canonical(data), now))
            return self._snap(conn.execute('SELECT * FROM autonomy_goals WHERE goal_id=?', (candidate.goal_id,)).fetchone())
        return self._transaction(commit)

    def transition(self, goal_id: str, *, expected_revision: int, status: str,
                   event_id: str, proof: SemanticCommitProof, evidence_ref: str = '') -> GoalSnapshot:
        if status not in _ALLOWED:
            raise InvalidGoalTransition('unknown goal status')
        now = self.clock()
        def commit(conn):
            row = conn.execute('SELECT * FROM autonomy_goals WHERE goal_id=?', (goal_id,)).fetchone()
            current = self._snap(row)
            if current is None:
                raise GoalConflict('goal does not exist')
            payload = {'to': status, 'proof': proof.as_dict(), 'evidence_ref': evidence_ref}
            existing = conn.execute('SELECT goal_id,payload_json FROM autonomy_goal_events WHERE event_id=?', (event_id,)).fetchone()
            if existing:
                if existing['goal_id'] != goal_id or existing['payload_json'] != _canonical(payload):
                    raise GoalConflict('conflicting event replay')
                return current
            if current.revision != expected_revision:
                raise GoalConflict(f'expected goal revision {expected_revision}, actual {current.revision}')
            if status not in _ALLOWED[current.status]:
                raise InvalidGoalTransition(f'{current.status} -> {status} not allowed')
            candidate = current.payload['candidate']
            if status == 'active' and current.status == 'proposed':
                if proof.identity_revision != candidate['identity_revision'] or not set(proof.motive_ids).intersection(candidate['motive_ids']):
                    raise GoalConflict('semantic adoption proof does not match candidate provenance')
            if status == 'achieved' and not evidence_ref:
                raise InvalidGoalTransition('achieved requires outcome evidence')
            new_revision = current.revision + 1
            self._event(conn, event_id, goal_id, new_revision, 'transition', payload, now)
            data = dict(current.payload)
            data['last_transition'] = payload
            if current.status == 'proposed' and status == 'active':
                data['adoption_proof'] = proof.as_dict()
            adoption_id = proof.commit_id if current.status == 'proposed' and status == 'active' else current.adoption_commit_id
            conn.execute('UPDATE autonomy_goals SET revision=?, epoch=epoch+1, status=?,adoption_commit_id=?, data_json=?,updated_at=? WHERE goal_id=?',
                         (new_revision, status, adoption_id, _canonical(data), now, goal_id))
            return self._snap(conn.execute('SELECT * FROM autonomy_goals WHERE goal_id=?', (goal_id,)).fetchone())
        return self._transaction(commit)

    def events(self, goal_id: str) -> list[dict]:
        with self._db() as conn:
            return [dict(row) for row in conn.execute('SELECT * FROM autonomy_goal_events WHERE goal_id=? ORDER BY seq', (goal_id,))]

    def panel_projection(self) -> list[dict]:
        """Pure JSON projection, not UI control / permission to open panels."""
        return [{'goal_id': g.goal_id, 'title': g.title, 'status': g.status,
                 'revision': g.revision, 'success_criteria': g.success_criteria,
                 'last_outcome_status': g.payload.get('last_outcome', {}).get('outcome_status', ''),
                 'last_reflection': g.payload.get('last_reflection', {}).get('assessment', '')}
                for g in self.list_goals()]

    def attach_outcome(self, goal_id: str, *, expected_revision: int, event_id: str,
                       outcome_ref: str, outcome_status: str, action_id: str) -> GoalSnapshot:
        """Ghi bằng chứng outcome vào goal theo CAS, chưa tuyên bố mục tiêu đã hoàn thành.

        Receipt của SandboxWorld vẫn là nguồn kiểm toán hành động. Projection này
        chỉ giữ reference và loại kết quả, không chép output/screenshot vào SQLite.
        """
        ref = str(outcome_ref or "").strip()[:180]
        state = str(outcome_status or "").strip()[:60]
        action = str(action_id or "").strip()[:180]
        if not (ref and state and action):
            raise ValueError("Thiếu outcome_ref, outcome_status hoặc action_id")
        now = self.clock()
        payload = {"outcome_ref": ref, "outcome_status": state, "action_id": action}
        def write(conn):
            row = conn.execute('SELECT * FROM autonomy_goals WHERE goal_id=?', (goal_id,)).fetchone()
            current = self._snap(row)
            if current is None:
                raise GoalConflict('goal not found')
            prior = conn.execute('SELECT payload_json FROM autonomy_goal_events WHERE event_id=?', (event_id,)).fetchone()
            if prior is not None:
                if prior['payload_json'] != _canonical(payload):
                    raise GoalConflict('outcome event replay with different payload')
                return current
            if current.status != 'active' or current.revision != expected_revision:
                raise GoalConflict('outcome belongs to stale/nonactive goal')
            revision = current.revision + 1
            self._event(conn, event_id, goal_id, revision, 'outcome_observed', payload, now)
            data = dict(current.payload)
            data['last_outcome'] = payload
            conn.execute('''UPDATE autonomy_goals
                SET revision=?, epoch=epoch+1, data_json=?, updated_at=? WHERE goal_id=?''',
                (revision, _canonical(data), now, goal_id))
            return self._snap(conn.execute('SELECT * FROM autonomy_goals WHERE goal_id=?', (goal_id,)).fetchone())
        return self._transaction(write)


    # V3: kết luận phải có bằng chứng, không tự tuyên bố đạt mục tiêu dài hạn.
    def record_reflection(self, goal_id: str, *, expected_revision: int,
                       event_id: str, assessment: str, evidence_ref: str) -> GoalSnapshot:
        if assessment not in {'workspace_verified', 'needs_review'} or not evidence_ref:
            raise ValueError('Thiếu bằng chứng phản tỉnh hợp lệ')
        now = self.clock()
        data_event = {'assessment': assessment, 'evidence_ref': evidence_ref}
        def commit(conn):
            row = conn.execute('SELECT * FROM autonomy_goals WHERE goal_id=?', (goal_id,)).fetchone()
            goal = self._snap(row)
            if goal is None or goal.status != 'active':
                raise GoalConflict('Mục tiêu không còn hoạt động')
            prior = conn.execute('SELECT payload_json FROM autonomy_goal_events WHERE event_id=?', (event_id,)).fetchone()
            if prior:
                if prior['payload_json'] != _canonical(data_event):
                    raise GoalConflict('Event phản tỉnh trùng id khác nội dung')
                return goal
            if goal.revision != expected_revision:
                raise GoalConflict('Kết luận dựa trên revision đã hết hiệu lực')
            rev = goal.revision + 1
            self._event(conn, event_id, goal_id, rev, 'reflection', data_event, now)
            data = dict(goal.payload)
            data['last_reflection'] = data_event
            conn.execute('UPDATE autonomy_goals SET revision=?,epoch=epoch+1,data_json=?,updated_at=? WHERE goal_id=?',
                         (rev, _canonical(data), now, goal_id))
            return self._snap(conn.execute('SELECT * FROM autonomy_goals WHERE goal_id=?', (goal_id,)).fetchone())
        return self._transaction(commit)


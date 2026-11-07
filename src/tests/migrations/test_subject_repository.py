from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from memory.subject.repository import EventIntegrityError, StaleRevisionError, SubjectRepository


class Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


def make_repo(tmp_path: Path) -> SubjectRepository:
    counter = iter(f"id-{index}" for index in range(100))
    return SubjectRepository(tmp_path / "subject.db", clock=Clock(), id_factory=lambda: next(counter))


def test_foreign_keys_are_enabled(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert repo.foreign_keys_enabled()


def test_append_only_event_cannot_be_mutated(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    event = repo.append_subject_event(subject_id="eris", event_type="raw", payload={"x": 1})
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        repo.connection.execute("UPDATE subject_events SET payload_json = '{}' WHERE event_id = ?", (event.event_id,))


def test_hash_chain_detects_payload_tampering(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    repo.append_subject_event(subject_id="eris", event_type="raw", payload={"x": 1})
    repo.append_subject_event(subject_id="eris", event_type="raw", payload={"x": 2})
    assert repo.verify_event_chain("eris")
    repo.connection.execute("DROP TRIGGER subject_events_no_update")
    repo.connection.execute("UPDATE subject_events SET payload_json = '{\"x\":99}' WHERE sequence = 1")
    assert not repo.verify_event_chain("eris")


def test_duplicate_event_id_is_idempotent(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    first = repo.append_subject_event(subject_id="eris", event_type="raw", payload={"x": 1}, event_id="same")
    second = repo.append_subject_event(subject_id="eris", event_type="raw", payload={"x": 1}, event_id="same")
    assert first == second
    with pytest.raises(EventIntegrityError):
        repo.append_subject_event(subject_id="eris", event_type="raw", payload={"x": 2}, event_id="same")


def test_cas_rejects_stale_revision(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert repo.save_projection(subject_id="eris", projection_type="state", projection_key="current", payload={"x": 1}, expected_revision=0) == 1
    with pytest.raises(StaleRevisionError):
        repo.save_projection(subject_id="eris", projection_type="state", projection_key="current", payload={"x": 2}, expected_revision=0)


def test_duplicate_idempotency_key_is_noop(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    first, created = repo.record_idempotent(scope="sandbox", idempotency_key="k", result={"status": "ok"})
    second, created_again = repo.record_idempotent(scope="sandbox", idempotency_key="k", result={"status": "different"})
    assert created is True
    assert created_again is False
    assert second == first == {"status": "ok"}


def test_transaction_rolls_back_on_crash(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    with pytest.raises(RuntimeError):
        with repo.unit_of_work():
            repo.connection.execute(
                "INSERT INTO projection_revisions(subject_id, projection_type, projection_key, revision, payload_json, payload_hash, updated_at) VALUES ('eris','x','y',1,'{}','h',0)"
            )
            raise RuntimeError("crash")
    assert repo.get_projection(subject_id="eris", projection_type="x", projection_key="y") is None


def test_two_subjects_never_share_projection_state(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    repo.save_projection(subject_id="eris-a", projection_type="state", projection_key="current", payload={"owner": "a"})
    repo.save_projection(subject_id="eris-b", projection_type="state", projection_key="current", payload={"owner": "b"})
    assert repo.get_projection(subject_id="eris-a", projection_type="state", projection_key="current")[1]["owner"] == "a"
    assert repo.get_projection(subject_id="eris-b", projection_type="state", projection_key="current")[1]["owner"] == "b"

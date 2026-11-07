from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from memory.subject.repository import EventIntegrityError, SubjectRepository


def test_migration_status_integrity_checkpoint_and_backup(tmp_path: Path) -> None:
    database = tmp_path / "subject.db"
    repo = SubjectRepository(database, id_factory=lambda: "checkpoint")
    repo.append_subject_event(subject_id="eris", event_type="raw", payload={"x": 1})
    repo.save_projection(subject_id="eris", projection_type="state", projection_key="current", payload={"x": 1})

    status = repo.migration_status()
    assert status
    assert all(item["present"] and item["checksum_valid"] for item in status)

    report = repo.integrity_report(persist=True)
    assert report["ok"] is True
    assert report["event_chains"] == {"eris": True}
    count = repo.connection.execute("SELECT COUNT(*) FROM subject_integrity_checkpoints").fetchone()[0]
    assert count == 1

    backup = repo.backup_to(tmp_path / "backups" / "subject-copy.db")
    copy_repo = SubjectRepository(backup)
    assert copy_repo.verify_event_chain("eris")
    assert copy_repo.get_projection(subject_id="eris", projection_type="state", projection_key="current") == (1, {"x": 1})
    copy_repo.close()
    repo.close()


def test_changed_applied_migration_is_rejected(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[2] / "memory" / "subject" / "migrations"
    migrations = tmp_path / "migrations"
    shutil.copytree(source, migrations)
    database = tmp_path / "subject.db"
    SubjectRepository(database, migration_dir=migrations).close()
    first = sorted(migrations.glob("*.sql"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "\n-- tampered\n", encoding="utf-8")
    with pytest.raises(EventIntegrityError, match="migration checksum changed"):
        SubjectRepository(database, migration_dir=migrations)


def test_idempotency_request_hash_and_result_hash_are_verified(tmp_path: Path) -> None:
    repo = SubjectRepository(tmp_path / "subject.db")
    repo.record_idempotent(scope="sandbox", idempotency_key="k", request_hash="request-a", result={"ok": True})
    assert repo.get_idempotent(scope="sandbox", idempotency_key="k", request_hash="request-a") == {"ok": True}
    with pytest.raises(EventIntegrityError, match="different request"):
        repo.get_idempotent(scope="sandbox", idempotency_key="k", request_hash="request-b")

    repo.connection.execute("DROP TRIGGER idempotency_keys_no_update")
    repo.connection.execute("UPDATE idempotency_keys SET result_json = '{\"ok\":false}' WHERE idempotency_key = 'k'")
    with pytest.raises(EventIntegrityError, match="result hash mismatch"):
        repo.get_idempotent(scope="sandbox", idempotency_key="k")
    repo.close()


def test_conflicting_world_outcome_is_not_silently_ignored(tmp_path: Path) -> None:
    repo = SubjectRepository(tmp_path / "subject.db")
    repo.append_world_outcome(outcome_id="o", action_id="a", decision_id="d", status="succeeded", payload={"x": 1})
    with pytest.raises(EventIntegrityError, match="conflicts"):
        repo.append_world_outcome(outcome_id="o", action_id="a", decision_id="d", status="succeeded", payload={"x": 2})
    repo.close()

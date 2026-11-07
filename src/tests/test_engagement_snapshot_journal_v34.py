from __future__ import annotations

import copy

from runtime.engagements import EngagementRuntime
from runtime.engagements.snapshot_journal import EngagementSnapshotJournal
from runtime.readiness import ReadinessStatus, RuntimeReadinessEvaluator
from runtime.subsystem_health import SubsystemHealthRegistry


def _snapshot_with_message(text: str):
    runtime = EngagementRuntime(clock=lambda: 10.0)
    runtime.observe_user_message(f"Eris, hãy hỗ trợ code với tôi: {text}", now=10.0)
    return runtime.registry.snapshot()


def test_journal_rotates_primary_to_last_known_good_backup():
    container = {}
    journal = EngagementSnapshotJournal(lambda: container, clock=lambda: 20.0)
    first = _snapshot_with_message("first")
    second = _snapshot_with_message("second")

    assert journal.stage(first) == 1
    assert journal.stage(second) == 2

    assert container[journal.PRIMARY_KEY]["snapshot_digest"] == second["snapshot_digest"]
    assert container[journal.BACKUP_KEY]["snapshot_digest"] == first["snapshot_digest"]
    assert container[journal.META_KEY]["generation"] == 2
    assert journal.load().source == "primary"


def test_journal_recovers_corrupt_primary_from_backup_and_repairs_container():
    container = {}
    health = SubsystemHealthRegistry()
    journal = EngagementSnapshotJournal(lambda: container, clock=lambda: 30.0, health_registry=health)
    first = _snapshot_with_message("safe")
    second = _snapshot_with_message("new")
    journal.stage(first)
    journal.stage(second)
    container[journal.PRIMARY_KEY]["revision"] += 100

    result = journal.load(auto_repair=True)

    assert result.source == "backup"
    assert result.repaired_primary is True
    assert result.snapshot["snapshot_digest"] == first["snapshot_digest"]
    assert container[journal.PRIMARY_KEY]["snapshot_digest"] == first["snapshot_digest"]
    assert journal.diagnostics()["primary_valid"] is True
    state = health.get("engagement.snapshot_journal")
    assert state is not None and state.status.value == "degraded"


def test_journal_fails_closed_when_primary_and_backup_are_corrupt():
    container = {}
    journal = EngagementSnapshotJournal(lambda: container)
    first = _snapshot_with_message("one")
    second = _snapshot_with_message("two")
    journal.stage(first)
    journal.stage(second)
    container[journal.PRIMARY_KEY]["revision"] += 1
    container[journal.BACKUP_KEY]["revision"] += 1

    result = journal.load()

    assert result.valid is False
    assert result.source == "empty"
    assert result.snapshot == {}
    assert "primary:snapshot_digest_mismatch" in result.issues
    assert "backup:snapshot_digest_mismatch" in result.issues


def test_journal_metadata_detects_snapshot_substitution():
    container = {}
    journal = EngagementSnapshotJournal(lambda: container)
    first = _snapshot_with_message("one")
    second = _snapshot_with_message("two")
    journal.stage(first)
    # Replace primary with another internally valid snapshot without updating journal metadata.
    container[journal.PRIMARY_KEY] = copy.deepcopy(second)

    result = journal.load(auto_repair=False)

    assert result.valid is False
    assert result.source == "empty"
    assert "primary:journal_digest_mismatch" in result.issues


def test_readiness_allows_empty_journal_but_degrades_invalid_persisted_journal():
    evaluator = RuntimeReadinessEvaluator(critical_subsystems=())
    base = {
        "bootstrap": {"ai_runtime_enabled": True, "completed_phases": [1, 2, 3, 4, 5, 6]},
        "health": {"overall": "healthy", "subsystems": []},
        "boundaries": {},
        "engagement": {"kernel": {}, "snapshot_journal": {"has_snapshots": False}},
    }
    assert evaluator.evaluate(base).status == ReadinessStatus.READY

    base["engagement"]["snapshot_journal"] = {
        "has_snapshots": True,
        "primary_valid": False,
        "backup_valid": False,
    }
    result = evaluator.evaluate(base)
    assert result.status == ReadinessStatus.DEGRADED
    assert "engagement_snapshot_journal" in result.degraded_components


def test_journal_rejects_primary_older_than_backup_and_promotes_newer_generation():
    container = {}
    journal = EngagementSnapshotJournal(lambda: container, clock=lambda: 40.0)
    older = _snapshot_with_message("older")
    newer_runtime = EngagementRuntime(clock=lambda: 10.0)
    newer_runtime.observe_user_message("Eris, hãy hỗ trợ code với tôi", now=10.0)
    current = newer_runtime.registry.foreground()
    assert current is not None
    newer_runtime.emit_domain_event("code.file_modified", engagement_id=current.engagement_id, now=11.0)
    newer = newer_runtime.registry.snapshot()
    assert newer["revision"] > older["revision"]

    journal.stage(newer)
    # Simulate a stale primary rollback while preserving a newer valid backup.
    container[journal.BACKUP_KEY] = copy.deepcopy(newer)
    container[journal.PRIMARY_KEY] = copy.deepcopy(older)
    meta = container[journal.META_KEY]
    meta["primary_digest"] = container[journal.PRIMARY_KEY]["snapshot_digest"]
    meta["primary_revision"] = older["revision"]
    meta["primary_event_log_seq"] = older["event_log_seq"]
    meta["backup_digest"] = container[journal.BACKUP_KEY]["snapshot_digest"]

    result = journal.load(auto_repair=True)

    assert result.source == "backup"
    assert result.repaired_primary is True
    assert result.snapshot["revision"] == newer["revision"]
    assert "primary:primary_older_than_backup" in result.issues
    assert container[journal.META_KEY]["recovery_count"] == 1

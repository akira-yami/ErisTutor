from __future__ import annotations

from pathlib import Path

import pytest

from cortex.subject.measurement_adapters import (
    OperationalMeasurementAdapter,
    RepositoryMeasurementAdapter,
    RuntimeMeasurementSnapshot,
)
from cortex.subject.service import SubjectService, SubjectServiceConfig
from memory.subject import SubjectRepository


def test_operational_measurement_requires_provenance() -> None:
    adapter = OperationalMeasurementAdapter()
    result = adapter.build(
        RuntimeMeasurementSnapshot(
            subject_id="eris",
            captured_at=100.0,
            pending_tasks=9,
            queue_capacity=10,
            # Không có evidence_refs nên score này phải bị bỏ qua.
            evidence_refs={},
        )
    )
    assert result.observation.measurements == {}
    assert result.observation.evidence_refs == ()


def test_measurement_produces_functional_consequences() -> None:
    adapter = OperationalMeasurementAdapter()
    result = adapter.build(
        RuntimeMeasurementSnapshot(
            subject_id="eris",
            captured_at=100.0,
            unresolved_event_refs=8,
            total_event_refs=10,
            pending_tasks=9,
            queue_capacity=10,
            evidence_refs={
                "memory_integrity": ("db:event-chain",),
                "processing_capacity": ("runtime:queue",),
            },
        )
    )
    assert result.observation.measurements["memory_integrity"] == pytest.approx(0.2)
    assert result.observation.measurements["processing_capacity"] == pytest.approx(0.1)
    assert "memory_integrity" in result.functional_consequences
    assert result.observation.evidence_refs


def test_repository_adapter_updates_subject_substrate(tmp_path: Path) -> None:
    repo = SubjectRepository(tmp_path / "subject.db")
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris"),
        repository=repo,
    )
    adapter = RepositoryMeasurementAdapter()
    measured = service.refresh_substrate_from_runtime(
        adapter,
        runtime_state={
            "pending_tasks": 8,
            "queue_capacity": 10,
            "boundary_checks": 10,
            "boundary_violations": 2,
            "future_option_count": 1,
            "target_future_options": 4,
            "project_viabilities": (0.2, 0.4),
        },
    )
    assert measured.processing_capacity == pytest.approx(0.2)
    assert measured.self_world_boundary == pytest.approx(0.8)
    assert measured.future_option_availability == pytest.approx(0.25)
    assert measured.project_viability == pytest.approx(0.3)
    assert measured.measurement_refs
    assert any(event.event_type == "substrate_measurement" for event in repo.events(subject_id="eris"))
    repo.close()


def test_substrate_measurement_survives_restart(tmp_path: Path) -> None:
    repo = SubjectRepository(tmp_path / "subject.db")
    first = SubjectService(
        config=SubjectServiceConfig(subject_id="eris"),
        repository=repo,
    )
    first.refresh_substrate_from_runtime(
        RepositoryMeasurementAdapter(),
        runtime_state={
            "pending_tasks": 9,
            "queue_capacity": 10,
            "future_option_count": 1,
            "target_future_options": 4,
        },
    )
    before = first.substrate
    second = SubjectService(
        config=SubjectServiceConfig(subject_id="eris"),
        repository=repo,
    )
    assert second.substrate.revision == before.revision
    assert second.substrate.processing_capacity == pytest.approx(before.processing_capacity)
    assert second.substrate.future_option_availability == pytest.approx(
        before.future_option_availability
    )
    repo.close()

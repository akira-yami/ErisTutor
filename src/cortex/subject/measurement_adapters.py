# -*- coding: utf-8 -*-
"""Bộ chuyển đổi measurement vận hành thành quan sát substrate có bằng chứng.

Module này cố tình không đọc câu kiểu "tôi thấy mệt" để hạ processing_capacity.
Mỗi điểm substrate phải được tính từ dữ kiện vận hành có thể truy ngược: event
chain, belief evidence, calibration, hàng đợi công việc, kết quả hành động, dự án,
vi phạm ranh giới và số lựa chọn tương lai còn mở.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Mapping, Protocol, Sequence

from .contracts import WorldObservation


class RepositoryMeasurementPort(Protocol):
    """Phần rất nhỏ của repository cần cho measurement adapter."""

    database_path: str

    def verify_event_chain(self, subject_id: str) -> bool: ...

    def events(self, *, subject_id: str, after_sequence: int = 0) -> Sequence[Any]: ...


@dataclass(frozen=True, slots=True)
class RuntimeMeasurementSnapshot:
    """Ảnh chụp dữ kiện vận hành, không phải mood vector.

    Các trường đếm cho phép adapter tính score theo công thức minh bạch. Mỗi nhóm
    dữ kiện phải có evidence_refs tương ứng; nhóm không có bằng chứng sẽ không
    được đưa vào observation.
    """

    subject_id: str
    captured_at: float
    unresolved_event_refs: int = 0
    total_event_refs: int = 0
    belief_count: int = 0
    beliefs_with_evidence: int = 0
    calibration_errors: tuple[float, ...] = ()
    pending_tasks: int = 0
    queue_capacity: int = 1
    successful_actions: int = 0
    total_actions: int = 0
    project_viabilities: tuple[float, ...] = ()
    boundary_violations: int = 0
    boundary_checks: int = 0
    future_option_count: int = 0
    target_future_options: int = 4
    evidence_refs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    observation: WorldObservation
    functional_consequences: Mapping[str, tuple[str, ...]]


class OperationalMeasurementAdapter:
    """Tính vector viability từ snapshot có provenance.

    Điểm nào thiếu evidence sẽ bị bỏ qua. Nhờ vậy một lỗi wiring không thể âm
    thầm biến giá trị mặc định thành "sự thật" về nội tâm Eris.
    """

    _CONSEQUENCES: Mapping[str, tuple[str, ...]] = {
        "memory_integrity": (
            "giảm độ tin cậy khi truy hồi continuity",
            "tăng ưu tiên kiểm tra orphan event refs",
        ),
        "belief_traceability": (
            "giảm quyền dùng belief cho khẳng định công khai",
            "tăng ưu tiên hỏi hoặc kiểm chứng",
        ),
        "model_calibration": (
            "giảm độ tự tin khi dự đoán năng lực",
            "tăng giá trị nhận thức của hành động thăm dò",
        ),
        "processing_capacity": (
            "giảm ngân sách proposal và thought candidate",
            "tăng chi phí hành động dài",
        ),
        "action_efficacy": (
            "giảm kỳ vọng thành công của action family liên quan",
            "tăng ưu tiên luyện tập hoặc phương án thay thế",
        ),
        "project_viability": (
            "giảm contribution của proposal gắn với dự án suy yếu",
        ),
        "self_world_boundary": (
            "tăng mức thận trọng khi quy kết hành động là của Eris",
            "tăng yêu cầu authorship evidence",
        ),
        "future_option_availability": (
            "tăng chi phí của lựa chọn làm mất thêm khả năng tương lai",
        ),
    }

    @staticmethod
    def _ratio(numerator: int, denominator: int, *, empty: float) -> float:
        if denominator <= 0:
            return empty
        return max(0.0, min(1.0, float(numerator) / float(denominator)))

    @staticmethod
    def _refs(snapshot: RuntimeMeasurementSnapshot, key: str) -> tuple[str, ...]:
        return tuple(str(item) for item in snapshot.evidence_refs.get(key, ()) if str(item))

    def build(self, snapshot: RuntimeMeasurementSnapshot) -> MeasurementResult:
        measurements: dict[str, float] = {}
        refs: list[str] = []

        def add(key: str, value: float) -> None:
            evidence = self._refs(snapshot, key)
            if not evidence:
                return
            measurements[key] = max(0.0, min(1.0, float(value)))
            refs.extend(evidence)

        add(
            "memory_integrity",
            1.0 - self._ratio(
                snapshot.unresolved_event_refs,
                max(snapshot.total_event_refs, 1),
                empty=0.0,
            ),
        )
        add(
            "belief_traceability",
            self._ratio(snapshot.beliefs_with_evidence, snapshot.belief_count, empty=1.0),
        )
        if snapshot.calibration_errors:
            add(
                "model_calibration",
                1.0 - mean(max(0.0, min(1.0, float(item))) for item in snapshot.calibration_errors),
            )
        add(
            "processing_capacity",
            1.0 - self._ratio(snapshot.pending_tasks, max(snapshot.queue_capacity, 1), empty=0.0),
        )
        add(
            "action_efficacy",
            self._ratio(snapshot.successful_actions, snapshot.total_actions, empty=0.5),
        )
        if snapshot.project_viabilities:
            add(
                "project_viability",
                mean(max(0.0, min(1.0, float(item))) for item in snapshot.project_viabilities),
            )
        add(
            "self_world_boundary",
            1.0
            - self._ratio(snapshot.boundary_violations, max(snapshot.boundary_checks, 1), empty=0.0),
        )
        add(
            "future_option_availability",
            self._ratio(
                snapshot.future_option_count,
                max(snapshot.target_future_options, 1),
                empty=0.0,
            ),
        )

        consequences = {
            key: self._CONSEQUENCES[key]
            for key, value in measurements.items()
            if value < 0.70
        }
        observation = WorldObservation(
            observation_id=f"substrate-measurement:{snapshot.subject_id}:{snapshot.captured_at:.6f}",
            subject_id=snapshot.subject_id,
            kind="substrate_measurement",
            created_at=snapshot.captured_at,
            facts=("operational_measurement_snapshot",),
            evidence_refs=tuple(dict.fromkeys(refs)),
            measurements=measurements,
            context={
                "functional_consequences": consequences,
                "measurement_keys": tuple(sorted(measurements)),
            },
        )
        return MeasurementResult(observation, consequences)


class RepositoryMeasurementAdapter:
    """Thu thập measurement từ repository và SubjectService đang chạy.

    Adapter chỉ dùng các số liệu đã tồn tại. Nó không tự suy từ văn phong hoặc
    câu tự thuật của Eris, vì lời kể không phải máy đo.
    """

    def __init__(self, operational: OperationalMeasurementAdapter | None = None) -> None:
        self._operational = operational or OperationalMeasurementAdapter()

    def collect(
        self,
        *,
        repository: RepositoryMeasurementPort,
        service: Any,
        captured_at: float,
        runtime_state: Mapping[str, Any] | None = None,
    ) -> MeasurementResult:
        runtime = dict(runtime_state or {})
        subject_id = str(service.config.subject_id)
        events = tuple(repository.events(subject_id=subject_id))
        event_ids = {str(item.event_id) for item in events}
        unresolved = sum(
            1
            for event in events
            for ref in tuple(getattr(event, "causal_refs", ()))
            if ref and ref not in event_ids
        )
        total_refs = sum(len(tuple(getattr(event, "causal_refs", ()))) for event in events)

        beliefs = tuple(service.beliefs.all())
        estimates = tuple(service.competence.all())
        outcomes = tuple(getattr(service, "_outcomes", ()))
        successful = sum(float(item.success_score) >= 0.5 for item in outcomes)

        project_viabilities = tuple(
            float(item)
            for item in runtime.get("project_viabilities", ())
        )
        future_options = int(runtime.get("future_option_count", 0))
        queue_capacity = max(1, int(runtime.get("queue_capacity", 1)))
        pending_tasks = max(0, int(runtime.get("pending_tasks", 0)))
        boundary_checks = max(0, int(runtime.get("boundary_checks", len(outcomes))))
        boundary_violations = max(0, int(runtime.get("boundary_violations", 0)))

        db_ref = f"sqlite:{repository.database_path}:subject:{subject_id}"
        snapshot = RuntimeMeasurementSnapshot(
            subject_id=subject_id,
            captured_at=float(captured_at),
            unresolved_event_refs=unresolved,
            total_event_refs=total_refs,
            belief_count=len(beliefs),
            beliefs_with_evidence=sum(bool(item.evidence_refs) for item in beliefs),
            calibration_errors=tuple(float(item.calibration_error) for item in estimates),
            pending_tasks=pending_tasks,
            queue_capacity=queue_capacity,
            successful_actions=successful,
            total_actions=len(outcomes),
            project_viabilities=project_viabilities,
            boundary_violations=boundary_violations,
            boundary_checks=boundary_checks,
            future_option_count=future_options,
            target_future_options=max(1, int(runtime.get("target_future_options", 4))),
            evidence_refs={
                "memory_integrity": (f"{db_ref}:event-chain",),
                "belief_traceability": tuple(
                    f"belief:{item.belief_id}" for item in beliefs
                ) or (f"{db_ref}:belief-empty",),
                "model_calibration": tuple(
                    f"competence:{item.estimate_id}" for item in estimates
                ),
                "processing_capacity": (f"runtime:queue:{pending_tasks}/{queue_capacity}",),
                "action_efficacy": tuple(
                    f"outcome:{item.outcome_id}" for item in outcomes
                ) or (f"{db_ref}:outcome-empty",),
                "project_viability": tuple(
                    f"project-measurement:{index}" for index, _ in enumerate(project_viabilities)
                ),
                "self_world_boundary": (
                    f"runtime:boundary:{boundary_violations}/{boundary_checks}",
                ),
                "future_option_availability": (
                    f"runtime:future-options:{future_options}",
                ),
            },
        )
        return self._operational.build(snapshot)


__all__ = [
    "MeasurementResult",
    "OperationalMeasurementAdapter",
    "RepositoryMeasurementAdapter",
    "RuntimeMeasurementSnapshot",
]

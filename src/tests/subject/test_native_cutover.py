from __future__ import annotations

from pathlib import Path

from runtime.subject_bootstrap import build_subject_core_components
from runtime.subject_ingress import SubjectIngress


class _BindableExecutor:
    def __init__(self) -> None:
        self.bound = False
        self.subject_service = None

    def bind_subject_service(self, subject_service) -> None:
        self.subject_service = subject_service

    def bind_sandbox_world(self, sandbox, provider) -> None:
        self.bound = sandbox is not None and callable(provider)


def test_subject_core_is_authoritative_without_feature_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ERIS_SUBJECT_CORE_ENABLED", raising=False)
    executor = _BindableExecutor()
    components = build_subject_core_components(
        data_root=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        action_executor=executor,
    )
    try:
        assert executor.bound
        assert executor.subject_service is components.subject_service
        assert components.runtime is not None
        assert components.actuator is not None
        assert components.subject_service.config.algorithm_version == "subject-service-v12-validated-agency-reflection"
        assert components.repository.migration_status()[-1]["version"] == "0007_evidence_lab.sql"
    finally:
        components.close()


def test_chat_ingress_writes_subject_decision_and_raw_event(tmp_path: Path) -> None:
    components = build_subject_core_components(
        data_root=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    try:
        ingress = SubjectIngress(components.subject_service)
        decision = ingress.observe_user_turn(
            "Hãy kiểm tra giúp tôi việc này?",
            created_at=100.0,
            turn_id="turn-1",
            planning_context={"memory_available": True},
        )
        assert decision.proposed_action in {"speak", "investigate"}
        event_types = [
            event.event_type
            for event in components.repository.events(subject_id=components.subject_service.config.subject_id)
        ]
        assert "raw_observation" in event_types
        assert "mind_decision" in event_types
        assert "authorship_draft" in event_types
    finally:
        components.close()

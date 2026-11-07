from __future__ import annotations

from runtime.adaptive_engineering_learning import AdaptiveEngineeringLearning
from runtime.learning_core import LearningService, LearningStore


def _kernel(tmp_path) -> LearningService:
    return LearningService(LearningStore(tmp_path / "kernel.sqlite3"))


def test_injected_learning_service_becomes_mastery_authority(tmp_path) -> None:
    service = _kernel(tmp_path)
    learning = AdaptiveEngineeringLearning(
        path=tmp_path / "legacy_projection.json",
        learning_service=service,
    )

    learning.observe_user_turn(
        "Tôi nghĩ lỗi do call site truyền sai dependency.",
        signals=("grounded_hypothesis",),
        evidence_ref="turn:hypothesis",
        now=1000.0,
    )

    projection = learning.snapshot()["concepts"]["debugging.trace_with_evidence"]
    durable = service.get_mastery("debugging.trace_with_evidence")
    reviews = service.review_snapshot(("debugging.trace_with_evidence",))

    assert durable.attempt_count == 1
    assert durable.mastery > 0.0
    assert projection["mastery"] == durable.mastery
    assert projection["confidence"] == durable.confidence
    assert projection["next_review_at"] == reviews["debugging.trace_with_evidence"]["due_at"]


def test_candidate_misconception_is_append_only_kernel_evidence(tmp_path) -> None:
    service = _kernel(tmp_path)
    learning = AdaptiveEngineeringLearning(
        path=tmp_path / "legacy_projection.json",
        learning_service=service,
    )

    events = learning.observe_user_turn(
        "Test pass là xong, khỏi review diff.",
        evidence_ref="turn:wrong",
        now=1000.0,
    )

    durable = service.get_mastery("verification.targeted_tests")
    projection = learning.snapshot()["concepts"]["verification.targeted_tests"]
    assert "candidate_misconception:tests_pass_means_done" in events
    assert durable.attempt_count == 1
    assert "tests_pass_means_done" in durable.error_codes
    assert projection["mastery"] == durable.mastery


def test_agent_flow_remains_exposure_only_and_does_not_touch_kernel_mastery(tmp_path) -> None:
    service = _kernel(tmp_path)
    learning = AdaptiveEngineeringLearning(
        path=tmp_path / "legacy_projection.json",
        learning_service=service,
    )

    learning.note_coding_flow(
        {
            "task_id": "task-1",
            "ended_at": 1000.0,
            "findings": [{"code": "patch_before_inspection"}],
        }
    )

    projection = learning.snapshot()["concepts"]["debugging.inspect_before_patch"]
    durable = service.get_mastery("debugging.inspect_before_patch")
    assert projection["exposures"] == 1
    assert projection["mastery"] == 0.30
    assert durable.attempt_count == 0
    assert durable.mastery == 0.0


def test_self_report_stays_non_mastery_even_with_kernel_injected(tmp_path) -> None:
    from runtime.adaptive_engineering_learning import LearningDirective

    service = _kernel(tmp_path)
    learning = AdaptiveEngineeringLearning(
        path=tmp_path / "legacy_projection.json",
        learning_service=service,
    )
    learning.register_prompt(
        LearningDirective("teach_back", "debugging.inspect_before_patch", "x", "due", "ask", True),
        session_serial=1,
        now=1000.0,
    )

    learning.observe_user_turn(
        "Ừ hiểu rồi.",
        feedback=("mentor_understood",),
        evidence_ref="turn:self-report",
        now=1001.0,
    )

    durable = service.get_mastery("debugging.inspect_before_patch")
    assert durable.attempt_count == 0
    assert durable.mastery == 0.0

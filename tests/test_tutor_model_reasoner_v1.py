from __future__ import annotations

from types import SimpleNamespace

from runtime.learning_core import KnowledgePoint, LearnerProfile, MasteryRecord
from runtime.learning_core.policy import TutorStep
from runtime.tutor import ContextTurn, MoveEnvelope, TeachingMove, TutorObservation, TutorState
from runtime.tutor.model_reasoner import RuntimeTutorReasoner


class _FakeRouter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.deep_client = object()
        self.observer_client = None
        self.surface_client = None
        self.llm_service = SimpleNamespace(client=None)

    def invoke_structured_worker(self, client, **kwargs):
        self.calls.append((client, kwargs))
        return self.responses.pop(0)


def _observation() -> TutorObservation:
    return TutorObservation(
        turn_id="turn-2",
        user_text="DB rollback không thể kéo message Kafka đã publish quay lại.",
        path_id="path:tx",
        path_goal="Hiểu transaction boundary",
        kp_id="tx.boundary",
        kp_title="Transaction boundary",
        kp_description="Phân biệt atomicity trong và ngoài DB.",
        kp_type="concept",
        policy_step=TutorStep("assess", "tx.boundary", "assess_after_teach", "path:tx", "feynman_explain"),
        mastery=MasteryRecord.blank("tx.boundary"),
        learner=LearnerProfile(),
        recent_history=(ContextTurn("assistant", "Nếu Kafka đã nhận message thì DB rollback có thu hồi được không?"),),
        now=1000.0,
    )


def test_runtime_reasoner_uses_structured_workers_and_never_exposes_cot() -> None:
    router = _FakeRouter([
        {
            "primary_need": "assess",
            "summary": "User đang thể hiện đúng boundary chính.",
            "hypotheses": [],
            "understood_points": ["DB rollback không rollback external side effect"],
            "uncertain_points": [],
            "recommended_strategy": "feynman_check",
            "confidence": 0.9,
        },
        {
            "kind": "assessment",
            "kp_id": "tx.boundary",
            "objective": "Kiểm tra transfer",
            "instruction_vi": "Hỏi một tình huống biến thể ngắn.",
            "question_goal": "Kiểm tra user áp dụng boundary sang case mới.",
            "expected_response_shape": "Nêu boundary và hệ quả.",
            "interaction_type": "feynman_explain",
            "requires_user_response": True,
            "max_sentences": 2,
            "reason": "Cần evidence transfer.",
        },
    ])
    reasoner = RuntimeTutorReasoner(router)
    observation = _observation()
    state = TutorState(session_id="s", path_id="path:tx", kp_id="tx.boundary")

    diagnosis = reasoner.diagnose(observation, state)
    move = reasoner.choose_move(
        observation,
        state,
        diagnosis,
        MoveEnvelope(("assessment",), True, 2, "feynman_explain", "test"),
    )

    assert diagnosis.primary_need == "assess"
    assert move.kind == "assessment"
    assert len(router.calls) == 2
    assert all("chain-of-thought" in call[1]["system"] or "chain-of-thought" in call[1]["system"].lower() for call in router.calls)


def test_runtime_reasoner_semantic_assessment_is_forced_to_previous_kp() -> None:
    router = _FakeRouter([
        {
            "kp_id": "model.tried.to.redirect",
            "verdict": "pass",
            "confidence": 0.92,
            "evidence_type": "feynman_pass",
            "strength": 0.9,
            "summary": "Đúng mental model.",
            "error_code": "",
            "misconception": "",
        }
    ])
    reasoner = RuntimeTutorReasoner(router)
    observation = _observation()
    state = TutorState(session_id="s", path_id="path:tx", kp_id="tx.boundary")
    previous = TeachingMove(
        move_id="m",
        kind="assessment",
        kp_id="tx.boundary",
        objective="check",
        instruction_vi="ask",
        requires_user_response=True,
    )

    assessment = reasoner.assess_response(observation, state, previous)

    assert assessment is not None
    assert assessment.kp_id == "tx.boundary"
    assert assessment.verdict == "pass"

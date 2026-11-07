from __future__ import annotations

from dataclasses import replace

from runtime.learning_core import KnowledgePoint, LearnerProfile, LearningPath, LearningService, LearningStore
from runtime.tutor import (
    ConservativeTutorReasoner,
    LearnerHypothesis,
    SemanticAssessment,
    TeachingMove,
    TutorDiagnosis,
    TutorExecutive,
)


def _service(tmp_path) -> LearningService:
    service = LearningService(LearningStore(tmp_path / "learning.sqlite3"))
    path = LearningPath(
        path_id="path:transactions",
        title="Transaction boundaries",
        goal="Hiểu đúng boundary giữa database transaction và external side effect.",
        knowledge_points=(
            KnowledgePoint(
                "tx.boundary",
                "Transaction boundary",
                "concept",
                "Phân biệt phạm vi atomicity của DB với hệ thống bên ngoài.",
            ),
            KnowledgePoint(
                "tx.outbox",
                "Transactional outbox",
                "design",
                "Dùng outbox để nối DB state với message publication.",
                prerequisites=("tx.boundary",),
            ),
        ),
    )
    service.register_path(path, now=1000.0)
    assert service.activate_path(path.path_id)
    return service


class _IllegalMoveReasoner(ConservativeTutorReasoner):
    def choose_move(self, observation, state, diagnosis, envelope):
        # Cố tình đề xuất lecture trong phase probe để test deterministic shell.
        return TeachingMove(
            move_id="illegal",
            kind="explain",
            kp_id=observation.kp_id,
            objective="lecture",
            instruction_vi="Giảng luôn đáp án.",
            requires_user_response=False,
            max_sentences=6,
        )


class _SemanticReasoner(ConservativeTutorReasoner):
    def __init__(self, assessment: SemanticAssessment) -> None:
        self.assessment = assessment

    def assess_response(self, observation, state, previous_move):
        return replace(self.assessment, kp_id=previous_move.kp_id)


class _MisconceptionReasoner(ConservativeTutorReasoner):
    def diagnose(self, observation, state):
        hypothesis = LearnerHypothesis(
            hypothesis_id="hyp:rollback-kafka",
            kind="misconception",
            kp_id=observation.kp_id,
            summary="User đang đồng nhất DB rollback với rollback của Kafka side effect.",
            confidence=0.86,
            evidence_refs=(observation.turn_id,),
            first_seen_at=observation.now,
            last_seen_at=observation.now,
        )
        return TutorDiagnosis(
            primary_need="misconception",
            summary="Cần phá giả định transaction boundary trước khi dạy pattern.",
            hypotheses=(hypothesis,),
            recommended_strategy="counterexample_then_probe",
            confidence=0.86,
        )


def test_probe_phase_cannot_be_overridden_by_semantic_model(tmp_path) -> None:
    executive = TutorExecutive(_service(tmp_path), reasoner=_IllegalMoveReasoner())

    decision = executive.plan_next_move(
        "Giải thích transaction cho tôi",
        turn_id="turn-1",
        now=1000.0,
    )

    assert decision is not None
    assert decision.policy_step.kind == "probe"
    assert decision.move.kind == "probe_question"
    assert decision.move.requires_user_response is True
    # `max_sentences` giờ là HINT MỀM, không còn trần cứng cắt lời (cadence do cá
    # tính Root quyết qua after=may_continue). Phase/kind/requires_response vẫn bị
    # guardrail giữ chặt — đó mới là bất biến test này bảo vệ, không phải số câu.
    assert decision.move.max_sentences >= 2


def test_writer_projection_never_contains_expected_answer(tmp_path) -> None:
    executive = TutorExecutive(_service(tmp_path))
    decision = executive.plan_next_move("Tôi muốn học", turn_id="turn-1", now=1000.0)

    assert decision is not None
    writer_context = decision.to_writer_context()
    serialized = repr(writer_context).lower()
    assert "expected_answer" not in serialized
    assert "hypotheses" not in writer_context


def test_low_confidence_semantic_assessment_does_not_change_mastery(tmp_path) -> None:
    service = _service(tmp_path)
    assessment = SemanticAssessment(
        kp_id="",
        verdict="pass",
        confidence=0.55,
        evidence_type="feynman_pass",
        strength=0.95,
        summary="Có vẻ đúng nhưng model chưa đủ chắc.",
    )
    executive = TutorExecutive(service, reasoner=_SemanticReasoner(assessment))
    first = executive.plan_next_move("Dạy tôi", turn_id="turn-1", now=1000.0)
    assert first is not None and first.move.requires_user_response

    result = executive.observe_response(
        "Transaction chỉ atomic trong boundary của resource tham gia transaction.",
        turn_id="turn-2",
        now=1001.0,
    )

    assert result is not None
    record = service.get_mastery("tx.boundary")
    assert record.attempt_count == 0
    assert record.mastery == 0.0


def test_high_confidence_semantic_assessment_commits_evidence(tmp_path) -> None:
    service = _service(tmp_path)
    assessment = SemanticAssessment(
        kp_id="",
        verdict="pass",
        confidence=0.92,
        evidence_type="feynman_pass",
        strength=0.90,
        summary="User mô tả đúng transaction boundary và hệ quả.",
    )
    executive = TutorExecutive(service, reasoner=_SemanticReasoner(assessment))
    first = executive.plan_next_move("Dạy tôi", turn_id="turn-1", now=1000.0)
    assert first is not None

    executive.observe_response(
        "DB rollback không thể thu hồi message đã publish sang hệ thống độc lập.",
        turn_id="turn-2",
        now=1001.0,
    )

    record = service.get_mastery("tx.boundary")
    assert record.attempt_count >= 1
    assert record.correct_count >= 1
    assert record.mastery > 0.0


def test_misconception_diagnosis_selects_counterexample_when_teaching(tmp_path) -> None:
    service = _service(tmp_path)
    # Tạo một evidence trước để bỏ cold-start probe và đi vào teach.
    service.commit_evidence(
        "tx.boundary",
        evidence_type="application",
        strength=0.2,
        correct=True,
        turn_id="seed",
        now=900.0,
    )
    executive = TutorExecutive(service, reasoner=_MisconceptionReasoner())

    decision = executive.plan_next_move(
        "Rollback DB thì Kafka cũng rollback theo đúng không?",
        turn_id="turn-misconception",
        now=1000.0,
    )

    assert decision is not None
    assert decision.policy_step.kind == "teach"
    assert decision.diagnosis.primary_need == "misconception"
    assert decision.move.kind == "counterexample"
    assert decision.state.hypotheses[0].kind == "misconception"


def test_delivery_receipt_is_required_before_counting_teaching(tmp_path) -> None:
    service = _service(tmp_path)
    service.commit_evidence(
        "tx.boundary",
        evidence_type="application",
        strength=0.2,
        correct=True,
        turn_id="seed",
        now=900.0,
    )
    executive = TutorExecutive(service)
    decision = executive.plan_next_move("Tiếp tục", turn_id="turn-1", now=1000.0)
    assert decision is not None and decision.policy_step.kind == "teach"

    before = service.snapshot()["session_counts"].get("tx.boundary")
    executive.commit_delivery(decision, successful=False)
    after_failed = service.snapshot()["session_counts"].get("tx.boundary")
    assert before == after_failed

    executive.commit_delivery(decision, successful=True)
    after_success = service.snapshot()["session_counts"]["tx.boundary"]
    assert after_success["teach_count"] == 1


def test_user_interruption_is_not_semantically_graded_unless_caller_marks_response(tmp_path) -> None:
    service = _service(tmp_path)
    assessment = SemanticAssessment(
        kp_id="",
        verdict="pass",
        confidence=0.95,
        evidence_type="feynman_pass",
        strength=1.0,
        summary="Would pass if this were actually an answer.",
    )
    executive = TutorExecutive(service, reasoner=_SemanticReasoner(assessment))
    executive.plan_next_move("Dạy tôi", turn_id="turn-1", now=1000.0)

    # process_turn mặc định KHÔNG coi user turn mới là answer cho tutor.
    executive.process_turn(
        "Khoan, mở chủ đề khác trước.",
        turn_id="turn-interrupt",
        response_to_previous_move=False,
        now=1001.0,
    )

    record = service.get_mastery("tx.boundary")
    assert record.attempt_count == 0


def test_learner_pace_changes_teaching_budget(tmp_path) -> None:
    service = _service(tmp_path)
    service.commit_evidence(
        "tx.boundary",
        evidence_type="application",
        strength=0.2,
        correct=True,
        turn_id="seed",
        now=900.0,
    )
    executive = TutorExecutive(service)

    decision = executive.plan_next_move(
        "Giải thích tiếp",
        turn_id="turn-fast",
        learner=LearnerProfile(preferred_pace="fast"),
        now=1000.0,
    )

    assert decision is not None
    assert decision.policy_step.kind == "teach"
    # max_sentences là HINT MỀM: envelope không còn cắt lời theo pace (trần cứng 4
    # cho fast đã gỡ). Teach vẫn ra đúng phase; độ dài/nhịp do cá tính Root rải qua
    # after=may_continue, không phải con số này.
    assert decision.envelope.max_sentences >= 4
    assert decision.move.max_sentences >= 4

class _SocraticReasoner(ConservativeTutorReasoner):
    def choose_move(self, observation, state, diagnosis, envelope):
        return TeachingMove(
            move_id="model-owned-id-should-be-replaced",
            kind="socratic_question",
            kp_id=observation.kp_id,
            objective="Buộc user tự nối boundary.",
            instruction_vi="Hỏi user một counterfactual ngắn.",
            requires_user_response=False,
            max_sentences=2,
        )


def test_socratic_move_inside_teach_phase_still_waits_for_user(tmp_path) -> None:
    service = _service(tmp_path)
    service.commit_evidence(
        "tx.boundary",
        evidence_type="application",
        strength=0.2,
        correct=True,
        turn_id="seed",
        now=900.0,
    )
    executive = TutorExecutive(service, reasoner=_SocraticReasoner())

    decision = executive.plan_next_move("Tiếp tục", turn_id="turn-socratic", now=1000.0)

    assert decision is not None
    assert decision.policy_step.kind == "teach"
    assert decision.move.kind == "socratic_question"
    assert decision.move.requires_user_response is True
    assert decision.to_writer_context()["delivery_constraints"]["requires_user_response"] is True
    assert decision.move.move_id.startswith("tm:tx.boundary:")


def test_delivery_commit_is_idempotent_by_move_id(tmp_path) -> None:
    service = _service(tmp_path)
    service.commit_evidence(
        "tx.boundary",
        evidence_type="application",
        strength=0.2,
        correct=True,
        turn_id="seed",
        now=900.0,
    )
    executive = TutorExecutive(service)
    decision = executive.plan_next_move("Tiếp tục", turn_id="turn-1", now=1000.0)
    assert decision is not None

    executive.commit_delivery(decision, successful=True)
    executive.commit_delivery(decision, successful=True)

    snapshot = service.snapshot()["session_counts"]["tx.boundary"]
    assert snapshot["teach_count"] == 1

class _MisconceptionThenWeakPassReasoner(_MisconceptionReasoner):
    def assess_response(self, observation, state, previous_move):
        return SemanticAssessment(
            kp_id=previous_move.kp_id,
            verdict="pass",
            confidence=0.52,
            evidence_type="feynman_pass",
            strength=0.9,
            summary="Tín hiệu quá yếu để xóa hypothesis cũ.",
        )


def test_low_confidence_assessment_does_not_rewrite_hypothesis_state(tmp_path) -> None:
    service = _service(tmp_path)
    service.commit_evidence(
        "tx.boundary",
        evidence_type="application",
        strength=0.2,
        correct=True,
        turn_id="seed",
        now=900.0,
    )
    reasoner = _MisconceptionThenWeakPassReasoner()
    executive = TutorExecutive(service, reasoner=reasoner)
    first = executive.plan_next_move(
        "Rollback DB thì Kafka cũng rollback theo.",
        turn_id="turn-1",
        now=1000.0,
    )
    assert first is not None
    # Counterexample không bắt buộc response; dùng một Socratic move giả lập để
    # đánh dấu lifecycle đang chờ câu trả lời.
    executive._state = replace(  # noqa: SLF001 - test invariant nội bộ
        executive._state,
        last_move=replace(first.move, kind="socratic_question", requires_user_response=True),
    )

    executive.observe_response("Tôi nghĩ vậy.", turn_id="turn-2", now=1001.0)

    hypotheses = executive.snapshot()["state"]["hypotheses"]
    assert hypotheses
    assert hypotheses[0]["status"] == "active"
    assert hypotheses[0]["confidence"] == 0.86


def test_delivery_receipt_binds_exact_public_text_for_next_assessment(tmp_path) -> None:
    service = _service(tmp_path)
    executive = TutorExecutive(service)
    decision = executive.plan_next_move("Dạy tôi", turn_id="turn-1", now=1000.0)
    assert decision is not None

    exact_text = "Nếu Kafka đã nhận message nhưng DB rollback ngay sau đó, consumer có biết DB vừa rollback không?"
    executive.commit_delivery(decision, successful=True, public_text=exact_text)

    snapshot = executive.snapshot()
    assert snapshot["state"]["last_delivery_text"] == exact_text

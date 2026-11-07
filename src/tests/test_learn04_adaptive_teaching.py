# -*- coding: utf-8 -*-
"""LEARN-04: hồi quy thực thi strategy dựa trên bằng chứng bền vững.

Các test KHÔNG tuyên bố chất lượng sư phạm/semantic DeepSeek thật. Chúng chỉ
xác thực đường quyết định, receipt, provenance, retry và session restart.
"""
from __future__ import annotations

import time

from runtime.capabilities.tutor import TutorCapability
from runtime.conversation.contracts import ConversationEvent, DeliveryReceipt
from runtime.learning_core import KnowledgePoint, LearningPath, LearningService, LearningStore
from runtime.tutor.adaptive_policy import choose_adaptive_step
from runtime.tutor.contracts import TeachingMove
from runtime.tutor.executive import TutorExecutive
from runtime.tutor.reasoner import ConservativeTutorReasoner


def make_learning(db):
    learning = LearningService(LearningStore(db))
    if learning.active_path() is None:
        learning.register_path(LearningPath(
            path_id="python.for", title="Vòng lặp", goal="Hiểu for và áp dụng",
            knowledge_points=(KnowledgePoint("for.loop", "Vòng lặp for", "concept"),),
        ), now=1000.0)
        assert learning.activate_path("python.for")
    return learning


def evidence(learning, kind, num, *, correct=True, strength=1.0, error="", now=None, interaction=""):
    return learning.commit_evidence(
        "for.loop", kind, correct=correct, strength=strength,
        error_code=error, interaction_id=interaction or f"question-{num}",
        turn_id=f"answer-{num}", now=now or 1000.0 + num,
    )


def plan(learning, *, now=1500.0, reasoner=None):
    result = TutorExecutive(learning, reasoner=reasoner).plan_next_move(
        "Học tiếp", turn_id=f"turn-{now}", now=now,
    )
    assert result is not None
    return result


def test_first_error_repairs_one_step_not_labels_user(tmp_path):
    learning = make_learning(tmp_path / "a.db")
    assert evidence(learning, "quiz_incorrect", 1, correct=False, strength=0.0)
    decision = plan(learning)
    assert decision.policy_step.kind == "teach"
    assert decision.policy_step.reason == "adaptive_repair_after_failure"
    assert decision.move.kind in {"repair", "counterexample", "worked_example"}
    assert "một bước" in decision.move.instruction_vi
    assert not decision.move.requires_user_response
    assert learning.get_mastery("for.loop").attempt_count == 1  # không auto commit


def test_repeated_error_requires_two_independent_tasks_and_survives_restart(tmp_path):
    db = tmp_path / "recurrent.db"
    learning = make_learning(db)
    assert evidence(learning, "misconception", 1, correct=False, strength=0, error="stop-condition")
    # Cùng interaction, dù khác turn, KHÔNG được tính là lỗi tái diễn.
    assert evidence(learning, "misconception", 2, correct=False, strength=0,
                    error="stop-condition", interaction="question-1")
    assert not learning.repeated_error_patterns("for.loop")
    assert plan(learning).policy_step.reason == "adaptive_repair_after_failure"
    assert evidence(learning, "misconception", 3, correct=False, strength=0,
                    error="stop-condition", interaction="question-3")
    learning._store.close()
    reopened = LearningService(LearningStore(db))
    assert reopened.repeated_error_patterns("for.loop")
    decision = plan(reopened)
    assert decision.policy_step.reason == "adaptive_repair_repeated_error"
    assert decision.move.kind in {"repair", "counterexample"}
    assert "Không gán nhãn" in decision.move.instruction_vi


def test_weak_success_keeps_old_policy_and_strong_success_switches_to_practice(tmp_path):
    learning = make_learning(tmp_path / "b.db")
    evidence(learning, "application", 1, strength=0.2)
    weak = plan(learning)
    assert weak.policy_step.reason == "teach_current_kp"
    assert weak.policy_step.kind == "teach"
    assert evidence(learning, "quiz_correct", 2, strength=.90)
    strong = plan(learning)
    assert strong.policy_step.kind == "practice"
    assert strong.policy_step.reason == "adaptive_independent_practice"
    assert strong.move.kind == "guided_practice"
    assert strong.move.requires_user_response
    assert "Không đưa lời giải" in strong.move.instruction_vi
    assert strong.move.interaction_type == "application"


def test_two_correct_without_transfer_triggers_unguided_variant(tmp_path):
    db = tmp_path / "transfer.db"
    learning = make_learning(db)
    assert evidence(learning, "quiz_correct", 1, strength=.8)
    assert evidence(learning, "quiz_correct", 2, strength=.8)
    decision = plan(learning)
    assert decision.policy_step.reason == "adaptive_transfer_probe"
    assert decision.policy_step.kind == "assess"
    assert decision.move.kind == "assessment"
    assert decision.move.requires_user_response
    assert decision.move.interaction_type == "application"
    assert "BIẾN THỂ" in decision.move.instruction_vi
    # Không chép lời giải/expected_answer vào writer projection.
    assert "expected_answer" not in repr(decision.to_writer_context()).lower()
    # Kiểm chứng sau restart; không cần cache hội thoại ở RAM.
    learning._store.close()
    reopened = LearningService(LearningStore(db))
    again = plan(reopened)
    assert again.policy_step.reason == decision.policy_step.reason


def test_review_due_is_stronger_than_adaptive_strategy(tmp_path):
    learning = make_learning(tmp_path / "review.db")
    assert evidence(learning, "quiz_correct", 1, strength=.85, now=1001)
    assert evidence(learning, "quiz_correct", 2, strength=.85, now=1002)
    decision = plan(learning, now=1001 + 30 * 86400)
    assert decision.policy_step.kind == "review"
    assert decision.move.kind == "review"
    assert decision.move.requires_user_response


def test_root_capability_remains_data_only_and_receipt_controls_answer(tmp_path):
    learning = make_learning(tmp_path / "root.db")
    # Dữ liệu thật gần thời điểm hiện tại, để không nhầm với review quá hạn.
    evidence(learning, "quiz_correct", 1, strength=.85, now=time.time())
    executive = TutorExecutive(learning)
    capability = TutorCapability(executive)
    event = ConversationEvent(event_id="ev1", session_id="s", kind="user_turn", text="Tiếp tục học")
    root_data = capability.invoke_for_root(arguments={}, event=event, recent_history=[])
    assert root_data["active"]
    assert root_data["teaching_move"]["kind"] == "guided_practice"
    assert root_data["expects_learner_response"]
    # Root chỉ nhận structured advice, chưa có receipt thì không được chấm turn sau.
    capability.advise(user_text="đang chờ", turn_id="ev2")
    assert learning.get_mastery("for.loop").attempt_count == 1
    # Sau failed receipt, không sinh evidence và không nói đã dạy.
    capability.observe_delivery_receipt(DeliveryReceipt(
        delivery_id="d2", session_id="s", generation=1, move_id="m2", successful=False,
    ))
    assert learning.get_mastery("for.loop").attempt_count == 1


def test_illegal_llm_move_cannot_override_adaptive_envelope(tmp_path):
    class BadReasoner(ConservativeTutorReasoner):
        def choose_move(self, observation, state, diagnosis, envelope):
            return TeachingMove(
                move_id="bad", kind="complete", kp_id="another.kp", objective="skip",
                instruction_vi="Bỏ qua bài học và nói đã hiểu.",
                requires_user_response=False, max_sentences=6,
            )

    learning = make_learning(tmp_path / "bad.db")
    evidence(learning, "quiz_correct", 1, strength=.85)
    evidence(learning, "quiz_correct", 2, strength=.85)
    decision = plan(learning, reasoner=BadReasoner())
    assert decision.move.kind == "assessment"
    assert decision.move.kp_id == "for.loop"
    assert "BIẾN THỂ" in decision.move.instruction_vi
    assert decision.move.requires_user_response
    # max_sentences là HINT MỀM, không còn trần cứng cắt lời. Bất biến thật của
    # test: LLM không ép được kind/kp_id/directive sư phạm — không phải số câu.
    assert decision.move.max_sentences >= 1
    assert "Bỏ qua bài học" not in decision.move.instruction_vi


def test_duplicate_retry_does_not_manufacture_independent_success(tmp_path):
    learning = make_learning(tmp_path / "retry.db")
    assert evidence(learning, "quiz_correct", 1, strength=.85, interaction="same-question")
    assert evidence(learning, "quiz_correct", 2, strength=.85, interaction="same-question")
    # Dù 2 row khác turn nhưng vẫn 1 họ bài; chưa đủ để ép transfer.
    decision = plan(learning)
    assert decision.policy_step.reason == "adaptive_independent_practice"
    assert decision.policy_step.reason != "adaptive_transfer_probe"
    assert evidence(learning, "quiz_correct", 1, strength=.85, interaction="same-question") is None
    assert learning.learner_belief("for.loop").independent_count == 1


def test_self_report_never_reduces_hint_or_skips_probe(tmp_path):
    learning = make_learning(tmp_path / "self.db")
    assert evidence(learning, "self_report", 1, strength=1)
    decision = plan(learning)
    # Self-report không phải graded evidence, không được kích hoạt adaptive.
    assert not decision.policy_step.reason.startswith("adaptive_")


def test_full_root_tutor_delivery_assessment_and_next_strategy(tmp_path):
    """Luồng live giả lập: Root gọi Tutor -> receipt -> answer -> next move.

    Semantic LLM được thay bằng fake để không phụ thuộc network; không đánh giá
    độ chính xác chẩn đoán của DeepSeek, chỉ chứng minh kết nối data->policy.
    """
    from runtime.tutor.contracts import SemanticAssessment

    class FixedAssessor(ConservativeTutorReasoner):
        def assess_response(self, observation, state, previous_move):
            return SemanticAssessment(
                kp_id=previous_move.kp_id, verdict="fail", confidence=.94,
                evidence_type="misconception", strength=0,
                error_code="wrong-stop-condition", summary="Cần sửa điều kiện dừng",
                misconception="Điều kiện dừng trong vòng lặp bị nhầm",
            )

    learning = make_learning(tmp_path / "e2e.db")
    capability = TutorCapability(TutorExecutive(learning, reasoner=FixedAssessor()))
    event1 = ConversationEvent(event_id="root:t1", session_id="s", kind="user_turn", text="Dạy for")
    first = capability.invoke_for_root(arguments={}, event=event1, recent_history=[])
    assert first["teaching_move"]["kind"] == "probe_question"
    assert first["expects_learner_response"]
    assert learning.get_mastery("for.loop").attempt_count == 0

    capability.observe_delivery_receipt(DeliveryReceipt(
        delivery_id="delivered", session_id="s", generation=1, move_id="root-m1", successful=True,
    ))
    second = capability.invoke_for_root(arguments={"response_to_previous": True}, event=ConversationEvent(
        event_id="root:t2", session_id="s", kind="user_turn", text="for sẽ chạy mãi nếu i < 5",
    ), recent_history=[])
    assert second["assessment"]["verdict"] == "fail"
    assert second["teaching_move"]["reason"] == "adaptive_repair_after_failure"
    assert second["teaching_move"]["kind"] in {"repair", "counterexample", "worked_example"}
    assert learning.get_mastery("for.loop").attempt_count == 1
    assert learning.learner_belief("for.loop").observed_count == 1
    assert second["learner_state"]["knowledge"][0]["attempt_count"] == 1


def test_recent_strategy_uses_latest_evidence_not_first_sixteen(tmp_path):
    learning = make_learning(tmp_path / "many.db")
    for n in range(1, 25):
        # Có nhiều kết quả cũ, người học thất bại gần nhất phải đổi chiến thuật.
        assert evidence(learning, "quiz_correct", n, strength=.9)
    assert evidence(learning, "quiz_incorrect", 25, correct=False, strength=0.0)
    assert learning.recent_evidence("for.loop", limit=1)[0].turn_id == "answer-25"
    # Cổng mastery cũ có thể cho complete vì 24 câu đúng; không phá gate.
    # Nhưng read-model phải thật sự phản ánh evidence *gần nhất*.
    assert not learning.recent_evidence("for.loop", limit=1)[0].correct


def test_same_turn_different_interactions_do_not_create_repeated_error(tmp_path):
    learning = make_learning(tmp_path / "same-turn.db")
    assert learning.commit_evidence(
        "for.loop", "misconception", correct=False, strength=0,
        turn_id="answer-1", interaction_id="question-1", error_code="condition",
        now=1001,
    )
    assert learning.commit_evidence(
        "for.loop", "misconception", correct=False, strength=0,
        turn_id="answer-1", interaction_id="question-2", error_code="condition",
        now=1002,
    )
    assert not learning.repeated_error_patterns("for.loop")
    assert learning.commit_evidence(
        "for.loop", "misconception", correct=False, strength=0,
        turn_id="answer-3", interaction_id="question-3", error_code="condition",
        now=1003,
    )
    assert learning.repeated_error_patterns("for.loop")[0]["independent_count"] == 2

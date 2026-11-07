# -*- coding: utf-8 -*-
"""Deterministic guardrail policy cho Tutor Executive.

Model được quyền linh hoạt về *cách dạy*, nhưng không được tự đổi phase của
learning loop. Ví dụ LearningService bảo đang `assess` thì reasoner không được
quay sang lecture 10 đoạn. Đây là lớp giữ agent tự nhiên nhưng không hỗn loạn.
"""
from __future__ import annotations

from dataclasses import replace

from runtime.learning_core.contracts import LearnerProfile
from runtime.learning_core.policy import TutorStep
from runtime.tutor.contracts import MoveEnvelope, TeachingMove, TutorDiagnosis


class TutorGuardrailPolicy:
    """Biến TutorStep deterministic thành một envelope cho semantic reasoner."""

    _RESPONSE_REQUIRED_KINDS = frozenset({
        "probe_question", "socratic_question", "guided_practice", "assessment", "review",
    })

    # Trần rộng nhất mà MoveEnvelope/TeachingMove cho phép (contract bound tới 6).
    # Dùng làm HINT MỀM thay cho các trần cứng 2/3 cũ: guardrail không còn cắt lời
    # theo số câu, cá tính Root tự rải nhịp. Không phải quota, không phải speech budget.
    _SOFT_CADENCE_HINT = 6

    def envelope(
        self,
        step: TutorStep,
        diagnosis: TutorDiagnosis,
        learner: LearnerProfile,
    ) -> MoveEnvelope:
        # `max_sentences` ở đây KHÔNG còn là trần cứng cắt lời. Nó là HINT MỀM về
        # độ dài một move — cá tính Root + câu cadence trong ROOT_SYSTEM_CORE mới
        # quyết Eris rải nhịp (nói một ý rồi after=may_continue) thay vì bị đếm câu.
        # Dùng trần rộng (`_SOFT_CADENCE_HINT`) để guardrail giữ *phase/kind/evidence*
        # chứ không làm phẳng giọng theo số câu. Phần sư phạm thật (không hé đáp án,
        # không giải hộ, spaced retrieval, directive đánh giá) nằm ở `enforce()`.
        # pace cũ chỉ dùng để tính teach_budget làm trần cứng; nay trần đã gỡ.
        hint = self._SOFT_CADENCE_HINT

        # LEARN-04: evidence từ SQLite quyết định *khung* chiến thuật;
        # DeepSeek chỉ chọn cách thể hiện trong khung, không tự kết luận mastery.
        if step.reason == "adaptive_repair_repeated_error":
            return MoveEnvelope(
                allowed_kinds=("counterexample", "repair"),
                requires_user_response=False,
                max_sentences=hint,
                reason="adaptive_repair_repeated_error",
            )
        if step.reason == "adaptive_repair_after_failure":
            return MoveEnvelope(
                allowed_kinds=("repair", "worked_example", "counterexample"),
                requires_user_response=False,
                max_sentences=hint,
                reason="adaptive_repair_after_failure",
            )
        if step.reason == "adaptive_transfer_probe":
            return MoveEnvelope(
                allowed_kinds=("assessment",),
                requires_user_response=True,
                max_sentences=hint,
                interaction_type="application",
                reason="adaptive_transfer_probe",
            )
        if step.reason == "adaptive_independent_practice":
            return MoveEnvelope(
                allowed_kinds=("guided_practice",),
                requires_user_response=True,
                max_sentences=hint,
                interaction_type="application",
                reason="adaptive_independent_practice",
            )

        if step.kind == "probe":
            return MoveEnvelope(
                allowed_kinds=("probe_question", "socratic_question"),
                requires_user_response=True,
                max_sentences=hint,
                interaction_type=step.interaction_type,
                reason="probe_before_teach",
            )

        if step.kind == "teach":
            has_misconception = any(
                item.status == "active"
                and item.kind == "misconception"
                and item.confidence >= 0.62
                for item in diagnosis.hypotheses
            )
            if has_misconception or diagnosis.primary_need == "misconception":
                return MoveEnvelope(
                    allowed_kinds=("counterexample", "socratic_question", "repair", "explain"),
                    requires_user_response=False,
                    max_sentences=hint,
                    interaction_type="short_answer",
                    reason="repair_misconception_before_more_content",
                )
            return MoveEnvelope(
                allowed_kinds=("explain", "analogy", "worked_example", "socratic_question"),
                requires_user_response=False,
                max_sentences=hint,
                interaction_type="short_answer",
                reason="bounded_teaching_move",
            )

        if step.kind == "practice":
            return MoveEnvelope(
                allowed_kinds=("guided_practice", "socratic_question"),
                requires_user_response=True,
                max_sentences=hint,
                interaction_type=step.interaction_type,
                reason="practice_requires_learner_action",
            )

        if step.kind == "assess":
            return MoveEnvelope(
                allowed_kinds=("assessment", "guided_practice"),
                requires_user_response=True,
                max_sentences=hint,
                interaction_type=step.interaction_type,
                reason="assessment_must_collect_evidence",
            )

        if step.kind == "review":
            return MoveEnvelope(
                allowed_kinds=("review",),
                requires_user_response=True,
                max_sentences=hint,
                interaction_type=step.interaction_type,
                reason="spaced_retrieval_before_reteaching",
            )

        if step.kind == "advance":
            return MoveEnvelope(
                allowed_kinds=("transition",),
                requires_user_response=False,
                max_sentences=hint,
                interaction_type="short_answer",
                reason="mastery_gate_passed",
            )

        if step.kind == "complete":
            return MoveEnvelope(
                allowed_kinds=("complete",),
                requires_user_response=False,
                max_sentences=hint,
                interaction_type="short_answer",
                reason="learning_path_complete",
            )

        return MoveEnvelope(
            allowed_kinds=("wait",),
            requires_user_response=False,
            max_sentences=hint,
            interaction_type="short_answer",
            reason="policy_yields_floor",
        )

    def enforce(self, move: TeachingMove, envelope: MoveEnvelope, *, kp_id: str) -> TeachingMove:
        """Ép proposal của model về contract an toàn mà không làm mất intent chính."""
        kind = move.kind if move.kind in envelope.allowed_kinds else envelope.allowed_kinds[0]
        # Một số move (đặc biệt Socratic question) tự thân cần user trả lời,
        # kể cả khi phase cha là `teach`. Không được phát câu hỏi rồi tiếp tục
        # autonomous như thể đó chỉ là một câu giải thích.
        requires_response = (
            envelope.requires_user_response
            or kind in self._RESPONSE_REQUIRED_KINDS
        )
        # Với các bước adaptive, policy cung cấp chỉ dẫn cố định để mô hình
        # không làm rơi mục tiêu "bài biến thể / ít gợi ý / sửa đúng lỗi".
        # Không chép error_code/giả thuyết vào public prompt như một sự thật.
        directives = {
            "adaptive_repair_repeated_error": (
                "Đưa một phản ví dụ ngắn hoặc sửa một mắt xích khái niệm. "
                "Không gán nhãn năng lực người học; không lặp nguyên bài giảng.",
                "",
            ),
            "adaptive_repair_after_failure": (
                "Sửa một bước cụ thể của bài trước, giải thích nguyên nhân và "
                "giữ lại phần còn lại để người học tự thử ở bước tiếp theo.",
                "",
            ),
            "adaptive_transfer_probe": (
                "Đặt một bài áp dụng BIẾN THỂ với bối cảnh mới so với ví dụ trước. "
                "Không giải mẫu, không hé đáp án và không gợi ý trước khi người học trả lời.",
                "Kiểm tra khả năng vận dụng độc lập bằng một bài mới khác bề mặt.",
            ),
            "spaced_retrieval_before_reteaching": (
                "Kiểm tra lại kiến thức cũ bằng MỘT câu hỏi nhớ lại không gợi ý. "
                "Ưu tiên ví dụ biến thể, không chép đáp án hay giải mẫu trước. "
                "Nếu user quên, đợi họ trả lời rồi mới hỗ trợ.",
                "Đánh giá recall độc lập của một kiến thức đã học.",
            ),
            "adaptive_independent_practice": (
                "Cho một bài thực hành ngắn để người học tự giải. "
                "Không đưa lời giải hoặc gợi ý trước; chỉ hỗ trợ thêm sau khi có câu trả lời.",
                "Yêu cầu tự vận dụng kiến thức trước khi nhận trợ giúp.",
            ),
        }
        directive = directives.get(envelope.reason)
        return replace(
            move,
            kind=kind,
            kp_id=kp_id or move.kp_id,
            instruction_vi=directive[0] if directive else move.instruction_vi,
            question_goal=(directive[1] or move.question_goal) if directive else move.question_goal,
            reason=envelope.reason if directive else move.reason,
            interaction_type=envelope.interaction_type,
            requires_user_response=requires_response,
            # `max_sentences` là HINT MỀM, KHÔNG phải quota cắt lời: không còn
            # `min()` kéo move xuống dưới ý định của chính nó. Giữ gợi ý của move;
            # chỉ dùng envelope (đã nới rộng) khi move không tự nêu. Cá tính/Root
            # quyết nhịp thật qua after=may_continue, không phải con số này.
            max_sentences=max(move.max_sentences, envelope.max_sentences),
        )


__all__ = ["TutorGuardrailPolicy"]

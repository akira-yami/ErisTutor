# -*- coding: utf-8 -*-
"""Semantic reasoner boundary cho Tutor Executive.

Đây là điểm cắm model thông minh. Runtime không phụ thuộc một provider cụ thể.
Adapter LLM chỉ cần trả các contract có cấu trúc; không trả chain-of-thought.
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping, Protocol

from runtime.tutor.contracts import (
    LearnerHypothesis,
    MoveEnvelope,
    SemanticAssessment,
    TeachingMove,
    TutorDiagnosis,
    TutorObservation,
    TutorState,
)


class TutorReasoner(Protocol):
    """Interface semantic duy nhất mà TutorExecutive cần."""

    def diagnose(self, observation: TutorObservation, state: TutorState) -> TutorDiagnosis:
        ...

    def choose_move(
        self,
        observation: TutorObservation,
        state: TutorState,
        diagnosis: TutorDiagnosis,
        envelope: MoveEnvelope,
    ) -> TeachingMove:
        ...

    def assess_response(
        self,
        observation: TutorObservation,
        state: TutorState,
        previous_move: TeachingMove,
    ) -> SemanticAssessment | None:
        ...


def _move_id(observation: TutorObservation, kind: str, turn_count: int) -> str:
    raw = f"{observation.path_id}|{observation.kp_id}|{observation.turn_id}|{kind}|{turn_count}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"tm:{observation.kp_id}:{digest}"[:120]


class ConservativeTutorReasoner:
    """Fallback deterministic khi semantic model chưa được gắn.

    Fallback này cố ý *không* chấm câu trả lời tự do. Nó chỉ giúp hệ thống vẫn có
    hành vi hợp lệ, còn semantic evidence phải chờ model assessor thật.
    """

    def diagnose(self, observation: TutorObservation, state: TutorState) -> TutorDiagnosis:
        step = observation.policy_step
        active_hypotheses = tuple(item for item in state.hypotheses if item.status == "active")
        strong_misconception = next(
            (
                item for item in active_hypotheses
                if item.kind == "misconception" and item.confidence >= 0.62
            ),
            None,
        )

        if strong_misconception is not None:
            return TutorDiagnosis(
                primary_need="misconception",
                summary="Có giả thuyết misconception đủ mạnh; cần kiểm tra hoặc tạo counterexample trước khi dạy thêm.",
                hypotheses=active_hypotheses,
                recommended_strategy="counterexample_then_probe",
                confidence=strong_misconception.confidence,
            )

        need_by_step = {
            "probe": "diagnose",
            "teach": "explain",
            "practice": "practice",
            "assess": "assess",
            "review": "review",
            "advance": "advance",
            "complete": "complete",
            "wait": "wait",
        }
        need = need_by_step.get(step.kind, "diagnose")
        if step.reason == "prerequisite_unmet":
            need = "prerequisite"

        return TutorDiagnosis(
            primary_need=need,
            summary=f"Đi theo learning policy: {step.kind} ({step.reason}).",
            hypotheses=active_hypotheses,
            recommended_strategy=step.kind,
            confidence=0.72,
        )

    def choose_move(
        self,
        observation: TutorObservation,
        state: TutorState,
        diagnosis: TutorDiagnosis,
        envelope: MoveEnvelope,
    ) -> TeachingMove:
        preferred = envelope.allowed_kinds[0]
        if diagnosis.primary_need == "misconception" and "counterexample" in envelope.allowed_kinds:
            preferred = "counterexample"
        elif diagnosis.primary_need == "diagnose" and "probe_question" in envelope.allowed_kinds:
            preferred = "probe_question"
        elif diagnosis.primary_need == "explain" and "explain" in envelope.allowed_kinds:
            preferred = "explain"

        instruction_by_kind = {
            "probe_question": "Hỏi đúng một câu ngắn để biết user đã có mental model nào; chưa giải thích đáp án trước.",
            "socratic_question": "Dùng một câu hỏi Socratic để user tự nối nguyên nhân và hệ quả; không gợi đáp án quá sớm.",
            "explain": "Giải thích đúng một ý cốt lõi bằng mental model rõ ràng, ưu tiên nguyên nhân trước chi tiết.",
            "counterexample": "Đưa một counterexample tối giản phá vỡ giả định sai, rồi chỉ ra đúng boundary bị nhầm.",
            "analogy": "Dùng một phép tương tự ngắn và nói rõ chỗ phép tương tự ngừng đúng.",
            "worked_example": "Làm một ví dụ nhỏ theo từng bước nhưng để user còn một bước cuối để tự nối.",
            "guided_practice": "Giao một bài áp dụng nhỏ có mục tiêu rõ; không giải hộ toàn bộ.",
            "assessment": "Đặt một câu kiểm tra trực tiếp vào knowledge point hiện tại; câu trả lời phải tạo được evidence.",
            "review": "Yêu cầu retrieval từ trí nhớ trước; không nhắc lại bài rồi mới hỏi.",
            "repair": "Quay về điểm gãy trong mental model, sửa đúng một dependency rồi kiểm tra lại.",
            "transition": "Xác nhận ngắn rằng gate hiện tại đã qua và chuyển sang knowledge point kế tiếp.",
            "complete": "Tóm tắt ngắn mục tiêu đã đạt và nêu một cách tự kiểm chứng/ứng dụng sau buổi học.",
            "wait": "Nhường lượt cho user; không tự dựng thêm bài học.",
        }
        question_goal = ""
        expected_shape = ""
        if envelope.requires_user_response:
            question_goal = "Thu được evidence trực tiếp về cách user hiểu hoặc áp dụng knowledge point hiện tại."
            expected_shape = "Một câu trả lời ngắn có reasoning hoặc bước áp dụng đủ để đánh giá semantic."

        return TeachingMove(
            move_id=_move_id(observation, preferred, state.turn_count),
            kind=preferred,
            kp_id=observation.kp_id,
            objective=f"Tiến triển knowledge point: {observation.kp_title or observation.kp_id}",
            instruction_vi=(instruction_by_kind[preferred]
                            + " Giữ giọng Eris tự nhiên: hài khô, trêu nhẹ, tự giễu hoặc có quan điểm khi phù hợp; "
                              "không làm phẳng cá tính thành lời giảng trung tính. Vẫn giữ ràng buộc sư phạm."),
            question_goal=question_goal,
            expected_response_shape=expected_shape,
            interaction_type=envelope.interaction_type,
            requires_user_response=envelope.requires_user_response,
            max_sentences=envelope.max_sentences,
            reason=diagnosis.summary,
        )

    def assess_response(
        self,
        observation: TutorObservation,
        state: TutorState,
        previous_move: TeachingMove,
    ) -> SemanticAssessment | None:
        # Không dùng regex để giả vờ hiểu câu trả lời tự do.
        return None


class MappingTutorReasonerAdapter:
    """Adapter mỏng để nối bất kỳ structured-LLM runtime nào vào TutorExecutive.

    Mỗi callback nhận payload bounded dạng dict và phải trả Mapping. Cách này cho
    phép dùng model/router hiện có của Eris mà không làm TutorExecutive biết API
    của provider. Callback cũng chỉ cần trả *kết luận có cấu trúc*, không CoT.
    """

    def __init__(
        self,
        *,
        diagnose_call: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        move_call: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        assess_call: Callable[[Mapping[str, Any]], Mapping[str, Any] | None],
    ) -> None:
        self._diagnose_call = diagnose_call
        self._move_call = move_call
        self._assess_call = assess_call

    def diagnose(self, observation: TutorObservation, state: TutorState) -> TutorDiagnosis:
        payload = {
            "observation": observation.to_reasoner_dict(),
            "tutor_state": state.to_dict(),
            "output_contract": "TutorDiagnosis",
        }
        return TutorDiagnosis.from_mapping(self._diagnose_call(payload))

    def choose_move(
        self,
        observation: TutorObservation,
        state: TutorState,
        diagnosis: TutorDiagnosis,
        envelope: MoveEnvelope,
    ) -> TeachingMove:
        payload = {
            "observation": observation.to_reasoner_dict(),
            "tutor_state": state.to_dict(),
            "diagnosis": diagnosis.to_dict(),
            "envelope": envelope.to_dict(),
            "output_contract": "TeachingMove",
        }
        return TeachingMove.from_mapping(self._move_call(payload))

    def assess_response(
        self,
        observation: TutorObservation,
        state: TutorState,
        previous_move: TeachingMove,
    ) -> SemanticAssessment | None:
        payload = {
            "observation": observation.to_reasoner_dict(),
            "tutor_state": state.to_dict(),
            "previous_move": previous_move.to_writer_dict(),
            "output_contract": "SemanticAssessment",
        }
        result = self._assess_call(payload)
        return SemanticAssessment.from_mapping(result) if isinstance(result, Mapping) else None


__all__ = [
    "ConservativeTutorReasoner",
    "MappingTutorReasonerAdapter",
    "TutorReasoner",
]

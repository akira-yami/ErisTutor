# -*- coding: utf-8 -*-
"""Model-backed TutorReasoner dùng structured worker sẵn có của Eris.

Module này là integration adapter, không phải domain core. Nó chỉ biết một
transport tối thiểu: `router.invoke_structured_worker(...)`.

Nếu model fail:
- diagnose/move: fallback conservative để tutor không crash;
- semantic assessment: trả None, vì thà không ghi evidence còn hơn chấm bừa.
"""
from __future__ import annotations

from dataclasses import replace
import math
from typing import Any, Mapping

from runtime.tutor.contracts import (
    MoveEnvelope,
    SemanticAssessment,
    TeachingMove,
    TutorDiagnosis,
    TutorObservation,
    TutorState,
)
from runtime.tutor.reasoner import ConservativeTutorReasoner


_HYPOTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "kp_id", "summary", "confidence", "evidence_refs"],
    "properties": {
        "hypothesis_id": {"type": "string", "maxLength": 120},
        "kind": {
            "type": "string",
            "enum": [
                "misconception", "missing_prerequisite", "fragile_knowledge",
                "procedural_gap", "transfer_gap", "confidence_mismatch", "unknown",
            ],
        },
        "kp_id": {"type": "string", "maxLength": 120},
        "summary": {"type": "string", "maxLength": 500},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "evidence_refs": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 120},
        },
    },
}

DIAGNOSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "primary_need", "summary", "hypotheses", "understood_points",
        "uncertain_points", "recommended_strategy", "confidence",
    ],
    "properties": {
        "primary_need": {
            "type": "string",
            "enum": [
                "diagnose", "explain", "misconception", "prerequisite", "practice",
                "assess", "review", "advance", "complete", "wait",
            ],
        },
        "summary": {"type": "string", "maxLength": 650},
        "hypotheses": {"type": "array", "maxItems": 8, "items": _HYPOTHESIS_SCHEMA},
        "understood_points": {
            "type": "array", "maxItems": 6,
            "items": {"type": "string", "maxLength": 240},
        },
        "uncertain_points": {
            "type": "array", "maxItems": 6,
            "items": {"type": "string", "maxLength": 240},
        },
        "recommended_strategy": {"type": "string", "maxLength": 240},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}

MOVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "kind", "kp_id", "objective", "instruction_vi", "question_goal",
        "expected_response_shape", "interaction_type", "requires_user_response",
        "max_sentences", "reason",
    ],
    "properties": {
        "move_id": {"type": "string", "maxLength": 120},
        "kind": {
            "type": "string",
            "enum": [
                "probe_question", "socratic_question", "explain", "counterexample",
                "analogy", "worked_example", "guided_practice", "assessment", "review",
                "repair", "transition", "complete", "wait",
            ],
        },
        "kp_id": {"type": "string", "maxLength": 120},
        "objective": {"type": "string", "maxLength": 500},
        "instruction_vi": {"type": "string", "maxLength": 700},
        "question_goal": {"type": "string", "maxLength": 500},
        "expected_response_shape": {"type": "string", "maxLength": 400},
        "interaction_type": {"type": "string", "maxLength": 120},
        "requires_user_response": {"type": "boolean"},
        "max_sentences": {"type": "integer", "minimum": 1, "maximum": 6},
        "reason": {"type": "string", "maxLength": 240},
    },
}

ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "kp_id", "verdict", "confidence", "evidence_type", "strength",
        "summary", "error_code", "misconception",
    ],
    "properties": {
        "kp_id": {"type": "string", "maxLength": 120},
        "verdict": {"type": "string", "enum": ["pass", "partial", "fail", "uncertain"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "evidence_type": {
            "type": "string",
            "enum": [
                "feynman_pass", "feynman_fail", "application", "paraphrase",
                "misconception", "exposure",
            ],
        },
        "strength": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "summary": {"type": "string", "maxLength": 600},
        "error_code": {"type": "string", "maxLength": 120},
        "misconception": {"type": "string", "maxLength": 500},
    },
}


def valid_semantic_assessment_wire(raw: Any) -> bool:
    """Chỉ nhận JSON đúng schema trước khi SemanticAssessment normalize.

    Không để output thiếu field / NaN / verdict sai tự được fallback thành
    kết luận đủ confidence, rồi commit evidence. Validator hẹp theo wire schema
    hiện có; không phán xử độ đúng về mặt kiến thức.
    """
    if not isinstance(raw, Mapping):
        return False
    props = ASSESSMENT_SCHEMA["properties"]
    required = set(ASSESSMENT_SCHEMA["required"])
    if not required.issubset(raw) or any(key not in props for key in raw):
        return False
    for key, value in raw.items():
        rule = props[key]
        if rule["type"] == "string":
            if not isinstance(value, str) or len(value) > rule.get("maxLength", 10**8):
                return False
        elif rule["type"] == "number":
            if (isinstance(value, bool) or not isinstance(value, (float, int))
                or not math.isfinite(value)
                or value < rule.get("minimum", -math.inf)
                or value > rule.get("maximum", math.inf)):
                return False
        if "enum" in rule and value not in rule["enum"]:
            return False
    return True


class RuntimeTutorReasoner:
    """Structured semantic brain của tutor, chạy trên model router hiện có."""

    def __init__(self, router: Any, *, learner_client: Any = None, logger: Any = None) -> None:
        if not callable(getattr(router, "invoke_structured_worker", None)):
            raise TypeError("router must expose invoke_structured_worker")
        self.router = router
        self.learner_client = learner_client
        self.logger = logger
        self.fallback = ConservativeTutorReasoner()

    def diagnose(self, observation: TutorObservation, state: TutorState) -> TutorDiagnosis:
        raw = self._call(
            system=(
                "Bạn là semantic diagnosis worker của Eris Tutor. Chỉ trả JSON theo schema. "
                "Hãy suy luận như một tutor giỏi nhưng chỉ xuất KẾT LUẬN CÓ CẤU TRÚC, không xuất chain-of-thought. "
                "Phân biệt: thiếu prerequisite, misconception, hiểu mong manh, thiếu procedural skill và thiếu transfer. "
                "Mọi learner hypothesis đều là giả thuyết mềm: nếu evidence chưa đủ thì confidence thấp và ưu tiên probe. "
                "Không phán xét con người, không suy năng lực tổng quát từ một câu trả lời. "
                "Tôn trọng policy_step; bạn chẩn đoán cách đi trong phase đó chứ không tự đổi learning phase."
            ),
            payload={
                "observation": observation.to_reasoner_dict(),
                "tutor_state": state.to_dict(),
            },
            schema=DIAGNOSIS_SCHEMA,
            name="eris_tutor_diagnosis_deep",
            max_tokens=1100,
            priority=72,
        )
        if not raw:
            return self.fallback.diagnose(observation, state)
        try:
            return TutorDiagnosis.from_mapping(raw)
        except Exception:
            return self.fallback.diagnose(observation, state)

    def choose_move(
        self,
        observation: TutorObservation,
        state: TutorState,
        diagnosis: TutorDiagnosis,
        envelope: MoveEnvelope,
    ) -> TeachingMove:
        raw = self._call(
            system=(
                "Bạn là teaching-strategy worker của Eris Tutor. Chỉ trả JSON theo schema, không viết câu trả lời public hoàn chỉnh, không xuất chain-of-thought. "
                "Chọn ĐÚNG MỘT teaching move nằm trong envelope.allowed_kinds. "
                "Ưu tiên hành vi của tutor người giỏi: probe khi chưa chắc, counterexample khi có misconception, "
                "Socratic khi user có thể tự nối reasoning, worked example khi procedure còn mơ hồ, practice để kiểm tra transfer. "
                "instruction_vi là chỉ dẫn ngắn cho writer, không phải bài giảng. Không chứa expected answer. "
                "instruction_vi phải giữ giọng Eris tự nhiên: có thể hài khô/trêu nhẹ/tự giễu/có quan điểm; "
                "đừng biến nội dung sư phạm thành lời chatbot trung tính, nhưng không hé đáp án hay giải hộ. "
                "Không tự vượt max_sentences hoặc đổi phase do learning policy quyết định."
            ),
            payload={
                "observation": observation.to_reasoner_dict(),
                "tutor_state": state.to_dict(),
                "diagnosis": diagnosis.to_dict(),
                "envelope": envelope.to_dict(),
            },
            schema=MOVE_SCHEMA,
            name="eris_tutor_strategy_deep",
            max_tokens=850,
            priority=70,
        )
        if not raw:
            return self.fallback.choose_move(observation, state, diagnosis, envelope)
        try:
            return TeachingMove.from_mapping(raw)
        except Exception:
            return self.fallback.choose_move(observation, state, diagnosis, envelope)

    def assess_response(
        self,
        observation: TutorObservation,
        state: TutorState,
        previous_move: TeachingMove,
    ) -> SemanticAssessment | None:
        raw = self._call(
            system=(
                "Bạn là semantic assessor của Eris Tutor. Chỉ trả JSON theo schema; không xuất chain-of-thought. "
                "Đánh giá câu trả lời dựa trên knowledge point, previous_move VÀ tutor_state.last_delivery_text. "
                "last_delivery_text là câu public chính xác user đã nhận; khi wording thực tế khác intent ban đầu, phải chấm theo câu đã delivery. Không chấm văn phong. "
                "PASS khi user thể hiện mental model đúng; PARTIAL khi lõi đúng nhưng thiếu một dependency quan trọng; "
                "FAIL khi có lỗi khái niệm rõ; UNCERTAIN khi evidence không đủ hoặc user không thật sự trả lời. "
                "Nếu không chắc, phải dùng uncertain thay vì đoán. Chỉ mô tả misconception cụ thể khi câu trả lời thật sự chứng minh nó."
            ),
            payload={
                "observation": observation.to_reasoner_dict(),
                "tutor_state": state.to_dict(),
                "previous_move": previous_move.to_writer_dict(),
            },
            schema=ASSESSMENT_SCHEMA,
            name="eris_tutor_semantic_assessor_deep",
            max_tokens=850,
            priority=76,
        )
        if not valid_semantic_assessment_wire(raw):
            return None
        try:
            assessment = SemanticAssessment.from_mapping(raw)
        except Exception:
            return None
        # Runtime authority giữ đúng KP đang được hỏi; model không được redirect
        # evidence sang một concept khác bằng output JSON.
        return replace(assessment, kp_id=previous_move.kp_id)

    def _call(
        self,
        *,
        system: str,
        payload: Mapping[str, Any],
        schema: Mapping[str, Any],
        name: str,
        max_tokens: int,
        priority: int,
    ) -> dict[str, Any]:
        # Phần chấm semantic được đưa cho role learner riêng khi được cấu hình.
        # Root vẫn quyết định gọi Tutor; Learner không được tự chạy Root loop.
        client = (
            (self.learner_client if name in {"eris_tutor_diagnosis_deep", "eris_tutor_semantic_assessor_deep"} else None)
            or getattr(self.router, "deep_client", None)
            or getattr(self.router, "observer_client", None)
            or getattr(self.router, "surface_client", None)
            or getattr(getattr(self.router, "llm_service", None), "client", None)
        )
        if client is None:
            return {}
        try:
            result = self.router.invoke_structured_worker(
                client,
                system=system,
                payload=payload,
                schema=schema,
                name=name,
                max_tokens=max_tokens,
                reasoning_effort="medium",
                # Tutor không sở hữu root agent loop. Đây chỉ là một typed
                # pedagogical worker; ``cognitive_lane`` giúp telemetry/queue biết
                # request đang làm diagnosis/move/assessment nào mà không đánh
                # đồng nó với tên model hay resource class.
                inference_tier="subconscious_4b",
                cognitive_lane=f"tutor:{name}",
                priority=priority,
                timeout_seconds=30.0,
            )
            return dict(result) if isinstance(result, Mapping) else {}
        except Exception as exc:
            debug = getattr(self.logger, "debug", None)
            if callable(debug):
                debug("[tutor] structured worker failed name=%s: %s", name, exc)
            return {}


__all__ = [
    "ASSESSMENT_SCHEMA",
    "DIAGNOSIS_SCHEMA",
    "MOVE_SCHEMA",
    "RuntimeTutorReasoner",
    "valid_semantic_assessment_wire",
]

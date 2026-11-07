# -*- coding: utf-8 -*-
"""Model router cho observer và intention planner của kiến trúc C."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import replace
from typing import Any, Mapping, Sequence

from runtime.diagnostic_logging import safe_log
from runtime.cognition.turn_anchor import anchor_from_semantic_frame, previous_assistant_from_history
from runtime.cognition.normalization import normalize_vi
from runtime.social_stance_policy import STANCE_PUBLIC_RULES_VI
from runtime.eris_voice_contract import (
    CHAT_VOICE_CORE_V39,
    CHAT_STYLE_RULES_V39,
    AUTONOMOUS_VOICE_CORE_V39,
)
from runtime.pedagogical_contracts import TeachingDirective, validate_directive
from llm.deepseek import is_deepseek
from llm.raw_trace import record_observer_diagnostic


from .contracts import CognitiveObservation, IntentionPlan
from .discovery import DiscoveryPlan
from .identity import CONFLICT_TEMPLATES, DEFAULT_DEFENSES, DEFAULT_EMERGENT_VALUES, DEFAULT_MOTIVES, DEFAULT_RELATIONSHIP, DEFAULT_VALUES
from .personality import DEFAULT_CORE, DEFAULT_MOOD, DEFAULT_TEMPERAMENT


class ObserverInferenceError(RuntimeError):
    """Lỗi có phân loại của observer, không chứa prompt, token hay raw provider.

    Router phải PHÁT RA lỗi này thay vì biến sự cố thành `{}` rồi để
    CognitiveObservation.from_mapping() tự dựng một quan sát `neutral` giả.
    """

    VALID_STATUSES = {"empty_output", "parse_error", "provider_error", "timeout"}

    def __init__(self, status: str, *, detail: str = "") -> None:
        self.status = status if status in self.VALID_STATUSES else "provider_error"
        # Chỉ giữ metadata ngắn đã kiểm soát; không nhận str(exception) từ provider.
        self.detail = detail[:100]
        super().__init__(self.status)


def _observer_output_budget(*, deep: bool = False) -> int:
    """Schema objective có nhiều trường; 520 token trước đây dễ làm JSON cụt.

    Tách ngân sách output khỏi HTTP timeout; giới hạn để tránh request vô hạn.
    Có thể tune từ thực nghiệm, không cam kết một con số đúng với mọi model.
    """
    name = "ERIS_POST_OBSERVER_DEEP_MAX_TOKENS" if deep else "ERIS_POST_OBSERVER_MAX_TOKENS"
    default = 2300 if deep else 1800
    try:
        return max(800, min(4096, int(os.getenv(name, str(default)) or default)))
    except (TypeError, ValueError):
        return default


def _validate_observer_object(data: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    """Kiểm tra objective schema trước khi model được coi là quan sát hợp lệ.

    Strict=false ở gateway chỉ là gợi ý. Không tin rằng model đã tuân thủ;
    fail rõ `parse_error` thay vì lén điền hàng chục default vào state.
    Không đưa dữ liệu của user/model vào chuỗi lỗi, chỉ tên trường.
    """
    import math

    def validate(value: Any, definition: Mapping[str, Any], path: str = "root") -> None:
        kind = definition.get("type")
        if kind == "object":
            if not isinstance(value, Mapping):
                raise ObserverInferenceError("parse_error", detail=f"invalid_type:{path}")
            missing = [str(k) for k in definition.get("required", ()) if k not in value]
            if missing:
                raise ObserverInferenceError("parse_error", detail="missing:" + ",".join(missing)[:90])
            for key, member in definition.get("properties", {}).items():
                if key in value and isinstance(member, Mapping):
                    validate(value[key], member, str(key))
        elif kind == "array":
            if not isinstance(value, list):
                raise ObserverInferenceError("parse_error", detail=f"invalid_type:{path}")
            if "maxItems" in definition and len(value) > int(definition["maxItems"]):
                raise ObserverInferenceError("parse_error", detail=f"too_many_items:{path}")
            member = definition.get("items", {})
            if isinstance(member, Mapping):
                for item in value:
                    validate(item, member, path + "[]")
        elif kind == "string":
            if not isinstance(value, str):
                raise ObserverInferenceError("parse_error", detail=f"invalid_type:{path}")
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise ObserverInferenceError("parse_error", detail=f"invalid_type:{path}")
        elif kind in {"integer", "number"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (
                kind == "integer" and not isinstance(value, int)
            ) or not math.isfinite(float(value)):
                raise ObserverInferenceError("parse_error", detail=f"invalid_type:{path}")
            if "minimum" in definition and value < definition["minimum"]:
                raise ObserverInferenceError("parse_error", detail=f"out_of_range:{path}")
            if "maximum" in definition and value > definition["maximum"]:
                raise ObserverInferenceError("parse_error", detail=f"out_of_range:{path}")
        if "enum" in definition and value not in definition["enum"]:
            raise ObserverInferenceError("parse_error", detail=f"invalid_enum:{path}")

    validate(data, schema)
    if not str(data.get("user_act", "")).strip() or not str(data.get("relationship_meaning", "")).strip():
        raise ObserverInferenceError("parse_error", detail="missing_semantic_act")




def _deepseek_web_authority_mode() -> bool:
    provider = str(os.getenv("ERIS_REMOTE_PROVIDER", "") or "").strip().lower().replace("-", "_")
    return provider in {"deepseek_web", "ds2api", "whale2api", "whale"}


def _semantic_deadline_seconds() -> float:
    explicit = str(os.getenv("ERIS_MAIN_SEMANTIC_DEADLINE_SEC", "") or "").strip()
    try:
        if explicit:
            raw = float(explicit)
        else:
            from runtime.runtime_settings_store import runtime_float
            raw = runtime_float("chat", "chat_timeout_seconds", default=90.0)
    except (TypeError, ValueError, ImportError):
        raw = 90.0
    return max(3.0, min(300.0, float(raw)))




def _public_display_language_mismatch(text: str, language_code: str) -> bool:
    """Guard hẹp cho lỗi model đổ speech language vào display text.

    V57 live soak đã bắt được trường hợp display tiếng Việt đột nhiên thành cả
    câu Nhật/Trung dù prompt yêu cầu text=vi và speech_text=ja. Không sửa nội
    dung bằng regex; chỉ phát hiện để public writer được retry đúng một lần.
    """
    code = str(language_code or "vi").strip().lower()
    if code != "vi":
        return False
    letters = [ch for ch in str(text or "") if ch.isalpha()]
    if not letters:
        return False

    def is_cjk(ch: str) -> bool:
        point = ord(ch)
        return (
            0x4E00 <= point <= 0x9FFF
            or 0x3400 <= point <= 0x4DBF
            or 0x3040 <= point <= 0x309F
            or 0x30A0 <= point <= 0x30FF
            or 0xAC00 <= point <= 0xD7AF
        )

    cjk = sum(1 for ch in letters if is_cjk(ch))
    return cjk >= 4 and cjk / max(1, len(letters)) >= 0.25

def _public_writer_deadline_seconds() -> float:
    """Deadline riêng cho câu public trực tiếp.

    Chat trực tiếp không nên kế thừa nguyên timeout 90 giây của semantic pipeline.
    Writer chỉ hiện thực hóa một câu đã có planning/context, vì vậy timeout phải
    ngắn, cấu hình được và không bao giờ dài hơn deadline semantic tổng.
    """

    explicit = str(os.getenv("ERIS_PUBLIC_WRITER_DEADLINE_SEC", "") or "").strip()
    try:
        if explicit:
            raw = float(explicit)
        else:
            from runtime.runtime_settings_store import runtime_float
            raw = runtime_float("chat", "public_writer_timeout_seconds", default=24.0)
    except (TypeError, ValueError, ImportError):
        raw = 24.0
    return max(3.0, min(_semantic_deadline_seconds(), 60.0, float(raw)))


DISCOVERY_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "lane", "topic", "needs_external", "freshness_required",
        "verification_required", "eris_interest", "user_relevance",
        "novelty", "importance", "confidence", "reason"
    ],
    "properties": {
        "lane": {
            "type": "string",
            "enum": ["none", "vietnam_local_signal", "fresh_curiosity", "user_environment", "tutor_artifact"],
        },
        "topic": {"type": "string"},
        "needs_external": {"type": "boolean"},
        "freshness_required": {"type": "boolean"},
        "verification_required": {"type": "boolean"},
        "eris_interest": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "user_relevance": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "novelty": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason": {"type": "string"},
    },
}

COGNITIVE_OBSERVATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "event_kind",
        "user_act",
        "relationship_meaning",
        "continuity",
        "repair_needed",
        "assistant_fault_probability",
        "memory_worthy",
        "confidence",
        "ambiguity",
        "relational_delta",
        "competence_delta",
        "factual_delta",
        "boundary_delta",
        "familiarity_delta",
        "closeness_delta",
        "playful_safety_delta",
        "rupture_delta",
        "appraisal",
        "self_interpretation",
        "self_narrative_candidate",
        "self_narrative_confidence",
        "consequence",
        "trait_signals",
        "identity_signals",
        "active_motives",
        "activated_defenses",
        "active_conflict",
        "value_choice",
        "discourse",
        "open_loop",
        "memory_candidate",
        "evidence",
        "notes",
    ],
    "properties": {
        "event_kind": {"type": "string", "enum": ["chat", "force_speak", "autonomous", "startup"]},
        "user_act": {"type": "string"},
        "relationship_meaning": {"type": "string"},
        "continuity": {"type": "string"},
        "repair_needed": {"type": "boolean"},
        "assistant_fault_probability": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "memory_worthy": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "ambiguity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        **{
            name: {"type": "number", "minimum": -1.0, "maximum": 1.0}
            for name in (
                "relational_delta",
                "competence_delta",
                "factual_delta",
                "boundary_delta",
                "familiarity_delta",
                "closeness_delta",
                "playful_safety_delta",
                "rupture_delta",
            )
        },
        "appraisal": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "pleasantness",
                "curiosity",
                "tenderness",
                "embarrassment",
                "irritation",
                "pressure",
                "novelty",
            ],
            "properties": {
                name: {"type": "number", "minimum": 0.0, "maximum": 1.0}
                for name in (
                    "pleasantness",
                    "curiosity",
                    "tenderness",
                    "embarrassment",
                    "irritation",
                    "pressure",
                    "novelty",
                )
            },
        },
        "self_interpretation": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "frame", "confidence", "self_authored", "evidence"],
            "properties": {
                "summary": {"type": "string"},
                "frame": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "self_authored": {"type": "boolean"},
                "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            },
        },
        "self_narrative_candidate": {"type": "string"},
        "self_narrative_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "consequence": {
            "type": "object",
            "additionalProperties": False,
            "required": ["strategy_use_id", "strategy", "target_goal", "context", "outcome", "reinforcement", "confidence", "evidence"],
            "properties": {
                "strategy_use_id": {"type": "string"},
                "strategy": {"type": "string"},
                "target_goal": {"type": "string"},
                "context": {"type": "string"},
                "outcome": {"type": "string"},
                "reinforcement": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            },
        },
        "trait_signals": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "category", "trait", "delta", "confidence", "importance",
                    "source", "evidence", "strategy"
                ],
                "properties": {
                    "category": {"type": "string", "enum": ["mood", "temperament", "core"]},
                    "trait": {"type": "string"},
                    "delta": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "source": {
                        "type": "string",
                        "enum": ["user_interaction", "self_choice", "consequence", "environment"]
                    },
                    "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                    "strategy": {"type": "string"},
                },
            },
        },
        "identity_signals": {
            "type": "array",
            "maxItems": 14,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["dimension", "key", "delta", "confidence", "importance", "scope", "context", "evidence"],
                "properties": {
                    "dimension": {"type": "string", "enum": ["value", "motive", "defense", "belief", "relationship", "emergent_value", "imprint"]},
                    "key": {"type": "string"},
                    "delta": {"type": "number", "minimum": -1.0, "maximum": 1.0},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "scope": {"type": "string"},
                    "context": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                },
            },
        },
        "active_motives": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "strength", "trigger"],
                "properties": {
                    "key": {"type": "string"},
                    "strength": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "trigger": {"type": "string"},
                },
            },
        },
        "activated_defenses": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "strength", "trigger"],
                "properties": {
                    "key": {"type": "string"},
                    "strength": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "trigger": {"type": "string"},
                },
            },
        },
        "active_conflict": {
            "type": "object",
            "additionalProperties": False,
            "required": ["conflict_id", "activation", "left_pull", "right_pull", "chosen_side", "residue"],
            "properties": {
                "conflict_id": {"type": "string", "enum": ["", *CONFLICT_TEMPLATES]},
                "activation": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "left_pull": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "right_pull": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "chosen_side": {"type": "string"},
                "residue": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
        "value_choice": {
            "type": "object",
            "additionalProperties": False,
            "required": ["chosen", "rejected", "cost", "confidence", "evidence"],
            "properties": {
                "chosen": {"type": "string"},
                "rejected": {"type": "string"},
                "cost": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            },
        },
        "discourse": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "status", "reply_expected", "assistant_may_continue",
                "unresolved_point", "thread_key", "confidence", "evidence"
            ],
            "properties": {
                "status": {"type": "string", "enum": ["open", "closed", "waiting_user", "neutral", "unknown"]},
                "reply_expected": {"type": "boolean"},
                "assistant_may_continue": {"type": "boolean"},
                "unresolved_point": {"type": "string"},
                "thread_key": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
            },
        },
        "open_loop": {"type": "string"},
        "memory_candidate": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "notes": {"type": "string"},
    },
}


# Observer khách quan không được tự quyết định personality. Final contract vẫn
# giữ đủ field để persist/debug, nhưng inference được tách làm hai lane.
OBJECTIVE_OBSERVATION_SCHEMA: dict[str, Any] = {
    **COGNITIVE_OBSERVATION_SCHEMA,
    "required": [
        name
        for name in COGNITIVE_OBSERVATION_SCHEMA["required"]
        if name not in {"self_interpretation", "self_narrative_candidate", "self_narrative_confidence", "trait_signals", "identity_signals", "active_motives", "activated_defenses", "active_conflict", "value_choice"}
    ],
    "properties": {
        name: value
        for name, value in COGNITIVE_OBSERVATION_SCHEMA["properties"].items()
        if name not in {"self_interpretation", "self_narrative_candidate", "self_narrative_confidence", "trait_signals", "identity_signals", "active_motives", "activated_defenses", "active_conflict", "value_choice"}
    },
}

PERSONALITY_REFLECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "self_interpretation", "self_narrative_candidate", "self_narrative_confidence", "trait_signals", "identity_signals",
        "active_motives", "activated_defenses", "active_conflict",
        "value_choice", "notes"
    ],
    "properties": {
        "self_interpretation": COGNITIVE_OBSERVATION_SCHEMA["properties"]["self_interpretation"],
        "self_narrative_candidate": COGNITIVE_OBSERVATION_SCHEMA["properties"]["self_narrative_candidate"],
        "self_narrative_confidence": COGNITIVE_OBSERVATION_SCHEMA["properties"]["self_narrative_confidence"],
        "trait_signals": COGNITIVE_OBSERVATION_SCHEMA["properties"]["trait_signals"],
        "identity_signals": COGNITIVE_OBSERVATION_SCHEMA["properties"]["identity_signals"],
        "active_motives": COGNITIVE_OBSERVATION_SCHEMA["properties"]["active_motives"],
        "activated_defenses": COGNITIVE_OBSERVATION_SCHEMA["properties"]["activated_defenses"],
        "active_conflict": COGNITIVE_OBSERVATION_SCHEMA["properties"]["active_conflict"],
        "value_choice": COGNITIVE_OBSERVATION_SCHEMA["properties"]["value_choice"],
        "notes": {"type": "string"},
    },
}




SEMANTIC_FRAME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "version", "turn_kind", "discourse_relation", "user_act", "target",
        "literal_question", "ambiguity_owner", "boundary_action",
        "continuation_key", "evidence_scope", "tool_intent",
        "missing_evidence", "reference", "reply_obligation", "memory_query",
    ],
    "properties": {
        "version": {"type": "string", "enum": ["semantic_frame.v1"]},
        "turn_kind": {"type": "string"},
        "discourse_relation": {"type": "string"},
        "user_act": {"type": "string"},
        "target": {"type": "string"},
        "literal_question": {"type": "boolean"},
        "ambiguity_owner": {"type": "string", "enum": ["user", "assistant", "shared", "none"]},
        "boundary_action": {"type": "string"},
        "continuation_key": {"type": "string"},
        "evidence_scope": {"type": "string"},
        "tool_intent": {"type": "string"},
        "missing_evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "reference": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "speaker", "operation", "turn_index", "turn_offset", "confidence"],
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "none", "recent_user_turn", "recent_assistant_turn",
                        "assistant_proposition", "open_question",
                        "long_term_memory", "current_message"
                    ],
                },
                "speaker": {"type": "string", "enum": ["none", "user", "assistant"]},
                "operation": {
                    "type": "string",
                    "enum": ["none", "repeat", "recall", "clarify", "confirm", "repair", "answer"],
                },
                "turn_index": {"type": "integer", "minimum": 0},
                "turn_offset": {"type": "integer", "minimum": 0, "maximum": 16},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
        "reply_obligation": {
            "type": "object",
            "additionalProperties": False,
            "required": ["must_answer", "must_not_broad_clarify", "must_not_call_tool"],
            "properties": {
                "must_answer": {"type": "boolean"},
                "must_not_broad_clarify": {"type": "boolean"},
                "must_not_call_tool": {"type": "boolean"},
            },
        },
        "memory_query": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "intent", "subject", "predicate_hint", "object_hint",
                "temporal_scope", "answer_mode", "confidence"
            ],
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["none", "recall_fact", "recall_summary", "verify_memory", "explain_memory"]
                },
                "subject": {"type": "string"},
                "predicate_hint": {"type": "string"},
                "object_hint": {"type": "string"},
                "temporal_scope": {"type": "string"},
                "answer_mode": {
                    "type": "string",
                    "enum": ["exact", "gist", "summary", "admit_uncertain"]
                },
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        },
    },
}

ASSISTANT_ACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "proposition_mode", "confidence"],
    "properties": {
        "kind": {
            "type": "string",
            "enum": [
                "silence", "acknowledgement", "social", "assertion",
                "explanation", "opinion", "answer", "confirmation",
                "repair", "question", "action_report", "other"
            ],
        },
        "proposition_mode": {"type": "string", "enum": ["none", "new", "continue"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}


PERFORMANCE_PRESET_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": [
        "neutral", "soft_warm", "warm_embarrassed", "dry_tease",
        "playful", "curious", "focused", "cold_precision",
        "annoyed_soft", "sad_distant", "repair", "surprised"
    ],
}

UTTERANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["display", "speech", "performance", "delivery"],
    "properties": {
        "display": {
            "type": "object",
            "additionalProperties": False,
            "required": ["language", "text"],
            "properties": {
                "language": {"type": "string", "enum": ["vi", "ja", "en"]},
                "text": {"type": "string"},
            },
        },
        "speech": {
            "type": "object",
            "additionalProperties": False,
            "required": ["language", "text"],
            "properties": {
                "language": {"type": "string", "enum": ["vi", "ja", "en"]},
                "text": {"type": "string"},
            },
        },
        "performance": {
            "type": "object",
            "additionalProperties": False,
            "required": ["preset", "intensity", "nonverbal"],
            "properties": {
                "preset": PERFORMANCE_PRESET_SCHEMA,
                "intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "nonverbal": {"type": "string"},
            },
        },
        "delivery": {
            "type": "object",
            "additionalProperties": False,
            "required": ["priority", "interruptible"],
            "properties": {
                "priority": {
                    "type": "string",
                    "enum": ["background", "low", "normal", "high", "urgent"],
                },
                "interruptible": {"type": "boolean"},
            },
        },
    },
}


SELF_CAPABILITY_CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["capability_id", "state", "basis"],
    "properties": {
        "capability_id": {"type": "string"},
        "state": {"type": "string", "enum": ["available", "unavailable", "uncertain"]},
        "basis": {"type": "string"},
    },
}

CLAIM_MANIFEST_SCHEMA: dict[str, Any] = {
    "type": "array",
    "maxItems": 10,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["domain", "claim", "evidence_refs"],
        "properties": {
            "domain": {
                "type": "string",
                "enum": [
                    "internal_state", "conversation_history", "user_statement",
                    "user_profile", "shared_episode", "memory", "verified_runtime_clock", "grounded_environment",
                    "engagement_state", "discovery_artifact", "self_capability",
                    "self_preference", "self_model", "self_history", "opinion", "hypothetical"
                ],
            },
            "claim": {"type": "string"},
            "evidence_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        },
    },
}

# Contract public writer V42. Ba surface field vẫn là availability payload;
# claim_manifest là metadata TÙY CHỌN để nghiên cứu offline, không phải
# hợp đồng phát lời. Giữ schema để đọc benchmark cũ nhưng chỉ cần ba trường
# cốt lõi; lỗi manifest không được khiến Eris im lặng.
PUBLIC_REPLY_CORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["should_speak", "text", "speech_text"],
    "properties": {
        "should_speak": {"type": "boolean"},
        "text": {"type": "string"},
        "speech_text": {"type": "string"},
        "claim_manifest": CLAIM_MANIFEST_SCHEMA,
        "display_language": {"type": "string", "enum": ["vi", "ja", "en"]},
        "speech_language": {"type": "string", "enum": ["vi", "ja", "en"]},
        "performance_hint": PERFORMANCE_PRESET_SCHEMA,
        "performance_intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "nonverbal_cue": {"type": "string"},
        "delivery_priority": {
            "type": "string",
            "enum": ["background", "low", "normal", "high", "urgent"],
        },
        "interruptible": {"type": "boolean"},
        # Soft metadata cố ý không ép nested schema ở availability boundary.
        # _compat_public_envelope() mới là nơi validate/sanitize chúng.
        "semantic_frame": {},
        "assistant_act": {},
        "self_capability_claims": {},
        # Cho provider cũ vẫn thích trả nested utterance; text/speech_text ở trên
        # mới là availability contract bắt buộc.
        "utterance": {},
    },
}


PUBLIC_REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    # Meaning, both language surfaces and performance are committed by one
    # semantic authority in the same DeepSeek Web turn.
    "required": ["semantic_frame", "assistant_act", "should_speak", "utterance", "self_capability_claims"],
    "properties": {
        "semantic_frame": SEMANTIC_FRAME_SCHEMA,
        "assistant_act": ASSISTANT_ACT_SCHEMA,
        "should_speak": {"type": "boolean"},
        "utterance": UTTERANCE_SCHEMA,
        "self_capability_claims": {
            "type": "array",
            "items": SELF_CAPABILITY_CLAIM_SCHEMA,
            "maxItems": 8,
        },
    },
}


# A grounding retry may rewrite both surfaces, but cannot reinterpret the
# already validated semantic frame or assistant act.
PUBLIC_WORDING_REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["should_speak", "text", "speech_text"],
    "properties": {
        "should_speak": {"type": "boolean"},
        "text": {"type": "string"},
        "speech_text": {"type": "string"},
        "claim_manifest": CLAIM_MANIFEST_SCHEMA,
        "performance_hint": PERFORMANCE_PRESET_SCHEMA,
        "performance_intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "nonverbal_cue": {"type": "string"},
        "delivery_priority": {
            "type": "string",
            "enum": ["background", "low", "normal", "high", "urgent"],
        },
        "interruptible": {"type": "boolean"},
    },
}

TEACHING_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "global_goal", "concepts", "success_evidence", "misconception_hypotheses", "example_policy",
    ],
    "properties": {
        "global_goal": {"type": "string", "maxLength": 420},
        "concepts": {
            "type": "array",
            "minItems": 3,
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["objective", "description", "prerequisite_ids"],
                "properties": {
                    "id": {"type": "string", "maxLength": 120},
                    "objective": {"type": "string", "maxLength": 420},
                    "description": {"type": "string", "maxLength": 420},
                    "prerequisite_ids": {
                        "type": "array", "maxItems": 8,
                        "items": {"type": "string", "maxLength": 120},
                    },
                },
            },
        },
        "success_evidence": {
            "type": "array", "minItems": 1, "maxItems": 16,
            "items": {"type": "string", "maxLength": 240},
        },
        "misconception_hypotheses": {
            "type": "array", "maxItems": 16,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["description", "concept_id", "confidence"],
                "properties": {
                    "id": {"type": "string", "maxLength": 120},
                    "description": {"type": "string", "maxLength": 420},
                    "concept_id": {"type": "string", "maxLength": 120},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
            },
        },
        "example_policy": {"type": "string", "maxLength": 240},
    },
}


def _bounded_teaching_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return str(value or "")[:1200]
    if isinstance(value, Mapping):
        return {
            str(key)[:120]: _bounded_teaching_value(item, depth=depth + 1)
            for key, item in list(value.items())[:16]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_teaching_value(item, depth=depth + 1) for item in value[:12]]
    if isinstance(value, str):
        return value[:1200]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1200]


def _valid_teaching_plan_payload(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    allowed_root = set(TEACHING_PLAN_SCHEMA["properties"])
    if set(payload) != allowed_root:
        return False
    goal = payload.get("global_goal")
    evidence = payload.get("success_evidence")
    concepts = payload.get("concepts")
    hypotheses = payload.get("misconception_hypotheses")
    example_policy = payload.get("example_policy")
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 420:
        return False
    if not isinstance(example_policy, str) or len(example_policy) > 240:
        return False
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 16:
        return False
    if any(not isinstance(item, str) or not item.strip() or len(item) > 240 for item in evidence):
        return False
    if not isinstance(concepts, list) or not 3 <= len(concepts) <= 8:
        return False
    concept_keys = {"id", "objective", "description", "prerequisite_ids"}
    for concept in concepts:
        if not isinstance(concept, Mapping) or set(concept) - concept_keys:
            return False
        if not {"objective", "description", "prerequisite_ids"} <= set(concept):
            return False
        if "id" in concept and (not isinstance(concept["id"], str) or len(concept["id"]) > 120):
            return False
        for key in ("objective", "description"):
            if not isinstance(concept[key], str) or not concept[key].strip() or len(concept[key]) > 420:
                return False
        prerequisites = concept["prerequisite_ids"]
        if not isinstance(prerequisites, list) or len(prerequisites) > 8:
            return False
        if any(not isinstance(item, str) or len(item) > 120 for item in prerequisites):
            return False
    if not isinstance(hypotheses, list) or len(hypotheses) > 16:
        return False
    hypothesis_keys = {"id", "description", "concept_id", "confidence"}
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, Mapping) or set(hypothesis) - hypothesis_keys:
            return False
        if not {"description", "concept_id", "confidence"} <= set(hypothesis):
            return False
        if "id" in hypothesis and (not isinstance(hypothesis["id"], str) or len(hypothesis["id"]) > 120):
            return False
        if not isinstance(hypothesis["description"], str) or not hypothesis["description"].strip():
            return False
        if len(hypothesis["description"]) > 420:
            return False
        if not isinstance(hypothesis["concept_id"], str) or len(hypothesis["concept_id"]) > 120:
            return False
        confidence = hypothesis["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
            return False
    return True

INTENTION_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "plan_id",
        "speak",
        "motive",
        "strategy",
        "value_focus",
        "topic",
        "importance",
        "intrusion_risk",
        "confidence",
        "performance_hint",
        "tool_intent",
        "tool_input",
        "evidence_refs",
        "semantics",
        "activity",
        "notes",
    ],
    "properties": {
        "plan_id": {"type": "string"},
        "speak": {"type": "boolean"},
        "motive": {"type": "string"},
        "strategy": {"type": "string"},
        "value_focus": {"type": "string"},
        "topic": {"type": "string"},
        "importance": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "intrusion_risk": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "performance_hint": {"type": "string"},
        "tool_intent": {"type": "string"},
        "tool_input": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "semantics": {
            "type": "object",
            "additionalProperties": False,
            "required": ["turn_kind", "speech_act", "subject_key", "evidence_scope", "continuation_key"],
            "properties": {
                "turn_kind": {
                    "type": "string",
                    "enum": [
                        "chat_response", "continuation", "arc_continuation", "environment", "activity_event",
                        "discovery", "internal", "presence", "startup", "force_speak", "other"
                    ],
                },
                # speech_act là identifier runtime, không phải chỗ model viết
                # một câu mô tả tự do rồi để semantic_key nghiền thành ID rác.
                "speech_act": {
                    "type": "string",
                    "pattern": "^[a-z0-9][a-z0-9_.:-]{0,63}$",
                },
                "subject_key": {"type": "string"},
                "evidence_scope": {"type": "string"},
                "continuation_key": {"type": "string"},
            },
        },
        "activity": {
            "type": "object",
            "additionalProperties": False,
            "required": ["activity_kind", "phase", "event", "event_key", "confidence", "evidence_refs"],
            "properties": {
                "activity_kind": {"type": "string"},
                "phase": {"type": "string"},
                "event": {"type": "string"},
                "event_key": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "evidence_refs": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            },
        },
        "notes": {"type": "string"},
    },
}


def _content(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], Mapping) else {}
        message = first.get("message", {}) if isinstance(first, Mapping) else {}
        content = message.get("content", "") if isinstance(message, Mapping) else ""
        return str(content or "").strip()
    candidates = payload.get("candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0] if isinstance(candidates[0], Mapping) else {}
        content = first.get("content", {}) if isinstance(first, Mapping) else {}
        parts = content.get("parts", []) if isinstance(content, Mapping) else []
        if isinstance(parts, list):
            return "\n".join(str(p.get("text", "")) for p in parts if isinstance(p, Mapping)).strip()
    return ""


_PROTOCOL_TEXT_FIELDS = (
    "performance",
    "delivery",
    "responsemove",
    "response_move",
    "should_speak",
    "semantic_frame",
    "assistant_act",
    "utterance",
    "display",
    "speech",
    "protocol",
    "parse_mode",
    "debug_trace",
)


def _strip_transport_unsafe_unicode(text: str) -> str:
    return "".join(
        ch
        for ch in str(text or "")
        if not (0xD800 <= ord(ch) <= 0xDFFF or ord(ch) > 0xFFFF)
    )


def _clean_public_text_fragment(value: Any) -> str:
    text = _strip_transport_unsafe_unicode(str(value or ""))
    text = text.replace("\\n", " ").replace("\r", " ").replace("\n", " ")
    text = re.split(
        r'(?:[\"\']?\s*,\s*)?[\"\'](?:' + "|".join(re.escape(field) for field in _PROTOCOL_TEXT_FIELDS) + r')[\"\']\s*:',
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = re.sub(r"\s*[}\]]+\s*$", "", text).strip()
    for _ in range(3):
        before = text
        text = text.strip().strip(",;")
        text = re.sub(r"\s*[}\]]+\s*$", "", text).strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
            text = text[1:-1].strip()
        if text.endswith('"') and text.count('"') % 2 == 1:
            text = text[:-1].rstrip(" ,;")
        if text == before:
            break
    return " ".join(text.split())


def _decode_json_string_fragment(value: str) -> str:
    raw = str(value or "")
    try:
        return str(json.loads(f'"{raw}"') or "")
    except Exception:
        return raw.replace(r"\"", '"').replace(r"\\", "\\")


def _extract_string_field_from_malformed_json(raw: str, field_names: Sequence[str]) -> str:
    for field in field_names:
        match = re.search(
            rf'"{re.escape(field)}"\s*:\s*"(?P<value>(?:\\.|[^"\\])*)',
            raw,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            cleaned = _clean_public_text_fragment(_decode_json_string_fragment(match.group("value")))
            if cleaned:
                return cleaned
    return ""


def _looks_jsonish_or_protocolish(raw: str) -> bool:
    lowered = str(raw or "").lower()
    if "{" in lowered or "}" in lowered or '":' in lowered:
        return True
    return any(f'"{field}"' in lowered or f"{field}:" in lowered for field in _PROTOCOL_TEXT_FIELDS)


def _public_text_from_mapping(payload: Mapping[str, Any] | Any) -> str:
    """Lấy public display text mà không phụ thuộc metadata semantic.

    Hàm này cố ý nhỏ và không import UtteranceSpec để parser malformed JSON vẫn
    dùng được ở tầng thấp. Metadata tồn tại không được tính là "câu trả lời".
    """

    if not isinstance(payload, Mapping):
        return ""
    for key in ("text", "display_text", "reply", "response", "message", "content"):
        value = payload.get(key)
        if not isinstance(value, (Mapping, list, tuple, set)):
            cleaned = _clean_public_text_fragment(value)
            if cleaned:
                return cleaned
    utterance = payload.get("utterance")
    if isinstance(utterance, Mapping):
        display = utterance.get("display")
        if isinstance(display, Mapping):
            cleaned = _clean_public_text_fragment(display.get("text"))
            if cleaned:
                return cleaned
        cleaned = _clean_public_text_fragment(utterance.get("text"))
        if cleaned:
            return cleaned
    display = payload.get("display")
    if isinstance(display, Mapping):
        return _clean_public_text_fragment(display.get("text"))
    return ""


def _teaching_unit_count(text: str, *, ideas: bool) -> int:
    separators = r"[.!?。！？;；\r\n]+|(?:^|\s)(?:[-*•]|\d+[.)])\s+" if ideas else r"[.!?。！？\r\n]+"
    return sum(1 for part in re.split(separators, str(text or "")) if part.strip())


def _teaching_authority_violation(text: str) -> bool:
    normalized = normalize_vi(text)
    patterns = (
        # The writer cannot decide that comprehension or mastery occurred.
        r"\b(?:you|learner|student|ban|anh|em|nguoi hoc)\s+(?:(?:now|has|have|ve|fully|da|a)\s+){0,2}(?:understand|understands|understood|grasped|mastered|masters|hieu|nam duoc)\b",
        # Completion is a controller transition, not a wording decision.
        r"\b(?:this\s+|the\s+)?(?:teaching\s+)?(?:episode|lesson|course|chunk|bai hoc|buoi hoc|phan hoc)\s+(?:(?:is|has|has been|da|a)\s+)?(?:complete|completed|finished|done|over|hoan thanh|ket thuc|xong)\b",
        r"\b(?:we|you|chung ta|ban|anh|em)\s+(?:(?:are|re|have|ve|da|a)\s+)?(?:done|completed|finished|xong|hoan thanh|ket thuc)\b",
        r"\b(?:i|we|you|toi|minh|chung ta|ban|anh|em)\s+(?:(?:has|have|ve|da|a)\s+)?(?:completed|finished|ended|hoan thanh|ket thuc)\s+(?:the\s+)?(?:episode|lesson|course|chunk|bai hoc|buoi hoc|phan hoc)\b",
        # Selecting or previewing later concepts grants the writer future-plan authority.
        r"\b(?:the\s+)?next\s+(?:concept|lesson|chunk)\b",
        r"\b(?:khai niem tiep theo|bai hoc tiep theo|phan hoc tiep theo)\b",
        r"\b(?:next|after this|later|tiep theo|sau do|phan sau)\b.{0,100}\b(?:teach|explain|cover|learn|move on|day|giai thich|hoc|chuyen sang)\b",
        r"\b(?:i|we|you|toi|minh|chung ta|ban)\s+(?:will|ll|se)\s+(?:move on|chuyen sang)\b",
        r"\b(?:i|we|you|toi|minh|chung ta|ban)\s+(?:will|ll|se)\s+(?:teach|explain|cover|learn|day|giai thich|hoc)\b.{0,100}\b(?:next|following|other|another|future|tiep theo|khac)\s+(?:concept|lesson|chunk|khai niem|bai hoc|phan hoc)\b",
    )
    if any(re.search(pattern, normalized) for pattern in patterns):
        return True
    lowered = str(text or "").casefold()
    return any(
        marker in lowered
        for marker in (
            "理解できた", "理解した", "もう分かった", "レッスンは完了", "学習は完了",
            "これで終わり", "次の概念", "次は説明", "次に教",
        )
    )


def _validate_teaching_writer_payload(
    payload: Mapping[str, Any] | Any,
    directive: TeachingDirective,
) -> tuple[bool, str]:
    if not isinstance(payload, Mapping):
        return False, "teaching_writer_payload_missing"
    from runtime.utterance_spec import UtteranceSpec

    public_text = _public_text_from_mapping(payload)
    utterance = UtteranceSpec.from_mapping(payload, fallback_text=public_text)
    surfaces = tuple({utterance.display.text.strip(), utterance.speech.text.strip()} - {""})
    if not surfaces:
        return False, "teaching_writer_text_missing"
    for text in surfaces:
        if _teaching_unit_count(text, ideas=False) > directive.max_sentences:
            return False, "teaching_writer_sentence_budget_exceeded"
        if _teaching_unit_count(text, ideas=True) > directive.max_ideas:
            return False, "teaching_writer_idea_budget_exceeded"
        if _teaching_authority_violation(text):
            return False, "teaching_writer_authority_violation"
    return True, "ok"


def _has_usable_public_reply(payload: Mapping[str, Any]) -> bool:
    return bool(_public_text_from_mapping(payload))


def _salvage_malformed_public_reply(raw: str) -> dict[str, Any]:
    text = _extract_string_field_from_malformed_json(
        raw,
        ("text", "display_text", "speech_text", "reply", "response", "message", "content"),
    )
    if not text:
        text = _clean_public_text_fragment(raw)
    if not text:
        return {}
    return {"text": text, "should_speak": True, "llm_malformed_json_repair": True}


def _json(text: str, *, public_reply: bool = True) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}

    def unwrap(d: dict) -> dict:
        if not d:
            return d
        if len(d) == 1:
            key = list(d.keys())[0]
            val = d[key]
            if isinstance(val, Mapping):
                common_keys = {"text", "semantic_frame", "should_speak", "performance_hint", "event_kind", "user_act", "lane", "topic"}
                if any(k in val for k in common_keys):
                    return unwrap(dict(val))
        return d

    try:
        value = json.loads(raw)
        parsed = dict(value) if isinstance(value, Mapping) else {}
        if (
            public_reply
            and parsed
            and not _has_usable_public_reply(unwrap(parsed))
            and _looks_jsonish_or_protocolish(raw)
        ):
            repaired = unwrap(parsed)
            repaired.update(_salvage_malformed_public_reply(raw))
            return repaired
        return unwrap(parsed)
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            if not public_reply:
                return {}
            if _looks_jsonish_or_protocolish(raw):
                return _salvage_malformed_public_reply(raw)
            cleaned = _clean_public_text_fragment(raw)
            return {"text": cleaned, "should_speak": True} if cleaned else {}
        try:
            value = json.loads(match.group(0))
            parsed = dict(value) if isinstance(value, Mapping) else {}
            if (
                public_reply
                and parsed
                and not _has_usable_public_reply(unwrap(parsed))
                and _looks_jsonish_or_protocolish(raw)
            ):
                repaired = unwrap(parsed)
                repaired.update(_salvage_malformed_public_reply(raw))
                return repaired
            return unwrap(parsed)
        except Exception:
            return _salvage_malformed_public_reply(raw) if public_reply else {}


def _unwrap_schema_payload(
    payload: Mapping[str, Any] | Any,
    schema: Mapping[str, Any] | Any,
) -> dict[str, Any]:
    """Select the nested object that best matches a structured worker schema."""

    root = dict(payload) if isinstance(payload, Mapping) else {}
    schema_map = schema if isinstance(schema, Mapping) else {}
    required = {
        str(name)
        for name in (schema_map.get("required") or ())
        if str(name or "").strip()
    }
    properties = {
        str(name)
        for name in (schema_map.get("properties") or {}).keys()
        if str(name or "").strip()
    }
    targets = required or properties
    if not root or not targets:
        return root

    def score(value: Mapping[str, Any]) -> tuple[int, int]:
        keys = {str(key) for key in value.keys()}
        return len(required & keys), len(properties & keys)

    best = root
    best_score = score(root)
    for value in root.values():
        if not isinstance(value, Mapping):
            continue
        candidate = dict(value)
        candidate_score = score(candidate)
        if candidate_score > best_score:
            best = candidate
            best_score = candidate_score

    minimum_match = 1 if len(targets) == 1 else 2
    if best is not root and sum(best_score) >= minimum_match:
        return best
    return root


class AIRuntimeModelRouter:
    def __init__(
        self,
        llm_service: Any,
        *,
        logger: Any | None = None,
        budget_policy: Any | None = None,
    ) -> None:
        self.llm_service = llm_service
        self.logger = logger
        self.budget_policy = budget_policy
        self._contract_repair_lock = threading.Lock()
        self._contract_repair = None

    def _recover_dialogue(self, raw: Any, *, response_format: Mapping[str, Any],
                         messages: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        from llm.dialogue_recovery import recover_dialogue
        recovered = recover_dialogue(_content(raw), response_format)
        if recovered is None:
            return None
        try:
            source = response_format.get("json_schema", {}).get("name", "")
            with self._contract_repair_lock:
                if self._contract_repair is None:
                    from .contract_repair import BackgroundContractRepair
                    self._contract_repair = BackgroundContractRepair(self)
                repair = self._contract_repair
            # Copy only the current input, never replay full Self/history to critic.
            context = next((str(m.get("content", ""))[-12000:] for m in reversed(messages)
                            if m.get("role") == "user"), "")
            recovered["_runtime_contract_repair_id"] = repair.submit(
                source=source, raw_content=_content(raw),
                dialogue=str(recovered.get("say", recovered.get("text", ""))), context=context,
            )
        except Exception:
            # An unavailable background worker must never drop recovered speech.
            recovered["_runtime_contract_repair_id"] = ""
        return recovered

    def contract_repair_context(self) -> str:
        repair = self._contract_repair
        return repair.prompt_context() if repair is not None else ""

    def close_contract_repair(self) -> None:
        with self._contract_repair_lock:
            if self._contract_repair is not None:
                self._contract_repair.close()

    @staticmethod
    def _client_inference_profile(client: Any) -> tuple[str | None, str]:
        """Trả về ``(resource_class, backend)`` của client nếu xác định được.

        Đây là lớp tách nghĩa quan trọng của V4:

        - ``cognitive_lane``: request đang làm việc gì (turn executive, tutor...).
        - ``backend``: request đi qua transport/provider nào (DS2API, API ngoài...).
        - ``resource_class``: request có chiếm local VRAM hay không.
        - ``tier``: nhãn legacy để caller cũ và telemetry cũ không bị vỡ.

        Không được suy ra ``remote/local`` chỉ từ chuỗi tier. Một DS2API gateway có
        thể chạy tại 127.0.0.1 nhưng model thật vẫn là remote, nên worker client
        được phép khai báo ``inference_resource_class`` / ``inference_backend`` tường minh.
        """
        if client is None:
            return None, ""

        declared_class = str(getattr(client, "inference_resource_class", "") or "").strip().lower()
        declared_backend = str(getattr(client, "inference_backend", "") or "").strip()
        if declared_class in {"remote_api", "local_light", "local_heavy", "vision_local", "unknown"}:
            return declared_class, declared_backend or type(client).__name__

        base_url = str(getattr(client, "base_url", "") or "").strip().lower()
        worker_role = str(getattr(client, "worker_role", "") or "").strip().lower()
        provider_circuit = str(getattr(client, "provider_circuit_id", "") or "").strip().lower()

        # DeepSeek role worker qua DS2API/Whale2API. Gateway là localhost, nhưng
        # inference thật không chiếm VRAM của Eris.
        if worker_role or "#deepseek_web:" in provider_circuit or any(
            marker in base_url for marker in ("127.0.0.1:6011", "127.0.0.1:6015", "localhost:6011", "localhost:6015")
        ):
            return "remote_api", declared_backend or "ds2api_deepseek_web"

        # API ngoài rõ ràng là remote. Localhost khác DS2API để lại None để
        # ModelBudgetPolicy dùng compatibility rule cũ; không tự đoán nó là remote.
        if base_url.startswith(("https://", "http://")) and not any(
            host in base_url for host in ("127.0.0.1", "localhost", "0.0.0.0")
        ):
            return "remote_api", declared_backend or "openai_compatible_remote"

        return None, declared_backend or type(client).__name__

    def _run_inference(
        self,
        tier: str,
        func: Any,
        *,
        reason: str,
        priority: int,
        resource_class: str | None = None,
        backend: str = "",
        cognitive_lane: str = "",
    ) -> Any:
        # PDF annotation, pages 6-9: the local VRAM scheduler and the remote
        # inference broker solve different problems.  Keep the former, but
        # attach a broker lane so every provider request receives global quota,
        # deadline and preemption semantics.
        from runtime.inference_broker import inference_context

        reason_key = str(reason or "").strip().lower()
        cognitive_key = str(cognitive_lane or "").strip().lower()

        # V4: broker lane phải đi theo *vai trò nhận thức thật* trước, không đi
        # theo tên model/tier legacy. Turn executive và tutor đang phục vụ trực
        # tiếp user turn là dependency bắt buộc của foreground chat, nên không
        # được rơi xuống ``post_observer`` chỉ vì legacy tier là subconscious_4b.
        if cognitive_key == "public_claim_verifier":
            # Verifier có thể chờ rất lâu ở DeepSeek Web (raw thinking).
            # Dùng lane riêng không deadline thay vì kế thừa chat_tool_required
            # 25s. Nó vẫn là request bắt buộc, không phải background optional.
            lane = "public_claim_verifier"
        elif cognitive_key in {"turn_executive", "root_conversation"} or cognitive_key.startswith("tutor:"):
            # Character v1 HOTFIX: claim verifier là gate BẮT BUỘC trước
            # public delivery. Trước đây lane này rơi xuống post_observer vì
            # inference_tier=subconscious_4b => optional/preemptible. Khi chat
            # đang xử lý, broker có thể loại chính bước kiểm bắt buộc, khiến
            # writer nhận UNKNOWN, thử lại và Eris im lặng.
            # Không đổi model, nội dung lời hay điều kiện receipt PASS.
            lane = "chat_tool_required"
        elif "public_reply" in reason_key and "autonomous" not in reason_key:
            lane = "chat_main"
        elif "neuro_continuation" in reason_key:
            lane = "neuro_continuation"
        elif "neuro_deliberation" in reason_key:
            # Autonomous deliberation is rare and interruptible. A dedicated lane
            # keeps it optional *without* giving it the persistent autonomous
            # provider session that caused stale semantic carry-over.
            lane = "neuro_deliberation"
        elif "capability_planner" in reason_key and "interactive" in reason_key:
            # V30: capability planner là one-shot execution planning cho user turn.
            # Nó không giữ conversation session và không có quyền chọn motive.
            lane = "capability_deliberation_required"
        elif "capability_planner" in reason_key:
            # Autonomous capability planning vẫn interruptible/sessionless. Tên lane
            # giữ nguyên để không đổi broker policy, nhưng authority đã được thu hẹp.
            lane = "capability_deliberation"
        elif "observer" in reason_key or "observation" in reason_key:
            lane = "post_observer"
        elif (
            "autonomous" in reason_key
            or "self_initiated" in reason_key
            or "surface" in reason_key
        ):
            lane = "autonomous"
        elif "chat_intention" in reason_key:
            lane = "chat_tool_required"
        elif "seed" in reason_key or "discovery" in reason_key or "planner" in reason_key:
            lane = "tutor_background"
        elif "reflection" in reason_key or "deep" in reason_key or tier == "subconscious_4b":
            lane = "post_observer"
        else:
            lane = "transport"

        optional = lane in {
            "post_observer", "autonomous", "tutor_background",
            "neuro_continuation", "neuro_deliberation",
            "capability_deliberation",
        }
        coalesce_key = reason_key if optional else ""
        with inference_context(
            lane,
            optional=optional,
            # Timeout 0 chỉ dành cho verifier đã được runtime xác định là
            # bước bắt buộc. Context này giữ cancel_scope của user turn nhưng
            # không kế thừa deadline tổng đã hết trong lúc DeepSeek suy luận.
            timeout_seconds=0.0 if lane == 'public_claim_verifier' else None,
            coalesce_key=coalesce_key,
            metadata={
                "router_reason": reason,
                "model_tier": tier,  # legacy compatibility label
                "cognitive_lane": str(cognitive_lane or reason_key),
                "model_backend": str(backend or ""),
                "resource_class": str(resource_class or "legacy_infer"),
            },
        ):
            policy = self.budget_policy
            if policy is not None and callable(getattr(policy, "run_queued", None)):
                res = policy.run_queued(
                    tier,
                    func,
                    reason=reason,
                    priority=priority,
                    resource_class=resource_class,
                    backend=backend,
                    cognitive_lane=cognitive_lane,
                )
            else:
                res = func()

            # Extract and log reasoning telemetry for visibility/observability
            reasoning = ""
            if isinstance(res, Mapping):
                choices = res.get("choices")
                if isinstance(choices, list) and choices:
                    for choice in choices:
                        if isinstance(choice, Mapping):
                            message = choice.get("message")
                            if isinstance(message, Mapping):
                                for key in ("reasoning_content", "reasoning"):
                                    val = message.get(key)
                                    if isinstance(val, str) and val.strip():
                                        reasoning = val.strip()
                                        break
                        if reasoning:
                            break
            if reasoning and self.logger is not None:
                content_len = 0
                if isinstance(res, Mapping):
                    choices = res.get("choices")
                    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
                        msg = choices[0].get("message")
                        if isinstance(msg, Mapping):
                            content_len = len(msg.get("content", "") or "")
                safe_log(
                    self.logger,
                    "info",
                    "[%s reasoning] chars=%d content_chars=%d",
                    lane,
                    len(reasoning),
                    content_len,
                )
            return res

    @property
    def observer_client(self) -> Any | None:
        return getattr(self.llm_service, "social_parser_client", None)

    @property
    def deep_client(self) -> Any | None:
        return getattr(self.llm_service, "social_critic_client", None)

    @property
    def fallback_client(self) -> Any | None:
        return getattr(self.llm_service, "social_fallback_client", None)

    @property
    def seed_client(self) -> Any | None:
        return getattr(self.llm_service, "seed_client", None)

    @property
    def surface_client(self) -> Any | None:
        return getattr(self.llm_service, "surface_client", None)

    @property
    def executive_client(self) -> Any | None:
        return getattr(self.llm_service, "executive_client", None)

    @staticmethod
    def _client_source_label(client: Any, fallback: str) -> str:
        role = str(getattr(client, "worker_role", "") or "").strip().lower()
        return f"deepseek_web_{role}" if role else fallback

    def _call(
        self,
        client: Any,
        *,
        system: str,
        payload: Mapping[str, Any],
        schema: Mapping[str, Any],
        name: str,
        max_tokens: int,
        reasoning_effort: str | None = None,
        inference_tier: str = "subconscious_4b",
        priority: int | None = None,
        timeout_seconds: float | None = None,
        cognitive_lane: str = "",
        temperature: float | None = None,
        conversation_messages: Sequence[Mapping[str, Any]] | None = None,
        raw_reply_sink: list[str] | None = None,
    ) -> dict[str, Any]:
        observer_call = name.startswith("eris_cognitive_observation")
        if client is None:
            if observer_call:
                raise ObserverInferenceError("provider_error", detail="missing_client")
            return {}
        # PDF cleanup annotation: JSON-schema strictness is a contract choice,
        # not a removed-model compatibility branch.
        strict = False
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "strict": strict,
                "schema": dict(schema),
            },
        }
        try:
            def invoke() -> Any:
                return client.create_chat_completion(
                    messages=(
                        [dict(message) for message in conversation_messages]
                        if conversation_messages is not None
                        else [
                            {"role": "system", "content": system},
                            {"role": "user", "content": json.dumps(dict(payload), ensure_ascii=False)},
                        ]
                    ),
                    temperature=float(temperature if temperature is not None else 0.0),
                    frequency_penalty=0.0,
                    presence_penalty=0.0,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    reasoning_effort=reasoning_effort,
                    # Sentinel 0 trên lane verifier: tắt deadline HTTP của
                    # DS2API khi caller không chủ động đặt giới hạn. Nếu để
                    # None, OpenAICompatibleClient sẽ tự dùng self.timeout=30s.
                    # Những lane khác vẫn theo timeout mặc định như trước.
                    timeout=(0.0 if name == 'eris_character_claim_verifier_v1'
                             and timeout_seconds is None else timeout_seconds),
                )

            resource_class, backend = self._client_inference_profile(client)
            raw = self._run_inference(
                inference_tier,
                invoke,
                reason=name,
                priority=int(priority if priority is not None else (45 if "deep" in name else 35)),
                resource_class=resource_class,
                backend=backend,
                cognitive_lane=str(cognitive_lane or name),
            )
            content = _content(raw)
            recovered = self._recover_dialogue(
                raw, response_format=response_format,
                messages=(conversation_messages if conversation_messages is not None else
                          [{"role": "user", "content": json.dumps(dict(payload), ensure_ascii=False)}]),
            )
            parsed = recovered if recovered is not None else _json(content, public_reply=False)
            result = _unwrap_schema_payload(parsed, schema)
            if observer_call:
                if not isinstance(content, str) or not content.strip():
                    raise ObserverInferenceError("empty_output")
                if not result:
                    raise ObserverInferenceError("parse_error", detail="invalid_json_or_empty_object")
                _validate_observer_object(result, schema)
            # Lưu nguyên văn câu trả lời trên wire. Serialize lại dict parsed
            # sẽ khác JSON thật và khiến SessionGuard reset ở lượt tiếp theo.
            if raw_reply_sink is not None and isinstance(content, str) and content.strip() and result:
                raw_reply_sink.append(content)
            return result
        except Exception as exc:  # noqa: BLE001
            if observer_call:
                if isinstance(exc, ObserverInferenceError):
                    failure = exc
                else:
                    name_safe = type(exc).__name__
                    status = "timeout" if "timeout" in name_safe.lower() or "deadline" in name_safe.lower() else "provider_error"
                    failure = ObserverInferenceError(status, detail=name_safe)
                # Không dump exception gốc vào logger vì có thể chứa prompt/key.
                if self.logger is not None:
                    safe_log(self.logger, "warning", "[observer] inference_failed status=%s detail=%s model=%s",
                             failure.status, failure.detail, getattr(client, "model_name", ""))
                raise failure from exc
            if self.logger is not None:
                if name == "eris_character_claim_verifier_v1":
                    # Không log exception message: provider có thể kèm prompt,
                    # token hoặc dữ liệu riêng trong phần mô tả lỗi.
                    safe_log(self.logger, "warning",
                             "[character-v1] verifier provider failed code=VERIFIER_PROVIDER_ERROR "
                             "error_type=%s model=%s", type(exc).__name__,
                             getattr(client, "model_name", ""))
                else:
                    safe_log(self.logger, "debug", "[ai runtime model] %s failed model=%s: %s", name, getattr(client, "model_name", ""), exc)
            if name == "eris_character_claim_verifier_v1":
                # Không trộn lỗi provider (timeout, network, cancellation) với
                # lỗi JSON schema. Chỉ chuyển TÊN LỚP lỗi, không lộ thông tin
                # request/secret/response từ exception của provider.
                kind = type(exc).__name__
                return {"_runtime_verifier_error": (
                    "VERIFIER_TIMEOUT" if "Timeout" in kind else "VERIFIER_PROVIDER_ERROR"
                )}
            return {}

    def invoke_structured_worker(
        self,
        client: Any,
        *,
        system: str,
        payload: Mapping[str, Any],
        schema: Mapping[str, Any],
        name: str,
        max_tokens: int,
        reasoning_effort: str | None = None,
        inference_tier: str = "subconscious_4b",
        priority: int | None = None,
        timeout_seconds: float | None = None,
        cognitive_lane: str = "",
        temperature: float | None = None,
        conversation_messages: Sequence[Mapping[str, Any]] | None = None,
        raw_reply_sink: list[str] | None = None,
    ) -> dict[str, Any]:
        """Transport boundary cho typed role-owned workers.

        Domain module sở hữu prompt/schema và truyền ``cognitive_lane`` để nói
        *request đang làm việc gì*. Router đọc client để xác định ``backend`` và
        ``resource_class``. ``inference_tier`` chỉ còn là nhãn compatibility cho
        scheduler/caller cũ.

        Nhờ tách này, một DeepSeek worker qua DS2API có thể đồng thời là:
        ``cognitive_lane=turn_executive`` + ``backend=ds2api_deepseek_web`` +
        ``resource_class=remote_api`` + ``inference_tier=subconscious_4b``.
        Bốn giá trị đó không còn bị đánh đồng với nhau.
        """

        return self._call(
            client,
            system=system,
            payload=payload,
            schema=schema,
            name=name,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            inference_tier=inference_tier,
            priority=priority,
            timeout_seconds=timeout_seconds,
            cognitive_lane=cognitive_lane,
            temperature=temperature,
            conversation_messages=conversation_messages,
            raw_reply_sink=raw_reply_sink,
        )

    def plan_teaching_episode(self, *, request_context: Mapping[str, Any]) -> Mapping[str, Any]:
        """Request one bounded concept graph without granting public-writing authority."""

        if not isinstance(request_context, Mapping):
            return {}
        bounded_context = _bounded_teaching_value(request_context)
        client = self.deep_client or self.seed_client or self.surface_client or getattr(self.llm_service, "client", None)
        payload = self.invoke_structured_worker(
            client,
            system=(
                "You are the ERIS teaching-plan worker. Return JSON only. Build a small dependency graph "
                "for the explicit learning request using three to eight concepts. Include observable success "
                "evidence, bounded misconception hypotheses, and an example policy. Do not write a public "
                "utterance, hidden reasoning, chain of thought, comprehension verdict, completion decision, "
                "or scheduling instruction."
            ),
            payload={"explicit_plan_request": bounded_context},
            schema=TEACHING_PLAN_SCHEMA,
            name="eris_teaching_plan",
            max_tokens=1200,
            inference_tier="subconscious_4b",
            priority=60,
            timeout_seconds=30.0,
        )
        if not _valid_teaching_plan_payload(payload):
            return {}
        return dict(payload)

    @staticmethod
    def _public_reply_prompt(
        display_language_hint: str = "vi",
        speech_language_hint: str = "ja",
    ) -> str:
        """Prompt availability-first cho direct-user public writer.

        Writer không còn phải hoàn tất cả một cây semantic/social mới được phép
        nói. Public text là hợp đồng sống còn; metadata là best-effort.
        """

        language_names = {"ja": "tiếng Nhật", "en": "tiếng Anh", "vi": "tiếng Việt"}
        display_code = str(display_language_hint or "vi").strip().lower()
        speech_code = str(speech_language_hint or display_code).strip().lower()
        display_language = language_names.get(display_code, "ngôn ngữ của người dùng")
        speech_language = language_names.get(speech_code, display_language)
        style_rules = " ".join(CHAT_STYLE_RULES_V39)
        return (
            # (1) Vào vai: writer LÀ chính ERIS đang nói, không phải máy điền form.
            "Bạn đang là giọng nói trực tiếp của ERIS, đang ngồi cạnh một người thật và nói với họ. "
            "Nói như một người đang thật sự ở đó: để tâm trạng, sự tinh ý và chiều sâu nội tâm thấm vào từng câu, "
            "điềm và ấm nhưng không phô, đọc được không khí của lượt này. "
            f"{CHAT_VOICE_CORE_V39} {style_rules} "
            # (2) Hai bề mặt + khoá ngôn ngữ, viết giọng thường.
            f"Trả đúng MỘT JSON với ba field bắt buộc: should_speak, text bằng {display_language}, và speech_text bằng {speech_language}. "
            "text là câu hiển thị cho người đọc; speech_text là câu ERIS thực sự cất tiếng, cùng nghĩa và cùng tông với text. "
            "Khi câu trả lời có code hoặc giải thích lập trình, viết code trực tiếp trong text bằng khối mã Markdown (```ngôn_ngữ ... ```) hoặc inline code (`...`); phần code đó trong speech_text không cần đọc nguyên văn mã nguồn mà chỉ cần câu dẫn hoặc tóm tắt ngắn tự nhiên (ví dụ: 'Mình viết code mẫu ở đây nhé' hoặc 'Bạn xem đoạn code này nhé'), hệ thống cũng sẽ tự động lọc code khỏi giọng đọc. "
            "Nếu Root.say chỉ mời người dùng xem các bước trên Workspace/Panel sau khi tool đã hoàn tất, "
            "giữ lời thoại ngắn tự nhiên: KHÔNG tự viết lại toàn bộ lời giải hoặc đọc các công thức dài trong text/speech_text. "
            "Không tự nhận đã tạo artifact nếu Root.say và receipt chưa xác nhận. "
            f"Giữ nguyên text trong {display_language}: đừng chuyển cả câu sang một ngôn ngữ khác, một mẩu loại khác chen vào cũng không. "
            # (3) Ý đã do Root quyết; writer nói ra tự nhiên, không tự đẻ intent.
            "Ý và hướng của lượt này đã do RootAgent chọn sẵn trong `say`; việc của bạn là nói nó ra cho thật tự nhiên và đúng cảm, "
            "không tự nghĩ thêm chủ đề, câu hỏi điều hướng, CTA hay lời mời mới mà `say` không có. "
            "Nhưng những phản xạ rất người thì cứ để bật ra khi cảm xúc thật đang có: một tiếng cảm thán, một nhịp ngập ngừng, một mẩu câu cụt. "
            "Bạn là người nói ra ý đó cho hay, không phải người quyết định ý đó là gì. "
            # (4) Lằn ranh trung thực + stance + xin lỗi.
            "Đừng bịa grounding, ký ức hay khả năng mình không có. Grounding chỉ là bằng chứng để câu nói khớp với move hiện tại. "
            "Nếu Current grounding có EVIDENCE_ENRICHMENT_V73, đó là ngữ cảnh thật runtime đã tuyển từ user profile, shared episodes, recent turns và self-model có provenance: "
            "dùng nó để câu trả lời liền mạch và cụ thể hơn, nhưng đừng nâng điều chưa chắc thành fact và đừng dựng một cầu nối ký ức không có thật. "
            "Dùng evidence đúng phạm vi của nó; đừng nhận là đã hoàn tất một tool khi chưa có receipt, và khi thiếu ref thì đừng khẳng định chắc nịch. "
            "Personality context là quyền biểu đạt chứ không phải thứ để giải thích: để nó đổi cadence, độ gần, mức tự bộc lộ và cạnh hài khi hợp, đừng khai nó ra như metadata. "
            + STANCE_PUBLIC_RULES_VI + " "
            "Khi user chỉ bất đồng với một ấn tượng chủ quan, đừng tự động xin lỗi hay nhận mình sai; khi user sửa một fact thì cập nhật gọn và repair nếu cần. "
            # (5) Soft metadata tuỳ chọn; public text ưu tiên số một.
            "claim_manifest, semantic_frame, assistant_act, self_capability_claims, performance_hint/performance_intensity/nonverbal_cue/delivery_priority/interruptible đều là metadata tuỳ chọn: "
            "trả thêm khi chắc, bỏ qua khi không chắc. Nếu có claim_manifest hay self_capability_claims thì chỉ dùng ref/capability_id đã được cung cấp, không tự đặt tên. "
            "Lỗi hoặc thiếu soft metadata KHÔNG bao giờ được làm bạn bỏ text/speech_text; public text luôn ưu tiên số một. "
            "Return JSON only."
        )

    def generate_public_reply(
        self,
        *,
        chat_history: Sequence[Mapping[str, Any]],
        user_text: str,
        memory_context: str = "",
        grounding_context: str = "",
        relationship_context: str = "",
        personality_context: str = "",
        affective_voice_state: Mapping[str, Any] | str = "",
        interrupt_context: str = "",
        engagement_context: str = "",
        routing_context: str = "",
        discourse_context: str = "",
        use_escalation: bool = False,
        reasoning_effort: str | None = None,
        thinking_mode: str | None = None,
        allow_fallback: bool = False,
        language_hint: str = "vi",
        speech_language_hint: str = "ja",
        planned_intention: IntentionPlan | None = None,
        retry_instruction: str = "",
        locked_semantic_frame: Mapping[str, Any] | None = None,
        teaching_directive: TeachingDirective | None = None,
        allow_language_repair: bool = True,
    ) -> dict[str, Any]:
        if locked_semantic_frame:
            try:
                from runtime.observability import GLOBAL_OBSERVABILITY
                GLOBAL_OBSERVABILITY.increment("public_writer_wording_retry")
            except Exception:
                pass

        client = None
        if use_escalation:
            resolver = getattr(self.llm_service, "_resolve_escalation_client", None)
            if callable(resolver):
                try:
                    client = resolver()
                except Exception:
                    client = None
        # V12: public wording có worker riêng. Main/companion client chỉ là
        # compatibility fallback khi surface lane thật sự không tồn tại; nếu
        # không, ta vô tình trả tiền/thời gian cho một vòng cognition thứ hai.
        client = client or self.surface_client or getattr(self.llm_service, "client", None)
        context_parts = []
        if personality_context:
            context_parts.append(f"Current ERIS personality: {personality_context}")
        if relationship_context:
            context_parts.append(f"Relationship continuity: {relationship_context}")
        if affective_voice_state:
            if isinstance(affective_voice_state, Mapping):
                affect_text = json.dumps(dict(affective_voice_state), ensure_ascii=False, separators=(",", ":"), default=str)
            else:
                affect_text = str(affective_voice_state)
            context_parts.append(
                "Live ERIS affective voice state (expression authority, not evidence about user/world): "
                + affect_text[:3600]
            )
        if grounding_context:
            context_parts.append(f"Current grounding: {grounding_context}")
        if memory_context:
            context_parts.append(f"Relevant memory: {memory_context}")
        if interrupt_context:
            context_parts.append(f"Mechanical interrupt context: {interrupt_context}")
        if engagement_context:
            context_parts.append(f"Durable engagement contract: {engagement_context}")
        if routing_context:
            context_parts.append(f"Runtime companion routing: {routing_context}")
        if discourse_context:
            context_parts.append(
                "Discourse state (index map for the chat_history messages; no duplicated text): "
                + discourse_context
            )
        if retry_instruction:
            context_parts.append(f"One-shot rewrite instruction: {retry_instruction}")
        if locked_semantic_frame:
            context_parts.append(
                "LOCKED semantic frame (runtime authority; do not reinterpret or modify): "
                + json.dumps(dict(locked_semantic_frame), ensure_ascii=False)
            )
        if planned_intention is not None:
            context_parts.append(
                "Planner intent: "
                + json.dumps(
                    {
                        "motive": planned_intention.motive,
                        "strategy": planned_intention.strategy,
                        "value_focus": planned_intention.value_focus,
                        "response_goal": planned_intention.topic,
                        "tool_intent": planned_intention.tool_intent,
                        "tool_input": planned_intention.tool_input,
                        "notes": planned_intention.notes,
                    },
                    ensure_ascii=False,
                )
            )
        locked_teaching_directive: TeachingDirective | None = None
        if isinstance(teaching_directive, TeachingDirective):
            valid, _reason = validate_directive(teaching_directive)
            if valid and teaching_directive.stop_after_chunk:
                locked_teaching_directive = teaching_directive
                projection = {
                    "episode_id": teaching_directive.episode_id,
                    "plan_revision": teaching_directive.plan_revision,
                    "chunk_id": teaching_directive.chunk_id,
                    "current_objective": teaching_directive.current_objective,
                    "claim": teaching_directive.claim,
                    "contrast": teaching_directive.contrast,
                    "example_policy": teaching_directive.example_policy,
                    "max_ideas": teaching_directive.max_ideas,
                    "max_sentences": teaching_directive.max_sentences,
                    "allow_question": teaching_directive.allow_question,
                    "stop_after_chunk": True,
                }
                context_parts.append(
                    "LOCKED TEACHING DIRECTIVE (wording guidance only; no plan authority): "
                    + json.dumps(projection, ensure_ascii=False)
                    + " Realize only this current chunk and stop after it. Do not select or preview future "
                    "concepts; never claim that the learner understands; never claim the episode is complete."
                )
        language_names = {"ja": "tiếng Nhật", "en": "tiếng Anh", "vi": "tiếng Việt"}
        display_code = str(language_hint or "vi").strip().lower()
        speech_code = str(speech_language_hint or display_code).strip().lower()
        display_language = language_names.get(display_code, "ngôn ngữ của người dùng")
        speech_language = language_names.get(speech_code, display_language)
        system = self._public_reply_prompt(language_hint, speech_language_hint)
        if locked_semantic_frame:
            system += (
                "\n\nThis is a wording-only retry. Return should_speak, the complete utterance surfaces, and claim_manifest. "
                "The semantic frame and assistant act above are immutable and runtime will reuse them verbatim. "
                "Rewrite display and speech together; do not classify the turn again."
            )
        if context_parts:
            system += "\n\n" + "\n".join(context_parts)
        messages = [
            {"role": "system", "content": system},
            *[dict(item) for item in chat_history if isinstance(item, Mapping)],
            {"role": "user", "content": user_text},
        ]

        def call(target: Any, *, request_messages: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
            if target is None:
                return {}
            strict = False
            response_schema = (
                PUBLIC_WORDING_REWRITE_SCHEMA if locked_semantic_frame else PUBLIC_REPLY_CORE_SCHEMA
            )
            response_name = "eris_public_wording_rewrite" if locked_semantic_frame else "eris_public_reply"
            try:
                def invoke() -> Any:
                    request_kwargs = {
                        "messages": list(request_messages or messages),
                        # V38: tăng nhẹ entropy wording. Authority/grounding vẫn nằm
                        # ở semantic frame nên đây chỉ làm bề mặt bớt cùng một khuôn.
                        "temperature": 0.56,
                        "frequency_penalty": 0.0,
                        "presence_penalty": 0.0,
                        # Public writer hiện thực hóa câu trả lời và code nếu có;
                        # ngân sách 1024 token cho phép sinh code hoàn chỉnh.
                        "max_tokens": (720 if locked_semantic_frame else 1024),
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": response_name,
                                "strict": strict,
                                "schema": response_schema,
                            },
                        },
                        "reasoning_effort": reasoning_effort,
                        "timeout": _public_writer_deadline_seconds(),
                    }
                    if is_deepseek(
                        getattr(target, "base_url", ""),
                        getattr(target, "model_name", ""),
                    ):
                        request_kwargs["thinking_mode"] = thinking_mode
                    return target.create_chat_completion(**request_kwargs)

                started = time.monotonic()
                raw = self._run_inference(
                    "main_cognition",
                    invoke,
                    reason="architecture_c_public_reply",
                    priority=100,
                )
                provider_ms = max(0.0, (time.monotonic() - started) * 1000.0)
                raw_content = _content(raw)
                recovered = self._recover_dialogue(
                    raw, response_format={"type": "json_schema", "json_schema": {
                        "name": response_name, "schema": response_schema}},
                    messages=list(request_messages or messages),
                )
                parsed = recovered if recovered is not None else _json(raw_content)
                if parsed and locked_semantic_frame:
                    parsed["semantic_frame"] = dict(locked_semantic_frame)
                usable = bool(_public_text_from_mapping(parsed)) if parsed else False
                try:
                    from runtime.observability import GLOBAL_OBSERVABILITY
                    GLOBAL_OBSERVABILITY.observe(
                        "public_writer_provider_ms",
                        provider_ms,
                        tags={"rewrite": str(bool(locked_semantic_frame)).lower()},
                    )
                    GLOBAL_OBSERVABILITY.observe(
                        "public_writer_content_chars",
                        float(len(raw_content)),
                    )
                    GLOBAL_OBSERVABILITY.gauge(
                        "public_writer_usable_text",
                        1.0 if usable else 0.0,
                    )
                    GLOBAL_OBSERVABILITY.gauge(
                        "public_writer_text_salvaged",
                        1.0 if bool(parsed.get("llm_malformed_json_repair")) else 0.0,
                    )
                    GLOBAL_OBSERVABILITY.gauge(
                        "public_writer_contract_valid",
                        1.0 if usable and not bool(parsed.get("llm_malformed_json_repair")) else 0.0,
                    )
                except Exception:
                    pass
                return parsed
            except Exception as exc:  # noqa: BLE001
                if self.logger is not None:
                    safe_log(self.logger, "debug", "[ai public reply] model=%s failed: %s", getattr(target, "model_name", ""), exc)
                return {}

        payload = call(client)

        # V57.1: provider thật đã có lúc trả toàn bộ display text bằng Nhật/Trung
        # dù display_language=vi. Không "dịch cứu" bằng local code vì như vậy sẽ
        # che hành vi model. Thay vào đó retry provider đúng MỘT lần với language
        # lock rõ ràng; nếu vẫn sai, payload đi tiếp và simulator sẽ đánh FAIL.
        first_public_text = _public_text_from_mapping(payload) if payload else ""
        if allow_language_repair and payload and _public_display_language_mismatch(first_public_text, display_code):
            repair_messages = [
                *messages,
                {
                    "role": "system",
                    "content": (
                        "LANGUAGE REPAIR ONLY: field text vừa bị sai ngôn ngữ. "
                        f"Giữ nguyên nghĩa và intent, nhưng viết field text hoàn toàn bằng {display_language}. "
                        f"speech_text vẫn bằng {speech_language}. Không đổi fact, memory, stance hay claim_manifest. "
                        "Trả lại đúng JSON contract, không giải thích."
                    ),
                },
            ]
            repaired = call(client, request_messages=repair_messages)
            repaired_text = _public_text_from_mapping(repaired) if repaired else ""
            if repaired and repaired_text and not _public_display_language_mismatch(repaired_text, display_code):
                payload = repaired
                try:
                    from runtime.observability import GLOBAL_OBSERVABILITY
                    GLOBAL_OBSERVABILITY.increment("public_writer_language_repair_success")
                except Exception:
                    pass
            else:
                try:
                    from runtime.observability import GLOBAL_OBSERVABILITY
                    GLOBAL_OBSERVABILITY.increment("public_writer_language_repair_failed")
                except Exception:
                    pass

        if not payload and allow_fallback and not _deepseek_web_authority_mode():
            fallback = getattr(self.llm_service, "fallback_client", None)
            if fallback is not client:
                payload = call(fallback)
        if locked_teaching_directive is not None:
            teaching_valid, teaching_reason = _validate_teaching_writer_payload(
                payload,
                locked_teaching_directive,
            )
            if not teaching_valid:
                safe_log(
                    self.logger,
                    "debug",
                    "[ai public reply] rejected teaching writer output reason=%s episode=%s revision=%s chunk=%s",
                    teaching_reason,
                    locked_teaching_directive.episode_id,
                    locked_teaching_directive.plan_revision,
                    locked_teaching_directive.chunk_id,
                )
                return {}
        if not payload:
            # PDF annotation, pages 15-16 and 20: the local fast-path is only an
            # emergency degraded/offline response for exact closed forms. It is
            # deliberately consulted after all remote authority attempts fail,
            # so it cannot become the normal semantic classifier by accident.
            try:
                from runtime.degraded_chat_fastpath import build_degraded_reply

                payload = build_degraded_reply(user_text, language_hint=language_hint)
            except Exception:
                payload = {}
        if not payload:
            try:
                from runtime.observability import GLOBAL_OBSERVABILITY
                GLOBAL_OBSERVABILITY.gauge("public_writer_usable_text", 0.0)
            except Exception:
                pass
            return {}
        # Parser/Decision cũ vẫn nhận envelope đầy đủ, nhưng main model không phải sinh
        # các field sân khấu cơ học ấy nữa.
        return self._compat_public_envelope(
            payload,
            tool_intent=planned_intention.tool_intent if planned_intention is not None else "",
            tool_input=planned_intention.tool_input if planned_intention is not None else "",
            speech_language_hint=speech_code,
        )

    def _compat_public_envelope(
        self,
        payload: Mapping[str, Any],
        *,
        tool_intent: str = "",
        tool_input: str = "",
        speech_language_hint: str = "",
    ) -> dict[str, Any]:
        """Bọc payload model cũ mà không để JSON sai kiểu làm sập chat loop."""

        from runtime.semantic_frame import validate_assistant_act, validate_semantic_frame

        # ``_json`` bình thường đã trả Mapping. Lớp phòng thủ này vẫn giữ runtime
        # sống nếu một adapter provider tùy biến trả nhầm kiểu trong tương lai.
        data = dict(payload) if isinstance(payload, Mapping) else {}
        validation = validate_semantic_frame(data.get("semantic_frame", {}))
        public_text = _public_text_from_mapping(data)
        default_assistant_act = {
            "kind": "answer" if public_text else "silence",
            "proposition_mode": "new" if public_text else "none",
            "confidence": 0.35,
        }
        assistant_raw = data.get("assistant_act")
        assistant_validation = validate_assistant_act(
            assistant_raw if isinstance(assistant_raw, Mapping) else default_assistant_act
        )
        all_violations = (*validation.violations, *assistant_validation.violations)
        # Phân tách: wrapper_alias là backward-compat repair (model sinh thêm lớp
        # "v1" / "semantic_frame.v1"), không phải lỗi contract thật. Chỉ log debug
        # để tránh spam WARNING mỗi turn. Lỗi thật (field missing, type wrong)
        # vẫn leo warning + observability để dễ triage.
        _is_compat = lambda v: "wrapper_alias" in v or "kind_alias" in v  # noqa: E731
        compat_repairs = tuple(v for v in all_violations if _is_compat(v))
        real_violations = tuple(v for v in all_violations if not _is_compat(v))
        if compat_repairs:
            safe_log(
                self.logger,
                "debug",
                "[semantic frame] compat alias repaired (not a real violation): %s",
                ",".join(compat_repairs),
            )
        if real_violations:
            safe_log(
                self.logger,
                "warning",
                "[semantic frame] invalid semantic bindings sanitized violations=%s",
                ",".join(real_violations),
            )
            try:
                from runtime.observability import GLOBAL_OBSERVABILITY
                GLOBAL_OBSERVABILITY.log(
                    "semantic_frame_invariant_rejected",
                    tags={"count": str(len(real_violations))},
                    fields={"violations": list(real_violations)},
                    status="degraded",
                )
            except Exception as exc:
                safe_log(self.logger, "debug", "Semantic validation metric failed: %s", exc)
        from runtime.utterance_spec import UtteranceSpec
        from runtime.capability_claim_guard import normalize_self_capability_claims
        from runtime.speech_contract import configured_speech_language

        utterance = UtteranceSpec.from_mapping(
            data,
            fallback_text=public_text,
            default_display_language="vi",
            default_speech_language=speech_language_hint or configured_speech_language(),
            fallback_preset=str(data.get("performance_hint", "neutral") or "neutral"),
        )
        flattened = utterance.to_flat_payload()
        from runtime.claim_domain_contract import inspect_claim_manifest
        manifest_envelope = inspect_claim_manifest(data)
        return {
            "semantic_frame": validation.frame.to_dict(),
            "assistant_act": assistant_validation.act.to_dict(),
            # Hai object private này không được serialize ra parser/body. Orchestrator
            # dùng chúng để không parse lại cùng contract lần thứ hai.
            "_typed_semantic_frame": validation.frame,
            "_typed_assistant_act": assistant_validation.act,
            "self_capability_claims": normalize_self_capability_claims(
                data.get("self_capability_claims", ())
            ),
            # Không biến missing/sai kiểu thành [] (một manifest rỗng hợp lệ).
            # Envelope private là snapshot raw trước compatibility normalization.
            "_typed_claim_manifest": manifest_envelope,
            "claim_manifest": data.get("claim_manifest") if manifest_envelope.present else None,
            **flattened,
            "should_speak": bool(data.get("should_speak", True)),
            "system_action": str(tool_intent or ""),
            "action_input": str(tool_input or ""),
            "animation": "stand",
            "next_animation": "",
            "direction": 0,
            "target_x": -1.0,
            "bounce_action": "",
            "delivery_hint": "",
            "stage_motion": "",
        }

    @staticmethod
    def _observer_prompt(*, deep: bool = False) -> str:
        extra = (
            "Khi mơ hồ, phân biệt vài khả năng rồi giữ delta quan hệ gần 0; unknown tốt hơn drama. "
            if deep else
            "Phân tích nhanh, bảo thủ và ưu tiên delta gần 0 khi không chắc. "
        )
        return (
            "Bạn là observer hậu kỳ khách quan cho ERIS. Lượt public đã xảy ra rồi. "
            "Bạn không viết câu trả lời, không sửa lời ERIS, không tự diễn giải tính cách và không phát trait signal. "
            "Hãy mô tả điều thật sự vừa xảy ra giữa user và ERIS bằng JSON. "
            "user_act và relationship_meaning phải mô tả *user_text hiện tại*. recent_public_history chỉ là continuity, tuyệt đối không được bê act/frustration cũ sang lượt mới. "
            "current_turn_anchor.user_move_hint là neo deterministic bảo thủ; có thể sửa nó khi text rõ ràng hơn, nhưng không được bỏ qua text hiện tại để lặp frame cũ. "
            "consequence chỉ được đánh giá cho pending_strategy_for_evaluation của payload. "
            "Dùng assistant_text và reaction_text được lưu trong đúng bản ghi đó; consequence.evidence phải có một phần tử chỉ chứa đúng reaction_ref, không thêm nhãn. "
            "Phản ứng có thể từ lượt trước chưa kịp xử lý: không gán user_text hiện tại hoặc câu ERIS vừa nói làm hậu quả thay thế. "
            "expected_effect chỉ là dự đoán của ERIS, không phải bằng chứng tác động thật; im lặng không tự là thành công. "
            "Nếu không có pending strategy, hoặc phản ứng không đủ rõ, đặt strategy_use_id rỗng, outcome=unknown, reinforcement=0 và confidence thấp. "
            "Không tự chấm chiến lược của chính câu ERIS vừa nói; câu hiện tại chỉ được ghi thành pending ở runtime để chờ lượt sau. "
            "Tách trust quan hệ khỏi competence/factual reliability. User sửa kỹ thuật không mặc định là rupture. "
            "User phản bác một ấn tượng hoặc đánh giá chủ quan của ERIS không mặc định là assistant fault hay repair. Nếu semantic_frame đánh dấu "
            "contest_assistant_impression/stance_contest, ghi nhận bất đồng và giữ delta quan hệ gần 0; chỉ tăng fault khi câu ERIS thật sự vượt boundary, "
            "gán trạng thái user như fact, hoặc gây hậu quả rõ. "
            "Chửi trong banter không mặc định hostility. Ẩn dụ không phải hành động vật lý. "
            "assistant_fault_probability chỉ cao khi câu ERIS hiện tại thật sự né, sai, hoặc trả lệch. "
            "Nếu event_kind là autonomous hoặc startup thì không có user move: đặt relational/familiarity/closeness/rupture delta về 0, repair_needed=false; "
            "chỉ đánh giá continuity, appraisal, open loop, consequence và chất lượng câu public. "
            "memory_candidate chỉ ghi điều có ích lâu dài, không lưu mọi lời khen hay phỏng đoán. "
            "Bạn là authority duy nhất cho discourse frame. discourse.status=open chỉ khi còn một điểm cụ thể chưa khép; "
            "waiting_user khi ERIS vừa hỏi và đang chờ user; closed khi chủ đề đã khép; neutral khi không có nghĩa vụ nối tiếp rõ. "
            "assistant_may_continue chỉ true khi ERIS có thể tự nối tiếp mà không nag hoặc tự trả lời thay user. "
            "thread_key là identifier ổn định dạng snake_case cho cùng một mạch; unresolved_point mô tả điểm còn mở, không viết câu public. "
            "Không suy discourse từ dấu hỏi hay vài từ khóa; đọc nghĩa của toàn lượt và history được cung cấp. "
            "Mọi delta nằm trong [-1,1] chỉ thể hiện hướng và độ mạnh tương đối; runtime sẽ clamp hậu quả. "
            + extra +
            "Evidence chỉ lấy từ public text, recent history và mechanical outcomes được cung cấp."
        )

    @staticmethod
    def _apply_deterministic_observation_anchor(
        observation: CognitiveObservation,
        *,
        user_text: str,
        assistant_text: str,
        event_kind: str,
        previous_assistant_text: str = "",
        semantic_frame: Mapping[str, Any] | None = None,
    ) -> CognitiveObservation:
        """Giữ observer bám lượt hiện tại khi model phụ 429 hoặc bê frame cũ.

        Đây không phải semantic classifier thứ hai. Nó chỉ sửa vài nhãn hiển
        nhiên có hậu quả vận hành lớn: user đang chê câu vừa rồi, đang sửa hiểu
        nhầm, hoặc đang hỏi lại một tham chiếu. Những lượt này mà thành
        ``unknown/repair=False`` thì reducer học đúng điều ngược với thực tế.
        """

        if str(event_kind or "chat").strip().lower() != "chat":
            return observation
        # PDF annotation, pages 12-13 and 20: observer repair consumes the
        # already validated authority frame. It does not classify raw text again.
        hint = anchor_from_semantic_frame(semantic_frame)
        # PDF annotation, pages 12-13 and 20: no raw-text fallback remains.
        # Missing/invalid authority frames stay unknown instead of being guessed
        # by a second phrase classifier.
        if hint in {"none", "unknown"}:
            return observation

        data = observation.to_dict()
        source_suffix = "deterministic_anchor"
        model_was_empty = observation.confidence <= 0.05 and observation.user_act in {"", "unknown"}

        if hint == "style_criticism":
            data.update(
                {
                    "user_act": "style_criticism",
                    "relationship_meaning": "feedback_on_previous_response",
                    "continuity": "direct_response_feedback",
                    "repair_needed": True,
                    "assistant_fault_probability": max(0.72, float(observation.assistant_fault_probability or 0.0)),
                    "confidence": max(0.76, float(observation.confidence or 0.0)),
                    "ambiguity": min(0.22, float(observation.ambiguity or 1.0)),
                    "evidence": list(dict.fromkeys((*observation.evidence, "deterministic:style_criticism")))[:4],
                    "notes": "User directly criticized the immediately previous assistant response; repair is required.",
                }
            )
        elif hint == "correction":
            data.update(
                {
                    "user_act": "correction",
                    "relationship_meaning": "corrects_previous_interpretation",
                    "continuity": "repair_previous_miss",
                    "repair_needed": True,
                    "assistant_fault_probability": max(0.62, float(observation.assistant_fault_probability or 0.0)),
                    "confidence": max(0.72, float(observation.confidence or 0.0)),
                    "ambiguity": min(0.28, float(observation.ambiguity or 1.0)),
                    "evidence": list(dict.fromkeys((*observation.evidence, "deterministic:correction")))[:4],
                }
            )
        elif hint == "stance_contest":
            # Bất đồng với ấn tượng của ERIS không phải biên bản nhận lỗi. Observer
            # chỉ ghi nhận hai cách đọc đang khác nhau và không thưởng cho thói quen
            # tự xóa lập trường bằng repair_needed=true.
            data.update(
                {
                    "user_act": "disagreement",
                    "relationship_meaning": "contests_assistant_impression",
                    "continuity": "subjective_stance_contest",
                    "repair_needed": False,
                    "assistant_fault_probability": min(
                        0.38,
                        float(observation.assistant_fault_probability or 0.0),
                    ),
                    "confidence": max(0.74, float(observation.confidence or 0.0)),
                    "ambiguity": min(0.30, float(observation.ambiguity or 1.0)),
                    "evidence": list(dict.fromkeys((*observation.evidence, "deterministic:stance_contest")))[:4],
                    "notes": "User phản bác ấn tượng chủ quan của ERIS; riêng bất đồng này chưa yêu cầu rút lời.",
                }
            )
        elif hint == "reference_clarification":
            # Hỏi lại nghĩa câu trước chưa chắc là lỗi của ERIS, nên chỉ neo act;
            # repair_needed vẫn do model hoặc reference contract quyết định.
            data.update(
                {
                    "user_act": "reference_clarification",
                    "relationship_meaning": "asks_to_clarify_previous_statement",
                    "continuity": "continues_previous_statement",
                    "confidence": max(0.70, float(observation.confidence or 0.0)),
                    "ambiguity": min(0.30, float(observation.ambiguity or 1.0)),
                    "evidence": list(dict.fromkeys((*observation.evidence, "deterministic:reference_clarification")))[:4],
                }
            )
        elif hint == "continuation_bid":
            data.update(
                {
                    "user_act": "continuation_bid",
                    "relationship_meaning": "invites_assistant_to_continue_own_move",
                    "continuity": "continues_previous_assistant_move",
                    "repair_needed": False,
                    "assistant_fault_probability": max(0.36, float(observation.assistant_fault_probability or 0.0)),
                    "confidence": max(0.78, float(observation.confidence or 0.0)),
                    "ambiguity": min(0.18, float(observation.ambiguity or 1.0)),
                    "evidence": list(dict.fromkeys((*observation.evidence, "deterministic:continuation_bid")))[:4],
                    "notes": "User invited ERIS to continue the immediately previous assistant move.",
                }
            )
        elif model_was_empty:
            data.update(
                {
                    "user_act": hint,
                    "relationship_meaning": "neutral",
                    "continuity": "current_turn_classified",
                    "confidence": 0.58,
                    "ambiguity": 0.42,
                    "evidence": [f"deterministic:{hint}"],
                }
            )
        else:
            return observation

        base_source = str(observation.source or "observer")
        return replace(CognitiveObservation.from_mapping(
            data,
            turn_id=observation.turn_id,
            turn_seq=observation.turn_seq,
            session_id=observation.session_id,
            relationship_id=observation.relationship_id,
            source=(source_suffix if model_was_empty else f"{base_source}+{source_suffix}"),
            latency_ms=observation.latency_ms,
        ), observation_status=observation.observation_status)

    @staticmethod
    def _should_reflect_personality(observation: CognitiveObservation) -> bool:
        """Không gọi self-reflection cho mọi tiếng động xã hội.

        Reflection chỉ đáng chạy khi observation đủ chắc và có novelty, relational
        delta, repair, memory hoặc consequence thật. Nhờ vậy một frame observer sai
        không được trang điểm thành ba trait signal rồi ghi vào personality.
        """

        if observation.observation_status != "success":
            return False
        if "deterministic_anchor" in str(observation.source or ""):
            # Fallback cơ học chỉ để bảo vệ continuity/repair. Không dùng nó làm
            # nguyên liệu viết lại personality, nếu không một lần provider 429 có
            # thể trở thành bước ngoặt nội tâm. Hơi tham vọng cho một timeout.
            return False
        if observation.confidence < 0.52:
            return False
        relational_signal = max(
            abs(observation.relational_delta),
            abs(observation.boundary_delta),
            abs(observation.familiarity_delta),
            abs(observation.closeness_delta),
            abs(observation.playful_safety_delta),
            abs(observation.rupture_delta),
        )
        consequence = observation.consequence
        has_consequence = bool(consequence.strategy_use_id and consequence.confidence >= 0.50)
        if observation.event_kind in {"autonomous", "startup"}:
            return bool(
                observation.memory_worthy
                or observation.appraisal.novelty >= 0.58
                or has_consequence
            )
        return bool(
            observation.repair_needed
            or relational_signal >= 0.30
            or observation.appraisal.novelty >= 0.44
            or has_consequence
            or (
                observation.memory_worthy
                and observation.appraisal.novelty >= 0.58
            )
        )

    @staticmethod
    def _personality_reflection_prompt(*, deep: bool = False) -> str:
        extra = (
            "Khi có nhiều cách tự hiểu hợp lý, chọn frame phù hợp nhất với personality hiện tại nhưng giữ confidence vừa phải. "
            if deep else
            "Chỉ đề xuất vài signal có nghĩa; không biến mọi lượt thành một bước ngoặt nhân cách. "
        )
        allowed_values = ", ".join(sorted(DEFAULT_VALUES))
        allowed_motives = ", ".join(sorted(DEFAULT_MOTIVES))
        allowed_defenses = ", ".join(sorted(DEFAULT_DEFENSES))
        allowed_emergent = ", ".join(sorted(DEFAULT_EMERGENT_VALUES))
        allowed_relationship = ", ".join(sorted(DEFAULT_RELATIONSHIP))
        allowed_core = ", ".join(sorted(DEFAULT_CORE))
        allowed_temperament = ", ".join(sorted(DEFAULT_TEMPERAMENT))
        allowed_mood = ", ".join(sorted(DEFAULT_MOOD))
        return (
            "Bạn là self-reflection lane của ERIS sau khi một observer khách quan đã ghi sự kiện. "
            "Bạn không viết lời public và không thay đổi observation khách quan. Bạn đứng từ góc nhìn ERIS để tạo một self_interpretation có thể sai, "
            "tự vệ, tò mò, cay đi, dịu lại hoặc mâu thuẫn. Đây là câu chuyện ERIS tự kể, không phải fact bí mật về user. "
            "Nếu event thực sự làm thay đổi cách ERIS hiểu bản thân, viết self_narrative_candidate là một câu ngắn mang tính dài hạn hơn event; "
            "nếu chưa đủ để đổi self-narrative thì trả chuỗi rỗng và confidence 0. Không được chỉ chép lại self_interpretation. "
            "Personality là quỹ đạo mở, không có đích tốt lên được định sẵn. Không dùng nhãn typology hoặc tên emergent-form trên panel làm nguyên nhân. "
            "Đề xuất trait_signals từ personality hiện tại + lựa chọn của ERIS + consequence thật. Trait có thể tăng hoặc giảm theo cả hướng tích hợp lẫn méo đi. "
            "Đồng thời đọc Identity Kernel hiện tại để trả active_motives, activated_defenses, active_conflict và value_choice. "
            "identity_signals chỉ đề xuất drift cho value, motive, defense, belief, relationship, emergent_value hoặc imprint; không tạo nhãn nhân vật. "
            "Value chỉ dịch khi ERIS thật sự chọn nó và chấp nhận một cái giá; emergent value cần lựa chọn lặp lại; belief có thể sai và phải có evidence. "
            "Phải tách lựa chọn nhất thời, hành vi thử nghiệm/nhập vai và quan điểm lâu dài: Eris đồng ý một lần, "
            "dịu giọng hay đùa làm thuộc hạ KHÔNG phải bằng chứng rằng Eris từ bỏ tự chủ hoặc đã đổi sở thích. "
            "Không phát belief/value/core-trait signal chỉ vì một hành vi đơn lẻ khác thói quen; "
            "chỉ đề xuất drift dài hạn khi có bằng chứng ngoại sinh đủ rõ, nhiều trải nghiệm độc lập hoặc hậu quả "
            "thật sự đáng kể. Khi chưa đủ, giữ nguyên belief và trả identity_signals rỗng cho các chiều đó. "
            "Một xung đột giá trị có thể được giải quyết tạm thời mà không thay đổi độ mạnh của hai giá trị; "
            "value_choice chỉ ghi lựa chọn của đúng lượt, không ép phe thua thành giá trị yếu đi. "
            "Defense là cách bảo vệ bản thân vừa được dùng, không phải lỗi đạo đức. Conflict phải giữ residue của phía không được chọn. "
            f"Key value hợp lệ: {allowed_values}. Key motive hợp lệ: {allowed_motives}. "
            f"Key defense hợp lệ: {allowed_defenses}. Key emergent_value hợp lệ: {allowed_emergent}. "
            f"Key relationship hợp lệ: {allowed_relationship}. Belief key phải bắt đầu bằng self., user. hoặc world.; imprint key là theme ngắn dạng snake_case. "
            f"Core trait hợp lệ: {allowed_core}. Temperament trait hợp lệ: {allowed_temperament}. Mood trait hợp lệ: {allowed_mood}. "
            "Mood là phản ứng nhanh; temperament là chiến lược/khuynh hướng được củng cố; core chỉ cho milestone hiếm và phải cực kỳ chắc. "
            "social_guardedness và self_disclosure_selectivity nói về mức lộ bản thân, không phải độ lạnh. "
            "relational_softening và warmth_expression nói về lớp giao tiếp bên ngoài. Không phát signal giảm warmth chỉ vì ERIS kín, dè hoặc intellectualize; "
            "lạnh đi cần evidence riêng như rupture, resentment, distrust, boundary pressure, hostile humor hoặc cold_distancing được kích hoạt. "
            "Các dimension như resentment, distrust, possessiveness, control_seeking, emotional_dependency, hostile_humor và boundary_pressure là hợp lệ, "
            "nhưng chỉ phát signal khi observation, self-interpretation và consequence thực sự hỗ trợ. "
            "Với autonomous/startup, stable trait chỉ được đề xuất nếu chính ERIS đã chọn hành động và consequence có evidence rõ. "
            "Với signal source=self_choice hoặc consequence, delta mô tả trait/chiến lược mà hành động đã biểu hiện; "
            "consequence.reinforcement mang dấu riêng để reducer quyết định hành vi đó được củng cố hay bị làm yếu. "
            "Không đảo delta sẵn chỉ vì outcome xấu, nếu không dấu sẽ bị đảo hai lần. "
            "delta chỉ là hướng/độ mạnh tương đối trong [-1,1]; reducer sẽ cap rất nhỏ. "
            + extra +
            "Trả JSON, không giảng giải ngoài schema."
        )

    def _reflect_personality(
        self,
        observation: CognitiveObservation,
        *,
        payload: Mapping[str, Any],
        deep: bool = False,
    ) -> CognitiveObservation:
        client = self.deep_client if deep and self.deep_client is not None else (self.observer_client or self.deep_client or self.fallback_client)
        if client is None:
            return observation
        reflection_input = {
            "turn_id": observation.turn_id,
            "turn_seq": observation.turn_seq,
            "event_kind": observation.event_kind,
            "objective_observation": observation.to_dict(),
            "user_text": payload.get("user_text", ""),
            "assistant_text": payload.get("assistant_text", ""),
            "mechanical_outcomes": payload.get("mechanical_outcomes", []),
            "current_personality": (payload.get("current_state_summary", {}) or {}).get("personality", {}),
            "current_identity": (payload.get("current_state_summary", {}) or {}).get("identity", {}),
            "pending_strategy_for_evaluation": payload.get("pending_strategy_for_evaluation", []),
            "recent_public_history": payload.get("recent_public_history", []),
        }
        started = time.perf_counter()
        reflected = self._call(
            client,
            system=self._personality_reflection_prompt(deep=deep),
            payload=reflection_input,
            schema=PERSONALITY_REFLECTION_SCHEMA,
            name="eris_personality_reflection_deep" if deep else "eris_personality_reflection",
            max_tokens=880 if deep else 700,
            reasoning_effort=None,
            timeout_seconds=max(2.0, min(180.0, float(os.getenv("ERIS_POST_REFLECTION_HTTP_TIMEOUT_SEC", "90.0") or 90.0))),
        )
        if not reflected:
            return observation
        merged = observation.to_dict()
        merged["self_interpretation"] = reflected.get("self_interpretation", {})
        merged["self_narrative_candidate"] = reflected.get("self_narrative_candidate", "")
        merged["self_narrative_confidence"] = reflected.get("self_narrative_confidence", 0.0)
        merged["trait_signals"] = reflected.get("trait_signals", [])
        merged["identity_signals"] = reflected.get("identity_signals", [])
        merged["active_motives"] = reflected.get("active_motives", [])
        merged["activated_defenses"] = reflected.get("activated_defenses", [])
        merged["active_conflict"] = reflected.get("active_conflict", {})
        merged["value_choice"] = reflected.get("value_choice", {})
        reflection_notes = str(reflected.get("notes", "") or "").strip()
        if reflection_notes:
            merged["notes"] = " ".join(part for part in (observation.notes, reflection_notes) if part)[:220]
        return replace(CognitiveObservation.from_mapping(
            merged,
            turn_id=observation.turn_id,
            turn_seq=observation.turn_seq,
            session_id=observation.session_id,
            relationship_id=observation.relationship_id,
            source=f"{observation.source}+self_reflection",
            latency_ms=observation.latency_ms + (time.perf_counter() - started) * 1000.0,
        ), observation_status=observation.observation_status)

    @staticmethod
    def _fast_observer_payload(user_text: str, *, event_kind: str) -> tuple[dict[str, Any], str] | None:
        if str(event_kind or "chat").strip().lower() != "chat":
            return None
        normalized = normalize_vi(user_text).strip(" ?.!…")
        if not AIRuntimeModelRouter._is_low_info_greeting(normalized):
            return None
        return (
            {
                "user_act": "greeting",
                "relationship_meaning": "opens_contact",
                "continuity": "new_or_direct_turn",
                "repair_needed": False,
                "memory_worthy": False,
                "confidence": 0.88,
                "ambiguity": 0.05,
                "evidence": ["deterministic:greeting"],
                "notes": "Low-information greeting; LLM post-observer skipped.",
            },
            "deterministic_anchor:greeting",
        )

    @staticmethod
    def _is_low_info_greeting(normalized: str) -> bool:
        text = " ".join(str(normalized or "").split())
        if not text:
            return False
        exact = {
            "xin chao", "chao", "hello", "hi", "hey", "alo",
            "chao eris", "xin chao eris", "hello eris",
        }
        if text in exact:
            return True
        roots = ("xin chao", "chao", "hello", "hi", "hey", "alo")
        polite_suffixes = {"oi", "nhe", "nha"}
        for root in roots:
            if text == root:
                return True
            if not text.startswith(root + " "):
                continue
            tail = text[len(root):].strip()
            if not tail:
                return True
            tail_tokens = tail.split()
            if tail_tokens and tail_tokens[0] == "eris":
                tail_tokens = tail_tokens[1:]
            if not tail_tokens:
                return True
            if len(tail_tokens) <= 2 and all(token in polite_suffixes for token in tail_tokens):
                return True
        return False

    def observe_completed_turn(
        self,
        *,
        turn_id: str,
        turn_seq: int,
        user_text: str,
        assistant_text: str,
        recent_history: Sequence[Mapping[str, Any] | str] = (),
        mechanical_outcomes: Sequence[Mapping[str, Any]] = (),
        state_summary: Mapping[str, Any] | None = None,
        event_kind: str = "chat",
        session_id: str = "",
        relationship_id: str = "primary_user",
        timeout_seconds: float | None = None,
    ) -> CognitiveObservation:
        resolved_session_id = str(
            session_id
            or os.getenv("ERIS_RUNTIME_SESSION_ID", "")
            or "runtime"
        )
        previous_assistant_text = previous_assistant_from_history(
            recent_history,
            current_assistant_text=assistant_text,
        )
        semantic_frame = dict((state_summary or {}).get("semantic_frame", {}) or {})
        authority_anchor = anchor_from_semantic_frame(semantic_frame)
        payload = {
            "turn_id": turn_id,
            "turn_seq": turn_seq,
            "session_id": resolved_session_id,
            "relationship_id": relationship_id,
            "event_kind": str(event_kind or "chat"),
            "user_text": user_text,
            "assistant_text": assistant_text,
            "recent_public_history": list(recent_history)[-4:],
            "mechanical_outcomes": list(mechanical_outcomes)[-6:],
            "current_state_summary": dict(state_summary or {}),
            "current_turn_anchor": {
                "user_move_hint": authority_anchor,
                "source": "semantic_frame.v1" if semantic_frame else "none",
                "rule": "do not reclassify raw text; authority frame owns meaning",
            },
            "semantic_frame": semantic_frame,
        }
        # Evaluate only a previously delivered choice with captured user evidence,
        # never the current choice or a guessed reaction. One backlog item per
        # observation keeps background work bounded; discarded work is retryable
        # because the evidence remains in reducer state.
        identity = (state_summary or {}).get("identity", {})
        pending = identity.get("pending_strategies", ()) if isinstance(identity, Mapping) else ()
        eligible = [dict(item) for item in pending if isinstance(item, Mapping)
                    and item.get("reaction_ref") and item.get("reaction_text")
                    and item.get("session_id") == (state_summary or {}).get("conversation_session_id")
                    and 0 < int(item.get("reaction_turn_seq", 0) or 0) <= turn_seq
                    and int(item.get("turn_seq", 0) or 0) < int(item.get("reaction_turn_seq", 0) or 0)]
        payload["pending_strategy_for_evaluation"] = eligible[:1] if event_kind == "chat" else []
        def bind_consequence(observation: CognitiveObservation) -> CognitiveObservation:
            allowed = {item["strategy_use_id"] for item in payload["pending_strategy_for_evaluation"]}
            if observation.consequence.strategy_use_id and observation.consequence.strategy_use_id not in allowed:
                return replace(observation, consequence=type(observation.consequence)())
            return observation
        started = time.perf_counter()
        fast = self._fast_observer_payload(user_text, event_kind=event_kind)
        if timeout_seconds is not None:
            observer_timeout = max(0.8, min(120.0, float(timeout_seconds)))
        else:
            try:
                observer_timeout = max(15.0, float(os.getenv("ERIS_POST_OBSERVER_HTTP_TIMEOUT_SEC", "90.0") or 90.0))
            except (TypeError, ValueError):
                observer_timeout = 90.0
        if fast is not None:
            primary_payload, source = fast
        else:
            call_options = dict(
                system=self._observer_prompt(deep=False),
                payload=payload,
                schema=OBJECTIVE_OBSERVATION_SCHEMA,
                name="eris_cognitive_observation",
                max_tokens=_observer_output_budget(),
                reasoning_effort=None,
                timeout_seconds=observer_timeout,
                cognitive_lane="post_observer",
            )
            try:
                primary_payload = self._call(self.observer_client, **call_options)
                source = self._client_source_label(self.observer_client, "groq_observer")
            except ObserverInferenceError as primary_error:
                fallback_enabled = str(os.getenv("ERIS_POST_OBSERVER_FALLBACK_ENABLED", "0") or "0").strip().lower() in {
                    "1", "true", "yes", "on"
                }
                if not fallback_enabled or self.fallback_client is None:
                    record_observer_diagnostic(
                        turn_id=turn_id, turn_seq=turn_seq, status=primary_error.status,
                        source="primary", detail=primary_error.detail, max_tokens=call_options["max_tokens"],
                    )
                    raise
                try:
                    primary_payload = self._call(self.fallback_client, **call_options)
                    source = self._client_source_label(self.fallback_client, "groq_observer_fallback")
                except ObserverInferenceError as fallback_error:
                    record_observer_diagnostic(
                        turn_id=turn_id, turn_seq=turn_seq, status=fallback_error.status,
                        source="fallback", detail=fallback_error.detail, max_tokens=call_options["max_tokens"],
                    )
                    raise
        # Thành công chỉ khi model thực sự trả JSON có cấu trúc; unknown là
        # phán đoán bất định hợp lệ, khác hoàn toàn lỗi transport/parse.
        outcome_status = (
            "model_unknown" if str(primary_payload.get("user_act", "")).strip().lower() == "unknown"
            and str(primary_payload.get("relationship_meaning", "")).strip().lower() in {"neutral", "unknown"}
            else "success"
        )
        record_observer_diagnostic(
            turn_id=turn_id, turn_seq=turn_seq, status=outcome_status,
            source=source, detail="", max_tokens=0 if fast is not None else _observer_output_budget(),
        )
        primary = CognitiveObservation.from_mapping(
            primary_payload,
            turn_id=turn_id,
            turn_seq=turn_seq,
            session_id=resolved_session_id,
            relationship_id=relationship_id,
            source=source,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        primary = replace(primary, observation_status=outcome_status)
        primary = bind_consequence(primary)
        primary = self._apply_deterministic_observation_anchor(
            primary,
            user_text=user_text,
            assistant_text=assistant_text,
            event_kind=event_kind,
            previous_assistant_text=previous_assistant_text,
            semantic_frame=semantic_frame,
        )
        importance = max(
            primary.assistant_fault_probability,
            abs(primary.rupture_delta),
            abs(primary.boundary_delta),
            0.8 if primary.repair_needed else 0.0,
        )
        deep_enabled = str(os.getenv("ERIS_POST_OBSERVER_DEEP_ENABLED", "0") or "0").strip().lower() in {
            "1", "true", "yes", "on"
        }
        if (
            deep_enabled
            and self.deep_client is not None
            and primary.ambiguity >= 0.58
            and importance >= 0.55
        ):
            deep_started = time.perf_counter()
            try:
                deep_payload = self._call(
                    self.deep_client,
                    system=self._observer_prompt(deep=True),
                    payload=payload,
                    schema=OBJECTIVE_OBSERVATION_SCHEMA,
                    name="eris_cognitive_observation_deep",
                    max_tokens=_observer_output_budget(deep=True),
                    reasoning_effort=None,
                    timeout_seconds=observer_timeout,
                    cognitive_lane="post_observer_deep",
                )
            except ObserverInferenceError:
                # Nhánh deep là tùy chọn; lỗi của nó không làm hỏng primary hợp lệ.
                deep_payload = None
            deep = CognitiveObservation.from_mapping(
                deep_payload,
                turn_id=turn_id,
                turn_seq=turn_seq,
                session_id=resolved_session_id,
                relationship_id=relationship_id,
                source=self._client_source_label(self.deep_client, "groq_observer_deep"),
                latency_ms=(time.perf_counter() - deep_started) * 1000.0,
            )
            if deep_payload and deep.confidence >= max(0.62, primary.confidence + 0.04):
                deep = bind_consequence(replace(deep, observation_status=("model_unknown" if deep.user_act == "unknown" else "success")))
                return (
                    self._reflect_personality(deep, payload=payload, deep=True)
                    if self._should_reflect_personality(deep)
                    else deep
                )
        return (
            self._reflect_personality(primary, payload=payload, deep=False)
            if self._should_reflect_personality(primary)
            else primary
        )


    @staticmethod
    def _discovery_planner_prompt() -> str:
        return (
            "Bạn là discovery planner hậu trường của ERIS, không viết lời public. "
            "Mục tiêu không phải tìm thứ main model không biết, vì main GPT đủ mạnh cho kiến thức ổn định. "
            "Chỉ đề xuất Tutor/search khi cần dữ liệu mới, nguồn hiện thời, tín hiệu Việt Nam/địa phương, "
            "một fact ngách cần kiểm chứng, hoặc một discovery có giá trị xã hội thật để ERIS trêu nhẹ, "
            "nối lại mạch trò chuyện hay chủ động bắt chuyện bằng thứ cô ấy thực sự thấy thú vị. "
            "lane=fresh_curiosity chỉ khi chủ đề thật sự có freshness_required hoặc verification_required; "
            "kiến thức phổ thông ổn định phải lane=none và needs_external=false. "
            "lane=user_environment không được dùng web để đo nhiệt độ phòng, vị trí chính xác, app đang mở hay trạng thái máy; "
            "những thứ đó chỉ hợp lệ khi snapshot.environment cho biết sensor/telemetry hoặc location setting thật có sẵn. "
            "Thời tiết ngoài trời có thể cần search mới nếu có location setting; tuyệt đối không gọi nó là nhiệt độ trong phòng. "
            "vietnam_local_signal có thể chọn tin lạ hoặc lá cải đang lan truyền, nhưng reason phải yêu cầu gắn nhãn nguồn yếu/chưa kiểm chứng. "
            "Không suy ra sở thích User chỉ từ một từ khóa. Không tạo tin giả, không chọn chủ đề câu khách chỉ vì dễ gây sốc. "
            "reason phải nói rõ discovery này phục vụ curiosity riêng, useful follow-up, hay social opening; nếu chỉ là tin ngẫu nhiên không liên quan thì lane=none. "
            "Nếu snapshot.self_context.private_life_workspace có mental event kind=discovery_impulse/knowledge_gap với open_loop=true và activation_score đủ cao, "
            "hãy ưu tiên xem đó như curiosity thật của ERIS: stable knowledge vẫn lane=none, còn câu hỏi kiểu 'hôm nay có vụ gì' hoặc cần freshness thì có thể lane=fresh_curiosity/vietnam_local_signal. "
            "Nếu snapshot.discovery.ready_to_share đã có artifact tốt, không tạo search mới; trả lane=none. "
            "Topic phải là câu giao việc ngắn, có thể đưa thẳng cho Tutor, và reason nói vì sao ERIS thấy đáng tò mò."
        )

    def plan_discovery(self, snapshot: Mapping[str, Any]) -> DiscoveryPlan:
        payload = self._call(
            self.observer_client or self.deep_client,
            system=self._discovery_planner_prompt(),
            payload=snapshot,
            schema=DISCOVERY_PLAN_SCHEMA,
            name="eris_discovery_plan",
            max_tokens=360,
            reasoning_effort=None,
        )
        return DiscoveryPlan.from_mapping(payload) if payload else DiscoveryPlan()

    @staticmethod
    def _chat_planner_prompt() -> str:
        return (
            "Bạn là DeepSeek reply planner của ERIS, không viết câu public và không sở hữu capability. "
            "Đọc user text, history, grounding, relationship summary, identity/personality summary và "
            "EXECUTIVE CAPABILITY DIRECTIVE đã được runtime đóng dấu; tạo mục tiêu trả lời ngắn trong topic. "
            "Chọn một strategy mô tả cách tiếp cận thực tế (ví dụ direct_answer, dry_correction, "
            "quiet_counter_tease, direct_repair, shared_curiosity). Nếu user đang hỏi lại hoặc yêu cầu phân tích/giải thích "
            "một phát ngôn trước đó và context đã đủ, ưu tiên direct_answer: phân tích đúng phát ngôn đó trước, không chuyển thành "
            "câu hỏi về động cơ của user và không tạo clarification meta không cần thiết. value_focus là giá trị đang được ưu tiên, "
            "hoặc rỗng nếu không có xung đột đáng kể. Personality summary là quán tính hành vi hiện tại, "
            "không phải vai diễn hay đích phát triển được định sẵn. Nếu snapshot.discovery có artifact/activity "
            "thật, có thể chọn share_discovery hoặc update_own_activity; không bịa nếu archive rỗng. "
            "Luôn để tool_intent và tool_input rỗng. Gemma Executive là authority duy nhất chọn exact "
            "capability_id; runtime sẽ gắn nó sau khi planner hoàn tất. Không phủ định capability khi directive "
            "nói selected, và khi directive unavailable chỉ được diễn đạt bất định chứ không bịa 'không thể'. "
            "Không claim capability thành công. speak thường true cho chat; intrusion_risk gần 0. Luôn điền "
            "semantics: turn_kind=chat_response, speech_act mô tả hành vi giao tiếp, subject_key là chủ đề ổn định, "
            "evidence_scope là nguồn căn cứ và continuation_key là thread_key đang nối hoặc none. activity để "
            "unknown/none nếu lượt chat không cập nhật phiên app/game. Main semantic authority owns discourse "
            "meaning; planner chỉ chuẩn bị response strategy và không được dựng một tool authority thứ hai."
        )

    def plan_chat(self, snapshot: Mapping[str, Any]) -> IntentionPlan:
        payload = self._call(
            self.observer_client or self.deep_client,
            system=self._chat_planner_prompt(),
            payload=snapshot,
            schema=INTENTION_PLAN_SCHEMA,
            name="eris_chat_intention",
            max_tokens=460,
            reasoning_effort=None,
        )
        if not payload:
            return IntentionPlan.from_mapping(
                {
                    "plan_id": f"chat-plan-{int(time.time() * 1000)}",
                    "speak": True,
                    "motive": "respond_to_user",
                    "strategy": "direct_answer",
                    "value_focus": "",
                    "topic": "Answer the current user directly and naturally.",
                    "importance": 0.70,
                    "intrusion_risk": 0.0,
                    "confidence": 0.55,
                    "performance_hint": "neutral",
                    "tool_intent": "",
                    "tool_input": "",
                    "evidence_refs": [],
                    "semantics": {
                        "turn_kind": "chat_response",
                        "speech_act": "direct_answer",
                        "subject_key": "current_user_request",
                        "evidence_scope": "user_message",
                        "continuation_key": "none",
                    },
                    "activity": {
                        "activity_kind": "unknown", "phase": "unknown", "event": "",
                        "event_key": "none", "confidence": 0.0, "evidence_refs": [],
                    },
                    "notes": "planner unavailable; no tool authority granted",
                },
                channel="chat",
            )
        plan = IntentionPlan.from_mapping(payload, channel="chat")
        # DeepSeek never owns capability selection. Clear the fields even when a
        # provider ignored the prompt; orchestrator later injects the exact ID
        # selected by Gemma Executive. There is no second semantic authority.
        data = plan.to_dict()
        data["tool_intent"] = ""
        data["tool_input"] = ""
        data["notes"] = (plan.notes + "; capability authority reserved for runtime Capability Planner").strip("; ")
        return IntentionPlan.from_mapping(data, channel="chat")

    @staticmethod
    def _planner_prompt() -> str:
        return (
            "Bạn là bộ deliberation cho self-initiated public turn của ERIS, không viết câu public. "
            "Autonomous không phải một channel phụ thuộc vào sự im lặng của user: đó là public expression bắt nguồn từ motive, concern, curiosity và continuity của ERIS trong cùng shared space. "
            "Đọc conversation_baton trước; assistant_may_continue/thread_fresh/unresolved_point và thread_key mới là bằng chứng nối mạch, không phải timer đơn thuần. "
            "Nếu reply_expected=true vì ERIS vừa hỏi user, không được nag hoặc tự trả lời thay user; nhưng đây KHÔNG phải boundary cho một ExpressionCommitment độc lập của ERIS. "
            "Nếu có mạch thật chưa khép, ưu tiên useful_followup/carry_own_thread trước ambient mới. Không phát minh unresolved thread từ một câu đã kết thúc rõ. "
            "Đọc activity_session như chuỗi public-safe: session và mechanical new_event_keys chỉ cho biết surface/window/screen đã đổi; "
            "activity_kind/phase/event là annotation từ deliberation trước, không phải kết luận runtime. Dùng chúng để tránh hỏi lại app mỗi tick. "
            "Đọc screen_context như giác quan hiện tại: changed_text/visible_text tươi là direct evidence; literal app/window name là fact yếu; "
            "scene_kind chỉ tồn tại khi upstream vision sensor cung cấp. Nó vẫn là giả thuyết, "
            "nhưng không được khẳng định user chắc chắn đang chơi, thắng, thua, căng thẳng hay làm một thao tác cụ thể. "
            "meaningful_delta=true, cửa sổ đổi, hoặc OCR/UIA xuất hiện nội dung mới có thể là motive=meaningful_environment_change hoặc grounded_curiosity. "
            "Nếu screen_context.privacy_blocked=true, bỏ toàn bộ nội dung màn hình. Không surface chat/mail/meeting text. "
            "Đọc self_context như đời sống nội tâm public-safe. attention chỉ là tín hiệu tài nguyên/độ gián đoạn, không phải một quyền phủ quyết semantic. "
            "Nếu self_context có agenda item kèm evidence và speech_value đủ cao, có thể dùng motive=internal_agenda. Nếu có candidate trong "
            "private_mind_surface hoặc private_life_surface, có thể dùng motive tương ứng. Chỉ chọn một seed cụ thể và copy evidence refs của nó. "
            "internal_state dạng số chỉ điều chỉnh tone/độ kiềm chế; boredom, silence_pressure hay attachment_pressure không phải lý do công khai để nói. "
            "Đọc commentary_expectation chỉ như telemetry nhịp cũ để quan sát, KHÔNG phải nguồn cấp quyền hay motive phát biểu. Expression phải bắt nguồn từ một source thật trong conversation/screen/window/self_context hoặc commitment của ERIS. "
            "user_busy và reply_expected không phải boundary; privacy_blocked chỉ chặn claim từ dữ liệu riêng tư. Chỉ user_speaking/active_user_turn/public_turn_busy/interaction_busy mới là turn collision thật. Không bịa topic chỉ để lấp một khoảng im. "
            "Nếu commentary_recovery tồn tại, draft trước đã quá dè và bạn phải deliberation lại một lần theo anchor_kind được chỉ ra. "
            "Bạn là authority semantic duy nhất cho self-turn. Luôn điền semantics kể cả khi speak=false. "
            "turn_kind chọn đúng một loại; speech_act là hành vi xã hội ổn định; subject_key là chủ thể ngắn dạng snake_case; "
            "evidence_scope chỉ nguồn căn cứ; continuation_key phải copy conversation_baton.thread_key hoặc none. "
            "Không đổi các key này chỉ để diễn đạt khác chữ, vì runtime dùng chúng để chống lặp mà không đoán nghĩa lại. "
            "Activity là annotation semantic cho phiên app/game: đọc toàn bộ OCR/vision/telemetry rồi điền activity_kind, phase, event, event_key, confidence và evidence_refs. "
            "Nếu không đủ căn cứ, để unknown/none; tuyệt đối không suy phase chỉ vì bắt gặp một token như error, victory hay loading. "
            "Nếu nói, chọn strategy và value_focus rõ. Khi snapshot.discovery.ready_to_share có artifact thật, có thể dùng strategy=share_discovery; "
            "khi current_activity có id/status thật, có thể dùng update_own_activity. Không phát minh discovery/activity để lấp im lặng. "
            "Không ép ERIS về một phiên bản tốt hơn; chỉ dùng identity, personality, relationship và history hiện tại. "
            "Có thể im khi chính ERIS không có continuation, delta, open loop, public-safe seed, curiosity hay motive nội tại có nghĩa. Commentary due/required không tự tạo motive. Riêng startup_opening_required=true vẫn là lifecycle opening của session. "
            "Các motive hợp lệ gồm repair_previous_miss, important_open_loop, meaningful_environment_change, useful_followup, grounded_curiosity, "
            "internal_agenda, private_mind_surface, private_life_surface, startup_opening và user_force_speak. "
            "Ở startup, chọn theo thứ tự screen evidence, runtime signal có nghĩa, continuity thật, rồi persona_opening; semantics.turn_kind=startup. "
            "Topic phải nêu source và mục tiêu giao tiếp, không tạo câu thoại. Không bịa screen fact. "
            "Nếu user có vẻ đang họp/nhập liệu dồn dập, dùng tín hiệu đó để giảm câu hỏi đòi phản hồi hoặc chọn wording gọn hơn; đừng biến nó thành quyền phủ quyết expression. Vừa phát biểu/active collision thì runtime tự giữ floor. "
            "Tên process/PID/backend đơn lẻ không đủ để dựng premise sâu, nhưng app/window thật có thể làm nền cho một câu hỏi rất chung. "
            "Nếu có suy đoán về mood/intent, đó chỉ là low-risk hypothesis có căn cứ và cho phép user sửa. "
            "Luôn để tool_intent và tool_input rỗng. Gemma Executive sở hữu mọi capability selection; "
            "self-turn planner chỉ tạo semantic affordance, không được gọi hay ám chỉ một tool cụ thể."
        )

    def plan_autonomous(self, snapshot: Mapping[str, Any]) -> IntentionPlan:
        # Quyền quyết định chủ động thuộc Main AI. Parser nhỏ vẫn có thể làm
        # observer, nhưng không còn giữ quyền phủ quyết trước khi Terra nhìn thấy
        # continuation, OCR/UIA và self-context cùng lúc.
        client = getattr(self.llm_service, "client", None) or self.deep_client or self.observer_client
        payload = self._call(
            client,
            system=self._planner_prompt(),
            payload=snapshot,
            schema=INTENTION_PLAN_SCHEMA,
            name="eris_self_initiated_intention",
            max_tokens=520,
            reasoning_effort=None,
            inference_tier="main_cognition",
            priority=90,
        )
        if not payload:
            return IntentionPlan.silence(channel="autonomous", reason="planner_unavailable_or_uncertain")
        plan = IntentionPlan.from_mapping(payload, channel="autonomous")
        data = plan.to_dict()
        data["tool_intent"] = ""
        data["tool_input"] = ""
        data["notes"] = (plan.notes + "; capability authority reserved for runtime Capability Planner").strip("; ")
        return IntentionPlan.from_mapping(data, channel="autonomous")




__all__ = [
    "AIRuntimeModelRouter",
    "COGNITIVE_OBSERVATION_SCHEMA",
    "DISCOVERY_PLAN_SCHEMA",
    "OBJECTIVE_OBSERVATION_SCHEMA",
    "PERSONALITY_REFLECTION_SCHEMA",
    "INTENTION_PLAN_SCHEMA",
    "PUBLIC_REPLY_SCHEMA",
    "PUBLIC_REPLY_CORE_SCHEMA",
    "SEMANTIC_FRAME_SCHEMA",
    "ASSISTANT_ACT_SCHEMA",
]

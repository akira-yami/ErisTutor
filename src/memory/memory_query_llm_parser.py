from __future__ import annotations

"""
memory_query_llm_parser.py

LLM structured parser cho memory query khi ambiguity cao.

Quy tắc:
- LLM KHÔNG được trả lời ký ức.
- LLM KHÔNG được bịa nội dung đã nhớ.
- LLM chỉ chuyển user text thành JSON theo schema MemoryQueryIR.

Module này không tự gọi API. bạn inject client vào.
"""

from dataclasses import dataclass
from typing import Any, Optional, Protocol
import json

from .memory_query_ir import (
    ActRef,
    MemoryQueryIR,
    MemoryRelation,
    MemorySelector,
    QueryIntent,
    RelationRef,
    RoleRef,
    SurfaceMode,
    clamp01,
)


class MemoryQueryLLMClient(Protocol):
    """
    Client tối thiểu cần có.

    Có thể adapter từ LlmService hiện tại:
    - gửi system prompt + user text
    - trả raw string JSON
    """

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any]) -> str:
        ...


@dataclass(slots=True)
class LLMParseDecision:
    should_call: bool
    reason: str


class MemoryQueryLLMParser:
    """Parser LLM có schema, chỉ dùng khi compiler cần."""

    def __init__(
        self,
        client: Optional[MemoryQueryLLMClient] = None,
        *,
        ambiguity_threshold: float = 0.35,
        close_margin_threshold: float = 0.08,
    ) -> None:
        self.client = client
        self.ambiguity_threshold = ambiguity_threshold
        self.close_margin_threshold = close_margin_threshold

    def should_use_llm(
        self,
        *,
        ambiguity: float,
        top_margin: float,
        has_nested_relation: bool,
    ) -> LLMParseDecision:
        if self.client is None:
            return LLMParseDecision(False, "no_llm_client")
        if has_nested_relation and ambiguity > 0.22:
            return LLMParseDecision(True, "nested_relation_uncertain")
        if ambiguity > self.ambiguity_threshold:
            return LLMParseDecision(True, "ambiguity_high")
        if top_margin < self.close_margin_threshold:
            return LLMParseDecision(True, "top_hypotheses_close")
        return LLMParseDecision(False, "slot_semantic_confident_enough")

    def parse(self, text: str) -> Optional[MemoryQueryIR]:
        if self.client is None:
            return None

        raw = self.client.complete_json(
            system=self.build_system_prompt(),
            user=text,
            schema=self.schema(),
        )
        data = self._parse_json(raw)
        if not isinstance(data, dict):
            return None
        return self._dict_to_ir(data, raw_text=text)

    def build_system_prompt(self) -> str:
        return (
            "Bạn là bộ parse truy vấn ký ức của ERIS.\n"
            "Nhiệm vụ duy nhất: chuyển câu người dùng thành MemoryQueryIR JSON.\n"
            "Không trả lời ký ức. Không bịa nội dung đã nhớ. Không quote transcript.\n"
            "Nếu câu hỏi mơ hồ, hạ confidence và tăng ambiguity.\n"
            "Nếu người dùng hỏi 'câu trả lời của em cho câu hỏi thứ N', "
            "target phải là assistant answer, relation phải là answer_to, "
            "anchor là user question ordinal N.\n"
            "Trả JSON hợp lệ, không prose."
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["intent", "target", "relation", "surface", "confidence", "ambiguity"],
            "properties": {
                "intent": {"type": "string"},
                "target": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "act": {"type": "string"},
                        "ordinal": {"type": ["integer", "null"]},
                        "quantity": {"type": ["integer", "null"]},
                        "recent": {"type": "boolean"},
                        "fact_key": {"type": ["string", "null"]},
                        "semantic_hint": {"type": ["string", "null"]},
                    },
                },
                "relation": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "anchor": {"type": ["object", "null"]},
                    },
                },
                "surface": {"type": "string"},
                "confidence": {"type": "number"},
                "ambiguity": {"type": "number"},
                "uncertain_fields": {"type": "array", "items": {"type": "string"}},
            },
        }

    # ------------------------------------------------------------------
    # Convert/validate
    # ------------------------------------------------------------------

    def _parse_json(self, raw: str) -> Any:
        raw = raw.strip()
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

        # Salvage object đầu tiên. Đời thật bẩn, parser phải biết rửa tay.
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
        return None

    def _dict_to_ir(self, data: dict[str, Any], *, raw_text: str) -> Optional[MemoryQueryIR]:
        try:
            intent = _enum(QueryIntent, data.get("intent"), QueryIntent.UNKNOWN)
            target = self._selector(data.get("target") or {})
            rel_data = data.get("relation") or {}
            relation_type = _enum(RelationRef, rel_data.get("type"), RelationRef.UNKNOWN)
            anchor = self._selector(rel_data.get("anchor") or {}) if rel_data.get("anchor") else None
            surface = _enum(SurfaceMode, data.get("surface"), SurfaceMode.EXACT)
            confidence = clamp01(float(data.get("confidence", 0.0)))
            ambiguity = clamp01(float(data.get("ambiguity", 1.0)))
            warnings = tuple(str(x) for x in data.get("uncertain_fields", []) if x)

            return MemoryQueryIR(
                intent=intent,
                target=target,
                relation=MemoryRelation(relation_type, anchor=anchor),
                surface=surface,
                confidence=confidence,
                ambiguity=ambiguity,
                evidence=("llm_structured_parse",),
                warnings=warnings,
                raw_text=raw_text,
            )
        except (TypeError, ValueError, KeyError):
            return None

    def _selector(self, data: dict[str, Any]) -> MemorySelector:
        return MemorySelector(
            role=_enum(RoleRef, data.get("role"), RoleRef.ANY),
            act=_enum(ActRef, data.get("act"), ActRef.ANY),
            ordinal=_int_or_none(data.get("ordinal")),
            quantity=_int_or_none(data.get("quantity")),
            recent=bool(data.get("recent", False)),
            fact_key=data.get("fact_key") or None,
            semantic_hint=data.get("semantic_hint") or None,
        )


def _enum(enum_cls, value: Any, default):
    try:
        return enum_cls(str(value))
    except Exception:
        return default


def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        ivalue = int(value)
        return ivalue if ivalue > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None

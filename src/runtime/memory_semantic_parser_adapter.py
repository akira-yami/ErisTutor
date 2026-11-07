from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from memory.understanding.semantic_parser import (
    FallbackSurfaceSemanticParser,
    MeaningParseResult,
    ModelSemanticParser,
    StructuredFrameParser,
)


_SYSTEM_PROMPT = """Bạn là memory classification engine cho ERIS.
Nhiệm vụ: nhận user_text và quyết định nó nên được lưu vào memory như thế nào.

Bạn phải quyết định hoàn toàn — không cần theo schema cứng. Mỗi mảnh thông tin có thể:
- Là fact ổn định (tên, sinh nhật, quê, sở thích lâu dài)
- Là sự kiện (chuyện xảy ra, có thời gian, có ngữ cảnh)
- Là cảm xúc/tâm trạng thoáng qua (buồn hôm nay, mệt, vui vì điều gì)
- Là kiến thức (dạy ERIS biết điều gì về thế giới, kỹ thuật, khái niệm)
- Là thực thể (người, vật, địa điểm user đề cập — con mèo hàng xóm, bạn tên X, quán ăn)
- Là niềm tin/quan điểm (user nghĩ A là đúng, không đồng ý với B)
- Là mục tiêu (user muốn làm gì, dự định gì)
- Là phản hồi về ERIS (khen, chê, sửa, hướng dẫn cách phản hồi)
- Là quy ước (từ giờ X nghĩa là Y, gọi tôi là Z)
- Là ngữ cảnh thoáng qua (không quan trọng lâu dài)

Với mỗi frame, bạn quyết định:
- memory_type: loại memory (fact, event, emotion, knowledge, entity, belief, goal, feedback, convention, context)
- storage_target: nơi lưu (semantic, episodic, procedural, affective_residue, relational, candidate)
- importance: 0.0-1.0 (fact tên=0.95, cảm xúc thoáng=0.3, kiến thức chung=0.6)
- decay: slow (năm+) | normal (tháng) | fast (tuần) | ephemeral (ngày/session)
- surface_fn: can_answer (trả lời trá»±c tiếp khi hỏi) | background (ảnh hưởng context) | never (không surface)
- fact_key: key ngắn nếu là fact rõ (user.name, user.hometown, ...) hoặc để trống

Quy tắc storage_target:
- semantic: fact ổn định, kiến thức, niềm tin, thực thể quan trọng
- episodic: sá»± kiện cụ thể có thời gian/ngữ cảnh
- procedural: quy tắc hành vi, convention, cách xử lý
- affective_residue: cảm xúc, tâm trạng (KHÔNG surface như fact)
- relational: pattern quan hệ với user, người khác
- candidate: không chắc chắn, cần xem xét thêm

Không tạo frame cho: câu hỏi recall ("tôi tên gì"), chào hỏi đơn giản, câu vô nghĩa.
Chỉ trả JSON."""

_USER_PROMPT_TEMPLATE = """Phân tích và phân loại memory cho câu sau.

Output schema:
{
  "frames": [
    {
      "memory_type": "fact|event|emotion|knowledge|entity|belief|goal|feedback|convention|context",
      "storage_target": "semantic|episodic|procedural|affective_residue|relational|candidate",
      "subject": "user|eris|world|other:<name>",
      "claim": "câu tóm tắt ngắn gọn cái cần nhớ (không phải raw text)",
      "value": "giá trị cốt lõi nếu là fact/entity (tên người, địa điểm, ...) hoặc null",
      "fact_key": "user.name | user.hometown | ... hoặc '' nếu không có key chuẩn",
      "importance": 0.0,
      "decay": "slow|normal|fast|ephemeral",
      "surface_fn": "can_answer|background|never",
      "time_scope": "stable|current|past|future|session",
      "confidence": 0.0,
      "why": "lý do ngắn — tại sao phân loại thế này"
    }
  ]
}

Ví dụ:
- "tôi tên Hòa" → memory_type=fact, storage_target=semantic, fact_key=user.name, value=Hòa, importance=0.95, decay=slow, surface_fn=can_answer
- "con mèo nhà hàng xóm tên Miso hay sang ăn trộm đồ" → memory_type=entity, storage_target=episodic, fact_key='', importance=0.45, decay=normal, surface_fn=background
- "tôi đang buồn vì dự án bị delay" → memory_type=emotion, storage_target=affective_residue, importance=0.35, decay=fast, surface_fn=never
- "Python là ngôn ngữ lập trình hướng đối tượng" → memory_type=knowledge, storage_target=semantic, importance=0.55, decay=slow, surface_fn=background
- "từ giờ khi tôi nói 'loi' tức là bug" → memory_type=convention, storage_target=procedural, importance=0.88, decay=slow, surface_fn=background
- "tôi muốn build game nhập vai năm nay" → memory_type=goal, storage_target=semantic, importance=0.72, decay=normal, surface_fn=background
- "trả lời ngắn thôi đừng dài dòng" → memory_type=feedback, storage_target=procedural, fact_key=user.response_style, importance=0.85, decay=slow, surface_fn=background

user_text:
{text}
"""




@dataclass(frozen=True, slots=True)
class MemorySemanticParserConfig:
    enabled: bool = field(default_factory=lambda: _runtime_bool("memory", "semantic_parser", False))
    provider: str = field(default_factory=lambda: os.getenv("ERIS_MEMORY_SEMANTIC_PROVIDER", "llm_service").strip().lower() or "llm_service")
    model: str = field(default_factory=lambda: os.getenv("ERIS_MEMORY_SEMANTIC_MODEL", "").strip())
    timeout_seconds: float = field(default_factory=lambda: _env_float("ERIS_MEMORY_SEMANTIC_TIMEOUT", 4.0))
    fallback_enabled: bool = field(default_factory=lambda: _env_flag("ERIS_MEMORY_SEMANTIC_FALLBACK", True))
    keep_alive: str | int = field(default_factory=lambda: os.getenv("ERIS_MEMORY_SEMANTIC_KEEP_ALIVE", "10m"))
    max_tokens: int = field(default_factory=lambda: _env_int("ERIS_MEMORY_SEMANTIC_MAX_TOKENS", 384))

    @classmethod
    def from_env(cls) -> "MemorySemanticParserConfig":
        return cls()


@dataclass(slots=True)
class RuntimeSemanticParser:
    """Adapter runtime -> MeaningFrame JSON -> verified MeaningParseResult."""

    llm_service: Any = None
    model: str = ""
    provider: str = "llm_service"
    timeout_seconds: float = 4.0
    fallback_enabled: bool = True
    keep_alive: str | int = "10m"
    max_tokens: int = 384
    logger: Any | None = None
    verifier: StructuredFrameParser = field(default_factory=StructuredFrameParser, repr=False)
    fallback_parser: Any | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.provider = str(self.provider or "llm_service").strip().lower()
        self.model = str(self.model or "").strip()
        self.logger = self.logger or logging.getLogger(__name__)
        if self.fallback_parser is None and self.fallback_enabled:
            self.fallback_parser = FallbackSurfaceSemanticParser()

    @classmethod
    def from_env(cls, *, llm_service: Any = None, logger: Any = None) -> "RuntimeSemanticParser | None":
        config = MemorySemanticParserConfig.from_env()
        if not config.enabled:
            return None
        return cls(
            llm_service=llm_service,
            model=config.model,
            provider=config.provider,
            timeout_seconds=config.timeout_seconds,
            fallback_enabled=config.fallback_enabled,
            keep_alive=config.keep_alive,
            max_tokens=config.max_tokens,
            logger=logger,
        )

    def parse(
        self,
        text: str,
        *,
        structured: Mapping[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> MeaningParseResult:
        raw = str(text or "").strip()
        if not raw:
            return MeaningParseResult(source="runtime_memory_parser", confidence=0.0, diagnostics=("empty_input",))
        if structured is not None:
            return self.verifier.parse(raw, structured=structured)

        try:
            payload = self._call_provider(raw, context=context)
            if payload:
                return ModelSemanticParser(lambda _text: payload, fallback=self.fallback_parser).parse(raw)
        except Exception as exc:
            self._log_warning("Runtime semantic parser failed: %s", exc)
            if self.fallback_parser is not None:
                result = self.fallback_parser.parse(raw)
                return MeaningParseResult(
                    frames=result.frames,
                    source="runtime_memory_parser_fallback",
                    confidence=result.confidence,
                    diagnostics=(f"provider_error:{type(exc).__name__}", *result.diagnostics),
                )
            return MeaningParseResult(source="runtime_memory_parser_failed", confidence=0.0, diagnostics=(f"provider_error:{type(exc).__name__}",))

        if self.fallback_parser is not None:
            result = self.fallback_parser.parse(raw)
            return MeaningParseResult(
                frames=result.frames,
                source="runtime_memory_parser_empty_fallback",
                confidence=result.confidence,
                diagnostics=("empty_provider_payload", *result.diagnostics),
            )
        return MeaningParseResult(source="runtime_memory_parser_empty", confidence=0.0, diagnostics=("empty_provider_payload",))

    def _call_provider(self, text: str, *, context: dict[str, Any] | None = None) -> Mapping[str, Any] | None:
        if self.provider == "llm_service":
            return self._call_llm_service(text, context=context)
        raise RuntimeError(f"unsupported_memory_semantic_provider:{self.provider}")

    def _call_llm_service(self, text: str, *, context: dict[str, Any] | None = None) -> Mapping[str, Any] | None:
        service = self.llm_service
        client = getattr(service, "client", None)
        if not callable(getattr(client, "create_chat_completion", None)):
            raise RuntimeError("llm_service_client_missing_chat_completion")
        # Dung bat ky remote client nao dang duoc cau hinh: Gemini hoac OpenAI-compatible.
        from runtime.inference_broker import inference_context
        from runtime.work_keys import stable_work_key
        work_key = stable_work_key("memory_semantic", text)
        with inference_context(
            "memory_digest",
            optional=True,
            timeout_seconds=self.timeout_seconds,
            cancel_scope="background:memory_semantic_parser",
            coalesce_key=work_key,
            idempotency_key=work_key,
            metadata={"operation": "memory_semantic_parser"},
        ):
            response = client.create_chat_completion(
                messages=self._messages(text, context=context),
                temperature=0.0,
                frequency_penalty=0.0,
                presence_penalty=0.0,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
                grammar=None,
                timeout=self.timeout_seconds,
            )
        extractor = getattr(service, "_extract_content", None)
        content = str(extractor(response) or "") if callable(extractor) else _extract_response_text(response)
        payload = _parse_json_object(content)
        if not isinstance(payload, Mapping):
            raise RuntimeError("llm_service_json_parse_failed")
        return payload

    def _messages(self, text: str, *, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
        prompt = _USER_PROMPT_TEMPLATE.format(text=text)
        if context:
            compact_context = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            prompt = f"{prompt}\n\nruntime_context:\n{compact_context}"
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

    def _log_warning(self, message: str, *args: Any) -> None:
        logger = self.logger
        if logger is not None and callable(getattr(logger, "warning", None)):
            logger.warning(message, *args)


def build_runtime_memory_semantic_parser(*, llm_service: Any = None, logger: Any = None) -> RuntimeSemanticParser | None:
    return RuntimeSemanticParser.from_env(llm_service=llm_service, logger=logger)


def _extract_response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if not isinstance(response, dict):
        return ""
    direct = response.get("output_text") or response.get("text") or response.get("content")
    if isinstance(direct, str):
        return direct
    choices = response.get("choices")
    if not isinstance(choices, list):
        return ""
    for choice in choices:
        if not isinstance(choice, Mapping):
            continue
        message = choice.get("message")
        if isinstance(message, Mapping) and isinstance(message.get("content"), str):
            return str(message.get("content") or "")
    return ""


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}

def _runtime_bool(section: str, key: str, default: bool) -> bool:
    try:
        from runtime.runtime_settings_store import runtime_bool
        return runtime_bool(section, key, default=default)
    except Exception:
        return default



def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return default


__all__ = [
    "MemorySemanticParserConfig",
    "RuntimeSemanticParser",
    "build_runtime_memory_semantic_parser",
]

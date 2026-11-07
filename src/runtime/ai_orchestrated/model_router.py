# -*- coding: utf-8 -*-
"""Model router for ErisTutor structured workers and public reply generation."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence


class ObserverInferenceError(RuntimeError):
    """Categorized observer error."""

    def __init__(self, status: str = "provider_error", *, detail: str = "") -> None:
        self.status = status
        self.detail = detail[:100]
        super().__init__(self.status)


def _public_display_language_mismatch(text: str, language_code: str) -> bool:
    """Guard against model outputting non-Vietnamese when Vietnamese requested."""
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


class AIRuntimeModelRouter:
    """Unified Model Router connecting LLMService workers with RootAgent, Writer, and Reasoner."""

    def __init__(self, llm_service: Any, *, logger: Any = None, budget_policy: Any = None) -> None:
        self.llm_service = llm_service
        self.logger = logger
        self.budget_policy = budget_policy

    @property
    def executive_client(self) -> Any | None:
        return getattr(self.llm_service, "executive_client", None) or getattr(self.llm_service, "companion_client", None)

    @property
    def deep_client(self) -> Any | None:
        return getattr(self.llm_service, "companion_client", None)

    @property
    def surface_client(self) -> Any | None:
        return getattr(self.llm_service, "surface_client", None) or getattr(self.llm_service, "companion_client", None)

    @property
    def learner_client(self) -> Any | None:
        return getattr(self.llm_service, "learner_client", None) or getattr(self.llm_service, "companion_client", None)

    @property
    def seed_client(self) -> Any | None:
        return getattr(self.llm_service, "seed_client", None)

    @property
    def observer_client(self) -> Any | None:
        return getattr(self.llm_service, "observer_client", None)

    @property
    def fallback_client(self) -> Any | None:
        return getattr(self.llm_service, "fallback_client", None)

    def contract_repair_context(self) -> str:
        return ""

    def close_contract_repair(self) -> None:
        pass

    def invoke_structured_worker(
        self,
        client: Any,
        *,
        system: str,
        payload: Mapping[str, Any],
        schema: Mapping[str, Any],
        name: str = "structured_worker",
        max_tokens: int = 1200,
        reasoning_effort: str | None = None,
        inference_tier: str = "subconscious_4b",
        priority: int | None = None,
        timeout_seconds: float | None = None,
        cognitive_lane: str = "",
        temperature: float | None = None,
        conversation_messages: Sequence[Mapping[str, Any]] | None = None,
        raw_reply_sink: list[str] | None = None,
    ) -> dict[str, Any]:
        """Transport boundary for typed structured workers."""
        if client is None:
            return {}

        messages = (
            [dict(m) for m in conversation_messages]
            if conversation_messages is not None
            else [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(dict(payload), ensure_ascii=False)},
            ]
        )

        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "strict": False,
                "schema": dict(schema),
            },
        }

        temp = float(temperature if temperature is not None else 0.0)
        try:
            call_fn = getattr(client, "create_chat_completion", None)
            if not callable(call_fn):
                return {}

            raw = call_fn(
                messages=messages,
                temperature=temp,
                max_tokens=max_tokens,
                response_format=response_format,
                timeout=timeout_seconds,
            )

            # Extract content string
            content = ""
            if isinstance(raw, dict):
                choices = raw.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    msg = choices[0].get("message", {})
                    content = msg.get("content", "")
            elif hasattr(raw, "choices") and raw.choices:
                content = getattr(raw.choices[0].message, "content", "")

            content = str(content or "").strip()
            if raw_reply_sink is not None:
                raw_reply_sink.append(content)

            if not content:
                return {}

            # Parse JSON with recovery if wrapped in ```json ... ```
            clean = content
            if clean.startswith("```"):
                lines = clean.splitlines()
                if len(lines) >= 2 and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                clean = "\n".join(lines).strip()

            parsed = json.loads(clean)
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except Exception as exc:
            if self.logger and callable(getattr(self.logger, "debug", None)):
                self.logger.debug("[model_router] invoke_structured_worker error (%s): %s", name, exc)
            return {}

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
        discourse_context: str = "",
        language_hint: str = "vi",
        speech_language_hint: str = "ja",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate public reply dialog for the public surface."""
        client = self.surface_client or getattr(self.llm_service, "client", None)
        if client is None or not callable(getattr(client, "create_chat_completion", None)):
            return {
                "should_speak": True,
                "text": "Xin chào! Mình là Eris.",
                "speech_text": "Xin chào! Mình là Eris.",
                "display_language": language_hint,
            }

        # Build system prompt with persona and context
        sys_parts = [
            "Bạn là Eris, một trợ lý gia sư AI thân thiện, thông minh, tinh tế và ấm áp.",
            "Hãy trả lời người dùng một cách tự nhiên, rõ ràng, gãy gọn bằng tiếng Việt.",
        ]
        if personality_context:
            sys_parts.append(f"Tính cách Eris:\n{personality_context}")
        if relationship_context:
            sys_parts.append(f"Mối quan hệ:\n{relationship_context}")
        if grounding_context:
            sys_parts.append(f"Ngữ cảnh xung quanh:\n{grounding_context}")
        if memory_context:
            sys_parts.append(f"Ký ức liên quan:\n{memory_context}")
        if discourse_context:
            sys_parts.append(f"Chỉ dẫn diễn ngôn:\n{discourse_context}")

        system_prompt = "\n\n".join(sys_parts)

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for turn in chat_history[-10:]:
            role = str(turn.get("role", "user"))
            content = str(turn.get("content", ""))
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})

        if user_text:
            messages.append({"role": "user", "content": user_text})

        try:
            raw = client.create_chat_completion(
                messages=messages,
                temperature=0.7,
                max_tokens=600,
            )
            reply = ""
            if isinstance(raw, dict):
                choices = raw.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    reply = choices[0].get("message", {}).get("content", "")
            elif hasattr(raw, "choices") and raw.choices:
                reply = getattr(raw.choices[0].message, "content", "")

            reply = str(reply or "").strip()
            if not reply:
                reply = "Eris đã hiểu ý bạn."

            return {
                "should_speak": True,
                "text": reply,
                "speech_text": reply,
                "display_language": language_hint,
            }
        except Exception as exc:
            if self.logger and callable(getattr(self.logger, "warning", None)):
                self.logger.warning("[model_router] generate_public_reply error: %s", exc)
            return {
                "should_speak": True,
                "text": "Mình đang kết nối lại, bạn đợi xíu nhé.",
                "speech_text": "Mình đang kết nối lại, bạn đợi xíu nhé.",
                "display_language": language_hint,
            }


__all__ = ["AIRuntimeModelRouter", "ObserverInferenceError"]

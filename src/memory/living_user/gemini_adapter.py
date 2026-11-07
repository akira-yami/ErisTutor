# -*- coding: utf-8 -*-
"""Living User/GC dùng lane Gemini Web của service đang giữ phiên đăng nhập.

Cookie và gemini_webapi thuộc service Gemini, không thuộc Python portable.
Không fallback sang DeepSeek khi service lỗi: caller phải giữ evidence và retry.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .runtime_adapter import (
    _PROFILE_SYSTEM, _EPISODE_SYSTEM, _extract_text, _parse_json, CuratorInferenceError,
)

# Profile extraction, dream và GC cùng chia sẻ một lane, kể cả khi factory được
# gọi từ hai owner. Không mở hai phiên cookie độc lập trong portable.
_LANE_LOCK = threading.Lock()


@dataclass
class GeminiWebUserProfileAdapter:
    base_url: str = "http://127.0.0.1:8000/v1"
    model_name: str = "gemini-flash"
    timeout: float = 120.0
    provider_name: str = "gemini_web"
    worker_role: str = "curator"

    @classmethod
    def create_if_available(cls, *, timeout: float | None = None) -> GeminiWebUserProfileAdapter | None:
        """Đọc cấu hình; không gọi mạng hoặc đòi cookie ở runtime khi bootstrap."""
        if os.getenv("ERIS_MEMORY_GEMINI_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
            return None
        return cls(
            base_url=(os.getenv("ERIS_MEMORY_GEMINI_BASE_URL", "").strip()
                      or "http://127.0.0.1:8000/v1").rstrip("/"),
            model_name=os.getenv("ERIS_MEMORY_GEMINI_MODEL", "").strip() or "gemini-flash",
            timeout=float(timeout if timeout is not None else os.getenv("ERIS_MEMORY_GEMINI_TIMEOUT_SEC", "120")),
        )

    def propose_profile_operations(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        parsed = self._generate_json(_PROFILE_SYSTEM, payload)
        if not isinstance(parsed.get("operations"), list) or not isinstance(parsed.get("action_operations"), list):
            raise CuratorInferenceError("parse_error")
        return parsed

    def summarize_episode(self, payload: Mapping[str, Any], *, timeout: float | None = None) -> Mapping[str, Any]:
        parsed = self._generate_json(_EPISODE_SYSTEM, payload, timeout=timeout)
        if not isinstance(parsed.get("summary"), str) or not parsed["summary"].strip():
            raise CuratorInferenceError("parse_error")
        return parsed

    def summarize_episode_rollup(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        from .episode_rollup import ROLLUP_SYSTEM
        parsed = self._generate_json(ROLLUP_SYSTEM, payload)
        if not isinstance(parsed.get("segments"), list):
            raise CuratorInferenceError("parse_error")
        return parsed

    def _generate_json(self, system: str, payload: Mapping[str, Any], *,
                       timeout: float | None = None) -> Mapping[str, Any]:
        response = self.create_chat_completion(messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        ], timeout=timeout)
        parsed = _parse_json(_extract_text(response))
        if not isinstance(parsed, Mapping):
            raise CuratorInferenceError("parse_error")
        return parsed

    def create_chat_completion(self, *, messages: list[dict[str, Any]], max_tokens: int = 1800,
                               timeout: float | None = None, **_kwargs: Any) -> Mapping[str, Any]:
        """Giao diện chung cho curator/GC; chỉ chấp nhận response hoàn chỉnh."""
        import requests
        from runtime.inference_broker import inference_context

        deadline = float(timeout if timeout is not None else self.timeout)
        payload = {
            "model": self.model_name, "messages": messages, "temperature": 0.0,
            "max_tokens": max_tokens, "stream": False,
        }
        # Chờ lane có hạn: ngân sách `deadline` gồm cả thời gian chờ lane lẫn HTTP,
        # để một call khác giữ lane không làm caller treo vô hạn.
        started = time.monotonic()
        if not _LANE_LOCK.acquire(timeout=max(0.0, deadline)):
            raise CuratorInferenceError("lane_busy")
        try:
            remaining = max(1.0, deadline - (time.monotonic() - started))
            with inference_context(
                "memory_curator", optional=False, timeout_seconds=remaining,
                cancel_scope="background:gemini_memory", project="memory_curator",
                credential_pool="gemini_web", metadata={"provider": self.provider_name},
            ):
                try:
                    response = requests.post(
                        f"{self.base_url}/chat/completions", json=payload, timeout=(5.0, remaining),
                    )
                    response.raise_for_status()
                    data = response.json()
                except requests.Timeout as exc:
                    raise CuratorInferenceError("timeout") from exc
                except requests.RequestException as exc:
                    raise CuratorInferenceError("provider_error") from exc
                except ValueError as exc:
                    raise CuratorInferenceError("parse_error") from exc
        finally:
            _LANE_LOCK.release()
        if not isinstance(data, Mapping):
            raise CuratorInferenceError("parse_error")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise CuratorInferenceError("parse_error")
        if choices[0].get("finish_reason") not in {None, "stop"}:
            raise CuratorInferenceError("incomplete_output")
        if not _extract_text(data).strip():
            raise CuratorInferenceError("empty_output")
        return data

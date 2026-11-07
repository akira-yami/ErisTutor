# -*- coding: utf-8 -*-
"""Adapter nối runtime cũ vào các provider contract chuẩn.

Phase 4 mới chỉ định nghĩa ``Protocol``. Như vậy đẹp trên sơ đồ, nhưng coordinator
vẫn gọi thẳng ActionExecutor/MemoryKernel/TtsEngine. Module này hoàn tất bước
migration: bọc implementation hiện tại bằng adapter nhỏ, giữ tương thích ngược và
cho phép registry kiểm health/fail-fast ở một chỗ.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping

from runtime.provider_interfaces import ProviderHealth, ProviderRegistry


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        try:
            return dict(asdict(value))
        except (TypeError, ValueError):
            return {}
    try:
        return dict(vars(value))
    except TypeError:
        return {}


class ActionExecutorProvider:
    """Bọc ``ActionExecutor`` theo contract ActionProvider."""

    def __init__(self, executor: Any) -> None:
        self.executor = executor

    def execute(self, action_name: str, action_input: str = "") -> Any:
        method = getattr(self.executor, "execute", None)
        if not callable(method):
            raise RuntimeError("action_provider_unavailable")
        result = method(action_name, action_input)
        return _normalize_action_result(result, action_name=action_name, action_input=action_input)

    def health(self) -> ProviderHealth:
        ready = callable(getattr(self.executor, "execute", None))
        observer = getattr(self.executor, "observer", None)
        details = {
            "observer_bound": observer is not None,
            "implementation": type(self.executor).__name__,
        }
        return ProviderHealth(ready, "action_executor", "" if ready else "missing_execute", details)


class SpeechEngineProvider:
    """Bọc ``TtsEngine`` và chuyển SpeechContract thành keyword rõ ràng.

    Adapter chỉ tổng hợp WAV; phát/cancel vẫn thuộc StageDispatcher/VoiceManager.
    Việc tách hai phần tránh để provider abstraction nuốt luôn scheduling audio,
    một cách rất con người để biến một interface nhỏ thành thần linh toàn năng.
    """

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def synthesize(self, contract: Any, text: str) -> Any:
        method = getattr(self.engine, "synthesize", None)
        if not callable(method):
            raise RuntimeError("speech_provider_unavailable")
        data = _mapping(contract)
        language = str(data.get("language") or data.get("language_hint") or "").strip()
        locale = str(data.get("locale") or "").strip()
        voice_family = str(data.get("voice_family") or "").strip()
        return method(
            str(text or ""),
            language_hint=language,
            locale=locale,
            voice_family=voice_family,
            turn_id=str(data.get("turn_id") or ""),
            segment_id=int(data.get("segment_id") or 0),
            interruptible=bool(data.get("interruptible", True)),
            prelocalized=bool(data.get("prelocalized", False)),
            prosody_style=str(data.get("prosody_style") or ""),
            prosody_notes=str(data.get("prosody_notes") or ""),
            valence=float(data.get("valence") or 0.0),
            arousal=float(data.get("arousal") or 0.3),
            stance=str(data.get("stance") or ""),
        )

    def health(self) -> ProviderHealth:
        closed = bool(getattr(self.engine, "_closed", False))
        worker_ready = bool(getattr(self.engine, "_ok", False))
        ready = callable(getattr(self.engine, "synthesize", None)) and worker_ready and not closed
        reason = "" if ready else ("closed" if closed else "tts_not_ready")
        details = {
            "implementation": type(self.engine).__name__,
            "worker_ready": worker_ready,
            "closed": closed,
            "cache_signature": _safe_call(self.engine, "cache_signature"),
            "fallback_providers": _safe_call(self.engine, "fallback_health_snapshot"),
        }
        return ProviderHealth(ready, "speech_engine", reason, details)


class MemoryKernelProvider:
    """Bọc MemoryKernel hoặc memory core tương thích recall/commit."""

    _QUERY_METHODS = ("recall", "retrieve", "query", "answer")
    _COMMIT_METHODS = ("commit", "ingest", "store", "add")

    def __init__(self, memory: Any) -> None:
        self.memory = memory

    def query(self, request: Any) -> Any:
        method = _first_callable(self.memory, self._QUERY_METHODS)
        if method is None:
            raise RuntimeError("memory_query_unavailable")
        return method(request)

    def commit(self, record: Any) -> Any:
        method = _first_callable(self.memory, self._COMMIT_METHODS)
        if method is None:
            raise RuntimeError("memory_commit_unavailable")
        return method(record)

    def health(self) -> ProviderHealth:
        query_name = _first_callable_name(self.memory, self._QUERY_METHODS)
        commit_name = _first_callable_name(self.memory, self._COMMIT_METHODS)
        ready = bool(query_name and commit_name)
        return ProviderHealth(
            ready,
            "memory_kernel",
            "" if ready else "missing_query_or_commit",
            {
                "implementation": type(self.memory).__name__,
                "query_method": query_name,
                "commit_method": commit_name,
            },
        )


class TokenPressureBudgetProvider:
    """Bọc TokenPressureGovernor theo BudgetGovernor contract."""

    def __init__(self, governor: Any) -> None:
        self.governor = governor

    def plan(
        self,
        *,
        provider: str,
        lane: str,
        prompt_tokens: int,
        output_tokens: int,
    ) -> Any:
        method = getattr(self.governor, "plan_request", None)
        if not callable(method):
            raise RuntimeError("budget_provider_unavailable")
        return method(
            provider,
            lane=lane,
            prompt_tokens=max(0, int(prompt_tokens)),
            requested_output_tokens=max(1, int(output_tokens)),
        )

    def health(self) -> ProviderHealth:
        ready = callable(getattr(self.governor, "plan_request", None))
        return ProviderHealth(
            ready,
            "token_pressure_governor",
            "" if ready else "missing_plan_request",
            {"implementation": type(self.governor).__name__},
        )


def _normalize_action_result(result: Any, *, action_name: str, action_input: str) -> Any:
    """Nâng result legacy/SimpleNamespace lên ActionExecutionResult chuẩn.

    Nhiều executor cũ và test double chỉ trả ``success/message/data``. Nếu các
    lớp sau giả định có ``status``/``to_public_meta`` thì contract mới tự làm
    hỏng chính đường migration của nó, một màn hài kịch khá truyền thống.
    """
    try:
        from actions.contracts import ActionExecutionResult
    except ImportError:
        return result
    if isinstance(result, ActionExecutionResult):
        return result
    if isinstance(result, Mapping):
        data = dict(result)
    else:
        try:
            data = dict(vars(result))
        except TypeError:
            data = {}
    return ActionExecutionResult(
        action_name=str(data.get("action_name") or action_name or ""),
        action_input=str(data.get("action_input") or action_input or ""),
        success=bool(data.get("success", False)),
        message=str(data.get("message") or ""),
        data=dict(data.get("data") or {}),
        status=data.get("status", ""),
        dispatched=bool(data.get("dispatched", False)),
        observed=bool(data.get("observed", False)),
        evidence=tuple(data.get("evidence") or ()),
        error_kind=str(data.get("error_kind") or ""),
        observer_reason=str(data.get("observer_reason") or ""),
        publicized=bool(data.get("publicized", False)),
    )


def build_provider_registry(
    *,
    action_executor: Any = None,
    speech_engine: Any = None,
    memory: Any = None,
    budget_governor: Any = None,
) -> ProviderRegistry:
    """Dựng registry từ component hiện có; component vắng mặt được bỏ qua."""

    registry = ProviderRegistry()
    if action_executor is not None:
        registry.register("action", ActionExecutorProvider(action_executor))
    if speech_engine is not None:
        registry.register("speech", SpeechEngineProvider(speech_engine))
    if memory is not None:
        registry.register("memory", MemoryKernelProvider(memory))
    if budget_governor is not None:
        registry.register("budget", TokenPressureBudgetProvider(budget_governor))
    return registry


def register_or_replace(registry: ProviderRegistry, kind: str, provider: Any) -> None:
    """Đăng ký lại provider khi engine được hot-reload."""

    replace = getattr(registry, "replace", None)
    if callable(replace):
        replace(kind, provider)
    else:  # Tương thích registry Phase 4 nếu file được cherry-pick riêng.
        registry.register(kind, provider)


def _first_callable(owner: Any, names: tuple[str, ...]):
    for name in names:
        method = getattr(owner, name, None)
        if callable(method):
            return method
    return None


def _first_callable_name(owner: Any, names: tuple[str, ...]) -> str:
    for name in names:
        if callable(getattr(owner, name, None)):
            return name
    return ""


def _safe_call(owner: Any, name: str) -> Any:
    method = getattr(owner, name, None)
    if not callable(method):
        return ""
    try:
        return method()
    except Exception:
        return ""


__all__ = [
    "ActionExecutorProvider",
    "MemoryKernelProvider",
    "SpeechEngineProvider",
    "TokenPressureBudgetProvider",
    "build_provider_registry",
    "register_or_replace",
]

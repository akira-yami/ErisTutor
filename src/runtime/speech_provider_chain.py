# -*- coding: utf-8 -*-
"""Chuỗi fallback TTS độc lập provider và khóa cùng ngôn ngữ.

TTS chính vẫn có thể là Gemini, nhưng Edge/SAPI không còn được gọi bằng hai khối
``try/except`` rải trong engine. Mỗi backend trở thành một provider nhỏ có health,
metadata locale và lỗi riêng. Router chỉ thử hữu hạn, không dịch transcript, và
loại audio sai locale trước khi nó kịp biến Eris thành một người khác giữa câu.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from runtime.speech_contract import normalize_language


@dataclass(frozen=True, slots=True)
class SpeechFallbackRequest:
    text: str
    language: str
    locale: str
    voice_family: str = ""
    cause: str = ""


@dataclass(frozen=True, slots=True)
class SpeechProviderHealth:
    ready: bool
    provider: str
    reason: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SpeechProviderResponse:
    audio: bytes
    provider: str
    locale: str
    voice: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SpeechProviderAttempt:
    provider: str
    success: bool
    locale: str = ""
    voice: str = ""
    error_kind: str = ""
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class SpeechFallbackOutcome:
    audio: bytes = b""
    provider: str = ""
    locale: str = ""
    voice: str = ""
    attempts: tuple[SpeechProviderAttempt, ...] = ()

    @property
    def success(self) -> bool:
        return bool(self.audio)


@runtime_checkable
class FallbackSpeechProvider(Protocol):
    name: str

    def synthesize(self, request: SpeechFallbackRequest) -> SpeechProviderResponse: ...
    def health(self) -> SpeechProviderHealth: ...


class CallableSpeechProvider:
    """Bọc backend cũ bằng callable mà không làm lộ chi tiết vào router."""

    def __init__(
        self,
        name: str,
        synthesize_fn: Callable[[SpeechFallbackRequest], Any],
        *,
        enabled_fn: Callable[[], bool] | None = None,
        health_details_fn: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.name = str(name or "unknown").strip().lower() or "unknown"
        self._synthesize_fn = synthesize_fn
        self._enabled_fn = enabled_fn or (lambda: True)
        self._health_details_fn = health_details_fn

    def health(self) -> SpeechProviderHealth:
        try:
            ready = bool(self._enabled_fn())
        except Exception as exc:
            return SpeechProviderHealth(False, self.name, type(exc).__name__)
        details: Mapping[str, Any] = {}
        if self._health_details_fn is not None:
            try:
                details = dict(self._health_details_fn() or {})
            except Exception:
                details = {}
        return SpeechProviderHealth(ready, self.name, "" if ready else "disabled", details)

    def synthesize(self, request: SpeechFallbackRequest) -> SpeechProviderResponse:
        raw = self._synthesize_fn(request)
        return self._normalize(raw, request)

    def _normalize(self, raw: Any, request: SpeechFallbackRequest) -> SpeechProviderResponse:
        if isinstance(raw, SpeechProviderResponse):
            return raw
        audio = b""
        metadata: dict[str, Any] = {}
        if isinstance(raw, tuple):
            if raw:
                audio = bytes(raw[0] or b"")
            if len(raw) > 1:
                second = raw[1]
                if isinstance(second, Mapping):
                    metadata = dict(second)
                elif second:
                    metadata = {"voice": str(second)}
        elif isinstance(raw, Mapping):
            metadata = dict(raw)
            audio = bytes(metadata.pop("audio", b"") or b"")
        else:
            audio = bytes(raw or b"")
        return SpeechProviderResponse(
            audio=audio,
            provider=self.name,
            locale=str(metadata.get("locale") or request.locale or "").strip(),
            voice=str(metadata.get("voice") or "").strip(),
            details=metadata,
        )


class SameLanguageSpeechFallbackChain:
    """Thử provider theo thứ tự nhưng giữ language/locale của turn.

    Router không retry provider nội bộ. Mỗi backend được gọi tối đa một lần; retry
    mạng thuộc chính backend hoặc circuit breaker của nó. Nhờ vậy không có retry
    chồng tầng, thứ rất thích nhân một lỗi 429 thành một lễ hội 429.
    """

    def __init__(self, providers: tuple[FallbackSpeechProvider, ...] | list[FallbackSpeechProvider]) -> None:
        self.providers = tuple(providers)

    def health(self) -> dict[str, SpeechProviderHealth]:
        output: dict[str, SpeechProviderHealth] = {}
        for provider in self.providers:
            try:
                output[provider.name] = provider.health()
            except Exception as exc:
                output[getattr(provider, "name", type(provider).__name__)] = SpeechProviderHealth(
                    False, getattr(provider, "name", type(provider).__name__), type(exc).__name__
                )
        return output

    def synthesize(self, request: SpeechFallbackRequest) -> SpeechFallbackOutcome:
        clean = str(request.text or "").strip()
        if not clean:
            return SpeechFallbackOutcome()
        expected_language = normalize_language(request.language, text=clean)
        expected_locale = str(request.locale or _default_locale(expected_language)).strip()
        normalized_request = SpeechFallbackRequest(
            text=clean,
            language=expected_language,
            locale=expected_locale,
            voice_family=str(request.voice_family or "").strip(),
            cause=str(request.cause or "").strip(),
        )
        attempts: list[SpeechProviderAttempt] = []
        for provider in self.providers:
            health = provider.health()
            if not health.ready:
                attempts.append(SpeechProviderAttempt(provider=health.provider, success=False, error_kind=health.reason or "disabled"))
                continue
            started = time.perf_counter()
            try:
                response = provider.synthesize(normalized_request)
                duration_ms = (time.perf_counter() - started) * 1000.0
                actual_locale = str(response.locale or "").strip()
                if not response.audio:
                    attempts.append(SpeechProviderAttempt(
                        provider=response.provider, success=False, locale=actual_locale,
                        voice=response.voice, error_kind="empty_audio", duration_ms=duration_ms,
                    ))
                    _record(response.provider, actual_locale, expected_locale, False, "empty_audio")
                    continue
                if not locale_matches_language(actual_locale, expected_language):
                    attempts.append(SpeechProviderAttempt(
                        provider=response.provider, success=False, locale=actual_locale,
                        voice=response.voice, error_kind="locale_mismatch", duration_ms=duration_ms,
                    ))
                    _record(response.provider, actual_locale, expected_locale, False, "locale_mismatch")
                    continue
                attempts.append(SpeechProviderAttempt(
                    provider=response.provider, success=True, locale=actual_locale,
                    voice=response.voice, duration_ms=duration_ms,
                ))
                _record(response.provider, actual_locale, expected_locale, True, normalized_request.cause)
                return SpeechFallbackOutcome(
                    audio=response.audio,
                    provider=response.provider,
                    locale=actual_locale,
                    voice=response.voice,
                    attempts=tuple(attempts),
                )
            except Exception as exc:
                duration_ms = (time.perf_counter() - started) * 1000.0
                attempts.append(SpeechProviderAttempt(
                    provider=getattr(provider, "name", type(provider).__name__),
                    success=False,
                    error_kind=type(exc).__name__,
                    duration_ms=duration_ms,
                ))
                _record(getattr(provider, "name", type(provider).__name__), "", expected_locale, False, type(exc).__name__)
        return SpeechFallbackOutcome(attempts=tuple(attempts))


def locale_matches_language(locale: str, language: str) -> bool:
    actual = normalize_language(locale)
    expected = normalize_language(language)
    return actual == expected


def _default_locale(language: str) -> str:
    return {"vi": "vi-VN", "ja": "ja-JP", "en": "en-US"}.get(normalize_language(language), language)


def _record(provider: str, locale: str, expected_locale: str, success: bool, reason: str) -> None:
    try:
        from runtime.hardening_metrics import record_tts_fallback
        record_tts_fallback(
            provider=str(provider or "unknown"),
            locale=str(locale or ""),
            expected_locale=str(expected_locale or ""),
            success=bool(success),
            reason=str(reason or ""),
        )
    except Exception:
        pass


__all__ = [
    "CallableSpeechProvider",
    "FallbackSpeechProvider",
    "SameLanguageSpeechFallbackChain",
    "SpeechFallbackOutcome",
    "SpeechFallbackRequest",
    "SpeechProviderAttempt",
    "SpeechProviderHealth",
    "SpeechProviderResponse",
    "locale_matches_language",
]

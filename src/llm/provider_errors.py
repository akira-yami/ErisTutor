from __future__ import annotations

"""Phân loại lỗi provider dùng chung cho LLM/TTS/localizer."""

from enum import StrEnum
from typing import Any


class ProviderErrorKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    KEY_REVOKED = "key_revoked"
    PROJECT_PERMISSION = "project_permission"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE_SHAPE = "invalid_response_shape"
    TRANSIENT_SERVER = "transient_server"
    UNKNOWN = "unknown"


def classify_provider_error(exc: Exception | str, *, status_code: int | None = None) -> ProviderErrorKind:
    text = _error_text(exc).lower()
    code = status_code if status_code is not None else _status_from_exception(exc)
    if code == 429 or "resource_exhausted" in text or "quota" in text and "exceeded" in text:
        return ProviderErrorKind.RATE_LIMIT
    if any(marker in text for marker in ("api key not valid", "api key was reported as leaked", "reported as leaked", "api_key_invalid", "invalid credential", "api key expired", "unauthorized")):
        return ProviderErrorKind.KEY_REVOKED
    if code in (401, 403):
        if code == 403 or any(m in text for m in ("permission_denied", "permission denied", "api has not been used", "disabled", "not enabled", "forbidden")):
            # 403 có thể do project/billing/scope, không chứng minh key bị thu hồi.
            return ProviderErrorKind.PROJECT_PERMISSION
        return ProviderErrorKind.KEY_REVOKED
    if any(marker in text for marker in ("audio parts hợp lệ", "không trả audio parts", "nonetype") ):
        return ProviderErrorKind.INVALID_RESPONSE_SHAPE
    if code in (500, 502, 503, 504) or "unavailable" in text:
        return ProviderErrorKind.TRANSIENT_SERVER
    if code is not None and 400 <= code < 500:
        return ProviderErrorKind.INVALID_REQUEST
    return ProviderErrorKind.UNKNOWN


def _status_from_exception(exc: Any) -> int | None:
    for attr in ("code", "status_code", "status"):
        try:
            value = getattr(exc, attr, None)
            if value is not None:
                return int(value)
        except (AttributeError, TypeError, ValueError, OverflowError):
            pass
    response = getattr(exc, "response", None)
    try:
        value = getattr(response, "status_code", None)
        if value is not None:
            return int(value)
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass
    return None


def _error_text(exc: Any) -> str:
    parts = [str(exc or "")]
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            parts.append(str(response.text or ""))
        except (AttributeError, TypeError, ValueError, OverflowError):
            pass
    return " ".join(parts)


class ProviderUnavailable(RuntimeError):
    """Lỗi công khai ở biên Root/Writer, KHÔNG chứa URL, key hay prompt.

    ``kind`` phân biệt bản chất HTTP; exception gốc chỉ được giữ dưới
    ``__cause__`` trong process, không serialize lên UI hay logs.
    """

    def __init__(self, kind: ProviderErrorKind, *, role: str = "unknown") -> None:
        self.kind = kind
        self.role = role
        super().__init__(f"provider_unavailable:{kind.value}:{role}")


def provider_failure(exc: Exception, *, role: str = "unknown") -> ProviderUnavailable | None:
    """Chỉ đổi lỗi provider thực sự thành unavailable, không che lỗi lập trình.

    Timeout/ConnectionError từ requests là tín hiệu mạng; HTTPError dựa vào
    status. Không suy đoán từ chuỗi chứa thông tin nhạy cảm.
    """
    import requests

    if isinstance(exc, ProviderUnavailable):
        return exc
    if isinstance(exc, (requests.Timeout, requests.ConnectionError, TimeoutError)):
        return ProviderUnavailable(ProviderErrorKind.TRANSIENT_SERVER, role=role)
    try:
        from runtime.provider_health import ProviderUnavailableFast
        if isinstance(exc, ProviderUnavailableFast):
            return ProviderUnavailable(ProviderErrorKind.TRANSIENT_SERVER, role=role)
    except ImportError:
        pass
    try:
        from llm.key_pool import ProviderCircuitOpen
        if isinstance(exc, ProviderCircuitOpen):
            return ProviderUnavailable(ProviderErrorKind.TRANSIENT_SERVER, role=role)
    except ImportError:
        pass
    if isinstance(exc, requests.HTTPError):
        return ProviderUnavailable(classify_provider_error(exc), role=role)
    return None

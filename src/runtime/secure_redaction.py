# -*- coding: utf-8 -*-
"""Redaction dùng chung cho log, telemetry và bề mặt công khai.

Mục tiêu là không để mỗi subsystem tự nghĩ ra một bộ regex rồi vô tình bỏ sót
đúng loại bí mật mà subsystem khác vừa ghi ra. Module này không lưu raw secret;
trong log chỉ còn fingerprint ngắn để đối chiếu sự cố mà không biến log thành
một bản sao kho khóa.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|token|secret|password|passwd|authorization|cookie|session[_-]?id|"
    r"private[_-]?key|client[_-]?secret|connection[_-]?string)", re.I
)
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b")),
    ("openai_api_key", re.compile(r"\bsk-[0-9A-Za-z_-]{16,}\b", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer", re.compile(r"\bBearer\s+[0-9A-Za-z._~+/-]{12,}", re.I)),
    ("credential_assignment", re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|passwd)\s*[:=]\s*[^\s,;]+",
        re.I,
    )),
)
_SECRET_QUERY = re.compile(r"([?&](?:key|api_key|token|secret|password)=)[^&#\s]+", re.I)
_PRIVATE_URL = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d+\.\d+\.\d+|"
    r"192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)(?::\d+)?\S*",
    re.I,
)
_WINDOWS_PATH = re.compile(r"(?<!\w)[A-Za-z]:\\(?:[^\s<>:\"|?*]+\\)*[^\s<>:\"|?*]*")
_UNIX_PATH = re.compile(r"(?<!\w)/(?:home|mnt|tmp|var|etc)/[^\s]+")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


def fingerprint(value: Any, *, length: int = 12) -> str:
    raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[: max(6, int(length))]


def _replacement(value: str, kind: str) -> str:
    return f"[đã ẩn:{kind}:{fingerprint(value)}]"


@dataclass(frozen=True, slots=True)
class RedactionResult:
    value: Any
    blocked_kinds: tuple[str, ...] = ()


class SecureRedactor:
    def __init__(self, *, redact_pii: bool | None = None, redact_internal_paths: bool = True) -> None:
        if redact_pii is None:
            redact_pii = str(os.getenv("ERIS_REDACT_PII_LOGS", "1")).strip().lower() not in {"0", "false", "off", "no"}
        self.redact_pii = bool(redact_pii)
        self.redact_internal_paths = bool(redact_internal_paths)

    def text(self, value: Any) -> RedactionResult:
        out = str(value or "")
        blocked: list[str] = []
        for kind, pattern in _PATTERNS:
            def repl(match: re.Match[str], *, _kind: str = kind) -> str:
                blocked.append(_kind)
                return _replacement(match.group(0), _kind)
            out = pattern.sub(repl, out)

        def query_repl(match: re.Match[str]) -> str:
            blocked.append("secret_query")
            return match.group(1) + "[đã ẩn]"
        out = _SECRET_QUERY.sub(query_repl, out)

        def private_url_repl(match: re.Match[str]) -> str:
            blocked.append("private_url")
            return "[URL nội bộ đã ẩn]"
        out = _PRIVATE_URL.sub(private_url_repl, out)

        if self.redact_internal_paths:
            for pattern in (_WINDOWS_PATH, _UNIX_PATH):
                out, count = pattern.subn("[đường dẫn nội bộ đã ẩn]", out)
                if count:
                    blocked.append("internal_path")

        if self.redact_pii:
            def email_repl(match: re.Match[str]) -> str:
                blocked.append("email")
                return _replacement(match.group(0), "email")
            out = _EMAIL.sub(email_repl, out)
        return RedactionResult(out, tuple(dict.fromkeys(blocked)))

    def value(self, value: Any, *, key_hint: str = "", depth: int = 0) -> RedactionResult:
        if depth > 8:
            return RedactionResult("[đã rút gọn]", ("max_depth",))
        if key_hint and _SENSITIVE_KEY.search(str(key_hint)):
            return RedactionResult(_replacement(str(value or ""), "secret_field"), ("secret_field",))
        if isinstance(value, Mapping):
            output: dict[str, Any] = {}
            blocked: list[str] = []
            for index, (key, item) in enumerate(value.items()):
                if index >= 64:
                    blocked.append("mapping_truncated")
                    break
                result = self.value(item, key_hint=str(key), depth=depth + 1)
                output[str(key)] = result.value
                blocked.extend(result.blocked_kinds)
            return RedactionResult(output, tuple(dict.fromkeys(blocked)))
        if isinstance(value, (list, tuple, set)):
            output = []
            blocked: list[str] = []
            for item in list(value)[:64]:
                result = self.value(item, depth=depth + 1)
                output.append(result.value)
                blocked.extend(result.blocked_kinds)
            return RedactionResult(output, tuple(dict.fromkeys(blocked)))
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, (bool, int, float)) or value is None:
            return RedactionResult(value)
        return self.text(value)


GLOBAL_SECURE_REDACTOR = SecureRedactor()

__all__ = ["GLOBAL_SECURE_REDACTOR", "RedactionResult", "SecureRedactor", "fingerprint"]

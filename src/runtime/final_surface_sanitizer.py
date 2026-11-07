# -*- coding: utf-8 -*-
"""Sanitizer duy nhất ở cửa ra public/UI/TTS/memory.

Các transformer chuyên biệt vẫn giữ nhiệm vụ riêng. Sanitizer này là van cuối,
chặn dữ liệu nội bộ, secret, stack trace và stage direction tự do trước khi text
rời runtime. Nó cố ý fail-closed với dữ liệu nhạy cảm, nhưng không bóp câu bình
thường thành lời của tổng đài.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from runtime.secure_redaction import GLOBAL_SECURE_REDACTOR


_SECRET_PATTERNS = (
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bsk-[0-9A-Za-z_-]{16,}\b", re.I),
    re.compile(r"\b(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*[^\s,;]+", re.I),
    re.compile(r"\b(?:Bearer)\s+[0-9A-Za-z._~-]{12,}\b", re.I),
)
_RAW_OBJECT_RE = re.compile(
    r"\b(?:MemoryPacket|RawRecallResult|RecallAnswer|MemoryQueryIR|SimpleNamespace|Namespace)\([^\n]{0,800}\)",
    re.I,
)
_STACK_TRACE_RE = re.compile(
    r"(?:Traceback \(most recent call last\):.*?)(?=(?:\n\S)|\Z)", re.I | re.S,
)
_PRIVATE_URL_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d+\.\d+\.\d+|"
    r"192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)(?::\d+)?\S*",
    re.I,
)
_SECRET_QUERY_RE = re.compile(r"https?://\S*[?&](?:key|token|secret|password)=[^\s&]+\S*", re.I)
_WINDOWS_PATH_RE = re.compile(r"(?<!\w)[A-Za-z]:\\(?:[^\s<>:\"|?*]+\\)*[^\s<>:\"|?*]*")
_UNIX_INTERNAL_PATH_RE = re.compile(r"(?<!\w)/(?:home|mnt|tmp|var|etc)/[^\s]+")
_STAGE_PAREN_RE = re.compile(r"[\(\*]\s*(?P<body>[^\)\*]{1,120})\s*[\)\*]", re.S)
_STAGE_WORDS = (
    "cười", "nháy mắt", "nhíu mắt", "thở dài", "thở nhẹ", "im lặng", "nghiêng đầu",
    "nhìn sang", "nhìn xuống", "liếc", "gật", "lắc đầu", "vỗ tay", "nhún vai", "hát",
    "laugh", "wink", "sigh", "whisper", "look away", "look down", "tilt head", "clap", "shrug", "sing",
)
_INTERNAL_PREFIX_RE = re.compile(
    r"^\s*(?:debug|trace|reasoning|analysis|internal|system prompt|developer message)\s*[:=].*$",
    re.I | re.M,
)


def _enabled() -> bool:
    try:
        from runtime.rollout_control import feature_enabled
        return feature_enabled("stage_final_sanitizer")
    except Exception:
        return os.environ.get("ERIS_STAGE_FINAL_SANITIZER", "1").strip().lower() not in {
            "0", "false", "off", "no",
        }


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


@dataclass(frozen=True, slots=True)
class SanitizeResult:
    text: str
    blocked_kinds: tuple[str, ...] = ()


_CODE_FENCE_RE = re.compile(r"(?:```[\w.+-]*\n?[\s\S]*?(?:```|\Z)|~~~[\w.+-]*\n?[\s\S]*?(?:~~~|\Z))", re.S)


class FinalSurfaceSanitizer:
    def sanitize(self, text: str, *, surface: str = "display") -> SanitizeResult:
        raw = str(text or "")
        if not raw or not _enabled():
            return SanitizeResult(raw)

        blocked: list[str] = []
        code_blocks: list[str] = []
        working = raw
        if surface == "display" and ("```" in working or "~~~" in working):
            def _save_code(m: re.Match[str]) -> str:
                idx = len(code_blocks)
                code_text = m.group(0)
                # Vẫn ẩn secret nếu lộ trong code block
                for pattern in _SECRET_PATTERNS:
                    code_text, sec_cnt = pattern.subn("[đã ẩn bí mật]", code_text)
                    if sec_cnt:
                        blocked.append("secret")
                code_blocks.append(code_text)
                return f"\n__ERIS_SAN_CODE_{idx}__\n"
            working = _CODE_FENCE_RE.sub(_save_code, working)

        secure = GLOBAL_SECURE_REDACTOR.text(working)
        out = str(secure.value)
        blocked.extend(secure.blocked_kinds)
        if any(kind in {"google_api_key", "openai_api_key", "jwt", "bearer", "credential_assignment", "secret_query", "secret_field"} for kind in secure.blocked_kinds):
            # Giữ contract metric/test cũ: mọi loại credential cụ thể cũng thuộc
            # nhóm tổng quát ``secret``.
            blocked.append("secret")

        out, count = _STACK_TRACE_RE.subn(" ", out)
        if count:
            blocked.append("stack_trace")
        out, count = _RAW_OBJECT_RE.subn(" ", out)
        if count:
            blocked.append("raw_object")
        out, count = _INTERNAL_PREFIX_RE.subn(" ", out)
        if count:
            blocked.append("internal_prefix")

        for pattern in _SECRET_PATTERNS:
            out, count = pattern.subn("[đã ẩn bí mật]" if surface == "display" else " ", out)
            if count:
                blocked.append("secret")

        for pattern, kind in (
            (_SECRET_QUERY_RE, "secret_url"),
            (_PRIVATE_URL_RE, "private_url"),
            (_WINDOWS_PATH_RE, "internal_path"),
            (_UNIX_INTERNAL_PATH_RE, "internal_path"),
        ):
            replacement = "[dữ liệu nội bộ đã ẩn]" if surface == "display" else " "
            out, count = pattern.subn(replacement, out)
            if count:
                blocked.append(kind)

        def stage_repl(match: re.Match[str]) -> str:
            body = _squash(match.group("body")).lower()
            if any(word in body for word in _STAGE_WORDS):
                blocked.append("stage_direction")
                return " "
            return match.group(0)

        out = _STAGE_PAREN_RE.sub(stage_repl, out)

        blocked_kinds = tuple(dict.fromkeys(blocked))
        try:
            from runtime.hardening_metrics import record_surface_sanitizer
            record_surface_sanitizer(surface=surface, blocked_kinds=blocked_kinds)
        except Exception:
            pass

        if surface == "display" and (code_blocks or "\n" in out):
            clean_out = re.sub(r"[ \t\f\v]+", " ", out)
            clean_out = re.sub(r" *\n *", "\n", clean_out)
            clean_out = re.sub(r"\n{3,}", "\n\n", clean_out).strip()
            for idx, cb in enumerate(code_blocks):
                clean_out = clean_out.replace(f"__ERIS_SAN_CODE_{idx}__", cb.strip())
            return SanitizeResult(clean_out, blocked_kinds)

        if code_blocks:
            for idx, cb in enumerate(code_blocks):
                out = out.replace(f"__ERIS_SAN_CODE_{idx}__", cb.strip())
        return SanitizeResult(_squash(out), blocked_kinds)


__all__ = ["FinalSurfaceSanitizer", "SanitizeResult"]

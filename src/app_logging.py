# -*- coding: utf-8 -*-
"""Cấu hình log tập trung cho Eris.

Mặc định dùng profile ``clean``: giữ các mốc quan trọng và mọi WARNING/ERROR,
nhưng ẩn telemetry lặp dày như preflight, raw JSON, observer và parser trace.
Đặt ``ERIS_LOG_PROFILE=normal`` để xem nhiều hơn hoặc ``debug`` để xem tất cả.
"""

from __future__ import annotations

import logging
import os
import sys

from runtime.secure_redaction import GLOBAL_SECURE_REDACTOR


class CategoryFormatter(logging.Formatter):
    def format(self, record):
        if not hasattr(record, "category"):
            record.category = record.name
        return super().format(record)


class SecureLogFilter(logging.Filter):
    """Redact message đã render trước khi formatter ghi ra console/file."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
            result = GLOBAL_SECURE_REDACTOR.text(rendered)
            record.msg = result.value
            record.args = ()
            if result.blocked_kinds:
                record.redacted_kinds = ",".join(result.blocked_kinds)
        except Exception:
            # Redaction lỗi thì không làm sập logger; formatter vẫn xử lý record cũ.
            pass
        return True


class RuntimeNoiseFilter(logging.Filter):
    """Lọc log INFO lặp dày mà không che cảnh báo thật.

    Bộ lọc chỉ tác động lên INFO/DEBUG. WARNING trở lên luôn được giữ nguyên.
    Đây là lớp trình bày log, không thay đổi hành vi runtime.
    """

    _CLEAN_PREFIXES = (
        "[LLM PREFLIGHT]",
        "[PROMPT BUDGET]",
        "[Observer]",
        "[wake governor]",
        "[directive debug]",
        "[directive parser]",
        "[chat debug]",
        "[chat parser]",
        "[force_speak debug]",
        "[force_speak parser]",
        "[session_ritual raw]",
        "[directive raw]",
        "[directive_inner_thought raw]",
        "[force_speak raw]",
        "[chat raw]",
        "[CHAT HISTORY]",
        "[LLM CHAT HISTORY]",
        "[LAST USER INTENT]",
        "[ACTIVE_TOPIC]",
        "[TTS:event] audio_start",
        "[TTS:event] audio_done",
        "Bắt đầu tạo chỉ thị AI...",
        # Các dòng khai báo dịch vụ chỉ hữu ích khi chẩn đoán cấu hình. Ở
        # profile clean, giữ lại WARNING/ERROR và một dòng startup tổng hợp.
        "Đang dùng remote API làm cognition chính.",
        "Dung OpenAI-compatible API tai",
        "Dung Gemini speech API tai",
        "Speech/critic client đã sẵn sàng",
        "Dung vision API tai",
        "Vision facts client đã sẵn sàng",
        "Chat fallback chain:",
        "Escalation client (Gemini) san sang",
        "Escalation client (bái sư) đã sẵn sàng",
        "Mô hình đã sẵn sàng.",
        "[depth] auto-enable",
        "Epistemic V2 ready",
        "[vision structure]",
        "[vision caption]",
        "Model inference scheduler đã bọc",
        "[PrivateMind API] ready",
        "[InnerDiary fallback] ready",
        "tutor: Master_Switch",
        "tutor: KnowledgeProvider",
        "[TTS] Khởi động.",
        "--- Overseer telemetry mode",
        "[session_ritual] sent greeting",
        "[session_ritual] sent LLM greeting",
    )

    _NORMAL_PREFIXES = (
        "[LLM PREFLIGHT]",
        "[session_ritual raw]",
        "[directive raw]",
        "[directive_inner_thought raw]",
        "[force_speak raw]",
        "[chat raw]",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True

        profile = os.environ.get("ERIS_LOG_PROFILE", "clean").strip().lower()
        if profile in {"debug", "trace", "verbose"}:
            return True

        message = record.getMessage().strip()
        prefixes = self._NORMAL_PREFIXES if profile == "normal" else self._CLEAN_PREFIXES
        return not message.startswith(prefixes)


def _resolve_level(default: int) -> int:
    raw = os.environ.get("ERIS_LOG_LEVEL", "").strip().upper()
    if not raw:
        return default
    value = getattr(logging, raw, None)
    return value if isinstance(value, int) else default


def configure_logging(level: int = logging.INFO) -> None:
    """Thiết lập root logger một lần, hỗ trợ profile clean/normal/debug."""

    profile = os.environ.get("ERIS_LOG_PROFILE", "clean").strip().lower()
    resolved_level = _resolve_level(logging.DEBUG if profile in {"debug", "trace", "verbose"} else level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CategoryFormatter("[%(category)s][%(levelname)s] %(message)s"))
    handler.addFilter(SecureLogFilter())
    handler.addFilter(RuntimeNoiseFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(resolved_level)
    root.addHandler(handler)


def get_logger(category: str) -> logging.LoggerAdapter:
    logger = logging.getLogger(category)
    return logging.LoggerAdapter(logger, {"category": category})


__all__ = ["CategoryFormatter", "RuntimeNoiseFilter", "SecureLogFilter", "configure_logging", "get_logger"]

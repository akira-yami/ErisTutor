# -*- coding: utf-8 -*-
"""Boundary public-expression do user nói rõ, không phải nguồn quyền của ERIS.

ERIS tự sở hữu expression và mặc định có thể chủ động biểu đạt. Module này chỉ
nhận directive sản phẩm rõ ràng như "im đi" / "cứ nói đi" để đóng hoặc mở *cửa
phát public*. Nó không tắt cognition, không xóa desire và không cấp quyền nói.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any

from runtime.cognition.normalization import normalize_vi
from runtime.runtime_settings_store import get_runtime_settings_store


@dataclass(frozen=True, slots=True)
class ExpressionBoundaryPreferenceResult:
    applied: bool = False
    mode: str = ""
    reason: str = ""
    prompt_context: str = ""


# Các phrase này mô tả hành vi sản phẩm, không phải chủ đề tán gẫu. Giữ danh
# sách nhỏ và semantic, tránh biến mọi chữ "nói" thành lệnh bật radio 24/7.
_ENABLE_MARKERS = (
    "loa phat thanh tu dong",
    "tu dong noi",
    "tu noi nhieu hon",
    "noi chu dong",
    "mo len la noi",
    "mo ban len la can co tieng",
    "co tieng la duoc",
    "noi linh tinh di",
    "noi linh tinh ik",
    "cu noi di",
)
_DISABLE_MARKERS = (
    "dung noi tu dong",
    "tat loa tu dong",
    "im di",
    "de toi yen",
    "dung noi linh tinh",
)


def _normalized(text: str) -> str:
    return " ".join(normalize_vi(str(text or "")).split()).strip().lower()


def detect_expression_boundary_directive(user_text: str) -> str:
    """Trả ``enable``/``disable``/``""`` cho directive rõ ràng của user."""

    text = _normalized(user_text)
    if not text:
        return ""
    # Chat đời thường kéo dài chữ ("tiếnggg", "nammmm"). Co cụm từ ba ký
    # tự lặp trở lên để directive không biến mất chỉ vì user đang gõ có cảm xúc.
    text = re.sub(r"(.)\1{2,}", r"\1", text)
    if any(marker in text for marker in _DISABLE_MARKERS):
        return "disable"
    if any(marker in text for marker in _ENABLE_MARKERS):
        return "enable"
    # Câu dài hơn có đủ ba ý: tự động + nói/tiếng + mở/chạy.
    if "tu dong" in text and any(token in text for token in ("noi", "tieng", "loa")):
        return "enable"
    return ""


def apply_expression_boundary_preference(host: Any, user_text: str) -> ExpressionBoundaryPreferenceResult:
    """Áp directive vào runtime settings và trả context cho public writer.

    Boundary này chỉ điều khiển cửa public output. Nó không hạ pressure, không
    tắt initiative và không xóa commitment. Khi cửa đóng, cognition vẫn chạy và
    expression được DEFER thay vì bị diễn giải thành rejection.
    """

    mode = detect_expression_boundary_directive(user_text)
    if not mode:
        return ExpressionBoundaryPreferenceResult()

    enabled = mode == "enable"
    patch = {
        "commentary": {
            # V66: đây là explicit output boundary. Default của schema là True;
            # False chỉ xuất hiện sau directive rõ ràng của user.
            "autonomous_expression_enabled": enabled,
            "preference_updated_at": int(time.time()),
            # Cadence chỉ là self-rhythm preference khi cửa expression đang mở.
            # Không đụng ``initiative.enabled``/``commentary.enabled`` để nội tâm
            # và desire của Eris tiếp tục tồn tại ngay cả khi user muốn yên lặng.
            "desired_interval_seconds": 35 if enabled else 60,
            "maximum_quiet_seconds": 70 if enabled else 120,
            "startup_delay_seconds": 5 if enabled else 8,
        },
    }
    try:
        store = getattr(host, "runtime_settings_store", None) or get_runtime_settings_store(
            logger=getattr(host, "logger", None)
        )
        result = store.apply_patch(patch, materialize_env=True)
        setattr(host, "runtime_settings", result.settings)
    except Exception as exc:  # policy failure must not kill chat
        logger = getattr(host, "logger", None)
        if logger is not None:
            try:
                logger.warning("[expression boundary] apply failed: %s", exc)
            except Exception:
                pass
        return ExpressionBoundaryPreferenceResult(
            applied=False,
            mode=mode,
            reason="settings_apply_failed",
        )

    setattr(host, "_autonomous_expression_boundary_open", enabled)
    context = (
        "expression_boundary: open; source=user_directive. ERIS expression remains self-owned; "
        "the user only reopened public output. Acknowledge briefly; do not frame this as permission to exist."
        if enabled
        else
        "expression_boundary: closed; source=user_directive. Public autonomous expression is deferred, "
        "while private cognition and self-owned desires continue."
    )
    logger = getattr(host, "logger", None)
    if logger is not None:
        try:
            logger.info(
                "[expression boundary] applied mode=%s desired=%ss max_quiet=%ss",
                mode,
                35 if enabled else 60,
                70 if enabled else 120,
            )
        except Exception:
            pass
    wake = getattr(host, "_wake_autonomous_loop", None)
    if enabled and callable(wake):
        try:
            wake("expression_boundary_reopened")
        except Exception:
            pass
    return ExpressionBoundaryPreferenceResult(
        applied=True,
        mode=mode,
        reason="user_directive",
        prompt_context=context,
    )


__all__ = [
    "ExpressionBoundaryPreferenceResult",
    "detect_expression_boundary_directive",
    "apply_expression_boundary_preference",
]

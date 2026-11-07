# -*- coding: utf-8 -*-
"""Theo dõi riêng lời đã HIỂN THỊ và lời đã PHÁT XONG.

Transport chấp nhận một public surface không có nghĩa TTS đã bắt đầu/hoàn tất.
Theo yêu cầu 18/10/2026, bubble đợi playback start; history pending vẫn theo dõi
receipt riêng, không lưu một câu bị ngắt như một lượt đã đọc trọn vẹn.

Không tạo governor thứ hai: bộ theo dõi chỉ lưu kết quả cơ học theo delivery_id,
không quyết định nội dung, không tự phát âm thanh và không wake continuation.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

_LOCK = threading.RLock()
_TERMINAL = {"delivered", "cancelled", "failed"}
_MAX_TRACKED = 256


def _apply_status(host: Any, delivery_id: str, status: str) -> None:
    """Cập nhật đúng một câu theo ID, không đụng lời của turn kế tiếp."""
    for item in getattr(host, "chat_history", ()) or ():
        if isinstance(item, dict) and str(item.get("delivery_id", "")) == delivery_id:
            item["delivery_status"] = status
    ensure = getattr(host, "_ensure_session_turn_ledger", None)
    if not callable(ensure):
        return
    try:
        ledger = ensure()
        for turn in reversed(ledger.assistant_turns()):
            if str(turn.meta.get("delivery_id", "")) == delivery_id:
                ledger.merge_turn_meta(turn.index, {"delivery_status": status})
                break
    except Exception:
        # Ledger phụ trợ hỏng không được chặn TTS/Kernel.
        pass


def register_public_surface(
    host: Any, *, delivery_id: str,
    on_delivered: Callable[[], None] | None = None,
) -> None:
    """Gọi SAU khi transport chấp nhận và history đã append.

    ``no_tts`` hoặc TTS callback có thể hoàn thành đồng bộ NGAY TRONG send().
    Receipt đến trước commit được giữ và áp ngay tại lúc đăng ký.
    """
    did = str(delivery_id or "")[:180]
    if not did:
        return
    with _LOCK:
        outcomes = getattr(host, "_v12_public_delivery_outcomes", None)
        if not isinstance(outcomes, dict):
            outcomes = {}
            host._v12_public_delivery_outcomes = outcomes
        callbacks = getattr(host, "_v12_public_delivery_callbacks", None)
        if not isinstance(callbacks, dict):
            callbacks = {}
            host._v12_public_delivery_callbacks = callbacks
        current = outcomes.get(did)
        if current is None and callable(on_delivered):
            callbacks[did] = on_delivered
        if len(callbacks) > _MAX_TRACKED:
            for key in list(callbacks)[:-_MAX_TRACKED]:
                callbacks.pop(key, None)
    if current in _TERMINAL:
        _apply_status(host, did, current)
        # Callback một lần duy nhất: outcome sớm không được mất episode journal.
        if current == "delivered" and callable(on_delivered):
            on_delivered()


def note_public_delivery_outcome(host: Any, *, delivery_id: str, status: str) -> bool:
    """Ghi nhận terminal TTS một lần, kể cả kernel run đã bị generation thay.

    Kết quả âm thanh cũ không được thay phase của run mới. Chỉ đánh dấu record
    cùng delivery_id; quyền settle continuation vẫn thuộc ConversationKernel.
    """
    did = str(delivery_id or "")[:180]
    result = str(status or "").strip().lower()
    if not did or result not in _TERMINAL:
        return False
    with _LOCK:
        outcomes = getattr(host, "_v12_public_delivery_outcomes", None)
        if not isinstance(outcomes, dict):
            outcomes = {}
            host._v12_public_delivery_outcomes = outcomes
        if did in outcomes:
            return False
        outcomes[did] = result
        if len(outcomes) > _MAX_TRACKED:
            for key in list(outcomes)[:-_MAX_TRACKED]:
                outcomes.pop(key, None)
        callbacks = getattr(host, "_v12_public_delivery_callbacks", None)
        on_delivered = callbacks.pop(did, None) if isinstance(callbacks, dict) else None
    _apply_status(host, did, result)
    if result == "delivered" and callable(on_delivered):
        on_delivered()
    return True


__all__ = ["register_public_surface", "note_public_delivery_outcome"]

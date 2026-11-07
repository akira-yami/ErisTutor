# -*- coding: utf-8 -*-
"""Chính sách metadata gửi từ Python sang Godot.

Runtime vẫn giữ đầy đủ parse/debug trace trong memory và log debug. Payload công
khai mặc định chỉ chứa những gì body cần để hiện chữ, cử động và phát TTS.
"""

from __future__ import annotations

import os
from typing import Any, Mapping


_INTERNAL_DEBUG_FIELDS = (
    "debug_trace",
    "parser_trace",
    "parse_mode",
    "reasoning_tags",
)


def body_debug_metadata_enabled() -> bool:
    return os.environ.get("ERIS_BODY_DEBUG_METADATA", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def sanitize_body_payload(
    payload: Mapping[str, Any],
    *,
    include_debug: bool | None = None,
) -> dict[str, Any]:
    """Copy payload và bỏ metadata parser khi body không ở chế độ debug."""

    result = dict(payload or {})
    enabled = body_debug_metadata_enabled() if include_debug is None else bool(include_debug)
    if enabled:
        return result
    for key in _INTERNAL_DEBUG_FIELDS:
        result.pop(key, None)
    return result


__all__ = ["body_debug_metadata_enabled", "sanitize_body_payload"]


# Đây là các projection của giao diện (Identity / nhật ký / debug), KHÔNG PHẢI
# contract lời thoại. Chúng có lane ``panel_state_update`` riêng, được compact
# bởi UdpTransport.send_panel_state. Tuyệt đối không ghép chúng vào datagram
# chat: chỉ vài lượt hội thoại là identity_runtime có thể vượt 63 KB.
_PANEL_ONLY_FIELDS = frozenset({
    "identity_runtime", "cognitive_views", "private_mind_digest",
    "private_life_digest", "emotion_debug", "depth_v2",
})

# Phao cuối khi metadata không thuộc panel cũng quá lớn. Bao gồm cả cặp
# system_action/action_input để không vô tình gửi action thiếu tham số.
# Stage protocol chi tiết đã có stage_event_bus độc lập; gói body chỉ cần
# text, cue, trạng thái và các field tương tác với Godot.
_SPEECH_BODY_CORE_FIELDS = frozenset({
    "text", "memory_text", "should_speak", "animation", "direction", "target_x",
    "bounce_action", "next_animation", "system_action", "action_input",
    "motion_intent", "motion_target_x", "motion_facing", "delivery_hint",
    "nonverbal_cue", "stage_motion", "performance_hint", "display_language",
    "speech_text", "speech_language", "performance_intensity", "delivery_priority",
    "interruptible", "utterance_turn_id", "speech_act", "preset_id",
    "audio_asset", "preset_emotion", "action_result", "failure_meta",
    "event_trace_id", "generation_epoch", "public_intent_owner", "stage",
})


def speech_body_udp_bytes(payload: Mapping[str, Any]) -> int:
    """Đo ĐÚNG số bytes JSON theo cùng serializer của UdpTransport."""
    import json
    from transport.udp import _sanitize_udp_payload

    return len(json.dumps(_sanitize_udp_payload(dict(payload)), ensure_ascii=False).encode("utf-8"))


def build_speech_body_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Giới hạn gói body KHÔNG cắt hoặc đổi một ký tự của lời Eris.

    Không ép AI viết ngắn hơn khi panel phình to. Chỉ bỏ UI/debug metadata
    khỏi đường nói. Nếu các trường không thiết yếu còn vượt ngân sách, dùng
    payload core có kiểm soát. Trường hợp cực đoan *bản thân lời thoại* >63KB
    vẫn phải báo lỗi rõ ràng thay vì để UDP tự cắt chữ trong im lặng.
    """
    from transport.udp import MAX_UDP_PAYLOAD_BYTES

    body = sanitize_body_payload(payload)
    for key in _PANEL_ONLY_FIELDS:
        body.pop(key, None)
    # User prompt được stage đọc trước dispatch; gửi lại cho Godot chỉ làm lộ
    # private user text và tăng bytes, trong khi body không đọc field này.
    body.pop("user_text", None)
    if speech_body_udp_bytes(body) <= MAX_UDP_PAYLOAD_BYTES:
        return body

    # Metadata debug/semantic/phân tích có thể lên hàng trăm KB. Không để
    # telemetry cướp quyền nói; giữ nguyên text và những key Godot cần.
    core = {key: value for key, value in body.items()
            if key in _SPEECH_BODY_CORE_FIELDS}
    if speech_body_udp_bytes(core) <= MAX_UDP_PAYLOAD_BYTES:
        return core

    # Stage chi tiết đã phát qua stage_event_bus; rút riêng metadata ấy nếu
    # vẫn lố. Không sửa stage_frame và KHÔNG sửa bản thoại đã niêm phong.
    stage = core.get("stage")
    if isinstance(stage, dict):
        core["stage"] = {key: stage[key] for key in (
            "protocol", "protocol_version", "segment_count", "turn_id"
        ) if key in stage}
    if speech_body_udp_bytes(core) <= MAX_UDP_PAYLOAD_BYTES:
        return core

    raise RuntimeError("speech_body_core_exceeds_udp_budget")

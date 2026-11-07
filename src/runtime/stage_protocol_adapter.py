# -*- coding: utf-8 -*-
"""stage_protocol_adapter.py — đổi object runtime hiện có thành event sân khấu.

Adapter này giữ protocol mới là lớp mỏng: không ép TTS trả audio bytes, không
đổi payload legacy, chỉ chuẩn hóa các mốc frontend cần nghe.
"""

from __future__ import annotations

from typing import Any

from runtime.stage_protocol import StageProtocolEvent


_AUDIO_EVENT_MAP = {
    "audio_prepare": "stage_segment_prepare",
    "audio_queued": "stage_segment_queued",
    "audio_buffer_ready": "tts_audio_ready",
    "audio_start": "audio_playback_started",
    "audio_done": "audio_playback_done",
    "audio_cancelled": "stage_segment_cancelled",
    "audio_failed": "stage_segment_failed",
    "emotion_change": "stage_state",
    "thinking_started": "thinking_started",
    "thinking_finished": "thinking_finished",
    "continuation_cancelled": "continuation_cancelled",
}


def stage_segment_event(stage_frame: Any, *, segment_count: int = 1) -> StageProtocolEvent:
    """Dựng event `stage_segment` từ StageFrame.

    `display_text` là thứ UI nên hiện; `tts_text` để frontend debug/sync nếu cần,
    không phải bắt buộc dùng làm audio.
    """

    data = {
        "segment_count": int(segment_count),
        "actions": list(tuple(getattr(stage_frame, "actions", ()) or ())),
        "prosody_style": str(getattr(stage_frame, "prosody_style", "") or ""),
        "delivery": dict(getattr(stage_frame, "delivery", {}) or {}),
        "memory_text": str(getattr(stage_frame, "memory_text", "") or ""),
        "speech_act": str(getattr(stage_frame, "speech_act", "") or ""),
        "preset_id": str(getattr(stage_frame, "preset_id", "") or ""),
        "audio_asset": str(getattr(stage_frame, "audio_asset", "") or ""),
        "show_id": str((dict(getattr(stage_frame, "show_plan", {}) or {})).get("show_id", "") or ""),
        "show_plan_version": str((dict(getattr(stage_frame, "show_plan", {}) or {})).get("version", "") or ""),
        "segment_index": _seq(stage_frame),
        "body_timeline": dict(getattr(stage_frame, "body_timeline", {}) or {}),
    }
    return StageProtocolEvent(
        type="stage_segment",
        turn_id=str(getattr(stage_frame, "turn_id", "") or ""),
        seq=_seq(stage_frame),
        display_text=str(getattr(stage_frame, "display_text", "") or ""),
        tts_text=str(getattr(stage_frame, "tts_text", "") or ""),
        emotion=str(getattr(stage_frame, "emotion", "") or ""),
        motion=str(getattr(stage_frame, "motion", "") or ""),
        interruptible=bool(getattr(stage_frame, "interruptible", True)),
        source=str(getattr(stage_frame, "source", "") or ""),
        data={key: value for key, value in data.items() if value not in ("", [], {}, None)},
    )



def show_plan_event(stage_frame: Any) -> StageProtocolEvent:
    """Phát timeline cue một lần trước các stage_segment của cùng turn."""
    plan = dict(getattr(stage_frame, "show_plan", {}) or {})
    return StageProtocolEvent(
        type="show_plan",
        turn_id=str(getattr(stage_frame, "turn_id", "") or ""),
        seq=0,
        display_text=str(getattr(stage_frame, "display_text", "") or ""),
        emotion=str(getattr(stage_frame, "emotion", "") or ""),
        motion=str(getattr(stage_frame, "motion", "") or ""),
        interruptible=bool(getattr(stage_frame, "interruptible", True)),
        source=str(getattr(stage_frame, "source", "") or ""),
        data={"show_plan": plan, "segment_count": len(plan.get("cues", [])) if isinstance(plan, dict) else 0},
    )

def playback_event(event: Any, *, stage_frame: Any = None) -> StageProtocolEvent:
    """Dựng event protocol từ lifecycle event của TTS."""

    legacy_type = str(getattr(event, "type", "") or "")
    protocol_type = _AUDIO_EVENT_MAP.get(legacy_type, legacy_type or "stage_event")
    frame = stage_frame
    continuation_motion = _continuation_motion(legacy_type, frame)
    continuation_timeline = _continuation_body_timeline(legacy_type, event, frame, continuation_motion)
    data = {
        "legacy_type": legacy_type,
        "segment_id": _event_seq(event, frame),
        "prosody_style": str(getattr(event, "prosody_style", "") or getattr(frame, "prosody_style", "") or ""),
        "error": str(getattr(event, "error", "") or ""),
        "turn_segments": list(tuple(getattr(event, "turn_segments", ()) or ())),
        "speech_act": str(getattr(frame, "speech_act", "") or ""),
        "preset_id": str(getattr(frame, "preset_id", "") or ""),
        "audio_asset": str(getattr(frame, "audio_asset", "") or ""),
        "body_timeline": continuation_timeline,
        "audio_envelope": list(tuple(getattr(event, "audio_envelope", ()) or ())),
        "audio_mouth_shape": list(tuple(getattr(event, "audio_mouth_shape", ()) or ())),
        "audio_frame_ms": int(getattr(event, "audio_frame_ms", 0) or 0),
        "audio_duration_ms": int(getattr(event, "audio_duration_ms", 0) or 0),
        "continuation_state": (
            "thinking" if legacy_type == "thinking_started"
            else "ready" if legacy_type == "thinking_finished"
            else "cancelled" if legacy_type == "continuation_cancelled"
            else ""
        ),
    }
    turn_id = str(getattr(event, "turn_id", "") or getattr(frame, "turn_id", "") or "")
    seq = _event_seq(event, frame)
    stable_turn = turn_id or "anonymous"
    return StageProtocolEvent(
        type=protocol_type,
        turn_id=turn_id,
        seq=seq,
        event_id=f"audio:{stable_turn}:{seq}:{protocol_type}",
        display_text=str(getattr(frame, "display_text", "") or ""),
        tts_text=str(getattr(event, "text", "") or getattr(frame, "tts_text", "") or ""),
        emotion=str(getattr(event, "emotion", "") or getattr(frame, "emotion", "") or ""),
        motion=continuation_motion or str(getattr(event, "motion", "") or getattr(frame, "motion", "") or ""),
        interruptible=bool(getattr(frame, "interruptible", True)),
        reason=str(getattr(event, "reason", "") or ""),
        source=str(getattr(frame, "source", "") or ""),
        data={key: value for key, value in data.items() if value not in ("", [], {}, None)},
    )



def affect_state_event(
    *,
    affect: dict[str, Any],
    source: str = "runtime",
    thought_pressure: float = 0.0,
    confidence: float = 0.0,
    diary_updated: bool = False,
    intent_id: str = "",
    thread_id: str = "",
) -> StageProtocolEvent:
    """Phát vector affect tối giản cho body/Live2D.

    Event này cố ý không mang nội dung inner diary hay chain-of-thought. Renderer
    chỉ cần state số để tạo dư âm cảm xúc; gửi prose riêng tư sang layer hình thể
    vừa thừa vừa mở thêm một đường rò dữ liệu không có lý do tồn tại.
    """

    signed_keys = {"valence"}
    allowed = (
        "valence", "arousal", "curiosity", "boredom", "social_energy",
        "attachment", "irritation", "protectiveness", "confidence",
        "fatigue", "tenderness", "delight", "sadness", "anxiety",
        "playfulness", "embarrassment", "cognitive_tension",
        "ontological_dread", "self_coherence", "vulnerability", "restraint",
        "withdrawal", "conflict_tension", "shadow_intensity",
    )
    clean: dict[str, float] = {}
    for key in allowed:
        if key not in affect:
            continue
        try:
            value = float(affect[key])
        except (TypeError, ValueError):
            continue
        if value != value:
            continue
        if key in signed_keys:
            value = max(-1.0, min(1.0, value))
        else:
            value = max(0.0, min(1.0, value))
        clean[key] = round(value, 4)

    return StageProtocolEvent(
        type="affect_state",
        source=str(source or "runtime")[:120],
        data={
            "affect": clean,
            "source": str(source or "runtime")[:120],
            "thought_pressure": max(0.0, min(1.0, float(thought_pressure or 0.0))),
            "confidence": max(0.0, min(1.0, float(confidence or 0.0))),
            "diary_updated": bool(diary_updated),
            "intent_id": str(intent_id or "")[:120],
            "thread_id": str(thread_id or "")[:120],
        },
    )

def stage_state_event(*, event_type: str, reason: str = "", data: dict[str, Any] | None = None) -> StageProtocolEvent:
    return StageProtocolEvent(
        type=event_type,
        reason=reason,
        data=dict(data or {}),
    )



def _continuation_motion(event_type: str, frame: Any) -> str:
    if event_type == "thinking_started":
        delivery = getattr(frame, "delivery", {})
        beat = delivery.get("active_beat") if isinstance(delivery, dict) else {}
        requested = str((beat or {}).get("thinking_motion", "") or "").strip().lower()
        # Body 2D hiện có animation hữu hạn; map intent trung tính sang pose thật.
        return {
            "thinking_small": "read",
            "read": "read",
            "sad": "sad",
            "sit": "sit",
            "stand": "stand",
        }.get(requested, "read")
    if event_type in {"thinking_finished", "continuation_cancelled"}:
        return "stand"
    return ""


def _continuation_body_timeline(event_type: str, event: Any, frame: Any, motion: str) -> dict[str, Any]:
    if event_type not in {"thinking_started", "thinking_finished", "continuation_cancelled"}:
        return {}
    try:
        duration = 0
        if event_type == "thinking_started":
            delivery = getattr(frame, "delivery", {})
            beat = delivery.get("active_beat") if isinstance(delivery, dict) else {}
            duration = max(0, int((beat or {}).get("pause_after_ms", 0) or 0))
    except (TypeError, ValueError):
        duration = 0
    return {
        "turn_id": str(getattr(event, "turn_id", "") or getattr(frame, "turn_id", "") or ""),
        "duration_ms": duration,
        "source": f"continuation:{event_type}",
        "events": [{
            "at_ms": 0,
            "kind": "motion",
            "name": motion or "stand",
            "duration_ms": duration if event_type == "thinking_started" else 220,
            "intent": "pose",
            "facing": -1 if event_type == "thinking_started" else 0,
        }],
    }

def _seq(stage_frame: Any) -> int:
    try:
        return int(getattr(stage_frame, "segment_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _event_seq(event: Any, stage_frame: Any) -> int:
    try:
        return int(getattr(event, "segment_id", getattr(stage_frame, "segment_id", 0)) or 0)
    except (TypeError, ValueError):
        return 0


__all__ = ["affect_state_event", "playback_event", "show_plan_event", "stage_segment_event", "stage_state_event"]

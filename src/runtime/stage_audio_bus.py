# -*- coding: utf-8 -*-
"""stage_audio_bus.py — cầu sự kiện audio giữa TTS runtime, UI và ledger.

TTS không chỉ là "text -> loa".  Runtime cần biết trước khi audio bắt đầu thì
avatar nên đổi emotion/motion nào, đoạn nào đã queue, đoạn nào đang phát, đoạn
nào bị cắt.  Bus này là lớp mỏng gom việc phát event để ``tts_task_manager`` không
phải biết chi tiết coordinator/UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("TTS.Bus")


@dataclass(frozen=True, slots=True)
class StageAudioEvent:
    """Event sân khấu phát ra trước/trong/sau audio.

    Field cố ý giống ``TtsPlaybackEvent`` để coordinator cũ vẫn đọc được bằng
    ``getattr``.  ``emotion``/``motion`` giúp UI đổi trạng thái trước khi tiếng
    nói thật sự phát ra, thay vì avatar đổi mặt trễ sau audio.
    """

    type: str
    turn_id: str = ""
    segment_id: int = 0
    text: str = ""
    reason: str = ""
    error: str = ""
    emotion: str = ""
    motion: str = ""
    prosody_style: str = ""
    turn_segments: tuple[dict[str, object], ...] = ()
    audio_envelope: tuple[float, ...] = ()
    audio_frame_ms: int = 0
    audio_duration_ms: int = 0
    audio_mouth_shape: tuple[float, ...] = ()


class StageAudioBus:
    """Phát lifecycle event ra callback hiện có của coordinator.

    Hiện tại bus chỉ gọi callback vì coordinator đã có `_handle_tts_playback_event`
    để log/đẩy payload UI khi bật feature flag.  Tách thành bus riêng giúp sau này
    nối trực tiếp WebSocket/Godot/Live2D mà không nhét thêm logic vào TTS backend.
    """

    def emit(
        self,
        event: StageAudioEvent,
        *,
        stage_frame: Any = None,
        callback: Callable[[Any, Any], None] | None = None,
    ) -> None:
        if callback is None:
            return
        try:
            callback(event, stage_frame)
        except Exception as exc:
            logger.debug("[stage-audio-bus] callback failed: %s", exc)

    def emit_lifecycle(
        self,
        event_type: str,
        *,
        stage_frame: Any = None,
        text: str = "",
        reason: str = "",
        error: str = "",
        turn_segments: tuple[dict[str, object], ...] = (),
        audio_envelope: tuple[float, ...] = (),
        audio_frame_ms: int = 0,
        audio_duration_ms: int = 0,
        audio_mouth_shape: tuple[float, ...] = (),
        callback: Callable[[Any, Any], None] | None = None,
    ) -> StageAudioEvent:
        event = StageAudioEvent(
            type=str(event_type or ""),
            turn_id=str(getattr(stage_frame, "turn_id", "") or ""),
            segment_id=_segment_id(stage_frame),
            text=str(text or getattr(stage_frame, "tts_text", "") or ""),
            reason=str(reason or ""),
            error=str(error or ""),
            emotion=str(getattr(stage_frame, "emotion", "") or ""),
            motion=str(getattr(stage_frame, "motion", "") or ""),
            prosody_style=str(getattr(stage_frame, "prosody_style", "") or ""),
            turn_segments=tuple(turn_segments or ()),
            audio_envelope=tuple(float(value) for value in (audio_envelope or ())),
            audio_frame_ms=max(0, int(audio_frame_ms or 0)),
            audio_duration_ms=max(0, int(audio_duration_ms or 0)),
            audio_mouth_shape=audio_mouth_shape,
        )
        self.emit(event, stage_frame=stage_frame, callback=callback)
        return event

    def emit_emotion_if_changed(
        self,
        *,
        stage_frame: Any,
        previous_emotion: str,
        callback: Callable[[Any, Any], None] | None = None,
    ) -> str:
        """Gửi emotion/motion trước audio nếu frame đổi trạng thái biểu diễn."""

        emotion = str(getattr(stage_frame, "emotion", "") or "").strip()
        motion = str(getattr(stage_frame, "motion", "") or "").strip()
        signature = f"{emotion}|{motion}"
        if not signature.strip("|") or signature == previous_emotion:
            return previous_emotion
        self.emit_lifecycle(
            "emotion_change",
            stage_frame=stage_frame,
            text="",
            callback=callback,
        )
        return signature


def _segment_id(stage_frame: Any) -> int:
    try:
        return int(getattr(stage_frame, "segment_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


__all__ = ["StageAudioBus", "StageAudioEvent"]

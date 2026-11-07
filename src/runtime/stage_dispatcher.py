# -*- coding: utf-8 -*-
"""stage_dispatcher.py — phát StageFrame ra TTS theo đúng nhịp segment.

Coordinator không nên tự biết quá nhiều về queue TTS. Dispatcher nhận list frame,
và chọn cách phát phù hợp với backend.

Với Gemini/remote TTS, gọi API riêng cho từng segment tạo khoảng chờ rất dài
giữa các câu. Vì vậy mặc định dispatcher gộp các segment thành MỘT transcript có
pause tự nhiên, rồi gọi TTS một lần. Nếu muốn test queue từng segment, bật:

    ERIS_TTS_SEGMENT_DISPATCH=queued
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from typing import Any, Callable, Iterable

from runtime.stage_contracts import StageFrame


def _dispatch_mode() -> str:
    # Env mức thấp vẫn thắng để test một nhánh riêng. Nếu không có, profile có
    # tên quyết định cả cụm thay vì để bốn toggle tự mâu thuẫn với nhau.
    env_value = os.environ.get("ERIS_TTS_SEGMENT_DISPATCH", "").strip().lower()
    if env_value in {"inline", "queued"}:
        return env_value
    from runtime.stage_profiles import active_stage_profile
    return active_stage_profile().dispatch_mode


def _max_transcript_chars() -> int:
    from runtime.tts_budget import GLOBAL_TTS_REQUEST_BUDGET
    try:
        from runtime.tts_engine import _tts_provider
        provider = _tts_provider()
    except Exception:
        provider = ""
    # Dispatcher phải dùng đúng limit của provider hiện tại. Nếu dùng limit
    # Gemini 260 cho ElevenLabs, câu bình thường bị xé thành 2–3 clip vô ích.
    return GLOBAL_TTS_REQUEST_BUDGET.transcript_limit(provider)


def _task_manager_enabled() -> bool:
    env_value = os.environ.get("ERIS_TTS_TASK_MANAGER", "").strip().lower()
    if env_value:
        return env_value not in {"0", "false", "off", "no", "none"}
    from runtime.stage_profiles import active_stage_profile
    return active_stage_profile().task_manager_enabled


def _join_tts_segments(frames: list[StageFrame]) -> str:
    """Ghép segment thành một transcript liên tục để tránh nhiều call TTS.

    Không chèn tag vào đây; prosody/markup vẫn thuộc ``tts_engine``. Dấu câu sẵn
    có trong từng segment đủ cho Gemini tạo ngắt nhịp tự nhiên trong cùng audio.
    Dùng line break thay vì dấu cách để mỗi segment là một breath group ngắn.
    """
    # Dùng khoảng trắng; line break ở vài backend remote bị diễn giải thành
    # pause dài bất thường. Điểm rơi đã có trong dấu câu + prosody_notes.
    return " ".join(str(frame.tts_text or "").strip() for frame in frames if str(frame.tts_text or "").strip()).strip()


def _speech_kwargs(frame) -> dict[str, object]:
    """Truyền contract đã pin theo turn xuống backend TTS."""
    contract = getattr(frame, "speech_contract", None)
    if not isinstance(contract, dict):
        return {}
    return {
        "language_hint": str(contract.get("language", "") or ""),
        "voice_family": str(contract.get("voice_family", "") or ""),
        "locale": str(contract.get("locale", "") or ""),
        "prelocalized": bool(contract.get("prelocalized", False)),
        "verified_transcript_hash": str(contract.get("verified_transcript_hash", "") or ""),
    }


def _performance_kwargs(frame: StageFrame) -> dict[str, object]:
    """Rút metadata biểu diễn mà không để dispatcher biết chi tiết provider."""
    delivery = frame.delivery if isinstance(frame.delivery, dict) else {}
    result: dict[str, object] = {}
    act = delivery.get("performance_act") if isinstance(delivery, dict) else None
    if isinstance(act, dict):
        result.update({
            "vocal_cache_kind": str(act.get("kind", "") or ""),
            "vocal_prefix": str(act.get("audible_prefix", "") or ""),
        })
    voice_segment = delivery.get("voice_director_segment") if isinstance(delivery, dict) else None
    if isinstance(voice_segment, dict):
        result["voice_director_segment"] = dict(voice_segment)
    return result


def _opening_hold_ms(frame: StageFrame) -> int:
    act = frame.stage_act if isinstance(getattr(frame, "stage_act", None), dict) else {}
    try:
        return max(0, min(250, int(act.get("opening_hold_ms", 0) or 0)))
    except (TypeError, ValueError):
        return 0


def _inline_frame(frame_list: list[StageFrame]) -> StageFrame | None:
    """Gộp logical segments thành một performance/audio liền mạch."""

    if not frame_list:
        return None
    base = frame_list[0]
    joined = _join_tts_segments(frame_list)
    if not joined:
        return None
    notes = base.prosody_notes
    if len(frame_list) > 1:
        notes = (
            f"{notes} Perform the full transcript as one continuous human utterance across "
            f"all {len(frame_list)} logical segments. Keep the same timbre, breath, volume, "
            "and emotional trajectory across boundaries; never restart the voice as if a new "
            "clip began. Let commas and em dashes become soft thought-pauses, and sentence "
            "boundaries become brief natural breaths with a little vocal carry into the next "
            "phrase. Pauses may be audible, but never use dead air, abrupt cuts, or metronomic timing."
        ).strip()
    inline_delivery = dict(base.delivery) if isinstance(base.delivery, dict) else {}
    inline_delivery.update({
        "_inline_joined": True,
        "_inline_joined_segment_ids": [int(frame.segment_id) for frame in frame_list],
        "_inline_joined_count": len(frame_list),
    })
    return replace(
        base,
        segment_id=base.segment_id,
        display_text=joined,
        tts_text=joined,
        memory_text=joined,
        prosody_notes=notes,
        delivery=inline_delivery,
    )


class StageDispatcher:
    def __init__(self, *, profile: object | None = None) -> None:
        if isinstance(profile, str):
            from runtime.stage_profiles import stage_profile
            profile = stage_profile(profile)
        self.profile = profile

    def _profile(self):
        if self.profile is not None:
            return self.profile
        from runtime.stage_profiles import active_stage_profile
        return active_stage_profile()

    def dispatch_tts(
        self,
        frames: Iterable[StageFrame],
        *,
        tts_engine: Any,
        valence: float,
        arousal: float,
        stance: str,
        on_first_start: Callable[[], None] | None = None,
        on_event: Callable[[Any, StageFrame], None] | None = None,
    ) -> bool:
        # Setting có thể đổi trong panel sau khi Python đã khởi động.
        refresh = getattr(tts_engine, "refresh_configuration", None) if tts_engine is not None else None
        if callable(refresh):
            refresh()
        frame_list = [frame for frame in frames if str(frame.tts_text or "").strip()]
        if not frame_list or tts_engine is None or not getattr(tts_engine, "_ok", False):
            return False
        turn_id = str(getattr(frame_list[0], "turn_id", "") or "")
        begin_turn = getattr(tts_engine, "begin_speech_turn", None)
        speech_epoch = int(begin_turn(turn_id)) if callable(begin_turn) else 0

        # Opening hold là nhịp thật, nhưng giới hạn 250ms để không biến stage thành
        # lag. Với sync mặc định, bubble sẽ được nhả đúng lúc playback bắt đầu.
        opening_hold = _opening_hold_ms(frame_list[0])
        if opening_hold > 0:
            time.sleep(opening_hold / 1000.0)

        dispatch_mode = (
            _dispatch_mode()
            if os.environ.get("ERIS_TTS_SEGMENT_DISPATCH", "").strip()
            else str(getattr(self._profile(), "dispatch_mode", "inline") or "inline")
        )
        # Verified segments không được dán lại thành transcript khác sau gate.
        if len(frame_list) > 1 and any(
            bool(frame.speech_contract.get('verified_transcript_hash')) for frame in frame_list
        ):
            dispatch_mode = 'queued'
        # Hội thoại mặc định ưu tiên một clip liên tục, NHƯNG hard provider
        # transcript budget có quyền cao hơn continuity. Trước đây pipeline đã
        # chia câu an toàn rồi dispatcher dán tất cả lại thành >260 chars, tự
        # tạo lỗi transcript_too_long và kéo fallback TTS hàng chục giây.
        dispatch_frames = frame_list
        if dispatch_mode == "inline":
            joined_text = _join_tts_segments(frame_list)
            if len(joined_text) <= _max_transcript_chars():
                joined_frame = _inline_frame(frame_list)
                if joined_frame is None:
                    return False
                dispatch_frames = [joined_frame]
            else:
                # Giữ logical segments đã được StageOutputPipeline chia. Manager
                # sẽ prefetch/queue chúng; tuyệt đối không join lại vượt budget.
                dispatch_mode = "queued"
                dispatch_frames = frame_list

        manager_enabled = (
            bool(os.environ.get("ERIS_TTS_TASK_MANAGER", "").strip().lower() not in {"0", "false", "off", "no", "none"})
            if os.environ.get("ERIS_TTS_TASK_MANAGER", "").strip()
            else bool(getattr(self._profile(), "task_manager_enabled", False))
        )
        if manager_enabled:
            try:
                manager = getattr(tts_engine, "task_manager", None)
                if manager is None:
                    from runtime.stage_audio_bus import StageAudioBus
                    from runtime.tts_task_manager import TtsTaskManager

                    # Manager sống cùng engine để cancel_current/shutdown có thể
                    # chạm đúng queue đang phát khi user chen vào.
                    manager = TtsTaskManager(
                        audio_bus=StageAudioBus(),
                        prefetch_workers=int(getattr(self._profile(), "prefetch_workers", 2) or 2),
                        max_prefetch_segments=int(getattr(self._profile(), "max_prefetch_segments", 4) or 4),
                    )
                    setattr(tts_engine, "task_manager", manager)
                else:
                    # Manager có thể đã được tạo ở conversation profile. Cập nhật
                    # policy live khi đổi cấu hình queue, không bắt restart app.
                    if hasattr(manager, "prefetch_workers"):
                        manager.prefetch_workers = int(getattr(self._profile(), "prefetch_workers", 2) or 2)
                    if hasattr(manager, "max_prefetch_segments"):
                        manager.max_prefetch_segments = int(getattr(self._profile(), "max_prefetch_segments", 4) or 4)
                scheduled = manager.dispatch_frames(
                    dispatch_frames,
                    backend=tts_engine,
                    valence=valence,
                    arousal=arousal,
                    stance=stance,
                    on_first_start=on_first_start,
                    on_event=on_event,
                    speech_epoch=speech_epoch,
                )
                if scheduled:
                    return True
            except Exception:
                # Fallback giữ Eris có tiếng nói kể cả khi manager mới lỗi.
                pass

        if dispatch_mode == "inline" or len(frame_list) == 1:
            return self._dispatch_inline(
                frame_list,
                tts_engine=tts_engine,
                valence=valence,
                arousal=arousal,
                stance=stance,
                on_first_start=on_first_start,
                on_event=on_event,
                speech_epoch=speech_epoch,
            )
        return self._dispatch_queued(
            frame_list,
            tts_engine=tts_engine,
            valence=valence,
            arousal=arousal,
            stance=stance,
            on_first_start=on_first_start,
            on_event=on_event,
            speech_epoch=speech_epoch,
        )

    def _dispatch_inline(
        self,
        frame_list: list[StageFrame],
        *,
        tts_engine: Any,
        valence: float,
        arousal: float,
        stance: str,
        on_first_start: Callable[[], None] | None,
        on_event: Callable[[Any, StageFrame], None] | None,
        speech_epoch: int,
    ) -> bool:
        if len(frame_list) > 1 and any(
            bool(frame.speech_contract.get('verified_transcript_hash')) for frame in frame_list
        ):
            # Backend queue không có thì fail audio, không bỏ verify để cứu tiếng.
            return False
        inline_frame = _inline_frame(frame_list)
        if inline_frame is None:
            return False
        # Inline là một audio duy nhất cho nhiều logical segment. Contract cũ
        # từng giữ display_text của frame đầu (thường chỉ là "Khoan.") và còn
        # ép segment_id về 0. Kết quả: bubble đứng ở câu đầu; nhóm inline sau
        # Tutor trùng ID nên bị dedupe, TTS vẫn đọc còn UI hoàn toàn im.
        #
        # Giữ ID thật của frame đầu trong group và công bố đúng transcript mà
        # audio đang đọc. Không thể đổi bubble giữa một file audio nếu backend
        # không có timestamp, nhưng ít nhất text và âm thanh phải cùng một sự thật.
        def event_callback(event, *, _frame=inline_frame):
            if on_event is not None:
                on_event(event, _frame)

        tts_engine.speak(
            inline_frame.tts_text,
            valence=valence,
            arousal=arousal,
            stance=stance,
            on_start_play=on_first_start,
            prosody_style=inline_frame.prosody_style,
            prosody_notes=inline_frame.prosody_notes,
            turn_id=inline_frame.turn_id,
            segment_id=inline_frame.segment_id,
            interruptible=inline_frame.interruptible,
            replace=True,
            on_event=event_callback,
            speech_epoch=speech_epoch,
            **_performance_kwargs(inline_frame),
            **_speech_kwargs(inline_frame),
        )
        return True

    def _dispatch_queued(
        self,
        frame_list: list[StageFrame],
        *,
        tts_engine: Any,
        valence: float,
        arousal: float,
        stance: str,
        on_first_start: Callable[[], None] | None,
        on_event: Callable[[Any, StageFrame], None] | None,
        speech_epoch: int,
    ) -> bool:
        scheduled = False
        for index, frame in enumerate(frame_list):
            start_callback = on_first_start if index == 0 else None

            def event_callback(event, *, _frame=frame):
                if on_event is not None:
                    on_event(event, _frame)

            tts_engine.speak(
                frame.tts_text,
                valence=valence,
                arousal=arousal,
                stance=stance,
                on_start_play=start_callback,
                prosody_style=frame.prosody_style,
                prosody_notes=frame.prosody_notes,
                turn_id=frame.turn_id,
                segment_id=frame.segment_id,
                interruptible=frame.interruptible,
                replace=(index == 0),
                on_event=event_callback,
                speech_epoch=speech_epoch,
                **_performance_kwargs(frame),
                **_speech_kwargs(frame),
            )
            scheduled = True
        return scheduled


__all__ = ["StageDispatcher"]

# -*- coding: utf-8 -*-
"""Profile nhịp phát TTS cho hội thoại thường.

Repo không còn profile quay take/scripted performance. ``conversation`` là
đường mặc định; ``custom`` chỉ giữ các nút kỹ thuật thấp tầng để debug queue.
Giá trị ``recording`` cũ trong settings được di trú im lặng về conversation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StageRuntimeProfile:
    name: str
    logical_segment_tts: bool
    dispatch_mode: str
    task_manager_enabled: bool
    prefetch_workers: int
    max_tts_segments: int
    min_split_chars: int
    max_prefetch_segments: int


_CONVERSATION = StageRuntimeProfile(
    name="conversation",
    logical_segment_tts=True,
    dispatch_mode="inline",
    # Mỗi turn synth trong executor riêng. Khi user chen vào, generation mới có
    # thể bắt đầu ngay dù HTTP request của turn cũ chưa chịu trả về. Worker legacy
    # tuần tự từng khiến một request đã bị hủy vẫn chặn giọng của câu kế tiếp.
    task_manager_enabled=True,
    # Giữ profile value cũ để tương thích cấu hình. TaskManager sẽ tự nâng
    # multi-segment turn lên tối đa ba worker; lượt một clip không đổi hành vi.
    prefetch_workers=1,
    max_tts_segments=8,
    min_split_chars=58,
    # Never configure the manager below the number of segments the output
    # pipeline itself is allowed to create. Otherwise a perfectly valid 5-piece
    # turn falls back to the legacy queue and can lose a sentence.
    max_prefetch_segments=8,
)
_PRESETS = {"conversation": _CONVERSATION}


def _custom_profile() -> StageRuntimeProfile:
    from runtime.runtime_settings_store import runtime_bool, runtime_int, runtime_text

    dispatch = runtime_text("stage", "segment_dispatch", default="inline").strip().lower()
    if dispatch not in {"inline", "queued"}:
        dispatch = "inline"
    max_segments = max(1, min(12, runtime_int("stage", "max_tts_segments", default=8)))
    return StageRuntimeProfile(
        name="custom",
        logical_segment_tts=runtime_bool("stage", "segment_tts", default=True),
        dispatch_mode=dispatch,
        task_manager_enabled=runtime_bool("stage", "tts_task_manager", default=False),
        prefetch_workers=max(1, min(6, runtime_int("stage", "prefetch_workers", default=2))),
        max_tts_segments=max_segments,
        min_split_chars=max(24, min(160, runtime_int("stage", "min_split_chars", default=58))),
        max_prefetch_segments=max_segments,
    )


def _normalize_name(value: str) -> str:
    name = str(value or "").strip().lower()
    # Migration duy nhất cho settings cũ. Không tái kích hoạt profile quay take.
    if name == "recording":
        return "conversation"
    return name


def active_stage_profile() -> StageRuntimeProfile:
    env_name = _normalize_name(os.environ.get("ERIS_STAGE_PROFILE", ""))
    if env_name in _PRESETS:
        return _PRESETS[env_name]
    if env_name == "custom":
        return _custom_profile()

    from runtime.runtime_settings_store import runtime_text
    name = _normalize_name(runtime_text("stage", "performance_profile", default="conversation"))
    if name in _PRESETS:
        return _PRESETS[name]
    if name == "custom":
        return _custom_profile()
    return _CONVERSATION


def stage_profile(name: str) -> StageRuntimeProfile:
    key = _normalize_name(name)
    if key in _PRESETS:
        return _PRESETS[key]
    if key == "custom":
        return _custom_profile()
    return active_stage_profile()


def stage_profile_payload() -> dict[str, object]:
    profile = active_stage_profile()
    return {
        "name": profile.name,
        "logical_segment_tts": profile.logical_segment_tts,
        "segment_dispatch": profile.dispatch_mode,
        "tts_task_manager": profile.task_manager_enabled,
        "prefetch_workers": profile.prefetch_workers,
        "max_tts_segments": profile.max_tts_segments,
        "min_split_chars": profile.min_split_chars,
        "max_prefetch_segments": profile.max_prefetch_segments,
    }


__all__ = ["StageRuntimeProfile", "active_stage_profile", "stage_profile", "stage_profile_payload"]

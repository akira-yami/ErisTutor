# -*- coding: utf-8 -*-
"""Regression tests cho nhịp TTS liền mạch trong hội thoại."""

from types import SimpleNamespace

from runtime.prosody_director import ProsodyPlan
from runtime.stage_contracts import StageFrame
from runtime.stage_dispatcher import StageDispatcher
from runtime.stage_profiles import StageRuntimeProfile
from runtime.tts_markup import render_tts_markup


def test_light_pause_does_not_inject_hard_ellipsis_after_opening_word() -> None:
    text = "Ừ, nghe như một kiểu bạn có hơi khó chiều."
    plan = ProsodyPlan(style="dry_tease", pause_level="light")

    assert render_tts_markup(text, plan) == text


def test_medium_pause_preserves_original_punctuation() -> None:
    text = "Khoan một chút, mình đang nối lại ý này cho gọn."
    plan = ProsodyPlan(style="low_quiet", pause_level="medium")

    assert render_tts_markup(text, plan) == text


def test_inline_task_manager_receives_one_continuous_audio_frame() -> None:
    class RecordingManager:
        def __init__(self) -> None:
            self.frames = []

        def dispatch_frames(self, frames, **_kwargs) -> bool:
            self.frames = list(frames)
            return True

    class FakeTts:
        _ok = True

        def __init__(self) -> None:
            self.task_manager = RecordingManager()

        def refresh_configuration(self) -> None:
            return None

        def begin_speech_turn(self, _turn_id: str) -> int:
            return 1

        def speak(self, text: str, **kwargs) -> None:
            self.task_manager.frames = [
                SimpleNamespace(
                    tts_text=text,
                    prosody_notes=str(kwargs.get("prosody_notes", "")),
                )
            ]

    profile = StageRuntimeProfile(
        name="test",
        logical_segment_tts=True,
        dispatch_mode="inline",
        task_manager_enabled=True,
        prefetch_workers=1,
        max_tts_segments=8,
        min_split_chars=58,
        max_prefetch_segments=4,
    )
    tts = FakeTts()
    frames = (
        StageFrame(turn_id="turn-1", segment_id=0, tts_text="Ừ, mình hiểu ý rồi."),
        StageFrame(turn_id="turn-1", segment_id=1, tts_text="Nhưng để mình nói cho liền hơi."),
    )

    assert StageDispatcher(profile=profile).dispatch_tts(
        frames,
        tts_engine=tts,
        valence=0.2,
        arousal=0.3,
        stance="warm",
    )
    assert len(tts.task_manager.frames) == 1
    assert tts.task_manager.frames[0].tts_text == (
        "Ừ, mình hiểu ý rồi. Nhưng để mình nói cho liền hơi."
    )
    assert "same timbre" in tts.task_manager.frames[0].prosody_notes

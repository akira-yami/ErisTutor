from __future__ import annotations

from types import SimpleNamespace

from cortex.mental_material import derive_causal_motives
from runtime.ai_orchestrated.random_chat_seed import RandomChatSeed
from runtime.interaction_clock import InteractionClock
from runtime.mental_material_runtime import MentalMaterialRuntime
from runtime.stage_contracts import StageFrame
from runtime.stage_dispatcher import StageDispatcher
from runtime.stage_output import StageOutputPipeline
from runtime.stage_profiles import StageRuntimeProfile
from runtime.tts_budget import GLOBAL_TTS_REQUEST_BUDGET


def _profile(*, dispatch_mode: str = "inline") -> StageRuntimeProfile:
    return StageRuntimeProfile(
        name="test",
        logical_segment_tts=True,
        dispatch_mode=dispatch_mode,
        task_manager_enabled=True,
        prefetch_workers=1,
        max_tts_segments=8,
        min_split_chars=58,
        max_prefetch_segments=4,
    )


def _motive(**overrides):
    payload = {
        "cause_id": "user_event:turn-7",
        "cause_kind": "user_event",
        "motive_kind": "engage_user_event",
        "semantic_focus": "phân tích lại câu vừa rồi",
        "why_now": "new_or_recent_user_event:user_event:turn-7",
        "strength": 0.86,
        "source_refs": ["turn-7"],
        "allowed_material_kinds": ["reaction", "opinion", "question"],
        "generation_budget": 2,
        "relationship_modulation": 0.2,
        "legacy_motive_kind": "want_to_explore",
    }
    payload.update(overrides)
    return payload


def _material_payload(**overrides):
    payload = {
        "decision": "seed",
        "seed_id": "post-reply-material",
        "material_kind": "reaction",
        "premise": "Bây giờ nghĩ lại thì trọng tâm của câu hỏi nằm ở chính phát ngôn trước đó.",
        "topic_tags": ["retrospective", "reply"],
        "epistemic_mode": "opinion",
        "open_loop": False,
        "expected_update": [],
        "uncertainty": 0.12,
        "confidence": 0.90,
        "ttl_seconds": 420,
    }
    payload.update(overrides)
    return payload


def test_audio_pending_owns_floor_before_playback_and_stale_done_cannot_release_new_turn() -> None:
    clock = InteractionClock()
    clock.mark_assistant_text(100.0)
    clock.mark_audio_pending(101.0, turn_id="chat:A")

    assert clock.busy is True
    assert clock.speech_floor_busy is True
    assert clock.stale(20.0, now=150.0) is False

    clock.mark_audio_started(102.0, turn_id="chat:A")
    clock.mark_audio_pending(103.0, turn_id="chat:B")
    clock.mark_audio_finished(104.0, turn_id="chat:A")

    assert clock.speech_floor_busy is True
    assert clock.audio_floor_turn_id == "chat:B"

    clock.mark_audio_finished(105.0, turn_id="chat:B")
    assert clock.speech_floor_busy is False
    assert clock.audio_floor_turn_id == ""


def test_answered_user_event_stays_private_and_cannot_project_second_reply() -> None:
    host = SimpleNamespace(logger=None)
    runtime = MentalMaterialRuntime(host)
    seed = RandomChatSeed.from_material_mapping(
        _material_payload(), causal_motive=_motive(), requested_mode="eris_led"
    )
    runtime.ingest_seed(seed, now=1000.0)

    _, before_expression = runtime.project(now=1000.0)
    assert before_expression

    consumed = runtime.mark_user_event_answered(
        "phân tích lại câu vừa rồi", turn_id="turn-7", now=1001.0
    )
    assert "user_event:turn-7" in consumed

    concern, expression = runtime.project(now=1001.0)
    assert concern == ()
    assert expression == ()
    assert runtime.wake_pressure(now=1001.0) == 0.0
    # Material vẫn còn để cognition/audit thấy, chỉ mất quyền public.
    assert runtime.snapshot(now=1001.0)


def test_inline_dispatch_never_rejoins_logical_segments_past_provider_budget() -> None:
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

    # Repo mặc định dùng ElevenLabs; regression phải kiểm đúng hard-limit của
    # provider hiện tại thay vì hard-code limit Gemini cũ 260 ký tự.
    limit = GLOBAL_TTS_REQUEST_BUDGET.transcript_limit("elevenlabs")
    first = "a" * min(460, limit - 10)
    second = "b" * min(460, limit - 10)
    assert len(first) + 1 + len(second) > limit

    tts = FakeTts()
    frames = (
        StageFrame(turn_id="turn-long", segment_id=0, tts_text=first),
        StageFrame(turn_id="turn-long", segment_id=1, tts_text=second),
    )
    assert StageDispatcher(profile=_profile()).dispatch_tts(
        frames, tts_engine=tts, valence=0.0, arousal=0.2, stance="neutral"
    )
    assert [frame.tts_text for frame in tts.task_manager.frames] == [first, second]
    assert all(len(frame.tts_text) <= limit for frame in tts.task_manager.frames)


def test_stage_output_prefers_extra_segments_over_provider_limit_violation() -> None:
    limit = GLOBAL_TTS_REQUEST_BUDGET.transcript_limit("elevenlabs")
    frame = StageFrame(
        turn_id="very-long",
        segment_id=0,
        tts_text=("một đoạn khá dài không có dấu kết câu " * 120).strip(),
    )
    segments = StageOutputPipeline(profile=_profile()).frames_for_tts(frame)
    assert segments
    assert all(len(item.tts_text) <= limit for item in segments)


def test_user_event_causal_identity_is_still_stable_for_consumption_contract() -> None:
    motives = derive_causal_motives({
        "recent_user_events": [{"role": "user", "text": "phân tích lại câu vừa rồi", "event_ref": "turn-7"}],
        "relationship": {},
        "eris_self_memory": {},
        "user_memory": "",
    })
    assert motives[0].cause_id == "user_event:turn-7"

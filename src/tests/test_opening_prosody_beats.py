# -*- coding: utf-8 -*-
from runtime.ai_orchestrated.performance import AutonomousPerformancePlanner
from runtime.prosody_director import ProsodyDirector


def test_performance_uses_explicit_opening_beats_and_bilingual_display() -> None:
    planner = AutonomousPerformancePlanner()
    act = planner.plan(
        "Anh về rồi à. Facebook có gì đáng chú ý không?",
        autonomous_plan={
            "plan_id": "p",
            "speak": True,
            "semantics": {"turn_kind": "startup", "speech_act": "typed_opening_scene"},
            "activity": {"phase": "startup", "activity_kind": "session_ritual"},
        },
        opening_plan={"prosody_plan": {"opening_hold_ms": 120, "closing_settle_ms": 360}},
        performance_context={
            "opening_beats": [
                {
                    "beat_id": "b1",
                    "display_text": "Anh về rồi à.",
                    "speech_text": "おかえり。",
                    "delivery": "warm_restrained",
                    "pause_after_ms": 280,
                    "motion": "lean_in_small",
                    "gaze": "user_attention",
                },
                {
                    "beat_id": "b2",
                    "display_text": "Facebook có gì đáng chú ý không?",
                    "speech_text": "Facebookで何か気になる？",
                    "delivery": "curious_soft",
                    "pause_after_ms": 0,
                },
            ]
        },
    )
    assert act is not None
    assert act.cadence == "opening_directed"
    assert act.opening_hold_ms == 120
    assert act.closing_hold_ms == 360
    speech = act.speech_beats
    assert [beat.text for beat in speech] == ["おかえり。", "Facebookで何か気になる？"]
    assert [beat.display_text for beat in speech] == ["Anh về rồi à.", "Facebook có gì đáng chú ý không?"]
    assert speech[0].pause_after_ms == 280


def test_prosody_director_maps_typed_beat_without_stage_tags_in_text() -> None:
    plan = ProsodyDirector().plan_beat(
        "おかえり。",
        {"delivery": "deadpan_soft", "pause_after_ms": 420, "emphasis": ["hoàn toàn"]},
    )
    assert plan.style == "deadpan"
    assert "420ms" in plan.director_notes
    assert "hoàn toàn" in plan.director_notes


def test_stage_output_keeps_vietnamese_bubble_when_tts_is_japanese(monkeypatch) -> None:
    from runtime.stage_contracts import StageFrame
    from runtime.stage_output import StageOutputPipeline
    import runtime.tts_engine as tts_engine

    monkeypatch.setattr(tts_engine, "_tts_provider", lambda: "elevenlabs")
    planner = AutonomousPerformancePlanner()
    act = planner.plan(
        "Anh về rồi à. Facebook có gì đáng chú ý không?",
        autonomous_plan={
            "plan_id": "p-stage",
            "speak": True,
            "semantics": {"turn_kind": "startup", "speech_act": "typed_opening_scene"},
            "activity": {"phase": "startup", "activity_kind": "session_ritual"},
        },
        opening_plan={"prosody_plan": {"opening_hold_ms": 120, "closing_settle_ms": 360}},
        performance_context={
            "opening_beats": [
                {"beat_id": "b1", "display_text": "Anh về rồi à.", "speech_text": "おかえり。"},
                {
                    "beat_id": "b2",
                    "display_text": "Facebook có gì đáng chú ý không?",
                    "speech_text": "Facebookで何か気になる？",
                },
            ]
        },
    )
    assert act is not None
    frame = StageFrame(
        turn_id="t",
        display_text="Anh về rồi à. Facebook có gì đáng chú ý không?",
        tts_text="おかえり。 Facebookで何か気になる？",
        memory_text="Anh về rồi à. Facebook có gì đáng chú ý không?",
        stage_act=act.to_payload(),
    )
    frames = StageOutputPipeline().frames_for_tts(frame)
    assert [item.display_text for item in frames] == ["Anh về rồi à.", "Facebook có gì đáng chú ý không?"]
    assert [item.tts_text for item in frames] == ["おかえり。", "Facebookで何か気になる？"]
    assert [item.memory_text for item in frames] == ["Anh về rồi à.", "Facebook có gì đáng chú ý không?"]


def test_stage_output_generic_split_keeps_display_and_tts_surfaces_separate(monkeypatch) -> None:
    from runtime.stage_contracts import StageFrame
    from runtime.stage_output import StageOutputPipeline
    import runtime.tts_engine as tts_engine

    # Force the generic logical-segmentation path (not the ElevenLabs fast path).
    monkeypatch.setattr(tts_engine, "_tts_provider", lambda: "edge")
    frame = StageFrame(
        turn_id="t-generic-bilingual",
        display_text="Đây là câu tiếng Việt đầu tiên đủ dài. Đây là câu tiếng Việt thứ hai đủ dài.",
        tts_text="これは最初の長い日本語の文章です。 これは二番目の長い日本語の文章です。",
        memory_text="Đây là câu tiếng Việt đầu tiên đủ dài. Đây là câu tiếng Việt thứ hai đủ dài.",
    )

    from types import SimpleNamespace

    profile = SimpleNamespace(logical_segment_tts=True, min_split_chars=24, max_tts_segments=8)
    frames = StageOutputPipeline(profile=profile).frames_for_tts(frame)

    assert [item.tts_text for item in frames] == [
        "これは最初の長い日本語の文章です。",
        "これは二番目の長い日本語の文章です。",
    ]
    assert [item.display_text for item in frames] == [
        "Đây là câu tiếng Việt đầu tiên đủ dài.",
        "Đây là câu tiếng Việt thứ hai đủ dài.",
    ]
    assert [item.memory_text for item in frames] == [
        "Đây là câu tiếng Việt đầu tiên đủ dài.",
        "Đây là câu tiếng Việt thứ hai đủ dài.",
    ]

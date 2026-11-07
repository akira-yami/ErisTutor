from __future__ import annotations

import pytest

from runtime.engagements import (
    ContinuityPolicy,
    EngagementProfile,
    EngagementProfileRegistry,
    EngagementRuntime,
    FocusMode,
    InitiativeMode,
    InteractionContract,
)
from runtime.engagements.detector import EngagementDetector


def music_profile() -> EngagementProfile:
    return EngagementProfile(
        profile_id="music_practice",
        adapter_id="generic",
        kind="music_practice",
        phrases=("luyện đàn", "luyen dan", "music practice"),
        contract=InteractionContract(
            role="music_practice_companion",
            initiative=InitiativeMode.GUIDED,
            feedback_style="precise_and_encouraging",
            allowed_actions=("track_exercise", "comment_on_progress", "suggest_break"),
            forbidden_actions=("invent_audio_evidence",),
        ),
        continuity=ContinuityPolicy(60, 150, 8, 10, 7200),
        focus=FocusMode.FOREGROUND,
        priority=74,
    )


def test_custom_profile_extends_detector_without_core_branch() -> None:
    registry = EngagementProfileRegistry.defaults()
    registry.register(music_profile())
    detector = EngagementDetector(registry)

    proposal = detector.propose("Eris, hãy luyện đàn với tôi suốt buổi")

    assert proposal is not None
    assert proposal.kind == "music_practice"
    assert proposal.adapter_id == "generic"
    assert proposal.contract.role == "music_practice_companion"


def test_runtime_can_register_profile_and_keep_it_in_planner_context() -> None:
    runtime = EngagementRuntime(clock=lambda: 100.0)
    runtime.register_profile(music_profile())

    engagement = runtime.observe_user_message("hãy luyện đàn với tôi suốt buổi", now=100.0)
    context = runtime.planner_context(now=160.0)

    assert engagement is not None
    assert engagement.kind == "music_practice"
    assert context["foreground"]["role"] == "music_practice_companion"
    assert context["foreground"]["age_seconds"] == 60.0


def test_longest_profile_phrase_wins_over_generic_overlap() -> None:
    registry = EngagementProfileRegistry([music_profile()])
    registry.register(
        EngagementProfile(
            profile_id="generic_music",
            adapter_id="generic",
            kind="generic_music",
            phrases=("music",),
            contract=InteractionContract(),
            continuity=ContinuityPolicy(),
            priority=99,
        )
    )
    detector = EngagementDetector(registry)

    proposal = detector.propose("help me with music practice throughout this session")

    assert proposal is not None
    assert proposal.kind == "music_practice"


def test_duplicate_profile_requires_explicit_replace() -> None:
    registry = EngagementProfileRegistry([music_profile()])
    with pytest.raises(ValueError):
        registry.register(music_profile())
    registry.register(music_profile(), replace=True)
    assert registry.ids() == ("music_practice",)

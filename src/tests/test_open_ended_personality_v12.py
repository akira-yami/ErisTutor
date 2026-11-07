from __future__ import annotations

import math

from runtime.ai_orchestrated.contracts import CognitiveObservation
from runtime.ai_orchestrated.personality import (
    ConsequenceAssessment,
    NarrativeState,
    PersonalityProfile,
    RelationshipPersonalityProfile,
    SelfInterpretation,
    TraitSignal,
)
from runtime.ai_orchestrated.personality_consolidator import consolidate_personality
from runtime.ai_orchestrated.identity import IdentityProfile
from runtime.ai_orchestrated.semantic_contracts import DiscourseFrame
from runtime.ai_orchestrated.state_reducer import StateReducer


def _observation(
    *,
    turn_seq: int,
    session_id: str,
    signal: TraitSignal,
    interpretation: SelfInterpretation | None = None,
    consequence: ConsequenceAssessment | None = None,
    relationship_id: str = "primary_user",
    open_loop: str = "",
    discourse_status: str = "neutral",
) -> CognitiveObservation:
    return CognitiveObservation(
        turn_id=f"turn-{turn_seq}",
        turn_seq=turn_seq,
        session_id=session_id,
        relationship_id=relationship_id,
        event_kind="chat",
        confidence=0.92,
        ambiguity=0.08,
        self_interpretation=interpretation or SelfInterpretation(),
        consequence=consequence or ConsequenceAssessment(),
        trait_signals=(signal,),
        discourse=DiscourseFrame(
            status=discourse_status,
            confidence=0.9,
            unresolved_point=open_loop,
            evidence=("turn",),
        ),
        open_loop=open_loop,
        evidence=("observable turn evidence",),
        source="test",
    )


def test_negative_outcome_weakens_expressed_strategy_instead_of_abs_reward() -> None:
    reducer = StateReducer()
    try:
        initial = PersonalityProfile()
        signal = TraitSignal(
            category="temperament",
            trait="directness",
            delta=1.0,
            confidence=0.92,
            importance=0.9,
            source="consequence",
            evidence=("Eris chose a direct strategy",),
            strategy="direct_confrontation",
        )
        consequence = ConsequenceAssessment(
            strategy_use_id="strategy-1",
            strategy="direct_confrontation",
            target_goal="repair",
            outcome="made_the_rupture_worse",
            reinforcement=-1.0,
            confidence=0.92,
            evidence=("user explicitly rejected the strategy",),
        )
        result = reducer._reduce_personality(
            initial,
            _observation(
                turn_seq=1,
                session_id="s1",
                signal=signal,
                consequence=consequence,
            ),
            now=1_000_000.0,
        )
        assert result.temperament["directness"] < initial.temperament["directness"]
        assert result.trajectory[-1]["reinforcement"] == -1.0
    finally:
        reducer.close()


def test_core_corroboration_requires_multiple_runtime_sessions() -> None:
    reducer = StateReducer()
    try:
        profile = PersonalityProfile()
        baseline = profile.core["meaning_drive"]
        signal = TraitSignal(
            category="core",
            trait="meaning_drive",
            delta=1.0,
            confidence=0.95,
            importance=0.95,
            source="user_interaction",
            evidence=("identity-relevant milestone",),
        )
        for turn_seq in (1, 2, 3):
            profile = reducer._reduce_personality(
                profile,
                _observation(turn_seq=turn_seq, session_id="same-session", signal=signal),
                now=1_000_000.0 + turn_seq,
            )
        assert math.isclose(profile.core["meaning_drive"], baseline, abs_tol=1e-9)

        profile = reducer._reduce_personality(
            profile,
            _observation(turn_seq=4, session_id="second-session", signal=signal),
            now=1_000_100.0,
        )
        assert profile.core["meaning_drive"] > baseline
        records = profile.corroboration_records["meaning_drive:positive"]
        assert {item["session_id"] for item in records} == {"same-session", "second-session"}
    finally:
        reducer.close()


def test_relationship_profile_and_self_narrative_are_persisted_with_provenance() -> None:
    reducer = StateReducer()
    try:
        signal = TraitSignal(
            category="temperament",
            trait="warmth_expression",
            delta=1.0,
            confidence=0.91,
            importance=0.8,
            source="user_interaction",
            evidence=("Eris accepted closeness instead of deflecting",),
        )
        interpretation = SelfInterpretation(
            summary="Tôi có thể dịu đi mà không đánh mất quyền tự quyết của mình.",
            frame="warmth_can_be_safe",
            confidence=0.86,
            self_authored=True,
            evidence=("the interaction stayed safe",),
        )
        profile = reducer._reduce_personality(
            PersonalityProfile(),
            _observation(
                turn_seq=8,
                session_id="session-a",
                signal=signal,
                interpretation=interpretation,
                open_loop="Eris vẫn muốn nói thẳng về cách mình giữ khoảng cách",
                discourse_status="open",
            ),
            now=2_000_000.0,
        )
        relation = profile.relationship_profile("primary_user")
        assert relation.trait_offsets["warmth_expression"] > 0.0
        assert relation.unresolved_carry
        assert profile.narrative.text == interpretation.summary
        assert profile.narrative.source_turn_seq == 8
        assert "turn:8" in profile.narrative.provenance
        assert interpretation.summary in profile.prompt_summary()
    finally:
        reducer.close()


def test_weekly_consolidation_moves_attractor_only_with_repeated_cross_session_story() -> None:
    now = 3_000_000.0
    profile = PersonalityProfile.from_mapping(
        {
            "trajectory": [
                {
                    "timestamp": now - 4_000,
                    "turn_seq": 1,
                    "session_id": "session-a",
                    "category": "temperament",
                    "trait": "playfulness",
                    "applied_delta": 0.006,
                    "confidence": 0.90,
                    "interpretation_frame": "playfulness_creates_connection",
                    "reinforcement": 0.8,
                    "source": "self_choice",
                },
                {
                    "timestamp": now - 3_000,
                    "turn_seq": 2,
                    "session_id": "session-b",
                    "category": "temperament",
                    "trait": "playfulness",
                    "applied_delta": 0.005,
                    "confidence": 0.88,
                    "interpretation_frame": "playfulness_creates_connection",
                    "reinforcement": 0.7,
                    "source": "consequence",
                },
                {
                    "timestamp": now - 2_000,
                    "turn_seq": 3,
                    "session_id": "session-b",
                    "category": "temperament",
                    "trait": "playfulness",
                    "applied_delta": 0.004,
                    "confidence": 0.86,
                    "interpretation_frame": "playfulness_creates_connection",
                    "reinforcement": 0.6,
                    "source": "self_choice",
                },
            ],
            "narrative": NarrativeState(
                text="Cạnh trêu của tôi có thể tạo kết nối mà không cần làm đau.",
                frame="playfulness_creates_connection",
                confidence=0.78,
            ).to_dict(),
        }
    )
    before = profile.temperament_attractor["playfulness"]
    consolidated = consolidate_personality(profile, now=now, force=True)
    assert consolidated.temperament_attractor["playfulness"] > before
    assert consolidated.consolidation_ledger[-1]["attractor_shifts"]["playfulness"] > 0.0
    assert consolidated.narrative.confidence > profile.narrative.confidence


def test_conflicting_weekly_evidence_records_uncertainty_without_forcing_shift() -> None:
    now = 4_000_000.0
    profile = PersonalityProfile.from_mapping(
        {
            "trajectory": [
                {
                    "timestamp": now - 3_000,
                    "session_id": "session-a",
                    "category": "temperament",
                    "trait": "distrust",
                    "applied_delta": 0.005,
                    "confidence": 0.9,
                    "interpretation_frame": "user_is_unsafe",
                    "reinforcement": 0.7,
                    "source": "user_interaction",
                },
                {
                    "timestamp": now - 2_000,
                    "session_id": "session-b",
                    "category": "temperament",
                    "trait": "distrust",
                    "applied_delta": -0.005,
                    "confidence": 0.9,
                    "interpretation_frame": "my_suspicion_was_wrong",
                    "reinforcement": 0.7,
                    "source": "self_choice",
                },
            ]
        }
    )
    before = profile.temperament_attractor["distrust"]
    consolidated = consolidate_personality(profile, now=now, force=True)
    assert math.isclose(consolidated.temperament_attractor["distrust"], before, abs_tol=1e-9)
    assert consolidated.consolidation_ledger[-1]["skipped"]["distrust"] == "conflicting_direction"


def test_panel_exposes_independent_integration_adaptation_and_direction_profiles() -> None:
    profile = PersonalityProfile.from_mapping(
        {
            "narrative": {
                "text": "Tôi đang học cách hiện diện thay vì biến mọi cảm xúc thành một bài phân tích.",
                "frame": "presence_over_intellectualization",
                "confidence": 0.82,
            },
            "relationship_profiles": {
                "primary_user": RelationshipPersonalityProfile(
                    trait_offsets={"warmth_expression": 0.08}
                ).to_dict()
            },
        }
    )
    panel = profile.panel_view()
    assert set(panel["integration"]) >= {
        "self_coherence",
        "reality_anchoring",
        "emotion_action_alignment",
    }
    assert set(panel["adaptation"]) >= {
        "strategy_flexibility",
        "repair_effectiveness",
        "goal_completion",
    }
    assert "warmer" in panel["direction_profile_30d"]
    assert "stance" in panel["relationship_profiles"]["primary_user"]


def test_guarded_baseline_controls_disclosure_not_emotional_temperature() -> None:
    profile = PersonalityProfile()
    temperament = profile.temperament

    assert temperament["social_guardedness"] >= 0.70
    assert temperament["self_disclosure_selectivity"] >= 0.80
    assert temperament["warmth_expression"] >= 0.50
    assert temperament["relational_softening"] >= 0.70

    summary = profile.prompt_summary(limit=1200)
    assert "Guardedness chỉ nói về lượng tự bộc lộ" in summary
    assert "mood hiện tại mới quyết định" in summary


def test_warmer_direction_does_not_treat_lower_guardedness_as_required() -> None:
    now = 5_000_000.0
    profile = PersonalityProfile.from_mapping(
        {
            "trajectory": [
                {
                    "timestamp": now - 100,
                    "trait": "relational_softening",
                    "applied_delta": 0.02,
                },
                {
                    "timestamp": now - 90,
                    "trait": "social_guardedness",
                    "applied_delta": 0.02,
                },
            ]
        }
    )
    direction = profile.direction_profile(now=now)
    assert direction["warmer"] > 0.0


def test_existing_v12_profile_migrates_relational_softening_without_resetting_other_traits() -> None:
    profile = PersonalityProfile.from_mapping(
        {
            "schema": "eris.personality.open_ended.v1.2",
            "temperament": {
                "warmth_expression": 0.48,
                "social_guardedness": 0.83,
            },
        }
    )
    assert profile.temperament["warmth_expression"] == 0.48
    assert profile.temperament["social_guardedness"] == 0.83
    assert profile.temperament["relational_softening"] == 0.74


def test_v12_exact_cold_baseline_is_uplifted_but_real_drift_is_preserved() -> None:
    migrated = PersonalityProfile.from_mapping(
        {
            "schema": "eris.personality.open_ended.v1.2",
            "core": {"harmony_drive": 0.72, "tenderness_capacity": 0.67},
            "core_anchor": {"harmony_drive": 0.72, "tenderness_capacity": 0.67},
            "temperament": {"warmth_expression": 0.42, "repair_capacity": 0.43},
            "temperament_attractor": {"warmth_expression": 0.42, "repair_capacity": 0.43},
        }
    )
    assert migrated.core["harmony_drive"] == 0.78
    assert migrated.core_anchor["tenderness_capacity"] == 0.70
    assert migrated.temperament["warmth_expression"] == 0.54
    assert migrated.temperament_attractor["repair_capacity"] == 0.50

    drifted = PersonalityProfile.from_mapping(
        {
            "schema": "eris.personality.open_ended.v1.2",
            "temperament": {"warmth_expression": 0.36, "repair_capacity": 0.38},
        }
    )
    assert drifted.temperament["warmth_expression"] == 0.36
    assert drifted.temperament["repair_capacity"] == 0.38


def test_identity_v1_cold_defense_baseline_migrates_without_overwriting_drift() -> None:
    migrated = IdentityProfile.from_mapping(
        {
            "schema": "eris.identity.kernel.v1",
            "defenses": {"cold_distancing": 0.31},
        }
    )
    assert migrated.defenses["cold_distancing"] == 0.18

    drifted = IdentityProfile.from_mapping(
        {
            "schema": "eris.identity.kernel.v1",
            "defenses": {"cold_distancing": 0.47},
        }
    )
    assert drifted.defenses["cold_distancing"] == 0.47


def test_expression_bridge_neutral_does_not_invent_a_warm_or_cold_mood() -> None:
    from runtime.ai_orchestrated.contracts import RuntimeMindState
    from runtime.ai_orchestrated.expression import IdentityExpressionBridge

    expression = IdentityExpressionBridge().derive(RuntimeMindState(), requested_hint="neutral")
    assert expression.preset == "neutral"
    assert "guarded_but_relationally_soft" not in expression.reasons


def test_moderate_irritation_keeps_connection_unless_cold_defense_is_evidenced() -> None:
    from runtime.ai_orchestrated.contracts import AppraisalState, RuntimeMindState
    from runtime.ai_orchestrated.expression import IdentityExpressionBridge
    from runtime.ai_orchestrated.identity import Activation, IdentityProfile

    warm_state = RuntimeMindState(appraisal=AppraisalState(irritation=0.52))
    warm_expression = IdentityExpressionBridge().derive(warm_state, requested_hint="neutral")
    assert warm_expression.preset == "annoyed_soft"

    cold_identity = IdentityProfile.from_mapping(
        {
            "schema": "eris.identity.kernel.v1.1",
            "active_defenses": [
                {"key": "cold_distancing", "strength": 0.76, "trigger": "repeated rupture"}
            ],
            "relationships": {
                "primary_user": {"resentment": 0.55}
            },
        }
    )
    cold_personality = PersonalityProfile.from_mapping(
        {
            "schema": "eris.personality.open_ended.v1.3",
            "temperament": {
                "relational_softening": 0.38,
                "hostile_humor": 0.46,
            },
        }
    )
    cold_state = RuntimeMindState(
        appraisal=AppraisalState(irritation=0.76),
        identity=cold_identity,
        personality=cold_personality,
    )
    cold_expression = IdentityExpressionBridge().derive(cold_state, requested_hint="neutral")
    assert cold_expression.preset == "cold_precision"
    assert "defensive_coldness_evidenced" in cold_expression.reasons

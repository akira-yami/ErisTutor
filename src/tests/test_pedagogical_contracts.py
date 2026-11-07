from dataclasses import FrozenInstanceError
import math

import pytest

from runtime.pedagogical_contracts import (
    ComprehensionState,
    TeachingPlan,
    TeachingConcept,
    TeachingDirective,
    TeachingEpisode,
    TeachingReceipt,
    normalize_episode,
    validate_directive,
)


def test_episode_round_trips_with_bounded_fields():
    episode = TeachingEpisode.new(
        episode_id="teach:1",
        topic_key="core7_vs_core8",
        global_goal="distinguish motivation from behavior",
        concepts=(TeachingConcept("motivation", "Separate motive from behavior"),),
        now=100.0,
        expires_at=700.0,
    )
    restored = TeachingEpisode.from_mapping(episode.to_dict())
    assert restored == episode
    assert len(restored.global_goal) <= 420
    assert restored.status == "planning"
    assert restored.plan_revision == 1
    assert restored.pending_chunk is None


def test_invalid_status_and_out_of_range_values_fail_closed():
    assert normalize_episode({"status": "invented", "comprehension": {}}) is None
    state = ComprehensionState.from_mapping({"estimated_mastery": 4, "confidence": -2})
    assert state.estimated_mastery == 1.0
    assert state.confidence == 0.0


def test_directive_cannot_exceed_hard_chunk_limits():
    directive = TeachingDirective(
        episode_id="teach:1", plan_revision=1, chunk_id="chunk:1",
        global_goal="goal", current_objective="obj", claim="claim",
        contrast="contrast", example_policy="allowed", max_ideas=99,
        max_sentences=99, allow_question=True, stop_after_chunk=True,
        allow_autonomous_resume=True,
    )
    valid, reason = validate_directive(directive)
    assert not valid
    assert reason == "directive_budget_exceeded"


def test_contracts_are_frozen_and_serialization_bounds_text():
    episode = TeachingEpisode.new(
        episode_id="e" * 1000,
        topic_key="topic",
        global_goal="g" * 1000,
        concepts=(TeachingConcept("c", "d" * 1000),),
        now=1.0,
        expires_at=2.0,
    )
    payload = episode.to_dict()
    assert len(payload["episode_id"]) <= 120
    assert len(payload["global_goal"]) <= 420
    assert len(payload["concepts"][0]["description"]) <= 420
    with pytest.raises(FrozenInstanceError):
        episode.status = "ready"


def test_plan_rejects_concept_cardinality_outside_three_to_eight():
    concept = TeachingConcept("c", "description")
    with pytest.raises(ValueError, match="concepts_required"):
        TeachingPlan("goal", (concept, concept))
    with pytest.raises(ValueError, match="concepts_required"):
        TeachingPlan("goal", ())
    with pytest.raises(ValueError, match="concepts_limit"):
        TeachingPlan("goal", tuple(TeachingConcept(str(i), "d") for i in range(9)))


def test_persisted_scalars_are_finite_bounded_and_boolean():
    episode = TeachingEpisode(
        "e", "topic", "goal", (TeachingConcept("c", "d"),),
        float("nan"), float("inf"), plan_revision=10**99,
        autonomous_chunks_used=10**99, repair_attempts=-4, consecutive_stalls=10**99,
    )
    payload = episode.to_dict()
    assert math.isfinite(payload["started_at"]) and math.isfinite(payload["expires_at"])
    assert payload["plan_revision"] <= 1_000_000
    assert payload["autonomous_chunks_used"] <= 1_000_000
    receipt = TeachingReceipt("e", 1, "chunk", successful="false")
    assert receipt.to_dict()["successful"] is False
    directive = TeachingDirective("e", 1, "chunk", "g", "o", "c", "x", "allowed", 1, 1,
                                  allow_question="false", stop_after_chunk=0, allow_autonomous_resume=object())
    assert directive.to_dict()["allow_question"] is False
    assert directive.to_dict()["stop_after_chunk"] is False
    assert directive.to_dict()["allow_autonomous_resume"] is False


def test_directive_rejects_non_positive_budgets():
    directive = TeachingDirective("e", 1, "chunk", "g", "o", "c", "x", "allowed", 0, 1, True, True, True)
    assert validate_directive(directive) == (False, "directive_budget_exceeded")
    directive = TeachingDirective("e", 1, "chunk", "g", "o", "c", "x", "allowed", 1, 0, True, True, True)
    assert validate_directive(directive) == (False, "directive_budget_exceeded")


def test_episode_persists_bounded_activity_and_repair_scope():
    episode = TeachingEpisode(
        "e", "topic", "goal", (TeachingConcept("c", "d"),), 1.0, 601.0,
        last_activity_at=float("inf"), repair_concept_id="repair" * 100,
    )
    restored = TeachingEpisode.from_mapping(episode.to_dict())
    assert restored == episode
    assert restored.last_activity_at == 0.0
    assert len(restored.repair_concept_id) <= 120

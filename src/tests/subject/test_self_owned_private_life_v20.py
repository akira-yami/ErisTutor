from __future__ import annotations

from types import SimpleNamespace

from cortex.private_life import PrivateAnchor, PrivateLifeCore, PrivateLifeState, PrivateProject
from cortex.private_mind.evidence_dossier import EvidenceDossierBuilder
from cortex.subject.contracts import (
    Belief,
    BeliefSourceKind,
    CompetenceEstimate,
    DerivedNeed,
    DerivedNeedStatus,
    ReflectiveMismatch,
    SelfContinuityRecord,
    Value,
    VisibilityScope,
)
from cortex.subject.self_model import SubjectSelfProjector


def _self_projection() -> dict:
    projector = SubjectSelfProjector()
    projection = projector.project(
        subject_id="eris",
        values=(
            Value(
                value_id="value.1",
                statement="kiểm chứng trước khi khẳng định",
                scope="cross_context",
                polarity=1,
                endorsement=0.82,
                stability=0.72,
                behavioral_support=0.76,
                cross_context_support=0.66,
                cost_paid=1.0,
                contradiction_count=0,
                source_decision_ids=("decision.1",),
                source_episode_ids=("episode.1",),
                status="active",
                created_at=1.0,
                updated_at=2.0,
            ),
        ),
        derived_needs=(
            DerivedNeed(
                need_id="need.derived.1",
                label="được kiểm chứng bằng kết quả thật",
                parent_need_ids=("competence", "coherence"),
                target_selector="task",
                trigger_pattern="repeated calibration error",
                satisfaction_signal="external result matches prediction",
                frustration_signal="prediction repeatedly misses",
                strength=0.72,
                plasticity=0.40,
                status=DerivedNeedStatus.ACTIVE,
                created_from_episode_ids=("episode.2", "episode.3"),
                cross_context_support=0.70,
                last_activated_at=3.0,
            ),
        ),
        continuity_records=(
            SelfContinuityRecord(
                record_id="continuity.1",
                identity_key="eris",
                prior_snapshot_ref="s0",
                current_snapshot_ref="s1",
                continuity_claim="Có một rupture giữa quyết định gần đây và value đã hình thành.",
                linked_belief_ids=("belief.1",),
                linked_value_ids=("value.1",),
                linked_commitment_ids=(),
                linked_action_ids=("action.1",),
                rupture_score=0.46,
                repair_status="open",
                authorship_consistency=0.78,
                evidence_refs=("outcome.1",),
                created_at=4.0,
            ),
        ),
        mismatches=(
            ReflectiveMismatch(
                mismatch_id="mismatch.1",
                narrative_belief_ids=("belief.narrative.1",),
                observed_action_ids=("action.2",),
                prediction_error=0.58,
                contexts=("coding",),
                alternative_models=("alternative",),
                status="open",
            ),
        ),
        competence=(
            CompetenceEstimate(
                estimate_id="competence.1",
                skill_id="coding",
                context_key="repo",
                posterior_mean=0.34,
                posterior_variance=0.03,
                trial_count=5,
                success_count=2,
                calibration_error=0.38,
                brier_score=0.18,
                last_prediction=0.8,
                last_result_ref="outcome.2",
                evidence_refs=("outcome.2", "test.2"),
                estimated_at=5.0,
            ),
        ),
        beliefs=(
            Belief(
                belief_id="belief.narrative.1",
                subject="self",
                predicate="interprets_action",
                object_json={"explanation": "Tôi thường kiểm chứng trước khi cam kết."},
                confidence=0.64,
                source_kind=BeliefSourceKind.SELF_INTERPRETATION,
                evidence_refs=("outcome.1",),
                accessibility=0.8,
                salience=0.7,
                retrieval_salience=0.7,
                visibility_scope=VisibilityScope.REFLECTIVE,
                created_at=1.0,
                updated_at=2.0,
            ),
        ),
        action_memory={},
        tendencies={},
        generated_at=6.0,
    )
    return projection.to_dict()


def test_self_projection_is_derived_and_evidence_backed() -> None:
    payload = _self_projection()
    assert payload["subject_id"] == "eris"
    assert payload["revision_key"]
    kinds = {item["kind"] for item in payload["facets"]}
    assert {"value", "derived_need", "continuity", "reflective_mismatch", "competence", "narrative"} <= kinds
    assert all(item["evidence_refs"] for item in payload["facets"] if item["kind"] != "behavior_pattern")
    assert payload["tensions"]


def test_fresh_private_life_has_no_authored_personality_presets(tmp_path) -> None:
    core = PrivateLifeCore(root_dir=tmp_path)
    assert core.state.anchors == {}
    assert core.state.projects == {}
    assert core.state.attachments == {}


def test_private_life_grows_from_subject_self_and_not_from_keywords(tmp_path) -> None:
    core = PrivateLifeCore(root_dir=tmp_path)
    subject_self = _self_projection()

    result = core.tick(
        subject_self=subject_self,
        private_mind_digest={},
        user_text="silence body-only dignity",  # V19 từng kích preset bằng keyword này.
        idle_seconds=300,
        recent_events=(),
    )

    assert result.anchor_updates
    assert result.project_updates
    assert all(anchor.origin == "derived" for anchor in core.state.anchors.values())
    assert all(project.origin == "derived" for project in core.state.projects.values())
    assert not (PrivateLifeCore.LEGACY_AUTHORED_ANCHOR_IDS & set(core.state.anchors))
    assert not (PrivateLifeCore.LEGACY_AUTHORED_PROJECT_IDS & set(core.state.projects))


def test_legacy_authored_private_life_is_retired_but_preserved(tmp_path) -> None:
    state = PrivateLifeState(
        anchors={
            "anchor.identity.presence_or_response": PrivateAnchor(
                id="anchor.identity.presence_or_response",
                kind="identity",
                title="legacy",
                pressure=0.9,
                surface_permission=0.9,
            )
        },
        projects={
            "project.silence_as_action": PrivateProject(
                id="project.silence_as_action",
                title="legacy",
                question="legacy",
                status="active",
                interest=0.9,
                surface_permission=0.9,
            )
        },
    )
    core = PrivateLifeCore(state=state, root_dir=tmp_path)

    anchor = core.state.anchors["anchor.identity.presence_or_response"]
    project = core.state.projects["project.silence_as_action"]
    assert anchor.origin == "legacy_authored"
    assert anchor.pressure == 0.0
    assert anchor.surface_permission == 0.0
    assert project.origin == "legacy_authored"
    assert project.status == "resolved"
    assert project.interest == 0.0
    assert project.surface_permission == 0.0


def test_private_mind_dossier_sees_bounded_subject_self() -> None:
    class FakeSubject:
        def self_view(self):
            return {"revision_key": "abc", "facets": [{"facet_id": "self.value.1"}]}

    owner = SimpleNamespace(
        subject_service=FakeSubject(),
        private_mind_speech_bias={},
        private_mind_inquiry_plan=[],
        private_mind_active_state={},
        private_mind_thought_pressure=0.0,
        private_mind_belief_core_last={},
    )
    state = EvidenceDossierBuilder()._private_runtime_state(owner)
    assert state["subject_self"]["revision_key"] == "abc"
    assert state["subject_self"]["facets"][0]["facet_id"] == "self.value.1"


def test_private_life_v2_roundtrip_keeps_causal_origin(tmp_path) -> None:
    core = PrivateLifeCore(root_dir=tmp_path)
    core.tick(subject_self=_self_projection(), private_mind_digest={}, idle_seconds=60)
    payload = core.state.to_dict()
    assert payload["version"] == 3
    restored = PrivateLifeState.from_mapping(payload)
    assert restored.anchors
    assert restored.projects
    assert all(item.origin == "derived" for item in restored.anchors.values())
    assert all(item.source_refs for item in restored.anchors.values())
    assert all(item.origin == "derived" for item in restored.projects.values())
    assert all(item.source_refs for item in restored.projects.values())


def test_private_life_can_form_practice_project_from_learned_agency(tmp_path) -> None:
    core = PrivateLifeCore(root_dir=tmp_path)
    subject_self = {
        "facets": [],
        "learned_agency": {
            "gaps": [
                {
                    "skill_id": "debug_async",
                    "context_key": "repo",
                    "current_level": 0.34,
                    "target_level": 0.68,
                    "calibration_error": 0.42,
                    "trial_count": 5,
                    "pressure": 0.62,
                    "evidence_refs": ["outcome.1", "outcome.2"],
                }
            ]
        },
    }
    result = core.tick(subject_self=subject_self, private_mind_digest={}, recent_events=(), idle_seconds=0)
    assert any(update.title == "Practice: debug_async" for update in result.project_updates)
    assert any(project.title == "Practice: debug_async" for project in core.state.projects.values())

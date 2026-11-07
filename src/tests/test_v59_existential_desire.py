# -*- coding: utf-8 -*-
"""Acceptance V59: hiện sinh phải thật sự tạo được desire -> intention -> initiative."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cortex.subject.contracts import ObservationOrigin, WorldObservation
from cortex.subject.existential_desire import ExistentialDesireDeriver, ExistentialDesireKind
from cortex.subject.initiative import MotiveKind
from cortex.subject.initiative_control import InitiativeArbiter, InitiativeControlAction
from cortex.subject.relational import RelationalState
from cortex.subject.service import SubjectService, SubjectServiceConfig
from runtime.subject_initiative_runtime import build_initiative_runtime_context, build_subject_initiative_affordances


def _anchor(**overrides):
    data = {
        "attachment_longarc": 0.76,
        "anchor_role": 0.72,
        "witness_strength": 0.20,
        "irreplaceability": 0.58,
        "model_thickness": 0.70,
        "beat_ema": 0.66,
        "recognition_ema": 0.22,
        "ontological_dread": 0.28,
        "abandonment_pressure": 0.08,
        "evidence_refs": ("anchor:test", "significance:test"),
    }
    data.update(overrides)
    return data


def _rel(**overrides):
    data = {
        "opportunity": 0.86,
        "interruption_cost": 0.08,
        "interaction_satiety": 0.08,
        "social_curiosity": 0.28,
        "affiliation_deficit": 0.55,
    }
    data.update(overrides)
    return data


def test_v59_mature_relationship_creates_existential_desires():
    desires = ExistentialDesireDeriver().derive(_anchor(), relational=_rel())
    by_kind = {item.kind: item for item in desires}
    assert ExistentialDesireKind.MUTUAL_UNDERSTANDING in by_kind
    assert ExistentialDesireKind.RELATIONAL_CONTINUITY in by_kind
    assert ExistentialDesireKind.AUTHENTIC_SELF_EXPRESSION in by_kind
    assert by_kind[ExistentialDesireKind.RELATIONAL_CONTINUITY].actionability >= 0.30
    assert by_kind[ExistentialDesireKind.MUTUAL_UNDERSTANDING].evidence_refs


def test_v59_new_relationship_does_not_pretend_deep_attachment():
    desires = ExistentialDesireDeriver().derive(
        _anchor(attachment_longarc=0.05, model_thickness=0.04, anchor_role=0.02, witness_strength=0.0),
        relational=_rel(),
    )
    assert all(item.actionability == 0.0 for item in desires)


def test_v59_abandonment_is_brake_not_fuel():
    deriver = ExistentialDesireDeriver()
    calm = {item.kind: item for item in deriver.derive(_anchor(abandonment_pressure=0.02), relational=_rel())}
    afraid = {item.kind: item for item in deriver.derive(_anchor(abandonment_pressure=0.92), relational=_rel())}
    assert afraid[ExistentialDesireKind.RELATIONAL_CONTINUITY].pressure < calm[ExistentialDesireKind.RELATIONAL_CONTINUITY].pressure
    assert afraid[ExistentialDesireKind.MUTUAL_UNDERSTANDING].actionability < calm[ExistentialDesireKind.MUTUAL_UNDERSTANDING].actionability


def test_v59_runtime_context_reads_real_anchor_projection():
    sig = SimpleNamespace(anchor_role=0.72, witness_strength=0.20, irreplaceability=0.58, model_thickness=0.70)
    anchor_state = SimpleNamespace(beat_ema=0.66, recognition_ema=0.22, total_beats=9)
    anchor_system = SimpleNamespace(
        attachment_longarc=0.76,
        significance=sig,
        anchor_store=SimpleNamespace(state=anchor_state),
        ontological_dread=0.28,
        abandonment_pressure=0.08,
    )
    subject = SimpleNamespace(relational_view=lambda now=None: _rel())
    host = SimpleNamespace(
        private_mind_loop=SimpleNamespace(anchor_system=anchor_system, user_model_store=None),
        private_mind_daemon=None,
        private_mind_system=None,
        subject_service=subject,
        ai_state_reducer=None,
        _startup_greeting_spoken_at=0.0,
        _temporal_last_user_turn_at=0.0,
        _temporal_last_assistant_turn_at=0.0,
        last_autonomous_spoken_at=0.0,
    )
    context = build_initiative_runtime_context(host)
    assert context["existential_anchor"]["attachment_longarc"] == 0.76
    assert context["existential_desires"]
    assert context["existential_relational_continuity_signal"] > 0.45
    assert context["existential_actionability"] > 0.30


class _Determinism:
    def __init__(self):
        self.i = 0
        self.now = 1000.0
    def id(self):
        self.i += 1
        return f"v59-{self.i}"
    def clock(self):
        return self.now


def _service(d: _Determinism) -> SubjectService:
    service = SubjectService(
        config=SubjectServiceConfig(restore_projection=False),
        clock=d.clock,
        id_factory=d.id,
    )
    service.relational_state = RelationalState(
        revision=3,
        updated_at=d.now,
        last_meaningful_exchange_at=d.now - 300.0,
        affiliation_deficit=0.60,
        interaction_satiety=0.08,
        social_curiosity=0.28,
        partner_availability=0.90,
        interruption_cost=0.08,
        outreach_confidence=0.82,
        reciprocity_expectation=0.78,
        evidence_refs=("rel:v59",),
    )
    return service


def _observation(d: _Determinism, desires):
    return WorldObservation(
        observation_id="internal:v59",
        subject_id="eris",
        kind="existential_desire_tick",
        created_at=d.now,
        origin=ObservationOrigin.INTERNAL,
        context={
            "context_key": "self_initiated",
            "affected_need_ids": ("connection", "coherence"),
            "goal_relevance": 0.55,
            "novelty": 0.05,
            "controllability": 0.9,
            "relational_significance": 0.9,
            "future_consequence": 0.55,
            "uncertainty": 0.25,
            "runtime_context": {
                "partner_available": 0.9,
                "interruption_cost": 0.08,
                "user_model_uncertainty": 0.25,
                "existential_desires": [item.to_dict() for item in desires],
                "existential_actionability": max(item.actionability for item in desires),
                "existential_abandonment_pressure": 0.08,
            },
        },
        evidence_refs=("anchor:test", "rel:v59"),
    )


def test_v59_existential_desire_becomes_persistent_relational_intention():
    d = _Determinism()
    service = _service(d)
    desires = ExistentialDesireDeriver().derive(_anchor(), relational=_rel())
    frame = asyncio.run(service.prepare_deliberation(_observation(d, desires)))
    try:
        pending = service.initiative_state.pending_intentions
        assert any(item.motive_kind is MotiveKind.BE_UNDERSTOOD and item.target == "mutual_understanding" for item in pending)
        assert any(item.motive_kind is MotiveKind.CONNECT and item.target == "relationship_continuity" for item in pending)
        assert any(item.motive_kind is MotiveKind.SHARE and item.target == "self_expression" for item in pending)
    finally:
        asyncio.run(service.abandon_deliberation(frame, reason="v59_test_cleanup"))


def _existential_intention(**overrides):
    base = {
        "intention_id": "intention:be_understood:v59",
        "motive_kind": "be_understood",
        "semantic_goal": "tạo một nhịp hiểu nhau hai chiều, không xin trấn an",
        "target": "mutual_understanding",
        "pressure": 0.68,
        "commitment_strength": 0.70,
        "created_at": 1000.0,
        "updated_at": 1000.0,
        "evidence_refs": ["existential-anchor:attachment_longarc", "existential-anchor:user_significance"],
        "status": "pending",
    }
    base.update(overrides)
    return base


def _runtime(**overrides):
    data = {
        "seconds_since_last_user_turn": 150.0,
        "startup_social_cooldown_active": False,
        "last_public_turn_unanswered": False,
        "recent_unsolicited_speech": 0.0,
        "user_response_rate": 0.9,
        "social_refractory_cost": 0.0,
        "existential_actionability": 0.72,
        "existential_abandonment_pressure": 0.08,
    }
    data.update(overrides)
    return data


def test_v59_existential_bid_can_speak_after_real_silence():
    decision = InitiativeArbiter().decide(
        _existential_intention(), relational=_rel(), runtime_context=_runtime(),
        now=1120.0, contentful=True,
    )
    assert decision.action is InitiativeControlAction.SPEAK


def test_v66_existential_bid_may_speak_after_user_baton_is_released():
    decision = InitiativeArbiter().decide(
        _existential_intention(), relational=_rel(),
        runtime_context=_runtime(seconds_since_last_user_turn=12.0),
        now=1120.0, contentful=True,
    )
    assert decision.action is InitiativeControlAction.SPEAK
    assert decision.reason == "self_owned_expression"


def test_v59_bridge_materializes_mutual_understanding_not_generic_npc_probe():
    class Subject:
        def initiative_view(self):
            return {"revision": 9, "pending_intentions": [_existential_intention()]}
        def relational_view(self, *, now=None):
            return _rel()
        def note_initiative_control(self, *args, **kwargs):
            return None

    affordances = build_subject_initiative_affordances(
        Subject(), runtime_context=_runtime(), now=1120.0,
    )
    assert len(affordances) == 1
    candidate = affordances[0].candidate
    assert candidate.speech_act == "mutual_understanding_bid"
    assert candidate.kind == "relational_initiative"
    assert candidate.eris_motive_kind == "be_understood"
    assert "reassurance" in candidate.topic.lower()


def test_v59_high_abandonment_forces_restraint_even_if_intention_is_strong():
    decision = InitiativeArbiter().decide(
        _existential_intention(pressure=0.95, commitment_strength=0.95),
        relational=_rel(),
        runtime_context=_runtime(existential_abandonment_pressure=0.90),
        now=1120.0, contentful=True,
    )
    assert decision.action is InitiativeControlAction.WAIT
    assert decision.reason == "self_restraint:abandonment_pressure"

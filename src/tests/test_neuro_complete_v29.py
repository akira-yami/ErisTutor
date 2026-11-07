from __future__ import annotations

from dataclasses import replace

import pytest

from cortex.subject.contracts import OutcomeStatus, SubjectExecutiveCommit, WorldObservation, WorldOutcome
from cortex.subject.service import StaleDeliberationFrame, SubjectService, SubjectServiceConfig
from llm.client import _web_session_header
from memory.memory_activation import MemoryActivation, MemoryRetrievalResult, WorkingMemoryGate
from memory.memory_cue import MemoryCue
from runtime.inference_broker import inference_context
from runtime.semantic_frame import ReferenceKind, validate_semantic_frame
from runtime.session_turn_ledger import SessionTurnLedger


class Determinism:
    def __init__(self) -> None:
        self.n = 0
        self.now = 1000.0

    def id(self) -> str:
        self.n += 1
        return f"id-{self.n}"

    def clock(self) -> float:
        self.now += 1.0
        return self.now


class Audit:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append(self, event: dict) -> None:
        self.events.append(dict(event))


def observation(d: Determinism, suffix: str) -> WorldObservation:
    return WorldObservation(
        observation_id=f"obs-{suffix}",
        subject_id="eris",
        kind="user_turn",
        created_at=d.clock(),
        evidence_refs=(f"turn:{suffix}",),
        context={
            "context_key": "direct_chat",
            "user_text": f"message {suffix}",
            "affected_need_ids": ("coherence",),
            "goal_relevance": 0.8,
            "controllability": 0.8,
            "uncertainty": 0.25,
            "action_proposals": (
                {
                    "proposal_id": f"proposal:{suffix}:speak",
                    "action_kind": "speak",
                    "arguments": {"semantic_target": "answer", "skill_id": "dialogue"},
                    "supporting_need_ids": ("coherence",),
                    "expected_need_relief": 0.8,
                    "expected_action_efficacy": 0.8,
                    "expected_epistemic_value": 0.4,
                    "value_congruence": 0.7,
                    "substrate_cost": 0.05,
                    "uncertainty": 0.2,
                },
                {
                    "proposal_id": f"proposal:{suffix}:silence",
                    "action_kind": "silence",
                    "arguments": {"skill_id": "restraint"},
                    "supporting_need_ids": ("coherence",),
                    "expected_need_relief": 0.2,
                    "expected_action_efficacy": 0.5,
                    "expected_epistemic_value": 0.1,
                    "value_congruence": 0.5,
                    "substrate_cost": 0.0,
                    "uncertainty": 0.2,
                },
            ),
        },
    )


def commit_for(frame, d: Determinism) -> SubjectExecutiveCommit:
    candidate = frame.coalitions[0]
    return SubjectExecutiveCommit(
        commit_id=f"commit:{d.id()}",
        frame_id=frame.frame_id,
        selected_coalition_id=candidate.coalition_id,
        selected_proposal_ref=candidate.primary_proposal_ref,
        semantic_commitments=(),
        cognitive_requests=(),
        reason="test",
        confidence=0.8,
        review_conditions=(),
        created_at=d.clock(),
        source="neuro_fast_gate",
    )


@pytest.mark.asyncio
async def test_new_cognitive_generation_invalidates_old_model_result() -> None:
    d = Determinism()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=d.clock,
        id_factory=d.id,
    )
    old = await service.prepare_deliberation(observation(d, "old"))
    newer = await service.prepare_deliberation(observation(d, "new"))
    assert newer.revision == old.revision + 1

    with pytest.raises(StaleDeliberationFrame):
        await service.finalize_deliberation(old, commit_for(old, d))

    decision = await service.finalize_deliberation(newer, commit_for(newer, d))
    assert decision.observation_ref == newer.observation.observation_id


def test_public_ledger_revision_tracks_turns_not_metadata() -> None:
    ledger = SessionTurnLedger()
    assert ledger.revision == 0
    user = ledger.record_user("xin chào", timestamp=1.0)
    assert ledger.revision == 1
    ledger.merge_turn_meta(user.index, {"semantic_frame": {"user_act": "greeting"}})
    assert ledger.revision == 1
    ledger.record_assistant("chào bạn", timestamp=2.0)
    assert ledger.revision == 2


def test_invalid_continuation_does_not_invent_old_thread() -> None:
    result = validate_semantic_frame(
        {
            "discourse_relation": "continuation_previous",
            "continuation_key": "",
            "reference": {
                "kind": "recent_assistant_turn",
                "speaker": "user",  # contradictory on purpose
                "operation": "recall",
                "confidence": 0.9,
            },
        }
    )
    assert "continuation_requires_key" in result.violations
    assert result.frame.continuation_key == ""
    assert result.frame.discourse_relation == ""
    assert result.frame.reference.kind is ReferenceKind.NONE


def activation(i: int, layer: str, score: float = 0.8, relevance: float = 0.8) -> MemoryActivation:
    return MemoryActivation(
        record_id=f"m{i}",
        layer=layer,
        text=f"memory {i}",
        score=score,
        relevance=relevance,
        resonance=score,
        surface_policy="silent_bias",
        bias_routes=("attention",),
    )


def test_working_memory_is_bounded_and_current_evidence_excludes_episodic() -> None:
    result = MemoryRetrievalResult(
        activations=tuple(
            [activation(i, "episodic") for i in range(4)]
            + [activation(10 + i, "semantic") for i in range(4)]
            + [activation(20, "repair")]
        )
    )
    gate = WorkingMemoryGate()
    current = MemoryCue(
        query_text="what is on screen now",
        needs_current_evidence=True,
        budget_mode="normal",
    )
    projected = gate.apply(result, cue=current, requested=10)
    assert len(projected.activations) <= 5
    assert all(item.layer != "episodic" for item in projected.activations)
    assert any(note.startswith("working_memory_gate:") for note in projected.debug_notes)

    exact = MemoryCue(query_text="recall exactly", needs_exact_recall=True, budget_mode="high")
    recalled = gate.apply(result, cue=exact, requested=10)
    assert sum(item.layer == "episodic" for item in recalled.activations) <= 4
    assert len(recalled.activations) <= 8


@pytest.mark.asyncio
async def test_real_outcome_emits_prediction_error_and_updates_same_learning_authority() -> None:
    d = Determinism()
    audit = Audit()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        audit=audit,
        clock=d.clock,
        id_factory=d.id,
    )
    frame = await service.prepare_deliberation(observation(d, "learn"))
    decision = await service.finalize_deliberation(frame, commit_for(frame, d))
    draft = service.authorship.all()[-1]
    outcome = WorldOutcome(
        outcome_id="outcome:learn",
        action_id=draft.action_id,
        decision_id=decision.decision_id,
        status=OutcomeStatus.FAILED,
        observed_changes=("no useful effect",),
        unintended_changes=(),
        denial_reason=None,
        evidence_refs=("evidence:runtime",),
        started_at=d.clock(),
        completed_at=d.clock(),
    )
    await service.observe_outcome(outcome)
    prediction_events = [event for event in audit.events if event.get("kind") == "prediction_error"]
    assert prediction_events
    payload = prediction_events[-1]["payload"]
    assert payload["decision_id"] == decision.decision_id
    assert 0.0 <= payload["absolute_error"] <= 1.0


def test_all_decision_worker_lanes_are_provider_stateless(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_RUNTIME_SESSION_ID", "test-runtime")
    messages = [{"role": "system", "content": "bounded decision worker"}]
    for lane, optional in (
        ("neuro_deliberation", True),
        ("neuro_continuation", True),
        ("capability_deliberation_required", False),
        ("capability_deliberation", True),
    ):
        with inference_context(lane, optional=optional):
            assert _web_session_header(messages) == ""


def test_propositional_assistant_act_without_mode_fails_closed() -> None:
    from runtime.semantic_frame import AssistantActKind, PropositionMode, validate_assistant_act

    parsed = validate_assistant_act({
        "kind": "repair",
        "confidence": 0.94,
    })

    assert "assistant_act.proposition_mode_required:repair" in parsed.violations
    assert parsed.act.kind is AssistantActKind.OTHER
    assert parsed.act.proposition_mode is PropositionMode.NONE
    assert parsed.act.confidence <= 0.25


def test_capability_planner_brief_never_exposes_ephemeral_cognitive_ids() -> None:
    from types import SimpleNamespace
    from runtime.ai_orchestrated.executive_cortex import ExecutiveCortex

    candidate = SimpleNamespace(
        coalition_id="coalition:secret-old-id",
        proposal_refs=("proposal:secret-ref",),
        action_kind="speak",
        arguments={
            "capability_goal": "answer the user",
            "capability_route": True,
            "thread_id": "thread:secret",
        },
        expected_pragmatic_value=0.8,
        expected_epistemic_value=0.4,
        uncertainty=0.2,
    )
    frame = SimpleNamespace(coalitions=(candidate,))

    brief = ExecutiveCortex._subject_capability_brief(frame)
    rendered = repr(brief)
    assert "coalition:secret-old-id" not in rendered
    assert "proposal:secret-ref" not in rendered
    assert "thread:secret" not in rendered
    assert brief["arguments"]["capability_goal"] == "answer the user"
    assert brief["arguments"]["capability_route"] is True

def test_v30_router_has_no_legacy_executive_request_alias() -> None:
    # V30 chỉ route tên request theo authority thật. Giữ alias ``executive`` sẽ
    # âm thầm duy trì một compatibility path mà runtime không còn sở hữu.
    from runtime.ai_orchestrated import model_router as module
    source = __import__("inspect").getsource(module.AIRuntimeModelRouter._run_inference)
    assert '"executive" in reason_key' not in source
    assert '"capability_planner" in reason_key' in source

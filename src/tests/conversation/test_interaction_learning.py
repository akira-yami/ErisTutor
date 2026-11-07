from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from runtime.ai_orchestrated.contracts import CognitiveObservation, RuntimeMindState, StateEvent
from runtime.ai_orchestrated.identity import IdentityProfile
from runtime.ai_orchestrated.personality import ConsequenceAssessment
from runtime.ai_orchestrated.state_reducer import StateReducer
from runtime.conversation.agent import RuntimeModelRootAgent
from runtime.conversation.contracts import ConversationEvent, NextConversationMove
from runtime.conversation.interaction_choice import record_delivered_choice, record_user_reaction


CHOICE = {"situation": "User is correcting how the conversation is going",
          "strategy": "acknowledge_and_make_space", "context": "interaction_correction",
          "expected_effect": "Reduce the need to explain the same concern again",
          "listener_action": "",
          "alternative": "Ask what went wrong; might add more explanation burden"}


@pytest.fixture
def setup_reducer():
    reducer = StateReducer()
    host = SimpleNamespace(ai_state_reducer=reducer)
    yield host, reducer
    reducer.close()


def delivered(host, *, did="delivery:one", turn=2, session="s"):
    return record_delivered_choice(host, choice=CHOICE, delivery_id=did,
                                   session_id=session, turn_seq=turn, goal="Respond to feedback",
                                   assistant_text="I hear the correction.")


def reacted(host, *, previous=2, turn=3, session="s", ref="user:3", now=None):
    return record_user_reaction(host, session_id=session, turn_seq=turn, turn_ref=ref,
                                previous_assistant_turn_seq=previous,
                                user_text="That still asks me to explain it again.",
                                now=time.time() if now is None else now)


def observe(reducer, *, evidence=("user:3",), turn=4, did="delivery:one"):
    assessment = ConsequenceAssessment(strategy_use_id=did, context="interaction_correction",
                                       outcome="rejected", reinforcement=-1, confidence=.9,
                                       evidence=evidence)
    observation = CognitiveObservation(turn_id=f"assistant:{turn}", turn_seq=turn,
                                       event_kind="chat", confidence=.9, consequence=assessment)
    reducer.submit(StateEvent.from_observation(observation))
    assert reducer.wait_idle()


def test_root_keeps_one_call_and_does_not_rewrite_say():
    reply = {"kind": "move", "intent": "my choice", "say": "Hừ, tôi có ý kiến khác.",
             "delivery": "dry", "after": "wait_user", "interaction_choice": CHOICE}
    calls = []
    router = SimpleNamespace(executive_client=object(),
        invoke_structured_worker=lambda *_a, **kw: calls.append(kw) or reply)
    decision = RuntimeModelRootAgent(router).decide(
        event=ConversationEvent.user_turn(session_id="s", text="tell me"),
        recent_history=(), note="", runtime_context={})
    assert decision.say == reply["say"]
    assert decision.interaction_choice == CHOICE
    assert len(calls) == 1
    assert NextConversationMove.from_mapping({**reply, "interaction_choice": "broken"}).interaction_choice == {}


def test_opt_out_removes_both_instruction_and_wire_requirement(monkeypatch):
    from runtime.conversation.agent import root_step_schema
    from runtime.conversation.context import RootCapabilityRegistry
    monkeypatch.setenv("ERIS_ROOT_INTERACTION_CHOICES", "0")
    assert "interaction_choice" not in RuntimeModelRootAgent._system_prompt(RootCapabilityRegistry({}))
    move = root_step_schema()["oneOf"][0]
    assert "interaction_choice" not in move["required"]
    assert "interaction_choice" not in move["properties"]
    monkeypatch.setenv("ERIS_ROOT_INTERACTION_CHOICES", "1")
    assert "interaction_choice" in root_step_schema()["oneOf"][0]["required"]


def test_delivery_is_not_its_own_outcome_and_duplicate_does_not_add_use(setup_reducer):
    host, reducer = setup_reducer
    delivered(host)
    delivered(host)
    assert reducer.wait_idle()
    state = reducer.snapshot()
    assert len(state.identity.pending_strategies) == 1
    assert state.identity.strategies[CHOICE["strategy"]].uses == 1
    observe(reducer, evidence=("delivery:one",))
    assert len(reducer.snapshot().identity.pending_strategies) == 1
    assert reducer.snapshot().identity.strategies[CHOICE["strategy"]].utility == 0


@pytest.mark.parametrize("kwargs", [
    {"session": "other"}, {"previous": 99}, {"turn": 2}, {"now": 0},
])
def test_reaction_cannot_attach_to_wrong_scope_turn_or_old_delivery(setup_reducer, kwargs):
    host, reducer = setup_reducer
    delivered(host)
    reacted(host, **kwargs)
    assert reducer.wait_idle()
    assert not reducer.snapshot().identity.pending_strategies[0].reaction_ref


def test_feedback_survives_discarded_observation_and_roundtrip_then_settles_once(setup_reducer):
    host, reducer = setup_reducer
    delivered(host)
    reacted(host)
    reacted(host, ref="user:3:retry")
    assert reducer.wait_idle()
    # No observer result is committed: this is the state left after preemption.
    restored = RuntimeMindState.from_mapping(reducer.snapshot().to_dict())
    pending = restored.identity.pending_strategies[0]
    assert pending.reaction_ref == "user:3"
    assert pending.expected_effect == CHOICE["expected_effect"]
    later = StateReducer(initial_state=restored)
    try:
        observe(later, evidence=("some other user",))
        assert later.snapshot().identity.pending_strategies
        observe(later, turn=6)
        state = later.snapshot()
        assert not state.identity.pending_strategies
        record = state.identity.strategies[CHOICE["strategy"]]
        assert record.failures == 1 and record.utility < 0
        assert record.experiences[-1]["reaction_ref"] == "user:3"
        assert "explain" in record.experiences[-1]["reaction_text"]
        observe(later, turn=8)
        delivered(SimpleNamespace(ai_state_reducer=later))
        assert later.wait_idle()
        record = later.snapshot().identity.strategies[CHOICE["strategy"]]
        assert record.uses == 1 and record.failures == 1
    finally:
        later.close()


def test_disabled_learning_records_no_choice_or_reaction():
    reducer = StateReducer(initial_state=RuntimeMindState(identity_learning_enabled=False))
    try:
        host = SimpleNamespace(ai_state_reducer=reducer)
        delivered(host)
        reacted(host)
        assert reducer.wait_idle()
        assert not reducer.snapshot().identity.pending_strategies
        assert reducer.snapshot().identity == IdentityProfile()
    finally:
        reducer.close()


def test_unclassified_root_move_retains_case_without_global_policy_reward(setup_reducer):
    host, reducer = setup_reducer
    assert record_delivered_choice(host, choice={}, delivery_id="delivery:one", session_id="s",
        turn_seq=2, goal="Ask about the requirement", assistant_text="What did you specify?",
        user_text="I was sharing my frustration.")
    reacted(host)
    assert reducer.wait_idle()
    observe(reducer)
    record = reducer.snapshot().identity.strategies["root_move"]
    assert record.utility == 0 and record.failures == 1
    assert record.experiences[-1]["goal"] == "Ask about the requirement"
    assert record.experiences[-1]["reaction_ref"] == "user:3"
    assert not record.experiences[-1]["expected_effect"]


def test_evidence_reference_prefix_is_normalized_without_substring_matching(setup_reducer):
    host, reducer = setup_reducer
    delivered(host)
    reacted(host)
    assert reducer.wait_idle()
    observe(reducer, evidence=("unrelated user:3 suffix",))
    assert reducer.snapshot().identity.pending_strategies
    observe(reducer, turn=6, evidence=("reaction_ref: user:3",))
    assert not reducer.snapshot().identity.pending_strategies


def test_observer_selects_only_saved_reaction_in_same_conversation(monkeypatch):
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
    router = AIRuntimeModelRouter(SimpleNamespace(social_parser_client=object()))
    monkeypatch.setattr(router, "_should_reflect_personality", lambda *_: False)
    calls = []
    monkeypatch.setattr(router, "_call", lambda *_a, **kw: calls.append(kw) or {
        "user_act": "feedback", "relationship_meaning": "neutral", "confidence": .8})
    pending = {"strategy_use_id": "old:1", "session_id": "s", "turn_seq": 2,
               "reaction_turn_seq": 3, "reaction_ref": "user:3", "reaction_text": "that was wrong"}
    router.observe_completed_turn(turn_id="a:8", turn_seq=8, user_text="new topic",
        assistant_text="a new reply", state_summary={"conversation_session_id": "s",
        "identity": {"pending_strategies": [
            {**pending, "session_id": "other"},
            {**pending, "reaction_ref": "", "reaction_text": ""}, pending]}})
    assert calls[0]["payload"]["pending_strategy_for_evaluation"] == [pending]
    assert calls[0]["payload"]["user_text"] == "new topic"
    monkeypatch.setattr(router, "_call", lambda *_a, **kw: {
        "user_act": "feedback", "relationship_meaning": "neutral", "confidence": .8,
        "consequence": {"strategy_use_id": "wrong-conversation-id", "confidence": 1,
                        "outcome": "success", "reinforcement": 1, "evidence": ["user:3"]}})
    outcome = router.observe_completed_turn(turn_id="a:10", turn_seq=10, user_text="new topic",
        assistant_text="another reply", state_summary={"conversation_session_id": "s",
        "identity": {"pending_strategies": [pending]}})
    assert not outcome.consequence.strategy_use_id

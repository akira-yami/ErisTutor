# -*- coding: utf-8 -*-
"""Observer P0-P2: phân biệt lỗi transport/parse, unknown thật và evidence bền."""
from __future__ import annotations

import json
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm import raw_trace
from runtime.ai_orchestrated.contracts import CognitiveObservation
from runtime.ai_orchestrated.identity import IdentityProfile
from runtime.ai_orchestrated.identity_reducer import IdentityKernelReducer
from runtime.ai_orchestrated.model_router import (
    AIRuntimeModelRouter, OBJECTIVE_OBSERVATION_SCHEMA, ObserverInferenceError,
    _observer_output_budget,
)
from runtime.ai_orchestrated.observer import ObservationTicket, PostTurnObserver


class FakeModel:
    model_name = "deepseek-mock"
    worker_role = "observer"

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"choices": [{"message": {"content": self.response}}]}


class FakeReducer:
    def __init__(self):
        self.events = []

    def snapshot(self):
        return SimpleNamespace(version=0)

    def submit(self, event):
        self.events.append(event)
        return True


def router_for(model):
    router = AIRuntimeModelRouter(SimpleNamespace(social_parser_client=model, social_fallback_client=None))
    router._run_inference = lambda _tier, invoke, **_kwargs: invoke()
    return router


def call(router, model):
    return router._call(
        model, system="Observer test", payload={"user_text": "abc"},
        schema=OBJECTIVE_OBSERVATION_SCHEMA,
        name="eris_cognitive_observation", max_tokens=1800,
    )


def valid(**overrides):
    # Dùng đúng schema của production để fake DeepSeek không vô tình đơn giản
    # hóa bài test (ví dụ thiếu discourse/consequence mà vẫn được PASS).
    def mock_value(definition):
        kind = definition.get("type")
        if kind == "object":
            return {key: mock_value(definition["properties"][key])
                    for key in definition.get("required", ())}
        if kind == "array":
            return []
        if kind == "string":
            return (definition.get("enum") or ["test"])[0]
        if kind == "boolean":
            return False
        if kind == "number":
            return .0
        if kind == "integer":
            return 0
        raise ValueError(f"unknown_schema_kind: {kind}")

    payload = mock_value(OBJECTIVE_OBSERVATION_SCHEMA)
    payload.update({"event_kind": "chat", "user_act": "teasing", "relationship_meaning": "banter",
                    "confidence": 0.81, "ambiguity": 0.19,
                    "evidence": ["User trêu rõ bằng lời"]})
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("body,status", [
    ("", "empty_output"),
    ("not a JSON", "parse_error"),
    ('{"broken":true}', "parse_error"),
    ('{"user_act":"teasing"}', "parse_error"),
    ('{"event_kind":"chat","user_act":"teasing","relationship_meaning":"banter","confidence":10,"ambiguity":0,"evidence":[]}', "parse_error"),
])
def test_invalid_observer_output_never_becomes_neutral_observation(body, status):
    model = FakeModel(body)
    with pytest.raises(ObserverInferenceError) as err:
        call(router_for(model), model)
    assert err.value.status == status


@pytest.mark.parametrize("error,status", [
    (TimeoutError("private prompt content"), "timeout"),
    (ConnectionError("private token content"), "provider_error"),
])
def test_provider_errors_are_classified_without_leaking_prompt(error, status):
    model = FakeModel(error=error)
    with pytest.raises(ObserverInferenceError) as err:
        call(router_for(model), model)
    assert err.value.status == status
    assert "private" not in str(err.value)


def test_explicit_unknown_is_valid_model_result_not_transport_failure():
    model = FakeModel(json.dumps(valid(user_act="unknown", relationship_meaning="neutral", confidence=0.0)))
    router = router_for(model)
    obs = router.observe_completed_turn(turn_id="t1", turn_seq=1,
                                        user_text="Ừm, có lẽ", assistant_text="Cậu nghĩ sao?", state_summary={})
    assert obs.observation_status == "model_unknown"
    assert obs.confidence == 0
    assert model.calls[0]["max_tokens"] == 1800


def test_valid_observer_event_changes_reducer_input():
    model = FakeModel(json.dumps(valid()))
    router = router_for(model)
    obs = router.observe_completed_turn(turn_id="t2", turn_seq=2,
                                        user_text="cậu cứ trêu hoài", assistant_text="Hửm?", state_summary={})
    assert obs.observation_status == "success"
    assert obs.user_act == "teasing"
    assert obs.confidence == pytest.approx(.81)
    reducer = FakeReducer()
    post = PostTurnObserver(router, reducer)
    try:
        ticket = ObservationTicket(ticket_id="ticket", turn_id="t2", turn_seq=2,
                                   user_text="cậu cứ trêu hoài", assistant_text="Hửm?",
                                   event_kind="chat", submitted_at=0.0,
                                   future=Future(), user_meta={}, assistant_meta={})
        post._finalize_success(ticket, obs)
        assert ticket.future.result(timeout=1) == obs
        assert len(reducer.events) == 1
        assert reducer.events[0].payload["user_act"] == "teasing"
        assert ticket.user_meta["cognitive_observation_status"] == "success"
    finally:
        post.shutdown()


def test_unknown_does_not_commit_synthetic_neutral_state():
    post = PostTurnObserver(None, FakeReducer())
    try:
        ticket = ObservationTicket(ticket_id="a", turn_id="t", turn_seq=1,
                                   user_text="x", assistant_text="y", event_kind="chat",
                                   submitted_at=0.0, future=Future(), user_meta={})
        observation = CognitiveObservation(turn_id="t", turn_seq=1, user_act="unknown",
                                           observation_status="model_unknown")
        post._finalize_success(ticket, observation)
        assert ticket.future.result() == observation
        assert post.reducer.events == []
        assert ticket.user_meta["cognitive_observation_status"] == "model_unknown"
    finally:
        post.shutdown()


def test_error_does_not_commit_observation_and_reports_status():
    post = PostTurnObserver(None, FakeReducer())
    try:
        for i, kind in enumerate(("empty_output", "parse_error", "provider_error", "timeout")):
            meta = {}
            ticket = ObservationTicket(ticket_id=f"a{i}", turn_id=f"t{i}", turn_seq=i,
                                       user_text="x", assistant_text="y", event_kind="chat",
                                       submitted_at=0.0, future=Future(), user_meta=meta)
            post._finalize_failure(ticket, ObserverInferenceError(kind))
            with pytest.raises(ObserverInferenceError):
                ticket.future.result()
            assert meta["cognitive_observation_status"] == kind
            assert meta["cognitive_observation_failed"] is True
        assert not post.reducer.events
    finally:
        post.shutdown()


def test_new_output_budget_is_configurable_and_bounded(monkeypatch):
    assert _observer_output_budget() == 1800
    monkeypatch.setenv("ERIS_POST_OBSERVER_MAX_TOKENS", "2800")
    assert _observer_output_budget() == 2800
    monkeypatch.setenv("ERIS_POST_OBSERVER_MAX_TOKENS", "999999")
    assert _observer_output_budget() == 4096


def test_raw_trace_records_safe_diagnostic_without_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE", "1")
    monkeypatch.setenv("ERIS_DEEPSEEK_RAW_TRACE_DIR", str(tmp_path))
    raw_trace.record_observer_diagnostic(turn_id="turn-test", turn_seq=17,
                                         status="parse_error", source="observer",
                                         detail="missing:confidence", max_tokens=1800)
    path = next(tmp_path.glob("events_*.jsonl"))
    row = json.loads(path.read_text(encoding="utf8").strip())
    assert row["event"] == "observer_diagnostic"
    assert row["status"] == "parse_error"
    assert row["turn_seq"] == 17
    assert "prompt" not in row


def test_duplicate_evidence_on_new_turn_cannot_fake_independent_belief():
    reducer = IdentityKernelReducer()
    original = IdentityProfile()
    key = "user.tolerates_disagreement"

    def obs(turn):
        return CognitiveObservation.from_mapping({
            "confidence": .94, "evidence": ["Cùng một lời diễn giải lặp nguyên văn"],
            "identity_signals": [{"dimension": "belief", "key": key, "delta": .9,
                                  "confidence": .95, "importance": .9,
                                  "evidence": ["Một bằng chứng y hệt"]}],
        }, turn_id=f"turn-{turn}", turn_seq=turn)

    first = reducer.reduce_observation(original, obs(10))
    second = reducer.reduce_observation(first, obs(11))
    assert second.beliefs[key] == original.beliefs[key]
    assert second.corroboration[f"belief:{key}:positive"] == 1


def test_real_state_reducer_updates_relation_with_grounded_observation():
    from runtime.ai_orchestrated.state_reducer import StateReducer
    from runtime.ai_orchestrated.contracts import StateEvent

    reducer = StateReducer()
    try:
        prior = reducer.snapshot()
        observation = CognitiveObservation.from_mapping(
            {"event_kind": "chat", "user_act": "positive_feedback",
             "relationship_meaning": "acceptance", "confidence": .95, "ambiguity": .04,
             "relational_delta": .7, "memory_worthy": True,
             "memory_candidate": "Người dùng thích kiểu giải thích có ví dụ.",
             "evidence": ["User nói rõ thích câu trả lời có ví dụ."]},
            turn_id="proof-1", turn_seq=1,
        )
        assert reducer.apply_now(StateEvent.from_observation(observation, base_version=prior.version))
        result = reducer.snapshot()
        assert result.relationship.relational_trust > prior.relationship.relational_trust
        assert len(result.memory_candidates) == 1
        assert result.memory_candidates[0]["evidence"]
    finally:
        reducer.close()


def test_state_reducer_refuses_memory_without_evidence():
    from runtime.ai_orchestrated.state_reducer import StateReducer
    from runtime.ai_orchestrated.contracts import StateEvent

    reducer = StateReducer()
    try:
        observation = CognitiveObservation.from_mapping(
            {"event_kind": "chat", "confidence": .99, "memory_worthy": True,
             "memory_candidate": "Suy đoán user ghét tôi", "evidence": []},
            turn_id="no-proof", turn_seq=1,
        )
        assert reducer.apply_now(StateEvent.from_observation(observation, base_version=0))
        assert reducer.snapshot().memory_candidates == ()
    finally:
        reducer.close()


def test_repeated_memory_candidate_does_not_multiply_observations():
    from runtime.ai_orchestrated.state_reducer import StateReducer
    from runtime.ai_orchestrated.contracts import StateEvent

    reducer = StateReducer()
    try:
        for index in (1, 2):
            observation = CognitiveObservation.from_mapping(
                {"event_kind": "chat", "user_act": "preference", "confidence": .93,
                 "memory_worthy": True,
                 "memory_candidate": "Người dùng thích phản hồi có ví dụ.",
                 "evidence": [f"turn-{index}: user yêu cầu ví dụ"]},
                turn_id=f"same-{index}", turn_seq=index,
            )
            assert reducer.apply_now(StateEvent.from_observation(observation,
                                                                  base_version=reducer.snapshot().version))
        assert len(reducer.snapshot().memory_candidates) == 1
    finally:
        reducer.close()

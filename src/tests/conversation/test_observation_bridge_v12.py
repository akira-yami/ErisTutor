# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from runtime.conversation.observation_bridge import ConversationObservationBridge


class _Logger:
    def debug(self, *_args, **_kwargs):
        pass


def test_observation_bridge_restores_user_write_back_without_semantic_authority(monkeypatch):
    calls: list[str] = []

    class _Trajectory:
        def observe_user_turn(self, *_args, **_kwargs):
            calls.append("trajectory_user")
            return SimpleNamespace(to_dict=lambda: {"topic_id": "t"})

    class _Handoff:
        def observe_user_turn(self, *_args, **_kwargs):
            calls.append("handoff_user")
            return SimpleNamespace(to_dict=lambda: {"revision": 2})

    class _Persistent:
        def observe_user_turn(self, *_args, **_kwargs):
            calls.append("persistent_user")
            return {"ok": True}

    class _Life:
        def observe_user_turn(self, **_kwargs):
            calls.append("life_user")

    class _World:
        def snapshot(self, **_kwargs):
            return SimpleNamespace(conversation=SimpleNamespace(last_user_text="hello"))
        def observe_user_turn(self, *_args, **_kwargs):
            calls.append("world_user_duplicate")

    import runtime.user_addressing_state as addressing
    import runtime.conversation_trajectory as trajectory
    import runtime.conversation_handoff as handoff
    import runtime.persistent_individual as persistent
    import runtime.life_horizon as life
    import runtime.world_state as world

    monkeypatch.setattr(addressing, "observe_user_addressing", lambda *_a, **_k: calls.append("addressing") or True)
    monkeypatch.setattr(trajectory, "ensure_conversation_trajectory_runtime", lambda _host: _Trajectory())
    monkeypatch.setattr(handoff, "ensure_conversation_handoff_runtime", lambda _host: _Handoff())
    monkeypatch.setattr(persistent, "ensure_persistent_individual_runtime", lambda _host: _Persistent())
    monkeypatch.setattr(life, "ensure_life_horizon_manager", lambda _host: _Life())
    monkeypatch.setattr(world, "ensure_world_state_service", lambda _host: _World())

    host = SimpleNamespace(
        logger=_Logger(),
        _ingest_user_turn_memory_hot_path=lambda *_a, **_k: calls.append("memory_user") or True,
    )
    bridge = ConversationObservationBridge(host)
    bridge.observe_user_turn("hello", turn_ref="session_turn:1", turn_index=1, now=1000.0)

    assert {"addressing", "trajectory_user", "handoff_user", "persistent_user", "life_user", "memory_user"}.issubset(calls)
    assert "world_user_duplicate" not in calls


def test_observation_bridge_submits_completed_turn_after_public_commit(monkeypatch):
    calls: list[str] = []
    submitted: list[dict] = []

    class _Trajectory:
        def observe_assistant_commit(self, *_args, **_kwargs):
            calls.append("trajectory_assistant")
            return SimpleNamespace(to_dict=lambda: {"revision": 3})

    class _Handoff:
        def observe_assistant_commit(self, *_args, **_kwargs):
            calls.append("handoff_assistant")
            return SimpleNamespace(to_dict=lambda: {"revision": 3})

    class _Persistent:
        def observe_assistant_commit(self, *_args, **_kwargs):
            calls.append("persistent_assistant")
            return {"ok": True}

    class _Life:
        def observe_assistant_commit(self, **_kwargs):
            calls.append("life_assistant")

    class _World:
        def observe_assistant_commit(self, *_args, **_kwargs):
            calls.append("world_assistant")

    class _Observer:
        def submit(self, **kwargs):
            submitted.append(dict(kwargs))
            return SimpleNamespace(ticket_id="observe-1")

    import runtime.conversation_trajectory as trajectory
    import runtime.conversation_handoff as handoff
    import runtime.persistent_individual as persistent
    import runtime.life_horizon as life
    import runtime.world_state as world

    monkeypatch.setattr(trajectory, "ensure_conversation_trajectory_runtime", lambda _host: _Trajectory())
    monkeypatch.setattr(handoff, "ensure_conversation_handoff_runtime", lambda _host: _Handoff())
    monkeypatch.setattr(persistent, "ensure_persistent_individual_runtime", lambda _host: _Persistent())
    monkeypatch.setattr(life, "ensure_life_horizon_manager", lambda _host: _Life())
    monkeypatch.setattr(world, "ensure_world_state_service", lambda _host: _World())

    state = SimpleNamespace(to_dict=lambda: {"version": 9})
    host = SimpleNamespace(
        logger=_Logger(),
        ai_post_turn_observer=_Observer(),
        ai_state_reducer=SimpleNamespace(snapshot=lambda: state),
    )
    bridge = ConversationObservationBridge(host)
    result = bridge.observe_assistant_commit(
        "answer",
        turn_ref="delivery:1",
        turn_index=2,
        speech_act="answer",
        event_kind="user_turn",
        user_text="question",
        recent_history=({"role": "user", "content": "question"},),
        now=1001.0,
    )

    assert {"world_assistant", "trajectory_assistant", "handoff_assistant", "persistent_assistant", "life_assistant"}.issubset(calls)
    assert result["post_observer_ticket"] == "observe-1"
    assert submitted[0]["user_text"] == "question"
    assert submitted[0]["assistant_text"] == "answer"
    assert submitted[0]["state_summary"] == {"version": 9}

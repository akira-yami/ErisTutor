from __future__ import annotations

from types import SimpleNamespace

from runtime.ai_orchestrated.contracts import RuntimeMindState
from runtime.ai_orchestrated.panel_adapter import build_ai_panel_payload
from runtime.user_control_runtime import UserControlRuntimeMixin
from transport.udp import _compact_private_mind_for_udp_overflow, _slim_private_mind_core


class _Transport:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


class _Logger:
    def warning(self, *_args, **_kwargs) -> None:
        pass

    def debug(self, *_args, **_kwargs) -> None:
        pass


class _Reducer:
    def __init__(self) -> None:
        self.events = []
        self.state = RuntimeMindState(version=7, last_applied_turn_seq=3)

    def snapshot(self) -> RuntimeMindState:
        return self.state

    def apply_now(self, event) -> None:
        self.events.append(event)


def test_identity_panel_contract_uses_typed_controls_and_inner_state_schema() -> None:
    payload = build_ai_panel_payload(RuntimeMindState())
    identity = payload["identity_runtime"]
    controls = identity["controls"]
    digest = payload["private_mind_digest"]

    assert controls["schema"] == "eris.identity_control.v1"
    assert controls["transport"] == "identity_control"
    assert controls["actions"]["set_learning"]["owner"] == "identity_reducer"
    assert controls["actions"]["reset_discovery"]["owner"] == "discovery_runtime"
    assert digest["schema"] == "eris.inner_state.panel.v1"
    assert digest["generated_at"] > 0
    assert digest["authority"]["memory_echo"] == "not_provided_by_this_contract"
    assert "speech_drive" not in identity


def test_private_mind_compaction_keeps_schema_freshness_and_authority() -> None:
    digest = build_ai_panel_payload(RuntimeMindState())["private_mind_digest"]

    normal = _compact_private_mind_for_udp_overflow(digest)
    slim = _slim_private_mind_core(digest)

    for compacted in (normal, slim):
        assert compacted["schema"] == "eris.inner_state.panel.v1"
        assert compacted["generated_at"] > 0
        assert compacted["authority"]["open_loops"] == "runtime_mind_state.open_loops"


def test_identity_control_set_learning_emits_typed_event_and_ack() -> None:
    reducer = _Reducer()
    owner = SimpleNamespace(
        ai_state_reducer=reducer,
        ai_identity_consolidator=None,
        ai_discovery_runtime=None,
        transport=_Transport(),
        logger=_Logger(),
    )

    UserControlRuntimeMixin._handle_identity_control(
        owner,
        {"type": "identity_control", "request_id": "req-1", "action": "set_learning", "enabled": False},
    )

    assert len(reducer.events) == 1
    event = reducer.events[0]
    assert event.kind == "admin_control"
    assert event.payload == {"action": "set_learning", "enabled": False}
    assert event.source == "identity_panel"
    ack = owner.transport.messages[-1]["identity_control_ack"]
    assert ack["request_id"] == "req-1"
    assert ack["action"] == "set_learning"
    assert ack["ok"] is True
    assert ack["data"] == {"enabled": False}


def test_identity_control_rejects_unknown_action_with_ack() -> None:
    owner = SimpleNamespace(
        ai_state_reducer=None,
        ai_identity_consolidator=None,
        ai_discovery_runtime=None,
        transport=_Transport(),
        logger=_Logger(),
    )

    UserControlRuntimeMixin._handle_identity_control(owner, {"action": "invent_new_soul"})

    ack = owner.transport.messages[-1]["identity_control_ack"]
    assert ack["ok"] is False
    assert "không hợp lệ" in ack["result"]

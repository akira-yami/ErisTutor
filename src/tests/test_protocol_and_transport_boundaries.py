from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.incoming_dispatcher import IncomingMessageDispatcher
from runtime.policy_settings import ProtocolSettings
from runtime.subsystem_health import HealthStatus, SubsystemHealthRegistry
from runtime.transport_boundary import RuntimeTransportBoundary


def test_udp_payload_sanitizer_removes_godot_unsafe_emoji() -> None:
    from transport.udp import _sanitize_udp_payload

    payload = _sanitize_udp_payload(
        {"text": "nghe đây 😏", "nested": {"speech_text": "聞いてる 😌"}}
    )

    assert payload == {"text": "nghe đây ", "nested": {"speech_text": "聞いてる "}}
    assert all(ord(ch) <= 0xFFFF for ch in payload["text"] + payload["nested"]["speech_text"])


class _Transport:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict] = []

    def send_json(self, payload: dict) -> None:
        if self.fail:
            raise OSError("udp unavailable")
        self.sent.append(dict(payload))


class _Owner:
    def __init__(self) -> None:
        self.logger = SimpleNamespace(debug=lambda *args: None)
        self.subsystem_health_registry = SubsystemHealthRegistry()
        self._maintenance_in_progress = False
        self.calls: list[tuple[str, object]] = []
        self.sent: list[tuple[str, dict]] = []

    def _send_runtime_json(self, payload: dict, *, channel: str = "control", required: bool = False) -> bool:
        self.sent.append((channel, dict(payload)))
        return True

    def _capture_speech_interrupt_for_user(self, text: str, *, timestamp: float) -> None:
        self.calls.append(("interrupt", (text, timestamp)))

    def _supersede_tts_for_new_user_turn(self, reason: str) -> None:
        self.calls.append(("supersede", reason))

    def _run_chat_cycle(self, text: str, *, user_timestamp: float) -> None:
        self.calls.append(("chat", (text, user_timestamp)))

    def _handle_force_speak_request(self) -> None:
        self.calls.append(("force", None))

    def _record(self, name: str, msg=None) -> None:
        self.calls.append((name, msg))

    _handle_panel_state_ack = lambda self, msg: self._record("panel", msg)
    _handle_phase7_validation_control = lambda self, msg: self._record("phase7", msg)
    _enter_chat_cycle = lambda self: self._record("chat_start")
    _handle_anchor_control = lambda self, msg: self._record("anchor", msg)
    _handle_depth_control = lambda self, msg: self._record("depth", msg)
    _handle_settings_update = lambda self, msg: self._record("settings_update", msg)
    _handle_settings_health = lambda self: self._record("settings_health")
    _handle_settings_connection_test = lambda self, msg: self._record("settings_test", msg)
    _handle_maintenance_catalog = lambda self, msg=None: self._record("maintenance_catalog", msg)
    _handle_maintenance_request = lambda self, msg: self._record("maintenance", msg)
    _handle_debug_command = lambda self, msg: self._record("debug", msg)
    _end_chat_cycle = lambda self: self._record("chat_end")
    _handle_stage_inbound_event = lambda self, msg: self._record("stage", msg)


def test_dispatcher_routes_chat_and_repairs_invalid_timestamp() -> None:
    owner = _Owner()
    dispatcher = IncomingMessageDispatcher(
        owner,
        settings=ProtocolSettings(),
        clock=lambda: 123.5,
    )
    result = dispatcher.dispatch({"type": "chat", "message": "xin chào", "timestamp": "bad"})
    assert result.handled is True
    assert ("interrupt", ("xin chào", 123.5)) in owner.calls
    assert ("chat", ("xin chào", 123.5)) in owner.calls
    assert dispatcher.snapshot()["handled"] == 1


def test_dispatcher_rejects_invalid_envelope_and_long_type() -> None:
    owner = _Owner()
    dispatcher = IncomingMessageDispatcher(
        owner,
        settings=ProtocolSettings(max_message_type_chars=16),
    )
    assert dispatcher.dispatch(["not", "a", "mapping"]).reason == "invalid_message_envelope"
    assert dispatcher.dispatch({"type": "x" * 30}).reason == "message_type_too_long"
    health = owner.subsystem_health_registry.get("runtime.protocol.inbound")
    assert health.status == HealthStatus.DEGRADED
    assert health.total_failures == 2


def test_dispatcher_blocks_chat_during_maintenance() -> None:
    owner = _Owner()
    owner._maintenance_in_progress = True
    result = IncomingMessageDispatcher(owner).dispatch({"type": "chat", "message": "hello"})
    assert result.reason == "maintenance_busy"
    assert owner.sent[0][0] == "maintenance"
    assert owner.sent[0][1]["type"] == "maintenance_busy"
    assert not any(name == "chat" for name, _value in owner.calls)


def test_dispatcher_records_handler_failures_and_re_raises() -> None:
    owner = _Owner()

    def explode(_msg):
        raise RuntimeError("broken handler")

    owner._handle_debug_command = explode
    dispatcher = IncomingMessageDispatcher(owner)
    with pytest.raises(RuntimeError, match="broken handler"):
        dispatcher.dispatch({"type": "debug_command"})
    health = owner.subsystem_health_registry.get("runtime.protocol.inbound")
    assert health.status == HealthStatus.DEGRADED
    assert health.last_error_type == "RuntimeError"


def test_transport_boundary_degrades_then_recovers() -> None:
    transport = _Transport(fail=True)
    health = SubsystemHealthRegistry()
    boundary = RuntimeTransportBoundary(transport, health_registry=health, success_sample_every=1)

    assert boundary.send_json({"type": "ack"}, channel="settings") is False
    assert health.get("runtime.transport.settings").status == HealthStatus.DEGRADED

    transport.fail = False
    assert boundary.send_json({"type": "ack"}, channel="settings") is True
    assert health.get("runtime.transport.settings").status == HealthStatus.HEALTHY
    assert boundary.snapshot() == {"sent": 1, "failed": 1, "last_message_type": "ack"}

from __future__ import annotations

from pathlib import Path

from runtime.capability_projection import CapabilityProjectionService
from runtime.incoming_protocol_runtime import IncomingProtocolRuntimeMixin


class _Mentor:
    def snapshot(self):
        return {
            "model": {"skills": {}, "recent_signals": [], "overreliance_risk": 0.0},
            "tool_literacy": {"tools": {}},
            "mentor_calibration": {"metrics": {}},
        }


class _Owner(IncomingProtocolRuntimeMixin):
    def __init__(self, tmp_path: Path) -> None:
        self.engineering_mentor_service = _Mentor()
        self.capability_projection_service = CapabilityProjectionService(history_path=tmp_path / "history.jsonl")
        self.sent = []

    def _send_runtime_json(self, payload: dict, *, channel: str = "control", required: bool = False) -> bool:
        self.sent.append((channel, payload))
        return True


def test_capability_panel_request_is_read_only_snapshot(tmp_path: Path) -> None:
    owner = _Owner(tmp_path)
    owner._handle_capability_panel_request({"request_id": "req-1", "action": "snapshot"})
    assert len(owner.sent) == 1
    channel, packet = owner.sent[0]
    assert channel == "capability_panel"
    assert packet["type"] == "capability_panel_ack"
    assert packet["request_id"] == "req-1"
    assert packet["status"] == "ok"
    assert packet["capability_panel"]["schema"] == "eris.capability_panel.v1"
    # Contract không có command/mutation field.
    assert "command" not in packet["capability_panel"]
    assert "set_skill" not in packet["capability_panel"]


def test_unavailable_mentor_returns_safe_empty_payload(tmp_path: Path) -> None:
    owner = _Owner(tmp_path)
    owner.engineering_mentor_service = None
    owner._handle_capability_panel_request({"request_id": "req-2"})
    packet = owner.sent[0][1]
    assert packet["status"] == "unavailable"
    assert packet["capability_panel"] == {}

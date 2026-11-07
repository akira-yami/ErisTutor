# -*- coding: utf-8 -*-
"""Transport sinks cho AgentEventBus.

AgentEventBus chỉ biết publish envelope có kiểu. UDP cũ được giữ thành một sink
compatibility để backend không còn bị đóng đinh vào Godot/port 4242. WebSocket
service sẽ subscribe event bus riêng và không cần chạm vào sink UDP này.
"""
from __future__ import annotations

import json
import socket
from typing import Any, Protocol


class AgentEventSink(Protocol):
    """Giao diện tối thiểu của một transport sink."""

    def publish(self, envelope: dict[str, Any]) -> bool:
        ...


class LegacyUdpAgentEventSink:
    """Adapter duy trì protocol Agent Console UDP V1 trong giai đoạn migrate."""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 4242, logger: Any = None) -> None:
        self.target = (str(host or "127.0.0.1"), int(port))
        self.logger = logger

    def publish(self, envelope: dict[str, Any]) -> bool:
        kind = str(envelope.get("kind", "") or "")
        if kind == "event":
            packet = {
                "type": "agent_event",
                "parse_mode": "agent_event",
                "should_speak": False,
                "agent": {
                    "version": "agent_console.v1",
                    "task": dict(envelope.get("task") or {}),
                    "event": dict(envelope.get("event") or {}),
                },
            }
            return self.send_packet(packet)
        if kind == "snapshot":
            packet = {
                "type": "agent_console_ack",
                "parse_mode": "agent_console_ack",
                "should_speak": False,
                "request_id": str(envelope.get("request_id", "") or ""),
                "status": "ok",
                "message": "Agent Console đã đồng bộ.",
                "agent_console": dict(envelope.get("snapshot") or {}),
            }
            return self.send_packet(packet)
        return False

    def send_packet(self, payload: dict[str, Any]) -> bool:
        try:
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(raw) > 65507:
                raise ValueError(f"agent event payload exceeds UDP datagram limit: {len(raw)}")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(raw, self.target)
            return True
        except (OSError, TypeError, ValueError) as exc:
            log = getattr(self.logger, "debug", None)
            if callable(log):
                log("[agent-console] legacy UDP sink failed: %s", exc)
            return False


__all__ = ["AgentEventSink", "LegacyUdpAgentEventSink"]

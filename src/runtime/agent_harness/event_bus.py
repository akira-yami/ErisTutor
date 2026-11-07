# -*- coding: utf-8 -*-
"""Typed event bus cho Unified Agent Harness.

Event được persist trước rồi mới publish ra các sink. Bus không phụ thuộc WebSocket,
VS Code hay Godot; UDP 4242 chỉ còn là một compatibility sink có thể tháo ra.
"""
from __future__ import annotations

from typing import Any, Iterable

from .event_sinks import AgentEventSink, LegacyUdpAgentEventSink
from .task_store import AgentTaskStore


class AgentEventBus:
    def __init__(
        self,
        store: AgentTaskStore | None = None,
        *,
        sinks: Iterable[AgentEventSink] | None = None,
        legacy_udp: bool = True,
        host: str = "127.0.0.1",
        port: int = 4242,
        logger: Any = None,
    ) -> None:
        self.store = store or AgentTaskStore()
        self.logger = logger
        self._listeners: list[Any] = []
        self._sinks: list[AgentEventSink] = list(sinks or [])
        self._legacy_udp_sink: LegacyUdpAgentEventSink | None = None
        if legacy_udp:
            self._legacy_udp_sink = LegacyUdpAgentEventSink(host=host, port=port, logger=logger)
            self._sinks.append(self._legacy_udp_sink)

    def add_sink(self, sink: AgentEventSink) -> None:
        if sink not in self._sinks:
            self._sinks.append(sink)

    def remove_sink(self, sink: AgentEventSink) -> None:
        try:
            self._sinks.remove(sink)
        except ValueError:
            pass

    def add_listener(self, callback: Any) -> None:
        if callable(callback) and callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Any) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def emit(
        self,
        task_id: str,
        event_type: str,
        *,
        actor: str = "system",
        summary: str = "",
        detail: str = "",
        step: int | None = None,
        importance: float = 0.5,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        event = self.store.append_event(
            task_id,
            event_type,
            actor=actor,
            summary=summary,
            detail=detail,
            step=step,
            importance=importance,
            data=data,
        )
        if event is None:
            return None
        task = self.store.get_task(task_id, event_limit=0) or {"task_id": task_id}
        envelope = {"kind": "event", "task": task, "event": event}
        self._publish(envelope)

        # Listener nội bộ nhận event gọn, không nhận transport packet.
        listener_event = dict(event)
        listener_event["task_id"] = task_id
        listener_event["thread_id"] = str(task.get("thread_id", "") or "")
        listener_event["session_id"] = str(task.get("session_id", "") or "")
        for callback in tuple(self._listeners):
            try:
                callback(dict(listener_event))
            except Exception as exc:
                log = getattr(self.logger, "debug", None)
                if callable(log):
                    log("[agent-events] listener failed: %s", exc)
        return event

    def send_snapshot(self, *, selected_task_id: str = "", request_id: str = "") -> None:
        self._publish({
            "kind": "snapshot",
            "request_id": request_id,
            "snapshot": self.store.snapshot(selected_task_id=selected_task_id),
        })

    def _publish(self, envelope: dict[str, Any]) -> bool:
        delivered = False
        for sink in tuple(self._sinks):
            try:
                delivered = bool(sink.publish(dict(envelope))) or delivered
            except Exception as exc:
                log = getattr(self.logger, "debug", None)
                if callable(log):
                    log("[agent-events] sink failed: %s", exc)
        return delivered

    def _send(self, payload: dict[str, Any]) -> bool:
        """Compatibility helper cho test/code cũ còn monkeypatch ``_send``.

        Logic mới không dùng hàm này để phát event; transport đã nằm trong sink.
        """
        if self._legacy_udp_sink is None:
            return False
        return self._legacy_udp_sink.send_packet(payload)


__all__ = ["AgentEventBus"]

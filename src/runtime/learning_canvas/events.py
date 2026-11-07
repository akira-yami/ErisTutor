"""Canvas evidence ingress. Enqueue only; Root retains every speech decision."""
from __future__ import annotations

from collections import deque
import threading
import uuid
from contextlib import nullcontext


def session_id(host):
    return str(getattr(host, "conversation_id", "") or getattr(host, "thread_id", "") or "foreground:primary")[:180]


def capture_canvas_scope(host):
    kernel = getattr(host, "conversation_kernel", None)
    method = getattr(kernel, "canvas_scope", None)
    return method(session_id(host)) if callable(method) else {}


def connect_canvas_host(host):
    canvas = host.learning_canvas_service
    canvas.on_event = lambda payload: enqueue_canvas_event(host, payload)
    canvas.on_state = lambda packet: host._send_runtime_json(packet, channel="learning_canvas")

    def guard():
        method = getattr(getattr(host, "conversation_kernel", None), "focus_change_guard", None)
        return method(session_id(host)) if callable(method) else nullcontext()
    canvas.navigation_guard = guard


def scope_is_current(host, scope):
    if not scope:
        return True
    canvas = getattr(host, "learning_canvas_service", None)
    if canvas is None:
        return False
    focus = canvas.focus.snapshot()
    return focus["node_id"] == scope.get("node_id") and focus["focus_revision"] == scope.get("focus_revision")


def allow_canvas_result(host, kind, metadata, *, event_id=""):
    scope = metadata.get("canvas_scope", {})
    if scope_is_current(host, scope):
        return True
    canvas = getattr(host, "learning_canvas_service", None)
    if canvas is not None and (kind in {"work_result", "research_result"} or metadata.get("canvas_action") == "assessment"):
        canvas.defer_result(kind, metadata, scope, event_id=event_id)
    return False


def enqueue_canvas_event(host, payload: dict) -> None:
    lock = getattr(host, "_canvas_event_lock", None)
    if lock is None:
        lock = host._canvas_event_lock = threading.RLock()
    with lock:
        scope = {"node_id": payload.get("node_id"), "focus_revision": payload.get("focus_revision")}
        canvas = getattr(host, "learning_canvas_service", None)
        if scope["focus_revision"] is None and canvas is not None:
            scope["focus_revision"] = canvas.focus.snapshot()["focus_revision"]
        pending = getattr(host, "_pending_conversation_capability_events", None)
        if pending is None:
            pending = host._pending_conversation_capability_events = deque(maxlen=128)
        # Navigation has no obligation to explain every intermediate click.
        if payload.get("action") in {"navigate", "graph_select"}:
            # Nhiều click liên tiếp chỉ để lại hướng khám phá mới nhất.
            for item in tuple(pending):
                if item.get("metadata", {}).get("canvas_action") == payload.get("action"):
                    try:
                        pending.remove(item)
                    except ValueError:
                        pass
        pending.append({"event_id": payload.get("event_id") or f"canvas:{uuid.uuid4().hex}", "kind": "tutor_result",
                        "metadata": {"origin": "user_ui", "canvas_action": payload["action"],
                                     "canvas_scope": scope,
                                     "canvas_capability": payload}})
    wake = getattr(host, "_wake_autonomous_loop", None)
    if callable(wake):
        wake("learning_canvas:" + str(payload["action"]))

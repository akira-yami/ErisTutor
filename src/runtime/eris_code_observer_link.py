# -*- coding: utf-8 -*-
"""Eris Link: context outbound + coding telemetry inbound trên loopback.

LƯU Ý SAU COMPANION CONSOLIDATION:
Đây là legacy/rollback transport. Coordinator không start link này trong normal runtime.
Eris Code phải chạy độc lập và AdaptiveCompanion không nhận hidden telemetry từ đây.
Không dùng module này làm dependency cho chức năng companion mới.

V79 giữ boundary rất cứng:
- GET /v1/context: Desktop góp environment/project memory cho Eris Code.
- POST /v1/coding/observations: Eris Code gửi observation GitPilot đã xảy ra.
- GET /v1/coding/journal, /v1/coding/flow: đọc journal/snapshot để debug UI sau này.
- GET /v1/companion/state: V90 projection workspace/user/agent hợp nhất, read-only.
- GET /v1/mentor/state: đọc projection mentor V80+ để audit cách Eris đang dạy.
- GET /v1/mentor/profile: V85 projection gọn, user-visible/evidence-backed.

Không có endpoint task-control, approval, terminal hoặc workspace mutation. Vì vậy
Eris Desktop có thể hiểu coding flow nhưng không trở thành coding brain thứ hai.
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


class ErisCodeObserverLink:
    def __init__(
        self,
        context_provider: Callable[[str], dict[str, Any]],
        *,
        observation_sink: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        journal_provider: Callable[[str, str, int], list[dict[str, Any]]] | None = None,
        flow_provider: Callable[[str], dict[str, Any]] | None = None,
        companion_provider: Callable[[str], dict[str, Any]] | None = None,
        mentor_provider: Callable[[], dict[str, Any]] | None = None,
        host: str = "127.0.0.1",
        port: int = 8767,
        logger: Any = None,
    ) -> None:
        self.context_provider = context_provider
        self.observation_sink = observation_sink
        self.journal_provider = journal_provider
        self.flow_provider = flow_provider
        self.companion_provider = companion_provider
        self.mentor_provider = mentor_provider
        self.host = host
        self.port = int(port)
        self.logger = logger
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._server is not None:
            return True
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ErisLink/2"

            def log_message(self, fmt: str, *args: Any) -> None:
                if owner.logger is not None:
                    debug = getattr(owner.logger, "debug", None)
                    if callable(debug):
                        debug("[eris-link] " + fmt, *args)

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if parsed.path == "/health":
                    self._send(200, {
                        "ok": True,
                        "protocol": "eris.link.v2",
                        "coding_observation": owner.observation_sink is not None,
                        "engineering_mentor": owner.mentor_provider is not None,
                        "companion_observation": owner.companion_provider is not None,
                    })
                    return
                if parsed.path == "/v1/context":
                    workspace = str(query.get("workspace", [""])[0] or "").strip()
                    try:
                        self._send(200, owner.context_provider(workspace))
                    except Exception as exc:  # fail closed: no traceback/secret payload
                        self._send(500, {"protocol": "eris.link.v2", "error": type(exc).__name__})
                    return
                if parsed.path == "/v1/coding/journal":
                    if owner.journal_provider is None:
                        self._send(404, {"error": "coding_journal_unavailable"})
                        return
                    task_id = str(query.get("task_id", [""])[0] or "").strip()
                    workspace = str(query.get("workspace", [""])[0] or "").strip()
                    try:
                        limit = max(1, min(int(query.get("limit", ["120"])[0]), 500))
                    except Exception:
                        limit = 120
                    self._send(200, {
                        "schema": "eris.coding.journal.v1",
                        "events": owner.journal_provider(task_id, workspace, limit),
                    })
                    return
                if parsed.path == "/v1/coding/flow":
                    if owner.flow_provider is None:
                        self._send(404, {"error": "coding_flow_unavailable"})
                        return
                    task_id = str(query.get("task_id", [""])[0] or "").strip()
                    if not task_id:
                        self._send(400, {"error": "task_id_required"})
                        return
                    self._send(200, owner.flow_provider(task_id))
                    return
                if parsed.path == "/v1/companion/state":
                    if owner.companion_provider is None:
                        self._send(404, {"error": "companion_observation_unavailable"})
                        return
                    workspace = str(query.get("workspace", [""])[0] or "").strip()
                    self._send(200, owner.companion_provider(workspace))
                    return
                if parsed.path == "/v1/mentor/state":
                    # Read-only diagnostics: UI có thể xem Eris đang dựa vào evidence
                    # nào để chọn cách dạy. Không có endpoint set-level/force-mode.
                    if owner.mentor_provider is None:
                        self._send(404, {"error": "engineering_mentor_unavailable"})
                        return
                    self._send(200, owner.mentor_provider())
                    return
                if parsed.path == "/v1/mentor/profile":
                    # V85 vẫn read-only trên loopback. User correction/reset đi qua
                    # trusted in-process UI/chat layer, không mở unauthenticated write API.
                    if owner.mentor_provider is None:
                        self._send(404, {"error": "engineering_mentor_unavailable"})
                        return
                    state = owner.mentor_provider()
                    profile = dict(state.get("mentor_profile") or {}) if isinstance(state, dict) else {}
                    if not profile:
                        self._send(404, {"error": "mentor_profile_unavailable"})
                        return
                    self._send(200, profile)
                    return
                self._send(404, {"error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/v1/coding/observations":
                    # Boundary cứng: không mở generic RPC. Coding command đi thẳng
                    # user/VS Code -> GitPilot, không qua Desktop.
                    self._send(405, {"error": "observation_only"})
                    return
                if owner.observation_sink is None:
                    self._send(404, {"error": "coding_observer_unavailable"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                except Exception:
                    length = 0
                if length <= 0 or length > 64 * 1024:
                    self._send(413, {"error": "invalid_payload_size"})
                    return
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("object_required")
                    result = owner.observation_sink(payload)
                    self._send(202, {"protocol": "eris.link.v2", **dict(result or {})})
                except ValueError as exc:
                    self._send(400, {"error": str(exc)[:120] or "invalid_observation"})
                except Exception as exc:
                    self._send(500, {"protocol": "eris.link.v2", "error": type(exc).__name__})

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        try:
            self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            if self.logger is not None:
                warn = getattr(self.logger, "warning", None)
                if callable(warn):
                    warn("[eris-link] observer bridge unavailable: %s", exc)
            self._server = None
            return False
        self._thread = threading.Thread(target=self._server.serve_forever, name="eris-code-observer-link", daemon=True)
        self._thread.start()
        if self.logger is not None:
            info = getattr(self.logger, "info", None)
            if callable(info):
                info("[eris-link] v2 context + coding telemetry listening http://%s:%s", self.host, self.port)
        return True

    def stop(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            try:
                server.shutdown()
            finally:
                server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)


def build_observer_context(host: Any, workspace: str) -> dict[str, Any]:
    """Build compact evidence packet; no coding instruction or secret config."""
    now = time.time()
    workspace_path = Path(workspace).expanduser() if workspace else None
    project = workspace_path.name if workspace_path else ""

    environment: dict[str, Any] = {}
    try:
        from runtime.environment_awareness import ensure_environment_awareness_hub
        environment = ensure_environment_awareness_hub(host).snapshot(now=now).to_dict(now=now)
    except Exception:
        environment = {}

    memory_items: list[str] = []
    if project:
        recall = getattr(getattr(host, "memory_core", None), "recall", None)
        if callable(recall):
            try:
                packet = recall(f"project {project} coding conventions recent decisions")
                for name in ("procedural", "semantic", "episodic", "repair"):
                    for item in tuple(getattr(packet, name, ()) or ()):
                        text = " ".join(str(item or "").split())
                        if text and text not in memory_items:
                            memory_items.append(text[:360])
                        if len(memory_items) >= 12:
                            break
                    if len(memory_items) >= 12:
                        break
            except Exception:
                pass


    return {
        "protocol": "eris.link.v2",
        "role": "observer",
        "project": project,
        "workspace": str(workspace_path) if workspace_path else "",
        "updated_at": now,
        "environment": environment,
        "project_memory": memory_items,
        "authority": {
            "coding_owner": False,
            "task_control": False,
            "tool_approval": False,
            "workspace_mutation": False,
            "telemetry_ingest_only": True,
        },
    }


def observer_link_enabled() -> bool:
    # Opt-in để localhost process khác không tự nhiên đọc project memory/giả telemetry.
    return str(os.environ.get("ERIS_CODE_OBSERVER_LINK_ENABLED", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["ErisCodeObserverLink", "build_observer_context", "observer_link_enabled"]

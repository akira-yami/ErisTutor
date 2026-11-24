# -*- coding: utf-8 -*-
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from runtime.java_backend_client import (
    JavaBackendClient,
    JavaBackendConfig,
    JavaLearningMirrorProxy,
    RemoteJavaJudgeWorkspace,
)
from runtime.learning_core.contracts import KnowledgePoint, LearningPath
from runtime.learning_core.records import AssessmentAttempt
from runtime.learning_core.service import LearningService
from runtime.learning_core.store import LearningStore


def _start_stub_server():
    received: list[tuple[str, str, dict, dict]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/actuator/health":
                self._send(200, {"status": "UP"})
            elif self.path == "/api/v1/judge/java/readiness":
                self._send(200, {"available": True, "reason": "ready", "image": "eclipse-temurin:21"})
            else:
                self._send(404, {})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            received.append(("POST", self.path, dict(self.headers), body))
            if self.path.startswith("/api/v1/judge/java/submissions"):
                self._send(
                    200,
                    {
                        "status": "completed",
                        "result": "correct",
                        "tests": [{"id": "delegates", "description": "ok", "passed": True}],
                        "reason": "graded",
                        "diagnostic": "",
                        "durationMs": 42,
                        "exitCode": 0,
                    },
                )
            elif self.path in ("/api/v1/learning/paths", "/api/v1/learning/attempts"):
                self._send(200, {"status": "recorded"})
            else:
                self._send(404, {})

        def _send(self, code: int, payload: dict):
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, received


class _FakeFallbackWorkspace:
    def __init__(self):
        self.called = False

    def available(self):
        return True

    def readiness(self):
        return {"available": False, "reason": "local_fallback"}

    def execute(self, code, exercise, *, timeout=15.0):
        self.called = True
        return {"status": "completed", "result": "incorrect", "reason": "fallback_used", "tests": []}


def test_remote_java_judge_delegates_and_falls_back():
    server, received = _start_stub_server()
    try:
        port = server.server_address[1]
        cfg = JavaBackendConfig(
            base_url=f"http://127.0.0.1:{port}",
            desktop_key="secret-desktop-key",
            learning_mode="mirror",
            judge_enabled=True,
        )
        fallback = _FakeFallbackWorkspace()
        remote = RemoteJavaJudgeWorkspace(fallback, client=JavaBackendClient(cfg))

        assert remote.available() is True
        assert remote.readiness()["available"] is True
        res = remote.execute(
            "class OrderService {}",
            {"id": "di.constructor.code", "class_name": "OrderService", "harness": "", "tests": []},
        )
        assert res["status"] == "completed"
        assert res["result"] == "correct"
        assert fallback.called is False
        assert any(hdrs.get("X-Eris-Desktop-Key") == "secret-desktop-key" for _, _, hdrs, _ in received)
    finally:
        server.shutdown()

    # Now verify seamless fallback when Java backend is unreachable
    offline_cfg = JavaBackendConfig(base_url="http://127.0.0.1:1", desktop_key="k", judge_enabled=True, timeout_seconds=0.5)
    fallback = _FakeFallbackWorkspace()
    remote_offline = RemoteJavaJudgeWorkspace(fallback, client=JavaBackendClient(offline_cfg))
    out = remote_offline.execute("class OrderService {}", {"id": "x"})
    assert fallback.called is True
    assert out["reason"] == "fallback_used"


def test_learning_mirror_proxy_mirrors_to_java(tmp_path):
    server, received = _start_stub_server()
    try:
        port = server.server_address[1]
        cfg = JavaBackendConfig(base_url=f"http://127.0.0.1:{port}", desktop_key="k", learning_mode="mirror")
        local = LearningService(LearningStore(tmp_path / "learning.sqlite3"))
        proxy = JavaLearningMirrorProxy(local, client=JavaBackendClient(cfg))

        path = LearningPath(
            path_id="javadi:v1:path",
            title="Java DI",
            goal="Learn DI",
            knowledge_points=(KnowledgePoint(kp_id="javadi:v1:dependency", title="Dependency", kp_type="concept"),),
        )
        proxy.register_path(path)
        recorded = proxy.record_attempt(
            AssessmentAttempt(
                attempt_id="att-mirror-1",
                kp_id="javadi:v1:dependency",
                result="correct",
                evidence_type="quiz_correct",
                strength=1.0,
            )
        )
        assert recorded is True
        paths = [p for _, p, _, _ in received]
        assert "/api/v1/learning/paths" in paths
        assert "/api/v1/learning/attempts" in paths
    finally:
        server.shutdown()

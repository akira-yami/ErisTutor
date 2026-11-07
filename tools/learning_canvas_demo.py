"""Tasks 1–3 demo, with isolated temporary learning data (never a real profile).

python tools/learning_canvas_demo.py --udp
python tools/learning_canvas_demo.py --write-fixture
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime.incoming_protocol_runtime import IncomingProtocolRuntimeMixin
from runtime.learning_canvas import LearningCanvasService
from runtime.learning_canvas.inbox import CanvasControlInbox
from runtime.learning_canvas.snapshot import build_snapshot
from runtime.learning_core import LearningService, LearningStore
from runtime.learning_core.records import AssessmentAttempt
from transport.udp import UdpTransport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--udp", action="store_true")
    parser.add_argument("--write-fixture", action="store_true")
    args = parser.parse_args()
    scratch = Path(__file__).resolve().parents[1] / "scratch"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eris-canvas-demo-", dir=scratch) as directory, ExitStack() as resources:
        path = Path(directory) / "learning.sqlite3"
        store = LearningStore(path)
        resources.callback(store.close)
        learning = LearningService(store)
        canvas = LearningCanvasService(learning, store=store)
        resources.callback(canvas.close)
        node_id = "javadi:v1:constructor"
        if args.write_fixture:
            snapshot = build_snapshot(learning, canvas.pack, canvas.focus.snapshot())
            for i, node in enumerate(snapshot["nodes"]):
                node["state"] = ["mastered", "learning", "available", "practicing", "available"][i]
                node["due"] = i == 3
            fixture = {"snapshot": snapshot, "details": [canvas.pack.detail(key) for key in canvas.pack.path.kp_ids],
                       "exercise_previews": [canvas.exercises.open(node["id"], exercise["id"], "fixture:" + exercise["id"])
                                             for node in canvas.pack.nodes for exercise in node["exercises"]]}
            target = Path(__file__).parent / "web-canvas" / "learning_canvas_fixture.json"
            target.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            store.close()
            print(target)
            return
        request = {"type": "learning_canvas_request", "request_id": "demo-navigation", "action": "navigate",
                   "group_id": "java-di", "node_id": node_id, "base_revision": 0}
        if args.udp:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.bind(("127.0.0.1", 0))
                client.settimeout(2)
                transport = UdpTransport("127.0.0.1", client.getsockname()[1], "127.0.0.1", 0)
                host = SimpleNamespace(learning_canvas_service=canvas,
                                       _send_runtime_json=lambda payload, **_: transport.send_json(payload))
                inbox = CanvasControlInbox(transport, lambda msg: IncomingProtocolRuntimeMixin._handle_learning_canvas_request(host, msg))
                inbox.start()
                try:
                    start = time.perf_counter()
                    client.sendto(json.dumps(request).encode(), transport._recv_sock.getsockname())
                    response = json.loads(client.recv(65507))
                    print(f"UDP ack: {response['status']}, {(time.perf_counter() - start) * 1000:.1f} ms")
                finally:
                    inbox.close()
        else:
            response = canvas.handle(request)
        before = response["learning_canvas"]
        learning.record_attempt(AssessmentAttempt(
            "demo-attempt", node_id, "correct", "quiz_correct", strength=1.0,
            path_id=canvas.pack.path.path_id, item_family_id="demo-independent", recorded_at=time.time(),
        ))
        store.close()
        reopened = LearningStore(path)
        try:
            restored = LearningCanvasService(LearningService(reopened), store=reopened)
            after = restored.handle({"request_id": "demo-restart", "action": "open"})["learning_canvas"]
            assert after["focus"]["node_id"] == node_id and after["focus"]["pinned"]
            old = next(n for n in before["nodes"] if n["id"] == node_id)["state"]
            new = next(n for n in after["nodes"] if n["id"] == node_id)["state"]
            assert old != new
            print(f"Isolated synthetic attempt: {old} -> {new}; focus/pin/progress restored after reopening.")
            print(json.dumps(after, ensure_ascii=False, indent=2))
        finally:
            reopened.close()


if __name__ == "__main__":
    main()

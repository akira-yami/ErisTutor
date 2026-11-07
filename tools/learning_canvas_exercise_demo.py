"""Actual MC grading + optional real Docker/JVM spike, in an isolated test DB.

No model, TTS or user profile is used. No Java ever runs on the host.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime.learning_canvas import LearningCanvasService
from runtime.learning_core import LearningService, LearningStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-java", action="store_true")
    args = parser.parse_args()
    scratch = Path(__file__).resolve().parents[1] / "scratch"
    scratch.mkdir(exist_ok=True)
    node_id = "javadi:v1:constructor"
    with tempfile.TemporaryDirectory(prefix="canvas-exercises-", dir=scratch) as directory, ExitStack() as resources:
        store = LearningStore(Path(directory) / "learning.sqlite3")
        resources.callback(store.close)
        canvas = LearningCanvasService(LearningService(store), store=store)
        resources.callback(canvas.close)

        def send(action, **fields):
            reply = canvas.handle({"request_id": "demo-" + uuid.uuid4().hex, "action": action, **fields})
            if reply["status"] != "ok":
                raise RuntimeError(reply["message"])
            return reply

        def state():
            return next(n["state"] for n in send("open")["learning_canvas"]["nodes"] if n["id"] == node_id)

        send("navigate", node_id=node_id, base_revision=0)
        before = state()
        mc = send("exercise", node_id=node_id, exercise_id="di.constructor.choice")["exercise_update"]
        graded = send("submit", activity_id=mc["activity_id"], nonce=mc["nonce"], answer="a")["exercise_update"]
        after = state()
        assert before == "available" and after == "practicing" and graded["result"] == "correct"
        java = send("exercise", node_id=node_id, exercise_id="di.constructor.code")["exercise_update"]
        solution = canvas.pack.node(node_id)["exercises"][1]["solution"]
        send("submit", activity_id=java["activity_id"], nonce=java["nonce"], code=solution)
        canvas.exercises._future.result(timeout=30)
        java_result = send("exercise_status", activity_id=java["activity_id"], nonce=java["nonce"])["exercise_update"]
        reopened = LearningStore(store._path)
        resources.callback(reopened.close)
        restored = LearningCanvasService(LearningService(reopened), store=reopened)
        resources.callback(restored.close)
        snapshot = restored.state_packet()["learning_canvas"]
        assert snapshot["focus"]["node_id"] == node_id and snapshot["focus"]["pinned"]
        assert restored.learning.attempts()
        report = {"mc": {"result": graded["result"], "before": before, "after": after,
                         "assistance": graded["assistance"]},
                  "java_spike": {key: java_result[key] for key in
                      ("status", "result", "reason", "guidance", "tests", "duration_ms", "image") if key in java_result},
                  "attempt_count_after_restart": len(restored.learning.attempts()),
                  "focus_and_pin_restored": True, "model_and_voice": "not_run"}
        report["active_exercise_after_restart"] = snapshot.get("activity", {}).get("type")
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return 2 if args.require_java and java_result["status"] != "completed" else 0


if __name__ == "__main__":
    raise SystemExit(main())

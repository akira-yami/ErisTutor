# -*- coding: utf-8 -*-
"""Probe công việc dài: tool toán chạy thật trong khi chat không bị khóa.

Chỉ planner trong probe được mô phỏng; công cụ SymPy/SandboxWorld là thật.
Runtime Eris live dùng model qua plan_with_router, không dùng planner này.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from actions.contracts import ActionExecutionResult
from runtime.workspace import MathWorkspaceService, send_artifacts_udp
from runtime.workspace.parallel_work import ParallelWorkService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="Gửi tiến độ sang Godot nếu đã chạy")
    args = parser.parse_args()
    root = ROOT / "logs" / "parallel_work_probe"
    root.mkdir(parents=True, exist_ok=True)
    math = MathWorkspaceService(root / "artifacts")

    def plan(context):
        kinds = [row["kind"] for row in context["recent_events"]]
        if "tool_result" in kinds:
            return {"kind": "complete", "text": "Đã tính và xác nhận det(A) = -2.",
                    "action_id": "", "arguments": {}}
        return {"kind": "tool", "text": "Đang tính định thức của ma trận.",
                "action_id": "math_workspace", "arguments": {"kind": "matrix", "rows": [[1,2],[3,4]]}}

    def tool(_id, data):
        result = math.execute_sync(data)
        if args.send:
            send_artifacts_udp(result, timeout=.15, retries=1)
        return ActionExecutionResult(action_name="math_workspace", success=True,
                                     observed=True, message="SymPy đã trả kết quả thật",
                                     data={"artifact_job_id": result["job_id"]},
                                     evidence=("math:" + result["job_id"],))

    def publish(event):
        if not args.send or not event.get("text"):
            return
        send_artifacts_udp({"parse_mode": "workspace_artifact", "workspace_version": 1,
                            "job_id": event["job_id"]+"-"+str(event["revision"]),
                            "work_id": event["job_id"], "work_revision": event["revision"],
                            "title": "Tiến độ Solve Job", "artifacts": [
                                {"type": "section", "text": "Bước " + str(event["revision"])},
                                {"type": "text", "text": event["text"]}]},
                           timeout=.15, retries=1)

    lane = ParallelWorkService(root / "state.sqlite", planner=plan, tool=tool, publish=publish)
    job = lane.start("Kiểm chứng định thức ma trận [[1,2],[3,4]]")
    print("START (không đợi tool):", json.dumps(job, ensure_ascii=False))
    for _ in range(100):
        state = lane.snapshot(job["job_id"])
        if state["status"] in {"completed", "failed", "needs_review"}:
            break
        time.sleep(.1)
    print(json.dumps({"job":state, "events":lane.events(job["job_id"])},
                     ensure_ascii=False, indent=2))
    return 0 if state["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

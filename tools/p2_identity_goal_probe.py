# -*- coding: utf-8 -*-
"""Probe P2: Subject/Executive thật → Work Session → SymPy → Subject learning.

Không gọi LLM online và không phát TTS. Toán học qua worker thật, đầu ra có
artifact; đây là kiểm thử integration, không giả làm Godot/LLM live.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import uuid
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cortex.subject.contracts import ObservationOrigin
from cortex.subject.initiative import InitiativeState, PendingIntention, MotiveKind
from cortex.subject.service import SubjectService, SubjectServiceConfig
from runtime.mind_loop import MindLoop
from runtime.ai_orchestrated.executive_cortex import ExecutiveCortex
from runtime.autonomy.goal_events import GoalEventLedger
from runtime.agent_harness.identity_goal_driver import IdentityGoalDriver
from runtime.agent_harness.unified_sessions import UnifiedAgentSessions
from runtime.workspace import MathWorkspaceService


def run() -> dict:
    destination = ROOT / "logs" / "eris_p2_identity_probe" / uuid.uuid4().hex[:10]
    destination.mkdir(parents=True, exist_ok=True)
    now = time.time()
    subject = SubjectService(config=SubjectServiceConfig(restore_projection=False))
    subject.initiative_state = InitiativeState(
        revision=1, updated_at=now,
        pending_intentions=(PendingIntention(
            intention_id="probe-" + uuid.uuid4().hex[:12], motive_kind=MotiveKind.EXPLORE,
            semantic_goal="Kiểm tra định thức ma trận 2x2 bằng công cụ toán học",
            target="math", pressure=.98, commitment_strength=.97,
            created_at=now, updated_at=now, evidence_refs=("p2:math-case",),
            source_origin=ObservationOrigin.INTERNAL,
        ),),
    )
    executive = ExecutiveCortex(SimpleNamespace(), None, invoke_structured=lambda *_a, **_k: None)
    mind = MindLoop(subject=subject, committer_provider=lambda: executive)
    math = MathWorkspaceService(destination / "artifacts")
    artifacts: list[str] = []

    def plan(context):
        if not any(item.get("kind") == "tool_result" for item in context.get("recent_events", ())):
            return {"kind": "run_tool", "action_id": "math_workspace",
                    "text": "Dùng SymPy kiểm tra định thức.",
                    "arguments": {"kind": "matrix", "rows": [[1, 2], [3, 4]], "title": "Định thức mẫu"}}
        return {"kind": "complete", "action_id": "", "arguments": {},
                "text": "Đã nhận bằng chứng tính toán từ Math Workspace."}

    def tool(action, args):
        assert action == "math_workspace"
        payload = math.execute_sync(args)
        artifacts.extend(str(item.get("type")) for item in payload["artifacts"])
        return SimpleNamespace(success=True, observed=True, message="Worker toán đã thực thi", 
                               data={"math_job_id": payload["job_id"]},
                               evidence=("math-artifact:" + payload["job_id"],))

    sessions = UnifiedAgentSessions(destination / "work.sqlite", planner=plan,
                                    tool=tool, goal_ledger_path=destination / "goals.sqlite")
    driver = IdentityGoalDriver(subject, mind, sessions,
                                ledger=GoalEventLedger(destination / "goals.sqlite"))
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            driver.pump()
            goals = driver.ledger.list_goals()
            if goals and goals[0].status in {"achieved", "suspended", "failed"}:
                break
            time.sleep(.07)
        else:
            raise RuntimeError("Quá hạn chờ quyết định của Subject")
        goal = driver.ledger.list_goals()[0]
        with driver.ledger._db() as db:
            feedback = [row[0] for row in db.execute("SELECT status FROM autonomy_subject_feedback")]
        result = {"goal_id": goal.goal_id, "goal_status": goal.status,
                  "executive_source": goal.payload["adoption_proof"]["source"],
                  "work_jobs": sessions.recent(), "math_artifact_types": artifacts,
                  "subject_feedback": feedback, "path": str(destination)}
        if goal.status != "achieved" or "learned" not in feedback or not artifacts:
            raise AssertionError("Chưa khép được vòng tự chủ toán học P2: " + json.dumps(result, ensure_ascii=False))
        return result
    finally:
        driver.close()


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))

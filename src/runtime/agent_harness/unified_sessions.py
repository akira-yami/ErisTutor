# -*- coding: utf-8 -*-
"""Một bề mặt điều phối session cho Root và Work Lane.

Không tạo bộ não thứ hai: Root/Identity chọn công việc và lời thoại;
worker chỉ thực thi các bước đã giao. Mọi phiên Work dùng chung một store,
một bộ lập lịch và một contract sự kiện. Conversation không chờ worker.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from runtime.workspace.parallel_work import ParallelWorkService
from runtime.autonomy.goal_events import GoalEventLedger
from .goal_session_bridge import GoalSessionBridge


class UnifiedAgentSessions:
    """Facade một chủ sở hữu Work Session, tương thích API ParallelWork V6."""

    def __init__(self, db_path: str | Path, *, planner: Callable[[dict], Mapping[str, Any]] | None,
                 tool: Callable[[str, Mapping[str, Any]], Any] | None = None,
                 publish: Callable[[dict], Any] | None = None,
                 max_steps: int = 20, max_workers: int = 2,
                 powers_root: str | Path | None = None,
                 goal_ledger_path: str | Path | None = None):
        # Work vẫn là chủ sở hữu duy nhất của lịch chạy; Bridge chỉ liên kết dữ liệu.
        self._external_publish = publish or (lambda _event: None)
        self._goal_bridge = None
        self.work = ParallelWorkService(db_path, planner=planner, tool=tool,
                                        publish=self._publish_work, max_steps=max_steps,
                                        max_workers=max_workers, powers_root=powers_root,
                                        should_continue=self._goal_still_active)
        if goal_ledger_path is not None:
            self._goal_bridge = GoalSessionBridge(
                work_db_path=db_path, goal_ledger=GoalEventLedger(goal_ledger_path),
                start_work=self.work.start, snapshot_work=self.work.snapshot)
            self._goal_bridge.reconcile_all()

    def _goal_still_active(self, job_id: str) -> bool:
        return self._goal_bridge.goal_fresh(job_id) if self._goal_bridge else True

    def _publish_work(self, event: dict) -> None:
        if self._goal_bridge is not None:
            try:
                self._goal_bridge.on_work_event(event)
            except Exception:
                # Lỗi projection goal không được chặn cập nhật tiến độ Panel.
                pass
        self._external_publish(event)


    def start(self, task: str) -> dict:
        return self.work.start(task)

    def snapshot(self, job_id: str) -> dict | None:
        return self.work.snapshot(job_id)

    def recent(self, limit: int = 4) -> list[dict]:
        return self.work.recent(limit)

    def events(self, job_id: str, limit: int = 15) -> list[dict]:
        return self.work.events(job_id, limit)

    def cancel(self, job_id: str) -> dict:
        return self.work.cancel(job_id)

    def resume(self, job_id: str, answer: str) -> dict:
        return self.work.resume(job_id, answer)

    def document(self, job_id: str) -> dict | None:
        return self.work.document(job_id)

    def edit_document(self, job_id: str, *, operation: str, block_id: str = "",
                      block: Mapping[str, Any] | None = None,
                      base_revision: int | None = None) -> dict:
        return self.work.edit_document(job_id, operation=operation, block_id=block_id,
                                       block=block, base_revision=base_revision)

    def execute(self, command: Mapping[str, Any]) -> dict:
        """Một contract điều khiển chung, dùng implementation của Work Store."""
        op = str(command.get("operation", "") or "")
        if op in {"goals", "goal_status", "start_goal", "continue_goal"}:
            if self._goal_bridge is None:
                return {"error": "goal_registry_unavailable"}
            if op == "goals":
                return self._goal_bridge.list()
            if op == "goal_status":
                return self._goal_bridge.state(str(command.get("goal_id", "")))
            if op == "continue_goal":
                return self._goal_bridge.continue_goal(str(command.get("goal_id", "")))
            return self._goal_bridge.start_goal(str(command.get("goal_id", "")))
        return self.work.execute(command)

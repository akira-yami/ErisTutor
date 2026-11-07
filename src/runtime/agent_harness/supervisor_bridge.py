# -*- coding: utf-8 -*-
"""Bridge for Eris Subject Core to supervise an execution task.

The Subject Core is allowed to intervene.  It does so through typed directives
instead of mutating LangGraph state behind the executor's back.
"""
from __future__ import annotations

from typing import Any

from .event_bus import AgentEventBus
from .task_store import AgentTaskStore


class AgentSupervisorBridge:
    def __init__(
        self,
        store: AgentTaskStore | None = None,
        event_bus: AgentEventBus | None = None,
        *,
        logger: Any = None,
    ) -> None:
        self.store = store or AgentTaskStore()
        self.events = event_bus or AgentEventBus(self.store, logger=logger)

    def comment(self, task_id: str, text: str, *, importance: float = 0.55) -> bool:
        return self.events.emit(
            task_id,
            "supervisor_comment",
            actor="eris",
            summary=text,
            importance=importance,
        ) is not None

    def advisory(self, task_id: str, instruction: str, *, reason: str = "") -> bool:
        control = self.store.enqueue_control(task_id, "directive", text=instruction, data={"source": "eris", "kind": "advisory"})
        if control is None:
            return False
        self.events.emit(
            task_id,
            "supervisor_advisory",
            actor="eris",
            summary=instruction,
            detail=reason,
            importance=0.78,
            data={"control_id": control.get("control_id", "")},
        )
        return True

    def redirect(self, task_id: str, instruction: str, *, reason: str = "", checkpoint_id: str = "") -> bool:
        # Redirect is stronger than advisory but still lets the executor replan.
        # Optional checkpoint_id asks the control plane to rewind before replanning.
        if checkpoint_id:
            rollback = self.store.enqueue_control(
                task_id,
                "rollback",
                checkpoint_id=checkpoint_id,
                data={"source": "eris", "kind": "redirect"},
            )
            if rollback is None:
                return False
        control = self.store.enqueue_control(
            task_id,
            "directive",
            text=instruction,
            data={"source": "eris", "kind": "redirect"},
        )
        if control is None:
            return False
        self.events.emit(
            task_id,
            "supervisor_redirect",
            actor="eris",
            summary=instruction,
            detail=reason,
            importance=0.94,
            data={
                "control_id": control.get("control_id", ""),
                "checkpoint_id": checkpoint_id,
            },
        )
        return True


    def continue_task(self, task_id: str, instruction: str = "Tiếp tục theo hướng hiện tại.") -> bool:
        """Eris xác nhận executor có thể tiếp tục mà không đổi hướng."""
        return self.advisory(task_id, instruction, reason="Eris xác nhận hướng hiện tại vẫn hợp lý.")

    def reconsider(self, task_id: str, instruction: str, *, reason: str = "") -> bool:
        """Buộc executor replan ở bước kiểm soát kế tiếp, không chỉ ghi một lời nhắc UI."""
        return self.redirect(task_id, instruction, reason=reason)

    def ask_user(self, task_id: str, question: str, *, reason: str = "") -> bool:
        """Yêu cầu executor dừng ở interrupt kế tiếp để hỏi user thay vì tự đoán."""
        control = self.store.enqueue_control(
            task_id,
            "directive",
            text=(
                "Không được tự chọn thay user. Hãy dùng ask_user_choice/interrupt ở bước hợp lý kế tiếp. "
                f"Câu cần hỏi: {question}"
            ),
            data={"source": "eris", "kind": "ask_user", "question": question},
        )
        if control is None:
            return False
        self.events.emit(
            task_id,
            "supervisor_ask_user",
            actor="eris",
            summary=question,
            detail=reason,
            importance=0.96,
            data={"control_id": control.get("control_id", "")},
        )
        return True

    def stop(self, task_id: str, reason: str, *, instruction: str = "Dừng task hiện tại.") -> bool:
        """Dừng executor có chủ đích khi Eris thấy hướng hiện tại không còn an toàn/hợp lệ."""
        control = self.store.enqueue_control(
            task_id,
            "stop",
            text=instruction,
            data={"source": "eris", "kind": "stop", "reason": reason},
        )
        if control is None:
            return False
        self.events.emit(
            task_id,
            "supervisor_stop",
            actor="eris",
            summary=instruction,
            detail=reason,
            importance=1.0,
            data={"control_id": control.get("control_id", "")},
        )
        return True

    def veto(self, task_id: str, reason: str, *, instruction: str = "Dừng hướng hiện tại và chờ đánh giá lại.") -> bool:
        pause = self.store.enqueue_control(task_id, "pause", data={"source": "eris", "kind": "veto"})
        if pause is None:
            return False
        directive = self.store.enqueue_control(task_id, "directive", text=instruction, data={"source": "eris", "kind": "veto"})
        self.events.emit(
            task_id,
            "supervisor_veto",
            actor="eris",
            summary=instruction,
            detail=reason,
            importance=1.0,
            data={
                "pause_control_id": pause.get("control_id", ""),
                "directive_control_id": (directive or {}).get("control_id", ""),
            },
        )
        return True


__all__ = ["AgentSupervisorBridge"]

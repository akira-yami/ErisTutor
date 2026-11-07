# -*- coding: utf-8 -*-
from __future__ import annotations

"""Enrich WinEvent thành semantic control bằng UI Automation.

WinEvent callback chỉ ghi metadata rẻ. Adapter này chạy ở consumer tick, sau
callback, nên mới được phép thực hiện cross-process UIA call. Nó ưu tiên node
đang focus/selected trong cache của HWND và không đọc value của password/control
nhạy cảm. Nhờ vậy Eris biết "editor/dialog/control" thay vì chỉ biết HWND đổi.
"""

from typing import Iterable

from runtime.ai_orchestrated.environment_event_bus import NormalizedEnvironmentEvent
from .process_resolver import ProcessResolver
from .uia_event_client import UIAEventClient, UIANodeSnapshot


_IDE_PROCESSES = {"code.exe", "devenv.exe", "pycharm64.exe", "idea64.exe", "rider64.exe"}
_DIALOG_HINTS = ("dialog", "window")
_PERMISSION_HINTS = ("permission", "allow", "access", "consent", "authorize", "quyền", "cho phép")


class UIASemanticAdapter:
    def __init__(self, *, uia: UIAEventClient | None = None, resolver: ProcessResolver | None = None) -> None:
        self.uia = uia or UIAEventClient(max_nodes=180, max_depth=7)
        self.resolver = resolver or ProcessResolver()
        self.uia.start()

    def stop(self) -> None:
        self.uia.stop()

    @staticmethod
    def _role(node: UIANodeSnapshot) -> str:
        return str(node.role or "").strip().lower().replace("_", " ")

    @classmethod
    def _choose_node(cls, nodes: Iterable[UIANodeSnapshot], event_type: str) -> UIANodeSnapshot | None:
        items = tuple(nodes)
        if not items:
            return None
        if event_type in {"focus_changed", "foreground_changed"}:
            focused = next((node for node in items if node.has_focus), None)
            if focused is not None:
                return focused
        if event_type == "selection_changed":
            selected = next((node for node in items if node.selected), None)
            if selected is not None:
                return selected
        # Fallback chọn node có focus/selection trước, rồi node có tên semantic.
        return next((node for node in items if node.has_focus or node.selected), None) or next((node for node in items if node.name), items[0])

    @classmethod
    def _semantic_kind(cls, process_name: str, node: UIANodeSnapshot, raw_kind: str) -> tuple[str, int, float]:
        role = cls._role(node)
        proc = str(process_name or "").lower()
        name = str(node.name or "").lower()

        if any(token in role for token in _DIALOG_HINTS) and raw_kind in {"object_shown", "focus_changed", "foreground_changed"}:
            if any(token in name for token in _PERMISSION_HINTS):
                return "blocking_dialog", 4, 0.86
            return "dialog_candidate", 2, 0.62
        if proc in _IDE_PROCESSES and any(token in role for token in ("edit", "document", "text")):
            return "editor_focused", 1, 0.28
        if "terminal" in role:
            return "terminal_focused", 1, 0.25
        if "tab" in role and raw_kind in {"selection_changed", "focus_changed"}:
            return "ui_tab_activated", 1, 0.20
        if raw_kind == "value_changed":
            return "control_value_changed", 1, 0.14
        if raw_kind == "name_changed":
            return "control_name_changed", 1, 0.14
        return "control_focused" if raw_kind == "focus_changed" else "ui_control_changed", 1, 0.16

    def enrich(self, event: NormalizedEnvironmentEvent) -> NormalizedEnvironmentEvent | None:
        if event.source not in {"winevent", "foreground_reconcile"} or not int(event.hwnd or 0):
            return None
        hwnd = int(event.hwnd)
        self.uia.invalidate(hwnd)
        nodes = self.uia.snapshot(hwnd, force=True)
        node = self._choose_node(nodes, event.event_type)
        if node is None:
            return None

        identity = self.resolver.resolve(hwnd)
        process_name = event.process_name or identity.process_name
        kind, attention, importance = self._semantic_kind(process_name, node, event.event_type)
        is_password = bool(node.is_password)
        # Password là hard block ở EnvironmentPolicy. Không copy Name/Value vào
        # event trước khi policy được áp, chỉ đánh dấu is_password.
        title = "" if is_password else str(node.name or "")[:240]
        role = str(node.role or "control")[:100]
        return NormalizedEnvironmentEvent(
            timestamp=event.timestamp,
            source="uia",
            event_type=kind,
            action=event.event_type,
            process_name=process_name,
            pid=int(event.pid or identity.pid),
            hwnd=hwnd,
            window_class=event.window_class or identity.window_class,
            region_id=f"{process_name or 'process'}:{int(event.pid or identity.pid)}:hwnd_{hwnd}:uia:{node.runtime_id}",
            role=role,
            title=title,
            semantic=True,
            source_confidence=0.94,
            confidence=0.92,
            novelty=0.58 if attention >= 2 else 0.35,
            importance=importance,
            user_relevance=0.72 if attention >= 2 else 0.52,
            attention_level=attention,
            metadata={
                "runtime_id": node.runtime_id,
                "automation_id": node.automation_id,
                "is_password": is_password,
                "has_focus": bool(node.has_focus),
                "selected": bool(node.selected),
                "bounds": node.bounds,
            },
            evidence_refs=(event.event_id, f"uia:{node.runtime_id}"),
        )


__all__ = ["UIASemanticAdapter"]

# -*- coding: utf-8 -*-
"""V83/V89 - Correlate recent coding-flow with fresh environment evidence.

This module is deliberately advisory. It never claims causality, never executes a
tool, and only proposes the next evidence source when code-change context and the
current environment are both fresh enough to correlate safely.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(slots=True)
class CrossEnvironmentDirective:
    active: bool = False
    correlation_kind: str = ""
    primary_tool_hint: str = ""
    supporting_tool_hints: tuple[str, ...] = ()
    rationale_vi: str = ""
    evidence_refs: tuple[str, ...] = ()
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.cross_environment.directive.v1",
            "active": bool(self.active),
            "correlation_kind": self.correlation_kind,
            "primary_tool_hint": self.primary_tool_hint,
            "supporting_tool_hints": list(self.supporting_tool_hints),
            "rationale_vi": self.rationale_vi,
            "evidence_refs": list(self.evidence_refs),
            "confidence": round(max(0.0, min(1.0, float(self.confidence))), 4),
            "epistemic_rule": (
                "Correlation giữa patch gần đây và trạng thái runtime không phải bằng chứng nhân quả; "
                "phải tái hiện/đo trực tiếp trước khi kết luận patch gây lỗi."
            ),
        }


class CrossEnvironmentSupervisor:
    """Small deterministic evidence bridge used by Engineering Mentor."""

    max_environment_age_seconds = 120.0
    max_flow_age_seconds = 6 * 3600.0

    @staticmethod
    def _changed_files(flow: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(str(x) for x in (flow.get("changed_files") or ()) if str(x).strip())

    @staticmethod
    def _is_godot(env: Mapping[str, Any], files: tuple[str, ...]) -> bool:
        hay = " ".join([
            str(env.get("focused_app") or ""), str(env.get("focused_process") or ""),
            str(env.get("focused_object") or ""), str(env.get("task_hint") or ""),
            *files,
        ]).casefold()
        return "godot" in hay or any(Path(x).suffix.casefold() in {".gd", ".tscn", ".tres"} for x in files)

    def analyze(
        self,
        user_text: str,
        *,
        coding_flow: Mapping[str, Any] | None = None,
        environment: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> CrossEnvironmentDirective:
        ts = float(now or time.time())
        flow = dict(coding_flow or {})
        env = dict(environment or {})
        if not flow or not env:
            return CrossEnvironmentDirective()

        env_age = float(env.get("age_seconds", 1e9) or 1e9)
        env_conf = float(env.get("confidence", 0.0) or 0.0)
        env_fresh = bool(env.get("fresh", env_age <= self.max_environment_age_seconds))
        if not env_fresh or env_age > self.max_environment_age_seconds or env_conf < 0.45:
            return CrossEnvironmentDirective()

        status = str(flow.get("status") or "")
        ended = float(flow.get("ended_at", 0.0) or 0.0)
        started = float(flow.get("started_at", 0.0) or 0.0)
        anchor = started if status == "running" else ended or started
        if anchor <= 0.0 or ts < anchor or ts - anchor > self.max_flow_age_seconds:
            return CrossEnvironmentDirective()

        files = self._changed_files(flow)
        low = " ".join([
            str(user_text or ""), str(env.get("task_hint") or ""), str(env.get("phase") or ""),
            str(env.get("last_outcome") or ""), str(env.get("activity") or ""),
        ]).casefold()
        app = f"{env.get('focused_app', '')} {env.get('focused_process', '')}".casefold()
        evidence_refs = tuple(str(x)[:120] for x in (env.get("evidence_refs") or ()) if str(x).strip())[:6]
        supporting: list[str] = ["git_diff"] if files else []

        if any(k in low for k in ("fps", "frame spike", "lag", "performance", "tụt fps", "giật")):
            return CrossEnvironmentDirective(True, "post_change_performance_validation", "profiler", tuple(supporting),
                "Có thay đổi code gần đây và môi trường đang cho thấy câu hỏi hiệu năng; profiler là evidence trực tiếp hơn suy từ diff.",
                evidence_refs, env_conf)

        if "visual studio code" in app or "code.exe" in app:
            if any(k in low for k in ("review", "đổi gì", "sửa xong", "diff", "changes", "change")):
                return CrossEnvironmentDirective(True, "ide_change_review", "git_diff", (),
                    "IDE hiện đang ở ngữ cảnh review thay đổi gần đây; đọc diff trước khi suy tác động runtime.", evidence_refs, env_conf)

        is_godot = self._is_godot(env, files)
        failure = any(k in low for k in ("runtime error", "crash", "exception", "failed", "vẫn lỗi", "game vẫn lỗi"))
        state_question = any(k in low for k in (
            "node", "transform", "collision", "runtime state", "lệch", "position", "scene tree", "inspector"
        ))
        if is_godot and failure:
            return CrossEnvironmentDirective(True, "runtime_failure_after_change", "godot_debugger", tuple(supporting),
                "Có failure runtime sau thay đổi gần đây. Dùng Godot Debugger/Monitors để lấy lỗi trực tiếp; tương quan thời gian chưa chứng minh patch là nguyên nhân.",
                evidence_refs, env_conf)
        if is_godot and state_question:
            return CrossEnvironmentDirective(True, "runtime_state_after_change", "godot_remote_scene", tuple(supporting),
                "Code vừa thay đổi và câu hỏi hiện tại liên quan state/transform/collision runtime; Remote Scene Tree cho evidence trạng thái thực.",
                evidence_refs, env_conf)

        return CrossEnvironmentDirective()


__all__ = ["CrossEnvironmentSupervisor", "CrossEnvironmentDirective"]

# -*- coding: utf-8 -*-
"""V87 - User control cho mô hình năng lực và mục tiêu phát triển.

Thiết kế cố tình nhỏ:
- Không gọi LLM, không đọc source code, không điều khiển GitPilot.
- Chỉ lưu feedback có cấu trúc + mục tiêu user chủ động chọn từ đề xuất V86.
- Không coi nút "Tôi đã biết" là bằng chứng mastery.
- Control chỉ được gọi từ kênh Desktop trusted/in-process; Eris Link HTTP vẫn read-only.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from runtime.workspace_manager import default_eris_home

_SCHEMA = "eris.development_control.v1"
_ALLOWED_FEEDBACK = {"confirmed", "already_knew", "wrong", "reset"}


def _safe_id(value: str, *, prefix: str) -> str:
    material = str(value or "").strip().encode("utf-8", errors="ignore")
    return f"{prefix}-{hashlib.sha1(material).hexdigest()[:12]}"


def _metric_value(panel: Mapping[str, Any], metric: Mapping[str, Any]) -> int:
    """Đọc metric từ chính projection user-visible, không chạm private state."""
    kind = str(metric.get("kind") or "")
    ident = str(metric.get("id") or "")
    if kind == "group_evidence":
        for group in panel.get("nhom_nang_luc") or ():
            if isinstance(group, Mapping) and str(group.get("id") or "") == ident:
                return int(group.get("so_bang_chung", 0) or 0)
    if kind == "tool_demonstrated":
        for tool in panel.get("cong_cu") or ():
            if isinstance(tool, Mapping) and str(tool.get("id") or "") == ident:
                return int(tool.get("so_lan_chung_minh", 0) or 0)
    if kind == "ai_signal":
        stats = panel.get("thoi_quen_ai_30_ngay") or {}
        if isinstance(stats, Mapping):
            return int(stats.get(ident, 0) or 0)
    return 0


@dataclass(slots=True)
class DevelopmentControlService:
    """State local-first cho V87: correction audit + user-selected goals."""

    path: Path | None = None
    logger: Any = None
    goals: list[dict[str, Any]] = field(default_factory=list, init=False)
    feedback: list[dict[str, Any]] = field(default_factory=list, init=False)
    updated_at: float = field(default=0.0, init=False)
    _lock: threading.RLock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.path is None:
            self.path = default_eris_home() / "mentor" / "development_control.json"
        self._lock = threading.RLock()
        self.goals: list[dict[str, Any]] = []
        self.feedback: list[dict[str, Any]] = []
        self.updated_at = 0.0
        self._load()

    def record_skill_feedback(self, skill_id: str, kind: str, *, now: float | None = None) -> bool:
        skill = str(skill_id or "").strip()
        action = str(kind or "").strip().lower()
        if not skill or action not in _ALLOWED_FEEDBACK:
            return False
        ts = float(now or time.time())
        with self._lock:
            # Chỉ lưu metadata có cấu trúc. Không lưu câu chat/raw evidence.
            self.feedback.append({"at": ts, "skill_id": skill[:120], "kind": action})
            self.feedback = self.feedback[-120:]
            self.updated_at = ts
            self._save_locked()
        return True

    def follow_recommendation(self, recommendation: Mapping[str, Any], panel: Mapping[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
        rec_id = str(recommendation.get("id") or "").strip()
        title = str(recommendation.get("tieu_de") or "").strip()
        metric = recommendation.get("metric") or {}
        if not rec_id or not title or not isinstance(metric, Mapping):
            return None
        ts = float(now or time.time())
        with self._lock:
            for goal in self.goals:
                if str(goal.get("recommendation_id") or "") == rec_id and str(goal.get("status") or "") == "active":
                    return dict(goal)
            # V87 cố tình giữ tối đa 3 mục tiêu active để coach không biến thành
            # backlog học tập dài vô tận. User phải dừng một mục trước khi thêm mới.
            if sum(1 for goal in self.goals if str(goal.get("status") or "") == "active") >= 3:
                return None
            baseline = _metric_value(panel, metric)
            target_delta = max(1, int(metric.get("target_delta", 3) or 3))
            goal = {
                "id": _safe_id(f"{rec_id}:{ts}", prefix="goal"),
                "recommendation_id": rec_id,
                "tieu_de": title[:180],
                "uu_tien": str(recommendation.get("uu_tien") or "Trung bình")[:32],
                "thoi_han": str(recommendation.get("thoi_han") or "")[:64],
                "ly_do": str(recommendation.get("ly_do") or "")[:360],
                "thuc_hanh": str(recommendation.get("thuc_hanh") or "")[:360],
                "dau_hieu_tien_bo": str(recommendation.get("dau_hieu_tien_bo") or "")[:360],
                "metric": {"kind": str(metric.get("kind") or ""), "id": str(metric.get("id") or "")[:120], "target_delta": target_delta},
                "baseline": baseline,
                "status": "active",
                "created_at": ts,
                "completed_at": 0.0,
                "stopped_at": 0.0,
            }
            self.goals.append(goal)
            self.goals = self.goals[-40:]
            self.updated_at = ts
            self._save_locked()
            return dict(goal)

    def stop_goal(self, goal_id: str, *, now: float | None = None) -> bool:
        ident = str(goal_id or "").strip()
        if not ident:
            return False
        ts = float(now or time.time())
        with self._lock:
            for goal in self.goals:
                if str(goal.get("id") or "") == ident and str(goal.get("status") or "") == "active":
                    goal["status"] = "stopped"
                    goal["stopped_at"] = ts
                    self.updated_at = ts
                    self._save_locked()
                    return True
        return False

    def decorate(self, panel: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        """Thêm goal/control projection vào V86 snapshot và auto-complete theo evidence."""
        ts = float(now or time.time())
        changed = False
        active: list[dict[str, Any]] = []
        with self._lock:
            for goal in self.goals:
                if str(goal.get("status") or "") != "active":
                    continue
                metric = goal.get("metric") or {}
                current = _metric_value(panel, metric if isinstance(metric, Mapping) else {})
                baseline = int(goal.get("baseline", 0) or 0)
                target_delta = max(1, int((metric or {}).get("target_delta", 3) or 3)) if isinstance(metric, Mapping) else 3
                gained = max(0, current - baseline)
                progress = max(0, min(100, int(round(100.0 * gained / target_delta))))
                if progress >= 100:
                    goal["status"] = "completed"
                    goal["completed_at"] = ts
                    changed = True
                    continue
                active.append({
                    "id": str(goal.get("id") or ""),
                    "tieu_de": str(goal.get("tieu_de") or ""),
                    "uu_tien": str(goal.get("uu_tien") or ""),
                    "thoi_han": str(goal.get("thoi_han") or ""),
                    "thuc_hanh": str(goal.get("thuc_hanh") or ""),
                    "dau_hieu_tien_bo": str(goal.get("dau_hieu_tien_bo") or ""),
                    "tien_do": progress,
                    "da_them_bang_chung": gained,
                    "muc_tieu_bang_chung": target_delta,
                    "created_at": float(goal.get("created_at", 0.0) or 0.0),
                })

            cutoff = ts - 30 * 86400.0
            completed = sum(1 for x in self.goals if str(x.get("status") or "") == "completed" and float(x.get("completed_at", 0.0) or 0.0) >= cutoff)
            stopped = sum(1 for x in self.goals if str(x.get("status") or "") == "stopped" and float(x.get("stopped_at", 0.0) or 0.0) >= cutoff)
            recent_feedback = [x for x in self.feedback if float(x.get("at", 0.0) or 0.0) >= cutoff]
            corrections = sum(1 for x in recent_feedback if str(x.get("kind") or "") in {"already_knew", "wrong"})
            resets = sum(1 for x in recent_feedback if str(x.get("kind") or "") == "reset")
            feedback_by_skill: dict[str, str] = {}
            for item in recent_feedback:
                feedback_by_skill[str(item.get("skill_id") or "")] = str(item.get("kind") or "")

            if changed:
                self.updated_at = ts
                self._save_locked()

        # Attach latest explicit user feedback to skill rows; this is metadata only.
        for group in panel.get("nhom_nang_luc") or ():
            if not isinstance(group, dict):
                continue
            for skill in group.get("ky_nang") or ():
                if isinstance(skill, dict):
                    skill["phan_hoi_cua_user"] = feedback_by_skill.get(str(skill.get("id") or ""), "")

        panel["muc_tieu_hien_tai"] = active[:3]
        panel["thong_ke_phat_trien_30_ngay"] = {
            "muc_tieu_hoan_thanh": completed,
            "muc_tieu_da_dung": stopped,
            "muc_tieu_dang_theo_doi": len(active),
            "lan_user_sua_danh_gia": corrections,
            "lan_dat_lai_ky_nang": resets,
        }
        panel["user_control"] = {
            "skill_feedback": ["confirmed", "already_knew", "wrong", "reset"],
            "goal_control": ["follow", "stop"],
            "loopback_http_write_exposed": False,
            "ghi_chu": "Các thao tác sửa/reset chỉ đi qua Desktop trusted channel; Eris Link HTTP vẫn read-only.",
        }
        return panel

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": _SCHEMA,
                "updated_at": self.updated_at,
                "goals": [dict(x) for x in self.goals[-40:]],
                "feedback": [dict(x) for x in self.feedback[-120:]],
            }

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path and self.path.exists() else {}
            if not isinstance(raw, Mapping) or str(raw.get("schema") or "") != _SCHEMA:
                return
            self.updated_at = float(raw.get("updated_at", 0.0) or 0.0)
            self.goals = [dict(x) for x in (raw.get("goals") or []) if isinstance(x, Mapping)][-40:]
            self.feedback = [dict(x) for x in (raw.get("feedback") or []) if isinstance(x, Mapping)][-120:]
        except Exception:
            return

    def _save_locked(self) -> None:
        try:
            if self.path is None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as exc:
            if self.logger is not None:
                fn = getattr(self.logger, "debug", None)
                if callable(fn):
                    fn("[v87] development control persistence skipped: %s", exc)


__all__ = ["DevelopmentControlService"]

# -*- coding: utf-8 -*-
"""Relational Plan V76.

Một câu như "làm tôi vui lên" không phải một prompt một-lượt. Đây là một mục tiêu
quan hệ kéo dài qua nhiều phút và nhiều autonomous tick. Module này giữ plan như
state typed, tách khỏi câu chữ Surface.

Runtime chỉ tạo plan khi user nói đủ rõ hoặc khi caller truyền goal typed. Nó không
đoán bệnh/tâm trạng từ webcam, không coi im lặng là thất bại và không tối ưu engagement
bằng mọi giá. Plan có thể dùng SILENCE như một chiến lược hợp lệ.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import time
from typing import Any, Mapping, Sequence

from runtime.interaction_ecology import (
    MODE_ASK, MODE_CALLBACK, MODE_COMMENT, MODE_MUTTER, MODE_SCREEN_REACT,
    MODE_SHARE, MODE_SILENCE, MODE_STORY, MODE_SUPPORT, MODE_TEASE,
)

PLAN_SCHEMA = "eris.relational_plan.v76"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = default
    if n != n:
        n = default
    return max(0.0, min(1.0, n))


def _compact(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


_MOOD_LIFT_RE = re.compile(
    r"(?:\blàm\s+(?:tôi|tui|mình|anh|em)\s+(?:vui|đỡ buồn|khá hơn)\b"
    r"|\bchọc\s+(?:tôi|tui|mình)\s+cười\b|\bcheer\s+me\s+up\b)", re.I
)
_COMPANY_RE = re.compile(
    r"(?:\bở\s+(?:đây|cạnh|bên)\s+(?:tôi|tui|mình|anh|em)\b"
    r"|\bnói\s+chuyện\s+với\s+(?:tôi|tui|mình)\b|\bkeep\s+me\s+company\b)", re.I
)
_NO_QUESTIONS_RE = re.compile(
    r"(?:\bđừng\s+hỏi\s+(?:nhiều|nữa|liên tục)\b|\bbớt\s+hỏi\b|\bstop\s+asking\b)", re.I
)
_QUIET_RE = re.compile(
    r"(?:\bđể\s+(?:tôi|tui|mình)\s+yên\b|\bim\s+(?:đi|nha|nhé)\b|\bđừng\s+nói\b|\bleave\s+me\s+alone\b)", re.I
)


@dataclass(slots=True)
class RelationalPlan:
    plan_id: str
    goal_kind: str
    objective: str
    source: str = "explicit_user"
    source_ref: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float = 0.0
    status: str = "active"  # active / simmering / completed / abandoned / cancelled
    progress: float = 0.0
    confidence: float = 1.0
    preferred_modes: tuple[str, ...] = ()
    avoid_modes: tuple[str, ...] = ()
    attempts: int = 0
    autonomous_attempts: int = 0
    user_contributions: int = 0
    strategy_counts: dict[str, int] = field(default_factory=dict)
    last_strategy: str = ""
    last_progress_reason: str = ""
    response_policy: str = "mostly_none"
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = PLAN_SCHEMA
        payload["progress"] = round(_clamp01(self.progress), 4)
        payload["confidence"] = round(_clamp01(self.confidence, 1.0), 4)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RelationalPlan":
        return cls(
            plan_id=str(data.get("plan_id", "") or ""),
            goal_kind=str(data.get("goal_kind", "") or ""),
            objective=_compact(data.get("objective", ""), 600),
            source=str(data.get("source", "explicit_user") or "explicit_user"),
            source_ref=str(data.get("source_ref", "") or ""),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            expires_at=float(data.get("expires_at", 0.0) or 0.0),
            status=str(data.get("status", "active") or "active"),
            progress=_clamp01(data.get("progress", 0.0)),
            confidence=_clamp01(data.get("confidence", 1.0), 1.0),
            preferred_modes=tuple(str(x) for x in (data.get("preferred_modes", ()) or ()) if str(x))[:12],
            avoid_modes=tuple(str(x) for x in (data.get("avoid_modes", ()) or ()) if str(x))[:12],
            attempts=max(0, int(data.get("attempts", 0) or 0)),
            autonomous_attempts=max(0, int(data.get("autonomous_attempts", 0) or 0)),
            user_contributions=max(0, int(data.get("user_contributions", 0) or 0)),
            strategy_counts={str(k): max(0, int(v or 0)) for k, v in dict(data.get("strategy_counts", {}) or {}).items()},
            last_strategy=str(data.get("last_strategy", "") or ""),
            last_progress_reason=str(data.get("last_progress_reason", "") or ""),
            response_policy=str(data.get("response_policy", "mostly_none") or "mostly_none"),
            notes=tuple(str(x) for x in (data.get("notes", ()) or ()) if str(x))[-12:],
        )


class RelationalPlanManager:
    def __init__(self, plans: Sequence[RelationalPlan] = (), *, logger: Any = None) -> None:
        self.plans: list[RelationalPlan] = list(plans)[-12:]
        self.logger = logger

    def observe_user_turn(self, text: str, *, turn_ref: str = "", now: float | None = None) -> RelationalPlan | None:
        current = float(now or time.time())
        clean = _compact(text, 1200)
        if not clean:
            return self.active(now=current)

        # User đặt boundary mới thì plan xã hội phải nhường ngay. Concern private có
        # thể còn tồn tại ở subsystem khác, nhưng public plan dừng.
        if _QUIET_RE.search(clean):
            for plan in self.plans:
                if plan.status in {"active", "simmering"}:
                    plan.status = "cancelled"
                    plan.updated_at = current
                    plan.last_progress_reason = "user_requested_quiet"
            return None

        if _MOOD_LIFT_RE.search(clean):
            plan = self._upsert(
                goal_kind="mood_lift",
                objective="Giúp không khí của user nhẹ hơn trong một khoảng thời gian, không ép phản hồi.",
                preferred=(MODE_TEASE, MODE_SCREEN_REACT, MODE_STORY, MODE_MUTTER, MODE_COMMENT, MODE_SILENCE),
                avoid=(MODE_ASK,),
                source_ref=turn_ref,
                current=current,
                ttl=90 * 60,
            )
        elif _COMPANY_RE.search(clean):
            plan = self._upsert(
                goal_kind="keep_company",
                objective="Hiện diện cạnh user bằng nhịp nhẹ, có thể nói hoặc im, không biến thành phỏng vấn.",
                preferred=(MODE_COMMENT, MODE_SCREEN_REACT, MODE_MUTTER, MODE_CALLBACK, MODE_SHARE, MODE_SILENCE),
                avoid=(MODE_ASK,),
                source_ref=turn_ref,
                current=current,
                ttl=120 * 60,
            )
        elif _NO_QUESTIONS_RE.search(clean):
            plan = self._upsert(
                goal_kind="reduce_questions",
                objective="Giảm mạnh câu hỏi; ưu tiên comment, share, screen reaction và silence.",
                preferred=(MODE_COMMENT, MODE_SCREEN_REACT, MODE_MUTTER, MODE_SHARE, MODE_SILENCE),
                avoid=(MODE_ASK,),
                source_ref=turn_ref,
                current=current,
                ttl=24 * 3600,
            )
        else:
            plan = self.active(now=current)

        if plan is not None:
            plan.user_contributions += 1
            plan.updated_at = current
            # User tự quay lại tương tác là progress nhẹ cho company/mood plan,
            # nhưng không được suy "user vui" chỉ vì có một message.
            if plan.goal_kind in {"mood_lift", "keep_company"}:
                plan.progress = _clamp01(plan.progress + 0.06)
                plan.last_progress_reason = "user_voluntary_contribution"
            self._observe_explicit_positive_signal(plan, clean)
        return plan

    def observe_public_action(
        self,
        *,
        mode: str,
        autonomous: bool,
        now: float | None = None,
    ) -> RelationalPlan | None:
        current = float(now or time.time())
        plan = self.active(now=current)
        if plan is None:
            return None
        plan.attempts += 1
        if autonomous:
            plan.autonomous_attempts += 1
        plan.last_strategy = str(mode or "")
        plan.strategy_counts[plan.last_strategy] = plan.strategy_counts.get(plan.last_strategy, 0) + 1
        plan.updated_at = current

        # Cùng strategy lặp mãi không phải plan, chỉ là loop có JSON. Khi một mode
        # bị dùng >=3 lần, projection sẽ tự hạ mode đó thông qua strategy_fatigue.
        if plan.attempts >= 8 and plan.progress < 0.18:
            plan.status = "simmering"
            plan.last_progress_reason = "many_attempts_low_evidence_of_progress"
        return plan

    def note_user_outcome(self, outcome: str, *, now: float | None = None) -> RelationalPlan | None:
        current = float(now or time.time())
        plan = self.active(now=current)
        if plan is None:
            return None
        label = str(outcome or "").strip().lower()
        if label in {"positive", "laugh", "delighted", "lighter", "engaged_positive"}:
            plan.progress = _clamp01(plan.progress + 0.30)
            plan.last_progress_reason = f"explicit_outcome:{label}"
        elif label in {"annoyed", "negative", "boundary", "stop"}:
            plan.status = "abandoned"
            plan.last_progress_reason = f"negative_outcome:{label}"
        elif label in {"ignored", "no_response"}:
            # Không coi im lặng là rejection. Chỉ làm plan chậm lại và ưu tiên silence.
            plan.status = "simmering" if plan.autonomous_attempts >= 2 else plan.status
            plan.last_progress_reason = "no_response_not_failure"
        plan.updated_at = current
        if plan.progress >= 0.78:
            plan.status = "completed"
        return plan

    def active(self, *, now: float | None = None) -> RelationalPlan | None:
        current = float(now or time.time())
        for plan in reversed(self.plans):
            if plan.status not in {"active", "simmering"}:
                continue
            if plan.expires_at > 0 and current >= plan.expires_at:
                plan.status = "completed" if plan.progress >= 0.45 else "abandoned"
                plan.updated_at = current
                plan.last_progress_reason = "plan_timeout"
                continue
            return plan
        return None

    def projection(self, *, now: float | None = None) -> dict[str, Any]:
        plan = self.active(now=now)
        if plan is None:
            return {"schema": PLAN_SCHEMA, "active": False}
        fatigue = {
            mode: min(1.0, count / 3.0) for mode, count in plan.strategy_counts.items()
        }
        preferred = [m for m in plan.preferred_modes if fatigue.get(m, 0.0) < 0.80]
        if plan.status == "simmering":
            preferred = [MODE_SILENCE, MODE_SCREEN_REACT, MODE_MUTTER, *preferred]
        return {
            **plan.to_dict(),
            "active": True,
            "strategy_fatigue": {k: round(v, 3) for k, v in fatigue.items()},
            "effective_preferred_modes": list(dict.fromkeys(preferred))[:10],
            "rule": (
                "Plan spans multiple turns. Do not ask for progress after every action. "
                "Silence is a valid strategy; most ambient actions should require no reply."
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"schema": PLAN_SCHEMA, "plans": [p.to_dict() for p in self.plans[-12:]]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None, *, logger: Any = None) -> "RelationalPlanManager":
        if not isinstance(data, Mapping):
            return cls(logger=logger)
        plans = [RelationalPlan.from_dict(row) for row in (data.get("plans", ()) or ()) if isinstance(row, Mapping)]
        return cls(plans, logger=logger)

    def _upsert(
        self,
        *,
        goal_kind: str,
        objective: str,
        preferred: Sequence[str],
        avoid: Sequence[str],
        source_ref: str,
        current: float,
        ttl: float,
    ) -> RelationalPlan:
        existing = next((p for p in reversed(self.plans) if p.goal_kind == goal_kind and p.status in {"active", "simmering"}), None)
        if existing is not None:
            existing.status = "active"
            existing.updated_at = current
            existing.expires_at = max(existing.expires_at, current + ttl)
            existing.source_ref = source_ref or existing.source_ref
            return existing
        plan = RelationalPlan(
            plan_id=f"relplan:{goal_kind}:{int(current * 1000)}",
            goal_kind=goal_kind,
            objective=objective,
            source_ref=source_ref,
            created_at=current,
            updated_at=current,
            expires_at=current + ttl,
            preferred_modes=tuple(preferred),
            avoid_modes=tuple(avoid),
            response_policy="mostly_none",
        )
        self.plans.append(plan)
        self.plans = self.plans[-12:]
        return plan

    @staticmethod
    def _observe_explicit_positive_signal(plan: RelationalPlan, text: str) -> None:
        lowered = text.casefold()
        markers = ("haha", "hahaha", "kkk", "buồn cười", "vui rồi", "đỡ rồi", "cười", "lol")
        if any(marker in lowered for marker in markers):
            plan.progress = _clamp01(plan.progress + 0.34)
            plan.last_progress_reason = "explicit_positive_user_text"
            if plan.progress >= 0.78:
                plan.status = "completed"


__all__ = ["PLAN_SCHEMA", "RelationalPlan", "RelationalPlanManager"]

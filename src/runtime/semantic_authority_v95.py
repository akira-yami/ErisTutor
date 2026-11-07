# -*- coding: utf-8 -*-
"""V95 - Semantic Authority Cleanup cho autonomous decision.

Tầng này *không* chọn câu nói và cũng không thay Neuro bằng một policy engine mới.
Nó chỉ làm một việc mà V94 chưa làm được: xác nhận motive còn hợp lệ về lifecycle
trước khi fast/slow semantic competition bắt đầu.

Authority order của V95:
1. Lifecycle validator: motive chết/stale không được vào competition.
2. Semantic critic: được quyền DEFER speech khi chính motive/evidence quá yếu.
3. Neuro: chọn action trong tập còn hợp lệ.
4. Surface: chỉ wording, tuyệt đối không hồi sinh motive đã bị loại.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from cortex.subject.contracts import SubjectDeliberationFrame


def _compact(value: Any, limit: int = 420) -> str:
    return " ".join(str(value or "").split())[: max(16, int(limit))]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _runtime_context(frame: SubjectDeliberationFrame) -> dict[str, Any]:
    observation = getattr(frame, "observation", None)
    context = _mapping(getattr(observation, "context", {}))
    return _mapping(context.get("runtime_context"))


@dataclass(frozen=True, slots=True)
class MotiveRejection:
    coalition_id: str
    proposal_ref: str
    speech_act: str
    code: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class MotiveValidationResult:
    frame: SubjectDeliberationFrame
    rejections: tuple[MotiveRejection, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.rejections)


def _resume_goal_rejection(
    frame: SubjectDeliberationFrame,
    coalition: Any,
) -> MotiveRejection | None:
    """Kiểm ``resume_shared_goal`` bằng state typed, không đoán từ câu chữ.

    Compatibility: nếu một adapter/test cũ hoàn toàn không đưa lifecycle context
    vào frame thì V95 không tự bịa trạng thái để reject. Production V94 luôn đưa
    ``initiative_runtime_context`` nên đường thật vẫn được kiểm cứng.
    """
    args = _mapping(getattr(coalition, "arguments", {}))
    speech_act = str(args.get("speech_act", "") or "").strip().lower()
    if speech_act != "resume_shared_goal":
        return None

    runtime = _runtime_context(frame)
    initiative = _mapping(runtime.get("initiative_runtime_context"))

    has_typed_goal_state = any(
        key in initiative
        for key in ("active_goal_state", "active_goal_owner", "active_goal_text", "active_goal_id")
    )
    if has_typed_goal_state:
        state = str(initiative.get("active_goal_state", "") or "").strip().lower()
        owner = str(initiative.get("active_goal_owner", "unknown") or "unknown").strip().lower()
        text = _compact(initiative.get("active_goal_text", ""), 520)
        if state not in {"active", "legacy_active"} or not text:
            return MotiveRejection(
                coalition_id=str(getattr(coalition, "coalition_id", "") or ""),
                proposal_ref=str(getattr(coalition, "primary_proposal_ref", "") or ""),
                speech_act=speech_act,
                code="resume_goal_not_active",
                detail=f"state={state or 'none'} text={bool(text)}",
            )
        if owner not in {"user", "shared"}:
            return MotiveRejection(
                coalition_id=str(getattr(coalition, "coalition_id", "") or ""),
                proposal_ref=str(getattr(coalition, "primary_proposal_ref", "") or ""),
                speech_act=speech_act,
                code="resume_goal_owner_not_shared",
                detail=f"owner={owner or 'unknown'}",
            )
        return None

    # V12: không fallback sang ConversationHandoff để suy một shared-goal debt.
    # Handoff đã trở thành observer thuần. Nếu initiative runtime không có typed
    # lifecycle state thì validator fail-open và RootAgent sẽ tự đánh giá event.
    return None


def validate_autonomous_motives(frame: SubjectDeliberationFrame) -> MotiveValidationResult:
    """Loại motive lifecycle-invalid trước Neuro/semantic critic.

    Chỉ SPEAK motive bị loại; SILENCE/BODY_ONLY luôn được giữ làm đường thoát an
    toàn. Nếu validator vô tình loại hết coalition (adapter dị thường), giữ frame
    gốc thay vì tạo frame bất hợp lệ — availability vẫn tốt hơn một crash.
    """
    observation = getattr(frame, "observation", None)
    if str(getattr(observation, "kind", "") or "") == "user_turn":
        return MotiveValidationResult(frame=frame)

    kept: list[Any] = []
    rejected: list[MotiveRejection] = []
    for coalition in tuple(getattr(frame, "coalitions", ()) or ()):
        action = str(getattr(coalition, "action_kind", "") or "").strip().lower()
        if action != "speak":
            kept.append(coalition)
            continue
        rejection = _resume_goal_rejection(frame, coalition)
        if rejection is not None:
            rejected.append(rejection)
            continue
        kept.append(coalition)

    if not rejected or not kept:
        return MotiveValidationResult(frame=frame, rejections=tuple(rejected) if kept else ())
    return MotiveValidationResult(
        frame=replace(frame, coalitions=tuple(kept)),
        rejections=tuple(rejected),
    )


__all__ = [
    "MotiveRejection",
    "MotiveValidationResult",
    "validate_autonomous_motives",
]

from __future__ import annotations

"""Renderer public duy nhất cho action discourse deterministic.

Resolver không biết tiếng Việt hay animation. Module này chỉ nhận action đã giải
đúng target và tạo text fallback/exact. Orchestrator sẽ bọc text thành Decision.
"""

from dataclasses import dataclass

from runtime.discourse_state import DiscourseAction, DiscourseActionKind


@dataclass(frozen=True, slots=True)
class DiscourseSurface:
    text: str = ""
    intent: str = "typed_discourse"
    performance_hint: str = "focused"
    deterministic: bool = False


class DiscourseSurfaceRenderer:
    def render(self, action: DiscourseAction, *, model_text: str = "") -> DiscourseSurface:
        target = action.proposition_root_turn or action.target_turn
        if target is None or action.confidence < 0.55:
            return DiscourseSurface()
        quoted = target.text.strip()
        if not quoted:
            return DiscourseSurface()

        # Exact repeat là thao tác dữ liệu nên luôn thắng wording của model. Model
        # không được paraphrase một câu mà user yêu cầu nhắc nguyên văn.
        if action.kind is DiscourseActionKind.REPEAT_TURN:
            prefix = "Bạn vừa nói" if target.role == "user" else "Tôi vừa nói"
            return DiscourseSurface(
                text=f"{prefix}: “{quoted}”.",
                intent="recent_turn_recall",
                deterministic=True,
            )
        if str(model_text or "").strip():
            return DiscourseSurface()
        if action.kind is DiscourseActionKind.CONFIRM_REFERENCE:
            return DiscourseSurface(
                text=f"Có. Tôi đang xác nhận điều này: “{quoted}”.",
                intent="discourse_confirm",
                performance_hint="soft_warm",
            )
        if action.kind is DiscourseActionKind.CLARIFY_REFERENCE:
            return DiscourseSurface(
                text=f"Ý tôi đang nói tới là: “{quoted}”.",
                intent="discourse_clarify",
            )
        if action.kind is DiscourseActionKind.REPAIR_REFERENCE:
            return DiscourseSurface(
                text=f"Tôi vừa nói chưa rõ. Phần cần sửa là: “{quoted}”.",
                intent="discourse_repair",
                performance_hint="repair",
            )
        return DiscourseSurface()


__all__ = ["DiscourseSurface", "DiscourseSurfaceRenderer"]

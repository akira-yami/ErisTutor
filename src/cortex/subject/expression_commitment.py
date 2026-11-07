# -*- coding: utf-8 -*-
"""Contract native cho quyền biểu đạt của ERIS.

Triết lý của module này cố ý rất hẹp:

* Một ý định có nội dung do chính ERIS hình thành có thể trở thành
  ``ExpressionCommitment``. Đây là quyết định *biểu đạt cái gì*, không phải giấy
  phép do user cấp.
* User chỉ sở hữu boundary rõ ràng của user và quyền giữ lượt đang nói. Im lặng,
  ít phản hồi, độ thân thấp hay "có vẻ bận" không tự biến thành boundary.
* Pacing là self-regulation của ERIS. Nó chỉ DEFER một commitment, không DROP và
  không diễn giải thành "user không muốn nghe".
* Sau khi một frame có commitment đang hoạt động, Neuro chỉ cạnh tranh giữa các
  candidate SPEAK thuộc commitment đó. SILENCE/BODY_ONLY vẫn tồn tại cho những
  frame chưa có commitment hoặc đang gặp hard boundary.

Module không giữ state toàn cục. Lifecycle thật vẫn thuộc các store hiện có
(ExpressionUrgeRuntime / Subject initiative). Nhờ vậy không tạo thêm một "não"
song song chỉ để nhớ một quyết định mà runtime vốn đã biết.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Sequence


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


@dataclass(frozen=True, slots=True)
class ExpressionCommitment:
    """Projection bất biến của một ý định biểu đạt đã có semantic identity."""

    commitment_id: str
    origin: str
    source_id: str
    semantic_target: str
    motive: str = "express"
    pressure: float = 0.0
    freshness: float = 0.0
    persistence: float = 0.0
    forced: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.expression_commitment.v67",
            "commitment_id": self.commitment_id,
            "origin": self.origin,
            "source_id": self.source_id,
            "semantic_target": self.semantic_target,
            "motive": self.motive,
            "pressure": round(clamp01(self.pressure), 4),
            "freshness": round(clamp01(self.freshness), 4),
            "persistence": round(clamp01(self.persistence), 4),
            "forced": bool(self.forced),
        }


@dataclass(frozen=True, slots=True)
class ExpressionRhythmDecision:
    """Kết quả pacing tự thân. ``allow=False`` luôn mang nghĩa DEFER."""

    allow: bool
    reason: str
    min_gap_seconds: float
    seconds_since_last_expression: float
    streak: int


def commitment_payload(
    *,
    origin: str,
    source_id: str,
    semantic_target: str,
    motive: str = "express",
    pressure: float = 0.0,
    freshness: float = 0.0,
    persistence: float = 0.0,
    forced: bool = False,
) -> dict[str, Any]:
    """Tạo payload ổn định để truyền xuyên affordance -> proposal -> Neuro."""

    origin_name = str(origin or "self").strip().lower() or "self"
    source = str(source_id or "").strip()
    target = " ".join(str(semantic_target or "").strip().split())[:1200]
    motive_name = str(motive or "express").strip().lower() or "express"
    # Không dùng ``hash()`` của Python vì nó random theo process. Commitment ID
    # phải replay/diff được giữa các lần chạy.
    target_digest = hashlib.sha1(target.encode("utf-8", errors="ignore")).hexdigest()[:16]
    commitment_id = f"{origin_name}:{source}" if source else f"{origin_name}:{target_digest}"
    return ExpressionCommitment(
        commitment_id=commitment_id,
        origin=origin_name,
        source_id=source,
        semantic_target=target,
        motive=motive_name,
        pressure=clamp01(pressure),
        freshness=clamp01(freshness),
        persistence=clamp01(persistence),
        forced=bool(forced),
    ).to_dict()


def read_commitment(arguments: Mapping[str, Any] | None) -> ExpressionCommitment | None:
    raw = (arguments or {}).get("expression_commitment")
    if not isinstance(raw, Mapping):
        return None
    commitment_id = str(raw.get("commitment_id") or "").strip()
    target = " ".join(str(raw.get("semantic_target") or "").strip().split())[:1200]
    if not commitment_id or not target:
        return None
    return ExpressionCommitment(
        commitment_id=commitment_id,
        origin=str(raw.get("origin") or "self").strip().lower() or "self",
        source_id=str(raw.get("source_id") or "").strip(),
        semantic_target=target,
        motive=str(raw.get("motive") or "express").strip().lower() or "express",
        pressure=clamp01(raw.get("pressure", 0.0)),
        freshness=clamp01(raw.get("freshness", 0.0)),
        persistence=clamp01(raw.get("persistence", 0.0)),
        forced=bool(raw.get("forced", False)),
    )


def is_committed_speech(candidate: Any) -> bool:
    action = str(getattr(candidate, "action_kind", "") or "").strip().lower()
    if action != "speak":
        return False
    arguments = getattr(candidate, "arguments", {})
    args = arguments if isinstance(arguments, Mapping) else {}
    # V67: chỉ typed ExpressionCommitment mới có authority. Không còn suy luận
    # commitment từ expression_urge_id/intention_id vì đó là hai representation
    # khác nhau của lifecycle, không phải quyết định public expression.
    return read_commitment(args) is not None


def hard_expression_boundary(runtime_context: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Chỉ nhận boundary/collision có evidence trực tiếp.

    Không đọc ``low_familiarity``, ``user_response_rate``, ``user_busy``,
    ``reply_expected`` hay generic ``interruption_cost``. Những tín hiệu đó có
    thể hữu ích cho wording/nhịp hội thoại, nhưng không sở hữu quyền phủ quyết.
    """

    ctx = runtime_context or {}
    checks = (
        ("explicit_quiet_request", "explicit_quiet_request"),
        ("user_requested_quiet", "user_requested_quiet"),
        # V74 Courtesy Contract: sau một câu hỏi cần phản hồi, quyền giữ lượt
        # thuộc về user cho đến khi có user turn mới (hoặc timeout contract).
        # Đây là hard boundary có state typed, khác hẳn heuristic reply_expected.
        ("courtesy_hold", "awaiting_user_answer"),
        ("awaiting_user_answer", "awaiting_user_answer"),
        ("conversation_closed", "conversation_closed"),
        ("shutdown_requested", "shutdown_requested"),
        ("user_speaking", "user_speaking"),
        ("voice_floor_owned_by_user", "voice_floor_owned_by_user"),
        ("active_user_turn", "active_user_turn"),
        ("public_turn_busy", "public_turn_busy"),
        ("chat_mode", "chat_mode"),
        ("interaction_busy", "interaction_busy"),
    )
    for key, reason in checks:
        if bool(ctx.get(key, False)):
            return True, reason
    return False, ""


def expression_rhythm(runtime_context: Mapping[str, Any] | None, *, force: bool = False) -> ExpressionRhythmDecision:
    """Pacing thuần self-regulation, không phụ thuộc user có trả lời hay không.

    Quy tắc cố ý nhỏ và dễ replay:
    - 5.5s là khoảng thở tối thiểu giữa hai autonomous expression;
    - streak >= 3 cần 18s;
    - streak >= 5 cần 35s.

    Streak tự decay ở PresenceController khi khoảng cách đủ dài. User reply không
    "nạp quyền nói" cho ERIS và cũng không phải điều kiện để budget hồi lại.
    """

    ctx = runtime_context or {}
    seconds = max(0.0, float(ctx.get("seconds_since_last_autonomous_speech", 10_000.0) or 0.0))
    streak = max(0, int(ctx.get("autonomous_expression_streak", 0) or 0))
    if force:
        return ExpressionRhythmDecision(True, "force_expression", 0.0, seconds, streak)
    if streak >= 5:
        gap = 35.0
    elif streak >= 3:
        gap = 18.0
    else:
        gap = 5.5
    return ExpressionRhythmDecision(seconds >= gap, "self_rhythm_open" if seconds >= gap else "self_rhythm_defer", gap, seconds, streak)


def committed_indices(coalitions: Sequence[Any]) -> tuple[int, ...]:
    return tuple(index for index, candidate in enumerate(tuple(coalitions or ())) if is_committed_speech(candidate))


__all__ = [
    "ExpressionCommitment",
    "ExpressionRhythmDecision",
    "commitment_payload",
    "read_commitment",
    "is_committed_speech",
    "hard_expression_boundary",
    "expression_rhythm",
    "committed_indices",
]

# -*- coding: utf-8 -*-
"""Owner native của commitment biểu đạt trong Subject Core (V67).

Module này cố ý không viết câu trả lời và không gọi model. Nó chỉ trả một trong
ba lựa chọn có nghĩa hành vi rõ ràng:

- SPEAK: ý định có nội dung, đủ quan trọng và thời điểm phù hợp để đưa vào vòng
  cạnh tranh của Executive/Neuro.
- WAIT: ý định vẫn đáng giữ nhưng đang vướng turn collision hoặc self-rhythm.
- DROP: ý định đã yếu/cũ tới mức nên bỏ thay vì biến thành lời nhắc dai dẳng.

Điểm quan trọng V67: một intention đã có nội dung là quyền biểu đạt của Eris.
Arbiter chỉ DEFER vì boundary thật hoặc nhịp tự thân; im lặng/ít phản hồi của user
không được chuyển lậu thành lệnh im.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from cortex.subject.expression_commitment import expression_rhythm, hard_expression_boundary


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


class InitiativeControlAction(StrEnum):
    SPEAK = "speak"
    WAIT = "wait"
    DROP = "drop"


@dataclass(frozen=True, slots=True)
class InitiativeControlConfig:
    """Ngưỡng chỉ dành cho *lifecycle* của intention, không cấp quyền nói.

    V67 bỏ toàn bộ speak-threshold user-centric. Một intention đã contentful thì
    mặc định được SPEAK; WAIT chỉ đến từ turn collision, self-rhythm hoặc một
    restraint thực sự thuộc về Eris.
    """

    drop_after_seconds: float = 12.0 * 60.0
    drop_pressure: float = 0.24
    drop_commitment: float = 0.34


@dataclass(frozen=True, slots=True)
class InitiativeControlDecision:
    action: InitiativeControlAction
    score: float
    reason: str
    intention_id: str
    motive_kind: str
    pressure: float
    commitment: float
    contentful: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.initiative_control.v67",
            "action": self.action.value,
            "score": round(self.score, 4),
            "reason": self.reason,
            "intention_id": self.intention_id,
            "motive_kind": self.motive_kind,
            "pressure": round(self.pressure, 4),
            "commitment": round(self.commitment, 4),
            "contentful": self.contentful,
        }


class InitiativeArbiter:
    """Owner duy nhất của SPEAK/WAIT/DROP trước khi commitment vào Neuro.

    Arbiter không đoán ý user từ im lặng. Nó chỉ giữ lượt khi có boundary/collision
    trực tiếp và điều tiết nhịp của chính Eris.
    """

    _SOCIAL_MOTIVES = {"understand_user", "connect", "share", "seek_recognition"}
    _PERSISTENCE_MOTIVES = {"contribute", "resolve", "be_understood", "demonstrate"}
    _EXISTENTIAL_TARGETS = {"mutual_understanding", "relationship_continuity", "self_expression"}
    _ENVIRONMENT_TARGETS = {"environment_shared_task"}

    def __init__(self, config: InitiativeControlConfig | None = None) -> None:
        self.config = config or InitiativeControlConfig()

    def decide(
        self,
        intention: Mapping[str, Any],
        *,
        relational: Mapping[str, Any] | None,
        runtime_context: Mapping[str, Any] | None,
        now: float,
        contentful: bool,
        interaction_busy: bool = False,
    ) -> InitiativeControlDecision:
        rel = relational or {}
        runtime = runtime_context or {}
        intention_id = str(intention.get("intention_id") or "")
        motive = str(intention.get("motive_kind") or "").strip().lower()
        target = str(intention.get("target") or "").strip().lower()
        pressure = _clamp01(intention.get("pressure", 0.0))
        commitment = _clamp01(intention.get("commitment_strength", 0.0))
        created_at = float(intention.get("created_at", now) or now)
        age = max(0.0, float(now) - created_at)

        def result(action: InitiativeControlAction, score: float, reason: str) -> InitiativeControlDecision:
            return InitiativeControlDecision(
                action=action,
                score=_clamp01(score),
                reason=reason,
                intention_id=intention_id,
                motive_kind=motive,
                pressure=pressure,
                commitment=commitment,
                contentful=bool(contentful),
            )

        # Hard boundary / turn collision có evidence trực tiếp. User-side scene
        # metadata như reply_expected/user_busy không thuộc API authority này.
        runtime_with_call = dict(runtime)
        # ``reply_expected`` chỉ mô tả Eris từng mở một câu hỏi; user im lặng
        # không đồng nghĩa họ đang giữ floor. Chỉ processing/collision trực tiếp
        # mới được nâng thành hard boundary.
        runtime_with_call["interaction_busy"] = bool(interaction_busy or runtime.get("interaction_busy", False))
        blocked, boundary_reason = hard_expression_boundary(runtime_with_call)
        if blocked:
            return result(InitiativeControlAction.WAIT, 0.0, f"hard_boundary:{boundary_reason}")

        # Ý định cũ và yếu nên tự tan. Đây là restraint của Eris, không liên quan
        # chuyện user có phản hồi hay không.
        if (
            age >= self.config.drop_after_seconds
            and pressure <= self.config.drop_pressure
            and commitment <= self.config.drop_commitment
        ):
            return result(InitiativeControlAction.DROP, 0.0, "stale_low_value_intention")

        # Không có semantic object thì chưa có gì để public hóa. WAIT giữ motive
        # sống để cognition có thể kết tinh thành nội dung sau.
        if not contentful:
            return result(InitiativeControlAction.WAIT, 0.0, "no_contentful_object")

        # Nỗi sợ abandonment không được tự ép thành speech. Đây là self-restraint
        # hợp lệ vì nguồn của nó nằm trong Eris, không phải suy đoán user khó chịu.
        if target in self._EXISTENTIAL_TARGETS and _clamp01(runtime.get("existential_abandonment_pressure", 0.0)) >= 0.72:
            return result(InitiativeControlAction.WAIT, 0.0, "self_restraint:abandonment_pressure")

        rhythm = expression_rhythm(
            runtime,
            force=bool(runtime.get("force_speak", False) or runtime.get("force_expression", False)),
        )
        if not rhythm.allow:
            # WAIT ở đây chỉ là DEFER theo nhịp biểu đạt của chính Eris.
            return result(
                InitiativeControlAction.WAIT,
                0.0,
                f"self_rhythm:gap={rhythm.min_gap_seconds:.1f}s:streak={rhythm.streak}",
            )

        # Score từ đây chỉ dùng observability/ranking, KHÔNG còn là ngưỡng cấp
        # quyền nói. Nó cố ý dựa vào state của Eris thay vì response-rate/cooldown.
        freshness = _clamp01(1.0 - age / max(60.0, self.config.drop_after_seconds * 1.8))
        curiosity = _clamp01(rel.get("social_curiosity", 0.0))
        existential_actionability = _clamp01(runtime.get("existential_actionability", 0.0))
        environment_actionability = _clamp01(runtime.get("environment_meaning_actionability", 0.0))
        persistence_bonus = 1.0 if motive in self._PERSISTENCE_MOTIVES else 0.0
        score = _clamp01(
            0.38 * pressure
            + 0.30 * commitment
            + 0.12 * freshness
            + 0.08 * curiosity
            + 0.07 * persistence_bonus
            + (0.10 * existential_actionability if target in self._EXISTENTIAL_TARGETS else 0.0)
            + (0.14 * environment_actionability if target in self._ENVIRONMENT_TARGETS else 0.0)
        )
        return result(InitiativeControlAction.SPEAK, score, "self_owned_expression")



__all__ = [
    "InitiativeArbiter",
    "InitiativeControlAction",
    "InitiativeControlConfig",
    "InitiativeControlDecision",
]

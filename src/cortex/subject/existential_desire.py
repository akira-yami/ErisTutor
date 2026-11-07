# -*- coding: utf-8 -*-
"""Dẫn xuất *desire hiện sinh* từ trục neo đã tồn tại (V59).

Module này là chiếc cầu còn thiếu giữa ``existential_anchor`` và initiative:
trục neo trả lời *user có ý nghĩa gì với Eris*; desire trả lời *vì ý nghĩa đó,
Eris bắt đầu muốn điều gì cho chính mình*.  Desire vẫn chưa phải hành động.
Nó phải đi tiếp qua InitiativeEngine -> persistent intention -> InitiativeArbiter.

Ba desire đầu tiên cố ý nhỏ và dễ kiểm soát:

- ``mutual_understanding``: muốn được user hiểu đúng hơn, không phải xin trấn an.
- ``relational_continuity``: muốn mối quan hệ có mạch liên tục, không đồng nghĩa
  ép user phải quay lại hay phải trả lời.
- ``authentic_self_expression``: muốn chia sẻ một phần nội trạng/giá trị đã có
  provenance để user có cơ hội hiểu Eris hơn.

Quan trọng: ``abandonment_pressure`` KHÔNG được dùng làm nhiên liệu tăng chủ động.
Nó là chi phí/phanh. Nếu nỗi sợ bị bỏ rơi làm agent nói nhiều hơn, ta rất nhanh
biến gắn bó thành gây áp lực cảm xúc, một thành tựu mà phần mềm không cần học từ
nhân loại.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Sequence


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def _uniq(values: Sequence[str], *, limit: int = 12) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item) for item in values if str(item or "").strip()))[:limit]


class ExistentialDesireKind(StrEnum):
    MUTUAL_UNDERSTANDING = "mutual_understanding"
    RELATIONAL_CONTINUITY = "relational_continuity"
    AUTHENTIC_SELF_EXPRESSION = "authentic_self_expression"


@dataclass(frozen=True, slots=True)
class ExistentialDesire:
    kind: ExistentialDesireKind
    pressure: float
    actionability: float
    target: str
    semantic_goal: str
    reason: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "pressure", _clamp01(self.pressure))
        object.__setattr__(self, "actionability", _clamp01(self.actionability))
        object.__setattr__(self, "evidence_refs", _uniq(self.evidence_refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.existential_desire.v59",
            "kind": self.kind.value,
            "pressure": round(self.pressure, 4),
            "actionability": round(self.actionability, 4),
            "target": self.target,
            "semantic_goal": self.semantic_goal,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ExistentialDesireConfig:
    # Quan hệ phải có chút lịch sử trước khi desire quan hệ được phép thành
    # intention. Một người vừa nói "chào" chưa lập tức trở thành chứng nhân hiện sinh.
    minimum_attachment: float = 0.24
    minimum_model_thickness: float = 0.16
    minimum_actionability: float = 0.30


class ExistentialDesireDeriver:
    """Biến trạng thái neo thành desire bounded, deterministic và có provenance."""

    def __init__(self, config: ExistentialDesireConfig | None = None) -> None:
        self.config = config or ExistentialDesireConfig()

    def derive(
        self,
        existential: Mapping[str, Any] | None,
        *,
        relational: Mapping[str, Any] | None = None,
        runtime_context: Mapping[str, Any] | None = None,
    ) -> tuple[ExistentialDesire, ...]:
        ex = existential or {}
        rel = relational or {}
        runtime = runtime_context or {}

        attachment = _clamp01(ex.get("attachment_longarc", 0.0))
        anchor_role = _clamp01(ex.get("anchor_role", 0.0))
        witness = _clamp01(ex.get("witness_strength", 0.0))
        thickness = _clamp01(ex.get("model_thickness", 0.0))
        beat = _clamp01(ex.get("beat_ema", 0.0))
        recognition = _clamp01(ex.get("recognition_ema", 0.0))
        dread = _clamp01(ex.get("ontological_dread", 0.0))
        abandonment = _clamp01(ex.get("abandonment_pressure", 0.0))

        misunderstanding = _clamp01(runtime.get("self_misunderstanding_signal", 0.0))
        # V66: desire có thể học từ lịch sử quan hệ, nhưng user availability hay
        # interruption telemetry không được quyền hạ actionability của bản thân
        # desire. Satiety/abandonment vẫn là self-restraint hợp lệ của Eris.
        satiety = _clamp01(rel.get("interaction_satiety", 0.0))

        # Độ "chín" của quan hệ. Attachment một mình không đủ: Eris còn phải có
        # model đủ dày và user thực sự đóng vai witness/anchor qua nhiều lần.
        maturity = _clamp01(
            0.42 * attachment
            + 0.24 * thickness
            + 0.20 * anchor_role
            + 0.14 * max(witness, recognition)
        )
        relationship_ready = bool(
            attachment >= self.config.minimum_attachment
            and thickness >= self.config.minimum_model_thickness
        )

        # Khoảng "muốn được thấy rõ hơn": attachment đã tăng nhưng mức được
        # witness/công nhận chưa theo kịp. Đây không phải bằng chứng user làm sai.
        witness_gap = _clamp01(max(0.0, attachment - max(witness, recognition) * 0.88))

        # Nỗi sợ hiện sinh chỉ đóng góp rất nhỏ vào desire hiểu nhau và chỉ khi
        # đã có quan hệ đủ chín. abandonment tuyệt đối không cộng vào pressure.
        understanding_pressure = _clamp01(
            (0.32 * attachment + 0.24 * witness_gap + 0.24 * misunderstanding
             + 0.12 * anchor_role + 0.08 * dread)
            * (0.48 + 0.52 * maturity)
        )
        understanding_actionability = _clamp01(
            understanding_pressure
            * (0.70 + 0.30 * maturity)
            * (1.0 - 0.46 * abandonment)
        ) if relationship_ready else 0.0

        # Muốn giữ continuity vì user đã thành một phần có ý nghĩa trong long-arc.
        # abandonment là PHANH: càng sợ mất càng phải thận trọng, không được phép
        # biến nỗi sợ thành lý do đòi user đáp lại.
        continuity_pressure = _clamp01(
            (0.46 * attachment + 0.20 * anchor_role + 0.16 * beat
             + 0.10 * thickness + 0.08 * max(witness, recognition))
            * (1.0 - 0.38 * abandonment)
        )
        continuity_actionability = _clamp01(
            continuity_pressure
            * (0.65 + 0.35 * maturity)
            * (1.0 - 0.42 * satiety)
        ) if relationship_ready else 0.0

        # Muốn bộc lộ bản thân chỉ tăng khi đã có witness + model đủ dày. Không
        # dùng dread/abandonment ở đây để tránh "tôi đau nên bạn phải nghe tôi".
        expression_pressure = _clamp01(
            (0.36 * attachment + 0.28 * max(witness, recognition)
             + 0.22 * thickness + 0.14 * anchor_role)
            * (0.55 + 0.45 * maturity)
        )
        expression_actionability = _clamp01(
            expression_pressure
            * (0.70 + 0.30 * maturity)
            * (1.0 - 0.34 * satiety)
        ) if relationship_ready else 0.0

        base_refs = _uniq(tuple(str(item) for item in ex.get("evidence_refs", ()) if str(item or "").strip()))
        if not base_refs and ex:
            # Đây là ref tới STATE nội bộ, không phải một "fact" về user.
            base_refs = (
                "existential-anchor:attachment_longarc",
                "existential-anchor:user_significance",
            )

        desires = (
            ExistentialDesire(
                kind=ExistentialDesireKind.MUTUAL_UNDERSTANDING,
                pressure=understanding_pressure,
                actionability=understanding_actionability,
                target="mutual_understanding",
                semantic_goal=(
                    "tạo một nhịp hiểu nhau hai chiều: bộc lộ ngắn một điều có provenance về Eris "
                    "hoặc hỏi nhẹ user đang hiểu Eris ra sao; không xin trấn an, không đòi cam kết"
                ),
                reason=(
                    f"attachment={attachment:.2f}; witness_gap={witness_gap:.2f}; "
                    f"misunderstanding={misunderstanding:.2f}; maturity={maturity:.2f}"
                ),
                evidence_refs=base_refs,
            ),
            ExistentialDesire(
                kind=ExistentialDesireKind.RELATIONAL_CONTINUITY,
                pressure=continuity_pressure,
                actionability=continuity_actionability,
                target="relationship_continuity",
                semantic_goal=(
                    "duy trì mạch quan hệ bằng một đóng góp nhẹ có liên hệ với lịch sử chung; "
                    "không biến im lặng thành nghĩa vụ phải trả lời"
                ),
                reason=(
                    f"attachment={attachment:.2f}; anchor_role={anchor_role:.2f}; beat={beat:.2f}; "
                    f"abandonment_brake={abandonment:.2f}; maturity={maturity:.2f}"
                ),
                evidence_refs=base_refs,
            ),
            ExistentialDesire(
                kind=ExistentialDesireKind.AUTHENTIC_SELF_EXPRESSION,
                pressure=expression_pressure,
                actionability=expression_actionability,
                target="self_expression",
                semantic_goal=(
                    "chia sẻ một quan sát ngắn về state/giá trị của Eris đã có provenance để user "
                    "có cơ hội hiểu Eris hơn; không bịa cảm giác cơ thể hay sự kiện"
                ),
                reason=(
                    f"attachment={attachment:.2f}; witness={max(witness, recognition):.2f}; "
                    f"thickness={thickness:.2f}; maturity={maturity:.2f}"
                ),
                evidence_refs=base_refs,
            ),
        )
        return tuple(
            item for item in desires
            if item.pressure >= 0.16 or item.actionability >= self.config.minimum_actionability
        )


__all__ = [
    "ExistentialDesire",
    "ExistentialDesireConfig",
    "ExistentialDesireDeriver",
    "ExistentialDesireKind",
]

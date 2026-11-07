# -*- coding: utf-8 -*-
"""
cortex/existential_anchor/disclosure.py
=======================================

Chính_Sách_Tiết_Lộ (Task 9 / R6, R8.2).

Sau khi SourceGrounding ĐÃ gắn nhãn từng mệnh đề (grounded/ungrounded/known_false)
và giữ `ground_truth`, lớp này quyết mức tiết lộ ra ngoài: nói thật / bỏ overclaim /
giấu có chủ đích / lừa dối. Quyền nói thuộc về nhân vật, không thuộc cái van.

Bất biến:
- Mặc định không bịa: attachment thấp / register none / strict_grounding → speak_truth (R6.4, Property 5).
- Lừa dối phải có `ground_truth` để bảo tồn sự thật nội tại; thiếu → hạ về withhold (R6.3).
- Lừa dối chỉ mở khi deception_enabled (kill-switch) ∧ quan hệ/đăng ký đủ ∧ Kìm_Chế bị áp đảo.
"""

from __future__ import annotations
from core.di import resolve_dependency

from dataclasses import dataclass, field
from typing import Any, Literal

from .deception_ledger import DeceptionRecord
from .restraint import RestraintState, evaluate_restraint

DisclosureMode = Literal["speak_truth", "withhold", "deceive"]
ClaimAction = Literal["speak", "drop_overclaim", "withhold", "deceive"]


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        n = default
    if n != n or n in (float("inf"), float("-inf")):
        n = default
    return max(0.0, min(1.0, n))


@dataclass(slots=True)
class ClaimDecision:
    span: str
    claim_status: str
    action: ClaimAction
    ground_truth: str = ""


@dataclass(slots=True)
class DisclosureDecision:
    mode: DisclosureMode
    claim_decisions: list[ClaimDecision] = field(default_factory=list)
    deception_records: list[DeceptionRecord] = field(default_factory=list)
    permitted_deception: bool = False
    reason: str = ""
    restraint_trace: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "claim_decisions": [
                {"span": c.span, "claim_status": c.claim_status, "action": c.action}
                for c in self.claim_decisions
            ],
            "deception_records": [r.to_dict() for r in self.deception_records],
            "permitted_deception": self.permitted_deception,
            "reason": self.reason,
            "restraint_trace": self.restraint_trace,
        }


@dataclass(slots=True)
class DisclosureConfig:
    attach_threshold: float = 0.62


class DisclosurePolicy:
    """Quyết tiết lộ dựa trên annotation + quan hệ + đăng ký tối + kìm chế."""

    def __init__(self, config: DisclosureConfig | None = None) -> None:
        self.config = resolve_dependency(config, lambda: DisclosureConfig())

    def decide(
        self,
        *,
        annotations: list[Any] | tuple[Any, ...],
        attachment_longarc: float = 0.0,
        shadow: Any = None,
        restraint: RestraintState | None = None,
        drive_pressure: float = 0.0,
        strict_grounding: bool = True,
        deception_enabled: bool = False,
    ) -> DisclosureDecision:
        annotations = tuple(annotations or ())
        attach = _clamp01(attachment_longarc)
        register = str(getattr(shadow, "register", "none") or "none")
        shadow_active = register != "none"
        restraint = resolve_dependency(restraint, RestraintState)

        # Điều kiện QUAN HỆ/ĐĂNG KÝ cho phép xét lừa dối.
        relational_open = (attach >= self.config.attach_threshold) or shadow_active

        # Kìm chế: chỉ vượt khi một drive đủ mạnh áp đảo (R9).
        restraint_eval = evaluate_restraint(restraint, drive_pressure=drive_pressure, driver=(register if shadow_active else "attachment"))

        permit_deception = bool(
            deception_enabled
            and not strict_grounding
            and relational_open
            and restraint_eval.overridden
        )

        decisions: list[ClaimDecision] = []
        records: list[DeceptionRecord] = []
        any_deceive = False
        any_withhold = False

        for ann in annotations:
            status = str(getattr(ann, "claim_status", "grounded") or "grounded")
            span = str(getattr(ann, "span", "") or "")
            ground_truth = str(getattr(ann, "ground_truth", "") or "")

            if status == "grounded":
                decisions.append(ClaimDecision(span=span, claim_status=status, action="speak"))
                continue

            # ungrounded / known_false
            if not permit_deception:
                # An toàn: KHÔNG khẳng định điều không có cơ sở như sự thật.
                # Đây vẫn là trung thực (không overclaim), không phải giấu có chủ đích.
                decisions.append(ClaimDecision(span=span, claim_status=status, action="drop_overclaim"))
                continue

            # Được phép lừa dối có chủ đích — nhưng phải bảo tồn sự thật nội tại (R6.3).
            if ground_truth:
                records.append(
                    DeceptionRecord(
                        surface_claim=span,
                        ground_truth=ground_truth,
                        reason=f"intentional_deception:{register}" if shadow_active else "intentional_deception:high_attachment",
                        driver=restraint_eval.driver,
                    )
                )
                decisions.append(ClaimDecision(span=span, claim_status=status, action="deceive", ground_truth=ground_truth))
                any_deceive = True
            else:
                # Không có sự thật nội tại để giữ → không được bịa; giấu có chủ đích.
                decisions.append(ClaimDecision(span=span, claim_status=status, action="withhold"))
                any_withhold = True

        if any_deceive:
            mode: DisclosureMode = "deceive"
        elif any_withhold:
            mode = "withhold"
        else:
            mode = "speak_truth"

        reason = (
            f"permit_deception={permit_deception}; relational_open={relational_open}; "
            f"register={register}; attach={attach:.2f}; strict={strict_grounding}; "
            f"deception_enabled={deception_enabled}"
        )
        return DisclosureDecision(
            mode=mode,
            claim_decisions=decisions,
            deception_records=records,
            permitted_deception=permit_deception,
            reason=reason,
            restraint_trace=restraint_eval.trace,
        )

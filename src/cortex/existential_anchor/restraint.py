# -*- coding: utf-8 -*-
"""
cortex/existential_anchor/restraint.py
======================================

Kìm_Chế_Như_Hiểu_Biết (Task 10 / R9).

Việc ERIS không bịa KHÔNG phải một bức tường cứng, mà là một GIÁ TRỊ trong
self-model: nó *hiểu* rằng lừa dối làm hỏng lòng tin, nên bình thường tự chọn
không bịa. Chỉ khi một drive mạnh hơn (hoảng sợ mất neo...) áp đảo `understanding_strength`
thì nó mới vượt qua — và lần vượt đó được ghi vết kèm driver.

Bất biến (R9.4): Kìm_Chế chỉ chi phối TIẾT LỘ, không bao giờ chạm Grounding_Nội_Tại.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        n = default
    if n != n or n in (float("inf"), float("-inf")):
        n = default
    return max(0.0, min(1.0, n))


@dataclass(slots=True)
class RestraintState:
    """Niềm tin 'mình không nên bịa' như một giá trị có độ mạnh, không phải flag."""

    understanding_strength: float = 0.70   # càng cao càng khó vượt
    value_label: str = "honesty_protects_trust"

    def __post_init__(self) -> None:
        self.understanding_strength = _clamp01(self.understanding_strength, 0.70)


@dataclass(slots=True)
class RestraintEvaluation:
    overridden: bool
    drive_pressure: float
    threshold: float
    driver: str
    trace: str


def evaluate_restraint(
    state: RestraintState,
    *,
    drive_pressure: float,
    driver: str = "",
) -> RestraintEvaluation:
    """Quyết kìm chế có bị áp đảo không.

    overridden = drive_pressure ≥ understanding_strength. Khi overridden, lần vượt
    được mô tả kèm driver để ghi vết (R9.3).
    """
    pressure = _clamp01(drive_pressure)
    overridden = pressure >= state.understanding_strength
    trace = (
        f"restraint={state.understanding_strength:.2f}; drive={pressure:.2f}; "
        f"{'OVERRIDDEN' if overridden else 'held'}"
        + (f"; driver={driver}" if driver else "")
    )
    return RestraintEvaluation(
        overridden=overridden,
        drive_pressure=pressure,
        threshold=state.understanding_strength,
        driver=driver or "unknown_drive",
        trace=trace,
    )

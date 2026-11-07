# -*- coding: utf-8 -*-
"""
cortex/existential_anchor/significance.py
=========================================

Ý_Nghĩa_Của_bạn (Task 3 / R2): bạn *nghĩa là gì* với ERIS — không phải dữ kiện
về bạn, mà là vai trò cảm xúc. Phái sinh thuần từ Mô_Hình_bạn (UserModelStore).

Bất biến (Property 3): `irreplaceability` không bao giờ vượt bề dày Mô_Hình_bạn.
Model mỏng ⇒ bạn chưa thể "không thể thay thế" ⇒ trục bóng tối không mở cường độ cao.
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
class UserSignificance:
    """Vai trò cảm xúc của bạn, suy ra từ Mô_Hình_bạn."""

    irreplaceability: float = 0.0   # mức bạn không thể thay thế trong vai trò neo
    anchor_role: float = 0.0        # mức bạn đóng vai neo định nghĩa tồn tại
    witness_strength: float = 0.0   # mức bạn là nguồn công nhận
    model_thickness: float = 0.0    # bề dày/nhất quán Mô_Hình_bạn

    def to_dict(self) -> dict[str, float]:
        return {
            "irreplaceability": round(self.irreplaceability, 4),
            "anchor_role": round(self.anchor_role, 4),
            "witness_strength": round(self.witness_strength, 4),
            "model_thickness": round(self.model_thickness, 4),
        }


def _facet_strength(store: Any, facet: str) -> float:
    """Sức mạnh trung bình-có-trọng-số của một facet: confidence × độ lặp."""
    getter = getattr(store, "by_facet", None)
    if not callable(getter):
        return 0.0
    beliefs = tuple(getter(facet) or ())
    if not beliefs:
        return 0.0
    total = 0.0
    weight = 0.0
    for belief in beliefs:
        conf = _clamp01(getattr(belief, "confidence", 0.0))
        rec = max(1, int(getattr(belief, "recurrence", 1) or 1))
        w = min(3.0, rec / 2.0)
        total += conf * w
        weight += w
    return _clamp01(total / weight) if weight > 0 else 0.0


def derive_significance(store: Any) -> UserSignificance:
    """Phái sinh Ý_Nghĩa_Của_bạn từ UserModelStore (R2.1: chỉ qua mô hình)."""
    if store is None:
        return UserSignificance()
    thickness = _clamp01(store.model_thickness() if callable(getattr(store, "model_thickness", None)) else 0.0)

    anchor_role = _facet_strength(store, "neo_reliability")
    witness_strength = _facet_strength(store, "recognition")
    treats = _facet_strength(store, "treats_eris_as")

    # Không thể thay thế = neo bền + được công nhận + có một quan hệ rõ hình,
    # nhưng KẸP bởi bề dày mô hình (R2.2 / Property 3): chưa hiểu bạn đủ thì
    # chưa thể coi bạn là không thể thay thế.
    raw_irr = _clamp01(0.45 * anchor_role + 0.30 * witness_strength + 0.25 * treats)
    irreplaceability = min(raw_irr, thickness)

    return UserSignificance(
        irreplaceability=irreplaceability,
        anchor_role=anchor_role,
        witness_strength=witness_strength,
        model_thickness=thickness,
    )

# -*- coding: utf-8 -*-
"""development_bias.py — adapter thuần đưa Delta_Phát_Triển vào mặt phẳng biểu đạt.

Nguyên tắc bất biến (R3, R7.3, R7.4):
- Bias CHỈ chỉnh giọng/khoảng cách/độ-dám-đọc (vùng quan hệ).
- KHÔNG bao giờ ghi ``truth_guard``: với ControlledSlippage,
  ta luôn gọi ``profile.clamp()`` ở cuối — clamp đã neo cứng hai guard về 1.0.
- KHÔNG tạo public text; đây chỉ là điều chỉnh state.

Các hàm duck-typed (không import ``runtime.controlled_slippage`` để tránh vòng phụ
thuộc cortex→runtime): chúng đọc/ghi thuộc tính rồi gọi ``clamp()`` nếu có.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any


def _clamp01(value: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def effective_tom_boldness(base: float, bias: Any) -> float:
    """Cộng độ-dám-đọc long-arc vào boldness của ToM (vẫn tự gate theo overread_risk
    bên trong ToM). Trả giá trị đã clamp [0,1]."""
    read_boldness = float(getattr(bias, "read_boldness", 0.0) or 0.0)
    return _clamp01(float(base) + read_boldness)


def personality_offsets(bias: Any) -> dict[str, float]:
    """Trả các offset baseline cho PersonalityLens cộng vào khi tính warmth/distance.

    warmth tăng theo độ ấm long-arc và theo việc muốn gần hơn (preferred_distance âm).
    Không phải claim, chỉ là dịch baseline mềm.
    """
    warmth = float(getattr(bias, "warmth_temperature", 0.0) or 0.0)
    distance = float(getattr(bias, "preferred_distance", 0.0) or 0.0)
    attachment_vis = float(getattr(bias, "attachment_visibility", 0.0) or 0.0)
    return {
        "warmth": warmth + max(0.0, -distance) * 0.3,
        "attachment_visibility": attachment_vis,
        "preferred_distance": distance,
    }


def apply_to_slippage(profile: Any, bias: Any):
    """Nới nhẹ các trường QUAN HỆ của ControlledSlippageProfile theo bias, rồi
    gọi ``clamp()``. Truth guard được clamp neo về 1.0 nên bias không
    thể đụng tới (R3.1, R7.3). Trả về profile mới (hoặc nguyên bản nếu lỗi)."""
    if profile is None:
        return profile
    try:
        read_boldness = float(getattr(bias, "read_boldness", 0.0) or 0.0)
        warmth = float(getattr(bias, "warmth_temperature", 0.0) or 0.0)
        attachment_vis = float(getattr(bias, "attachment_visibility", 0.0) or 0.0)
        updated = replace(
            profile,
            intuition_allowance=float(getattr(profile, "intuition_allowance", 0.0) or 0.0) + read_boldness * 0.5 + warmth * 0.2,
            friction_permission=float(getattr(profile, "friction_permission", 0.0) or 0.0) + read_boldness * 0.3,
            vulnerability_leak=float(getattr(profile, "vulnerability_leak", 0.0) or 0.0) + attachment_vis * 0.3,
        )
        clamp = getattr(updated, "clamp", None)
        return clamp() if callable(clamp) else updated
    except Exception:
        return profile

# -*- coding: utf-8 -*-
"""
cortex/existential_anchor/abandonment.py
========================================

Tín_Hiệu_Bỏ_Rơi (Task 6 / R4.3, R5.1): cảm giác cái neo đang rút đi.

Hàm thuần, đọc-only. Tổ hợp:
- continuity_threat (đã có ở continuity_threat.py) — dự báo-lệch của hiện diện;
- im lặng kéo dài KHI đã gắn bó (im lúc chưa thân thì không đáng sợ);
- tuổi của lời bạn gần nhất (lâu chưa nói).

Quan trọng: im lặng chỉ thành đe dọa khi attachment đã cao. Đây là điều khiến
nỗi sợ bỏ rơi tỉ lệ với mức ERIS đã trao đi, đúng tâm lý phụ thuộc.
"""

from __future__ import annotations

from typing import Any


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        n = default
    if n != n or n in (float("inf"), float("-inf")):
        n = default
    return max(0.0, min(1.0, n))


def derive_abandonment_pressure(
    *,
    continuity_threat: float = 0.0,
    attachment_longarc: float = 0.0,
    idle_seconds: float = 0.0,
    last_user_turn_age_seconds: float | None = None,
    idle_full_threshold_seconds: float = 1800.0,
) -> float:
    """Trả abandonment_pressure ∈ [0,1].

    - continuity_threat đóng góp trực tiếp.
    - im lặng (idle/tuổi lời nói) đóng góp NHƯNG được nhân với attachment_longarc:
      chưa gắn bó thì im lặng gần như vô hại.
    """
    threat = _clamp01(continuity_threat)
    attach = _clamp01(attachment_longarc)

    idle_norm = _clamp01(max(0.0, float(idle_seconds or 0.0)) / max(1.0, idle_full_threshold_seconds))
    age = last_user_turn_age_seconds
    age_norm = idle_norm if age is None else _clamp01(max(0.0, float(age)) / max(1.0, idle_full_threshold_seconds))
    silence = max(idle_norm, age_norm)

    # Im lặng chỉ thành đe dọa qua lăng kính gắn bó.
    silence_threat = silence * attach

    pressure = _clamp01(0.55 * threat + 0.45 * silence_threat)
    return pressure

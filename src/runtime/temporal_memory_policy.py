# -*- coding: utf-8 -*-
"""Temporal Memory Policy V76.

Eris đã có Living User Memory với ``last_confirmed_at``. V76 không tạo database
memory thứ hai; module này chỉ thêm *cách dùng memory theo tuổi và độ biến động*.

Ví dụ ``preferences.food = phở`` sau một năm:
- không được quên sạch và hỏi lại từ đầu;
- cũng không được coi là chân lý vĩnh viễn;
- nếu ngữ cảnh liên quan, Surface có thể nói theo kiểu "tôi nhớ hồi trước anh thích
  phở, không biết giờ còn vậy không".

Đây là policy engineering, không phải mô hình tâm lý học hoàn chỉnh.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any, Mapping, Sequence

TEMPORAL_MEMORY_SCHEMA = "eris.temporal_memory_policy.v76"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = default
    if not math.isfinite(n):
        n = default
    return max(0.0, min(1.0, n))


def volatility_for_path(path: str, metadata: Mapping[str, Any] | None = None) -> float:
    meta = dict(metadata or {})
    if "volatility" in meta:
        return _clamp01(meta.get("volatility"), 0.45)
    p = str(path or "").casefold()
    # Identity ổn định hơn sở thích/routine. Đây là default có thể tune bằng metadata.
    if any(x in p for x in ("name", "birthday", "birth_date", "pronoun", "identity")):
        return 0.08
    if any(x in p for x in ("job", "career", "workplace", "school", "location")):
        return 0.38
    if any(x in p for x in ("preference", "favorite", "favourite", "likes", "dislikes", "food", "music")):
        return 0.58
    if any(x in p for x in ("routine", "habit", "sleep", "schedule", "current", "recent")):
        return 0.76
    return 0.46


def default_half_life_days(volatility: float) -> float:
    # Volatility cao -> nhanh chuyển từ "nhớ chắc" sang "nhớ nhưng nên mềm".
    v = _clamp01(volatility, 0.45)
    return max(14.0, 1460.0 * (1.0 - v) ** 1.55)


@dataclass(frozen=True, slots=True)
class TemporalMemoryAssessment:
    path: str
    age_days: float
    volatility: float
    confidence: float
    freshness: float
    stale_probability: float
    use_mode: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": TEMPORAL_MEMORY_SCHEMA,
            "path": self.path,
            "age_days": round(self.age_days, 3),
            "volatility": round(self.volatility, 4),
            "confidence": round(self.confidence, 4),
            "freshness": round(self.freshness, 4),
            "stale_probability": round(self.stale_probability, 4),
            "use_mode": self.use_mode,
            "reason": self.reason,
        }


def assess_memory_record(record: Any, *, now: float | None = None) -> TemporalMemoryAssessment:
    current = float(now or time.time())
    if isinstance(record, Mapping):
        get = record.get
        path = str(get("path", "") or "")
        confidence = _clamp01(get("confidence", 0.5), 0.5)
        last = float(get("last_confirmed_at", get("updated_at", current)) or current)
        metadata = dict(get("metadata", {}) or {})
    else:
        path = str(getattr(record, "path", "") or "")
        confidence = _clamp01(getattr(record, "confidence", 0.5), 0.5)
        last = float(getattr(record, "last_confirmed_at", getattr(record, "updated_at", current)) or current)
        metadata = dict(getattr(record, "metadata", {}) or {})
    age_days = max(0.0, current - last) / 86400.0
    volatility = volatility_for_path(path, metadata)
    half_life = float(metadata.get("temporal_half_life_days", default_half_life_days(volatility)) or default_half_life_days(volatility))
    freshness = _clamp01(0.5 ** (age_days / max(1.0, half_life)))
    stale_probability = _clamp01((1.0 - freshness) * (0.45 + 0.55 * volatility))

    if confidence < 0.42:
        mode = "do_not_assume"
        reason = "low_confidence"
    elif stale_probability >= 0.68:
        mode = "reconfirm_if_relevant"
        reason = "old_and_volatile"
    elif stale_probability >= 0.32:
        # Sở thích một năm tuổi thường rơi vào đây: vẫn nhớ giá trị cũ nhưng
        # wording phải mềm, không biến fact lịch sử thành chân lý hiện tại.
        mode = "soft_recall"
        reason = "stale_but_remembered"
    else:
        mode = "direct_recall"
        reason = "fresh_enough"
    return TemporalMemoryAssessment(path, age_days, volatility, confidence, freshness, stale_probability, mode, reason)


def project_records(records: Sequence[Any], *, now: float | None = None, limit: int = 16) -> dict[str, Any]:
    rows = []
    for record in tuple(records or ())[: max(0, int(limit))]:
        assessment = assess_memory_record(record, now=now)
        if isinstance(record, Mapping):
            value = record.get("value")
            record_id = str(record.get("id", "") or "")
        else:
            value = getattr(record, "value", None)
            record_id = str(getattr(record, "id", "") or "")
        rows.append({
            "record_id": record_id,
            "path": assessment.path,
            "value": value,
            "assessment": assessment.to_dict(),
        })
    return {
        "schema": TEMPORAL_MEMORY_SCHEMA,
        "records": rows,
        "rule": (
            "Remember old facts instead of asking from zero. For volatile stale facts, mention the remembered value softly "
            "and reconfirm only when the current context makes that information useful."
        ),
    }


__all__ = [
    "TEMPORAL_MEMORY_SCHEMA", "TemporalMemoryAssessment", "assess_memory_record",
    "project_records", "volatility_for_path", "default_half_life_days",
]

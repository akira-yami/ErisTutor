# -*- coding: utf-8 -*-
"""Policy bất định cho memory dùng trong câu trả lời.

Embedding chỉ tìm được ứng viên; policy này quyết định memory có được dùng thẳng,
phải nói dè dặt, cần xác nhận hay nên bỏ qua. Mục tiêu là giảm false-memory, thứ
nguy hiểm hơn nhiều so với việc đôi khi ERIS thành thật nói rằng nó không chắc.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .models import ProfileRecord, compact_text

MemoryUseMode = Literal["direct", "hedged", "clarify", "omit", "conflict"]


@dataclass(frozen=True, slots=True)
class MemoryUseDecision:
    path: str
    mode: MemoryUseMode
    confidence: float
    message: str = ""
    record_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class MemoryClarificationPolicy:
    direct_threshold: float = 0.85
    hedged_threshold: float = 0.68
    clarify_threshold: float = 0.50

    def decide(self, records: Iterable[ProfileRecord]) -> MemoryUseDecision:
        rows = tuple(records)
        if not rows:
            return MemoryUseDecision("", "omit", 0.0)
        path = rows[0].path
        active = tuple(x for x in rows if x.status == "active" and x.is_current)
        disputed = tuple(x for x in rows if x.status == "disputed")
        if active and disputed:
            main = max(active, key=lambda x: x.trust_weight)
            alternative = max(disputed, key=lambda x: x.trust_weight)
            return MemoryUseDecision(
                path, "conflict", min(main.trust_weight, alternative.trust_weight),
                f"Thông tin {path} đang có hai khả năng: {compact_text(main.value, 120)} và {compact_text(alternative.value, 120)}. Không tự chọn; hãy xác nhận khi nó ảnh hưởng câu trả lời.",
                tuple(x.id for x in (*active, *disputed)),
            )
        if not active:
            return MemoryUseDecision(path, "omit", 0.0, record_ids=tuple(x.id for x in rows))
        strongest = max(active, key=lambda x: x.trust_weight)
        confidence = strongest.trust_weight
        if strongest.explicitness == "explicit" and confidence >= self.direct_threshold:
            mode: MemoryUseMode = "direct"
        elif confidence >= self.hedged_threshold:
            mode = "hedged"
        elif confidence >= self.clarify_threshold:
            mode = "clarify"
        else:
            mode = "omit"
        message = ""
        if mode == "hedged":
            message = f"Có thể dùng {path} nhưng diễn đạt dè dặt, vì đây chưa phải thông tin explicit đủ mạnh."
        elif mode == "clarify":
            message = f"Không dùng {path} như sự thật. Hỏi xác nhận tự nhiên nếu thông tin này cần thiết."
        return MemoryUseDecision(path, mode, confidence, message, tuple(x.id for x in active))


__all__ = ["MemoryClarificationPolicy", "MemoryUseDecision", "MemoryUseMode"]

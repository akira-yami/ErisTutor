# -*- coding: utf-8 -*-
"""
cortex/tutor/curiosity_gate.py
==============================

Quyết định một Knowledge_Gap có đáng đem đi hỏi gia sư không: chỉ khi rảnh, tôn trọng
rate-limit + trần/giờ, ưu tiên theo stake & prediction error, điều tiết bởi drive nội sinh.
"""

from __future__ import annotations
from core.di import resolve_dependency

from typing import Optional

from .config import TutorConfig
from .contracts import KnowledgeGap


def _clamp01(v: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0.0
    if n != n:
        return 0.0
    return max(0.0, min(1.0, n))


class CuriosityGate:
    """Cổng tò mò. Giữ lịch sử episode để rate-limit và trần/giờ."""

    # Trọng số ưu tiên (dương ⇒ đơn điệu tăng theo stake và prediction error).
    W_STAKE = 0.55
    W_PE = 0.45

    def __init__(self, config: Optional[TutorConfig] = None) -> None:
        self.config = resolve_dependency(config, TutorConfig).normalized()
        self._last_started_ts: float = 0.0
        self._recent_starts: list[float] = []

    def priority(self, gap: KnowledgeGap, *, drive_level: float = 1.0) -> float:
        base = self.W_STAKE * _clamp01(gap.stake_relevance) + self.W_PE * _clamp01(gap.prediction_error)
        drive_factor = 0.3 + 0.7 * _clamp01(drive_level)
        return _clamp01(base) * drive_factor

    def can_start(self, now: float) -> bool:
        cfg = self.config
        if now - self._last_started_ts < cfg.min_seconds_between_episodes:
            return False
        self._prune_hour(now)
        if cfg.max_episodes_per_hour > 0 and len(self._recent_starts) >= cfg.max_episodes_per_hour:
            return False
        return True

    def select(
        self,
        gaps: list[KnowledgeGap],
        *,
        idle: bool,
        now: float,
        drive_level: float = 1.0,
    ) -> Optional[KnowledgeGap]:
        """Chọn gap đáng hỏi nhất, hoặc None."""

        if not idle:
            return None
        if not self.can_start(now):
            return None
        candidates = [g for g in gaps if not g.resolved]
        if not candidates:
            return None
        scored = [(self.priority(g, drive_level=drive_level), g) for g in candidates]
        scored = [(p, g) for (p, g) in scored if p >= self.config.curiosity_threshold]
        if not scored:
            return None
        # Ưu tiên cao nhất; phá hòa bằng updated_ts mới hơn.
        scored.sort(key=lambda item: (item[0], item[1].updated_ts), reverse=True)
        return scored[0][1]

    def select_forced(self, gaps: list[KnowledgeGap]) -> Optional[KnowledgeGap]:
        """Chọn gap 'force' (lệnh trực tiếp / 'không biết') — bỏ qua idle + rate-limit.

        Caller vẫn phải tôn trọng risk_guard (captcha/cooldown) trước khi gọi.
        """

        forced = [g for g in gaps if getattr(g, "force", False) and not g.resolved]
        if not forced:
            return None
        forced.sort(key=lambda g: (self.priority(g, drive_level=1.0), g.updated_ts), reverse=True)
        return forced[0]

    def note_started(self, now: float) -> None:
        self._last_started_ts = now
        self._recent_starts.append(now)
        self._prune_hour(now)

    def _prune_hour(self, now: float) -> None:
        cutoff = now - 3600.0
        self._recent_starts = [t for t in self._recent_starts if t >= cutoff]


__all__ = ["CuriosityGate"]

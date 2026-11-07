# -*- coding: utf-8 -*-
"""
cortex/tutor/share_impulse.py
=============================

Sau khi học, tạo Learning_Highlight (gist diễn đạt lại) và đẩy một Share_Impulse vào
dòng suy nghĩ/agenda nội bộ để vòng autonomous có thể tự nhiên "khoe" bằng giọng ERIS.

KHÔNG phát thẳng ra kênh nói. Có lọc tri thức tầm thường + rate-limit chống khoe liên tục.
"""

from __future__ import annotations
from core.di import resolve_dependency

from typing import Any, Callable, Optional

from .config import TutorConfig
from .contracts import TutorEpisode


# thought_sink: object có .push_share_impulse(highlight, *, priority) hoặc callable(highlight, priority).
# novelty_fn(highlight) -> [0,1]: độ mới so với tri thức đã biết.
NoveltyFn = Callable[[str], float]


class LearningShareImpulse:
    """Tạo + đẩy Share_Impulse có tiết chế."""

    def __init__(
        self,
        config: Optional[TutorConfig] = None,
        *,
        formulator: Any = None,
        thought_sink: Any = None,
        novelty_fn: Optional[NoveltyFn] = None,
        logger: Any = None,
    ) -> None:
        self.config = resolve_dependency(config, TutorConfig).normalized()
        self._formulator = formulator
        self._thought_sink = thought_sink
        self._novelty_fn = novelty_fn
        self._logger = logger
        self._last_share_ts: float = 0.0

    def maybe_emit(self, episode: TutorEpisode, *, now: float) -> bool:
        """Trả True nếu đã đẩy một Share_Impulse."""

        if not self.config.enabled:
            return False
        if self._formulator is None or self._thought_sink is None:
            return False
        # Rate-limit (Req 10.5).
        if now - self._last_share_ts < self.config.share_window_s:
            return False

        try:
            highlight = self._formulator.summarize_learning(episode)
        except Exception:
            highlight = ""
        if not highlight:
            return False

        # Lọc tri thức tầm thường (Req 10.4).
        novelty = self._novelty(highlight)
        stake = float(getattr(episode.gap, "stake_relevance", 0.0) or 0.0)
        if novelty < self.config.share_novelty_threshold and stake < self.config.share_novelty_threshold:
            return False

        priority = max(novelty, stake)
        if not self._push(highlight, priority):
            return False

        self._last_share_ts = now
        episode.shared = True
        self._log(episode, highlight, novelty)
        return True

    # ------------------------------------------------------------------

    def push_remark(self, text: str, *, now: float, priority: float = 0.6) -> bool:
        """Đẩy một câu ngắn (context/banter) vào thought stream. Không lưu trí nhớ.

        Bỏ qua novelty gate (đây không phải tri thức), vẫn tôn trọng enabled + rate-limit.
        """

        if not self.config.enabled:
            return False
        if self._thought_sink is None:
            return False
        text = str(text or "").strip()
        if not text:
            return False
        if now - self._last_share_ts < self.config.share_window_s:
            return False
        if not self._push(text, priority):
            return False
        self._last_share_ts = now
        return True

    def _novelty(self, highlight: str) -> float:
        if self._novelty_fn is None:
            return 1.0  # không có nguồn so sánh ⇒ coi như mới
        try:
            n = float(self._novelty_fn(highlight))
        except Exception:
            return 1.0
        if n != n:
            return 1.0
        return max(0.0, min(1.0, n))

    def _push(self, highlight: str, priority: float) -> bool:
        sink = self._thought_sink
        try:
            fn = getattr(sink, "push_share_impulse", None)
            if callable(fn):
                fn(highlight, priority=priority)
                return True
            if callable(sink):
                sink(highlight, priority)
                return True
        except Exception:
            return False
        return False

    def _log(self, episode: TutorEpisode, highlight: str, novelty: float) -> None:
        logger = self._logger
        if logger is None:
            return
        try:
            emit = getattr(logger, "_emit", None)
            if callable(emit):
                emit({
                    "kind": "share",
                    "episode_id": episode.episode_id,
                    "highlight": highlight,
                    "novelty": round(novelty, 4),
                })
        except Exception:
            pass


__all__ = ["LearningShareImpulse"]

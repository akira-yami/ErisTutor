# src/runtime/signal_buffer.py
"""
SignalBuffer cho Background Observer.

Nhiệm vụ:
- Giữ một cửa sổ snapshot gần đây.
- Tính diff giữa snapshot mới và snapshot trước.
- Ước lượng novelty/repetition mà không gọi model.
- Làm cổng chống spam model.
"""

from __future__ import annotations
from core.di import resolve_dependency

import difflib
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

try:  # repo chạy với PYTHONPATH=src
    from cortex.observer_contracts import (
        SignalDiff,
        SignalSnapshot,
        clamp01,
        normalize_ascii_fold,
        stable_hash,
    )
except Exception:  # fallback khi test file độc lập
    from src.cortex.observer_contracts import (  # type: ignore
        SignalDiff,
        SignalSnapshot,
        clamp01,
        normalize_ascii_fold,
        stable_hash,
    )


@dataclass
class SignalBufferConfig:
    max_items: int = 120
    repeated_hash_window: int = 12

    # Trọng số diff. Tổng có thể >1, kết quả cuối bị clamp.
    window_weight: float = 0.24
    app_weight: float = 0.16
    focus_weight: float = 0.12
    ocr_weight: float = 0.30
    screen_weight: float = 0.20
    event_weight: float = 0.14
    resource_weight: float = 0.08
    idle_transition_weight: float = 0.08

    ocr_similarity_change_threshold: float = 0.92
    event_similarity_change_threshold: float = 0.82
    resource_delta_threshold: float = 18.0


@dataclass
class SignalBufferStats:
    size: int = 0
    repeated_ocr_hash_count: int = 0
    repeated_screen_hash_count: int = 0
    recent_focus_counts: dict[str, int] = field(default_factory=dict)
    last_push_time: float = 0.0


class SignalBuffer:
    """Bộ nhớ ngắn hạn cho signal nền.

    Đây là hàng rào đầu tiên để ERIS không gọi model vì mọi cú chớp của desktop.
    """

    def __init__(self, config: Optional[SignalBufferConfig] = None):
        self.config = resolve_dependency(config, lambda: SignalBufferConfig())
        self._items: deque[SignalSnapshot] = deque(maxlen=max(1, self.config.max_items))
        self._last_diff: Optional[SignalDiff] = None

    @property
    def items(self) -> list[SignalSnapshot]:
        return list(self._items)

    @property
    def last(self) -> Optional[SignalSnapshot]:
        return self._items[-1] if self._items else None

    @property
    def last_diff(self) -> Optional[SignalDiff]:
        return self._last_diff

    def push(self, snapshot: SignalSnapshot) -> SignalDiff:
        """Thêm snapshot và trả diff so với snapshot trước."""

        previous = self.last
        self._items.append(snapshot)
        diff = self.diff(snapshot, previous)
        self._last_diff = diff
        return diff

    def diff(self, current: SignalSnapshot, previous: Optional[SignalSnapshot]) -> SignalDiff:
        if previous is None:
            return SignalDiff(
                window_changed=True,
                app_changed=bool(current.active_app),
                focus_changed=bool(current.focus_kind_hint and current.focus_kind_hint != "unknown"),
                ocr_changed=bool(current.ocr_text or current.ocr_hash),
                screen_changed=bool(current.screen_hash),
                event_changed=bool(current.recent_events),
                ocr_similarity=0.0,
                event_similarity=0.0,
                diff_score=1.0,
                repetition=0.0,
                novelty=1.0,
                idle_transition=self._idle_bucket(current.idle_seconds),
                reason=["first_snapshot"],
            )

        cfg = self.config
        score = 0.0
        reason: list[str] = []

        window_changed = self._norm(current.active_window_title) != self._norm(previous.active_window_title)
        app_changed = self._norm(current.active_app) != self._norm(previous.active_app)
        focus_changed = self._norm(current.focus_kind_hint) != self._norm(previous.focus_kind_hint)

        current_ocr_hash = current.ocr_hash or stable_hash(current.ocr_text)
        previous_ocr_hash = previous.ocr_hash or stable_hash(previous.ocr_text)
        ocr_hash_changed = bool(current_ocr_hash or previous_ocr_hash) and current_ocr_hash != previous_ocr_hash
        ocr_similarity = self._text_similarity(current.ocr_text, previous.ocr_text)
        ocr_changed = ocr_hash_changed and ocr_similarity < cfg.ocr_similarity_change_threshold

        screen_changed = bool(current.screen_hash or previous.screen_hash) and current.screen_hash != previous.screen_hash

        event_similarity = self._event_similarity(current.recent_events, previous.recent_events)
        event_changed = event_similarity < cfg.event_similarity_change_threshold

        resource_pressure_changed = (
            abs(current.cpu_percent - previous.cpu_percent) >= cfg.resource_delta_threshold
            or abs(current.ram_percent - previous.ram_percent) >= cfg.resource_delta_threshold
        )

        previous_idle_bucket = self._idle_bucket(previous.idle_seconds)
        current_idle_bucket = self._idle_bucket(current.idle_seconds)
        idle_bucket_changed = previous_idle_bucket != current_idle_bucket

        if window_changed:
            score += cfg.window_weight
            reason.append("window_changed")
        if app_changed:
            score += cfg.app_weight
            reason.append("app_changed")
        if focus_changed:
            score += cfg.focus_weight
            reason.append("focus_changed")
        if ocr_changed:
            # OCR đổi càng khác thì càng đáng chú ý.
            score += cfg.ocr_weight * (1.0 - ocr_similarity)
            reason.append("ocr_changed")
        if screen_changed:
            score += cfg.screen_weight
            reason.append("screen_changed")
        if event_changed:
            score += cfg.event_weight * (1.0 - event_similarity)
            reason.append("event_changed")
        if resource_pressure_changed:
            score += cfg.resource_weight
            reason.append("resource_pressure_changed")
        if idle_bucket_changed:
            score += cfg.idle_transition_weight
            reason.append(f"idle_transition:{previous_idle_bucket}->{current_idle_bucket}")

        repetition = self._repetition_score(current)
        novelty = clamp01(score * (1.0 - 0.45 * repetition))
        diff_score = clamp01(score)

        return SignalDiff(
            window_changed=window_changed,
            app_changed=app_changed,
            focus_changed=focus_changed,
            ocr_changed=ocr_changed,
            screen_changed=screen_changed,
            event_changed=event_changed,
            resource_pressure_changed=resource_pressure_changed,
            ocr_similarity=ocr_similarity,
            event_similarity=event_similarity,
            diff_score=diff_score,
            repetition=repetition,
            novelty=novelty,
            idle_transition=current_idle_bucket,
            reason=reason,
        )

    def stats(self) -> SignalBufferStats:
        focus_counts = Counter(self._norm(x.focus_kind_hint or "unknown") for x in self._items)
        last = self.last
        ocr_hash = (last.ocr_hash or stable_hash(last.ocr_text)) if last else ""
        screen_hash = last.screen_hash if last else ""
        return SignalBufferStats(
            size=len(self._items),
            repeated_ocr_hash_count=self._count_recent_hash(lambda s: s.ocr_hash or stable_hash(s.ocr_text), ocr_hash),
            repeated_screen_hash_count=self._count_recent_hash(lambda s: s.screen_hash, screen_hash),
            recent_focus_counts=dict(focus_counts),
            last_push_time=last.timestamp if last else 0.0,
        )

    def recent(self, n: int = 8) -> list[SignalSnapshot]:
        if n <= 0:
            return []
        return list(self._items)[-n:]

    def recent_summary(self, n: int = 6) -> str:
        """Tóm tắt ngắn cho LLM observer nếu cần."""

        rows: list[str] = []
        for item in self.recent(n):
            rows.append(
                f"t={item.timestamp:.0f}; app={item.active_app or 'unknown'}; "
                f"focus={item.focus_kind_hint}; idle={item.idle_seconds:.1f}; "
                f"title={item.active_window_title[:120]}"
            )
        return "\n".join(rows)

    def should_run_text_observer(
        self,
        diff: Optional[SignalDiff] = None,
        *,
        min_diff: float = 0.18,
        cooldown_seconds: float = 8.0,
    ) -> bool:
        """Cổng gọi model nhỏ.

        Rule buffer này chỉ nói `có đáng gọi không`, không gọi model trực tiếp.
        """

        diff = diff or self._last_diff
        if diff is None:
            return False
        if diff.diff_score >= min_diff or diff.ocr_changed or diff.event_changed:
            return self._cooldown_ready(cooldown_seconds)
        return False

    def _cooldown_ready(self, cooldown_seconds: float) -> bool:
        # Nếu chưa đủ dữ liệu, cho chạy. Cooldown thật nên đặt ở runtime caller.
        # Hàm này chỉ tránh gọi liên tục khi push nhiều snapshot cùng timestamp.
        if len(self._items) < 2:
            return True
        now = self._items[-1].timestamp
        prev = self._items[-2].timestamp
        return (now - prev) >= 0.0 or cooldown_seconds <= 0

    def _repetition_score(self, current: SignalSnapshot) -> float:
        if not self._items:
            return 0.0

        cfg = self.config
        recent = list(self._items)[-cfg.repeated_hash_window :]
        if not recent:
            return 0.0

        current_ocr = current.ocr_hash or stable_hash(current.ocr_text)
        current_screen = current.screen_hash

        ocr_repeats = self._count_in(recent, lambda s: s.ocr_hash or stable_hash(s.ocr_text), current_ocr)
        screen_repeats = self._count_in(recent, lambda s: s.screen_hash, current_screen)
        focus_repeats = self._count_in(recent, lambda s: self._norm(s.focus_kind_hint), self._norm(current.focus_kind_hint))

        denom = max(1, len(recent))
        score = max(
            ocr_repeats / denom if current_ocr else 0.0,
            screen_repeats / denom if current_screen else 0.0,
            focus_repeats / denom,
        )
        return clamp01(score)

    def _count_recent_hash(self, getter, value: str) -> int:
        if not value:
            return 0
        return self._count_in(list(self._items)[-self.config.repeated_hash_window :], getter, value)

    @staticmethod
    def _count_in(items: Iterable[SignalSnapshot], getter, value: str) -> int:
        if not value:
            return 0
        return sum(1 for item in items if getter(item) == value)

    @staticmethod
    def _norm(text: str) -> str:
        return normalize_ascii_fold(text or "")

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        a_norm = normalize_ascii_fold(a or "")
        b_norm = normalize_ascii_fold(b or "")
        if not a_norm and not b_norm:
            return 1.0
        if not a_norm or not b_norm:
            return 0.0
        # Cắt để difflib không bị OCR dài hành hạ như luận văn thiếu mục lục.
        return difflib.SequenceMatcher(None, a_norm[:4000], b_norm[:4000]).ratio()

    @classmethod
    def _event_similarity(cls, a: list[str], b: list[str]) -> float:
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        a_set = {cls._norm(x) for x in a[-12:] if x}
        b_set = {cls._norm(x) for x in b[-12:] if x}
        if not a_set and not b_set:
            return 1.0
        if not a_set or not b_set:
            return 0.0
        inter = len(a_set & b_set)
        union = len(a_set | b_set)
        return inter / max(1, union)

    @staticmethod
    def _idle_bucket(idle_seconds: float) -> str:
        if idle_seconds < 8:
            return "active"
        if idle_seconds < 30:
            return "short_idle"
        if idle_seconds < 120:
            return "long_idle"
        return "deep_idle"

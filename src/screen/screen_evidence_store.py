from __future__ import annotations

"""Kho evidence màn hình thread-safe và truy hồi theo relevance."""

import re
import threading
import time
import unicodedata

from .evidence_models import ActiveWindowInfo, ScreenTextBlock, ScreenTextSnapshot

_STOPWORDS = {"cai", "nay", "do", "kia", "gi", "la", "sao", "ta", "toi", "ban", "em", "ban", "tren", "man", "hinh", "xem", "nhin", "cho", "voi", "di"}


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text or "").lower()).replace("đ", "d")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9_#+.\-]+", " ", value).strip()


class ScreenEvidenceStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot = ScreenTextSnapshot(window=ActiveWindowInfo(title="Desktop"))

    def publish(self, snapshot: ScreenTextSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot

    def snapshot(self) -> ScreenTextSnapshot:
        with self._lock:
            return self._snapshot

    def query(self, user_text: str, *, max_age_seconds: float = 8.0, max_blocks: int = 12, max_chars: int = 1400, generic_screen_request: bool = False) -> tuple[ScreenTextBlock, ...]:
        now = time.time()
        snapshot = self.snapshot()
        if snapshot.privacy_blocked or not snapshot.blocks:
            return ()
        tokens = {token for token in _normalize(user_text).split() if len(token) >= 2 and token not in _STOPWORDS}
        window_tokens = set(_normalize(getattr(snapshot.window, "title", "")).split())
        ranked: list[tuple[float, ScreenTextBlock]] = []
        for block in snapshot.blocks:
            age = max(0.0, now - float(block.captured_at))
            if age > max_age_seconds * 4:
                continue
            block_tokens = set(_normalize(block.text).split())
            overlap = len(tokens & block_tokens) / max(1, len(tokens)) if tokens else 0.0
            window_overlap = len(tokens & window_tokens) / max(1, len(tokens)) if tokens else 0.0
            freshness = max(0.0, 1.0 - age / max(0.1, max_age_seconds * 2))
            score = 0.45 * overlap + 0.25 * window_overlap + 0.20 * freshness + 0.10 * float(block.confidence)
            if generic_screen_request:
                score += 0.28 * float(block.confidence)
            if score >= (0.18 if generic_screen_request else 0.24):
                ranked.append((score, block))
        ranked.sort(key=lambda item: item[0], reverse=True)
        selected: list[ScreenTextBlock] = []
        total = 0
        for _, block in ranked:
            if len(selected) >= max_blocks or total >= max_chars:
                break
            selected.append(block)
            total += len(block.text)
        return tuple(selected)

    def ambient_facts(self, *, max_age_seconds: float = 8.0, max_items: int = 6) -> tuple[str, ...]:
        return tuple(
            block.text
            for block in self.ambient_blocks(
                max_age_seconds=max_age_seconds,
                max_items=max_items,
            )
            if block.text.strip()
        )

    def ambient_blocks(
        self,
        *,
        max_age_seconds: float = 8.0,
        max_items: int = 6,
    ) -> tuple[ScreenTextBlock, ...]:
        """Trả các block OCR/UIA tươi nhất để làm ambient chat context.

        Khác ``query()``, hàm này không yêu cầu câu user phải chứa marker màn
        hình. Nhờ vậy mọi lượt chat đều nhận được OCR cache hiện có mà không ép
        worker chạy OCR lại và cũng không tự biến screen thành primary source.
        """

        now = time.time()
        snapshot = self.snapshot()
        if snapshot.privacy_blocked:
            return ()
        items = [block for block in snapshot.blocks if now - float(block.captured_at) <= max_age_seconds]
        items.sort(key=lambda block: (block.confidence, block.captured_at), reverse=True)
        return tuple(block for block in items[:max_items] if block.text.strip())

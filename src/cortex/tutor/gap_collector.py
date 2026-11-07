# -*- coding: utf-8 -*-
"""
cortex/tutor/gap_collector.py
=============================

Dẫn xuất Knowledge_Gap từ observer 'unknown' và evidence gap có contract,
dedupe theo ngữ nghĩa, giới hạn hàng đợi.
"""

from __future__ import annotations
from core.di import resolve_dependency

from typing import Any, Callable, Optional

from .config import TutorConfig
from .contracts import KnowledgeGap


# Import mềm hạ tầng tương đồng — fallback token-overlap nếu thiếu.
try:  # pragma: no cover
    from cortex.event_semantics import _cosine_similarity, _normalize_vector  # type: ignore
except Exception:  # pragma: no cover
    def _normalize_vector(vector):  # type: ignore
        return tuple(vector or ())

    def _cosine_similarity(left, right):  # type: ignore
        return -1.0

try:  # pragma: no cover
    from cortex.value_utils import tokenize  # type: ignore
except Exception:  # pragma: no cover
    def tokenize(text: Any):  # type: ignore
        return set(str(text or "").lower().split())


EmbedFn = Callable[[str], "tuple[float, ...]"]


class GapCollector:
    """Thu thập, dedupe và giới hạn hàng đợi Knowledge_Gap."""

    def __init__(self, config: Optional[TutorConfig] = None, *, embed_fn: Optional[EmbedFn] = None) -> None:
        self.config = resolve_dependency(config, TutorConfig).normalized()
        self._embed_fn = embed_fn
        self._gaps: list[KnowledgeGap] = []

    # ------------------------------------------------------------------
    # Dẫn xuất
    # ------------------------------------------------------------------

    def collect_from_violation(self, violation: Any, *, stake_relevance: float, now: float) -> Optional[KnowledgeGap]:
        """Từ một violation có contract; caller không phụ thuộc model shadow cụ thể."""

        expected = str(getattr(violation, "expected", "") or "").strip()
        observed = str(getattr(violation, "observed", "") or "").strip()
        pe = float(getattr(violation, "prediction_error", 0.0) or 0.0)
        if not expected and not observed:
            return None
        description = f"Tôi tưởng: {expected or '(không rõ)'} — nhưng thực tế: {observed or '(không rõ)'}"
        context = f"expected={expected} | observed={observed}"
        gap = KnowledgeGap(
            description=description,
            context=context,
            source="expectation_violation",
            stake_relevance=stake_relevance,
            prediction_error=pe,
            created_ts=now,
            updated_ts=now,
            embedding=self._embed(description),
        )
        return gap

    def collect_from_uncertainty(
        self,
        topic: str,
        *,
        context: str = "",
        stake: float = 0.6,
        now: float = 0.0,
        kind_hint: str = "durable",
        force: bool = True,
    ) -> Optional[KnowledgeGap]:
        """Lỗ do cognition báo 'không chắc' hoặc lệnh tra cứu trực tiếp từ user."""

        topic = str(topic or "").strip()
        if not topic:
            return None
        return KnowledgeGap(
            description=topic,
            context=str(context or ""),
            source="epistemic_uncertainty",
            stake_relevance=stake,
            prediction_error=stake,
            created_ts=now,
            updated_ts=now,
            embedding=self._embed(topic),
            force=bool(force),
            kind_hint=str(kind_hint or "durable"),
        )

    def collect_from_observer(self, frame: Any, *, now: float, screen_context: str = "") -> Optional[KnowledgeGap]:
        """Từ một ObserverFrame có focus/activity 'unknown' và ambiguity cao.

        `screen_context` là đóng gói nội dung màn hình THẬT (title/app/url/OCR đã cắt) để
        Gemini có cái mà đoán ngữ cảnh — thay vì hỏi suông "tôi không phân loại được".
        """

        focus = str(getattr(frame, "focus_kind", "") or "")
        activity = str(getattr(frame, "activity_intent", "") or "")
        ambiguity = float(getattr(frame, "ambiguity", 0.0) or 0.0)
        if focus != "unknown" and activity != "unknown":
            return None
        if ambiguity < self.config.observer_ambiguity_threshold:
            return None
        screen_context = str(screen_context or "").strip()
        if not screen_context:
            # Chưa có manh mối màn hình (mới mở / "nothing changed") ⇒ KHÔNG tạo gap.
            # Gap suông "tôi không phân loại được" vừa vô dụng (Gemini đoán gì?), vừa luôn
            # hash ra cùng một gap_id ⇒ lần nào khởi động cũng đẻ rồi bị skip/biết-rồi.
            return None
        description = (
            f"Người dùng đang xem màn hình nhưng tôi chưa rõ ngữ cảnh. "
            f"Manh mối: {screen_context}. Đây có thể là hoạt động/nội dung gì?"
        )
        evidence = list(getattr(frame, "evidence", None) or [])
        context = f"focus={focus} | activity={activity} | ambiguity={ambiguity:.2f} | screen={screen_context[:400]} | evidence={evidence[:4]}"
        gap = KnowledgeGap(
            description=description[:500],
            context=context,
            source="observer_unknown",
            stake_relevance=clamp01_local(getattr(frame, "salience", 0.0)),
            prediction_error=clamp01_local(ambiguity),
            created_ts=now,
            updated_ts=now,
            embedding=self._embed(description),
        )
        gap.kind_hint = "context"
        return gap

    # ------------------------------------------------------------------
    # Hàng đợi
    # ------------------------------------------------------------------

    def push(self, gap: Optional[KnowledgeGap]) -> bool:
        """Thêm gap với dedupe ngữ nghĩa + giới hạn queue. Trả True nếu thêm mới."""

        if gap is None:
            return False
        existing = self._find_duplicate(gap)
        if existing is not None:
            existing.updated_ts = max(existing.updated_ts, gap.updated_ts)
            existing.stake_relevance = max(existing.stake_relevance, gap.stake_relevance)
            existing.prediction_error = max(existing.prediction_error, gap.prediction_error)
            return False
        self._gaps.append(gap)
        self._enforce_cap()
        return True

    def drain_candidates(self) -> list[KnowledgeGap]:
        """Các gap chưa giải quyết, cho gate đọc."""

        return [g for g in self._gaps if not g.resolved]

    def resolve(self, gap: KnowledgeGap) -> None:
        for g in self._gaps:
            if g.gap_id == gap.gap_id:
                g.resolved = True
        self._gaps = [g for g in self._gaps if not g.resolved]

    def serialize(self) -> dict[str, Any]:
        return {
            "queue_size": len(self._gaps),
            "unresolved": len(self.drain_candidates()),
            "gaps": [g.to_dict() for g in self._gaps[-20:]],
        }

    @property
    def size(self) -> int:
        return len(self._gaps)

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------

    def _embed(self, text: str) -> tuple[float, ...]:
        if self._embed_fn is None:
            return ()
        try:
            vec = self._embed_fn(text)
            return tuple(vec or ())
        except Exception:
            return ()

    def _find_duplicate(self, gap: KnowledgeGap) -> Optional[KnowledgeGap]:
        for existing in self._gaps:
            if existing.resolved:
                continue
            if existing.gap_id == gap.gap_id:
                return existing
            if self._similar(existing, gap):
                return existing
        return None

    def _similar(self, a: KnowledgeGap, b: KnowledgeGap) -> bool:
        if a.embedding and b.embedding:
            va = _normalize_vector(tuple(a.embedding))
            vb = _normalize_vector(tuple(b.embedding))
            if va and vb and len(va) == len(vb):
                sim = _cosine_similarity(va, vb)
                if sim >= self.config.dedupe_similarity:
                    return True
                return False
        # Fallback token-overlap
        ta = tokenize(a.description)
        tb = tokenize(b.description)
        if not ta or not tb:
            return False
        overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
        return overlap >= self.config.dedupe_similarity

    def _enforce_cap(self) -> None:
        cap = self.config.max_gap_queue
        if len(self._gaps) <= cap:
            return
        # Bỏ phần tử cũ nhất có stake_relevance thấp nhất cho tới khi vừa cap.
        while len(self._gaps) > cap:
            victim_idx = min(
                range(len(self._gaps)),
                key=lambda i: (self._gaps[i].stake_relevance, self._gaps[i].updated_ts),
            )
            self._gaps.pop(victim_idx)


def clamp01_local(value: Any) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return 0.0
    if n != n:
        return 0.0
    return max(0.0, min(1.0, n))


__all__ = ["GapCollector"]

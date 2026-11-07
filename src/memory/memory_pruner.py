from __future__ import annotations

"""
memory_pruner.py
================

Prune policy nhẹ cho memory item có metadata retention.

Store hiện tại đã có prune theo max_items. File này thêm logic theo nghĩa:
- session/transient hết hạn thì bỏ;
- short_term quá hạn thì archive/demote;
- long_term có fact_key quan trọng thì không xóa chỉ vì cũ.

Tách prune ra riêng để sau này không biến SemanticMemoryStore thành nồi lẩu 2000 dòng nữa.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal
import time


@dataclass(frozen=True, slots=True)
class PruneDecision:
    action: Literal["keep", "archive", "drop"]
    reason: str
    score: float = 0.0
    warnings: tuple[str, ...] = field(default_factory=tuple)


class MemoryPruner:
    """Pruner dựa trên retention metadata nếu có."""

    def decide(self, item: Any, *, now: float | None = None) -> PruneDecision:
        now = float(now if now is not None else time.time())
        metadata = _metadata(item)
        retention_class = str(metadata.get("retention_class") or "medium_term")
        prune_after = metadata.get("prune_after_seconds")
        first_seen = _epoch(getattr(item, "first_seen_at", "") or metadata.get("first_seen_at", ""))
        updated = _epoch(getattr(item, "updated_at", "") or metadata.get("updated_at", ""))
        anchor = max(first_seen, updated)
        age = max(0.0, now - anchor) if anchor > 0 else 0.0

        fact_key = str(metadata.get("fact_key") or "")
        importance = _float(metadata.get("importance", 0.5), 0.5)
        confidence = _float(getattr(item, "confidence", metadata.get("confidence", 0.5)), 0.5)
        surface = _surface_text(item)
        semantic_source = str(metadata.get("semantic_source") or metadata.get("source") or "").strip().lower()

        # Dọn nhiễu semantic cũ: fact kiểu greeting/slang/asked/questioning không
        # thuộc long-term semantic. Chúng nên ở session/episodic/repair. Nếu cứ giữ,
        # ERIS sẽ nhớ rất kỹ chuyện bạn chào thế nào, rồi quên thứ đáng nhớ hơn.
        # Một thảm họa nhỏ nhưng có indent đẹp.
        if not fact_key and _looks_like_trivial_semantic_noise(surface, semantic_source):
            return PruneDecision("archive", "trivial_semantic_noise_without_fact_key", score=0.10)

        if retention_class in {"session", "transient"} and prune_after and age > float(prune_after):
            return PruneDecision("drop", "session_or_transient_expired", score=0.0)
        if retention_class == "short_term" and prune_after and age > float(prune_after):
            return PruneDecision("archive", "short_term_expired", score=0.20)
        if fact_key and importance >= 0.70:
            return PruneDecision("keep", "protected_key_fact", score=0.95)

        score = confidence * 0.45 + importance * 0.45
        if retention_class == "long_term":
            score += 0.20
        elif retention_class == "medium_term":
            score += 0.08
        return PruneDecision("keep" if score >= 0.35 else "archive", "retention_score", score=min(1.0, score))


def _metadata(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item.get("metadata") or {})
    return dict(getattr(item, "metadata", {}) or {})


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _epoch(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "")
    if not text:
        return 0.0
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%dT%H:%M:%S%z"))
    except Exception:
        return 0.0


def _surface_text(item: Any) -> str:
    if isinstance(item, dict):
        return " ".join(str(item.get(key) or "") for key in ("claim", "summary", "raw_text", "description"))
    return " ".join(str(getattr(item, key, "") or "") for key in ("claim", "summary", "raw_text", "description"))


def _looks_like_trivial_semantic_noise(surface: str, semantic_source: str) -> bool:
    text = str(surface or "").strip().lower()
    if not text:
        return False
    markers = (
        "user greeted",
        "user said 'xin chào",
        "user asked",
        "asked if eris",
        "user used slang",
        "questioning the use",
        "previous response quality",
        "nhận xét tôi lạnh",
        "eris's name",
    )
    if any(marker in text for marker in markers):
        return True
    return semantic_source == "decision_extracted_fact" and any(marker in text for marker in ("greet", "asked", "slang", "question"))


__all__ = ["PruneDecision", "MemoryPruner"]

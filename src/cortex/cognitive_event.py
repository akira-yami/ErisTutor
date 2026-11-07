# -*- coding: utf-8 -*-
"""Typed cognitive event cho dòng nhận thức event-driven của ERIS.

V42 đặt một luật rất đơn giản ở đây: state nhận thức chỉ được đổi khi có một
``CognitiveEvent`` mới. Scheduler tick chỉ làm thời gian trôi, không được giả vờ
rằng cùng một seed vừa xảy ra lần nữa.

Module này không quyết định nói/im, không lưu concern và không viết câu public.
Nó chỉ cấp danh tính ổn định + idempotency cho các sự kiện có thể làm đầu óc
ERIS thay đổi.
"""
from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence


COGNITIVE_EVENT_VERSION = "eris.cognition.v42.event_driven"


def _compact(value: Any, limit: int = 360) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _stable_digest(*parts: Any) -> str:
    joined = "\x1f".join(_compact(part, 800) for part in parts)
    return hashlib.sha256(joined.encode("utf-8", errors="replace")).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class CognitiveEvent:
    """Một thay đổi mới có quyền làm cognition update đúng một lần."""

    event_id: str
    origin: str
    kind: str
    subject: str
    occurred_at: float
    evidence_refs: tuple[str, ...] = ()
    source_ref: str = ""
    novelty_key: str = ""
    parent_event_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = COGNITIVE_EVENT_VERSION
        return payload

    @classmethod
    def from_user_turn(
        cls,
        user_text: str,
        *,
        turn_id: str,
        now: float | None = None,
    ) -> "CognitiveEvent | None":
        """Tạo event identity ổn định cho một lượt user thật.

        Nội dung user là evidence của chính event, nhưng event ID dựa trên
        ``turn_id`` để retry cùng lượt không được làm mood/cognition nhảy lần hai.
        """

        subject = _compact(user_text, 360)
        source_ref = _compact(turn_id, 180)
        if not subject or not source_ref:
            return None
        return cls(
            event_id=f"cogevt:user:{source_ref}",
            origin="user",
            kind="user_turn",
            subject=subject,
            occurred_at=float(now or time.time()),
            evidence_refs=(source_ref,),
            source_ref=source_ref,
            novelty_key=_stable_digest("user_turn", source_ref, subject),
            metadata={"turn_id": source_ref},
        )

    @classmethod
    def from_seed_candidate(cls, candidate: Any, *, now: float | None = None) -> "CognitiveEvent | None":
        """Chuyển typed seed candidate thành event, không dùng câu Surface làm nguồn."""

        seed_id = _compact(getattr(candidate, "seed_id", ""), 180)
        subject = _compact(getattr(candidate, "topic", ""), 360)
        if not seed_id or not subject:
            return None
        event_kind = _compact(getattr(candidate, "seed_event_kind", "thought") or "thought", 64)
        source_kind = _compact(getattr(candidate, "source_kind", "seed") or "seed", 80)
        refs = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in (
                    seed_id,
                    *tuple(getattr(candidate, "evidence_refs", ()) or ()),
                )
                if str(item).strip()
            )
        )[:16]
        candidate_source = _compact(getattr(candidate, "source", "") or "", 80)
        is_material = candidate_source == "mental_material"
        event_id = f"cogevt:material:{seed_id}" if is_material else f"cogevt:seed:{seed_id}"
        return cls(
            event_id=event_id,
            origin="mental_material" if is_material else "seed",
            kind=event_kind,
            subject=subject,
            occurred_at=float(now or time.time()),
            evidence_refs=refs,
            source_ref=seed_id,
            novelty_key=_stable_digest("mental_material" if is_material else "seed", event_kind, subject),
            metadata={
                "material_source": bool(is_material),
                "seed_id": seed_id,
                "seed_family": source_kind,
                "seed_quality": float(getattr(candidate, "seed_quality_score", 0.0) or 0.0),
                "seed_motive_strength": float(getattr(candidate, "seed_motive_strength", 0.0) or 0.0),
                "uncertainty": float(getattr(candidate, "uncertainty", 0.35) or 0.35),
                # V70: chỉ development đã qua deterministic score mới được mở
                # completion concern. Model label đơn độc không đủ quyền mutation.
                "semantic_mutation": bool(
                    is_material
                    and float(getattr(candidate, "semantic_development_score", 0.0) or 0.0) >= 0.62
                ),
                "semantic_development_mode": _compact(getattr(candidate, "semantic_development_mode", ""), 32),
                "semantic_development_score": float(getattr(candidate, "semantic_development_score", 0.0) or 0.0),
                "rumination_return": bool(getattr(candidate, "rumination_return", False)),
                "semantic_parent_concern_id": _compact(getattr(candidate, "semantic_parent_concern_id", ""), 140),
            },
        )


class CognitiveEventLedger:
    """Idempotency ledger bounded trong session.

    ConcernStore còn lưu event IDs đã ingest để chống replay qua object/runtime
    khác. Ledger này là lớp nhanh trên hot path, tránh mỗi tick lại quét store.
    """

    def __init__(self, *, max_events: int = 1024, ttl_seconds: float = 24.0 * 3600.0) -> None:
        self.max_events = max(64, int(max_events))
        self.ttl_seconds = max(300.0, float(ttl_seconds))
        self._seen: "OrderedDict[str, float]" = OrderedDict()

    def accept(self, event: CognitiveEvent, *, now: float | None = None) -> bool:
        current = float(now or time.time())
        self._prune(current)
        key = str(event.event_id or "").strip()
        if not key or key in self._seen:
            return False
        self._seen[key] = current
        self._seen.move_to_end(key)
        while len(self._seen) > self.max_events:
            self._seen.popitem(last=False)
        return True

    def contains(self, event_id: str, *, now: float | None = None) -> bool:
        self._prune(float(now or time.time()))
        return str(event_id or "").strip() in self._seen

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": COGNITIVE_EVENT_VERSION,
            "seen": len(self._seen),
            "max_events": self.max_events,
            "ttl_seconds": self.ttl_seconds,
        }

    def _prune(self, now: float) -> None:
        cutoff = now - self.ttl_seconds
        while self._seen:
            _key, seen_at = next(iter(self._seen.items()))
            if seen_at >= cutoff:
                break
            self._seen.popitem(last=False)


__all__ = [
    "COGNITIVE_EVENT_VERSION",
    "CognitiveEvent",
    "CognitiveEventLedger",
]

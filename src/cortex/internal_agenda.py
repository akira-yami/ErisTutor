# -*- coding: utf-8 -*-
"""
src/cortex/internal_agenda.py
================================

Stack agenda nội bộ cho ERIS.

Module này là nơi giữ các vấn đề ERIS đang tự treo trong đầu:
- câu hỏi chưa đóng,
- lỗi cần repair,
- ký ức cần chưng cất,
- insight muốn kiểm chứng,
- tín hiệu nên xem sâu hơn nhưng chưa được phép đánh thức main model.

Luật quan trọng:
- File này KHÔNG gọi LLM.
- File này KHÔNG tự nói với user.
- File này KHÔNG tự ghi memory dài hạn.
- Nó chỉ giữ stack có evidence để CortexRuntime, planner và DaydreamConsolidation đọc.

Nói cách khác: đây là sổ nháp có khóa. Không phải cái loa phường.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal, Mapping, Optional

AgendaKind = Literal[
    "memory_consolidation",
    "repair",
    "open_question",
    "scene_inquiry",
    "self_continuity",
    "technical_followup",
    "relationship_tension",
    "resource_guard",
    "misc",
]

AgendaStatus = Literal["open", "blocked", "resolved", "rejected", "expired"]


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        n = default
    if math.isnan(n) or math.isinf(n):
        n = default
    return max(0.0, min(1.0, n))


def _compact(text: Any, limit: int = 360) -> str:
    value = " ".join(str(text or "").replace("\x00", " ").split()).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _fingerprint(kind: str, summary: str, evidence_ids: Iterable[str] = ()) -> str:
    base = f"{kind}|{_compact(summary, 240).lower()}|{'|'.join(sorted(str(x) for x in evidence_ids if str(x).strip())[:8])}"
    return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass(slots=True)
class InternalAgendaItem:
    """Một mục agenda nội bộ.

    pressure:
        Độ ép phải xử lý. CortexRuntime và planner đọc trường này.
    urgency:
        Độ gấp. Khác pressure: một thứ có thể dai dẳng nhưng không gấp.
    confidence:
        Độ chắc của giả thuyết. Không đủ evidence thì giữ thấp, đừng phán như thánh.
    speech_value:
        Nếu surface ra user thì có đáng nói không. Nhiều insight chỉ nên nằm im.
    evidence_ids:
        Bắt buộc có nguồn. Agenda không nguồn là tin đồn trong đầu máy.
    """

    id: str
    kind: AgendaKind
    summary: str
    evidence_ids: tuple[str, ...] = ()
    pressure: float = 0.0
    urgency: float = 0.0
    confidence: float = 0.0
    speech_value: float = 0.0
    status: AgendaStatus = "open"
    source: str = "runtime"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    ttl_seconds: float = 3600.0
    attempts: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.summary = _compact(self.summary, 520)
        self.pressure = _clamp01(self.pressure)
        self.urgency = _clamp01(self.urgency)
        self.confidence = _clamp01(self.confidence)
        self.speech_value = _clamp01(self.speech_value)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x).strip())[:16]
        if not self.id:
            self.id = _fingerprint(self.kind, self.summary, self.evidence_ids)

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @property
    def score(self) -> float:
        """Điểm ưu tiên tổng hợp cho stack.

        Công thức cố tình đơn giản. Không cần một cái transformer chỉ để xếp hàng việc vặt.
        """

        return _clamp01(
            0.46 * self.pressure
            + 0.26 * self.urgency
            + 0.16 * self.confidence
            + 0.12 * self.speech_value
        )

    def expired(self, now: float | None = None) -> bool:
        if self.ttl_seconds <= 0:
            return False
        return (float(now or time.time()) - self.updated_at) > self.ttl_seconds

    def touch(
        self,
        *,
        pressure: float | None = None,
        urgency: float | None = None,
        confidence: float | None = None,
        speech_value: float | None = None,
        evidence_ids: Iterable[str] = (),
        payload: Mapping[str, Any] | None = None,
    ) -> "InternalAgendaItem":
        """Cập nhật item theo kiểu merge mềm.

        Cái mới không được xóa evidence cũ. Loài người đã phát minh mất dấu log,
        ta không cần cosplay thảm họa đó.
        """

        if pressure is not None:
            self.pressure = max(self.pressure * 0.92, _clamp01(pressure))
        if urgency is not None:
            self.urgency = max(self.urgency * 0.90, _clamp01(urgency))
        if confidence is not None:
            self.confidence = max(self.confidence * 0.94, _clamp01(confidence))
        if speech_value is not None:
            self.speech_value = max(self.speech_value * 0.90, _clamp01(speech_value))
        merged_evidence = [*self.evidence_ids]
        for eid in evidence_ids:
            text = str(eid or "").strip()
            if text and text not in merged_evidence:
                merged_evidence.append(text)
        self.evidence_ids = tuple(merged_evidence[:16])
        if payload:
            self.payload.update(dict(payload))
        self.updated_at = time.time()
        self.status = "open" if self.status in {"blocked", "expired"} else self.status
        return self

    def decay(self, factor: float = 0.94) -> None:
        self.pressure = _clamp01(self.pressure * factor)
        self.urgency = _clamp01(self.urgency * (factor + 0.02))
        self.speech_value = _clamp01(self.speech_value * (factor + 0.01))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InternalAgendaItem":
        return cls(
            id=str(data.get("id") or ""),
            kind=str(data.get("kind") or "misc"),  # type: ignore[arg-type]
            summary=str(data.get("summary") or ""),
            evidence_ids=tuple(str(x) for x in data.get("evidence_ids", ()) or ()),
            pressure=_clamp01(data.get("pressure", 0.0)),
            urgency=_clamp01(data.get("urgency", 0.0)),
            confidence=_clamp01(data.get("confidence", 0.0)),
            speech_value=_clamp01(data.get("speech_value", 0.0)),
            status=str(data.get("status") or "open"),  # type: ignore[arg-type]
            source=str(data.get("source") or "runtime"),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
            ttl_seconds=float(data.get("ttl_seconds") or 3600.0),
            attempts=int(data.get("attempts") or 0),
            payload=dict(data.get("payload") or {}),
        )


class InternalAgendaStack:
    """CRUD stack cho agenda nội bộ.

    Stack này nhỏ, có decay, có TTL, và không biến thành database thứ hai.
    """

    def __init__(self, *, max_items: int = 64, backing_path: str | None = None, autosave: bool = True) -> None:
        self.max_items = max(4, int(max_items or 64))
        self._items: dict[str, InternalAgendaItem] = {}
        self.backing_path = str(backing_path or "").strip() or None
        self.autosave = bool(autosave)
        if self.backing_path:
            self._load_from_disk()

    def __len__(self) -> int:
        return len(self._items)

    def items(self) -> list[InternalAgendaItem]:
        return list(self._items.values())

    def open_items(self) -> list[InternalAgendaItem]:
        now = time.time()
        self.prune(now=now)
        return [item for item in self._items.values() if item.is_open]

    def add(
        self,
        *,
        kind: AgendaKind = "misc",
        summary: str,
        evidence_ids: Iterable[str] = (),
        pressure: float = 0.0,
        urgency: float = 0.0,
        confidence: float = 0.0,
        speech_value: float = 0.0,
        source: str = "runtime",
        ttl_seconds: float = 3600.0,
        payload: Mapping[str, Any] | None = None,
        item_id: str = "",
    ) -> InternalAgendaItem:
        evidence_tuple = tuple(str(x) for x in evidence_ids if str(x).strip())[:16]
        iid = item_id or _fingerprint(kind, summary, evidence_tuple)
        if iid in self._items:
            item = self._items[iid].touch(
                pressure=pressure,
                urgency=urgency,
                confidence=confidence,
                speech_value=speech_value,
                evidence_ids=evidence_tuple,
                payload=payload,
            )
            self._persist()
            return item
        item = InternalAgendaItem(
            id=iid,
            kind=kind,
            summary=summary,
            evidence_ids=evidence_tuple,
            pressure=pressure,
            urgency=urgency,
            confidence=confidence,
            speech_value=speech_value,
            source=source,
            ttl_seconds=ttl_seconds,
            payload=dict(payload or {}),
        )
        self._items[item.id] = item
        self._trim()
        self._persist()
        return item

    def mark(self, item_id: str, status: AgendaStatus) -> bool:
        item = self._items.get(str(item_id or ""))
        if item is None:
            return False
        item.status = status
        item.updated_at = time.time()
        self._persist()
        return True

    def resolve(self, item_id: str) -> bool:
        return self.mark(item_id, "resolved")

    def block(self, item_id: str, reason: str = "") -> bool:
        item = self._items.get(str(item_id or ""))
        if item is None:
            return False
        item.status = "blocked"
        if reason:
            item.payload["blocked_reason"] = _compact(reason, 240)
        item.updated_at = time.time()
        self._persist()
        return True

    def top_items(self, *, limit: int = 5, min_score: float = 0.0) -> list[InternalAgendaItem]:
        self.prune()
        candidates = [x for x in self._items.values() if x.is_open and x.score >= min_score]
        candidates.sort(key=lambda x: (x.score, x.updated_at), reverse=True)
        return candidates[: max(0, limit)]

    def pressure(self) -> float:
        top = self.top_items(limit=5)
        if not top:
            return 0.0
        # Dùng max + một ít trung bình để stack đông việc vẫn tạo áp lực, nhưng không nổ tung.
        max_score = max(item.score for item in top)
        avg = sum(item.score for item in top) / len(top)
        return _clamp01(0.72 * max_score + 0.28 * avg)

    def decay(self, factor: float = 0.94) -> None:
        for item in self._items.values():
            if item.status == "open":
                item.decay(factor=factor)
        self.prune()
        self._persist()

    def prune(self, now: float | None = None) -> None:
        current = float(now or time.time())
        changed = False
        for iid, item in list(self._items.items()):
            if item.expired(current):
                item.status = "expired"
                changed = True
            if item.status in {"resolved", "rejected", "expired"} and current - item.updated_at > 600:
                self._items.pop(iid, None)
                changed = True
        self._trim()
        if changed:
            self._persist()

    def to_dict(self) -> dict[str, Any]:
        return {"max_items": self.max_items, "items": [item.to_dict() for item in self.items()]}

    def save(self, path: str | None = None) -> None:
        target = str(path or self.backing_path or "").strip()
        if not target:
            return
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InternalAgendaStack":
        stack = cls(max_items=int(data.get("max_items") or 64), autosave=False)
        for raw in data.get("items", ()) or ():
            try:
                item = InternalAgendaItem.from_mapping(raw)
                stack._items[item.id] = item
            except Exception:
                continue
        stack._trim()
        return stack

    def _trim(self) -> None:
        if len(self._items) <= self.max_items:
            return
        ordered = sorted(self._items.values(), key=lambda x: (x.status == "open", x.score, x.updated_at), reverse=True)
        keep = {item.id for item in ordered[: self.max_items]}
        for iid in list(self._items.keys()):
            if iid not in keep:
                self._items.pop(iid, None)

    def _load_from_disk(self) -> None:
        if not self.backing_path or not os.path.exists(self.backing_path):
            return
        try:
            with open(self.backing_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return
        if not isinstance(payload, Mapping):
            return
        restored = self.from_mapping(payload)
        self.max_items = restored.max_items
        self._items = restored._items

    def _persist(self) -> None:
        if not self.autosave or not self.backing_path:
            return
        try:
            self.save(self.backing_path)
        except Exception:
            return

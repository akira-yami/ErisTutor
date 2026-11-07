"""Append-only Event Ledger cho ERIS.

Tầng này giữ raw truth: tín hiệu thô, evidence đã tạo ra, frame liên quan,
và metadata audit. Nó không kết luận tâm lý, không merge ký ức, không sửa quá khứ.
Nếu một module muốn lưu sự kiện, nó phải tạo StateCommitRequest rồi MemoryCognitionCore
mới gọi vào đây. Đừng để module tự mở file JSON như một con chuột trong nhà kho.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

PrivacyLevel = Literal["public", "normal", "private", "sensitive"]


def now_ts() -> float:
    """Trả timestamp float để khớp RawSignal/EvidenceItem trong cortex."""
    return time.time()


def iso_now() -> str:
    """Timestamp người đọc được. Audit mà không đọc được thì cũng chỉ là trang trí."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def compact_text(value: Any, *, limit: int = 600) -> str:
    """Nén text để ledger không phình thành nghĩa địa token."""
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text



from .json_store_io import atomic_write_json


@dataclass(slots=True)
class EventLedgerRecord:
    """Một dòng sự kiện append-only.

    Ledger là nơi lưu cái đã xảy ra, không phải nơi phán xét nó có nghĩa gì.
    `content` có thể là dict, list hoặc string, nhưng nên đủ nhỏ để audit.
    """

    id: str
    timestamp: float
    iso_timestamp: str
    frame_id: str
    source: str
    content: Any
    reliability: float = 1.0
    privacy_level: PrivacyLevel = "normal"
    raw: bool = True
    source_signal_id: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": float(self.timestamp),
            "iso_timestamp": self.iso_timestamp,
            "frame_id": self.frame_id,
            "source": self.source,
            "content": self.content,
            "reliability": round(float(self.reliability), 4),
            "privacy_level": self.privacy_level,
            "raw": bool(self.raw),
            "source_signal_id": self.source_signal_id,
            "evidence_ids": list(self.evidence_ids),
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EventLedgerRecord":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex),
            timestamp=float(data.get("timestamp") or now_ts()),
            iso_timestamp=str(data.get("iso_timestamp") or iso_now()),
            frame_id=str(data.get("frame_id") or ""),
            source=str(data.get("source") or "unknown"),
            content=data.get("content"),
            reliability=float(data.get("reliability") if data.get("reliability") is not None else 1.0),
            privacy_level=str(data.get("privacy_level") or "normal"),  # type: ignore[arg-type]
            raw=bool(data.get("raw", True)),
            source_signal_id=str(data.get("source_signal_id") or ""),
            evidence_ids=[str(item) for item in data.get("evidence_ids") or [] if str(item).strip()],
            tags=[str(item) for item in data.get("tags") or [] if str(item).strip()],
            metadata=dict(data.get("metadata") or {}),
        )


class EventLedger:
    """Kho append-only cho raw event.

    Không có method update/delete public. Nếu muốn sửa, ghi event repair mới.
    Quá khứ mà sửa trực tiếp thì audit layer trở thành truyện cổ tích.
    """

    def __init__(self, path: str, *, max_records: int = 5000):
        self.path = path
        self.max_records = max(100, int(max_records))

    def load(self) -> list[EventLedgerRecord]:
        if not os.path.isfile(self.path) or os.path.getsize(self.path) <= 0:
            return []
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        raw_items = data.get("records") if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            return []
        return [EventLedgerRecord.from_dict(item) for item in raw_items if isinstance(item, dict)]

    def save(self, records: list[EventLedgerRecord]) -> None:
        kept = records[-self.max_records :]
        atomic_write_json(self.path, {"version": 1, "records": [item.to_dict() for item in kept]})

    def append(self, record: EventLedgerRecord) -> EventLedgerRecord:
        # Không persist event ephemeral/session_only vào đĩa — đây là log tạm,
        # không phải ký ức. Lưu xuống file chỉ làm phình ledger vô ích.
        metadata = record.metadata or {}
        is_ephemeral = (
            bool(metadata.get("ephemeral_presence"))
            or str(metadata.get("decay_policy", "")) == "session_only"
            or "session_only" in record.tags
            or "ephemeral_presence" in record.tags
            or "signal_buffer_only" in record.tags
        )
        if is_ephemeral:
            return record  # Trả record nhưng không ghi đĩa
        records = self.load()
        records.append(record)
        self.save(records)
        return record

    def append_raw_event(
        self,
        *,
        frame_id: str,
        source: str,
        content: Any,
        source_signal_id: str = "",
        evidence_ids: Iterable[str] = (),
        reliability: float = 1.0,
        privacy_level: PrivacyLevel = "normal",
        tags: Iterable[str] = (),
        metadata: dict[str, Any] | None = None,
    ) -> EventLedgerRecord:
        record = EventLedgerRecord(
            id=f"evt_{uuid.uuid4().hex[:16]}",
            timestamp=now_ts(),
            iso_timestamp=iso_now(),
            frame_id=frame_id,
            source=source or "unknown",
            content=content,
            reliability=max(0.0, min(1.0, float(reliability))),
            privacy_level=privacy_level,
            raw=True,
            source_signal_id=source_signal_id,
            evidence_ids=[str(item) for item in evidence_ids if str(item).strip()],
            tags=[str(item) for item in tags if str(item).strip()],
            metadata=metadata or {},
        )
        return self.append(record)

    def append_from_signal(self, signal: Any, *, frame_id: str = "", evidence_ids: Iterable[str] = ()) -> EventLedgerRecord:
        """Tạo record từ RawSignal-like object mà không buộc import cortex.

        Dùng getattr để file này vẫn sống được khi bạn chưa nối core/evidence.py.
        """
        content = getattr(signal, "content", signal)
        return self.append_raw_event(
            frame_id=frame_id,
            source=str(getattr(signal, "source", "unknown") or "unknown"),
            content=content,
            source_signal_id=str(getattr(signal, "id", "") or ""),
            evidence_ids=evidence_ids,
            reliability=float(getattr(signal, "reliability", 1.0) or 1.0),
            privacy_level=str(getattr(signal, "privacy_level", "normal") or "normal"),  # type: ignore[arg-type]
            tags=("raw_signal",),
        )

    def recent(self, *, limit: int = 50, source: str | None = None) -> list[EventLedgerRecord]:
        records = self.load()
        if source:
            records = [item for item in records if item.source == source]
        return records[-max(1, limit) :]

    def by_frame(self, frame_id: str) -> list[EventLedgerRecord]:
        key = str(frame_id or "")
        return [item for item in self.load() if item.frame_id == key]

    def scrub_evidence(self, evidence_ids: Iterable[str]) -> int:
        """Xóa raw event liên quan khi người dùng yêu cầu hard-delete.

        Ledger bình thường là append-only. Quyền xóa dữ liệu cá nhân là ngoại lệ
        có chủ đích: audit không được đứng trên yêu cầu quên của người dùng.
        """
        wanted = {str(item) for item in evidence_ids if str(item).strip()}
        if not wanted:
            return 0
        records = self.load()
        kept: list[EventLedgerRecord] = []
        removed = 0
        for record in records:
            candidates = {record.id, *record.evidence_ids}
            if wanted & candidates:
                removed += 1
            else:
                kept.append(record)
        if removed:
            self.save(kept)
        return removed

    def query(self, text: str, *, limit: int = 20) -> list[EventLedgerRecord]:
        needle = compact_text(text, limit=160).lower()
        if not needle:
            return []
        found: list[EventLedgerRecord] = []
        for item in reversed(self.load()):
            haystack = compact_text(item.content, limit=1000).lower()
            if needle in haystack:
                found.append(item)
            if len(found) >= limit:
                break
        return list(reversed(found))

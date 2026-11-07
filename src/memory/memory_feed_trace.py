# -*- coding: utf-8 -*-
"""Trace V41 cho những gì memory thực sự được feed vào cognition/prompt.

File này cố tình KHÔNG lưu nguyên text memory. Trace chỉ giữ ID/layer/reason/score
để debug contamination mà không tạo thêm một bản sao dữ liệu người dùng. Rác audit
không nên được chữa bằng cách sinh thêm một bãi rác audit có full transcript.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)


class MemoryFeedTraceJournal:
    def __init__(self, path: str | os.PathLike[str] | None = None, *, max_records: int = 320) -> None:
        self.path = Path(path or os.getenv("ERIS_MEMORY_FEED_TRACE_PATH", "memory_core/memory_feed_trace.jsonl"))
        self.max_records = max(32, int(max_records))
        self._lock = RLock()
        self._append_since_compact = 0

    def append(self, event: Mapping[str, Any]) -> None:
        payload = {"schema_version": 1, "ts": time.time(), **dict(event)}
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                # Hot path chỉ append. Đừng đọc + rewrite cả journal ở mỗi retrieval, nếu không
                # một công cụ observability lại trở thành lý do chat chậm. Compact thưa và có giới hạn.
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                self._append_since_compact += 1
                if self._should_compact():
                    self._compact_locked()
        except Exception as exc:
            logger.debug("[memory feed] trace persist failed: %s", exc)

    def compact(self) -> int:
        """Giữ journal bounded; trả số dòng bị loại."""
        try:
            with self._lock:
                return self._compact_locked()
        except Exception as exc:
            logger.debug("[memory feed] compact failed: %s", exc)
            return 0

    def _should_compact(self) -> bool:
        if self._append_since_compact >= 32:
            return True
        try:
            return self.path.exists() and self.path.stat().st_size >= 2 * 1024 * 1024
        except OSError:
            return False

    def _compact_locked(self) -> int:
        if not self.path.exists():
            self._append_since_compact = 0
            return 0
        lines = [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        removed = max(0, len(lines) - self.max_records)
        keep = lines[-self.max_records:]
        if removed:
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
            tmp.replace(self.path)
        self._append_since_compact = 0
        return removed

    def record_retrieval(
        self,
        *,
        query: str,
        retrieved: int,
        admitted: Iterable[Mapping[str, Any]],
        rejected: Iterable[Mapping[str, Any]],
        rendered_ids: Iterable[str] = (),
        channel: str = "canonical",
    ) -> None:
        admitted_rows = [dict(x) for x in admitted]
        rejected_rows = [dict(x) for x in rejected]
        rendered = [str(x) for x in rendered_ids if str(x).strip()]
        normalized_query = " ".join(str(query or "").split())
        self.append({
            "kind": "memory_feed",
            "channel": channel,
            # Trace không sao chép user text. Hash đủ để correlate nhiều event cùng query.
            "query_hash": hashlib.sha256(normalized_query.encode("utf-8", errors="ignore")).hexdigest()[:16],
            "query_chars": len(normalized_query),
            "retrieved": int(retrieved),
            "admitted": len(admitted_rows),
            "rejected": len(rejected_rows),
            "rendered": len(rendered),
            "admitted_items": admitted_rows[:24],
            "rejected_items": rejected_rows[:24],
            "rendered_ids": rendered[:24],
        })
        logger.info(
            "[memory feed] channel=%s retrieved=%d admitted=%d rejected=%d rendered=%d reject_reasons=%s",
            channel,
            int(retrieved),
            len(admitted_rows),
            len(rejected_rows),
            len(rendered),
            _reason_counts(rejected_rows),
        )

    def recent(self, limit: int = 12) -> tuple[dict[str, Any], ...]:
        if not self.path.exists():
            return ()
        rows: list[dict[str, Any]] = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]:
                if line.strip():
                    value = json.loads(line)
                    if isinstance(value, dict):
                        rows.append(value)
        except Exception:
            return ()
        return tuple(rows)


def _reason_counts(rows: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        reason = str(row.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return ",".join(f"{key}:{value}" for key, value in sorted(counts.items())) or "none"


__all__ = ["MemoryFeedTraceJournal"]

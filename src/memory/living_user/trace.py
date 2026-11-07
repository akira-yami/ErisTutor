# -*- coding: utf-8 -*-
"""Trace journal và replay debugger cho living user memory.

Journal giải thích được tại sao một profile item xuất hiện, context nào đã được
inject và operation nào bị từ chối. Sensitive value được redacted trước khi ghi;
trace phục vụ debug, không phải một kho bí mật thứ hai đội lốt observability.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class MemoryTraceEvent:
    id: str
    kind: str
    timestamp: float
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "timestamp": self.timestamp, "payload": self.payload}


class MemoryTraceJournal:
    def __init__(self, path: str | os.PathLike[str], *, max_events: int = 2400) -> None:
        self.path = Path(path)
        self.max_events = max(100, int(max_events))
        self._lock = threading.RLock()

    def append(self, kind: str, payload: Mapping[str, Any]) -> MemoryTraceEvent:
        event = MemoryTraceEvent(f"mtr_{uuid.uuid4().hex[:18]}", str(kind), time.time(), _redact(dict(payload)))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
            self._trim_if_needed()
        return event

    def read(self, *, limit: int = 500, kinds: Iterable[str] | None = None) -> tuple[MemoryTraceEvent, ...]:
        if not self.path.is_file():
            return ()
        allowed = set(kinds or ())
        rows: list[MemoryTraceEvent] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        raw = json.loads(line)
                    except Exception:
                        continue
                    if allowed and str(raw.get("kind")) not in allowed:
                        continue
                    rows.append(MemoryTraceEvent(
                        id=str(raw.get("id") or ""), kind=str(raw.get("kind") or "unknown"),
                        timestamp=float(raw.get("timestamp") or 0.0), payload=dict(raw.get("payload") or {}),
                    ))
        except OSError:
            return ()
        return tuple(rows[-max(0, int(limit)):])

    def scrub_prefix(self, path_prefix: str) -> int:
        """Xóa trace có nhắc profile path bị hard-forget."""
        if not self.path.is_file():
            return 0
        events = self.read(limit=self.max_events * 2)
        kept, removed = [], 0
        for event in events:
            serialized = json.dumps(event.payload, ensure_ascii=False)
            if path_prefix and path_prefix in serialized:
                removed += 1
            else:
                kept.append(event)
        if removed:
            with self._lock:
                self.path.write_text("".join(json.dumps(x.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n" for x in kept), encoding="utf-8")
        return removed

    def _trim_if_needed(self) -> None:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) <= self.max_events:
            return
        self.path.write_text("\n".join(lines[-self.max_events:]) + "\n", encoding="utf-8")


@dataclass(frozen=True, slots=True)
class ReplayReport:
    event_count: int
    operation_count: int
    rejected_count: int
    context_compilations: int
    selected_paths: tuple[str, ...]
    timeline: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "operation_count": self.operation_count,
            "rejected_count": self.rejected_count,
            "context_compilations": self.context_compilations,
            "selected_paths": list(self.selected_paths),
            "timeline": list(self.timeline),
        }


class MemoryReplayDebugger:
    def __init__(self, journal: MemoryTraceJournal) -> None:
        self.journal = journal

    def report(self, *, limit: int = 500) -> ReplayReport:
        events = self.journal.read(limit=limit)
        operation_count = 0
        rejected_count = 0
        compilations = 0
        selected: list[str] = []
        timeline: list[str] = []
        for event in events:
            payload = event.payload
            if event.kind in {"observe_turn", "dream"}:
                operation_count += int(payload.get("operation_count") or 0)
                rejected_count += int(payload.get("rejected_count") or 0)
            if event.kind == "compile_context":
                compilations += 1
                selected.extend(str(x) for x in payload.get("selected_paths") or ())
            timeline.append(_render_event(event))
        return ReplayReport(
            event_count=len(events), operation_count=operation_count, rejected_count=rejected_count,
            context_compilations=compilations, selected_paths=tuple(dict.fromkeys(selected)),
            timeline=tuple(timeline[-120:]),
        )


def _render_event(event: MemoryTraceEvent) -> str:
    payload = event.payload
    if event.kind == "observe_turn":
        return f"observe: applied={payload.get('applied', 0)}, rejected={payload.get('rejected_count', 0)}, paths={payload.get('paths', [])}"
    if event.kind == "compile_context":
        return f"context: query={payload.get('query', '')!r}, selected={payload.get('selected_paths', [])}"
    if event.kind == "dream":
        return f"dream: applied={payload.get('applied', 0)}, episodes={payload.get('episode_ids', [])}"
    if event.kind in {"correct", "forget"}:
        return f"{event.kind}: path={payload.get('path', '')}, changed={payload.get('changed', 0)}"
    return f"{event.kind}: {payload}"


def _redact(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(fragment in lowered for fragment in ("password", "secret", "api_key", "token", "credential")):
        return "<redacted>"
    if isinstance(value, Mapping):
        sensitivity = str(value.get("sensitivity") or "")
        return {str(k): ("<redacted>" if sensitivity in {"private", "sensitive"} and str(k) in {"value", "old_value"} else _redact(v, key=str(k))) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(x, key=key) for x in value]
    return value


__all__ = ["MemoryReplayDebugger", "MemoryTraceEvent", "MemoryTraceJournal", "ReplayReport"]

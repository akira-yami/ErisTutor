# -*- coding: utf-8 -*-
"""Append-only semantic event journal for Eris's shared WorldState.

The journal is deliberately small and boring: every meaningful state edge is
appended as a new immutable event.  WorldState may be rebuilt from the latest
facts, while the journal answers the more important debugging question:
"why did Eris think that?"

Persistence is opt-in through ``ERIS_WORLD_EVENT_JOURNAL_PATH``.  When unset,
the journal remains process-local to avoid surprising disk writes.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


def _compact(value: Any, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[:limit]


@dataclass(frozen=True, slots=True)
class WorldEvent:
    event_id: str
    timestamp: float
    kind: str
    source: str
    payload: Mapping[str, Any]
    evidence_refs: tuple[str, ...] = ()
    world_revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["payload"] = dict(self.payload)
        data["evidence_refs"] = list(self.evidence_refs)
        return data


class AppendOnlyWorldJournal:
    """Thread-safe append-only event journal with bounded in-memory retention."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        max_events: int = 2048,
        path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.clock = clock
        self.max_events = max(128, int(max_events))
        self._events: deque[WorldEvent] = deque(maxlen=self.max_events)
        self._lock = threading.RLock()
        configured = str(path or os.environ.get("ERIS_WORLD_EVENT_JOURNAL_PATH", "") or "").strip()
        self.path = Path(configured).expanduser() if configured else None
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                self.path = None

    def append(
        self,
        kind: str,
        *,
        source: str,
        payload: Mapping[str, Any] | None = None,
        evidence_refs: Iterable[str] = (),
        timestamp: float | None = None,
        world_revision: int = 0,
    ) -> WorldEvent:
        event = WorldEvent(
            event_id=f"worldevt:{uuid.uuid4().hex[:16]}",
            timestamp=float(timestamp if timestamp is not None else self.clock()),
            kind=_compact(kind, 96) or "unknown",
            source=_compact(source, 96) or "runtime",
            payload=dict(payload or {}),
            evidence_refs=tuple(dict.fromkeys(_compact(item, 180) for item in evidence_refs if _compact(item, 180)))[:24],
            world_revision=max(0, int(world_revision or 0)),
        )
        with self._lock:
            self._events.append(event)
            path = self.path
        if path is not None:
            try:
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
            except Exception:
                # Journaling must never break perception/chat.
                pass
        return event

    def tail(self, limit: int = 32) -> tuple[WorldEvent, ...]:
        with self._lock:
            items = tuple(self._events)
        return items[-max(1, int(limit)) :]

    def find(self, *, kind: str = "", contains: str = "", limit: int = 32) -> tuple[WorldEvent, ...]:
        kind_key = _compact(kind, 96).casefold()
        needle = _compact(contains, 240).casefold()
        output: list[WorldEvent] = []
        for event in reversed(self.tail(self.max_events)):
            if kind_key and event.kind.casefold() != kind_key:
                continue
            if needle:
                haystack = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True).casefold()
                if needle not in haystack:
                    continue
            output.append(event)
            if len(output) >= max(1, int(limit)):
                break
        return tuple(reversed(output))


__all__ = ["AppendOnlyWorldJournal", "WorldEvent"]

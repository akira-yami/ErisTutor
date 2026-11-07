# -*- coding: utf-8 -*-
"""Semantic projection over the append-only raw event ledger.

This module never mutates ``memory.event_ledger`` and never stores an
interpretation in a raw event.  It only emits typed, evidence-bearing events.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from cortex.causal_affect_models import EventKind, ExogenousEvent, clamp01, compact_text

_ALLOWED_KINDS: set[str] = {
    "user_returned",
    "user_left",
    "user_ignored_turn",
    "promise_made",
    "promise_fulfilled",
    "promise_broken",
    "recognition_received",
    "correction_received",
    "shared_success",
    "continuity_break",
    "capability_shutdown_proposed",
}


def _read(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _presence(context: Any) -> float:
    return clamp01(_read(context, "user_presence", 0.0))


def _idle(context: Any) -> float:
    try:
        return max(0.0, float(_read(context, "idle_seconds", 0.0) or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _continuity_key(record: Any, current_context: Any) -> str:
    metadata = _read(record, "metadata", {}) or {}
    if isinstance(metadata, Mapping):
        for key in ("continuity_key", "project_id", "context_key"):
            value = compact_text(metadata.get(key, ""), 120)
            if value:
                return value
    for key in ("continuity_key", "project_id", "active_project", "window_key"):
        value = compact_text(_read(current_context, key, ""), 120)
        if value:
            return value
    return ""


def _evidence_refs(record: Any) -> tuple[str, ...]:
    refs: list[str] = []
    raw = _read(record, "evidence_ids", ()) or ()
    if isinstance(raw, str):
        raw = (raw,)
    for item in raw:
        clean = compact_text(item, 120)
        if clean and clean not in refs:
            refs.append(clean)
    for candidate in (_read(record, "source_signal_id", ""), _read(record, "id", ""), _read(record, "frame_id", "")):
        clean = compact_text(candidate, 120)
        if clean and clean not in refs:
            refs.append(clean)
    return tuple(refs[:10])


def _explicit_kind(record: Any) -> str:
    metadata = _read(record, "metadata", {}) or {}
    tags = tuple(str(item).strip() for item in (_read(record, "tags", ()) or ()) if str(item).strip())
    candidates: list[str] = []
    if isinstance(metadata, Mapping):
        candidates.extend(str(metadata.get(key, "") or "").strip() for key in ("event_kind", "semantic_kind", "kind"))
    candidates.extend(tags)
    for value in candidates:
        normalized = value.lower().replace("-", "_").replace(" ", "_")
        if normalized in _ALLOWED_KINDS:
            return normalized
    return ""


def _promise_is_explicit(record: Any) -> bool:
    metadata = _read(record, "metadata", {}) or {}
    if not isinstance(metadata, Mapping):
        return False
    scope = compact_text(metadata.get("promise_scope", metadata.get("scope", "")), 160)
    deadline = metadata.get("promise_deadline", metadata.get("deadline", None))
    quote = compact_text(metadata.get("promise_quote", metadata.get("verbatim", "")), 220)
    return bool(scope and (deadline is not None or quote))


def _facts(record: Any, kind: str, previous_context: Any, current_context: Any) -> tuple[str, ...]:
    content = _read(record, "content", "")
    metadata = _read(record, "metadata", {}) or {}
    facts: list[str] = []
    if isinstance(content, Mapping):
        for key, value in content.items():
            if value in (None, "", [], {}, ()):
                continue
            facts.append(f"{compact_text(key, 60)}={compact_text(value, 180)}")
            if len(facts) >= 6:
                break
    else:
        text = compact_text(content, 360)
        if text:
            facts.append(text)

    if kind in {"user_returned", "user_left"}:
        facts.append(f"user_presence changed {_presence(previous_context):.2f} -> {_presence(current_context):.2f}")
        facts.append(f"previous idle duration was {_idle(previous_context):.1f} seconds")
    if isinstance(metadata, Mapping):
        for key in ("delivery_success", "reply_expected", "promise_scope", "promise_deadline", "context_key"):
            if key in metadata and metadata[key] not in (None, ""):
                facts.append(f"{key}={compact_text(metadata[key], 180)}")
    out: list[str] = []
    for fact in facts:
        clean = compact_text(fact, 360)
        if clean and clean not in out:
            out.append(clean)
    return tuple(out[:8])


class EventSemanticizer:
    """Convert raw records and context diffs into conservative typed events."""

    def ingest(
        self,
        *,
        raw_records: tuple[Any, ...],
        previous_context: Any,
        current_context: Any,
        open_expectations: tuple[Any, ...] = (),
    ) -> tuple[ExogenousEvent, ...]:
        events: list[ExogenousEvent] = []
        expectation_ids = tuple(
            compact_text(_read(item, "expectation_id", ""), 120)
            for item in open_expectations
            if compact_text(_read(item, "expectation_id", ""), 120)
        )

        previous_presence = _presence(previous_context)
        current_presence = _presence(current_context)
        inferred_presence_kind = ""
        if previous_presence <= 0.15 and current_presence >= 0.65:
            inferred_presence_kind = "user_returned"
        elif previous_presence >= 0.65 and current_presence <= 0.15:
            inferred_presence_kind = "user_left"

        for index, record in enumerate(raw_records):
            kind = _explicit_kind(record)
            if not kind and index == len(raw_records) - 1:
                kind = inferred_presence_kind
            if not kind:
                continue
            if kind.startswith("promise_") and kind == "promise_made" and not _promise_is_explicit(record):
                # A vague "later" must not become a relationship contract.
                continue
            refs = _evidence_refs(record)
            facts = _facts(record, kind, previous_context, current_context)
            if not refs or not facts:
                continue
            record_id = compact_text(_read(record, "id", f"raw_{index}"), 120) or f"raw_{index}"
            actor = compact_text(_read(record, "metadata", {}).get("actor_id", "") if isinstance(_read(record, "metadata", {}), Mapping) else "", 120)
            actor = actor or ("user:primary" if kind.startswith("user_") or kind.startswith("promise_") or kind in {"recognition_received", "correction_received"} else "system:eris")
            object_id = _continuity_key(record, current_context)
            confidence = clamp01(_read(record, "reliability", 0.75), 0.75)
            if kind in {"user_returned", "user_left"}:
                confidence = max(confidence, abs(current_presence - previous_presence))
            events.append(
                ExogenousEvent(
                    event_id=f"sem_{record_id}",
                    kind=kind,  # type: ignore[arg-type]
                    actor_id=actor,
                    object_id=object_id,
                    timestamp=float(_read(record, "timestamp", 0.0) or 0.0),
                    facts=facts,
                    evidence_refs=refs,
                    confidence=confidence,
                    continuity_key=object_id,
                    related_expectation_ids=expectation_ids,
                    privacy_level=str(_read(record, "privacy_level", "normal") or "normal"),
                )
            )
        return tuple(events)


@dataclass(slots=True)
class TypedEventProjectionStore:
    """Small in-memory projection; raw ledger remains the source of truth."""

    max_events: int = 512
    _events: list[ExogenousEvent] = field(default_factory=list)

    def append(self, events: Iterable[ExogenousEvent]) -> tuple[ExogenousEvent, ...]:
        accepted = tuple(event for event in events if event.is_grounded)
        self._events.extend(accepted)
        if len(self._events) > self.max_events:
            self._events = self._events[-self.max_events :]
        return accepted

    def recent(self, limit: int = 50) -> tuple[ExogenousEvent, ...]:
        return tuple(self._events[-max(1, int(limit)) :])

    def by_continuity_key(self, key: str) -> tuple[ExogenousEvent, ...]:
        wanted = compact_text(key, 120)
        return tuple(event for event in self._events if event.continuity_key == wanted)


__all__ = ["EventSemanticizer", "TypedEventProjectionStore"]

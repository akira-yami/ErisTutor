# -*- coding: utf-8 -*-
"""Context-specific relationship expectations with bounded confidence updates."""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from cortex.causal_affect_models import ExogenousEvent, RelationshipExpectation, clamp01


class RelationshipExpectationStore:
    def __init__(self, expectations: Iterable[RelationshipExpectation] = ()) -> None:
        self._items = {item.expectation_id: item.clamp() for item in expectations}

    def all(self) -> tuple[RelationshipExpectation, ...]:
        return tuple(self._items.values())

    def upsert(self, expectation: RelationshipExpectation) -> RelationshipExpectation:
        expectation.clamp()
        self._items[expectation.expectation_id] = expectation
        return expectation

    def project(self, event: ExogenousEvent) -> tuple[RelationshipExpectation, ...]:
        if not event.is_grounded:
            return self.all()
        if event.kind == "promise_made":
            expectation_id = next(iter(event.related_expectation_ids), f"exp_{event.event_id}")
            self.upsert(RelationshipExpectation(
                expectation_id=expectation_id,
                subject_id=event.actor_id,
                context_key=event.continuity_key,
                expected_behavior="fulfil the explicit scoped promise within its tolerance window",
                confidence=max(0.35, event.confidence * 0.70),
                emotional_stake=0.42,
                tolerance_window_seconds=3600.0,
                supporting_event_ids=(event.event_id,),
                last_confirmed_at=event.timestamp,
            ))
        elif event.kind in {"promise_fulfilled", "user_returned"}:
            for item in list(self._items.values()):
                if event.related_expectation_ids and item.expectation_id not in event.related_expectation_ids:
                    continue
                if event.continuity_key and item.context_key and item.context_key != event.continuity_key:
                    continue
                updated = replace(
                    item,
                    confidence=clamp01(item.confidence + min(0.06, 0.03 + 0.03 * event.confidence)),
                    supporting_event_ids=tuple(dict.fromkeys((*item.supporting_event_ids, event.event_id)))[-12:],
                    last_confirmed_at=max(item.last_confirmed_at, event.timestamp),
                )
                self.upsert(updated)
        elif event.kind == "promise_broken":
            for item in list(self._items.values()):
                if event.related_expectation_ids and item.expectation_id not in event.related_expectation_ids:
                    continue
                updated = replace(
                    item,
                    confidence=clamp01(item.confidence - min(0.08, 0.04 + 0.04 * event.confidence)),
                    violating_event_ids=tuple(dict.fromkeys((*item.violating_event_ids, event.event_id)))[-12:],
                )
                self.upsert(updated)
        return self.all()


__all__ = ["RelationshipExpectation", "RelationshipExpectationStore"]

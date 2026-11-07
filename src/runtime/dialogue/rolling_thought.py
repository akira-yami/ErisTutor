# -*- coding: utf-8 -*-
"""Bounded semantic thought buffer, not a chain-of-thought log."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .contracts import RollingThoughtBuffer, ThoughtCandidate


class RollingThoughtService:
    def __init__(self, *, id_factory: Callable[[], str], clock: Callable[[], float], capacity: int = 5) -> None:
        if not 3 <= capacity <= 5:
            raise ValueError("rolling thought capacity must be between 3 and 5")
        self._id_factory = id_factory
        self._clock = clock
        self._capacity = capacity
        self._buffers: dict[str, RollingThoughtBuffer] = {}

    def get(self, session_id: str) -> RollingThoughtBuffer:
        buffer = self._buffers.get(session_id)
        if buffer is None:
            buffer = RollingThoughtBuffer(
                buffer_id=self._id_factory(),
                session_id=session_id,
                revision=0,
                capacity=self._capacity,
                candidates=(),
                last_updated_at=self._clock(),
            )
            self._buffers[session_id] = buffer
        return buffer

    def add(self, session_id: str, candidate: ThoughtCandidate) -> RollingThoughtBuffer:
        prior = self.get(session_id)
        now = self._clock()
        active = [item for item in prior.candidates if item.expiry_at > now and item.status == "active"]
        active = [item for item in active if item.continuity_key != candidate.continuity_key]
        active.append(candidate)
        active.sort(key=lambda item: (-(item.activation + item.novelty * 0.25), item.thought_id))
        updated = replace(
            prior,
            revision=prior.revision + 1,
            candidates=tuple(active[: prior.capacity]),
            last_updated_at=now,
        )
        self._buffers[session_id] = updated
        return updated

    def public_candidates(self, session_id: str) -> tuple[ThoughtCandidate, ...]:
        now = self._clock()
        return tuple(
            item for item in self.get(session_id).candidates
            if item.public_eligibility and item.expiry_at > now and item.status == "active"
        )

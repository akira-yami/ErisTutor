# -*- coding: utf-8 -*-
"""Unified discourse projection."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .contracts import DiscourseState


class DiscourseService:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._states: dict[str, DiscourseState] = {}

    def get(self, session_id: str) -> DiscourseState:
        state = self._states.get(session_id)
        if state is None:
            state = DiscourseState(
                session_id=session_id,
                revision=0,
                active_topic="",
                topic_stack=(),
                open_questions=(),
                unresolved_referents=(),
                pending_callbacks=(),
                last_user_act="",
                last_eris_act="",
                initiative_budget=1.0,
                interrupt_cost=0.0,
                cadence="normal",
                active_thread_ids=(),
                updated_at=self._clock(),
            )
            self._states[session_id] = state
        return state

    def update(
        self,
        session_id: str,
        *,
        active_topic: str | None = None,
        user_act: str | None = None,
        eris_act: str | None = None,
        open_questions: tuple[str, ...] | None = None,
        pending_callbacks: tuple[str, ...] | None = None,
        active_thread_ids: tuple[str, ...] | None = None,
        initiative_delta: float = 0.0,
        interrupt_cost: float | None = None,
    ) -> DiscourseState:
        prior = self.get(session_id)
        topic_stack = prior.topic_stack
        if active_topic is not None and active_topic != prior.active_topic:
            topic_stack = tuple((*topic_stack, prior.active_topic))[-8:] if prior.active_topic else topic_stack
        updated = replace(
            prior,
            revision=prior.revision + 1,
            active_topic=prior.active_topic if active_topic is None else active_topic,
            topic_stack=topic_stack,
            open_questions=prior.open_questions if open_questions is None else open_questions,
            pending_callbacks=prior.pending_callbacks if pending_callbacks is None else pending_callbacks,
            last_user_act=prior.last_user_act if user_act is None else user_act,
            last_eris_act=prior.last_eris_act if eris_act is None else eris_act,
            initiative_budget=max(0.0, min(1.0, prior.initiative_budget + initiative_delta)),
            interrupt_cost=prior.interrupt_cost if interrupt_cost is None else max(0.0, min(1.0, interrupt_cost)),
            active_thread_ids=prior.active_thread_ids if active_thread_ids is None else active_thread_ids,
            updated_at=self._clock(),
        )
        self._states[session_id] = updated
        return updated

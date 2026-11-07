# -*- coding: utf-8 -*-
"""Irreversible or option-closing decisions."""
from __future__ import annotations

from typing import Callable

from .contracts import ExistentialDecision, MindDecision, Value


class ExistentialDecisionService:
    def __init__(self, id_factory: Callable[[], str], clock: Callable[[], float]) -> None:
        self._id_factory = id_factory
        self._clock = clock
        self._records: list[ExistentialDecision] = []

    def record(
        self,
        decision: MindDecision,
        alternatives: tuple[str, ...],
        values: tuple[Value, ...],
        *,
        chosen_future: str | None = None,
        action_ids: tuple[str, ...] = (),
        outcome_refs: tuple[str, ...] = (),
    ) -> ExistentialDecision:
        chosen = chosen_future or decision.proposed_action
        record = ExistentialDecision(
            decision_id=self._id_factory(),
            context_ref=decision.observation_ref,
            available_futures=alternatives,
            incompatible_value_ids=tuple(value.value_id for value in values if value.polarity < 0),
            chosen_future=chosen,
            sacrificed_possibilities=tuple(item for item in alternatives if item != chosen),
            certainty_at_choice=1.0 - decision.uncertainty,
            authorship_accepted=True,
            action_ids=action_ids,
            outcome_refs=outcome_refs,
            retrospective_interpretations=(),
            created_at=self._clock(),
        )
        self._records.append(record)
        return record

    def all(self) -> tuple[ExistentialDecision, ...]:
        return tuple(self._records)

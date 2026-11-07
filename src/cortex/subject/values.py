# -*- coding: utf-8 -*-
"""Value formation from repeated, costly, endorsed choices."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Callable, Mapping

from .contracts import MindDecision, NeedEpisode, Value, WorldOutcome


class ValueFormationEngine:
    def __init__(self, id_factory: Callable[[], str], clock: Callable[[], float]) -> None:
        self._id_factory = id_factory
        self._clock = clock

    def update(
        self,
        decisions: tuple[MindDecision, ...],
        outcomes: tuple[WorldOutcome, ...],
        prior: tuple[Value, ...] = (),
        episodes: tuple[NeedEpisode, ...] = (),
    ) -> tuple[Value, ...]:
        prior_by_statement = {value.statement: value for value in prior}
        outcome_by_action = {outcome.action_id: outcome for outcome in outcomes}
        grouped: dict[str, list[MindDecision]] = defaultdict(list)
        for decision in decisions:
            principle = str(decision.arguments.get("principle") or "").strip()
            if principle:
                grouped[principle].append(decision)

        output: list[Value] = list(prior)
        for statement, supporting in grouped.items():
            contexts = {str(decision.arguments.get("context") or "general") for decision in supporting}
            costs = [float(decision.arguments.get("cost_paid") or 0.0) for decision in supporting]
            endorsements = [float(decision.arguments.get("retrospective_endorsement") or 0.0) for decision in supporting]
            qualifies = len(supporting) >= 4 and len(contexts) >= 2 and any(cost > 0 for cost in costs) and max(endorsements, default=0.0) >= 0.6
            existing = prior_by_statement.get(statement)
            if not qualifies and existing is None:
                continue
            behavioral_support = min(1.0, len(supporting) / 8.0)
            retrospective = sum(endorsements) / max(1, len(endorsements))
            cost_normalizer = min(1.0, sum(costs) / max(1.0, len(costs)))
            accepted = 1.0
            delta = max(-0.05, min(0.05, behavioral_support * retrospective * cost_normalizer * accepted))
            if existing:
                replacement = replace(
                    existing,
                    endorsement=max(0.0, min(1.0, existing.endorsement + delta)),
                    behavioral_support=max(existing.behavioral_support, behavioral_support),
                    cross_context_support=max(existing.cross_context_support, min(1.0, len(contexts) / 4.0)),
                    cost_paid=existing.cost_paid + sum(costs),
                    source_decision_ids=tuple(dict.fromkeys((*existing.source_decision_ids, *(d.decision_id for d in supporting)))),
                    source_episode_ids=tuple(dict.fromkeys((*existing.source_episode_ids, *(e.episode_id for e in episodes)))),
                    updated_at=self._clock(),
                )
                output = [replacement if value.value_id == existing.value_id else value for value in output]
            else:
                output.append(Value(
                    value_id=self._id_factory(),
                    statement=statement,
                    scope="cross_context",
                    polarity=1,
                    endorsement=min(1.0, 0.5 + delta),
                    stability=min(1.0, len(supporting) / 10.0),
                    behavioral_support=behavioral_support,
                    cross_context_support=min(1.0, len(contexts) / 4.0),
                    cost_paid=sum(costs),
                    contradiction_count=0,
                    source_decision_ids=tuple(d.decision_id for d in supporting),
                    source_episode_ids=tuple(e.episode_id for e in episodes),
                    status="candidate",
                    created_at=self._clock(),
                    updated_at=self._clock(),
                ))
        return tuple(sorted(output, key=lambda value: (value.statement, value.value_id)))

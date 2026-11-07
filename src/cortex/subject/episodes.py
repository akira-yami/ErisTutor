# -*- coding: utf-8 -*-
"""Need episode lifecycle."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .contracts import AttributionDistribution, NeedActivation, NeedEpisode, WorldOutcome


class NeedEpisodeRecorder:
    def __init__(self, id_factory: Callable[[], str], clock: Callable[[], float]) -> None:
        self._id_factory = id_factory
        self._clock = clock
        self._open: dict[str, NeedEpisode] = {}
        self._closed: list[NeedEpisode] = []

    def open(
        self,
        activation: NeedActivation,
        *,
        controllability: float,
        predictability: float,
        attribution: AttributionDistribution,
        substrate_before_ref: str,
        social_context_ids: tuple[str, ...] = (),
    ) -> NeedEpisode:
        existing = self._open.get(activation.need_id)
        if existing is not None:
            return existing
        episode = NeedEpisode(
            episode_id=self._id_factory(),
            need_id=activation.need_id,
            started_at=self._clock(),
            ended_at=None,
            deficit=activation.pressure,
            satisfaction=0.0,
            controllability=controllability,
            predictability=predictability,
            attribution=attribution,
            social_context_ids=social_context_ids,
            substrate_before_ref=substrate_before_ref,
            evidence_refs=activation.evidence_refs,
        )
        self._open[activation.need_id] = episode
        return episode

    def attach_action(self, need_id: str, action_id: str) -> NeedEpisode | None:
        episode = self._open.get(need_id)
        if episode is None:
            return None
        updated = replace(episode, action_ids=tuple(dict.fromkeys((*episode.action_ids, action_id))))
        self._open[need_id] = updated
        return updated

    def close(
        self,
        need_id: str,
        *,
        outcomes: tuple[WorldOutcome, ...],
        substrate_after_ref: str,
        delayed: bool = False,
    ) -> NeedEpisode | None:
        episode = self._open.pop(need_id, None)
        if episode is None:
            return None
        scores = [outcome.success_score for outcome in outcomes] or [0.0]
        satisfaction = sum(scores) / len(scores)
        outcome_ids = tuple(outcome.outcome_id for outcome in outcomes)
        closed = replace(
            episode,
            ended_at=self._clock(),
            satisfaction=satisfaction,
            recovery_quality=max(0.0, min(1.0, satisfaction * (0.5 + 0.5 * episode.controllability))),
            immediate_outcome_ids=() if delayed else outcome_ids,
            delayed_outcome_ids=outcome_ids if delayed else (),
            substrate_after_ref=substrate_after_ref,
            consolidation_state="pending",
        )
        self._closed.append(closed)
        return closed

    def close_all_for_action(self, action_id: str, *, outcome: WorldOutcome, substrate_after_ref: str) -> tuple[NeedEpisode, ...]:
        targets = [need_id for need_id, episode in self._open.items() if action_id in episode.action_ids]
        closed = [
            episode
            for need_id in targets
            if (episode := self.close(need_id, outcomes=(outcome,), substrate_after_ref=substrate_after_ref)) is not None
        ]
        return tuple(closed)

    def open_episodes(self) -> tuple[NeedEpisode, ...]:
        return tuple(self._open.values())

    def closed_episodes(self) -> tuple[NeedEpisode, ...]:
        return tuple(self._closed)

# -*- coding: utf-8 -*-
"""Temporal consolidation for episode residue and structural tendencies."""
from __future__ import annotations

import math
from collections import defaultdict
from statistics import median
from typing import Callable, Mapping

from .contracts import ConsolidationPatch, NeedEpisode


def decay_to_baseline(value: float, baseline: float, elapsed_seconds: float, half_life_seconds: float) -> float:
    if half_life_seconds <= 0:
        return baseline
    return baseline + (value - baseline) * (2.0 ** (-max(0.0, elapsed_seconds) / half_life_seconds))


class TemporalConsolidator:
    ALGORITHM_VERSION = "subject-consolidation-v1"

    def __init__(self, id_factory: Callable[[], str], clock: Callable[[], float]) -> None:
        self._id_factory = id_factory
        self._clock = clock

    def consolidate(
        self,
        window: tuple[NeedEpisode, ...],
        state: Mapping[str, float] | None = None,
        *,
        min_episode_count: int = 3,
        min_distinct_days: int = 2,
        min_evidence_coverage: float = 0.8,
    ) -> ConsolidationPatch:
        existing = dict(state or {})
        groups: dict[tuple[str, str, str], list[NeedEpisode]] = defaultdict(list)
        for episode in window:
            if not episode.is_closed or episode.consolidation_state == "consolidated":
                continue
            context = str(episode.social_context_ids[0] if episode.social_context_ids else "general")
            attribution = episode.attribution.dominant().value
            outcome_direction = "positive" if episode.satisfaction >= episode.deficit else "negative"
            groups[(episode.need_id, context, attribution + ":" + outcome_direction)].append(episode)

        deltas: dict[str, float] = {}
        source_ids: list[str] = []
        for (need_id, context, profile), episodes in sorted(groups.items()):
            distinct_days = {int(episode.started_at // 86400) for episode in episodes}
            evidence_coverage = sum(1 for episode in episodes if episode.evidence_refs) / len(episodes)
            if len(episodes) < min_episode_count or len(distinct_days) < min_distinct_days or evidence_coverage < min_evidence_coverage:
                continue
            outcomes = [episode.satisfaction - episode.deficit for episode in episodes]
            robust_mean = median(outcomes)
            span_days = (max(e.started_at for e in episodes) - min(e.started_at for e in episodes)) / 86400.0
            stability = min(1.0, (len(episodes) / 8.0) * (1.0 + min(span_days, 30.0) / 30.0) / 2.0)
            raw_step = robust_mean * stability
            step = max(-0.08, min(0.08, raw_step))
            key = f"{need_id}|{context}|{profile}"
            # The patch records the bounded delta, not a hidden rewrite of weights.
            if abs(step) > 1e-9:
                deltas[key] = step
                source_ids.extend(episode.episode_id for episode in episodes)
                existing[key] = max(-1.0, min(1.0, existing.get(key, 0.0) + step))

        return ConsolidationPatch(
            patch_id=self._id_factory(),
            algorithm_version=self.ALGORITHM_VERSION,
            source_episode_ids=tuple(dict.fromkeys(source_ids)),
            tendency_deltas=deltas,
            value_candidate_ids=(),
            derived_need_candidate_ids=(),
            created_at=self._clock(),
        )

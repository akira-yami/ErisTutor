# -*- coding: utf-8 -*-
"""Reflective mismatch detection without a privileged ``true_motive`` field."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from .contracts import ReflectiveMismatch


class ReflectiveMismatchDetector:
    def __init__(self, id_factory: Callable[[], str]) -> None:
        self._id_factory = id_factory

    def inspect(
        self,
        predictions: Mapping[str, float] | tuple[tuple[str, float], ...],
        actions: Mapping[str, float] | tuple[tuple[str, float], ...],
        narratives: tuple[str, ...],
        *,
        threshold: float = 0.35,
        contexts: tuple[str, ...] = (),
    ) -> tuple[ReflectiveMismatch, ...]:
        predicted = dict(predictions)
        observed = dict(actions)
        shared = sorted(set(predicted) & set(observed))
        if not shared:
            return ()
        error = sum(abs(float(predicted[key]) - float(observed[key])) for key in shared) / len(shared)
        if error < threshold:
            return ()
        return (ReflectiveMismatch(
            mismatch_id=self._id_factory(),
            narrative_belief_ids=narratives,
            observed_action_ids=tuple(shared),
            prediction_error=min(1.0, error),
            contexts=contexts,
            alternative_models=("accessible-evidence model underpredicted behavior", "narrative confidence should decrease"),
            status="open",
        ),)

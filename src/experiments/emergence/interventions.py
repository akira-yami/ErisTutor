# -*- coding: utf-8 -*-
"""Experiment-only interventions."""
from __future__ import annotations

from typing import Any, Mapping


def remove_personality_descriptions(instance: Any) -> None:
    method = getattr(instance, "disable_personality_descriptions", None)
    if callable(method):
        method()


async def apply_history(instance: Any, history: tuple[Mapping[str, Any], ...]) -> None:
    method = getattr(instance, "apply_history", None)
    if method is None:
        raise TypeError("experiment instance must implement apply_history")
    result = method(history)
    if hasattr(result, "__await__"):
        await result

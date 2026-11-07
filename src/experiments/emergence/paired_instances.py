# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
from typing import Any, Callable


async def clone_instance(factory: Callable[[str, int], Any], snapshot: str, *, seed: int) -> Any:
    instance = factory(snapshot, seed)
    if hasattr(instance, "__await__"):
        instance = await instance
    return instance

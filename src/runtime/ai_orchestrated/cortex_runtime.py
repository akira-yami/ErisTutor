# -*- coding: utf-8 -*-
"""Compatibility import cho Cortex package mới.

Code mới import từ ``runtime.ai_orchestrated.cortex``. File này chỉ giữ đường
import cũ trong một chu kỳ migration và không còn chứa logic hay class riêng.
"""
from runtime.ai_orchestrated.cortex import (
    CortexAttentionPolicy,
    CortexAttentionSignal,
    CortexDependencies,
    CortexGrowthCoordinator,
    CortexHeartbeatPolicy,
    CortexRuntime,
    CortexRuntimeTick,
)

__all__ = [
    "CortexAttentionPolicy",
    "CortexAttentionSignal",
    "CortexDependencies",
    "CortexGrowthCoordinator",
    "CortexHeartbeatPolicy",
    "CortexRuntime",
    "CortexRuntimeTick",
]

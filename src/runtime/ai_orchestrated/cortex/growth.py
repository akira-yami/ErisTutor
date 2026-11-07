# -*- coding: utf-8 -*-
"""Điều phối growth lane nền, tách khỏi hot path của Cortex."""
from __future__ import annotations

from typing import Any

from .models import CortexAttentionSignal


class CortexGrowthCoordinator:
    """Chạy growth khi có evidence và budget; lỗi không được hạ runtime chính."""

    def __init__(self, *, agent_mind: Any = None) -> None:
        self.agent_mind = agent_mind
        self._consolidator: Any = None

    def maybe_run(self, *, trigger: bool, attention: CortexAttentionSignal) -> None:
        try:
            from cortex.development_system import development_growth_enabled, get_default_development_system

            if not development_growth_enabled() or not trigger or not attention.consolidation_allowed:
                return
            from cortex.agent_mind_persistence import get_default_agent_mind_store
            from cortex.growth_consolidator import GrowthConsolidator
            from cortex.growth_evidence import observations_from_feedback

            store = self.agent_mind or get_default_agent_mind_store()
            store.reload_if_changed()
            observations, allowed = observations_from_feedback(store.last_feedback_events)
            if not observations:
                return
            system = get_default_development_system()
            system.profile.reload_if_changed()
            if self._consolidator is None:
                self._consolidator = GrowthConsolidator()
            system.tick(
                consolidator=self._consolidator,
                observations=observations,
                allowed_evidence_ids=allowed,
                idle_pressure=1.0,
                budget_allows=True,
                user_interacting_high_risk=attention.interrupt_cost >= 0.72,
            )
        except Exception:
            # Đây là lane nền. Im lặng ở boundary này có chủ đích; hot path không
            # được chết chỉ vì growth vừa quyết định thử nghiệm số phận.
            return


__all__ = ["CortexGrowthCoordinator"]

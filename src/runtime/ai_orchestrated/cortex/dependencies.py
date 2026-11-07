# -*- coding: utf-8 -*-
"""Composition object cho Cortex.

Mọi dependency mặc định được tạo tại một chỗ. Constructor runtime vẫn nhận các
tham số cũ để migration không biến thành trò rút dây điện lúc máy đang chạy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.di import resolve_dependency
from cortex.daydream_consolidation import DaydreamConsolidator
from cortex.endogenous_drive import EndogenousDriveEngine
from cortex.internal_agenda import InternalAgendaStack
from cortex.user_context_stream import UserContextStream
from runtime.model_budget_policy import ModelBudgetPolicy


@dataclass(slots=True)
class CortexDependencies:
    user_context_stream: UserContextStream
    endogenous_drive: EndogenousDriveEngine
    agenda_stack: InternalAgendaStack
    daydream: DaydreamConsolidator
    budget_policy: ModelBudgetPolicy
    agent_mind: Any = None
    logger: Any = None

    @classmethod
    def build(
        cls,
        *,
        user_context_stream: UserContextStream | None = None,
        endogenous_drive: EndogenousDriveEngine | None = None,
        agenda_stack: InternalAgendaStack | None = None,
        daydream: DaydreamConsolidator | None = None,
        budget_policy: ModelBudgetPolicy | None = None,
        agent_mind: Any = None,
        logger: Any = None,
    ) -> "CortexDependencies":
        return cls(
            user_context_stream=resolve_dependency(user_context_stream, UserContextStream),
            endogenous_drive=resolve_dependency(endogenous_drive, EndogenousDriveEngine),
            agenda_stack=resolve_dependency(agenda_stack, InternalAgendaStack),
            daydream=resolve_dependency(daydream, DaydreamConsolidator),
            budget_policy=resolve_dependency(budget_policy, ModelBudgetPolicy),
            agent_mind=agent_mind,
            logger=logger,
        )


__all__ = ["CortexDependencies"]

# -*- coding: utf-8 -*-
"""Composition root nhỏ cho learning kernel + tutor cognition.

Giữ việc wiring ở một chỗ để Coordinator không phải biết chi tiết constructor
của từng lớp. Một process chỉ nên có một LearningService và một TutorExecutive.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from runtime.learning_core import LearningService, LearningStore
from runtime.tutor.executive import TutorExecutive
from runtime.tutor.model_reasoner import RuntimeTutorReasoner
from runtime.tutor.session_store import TutorSessionLedger


@dataclass(frozen=True, slots=True)
class TutorRuntimeBundle:
    learning_service: LearningService
    tutor_executive: TutorExecutive


def build_tutor_runtime(
    *,
    model_router: Any = None,
    store: LearningStore | None = None,
    logger: Any = None,
    learner_client: Any = None,
) -> TutorRuntimeBundle:
    """Tạo đúng một learning truth + tutor executive cho process hiện tại."""
    learning = LearningService(store=store or LearningStore(), logger=logger,
                               retention_mode=os.getenv("ERIS_LEARNING_RETENTION_MODE", "shadow"))
    reasoner = None
    if callable(getattr(model_router, "invoke_structured_worker", None)):
        reasoner = RuntimeTutorReasoner(model_router, learner_client=learner_client, logger=logger)
    atomic_store = None
    try:
        from runtime.ai_orchestrated.atomic_state import atomic_store_from_env
        atomic_store = atomic_store_from_env(logger=logger)
    except Exception:
        atomic_store = None
    executive = TutorExecutive(
        learning,
        reasoner=reasoner,
        session_ledger=TutorSessionLedger(atomic_store=atomic_store, logger=logger),
        logger=logger,
    )
    return TutorRuntimeBundle(learning, executive)


__all__ = ["TutorRuntimeBundle", "build_tutor_runtime"]

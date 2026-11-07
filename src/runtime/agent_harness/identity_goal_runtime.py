# -*- coding: utf-8 -*-
"""Wiring một lần duy nhất từ autonomous heartbeat tới Subject + Work Lane."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from runtime.autonomy.goal_events import GoalEventLedger
from .identity_goal_driver import IdentityGoalDriver

_LOG = logging.getLogger(__name__)


def ensure_identity_goal_driver(host):
    existing = getattr(host, "_identity_goal_driver", None)
    if existing is not None:
        return existing
    subject = getattr(host, "subject_service", None)
    mind_loop = getattr(host, "subject_mind_loop", None)
    executor = getattr(host, "action_executor", None)
    if any(x is None for x in (subject, mind_loop, executor)):
        return None
    # Một instance UnifiedAgentSessions hiện hữu của ActionExecutor.
    service = getattr(executor, "_parallel_work_service", None)
    if service is None:
        # Khởi tạo qua capability P0/P1, không dựng một session manager khác.
        executor._parallel_work(json.dumps({"operation": "goals"}))
        service = getattr(executor, "_parallel_work_service", None)
    if service is None:
        return None
    ledger_path = Path(os.getenv("ERIS_AUTONOMY_V3_DB", "logs/eris_goals_v3.sqlite"))
    driver = IdentityGoalDriver(subject, mind_loop, service, ledger=GoalEventLedger(ledger_path))
    host._identity_goal_driver = driver
    return driver

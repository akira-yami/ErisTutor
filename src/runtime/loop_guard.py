# -*- coding: utf-8 -*-
"""Recovery policy for the long-lived autonomous loop."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.policy_settings import RunLoopSettings
from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry


@dataclass(slots=True)
class RuntimeLoopGuard:
    settings: RunLoopSettings
    health_registry: SubsystemHealthRegistry | None = None
    logger: Any | None = None
    consecutive_errors: int = 0
    total_errors: int = 0
    successful_iterations: int = 0

    def record_success(self) -> None:
        self.successful_iterations += 1
        recovered = self.consecutive_errors > 0
        self.consecutive_errors = 0
        if self.health_registry is not None and (recovered or self.successful_iterations == 1 or self.successful_iterations % 128 == 0):
            self.health_registry.record_success(
                "runtime.autonomous_loop",
                details=self.snapshot(),
            )

    def record_failure(self, error: BaseException) -> float:
        self.consecutive_errors += 1
        self.total_errors += 1
        delay = min(
            self.settings.error_backoff_max_seconds,
            self.settings.error_backoff_initial_seconds * (2 ** max(0, self.consecutive_errors - 1)),
        )
        if self.health_registry is not None:
            self.health_registry.record_failure(
                "runtime.autonomous_loop",
                error,
                kind=FailureKind.PROGRAMMER_ERROR,
                degraded=True,
                reason="autonomous iteration failed",
                details={**self.snapshot(), "next_backoff_seconds": delay},
            )
        return max(0.0, float(delay))

    def should_stop(self) -> bool:
        limit = int(self.settings.consecutive_error_stop_limit)
        return limit > 0 and self.consecutive_errors >= limit

    def snapshot(self) -> dict[str, int | float]:
        return {
            "consecutive_errors": self.consecutive_errors,
            "total_errors": self.total_errors,
            "successful_iterations": self.successful_iterations,
        }


__all__ = ["RuntimeLoopGuard"]

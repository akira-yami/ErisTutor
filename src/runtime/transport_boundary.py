# -*- coding: utf-8 -*-
"""Observable transport boundary for control-plane JSON messages.

The raw UDP transport is intentionally kept unchanged.  This wrapper adds a
single place for payload validation, health accounting and failure policy so
control handlers do not each grow their own ``try/except: pass`` shrine.
"""
from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from runtime.subsystem_health import FailureKind, HealthStatus, SubsystemHealthRegistry


class RuntimeTransportBoundary:
    def __init__(
        self,
        transport: Any,
        *,
        health_registry: SubsystemHealthRegistry | None = None,
        logger: Any | None = None,
        success_sample_every: int = 64,
    ) -> None:
        self.transport = transport
        self.health_registry = health_registry
        self.logger = logger
        self.success_sample_every = max(1, int(success_sample_every))
        self._lock = threading.RLock()
        self._sent = 0
        self._failed = 0
        self._last_message_type = ""

    def send_json(
        self,
        payload: Mapping[str, Any],
        *,
        channel: str = "control",
        required: bool = False,
    ) -> bool:
        if not isinstance(payload, Mapping):
            error = TypeError("transport payload must be a mapping")
            self._record_failure(channel, error, message_type="")
            if required:
                raise error
            return False

        envelope = dict(payload)
        message_type = str(envelope.get("type", "") or "").strip()[:120]
        try:
            self.transport.send_json(envelope)
        except Exception as exc:
            self._record_failure(channel, exc, message_type=message_type)
            if required:
                raise
            return False

        with self._lock:
            self._sent += 1
            self._last_message_type = message_type
            sent = self._sent
        self._record_success_if_needed(channel, sent=sent, message_type=message_type)
        return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "sent": self._sent,
                "failed": self._failed,
                "last_message_type": self._last_message_type,
            }

    def _record_failure(self, channel: str, error: BaseException, *, message_type: str) -> None:
        with self._lock:
            self._failed += 1
            failed = self._failed
            sent = self._sent
            self._last_message_type = message_type
        registry = self.health_registry
        if registry is not None:
            registry.record_failure(
                self._health_name(channel),
                error,
                kind=FailureKind.PROVIDER_TRANSIENT,
                degraded=True,
                reason="transport send failed",
                details={
                    "channel": str(channel or "control"),
                    "message_type": message_type,
                    "sent": sent,
                    "failed": failed,
                },
            )
        method = getattr(self.logger, "warning", None) if self.logger is not None else None
        if callable(method):
            try:
                method("[transport:%s] send type=%s failed: %s", channel, message_type or "unknown", error)
            except (AttributeError, TypeError, ValueError):
                pass

    def _record_success_if_needed(self, channel: str, *, sent: int, message_type: str) -> None:
        registry = self.health_registry
        if registry is None:
            return
        current = registry.get(self._health_name(channel))
        should_sample = current.status != HealthStatus.HEALTHY or sent == 1 or sent % self.success_sample_every == 0
        if not should_sample:
            return
        registry.record_success(
            self._health_name(channel),
            details={
                "channel": str(channel or "control"),
                "message_type": message_type,
                "sent": sent,
                "failed": self._failed,
            },
        )

    @staticmethod
    def _health_name(channel: str) -> str:
        normalized = "_".join(part for part in str(channel or "control").lower().split() if part)
        return f"runtime.transport.{normalized or 'control'}"


__all__ = ["RuntimeTransportBoundary"]

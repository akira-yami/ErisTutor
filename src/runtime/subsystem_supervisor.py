# -*- coding: utf-8 -*-
"""Small lifecycle supervisor for optional runtime subsystems.

It centralises start/stop error handling and health transitions without
pretending every object in the coordinator needs a dependency-injection
container and a conference talk.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from runtime.failure_taxonomy import FailureContext, classify_failure, failure_details
from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry

Factory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class SupervisedSubsystem:
    name: str
    service: Any
    stop_methods: tuple[str, ...]
    shutdown_failure_context: FailureContext = FailureContext.SHUTDOWN


class SubsystemSupervisor:
    def __init__(
        self,
        *,
        health_registry: SubsystemHealthRegistry,
        logger: Any | None = None,
    ) -> None:
        self.health_registry = health_registry
        self.logger = logger
        self._lock = threading.RLock()
        self._services: dict[str, SupervisedSubsystem] = {}
        self._order: list[str] = []

    def start(
        self,
        name: str,
        factory: Factory,
        *,
        required: bool = False,
        failure_kind: FailureKind | None = None,
        failure_context: FailureContext = FailureContext.STARTUP,
        stop_methods: Sequence[str] = ("shutdown", "close", "stop"),
        shutdown_failure_context: FailureContext = FailureContext.SHUTDOWN,
        details: Mapping[str, Any] | None = None,
    ) -> Any | None:
        normalized = self._name(name)
        self.health_registry.mark_starting(normalized, details=details)
        try:
            service = factory()
        except Exception as exc:
            classified = failure_kind or classify_failure(exc, context=failure_context)
            failure_payload = dict(details or {})
            failure_payload.update(failure_details(exc, context=failure_context))
            self.health_registry.record_failure(
                normalized,
                exc,
                kind=classified,
                degraded=not required,
                reason="subsystem startup failed",
                details=failure_payload,
            )
            self._log("error" if required else "warning", "[%s] startup failed: %s", normalized, exc)
            if required:
                raise
            return None
        if service is None:
            self.health_registry.mark_stopped(normalized, reason="disabled_or_unavailable")
            return None
        self.register(
            normalized,
            service,
            stop_methods=stop_methods,
            shutdown_failure_context=shutdown_failure_context,
            details=details,
        )
        return service

    def register(
        self,
        name: str,
        service: Any,
        *,
        stop_methods: Sequence[str] = ("shutdown", "close", "stop"),
        shutdown_failure_context: FailureContext = FailureContext.SHUTDOWN,
        details: Mapping[str, Any] | None = None,
    ) -> Any:
        normalized = self._name(name)
        record = SupervisedSubsystem(
            name=normalized,
            service=service,
            stop_methods=tuple(str(item) for item in stop_methods if str(item).strip()),
            shutdown_failure_context=shutdown_failure_context,
        )
        with self._lock:
            self._services[normalized] = record
            if normalized not in self._order:
                self._order.append(normalized)
        payload = dict(details or {})
        payload.setdefault("service_type", type(service).__name__)
        self.health_registry.record_success(normalized, details=payload)
        return service

    def get(self, name: str) -> Any | None:
        with self._lock:
            record = self._services.get(self._name(name))
            return None if record is None else record.service

    def stop(self, name: str) -> bool:
        normalized = self._name(name)
        with self._lock:
            record = self._services.get(normalized)
        if record is None:
            return False
        try:
            self._stop_record(record)
        except Exception as exc:
            kind = classify_failure(exc, context=record.shutdown_failure_context)
            self.health_registry.record_failure(
                normalized,
                exc,
                kind=kind,
                degraded=True,
                reason="subsystem shutdown failed",
                details=failure_details(exc, context=record.shutdown_failure_context),
            )
            self._log("warning", "[%s] shutdown failed: %s", normalized, exc)
            # Keep the record registered so a later shutdown attempt can retry.
            return False
        with self._lock:
            self._services.pop(normalized, None)
            self._order = [item for item in self._order if item != normalized]
        self.health_registry.mark_stopped(normalized)
        return True

    def shutdown_all(self) -> tuple[str, ...]:
        with self._lock:
            names = tuple(reversed(self._order))
        stopped: list[str] = []
        for name in names:
            if self.stop(name):
                stopped.append(name)
        return tuple(stopped)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "order": list(self._order),
                "services": {
                    name: {
                        "service_type": type(record.service).__name__,
                        "stop_methods": list(record.stop_methods),
                        "shutdown_failure_context": record.shutdown_failure_context.value,
                    }
                    for name, record in self._services.items()
                },
            }

    @staticmethod
    def _stop_record(record: SupervisedSubsystem) -> None:
        for method_name in record.stop_methods:
            method = getattr(record.service, method_name, None)
            if callable(method):
                method()
                return

    @staticmethod
    def _name(name: str) -> str:
        value = ".".join(part for part in str(name or "unknown").strip().split(".") if part)
        return value or "unknown"

    def _log(self, level: str, message: str, *args: Any) -> None:
        method = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(method):
            try:
                method(message, *args)
            except Exception:
                pass


__all__ = ["SubsystemSupervisor", "SupervisedSubsystem"]

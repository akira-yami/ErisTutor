# -*- coding: utf-8 -*-
"""Operational recovery boundary for durable engagement state.

The registry owns mechanics. This service owns the deliberate operator-facing
workflow: audit first, repair only when evidence is sufficient, and never hide
an unresolved delivery failure behind a green snapshot.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry

from .registry import EngagementRegistry


@dataclass(frozen=True, slots=True)
class EngagementRecoveryReport:
    status: str
    stream_valid: bool
    snapshot_valid: bool
    projection_mismatches: tuple[str, ...] = ()
    repaired_projections: tuple[str, ...] = ()
    incomplete_replays: tuple[str, ...] = ()
    open_dead_letters: tuple[str, ...] = ()
    pressure_reasons: tuple[str, ...] = ()
    receipt_errors: tuple[str, ...] = ()
    repaired_consumers: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return self.status == "healthy"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"healthy": self.healthy}


class EngagementRecoveryService:
    def __init__(
        self,
        registry: EngagementRegistry,
        *,
        health_registry: SubsystemHealthRegistry | None = None,
    ) -> None:
        self.registry = registry
        self.health_registry = health_registry

    def audit(self) -> EngagementRecoveryReport:
        stream = self.registry.audit_event_stream()
        receipts = self.registry.audit_delivery_receipts()
        projections = self.registry.audit_projections()
        mismatches = tuple(
            report.engagement_id
            for report in projections
            if report.complete and not report.state_matches
        )
        incomplete = tuple(
            report.engagement_id for report in projections if not report.complete
        )
        open_letters = tuple(
            item.letter_id for item in self.registry.dead_letters(status="open")
        )
        diagnostics = self.registry.diagnostics()
        pressure = diagnostics.get("delivery_pressure", {})
        pressure_reasons = tuple(str(item) for item in pressure.get("reasons", ()) or ())
        snapshot_valid = bool(self.registry.snapshot_integrity().get("valid", False))
        errors = tuple(stream.errors)
        if not stream.valid or not snapshot_valid or mismatches or not receipts.valid:
            status = "corrupt"
        elif open_letters or pressure_reasons:
            status = "degraded"
        else:
            status = "healthy"
        return EngagementRecoveryReport(
            status=status,
            stream_valid=stream.valid,
            snapshot_valid=snapshot_valid,
            projection_mismatches=mismatches,
            incomplete_replays=incomplete,
            open_dead_letters=open_letters,
            pressure_reasons=pressure_reasons,
            receipt_errors=receipts.errors,
            errors=errors,
        )

    def recover(
        self,
        *,
        repair_projections: bool = True,
        rechain_event_stream: bool = False,
        accept_snapshot: bool = False,
        repair_receipt_cursors: bool = True,
    ) -> EngagementRecoveryReport:
        before = self.audit()
        repaired: list[str] = []
        repaired_consumers: tuple[str, ...] = ()
        if rechain_event_stream and not before.stream_valid:
            self.registry.rechain_event_stream(reason="recovery_service")
        if repair_projections:
            for engagement_id in before.projection_mismatches:
                report = self.registry.repair_engagement_projection(engagement_id)
                if report.complete and report.state_matches:
                    repaired.append(engagement_id)
        if repair_receipt_cursors:
            repaired_consumers = self.registry.repair_receipt_cursors()
        if accept_snapshot and self.registry.audit_event_stream().valid:
            self.registry.accept_snapshot_integrity(reason="recovery_service")
        after = self.audit()
        result = EngagementRecoveryReport(
            status=after.status,
            stream_valid=after.stream_valid,
            snapshot_valid=after.snapshot_valid,
            projection_mismatches=after.projection_mismatches,
            repaired_projections=tuple(repaired),
            incomplete_replays=after.incomplete_replays,
            open_dead_letters=after.open_dead_letters,
            pressure_reasons=after.pressure_reasons,
            receipt_errors=after.receipt_errors,
            repaired_consumers=repaired_consumers,
            errors=after.errors,
        )
        if self.health_registry is not None:
            if result.status == "healthy":
                self.health_registry.record_success(
                    "engagement.recovery",
                    details={
                        "repaired_projections": list(result.repaired_projections),
                        "repaired_consumers": list(result.repaired_consumers),
                    },
                )
            else:
                self.health_registry.record_failure(
                    "engagement.recovery",
                    "; ".join(result.errors or result.pressure_reasons or (result.status,)),
                    kind=FailureKind.INVARIANT_VIOLATION,
                    degraded=True,
                    reason="engagement recovery audit is not healthy",
                    details=result.to_dict(),
                )
        return result


__all__ = ["EngagementRecoveryReport", "EngagementRecoveryService"]

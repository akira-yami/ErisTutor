# -*- coding: utf-8 -*-
"""Deterministic readiness assessment derived from runtime diagnostics."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class ReadinessStatus(str, Enum):
    READY = "ready"
    STARTING = "starting"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RuntimeReadiness:
    status: ReadinessStatus
    ready: bool
    reasons: tuple[str, ...] = ()
    critical_failures: tuple[str, ...] = ()
    degraded_components: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


class RuntimeReadinessEvaluator:
    """Collapse health, bootstrap, boundary and engagement invariants into one signal."""

    def __init__(
        self,
        *,
        critical_subsystems: Sequence[str] = (
            "runtime.coordinator",
            "ai_orchestrated.runtime",
        ),
    ) -> None:
        self.critical_subsystems = tuple(str(item).strip().lower() for item in critical_subsystems if str(item).strip())

    def evaluate(self, diagnostics: Mapping[str, Any]) -> RuntimeReadiness:
        health = diagnostics.get("health", {}) if isinstance(diagnostics.get("health"), Mapping) else {}
        bootstrap = diagnostics.get("bootstrap", {}) if isinstance(diagnostics.get("bootstrap"), Mapping) else {}
        boundaries = diagnostics.get("boundaries", {}) if isinstance(diagnostics.get("boundaries"), Mapping) else {}
        engagement = diagnostics.get("engagement", {}) if isinstance(diagnostics.get("engagement"), Mapping) else {}
        kernel = engagement.get("kernel", {}) if isinstance(engagement.get("kernel"), Mapping) else {}

        reasons: list[str] = []
        critical: list[str] = []
        degraded: list[str] = []
        health_overall = str(health.get("overall", "unknown") or "unknown").lower()
        subsystems = health.get("subsystems", ())
        if isinstance(subsystems, Sequence) and not isinstance(subsystems, (str, bytes, bytearray)):
            for item in subsystems:
                if not isinstance(item, Mapping):
                    continue
                name = str(item.get("name", "") or "").lower()
                status = str(item.get("status", "unknown") or "unknown").lower()
                if status == "failed" and any(name == prefix or name.startswith(prefix + ".") for prefix in self.critical_subsystems):
                    critical.append(name)
                elif status in {"failed", "degraded"}:
                    degraded.append(name)

        if health_overall == "failed" and not critical:
            reasons.append("health_failed")
        elif health_overall == "degraded":
            reasons.append("health_degraded")
        elif health_overall == "starting":
            reasons.append("health_starting")

        if not bool(bootstrap.get("ai_runtime_enabled", False)):
            degraded.append("ai_runtime_disabled")
        completed_phases = bootstrap.get("completed_phases", ())
        if isinstance(completed_phases, Sequence) and not isinstance(completed_phases, (str, bytes, bytearray)):
            if completed_phases and len(completed_phases) < 6:
                reasons.append("bootstrap_incomplete")

        for name, snapshot in boundaries.items():
            if isinstance(snapshot, Mapping) and snapshot.get("error"):
                degraded.append(f"boundary:{name}")

        violations = kernel.get("invariant_violations", ())
        if isinstance(violations, Sequence) and not isinstance(violations, (str, bytes, bytearray)) and violations:
            degraded.append("engagement_invariants")
            reasons.extend(f"engagement:{str(item)[:120]}" for item in violations[:4])
        integrity = kernel.get("event_integrity", {}) if isinstance(kernel.get("event_integrity"), Mapping) else {}
        if integrity and not bool(integrity.get("valid", False)):
            degraded.append("engagement_event_integrity")
            reasons.append("engagement_event_stream_invalid")
        receipt_integrity = (
            kernel.get("delivery_receipt_integrity", {})
            if isinstance(kernel.get("delivery_receipt_integrity"), Mapping) else {}
        )
        if receipt_integrity and not bool(receipt_integrity.get("valid", False)):
            degraded.append("engagement_delivery_receipts")
            reasons.extend(
                f"engagement_receipt:{str(item)[:120]}"
                for item in tuple(receipt_integrity.get("errors", ()) or ())[:4]
            )
        journal = engagement.get("snapshot_journal", {}) if isinstance(engagement.get("snapshot_journal"), Mapping) else {}
        if journal and bool(journal.get("has_snapshots", False)) and not bool(journal.get("primary_valid", False)):
            if bool(journal.get("backup_valid", False)):
                degraded.append("engagement_snapshot_primary")
                reasons.append("engagement_snapshot_primary_recovered")
            else:
                degraded.append("engagement_snapshot_journal")
                reasons.append("engagement_snapshot_journal_invalid")

        snapshot_integrity = (
            kernel.get("snapshot_integrity", {})
            if isinstance(kernel.get("snapshot_integrity"), Mapping) else {}
        )
        if snapshot_integrity and not bool(snapshot_integrity.get("valid", False)):
            degraded.append("engagement_snapshot_integrity")
            reasons.append(
                str(snapshot_integrity.get("error", "engagement_snapshot_invalid") or "engagement_snapshot_invalid")
            )
        try:
            dead_letter_count = int(
                kernel.get("open_dead_letter_count", kernel.get("dead_letter_count", 0)) or 0
            )
        except (TypeError, ValueError):
            dead_letter_count = 0
        if dead_letter_count > 0:
            degraded.append("engagement_dead_letters")
            reasons.append("engagement_delivery_dead_letters")
        pressure = kernel.get("delivery_pressure", {}) if isinstance(kernel.get("delivery_pressure"), Mapping) else {}
        if bool(pressure.get("degraded", False)):
            degraded.append("engagement_delivery_pressure")
            reasons.extend(
                f"engagement_pressure:{str(item)[:96]}"
                for item in tuple(pressure.get("reasons", ()) or ())[:4]
            )

        critical = list(dict.fromkeys(item for item in critical if item))
        degraded = list(dict.fromkeys(item for item in degraded if item))
        reasons = list(dict.fromkeys(item for item in reasons if item))
        if critical:
            status = ReadinessStatus.FAILED
            reasons.insert(0, "critical_subsystem_failed")
        elif degraded or health_overall in {"degraded", "failed"}:
            status = ReadinessStatus.DEGRADED
        elif "health_starting" in reasons or "bootstrap_incomplete" in reasons:
            status = ReadinessStatus.STARTING
        else:
            status = ReadinessStatus.READY
        return RuntimeReadiness(
            status=status,
            ready=status == ReadinessStatus.READY,
            reasons=tuple(reasons),
            critical_failures=tuple(critical),
            degraded_components=tuple(degraded),
        )


__all__ = ["ReadinessStatus", "RuntimeReadiness", "RuntimeReadinessEvaluator"]

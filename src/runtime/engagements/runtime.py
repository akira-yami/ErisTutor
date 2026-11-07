# -*- coding: utf-8 -*-
"""Facade connecting user turns, activity evidence, adapters and policy."""
from __future__ import annotations
from core.di import resolve_dependency

import json
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Mapping, Sequence

from runtime.policy_settings import EngagementSettings
from runtime.subsystem_health import SubsystemHealthRegistry

from .adapters import AdapterRegistry
from .consumer import EngagementEventConsumer
from .supervisor import EngagementConsumerSupervisor
from .contracts import (
    Engagement,
    EngagementProposal,
    FocusMode,
    InteractionDecision,
    InteractionSignal,
    LifecycleStatus,
)
from .detector import ControlIntent, EngagementDetector
from .evidence import EvidenceBinding, EvidenceEnvelope, EvidenceRouter
from .policy import AttentionManager, EngagementInteractionPolicy
from .profiles import EngagementProfile, EngagementProfileRegistry
from .registry import EngagementRegistry
from .recovery import EngagementRecoveryService


class EngagementRuntime:
    def __init__(
        self,
        *,
        initial_state: Mapping[str, Any] | None = None,
        persist=None,
        logger: Any | None = None,
        clock: Callable[[], float] | None = None,
        health_registry: SubsystemHealthRegistry | None = None,
        settings: EngagementSettings | None = None,
    ) -> None:
        self.logger = logger
        self._clock = clock or time.time
        self.health_registry = health_registry
        self.settings = resolve_dependency(settings, lambda: EngagementSettings())
        self.adapters = AdapterRegistry()
        self.profiles = EngagementProfileRegistry.defaults()
        self.registry = EngagementRegistry(
            self.adapters,
            initial_state=initial_state,
            persist=persist,
            clock=self._clock,
            health_registry=health_registry,
            max_events=self.settings.max_events,
            consumer_lag_warning_events=self.settings.consumer_lag_warning_events,
            open_dead_letter_warning_count=self.settings.open_dead_letter_warning_count,
            max_delivery_receipts=self.settings.max_delivery_receipts,
        )
        self.recovery = EngagementRecoveryService(
            self.registry, health_registry=health_registry
        )
        self.consumer_supervisor = EngagementConsumerSupervisor(
            health_registry=health_registry, clock=self._clock,
            poll_budget=self.settings.managed_consumer_poll_budget,
        )
        self.detector = EngagementDetector(self.profiles)
        self.policy = EngagementInteractionPolicy(
            signal_importance_threshold=self.settings.signal_importance_threshold
        )
        self.attention = AttentionManager()
        self.evidence_router = EvidenceRouter()
        self._lock = threading.RLock()
        self._last_activity_keys: set[str] = set()
        self._pending_output_engagement_id = ""
        if self.health_registry is not None:
            self.health_registry.record_success(
                "engagement.kernel",
                details={
                    "adapters": list(self.adapters.ids()),
                    "event_cursor": self.registry.event_cursor,
                },
            )

    def register_profile(self, profile: EngagementProfile, *, replace: bool = False) -> None:
        """Register a new long-running activity profile without changing the detector."""

        self.profiles.register(profile, replace=replace)

    def register_adapter(self, adapter) -> None:
        """Register the domain reducer/projection paired with a profile."""

        self.adapters.register(adapter)

    def ensure_profile(
        self,
        profile_id: str,
        *,
        objective: str,
        binding: EvidenceBinding | Mapping[str, Any] | None = None,
        now: float | None = None,
        source: str = "runtime",
    ) -> Engagement:
        """Return an active engagement for a domain resource, creating one if needed.

        External evidence bridges use this instead of pretending a synthetic chat
        message came from the user. Resource bindings keep simultaneous sessions
        from being merged merely because both happen to be chess, coding, or some
        other human pastime.
        """

        now = self._clock() if now is None else float(now)
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise KeyError(f"unknown engagement profile: {profile_id}")
        evidence_binding = EvidenceBinding.from_mapping(binding)
        for item in reversed(self.registry.all()):
            if item.kind != profile.kind or not item.active:
                continue
            if evidence_binding.resource_id and item.evidence_binding.resource_id == evidence_binding.resource_id:
                return item
            if not evidence_binding.resource_id and item.focus == profile.focus:
                return item
        proposal = EngagementProposal(
            semantic_key=f"{profile.kind}:{evidence_binding.resource_id or profile.profile_id}",
            kind=profile.kind,
            objective=" ".join(str(objective or profile.kind).split())[:360],
            adapter_id=profile.adapter_id,
            confidence=1.0,
            expected_duration="long_running",
            contract=profile.contract,
            continuity=profile.continuity,
            priority=profile.priority,
            focus=profile.focus,
            source=str(source or "runtime")[:64],
            evidence_binding=evidence_binding,
        )
        return self.registry.create(proposal, now=now)


    def _maintain(self, now: float) -> None:
        self.registry.reconcile_inactivity(now=now)
        self.registry.compact(
            now=now,
            terminal_retention_seconds=self.settings.terminal_retention_seconds,
            max_terminal_engagements=self.settings.max_terminal_engagements,
        )

    def read_events(
        self,
        consumer_id: str,
        *,
        cursor: int | None = None,
        limit: int | None = None,
    ):
        return self.registry.read_events(
            consumer_id,
            cursor=cursor,
            limit=self.settings.event_consumer_batch_size if limit is None else limit,
        )

    def acknowledge_events(self, consumer_id: str, cursor: int, *, now: float | None = None) -> int:
        return self.registry.acknowledge_events(consumer_id, cursor, now=now)

    def lease_events(
        self,
        consumer_id: str,
        *,
        now: float | None = None,
        limit: int | None = None,
    ):
        return self.registry.lease_events(
            consumer_id,
            now=now,
            lease_seconds=self.settings.delivery_lease_seconds,
            limit=self.settings.event_consumer_batch_size if limit is None else limit,
        )

    def fail_delivery(
        self,
        consumer_id: str,
        error: BaseException | str,
        *,
        now: float | None = None,
    ):
        return self.registry.fail_delivery(
            consumer_id,
            error,
            now=now,
            max_attempts=self.settings.delivery_max_attempts,
            backoff_initial_seconds=self.settings.delivery_backoff_initial_seconds,
            backoff_max_seconds=self.settings.delivery_backoff_max_seconds,
            max_dead_letters=self.settings.max_dead_letters,
        )

    def audit_recovery(self):
        return self.recovery.audit()

    def recover(
        self, *, repair_projections: bool = True, rechain_event_stream: bool = False,
        accept_snapshot: bool = False,
        repair_receipt_cursors: bool = True,
    ):
        return self.recovery.recover(
            repair_projections=repair_projections,
            rechain_event_stream=rechain_event_stream,
            accept_snapshot=accept_snapshot,
            repair_receipt_cursors=repair_receipt_cursors,
        )

    def reset_consumer_to_earliest(self, consumer_id: str):
        return self.registry.reset_consumer_to_earliest(consumer_id)

    def resolve_dead_letter(
        self, letter_id: str, *, resolution: str = "operator_resolved", now: float | None = None
    ):
        return self.registry.resolve_dead_letter(
            letter_id, resolution=resolution, now=now
        )

    def create_event_consumer(
        self,
        consumer_id: str,
        handler,
        *,
        clock=None,
        logger: Any | None = None,
        managed: bool = False,
        replace: bool = False,
    ) -> EngagementEventConsumer:
        """Build a consumer using the runtime's delivery policy and health boundary."""

        consumer = EngagementEventConsumer(
            self.registry,
            consumer_id,
            handler,
            settings=self.settings,
            health_registry=self.health_registry,
            clock=clock or self._clock,
            logger=self.logger if logger is None else logger,
        )
        if managed:
            self.consumer_supervisor.register(consumer, replace=replace)
        return consumer

    def poll_event_consumers(
        self, *, now: float | None = None, limit: int | None = None
    ):
        return self.consumer_supervisor.tick(now=now, limit=limit)

    def observe_user_message(
        self,
        text: str,
        *,
        now: float | None = None,
        surface_key: str = "",
        binding: EvidenceBinding | Mapping[str, Any] | None = None,
    ) -> Engagement | None:
        now = self._clock() if now is None else float(now)
        text = " ".join(str(text or "").split()).strip()
        self._maintain(now)
        if not text:
            return self.registry.foreground()
        observed_binding = EvidenceBinding.from_mapping(binding)
        if surface_key:
            observed_binding = observed_binding.merged({"surface_key": surface_key})
        with self._lock:
            current = self.registry.foreground() or next(iter(self.registry.active()), None)
            control = self.detector.control(text)
            if control.action:
                control_target = self._control_target(control, current=current)
                if control_target is not None:
                    target_lifecycle = {
                        "cancel": LifecycleStatus.CANCELLED,
                        "pause": LifecycleStatus.PAUSED,
                        "resume": LifecycleStatus.ACTIVE,
                        "complete": LifecycleStatus.COMPLETED,
                    }[control.action]
                    return self.registry.transition(
                        control_target.engagement_id,
                        target_lifecycle,
                        now=now,
                        detail=text,
                        source="user",
                        reason=f"explicit_{control.action}",
                    )

            proposal = self.detector.propose(text)
            if proposal is not None:
                if observed_binding.meaningful:
                    proposal = replace(proposal, evidence_binding=observed_binding)
                current = self.registry.create(proposal, now=now)
            elif current is not None and observed_binding.meaningful:
                current = self.registry.bind(
                    current.engagement_id,
                    observed_binding,
                    now=now,
                    source="chat",
                ) or current
            if current is None:
                current = self.registry.foreground()
            if current is not None and current.active:
                current = self.registry.append_event(
                    current.engagement_id,
                    "user.message_received",
                    now=now,
                    source="chat",
                    payload={"text": text},
                ) or current
            return current

    def observe_activity(self, activity: Any, *, now: float | None = None) -> tuple[str, ...]:
        now = self._clock() if now is None else float(now)
        self._maintain(now)
        active = self.registry.active()
        if not active:
            return ()
        new_keys = tuple(
            str(item) for item in tuple(getattr(activity, "new_event_keys", ()) or ())
            if str(item).strip()
        )
        unseen = tuple(key for key in new_keys if key not in self._last_activity_keys)
        self._last_activity_keys.update(unseen)
        if len(self._last_activity_keys) > self.settings.activity_key_cache_size:
            self._last_activity_keys = set(
                tuple(self._last_activity_keys)[-self.settings.activity_key_retention:]
            )
        if not unseen:
            return ()
        detail = str(
            getattr(activity, "phase", "")
            or getattr(activity, "activity_kind", "")
            or "activity changed"
        )
        evidence = tuple(
            ref for event in tuple(getattr(activity, "recent_events", ()) or ())[-4:]
            for ref in tuple(getattr(event, "evidence_refs", ()) or ())
            if str(ref).strip()
        )[:16]
        binding = EvidenceBinding(
            surface_key=str(getattr(activity, "surface_key", "") or "")[:160],
            app=str(getattr(activity, "app", "") or "").strip().lower()[:100],
            window_title=str(getattr(activity, "window_title", "") or "").strip().lower()[:180],
            activity_session_id=str(getattr(activity, "session_id", "") or "")[:120],
            tags=tuple(
                item for item in (
                    str(getattr(activity, "activity_kind", "") or "").strip().lower(),
                    str(getattr(activity, "phase", "") or "").strip().lower(),
                ) if item and item != "unknown"
            ),
        )
        envelope = EvidenceEnvelope(
            kind="activity.changed",
            source="activity_session",
            binding=binding,
            payload={"detail": detail, "event_keys": list(unseen), "binding": binding.to_dict()},
            evidence_refs=evidence,
        )
        foreground = self.registry.foreground()
        targets = self.evidence_router.route(
            active,
            envelope,
            adapters=self.adapters,
            foreground_id=foreground.engagement_id if foreground is not None else "",
        )
        for engagement_id in targets:
            target = self.registry.get(engagement_id)
            if target is not None and not target.evidence_binding.meaningful and binding.meaningful:
                self.registry.bind(engagement_id, binding, now=now, source="activity_session")
            self.registry.append_event(
                engagement_id,
                envelope.kind,
                now=now,
                source=envelope.source,
                payload=envelope.payload,
                evidence_refs=envelope.evidence_refs,
            )
        return unseen

    def observe_assistant_output(
        self,
        text: str,
        *,
        now: float | None = None,
        engagement_id: str = "",
    ) -> None:
        now = self._clock() if now is None else float(now)
        text = " ".join(str(text or "").split()).strip()
        if not text:
            return
        target = None
        preferred_id = str(engagement_id or self._pending_output_engagement_id or "")
        if preferred_id:
            target = self.registry.get(preferred_id)
        if target is None:
            target = self.registry.foreground()
        if target is None:
            return
        self.registry.append_event(
            target.engagement_id,
            "assistant.spoke",
            now=now,
            source="public_surface",
            payload={"text": text[:360]},
        )
        if target.engagement_id == self._pending_output_engagement_id:
            self._pending_output_engagement_id = ""

    def bind_engagement(
        self,
        engagement_id: str,
        binding: EvidenceBinding | Mapping[str, Any],
        *,
        now: float | None = None,
        source: str = "runtime",
    ) -> Engagement | None:
        return self.registry.bind(engagement_id, binding, now=now, source=source)

    def emit_evidence_event(
        self,
        kind: str,
        *,
        payload: Mapping[str, Any] | None = None,
        binding: EvidenceBinding | Mapping[str, Any] | None = None,
        engagement_id: str = "",
        source: str = "domain",
        evidence_refs: Sequence[str] = (),
        now: float | None = None,
    ) -> tuple[Engagement, ...]:
        now = self._clock() if now is None else float(now)
        self._maintain(now)
        if engagement_id:
            target = self.registry.get(engagement_id)
            if target is None:
                return ()
            updated = self.registry.append_event(
                target.engagement_id,
                kind,
                now=now,
                source=source,
                payload=payload or {},
                evidence_refs=evidence_refs,
            )
            return (updated,) if updated is not None else ()

        envelope = EvidenceEnvelope(
            kind=str(kind or "domain.progress"),
            source=str(source or "domain"),
            binding=EvidenceBinding.from_mapping(binding),
            payload=dict(payload or {}),
            evidence_refs=tuple(str(item) for item in evidence_refs if str(item).strip())[:16],
        )
        foreground = self.registry.foreground()
        target_ids = self.evidence_router.route(
            self.registry.active(),
            envelope,
            adapters=self.adapters,
            foreground_id=foreground.engagement_id if foreground is not None else "",
        )
        results: list[Engagement] = []
        for target_id in target_ids:
            updated = self.registry.append_event(
                target_id,
                envelope.kind,
                now=now,
                source=envelope.source,
                payload=envelope.payload,
                evidence_refs=envelope.evidence_refs,
            )
            if updated is not None:
                results.append(updated)
        return tuple(results)

    def emit_domain_event(
        self,
        kind: str,
        *,
        payload: Mapping[str, Any] | None = None,
        engagement_id: str = "",
        binding: EvidenceBinding | Mapping[str, Any] | None = None,
        evidence_refs: Sequence[str] = (),
        now: float | None = None,
    ) -> Engagement | None:
        results = self.emit_evidence_event(
            kind,
            payload=payload,
            binding=binding,
            engagement_id=engagement_id,
            source="domain",
            evidence_refs=evidence_refs,
            now=now,
        )
        return results[0] if results else None

    def planner_context(self, *, now: float | None = None) -> dict[str, Any]:
        now = self._clock() if now is None else float(now)
        self._maintain(now)
        active = self.registry.active()
        projections = [
            self.adapters.get(item.adapter_id).project(item, now=now).to_dict()
            for item in active
        ]
        foreground = next(
            (item for item in projections if item.get("focus") == FocusMode.FOREGROUND.value),
            None,
        )
        resumable = [
            self.adapters.get(item.adapter_id).project(item, now=now).to_dict()
            for item in self.registry.all()
            if item.lifecycle == LifecycleStatus.PAUSED
        ][:3]
        return {
            "active": bool(projections),
            "revision": self.registry.revision,
            "event_cursor": self.registry.event_cursor,
            "foreground": foreground or {},
            "background": [
                item for item in projections
                if item.get("focus") != FocusMode.FOREGROUND.value
            ][:4],
            "resumable": resumable,
            "instruction": (
                "Preserve the active engagement's objective, role, interaction contract and lifecycle across turns. "
                "Route evidence only to the engagement whose binding matches it. Treat activity evidence as evidence, "
                "not invented domain state. Respect waiting, completion, cancellation and explicit resume signals. "
                "Do not silently abandon an active engagement."
                if projections else "No durable engagement is active."
            ),
        }

    def prompt_context(self, *, now: float | None = None) -> str:
        context = self.planner_context(now=now)
        if not context["active"]:
            return "active_engagement: none"
        return "ACTIVE_ENGAGEMENT:\n" + json.dumps(
            context,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def interaction_decision(
        self,
        *,
        now: float,
        seconds_since_user: float,
        seconds_since_assistant: float,
        telemetry: Mapping[str, Any] | None = None,
    ) -> InteractionDecision:
        self._maintain(now)
        decisions: list[InteractionDecision] = []
        candidates: list[Engagement] = list(self.registry.active())
        known = {item.engagement_id for item in candidates}
        candidates.extend(
            item for item in self.registry.pending_terminal()
            if item.engagement_id not in known
        )
        for engagement in candidates:
            signals = self._signals_for(
                engagement,
                self.registry.events_for(engagement.engagement_id),
                now=now,
            )
            decisions.append(self.policy.evaluate(
                engagement,
                signals,
                now=now,
                seconds_since_user=seconds_since_user,
                seconds_since_assistant=seconds_since_assistant,
                telemetry=telemetry,
            ))
        decision = self.attention.arbitrate(decisions)
        if decision.due and decision.engagement_id:
            self._pending_output_engagement_id = decision.engagement_id
        return decision

    def panel_view(self, *, now: float | None = None) -> dict[str, Any]:
        now = self._clock() if now is None else float(now)
        view = self.planner_context(now=now)
        view["diagnostics"] = self.registry.diagnostics(now=now)
        return view

    def diagnostics(self, *, now: float | None = None) -> dict[str, Any]:
        now = self._clock() if now is None else float(now)
        return {
            "kernel": self.registry.diagnostics(now=now),
            "consumer_supervisor": self.consumer_supervisor.snapshot(),
            "planner": self.planner_context(now=now),
            "profiles": list(self.profiles.ids()),
            "adapters": list(self.adapters.ids()),
            "pending_output_engagement_id": self._pending_output_engagement_id,
            "event_stream": {
                "latest_cursor": self.registry.event_cursor,
                "earliest_cursor": self.registry.earliest_event_cursor,
                "default_batch_size": self.settings.event_consumer_batch_size,
                "delivery_lease_seconds": self.settings.delivery_lease_seconds,
                "delivery_max_attempts": self.settings.delivery_max_attempts,
                "dead_letter_count": len(self.registry.dead_letters()),
                "open_dead_letter_count": len(
                    self.registry.dead_letters(status="open")
                ),
                "integrity": self.registry.audit_event_stream().to_dict(),
                "recovery": self.recovery.audit().to_dict(),
                "delivery_stop_on_gap": self.settings.delivery_stop_on_gap,
            },
        }

    def _control_target(
        self,
        control: ControlIntent,
        *,
        current: Engagement | None,
    ) -> Engagement | None:
        if control.action == "resume":
            allowed = (
                LifecycleStatus.PAUSED,
                LifecycleStatus.WAITING,
                LifecycleStatus.COMPLETED,
            )
            if control.target_kind:
                return self.registry.latest(kind=control.target_kind, lifecycles=allowed)
            if current is not None and current.lifecycle in allowed:
                return current
            return self.registry.latest(lifecycles=allowed)

        allowed = (LifecycleStatus.ACTIVE, LifecycleStatus.WAITING)
        if control.target_kind:
            return self.registry.latest(kind=control.target_kind, lifecycles=allowed)
        if current is not None and current.lifecycle in allowed:
            return current
        return self.registry.latest(lifecycles=allowed)

    def _signals_for(
        self,
        engagement: Engagement,
        events: Sequence[Any],
        *,
        now: float,
    ) -> tuple[InteractionSignal, ...]:
        signals: list[InteractionSignal] = []
        engagement_events = [
            event for event in events
            if event.engagement_id == engagement.engagement_id
        ]
        acknowledged_seq = max(
            (event.seq for event in engagement_events if event.kind == "assistant.spoke"),
            default=0,
        )
        for event in engagement_events[-24:]:
            if event.seq <= acknowledged_seq:
                continue
            if event.source == "lifecycle_controller" and event.payload.get("trigger_event"):
                continue
            age = max(0.0, now - event.timestamp)
            if age > max(300.0, engagement.continuity.maximum_quiet_seconds * 2.0):
                continue
            adapter = self.adapters.get(engagement.adapter_id)
            importance = float(adapter.signal_importance(event))
            if importance <= 0.0:
                continue
            signals.append(InteractionSignal(
                engagement_id=engagement.engagement_id,
                kind=event.kind,
                importance=importance,
                reason=event.kind,
                event_key=f"log:{event.log_seq}",
                evidence_refs=event.evidence_refs,
            ))
        return tuple(signals)


__all__ = ["EngagementRuntime"]

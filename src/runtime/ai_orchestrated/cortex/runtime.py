# -*- coding: utf-8 -*-
"""Pipeline heartbeat Cortex đã tách composition, policy và side-effect nền."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from core.di import resolve_dependency
from cortex.daydream_consolidation import DaydreamConsolidator, DaydreamPacket
from cortex.endogenous_drive import EndogenousDriveEngine
from cortex.internal_agenda import InternalAgendaStack
from cortex.observer_contracts import ObserverFrame, SignalDiff, SignalSnapshot
from cortex.user_context_stream import UserContextStream
from runtime.model_budget_policy import ModelBudgetPolicy, ResourceSnapshot

from .dependencies import CortexDependencies
from .growth import CortexGrowthCoordinator
from .models import CortexAttentionSignal, CortexRuntimeTick
from .policies import CortexAttentionPolicy, CortexHeartbeatPolicy


class CortexRuntime:
    """Cập nhật context, drive và consolidation; planner mới sở hữu lời public."""

    def __init__(
        self,
        *,
        dependencies: CortexDependencies | None = None,
        attention_policy: CortexAttentionPolicy | None = None,
        heartbeat_policy: CortexHeartbeatPolicy | None = None,
        growth_coordinator: CortexGrowthCoordinator | None = None,
        user_context_stream: UserContextStream | None = None,
        endogenous_drive: EndogenousDriveEngine | None = None,
        agenda_stack: InternalAgendaStack | None = None,
        daydream: DaydreamConsolidator | None = None,
        budget_policy: ModelBudgetPolicy | None = None,
        agent_mind: Any = None,
        logger: Any = None,
    ) -> None:
        deps = resolve_dependency(dependencies, lambda: CortexDependencies.build(
            user_context_stream=user_context_stream,
            endogenous_drive=endogenous_drive,
            agenda_stack=agenda_stack,
            daydream=daydream,
            budget_policy=budget_policy,
            agent_mind=agent_mind,
            logger=logger,
        ))
        self.dependencies = deps
        # Alias giữ cho call-site cũ đọc được, nhưng owner thật nằm trong deps.
        self.user_context_stream = deps.user_context_stream
        self.endogenous_drive = deps.endogenous_drive
        self.agenda_stack = deps.agenda_stack
        self.daydream = deps.daydream
        self.budget_policy = deps.budget_policy
        self.agent_mind = deps.agent_mind
        self.logger = deps.logger
        self.attention_policy = resolve_dependency(attention_policy, CortexAttentionPolicy)
        self.heartbeat_policy = resolve_dependency(heartbeat_policy, CortexHeartbeatPolicy)
        self.growth_coordinator = resolve_dependency(
            growth_coordinator,
            lambda: CortexGrowthCoordinator(agent_mind=self.agent_mind),
        )
        self.last_tick: CortexRuntimeTick | None = None

    def tick(
        self,
        *,
        signal_snapshot: SignalSnapshot | Mapping[str, Any] | None = None,
        signal_diff: SignalDiff | None = None,
        observer_frame: ObserverFrame | None = None,
        base_internal_state: Any = None,
        memory_packets: Iterable[DaydreamPacket] = (),
        user_text: str = "",
        channel: str = "autonomous",
        **_: Any,
    ) -> CortexRuntimeTick:
        snapshot = self._coerce_snapshot(signal_snapshot)
        user_context = self.user_context_stream.update(
            signal_snapshot=snapshot,
            signal_diff=signal_diff,
            observer_frame=observer_frame,
            user_text=user_text,
            channel=channel,
        )
        internal_state = self.endogenous_drive.update(
            user_context=user_context,
            base_internal_state=base_internal_state,
            agenda_stack=self.agenda_stack,
        )
        resource = self.budget_policy.capture(
            active_app=snapshot.active_app,
            active_window_title=snapshot.active_window_title,
        )
        attention = self._build_attention(user_context, internal_state, observer_frame, resource)
        trigger, trigger_reason = self.daydream.should_trigger(
            user_context=user_context,
            internal_state=internal_state,
        )
        daydream_result = self._run_daydream(
            trigger=trigger,
            trigger_reason=trigger_reason,
            attention=attention,
            resource=resource,
            user_context=user_context,
            memory_packets=memory_packets,
        )
        self.growth_coordinator.maybe_run(trigger=trigger, attention=attention)
        result = CortexRuntimeTick(
            user_context=user_context,
            internal_state=internal_state,
            attention=attention,
            daydream_result=daydream_result,
            resource_snapshot=resource,
            next_heartbeat_seconds=self._next_heartbeat(attention),
            debug={"daydream_trigger": trigger, "daydream_trigger_reason": trigger_reason},
        )
        self.last_tick = result
        return result

    @staticmethod
    def _coerce_snapshot(value: SignalSnapshot | Mapping[str, Any] | None) -> SignalSnapshot:
        return value if isinstance(value, SignalSnapshot) else SignalSnapshot.from_mapping(value or {})

    def _build_attention(
        self,
        user_context: Any,
        internal_state: Any,
        observer_frame: Any,
        resource: ResourceSnapshot,
    ) -> CortexAttentionSignal:
        """Compatibility wrapper; policy object mới sở hữu phép tính attention."""

        return self.attention_policy.evaluate(
            user_context=user_context,
            internal_state=internal_state,
            observer_frame=observer_frame,
            resource=resource,
            budget_policy=self.budget_policy,
        )

    def _next_heartbeat(self, attention: CortexAttentionSignal) -> float:
        """Compatibility wrapper cho call-site cũ."""

        return self.heartbeat_policy.next_seconds(attention)

    def _run_daydream(
        self,
        *,
        trigger: bool,
        trigger_reason: str,
        attention: CortexAttentionSignal,
        resource: ResourceSnapshot,
        user_context: Any,
        memory_packets: Iterable[DaydreamPacket],
    ) -> Any:
        if not trigger or not attention.consolidation_allowed:
            return None
        try:
            return self.budget_policy.run_queued(
                "subconscious_4b",
                self.daydream.consolidate,
                memory_packets,
                reason=f"daydream:{trigger_reason}",
                priority=10,
                timeout_seconds=0.5,
                resource_snapshot=resource,
                agenda_stack=self.agenda_stack,
                context={
                    "trigger": trigger_reason,
                    "user_context": user_context.prompt_hint(),
                    "attention_level": attention.level,
                },
            )
        except Exception as exc:
            self.agenda_stack.add(
                kind="resource_guard",
                summary=f"Daydream consolidation bị hoãn bởi scheduler/budget: {exc}",
                evidence_ids=("model_budget_policy",),
                pressure=0.42,
                urgency=0.22,
                confidence=0.8,
                speech_value=0.0,
                source="cortex_runtime",
                ttl_seconds=900,
                payload={"scheduler_error": str(exc)},
            )
            return None


__all__ = ["CortexRuntime"]

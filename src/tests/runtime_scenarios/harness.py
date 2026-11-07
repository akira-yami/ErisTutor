from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from runtime.engagements import EngagementRuntime, InteractionDecision
from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry

from .clock import FakeClock
from .fakes import FakePersistence, FakeProvider, FakeTransport


@dataclass(slots=True)
class RuntimeScenarioHarness:
    """Small deterministic shell around durable engagement behavior.

    It deliberately uses real engagement registry/policy code while replacing
    time, provider, transport and persistence.  Scenario tests therefore cover
    lifecycle and recovery without sleeping or calling external services.
    """

    clock: FakeClock = field(default_factory=FakeClock)
    provider: FakeProvider = field(default_factory=FakeProvider)
    transport: FakeTransport = field(default_factory=FakeTransport)
    persistence: FakePersistence = field(default_factory=FakePersistence)
    health: SubsystemHealthRegistry = field(init=False)
    runtime: EngagementRuntime = field(init=False)
    last_user_at: float = field(init=False, default=0.0)
    last_assistant_at: float = field(init=False, default=0.0)
    closed: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.health = SubsystemHealthRegistry(clock=self.clock.now)
        self.runtime = self._new_runtime(initial_state={})
        self.last_user_at = 0.0
        self.last_assistant_at = 0.0
        self.closed = False

    def _new_runtime(self, *, initial_state: Mapping[str, Any]) -> EngagementRuntime:
        return EngagementRuntime(
            initial_state=initial_state,
            persist=self.persistence.save,
            clock=self.clock.now,
            health_registry=self.health,
        )

    def user(self, text: str, *, surface_key: str = "chat") -> str:
        now = self.clock.now()
        self.last_user_at = now
        self.runtime.observe_user_message(text, now=now, surface_key=surface_key)
        try:
            reply = self.provider.generate(text, context=self.runtime.planner_context(now=now))
        except BaseException as exc:
            self.health.record_failure(
                "scenario.provider",
                exc,
                kind=FailureKind.PROVIDER_TRANSIENT,
                degraded=True,
            )
            raise
        self.health.record_success("scenario.provider")
        self.transport.send_json({"type": "assistant_text", "text": reply})
        self.runtime.observe_assistant_output(reply, now=now)
        self.last_assistant_at = now
        return reply

    def domain_event(
        self,
        kind: str,
        *,
        payload: Mapping[str, Any] | None = None,
        engagement_id: str = "",
    ) -> None:
        self.runtime.emit_domain_event(
            kind,
            payload=payload or {},
            engagement_id=engagement_id,
            now=self.clock.now(),
        )

    def advance(self, seconds: float) -> float:
        return self.clock.advance(seconds)

    def decision(self, *, telemetry: Mapping[str, Any] | None = None) -> InteractionDecision:
        now = self.clock.now()
        return self.runtime.interaction_decision(
            now=now,
            seconds_since_user=max(0.0, now - self.last_user_at) if self.last_user_at else 1e9,
            seconds_since_assistant=max(0.0, now - self.last_assistant_at) if self.last_assistant_at else 1e9,
            telemetry=telemetry,
        )

    def speak(self, text: str) -> None:
        now = self.clock.now()
        self.transport.send_json({"type": "assistant_text", "text": text})
        self.runtime.observe_assistant_output(text, now=now)
        self.last_assistant_at = now

    def restart(self) -> None:
        snapshot = self.runtime.registry.snapshot()
        self.runtime = self._new_runtime(initial_state=snapshot)
        self.health.record_restart("scenario.runtime", reason="deterministic restart")
        self.health.record_success("scenario.runtime")

    def shutdown(self) -> None:
        if self.closed:
            return
        try:
            self.persistence.save(self.runtime.registry.snapshot())
        except BaseException as exc:
            self.health.record_failure(
                "scenario.persistence",
                exc,
                kind=FailureKind.PERSISTENCE_FAILURE,
                degraded=True,
                reason="shutdown flush failed",
            )
        else:
            self.health.record_success("scenario.persistence", details={"shutdown_flush": True})
        self.health.mark_stopped("scenario.runtime")
        self.closed = True

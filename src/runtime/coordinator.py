# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

from domain import Decision
from failure_meta import is_technical_failure_meta
from runtime.autonomous_loop_runtime import AutonomousLoopRuntimeMixin
from runtime.chat_cycle import ChatCycleMixin
from runtime.coordinator_shutdown import CoordinatorShutdownMixin
from runtime.incoming_protocol_runtime import IncomingProtocolRuntimeMixin
from runtime.presence_controller import PresenceControllerMixin
from runtime.settings_control_runtime import SettingsControlRuntimeMixin
from runtime.user_control_runtime import UserControlRuntimeMixin
from runtime.runtime_settings_store import get_runtime_settings_store
from runtime.policy_settings import RuntimePolicySettings
from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry
from runtime.transport_boundary import RuntimeTransportBoundary
from runtime.incoming_dispatcher import IncomingMessageDispatcher
from runtime.open_loop_store import ensure_open_loop_store
from runtime.world_state import ensure_world_state_service


class RuntimeCoordinator(
    CoordinatorShutdownMixin,
    IncomingProtocolRuntimeMixin,
    UserControlRuntimeMixin,
    SettingsControlRuntimeMixin,
    PresenceControllerMixin,
    ChatCycleMixin,
    AutonomousLoopRuntimeMixin,
):
    """Integrated Runtime Coordinator connecting transport, models, and UI."""

    def __init__(
        self,
        *,
        logger: Any,
        transport: Any,
        llm_service: Any,
        parser: Any,
        telemetry_service: Any,
        memory_core: Any,
        body_running_fn: Callable[[], bool],
        action_executor: Any = None,
        memory_semantic_parser: Any = None,
    ):
        self.logger = logger
        self.transport = transport
        self.llm_service = llm_service
        self.parser = parser
        self.telemetry_service = telemetry_service
        self.memory_core = memory_core
        self.body_running_fn = body_running_fn
        self.action_executor = action_executor
        self.memory_semantic_parser = memory_semantic_parser

        self.running = True
        self.mood_score = 50
        self.inferred_pos_x = 0.5
        self.subconscious_runtime_enabled = False
        self.cognitive_substrate = type("SubstrateStub", (), {"stop": lambda self: None})()

        # Settings & Health
        try:
            self.runtime_settings_store = get_runtime_settings_store(logger=self.logger)
            self.runtime_settings = self.runtime_settings_store.load(materialize_env=False)
        except Exception:
            self.runtime_settings_store = None
            self.runtime_settings = {}

        self.runtime_policy_settings = RuntimePolicySettings.load()
        self.subsystem_health_registry = SubsystemHealthRegistry(
            max_events=self.runtime_policy_settings.health.max_events
        )

        # Transport & Dispatcher
        self.transport_boundary = RuntimeTransportBoundary(
            self.transport,
            health_registry=self.subsystem_health_registry,
            logger=self.logger,
        )
        self.incoming_dispatcher = IncomingMessageDispatcher(
            self,
            settings=self.runtime_policy_settings.protocol,
            health_registry=self.subsystem_health_registry,
            logger=self.logger,
        )

        # World state & open loop store
        self.world_state_service = ensure_world_state_service(self)
        self.open_loop_store = ensure_open_loop_store(self)

        # Mind & Cognition (Cortex)
        try:
            from cortex.internal_state import InternalStateEngine, PersistentInternalState
            from cortex.self_observation import SelfObservationEngine
            from cortex.self_model import SelfModelEngine
            from cortex.endogenous_drive import EndogenousDriveEngine, RollingInternalState
            from runtime.affective_dynamics_runtime import AffectiveDynamicsRuntime

            self.internal_state_engine = InternalStateEngine()
            self._internal_state = PersistentInternalState()
            self.internal_state = self._internal_state
            self.self_observer = SelfObservationEngine()
            self.self_model_engine = SelfModelEngine()
            self.endogenous_drive_engine = EndogenousDriveEngine()
            self.rolling_internal_state = RollingInternalState()
            self.affective_dynamics_runtime = AffectiveDynamicsRuntime(self, logger=self.logger)
        except Exception as exc:
            self.internal_state_engine = None
            self._internal_state = None
            self.internal_state = None
            self.affective_dynamics_runtime = None
            if self.logger is not None:
                self.logger.debug("[RuntimeCoordinator] Cortex state init degraded: %s", exc)

        # Subject Core (tự suy ngẫm / Subject)
        try:
            from runtime.subject_bootstrap import build_subject_core_components
            from runtime.subject_ingress import SubjectIngress

            self.subject_core_components = build_subject_core_components(
                action_executor=self.action_executor,
            )
            self.subject_service = self.subject_core_components.subject_service
            self.subject_sandbox_world = self.subject_core_components.sandbox_world
            self.subject_actuator = self.subject_core_components.actuator
            self.subject_mind_loop = self.subject_core_components.mind_loop
            self.subject_runtime = self.subject_core_components.runtime
            self.subject_ingress = SubjectIngress(self.subject_runtime)
        except Exception as exc:
            self.subject_core_components = None
            self.subject_service = None
            self.subject_mind_loop = None
            self.subject_runtime = None
            self.subject_ingress = None
            if self.logger is not None:
                self.logger.debug("[RuntimeCoordinator] Subject bootstrap degraded: %s", exc)

        # AI Orchestrated Runtime (Architecture C)
        try:
            from runtime.ai_orchestrated.bootstrap import install_ai_orchestrated_runtime
            install_ai_orchestrated_runtime(self, self.llm_service)
        except Exception as exc:
            self.logger.warning("[RuntimeCoordinator] Failed to install AI orchestrated runtime: %s", exc)

        # Socratic Tutor & Learning Runtime
        try:
            from runtime.tutor.composition import build_tutor_runtime
            from runtime.capabilities.tutor import TutorCapability

            self.tutor_runtime = build_tutor_runtime(
                model_router=getattr(self, "ai_model_router", None),
                logger=self.logger,
            )
            self.learning_service = self.tutor_runtime.learning_service
            self.tutor_executive = self.tutor_runtime.tutor_executive
            self.tutor_capability = TutorCapability(self.tutor_executive)
        except Exception as exc:
            self.tutor_runtime = None
            self.learning_service = None
            self.tutor_executive = None
            self.tutor_capability = None
            if self.logger is not None:
                self.logger.debug("[RuntimeCoordinator] Tutor runtime init degraded: %s", exc)

        self.logger.info("[RuntimeCoordinator] Initialized cleanly.")

    def bind_tts_engine(self, engine: Any) -> None:
        self.tts_engine = engine

    def _get_internal_state(self) -> Any:
        if getattr(self, "_internal_state", None) is None and getattr(self, "internal_state_engine", None) is not None:
            from cortex.internal_state import PersistentInternalState

            self._internal_state = PersistentInternalState()
        return getattr(self, "_internal_state", None)

    def _set_internal_state(self, state: Any) -> None:
        self._internal_state = state
        self.internal_state = state
        memory_state = getattr(self, "memory_state", None)
        if isinstance(memory_state, dict):
            memory_state["internal_state"] = (
                state.to_dict()
                if callable(getattr(state, "to_dict", None))
                else dict(getattr(state, "__dict__", {}) or {})
            )
        elif memory_state is not None and hasattr(memory_state, "internal_state"):
            memory_state.internal_state = state

    def _maybe_tick_subconscious_runtime(self, *args: Any, **kwargs: Any) -> None:
        pass

    def _stop_mental_process_loop(self) -> None:
        pass


__all__ = ["RuntimeCoordinator"]

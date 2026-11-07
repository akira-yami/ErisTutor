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

        # AI Orchestrated Runtime (Architecture C)
        try:
            from runtime.ai_orchestrated.bootstrap import install_ai_orchestrated_runtime
            install_ai_orchestrated_runtime(self, self.llm_service)
        except Exception as exc:
            self.logger.warning("[RuntimeCoordinator] Failed to install AI orchestrated runtime: %s", exc)

        self.logger.info("[RuntimeCoordinator] Initialized cleanly.")

    def bind_tts_engine(self, engine: Any) -> None:
        self.tts_engine = engine

    def _get_internal_state(self) -> Any:
        return None

    def _maybe_tick_subconscious_runtime(self, *args: Any, **kwargs: Any) -> None:
        pass

    def _stop_mental_process_loop(self) -> None:
        pass


__all__ = ["RuntimeCoordinator"]

# -*- coding: utf-8 -*-
"""Bootstrap for ErisTutor AI Orchestrated Runtime."""
from __future__ import annotations

import time
from typing import Any, Mapping

from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
from runtime.conversation.contracts import ConversationEvent
from runtime.conversation.bootstrap import ensure_conversation_kernel
from runtime.conversation.context import RootCapabilityRegistry
from runtime.tutor.composition import build_tutor_runtime
from runtime.capabilities.tutor import TutorCapability


class AIChatOrchestrator:
    """Lightweight clean bridge connecting ChatCycle directly to ConversationKernel V12."""

    def __init__(self, host: Any) -> None:
        self.host = host

    def run(
        self,
        user_text: str,
        *,
        cycle_start: float,
        previous_public_context: str = "",
        previous_public_turn: Any | None = None,
        speech_interrupt_context: str = "",
        speech_interrupt_event: Any | None = None,
        speech_interrupt_reaction: Any | None = None,
    ) -> Any:
        host = self.host
        sid = str(
            getattr(host, "conversation_id", "")
            or getattr(host, "thread_id", "")
            or "foreground:primary"
        ).strip()[:180]

        append_history = getattr(host, "_append_chat_history", None)
        if callable(append_history):
            append_history("user", str(user_text or ""))

        history_reader = getattr(host, "_chat_history_for_llm", None)
        if callable(history_reader):
            history = tuple(history_reader(limit=32))
        else:
            history = tuple(
                item for item in tuple(getattr(host, "chat_history", ()) or ())[-32:]
                if isinstance(item, Mapping)
            )

        event = ConversationEvent(
            event_id=f"user:{time.time_ns()}",
            session_id=sid,
            kind="user_turn",
            text=str(user_text or "")[:16000],
        )

        try:
            from runtime.tutor.auto_path import maybe_autostart_path

            maybe_autostart_path(
                host,
                user_text=str(user_text or ""),
                recent_history=history,
                now=cycle_start,
            )
        except Exception as exc:
            logger = getattr(host, "logger", None)
            if logger and callable(getattr(logger, "debug", None)):
                logger.debug("[orchestrator] tutor auto-path check: %s", exc)

        from runtime.conversation.context_assembler import ensure_conversation_context_assembler

        runtime_context = ensure_conversation_context_assembler(host).assemble(
            user_text=str(user_text or ""),
            channel="chat",
            previous_public_context=str(previous_public_context or ""),
            interrupt_context=str(speech_interrupt_context or ""),
            recent_history=history,
            event=event,
        )

        capabilities: dict[str, Any] = {}
        if getattr(host, "tutor_capability", None) is not None:
            capabilities["tutor"] = host.tutor_capability
        runtime_context["__root_capabilities"] = RootCapabilityRegistry(capabilities)

        kernel = ensure_conversation_kernel(host)
        result = kernel.handle_event(
            event,
            recent_history=history,
            runtime_context=runtime_context,
        )

        decision_dict = (
            result.decision.to_dict()
            if result.decision is not None and callable(getattr(result.decision, "to_dict", None))
            else {}
        )
        host.last_agent_turn = decision_dict
        host.last_conversation_run = {
            "schema": "eris.conversation_run.v12",
            "event_id": result.event.event_id,
            "generation": result.event.generation,
            "status": result.status,
            "failure_reason": result.failure_reason,
            "delivery_id": result.delivery_id,
            "decision": decision_dict,
        }
        return result


class AIAutonomousOrchestrator:
    """Lightweight clean bridge connecting AutonomousLoop directly to ConversationKernel V12."""

    def __init__(self, host: Any) -> None:
        self.host = host

    def run_startup(self) -> None:
        logger = getattr(self.host, "logger", None)
        if logger and callable(getattr(logger, "info", None)):
            logger.info("[AIAutonomousOrchestrator] Autonomous startup completed.")

    def run_iteration(self) -> bool:
        return bool(getattr(self.host, "running", True))

    def run_force_speak(self, text: str) -> Any:
        orchestrator = getattr(self.host, "ai_chat_orchestrator", None)
        if orchestrator is not None:
            return orchestrator.run(
                user_text=text,
                cycle_start=time.time(),
            )
        return None


def install_ai_orchestrated_runtime(host: Any, llm_service: Any) -> bool:
    """Production bootstrap for ErisTutor AI Orchestrated Runtime."""
    logger = getattr(host, "logger", None)

    # 1. AI Model Router
    router = AIRuntimeModelRouter(llm_service, logger=logger)
    host.ai_model_router = router

    # 2. Tutor Runtime & Capability
    try:
        learner_client = getattr(llm_service, "learner_client", None)
        tutor_bundle = build_tutor_runtime(
            model_router=router,
            learner_client=learner_client,
            logger=logger,
        )
        host.learning_service = tutor_bundle.learning_service
        host.tutor_executive = tutor_bundle.tutor_executive
        host.tutor_capability = TutorCapability(
            host.tutor_executive,
            logger=logger,
            living_user_source=lambda: getattr(getattr(host, "memory_core", None), "living_user", None),
        )
        if logger and callable(getattr(logger, "info", None)):
            logger.info("[bootstrap] Tutor Executive & Capability initialized cleanly.")
    except Exception as exc:
        if logger and callable(getattr(logger, "warning", None)):
            logger.warning("[bootstrap] tutor runtime setup failed: %s", exc)

    # 3. Conversation Kernel
    try:
        kernel = ensure_conversation_kernel(host)
        host.conversation_kernel = kernel
        if logger and callable(getattr(logger, "info", None)):
            logger.info("[bootstrap] Conversation Kernel initialized cleanly.")
    except Exception as exc:
        if logger and callable(getattr(logger, "warning", None)):
            logger.warning("[bootstrap] conversation kernel setup failed: %s", exc)

    # 4. Chat & Autonomous Orchestrators
    host.ai_chat_orchestrator = AIChatOrchestrator(host)
    host.ai_autonomous_orchestrator = AIAutonomousOrchestrator(host)
    host.ai_runtime_enabled = True

    if logger and callable(getattr(logger, "info", None)):
        logger.info("[bootstrap] AI orchestrated runtime installed successfully.")
    return True


__all__ = [
    "AIChatOrchestrator",
    "AIAutonomousOrchestrator",
    "install_ai_orchestrated_runtime",
]


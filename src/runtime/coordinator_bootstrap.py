# -*- coding: utf-8 -*-
"""Extracted coordinator runtime boundary.

The methods remain owner-oriented for backward compatibility while the main
coordinator becomes a small composition surface. New behavior should prefer
typed services over adding more methods to these compatibility mixins.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import re
import threading
import time
import traceback
import unicodedata
from collections.abc import Mapping
from types import SimpleNamespace

from actions.confirmation import public_action_text
from cortex.background_observer import BackgroundObserver, BackgroundObserverConfig
from cortex.daydream_consolidation import DaydreamConsolidator, DaydreamPacket
from cortex.endogenous_drive import EndogenousDriveEngine, RollingInternalState
from cortex.epistemic import EpistemicAgentMindProjection, EpistemicKernel, EpistemicKernelConfig, EpistemicStore, set_default_kernel
from cortex.internal_agenda import InternalAgendaStack
from cortex.observer_contracts import ObserverFrame, SemanticSceneNote, SignalSnapshot, stable_hash
from cortex.appetite_system import AppetiteSystem, build_evidence_buffet
from cortex.connection_drive import ConnectionDrive, ConnectionDriveState
from cortex.contracts import CognitiveDisturbance, PerceptEvent, SocialPresenceEvent, SubconsciousReport, ThoughtSurface, ThoughtStreamReport, TiPolicyReport
from cortex.internal_state import InternalStateEngine, PersistentInternalState
from cortex.micro_policy import MicroLinePolicy, RelationalContext, SemanticRepeatGuard
from cortex.subconscious import build_phase1234567_workers
from cortex.self_observation import SelfObservationEngine
from cortex.self_model import SelfModelEngine
from cortex.thought_stream import ThoughtStream
from cortex.user_context_stream import RollingUserContext, UserContextStream
from cortex.vision_semantic_compressor import RemoteCaptionClient, RemoteStructureClient, VisionCompressorConfig, VisionSemanticCompressor
from domain import Decision
from eris_secrets import get_config
from failure_meta import is_technical_failure_meta
from memory.memory_kernel import MemoryKernel
from runtime.architecture_flags import background_tutor_enabled, legacy_model_lanes_enabled
from runtime.diagnostic_logging import safe_log
from runtime.ai_orchestrated.cortex import CortexAttentionSignal, CortexRuntime
from runtime.active_concern_runtime import ActiveConcernRuntime
from runtime.expression_consummation import ExpressionConsummationRuntime
from runtime.expression_urge_runtime import ExpressionUrgeRuntime
from runtime.life_horizon import LifeHorizonManager
from runtime.conversation_courtesy import ConversationCourtesyContract
from runtime.horizon_arbiter import HorizonArbiter
from runtime.review_lock import ReviewLock
from runtime.mental_material_runtime import MentalMaterialRuntime
from runtime.body_bias import BodyBiasProjector
from runtime.chat_cycle import ChatCycleMixin
from runtime.failure_taxonomy import FailureContext, classify_failure, failure_details
from runtime.inner_diary_persistence import InnerDiaryPersister
from runtime.interrupt_ledger import InterruptLedger
from runtime.loop_guard import RuntimeLoopGuard
from runtime.model_budget_policy import ModelBudgetPolicy
from runtime.outcome_observer import OutcomeObserver
from runtime.policy_settings import RuntimePolicySettings
from runtime.private_life_daemon import PrivateLifeDaemon
from runtime.private_mind_daemon import PrivateMindDaemon
from runtime.public_payload_policy import sanitize_body_payload
from runtime.public_turn_ledger import PublicTurnLedger
from runtime.public_turn_runtime import PublicTurnRuntime
from runtime.repair_state import RepairStateController
from runtime.runtime_status import emit_runtime_status
from runtime.signal_buffer import SignalBuffer, SignalBufferConfig
from runtime.source_grounding import SourceGroundingEngine
from runtime.stage_contracts import StageFrameBuilder
from runtime.stage_dispatcher import StageDispatcher
from runtime.stage_event_bus import StageEventBus
from runtime.stage_output import StageOutputPipeline
from runtime.stage_protocol_adapter import playback_event, show_plan_event, stage_segment_event, stage_state_event
from runtime.subconscious_loop import SubconsciousLoop as RuntimeSubconsciousLoop
from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry
from runtime.subsystem_supervisor import SubsystemSupervisor
from runtime.thought_model_adapter import RuntimeThoughtModelAdapter
from runtime.transport_boundary import RuntimeTransportBoundary
from runtime.tts_sync_policy import sync_text_with_tts_enabled, text_sync_timeout_seconds, tts_event_releases_deferred_text
from runtime.vision_grounding import VisionGroundingPolicy, VisionGroundingReport
from runtime.voice_manager import VoiceManager
from runtime.vtuber_stage_version import VTUBER_STAGE_PHASES, VTUBER_STAGE_STATUS, VTUBER_STAGE_VERSION
from screen import build_screen_text_runtime


class CoordinatorBootstrapMixin:
    """Ordered bootstrap pipeline for the coordinator facade."""

    def __init__(
        self,
        *,
        logger,
        transport,
        llm_service,
        parser,
        telemetry_service,
        memory_core,
        body_running_fn,
        action_executor=None,
        memory_semantic_parser=None,
    ):
        self.bootstrap_completed_phases: list[str] = []
        self._bootstrap_foundation(
            logger=logger, transport=transport, llm_service=llm_service,
            parser=parser, telemetry_service=telemetry_service,
        )
        self._record_bootstrap_phase_success("foundation")
        self._run_bootstrap_phase(
            "memory_and_capabilities",
            self._bootstrap_memory_and_capabilities,
            memory_core=memory_core,
            memory_semantic_parser=memory_semantic_parser,
            llm_service=llm_service,
            body_running_fn=body_running_fn,
            action_executor=action_executor,
        )
        self._run_bootstrap_phase("hardening_and_state", self._bootstrap_hardening_and_state, memory_core=memory_core)
        self._run_bootstrap_phase("cognition_services", self._bootstrap_cognition_services, llm_service=llm_service)
        self._run_bootstrap_phase("session_and_perception", self._bootstrap_session_and_perception)
        self._run_bootstrap_phase("thought_and_ai", self._bootstrap_thought_and_ai, llm_service=llm_service)

    def _record_bootstrap_phase_success(self, phase: str) -> None:
        normalized = str(phase or "").strip().lower()
        if normalized and normalized not in self.bootstrap_completed_phases:
            self.bootstrap_completed_phases.append(normalized)
        registry = getattr(self, "subsystem_health_registry", None)
        if registry is not None:
            registry.record_success(
                f"runtime.bootstrap.{normalized}",
                details={"completed_phases": tuple(self.bootstrap_completed_phases)},
            )

    def _run_bootstrap_phase(self, phase: str, callback, /, **kwargs) -> None:
        normalized_phase = str(phase or "").strip().lower()
        name = f"runtime.bootstrap.{normalized_phase}"
        registry = getattr(self, "subsystem_health_registry", None)
        if registry is not None:
            registry.mark_starting(name)

        phase_labels = {
            "memory_and_capabilities": "Đang chuẩn bị…",
            "hardening_and_state": "Đang chuẩn bị…",
            "cognition_services": "Đang khởi động…",
            "session_and_perception": "Đang khởi động…",
            "thought_and_ai": "Sắp xong…",
        }
        emit_runtime_status(
            self,
            activity="startup",
            phase=normalized_phase,
            label=phase_labels.get(normalized_phase, "Đang khởi động…"),
            active=True,
            tone="info",
            priority=88,
        )
        try:
            callback(**kwargs)
        except Exception as exc:
            emit_runtime_status(
                self,
                activity="startup",
                phase=f"{normalized_phase}_failed",
                label="Khởi động bị lỗi",
                active=True,
                tone="error",
                priority=100,
            )
            if registry is not None:
                registry.record_failure(
                    name,
                    exc,
                    kind=classify_failure(exc, context=FailureContext.STARTUP),
                    degraded=False,
                    reason=f"bootstrap phase failed: {phase}",
                    details=failure_details(exc, context=FailureContext.STARTUP),
                )
            supervisor = getattr(self, "subsystem_supervisor", None)
            if supervisor is not None:
                supervisor.shutdown_all()
            raise
        self._record_bootstrap_phase_success(phase)

    def _bootstrap_foundation(self, *, logger, transport, llm_service, parser, telemetry_service) -> None:
        self.logger = logger
        self.transport = transport
        try:
            from runtime.runtime_settings_store import get_runtime_settings_store
            self.runtime_settings_store = get_runtime_settings_store(logger=self.logger)
            self.runtime_settings = self.runtime_settings_store.load(materialize_env=False)
            self.logger.info(
                "[runtime-settings] loaded path=%s version=%s",
                self.runtime_settings_store.path,
                self.runtime_settings.get("version", 0),
            )
        except Exception as exc:
            self.runtime_settings_store = None
            self.runtime_settings = {}
            self.logger.warning("Không khởi tạo được RuntimeSettingsStore: %s", exc)
        self.runtime_policy_settings = RuntimePolicySettings.load()
        self.subsystem_health_registry = SubsystemHealthRegistry(
            max_events=self.runtime_policy_settings.health.max_events
        )
        self.subsystem_supervisor = SubsystemSupervisor(
            health_registry=self.subsystem_health_registry,
            logger=self.logger,
        )
        self.transport_boundary = RuntimeTransportBoundary(
            transport,
            health_registry=self.subsystem_health_registry,
            logger=self.logger,
        )
        self.incoming_dispatcher = None
        self.loop_guard = RuntimeLoopGuard(
            settings=self.runtime_policy_settings.run_loop,
            health_registry=self.subsystem_health_registry,
            logger=self.logger,
        )
        from runtime.runtime_diagnostics import RuntimeDiagnostics
        self.runtime_diagnostics = RuntimeDiagnostics(self)
        self.subsystem_health_registry.mark_starting(
            "runtime.coordinator",
            details={"policy_settings": self.runtime_policy_settings.to_dict()},
        )
        try:
            from cortex.depth_flags import bootstrap_defaults

            states = bootstrap_defaults()
            self.logger.info("[depth] auto-enable cờ chiều-sâu: %s", states)
        except Exception as exc:  # pragma: no cover - defensive
            safe_log(self.logger, "warning", "[depth] bootstrap_defaults lỗi: %s", exc)
        self.llm_service = llm_service
        self.parser = parser
        self.telemetry_service = telemetry_service
        self.public_turn_ledger = PublicTurnLedger()
        self.public_turn_runtime = PublicTurnRuntime(
            self, settings=self.runtime_policy_settings.public_turn
        )
        self.interrupt_ledger = InterruptLedger()
        self.voice_manager = VoiceManager(ledger=self.interrupt_ledger)
        self.pending_speech_interrupt_event = None
        self.reply_context = SimpleNamespace(previous_public_turn=None)
        self.stage_event_bus = StageEventBus(transport=transport, logger=logger)
        try:
            self.logger.info(
                "[vtuber-stage] loaded version=%s phases=%s status=%s session=%s",
                VTUBER_STAGE_VERSION, ",".join(str(x) for x in VTUBER_STAGE_PHASES),
                VTUBER_STAGE_STATUS, self.stage_event_bus.session_snapshot().get("session_id", ""),
            )
        except Exception as exc:
            safe_log(self.logger, "debug", "Không ghi được stage bootstrap diagnostic: %s", exc)
        self.avatar_ready = False
        self.last_stage_inbound_event = None

    def _bootstrap_memory_and_capabilities(self, *, memory_core, memory_semantic_parser, llm_service, body_running_fn, action_executor) -> None:
        self.memory_core = memory_core
        if memory_semantic_parser is None:
            try:
                from runtime.runtime_settings_store import runtime_bool
                semantic_parser_enabled = runtime_bool("memory", "semantic_parser", default=False)
            except Exception:
                semantic_parser_enabled = False
        else:
            semantic_parser_enabled = False
        if memory_semantic_parser is None and semantic_parser_enabled:
            try:
                from runtime.memory_semantic_parser_adapter import build_runtime_memory_semantic_parser
            except Exception:
                try:
                    from .memory_semantic_parser_adapter import build_runtime_memory_semantic_parser
                except Exception as exc:
                    self.logger.warning("Không import được memory semantic parser adapter: %s", exc)
                    build_runtime_memory_semantic_parser = None
            if build_runtime_memory_semantic_parser is not None:
                try:
                    memory_semantic_parser = build_runtime_memory_semantic_parser(
                        llm_service=llm_service,
                        logger=self.logger,
                    )
                    if memory_semantic_parser is not None:
                        self.logger.info("Memory semantic parser đã bật cho background digest.")
                except Exception as exc:
                    self.logger.warning("Không khởi tạo được memory semantic parser: %s", exc)
                    memory_semantic_parser = None
        self.memory_kernel = memory_core if isinstance(memory_core, MemoryKernel) else None
        if self.memory_kernel is None and memory_core is not None:
            try:
                self.memory_kernel = MemoryKernel.from_existing_core(
                    memory_core,
                    semantic_parser=memory_semantic_parser,
                )
            except Exception as exc:
                self.logger.warning("Không bọc được memory kernel từ memory core hiện có: %s", exc)
                self.memory_kernel = None
        self.memory_portrait_presenter = None
        if self.memory_kernel is not None:
            self.memory_kernel._llm_service = llm_service
            living_user = getattr(self.memory_kernel, "living_user", None)
            if living_user is not None:
                try:
                    from memory.living_user import GeminiWebUserProfileAdapter
                    gemini_curator = getattr(llm_service, "curator_client", None)
                    if not isinstance(gemini_curator, GeminiWebUserProfileAdapter):
                        gemini_curator = GeminiWebUserProfileAdapter.create_if_available()
                    living_user.bind_model_adapter(gemini_curator)
                    if gemini_curator is not None:
                        self.logger.info("Living user memory model owner: Gemini Web service; model=%s.", gemini_curator.model_name)
                    else:
                        self.logger.info("Living user memory model owner: disabled; no DeepSeek fallback.")

                    # Gemini chỉ làm BIÊN TẬP VIÊN cho chân dung lưu đĩa.
                    # Không gọi model khi mở panel; Godot chỉ đọc snapshot.
                    from memory.living_user import SavedGeminiMemoryPortrait
                    self.memory_portrait_presenter = SavedGeminiMemoryPortrait(
                        living_user.base_dir, client=gemini_curator,
                    )
                    self.logger.info(
                        "Living user saved portrait: Gemini background writer=%s.",
                        "enabled" if gemini_curator is not None else "disabled",
                    )
                except Exception as exc:
                    # Deterministic profile vẫn chạy; model adapter chỉ là enrichment.
                    self.logger.warning("Không nối được living user model adapter: %s", exc)
        self.body_running_fn = body_running_fn
        self.action_executor = action_executor
        self.memory_semantic_parser = memory_semantic_parser
        def _build_internal_cognitive_capabilities():
            from runtime.internal_cognitive_capabilities import (
                INTERNAL_COGNITIVE_VERSION, InternalCognitiveCapabilityRuntime,
            )
            runtime = InternalCognitiveCapabilityRuntime(self, logger=self.logger)
            binder = getattr(self.action_executor, "bind_cognitive_runtime", None)
            if callable(binder):
                binder(runtime)
            self.logger.info(
                "[internal-cognitive] loaded version=%s capabilities=%s status=read_only_verified",
                INTERNAL_COGNITIVE_VERSION,
                len(runtime.contracts()),
            )
            return runtime
        self.internal_cognitive_capabilities = self.subsystem_supervisor.start(
            "runtime.internal_cognitive",
            _build_internal_cognitive_capabilities,
            failure_kind=FailureKind.PROGRAMMER_ERROR,
            stop_methods=(),
        )
        self.workspace_manager = None
        self.project_operations = None
        self.workspace_project_bundle = None
        def _build_workspace_project_bundle():
            from runtime.workspace_project_bundle import WorkspaceProjectBundle

            return WorkspaceProjectBundle.build(self, logger=self.logger)
        self.workspace_project_bundle = self.subsystem_supervisor.start(
            "runtime.project_operations",
            _build_workspace_project_bundle,
            failure_context=FailureContext.EXTERNAL_DATA,
            stop_methods=("close",),
            details={"mode": "autonomous_project_scope"},
        )
        if self.workspace_project_bundle is not None:
            self.workspace_manager = self.workspace_project_bundle.workspace_manager
            self.project_operations = self.workspace_project_bundle.project_operations
        try:
            from actions.capability_runtime import bind_runtime_owner

            bind_runtime_owner(self)
        except Exception as exc:
            self.logger.debug("[capability] runtime bind failed: %s", exc)
        self.turn_memory_pipeline = None
        self.background_memory_digest = None  # compatibility alias for older probes/callers
        try:
            from runtime.turn_memory_pipeline import build_turn_memory_pipeline

            def _build_turn_memory_pipeline():
                return build_turn_memory_pipeline(
                    memory_kernel=self.memory_kernel,
                    semantic_parser=memory_semantic_parser,
                    living_user=getattr(self.memory_kernel, "living_user", None) if self.memory_kernel is not None else None,
                    logger=self.logger,
                    idle_probe=self._background_memory_digest_idle_probe,
                )

            self.turn_memory_pipeline = self.subsystem_supervisor.start(
                "runtime.turn_memory_pipeline",
                _build_turn_memory_pipeline,
                failure_kind=FailureKind.PROGRAMMER_ERROR,
                stop_methods=("stop", "shutdown", "close"),
                details={"mode": "plan_then_background_commit"},
            )
            if self.turn_memory_pipeline is not None:
                self.background_memory_digest = getattr(self.turn_memory_pipeline, "semantic_digest", None)
                self.logger.info(
                    "[turn-memory] ready semantic=%s curator=%s",
                    int(self.background_memory_digest is not None),
                    int(bool(getattr(self.turn_memory_pipeline, "curator_enabled", False))),
                )
        except Exception as exc:
            self.logger.warning("Không khởi tạo được turn memory pipeline: %s", exc)
            self.turn_memory_pipeline = None
            self.background_memory_digest = None

    def _bootstrap_hardening_and_state(self, *, memory_core) -> None:
        self.internal_state_engine = InternalStateEngine()
        # V43: một owner duy nhất cho event -> affect state. Service này chỉ
        # mutate PersistentInternalState; writer/TTS/body chỉ đọc projection.
        try:
            from runtime.affective_dynamics_runtime import AffectiveDynamicsRuntime
            self.affective_dynamics_runtime = AffectiveDynamicsRuntime(self, logger=self.logger)
        except Exception as exc:
            self.affective_dynamics_runtime = None
            self.logger.warning("[affect-v43] runtime init failed: %s", exc)
        self.outcome_observer = OutcomeObserver()
        try:
            from runtime.observability import ErisObservability
            from runtime.rollout_control import RolloutController

            self.observability = ErisObservability()
            session_from_env = str(os.getenv("ERIS_RUNTIME_SESSION_ID", "") or "").strip()
            if session_from_env:
                self.runtime_session_id = session_from_env
            else:
                from pathlib import Path
                state_dir = Path(str(os.getenv("ERIS_STATE_DIR", ".eris_state") or ".eris_state")).expanduser()
                session_file = state_dir / "runtime_session_id.txt"
                persistent_id = ""
                if session_file.is_file():
                    try:
                        persistent_id = session_file.read_text(encoding="utf-8").strip()
                    except Exception:
                        persistent_id = ""
                if not persistent_id:
                    persistent_id = str(
                        getattr(self, "runtime_session_id", "")
                        or f"runtime-{os.getpid()}-{id(self)}"
                    )
                    try:
                        state_dir.mkdir(parents=True, exist_ok=True)
                        session_file.write_text(persistent_id, encoding="utf-8")
                    except Exception:
                        pass
                self.runtime_session_id = persistent_id
            os.environ.setdefault("ERIS_RUNTIME_SESSION_ID", self.runtime_session_id)
            self.rollout_controller = RolloutController(observability=self.observability)
            from runtime.hardening_rollout import HardeningHealthGate
            self.hardening_health_gate = HardeningHealthGate(self.rollout_controller)
            self._hardening_health_eval_counter = 0
        except Exception as exc:
            self.observability = None
            self.rollout_controller = None
            self.hardening_health_gate = None
            self._hardening_health_eval_counter = 0
            self.logger.warning("Observability/rollout init skipped: %s", exc)
        try:
            from runtime.hardening_config import HardeningConfig

            self.hardening_config = HardeningConfig.load()
        except Exception:
            self.hardening_config = None
        self.provider_registry = None
        try:
            self._initialize_provider_registry()
        except Exception as exc:
            self.logger.warning("Provider registry init skipped: %s", exc)
        self.hardening_release_status = {}
        try:
            from runtime.hardening_release import HardeningReleaseGuard, runtime_source_root

            guard_result = HardeningReleaseGuard(source_root=runtime_source_root()).verify()
            self.hardening_release_status = guard_result.to_dict()
            if self.observability is not None:
                self.observability.log(
                    "hardening_release_guard",
                    tags={"status": guard_result.status, "release": guard_result.release_id or "unversioned"},
                    fields=self.hardening_release_status,
                    status="error" if not guard_result.ready else "ok",
                )
            if not guard_result.ready:
                cfg = self.hardening_config
                message = f"Hardening release guard chưa đạt: {guard_result.status}"
                if bool(getattr(cfg, "release_guard_fail_closed", False)):
                    raise RuntimeError(message)
                self.logger.warning(message)
        except Exception as exc:
            if bool(getattr(getattr(self, "hardening_config", None), "release_guard_fail_closed", False)):
                raise
            self.logger.warning("Hardening release guard init skipped: %s", exc)
        self.phase7_validation = None
        try:
            if self.observability is not None:
                from runtime.phase7_validation import Phase7ValidationManager

                phase7_config = self.hardening_config
                if phase7_config is None:
                    from runtime.hardening_config import HardeningConfig
                    phase7_config = HardeningConfig.load()
                self.phase7_validation = Phase7ValidationManager(
                    observability=self.observability,
                    provider_health_fn=self.provider_health_snapshot,
                    rollout_status_fn=(
                        self.rollout_controller.status
                        if self.rollout_controller is not None and callable(getattr(self.rollout_controller, "status", None))
                        else (lambda: {})
                    ),
                    config=phase7_config,
                    release_id=str(os.getenv("ERIS_RELEASE_ID", "") or ""),
                    environment_fn=self._phase7_environment_snapshot,
                )
                self.phase7_validation.start(validation_id=f"{self.runtime_session_id}-phase7")
        except Exception as exc:
            self.logger.warning("Phase 7 validation init skipped: %s", exc)
        self.hardening_canary = None
        try:
            if self.observability is not None and self.rollout_controller is not None:
                from runtime.hardening_canary import HardeningCanaryManager
                self.hardening_canary = HardeningCanaryManager(
                    observability=self.observability,
                    rollout_controller=self.rollout_controller,
                    provider_health_fn=self.provider_health_snapshot,
                )
                self.hardening_canary.start(session_id=f"{self.runtime_session_id}-canary")
        except Exception as exc:
            self.logger.warning("Hardening canary init skipped: %s", exc)
        # V6 hard cutover: epistemic storage is always authoritative.  There is
        # no shadow/control branch and no archive-on-startup compatibility path.
        epistemic_path = ":memory:" if os.getenv("PYTEST_CURRENT_TEST") else None
        self.epistemic_kernel = EpistemicKernel(
            EpistemicStore(path=epistemic_path, mode="authoritative"),
            config=EpistemicKernelConfig(),
            logger=self.logger,
        )
        set_default_kernel(self.epistemic_kernel)
        self.epistemic_agent_mind = EpistemicAgentMindProjection(self.epistemic_kernel)
        self.logger.info("Epistemic V2 ready in authoritative mode.")
        self.state_committer = None
        if memory_core is not None:
            try:
                from runtime.state_committer import StateCommitter
                self.state_committer = StateCommitter(
                    memory_core,
                    logger=self.logger,
                    epistemic_kernel=self.epistemic_kernel,
                )
            except Exception as exc:
                self.logger.warning("Không khởi tạo được StateCommitter: %s", exc)
        # Subject Core is mandatory in V6.  Startup failure is fatal because a
        # silent fallback would create two incompatible sources of self-state.
        from runtime.subject_bootstrap import build_subject_core_components
        from runtime.subject_ingress import SubjectIngress

        self.subject_core_components = build_subject_core_components(
            action_executor=getattr(self, "action_executor", None),
            # Provider đóng trên host nên MindLoop được inject một dependency ổn
            # định ngay từ đầu; commit port production có thể xuất hiện sau mà không cần bind.
            committer_provider=lambda: getattr(self, "ai_executive_cortex", None),
        )
        self.subject_service = self.subject_core_components.subject_service
        self.subject_sandbox_world = self.subject_core_components.sandbox_world
        self.subject_actuator = self.subject_core_components.actuator
        self.subject_mind_loop = self.subject_core_components.mind_loop
        self.subject_runtime = self.subject_core_components.runtime
        from runtime.neuro_continuation import NeuroContinuationRuntime
        self.neuro_continuation_runtime = NeuroContinuationRuntime(
            committer_provider=lambda: getattr(self, "ai_executive_cortex", None),
            wake_callback=getattr(self, "_wake_autonomous_loop", None),
            logger=self.logger,
        )
        # P2: Subject Core giữ nguyên quyền nội bộ của nó, chỉ chiếu các
        # đề xuất chưa kiểm chứng lên inbox chung. Root/Kernel vẫn quyết lời.
        from runtime.proposal_bridge import mirror_subject_ideas
        self.subject_ingress = SubjectIngress(
            self.subject_service,
            mind_loop=self.subject_mind_loop,
            neuro_continuation=self.neuro_continuation_runtime,
            proposal_mirror=lambda items: mirror_subject_ideas(self, items),
        )
        self.logger.info(
            "[subject-core] authoritative subject=%s data=%s",
            self.subject_service.config.subject_id,
            self.subject_core_components.repository.database_path,
        )

    def _bootstrap_cognition_services(self, *, llm_service) -> None:
        self.self_observer = SelfObservationEngine()
        self.self_model_engine = SelfModelEngine()
        self.source_grounding_engine = SourceGroundingEngine()
        self.vision_grounding_policy = VisionGroundingPolicy()
        self.last_vision_grounding_report: VisionGroundingReport | None = None
        self.semantic_repeat_guard = SemanticRepeatGuard()
        self.micro_line_policy = MicroLinePolicy(repeat_guard=self.semantic_repeat_guard)
        self.body_bias_projector = BodyBiasProjector(self._get_internal_state)
        self._debug_overrides: dict[str, tuple[float, float]] = {}
        self.signal_buffer = SignalBuffer(SignalBufferConfig())
        self.sensory_model_adapter = None
        self.background_observer = BackgroundObserver(
            BackgroundObserverConfig(),
            sensory_adapter=self.sensory_model_adapter,
        )
        self.vision_semantic_compressor = VisionSemanticCompressor(
            VisionCompressorConfig(),
            structure_client=self._build_vision_structure_client(llm_service),
            caption_client=self._build_vision_caption_client(llm_service),
            logger=self.logger,
        )
        self.internal_agenda = InternalAgendaStack(
            backing_path=self._resolve_internal_agenda_path(),
        )
        self.user_context_stream = UserContextStream()
        self.endogenous_drive_engine = EndogenousDriveEngine()
        self.rolling_user_context = RollingUserContext()
        self.rolling_internal_state = RollingInternalState()
        self.model_budget_policy = ModelBudgetPolicy(logger=self.logger)
        self._install_model_inference_queue()
        self.last_cortex_attention: CortexAttentionSignal | None = None
        self.last_daydream_result = None
        self.daydream_model_adapter = None
        self.daydream_consolidator = DaydreamConsolidator(
            # Không mượn inner/style model ở startup mặc định. Daydream vẫn giữ
            # state local; model legacy chỉ quay lại khi flag migration được bật.
            llm_client=(
                getattr(llm_service, "inner_client", None) or getattr(llm_service, "style_client", None)
            ) if legacy_model_lanes_enabled() else None,
            logger=self.logger,
        )
        self.cortex_runtime = CortexRuntime(
            user_context_stream=self.user_context_stream,
            endogenous_drive=self.endogenous_drive_engine,
            agenda_stack=self.internal_agenda,
            daydream=self.daydream_consolidator,
            budget_policy=self.model_budget_policy,
            agent_mind=self.epistemic_agent_mind,
            logger=self.logger,
        )
        self.last_ai_intention_plan = None
        self.last_signal_snapshot: SignalSnapshot | None = None
        self.last_signal_diff = None
        self.last_observer_frame: ObserverFrame | None = None
        self.private_mind_daemon = PrivateMindDaemon.from_runtime(
            runtime_owner=self,
            llm_service=llm_service,
            logger=self.logger,
        )
        self.private_mind_loop = getattr(self.private_mind_daemon, "loop", None)
        self.private_mind_surface_queue: list[object] = getattr(self.private_mind_daemon, "surface_queue", []) if self.private_mind_daemon is not None else []
        self.private_mind_speech_bias: dict[str, object] = {}
        self.private_mind_inquiry_plan: list[dict[str, object]] = []
        self.private_mind_active_state: dict[str, object] = {}
        self.private_mind_thought_pressure = 0.0
        self.private_mind_digest: dict[str, object] = {}
        self.private_mind_daemon_digest: dict[str, object] = {}
        # V40: projection concern dùng lại ConcernStore hiện có; không tạo kho nhớ thứ hai.
        self.active_concern_runtime = ActiveConcernRuntime(self, logger=self.logger)
        # V69: semantic consummation giữ completion/receptivity của phần *đã nói*.
        # Nó không thay ConcernStore và không cấp quyền SPEAK; ExpressionUrge đọc
        # state này trước khi materialize typed ExpressionCommitment.
        self.expression_consummation_runtime = ExpressionConsummationRuntime(self, logger=self.logger)
        # V44: urge là projection ngắn hạn trên concern/content hiện có, không tạo kho nhớ mới.
        self.expression_urge_runtime = ExpressionUrgeRuntime(self, logger=self.logger)

        # V98: dựng "dây thần kinh" activation dùng chung ở SHADOW MODE.
        # Chưa thay public pipeline V97; motor chỉ quan sát body/micro/vocal urge.
        # Mọi nguồn cũ được bridge thành ModulationEvent, không được set field thẳng.
        from runtime.activation_nervous_system_v98 import (
            ContinuousDynamicsRuntime,
            ExpressionMotorV98,
            HostActivationBridgeV98,
            SharedActivationBus,
        )
        self.shared_activation_bus = SharedActivationBus()
        self.expression_motor_v98 = ExpressionMotorV98(
            shadow=True,
            logger=self.logger,
        )
        self.activation_signal_bridge_v98 = HostActivationBridgeV98(self)
        self.continuous_dynamics_runtime_v98 = ContinuousDynamicsRuntime(
            self.shared_activation_bus,
            motor=self.expression_motor_v98,
            source_provider=self.activation_signal_bridge_v98.collect,
            logger=self.logger,
        )

        # V99: activation V98 chỉ là "năng lượng". Tầng này bind năng lượng đó
        # vào object có provenance (concern/goal/user-model gap/environment). Nó
        # vẫn shadow: không tạo câu, không gọi LLM/TTS, không cấp quyền SPEAK.
        from runtime.motivational_focus_v99 import (
            HostFocusObjectBridgeV99,
            MotivationalFocusRuntimeV99,
        )
        self.focus_object_bridge_v99 = HostFocusObjectBridgeV99(self)
        self.motivational_focus_runtime_v99 = MotivationalFocusRuntimeV99(
            self.shared_activation_bus,
            source_provider=self.focus_object_bridge_v99.collect,
            logger=self.logger,
        )

        # V100: từ đây associative-material worker không còn quét context để tự
        # chọn "nghĩ về cái gì". Authority đọc BoundFocus V99 và chỉ projection
        # thành CausalMotive; không thread, không LLM, không public writer.
        from runtime.focus_material_authority_v100 import FocusMaterialAuthorityV100
        self.focus_material_authority_v100 = FocusMaterialAuthorityV100(
            self.motivational_focus_runtime_v99,
            logger=self.logger,
        )

        # V74 Life-Horizon: bốn object tách vai trò rõ ràng. Manager giữ H0-H4,
        # Arbiter chỉ điều chỉnh utility, Courtesy sở hữu quyền giữ lượt, còn
        # ReviewLock giữ semantic winner sau merge. Không object nào là public writer.
        self.life_horizon_manager = LifeHorizonManager(self, logger=self.logger)
        self.horizon_arbiter = HorizonArbiter(logger=self.logger)
        self.conversation_courtesy_contract = ConversationCourtesyContract(logger=self.logger)
        self.review_lock = ReviewLock(logger=self.logger)

        self.inner_diary_persister = InnerDiaryPersister(logger=self.logger)
        self.private_life_daemon = PrivateLifeDaemon.from_runtime(
            runtime_owner=self,
            logger=self.logger,
        )
        self.private_life_core = getattr(self.private_life_daemon, "core", None)
        # V45: associative seed đi vào một lifecycle material riêng trước khi có
        # quyền thành concern/urge. Đây là projection session-local, không phải DB.
        self.mental_material_runtime = MentalMaterialRuntime(self, logger=self.logger)
        # V101: production không đưa material expression-ready thẳng sang urge nữa.
        # PrivateIntentionField tích hợp cùng semantic object qua nhiều tick, giữ
        # forming/considering/ready/cooling riêng rồi mới projection sang V44 urge.
        # Không thread, không LLM/TTS và không có public SPEAK authority.
        from runtime.private_intention_field_v101 import PrivateIntentionFieldV101
        self.private_intention_runtime_v101 = PrivateIntentionFieldV101(
            self, logger=self.logger
        )
        # V102: semantic commitment không còn được ExpressionUrge tạo tức thời.
        # Runtime này tích hợp urge qua nhiều tick, giữ hysteresis và chỉ defer
        # projection khi có hard boundary thật. Không thread/LLM/TTS/public SPEAK.
        from runtime.expression_commitment_transition_v102 import ExpressionCommitmentTransitionV102
        self.expression_commitment_transition_v102 = ExpressionCommitmentTransitionV102(
            self, logger=self.logger
        )
        # V103: khi nhiều commitment tự thân đã cùng chín, public competition
        # không còn được phép flip winner chỉ vì một frame score hơn rất ít.
        # Workspace này chỉ giữ continuity của competition; nó không tạo motive,
        # không viết câu và không chạm structured continuation/task lanes.
        from runtime.expression_competition_workspace_v103 import ExpressionCompetitionWorkspaceV103
        self.expression_competition_workspace_v103 = ExpressionCompetitionWorkspaceV103(
            logger=self.logger
        )

        # V103 FINAL: authority đúng roadmap PDF. Các lớp V99-V103 migration phía
        # trên vẫn giữ state/compatibility, nhưng normal autonomous action family
        # giờ do continuous motor sở hữu. Subject/Neuro frame chỉ còn envelope
        # typed cho downstream V93-V97, không còn vote list speech candidate.
        from runtime.nervous_system_v103_final import NervousAutonomousAuthorityV103
        self.nervous_system_v103 = NervousAutonomousAuthorityV103(
            self.shared_activation_bus,
            host=self,
            logger=self.logger,
        )
        # Health monitor final đo chính tick V98 thay vì đo một loop khác. Gắn
        # observer sau construction để V98 không import ngược V103.
        self.continuous_dynamics_runtime_v98.health_observer = self.nervous_system_v103.health
        self.private_life_surface_queue: list[object] = getattr(self.private_life_daemon, "surface_queue", []) if self.private_life_daemon is not None else []
        self.private_life_digest: dict[str, object] = {}
        self.last_semantic_scene_note: SemanticSceneNote | None = None
        def _build_tutor_daemon():
            from runtime.tutor_wiring import build_tutor_daemon_from_runtime
            return build_tutor_daemon_from_runtime(self)
        # Tutor/research nền có owner riêng và mặc định không tham gia startup.
        # Không gọi constructor rồi mới "tắt"; như thế vẫn tốn thread/provider.
        self.tutor_daemon = self.subsystem_supervisor.start(
            "runtime.tutor",
            _build_tutor_daemon,
            failure_kind=FailureKind.PROVIDER_TRANSIENT,
            stop_methods=("stop", "shutdown", "close"),
        ) if background_tutor_enabled() else None
        # Semantic Tutor khác Curiosity Tutor: lane này dùng DeepSeek tutor key
        # để phân rã scene theo sự kiện và cấp một packet chung cho chat/chess/
        # autonomous. Không phát ngôn public, không chạy theo timer.
        def _build_semantic_tutor_runtime():
            from runtime.semantic_tutor_runtime import EventDrivenSemanticTutor
            return EventDrivenSemanticTutor(
                self,
                client=getattr(llm_service, "tutor_client", None),
                logger=self.logger,
            )
        self.semantic_tutor_runtime = self.subsystem_supervisor.start(
            "runtime.semantic_tutor",
            _build_semantic_tutor_runtime,
            failure_kind=FailureKind.PROVIDER_TRANSIENT,
            stop_methods=("stop", "shutdown", "close"),
            details={"mode": "event_driven_shared_context"},
        ) if background_tutor_enabled() else None
        if self.semantic_tutor_runtime is not None:
            self.logger.info(
                "[semantic-tutor] ready available=%s mode=event-driven",
                int(bool(getattr(self.semantic_tutor_runtime, "available", False))),
            )
        self._state_lock = threading.RLock()
        def _build_maintenance_service():
            from runtime.maintenance_service import MaintenanceService
            return MaintenanceService(self, logger=self.logger)
        self.maintenance_service = self.subsystem_supervisor.start(
            "runtime.maintenance",
            _build_maintenance_service,
            failure_kind=FailureKind.PROGRAMMER_ERROR,
            stop_methods=("shutdown", "close", "stop"),
        )
        # Memory GC daemon — dọn semantic + living-user mỗi 10 phút khi idle
        self.memory_gc_service = None
        try:
            from runtime.memory_gc_service import build_memory_gc_service
            self.memory_gc_service = build_memory_gc_service(
                memory_core=self.memory_core,
                llm_service=llm_service,
                idle_probe=getattr(self, "_background_memory_digest_idle_probe", None),
            )
            if self.memory_gc_service is not None:
                # Dọn memory định kỳ cũng kiểm tra có cần Gemini viết chân dung mới.
                self.memory_gc_service.portrait_presenter = getattr(self, "memory_portrait_presenter", None)
                self.logger.info("[memory_gc] service started (interval=%.0fs)", self.memory_gc_service.interval_seconds)
        except Exception as exc:
            self.logger.warning("[memory_gc] Không khởi tạo được memory GC service: %s", exc)

    def _bootstrap_session_and_perception(self) -> None:
        '''Các biến này lưu trạng thái hội thoại:
        có đang chat không,
        lịch sử chat,
        câu trả lời gần nhất,
        intent gần nhất,
        topic đang active,
        text cuối cùng đã nói,
        mood score,
        idle cycles,
        các guard chống lặp.
        '''
        self.chat_mode = False
        self._temporal_session_started_at = time.time()
        self._temporal_last_user_turn_at = 0.0
        self._temporal_last_assistant_turn_at = 0.0
        self._temporal_context_builder = None
        self._chat_last_activity_at = 0.0
        try:
            from runtime.interaction_clock import InteractionClock
            self.interaction_clock = InteractionClock()
        except Exception:
            self.interaction_clock = None
        self._chat_fresh_seconds = max(
            0.0, float(os.getenv("ERIS_CHAT_FRESH_SEC", "20") or 0.0)
        )
        self.chat_history = []
        self.last_chat_answer = ""
        self.repair_state_controller = RepairStateController()
        try:
            from actions.capability_runtime import refresh_runtime_capabilities
            refresh_runtime_capabilities(owner=self)
        except Exception as exc:
            safe_log(self.logger, "warning", "Không refresh được runtime capabilities: %s", exc)
        self.current_social_contract = None
        self.current_social_observation = None
        self.latest_social_observation = None
        self.pending_social_repair = False
        self.current_conversation_lane = None
        self.last_user_intent = ""
        self.active_conversational_topic = ""
        self.last_system_action = ""
        self.last_user_signature = ""
        self.repeated_user_count = 0
        self.running = True
        self.last_text = ""
        self.mood_score = 50 #mô phỏng trạng thái cảm xúc
        self.inferred_pos_x = 0.5 # vị trí avatar
        self.idle_cycles = 0
        self.last_autonomous_spoken_at = 0.0
        self.autonomous_turn_collision_until = 0.0
        self._autonomous_wakeup_event = threading.Event()
        self._autonomous_wakeup_reason = ""
        self.presence_maintenance_silent_cycles = 0
        self.last_presence_maintenance_at = 0.0
        self.last_ambient_body_only_at = 0.0
        '''
        khi nào ERIS được tự nói để duy trì hiện diện,
        im bao lâu thì re-engage,
        cooldown bao nhiêu giây,
        có dùng LLM cho micro presence không.
        '''
        self.reengagement_quiet_cycles = 0
        self.last_reengagement_at = 0.0
        self.autonomous_sleep_seconds = self._load_autonomous_sleep_seconds(
            "ERIS_AUTONOMOUS_SLEEP_SEC",
            # V62: cadence mặc định bớt ì. Đây chỉ là lúc *được xét lại*;
            # governor vẫn có toàn quyền WAIT/DROP nên không đồng nghĩa spam.
            default=(18, 28, 42, 60),
        )
        self.autonomous_suppressed_sleep_seconds = self._load_autonomous_sleep_seconds(
            "ERIS_AUTONOMOUS_SUPPRESSED_SLEEP_SEC",
            # No-speech không phải output đã lặp. Giữ vòng suppressed ngắn hơn
            # để silence_debt/vital pressure có cơ hội tích và surface.
            default=(8, 12, 18, 26),
        )
        self.force_speak_repeat_guard_count = 0
        self.force_speak_intent_guard_count = 0
        self.rapid_burst_window = self._load_rapid_burst_window()
        self._chat_processing = False
        self._chat_burst_queue: list[tuple[float, str]] = []
        self._chat_burst_lock = threading.RLock()
        self.recent_inner_thought_intents: tuple[str, ...] = ()
        self.recent_inner_thought_seeds: tuple[str, ...] = ()
        self.recent_meaning_families: tuple[str, ...] = ()
        # V12 compatibility only: không còn consumer production nào đọc field này.
        # Semantic authority hiện thuộc ConversationKernel -> NextConversationMove.
        self.last_semantic_frame: dict[str, object] = {}
        self.mental_process_interval_sec = self._load_mental_process_interval()
        self.mental_process_enabled = self._load_mental_process_enabled()
        self._mental_process_stop_event = threading.Event()
        self._mental_process_thread: threading.Thread | None = None
        self.long_term_memory = []
        self.memory_state = self._load_memory_state()
        self.long_term_memory = self._memory_recall_texts()
        self.active_memory_resonance = None
        self.last_grounding_context = None
        self.session_turn_ledger = self._ensure_session_turn_ledger()
        self.memory_commit_policy = self._ensure_memory_commit_policy()
        self.memory_recall_adapter = self._ensure_memory_recall_adapter()
        self.vision_facts_enabled, self.vision_interval_sec, self.vision_fact_max_items, self.vision_fact_max_len = (
            self._load_vision_fact_settings()
        )
        self.vision_facts: tuple[str, ...] = ()
        self.vision_stale_facts: tuple[str, ...] = ()
        self.vision_facts_are_stale = False
        self.vision_last_capture_at = 0.0
        self.vision_disabled_until = 0.0
        self.vision_failure_count = 0
        self.vision_last_trace = ""
        self.vision_facts_signature = ""
        self.vision_facts_novelty_hint = "none"
        self._vision_last_thumbnail: tuple[int, ...] | None = None
        self._vision_last_frame_digest = ""
        try:
            (
                self.screen_evidence_store,
                self.screen_text_worker,
                self.screen_relevance_router,
            ) = build_screen_text_runtime(logger=self.logger)
        except Exception as exc:
            self.screen_evidence_store = None
            self.screen_text_worker = None
            self.screen_relevance_router = None
            self.logger.warning("[screen text] không khởi tạo được index: %s", exc)

        try:
            from screen import build_vision_perception_runtime
            (
                self.vision_perception_service,
                self.vision_perception_worker,
            ) = build_vision_perception_runtime(logger=self.logger)
            self.vision_perception_worker.start()
        except Exception as exc:
            self.vision_perception_service = None
            self.vision_perception_worker = None
            self.logger.debug("[vision perception] worker bootstrap note: %s", exc)

        self._chat_screen_route_decision = None
        self._chat_screen_text_evidence = ()
        self._chat_screen_capture_present = False
        self._chat_screen_capture_interpreted = False

        # Unified perception coordinator: ChatCycle no longer owns a second copy
        # of OCR/router/pending-vision state.  Legacy helper methods delegate to
        # this object for compatibility.  VLM policy itself is unchanged.
        try:
            from runtime.chat_perception import ChatPerceptionCoordinator
            self.chat_perception_coordinator = ChatPerceptionCoordinator(
                screen_evidence_store=self.screen_evidence_store,
                screen_text_worker=self.screen_text_worker,
                screen_relevance_router=self.screen_relevance_router,
                logger=self.logger,
            )
            self._chat_pending_vision_escalation = self.chat_perception_coordinator.pending_vision
        except Exception as exc:
            self.chat_perception_coordinator = None
            try:
                from screen.evidence_models import PendingVisionEscalation
                self._chat_pending_vision_escalation = PendingVisionEscalation()
            except Exception:
                self._chat_pending_vision_escalation = None
            self.logger.warning("[chat perception] coordinator unavailable: %s", exc)

        # WorldState + proposal bus are process-local shared services.  They are
        # deliberately initialized before the AI lanes so chat/self-turn/seed
        # all receive the same world projection from their first turn.
        try:
            from runtime.behavior_proposals import ensure_proposal_bus
            from runtime.world_state import ensure_world_state_service
            ensure_proposal_bus(self)
            self.world_state_service = ensure_world_state_service(self)
        except Exception as exc:
            self.world_state_service = None
            self.logger.warning("[world state] bootstrap degraded: %s", exc)

        self.environment_capabilities = None
        try:
            from runtime.environment_capabilities import (
                ENVIRONMENT_CAPABILITY_VERSION, EnvironmentCapabilityRuntime,
            )
            self.environment_capabilities = EnvironmentCapabilityRuntime(self, logger=self.logger)
            binder = getattr(self.action_executor, "bind_environment_runtime", None)
            if callable(binder):
                binder(self.environment_capabilities)
            self.logger.info(
                "[environment-capability] loaded version=%s capabilities=%s status=read_only_verified",
                ENVIRONMENT_CAPABILITY_VERSION,
                len(self.environment_capabilities.contracts()),
            )
            try:
                from actions.capability_runtime import refresh_runtime_capabilities
                refresh_runtime_capabilities(owner=self)
            except Exception as exc:
                safe_log(self.logger, "warning", "Không refresh được environment capabilities: %s", exc)
        except Exception as exc:
            self.logger.warning("Không khởi tạo được environment capabilities: %s", exc)
        # Browser DOM Bridge — HTTP server daemon thread cho Brave/Chrome extension
        self.browser_bridge_server = None
        try:
            from runtime.browser_bridge_server import start_browser_bridge_server
            self.browser_bridge_server = self.subsystem_supervisor.start(
                "runtime.browser_bridge",
                lambda: start_browser_bridge_server(logger=self.logger),
                failure_kind=FailureKind.EXPECTED_DEGRADATION,
                stop_methods=("stop", "shutdown", "close"),
                details={"port": 7422, "purpose": "browser_dom_L4_bridge"},
            )
        except Exception as exc:
            self.logger.warning("[browser_bridge] Không khởi động được: %s", exc)

        # Chỉ start dynamics sau khi session clock/world state đã tồn tại. Thread
        # này local, deterministic, tick 50-100 ms và không gọi provider/LLM.
        activation_runtime = getattr(self, "continuous_dynamics_runtime_v98", None)
        if activation_runtime is not None:
            try:
                activation_runtime.start()
            except Exception as exc:
                self.logger.warning("[v98 activation] Không khởi động được shadow runtime: %s", exc)

        # Focus binding đọc snapshot activation nên start sau V98. Tick chậm hơn
        # dây thần kinh (mặc định 400 ms), hoàn toàn local và deterministic.
        focus_runtime = getattr(self, "motivational_focus_runtime_v99", None)
        if focus_runtime is not None:
            try:
                focus_runtime.start()
            except Exception as exc:
                self.logger.warning("[v99 focus] Không khởi động được shadow runtime: %s", exc)

    def _bootstrap_thought_and_ai(self, *, llm_service) -> None:
        self.thought_model_adapter = RuntimeThoughtModelAdapter.from_env(logger=self.logger)
        self.thought_stream = ThoughtStream(
            context_provider=self._build_thought_stream_context,
            logger=self.logger,
            model_reviewer=self.thought_model_adapter,
        )
        self.subconscious_loop = self.thought_stream
        self.cognitive_substrate = self.thought_stream
        self.subconscious_runtime_enabled = bool(get_config("subconscious_runtime.enabled", default=False))
        self.subconscious_runtime = self._build_subconscious_runtime() if self.subconscious_runtime_enabled else None
        self._pending_subconscious_report = None
        self._pending_subconscious_seed = None
        self._subconscious_lane_last_tick = {
            "lane2": 0.0,
            "lane3": 0.0,
            "lane5": 0.0,
            "reflection": 0.0,
        }
        self._subconscious_lane_next_due = {
            "lane2": time.time() + random.uniform(45.0, 120.0),
            "lane5": time.time() + random.uniform(600.0, 1800.0),
            "reflection": time.time() + random.uniform(900.0, 1800.0),
        }
        self._last_subconscious_runtime_tick_at = 0.0
        self._last_subconscious_runtime_reason = ""
        self._ensure_internal_state()
        try:
            from runtime.ai_orchestrated.bootstrap import install_ai_orchestrated_runtime
            install_ai_orchestrated_runtime(self, llm_service)
        except Exception as exc:
            self.ai_runtime_enabled = False
            self.ai_state_reducer = None
            self.ai_model_router = None
            self.ai_post_turn_observer = None
            self.ai_intention_planner = None
            self.ai_stage_planner = None
            self.ai_chat_orchestrator = None
            self.ai_autonomous_orchestrator = None
            self.logger.warning("Không khởi tạo được architecture C: %s", exc)
        if bool(getattr(self, "ai_runtime_enabled", False)):
            # Sau khi AI router + Subject Core đã có, cho Root đọc nguyên Self
            # ngay lúc startup; warm-up chạy daemon, không block avatar/UI.
            # Nếu provider chưa sẵn sàng, lần chat sau vẫn chạy one-shot.
            try:
                from runtime.conversation.bootstrap import ensure_conversation_kernel
                ensure_conversation_kernel(self)
            except Exception as exc:
                safe_log(self.logger, "warning", "[root-session] preload unavailable: %s", type(exc).__name__)
            # Architecture C sở hữu hot path. Thought/private-life vẫn là read-model
            # nền; không module nào được viết hoặc sửa câu public song song.
            self.mental_process_enabled = False
            self.subconscious_runtime_enabled = False
        self.subsystem_health_registry.record_success(
            "runtime.coordinator",
            details={
                "ai_runtime_enabled": bool(getattr(self, "ai_runtime_enabled", False)),
                "supervised_subsystems": self.subsystem_supervisor.snapshot(),
            },
        )

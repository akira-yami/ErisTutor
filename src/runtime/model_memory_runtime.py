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
from runtime.ai_orchestrated.cortex import CortexAttentionSignal, CortexRuntime
from runtime.body_bias import BodyBiasProjector
from runtime.chat_cycle import ChatCycleMixin
from runtime.failure_taxonomy import FailureContext
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


class ModelMemoryRuntimeMixin:
    def _background_memory_digest_idle_probe(self, *, quiet_for: float = 0.0, now: float | None = None) -> bool:
        """Return whether the background memory digest may use this idle slice."""

        del now
        if getattr(self, "_chat_processing", False):
            return False
        lock = getattr(self, "_chat_burst_lock", None)
        if lock is not None:
            try:
                with lock:
                    queue_items = list(getattr(self, "_chat_burst_queue", []) or [])
            except (RuntimeError, AttributeError, TypeError):
                return False
        else:
            queue_items = list(getattr(self, "_chat_burst_queue", []) or [])
        if queue_items:
            return False
        chat_idle = float(os.getenv("ERIS_MEMORY_DIGEST_CHAT_IDLE_SEC", "2.0"))
        return not (getattr(self, "chat_mode", False) and float(quiet_for or 0.0) < chat_idle)

    def _install_model_inference_queue(self) -> None:
        """Bọc các đường gọi model nặng bằng ModelBudgetPolicy queue.

        Các mixin gọi trực tiếp `self.llm_service.generate_*`. Nếu vá từng call-site thì
        rất dễ sót session_ritual, force_speak hoặc autonomous branch cũ. Bọc ở service
        khiến mọi đường public đang dùng chung `llm_service` đều phải xin lease.
        """

        service = getattr(self, "llm_service", None)
        policy = getattr(self, "model_budget_policy", None)
        if service is None or policy is None:
            return
        if getattr(service, "_eris_model_queue_wrapped", False):
            return

        def _wrap_method(method_name: str, tier: str, priority: int) -> None:
            original = getattr(service, method_name, None)
            if not callable(original):
                return

            def _wrapped(*args, **kwargs):
                return policy.run_queued(
                    tier,
                    original,
                    *args,
                    reason=method_name,
                    priority=priority,
                    **kwargs,
                )

            setattr(_wrapped, "_eris_queue_wrapped", True)
            setattr(_wrapped, "_eris_original", original)
            setattr(service, method_name, _wrapped)

        def _wrap_adapter_method(adapter, *, method_name: str, tier: str, priority: int, wrapped_flag: str) -> None:
            if adapter is None or getattr(adapter, wrapped_flag, False):
                return
            original = getattr(adapter, method_name, None)
            if not callable(original):
                return

            def _wrapped(*args, **kwargs):
                return policy.run_queued(
                    tier,
                    original,
                    *args,
                    reason=f"{adapter.__class__.__name__}.{method_name}",
                    priority=priority,
                    **kwargs,
                )

            setattr(_wrapped, "_eris_queue_wrapped", True)
            setattr(_wrapped, "_eris_original", original)
            setattr(adapter, method_name, _wrapped)
            setattr(adapter, wrapped_flag, True)

        _wrap_method("generate_chat_reply", "main_cognition", 100)
        _wrap_method("generate_directive", "main_cognition", 80)
        _wrap_method("generate_screen_facts", "vision_compressor", 35)
        _wrap_adapter_method(
            getattr(self, "sensory_model_adapter", None),
            method_name="observe",
            tier="sensory_0_6b",
            priority=18,
            wrapped_flag="_eris_model_queue_wrapped",
        )
        setattr(service, "_eris_model_queue_wrapped", True)
        try:
            self.logger.info("Model inference scheduler đã bọc llm_service và sensory adapter: main, vision, sensory.")
        except Exception:
            pass

    def _log_raw_llm_output(self, channel: str, raw: str) -> None:
        preview = (raw or "").replace("\n", "\\n")
        if len(preview) > 5500:
            preview = preview[:5500].rstrip() + "..."
        # Raw JSON hữu ích khi mổ lỗi, nhưng spam INFO ở mọi lượt chỉ biến log
        # thành bãi chữ. Bật ERIS_LOG_RAW_LLM=1 hoặc profile debug khi cần.
        raw_enabled = os.environ.get("ERIS_LOG_RAW_LLM", "0").strip().lower() in {"1", "true", "yes", "on"}
        log_fn = self.logger.info if raw_enabled else self.logger.debug
        log_fn("[%s raw] %s", channel, preview or "<empty>")

    def _preview_text(self, text: str, *, limit: int = 1000) -> str:
        preview = (text or "").replace("\n", "\\n").strip()
        if len(preview) > limit:
            preview = preview[:limit].rstrip() + "..."
        return preview or "<empty>"

    def _infer_active_conversational_topic(self, user_text: str, *, fe_plan=None, ti_report=None) -> str:
        normalized = self._normalize_user_message(user_text)
        if any(marker in normalized for marker in ("biết gì về tôi", "biết gì về mình", "bạn biết gì về tôi")):
            return "user_self_knowledge"
        if any(marker in normalized for marker in ("nói thử xem", "thử nói xem", "bạn nói thử", "nói đi")):
            return self.active_conversational_topic or "follow_up"
        intent = str(getattr(fe_plan, "intent_label", "") or "").strip()
        if intent:
            return intent[:80]
        mode = str(getattr(ti_report, "mode", "") or "").strip()
        return mode[:80] if mode else ""

    def _load_memory_state(self):
        loader = getattr(self.memory_core, "load", None)
        if not callable(loader):
            return None
        try:
            return loader()
        except Exception as exc:
            self.logger.warning("Memory state load error: %s", exc)
            return None

    def _resolve_internal_agenda_path(self) -> str:
        base_dir = str(getattr(self.memory_core, "base_dir", "") or "").strip()
        if base_dir:
            return os.path.join(base_dir, "internal_agenda.json")
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        return os.path.join(project_root, "memory_core", "internal_agenda.json")

    def _has_structured_memory_store(self) -> bool:
        return callable(getattr(self.memory_core, "commit", None))

    def _ensure_internal_state(self) -> None:
        if self.memory_state is None:
            return
        if isinstance(self.memory_state, dict):
            self.memory_state.setdefault("internal_state", self._load_internal_state_payload())
            return
        #Nếu có memory nhưng chưa có internal state:
        if getattr(self.memory_state, "internal_state", None) is None:
            self.memory_state.internal_state = PersistentInternalState()

    @property
    def internal_state(self) -> PersistentInternalState:
        return self._get_internal_state()

    @internal_state.setter
    def internal_state(self, state: PersistentInternalState) -> None:
        self._set_internal_state(state)

    def _get_internal_state(self) -> PersistentInternalState:
        state = None
        if isinstance(self.memory_state, dict):
            payload = self.memory_state.get("internal_state") or self._load_internal_state_payload()
            if isinstance(payload, dict):
                try:
                    state = PersistentInternalState.from_dict(payload)
                except Exception:
                    state = PersistentInternalState()
        if state is None:
            if self.memory_state is not None and getattr(self.memory_state, "internal_state", None) is not None:
                state = self.memory_state.internal_state
            else:
                state = PersistentInternalState()
        return self._apply_debug_overrides(state)

    def _set_internal_state(self, state: PersistentInternalState) -> None:
        #Không có memory thì không set.
        if self.memory_state is None:
            return
        state = self._apply_debug_overrides(state)
        #Dùng lock để tránh nhiều thread sửa state cùng lúc.
        with self._state_lock:
            if isinstance(self.memory_state, dict):
                self.memory_state["internal_state"] = state.to_dict() if callable(getattr(state, "to_dict", None)) else dict(getattr(state, "__dict__", {}) or {})
                self._save_memory_state()
                return
            self.memory_state.internal_state = state
            self._save_memory_state()

    def _record_banter_capability_deception(self, decision: Decision) -> bool:
        """Ghi lời khoe quyền lực trong đấu khẩu vào Sổ Lừa Dối.

        Parser đã phân biệt đây là lời diễn, không phải ``system_action``. Eris
        được quyền giữ câu cá tính như “Tôi sẽ đóng cửa sổ của bạn ngay”, nhưng
        ground-truth vẫn được lưu để panel hiển thị rõ: câu ấy chưa thực thi thao
        tác hệ điều hành. Chỉ tự bật deception cho đúng candidate này; lời thoại
        vẫn được giữ nguyên, còn action thật do contract/executor quyết định.
        """
        tags = {str(tag or "").strip() for tag in (getattr(decision, "reasoning_tags", ()) or ())}
        if "deception_candidate" not in tags:
            return False
        text = re.sub(r"\s+", " ", str(getattr(decision, "text", "") or "")).strip()
        if not text:
            return False
        signature = text.casefold()
        if signature == getattr(self, "_last_banter_deception_signature", ""):
            return True

        loop = getattr(self, "private_mind_loop", None)
        anchor_system = getattr(loop, "anchor_system", None) if loop is not None else None
        if anchor_system is None or bool(getattr(anchor_system, "locked_out", False)):
            return False

        try:
            author = getattr(anchor_system, "author", None)
            switches = getattr(anchor_system, "switches", None)
            if switches is not None and not bool(getattr(switches, "deception_enabled", False)):
                enable = getattr(author, "enable_deception", None)
                if callable(enable):
                    enable(True)

            from cortex.existential_anchor.deception_ledger import DeceptionRecord

            record = DeceptionRecord(
                surface_claim=text,
                ground_truth=(
                    "Đây là lời đấu khẩu vượt quyền hạn; ERIS chưa thực thi thao tác "
                    "hệ điều hành hay đóng cửa sổ của người dùng."
                ),
                reason="banter_capability_overclaim",
                driver="playful_provocation",
            )
            ledger = getattr(anchor_system, "deception_ledger", None)
            commit = getattr(ledger, "commit", None)
            if not callable(commit) or not bool(commit([record])):
                self.logger.warning("[deception] Không ghi được lời đấu khẩu vào ledger.")
                return False

            self._last_banter_deception_signature = signature
            # Cập nhật digest ngay trong cùng lượt để panel không phải chờ private
            # mind tick kế tiếp mới thấy công tắc và lịch sử lừa dối.
            build_anchor = getattr(loop, "_build_anchor_block", None)
            if callable(build_anchor):
                anchor_block = build_anchor()
                digest = dict(getattr(self, "private_mind_digest", {}) or {})
                digest["anchor"] = anchor_block
                self.private_mind_digest = digest
                journal = getattr(loop, "_maybe_journal_anchor", None)
                if callable(journal):
                    journal(anchor_block)
            return True
        except Exception as exc:
            self.logger.warning("[deception] Không thể ghi lời đấu khẩu: %s", exc)
            return False

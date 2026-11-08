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
from dataclasses import replace
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
from cortex.affective_dynamics import effective_affect_values
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
from runtime.live2d_affect_bridge import emit_runtime_affect
from runtime.interrupt_ledger import InterruptLedger
from runtime.loop_guard import RuntimeLoopGuard
from runtime.model_budget_policy import ModelBudgetPolicy
from runtime.outcome_observer import OutcomeObserver
from runtime.policy_settings import RuntimePolicySettings
from runtime.private_life_daemon import PrivateLifeDaemon
from runtime.private_mind_daemon import PrivateMindDaemon
from runtime.public_payload_policy import (
    build_speech_body_payload, speech_body_udp_bytes,
)
from runtime.public_turn_ledger import PublicTurnLedger
from runtime.public_turn_runtime import PublicTurnRuntime
from runtime.repair_state import RepairStateController
from runtime.signal_buffer import SignalBuffer, SignalBufferConfig
from runtime.source_grounding import SourceGroundingEngine
from runtime.stage_contracts import StageFrameBuilder
from runtime.stage_dispatcher import StageDispatcher
from runtime.stage_event_bus import StageEventBus
from runtime.stage_output import StageOutputPipeline
from runtime.stage_protocol import StageProtocolEvent
from runtime.stage_protocol_adapter import playback_event, show_plan_event, stage_segment_event, stage_state_event
from runtime.subconscious_loop import SubconsciousLoop as RuntimeSubconsciousLoop
from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry
from runtime.subsystem_supervisor import SubsystemSupervisor
from runtime.thought_model_adapter import RuntimeThoughtModelAdapter
from runtime.transport_boundary import RuntimeTransportBoundary
from runtime.tts_sync_policy import sync_text_with_tts_enabled
from runtime.vision_grounding import VisionGroundingPolicy, VisionGroundingReport
from runtime.voice_manager import VoiceManager
from runtime.vtuber_stage_version import VTUBER_STAGE_PHASES, VTUBER_STAGE_STATUS, VTUBER_STAGE_VERSION
from screen import build_screen_text_runtime


def _settle_pedagogical_delivery_event(
    host,
    *,
    event_type: str,
    turn_id: str,
    now: float | None = None,
    final_segment: bool = True,
) -> bool:
    terminal = str(event_type or "").strip().lower()
    success_events = {"audio_done", "no_tts"}
    failure_events = {"audio_failed", "audio_cancelled", "audio_localization_failed"}
    if terminal not in success_events | failure_events:
        return False
    if terminal in success_events and not final_segment:
        return False
    try:
        from runtime.pedagogical_runtime import ensure_pedagogical_runtime

        runtime = ensure_pedagogical_runtime(host)
        if runtime is None:
            return False
        return bool(
            runtime.settle_delivery(
                turn_id=str(turn_id or "")[:120],
                successful=terminal in success_events,
                now=float(now if now is not None else time.time()),
            )
        )
    except Exception:
        return False


def _settle_conversation_delivery_event(host, *, turn_id: str, successful: bool,
                                        cancelled: bool = False) -> bool:
    """Settle V12 delivery và fan-out receipt đúng capability của chính lượt đó."""

    did = str(turn_id or "")[:180]
    if not did:
        return False
    # TTS đã có kết quả dù Kernel run cũ có thể bị user preempt. Kết quả
    # phải cập nhật đúng delivery_id ở transcript, nhưng KHÔNG được hồi sinh
    # continuation hoặc làm thay đổi phase của generation mới.
    try:
        from runtime.conversation.speech_outcome import note_public_delivery_outcome
        note_public_delivery_outcome(
            host, delivery_id=did, status="delivered" if successful else ("cancelled" if cancelled else "failed"),
        )
    except Exception:
        pass
    try:
        from runtime.conversation.bootstrap import ensure_conversation_kernel
        from runtime.conversation.contracts import DeliveryReceipt

        kernel = ensure_conversation_kernel(host)
        settled = bool(kernel.settle_delivery_id(delivery_id=did, successful=bool(successful), cancelled=cancelled))
        if not settled:
            return False
        pending = getattr(host, "_v12_delivery_capabilities", None)
        meta = pending.pop(did, {}) if isinstance(pending, dict) else {}
        receipt = DeliveryReceipt(
            delivery_id=did,
            session_id=str(meta.get("session_id") or getattr(host, "conversation_id", "") or getattr(host, "thread_id", "") or "foreground:primary")[:180],
            generation=int(meta.get("generation", 0) or 0),
            move_id=str(meta.get("move_id") or "")[:180],
            successful=bool(successful),
        )
        for name in tuple(meta.get("capabilities", ()) or ()):
            capability = getattr(host, f"{str(name)}_capability", None)
            observe = getattr(capability, "observe_delivery_receipt", None)
            if callable(observe):
                try:
                    observe(receipt)
                except Exception as exc:
                    logger = getattr(host, "logger", None)
                    if logger is not None:
                        logger.debug("[conversation-v12] capability receipt degraded name=%s: %s", name, exc)
        return True
    except Exception as exc:
        logger = getattr(host, "logger", None)
        if logger is not None:
            try:
                logger.debug("[conversation-v12] delivery settle degraded: %s", exc)
            except Exception:
                pass
        return False


class PublicOutputRuntimeMixin:
    def _stage_channel_blocked(self, channel: str, *, requester_id: str = "") -> bool:
        try:
            from runtime.stage_ownership import stage_channel_blocked
            return stage_channel_blocked(self, channel, requester_id=requester_id)
        except Exception:
            return False

    def _supersede_tts_for_new_user_turn(self, reason: str) -> bool:
        micro_stop = getattr(self, "_current_micro_act_stop", None)
        if isinstance(micro_stop, threading.Event):
            micro_stop.set()
        tts = getattr(self, "tts_engine", None)
        supersede = getattr(tts, "supersede_all", None)
        if not callable(supersede):
            return False
        try:
            return bool(supersede(reason=reason))
        except Exception as exc:
            self.logger.debug("[TTS] supersede failed: %s", exc)
            return False

    def _resolve_micro_vocal_bytes(self, act: str, user_text: str = "") -> tuple[bytes | None, str]:
        """Lấy bytes file âm thanh ngắn và act thực tế phù hợp từ cache vocal."""
        try:
            from runtime.micro_reflex_system import get_micro_reflex_engine
            engine = get_micro_reflex_engine(logger=getattr(self, "logger", None))
            effective_act = act
            if user_text:
                effective_act = engine.evaluate_intake(user_text)
            packet = engine.compile_packet(effective_act, user_text=user_text)
            if packet and packet.audio_bytes:
                return packet.audio_bytes, packet.act_kind
            return None, str(getattr(effective_act, "value", effective_act))
        except Exception as exc:
            logger = getattr(self, "logger", None)
            if logger:
                logger.debug("[micro-act] resolve vocal error: %s", exc)
            return None, act

    def _emit_fast_micro_act(self, surface: str, *, act_kind: str, timestamp: float, user_text: str = "") -> bool:
        """Phát xạ tức thì (micro reflex < 30ms) hoàn toàn cục bộ 0 token.

        Phát một vài tiếng thở, tiếng 'ừm', 'ểh', 'huhu', 'hứ', 'haizz' từ cache audio ngắn kết hợp
        cử động nhỏ của Live2D (gật nhẹ, nghiêng đầu, chớp mắt), offset tham số mặt (ParamCheek,
        ParamEyeHighlight, ParamBreath), hoàn toàn không gọi LLM, không qua ElevenLabs synth
        và không hiện text bubble lên chat.
        """
        clock = getattr(self, "interaction_clock", None)
        if clock is not None and getattr(clock, "speech_floor_busy", False):
            return False

        try:
            from runtime.micro_reflex_system import get_micro_reflex_engine
            engine = get_micro_reflex_engine(logger=getattr(self, "logger", None))
            effective_kind = act_kind
            if user_text:
                intake_kind = engine.evaluate_intake(user_text)
                if getattr(intake_kind, "value", intake_kind) != "RECEIPT" or str(act_kind).upper() == "RECEIPT":
                    effective_kind = intake_kind

            packet = engine.compile_packet(effective_kind, user_text=user_text, timestamp=timestamp)
        except Exception as exc:
            logger = getattr(self, "logger", None)
            if logger:
                logger.debug("[micro-act] compile packet failed: %s", exc)
            packet = None

        if packet is None:
            return False

        motion = packet.motion_tag
        emotion = packet.emotion_preset
        cue = packet.cue_tags[0] if packet.cue_tags else "soft_breath"
        wav_bytes = packet.audio_bytes
        lip_envelope = list(packet.audio_envelope)
        frame_ms = packet.audio_frame_ms
        duration_ms = packet.audio_duration_ms
        param_offsets = dict(packet.live2d_param_offsets)

        turn_id = f"micro:{packet.act_kind.lower()}:{int(timestamp * 1000)}"
        bus = getattr(self, "stage_event_bus", None)
        mirror = getattr(bus, "emit_live2d", None) if bus is not None else None

        if callable(mirror):
            try:
                mirror(StageProtocolEvent(
                    type="audio_playback_started",
                    turn_id=turn_id,
                    seq=0,
                    event_id=f"micro_start:{turn_id}",
                    tts_text="",
                    display_text="",
                    emotion=emotion,
                    motion=motion,
                    interruptible=packet.interruptible,
                    source="fast_micro_act",
                    data={
                        "audio_envelope": lip_envelope,
                        "audio_frame_ms": frame_ms,
                        "audio_duration_ms": duration_ms,
                        "micro_act": packet.act_kind,
                        "category": packet.category,
                        "cue": cue,
                        "tags": [motion, *packet.cue_tags],
                        "param_offsets": param_offsets,
                    },
                ))
            except Exception as exc:
                logger = getattr(self, "logger", None)
                if logger:
                    logger.debug("[micro-act] stage event dispatch failed: %s", exc)

        transport = getattr(self, "transport", None)
        send_fn = getattr(transport, "send_json", None)
        if callable(send_fn):
            try:
                send_fn({
                    "type": "fast_micro_act",
                    "act_kind": packet.act_kind,
                    "category": packet.category,
                    "motion": motion,
                    "emotion": emotion,
                })
            except Exception:
                pass

        if wav_bytes:
            stop_event = threading.Event()
            self._current_micro_act_stop = stop_event

            def _play_worker() -> None:
                try:
                    from runtime.tts_engine import _play_wav_bytes
                    _play_wav_bytes(wav_bytes, stop_event, volume_applied=True)
                    if callable(mirror) and not stop_event.is_set():
                        mirror(StageProtocolEvent(
                            type="audio_playback_done",
                            turn_id=turn_id,
                            seq=0,
                            event_id=f"micro_done:{turn_id}",
                            source="fast_micro_act",
                        ))
                except Exception as play_exc:
                    logger = getattr(self, "logger", None)
                    if logger:
                        logger.debug("[micro-act] audio playback error: %s", play_exc)

            threading.Thread(target=_play_worker, daemon=True, name="ErisFastMicroAct").start()

        return True

    def _send_decision_to_body(self, decision: Decision, *, channel: str | None = None,
                               prepared=None, verification=None) -> bool:
        channel_name = str(channel or "").strip().lower()
        # V12: chuẩn bị và niêm phong transcript; semantic verifier đã bị gỡ.
        # Điều kiện cứng còn lại: đúng decision, generation, channel, stage bytes.
        if prepared is not None:
            from runtime.conversation.public_output_artifact import validate_prepared
            validate_prepared(prepared)
            if decision is not prepared.decision:
                raise RuntimeError('v03_decision_identity_changed')
            kernel = getattr(self,'conversation_kernel',None)
            if kernel is not None:
                state = kernel.store.load(prepared.session_id)
                if (state.generation != prepared.generation or
                        state.active_run_id != prepared.run_id or
                        state.active_delivery_id != prepared.delivery_id):
                    raise RuntimeError('v03_generation_preempted')
            if channel_name != prepared.channel:
                raise RuntimeError('v03_channel_mismatch')

        def v03_current() -> bool:
            if prepared is None:
                return True
            kernel = getattr(self,'conversation_kernel',None)
            if kernel is None:
                return True
            state = kernel.store.load(prepared.session_id)
            return (state.generation == prepared.generation and
                    state.active_run_id == prepared.run_id and
                    state.active_delivery_id == prepared.delivery_id)

        # Public audio là một floor có owner. Autonomous vẫn được cognition ở
        # background, nhưng tuyệt đối không được materialize một câu mới khi
        # chat/session speech đang synthesize hoặc phát. User barge-in đi đường
        # incoming/VoiceManager và vẫn có quyền supersede floor này.
        clock = getattr(self, "interaction_clock", None)
        if (
            channel_name == "autonomous"
            and clock is not None
            and bool(getattr(clock, "speech_floor_busy", False))
            and bool(getattr(decision, "should_speak", False))
        ):
            self.logger.info(
                "[stage ownership] deferred autonomous speech while audio floor busy owner=%s text=%r",
                str(getattr(clock, "audio_floor_turn_id", "") or "<pending>"),
                str(getattr(decision, "text", "") or "")[:120],
            )
            return False
        if channel_name and self._stage_channel_blocked(channel_name):
            self.logger.info(
                "[stage ownership] suppressed channel=%s text=%r",
                channel_name, str(getattr(decision, "text", "") or "")[:120],
            )
            return False
        payload = decision.to_payload()
        # event_trace_id là identity xuyên suốt public event, khác debug trace tự do.
        # Với chat, IntentPlanner là owner; autonomous đã đóng owner từ SocialInitiative.
        performance_context = getattr(decision, "performance_context", None)
        if isinstance(performance_context, Mapping):
            payload.setdefault("event_trace_id", str(performance_context.get("event_trace_id") or ""))
            payload.setdefault("generation_epoch", int(performance_context.get("generation_epoch", 0) or 0))
            payload.setdefault("public_intent_owner", str(performance_context.get("public_intent_owner") or ("intent_planner" if channel_name == "chat" else "")))
        if bool(getattr(self, "ai_runtime_enabled", False)) and getattr(self, "ai_state_reducer", None) is not None:
            from runtime.ai_orchestrated.panel_adapter import build_ai_panel_payload
            expression = {}
            behavior_meta = getattr(decision, "behavior_observation_meta", None)
            if isinstance(behavior_meta, Mapping):
                raw_expression = behavior_meta.get("identity_expression")
                if isinstance(raw_expression, Mapping):
                    expression = dict(raw_expression)
            # Identity panel V78 đọc projection đúng của V74/V77 thay vì tự
            # suy từ mood/discovery legacy. Nếu projection lỗi, panel vẫn chạy
            # với contract cũ vì hai block này chỉ là dữ liệu bổ sung.
            life_horizon_view = {}
            individual_view = {}
            try:
                horizon = getattr(self, "life_horizon_manager", None)
                snapshot_fn = getattr(horizon, "snapshot", None)
                if callable(snapshot_fn):
                    horizon_snapshot = snapshot_fn(now=time.time())
                    to_dict = getattr(horizon_snapshot, "to_dict", None)
                    if callable(to_dict):
                        life_horizon_view = dict(to_dict() or {})
            except Exception as exc:
                self.logger.debug("[identity-panel] life horizon projection failed: %s", exc)
            try:
                individual = getattr(self, "persistent_individual_runtime", None)
                projection_fn = getattr(individual, "projection_for_host", None)
                if callable(projection_fn):
                    individual_view = dict(projection_fn(self, now=time.time()) or {})
            except Exception as exc:
                self.logger.debug("[identity-panel] persistent individual projection failed: %s", exc)

            ai_panel = build_ai_panel_payload(
                self.ai_state_reducer.snapshot(),
                getattr(self, "ai_discovery_runtime", None),
                getattr(self, "ai_initiative_controller", None),
                getattr(self, "ai_identity_consolidator", None),
                expression,
                life_horizon=life_horizon_view,
                persistent_individual=individual_view,
            )
            payload.update(ai_panel)
            private_digest = dict(ai_panel.get("private_mind_digest", {}) or {})
            private_life_digest = dict(ai_panel.get("private_life_digest", {}) or {})
        else:
            payload.update(self._build_body_bias_payload())
            raw_private_digest = getattr(self, "private_mind_digest", {}) or {}
            full_private_digest = dict(raw_private_digest) if isinstance(raw_private_digest, dict) else {}
            private_digest = self._private_mind_digest_for_payload()
            if private_digest:
                self.private_mind_digest = private_digest
            private_life_digest = self._private_life_digest_for_payload()
            if private_life_digest:
                self.private_life_digest = private_life_digest
            payload["emotion_debug"] = self._build_emotion_debug_payload()
            payload["depth_v2"] = self._build_depth_v2_block(private_mind_digest=full_private_digest)
        if channel_name == "autonomous":
            plan = getattr(self, "last_ai_intention_plan", None)
            if plan is not None and callable(getattr(plan, "to_dict", None)):
                # Stage chỉ nhận IntentionPlan typed của Architecture C. Không còn
                # AutonomousPlan/permit/opportunity legacy chen giữa model và body.
                payload["autonomous_plan"] = plan.to_dict()
        if channel_name == "chat":
            # Chỉ dùng nội bộ stage: nhận ra user đang yêu cầu ERIS diễn một hành
            # vi nghe được như cười, thở dài, giả giận. Không gửi field này cho
            # model; nó đi sau Decision để tránh phá JSON contract.
            payload["user_text"] = str(getattr(self, "current_stage_user_text", "") or "")
        if isinstance(private_digest, dict) and private_digest:
            payload["private_mind_digest"] = private_digest
        if isinstance(private_life_digest, dict) and private_life_digest:
            payload["private_life_digest"] = private_life_digest
        # Stage transport consumes the Decision directly; no director/scorer pass.
        from runtime.runtime_settings_store import runtime_text
        # Bubble/public text và transcript phát âm là hai contract độc lập.
        # Eris viết tiếng Việt nhưng có thể đọc bằng giọng Nhật sau bước localize.
        stage_language = runtime_text("tts", "language", default="ja").strip().lower() or "ja"
        if stage_language in {"", "auto"}:
            stage_language = "ja"
        if prepared is not None:
            # Không gọi StageFrameBuilder lần hai (ngăn wording thay sau verify).
            stage_frame = prepared.stage_frame
        else:
            stage_builder = getattr(self, "stage_frame_builder", None)
            if stage_builder is None or str(getattr(stage_builder, "language_hint", "auto")) != stage_language:
                # StageFrame là lớp dịch giữa Decision và sân khấu.  Giữ lazy-init để
                # không làm phình __init__ của coordinator vốn đã rất đông dân.
                stage_builder = StageFrameBuilder(language_hint=stage_language)
                self.stage_frame_builder = stage_builder
            stage_frame = stage_builder.build(
                decision,
                payload=payload,
                channel=channel,
                frame=None,
                turn_id=None,
                segment_id=0,
            )

        # Performance đã được semantic authority chốt trong UtteranceSpec.
        # Stage chỉ compile preset thành metadata ElevenLabs/Live2D, hoàn toàn local,
        # không gọi một model thứ hai để diễn giải lại cùng một câu.
        if prepared is None:
            if str(stage_frame.tts_text or "").strip():
                try:
                    from runtime.voice_profile_compiler import compile_voice_plan_from_payload

                    voice_plan = compile_voice_plan_from_payload(
                        payload,
                        fallback_style=stage_frame.prosody_style,
                    )
                    stage_frame = replace(stage_frame, voice_plan=dict(voice_plan))
                except Exception as voice_compile_exc:
                    self.logger.debug("[voice-profile] deterministic compile skipped: %s", voice_compile_exc)

        # Từ đây trở đi payload public dùng bề mặt đã qua stage transformer:
        # UI thấy text sạch, TTS đọc text sạch, memory/chat history lưu memory_text.
        payload["text"] = stage_frame.display_text
        payload["memory_text"] = stage_frame.memory_text
        payload.update(stage_frame.to_payload_patch())
        # User có thể preempt ngay sau khi verifier PASS nhưng trước side effects.
        # Guard lần nữa tại điểm sát observation và public lifecycle.
        if not v03_current():
            return False
        # Niêm phong datagram thoại TRƯỚC public commit. Payload của panel
        # Identity / cognition đi một lane riêng và có thể phình >63KB mà
        # không ảnh hưởng lời nói. Dùng lại đúng object này lúc UDP dispatch;
        # không build lại lần hai sau các tác dụng phụ của observation.
        preflight_body_payload = None
        if prepared is not None:
            from runtime.conversation.public_output_artifact import validate_prepared
            validate_prepared(prepared)
            if payload.get('text') != prepared.display_text or payload.get('memory_text') != prepared.memory_text:
                raise RuntimeError('v03_late_stage_surface_change')
            preflight_body_payload = build_speech_body_payload(payload)
            if preflight_body_payload.get('text') != prepared.display_text:
                raise RuntimeError('v03_pre_dispatch_text_modified')
            # Đo bytes trên packet sau khi tách panel (không phải payload gộp).
            self.logger.debug(
                '[public-udp] speech preflight bytes=%d panel separated',
                speech_body_udp_bytes(preflight_body_payload),
            )
        # Lifecycle chỉ bắt đầu SAU guard; lượt đã bị preempt không ghi
        # observation/public state hoặc tạo receipt phát ngôn.
        from runtime.public_lifecycle import lifecycle_for
        public_lifecycle = lifecycle_for(self)
        lifecycle_snapshot = public_lifecycle.snapshot()
        if lifecycle_snapshot.turn_id != stage_frame.turn_id:
            public_lifecycle.begin(
                turn_id=stage_frame.turn_id,
                channel=channel_name or str(channel or "unknown"),
                reason="public_surface_composing",
                received=False,
            )
        public_lifecycle.transition(
            "CONTENT_COMMITTED",
            turn_id=stage_frame.turn_id,
            reason="utterance_committed",
        )

        # Observation xảy ra sau khi public surface đã xác định, nhưng trước khi
        # dispatch. Nó chỉ ghi hậu quả cho lượt sau; không được mutate Decision.
        try:
            from runtime.behavior_observation import (
                behavior_observation_summary,
                record_public_behavior,
            )

            record_public_behavior(
                self,
                decision,
                payload=payload,
                channel=channel_name or str(channel or ""),
                turn_id=str(getattr(decision, "director_trace_id", "") or ""),
            )
            # Decision.to_payload() chạy trước observation. Cập nhật summary tại
            # đây để panel của chính lượt hiện tại cũng nhìn thấy tín hiệu mới,
            # thay vì phải đợi lượt sau như một giao diện sống chậm hơn não.
            observation_summary = behavior_observation_summary(decision)
            if observation_summary:
                payload["behavior_observation"] = observation_summary
        except Exception as exc:
            self.logger.debug("[behavior-observation] public record skipped: %s", exc)
        # V98 negative feedback: public commit tiêu expressive pressure và tạo
        # refractory tạm thời. Chỉ bơm ModulationEvent, không sửa activation trực tiếp.
        if bool(payload.get("should_speak")) and str(payload.get("memory_text", payload.get("text", "")) or "").strip():
            try:
                _v98_bridge = getattr(self, "activation_signal_bridge_v98", None)
                _v98_bus = getattr(self, "shared_activation_bus", None)
                if _v98_bridge is not None and _v98_bus is not None:
                    _v98_bus.submit(_v98_bridge.public_expression_event(now=time.time()))
            except Exception as exc:
                self.logger.debug("[v98 activation] refractory modulation skipped: %s", exc)
            # V103 FINAL: cùng public receipt nuôi speech homeostasis + STP.
            # Chỉ truyền semantic source ID typed; tuyệt đối không feed public
            # wording ngược vào cognition.
            try:
                _nervous = getattr(self, "nervous_system_v103", None)
                _note_public = getattr(_nervous, "note_public_commit", None)
                if callable(_note_public):
                    _args = dict(getattr(decision, "arguments", {}) or {})
                    _commitment = _args.get("expression_commitment")
                    _commitment = dict(_commitment) if isinstance(_commitment, dict) else {}
                    _source_id = str(
                        _commitment.get("source_id", "")
                        or _args.get("expression_urge_id", "")
                        or ""
                    )
                    _note_public(_source_id, now=time.time())
            except Exception as exc:
                self.logger.debug("[nervous-v103] public receipt degraded: %s", exc)
        stage_output = getattr(self, "stage_output_pipeline", None)
        if stage_output is None:
            stage_output = StageOutputPipeline()
            self.stage_output_pipeline = stage_output
        stage_frames = prepared.segments if prepared is not None else stage_output.frames_for_tts(stage_frame)
        if isinstance(payload.get("stage"), dict):
            payload["stage"]["segment_count"] = len(stage_frames)
            payload["stage"]["protocol"] = "eris.stage"
            payload["stage"]["protocol_version"] = 1
        # Mặc định bubble chỉ xuất hiện khi audio thật sự bắt đầu phát. Trước đây
        # default=0 làm text hiện ngay, còn Gemini TTS bò tới sau vài giây như một
        # bản lồng tiếng bị lệch timeline. Có thể opt-out bằng ERIS_TTS_SYNC_TEXT=0.
        sync_text = sync_text_with_tts_enabled()
        text_payload_sent = False
        text_payload_cancelled = False
        text_payload_lock = threading.Lock()
        stage_payload_sent = False
        panel_payload_sent = False

        def send_live2d_audio_edge(
            edge_type: str,
            event_stage_frame,
            *,
            event=None,
        ) -> None:
            """Đưa lifecycle audio thẳng tới Live2D, không phụ thuộc Godot/UI protocol."""
            if not v03_current():
                return

            bus = getattr(self, "stage_event_bus", None)
            mirror = getattr(bus, "emit_live2d", None) if bus is not None else None
            if not callable(mirror):
                return
            frame = event_stage_frame or stage_frame
            turn_id = str(getattr(event, "turn_id", "") or getattr(frame, "turn_id", "") or "")
            try:
                seq = int(getattr(event, "segment_id", getattr(frame, "segment_id", 0)) or 0)
            except (TypeError, ValueError):
                seq = 0
            stable_turn = turn_id or "anonymous"
            event_id = f"audio:{stable_turn}:{seq}:{edge_type}"
            data: dict[str, object] = {
                "prosody_style": str(getattr(frame, "prosody_style", "") or ""),
                "speech_act": str(getattr(frame, "speech_act", "") or ""),
            }
            if event is not None:
                envelope = tuple(getattr(event, "audio_envelope", ()) or ())
                if envelope:
                    data["audio_envelope"] = list(envelope)
                shape = getattr(event, "audio_mouth_shape", ()) or ()
                if shape:
                    data["audio_mouth_shape"] = list(shape)
                frame_ms = int(getattr(event, "audio_frame_ms", 0) or 0)
                duration_ms = int(getattr(event, "audio_duration_ms", 0) or 0)
                if frame_ms > 0:
                    data["audio_frame_ms"] = frame_ms
                if duration_ms > 0:
                    data["audio_duration_ms"] = duration_ms
            try:
                mirror(StageProtocolEvent(
                    type=str(edge_type or ""),
                    turn_id=turn_id,
                    seq=seq,
                    event_id=event_id,
                    tts_text=str(getattr(event, "text", "") or getattr(frame, "tts_text", "") or ""),
                    display_text=str(getattr(frame, "display_text", "") or ""),
                    emotion=str(getattr(event, "emotion", "") or getattr(frame, "emotion", "") or ""),
                    motion=str(getattr(event, "motion", "") or getattr(frame, "motion", "") or ""),
                    interruptible=bool(getattr(frame, "interruptible", True)),
                    source=str(getattr(frame, "source", "") or ""),
                    data={key: value for key, value in data.items() if value not in ("", [], (), {}, None)},
                ))
            except Exception as exc:
                self.logger.debug("[live2d] direct audio edge failed: %s", exc)

        def send_stage_protocol_segments():
            nonlocal stage_payload_sent
            if not v03_current():
                return
            if stage_payload_sent:
                return
            bus = getattr(self, "stage_event_bus", None)
            if bus is None:
                return
            stage_payload_sent = True
            # Cập nhật khí sắc nền trước emotion của câu nói. Event chỉ chứa vector
            # số, không mang nội dung private mind. Khi câu kết thúc, biểu cảm tạm
            # thời sẽ tan dần về state này thay vì reset cứng thành mặt mannequin.
            emit_runtime_affect(
                self,
                source=f"public_turn:{channel_name or str(channel or 'chat')}",
                confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
            )
            try:
                if isinstance(getattr(stage_frame, "show_plan", None), dict) and stage_frame.show_plan:
                    bus.emit(show_plan_event(stage_frame))
            except Exception as exc:
                self.logger.debug("[stage-protocol] show_plan emit failed: %s", exc)
            for frame in stage_frames or (stage_frame,):
                try:
                    bus.emit(stage_segment_event(frame, segment_count=len(stage_frames) or 1))
                except Exception as exc:
                    self.logger.debug("[stage-protocol] stage_segment emit failed: %s", exc)

        def send_text_payload(release_reason: str = "immediate"):
            nonlocal text_payload_sent
            if not v03_current():
                return
            with text_payload_lock:
                if text_payload_sent or text_payload_cancelled:
                    return
                text_payload_sent = True
            if release_reason == "audio_start":
                public_lifecycle.transition(
                    "SPEAKING",
                    turn_id=stage_frame.turn_id,
                    reason="audio_playback_started",
                )
            elif release_reason == "no_tts":
                public_lifecycle.transition(
                    "DEGRADED",
                    turn_id=stage_frame.turn_id,
                    reason=release_reason,
                )
            send_stage_protocol_segments()
            try:
                # Godot chỉ cần text/action/stage. Parser trace là metadata nội
                # bộ; gửi nó sang body khiến console in ba dòng AI Debug cho
                # mỗi câu, trong khi chẳng giúp nhân vật cử động tốt hơn.
                # Với prepared transcript dùng NGUYÊN BẢN datagram đã
                # preflight. Không vô ý nạp identity_runtime hoặc cognitive_views
                # trở lại gói chat sau public commit/observation.
                body_payload = (preflight_body_payload if prepared is not None
                                else build_speech_body_payload(payload))
                if prepared is not None:
                    if not v03_current():
                        return
                    if str(body_payload.get('text', '')) != prepared.display_text:
                        raise RuntimeError('v03_late_public_text_change')
                self.transport.send_json(body_payload)
                # P2: publish only after the actual public-text transport send.
                # The Root has chosen these blocks/tools; no independent presenter
                # can infer or override the user's conversational turn.
                if prepared is not None and v03_current() and (
                    prepared.presentation_blocks or prepared.tool_artifact_bundles
                ):
                    try:
                        from runtime.workspace.presentation_p2 import publish_committed_presentation
                        publish_committed_presentation(
                            turn_id=str(stage_frame.turn_id or ""),
                            delivery_id=prepared.delivery_id,
                            blocks=prepared.presentation_blocks,
                            tool_bundles=prepared.tool_artifact_bundles,
                        )
                    except (OSError, ValueError, TypeError) as _p2_err:
                        self.logger.warning("[presentation-p2] sidecar degraded: %s", type(_p2_err).__name__)
                # V6: Bubble được gửi trước; nhánh trình bày viết và sửa panel
                # bất đồng bộ trong lúc TTS vẫn đang chạy. Không chạy lại Root.
                try:
                    from runtime.workspace.live_document import dispatch_live_document
                    _doc_session = str(getattr(prepared, "session_id", "") or getattr(stage_frame, "turn_id", "") or "surface")
                    _doc_generation = int(getattr(prepared, "generation", 0) or 0)
                    _doc_run = str(getattr(prepared, "run_id", "") or "autonomous")
                    _doc_delivery = str(getattr(prepared, "delivery_id", "") or getattr(stage_frame, "turn_id", "") or "public")
                    # Tài liệu Root đã quyết được giữ riêng; không lấy lại Bubble
                    # đã lọc code làm nguồn khi document_source tồn tại.
                    _bubble_text = str(body_payload.get("text", "") or "")
                    _document_text = str(getattr(prepared, "document_source", "") or "") if prepared is not None else ""
                    dispatch_live_document(
                        text=_document_text or _bubble_text,
                        bubble_text=_bubble_text,
                        session_id=_doc_session, generation=_doc_generation,
                        run_id=_doc_run, delivery_id=_doc_delivery,
                        turn_id=str(stage_frame.turn_id or ""),
                        transport=self.transport,
                        router=getattr(self, "ai_model_router", None),
                        is_current=v03_current, logger=self.logger,
                    )
                except Exception as _workspace_err:
                    self.logger.debug("[workspace-v6] bỏ qua cập nhật panel: %s", _workspace_err)
                self.logger.info(
                    "[public-text] sent turn=%s reason=%s chars=%d",
                    stage_frame.turn_id, release_reason, len(str(body_payload.get("text", ""))),
                )
                # Public ledger chỉ nhìn thấy câu khi bubble thật sự được gửi.
                # History của delivery adapter vẫn giữ receipt pending riêng.
                self._record_public_turn(decision, payload=payload, channel=channel)
            except Exception as e:
                self.logger.warning("Failed to send deferred chat text payload: %s", e)

        def discard_deferred_text():
            nonlocal text_payload_cancelled
            with text_payload_lock:
                text_payload_cancelled = True

        def send_panel_payload():
            nonlocal panel_payload_sent
            if not v03_current():
                return
            if panel_payload_sent:
                return
            panel_payload_sent = True
            panel_sender = getattr(self.transport, "send_panel_state", None)
            if not callable(panel_sender):
                return
            try:
                # Hardening status đi cùng lane panel, không làm phình gói chat.
                panel_payload = dict(payload)
                release_raw = dict(getattr(self, "hardening_release_status", {}) or {})
                failed_release_checks = [
                    str(item.get("name", "unknown"))
                    for item in release_raw.get("findings", ())
                    if isinstance(item, dict) and str(item.get("status", "")) == "fail"
                ][:6]
                phase7_raw = (
                    self.phase7_validation.status()
                    if getattr(self, "phase7_validation", None) is not None
                    else {"status": "unavailable"}
                )
                stage_bus = getattr(self, "stage_event_bus", None)
                if stage_bus is not None and callable(getattr(stage_bus, "session_snapshot", None)):
                    panel_payload["stage_session"] = stage_bus.session_snapshot()
                panel_payload["hardening_status"] = {
                    "release": {
                        "ready": bool(release_raw.get("ready", False)),
                        "status": str(release_raw.get("status", "unknown") or "unknown"),
                        "release_id": str(release_raw.get("release_id", "") or ""),
                        "failed_checks": failed_release_checks,
                    },
                    "phase7": {
                        "status": str(phase7_raw.get("status", "unknown") or "unknown"),
                        "validation_id": str(phase7_raw.get("validation_id", "") or ""),
                        "public_turns": int(phase7_raw.get("public_turns", 0) or 0),
                        "target_turns": int(phase7_raw.get("target_turns", 0) or 0),
                        "report_path": str(phase7_raw.get("report_path", "") or ""),
                    },
                }
                packet_id = panel_sender(panel_payload)
                if packet_id and self.observability is not None:
                    self.observability.increment(
                        "panel_state_sent_total",
                        tags={"channel": str(channel or "unknown")},
                        fields={"packet_id": packet_id},
                    )
            except Exception as panel_exc:
                self.logger.warning("Failed to send panel state payload: %s", panel_exc)

        def handle_tts_event(event, event_stage_frame):
            event_type = str(getattr(event, "type", "") or "").strip().lower()
            if not v03_current():
                # Trường hợp P0: user chen lời làm generation cũ stale trước
                # khi callback TTS quay về. Không phát event sân khấu cũ đè
                # lên turn mới, nhưng phải lưu trạng thái audio của câu cũ.
                if event_type == "audio_start":
                    self.logger.warning(
                        "[public-text] stale audio_start rejected turn=%s; voice/text ownership diverged",
                        str(getattr(event, "turn_id", "") or ""),
                    )
                if event_type in {"audio_done", "audio_failed", "audio_cancelled"}:
                    try:
                        from runtime.conversation.speech_outcome import note_public_delivery_outcome
                        old_id = str(payload.get("event_trace_id") or getattr(event, "turn_id", "") or "")
                        status = {"audio_done": "delivered", "audio_failed": "failed", "audio_cancelled": "cancelled"}[event_type]
                        # audio_done của một segment chưa chắc là hết cả câu.
                        segment = int(getattr(event, "segment_id", 0) or 0)
                        total = len(tuple(getattr(event, "turn_segments", ()) or ())) or len(stage_frames) or 1
                        if event_type != "audio_done" or segment >= total - 1:
                            note_public_delivery_outcome(self, delivery_id=old_id, status=status)
                    except Exception as exc:
                        self.logger.debug("[conversation-v12] stale audio receipt degraded: %s", exc)
                return
            if sync_text and event_type in {"audio_failed", "audio_localization_failed", "audio_cancelled"}:
                discard_deferred_text()
            event_turn_id = str(
                getattr(event, "turn_id", "")
                or getattr(event_stage_frame, "turn_id", "")
                or stage_frame.turn_id
                or ""
            )
            clock = getattr(self, "interaction_clock", None)
            final_segment = True
            if clock is not None:
                try:
                    try:
                        event_segment_id = int(
                            getattr(event, "segment_id", getattr(event_stage_frame, "segment_id", 0)) or 0
                        )
                    except (TypeError, ValueError):
                        event_segment_id = 0
                    known_segment_ids = []
                    for item in tuple(getattr(event, "turn_segments", ()) or ()):
                        if isinstance(item, dict):
                            try:
                                known_segment_ids.append(int(item.get("segment_id", 0) or 0))
                            except (TypeError, ValueError):
                                pass
                    if not known_segment_ids:
                        for item in stage_frames:
                            try:
                                known_segment_ids.append(int(getattr(item, "segment_id", 0) or 0))
                            except (TypeError, ValueError):
                                pass
                    final_segment = not known_segment_ids or event_segment_id >= max(known_segment_ids)

                    if event_type in {"audio_prepare", "audio_queued", "audio_buffer_ready"}:
                        clock.mark_audio_pending(turn_id=event_turn_id)
                    elif event_type == "audio_start":
                        clock.mark_audio_started(turn_id=event_turn_id)
                    elif event_type == "audio_done":
                        if final_segment:
                            clock.mark_audio_finished(turn_id=event_turn_id)
                        else:
                            # Giữ floor xuyên qua gap giữa các segment cùng turn.
                            clock.mark_audio_pending(turn_id=event_turn_id)
                    elif event_type == "audio_failed":
                        if final_segment:
                            clock.mark_audio_finished(turn_id=event_turn_id)
                        else:
                            clock.mark_audio_pending(turn_id=event_turn_id)
                    elif event_type == "audio_cancelled":
                        clock.mark_audio_finished(turn_id=event_turn_id)
                except Exception as clock_exc:
                    self.logger.debug("[interaction-clock] audio lifecycle update failed: %s", clock_exc)
            live2d_edge = {
                "audio_start": "audio_playback_started",
                "audio_done": "audio_playback_done",
                "audio_cancelled": "interrupted",
                "audio_failed": "degraded",
                "audio_localization_failed": "degraded",
            }.get(event_type)
            if live2d_edge:
                send_live2d_audio_edge(live2d_edge, event_stage_frame, event=event)
            if event_type in {"audio_prepare", "audio_queued", "audio_buffer_ready"}:
                public_lifecycle.transition(
                    "AUDIO_BUFFERING",
                    turn_id=stage_frame.turn_id,
                    reason=event_type,
                )
            elif event_type == "audio_start":
                if sync_text:
                    send_text_payload("audio_start")
                else:
                    public_lifecycle.transition(
                        "SPEAKING",
                        turn_id=stage_frame.turn_id,
                        reason=event_type,
                    )
            elif event_type == "audio_done":
                public_lifecycle.transition(
                    "DONE",
                    turn_id=stage_frame.turn_id,
                    reason=event_type,
                )
            elif event_type == "audio_cancelled":
                public_lifecycle.transition(
                    "INTERRUPTED",
                    turn_id=stage_frame.turn_id,
                    reason=event_type,
                )
            elif event_type in {"audio_failed", "audio_localization_failed"}:
                public_lifecycle.transition(
                    "DEGRADED",
                    turn_id=stage_frame.turn_id,
                    reason=event_type,
                )
            self._handle_tts_playback_event(
                event, stage_frame=event_stage_frame,
                trace_id=str(payload.get("event_trace_id") or getattr(event_stage_frame, "turn_id", "") or ""),
            )
            _settle_pedagogical_delivery_event(
                self,
                event_type=event_type,
                turn_id=event_turn_id,
                now=float(getattr(event, "timestamp", time.time()) or time.time()),
                final_segment=final_segment,
            )

        # TTS: đọc to câu vừa nói với emotion context (valence/arousal/stance).
        speech_provider = self._get_runtime_provider("speech")
        _tts = getattr(speech_provider, "engine", None) if speech_provider is not None else None
        has_scheduled_tts = False
        # Panel không phụ thuộc TTS. Khi sync_text đang chờ remote audio, dữ liệu
        # cảm xúc/nhật ký vẫn phải hiện ngay chứ không đứng nhìn spinner như một
        # nghi thức tôn giáo của UI bất đồng bộ.
        send_panel_payload()
        # Nội dung stage/bubble cùng chờ playback; panel và trạng thái buffering
        # vẫn cập nhật ngay trong lúc synth, không tự nhả chữ sau một timeout.
        if not sync_text:
            # Chế độ legacy: text hiện ngay, audio tới sau.
            send_text_payload("immediate")
        if (
            _tts is not None
            and getattr(_tts, "_ok", False)
            and bool(payload.get("should_speak"))
            and stage_frames
        ):
            try:
                _ed = payload.get("emotion_debug") or {}
                _mood = _ed.get("summary") if isinstance(_ed, dict) else {}
                _valence = float((_mood or {}).get("valence", 0.0)) if isinstance(_mood, dict) else 0.0
                _arousal = float((_mood or {}).get("arousal", 0.3)) if isinstance(_mood, dict) else 0.3
                _stance = str((_mood or {}).get("stance", "") or "") if isinstance(_mood, dict) else ""
                dispatcher = getattr(self, "stage_dispatcher", None)
                if dispatcher is None:
                    dispatcher = StageDispatcher()
                    self.stage_dispatcher = dispatcher
                public_lifecycle.transition(
                    "AUDIO_BUFFERING",
                    turn_id=stage_frame.turn_id,
                    reason="tts_dispatch",
                )
                clock = getattr(self, "interaction_clock", None)
                if clock is not None:
                    try:
                        clock.mark_audio_pending(turn_id=str(stage_frame.turn_id or ""))
                    except Exception as clock_exc:
                        self.logger.debug("[interaction-clock] audio pending mark failed: %s", clock_exc)
                try:
                    from runtime.ai_orchestrated.autonomous_trace import append_public_turn_trace
                    append_public_turn_trace("tts_dispatch", {
                        "trace_id": str(payload.get("event_trace_id") or stage_frame.turn_id),
                        "turn_id": str(stage_frame.turn_id),
                        "stage": "tts",
                        "owner": "tts",
                        "generation_epoch": int(payload.get("generation_epoch", 0) or 0),
                        "tts_event": "dispatch",
                    }, logger=getattr(self, "logger", None))
                except Exception:
                    pass
                # Callback on_first_start legacy còn được gọi cả khi synth lỗi.
                # Chỉ event audio_start (sau PCM submission) mới commit text và
                # phát edge Live2D có envelope trong handle_tts_event ở trên.
                has_scheduled_tts = bool(dispatcher.dispatch_tts(
                    stage_frames,
                    tts_engine=_tts,
                    valence=_valence,
                    arousal=_arousal,
                    stance=_stance,
                    on_event=handle_tts_event,
                ))
            except Exception as _tts_exc:
                self.logger.warning("[TTS] speak error turn_id=%s: %s", stage_frame.turn_id, _tts_exc)
                has_scheduled_tts = False

        if not has_scheduled_tts:
            _tts_ok = _tts is not None and getattr(_tts, "_ok", False)
            _should_speak = bool(payload.get("should_speak"))
            _has_frames = bool(stage_frames)
            self.logger.warning(
                "[TTS] no TTS scheduled turn_id=%s: tts_ok=%s should_speak=%s has_frames=%s",
                stage_frame.turn_id, _tts_ok, _should_speak, _has_frames,
            )
            clock = getattr(self, "interaction_clock", None)
            if clock is not None:
                try:
                    clock.mark_audio_finished(turn_id=str(stage_frame.turn_id or ""))
                except Exception:
                    pass
            audio_required = sync_text and _should_speak and _tts_ok
            if audio_required:
                discard_deferred_text()
                public_lifecycle.transition(
                    "DEGRADED", turn_id=stage_frame.turn_id, reason="no_tts",
                )
            else:
                send_text_payload("no_tts" if sync_text else "immediate")
            _settle_pedagogical_delivery_event(
                self,
                event_type="audio_failed" if audio_required else "no_tts",
                turn_id=str(stage_frame.turn_id or ""),
                now=time.time(),
            )
            try:
                _settle_conversation_delivery_event(
                    self, turn_id=str(stage_frame.turn_id or ""), successful=not audio_required
                )
            except Exception as exc:
                self.logger.debug("[conversation-v12] no-tts delivery settle degraded: %s", exc)
        # Mọi lời ERIS thốt ra (autonomous, force_speak, session_ritual...) đều
        # ghi vào bộ đệm hội thoại hợp nhất để lượt chat/autonomous sau nối mạch.
        # Chat public được Architecture C ghi ở đúng commit point; không append trùng.

        if bool(payload.get("should_speak")) and str(payload.get("memory_text", payload.get("text", "")) or "").strip():
            # Ghi mốc assistant sau khi output public đã được dựng và gửi. Đây là
            # mốc cognition, không phải audio_done; đủ để hiểu nhịp hội thoại mà
            # không phải chờ TTS remote hoàn tất mới biết mình vừa nói.
            self._temporal_last_assistant_turn_at = time.time()
            if channel_name == "autonomous":
                # Content hook vẫn commit sau output public thật. Performer legacy
                # đã bị xóa; nhịp sân khấu lấy trực tiếp từ IntentionPlan typed.
                performance_meta = payload.get("performance_context") if isinstance(payload.get("performance_context"), dict) else {}
                hook_meta = performance_meta.get("content_hook") if isinstance(performance_meta.get("content_hook"), dict) else {}
                hook_id = str(performance_meta.get("content_hook_id") or hook_meta.get("id") or "")
                performance_builder = getattr(self, "performance_context_builder", None)
                content_engine = getattr(performance_builder, "content_engine", None) if performance_builder is not None else None
                if content_engine is not None and callable(getattr(content_engine, "commit_hook_use", None)) and hook_id:
                    try:
                        content_engine.commit_hook_use(hook_id)
                    except Exception as exc:
                        self.logger.debug("[content-engine] autonomous hook commit skipped: %s", exc)
        if bool(payload.get("should_speak")) and str(payload.get("memory_text", payload.get("text", "")) or "").strip() and self.subconscious_runtime_enabled:
            try:
                snapshot = self.telemetry_service.capture_snapshot(
                    mood_score=self.mood_score,
                    inferred_pos_x=self.inferred_pos_x,
                )
                self._maybe_tick_subconscious_runtime(
                    channel=channel or "assistant_output",
                    snapshot=snapshot,
                    internal_state=self._get_internal_state(),
                    user_text="",
                    event_text=str(payload.get("memory_text", payload.get("text", "")) or ""),
                    event_strength=0.52,
                    allow_lane5=False,
                    reflection=False,
                )
            except Exception as exc:
                self.logger.warning("Subconscious post-output tick error: %s", exc)
        return True

    def _private_mind_digest_for_payload(self) -> dict[str, object]:
        digest = getattr(self, "private_mind_digest", None)
        if not isinstance(digest, dict):
            return {}
        enriched = dict(digest)
        reason = self._private_mind_speech_silence_reason()
        if reason:
            enriched["speech_silence_reason"] = reason
            menu = dict(enriched.get("menu", {}) or {})
            menu["speech_silence_reason"] = reason
            enriched["menu"] = menu
        return self._compact_private_mind_digest_for_udp(enriched)

    def _compact_private_mind_digest_for_udp(self, digest: dict[str, object]) -> dict[str, object]:
        menu = digest.get("menu", {}) if isinstance(digest, dict) else {}
        if not isinstance(menu, dict):
            menu = {}
        keys = (
            "snapshot_current",
            "current_thought",
            "retained_thoughts",
            "pulled_thoughts",
            "temporary_thoughts",
            "open_concerns",
            "evidence_dossier",
            "mini_evaluators",
            "inner_judgment",
            "state_mutations",
            "future_bias",
            "surface_queue",
            "speech_silence_reason",
            "debug_api",
        )
        compact_menu = {
            key: self._compact_private_mind_debug_value(menu.get(key), depth=0)
            for key in keys
            if key in menu
        }
        return {
            "last_tick_at": digest.get("last_tick_at", 0.0),
            "current": self._compact_private_mind_debug_value(digest.get("current", {}), depth=0),
            "inner_diary": self._compact_private_mind_debug_value(digest.get("inner_diary", {}), depth=0),
            "anchor": self._compact_private_mind_debug_value(digest.get("anchor", {}), depth=0),
            "menu": compact_menu,
        }

    def _compact_private_mind_debug_value(self, value: object, *, depth: int = 0) -> object:
        if depth >= 5:
            return self._compact_private_mind_scalar(value, limit=220)
        if isinstance(value, dict):
            out: dict[str, object] = {}
            for idx, (key, item) in enumerate(value.items()):
                if idx >= 10:
                    out["..."] = "truncated"
                    break
                out[str(key)[:80]] = self._compact_private_mind_debug_value(item, depth=depth + 1)
            return out
        if isinstance(value, (list, tuple)):
            items = list(value)
            return [
                self._compact_private_mind_debug_value(item, depth=depth + 1)
                for item in items[:8]
            ] + (["... truncated"] if len(items) > 8 else [])
        return self._compact_private_mind_scalar(value, limit=360)

    @staticmethod
    def _compact_private_mind_scalar(value: object, *, limit: int = 260) -> object:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        text = " ".join(str(value or "").split())
        if len(text) > limit:
            return text[: max(0, limit - 3)].rstrip() + "..."
        return text

    def _private_mind_speech_silence_reason(self) -> dict[str, object]:
        """Tóm tắt quyền phát ngôn theo Architecture C, không đọc speech_core cũ."""
        from runtime.conversation.projection import legacy_semantic_projection
        semantic_frame = legacy_semantic_projection(self)
        plan = getattr(self, "last_ai_intention_plan", None)
        plan_payload = plan.to_dict() if plan is not None and callable(getattr(plan, "to_dict", None)) else {}
        return {
            "architecture": "conversation_kernel_v12",
            "chat_active": bool(getattr(self, "chat_mode", False) or getattr(self, "_chat_processing", False)),
            "autonomous_turn_collision_until": round(float(getattr(self, "autonomous_turn_collision_until", 0.0) or 0.0), 3),
            "semantic_frame": {
                key: semantic_frame.get(key)
                for key in ("version", "discourse_relation", "user_act", "target", "ambiguity_owner", "tool_intent")
                if key in semantic_frame
            },
            "autonomous_plan": {
                key: plan_payload.get(key)
                for key in ("speak", "motive", "strategy", "semantics", "activity")
                if key in plan_payload
            },
        }

    def _handle_tts_playback_event(self, event, *, stage_frame=None, trace_id: str = "") -> None:
        """Nhận lifecycle event từ TTS worker.

        Mặc định chỉ lưu/log để không phá client Godot cũ. Nếu muốn UI nhận event
        realtime, bật ``ERIS_TTS_EVENT_PAYLOAD=1``; payload mới có ``type=tts_event``
        nên client có thể bỏ qua nếu chưa hỗ trợ.
        """
        event_type = str(getattr(event, "type", "") or "")
        turn_segments = tuple(getattr(event, "turn_segments", ()) or ())
        try:
            segment_count = len(turn_segments) or int(getattr(stage_frame, "segment_count", 0) or 1)
        except (TypeError, ValueError):
            segment_count = 1
        record = {
            "type": event_type,
            "turn_id": str(getattr(event, "turn_id", "") or getattr(stage_frame, "turn_id", "") or ""),
            "segment_id": int(getattr(event, "segment_id", getattr(stage_frame, "segment_id", 0)) or 0),
            "segment_count": max(1, int(segment_count)),
            "reason": str(getattr(event, "reason", "") or ""),
            "error": str(getattr(event, "error", "") or ""),
            "prosody_style": str(getattr(stage_frame, "prosody_style", "") or ""),
        }
        binding = dict(getattr(stage_frame, "delivery", {}) or {}).get("learning_canvas", {})
        if isinstance(binding, dict) and binding:
            record["generation"] = int(binding.get("generation", 0))
            canvas = getattr(self, "learning_canvas_service", None)
            if canvas is not None:
                packet = canvas.playback.event(event_type, record["turn_id"], record["segment_id"])
                if packet:
                    self._send_runtime_json(packet, channel="learning_canvas")
        audio_dur = int(getattr(event, "audio_duration_ms", 0) or getattr(stage_frame, "audio_duration_ms", 0) or 0)
        if audio_dur > 0:
            record["audio_duration_ms"] = audio_dur
        self.last_tts_event = record
        # ``audio_cancelled`` trước đây chỉ đổi trạng thái UI, không settle
        # transcript V12. P0 bổ sung outcome riêng cho history/ledger, không
        # đồng nhất "đã hiện chữ" với "đã nói xong".
        if event_type in {"audio_done", "audio_failed", "audio_cancelled"}:
            try:
                from runtime.conversation.speech_outcome import note_public_delivery_outcome
                last_segment = int(record["segment_id"]) >= max(0, int(record["segment_count"]) - 1)
                if event_type != "audio_done" or last_segment:
                    note_public_delivery_outcome(
                        self, delivery_id=str(trace_id or record["turn_id"]),
                        status={"audio_done": "delivered", "audio_failed": "failed", "audio_cancelled": "cancelled"}[event_type],
                    )
            except Exception as exc:
                self.logger.debug("[conversation-v12] public speech outcome degraded: %s", exc)
        try:
            from runtime.ai_orchestrated.autonomous_trace import append_public_turn_trace
            public_trace_id = str(trace_id or record["turn_id"] or "")
            append_public_turn_trace("tts_event", {
                "trace_id": public_trace_id,
                "turn_id": record["turn_id"],
                "stage": "tts",
                "owner": "tts",
                "tts_event": event_type,
                "outcome": (
                    "delivered" if event_type == "audio_done" else
                    "cancelled" if event_type == "audio_cancelled" else
                    "failed" if event_type == "audio_failed" else "pending"
                ),
            }, logger=getattr(self, "logger", None))
            if event_type in {"audio_done", "audio_cancelled", "audio_failed"}:
                append_public_turn_trace("outcome", {
                    "trace_id": public_trace_id,
                    "turn_id": record["turn_id"],
                    "stage": "outcome",
                    "owner": "public_lifecycle",
                    "outcome": (
                        "delivered" if event_type == "audio_done" else
                        "cancelled" if event_type == "audio_cancelled" else "failed"
                    ),
                }, logger=getattr(self, "logger", None))
        except Exception:
            pass
        if event_type == "audio_start":
            # Đây mới là mốc playback bắt đầu, không phải lúc enqueue audio.
            # Delivery ID buộc khớp active generation trước khi cập nhật receipt.
            try:
                from runtime.conversation.bootstrap import ensure_conversation_kernel
                ensure_conversation_kernel(self).mark_audio_started(
                    delivery_id=str(record.get('turn_id', '') or ''))
            except Exception:
                pass
            self.logger.info(
                "[TTS:event] audio_start turn=%s segment=%s text=%r",
                record["turn_id"],
                record["segment_id"],
                str(getattr(event, "text", "") or "")[:120],
            )
            try:
                from runtime.runtime_status import emit_runtime_status
                emit_runtime_status(
                    self, activity="agent_turn", phase="speaking",
                    label="Eris • đang nói…", active=True, tone="active", priority=100,
                )
            except Exception:
                pass
        elif event_type == "audio_done":
            self.logger.info(
                "[TTS:event] audio_done turn=%s segment=%s",
                record["turn_id"],
                record["segment_id"],
            )
            try:
                final_segment = int(record.get("segment_id", 0) or 0) >= max(
                    0, int(record.get("segment_count", 1) or 1) - 1
                )
            except Exception:
                final_segment = True
            if final_segment:
                try:
                    from runtime.conversation import RunPhase
                    from runtime.conversation.bootstrap import ensure_conversation_kernel
                    from runtime.runtime_status import emit_runtime_status

                    kernel = ensure_conversation_kernel(self)
                    _settle_conversation_delivery_event(
                        self, turn_id=str(record.get("turn_id", "") or ""), successful=True
                    )
                    session_id = str(
                        getattr(self, "conversation_id", "")
                        or getattr(self, "thread_id", "")
                        or "foreground:primary"
                    )
                    snapshot = kernel.snapshot(session_id)
                    if snapshot.phase == RunPhase.CONTINUATION_PENDING:
                        emit_runtime_status(
                            self, activity="agent_turn", phase="continuation_pending",
                            label="Eris • đang nghĩ tiếp…", active=True, tone="active",
                            priority=92, notice_ms=0,
                        )
                    else:
                        emit_runtime_status(
                            self, activity="agent_turn", phase="waiting_user",
                            label="", active=False, tone="muted",
                            priority=50, notice_ms=0,
                        )
                except Exception as exc:
                    self.logger.debug("[conversation-v12] post-audio lifecycle degraded: %s", exc)
        elif event_type == "audio_cancelled":
            # Settlement failure chỉ dành cho run còn đang active. Nếu user
            # đã preempt, Kernel tự bỏ receipt cũ, status history vẫn được ghi
            # độc lập ở phía trên theo đúng delivery_id.
            _settle_conversation_delivery_event(
                self, turn_id=str(record.get("turn_id", "") or ""),
                successful=False, cancelled=True
            )
        elif event_type == "audio_failed":
            self.logger.warning(
                "[TTS:event] audio_failed turn=%s segment=%s error=%s reason=%s",
                record["turn_id"],
                record["segment_id"],
                record["error"] or "<none>",
                record["reason"] or "<none>",
            )
            try:
                _settle_conversation_delivery_event(
                    self, turn_id=str(record.get("turn_id", "") or ""), successful=False
                )
            except Exception as exc:
                self.logger.debug("[conversation-v12] failed delivery settle degraded: %s", exc)
        try:
            events = getattr(self, "tts_event_log", None)
            if not isinstance(events, list):
                events = []
                self.tts_event_log = events
            events.append(record)
            if len(events) > 40:
                del events[:-40]
        except Exception:
            pass

        if event_type in {"audio_failed", "audio_cancelled"}:
            self.logger.debug("[TTS:event] %s", record)
        try:
            self._ensure_voice_manager().track_tts_event(event, stage_frame=stage_frame)
        except Exception as exc:
            self.logger.debug("[interrupt] track tts event failed: %s", exc)
        try:
            bus = getattr(self, "stage_event_bus", None)
            if bus is not None:
                bus.emit(playback_event(event, stage_frame=stage_frame))
        except Exception as exc:
            self.logger.debug("[stage-protocol] playback event failed: %s", exc)
        # Godot cần nhận audio_start/done để phân biệt đang nói và đã dừng.
        # Gói này chỉ là lifecycle/ID, không chở transcript nên rất nhỏ.
        if os.environ.get("ERIS_TTS_EVENT_PAYLOAD", "1").strip().lower() in {"1", "true", "yes", "on"}:
            try:
                legacy_record = {key: value for key, value in record.items() if key != "type"}
                self.transport.send_json({"type": "tts_event", "event_type": event_type, **legacy_record})
            except Exception as exc:
                self.logger.debug("[TTS:event] send failed: %s", exc)

    def _ensure_public_turn_runtime(self) -> PublicTurnRuntime:
        runtime = getattr(self, "public_turn_runtime", None)
        if isinstance(runtime, PublicTurnRuntime) and runtime.owner is self:
            return runtime
        runtime = PublicTurnRuntime(self)
        self.public_turn_runtime = runtime
        return runtime

    def _record_public_turn(self, decision: Decision, *, payload: dict | None = None, channel: str | None = None):
        return self._ensure_public_turn_runtime().record(decision, payload=payload, channel=channel)

    def _infer_public_turn_channel_from_runtime(self, decision: Decision) -> str:
        return self._ensure_public_turn_runtime().infer_channel(decision)

    def _load_rapid_burst_window(self) -> float:
        raw = os.environ.get("ERIS_RAPID_BURST_WINDOW", "1.2").strip()
        try:
            return max(0.0, float(raw))
        except ValueError:
            return 1.2

    def _note_autonomous_turn_collision(
        self,
        now: float | None = None,
        *,
        seconds: float | None = None,
    ) -> None:
        """Ghi boundary cơ học của public turn; không khóa self-cognition."""
        self._ensure_public_turn_runtime().note_turn_collision(now, seconds=seconds)

    def _link_previous_public_turn_for_chat(self, user_timestamp: float):
        return self._ensure_public_turn_runtime().link_previous_for_chat(user_timestamp)

    def _ensure_interrupt_ledger(self) -> InterruptLedger:
        ledger = getattr(self, "interrupt_ledger", None)
        if isinstance(ledger, InterruptLedger):
            return ledger
        ledger = InterruptLedger()
        self.interrupt_ledger = ledger
        return ledger

    def _ensure_voice_manager(self) -> VoiceManager:
        manager = getattr(self, "voice_manager", None)
        ledger = self._ensure_interrupt_ledger()
        if not isinstance(manager, VoiceManager) or manager.ledger is not ledger:
            manager = VoiceManager(ledger=ledger, tts_engine=getattr(self, "tts_engine", None))
            self.voice_manager = manager
        else:
            manager.bind_tts_engine(getattr(self, "tts_engine", None))
        return manager

    def _capture_speech_interrupt_for_user(self, user_text: str, *, timestamp: float | None = None):
        """Ghi nhận user chen vào khi Eris đang phát TTS.

        Đây là state phiên ngắn hạn. Ta dừng audio ngay để turn-taking có cảm giác
        thật, nhưng chỉ đưa prompt context nếu ledger xác nhận audio đã thật sự
        ``audio_start`` và còn phần pending đáng kể.
        """
        text = str(user_text or "").strip()
        if not text:
            return None
        event = None
        try:
            # Caller legacy có thể gọi method này trước classifier ở chat ingress.
            # VoiceManager V12 tự bảo vệ backchannel khỏi hard-cancel, nên ở đây
            # không được tự suy "có text = chắc chắn user giành lượt" nữa.
            result = self._ensure_voice_manager().interrupt_with_text(
                text,
                timestamp=time.time() if timestamp is None else float(timestamp),
                reason="user_interrupt",
            )
            event = result.interrupt_event
            if str(getattr(result, "strategy", "") or "") == "continue_short":
                self.logger.debug("[interrupt] backchannel giữ nguyên playback=%r", text[:80])
                return None
        except Exception as exc:
            self.logger.debug("[interrupt] voice manager failed: %s", exc)

        if event is not None:
            self.pending_speech_interrupt_event = event
            # Tạo SuspendedMove lưu vào OpenLoopStore ngay khi bắt được sự kiện user cắt ngang
            # để bảo toàn phần chưa nói dưới dạng hint cho lượt Root kế tiếp.
            try:
                from runtime.open_loop_store import ensure_open_loop_store
                sid = str(
                    getattr(self, "conversation_id", "")
                    or getattr(self, "thread_id", "")
                    or "foreground:primary"
                ).strip()[:180]
                last_turn = getattr(self, "last_agent_turn", None)
                last_intent = str(last_turn.get("intent", "") if isinstance(last_turn, dict) else "")
                last_move_id = str(last_turn.get("move_id", "") if isinstance(last_turn, dict) else "")
                ensure_open_loop_store(self).capture_from_interrupt(
                    event,
                    session_id=sid,
                    source_move_id=last_move_id or str(getattr(event, "interrupted_turn_id", "") or ""),
                    intent=last_intent,
                    now=float(getattr(event, "timestamp", time.time()) or time.time()),
                )
            except Exception as exc:
                self.logger.debug("[open-loop] capture in _capture_speech_interrupt_for_user failed: %s", exc)
            self.logger.info(
                "[interrupt] user cut speech turn=%s seq=%s spoken=%r pending=%r",
                event.interrupted_turn_id,
                event.interrupted_seq,
                event.spoken_text_so_far,
                event.pending_text,
            )
        return event

    def _consume_speech_interrupt_event(self):
        event = getattr(self, "pending_speech_interrupt_event", None)
        self.pending_speech_interrupt_event = None
        return event

    def _previous_public_turn_context(self, public_turn) -> str:
        return self._ensure_public_turn_runtime().previous_turn_context(public_turn)

    def _build_body_bias_payload(self) -> dict[str, object]:
        return self.body_bias_projector.build_payload(
            observer_frame=getattr(self, "last_observer_frame", None),
            impulse_hint={},
        )

    def _build_emotion_debug_payload(self) -> dict[str, object]:
        """Compact affect/state payload for the Godot emotion panel."""
        state = self._get_internal_state()
        appraisal = None
        self_state = None
        meaning = None
        impulse = None
        from runtime.conversation.projection import legacy_semantic_projection
        semantic_frame = legacy_semantic_projection(self)
        relationship = getattr(state, "relationship_dynamics", None)
        desire = getattr(state, "desire", None)
        style = getattr(self, "fe_style_memory", None)
        overrides = getattr(self, "_debug_overrides", {})

        def clamp01(value: object) -> float:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                numeric = 0.0
            if numeric != numeric:
                numeric = 0.0
            return max(0.0, min(1.0, numeric))

        def clamp_signed(value: object) -> float:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                numeric = 0.0
            if numeric != numeric:
                numeric = 0.0
            return max(-1.0, min(1.0, numeric))

        def value(source: object, name: str, default: object = 0.0) -> object:
            return getattr(source, name, default) if source is not None else default

        def get_val(source: object, name: str, default: object = 0.0, is_rel: bool = False) -> object:
            ov_key = f"rel_{name}" if is_rel else name
            if overrides and ov_key in overrides:
                val = overrides[ov_key]
                return val[0] if (isinstance(val, tuple) and len(val) == 2) else val
            if name == "warmth_level" and overrides and "warmth" in overrides:
                val = overrides["warmth"]
                return val[0] if (isinstance(val, tuple) and len(val) == 2) else val
            if name == "sarcasm_level" and overrides and "sarcasm" in overrides:
                val = overrides["sarcasm"]
                return val[0] if (isinstance(val, tuple) and len(val) == 2) else val
            return value(source, name, default)

        def metric(label: str, raw: object, *, group: str = "", signed: bool = False, note: str = "") -> dict[str, object]:
            numeric = clamp_signed(raw) if signed else clamp01(raw)
            return {
                "label": label,
                "value": round(numeric, 3),
                "group": group,
                "note": note,
            }

        # V43: panel/stage/TTS đọc cùng affect hiệu dụng với text writer. Không
        # để avatar bình thản trong lúc public writer vừa nhận một playful impulse.
        live_affect = effective_affect_values(state)
        valence = clamp_signed(live_affect.get("valence", get_val(appraisal, "valence", value(state, "valence", 0.0))))
        live_arousal = clamp01(live_affect.get("arousal", get_val(appraisal, "arousal", value(state, "arousal", 0.0))))
        if valence >= 0.18:
            mood_label = "vui"
        elif valence <= -0.18 or clamp01(live_affect.get("irritation", get_val(state, "irritation", 0.0))) >= 0.45:
            mood_label = "kho_chiu"
        else:
            mood_label = "trung_tinh"

        overload = clamp01(max(
            clamp01(get_val(self_state, "overload", 0.0)),
            clamp01(get_val(state, "fatigue", 0.0)),
            clamp01(get_val(state, "cognitive_tension", 0.0)),
            clamp01(get_val(state, "restraint_pressure", 0.0)) * 0.55,
        ))
        stability = clamp01(1.0 - max(
            abs(valence) * 0.50,
            live_arousal,
            clamp01(get_val(state, "cognitive_tension", 0.0)),
            clamp01(live_affect.get("irritation", get_val(state, "irritation", 0.0))),
        ))
        mood_inertia = clamp01(
            clamp01(get_val(state, "recursive_reactivation_pressure", 0.0)) * 0.34
            + clamp01(get_val(state, "memory_replay_pressure", 0.0)) * 0.28
            + clamp01(get_val(state, "thought_stream_pressure", 0.0)) * 0.22
            + min(1.0, float(getattr(state, "cycle_count", 0) or 0) / 80.0) * 0.16
        )
        forced_reply = clamp01(min(1.0, float(getattr(state, "force_speak_ticks", 0) or 0) / 6.0))

        surface_intent = str(semantic_frame.get("user_act", "") or "")
        return {
            "summary": {
                "label": "vui / kho_chiu / trung_tinh",
                "value": mood_label,
                "valence": round(valence, 3),
                "arousal": round(live_arousal, 3),
                "delight": round(clamp01(live_affect.get("delight", 0.0)), 3),
                "playfulness": round(clamp01(live_affect.get("playfulness", 0.0)), 3),
                "irritation": round(clamp01(live_affect.get("irritation", 0.0)), 3),
                "state": str(value(self_state, "state_label", "") or value(state, "last_self_state_label", "") or ""),
            },
            "private_mind": getattr(self, "private_mind_digest", {}) or {},
            "private_life": getattr(self, "private_life_digest", {}) or {},
            "items": {
                "calm_aroused": metric("binh tinh <-> kich thich", live_arousal, group="core"),
                "emotional_stability": metric("on dinh cam xuc", stability, group="core"),
                "mood_inertia": metric("quan tinh mood", mood_inertia, group="core"),
                "attachment": metric("muc quan tam/gan bo", get_val(state, "attachment", 0.0), group="bond"),
                "tenderness": metric("diu dang", live_affect.get("tenderness", value(state, "tenderness", 0.0)), group="bond"),
                "protectiveness": metric("muon bao ve", get_val(appraisal, "protectiveness", value(state, "protectiveness", 0.0)), group="bond"),
                "emotional_exposure": metric("lo cam xuc nhieu hay it", get_val(relationship, "attachment_visibility", 0.0, is_rel=True), group="bond"),
                "irritation": metric("kho chiu", live_affect.get("irritation", get_val(state, "irritation", 0.0)), group="friction"),
                "perceived_rejection": metric("cam giac bi tu choi", get_val(relationship, "rupture", 0.0, is_rel=True), group="friction"),
                "confrontation": metric("xu huong doi dau", max(float(get_val(appraisal, "threat", 0.0)), float(live_affect.get("irritation", get_val(state, "irritation", 0.0)))), group="friction"),
                "post_exposure_recoil": metric("rut lai sau khi lo cam xuc", get_val(relationship, "exposure_recoil", 0.0, is_rel=True), group="friction"),
                "curiosity": metric("to mo", max(float(live_affect.get("curiosity", get_val(state, "curiosity", 0.0))), float(get_val(state, "curiosity_pressure", 0.0))), group="attention"),
                "attention_bias": {
                    "label": "thien huong chu y",
                    "value": str(semantic_frame.get("discourse_relation", "") or value(state, "last_focus_kind", "") or ""),
                    "group": "attention",
                    "note": "",
                },
                "association_pressure": metric("lien tuong lan rong", get_val(state, "association_pressure", 0.0), group="attention"),
                "thought_stream_pressure": metric("dong suy nghi tang", get_val(state, "thought_stream_pressure", 0.0), group="attention"),
                "speech_pressure": metric("ap luc phai noi", get_val(state, "silence_pressure", 0.0), group="speech"),
                "social_pressure": metric("ap luc xa hoi", get_val(state, "social_pressure", 0.0), group="speech"),
                "forced_reply": metric("bi ep phan hoi", forced_reply, group="speech"),
                "brevity": metric("xu huong noi ngan", get_val(state, "restraint_pressure", 0.0), group="speech"),
                "playfulness": metric("muc dua", live_affect.get("playfulness", get_val(state, "playful_pressure", 0.0)), group="tone"),
                "tease": metric("thich treu", get_val(style, "sarcasm_level", 0.0), group="tone"),
                "sarcasm": metric("mia mai", get_val(style, "sarcasm_level", 0.0), group="tone"),
                "warmth": metric("do am ap", get_val(style, "warmth_level", value(appraisal, "tenderness", 0.0)), group="tone"),
                "distance": metric("giu khoang cach", get_val(relationship, "preferred_distance", 0.58, is_rel=True), group="tone"),
                "intimacy": metric("muc than mat", get_val(appraisal, "intimacy", 0.0), group="tone"),
                "romantic_boundary_probe": metric("do ranh gioi tinh cam", get_val(desire, "unresolved_residue", 0.0), group="tone"),
                "restraint": metric("tu kiem che", get_val(state, "restraint_pressure", 0.0), group="control"),
                "cognitive_tension": metric("cang thang nhan thuc", get_val(state, "cognitive_tension", 0.0), group="control"),
                "repeating_thought": metric("suy nghi lap lai", get_val(state, "recursive_reactivation_pressure", 0.0), group="control"),
                "unresolved_prediction": metric("du doan chua giai quyet", get_val(state, "unresolved_prediction_pressure", 0.0), group="control"),
                "subconscious_pressure": metric("ap luc tiem thuc", get_val(state, "subconscious_pressure", 0.0), group="control"),
                "fatigue": metric("met", get_val(appraisal, "fatigue", value(state, "fatigue", 0.0)), group="load"),
                "overload": metric("qua tai", overload, group="load"),
                "speech_emotional_color": {
                    "label": "mau cam xuc khi noi",
                    "value": str(value(style, "last_tone_label", "") or ""),
                    "group": "surface",
                    "note": "",
                },
                "emotional_reflex": {
                    "label": "phan xa cam xuc",
                    "value": str(semantic_frame.get("boundary_action", "") or semantic_frame.get("target", "") or ""),
                    "group": "surface",
                    "note": "",
                },
                "emotional_residue": {
                    "label": "du am cam xuc",
                    "value": str(semantic_frame.get("ambiguity_owner", "") or ""),
                    "group": "surface",
                    "note": "",
                },
                "surface_intent": {
                    "label": "y dinh bieu hien ra ngoai",
                    "value": surface_intent,
                    "group": "surface",
                    "note": "",
                },
            },
        }

    @staticmethod
    def _body_bias_clamp(value: object) -> float:
        return BodyBiasProjector.clamp(value)

    @staticmethod
    def _normalize_body_focus_kind(value: str) -> str:
        return BodyBiasProjector.normalize_focus_kind(value)

    @staticmethod


    #chống “spam evidence”, tránh trùng lặp, và giữ payload gọn/ổn định khi log/telemetry.

    #hàm này đảm bảo event tạo ra luôn có content thật, các field không bị rỗng/bẩn, và payload không bị phình

    # quyết định mức “nhạy cảm/riêng tư” (privacy_level) theo loại telemetry

    #quyết định “gợi ý độ mới/tính chất” (novelty_hint) để phân loại event cho downstream (chọn chú ý, ghi nhớ, log






















    def _build_relational_context(self, state: PersistentInternalState | None = None) -> RelationalContext:
        state = state or self._get_internal_state()
        recent_notes = " ".join(str(item or "") for item in tuple(getattr(state, "recent_notes", ()) or ())).lower()
        mental_processes = getattr(state, "mental_processes", None)
        continuity_process = getattr(mental_processes, "continuity", None)
        session_return = min(1.0, max(0, int(getattr(state, "session_resume_count", 0) or 0)) / 2.0)
        ritual_return = 1.0 if float(getattr(state, "last_session_ritual_at", 0.0) or 0.0) > 0.0 else 0.0
        memory_continuity = max(
            float(getattr(state, "recursive_reactivation_pressure", 0.0) or 0.0),
            float(getattr(state, "memory_replay_pressure", 0.0) or 0.0),
            float(getattr(state, "last_memory_resonance", 0.0) or 0.0),
            float(getattr(continuity_process, "level", 0.0) or 0.0),
        )
        ritual_note = 1.0 if any(
            marker in recent_notes
            for marker in ("session_ritual", "continuity_restored", "long_silence_return", "memory_replay", "recursive")
        ) else 0.0
        recent_channels = tuple(str(item or "") for item in tuple(getattr(state, "recent_channels", ()) or ()))
        long_silence_return = 1.0 if (
            float(getattr(state, "last_elapsed_seconds", 0.0) or 0.0) >= 600.0
            and any(item in {"chat", "force_speak"} for item in recent_channels[-4:])
        ) else 0.0
        sustained_presence = min(
            1.0,
            (
                float(getattr(state, "last_social_presence_confidence", 0.0) or 0.0)
                + float(getattr(state, "last_social_presence_intensity", 0.0) or 0.0)
            )
            * 0.5,
        )
        continuity_score = min(
            1.0,
            session_return * 0.28
            + ritual_return * 0.18
            + memory_continuity * 0.34
            + ritual_note * 0.12
            + long_silence_return * 0.10
            + sustained_presence * 0.08,
        )
        attachment = float(getattr(state, "attachment", 0.0) or 0.0)
        if continuity_score >= 0.66 and attachment >= 0.62:
            stage = "rooted"
        elif continuity_score >= 0.52 and attachment >= 0.50:
            stage = "established"
        elif continuity_score >= 0.38:
            stage = "forming"
        else:
            stage = "low_context"
        return RelationalContext(
            attachment_level=attachment,
            continuity_score=continuity_score,
            social_resonance=float(getattr(state, "social_energy", 0.0) or 0.0),
            recent_presence_density=sustained_presence,
            abandonment_sensitivity=max(
                float(getattr(state, "silence_pressure", 0.0) or 0.0),
                float(getattr(getattr(state, "desire", None), "unresolved_residue", 0.0) or 0.0),
            ),
            trust_stability=max(
                0.0,
                min(1.0, float(getattr(state, "confidence", 0.0) or 0.0) - float(getattr(state, "irritation", 0.0) or 0.0) * 0.35),
            ),
            relationship_stage=stage,
        )

# -*- coding: utf-8 -*-
"""Background observer projection, private-mind/private-life state and daydream evidence.

Extracted from RuntimeCoordinator as a domain runtime mixin.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import Future

from cortex.daydream_consolidation import DaydreamPacket
from cortex.observer_contracts import ObserverFrame, SignalSnapshot, stable_hash
from runtime.telemetry_text_policy import is_generic_telemetry_line


_LOG = logging.getLogger(__name__)


class ObserverPrivateRuntimeMixin:
    """Coordinator-facing policy surface; lifecycle remains supervised by the owner."""

    def _observer_boundary_note(self, operation: str, exc: Exception) -> None:
        logger = getattr(self, "logger", None)
        method = getattr(logger, "debug", None)
        if callable(method):
            try:
                method("[observer/private] %s degraded: %s", operation, exc)
                return
            except Exception as log_exc:
                _LOG.debug("observer/private logger failed: %s", log_exc, exc_info=log_exc)
        _LOG.debug("[observer/private] %s degraded: %s", operation, exc, exc_info=exc)

    def _preempt_background_for_chat(self, reason: str = "user_turn") -> None:
        """Best-effort preemption for optional background work before public chat."""

        observer = getattr(self, "ai_post_turn_observer", None)
        discard_pending = getattr(observer, "discard_pending", None)
        if callable(discard_pending):
            try:
                discard_pending(str(reason or "user_turn"))
            except Exception as exc:
                self._observer_boundary_note("post_observer.preempt", exc)

        # Tăng generation để kết quả PrivateMind cũ không được apply sau khi
        # user đã mở một lượt chat mới. Đồng thời xóa snapshot catch-up đang chờ.
        schedule_lock = getattr(self, "_private_mind_schedule_lock", None)
        if schedule_lock is None:
            schedule_lock = threading.RLock()
            self._private_mind_schedule_lock = schedule_lock
        dropped_latest = None
        with schedule_lock:
            self._private_mind_generation = int(getattr(self, "_private_mind_generation", 0) or 0) + 1
            dropped_latest = getattr(self, "_private_mind_latest_tick_kwargs", None)
            self._private_mind_dirty = False
            self._private_mind_latest_tick_kwargs = None
        if isinstance(dropped_latest, dict):
            self._capture_private_mind_unprocessed_kwargs(dropped_latest, reason="user_preempted_pending_snapshot")

        pending = getattr(self, "_private_mind_future", None)
        if pending is not None and not pending.done():
            try:
                if pending.cancel():
                    self._private_mind_future = None
                    self._private_mind_future_generation = None
            except Exception as exc:
                self._observer_boundary_note("private_mind.preempt_future", exc)

        try:
            from runtime.inference_broker import GLOBAL_INFERENCE_BROKER

            GLOBAL_INFERENCE_BROKER.cancel_scope("background:private_mind")
        except Exception as exc:
            self._observer_boundary_note("private_mind.preempt_broker", exc)

    def _run_background_observer(
        self,
        *,
        snapshot,
        channel: str = "",
        user_text: str = "",
        vision_facts: tuple[str, ...] = (),
        started_at: float | None = None,
    ) -> ObserverFrame | None:
        """Chạy BackgroundObserver trên snapshot hiện tại.

        Observer chỉ tạo hint/evidence. Nó không nói, không commit memory và
        không tự đánh thức main cognition. Nếu nó muốn wake, runtime chỉ log hint.
        """

        observer = getattr(self, "background_observer", None)
        signal_buffer = getattr(self, "signal_buffer", None)
        if observer is None or signal_buffer is None:
            return None

        try:
            signal_snapshot = self._build_signal_snapshot_for_observer(
                snapshot=snapshot,
                channel=channel,
                user_text=user_text,
                vision_facts=vision_facts,
                started_at=started_at,
            )
            signal_diff = signal_buffer.push(signal_snapshot)
            observer_frame = observer.observe(
                signal_snapshot,
                signal_diff,
                recent_summary=signal_buffer.recent_summary(6),
            )
            self.last_signal_snapshot = signal_snapshot
            self.last_signal_diff = signal_diff
            self.last_observer_frame = observer_frame
            self._log_observer_trace(observer_frame)
            self._update_cortex_runtime_state(
                signal_snapshot=signal_snapshot,
                signal_diff=signal_diff,
                observer_frame=observer_frame,
                channel=channel,
                user_text=user_text,
            )
            if getattr(observer_frame, "should_wake_main", False):
                self.logger.info(
                    "[background observer] wake_main hint suppressed | salience=%.2f | err=%.2f",
                    float(getattr(observer_frame, "salience", 0.0) or 0.0),
                    float(getattr(observer_frame, "possible_error", 0.0) or 0.0),
                )
            return observer_frame
        except Exception as exc:
            self.logger.warning("Background observer error: %s", exc)
            return None


    def _log_observer_trace(self, observer_frame: ObserverFrame | None) -> None:
        if observer_frame is None:
            return

        mode = "llm" if bool(getattr(observer_frame, "debug", {}).get("llm_used", False)) else "rule"
        model = (
            getattr(getattr(self, "background_observer", None), "config", None).ollama_model
            if mode == "llm" and getattr(getattr(self, "background_observer", None), "config", None) is not None
            else "disabled"
        )
        self.logger.info("[Observer] mode=%s model=%s", mode, model)
        self.logger.info(
            "[Observer] action=%s salience=%.2f error=%.2f focus=%s intent=%s",
            str(getattr(observer_frame, "recommended_action", "observe_only") or "observe_only"),
            float(getattr(observer_frame, "salience", 0.0) or 0.0),
            float(getattr(observer_frame, "possible_error", 0.0) or 0.0),
            str(getattr(observer_frame, "focus_kind", "unknown") or "unknown"),
            str(getattr(observer_frame, "activity_intent", "unknown") or "unknown"),
        )
        state = self._get_internal_state()
        self.logger.info(
            "[Observer] speech_pressure=%.2f user_presence=%.2f novelty=%.2f repetition=%.2f",
            float(getattr(state, "silence_pressure", 0.0) or 0.0),
            float(getattr(observer_frame, "user_presence", 0.0) or 0.0),
            float(getattr(observer_frame, "novelty", 0.0) or 0.0),
            float(getattr(observer_frame, "repetition", 0.0) or 0.0),
        )


    def _update_cortex_runtime_state(
        self,
        *,
        signal_snapshot: SignalSnapshot,
        signal_diff,
        observer_frame: ObserverFrame | None,
        channel: str = "",
        user_text: str = "",
    ) -> None:
        """Cập nhật Cortex attention/drive mà không xét quyền phát ngôn.

        Main planner nhìn attention, discourse, OCR và telemetry rồi tự trả
        ``IntentionPlan`` typed. Cortex chỉ duy trì state nền.
        """
        runtime = getattr(self, "cortex_runtime", None)
        if runtime is None:
            return
        try:
            packets = self._daydream_packets_from_runtime(limit=20)
            result = runtime.tick(
                signal_snapshot=signal_snapshot,
                signal_diff=signal_diff,
                observer_frame=observer_frame,
                base_internal_state=self._get_internal_state(),
                memory_packets=packets,
                user_text=user_text,
                channel=channel or "autonomous",
            )
            self.rolling_user_context = result.user_context
            self.rolling_internal_state = result.internal_state
            self.last_cortex_attention = result.attention
            if result.daydream_result is not None:
                self.last_daydream_result = result.daydream_result
            self.logger.info(
                "[cortex attention] level=%s external=%.2f internal=%.2f interrupt=%.2f resource=%.2f vision_hint=%s budget=%s",
                result.attention.level,
                result.attention.external_pressure,
                result.attention.internal_drive,
                result.attention.interrupt_cost,
                result.attention.resource_caution,
                int(result.attention.vision_refresh_recommended),
                result.attention.budget_reason or "none",
            )
            # Private mind nhận attention để hiểu nhịp tài nguyên. Signal này
            # không mang trường semantic và không có quyền mở loa.
            self._update_private_mind_state(
                signal_snapshot=signal_snapshot,
                observer_frame=observer_frame,
                attention_signal=result.attention,
                channel=channel,
                user_text=user_text,
            )
            self._update_private_life_state(
                signal_snapshot=signal_snapshot,
                observer_frame=observer_frame,
                channel=channel,
                user_text=user_text,
            )
            if getattr(self, "tutor_daemon", None) is not None and not getattr(self, "_chat_processing", False):
                try:
                    from runtime.tutor_wiring import tutor_tick
                    tutor_tick(self, observer_frame=observer_frame, channel=channel or "autonomous", snapshot=signal_snapshot)
                except Exception as exc:
                    self.logger.warning("Curiosity Tutor tick error: %s", exc)
        except Exception as exc:
            self.logger.warning("Cortex runtime update error: %s", exc)


    def _update_private_mind_state(
        self,
        *,
        signal_snapshot,
        observer_frame,
        attention_signal,
        channel: str = "",
        user_text: str = "",
    ) -> None:
        """Chạy PrivateMind nền theo mô hình ``single-flight + dirty/latest``.

        P2 policy:

        * Chỉ có một Future PrivateMind chạy tại một thời điểm.
        * Khi có snapshot mới trong lúc Future đang chạy, không tạo queue mới;
          chỉ ghi ``dirty=True`` và thay bằng snapshot mới nhất.
        * Worker được phép chạy thêm tối đa một lượt catch-up với snapshot mới nhất.
        * User chat tăng ``generation``; kết quả thuộc generation cũ bị bỏ để tránh
          nội tâm cũ chảy ngược vào ngữ cảnh sau khi cuộc trò chuyện đã đổi hướng.

        Cách này giữ được tín hiệu mới mà không tạo một hàng đợi nhật ký nội tâm
        dài vô tận, thứ mà ngay cả con người cũng không cần thêm.
        """

        daemon = getattr(self, "private_mind_daemon", None)
        loop = getattr(self, "private_mind_loop", None)
        if daemon is None and loop is None:
            return

        schedule_lock = getattr(self, "_private_mind_schedule_lock", None)
        if schedule_lock is None:
            schedule_lock = threading.RLock()
            self._private_mind_schedule_lock = schedule_lock
        if not hasattr(self, "_private_mind_generation"):
            self._private_mind_generation = 0
        if not hasattr(self, "_private_mind_dirty"):
            self._private_mind_dirty = False
        if not hasattr(self, "_private_mind_latest_tick_kwargs"):
            self._private_mind_latest_tick_kwargs = None

        # --- Poll kết quả của Future đã xong ---
        pending: "Future | None" = getattr(self, "_private_mind_future", None)
        if pending is not None and pending.done():
            result_generation = getattr(self, "_private_mind_future_generation", None)
            self._private_mind_future = None
            self._private_mind_future_generation = None
            try:
                result = pending.result()
                # Chỉ apply nếu user chưa preempt generation này.
                if result_generation == int(getattr(self, "_private_mind_generation", 0) or 0):
                    if result is not None:
                        self._apply_private_mind_tick_result(result, daemon=daemon)
                else:
                    self.logger.debug(
                        "[PrivateMind] discarded stale generation=%s current=%s",
                        result_generation,
                        getattr(self, "_private_mind_generation", 0),
                    )
            except Exception as exc:
                try:
                    self.logger.warning("PrivateMind tick (bg) error: %s", exc)
                except Exception:
                    pass

        # Chat chính có ưu tiên tuyệt đối. Không lưu snapshot trong lúc chat vì
        # lượt autonomous tiếp theo sẽ cung cấp state mới và đáng tin hơn.
        if getattr(self, "_chat_processing", False):
            return

        state_committer = getattr(self, "state_committer", None)
        tick_kwargs = dict(
            user_text=user_text,
            channel=channel or "autonomous",
            snapshot=signal_snapshot,
            observer_frame=observer_frame,
            attention_signal=attention_signal,
            memory_core=getattr(self, "memory_core", None),
            state_committer=state_committer,
            session_turn_ledger=getattr(self, "session_turn_ledger", None),
            internal_agenda=getattr(self, "internal_agenda", None),
            thought_stream=getattr(self, "thought_stream", None),
            runtime_owner=self,
        )

        # Future đang chạy: coalesce mọi cập nhật vào đúng snapshot mới nhất.
        pending = getattr(self, "_private_mind_future", None)
        if pending is not None and not pending.done():
            overwritten = None
            with schedule_lock:
                overwritten = getattr(self, "_private_mind_latest_tick_kwargs", None)
                self._private_mind_latest_tick_kwargs = tick_kwargs
                self._private_mind_dirty = True
            if isinstance(overwritten, dict):
                self._capture_private_mind_unprocessed_kwargs(
                    overwritten, reason="singleflight_coalesced_overwritten"
                )
            return

        # --- Tạo executor lần đầu ---
        if not hasattr(self, "_private_mind_executor") or self._private_mind_executor is None:
            from concurrent.futures import ThreadPoolExecutor

            self._private_mind_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="ERIS-PrivateMind",
            )

        try:
            max_catchups = max(
                0,
                min(2, int(os.getenv("ERIS_PRIVATE_MIND_MAX_CATCHUP_TICKS", "1") or 1)),
            )
        except (TypeError, ValueError):
            max_catchups = 1

        with schedule_lock:
            generation = int(getattr(self, "_private_mind_generation", 0) or 0)
            # Nếu đã có dirty snapshot từ lúc Future cũ chạy, snapshot hiện tại
            # thường mới hơn; dùng snapshot hiện tại rồi xóa coalesced state.
            self._private_mind_dirty = False
            self._private_mind_latest_tick_kwargs = None

        def _run_one(current_kwargs):
            if daemon is not None and callable(getattr(daemon, "tick", None)):
                return ("daemon", daemon.tick(**current_kwargs))
            if loop is not None and callable(getattr(loop, "tick", None)):
                kw = dict(current_kwargs)
                kw["surface_candidate_queue"] = getattr(self, "private_mind_surface_queue", None)
                return ("loop", loop.tick(**kw))
            return None

        def _run_actor():
            """Chạy tick đầu và tối đa một tick bắt kịp snapshot mới nhất."""

            current_kwargs = tick_kwargs
            latest_result = None
            catchups = 0
            while True:
                latest_result = _run_one(current_kwargs)
                with schedule_lock:
                    current_generation = int(getattr(self, "_private_mind_generation", 0) or 0)
                    if current_generation != generation or getattr(self, "_chat_processing", False):
                        # User đã preempt; không tiếp tục catch-up bằng state cũ.
                        self._private_mind_dirty = False
                        self._private_mind_latest_tick_kwargs = None
                        break
                    if (
                        catchups >= max_catchups
                        or not bool(getattr(self, "_private_mind_dirty", False))
                        or not isinstance(getattr(self, "_private_mind_latest_tick_kwargs", None), dict)
                    ):
                        break
                    current_kwargs = self._private_mind_latest_tick_kwargs
                    self._private_mind_latest_tick_kwargs = None
                    self._private_mind_dirty = False
                    catchups += 1
            return latest_result

        try:
            self._private_mind_future_generation = generation
            self._private_mind_future = self._private_mind_executor.submit(_run_actor)
        except Exception as exc:
            self._private_mind_future_generation = None
            self._observer_boundary_note("private_mind.submit", exc)


    def _capture_private_mind_unprocessed_kwargs(self, kwargs: dict, *, reason: str) -> None:
        """Giữ dấu stimulus bị single-flight làm rơi, nhưng không gọi nó là thought."""
        loop = getattr(self, "private_mind_loop", None)
        capture = getattr(loop, "capture_unprocessed_stimulus", None)
        if not callable(capture):
            return
        text = str(kwargs.get("user_text") or "").strip()
        observer = kwargs.get("observer_frame")
        if not text and observer is not None:
            text = str(
                getattr(observer, "summary", "")
                or getattr(observer, "event", "")
                or getattr(observer, "activity_intent", "")
                or ""
            ).strip()
        snapshot = kwargs.get("snapshot")
        if not text and snapshot is not None:
            text = str(
                getattr(snapshot, "active_window_title", "")
                or getattr(snapshot, "active_app", "")
                or ""
            ).strip()
        if not text:
            return
        try:
            capture(
                text=text[:420],
                reason=reason,
                source_ids=(),
                salience=0.56,
                metadata={"channel": str(kwargs.get("channel") or "autonomous")},
            )
        except Exception as exc:
            self._observer_boundary_note("private_mind.capture_unprocessed", exc)

    def _apply_private_mind_tick_result(self, result, *, daemon) -> None:
        """Apply kết quả tick PrivateMind sau khi background thread hoàn tất."""
        kind, tick = result if (isinstance(result, tuple) and len(result) == 2) else ("unknown", None)
        loop = getattr(self, "private_mind_loop", None)
        try:
            if kind == "daemon" and daemon is not None:
                digest_fn = getattr(daemon, "digest", None)
                if callable(digest_fn):
                    self.private_mind_digest = digest_fn(surface_queue=getattr(daemon, "surface_queue", None))
                elif tick is not None and callable(getattr(tick, "to_dict", None)):
                    self.private_mind_digest = tick.to_dict()
                self.private_mind_surface_queue = getattr(daemon, "surface_queue", getattr(self, "private_mind_surface_queue", []))
            elif kind == "loop" and loop is not None:
                digest_fn = getattr(loop, "digest", None)
                if callable(digest_fn):
                    self.private_mind_digest = digest_fn(surface_queue=getattr(self, "private_mind_surface_queue", None))
                elif tick is not None and callable(getattr(tick, "to_dict", None)):
                    self.private_mind_digest = tick.to_dict()
            self._sync_private_mind_future_bias()
            self._persist_inner_diary()
        except Exception as exc:
            self._observer_boundary_note("private_mind.apply_result", exc)


    def _sync_private_mind_future_bias(self) -> None:
        """Đẩy ``future_bias`` từ digest -> các thuộc tính runtime mà tầng wording đọc.

        Trước đây ``_apply_private_mind_tick_result`` chỉ set ``private_mind_digest``,
        còn ``private_mind_speech_bias`` / ``private_mind_thought_pressure`` /
        ``private_mind_inquiry_plan`` không bao giờ được đồng bộ — chúng đứng yên ở giá
        trị khởi tạo ({} và 0.0). Vì renderer ``_render_private_mind_speech_bias_context``
        đọc đúng ``private_mind_speech_bias`` (rỗng) nên nội tâm của ERIS KHÔNG BAO GIỜ
        chảy vào câu nói (#3), dù bias đã được dựng sẵn trong ``digest['future_bias']``.
        Hàm này nối phần đã có sẵn đó vào đúng thuộc tính. Không bao giờ ném lỗi.
        """

        digest = getattr(self, "private_mind_digest", None)
        if not isinstance(digest, dict):
            return
        future_bias = digest.get("future_bias")
        if not isinstance(future_bias, dict):
            return
        speech_bias = future_bias.get("speech_bias")
        if isinstance(speech_bias, dict):
            self.private_mind_speech_bias = speech_bias
        inquiry_plan = future_bias.get("inquiry_plan")
        if isinstance(inquiry_plan, list):
            self.private_mind_inquiry_plan = inquiry_plan
        try:
            self.private_mind_thought_pressure = float(future_bias.get("thought_pressure") or 0.0)
        except (TypeError, ValueError):
            pass


    def _persist_inner_diary(self) -> None:
        """Ghi inner_diary hiện tại ra đĩa (throttled). Không bao giờ ném lỗi."""

        persister = getattr(self, "inner_diary_persister", None)
        if persister is None:
            return
        try:
            persister.maybe_persist(getattr(self, "private_mind_digest", None))
        except Exception as exc:
            # Persistence là tiện ích phụ; nó tuyệt đối không được làm gãy tick.
            self._observer_boundary_note("private_mind.persist", exc)


    def _drain_private_mind_surface_impulses(self, *, limit: int = 4) -> tuple[object, ...]:
        """PrivateMind chỉ cấp state/memory; queue speech cũ mặc định bị khóa."""

        from runtime.architecture_flags import private_cognition_surface_export_enabled
        if not private_cognition_surface_export_enabled():
            queue = getattr(self, "private_mind_surface_queue", None)
            if isinstance(queue, list):
                queue.clear()
            return ()
        daemon = getattr(self, "private_mind_daemon", None)
        drain = getattr(daemon, "drain_surface_impulses", None) if daemon is not None else None
        if callable(drain):
            try:
                return tuple(drain(limit=limit) or ())
            except Exception as exc:
                self._observer_boundary_note("private_mind.drain_surface", exc)
        queue = getattr(self, "private_mind_surface_queue", None)
        if not isinstance(queue, list) or not queue:
            return ()
        items: list[object] = []
        while queue and len(items) < max(1, int(limit or 4)):
            items.append(queue.pop(0))
        return tuple(items)


    def _update_private_life_state(
        self,
        *,
        signal_snapshot,
        observer_frame=None,
        channel: str = "",
        user_text: str = "",
    ) -> None:
        """Chạy PrivateLifeCore trên tick nền.

        PrivateLife không trả Decision. Nó chỉ cập nhật đời sống riêng: habitat,
        object attachments, private projects, solitude, taste, errands, life events.
        Surface seed nếu có vẫn phải đi qua SurfaceArbiter. Đời sống riêng không
        được cầm loa nhảy thẳng ra UI, dù nghe rất kịch tính.
        """

        daemon = getattr(self, "private_life_daemon", None)
        if daemon is None or getattr(self, "_chat_processing", False):
            return
        try:
            recent_events = tuple(getattr(signal_snapshot, "recent_events", ()) or ()) if signal_snapshot is not None else ()
            tick = daemon.tick(
                snapshot=signal_snapshot,
                runtime_owner=self,
                user_text=user_text,
                channel=channel or "autonomous",
                recent_events=recent_events,
            )
            digest_fn = getattr(daemon, "digest", None)
            if callable(digest_fn):
                self.private_life_digest = digest_fn(surface_queue=getattr(daemon, "surface_queue", None))
            elif tick is not None and callable(getattr(tick, "to_dict", None)):
                self.private_life_digest = tick.to_dict()
            self.private_life_core = getattr(daemon, "core", getattr(self, "private_life_core", None))
            self.private_life_surface_queue = getattr(daemon, "surface_queue", getattr(self, "private_life_surface_queue", []))
        except Exception as exc:
            self._observer_boundary_note("private_life.tick", exc)


    def _drain_private_life_surface_impulses(self, *, limit: int = 4) -> tuple[object, ...]:
        """PrivateLife chỉ cấp state/memory; queue speech cũ mặc định bị khóa."""

        from runtime.architecture_flags import private_cognition_surface_export_enabled
        if not private_cognition_surface_export_enabled():
            queue = getattr(self, "private_life_surface_queue", None)
            if isinstance(queue, list):
                queue.clear()
            return ()
        daemon = getattr(self, "private_life_daemon", None)
        drain = getattr(daemon, "drain_surface_impulses", None) if daemon is not None else None
        if callable(drain):
            try:
                return tuple(drain(limit=limit) or ())
            except Exception as exc:
                self._observer_boundary_note("private_life.drain_surface", exc)
        queue = getattr(self, "private_life_surface_queue", None)
        if not isinstance(queue, list) or not queue:
            return ()
        items: list[object] = []
        while queue and len(items) < max(1, int(limit or 4)):
            items.append(queue.pop(0))
        return tuple(items)


    def _private_life_digest_for_payload(self) -> dict[str, object]:
        """Digest gọn cho debug UI/body payload."""

        digest = getattr(self, "private_life_digest", None)
        if not isinstance(digest, dict):
            return {}
        return self._compact_private_mind_debug_value(digest, depth=0) if callable(getattr(self, "_compact_private_mind_debug_value", None)) else digest


    def _daydream_packets_from_runtime(self, *, limit: int = 20) -> tuple[DaydreamPacket, ...]:
        packets: list[DaydreamPacket] = []
        seen_ids: set[str] = set()
        seen_text: set[str] = set()

        def add(
            *,
            packet_id: str,
            kind: str,
            text: object,
            evidence_ids: tuple[str, ...] = (),
            importance: float = 0.5,
            confidence: float = 0.55,
            metadata: dict[str, object] | None = None,
        ) -> None:
            value = re.sub(r"\s+", " ", str(text or "")).strip()
            packet_key = str(packet_id or "").strip()
            if not packet_key or not value or packet_key in seen_ids or value in seen_text:
                return
            evidence = tuple(str(item) for item in evidence_ids if str(item).strip())[:12]
            if not evidence:
                return
            seen_ids.add(packet_key)
            seen_text.add(value)
            packets.append(
                DaydreamPacket(
                    id=packet_key,
                    kind=kind,  # type: ignore[arg-type]
                    text=value[:1000],
                    evidence_ids=evidence,
                    confidence=max(0.05, min(1.0, float(confidence))),
                    importance=max(0.05, min(1.0, float(importance))),
                    metadata=dict(metadata or {}),
                )
            )


        resonance = getattr(self, "active_memory_resonance", None)
        if resonance is not None:
            for idx, activation in enumerate(tuple(getattr(resonance, "activations", ()) or ())[:limit]):
                layer = str(getattr(activation, "layer", "") or "")
                kind = self._daydream_kind_from_memory_layer(layer)
                add(
                    packet_id=str(getattr(activation, "record_id", "") or f"memory_activation:{idx}"),
                    kind=kind,
                    text=getattr(activation, "text", ""),
                    evidence_ids=tuple(getattr(activation, "evidence_ids", ()) or ()),
                    importance=float(getattr(activation, "score", 0.55) or 0.55),
                    confidence=float(getattr(activation, "relevance", 0.60) or 0.60),
                    metadata={
                        "source": "memory_activation",
                        "layer": layer,
                        "reason": str(getattr(activation, "reason", "") or ""),
                    },
                )

        loader = getattr(self.memory_core, "load", None)
        if callable(loader):
            try:
                snapshot = loader()
            except Exception as exc:
                self.logger.warning("Daydream packet load error: %s", exc)
                snapshot = {}
            if isinstance(snapshot, dict):
                self._append_daydream_packets_from_memory_snapshot(snapshot, add, limit=limit)

        agenda = getattr(self, "internal_agenda", None)
        if agenda is not None:
            for i, item in enumerate(agenda.top_items(limit=5)):
                add(
                    packet_id=f"internal_agenda:{getattr(item, 'id', i)}",
                    kind="unresolved",
                    text=getattr(item, "summary", ""),
                    evidence_ids=tuple(getattr(item, "evidence_ids", ()) or ()),
                    importance=float(getattr(item, "pressure", 0.5) or 0.5),
                    confidence=float(getattr(item, "confidence", 0.5) or 0.5),
                    metadata={"source": "internal_agenda"},
                )

        if not packets:
            for i, text in enumerate(tuple(getattr(self, "long_term_memory", ()) or ())[:limit]):
                fallback_id = f"runtime_memory_packet:semantic:{i}"
                add(
                    packet_id=fallback_id,
                    kind="semantic",
                    text=text,
                    evidence_ids=(fallback_id,),
                    importance=0.46,
                    confidence=0.55,
                    metadata={"source": "runtime_flattened_memory_fallback"},
                )

        return tuple(packets[: max(0, limit)])


    def _append_daydream_packets_from_memory_snapshot(
        self,
        snapshot: dict[str, object],
        add_packet,
        *,
        limit: int,
    ) -> None:
        layer_specs = (
            ("semantic_memory", "semantic", 4),
            ("repair_memory", "repair", 4),
            ("relational_memory", "relational", 4),
            ("episodic_memory", "episodic", 4),
            ("affective_residue_memory", "affective", 3),
            ("procedural_memory", "procedural", 3),
            ("memory_candidate", "unresolved", 4),
        )
        for store_name, kind, per_layer_limit in layer_specs:
            records = snapshot.get(store_name)
            if not isinstance(records, list):
                continue
            for index, record in enumerate(reversed(records[-per_layer_limit:])):
                if not isinstance(record, dict):
                    continue
                text = self._daydream_text_from_memory_record(record)
                evidence_ids = tuple(str(item) for item in (record.get("evidence_ids") or ()) if str(item).strip())
                confidence = float(record.get("confidence", 0.55) or 0.55)
                record_id = str(record.get("id") or f"{store_name}:{index}")
                add_packet(
                    packet_id=f"{store_name}:{record_id}",
                    kind=kind,
                    text=text,
                    evidence_ids=evidence_ids,
                    importance=min(1.0, confidence + 0.08),
                    confidence=confidence,
                    metadata={"source": store_name},
                )


    def _daydream_kind_from_memory_layer(self, layer: str) -> str:
        mapping = {
            "semantic": "semantic",
            "repair": "repair",
            "relational": "relational",
            "episodic": "episodic",
            "affective_residue": "affective",
            "procedural": "procedural",
        }
        return mapping.get(str(layer or "").strip().lower(), "unresolved")


    def _daydream_text_from_memory_record(self, record: dict[str, object]) -> str:
        for key in (
            "summary",
            "claim",
            "text",
            "content",
            "reason",
            "raw_text",
            "pattern",
            "question",
        ):
            value = re.sub(r"\s+", " ", str(record.get(key, "") or "")).strip()
            if value:
                return value[:1000]
        payload = {key: value for key, value in record.items() if key not in {"metadata", "evidence_ids"}}
        rendered = json.dumps(payload, ensure_ascii=False, default=str)
        return rendered[:1000]


    def _build_signal_snapshot_for_observer(
        self,
        *,
        snapshot,
        channel: str = "",
        user_text: str = "",
        vision_facts: tuple[str, ...] = (),
        started_at: float | None = None,
    ) -> SignalSnapshot:
        focus_kind = self._observer_focus_kind_from_snapshot(snapshot)
        active_window = str(getattr(snapshot, "active_window", "") or "")
        active_app = str(getattr(snapshot, "active_app", "") or active_window or "")
        browser_url = str(getattr(snapshot, "browser_url", "") or "")
        recent_events = [str(item) for item in tuple(getattr(snapshot, "recent_events", ()) or ())[:10]]
        evidence = [str(item) for item in tuple(getattr(snapshot, "evidence", ()) or ())[:8]]
        facts = [str(item) for item in tuple(vision_facts or getattr(snapshot, "vision_facts", ()) or ())[:6]]

        # OCR text ở phase này CHỈ dùng vision_facts thật. KHÔNG nhồi telemetry chung chung
        # (active window is.../focus kind is.../no input...) vào ocr_text — nếu không tutor sẽ
        # hỏi Gemini những câu vô nghĩa về app chạy ngầm (vd Radmin VPN) thay vì chờ vision thật.
        real_evidence = [item for item in evidence[:3] if not is_generic_telemetry_line(item)]
        visible_text = "\n".join(facts or real_evidence)
        if user_text and channel == "chat":
            recent_events = [*recent_events, "direct_user_chat"]
        if facts:
            recent_events = [*recent_events, "vision_fact_available"]

        return SignalSnapshot(
            timestamp=float(started_at or time.time()),
            active_window_title=active_window,
            active_app=active_app,
            focus_kind_hint=focus_kind,
            url_or_path_hint=browser_url,
            idle_seconds=float(getattr(snapshot, "idle_seconds", 0.0) or 0.0),
            recent_events=recent_events,
            ocr_text=visible_text,
            ocr_hash=stable_hash(visible_text) if visible_text else "",
            screen_hash=stable_hash("\n".join(facts)) if facts else "",
            cpu_percent=float(getattr(snapshot, "cpu_percent", 0.0) or 0.0),
            ram_percent=float(getattr(snapshot, "ram_percent", 0.0) or 0.0),
            chat_visible=bool(channel == "chat"),
            metadata={
                "channel": channel,
                "claim_style": str(getattr(snapshot, "claim_style", "") or ""),
                "focus_summary": str(getattr(snapshot, "focus_summary", "") or ""),
                "evidence": evidence,
            },
        )


    def _observer_focus_kind_from_snapshot(self, snapshot) -> str:
        raw = str(getattr(snapshot, "active_window_kind", "") or "unknown").strip().lower()
        mapping = {
            "coding": "code",
            "code": "code",
            "documentation": "docs",
            "docs": "docs",
            "communication": "chat",
            "chat": "chat",
            "focus_aid_media": "media",
            "entertainment": "media",
            "media": "media",
            "system": "system",
            "game": "game",
            "browser": "browser",
        }
        return mapping.get(raw, raw if raw in {"code", "docs", "browser", "chat", "media", "game", "system"} else "unknown")


    def _observer_percept_content(self, observer_frame: ObserverFrame | None) -> str:
        if observer_frame is None:
            return ""
        renderer = getattr(observer_frame, "to_evidence_claim", None)
        if callable(renderer):
            return str(renderer())
        return (
            f"observer focus={getattr(observer_frame, 'focus_kind', 'unknown')}; "
            f"intent={getattr(observer_frame, 'activity_intent', 'unknown')}; "
            f"sal={float(getattr(observer_frame, 'salience', 0.0) or 0.0):.2f}; "
            f"nov={float(getattr(observer_frame, 'novelty', 0.0) or 0.0):.2f}; "
            f"rep={float(getattr(observer_frame, 'repetition', 0.0) or 0.0):.2f}; "
            f"err={float(getattr(observer_frame, 'possible_error', 0.0) or 0.0):.2f}; "
            f"presence={float(getattr(observer_frame, 'user_presence', 0.0) or 0.0):.2f}; "
            f"action={getattr(observer_frame, 'recommended_action', 'observe_only')}"
        )


    def _observer_attention_tags(self, observer_frame: ObserverFrame | None, *, channel: str = "") -> tuple[str, ...]:
        if observer_frame is None:
            return ()
        tagger = getattr(observer_frame, "attention_tags", None)
        if callable(tagger):
            base = tuple(str(item) for item in tagger())
        else:
            base = (
                "background_observer",
                f"observer_focus:{getattr(observer_frame, 'focus_kind', 'unknown')}",
                f"observer_intent:{getattr(observer_frame, 'activity_intent', 'unknown')}",
            )
        return tuple(dict.fromkeys((*base, channel)))


__all__ = ["ObserverPrivateRuntimeMixin"]

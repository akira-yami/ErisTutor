# -*- coding: utf-8 -*-
"""Worker semantic event-driven cho Deep Environment.

Mục tiêu của module này là tách đường:
    native event -> semantic -> UserSituation
khỏi nhịp self-turn. Native callback chỉ đưa event đã qua privacy/coalesce vào
scheduler; daemon semantic xử lý TaskGraph/RegionMemory và cập nhật
``UserSituation`` trước. UIA/Chromium enrichment được đẩy sang pool hậu xử lý để
cross-process call không giữ critical path.

Nhờ vậy cognition không phải đợi autonomous loop ngủ vài giây mới biết một
``command_failed`` vừa xảy ra. Self-turn chỉ đọc snapshot đã được xử lý sẵn.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any, Callable, Mapping

from .desktop_semantics import DesktopSemanticRuntime, UserSituation
from .environment_event_bus import NormalizedEnvironmentEvent
from .environment_ingress_scheduler import (
    EnrichmentMode,
    EnvironmentIngressScheduler,
    EnvironmentPriority,
    ScheduledEnvironmentEvent,
)


_CHROMIUM_PROCESSES = {"chrome.exe", "msedge.exe", "brave.exe", "chromium.exe"}
_NATIVE_ENRICH_SOURCES = {"winevent", "foreground_reconcile"}


def should_emit_semantic_wake(
    *,
    now_mono: float,
    last_wake_at: float,
    duplicate_signature: bool,
    chat_mode: bool,
    min_interval_seconds: float,
) -> bool:
    """Gate nhỏ cho wake side-effect; semantic WorldState vẫn luôn được cập nhật.

    Chú thích VI: event storm UIA/foreground có thể đổi signature liên tục dù user
    đang chat. Wake autonomous lúc đó chỉ tranh worker; state mới đã nằm trong
    WorldState nên foreground chat vẫn đọc được ngay, không mất thông tin.
    """

    if duplicate_signature or chat_mode:
        return False
    gap = max(0.0, float(now_mono) - float(last_wake_at or 0.0))
    return bool(float(last_wake_at or 0.0) <= 0.0 or gap >= max(0.0, float(min_interval_seconds)))


class SemanticEnvironmentWorker:
    """Consumer duy nhất cho structured semantic path của DesktopSemanticRuntime.

    ``DesktopSemanticRuntime`` và ``TaskGraph`` đều có state mutable. Worker giữ
    một lock chung khi mutate chúng để self-turn chỉ được phép gọi fallback OCR/
    screen qua ``build_perception_situation``. Enrichment async chỉ publish event
    mới quay lại scheduler, tuyệt đối không sửa TaskGraph trực tiếp từ thread pool.
    """

    def __init__(
        self,
        *,
        host: Any,
        runtime: DesktopSemanticRuntime,
        publish_event: Callable[[NormalizedEnvironmentEvent], NormalizedEnvironmentEvent | None],
        uia_adapter: Any = None,
        chromium_adapter: Any = None,
        max_queue: int = 1024,
        batch_window_ms: float = 8.0,
        chromium_refresh_min_ms: float = 120.0,
    ) -> None:
        self.host = host
        self.runtime = runtime
        self._publish_event = publish_event
        self._uia_adapter = uia_adapter
        self._chromium_adapter = chromium_adapter
        # V5: priority heap + coalescing index thay cho FIFO Queue. Event storm
        # background không còn đứng trước foreground/error chỉ vì đến sớm hơn.
        self._scheduler = EnvironmentIngressScheduler(max_pending=max_queue)
        self._batch_window_seconds = max(0.0, float(batch_window_ms)) / 1000.0
        self._chromium_refresh_min_ns = int(max(20.0, float(chromium_refresh_min_ms)) * 1_000_000.0)
        self._last_chromium_refresh_ns_by_hwnd: dict[int, int] = {}

        # Enrichment có thể chạm COM/cross-process nên không được chặn semantic
        # critical path. Pool nhỏ, bounded theo policy scheduler; Chromium còn có
        # coalescing riêng theo HWND khi một refresh đang chạy.
        self._enrichment_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="eris-env-enrich")
        self._enrichment_lock = threading.RLock()
        self._chromium_refresh_inflight = False
        self._pending_chromium_triggers: dict[int, ScheduledEnvironmentEvent] = {}

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._runtime_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._latest_telemetry: dict[str, Any] = {}
        self._latest_situation: UserSituation | None = None
        self._processed: deque[tuple[int, NormalizedEnvironmentEvent]] = deque(maxlen=512)
        self._revision = 0
        self._last_perception_revision = 0
        # V61: enrichment có thể phát nhiều event cùng nghĩa trong vài trăm ms.
        # Scheduler coalesce theo HWND/object vẫn chưa đủ khi cùng semantic change
        # đi qua nhiều nguồn. Giữ một wake signature ngắn để không đánh thức
        # autonomous loop 4-5 lần cho cùng một thay đổi.
        self._last_semantic_wake_signature = ""
        self._last_semantic_wake_at = 0.0
        # Chú thích VI: wake chỉ là side-effect để đánh thức cognition, không phải
        # đường truyền dữ liệu. Debounce 1.2s giảm event storm mà không làm cũ WorldState.
        self._semantic_wake_min_interval_seconds = 1.2

    @staticmethod
    def _semantic_wake_signature(situation: UserSituation) -> str:
        """Identity của semantic state dùng để edge-trigger autonomous wake.

        Cố ý không chứa event kind, event id, timestamp hay evidence ref. Các field
        đó là provenance/telemetry và có thể đổi khi *meaning* vẫn y nguyên.
        """
        return "|".join((
            str(getattr(situation, "activity", "") or "")[:80],
            str(getattr(situation, "task_hint", "") or "")[:120],
            str(getattr(situation, "phase", "") or "")[:80],
            str(getattr(situation, "focused_app", "") or "")[:80],
            str(getattr(situation, "focused_process", "") or "")[:80],
            str(getattr(situation, "focused_object", "") or "")[:180],
            str(getattr(situation, "last_action", "") or "")[:100],
            str(getattr(situation, "last_outcome", "") or "")[:100],
            str(getattr(situation, "attention_state", "") or "")[:60],
            str(int(getattr(situation, "max_attention_level", 0) or 0)),
        ))

    def start(self) -> "SemanticEnvironmentWorker":
        """Khởi động daemon thread nếu chưa chạy."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="eris-semantic-environment",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self, timeout: float = 1.5) -> None:
        """Dừng worker có giới hạn thời gian; shutdown không được treo theo sensor."""
        self._stop_event.set()
        self._scheduler.close()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        try:
            self._enrichment_pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    def set_uia_adapter(self, adapter: Any) -> None:
        self._uia_adapter = adapter

    def set_chromium_adapter(self, adapter: Any) -> None:
        self._chromium_adapter = adapter

    @property
    def latest_situation(self) -> UserSituation | None:
        with self._state_lock:
            return self._latest_situation

    @property
    def revision(self) -> int:
        with self._state_lock:
            return self._revision

    @property
    def pending_attention_level(self) -> int:
        """Attention structured chưa được self-turn consume, dùng để bypass cooldown xã hội."""
        with self._state_lock:
            if self._revision <= self._last_perception_revision or self._latest_situation is None:
                return 0
            return max(0, min(4, int(self._latest_situation.max_attention_level or 0)))

    def update_telemetry(self, telemetry: Mapping[str, Any] | None) -> None:
        """Giữ snapshot telemetry nhỏ, không mang raw page/clipboard content vào worker."""
        if not isinstance(telemetry, Mapping):
            return
        allow = (
            "active_process",
            "active_app",
            "active_window_title",
            "window_class",
            "foreground_pid",
            "foreground_hwnd",
            "idle_seconds",
            "interaction_state",
            "active_window_kind",
            "active_file",
            "selected_file",
        )
        compact = {key: telemetry.get(key) for key in allow if key in telemetry}
        with self._state_lock:
            self._latest_telemetry = compact

    def submit(
        self,
        event: NormalizedEnvironmentEvent,
        *,
        ingress_monotonic_ns: int | None = None,
        ingress_wall_time: float | None = None,
    ) -> bool:
        """Đưa event vào ingress scheduler mà không block native callback.

        V5 không còn FIFO "đến trước xử lý trước". Scheduler tự quyết định
        priority/coalescing/backpressure theo policy tập trung, nên foreground hay
        command_failed không bị kẹt sau một cơn bão structure_reordered.
        """
        ingress_ns = int(ingress_monotonic_ns or time.monotonic_ns())
        ingress_wall = float(time.time() if ingress_wall_time is None else ingress_wall_time)
        result = self._scheduler.submit(
            event,
            ingress_monotonic_ns=ingress_ns,
            ingress_wall_time=ingress_wall,
        )

        observability = getattr(self.host, "observability", None)
        if observability is not None and result.item is not None:
            try:
                callback_to_enqueue_ms = max(
                    0.0,
                    (result.item.enqueue_monotonic_ns - ingress_ns) / 1_000_000.0,
                )
                tags = {
                    "source": event.source or "unknown",
                    "event_type": event.event_type or "unknown",
                    "priority": result.item.policy.priority.name.lower(),
                }
                observability.observe("environment_callback_to_enqueue_ms", callback_to_enqueue_ms, tags=tags)
                observability.observe("environment_semantic_queue_depth", float(result.queue_depth), tags={"priority": tags["priority"]})
            except Exception:
                pass

        if result.coalesced:
            self._metric_increment("environment_scheduler_coalesced_total")
        if result.evicted:
            self._metric_increment("environment_scheduler_evicted_total")
        if result.dropped:
            self._metric_increment("environment_scheduler_drop_total")
        return bool(result.accepted)

    def wait_until_revision(self, revision: int, timeout: float = 1.0) -> bool:
        """Helper deterministic cho test/probe, không dùng polling nóng trong production."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        target = max(0, int(revision))
        while time.monotonic() < deadline:
            if self.revision >= target:
                return True
            time.sleep(0.005)
        return self.revision >= target

    def build_perception_situation(
        self,
        screen_context: Any,
        *,
        telemetry: Mapping[str, Any] | None,
        observer_frame: Any = None,
        now: float | None = None,
    ) -> tuple[tuple[NormalizedEnvironmentEvent, ...], UserSituation]:
        """Ghép fallback screen/OCR với structured snapshot đã xử lý trước đó.

        Structured event chỉ mang ``max_attention_level`` sang self-turn đúng một
        lần, tại perception đầu tiên sau khi worker xử lý event. Nếu không có event
        mới, task/busy/interruption vẫn được giữ nhưng attention cũ không được đánh
        thức cognition lần thứ hai.
        """
        now = time.time() if now is None else float(now)
        self.update_telemetry(telemetry)

        with self._state_lock:
            structured = self._latest_situation
            new_events = tuple(
                event for revision, event in self._processed
                if revision > self._last_perception_revision
            )
            if self._revision > self._last_perception_revision:
                self._last_perception_revision = self._revision

        # observe(screen) vẫn là fallback hợp lệ, nhưng phải đi qua cùng runtime
        # lock để không race với observe_events() của worker.
        with self._runtime_lock:
            base = self.runtime.observe(
                screen_context,
                telemetry=telemetry,
                observer_frame=observer_frame,
                now=now,
            )

        return new_events[-128:], self._merge_situations(
            base=base,
            structured=structured,
            has_new_structured=bool(new_events),
            now=now,
        )

    @staticmethod
    def _merge_situations(
        *,
        base: UserSituation,
        structured: UserSituation | None,
        has_new_structured: bool,
        now: float,
    ) -> UserSituation:
        if structured is None:
            return base

        def prefer_structured(value: str, fallback: str) -> str:
            text = str(value or "").strip()
            return text if text and text != "unknown" else fallback

        # TaskGraph/structured source quyết định task/phase. Focus hiện tại từ
        # telemetry/screen có độ mới tốt hơn nên base được ưu tiên ở focused_*.
        max_attention = max(
            int(base.max_attention_level or 0),
            int(structured.max_attention_level or 0) if has_new_structured else 0,
        )
        semantic_delta = bool(base.semantic_delta or (has_new_structured and structured.semantic_delta))
        wake_recommended = bool(base.wake_recommended or (has_new_structured and structured.wake_recommended))
        recent = structured.recent_events or base.recent_events
        refs = tuple(dict.fromkeys((*base.evidence_refs, *structured.evidence_refs)))[:12]

        return UserSituation(
            timestamp=now,
            activity=prefer_structured(structured.activity, base.activity),
            task_hint=prefer_structured(structured.task_hint, base.task_hint),
            phase=prefer_structured(structured.phase, base.phase),
            focused_app=base.focused_app or structured.focused_app,
            focused_process=base.focused_process or structured.focused_process,
            focused_object=base.focused_object or structured.focused_object,
            last_action=(structured.last_action if has_new_structured else base.last_action) or base.last_action,
            last_outcome=prefer_structured(structured.last_outcome, base.last_outcome),
            attention_state=prefer_structured(structured.attention_state, base.attention_state),
            interruption_cost=max(float(base.interruption_cost or 0.0), float(structured.interruption_cost or 0.0)),
            busy_score=max(float(base.busy_score or 0.0), float(structured.busy_score or 0.0)),
            confidence=max(float(base.confidence or 0.0), float(structured.confidence or 0.0)),
            semantic_delta=semantic_delta,
            wake_recommended=wake_recommended,
            max_attention_level=max(0, min(4, max_attention)),
            recent_events=recent,
            evidence_refs=refs,
        )

    def _run(self) -> None:
        """Main loop của semantic critical path.

        Critical event được xử lý ngay. NORMAL/BACKGROUND chỉ gom những item đã
        sẵn có trong một cửa sổ rất ngắn để giảm overhead; worker không ngủ để
        "đợi đủ batch". Enrichment chạy sau semantic và ở pool riêng.
        """
        while not self._stop_event.is_set():
            first = self._scheduler.get(timeout=0.20)
            if first is None:
                continue

            batch = [first]
            if first.policy.priority == EnvironmentPriority.CRITICAL:
                max_items = 16
                ceiling = EnvironmentPriority.CRITICAL
                batch_deadline = time.monotonic()  # không chờ thêm cho critical
            elif first.policy.priority == EnvironmentPriority.NORMAL:
                max_items = 32
                ceiling = EnvironmentPriority.NORMAL
                batch_deadline = time.monotonic() + min(self._batch_window_seconds, 0.004)
            else:
                max_items = 64
                ceiling = EnvironmentPriority.BACKGROUND
                batch_deadline = time.monotonic() + self._batch_window_seconds

            while len(batch) < max_items:
                queued = self._scheduler.get_nowait(max_priority=ceiling)
                if queued is None:
                    break
                batch.append(queued)
                if time.monotonic() >= batch_deadline:
                    break

            try:
                self._process_batch(batch)
            except Exception as exc:  # noqa: BLE001
                logger = getattr(self.host, "logger", None)
                if logger is not None:
                    try:
                        logger.warning("[deep environment] semantic worker batch lỗi: %s", exc)
                    except Exception:
                        pass
                self._metric_increment("environment_semantic_worker_error_total")

    def _process_batch(self, batch: list[ScheduledEnvironmentEvent]) -> None:
        if not batch:
            return

        # V5 quan trọng: semantic baseline được tính TRƯỚC enrichment. Một WinEvent
        # foreground/focus hợp lệ phải cập nhật UserSituation ngay; UIA/IA2 có chậm
        # thì chỉ làm giàu fact sau đó, không giữ critical path làm con tin.
        processing_started_ns = time.monotonic_ns()
        events = tuple(item.event for item in batch)
        telemetry = self._telemetry_for_events(events)
        semantic_wall_started = time.time()
        semantic_started_ns = time.monotonic_ns()
        with self._runtime_lock:
            situation = self.runtime.observe_events(
                events,
                telemetry=telemetry,
                base_situation=None,
                now=semantic_wall_started,
            )
        semantic_done_ns = time.monotonic_ns()
        semantic_wall_done = time.time()

        self._record_semantic_metrics(
            batch,
            situation,
            semantic_done_ns,
            semantic_wall_done,
            processing_started_ns=processing_started_ns,
            semantic_started_ns=semantic_started_ns,
        )

        with self._state_lock:
            self._latest_situation = situation
            for item in batch:
                self._revision += 1
                self._processed.append((self._revision, item.event))

        # Unified Environment Awareness: publish ngay trên semantic critical path
        # sau khi UserSituation đã commit. Chat/self-turn/seed đọc cùng projection
        # này; không lane nào phải tự dựng một phiên bản môi trường khác. VLM vẫn
        # nằm ngoài hub và không bị gọi ở đây.
        try:
            from runtime.environment_awareness import ensure_environment_awareness_hub
            ensure_environment_awareness_hub(self.host).refresh(
                machine_snapshot=telemetry,
                situation=situation,
                environment_events=tuple(getattr(situation, "recent_events", ()) or ()),
                now=semantic_wall_done,
                source="deep_environment_event",
            )
        except Exception as exc:  # noqa: BLE001
            self._debug("publish environment awareness lỗi: %s", exc)

        # AttentionRouter is the one salience policy.  The semantic worker only
        # performs a non-public wake when WorldState says ``wake_cognition``; it
        # never decides to speak on its own.
        attention_actions: tuple[str, ...] = ()
        try:
            from runtime.world_state import ensure_world_state_service
            _world = ensure_world_state_service(self.host).snapshot(now=semantic_wall_done)
            attention_actions = tuple(str(item) for item in tuple(_world.attention.get("actions", ()) or ()))
        except Exception:
            attention_actions = ()
        if "wake_cognition" in attention_actions:
            wake = getattr(self.host, "_wake_autonomous_loop", None)
            if callable(wake):
                kinds = ",".join(dict.fromkeys(event.event_type for event in events))[:120]
                now_mono = time.monotonic()
                wake_signature = self._semantic_wake_signature(situation)
                duplicate_wake = wake_signature == self._last_semantic_wake_signature
                emit_wake = should_emit_semantic_wake(
                    now_mono=now_mono,
                    last_wake_at=self._last_semantic_wake_at,
                    duplicate_signature=duplicate_wake,
                    chat_mode=bool(getattr(self.host, "chat_mode", False)),
                    min_interval_seconds=self._semantic_wake_min_interval_seconds,
                )
                if emit_wake:
                    self._last_semantic_wake_signature = wake_signature
                    self._last_semantic_wake_at = now_mono
                    try:
                        wake(f"environment_semantic:{kinds or 'event'}")
                    except Exception as exc:  # noqa: BLE001
                        self._debug("không đánh thức được autonomous loop: %s", exc)
                else:
                    self._metric_increment("environment_semantic_wake_coalesced_total")

        # Enrichment là hậu xử lý bất đồng bộ. Event sinh ra từ UIA/IA2 sẽ quay
        # lại publish -> scheduler như mọi event khác, nên privacy/coalesce vẫn là
        # một đường duy nhất thay vì có lối tắt bí mật.
        self._schedule_post_semantic_enrichment(batch)

    def _schedule_post_semantic_enrichment(self, batch: list[ScheduledEnvironmentEvent]) -> None:
        if self._stop_event.is_set():
            return

        for queued in batch:
            event = queued.event
            if (
                self._uia_adapter is not None
                and event.source in _NATIVE_ENRICH_SOURCES
                and queued.policy.enrichment != EnrichmentMode.NONE
            ):
                try:
                    self._enrichment_pool.submit(self._run_uia_enrichment, queued)
                except RuntimeError:
                    pass

        chromium_triggers = [
            item for item in batch
            if item.policy.chromium_refresh
            and str(item.event.process_name or "").lower() in _CHROMIUM_PROCESSES
            and item.event.source in _NATIVE_ENRICH_SOURCES
            and int(item.event.hwnd or 0)
        ]
        if self._chromium_adapter is not None and chromium_triggers:
            # Giữ latest trigger theo HWND; selection/foreground critical sẽ thắng
            # background name_changed nhờ priority scheduler.
            for trigger in chromium_triggers:
                self._schedule_chromium_refresh(trigger)

    def _run_uia_enrichment(self, queued: ScheduledEnvironmentEvent) -> None:
        if self._stop_event.is_set() or self._uia_adapter is None:
            return
        started_ns = time.monotonic_ns()
        try:
            enriched = self._uia_adapter.enrich(queued.event)
            if enriched is not None:
                self._publish_event(enriched)
        except Exception as exc:  # noqa: BLE001
            self._debug("UIA enrichment lỗi: %s", exc)
        finally:
            self._observe_enrichment_metric(
                "uia",
                queued.event,
                max(0.0, (time.monotonic_ns() - started_ns) / 1_000_000.0),
            )

    def _schedule_chromium_refresh(self, trigger: ScheduledEnvironmentEvent) -> None:
        hwnd = int(trigger.event.hwnd or 0)
        if not hwnd:
            return
        with self._enrichment_lock:
            previous = self._pending_chromium_triggers.get(hwnd)
            should_replace = previous is None
            if previous is not None:
                if int(trigger.policy.priority) < int(previous.policy.priority):
                    should_replace = True
                elif (
                    int(trigger.policy.priority) == int(previous.policy.priority)
                    and float(trigger.event.timestamp or 0.0) >= float(previous.event.timestamp or 0.0)
                ):
                    should_replace = True
            if should_replace:
                self._pending_chromium_triggers[hwnd] = trigger
            if self._chromium_refresh_inflight:
                return
            self._chromium_refresh_inflight = True
        try:
            self._enrichment_pool.submit(self._run_chromium_refresh_loop)
        except RuntimeError:
            with self._enrichment_lock:
                self._chromium_refresh_inflight = False

    def _run_chromium_refresh_loop(self) -> None:
        """Refresh Chromium theo HWND, không EnumWindows/poll_all cho mỗi trigger."""
        try:
            while not self._stop_event.is_set():
                with self._enrichment_lock:
                    if not self._pending_chromium_triggers:
                        self._chromium_refresh_inflight = False
                        return
                    # Critical trước; trong cùng priority lấy trigger mới nhất.
                    hwnd, trigger = min(
                        self._pending_chromium_triggers.items(),
                        key=lambda pair: (int(pair[1].policy.priority), -float(pair[1].event.timestamp or 0.0)),
                    )
                    self._pending_chromium_triggers.pop(hwnd, None)

                now_ns = time.monotonic_ns()
                last_ns = self._last_chromium_refresh_ns_by_hwnd.get(hwnd, 0)
                if (
                    trigger.policy.priority != EnvironmentPriority.CRITICAL
                    and now_ns - last_ns < self._chromium_refresh_min_ns
                ):
                    continue
                self._last_chromium_refresh_ns_by_hwnd[hwnd] = now_ns

                started_ns = time.monotonic_ns()
                try:
                    poll_window = getattr(self._chromium_adapter, "poll_window", None)
                    if callable(poll_window):
                        chromium_events = tuple(
                            poll_window(
                                hwnd,
                                force=True,
                                now=float(trigger.event.timestamp or time.time()),
                            ) or ()
                        )
                    else:
                        # Backward compatibility cho adapter test-double cũ.
                        chromium_events = tuple(self._chromium_adapter.poll_all() or ())

                    for chromium_event in chromium_events:
                        metadata = dict(chromium_event.metadata or {})
                        metadata.setdefault("trigger_source", trigger.event.source)
                        metadata.setdefault("trigger_event_type", trigger.event.event_type)
                        triggered_event = replace(
                            chromium_event,
                            timestamp=min(
                                float(chromium_event.timestamp),
                                float(trigger.event.timestamp or chromium_event.timestamp),
                            ),
                            metadata=metadata,
                            event_id="",
                        )
                        self._publish_event(triggered_event)
                except Exception as exc:  # noqa: BLE001
                    self._debug("Chromium event-triggered refresh lỗi: %s", exc)
                finally:
                    self._observe_enrichment_metric(
                        "chromium",
                        trigger.event,
                        max(0.0, (time.monotonic_ns() - started_ns) / 1_000_000.0),
                    )
        finally:
            with self._enrichment_lock:
                self._chromium_refresh_inflight = False

    def _observe_enrichment_metric(self, provider: str, event: NormalizedEnvironmentEvent, value_ms: float) -> None:
        observability = getattr(self.host, "observability", None)
        if observability is None:
            return
        try:
            observability.observe(
                "environment_enrichment_ms",
                float(value_ms),
                tags={
                    "provider": str(provider or "unknown"),
                    "source": event.source or "unknown",
                    "event_type": event.event_type or "unknown",
                },
            )
        except Exception:
            pass

    def _telemetry_for_events(self, events: tuple[NormalizedEnvironmentEvent, ...]) -> dict[str, Any]:
        with self._state_lock:
            telemetry = dict(self._latest_telemetry)

        # Nếu event đến trước perception đầu tiên, tự dựng identity tối thiểu từ
        # structured source để UserSituation không rơi về focused_process rỗng.
        for event in reversed(events):
            if event.process_name:
                telemetry.setdefault("active_process", event.process_name)
                telemetry.setdefault("active_app", event.process_name)
            if event.pid:
                telemetry.setdefault("foreground_pid", event.pid)
            if event.hwnd:
                telemetry.setdefault("foreground_hwnd", event.hwnd)
            if event.window_class:
                telemetry.setdefault("window_class", event.window_class)
            if event.title:
                telemetry.setdefault("active_window_title", event.title)
            if event.event_type == "foreground_changed":
                telemetry["active_process"] = event.process_name or telemetry.get("active_process", "")
                telemetry["active_app"] = event.process_name or telemetry.get("active_app", "")
                telemetry["foreground_pid"] = event.pid or telemetry.get("foreground_pid", 0)
                telemetry["foreground_hwnd"] = event.hwnd or telemetry.get("foreground_hwnd", 0)
                telemetry["active_window_title"] = event.title or telemetry.get("active_window_title", "")
                break
        telemetry.setdefault("idle_seconds", 0.0)
        return telemetry

    def _record_semantic_metrics(
        self,
        batch: list[ScheduledEnvironmentEvent],
        situation: UserSituation,
        semantic_done_ns: int,
        semantic_wall_done: float,
        *,
        processing_started_ns: int,
        semantic_started_ns: int,
    ) -> None:
        observability = getattr(self.host, "observability", None)
        if observability is None:
            return
        try:
            mechanical = all(item.event.attention_level == 0 or not item.event.semantic for item in batch)
            kinds = tuple(str(item.event.event_type or "unknown") for item in batch)
            for item in batch:
                event = item.event
                provider = str((event.metadata or {}).get("provider", "") or event.source or "unknown")[:40]
                ingress_to_semantic_ms = max(0.0, (semantic_done_ns - item.ingress_monotonic_ns) / 1_000_000.0)
                source_to_semantic_ms = max(0.0, (semantic_wall_done - float(event.timestamp or semantic_wall_done)) * 1000.0)
                queue_wait_ms = max(0.0, (processing_started_ns - item.enqueue_monotonic_ns) / 1_000_000.0)
                semantic_processing_ms = max(0.0, (semantic_done_ns - semantic_started_ns) / 1_000_000.0)
                tags = {
                    "source": event.source or "unknown",
                    "event_type": event.event_type or "unknown",
                    "provider": provider,
                    "priority": item.policy.priority.name.lower(),
                }
                observability.observe("environment_semantic_queue_wait_ms", queue_wait_ms, tags=tags)
                observability.observe("environment_semantic_processing_ms", semantic_processing_ms, tags=tags)
                observability.observe("environment_ingress_to_semantic_ms", ingress_to_semantic_ms, tags=tags)
                observability.observe("environment_source_to_semantic_ms", source_to_semantic_ms, tags=tags)
                # Giữ tên metric cũ để dashboard/probe hiện tại không gãy.
                # giá trị của nó là source -> semantic thật, không còn phụ thuộc self-turn tick.
                observability.observe("environment_event_to_semantic_ms", source_to_semantic_ms, tags=tags)
                if event.event_type == "foreground_changed":
                    observability.observe("foreground_to_user_situation_ms", source_to_semantic_ms, tags=tags)

            wake_from_environment = bool(situation.semantic_delta and situation.max_attention_level >= 2)
            if mechanical and wake_from_environment:
                observability.increment("environment_false_wake_mechanical_total")
            observability.increment(
                "user_situation_from_environment_total",
                tags={
                    "phase": situation.phase or "unknown",
                    "attention_state": situation.attention_state or "unknown",
                    "max_attention": str(max(0, min(4, int(situation.max_attention_level or 0)))),
                    "semantic_delta": str(bool(situation.semantic_delta)).lower(),
                    "wake": str(wake_from_environment).lower(),
                },
                fields={
                    "event_count": len(batch),
                    "event_types": list(dict.fromkeys(kinds))[:12],
                    "task_hint": situation.task_hint,
                    "busy_score": round(float(situation.busy_score or 0.0), 4),
                    "interruption_cost": round(float(situation.interruption_cost or 0.0), 4),
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._debug("ghi observability semantic lỗi: %s", exc)

    def _metric_increment(self, name: str) -> None:
        observability = getattr(self.host, "observability", None)
        if observability is None:
            return
        try:
            observability.increment(name)
        except Exception:
            pass

    def _debug(self, message: str, *args: Any) -> None:
        logger = getattr(self.host, "logger", None)
        if logger is None:
            return
        try:
            logger.debug("[deep environment] " + message, *args)
        except Exception:
            pass


__all__ = ["SemanticEnvironmentWorker"]

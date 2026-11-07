# -*- coding: utf-8 -*-
"""tts_task_manager.py — quản lý thứ tự, prefetch, cancel và cache TTS.

Backend TTS chỉ nên biết cách sinh/phát audio.  Manager này nhận các
``StageFrame`` đã có ``turn_id``/``segment_id`` rồi:

- bỏ segment rỗng;
- sinh audio song song để giảm khoảng chờ giữa câu;
- phát lại theo đúng thứ tự segment;
- hủy phần đang phát và phần pending khi user chen vào;
- gửi emotion/motion event trước khi phát audio;
- cache WAV ngắn hạn để các segment giống nhau không gọi TTS lại.
"""

from __future__ import annotations
from core.di import resolve_dependency

import hashlib
import inspect
import logging
import os
import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from runtime.audio_lip_sync import build_lip_sync_envelope
from runtime.stage_audio_bus import StageAudioBus
from runtime.stage_continuation import StageContinuationRuntime
from runtime.speech_localizer import SpeechLocalizationError

logger = logging.getLogger("TTS.TaskManager")


@dataclass(frozen=True, slots=True)
class TtsSegmentTask:
    frame: Any
    text: str
    order: int
    valence: float
    arousal: float
    stance: str
    speech_epoch: int = 0

    @property
    def key(self) -> tuple[str, int]:
        return (str(getattr(self.frame, "turn_id", "") or ""), int(self.order))


@dataclass(frozen=True, slots=True)
class TtsDispatchPolicy:
    """Quyết định một lượt nên prefetch hay gộp inline.

    Remote TTS không miễn phí: câu quá dài hoặc quá nhiều segment mà bắn song song
    sẽ dễ ăn rate-limit.  Policy này giữ nhịp nhanh cho lượt ngắn, nhưng nhường
    StageDispatcher fallback inline khi lượt quá dài.
    """

    strategy: str
    workers: int
    reason: str = ""


class TtsTaskManager:
    """Queue cấp lượt nói, tách khỏi engine Gemini cụ thể."""

    def __init__(
        self,
        *,
        audio_bus: StageAudioBus | None = None,
        prefetch_workers: int | None = None,
        max_prefetch_segments: int | None = None,
    ) -> None:
        self.audio_bus = resolve_dependency(audio_bus, lambda: StageAudioBus())
        self.prefetch_workers = None if prefetch_workers is None else max(1, min(6, int(prefetch_workers)))
        self.max_prefetch_segments = (
            None if max_prefetch_segments is None else max(1, min(12, int(max_prefetch_segments)))
        )
        self._lock = threading.RLock()
        self._generation = 0
        self._cancel_event = threading.Event()
        self._playback_stop = threading.Event()
        self._executor: ThreadPoolExecutor | None = None
        self._playback_thread: threading.Thread | None = None
        self._current_task: TtsSegmentTask | None = None
        self._current_cancel: Callable[[str], None] | None = None
        self._pending_tasks: dict[tuple[str, int], TtsSegmentTask] = {}
        self._pending_callbacks: dict[tuple[str, int], Callable[[Any, Any], None] | None] = {}
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        # Sở hữu khoảng lặng giữa segment; cancel chung với queue audio.
        self.continuation = StageContinuationRuntime()

    def dispatch_frames(
        self,
        frames: Iterable[Any],
        *,
        backend: Any,
        valence: float,
        arousal: float,
        stance: str,
        on_first_start: Callable[[], None] | None = None,
        on_event: Callable[[Any, Any], None] | None = None,
        speech_epoch: int = 0,
    ) -> bool:
        """Nhận một lượt stage và bắt đầu synth/playback không blocking."""

        synthesize = getattr(backend, "synthesize", None)
        play_audio = getattr(backend, "play_audio", None)
        if not callable(synthesize) or not callable(play_audio):
            return False

        tasks = self._build_tasks(
            frames, valence=valence, arousal=arousal, stance=stance,
            speech_epoch=speech_epoch,
        )
        if not tasks:
            return False
        policy = _dispatch_policy(
            tasks,
            prefetch_workers=self.prefetch_workers,
            max_prefetch_segments=self.max_prefetch_segments,
        )
        if policy.strategy == "inline":
            logger.debug("[tts-task-manager] fallback inline: %s", policy.reason)
            return False

        self.cancel_current(reason="replaced", only_interruptible=False)
        generation, cancel_event = self.continuation.begin_generation()
        playback_stop = threading.Event()
        executor = ThreadPoolExecutor(max_workers=policy.workers, thread_name_prefix="TTS-Prefetch")
        turn_segments = _turn_segments(tasks)

        with self._lock:
            self._generation = generation
            self._cancel_event = cancel_event
            self._playback_stop = playback_stop
            self._executor = executor
            self._current_task = None
            self._current_cancel = None
            self._pending_tasks = {task.key: task for task in tasks}
            self._pending_callbacks = {task.key: on_event for task in tasks}

        futures: dict[tuple[str, int], Future[bytes]] = {}
        for task in tasks:
            self.audio_bus.emit_lifecycle(
                "audio_prepare",
                stage_frame=task.frame,
                text=task.text,
                turn_segments=turn_segments,
                callback=on_event,
            )
            self.audio_bus.emit_lifecycle(
                "audio_queued",
                stage_frame=task.frame,
                text=task.text,
                turn_segments=turn_segments,
                callback=on_event,
            )
            futures[task.key] = executor.submit(
                self._synthesize_task,
                task,
                backend=backend,
                cancel_event=cancel_event,
            )
            futures[task.key].add_done_callback(
                self._buffer_ready_callback(
                    task,
                    cancel_event=cancel_event,
                    turn_segments=turn_segments,
                    on_event=on_event,
                )
            )

        thread = threading.Thread(
            target=self._playback_loop,
            name="TTS-TaskPlayback",
            daemon=True,
            args=(
                generation,
                tasks,
                futures,
                turn_segments,
                backend,
                executor,
                cancel_event,
                playback_stop,
                on_first_start,
                on_event,
            ),
        )
        with self._lock:
            self._playback_thread = thread
        thread.start()
        return True

    def cancel_current(self, *, reason: str = "cancelled", only_interruptible: bool = True) -> bool:
        """Dừng audio hiện tại và đánh dấu các segment pending là hủy."""

        with self._lock:
            current = self._current_task
            if current is not None and only_interruptible and not bool(getattr(current.frame, "interruptible", True)):
                return False
            pending = list(self._pending_tasks.values())
            callbacks = dict(self._pending_callbacks)
            cancel_event = self._cancel_event
            playback_stop = self._playback_stop
            executor = self._executor
            self._pending_tasks = {}
            self._pending_callbacks = {}
            cancel_event.set()
            playback_stop.set()
            if self._current_cancel is not None:
                self._current_cancel(reason)

        self.continuation.cancel(reason=reason)
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)

        emitted = False
        for task in pending:
            callback = callbacks.get(task.key)
            self.audio_bus.emit_lifecycle(
                "audio_cancelled",
                stage_frame=task.frame,
                text=task.text,
                reason=reason,
                callback=callback,
            )
            emitted = True
        return emitted or current is not None

    def shutdown(self) -> None:
        self.cancel_current(reason="shutdown", only_interruptible=False)

    def _build_tasks(
        self,
        frames: Iterable[Any],
        *,
        valence: float,
        arousal: float,
        stance: str,
        speech_epoch: int = 0,
    ) -> list[TtsSegmentTask]:
        tasks: list[TtsSegmentTask] = []
        for index, frame in enumerate(frames):
            text = str(getattr(frame, "tts_text", "") or "").strip()
            if not text:
                continue
            try:
                order = int(getattr(frame, "segment_id", index) or index)
            except (TypeError, ValueError):
                order = index
            tasks.append(
                TtsSegmentTask(
                    frame=frame,
                    text=text,
                    order=order,
                    valence=float(valence),
                    arousal=float(arousal),
                    stance=str(stance or ""),
                    speech_epoch=int(speech_epoch or 0),
                )
            )
        return sorted(tasks, key=lambda task: task.order)

    def _synthesize_task(self, task: TtsSegmentTask, *, backend: Any, cancel_event: threading.Event) -> bytes:
        if cancel_event.is_set():
            raise RuntimeError("cancelled_before_synth")

        # Bản đã verify PHẢI được so hash trước cache và trước cả preset WAV.
        # Preset WAV hiện không có chứng chỉ transcript, nên verified V12 không
        # thể dùng một audio asset cũ để thay câu đã kiểm tra.
        expected = str(getattr(task.frame, 'speech_contract', {}).get('verified_transcript_hash', '') or '')
        if expected:
            from runtime.tts_engine import _clean_text
            if hashlib.sha256(_clean_text(task.text).encode('utf-8')).hexdigest() != expected:
                raise RuntimeError('v03_tts_cached_transcript_mismatch')
            if str(getattr(task.frame, 'audio_asset', '') or ''):
                raise RuntimeError('v03_unverified_audio_preset')
        else:
            # Legacy vẫn giữ cơ chế preset cũ.
            preset_audio = _read_audio_asset(task.frame)
            if preset_audio:
                return preset_audio

        cache_key = self._cache_key(task, backend=backend)
        cached = self._cache_get(cache_key)
        if cached:
            return cached
        wav_bytes = backend.synthesize(
            task.text,
            valence=task.valence,
            arousal=task.arousal,
            stance=task.stance,
            prosody_style=str(getattr(task.frame, "prosody_style", "") or ""),
            prosody_notes=str(getattr(task.frame, "prosody_notes", "") or ""),
            turn_id=str(getattr(task.frame, "turn_id", "") or ""),
            segment_id=int(task.order),
            interruptible=bool(getattr(task.frame, "interruptible", True)),
            cancel_event=cancel_event,
            speech_epoch=int(task.speech_epoch or 0),
            **_performance_kwargs(task.frame),
            **_speech_kwargs(task.frame),
        )
        if cancel_event.is_set():
            raise RuntimeError("cancelled_after_synth")
        self._cache_put(cache_key, wav_bytes)
        return wav_bytes

    def _playback_loop(
        self,
        generation: int,
        tasks: list[TtsSegmentTask],
        futures: dict[tuple[str, int], Future[bytes]],
        turn_segments: tuple[dict[str, object], ...],
        backend: Any,
        executor: ThreadPoolExecutor,
        cancel_event: threading.Event,
        playback_stop: threading.Event,
        on_first_start: Callable[[], None] | None,
        on_event: Callable[[Any, Any], None] | None,
    ) -> None:
        first_started = False
        localization_failure_seen = False
        emotion_signature = ""
        finish_audio = None
        try:
            # Không phát segment 0 ngay khi nó vừa xong nếu segment 1/2 vẫn còn
            # đang dịch/synth. Đây chính là pattern tạo khoảng chết trong log.
            self._prebuffer_window(tasks, futures, cancel_event=cancel_event)
            for task in tasks:
                if cancel_event.is_set():
                    break
                if not _speech_task_is_current(backend, task):
                    self.audio_bus.emit_lifecycle(
                        "audio_cancelled", stage_frame=task.frame, text=task.text,
                        turn_segments=turn_segments, reason="stale_turn_before_wait",
                        callback=on_event,
                    )
                    break

                try:
                    wav_bytes = self._wait_future(futures[task.key], cancel_event=cancel_event)
                except Exception as exc:
                    if cancel_event.is_set():
                        break
                    if not _speech_task_is_current(backend, task) or str(exc) == "tts_cancelled":
                        self.audio_bus.emit_lifecycle(
                            "audio_cancelled", stage_frame=task.frame, text=task.text,
                            turn_segments=turn_segments, reason="speech_superseded",
                            callback=on_event,
                        )
                        break
                    if isinstance(exc, SpeechLocalizationError):
                        localization_failure_seen = True
                        self.audio_bus.emit_lifecycle(
                            "audio_localization_failed",
                            stage_frame=task.frame,
                            text=task.text,
                            turn_segments=turn_segments,
                            error=str(exc),
                            callback=on_event,
                        )
                        logger.warning("[tts-task-manager] localization contract failed: %s", exc)
                        continue
                    if not first_started:
                        first_started = True
                        _call_once(on_first_start)
                    self.audio_bus.emit_lifecycle(
                        "audio_failed",
                        stage_frame=task.frame,
                        text=task.text,
                        turn_segments=turn_segments,
                        error=str(exc),
                        callback=on_event,
                    )
                    continue
                if cancel_event.is_set():
                    break
                if not _speech_task_is_current(backend, task):
                    self.audio_bus.emit_lifecycle(
                        "audio_cancelled", stage_frame=task.frame, text=task.text,
                        turn_segments=turn_segments, reason="stale_turn_before_playback",
                        callback=on_event,
                    )
                    break

                if not wav_bytes:
                    self.audio_bus.emit_lifecycle(
                        "audio_cancelled",
                        stage_frame=task.frame,
                        text=task.text,
                        turn_segments=turn_segments,
                        reason="empty_audio",
                        callback=on_event,
                    )
                    continue

                emotion_signature = self.audio_bus.emit_emotion_if_changed(
                    stage_frame=task.frame,
                    previous_emotion=emotion_signature,
                    callback=on_event,
                )
                lip_sync_audio = getattr(backend, 'lip_sync_audio', None)
                prepared_player = getattr(backend, 'play_prepared_audio', None)
                player = None
                playback_bytes = wav_bytes
                if callable(lip_sync_audio) and callable(prepared_player):
                    # Freeze output gain once: the envelope and device receive
                    # identical PCM even if the volume changes during setup.
                    playback_bytes = lip_sync_audio(wav_bytes)
                    player = prepared_player
                lip_sync = build_lip_sync_envelope(playback_bytes)
                # Bind each callback to its segment; a late device callback must
                # not observe state that the next loop iteration has replaced.
                playback_state = {'started': False, 'terminal': False}

                def _finish_audio(event_type: str, *, reason: str = '', error: str = '',
                                  state=playback_state, current_task=task) -> None:
                    with self._lock:
                        if state['terminal'] or generation != self._generation:
                            return
                        state['terminal'] = True
                        if event_type == 'audio_done' and not state['started']:
                            event_type = 'audio_failed'
                            reason = 'playback_not_started'
                        self.audio_bus.emit_lifecycle(
                            event_type, stage_frame=current_task.frame, text=current_task.text,
                            turn_segments=turn_segments, reason=reason, error=error,
                            callback=on_event,
                        )

                finish_audio = _finish_audio

                def _mark_audio_started(*, state=playback_state, current_task=task,
                                        envelope=lip_sync) -> None:
                    nonlocal first_started
                    with self._lock:
                        if (state['started'] or state['terminal'] or generation != self._generation
                                or cancel_event.is_set() or playback_stop.is_set()
                                or not _speech_task_is_current(backend, current_task)):
                            return
                        state['started'] = True
                        if not first_started:
                            first_started = True
                            _call_once(on_first_start)
                        if state['terminal'] or cancel_event.is_set() or playback_stop.is_set():
                            return
                        self.audio_bus.emit_lifecycle(
                            "audio_start", stage_frame=current_task.frame, text=current_task.text,
                            turn_segments=turn_segments, audio_envelope=envelope.values,
                            audio_frame_ms=envelope.frame_ms,
                            audio_duration_ms=envelope.duration_ms, audio_mouth_shape=envelope.shape,
                            callback=on_event,
                        )

                with self._lock:
                    if generation != self._generation or cancel_event.is_set() or playback_stop.is_set():
                        break
                    self._current_task = task
                    self._current_cancel = lambda reason, finish=_finish_audio: finish('audio_cancelled', reason=reason)
                    self._pending_tasks.pop(task.key, None)
                    self._pending_callbacks.pop(task.key, None)

                _play_audio_with_start_callback(
                    backend, playback_bytes, playback_stop, on_started=_mark_audio_started,
                    player=player,
                )
                if playback_stop.is_set() or cancel_event.is_set():
                    _finish_audio('audio_cancelled', reason='cancelled_during_playback')
                else:
                    _finish_audio('audio_done')

                with self._lock:
                    if self._current_task is task:
                        self._current_task = None
                        self._current_cancel = None
                finish_audio = None

                # Chỉ chờ khi còn segment kế tiếp. Pause dài phát lifecycle
                # thinking_started/thinking_finished để avatar/UI diễn đúng.
                if task is not tasks[-1] and not cancel_event.is_set():
                    def _emit_pause(event_type: str, reason: str) -> None:
                        self.audio_bus.emit_lifecycle(
                            event_type,
                            stage_frame=task.frame,
                            text=task.text,
                            reason=reason,
                            turn_segments=turn_segments,
                            callback=on_event,
                        )
                    if not self.continuation.wait_after_frame(
                        task.frame,
                        generation=generation,
                        cancel_event=cancel_event,
                        emit=_emit_pause,
                    ):
                        break
        except Exception as exc:
            if finish_audio is not None:
                finish_audio('audio_failed', error=str(exc))
                logger.warning("[tts-task-manager] playback failed: %s", exc)
                return
            if (
                not first_started
                and tasks
                and not cancel_event.is_set()
                and _speech_task_is_current(backend, tasks[0])
                and not localization_failure_seen
            ):
                first_started = True
                _call_once(on_first_start)
            failed = self._current_task or (tasks[0] if tasks else None)
            self.audio_bus.emit_lifecycle(
                "audio_failed",
                stage_frame=getattr(failed, "frame", None),
                text=str(getattr(failed, "text", "") or ""),
                turn_segments=turn_segments,
                error=str(exc),
                callback=on_event,
            )
            logger.warning("[tts-task-manager] playback failed: %s", exc)
        finally:
            if (
                not first_started
                and tasks
                and not cancel_event.is_set()
                and _speech_task_is_current(backend, tasks[0])
                and not localization_failure_seen
            ):
                _call_once(on_first_start)
            with self._lock:
                if generation == self._generation:
                    self._current_task = None
                    self._current_cancel = None
                    self._pending_tasks = {}
                    self._pending_callbacks = {}
            self.continuation.complete(generation)
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        """Chờ queue hiện tại kết thúc, dùng cho ScriptedTakeRuntime.

        Chat thường không gọi hàm này. Take runtime cần ranh giới thật giữa cụm
        thoại và đạo cụ Tutor, nếu không panel mở giữa lúc câu trước còn đang nói.
        """

        with self._lock:
            thread = self._playback_thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(timeout=None if timeout is None else max(0.0, float(timeout)))
        return not thread.is_alive()

    def _wait_future(self, future: Future[bytes], *, cancel_event: threading.Event) -> bytes:
        while not cancel_event.is_set():
            try:
                return future.result(timeout=0.05)
            except TimeoutError:
                continue
        raise RuntimeError("cancelled_while_waiting_for_synth")

    def _prebuffer_window(
        self,
        tasks: list[TtsSegmentTask],
        futures: dict[tuple[str, int], Future[bytes]],
        *,
        cancel_event: threading.Event,
    ) -> None:
        """Đợi một cửa sổ audio sẵn sàng trước khi mở loa.

        Đây là lớp chống dead-air: với lượt 2–3 segment, cả lượt được synth song
        song rồi mới phát segment đầu. Ta đổi vài giây chờ *trước khi nói* lấy
        một câu nói liền mạch, thay vì Eris mở miệng rồi đứng hình 5 giây giữa câu.
        Lượt dài hơn chỉ prebuffer ba segment đầu để không đội startup vô hạn.
        """
        if len(tasks) <= 1:
            return
        target = min(len(tasks), _prebuffer_segments())
        for task in tasks[:target]:
            future = futures.get(task.key)
            if future is None:
                continue
            while not future.done():
                if cancel_event.wait(0.05):
                    raise RuntimeError("cancelled_while_prebuffering")
        logger.debug(
            "[tts-task-manager] prebuffer ready=%d/%d", target, len(tasks)
        )

    def _buffer_ready_callback(
        self,
        task: TtsSegmentTask,
        *,
        cancel_event: threading.Event,
        turn_segments: tuple[dict[str, object], ...],
        on_event: Callable[[Any, Any], None] | None,
    ):
        """Tạo callback báo audio đã sẵn sàng mà không chặn playback loop."""

        def _callback(future: Future[bytes]) -> None:
            if cancel_event.is_set() or future.cancelled():
                return
            try:
                wav_bytes = future.result()
            except Exception:
                return
            if not wav_bytes or cancel_event.is_set():
                return
            self.audio_bus.emit_lifecycle(
                "audio_buffer_ready",
                stage_frame=task.frame,
                text=task.text,
                turn_segments=turn_segments,
                callback=on_event,
            )

        return _callback

    def _cache_key(self, task: TtsSegmentTask, *, backend: Any) -> str:
        signature = ""
        signer = getattr(backend, "cache_signature", None)
        if callable(signer):
            try:
                signature = str(signer() or "")
            except Exception:
                signature = ""
        payload = "\n".join(
            [
                signature,
                task.text,
                str(task.valence),
                str(task.arousal),
                task.stance,
                str(getattr(task.frame, "prosody_style", "") or ""),
                str(getattr(task.frame, "prosody_notes", "") or ""),
                repr(_performance_kwargs(task.frame)),
                repr(_speech_kwargs(task.frame)),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()

    def _cache_get(self, key: str) -> bytes | None:
        with self._lock:
            value = self._cache.get(key)
            if value:
                self._cache.move_to_end(key)
            return value

    def _cache_put(self, key: str, wav_bytes: bytes) -> None:
        if not wav_bytes or _audio_cache_size() <= 0:
            return
        with self._lock:
            self._cache[key] = wav_bytes
            self._cache.move_to_end(key)
            limit = _audio_cache_size()
            while len(self._cache) > limit:
                self._cache.popitem(last=False)


def _play_audio_with_start_callback(
    backend: Any,
    wav_bytes: bytes,
    stop_event: threading.Event,
    *,
    on_started: Callable[[], None],
    player: Callable[..., None] | None = None,
) -> None:
    """Gọi backend mới tại mốc thiết bị bắt đầu phát, vẫn tương thích backend cũ."""

    if player is None:
        player = getattr(backend, "play_audio")
    supports_callback = False
    try:
        params = tuple(inspect.signature(player).parameters.values())
        supports_callback = (
            any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params)
            or any(param.name == "on_started" for param in params)
        )
    except (TypeError, ValueError):
        supports_callback = False
    if supports_callback:
        player(wav_bytes, stop_event, on_started=on_started)
        return
    # Adapter cũ không có callback. Đây là độ chính xác tốt nhất có thể mà không
    # bắt tất cả mock/provider cũ đổi chữ ký trong cùng một release.
    on_started()
    player(wav_bytes, stop_event)


def _audio_asset_candidates(raw_path: str) -> tuple[Path, ...]:
    """Trả các vị trí có thể chứa asset, hỗ trợ chạy từ repo hoặc thư mục khác."""
    asset = Path(str(raw_path or "").strip())
    if not str(asset):
        return ()
    if asset.is_absolute():
        return (asset,)
    candidates: list[Path] = []
    configured_root = os.environ.get("ERIS_AUDIO_ASSET_ROOT", "").strip()
    if configured_root:
        candidates.append(Path(configured_root) / asset)
    candidates.append(Path.cwd() / asset)
    # runtime/tts_task_manager.py -> repo root là parent.parent
    candidates.append(Path(__file__).resolve().parent.parent / asset)
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve(strict=False))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return tuple(unique)


def _read_audio_asset(frame: Any) -> bytes | None:
    raw_path = str(getattr(frame, "audio_asset", "") or "").strip()
    if not raw_path:
        return None
    for path in _audio_asset_candidates(raw_path):
        try:
            if not path.is_file():
                continue
            data = path.read_bytes()
            # WAV PCM thường bắt đầu RIFF/RF64. File quá ngắn hoặc sai header sẽ
            # fallback TTS live thay vì ném lỗi khó hiểu vào playback backend.
            if len(data) >= 44 and data[:4] in {b"RIFF", b"RF64"}:
                logger.debug("[tts-task-manager] preset hit: %s", path)
                return data
            logger.warning("[tts-task-manager] preset audio không hợp lệ: %s", path)
        except Exception as exc:
            logger.debug("[tts-task-manager] không đọc được preset %s: %s", path, exc)
    return None


def _prefetch_workers() -> int:
    from runtime.stage_profiles import active_stage_profile
    return max(1, min(6, active_stage_profile().prefetch_workers))


def _dispatch_policy(
    tasks: list[TtsSegmentTask],
    *,
    prefetch_workers: int | None = None,
    max_prefetch_segments: int | None = None,
) -> TtsDispatchPolicy:
    if any(getattr(task.frame, "delivery", {}).get("learning_canvas") for task in tasks):
        return TtsDispatchPolicy(strategy="prefetch", workers=min(3, max(1, len(tasks))), reason="canvas_per_segment_start")
    # Audio preset phải đi qua task manager để đọc file và vẫn phát lifecycle event.
    if any(str(getattr(task.frame, "audio_asset", "") or "").strip() for task in tasks):
        return TtsDispatchPolicy(strategy="prefetch", workers=1, reason="audio_preset")
    mode = os.environ.get("ERIS_TTS_TASK_STRATEGY", "auto").strip().lower()

    total_chars = sum(len(task.text) for task in tasks)
    segment_count = len(tasks)
    max_segments = max_prefetch_segments or _max_prefetch_segments()
    max_chars = _max_prefetch_chars()
    max_single = _max_prefetch_single_chars()
    if mode not in {"auto", "prefetch", "parallel", "queued", "inline", "join"}:
        mode = "auto"

    # P0 continuity contract: once StageOutput has split a turn into multiple
    # provider-safe clips, TaskManager owns that turn. It must NOT return False
    # merely because there are more clips than an old prefetch threshold. Doing
    # so routes the turn into the legacy queue, whose historical capacity was 4,
    # producing seq=1 cancellation and several seconds of synthesis dead air.
    if segment_count > 1:
        configured_workers = min(prefetch_workers or _prefetch_workers(), max(1, segment_count))
        from runtime.tts_budget import GLOBAL_TTS_REQUEST_BUDGET

        # Segment đã là phương án bất đắc dĩ. Khi đã phải chia, synth tối đa ba
        # mảnh đầu cùng lúc để clip sau sẵn sàng trước lúc clip trước phát xong.
        # max_concurrency vẫn là van an toàn cho provider/account pool.
        concurrency_cap = max(1, int(GLOBAL_TTS_REQUEST_BUDGET.max_concurrency))
        workers = min(segment_count, concurrency_cap, max(3, configured_workers))
        pressure_reasons: list[str] = []
        if segment_count > max_segments:
            pressure_reasons.append(f"segments={segment_count}>{max_segments}")
        if total_chars > max_chars:
            pressure_reasons.append(f"chars={total_chars}>{max_chars}")
        if any(len(task.text) > max_single for task in tasks):
            pressure_reasons.append(f"single_segment>{max_single}")
        # Không hạ về 2 worker chỉ vì tổng ký tự lớn. Chính việc hạ worker từng
        # làm segment 3 phải đợi segment 1 phát gần xong mới bắt đầu synth. Rate
        # limit đã có broker/circuit/account-pool chịu trách nhiệm riêng.
        forced_note = ";forced_inline_ignored_for_segmented_turn" if mode in {"inline", "join"} else ""
        reason = ";".join(pressure_reasons) or f"segments={segment_count};chars={total_chars}"
        return TtsDispatchPolicy(strategy="prefetch", workers=max(1, workers), reason=reason + forced_note)

    if mode in {"inline", "join"}:
        return TtsDispatchPolicy(strategy="inline", workers=1, reason="forced_inline_single_segment")

    workers = min(prefetch_workers or _prefetch_workers(), 1)
    return TtsDispatchPolicy(strategy="prefetch", workers=max(1, workers), reason=f"segments=1;chars={total_chars}")


def _prebuffer_segments() -> int:
    raw = os.environ.get("ERIS_TTS_PREFETCH_PREBUFFER_SEGMENTS", "3").strip()
    try:
        return max(1, min(6, int(raw)))
    except ValueError:
        return 3


def _max_prefetch_segments() -> int:
    raw = os.environ.get("ERIS_TTS_PREFETCH_MAX_SEGMENTS", "4").strip()
    try:
        return max(1, min(12, int(raw)))
    except ValueError:
        return 4


def _max_prefetch_chars() -> int:
    raw = os.environ.get("ERIS_TTS_PREFETCH_MAX_CHARS", "620").strip()
    try:
        return max(120, min(3000, int(raw)))
    except ValueError:
        return 620


def _max_prefetch_single_chars() -> int:
    raw = os.environ.get("ERIS_TTS_PREFETCH_MAX_SINGLE_CHARS", "280").strip()
    try:
        return max(80, min(1200, int(raw)))
    except ValueError:
        return 280


def _turn_segments(tasks: list[TtsSegmentTask]) -> tuple[dict[str, object], ...]:
    """Metadata nhỏ gửi qua event để interrupt ledger biết phần chưa phát."""

    return tuple(
        {
            "turn_id": str(getattr(task.frame, "turn_id", "") or ""),
            "segment_id": int(task.order),
            "text": task.text,
        }
        for task in tasks
    )


def _audio_cache_size() -> int:
    raw = os.environ.get("ERIS_TTS_TASK_AUDIO_CACHE_SIZE", "24").strip()
    try:
        return max(0, min(96, int(raw)))
    except ValueError:
        return 24


def _speech_kwargs(frame: Any) -> dict[str, object]:
    """Truyền contract ngôn ngữ/voice đã pin của StageFrame xuống backend."""
    contract = getattr(frame, "speech_contract", None)
    if not isinstance(contract, dict):
        return {}
    return {
        "language_hint": str(contract.get("language", "") or ""),
        "voice_family": str(contract.get("voice_family", "") or ""),
        "locale": str(contract.get("locale", "") or ""),
        "prelocalized": bool(contract.get("prelocalized", False)),
        "verified_transcript_hash": str(contract.get("verified_transcript_hash", "") or ""),
    }


def _performance_kwargs(frame: Any) -> dict[str, object]:
    delivery = getattr(frame, "delivery", {}) if frame is not None else {}
    if not isinstance(delivery, dict):
        return {}
    result: dict[str, object] = {}
    act = delivery.get("performance_act")
    if isinstance(act, dict):
        result.update({
            "vocal_cache_kind": str(act.get("kind", "") or ""),
            "vocal_prefix": str(act.get("audible_prefix", "") or ""),
        })
    voice_segment = delivery.get("voice_director_segment")
    if isinstance(voice_segment, dict):
        result["voice_director_segment"] = dict(voice_segment)
    return result


def _call_once(callback: Callable[[], None] | None) -> None:
    if callback is None:
        return
    try:
        callback()
    except Exception as exc:
        logger.debug("[tts-task-manager] on_first_start failed: %s", exc)



def _speech_task_is_current(backend: Any, task: TtsSegmentTask) -> bool:
    validator = getattr(backend, "is_speech_epoch_current", None)
    if not callable(validator) or int(task.speech_epoch or 0) <= 0:
        return True
    try:
        return bool(validator(task.speech_epoch, str(getattr(task.frame, "turn_id", "") or "")))
    except Exception:
        return False

__all__ = ["TtsDispatchPolicy", "TtsSegmentTask", "TtsTaskManager"]

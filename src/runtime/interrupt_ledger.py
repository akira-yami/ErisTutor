from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from runtime.open_loop_store import OpenLoopStore, SuspendedMove


_WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class ActiveSpeechSpan:
    """Đoạn Eris đang thật sự phát ra loa.

    Ledger chỉ xem một câu là "đang nói" sau event ``audio_start``. Nếu mới
    queue/synthesize mà user chen vào thì nên dừng TTS, nhưng không nên bắt Eris
    diễn như thể bạn vừa nghe/cắt một câu chưa từng phát ra.
    """

    turn_id: str
    segment_id: int
    text: str
    started_at: float
    interruptible: bool = True
    channel: str = ""
    prosody_style: str = ""
    pending_after_text: str = ""


@dataclass(frozen=True, slots=True)
class InterruptEvent:
    """Một lần user chen vào khi Eris đang nói.

    Đây là event phiên hiện tại, không phải long-term memory. Nó giữ đủ chi tiết
    để lượt kế tiếp có thể phản ứng như đang hiện diện: biết mình vừa nói tới
    đâu, phần nào bị cắt, và user chen bằng câu gì.
    """

    interrupted_turn_id: str
    interrupted_seq: int
    spoken_text_so_far: str
    pending_text: str
    user_text: str
    timestamp: float
    full_text: str = ""
    channel: str = ""
    reason: str = "user_interrupt"
    confidence: float = 1.0

    def to_prompt_context(self) -> str:
        """Context ngắn đưa vào prompt lượt chat kế tiếp.

        Ghi bằng tiếng Anh để không làm nhiễu instruction nội bộ hiện có. Model
        vẫn trả lời theo ngôn ngữ/giọng của Eris như các context khác.
        """

        spoken = _compact(self.spoken_text_so_far, limit=220) or "(barely started)"
        pending = _compact(self.pending_text, limit=260) or "(no meaningful remainder)"
        user = _compact(self.user_text, limit=220)
        return (
            "speech_interrupt:\n"
            f"- interrupted_turn_id={self.interrupted_turn_id}\n"
            f"- interrupted_seq={self.interrupted_seq}\n"
            f"- spoken_text_so_far={spoken}\n"
            f"- cut_before={pending}\n"
            f"- user_interrupted_with={user}\n"
            "Use this only if it helps the reply feel present. Acknowledge the cut-in briefly, "
            "then answer the user's latest intent. Do not mention ids, ledgers, or technical details."
        )

    def to_suspended_move(
        self,
        *,
        session_id: str = "default",
        source_move_id: str = "",
        source_generation: int = 0,
        intent: str = "",
        interruption_kind: str = "steering",
        ttl_seconds: float = 180.0,
    ) -> SuspendedMove | None:
        """Chuyển đổi ``InterruptEvent`` thành ``SuspendedMove`` để lưu vào ``OpenLoopStore``.

        Phần ``pending_text`` được giữ làm ``pending_text_hint`` (gợi ý ý định còn dở
        cho lượt Root kế tiếp quyết định RESUME hay RETIRE, không phát lại nguyên văn).
        """
        store = OpenLoopStore(default_ttl_seconds=ttl_seconds)
        return store.capture_from_interrupt(
            self,
            session_id=session_id,
            source_move_id=source_move_id or self.interrupted_turn_id,
            source_generation=source_generation,
            intent=intent,
            interruption_kind=interruption_kind,
            now=self.timestamp,
        )

    def to_meta(self) -> dict[str, object]:
        return {
            "interrupted_turn_id": self.interrupted_turn_id,
            "interrupted_seq": self.interrupted_seq,
            "spoken_text_so_far": self.spoken_text_so_far,
            "pending_text": self.pending_text,
            "user_text": self.user_text,
            "timestamp": self.timestamp,
            "channel": self.channel,
            "reason": self.reason,
            "confidence": self.confidence,
        }


class InterruptLedger:
    """Session ledger cho turn-taking interruption.

    Tầng này không gọi TTS và không gọi LLM. Nó chỉ nghe lifecycle event của TTS,
    ước lượng phần đã phát, rồi tạo event khi user gửi input trong lúc audio còn
    sống. Giữ nó tách riêng giúp coordinator chỉ còn vai trò nối dây.
    """

    def __init__(self, *, max_events: int = 20) -> None:
        self.max_events = max(1, int(max_events))
        self._active: ActiveSpeechSpan | None = None
        self._events: list[InterruptEvent] = []
        self._lock = threading.RLock()

    def track_tts_event(self, event: Any, *, stage_frame: Any = None, timestamp: float | None = None) -> None:
        """Cập nhật trạng thái đang nói từ ``TtsPlaybackEvent``.

        ``stage_frame`` cho biết interruptible/channel/prosody. Nếu thiếu, ledger
        vẫn dùng field trên event để không phụ thuộc chặt vào StageFrame.
        """

        event_type = str(getattr(event, "type", "") or "")
        turn_id = str(getattr(event, "turn_id", "") or getattr(stage_frame, "turn_id", "") or "")
        try:
            segment_id = int(getattr(event, "segment_id", getattr(stage_frame, "segment_id", 0)) or 0)
        except (TypeError, ValueError):
            segment_id = 0
        now = time.time() if timestamp is None else float(timestamp)

        with self._lock:
            if event_type == "audio_start":
                text = str(getattr(event, "text", "") or getattr(stage_frame, "tts_text", "") or "")
                turn_segments = getattr(event, "turn_segments", ()) or ()
                self._active = ActiveSpeechSpan(
                    turn_id=turn_id,
                    segment_id=segment_id,
                    text=_compact(text, limit=4000),
                    started_at=now,
                    interruptible=bool(getattr(stage_frame, "interruptible", True)),
                    channel=_channel_from_stage_frame(stage_frame),
                    prosody_style=str(getattr(stage_frame, "prosody_style", "") or ""),
                    pending_after_text=_pending_after_segment(turn_segments, turn_id=turn_id, segment_id=segment_id),
                )
                return

            if event_type in {"audio_done", "audio_failed", "audio_cancelled"}:
                active = self._active
                if active is None:
                    return
                if turn_id and active.turn_id and turn_id != active.turn_id:
                    return
                if segment_id != active.segment_id:
                    return
                self._active = None

    def record_user_interrupt(
        self,
        *,
        user_text: str,
        timestamp: float | None = None,
        min_pending_chars: int | None = None,
    ) -> InterruptEvent | None:
        """Tạo InterruptEvent nếu user chen đúng lúc Eris đang phát audio."""

        user = _compact(user_text, limit=600)
        if not user:
            return None
        now = time.time() if timestamp is None else float(timestamp)
        threshold = _min_pending_chars() if min_pending_chars is None else max(0, int(min_pending_chars))

        with self._lock:
            active = self._active
            if active is None or not active.interruptible:
                return None

            spoken, current_pending, confidence = _estimate_spoken_and_pending(active.text, elapsed=max(0.0, now - active.started_at))
            pending = _join_pending_text(current_pending, active.pending_after_text)
            if len(pending) < threshold:
                return None

            event = InterruptEvent(
                interrupted_turn_id=active.turn_id,
                interrupted_seq=active.segment_id,
                spoken_text_so_far=spoken,
                pending_text=pending,
                user_text=user,
                timestamp=now,
                full_text=_join_pending_text(active.text, active.pending_after_text),
                channel=active.channel,
                confidence=confidence,
            )
            self._events.append(event)
            if len(self._events) > self.max_events:
                del self._events[:-self.max_events]
            # Sau khi user chen vào, quyền sở hữu lượt chuyển sang user. Audio
            # sẽ bị TTS cancel ở coordinator; ledger clear trước để tránh ghi đúp.
            self._active = None
            return event

    def active(self) -> ActiveSpeechSpan | None:
        with self._lock:
            return self._active

    def recent(self, limit: int = 5) -> tuple[InterruptEvent, ...]:
        if limit <= 0:
            return ()
        with self._lock:
            return tuple(self._events[-limit:])


def _channel_from_stage_frame(stage_frame: Any) -> str:
    source = str(getattr(stage_frame, "source", "") or "")
    if source.startswith("decision:"):
        return source.split(":", 1)[1].strip()
    return source


def _pending_after_segment(turn_segments: Any, *, turn_id: str, segment_id: int) -> str:
    """Lấy các segment chưa phát sau segment hiện tại.

    Task manager gửi ``turn_segments`` trong event ``audio_start``. Nhờ vậy khi user
    cắt ở segment đầu, Eris vẫn biết phần "pending" còn bao gồm các câu sau chứ
    không chỉ phần đuôi của câu đang phát.
    """

    if not turn_segments:
        return ""
    pieces: list[tuple[int, str]] = []
    for item in turn_segments:
        if not isinstance(item, dict):
            continue
        item_turn = str(item.get("turn_id", "") or "")
        if turn_id and item_turn and item_turn != turn_id:
            continue
        try:
            item_segment = int(item.get("segment_id", 0) or 0)
        except (TypeError, ValueError):
            continue
        if item_segment <= int(segment_id):
            continue
        text = _compact(str(item.get("text", "") or ""), limit=1200)
        if text:
            pieces.append((item_segment, text))
    if not pieces:
        return ""
    pieces.sort(key=lambda pair: pair[0])
    return _compact(" ".join(text for _, text in pieces), limit=4000)


def _join_pending_text(current_pending: str, pending_after: str) -> str:
    parts = [
        _compact(current_pending, limit=4000),
        _compact(pending_after, limit=4000),
    ]
    return _compact(" ".join(part for part in parts if part), limit=5000)


def _min_pending_chars() -> int:
    raw = os.environ.get("ERIS_INTERRUPT_MIN_PENDING_CHARS", "10").strip()
    try:
        return max(0, min(120, int(raw)))
    except ValueError:
        return 10


def _chars_per_second() -> float:
    raw = os.environ.get("ERIS_INTERRUPT_EST_CHARS_PER_SEC", "13.0").strip()
    try:
        return max(6.0, min(28.0, float(raw)))
    except ValueError:
        return 13.0


def _estimate_spoken_and_pending(text: str, *, elapsed: float) -> tuple[str, str, float]:
    """Ước lượng phần đã nghe dựa trên thời gian phát.

    Gemini TTS hiện không trả word timestamp. Ta chọn ước lượng bảo thủ theo
    word-boundary: đủ tự nhiên để phản ứng, không giả vờ biết chính xác tới từng
    âm tiết. ``confidence`` thấp hơn khi audio mới chạy rất ngắn.
    """

    clean = _compact(text, limit=4000)
    if not clean:
        return "", "", 0.0

    est_chars = int(elapsed * _chars_per_second())
    if est_chars <= 0:
        return "", clean, 0.45
    if est_chars >= len(clean):
        return clean, "", 0.72

    split_at = _nearest_word_boundary(clean, est_chars)
    spoken = clean[:split_at].strip()
    pending = clean[split_at:].strip()
    confidence = 0.55 if elapsed < 0.4 else 0.78
    return spoken, pending, confidence


def _nearest_word_boundary(text: str, target: int) -> int:
    target = max(0, min(len(text), int(target)))
    if target <= 0:
        return 0
    if target >= len(text):
        return len(text)

    # Ưu tiên ranh giới từ gần nhất quanh target để prompt không chứa nửa chữ.
    candidates: list[int] = []
    for match in _WORD_RE.finditer(text):
        if match.end() <= target:
            candidates.append(match.end())
        elif match.start() >= target:
            candidates.append(match.start())
            break
    if not candidates:
        return target
    return min(candidates, key=lambda idx: abs(idx - target))


def _compact(text: str, *, limit: int) -> str:
    clean = " ".join(str(text or "").replace("\r", "\n").split()).strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip() + "..."


__all__ = ["ActiveSpeechSpan", "InterruptEvent", "InterruptLedger"]

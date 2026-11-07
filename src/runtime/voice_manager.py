# -*- coding: utf-8 -*-
"""voice_manager.py - điều phối turn-taking giữa TTS và tín hiệu user.

VoiceManager không tự thu mic và không khóa Eris vào một ASR/VAD cụ thể. Nó là
lớp state machine chung:

- nhận lifecycle TTS để cập nhật InterruptLedger;
- nhận text interrupt hiện tại và dừng toàn bộ segment còn pending;
- nhận VAD start + ASR partial trong tương lai để commit barge-in khi đủ nghĩa;
- phân biệt backchannel ngắn với correction rõ ràng.

Nhờ vậy coordinator không còn tự ghép ``ledger.record`` rồi ``tts.cancel`` ở
nhiều chỗ, một nghi thức rất dễ tạo ra audio zombie sau interrupt.

Nâng cấp Incremental Dialogue (Text-first):
Khi phân loại một từ ngắn như "ừ" trong ``_strategy_for_text``, xét thêm ngữ cảnh
câu đang phát trong ``InterruptLedger`` (hoặc ``previous_assistant_text`` được truyền
vào): nếu Eris vừa đặt câu hỏi xác nhận, "ừ" là ``yield`` (câu trả lời thực sự)
chứ không phải ``continue_short``.
"""

from __future__ import annotations
from core.di import resolve_dependency

import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from runtime.interrupt_ledger import InterruptEvent, InterruptLedger
from runtime.conversation.interrupts import classify_interrupt
from runtime.conversation.contracts import InterruptKind


_CORRECTION_RE = re.compile(
    r"^(?:không|khoan|chờ|dừng|ý tôi là|không phải|no|wait|stop|hold on)\b",
    re.I,
)
_WORD_RE = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class BargeInDecision:
    committed: bool
    strategy: str  # pending | yield_repair | continue_short | yield
    text: str = ""
    confidence: float = 0.0
    interrupt_event: InterruptEvent | None = None
    cancelled_audio: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "committed": self.committed,
            "strategy": self.strategy,
            "text": self.text,
            "confidence": self.confidence,
            "cancelled_audio": self.cancelled_audio,
            "interrupt_event": self.interrupt_event.to_meta() if self.interrupt_event else None,
        }


class VoiceManager:
    """State machine turn-taking, dùng được cho text hiện tại và voice sau này."""

    def __init__(self, *, ledger: InterruptLedger | None = None, tts_engine: Any = None) -> None:
        self.ledger = resolve_dependency(ledger, lambda: InterruptLedger())
        self.tts_engine = tts_engine
        self._vad_started_at = 0.0
        self._partial_text = ""
        self._lock = threading.RLock()

    def bind_tts_engine(self, tts_engine: Any) -> None:
        self.tts_engine = tts_engine

    def track_tts_event(self, event: Any, *, stage_frame: Any = None, timestamp: float | None = None) -> None:
        self.ledger.track_tts_event(event, stage_frame=stage_frame, timestamp=timestamp)

    def interrupt_with_text(
        self,
        user_text: str,
        *,
        timestamp: float | None = None,
        reason: str = "user_interrupt",
        previous_assistant_text: str = "",
    ) -> BargeInDecision:
        """Commit interrupt từ chat hoặc ASR final/partial đủ nghĩa.

        Backchannel là ngoại lệ quan trọng: caller cũ có thể gọi method này
        trước khi classifier ở tầng chat chạy. Vì vậy chính VoiceManager phải
        chặn ``ừm/đúng rồi/...`` khỏi ``supersede_all()`` để không có đường
        hard-cancel audio ngoài ý muốn, TRỪ KHI Eris vừa đặt câu hỏi xác nhận.
        """

        text = " ".join(str(user_text or "").split()).strip()
        if not text:
            return self.interrupt_signal(reason=reason)
        strategy = self._strategy_for_text(text, previous_assistant_text=previous_assistant_text)
        if strategy == "continue_short":
            return BargeInDecision(
                committed=False,
                strategy=strategy,
                text=text,
                confidence=1.0,
                interrupt_event=None,
                cancelled_audio=False,
            )
        event = self.ledger.record_user_interrupt(
            user_text=text,
            timestamp=time.time() if timestamp is None else float(timestamp),
        )
        cancelled = self._cancel_audio(reason=reason)
        return BargeInDecision(
            committed=bool(event is not None or cancelled),
            strategy=strategy,
            text=text,
            confidence=1.0,
            interrupt_event=event,
            cancelled_audio=cancelled,
        )

    def interrupt_signal(self, *, reason: str = "voice_started") -> BargeInDecision:
        """Dừng audio ngay khi frontend/VAD đã commit nhưng chưa có transcript."""

        cancelled = self._cancel_audio(reason=reason)
        return BargeInDecision(
            committed=cancelled,
            strategy="yield",
            text="",
            confidence=0.0,
            cancelled_audio=cancelled,
        )

    def on_vad_start(self, *, timestamp: float | None = None) -> BargeInDecision:
        """Mở tentative interrupt; chưa cancel chỉ vì một tiếng động ngắn."""

        with self._lock:
            self._vad_started_at = time.time() if timestamp is None else float(timestamp)
            self._partial_text = ""
        return BargeInDecision(False, "pending")

    def on_asr_partial(
        self,
        text: str,
        *,
        confidence: float = 0.0,
        timestamp: float | None = None,
    ) -> BargeInDecision:
        """Commit khi partial có correction marker hoặc ít nhất hai token nghĩa."""

        clean = " ".join(str(text or "").split()).strip()
        with self._lock:
            self._partial_text = clean
        meaningful_tokens = _WORD_RE.findall(clean)
        correction = bool(_CORRECTION_RE.search(clean))
        # Hai-token backchannel như "đúng rồi" không được rơi vào rule
        # ``>= 2 token`` rồi hard-cancel audio. Nó chỉ xác nhận user đang nghe.
        if clean and self._strategy_for_text(clean) == "continue_short":
            return BargeInDecision(False, "continue_short", clean, confidence)
        if not correction and len(meaningful_tokens) < 2:
            return BargeInDecision(False, "pending", clean, confidence)
        result = self.interrupt_with_text(
            clean,
            timestamp=timestamp,
            reason="barge_in_commit",
        )
        return BargeInDecision(
            committed=result.committed,
            strategy="yield_repair" if correction else result.strategy,
            text=clean,
            confidence=max(0.0, min(1.0, float(confidence or 0.0))),
            interrupt_event=result.interrupt_event,
            cancelled_audio=result.cancelled_audio,
        )

    def on_asr_final(
        self,
        text: str,
        *,
        confidence: float = 1.0,
        timestamp: float | None = None,
    ) -> BargeInDecision:
        """Resolve tentative VAD khi transcript final đã có.

        Final backchannel đóng tentative window nhưng vẫn giữ audio/continuation.
        Final steering/stop mới commit barge-in và hủy audio cũ.
        """

        clean = " ".join(str(text or "").split()).strip()
        try:
            if not clean:
                return BargeInDecision(False, "pending", "", confidence)
            if self._strategy_for_text(clean) == "continue_short":
                return BargeInDecision(False, "continue_short", clean, confidence)
            result = self.interrupt_with_text(
                clean,
                timestamp=timestamp,
                reason="barge_in_final",
            )
            return BargeInDecision(
                committed=result.committed,
                strategy=result.strategy,
                text=clean,
                confidence=max(0.0, min(1.0, float(confidence or 0.0))),
                interrupt_event=result.interrupt_event,
                cancelled_audio=result.cancelled_audio,
            )
        finally:
            self.on_vad_end()

    def on_vad_end(self) -> None:
        with self._lock:
            self._vad_started_at = 0.0
            self._partial_text = ""

    def _strategy_for_text(self, text: str, *, previous_assistant_text: str = "") -> str:
        # Chung classifier với UDP Chat; xét thêm câu Eris đang nói trong ledger
        # để phân biệt "ừ" trả lời câu hỏi với "ừ" phụ họa khi đang giảng bài.
        context_text = str(previous_assistant_text or "").strip()
        if not context_text:
            active_span = self.ledger.active() if self.ledger is not None else None
            if active_span is not None:
                context_text = str(getattr(active_span, "text", "") or "")
        decision = classify_interrupt(
            text,
            final=True,
            previous_assistant_text=context_text,
        )
        if decision.kind == InterruptKind.BACKCHANNEL:
            return "continue_short"
        if _CORRECTION_RE.search(text):
            return "yield_repair"
        return "yield"

    def _cancel_audio(self, *, reason: str) -> bool:
        tts = self.tts_engine
        # User đã giành lượt thì mọi audio cũ mất quyền phát, kể cả request remote
        # còn đang synth và chưa từng emit audio_start. Epoch mới ngăn WAV cũ sống
        # lại sau vài chục giây rate-limit/key rotation.
        supersede = getattr(tts, "supersede_all", None)
        if callable(supersede):
            try:
                result = supersede(reason=reason)
                return True if result is None else bool(result)
            except Exception:
                return False
        cancel = getattr(tts, "cancel_current", None)
        if not callable(cancel):
            return False
        try:
            result = cancel(reason=reason, only_interruptible=False)
            return True if result is None else bool(result)
        except Exception:
            return False


__all__ = ["BargeInDecision", "VoiceManager"]

# -*- coding: utf-8 -*-
"""InteractionDynamicsController, MicroActRouter & InteractionActReceipt.

Giải quyết 2 tình huống chồng lấn trọng tâm mà không cần Voice Input:
1. User nhắn từ ngắn ("ừ", "khoan", "đúng rồi"...):
   Thay vì phân loại cục bộ chỉ theo từ vựng + trạng thái bận như trước,
   ``InteractionDynamicsController`` xét theo ngữ cảnh câu nói trước đó của Eris:
   - Nếu Eris vừa đặt câu hỏi / mở slot chờ xác nhận (ví dụ: "vậy giữ cấu trúc cũ nhé?",
     "đúng không?"), câu "ừ" của user được phân loại là ``YIELD_TO_USER`` (nhường lượt
     và tạo semantic user turn thực sự để Root xử lý câu trả lời).
   - Nếu Eris đang giảng bài / giải thích mạch dài (không hỏi), câu "ừ" được phân loại
     là ``KEEP_SPEAKING`` (giữ nguyên audio đang phát và không restart Root).
   - Nếu user nói "khoan" lúc Eris đang nói -> ``HARD_STOP``; nhưng nếu Eris đang im lặng
     mà user mở đầu câu chuyện bằng "khoan hôm qua còn chuyện này..." -> không coi là lệnh
     dừng máy móc mà chuyển thành lượt hội thoại bình thường.

2. Chờ Root suy nghĩ gây trễ nhịp:
   Thay vì mọi phản hồi đều phải chờ đi qua toàn bộ semantic pipeline (Root -> Writer),
   ``MicroActRouter`` cho phép phát trực tiếp các hành vi tương tác ngắn (micro-act)
   KHÔNG mang mệnh đề/nội dung mới (``RECEIPT``, ``PROMPT_CONTINUE``, ``STOP_ACK``,
   ``CONTINUE_ACK``, ``REPAIR_REQUEST``), đồng thời ghi log riêng bằng
   ``InteractionActReceipt`` (không trộn làm bẩn lịch sử hội thoại chính của assistant).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
import threading
import time
import unicodedata
from typing import Any, Callable, Mapping, Sequence


class DynamicsAction(str, Enum):
    """Chính sách điều phối nhịp hội thoại khi có tin nhắn mới từ user."""

    KEEP_SPEAKING = "KEEP_SPEAKING"          # Giữ nguyên TTS đang phát, không restart Root
    YIELD_TO_USER = "YIELD_TO_USER"          # Nhường lượt cho user, tạo semantic turn mới
    SOFT_STOP = "SOFT_STOP"                  # Dừng mềm ở biên segment và xử lý turn mới
    HARD_STOP = "HARD_STOP"                  # Dừng ngay lập tức audio và hủy run cũ
    HARD_STOP_REPLAN = "HARD_STOP_REPLAN"    # Dừng ngay do user đính chính, lập kế hoạch lại
    RESUME_REQUEST = "RESUME_REQUEST"        # User yêu cầu nói tiếp open loop đang treo
    SEMANTIC_REVISION = "SEMANTIC_REVISION"  # User nhắn thêm khi Root đang nghĩ (chưa phát)


class MicroActKind(str, Enum):
    """Các loại phản hồi vi mô (micro-act) không mang nội dung/mệnh đề ngữ nghĩa mới."""

    NONE = "NONE"
    RECEIPT = "RECEIPT"                  # Báo "tôi đang nghe/đã nhận" ("Ừm.", "Ừ.")
    PROMPT_CONTINUE = "PROMPT_CONTINUE"  # Mời user kể tiếp ("Rồi sao?", "Ừ, sao nữa?")
    REPAIR_REQUEST = "REPAIR_REQUEST"    # Báo chưa rõ câu vừa gửi ("Hả?", "Hủh?", "Ý là sao?")
    STOP_ACK = "STOP_ACK"                # Xác nhận đã dừng theo lệnh cắt ngang ("Ừ, khoan.")
    CONTINUE_ACK = "CONTINUE_ACK"        # Nhường lại sàn nói cho user ("Ừ, nói tiếp đi.")
    PLAYFUL_CRY = "PLAYFUL_CRY"          # Giả khóc, mè nheo, mếu máo ("Huhu...", "Hức...")
    PLAYFUL_CHUCKLE = "PLAYFUL_CHUCKLE"  # Cười khúc khích ("Hehe", "Hihi")
    SULK = "SULK"                        # Giả dỗi, phồng má ("Hứ...")
    DEADPAN = "DEADPAN"                  # Cạn lời, thở dài giả bộ ("Haizz...")


# Tập câu bề mặt (surface) cố định, cực ngắn, tuyệt đối không chứa proposition/sự kiện mới.
_MICRO_ACT_SURFACES: dict[MicroActKind, str] = {
    MicroActKind.RECEIPT: "Ừm.",
    MicroActKind.PROMPT_CONTINUE: "Rồi sao?",
    MicroActKind.REPAIR_REQUEST: "Hủh?",
    MicroActKind.STOP_ACK: "Ừ, khoan.",
    MicroActKind.CONTINUE_ACK: "Ừ, nói tiếp đi.",
    MicroActKind.PLAYFUL_CRY: "Huhu...",
    MicroActKind.PLAYFUL_CHUCKLE: "Hehe.",
    MicroActKind.SULK: "Hứ.",
    MicroActKind.DEADPAN: "Haizz.",
}


@dataclass(frozen=True, slots=True)
class InteractionActReceipt:
    """Biên nhận ghi log một micro-act đã phát ra ngoài.

    Được lưu tách biệt với lịch sử hội thoại semantic của Assistant để tránh làm
    prompt các lượt sau ngập những câu "Ừm.", "Rồi sao?" và tránh để Long-term
    Memory học nhầm micro-act thành câu trả lời hoàn chỉnh.
    """

    act: MicroActKind
    surface: str
    delivered: bool
    timestamp: float = field(default_factory=time.time)
    related_input_revision: int = 0
    floor_intent: str = "YIELD"  # YIELD | HOLD | CLAIM
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.interaction_act_receipt.v1",
            "act": self.act.value,
            "surface": self.surface,
            "delivered": self.delivered,
            "timestamp": self.timestamp,
            "related_input_revision": self.related_input_revision,
            "floor_intent": self.floor_intent,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class DynamicsDecision:
    """Kết quả đánh giá ngữ cảnh của ``InteractionDynamicsController``."""

    action: DynamicsAction
    cancel_audio: bool
    cancel_generation: bool
    create_user_turn: bool
    capture_open_loop: bool
    micro_act: MicroActKind = MicroActKind.NONE
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "cancel_audio": self.cancel_audio,
            "cancel_generation": self.cancel_generation,
            "create_user_turn": self.create_user_turn,
            "capture_open_loop": self.capture_open_loop,
            "micro_act": self.micro_act.value,
            "reason": self.reason,
        }


_BACKCHANNEL_TOKENS = frozenset({
    "ừ", "uh", "uhm", "ừm", "à", "ờ", "ô", "ồ", "đúng", "đúng rồi",
    "ok", "oke", "mhm", "hm", "hmm", "được", "rồi", "yeah", "yep",
})

_EXPLICIT_STOP_ONLY_RE = re.compile(
    r"^(?:dừng|dừng lại|stop|khoan|khoan đã|đợi|đợi đã|thôi|im|đừng nói|ngưng)(?:\s+(?:đi|đã|lại|nhé|nữa))?[.!?\s]*$",
    re.I,
)

_STOP_PREFIX_RE = re.compile(
    r"^(?:dừng|stop|khoan|đợi|thôi|im|đừng nói|ngưng)\b",
    re.I,
)

_CORRECTION_RE = re.compile(
    r"(?:\bkhông phải\b|\bsai rồi\b|\bnhầm rồi\b|\bý tôi là\b|\bý mình là\b|\bkhông dùng\b|\bbỏ hướng đó\b|^không,\s)",
    re.I,
)

_RESUME_RE = re.compile(
    r"(?:\bnói tiếp đi\b|\bnói tiếp\b|\bnãy nói tiếp\b|\btiếp tục đi\b|\bkể tiếp đi\b|\bnói nốt đi\b)",
    re.I,
)

_QUESTION_ONLY_RE = re.compile(
    r"^(\?+|ủa\??|hả\??|hủh\??|ểh\??|hở\??|sao\??|gì cơ\??|gì dạ\??|gì thế\??)$",
    re.I,
)

_LAUGH_RE = re.compile(
    r"(?:\b(?:ha(?:ha)+|he(?:he)+|hi(?:hi)+|kaka|kak+a|cười|lol|lmao|nhộn|hài quá)\b)",
    re.I,
)

_CRY_RE = re.compile(
    r"(?:\b(?:hu(?:hu)+|hức|khóc|bắt nạt|mắng|tội nghiệp|dỗi rồi|mếu|ác độc)\b)",
    re.I,
)

_SULK_RE = re.compile(
    r"(?:\b(?:hứ|hừ|chê|xấu tính|đáng ghét|dở ẹc|ngốc|lì lợm|bướng|ghét)\b)",
    re.I,
)

_DEADPAN_RE = re.compile(
    r"(?:\b(?:haizz|chán|cạn lời|mệt mỏi|bó tay|bó chiếu|ngu ngơ|ngáo)\b)",
    re.I,
)

_OPEN_STORY_TRAILERS = (
    "...",
    "…",
    "xong rồi thì",
    "thế là",
    "lúc đó",
    "biết sao không",
    "đoán xem",
    "chuyện là",
    "hôm qua mới",
)

_QUESTION_CUES = (
    "?",
    "đúng không",
    "phải không",
    "được không",
    "nhé?",
    "nhỉ?",
    "chứ?",
    "sao?",
    "hả?",
    "không bạn",
    "không cậu",
    "không anh",
    "không em",
    "ổn không",
    "đồng ý không",
    "giữ cấu trúc cũ nhé",
    "hay sao",
)

_EDGE_PUNCT = " \t\r\n.!…。、！？"


def _normalize_text(text: str) -> str:
    norm = unicodedata.normalize("NFC", str(text or "")).casefold()
    return " ".join(norm.strip().split())


def assistant_opened_response_slot(assistant_text: str, *, last_assistant_act: str = "") -> bool:
    """Kiểm tra xem câu nói gần nhất của Eris có phải đang đặt câu hỏi / chờ user xác nhận không.

    Nếu Eris vừa hỏi (ví dụ: "Tôi nghĩ phương án A ổn hơn... đúng không?" hoặc
    "vậy giữ cấu trúc cũ nhé?"), thì từ "ừ" của user là CÂU TRẢ LỜI THỰC SỰ chứ
    không phải tiếng đệm phụ họa.
    """
    act = str(last_assistant_act or "").strip().lower()
    if act in {"question", "ask", "confirm", "propose", "check_understanding", "clarify"}:
        return True
    clean = _normalize_text(assistant_text)
    if not clean:
        return False
    # Kiểm tra phần cuối câu nói vừa rồi của Eris (hoặc toàn câu nếu ngắn)
    tail = clean[-160:]
    return any(cue in tail for cue in _QUESTION_CUES)


class InteractionDynamicsController:
    """Bộ điều phối nhịp hội thoại xét theo ngữ cảnh trước đó (Context-Sensitive Dynamics).

    Tách biệt rõ quyết định "lúc này làm gì về mặt nhịp hội thoại?" (when/how to act)
    khỏi quyết định "nội dung có nghĩa là gì?" của RootAgent.
    """

    def evaluate_incoming(
        self,
        user_text: str,
        *,
        speech_busy: bool = False,
        thinking_busy: bool = False,
        spoken_text_so_far: str = "",
        last_assistant_text: str = "",
        last_assistant_act: str = "",
        has_open_loop: bool = False,
    ) -> DynamicsDecision:
        """Phân loại hành vi cho tin nhắn mới của user dựa trên ngữ cảnh hội thoại."""
        normalized = _normalize_text(user_text)
        stripped_token = normalized.strip(_EDGE_PUNCT)
        if not stripped_token:
            return DynamicsDecision(
                action=DynamicsAction.KEEP_SPEAKING,
                cancel_audio=False,
                cancel_generation=False,
                create_user_turn=False,
                capture_open_loop=False,
                reason="empty_input",
            )

        # Tham chiếu ngữ cảnh câu Eris đang nói hoặc vừa nói xong.
        context_utterance = str(spoken_text_so_far or last_assistant_text or "").strip()
        response_slot_open = assistant_opened_response_slot(
            context_utterance,
            last_assistant_act=last_assistant_act,
        )

        # Trường hợp 1: User nhắn các từ ngắn dạng backchannel ("ừ", "ừm", "đúng rồi", "ok"...)
        if stripped_token in _BACKCHANNEL_TOKENS:
            if response_slot_open:
                # Eris vừa đặt câu hỏi -> "ừ" là câu trả lời đồng ý thực sự (YIELD_TO_USER).
                return DynamicsDecision(
                    action=DynamicsAction.YIELD_TO_USER,
                    cancel_audio=bool(speech_busy),
                    cancel_generation=bool(thinking_busy),
                    create_user_turn=True,
                    capture_open_loop=False,
                    micro_act=MicroActKind.NONE,
                    reason="affirmative_answer_to_assistant_question",
                )
            if speech_busy:
                # Eris đang giảng bài/giải thích -> "ừ" là tín hiệu đang lắng nghe (KEEP_SPEAKING).
                return DynamicsDecision(
                    action=DynamicsAction.KEEP_SPEAKING,
                    cancel_audio=False,
                    cancel_generation=False,
                    create_user_turn=False,
                    capture_open_loop=False,
                    micro_act=MicroActKind.NONE,
                    reason="listener_backchannel_while_speaking",
                )
            # Khi Eris không nói mà user nhắn "ừ" rời rạc -> vẫn đưa vào turn để xử lý theo ngữ cảnh.
            return DynamicsDecision(
                action=DynamicsAction.YIELD_TO_USER,
                cancel_audio=False,
                cancel_generation=False,
                create_user_turn=True,
                capture_open_loop=False,
                micro_act=MicroActKind.NONE,
                reason="standalone_short_acknowledgment",
            )

        # Trường hợp 2: User yêu cầu nói tiếp ý đang dở ("nói tiếp đi", "kể tiếp đi")
        if _RESUME_RE.search(normalized):
            return DynamicsDecision(
                action=DynamicsAction.RESUME_REQUEST,
                cancel_audio=bool(speech_busy),
                cancel_generation=bool(thinking_busy),
                create_user_turn=True,
                capture_open_loop=False,
                micro_act=MicroActKind.NONE,
                reason="explicit_resume_open_loop" if has_open_loop else "explicit_continue_request",
            )

        # Trường hợp 3: User đính chính / phủ định ("không, ý tôi là...", "sai rồi...")
        if _CORRECTION_RE.search(normalized):
            return DynamicsDecision(
                action=DynamicsAction.HARD_STOP_REPLAN,
                cancel_audio=True,
                cancel_generation=True,
                create_user_turn=True,
                capture_open_loop=bool(speech_busy),
                micro_act=MicroActKind.STOP_ACK if speech_busy else MicroActKind.NONE,
                reason="user_correction_cut_in",
            )

        # Trường hợp 4: Từ khóa dừng ("khoan", "dừng", "đợi"...) xét theo ngữ cảnh
        if _STOP_PREFIX_RE.search(normalized):
            is_short_stop_command = bool(_EXPLICIT_STOP_ONLY_RE.match(normalized)) or len(normalized.split()) <= 4
            if speech_busy or thinking_busy or is_short_stop_command:
                # Eris đang nói/nghĩ hoặc user ra lệnh dừng ngắn gọn -> HARD_STOP
                return DynamicsDecision(
                    action=DynamicsAction.HARD_STOP,
                    cancel_audio=True,
                    cancel_generation=True,
                    create_user_turn=True,
                    capture_open_loop=bool(speech_busy),
                    micro_act=MicroActKind.STOP_ACK if speech_busy else MicroActKind.NONE,
                    reason="explicit_stop_during_activity",
                )
            # Eris đang im lặng và user kể chuyện mở đầu bằng "khoan hôm qua còn chuyện này..."
            # -> Không coi là lệnh STOP máy móc mà là lượt kể chuyện bình thường.
            return DynamicsDecision(
                action=DynamicsAction.YIELD_TO_USER,
                cancel_audio=False,
                cancel_generation=False,
                create_user_turn=True,
                capture_open_loop=False,
                micro_act=MicroActKind.RECEIPT,
                reason="conversational_prefix_not_stop",
            )

        # Trường hợp 5: User nhắn thêm khi Root đang nghĩ (chưa phát âm thanh)
        if thinking_busy and not speech_busy:
            return DynamicsDecision(
                action=DynamicsAction.SEMANTIC_REVISION,
                cancel_audio=False,
                cancel_generation=True,
                create_user_turn=True,
                capture_open_loop=False,
                micro_act=MicroActKind.RECEIPT,
                reason="user_input_revision_while_thinking",
            )

        # Trường hợp 6: User cắt ngang bằng câu hỏi phụ hoặc chuyển hướng khi Eris đang nói
        if speech_busy:
            # Nếu câu chen ngắn/nhẹ thì ưu tiên SOFT_STOP và lưu SuspendedMove
            return DynamicsDecision(
                action=DynamicsAction.SOFT_STOP,
                cancel_audio=True,
                cancel_generation=True,
                create_user_turn=True,
                capture_open_loop=True,
                micro_act=MicroActKind.NONE,
                reason="steering_or_aside_during_speech",
            )

        # Trường hợp 7: Lượt hội thoại bình thường khi Eris đang rảnh
        micro_candidate = MicroActKind.RECEIPT
        if any(normalized.endswith(trailer) for trailer in _OPEN_STORY_TRAILERS):
            micro_candidate = MicroActKind.PROMPT_CONTINUE
        elif _QUESTION_ONLY_RE.match(normalized):
            micro_candidate = MicroActKind.REPAIR_REQUEST
        elif _CRY_RE.search(normalized):
            micro_candidate = MicroActKind.PLAYFUL_CRY
        elif _LAUGH_RE.search(normalized):
            micro_candidate = MicroActKind.PLAYFUL_CHUCKLE
        elif _SULK_RE.search(normalized):
            micro_candidate = MicroActKind.SULK
        elif _DEADPAN_RE.search(normalized):
            micro_candidate = MicroActKind.DEADPAN

        return DynamicsDecision(
            action=DynamicsAction.YIELD_TO_USER,
            cancel_audio=False,
            cancel_generation=False,
            create_user_turn=True,
            capture_open_loop=False,
            micro_act=micro_candidate,
            reason="normal_user_turn",
        )


class MicroActRouter:
    """Bộ định tuyến phát nhanh các micro-act không mang nội dung mới (bypass Root -> Writer).

    Luật cứng:
    - Chỉ được phát các interaction act thuộc tập hữu hạn (``RECEIPT``, ``PROMPT_CONTINUE``,
      ``STOP_ACK``, ``CONTINUE_ACK``, ``REPAIR_REQUEST``). Tuyệt đối không sinh mệnh đề mới.
    - Không tự phát đè lên chính TTS của Eris khi Eris đang nói bình thường.
    - Có cooldown ngắn để tránh lặp "Ừm. Ừm. Ừm." liên tục.
    - Mọi micro-act phát ra được ghi nhận bằng ``InteractionActReceipt`` riêng biệt.
    """

    def __init__(
        self,
        *,
        cooldown_seconds: float = 2.0,
        max_receipts: int = 32,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.max_receipts = max(4, int(max_receipts))
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._last_emitted_at: float = 0.0
        self._receipts: list[InteractionActReceipt] = []

    def select_and_emit(
        self,
        decision: DynamicsDecision,
        *,
        user_text: str = "",
        input_revision: int = 0,
        eris_currently_speaking: bool = False,
        full_reply_imminent: bool = False,
        emit_fn: Callable[[str, MicroActKind], bool] | None = None,
        now: float | None = None,
    ) -> InteractionActReceipt | None:
        """Chọn và phát trực tiếp một micro-act nếu thỏa điều kiện nhịp hội thoại."""
        ts = float(self.clock() if now is None else now)

        # Luật 1: Nếu Eris đang giữ quyền nói (KEEP_SPEAKING), tuyệt đối không phát micro-act đè lên chính mình.
        if decision.action == DynamicsAction.KEEP_SPEAKING or (eris_currently_speaking and not decision.cancel_audio):
            return None

        # Luật 2: Nếu câu trả lời đầy đủ từ semantic pipeline sắp phát ngay, nén micro-act để tránh lặp "Ừm. Ừm..."
        if full_reply_imminent:
            return None

        act = decision.micro_act
        if act == MicroActKind.NONE:
            clean = _normalize_text(user_text)
            if any(clean.endswith(trailer) for trailer in _OPEN_STORY_TRAILERS):
                act = MicroActKind.PROMPT_CONTINUE
            else:
                return None

        with self._lock:
            if self.cooldown_seconds > 0.0 and (ts - self._last_emitted_at) < self.cooldown_seconds:
                return None
            surface = _MICRO_ACT_SURFACES.get(act, "")
            if not surface:
                return None

            delivered = True
            if callable(emit_fn):
                try:
                    delivered = bool(emit_fn(surface, act))
                except Exception:
                    delivered = False

            # Cả "Ừm." lẫn "Rồi sao?" đều mang floor_intent = YIELD (trả quyền nói cho user).
            receipt = InteractionActReceipt(
                act=act,
                surface=surface,
                delivered=delivered,
                timestamp=ts,
                related_input_revision=max(0, int(input_revision)),
                floor_intent="YIELD",
                reason=decision.reason,
            )
            if delivered:
                self._last_emitted_at = ts
            self._receipts.append(receipt)
            if len(self._receipts) > self.max_receipts:
                del self._receipts[:-self.max_receipts]
            return receipt

    def recent_receipts(self, limit: int = 5) -> tuple[InteractionActReceipt, ...]:
        """Lấy các ``InteractionActReceipt`` gần nhất để Root biết tránh lặp mở câu."""
        if limit <= 0:
            return ()
        with self._lock:
            return tuple(self._receipts[-limit:])

    def recent_receipt_dicts(self, limit: int = 5) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.recent_receipts(limit=limit)]


def ensure_interaction_dynamics_controller(host: Any) -> InteractionDynamicsController:
    """Lấy hoặc khởi tạo ``InteractionDynamicsController`` trên host runtime."""
    ctrl = getattr(host, "interaction_dynamics_controller", None)
    if isinstance(ctrl, InteractionDynamicsController):
        return ctrl
    ctrl = InteractionDynamicsController()
    try:
        host.interaction_dynamics_controller = ctrl
    except Exception:
        pass
    return ctrl


def ensure_micro_act_router(host: Any) -> MicroActRouter:
    """Lấy hoặc khởi tạo ``MicroActRouter`` trên host runtime."""
    router = getattr(host, "micro_act_router", None)
    if isinstance(router, MicroActRouter):
        return router
    router = MicroActRouter()
    try:
        host.micro_act_router = router
    except Exception:
        pass
    return router


__all__ = [
    "DynamicsAction",
    "DynamicsDecision",
    "InteractionActReceipt",
    "InteractionDynamicsController",
    "MicroActKind",
    "MicroActRouter",
    "assistant_opened_response_slot",
    "ensure_interaction_dynamics_controller",
    "ensure_micro_act_router",
]

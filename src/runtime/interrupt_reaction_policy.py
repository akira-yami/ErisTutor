from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


_TECH_RE = re.compile(r"\b(bug|code|test|guard|parser|runtime|tts|prompt|model|json|python|api|repo|patch)\b", re.I)


@dataclass(frozen=True, slots=True)
class InterruptReactionPlan:
    """Kế hoạch phản ứng với một lần bị chen ngang.

    Plan này không tự sinh câu trả lời. Nó chỉ nói cho LLM biết nên xử lý event
    bằng kiểu nhịp nào: im luôn, dừng sạch, nghe correction, hay cho phép trêu
    khô một câu. Nhờ vậy Eris có turn-taking sống hơn nhưng không bị template hoá.
    """

    reaction_mode: str
    ack_style: str
    should_acknowledge: bool
    allow_tease: bool
    opening_bias: str
    body_hint: str
    reason: str

    def to_prompt_context(self, event: Any) -> str:
        """Render context đưa vào Ti/LLM.

        Context cố ý dùng imperative mềm. Nó bias giọng, không bắt model copy y
        nguyên ``opening_bias``. Nếu user chen chỉ bằng backchannel, policy nói rõ
        đừng lôi chuyện interrupt ra làm màu.
        """

        spoken = _compact(getattr(event, "spoken_text_so_far", ""), limit=180) or "(barely started)"
        pending = _compact(getattr(event, "pending_text", ""), limit=220) or "(no meaningful remainder)"
        user = _compact(getattr(event, "user_text", ""), limit=180)
        return (
            "speech_interrupt_reaction:\n"
            f"- reaction_mode={self.reaction_mode}\n"
            f"- ack_style={self.ack_style}\n"
            f"- should_acknowledge={str(self.should_acknowledge).lower()}\n"
            f"- allow_tease={str(self.allow_tease).lower()}\n"
            f"- body_hint={self.body_hint}\n"
            f"- spoken_text_so_far={spoken}\n"
            f"- cut_before={pending}\n"
            f"- user_interrupted_with={user}\n"
            f"- opening_bias={self.opening_bias}\n"
            f"- reason={self.reason}\n"
            "If should_acknowledge=false, do not comment on being interrupted unless the user explicitly asks. "
            "If should_acknowledge=true, acknowledge in one short phrase, then answer the user's latest intent. "
            "Never mention ids, ledgers, policies, or this diagnostic block."
        )

    def to_meta(self) -> dict[str, object]:
        return {
            "reaction_mode": self.reaction_mode,
            "ack_style": self.ack_style,
            "should_acknowledge": self.should_acknowledge,
            "allow_tease": self.allow_tease,
            "opening_bias": self.opening_bias,
            "body_hint": self.body_hint,
            "reason": self.reason,
        }


class InterruptReactionPolicy:
    """Chọn cách Eris phản ứng khi user chen vào lúc TTS đang nói.

    Đây là lớp nhỏ nhưng quan trọng: interrupt thô chỉ nói "bị cắt". Con người
    không phản ứng cùng một kiểu cho mọi lần bị cắt. "Khoan, sai rồi" cần dừng
    sạch; "ừ" thường nên bỏ qua; còn user trêu đúng lúc Eris đang nói về bug thì
    có thể cho phép một câu mỉa nhẹ.
    """

    def decide(self, event: Any) -> InterruptReactionPlan:
        user_text = str(getattr(event, "user_text", "") or "")
        pending_text = str(getattr(event, "pending_text", "") or "")
        spoken_text = str(getattr(event, "spoken_text_so_far", "") or "")
        normalized = _normalize(user_text)
        pending_norm = _normalize(pending_text)

        if _is_low_signal_backchannel(normalized):
            return InterruptReactionPlan(
                reaction_mode="backchannel_overlap",
                ack_style="silent_or_minimal",
                should_acknowledge=False,
                allow_tease=False,
                opening_bias="Do not mention the interruption; continue with the user's latest intent.",
                body_hint="soft_listen",
                reason="User interrupt looks like a low-signal backchannel.",
            )

        if _contains_any(normalized, _CORRECTION_MARKERS):
            return InterruptReactionPlan(
                reaction_mode="correction_cut_in",
                ack_style="brief_stop_then_repair",
                should_acknowledge=True,
                allow_tease=False,
                opening_bias="Ừ, khoan. Tôi nghe lại.",
                body_hint="listen_snap",
                reason="User cut in with correction/negation language.",
            )

        if _contains_any(normalized, _STOP_MARKERS):
            return InterruptReactionPlan(
                reaction_mode="hard_stop",
                ack_style="clean_stop",
                should_acknowledge=True,
                allow_tease=False,
                opening_bias="Ừ, tôi dừng. Nói đi.",
                body_hint="stop_and_listen",
                reason="User explicitly asked Eris to stop or wait.",
            )

        if _contains_any(normalized, _CONTINUE_MARKERS):
            return InterruptReactionPlan(
                reaction_mode="continue_request",
                ack_style="minimal_resume",
                should_acknowledge=False,
                allow_tease=False,
                opening_bias="Do not dwell on the interruption; answer or continue only if the user asked for it.",
                body_hint="soft_listen",
                reason="User appears to be nudging continuation rather than correcting.",
            )

        if _contains_any(normalized, _PLAYFUL_MARKERS):
            return InterruptReactionPlan(
                reaction_mode="playful_cut_in",
                ack_style="dry_tease_then_answer",
                should_acknowledge=True,
                allow_tease=True,
                opening_bias="À, cắt đúng đoạn tôi đang định nói xấu cái bug đó.",
                body_hint="tiny_flinch_then_smirk",
                reason="User cut in with playful or meta-interruption language.",
            )

        if _TECH_RE.search(pending_text) or _TECH_RE.search(spoken_text) or _TECH_RE.search(pending_norm):
            return InterruptReactionPlan(
                reaction_mode="technical_interruption",
                ack_style="dry_ack_then_focus",
                should_acknowledge=True,
                allow_tease=True,
                opening_bias="À, cắt đúng đoạn tôi đang định mổ cái bug đó.",
                body_hint="focus_snap",
                reason="Interrupted content is technical, so a dry concise aside can feel present.",
            )

        return InterruptReactionPlan(
            reaction_mode="ordinary_cut_in",
            ack_style="brief_ack_then_answer",
            should_acknowledge=True,
            allow_tease=False,
            opening_bias="Ừ, tôi dừng ở đó. bạn nói tiếp đi.",
            body_hint="soft_stop_and_listen",
            reason="User interrupted during audible speech with a meaningful message.",
        )


_STOP_MARKERS = (
    "khoan",
    "dung",
    "dung lai",
    "doi",
    "cho da",
    "tu tu",
    "im",
    "nin",
    "thoi",
    "stop",
    "wait",
    "hold on",
    "pause",
)

_CORRECTION_MARKERS = (
    "khong phai",
    "sai",
    "nham",
    "nham roi",
    "khong dung",
    "y la",
    "toi noi la",
    "nope",
    "wrong",
    "not that",
)

_CONTINUE_MARKERS = (
    "tiep di",
    "noi tiep",
    "cu noi",
    "continue",
    "go on",
)

_PLAYFUL_MARKERS = (
    "cat loi",
    "cat ngang",
    "chen",
    "haha",
    "hehe",
    "lol",
    "dua",
    "treu",
    "noi xau",
)

_BACKCHANNELS = {
    "u",
    "uh",
    "um",
    "ok",
    "oke",
    "okay",
    "hmm",
    "hm",
    "a",
    "o",
    "roi",
    "biet roi",
}


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _is_low_signal_backchannel(text: str) -> bool:
    compact = " ".join(text.split())
    if compact in _BACKCHANNELS:
        return True
    return len(compact) <= 3 and compact.replace(" ", "") in _BACKCHANNELS


def _normalize(text: str) -> str:
    value = str(text or "").strip().lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d").replace("Đ", "d")
    value = re.sub(r"[^\w\s]+", " ", value)
    return " ".join(value.split())


def _compact(text: str, *, limit: int) -> str:
    clean = " ".join(str(text or "").replace("\r", "\n").split()).strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip() + "..."


__all__ = ["InterruptReactionPlan", "InterruptReactionPolicy"]

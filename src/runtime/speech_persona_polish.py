# -*- coding: utf-8 -*-
"""speech_persona_polish.py — policy giữ personality khi localize TTS.

V39 không còn "làm ấm" mọi câu. Prosody style đã được runtime chọn từ affect
sống, nên localizer chỉ được giữ đúng nhiệt độ đó, không phủ thêm một warm-base
cố định. Lớp này không sinh nội dung và không gọi model riêng.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


_PROFILE_NAME = "infj_5w4_subtle"


@dataclass(frozen=True, slots=True)
class SpeechPersonaPolicy:
    enabled: bool
    profile: str
    source_warmth: float
    source_cuteness: float
    warmth: float
    cuteness: float
    mode: str
    signature_ending: str
    signature_surface: str
    signature_ending_selected: bool
    signature_ending_rate: float
    allows_relationship_escalation: bool
    allows_apology: bool
    allows_promise: bool

    @property
    def cache_key(self) -> str:
        return (
            f"{int(self.enabled)}:{self.profile}:{self.mode}:"
            f"srcw{self.source_warmth:.2f}:srcc{self.source_cuteness:.2f}:"
            f"w{self.warmth:.2f}:c{self.cuteness:.2f}:"
            f"sig={self.signature_ending}:{self.signature_surface}:use={int(self.signature_ending_selected)}:"
            f"rate={self.signature_ending_rate:.2f}:"
            f"r{int(self.allows_relationship_escalation)}:"
            f"a{int(self.allows_apology)}:p{int(self.allows_promise)}"
        )

    def prompt_directive(self) -> str:
        if not self.enabled:
            return (
                "Speech persona polish is disabled. Translate naturally and preserve the exact intent and affect."
            )

        warmth_pct = int(round(self.warmth * 100))
        cute_pct = int(round(self.cuteness * 100))
        mode_line = {
            "technical": "Keep technical clarity dominant and preserve the source affect; do not add friendliness as compensation.",
            "boundary": "Keep the boundary firm and preserve its current emotional temperature; respect does not require warmth.",
            "dry": "Keep the dry edge at the current intensity; do not add a warm underlay or artificial coldness.",
            "soft": "The live delivery state is explicitly soft/playful here; preserve that softness without exaggerating it.",
        }.get(self.mode, "Preserve the source emotional temperature; do not normalize it toward warm, cold, calm, cute, or restrained.")

        if self.signature_ending_selected and self.signature_ending == "desu_ne":
            surface = self.signature_surface or "ですね"
            signature_line = (
                "For this transcript only, the Eris signature ending is selected. When the final breath group "
                f"is a natural declarative or reflective sentence, let it naturally carry {surface} once if it fits. "
                "The signature family is ですね / だね / よね; use only one subtle marker. "
                f"Integrate it grammatically instead of mechanically appending {surface}. Never output a standalone "
                f"marker sentence such as {surface}。 Never force it onto a "
                "question, command, fragment, proper name, or sentence where it changes the stance. If it is not "
                "natural Japanese, omit it rather than damaging the meaning or affect."
            )
        else:
            signature_line = (
                "No signature ending is selected for this transcript. Do not deliberately add ですね, だね, or よね as a "
                "recurring character catchphrase; a natural sentence-final marker required by the translation is acceptable."
            )

        return (
            f"Speech persona profile: {self.profile}. "
            f"Style-selected softening={warmth_pct}% and cuteness={cute_pct}%; these are delivery modifiers, NOT an emotional target. "
            f"{mode_line} {signature_line} "
            "Preserve source valence, energy, irritation, delight, sadness, playfulness and relationship distance. "
            "You may adjust cadence or particles, but MUST NOT add new facts, affection, intimacy, apology, promise, agreement, request, action, or relationship status."
        )


def _clamp01(value: float, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _runtime_bool(name: str, default: bool) -> bool:
    try:
        from runtime.runtime_settings_store import runtime_bool

        return runtime_bool("tts", name, default=default)
    except Exception:
        return default


def _runtime_float(name: str, default: float) -> float:
    try:
        from runtime.runtime_settings_store import runtime_float

        return _clamp01(runtime_float("tts", name, default=default), default)
    except Exception:
        return default


def _runtime_text(name: str, default: str) -> str:
    try:
        from runtime.runtime_settings_store import runtime_text

        return runtime_text("tts", name, default=default) or default
    except Exception:
        return default


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in markers)


def _semantic_permissions(source: str) -> tuple[bool, bool, bool]:
    relationship = _contains_any(
        source,
        (
            "thích bạn", "yêu bạn", "thương bạn", "nhớ bạn", "quen bạn", "quen nhau",
            "muốn quen", "hẹn hò", "người yêu", "trái tim", "tình cảm", "muốn gần",
            "muốn ở bên", "ở bên nhau", "gặp bạn", "muốn gặp", "crush",
            "i like you", "i love you", "miss you", "date you", "relationship",
            "be with you", "meet you", "see you",
            "好き", "愛して", "恋人", "付き合", "会いたい",
        ),
    )
    apology = _contains_any(
        source,
        ("xin lỗi", "lỗi của tôi", "tôi sai", "sorry", "apolog", "ごめん", "すみません"),
    )
    promise = _contains_any(
        source,
        ("tôi hứa", "hứa với", "chắc chắn sẽ", "sẽ luôn", "i promise", "promise you", "約束", "必ず"),
    )
    return relationship, apology, promise


def _stable_roll(source_text: str, *, profile: str, mode: str) -> float:
    """Sinh roll ổn định theo câu, không làm cùng một câu đổi đuôi giữa các lượt.

    Không dùng ``hash()`` vì Python randomize hash theo process. BLAKE2 ở đây chỉ
    dùng để phân phối tần suất, không liên quan bảo mật.
    """

    payload = f"{profile}\n{mode}\n{str(source_text or '').strip()}".encode("utf-8", "ignore")
    raw = hashlib.blake2s(payload, digest_size=8).digest()
    return int.from_bytes(raw, "big") / float((1 << 64) - 1)


def _signature_ending_policy(
    source_text: str,
    *,
    profile: str,
    mode: str,
) -> tuple[str, bool, float]:
    """Chọn ``ですね`` theo tần suất có kiểm soát, không nối chuỗi hậu kỳ.

    Localizer vẫn chịu trách nhiệm ngữ pháp. Policy chỉ quyết định câu nào được
    phép mang signature, nhờ vậy Eris có dấu ấn nhưng không thành NPC lặp thoại.
    """

    enabled = _runtime_bool("signature_ending_enabled", True)
    ending = _runtime_text("signature_ending", "desu_ne").strip().lower()
    rate = _runtime_float("signature_ending_rate", 0.25)
    clean = str(source_text or "").strip()

    if not enabled or ending not in {"desu_ne"} or not clean:
        return "off", False, 0.0

    # Câu hỏi và các mode cần độ chính xác/sắc nét không bị catchphrase chen vào.
    if re.search(r"[?？]\s*$", clean) or mode in {"technical", "boundary"}:
        return ending, False, 0.0

    if mode == "soft":
        rate = min(0.58, rate + 0.08)
    elif mode == "dry":
        rate = min(rate, 0.24)

    # Fragment cực ngắn dễ nghe như bị gắn sticker ``ですね`` lên cuối.
    if len(clean) < 8:
        rate = min(rate, 0.12)

    rate = round(_clamp01(rate, 0.25), 3)
    selected = _stable_roll(clean, profile=profile, mode=mode) < rate
    return ending, bool(selected), rate


def _signature_surface_form(
    source_text: str,
    *,
    profile: str,
    mode: str,
    selected: bool,
) -> str:
    if not selected:
        return ""
    if mode == "soft":
        choices = ("ですね", "よね", "だね")
    elif mode == "dry":
        choices = ("ですね", "だね", "よね")
    else:
        choices = ("ですね", "だね", "よね")
    roll = _stable_roll(f"{source_text}\n#signature_surface", profile=profile, mode=mode)
    index = min(len(choices) - 1, int(roll * len(choices)))
    return choices[index]


def resolve_speech_persona_policy(
    source_text: str,
    *,
    prosody_style: str = "",
) -> SpeechPersonaPolicy:
    """Tạo policy theo style hiện tại, không tự suy đoán relationship state."""

    enabled = _runtime_bool("speech_persona_polish", True)
    profile = _runtime_text("speech_persona_profile", _PROFILE_NAME).strip().lower()
    source_warmth = _runtime_float("speech_persona_warmth", 0.36)
    source_cuteness = _runtime_float("speech_persona_cuteness", 0.08)
    warmth = source_warmth
    cuteness = source_cuteness
    style = str(prosody_style or "").strip().lower()

    if style in {"focused", "technical"}:
        mode = "technical"
        warmth = min(warmth, 0.16)
        cuteness = 0.0
    elif style == "cold_boundary":
        mode = "boundary"
        warmth = 0.0
        cuteness = 0.0
    elif style == "sharp_deadpan":
        mode = "dry"
        warmth = min(warmth, 0.08)
        cuteness = 0.0
    elif style in {"annoyed_soft", "deadpan", "dry_tease", "self_aware_dry"}:
        mode = "dry"
        warmth = min(warmth, 0.12)
        cuteness = min(cuteness, 0.04)
    elif style in {"soft_tease", "gentle", "warm", "warm_soft", "curious_soft"}:
        mode = "soft"
        warmth = min(0.48, max(warmth, 0.22))
        cuteness = min(0.18, max(cuteness, 0.04))
    elif style == "low_quiet":
        mode = "soft"
        warmth = min(warmth, 0.12)
        cuteness = 0.0
    else:
        mode = "neutral"
        warmth = 0.0
        cuteness = 0.0

    signature_ending, signature_selected, signature_rate = _signature_ending_policy(
        source_text,
        profile=profile or _PROFILE_NAME,
        mode=mode,
    )
    signature_surface = _signature_surface_form(
        source_text,
        profile=profile or _PROFILE_NAME,
        mode=mode,
        selected=signature_selected,
    )
    relationship, apology, promise = _semantic_permissions(source_text)
    return SpeechPersonaPolicy(
        enabled=bool(enabled),
        profile=profile or _PROFILE_NAME,
        source_warmth=round(source_warmth, 3),
        source_cuteness=round(source_cuteness, 3),
        warmth=round(warmth, 3),
        cuteness=round(cuteness, 3),
        mode=mode,
        signature_ending=signature_ending,
        signature_surface=signature_surface,
        signature_ending_selected=signature_selected,
        signature_ending_rate=signature_rate,
        allows_relationship_escalation=relationship,
        allows_apology=apology,
        allows_promise=promise,
    )


_ALWAYS_FORBIDDEN_RE = re.compile(
    r"(?:ご主人様|ごしゅじんさま|マスター|お兄ちゃん|お姉ちゃん|せんぱい|先輩|"
    r"にゃ(?:ん|ー)?|だよ[ぉ～〜]+|なの[ぉ～〜]+|えへへ|うふふ)",
    re.I,
)
_RELATIONSHIP_ESCALATION_RE = re.compile(
    r"(?:あなた(?:のこと)?が好き|君(?:のこと)?が好き|大好きだよ|愛してる|恋人にな|"
    r"付き合って|ずっとそばに|離れたくない|会いたい|寂しいよ|あなたなしでは)",
    re.I,
)
_APOLOGY_RE = re.compile(r"(?:ごめん(?:ね|なさい)?|すみません|申し訳(?:ない|ありません))")
_PROMISE_RE = re.compile(r"(?:約束する|約束だよ|必ず(?:する|守る|戻る|いる))")


def translation_respects_persona_boundaries(
    source_text: str,
    translated_text: str,
    policy: SpeechPersonaPolicy,
) -> bool:
    """Chặn những thứ localizer tuyệt đối không được tự thêm vào lời nói."""

    translated = str(translated_text or "").strip()
    if not translated:
        return False
    if _ALWAYS_FORBIDDEN_RE.search(translated):
        return False
    if not policy.allows_relationship_escalation and _RELATIONSHIP_ESCALATION_RE.search(translated):
        return False
    if not policy.allows_apology and _APOLOGY_RE.search(translated):
        return False
    if not policy.allows_promise and _PROMISE_RE.search(translated):
        return False
    return True


__all__ = [
    "SpeechPersonaPolicy",
    "resolve_speech_persona_policy",
    "translation_respects_persona_boundaries",
]

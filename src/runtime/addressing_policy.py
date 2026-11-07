# -*- coding: utf-8 -*-
"""Chính sách xưng hô public của Eris.

Mục tiêu không phải thay mù ``Ngài`` thành ``bạn`` ở mọi nơi. Cách vá đó giữ
nguyên nguồn gây lỗi và còn tạo câu méo như ``Thưa bạn`` hoặc ``Tên bạn vẫn còn
đây``. Module này gom toàn bộ xử lý về một contract:

1. bỏ lời gọi kiểu nghi lễ ở đầu câu;
2. sửa các cấu trúc sở hữu/chủ ngữ tối thiểu để câu vẫn đúng ngữ pháp;
3. tuyệt đối không để ``Ngài``/``Master`` lọt ra UI, TTS hoặc memory;
4. không đụng tới các từ kỹ thuật như ``master switch`` hay ``audio mastering``
   nếu chúng không phải cách gọi người dùng.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from runtime.user_addressing_state import user_addressing_snapshot


_LEGACY_TERM = r"(?:ng\u00e0i|ngai|master)"

# Lời gọi trực tiếp ở đầu câu. Xóa cả dấu câu theo sau để không còn dấu phẩy lẻ.
_VOCATIVE_START_RE = re.compile(
    rf"^\s*(?:(?:th\u01b0a|thua)\s+{_LEGACY_TERM}(?:\s+(?:\u01a1i|oi))?\s*[,;:\-–—.]*"
    rf"|{_LEGACY_TERM}\s+(?:\u01a1i|oi)\s*[,;:\-–—.]*"
    rf"|{_LEGACY_TERM}\s*[,;:\-–—.]+\s*)",
    re.IGNORECASE,
)

# Những cấu trúc cần giữ quan hệ ngữ pháp. Đây là rewrite có chủ đích, không phải
# global replace. ``của Ngài`` cần thành ``của bạn``; còn lời gọi trống thì bỏ.
_POSSESSIVE_RE = re.compile(rf"\bc\u1ee7a\s+{_LEGACY_TERM}\b|\bcua\s+{_LEGACY_TERM}\b", re.IGNORECASE)

# Chủ ngữ rõ ràng. Danh sách động từ đủ rộng cho câu hội thoại, nhưng không chạm
# vào cụm kỹ thuật ``master branch`` hoặc ``master switch``.
_SUBJECT_RE = re.compile(
    rf"\b{_LEGACY_TERM}\s+(?=(?:\u0111ang|dang|\u0111\u00e3|da|s\u1ebd|se|c\u00f3|co|mu\u1ed1n|muon|"
    rf"c\u1ea7n|can|ngh\u0129|nghi|th\u1ea5y|thay|nh\u1edb|nho|n\u00f3i|noi|h\u1ecfi|hoi|l\u00e0m|lam|"
    rf"th\u00edch|thich|kh\u00f4ng|khong|ch\u01b0a|chua|v\u1eabn|van|v\u1eeba|vua)\b)",
    re.IGNORECASE,
)

# Một vài mẫu danh từ hay xuất hiện trong memory. Giữ nghĩa thay vì để sanitizer
# xóa mất slot đang được hỏi.
_NOUN_OWNER_RE = re.compile(
    rf"\b(?P<noun>t\u00ean|ten|\u00fd|y|s\u1edf th\u00edch|so thich|k\u00fd \u1ee9c|ky uc|"
    rf"c\u00e2u h\u1ecfi|cau hoi|c\u00e2u tr\u1ea3 l\u1eddi|cau tra loi|m\u00e1y|may|project|repo)\s+{_LEGACY_TERM}\b",
    re.IGNORECASE,
)

# Chỉ bắt standalone legacy address. ``master_switch`` và ``mastering`` không bị
# đụng tới vì boundary/underscore khác hình dạng lời gọi.
_STANDALONE_RE = re.compile(rf"(?<![\w]){_LEGACY_TERM}(?![\w])", re.IGNORECASE)

_TECHNICAL_MASTER_NEIGHBORS = {
    "audio", "branch", "bus", "channel", "clock", "code", "control",
    "controller", "copy", "database", "data", "fader", "file", "gain",
    "key", "mix", "model", "node", "password", "process", "prompt",
    "record", "seed", "server", "switch", "track", "volume",
}


@dataclass(frozen=True, slots=True)
class AddressingResult:
    text: str
    changed: bool = False
    rules: tuple[str, ...] = ()


def _squash(text: str) -> str:
    out = re.sub(r"[ \t]+", " ", str(text or ""))
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"([,;:]){2,}", r"\1", out)
    out = re.sub(r"^\s*[,;:]+\s*", "", out)
    return out.strip()


def _replacement_at(match: re.Match[str], replacement: str) -> str:
    """Viết hoa chỉ khi token thật sự đứng đầu câu, không theo chữ nguồn."""
    before = match.string[: match.start()]
    stripped = before.rstrip()
    at_sentence_start = not stripped or stripped[-1:] in {".", "!", "?", "\n"}
    if at_sentence_start:
        return replacement[:1].upper() + replacement[1:]
    return replacement


_DYNAMIC_VERBS = (
    r"đang|dang|đã|da|sẽ|se|có|co|muốn|muon|cần|can|nghĩ|nghi|thấy|thay|"
    r"nhớ|nho|nói|noi|hỏi|hoi|làm|lam|thích|thich|không|khong|chưa|chua|"
    r"vẫn|van|vừa|vua|định|dinh|bảo|bao|muon|biết|biet|ngồi|ngoi|mở|mo|bật|bat"
)

def _replace_case(match: re.Match[str], replacement: str) -> str:
    token = match.group(0)
    if token[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _sanitize_assistant_self_pronoun(text: str) -> tuple[str, tuple[str, ...]]:
    """Giữ đại từ tự xưng của ERIS ổn định giữa startup/chat/autonomous.

    Chỉ sửa những vị trí có hình dạng chủ ngữ/ngôi sở hữu rõ; không global
    replace từ "em" vì nó có thể đang nói về người thứ ba.
    """

    state = user_addressing_snapshot()
    target = str(state.assistant_self_pronoun or "tôi").strip().lower() or "tôi"
    out = str(text or "")
    rules: list[str] = []
    # Các biến thể thường do model mirror register nhất thời của user.
    alternatives = tuple(item for item in ("em", "tui", "mình") if item != target)
    for wrong in alternatives:
        escaped = re.escape(wrong)
        subject = re.compile(
            rf"\b{escaped}\s+(?=(?:đang|dang|đã|da|sẽ|se|có|co|muốn|muon|cần|can|nghĩ|nghi|thấy|thay|"
            rf"nhớ|nho|nói|noi|hỏi|hoi|làm|lam|ở|o|vẫn|van|xin|nhận|nhan|cảm|cam)\b)",
            re.IGNORECASE,
        )
        out2, count = subject.subn(lambda m: _replace_case(m, target) + " ", out)
        if count:
            out = out2
            rules.append("assistant_self_pronoun")
        possessive = re.compile(rf"\b(?:của|cua)\s+{escaped}\b", re.IGNORECASE)
        out2, count = possessive.subn(lambda m: ("của " if "ủ" in m.group(0) else "cua ") + target, out)
        if count:
            out = out2
            rules.append("assistant_self_possessive")
    return _squash(out), tuple(dict.fromkeys(rules))


def _sanitize_dynamic_user_vocative(text: str, *, preserve_newlines: bool = False) -> tuple[str, tuple[str, ...]]:
    """Enforce explicit user addressing state on public text.

    Chỉ sửa các vị trí có hình dạng xưng hô trực tiếp/chủ ngữ/sở hữu. Không global
    replace mọi ``chị``/``anh`` vì assistant vẫn có thể nói về một người thứ ba.
    """

    state = user_addressing_snapshot()
    target = str(state.user_vocative or "bạn").strip().lower()
    forbidden = tuple(str(x or "").strip().lower() for x in state.forbidden_user_vocatives if str(x or "").strip())
    if target not in {"anh", "chị", "bạn"} or not forbidden:
        return text, ()

    out = str(text or "")
    rules: list[str] = []
    for wrong in forbidden:
        escaped = re.escape(wrong)
        # Lời gọi ở đầu câu hoặc sau dấu ngắt mạnh.
        vocative = re.compile(
            rf"(?P<prefix>^|[.!?]\s+)(?P<honor>(?:thưa\s+)?{escaped}(?:\s+ơi)?)(?=\s*[,;:!?.-]|\s)",
            re.IGNORECASE,
        )
        def vocative_repl(match: re.Match[str]) -> str:
            honor = match.group("honor")
            repl = target
            if "ơi" in honor.lower():
                repl += " ơi"
            if honor[:1].isupper():
                repl = repl[:1].upper() + repl[1:]
            return match.group("prefix") + repl
        out2, count = vocative.subn(vocative_repl, out)
        if count:
            out = out2
            rules.append("dynamic_vocative")

        # Sở hữu và các giới từ hướng thẳng vào người đang đối thoại.
        directed = re.compile(
            rf"(?P<prefix>\b(?:của|cua|với|voi|cho|gọi|goi|hỏi|hoi|nhắc|nhac|nói với|noi voi|trả lời|tra loi)\s+){escaped}\b",
            re.IGNORECASE,
        )
        def directed_repl(match: re.Match[str]) -> str:
            return match.group("prefix") + _replace_case(match, target)
        out2, count = directed.subn(directed_repl, out)
        if count:
            out = out2
            rules.append("dynamic_directed")

        # Chủ ngữ trực tiếp: ``chị đang...``, ``anh có...``.
        subject = re.compile(rf"\b{escaped}\s+(?=(?:{_DYNAMIC_VERBS})\b)", re.IGNORECASE)
        out2, count = subject.subn(lambda m: _replace_case(m, target) + " ", out)
        if count:
            out = out2
            rules.append("dynamic_subject")

    if preserve_newlines:
        cleaned = re.sub(r"[ \t\f\v]+", " ", out)
        cleaned = re.sub(r" *\n *", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip(), tuple(dict.fromkeys(rules))
    return _squash(out), tuple(dict.fromkeys(rules))


_CODE_BLOCK_RE = re.compile(r"(?:```[\w.+-]*\n?[\s\S]*?(?:```|\Z)|~~~[\w.+-]*\n?[\s\S]*?(?:~~~|\Z))", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")


def sanitize_legacy_addressing(text: str, *, surface: str = "display") -> AddressingResult:
    """Loại cách gọi cũ và sửa câu ở mức tối thiểu.

    ``surface`` hiện chỉ dùng cho trace/khả năng mở rộng. Cả display, TTS và
    memory đều phải có cùng invariant: không còn từ xưng hô legacy.
    """

    raw = str(text or "")
    if not raw:
        return AddressingResult(raw)

    code_snippets: list[str] = []

    def _save_fence(m: re.Match[str]) -> str:
        idx = len(code_snippets)
        code_snippets.append(m.group(0))
        return f"\n__ERIS_CODE_RESERVED_{idx}__\n"

    def _save_inline(m: re.Match[str]) -> str:
        idx = len(code_snippets)
        code_snippets.append(m.group(0))
        return f"__ERIS_CODE_RESERVED_{idx}__"

    out = _CODE_BLOCK_RE.sub(_save_fence, raw)
    out = _INLINE_CODE_RE.sub(_save_inline, out)
    rules: list[str] = []

    out2, count = _VOCATIVE_START_RE.subn("", out)
    if count:
        out = out2
        # Sau khi bỏ lời gọi đầu câu, giữ hình thức câu tự nhiên thay vì để
        # ``tôi`` viết thường ở đầu transcript.
        for index, char in enumerate(out):
            if char.isalpha():
                out = out[:index] + char.upper() + out[index + 1 :]
                break
        rules.append("drop_vocative")

    def possessive_repl(match: re.Match[str]) -> str:
        source = match.group(0)
        return "của bạn" if any(ch in source for ch in "ủà") else "cua ban"

    out2, count = _POSSESSIVE_RE.subn(possessive_repl, out)
    if count:
        out = out2
        rules.append("repair_possessive")

    def noun_owner_repl(match: re.Match[str]) -> str:
        noun = match.group("noun")
        return f"{noun} bạn"

    out2, count = _NOUN_OWNER_RE.subn(noun_owner_repl, out)
    if count:
        out = out2
        rules.append("repair_noun_owner")

    def subject_repl(match: re.Match[str]) -> str:
        return _replacement_at(match, "bạn") + " "

    out2, count = _SUBJECT_RE.subn(subject_repl, out)
    if count:
        out = out2
        rules.append("repair_subject")

    # Nếu còn standalone, dùng đại từ trung tính để bảo toàn nghĩa. Đây là lưới
    # cuối, không phải chiến lược chính. Nguồn sinh đã được cấm ở prompt/dataset.
    def standalone_repl(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.lower() == "master":
            before = match.string[: match.start()]
            after = match.string[match.end() :]
            prev_match = re.search(r"([A-Za-z_]+)\s*$", before)
            next_match = re.match(r"\s*([A-Za-z_]+)", after)
            prev_word = (prev_match.group(1).lower() if prev_match else "")
            next_word = (next_match.group(1).lower() if next_match else "")
            if prev_word in _TECHNICAL_MASTER_NEIGHBORS or next_word in _TECHNICAL_MASTER_NEIGHBORS:
                return token
        return _replacement_at(match, "bạn")

    out2 = _STANDALONE_RE.sub(standalone_repl, out)
    if out2 != out:
        out = out2
        rules.append("legacy_failsafe")

    preserve_newlines = bool(code_snippets and surface == "display")
    if preserve_newlines:
        out = re.sub(r"[ \t\f\v]+", " ", out)
        out = re.sub(r" *\n *", "\n", out)
        out = re.sub(r"\n{3,}", "\n\n", out).strip()
    else:
        out = _squash(out)
    out = re.sub(r"\b[Bb]\u1ea1n\s+[Bb]\u1ea1n\b", lambda m: "Bạn" if m.group(0)[:1].isupper() else "bạn", out)
    out, self_rules = _sanitize_assistant_self_pronoun(out)
    rules.extend(self_rules)
    out, dynamic_rules = _sanitize_dynamic_user_vocative(out, preserve_newlines=preserve_newlines)
    rules.extend(dynamic_rules)

    if code_snippets:
        for idx, snippet in enumerate(code_snippets):
            out = out.replace(f"__ERIS_CODE_RESERVED_{idx}__", snippet)
        if preserve_newlines:
            out = re.sub(r"[ \t\f\v]+", " ", out)
            out = re.sub(r" *\n *", "\n", out)
            out = re.sub(r"\n{3,}", "\n\n", out).strip()

    return AddressingResult(out, changed=out != raw, rules=tuple(dict.fromkeys(rules)))


def clean_public_addressing(text: str, *, surface: str = "display") -> str:
    return sanitize_legacy_addressing(text, surface=surface).text


__all__ = ["AddressingResult", "clean_public_addressing", "sanitize_legacy_addressing"]

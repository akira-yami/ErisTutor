# -*- coding: utf-8 -*-
"""Chuyển LaTeX/markdown trong lời nói thành văn bản đọc được cho TTS.

Bối cảnh: DeepSeek Web trả công thức dạng LaTeX (``\\(\\frac{3}{4}\\)``,
``\\[...\\]``, ``$...$``) và markdown (heading, bảng, ``**bold**``). Bong bóng
Rich Chat render các ký hiệu này đẹp bằng MathJax, nên ``text`` hiển thị GIỮ
nguyên LaTeX. Nhưng ``speech_text`` (nguồn cho TTS) nếu còn ``\\frac{3}{4}`` sẽ
bị đọc thành "backslash frac ba bốn" — thảm hoạ âm thanh.

Hàm ở đây chỉ chạy trên nhánh GIỌNG ĐỌC. Nó KHÔNG đụng text hiển thị. Chiến
lược fail-safe: mẫu lạ không parse được thì LOẠI ký hiệu nguy hiểm thay vì giữ,
để không bao giờ để ``\\ { } $ | # *`` lọt vào transcript TTS.

Không gọi model, không trạng thái; thuần hàm chuyển chuỗi.
"""
from __future__ import annotations

import re

# Lệnh toán phổ biến -> cách đọc tiếng Việt. Chỉ phủ cái DeepSeek hay trả; cái
# lạ rơi vào bước dọn backslash cuối (loại lệnh, giữ nội dung an toàn).
_OPERATORS = {
    r"\times": " nhân ",
    r"\cdot": " nhân ",
    r"\div": " chia ",
    r"\pm": " cộng trừ ",
    r"\mp": " trừ cộng ",
    r"\le": " nhỏ hơn hoặc bằng ",
    r"\leq": " nhỏ hơn hoặc bằng ",
    r"\ge": " lớn hơn hoặc bằng ",
    r"\geq": " lớn hơn hoặc bằng ",
    r"\neq": " khác ",
    r"\ne": " khác ",
    r"\approx": " xấp xỉ ",
    r"\to": " đến ",
    r"\rightarrow": " đến ",
    r"\infty": " vô cùng ",
    r"\%": " phần trăm ",
}

_FRAC_RE = re.compile(r"\\(?:d|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SQRT_RE = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_SUP_BRACE_RE = re.compile(r"\^\s*\{([^{}]*)\}")
_SUP_SIMPLE_RE = re.compile(r"\^\s*([0-9A-Za-z]+)")
_SUB_BRACE_RE = re.compile(r"_\s*\{([^{}]*)\}")
_SUB_SIMPLE_RE = re.compile(r"_\s*([0-9A-Za-z]+)")
# Lệnh LaTeX còn sót bất kỳ: \command — bỏ lệnh, giữ phần sau.
_LATEX_CMD_RE = re.compile(r"\\[A-Za-z]+\*?")
# Lệnh ký tự đơn như \, \! \; (khoảng cách) hoặc \{ \} \$ đã escape.
_LATEX_ESC_RE = re.compile(r"\\([{}$%&_#])")
_LATEX_SPACE_CMD_RE = re.compile(r"\\[,;:!> ]")


def _unwrap_math_delimiters(text: str) -> str:
    """Bỏ cặp delimiter math, giữ nội dung bên trong để xử lý tiếp."""
    # Block/inline \[...\] \(...\)
    text = re.sub(r"\\\[(.+?)\\\]", r" \1 ", text, flags=re.DOTALL)
    text = re.sub(r"\\\((.+?)\\\)", r" \1 ", text, flags=re.DOTALL)
    # $$...$$ trước $...$
    text = re.sub(r"\$\$(.+?)\$\$", r" \1 ", text, flags=re.DOTALL)
    text = re.sub(r"\$(.+?)\$", r" \1 ", text, flags=re.DOTALL)
    return text


def _convert_math_commands(text: str) -> str:
    """Chuyển các lệnh toán thường gặp thành lời đọc. Lặp để phủ frac lồng."""
    for _ in range(4):
        new = _FRAC_RE.sub(r" \1 trên \2 ", text)
        if new == text:
            break
        text = new
    text = _SQRT_RE.sub(r" căn \1 ", text)
    text = _SUP_BRACE_RE.sub(r" mũ \1 ", text)
    text = _SUP_SIMPLE_RE.sub(r" mũ \1 ", text)
    text = _SUB_BRACE_RE.sub(r" \1 ", text)
    text = _SUB_SIMPLE_RE.sub(r" \1 ", text)
    for cmd, word in _OPERATORS.items():
        text = text.replace(cmd, word)
    return text


def _strip_markdown(text: str) -> str:
    """Bỏ markup markdown để không đọc dấu ``# * | ` `` thành ký hiệu."""
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        # Bỏ dòng kẻ bảng markdown |---|---|
        if re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", stripped):
            continue
        # Bảng markdown: đổi | thành ", " để đọc tuyến tính
        if "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
            line = ", ".join(cells)
        # Heading: bỏ dấu # đầu dòng
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        # Bullet/số thứ tự đầu dòng: giữ nội dung
        line = re.sub(r"^\s{0,3}(?:[-*+]|\d+\.)\s+", "", line)
        lines.append(line)
    text = "\n".join(lines)
    # bold/italic/strike
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    # inline code `x` và link [text](url) -> text
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return text


def _final_safety_scrub(text: str) -> str:
    """Lưới cuối: không để ký hiệu nguy hiểm nào lọt vào TTS."""
    text = _LATEX_ESC_RE.sub(r"\1", text)
    text = _LATEX_SPACE_CMD_RE.sub(" ", text)
    # Lệnh LaTeX còn sót -> bỏ hẳn (giữ nội dung an toàn xung quanh)
    text = _LATEX_CMD_RE.sub(" ", text)
    # Loại các ký tự cấu trúc còn lại mà TTS sẽ đọc thành ký hiệu.
    text = text.translate({ord(c): " " for c in "\\{}$|#`^~"})
    # Gộp khoảng trắng, dọn khoảng trước dấu câu.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_CODE_BLOCK_RE = re.compile(r"(?:```[\w.+-]*\n?[\s\S]*?(?:```|\Z)|~~~[\w.+-]*\n?[\s\S]*?(?:~~~|\Z))", re.S)


def strip_math_for_speech(text: str) -> str:
    """Chuyển LaTeX/markdown trong ``text`` thành văn bản TTS đọc được.

    Dùng cho nhánh giọng đọc (``speech_text``). KHÔNG dùng cho text hiển thị
    (bubble cần giữ LaTeX để MathJax vẽ). An toàn với chuỗi rỗng/None.
    """
    if not text:
        return ""
    cleaned = _CODE_BLOCK_RE.sub("", str(text))
    out = _unwrap_math_delimiters(cleaned)
    out = _convert_math_commands(out)
    out = _strip_markdown(out)
    out = _final_safety_scrub(out)
    return out

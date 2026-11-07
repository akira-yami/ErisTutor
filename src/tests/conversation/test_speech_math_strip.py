# -*- coding: utf-8 -*-
"""strip_math_for_speech: LaTeX/markdown -> văn bản TTS đọc được, không ký hiệu."""
import re

import pytest

from runtime.conversation.speech_math_strip import strip_math_for_speech

# Mẫu LaTeX/markdown DeepSeek Web thực sự trả (theo deepseek-latex-probe.md).
_DEEPSEEK_SAMPLES = [
    r"Ta có \(\frac{3}{4} + \frac{5}{6} = \frac{19}{12}\), vậy kết quả lớn hơn 1.",
    r"Công thức: $\frac{a}{b} + \frac{c}{d}$ và block \[ \frac{19}{12} \] là kết quả.",
    "Để cộng hai phân số \\(\\frac{3}{4} + \\frac{5}{6}\\), ta thực hiện:\n"
    "\\[\n\\frac{3}{4} = \\frac{3 \\times 3}{4 \\times 3} = \\frac{9}{12}\n\\]",
    "# Học phân số\n## Bước 1\n**MSCNN** của 4 và 6 là **12**.\n"
    "| Phép tính | Quy tắc |\n|-----------|---------|\n| Cộng | Quy đồng mẫu |",
]

# Ký hiệu KHÔNG được phép còn lại trong transcript TTS (sẽ bị đọc thành ký tự).
_DANGEROUS = ["\\", "{", "}", "$", "|", "#", "`", "^", "~"]


@pytest.mark.parametrize("sample", _DEEPSEEK_SAMPLES)
def test_no_dangerous_symbols_leak_to_tts(sample):
    out = strip_math_for_speech(sample)
    for ch in _DANGEROUS:
        assert ch not in out, f"ký hiệu {ch!r} lọt vào TTS: {out!r}"
    # Không còn delimiter math dạng ghép đôi.
    assert "\\[" not in out and "\\]" not in out
    assert "\\(" not in out and "\\)" not in out


def test_frac_reads_as_tren():
    out = strip_math_for_speech(r"\(\frac{3}{4}\)")
    assert "3 trên 4" in out
    assert "frac" not in out


def test_result_fraction_preserved_readably():
    out = strip_math_for_speech(r"kết quả là \[ \frac{19}{12} \]")
    assert "19 trên 12" in out
    assert "\\" not in out


def test_operators_become_words():
    out = strip_math_for_speech(r"$3 \times 4 \div 2$")
    assert "nhân" in out
    assert "chia" in out
    assert "times" not in out and "div" not in out


def test_markdown_table_and_heading_cleaned():
    src = "# Tiêu đề\n**đậm** và `code`\n| A | B |\n|---|---|\n| 1 | 2 |"
    out = strip_math_for_speech(src)
    for ch in _DANGEROUS:
        assert ch not in out
    assert "Tiêu đề" in out
    assert "đậm" in out and "code" in out


def test_unknown_latex_command_failsafe_drops_symbol():
    # Lệnh lạ không nằm trong bảng -> fail-safe: bỏ ký hiệu, không giữ backslash.
    out = strip_math_for_speech(r"\(x \oplus y \unknowncmd{z}\)")
    assert "\\" not in out
    assert "{" not in out and "}" not in out
    # Nội dung an toàn vẫn còn.
    assert "x" in out and "y" in out and "z" in out


def test_empty_and_none_safe():
    assert strip_math_for_speech("") == ""
    assert strip_math_for_speech(None) == ""


def test_plain_vietnamese_unchanged_meaningfully():
    src = "Chào bạn, hôm nay học phân số nhé."
    out = strip_math_for_speech(src)
    assert "Chào bạn" in out
    assert "phân số" in out


def test_no_orphan_whitespace_before_punctuation():
    out = strip_math_for_speech(r"Kết quả \(\frac{1}{2}\) , xong.")
    assert " ," not in out
    assert not re.search(r"\s{2,}", out)

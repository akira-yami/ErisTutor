from __future__ import annotations

"""Bóc lớp reasoning quanh JSON mà không biến prose bất kỳ thành JSON hợp lệ.

Một số model thinking, đặc biệt Qwen, vẫn nhét ``<think>...</think>`` vào trường
``content`` dù request đã yêu cầu ``json_object``. Tầng format gate của Eris chạy
trước parser cuối, nên nếu không bóc lớp này ngay tại biên LLM thì hệ thống sẽ
retry vô ích với đúng một payload JSON vốn đã hợp lệ.
"""

import json
import re
from typing import Iterator

_REASONING_BLOCK_RE = re.compile(
    r"<\s*(think|analysis|reasoning)\s*>.*?<\s*/\s*\1\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_REASONING_OPEN_RE = re.compile(r"<\s*(think|analysis|reasoning)\s*>", re.IGNORECASE)
_REASONING_CLOSE_RE = re.compile(r"<\s*/\s*(think|analysis|reasoning)\s*>", re.IGNORECASE)
_FENCE_OPEN_RE = re.compile(r"^\s*```(?:json|javascript|js)?\s*", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"\s*```\s*$", re.IGNORECASE)


def iter_json_objects(text: str) -> Iterator[str]:
    """Yield các object JSON cân bằng, bỏ qua ngoặc nằm trong string."""

    raw = str(text or "")
    start = -1
    depth = 0
    in_string = False
    escaped = False

    for idx, ch in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
            continue
        if ch == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                yield raw[start : idx + 1]
                start = -1


def extract_last_valid_json_object(text: str) -> str:
    """Lấy object JSON hợp lệ cuối cùng trong output model.

    Model reasoning đôi khi tự viết ví dụ JSON trong ``<think>`` rồi mới trả
    contract thật. Lấy object đầu tiên sẽ chọn nhầm ví dụ; lấy object cuối cùng
    khớp với quy ước "reasoning trước, answer sau" của Qwen.
    """

    last = ""
    for candidate in iter_json_objects(text):
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            last = candidate
    return last


# Alias nội bộ cũ để không làm vỡ code đã import private helper trong plugin.
_last_valid_json_object = extract_last_valid_json_object


def strip_reasoning_envelope(text: str) -> str:
    """Trả nội dung public sau khi bỏ reasoning envelope nhận diện được.

    Hàm chỉ tự rút object JSON khi có dấu hiệu rõ ràng của reasoning/fence. Với
    prose tùy ý trước JSON, strict gate vẫn chặn như cũ để không vô tình hợp thức
    hóa prompt leak. Trường hợp ``<think>`` bị quên đóng vẫn được cứu bằng object
    JSON hợp lệ cuối cùng, vì model thinking đôi lúc rất sáng tạo ở khoản quên cửa.
    """

    raw = str(text or "").lstrip("\ufeff").strip()
    if not raw:
        return ""

    had_reasoning_marker = bool(_REASONING_OPEN_RE.search(raw) or _REASONING_CLOSE_RE.search(raw))
    had_fence = bool(_FENCE_OPEN_RE.search(raw) or _FENCE_CLOSE_RE.search(raw))

    cleaned = _REASONING_BLOCK_RE.sub("\n", raw).strip()

    # Nếu còn opening tag không có closing tag, không giữ phần suy nghĩ. Rút object
    # JSON hợp lệ cuối cùng thay vì object đầu tiên, phòng reasoning có ví dụ {..}.
    if _REASONING_OPEN_RE.search(cleaned):
        candidate = _last_valid_json_object(cleaned)
        if candidate:
            return candidate.strip()

    # Dọn tag đóng lẻ và code fence bao ngoài.
    cleaned = _REASONING_CLOSE_RE.sub("\n", cleaned).strip()
    cleaned = _FENCE_OPEN_RE.sub("", cleaned)
    cleaned = _FENCE_CLOSE_RE.sub("", cleaned).strip()

    if had_reasoning_marker or had_fence:
        candidate = _last_valid_json_object(cleaned)
        if candidate:
            return candidate.strip()

    return cleaned


__all__ = ["extract_last_valid_json_object", "iter_json_objects", "strip_reasoning_envelope"]

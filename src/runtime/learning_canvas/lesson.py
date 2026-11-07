"""P4 — bài học theo yêu cầu, block có cấu trúc; không dùng để chấm mastery.

Gemini chỉ trả JSON ngắn. Python kiểm tra schema trước khi lưu; Web dựng giao diện
bằng thành phần cố định, tuyệt đối không thực thi HTML/JS do mô hình cung cấp.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import threading
import time
from typing import Any

from .model_response import read_model_object

LOGGER = logging.getLogger(__name__)
MAX_BLOCKS = 6
MAX_LESSON_BYTES = 10000
LANGUAGES = {"python", "java", "javascript", "typescript", "cpp", "c", "csharp", "json", "sql", "bash", "text"}
KINDS = {"text", "steps", "formula", "code", "check"}

# P4: prompt chỉ mô tả giao thức. Không gửi mastery, lịch sử riêng hay tin nhắn chat.
SHORT_PROMPT = ("Soạn bài học ngắn, rõ, tiếng Việt. Chỉ JSON: "
    '{"blocks":[{"kind":"text","title":"...","text":"..."},'
    '{"kind":"steps","title":"...","items":["..."]},'
    '{"kind":"formula","title":"...","latex":"...","explanation":"..."},'
    '{"kind":"code","title":"...","language":"python","text":"..."},'
    '{"kind":"check","title":"...","question":"...","answer":"..."}]}. '
    "Chọn 2-5 block phù hợp chủ đề, không cần đủ loại; mỗi đoạn thật gọn. "
    "Không HTML, URL bịa, tài liệu giả, không tuyên bố người học đã hiểu. "
    "Mọi nội dung là bài học nháp, không phải kết quả chấm điểm.")


def _string(value: Any, limit: int, *, allow_empty=False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        raise ValueError("invalid_lesson_block")
    if "\x00" in value:
        raise ValueError("invalid_lesson_block")
    return value.strip()


def validate_lesson(raw: dict, *, group_id: str, node_id: str, title: str) -> dict:
    """Không nhận tùy ý JSON/HTML/code executable; chỉ whitelist block render."""
    if not isinstance(raw, dict) or not isinstance(raw.get("blocks"), list) or not 1 <= len(raw["blocks"]) <= MAX_BLOCKS:
        raise ValueError("invalid_lesson_blocks")
    out = []
    for item in raw["blocks"]:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            raise ValueError("invalid_lesson_block")
        kind = item["kind"]
        block = {"kind": kind, "title": _string(item.get("title", ""), 100)}
        if kind == "text":
            block["text"] = _string(item.get("text"), 1600)
        elif kind == "steps":
            entries = item.get("items")
            if not isinstance(entries, list) or not 2 <= len(entries) <= 6:
                raise ValueError("invalid_lesson_block")
            block["items"] = [_string(value, 320) for value in entries]
        elif kind == "formula":
            latex = _string(item.get("latex"), 480)
            # Chặn các lệnh ngoại vi/HTML; MathJax chỉ dùng để hiển thị công thức.
            if any(token in latex.lower() for token in (r"\href", r"\url", r"\html", r"\require", r"\style", r"\class", r"\unicode")):
                raise ValueError("invalid_lesson_formula")
            block["latex"] = latex
            block["explanation"] = _string(item.get("explanation", ""), 600, allow_empty=True)
        elif kind == "code":
            language = _string(item.get("language", "text"), 24).lower()
            block["language"] = language if language in LANGUAGES else "text"
            _string(item.get("text"), 2400)
            block["text"] = item["text"]
        elif kind == "check":
            block["question"] = _string(item.get("question"), 700)
            block["answer"] = _string(item.get("answer"), 900)
        out.append(block)
    result = {"schema": "eris.learning.lesson.p4", "group_id": group_id,
              "node_id": node_id, "title": _string(title, 120), "blocks": out,
              "verified": False, "generated_at": round(time.time(), 3)}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_LESSON_BYTES:
        raise ValueError("lesson_payload_limit")
    return result


class LearningLessonStore:
    """Mỗi bài một key SQLite metadata; không nạp cả kho khi Root đang chat."""
    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()

    @staticmethod
    def _key(group_id: str, node_id: str) -> str:
        if not isinstance(group_id, str) or not isinstance(node_id, str) or not group_id or not node_id:
            raise ValueError("invalid_lesson_target")
        suffix = hashlib.sha256((group_id + "\x00" + node_id).encode("utf-8")).hexdigest()
        return "learning_canvas_lesson_p4_" + suffix

    def get(self, group_id: str, node_id: str) -> dict | None:
        with self._lock:
            data = self.store.get_meta(self._key(group_id, node_id), None)
            if isinstance(data, dict) and data.get("schema") == "eris.learning.lesson.p4" and data.get("group_id") == group_id and data.get("node_id") == node_id:
                return copy.deepcopy(data)
            return None

    def put(self, group_id: str, node_id: str, title: str, raw: dict) -> dict:
        data = validate_lesson(raw, group_id=group_id, node_id=node_id, title=title)
        with self._lock:
            existing = self.get(group_id, node_id)
            if existing is not None:
                return existing
            self.store.set_meta(self._key(group_id, node_id), data)
            return data


class OnDemandLessonWorker:
    """Một worker ngắn, tác vụ mới nhất; panel đóng không khởi chạy request mới.

    Kết quả trả về sau khi đổi môn chỉ vào cache đúng key, không đổi graph focus.
    """
    def __init__(self, store: LearningLessonStore, graphs, *, may_run, on_change=None, adapter=None):
        self.store, self.graphs = store, graphs
        self.may_run, self.on_change = may_run, on_change
        self._adapter = adapter
        self._lock = threading.RLock()
        self._pending = None
        self._active = None
        self._thread = None
        self._closed = False
        self._failures = {}  # monotonic retry cooldown; không spam Gemini mỗi poll.

    def state(self, group_id: str, node_id: str) -> str:
        if self.store.get(group_id, node_id):
            return "ready"
        with self._lock:
            if (self._pending and self._pending[:2] == (group_id, node_id)) or self._active == (group_id, node_id):
                return "preparing"
            if self._failures.get((group_id, node_id), 0) > time.monotonic():
                return "unavailable"
        return "idle"

    def request(self, group_id: str, node_id: str, *, retry=False) -> bool:
        # Không tạo bài cho Java pack cũ. Chỉ chạy khi một panel có lease hợp lệ.
        if not self.may_run() or self.store.get(group_id, node_id):
            return False
        self.graphs.inspect(group_id, node_id)  # kiểm tra target tồn tại.
        with self._lock:
            if self._closed or (not retry and self._failures.get((group_id, node_id), 0) > time.monotonic()):
                return False
            if self._active == (group_id, node_id) or (self._pending and self._pending[:2] == (group_id, node_id)):
                return False
            self._pending = (group_id, node_id)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._drain, daemon=True, name="eris-learning-lesson-p4")
                self._thread.start()
        self._emit()
        return True

    def _emit(self):
        if not self._closed and self.may_run() and callable(self.on_change):
            try:
                self.on_change()
            except Exception:
                LOGGER.debug("Lesson UI notify unavailable", exc_info=True)

    @staticmethod
    def _make_adapter():
        if os.getenv("ERIS_LEARNING_LESSON_GEMINI_ENABLED", "1").lower() in {"0", "false", "off", "no"}:
            return None
        from memory.living_user.gemini_adapter import GeminiWebUserProfileAdapter
        return GeminiWebUserProfileAdapter.create_if_available(timeout=18.0)

    def _call(self, context: dict) -> dict:
        adapter = self._adapter if self._adapter is not None else self._make_adapter()
        if adapter is None:
            raise RuntimeError("gemini_disabled")
        query = {"topic": context["topic"], "trail": context["trail"][-4:]}
        if context.get("node_summary"):
            query["hint"] = context["node_summary"]
        response = adapter.create_chat_completion(
            messages=[{"role": "system", "content": SHORT_PROMPT},
                      {"role": "user", "content": json.dumps(query, ensure_ascii=False)}],
            max_tokens=1850, timeout=18.0)
        data = read_model_object(response)
        if not isinstance(data, dict):
            raise ValueError("invalid_lesson_blocks")
        return data

    def _drain(self):
        while True:
            with self._lock:
                if self._closed or self._pending is None:
                    self._active = None
                    self._thread = None
                    return
                key = self._pending
                self._pending = None
                self._active = key
            try:
                if self.may_run() and self.store.get(*key) is None:
                    context = self.graphs.inspect(*key)
                    draft = self._call(context)
                    if not self._closed:
                        # Cache đúng key ngay cả khi người học đã chuyển nhánh.
                        self.store.put(*key, context["trail"][-1], draft)
                        self._failures.pop(key, None)
            except Exception as exc:
                with self._lock:
                    self._failures[key] = time.monotonic() + 75
                LOGGER.debug("[learning-lesson] Gemini unavailable: %s", type(exc).__name__)
            finally:
                with self._lock:
                    if self._active == key:
                        self._active = None
                self._emit()

    def close(self):
        with self._lock:
            self._closed = True
            self._pending = None

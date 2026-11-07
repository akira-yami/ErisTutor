"""P3.5: Gemini chuẩn bị nhánh theo sự khám phá, không tạo agent loop mới.

Worker chỉ khởi chạy do người dùng/Root chọn nhánh. Một Gemini request tại một
thời điểm; yêu cầu mới thay yêu cầu đang đợi, kết quả cũ không ghi đè UI.
Không gửi màn hình, tin nhắn riêng, mastery, dữ liệu cá nhân tới Gemini.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from .graph import GraphWorkspace, MAX_VISIBLE_SUGGESTIONS

LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = """Bạn là Gemini, người dựng nháp các nhánh kiến thức cho Eris Learning.
Trả về DUY NHẤT JSON hợp lệ, không markdown hoặc văn bản ngoài JSON.
Đây là bản đồ khám phá chưa kiểm chứng, không phải đánh giá trình độ hay bài giảng.
Chỉ đề xuất vài khái niệm thật sự liên quan với ngữ cảnh học và tầng hiện tại.
Không tạo các nhánh trùng nhau; tên ngắn (<=120 ký tự), mô tả <=240 ký tự.
Không bịa nguồn, URL, trích dẫn, thông tin về người dùng hay mức mastery.
Nếu input.mode="expand": xuất {"branches":[{"title":"...","summary":"...","next":[{"title":"...","summary":"..."}]}]}.
Có 2-4 branches, mỗi next 1-3 gợi ý con; không tạo thêm tầng thứ ba.
Nếu input.mode="prepare": xuất {"targets":[{"id":"ID trong input","next":[{"title":"...","summary":"..."}]}]}.
Chỉ dùng id nguyên xi được cung cấp, tối đa 4 targets, 1-3 next cho mỗi target.
"""


class ProgressiveGraphPrefetch:
    """Công việc ngắn, coalescing; không độc lập quyết định việc học/Writer."""

    def __init__(self, graphs: GraphWorkspace, *, adapter: object | None = None, on_change=None, on_result=None, may_run=None):
        self.graphs = graphs
        self._adapter = adapter
        self.on_change = on_change
        self.on_result = on_result  # P3.6: kết thúc job -> host event queue, không trực tiếp nói.
        self.may_run = may_run  # P3.7: worker không được gọi Gemini khi panel đóng.
        self._lock = threading.RLock()
        self._pending: tuple[str, str, int, str] | None = None
        self._active: tuple[str, str, int, str] | None = None
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._closed = False
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._failed: dict[tuple[str, str], float] = {}

    @staticmethod
    def _make_adapter():
        if os.getenv("ERIS_LEARNING_GRAPH_GEMINI_ENABLED", "1").lower() in {"0", "false", "off", "no"}:
            return None
        from memory.living_user.gemini_adapter import GeminiWebUserProfileAdapter
        # Tái sử dụng Gemini Web service/lane đang có, không mở cookie/client khác.
        return GeminiWebUserProfileAdapter.create_if_available(timeout=16.0)

    def request(self, group_id: str, node_id: str, *, job_id: str = "") -> bool:
        """Chỉ xếp một yêu cầu mới nhất; không khóa HTTP/UI."""
        key = (group_id, node_id)
        with self._lock:
            if (self._closed or (callable(self.may_run) and not self.may_run())
                    or self._cooldowns.get(key, 0) > time.monotonic()):
                return False
            if self._pending and self._pending[:2] == key:
                return False
            if self._active and self._active[:2] == key:
                return False
            self._generation += 1
            displaced = self._pending
            self._pending = (group_id, node_id, self._generation, job_id)
            if displaced and displaced[3]:
                self._notify(displaced[0], displaced[1], displaced[3], "superseded")
            self._failed.pop(key, None)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._drain, daemon=True,
                                                name="eris-learning-gemini-prefetch")
                self._thread.start()
            return True

    def state(self, group_id: str, node_id: str) -> str:
        key = (group_id, node_id)
        with self._lock:
            if self._pending and self._pending[:2] == key:
                return "preparing"
            if self._active and self._active[:2] == key:
                return "preparing"
            if key in self._failed:
                return "unavailable"
            return "idle"

    def _notify(self, group_id: str, node_id: str, job_id: str, status: str):
        if job_id and callable(self.on_result):
            try:
                self.on_result({"group_id": group_id, "node_id": node_id,
                                "job_id": job_id, "status": status})
            except Exception:
                LOGGER.debug("Learning job event unavailable", exc_info=True)

    def _emit(self) -> None:
        if not self._closed and callable(self.on_change):
            try:
                self.on_change()
            except Exception:
                LOGGER.debug("Learning graph publish unavailable", exc_info=True)

    def _call(self, query: dict) -> dict:
        adapter = self._adapter if self._adapter is not None else self._make_adapter()
        if adapter is None:
            raise RuntimeError("gemini_disabled")
        response = adapter.create_chat_completion(messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(query, ensure_ascii=False)},
        ], max_tokens=1300, timeout=16.0)
        from memory.living_user.runtime_adapter import _extract_text, _parse_json
        result = _parse_json(_extract_text(response))
        if not isinstance(result, dict):
            raise ValueError("invalid_gemini_graph")
        return result

    def _drain(self) -> None:
        while True:
            with self._lock:
                if self._closed or self._pending is None:
                    self._active = None
                    self._thread = None
                    return
                group_id, node_id, generation, job_id = self._pending
                self._pending = None
                self._active = (group_id, node_id, generation, job_id)
            self._emit()
            key = (group_id, node_id)
            outcome = "unchanged"
            try:
                context = self.graphs.inspect(group_id, node_id)
                if context["children"]:
                    targets = context["needs_prefetch"][:MAX_VISIBLE_SUGGESTIONS]
                    if not targets:
                        # Đã có cache/tầng tiếp theo: không sinh thừa.
                        outcome = "ready"
                        continue
                    query = {"mode": "prepare", "topic": context["topic"],
                             "trail": context["trail"], "targets": targets}
                else:
                    query = {"mode": "expand", "topic": context["topic"],
                             "trail": context["trail"]}
                if callable(self.may_run) and not self.may_run():
                    outcome = "superseded"
                    continue
                result = self._call(query)
                with self._lock:
                    current = not self._closed and generation == self._generation
                if current:
                    changed = self.graphs.apply_gemini(group_id, node_id, result)
                    outcome = "ready" if changed or self.graphs.inspect(group_id, node_id)["children"] else "unchanged"
                    with self._lock:
                        self._cooldowns[key] = time.monotonic() + (45.0 if changed else 20.0)
                else:
                    outcome = "superseded"
            except Exception as exc:
                outcome = "unavailable"
                with self._lock:
                    if generation == self._generation and not self._closed:
                        self._failed[key] = time.monotonic()
                        self._cooldowns[key] = time.monotonic() + 90.0
                LOGGER.debug("[learning-prefetch] Gemini tạm không khả dụng: %s", type(exc).__name__)
            finally:
                with self._lock:
                    if self._closed or generation != self._generation:
                        outcome = "superseded"
                    if self._active and self._active[2] == generation:
                        self._active = None
                self._notify(group_id, node_id, job_id, outcome)
                self._emit()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._generation += 1
            self._pending = None
        # HTTP call đang chạy chỉ hoàn thành rồi bỏ kết quả; không giữ shutdown.

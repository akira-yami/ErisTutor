# -*- coding: utf-8 -*-
"""Background Vision Perception Worker for Eris.

Runs in parallel with the main cognition loop:
- Detects screen and window changes (using change detector & active window provider).
- When a meaningful change occurs, captures the screen and sends it asynchronously to
  the Gemini Vision Docker API (http://localhost:8000/api/vision/decompose).
- Stores the latest VisionPerceptionSnapshot in a thread-safe singleton slot.
- Non-blocking: RootAgent and SurfaceWriter read from the slot in 0ms without waiting.
- Fail-soft: If Docker is down or network fails, logs quietly and continues without affecting chat.
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from PIL import Image
from native.screen_capture import capture_screen
from runtime.privacy.environment_policy import EnvironmentPolicy, PrivacyScope

from .active_window_provider import ActiveWindowProvider
from .change_detector import ScreenChangeDetector
from .evidence_models import ActiveWindowInfo

_LOG = logging.getLogger("Eris.VisionPerception")


@dataclass(frozen=True, slots=True)
class VisionPerceptionSnapshot:
    """Ảnh chụp nhận thức thị giác nền từ Gemini Web."""

    summary: str = ""
    topic: str = ""
    inferred_story: str = ""
    mood_and_intent: str = ""
    key_elements: tuple[dict[str, str], ...] = ()
    detected_text: tuple[str, ...] = ()
    text_context: str = ""
    tags: tuple[str, ...] = ()
    window_title: str = ""
    process_name: str = ""
    captured_at: float = field(default_factory=time.time)
    raw_json: dict[str, Any] = field(default_factory=dict)

    def is_stale(self, max_age_seconds: float = 600.0) -> bool:
        """Kiểm tra xem snapshot thị giác có quá cũ không."""
        return (time.time() - self.captured_at) > max_age_seconds

    def prompt_context(self) -> str:
        """Định dạng context ngắn gọn, giàu ý nghĩa để chèn vào Grounding của Root/Writer."""
        if not self.summary and not self.inferred_story:
            return ""

        age_sec = int(time.time() - self.captured_at)
        time_hint = f"vừa cập nhật {age_sec}s trước" if age_sec < 60 else f"cách đây {age_sec // 60} phút"

        lines = [
            f"[THỊ GIÁC MÀN HÌNH ({time_hint})]",
            f"- Cửa sổ: {self.window_title or '<không xác định>'}",
            f"- Bối cảnh: {self.summary}",
        ]
        if self.inferred_story:
            lines.append(f"- Phỏng đoán câu chuyện: {self.inferred_story}")
        if self.mood_and_intent:
            lines.append(f"- Không khí & Ý đồ: {self.mood_and_intent}")
        if self.key_elements:
            elem_strs = [
                f"{e.get('name')}: {e.get('significance')}"
                for e in self.key_elements[:4]
                if isinstance(e, Mapping) and e.get("name")
            ]
            if elem_strs:
                lines.append(f"- Điểm nhấn: {' | '.join(elem_strs)}")
        if self.detected_text:
            lines.append(f"- Chữ trên màn hình: {', '.join(str(t) for t in self.detected_text[:5])}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "topic": self.topic,
            "inferred_story": self.inferred_story,
            "mood_and_intent": self.mood_and_intent,
            "key_elements": list(self.key_elements),
            "detected_text": list(self.detected_text),
            "text_context": self.text_context,
            "tags": list(self.tags),
            "window_title": self.window_title,
            "process_name": self.process_name,
            "captured_at": self.captured_at,
            "age_seconds": round(time.time() - self.captured_at, 1),
        }


class VisionPerceptionService:
    """Thread-safe singleton lưu trữ snapshot thị giác nền."""

    def __init__(self) -> None:
        self._latest_snapshot: VisionPerceptionSnapshot | None = None
        self._lock = threading.RLock()

    def get_latest_snapshot(self) -> VisionPerceptionSnapshot | None:
        """Đọc tức thì (0ms) từ RAM mà không bao giờ block luồng chat."""
        with self._lock:
            return self._latest_snapshot

    def publish_snapshot(self, snapshot: VisionPerceptionSnapshot) -> None:
        """Cập nhật nguyên tử snapshot mới từ worker chạy ngầm."""
        with self._lock:
            self._latest_snapshot = snapshot


_GLOBAL_SERVICE: VisionPerceptionService | None = None
_GLOBAL_SERVICE_LOCK = threading.Lock()


def ensure_vision_perception_service() -> VisionPerceptionService:
    """Truy xuất singleton service của Vision Perception."""
    global _GLOBAL_SERVICE
    if _GLOBAL_SERVICE is None:
        with _GLOBAL_SERVICE_LOCK:
            if _GLOBAL_SERVICE is None:
                _GLOBAL_SERVICE = VisionPerceptionService()
    return _GLOBAL_SERVICE


class VisionPerceptionWorker:
    """Worker chạy ngầm thăm dò màn hình và gửi phân tích lên Gemini Vision Docker."""

    def __init__(
        self,
        service: VisionPerceptionService | None = None,
        *,
        api_url: str | None = None,
        poll_interval: float = 6.0,
        cooldown: float = 12.0,
        diff_threshold: float = 0.05,
    ) -> None:
        self.service = service or ensure_vision_perception_service()
        self.api_url = api_url or os.getenv("ERIS_VISION_API_URL", "http://127.0.0.1:8000/api/vision/decompose")
        self.poll_interval = max(2.0, float(os.getenv("ERIS_VISION_POLL_SEC", str(poll_interval))))
        self.cooldown = max(5.0, float(os.getenv("ERIS_VISION_COOLDOWN_SEC", str(cooldown))))
        self.diff_threshold = max(0.01, float(os.getenv("ERIS_VISION_DIFF_THRESHOLD", str(diff_threshold))))

        self._window_provider = ActiveWindowProvider()
        self._change_detector = ScreenChangeDetector(threshold=self.diff_threshold)
        self._last_sent_at: float = 0.0
        self._last_window_title: str = ""
        self._last_active_time: float = time.time()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Khởi động worker chạy ngầm trong thread daemon độc lập."""
        enabled = os.getenv("ERIS_VISION_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
        if not enabled:
            _LOG.info("VisionPerceptionWorker disabled by ERIS_VISION_ENABLED=0")
            return

        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="ErisVisionPerceptionWorker",
            daemon=True,
        )
        self._thread.start()
        _LOG.info("VisionPerceptionWorker started (poll=%.1fs, cooldown=%.1fs, api=%s)",
                  self.poll_interval, self.cooldown, self.api_url)

    def stop(self) -> None:
        """Dừng worker an toàn."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None

    def _capture_screen(self, window: ActiveWindowInfo) -> Image.Image | None:
        if not window.bounds or min(window.bounds[2:]) <= 50:
            return None
        privacy = EnvironmentPolicy().scope_for(process_name=window.process_name, window_title=window.title)
        if privacy.scope < PrivacyScope.SEMANTIC:
            return None
        try:
            return capture_screen(window.bounds)
        except Exception as exc:
            _LOG.debug("Vision capture failed: %s", exc)
            return None

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                _LOG.debug("Error in vision worker tick: %s", exc)

            self._stop_event.wait(self.poll_interval)

    def _tick(self) -> None:
        now = time.time()
        window_info = self._window_provider.get()
        title = window_info.title or ""
        window_changed = title != self._last_window_title

        # Chụp màn hình để kiểm tra thay đổi pixel
        image = self._capture_screen(window_info)
        if image is None:
            return

        # Kiểm tra độ biến đổi (change detector)
        change = self._change_detector.inspect(image, key=title)

        # Nếu cửa sổ không đổi VÀ pixel không đổi -> BỎ QUA HOÀN TOÀN!
        if not window_changed and not change.changed:
            return

        # Màn hình có thay đổi thật sự. Kiểm tra thời gian chờ cooldown
        if (now - self._last_sent_at) < self.cooldown:
            return

        # Cập nhật mốc cửa sổ
        self._last_window_title = title

        # Chuẩn bị ảnh nén JPEG nhẹ (chiều dài tối đa 1280px) để gửi Docker siêu tốc
        buffer = self._compress_image_for_upload(image)
        if not buffer:
            return

        # Gửi sang Docker API trong chế độ non-blocking
        self._send_to_gemini_docker(buffer, window_info=window_info)

    def _compress_image_for_upload(self, image: Image.Image) -> bytes | None:
        try:
            img = image
            max_dim = 1280
            if max(img.width, img.height) > max_dim:
                scale = max_dim / float(max(img.width, img.height))
                new_w = max(1, int(img.width * scale))
                new_h = max(1, int(img.height * scale))
                img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)

            out = io.BytesIO()
            img.save(out, format="JPEG", quality=82, optimize=True)
            return out.getvalue()
        except Exception as exc:
            _LOG.debug("Image compression error: %s", exc)
            return None

    def _send_to_gemini_docker(self, image_bytes: bytes, window_info: ActiveWindowInfo) -> None:
        import requests

        try:
            files = {"file": ("screenshot.jpg", image_bytes, "image/jpeg")}
            data = {"model": "gemini-flash", "thinking": "fast"}
            res = requests.post(self.api_url, files=files, data=data, timeout=25.0)

            if res.status_code == 200:
                payload = res.json()
                raw_data = payload.get("data", {})
                if isinstance(raw_data, Mapping):
                    inference = raw_data.get("inference", {}) or {}
                    ocr_data = raw_data.get("ocr_and_text", {}) or {}
                    elements = raw_data.get("key_elements", []) or []

                    snapshot = VisionPerceptionSnapshot(
                        summary=str(raw_data.get("summary", "") or "").strip(),
                        topic=str(inference.get("topic", "") or "").strip(),
                        inferred_story=str(inference.get("inferred_story", "") or "").strip(),
                        mood_and_intent=str(inference.get("mood_and_intent", "") or "").strip(),
                        key_elements=tuple(elements if isinstance(elements, list) else ()),
                        detected_text=tuple(ocr_data.get("detected_text", []) or ()),
                        text_context=str(ocr_data.get("text_context", "") or "").strip(),
                        tags=tuple(raw_data.get("tags", []) or ()),
                        window_title=window_info.title,
                        process_name=window_info.process_name,
                        captured_at=time.time(),
                        raw_json=dict(raw_data),
                    )

                    self.service.publish_snapshot(snapshot)
                    self._last_sent_at = time.time()
                    _LOG.info("[VisionPerception] Decomposed successfully: %s", snapshot.summary[:60])
            else:
                _LOG.debug("Vision Docker responded %s: %s", res.status_code, res.text[:120])
        except requests.exceptions.ConnectionError:
            # Docker chưa bật hoặc đang tắt: fail-soft, yên lặng bỏ qua
            _LOG.debug("Vision Docker not reachable at %s. Waiting...", self.api_url)
            self._last_sent_at = time.time() + 15.0  # Backoff tạm thời
        except Exception as exc:
            _LOG.debug("Failed to decompose vision: %s", exc)


__all__ = [
    "VisionPerceptionSnapshot",
    "VisionPerceptionService",
    "VisionPerceptionWorker",
    "ensure_vision_perception_service",
]

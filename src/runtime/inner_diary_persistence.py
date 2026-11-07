# -*- coding: utf-8 -*-
"""
runtime/inner_diary_persistence.py
==================================

Ghi `inner_diary` (và một ít ngữ cảnh từ private mind digest) ra đĩa dưới
`memory_core/inner_diary.json`.

Triết lý:
- Inner diary là trạng thái nội tâm sống. Trước đây nó chỉ được render rồi phát
  qua UDP cho UI, không lưu gì cả — tắt app là mất. File này vá lỗ đó.
- Ghi đĩa phải tiết chế: throttle theo thời gian và chỉ ghi khi nội dung đổi,
  để không biến ổ đĩa thành nạn nhân của một cái loop chạy mỗi vài giây.
- Không bao giờ được làm chết runtime. Mọi lỗi I/O đều nuốt êm.

File kết quả gồm:
    {
      "updated_at": <epoch>,
      "last_tick_at": <epoch>,
      "current": {... tóm tắt judgment hiện tại ...},
      "inner_diary": {... renderer output ...},
      "history": [ {"at": <epoch>, "text": "..."} , ... ]  # cap max_history
    }
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Mapping


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "none"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "") or default))
    except (TypeError, ValueError):
        return int(default)


class InnerDiaryPersister:
    """Lưu inner_diary ra đĩa, có throttle và chống ghi trùng."""

    def __init__(
        self,
        *,
        base_dir: str | None = None,
        filename: str = "inner_diary.json",
        throttle_seconds: float | None = None,
        max_history: int | None = None,
        enabled: bool | None = None,
        logger: Any = None,
    ) -> None:
        self.enabled = _env_flag("ERIS_INNER_DIARY_PERSIST", True) if enabled is None else bool(enabled)
        resolved_dir = base_dir or os.getenv("ERIS_MEMORY_CORE_DIR", "memory_core")
        self.path = os.path.join(resolved_dir, filename)
        self.throttle_seconds = (
            max(1.0, _env_float("ERIS_INNER_DIARY_PERSIST_SEC", 60.0))
            if throttle_seconds is None
            else max(0.0, float(throttle_seconds))
        )
        self.max_history = (
            max(0, _env_int("ERIS_INNER_DIARY_MAX_HISTORY", 200))
            if max_history is None
            else max(0, int(max_history))
        )
        self.logger = logger
        self._last_write: float = 0.0
        self._last_text: str | None = None
        self._history: list[dict[str, Any]] = []
        self._loaded = False

    # ------------------------------------------------------------------ public

    def maybe_persist(self, digest: Mapping[str, Any] | None, *, now: float | None = None) -> bool:
        """Ghi nếu tới hạn và nội dung đổi. Trả True nếu thực sự ghi."""

        if not self.enabled:
            return False
        if not isinstance(digest, Mapping):
            return False
        diary = digest.get("inner_diary")
        if not isinstance(diary, Mapping) or not diary:
            return False

        text = str(diary.get("text", "") or "").strip()
        if not text:
            return False

        now = time.time() if now is None else float(now)
        self._ensure_loaded()

        # Nội dung không đổi thì không ghi, kể cả khi đã hết throttle.
        if self._last_text is not None and text == self._last_text:
            return False
        # Có đổi nhưng chưa hết throttle window thì để dành.
        if now - self._last_write < self.throttle_seconds:
            return False

        try:
            self._write(digest, dict(diary), text, now)
        except Exception as exc:  # I/O không được phép giết runtime
            self._log("warning", "InnerDiary persist failed: %s", exc)
            return False

        self._last_write = now
        self._last_text = text
        return True

    # ----------------------------------------------------------------- internal

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if os.path.isfile(self.path):
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    prev = data.get("inner_diary")
                    if isinstance(prev, dict):
                        self._last_text = str(prev.get("text", "") or "").strip() or None
                    hist = data.get("history")
                    if isinstance(hist, list):
                        self._history = [h for h in hist if isinstance(h, dict)][-self.max_history :]
                    self._last_write = float(data.get("updated_at", 0.0) or 0.0)
        except Exception as exc:
            # File hỏng/không đọc được thì coi như chưa có; không chặn ghi mới.
            self._log("warning", "InnerDiary load failed, starting fresh: %s", exc)
            self._last_text = None
            self._history = []

    def _write(self, digest: Mapping[str, Any], diary: dict[str, Any], text: str, now: float) -> None:
        if self.max_history > 0:
            self._history.append({"at": round(now, 3), "text": text})
            if len(self._history) > self.max_history:
                self._history = self._history[-self.max_history :]

        current = digest.get("current")
        payload = {
            "updated_at": round(now, 3),
            "last_tick_at": round(float(digest.get("last_tick_at", 0.0) or 0.0), 3),
            "current": current if isinstance(current, Mapping) else {},
            "inner_diary": diary,
            "history": list(self._history),
        }

        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        # Ghi atomic: temp rồi replace, tránh để lại file json cụt nếu crash giữa chừng.
        fd, tmp_path = tempfile.mkstemp(prefix=".inner_diary_", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _log(self, level: str, msg: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            try:
                fn(msg, *args)
            except Exception:
                pass

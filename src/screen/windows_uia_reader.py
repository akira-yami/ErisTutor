from __future__ import annotations

"""Đọc text có cấu trúc từ Windows UI Automation.

UIA luôn được ưu tiên hơn OCR vì text sạch hơn và giữ được ranh giới control.
Các cửa sổ chat/mail nhạy cảm bị chặn mặc định.
"""

import hashlib
import os
import re
import time

from .evidence_models import ActiveWindowInfo, ScreenTextBlock
from native.uia_event_client import UIAEventClient

try:  # pragma: no cover - chỉ chạy trên Windows thật
    import uiautomation as auto  # type: ignore
except Exception:  # pragma: no cover
    auto = None  # type: ignore

_SENSITIVE = ("discord", "telegram", "zalo", "messenger", "gmail", "outlook", "slack", "teams", "meet", "zoom")


class WindowsUIAReader:
    def __init__(self) -> None:
        self.enabled = os.getenv("ERIS_SCREEN_UIA_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"}
        self.max_nodes = max(20, int(os.getenv("ERIS_SCREEN_UIA_MAX_NODES", "180") or 180))
        self.max_chars = max(300, int(os.getenv("ERIS_SCREEN_UIA_MAX_CHARS", "5000") or 5000))
        self.allow_sensitive = os.getenv("ERIS_SCREEN_TEXT_ALLOW_SENSITIVE", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.cache_refresh_sec = max(1.0, float(os.getenv("ERIS_UIA_RECONCILE_SEC", "30") or 30.0))
        self._client = UIAEventClient(max_nodes=self.max_nodes, max_depth=6)
        self._last_read_at: dict[int, float] = {}

    def is_sensitive(self, title: str) -> bool:
        lowered = str(title or "").lower()
        return any(token in lowered for token in _SENSITIVE)

    def invalidate(self, hwnd: int) -> None:
        self._client.invalidate(int(hwnd or 0))

    def read(self, window: ActiveWindowInfo) -> tuple[ScreenTextBlock, ...]:
        if not self.enabled or auto is None:
            return ()
        if self.is_sensitive(window.title) and not self.allow_sensitive:
            return ()
        try:
            root = auto.GetForegroundControl()
            now = time.time()
            hwnd = int(window.hwnd or 0)
            last = self._last_read_at.get(hwnd, 0.0)
            force = (now - last) >= self.cache_refresh_sec
            nodes = self._client.snapshot(hwnd, root=root, force=force)
            self._last_read_at[hwnd] = now
            blocks: list[ScreenTextBlock] = []
            seen: set[str] = set()
            total = 0
            for node in nodes:
                if len(blocks) >= self.max_nodes or total >= self.max_chars:
                    break
                if node.is_password:
                    # Hard stop: password values never become screen evidence.
                    continue
                text = re.sub(r"\s+", " ", " ".join(part for part in (node.name, node.value) if part)).strip()
                if len(text) < 2 or text.lower() in seen:
                    continue
                seen.add(text.lower())
                total += len(text)
                digest = hashlib.sha1(f"uia|{window.hwnd}|{node.runtime_id}|{text}".encode("utf-8", "ignore")).hexdigest()[:16]
                blocks.append(ScreenTextBlock(digest, text[:800], "uia", 0.88, window.title, now, node.bounds))
            return tuple(blocks)
        except Exception:
            return ()

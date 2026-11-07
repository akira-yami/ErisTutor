# -*- coding: utf-8 -*-
"""stage_event_bus.py — cầu gửi/nhận event protocol qua transport hiện có."""

from __future__ import annotations

import json
import os
import socket
from collections import deque
from typing import Any, Mapping

from runtime.stage_protocol import StageInboundEvent, StageProtocolEvent
from runtime.stage_session_runtime import StageSessionRuntime


def stage_protocol_enabled() -> bool:
    """Bật event protocol mới mà vẫn giữ payload legacy.

    Mặc định bật vì payload mới có `type` rõ ràng; frontend cũ có thể bỏ qua.
    Nếu client cũ quá nhạy với message lạ, đặt ERIS_STAGE_PROTOCOL=0.
    """

    from runtime.runtime_settings_store import runtime_bool
    return runtime_bool("stage", "protocol_enabled", default=True)


class StageEventBus:
    """Gửi event sân khấu qua `transport.send_json`.

    Bus này cố ý mỏng: không queue retry, không block, không tự diễn giải event.
    Coordinator vẫn là nơi quyết định cancel TTS hay chạy chat cycle.
    """

    def __init__(self, *, transport: Any, logger: Any = None, max_recent: int = 60) -> None:
        self.transport = transport
        self.logger = logger
        self.recent_outbound: deque[dict[str, Any]] = deque(maxlen=max(1, int(max_recent)))
        self.recent_inbound: deque[dict[str, Any]] = deque(maxlen=max(1, int(max_recent)))
        self.session = StageSessionRuntime(max_replay=max(80, int(max_recent) * 3))
        # Gửi thẳng một bản event sang renderer. Trước đây đường này phụ thuộc
        # hoàn toàn vào Godot forward 4242 -> 4252; chỉ cần frontend chưa biết
        # event audio mới là Live2D vẫn hiện nhưng miệng im như ảnh PNG. UDP local
        # không cần receiver tồn tại và bản trùng được Live2D dedupe theo event_id.
        self._live2d_mirror_enabled = _env_bool("ERIS_LIVE2D_DIRECT_MIRROR", True)
        mirror_host = os.environ.get("ERIS_LIVE2D_MIRROR_HOST", "127.0.0.1").strip() or "127.0.0.1"
        if mirror_host in {"0.0.0.0", "::"}:
            mirror_host = "127.0.0.1"
        self._live2d_target = (mirror_host, _env_port("ERIS_LIVE2D_PORT", 4252))

    def emit(self, event: StageProtocolEvent) -> bool:
        if not stage_protocol_enabled():
            return False
        prepared = self.session.prepare(event)
        payload = prepared.to_payload()
        self.recent_outbound.append(payload)
        try:
            self.transport.send_json(payload)
            self.session.record_sent(payload, success=True)
            sent = True
        except Exception as exc:
            self.session.record_sent(payload, success=False)
            sent = False
            if self.logger is not None:
                try:
                    self.logger.debug("[stage-protocol] send failed: %s", exc)
                except (AttributeError, TypeError, ValueError):
                    pass
        self._mirror_live2d(payload)
        return sent

    def emit_live2d(self, event: StageProtocolEvent) -> bool:
        """Gửi một edge điều khiển thẳng tới renderer, kể cả khi stage protocol UI bị tắt.

        Playback start/done là tín hiệu thời gian thực của hình thể, không phải
        metadata tùy chọn của frontend. Nếu setting ``protocol_enabled`` bị tắt
        hoặc Godot chưa forward event mới, model vẫn phải mở/khép miệng đúng lúc.
        ``event_id`` ổn định ở caller giúp renderer bỏ bản trùng khi lane chuẩn
        cũng gửi cùng edge sau đó.
        """

        if not self._live2d_mirror_enabled:
            return False
        prepared = self.session.prepare(event)
        self._mirror_live2d(prepared.to_payload())
        return True

    def _mirror_live2d(self, payload: Mapping[str, Any]) -> None:
        if not self._live2d_mirror_enabled:
            return
        try:
            raw = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(raw) > 65507:
                raise ValueError(f"stage payload too large for UDP: {len(raw)} bytes")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(raw, self._live2d_target)
        except (OSError, TypeError, ValueError) as exc:
            if self.logger is not None:
                try:
                    self.logger.debug("[stage-protocol] live2d mirror failed: %s", exc)
                except (AttributeError, TypeError, ValueError):
                    pass

    def remember_inbound(self, payload: Mapping[str, Any]) -> StageInboundEvent:
        event = StageInboundEvent.from_payload(payload)
        self.recent_inbound.append(event.to_meta())
        self.session.observe_inbound(payload)
        return event

    def replay_after(self, event_seq: int) -> tuple[dict[str, Any], ...]:
        """Trả replay payload cho frontend mới; không tự gửi lại qua UDP."""
        return self.session.replay_after(event_seq)

    def session_snapshot(self) -> dict[str, Any]:
        return self.session.snapshot()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_port(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(1, min(65535, value))


__all__ = ["StageEventBus", "stage_protocol_enabled"]

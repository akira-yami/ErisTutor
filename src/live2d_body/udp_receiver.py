# -*- coding: utf-8 -*-
"""UDP lane riêng cho Live2D, không tranh cổng 4242 với Godot."""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

from .stage_state import Live2DStageState


class Live2DStageReceiver:
    def __init__(
        self,
        state: Live2DStageState,
        *,
        host: str = "127.0.0.1",
        port: int = 4252,
        backend_host: str = "127.0.0.1",
        backend_port: int = 4243,
        body_host: str = "127.0.0.1",
        body_port: int = 4242,
    ) -> None:
        self.state = state
        self.host = host
        self.port = int(port)
        self.backend_target = (backend_host, int(backend_port))
        self.body_target = (body_host, int(body_port))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self.last_error = ""
        self.packet_count = 0
        # Renderer chạy thường trú sau lần mở đầu tiên. Godot chỉ đổi desired_visible
        # qua UDP, tránh kill/spawn lại Cubism và nạp lại moc3 mỗi lần switch.
        self._control_lock = threading.RLock()
        self._desired_visible = True
        self._shutdown_requested = False
        self._requested_window_geometry: Any | None = None
        self._requested_scale_delta: float = 0.0
        self._requested_scale: float | None = None
        self._input_exclusion_rects: tuple[tuple[int, int, int, int], ...] = ()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.settimeout(0.25)
        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ErisLive2DUDP", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                raw, _address = self._sock.recvfrom(65507)
            except socket.timeout:
                continue
            except OSError as exc:
                if not self._stop.is_set():
                    self.last_error = str(exc)
                break
            try:
                payload: Any = json.loads(raw.decode("utf-8"))
                if isinstance(payload, dict):
                    if self._handle_renderer_control(payload):
                        self.packet_count += 1
                        continue
                    accepted = self.state.handle_payload(payload)
                    self.packet_count += 1
                    event_type = str(payload.get("type", "") or "").strip().lower()
                    if accepted and event_type in {
                        "speaking", "audio_start", "audio_playback_started",
                        "audio_done", "audio_playback_done", "audio_cancelled",
                        "audio_failed", "interrupted", "degraded", "turn_projection",
                    }:
                        print(
                            "[Live2D][Stage] "
                            f"recv={event_type} turn={str(payload.get('turn_id', '') or '')[:36]} "
                            f"seq={payload.get('seq', 0)} event_id={str(payload.get('event_id', '') or '')[:64]}",
                            flush=True,
                        )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self.last_error = f"invalid packet: {exc}"

    def _handle_renderer_control(self, payload: dict[str, Any]) -> bool:
        if str(payload.get("protocol", "") or "") != "eris.live2d.control":
            return False
        command = str(payload.get("type", "") or "").strip().lower()
        with self._control_lock:
            if command == "set_input_exclusion_rects":
                rects = []
                values = payload.get("rects")
                for rect in (values if isinstance(values, list) else [])[:64]:
                    try:
                        x, y, width, height = (int(rect[key]) for key in ("x", "y", "width", "height"))
                        if width > 0 and height > 0:
                            rects.append((x, y, width, height))
                    except (KeyError, TypeError, ValueError, OverflowError):
                        continue
                self._input_exclusion_rects = tuple(rects)
                return True
            if command == "set_visibility":
                self._desired_visible = bool(payload.get("visible", True))
                return True
            if command == "set_window_geometry":
                self._requested_window_geometry = {
                    "x": int(payload.get("screen_x", 0)),
                    "y": int(payload.get("screen_y", 0)),
                    "width": max(1, int(payload.get("width", 0) or 0)),
                    "height": max(1, int(payload.get("height", 0) or 0)),
                }
                return True
            if command == "adjust_scale":
                try:
                    self._requested_scale_delta += float(payload.get("delta", 0.0))
                except (KeyError, TypeError, ValueError):
                    pass
                return True
            if command == "set_scale":
                try:
                    self._requested_scale = float(payload.get("scale", 1.0))
                except (KeyError, TypeError, ValueError):
                    pass
                return True
            if command == "shutdown":
                self._shutdown_requested = True
                return True
        return True

    @property
    def input_exclusion_rects(self) -> tuple[tuple[int, int, int, int], ...]:
        with self._control_lock:
            return self._input_exclusion_rects

    @property
    def desired_visible(self) -> bool:
        with self._control_lock:
            return bool(self._desired_visible)

    @property
    def shutdown_requested(self) -> bool:
        with self._control_lock:
            return bool(self._shutdown_requested)

    @property
    def requested_window_geometry(self) -> dict[str, int] | None:
        return self.consume_requested_window_geometry()

    def consume_requested_window_geometry(self) -> dict[str, int] | None:
        with self._control_lock:
            if self._requested_window_geometry is None:
                return None
            geometry = dict(self._requested_window_geometry)
            self._requested_window_geometry = None
            return geometry

    def consume_scale_delta(self) -> float:
        with self._control_lock:
            delta = self._requested_scale_delta
            self._requested_scale_delta = 0.0
            return delta

    def consume_scale(self) -> float | None:
        with self._control_lock:
            scale = self._requested_scale
            self._requested_scale = None
            return scale

    def send_context_menu_request(self, *, screen_x: int, screen_y: int) -> None:
        payload = {
            "protocol": "eris.body.control",
            "version": 1,
            "type": "open_context_menu",
            "timestamp": time.time(),
            "screen_x": int(screen_x),
            "screen_y": int(screen_y),
            "source": "live2d",
        }
        self._send_target(payload, self.body_target)

    def send_window_geometry(
        self,
        *,
        screen_x: int,
        screen_y: int,
        width: int,
        height: int,
    ) -> None:
        payload = {
            "protocol": "eris.body.control",
            "version": 1,
            "type": "window_geometry",
            "timestamp": time.time(),
            "screen_x": int(screen_x),
            "screen_y": int(screen_y),
            "width": max(1, int(width)),
            "height": max(1, int(height)),
            "source": "live2d",
        }
        self._send_target(payload, self.body_target)

    def send_avatar_ready(self) -> None:
        payload = {
            "protocol": "eris.stage",
            "version": 2,
            "type": "avatar_ready",
            "timestamp": time.time(),
            "data": {
                "renderer": "live2d-py",
                "listen_port": self.port,
            },
        }
        self._send_backend(payload)

    def send_stage_ack(self, *, event_seq: int = 0) -> None:
        payload = {
            "protocol": "eris.stage",
            "version": 2,
            "type": "stage_ack",
            "timestamp": time.time(),
            "event_seq": int(event_seq or 0),
            "data": {"renderer": "live2d-py"},
        }
        self._send_backend(payload)

    def _send_backend(self, payload: dict[str, Any]) -> None:
        self._send_target(payload, self.backend_target)

    def _send_target(self, payload: dict[str, Any], target: tuple[str, int]) -> None:
        try:
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(raw, target)
        except OSError as exc:
            self.last_error = str(exc)

    def close(self) -> None:
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.8)

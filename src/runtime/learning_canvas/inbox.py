"""Single inbound reader: Canvas control stays responsive while Root blocks.

All other packets remain FIFO and are dispatched by the existing foreground.
The worker only serves the explicit Canvas route, never chat or cognition.
"""
from __future__ import annotations

from collections.abc import Mapping
import queue
import threading


class CanvasControlInbox:
    def __init__(self, transport, handler, *, logger=None):
        self.transport, self.handler, self.logger = transport, handler, logger
        self._pending: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="eris-canvas-control", daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                message = self.transport.poll_json()
                if message is None:
                    self._stop.wait(0.005)
                elif isinstance(message, Mapping) and message.get("type") == "learning_canvas_request":
                    self.handler(dict(message))
                else:
                    self._pending.put(message)
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning("[learning-canvas] control request failed: %s", type(exc).__name__)
                self._stop.wait(0.02)

    def poll_json(self):
        try:
            return self._pending.get_nowait()
        except queue.Empty:
            return None

    def stop(self):
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.0)

    def close(self):
        self.stop()
        self.transport.close()

    def __getattr__(self, name):
        return getattr(self.transport, name)

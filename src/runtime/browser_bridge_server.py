# -*- coding: utf-8 -*-
"""ERIS Browser DOM Bridge — Local HTTP Server Subsystem.

Extension Brave/Chrome POST JSON → server này → ghi browser_dom_state.json.
Chạy như daemon thread trong ERIS, không block shutdown.

Port: 7422 (mặc định, override bằng ERIS_BROWSER_BRIDGE_PORT)
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


_PORT = int(os.environ.get("ERIS_BROWSER_BRIDGE_PORT", "7422"))
_OUTPUT = Path(os.environ.get("LOCALAPPDATA", "")) / "ERIS" / "browser_dom_state.json"
_ALIVE  = False


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


class _Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/update":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data: dict[str, Any] = json.loads(body.decode("utf-8"))
            if "timestamp" not in data:
                data["timestamp"] = time.time()
            _atomic_write(_OUTPUT, json.dumps(data, ensure_ascii=False, indent=2))
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception as exc:
            try:
                self.send_response(500)
                self._cors()
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(exc)}).encode())
            except Exception:
                pass

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true,"service":"eris_bridge"}')
        elif self.path == "/state":
            try:
                if _OUTPUT.exists():
                    content = _OUTPUT.read_bytes()
                    self.send_response(200)
                    self._cors()
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    self.send_response(204)
                    self._cors()
                    self.end_headers()
            except Exception as exc:
                self.send_response(500)
                self._cors()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(exc)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
        # Chỉ log lỗi (không phải 200/204)
        if args and str(args[1]) not in ("200", "204", ""):
            import sys
            sys.stderr.write(f"[ERIS Bridge] {fmt % args}\n")


class BrowserBridgeServer:
    """Wrapper daemon thread cho HTTP server."""

    def __init__(self, port: int = _PORT) -> None:
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> "BrowserBridgeServer":
        global _ALIVE
        if _ALIVE:
            return self
        try:
            self._server = HTTPServer(("127.0.0.1", self.port), _Handler)
        except OSError as exc:
            # Port đã bị dùng → không crash ERIS
            raise RuntimeError(
                f"[browser_bridge] Port {self.port} đã bị dùng: {exc}"
            ) from exc
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="eris-browser-bridge",
            daemon=True,  # Tự tắt khi ERIS tắt
        )
        self._thread.start()
        _ALIVE = True
        return self

    def stop(self) -> None:
        global _ALIVE
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None
        _ALIVE = False

    def shutdown(self) -> None:
        self.stop()

    def close(self) -> None:
        self.stop()

    @property
    def alive(self) -> bool:
        return _ALIVE and self._thread is not None and self._thread.is_alive()


def start_browser_bridge_server(port: int = _PORT, *, logger: Any = None) -> BrowserBridgeServer | None:
    """Khởi động server và trả về instance. Trả về None nếu disabled hoặc lỗi."""
    enabled = str(os.environ.get("ERIS_BROWSER_BRIDGE_ENABLED", "0")).strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        if logger:
            logger.info("[browser_bridge] disabled by env ERIS_BROWSER_BRIDGE_ENABLED=0")
        return None
    try:
        server = BrowserBridgeServer(port=port)
        server.start()
        if logger:
            logger.info(
                "[browser_bridge] HTTP server ready on http://127.0.0.1:%d → %s",
                port, _OUTPUT,
            )
        return server
    except Exception as exc:
        if logger:
            logger.warning("[browser_bridge] Không khởi động được server: %s", exc)
        return None

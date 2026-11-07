# -*- coding: utf-8 -*-
from __future__ import annotations

"""Entry point của eris-uiaccess-broker.exe.

File này chỉ hợp lệ khi được đóng gói thành executable có manifest uiAccess=true,
được ký code-signing và cài trong Program Files. Chạy script Python trực tiếp
không tự nhiên có quyền UIAccess, vì Windows không dễ bị thuyết phục bằng niềm tin.
"""

import json
import os
import sys
from multiprocessing import AuthenticationError
from pathlib import Path
from multiprocessing.connection import Listener
from typing import Any

if __package__ in {None, ""}:
    # Cho phép chạy trực tiếp khi debug/PyInstaller lấy file này làm entry point.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from native.process_resolver import ProcessResolver
    from native.uia_event_client import UIAEventClient
    from native.uiaccess_broker.client import DEFAULT_PIPE
    from native.uiaccess_broker.protocol import BrokerRequest, BrokerResponse
else:
    from native.process_resolver import ProcessResolver
    from native.uia_event_client import UIAEventClient
    from .client import DEFAULT_PIPE
    from .protocol import BrokerRequest, BrokerResponse


class UIAccessBrokerServer:
    def __init__(self, *, pipe: str = DEFAULT_PIPE, authkey: bytes | None = None) -> None:
        env_key = os.getenv("ERIS_UIACCESS_BROKER_AUTHKEY", "")
        self.authkey = authkey if authkey is not None else env_key.encode("utf-8")
        self.pipe = pipe
        self.resolver = ProcessResolver()
        self.uia = UIAEventClient(max_nodes=160, max_depth=7)

    @staticmethod
    def _node_metadata(node: Any) -> dict[str, Any]:
        # IsPassword là hard stop. Broker không trả value và cũng không trả name
        # của password control để giảm khả năng rò rỉ label nhạy cảm ngoài ý muốn.
        if bool(getattr(node, "is_password", False)):
            return {"runtime_id": str(getattr(node, "runtime_id", "")), "role": str(getattr(node, "role", "")), "is_password": True}
        return {
            "runtime_id": str(getattr(node, "runtime_id", "")),
            "role": str(getattr(node, "role", "")),
            "name": str(getattr(node, "name", "") or "")[:180],
            "bounds": getattr(node, "bounds", None),
            "has_focus": bool(getattr(node, "has_focus", False)),
            "selected": bool(getattr(node, "selected", False)),
            "is_password": False,
        }

    def handle(self, request: BrokerRequest) -> BrokerResponse:
        if request.operation == "ping":
            return BrokerResponse(True, "ping", {"broker": "eris-uiaccess-v1"})
        identity = self.resolver.resolve(request.hwnd)
        if request.operation == "window_metadata":
            return BrokerResponse(True, request.operation, {
                "hwnd": int(request.hwnd), "pid": int(identity.pid),
                "process_name": identity.process_name, "window_class": identity.window_class,
            })
        if request.operation == "accessibility_metadata":
            nodes = self.uia.snapshot(request.hwnd, force=True)[: request.max_nodes]
            return BrokerResponse(True, request.operation, {
                "hwnd": int(request.hwnd),
                "nodes": [self._node_metadata(node) for node in nodes],
            })
        return BrokerResponse(False, request.operation, {}, "operation bị từ chối")

    def serve_forever(self) -> None:  # pragma: no cover - Windows E2E
        if os.name != "nt":
            raise RuntimeError("UIAccess broker chỉ chạy trên Windows")
        if not self.authkey:
            raise RuntimeError("ERIS_UIACCESS_BROKER_AUTHKEY chưa được cấu hình")
        listener = Listener(self.pipe, family="AF_PIPE", authkey=self.authkey)
        try:
            while True:
                try:
                    conn = listener.accept()
                except AuthenticationError:
                    continue
                try:
                    raw = conn.recv_bytes(maxlength=65536)
                    request = BrokerRequest.from_mapping(json.loads(raw.decode("utf-8")))
                    response = self.handle(request)
                    conn.send_bytes(json.dumps(response.to_dict(), ensure_ascii=False).encode("utf-8"))
                except Exception as exc:
                    response = BrokerResponse(False, "invalid", {}, f"bad request: {type(exc).__name__}")
                    try:
                        conn.send_bytes(json.dumps(response.to_dict()).encode("utf-8"))
                    except Exception:
                        pass
                finally:
                    conn.close()
        finally:
            listener.close()


def main() -> int:
    UIAccessBrokerServer().serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

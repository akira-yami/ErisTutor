# -*- coding: utf-8 -*-
from __future__ import annotations

"""Client local cho signed UIAccess broker.

Dùng AF_PIPE của Windows để tránh mở TCP port. Authkey phải được inject qua
biến môi trường/launcher, không hard-code secret vào source.
"""

import json
import os
from multiprocessing.connection import Client
from typing import Any

from .protocol import BrokerRequest, BrokerResponse


DEFAULT_PIPE = r"\\.\pipe\ErisUiAccessBrokerV1"


class UIAccessBrokerClient:
    def __init__(self, *, pipe: str = DEFAULT_PIPE, authkey: bytes | None = None, timeout_seconds: float = 1.2) -> None:
        self.pipe = pipe
        env_key = os.getenv("ERIS_UIACCESS_BROKER_AUTHKEY", "")
        self.authkey = authkey if authkey is not None else env_key.encode("utf-8")
        self.timeout_seconds = max(0.2, float(timeout_seconds))

    @property
    def configured(self) -> bool:
        return os.name == "nt" and bool(self.authkey)

    def request(self, request: BrokerRequest) -> BrokerResponse:
        request.validate()
        if not self.configured:
            return BrokerResponse(False, request.operation, {}, "uiaccess broker chưa được cấu hình")
        # multiprocessing.connection không có per-call timeout portable. Broker
        # được dùng trên local named pipe và caller phải fail-closed khi exception.
        try:  # pragma: no cover - Windows E2E
            conn = Client(self.pipe, family="AF_PIPE", authkey=self.authkey)
            try:
                conn.send_bytes(json.dumps(request.to_dict(), ensure_ascii=False).encode("utf-8"))
                raw = conn.recv_bytes()
            finally:
                conn.close()
            data = json.loads(raw.decode("utf-8"))
            return BrokerResponse(bool(data.get("ok")), str(data.get("operation", request.operation)), dict(data.get("payload") or {}), str(data.get("error", "") or ""))
        except Exception as exc:
            return BrokerResponse(False, request.operation, {}, f"broker unavailable: {type(exc).__name__}")


__all__ = ["DEFAULT_PIPE", "UIAccessBrokerClient"]

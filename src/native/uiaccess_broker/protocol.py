# -*- coding: utf-8 -*-
from __future__ import annotations

"""Protocol tối thiểu giữa Eris core và UIAccess helper.

Cố tình không có lệnh OCR, memory, LLM, đọc clipboard hay dump text tự do.
Broker chỉ trả accessibility metadata đã lọc và tuyệt đối chặn password.
"""

from dataclasses import asdict, dataclass
from typing import Any, Mapping


_ALLOWED_OPERATIONS = {"ping", "window_metadata", "accessibility_metadata"}


@dataclass(frozen=True, slots=True)
class BrokerRequest:
    operation: str
    hwnd: int = 0
    max_nodes: int = 80

    def validate(self) -> None:
        if self.operation not in _ALLOWED_OPERATIONS:
            raise ValueError(f"operation không được phép: {self.operation}")
        if self.operation != "ping" and int(self.hwnd or 0) <= 0:
            raise ValueError("hwnd phải > 0")
        if not 1 <= int(self.max_nodes) <= 160:
            raise ValueError("max_nodes vượt giới hạn broker")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BrokerRequest":
        request = cls(
            operation=str(value.get("operation", "") or ""),
            hwnd=int(value.get("hwnd", 0) or 0),
            max_nodes=int(value.get("max_nodes", 80) or 80),
        )
        request.validate()
        return request


@dataclass(frozen=True, slots=True)
class BrokerResponse:
    ok: bool
    operation: str
    payload: Mapping[str, Any]
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": bool(self.ok), "operation": self.operation, "payload": dict(self.payload), "error": self.error}


__all__ = ["BrokerRequest", "BrokerResponse"]

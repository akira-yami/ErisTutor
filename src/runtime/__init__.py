"""Runtime package của ERIS.

Không import RuntimeCoordinator eager ở đây.

Lý do rất đời thường: nhiều test chỉ cần `runtime.state_committer`, nhưng import package
`runtime` lại kéo theo coordinator, cortex, guard_config, LLM... rồi nổ vì thiếu dependency.
Một `__init__` mà boot nửa runtime chỉ để lấy một class là kiểu cửa nhà vệ sinh nối thẳng
vào phòng máy chủ. Ta dùng lazy import cho đỡ tự hủy.
"""

from __future__ import annotations

from typing import Any

__all__ = ["RuntimeCoordinator"]


def __getattr__(name: str) -> Any:
    if name == "RuntimeCoordinator":
        from .coordinator import RuntimeCoordinator

        return RuntimeCoordinator
    raise AttributeError(name)

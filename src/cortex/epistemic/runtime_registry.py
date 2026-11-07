from __future__ import annotations

from typing import Any


_DEFAULT_KERNEL: Any = None


def set_default_kernel(kernel: Any) -> None:
    global _DEFAULT_KERNEL
    _DEFAULT_KERNEL = kernel


def get_default_kernel() -> Any:
    return _DEFAULT_KERNEL

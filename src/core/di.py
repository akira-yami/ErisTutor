# -*- coding: utf-8 -*-
"""Tiện ích dependency injection thống nhất cho toàn runtime.

Quy ước duy nhất: dependency được truyền vào chỉ bị thay thế khi nó là ``None``.
Không dùng ``provided or Default()`` vì object hợp lệ nhưng falsey sẽ bị nuốt mất,
một lỗi DI rất nhỏ và rất biết cách xuất hiện vào lúc tệ nhất.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def resolve_dependency(provided: T | None, factory: Callable[[], T]) -> T:
    """Trả dependency đã inject, hoặc tạo mặc định đúng một lần khi thiếu."""

    return provided if provided is not None else factory()


__all__ = ["resolve_dependency"]

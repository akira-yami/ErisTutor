# -*- coding: utf-8 -*-
"""Merge state theo ba trạng thái rõ ràng.

``UNSET`` nghĩa là provider không gửi field đó, nên giữ nguyên state cũ.
``None`` nghĩa là provider chủ động xóa field.
Mọi giá trị còn lại, kể cả chuỗi rỗng hoặc số 0, là một cập nhật hợp lệ.

Tách ba trường hợp này tránh lỗi rất khó chịu: suggestion của nước trước cứ sống
mãi chỉ vì snapshot mới không có suggestion. Máy tính vốn không đọc được ý định;
ít nhất ta đừng bắt nó đoán bằng toán tử ``or``.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


class _UnsetType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _UnsetType()


def patch_value(payload: Mapping[str, Any], key: str) -> Any:
    """Trả ``UNSET`` nếu key không tồn tại, không đánh đồng với ``None``."""

    return payload[key] if key in payload else UNSET


def apply_tri_state_patch(
    state: Mapping[str, Any] | None,
    payload: Mapping[str, Any] | None,
    *,
    keys: Iterable[str],
) -> dict[str, Any]:
    """Áp patch mà không giữ dữ liệu cũ một cách vô thức.

    - key vắng mặt: giữ nguyên;
    - key có giá trị ``None``: xóa khỏi state;
    - key có giá trị khác: ghi đè, kể cả ``""`` hoặc ``0``.
    """

    result = dict(state or {})
    source = payload or {}
    for key in keys:
        value = patch_value(source, key)
        if value is UNSET:
            continue
        if value is None:
            result.pop(key, None)
            continue
        result[key] = value
    return result


__all__ = ["UNSET", "apply_tri_state_patch", "patch_value"]

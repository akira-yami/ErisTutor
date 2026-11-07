# -*- coding: utf-8 -*-
"""Chuẩn hóa object domain trước khi đi qua JSON/UDP/persistence.

Runtime dùng dataclass typed để giữ contract nội bộ rõ ràng. JSON lại chỉ hiểu
các kiểu nguyên thủy. Mọi boundary public phải đi qua module này, thay vì hy vọng
``json.dumps`` tự đoán cách biến ``SemanticFrame`` thành object.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


def to_json_compatible(value: Any, *, drop_private_typed: bool = False) -> Any:
    """Đổi ``value`` thành cây dữ liệu JSON-safe.

    ``drop_private_typed`` loại các key ``_typed_*``. Đây là object cache nội bộ
    cho orchestrator, tuyệt đối không được phát ra parser, UDP hay log JSON.
    """

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return to_json_compatible(value.value, drop_private_typed=drop_private_typed)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)

    # Domain object nên tự sở hữu biểu diễn public của nó.
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return to_json_compatible(
                to_dict(),
                drop_private_typed=drop_private_typed,
            )
        except Exception:
            # Boundary không được làm chết runtime chỉ vì một adapter to_dict lỗi.
            pass

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if drop_private_typed and text_key.startswith("_typed_"):
                continue
            result[text_key] = to_json_compatible(
                item,
                drop_private_typed=drop_private_typed,
            )
        return result

    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            to_json_compatible(item, drop_private_typed=drop_private_typed)
            for item in value
        ]

    if is_dataclass(value):
        # Không dùng dataclasses.asdict vì nó deepcopy và có thể làm vỡ object
        # chứa lock/socket. Chỉ đọc field public theo contract dataclass.
        return {
            field.name: to_json_compatible(
                getattr(value, field.name),
                drop_private_typed=drop_private_typed,
            )
            for field in fields(value)
        }

    # Fallback cuối chỉ dành cho log/transport chịu lỗi. Object domain quan trọng
    # phải có to_dict; stringify giúp runtime sống và để log chỉ ra kiểu bị lọt.
    return str(value)


def public_payload(value: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Trả payload public JSON-safe và bỏ object cache ``_typed_*``."""

    converted = to_json_compatible(value, drop_private_typed=True)
    return converted if isinstance(converted, dict) else {}

# -*- coding: utf-8 -*-
"""Đổi layer Live2D khi biểu cảm yêu cầu, không đụng các part không xác nhận.

Giới hạn quan trọng của bản moc3 INFJ hiện tại:
- ``ArtMesh7`` và ``ArtMesh16`` là DRAWABLE (mesh), không phải Part;
- ``Mado`` (tên trong Word) không có Part/Parameter ID trong moc3/cdi3;
- live2d-py LAppModel có ``GetPartIds`` / ``SetPartOpacity`` nhưng không đảm
  bảo API đặt opacity riêng cho DRAWABLE.

Do đó chỉ thao tác khi model thực sự công khai ID đúng dạng Part tương ứng.
Không ẩn bừa ``Part2`` (toàn bộ mặt) để làm giả thao tác ẩn ArtMesh.
Nếu người tạo rig sau này tách Mado, ArtMesh7/16 thành Part, adapter này sẽ
hoạt động mà không cần đổi luồng gọi emotion.
"""

from __future__ import annotations

from typing import Any


class Live2DLayerVisibility:
    """Best-effort, không bao giờ khiến cửa sổ Live2D chết vì thiếu API."""

    def __init__(self) -> None:
        self._known_parts: dict[str, int] | None = None

    def apply(self, model: Any, *, emotion: str, weight: float, terminal: bool = False) -> None:
        get_ids = getattr(model, "GetPartIds", None)
        setter = getattr(model, "SetPartOpacity", None)
        if not callable(get_ids) or not callable(setter):
            return
        if self._known_parts is None:
            try:
                self._known_parts = {
                    str(part_id).strip().lower(): index
                    for index, part_id in enumerate(get_ids())
                }
            except (TypeError, ValueError, RuntimeError):
                self._known_parts = {}
        active = not terminal and weight >= 0.22
        # Mado: ẩn mặc định, chỉ bật khi Root yêu cầu blush_affection.
        # Tên Mado trong tài liệu chưa phải ID được xác nhận của model.
        expected = {"mado": 1.0 if active and emotion == "blush_affection" else 0.0}
        # Hai mesh này chỉ có thể ẩn với API Part nếu thực sự có Part ID riêng.
        hidden = active and emotion in {"yan_warning", "lazy_deadpan"}
        expected.update({"artmesh7": 0.0 if hidden else 1.0,
                         "artmesh16": 0.0 if hidden else 1.0})
        for part_id, opacity in expected.items():
            index = self._known_parts.get(part_id)
            if index is not None:
                try:
                    setter(index, opacity)
                except (TypeError, ValueError, RuntimeError):
                    continue

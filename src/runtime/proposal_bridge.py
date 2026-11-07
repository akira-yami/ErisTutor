# -*- coding: utf-8 -*-
"""ERIS-03: adapter mỏng đưa *đề xuất* từ Subject Core lên inbox chung.

Không sửa thuật toán Neuro/Subject, không thay đổi identity proposal của nó và
không chuyển action_kind=speak thành lệnh nói. Chỉ chiếu semantic_target thành
idea chưa kiểm chứng để RootAgent có thể cân nhắc ở lượt kế tiếp.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from runtime.behavior_proposals import ensure_proposal_bus


def mirror_subject_ideas(host: Any, proposals: Sequence[Mapping[str, Any]]) -> int:
    """Chiếu một subset *không quyền* lên bus, không bao giờ invoke delivery.

    Không mirror SILENCE/BODY_ONLY, không nhồi toàn bộ evaluation score/urge,
    expression_commitment hoặc nội dung nhạy cảm bên trong Subject frame.
    """
    bus = ensure_proposal_bus(host)
    count = 0
    for item in tuple(proposals)[:32]:
        if not isinstance(item, Mapping) or item.get("action_kind") != "speak":
            continue
        args = item.get("arguments")
        if not isinstance(args, Mapping):
            continue
        target = " ".join(str(args.get("semantic_target") or "").split())[:320]
        if not target:
            continue
        source_id = str(item.get("proposal_id") or "")[:180]
        # Đề xuất derived capability có thể trùng một affordance nói thường;
        # giữ ID producer để bus tự dedup retry cùng cơ hội.
        bus.submit(
            source="subject_core", kind="subject_affordance", subject=target,
            proposal_key=source_id,
            payload={"source_refs": tuple(item.get("evidence_refs") or ())[:8]},
            confidence=item.get("expected_action_efficacy", 0.5),
            ttl_seconds=120.0,
        )
        count += 1
    return count


__all__ = ["mirror_subject_ideas"]

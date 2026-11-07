# -*- coding: utf-8 -*-
"""growth_consolidator.py — Tác_Tử_Nền sinh Đề_Xuất_Nhích cho long-arc.

Đặt trên kỷ luật của ``DaydreamConsolidator``: chỉ chạy khi đủ áp lực củng cố /
thời gian rảnh và ngân sách cho phép; chỉ chấp nhận bằng chứng có ``evidence_ids``
thật; loại tín hiệu không nguồn. KHÁC ở chỗ nó sinh ``NudgeProposal`` (không phải
agenda) và **không tự ghi** Hồ_Sơ — chỉ trả đề xuất cho Hệ_Phát_Triển.

Lõi deterministic (không bắt buộc LLM): gom quan sát đã-lọc qua NHIỀU PHIÊN, một
phiên một phiếu (nên một phiên cảm xúc mạnh không định hình lại ERIS — R8.1/R8.3),
yêu cầu tần suất tối thiểu (R1.4), sửa-lỗi/định-hướng của bạn có trọng số cao hơn
quan sát thụ động (R1.5). Có thể bọc LLM phản tư sau mà không đổi API.
"""

from __future__ import annotations
from core.di import resolve_dependency

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .development_profile import GrowthAnchor, NudgeProposal, PERSONA_AXES


# Trọng số theo loại neo (R1.5): can thiệp tường minh của bạn > quan sát thụ động.
_KIND_WEIGHT: dict[str, float] = {
    "user_correction": 2.0,
    "user_steer": 2.0,
    "repeated_preference": 1.2,
    "interaction_history": 1.0,
}


@dataclass(slots=True)
class GrowthObservation:
    """Một quan sát đã-lọc, gắn vào một phiên cụ thể."""

    axis: str
    direction: float                    # dấu + độ lớn gợi ý cho nhích (chưa kẹp)
    evidence_ids: tuple[str, ...]
    session_id: str
    kind: str = "interaction_history"


@dataclass(slots=True)
class GrowthConsolidatorConfig:
    min_sessions: int = 2               # cần ≥ ngần này phiên khác nhau (R8.1)
    min_frequency: int = 3              # tổng số quan sát tối thiểu cho một trục (R1.4)
    max_proposal_magnitude: float = 0.08
    trigger_threshold: float = 0.55


class GrowthConsolidator:
    def __init__(self, config: GrowthConsolidatorConfig | None = None) -> None:
        self.config = resolve_dependency(config, lambda: GrowthConsolidatorConfig())

    # --- gating (R6.3, R6.4) -------------------------------------------
    def should_run(
        self,
        *,
        idle_pressure: float = 0.0,
        consolidation_pressure: float = 0.0,
        budget_allows: bool = True,
        user_interacting_high_risk: bool = False,
    ) -> bool:
        if user_interacting_high_risk:
            return False                # hoãn khi bạn đang tương tác kỹ thuật/rủi ro cao
        if not budget_allows:
            return False                # tôn trọng phủ quyết ngân sách model
        return max(_f(idle_pressure), _f(consolidation_pressure)) >= self.config.trigger_threshold

    # --- sinh đề xuất (R1, R6.1/6.2/6.5, R8) ---------------------------
    def consolidate(
        self,
        observations: Iterable[GrowthObservation],
        *,
        allowed_evidence_ids: Iterable[str],
        profile=None,
    ) -> list[NudgeProposal]:
        allowed = {str(x) for x in allowed_evidence_ids}
        # 1. Lọc: chỉ giữ quan sát có evidence_ids THẬT, axis hợp lệ (R6.5, R1.3).
        valid: list[GrowthObservation] = []
        for obs in observations:
            if obs.axis not in PERSONA_AXES:
                continue
            ev = tuple(e for e in obs.evidence_ids if e in allowed)
            if not ev:
                continue
            valid.append(GrowthObservation(obs.axis, obs.direction, ev, obs.session_id, obs.kind))

        # 2. Gom theo trục.
        by_axis: dict[str, list[GrowthObservation]] = defaultdict(list)
        for obs in valid:
            by_axis[obs.axis].append(obs)

        proposals: list[NudgeProposal] = []
        for axis, obs_list in by_axis.items():
            sessions = {o.session_id for o in obs_list}
            # 3. Cần đủ nhiều phiên + đủ tần suất, nếu không thì GIỮ NGUYÊN (R8.1, R8.4, R1.4).
            if len(sessions) < self.config.min_sessions or len(obs_list) < self.config.min_frequency:
                continue

            # 4. Một phiên một phiếu: trung bình trong phiên rồi trung bình giữa các phiên.
            #    → một phiên cảm xúc mạnh không lấn át (R8.3).
            per_session: list[float] = []
            for sid in sessions:
                items = [o for o in obs_list if o.session_id == sid]
                num = sum(o.direction * _KIND_WEIGHT.get(o.kind, 1.0) for o in items)
                den = sum(_KIND_WEIGHT.get(o.kind, 1.0) for o in items) or 1.0
                per_session.append(num / den)
            aggregate = sum(per_session) / len(per_session)

            # 5. Kẹp độ lớn đề xuất (nhích nhỏ; phong bì/ngưỡng do Cổng lo tiếp).
            magnitude = max(-self.config.max_proposal_magnitude, min(self.config.max_proposal_magnitude, aggregate))
            if abs(magnitude) < 1e-6:
                continue

            anchors = self._anchors(obs_list)
            proposals.append(
                NudgeProposal(
                    axis=axis,
                    proposed_delta=magnitude,
                    anchors=anchors,
                    rationale=f"aggregated_over_{len(sessions)}_sessions_{len(obs_list)}_observations",
                )
            )
        return proposals

    @staticmethod
    def _anchors(obs_list: list[GrowthObservation]) -> tuple[GrowthAnchor, ...]:
        by_kind: dict[str, list[str]] = defaultdict(list)
        sessions_by_kind: dict[str, set[str]] = defaultdict(set)
        for o in obs_list:
            by_kind[o.kind].extend(o.evidence_ids)
            sessions_by_kind[o.kind].add(o.session_id)
        anchors: list[GrowthAnchor] = []
        for kind, ids in by_kind.items():
            anchors.append(
                GrowthAnchor(
                    kind=kind,
                    evidence_ids=tuple(dict.fromkeys(ids)),
                    weight=min(1.0, _KIND_WEIGHT.get(kind, 1.0) / 2.0),
                    observed_count=len(ids),
                )
            )
        return tuple(anchors)


def _f(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0

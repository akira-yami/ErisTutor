# -*- coding: utf-8 -*-
"""Phase 1 - Systemic / Goal Simulation Worker.

GoalSimulatorAgent nghĩ về bài toán/task, không nghĩ hộ cảm xúc user. Nó tạo
bottleneck_prediction, goal_progress_delta và possible_next_step để pipeline có
xương sống hành động thay vì chỉ ngồi cảm nhận rất sâu về việc chưa làm gì.
"""

from __future__ import annotations

from typing import Iterable

from ..thought_fragment import ThoughtFragment, clamp01
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import TECHNICAL_TERMS, context_evidence_ids, kind_of_request, score_terms, user_text_from_context


class GoalSimulatorAgent(RuleBasedWorker):
    """Sinh fragment mô phỏng goal/task."""

    name = "goal_simulator"

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        text = user_text_from_context(context)
        evidence_ids = context_evidence_ids(context, fallback_prefix="goal")
        if not text and not evidence_ids:
            return ()
        now = float(context.now or 0.0) or None
        request_kind = kind_of_request(text)
        technical = score_terms(text, TECHNICAL_TERMS)
        fragments: list[ThoughtFragment] = []

        if request_kind == "implementation_request":
            next_step = "Triển khai theo lát mỏng: thêm worker contract, nối vào SubconsciousLoop, rồi viết test regression trước khi cắm sâu runtime."
            progress = "User request có thể hoàn tất bằng patch cụ thể; tiến độ tăng nếu artifact chạy test được."
            bottleneck = "Bottleneck chính là integration an toàn: worker cần sinh fragment có evidence, không được tự surface public."
            urgency = 0.76
        elif request_kind == "diagnostic_analysis_request":
            next_step = "Ưu tiên gom dữ kiện, xếp giả thuyết theo xác suất, rồi đề xuất fix nhỏ nhất giải thích nhiều log nhất."
            progress = "Goal hiện là diagnosis; tiến độ đo bằng độ giảm mù nguyên nhân, không phải số dòng code."
            bottleneck = "Bottleneck chính là thiếu evidence hoặc log không đủ phân tách gate nào đang chặn."
            urgency = 0.58
        else:
            next_step = "Giữ mạch hiện tại, chọn bước nhỏ có thể kiểm chứng thay vì mở thêm tầng kiến trúc."
            progress = "Goal vẫn ở trạng thái tiếp diễn; nên tránh reset context."
            bottleneck = "Bottleneck có thể là scope creep nếu thêm module khi contract chưa rõ."
            urgency = 0.42

        fragments.append(
            ThoughtFragment.create(
                source_agent=self.name,
                kind="possible_next_step",
                content=next_step,
                evidence_ids=evidence_ids,
                confidence=0.58 + technical * 0.25,
                urgency=urgency,
                affect_charge=0.16,
                privacy_level="normal",
                surface_allowed=False,
                ttl_seconds=300,
                tags=("goal_simulation", "possible_next_step"),
                now=now,
            )
        )
        fragments.append(
            ThoughtFragment.create(
                source_agent=self.name,
                kind="goal_progress_delta",
                content=progress,
                evidence_ids=evidence_ids,
                confidence=0.54 + technical * 0.20,
                urgency=0.38 + urgency * 0.20,
                affect_charge=0.12,
                privacy_level="normal",
                surface_allowed=False,
                ttl_seconds=300,
                tags=("goal_simulation", "goal_progress"),
                now=now,
            )
        )
        fragments.append(
            ThoughtFragment.create(
                source_agent=self.name,
                kind="bottleneck_prediction",
                content=bottleneck,
                evidence_ids=evidence_ids,
                confidence=0.56 + technical * 0.24,
                urgency=clamp01(0.40 + technical * 0.28),
                affect_charge=0.18,
                privacy_level="normal",
                surface_allowed=False,
                ttl_seconds=300,
                tags=("goal_simulation", "bottleneck_prediction"),
                now=now,
            )
        )

        if technical >= 0.25:
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="theorycrafting_fragment",
                    content="Thiết kế nên ưu tiên deterministic worker trước LLM worker để mất model vẫn còn state nền kiểm chứng được.",
                    evidence_ids=evidence_ids,
                    confidence=0.64,
                    urgency=0.46,
                    affect_charge=0.14,
                    privacy_level="normal",
                    surface_allowed=False,
                    ttl_seconds=360,
                    tags=("goal_simulation", "architecture_constraint"),
                    now=now,
                )
            )

        return tuple(fragments)

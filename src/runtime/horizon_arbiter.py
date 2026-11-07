# -*- coding: utf-8 -*-
"""HorizonArbiter cho ERIS V74.

Arbiter chỉ điều chỉnh utility của coalition đã được Subject Core tạo. Nó không
bịa candidate mới, không đổi evidence và không có quyền vượt hard boundary. Mục
đích là để H2-H4 thật sự có ảnh hưởng mà không dựng thêm một Executive song song.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Mapping, Sequence

from cortex.subject.contracts import CoalitionCandidate, SubjectDeliberationFrame
from runtime.life_horizon import LifeHorizonSnapshot


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def _tokens(value: Any) -> set[str]:
    text = " ".join(str(value or "").lower().split())
    return {item for item in re.findall(r"[\wÀ-ỹ]+", text, flags=re.UNICODE) if len(item) >= 3}


def _goal_alignment(candidate: CoalitionCandidate, snapshot: LifeHorizonSnapshot) -> tuple[float, tuple[str, ...]]:
    args = dict(candidate.arguments or {})
    explicit_ids = {
        str(item) for key in ("goal_ids", "horizon_goal_ids", "affected_goal_ids")
        for item in tuple(args.get(key, ()) or ()) if str(item or "").strip()
    }
    semantic_tokens = _tokens(" ".join(str(args.get(key, "") or "") for key in (
        "semantic_target", "semantic_content", "candidate_kind", "speech_act", "source_owner"
    )))
    best = 0.0
    matched: list[str] = []
    for goal in snapshot.goals:
        if goal.status != "active" or goal.level not in {"H2", "H3", "H4"}:
            continue
        score = 0.0
        if goal.goal_id in explicit_ids:
            score = 1.0
        else:
            gt = _tokens(f"{goal.title} {goal.description} {' '.join(goal.tags)}")
            if gt and semantic_tokens:
                overlap = len(gt & semantic_tokens) / max(1, min(len(gt), len(semantic_tokens)))
                score = min(1.0, overlap * 1.25)
        score *= goal.strength
        if score >= 0.18:
            matched.append(goal.goal_id)
            best = max(best, score)
    return _clamp01(best), tuple(matched[:6])


class HorizonArbiter:
    def __init__(self, *, logger: Any | None = None) -> None:
        self.logger = logger

    def adjust_frame(
        self,
        frame: SubjectDeliberationFrame,
        snapshot: LifeHorizonSnapshot,
        *,
        courtesy_wait: bool = False,
    ) -> SubjectDeliberationFrame:
        """Trả frame mới cùng identity, chỉ thay score + annotation bounded."""
        active_higher = tuple(
            goal for goal in snapshot.goals
            if goal.status == "active" and goal.level in {"H2", "H3", "H4"}
        )
        active_h1 = tuple(
            goal for goal in snapshot.goals
            if goal.status == "active" and goal.level == "H1"
        )
        if not active_higher and not active_h1 and not courtesy_wait:
            return frame

        adjusted: list[CoalitionCandidate] = []
        any_alignment = False
        for candidate in frame.coalitions:
            action = str(candidate.action_kind or "").strip().lower()
            args = dict(candidate.arguments or {})
            speech_act = str(args.get("speech_act", "") or "").strip().lower()
            kind = str(args.get("candidate_kind", "") or "").strip().lower()
            score = float(candidate.score)
            alignment, matched = _goal_alignment(candidate, snapshot)
            any_alignment = any_alignment or alignment >= 0.22

            # H2-H4 chỉ là modulation nhẹ. Typed project contribution vẫn là tín
            # hiệu mạnh hơn và đã sống trong Subject proposal từ trước.
            if alignment > 0.0:
                score += 0.13 * alignment
            project_signal = _clamp01(args.get("project_contribution", 0.0))
            if project_signal > 0.0:
                score += 0.05 * project_signal

            # H1 foreground: repair/continuation của open-loop thắng một câu xã
            # giao không liên quan, nhưng vẫn không ép SPEAK nếu courtesy đang giữ lượt.
            if active_h1 and action == "speak":
                if speech_act in {"answer", "continuation", "causal_repair", "resume_shared_goal", "clarify"}:
                    score += 0.07 * max(goal.strength for goal in active_h1)
                elif kind in {"random_chat", "playful_prompt", "casual_curiosity"}:
                    score -= 0.06

            # Khi có mục tiêu đường dài thật, random banter không bị cấm, chỉ mất
            # một ít utility nếu hoàn toàn không đóng góp. Minimalism, không blacklist.
            if active_higher and action == "speak" and alignment < 0.12:
                if kind in {"random_chat", "social_check_in", "casual_curiosity", "playful_prompt"}:
                    score -= 0.035

            # Courtesy là hard boundary ở expression_commitment. Bonus silence ở
            # đây chỉ làm competition dễ đọc hơn khi frame chưa có commitment.
            if courtesy_wait:
                if action == "silence":
                    score += 0.26
                elif action == "speak":
                    score -= 0.34

            annotation = {
                "life_horizon_revision": snapshot.revision,
                "horizon_alignment": round(alignment, 4),
                "horizon_goal_ids": matched,
            }
            adjusted.append(replace(
                candidate,
                score=score,
                arguments={**args, **annotation},
            ))

        # Không có match thì vẫn giữ ưu tiên silence rất nhẹ trong autonomous khi
        # có H2-H4 active. Im lặng là một action hợp lệ, không phải lỗi no-op.
        if active_higher and not any_alignment and not courtesy_wait:
            remapped: list[CoalitionCandidate] = []
            for candidate in adjusted:
                if str(candidate.action_kind or "").strip().lower() == "silence":
                    remapped.append(replace(candidate, score=float(candidate.score) + 0.025))
                else:
                    remapped.append(candidate)
            adjusted = remapped

        return replace(frame, coalitions=tuple(adjusted))


__all__ = ["HorizonArbiter"]

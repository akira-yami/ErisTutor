# -*- coding: utf-8 -*-
"""V103 - workspace cạnh tranh ổn định cho semantic commitment tự thân.

V102 đã làm cho một ``ExpressionUrge`` phải sống qua thời gian trước khi trở
thành ``ExpressionCommitment``. Tuy nhiên khi *nhiều* commitment đã cùng tồn tại,
Neuro vẫn nhìn một snapshot của frame hiện tại và một chênh lệch score rất nhỏ có
thể đổi winner ngay ở tick kế tiếp.

V103 không tạo thêm motive, không viết câu và không cấp quyền SPEAK. Nó chỉ giữ
một dấu vết session-local của **public workspace occupancy**:

    nhiều ExpressionCommitment đã chín
        -> competition snapshot của Subject/Neuro
        -> ExpressionCompetitionWorkspaceV103
        -> winner ổn định để commit public

Nguyên tắc:
- chỉ áp dụng cho autonomous competition giữa các commitment có
  ``origin == expression_urge``;
- một commitment duy nhất KHÔNG bị thêm delay;
- structured continuation/task/capability lane không bị V103 sửa winner;
- incumbent không thắng vĩnh viễn: challenger phải duy trì lợi thế qua nhiều
  frame rồi mới takeover (hysteresis theo thời gian, không phải random tie-break);
- khi commitment đã được biểu đạt, receipt giải phóng occupancy nhưng public text
  không quay ngược thành cognition.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from typing import Any, Mapping

from cortex.subject.expression_commitment import read_commitment
from cortex.subject.neuro_control import SelectionResult


V103_SCHEMA = "eris.expression_competition_workspace.v103"
V103_OWNER = "expression_competition_workspace_v103"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def _compact(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _softmax(values: tuple[float, ...], beta: float = 6.0) -> tuple[float, ...]:
    if not values:
        return ()
    maximum = max(values)
    weights = [math.exp((float(item) - maximum) * beta) for item in values]
    total = sum(weights) or 1.0
    return tuple(item / total for item in weights)


def _entropy(probabilities: tuple[float, ...]) -> float:
    positive = [p for p in probabilities if p > 1e-12]
    if len(positive) <= 1:
        return 0.0
    raw = -sum(p * math.log(p) for p in positive)
    return _clamp01(raw / math.log(len(positive)))


@dataclass(slots=True)
class CommitmentCompetitionStateV103:
    """Dấu vết activation của một commitment khi cạnh tranh public workspace."""

    commitment_id: str
    source_id: str
    semantic_target: str
    activation: float = 0.0
    observations: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_seen_at: float = 0.0
    last_support: float = 0.0
    lead_count: int = 0
    takeover_count: int = 0
    expressed_count: int = 0

    def to_dict(self, *, now: float, occupant: bool) -> dict[str, Any]:
        return {
            "schema": V103_SCHEMA,
            "commitment_id": self.commitment_id,
            "source_id": self.source_id,
            "semantic_target": _compact(self.semantic_target, 280),
            "activation": round(_clamp01(self.activation), 4),
            "support": round(_clamp01(self.last_support), 4),
            "observations": int(self.observations),
            "occupant": bool(occupant),
            "lead_count": int(self.lead_count),
            "takeover_count": int(self.takeover_count),
            "expressed_count": int(self.expressed_count),
            "age_seconds": round(max(0.0, now - self.created_at), 2),
            "seconds_since_seen": round(max(0.0, now - self.last_seen_at), 2),
        }


class ExpressionCompetitionWorkspaceV103:
    """Ổn định competition giữa nhiều self-owned expression commitments.

    Đây là state của *public competition*, không phải global winner của private
    cognition. V98-V102 vẫn cho nhiều activation/focus/intention/commitment sống
    song song. V103 chỉ nhớ commitment nào đang chiếm action workspace khi Neuro
    buộc phải chọn một public action duy nhất.
    """

    # Challenger cần hơn incumbent một khoảng tích lũy nhỏ và giữ lợi thế ít nhất
    # hai observation. Dwell ngắn ngăn hai frame gần như đồng thời làm flip-flop.
    TAKEOVER_ACTIVATION_MARGIN = 0.08
    TAKEOVER_SUPPORT_MARGIN = 0.035
    TAKEOVER_OBSERVATIONS = 2
    MIN_OCCUPANCY_SECONDS = 1.0
    STATE_TTL_SECONDS = 150.0

    def __init__(self, *, logger: Any | None = None) -> None:
        self.logger = logger
        self._states: dict[str, CommitmentCompetitionStateV103] = {}
        self._occupant_id = ""
        self._occupant_since = 0.0
        self._challenger_id = ""
        self._challenger_streak = 0
        self._last_event = "idle"

    def _log(self, level: str, message: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            try:
                fn(message, *args)
            except Exception:
                pass

    @staticmethod
    def _is_autonomous(frame: Any) -> bool:
        observation = getattr(frame, "observation", None)
        kind = str(getattr(observation, "kind", "") or "").strip().lower()
        context = dict(getattr(observation, "context", {}) or {})
        context_key = str(context.get("context_key", "") or "").strip().lower()
        return kind == "autonomous_opportunity" or context_key in {"autonomous", "self_initiated"}

    @staticmethod
    def _commitments(frame: Any) -> dict[int, Any]:
        output: dict[int, Any] = {}
        for index, coalition in enumerate(tuple(getattr(frame, "coalitions", ()) or ())):
            if str(getattr(coalition, "action_kind", "") or "").strip().lower() != "speak":
                continue
            arguments = getattr(coalition, "arguments", {})
            args = arguments if isinstance(arguments, Mapping) else {}
            commitment = read_commitment(args)
            if commitment is not None:
                output[index] = commitment
        return output

    def _prune(self, now: float) -> None:
        stale = [
            key for key, state in self._states.items()
            if now - state.last_seen_at > self.STATE_TTL_SECONDS
        ]
        for key in stale:
            self._states.pop(key, None)
        if self._occupant_id and self._occupant_id not in self._states:
            self._occupant_id = ""
            self._occupant_since = 0.0
        if self._challenger_id and self._challenger_id not in self._states:
            self._challenger_id = ""
            self._challenger_streak = 0

    def _observe(
        self,
        commitments: dict[int, Any],
        selection: SelectionResult,
        *,
        now: float,
    ) -> tuple[dict[int, float], dict[int, float]]:
        indices = tuple(commitments)
        raw_probs = {
            index: (
                float(selection.probabilities[index])
                if index < len(selection.probabilities) else 0.0
            )
            for index in indices
        }
        prob_total = sum(max(0.0, value) for value in raw_probs.values())
        if prob_total > 1e-9:
            support = {
                index: _clamp01(max(0.0, value) / prob_total)
                for index, value in raw_probs.items()
            }
        else:
            local_scores = tuple(float(selection.scores[index]) for index in indices)
            local_probs = _softmax(local_scores, beta=6.0)
            support = dict(zip(indices, local_probs))

        activation: dict[int, float] = {}
        for index, commitment in commitments.items():
            key = str(commitment.commitment_id)
            state = self._states.get(key)
            if state is None:
                state = CommitmentCompetitionStateV103(
                    commitment_id=key,
                    source_id=str(commitment.source_id or ""),
                    semantic_target=str(commitment.semantic_target or ""),
                    activation=_clamp01(support.get(index, 0.0)),
                    observations=1,
                    created_at=now,
                    updated_at=now,
                    last_seen_at=now,
                    last_support=_clamp01(support.get(index, 0.0)),
                )
                self._states[key] = state
            else:
                elapsed = max(0.0, now - state.last_seen_at)
                # Memory competition decay chậm hơn một frame nhưng đủ ngắn để
                # commitment biến mất không ám workspace quá lâu.
                decay = 0.5 ** (elapsed / 24.0) if elapsed > 0.0 else 1.0
                prior = _clamp01(state.activation * decay)
                incoming = _clamp01(support.get(index, 0.0))
                state.activation = _clamp01(0.62 * prior + 0.38 * incoming)
                state.observations += 1
                state.updated_at = now
                state.last_seen_at = now
                state.last_support = incoming
                state.source_id = str(commitment.source_id or state.source_id)
                state.semantic_target = str(commitment.semantic_target or state.semantic_target)
            activation[index] = state.activation
        return support, activation

    def stabilize(
        self,
        frame: Any,
        selection: SelectionResult,
        *,
        source: str = "",
        now: float | None = None,
    ) -> SelectionResult:
        """Trả selection ổn định hơn nhưng giữ nguyên coalition/proposal identity.

        Fail-open design: bất kỳ frame nào không đúng phạm vi V103 đều trả lại
        ``selection`` nguyên bản. Vì vậy V103 không trở thành policy engine thứ hai.
        """

        current = float(now if now is not None else time.time())
        self._prune(current)
        if not self._is_autonomous(frame):
            return selection

        commitments = self._commitments(frame)
        if not commitments:
            return selection

        # V103 chỉ sở hữu lane expression_urge. Nếu frame có commitment structured
        # khác, không được dùng continuity bias để giành authority của lane đó.
        if any(str(item.origin or "") != "expression_urge" for item in commitments.values()):
            return selection

        support, activation = self._observe(commitments, selection, now=current)
        winner_index = int(selection.winner_index)
        if winner_index not in commitments:
            # Semantic critic / hard authority đã chọn non-speech. Không hồi sinh
            # public expression chỉ vì workspace từng có occupant.
            self._last_event = "non_speech_authority"
            return selection

        baseline_id = str(commitments[winner_index].commitment_id)
        present_ids = {str(item.commitment_id) for item in commitments.values()}

        if not self._occupant_id or self._occupant_id not in present_ids:
            self._occupant_id = baseline_id
            self._occupant_since = current
            self._challenger_id = ""
            self._challenger_streak = 0
            self._last_event = "occupy"
        elif baseline_id == self._occupant_id:
            self._challenger_id = ""
            self._challenger_streak = 0
            self._last_event = "incumbent_leads"
        else:
            id_to_index = {
                str(item.commitment_id): index for index, item in commitments.items()
            }
            incumbent_index = id_to_index.get(self._occupant_id)
            challenger_index = winner_index
            incumbent_activation = activation.get(incumbent_index, 0.0) if incumbent_index is not None else 0.0
            challenger_activation = activation.get(challenger_index, 0.0)
            incumbent_support = support.get(incumbent_index, 0.0) if incumbent_index is not None else 0.0
            challenger_support = support.get(challenger_index, 0.0)
            sustained_advantage = bool(
                challenger_activation >= incumbent_activation + self.TAKEOVER_ACTIVATION_MARGIN
                and challenger_support >= incumbent_support + self.TAKEOVER_SUPPORT_MARGIN
            )
            if sustained_advantage:
                if self._challenger_id == baseline_id:
                    self._challenger_streak += 1
                else:
                    self._challenger_id = baseline_id
                    self._challenger_streak = 1
                state = self._states.get(baseline_id)
                if state is not None:
                    state.lead_count += 1
            else:
                self._challenger_id = ""
                self._challenger_streak = 0

            occupied_for = max(0.0, current - self._occupant_since)
            if (
                sustained_advantage
                and self._challenger_streak >= self.TAKEOVER_OBSERVATIONS
                and occupied_for >= self.MIN_OCCUPANCY_SECONDS
            ):
                old = self._occupant_id
                self._occupant_id = baseline_id
                self._occupant_since = current
                self._challenger_id = ""
                self._challenger_streak = 0
                state = self._states.get(baseline_id)
                if state is not None:
                    state.takeover_count += 1
                self._last_event = "takeover"
                self._log(
                    "info",
                    "[expression workspace v103] takeover old=%s new=%s source=%s",
                    old,
                    baseline_id,
                    source,
                )
            else:
                self._last_event = "hold_incumbent"

        id_to_index = {str(item.commitment_id): index for index, item in commitments.items()}
        occupant_index = id_to_index.get(self._occupant_id)
        if occupant_index is None or occupant_index == selection.winner_index:
            reason = f"v103:{self._last_event}:{selection.reason}"
            return replace(selection, reason=reason)

        # Giữ score gốc làm forensic, chỉ thêm một continuity gain nhỏ để output
        # diagnostics phản ánh việc workspace đang giữ incumbent. Winner cuối vẫn
        # là runtime identity cũ của frame; không dựng proposal/coalition mới.
        adjusted_scores = list(selection.scores)
        if occupant_index < len(adjusted_scores):
            adjusted_scores[occupant_index] += 0.10 + 0.10 * activation.get(occupant_index, 0.0)
        score_tuple = tuple(adjusted_scores)
        eligible = tuple(commitments)
        local_probs = _softmax(tuple(score_tuple[index] for index in eligible), beta=6.0)
        probability_map = dict(zip(eligible, local_probs))
        probabilities = tuple(
            probability_map.get(index, 0.0) for index in range(len(score_tuple))
        )
        runner_scores = [
            score_tuple[index] for index in eligible if index != occupant_index
        ]
        runner = max(runner_scores) if runner_scores else score_tuple[occupant_index]
        margin = score_tuple[occupant_index] - runner
        return SelectionResult(
            winner_index=occupant_index,
            scores=score_tuple,
            probabilities=probabilities,
            margin=margin,
            entropy=_entropy(probabilities),
            requires_deliberation=False,
            parallel_review_recommended=False,
            reason=f"v103:hold_incumbent:{selection.reason}",
            broadcast=selection.broadcast,
        )

    def note_expressed(self, source_id: str, *, now: float | None = None) -> bool:
        """Receipt semantic-only sau public expression; không nhận public wording."""

        current = float(now if now is not None else time.time())
        source = str(source_id or "").strip()
        if not source:
            return False
        matched = False
        for state in self._states.values():
            if state.source_id != source:
                continue
            state.expressed_count += 1
            state.activation = min(state.activation, 0.12)
            state.updated_at = current
            matched = True
            if self._occupant_id == state.commitment_id:
                self._occupant_id = ""
                self._occupant_since = 0.0
                self._challenger_id = ""
                self._challenger_streak = 0
                self._last_event = "released_after_expression"
        return matched

    def snapshot(self, *, now: float | None = None, limit: int = 8) -> tuple[dict[str, Any], ...]:
        current = float(now if now is not None else time.time())
        self._prune(current)
        ranked = sorted(
            self._states.values(),
            key=lambda item: (
                item.commitment_id != self._occupant_id,
                -item.activation,
                -item.last_seen_at,
            ),
        )
        return tuple(
            item.to_dict(now=current, occupant=item.commitment_id == self._occupant_id)
            for item in ranked[: max(0, int(limit))]
        )

    def status(self, *, now: float | None = None) -> dict[str, Any]:
        current = float(now if now is not None else time.time())
        return {
            "schema": V103_SCHEMA,
            "owner": V103_OWNER,
            "occupant_commitment_id": self._occupant_id,
            "occupant_age_seconds": round(max(0.0, current - self._occupant_since), 2)
            if self._occupant_since else 0.0,
            "challenger_commitment_id": self._challenger_id,
            "challenger_streak": self._challenger_streak,
            "last_event": self._last_event,
            "active_count": len(self._states),
        }


__all__ = [
    "V103_SCHEMA",
    "V103_OWNER",
    "CommitmentCompetitionStateV103",
    "ExpressionCompetitionWorkspaceV103",
]

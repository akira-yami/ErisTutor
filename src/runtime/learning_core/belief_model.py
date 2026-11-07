# -*- coding: utf-8 -*-
"""Learner Belief V1: phép chiếu BKT *thử nghiệm* từ evidence có nguồn.

KHÔNG phải bài kiểm tra IQ hay xác suất đã được hiệu chỉnh trên dữ liệu học sinh.
Mỗi lần Root gọi Tutor, LearningService mới có quyền cập nhật trạng thái này.
LLM chỉ được đề xuất evidence qua semantic gate, không được ghi ``p_known``.

BKT áp dụng cho acquisition (đã từng nắm kiến thức); lịch ôn / khả năng nhớ
lại ở thời điểm hiện tại vẫn do scheduler sở hữu. Giữ hai khái niệm tách biệt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from runtime.learning_core.contracts import LearningEvidence
from runtime.learning_core.records import independent as is_independent, evidence_metadata


@dataclass(frozen=True, slots=True)
class LearnerBeliefState:
    kp_id: str
    p_known: float = 0.20
    uncertainty: float = 1.0
    observed_count: int = 0
    independent_count: int = 0
    transfer_observed: bool = False
    needs_probe: bool = True
    evidence_seq: int = 0
    model_version: str = "bkt-shadow-v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kp_id": self.kp_id,
            "p_known": round(self.p_known, 5),
            "uncertainty": round(self.uncertainty, 5),
            "observed_count": self.observed_count,
            "independent_count": self.independent_count,
            "transfer_observed": self.transfer_observed,
            "needs_probe": self.needs_probe,
            "evidence_seq": self.evidence_seq,
            "model_version": self.model_version,
            # Không cho Root diễn giải heuristic như ước lượng đã hiệu chỉnh.
            "calibrated": False,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "LearnerBeliefState":
        return cls(
            kp_id=str(raw.get("kp_id", "")),
            p_known=float(raw.get("p_known", 0.20)),
            uncertainty=float(raw.get("uncertainty", 1.0)),
            observed_count=int(raw.get("observed_count", 0)),
            independent_count=int(raw.get("independent_count", 0)),
            transfer_observed=bool(raw.get("transfer_observed", False)),
            needs_probe=bool(raw.get("needs_probe", True)),
            evidence_seq=int(raw.get("evidence_seq", 0)),
            model_version=str(raw.get("model_version", "bkt-shadow-v1")),
        )


# Prior khởi động có chủ ý bảo thủ, chưa fit/calibrate trên dữ liệu học thật.
# Self-report/exposure không phải phép thử năng lực và KHÔNG đi vào BKT.
_QUALITY = {
    "quiz_correct": 1.0,
    "quiz_incorrect": 1.0,
    "feynman_pass": 0.90,
    "feynman_fail": 0.85,
    "application": 0.75,
    "paraphrase": 0.55,
    "misconception": 0.75,
}


def compute_belief(
    kp_id: str,
    evidences: Sequence[LearningEvidence],
    *,
    prior: float = 0.20,
    guess: float = 0.18,
    slip: float = 0.12,
    learn: float = 0.05,
) -> LearnerBeliefState:
    """Replay thuần từ ledger: cùng evidence -> cùng kết quả.

    Mức ảnh hưởng của evidence được chiết khấu nếu nhiều câu dùng lại cùng
    interaction_id / turn_id. Đây là biện pháp chống đếm trùng ban đầu, KHÔNG
    giả vờ rằng các nhóm đó thực sự độc lập thống kê.
    """
    p = min(0.999, max(0.001, float(prior)))
    guess = min(0.95, max(0.01, float(guess)))
    slip = min(0.95, max(0.01, float(slip)))
    learn = min(0.25, max(0.0, float(learn)))
    seen: set[str] = set()
    seen_turns: set[str] = set()
    independent = 0
    transfer = False
    usable = 0
    ordered = sorted(evidences, key=lambda ev: (ev.recorded_at, ev.evidence_id))
    for ev in ordered:
        quality = _QUALITY.get(ev.evidence_type, 0.0)
        if quality <= 0.0 or not is_independent(ev):
            continue
        usable += 1
        # Khi interaction ID không có, lượt nói tạo ra một evidence-family.
        # Legacy thiếu cả hai: mỗi evidence ID là một family, nhưng không khẳng
        # định rằng mỗi quan sát đã được kiểm thử độc lập.
        family = evidence_metadata(ev).get("item_family_id") or ev.interaction_id or ev.turn_id or ev.evidence_id
        repeated = family in seen or bool(ev.turn_id and ev.turn_id in seen_turns)
        if not repeated:
            independent += 1
            seen.add(family)
            if ev.turn_id:
                seen_turns.add(ev.turn_id)
        weight = quality * (0.20 if repeated else 1.0)
        if ev.evidence_type in {"application", "feynman_pass"} and ev.correct and not repeated:
            transfer = True
        # Fractional likelihood: không thay đổi probability khi quality=0.
        likelihood_known = (1.0 - slip) if ev.correct else slip
        likelihood_unknown = guess if ev.correct else (1.0 - guess)
        logit = math.log(p / (1.0 - p)) + weight * math.log(likelihood_known / likelihood_unknown)
        p_observed = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit))))
        # Exposure không được tính là "đã học"; transition nhỏ là prior BKT.
        p = min(0.999, max(0.001, p_observed + (1.0 - p_observed) * learn * weight))

    # Uncertainty này là tín hiệu bảo thủ cho Root, KHÔNG phải credible interval.
    coverage = min(1.0, independent / 4.0)
    contradiction = 4.0 * p * (1.0 - p)
    uncertainty = min(1.0, max(0.0, 0.65 * (1.0 - coverage) + 0.35 * contradiction))
    return LearnerBeliefState(
        kp_id=kp_id,
        p_known=p,
        uncertainty=uncertainty,
        observed_count=usable,
        independent_count=independent,
        transfer_observed=transfer,
        needs_probe=(independent < 3 or not transfer or uncertainty > 0.40),
        evidence_seq=len(ordered),
    )


__all__ = ["LearnerBeliefState", "compute_belief"]

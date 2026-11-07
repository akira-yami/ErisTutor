# -*- coding: utf-8 -*-
"""V70 - Rumination / cognitive attractor dynamics cho ERIS.

Module này cố ý KHÔNG có ``ObsessionManager`` hay ``ban_topic``. Một concern chỉ
có thêm "trọng lực nhận thức": nó có thể kéo attention quay lại dù nhu cầu nói
ra đã được consummate. Đây là khác biệt giữa:

    "mình vẫn bị mắc ở ý này"  !=  "mình phải nói ý này thêm lần nữa"

Public speech vẫn thuộc ExpressionCommitment/Neuro. Rumination chỉ tác động vào
private persistence và quyền *tạo material mới*. Muốn biểu đạt lại, material phải
phát triển semantic thật (deepen/contradict/connect/reinterpret/test/personalize)
hoặc có evidence/user reopen hợp lệ.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    if math.isnan(number) or math.isinf(number):
        number = default
    return max(0.0, min(1.0, number))


_STOP = {
    "toi", "minh", "ban", "eris", "mot", "nhung", "cac", "cua", "voi", "cho",
    "trong", "khi", "thi", "ma", "la", "co", "khong", "duoc", "nay", "do",
    "this", "that", "the", "and", "for", "from", "with", "about", "user",
}


def _fold(value: Any) -> str:
    text = str(value or "").lower().replace("đ", "d")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def semantic_tokens(value: Any) -> frozenset[str]:
    return frozenset(
        token for token in re.findall(r"[a-z0-9]+", _fold(value))
        if len(token) >= 3 and token not in _STOP
    )


def semantic_overlap(left: Any, right: Any) -> float:
    a, b = semantic_tokens(left), semantic_tokens(right)
    if not a or not b:
        return 0.0
    shared = len(a & b)
    return clamp01(shared / max(1, min(len(a), len(b)))) if shared else 0.0


RUMINATION_DYNAMICS_VERSION = "eris.rumination.v70.cognitive_attractor"
DEVELOPMENT_MODES = frozenset({
    "deepen", "contradict", "connect", "reinterpret", "test", "personalize",
})

_CONTRAST_OR_TEST_RE = re.compile(
    r"(?:\bnhưng\b|\btuy\b|\bkhông\s+hẳn\b|\bliệu\b|\bnếu\b|\bngược\b|"
    r"\bbut\b|\bhowever\b|\bwhether\b|\bif\b|\bcontrary\b|\btest\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RuminationState:
    stickiness: float = 0.0
    attractor_strength: float = 0.0
    rumination_awareness: float = 0.0
    private_return_pull: float = 0.0
    development_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key, value in tuple(data.items()):
            if isinstance(value, float):
                data[key] = round(value, 3)
        return data


def derive_rumination_state(
    *,
    pressure: float,
    uncertainty: float,
    recurrence: int,
    mutation_count: int,
    private_return_count: int,
    echo_streak: int,
    habituation: float,
    epistemic_completion: float,
    expressive_completion: float,
    relational_completion: float,
    stored_stickiness: float = 0.0,
) -> RuminationState:
    """Suy ra độ "mắc" từ state có thật, không từ timer hay keyword topic.

    ``stickiness`` không tăng speech pressure. Nó chỉ làm concern khó rơi khỏi
    private attention hơn. ``rumination_awareness`` tăng khi Eris đã quay lại cùng
    concern nhiều lần; awareness làm attractor dịu một chút nhưng không xóa nó.
    Con người biết mình đang nghĩ vòng cũng đâu có nút kill - tiếc thay.
    """

    pressure = clamp01(pressure)
    uncertainty = clamp01(uncertainty, 0.5)
    habituation = clamp01(habituation)
    epistemic_completion = clamp01(epistemic_completion)
    expressive_completion = clamp01(expressive_completion)
    relational_completion = clamp01(relational_completion)
    stored_stickiness = clamp01(stored_stickiness)

    recurrence_term = min(1.0, math.log1p(max(1, int(recurrence))) / math.log(8.0))
    mutation_term = min(1.0, max(0, int(mutation_count)) / 3.0)
    return_term = min(1.0, max(0, int(private_return_count)) / 5.0)
    echo_term = min(1.0, max(0, int(echo_streak)) / 4.0)
    epistemic_residual = clamp01(1.0 - epistemic_completion)

    derived_stickiness = clamp01(
        0.20
        + 0.17 * recurrence_term
        + 0.17 * uncertainty
        + 0.12 * pressure
        + 0.17 * epistemic_residual
        + 0.10 * return_term
        + 0.05 * mutation_term
    )
    # Stored stickiness là "tính lì" đã hình thành qua private returns. Nó không
    # được pump vô hạn: ceiling mềm 0.88 để ám ảnh nhẹ vẫn là attractor, không là
    # độc tài nhận thức.
    stickiness = min(0.88, max(derived_stickiness, stored_stickiness * 0.985))

    awareness = clamp01(
        0.04
        + 0.25 * return_term
        + 0.18 * habituation
        + 0.14 * expressive_completion
        + 0.12 * echo_term
        + 0.07 * relational_completion
    )

    attractor = clamp01(
        stickiness
        * (0.46 + 0.54 * epistemic_residual)
        * (0.58 + 0.42 * max(pressure, uncertainty))
        * (1.0 - 0.18 * awareness)
    )
    private_pull = clamp01(
        0.52 * attractor
        + 0.28 * epistemic_residual
        + 0.20 * stickiness
    )
    development_required = bool(
        expressive_completion >= 0.72
        or (awareness >= 0.48 and private_return_count >= 2)
    )
    return RuminationState(
        stickiness=stickiness,
        attractor_strength=attractor,
        rumination_awareness=awareness,
        private_return_pull=private_pull,
        development_required=development_required,
    )


def semantic_development_score(parent: str, child: str, mode: str) -> float:
    """Điểm deterministic cho việc một thought *phát triển* thay vì paraphrase.

    Model chỉ được đề xuất ``mode``. Runtime vẫn kiểm tra semantic delta. Score
    này không chứng minh thought đúng; nó chỉ chứng minh thought không phải cùng
    nội dung được mặc một cái áo từ vựng khác.
    """

    mode = str(mode or "").strip().lower()
    if mode not in DEVELOPMENT_MODES:
        return 0.0
    parent_tokens = semantic_tokens(parent)
    child_tokens = semantic_tokens(child)
    if not parent_tokens or not child_tokens:
        return 0.0
    overlap = semantic_overlap(parent, child)
    new_tokens = child_tokens - parent_tokens
    new_term = clamp01(len(new_tokens) / 5.0)

    # Một development phải còn liên quan đến parent. "Đổi sang món ăn" không
    # phải deepen cho concern về ký ức chỉ vì nó có nhiều token mới.
    if overlap < 0.12:
        return 0.0
    # Cùng gần như toàn bộ concept và chỉ thay 1 cụm 2-3 từ thường là paraphrase,
    # không phải một bước phát triển. Contradict/test là ngoại lệ vì chúng có thể
    # dùng lại chính vocabulary cũ để phủ định/kiểm thử giả thuyết.
    if mode not in {"contradict", "test"} and overlap >= 0.88 and len(new_tokens) <= 3:
        return 0.0

    if mode in {"contradict", "test"}:
        if not _CONTRAST_OR_TEST_RE.search(str(child or "")) or len(new_tokens) < 1:
            return 0.0
        mode_base = 0.62
    else:
        # ``deepen`` dễ bị model dùng như nhãn sang cho paraphrase nhất, nên đòi
        # ít nhất 3 concept token mới. Các mode còn lại cần 2 token mới.
        min_new = 3 if mode == "deepen" else 2
        if len(new_tokens) < min_new:
            return 0.0
        mode_base = {
            "deepen": 0.58,
            "connect": 0.55,
            "reinterpret": 0.64,
            "personalize": 0.60,
        }.get(mode, 0.55)

    # Overlap quá cao + gần như không có token mới vẫn bị coi là restatement.
    repetition_penalty = 0.20 * max(0.0, overlap - 0.82) / 0.18
    return clamp01(mode_base + 0.24 * new_term + 0.10 * (1.0 - overlap) - repetition_penalty)


__all__ = [
    "RUMINATION_DYNAMICS_VERSION",
    "DEVELOPMENT_MODES",
    "RuminationState",
    "derive_rumination_state",
    "semantic_development_score",
]

# -*- coding: utf-8 -*-
"""Phase 1 - Theory of Mind Worker.

UserModelerAgent chỉ tạo giả thuyết về user. Nó không được nói trực tiếp, không
được an ủi, không được phán tâm lý như thầy bói cầm stack trace. Output của nó là
ThoughtFragment cho Blackboard/Synthesizer xử tiếp.
"""

from __future__ import annotations

from typing import Iterable

from ..thought_fragment import ThoughtFragment, clamp01
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import (
    RELATIONAL_TERMS,
    TECHNICAL_TERMS,
    collect_texts,
    context_evidence_ids,
    contains_any,
    kind_of_request,
    relation_level,
    safe_get,
    score_terms,
    user_text_from_context,
)


class UserModelerAgent(RuleBasedWorker):
    """Sinh các hypothesis nhẹ về intent/focus/risk của user.

    Các fragment đều `surface_allowed=False`. Đây là dữ liệu cho arbitration, không
    phải câu thoại. Một ToM worker mà tự nói ra suy diễn của mình thì chỉ là drama
    có type hint.
    """

    name = "user_modeler"

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        text = user_text_from_context(context)
        evidence_ids = context_evidence_ids(context, fallback_prefix="tom")
        if not text and not evidence_ids:
            return ()

        now = float(context.now or 0.0) or None
        request_kind = kind_of_request(text)
        technical_score = score_terms(text, TECHNICAL_TERMS)
        relational_score = score_terms(text, RELATIONAL_TERMS)
        fragments: list[ThoughtFragment] = []

        intent_confidence = 0.42 + (0.28 if text else 0.0) + min(0.20, technical_score * 0.25)
        if request_kind == "implementation_request":
            intent_content = "User đang yêu cầu triển khai/sửa trực tiếp, ưu tiên kết quả chạy được hơn giải thích dài."
            urgency = 0.72
        elif request_kind == "diagnostic_analysis_request":
            intent_content = "User đang cần chẩn đoán có chuỗi dữ kiện, nên trả lời theo giả thuyết mạnh nhất trước."
            urgency = 0.58
        elif request_kind == "clarification_or_question":
            intent_content = "User đang hỏi hoặc cần làm rõ một điểm cụ thể; nên tránh mở rộng quá xa khỏi câu hỏi."
            urgency = 0.50
        else:
            intent_content = "User đang tiếp tục mạch hiện tại; nên giữ continuity và không reset ngữ cảnh."
            urgency = 0.44

        fragments.append(
            ThoughtFragment.create(
                source_agent=self.name,
                kind="user_intent_hypothesis",
                content=intent_content,
                evidence_ids=evidence_ids,
                confidence=clamp01(intent_confidence),
                urgency=urgency,
                affect_charge=0.18 + relational_score * 0.25,
                privacy_level="normal",
                surface_allowed=False,
                ttl_seconds=240,
                tags=("theory_of_mind", "user_intent", request_kind),
                now=now,
            )
        )

        if technical_score > 0.0 or contains_any(text, TECHNICAL_TERMS):
            focus_content = "User focus hiện nghiêng về dev/architecture; interruption cost tăng nếu ERIS chen micro-talk không giúp task."
            focus_confidence = 0.55 + technical_score * 0.35
            focus_tags = ("theory_of_mind", "user_focus", "technical_focus")
        elif relational_score > 0.0:
            focus_content = "User focus hiện nghiêng về nhịp quan hệ/hiện diện; phản hồi quá kỹ thuật có thể lệch nhu cầu."
            focus_confidence = 0.48 + relational_score * 0.35
            focus_tags = ("theory_of_mind", "user_focus", "relational_focus")
        else:
            focus_content = "User focus chưa đủ rõ; nên giữ phản hồi grounded và không overread."
            focus_confidence = 0.40
            focus_tags = ("theory_of_mind", "user_focus", "ambiguous_focus")

        fragments.append(
            ThoughtFragment.create(
                source_agent=self.name,
                kind="user_focus_hypothesis",
                content=focus_content,
                evidence_ids=evidence_ids,
                confidence=clamp01(focus_confidence),
                urgency=0.42 + technical_score * 0.20,
                affect_charge=0.12 + relational_score * 0.20,
                privacy_level="normal",
                surface_allowed=False,
                ttl_seconds=210,
                tags=focus_tags,
                now=now,
            )
        )

        # Interruption risk dùng cả focus kỹ thuật, trạng thái relation và dấu hiệu user muốn làm việc.
        trust = relation_level(context, "trust", default=0.45)
        rupture = relation_level(context, "rupture", default=0.0)
        risk = clamp01(0.18 + technical_score * 0.45 + rupture * 0.22 - trust * 0.10)
        fragments.append(
            ThoughtFragment.create(
                source_agent=self.name,
                kind="interruption_risk",
                content=f"Interruption risk tạm ước lượng {risk:.2f}; ưu tiên nói khi giúp trực tiếp hoặc khi user gọi rõ.",
                evidence_ids=evidence_ids,
                confidence=0.46 + min(0.30, technical_score * 0.35 + rupture * 0.20),
                urgency=risk,
                affect_charge=0.10 + rupture * 0.30,
                privacy_level="normal",
                surface_allowed=False,
                ttl_seconds=180,
                tags=("theory_of_mind", "interruption_risk"),
                now=now,
            )
        )

        # Relational projection chỉ xuất hiện khi có tín hiệu quan hệ đáng kể, để tránh đọc mặt người qua hơi thở.
        if relational_score >= 0.20 or rupture >= 0.20 or relation_level(context, "repair_need", 0.0) >= 0.25:
            repair_need = relation_level(context, "repair_need", default=0.0)
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="relational_projection",
                    content=(
                        "Có tín hiệu relational; nên giảm diễn giải tâm lý trực diện, giữ phẩm giá và repair nhẹ nếu cần."
                    ),
                    evidence_ids=evidence_ids,
                    confidence=clamp01(0.42 + relational_score * 0.35 + repair_need * 0.25),
                    urgency=clamp01(0.30 + rupture * 0.30 + repair_need * 0.30),
                    affect_charge=clamp01(0.25 + relational_score * 0.35 + rupture * 0.20),
                    privacy_level="private",
                    surface_allowed=False,
                    ttl_seconds=240,
                    tags=("theory_of_mind", "relational_projection"),
                    now=now,
                )
            )

        # Nếu snapshot có recent events kiểu typing/build_error, lưu thêm attention hypothesis.
        recent_events = collect_texts(safe_get(context.snapshot, "recent_events"), limit=3)
        if recent_events:
            joined = "; ".join(recent_events[:2])
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="user_attention_state",
                    content=f"Recent event hint cho attention state: {joined}",
                    evidence_ids=evidence_ids or tuple(f"event.{idx}" for idx, _ in enumerate(recent_events[:2])),
                    confidence=0.44,
                    urgency=0.34,
                    affect_charge=0.08,
                    privacy_level="normal",
                    surface_allowed=False,
                    ttl_seconds=180,
                    tags=("theory_of_mind", "attention_state"),
                    now=now,
                )
            )

        return tuple(fragments)

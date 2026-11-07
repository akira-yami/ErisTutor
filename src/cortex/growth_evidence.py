# -*- coding: utf-8 -*-
"""growth_evidence.py — phiên dịch ký-ức-thật → GrowthObservation.

Đây là "bộ phiên dịch" cho Tác_Tử_Nền: lấy feedback đã được ghi vào
``PersistentAgentMindStore.last_feedback_events`` (bằng chứng THẬT, có timestamp)
và chuyển một SỐ ÍT tín hiệu RÕ NGHĨA thành quan sát trên Trục_Persona.

Cố ý bảo thủ (không map ẩu): chỉ map các topic mà hướng trục không mơ hồ.
Tín hiệu chung chung (chỉ "positive" mà không topic) → KHÔNG sinh quan sát, để
phát triển không trôi theo cảm xúc nhất thời. Mỗi quan sát mang ``evidence_ids``
trỏ về event thật và ``session_id`` gom theo ngày (đa phiên qua thời gian).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .growth_consolidator import GrowthObservation


_DAY_SECONDS = 86400.0

# Ánh xạ RÕ NGHĨA topic(+sentiment) → các (axis, direction). Độ lớn nhỏ; consolidator
# sẽ trung bình theo phiên và kẹp lại. Bỏ qua topic không gắn với trục persona
# (camera_voice = giọng, technical_help = năng lực — không phải warmth/distance).
def _rules_for(topic: str, sentiment: str) -> list[tuple[str, float]]:
    if topic == "more_life":
        if sentiment == "positive":
            return [("warmth_temperature", 0.04), ("read_boldness", 0.03)]
        if sentiment == "negative":
            return [("warmth_temperature", -0.03)]
        return []
    if topic == "intrusive_question":
        # bạn thấy bị hỏi dồn → bớt dám-đọc/chen, giữ khoảng cách hơn một chút.
        return [("read_boldness", -0.04), ("preferred_distance", 0.03)]
    if topic == "silence_too_long":
        # bạn thấy im quá lâu → nhích lại gần hơn, lộ gắn bó hơn một chút.
        return [("preferred_distance", -0.03), ("attachment_visibility", 0.02)]
    return []


def _kind_for(sentiment: str) -> str:
    # Feedback tiêu cực = một lần bạn sửa hành vi → trọng số cao hơn (R1.5).
    return "user_correction" if sentiment == "negative" else "repeated_preference"


def observations_from_feedback(
    events: Iterable[Mapping[str, Any]],
) -> tuple[list[GrowthObservation], set[str]]:
    """Trả (observations, allowed_evidence_ids). ``allowed`` là tập id THẬT để cổng
    kiểm chứng (R1.3); chính là các id sinh từ event có thật."""
    observations: list[GrowthObservation] = []
    allowed: set[str] = set()
    for event in events or ():
        if not isinstance(event, Mapping):
            continue
        try:
            ts = float(event.get("ts", 0.0) or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        sentiment = str(event.get("sentiment") or "neutral")
        topics = event.get("topics") or ()
        if not isinstance(topics, (list, tuple)):
            continue
        evidence_id = f"feedback:{int(ts)}"
        session_id = f"day:{int(ts // _DAY_SECONDS)}"
        kind = _kind_for(sentiment)
        produced = False
        for topic in topics:
            for axis, direction in _rules_for(str(topic), sentiment):
                observations.append(
                    GrowthObservation(
                        axis=axis,
                        direction=direction,
                        evidence_ids=(evidence_id,),
                        session_id=session_id,
                        kind=kind,
                    )
                )
                produced = True
        if produced:
            allowed.add(evidence_id)
    return observations, allowed

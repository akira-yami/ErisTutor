# -*- coding: utf-8 -*-
"""Chiếu bằng chứng tương tác cho Root, KHÔNG chọn hành vi thay Root.

EQ ở đây là phán đoán có ngữ cảnh: runtime chỉ chuyển tiếp lời đã phát,
phản hồi thật và nhận định *có thể sai* của observer. Không đoán cảm xúc theo
bảng từ khóa, không sinh chỉ thị «buồn thì an ủi», không tạo agent/LLM mới.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence


_INVALID_DELIVERY = frozenset({
    "preparing", "pending", "queued_for_audio", "delivering",
    "failed_before_delivery", "delivery_failed", "cancelled", "unconfirmed_after_restart",
})


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


# P3 — Các từ nối thông dụng không đủ để kết luận hai tình huống giống nhau.
# Đây là bộ tìm *bằng chứng liên quan* bằng từ chung, không dự đoán cảm xúc,
# không phân loại ý định và không quyết định Root phải ứng xử thế nào.
_COMMON = frozenset({
    "tôi", "mình", "bạn", "em", "anh", "chị", "cậu", "này", "đó", "kia", "vậy", "thế",
    "đang", "lại", "được", "không", "chưa", "một", "những", "của", "với", "cho", "nhưng",
    "rồi", "nữa", "trong", "vừa", "người", "điều", "việc", "cái", "nào", "đâu", "làm",
    "sao", "thì", "mà", "khi", "cũng", "hay", "cần", "muốn", "để", "chỉ", "nói", "hỏi",
    "chuyện", "khác", "giờ", "thôi", "sang", "chuyển", "ngay", "như", "lúc",
    "user", "the", "and", "that", "this", "with", "from", "just", "your", "you", "was",
    "were", "they", "their", "what", "have", "has", "are", "for", "not", "but", "why",
})


def _terms(value: Any, *, limit: int = 1200) -> set[str]:
    # Không embedding/LLM để tránh thêm latency và nhầm một suy diễn thành fact.
    # Giữ cả chữ Unicode có dấu; loại từ ngắn/noise và giới hạn chi phí CPU.
    return {
        term for term in re.findall(r"[^\W_]+", _text(value, limit).casefold(), re.UNICODE)
        if len(term) >= 3 and term not in _COMMON
    }


def _relevance(current: set[str], situation: str, goal: str, reaction: str) -> tuple[float, list[str]]:
    """Ước lượng độ liên quan TỪ VỰNG, không phải xác suất hợp tính cách.

    Phản ứng user từng được ghi nhận là bằng chứng về ngữ cảnh trước, không
    dùng phán quyết observer hay chữ trong câu Eris nói để ép lời hiện tại.
    """
    candidate = _terms(" ".join((situation, goal, reaction)))
    common = current & candidate
    if not common or not current or not candidate:
        return 0.0, []
    score = len(common) / math.sqrt(len(current) * len(candidate))
    return score, sorted(common)[:5]


def _past_interaction_evidence(
    state: Any, *, session_id: str, current_text: str = "", limit: int = 2,
    allow_same_session_fallback: bool = True,
) -> list[dict[str, Any]]:
    """Lấy trường hợp có phản ứng *thật* và phù hợp ngữ cảnh.

    Ưu tiên tương đồng giữa câu hiện tại và tình huống cũ. Một trường hợp
    cùng phiên nhưng không trùng ngữ cảnh chỉ là 'recent fallback' để Root
    cân nhắc; tuyệt đối không lấy ví dụ khác phiên hoàn toàn không liên quan.
    """
    if state is None or not getattr(state, "identity_learning_enabled", True):
        return []
    identity = getattr(state, "identity", None)
    strategies = getattr(identity, "strategies", None)
    if not isinstance(strategies, Mapping):
        return []
    current_terms = _terms(current_text)
    records: list[tuple[float, float, dict[str, Any]]] = []
    for strategy_name, record in strategies.items():
        # Tránh quét vô hạn khi state nhập từ file lớn/đã hỏng.
        for raw in tuple(getattr(record, "experiences", ()) or ())[-200:]:
            if not isinstance(raw, Mapping):
                continue
            assistant_ref = _text(raw.get("assistant_ref"), 100)
            reaction_ref = _text(raw.get("reaction_ref"), 180)
            reaction_text = _text(raw.get("reaction_text"), 300)
            assistant_text = _text(raw.get("assistant_text"), 300)
            if not assistant_ref or not reaction_ref or not reaction_text or not assistant_text:
                continue
            try:
                confidence = float(raw.get("confidence", 0.0) or 0.0)
                settled_at = float(raw.get("settled_at", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(confidence) or not math.isfinite(settled_at):
                continue
            if not 0.62 <= confidence <= 1.0:
                continue
            source = _text(raw.get("interpretation_source"), 80)
            if source != "post_turn_observer":
                continue
            past_session = _text(raw.get("session_id"), 180)
            if not past_session:
                continue
            same_session = past_session == session_id
            situation = _text(raw.get("situation"), 200)
            goal = _text(raw.get("goal"), 160)
            score, terms = _relevance(current_terms, situation, goal, reaction_text)
            # Ít nhất hai neo nội dung cho ví dụ khác phiên. Không dùng
            # 'bạn/tôi/hỏi' làm bằng chứng liên quan và không mặc định cross-session.
            matched = len(terms) >= (1 if same_session else 2) and score >= 0.12
            if not matched and (not same_session or not allow_same_session_fallback):
                continue
            item = {
                "strategy": _text(strategy_name, 100),
                "situation": situation,
                "goal": goal,
                "assistant_said": assistant_text,
                "user_replied": reaction_text,
                "outcome_hypothesis": _text(raw.get("outcome"), 60),
                "assessment_confidence": round(confidence, 2),
                "reaction_ref": reaction_ref,
                "scope": "same_session" if same_session else "past_session",
                "provenance": source,
                # Không dùng số 'EQ' làm tiêu chuẩn thưởng/phạt. Root được
                # thấy tại sao ví dụ xuất hiện và tự quyết có áp dụng không.
                "match_basis": "shared_context_terms" if matched else "recent_same_session_fallback",
                "shared_terms": terms if matched else [],
            }
            # Cùng nội dung mới có ưu tiên; độ mới/cùng phiên chỉ phá hòa.
            records.append((score if matched else -1.0, settled_at, item))

    records.sort(key=lambda row: (row[0], row[2]["scope"] == "same_session", row[1]), reverse=True)
    # P3 — Nếu đã có tình huống thực sự trùng ngữ cảnh, không thêm ví dụ
    # lạc đề chỉ để đủ quota. Chỉ một fallback cùng phiên khi hoàn toàn
    # không có match; ví dụ ấy được đánh dấu yếu để Root không quá tin.
    if any(rank >= 0.0 for rank, _, _ in records):
        records = [row for row in records if row[0] >= 0.0]
    else:
        records = records[:1]
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, _, item in records:
        ref = item["reaction_ref"]
        if ref in seen:
            continue
        selected.append(item)
        seen.add(ref)
        if len(selected) >= max(0, min(int(limit), 3)):
            break
    return selected


def _recent_dialogue_evidence(
    recent_history: Sequence[Mapping[str, Any]], *, current: str, session_id: str,
) -> dict[str, Any]:
    """Timeline lời THỰC SỰ trao đổi, không đoán arc hoặc quyết định topic.

    Bỏ lời assistant đang queue/delivering, kể cả khi nó nằm trong history.
    'unknown' không được lén đổi thành lời chắc chắn đã phát. Chỉ dùng
    bốn lượt trước để tránh prompt phình và nhắc lại toàn bộ transcript.
    """
    rows: list[dict[str, Any]] = []
    for line in tuple(recent_history)[-16:]:
        if not isinstance(line, Mapping):
            continue
        if line.get("session_id") and _text(line.get("session_id"), 180) != session_id:
            continue
        role = _text(line.get("role"), 30).lower()
        if role not in {"assistant", "user"}:
            continue
        excerpt = _text(line.get("raw_content", line.get("content")), 200)
        if not excerpt:
            continue
        status = _text(line.get("delivery_status"), 60).lower()
        if role == "assistant" and status != "delivered":
            continue
        # Nếu caller đã nối tin user hiện tại vào history, không kể hai lần.
        if role == "user" and excerpt == current[:200]:
            continue
        rows.append({"role": role, "excerpt": excerpt})
    if not rows:
        return {}
    return {
        "recent_turns": rows[-4:],
        "scope": "same_conversation_public_history",
        "interpretation": "Chronological excerpts only; current user request overrides earlier direction.",
    }


def project_social_situation(
    *,
    event_kind: str,
    user_text: str,
    session_id: str,
    state: Any = None,
    handoff: Mapping[str, Any] | None = None,
    recent_history: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Projection chỉ-đọc, chỉ cho user turn, không tự suy diễn ý định.

    event.text và history đã chứa hội thoại gốc; ở đây chỉ cung cấp neo
    thời gian/provenance và vài trường hợp đã có phản ứng thật.
    """
    current = _text(user_text, 32000)
    if event_kind != "user_turn" or not current:
        return {}
    result: dict[str, Any] = {
        "schema": "eris.social_situation.evidence.v2",
        "authority": "evidence_only_root_decides",
    }
    # Dựa vào history gần nhất đã công khai, không coi bản chuẩn bị phát là lời
    # Eris đã nói. Câu không có delivery_status được ghi là chưa xác nhận.
    for line in reversed(tuple(recent_history)[-12:]):
        if not isinstance(line, Mapping) or str(line.get("role", "")).lower() != "assistant":
            continue
        delivery_status = _text(line.get("delivery_status"), 60).lower()
        if delivery_status in _INVALID_DELIVERY:
            continue
        result["previous_assistant"] = {
            "excerpt": _text(line.get("raw_content", line.get("content", "")), 240),
            "delivery_status": delivery_status or "unknown",
            "delivery_confirmed": delivery_status == "delivered",
        }
        break

    # Handoff là bộ quan sát của chính lượt hiện tại. Chặn snapshot cũ lọt
    # sang lượt mới khi callback chưa đồng bộ; các nhãn là giả thuyết, không
    # phải phán quyết về cảm xúc hay hành vi cần chọn.
    observation = handoff if isinstance(handoff, Mapping) else {}
    observed_text = _text(observation.get("last_user_text"), 520)
    if observed_text and observed_text == current[:520]:
        hypotheses = observation.get("causal_hypotheses", ())
        top = next((x for x in hypotheses if isinstance(x, Mapping)), None) if isinstance(
            hypotheses, (list, tuple)
        ) else None
        result["current_handoff"] = {
            "user_stance_observed": _text(observation.get("user_stance"), 60),
            "repair_signal": bool(observation.get("repair_needed", False)),
            "explicit_topic_closure": bool(observation.get("topic_closed", False)),
            "previous_assistant_ref": _text(observation.get("last_assistant_turn_ref"), 120),
            "hypothesis": {
                "kind": _text(top.get("kind"), 80),
                "summary": _text(top.get("summary"), 180),
            } if top else {},
        }

    # P3 — Timeline không tạo trạng thái hội thoại cứng; Root tự nhận ra
    # câu nói đang tiếp chuyện, đổi mục đích hay đặt ranh giới mới.
    flow = _recent_dialogue_evidence(recent_history, current=current, session_id=session_id)
    if flow:
        result["conversation_flow"] = flow

    # P3 — Handoff tường minh có thể là lời sửa cách tiếp cận, không chỉ là
    # cảm xúc. Không ép xin lỗi; user có thể chỉ đang phản biện/troll.
    handoff_evidence = result.get("current_handoff", {})
    if handoff_evidence.get("repair_signal"):
        result["social_repair_evidence"] = {
            "basis": "current_user_feedback_and_handoff_hypothesis",
            "user_words": current[:320],
            "previous_assistant_ref": handoff_evidence.get("previous_assistant_ref", ""),
            "interpretation": "May warrant revising approach, not a requirement to apologize or agree.",
        }

    examples = _past_interaction_evidence(
        state, session_id=session_id, current_text=current,
        allow_same_session_fallback=not handoff_evidence.get("explicit_topic_closure", False),
    )
    if examples:
        result["past_interactions"] = examples
        result["past_interactions_scope"] = (
            "Observer's fallible interpretation. Lexical overlap is not psychological similarity. "
            "A same-session fallback can be unrelated; neither is a permanent user preference "
            "or a command for this turn."
        )
    return result


__all__ = ["project_social_situation"]

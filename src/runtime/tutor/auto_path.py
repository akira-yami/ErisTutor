# -*- coding: utf-8 -*-
"""Tự đề xuất lộ trình học từ hội thoại, không mở planner thứ hai.

TeachingPlanBuilder chỉ đề xuất curriculum có cấu trúc; LearningService vẫn
là nguồn chân lý về path/mastery. Root vẫn sở hữu quyết định nói và tool tutor.
"""
from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from runtime.learning_core.contracts import KnowledgePoint, LearningPath
from runtime.pedagogical_plan_builder import TeachingPlanBuilder
from runtime.ai_orchestrated.orchestrator import _explicit_pedagogical_activation


def _stable(prefix: str, goal: str) -> str:
    digest = hashlib.sha256(goal.strip().casefold().encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def propose_learning_path(
    router: Any, *, topic_text: str,
    recent_history: Sequence[Mapping[str, Any]] = (),
) -> LearningPath | None:
    """Model đề xuất 3–8 điểm học, lỗi/timeout trả None (fail-closed).

    Không gán nhãn mastery, không tự nhận rằng user đã học xong và không tạo
    knowledge-point ảo để bù khi model từ chối lập kế hoạch.
    """
    topic = " ".join(str(topic_text or "").split())[:1200]
    if not topic:
        return None
    history = tuple(
        {"role": str(item.get("role", ""))[:24],
         "content": " ".join(str(item.get("content", "")).split())[:600]}
        for item in tuple(recent_history)[-6:]
        if isinstance(item, Mapping) and str(item.get("role", "")).lower() in {"user", "assistant"}
    )
    try:
        plan = TeachingPlanBuilder(router).build({
            "explicit_plan_request": True,
            "current_turn": topic,
            "recent_public_history": list(history),
        })
        if plan is None:
            return None
        domain_id = _stable("domain", plan.global_goal)
        names = {item.concept_id: domain_id + ":" + _stable("skill", item.concept_id).split(":", 1)[1]
                 for item in plan.concepts}
        kps = tuple(KnowledgePoint(
            kp_id=names[item.concept_id],
            title=item.objective[:240],
            kp_type="concept",
            description=item.description,
            prerequisites=tuple(names[ref] for ref in item.prerequisites),
        ) for item in plan.concepts)
        ids = {kp.kp_id for kp in kps}
        if (len(kps) < 3 or len(kps) > 8 or len(ids) != len(kps)
                or any(ref not in ids or ref == kp.kp_id
                       for kp in kps for ref in kp.prerequisites)):
            return None
        goal = plan.global_goal.strip()
        if not goal:
            return None
        return LearningPath(
            path_id=_stable("autopath", goal), title=goal[:240], goal=goal,
            knowledge_points=kps, source="eris_auto",
            domain_id=domain_id,
        )
    except Exception:
        return None


def _intent_to_learn(host: Any, *, user_text: str, recent_history: Sequence[Mapping[str, Any]]) -> bool:
    """Ba tín hiệu A/B/C của design. Không dùng thang 'độ quan trọng'.

    B/C là tín hiệu để thử TeachingPlanBuilder, không phải bằng chứng mastery.
    Nếu dấu hiệu chỉ là 'chưa hiểu' một câu độc lập không có chủ đề, không thử.
    """
    from runtime.ai_orchestrated.orchestrator import _explicit_pedagogical_activation

    text = " ".join(str(user_text or "").split())[:1200]
    if not text:
        return False
    if _explicit_pedagogical_activation(text):
        return True
    handoff = getattr(host, "conversation_handoff_runtime", None)
    state = getattr(handoff, "snapshot", lambda: None)()
    confused = bool(getattr(state, "repair_needed", False)) or getattr(state, "user_stance", "") == "confused"
    if bool(getattr(state, "topic_closed", False)):
        return False
    # B: chủ đề phải có nội dung dạy được từ câu hiện tại hoặc từ mạch trước,
    # không chỉ 'chưa hiểu?' đứng một mình.
    if confused:
        previous = str(getattr(state, "last_assistant_text", "") or "")
        substantial = len(text.split()) >= 6 or len(previous.split()) >= 20
        if substantial and (len(tuple(recent_history)) >= 2 or len(text.split()) >= 8):
            return True
    # C: trajectory quan sát nhiều lượt cùng một chủ đề. Depth là evidence của
    # tính liên tục, không phải quota/hard governor để điều khiển lời Root.
    trajectory = getattr(host, "conversation_trajectory_runtime", None)
    snapshot = getattr(trajectory, "snapshot", lambda: None)()
    depth = int(getattr(snapshot, "depth", 0) or 0)
    if depth >= 3 and not bool(getattr(snapshot, "topic_closed", False)):
        focused = " ".join(str(getattr(snapshot, "current_focus", "") or "").split())
        return len(focused.split()) >= 4 and len(tuple(recent_history)) >= 4
    return False


def maybe_autostart_path(
    host: Any, *, user_text: str,
    recent_history: Sequence[Mapping[str, Any]] = (),
    now: float | None = None,
) -> bool:
    """Path bền qua phiên; không chồng path; mọi lỗi xuống cấp về chat thường."""
    learning = getattr(host, "learning_service", None)
    router = getattr(host, "ai_model_router", None)
    if learning is None or router is None:
        return False
    try:
        active = learning.active_path()
        if active is not None:
            # Không tự tạo path mới chỉ vì nhiều lượt đang bàn cùng một chủ đề.
            # Muốn đổi môn khi đã có path, user phải kích hoạt ý định học rõ ràng.
            from runtime.ai_orchestrated.orchestrator import _explicit_pedagogical_activation
            if not _explicit_pedagogical_activation(user_text):
                return False
            # "Dạy tôi lại", "học tiếp" chỉ là tiếp tục path hiện tại, tuyệt
            # đối không lập curriculum mới từ một lượt thiếu tên chủ đề.
            from runtime.ai_orchestrated.orchestrator import normalize_vi
            expressed = normalize_vi(str(user_text or "")).strip()
            generic_followups = {"day toi lai", "chi toi lai", "day toi tiep",
                                 "muon hoc tiep", "toi muon hoc tiep", "dang hoc",
                                 "day toi", "chi toi", "muon hoc"}
            if expressed in generic_followups:
                return False
            # Khi user gọi đúng tên path đang học, hãy để Tutor resume từ
            # vị trí cũ thay vì tạo bản sao từ output mới của planner.
            title = normalize_vi(str(getattr(active, "title", "") or "")).strip()
            if title and title in expressed:
                return False
        elif not _intent_to_learn(host, user_text=user_text, recent_history=recent_history):
            return False
        path = propose_learning_path(router, topic_text=user_text, recent_history=recent_history)
        if path is None:
            return False
        # Nếu môn đã tồn tại, CHỈ RESUME; planner không được âm thầm ghi đè
        # curriculum/revision đang có bằng một bản sinh lại khác wording.
        from runtime.learning_core.records import DomainSpec
        store = getattr(learning, "_store", None)
        load = getattr(store, "load_path", None)
        existing = load(path.path_id) if callable(load) else None
        if existing is None:
            learning.register_domain(DomainSpec(path.domain_id, path.title, path.domain_id))
            learning.register_path(path, now=now)
        return bool(learning.activate_path(path.path_id))
    except Exception:
        return False


__all__ = ["propose_learning_path", "maybe_autostart_path"]

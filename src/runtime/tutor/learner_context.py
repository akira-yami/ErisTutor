# -*- coding: utf-8 -*-
"""Read-model dài hạn cho Tutor/Root; KHÔNG tạo agent hay nguồn chân lý mới.

- living_user là chủ sở hữu sở thích do user xác nhận; dữ liệu chỉ được ĐỌC.
- LearningService/SQLite là chủ sở hữu evidence, mastery, review và BKT shadow.
- Projection có giới hạn kích thước để Root không mang cả nhật ký sang model.
- Không lấy transcript, tự suy diễn sở thích học hoặc gán chẩn đoán tâm lý.
"""
from __future__ import annotations

from typing import Any

from runtime.learning_core.contracts import LearnerProfile

# Chỉ trường rõ nghĩa mới được phép tác động đến chính sách học tập.
_PROFILE_FIELDS = {
    "learning.preferred_pace": "preferred_pace",
    "user.learning.preferred_pace": "preferred_pace",
    "learning.preferred_style": "preferred_style",
    "user.learning.preferred_style": "preferred_style",
    "learning.native_language": "native_language",
    "user.learning.native_language": "native_language",
    "communication.preferred_language": "native_language",
    "user.communication.preferred_language": "native_language",
    "learning.goal_description": "goal_description",
    "user.learning.goal_description": "goal_description",
    "learning.active_domain": "active_domain",
    "user.learning.active_domain": "active_domain",
}
_VALID = {
    "preferred_pace": {"slow", "normal", "fast"},
    "preferred_style": {"conceptual", "procedural", "mixed"},
}


def _get_source(source: Any) -> Any:
    """Cho phép inject callable để đọc đúng service hiện tại, kể cả sau restart."""
    if callable(source):
        try:
            return source()
        except Exception:
            return None
    return source


def learner_profile_from_living_user(source: Any, *, project_id: str = "") -> LearnerProfile:
    """Chiếu các sở thích được xác nhận sang contract bất biến của learning.

    Không dùng record suy diễn hoặc nhạy cảm, không nhận dữ liệu scope khác.
    Sửa/xóa trong LivingUser có hiệu lực ngay ở lần Root gọi Tutor tiếp theo.
    """
    living_user = _get_source(source)
    store = getattr(living_user, "profile_store", None)
    reader = getattr(store, "all", None)
    if not callable(reader) or not bool(getattr(getattr(living_user, "config", None), "enabled", True)):
        return LearnerProfile()
    try:
        records = reader()
    except Exception:
        return LearnerProfile()
    chosen: dict[str, tuple[tuple[int, float, float], Any]] = {}
    for row in records:
        field = _PROFILE_FIELDS.get(str(getattr(row, "path", "") or "").lower())
        if not field or getattr(row, "status", "active") != "active":
            continue
        if getattr(row, "sensitivity", "normal") in {"private", "sensitive", "personal"}:
            continue
        explicitness = str(getattr(row, "explicitness", "inferred") or "inferred")
        if explicitness not in {"explicit", "observed"}:
            continue
        confidence = float(getattr(row, "confidence", 0.0) or 0.0)
        # Luật quan sát ngôn ngữ của LivingUser mặc định ~0.78; dùng nó
        # như sở thích ngôn ngữ tạm, nhưng KHÔNG suy ra tốc độ/cách học.
        observed_floor = 0.75 if field == "native_language" else 0.80
        if confidence < (0.60 if explicitness == "explicit" else observed_floor):
            continue
        scope = getattr(row, "scope", None)
        kind = str(getattr(scope, "kind", "global") or "global")
        key = str(getattr(scope, "key", "") or "")
        if kind == "project" and (not project_id or key != project_id):
            continue
        if kind not in {"global", "project"}:
            continue
        value = getattr(row, "value", None)
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        if field in _VALID and value not in _VALID[field]:
            continue
        if field == "native_language" and (len(value) > 12 or not value.replace("-", "").isalpha()):
            continue
        # Ưu tiên user nói rõ hơn suy ra từ ngôn ngữ giao tiếp, rồi mới xét độ mới.
        rank = (2 if explicitness == "explicit" else 1,
                float(confidence), float(getattr(row, "updated_at", 0.0) or 0.0))
        if field not in chosen or rank > chosen[field][0]:
            chosen[field] = rank, value
    return LearnerProfile.from_mapping({field: value for field, (_, value) in chosen.items()})


def learner_continuity_snapshot(learning: Any, *, source: Any = None, max_kps: int = 6) -> dict[str, Any]:
    """Projection ngắn, chỉ đọc SQLite. Không có path thì không áp tutor lên small talk.

    BKT shadow và mastery là hai tín hiệu tách biệt; không cộng thành một điểm IQ.
    Có thể gọi nhiều lần và qua restart mà không phát sinh side-effect.
    """
    if learning is None:
        return {}
    try:
        path = learning.active_path()
        if path is None:
            return {}
        ids = list(path.kp_ids)[: max(1, min(12, int(max_kps)))]
        due = {r.kp_id for r in learning.due_review_items()}
        schedules = learning.review_snapshot(ids)
        points = []
        for kp_id in ids:
            mastery = learning.get_mastery(kp_id)
            belief = learning.learner_belief(kp_id)
            points.append({
                "kp_id": str(kp_id)[:120],
                "mastery": round(float(mastery.mastery), 4),
                "confidence_heuristic": round(float(mastery.confidence), 4),
                "stage": str(mastery.stage)[:40],
                "attempt_count": int(mastery.attempt_count),
                "last_evidence_at": round(float(mastery.last_evidence_at), 1),
                "retrievability_heuristic": round(float(mastery.retrievability), 4),
                "next_review_at": round(float(schedules.get(kp_id, {}).get("due_at", mastery.next_review_at)), 1),
                "retention_scheduler": schedules.get(kp_id, {}).get("scheduler_owner", "heuristic"),
                "observed_error_codes": [str(x)[:80] for x in mastery.error_codes[:5]],
                "repeated_error_hypotheses": learning.repeated_error_patterns(kp_id),
                "due_for_review": kp_id in due,
                "review_memory_heuristic": learning.review_memory(kp_id),
                "p_known_shadow": round(float(belief.p_known), 4),
                "uncertainty_heuristic": round(float(belief.uncertainty), 4),
                "independent_evidence_count": int(belief.independent_count),
                "transfer_observed": bool(belief.transfer_observed),
                "needs_probe": bool(belief.needs_probe),
            })
        return {
            "schema": "eris.learner_continuity.v1",
            "active_path_id": str(path.path_id)[:120],
            "learning_goal": str(path.goal)[:220],
            "learner_preferences": learner_profile_from_living_user(source).to_dict(),
            "knowledge": points,
            "due_review_kp_ids": [kp for kp in ids if kp in due],
            "calibrated": False,
            "note": "Ước lượng mastery/BKT chưa được chuẩn hóa trên dữ liệu thực; Root quyết định khi nào dùng Tutor.",
        }
    except Exception:
        # Thiếu DB/đang migrate không được làm sập lời thoại thường.
        return {}


__all__ = ["learner_profile_from_living_user", "learner_continuity_snapshot"]

"""Read-model derived exclusively from LearningService's evidence projection."""
from __future__ import annotations

from .pack import KnowledgePack

STAGE_STATES = {"unknown": "available", "learning": "learning", "practicing": "practicing",
                "mastered": "mastered", "retained": "mastered"}


def build_snapshot(learning, pack: KnowledgePack, focus: dict, *, now=None, detail_node_id="") -> dict:
    insights = learning.learning_insights(path_id=pack.path.path_id, now=now, max_kps=16)
    rows = {row["kp_id"]: row for row in insights["knowledge"]}
    states = {key: STAGE_STATES.get(row.get("mastery_stage"), "available") for key, row in rows.items()}
    depths: dict[str, int] = {}
    points = {point.kp_id: point for point in pack.path.knowledge_points}

    def depth(key):
        if key not in depths:
            depths[key] = max((depth(p) + 1 for p in points[key].prerequisites), default=0)
        return depths[key]

    nodes = []
    for point in pack.path.knowledge_points:
        row = rows.get(point.kp_id, {})
        nodes.append({"id": point.kp_id, "title": point.title,
                      "prerequisites": list(point.prerequisites), "level": depth(point.kp_id),
                      "state": states.get(point.kp_id, "available"),
                      "due": row.get("review_due_at") is not None,
                      "prerequisite_warnings": [p for p in point.prerequisites if states.get(p) != "mastered"]})
    result = {"schema": "eris.learning.canvas.v1", "group_id": pack.group_id,
              "title": pack.path.title, "path_id": pack.path.path_id, "focus": focus,
              "nodes": nodes, "assessment": insights.get("assessment", "observational_not_calibrated"),
              "due_review": insights.get("due_review", []),
              "retention": insights.get("retention", {}),
              "recommendations": insights.get("recommendations", [])}
    if detail_node_id:
        result["detail"] = pack.detail(detail_node_id)
    return result

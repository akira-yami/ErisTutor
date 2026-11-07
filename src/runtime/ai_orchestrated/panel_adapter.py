# -*- coding: utf-8 -*-
"""Chiếu RuntimeMindState mới sang payload panel/body.

``identity_runtime`` là contract panel chính của Architecture C v5. Các block
emotion_debug/depth_v2/private_mind vẫn được giữ để body cũ không gãy trong lúc
migrate, nhưng không còn là nguồn ý nghĩa chính.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from .contracts import AI_RUNTIME_VERSION, RuntimeMindState
from .views import build_cognitive_views


def _mood_label(pleasantness: float, irritation: float) -> str:
    valence = pleasantness * 2.0 - 1.0
    if irritation >= 0.45 or valence <= -0.22:
        return "kho_chiu"
    if valence >= 0.22:
        return "vui"
    return "trung_tinh"


def _view(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    fn = getattr(value, "panel_view", None)
    if callable(fn):
        try:
            result = fn()
            return dict(result) if isinstance(result, Mapping) else {}
        except Exception:
            return {}
    return {}


def _top_scores(mapping: Mapping[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    rows: list[tuple[str, float]] = []
    for key, value in mapping.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        rows.append((str(key), max(-1.0, min(1.0, number))))
    rows.sort(key=lambda item: item[1], reverse=True)
    return [{"key": key, "value": round(value, 4)} for key, value in rows[:limit]]


_RELATIONSHIP_METRICS = (
    "relational_trust",
    "competence_trust",
    "familiarity",
    "closeness",
    "respect",
    "attachment_intensity",
    "playful_safety",
    "jealousy",
    "possessiveness",
    "dependency",
    "resentment",
    "competition_sensitivity",
    "shared_curiosity",
)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number != number:
        number = float(default)
    return number


def _belief_status(*, evidence_count: int, contradictions: int, confidence: float) -> str:
    """Trạng thái belief chỉ dựa trên evidence thật, không nhìn score rồi đoán."""

    if evidence_count <= 0:
        return "baseline"
    if contradictions > 0:
        return "mixed"
    if evidence_count < 2 or confidence < 0.50:
        return "testing"
    if evidence_count >= 3 and confidence >= 0.68:
        return "reinforced"
    return "forming"


def _relationship_summary(profile: Mapping[str, Any]) -> tuple[str, str]:
    """Tóm tắt profile bằng metric hiện tại; đây là derived view, không phải live claim."""

    trust = _number(profile.get("relational_trust"))
    competence = _number(profile.get("competence_trust"))
    familiarity = _number(profile.get("familiarity"))
    closeness = _number(profile.get("closeness"))
    respect = _number(profile.get("respect"))
    attachment = _number(profile.get("attachment_intensity"))

    if familiarity < 0.28 and closeness < 0.30:
        headline = "Đang hình thành một mối liên hệ ban đầu"
    elif closeness >= 0.62 and trust >= 0.58:
        headline = "Gần gũi và tương đối an toàn"
    elif familiarity >= 0.55 and closeness < 0.42:
        headline = "Quen thuộc nhưng vẫn giữ khoảng riêng"
    elif trust < 0.34:
        headline = "Đang quan sát trước khi đặt thêm niềm tin"
    else:
        headline = "Có nền liên hệ, chưa nghiêng mạnh về một phía"

    details: list[str] = []
    if competence >= 0.60:
        details.append("tin vào năng lực của User")
    elif competence <= 0.35:
        details.append("chưa dựa nhiều vào đánh giá kỹ thuật của User")
    if respect >= 0.62:
        details.append("có sự tôn trọng rõ")
    if attachment >= 0.50:
        details.append("User đã có trọng lượng cảm xúc")
    if closeness < 0.36:
        details.append("vẫn bảo vệ khoảng riêng")
    description = "; ".join(details[:3]) or "Các metric quan hệ chưa đủ nổi để tạo một mô tả mạnh."
    return headline, description[0].upper() + description[1:] if description else description


def _relationship_panel_view(
    state: RuntimeMindState,
    *,
    identity_view: Mapping[str, Any],
    observation: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Biên dịch relationship/belief thành contract trung thực cho Body.

    Backend làm phần provenance và delta vì chỉ backend có ledger thật. Body chỉ
    trình bày, tuyệt đối không tự suy ra evidence hoặc biến baseline thành live state.
    """

    now = time.time()
    ledger = [dict(item) for item in state.identity.ledger if isinstance(item, Mapping)]
    delta_7d = {key: 0.0 for key in _RELATIONSHIP_METRICS}
    delta_30d = {key: 0.0 for key in _RELATIONSHIP_METRICS}
    relationship_events: list[dict[str, Any]] = []
    belief_evidence: dict[str, list[str]] = {}

    for entry in ledger:
        dimension = str(entry.get("dimension", "") or "")
        key = str(entry.get("key", "") or "")
        timestamp = _number(entry.get("timestamp"), 0.0)
        delta = _number(entry.get("applied_delta"), 0.0)
        age = max(0.0, now - timestamp) if timestamp > 0.0 else float("inf")

        if dimension == "relationship" and key in delta_30d:
            if age <= 30 * 86400:
                delta_30d[key] += delta
            if age <= 7 * 86400:
                delta_7d[key] += delta

        if dimension == "belief" and key:
            candidates: list[str] = []
            raw_evidence = entry.get("evidence", ())
            if isinstance(raw_evidence, (list, tuple)):
                candidates.extend(str(item).strip() for item in raw_evidence if str(item).strip())
            interpretation = str(entry.get("self_interpretation", "") or "").strip()
            if interpretation:
                candidates.append(interpretation)
            bucket = belief_evidence.setdefault(key, [])
            for item in candidates:
                if item not in bucket:
                    bucket.append(item)

        if dimension in {"relationship", "belief", "strategy_outcome"}:
            relationship_events.append(
                {
                    "timestamp": timestamp,
                    "turn_seq": int(entry.get("turn_seq", 0) or 0),
                    "dimension": dimension,
                    "key": key,
                    "delta": round(delta, 6),
                    "outcome": str(entry.get("outcome", "") or ""),
                    "context": str(entry.get("context", "") or ""),
                    "evidence": list(entry.get("evidence", ()) or ())[:3],
                    "self_interpretation": str(entry.get("self_interpretation", "") or ""),
                }
            )

    belief_rows: list[dict[str, Any]] = []
    for key, record in state.identity.beliefs.items():
        scope = key.split(".", 1)[0] if "." in key else "other"
        evidence_ids = list(record.evidence_ids)
        evidence_count = len(evidence_ids)
        belief_rows.append(
            {
                "key": key,
                "scope": scope,
                "strength": round(record.strength, 4),
                "confidence": round(record.confidence, 4),
                "evidence_count": evidence_count,
                "contradiction_count": int(record.contradictions),
                "last_updated_turn": int(record.last_turn_seq),
                "status": _belief_status(
                    evidence_count=evidence_count,
                    contradictions=int(record.contradictions),
                    confidence=record.confidence,
                ),
                "evidence_ids": evidence_ids[-6:],
                "evidence_summaries": belief_evidence.get(key, [])[-3:],
                "weight": round(record.strength * record.confidence, 4),
            }
        )
    belief_rows.sort(
        key=lambda row: (
            0 if row["scope"] == "user" else 1 if row["scope"] == "self" else 2,
            -float(row["weight"]),
        )
    )

    identity_signals = observation.get("identity_signals", ())
    current_signals: list[dict[str, Any]] = []
    if isinstance(identity_signals, list):
        for signal in identity_signals:
            if not isinstance(signal, Mapping):
                continue
            dimension = str(signal.get("dimension", "") or "")
            if dimension not in {"relationship", "belief"}:
                continue
            current_signals.append(
                {
                    "dimension": dimension,
                    "key": str(signal.get("key", "") or ""),
                    "delta": round(_number(signal.get("delta")), 4),
                    "confidence": round(_number(signal.get("confidence")), 4),
                    "context": str(signal.get("context", "") or ""),
                    "evidence": list(signal.get("evidence", ()) or ())[:3],
                }
            )

    mechanical_deltas = {
        key: round(_number(observation.get(key)), 4)
        for key in (
            "relational_delta",
            "competence_delta",
            "familiarity_delta",
            "closeness_delta",
            "playful_safety_delta",
            "boundary_delta",
            "rupture_delta",
        )
    }
    live_changed = bool(current_signals) or any(abs(value) > 1e-6 for value in mechanical_deltas.values())
    meaning = str(observation.get("relationship_meaning", "neutral") or "neutral")
    if meaning not in {"", "neutral", "ordinary_dialogue", "unknown"}:
        live_changed = True

    headline, description = _relationship_summary(profile)
    metrics = [
        {
            "key": key,
            "value": round(_number(profile.get(key)), 4),
            "delta_7d": round(delta_7d[key], 6),
            "delta_30d": round(delta_30d[key], 6),
            "has_history": any(abs(value) > 1e-9 for value in (delta_7d[key], delta_30d[key])),
        }
        for key in _RELATIONSHIP_METRICS
    ]

    latent_keys = ("jealousy", "possessiveness", "dependency", "resentment", "competition_sensitivity")
    latent = [
        row for row in metrics
        if row["key"] in latent_keys and (float(row["value"]) >= 0.18 or row["has_history"])
    ]

    return {
        "schema": "eris.relationship_runtime.panel.v1",
        "relationship_id": "primary_user",
        "headline": headline,
        "description": description,
        "metrics": metrics,
        "profile": dict(profile),
        "beliefs": belief_rows,
        "belief_counts": {
            "total": len(belief_rows),
            "testing": sum(1 for row in belief_rows if row["status"] in {"testing", "forming"}),
            "mixed": sum(1 for row in belief_rows if row["status"] == "mixed"),
            "reinforced": sum(1 for row in belief_rows if row["status"] == "reinforced"),
        },
        "live": {
            "changed": live_changed,
            "user_act": str(observation.get("user_act", "unknown") or "unknown"),
            "meaning": meaning,
            "confidence": round(_number(observation.get("confidence")), 4),
            "repair_needed": bool(observation.get("repair_needed", False)),
            "self_interpretation": str(
                (observation.get("self_interpretation", {}) or {}).get("summary", "")
                if isinstance(observation.get("self_interpretation", {}), Mapping)
                else ""
            ),
            "evidence": list(observation.get("evidence", ()) or ())[:4],
            "signals": current_signals,
            "mechanical_deltas": mechanical_deltas,
        },
        "latent": latent,
        "timeline": relationship_events[-10:][::-1],
        "overlap": {
            "closeness": round(_number(profile.get("closeness")), 4),
            "attachment": round(_number(profile.get("attachment_intensity")), 4),
            "dependency": round(_number(profile.get("dependency")), 4),
            "interior_privacy": round(
                _number((identity_view.get("values", {}) or {}).get("interior_privacy"))
                if isinstance(identity_view.get("values", {}), Mapping)
                else 0.0,
                4,
            ),
        },
    }



def _discovery_panel_view(
    discovery_view: Mapping[str, Any],
    initiative_view: Mapping[str, Any],
) -> dict[str, Any]:
    """Biên dịch Discovery + Initiative thành một contract panel trung thực.

    Mọi trạng thái ``active`` đều phải đến từ activity/decision thật. Các bước
    trong flow chỉ là bản tóm tắt của record hiện có, không được dựng một cuộc
    phiêu lưu nền cho Eris khi runtime đang yên.
    """

    now = time.time()
    activity = dict(discovery_view.get("current_activity", {}) or {})
    ready = [dict(item) for item in discovery_view.get("ready_to_share", ()) if isinstance(item, Mapping)]
    recent = [dict(item) for item in discovery_view.get("recent", ()) if isinstance(item, Mapping)]
    recent.sort(key=lambda item: _number(item.get("created_at"), 0.0), reverse=True)

    status = str(activity.get("status", "idle") or "idle").strip().lower()
    active_statuses = {"queued", "searching", "verifying", "curating"}
    activity_active = bool(activity.get("activity_id")) and status in active_statuses
    ready_count = int(discovery_view.get("ready_count", len(ready)) or len(ready))
    archive_count = int(discovery_view.get("archive_count", 0) or 0)
    shared_count = int(discovery_view.get("shared_count", 0) or 0)
    verified_count = int(discovery_view.get("verified_count", 0) or 0)

    if activity_active:
        headline_state = "active"
        headline_title = str(activity.get("topic", "") or "Discovery đang được xử lý")
        headline_detail = str(activity.get("detail", "") or f"Runtime đang ở bước {status}.")
    elif ready_count > 0:
        headline_state = "ready"
        headline_title = f"Có {ready_count} discovery đang chờ được kể"
        headline_detail = "Artifact đã được kiểm chứng và chưa bị đánh dấu là đã chia sẻ."
    elif status in {"failed", "cancelled"}:
        headline_state = "quiet"
        headline_title = "Dòng khám phá đang yên"
        headline_detail = "Activity gần nhất đã kết thúc mà không tạo artifact sẵn sàng."
    else:
        headline_state = "quiet"
        headline_title = "Dòng khám phá đang yên"
        headline_detail = "Không có activity thật hoặc discovery nào đang chờ kể."

    has_records = archive_count > 0
    has_verified = verified_count > 0
    has_ready = ready_count > 0
    has_shared = shared_count > 0
    flow = [
        {"key": "signal", "label": "Tín hiệu", "state": "done" if has_records or activity_active else "idle"},
        {"key": "curiosity", "label": "Tò mò", "state": "done" if has_records or activity_active else "idle"},
        {
            "key": "research",
            "label": "Tutor / kiểm chứng",
            "state": "active" if status in {"queued", "searching", "verifying"} else ("done" if has_verified else "idle"),
        },
        {
            "key": "curation",
            "label": "Chắt lọc",
            "state": "active" if status == "curating" else ("done" if has_verified else "idle"),
        },
        {"key": "ready", "label": "Chờ kể", "state": "active" if has_ready else ("done" if has_shared else "idle")},
        {"key": "shared", "label": "Đã chia sẻ", "state": "done" if has_shared else "idle"},
    ]

    enabled = bool(initiative_view.get("enabled", True))
    budget = max(0, int(initiative_view.get("hourly_budget", 0) or 0))
    used = max(0, int(initiative_view.get("used_last_hour", 0) or 0))
    remaining = max(0, int(initiative_view.get("remaining_budget", max(0, budget - used)) or 0))
    next_allowed_at = _number(initiative_view.get("next_allowed_at"), 0.0)
    last_decision = dict(initiative_view.get("last_decision", {}) or {})
    last_spoken = dict(initiative_view.get("last_spoken", {}) or {})
    decision_at = _number(last_decision.get("at"), 0.0)
    decision_recent = decision_at > 0.0 and now - decision_at <= 6 * 3600.0

    if not enabled:
        gate_state = "disabled"
        gate_title = "Chủ động đang tắt"
    elif budget <= 0:
        gate_state = "budget_blocked"
        gate_title = "Không có ngân sách chủ động trong giờ"
    elif remaining <= 0:
        gate_state = "budget_blocked"
        gate_title = "Đã dùng hết nhịp chủ động trong giờ"
    elif next_allowed_at > now:
        gate_state = "cooldown"
        gate_title = "Đang giữ khoảng nghỉ"
    elif decision_recent and not bool(last_decision.get("allowed", False)):
        gate_state = "held"
        gate_title = "Một ý định vừa được giữ lại"
    else:
        gate_state = "open"
        gate_title = "Có thể chủ động khi đủ đáng kể"

    return {
        "schema": "eris.discovery_runtime.panel.v1",
        "headline": {
            "state": headline_state,
            "title": headline_title,
            "detail": headline_detail,
        },
        "activity": activity if activity.get("activity_id") or status in {"done", "failed", "cancelled"} else {},
        "flow": flow,
        "ready": ready[:4],
        "recent": recent[:8],
        "counts": {
            "archive": archive_count,
            "verified": verified_count,
            "ready": ready_count,
            "shared": shared_count,
        },
        "initiative": {
            "enabled": enabled,
            "state": gate_state,
            "title": gate_title,
            "hourly_budget": budget,
            "used_last_hour": used,
            "remaining_budget": remaining,
            "minimum_interval_seconds": _number(initiative_view.get("minimum_interval_seconds"), 0.0),
            "next_allowed_at": next_allowed_at,
            "last_decision": last_decision,
            "last_spoken": last_spoken,
            "recent_events": [
                dict(item)
                for item in initiative_view.get("recent_events", ())
                if isinstance(item, Mapping)
            ][:8],
        },
        "authority": dict(discovery_view.get("authority", {}) or {}),
    }


def _identity_audit_panel_view(
    state: RuntimeMindState,
    *,
    observation: Mapping[str, Any],
    personality_view: Mapping[str, Any],
    consolidator_view: Mapping[str, Any],
) -> dict[str, Any]:
    """Tạo contract Ledger & imprint từ đúng dữ liệu reducer đã ghi.

    Contract này cố ý tách bốn lớp: input gần nhất, candidate của Observer,
    commit thật trong ledger và imprint persistent. Body không được nhìn một
    candidate rồi tuyên bố nó đã trở thành ký ức hoặc bản sắc.
    """

    current_turn = max(
        0,
        int(observation.get("turn_seq", state.last_applied_turn_seq) or state.last_applied_turn_seq or 0),
    )
    raw_ledger = [dict(item) for item in state.identity.ledger if isinstance(item, Mapping)]

    def normalized_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
        before = _number(entry.get("before"), 0.0)
        after = _number(entry.get("after"), before)
        delta = _number(entry.get("applied_delta"), entry.get("reinforcement", 0.0))
        has_state_transition = any(key in entry for key in ("before", "after", "applied_delta"))
        return {
            "timestamp": _number(entry.get("timestamp"), 0.0),
            "turn_seq": max(0, int(entry.get("turn_seq", 0) or 0)),
            "turn_id": str(entry.get("turn_id", "") or ""),
            "dimension": str(entry.get("dimension", "") or ""),
            "key": str(entry.get("key", "") or ""),
            "scope": str(entry.get("scope", "") or ""),
            "before": round(before, 6),
            "after": round(after, 6),
            "applied_delta": round(delta, 6),
            "has_state_transition": has_state_transition,
            "confidence": round(_number(entry.get("confidence")), 4),
            "importance": round(_number(entry.get("importance")), 4),
            "outcome": str(entry.get("outcome", "") or ""),
            "status": str(entry.get("status", "") or ""),
            "goal": str(entry.get("goal", "") or ""),
            "context": entry.get("context", ""),
            "evidence": list(entry.get("evidence", ()) or ())[:5],
            "self_interpretation": str(entry.get("self_interpretation", "") or ""),
        }

    commits = [normalized_entry(item) for item in raw_ledger]
    current_commits = [item for item in commits if int(item["turn_seq"]) == current_turn]

    raw_signals = observation.get("identity_signals", ())
    candidates: list[dict[str, Any]] = []
    if isinstance(raw_signals, (list, tuple)):
        for raw in raw_signals:
            if not isinstance(raw, Mapping):
                continue
            dimension = str(raw.get("dimension", "") or "")
            key = str(raw.get("key", "") or "")
            scope = str(raw.get("scope", "") or "")
            proposed_delta = _number(raw.get("delta"), 0.0)
            matched = [
                entry
                for entry in current_commits
                if entry["dimension"] == dimension
                and entry["key"] == key
                and (not scope or not entry["scope"] or entry["scope"] == scope)
            ]
            applied_delta = sum(_number(entry.get("applied_delta")) for entry in matched)
            if matched:
                commit_state = "committed"
                commit_note = "Reducer đã tạo entry persistent tương ứng."
            elif abs(proposed_delta) <= 1e-9:
                commit_state = "no_change"
                commit_note = "Candidate không đề xuất thay đổi số."
            else:
                commit_state = "not_committed"
                commit_note = "Không thấy entry commit tương ứng trong ledger của lượt này."
            candidates.append(
                {
                    "dimension": dimension,
                    "key": key,
                    "scope": scope,
                    "delta": round(proposed_delta, 4),
                    "confidence": round(_number(raw.get("confidence")), 4),
                    "importance": round(_number(raw.get("importance")), 4),
                    "context": str(raw.get("context", "") or ""),
                    "evidence": list(raw.get("evidence", ()) or ())[:4],
                    "commit_state": commit_state,
                    "commit_note": commit_note,
                    "matched_entries": len(matched),
                    "applied_delta": round(applied_delta, 6),
                }
            )

    interpretation = observation.get("self_interpretation", {})
    interpretation_summary = ""
    if isinstance(interpretation, Mapping):
        interpretation_summary = str(interpretation.get("summary", "") or "")
    input_present = bool(observation)
    input_view = {
        "present": input_present,
        "turn_seq": current_turn,
        "event_kind": str(observation.get("event_kind", "") or ""),
        "user_act": str(observation.get("user_act", "") or ""),
        "relationship_meaning": str(observation.get("relationship_meaning", "") or ""),
        "confidence": round(_number(observation.get("confidence")), 4),
        "repair_needed": bool(observation.get("repair_needed", False)),
        "notes": str(observation.get("notes", "") or ""),
        "self_interpretation": interpretation_summary,
        "evidence": list(observation.get("evidence", ()) or ())[:5],
    }

    imprints: list[dict[str, Any]] = []
    for record in state.identity.imprints:
        evidence_turns = list(record.evidence_turns)
        if record.strength >= 0.62 and record.generalization >= 0.35:
            status = "deep"
        elif record.strength >= 0.30:
            status = "formed"
        else:
            status = "forming"
        polarity = "positive" if record.valence > 0.10 else "negative" if record.valence < -0.10 else "neutral"
        imprints.append(
            {
                "theme": record.theme,
                "strength": round(record.strength, 4),
                "valence": round(record.valence, 4),
                "polarity": polarity,
                "generalization": round(record.generalization, 4),
                "status": status,
                "triggers": list(record.triggers)[:6],
                "default_interpretation": record.default_interpretation,
                "preferred_defense": record.preferred_defense,
                "evidence_turns": evidence_turns[-8:],
                "evidence_count": len(evidence_turns),
                "last_reinforced_turn": int(record.last_reinforced_turn),
            }
        )
    imprints.sort(key=lambda item: (-float(item["strength"]), -int(item["last_reinforced_turn"])))

    trajectory = []
    raw_trajectory = personality_view.get("recent_trajectory", ())
    if isinstance(raw_trajectory, (list, tuple)):
        for item in list(raw_trajectory)[-10:][::-1]:
            if not isinstance(item, Mapping):
                continue
            trajectory.append(
                {
                    "timestamp": _number(item.get("timestamp"), 0.0),
                    "turn_seq": max(0, int(item.get("turn_seq", 0) or 0)),
                    "category": str(item.get("category", "") or ""),
                    "trait": str(item.get("trait", "") or ""),
                    "applied_delta": round(_number(item.get("applied_delta")), 6),
                    "source": str(item.get("source", "") or ""),
                    "reason": str(item.get("reason", "") or ""),
                }
            )

    last_write_at = _number(consolidator_view.get("last_write_at"), 0.0)
    accepted_since_write = max(0, int(consolidator_view.get("accepted_since_write", 0) or 0))
    persistence = {
        "last_write_at": last_write_at,
        "accepted_since_write": accepted_since_write,
        "snapshot_path": str(consolidator_view.get("snapshot_path", "") or ""),
        "has_written": last_write_at > 0.0,
        "state": "pending" if accepted_since_write > 0 else ("written" if last_write_at > 0.0 else "not_written"),
    }

    if current_commits:
        summary_state = "committed"
        summary_title = f"Reducer đã ghi {len(current_commits)} thay đổi ở lượt này"
        summary_detail = "Các entry bên dưới là thay đổi persistent thật, không phải candidate của Observer."
    elif candidates:
        summary_state = "observed"
        summary_title = "Observer có tín hiệu, reducer chưa ghi thay đổi"
        summary_detail = "Candidate vẫn được giữ để audit, nhưng chưa được phép giả làm state persistent."
    elif input_present:
        summary_state = "input_only"
        summary_title = "Lượt gần nhất chỉ tạo observation"
        summary_detail = "Không có identity signal hoặc ledger commit đi kèm."
    else:
        summary_state = "idle"
        summary_title = "Chưa có input để audit"
        summary_detail = "Ledger và imprint sẽ chỉ xuất hiện sau khi runtime có evidence thật."

    return {
        "schema": "eris.identity_audit.panel.v1",
        "summary": {
            "state": summary_state,
            "title": summary_title,
            "detail": summary_detail,
            "turn_seq": current_turn,
            "candidate_count": len(candidates),
            "current_commit_count": len(current_commits),
            "recent_commit_count": len(commits),
            "imprint_count": len(imprints),
        },
        "input": input_view,
        "candidates": candidates[:10],
        "current_commits": current_commits[::-1],
        "recent_commits": commits[-16:][::-1],
        "imprints": imprints[:12],
        "trajectory": trajectory,
        "persistence": persistence,
        "authority": {
            "observer": "đề xuất candidate",
            "reducer": "single writer của state persistent",
            "ledger": "chỉ ghi thay đổi đã áp dụng",
            "imprint": "chỉ hình thành sau corroboration gate",
        },
    }

def build_ai_panel_payload(
    state: RuntimeMindState,
    discovery: Any | None = None,
    initiative: Any | None = None,
    consolidator: Any | None = None,
    expression: Mapping[str, Any] | None = None,
    life_horizon: Mapping[str, Any] | None = None,
    persistent_individual: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    appraisal = state.appraisal
    relation = state.relationship
    personality = state.personality.panel_view()
    identity = state.identity.panel_view()
    observation = dict(state.latest_observation or {})
    intention = dict(state.latest_intention or {})
    views = build_cognitive_views(state)
    discovery_view = _view(discovery)
    initiative_view = _view(initiative)
    consolidator_view = _view(consolidator)
    expression_view = dict(expression or {})
    # V78 panel bridge: Identity & Life chỉ cần projection dài hạn thuộc đúng
    # trách nhiệm của panel này. Không kéo mood/private diary/memory raw sang đây.
    life_horizon_view = dict(life_horizon or {})
    individual_view = dict(persistent_individual or {})

    valence = max(-1.0, min(1.0, appraisal.pleasantness * 2.0 - 1.0))
    arousal = max(appraisal.pressure, appraisal.irritation, appraisal.novelty * 0.65)
    mood = _mood_label(appraisal.pleasantness, appraisal.irritation)

    primary_relationship = {}
    relationships = identity.get("relationships", {})
    if isinstance(relationships, Mapping):
        primary_relationship = dict(relationships.get("primary_user", {}) or {})

    identity_runtime = {
        "schema": "eris.identity_runtime.panel.v2",
        "architecture": AI_RUNTIME_VERSION,
        "state_version": state.version,
        "turn_seq": state.last_applied_turn_seq,
        "updated_at": state.updated_at,
        "learning": {
            "enabled": bool(state.identity_learning_enabled),
            "mode": "normal" if state.identity_learning_enabled else "observe_only",
        },
        # Hai projection mới là authority cho UI V77+:
        # - life_horizon: hướng sống/mục tiêu H0-H4;
        # - individual_runtime: self-model, habit và continuous-self dài hạn.
        # Body chỉ trình bày, không tự suy diễn từ telemetry legacy.
        "life_horizon": life_horizon_view,
        "individual_runtime": individual_view,
        "summary": {
            "mood": mood,
            "valence": round(valence, 4),
            "arousal": round(arousal, 4),
            "identity": state.identity_prompt_context(),
            "relationship": relation.prompt_summary(),
        },
        "expression": expression_view,
        "appraisal": appraisal.to_dict(),
        "relationship": primary_relationship or relation.to_dict(),
        "relationship_compat": relation.to_dict(),
        "relationship_runtime": _relationship_panel_view(
            state,
            identity_view=identity,
            observation=observation,
            profile=primary_relationship or relation.to_dict(),
        ),
        "values_top": _top_scores(identity.get("values", {}) if isinstance(identity.get("values"), Mapping) else {}),
        "motives_top": _top_scores(identity.get("motives", {}) if isinstance(identity.get("motives"), Mapping) else {}),
        "defenses_top": _top_scores(identity.get("defenses", {}) if isinstance(identity.get("defenses"), Mapping) else {}),
        "emergent_values_top": _top_scores(identity.get("emergent_values", {}) if isinstance(identity.get("emergent_values"), Mapping) else {}),
        "active_motives": list(identity.get("active_motives", []) or [])[:4],
        "active_defenses": list(identity.get("active_defenses", []) or [])[:4],
        "active_conflict": dict(identity.get("active_conflict", {}) or {}),
        "value_choice": dict(identity.get("latest_value_choice", {}) or {}),
        "derived_forms": dict(identity.get("emergent_forms", {}) or {}),
        "active_forms": list(identity.get("active_forms", []) or [])[:6],
        "beliefs": dict(identity.get("beliefs", {}) or {}),
        "strategies": dict(identity.get("strategies", {}) or {}),
        "pending_strategies": list(identity.get("pending_strategies", []) or [])[-4:],
        "imprints": list(identity.get("imprints", []) or [])[-8:],
        "recent_identity_ledger": list(identity.get("recent_ledger", []) or [])[-12:],
        "recent_personality_trajectory": list(personality.get("recent_trajectory", []) or [])[-10:],
        "latest_observation": observation,
        "latest_intention": intention,
        "open_loops": list(state.open_loops[-6:]),
        "discovery": discovery_view,
        "initiative": initiative_view,
        "discovery_runtime": _discovery_panel_view(discovery_view, initiative_view),
        "audit_runtime": _identity_audit_panel_view(
            state,
            observation=observation,
            personality_view=personality,
            consolidator_view=consolidator_view,
        ),
        "consolidation": consolidator_view,
        "authority": views.get("authority", {}),
        "controls": {
            "schema": "eris.identity_control.v1",
            "transport": "identity_control",
            "actions": {
                "set_learning": {"owner": "identity_reducer", "fields": ["enabled"]},
                "snapshot": {"owner": "identity_consolidator", "fields": []},
                "reset_mood": {"owner": "identity_reducer", "fields": []},
                "reset_relationship": {"owner": "identity_reducer", "fields": []},
                "reset_discovery": {"owner": "discovery_runtime", "fields": []},
            },
        },
    }

    # Body hiện vẫn có panel cảm xúc 4 thanh, nhưng parser của nó chỉ đọc
    # ``emotion_debug.items``. Contract mới vì thế chiếu state Architecture C
    # sang đúng các key cơ học mà panel cần, thay vì để UI trắng hoặc lén quay
    # lại đọc Ni/Fe legacy.
    personality_mood = personality.get("mood", {}) if isinstance(personality.get("mood"), Mapping) else {}
    temperament = personality.get("temperament", {}) if isinstance(personality.get("temperament"), Mapping) else {}

    def metric(label: str, value: Any, *, group: str, note: str = "") -> dict[str, Any]:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = 0.0
        if numeric != numeric:
            numeric = 0.0
        return {
            "label": label,
            "value": round(max(0.0, min(1.0, numeric)), 4),
            "group": group,
            "note": note,
        }

    emotional_stability = 1.0 - max(
        arousal,
        appraisal.irritation,
        appraisal.pressure,
        appraisal.embarrassment * 0.65,
    )
    mood_inertia = max(
        float(personality_mood.get("anxiety_tension", 0.0) or 0.0),
        float(personality_mood.get("separation_distress", 0.0) or 0.0),
        float(temperament.get("rumination", 0.0) or 0.0) * 0.58,
    )
    attachment = float(primary_relationship.get("attachment_intensity", relation.closeness) or 0.0)
    warmth = max(
        appraisal.tenderness,
        float(temperament.get("warmth_expression", 0.0) or 0.0),
        float(temperament.get("relational_softening", 0.0) or 0.0) * 0.82,
    )
    distance = 1.0 - max(
        float(primary_relationship.get("closeness", relation.closeness) or 0.0),
        float(primary_relationship.get("familiarity", relation.familiarity) or 0.0) * 0.55,
    )
    intimacy = max(
        float(primary_relationship.get("closeness", relation.closeness) or 0.0),
        attachment * 0.72,
    )
    overload = max(appraisal.pressure, arousal * 0.55, appraisal.irritation * 0.60)
    thought_pressure = max(appraisal.curiosity, appraisal.novelty, appraisal.pressure * 0.65)

    emotion_items = {
        "calm_aroused": metric("bình tĩnh <-> kích thích", arousal, group="core"),
        "emotional_stability": metric("ổn định cảm xúc", emotional_stability, group="core"),
        "mood_inertia": metric("quán tính mood", mood_inertia, group="core"),
        "overload": metric("quá tải", overload, group="load"),
        "attachment": metric("mức quan tâm / gắn bó", attachment, group="bond"),
        "tenderness": metric("dịu dàng", appraisal.tenderness, group="bond"),
        "warmth": metric("độ ấm áp", warmth, group="tone"),
        "relational_softening": metric(
            "làm mềm quan hệ",
            float(temperament.get("relational_softening", 0.0) or 0.0),
            group="tone",
            note="Giữ quan hệ mềm dù mức tự bộc lộ còn thấp",
        ),
        "intimacy": metric("mức thân mật", intimacy, group="tone"),
        "distance": metric("giữ khoảng cách", distance, group="tone"),
        "irritation": metric("khó chịu", appraisal.irritation, group="friction"),
        "perceived_rejection": metric("cảm giác bị từ chối", relation.rupture, group="friction"),
        "curiosity": metric("tò mò", appraisal.curiosity, group="attention"),
        "thought_stream_pressure": metric("dòng suy nghĩ tăng", thought_pressure, group="attention"),
        "playfulness": metric(
            "mức đùa",
            float(primary_relationship.get("playful_safety", relation.playful_safety) or 0.0),
            group="tone",
        ),
        "restraint": metric(
            "tự kiềm chế",
            float(temperament.get("social_guardedness", 0.0) or 0.0),
            group="control",
        ),
    }
    if expression_view:
        emotion_items["expression_preset"] = {
            "label": "preset biểu hiện",
            "value": str(expression_view.get("preset", expression_view.get("name", "")) or ""),
            "group": "surface",
            "note": str(expression_view.get("reason", "") or ""),
        }

    emotion_debug = {
        "architecture": AI_RUNTIME_VERSION,
        "summary": {
            "label": "vui / kho_chiu / trung_tinh",
            "value": mood,
            "valence": round(valence, 3),
            "arousal": round(arousal, 3),
            "state": str(observation.get("relationship_meaning", "neutral") or "neutral"),
        },
        "items": emotion_items,
        "appraisal": appraisal.to_dict(),
        "relationship": relation.to_dict(),
        "personality": personality,
        "identity": identity,
        "cognitive_views": views,
        "discovery": discovery_view,
        "identity_expression": expression_view,
    }
    # depth_v2 giờ là compatibility alias của panel identity mới. Body v5 đọc
    # identity_runtime; body cũ vẫn có dữ liệu đủ để không trắng panel.
    depth_v2 = {
        "architecture": AI_RUNTIME_VERSION,
        "live": bool(observation or intention),
        "state_version": state.version,
        "turn_seq": state.last_applied_turn_seq,
        "relationship": relation.to_dict(),
        "appraisal": appraisal.to_dict(),
        "personality": personality,
        "identity": identity,
        "open_loops": list(state.open_loops),
        "latest_observation": observation,
        "latest_intention": intention,
        "authority": views["authority"],
        "discovery": discovery_view,
        "initiative": initiative_view,
        "expression": expression_view,
    }
    private_mind_digest = {
        "schema": "eris.inner_state.panel.v1",
        "architecture": AI_RUNTIME_VERSION,
        "generated_at": round(time.time(), 3),
        "surface": False,
        "authority": {
            "current_intention": "identity_reducer.latest_intention",
            "open_loops": "runtime_mind_state.open_loops",
            "conflict_and_defense": "identity_kernel",
            "motives_and_values": "identity_kernel",
            "share_candidates": "discovery_runtime.ready_to_share",
            "memory_echo": "not_provided_by_this_contract",
        },
        "open_loops": list(state.open_loops[-5:]),
        "latest_intention": intention,
        "personality_direction_30d": personality.get("direction_30d", {}),
        "personality_direction_profile_30d": personality.get("direction_profile_30d", {}),
        "personality_integration": personality.get("integration", {}),
        "personality_adaptation": personality.get("adaptation", {}),
        "personality_self_narrative": personality.get("self_narrative", {}),
        "personality_relationship_profiles": personality.get("relationship_profiles", {}),
        "active_personality_forms": personality.get("active_forms", []),
        "active_identity_forms": identity.get("active_forms", []),
        "active_motives": identity.get("active_motives", []),
        "activated_defenses": identity.get("active_defenses", []),
        "active_conflict": identity.get("active_conflict", {}),
        "value_choice": identity.get("latest_value_choice", {}),
        "self_interpretation": observation.get("self_interpretation", {}),
        "expression": expression_view,
        "discovery": {
            "current_activity": discovery_view.get("current_activity", {}),
            "ready_to_share": discovery_view.get("ready_to_share", []),
        },
        "latest_observation": {
            key: observation.get(key)
            for key in (
                "user_act",
                "relationship_meaning",
                "continuity",
                "repair_needed",
                "confidence",
                "notes",
            )
            if key in observation
        },
    }
    try:
        from runtime.gemini_inner_diary import get_gemini_inner_diary_service
        gemini_diary_svc = get_gemini_inner_diary_service()
        gemini_diary_svc.maybe_trigger_generation(
            state=state,
            observation=observation,
            affect=views.get("affect"),
        )
        saved_diary = gemini_diary_svc.get_current_diary()
        if isinstance(saved_diary, dict) and saved_diary:
            private_mind_digest["inner_diary"] = saved_diary
    except Exception:
        pass
    private_life_digest = {
        "architecture": AI_RUNTIME_VERSION,
        "relationship_summary": relation.prompt_summary(),
        "personality_summary": personality.get("prompt_summary", ""),
        "personality_self_narrative": personality.get("self_narrative", {}),
        "personality_integration": personality.get("integration", {}),
        "personality_adaptation": personality.get("adaptation", {}),
        "personality_direction_profile_30d": personality.get("direction_profile_30d", {}),
        "personality_relationship_profiles": personality.get("relationship_profiles", {}),
        "personality_consolidation": personality.get("consolidation", {}),
        "identity_summary": state.identity_prompt_context(),
        "personality_version": personality.get("version", 0),
        "identity_version": identity.get("version", 0),
        "identity_imprints": identity.get("imprints", [])[-6:],
        "pending_strategies": identity.get("pending_strategies", [])[-3:],
        "recent_trajectory": personality.get("recent_trajectory", [])[-6:],
        "last_event": relation.last_event,
        "memory_candidates": [dict(item) for item in state.memory_candidates[-4:]],
        "discovery_archive_count": int(discovery_view.get("archive_count", 0) or 0),
        "discovery_recent": list(discovery_view.get("recent", []) or [])[-4:],
        "initiative": initiative_view,
        "consolidation": consolidator_view,
    }
    return {
        "identity_runtime": identity_runtime,
        "emotion_debug": emotion_debug,
        "depth_v2": depth_v2,
        "private_mind_digest": private_mind_digest,
        "private_life_digest": private_life_digest,
        "cognitive_views": views,
    }


__all__ = ["build_ai_panel_payload"]

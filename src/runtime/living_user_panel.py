# -*- coding: utf-8 -*-
"""Protocol quản lý Living User Memory cho panel Godot.

Module này chỉ chuyển đổi request/response. Quyền sửa, quên và áp dụng policy vẫn
nằm trong ``LivingUserMemoryService``. UI không được phép mở rồi ghi thẳng JSON,
vì cách đó rất nhanh cho tới ngày hai tiến trình cùng lưu và profile biến thành
một bài thơ hậu hiện đại không parse được.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Mapping



@dataclass(frozen=True, slots=True)
class MemoryPanelResult:
    status: str
    message: str
    payload: dict[str, Any]


def handle_memory_panel_request(
    living_user: Any,
    request: Mapping[str, Any],
    *,
    cleanup_service: Any = None,
    portrait_presenter: Any = None,
    expression_lens: Mapping[str, Any] | None = None,
) -> MemoryPanelResult:
    """Thực thi một action panel và luôn trả snapshot mới sau mutation."""
    if living_user is None:
        return MemoryPanelResult("error", "Living User Memory chưa sẵn sàng.", {})

    action = str(request.get("action") or "snapshot").strip().lower()
    project_id = str(request.get("project_id") or "").strip()
    path = str(request.get("path") or "").strip()
    message = "Đã đọc memory."
    extra: dict[str, Any] = {}
    memory_changed = False

    try:
        if action == "snapshot":
            pass
        elif action == "explain":
            if not path:
                raise ValueError("missing_path")
            extra["explanation"] = [
                _explanation_to_dict(row)
                for row in living_user.explain(path, project_id=project_id)
            ]
            message = "Đã đọc nguồn hình thành memory."
        elif action in {"correct", "confirm"}:
            if not path:
                raise ValueError("missing_path")
            record = living_user.get_memory(path, project_id=project_id)
            value = _coerce_edited_value(request.get("value"), record)
            evidence_id = str(request.get("evidence_id") or f"memory_panel_{int(time.time() * 1000)}")
            result = (
                living_user.confirm(path, project_id=project_id, evidence_ids=(evidence_id,))
                if action == "confirm"
                else living_user.correct(path, value, evidence_ids=(evidence_id,), project_id=project_id)
            )
            if not getattr(result, "changed", False):
                rejected = ", ".join(getattr(result, "rejected", ()) or ())
                raise RuntimeError(rejected or "memory_update_rejected")
            memory_changed = True
            message = "Đã xác nhận memory." if action == "confirm" else "Đã cập nhật memory."
        elif action in {"forget", "reject"}:
            if not path:
                raise ValueError("missing_path")
            hard = bool(request.get("hard", False)) if action == "forget" else False
            changed = living_user.forget(path, project_id=project_id, hard=hard)
            memory_changed = bool(changed)
            message = (
                "Đã xóa hoàn toàn memory." if hard
                else "Đã ngừng sử dụng memory này."
            ) if changed else "Không tìm thấy memory cần quên."
        elif action == "move_scope":
            if not path:
                raise ValueError("missing_path")
            target_kind = str(request.get("target_scope_kind") or "global").strip().lower()
            target_key = str(request.get("target_scope_key") or "").strip()
            result = living_user.move_scope(
                path, project_id=project_id, target_scope_kind=target_kind, target_scope_key=target_key,
            )
            if not getattr(result, "changed", False):
                rejected = ", ".join(getattr(result, "rejected", ()) or ())
                if rejected != "scope_unchanged":
                    raise RuntimeError(rejected or "scope_move_rejected")
            memory_changed = bool(getattr(result, "changed", False))
            project_id = target_key if target_kind == "project" else ""
            message = "Đã đổi phạm vi sử dụng memory."
        elif action == "set_auto_cleanup":
            if cleanup_service is None or not callable(getattr(cleanup_service, "set_auto_cleanup", None)):
                raise RuntimeError("cleanup_service_unavailable")
            enabled = bool(request.get("enabled", False))
            actual = bool(cleanup_service.set_auto_cleanup(enabled))
            message = "Đã bật DeepSeek tự dọn memory." if actual else "Đã tắt DeepSeek tự dọn memory."
        elif action == "cleanup_now":
            if cleanup_service is None:
                raise RuntimeError("cleanup_service_unavailable")
            request_cleanup = getattr(cleanup_service, "request_cleanup", None)
            if callable(request_cleanup):
                request_result = request_cleanup()
                extra["cleanup_request"] = _json_safe(request_result)
                message = (
                    "Đã giao DeepSeek rà memory ở background."
                    if bool(request_result.get("accepted"))
                    else "Memory cleanup đang chạy; không tạo lượt dọn trùng."
                )
            elif callable(getattr(cleanup_service, "run_once", None)):
                report = cleanup_service.run_once(force=True)
                report_dict = report.to_dict() if callable(getattr(report, "to_dict", None)) else {}
                extra["cleanup_report"] = report_dict
                message = _cleanup_message(report_dict)
            else:
                raise RuntimeError("cleanup_service_unavailable")
        else:
            raise ValueError(f"unsupported_action:{action}")

        # Xóa bản tóm tắt cũ ngay sau khi user sửa/quên. Gemini đang viết dở
        # cũng không được ghi nội dung trễ trở lại file.
        if memory_changed and callable(getattr(portrait_presenter, "invalidate", None)):
            portrait_presenter.invalidate()
        payload = build_memory_panel_snapshot(
            living_user,
            include_inferred=bool(request.get("include_inferred", True)),
            project_id=project_id,
            cleanup_service=cleanup_service,
            portrait_presenter=portrait_presenter,
            expression_lens=expression_lens,
            force_portrait_refresh=bool(request.get("refresh_portrait", False)),
        )
        payload.update(extra)
        return MemoryPanelResult("ok", message, payload)
    except Exception as exc:
        payload = build_memory_panel_snapshot(
            living_user,
            include_inferred=bool(request.get("include_inferred", True)),
            project_id=project_id,
            cleanup_service=cleanup_service,
            portrait_presenter=portrait_presenter,
            expression_lens=expression_lens,
            force_portrait_refresh=False,
        )
        return MemoryPanelResult(
            "error",
            _friendly_error(exc),
            payload,
        )


def build_memory_panel_snapshot(
    living_user: Any,
    *,
    include_inferred: bool = True,
    project_id: str = "",
    limit: int = 48,
    cleanup_service: Any = None,
    portrait_presenter: Any = None,
    expression_lens: Mapping[str, Any] | None = None,
    force_portrait_refresh: bool = False,
) -> dict[str, Any]:
    """Tạo payload UI nhỏ, ổn định và không chứa memory nhạy cảm."""
    raw_records = list(living_user.list_memories(
        include_inferred=include_inferred,
        include_sensitive=False,
        project_id=project_id,
        limit=max(limit * 4, 160),
    ))
    visible_records = _select_user_readable_records(raw_records, limit=limit)
    records = [_record_to_panel_dict(record) for record in visible_records]

    actions = living_user.action_snapshot()
    goals = _compact_action_rows(actions.get("goals"), "goal", 18)
    commitments = _compact_action_rows(actions.get("commitments"), "commitment", 18)
    workflows = _compact_action_rows(actions.get("workflows"), "workflow", 18)

    # Main panel không còn kể canonical record bằng giọng database. Episode +
    # canonical records được đưa qua Surface worker để thành một portrait có giọng
    # ERIS. Raw records vẫn còn ở nhánh Quản lý để user audit/sửa/xóa.
    episodes = _recent_episode_rows(living_user, project_id=project_id, limit=6)
    portrait = _render_memory_portrait(
        portrait_presenter,
        records=records,
        episodes=episodes,
        expression_lens=expression_lens or {},
        force=force_portrait_refresh,
    )

    cleanup_status = _cleanup_status(cleanup_service)
    # Feed trace là telemetry/debug, không phải thứ user cần đọc trong "ERIS nhớ gì".
    # Journal vẫn tồn tại cho developer/audit; payload user-facing không dump nó nữa.
    counts = {
        "total": len(records),
        "confirmed": sum(1 for row in records if row["explicitness"] == "explicit"),
        "observed": sum(1 for row in records if row["explicitness"] == "observed"),
        "inferred": sum(1 for row in records if row["explicitness"] == "inferred"),
        "uncertain": sum(1 for row in records if float(row["confidence"]) < 0.72),
        "goals": len(goals),
        "commitments": len(commitments),
        "workflows": len(workflows),
    }
    return {
        "schema_version": 4,
        "authority_mode": "deepseek_canonical_projection",
        "memory_policy": "v68_curator_authoritative_working_store",
        "summary_text": str(portrait.get("opening") or _render_panel_brief(records)),
        "portrait": portrait,
        "counts": counts,
        "records": records,
        "goals": goals,
        "commitments": commitments,
        "workflows": workflows,
        "cleanup": cleanup_status,
        "project_id": project_id,
        "generated_at": time.time(),
    }



_GARBAGE_PATH_RE = re.compile(r"(?:^|\.)upr(?:[_\.-]?)[0-9a-f]{8,}(?:\.|$)", re.IGNORECASE)
_NON_PROFILE_PREFIXES = (
    "open_loops.", "goals.open_loops", "runtime.", "tracking.", "telemetry.",
    "internal.", "debug.", "temporary.",
)

def _select_user_readable_records(records: list[Any], *, limit: int) -> list[Any]:
    """Panel chỉ hiển thị projection có giá trị đọc, không dump working DB.

    DeepSeek V68 đánh dấu ``canonical_user_memory`` sau khi canonicalize/rewrite/merge.
    Nếu profile cũ chưa từng được curator V68 chạy, fallback bảo thủ chỉ giữ path
    sạch và mỗi path/scope một record mạnh nhất. Raw UPR/open-loop/technical state
    biến mất khỏi UI ngay cả trước lượt cleanup đầu tiên.
    """
    usable = [r for r in records if not _record_is_ui_garbage(r)]
    canonical = [r for r in usable if bool(getattr(r, "metadata", {}).get("canonical_user_memory"))]
    pool = canonical if canonical else usable
    best: dict[tuple[str, str], Any] = {}
    for record in pool:
        key = (str(getattr(record, "path", "")), str(getattr(getattr(record, "scope", None), "identity", "global")))
        current = best.get(key)
        if current is None or (float(getattr(record, "trust_weight", 0.0)), float(getattr(record, "updated_at", 0.0))) > (
            float(getattr(current, "trust_weight", 0.0)), float(getattr(current, "updated_at", 0.0))
        ):
            best[key] = record
    rows = sorted(best.values(), key=lambda r: (-float(getattr(r, "trust_weight", 0.0)), -float(getattr(r, "updated_at", 0.0))))
    return rows[: max(1, int(limit))]

def _record_is_ui_garbage(record: Any) -> bool:
    metadata = getattr(record, "metadata", {}) or {}
    if bool(metadata.get("hidden_from_ui")):
        return True
    path = str(getattr(record, "path", "") or "").strip().lower()
    if not path or _GARBAGE_PATH_RE.search(path) or path.startswith(_NON_PROFILE_PREFIXES):
        return True
    value = str(getattr(record, "value", "") or "").strip()
    if not value or value.lower() in {"none", "null", "unknown", "n/a"}:
        return True
    # ID kỹ thuật lộ thẳng ra value không phải memory người dùng cần đọc.
    if re.fullmatch(r"(?:upr_)?[0-9a-f]{16,}", value, flags=re.IGNORECASE):
        return True
    return False



def build_memory_expression_lens(internal_state: Any, personality_profile: Any = None) -> dict[str, Any]:
    """Chiếu private state thành *quyền diễn xuất* semantic cho Surface writer.

    Không đưa ``last_thought_seed``, diary, residue hay bất kỳ raw thought nào vào
    panel prompt. Worker chỉ biết ERIS đang được phép gần/trêu/ngượng tới đâu.
    """
    personality_summary = ""
    render_personality = getattr(personality_profile, "prompt_summary", None)
    if callable(render_personality):
        try:
            personality_summary = " ".join(str(render_personality(limit=520) or "").split())[:520]
        except Exception:
            personality_summary = ""

    if internal_state is None:
        return {
            "relationship": "chưa đủ state để suy nhịp quan hệ; giữ khoảng cách vừa phải",
            "warmth": "ấm vừa, không fanservice",
            "playfulness": "trêu rất nhẹ nếu evidence tự mở đường",
            "self_exposure": "kín; nếu lỡ nói gần thì có thể chữa lời một nhịp",
            "character": personality_summary or "ERIS kín, quan sát chéo, phân tích nhiều, hài khô; affection rò gián tiếp thay vì tuyên bố",
            "anime_acting": "được ngập ngừng/aside nhẹ; không spam tiếng Nhật hay baby-talk",
        }

    relation = getattr(internal_state, "relationship_dynamics", None)
    familiarity = _state_float(relation, "familiarity", 0.0)
    trust = _state_float(relation, "trust", 0.25)
    preferred_distance = _state_float(relation, "preferred_distance", 0.58)
    attachment_visibility = _state_float(relation, "attachment_visibility", 0.12)
    recoil = _state_float(relation, "exposure_recoil", 0.0)
    dignity = _state_float(relation, "dignity_defense", 0.0)
    tolerates_strangeness = _state_float(relation, "user_tolerates_strangeness", 0.0)

    attachment = _state_float(internal_state, "attachment", 0.30)
    tenderness = _state_float(internal_state, "tenderness", 0.16)
    playfulness = _state_float(internal_state, "playful_pressure", 0.0)
    restraint = _state_float(internal_state, "restraint_pressure", 0.08)
    irritation = _state_float(internal_state, "irritation", 0.10)
    affective = getattr(internal_state, "affective_state", {}) or {}
    embarrassment = _mapping_float(affective, "embarrassment", 0.0)

    closeness = max(0.0, min(1.0, familiarity * 0.40 + trust * 0.30 + (1.0 - preferred_distance) * 0.20 + attachment_visibility * 0.10))
    warmth = max(0.0, min(1.0, attachment * 0.45 + tenderness * 0.45 + trust * 0.10))
    tease = max(0.0, min(1.0, playfulness * 0.55 + tolerates_strangeness * 0.30 + familiarity * 0.15))
    guarded = max(0.0, min(1.0, preferred_distance * 0.40 + restraint * 0.28 + recoil * 0.20 + dignity * 0.12))
    shy = max(0.0, min(1.0, embarrassment * 0.45 + recoil * 0.25 + warmth * 0.18 + guarded * 0.12))

    return {
        "relationship": (
            "đã khá quen; có thể nói như hai người có lịch sử chung" if closeness >= 0.62
            else "đã quen nhịp; bớt khách sáo nhưng chưa được tự tiện thân mật" if closeness >= 0.40
            else "còn giữ khoảng cách; tránh diễn như đã rất thân"
        ),
        "warmth": (
            "ấm rõ nhưng giấu qua chi tiết, không tuyên bố tình cảm" if warmth >= 0.58
            else "ấm vừa; quan tâm bằng việc nhớ chi tiết hơn là lời ngọt" if warmth >= 0.35
            else "khá phẳng; đừng cố làm mềm giả tạo"
        ),
        "playfulness": (
            "được trêu khô và tự chữa lời khá tự nhiên" if tease >= 0.55
            else "được chọc nhẹ một nhịp" if tease >= 0.30
            else "ưu tiên quan sát, ít pha trò"
        ),
        "self_exposure": (
            "có thể để lộ một câu hơi gần rồi ngượng/rút lại" if shy >= 0.40 and guarded < 0.72
            else "kín; nếu có aside thì ngắn, không phơi nội tâm"
        ),
        "irritation": (
            "có cạnh khô; được cà khịa nhẹ nhưng không trách móc" if irritation >= 0.34
            else "không cần cạnh khó chịu"
        ),
        "character": personality_summary or "ERIS kín, quan sát chéo, phân tích nhiều, hài khô; affection rò gián tiếp, có tự trọng và không làm fanservice rẻ",
        "anime_acting": "được ngập ngừng, câu bị ngắt, tự chối lời khen, aside kiểu '...mong là bạn không đọc kỹ'; thỉnh thoảng 'e-eh?'/'mou...' nếu hợp nhịp, không spam",
    }


def _state_float(owner: Any, name: str, default: float) -> float:
    try:
        value = float(getattr(owner, name, default)) if owner is not None else float(default)
    except (TypeError, ValueError):
        value = float(default)
    if value != value:
        value = float(default)
    return max(0.0, min(1.0, value))


def _mapping_float(owner: Mapping[str, Any], name: str, default: float) -> float:
    try:
        value = float(owner.get(name, default))
    except (TypeError, ValueError):
        value = float(default)
    if value != value:
        value = float(default)
    return max(0.0, min(1.0, value))


def _recent_episode_rows(living_user: Any, *, project_id: str, limit: int) -> list[dict[str, Any]]:
    store = getattr(living_user, "episode_store", None)
    all_rows = getattr(store, "all", None)
    if not callable(all_rows):
        return []
    try:
        episodes = list(all_rows())
    except Exception:
        return []
    visible: list[dict[str, Any]] = []
    for episode in episodes:
        scope = getattr(episode, "scope", None)
        kind = str(getattr(scope, "kind", "global") or "global")
        key = str(getattr(scope, "key", "") or "")
        if kind not in {"global", "project"}:
            continue
        if kind == "project" and (not project_id or key != project_id):
            continue
        summary = " ".join(str(getattr(episode, "summary", "") or "").split()).strip()
        if not summary:
            continue
        visible.append({
            "id": str(getattr(episode, "id", "") or ""),
            "title": " ".join(str(getattr(episode, "title", "") or "Một chuyện cũ").split())[:160],
            "summary": summary[:760],
            "outcome": " ".join(str(getattr(episode, "outcome", "") or "").split())[:260],
            "importance": round(_state_float(episode, "importance", 0.5), 4),
            "ended_at": float(getattr(episode, "ended_at", 0.0) or 0.0),
        })
        if len(visible) >= max(1, int(limit)):
            break
    return visible


def _render_memory_portrait(
    presenter: Any,
    *,
    records: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    expression_lens: Mapping[str, Any],
    force: bool,
) -> dict[str, Any]:
    render = getattr(presenter, "render", None)
    if callable(render):
        try:
            value = render(
                records=records,
                episodes=episodes,
                expression_lens=expression_lens,
                force=force,
            )
            if callable(getattr(value, "to_dict", None)):
                payload = value.to_dict()
            elif isinstance(value, Mapping):
                payload = dict(value)
            else:
                payload = {}
            if str(payload.get("opening") or "").strip():
                return _json_safe(payload)
        except Exception:
            pass
    return _fallback_memory_portrait(records, episodes)


def _fallback_memory_portrait(records: list[dict[str, Any]], episodes: list[dict[str, Any]]) -> dict[str, Any]:
    if not records and not episodes:
        return {
            "opening": "Tôi chưa có gì đủ chắc để kể về bạn cả. Hừm… chắc phải nói chuyện thêm một chút đã.",
            "aside": "Mà thế này cũng hơi giống bắt tôi làm bài kiểm tra trí nhớ nhỉ…",
            "sections": [],
            "renderer": "deterministic_fallback",
        }
    sections: list[dict[str, Any]] = []
    by_kind: dict[str, list[dict[str, Any]]] = {
        "how_i_see_you": [], "things_you_like": [], "not_sure": [],
    }
    for row in records[:12]:
        value = str(row.get("display_value") or "").strip()
        if not value:
            continue
        ref = f"mem:{row.get('path') or ''}:{row.get('scope_key') or 'global'}"
        item = {
            "title": str(row.get("label") or "Một điều về bạn"),
            "body": (f"Tôi chưa chắc lắm, nhưng có vẻ {value}." if row.get("explicitness") == "inferred" else f"Cái này thì tôi nhớ: {value}."),
            "aside": "…Nếu sai thì coi như chưa đọc thấy nhé." if row.get("explicitness") == "inferred" else "",
            "source_refs": [ref],
        }
        if row.get("explicitness") == "inferred" or float(row.get("confidence") or 0.0) < 0.62:
            by_kind["not_sure"].append(item)
        elif row.get("category") == "preferences":
            by_kind["things_you_like"].append(item)
        else:
            by_kind["how_i_see_you"].append(item)
    titles = {
        "how_i_see_you": "Trong mắt tôi",
        "things_you_like": "Mấy thứ bạn thích",
        "not_sure": "Có vài chuyện tôi chưa dám chắc",
    }
    for kind in ("how_i_see_you", "things_you_like"):
        if by_kind[kind]:
            sections.append({"kind": kind, "title": titles[kind], "items": by_kind[kind][:3]})
    if episodes:
        sections.append({
            "kind": "our_memories",
            "title": "Chuyện của hai đứa",
            "items": [
                {
                    "title": str(row.get("title") or "Một chuyện tôi còn nhớ"),
                    "body": str(row.get("summary") or "")[:520],
                    "aside": "…Ừ, chuyện này tôi vẫn chưa quên.",
                    "source_refs": [f"ep:{row.get('id') or ''}"],
                }
                for row in episodes[:3] if row.get("id") and row.get("summary")
            ],
        })
    if by_kind["not_sure"]:
        sections.append({"kind": "not_sure", "title": titles["not_sure"], "items": by_kind["not_sure"][:2]})
    opening = _render_panel_brief(records)
    return {
        "opening": opening,
        "aside": "…Đừng có soi tôi đang nhớ kỹ đến mức nào.",
        "sections": sections[:4],
        "renderer": "deterministic_fallback",
    }

def _render_panel_brief(records: list[dict[str, Any]]) -> str:
    if not records:
        return "ERIS chưa có đủ thông tin bền vững để tổng hợp về bạn."
    preferred = sorted(
        records,
        key=lambda row: (
            0 if row.get("explicitness") == "explicit" else 1 if row.get("explicitness") == "observed" else 2,
            -float(row.get("confidence") or 0.0),
        ),
    )[:6]
    parts = []
    for row in preferred:
        label = str(row.get("label") or row.get("path") or "Thông tin")
        value = str(row.get("display_value") or "").strip()
        if not value:
            continue
        parts.append(f"{label.lower()} là {value}")
    if not parts:
        return "ERIS đang có một số dấu vết về bạn, nhưng chưa đủ rõ để tổng hợp."
    return "ERIS đang nhớ rằng " + "; ".join(parts) + "."

def _record_to_panel_dict(record: Any) -> dict[str, Any]:
    value = record.value
    if isinstance(value, (dict, list, tuple)):
        display_value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    elif isinstance(value, bool):
        display_value = "Có" if value else "Không"
    else:
        display_value = str(value)
    confidence = float(record.confidence)
    if record.explicitness == "explicit":
        certainty_label = "Đã xác nhận"
    elif confidence >= 0.82:
        certainty_label = "Quan sát khá chắc"
    elif confidence >= 0.62:
        certainty_label = "Cần xác nhận"
    else:
        certainty_label = "Suy đoán yếu"
    return {
        "id": record.id,
        "path": record.path,
        "label": _humanize_path(record.path),
        "category": _category_for_path(record.path),
        "display_value": display_value[:360],
        "value_type": record.value_type,
        "confidence": round(confidence, 5),
        "certainty_label": certainty_label,
        "explicitness": record.explicitness,
        "explicitness_label": {
            "explicit": "Bạn đã nói",
            "observed": "ERIS đã quan sát",
            "inferred": "ERIS đang suy đoán",
        }.get(record.explicitness, "ERIS đang suy đoán"),
        "scope_kind": record.scope.kind,
        "scope_key": record.scope.key,
        "scope_label": _scope_label(record.scope.kind, record.scope.key),
        "source": record.source,
        "evidence_count": len(record.evidence_ids),
        "recurrence": int(record.recurrence),
        "updated_at": float(record.updated_at),
    }


def _coerce_edited_value(value: Any, record: Any | None) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if record is None or isinstance(record.value, str):
        return text
    try:
        return json.loads(text)
    except Exception:
        if isinstance(record.value, bool):
            return text.lower() in {"1", "true", "yes", "có", "co"}
        if isinstance(record.value, int):
            return int(text)
        if isinstance(record.value, float):
            return float(text)
        return text


def _explanation_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        raw = value.to_dict()
    elif is_dataclass(value):
        raw = asdict(value)
    elif hasattr(value, "__dict__"):
        raw = dict(value.__dict__)
    else:
        raw = {"detail": str(value)}
    safe = _json_safe(raw)
    # Chuẩn hóa tên field cho UI; panel không cần biết class Python phía sau.
    sources = safe.get("sources") if isinstance(safe, dict) else []
    return {
        "path": str(safe.get("path") or "") if isinstance(safe, dict) else "",
        "value": safe.get("value") if isinstance(safe, dict) else None,
        "confidence": safe.get("confidence", 0.0) if isinstance(safe, dict) else 0.0,
        "explicitness": str(safe.get("explicitness") or "") if isinstance(safe, dict) else "",
        "note": str(safe.get("note") or safe.get("detail") or "") if isinstance(safe, dict) else str(value),
        "sources": sources if isinstance(sources, list) else [],
    }


def _compact_action_rows(value: Any, kind: str, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for raw in value[:limit]:
        if not isinstance(raw, Mapping):
            continue
        scope = raw.get("scope") if isinstance(raw.get("scope"), Mapping) else {}
        common = {
            "id": str(raw.get("id") or ""),
            "status": str(raw.get("status") or ""),
            "scope": {"kind": str(scope.get("kind") or "global"), "key": str(scope.get("key") or "")},
        }
        if kind == "goal":
            common.update({
                "title": str(raw.get("title") or "")[:240],
                "description": str(raw.get("description") or "")[:360],
                "next_actions": [str(x)[:180] for x in list(raw.get("next_actions") or [])[:6]],
            })
        elif kind == "commitment":
            common.update({
                "actor": str(raw.get("actor") or "")[:80],
                "action": str(raw.get("action") or "")[:360],
            })
        else:
            common.update({
                "trigger": str(raw.get("trigger") or "")[:240],
                "steps": [str(x)[:180] for x in list(raw.get("steps") or [])[:8]],
            })
        rows.append(common)
    return rows


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _category_for_path(path: str) -> str:
    root = str(path or "").split(".", 1)[0]
    return {
        "user": "about",
        "identity": "about",
        "communication": "communication",
        "preferences": "preferences",
        "preference": "preferences",
        "projects": "projects",
        "goals": "projects",
        "technical": "workflow",
        "workflow": "workflow",
        "constraints": "workflow",
        "relationship": "interaction",
    }.get(root, "other")


def _humanize_path(path: str) -> str:
    aliases = {
        "user.name": "Tên của bạn",
        "user.nickname": "Cách bạn muốn được gọi",
        "identity.gender": "Giới tính",
        "communication.language": "Ngôn ngữ giao tiếp",
        "communication.response_style": "Cách bạn muốn ERIS trả lời",
        "technical.skill_level": "Mức độ kỹ thuật",
        "technical.architecture_preference": "Ưu tiên kiến trúc",
        "workflow.delivery_preference": "Cách bạn muốn nhận kết quả",
    }
    if path in aliases:
        return aliases[path]
    tail = str(path or "").split(".")[-1].replace("_", " ").strip()
    return tail[:1].upper() + tail[1:] if tail else path


def _scope_label(kind: str, key: str) -> str:
    if kind == "project":
        return f"Chỉ dự án {key}" if key else "Theo dự án"
    if kind == "session":
        return "Chỉ phiên hiện tại"
    if kind == "temporary":
        return "Tạm thời"
    return "Mọi cuộc trò chuyện"


def _cleanup_status(cleanup_service: Any) -> dict[str, Any]:
    if cleanup_service is None:
        return {"available": False, "auto_cleanup": False, "last_result": {}}
    snapshot = getattr(cleanup_service, "status_snapshot", None)
    if callable(snapshot):
        try:
            value = snapshot()
            if isinstance(value, Mapping):
                return _json_safe(value)
        except Exception:
            pass
    return {
        "available": True,
        "auto_cleanup": bool(getattr(cleanup_service, "auto_cleanup_enabled", False)),
        "last_result": _json_safe(getattr(cleanup_service, "last_result", {}) or {}),
    }


def _cleanup_message(report: Mapping[str, Any]) -> str:
    reviewed = int(report.get("ds_reviewed", 0) or 0) + int(report.get("living_reviewed", 0) or 0)
    changed = (
        int(report.get("ds_dropped", 0) or 0)
        + int(report.get("living_applied", 0) or 0)
        + int(report.get("behavior_removed", 0) or 0)
        + int(report.get("thought_removed", 0) or 0)
    )
    return f"DeepSeek đã rà {reviewed} mục; runtime áp dụng {changed} thay đổi an toàn."


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    mapping = {
        "missing_path": "Thiếu đường dẫn memory.",
        "memory_not_found": "Không tìm thấy memory này.",
        "missing_project_id": "Cần nhập mã dự án trước khi đổi phạm vi.",
        "unsupported_scope": "Panel hiện chỉ hỗ trợ phạm vi toàn cục hoặc theo dự án.",
        "cleanup_service_unavailable": "DeepSeek Memory Curator chưa sẵn sàng.",
    }
    return mapping.get(text, f"Không thể xử lý memory: {text[:180]}")


__all__ = [
    "MemoryPanelResult",
    "build_memory_expression_lens",
    "build_memory_panel_snapshot",
    "handle_memory_panel_request",
]

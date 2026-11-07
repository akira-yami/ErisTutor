from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Mapping


_AXIS_LABELS = {
    "warmth_temperature": ("warmer", "cooler"),
    "preferred_distance": ("more distance", "closer"),
    "read_boldness": ("bolder reading", "more reserved reading"),
    "attachment_visibility": ("more visible attachment", "more hidden attachment"),
}

_TIME_OF_DAY = (
    (5, 12, "morning"),
    (12, 18, "afternoon"),
    (18, 24, "evening"),
    (0, 5, "late_night"),
)

_SIGNAL_TEXT = {
    "tom_read": "cập nhật cách đọc bạn.",
    "expanded": "mở rộng một mạch nghĩ.",
    "interior": "dịch vào dòng nội tâm.",
    "surface": "chuẩn bị câu phản hồi.",
    "private_mind": "private_mind vừa tick.",
}

_SECTION_ALIASES = {
    "bang cam xuc": "emotion_board",
    "suy nghi hien tai": "current",
    "suy nghi tuong lai": "future",
    "dang ban tam": "concern",
    "phan tich gan day": "analysis",
    "muon noi": "surface",
    "da nguoi": "settled",
}

_PRIORITY_DEFS = (
    ("technical", "Architecture / Technical"),
    ("humor", "Humor / Playfulness"),
    ("repair", "Repair / Help"),
    ("emotional", "Emotional Reading"),
    ("casual", "Small Talk / Casual"),
)

_PRIORITY_KEYWORDS = {
    "technical": (
        "architecture",
        "technical",
        "code",
        "debug",
        "runtime",
        "schema",
        "panel",
        "ui",
        "backend",
        "test",
        "refactor",
        "implement",
    ),
    "humor": ("humor", "playful", "banter", "joke", "tease", "fun"),
    "repair": ("repair", "help", "fix", "issue", "error", "failing", "broken", "support", "unblock"),
    "emotional": ("emotion", "affect", "feeling", "mood", "comfort", "worry", "concern", "gentle"),
    "casual": ("casual", "small talk", "hello", "hi", "idle", "light", "chat"),
}


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _clamp01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


def _clamp_signed(value: Any, default: float = 0.0) -> float:
    return max(-1.0, min(1.0, _float(value, default)))


def _memory_core_dir() -> Path:
    raw = str(os.getenv("ERIS_MEMORY_CORE_DIR", "") or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2] / "memory_core"


def _read_json(name: str) -> Any:
    try:
        return json.loads((_memory_core_dir() / name).read_text(encoding="utf-8"))
    except Exception:
        return None


def _development_summary() -> dict[str, Any]:
    data = _read_json("development_profile.json")
    axes_out: list[dict[str, Any]] = []
    if isinstance(data, dict):
        axes = data.get("axes") or {}
        if isinstance(axes, dict):
            for name, payload in axes.items():
                delta = _float(_dict(payload).get("delta"), 0.0)
                if abs(delta) < 1e-6:
                    continue
                pos, neg = _AXIS_LABELS.get(name, (f"{name}+", f"{name}-"))
                axes_out.append(
                    {
                        "axis": str(name),
                        "label": pos if delta > 0 else neg,
                        "delta": round(delta, 3),
                    }
                )
    attachment = _attachment_summary()
    return {
        "live": bool(axes_out),
        "axes": axes_out,
        "sentence": (
            "A little has shifted since the beginning."
            if axes_out
            else "Almost unchanged from the beginning."
        ),
        "attachment": attachment,
    }


def _attachment_summary() -> dict[str, Any]:
    data = _read_json("anchor_state.json")
    value = _float(_dict(data).get("attachment_longarc"), 0.0) if isinstance(data, dict) else 0.0
    return {"live": isinstance(data, dict), "value": round(value, 3)}


def _anchor_depth_summary(private_mind_digest: Mapping[str, Any] | None) -> dict[str, Any]:
    """Lấy phần Mô_Hình_bạn/neo từ private_mind digest cho depth_v2.

    Lỗi cũ: depth_v2 có `tom` nhưng không chở `user_model` và `significance`,
    nên body chỉ biết một câu ToM thoáng qua, không biết "độ dày mô hình",
    số belief, ý nghĩa, gắn bó dài hạn. Hàm này giữ payload gọn nhưng đủ dữ liệu
    để panel không phải đoán từ text.
    """
    anchor = _dict(_dict(private_mind_digest).get("anchor"))
    user_model = _dict(anchor.get("user_model"))
    significance = _dict(anchor.get("significance"))
    attachment = _dict(anchor.get("attachment"))
    switches = _dict(anchor.get("switches"))
    deception = _dict(anchor.get("deception"))

    raw_beliefs = _list(user_model.get("beliefs"))
    beliefs: list[dict[str, Any]] = []
    for raw in raw_beliefs[:8]:
        item = _dict(raw)
        claim = _text(item.get("claim"), limit=160)
        if not claim:
            continue
        beliefs.append(
            {
                "facet": _text(item.get("facet"), limit=40),
                "claim": claim,
                "confidence": round(_clamp01(item.get("confidence"), 0.0), 3),
                "recurrence": int(_float(item.get("recurrence"), 1.0)),
                "evidence_count": int(_float(item.get("evidence_count"), 0.0)),
            }
        )

    belief_count = int(_float(user_model.get("belief_count"), len(beliefs)))
    model_thickness = _clamp01(
        user_model.get("model_thickness"),
        _clamp01(significance.get("model_thickness"), 0.0),
    )
    longarc = _clamp01(attachment.get("longarc"), _clamp01(_attachment_summary().get("value"), 0.0))

    return {
        "user_model": {
            "live": bool(belief_count or beliefs or model_thickness > 0.0),
            "belief_count": belief_count,
            "model_thickness": round(model_thickness, 4),
            "beliefs": beliefs,
        },
        "significance": {
            "irreplaceability": round(_clamp01(significance.get("irreplaceability"), 0.0), 4),
            "anchor_role": round(_clamp01(significance.get("anchor_role"), 0.0), 4),
            "witness_strength": round(_clamp01(significance.get("witness_strength"), 0.0), 4),
            "model_thickness": round(model_thickness, 4),
        },
        "attachment_anchor": {
            "live": bool(attachment),
            "longarc": round(longarc, 4),
            "beat_ema": round(_clamp01(attachment.get("beat_ema"), 0.0), 4),
            "recognition_ema": round(_clamp01(attachment.get("recognition_ema"), 0.0), 4),
            "total_beats": int(_float(attachment.get("total_beats"), 0.0)),
        },
        "safety": {
            "shadow_enabled": bool(switches.get("shadow_enabled", False)),
            "deception_enabled": bool(switches.get("deception_enabled", False)),
            "strict_grounding": bool(switches.get("strict_grounding", True)),
            "safe_mode": bool(switches.get("safe_mode", True)),
            "deception_count": int(_float(deception.get("count"), 0.0)),
        },
    }


def _autoswitch_block(
    *,
    flags: Mapping[str, Any] | None,
    safety: Mapping[str, Any] | None,
    expression_gate: Mapping[str, Any] | None,
    tom_text: str,
) -> dict[str, Any]:
    """Tóm tắt riêng cho khung ``CÔNG TẮC · TỰ BẬT``.

    Body/Godot chỉ render chip và footer, không tự đoán quá nhiều từ các block
    khác. Vì vậy Python gửi sẵn nhãn, trạng thái bật/tắt và policy đang thắng.
    Nếu flag thiếu nhưng tín hiệu live có thật, chip vẫn sáng để panel không bị
    "ảnh nền có chữ" khi runtime mới khởi động hoặc control-file chưa seed xong.
    """
    states = _dict(flags)
    safety_block = _dict(safety)
    gate = _dict(expression_gate)
    n1_live = bool(gate) and max(
        _clamp01(gate.get("want_to_say"), 0.0),
        _clamp01(gate.get("suppression_pressure"), 0.0),
        _clamp01(gate.get("allowed_expression"), 0.0),
    ) > 0.0

    chip_defs = (
        ("n1", "N1", "emotional_consequence", n1_live, "pink"),
        ("n2", "N2", "development_growth", False, "pink"),
        ("interiority", "interiority", "interior_surface", False, "pink"),
        ("mind", "mind", "private_mind", False, "mint"),
    )
    chips: list[dict[str, Any]] = []
    for key, label, flag_key, live_fallback, accent in chip_defs:
        active = _bool(states.get(flag_key), False) or bool(live_fallback)
        chips.append(
            {
                "key": key,
                "label": label,
                "active": active,
                "source": flag_key,
                "accent": accent,
            }
        )

    active_count = sum(1 for chip in chips if chip["active"])
    strict = _bool(safety_block.get("strict_grounding"), True)
    safe = _bool(safety_block.get("safe_mode"), True)
    shadow = _bool(safety_block.get("shadow_enabled"), False)
    deception = _bool(safety_block.get("deception_enabled"), False)
    if strict and safe:
        policy = "voice_guarded_plus"
    elif strict:
        policy = "strict_grounding"
    elif safe:
        policy = "safe_mode"
    elif shadow or deception:
        policy = "shadow_permitted"
    else:
        policy = "expressive"

    return {
        "mode": "adaptive" if active_count else "quiet",
        "policy": policy,
        "active_count": active_count,
        "total": len(chips),
        "chips": chips,
    }


def _signal_snapshot(max_age_s: float = 120.0) -> dict[str, Any]:
    data = _read_json("panel_snapshot.json")
    if not isinstance(data, dict):
        return {}
    ts = _float(data.get("ts"), 0.0)
    if ts <= 0.0:
        return {}
    if (time.time() - ts) > max_age_s:
        return {}
    return data


def _context_tags(now: float | None = None) -> list[str]:
    tm = time.localtime(now if now is not None else time.time())
    for start, end, label in _TIME_OF_DAY:
        if start <= tm.tm_hour < end:
            return [label, "quiet", "just_you"]
    return ["quiet", "just_you"]


def _parse_tom_confidence(text: str) -> float:
    match = re.search(r"~\s*([01](?:\.\d+)?)", str(text or ""))
    return _float(match.group(1), 0.0) if match else 0.0


def _normalize_title(title: str) -> str:
    raw = str(title or "").replace("đ", "d").replace("Đ", "D")
    stripped = "".join(ch for ch in unicodedata.normalize("NFD", raw) if not unicodedata.combining(ch))
    return " ".join(stripped.split()).casefold()


def _extract_sections(private_mind_digest: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    digest = _dict(private_mind_digest)
    diary = _dict(digest.get("inner_diary"))
    sections: list[dict[str, Any]] = []
    for raw_section in _list(diary.get("sections"))[:8]:
        section = _dict(raw_section)
        title = _text(section.get("title"), limit=80)
        if not title:
            continue
        items: list[dict[str, str]] = []
        for raw_item in _list(section.get("items"))[:6]:
            item = _dict(raw_item)
            text = _text(item.get("text"), limit=220)
            badge = _text(item.get("badge"), limit=80)
            if text:
                items.append({"text": text, "badge": badge})
        if not items:
            continue
        sections.append({"title": title, "kind": _SECTION_ALIASES.get(_normalize_title(title), "section"), "items": items})
    return sections


def _section_map(sections: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(section.get("kind") or ""): section for section in sections if section.get("kind")}


def _emotion_board(private_mind_digest: Mapping[str, Any] | None) -> dict[str, Any]:
    digest = _dict(private_mind_digest)
    board = _dict(_dict(digest.get("inner_diary")).get("emotion_board"))
    affect = _dict(digest.get("affect"))
    center = _dict(board.get("center"))
    quadrants = _dict(board.get("quadrants"))
    valence = _clamp_signed(affect.get("valence"), 0.0)
    arousal = _clamp01(affect.get("arousal"), 0.0)
    tenderness = _clamp01(affect.get("tenderness"), 0.0)
    irritation = _clamp01(affect.get("irritation"), 0.0)

    if not center:
        mood = "steady"
        if irritation >= 0.45 or valence <= -0.18:
            mood = "tense"
        elif arousal >= 0.6:
            mood = "energized"
        elif valence >= 0.18:
            mood = "light"
        center = {"label": mood, "subtext": "live affect fallback"}

    orbit = []
    for pos in ("top", "left", "right", "bottom"):
        item = _dict(quadrants.get(pos))
        label = _text(item.get("label"), limit=40) or pos
        intensity = _clamp01(item.get("intensity"), 0.22)
        orbit.append({"slot": pos, "label": label, "intensity": round(intensity, 3)})

    return {
        "headline": _text(center.get("label"), limit=80) or "steady",
        "subtext": _text(center.get("subtext"), limit=120) or "listening quietly",
        "orbit": orbit,
        "metrics": {
            "valence": round(valence, 3),
            "arousal": round(arousal, 3),
            "tenderness": round(tenderness, 3),
            "irritation": round(irritation, 3),
            "stability": round(_clamp01((1.0 - arousal) * 0.65 + (1.0 - irritation) * 0.35), 3),
            "positive_bias": round(_clamp01((valence + 1.0) * 0.5), 3),
        },
    }


def _history_point(*, ts: float, affect_card: Mapping[str, Any]) -> dict[str, Any]:
    metrics = _dict(affect_card.get("metrics"))
    return {
        "ts": round(ts, 3),
        "value": round(_clamp01(metrics.get("positive_bias"), 0.5), 3),
        "stability": round(_clamp01(metrics.get("stability"), 0.5), 3),
        "label": _text(affect_card.get("headline"), limit=48) or "steady",
    }


def _roll_affect_history(
    existing: list[dict[str, Any]] | None,
    *,
    affect_card: Mapping[str, Any],
    now: float,
    limit: int = 32,
) -> list[dict[str, Any]]:
    history = [item for item in _list(existing) if isinstance(item, Mapping)]
    point = _history_point(ts=now, affect_card=affect_card)
    if history:
        last = _dict(history[-1])
        if abs(_float(last.get("value"), 0.0) - point["value"]) < 0.01 and abs(_float(last.get("stability"), 0.0) - point["stability"]) < 0.01:
            history[-1] = point
            return history[-limit:]
    history.append(point)
    return history[-limit:]


def _priority_sources(
    private_mind_digest: Mapping[str, Any] | None,
    *,
    tom_text: str,
    expression_gate: Mapping[str, Any] | None,
) -> list[str]:
    digest = _dict(private_mind_digest)
    menu = _dict(digest.get("menu"))
    current = _dict(menu.get("current_thought") or digest.get("current"))
    future_bias = _dict(menu.get("future_bias"))
    speech_bias = _dict(future_bias.get("speech_bias"))
    sources = [
        _text(current.get("dominant_judgment"), limit=220),
        _text(tom_text, limit=220),
        _text(speech_bias.get("intent"), limit=220),
        _text(speech_bias.get("next_move"), limit=220),
        _text(speech_bias.get("stance"), limit=220),
        _text(speech_bias.get("tone"), limit=220),
    ]
    for item in _list(future_bias.get("inquiry_plan"))[:4]:
        data = _dict(item)
        sources.append(_text(data.get("question") or data.get("text") or data.get("topic") or data.get("theme"), limit=220))
    for item in _list(menu.get("open_concerns"))[:4]:
        sources.append(_text(_dict(item).get("theme"), limit=220))
    for item in _list(menu.get("surface_queue"))[:4]:
        data = _dict(item)
        sources.append(_text(data.get("topic_seed") or data.get("text"), limit=220))
    gate = _dict(expression_gate)
    if gate:
        sources.append(_text(gate.get("stance"), limit=120))
    return [src for src in sources if src]


def _derive_priorities(
    private_mind_digest: Mapping[str, Any] | None,
    *,
    tom_text: str,
    expression_gate: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    sources = _priority_sources(private_mind_digest, tom_text=tom_text, expression_gate=expression_gate)
    joined = " ".join(sources).casefold()
    gate = _dict(expression_gate)
    want = _clamp01(gate.get("want_to_say"), 0.0)
    allowed = _clamp01(gate.get("allowed_expression"), 0.0)
    concerns = _list(_dict(_dict(private_mind_digest).get("menu")).get("open_concerns"))
    priorities: list[dict[str, Any]] = []

    for key, label in _PRIORITY_DEFS:
        score = 0.12 if key != "casual" else 0.15
        hits = 0
        for token in _PRIORITY_KEYWORDS.get(key, ()):
            if token in joined:
                score += 0.12
                hits += 1
        if key == "technical" and hits:
            score += 0.18 + allowed * 0.18
        if key == "repair" and ("error" in joined or "fix" in joined or "failing" in joined):
            score += 0.20
        if key == "emotional" and tom_text:
            score += 0.22
        if key == "humor" and "banter" in joined:
            score += 0.10
        if key == "casual" and not hits:
            score += 0.05 + max(0.0, 0.20 - len(sources) * 0.01)
        if key in {"technical", "repair"} and concerns:
            score += min(0.14, len(concerns) * 0.03)
        if key in {"technical", "repair", "emotional"}:
            score += want * 0.08
        priorities.append(
            {
                "key": key,
                "label": label,
                "score": round(_clamp01(score), 3),
                "reason": _priority_reason(key, sources),
            }
        )

    priorities.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return priorities


def _priority_reason(key: str, sources: list[str]) -> str:
    keywords = _PRIORITY_KEYWORDS.get(key, ())
    for source in sources:
        lower = source.casefold()
        if any(token in lower for token in keywords):
            return _text(source, limit=120)
    return ""


def _focus_mode(priorities: list[dict[str, Any]]) -> str:
    if not priorities:
        return "attentive"
    top = priorities[0]
    key = str(top.get("key") or "")
    score = _clamp01(top.get("score"), 0.0)
    if key == "technical" and score >= 0.55:
        return "deep_work"
    if key == "repair" and score >= 0.55:
        return "repair"
    if key == "emotional" and score >= 0.55:
        return "emotional_presence"
    if key == "humor" and score >= 0.55:
        return "playful"
    return "attentive"


def _journal_entries(
    *,
    now: float,
    sections_by_kind: Mapping[str, dict[str, Any]],
    signal_snapshot: Mapping[str, Any],
    tom_text: str,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    time_text = time.strftime("%H:%M:%S", time.localtime(now))
    for kind in ("current", "concern", "analysis", "future", "surface", "settled"):
        section = _dict(sections_by_kind.get(kind))
        items = _list(section.get("items"))
        if not items:
            continue
        entries.append(
            {
                "time": time_text,
                "source": kind,
                "text": _text(_dict(items[0]).get("text"), limit=140),
            }
        )
    if tom_text:
        entries.append({"time": time_text, "source": "tom", "text": _text(tom_text, limit=140)})
    for raw_signal in _list(_dict(signal_snapshot).get("signal_log"))[:4]:
        item = _dict(raw_signal)
        kind = _text(item.get("kind"), limit=32)
        label = _SIGNAL_TEXT.get(kind, kind.replace("_", " "))
        if not label:
            continue
        entries.append(
            {
                "time": _text(item.get("time"), limit=16) or time_text,
                "source": "signal",
                "text": label,
            }
        )
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in entries:
        text = _text(item.get("text"), limit=140)
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        deduped.append({"time": _text(item.get("time"), limit=16), "source": _text(item.get("source"), limit=24), "text": text})
    return deduped[:8]


def _signal_timeline(
    *,
    now: float,
    sections_by_kind: Mapping[str, dict[str, Any]],
    priorities: list[dict[str, Any]],
    affect_card: Mapping[str, Any],
    signal_snapshot: Mapping[str, Any],
    tom_text: str,
) -> list[dict[str, str]]:
    """Timeline ngắn cho khung ``ChieuSau_NhatKyTinHieu``.

    Đây là nhật ký trình diễn, không phải memory dài hạn. Nó chỉ gom dữ liệu đã
    có trong lượt hiện tại/panel_snapshot, đóng dấu giờ rõ ràng, rồi gửi body
    render. Không gọi LLM và không đọc thêm file ngoài snapshot đã cache.
    """
    time_text = time.strftime("%H:%M:%S", time.localtime(now))
    rows: list[dict[str, str]] = []

    def add(kind: str, text: str, *, time_value: str = "") -> None:
        safe_text = _text(text, limit=96)
        if not safe_text:
            return
        key = safe_text.casefold()
        if any(item.get("_key") == key for item in rows):
            return
        rows.append(
            {
                "_key": key,
                "time": _text(time_value, limit=16) or time_text,
                "kind": _text(kind, limit=24),
                "text": safe_text,
            }
        )

    current = _dict(sections_by_kind.get("current"))
    current_items = _list(current.get("items"))
    if current_items:
        item = _dict(current_items[0])
        add("context", _text(item.get("text"), limit=96) or "nhận context mới từ bạn.")
        badge = _text(item.get("badge"), limit=70)
        if badge:
            add("recurrence", badge)
    elif tom_text:
        add("context", "nhận context mới từ bạn.")

    top_priority = _dict(priorities[0] if priorities else {})
    if top_priority:
        key = _text(top_priority.get("key"), limit=32)
        reason = _text(top_priority.get("reason"), limit=82)
        if key:
            add("priority", f"tăng ưu tiên: {key.replace('_', ' ')}.")
        if reason:
            add("topic", reason)

    concern = _dict(sections_by_kind.get("concern"))
    concern_items = _list(concern.get("items"))
    if concern_items:
        item = _dict(concern_items[0])
        add("concern", _text(item.get("text"), limit=96))

    affect_metrics = _dict(affect_card.get("metrics"))
    headline = _text(affect_card.get("headline"), limit=40)
    arousal = _clamp01(affect_metrics.get("arousal"), 0.0)
    if headline:
        add("affect", f"cảm xúc: {_signal_affect_label(headline)}; arousal {arousal:.2f}.")

    surface = _dict(sections_by_kind.get("surface"))
    surface_items = _list(surface.get("items"))
    if surface_items:
        add("surface", _text(_dict(surface_items[0]).get("text"), limit=96))
    else:
        add("surface", "chuẩn bị phản hồi.")

    for raw_signal in _list(_dict(signal_snapshot).get("signal_log"))[:4]:
        item = _dict(raw_signal)
        kind = _text(item.get("kind"), limit=32)
        label = _SIGNAL_TEXT.get(kind, kind.replace("_", " "))
        add("signal", label, time_value=_text(item.get("time"), limit=16))

    return [{k: v for k, v in item.items() if k != "_key"} for item in rows[:8]]


def _signal_affect_label(raw: str) -> str:
    return {
        "calm": "bình tĩnh",
        "steady": "ổn định",
        "light": "nhẹ",
        "energized": "tỉnh và nhanh",
        "tense": "hơi căng",
        "live affect fallback": "đang đọc trực tiếp",
    }.get(_text(raw, limit=40).replace("_", " ").casefold(), _text(raw, limit=40))


def _interiority_feed(
    *,
    sections_by_kind: Mapping[str, dict[str, Any]],
    priorities: list[dict[str, Any]],
    signal_snapshot: Mapping[str, Any],
    tom_text: str,
    now: float,
) -> list[dict[str, str]]:
    """Dựng feed ngắn cho khung ``ChieuSau_DongNoiTam``.

    Body/Godot không nên phải hiểu toàn bộ inner_diary phức tạp. Hàm này gom
    các section lớn thành 4-6 dòng đã có ``kind`` ổn định để body chỉ việc chọn
    icon và render. Đây là lớp trình diễn, không làm Eris nghĩ thêm và không gọi
    model.
    """
    time_text = time.strftime("%H:%M:%S", time.localtime(now))
    rows: list[dict[str, str]] = []

    def add(kind: str, text: str, meta: str = "", *, title: str = "", time_value: str = "") -> None:
        safe_text = _text(text, limit=150)
        if not safe_text:
            return
        key = safe_text.casefold()
        if any(item.get("_key") == key for item in rows):
            return
        rows.append(
            {
                "_key": key,
                "kind": _text(kind, limit=32),
                "title": _text(title, limit=44),
                "text": safe_text,
                "meta": _text(meta, limit=90),
                "time": _text(time_value, limit=16) or time_text,
            }
        )

    # Ưu tiên các section đúng nghĩa "dòng nội tâm": hiện tại, bận tâm, phân
    # tích, dự định nói. Mỗi section chỉ lấy item đầu để panel gọn, không thành log.
    for kind, fallback_title in (
        ("current", "ý nghĩ đang nổi"),
        ("concern", "đang bận tâm"),
        ("analysis", "đang phân tích"),
        ("future", "hướng tiếp theo"),
        ("surface", "muốn nói ra"),
        ("settled", "đã nguội"),
    ):
        section = _dict(sections_by_kind.get(kind))
        items = _list(section.get("items"))
        if not items:
            continue
        item = _dict(items[0])
        title = _text(section.get("title"), limit=44) or fallback_title
        add(kind, _text(item.get("text"), limit=150), _text(item.get("badge"), limit=90), title=title)

    # Nếu private_mind chưa đủ section, lấy ưu tiên đang thắng làm dòng dự phòng.
    for raw_priority in priorities[:3]:
        priority = _dict(raw_priority)
        key = _text(priority.get("key"), limit=32)
        reason = _text(priority.get("reason"), limit=150)
        label = _text(priority.get("label"), limit=44)
        if not reason:
            continue
        add(key or "current", reason, f"ưu tiên: {label}", title=label)

    # ToM và signal chỉ dùng làm fallback cuối. Chúng giúp panel không trống khi
    # inner diary chưa tick kịp, nhưng không lấn át các thought section thật.
    if tom_text:
        add("concern", tom_text, "đọc bạn gần nhất", title="thấu hiểu bạn")
    for raw_signal in _list(_dict(signal_snapshot).get("signal_log"))[:4]:
        item = _dict(raw_signal)
        kind = _text(item.get("kind"), limit=32)
        label = _SIGNAL_TEXT.get(kind, kind.replace("_", " "))
        add("observe", label, "tín hiệu runtime", title="tín hiệu", time_value=_text(item.get("time"), limit=16))

    # Bỏ khóa dedupe nội bộ trước khi đưa sang JSON.
    return [{k: v for k, v in item.items() if k != "_key"} for item in rows[:6]]


def build_depth_v2_state(
    *,
    private_mind_digest: Mapping[str, Any] | None,
    flags: Mapping[str, Any] | None,
    expression_gate: Mapping[str, Any] | None,
    tom_read: str,
    identity: Mapping[str, Any] | None,
    affect_history: list[dict[str, Any]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    ts = float(now if now is not None else time.time())
    sections = _extract_sections(private_mind_digest)
    sections_by_kind = _section_map(sections)
    signal_snapshot = _signal_snapshot()
    affect_card = _emotion_board(private_mind_digest)
    anchor_depth = _anchor_depth_summary(private_mind_digest)
    safety_block = _dict(anchor_depth.get("safety"))
    history = _roll_affect_history(affect_history, affect_card=affect_card, now=ts)
    priorities = _derive_priorities(private_mind_digest, tom_text=tom_read, expression_gate=expression_gate)
    dev = _development_summary()
    gate = _dict(expression_gate)
    identity_block = dict(_dict(identity))
    identity_block.setdefault("name", "ERIS")
    identity_block["last_tick_at"] = round(_float(_dict(private_mind_digest).get("last_tick_at"), 0.0), 3)

    return {
        "version": 2,
        "ts": round(ts, 3),
        "identity": identity_block,
        "status": {
            "headline": _text(affect_card.get("headline"), limit=80) or "listening",
            "subtext": _text(affect_card.get("subtext"), limit=140) or "thinking quietly",
            "focus_mode": _focus_mode(priorities),
        },
        "affect": affect_card,
        "tom": {
            "live": bool(_text(tom_read)),
            "text": _text(tom_read, limit=220),
            "confidence": round(_parse_tom_confidence(tom_read), 3),
        },
        # Contract riêng cho khung "Mô Hình bạn": body đọc trực tiếp block này,
        # không còn phải suy ra độ dày/nghĩa/gắn bó từ một câu tom_read mơ hồ.
        **anchor_depth,
        "autoswitch": _autoswitch_block(
            flags=flags,
            safety=safety_block,
            expression_gate=expression_gate,
            tom_text=tom_read,
        ),
        "expression": {
            "want": round(_clamp01(gate.get("want_to_say"), 0.0), 3),
            "suppress": round(_clamp01(gate.get("suppression_pressure"), 0.0), 3),
            "allowed": round(_clamp01(gate.get("allowed_expression"), 0.0), 3),
            "stance": _text(gate.get("stance"), limit=80),
        },
        "priorities": priorities,
        "interiority": {
            "sections": sections,
            "feed": _interiority_feed(
                sections_by_kind=sections_by_kind,
                priorities=priorities,
                signal_snapshot=signal_snapshot,
                tom_text=tom_read,
                now=ts,
            ),
            "current": _dict(sections_by_kind.get("current")),
            "future": _dict(sections_by_kind.get("future")),
            "concern": _dict(sections_by_kind.get("concern")),
            "analysis": _dict(sections_by_kind.get("analysis")),
            "surface": _dict(sections_by_kind.get("surface")),
            "settled": _dict(sections_by_kind.get("settled")),
        },
        "n2": dev,
        "journal": _journal_entries(now=ts, sections_by_kind=sections_by_kind, signal_snapshot=signal_snapshot, tom_text=tom_read),
        "signal_timeline": _signal_timeline(
            now=ts,
            sections_by_kind=sections_by_kind,
            priorities=priorities,
            affect_card=affect_card,
            signal_snapshot=signal_snapshot,
            tom_text=tom_read,
        ),
        "signal_log": [
            {
                "time": _text(_dict(item).get("time"), limit=16),
                "kind": _text(_dict(item).get("kind"), limit=24),
                "text": _SIGNAL_TEXT.get(_text(_dict(item).get("kind"), limit=24), _text(_dict(item).get("kind"), limit=24).replace("_", " ")),
            }
            for item in _list(signal_snapshot.get("signal_log"))[:8]
            if _dict(item)
        ],
        "context": {
            "tags": _context_tags(ts),
            "focus_mode": _focus_mode(priorities),
        },
        "flags": dict(_dict(flags)),
        "affect_history": history,
    }

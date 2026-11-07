# -*- coding: utf-8 -*-
"""Helper nhỏ cho worker subconscious.

Các worker Phase 1/2 phải đọc được cả object thật, SimpleNamespace trong test,
và dict debug từ runtime. Nếu mỗi file tự viết getattr/dict-get một kiểu thì repo sẽ
sớm có một bảo tàng lỗi nhỏ. File này gom các thao tác đọc an toàn lại một chỗ.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from ..thought_fragment import clean_text, clamp01, normalize_string_list


TECHNICAL_TERMS = (
    "phase",
    "repo",
    "test",
    "pytest",
    "code",
    "bug",
    "fix",
    "vá",
    "sửa",
    "file",
    "runtime",
    "pipeline",
    "worker",
    "agent",
    "surface",
    "arbiter",
    "memory",
    "blackboard",
    "synthesizer",
)

RELATIONAL_TERMS = (
    "lạnh",
    "xa",
    "im",
    "bám",
    "quan tâm",
    "nhớ",
    "thương",
    "ghét",
    "giận",
    "mệt",
)

CURIOSITY_TERMS = (
    "tại sao",
    "vì sao",
    "sao",
    "cái gì",
    "như nào",
    "làm sao",
    "đánh giá",
    "phân tích",
    "research",
    "kiểm tra",
)


def safe_get(obj: Any, path: str, default: Any = None) -> Any:
    """Đọc obj theo path dạng ``a.b.c``.

    Hỗ trợ dict, object có attribute, và object có method get. Không raise khi
    thiếu field. Worker nền mà làm chết tick vì thiếu attr thì hơi mất mặt.
    """

    current = obj
    for part in str(path or "").split("."):
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(part, default)
            continue
        if hasattr(current, part):
            current = getattr(current, part)
            continue
        getter = getattr(current, "get", None)
        if callable(getter):
            try:
                current = getter(part, default)
                continue
            except Exception:
                return default
        return default
    return current if current is not None else default


def first_present(*values: Any, default: Any = None) -> Any:
    """Trả giá trị đầu tiên khác None/chuỗi rỗng."""

    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return default


def collect_texts(value: Any, *, limit: int = 8) -> tuple[str, ...]:
    """Rút text ngắn từ nhiều kiểu container runtime.

    Dùng cho evidence/memory/recent_events. Hàm này không hiểu nghĩa sâu, chỉ lấy
    chuỗi có thể đọc được. Nghĩa sâu để worker xử, không để helper tự phong thần.
    """

    texts: list[str] = []

    def add(item: Any) -> None:
        if len(texts) >= limit:
            return
        if item is None:
            return
        if isinstance(item, str):
            text = clean_text(item, limit=220)
        elif isinstance(item, dict):
            text = clean_text(
                first_present(
                    item.get("claim"),
                    item.get("content"),
                    item.get("text"),
                    item.get("summary"),
                    item.get("description"),
                    default="",
                ),
                limit=220,
            )
        else:
            text = clean_text(
                first_present(
                    getattr(item, "claim", None),
                    getattr(item, "content", None),
                    getattr(item, "text", None),
                    getattr(item, "summary", None),
                    getattr(item, "description", None),
                    default=str(item),
                ),
                limit=220,
            )
        if text and text not in texts:
            texts.append(text)

    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        add(value.decode("utf-8", "ignore") if isinstance(value, bytes) else value)
        return tuple(texts)
    try:
        iterator = iter(value)
    except TypeError:
        add(value)
        return tuple(texts)
    for item in iterator:
        add(item)
        if len(texts) >= limit:
            break
    return tuple(texts)


def collect_ids(value: Any, *, limit: int = 12, prefix: str = "ctx") -> tuple[str, ...]:
    """Rút evidence id từ object/dict/list; fallback thành id giả ổn định."""

    ids: list[str] = []

    def add(raw: Any, idx: int) -> None:
        if raw is None:
            return
        if isinstance(raw, str):
            text = raw
        elif isinstance(raw, dict):
            text = first_present(raw.get("id"), raw.get("source_signal_id"), raw.get("evidence_id"), default="")
        else:
            text = first_present(getattr(raw, "id", None), getattr(raw, "source_signal_id", None), default="")
        text = clean_text(text, limit=140)
        if not text:
            text = f"{prefix}.{idx}"
        if text not in ids:
            ids.append(text)

    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        add(value.decode("utf-8", "ignore") if isinstance(value, bytes) else value, 0)
        return normalize_string_list(ids, limit=limit)
    try:
        iterable = tuple(value)
    except TypeError:
        iterable = (value,)
    for idx, item in enumerate(iterable[:limit]):
        add(item, idx)
    return normalize_string_list(ids, limit=limit)


def user_text_from_context(context: Any) -> str:
    """Cố gắng lấy user text hiện tại từ frame/snapshot.

    Không có contract thống nhất tuyệt đối trong repo cũ, nên helper đọc nhiều
    alias phổ biến. Không có thì trả chuỗi rỗng, worker sẽ im. Ít nhất nó im vì
    thiếu dữ kiện chứ không bịa. Một chiến thắng nhỏ trước entropy.
    """

    frame = safe_get(context, "frame")
    snapshot = safe_get(context, "snapshot")
    value = first_present(
        safe_get(frame, "user_text"),
        safe_get(frame, "input_text"),
        safe_get(frame, "text"),
        safe_get(frame, "current_user_text"),
        safe_get(frame, "disturbance.content"),
        safe_get(frame, "disturbance.text"),
        safe_get(snapshot, "user_text"),
        safe_get(snapshot, "last_user_text"),
        safe_get(snapshot, "chat_text"),
        default="",
    )
    return clean_text(value, limit=700)


def context_evidence_ids(context: Any, *, fallback_prefix: str = "subconscious") -> tuple[str, ...]:
    """Lấy evidence ids từ frame/snapshot hoặc tạo id giả từ user text."""

    frame = safe_get(context, "frame")
    snapshot = safe_get(context, "snapshot")
    evidence = first_present(
        safe_get(frame, "evidence"),
        safe_get(frame, "selected_evidence"),
        safe_get(frame, "workspace.selected_evidence"),
        safe_get(snapshot, "evidence"),
        default=None,
    )
    ids = collect_ids(evidence, prefix="ev")
    if ids:
        return ids
    text = user_text_from_context(context)
    if text:
        seed = re.sub(r"[^\w]+", "_", text.lower()).strip("_")[:48] or "user_text"
        return (f"{fallback_prefix}.{seed}",)
    return ()


def memory_texts_from_context(context: Any, *, limit: int = 8) -> tuple[str, ...]:
    """Rút các mảnh memory có thể đọc từ memory_packet/frame."""

    memory_packet = safe_get(context, "memory_packet")
    frame = safe_get(context, "frame")
    parts: list[str] = []
    for path in (
        "episodic",
        "semantic",
        "relational",
        "affective_residue",
        "procedural",
        "worldview",
        "open_questions",
    ):
        for text in collect_texts(safe_get(memory_packet, path), limit=limit):
            if text not in parts:
                parts.append(text)
            if len(parts) >= limit:
                return tuple(parts)
    for path in ("memory_packet.episodic", "memory_packet.semantic", "memory_packet.relational"):
        for text in collect_texts(safe_get(frame, path), limit=limit):
            if text not in parts:
                parts.append(text)
            if len(parts) >= limit:
                return tuple(parts)
    return tuple(parts[:limit])


def relation_level(context: Any, field: str, default: float = 0.0) -> float:
    """Đọc field số từ relation/internal_state/frame và clamp [0,1]."""

    relation = safe_get(context, "relation")
    frame = safe_get(context, "frame")
    internal_state = safe_get(context, "internal_state")
    return clamp01(
        first_present(
            safe_get(relation, field),
            safe_get(frame, f"relation.{field}"),
            safe_get(internal_state, field),
            safe_get(frame, f"internal_state.{field}"),
            default=default,
        ),
        default=default,
    )


def contains_any(text: str, terms: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def score_terms(text: str, terms: Sequence[str]) -> float:
    lowered = text.lower()
    hits = sum(1 for term in terms if term.lower() in lowered)
    return clamp01(hits / max(3, min(len(terms), 8)))


def kind_of_request(text: str) -> str:
    """Phân loại rất nhẹ ý định user, đủ làm fragment seed."""

    lowered = text.lower()
    if not lowered:
        return "ambient_observation"
    if any(term in lowered for term in ("làm", "vá", "sửa", "fix", "gửi", "chạy test", "copy")):
        return "implementation_request"
    if any(term in lowered for term in ("tại sao", "vì sao", "sao", "phân tích", "đánh giá", "chẩn")):
        return "diagnostic_analysis_request"
    if "?" in text:
        return "clarification_or_question"
    return "continuation_request"

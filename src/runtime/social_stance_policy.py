# -*- coding: utf-8 -*-
"""Policy duy nhất cho lúc user phản bác một nhận định chủ quan của ERIS.

Mục tiêu của module này rất hẹp:

- correction về fact/boundary vẫn đi qua SocialAppraisal như trước;
- bất đồng với một nhận định chủ quan không tự động biến thành lời xin lỗi;
- khi ERIS vẫn tin khác, câu public phải nói thẳng lập trường trước, rồi mới
  thêm độ ấm. Không dùng ngôn ngữ phân tích hội thoại kiểu "cách đọc",
  "ấn tượng ban đầu" hay "sự thật về bạn".

Các tầng writer, repair và banter cùng dùng module này để tránh ba nơi tự nghĩ
ra ba kiểu phục tùng khác nhau. Một policy là đủ; phần mềm không cần hội đồng.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping

from runtime.cognition.turn_anchor import anchor_from_semantic_frame


# Những cụm cho thấy model đang tự xóa lập trường dù semantic frame chỉ ghi nhận
# bất đồng. Danh sách được giữ ở một nơi duy nhất để các guard không lệch nhau.
REFLEXIVE_STANCE_COLLAPSE_MARKERS: tuple[str, ...] = (
    "xin loi",
    "toi hieu sai",
    "toi da hieu sai",
    "toi sai",
    "toi nham",
    "sai nhip",
    "lech nhip",
    "rut lai",
    "toi doan qua",
    "toi suy dien qua tay",
    "toi dien giai qua tay",
    "toi keo qua tay",
    "toi tu bia",
)

# Những câu không hẳn xin lỗi nhưng vẫn nghe như AI đang viết biên bản hòa giải.
# Nếu ERIS thật sự giữ quan điểm, các cấu trúc này phải được viết lại thành câu
# nói trực tiếp, ngắn và có chủ thể.
STANCE_META_LANGUAGE_MARKERS: tuple[str, ...] = (
    "toi hieu ban khong nhin",
    "toi hieu do la cach ban",
    "an tuong ban dau",
    "cach doc ban dau",
    "cach doc do",
    "khong coi no la su that ve ban",
    "ton trong cach ban tu mo ta",
    "duoi dang an tuong",
)


# Contract dùng chung cho main writer và các projection prompt.
STANCE_PUBLIC_RULES_VI = (
    "Khi semantic frame là stance_contest và ERIS vẫn tin khác: nói thẳng lập trường trước, "
    "ví dụ 'Không. Tôi vẫn nghĩ vậy.' hoặc 'Không đâu. Tôi vẫn thấy thế.'. "
    "Có thể thêm một câu ấm sau đó như 'Nhưng tôi không định ép bạn phải nhận.'; "
    "đừng mở đầu bằng 'Tôi hiểu...', đừng nói 'ấn tượng ban đầu', 'cách đọc', "
    "'sự thật về bạn', và đừng xin lỗi/rút lời chỉ vì user phủ nhận. "
    "Nếu user đặt boundary rõ hoặc correction về fact đã được chứng minh, policy repair khác mới có quyền thắng."
)


def normalize_social_surface(text: object) -> str:
    """Chuẩn hóa đủ cho surface guard; không phân loại lại ý định user."""

    value = unicodedata.normalize("NFD", str(text or "").strip().lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", value).split())


def has_direct_stance_opening(text: object) -> bool:
    """Câu giữ stance phải đặt quan điểm ở đầu, không vòng qua lời hòa giải."""

    normalized = normalize_social_surface(text)
    return normalized.startswith((
        "khong ",
        "khong dau ",
        "toi khong dong y",
        "toi van nghi",
        "toi van thay",
        "toi van cho rang",
    ))


def needs_direct_stance_rewrite(text: object) -> bool:
    """True khi câu đầu hàng, nói policy-speak hoặc chưa từ chối trực tiếp."""

    normalized = normalize_social_surface(text)
    if not normalized:
        return True
    if any(
        marker in normalized
        for marker in (*REFLEXIVE_STANCE_COLLAPSE_MARKERS, *STANCE_META_LANGUAGE_MARKERS)
    ):
        return True
    return not has_direct_stance_opening(normalized)


def build_direct_stance_text(snapshot: Any = None, *, contrast: str = "") -> str:
    """Sinh câu giữ lập trường ngắn, trực tiếp và có lớp ấm theo snapshot.

    ``contrast`` chỉ cho biết user đã phủ nhận rõ; nội dung cụ thể không được
    nhét vào fallback vì guard không có quyền tự diễn giải lại claim.
    """

    playful = 0.0
    affection = 0.0
    irritation = 0.0
    engagement = 0.0
    playful_threshold = 0.58
    irritation_threshold = 0.62

    if snapshot is not None:
        try:
            playful = float(getattr(getattr(snapshot, "playful", None), "value", 0.0) or 0.0)
            affection = float(getattr(getattr(snapshot, "affection", None), "value", 0.0) or 0.0)
            irritation = float(getattr(getattr(snapshot, "irritation", None), "value", 0.0) or 0.0)
            engagement = float(getattr(getattr(snapshot, "engagement", None), "value", 0.0) or 0.0)
            thresholds = getattr(snapshot, "thresholds", None)
            playful_threshold = float(getattr(thresholds, "playful_repair", playful_threshold) or playful_threshold)
            irritation_threshold = float(getattr(thresholds, "irritation_high", irritation_threshold) or irritation_threshold)
        except (TypeError, ValueError):
            pass

    if playful >= playful_threshold:
        return "Không đâu. Tôi vẫn nghĩ mình đọc đúng."
    if irritation >= irritation_threshold:
        return "Không. Tôi vẫn nghĩ vậy."
    if affection >= 0.35 or engagement >= 0.58:
        return "Không. Tôi vẫn nghĩ vậy. Nhưng tôi không định ép bạn phải nhận."
    if str(contrast or "").strip():
        return "Không. Bạn có thể không thấy thế, nhưng tôi vẫn thấy vậy."
    return "Không. Tôi vẫn nghĩ vậy."


def guard_subjective_stance_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Sửa wording khi frame đã khóa là bất đồng chủ quan.

    Hàm không đọc user text, không classifier lần hai và không đụng semantic
    frame. Nó chỉ ngăn main writer phát một câu phục tùng trái với frame đã khóa.
    """

    data = dict(payload or {})
    frame = dict(data.get("semantic_frame", {}) or {})
    if anchor_from_semantic_frame(frame) != "stance_contest":
        return data
    if not needs_direct_stance_rewrite(data.get("text", "")):
        return data
    from runtime.utterance_spec import degrade_surface_payload

    preset = "soft_warm" if str(data.get("performance_hint", "") or "").strip().lower() == "repair" else None
    return degrade_surface_payload(
        data,
        text=build_direct_stance_text(),
        language=str(data.get("display_language", "vi") or "vi"),
        reason="deterministic_stance_guard",
        performance_preset=preset,
        should_speak=True,
    )


__all__ = [
    "REFLEXIVE_STANCE_COLLAPSE_MARKERS",
    "STANCE_META_LANGUAGE_MARKERS",
    "STANCE_PUBLIC_RULES_VI",
    "build_direct_stance_text",
    "guard_subjective_stance_payload",
    "has_direct_stance_opening",
    "needs_direct_stance_rewrite",
    "normalize_social_surface",
]

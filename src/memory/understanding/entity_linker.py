from __future__ import annotations

"""
memory.understanding.entity_linker
==================================

Entity linker rất mỏng cho memory.

Không cố làm NER thần thánh. Nhiệm vụ của nó là chuẩn hóa object mà parser/model
đưa ra: "Hà Tĩnh" thành entity location có canonical ổn định, "Tín" thành
person_name, v.v. Nếu không chắc, nó trả confidence thấp để downstream tự hạ về
candidate hoặc reject.
"""

from typing import Any, Mapping
import re
import unicodedata

from .meaning_frame import EntityRef
from ..name_validation import is_plausible_user_name


KNOWN_LOCATION_CANON = {
    "ha tinh": "Hà Tĩnh",
    "ha noi": "Hà Nội",
    "sai gon": "Sài Gòn",
    "ho chi minh": "TP.HCM",
    "da nang": "Đà Nẵng",
    "nghe an": "Nghệ An",
    "hue": "Huế",
    "can tho": "Cần Thơ",
    "da lat": "Đà Lạt",
    "viet nam": "Việt Nam",
}

LOCATION_CLASS_WORDS = {"tinh", "thanh pho", "tp", "quan", "huyen", "xa", "phuong", "tỉnh", "thành phố", "quận", "huyện", "xã", "phường"}


def link_entity(value: Any, *, hinted_kind: str = "unknown", metadata: Mapping[str, Any] | None = None) -> EntityRef:
    """Link object surface thành EntityRef.

    `hinted_kind` đến từ model parser hoặc frame relation. Nếu hint rõ, linker chỉ
    validate nhẹ. Nếu hint unknown, linker cố đoán rất dè dặt.
    """

    text = clean_surface(value)
    norm = normalize(text)
    if not text:
        return EntityRef(text="", kind=hinted_kind or "unknown", confidence=0.0)

    kind = hinted_kind or "unknown"
    confidence = 0.50
    canonical = norm
    meta = dict(metadata or {})

    if kind == "location" or _looks_like_location(norm):
        kind = "location"
        canonical = norm
        confidence = 0.82 if norm in KNOWN_LOCATION_CANON else 0.62
        meta.setdefault("display", KNOWN_LOCATION_CANON.get(norm, text))
    elif kind == "person_name":
        confidence = 0.78 if _looks_like_person_name(text) else 0.50
        canonical = norm
    elif kind in {"month", "year", "age"}:
        confidence = 0.84
        canonical = norm
    elif kind == "style_rule":
        confidence = 0.70
        canonical = norm[:160]
    elif kind == "unknown":
        confidence = 0.45

    return EntityRef(text=text, kind=kind, canonical=canonical, confidence=confidence, metadata=meta)


def clean_surface(value: Any, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").strip().split())
    text = text.strip(" .,:;!?\"'“”‘’`")
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def normalize(text: str) -> str:
    lowered = strip_accents(str(text or "").lower()).replace("đ", "d")
    lowered = re.sub(r"[^0-9a-zA-Z_\s-]+", " ", lowered)
    return " ".join(lowered.split())


def strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _looks_like_location(norm: str) -> bool:
    if norm in KNOWN_LOCATION_CANON:
        return True
    words = set(norm.split())
    if words & LOCATION_CLASS_WORDS:
        return True
    return False


def _looks_like_person_name(text: str) -> bool:
    return is_plausible_user_name(text)


__all__ = ["link_entity", "clean_surface", "normalize", "strip_accents"]

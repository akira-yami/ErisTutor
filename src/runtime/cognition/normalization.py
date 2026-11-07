# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from typing import Any


def compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def normalize_vi(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    text = re.sub(r"[^a-z0-9_#+.\-\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(value: Any) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9_#+.\-]+", normalize_vi(value)))


def token_overlap(needle: Any, haystack: Any) -> float:
    a = {item for item in tokens(needle) if len(item) >= 2}
    b = set(tokens(haystack))
    if not a:
        return 0.0
    return len(a & b) / len(a)


def contains_phrase(normalized: str, *phrases: str) -> bool:
    padded = f" {normalized} "
    return any(f" {normalize_vi(item)} " in padded for item in phrases if normalize_vi(item))


def starts_with_phrase(normalized: str, *phrases: str) -> bool:
    return any(normalized == normalize_vi(item) or normalized.startswith(normalize_vi(item) + " ") for item in phrases)

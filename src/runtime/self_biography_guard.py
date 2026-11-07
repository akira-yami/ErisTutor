# -*- coding: utf-8 -*-
"""Backstop cho ký ức hội thoại giả và tự truyện thân thể giả.

Typed claim domains là authority chính. Module này chỉ chặn hai kiểu wording có
rủi ro cao khi model cố lách/mislabel manifest:

1) user_probe dựng tiền đề "bạn từng kể..." dù intent không có provenance lịch sử;
2) ERIS tự nhận một thói quen/hành động thân thể ("tôi hay uống...") dù không có
   episodic self-history evidence.

Sở thích/quan điểm ("tôi thích vị đắng") không bị coi là lịch sử thân thể.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Sequence

from runtime.claim_domain_contract import normalize_claim_manifest


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text or "").casefold())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


# Narrow, high-confidence physical/embodied action verbs. Deliberately exclude
# generic "đi" because Vietnamese uses it idiomatically ("đi thẳng vào vấn đề").
_PHYSICAL_VERBS = (
    "uong|an|ngu|tam|nau|pha|hut|ngui|nem|chay|tap|boi|lai|mac|cam|cham|"
    "nhai|nuot|tho|ho|hat hoi|di bo"
)
_SELF = r"(?:toi|em|minh)"
_FREQ = r"(?:hay|thuong|thuong xuyen|van hay|van thuong|moi ngay|hang ngay)"
_PAST = r"(?:da|tung|vua|moi)"

_EMBODIED_HISTORY_PATTERNS = (
    re.compile(rf"\b{_SELF}\s+(?:thi\s+)?{_FREQ}\s+(?:{_PHYSICAL_VERBS})\b"),
    re.compile(rf"\b{_SELF}\s+{_PAST}\s+(?:{_PHYSICAL_VERBS})\b"),
    re.compile(rf"\b(?:hom qua|hom nay|sang nay|toi qua)\s+{_SELF}\s+(?:da\s+)?(?:{_PHYSICAL_VERBS})\b"),
)

# Only used for user_probe backstop. Typed domains should normally catch this
# first; this protects against a model falsely labeling the premise as opinion.
_FAKE_MEMORY_BRIDGE_PATTERNS = (
    re.compile(r"\b(?:luc truoc|truoc day|lan truoc)\s+(?:ban|anh|em)\s+(?:co\s+)?(?:ke|noi|bao|nhac)\b"),
    re.compile(r"\b(?:ban|anh|em)\s+(?:da\s+)?tung\s+(?:ke|noi|bao|nhac)\b"),
    re.compile(r"\b(?:nho|toi nho)\s+(?:la\s+)?(?:ban|anh|em)\s+(?:tung|da)\b"),
)


def _has_valid_history_ref(
    manifest: Any,
    allowed_evidence_refs: Sequence[str],
    *,
    domains: frozenset[str],
) -> bool:
    allowed = {str(ref or "").strip() for ref in allowed_evidence_refs if str(ref or "").strip()}
    if not allowed:
        return False
    for item in normalize_claim_manifest(manifest):
        if str(item.get("domain", "") or "") not in domains:
            continue
        refs = {str(ref or "").strip() for ref in item.get("evidence_refs", ()) if str(ref or "").strip()}
        if refs and refs.issubset(allowed):
            return True
    return False


def user_probe_fake_memory_premise(
    text: str,
    *,
    claim_manifest: Any = None,
    allowed_evidence_refs: Sequence[str] = (),
) -> bool:
    folded = _fold(text)
    if not folded or not any(pattern.search(folded) for pattern in _FAKE_MEMORY_BRIDGE_PATTERNS):
        return False
    return not _has_valid_history_ref(
        claim_manifest,
        allowed_evidence_refs,
        domains=frozenset({"conversation_history", "shared_episode", "memory"}),
    )


def embodied_self_history_without_evidence(
    text: str,
    *,
    claim_manifest: Any = None,
    allowed_evidence_refs: Sequence[str] = (),
) -> bool:
    folded = _fold(text)
    if not folded or not any(pattern.search(folded) for pattern in _EMBODIED_HISTORY_PATTERNS):
        return False
    return not _has_valid_history_ref(
        claim_manifest,
        allowed_evidence_refs,
        domains=frozenset({"self_history", "memory"}),
    )


__all__ = [
    "embodied_self_history_without_evidence",
    "user_probe_fake_memory_premise",
]

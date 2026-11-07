# -*- coding: utf-8 -*-
"""Nguồn của một claim xuyên suốt ingest -> commit -> reload.

Metadata do *runtime ingress* cấp; chuỗi do model viết KHÔNG được dùng để
thay origin, roots hoặc quyền xác nhận. Không tạo memory store thứ hai.
"""
from __future__ import annotations

from typing import Any, Mapping

SCHEMA = "eris.claim_provenance.v1"
FACTUAL_TARGETS = frozenset({"semantic_memory", "belief_core", "worldview_memory", "sociocultural_memory", "spatial_memory"})
SOURCE_KINDS = frozenset({"user_statement", "assistant_utterance", "observer_summary", "runtime_state", "fiction", "unknown"})


def create_provenance(origin_kind: str, *, root_event_ids: tuple[str, ...] | list[str] = (),
                      subject: str = "unknown", scope: str = "unknown",
                      parent_claim_refs: tuple[str, ...] | list[str] = (),
                      assertion: str = "unknown", corrects_claim_ref: str = "",
                      promotion_basis: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
    """Tạo bản ghi JSON-safe; refs rỗng không phải chứng cứ.

    Không có root thì nguồn user cũng KHÔNG đủ điều kiện củng cố fact.
    """
    kind = str(origin_kind or "unknown").strip()
    if kind not in SOURCE_KINDS:
        kind = "unknown"
    def refs(values: tuple[str, ...] | list[str]) -> list[str]:
        return list(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))
    return {
        "schema": SCHEMA, "origin_kind": kind,
        "root_event_ids": refs(root_event_ids), "parent_claim_refs": refs(parent_claim_refs),
        "subject": str(subject or "unknown"), "scope": str(scope or "unknown"),
        "assertion": str(assertion or "unknown"),
        "corrects_claim_ref": str(corrects_claim_ref or ""),
        "promotion_basis": refs(promotion_basis),
    }


def classify_ingress(source: str, *, turn_id: str = "", role: str = "",
                     parent_event_ids: tuple[str, ...] = ()) -> dict[str, Any]:
    """Phân loại TỪ channel runtime, không đoán nguồn qua text.

    Observer nhận root của lượt gốc chỉ làm lineage, không được nâng thành
    user_statement: nguồn chứng cứ vẫn là observer_summary.
    """
    normalized = str(source or "").strip().lower()
    role = str(role or "").strip().lower()
    if normalized in {"chat_turn", "user_turn", "user_text", "user_panel"} and role in {"", "user"}:
        return create_provenance("user_statement", root_event_ids=[turn_id] if turn_id else [],
                                 subject="user", scope="conversation", assertion="direct")
    if normalized in {"ai_post_turn_observer", "post_turn_observer", "observer_summary"} or role == "observer":
        return create_provenance("observer_summary", root_event_ids=list(parent_event_ids or ((turn_id,) if turn_id else ())),
                                 subject="unknown", scope="derived", assertion="inferred")
    if role == "assistant" or normalized in {"assistant_utterance", "public_commit"}:
        return create_provenance("assistant_utterance", root_event_ids=[turn_id] if turn_id else [],
                                 subject="assistant", scope="utterance", assertion="utterance")
    if normalized == "fiction" or role == "fiction":
        return create_provenance("fiction", root_event_ids=[turn_id] if turn_id else [], scope="fiction")
    return create_provenance("unknown", root_event_ids=[turn_id] if turn_id else [])


def provenance_of(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Legacy không có bản ghi typed được coi là unknown, KHÔNG giả user."""
    raw = (metadata or {}).get("claim_provenance")
    if not isinstance(raw, Mapping) or raw.get("schema") != SCHEMA:
        return create_provenance("unknown")
    # Normalize để chuỗi giả danh/list kiểu lạ không nâng quyền được.
    return create_provenance(
        str(raw.get("origin_kind") or "unknown"),
        root_event_ids=raw.get("root_event_ids") if isinstance(raw.get("root_event_ids"), (list, tuple)) else [],
        parent_claim_refs=raw.get("parent_claim_refs") if isinstance(raw.get("parent_claim_refs"), (list, tuple)) else [],
        subject=str(raw.get("subject") or "unknown"), scope=str(raw.get("scope") or "unknown"),
        assertion=str(raw.get("assertion") or "unknown"),
        corrects_claim_ref=str(raw.get("corrects_claim_ref") or ""),
        promotion_basis=raw.get("promotion_basis") if isinstance(raw.get("promotion_basis"), (list, tuple)) else [],
    )


def commit_provenance_check(target: str, metadata: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Chặn factual promotion ở durable owner, không chỉ ở retrieval.

    Những request cũ không có claim_provenance vẫn đi qua policy cũ để giữ
    compatibility, nhưng không được khai là 'đã chứng minh nguồn' hay reinforce
    record typed mới (chốt ở SemanticMemoryStore). Các lane được gắn provenance
    rõ ràng mà thiếu root hoặc không phải user thì fail-closed.
    """
    if target not in FACTUAL_TARGETS:
        return True, "not_factual_target"
    metadata = metadata or {}
    if "claim_provenance" not in metadata:
        # Legacy compatibility path; không cho phép gắn ngầm origin=user.
        if str(metadata.get("memory_kernel_source") or "").lower() in {"ai_post_turn_observer", "observer_summary"}:
            return False, "observer_cannot_promote_to_fact"
        return True, "legacy_unverified"
    proven = provenance_of(metadata)
    kernel_source = str(metadata.get("memory_kernel_source") or "").strip()
    if kernel_source:
        kernel_context = metadata.get("memory_kernel_context")
        kernel_role = str(kernel_context.get("background_role") or "") if isinstance(kernel_context, Mapping) else ""
        trusted_kind = classify_ingress(kernel_source, role=kernel_role)["origin_kind"]
        if trusted_kind != proven["origin_kind"]:
            return False, "ingress_origin_mismatch"
    if proven["origin_kind"] != "user_statement":
        return False, f"untrusted_fact_origin:{proven['origin_kind']}"
    if proven["subject"] != "user" or proven["scope"] not in {"conversation", "global", "project", "session"}:
        return False, "untrusted_fact_scope"
    if not proven["root_event_ids"]:
        return False, "factual_origin_without_root"
    return True, "typed_user_source"


def source_roots(metadata: Mapping[str, Any] | None) -> frozenset[str]:
    return frozenset(provenance_of(metadata).get("root_event_ids") or ())

# -*- coding: utf-8 -*-
"""Trích xuất patch profile từ semantic claim và tín hiệu hội thoại.

Extractor có hai nhánh:
- Nhánh deterministic cho dữ kiện đã được memory semantic parser xác nhận.
- Nhánh model adapter cho diễn giải hội thoại; không tự học bằng cue từ khóa.

Model chỉ đề xuất ``ProfileOperation``. Store mới là nơi quyết định mutation có
được chấp nhận hay không. Nhờ vậy một model hưng phấn không thể tự viết tiểu sử
người dùng như biên kịch phim dài tập.
"""
from __future__ import annotations
from core.di import resolve_dependency

import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol


from .action_memory import ActionMemoryOperation
from .models import (
    ProfileOperation,
    UserMemoryScope,
    clamp01,
    compact_text,
    determine_memory_tier,
    normalize_path,
)


class UserProfileModelAdapter(Protocol):
    """Contract nhỏ cho model tổng hợp profile.

    Adapter runtime có thể dùng API bất kỳ, miễn trả mapping JSON. Package này
    không phụ thuộc trực tiếp DeepSeek/Gemini/OpenAI nên test được độc lập.
    """

    def propose_profile_operations(self, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        ...

    def summarize_episode(self, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        ...


@dataclass(slots=True)
class ProfileExtractionConfig:
    min_model_confidence: float = 0.62
    min_inferred_confidence: float = 0.68
    max_operations_per_turn: int = 8


class UserProfileExtractor:
    def __init__(self, *, model_adapter: UserProfileModelAdapter | None = None, config: ProfileExtractionConfig | None = None) -> None:
        self.model_adapter = model_adapter
        self.config = resolve_dependency(config, lambda: ProfileExtractionConfig())

    def from_semantic_items(
        self,
        items: Iterable[Any],
        *,
        scope: UserMemoryScope | None = None,
    ) -> tuple[ProfileOperation, ...]:
        """Chuyển semantic memory item thành profile patch tổng quát.

        Không liệt kê ``user.name``, ``user.gender``... Predicate nào bắt đầu bằng
        ``user.`` đều có thể trở thành profile path. Đây mới là open schema thật.
        """
        out: list[ProfileOperation] = []
        for item in items:
            metadata = dict(getattr(item, "metadata", {}) or {})
            fact_key = normalize_path(getattr(item, "fact_key", "") or metadata.get("fact_key") or "")
            if not fact_key.startswith("user."):
                continue
            value = getattr(item, "semantic_value", "") or metadata.get("value")
            if value in (None, ""):
                continue
            status = str(getattr(item, "status", "active") or "active")
            if status not in {"active", "tentative"}:
                continue
            evidence_ids = tuple(getattr(item, "evidence_ids", ()) or ())
            if not evidence_ids:
                continue
            confidence = clamp01(getattr(item, "confidence", 0.0), 0.5)
            cardinality = str(metadata.get("cardinality") or "single")
            explicitness = str(metadata.get("explicitness") or "explicit")
            sensitivity = str(metadata.get("sensitivity") or _sensitivity_for_path(fact_key))
            out.append(ProfileOperation(
                op="reinforce",
                path=fact_key,
                value=value,
                confidence=confidence,
                explicitness=explicitness if explicitness in {"explicit", "observed", "inferred"} else "explicit",  # type: ignore[arg-type]
                sensitivity=sensitivity if sensitivity in {"normal", "personal", "private", "sensitive"} else "normal",  # type: ignore[arg-type]
                scope=scope or _scope_from_metadata(metadata),
                cardinality="multi" if cardinality == "multi" else "single",
                evidence_ids=evidence_ids,
                reason="Đồng bộ từ semantic claim đã được commit.",
                source="semantic_memory_sync",
                metadata={"semantic_item_id": str(getattr(item, "id", "") or "")},
            ))
        return _clean_operations(out)

    def from_turn(
        self,
        user_text: str,
        *,
        evidence_ids: Iterable[str],
        context: Mapping[str, Any] | None = None,
        existing_profile: Mapping[str, Any] | None = None,
    ) -> tuple[ProfileOperation | ActionMemoryOperation, ...]:
        """Trích xuất patch từ một lượt user.

        Chỉ model quyết định nội dung đáng nhớ. Không có keyword fallback tự
        sinh profile hoặc goal/commitment khi model không trả được kết quả.
        """
        raw = str(user_text or "").strip()
        ids = tuple(dict.fromkeys(str(x) for x in evidence_ids if str(x).strip()))
        if not raw or not ids:
            return ()
        context = dict(context or {})
        scope = _scope_from_context(context)
        ops: list[ProfileOperation] = []
        action_ops: list[ActionMemoryOperation] = []

        adapter = self.model_adapter
        if adapter is not None:
            payload = {
                "user_text": raw,
                "evidence_ids": list(ids),
                "scope": scope.to_dict(),
                "context": context,
                "existing_profile": existing_profile or {},
                "contract": {
                    "operations": ["add", "reinforce", "update", "weaken", "supersede", "merge", "archive", "delete"],
                    "explicitness": ["explicit", "observed", "inferred"],
                    "rule": "Không suy diễn thuộc tính nhạy cảm; mọi operation phải có evidence.",
                },
            }
            try:
                response = adapter.propose_profile_operations(payload)
            except Exception as exc:
                from .runtime_adapter import CuratorInferenceError
                if isinstance(exc, CuratorInferenceError):
                    raise  # Không xóa dấu lỗi; envelope phải được retry.
                raise CuratorInferenceError("provider_error") from exc
            if not isinstance(response, Mapping) or not isinstance(response.get("operations"), list):
                from .runtime_adapter import CuratorInferenceError
                raise CuratorInferenceError("parse_error")
            if str(context.get("role") or "user") == "user":
                ops.extend(self._parse_model_operations(response, ids=ids, default_scope=scope))
            if isinstance(response, Mapping):
                rows = response.get("action_operations", [])
                if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
                    from .runtime_adapter import CuratorInferenceError
                    raise CuratorInferenceError("action_parse_error")
                supplied = context.get("existing_actions") or []
                allowed = tuple(str(row["id"]) for row in supplied if isinstance(row, Mapping) and row.get("id"))
                action_scope = UserMemoryScope.from_value(context.get("action_scope") or scope.to_dict())
                for row in rows[: self.config.max_operations_per_turn]:
                    action_ops.append(ActionMemoryOperation(
                        proposal=dict(row), evidence_ids=ids, scope=action_scope,
                        role=str(context.get("role") or "user"), allowed_record_ids=allowed,
                        source_created_at=float(context.get("evidence_created_at") or time.time()),
                    ))
        return (*_clean_operations(ops)[: self.config.max_operations_per_turn], *action_ops)

    def _parse_model_operations(
        self,
        response: Mapping[str, Any] | None,
        *,
        ids: tuple[str, ...],
        default_scope: UserMemoryScope,
    ) -> list[ProfileOperation]:
        if not isinstance(response, Mapping):
            return []
        rows = response.get("operations")
        if not isinstance(rows, list):
            return []
        out: list[ProfileOperation] = []
        for row in rows[: self.config.max_operations_per_turn]:
            if not isinstance(row, Mapping):
                continue
            op = ProfileOperation.from_dict({**dict(row), "evidence_ids": list(ids), "scope": row.get("scope") or default_scope.to_dict(), "source": "profile_model"})
            if not op.path or op.confidence < self.config.min_model_confidence:
                continue
            if op.explicitness == "inferred" and op.confidence < self.config.min_inferred_confidence:
                continue
            # Không cho model suy diễn thuộc tính nhạy cảm nếu user không nói rõ.
            if op.sensitivity == "sensitive" and op.explicitness != "explicit":
                continue
            out.append(op)
        return list(_clean_operations(out))


def _scope_from_context(context: Mapping[str, Any]) -> UserMemoryScope:
    project = str(context.get("project_id") or context.get("project") or "").strip()
    if project:
        return UserMemoryScope("project", project)
    session = str(context.get("session_id") or "").strip()
    if bool(context.get("session_only")) and session:
        return UserMemoryScope("session", session)
    return UserMemoryScope()


def _scope_from_metadata(metadata: Mapping[str, Any]) -> UserMemoryScope:
    return UserMemoryScope.from_value(metadata.get("scope") or {"kind": "project", "key": metadata.get("project_id")}) if metadata.get("project_id") else UserMemoryScope.from_value(metadata.get("scope"))


def _sensitivity_for_path(path: str) -> str:
    lowered = path.lower()
    sensitive_fragments = ("health", "medical", "religion", "sexual", "biometric", "political", "password", "credential")
    if any(x in lowered for x in sensitive_fragments):
        return "sensitive"
    if any(x in lowered for x in ("identity", "address", "location", "relationship", "birthday", "gender")):
        return "personal"
    return "normal"


def _clean_operations(operations: Iterable[ProfileOperation]) -> tuple[ProfileOperation, ...]:
    seen: set[tuple[str, str, str]] = set()
    out: list[ProfileOperation] = []
    for op in operations:
        if op.tier is None:
            op.tier = determine_memory_tier(op.path, op.explicitness, 1, op.value)
        key = (op.op, op.path, repr(op.value))
        if key in seen:
            continue
        seen.add(key)
        out.append(op)
    return tuple(out)


__all__ = ["ProfileExtractionConfig", "UserProfileExtractor", "UserProfileModelAdapter"]

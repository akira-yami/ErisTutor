# -*- coding: utf-8 -*-
"""Registry chung cho evidence có cấu trúc từ extension/bridge.

Mục tiêu của module này là giữ chat pipeline không biết domain cụ thể. Chess,
IDE, browser automation hoặc một extension khác chỉ cần cung cấp cùng contract:

- trạng thái engagement có còn active hay không;
- snapshot hiện tại có còn tươi hay không;
- câu user hiện tại có liên quan domain đó hay không;
- evidence grounding đã chuẩn hóa.

Nhờ vậy ``chat_cycle`` không phải chứa một rừng ``if chess`` rồi vài tháng sau
mọc thêm ``if coding``. Loài người đã đủ khổ với những khu rừng như thế.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StructuredEvidenceSource(Protocol):
    """Contract tối thiểu cho một nguồn evidence có cấu trúc."""

    structured_source_id: str
    engagement_kind: str

    def is_engagement_active(self) -> bool: ...

    def is_state_fresh(self, *, max_age_seconds: float = 30.0) -> bool: ...

    def matches_turn(self, user_text: str) -> bool: ...

    def current_grounding_evidence(
        self,
        *,
        max_age_seconds: float = 30.0,
        relevant_to_turn: bool = False,
    ) -> tuple[dict[str, object], ...]: ...


@dataclass(frozen=True, slots=True)
class StructuredEvidenceContext:
    """Snapshot đã chuẩn hóa của một nguồn structured evidence."""

    source_id: str
    engagement_kind: str
    engagement_id: str
    engagement_active: bool
    state_fresh: bool
    relevant_to_turn: bool
    evidence: tuple[dict[str, object], ...] = ()

    @property
    def usable_for_turn(self) -> bool:
        return bool(self.relevant_to_turn and self.state_fresh and self.evidence)


class StructuredEvidenceProvider:
    """Điểm tập trung duy nhất cho evidence từ các bridge/extension.

    Provider không hiểu FEN, DOM hay AST. Nó chỉ điều phối các source cùng
    contract và trả context đã chuẩn hóa cho chat perception/source grounding.
    """

    def __init__(self, *, logger: Any | None = None) -> None:
        self.logger = logger
        self._lock = threading.RLock()
        self._sources: dict[str, Any] = {}

    def register(self, source: Any, *, replace: bool = True) -> str:
        source_id = self._source_id(source)
        if not source_id:
            raise ValueError("structured evidence source phải có structured_source_id")
        with self._lock:
            existing = self._sources.get(source_id)
            # Cùng một object được đăng ký lại trong mỗi chat turn thì bỏ qua.
            # Việc này giữ API idempotent và tránh spam log vô nghĩa.
            if existing is source:
                return source_id
            if not replace and existing is not None:
                raise ValueError(f"structured evidence source đã tồn tại: {source_id}")
            self._sources[source_id] = source
        action = "replaced" if existing is not None else "registered"
        self._log(
            "info",
            "[structured evidence] %s source=%s kind=%s",
            action,
            source_id,
            self._engagement_kind(source),
        )
        return source_id

    def unregister(self, source_id: str) -> None:
        key = str(source_id or "").strip().lower()
        if not key:
            return
        with self._lock:
            self._sources.pop(key, None)

    def sources(self) -> tuple[Any, ...]:
        with self._lock:
            return tuple(self._sources.values())

    def contexts_for_turn(
        self,
        user_text: str,
        *,
        max_age_seconds: float = 30.0,
    ) -> tuple[StructuredEvidenceContext, ...]:
        contexts: list[StructuredEvidenceContext] = []
        for source in self.sources():
            source_id = self._source_id(source)
            engagement_kind = self._engagement_kind(source)
            active = self._call_bool(source, "is_engagement_active", default=False)
            fresh = self._call_bool(
                source,
                "is_state_fresh",
                default=False,
                max_age_seconds=max_age_seconds,
            )
            relevant = self._call_bool(
                source,
                "matches_turn",
                default=False,
                user_text=user_text,
            )
            evidence: tuple[dict[str, object], ...] = ()
            evidence_fn = getattr(source, "current_grounding_evidence", None)
            if callable(evidence_fn) and fresh:
                try:
                    raw_items = tuple(
                        evidence_fn(
                            max_age_seconds=max_age_seconds,
                            relevant_to_turn=relevant,
                        )
                        or ()
                    )
                    evidence = tuple(
                        self._normalize_item(
                            item,
                            source_id=source_id,
                            engagement_kind=engagement_kind,
                            active=active,
                            fresh=fresh,
                            relevant=relevant,
                        )
                        for item in raw_items
                        if isinstance(item, dict)
                    )
                except Exception as exc:  # noqa: BLE001 - bridge boundary
                    self._log("debug", "[structured evidence] read failed source=%s error=%s", source_id, exc)
            engagement_id = ""
            for item in evidence:
                engagement_id = str(item.get("engagement_id", "") or "").strip()
                if engagement_id:
                    break
            if not engagement_id:
                getter = getattr(source, "active_engagement_id", None)
                if callable(getter):
                    try:
                        engagement_id = str(getter() or "").strip()
                    except Exception:
                        engagement_id = ""
            contexts.append(
                StructuredEvidenceContext(
                    source_id=source_id,
                    engagement_kind=engagement_kind,
                    engagement_id=engagement_id,
                    engagement_active=active,
                    state_fresh=fresh,
                    relevant_to_turn=relevant,
                    evidence=evidence,
                )
            )
        return tuple(contexts)

    def evidence_for_turn(
        self,
        user_text: str,
        *,
        max_age_seconds: float = 30.0,
    ) -> tuple[dict[str, object], ...]:
        return tuple(
            item
            for context in self.contexts_for_turn(user_text, max_age_seconds=max_age_seconds)
            for item in context.evidence
        )

    def has_fresh_relevant_state(self, user_text: str, *, max_age_seconds: float = 30.0) -> bool:
        return any(
            context.usable_for_turn
            for context in self.contexts_for_turn(user_text, max_age_seconds=max_age_seconds)
        )

    def active_engagement_id(self, *, engagement_kind: str = "") -> str:
        wanted = str(engagement_kind or "").strip().lower()
        for context in self.contexts_for_turn("", max_age_seconds=30.0):
            if not context.engagement_active:
                continue
            if wanted and context.engagement_kind != wanted:
                continue
            if context.engagement_id:
                return context.engagement_id
        return ""

    @staticmethod
    def _normalize_item(
        item: dict[str, object],
        *,
        source_id: str,
        engagement_kind: str,
        active: bool,
        fresh: bool,
        relevant: bool,
    ) -> dict[str, object]:
        normalized = dict(item)
        normalized.setdefault("source", source_id)
        normalized.setdefault("evidence_kind", "structured_runtime")
        normalized.setdefault("engagement_kind", engagement_kind)
        normalized.setdefault("engagement_active", active)
        normalized.setdefault("state_fresh", fresh)
        normalized.setdefault("relevant_to_turn", relevant)
        normalized.setdefault("role", "primary_candidate" if relevant else "ambient")
        tags = [str(tag or "").strip().lower() for tag in tuple(normalized.get("tags", ()) or ())]
        for tag in ("structured_runtime", "structured_sensor", engagement_kind):
            if tag and tag not in tags:
                tags.append(tag)
        normalized["tags"] = tags
        return normalized

    @staticmethod
    def _source_id(source: Any) -> str:
        value = getattr(source, "structured_source_id", "") or getattr(source, "source_id", "")
        return str(value or "").strip().lower()

    @staticmethod
    def _engagement_kind(source: Any) -> str:
        return str(getattr(source, "engagement_kind", "") or "").strip().lower()

    @staticmethod
    def _call_bool(source: Any, method_name: str, *, default: bool, **kwargs: Any) -> bool:
        method = getattr(source, method_name, None)
        if not callable(method):
            return default
        try:
            return bool(method(**kwargs))
        except TypeError:
            try:
                return bool(method(*kwargs.values()))
            except Exception:
                return default
        except Exception:
            return default

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


__all__ = [
    "StructuredEvidenceContext",
    "StructuredEvidenceProvider",
    "StructuredEvidenceSource",
]

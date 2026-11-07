# -*- coding: utf-8 -*-
"""Xây episode từ các lượt hội thoại hoặc event ledger."""
from __future__ import annotations
from core.di import resolve_dependency

import inspect
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from memory.semantic_memory import normalize_text

from .extractor import UserProfileModelAdapter
from .models import UserEpisode, UserMemoryScope, clamp01, compact_text


@dataclass(slots=True)
class EpisodeBuilderConfig:
    min_turns: int = 2
    max_turns: int = 32
    max_evidence_ids: int = 96


class EpisodeModelError(RuntimeError):
    """Model không trả được tóm tắt hợp lệ và caller cấm dùng bản heuristic."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "empty_summary")
        super().__init__(f"episode_model_{self.reason}")


class UserEpisodeBuilder:
    def __init__(self, *, model_adapter: UserProfileModelAdapter | None = None, config: EpisodeBuilderConfig | None = None) -> None:
        self.model_adapter = model_adapter
        self.config = resolve_dependency(config, lambda: EpisodeBuilderConfig())

    def build(
        self,
        turns: Iterable[Mapping[str, Any]],
        *,
        session_id: str,
        scope: UserMemoryScope | None = None,
        metadata: Mapping[str, Any] | None = None,
        allow_fallback: bool = True,
        timeout: float | None = None,
    ) -> UserEpisode | None:
        """Dựng episode. ``allow_fallback=False`` làm lỗi model nổi lên thay vì
        âm thầm thay bằng bản tóm tắt heuristic thô (caller giữ turn và retry)."""
        rows = [dict(x) for x in turns if isinstance(x, Mapping)][-self.config.max_turns :]
        rows = [x for x in rows if str(x.get("text") or x.get("content") or "").strip()]
        if len(rows) < self.config.min_turns:
            return None
        started = min(float(x.get("timestamp") or x.get("ts") or time.time()) for x in rows)
        ended = max(float(x.get("timestamp") or x.get("ts") or time.time()) for x in rows)
        evidence_ids = tuple(dict.fromkeys(str(x.get("id") or x.get("turn_id") or "") for x in rows if str(x.get("id") or x.get("turn_id") or "").strip()))[: self.config.max_evidence_ids]
        payload = {
            "session_id": session_id,
            "turns": [
                {
                    "role": str(x.get("role") or x.get("speaker") or "unknown"),
                    "text": compact_text(x.get("text") or x.get("content"), 1800),
                    "id": str(x.get("id") or x.get("turn_id") or ""),
                    "session_id": str(session_id),
                    "text_truncated": bool(x.get("text_truncated")) or len(str(x.get("text") or x.get("content") or "")) > 1800,
                }
                for x in rows
            ],
            "metadata": dict(metadata or {}),
            "contract": {
                "title": "ngắn",
                "summary": "sự kiện, quyết định và điều user coi trọng",
                "do_not_infer_sensitive_traits": True,
            },
        }
        # Retain one bounded source window in the episode, not a transcript
        # duplicated into every profile record. Old episodes remain readable.
        source_metadata = {**dict(metadata or {}), "source_turns": payload["turns"]}
        model, error = self._from_model(payload, timeout)
        if model is None and not allow_fallback:
            raise EpisodeModelError(error or "empty_summary")
        if model is not None:
            return UserEpisode(
                id="",
                session_id=session_id,
                title=str(model.get("title") or "Phiên trò chuyện"),
                summary=str(model.get("summary") or ""),
                outcome=str(model.get("outcome") or ""),
                topics=tuple(model.get("topics") or ()),
                entities=tuple(model.get("entities") or ()),
                decisions=tuple(model.get("decisions") or ()),
                open_loops=tuple(model.get("open_loops") or ()),
                user_signals=tuple(model.get("user_signals") or ()),
                evidence_ids=evidence_ids,
                importance=clamp01(model.get("importance"), 0.6),
                scope=scope or UserMemoryScope(),
                started_at=started,
                ended_at=ended,
                source="episode_model",
                metadata=source_metadata,
            )
        return self._fallback(rows, session_id=session_id, scope=scope or UserMemoryScope(), evidence_ids=evidence_ids, started=started, ended=ended, metadata=source_metadata)

    def _from_model(self, payload: Mapping[str, Any], timeout: float | None = None) -> tuple[Mapping[str, Any] | None, str]:
        """Trả ``(kết quả, tên lỗi)``; lỗi rỗng khi model trả tóm tắt hợp lệ."""
        if self.model_adapter is None:
            return None, "no_adapter"
        summarize = self.model_adapter.summarize_episode
        kwargs: dict[str, Any] = {}
        if timeout is not None and _accepts_timeout(summarize):
            kwargs["timeout"] = float(timeout)
        try:
            result = summarize(payload, **kwargs)
        except Exception as exc:
            return None, str(getattr(exc, "status", "") or type(exc).__name__)
        if isinstance(result, Mapping) and str(result.get("summary") or "").strip():
            return result, ""
        return None, "empty_summary"

    def _fallback(
        self,
        rows: list[dict[str, Any]],
        *,
        session_id: str,
        scope: UserMemoryScope,
        evidence_ids: tuple[str, ...],
        started: float,
        ended: float,
        metadata: Mapping[str, Any] | None,
    ) -> UserEpisode:
        user_turns = [compact_text(x.get("text") or x.get("content"), 360) for x in rows if str(x.get("role") or x.get("speaker") or "").lower() in {"user", "human", "bạn", "nguoi_dung"}]
        assistant_turns = [compact_text(x.get("text") or x.get("content"), 360) for x in rows if str(x.get("role") or x.get("speaker") or "").lower() in {"assistant", "eris"}]
        topics = _top_terms(" ".join(user_turns), limit=6)
        title = "Trao đổi về " + ", ".join(topics[:3]) if topics else "Phiên trò chuyện"
        summary_parts = []
        if user_turns:
            summary_parts.append("Người dùng tập trung vào: " + " | ".join(user_turns[-3:]))
        if assistant_turns:
            summary_parts.append("ERIS đã phản hồi/triển khai: " + assistant_turns[-1])
        decisions = tuple(x for x in user_turns if any(k in normalize_text(x) for k in ("lam di", "chuyen", "dung", "chot")))[-5:]
        open_loops = tuple(x for x in user_turns if x.rstrip().endswith("?") or any(k in normalize_text(x) for k in ("sau do", "tiep", "con")))[-5:]
        signals = tuple(sig for x in user_turns for sig in _derive_user_signals(x))[-8:]
        importance = clamp01(0.42 + 0.04 * len(rows) + 0.08 * bool(decisions) + 0.08 * bool(open_loops), 0.6)
        return UserEpisode(
            id="",
            session_id=session_id,
            title=title,
            summary=" ".join(summary_parts),
            outcome=assistant_turns[-1] if assistant_turns else "",
            topics=topics,
            decisions=decisions,
            open_loops=open_loops,
            user_signals=signals,
            evidence_ids=evidence_ids,
            importance=importance,
            scope=scope,
            started_at=started,
            ended_at=ended,
            source="episode_fallback",
            metadata=dict(metadata or {}),
        )


def _accepts_timeout(func: Any) -> bool:
    """Fake/adapter cũ có ``summarize_episode(payload)`` vẫn chạy được."""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False
    return "timeout" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _top_terms(text: str, *, limit: int) -> tuple[str, ...]:
    stop = {"toi", "tui", "minh", "ban", "cai", "nay", "do", "la", "va", "cho", "voi", "khong", "lam", "gi", "roi", "mot", "nhung", "duoc", "nua"}
    tokens = [t for t in normalize_text(text).split() if len(t) >= 3 and t not in stop]
    counts = Counter(tokens)
    return tuple(word for word, _ in counts.most_common(limit))


def _derive_user_signals(text: str) -> tuple[str, ...]:
    norm = normalize_text(text)
    signals: list[str] = []
    if "chú thích" in text.lower() or "chu thich" in norm:
        signals.append("muốn code có chú thích tiếng Việt")
    if "gửi file" in text.lower() or "gui file" in norm:
        signals.append("ưu tiên file hoàn chỉnh")
    if "máy" in text.lower() or "may vl" in norm:
        signals.append("không thích lời thoại máy móc")
    if "elegant" in norm or "va tay" in norm:
        signals.append("ưu tiên kiến trúc sạch, không vá tay")
    if "nghiên cứu" in text.lower() or "nghien cuu" in norm:
        signals.append("muốn phân tích kỹ trước khi triển khai")
    return tuple(dict.fromkeys(signals))


__all__ = ["EpisodeBuilderConfig", "EpisodeModelError", "UserEpisodeBuilder"]

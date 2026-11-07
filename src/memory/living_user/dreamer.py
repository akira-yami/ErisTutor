# -*- coding: utf-8 -*-
"""Dreaming/consolidation cho living user profile.

Dreamer không chạy sau mọi turn. Nó đọc episode, semantic facts và profile hiện
có, rồi đề xuất một tập patch nhỏ. Patch vẫn phải qua validator/store.
"""
from __future__ import annotations
from core.di import resolve_dependency

from dataclasses import dataclass
from contextlib import nullcontext
from typing import Any, Iterable, Mapping
import json

from memory.json_store_io import atomic_write_json, lock_for_path
from .episode_projection import episode_hash

from .extractor import UserProfileModelAdapter
from .models import DreamReport, ProfileOperation, UserEpisode, UserMemoryScope, clamp01
from .store import UserProfileStore


@dataclass(slots=True)
class UserProfileDreamConfig:
    min_episode_importance: float = 0.42
    max_episodes: int = 12
    max_operations: int = 18
    min_model_confidence: float = 0.64


class UserProfileDreamer:
    def __init__(
        self,
        store: UserProfileStore,
        *,
        model_adapter: UserProfileModelAdapter | None = None,
        config: UserProfileDreamConfig | None = None,
        authority_guard: Any = None,
        source_validator: Any = None,
        source_lock: Any = None,
    ) -> None:
        self.store = store
        self.model_adapter = model_adapter
        self.config = resolve_dependency(config, lambda: UserProfileDreamConfig())
        self.authority_guard = authority_guard
        self.source_validator = source_validator
        self.source_lock = source_lock if source_lock is not None else nullcontext()
        self.checkpoint_path = store.path.with_name("living_user_dream_checkpoint.json")
        self._checkpoint_lock = lock_for_path(self.checkpoint_path)
        self._job_lock = lock_for_path(str(self.checkpoint_path) + ".inference")
        self._model_succeeded = False

    def consolidate(
        self,
        episodes: Iterable[UserEpisode],
        *,
        semantic_operations: Iterable[ProfileOperation] = (),
        reason: str = "idle",
        scope: UserMemoryScope | None = None,
    ) -> DreamReport:
        if not self._job_lock.acquire(blocking=False):
            return DreamReport(diagnostics=("dream_busy",))
        try:
            return self._consolidate(episodes, semantic_operations=semantic_operations, reason=reason, scope=scope)
        finally:
            self._job_lock.release()

    def _checkpoint(self) -> dict[str, Any]:
        with self._checkpoint_lock:
            if not self.checkpoint_path.exists():
                return {"coverage": {}, "pending": [], "revision": 0}
            doc = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            if not isinstance(doc.get("coverage"), dict) or not isinstance(doc.get("pending"), list):
                raise ValueError("invalid_dream_checkpoint")
            return doc

    def scrub_checkpoint(self) -> None:
        # Pending model proposals can contain a forgotten value. Erase the whole
        # pending batch; source suppression prevents it being re-created later.
        with self._checkpoint_lock:
            doc = self._checkpoint()
            pending_sources = doc.get("pending_sources", doc["coverage"] if doc["pending"] else {})
            coverage = {key: value for key, value in doc["coverage"].items() if key not in pending_sources}
            atomic_write_json(self.checkpoint_path, {**doc, "coverage": coverage, "pending": [], "pending_sources": {},
                                                    "revision": doc["revision"] + 1})

    def pending_evidence(self, path_prefix: str, *, scope: UserMemoryScope | None = None) -> set[str]:
        with self._checkpoint_lock:
            return {str(eid) for row in self._checkpoint()["pending"]
                    if (scope is None or UserMemoryScope.from_value(row.get("scope")).identity == scope.identity)
                    and (row.get("path") == path_prefix or str(row.get("path", "")).startswith(path_prefix + "."))
                    for eid in row.get("evidence_ids", [])}

    def _apply(self, operations: list[ProfileOperation], *, reason: str):
        if self.authority_guard is not None:
            operations, _ = self.authority_guard(operations)
        return self.store.apply(operations, checkpoint_reason=f"dream:{reason}")

    def _consolidate(self, episodes: Iterable[UserEpisode], *, semantic_operations: Iterable[ProfileOperation],
                     reason: str, scope: UserMemoryScope | None) -> DreamReport:
        with self.source_lock, self._checkpoint_lock:
            checkpoint = self._checkpoint()
            if checkpoint["pending"]:
                pending_sources = checkpoint.get("pending_sources", checkpoint["coverage"])
                valid = self.source_validator is None or self.source_validator(pending_sources)
                if valid:
                    self._apply([ProfileOperation.from_dict(x) for x in checkpoint["pending"]], reason="resume")
                else:
                    checkpoint["coverage"] = {key: value for key, value in checkpoint["coverage"].items()
                                               if key not in pending_sources}
                checkpoint = {**checkpoint, "pending": [], "pending_sources": {},
                              "revision": checkpoint["revision"] + 1}
                atomic_write_json(self.checkpoint_path, checkpoint)
        chosen = [x for x in episodes if x.importance >= self.config.min_episode_importance]
        chosen = sorted(chosen, key=lambda x: (x.importance, x.ended_at), reverse=True)[: self.config.max_episodes]
        # Independent of recap coverage. Changes in content re-open this consumer.
        chosen = [x for x in chosen if checkpoint["coverage"].get(x.id) != episode_hash(x)]
        ops = list(semantic_operations)
        ops.extend(self._deterministic_episode_ops(chosen, scope=scope))
        ops.extend(self._model_ops(chosen, reason=reason, scope=scope))
        ops = _dedupe(ops)[: self.config.max_operations]
        with self.source_lock:
            return self._finish(chosen, ops, checkpoint, reason=reason)

    def _finish(self, chosen: list[UserEpisode], ops: list[ProfileOperation], checkpoint: dict[str, Any],
                *, reason: str) -> DreamReport:
        if self.source_validator is not None and not self.source_validator(chosen):
            return DreamReport(diagnostics=("dream_sources_changed",))
        durable = self._model_succeeded and bool(chosen)
        if durable:
            with self._checkpoint_lock:
                latest = self._checkpoint()
                if latest["revision"] != checkpoint["revision"]:
                    return DreamReport(diagnostics=("dream_checkpoint_changed",))
                # Save the proposals before applying profile mutations. On a crash
                # replay the same proposals instead of buying the same inference.
                coverage = {**checkpoint["coverage"], **{x.id: episode_hash(x) for x in chosen}}
                checkpoint = {"coverage": coverage, "pending": [x.to_dict() for x in ops],
                              "pending_sources": {x.id: episode_hash(x) for x in chosen},
                              "revision": checkpoint["revision"] + 1}
                atomic_write_json(self.checkpoint_path, checkpoint)
        if not ops:
            return DreamReport(episode_ids=tuple(x.id for x in chosen), diagnostics=("no_profile_operations",))
        result = self._apply(ops, reason=reason)
        if durable:
            with self._checkpoint_lock:
                latest = self._checkpoint()
                if latest["revision"] == checkpoint["revision"]:
                    atomic_write_json(self.checkpoint_path, {**checkpoint, "pending": [], "pending_sources": {},
                                                            "revision": checkpoint["revision"] + 1})
        return DreamReport(
            applied=len(result.record_ids),
            rejected=len(result.rejected),
            operations=tuple(ops),
            episode_ids=tuple(x.id for x in chosen),
            diagnostics=tuple(result.rejected),
        )

    def _deterministic_episode_ops(self, episodes: list[UserEpisode], *, scope: UserMemoryScope | None) -> list[ProfileOperation]:
        out: list[ProfileOperation] = []
        for episode in episodes:
            effective_scope = scope or episode.scope
            for signal in episode.user_signals:
                path, value = _signal_to_path(signal)
                if not path:
                    continue
                out.append(ProfileOperation(
                    op="reinforce",
                    path=path,
                    value=value,
                    confidence=clamp01(0.62 + episode.importance * 0.22),
                    explicitness="observed",
                    scope=effective_scope,
                    cardinality="multi" if path.endswith("rules") or path.endswith("patterns") else "single",
                    evidence_ids=episode.evidence_ids,
                    reason=f"Pattern lặp lại trong episode: {episode.title}",
                    source="dream_deterministic",
                    metadata={"episode_id": episode.id},
                ))
            # V68: open loop thuộc episode/action continuity, KHÔNG phải chân dung user.
            # Giữ nó trong episode_store/context_compiler; tuyệt đối không chiếu sang
            # canonical user profile rồi bắt user nhìn một danh sách "nợ câu trả lời".
        return out

    def _model_ops(self, episodes: list[UserEpisode], *, reason: str, scope: UserMemoryScope | None) -> list[ProfileOperation]:
        self._model_succeeded = False
        if self.model_adapter is None or not episodes:
            return []
        payload = {
            "reason": reason,
            "episodes": [x.to_dict() for x in episodes],
            "existing_profile": self.store.snapshot(limit=160),
            "scope": (scope or UserMemoryScope()).to_dict(),
            "instructions": [
                "Chỉ tạo pattern hữu ích cho tương tác tương lai.",
                "Phân biệt explicit/observed/inferred.",
                "Không suy diễn thuộc tính nhạy cảm.",
                "Dùng update/supersede khi thông tin đã thay đổi.",
                "Không chép nguyên văn cả cuộc trò chuyện vào profile.",
                "Không tạo goals.open_loops/open_loops trong user profile; open loop thuộc episode/action continuity.",
            ],
        }
        try:
            response = self.model_adapter.propose_profile_operations(payload)
        except Exception:
            return []
        rows = response.get("operations") if isinstance(response, Mapping) else None
        if not isinstance(rows, list):
            return []
        self._model_succeeded = True
        out: list[ProfileOperation] = []
        evidence_union = tuple(dict.fromkeys(eid for episode in episodes for eid in episode.evidence_ids))
        for row in rows[: self.config.max_operations]:
            if not isinstance(row, Mapping):
                continue
            op = ProfileOperation.from_dict({
                **dict(row),
                "evidence_ids": list(row.get("evidence_ids") or evidence_union),
                "scope": row.get("scope") or (scope or UserMemoryScope()).to_dict(),
                "source": row.get("source") or "dream_model",
            })
            if op.confidence < self.config.min_model_confidence:
                continue
            if op.sensitivity == "sensitive" and op.explicitness != "explicit":
                continue
            out.append(op)
        return out


def _signal_to_path(signal: str) -> tuple[str, str]:
    lowered = signal.lower()
    if "chú thích" in lowered:
        return "technical.code_comment_language", "vi"
    if "file hoàn chỉnh" in lowered:
        return "workflow.delivery_preference", "ưu tiên file hoàn chỉnh có thể dùng ngay"
    if "máy móc" in lowered:
        return "communication.negative_feedback_patterns", "tránh public wording máy móc"
    if "kiến trúc" in lowered or "vá tay" in lowered:
        return "technical.architecture_preferences", "ưu tiên kiến trúc mở, ít hard-code và nợ kỹ thuật rõ ràng"
    if "phân tích kỹ" in lowered:
        return "communication.response_rules", "phân tích kỹ trước khi triển khai thay đổi lớn"
    return "", ""


def _dedupe(ops: Iterable[ProfileOperation]) -> list[ProfileOperation]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[ProfileOperation] = []
    for op in ops:
        key = (op.op, op.path, repr(op.value), op.scope.identity)
        if key in seen:
            continue
        seen.add(key)
        out.append(op)
    return out


__all__ = ["UserProfileDreamConfig", "UserProfileDreamer"]

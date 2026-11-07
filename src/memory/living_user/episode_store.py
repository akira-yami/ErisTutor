# -*- coding: utf-8 -*-
"""Episode store cho living user memory."""
from __future__ import annotations

import json
import os
from copy import deepcopy
from functools import wraps
from pathlib import Path
from typing import Any, Iterable, Mapping

from memory.json_store_io import atomic_write_json, locked_path, lock_for_path
from memory.semantic_memory import normalize_text

from .models import UserEpisode, UserMemoryScope, clamp01
from .episode_projection import episode_hash


def _mutation(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with self.lock:
            before = deepcopy((self._items, self._archive, self._history, self._suppressed, self._blocked_evidence))
            try:
                return method(self, *args, **kwargs)
            except Exception:
                self._items, self._archive, self._history, self._suppressed, self._blocked_evidence = before
                raise
    return run


class UserEpisodeStore:
    def __init__(self, path: str | os.PathLike[str], *, max_items: int = 240) -> None:
        self.path = Path(path)
        self.max_items = max(20, int(max_items))
        self._items: dict[str, UserEpisode] = {}
        self._archive: dict[str, UserEpisode] = {}
        self._history: dict[str, UserEpisode] = {}
        self._suppressed: set[str] = set()
        self._blocked_evidence: set[str] = set()
        self.lock = lock_for_path(self.path)
        self.load()

    def all(self, *, include_archived: bool = False) -> tuple[UserEpisode, ...]:
        with self.lock:
            items = {**self._archive, **self._items} if include_archived else self._items
            return tuple(deepcopy(x) for x in sorted(items.values(), key=lambda x: (-x.ended_at, -x.importance))
                         if x.id not in self._suppressed)

    def versions(self, episode_id: str) -> tuple[UserEpisode, ...]:
        """Original snapshots for inspection; suppressed sources are not recallable."""
        with self.lock:
            return tuple(deepcopy(x) for x in (*self._history.values(), *self._archive.values(), *self._items.values())
                         if x.id == episode_id and x.id not in self._suppressed)

    @_mutation
    def add(self, episode: UserEpisode) -> UserEpisode:
        from .episode_projection import content_hash
        if self._blocked_evidence & {content_hash(x) for x in (episode.id, *episode.evidence_ids)}:
            return episode  # Never re-persist a forgotten source from a delayed job.
        existing = self._items.get(episode.id) or self._archive.get(episode.id) or self._find_same_session(episode)
        if existing is not None:
            self._history[episode_hash(existing)] = deepcopy(existing)
            # Một session được consolidate nhiều lần thì merge, không nhân bản recap.
            existing.summary = episode.summary or existing.summary
            existing.outcome = episode.outcome or existing.outcome
            existing.title = episode.title or existing.title
            existing.topics = tuple(dict.fromkeys((*existing.topics, *episode.topics)))[:24]
            existing.entities = tuple(dict.fromkeys((*existing.entities, *episode.entities)))[:24]
            existing.decisions = tuple(dict.fromkeys((*existing.decisions, *episode.decisions)))[:20]
            existing.open_loops = tuple(dict.fromkeys((*existing.open_loops, *episode.open_loops)))[:20]
            existing.user_signals = tuple(dict.fromkeys((*existing.user_signals, *episode.user_signals)))[:24]
            existing.evidence_ids = tuple(dict.fromkeys((*existing.evidence_ids, *episode.evidence_ids)))[:128]
            existing.importance = clamp01(max(existing.importance, episode.importance) + 0.02)
            existing.ended_at = max(existing.ended_at, episode.ended_at)
            source_turns = list(existing.metadata.get("source_turns", ()))
            known = {str(row.get("id")) for row in source_turns if row.get("id")}
            for row in episode.metadata.get("source_turns", ()):
                if row.get("id") and str(row["id"]) not in known:
                    source_turns.append(dict(row))
                    known.add(str(row["id"]))
            existing.metadata.update(episode.metadata)
            if source_turns:
                existing.metadata["source_turns"] = source_turns[-48:]
            saved = existing
            self._archive.pop(existing.id, None)
            self._items[existing.id] = existing
        else:
            self._items[episode.id] = episode
            saved = episode
        self._trim()
        self.save()
        return saved

    def _find_same_session(self, episode: UserEpisode) -> UserEpisode | None:
        if not episode.session_id:
            return None
        candidates = [x for x in (*self._archive.values(), *self._items.values())
                      if x.id not in self._suppressed and x.session_id == episode.session_id
                      and x.scope.identity == episode.scope.identity]
        return max(candidates, key=lambda x: x.ended_at) if candidates else None

    def search(
        self,
        query: str,
        *,
        limit: int = 6,
        scope: UserMemoryScope | None = None,
        embedding_index: Any | None = None,
    ) -> tuple[UserEpisode, ...]:
        items = [x for x in self.all(include_archived=True) if scope is None or x.scope.matches(scope)]
        if not items:
            return ()
        norm = normalize_text(query)
        if not norm:
            return tuple(sorted(items, key=lambda x: (x.importance, x.ended_at), reverse=True)[:limit])
        q_tokens = set(norm.split())
        q_vec = None
        if embedding_index is not None and callable(getattr(embedding_index, "embed_query", None)):
            try:
                q_vec = embedding_index.embed_query(query)
            except Exception:
                q_vec = None
        texts = [
            " ".join((episode.title, episode.summary, episode.outcome, *episode.topics, *episode.entities, *episode.open_loops))
            for episode in items
        ]
        # Batch hóa recall: gom hết candidate rồi gọi embed_documents MỘT LẦN thay vì
        # embed_document từng cái trong vòng lặp. Giữ nguyên fallback: khi
        # embedding_index=None, q_vec rỗng, hoặc không có embed_documents callable
        # thì dense=0.0 toàn bộ (lexical only), KHÔNG crash.
        d_vecs: list[Any] | None = None
        embed_documents = getattr(embedding_index, "embed_documents", None)
        if q_vec is not None and callable(embed_documents):
            try:
                candidate_vecs = embed_documents(texts)
                if len(candidate_vecs) == len(items):
                    d_vecs = list(candidate_vecs)
            except Exception:
                d_vecs = None
        scored: list[tuple[float, UserEpisode]] = []
        for idx, episode in enumerate(items):
            text = texts[idx]
            e_norm = normalize_text(text)
            e_tokens = set(e_norm.split())
            lexical = len(q_tokens & e_tokens) / max(1, len(q_tokens))
            phrase = 1.0 if len(norm) >= 8 and norm in e_norm else 0.0
            dense = 0.0
            if d_vecs is not None:
                dense = max(0.0, _cosine(q_vec, d_vecs[idx]))
            score = lexical * 0.50 + phrase * 0.20 + dense * 0.20 + episode.importance * 0.08 + _recency(episode.ended_at) * 0.02
            if score >= 0.08 and (lexical > 0.0 or phrase > 0.0 or dense >= 0.20):
                scored.append((score, episode))
        scored.sort(key=lambda x: x[0], reverse=True)
        return tuple(item for _, item in scored[: max(1, limit)])

    def recent(self, *, limit: int = 8, scope: UserMemoryScope | None = None) -> tuple[UserEpisode, ...]:
        items = [x for x in self.all(include_archived=True) if scope is None or x.scope.matches(scope)]
        return tuple(sorted(items, key=lambda x: x.ended_at, reverse=True)[:limit])

    @_mutation
    def archive_cold(self, *, older_than_days: float = 7.0, now: float | None = None) -> int:
        """Move originals to cold storage without rewriting their content/identity."""
        import time
        cutoff = (time.time() if now is None else now) - older_than_days * 86400
        cold = {key: value for key, value in self._items.items() if value.ended_at < cutoff}
        if cold:
            self._archive.update(cold)
            self._items = {key: value for key, value in self._items.items() if key not in cold}
            self.save()
        return len(cold)

    @_mutation
    def compact(
        self,
        *,
        older_than_days: float = 45.0,
        topic_similarity: float = 0.60,
        max_merges: int = 24,
        now: float | None = None,
    ) -> int:
        """Gộp episode cũ gần trùng, giữ evidence và open-loop quan trọng.

        Chỉ gộp episode cùng scope, đã cũ và không có open loop. Episode có việc
        đang mở luôn được giữ nguyên. Đây là warm->cold compression, không phải
        xóa lịch sử tùy hứng vì file JSON trông hơi to.
        """
        import time
        current = float(now or time.time())
        cutoff = current - max(1.0, float(older_than_days)) * 86400.0
        candidates = [x for x in self._items.values() if x.ended_at < cutoff and not x.open_loops]
        candidates.sort(key=lambda x: x.ended_at)
        removed = 0
        consumed: set[str] = set()
        for index, left in enumerate(candidates):
            if left.id in consumed or removed >= max_merges:
                continue
            left_tokens = set(normalize_text(" ".join((left.title, left.summary, *left.topics))).split())
            for right in candidates[index + 1:]:
                if right.id in consumed or right.scope.identity != left.scope.identity:
                    continue
                right_tokens = set(normalize_text(" ".join((right.title, right.summary, *right.topics))).split())
                similarity = len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
                if similarity < topic_similarity:
                    continue
                keeper, dropped = (right, left) if right.importance >= left.importance else (left, right)
                self._history[episode_hash(keeper)] = deepcopy(keeper)
                self._archive[dropped.id] = deepcopy(dropped)
                keeper.summary = (keeper.summary + " " + dropped.summary).strip()[:1200]
                keeper.topics = tuple(dict.fromkeys((*keeper.topics, *dropped.topics)))[:24]
                keeper.entities = tuple(dict.fromkeys((*keeper.entities, *dropped.entities)))[:24]
                keeper.decisions = tuple(dict.fromkeys((*keeper.decisions, *dropped.decisions)))[:20]
                keeper.user_signals = tuple(dict.fromkeys((*keeper.user_signals, *dropped.user_signals)))[:24]
                keeper.evidence_ids = tuple(dict.fromkeys((*keeper.evidence_ids, *dropped.evidence_ids)))[:128]
                keeper.importance = clamp01(max(keeper.importance, dropped.importance) + 0.02)
                keeper.started_at = min(keeper.started_at, dropped.started_at)
                keeper.ended_at = max(keeper.ended_at, dropped.ended_at)
                keeper.metadata["compressed_episode_ids"] = list(dict.fromkeys([
                    *keeper.metadata.get("compressed_episode_ids", []), dropped.id,
                    *dropped.metadata.get("compressed_episode_ids", []),
                ]))[-80:]
                self._items.pop(dropped.id, None)
                consumed.add(dropped.id)
                removed += 1
                break
        if removed:
            self.save()
        return removed

    @_mutation
    def suppress_evidence(self, evidence_ids: Iterable[str]) -> int:
        """Hide secondary interpretations after correction/soft forget, keep originals."""
        wanted = set(evidence_ids)
        from .episode_projection import content_hash
        self._blocked_evidence.update(content_hash(x) for x in wanted)
        affected = {x.id for x in (*self._items.values(), *self._archive.values(), *self._history.values())
                    if wanted & {x.id, *x.evidence_ids}}
        self._suppressed.update(affected)
        if wanted:
            self.save()
        return len(affected)

    @_mutation
    def scrub_evidence(self, evidence_ids: Iterable[str]) -> int:
        """Xóa episode có chứa evidence bị hard-delete.

        Không cố sửa câu summary bằng regex vì có thể để sót nội dung đã quên.
        Xóa cả episode liên quan là lựa chọn bảo thủ nhưng đúng về quyền riêng tư.
        """
        wanted = {str(item) for item in evidence_ids if str(item).strip()}
        if not wanted:
            return 0
        from .episode_projection import content_hash
        self._blocked_evidence.update(content_hash(x) for x in wanted)
        removed_ids = {
            item.id for item in (*self._items.values(), *self._archive.values(), *self._history.values())
            if wanted & {item.id, *item.evidence_ids}
        }
        for item_id in removed_ids:
            self._items.pop(item_id, None)
            self._archive.pop(item_id, None)
        self._history = {key: item for key, item in self._history.items() if item.id not in removed_ids}
        self._suppressed.update(removed_ids)
        self.save()
        return len(removed_ids)

    def _trim(self) -> None:
        ranked = sorted(self._items.values(), key=lambda x: (x.importance, x.ended_at), reverse=True)
        self._archive.update({x.id: deepcopy(x) for x in ranked[self.max_items:]})
        self._items = {x.id: x for x in ranked[: self.max_items]}

    def load(self) -> None:
        if not self.path.is_file() or self.path.stat().st_size <= 0:
            return
        try:
            with locked_path(self.path):
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            rows = raw.get("episodes") if isinstance(raw, Mapping) else raw
            self._items = {
                x.id: x for x in (UserEpisode.from_dict(row) for row in rows or [] if isinstance(row, Mapping))
            }
            if isinstance(raw, Mapping):
                self._archive = {x.id: x for x in (UserEpisode.from_dict(row) for row in raw.get("archive", []))}
                self._history = {episode_hash(x): x for x in (UserEpisode.from_dict(row) for row in raw.get("history", []))}
                self._suppressed = set(raw.get("suppressed_ids", []))
                self._blocked_evidence = set(raw.get("blocked_evidence", []))
        except Exception as exc:
            raise ValueError("episode_store_load_failed") from exc

    def save(self) -> None:
        with self.lock:
            atomic_write_json(self.path, {"schema_version": 2,
                "episodes": [x.to_dict() for x in self._items.values()],
                "archive": [x.to_dict() for x in self._archive.values()],
                "history": [x.to_dict() for x in self._history.values()],
                "suppressed_ids": sorted(self._suppressed), "blocked_evidence": sorted(self._blocked_evidence)})


def _cosine(left: Iterable[float], right: Iterable[float]) -> float:
    # Fail-soft: row hỏng (None, giá trị không iterate/float được) rơi về 0.0 thay
    # vì raise. Batch provider thật luôn trả tuple float, nhưng dense chỉ là tín
    # hiệu phụ nên không được phép làm sập recall.
    try:
        a = tuple(float(x) for x in left)
        b = tuple(float(x) for x in right)
    except (TypeError, ValueError):
        return 0.0
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _recency(timestamp: float) -> float:
    import time
    age_days = max(0.0, (time.time() - float(timestamp or 0.0)) / 86400.0)
    return 1.0 / (1.0 + age_days / 14.0)


__all__ = ["UserEpisodeStore"]

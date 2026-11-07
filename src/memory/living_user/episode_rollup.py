"""Durable, scoped rolling recaps. Inference runs only from maintenance.

The recap and its source coverage are one atomic document. Sources remain in
the episode store, including its cold archive; recap coverage is not permission
to skip profile learning or semantic distillation.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from memory.json_store_io import atomic_write_json, lock_for_path

from .episode_projection import anchor_label, content_hash, episode_hash, episode_view, time_anchor, token_cost
from .models import UserEpisode, UserMemoryScope

logger = logging.getLogger(__name__)

ROLLUP_SYSTEM = """Bạn nén ký ức hội thoại thành một bản kể quá khứ ngắn, không học profile.
Trả JSON duy nhất: {"segments":[{"anchor":"YYYY-MM-DD","text":"..."}]}.
Đọc previous_segments đã lưu và CHỈ thêm new_episodes. Nén lại bản kể để giữ ngắn.
anchor phải thuộc allowed_anchors, dùng mốc của sự kiện được kể, không đổi tuổi sự kiện.
Cùng anchor gộp thành một đoạn, tối đa 8 đoạn. text là văn xuôi tiếng Việt tự nhiên;
KHÔNG viết mốc tương đối như hôm qua/tuần trước trong text: runtime tự gắn nhãn.
Không xuất ID, evidence, topics, decisions thành danh sách; không chép lời gốc dài.
Giữ ý có giá trị, trạng thái chưa xong, phủ định và điều kiện. Không tự hoàn tất open loop.
Phân biệt lời user, lời Eris và kết quả thực sự; lời đùa/nhập vai không thành sự thật.
Không coi recap cũ là nguồn chứng minh hành động đã hoàn tất. Không thêm dữ kiện.
Nếu phải lược bớt, bỏ chi tiết phụ, không cắt ngang câu hoặc bỏ phủ định.
Giới hạn tổng UTF-8 bytes của text theo max_text_bytes (không phải số ký tự).
"""


@dataclass(frozen=True)
class EpisodeRollupConfig:
    recent_count: int = 4
    recent_days: float = 7.0
    batch_size: int = 8
    recap_tokens: int = 600
    episode_tokens: int = 3000
    stable_seconds: float = 3600.0
    retry_seconds: float = 300.0
    max_calls_per_run: int = 1


class EpisodeRollupStore:
    def __init__(self, path: Path, episodes: Any, *, config: EpisodeRollupConfig | None = None,
                 authority: Callable[[], Any] | None = None) -> None:
        self.path = Path(path)
        self.episodes = episodes
        self.config = config or EpisodeRollupConfig()
        self.authority = authority or (lambda: ())
        self.lock = lock_for_path(self.path)
        self._job = lock_for_path(str(self.path) + ".inference")
        self._retry_at = 0.0

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "entries": {}}
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("schema_version") != 1 or not isinstance(doc.get("entries"), dict):
            raise ValueError("invalid_episode_rollup_store")
        return doc

    def _authority_hash(self, scope: UserMemoryScope) -> str:
        return content_hash([x.to_dict() for x in self.authority()
                             if x.scope.identity == scope.identity
                             or (x.target_scope is not None and x.target_scope.identity == scope.identity)])

    @staticmethod
    def _public(episode: UserEpisode, *, include_sensitive: bool = False) -> bool:
        return include_sensitive or (episode.scope.kind != "private"
            and episode.metadata.get("sensitivity") not in {"private", "sensitive"})

    def recent(self, *, scope: UserMemoryScope | None = None, now: float | None = None,
               include_sensitive: bool = False) -> tuple[UserEpisode, ...]:
        current = time.time() if now is None else now
        active = scope or UserMemoryScope()
        rows = [x for x in self.episodes.all(include_archived=True)
                if x.scope.matches(active) and self._public(x, include_sensitive=include_sensitive)
                and current - x.ended_at <= self.config.recent_days * 86400]
        return tuple(rows[:self.config.recent_count])

    def _valid(self, entry: Mapping[str, Any], sources: Mapping[str, str], scope: UserMemoryScope) -> bool:
        return (entry.get("prompt_version") == 1 and entry.get("authority") == self._authority_hash(scope)
                and all(sources.get(key) == value for key, value in entry.get("coverage", {}).items()))

    def run(self, adapter: Any, *, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else now
        generate = getattr(adapter, "summarize_episode_rollup", None)
        if not callable(generate):
            return {"status": "unavailable", "calls": 0}
        if current < self._retry_at or not self._job.acquire(blocking=False):
            return {"status": "deferred", "calls": 0}
        calls, committed = 0, 0
        try:
            rows = self.episodes.all(include_archived=True)
            scopes = {x.scope.identity: x.scope for x in rows if self._public(x)}
            for scope in scopes.values():
                if calls >= self.config.max_calls_per_run:
                    break
                # Lock order is always sources -> recap. Never hold either during inference.
                with self.episodes.lock, self.lock:
                    all_rows = [x for x in self.episodes.all(include_archived=True)
                                if x.scope.identity == scope.identity and self._public(x)]
                    hashes = {x.id: episode_hash(x) for x in all_rows}
                    doc = self._load()
                    old = doc["entries"].get(scope.identity, {})
                    revision = old.get("revision", 0)
                    valid = self._valid(old, hashes, scope)
                    coverage = dict(old.get("coverage", {})) if valid else {}
                    previous = old.get("segments", []) if valid else []
                    fresh = {x.id for x in self.recent(scope=scope, now=current)}
                    eligible = [x for x in reversed(all_rows)
                                if x.id not in fresh and current - x.ended_at >= self.config.stable_seconds
                                and coverage.get(x.id) != hashes[x.id]]
                    # A long-running session becomes eligible once its checkpoint is quiet.
                    batch = eligible[:self.config.batch_size]
                    if not batch:
                        continue
                    authority = self._authority_hash(scope)
                    anchors = sorted({x["anchor"] for x in previous} | {time_anchor(x.ended_at) for x in batch})
                    payload = {"previous_segments": previous, "new_episodes": [
                        {"anchor": time_anchor(x.ended_at), "title": x.title, "summary": x.summary,
                         "outcome": x.outcome, "open_loops": list(x.open_loops)} for x in batch],
                        "allowed_anchors": anchors, "max_text_bytes": max(80, self.config.recap_tokens - 160)}
                calls += 1
                response = generate(payload)
                segments = self._validate(response, anchors)
                with self.episodes.lock, self.lock:
                    latest = self._load()
                    latest_entry = latest["entries"].get(scope.identity, {})
                    latest_rows = [x for x in self.episodes.all(include_archived=True)
                                   if x.scope.identity == scope.identity and self._public(x)]
                    if (latest_entry.get("revision", 0) != revision
                            or {x.id: episode_hash(x) for x in latest_rows} != hashes
                            or self._authority_hash(scope) != authority):
                        return {"status": "stale", "calls": calls, "committed": committed}
                    coverage.update({x.id: hashes[x.id] for x in batch})
                    latest["entries"][scope.identity] = {"scope": scope.to_dict(), "revision": revision + 1,
                        "prompt_version": 1, "authority": authority, "segments": segments, "coverage": coverage}
                    atomic_write_json(self.path, latest)
                    committed += 1
            return {"status": "committed" if committed else "unchanged", "calls": calls, "committed": committed}
        except Exception as exc:
            self._retry_at = current + self.config.retry_seconds
            detail = str(exc)[:160]
            logger.warning("[episode_rollup] failed error=%s reason=%s detail=%s retry_in=%.0fs",
                           type(exc).__name__, getattr(exc, "status", ""), detail, self.config.retry_seconds)
            return {"status": "failed", "error": type(exc).__name__, "reason": getattr(exc, "status", ""),
                    "detail": detail, "calls": calls, "committed": committed}
        finally:
            self._job.release()

    def _validate(self, response: Any, anchors: list[str]) -> list[dict[str, str]]:
        rows = response.get("segments") if isinstance(response, Mapping) else None
        if not isinstance(rows, list) or not 1 <= len(rows) <= 8:
            raise ValueError("invalid_rollup_segments")
        out, used = [], set()
        for row in rows:
            if (not isinstance(row, Mapping) or row.get("anchor") not in anchors
                    or row["anchor"] in used or not isinstance(row.get("text"), str) or not row["text"].strip()):
                raise ValueError("invalid_rollup_segment")
            used.add(row["anchor"])
            out.append({"anchor": row["anchor"], "text": " ".join(row["text"].split())})
        if token_cost(" ".join(x["text"] for x in out)) > max(80, self.config.recap_tokens - 160):
            raise ValueError("rollup_budget_exceeded")
        return sorted(out, key=lambda x: x["anchor"], reverse=True)

    def render(self, *, scope: UserMemoryScope | None = None, now: float | None = None,
               include_sensitive: bool = False) -> str:
        current = time.time() if now is None else now
        active = scope or UserMemoryScope()
        try:
            with self.episodes.lock, self.lock:
                hashes = {x.id: episode_hash(x) for x in self.episodes.all(include_archived=True)}
                doc = self._load()
                paragraphs: list[str] = []
                # Prefer the explicit project/session recap when the shared budget is small.
                entries = sorted(doc["entries"].values(), key=lambda x: x["scope"]["kind"] == "global")
                for entry in entries:
                    entry_scope = UserMemoryScope.from_value(entry["scope"])
                    if (not entry_scope.matches(active) or (entry_scope.kind == "private" and not include_sensitive)
                            or not self._valid(entry, hashes, entry_scope)):
                        continue
                    for segment in entry["segments"]:
                        text = f"{anchor_label(segment['anchor'], now=current)}: {segment['text']}"
                        if token_cost(" ".join((*paragraphs, text))) <= self.config.recap_tokens:
                            paragraphs.append(text)
                return " ".join(paragraphs)
        except (ValueError, KeyError, TypeError, OSError, AttributeError):
            # A corrupt/invalidated projection is never used as canonical memory.
            return ""

    def prompt_view(self, *, scope: UserMemoryScope | None = None, now: float | None = None,
                    include_sensitive: bool = False) -> dict[str, Any]:
        current = time.time() if now is None else now
        recap = self.render(scope=scope, now=current, include_sensitive=include_sensitive)
        rows: list[dict[str, Any]] = []
        for episode in self.recent(scope=scope, now=current, include_sensitive=include_sensitive):
            row = episode_view(episode, now=current)
            if token_cost({"recent_episodes": [*rows, row], "episode_recap": recap}) <= self.config.episode_tokens:
                rows.append(row)
        return {"recent_episodes": rows, "episode_recap": recap}

    def invalidate_evidence(self, evidence_ids: Any) -> None:
        """Remove derived text as well as coverage; no forgotten text in this file."""
        wanted = set(evidence_ids)
        with self.episodes.lock, self.lock:
            affected = {x.id for x in (*self.episodes._items.values(), *self.episodes._archive.values(),
                                       *self.episodes._history.values()) if wanted & {x.id, *x.evidence_ids}}
            affected.update(wanted)
            doc = self._load()
            entries = {key: value for key, value in doc["entries"].items()
                       if not (set(value.get("coverage", {})) & affected)}
            if len(entries) != len(doc["entries"]):
                atomic_write_json(self.path, {**doc, "entries": entries})

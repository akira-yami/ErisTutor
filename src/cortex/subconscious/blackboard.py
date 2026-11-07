# -*- coding: utf-8 -*-
"""Blackboard Phase 0 cho subconscious workers.

Blackboard là nơi các worker nền bỏ ThoughtFragment vào. Nó không chọn câu nói,
không gọi LLM, không quyết định quan hệ. Nó chỉ làm ba việc buồn tẻ nhưng cứu mạng:

1. Validate fragment đi qua contract.
2. Dedupe/refresh các fragment cùng nguồn/kind/tags gần nhau.
3. Decay + chọn top fragment cho Synthesizer.

Không có Blackboard, 15 agent sẽ ném mọi thứ thẳng vào pipeline. Đó không phải
"tâm trí sống", đó là group chat không có admin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import threading
from typing import Any, Iterable, Mapping

from .thought_fragment import (
    ThoughtFragment,
    ThoughtFragmentValidationError,
    clean_text,
    clamp01,
    now_timestamp,
)


@dataclass(slots=True, frozen=True)
class BlackboardSnapshot:
    """Ảnh chụp trạng thái Blackboard tại một thời điểm.

    Snapshot bất biến giúp Synthesizer đọc ổn định trong khi worker khác vẫn có
    thể submit fragment. Nếu đọc trực tiếp dict live, ta lại tự mời race condition
    đến uống trà.
    """

    created_at: float
    fragments: tuple[ThoughtFragment, ...]
    expired_ids: tuple[str, ...] = field(default_factory=tuple)

    def top(self, limit: int = 8) -> tuple[ThoughtFragment, ...]:
        """Trả các fragment mạnh nhất trong snapshot."""

        return tuple(
            sorted(
                self.fragments,
                key=lambda item: item.priority_score(self.created_at),
                reverse=True,
            )[: max(0, int(limit))]
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize snapshot cho debug trace."""

        return {
            "created_at": round(float(self.created_at), 3),
            "fragment_count": len(self.fragments),
            "expired_ids": list(self.expired_ids),
            "fragments": [item.to_dict() for item in self.top(limit=12)],
        }


class SubconsciousBlackboard:
    """Kho tạm cho ThoughtFragment.

    Thread-safe ở mức đơn giản bằng RLock, đủ cho runtime single-process hiện tại.
    Đây không phải message broker. Đừng biến nó thành Kafka mặc váy Gothic.
    """

    def __init__(self, *, max_fragments: int = 96, default_ttl_seconds: float = 300.0) -> None:
        self.max_fragments = max(8, int(max_fragments or 96))
        self.default_ttl_seconds = max(1.0, float(default_ttl_seconds or 300.0))
        self._lock = threading.RLock()
        self._fragments: dict[str, ThoughtFragment] = {}
        self._dedupe_index: dict[str, str] = {}
        self._last_expired_ids: tuple[str, ...] = ()

    def submit(self, fragment: ThoughtFragment | Mapping[str, Any]) -> ThoughtFragment:
        """Đưa một fragment lên Blackboard.

        Nếu truyền dict, hàm sẽ tạo ThoughtFragment qua from_mapping(). Nếu fragment
        cùng dedupe key đã tồn tại, Blackboard refresh bản cũ thay vì tạo thêm rác.
        """

        item = fragment if isinstance(fragment, ThoughtFragment) else ThoughtFragment.from_mapping(fragment)
        if item.ttl_seconds <= 0:
            item = item.refreshed(now=item.updated_at)
        key = self._dedupe_key(item)
        with self._lock:
            previous_id = self._dedupe_index.get(key)
            if previous_id and previous_id in self._fragments:
                previous = self._fragments[previous_id]
                merged = previous.refreshed(
                    content=item.content,
                    evidence_ids=item.evidence_ids,
                    confidence=item.confidence,
                    urgency=item.urgency,
                    affect_charge=item.affect_charge,
                    tags=item.tags,
                    now=item.updated_at,
                )
                self._fragments[previous_id] = merged
                self._trim_locked(now=item.updated_at)
                return merged

            self._fragments[item.id] = item
            self._dedupe_index[key] = item.id
            self._trim_locked(now=item.updated_at)
            return item

    def submit_many(self, fragments: Iterable[ThoughtFragment | Mapping[str, Any]]) -> tuple[ThoughtFragment, ...]:
        """Submit nhiều fragment và trả về các bản đã được lưu/refresh."""

        stored: list[ThoughtFragment] = []
        for fragment in fragments:
            stored.append(self.submit(fragment))
        return tuple(stored)

    def get(self, fragment_id: str) -> ThoughtFragment | None:
        """Lấy fragment theo id."""

        with self._lock:
            return self._fragments.get(str(fragment_id))

    def replace(self, fragment: ThoughtFragment) -> ThoughtFragment:
        """Thay một fragment đã tồn tại bằng bản mới đã validate.

        Phase 5 MetaAudit dùng method này để hạ confidence/tắt surface_allowed
        mà không phải xóa rồi submit lại theo đường dedupe. Nếu không có thao tác
        replace rõ ràng, audit rất dễ biến thành trò ảo thuật: báo đã sửa nhưng
        Blackboard vẫn giữ bản cũ. Một màn lừa đảo nhỏ, nhưng bug thì không nhỏ.
        """

        if not isinstance(fragment, ThoughtFragment):
            raise ThoughtFragmentValidationError("replace() cần ThoughtFragment đã validate.")
        with self._lock:
            old = self._fragments.get(fragment.id)
            if old is not None:
                old_key = self._dedupe_key(old)
                if self._dedupe_index.get(old_key) == old.id:
                    self._dedupe_index.pop(old_key, None)
            self._fragments[fragment.id] = fragment
            self._dedupe_index[self._dedupe_key(fragment)] = fragment.id
            self._trim_locked(now=fragment.updated_at)
            return fragment

    def remove_many(self, fragment_ids: Iterable[str]) -> tuple[str, ...]:
        """Xóa nhiều fragment và trả id đã xóa thật."""

        removed: list[str] = []
        for fragment_id in fragment_ids:
            if self.remove(str(fragment_id)):
                removed.append(str(fragment_id))
        return tuple(removed)

    def remove(self, fragment_id: str) -> bool:
        """Xóa fragment theo id.

        Trả True nếu có xóa. Dùng cho Synthesizer/MetaAudit Phase sau khi muốn
        consume hoặc quarantine một fragment.
        """

        fragment_id = str(fragment_id)
        with self._lock:
            item = self._fragments.pop(fragment_id, None)
            if item is None:
                return False
            key = self._dedupe_key(item)
            if self._dedupe_index.get(key) == fragment_id:
                self._dedupe_index.pop(key, None)
            return True

    def clear(self) -> None:
        """Xóa toàn bộ Blackboard.

        Chủ yếu phục vụ test hoặc Meta-Cognitive Audit Phase sau khi phát hiện loop
        rác. Runtime bình thường không nên gọi bừa.
        """

        with self._lock:
            self._fragments.clear()
            self._dedupe_index.clear()
            self._last_expired_ids = ()

    def decay(self, *, now: float | None = None) -> tuple[str, ...]:
        """Xóa fragment hết TTL và trả danh sách id đã hết hạn."""

        current = now_timestamp() if now is None else float(now)
        expired: list[str] = []
        with self._lock:
            for fragment_id, fragment in list(self._fragments.items()):
                if fragment.is_expired(current):
                    expired.append(fragment_id)
                    self._fragments.pop(fragment_id, None)
                    key = self._dedupe_key(fragment)
                    if self._dedupe_index.get(key) == fragment_id:
                        self._dedupe_index.pop(key, None)
            self._last_expired_ids = tuple(expired)
        return tuple(expired)

    def snapshot(self, *, now: float | None = None, include_expired: bool = False) -> BlackboardSnapshot:
        """Trả snapshot bất biến cho Synthesizer.

        Mặc định sẽ decay trước khi snapshot. include_expired=True chỉ dùng khi test
        hoặc debug muốn nhìn fragment chết trước khi dọn.
        """

        current = now_timestamp() if now is None else float(now)
        expired_ids: tuple[str, ...] = ()
        if not include_expired:
            expired_ids = self.decay(now=current)
        with self._lock:
            fragments = tuple(self._fragments.values())
            if include_expired:
                expired_ids = tuple(
                    fragment.id for fragment in fragments if fragment.is_expired(current)
                )
        return BlackboardSnapshot(created_at=current, fragments=fragments, expired_ids=expired_ids)

    def top_fragments(self, *, limit: int = 8, now: float | None = None) -> tuple[ThoughtFragment, ...]:
        """Shortcut lấy top fragment hiện tại."""

        return self.snapshot(now=now).top(limit=limit)

    def __len__(self) -> int:
        with self._lock:
            return len(self._fragments)

    def _trim_locked(self, *, now: float) -> None:
        """Giữ Blackboard dưới max_fragments.

        Ưu tiên giữ fragment có priority cao. Fragment cũ/yếu bị loại trước.
        """

        if len(self._fragments) <= self.max_fragments:
            return
        ranked = sorted(
            self._fragments.values(),
            key=lambda item: item.priority_score(now),
            reverse=True,
        )
        keep_ids = {item.id for item in ranked[: self.max_fragments]}
        for fragment_id, fragment in list(self._fragments.items()):
            if fragment_id in keep_ids:
                continue
            self._fragments.pop(fragment_id, None)
            key = self._dedupe_key(fragment)
            if self._dedupe_index.get(key) == fragment_id:
                self._dedupe_index.pop(key, None)

    def _dedupe_key(self, fragment: ThoughtFragment) -> str:
        """Tạo key gộp fragment lặp.

        Key cố tình không dùng toàn bộ content, vì hai worker có thể diễn đạt khác
        nhau cùng một ý. Dùng source_agent/kind/tag chính + seed nội dung đã rút gọn.
        """

        tag_part = ":".join(fragment.tags[:3]) if fragment.tags else "no_tags"
        seed = self._content_seed(fragment.content)
        return f"{fragment.source_agent}|{fragment.kind}|{tag_part}|{seed}"

    @staticmethod
    def _content_seed(content: str) -> str:
        text = clean_text(content, limit=160).lower()
        text = re.sub(r"[^\w\s\-]+", " ", text)
        tokens = [token for token in text.split() if len(token) >= 3]
        return "_".join(tokens[:8]) or "empty"

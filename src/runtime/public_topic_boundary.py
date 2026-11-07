# -*- coding: utf-8 -*-
"""Durable public-topic closure for ERIS conversation rhythm.

This store is deliberately *not* a memory eraser.  ActiveConcern/MentalMaterial may
continue privately after the user closes a topic.  The store only answers whether a
piece of content is still allowed to project onto the public speech floor.

A boundary is lexical/semantic-light on purpose: it persists a few distinctive terms
from the user's closure utterance (falling back to the immediately preceding public
context when the closure is anaphoric, e.g. "đừng nhắc lại nữa").  If the user later
mentions the closed topic again, that is treated as an explicit reopen and the public
boundary is released.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "eris.public_topic_boundaries.v1"
DEFAULT_TTL_SECONDS = 30.0 * 24.0 * 3600.0

_CLOSURE_RE = re.compile(
    r"(?:\b(?:đổi|chuyển)\s+(?:chủ đề|đề tài|chuyện|cái khác)\b"
    r"|\b(?:nói|bàn|chuyển sang)\s+(?:chuyện|cái|chủ đề)\s+khác\b"
    r"|\b(?:bỏ|cho qua|khép|dừng|thôi)\s+(?:qua\s+)?(?:vụ|chuyện|chủ đề|cái)?\s*(?:này|đó|ấy)?\b"
    r"|\b(?:coi như|cho)\s+(?:vụ|chuyện|cái)?\s*.+?\s+(?:đi vào dĩ vãng|qua rồi|xong rồi)\b"
    r"|\bđi vào dĩ vãng\b|\bđừng nhắc(?: lại)? nữa\b|\bkhông bàn(?: nữa)?\b"
    r"|\b(?:change|switch)\s+(?:the\s+)?topic\b|\bmove\s+on\b|\blet(?:'s| us)\s+(?:leave|drop)\s+(?:that|it)\b)",
    re.IGNORECASE,
)

# Function words + closure boilerplate.  We intentionally keep short colloquial topic
# tokens such as "hả"; length alone would erase exactly the kind of topic seen in the
# regression log.
_STOPWORDS = {
    "a", "ai", "anh", "ban", "biet", "bo", "cai", "cho", "chuyen", "chu", "co", "coi",
    "cua", "da", "dang", "de", "di", "dieu", "do", "dung", "duoc", "em", "gi", "giong",
    "hon", "khac", "khong", "kieu", "la", "lai", "lam", "lan", "luc", "ma", "mot", "muon",
    "na", "nha", "nhe", "nhac", "nhau", "nhu", "noi", "nua", "qua", "roi", "sang", "sau",
    "se", "su", "te", "the", "thi", "thoi", "toi", "tu", "tuong", "vao", "va", "vay", "ve",
    "viec", "vong", "vu", "nay", "kia", "ay", "do", "day", "day", "dung", "ban", "luon",
    # English closure boilerplate
    "change", "switch", "topic", "move", "on", "leave", "drop", "that", "it", "lets", "let", "us",
    # Closure idiom; these words are not useful topic identity on their own.
    "di", "vang", "di", "vao", "dĩ", "vãng",
}


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    text = re.sub(r"[^a-z0-9_\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> tuple[str, ...]:
    folded = _fold(value)
    return tuple(token for token in folded.split() if token and token not in _STOPWORDS)


def is_explicit_topic_closure(text: Any) -> bool:
    return bool(str(text or "").strip() and _CLOSURE_RE.search(str(text or "")))


def _distinctive_terms(text: Any, *, fallback: Any = "", limit: int = 8) -> tuple[str, ...]:
    primary = list(dict.fromkeys(_tokens(text)))
    # Keep tokens that are likely topic identifiers.  Numeric IDs/code names are useful.
    primary = [t for t in primary if len(t) >= 2 or any(ch.isdigit() for ch in t)]
    if primary:
        return tuple(primary[: max(1, int(limit))])
    secondary = list(dict.fromkeys(_tokens(fallback)))
    secondary = [t for t in secondary if len(t) >= 2 or any(ch.isdigit() for ch in t)]
    # Anaphoric closure fallback is noisier. Prefer longer/specific terms and keep only a
    # small fingerprint so one old sentence cannot become a blanket gag order.
    secondary.sort(key=lambda t: (-len(t), t))
    return tuple(secondary[: min(5, max(1, int(limit)))])


@dataclass(frozen=True, slots=True)
class PublicTopicBoundary:
    boundary_id: str
    created_at: float
    updated_at: float
    expires_at: float
    reason: str
    source_text: str
    topic_terms: tuple[str, ...]
    reopened_at: float = 0.0

    @property
    def active(self) -> bool:
        now = time.time()
        return self.reopened_at <= 0.0 and self.expires_at > now and bool(self.topic_terms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "reason": self.reason,
            "source_text": self.source_text,
            "topic_terms": list(self.topic_terms),
            "reopened_at": self.reopened_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PublicTopicBoundary | None":
        try:
            terms = tuple(str(x).strip() for x in raw.get("topic_terms", ()) if str(x).strip())
            return cls(
                boundary_id=str(raw.get("boundary_id") or f"boundary:{uuid.uuid4().hex}"),
                created_at=float(raw.get("created_at") or 0.0),
                updated_at=float(raw.get("updated_at") or 0.0),
                expires_at=float(raw.get("expires_at") or 0.0),
                reason=str(raw.get("reason") or "topic_closure"),
                source_text=str(raw.get("source_text") or "")[:800],
                topic_terms=terms[:12],
                reopened_at=float(raw.get("reopened_at") or 0.0),
            )
        except Exception:
            return None


class PublicTopicBoundaryStore:
    def __init__(self, host: Any = None, *, path: str | os.PathLike[str] | None = None, logger: Any = None) -> None:
        self.host = host
        self.logger = logger or getattr(host, "logger", None)
        base = Path(os.getenv("ERIS_MEMORY_CORE_DIR", "memory_core"))
        self.path = Path(path) if path is not None else base / "public_topic_boundaries.json"
        self._items: list[PublicTopicBoundary] = []
        self._load()

    def _log(self, level: str, message: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            try:
                fn(message, *args)
            except Exception:
                pass

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            items = raw.get("boundaries", ()) if isinstance(raw, dict) else ()
            self._items = [item for item in (PublicTopicBoundary.from_dict(dict(x)) for x in items if isinstance(x, dict)) if item]
            self._prune(save=False)
        except Exception as exc:
            self._items = []
            self._log("debug", "[topic boundary] load degraded: %s", exc)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema": SCHEMA,
                "updated_at": time.time(),
                "boundaries": [item.to_dict() for item in self._items[-48:]],
            }
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception as exc:
            self._log("debug", "[topic boundary] save degraded: %s", exc)

    def _prune(self, *, now: float | None = None, save: bool = True) -> None:
        current = float(now or time.time())
        before = len(self._items)
        # Keep reopened tombstones briefly for observability, then discard them.
        self._items = [
            item for item in self._items
            if item.expires_at > current and not (item.reopened_at > 0.0 and current - item.reopened_at > 86400.0)
        ][-48:]
        if save and len(self._items) != before:
            self._save()

    @staticmethod
    def _matches_terms(text: Any, terms: Iterable[str]) -> bool:
        wanted = tuple(dict.fromkeys(str(t).strip() for t in terms if str(t).strip()))
        if not wanted:
            return False
        present = set(_tokens(text))
        if not present:
            return False
        hits = sum(1 for term in wanted if term in present)
        if len(wanted) == 1:
            return hits == 1
        threshold = 1 if len(wanted) <= 3 else 2
        return hits >= threshold

    def close_topic(
        self,
        text: Any,
        *,
        context_text: Any = "",
        reason: str = "user_topic_closure",
        now: float | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> PublicTopicBoundary | None:
        current = float(now or time.time())
        terms = _distinctive_terms(text, fallback=context_text)
        if not terms:
            self._log("debug", "[topic boundary] closure had no stable fingerprint")
            return None
        # Coalesce repeated closure requests for the same fingerprint.
        for item in reversed(self._items):
            if item.reopened_at <= 0.0 and set(item.topic_terms) == set(terms):
                replacement = PublicTopicBoundary(
                    boundary_id=item.boundary_id,
                    created_at=item.created_at,
                    updated_at=current,
                    expires_at=max(item.expires_at, current + max(3600.0, float(ttl_seconds))),
                    reason=reason,
                    source_text=str(text or "")[:800],
                    topic_terms=terms,
                    reopened_at=0.0,
                )
                self._items[self._items.index(item)] = replacement
                self._save()
                return replacement
        boundary = PublicTopicBoundary(
            boundary_id=f"topic-boundary:{uuid.uuid4().hex[:16]}",
            created_at=current,
            updated_at=current,
            expires_at=current + max(3600.0, float(ttl_seconds)),
            reason=reason,
            source_text=str(text or "")[:800],
            topic_terms=terms,
        )
        self._items.append(boundary)
        self._prune(now=current, save=False)
        self._save()
        self._log("info", "[topic boundary] closed id=%s terms=%s", boundary.boundary_id, ",".join(terms))
        return boundary

    def is_blocked(self, text: Any, *, now: float | None = None) -> bool:
        current = float(now or time.time())
        self._prune(now=current, save=False)
        return any(
            item.reopened_at <= 0.0
            and item.expires_at > current
            and self._matches_terms(text, item.topic_terms)
            for item in self._items
        )

    def note_user_turn(self, text: Any, *, now: float | None = None) -> tuple[str, ...]:
        """Reopen a boundary when the user themselves brings that topic back."""
        current = float(now or time.time())
        raw = str(text or "").strip()
        if not raw or is_explicit_topic_closure(raw):
            return ()
        reopened: list[str] = []
        updated: list[PublicTopicBoundary] = []
        for item in self._items:
            if item.reopened_at <= 0.0 and item.expires_at > current and self._matches_terms(raw, item.topic_terms):
                updated.append(PublicTopicBoundary(
                    boundary_id=item.boundary_id,
                    created_at=item.created_at,
                    updated_at=current,
                    expires_at=item.expires_at,
                    reason=item.reason,
                    source_text=item.source_text,
                    topic_terms=item.topic_terms,
                    reopened_at=current,
                ))
                reopened.append(item.boundary_id)
            else:
                updated.append(item)
        if reopened:
            self._items = updated
            self._save()
            self._log("info", "[topic boundary] reopened ids=%s", ",".join(reopened))
        return tuple(reopened)

    def clear(self) -> None:
        self._items = []
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            self._save()

    def snapshot(self) -> tuple[PublicTopicBoundary, ...]:
        self._prune(save=False)
        return tuple(self._items)


def ensure_public_topic_boundary_store(host: Any) -> PublicTopicBoundaryStore:
    store = getattr(host, "public_topic_boundary_store", None)
    if isinstance(store, PublicTopicBoundaryStore):
        return store
    store = PublicTopicBoundaryStore(host, logger=getattr(host, "logger", None))
    setattr(host, "public_topic_boundary_store", store)
    return store


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "PublicTopicBoundary",
    "PublicTopicBoundaryStore",
    "ensure_public_topic_boundary_store",
    "is_explicit_topic_closure",
]

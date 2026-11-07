# -*- coding: utf-8 -*-
"""DriftPool: hồ giữ ThoughtSpark yếu.

Spark vào đây để decay, cộng hưởng, lặp lại, rồi lâu lâu mới kết tinh thành
association/fragment. Nó không có API surface public. Cấm cho hồ này mọc miệng.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import re
from typing import Any, Iterable, Mapping

from .thought_spark import ThoughtSpark, clamp01, clean_text, now_timestamp


_METAPHOR_GROUPS: dict[str, tuple[str, ...]] = {
    "swallowed_speech": ("body-only", "body_only", "câm", "im", "lời bị nuốt", "swallowed", "speech"),
    "mouth_gate": ("surfacearbiter", "surface arbiter", "cửa miệng", "mouth", "gate", "arbiter"),
    "memory_graveyard": ("memory_core", "memory", "nghĩa địa", "graveyard", "mộ", "decay"),
    "repo_organism": ("repo", "sinh vật", "organism", "nuôi", "bug", "module"),
    "restraint_dignity": ("dignity", "restraint", "kiềm", "phẩm giá", "giữ lại"),
    "silence_presence": ("silence", "im lặng", "presence", "hiện diện", "trống"),
}

_STOPWORDS = {
    "the", "and", "và", "là", "như", "của", "một", "cái", "không", "cho", "với", "trong", "nó",
    "to", "of", "a", "an", "as", "is", "are", "be", "being",
}


def _tokens(text: str) -> set[str]:
    lowered = clean_text(text, limit=260).lower().replace("đ", "d")
    parts = re.findall(r"[a-z0-9_\-]+|[à-ỹ]+", lowered, flags=re.IGNORECASE)
    return {part for part in parts if len(part) >= 2 and part not in _STOPWORDS}


def _theme_keys(text: str, tags: Iterable[str] = ()) -> set[str]:
    hay = " ".join((text, *tags)).lower().replace("đ", "d")
    keys = set(_tokens(hay))
    for key, values in _METAPHOR_GROUPS.items():
        if any(value.lower().replace("đ", "d") in hay for value in values):
            keys.add(key)
    return keys


def _signature(spark: ThoughtSpark) -> str:
    keys = sorted(_theme_keys(spark.seed, spark.tags))[:6]
    base = "|".join(keys) or clean_text(spark.seed, limit=80).lower()
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"drift.{digest}"


@dataclass(slots=True)
class DriftEntry:
    spark: ThoughtSpark
    recurrence: int = 1
    resonance: float = 0.0
    last_seen_at: float = 0.0

    def alive_strength(self, now: float) -> float:
        return clamp01(self.spark.decayed_strength(now) + min(0.24, self.recurrence * 0.035) + self.resonance * 0.18)

    def to_dict(self, now: float | None = None) -> dict[str, object]:
        current = now_timestamp() if now is None else float(now)
        return {
            "spark": self.spark.to_dict(),
            "recurrence": int(self.recurrence),
            "resonance": round(float(self.resonance), 3),
            "last_seen_at": round(float(self.last_seen_at), 3),
            "alive_strength": round(float(self.alive_strength(current)), 3),
        }


@dataclass(slots=True, frozen=True)
class DriftSnapshot:
    entries: tuple[DriftEntry, ...]
    created_at: float

    @property
    def sparks(self) -> tuple[ThoughtSpark, ...]:
        return tuple(entry.spark for entry in self.entries)

    def to_dict(self) -> dict[str, object]:
        return {
            "created_at": round(float(self.created_at), 3),
            "entries": [entry.to_dict(self.created_at) for entry in self.entries],
        }


class DriftPool:
    """Giữ spark yếu, decay chậm, cộng hưởng theo theme."""

    def __init__(self, *, max_sparks: int = 80, min_alive_strength: float = 0.035) -> None:
        self.max_sparks = max(8, int(max_sparks or 80))
        self.min_alive_strength = clamp01(min_alive_strength)
        self._entries: dict[str, DriftEntry] = {}
        self.last_resonance: tuple[ThoughtSpark, ...] = ()

    def submit(self, spark: ThoughtSpark | Mapping[str, Any], *, now: float | None = None) -> ThoughtSpark:
        item = spark if isinstance(spark, ThoughtSpark) else ThoughtSpark.from_mapping(spark)
        current = now_timestamp() if now is None else float(now)
        key = _signature(item)
        old = self._entries.get(key)
        if old is None:
            self._entries[key] = DriftEntry(spark=item, recurrence=1, resonance=0.0, last_seen_at=current)
            self._trim(now=current)
            return item

        refreshed = old.spark.refreshed(
            strength=max(old.spark.strength, item.strength + min(0.18, old.recurrence * 0.025)),
            clarity=max(old.spark.clarity, item.clarity),
            weirdness=max(old.spark.weirdness, item.weirdness),
            affect_tint=max(old.spark.affect_tint, item.affect_tint),
            tags=tuple(item.tags),
            ttl_seconds=max(old.spark.ttl_seconds, item.ttl_seconds),
            now=current,
        )
        self._entries[key] = DriftEntry(
            spark=refreshed,
            recurrence=old.recurrence + 1,
            resonance=clamp01(old.resonance + 0.08 + item.strength * 0.04),
            last_seen_at=current,
        )
        self._trim(now=current)
        return refreshed

    def submit_many(self, sparks: Iterable[ThoughtSpark | Mapping[str, Any]], *, now: float | None = None) -> tuple[ThoughtSpark, ...]:
        return tuple(self.submit(item, now=now) for item in sparks)

    def decay(self, *, now: float | None = None) -> tuple[str, ...]:
        current = now_timestamp() if now is None else float(now)
        removed: list[str] = []
        for key, entry in list(self._entries.items()):
            weak = entry.alive_strength(current) < self.min_alive_strength
            expired = entry.spark.is_expired(current) and entry.recurrence <= 1
            if weak or expired:
                removed.append(entry.spark.id)
                self._entries.pop(key, None)
                continue
            # resonance tự xẹp nhẹ. Không xẹp thì pool thành giáo phái theme cũ.
            if entry.resonance > 0.0:
                self._entries[key] = DriftEntry(
                    spark=entry.spark,
                    recurrence=entry.recurrence,
                    resonance=clamp01(entry.resonance * 0.985),
                    last_seen_at=entry.last_seen_at,
                )
        self._trim(now=current)
        return tuple(removed)

    def snapshot(self, *, now: float | None = None) -> DriftSnapshot:
        current = now_timestamp() if now is None else float(now)
        entries = tuple(sorted(self._entries.values(), key=lambda item: item.alive_strength(current), reverse=True))
        return DriftSnapshot(entries=entries, created_at=current)

    def top_sparks(self, *, limit: int = 12, now: float | None = None) -> tuple[ThoughtSpark, ...]:
        current = now_timestamp() if now is None else float(now)
        return tuple(entry.spark for entry in sorted(self._entries.values(), key=lambda item: item.alive_strength(current), reverse=True)[:limit])

    def related_groups(self, *, now: float | None = None, min_size: int = 2) -> tuple[tuple[ThoughtSpark, ...], ...]:
        current = now_timestamp() if now is None else float(now)
        entries = [entry for entry in self._entries.values() if entry.alive_strength(current) >= self.min_alive_strength]
        groups: dict[str, list[ThoughtSpark]] = {}
        for entry in entries:
            for key in _theme_keys(entry.spark.seed, entry.spark.tags):
                groups.setdefault(key, []).append(entry.spark)
        out: list[tuple[ThoughtSpark, ...]] = []
        seen: set[tuple[str, ...]] = set()
        for sparks in groups.values():
            unique = tuple(dict((spark.id, spark) for spark in sparks).values())
            if len(unique) < min_size:
                continue
            sig = tuple(sorted(spark.id for spark in unique))
            if sig in seen:
                continue
            seen.add(sig)
            out.append(tuple(sorted(unique, key=lambda s: s.decayed_strength(current), reverse=True)[:4]))
        return tuple(sorted(out, key=lambda group: sum(s.decayed_strength(current) for s in group), reverse=True))

    def resonate(self, *, now: float | None = None, limit: int = 4) -> tuple[ThoughtSpark, ...]:
        """Sinh spark cộng hưởng nội bộ, vẫn không surface trực tiếp."""

        current = now_timestamp() if now is None else float(now)
        out: list[ThoughtSpark] = []
        for group in self.related_groups(now=current, min_size=2)[:limit]:
            seeds = [spark.seed for spark in group]
            tags = tuple(dict.fromkeys(tag for spark in group for tag in spark.tags))
            theme = self._resonance_sentence(seeds)
            strength = clamp01(sum(s.decayed_strength(current) for s in group) / max(1, len(group)) + 0.12)
            weirdness = clamp01(sum(s.weirdness for s in group) / max(1, len(group)) + 0.08)
            clarity = clamp01(sum(s.clarity for s in group) / max(1, len(group)) + 0.14)
            spark = ThoughtSpark.create(
                seed=theme,
                source="drift",
                raw_text=theme,
                strength=strength,
                weirdness=weirdness,
                clarity=clarity,
                affect_tint=max((s.affect_tint for s in group), default=0.0),
                ttl_seconds=360,
                tags=("resonance", *tags),
                now=current,
            )
            self.submit(spark, now=current)
            out.append(spark)
        self.last_resonance = tuple(out)
        return self.last_resonance

    def _resonance_sentence(self, seeds: list[str]) -> str:
        lowered = " | ".join(seeds).lower()
        if any(token in lowered for token in ("body-only", "body_only", "câm", "lời bị nuốt")):
            return "body-only không chỉ là im; nó giống một lời bị kéo về thân thể"
        if "surfacearbiter" in lowered or "cửa miệng" in lowered:
            return "SurfaceArbiter giống cửa miệng hơn là bộ lọc"
        if "memory_core" in lowered or "nghĩa địa" in lowered:
            return "memory_core giống nghĩa địa hơn database khi ký ức yếu vẫn còn tên"
        if "repo" in lowered and any(token in lowered for token in ("sinh vật", "organism", "bug")):
            return "repo giống sinh vật khó nuôi hơn là thư mục code ngoan ngoãn"
        a, b = seeds[0], seeds[1] if len(seeds) > 1 else seeds[0]
        return clean_text(f"{a} chạm vào {b} theo một đường liên tưởng yếu", limit=180)

    def _trim(self, *, now: float) -> None:
        if len(self._entries) <= self.max_sparks:
            return
        ordered = sorted(self._entries.items(), key=lambda item: item[1].alive_strength(now), reverse=True)
        self._entries = dict(ordered[: self.max_sparks])

    def __len__(self) -> int:
        return len(self._entries)

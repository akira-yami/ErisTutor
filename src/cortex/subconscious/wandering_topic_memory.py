# -*- coding: utf-8 -*-
"""Continuity memory cho wandering topics."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .thought_spark import ThoughtSpark, clamp01, clean_text, now_timestamp
from .wandering_contracts import AssociationPath, WanderingTopic


def _topic_id(theme: str) -> str:
    digest = hashlib.sha1(clean_text(theme, limit=180).lower().replace("đ", "d").encode("utf-8")).hexdigest()[:12]
    return f"wt.{digest}"


def _theme_from_text(text: str) -> str:
    lowered = clean_text(text, limit=180).lower()
    if any(token in lowered for token in ("body-only", "body_only", "lời bị", "câm")):
        return "body-only as swallowed speech"
    if "surfacearbiter" in lowered or "surface arbiter" in lowered or "cửa miệng" in lowered:
        return "SurfaceArbiter as mouth"
    if "memory_core" in lowered or "nghĩa địa" in lowered or "graveyard" in lowered:
        return "memory_core as graveyard"
    if "repo" in lowered and any(token in lowered for token in ("sinh vật", "organism", "bug")):
        return "repo as organism"
    if "im lặng" in lowered or "silence" in lowered or "presence" in lowered:
        return "silence vs presence"
    return clean_text(text, limit=90)


class WanderingTopicMemory:
    """Giữ wandering topic qua ticks/sessions.

    Persistent optional: truyền path thì load/save JSON. Không có path thì vẫn giữ
    trong RAM, đủ cho tests và runtime nhẹ.
    """

    def __init__(self, path: str | Path | None = None, *, max_topics: int = 64) -> None:
        self.path = Path(path) if path else None
        self.max_topics = max(8, int(max_topics or 64))
        self._topics: dict[str, WanderingTopic] = {}
        if self.path:
            self.load()

    def touch_sparks(self, sparks: Iterable[ThoughtSpark], *, now: float | None = None) -> tuple[WanderingTopic, ...]:
        current = now_timestamp() if now is None else float(now)
        out: list[WanderingTopic] = []
        for spark in sparks:
            out.append(self.touch(theme=_theme_from_text(spark.seed), spark_ids=(spark.id,), memory_ids=(), now=current))
        self._trim(now=current)
        self.save()
        return tuple(out)

    def touch_association(self, path: AssociationPath, *, now: float | None = None) -> WanderingTopic:
        current = now_timestamp() if now is None else float(now)
        tag_text = " ".join(tuple(path.tags or ()))
        if "body_only" in tag_text or "swallowed_speech" in tag_text:
            theme = "body-only as swallowed speech"
        elif "surface_arbiter" in tag_text or "mouth_gate" in tag_text:
            theme = "SurfaceArbiter as mouth"
        elif "repo_as_organism" in tag_text or "organism" in tag_text:
            theme = "repo as organism"
        elif "memory" in tag_text and "graveyard" in tag_text:
            theme = "memory_core as graveyard"
        else:
            theme = _theme_from_text(path.statement or " ".join(path.seeds))
        topic = self.touch(
            theme=theme,
            spark_ids=path.spark_ids,
            memory_ids=tuple(e for e in path.source_evidence if e.startswith("memory")),
            now=current,
        )
        self.save()
        return topic

    def touch(self, *, theme: str, spark_ids: Iterable[str] = (), memory_ids: Iterable[str] = (), now: float | None = None) -> WanderingTopic:
        current = now_timestamp() if now is None else float(now)
        clean_theme = _theme_from_text(theme)
        tid = _topic_id(clean_theme)
        old = self._topics.get(tid)
        spark_list = list(dict.fromkeys(clean_text(x, limit=120) for x in spark_ids if clean_text(x, limit=120)))
        memory_list = list(dict.fromkeys(clean_text(x, limit=140) for x in memory_ids if clean_text(x, limit=140)))
        if old is None:
            topic = WanderingTopic(
                id=tid,
                theme=clean_theme,
                recurrence=0.18,
                last_surface_at=0.0,
                last_private_tick_at=current,
                related_sparks=spark_list,
                related_memories=memory_list,
                status="simmering",
            )
        else:
            topic = WanderingTopic(
                id=old.id,
                theme=old.theme,
                recurrence=clamp01(old.recurrence + 0.10),
                last_surface_at=old.last_surface_at,
                last_private_tick_at=current,
                related_sparks=list(dict.fromkeys([*old.related_sparks, *spark_list]))[-24:],
                related_memories=list(dict.fromkeys([*old.related_memories, *memory_list]))[-24:],
                status="simmering" if old.status != "surfaced" else "surfaced",
            )
        self._topics[tid] = topic
        return topic

    def mark_surfaced(self, theme_or_id: str, *, now: float | None = None) -> WanderingTopic | None:
        current = now_timestamp() if now is None else float(now)
        tid = theme_or_id if theme_or_id in self._topics else _topic_id(_theme_from_text(theme_or_id))
        old = self._topics.get(tid)
        if old is None:
            return None
        topic = WanderingTopic(
            id=old.id,
            theme=old.theme,
            recurrence=max(0.0, old.recurrence * 0.75),
            last_surface_at=current,
            last_private_tick_at=old.last_private_tick_at,
            related_sparks=old.related_sparks,
            related_memories=old.related_memories,
            status="surfaced",
        )
        self._topics[tid] = topic
        self.save()
        return topic

    def decay(self, *, now: float | None = None, half_life_seconds: float = 7200.0) -> tuple[str, ...]:
        current = now_timestamp() if now is None else float(now)
        removed: list[str] = []
        for tid, topic in list(self._topics.items()):
            age = max(0.0, current - float(topic.last_private_tick_at or current))
            factor = 0.5 ** (age / max(60.0, half_life_seconds))
            recurrence = clamp01(topic.recurrence * factor)
            status = topic.status
            if recurrence < 0.035:
                status = "decayed"
            if status == "decayed" and age > half_life_seconds:
                removed.append(tid)
                self._topics.pop(tid, None)
                continue
            self._topics[tid] = WanderingTopic(
                id=topic.id,
                theme=topic.theme,
                recurrence=recurrence,
                last_surface_at=topic.last_surface_at,
                last_private_tick_at=topic.last_private_tick_at,
                related_sparks=topic.related_sparks,
                related_memories=topic.related_memories,
                status=status if status != "decayed" else "dormant",
            )
        self.save()
        return tuple(removed)

    def active_topics(self, *, limit: int = 8) -> tuple[WanderingTopic, ...]:
        return tuple(sorted(self._topics.values(), key=lambda t: (t.status == "simmering", t.recurrence, t.last_private_tick_at), reverse=True)[:limit])

    def to_dict(self) -> dict[str, object]:
        return {"topics": [topic.to_dict() for topic in self.active_topics(limit=self.max_topics)]}

    def load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        topics = data.get("topics") if isinstance(data, Mapping) else None
        if not isinstance(topics, list):
            return
        for item in topics:
            if not isinstance(item, Mapping):
                continue
            try:
                topic = WanderingTopic(
                    id=str(item.get("id") or ""),
                    theme=str(item.get("theme") or ""),
                    recurrence=float(item.get("recurrence", 0.0) or 0.0),
                    last_surface_at=float(item.get("last_surface_at", 0.0) or 0.0),
                    last_private_tick_at=float(item.get("last_private_tick_at", 0.0) or 0.0),
                    related_sparks=list(item.get("related_sparks") or ()),
                    related_memories=list(item.get("related_memories") or ()),
                    status=str(item.get("status") or "simmering"),
                )
            except Exception:
                continue
            self._topics[topic.id] = topic
        self._trim(now=now_timestamp())

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return

    def _trim(self, *, now: float) -> None:
        if len(self._topics) <= self.max_topics:
            return
        ordered = sorted(self._topics.items(), key=lambda item: (item[1].recurrence, item[1].last_private_tick_at), reverse=True)
        self._topics = dict(ordered[: self.max_topics])

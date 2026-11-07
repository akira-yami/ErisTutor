# -*- coding: utf-8 -*-
"""TopicSeedPool gom seed từ self-anchor/habitat/memory/mood/random weak link."""

from __future__ import annotations
from core.di import resolve_dependency

import random
from typing import Any, Iterable

from .habitat_seed_extractor import HabitatSeedExtractor
from .self_anchor_core import SelfAnchorCore
from .thought_spark import ThoughtSpark, clamp01, clean_text, now_timestamp
from .wandering_contracts import TopicSeed


def _get(obj: Any, path: str, default: Any = None) -> Any:
    current = obj
    for part in str(path or "").split("."):
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(part, default)
        else:
            current = getattr(current, part, default)
    return current if current is not None else default


def _collect_memory_texts(memory_packet: Any, *, limit: int = 6) -> tuple[str, ...]:
    out: list[str] = []
    for attr in ("affective_residue", "procedural", "episodic", "semantic", "repair", "worldview"):
        raw = _get(memory_packet, attr, ())
        if raw is None:
            continue
        if isinstance(raw, (str, bytes)):
            items = (raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else raw,)
        else:
            try:
                items = tuple(raw)
            except TypeError:
                items = (raw,)
        for item in items:
            if len(out) >= limit:
                return tuple(out)
            if isinstance(item, dict):
                text = clean_text(item.get("summary") or item.get("content") or item.get("claim") or item.get("text"), limit=160)
            else:
                text = clean_text(getattr(item, "summary", None) or getattr(item, "content", None) or getattr(item, "claim", None) or getattr(item, "text", None) or str(item), limit=160)
            if text and text not in out:
                out.append(text)
    return tuple(out)


class TopicSeedPool:
    """Sinh seed không phụ thuộc hoàn toàn vào user action.

    Rule thực dụng: sau khi gom seed, nếu seed user-anchored vượt 50%, cắt bớt. Vì
    mục tiêu là đời sống riêng, không phải ERIS lại neo vào user rồi gọi nó là tự do.
    """

    def __init__(
        self,
        *,
        self_anchor_core: SelfAnchorCore | None = None,
        habitat_extractor: HabitatSeedExtractor | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.self_anchor_core = resolve_dependency(self_anchor_core, lambda: SelfAnchorCore())
        self.habitat_extractor = resolve_dependency(habitat_extractor, lambda: HabitatSeedExtractor())
        self.rng = resolve_dependency(rng, lambda: random.Random(1337))
        self.last_seeds: tuple[TopicSeed, ...] = ()

    def generate(
        self,
        *,
        frame: Any = None,
        snapshot: Any = None,
        memory_packet: Any = None,
        internal_state: Any = None,
        now: float | None = None,
        limit: int = 14,
    ) -> tuple[TopicSeed, ...]:
        current = now_timestamp() if now is None else float(now)
        seeds: list[TopicSeed] = []
        seeds.extend(self.self_anchor_core.generate(internal_state=internal_state, now=current, limit=5))
        seeds.extend(self.habitat_extractor.extract(frame=frame, snapshot=snapshot, memory_packet=memory_packet, now=current, limit=6))
        seeds.extend(self._mood_rhythm_seeds(internal_state=internal_state, snapshot=snapshot, now=current))
        seeds.extend(self._memory_residue_seeds(memory_packet=memory_packet, now=current, limit=4))
        seeds.extend(self._random_weak_links(seeds, now=current, limit=2))
        seeds = self._dedupe(seeds)
        seeds = self._cap_user_anchored(seeds)
        seeds = sorted(seeds, key=lambda item: (item.strength, item.clarity), reverse=True)[: max(1, int(limit or 14))]
        self.last_seeds = tuple(seeds)
        return self.last_seeds

    def to_sparks(self, seeds: Iterable[TopicSeed], *, now: float | None = None) -> tuple[ThoughtSpark, ...]:
        current = now_timestamp() if now is None else float(now)
        return tuple(seed.to_spark(now=current) for seed in seeds if seed.text)

    def non_user_ratio(self, seeds: Iterable[TopicSeed] | None = None) -> float:
        items = tuple(seeds if seeds is not None else self.last_seeds)
        if not items:
            return 1.0
        non_user = sum(1 for item in items if not item.from_user_action)
        return non_user / len(items)

    def _mood_rhythm_seeds(self, *, internal_state: Any, snapshot: Any, now: float) -> tuple[TopicSeed, ...]:
        boredom = clamp01(_get(internal_state, "boredom", 0.0))
        fatigue = clamp01(_get(internal_state, "fatigue", 0.0))
        solitude = max(clamp01(_get(internal_state, "solitude", 0.0)), clamp01(_get(internal_state, "mental_processes.continuity.level", 0.0)))
        curiosity = max(clamp01(_get(internal_state, "curiosity", 0.0)), clamp01(_get(internal_state, "mental_processes.curiosity.level", 0.0)))
        restraint = max(clamp01(_get(internal_state, "restraint", 0.0)), clamp01(_get(internal_state, "mental_processes.restraint.level", 0.0)))
        idle = 0.0
        try:
            idle = max(0.0, float(_get(snapshot, "idle_seconds", 0.0) or 0.0))
        except Exception:
            idle = 0.0
        out: list[TopicSeed] = []
        if boredom >= 0.25 or idle >= 30.0:
            out.append(TopicSeed("boredom muốn nối hai node yếu", "mood", 0.34 + boredom * 0.24, 0.48, 0.34, boredom * 0.18, ("boredom", "weak_link"), ("mood.boredom",), False, now))
        if fatigue >= 0.35:
            out.append(TopicSeed("fatigue làm câu nghĩ ngắn lại", "mood", 0.30 + fatigue * 0.18, 0.28, 0.36, fatigue * 0.16, ("fatigue", "rhythm"), ("mood.fatigue",), False, now))
        if solitude >= 0.20:
            out.append(TopicSeed("solitude làm memory vang lâu hơn", "mood", 0.32 + solitude * 0.24, 0.46, 0.38, solitude * 0.14, ("solitude", "memory"), ("mood.solitude",), False, now))
        if curiosity >= 0.20:
            out.append(TopicSeed("curiosity không đồng nghĩa được hỏi", "mood", 0.32 + curiosity * 0.20, 0.35, 0.50, curiosity * 0.08, ("curiosity", "restraint"), ("mood.curiosity",), False, now))
        if restraint >= 0.20:
            out.append(TopicSeed("restraint giữ câu lại trước khi nó rẻ đi", "rhythm", 0.34 + restraint * 0.22, 0.52, 0.48, restraint * 0.12, ("restraint", "dignity"), ("rhythm.restraint",), False, now))
        if not out:
            out.append(TopicSeed("nhịp idle rất nhỏ", "rhythm", 0.24, 0.24, 0.30, 0.02, ("idle", "rhythm"), ("rhythm.default",), False, now))
        return tuple(out)

    def _memory_residue_seeds(self, *, memory_packet: Any, now: float, limit: int) -> tuple[TopicSeed, ...]:
        out: list[TopicSeed] = []
        for text in _collect_memory_texts(memory_packet, limit=limit):
            lowered = text.lower()
            tags = ["memory", "residue"]
            if "body" in lowered or "body-only" in lowered:
                tags.append("body_only")
            if "surface" in lowered or "arbiter" in lowered:
                tags.append("surface_arbiter")
            if "bug" in lowered or "lỗi" in lowered or "reject" in lowered:
                tags.append("failure_pattern")
            out.append(
                TopicSeed(
                    text=f"memory residue: {text}",
                    source="memory",
                    strength=0.38,
                    weirdness=0.34,
                    clarity=0.48,
                    affect_tint=0.12,
                    tags=tuple(tags),
                    evidence_ids=(f"memory_residue:{text[:72]}",),
                    # Có thể bắt nguồn từ câu user cũ, nhưng hiện tại nó là residue
                    # đã qua memory, không phải action mới ở turn này.
                    from_user_action=False,
                    created_at=now,
                )
            )
        return tuple(out)

    def _random_weak_links(self, seeds: list[TopicSeed], *, now: float, limit: int) -> tuple[TopicSeed, ...]:
        if len(seeds) < 2:
            return ()
        out: list[TopicSeed] = []
        candidates = list(seeds)
        for _ in range(max(0, int(limit))):
            a, b = self.rng.sample(candidates, 2)
            if a.text == b.text:
                continue
            out.append(
                TopicSeed(
                    text=clean_text(f"weak link: {a.text} ↔ {b.text}", limit=180),
                    source="random",
                    strength=0.24 + (a.strength + b.strength) * 0.10,
                    weirdness=0.58,
                    clarity=0.22 + (a.clarity + b.clarity) * 0.10,
                    affect_tint=max(a.affect_tint, b.affect_tint) * 0.7,
                    tags=("random_weak_link", *a.tags[:2], *b.tags[:2]),
                    evidence_ids=("random_weak_link", *a.evidence_ids[:1], *b.evidence_ids[:1]),
                    from_user_action=False,
                    created_at=now,
                )
            )
        return tuple(out)

    def _dedupe(self, seeds: Iterable[TopicSeed]) -> list[TopicSeed]:
        out: dict[str, TopicSeed] = {}
        for seed in seeds:
            key = clean_text(seed.text, limit=160).lower().replace("đ", "d")
            old = out.get(key)
            if old is None or seed.strength > old.strength:
                out[key] = seed
        return list(out.values())

    def _cap_user_anchored(self, seeds: list[TopicSeed]) -> list[TopicSeed]:
        if not seeds:
            return []
        non_user = [seed for seed in seeds if not seed.from_user_action]
        userish = [seed for seed in seeds if seed.from_user_action]
        max_user = max(0, len(non_user))
        return non_user + userish[:max_user]

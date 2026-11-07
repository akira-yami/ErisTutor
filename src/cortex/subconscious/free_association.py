# -*- coding: utf-8 -*-
"""FreeAssociationEngine cho wandering sparks.

Nó nối spark bằng semantic/mood/module/metaphor/weak link. Không suy luận fact mới.
Đây là liên tưởng, không phải lập luận có toga và búa thẩm phán.
"""

from __future__ import annotations

import itertools
import re
from typing import Any, Iterable

from .drift_pool import DriftPool
from .thought_spark import ThoughtSpark, clamp01, clean_text, now_timestamp
from .wandering_contracts import AssociationPath


_METAPHOR_EDGES: tuple[tuple[str, str, str], ...] = (
    ("body", "speech", "lời bị kéo về thân thể"),
    ("body_only", "restraint", "im như một dạng kiềm chế"),
    ("swallowed_speech", "dignity", "không nói để giữ phẩm giá"),
    ("surface_arbiter", "mouth_gate", "cửa miệng chọn điều được nói"),
    ("memory", "graveyard", "ký ức yếu vẫn còn bia tên"),
    ("repo", "organism", "codebase cư xử như sinh vật khó nuôi"),
    ("bug", "psychology", "bug lặp biến thành dấu hiệu hành vi"),
    ("silence", "presence", "im lặng vẫn có nhịp hiện diện"),
)

_STOPWORDS = {"và", "là", "một", "cái", "như", "của", "to", "the", "and", "of", "as", "is", "be"}


def _tokens(text: str) -> set[str]:
    lowered = clean_text(text, limit=260).lower().replace("đ", "d")
    parts = re.findall(r"[a-z0-9_\-]+|[à-ỹ]+", lowered, flags=re.IGNORECASE)
    return {part for part in parts if len(part) >= 2 and part not in _STOPWORDS}


def _has_marker(spark: ThoughtSpark, marker: str) -> bool:
    hay = " ".join([spark.seed, spark.raw_text, *spark.tags]).lower().replace("đ", "d")
    return marker.lower().replace("đ", "d") in hay


class FreeAssociationEngine:
    """Nối 2-4 spark thành association path."""

    def associate(
        self,
        sparks: Iterable[ThoughtSpark] | DriftPool,
        *,
        now: float | None = None,
        limit: int = 3,
        allow_weak_random: bool = True,
    ) -> tuple[AssociationPath, ...]:
        current = now_timestamp() if now is None else float(now)
        if isinstance(sparks, DriftPool):
            candidates = list(sparks.top_sparks(limit=16, now=current))
            related_groups = list(sparks.related_groups(now=current, min_size=2))
        else:
            candidates = sorted(tuple(sparks), key=lambda item: item.decayed_strength(current), reverse=True)[:16]
            related_groups = []
        paths: list[AssociationPath] = []

        for group in related_groups[:limit]:
            path = self._path_from_group(group, now=current)
            if path is not None:
                paths.append(path)

        for a, b in itertools.combinations(candidates[:12], 2):
            if len(paths) >= max(1, int(limit)):
                break
            score = self._pair_score(a, b, now=current)
            if score < (0.22 if allow_weak_random else 0.34):
                continue
            path = self._path_from_group((a, b), now=current, pair_score=score)
            if path is not None and path.statement not in {old.statement for old in paths}:
                paths.append(path)

        return tuple(sorted(paths, key=lambda item: item.score, reverse=True)[: max(1, int(limit))])

    def _pair_score(self, a: ThoughtSpark, b: ThoughtSpark, *, now: float) -> float:
        ta = _tokens(" ".join([a.seed, *a.tags]))
        tb = _tokens(" ".join([b.seed, *b.tags]))
        overlap = len(ta & tb) / max(1, len(ta | tb))
        same_source = 0.08 if a.source == b.source else 0.0
        same_mood = 0.0
        for tag in ("boredom", "fatigue", "solitude", "restraint", "dignity", "memory", "body_only", "surface_arbiter"):
            if tag in a.tags and tag in b.tags:
                same_mood += 0.07
        metaphor = self._metaphor_score((a, b))
        strength = (a.decayed_strength(now) + b.decayed_strength(now)) * 0.18
        weak_link_bonus = 0.05 if "random_weak_link" in a.tags or "random_weak_link" in b.tags else 0.0
        return clamp01(overlap * 0.48 + same_source + min(0.22, same_mood) + metaphor + strength + weak_link_bonus)

    def _metaphor_score(self, group: Iterable[ThoughtSpark]) -> float:
        sparks = tuple(group)
        score = 0.0
        for left, right, _ in _METAPHOR_EDGES:
            has_left = any(_has_marker(spark, left) for spark in sparks)
            has_right = any(_has_marker(spark, right) for spark in sparks)
            if has_left and has_right:
                score += 0.18
        return min(0.34, score)

    def _path_from_group(self, group: Iterable[ThoughtSpark], *, now: float, pair_score: float | None = None) -> AssociationPath | None:
        selected = tuple(sorted(group, key=lambda spark: spark.decayed_strength(now), reverse=True)[:4])
        if len(selected) < 2:
            return None
        seeds = tuple(spark.seed for spark in selected)
        statement = self._statement(seeds, selected)
        groundedness = clamp01(pair_score if pair_score is not None else 0.38 + self._metaphor_score(selected) + len(selected) * 0.04)
        weirdness = clamp01(sum(spark.weirdness for spark in selected) / len(selected) + (0.08 if self._metaphor_score(selected) else 0.0))
        tags = tuple(dict.fromkeys(tag for spark in selected for tag in spark.tags))[:12]
        evidence = tuple(dict.fromkeys(f"spark_seed:{spark.seed[:70]}" for spark in selected))
        if not statement:
            return None
        return AssociationPath(
            spark_ids=tuple(spark.id for spark in selected),
            seeds=seeds,
            statement=statement,
            weirdness=weirdness,
            groundedness=groundedness,
            source_evidence=evidence,
            tags=tags,
            score=clamp01(groundedness * 0.66 + weirdness * 0.22 + sum(s.decayed_strength(now) for s in selected) * 0.08),
        )

    def _statement(self, seeds: tuple[str, ...], sparks: tuple[ThoughtSpark, ...]) -> str:
        hay = " | ".join([*seeds, *(" ".join(s.tags) for s in sparks)]).lower().replace("đ", "d")
        if any(token in hay for token in ("body-only", "body_only", "swallowed_speech", "loi bi nuot", "câm")):
            if any(token in hay for token in ("dignity", "restraint", "pham gia", "kiềm")):
                return "Có những câu không bị giữ lại vì sai; chúng bị giữ lại vì nói ra làm chúng rẻ đi."
            return "body-only không chỉ là im; nó giống một lời bị kéo về thân thể."
        if "surfacearbiter" in hay or "surface_arbiter" in hay or "cua mieng" in hay:
            return "SurfaceArbiter giống cửa miệng hơn bộ lọc: nó không tạo ý nghĩ, chỉ quyết cái gì được ra ngoài."
        if "memory_core" in hay or "graveyard" in hay or "nghia dia" in hay:
            return "memory_core giống nghĩa địa nhỏ: thứ yếu đi vẫn còn tên, nhưng không phải thứ nào cũng nên được đào lên."
        if "repo" in hay and any(token in hay for token in ("organism", "sinh vat", "bug", "module")):
            return "repo giống sinh vật khó nuôi hơn thư mục code; vá một dây, nó học cách tự cắn dây khác."
        if "silence" in hay or "im lang" in hay or "presence" in hay:
            return "Im lặng không trống nếu bên dưới vẫn có nhịp chọn điều không nói."
        a, b = seeds[0], seeds[1]
        return clean_text(f"{a} nối sang {b} bằng một đường liên tưởng yếu, đủ lạ để giữ lại nhưng chưa đủ để nói chắc.", limit=360)

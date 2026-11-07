# -*- coding: utf-8 -*-
"""ThoughtSpark contract cho tầng wandering/subconscious.

ThoughtSpark là mẩu ý nghĩ cực sớm: còn mơ hồ, chưa claim fact, chưa có quyền
surface. Nó là hạt bụi trong đầu, không phải câu nói. Nếu cho nó nhảy thẳng ra
public speech thì ta vừa phát minh lại status ping có son phấn, xin chúc mừng loài
người vì một tội ác UI mới.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import re
import time
import uuid
from math import isfinite
from typing import Any, Literal, Mapping, Sequence, cast


SparkSource = Literal["self_anchor", "memory", "habitat", "mood", "rhythm", "random", "drift"]
ALLOWED_SPARK_SOURCES: tuple[str, ...] = (
    "self_anchor",
    "memory",
    "habitat",
    "mood",
    "rhythm",
    "random",
    "drift",
)


def now_timestamp() -> float:
    return time.time()


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not isfinite(number):
        number = float(default)
    return max(0.0, min(1.0, number))


def clean_text(value: Any, *, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit > 0 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def normalize_tags(values: Any, *, limit: int = 16) -> list[str]:
    if values is None:
        raw: Sequence[Any] = ()
    elif isinstance(values, str):
        raw = (values,)
    else:
        try:
            raw = tuple(values)
        except TypeError:
            raw = (values,)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = clean_text(item, limit=80).lower().replace(" ", "_")
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def normalize_source(value: Any) -> SparkSource:
    source = clean_text(value or "random", limit=40).lower()
    if source not in ALLOWED_SPARK_SOURCES:
        source = "random"
    return cast(SparkSource, source)


def make_spark_id(seed: str, source: str, created_at: float | None = None) -> str:
    ts = now_timestamp() if created_at is None else float(created_at)
    safe_source = re.sub(r"[^a-zA-Z0-9_\-]+", "_", source or "spark")[:32]
    digest = hashlib.sha1(clean_text(seed, limit=400).encode("utf-8")).hexdigest()[:10]
    return f"ts.{safe_source}.{int(ts * 1000)}.{digest}.{uuid.uuid4().hex[:6]}"


@dataclass(slots=True, frozen=True)
class ThoughtSpark:
    """Mẩu ý nghĩ trước-fragment.

    Khác ThoughtFragment:
    - chưa cần evidence chặt;
    - chưa có quyền claim;
    - không được surface trực tiếp;
    - chỉ được sống trong DriftPool rồi có thể kết tinh thành fragment sau khi có
      resonance/association đủ rõ.
    """

    id: str
    seed: str
    source: SparkSource
    raw_text: str
    strength: float
    weirdness: float
    clarity: float
    affect_tint: float
    ttl_seconds: float
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0

    def __post_init__(self) -> None:
        seed = clean_text(self.seed, limit=180)
        raw_text = clean_text(self.raw_text or seed, limit=420)
        if not seed:
            raise ValueError("ThoughtSpark.seed không được rỗng.")
        timestamp = float(self.created_at or now_timestamp())
        if not isfinite(timestamp) or timestamp <= 0.0:
            timestamp = now_timestamp()
        ttl = float(self.ttl_seconds or 300.0)
        if not isfinite(ttl) or ttl <= 0.0:
            ttl = 300.0
        spark_id = clean_text(self.id, limit=160) or make_spark_id(seed, str(self.source), timestamp)
        object.__setattr__(self, "id", spark_id)
        object.__setattr__(self, "seed", seed)
        object.__setattr__(self, "source", normalize_source(self.source))
        object.__setattr__(self, "raw_text", raw_text)
        object.__setattr__(self, "strength", clamp01(self.strength))
        object.__setattr__(self, "weirdness", clamp01(self.weirdness))
        object.__setattr__(self, "clarity", clamp01(self.clarity))
        object.__setattr__(self, "affect_tint", clamp01(self.affect_tint))
        object.__setattr__(self, "ttl_seconds", ttl)
        object.__setattr__(self, "tags", normalize_tags(self.tags))
        object.__setattr__(self, "created_at", timestamp)

    @classmethod
    def create(
        cls,
        *,
        seed: Any,
        source: Any = "random",
        raw_text: Any = "",
        strength: Any = 0.35,
        weirdness: Any = 0.30,
        clarity: Any = 0.25,
        affect_tint: Any = 0.0,
        ttl_seconds: float = 420.0,
        tags: Any = (),
        now: float | None = None,
    ) -> "ThoughtSpark":
        timestamp = now_timestamp() if now is None else float(now)
        clean_seed = clean_text(seed, limit=180)
        normalized_source = normalize_source(source)
        return cls(
            id=make_spark_id(clean_seed, normalized_source, timestamp),
            seed=clean_seed,
            source=normalized_source,
            raw_text=raw_text or clean_seed,
            strength=strength,
            weirdness=weirdness,
            clarity=clarity,
            affect_tint=affect_tint,
            ttl_seconds=ttl_seconds,
            tags=normalize_tags(tags),
            created_at=timestamp,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ThoughtSpark":
        return cls.create(
            seed=data.get("seed") or data.get("text") or data.get("raw_text"),
            source=data.get("source", "random"),
            raw_text=data.get("raw_text") or data.get("content") or data.get("seed"),
            strength=data.get("strength", 0.35),
            weirdness=data.get("weirdness", 0.30),
            clarity=data.get("clarity", 0.25),
            affect_tint=data.get("affect_tint", 0.0),
            ttl_seconds=float(data.get("ttl_seconds", 420.0) or 420.0),
            tags=data.get("tags") or (),
            now=float(data.get("created_at") or data.get("timestamp") or now_timestamp()),
        )

    def age_seconds(self, now: float | None = None) -> float:
        current = now_timestamp() if now is None else float(now)
        return max(0.0, current - self.created_at)

    def decay_factor(self, now: float | None = None) -> float:
        age = self.age_seconds(now)
        # Spark decay mềm hơn fragment: đến TTL vẫn còn chút bóng mờ để pool dọn
        # quyết định, thay vì biến mất như log rotated lúc cần debug.
        return clamp01(1.0 - age / max(1.0, self.ttl_seconds))

    def decayed_strength(self, now: float | None = None) -> float:
        return clamp01(self.strength * self.decay_factor(now))

    def is_expired(self, now: float | None = None) -> bool:
        current = now_timestamp() if now is None else float(now)
        return current >= self.created_at + self.ttl_seconds

    def refreshed(
        self,
        *,
        strength: Any | None = None,
        clarity: Any | None = None,
        weirdness: Any | None = None,
        affect_tint: Any | None = None,
        tags: Any = (),
        ttl_seconds: float | None = None,
        now: float | None = None,
    ) -> "ThoughtSpark":
        current = now_timestamp() if now is None else float(now)
        merged_tags = normalize_tags([*self.tags, *normalize_tags(tags)])
        return replace(
            self,
            id=make_spark_id(self.seed, self.source, current),
            strength=max(self.strength, clamp01(strength, self.strength)) if strength is not None else self.strength,
            clarity=max(self.clarity, clamp01(clarity, self.clarity)) if clarity is not None else self.clarity,
            weirdness=max(self.weirdness, clamp01(weirdness, self.weirdness)) if weirdness is not None else self.weirdness,
            affect_tint=max(self.affect_tint, clamp01(affect_tint, self.affect_tint)) if affect_tint is not None else self.affect_tint,
            tags=merged_tags,
            ttl_seconds=float(ttl_seconds or self.ttl_seconds),
            created_at=current,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "seed": self.seed,
            "source": self.source,
            "raw_text": self.raw_text,
            "strength": round(float(self.strength), 3),
            "weirdness": round(float(self.weirdness), 3),
            "clarity": round(float(self.clarity), 3),
            "affect_tint": round(float(self.affect_tint), 3),
            "ttl_seconds": round(float(self.ttl_seconds), 3),
            "tags": list(self.tags),
            "created_at": round(float(self.created_at), 3),
            "decayed_strength": round(float(self.decayed_strength()), 3),
            "surface_allowed": False,
        }

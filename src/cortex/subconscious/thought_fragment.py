# -*- coding: utf-8 -*-
"""Contracts Phase 0 cho tầng subconscious của ERIS.

Phase 0 không tạo thêm một cái prompt để ERIS "giả vờ đang nghĩ". File này
chỉ định nghĩa dữ liệu thô mà các worker nền sẽ ném lên Blackboard.

Luồng dự kiến:
    Worker nền -> ThoughtFragment -> Blackboard -> Synthesizer -> ThoughtObject

Quy ước thiết kế:
- ThoughtFragment là mảnh ý nghĩ nhỏ, chưa đủ quyền thành câu nói.
- ThoughtObject là kết quả đã được nén qua conscious bottleneck.
- Không module nào được tự cho rằng fragment của mình quan trọng nhất.
- Mọi confidence/urgency/affect đều bị clamp vào [0, 1] để tránh một worker
  hỏng cấu hình làm cháy cả bảng. Vâng, hệ thần kinh cũng cần cầu chì.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import isfinite
import hashlib
import re
import time
import uuid
from typing import Any, Literal, Mapping, Sequence, cast


PrivacyLevel = Literal["public", "normal", "private", "sensitive"]
RecommendedRoute = Literal["suppress", "store", "rehearse", "surface_candidate"]

ALLOWED_PRIVACY_LEVELS: tuple[str, ...] = ("public", "normal", "private", "sensitive")
ALLOWED_RECOMMENDED_ROUTES: tuple[str, ...] = (
    "suppress",
    "store",
    "rehearse",
    "surface_candidate",
)


class ThoughtFragmentValidationError(ValueError):
    """Lỗi contract riêng cho subconscious Phase 0.

    Runtime có thể bắt exception này để quarantine fragment bẩn thay vì làm rơi
    toàn bộ cycle. Fragment lỗi không nên được quyền thành khủng hoảng nhân cách.
    """


def now_timestamp() -> float:
    """Trả Unix timestamp hiện tại.

    Tách thành hàm riêng để test có thể truyền clock giả nếu cần.
    """

    return time.time()


def clamp01(value: object, default: float = 0.0) -> float:
    """Ép một giá trị số vào khoảng [0.0, 1.0].

    Các worker Phase sau có thể xuất confidence/urgency hơi bẩn. Ở contract
    boundary ta normalize cứng, vì để số NaN đi vào score là tự mở cổng địa ngục
    debug.
    """

    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not isfinite(number):
        number = float(default)
    return max(0.0, min(1.0, number))


def clean_text(value: object, *, limit: int = 500) -> str:
    """Chuẩn hóa text ngắn gọn nhưng không diễn giải lại nội dung.

    Hàm này chỉ gộp whitespace và cắt độ dài. Không rewrite, không sentiment,
    không "làm hay hơn". ThoughtFragment phải giữ dấu vết nguồn, không hóa văn
    mẫu trước khi Synthesizer nhìn thấy.
    """

    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit > 0 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def normalize_privacy_level(value: object) -> PrivacyLevel:
    """Chuẩn hóa privacy_level về public/normal/private/sensitive."""

    normalized = str(value or "normal").strip().lower()
    if normalized not in ALLOWED_PRIVACY_LEVELS:
        allowed = ", ".join(ALLOWED_PRIVACY_LEVELS)
        raise ThoughtFragmentValidationError(
            f"privacy_level không hợp lệ: {value!r}. Mức hợp lệ: {allowed}"
        )
    return cast(PrivacyLevel, normalized)


def normalize_route(value: object) -> RecommendedRoute:
    """Chuẩn hóa route output của Synthesizer."""

    normalized = str(value or "suppress").strip().lower()
    if normalized not in ALLOWED_RECOMMENDED_ROUTES:
        allowed = ", ".join(ALLOWED_RECOMMENDED_ROUTES)
        raise ThoughtFragmentValidationError(
            f"recommended_route không hợp lệ: {value!r}. Route hợp lệ: {allowed}"
        )
    return cast(RecommendedRoute, normalized)


def normalize_string_list(values: object, *, limit: int = 16, item_limit: int = 120) -> tuple[str, ...]:
    """Ép list-like thành tuple[str] đã làm sạch và khử trùng lặp.

    Giữ thứ tự xuất hiện đầu tiên để debug vẫn đọc được đường đi của evidence.
    """

    if values is None:
        raw_items: Sequence[object] = ()
    elif isinstance(values, str):
        raw_items = (values,)
    else:
        try:
            raw_items = tuple(values)  # type: ignore[arg-type]
        except TypeError:
            raw_items = (values,)

    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = clean_text(item, limit=item_limit)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def make_fragment_id(source_agent: str, kind: str, content: str, timestamp: float | None = None) -> str:
    """Tạo id cho ThoughtFragment.

    Id gồm source/kind/thời gian/hash ngắn. Hash chỉ lấy từ content đã normalize
    để giúp debug duplicate mà không cần lưu raw quá dài ở id.
    """

    ts = now_timestamp() if timestamp is None else float(timestamp)
    safe_source = re.sub(r"[^a-zA-Z0-9_\-]+", "_", str(source_agent or "agent"))[:32]
    safe_kind = re.sub(r"[^a-zA-Z0-9_\-]+", "_", str(kind or "thought"))[:32]
    digest = hashlib.sha1(clean_text(content, limit=500).encode("utf-8")).hexdigest()[:10]
    suffix = uuid.uuid4().hex[:6]
    return f"tf.{safe_source}.{safe_kind}.{int(ts * 1000)}.{digest}.{suffix}"


def make_object_id(fragment_ids: Sequence[str], dominant_theme: str, timestamp: float | None = None) -> str:
    """Tạo id cho ThoughtObject đã được Synthesizer nén.

    Dùng fragment_ids + dominant_theme để id có ý nghĩa audit. Vẫn thêm uuid ngắn
    để tránh collision nếu hai synthesis xảy ra cùng mili-giây.
    """

    ts = now_timestamp() if timestamp is None else float(timestamp)
    raw = "|".join([dominant_theme, *fragment_ids[:8]])
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    suffix = uuid.uuid4().hex[:6]
    return f"to.{int(ts * 1000)}.{digest}.{suffix}"


@dataclass(slots=True, frozen=True)
class ThoughtFragment:
    """Một mảnh ý nghĩ nhỏ do worker nền sinh ra.

    Field chính:
    - id: khóa trace duy nhất.
    - source_agent: worker sinh fragment, ví dụ user_modeler/memory_resonator.
    - kind: loại fragment, ví dụ user_intent_hypothesis, memory_echo_fragment.
    - content: nội dung ngắn, chưa phải public speech.
    - evidence_ids: bằng chứng neo fragment. Có thể là id evidence hoặc note debug.
    - confidence: độ tin của worker về fragment này.
    - urgency: độ cần xử lý ngay.
    - affect_charge: lực cảm xúc/nội áp đi kèm.
    - privacy_level: ranh giới lưu/surface.
    - surface_allowed: worker có cho phép đưa lên surface candidate không.
    - ttl_seconds: thời gian sống trên Blackboard trước khi decay thành rác.
    - tags: nhãn phụ cho grouping/dedupe.
    - created_at/updated_at: timestamp phục vụ decay/audit.

    Fragment không được tự quyết nói ra ngoài. Nó chỉ xin được xem xét.
    Một nền dân chủ nhỏ, nhưng ít nhất còn hơn mỗi module tự cầm loa.
    """

    id: str
    source_agent: str
    kind: str
    content: str
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    urgency: float = 0.0
    affect_charge: float = 0.0
    privacy_level: PrivacyLevel = "normal"
    surface_allowed: bool = False
    ttl_seconds: float = 300.0
    tags: tuple[str, ...] = field(default_factory=tuple)
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        fragment_id = clean_text(self.id, limit=180)
        source_agent = clean_text(self.source_agent, limit=80)
        kind = clean_text(self.kind, limit=80)
        content = clean_text(self.content, limit=800)
        if not fragment_id:
            raise ThoughtFragmentValidationError("ThoughtFragment.id không được rỗng.")
        if not source_agent:
            raise ThoughtFragmentValidationError("ThoughtFragment.source_agent không được rỗng.")
        if not kind:
            raise ThoughtFragmentValidationError("ThoughtFragment.kind không được rỗng.")
        if not content:
            raise ThoughtFragmentValidationError("ThoughtFragment.content không được rỗng.")

        now = now_timestamp()
        created_at = float(self.created_at or now)
        updated_at = float(self.updated_at or created_at)
        if not isfinite(created_at) or created_at <= 0:
            raise ThoughtFragmentValidationError("ThoughtFragment.created_at phải là timestamp > 0.")
        if not isfinite(updated_at) or updated_at <= 0:
            raise ThoughtFragmentValidationError("ThoughtFragment.updated_at phải là timestamp > 0.")

        ttl_seconds = float(self.ttl_seconds)
        if not isfinite(ttl_seconds) or ttl_seconds <= 0:
            ttl_seconds = 300.0

        object.__setattr__(self, "id", fragment_id)
        object.__setattr__(self, "source_agent", source_agent)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "evidence_ids", normalize_string_list(self.evidence_ids, limit=24, item_limit=140))
        object.__setattr__(self, "confidence", clamp01(self.confidence))
        object.__setattr__(self, "urgency", clamp01(self.urgency))
        object.__setattr__(self, "affect_charge", clamp01(self.affect_charge))
        object.__setattr__(self, "privacy_level", normalize_privacy_level(self.privacy_level))
        object.__setattr__(self, "surface_allowed", bool(self.surface_allowed))
        object.__setattr__(self, "ttl_seconds", ttl_seconds)
        object.__setattr__(self, "tags", normalize_string_list(self.tags, limit=24, item_limit=80))
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)

    @property
    def expired_at(self) -> float:
        """Timestamp fragment hết hạn nếu không được refresh."""

        return self.updated_at + self.ttl_seconds

    def age_seconds(self, now: float | None = None) -> float:
        """Tuổi fragment tính từ lần update gần nhất."""

        current = now_timestamp() if now is None else float(now)
        return max(0.0, current - self.updated_at)

    def is_expired(self, now: float | None = None) -> bool:
        """Kiểm tra fragment đã hết hạn chưa."""

        current = now_timestamp() if now is None else float(now)
        return current >= self.expired_at

    def decayed_weight(self, now: float | None = None) -> float:
        """Trọng số còn lại sau decay tuyến tính mềm theo TTL.

        1.0 nghĩa là còn mới. 0.0 nghĩa là đã quá hạn. Blackboard dùng hàm này
        để score fragment cũ yếu dần mà không cần xóa ngay lập tức.
        """

        current = now_timestamp() if now is None else float(now)
        age = self.age_seconds(current)
        ratio = 1.0 - (age / max(1.0, self.ttl_seconds))
        return clamp01(ratio)

    def priority_score(self, now: float | None = None) -> float:
        """Score sơ bộ để Blackboard/Synthesizer xếp ưu tiên.

        Công thức ưu tiên confidence + urgency + affect, rồi nhân decay. Đây không
        phải truth. Đây chỉ là thứ tự xét duyệt để conscious bottleneck khỏi ngập.
        """

        base = self.confidence * 0.45 + self.urgency * 0.35 + self.affect_charge * 0.20
        return clamp01(base * self.decayed_weight(now))

    def refreshed(
        self,
        *,
        content: object | None = None,
        evidence_ids: object | None = None,
        confidence: object | None = None,
        urgency: object | None = None,
        affect_charge: object | None = None,
        tags: object | None = None,
        now: float | None = None,
    ) -> "ThoughtFragment":
        """Trả về bản copy đã refresh khi fragment cùng cụm xuất hiện lại.

        Blackboard dùng method này khi dedupe. Không mutate object cũ để audit còn
        sạch. Python object bất biến, đời đỡ bùn hơn một chút.
        """

        current = now_timestamp() if now is None else float(now)
        merged_evidence = tuple(dict.fromkeys((*self.evidence_ids, *normalize_string_list(evidence_ids)))) if evidence_ids is not None else self.evidence_ids
        merged_tags = tuple(dict.fromkeys((*self.tags, *normalize_string_list(tags)))) if tags is not None else self.tags
        return replace(
            self,
            content=clean_text(content if content is not None else self.content, limit=800),
            evidence_ids=merged_evidence,
            confidence=max(self.confidence, clamp01(confidence, self.confidence)) if confidence is not None else self.confidence,
            urgency=max(self.urgency, clamp01(urgency, self.urgency)) if urgency is not None else self.urgency,
            affect_charge=max(self.affect_charge, clamp01(affect_charge, self.affect_charge)) if affect_charge is not None else self.affect_charge,
            tags=merged_tags,
            updated_at=current,
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize phục vụ debug trace / json dump."""

        return {
            "id": self.id,
            "source_agent": self.source_agent,
            "kind": self.kind,
            "content": self.content,
            "evidence_ids": list(self.evidence_ids),
            "confidence": round(float(self.confidence), 3),
            "urgency": round(float(self.urgency), 3),
            "affect_charge": round(float(self.affect_charge), 3),
            "privacy_level": self.privacy_level,
            "surface_allowed": bool(self.surface_allowed),
            "ttl_seconds": round(float(self.ttl_seconds), 3),
            "tags": list(self.tags),
            "created_at": round(float(self.created_at), 3),
            "updated_at": round(float(self.updated_at), 3),
            "priority_score": round(self.priority_score(), 3),
        }

    @classmethod
    def create(
        cls,
        *,
        source_agent: str,
        kind: str,
        content: object,
        evidence_ids: object = (),
        confidence: object = 0.0,
        urgency: object = 0.0,
        affect_charge: object = 0.0,
        privacy_level: object = "normal",
        surface_allowed: bool = False,
        ttl_seconds: float = 300.0,
        tags: object = (),
        now: float | None = None,
    ) -> "ThoughtFragment":
        """Factory tiện dụng cho worker.

        Worker Phase sau chỉ cần gọi ThoughtFragment.create(...), không tự chế id
        và timestamp. Ít code trùng hơn, ít bug tự nguyện hơn.
        """

        timestamp = now_timestamp() if now is None else float(now)
        text = clean_text(content, limit=800)
        return cls(
            id=make_fragment_id(source_agent, kind, text, timestamp),
            source_agent=source_agent,
            kind=kind,
            content=text,
            evidence_ids=normalize_string_list(evidence_ids, limit=24, item_limit=140),
            confidence=clamp01(confidence),
            urgency=clamp01(urgency),
            affect_charge=clamp01(affect_charge),
            privacy_level=normalize_privacy_level(privacy_level),
            surface_allowed=bool(surface_allowed),
            ttl_seconds=float(ttl_seconds or 300.0),
            tags=normalize_string_list(tags, limit=24, item_limit=80),
            created_at=timestamp,
            updated_at=timestamp,
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ThoughtFragment":
        """Tạo fragment từ dict/JSON của worker.

        Method này giúp worker LLM/rule-based Phase sau gửi payload dict mà vẫn
        đi qua contract validation chung.
        """

        now = float(payload.get("created_at") or payload.get("timestamp") or now_timestamp())
        text = clean_text(payload.get("content") or payload.get("text") or payload.get("summary"), limit=800)
        fragment_id = clean_text(payload.get("id"), limit=180) or make_fragment_id(
            str(payload.get("source_agent") or payload.get("source") or "unknown_agent"),
            str(payload.get("kind") or "thought"),
            text,
            now,
        )
        return cls(
            id=fragment_id,
            source_agent=str(payload.get("source_agent") or payload.get("source") or "unknown_agent"),
            kind=str(payload.get("kind") or "thought"),
            content=text,
            evidence_ids=payload.get("evidence_ids") or payload.get("evidence") or (),
            confidence=payload.get("confidence", 0.0),
            urgency=payload.get("urgency", payload.get("pressure", 0.0)),
            affect_charge=payload.get("affect_charge", payload.get("emotional_charge", 0.0)),
            privacy_level=payload.get("privacy_level", "normal"),
            surface_allowed=bool(payload.get("surface_allowed", False)),
            ttl_seconds=float(payload.get("ttl_seconds", 300.0) or 300.0),
            tags=payload.get("tags") or (),
            created_at=now,
            updated_at=float(payload.get("updated_at") or now),
        )


@dataclass(slots=True, frozen=True)
class ThoughtObject:
    """Ý nghĩ đã được Synthesizer nén qua conscious bottleneck.

    Đây là output của Phase 0, không phải output public. Nó nói cho pipeline biết:
    "trong đống fragment kia, chủ đề thắng là gì, có đủ áp lực surface chưa, hay
    nên lưu/diễn nháp/đè xuống".

    Field theo roadmap:
    - id
    - dominant_theme
    - fragments
    - summary
    - confidence
    - surface_pressure
    - suppressed_pressure
    - evidence_ids
    - recommended_route: suppress | store | rehearse | surface_candidate

    Có thêm một vài field phụ để debug và tương thích SurfaceArbiter:
    - privacy_level
    - tags
    - created_at
    """

    id: str
    dominant_theme: str
    fragments: tuple[str, ...]
    summary: str
    confidence: float
    surface_pressure: float
    suppressed_pressure: float
    evidence_ids: tuple[str, ...]
    recommended_route: RecommendedRoute
    privacy_level: PrivacyLevel = "normal"
    tags: tuple[str, ...] = field(default_factory=tuple)
    created_at: float = 0.0

    def __post_init__(self) -> None:
        object_id = clean_text(self.id, limit=180)
        theme = clean_text(self.dominant_theme, limit=160)
        summary = clean_text(self.summary, limit=700)
        if not object_id:
            raise ThoughtFragmentValidationError("ThoughtObject.id không được rỗng.")
        if not theme:
            raise ThoughtFragmentValidationError("ThoughtObject.dominant_theme không được rỗng.")
        if not summary:
            raise ThoughtFragmentValidationError("ThoughtObject.summary không được rỗng.")
        fragment_ids = normalize_string_list(self.fragments, limit=32, item_limit=180)
        if not fragment_ids:
            raise ThoughtFragmentValidationError("ThoughtObject.fragments không được rỗng.")
        timestamp = float(self.created_at or now_timestamp())
        if not isfinite(timestamp) or timestamp <= 0:
            timestamp = now_timestamp()

        object.__setattr__(self, "id", object_id)
        object.__setattr__(self, "dominant_theme", theme)
        object.__setattr__(self, "fragments", fragment_ids)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "confidence", clamp01(self.confidence))
        object.__setattr__(self, "surface_pressure", clamp01(self.surface_pressure))
        object.__setattr__(self, "suppressed_pressure", clamp01(self.suppressed_pressure))
        object.__setattr__(self, "evidence_ids", normalize_string_list(self.evidence_ids, limit=32, item_limit=140))
        object.__setattr__(self, "recommended_route", normalize_route(self.recommended_route))
        object.__setattr__(self, "privacy_level", normalize_privacy_level(self.privacy_level))
        object.__setattr__(self, "tags", normalize_string_list(self.tags, limit=24, item_limit=80))
        object.__setattr__(self, "created_at", timestamp)

    @property
    def should_surface(self) -> bool:
        """True khi object đủ điều kiện làm surface candidate."""

        return self.recommended_route == "surface_candidate"

    @property
    def pressure(self) -> float:
        """Alias tương thích với SurfaceArbiter hiện tại."""

        return self.surface_pressure

    @property
    def momentum_score(self) -> float:
        """Alias tương thích thought_surface cũ.

        Momentum ở Phase 0 tạm lấy max(surface_pressure, suppressed_pressure * 0.5)
        để object bị kìm vẫn còn dấu vết áp lực trong debug, nhưng không tự động
        biến thành quyền nói.
        """

        return clamp01(max(self.surface_pressure, self.suppressed_pressure * 0.5))

    @property
    def novelty(self) -> float:
        """Alias nhẹ cho SurfaceArbiter.

        Phase 0 chưa có model novelty riêng, nên dùng confidence làm proxy yếu.
        """

        return self.confidence

    @property
    def topic_seed(self) -> str:
        """Seed ngắn cho tầng surface/thought_stream."""

        return self.dominant_theme

    @property
    def partial_text(self) -> str:
        """Nội dung tóm tắt để SurfaceArbiter có payload thật, không phải nhãn rỗng."""

        return self.summary

    @property
    def public_seed(self) -> str:
        """Seed public-safe tối thiểu.

        Chỉ trả summary khi route là surface_candidate. Route khác không nên vô
        tình trồi thành câu nói.
        """

        return self.summary if self.should_surface else ""

    def to_dict(self) -> dict[str, object]:
        """Serialize đầy đủ cho debug/dev panel."""

        return {
            "id": self.id,
            "dominant_theme": self.dominant_theme,
            "fragments": list(self.fragments),
            "summary": self.summary,
            "confidence": round(float(self.confidence), 3),
            "surface_pressure": round(float(self.surface_pressure), 3),
            "suppressed_pressure": round(float(self.suppressed_pressure), 3),
            "evidence_ids": list(self.evidence_ids),
            "recommended_route": self.recommended_route,
            "privacy_level": self.privacy_level,
            "tags": list(self.tags),
            "created_at": round(float(self.created_at), 3),
            # Các key tương thích với SurfaceArbiter/thought_surface cũ.
            "topic_seed": self.topic_seed,
            "partial_text": self.partial_text,
            "public_seed": self.public_seed,
            "pressure": round(float(self.pressure), 3),
            "momentum_score": round(float(self.momentum_score), 3),
            "novelty": round(float(self.novelty), 3),
            "impulse_kind": self._surface_impulse_kind(),
            "root_source": "wandering" if "wandering" in self.tags else "subconscious_synthesizer",
            "action_opportunity": "wandering_surface_candidate" if "wandering" in self.tags and self.should_surface else self.recommended_route,
        }

    def to_surface_seed(self) -> dict[str, object]:
        """Payload nhỏ để gắn vào frame.subconscious_seed hoặc thought_stream.

        SurfaceArbiter hiện đọc các key như topic_seed/partial_text/pressure.
        Method này tránh phải sửa rộng pipeline ngay trong Phase 0.
        """

        return {
            "topic_seed": self.topic_seed,
            "partial_text": self.partial_text,
            "public_seed": self.public_seed,
            "pressure": self.surface_pressure,
            "momentum_score": self.momentum_score,
            "novelty": self.novelty,
            "evidence": list(self.evidence_ids),
            "recommended_route": self.recommended_route,
            "impulse_kind": self._surface_impulse_kind(),
            "root_source": "wandering" if "wandering" in self.tags else "subconscious_synthesizer",
            "action_opportunity": "wandering_surface_candidate" if "wandering" in self.tags and self.should_surface else self.recommended_route,
            "surface_allowed": self.should_surface,
        }

    def _surface_impulse_kind(self) -> str:
        """Tách source surface cho SurfaceArbiter.

        Wandering thought có lane riêng để policy/debug không nhầm nó với status ping
        hay thought_stream_surface cũ. Một cái tên khác, ít nhất đỡ tự bắn vào chân.
        """

        if "wandering" in self.tags:
            return "wandering_surface_candidate"
        if "memory" in self.tags:
            return "memory_resonance"
        return "inner_whim"

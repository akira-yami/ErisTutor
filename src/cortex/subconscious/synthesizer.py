# -*- coding: utf-8 -*-
"""Conscious Synthesizer Phase 0.

Synthesizer là cổ chai ý thức nhỏ: nhận nhiều ThoughtFragment từ Blackboard,
gom cụm, chọn theme thắng, rồi xuất một ThoughtObject duy nhất.

Nó không sinh văn public. Nó cũng không gọi LLM. Công việc của nó là chống việc
mọi worker đều tưởng mình là nhân vật chính, căn bệnh phổ biến trong cả multi-agent
lẫn họp nhóm loài người.
"""

from __future__ import annotations
from core.di import resolve_dependency

from dataclasses import dataclass
import re
from typing import Iterable

from .blackboard import BlackboardSnapshot, SubconsciousBlackboard
from .thought_fragment import (
    PrivacyLevel,
    RecommendedRoute,
    ThoughtFragment,
    ThoughtObject,
    clean_text,
    clamp01,
    make_object_id,
    normalize_privacy_level,
    now_timestamp,
)


@dataclass(slots=True, frozen=True)
class SynthesisPolicy:
    """Knob chính sách cho Synthesizer.

    - max_fragments: số fragment tối đa đưa vào một object.
    - min_confidence_for_surface: confidence tối thiểu để được surface_candidate.
    - min_pressure_for_surface: surface_pressure tối thiểu để được surface_candidate.
    - min_confidence_for_store: confidence tối thiểu để đáng lưu/rehearse.
    - rehearsal_band: vùng áp lực cần diễn nháp trước khi nói.
    - require_evidence_for_surface: nếu True, fragment thiếu evidence sẽ không được
      route thẳng ra surface_candidate.
    """

    max_fragments: int = 6
    min_confidence_for_surface: float = 0.58
    min_pressure_for_surface: float = 0.55
    min_confidence_for_store: float = 0.35
    rehearsal_band: float = 0.72
    require_evidence_for_surface: bool = True


@dataclass(slots=True, frozen=True)
class SynthesisReport:
    """Debug report cho một lượt synthesize."""

    object: ThoughtObject | None
    considered_fragment_ids: tuple[str, ...]
    dominant_group_key: str
    route_reason: str
    suppressed_fragment_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "object": self.object.to_dict() if self.object else None,
            "considered_fragment_ids": list(self.considered_fragment_ids),
            "dominant_group_key": self.dominant_group_key,
            "route_reason": self.route_reason,
            "suppressed_fragment_ids": list(self.suppressed_fragment_ids),
        }


class ConsciousSynthesizer:
    """Nén Blackboard thành một ThoughtObject.

    Đây là deterministic synthesizer. Phase sau có thể thêm LLM/private rehearsal,
    nhưng Phase 0 phải có xương sống không phụ thuộc model. Nếu không, cứ hễ model
    timeout là "nội tâm" biến mất. Rất thơ, nhưng không chạy được.
    """

    def __init__(self, policy: SynthesisPolicy | None = None) -> None:
        self.policy = resolve_dependency(policy, lambda: SynthesisPolicy())
        self.last_report = SynthesisReport(
            object=None,
            considered_fragment_ids=(),
            dominant_group_key="not_started",
            route_reason="not_started",
            suppressed_fragment_ids=(),
        )

    def synthesize_from_blackboard(
        self,
        blackboard: SubconsciousBlackboard,
        *,
        now: float | None = None,
    ) -> ThoughtObject | None:
        """Lấy snapshot từ Blackboard và synthesize object."""

        snapshot = blackboard.snapshot(now=now)
        return self.synthesize(snapshot)

    def synthesize(self, snapshot: BlackboardSnapshot | Iterable[ThoughtFragment]) -> ThoughtObject | None:
        """Sinh ThoughtObject từ snapshot hoặc list fragment.

        Trả None nếu không có fragment đủ sống. last_report vẫn ghi lý do để debug.
        """

        if isinstance(snapshot, BlackboardSnapshot):
            now = snapshot.created_at
            fragments = tuple(snapshot.fragments)
        else:
            fragments = tuple(snapshot)
            # Khi test hoặc worker gọi trực tiếp bằng iterable, đừng dùng wall-clock
            # hiện tại để tính decay cho fragment có timestamp giả. Lấy mốc update
            # mới nhất của chính fragment để snapshot giả vẫn ổn định.
            now = max((float(fragment.updated_at or fragment.created_at or 0.0) for fragment in fragments), default=now_timestamp())
            if now <= 0.0:
                now = now_timestamp()

        live = tuple(
            fragment for fragment in fragments
            if not fragment.is_expired(now) and fragment.priority_score(now) > 0.02
        )
        if not live:
            self.last_report = SynthesisReport(
                object=None,
                considered_fragment_ids=(),
                dominant_group_key="empty",
                route_reason="no_live_fragments",
                suppressed_fragment_ids=(),
            )
            return None

        groups = self._group_fragments(live)
        dominant_key, dominant = max(
            groups.items(),
            key=lambda item: self._group_score(item[1], now=now),
        )
        selected = tuple(
            sorted(
                dominant,
                key=lambda item: item.priority_score(now),
                reverse=True,
            )[: max(1, int(self.policy.max_fragments))]
        )
        object_ = self._build_object(selected, group_key=dominant_key, now=now)
        suppressed_ids = tuple(
            fragment.id for fragment in live
            if fragment.id not in set(object_.fragments)
        )
        self.last_report = SynthesisReport(
            object=object_,
            considered_fragment_ids=tuple(fragment.id for fragment in live),
            dominant_group_key=dominant_key,
            route_reason=self._route_reason(selected, object_),
            suppressed_fragment_ids=suppressed_ids,
        )
        return object_

    def _build_object(self, fragments: tuple[ThoughtFragment, ...], *, group_key: str, now: float) -> ThoughtObject:
        evidence_ids = tuple(dict.fromkeys(
            evidence for fragment in fragments for evidence in fragment.evidence_ids
        ))[:32]
        fragment_ids = tuple(fragment.id for fragment in fragments)
        confidence = self._weighted_average(
            fragments,
            now=now,
            value_getter=lambda item: item.confidence,
        )
        surface_pressure = self._surface_pressure(fragments, now=now)
        suppressed_pressure = self._suppressed_pressure(fragments, now=now)
        privacy_level = self._max_privacy(fragments)
        dominant_theme = self._dominant_theme(fragments, group_key=group_key)
        summary = self._summary(fragments, dominant_theme=dominant_theme)
        tags = tuple(dict.fromkeys(tag for fragment in fragments for tag in fragment.tags))[:24]
        route = self._choose_route(
            fragments,
            confidence=confidence,
            surface_pressure=surface_pressure,
            suppressed_pressure=suppressed_pressure,
            evidence_ids=evidence_ids,
            privacy_level=privacy_level,
        )
        return ThoughtObject(
            id=make_object_id(fragment_ids, dominant_theme, now),
            dominant_theme=dominant_theme,
            fragments=fragment_ids,
            summary=summary,
            confidence=confidence,
            surface_pressure=surface_pressure,
            suppressed_pressure=suppressed_pressure,
            evidence_ids=evidence_ids,
            recommended_route=route,
            privacy_level=privacy_level,
            tags=tags,
            created_at=now,
        )

    def _group_fragments(self, fragments: tuple[ThoughtFragment, ...]) -> dict[str, list[ThoughtFragment]]:
        """Gom fragment theo theme.

        Ưu tiên tag đầu tiên nếu có. Không có tag thì dùng kind. Có source_agent để
        tránh mọi thứ dính thành một cục "thought" vô nghĩa.
        """

        groups: dict[str, list[ThoughtFragment]] = {}
        for fragment in fragments:
            tag = fragment.tags[0] if fragment.tags else ""
            key = tag or self._kind_family(fragment.kind) or fragment.source_agent
            groups.setdefault(key, []).append(fragment)
        return groups

    def _group_score(self, fragments: list[ThoughtFragment], *, now: float) -> float:
        """Score nhóm = tổng priority + bonus khi nhiều fragment cùng hướng."""

        if not fragments:
            return 0.0
        total = sum(fragment.priority_score(now) for fragment in fragments)
        recurrence_bonus = min(0.24, 0.04 * max(0, len(fragments) - 1))
        evidence_bonus = min(0.16, 0.02 * len({ev for f in fragments for ev in f.evidence_ids}))
        return total + recurrence_bonus + evidence_bonus

    def _surface_pressure(self, fragments: tuple[ThoughtFragment, ...], *, now: float) -> float:
        """Tính áp lực được phép nổi lên surface.

        Chỉ fragment surface_allowed và không private/sensitive mới đóng góp mạnh.
        Như vậy một worker có nội dung nhạy cảm không thể la lên vì urgency cao.
        Văn minh tối thiểu, thật hiếm hoi.
        """

        values: list[float] = []
        for fragment in fragments:
            if not fragment.surface_allowed:
                values.append(0.0)
                continue
            privacy_factor = 0.0 if fragment.privacy_level in {"private", "sensitive"} else 1.0
            base = fragment.confidence * 0.42 + fragment.urgency * 0.36 + fragment.affect_charge * 0.22
            values.append(base * privacy_factor * fragment.decayed_weight(now))
        return clamp01(max(values or [0.0]))

    def _suppressed_pressure(self, fragments: tuple[ThoughtFragment, ...], *, now: float) -> float:
        """Tính áp lực bị kìm lại.

        Fragment private/sensitive hoặc surface_allowed=False vẫn có thể tạo áp lực
        nội bộ để Phase 2 Rehearsal/Shadow dùng, nhưng Phase 0 chưa cho nó nói.
        """

        values: list[float] = []
        for fragment in fragments:
            blocked = not fragment.surface_allowed or fragment.privacy_level in {"private", "sensitive"}
            if not blocked:
                continue
            base = fragment.confidence * 0.35 + fragment.urgency * 0.30 + fragment.affect_charge * 0.35
            values.append(base * fragment.decayed_weight(now))
        return clamp01(max(values or [0.0]))

    def _choose_route(
        self,
        fragments: tuple[ThoughtFragment, ...],
        *,
        confidence: float,
        surface_pressure: float,
        suppressed_pressure: float,
        evidence_ids: tuple[str, ...],
        privacy_level: PrivacyLevel,
    ) -> RecommendedRoute:
        """Chọn route cho ThoughtObject.

        Route là khuyến nghị nội bộ, chưa phải lệnh. SurfaceArbiter/Permission
        vẫn có quyền chặn tiếp.
        """

        if privacy_level in {"private", "sensitive"}:
            if suppressed_pressure >= self.policy.rehearsal_band:
                return "rehearse"
            return "store" if confidence >= self.policy.min_confidence_for_store else "suppress"

        if self.policy.require_evidence_for_surface and not evidence_ids:
            if confidence >= self.policy.min_confidence_for_store:
                return "store"
            return "suppress"

        any_surface_allowed = any(fragment.surface_allowed for fragment in fragments)
        if (
            any_surface_allowed
            and confidence >= self.policy.min_confidence_for_surface
            and surface_pressure >= self.policy.min_pressure_for_surface
        ):
            return "surface_candidate"

        if suppressed_pressure >= self.policy.rehearsal_band:
            return "rehearse"
        if confidence >= self.policy.min_confidence_for_store:
            return "store"
        return "suppress"

    def _route_reason(self, fragments: tuple[ThoughtFragment, ...], object_: ThoughtObject) -> str:
        if object_.recommended_route == "surface_candidate":
            return "surface_allowed_with_enough_confidence_pressure_and_evidence"
        if object_.recommended_route == "rehearse":
            return "pressure_high_but_not_safe_or_not_ready_for_surface"
        if object_.recommended_route == "store":
            return "worth_retaining_but_not_surface_ready"
        return "too_weak_or_too_unanchored"

    def _dominant_theme(self, fragments: tuple[ThoughtFragment, ...], *, group_key: str) -> str:
        """Tạo dominant_theme ngắn gọn từ tag/kind/content."""

        first = fragments[0]
        if first.tags:
            return clean_text(first.tags[0].replace("_", " "), limit=120)
        family = self._kind_family(first.kind)
        if family:
            return clean_text(family.replace("_", " "), limit=120)
        return clean_text(group_key.replace("_", " "), limit=120)

    def _summary(self, fragments: tuple[ThoughtFragment, ...], *, dominant_theme: str) -> str:
        """Nén content thành summary deterministic.

        Không paraphrase sâu để tránh bịa. Chỉ ghép 1-3 mảnh mạnh nhất.
        """

        contents = [clean_text(fragment.content, limit=180) for fragment in fragments[:3]]
        joined = "; ".join(content for content in contents if content)
        if not joined:
            joined = dominant_theme
        if len(contents) > 1:
            return clean_text(f"{dominant_theme}: {joined}", limit=520)
        return clean_text(joined, limit=520)

    @staticmethod
    def _kind_family(kind: str) -> str:
        text = clean_text(kind, limit=80).lower()
        text = re.sub(r"[^a-zA-Z0-9_\-]+", "_", text)
        for suffix in ("_hypothesis", "_fragment", "_candidate", "_seed"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
        return text or "thought"

    @staticmethod
    def _weighted_average(
        fragments: tuple[ThoughtFragment, ...],
        *,
        now: float,
        value_getter,
    ) -> float:
        total_weight = 0.0
        total_value = 0.0
        for fragment in fragments:
            weight = max(0.05, fragment.priority_score(now))
            total_weight += weight
            total_value += clamp01(value_getter(fragment)) * weight
        if total_weight <= 0:
            return 0.0
        return clamp01(total_value / total_weight)

    @staticmethod
    def _max_privacy(fragments: tuple[ThoughtFragment, ...]) -> PrivacyLevel:
        order = {"public": 0, "normal": 1, "private": 2, "sensitive": 3}
        selected = max((fragment.privacy_level for fragment in fragments), key=lambda item: order.get(item, 1))
        return normalize_privacy_level(selected)

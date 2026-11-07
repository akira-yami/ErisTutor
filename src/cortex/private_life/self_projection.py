# -*- coding: utf-8 -*-
"""Chiếu Subject Self + PrivateMind concern thành đời sống riêng.

Điểm mấu chốt của V20: PrivateLife không còn được phép tự có sẵn "ám ảnh",
"gu" hay "dự án riêng" chỉ vì tác giả thấy chúng hợp nhân vật. Nó chỉ nhận các
driver đã có provenance từ Subject Core hoặc concern lặp lại có evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import PrivateAnchorKind, clamp01, compact_text, stable_id


@dataclass(frozen=True, slots=True)
class PrivateLifeDriver:
    driver_id: str
    mode: str  # anchor | project
    anchor_kind: PrivateAnchorKind
    title: str
    question: str
    stance: str
    pressure: float
    confidence: float
    stability: float
    evidence_ids: tuple[str, ...]
    source_refs: tuple[str, ...]
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pressure", clamp01(self.pressure))
        object.__setattr__(self, "confidence", clamp01(self.confidence))
        object.__setattr__(self, "stability", clamp01(self.stability))
        object.__setattr__(self, "title", compact_text(self.title, 180))
        object.__setattr__(self, "question", compact_text(self.question, 520))
        object.__setattr__(self, "stance", compact_text(self.stance, 520))
        object.__setattr__(self, "evidence_ids", tuple(dict.fromkeys(str(x) for x in self.evidence_ids if str(x).strip()))[:20])
        object.__setattr__(self, "source_refs", tuple(dict.fromkeys(str(x) for x in self.source_refs if str(x).strip()))[:20])
        object.__setattr__(self, "tags", tuple(dict.fromkeys(str(x) for x in self.tags if str(x).strip()))[:12])


class PrivateLifeSelfProjector:
    """Biến self-model thành driver riêng mà không invent thêm trait."""

    def derive(
        self,
        *,
        subject_self: Mapping[str, Any] | None,
        private_mind_digest: Mapping[str, Any] | None = None,
    ) -> tuple[PrivateLifeDriver, ...]:
        drivers: list[PrivateLifeDriver] = []
        self_payload = subject_self if isinstance(subject_self, Mapping) else {}
        facets = self_payload.get("facets", ())
        if not isinstance(facets, Sequence) or isinstance(facets, (str, bytes)):
            facets = ()

        for raw in facets:
            if not isinstance(raw, Mapping):
                continue
            facet_id = str(raw.get("facet_id") or "").strip()
            kind = str(raw.get("kind") or "").strip()
            statement = compact_text(raw.get("statement", ""), 440)
            evidence = tuple(str(x) for x in raw.get("evidence_refs", ()) if str(x).strip())
            sources = tuple(str(x) for x in raw.get("source_refs", ()) if str(x).strip())
            confidence = clamp01(raw.get("confidence", 0.0))
            stability = clamp01(raw.get("stability", 0.0))
            pressure = clamp01(raw.get("pressure", 0.0))
            polarity = int(raw.get("polarity", 0) or 0)
            if not facet_id or not statement:
                continue
            # PrivateLife chỉ được tăng trưởng từ facet có chuỗi bằng chứng thật.
            # source_refs đơn độc không đủ vì vài projection kỹ thuật là derivation
            # không có causal refs hạt mịn.
            if not evidence:
                continue

            anchor_kind: PrivateAnchorKind
            question: str
            stance: str
            title: str
            tags = ("self_owned", kind)
            if kind == "continuity":
                anchor_kind = "identity"
                title = "Self continuity"
                question = "Điều gì trong lịch sử hiện tại đang giữ hoặc làm rạn tính liên tục của mình?"
                stance = statement
            elif kind == "reflective_mismatch":
                anchor_kind = "obsession"
                title = "Narrative mismatch"
                question = "Cách mình giải thích bản thân đang sai ở đâu so với hành vi thật?"
                stance = statement
            elif kind == "competence":
                anchor_kind = "private_agenda"
                title = "Competence boundary"
                question = "Giới hạn năng lực này cần được kiểm lại bằng kết quả nào thay vì tự mô tả?"
                stance = statement
            elif kind == "derived_need":
                anchor_kind = "private_agenda"
                title = "Derived need"
                question = "Nhu cầu dẫn xuất này có tiếp tục dự đoán lựa chọn qua nhiều ngữ cảnh không?"
                stance = statement
            elif kind == "value":
                anchor_kind = "identity"
                title = "Behavior-backed value"
                question = "Giá trị này có còn được hành vi thực tế nâng đỡ khi phải trả giá không?"
                stance = statement
            elif kind == "narrative":
                anchor_kind = "identity"
                title = "Self interpretation"
                question = "Cách tự diễn giải này có tiếp tục dự đoán được hành vi sau không?"
                stance = statement
            else:
                # Các facet kỹ thuật khác không tự động thành "đời sống riêng".
                continue

            anchor_pressure = clamp01(0.55 * pressure + 0.25 * confidence + 0.20 * stability)
            drivers.append(
                PrivateLifeDriver(
                    driver_id=stable_id("driver", "anchor", facet_id),
                    mode="anchor",
                    anchor_kind=anchor_kind,
                    title=title,
                    question=question,
                    stance=stance,
                    pressure=anchor_pressure,
                    confidence=confidence,
                    stability=stability,
                    evidence_ids=evidence,
                    source_refs=(facet_id, *sources),
                    tags=tags,
                )
            )

            # Project là thứ đòi hỏi tiếp tục xử lý, nên chỉ tạo khi có tension,
            # giới hạn hay need đủ áp lực. Value ổn định không biến thành to-do list.
            make_project = (
                kind == "reflective_mismatch"
                or (kind == "continuity" and pressure >= 0.28)
                or (kind == "competence" and polarity < 0 and pressure >= 0.36)
                or (kind == "derived_need" and pressure >= 0.48)
            )
            if make_project:
                drivers.append(
                    PrivateLifeDriver(
                        driver_id=stable_id("driver", "project", facet_id),
                        mode="project",
                        anchor_kind=anchor_kind,
                        title=f"Theo dõi: {title}",
                        question=question,
                        stance=stance,
                        pressure=clamp01(max(pressure, anchor_pressure)),
                        confidence=confidence,
                        stability=stability,
                        evidence_ids=evidence,
                        source_refs=(facet_id, *sources),
                        tags=("self_owned_project", kind),
                    )
                )

        # Learned Agency V24: competence gap đã đo được có thể trở thành private
        # practice project. Không có gap/trial/evidence thì không tạo project.
        agency = self_payload.get("learned_agency", {})
        if isinstance(agency, Mapping):
            gaps = agency.get("gaps", ())
            if isinstance(gaps, Sequence) and not isinstance(gaps, (str, bytes)):
                for raw_gap in gaps:
                    if not isinstance(raw_gap, Mapping):
                        continue
                    skill = compact_text(raw_gap.get("skill_id", ""), 120)
                    context = compact_text(raw_gap.get("context_key", "general"), 120)
                    pressure = clamp01(raw_gap.get("pressure", 0.0))
                    trials = max(0, int(raw_gap.get("trial_count") or 0))
                    evidence = tuple(str(x) for x in raw_gap.get("evidence_refs", ()) if str(x).strip())
                    if not skill or trials < 3 or pressure < 0.20 or not evidence:
                        continue
                    target = clamp01(raw_gap.get("target_level", 0.68))
                    current = clamp01(raw_gap.get("current_level", 0.5))
                    drivers.append(
                        PrivateLifeDriver(
                            driver_id=stable_id("driver", "practice", skill, context),
                            mode="project",
                            anchor_kind="private_agenda",
                            title=f"Practice: {skill}",
                            question=f"Làm sao nâng năng lực {skill} trong {context} từ {current:.2f} về phía {target:.2f} bằng task có kết quả ngoài?",
                            stance="Đây là khoảng cách năng lực đo được; ưu tiên luyện và kiểm tra transfer, không tự phong đã giỏi.",
                            pressure=pressure,
                            confidence=clamp01(min(1.0, trials / 8.0)),
                            stability=clamp01(min(1.0, trials / 10.0)),
                            evidence_ids=evidence,
                            source_refs=(f"competence_gap:{skill}:{context}",),
                            tags=("learned_agency", "practice", skill),
                        )
                    )

        # Concern của PrivateMind chỉ được "thăng cấp" thành đời sống riêng khi nó
        # lặp lại, có áp lực và có nhiều evidence refs. Một câu model nghĩ thoáng qua
        # không được phong làm ám ảnh dài hạn. Có chút tiêu chuẩn cũng đỡ khổ database.
        digest = private_mind_digest if isinstance(private_mind_digest, Mapping) else {}
        retained = digest.get("retained", {}) if isinstance(digest.get("retained", {}), Mapping) else {}
        concerns = retained.get("concerns", digest.get("concerns", ()))
        if isinstance(concerns, Sequence) and not isinstance(concerns, (str, bytes)):
            for concern in concerns:
                if not isinstance(concern, Mapping):
                    continue
                if str(concern.get("status") or "") not in {"open", "watching", "blocked"}:
                    continue
                recurrence = max(0, int(concern.get("recurrence") or 0))
                pressure = clamp01(concern.get("pressure", 0.0))
                evidence = tuple(str(x) for x in concern.get("evidence_ids", ()) if str(x).strip())
                theme = compact_text(concern.get("theme", ""), 360)
                concern_id = str(concern.get("id") or stable_id("concern", theme))
                if recurrence < 3 or pressure < 0.42 or len(evidence) < 2 or not theme:
                    continue
                question = "Mối bận tâm lặp lại này cần bằng chứng mới hoặc một hành động kiểm tra nào để tiến triển?"
                drivers.extend(
                    (
                        PrivateLifeDriver(
                            driver_id=stable_id("driver", "concern-anchor", concern_id),
                            mode="anchor",
                            anchor_kind="private_agenda",
                            title="Recurring private concern",
                            question=question,
                            stance=theme,
                            pressure=pressure,
                            confidence=clamp01(0.40 + 0.08 * min(5, recurrence)),
                            stability=clamp01(recurrence / 8.0),
                            evidence_ids=evidence,
                            source_refs=(concern_id,),
                            tags=("private_mind", "recurring_concern"),
                        ),
                        PrivateLifeDriver(
                            driver_id=stable_id("driver", "concern-project", concern_id),
                            mode="project",
                            anchor_kind="private_agenda",
                            title="Điều tra concern lặp lại",
                            question=question,
                            stance=theme,
                            pressure=pressure,
                            confidence=clamp01(0.40 + 0.08 * min(5, recurrence)),
                            stability=clamp01(recurrence / 8.0),
                            evidence_ids=evidence,
                            source_refs=(concern_id,),
                            tags=("private_mind", "recurring_concern"),
                        ),
                    )
                )

        dedup = {item.driver_id: item for item in drivers}
        return tuple(sorted(dedup.values(), key=lambda item: item.driver_id))


__all__ = ["PrivateLifeDriver", "PrivateLifeSelfProjector"]

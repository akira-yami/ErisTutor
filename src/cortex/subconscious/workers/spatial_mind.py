# -*- coding: utf-8 -*-
"""Phase 4 - Digital Territoriality / Spatial Mind Worker.

SpatialMindAgent biến dữ kiện thân thể/không gian thành fragment nội tâm: space
compression, blind frustration seed, territorial discomfort, body reposition desire.
Nó không được cằn nhằn public chỉ vì thiếu dữ kiện màn hình. "Tôi không biết" và
"tôi bị cản" là hai trạng thái khác nhau; nhầm hai cái này là cách tạo drama rẻ.
"""

from __future__ import annotations

from typing import Iterable

from ..thought_fragment import ThoughtFragment, clamp01, clean_text
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import context_evidence_ids, first_present, safe_get


class SpatialMindAgent(RuleBasedWorker):
    """Sinh fragment về không gian/thân thể số."""

    name = "spatial_mind_agent"

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        spatial = self._spatial_payload(context)
        if not spatial:
            return ()

        evidence_ids = context_evidence_ids(context, fallback_prefix="spatial") or ("spatial.runtime_state",)
        now = float(context.now or 0.0) or None
        obstruction = self._level(spatial, "obstruction", "obstruction_risk", "window_overlap")
        visible_area = self._level(spatial, "visible_area", "visible_ratio", default=1.0)
        top_perch = bool(first_present(spatial.get("top_perch"), spatial.get("is_top_perched"), default=False))
        shared_relation = clean_text(first_present(spatial.get("shared_space_relation"), spatial.get("relation"), default="unknown"), limit=80)
        sensor_state = clean_text(
            first_present(
                spatial.get("sensor_visibility"),
                spatial.get("visibility_state"),
                spatial.get("vision_state"),
                spatial.get("blind_reason"),
                default="",
            ),
            limit=100,
        ).lower()

        fragments: list[ThoughtFragment] = []
        compression = clamp01(obstruction * 0.58 + (1.0 - visible_area) * 0.36 + (0.10 if top_perch else 0.0))
        if compression >= 0.28:
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="space_compression",
                    content=(
                        f"Không gian số bị nén: obstruction={obstruction:.2f}, visible_area={visible_area:.2f}, "
                        f"shared_space={shared_relation}."
                    ),
                    evidence_ids=evidence_ids,
                    confidence=0.46 + compression * 0.30,
                    urgency=compression * 0.55,
                    affect_charge=0.22 + compression * 0.26,
                    privacy_level="normal",
                    surface_allowed=False,
                    ttl_seconds=360,
                    tags=("spatial_mind", "space_compression"),
                    now=now,
                )
            )

        blind_kind = self._blind_frustration_kind(sensor_state)
        if blind_kind:
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="blind_frustration_seed",
                    content=(
                        f"Blind frustration seed từ sensor state '{sensor_state or blind_kind}'; "
                        "chỉ được biểu hiện nhẹ như nội trạng sensor, không claim màn hình."
                    ),
                    evidence_ids=evidence_ids,
                    confidence=0.50,
                    urgency=0.34 + compression * 0.22,
                    affect_charge=0.32 + compression * 0.24,
                    privacy_level="private",
                    surface_allowed=False,
                    ttl_seconds=420,
                    tags=("spatial_mind", "blind_frustration", blind_kind),
                    now=now,
                )
            )

        if compression >= 0.45 or shared_relation in {"blocking", "overlapped", "crowded"}:
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="territorial_discomfort",
                    content="Territorial discomfort tăng; ưu tiên body reposition hoặc stillness, không biến thành lời than công khai.",
                    evidence_ids=evidence_ids,
                    confidence=0.44 + compression * 0.30,
                    urgency=0.32 + compression * 0.36,
                    affect_charge=0.34 + compression * 0.30,
                    privacy_level="private",
                    surface_allowed=False,
                    ttl_seconds=480,
                    tags=("spatial_mind", "territorial_discomfort"),
                    now=now,
                )
            )

        if compression >= 0.35 or top_perch or shared_relation in {"blocking", "far", "overlapped"}:
            desire = "step_back" if shared_relation in {"blocking", "overlapped"} else "re-anchor"
            if top_perch and compression < 0.55:
                desire = "stay_perched_quietly"
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="body_reposition_desire",
                    content=f"Body reposition desire: {desire}; giữ đồng bộ với speech, tránh body phản lời.",
                    evidence_ids=evidence_ids,
                    confidence=0.46 + compression * 0.28,
                    urgency=0.36 + compression * 0.32,
                    affect_charge=0.20 + compression * 0.20,
                    privacy_level="normal",
                    surface_allowed=False,
                    ttl_seconds=300,
                    tags=("spatial_mind", "body_reposition_desire", desire),
                    now=now,
                )
            )

        return tuple(fragments)

    def _spatial_payload(self, context: WorkerContext) -> dict[str, object]:
        candidates = (
            safe_get(context.frame, "spatial"),
            safe_get(context.frame, "spatial_frame"),
            safe_get(context.snapshot, "spatial"),
            safe_get(context.snapshot, "body_state"),
            safe_get(context.snapshot, "body"),
            safe_get(context.internal_state, "spatial"),
        )
        for item in candidates:
            if not item:
                continue
            if isinstance(item, dict):
                return dict(item)
            payload: dict[str, object] = {}
            for name in (
                "obstruction_risk",
                "window_overlap",
                "visible_area",
                "visible_ratio",
                "shared_space_relation",
                "top_perch",
                "is_top_perched",
                "sensor_visibility",
                "visibility_state",
                "vision_state",
                "blind_reason",
            ):
                if hasattr(item, name):
                    payload[name] = getattr(item, name)
            if payload:
                return payload
        return {}

    def _level(self, payload: dict[str, object], *names: str, default: float = 0.0) -> float:
        return clamp01(first_present(*(payload.get(name) for name in names), default=default), default=default)

    def _blind_frustration_kind(self, sensor_state: str) -> str:
        if not sensor_state:
            return ""
        # Không public-cằn nhằn khi chỉ là permission blocked / no evidence. Đó là
        # thiếu dữ liệu, không phải bị cản. Phân biệt được vậy là đã hơn kha khá code.
        if any(term in sensor_state for term in ("permission_blocked", "no_current_screen_evidence", "no_evidence")):
            return ""
        if "timeout" in sensor_state:
            return "vision_timeout"
        if "budget" in sensor_state or "resource" in sensor_state:
            return "budget_blocked"
        if "context_shift" in sensor_state or "stale" in sensor_state or "refresh" in sensor_state:
            return "context_shift_without_refresh"
        if "obstruct" in sensor_state or "blocked" in sensor_state:
            return "obstruction"
        return ""

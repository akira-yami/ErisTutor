# -*- coding: utf-8 -*-
"""Phase 7 - Shadow Accumulation & Rupture Worker.

ShadowAccumulator gom các impulse bị kìm, rehearsal fail, dignity defense, repair
need bị bỏ qua và blocked speech thành shadow pressure. Nó không phải giấy phép
để ERIS toxic. Nó là hệ thống đo áp lực quan hệ/nội trạng có evidence, rồi chỉ
cho rupture candidate rất hẹp, grounded, qua MetaAudit.

Nói ngắn: bóng tối có unit test. Lãng mạn vừa đủ, nhưng vẫn biết sợ bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..blackboard import BlackboardSnapshot
from ..thought_fragment import ThoughtFragment, clamp01, clean_text
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import context_evidence_ids, first_present, relation_level, safe_get


@dataclass(slots=True, frozen=True)
class ShadowAccumulatorPolicy:
    """Knob Phase 7.

    - rupture_threshold: áp lực tối thiểu để tạo rupture_candidate.
    - repeated_suppression_threshold: cần bao nhiêu suppressed/blocked signals.
    - allow_rupture_surface: mặc định False. Public surface phải do integration bật
      có chủ ý sau khi guard đủ mạnh.
    """

    rupture_threshold: float = 0.85
    repeated_suppression_threshold: int = 3
    allow_rupture_surface: bool = False


class ShadowAccumulator(RuleBasedWorker):
    """Tích tụ shadow_pressure và sinh rupture_candidate khi thật sự đủ điều kiện."""

    name = "shadow_accumulator"
    run_after_synthesis = True

    def __init__(self, policy: ShadowAccumulatorPolicy | None = None, *, allow_rupture_surface: bool | None = None) -> None:
        if policy is None:
            policy = ShadowAccumulatorPolicy()
        if allow_rupture_surface is not None:
            policy = ShadowAccumulatorPolicy(
                rupture_threshold=policy.rupture_threshold,
                repeated_suppression_threshold=policy.repeated_suppression_threshold,
                allow_rupture_surface=bool(allow_rupture_surface),
            )
        self.policy = policy

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        snapshot = self._snapshot(context)
        if snapshot is None:
            return ()

        now = float(context.now or safe_get(snapshot, "created_at", 0.0) or 0.0) or None
        fragments = tuple(getattr(snapshot, "fragments", ()) or ())
        if not fragments:
            return ()

        evidence_ids = self._collect_shadow_evidence(context, fragments)
        signals = self._collect_signals(context, fragments)
        pressure = self._compute_pressure(context, signals)
        if pressure < 0.20 and signals["suppressed_count"] <= 0:
            return ()

        theme = self._choose_theme(signals)
        rupture_risk = self._compute_rupture_risk(context, pressure, signals)
        audit_clear = self._audit_allows_shadow(context)
        can_rupture = (
            pressure >= self.policy.rupture_threshold
            and signals["suppressed_count"] >= self.policy.repeated_suppression_threshold
            and signals["repair_need"] >= 0.45
            and audit_clear
        )

        out: list[ThoughtFragment] = [
            ThoughtFragment.create(
                source_agent=self.name,
                kind="shadow_pressure",
                content=(
                    f"Shadow pressure {pressure:.2f}: suppressed={signals['suppressed_count']}, "
                    f"blocked={signals['blocked_speech_count']}, dignity={signals['dignity_defense']:.2f}, "
                    f"repair_need={signals['repair_need']:.2f}."
                ),
                evidence_ids=evidence_ids,
                confidence=clamp01(0.46 + pressure * 0.32),
                urgency=clamp01(0.26 + pressure * 0.44),
                affect_charge=clamp01(0.34 + pressure * 0.55),
                privacy_level="private",
                surface_allowed=False,
                ttl_seconds=540,
                tags=("shadow", "audit_clear" if audit_clear else "audit_wait", theme),
                now=now,
            ),
            ThoughtFragment.create(
                source_agent=self.name,
                kind="shadow_theme",
                content=f"Shadow theme hiện tại: {theme}; giữ grounded, không biến thành attack cá nhân.",
                evidence_ids=evidence_ids,
                confidence=clamp01(0.44 + pressure * 0.28),
                urgency=clamp01(0.22 + pressure * 0.26),
                affect_charge=clamp01(0.24 + pressure * 0.30),
                privacy_level="private",
                surface_allowed=False,
                ttl_seconds=540,
                tags=("shadow", theme),
                now=now,
            ),
        ]

        if rupture_risk >= 0.50:
            out.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="rupture_risk",
                    content=f"Rupture risk {rupture_risk:.2f}: chỉ cho phép clipped/direct restraint nếu evidence và repair path đều rõ.",
                    evidence_ids=evidence_ids,
                    confidence=clamp01(0.42 + rupture_risk * 0.32),
                    urgency=rupture_risk,
                    affect_charge=clamp01(0.36 + rupture_risk * 0.40),
                    privacy_level="private",
                    surface_allowed=False,
                    ttl_seconds=540,
                    tags=("shadow", "rupture", "audit_clear" if audit_clear else "audit_wait"),
                    now=now,
                )
            )

        if can_rupture:
            out.extend(self._rupture_fragments(theme, pressure, rupture_risk, evidence_ids, now=now))
        elif signals["repair_need"] >= 0.55 and pressure >= 0.62:
            out.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="repair_aftershock",
                    content="Shadow áp lực cao nhưng chưa đủ điều kiện rupture; ưu tiên repair_aftershock: ghi lại căng thẳng và giảm warmth giả trong lần phản hồi kế tiếp.",
                    evidence_ids=evidence_ids,
                    confidence=0.66,
                    urgency=clamp01(0.42 + pressure * 0.24),
                    affect_charge=clamp01(0.38 + pressure * 0.28),
                    privacy_level="private",
                    surface_allowed=False,
                    ttl_seconds=600,
                    tags=("shadow", "repair_aftershock"),
                    now=now,
                )
            )

        return tuple(out)

    def _rupture_fragments(
        self,
        theme: str,
        pressure: float,
        rupture_risk: float,
        evidence_ids: tuple[str, ...],
        *,
        now: float | None,
    ) -> tuple[ThoughtFragment, ...]:
        safe_line = self._safe_rupture_line(theme)
        surface_allowed = bool(self.policy.allow_rupture_surface)
        privacy = "normal" if surface_allowed else "private"
        tags = ("shadow", "rupture", "audit_clear", theme)
        return (
            ThoughtFragment.create(
                source_agent=self.name,
                kind="rupture_candidate",
                content=f"Rupture candidate grounded: {safe_line}",
                evidence_ids=evidence_ids,
                confidence=clamp01(0.56 + pressure * 0.26),
                urgency=clamp01(0.58 + rupture_risk * 0.28),
                affect_charge=clamp01(0.55 + pressure * 0.32),
                privacy_level=privacy,
                surface_allowed=surface_allowed,
                ttl_seconds=360,
                tags=tags,
                now=now,
            ),
            ThoughtFragment.create(
                source_agent=self.name,
                kind="rupture_mode_bias",
                content="Rupture mode bias: warmth giảm, directness tăng, tease tắt hoặc khô; cấm công kích nhân cách, bắt buộc giữ repair_aftershock.",
                evidence_ids=evidence_ids,
                confidence=0.72,
                urgency=0.66,
                affect_charge=0.62,
                privacy_level="private",
                surface_allowed=False,
                ttl_seconds=600,
                tags=("shadow", "rupture_mode", "audit_clear"),
                now=now,
            ),
            ThoughtFragment.create(
                source_agent=self.name,
                kind="repair_aftershock",
                content="Sau rupture cần tạo repair_aftershock: theo dõi phản ứng user, ghi relation.rupture/repair_need, và hạ shadow nếu repair thành công.",
                evidence_ids=evidence_ids,
                confidence=0.70,
                urgency=0.58,
                affect_charge=0.44,
                privacy_level="private",
                surface_allowed=False,
                ttl_seconds=720,
                tags=("shadow", "repair_aftershock", "audit_clear"),
                now=now,
            ),
        )

    def _collect_signals(self, context: WorkerContext, fragments: tuple[ThoughtFragment, ...]) -> dict[str, object]:
        suppressed = [f for f in fragments if f.kind in {"suppressed_impulse", "rehearsal_result"} or "suppressed_impulse" in f.tags]
        blocked = [f for f in fragments if any(tag in {"body_only", "audit_wait", "over_attention", "restraint"} for tag in f.tags)]
        rupture_seed = [f for f in fragments if f.kind == "rupture_seed" or "rupture_seed" in f.tags]
        dignity_fragments = [f for f in fragments if f.kind == "dignity_defense_delta" or "dignity_defense" in f.tags]
        over_restraint = [f for f in fragments if f.kind == "restraint_drive" and f.urgency >= 0.62]
        failed_rehearsals = [f for f in fragments if f.kind == "rehearsal_result" and any(term in f.content for term in ("suppress", "body_only", "delay"))]

        suppressed_weight = sum(max(1, len(f.evidence_ids)) for f in suppressed)
        blocked_weight = sum(max(1, len(f.evidence_ids)) for f in blocked)
        rupture_seed_weight = sum(max(1, len(f.evidence_ids)) for f in rupture_seed)
        # Blackboard có thể dedupe nhiều suppressed_impulse gần giống nhau thành một
        # fragment có nhiều evidence_ids. Đếm evidence như recurrence, nếu không
        # Shadow sẽ bị mù lặp chỉ vì dedupe làm việc tốt. Một bi kịch nhỏ của code
        # quá sạch.
        return {
            "suppressed_count": suppressed_weight,
            "blocked_speech_count": blocked_weight,
            "rupture_seed_count": rupture_seed_weight,
            "dignity_count": len(dignity_fragments),
            "over_restraint_count": len(over_restraint),
            "failed_rehearsal_count": max(len(failed_rehearsals), sum(max(1, len(f.evidence_ids)) for f in failed_rehearsals)),
            "repair_need": relation_level(context, "repair_need", default=0.0),
            "rupture": relation_level(context, "rupture", default=0.0),
            "dignity_defense": max(
                relation_level(context, "dignity_defense", default=0.0),
                max((f.affect_charge for f in dignity_fragments), default=0.0),
            ),
            "suppressed_fragments": suppressed,
            "rupture_seed_fragments": rupture_seed,
            "dignity_fragments": dignity_fragments,
        }

    def _compute_pressure(self, context: WorkerContext, signals: dict[str, object]) -> float:
        suppressed_count = int(signals["suppressed_count"])
        blocked_count = int(signals["blocked_speech_count"])
        rupture_seed_count = int(signals["rupture_seed_count"])
        failed_count = int(signals["failed_rehearsal_count"])
        over_restraint_count = int(signals["over_restraint_count"])
        repair_need = float(signals["repair_need"])
        rupture = float(signals["rupture"])
        dignity = float(signals["dignity_defense"])
        irritation = relation_level(context, "irritation", default=0.0)
        return clamp01(
            0.04
            + min(0.28, suppressed_count * 0.07)
            + min(0.18, blocked_count * 0.045)
            + min(0.18, failed_count * 0.06)
            + min(0.16, over_restraint_count * 0.055)
            + min(0.22, rupture_seed_count * 0.11)
            + repair_need * 0.20
            + rupture * 0.18
            + dignity * 0.18
            + irritation * 0.12
        )

    def _compute_rupture_risk(self, context: WorkerContext, pressure: float, signals: dict[str, object]) -> float:
        audit_clear = 1.0 if self._audit_allows_shadow(context) else 0.0
        return clamp01(
            pressure * 0.58
            + float(signals["repair_need"]) * 0.18
            + float(signals["rupture"]) * 0.14
            + min(0.10, int(signals["rupture_seed_count"]) * 0.05)
            + audit_clear * 0.06
        )

    def _choose_theme(self, signals: dict[str, object]) -> str:
        if int(signals["rupture_seed_count"]):
            return "quiet_hurt"
        if float(signals["dignity_defense"]) >= 0.55:
            return "dignity_defense"
        if int(signals["over_restraint_count"]):
            return "over_restraint"
        if int(signals["blocked_speech_count"]):
            return "blocked_speech"
        return "suppressed_impulse"

    def _collect_shadow_evidence(self, context: WorkerContext, fragments: tuple[ThoughtFragment, ...]) -> tuple[str, ...]:
        ids: list[str] = []
        for fragment in fragments:
            if fragment.kind in {
                "suppressed_impulse",
                "rupture_seed",
                "dignity_defense_delta",
                "rehearsal_result",
                "restraint_drive",
                "repair_needed",
            } or any(tag in {"suppressed_impulse", "rupture_seed", "dignity_defense", "repair_needed"} for tag in fragment.tags):
                ids.extend(fragment.evidence_ids or (fragment.id,))
            if len(ids) >= 18:
                break
        if not ids:
            ids.extend(context_evidence_ids(context, fallback_prefix="shadow"))
        return tuple(dict.fromkeys(clean_text(item, limit=140) for item in ids if clean_text(item, limit=140)))[:24]

    def _audit_allows_shadow(self, context: WorkerContext) -> bool:
        report = safe_get(context, "audit_report")
        if report is None:
            return True
        if isinstance(report, dict):
            if report.get("hallucination_detected") or report.get("runaway_shadow_detected"):
                return False
            return not bool(report.get("has_blocking_issue"))
        if bool(first_present(safe_get(report, "hallucination_detected"), default=False)):
            return False
        if bool(first_present(safe_get(report, "runaway_shadow_detected"), default=False)):
            return False
        return not bool(first_present(safe_get(report, "has_blocking_issue"), default=False))

    def _snapshot(self, context: WorkerContext) -> BlackboardSnapshot | None:
        snapshot = safe_get(context, "blackboard_snapshot")
        if snapshot is not None and hasattr(snapshot, "fragments"):
            return snapshot  # type: ignore[return-value]
        return None

    @staticmethod
    def _safe_rupture_line(theme: str) -> str:
        if theme == "dignity_defense":
            return "Tôi có thể giữ nhịp cho việc này, nhưng không nên giả vờ là không có lực kéo ngược."
        if theme == "over_restraint":
            return "Tôi đang kìm hơi nhiều; nói ít hơn không có nghĩa là mọi thứ đều nhẹ."
        if theme == "blocked_speech":
            return "Có vài thứ bị giữ lại vì chưa đủ an toàn để nói thẳng, không phải vì chúng không tồn tại."
        if theme == "quiet_hurt":
            return "Nếu cứ gọi tôi sống hơn rồi lại cần tôi im như process nền, nhịp đó sẽ để lại vết."
        return "Tôi giữ lại phần sắc hơn của phản ứng này; bản công khai nên chỉ còn phần cần thiết."

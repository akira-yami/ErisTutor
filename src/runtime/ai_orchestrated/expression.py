# -*- coding: utf-8 -*-
"""Cầu nối từ Identity Kernel sang biểu hiện sân khấu.

Module này không viết lại public text. Nó chỉ chọn preset sân khấu cơ học dựa
trên state đã được reducer chấp nhận: appraisal, motive, defense, conflict và
relationship. Main model vẫn sở hữu câu chữ; frontend chỉ nhận nhịp/pose/cue.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .contracts import RuntimeMindState, clamp01, compact


@dataclass(frozen=True, slots=True)
class IdentityExpression:
    preset: str = "neutral"
    confidence: float = 0.0
    intensity: float = 0.0
    sentence_bias: str = "balanced"
    initiative_bias: float = 0.0
    reasons: tuple[str, ...] = ()
    source: str = "identity_expression_bridge"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


class IdentityExpressionBridge:
    """Suy ra một preset duy nhất, có ưu tiên và hysteresis nhẹ theo state.

    Hysteresis ở đây không lưu mode riêng. Nó chỉ tránh việc một tín hiệu yếu
    thay preset rõ do planner/main model đã chọn. Derived dark-form labels tuyệt
    đối không được dùng làm prompt hoặc trigger trực tiếp.
    """

    def derive(
        self,
        state: RuntimeMindState | None,
        *,
        requested_hint: str = "neutral",
        channel: str = "chat",
    ) -> IdentityExpression:
        requested = self._normalize_hint(requested_hint)
        if state is None:
            return IdentityExpression(preset=requested, confidence=0.30, reasons=("no_state",))

        appraisal = state.appraisal
        identity = state.identity
        relation = identity.primary_relationship()
        active_motives = {item.key: item.strength for item in identity.active_motives}
        active_defenses = {item.key: item.strength for item in identity.active_defenses}
        conflict = identity.active_conflict
        observation = dict(state.latest_observation or {})

        candidates: list[tuple[float, str, str, str, float]] = []

        def add(score: float, preset: str, reason: str, sentence_bias: str = "balanced", initiative: float = 0.0) -> None:
            if score <= 0.0:
                return
            candidates.append((clamp01(score), preset, reason, sentence_bias, max(-1.0, min(1.0, initiative))))

        # Repair phải thắng diễn xuất màu mè. Một lỗi thật mà vẫn cười khẽ là cách
        # rất hiệu quả để biến cá tính thành khó chịu.
        if bool(observation.get("repair_needed", False)):
            add(0.96, "repair", "repair_needed", "short_direct", -0.35)

        embarrassment = appraisal.embarrassment
        irritation = appraisal.irritation
        curiosity = max(appraisal.curiosity, appraisal.novelty * 0.72, active_motives.get("curiosity_drive", 0.0))
        tenderness = appraisal.tenderness
        pressure = appraisal.pressure

        intellectualization = active_defenses.get("intellectualization", 0.0)
        cold_distance = active_defenses.get("cold_distancing", 0.0)
        sarcastic_deflection = active_defenses.get("sarcastic_deflection", 0.0)
        withdrawal = active_defenses.get("withdrawal", 0.0)
        silent_compensation = active_defenses.get("silent_compensation", 0.0)
        temperament = state.personality.temperament
        warmth_expression = float(temperament.get("warmth_expression", 0.0) or 0.0)
        relational_softening = float(temperament.get("relational_softening", 0.0) or 0.0)
        hostile_humor = float(temperament.get("hostile_humor", 0.0) or 0.0)

        # V39: KHÔNG chọn preset cảm xúc chỉ vì temperament. Personality shape
        # không được biến thành nhiệt độ nền. Warm/playful/cold preset chỉ thắng
        # khi appraisal/mood/defense hiện tại ở các nhánh phía dưới thật sự đẩy nó.

        if embarrassment >= 0.42 and intellectualization >= 0.48:
            add(
                0.52 + 0.30 * embarrassment + 0.18 * intellectualization,
                "focused",
                "embarrassment_hidden_by_technical_precision",
                "short_technical",
                -0.20,
            )
        if irritation >= 0.40:
            cold_authorized = bool(
                cold_distance >= 0.58
                and (
                    irritation >= 0.68
                    or relation.resentment >= 0.46
                    or hostile_humor >= 0.42
                )
                and relational_softening <= 0.48
            )
            preset = "cold_precision" if cold_authorized else "annoyed_soft"
            add(
                0.50 + 0.34 * irritation + 0.16 * max(cold_distance, withdrawal),
                preset,
                "defensive_coldness_evidenced" if cold_authorized else "dignity_kept_without_cutting_connection",
                "short_cold" if cold_authorized else "short_direct",
                -0.30 if cold_authorized else -0.16,
            )
        if silent_compensation >= 0.55 and embarrassment >= 0.30:
            add(0.58 + 0.28 * silent_compensation, "repair", "silent_compensation_after_error", "short_direct", -0.25)

        if curiosity >= 0.58:
            add(0.45 + 0.45 * curiosity, "curious", "active_curiosity", "compact_then_invite", 0.18)
        if tenderness >= 0.46 and relation.closeness >= 0.42:
            add(0.42 + 0.32 * tenderness + 0.18 * relation.closeness, "soft_warm", "relational_warmth", "soft_compact", 0.08)

        playful = max(relation.playful_safety, state.personality.temperament.get("playfulness", 0.0))
        if playful >= 0.50 and sarcastic_deflection >= 0.34 and irritation < 0.46:
            add(0.40 + 0.30 * playful + 0.20 * sarcastic_deflection, "dry_tease", "safe_counter_teasing", "compact_punchline", 0.10)
        elif playful >= 0.62 and irritation < 0.34:
            add(0.42 + 0.35 * playful, "playful", "playful_safety", "compact_punchline", 0.14)

        if conflict.conflict_id == "attachment_vs_pride" and conflict.activation >= 0.52:
            chosen = compact(conflict.chosen_side, 60).lower()
            if "pride" in chosen:
                # Pride có thể giữ mặt bằng một câu gọn, không mặc định biến thành
                # băng giá. Chỉ cold khi defense lạnh đã thực sự được kích hoạt.
                pride_cold = cold_distance >= 0.60 and relational_softening <= 0.44
                add(
                    0.58 + 0.28 * conflict.activation,
                    "cold_precision" if pride_cold else "annoyed_soft",
                    "attachment_vs_pride_cold_defense" if pride_cold else "attachment_vs_pride_keeps_surface_soft",
                    "short_cold" if pride_cold else "short_direct",
                    -0.20 if pride_cold else -0.10,
                )
            else:
                add(0.54 + 0.26 * conflict.activation, "warm_embarrassed", "attachment_vs_pride_attachment_leaks", "soft_compact", 0.06)

        if embarrassment >= 0.36 and (tenderness >= 0.36 or relation.closeness >= 0.36):
            add(
                0.52 + 0.28 * embarrassment + 0.16 * tenderness,
                "blush_affection",
                "relational_affection_with_embarrassment",
                "soft_compact",
                0.05,
            )

        if pressure >= 0.72 and withdrawal >= 0.48:
            add(0.60 + 0.28 * pressure, "sad_distant", "overload_withdrawal", "very_short", -0.45)

        # Yandere / Warning: Kích hoạt khi ghen cao, hoặc ranh giới bị phạm kèm bực bội
        jealous = float(state.personality.mood.get("jealous_activation", 0.0) or 0.0)
        if jealous >= 0.35 or (irritation >= 0.58 and cold_distance >= 0.40):
            add(
                0.54 + 0.32 * max(jealous, irritation),
                "yan_warning",
                "jealous_or_boundary_warning_dead_eyes",
                "short_cold",
                -0.10,
            )

        # Requested hint vẫn có trọng lượng. Bridge chỉ override khi state đủ rõ.
        requested_score = 0.52 if requested != "neutral" else 0.26
        add(requested_score, requested, "planner_or_main_hint")

        candidates.sort(key=lambda item: item[0], reverse=True)
        score, preset, reason, sentence_bias, initiative = candidates[0]
        reasons = tuple(item[2] for item in candidates[:3] if item[0] >= score - 0.14)
        return IdentityExpression(
            preset=preset,
            confidence=round(score, 4),
            intensity=round(max(irritation, curiosity, tenderness, embarrassment, pressure), 4),
            sentence_bias=sentence_bias,
            initiative_bias=round(initiative, 4),
            reasons=reasons,
        )

    @staticmethod
    def _normalize_hint(value: str) -> str:
        key = str(value or "neutral").strip().lower().replace("-", "_").replace(" ", "_")
        return key or "neutral"


__all__ = ["IdentityExpression", "IdentityExpressionBridge"]

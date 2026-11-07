from __future__ import annotations
from core.di import resolve_dependency

from .micro_contracts import (
    MicroLinePolicyResult,
    RelationalContext,
    RelationalRewritePlan,
    SemanticRepeatResult,
    ValidationIssue,
    clean_family,
    clamp01,
    dedupe_issues,
    normalize_text,
    word_count,
)
from .micro_grounding_guard import MicroGroundingGuard
from .micro_repeat_guard import SemanticRepeatGuard
from .micro_surface_lint import MicroSurfaceLint


class MicroLinePolicy:
    """Orchestrator cho micro-line policy.

    File này giờ chỉ điều phối:
    - line_type/risk/anchor từ speech intent và evidence.
    - surface lint mềm.
    - grounding guard nghiêm ở chỗ cần nghiêm.
    - repeat guard dựa vào semantic_family/fingerprint.
    - relational exposure plan.

    Nó KHÔNG còn tự làm semantic brain. Nếu muốn ERIS "nghĩ tốt lẫn xấu" theo
    nghĩa có residue, bias, lệch nhẹ, awkwardness và cả chỗ chưa trưởng thành,
    thì micro_policy phải biết đứng đúng chỗ. Guard mà làm não thì nhân cách chết
    từ từ, chỉ còn lại cái máy không dám nói gì ngoài câu hợp quy chuẩn.
    """

    OBSERVATION_TYPES = {"micro_observation", "screen_observation"}
    MICRO_TYPES = {
        "micro_presence",
        "ambient_residue",
        "soft_impression",
        "micro_observation",
        "curious_question",
        "tender_checkin",
    }

    def __init__(
        self,
        *,
        repeat_guard: SemanticRepeatGuard | None = None,
        surface_lint: MicroSurfaceLint | None = None,
        grounding_guard: MicroGroundingGuard | None = None,
    ):
        self.repeat_guard = resolve_dependency(repeat_guard, lambda: SemanticRepeatGuard())
        self.surface_lint = resolve_dependency(surface_lint, lambda: MicroSurfaceLint())
        self.grounding_guard = resolve_dependency(grounding_guard, lambda: MicroGroundingGuard())

    def evaluate(
        self,
        *,
        speech_intent: str,
        broadcast=None,
        vision_fact: str = "",
        user_action: str = "",
        session_context: str = "",
        last_lines: tuple[str, ...] = (),
        channel: str = "",
        speech_risk_level: str = "",
        semantic_family: str = "",
        meaning_family: str = "",
        family_source: str = "",
    ) -> MicroLinePolicyResult:
        """Chọn line_type/risk/anchor trước khi sinh hoặc nhận final text.

        semantic_family/meaning_family là đường sạch. Nếu không có, policy chỉ
        dùng fallback keyword như hint yếu, không tự block. Đây là khác biệt lớn
        so với bản cũ.
        """

        intent = str(speech_intent or "no_speech").strip().lower()
        line_type = self._line_type_for_intent(intent)
        signal_score = self.grounding_guard.signal_score(broadcast=broadcast, vision_fact=vision_fact)
        anchor = self.grounding_guard.resolve_anchor(
            line_type=line_type,
            broadcast=broadcast,
            vision_fact=vision_fact,
            user_action=user_action,
            session_context=session_context,
        )
        risk_level = self.grounding_guard.risk_level_for_line(
            line_type=line_type,
            signal_score=signal_score,
            requested=speech_risk_level,
        )
        reasons: list[str] = []

        # Silence là output hợp lệ cho autonomous. Nhưng nếu downstream đã có final
        # text trong khi line_type là silence thì phải chặn.
        if line_type == "silence" and intent != "presence_maintenance":
            reasons.append("no_speech_blocks_final")

        # Observation yếu được hạ thành impression/residue. Không để model tự nâng
        # thành "tôi thấy màn hình..." khi evidence chỉ là telemetry mỏng.
        if line_type == "micro_observation" and not (vision_fact or signal_score >= 0.55):
            line_type = "soft_impression"
            risk_level = "texture" if signal_score < 0.35 else "soft_read"
            anchor = self.grounding_guard.resolve_anchor(
                line_type=line_type,
                broadcast=broadcast,
                vision_fact=vision_fact,
                user_action=user_action,
                session_context=session_context,
            )

        if line_type == "micro_observation" and not anchor.source:
            line_type = "silence"
            reasons.append("micro_observation_requires_anchor")
            reasons.append("no_speech_blocks_final")

        if risk_level == "hard_claim" and signal_score < 0.72:
            risk_level = "soft_read"

        if risk_level == "soft_read" and signal_score < 0.35 and line_type in self.OBSERVATION_TYPES:
            line_type = "soft_impression"
            risk_level = "texture"

        # Repeat hint: chỉ semantic_family đáng tin mới nên chặn ở coordinator qua
        # repeat_guard.check(). Ở evaluate ta chỉ thêm reason mềm nếu thấy family
        # fallback giống gần đây. Không tự biến hint thành silence nữa.
        current_family = self._current_semantic_family(
            semantic_family=semantic_family,
            meaning_family=meaning_family,
            text=vision_fact or str(getattr(broadcast, "content", "") or ""),
        )
        if line_type in {"micro_presence", "ambient_residue", "micro_observation", "soft_impression", "curious_question"}:
            recent_fallback_families = tuple(
                family
                for family in (
                    self.repeat_guard.family_for_text(line)
                    for line in tuple(last_lines or ())[:3]
                )
                if family
            )
            if current_family and current_family in set(recent_fallback_families):
                reasons.append(f"weak_recent_family_hint={current_family}")
                if line_type == "micro_observation":
                    line_type = "silence"
                    reasons.append(f"recent_semantic_family={current_family}")
                    reasons.append("no_speech_blocks_final")

        _ = channel  # Giữ tham số để không phá call-site cũ. Có thể dùng sau.
        _ = family_source
        return MicroLinePolicyResult(
            allowed_line_type=line_type,
            reject_reasons=tuple(dict.fromkeys(reasons)),
            signal_score=signal_score,
            risk_level=risk_level,
            anchor_source=anchor.source,
            anchor_text=anchor.text,
        )

    def validate_text(
        self,
        *,
        allowed_line_type: str,
        text: str,
        speech_risk_level: str = "",
        anchor_source: str = "",
        anchor_text: str = "",
        user_action: str = "",
        session_context: str = "",
        screen_evidence: str = "",
        relational_context: RelationalContext | None = None,
        semantic_family: str = "",
        meaning_family: str = "",
        family_source: str = "",
    ) -> tuple[str, ...]:
        """Public API cũ: trả code lỗi cần hành động.

        Chỉ trả fatal + rewriteable. Soft issue không trả ở đây để tránh
        coordinator cũ coi hint mềm là lý do reject. Muốn log đầy đủ thì dùng
        validate_text_detailed().
        """

        issues = self.validate_text_detailed(
            allowed_line_type=allowed_line_type,
            text=text,
            speech_risk_level=speech_risk_level,
            anchor_source=anchor_source,
            anchor_text=anchor_text,
            user_action=user_action,
            session_context=session_context,
            screen_evidence=screen_evidence,
            relational_context=relational_context,
            semantic_family=semantic_family,
            meaning_family=meaning_family,
            family_source=family_source,
        )
        return tuple(issue.code for issue in issues)

    def validate_text_detailed(
        self,
        *,
        allowed_line_type: str,
        text: str,
        speech_risk_level: str = "",
        anchor_source: str = "",
        anchor_text: str = "",
        user_action: str = "",
        session_context: str = "",
        screen_evidence: str = "",
        relational_context: RelationalContext | None = None,
        semantic_family: str = "",
        meaning_family: str = "",
        family_source: str = "",
    ) -> tuple[ValidationIssue, ...]:
        """Kiểm tra final text với severity chi tiết."""

        cleaned = str(text or "").strip()
        if not cleaned:
            return ()

        line_type = str(allowed_line_type or "").strip().lower()
        risk_level = str(speech_risk_level or "").strip().lower()
        issues: list[ValidationIssue] = []
        issues.extend(self.surface_lint.validate(
            cleaned,
            line_type=line_type,
            risk_level=risk_level,
            anchor_source=anchor_source,
            anchor_text=anchor_text,
            user_action=user_action,
            session_context=session_context,
            screen_evidence=screen_evidence,
            relational_context=relational_context,
        ))
        issues.extend(self.grounding_guard.validate(
            cleaned,
            line_type=line_type,
            risk_level=risk_level,
            anchor_source=anchor_source,
            anchor_text=anchor_text,
            user_action=user_action,
            session_context=session_context,
            screen_evidence=screen_evidence,
        ))

        # Repeat ở đây chỉ tạo issue nếu call-site truyền family trusted và có
        # recent_lines thì coordinator nên gọi repeat_guard.check() riêng. Hàm này
        # không có timestamp/history nên không tự block repeat. Giữ hook để debug.
        _ = (semantic_family, meaning_family, family_source)
        return dedupe_issues(issues)

    # ------------------------------------------------------------------
    # Relational rewrite API cũ
    # ------------------------------------------------------------------

    def build_relational_rewrite_plan(
        self,
        text: str,
        *,
        relational_context: RelationalContext | None = None,
        allowed_line_type: str = "",
    ) -> RelationalRewritePlan:
        return self.surface_lint.build_relational_rewrite_plan(
            text,
            relational_context=relational_context,
            allowed_line_type=allowed_line_type,
        )

    def rewrite_relational_line(
        self,
        text: str,
        *,
        relational_context: RelationalContext | None = None,
        allowed_line_type: str = "",
    ) -> str:
        plan = self.build_relational_rewrite_plan(
            text,
            relational_context=relational_context,
            allowed_line_type=allowed_line_type,
        )
        return plan.seed_line

    def relational_exposure_score(self, text: str) -> float:
        return self.surface_lint.relational_exposure_score(text)

    # ------------------------------------------------------------------
    # Type/risk helpers
    # ------------------------------------------------------------------

    def _line_type_for_intent(self, intent: str) -> str:
        """Map speech intent sang dạng câu nhỏ.

        Đây chỉ là map hình dạng. Meaning chính đã được quyết ở tầng trên.
        """

        if intent in {"", "no_speech"}:
            return "silence"
        if intent == "screen_observation":
            return "screen_observation"
        if intent == "micro_observation":
            return "micro_observation"
        if intent in {"scene_curiosity", "ambient_comment"}:
            return "micro_observation"
        if intent == "curious_question":
            return "curious_question"
        if intent in {"tender_checkin", "relational_checkin"}:
            return "tender_checkin"
        if intent == "guarded_reply":
            return "brief_line"
        if intent in {"ambient_residue", "presence_residue", "quiet_residue", "inner_whim", "random_chatter", "light_muse", "micro_story"}:
            return "ambient_residue"
        if intent in {"indirect_presence", "hesitate", "suppress"}:
            return "micro_presence"
        if intent in {"presence_maintenance", "micro_presence", "social_presence", "re_engagement", "companionship_ping"}:
            return "micro_presence"
        return "brief_line"

    def _current_semantic_family(self, *, semantic_family: str, meaning_family: str, text: str) -> str:
        return (
            clean_family(semantic_family)
            or clean_family(meaning_family)
            or self.repeat_guard.family_for_text(text)
        )

    # ------------------------------------------------------------------
    # Backward-compatible private wrappers
    # ------------------------------------------------------------------

    def _validate_surface_shape(self, *args, **kwargs):
        return self.surface_lint._validate_surface_shape(*args, **kwargs)

    def _validate_grounding(self, text: str, **kwargs):
        return list(self.grounding_guard.validate(text, **kwargs))

    def _validate_claim_risk(self, text: str, *, line_type: str, risk_level: str):
        return [issue for issue in self.grounding_guard.validate(text, line_type=line_type, risk_level=risk_level) if "claim" in issue.code or "evidence" in issue.code]

    def _validate_presence_residue(self, *args, **kwargs):
        return self.surface_lint._validate_presence_residue(*args, **kwargs)

    def _validate_relational_permission(self, text: str, *, relational_context: RelationalContext | None):
        return self.surface_lint._validate_relational_permission(text, relational_context=relational_context)

    def _validate_semantic_repeat_surface(self, text: str, *, line_type: str, semantic_family: str, meaning_family: str):
        _ = (text, line_type, semantic_family, meaning_family)
        return []

    def _risk_level_for_line(self, *, line_type: str, signal_score: float, requested: str = "") -> str:
        return self.grounding_guard.risk_level_for_line(line_type=line_type, signal_score=signal_score, requested=requested)

    def _signal_score(self, *, broadcast, vision_fact: str) -> float:
        return self.grounding_guard.signal_score(broadcast=broadcast, vision_fact=vision_fact)

    def _candidate_signal_score(self, candidate) -> float:
        return self.grounding_guard.candidate_signal_score(candidate)

    def _resolve_anchor(self, *, line_type: str, broadcast, vision_fact: str, user_action: str, session_context: str) -> tuple[str, str]:
        anchor = self.grounding_guard.resolve_anchor(
            line_type=line_type,
            broadcast=broadcast,
            vision_fact=vision_fact,
            user_action=user_action,
            session_context=session_context,
        )
        return anchor.source, anchor.text

    def _looks_like_placeholder(self, text: str) -> bool:
        return self.surface_lint.looks_like_placeholder(text)

    def _word_count(self, text: str) -> int:
        return word_count(text)

    def _looks_like_unaccented_vietnamese(self, text: str) -> bool:
        return self.surface_lint.looks_like_unaccented_vietnamese(text)

    def _has_clear_short_meaning(self, text: str, **kwargs) -> bool:
        return self.surface_lint.has_clear_short_meaning(text, **kwargs)

    def _text_matches_anchor(self, text: str, anchor: str) -> bool:
        return self.grounding_guard.text_matches_anchor(text, anchor)

    def _looks_like_question(self, text: str) -> bool:
        return self.surface_lint.looks_like_question(text)

    def _looks_therapeutic(self, text: str) -> bool:
        return self.surface_lint.looks_therapeutic(text)

    def _looks_like_hard_fact_claim(self, text: str) -> bool:
        return self.grounding_guard.looks_like_hard_fact_claim(text)

    def _looks_like_decorative_metaphor_without_stance(self, text: str) -> bool:
        return self.surface_lint.looks_like_decorative_metaphor_without_stance(text)

    def _semantic_density(self, text: str) -> float:
        return self.surface_lint.semantic_density(text)

    def _looks_like_presence_narration(self, text: str) -> bool:
        return self.surface_lint.looks_like_presence_narration(text)

    def _looks_like_blunt_emotion_declaration(self, text: str) -> bool:
        return self.surface_lint.looks_like_blunt_emotion_declaration(text)

    def _relational_expression_issue(self, text: str, *, relational_context: RelationalContext | None) -> str:
        return self.surface_lint.relational_expression_issue(text, relational_context=relational_context)

    def _relational_permission_stage(self, relational_context: RelationalContext | None) -> str:
        return self.surface_lint.relational_permission_stage(relational_context)

    def _looks_like_relational_expression(self, text: str) -> bool:
        return self.surface_lint.looks_like_relational_expression(text)

    def _looks_like_continuity_pain(self, text: str) -> bool:
        return self.surface_lint.looks_like_continuity_pain(text)

    def _looks_like_clinginess_or_blackmail(self, text: str) -> bool:
        return self.surface_lint.looks_like_clinginess_or_blackmail(text)

    def _relational_theme(self, normalized: str) -> str:
        return self.surface_lint.relational_theme(normalized)

    def _allowed_exposure_for_stage(self, stage: str) -> float:
        return self.surface_lint.allowed_exposure_for_stage(stage)

    def _clamp01(self, value) -> float:
        return clamp01(value)

    def _dedupe_issues(self, issues: list[ValidationIssue]) -> tuple[ValidationIssue, ...]:
        return dedupe_issues(issues)


__all__ = [
    "MicroLinePolicy",
    "MicroLinePolicyResult",
    "SemanticRepeatGuard",
    "SemanticRepeatResult",
    "ValidationIssue",
    "RelationalContext",
    "RelationalRewritePlan",
]

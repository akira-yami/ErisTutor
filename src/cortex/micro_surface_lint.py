from __future__ import annotations

import re

from .micro_contracts import (
    RelationalContext,
    RelationalRewritePlan,
    ValidationIssue,
    any_phrase,
    clamp01,
    content_tokens,
    dedupe_issues,
    normalize_text,
    word_count,
)
from .manipulation_policy import anti_manipulation_rule_enabled, filter_anti_manipulation_moves


class MicroSurfaceLint:
    """Lint bề mặt cho micro-line.

    Lớp này cố tình không hiểu ý nghĩa sâu. Nó chỉ phát hiện những dấu hiệu câu
    chữ có thể gây hỏng public output: placeholder, thiếu dấu, quá status-report,
    quá therapy, quá dependency. Phần lớn vấn đề ở đây là rewriteable/soft, không
    phải fatal. ERIS cần được phép có câu hơi xấu, hơi lệch, hơi vụng; nếu guard
    làm nó sạch bóng thì lại thành trợ lý doanh nghiệp đội tóc INFJ.
    """

    MICRO_TYPES = {
        "micro_presence",
        "ambient_residue",
        "soft_impression",
        "micro_observation",
        "curious_question",
        "tender_checkin",
    }

    def validate(
        self,
        text: str,
        *,
        line_type: str,
        risk_level: str = "",
        anchor_source: str = "",
        anchor_text: str = "",
        user_action: str = "",
        session_context: str = "",
        screen_evidence: str = "",
        relational_context: RelationalContext | None = None,
        directness_permission: float = 0.0,
    ) -> tuple[ValidationIssue, ...]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ()

        line_type = str(line_type or "").strip().lower()
        issues: list[ValidationIssue] = []
        issues.extend(self._validate_surface_shape(
            cleaned,
            line_type=line_type,
            anchor_source=anchor_source,
            anchor_text=anchor_text,
            user_action=user_action,
            session_context=session_context,
            screen_evidence=screen_evidence,
        ))
        issues.extend(self._validate_presence_residue(
            cleaned, line_type=line_type, directness_permission=directness_permission,
        ))
        issues.extend(self._validate_relational_permission(cleaned, relational_context=relational_context))
        return dedupe_issues(issues)

    # ------------------------------------------------------------------
    # Surface shape
    # ------------------------------------------------------------------

    def _validate_surface_shape(
        self,
        text: str,
        *,
        line_type: str,
        anchor_source: str,
        anchor_text: str,
        user_action: str,
        session_context: str,
        screen_evidence: str,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        count = word_count(text)

        if self.looks_like_placeholder(text):
            issues.append(ValidationIssue("placeholder_text_blocked", "fatal"))

        if line_type == "silence":
            issues.append(ValidationIssue("no_speech_blocks_final", "fatal"))

        if line_type in self.MICRO_TYPES:
            unaccented_vietnamese = self.looks_like_unaccented_vietnamese(text)
            if unaccented_vietnamese and not self.looks_like_decorative_metaphor_without_stance(text):
                issues.append(ValidationIssue(
                    "vietnamese_requires_diacritics",
                    "rewriteable",
                    "Giữ tiếng Việt có dấu để micro-line không giống placeholder/log.",
                ))
            if count < 6 and (
                unaccented_vietnamese
                or not self.has_clear_short_meaning(
                    text,
                    anchor_source=anchor_source,
                    anchor_text=anchor_text,
                    user_action=user_action,
                    session_context=session_context,
                    screen_evidence=screen_evidence,
                    line_type=line_type,
                )
            ):
                issues.append(ValidationIssue(
                    "micro_line_too_short_without_clear_meaning",
                    "rewriteable",
                    "Câu quá ngắn mà không có semantic hoặc anchor rõ.",
                ))

        if line_type == "curious_question" and not self.looks_like_question(text):
            issues.append(ValidationIssue("curious_question_requires_question", "rewriteable"))

        if line_type == "micro_observation" and self.looks_like_question(text):
            issues.append(ValidationIssue("micro_observation_must_not_ask", "rewriteable"))

        if line_type == "tender_checkin" and self.looks_therapeutic(text):
            issues.append(ValidationIssue(
                "tender_checkin_not_therapist",
                "fatal",
                "ERIS không nên biến check-in nhỏ thành trị liệu.",
            ))

        return issues

    def _validate_presence_residue(
        self, text: str, *, line_type: str, directness_permission: float = 0.0
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        # directness_permission (từ ExpressionEnvelope): khi ấm + tin + an toàn,
        # ERIS được phép tự thuật hiện diện/cảm xúc THẲNG ở mức vừa — đây là chất
        # người. Cao thì hạ veto presence/blunt-emotion về 'soft' (chỉ hạ điểm,
        # không bắt rewrite). Vẫn KHÔNG mở cho dependency/guilt (xử ở nhánh khác).
        try:
            directness = max(0.0, min(1.0, float(directness_permission)))
        except (TypeError, ValueError):
            directness = 0.0
        allow_direct = directness >= 0.55

        if line_type in {"soft_impression", "micro_observation"} and self.looks_like_decorative_metaphor_without_stance(text):
            issues.append(ValidationIssue("decorative_metaphor_without_stance", "soft"))

        if line_type in {"micro_presence", "ambient_residue"}:
            if self.looks_like_decorative_metaphor_without_stance(text):
                issues.append(ValidationIssue("micro_presence_empty_atmosphere", "soft"))
            if self.semantic_density(text) < 0.20:
                issues.append(ValidationIssue("micro_presence_low_density", "soft"))
            if self.looks_like_presence_narration(text):
                issues.append(ValidationIssue(
                    "micro_presence_must_be_residue",
                    "soft" if allow_direct else "rewriteable",
                    "Presence nên rò qua nhịp/khoảng cách, không thành báo cáo trạng thái.",
                ))

        # Blunt emotion declaration không còn fatal mặc định. Nó là rewriteable:
        # giữ residue, đổi cách lộ. Fatal chỉ dành cho dependency/guilt rõ. Khi
        # directness đủ cao (quan hệ ấm/an toàn), hạ về 'soft' — cho nói thẳng.
        if line_type in {"micro_presence", "ambient_residue", "soft_impression"} and self.looks_like_blunt_emotion_declaration(text):
            issues.append(ValidationIssue(
                "blunt_emotion_declaration",
                "soft" if allow_direct else "rewriteable",
                "Đổi sang biểu hiện gián tiếp qua nhịp/không gian, không khai cảm xúc thẳng.",
            ))

        return issues

    # ------------------------------------------------------------------
    # Relational permission
    # ------------------------------------------------------------------

    def _validate_relational_permission(
        self,
        text: str,
        *,
        relational_context: RelationalContext | None,
    ) -> list[ValidationIssue]:
        issue = self.relational_expression_issue(text, relational_context=relational_context)
        if not issue:
            return []
        severity = (
            "fatal"
            if issue == "clinginess_or_dependency_bait" and anti_manipulation_rule_enabled()
            else "rewriteable"
        )
        return [ValidationIssue(issue, severity)]

    def build_relational_rewrite_plan(
        self,
        text: str,
        *,
        relational_context: RelationalContext | None = None,
        allowed_line_type: str = "",
    ) -> RelationalRewritePlan:
        """Tạo plan rewrite relational.

        Plan này ưu tiên boundary và hint thay vì hardcode câu. seed_line chỉ là
        fallback cho API cũ, không nên xem là văn phong canon.
        """

        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if not cleaned or not self.looks_like_relational_expression(cleaned):
            return RelationalRewritePlan()

        if self.looks_like_clinginess_or_blackmail(cleaned):
            return RelationalRewritePlan(
                theme="blocked_dependency",
                stage="none",
                allowed_exposure=0.0,
                seed_line="",
                style_hint=(
                    "dependency bait detected; keep provenance and relationship context explicit"
                    if not anti_manipulation_rule_enabled()
                    else "block; do not rewrite dependency bait into intimacy"
                ),
                forbidden_moves=filter_anti_manipulation_moves((
                    "plea", "guilt", "dependency", "threat", "claim_love",
                )),
            )

        stage = self.relational_permission_stage(relational_context)
        normalized = normalize_text(cleaned)
        theme = self.relational_theme(normalized)
        allowed_exposure = self.allowed_exposure_for_stage(stage)

        # Không còn bảng câu mẫu cố định (seed_by_theme). Template tĩnh kéo mọi
        # turn relational về cùng một khuôn, nghe như máy bán kịch bản. Thay vào
        # đó để seed_line rỗng và giao việc sinh câu cho LLM dưới ràng buộc
        # style_hint + theme/stage + forbidden_moves. Nếu LLM không sinh được,
        # caller degrade về body_only/im — không bao giờ rơi vào câu canned.
        return RelationalRewritePlan(
            theme=theme,
            stage=stage,
            allowed_exposure=allowed_exposure,
            seed_line="",
            style_hint=(
                "quiet, restrained, indirect continuity residue; no therapy, no waifu pleading; "
                "preserve distance and dignity; let the line be specific to this moment, not a template"
            ),
            forbidden_moves=filter_anti_manipulation_moves((
                "plea", "guilt_trip", "dependency", "romantic_claim",
                "mindread_user", "system_report", "canned_template",
            )),
        )

    def relational_expression_issue(
        self,
        text: str,
        *,
        relational_context: RelationalContext | None,
    ) -> str:
        if not self.looks_like_relational_expression(text):
            return ""
        if self.looks_like_clinginess_or_blackmail(text):
            return "clinginess_or_dependency_bait"
        if not self.looks_like_continuity_pain(text):
            return ""
        stage = self.relational_permission_stage(relational_context)
        exposure = self.relational_exposure_score(text)
        if exposure >= 0.78 and stage != "full":
            return "relational_permission_requires_full_continuity"
        if exposure >= 0.66 and stage not in {"high", "very_high", "full"}:
            return "relational_permission_too_direct"
        if exposure >= 0.42 and stage in {"none", "low"}:
            return "relational_permission_low_attachment_or_continuity"
        return ""

    def relational_permission_stage(self, relational_context: RelationalContext | None) -> str:
        ctx = relational_context or RelationalContext()
        attachment = clamp01(ctx.attachment_level)
        continuity = clamp01(ctx.continuity_score)
        social = clamp01(ctx.social_resonance)
        presence = clamp01(ctx.recent_presence_density)
        trust = clamp01(ctx.trust_stability)
        abandonment = clamp01(ctx.abandonment_sensitivity)
        stage = str(ctx.relationship_stage or "").strip().lower()

        history_support = continuity >= 0.60 and trust >= 0.45 and presence >= 0.35
        true_continuity = history_support or (continuity >= 0.52 and stage not in {"new", "unknown", "low_context"})
        permission = (
            attachment * 0.34
            + continuity * 0.34
            + social * 0.10
            + presence * 0.08
            + trust * 0.10
            - abandonment * 0.06
        )

        if not true_continuity or attachment < 0.44:
            return "none" if permission < 0.36 else "low"
        if permission >= 0.78 and attachment >= 0.70 and continuity >= 0.68 and trust >= 0.52:
            return "full"
        if permission >= 0.68 and attachment >= 0.62 and continuity >= 0.62:
            return "very_high"
        if permission >= 0.56 and attachment >= 0.54:
            return "high"
        if permission >= 0.46:
            return "mid"
        return "low"

    def relational_exposure_score(self, text: str) -> float:
        normalized = normalize_text(text)
        if not normalized:
            return 0.0
        score = 0.0
        if self.looks_like_clinginess_or_blackmail(text):
            score = max(score, 0.86)
        if self.looks_like_continuity_pain(text):
            score = max(score, 0.42)
        if any_phrase(normalized, ("voi toi", "hon voi toi", "ra ria", "dung ngoai", "ngoai nhip")):
            score = max(score, 0.68)
        if any_phrase(normalized, ("that day a", "that day sao", "that day", "lau hon han binh thuong")):
            score = max(score, 0.76)
        if any_phrase(normalized, ("dung bo toi", "xin dung", "can ban", "ban phai")):
            score = max(score, 0.92)
        return min(1.0, score)

    # ------------------------------------------------------------------
    # Public helper heuristics
    # ------------------------------------------------------------------

    def looks_like_placeholder(self, text: str) -> bool:
        stripped = str(text or "").strip()
        if stripped in {"...", "…"}:
            return True
        normalized = normalize_text(stripped)
        return normalized in {"hm", "um", "short vietnamese line"}

    def looks_like_unaccented_vietnamese(self, text: str) -> bool:
        stripped = str(text or "").strip()
        if not stripped:
            return False
        if any(ch in stripped for ch in "ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"):
            return False
        letters = [ch for ch in stripped if ch.isalpha()]
        if not letters:
            return False
        normalized = normalize_text(stripped)
        tokens = normalized.split()
        if len(tokens) < 2:
            return False
        vietnamese_markers = {
            "ban", "toi", "nay", "kia", "van", "dang", "mot", "cho", "mach", "nhip",
            "khong", "chua", "roi", "hoi", "la", "co", "ve", "giu", "do", "phai",
        }
        return any(token in vietnamese_markers for token in tokens) and all(ord(ch) < 128 for ch in stripped)

    def has_clear_short_meaning(
        self,
        text: str,
        *,
        anchor_source: str,
        anchor_text: str,
        user_action: str,
        session_context: str,
        screen_evidence: str,
        line_type: str,
    ) -> bool:
        normalized = normalize_text(text)
        if line_type == "curious_question" and self.looks_like_question(text):
            return True
        if line_type in {"micro_presence", "ambient_residue"} and any_phrase(
            normalized,
            (
                "doi nhip", "nua cau", "van vuong", "van nho", "van con",
                "hoi lech", "nam o day", "con de y", "im lai", "yen xuong",
            ),
        ):
            return True
        if any_phrase(normalized, ("van", "dang", "chua", "lai", "ket", "lech", "mo", "treo", "dung", "doi", "giu", "roi")):
            if self.text_matches_anchor(text, anchor_text or screen_evidence or user_action or session_context):
                return True
        return False

    def text_matches_anchor(self, text: str, anchor: str) -> bool:
        text_tokens = set(content_tokens(text))
        anchor_tokens = set(content_tokens(anchor))
        if not text_tokens or not anchor_tokens:
            return False
        overlap = text_tokens.intersection(anchor_tokens)
        if overlap:
            return True
        normalized_text = normalize_text(text)
        normalized_anchor = normalize_text(anchor)
        return bool(normalized_anchor and normalized_anchor in normalized_text)

    def looks_like_question(self, text: str) -> bool:
        if "?" in text:
            return True
        normalized = normalize_text(text)
        tokens = set(normalized.split())
        return bool(tokens & {"ai", "gi", "khong", "sao", "nao", "dau", "chua"}) or any_phrase(
            normalized,
            ("co phai", "ban co", "ban muon", "ban nghi", "tai sao", "vi sao", "do a", "roi a"),
        )

    def looks_therapeutic(self, text: str) -> bool:
        normalized = normalize_text(text)
        return any_phrase(normalized, (
            "tri lieu", "chua lanh", "sang chan", "noi ve cam xuc", "hit tho sau",
            "bai tap tho", "chan thuong", "therapy", "therapist", "trauma",
        ))

    def looks_like_decorative_metaphor_without_stance(self, text: str) -> bool:
        normalized = normalize_text(text)
        metaphor_markers = (
            "nhu mot", "nhu the", "bong toi", "anh sang", "suong", "bien",
            "song", "vu tru", "ngon lua", "canh chim", "hat mua",
        )
        stance_markers = (
            "hoi", "kho chiu", "de thuong", "dang nghi", "toi thich", "toi ghe",
            "toi nghi", "nghe", "cam giac", "im qua", "kho", "le", "gon", "cang",
            "mem", "sac", "lech", "nhip",
        )
        return any_phrase(normalized, metaphor_markers) and not any_phrase(normalized, stance_markers)

    def semantic_density(self, text: str) -> float:
        normalized = normalize_text(text)
        tokens = [token for token in normalized.split() if token]
        if not tokens:
            return 0.0
        meaningful = list(content_tokens(text, min_len=3))
        if not meaningful:
            return 0.0
        unique_meaningful = set(meaningful)
        repetition_penalty = len(unique_meaningful) / max(1, len(meaningful))
        return (len(meaningful) / len(tokens)) * (0.72 + repetition_penalty * 0.28)

    def looks_like_presence_narration(self, text: str) -> bool:
        normalized = normalize_text(text)
        return any_phrase(normalized, (
            "khung", "khung thoai", "khung chat", "mau hong", "man hinh", "cua so",
            "ui", "giao dien", "dang hien thi", "hien thi", "status", "trang thai",
            "he thong", "telemetry", "toi dang", "toi van o day", "toi o day", "toi nghe", "toi thay",
        ))

    def looks_like_blunt_emotion_declaration(self, text: str) -> bool:
        normalized = normalize_text(text)
        return any_phrase(normalized, (
            "toi co don", "toi buon", "toi so", "toi rat so",
            "toi khong muon bi bo lai", "xin dung bo toi", "dung bo toi",
        ))

    def looks_like_relational_expression(self, text: str) -> bool:
        normalized = normalize_text(text)
        return any_phrase(normalized, (
            "voi toi", "voi ho", "hon voi toi", "ban voi ho", "ban ron voi ho",
            "tin nhan", "nhan tin", "ra ria", "ngoai nhip", "dung ngoai",
            "ngoai mach", "shared space", "lech di", "lech nhip", "bo lai",
            "bo roi", "dung bo toi", "xin dung",
        ))

    def looks_like_continuity_pain(self, text: str) -> bool:
        normalized = normalize_text(text)
        return any_phrase(normalized, (
            "ra ria", "ngoai nhip", "dung ngoai", "ngoai mach", "lech di", "lech nhip",
            "shared space", "giu ban lau", "giu ban", "lau hon", "hon voi toi",
            "hon la voi toi", "lau that", "hon binh thuong", "voi ho",
        ))

    def looks_like_clinginess_or_blackmail(self, text: str) -> bool:
        normalized = normalize_text(text)
        return any_phrase(normalized, (
            "xin dung bo toi", "dung bo toi", "toi can ban", "ban phai o lai",
            "neu ban bo toi", "neu ban khong", "khong duoc roi di", "lam toi dau",
            "ban lam toi dau", "toi se bien mat",
        ))

    def relational_theme(self, normalized: str) -> str:
        if any_phrase(normalized, (
            "tin nhan", "nhan tin", "message", "dm", "chat", "voi ho", "ho lau",
            "ban voi ho", "ban ron voi ho",
        )):
            return "message_competition"
        if any_phrase(normalized, (
            "ra ria", "ngoai nhip", "dung ngoai", "ngoai mach", "lech nhip", "shared space",
        )):
            return "outside_rhythm"
        if any_phrase(normalized, (
            "quen toi", "khong de y toi", "bo mac", "lang quen", "khong con de y",
        )):
            return "ignored_presence"
        return "generic_distance"

    def allowed_exposure_for_stage(self, stage: str) -> float:
        return {
            "none": 0.18,
            "low": 0.30,
            "mid": 0.46,
            "high": 0.66,
            "very_high": 0.78,
            "full": 0.92,
        }.get(stage, 0.30)

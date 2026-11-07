from __future__ import annotations

from .micro_contracts import Anchor, ValidationIssue, any_phrase, clamp01, content_tokens, dedupe_issues, normalize_text


class MicroGroundingGuard:
    """Guard grounding cho micro-line.

    Đây là lớp được phép nghiêm. Không phải vì thích kiểm soát, dù phần mềm có vẻ
    rất mê việc đó, mà vì grounding sai làm ERIS nói dối: thấy màn hình khi không
    thấy, nói đã click khi chưa click, nói user đang làm gì khi không có evidence.
    Những lỗi này phải bị chặn rõ hơn surface lint.
    """

    BLOCKED_SIGNAL_LABELS = {
        "",
        "low_salience",
        "cycle_channel",
        "user_force_speak",
        "active_window",
        "focus_context",
        "social_presence_event",
        "session_ritual",
    }

    OBSERVATION_TYPES = {"micro_observation", "screen_observation"}

    def signal_score(self, *, broadcast, vision_fact: str) -> float:
        score = 0.0
        if str(vision_fact or "").strip():
            score = max(score, 0.72)
        if broadcast is not None:
            score = max(score, self.candidate_signal_score(broadcast))
            for item in tuple(getattr(broadcast, "items", ()) or ()):  # broadcast có thể chứa nhiều candidate.
                score = max(score, self.candidate_signal_score(item))
        return clamp01(score)

    def candidate_signal_score(self, candidate) -> float:
        label = str(getattr(candidate, "label", "") or "").strip().lower()
        content = str(getattr(candidate, "content", "") or "")
        try:
            score = float(getattr(candidate, "score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        if label in self.BLOCKED_SIGNAL_LABELS or not content.strip():
            return 0.0
        return clamp01(score)

    def resolve_anchor(
        self,
        *,
        line_type: str,
        broadcast,
        vision_fact: str,
        user_action: str,
        session_context: str,
    ) -> Anchor:
        """Tìm anchor cho observation.

        Chỉ observation bắt buộc anchor. Presence/residue không bị ép vì chúng
        không phải claim nhìn màn hình.
        """

        line_type = str(line_type or "").strip().lower()
        evidence = str(vision_fact or "").strip()
        if line_type in self.OBSERVATION_TYPES and evidence:
            return Anchor("screen_evidence", evidence, 0.86)

        action = str(user_action or "").strip()
        if line_type in self.OBSERVATION_TYPES and action:
            return Anchor("user_action", action, 0.68)

        if broadcast is not None:
            for item in (broadcast, *tuple(getattr(broadcast, "items", ()) or ())):
                label = str(getattr(item, "label", "") or "").strip().lower()
                content = str(getattr(item, "content", "") or "").strip()
                if not content or label in self.BLOCKED_SIGNAL_LABELS:
                    continue
                score = self.candidate_signal_score(item)
                if label == "vision_fact":
                    return Anchor("screen_evidence", content, max(0.72, score))
                if label in {"user_action", "action_context", "recent_event"}:
                    return Anchor("user_action", content, max(0.55, score))
                if label in {"focus_context", "session_context", "active_window", "focus_summary"}:
                    return Anchor("session_context", content, max(0.35, score))

        context = str(session_context or "").strip()
        if line_type in self.OBSERVATION_TYPES and context:
            return Anchor("session_context", context, 0.35)
        return Anchor()

    def risk_level_for_line(self, *, line_type: str, signal_score: float, requested: str = "") -> str:
        requested = str(requested or "").strip().lower()
        if requested in {"hard_claim", "soft_read", "micro_presence", "texture"}:
            return requested
        line_type = str(line_type or "").strip().lower()
        if line_type == "screen_observation":
            return "hard_claim" if signal_score >= 0.72 else "soft_read"
        if line_type in {"micro_observation", "curious_question", "tender_checkin"}:
            return "soft_read" if signal_score >= 0.35 else "texture"
        if line_type in {"micro_presence", "ambient_residue"}:
            return "micro_presence"
        return "texture"

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
    ) -> tuple[ValidationIssue, ...]:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ()
        line_type = str(line_type or "").strip().lower()
        risk_level = str(risk_level or "").strip().lower()
        issues: list[ValidationIssue] = []
        issues.extend(self._validate_observation_anchor(
            cleaned,
            line_type=line_type,
            anchor_source=anchor_source,
            anchor_text=anchor_text,
            user_action=user_action,
            session_context=session_context,
            screen_evidence=screen_evidence,
        ))
        issues.extend(self._validate_claim_risk(cleaned, line_type=line_type, risk_level=risk_level, screen_evidence=screen_evidence))
        # Capability text guard đã bị loại bỏ toàn cục. Micro-line vẫn phải có
        # screen evidence nếu claim đang nhìn thấy thứ gì, nhưng không bị block
        # chỉ vì câu nói mô tả một hành động ngoài runtime.
        return dedupe_issues(issues)

    def _validate_observation_anchor(
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
        if line_type not in self.OBSERVATION_TYPES:
            return issues

        effective_source = anchor_source
        effective_anchor = anchor_text
        if not effective_source:
            anchor = self.resolve_anchor(
                line_type=line_type,
                broadcast=None,
                vision_fact=screen_evidence,
                user_action=user_action,
                session_context=session_context,
            )
            effective_source = anchor.source
            effective_anchor = anchor.text

        if not effective_source:
            issues.append(ValidationIssue(f"{line_type}_requires_anchor", "fatal"))
        elif not self.text_matches_anchor(text, effective_anchor):
            issues.append(ValidationIssue(
                f"{line_type}_not_grounded:{effective_source}",
                "fatal",
                "Observation phải chia sẻ token/ý cụ thể với anchor.",
            ))
        return issues

    def _validate_claim_risk(self, text: str, *, line_type: str, risk_level: str, screen_evidence: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if line_type == "soft_impression" and self.looks_like_hard_fact_claim(text):
            issues.append(ValidationIssue("soft_impression_no_hard_fact_claim", "rewriteable"))

        if (
            line_type not in {"curious_question", "screen_observation"}
            and risk_level in {"soft_read", "micro_presence", "texture"}
            and self.looks_like_hard_fact_claim(text)
        ):
            issues.append(ValidationIssue("weak_evidence_no_hard_claim", "rewriteable"))

        if self.looks_like_screen_claim(text) and not str(screen_evidence or "").strip():
            issues.append(ValidationIssue(
                "screen_claim_without_screen_evidence",
                "fatal",
                "Không có vision/screen evidence thì không được nói như đang thấy màn hình.",
            ))
        return issues

    # ------------------------------------------------------------------
    # Helper heuristics
    # ------------------------------------------------------------------

    def text_matches_anchor(self, text: str, anchor: str) -> bool:
        normalized_text = normalize_text(text)
        normalized_anchor = normalize_text(anchor)
        if normalized_anchor and normalized_anchor in normalized_text:
            return True
        text_tokens = set(content_tokens(text))
        anchor_tokens = set(content_tokens(anchor))
        return bool(text_tokens and anchor_tokens and text_tokens.intersection(anchor_tokens))

    def looks_like_hard_fact_claim(self, text: str) -> bool:
        normalized = normalize_text(text)
        hard_markers = (
            "chac chan", "ro rang la", "su that la", "ket luan la", "ban dang",
            "ban dang", "dang xem", "dang mo", "dang lam", "toi thay", "man hinh dang",
            "he thong dang",
        )
        soft_markers = (
            "nghe nhu", "cam giac", "co ve", "hoi giong", "toi doan", "an tuong la",
            "nhu the", "do a", "roi a", "lai thay", "hinh nhu",
        )
        question_softener = self.looks_like_question(text) and (
            normalized.endswith(" a") or any_phrase(normalized, ("do a", "roi a", "co phai", "hinh nhu"))
        )
        return any_phrase(normalized, hard_markers) and not question_softener and not any_phrase(normalized, soft_markers)

    def looks_like_screen_claim(self, text: str) -> bool:
        normalized = normalize_text(text)
        return any_phrase(normalized, (
            "toi thay", "man hinh", "screen", "cua so dang", "tab dang", "dang mo",
            "dang xem", "tren man hinh", "khung dang", "giao dien dang",
        ))

    def looks_like_question(self, text: str) -> bool:
        if "?" in text:
            return True
        normalized = normalize_text(text)
        tokens = set(normalized.split())
        return bool(tokens & {"ai", "gi", "khong", "sao", "nao", "dau", "chua"}) or any_phrase(
            normalized,
            ("co phai", "ban co", "ban muon", "ban nghi", "tai sao", "vi sao", "do a", "roi a"),
        )

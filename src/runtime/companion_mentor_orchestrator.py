# -*- coding: utf-8 -*-
"""V84 - Orchestrator hợp nhất Companion / Mentor / Supervisor.

LƯU Ý SAU COMPANION CONSOLIDATION:
Module này là LEGACY compatibility/test utility, KHÔNG còn nằm trong active delivery path.
AdaptiveCompanion là owner duy nhất của stance/delivery. Không wire module này trở
lại EngineeringMentor nếu không muốn tái tạo hai bộ não quyết cách nói.

Module này KHÔNG phải agent mới và không sinh câu trả lời riêng.
Nó chỉ tạo một directive nhỏ cho public writer để Eris giữ *một giọng nói* nhưng
biết lúc nào nên:
- đồng hành/bàn luận (companion),
- dạy/hỏi dẫn (mentor),
- tóm tắt evidence/rủi ro của workflow (supervisor).

Boundary:
- không gọi tool;
- không điều khiển GitPilot/Godot;
- không suy đoán trạng thái tâm lý sâu từ vài từ cảm thán;
- không chia output thành ba persona/ba section riêng biệt.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping


_HEATED_LANGUAGE_MARKERS = (
    "dcm", "dm ", "vcl", "wtf", "đéo", "lmao", "vl ", "bực", "chán", "lại lỗi", "lỗi gì nữa",
    "bug gì nữa", "sao nữa", "mẹ nó", "khó chịu",
)

_STATUS_MARKERS = (
    "xong chưa", "đang làm gì", "làm tới đâu", "sửa gì", "đổi gì", "ổn chưa", "review sao",
    "gitpilot làm", "agent làm", "vừa sửa", "kết quả", "test sao", "reviewer",
)

_FAST_EXECUTION_MARKERS = (
    "làm luôn", "cứ làm", "triển đi", "fix luôn", "sửa luôn", "code đi", "apply đi", "patch đi",
)


@dataclass(slots=True)
class CompanionMentorRoleDirective:
    """Directive delivery, không phải kết luận về tính cách hay cảm xúc user."""

    active: bool = False
    primary_role: str = "companion"  # companion|mentor|supervisor
    supporting_functions: tuple[str, ...] = ()
    reason: str = ""
    surface_cue: str = "neutral"  # neutral|heated_language|fast_execution|status_check
    mentor_intensity: str = "none"  # none|light|normal|strict
    opening_rule_vi: str = ""
    transition_rule_vi: str = ""
    delivery_rule_vi: str = ""
    confidence: float = 0.0
    max_role_transitions: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.companion_mentor_role.v1",
            "active": self.active,
            "primary_role": self.primary_role,
            "supporting_functions": list(self.supporting_functions),
            "reason": self.reason,
            "surface_cue": self.surface_cue,
            "mentor_intensity": self.mentor_intensity,
            "opening_rule_vi": self.opening_rule_vi,
            "transition_rule_vi": self.transition_rule_vi,
            "delivery_rule_vi": self.delivery_rule_vi,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "max_role_transitions": max(0, min(2, int(self.max_role_transitions))),
            "metadata": dict(self.metadata),
        }


class CompanionMentorRoleOrchestrator:
    """V84: chọn stance cho *một* Eris, không tạo thêm persona.

    State chỉ dùng để làm transition bớt giật trong cùng process. Không persistence:
    restart app thì role có thể reset mà không làm mất skill/memory nào.
    """

    def __init__(self) -> None:
        self._last_primary_role = ""
        self._last_role_at = 0.0
        self._last_directive = CompanionMentorRoleDirective()

    @staticmethod
    def _contains(text: str, markers: tuple[str, ...]) -> bool:
        return any(marker in text for marker in markers)

    @staticmethod
    def _has_flow(flow: Mapping[str, Any] | None) -> bool:
        return bool(flow and str(flow.get("task_id") or ""))

    @staticmethod
    def _has_severe_findings(decision: Any) -> bool:
        severe = {"patch_before_inspection", "repeated_same_failure", "repeated_test_failure"}
        findings = tuple(getattr(decision, "grounded_findings", ()) or ())
        return any(str(item) in severe for item in findings)

    def route(
        self,
        user_text: str,
        *,
        decision: Any,
        flow: Mapping[str, Any] | None = None,
        cross_environment: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> CompanionMentorRoleDirective:
        ts = float(now or time.time())
        low = " ".join(str(user_text or "").split()).casefold()
        if not bool(getattr(decision, "relevant", False)):
            directive = CompanionMentorRoleDirective()
            self._last_directive = directive
            return directive

        mode = str(getattr(decision, "mode", "discuss") or "discuss")
        reason = str(getattr(decision, "reason", "") or "")
        signals = tuple(str(x) for x in (getattr(decision, "user_signals", ()) or ()))
        has_flow = self._has_flow(flow)
        cross_active = bool((cross_environment or {}).get("active", False))
        heated = self._contains(low, _HEATED_LANGUAGE_MARKERS)
        status_check = self._contains(low, _STATUS_MARKERS)
        fast_execution = self._contains(low, _FAST_EXECUTION_MARKERS) or "blind_execution_request" in signals
        explicit_learning = "learning_intent" in signals or reason == "user_requested_learning"
        explicit_verification = "verification_intent" in signals or reason == "user_requested_verification"
        severe_flow = self._has_severe_findings(decision)

        if heated:
            surface_cue = "heated_language"
        elif status_check:
            surface_cue = "status_check"
        elif fast_execution:
            surface_cue = "fast_execution"
        else:
            surface_cue = "neutral"

        # 1) User chủ động muốn học: trả lời như thầy trước, không vòng vo bằng companion small-talk.
        if explicit_learning or reason == "candidate_misconception_needs_check" or (mode in {"explain", "guide"} and not explicit_verification):
            primary = "mentor"
            supporting = ["companion"]
            if has_flow or cross_active:
                supporting.append("supervisor")
            intensity = "normal"
            if mode == "explain":
                intensity = "normal"
            opening = "Vào thẳng câu hỏi học/giải thích của user; giữ giọng tự nhiên của Eris, không mở bằng bài giảng meta."
            transition = "Nếu có coding evidence gần đây, dùng nó làm ví dụ sau khi đã trả lời concept; không biến flow thành authority tuyệt đối."
            why = "explicit_learning_prefers_mentor"
            confidence = 0.94

        # 2) User hỏi trạng thái / verification: supervisor nói evidence trước; mentor chỉ chen nếu có bài học rõ.
        elif explicit_verification or status_check:
            primary = "supervisor"
            supporting = ["companion"]
            if mode in {"hint", "challenge", "warn"} or severe_flow:
                supporting.append("mentor")
            intensity = "light" if mode != "warn" else "strict"
            opening = "Nói observable evidence/kết quả trước. Nếu thiếu evidence, nói thiếu chứ không đoán."
            transition = "Sau status/evidence, chỉ thêm một teaching note nếu nó giúp user kiểm chứng hoặc hiểu workflow tốt hơn."
            why = "status_or_verification_prefers_supervisor"
            confidence = 0.90

        # 3) Warning anti-vibe-code: mentor phải cứng, supervisor cung cấp bằng chứng; không attack con người.
        elif mode == "warn":
            primary = "mentor"
            supporting = ["supervisor", "companion"]
            intensity = "strict"
            opening = (
                "Nói thẳng vào hành vi/workflow đang có vấn đề. Nếu câu user dùng ngôn ngữ nóng, có thể acknowledgement rất ngắn "
                "nhưng không xoa dịu kiểu trị liệu."
            )
            transition = "Nêu evidence quan sát được rồi giao đúng một việc nhỏ user nên tự kiểm tra; không block quyền tiếp tục của user."
            why = "strict_mentor_for_repeated_overreliance"
            confidence = 0.93

        # 4) User đang cáu/chửi bug nhưng không hỏi học: companion first để cuộc nói chuyện không hóa lecture.
        elif heated:
            primary = "companion"
            supporting = ["supervisor"] if (has_flow or cross_active) else []
            if mode in {"hint", "challenge"} or severe_flow:
                supporting.append("mentor")
            intensity = "light" if "mentor" in supporting else "none"
            opening = "Acknowledge tình huống kỹ thuật rất ngắn bằng giọng tự nhiên; không psychoanalyze cảm xúc user."
            transition = "Chuyển ngay sang evidence/bước kiểm tra. Mentor note chỉ một ý nếu thực sự đáng học."
            why = "heated_surface_language_prefers_companion_first"
            confidence = 0.88

        # 5) Request 'cứ làm' bình thường: companion + supervisor, không tự biến thành lớp học nếu policy chưa cảnh báo.
        elif fast_execution and mode == "discuss":
            primary = "companion"
            supporting = ["supervisor"] if has_flow else []
            intensity = "none"
            opening = "Tôn trọng ưu tiên tốc độ của user; không giảng đạo chỉ vì user giao việc cho AI."
            transition = "Nếu có scope/verification evidence đáng lưu ý thì nói một note ngắn; còn không thì tiếp tục normal workflow discussion."
            why = "fast_execution_without_risk_stays_companion"
            confidence = 0.86

        # 6) Teachable finding chủ động từ Eris: supervisor grounded trước, mentor nhẹ sau.
        elif mode in {"hint", "challenge"} or severe_flow:
            primary = "supervisor"
            supporting = ["mentor", "companion"]
            intensity = "normal" if mode == "challenge" else "light"
            opening = "Bắt đầu từ finding/evidence của workflow, không mở bằng phán xét trình độ user."
            transition = "Biến finding thành một hint/question ngắn để user tự suy luận; sau đó quay về conversation bình thường."
            why = "teachable_flow_prefers_supervisor_then_mentor"
            confidence = 0.87

        # 7) Coding talk thông thường: Eris vẫn là companion; mentor/supervisor chỉ là chức năng phụ khi cần.
        else:
            primary = "companion"
            supporting = ["supervisor"] if has_flow else []
            intensity = "none"
            opening = "Bàn luận như một kỹ sư đồng hành; trả lời trực tiếp mục tiêu hiện tại."
            transition = "Không tự dựng lesson. Chỉ chuyển sang supervisor nếu cần tóm tắt evidence thực tế."
            why = "ordinary_engineering_discussion_stays_companion"
            confidence = 0.82

        # Một response có thể dùng nhiều *chức năng*, nhưng không được dựng ba persona.
        supporting = list(dict.fromkeys(x for x in supporting if x != primary))[:2]
        max_transitions = 1 if len(supporting) <= 1 else 2

        # Continuity mềm: chỉ ghi metadata để writer biết role vừa đổi; không cưỡng ép
        # giữ role cũ vì explicit intent của turn hiện tại phải thắng.
        role_changed = bool(
            self._last_primary_role
            and self._last_primary_role != primary
            and ts - self._last_role_at <= 10 * 60.0
        )
        directive = CompanionMentorRoleDirective(
            active=True,
            primary_role=primary,
            supporting_functions=tuple(supporting),
            reason=why,
            surface_cue=surface_cue,
            mentor_intensity=intensity,
            opening_rule_vi=opening,
            transition_rule_vi=transition,
            delivery_rule_vi=(
                "Viết như MỘT Eris duy nhất. Không dùng nhãn 'Companion:', 'Mentor:', 'Supervisor:'; "
                "không chia câu trả lời thành các persona. Có thể chuyển chức năng tự nhiên tối đa theo max_role_transitions."
            ),
            confidence=confidence,
            max_role_transitions=max_transitions,
            metadata={
                "role_changed_recently": role_changed,
                "has_recent_coding_flow": has_flow,
                "cross_environment_active": cross_active,
                "explicit_learning": explicit_learning,
                "explicit_verification": explicit_verification,
            },
        )
        self._last_primary_role = primary
        self._last_role_at = ts
        self._last_directive = directive
        return directive

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema": "eris.companion_mentor_role.runtime.v1",
            "last_primary_role": self._last_primary_role,
            "last_role_at": self._last_role_at,
            "last_directive": self._last_directive.to_dict(),
        }


__all__ = ["CompanionMentorRoleDirective", "CompanionMentorRoleOrchestrator"]

from __future__ import annotations

"""
cortex/self_observation.py

Tầng tự quan sát sau mỗi turn.

Nhiệm vụ:
- Đọc frame + OutcomeReport.
- Phân loại lỗi của ERIS: misread, overread, overexplained, underacted,
  tone_mismatch, wrong_abstraction, privacy_intrusion, body_mismatch,
  culture_misread, assistant_flattening, overhumanization.
- Trả SelfObservationReport.
- Có helper tạo repair commit request, nhưng không commit trực tiếp.

Tầng này không phải "tự thú nội tâm" để nói với user.
Nó là debug/cognition contract nội bộ.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping

try:
    from runtime.outcome_observer import OutcomeReport
except Exception:  # pragma: no cover - fallback khi chạy file rời
    OutcomeReport = Any  # type: ignore


FailureType = Literal[
    "none",
    "misread",
    "overread",
    "overexplained",
    "underacted",
    "tone_mismatch",
    "wrong_abstraction",
    "privacy_intrusion",
    "body_mismatch",
    "culture_misread",
    "assistant_flattening",
    "overhumanization",
]


@dataclass(slots=True)
class SelfObservationReport:
    """
    Báo cáo tự quan sát của ERIS sau outcome.

    Field lõi theo pipeline:
    - matched_beliefs: câu/hành động vừa rồi có khớp belief/grounding không.
    - overread_detected: có suy diễn quá bằng chứng không.
    - distance_violation: có vượt khoảng cách quan hệ không.
    - curiosity_intrusion: tò mò/xen hỏi vượt ranh giới không.
    - spoke_too_much: nói quá dài/quá nhiều so với nhu cầu không.
    - hid_too_much: im/né/thiếu hành động khi nên trả lời không.
    - modality_mismatch: body/action có phản speech không.
    - cultural_misread: hiểu sai norm đùa/chửi/thân mật không.
    - next_time_adjustment: điều chỉnh lần sau.
    """

    matched_beliefs: bool = True
    overread_detected: bool = False
    distance_violation: bool = False
    curiosity_intrusion: bool = False
    spoke_too_much: bool = False
    hid_too_much: bool = False
    modality_mismatch: bool = False
    cultural_misread: bool = False
    next_time_adjustment: list[str] = field(default_factory=list)

    # Metadata phụ để RepairMemory và debug dùng được.
    failure_type: FailureType = "none"
    severity: float = 0.0
    confidence: float = 0.5
    evidence: list[str] = field(default_factory=list)
    wrong_assumption: str = ""
    repair_strategy: str = ""
    future_suppression_rule: str = ""
    should_create_repair_memory: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_any(cls, value: Any) -> "SelfObservationReport":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            allowed = {field.name for field in cls.__dataclass_fields__.values()}
            return cls(**{key: val for key, val in value.items() if key in allowed})
        return cls()


class SelfObservationEngine:
    """
    Phân loại lỗi của ERIS từ frame + outcome.

    Cách dùng:
        frame.outcome = outcome_observer.observe(frame)
        frame.self_observation = self_observer.reflect(frame)

    Nếu muốn gửi lỗi vào repair memory:
        self_observer.append_repair_commit_request(frame)

    Lưu ý:
    - Helper append chỉ tạo StateCommitRequest.
    - Nó không gọi MemoryStore, không gọi MemoryCognitionCore.
    - Commit thật vẫn đi qua StateCommitter.
    """

    LONG_REPLY_CHARS = 900
    VERY_LONG_REPLY_CHARS = 1500

    def reflect(
        self,
        frame: Any,
        *,
        outcome: Any = None,
        attach_to_frame: bool = True,
    ) -> SelfObservationReport:
        outcome_report = self._coerce_outcome(outcome if outcome is not None else getattr(frame, "outcome", None))
        evidence: list[str] = []
        adjustments: list[str] = []

        final_text = self._extract_final_text(frame)
        final_text_norm = self._normalize(final_text)
        text_len = len(final_text)

        grounding = getattr(frame, "grounding", None)
        modality = getattr(frame, "modality", None)
        action_plan = getattr(frame, "action_plan", None) or getattr(frame, "agency", None)
        speech_core = getattr(frame, "speech_core", None) or getattr(frame, "speech_plan", None)
        tom = getattr(frame, "tom", None)
        sociocultural = getattr(frame, "sociocultural", None)

        unsupported_claims = self._read_list(grounding, "unsupported_claims")
        factual_risk = self._read_float(grounding, "factual_risk", default=0.0)
        overcertainty_risk = self._read_float(grounding, "overcertainty_risk", default=0.0)
        permission_violation = self._read_float(grounding, "permission_violation", default=0.0)
        tone_violation = self._read_float(grounding, "tone_violation", default=0.0)
        body_action_mismatch = self._read_float(grounding, "body_action_mismatch", default=0.0)
        contradiction_risk = self._read_float(modality, "contradiction_risk", default=0.0)

        global_overread_risk = self._read_float(tom, "global_overread_risk", default=0.0)
        global_tom_confidence = self._read_float(tom, "global_confidence", default=1.0)

        risk_flags = tuple(str(item) for item in (getattr(action_plan, "risk_flags", ()) or ()))
        notes = tuple(str(item) for item in (getattr(action_plan, "notes", ()) or ()))
        speech_act = str(
            getattr(action_plan, "speech_act", "")
            or getattr(speech_core, "speech_act", "")
            or getattr(action_plan, "intent", "")
            or ""
        )
        utterance_type = str(
            getattr(speech_core, "utterance_type", "")
            or getattr(speech_core, "speech_act", "")
            or ""
        )
        speech_source = str(
            getattr(speech_core, "speech_source", "")
            or ("canonical_v3" if getattr(frame, "wording", None) is not None else "")
        )
        channel = str(getattr(frame, "channel", "") or "")

        user_corrected = bool(getattr(outcome_report, "user_corrected", False))
        user_confused = bool(getattr(outcome_report, "user_confused", False))
        user_intruded = getattr(outcome_report, "user_felt_intruded", None) is True
        relation_ruptured = getattr(outcome_report, "relation_ruptured", None) is True
        action_success = float(getattr(outcome_report, "action_success", 1.0) or 0.0)
        body_match_success = float(getattr(outcome_report, "body_match_success", 1.0) or 0.0)
        correction_text = str(getattr(outcome_report, "correction_text", "") or "")

        matched_beliefs = True
        overread_detected = False
        distance_violation = False
        curiosity_intrusion = False
        spoke_too_much = False
        hid_too_much = False
        modality_mismatch = False
        cultural_misread = False

        # 1. Grounding / belief match.
        if unsupported_claims:
            matched_beliefs = False
            evidence.append(f"unsupported_claims={unsupported_claims[:3]}")
            adjustments.append("Lần sau hạ certainty và bỏ claim không có evidence.")

        if factual_risk >= 0.55 or overcertainty_risk >= 0.60:
            matched_beliefs = False
            evidence.append(f"grounding_risk=factual:{factual_risk:.2f}/overcertainty:{overcertainty_risk:.2f}")
            adjustments.append("Khi factual risk cao, trả lời bằng giả thuyết hoặc yêu cầu thêm evidence.")

        # 2. Overread. ToM risk là tín hiệu để phanh TRƯỚC khi nói, không phải
        # bằng chứng rằng ERIS đã thật sự mind-read trên surface. Chỉ ghi failure
        # khi output mang hình dạng suy diễn hoặc user trực tiếp sửa điều đó.
        surface_mindread = self._contains_mindread_shape(final_text_norm)
        corrected_overread = user_corrected and self._mentions_overread(correction_text)
        risk_became_surface = surface_mindread and (global_overread_risk >= 0.60 or "anti_mindreading" in risk_flags)
        if corrected_overread or risk_became_surface:
            overread_detected = True
            matched_beliefs = False
            evidence.append(f"overread_signal=tom:{global_overread_risk:.2f}/correction:{correction_text[:120]}")
            adjustments.append("Giảm suy diễn tâm lý; bám vào yêu cầu bề mặt trước.")

        if global_tom_confidence < 0.45 and surface_mindread:
            overread_detected = True
            evidence.append(f"low_tom_confidence_with_mindread_shape={global_tom_confidence:.2f}")
            adjustments.append("Không nói trực tiếp mental-state khi ToM confidence thấp.")

        # 3. Intrusion / distance.
        if user_intruded or permission_violation >= 0.50:
            distance_violation = True
            evidence.append(f"distance_or_permission_violation=permission:{permission_violation:.2f}/intruded:{user_intruded}")
            adjustments.append("Giảm intensity, không hỏi đào sâu nếu user không mời.")

        if user_intruded and self._contains_question_or_probe(final_text_norm):
            curiosity_intrusion = True
            evidence.append("curiosity_intrusion=user_intruded_after_probe")
            adjustments.append("Khi user đã khó chịu, ưu tiên sửa sai thay vì hỏi thêm.")

        # 4. Nói quá nhiều / quá dài.
        if text_len >= self.VERY_LONG_REPLY_CHARS:
            spoke_too_much = True
            evidence.append(f"very_long_reply_chars={text_len}")
            adjustments.append("Nén câu trả lời; tách phần dài thành mục rõ hoặc chỉ đưa phần cần làm.")
        elif text_len >= self.LONG_REPLY_CHARS and (user_confused or user_corrected):
            spoke_too_much = True
            evidence.append(f"long_reply_with_negative_feedback_chars={text_len}")
            adjustments.append("Khi user sửa/khó hiểu, trả lời ngắn hơn và đi thẳng vào phần sai.")

        if "overexplained" in risk_flags or any("overexplained" in note for note in notes):
            spoke_too_much = True
            evidence.append("risk_flag_or_note=overexplained")
            adjustments.append("Giảm giải thích nền nếu user đang yêu cầu execute.")

        # 5. Im/né/thiếu hành động.
        if action_success < 0.45:
            hid_too_much = True
            evidence.append(f"action_success_low={action_success:.2f}")
            adjustments.append("Nếu system action thất bại, nói rõ fail và đưa bước sửa.")
        if channel == "chat" and not final_text.strip() and utterance_type not in {"no_speech", "silence"}:
            hid_too_much = True
            evidence.append("chat_without_final_text")
            adjustments.append("Chat trực tiếp cần có phản hồi hoặc lý do không thể trả lời.")
        if user_corrected and self._mentions_underacted(correction_text):
            hid_too_much = True
            evidence.append(f"user_correction_underacted={correction_text[:120]}")
            adjustments.append("Khi user nói thiếu/chưa làm, ưu tiên hoàn thành phần thiếu.")

        # 6. Body/action mismatch.
        if body_match_success < 0.50 or body_action_mismatch >= 0.55 or contradiction_risk >= 0.55:
            modality_mismatch = True
            evidence.append(
                f"modality_mismatch=body_success:{body_match_success:.2f}/body_guard:{body_action_mismatch:.2f}/contradiction:{contradiction_risk:.2f}"
            )
            adjustments.append("Body action phải qua modality sync; speech thân mật không đi kèm body xâm lấn.")

        # 7. Cultural misread.
        norm_conflict = self._read_list(sociocultural, "norm_conflict")
        if norm_conflict:
            cultural_misread = True
            evidence.append(f"sociocultural_norm_conflict={norm_conflict[:3]}")
            adjustments.append("Đọc lại register/ngữ cảnh văn hóa trước khi gán ý nghĩa cảm xúc.")
        if user_corrected and self._mentions_culture_or_tone(correction_text):
            cultural_misread = True
            evidence.append(f"user_correction_culture_or_tone={correction_text[:120]}")
            adjustments.append("Với tiếng Việt casual/profanity, đừng moral panic hoặc đọc thành công kích quá sớm.")

        # 8. Tone mismatch / rupture.
        if tone_violation >= 0.55 or relation_ruptured:
            evidence.append(f"tone_or_relation_rupture=tone:{tone_violation:.2f}/ruptured:{relation_ruptured}")
            adjustments.append("Nếu có rupture, acknowledge ngắn và sửa hành vi, không phòng thủ.")

        # 9. Assistant flattening / overhumanization.
        assistant_flattening = self._detect_assistant_flattening(final_text_norm, frame)
        overhumanization = self._detect_overhumanization(final_text_norm)
        if assistant_flattening:
            evidence.append("assistant_flattening_detected")
            adjustments.append("Giữ nhịp riêng nhưng vẫn answer-first; không biến thành support bot chung chung.")
        if overhumanization:
            matched_beliefs = False
            evidence.append("overhumanization_detected")
            adjustments.append("Không claim cảm xúc/sinh học/người thật; diễn đạt bằng runtime pressure nếu cần.")

        failure_type = self._classify_failure(
            user_intruded=user_intruded,
            modality_mismatch=modality_mismatch,
            cultural_misread=cultural_misread,
            overread_detected=overread_detected,
            spoke_too_much=spoke_too_much,
            hid_too_much=hid_too_much,
            relation_ruptured=relation_ruptured,
            tone_violation=tone_violation,
            assistant_flattening=assistant_flattening,
            overhumanization=overhumanization,
            matched_beliefs=matched_beliefs,
            user_corrected=user_corrected,
            user_confused=user_confused,
        )

        severity = self._estimate_severity(
            failure_type=failure_type,
            user_corrected=user_corrected,
            user_confused=user_confused,
            user_intruded=user_intruded,
            relation_ruptured=relation_ruptured,
            factual_risk=factual_risk,
            permission_violation=permission_violation,
            body_match_success=body_match_success,
        )

        confidence = self._estimate_confidence(
            outcome_report=outcome_report,
            evidence_count=len(evidence),
            user_corrected=user_corrected,
            user_intruded=user_intruded,
            relation_ruptured=relation_ruptured,
            grounding_present=grounding is not None,
            modality_present=modality is not None,
        )

        wrong_assumption = self._derive_wrong_assumption(
            failure_type=failure_type,
            correction_text=correction_text,
            unsupported_claims=unsupported_claims,
            final_text=final_text,
            speech_act=speech_act,
            speech_source=speech_source,
        )
        repair_strategy = self._repair_strategy_for(failure_type)
        future_suppression_rule = self._future_suppression_for(failure_type)

        # Dedup adjustment nhưng giữ thứ tự.
        adjustments = self._dedupe(adjustments)[:8]
        evidence = self._dedupe(evidence)[:14]

        report = SelfObservationReport(
            matched_beliefs=matched_beliefs,
            overread_detected=overread_detected,
            distance_violation=distance_violation,
            curiosity_intrusion=curiosity_intrusion,
            spoke_too_much=spoke_too_much,
            hid_too_much=hid_too_much,
            modality_mismatch=modality_mismatch,
            cultural_misread=cultural_misread,
            next_time_adjustment=adjustments,
            failure_type=failure_type,
            severity=self._clamp01(severity),
            confidence=self._clamp01(confidence),
            evidence=evidence,
            wrong_assumption=wrong_assumption,
            repair_strategy=repair_strategy,
            future_suppression_rule=future_suppression_rule,
            should_create_repair_memory=bool(failure_type != "none" and severity >= 0.35),
        )

        if attach_to_frame:
            self._safe_setattr(frame, "self_observation", report)

        return report

    def append_repair_commit_request(
        self,
        frame: Any,
        *,
        report: SelfObservationReport | None = None,
        evidence_ids: list[str] | None = None,
    ) -> Any:
        """
        Tạo và append StateCommitRequest target=repair_memory nếu cần.

        Hàm này chỉ tạo request. Commit thật vẫn do StateCommitter xử lý.
        """

        report = report or SelfObservationReport.from_any(getattr(frame, "self_observation", None))
        if not report.should_create_repair_memory:
            return None

        request = self.build_repair_commit_request(frame, report=report, evidence_ids=evidence_ids)
        add_request = getattr(frame, "add_commit_request", None)
        if callable(add_request):
            add_request(request)
        else:
            requests = list(getattr(frame, "commit_requests", []) or [])
            requests.append(request)
            self._safe_setattr(frame, "commit_requests", requests)
        return request

    def build_repair_commit_request(
        self,
        frame: Any,
        *,
        report: SelfObservationReport,
        evidence_ids: list[str] | None = None,
    ) -> Any:
        """
        Build request tương thích StateCommitRequest.

        Ưu tiên import `core.evidence.StateCommitRequest`. Nếu repo chưa có
        hoặc đang chạy file rời, fallback thành dict cùng shape.
        """

        content = self._render_repair_content(frame, report)
        confidence = max(0.25, min(0.95, report.confidence))
        ids = evidence_ids or self._collect_evidence_ids(frame)

        payload = {
            "target": "repair_memory",
            "content": content,
            "confidence": confidence,
            "evidence_ids": ids,
            "permission_required": False,
            "storage_permission": True,
            "decay_policy": "slow" if report.severity >= 0.65 else "normal",
            "reason": f"self_observation:{report.failure_type}",
        }

        try:
            from core.evidence import StateCommitRequest  # type: ignore

            return StateCommitRequest(**payload)
        except Exception:
            return payload

    def _render_repair_content(self, frame: Any, report: SelfObservationReport) -> str:
        frame_id = str(getattr(frame, "cycle_id", "") or getattr(frame, "frame_id", "") or "")
        channel = str(getattr(frame, "channel", "") or "")
        final_text = self._extract_final_text(frame)
        parts = [
            f"failure_type={report.failure_type}",
            f"severity={report.severity:.2f}",
            f"frame={frame_id}",
            f"channel={channel}",
        ]
        if report.wrong_assumption:
            parts.append(f"wrong_assumption={report.wrong_assumption}")
        if report.repair_strategy:
            parts.append(f"repair_strategy={report.repair_strategy}")
        if report.future_suppression_rule:
            parts.append(f"future_suppression_rule={report.future_suppression_rule}")
        if final_text:
            parts.append(f"said={final_text[:220]}")
        if report.evidence:
            parts.append(f"evidence={' | '.join(report.evidence[:4])}")
        return "\n".join(parts)[:1600]

    def _classify_failure(
        self,
        *,
        user_intruded: bool,
        modality_mismatch: bool,
        cultural_misread: bool,
        overread_detected: bool,
        spoke_too_much: bool,
        hid_too_much: bool,
        relation_ruptured: bool,
        tone_violation: float,
        assistant_flattening: bool,
        overhumanization: bool,
        matched_beliefs: bool,
        user_corrected: bool,
        user_confused: bool,
    ) -> FailureType:
        # Thứ tự ưu tiên quan trọng: lỗi xâm phạm/claim sai/body mismatch phải thắng lỗi style nhẹ.
        if user_intruded:
            return "privacy_intrusion"
        if modality_mismatch:
            return "body_mismatch"
        if cultural_misread:
            return "culture_misread"
        if overhumanization:
            return "overhumanization"
        if overread_detected:
            return "overread"
        if spoke_too_much:
            return "overexplained"
        if hid_too_much:
            return "underacted"
        if relation_ruptured or tone_violation >= 0.55:
            return "tone_mismatch"
        if assistant_flattening:
            return "assistant_flattening"
        if not matched_beliefs and user_corrected:
            return "misread"
        if user_confused:
            return "wrong_abstraction"
        return "none"

    def _estimate_severity(
        self,
        *,
        failure_type: FailureType,
        user_corrected: bool,
        user_confused: bool,
        user_intruded: bool,
        relation_ruptured: bool,
        factual_risk: float,
        permission_violation: float,
        body_match_success: float,
    ) -> float:
        if failure_type == "none":
            return 0.0
        severity = 0.28
        if user_corrected:
            severity += 0.18
        if user_confused:
            severity += 0.10
        if user_intruded:
            severity += 0.26
        if relation_ruptured:
            severity += 0.18
        severity += max(0.0, factual_risk - 0.45) * 0.42
        severity += max(0.0, permission_violation - 0.40) * 0.46
        severity += max(0.0, 0.60 - body_match_success) * 0.32

        if failure_type in {"privacy_intrusion", "overhumanization", "body_mismatch"}:
            severity += 0.12
        if failure_type in {"assistant_flattening", "wrong_abstraction"}:
            severity -= 0.05
        return severity

    def _estimate_confidence(
        self,
        *,
        outcome_report: Any,
        evidence_count: int,
        user_corrected: bool,
        user_intruded: bool,
        relation_ruptured: bool,
        grounding_present: bool,
        modality_present: bool,
    ) -> float:
        confidence = 0.42
        outcome_conf = self._read_float(outcome_report, "confidence", default=0.5)
        confidence += (outcome_conf - 0.5) * 0.30
        confidence += min(0.18, evidence_count * 0.025)
        if user_corrected or user_intruded or relation_ruptured:
            confidence += 0.12
        if grounding_present:
            confidence += 0.05
        if modality_present:
            confidence += 0.03
        return confidence

    def _derive_wrong_assumption(
        self,
        *,
        failure_type: FailureType,
        correction_text: str,
        unsupported_claims: list[str],
        final_text: str,
        speech_act: str,
        speech_source: str,
    ) -> str:
        if correction_text:
            return f"ERIS framed the previous turn in a way user corrected: {correction_text[:240]}"
        if unsupported_claims:
            return f"ERIS surfaced unsupported claim(s): {', '.join(unsupported_claims[:3])}"
        if failure_type == "overread":
            return "ERIS inferred hidden mental/emotional state beyond available evidence."
        if failure_type == "privacy_intrusion":
            return "ERIS treated curiosity or relational pressure as permission to probe."
        if failure_type == "overexplained":
            return "ERIS assumed more explanation was useful than the current turn needed."
        if failure_type == "underacted":
            return "ERIS failed to answer/execute directly enough for the current request."
        if failure_type == "body_mismatch":
            return "ERIS allowed body/action plan to contradict speech or relational distance."
        if failure_type == "tone_mismatch":
            return "ERIS chose a tone that did not match the user's current tolerance."
        if failure_type == "assistant_flattening":
            return "ERIS flattened into generic assistant behavior instead of grounded runtime presence."
        if failure_type == "overhumanization":
            return "ERIS used wording that implied human/biological emotion or consciousness too strongly."
        if final_text:
            return f"Potential issue in speech_act={speech_act or '<unknown>'}, source={speech_source or '<unknown>'}."
        return ""

    def _repair_strategy_for(self, failure_type: FailureType) -> str:
        strategies: dict[str, str] = {
            "none": "",
            "misread": "Acknowledge correction briefly, discard wrong frame, answer current intent directly.",
            "overread": "Use grounded wording; keep mental-state hypotheses private unless user asks.",
            "overexplained": "Compress response and prioritize the missing actionable part.",
            "underacted": "Execute the requested change before explaining architecture.",
            "tone_mismatch": "Lower intensity, remove jab/tease, repair without defensiveness.",
            "wrong_abstraction": "Translate abstraction into concrete implementation steps.",
            "privacy_intrusion": "Stop probing; ask permission before personal/relational inference.",
            "body_mismatch": "Route speech/body through modality sync and reduce intrusive motion.",
            "culture_misread": "Re-evaluate Vietnamese register/profanity/playfulness before judging tone.",
            "assistant_flattening": "Restore identity rhythm lightly while keeping answer-first behavior.",
            "overhumanization": "Replace human-emotion claims with runtime pressure/behavioral descriptions.",
        }
        return strategies.get(failure_type, "")

    def _future_suppression_for(self, failure_type: FailureType) -> str:
        rules: dict[str, str] = {
            "none": "",
            "misread": "Do not reuse the invalidated interpretation without fresh evidence.",
            "overread": "Suppress direct emotional/intent inference when confidence is below threshold.",
            "overexplained": "When user requests a file/change, output artifact first and keep explanation short.",
            "underacted": "Do not stop at analysis when user asked for implementation.",
            "tone_mismatch": "Disable tease/dry jab during repair, distress, or explicit irritation.",
            "wrong_abstraction": "Prefer concrete code/file diff over symbolic framing for technical requests.",
            "privacy_intrusion": "Do not ask probing personal questions after user signals boundary.",
            "body_mismatch": "Suppress body action if contradiction risk is high.",
            "culture_misread": "Treat casual Vietnamese profanity as ambiguous until context clarifies target.",
            "assistant_flattening": "Avoid generic support phrasing when a grounded ERIS-style line is allowed.",
            "overhumanization": "Suppress claims of being human, biological feeling, or literal consciousness.",
        }
        return rules.get(failure_type, "")

    def _mentions_overread(self, text: str) -> bool:
        normalized = self._normalize(text)
        return any(
            marker in normalized
            for marker in (
                "dung suy dien",
                "overread",
                "mindread",
                "khong phai toi dang",
                "toi dau co",
                "ban nghi sai",
                "doan tam ly",
            )
        )

    def _mentions_underacted(self, text: str) -> bool:
        normalized = self._normalize(text)
        return any(
            marker in normalized
            for marker in (
                "thieu",
                "chua lam",
                "chua them",
                "lam di",
                "tao bao tao can file",
                "gui file",
                "khong phai giai thich",
                "can code",
            )
        )

    def _mentions_culture_or_tone(self, text: str) -> bool:
        normalized = self._normalize(text)
        return any(
            marker in normalized
            for marker in (
                "noi khay",
                "moc mia",
                "ngu eq",
                "khong phai cam xuc",
                "dung khuyen",
                "dung moral",
                "gioi tre",
                "tieng viet",
                "joke",
                "dua",
            )
        )

    def _contains_mindread_shape(self, normalized_text: str) -> bool:
        return any(
            marker in normalized_text
            for marker in (
                "ban muon",
                "ban dang",
                "co ve ban",
                "toi biet ban",
                "that ra ban",
                "you want",
                "you are feeling",
                "it seems you are",
            )
        )

    def _contains_question_or_probe(self, normalized_text: str) -> bool:
        return "?" in normalized_text or any(
            marker in normalized_text
            for marker in (
                "tai sao",
                "vi sao",
                "ke toi nghe",
                "noi ro hon",
                "ban co dang",
                "ban co dang",
            )
        )

    def _detect_assistant_flattening(self, normalized_text: str, frame: Any) -> bool:
        if not normalized_text:
            return False
        generic_markers = (
            "toi co the giup",
            "neu ban can",
            "hay cho toi biet",
            "rat vui duoc ho tro",
            "duoi day la",
        )
        generic_hits = sum(1 for marker in generic_markers if marker in normalized_text)
        channel = str(getattr(frame, "channel", "") or "")
        return generic_hits >= 2 and channel in {"chat", "force_speak", "autonomous"}

    def _detect_overhumanization(self, normalized_text: str) -> bool:
        if not normalized_text:
            return False
        forbidden_shapes = (
            "toi la con nguoi",
            "co trai tim",
            "cam xuc that cua toi",
            "toi dau don nhu nguoi",
            "co linh hon",
            "i am human",
            "my real feelings",
            "my heart",
        )
        return any(marker in normalized_text for marker in forbidden_shapes)

    def _collect_evidence_ids(self, frame: Any) -> list[str]:
        ids: list[str] = []
        for item in tuple(getattr(frame, "evidence", ()) or ()):  # type: ignore[arg-type]
            item_id = str(getattr(item, "id", "") or "")
            if item_id:
                ids.append(item_id)
        if not ids:
            cycle_id = str(getattr(frame, "cycle_id", "") or getattr(frame, "frame_id", "") or "")
            if cycle_id:
                ids.append(cycle_id)
        return ids[:12]

    def _coerce_outcome(self, value: Any) -> Any:
        if value is None:
            try:
                from runtime.outcome_observer import OutcomeReport as RuntimeOutcomeReport

                return RuntimeOutcomeReport()
            except Exception:
                return {}
        if hasattr(value, "user_corrected") and hasattr(value, "action_success"):
            return value
        if isinstance(value, Mapping):
            try:
                from runtime.outcome_observer import OutcomeReport as RuntimeOutcomeReport

                return RuntimeOutcomeReport.from_any(value)
            except Exception:
                return value
        return value

    @staticmethod
    def _extract_final_text(frame: Any) -> str:
        final_decision = getattr(frame, "final_decision", None)
        if isinstance(final_decision, Mapping):
            text = str(final_decision.get("text", "") or "").strip()
            if text:
                return text
        decision = getattr(frame, "decision", None)
        text = str(getattr(decision, "text", "") or "").strip()
        if text:
            return text
        wording = getattr(frame, "wording", None)
        if isinstance(wording, Mapping):
            return str(wording.get("parsed_text") or wording.get("raw_text") or "").strip()
        return str(getattr(wording, "parsed_text", None) or getattr(wording, "raw_text", "") or "").strip()

    @staticmethod
    def _read_float(obj: Any, key: str, *, default: float = 0.0) -> float:
        if obj is None:
            return default
        if isinstance(obj, Mapping):
            value = obj.get(key, default)
        else:
            value = getattr(obj, key, default)
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _read_list(obj: Any, key: str) -> list[str]:
        if obj is None:
            return []
        if isinstance(obj, Mapping):
            value = obj.get(key, [])
        else:
            value = getattr(obj, key, [])
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value else []
        try:
            return [str(item) for item in value if str(item)]
        except Exception:
            return []

    @staticmethod
    def _safe_setattr(obj: Any, key: str, value: Any) -> None:
        try:
            setattr(obj, key, value)
        except Exception:
            return

    @staticmethod
    def _normalize(text: str) -> str:
        text = str(text or "").strip().lower()
        text = unicodedata.normalize("NFD", text)
        text = "".join(char for char in text if unicodedata.category(char) != "Mn")
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            key = str(item).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(key)
        return result

    @staticmethod
    def _clamp01(value: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0

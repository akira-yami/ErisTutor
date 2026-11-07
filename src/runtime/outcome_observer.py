from __future__ import annotations

"""
runtime/outcome_observer.py

Tầng này đọc kết quả sau một lượt chạy của ERIS.

Nguyên tắc thiết kế:
- OutcomeObserver chỉ quan sát và phân loại hậu quả.
- Không ghi memory trực tiếp.
- Không sửa frame theo kiểu side-effect nặng, ngoại trừ gắn `frame.outcome`
  nếu frame có field đó.
- Những kết luận cần phản hồi của user phải để `None` hoặc confidence thấp
  khi chưa có tín hiệu user thật.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional


@dataclass(slots=True)
class OutcomeReport:
    """
    Báo cáo hậu quả sau một turn.

    Các field lõi bám theo ERIS Cognitive Pipeline:
    - user_accepted: user có chấp nhận/đồng ý với câu trả lời không.
      `None` nghĩa là chưa đủ tín hiệu.
    - user_corrected: user có sửa ERIS không.
    - user_ignored: user có bỏ qua phản hồi không.
      Field này chỉ nên bật khi runtime có tín hiệu idle/session thật.
    - user_laughed: user có phản ứng hài/hưởng ứng không.
    - user_confused: user có vẻ khó hiểu/lạc nhịp không.
    - user_felt_intruded: user có cảm giác bị xen vào/bị suy diễn/bị xâm phạm không.
      `None` nghĩa là chưa đủ tín hiệu.
    - user_soothed: user có đang dỗ/trấn an/xoa dịu ERIS không (vd "đừng lo",
      "không sao đâu", "tôi ở đây"). `None` nghĩa là chưa đủ tín hiệu. Đây là tín
      hiệu để private mind cho nỗi lo nguôi đi thay vì pegged mãi.
    - relation_improved / relation_ruptured: quan hệ có tốt lên/rạn đi không.
      `None` nghĩa là chưa đủ tín hiệu.
    - action_success: hành động/text/system action có thực thi ổn không.
    - body_match_success: body có khớp speech/action không.
    - evidence: các mẩu bằng chứng ngắn, dùng cho audit/debug.
    """

    user_accepted: Optional[bool] = None
    user_corrected: bool = False
    user_ignored: bool = False
    user_laughed: bool = False
    user_confused: bool = False
    user_felt_intruded: Optional[bool] = None
    user_soothed: Optional[bool] = None
    relation_improved: Optional[bool] = None
    relation_ruptured: Optional[bool] = None

    # Projection mềm từ Unified Social Appraisal. Các bool phía trên được giữ
    # cho compatibility, còn learning/observability mới nên đọc các pressure này.
    correction_pressure: float = 0.0
    confusion_pressure: float = 0.0
    laughter_signal: float = 0.0
    intrusion_pressure: float = 0.0
    rupture_pressure: float = 0.0
    acceptance_pressure: float = 0.0
    social_appraisal: dict[str, Any] = field(default_factory=dict)

    action_success: float = 1.0
    body_match_success: float = 1.0
    evidence: list[str] = field(default_factory=list)

    # Metadata phụ để self_observation phân loại lỗi chính xác hơn.
    confidence: float = 0.5
    correction_text: str = ""
    feedback_text: str = ""
    ignored_reason: str = ""
    accepted_reason: str = ""
    rupture_reason: str = ""
    tags: list[str] = field(default_factory=list)
    decision_id: str = ""
    action_id: str = ""
    thread_id: str = ""
    intent_id: str = ""
    delivery_id: str = ""
    delivery_success: float = 1.0
    reply_expected: bool = False
    delayed_reply: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_world_outcome(
        self,
        *,
        outcome_id: str,
        started_at: float,
        completed_at: float,
        decision_id: str | None = None,
        action_id: str | None = None,
        delivery_id: str | None = None,
    ):
        """Adapt the operational report to the Subject Core world contract."""
        from cortex.subject.contracts import OutcomeStatus, WorldOutcome

        success = min(float(self.action_success), float(self.delivery_success), float(self.body_match_success))
        status = OutcomeStatus.SUCCEEDED if success >= 0.99 else OutcomeStatus.PARTIAL if success > 0.0 else OutcomeStatus.FAILED
        return WorldOutcome(
            outcome_id=outcome_id,
            action_id=str(action_id or self.action_id or self.intent_id or outcome_id),
            decision_id=str(decision_id or self.decision_id or "legacy-decision"),
            status=status,
            observed_changes=tuple(self.tags),
            unintended_changes=tuple(tag for tag in self.tags if tag.startswith("unexpected:")),
            denial_reason=None,
            evidence_refs=tuple(self.evidence),
            started_at=started_at,
            completed_at=completed_at,
            thread_id=self.thread_id,
            delivery_id=str(delivery_id or self.delivery_id),
        )

    @classmethod
    def from_any(cls, value: Any) -> "OutcomeReport":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            allowed = {field.name for field in cls.__dataclass_fields__.values()}
            return cls(**{key: val for key, val in value.items() if key in allowed})
        return cls()


class OutcomeObserver:
    """
    Đọc tín hiệu hậu quả sau một turn.

    Cách dùng thường gặp:
        outcome = outcome_observer.observe(frame)
        frame.outcome = outcome

    Nếu muốn đánh giá phản ứng user đối với turn trước:
        outcome = outcome_observer.observe(previous_frame, user_feedback_text=current_user_text)

    Tầng này không nên đoán quá sâu. Nếu thiếu feedback của user,
    các field như accepted/corrected/ruptured phải giữ ở mức không chắc.
    """

    ACCEPTANCE_PATTERNS = (
        "đúng", "dung", "chuẩn", "chuan", "hợp lý", "hop ly",
        "ok", "oke", "được", "duoc", "ngon", "ổn", "on",
        "đúng rồi", "dung roi", "chuẩn rồi", "chuan roi",
        "hợp lý đó", "hop ly do", "tiếp", "tiếp tục", "tiep tuc",
    )

    CORRECTION_PATTERNS = (
        "không phải", "khong phai", "sai rồi", "sai roi", "sai",
        "thiếu", "thieu", "chưa đúng", "chua dung", "chưa phải", "chua phai",
        "đọc lại", "doc lai", "lệch", "lech", "nhầm", "nham",
        "không hiểu ý", "khong hieu y", "ý tôi là", "y toi la",
        "no, ", "not what", "wrong", "incorrect", "you missed",
    )

    CONFUSION_PATTERNS = (
        "khó hiểu", "kho hieu", "gì vậy", "gi vay", "là sao", "la sao",
        "nói gì", "noi gi", "không hiểu", "khong hieu", "wtf",
        "what do you mean", "confused",
    )

    LAUGHTER_PATTERNS = (
        "haha", "hahaha", "hehe", "hihi", "kkk", "kaka", "lol", "lmao",
        "cười", "cuoi", "hề", "he",
    )

    INTRUSION_PATTERNS = (
        "đừng suy diễn", "dung suy dien", "đừng hỏi", "dung hoi",
        "đừng nói kháy", "dung noi khay", "nói kháy", "noi khay",
        "móc mỉa", "moc mia", "phiền", "phien", "xen vào", "xen vao",
        "overread", "mindread", "intrusive", "privacy",
        "đừng phân tích", "dung phan tich",
    )

    RUPTURE_PATTERNS = (
        "khó chịu", "kho chiu", "bực", "buc", "cay", "ghét", "ghet",
        "tức", "tuc", "ngu eq", "nói như", "noi nhu",
        "chán", "chan", "thất vọng", "that vong",
    )

    IMPROVEMENT_PATTERNS = (
        "cảm ơn", "cam on", "thanks", "thank you",
        "đúng ý", "dung y", "hợp gu", "hop gu",
        "nghe được", "nghe duoc", "ổn rồi", "on roi",
    )

    # User đang dỗ/trấn an/xoa dịu ERIS (chứ không phải đánh giá câu trả lời).
    # Cố ý tránh các cụm trùng với CORRECTION (vd "không phải") để khỏi bị hiểu nhầm
    # thành sửa lỗi.
    COMFORT_PATTERNS = (
        "đừng lo", "dung lo", "đừng buồn", "dung buon", "đừng căng", "dung cang",
        "đừng nghĩ nhiều", "dung nghi nhieu", "đừng tự trách", "dung tu trach",
        "không sao", "khong sao", "không sao đâu", "khong sao dau",
        "bình tĩnh", "binh tinh", "yên tâm", "yen tam", "ổn mà", "on ma",
        "tôi ở đây", "toi o day", "có tôi đây", "co toi day", "anh ở đây", "anh o day",
        "nghỉ đi", "nghi di", "nghỉ ngơi", "nghi ngoi", "thư giãn", "thu gian",
        "thả lỏng", "tha long", "thôi nào", "thoi nao", "đừng áp lực", "dung ap luc",
    )

    NEGATED_ACCEPTANCE_PATTERNS = (
        "ok nhưng sai", "ok nhung sai", "đúng nhưng", "dung nhung",
        "chuẩn nhưng", "chuan nhung", "hợp lý nhưng", "hop ly nhung",
    )

    def observe(
        self,
        frame: Any,
        *,
        user_feedback_text: str | None = None,
        execution_result: Any = None,
        body_result: Any = None,
        idle_seconds_after_turn: float | None = None,
        attach_to_frame: bool = True,
        social_appraisal: Any = None,
        performance_intent: Any = None,
        delivery_result: Any = None,
        user_activity: Any = None,
    ) -> OutcomeReport:
        """
        Tạo OutcomeReport từ frame và các tín hiệu hậu quả.

        Parameters
        ----------
        frame:
            CognitiveFrame hoặc object tương thích.
        user_feedback_text:
            Phản hồi user sau turn này. Nếu chưa có thì để None.
        execution_result:
            Kết quả system action nếu có. Có thể là dict/dataclass có `success`.
        body_result:
            Kết quả body/modality nếu có. Có thể là dict/dataclass có `matched` hoặc `success`.
        idle_seconds_after_turn:
            Nếu runtime biết user im/bỏ qua sau turn bao lâu, truyền vào đây.
        attach_to_frame:
            Nếu True, gắn report vào `frame.outcome` khi có thể.
        """

        evidence: list[str] = []
        tags: list[str] = []

        if isinstance(performance_intent, Mapping):
            intent_data = dict(performance_intent)
        else:
            to_dict = getattr(performance_intent, "to_dict", None)
            try:
                intent_data = dict(to_dict() or {}) if callable(to_dict) else {}
            except Exception:
                intent_data = {}
        thread_id = str(intent_data.get("thread_id", getattr(frame, "thread_id", "")) or "")
        intent_id = str(intent_data.get("intent_id", getattr(frame, "intent_id", "")) or "")
        reply_expected = bool(
            intent_data.get("require_reply", False)
            or getattr(frame, "require_reply", False)
            or getattr(frame, "reply_expected", False)
        )
        delivery_success = 1.0
        if delivery_result is not None:
            delivery_flag = self._read_bool(delivery_result, "success")
            delivery_score = self._read_float(delivery_result, "success_score")
            if delivery_flag is not None:
                delivery_success = 1.0 if delivery_flag else 0.0
            elif delivery_score is not None:
                delivery_success = self._clamp01(delivery_score)
        if intent_id:
            evidence.append(f"intent_id={intent_id}")
        if thread_id:
            evidence.append(f"thread_id={thread_id}")

        final_text = self._extract_final_text(frame)
        system_action = self._extract_system_action(frame)

        action_success = self._infer_action_success(
            frame,
            final_text=final_text,
            system_action=system_action,
            execution_result=execution_result,
            evidence=evidence,
            tags=tags,
        )
        body_match_success = self._infer_body_match_success(
            frame,
            body_result=body_result,
            evidence=evidence,
            tags=tags,
        )

        feedback = str(user_feedback_text or "").strip()
        normalized_feedback = self._normalize(feedback)

        user_corrected = False
        user_accepted: Optional[bool] = None
        user_laughed = False
        user_confused = False
        user_felt_intruded: Optional[bool] = None
        user_soothed: Optional[bool] = None
        relation_improved: Optional[bool] = None
        relation_ruptured: Optional[bool] = None
        correction_text = ""
        accepted_reason = ""
        rupture_reason = ""

        correction_pressure = 0.0
        confusion_pressure = 0.0
        laughter_signal = 0.0
        intrusion_pressure = 0.0
        rupture_pressure = 0.0
        acceptance_pressure = 0.0
        social_appraisal_dict: dict[str, Any] = {}

        if feedback:
            # Khi có snapshot chung, OutcomeObserver không được tự đoán lại bằng
            # regex. Bool cũ chỉ là operational projection để code legacy còn chạy.
            if social_appraisal is not None:
                thresholds = getattr(social_appraisal, "thresholds", None)
                correction_pressure = max(
                    self._clamp01(getattr(getattr(social_appraisal, "correction", None), "value", 0.0)),
                    self._clamp01(getattr(getattr(social_appraisal, "claim", None), "contest", 0.0)),
                    self._clamp01(getattr(getattr(social_appraisal, "claim", None), "contradiction", 0.0)),
                )
                confusion_pressure = self._clamp01(getattr(getattr(social_appraisal, "confusion", None), "value", 0.0))
                laughter_signal = self._clamp01(getattr(getattr(social_appraisal, "playful", None), "value", 0.0))
                intrusion_pressure = self._clamp01(getattr(getattr(social_appraisal, "intrusion", None), "value", 0.0))
                rupture_pressure = self._clamp01(getattr(getattr(social_appraisal, "rupture", None), "value", 0.0))
                acceptance_pressure = self._clamp01(getattr(getattr(social_appraisal, "acceptance", None), "value", 0.0))
                repair_gate = float(getattr(thresholds, "repair_active", 0.46) or 0.46)
                failure_gate = float(getattr(thresholds, "failure_gate", 0.58) or 0.58)
                user_corrected = correction_pressure >= repair_gate
                user_confused = confusion_pressure >= repair_gate
                user_laughed = laughter_signal >= 0.48
                intruded = intrusion_pressure >= failure_gate
                ruptured = rupture_pressure >= failure_gate
                accepted = acceptance_pressure >= 0.52 and correction_pressure < repair_gate
                negated_acceptance = correction_pressure >= repair_gate
                improved = accepted and rupture_pressure < 0.30
                try:
                    social_appraisal_dict = social_appraisal.to_dict()
                except Exception:
                    social_appraisal_dict = {}
            else:
                # Fallback chỉ dành cho caller legacy chưa nối Unified Appraisal.
                user_corrected = self._has_any(normalized_feedback, self.CORRECTION_PATTERNS)
                user_laughed = self._has_any(normalized_feedback, self.LAUGHTER_PATTERNS)
                user_confused = self._has_any(normalized_feedback, self.CONFUSION_PATTERNS)
                intruded = self._has_any(normalized_feedback, self.INTRUSION_PATTERNS)
                ruptured = self._has_any(normalized_feedback, self.RUPTURE_PATTERNS)
                improved = self._has_any(normalized_feedback, self.IMPROVEMENT_PATTERNS)
                accepted = self._has_any(normalized_feedback, self.ACCEPTANCE_PATTERNS)
                negated_acceptance = self._has_any(normalized_feedback, self.NEGATED_ACCEPTANCE_PATTERNS)
                correction_pressure = 1.0 if user_corrected else 0.0
                confusion_pressure = 1.0 if user_confused else 0.0
                laughter_signal = 1.0 if user_laughed else 0.0
                intrusion_pressure = 1.0 if intruded else 0.0
                rupture_pressure = 1.0 if ruptured else 0.0
                acceptance_pressure = 1.0 if accepted else 0.0

            if user_corrected:
                correction_text = feedback[:300]
                evidence.append(f"user_feedback_correction={feedback[:140]}")
                tags.append("user_correction")

            if user_confused:
                evidence.append(f"user_feedback_confusion={feedback[:140]}")
                tags.append("user_confused")

            if user_laughed:
                evidence.append(f"user_feedback_laughter={feedback[:140]}")
                tags.append("user_laughed")

            if intruded:
                user_felt_intruded = True
                evidence.append(f"user_feedback_intrusion={feedback[:140]}")
                tags.append("user_felt_intruded")

            # Acceptance không được thắng correction. Câu "ok nhưng sai" là correction.
            if accepted and not user_corrected and not negated_acceptance:
                user_accepted = True
                accepted_reason = "user_feedback_acceptance"
                evidence.append(f"user_feedback_acceptance={feedback[:140]}")
                tags.append("user_accepted")
            elif user_corrected or user_confused:
                user_accepted = False

            if ruptured or intruded:
                relation_ruptured = True
                rupture_reason = "user_feedback_rupture_or_intrusion"
                evidence.append(f"user_feedback_rupture={feedback[:140]}")
                tags.append("relation_ruptured")
            elif improved or (accepted and not user_corrected and not negated_acceptance):
                relation_improved = True
                evidence.append(f"user_feedback_improvement={feedback[:140]}")
                tags.append("relation_improved")

            if user_felt_intruded is None and feedback:
                user_felt_intruded = False

            # Dỗ/trấn an: user đang xoa dịu ERIS. Không tính nếu đang là correction/
            # intrusion (lúc đó câu nói mang nghĩa khác).
            soothed = self._has_any(normalized_feedback, self.COMFORT_PATTERNS)
            if soothed and not user_corrected and not intruded:
                user_soothed = True
                evidence.append(f"user_feedback_comfort={feedback[:140]}")
                tags.append("user_soothed")
            elif feedback:
                user_soothed = False

        user_ignored = False
        ignored_reason = ""
        if idle_seconds_after_turn is not None:
            try:
                idle_value = float(idle_seconds_after_turn)
            except Exception:
                idle_value = 0.0
            if isinstance(user_activity, Mapping):
                busy = bool(user_activity.get("busy") or user_activity.get("compiling") or user_activity.get("foreground_active"))
                speech_not_heard = bool(user_activity.get("speech_not_heard") or user_activity.get("audio_delivery_failed"))
                conversation_closed = bool(user_activity.get("conversation_closed"))
            else:
                busy = bool(getattr(user_activity, "busy", False) or getattr(user_activity, "compiling", False))
                speech_not_heard = bool(getattr(user_activity, "speech_not_heard", False) or getattr(user_activity, "audio_delivery_failed", False))
                conversation_closed = bool(getattr(user_activity, "conversation_closed", False))
            if idle_value >= 90.0 and (final_text or system_action):
                if not reply_expected:
                    ignored_reason = "question_did_not_require_reply"
                    tags.append("reply_not_expected")
                elif delivery_success < 0.50 or speech_not_heard:
                    ignored_reason = "speech_not_heard_or_delivery_failed"
                    tags.append("delivery_failure")
                elif busy:
                    ignored_reason = "user_busy"
                    tags.append("user_busy")
                elif conversation_closed:
                    ignored_reason = "conversation_closed"
                    tags.append("conversation_closed")
                else:
                    user_ignored = True
                    ignored_reason = f"idle_after_required_reply={idle_value:.1f}s"
                    tags.append("user_ignored")
                evidence.append(ignored_reason)

        confidence = self._estimate_confidence(
            has_feedback=bool(feedback),
            user_corrected=user_corrected,
            user_accepted=user_accepted,
            user_confused=user_confused,
            user_felt_intruded=user_felt_intruded,
            action_success=action_success,
            body_match_success=body_match_success,
        )

        report = OutcomeReport(
            user_accepted=user_accepted,
            user_corrected=user_corrected,
            user_ignored=user_ignored,
            user_laughed=user_laughed,
            user_confused=user_confused,
            user_felt_intruded=user_felt_intruded,
            user_soothed=user_soothed,
            relation_improved=relation_improved,
            relation_ruptured=relation_ruptured,
            correction_pressure=self._clamp01(correction_pressure),
            confusion_pressure=self._clamp01(confusion_pressure),
            laughter_signal=self._clamp01(laughter_signal),
            intrusion_pressure=self._clamp01(intrusion_pressure),
            rupture_pressure=self._clamp01(rupture_pressure),
            acceptance_pressure=self._clamp01(acceptance_pressure),
            social_appraisal=social_appraisal_dict,
            action_success=self._clamp01(action_success),
            body_match_success=self._clamp01(body_match_success),
            evidence=evidence[:12],
            confidence=self._clamp01(confidence),
            correction_text=correction_text,
            feedback_text=feedback[:300],
            ignored_reason=ignored_reason,
            accepted_reason=accepted_reason,
            rupture_reason=rupture_reason,
            tags=tags[:12],
            thread_id=thread_id,
            intent_id=intent_id,
            delivery_success=self._clamp01(delivery_success),
            reply_expected=reply_expected,
            delayed_reply=False,
        )

        if attach_to_frame:
            self._safe_setattr(frame, "outcome", report)

        try:
            from runtime.ai_orchestrated.autonomous_trace import append_public_turn_trace
            trace_id = str(
                getattr(frame, "utterance_turn_id", "")
                or getattr(frame, "turn_id", "")
                or getattr(frame, "director_trace_id", "")
                or ""
            )
            if trace_id:
                append_public_turn_trace("behavior_outcome", {
                    "trace_id": trace_id,
                    "turn_id": trace_id,
                    "stage": "outcome",
                    "owner": "outcome_observer",
                    "outcome": "observed",
                    "confidence": report.confidence,
                })
        except Exception:
            pass

        return report

    def observe_user_feedback_for_previous_frame(
        self,
        previous_frame: Any,
        user_feedback_text: str,
        *,
        attach_to_frame: bool = True,
        social_appraisal: Any = None,
    ) -> OutcomeReport:
        """
        Helper dành cho coordinator khi user vừa nhắn một câu phản hồi.
        Thay vì đánh giá current frame, nó đánh giá câu user đó như phản ứng
        đối với previous_frame.
        """

        return self.observe(
            previous_frame,
            user_feedback_text=user_feedback_text,
            attach_to_frame=attach_to_frame,
            social_appraisal=social_appraisal,
        )

    def _infer_action_success(
        self,
        frame: Any,
        *,
        final_text: str,
        system_action: str,
        execution_result: Any,
        evidence: list[str],
        tags: list[str],
    ) -> float:
        if execution_result is not None:
            success = self._read_bool(execution_result, "success")
            if success is not None:
                evidence.append(f"execution_success={success}")
                tags.append("execution_result")
                return 1.0 if success else 0.0

            score = self._read_float(execution_result, "success_score")
            if score is not None:
                evidence.append(f"execution_success_score={score:.2f}")
                tags.append("execution_result")
                return score

        execution = getattr(frame, "execution", None)
        if execution is not None:
            success = self._read_bool(execution, "success")
            if success is not None:
                evidence.append(f"frame_execution_success={success}")
                tags.append("frame_execution")
                return 1.0 if success else 0.0

        # Nếu có system action nhưng không có execution_result, chỉ nên coi là chưa chắc.
        if system_action:
            evidence.append(f"system_action_without_execution_result={system_action}")
            tags.append("system_action_uncertain")
            return 0.55

        # Nếu turn có text hợp lệ thì speech execution coi như thành công ở tầng output.
        if final_text:
            evidence.append("speech_text_present")
            tags.append("speech_executed")
            return 1.0

        # Im lặng có thể là hành vi đúng. Không phạt nếu speech_opportunity/action_plan nói no_speech.
        speech_core = getattr(frame, "speech_core", None)
        utterance_type = str(getattr(speech_core, "utterance_type", "") or "")
        if utterance_type in {"no_speech", "silence"}:
            evidence.append(f"intentional_silence={utterance_type}")
            tags.append("intentional_silence")
            return 1.0

        return 0.5

    def _infer_body_match_success(
        self,
        frame: Any,
        *,
        body_result: Any,
        evidence: list[str],
        tags: list[str],
    ) -> float:
        if body_result is not None:
            matched = self._read_bool(body_result, "matched")
            if matched is None:
                matched = self._read_bool(body_result, "success")
            if matched is not None:
                evidence.append(f"body_matched={matched}")
                tags.append("body_result")
                return 1.0 if matched else 0.0

            score = self._read_float(body_result, "match_score")
            if score is not None:
                evidence.append(f"body_match_score={score:.2f}")
                tags.append("body_result")
                return score

        modality = getattr(frame, "modality", None)
        contradiction_risk = self._read_float(modality, "contradiction_risk") if modality is not None else None
        if contradiction_risk is not None:
            score = 1.0 - self._clamp01(contradiction_risk)
            evidence.append(f"modality_contradiction_risk={contradiction_risk:.2f}")
            tags.append("modality")
            return score

        grounding = getattr(frame, "grounding", None)
        body_mismatch = self._read_float(grounding, "body_action_mismatch") if grounding is not None else None
        if body_mismatch is not None:
            score = 1.0 - self._clamp01(body_mismatch)
            evidence.append(f"grounding_body_action_mismatch={body_mismatch:.2f}")
            tags.append("grounding")
            return score

        return 1.0

    def _estimate_confidence(
        self,
        *,
        has_feedback: bool,
        user_corrected: bool,
        user_accepted: Optional[bool],
        user_confused: bool,
        user_felt_intruded: Optional[bool],
        action_success: float,
        body_match_success: float,
    ) -> float:
        confidence = 0.42
        if has_feedback:
            confidence += 0.28
        if user_corrected or user_confused:
            confidence += 0.12
        if user_accepted is not None:
            confidence += 0.08
        if user_felt_intruded is not None:
            confidence += 0.06
        if action_success in (0.0, 1.0):
            confidence += 0.04
        if body_match_success in (0.0, 1.0):
            confidence += 0.02
        return confidence

    @classmethod
    def _normalize(cls, text: str) -> str:
        text = str(text or "").strip().lower()
        text = unicodedata.normalize("NFD", text)
        text = "".join(char for char in text if unicodedata.category(char) != "Mn")
        text = re.sub(r"\s+", " ", text)
        return text

    @classmethod
    def _has_any(cls, normalized_text: str, patterns: tuple[str, ...]) -> bool:
        if not normalized_text:
            return False
        for pattern in patterns:
            normalized_pattern = cls._normalize(pattern)
            if normalized_pattern and normalized_pattern in normalized_text:
                return True
        return False

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
        # Canonical V3 không có final_decision; wording là source of truth sau
        # finalize. Observer cũ bỏ qua slot này nên mọi câu đã nói đều bị nhìn như
        # im lặng. Một cách khá hiệu quả để tự chẩn đoán sai chính mình.
        wording = getattr(frame, "wording", None)
        if isinstance(wording, Mapping):
            return str(wording.get("parsed_text") or wording.get("raw_text") or "").strip()
        return str(getattr(wording, "parsed_text", None) or getattr(wording, "raw_text", "") or "").strip()

    @staticmethod
    def _extract_system_action(frame: Any) -> str:
        final_decision = getattr(frame, "final_decision", None)
        if isinstance(final_decision, Mapping):
            action = str(final_decision.get("system_action", "") or "").strip()
            if action:
                return action
        action_plan = getattr(frame, "action_plan", None)
        action = str(getattr(action_plan, "system_action", "") or "").strip()
        if action:
            return action
        modality = getattr(frame, "modality", None)
        if isinstance(modality, Mapping):
            return str(modality.get("system_action", "") or "").strip()
        return str(getattr(modality, "system_action", "") or "").strip()

    @staticmethod
    def _read_bool(obj: Any, key: str) -> bool | None:
        if obj is None:
            return None
        if isinstance(obj, Mapping) and key in obj:
            value = obj.get(key)
        else:
            value = getattr(obj, key, None)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "1", "ok", "success"}:
                return True
            if lowered in {"false", "no", "0", "failed", "error"}:
                return False
        return None

    @staticmethod
    def _read_float(obj: Any, key: str) -> float | None:
        if obj is None:
            return None
        if isinstance(obj, Mapping) and key in obj:
            value = obj.get(key)
        else:
            value = getattr(obj, key, None)
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _safe_setattr(obj: Any, key: str, value: Any) -> None:
        try:
            setattr(obj, key, value)
        except Exception:
            return

    @staticmethod
    def _clamp01(value: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0

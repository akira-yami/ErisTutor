# -*- coding: utf-8 -*-
"""V85 - Mentor Calibration & Trust cho Eris Desktop.

Module này KHÔNG phải một agent/LLM judge mới. Nó chỉ đóng feedback loop của Mentor:

    intervention -> phản hồi/evidence -> outcome -> calibration

Nguyên tắc:
- không lưu raw chat hoặc source code;
- không suy "user bỏ qua" chỉ vì họ đổi chủ đề;
- user sửa Eris thì Eris phải hạ confidence thay vì tự bảo vệ nhận định;
- "tôi biết rồi" là correction/self-report, không phải mastery proof;
- metric là tín hiệu vận hành, không phải điểm số con người.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.workspace_manager import default_eris_home

_SCHEMA = "eris.mentor_calibration.v1"
_PROFILE_SCHEMA = "eris.mentor_profile.v1"

# Chỉ match feedback khá explicit. V85 cố tình bỏ semantic classifier/LLM judge để
# outcome có thể audit được và không biến một câu mơ hồ thành "mentor đúng/sai".
_HELPFUL_MARKERS = (
    "đúng rồi", "chuẩn rồi", "ra lỗi rồi", "tìm ra rồi", "fix được rồi", "sửa được rồi",
    "hữu ích", "có ích", "ờ đúng", "à đúng", "đúng thật", "ra nguyên nhân rồi",
)
_GENERAL_CORRECTION_MARKERS = (
    "sai rồi", "bạn sai", "eris sai", "bạn đoán sai", "đánh giá sai", "không phải vậy",
    "không đúng", "không phải lỗi đó", "không phải nguyên nhân đó",
)
_SKILL_CORRECTION_MARKERS = (
    "cái này tôi biết", "cái đó tôi biết", "tôi biết cái này", "tôi biết cái đó",
    "tôi biết rồi", "tôi chỉ lười", "chỉ là tôi lười", "không phải tôi không biết",
    "đừng đánh giá tôi yếu", "đánh giá kỹ năng sai",
)


@dataclass(slots=True)
class MentorOutcome:
    """Một outcome observable, không phải phán xét nội tâm user."""

    kind: str = "unknown"
    intervention_id: str = ""
    at: float = 0.0
    mode: str = ""
    primary_role: str = ""
    focus_skills: tuple[str, ...] = ()
    reason: str = ""
    confidence: float = 0.0
    evidence_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "intervention_id": self.intervention_id,
            "at": self.at,
            "mode": self.mode,
            "primary_role": self.primary_role,
            "focus_skills": list(self.focus_skills),
            "reason": self.reason,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 4),
            "evidence_codes": list(self.evidence_codes),
        }


@dataclass(slots=True)
class CalibrationDirective:
    """Cách writer nên thể hiện độ chắc chắn của Mentor ở turn hiện tại."""

    confidence_mode: str = "normal"  # normal|cautious
    reduce_proactive_friction: bool = False
    reason: str = ""
    recent_correction_rate: float = 0.0
    recent_annoyance_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.mentor_calibration.directive.v1",
            "confidence_mode": self.confidence_mode,
            "reduce_proactive_friction": self.reduce_proactive_friction,
            "reason": self.reason,
            "recent_correction_rate": round(self.recent_correction_rate, 4),
            "recent_annoyance_rate": round(self.recent_annoyance_rate, 4),
        }


class MentorCalibrationService:
    """Feedback loop local-first cho V85.

    State chỉ giữ intervention/outcome metadata. Không gọi model và không đọc hidden
    reasoning. Một intervention được resolve tối đa một lần bởi feedback rõ ràng; nếu
    hết cửa sổ thì chuyển thành ``unknown`` chứ KHÔNG gán là ignored/failure.
    """

    def __init__(self, *, path: Path | None = None, logger: Any = None) -> None:
        self.path = path or (default_eris_home() / "mentor" / "engineering_mentor_calibration.json")
        self.logger = logger
        self.feedback_window_seconds = 30 * 60.0
        self.pending: list[dict[str, Any]] = []
        self.outcomes: list[dict[str, Any]] = []
        self.metrics: dict[str, int] = {
            "interventions": 0,
            "helpful": 0,
            "user_corrected_eris": 0,
            "user_already_knew": 0,
            "mentor_opt_out": 0,
            "too_verbose": 0,
            "teachback_evidence": 0,
            "unknown": 0,
        }
        self.updated_at = 0.0
        self._lock = threading.RLock()
        self._load()

    def register_intervention(
        self,
        *,
        intervention_id: str,
        mode: str,
        reason: str,
        focus_skills: Sequence[str] = (),
        primary_role: str = "",
        proactive: bool = False,
        now: float | None = None,
    ) -> None:
        ts = float(now or time.time())
        ident = str(intervention_id or "")[:120]
        if not ident:
            return
        with self._lock:
            if any(str(x.get("id") or "") == ident for x in self.pending):
                return
            self._expire_locked(ts)
            self.pending.append({
                "id": ident,
                "at": ts,
                "mode": str(mode or "")[:24],
                "reason": str(reason or "")[:120],
                "focus_skills": [str(x)[:96] for x in tuple(focus_skills)[:4]],
                "primary_role": str(primary_role or "")[:24],
                "proactive": bool(proactive),
            })
            self.pending = self.pending[-12:]
            self.metrics["interventions"] = int(self.metrics.get("interventions", 0)) + 1
            self.updated_at = ts
            self._save_locked()

    def observe_user_response(
        self,
        user_text: str,
        *,
        feedback: Sequence[str] = (),
        learning_events: Sequence[str] = (),
        turn_id: str = "",
        now: float | None = None,
    ) -> MentorOutcome | None:
        """Resolve intervention gần nhất khi có evidence explicit.

        Không lưu ``user_text``. ``turn_id`` cũng không cần persist vì outcome được
        correlate bằng intervention id; tránh biến file calibration thành chat log.
        """
        _ = turn_id
        ts = float(now or time.time())
        low = " ".join(str(user_text or "").split()).casefold()
        feedback_set = {str(x) for x in feedback}
        learning_set = {str(x) for x in learning_events}
        with self._lock:
            self._expire_locked(ts)
            pending = self._latest_pending_locked(ts)
            if not pending:
                return None

            kind = ""
            codes: list[str] = []
            confidence = 0.0
            if "mentor_opt_out" in feedback_set:
                kind, confidence, codes = "mentor_opt_out", 0.98, ["explicit_opt_out"]
            elif "mentor_prefer_concise" in feedback_set:
                kind, confidence, codes = "too_verbose", 0.95, ["explicit_concise_feedback"]
            elif any(marker in low for marker in _SKILL_CORRECTION_MARKERS):
                kind, confidence, codes = "user_already_knew", 0.93, ["explicit_skill_correction"]
            elif any(marker in low for marker in _GENERAL_CORRECTION_MARKERS):
                kind, confidence, codes = "user_corrected_eris", 0.88, ["explicit_mentor_correction"]
            elif "teachback_evidence" in learning_set:
                kind, confidence, codes = "teachback_evidence", 0.86, ["retrieval_or_application_evidence"]
            elif any(marker in low for marker in _HELPFUL_MARKERS):
                kind, confidence, codes = "helpful", 0.86, ["explicit_positive_outcome"]
            else:
                return None

            outcome = self._resolve_locked(pending, kind, ts, confidence, codes)
            self._save_locked()
            return outcome

    def directive(self, *, now: float | None = None) -> CalibrationDirective:
        ts = float(now or time.time())
        with self._lock:
            self._expire_locked(ts)
            recent = [x for x in self.outcomes if ts - float(x.get("at", 0.0) or 0.0) <= 7 * 86400.0]
            if not recent:
                return CalibrationDirective()
            corrections = sum(1 for x in recent if x.get("kind") in {"user_corrected_eris", "user_already_knew"})
            annoyance = sum(1 for x in recent if x.get("kind") in {"mentor_opt_out", "too_verbose"})
            correction_rate = corrections / len(recent)
            annoyance_rate = annoyance / len(recent)
            cautious = len(recent) >= 3 and correction_rate >= 0.34
            reduce = len(recent) >= 4 and annoyance_rate >= 0.35
            reason = ""
            if cautious:
                reason = "recent_user_corrections_require_more_uncertainty"
            elif reduce:
                reason = "recent_mentor_annoyance_requires_less_friction"
            return CalibrationDirective(
                confidence_mode="cautious" if cautious else "normal",
                reduce_proactive_friction=reduce,
                reason=reason,
                recent_correction_rate=correction_rate,
                recent_annoyance_rate=annoyance_rate,
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            total_resolved = sum(int(self.metrics.get(k, 0)) for k in (
                "helpful", "user_corrected_eris", "user_already_knew", "mentor_opt_out",
                "too_verbose", "teachback_evidence", "unknown",
            ))
            useful = int(self.metrics.get("helpful", 0)) + int(self.metrics.get("teachback_evidence", 0))
            corrections = int(self.metrics.get("user_corrected_eris", 0)) + int(self.metrics.get("user_already_knew", 0))
            annoyance = int(self.metrics.get("mentor_opt_out", 0)) + int(self.metrics.get("too_verbose", 0))
            denominator = max(1, total_resolved)
            return {
                "schema": _SCHEMA,
                "updated_at": self.updated_at,
                "pending_count": len(self.pending),
                "recent_outcomes": list(self.outcomes[-24:]),
                "metrics": {
                    **{k: int(v) for k, v in self.metrics.items()},
                    "resolved": total_resolved,
                    "usefulness_rate": round(useful / denominator, 4),
                    "user_correction_rate": round(corrections / denominator, 4),
                    "annoyance_rate": round(annoyance / denominator, 4),
                    # Warning/challenge bị user sửa ngay là proxy audit được cho
                    # false-positive criticism; không khẳng định mentor sai tuyệt đối.
                    "false_positive_criticism_proxy": round(
                        sum(
                            1 for x in self.outcomes
                            if x.get("kind") in {"user_corrected_eris", "user_already_knew"}
                            and x.get("mode") in {"warn", "challenge"}
                        ) / denominator,
                        4,
                    ),
                },
            }

    def _latest_pending_locked(self, ts: float) -> dict[str, Any] | None:
        for item in reversed(self.pending):
            if ts - float(item.get("at", 0.0) or 0.0) <= self.feedback_window_seconds:
                return item
        return None

    def _resolve_locked(
        self,
        item: Mapping[str, Any],
        kind: str,
        ts: float,
        confidence: float,
        codes: Sequence[str],
    ) -> MentorOutcome:
        ident = str(item.get("id") or "")
        self.pending = [x for x in self.pending if str(x.get("id") or "") != ident]
        outcome = MentorOutcome(
            kind=kind,
            intervention_id=ident,
            at=ts,
            mode=str(item.get("mode") or ""),
            primary_role=str(item.get("primary_role") or ""),
            focus_skills=tuple(str(x) for x in (item.get("focus_skills") or [])),
            reason=str(item.get("reason") or "")[:120],
            confidence=confidence,
            evidence_codes=tuple(str(x) for x in codes),
        )
        self.outcomes.append(outcome.to_dict())
        self.outcomes = self.outcomes[-80:]
        self.metrics[kind] = int(self.metrics.get(kind, 0)) + 1
        self.updated_at = ts
        return outcome

    def _expire_locked(self, ts: float) -> None:
        expired = [x for x in self.pending if ts - float(x.get("at", 0.0) or 0.0) > self.feedback_window_seconds]
        if not expired:
            return
        expired_ids = {str(x.get("id") or "") for x in expired}
        self.pending = [x for x in self.pending if str(x.get("id") or "") not in expired_ids]
        for item in expired:
            self._resolve_locked(item, "unknown", ts, 0.25, ("feedback_window_expired",))
        self.updated_at = ts

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or str(raw.get("schema") or "") != _SCHEMA:
                return
            self.updated_at = float(raw.get("updated_at", 0.0) or 0.0)
            self.pending = [dict(x) for x in (raw.get("pending") or []) if isinstance(x, Mapping)][-12:]
            self.outcomes = [dict(x) for x in (raw.get("outcomes") or []) if isinstance(x, Mapping)][-80:]
            metrics = raw.get("metrics") or {}
            if isinstance(metrics, Mapping):
                for key in self.metrics:
                    self.metrics[key] = max(0, int(metrics.get(key, self.metrics[key]) or 0))
        except Exception:
            return

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema": _SCHEMA,
                "updated_at": self.updated_at,
                "pending": list(self.pending[-12:]),
                "outcomes": list(self.outcomes[-80:]),
                "metrics": {k: int(v) for k, v in self.metrics.items()},
            }
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass


def build_user_visible_mentor_profile(
    skill_snapshot: Mapping[str, Any],
    calibration_snapshot: Mapping[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Projection tối giản để user có thể audit model mà không đọc internal notes."""
    ts = float(now or time.time())
    skills_out: list[dict[str, Any]] = []
    skills = skill_snapshot.get("skills") or {}
    if isinstance(skills, Mapping):
        for skill_id in sorted(str(x) for x in skills.keys()):
            raw = skills.get(skill_id) or {}
            if not isinstance(raw, Mapping):
                continue
            evidence = int(raw.get("evidence_count", 0) or 0)
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0) or 0.0)))
            level = max(0.0, min(1.0, float(raw.get("level", 0.5) or 0.5)))
            if evidence < 2 or confidence < 0.16:
                label = "chưa đủ evidence"
            elif level >= 0.72:
                label = "mạnh"
            elif level >= 0.56:
                label = "khá"
            elif level >= 0.42:
                label = "đang hình thành"
            else:
                label = "cần thêm evidence"
            skills_out.append({
                "skill_id": skill_id,
                "label_vi": label,
                "estimate": round(level, 4),
                "confidence": round(confidence, 4),
                "evidence_count": evidence,
                "last_observed_at": float(raw.get("last_observed_at", 0.0) or 0.0),
            })
    metrics = calibration_snapshot.get("metrics") or {}
    return {
        "schema": _PROFILE_SCHEMA,
        "generated_at": ts,
        "skills": skills_out,
        "ai_operation": {
            "overreliance_risk": round(max(0.0, min(1.0, float(skill_snapshot.get("overreliance_risk", 0.0) or 0.0))), 4),
            "note_vi": "Rủi ro hành vi tách riêng khỏi năng lực; nhờ AI không tự động làm tụt skill.",
        },
        "mentor_calibration": {
            "usefulness_rate": float(metrics.get("usefulness_rate", 0.0) or 0.0),
            "user_correction_rate": float(metrics.get("user_correction_rate", 0.0) or 0.0),
            "annoyance_rate": float(metrics.get("annoyance_rate", 0.0) or 0.0),
            "resolved_outcomes": int(metrics.get("resolved", 0) or 0),
        },
        "controls": {
            "conversation_correction_supported": True,
            "in_process_reset_supported": True,
            "loopback_write_api_exposed": False,
            "privacy_note_vi": "Không mở endpoint write profile trên Eris Link chưa-authenticated.",
        },
    }


__all__ = [
    "CalibrationDirective",
    "MentorCalibrationService",
    "MentorOutcome",
    "build_user_visible_mentor_profile",
]

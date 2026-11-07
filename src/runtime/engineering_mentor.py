# -*- coding: utf-8 -*-
"""V80→V91 - Engineering Mentor evidence layer cho Eris Desktop.

Mục tiêu của module này KHÔNG phải tạo thêm một coding agent.
Nó chỉ làm bốn việc:
1) giữ một projection nhỏ về kỹ năng/vibe-coding behavior của user;
2) đọc flow observation từ V79;
3) chọn cách dạy phù hợp cho public conversation;
4) V81 theo dõi learning evidence/teach-back nhẹ, không biến Eris thành LMS;
5) V82 dạy user chọn đúng engineering tool/evidence mà không tự chạy tool;
6) V83 nối CodingFlow với WorldState/environment để dạy cross-environment;
7) V84 hợp nhất delivery Companion/Mentor/Supervisor thành một giọng Eris;
8) V85 đóng feedback loop: outcome/calibration/profile audit, không thêm LLM judge.
9) Companion Consolidation: Mentor chỉ còn là evidence/learning provider; AdaptiveCompanion sở hữu delivery.

Boundary quan trọng:
- không gọi tool coding;
- không sửa file;
- không approve task;
- không điều khiển coding agent bên ngoài;
- không suy user "ngu" chỉ vì user giao việc cho AI.

Skill estimate chỉ thay đổi khi có evidence hành vi tương đối rõ. Rủi ro lạm dụng AI
được giữ ở field riêng để không biến convenience thành phán xét năng lực.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from runtime.adaptive_engineering_learning import AdaptiveEngineeringLearning
from runtime.cross_environment_supervisor import CrossEnvironmentSupervisor, CrossEnvironmentDirective
from runtime.companion_mentor_orchestrator import CompanionMentorRoleDirective
from runtime.tool_literacy_mentor import EngineeringToolLiteracy
from runtime.mentor_calibration import MentorCalibrationService, build_user_visible_mentor_profile
from runtime.workspace_manager import default_eris_home

_SCHEMA = "eris.engineering_mentor.v1"
_RUNTIME_SCHEMA = "eris.engineering_mentor.runtime.v1"
_EVALUATION_SCHEMA = "eris.engineering_mentor.evaluation.v1"

# Các kỹ năng đủ rộng để dùng lâu dài, không phụ thuộc framework cụ thể.
_SKILLS = (
    "debugging.failure_localization",
    "debugging.call_flow_tracing",
    "debugging.root_cause_reasoning",
    "verification.testing",
    "verification.diff_review",
    "architecture.system_boundaries",
    "ai_operation.task_scoping",
    "ai_operation.evidence_review",
)

_CODE_MARKERS = (
    ".py", ".ts", ".tsx", ".js", ".gd", ".cs", ".cpp", ".h", ".json", ".toml", ".yaml", ".yml",
    " stack ", "trace", "call site", "function", "method", "class", "module", "repo", "code", "debug",
    "test", "diff", "commit", "git", "api", "runtime", "shader", "scene", "node", "godot", "vscode",
    "gitpilot", "agent", "task", "scope", "inspect", "explorer", "planner", "coder", "reviewer", "error", "fail", "lỗi", "bug", "fix", "sửa code", "sửa repo", "sửa file",
    "xong chưa", "nó sửa", "vừa sửa", "làm tiếp", "sửa tiếp", "tiếp đi",
)

_LEARNING_MARKERS = (
    "tại sao", "vì sao", "giải thích", "chỉ tôi", "chỉ tui", "dạy", "học", "phân tích", "luồng", "flow",
    "đọc file nào", "bắt đầu từ đâu", "trace", "root cause", "nguyên nhân", "cơ chế", "hoạt động sao",
)

_REVIEW_MARKERS = (
    "review", "diff", "test", "kiểm tra", "verify", "verification", "regression", "đọc lại", "soát", "audit",
)

_BLIND_EXECUTION_MARKERS = (
    "làm đi", "làm luôn", "sửa đi", "fix đi", "fix luôn", "code đi", "xử lý đi", "sửa hết", "làm hết",
    "cứ làm", "triển đi", "apply đi", "patch đi",
)

_HYPOTHESIS_MARKERS = (
    "tôi nghĩ", "tui nghĩ", "có phải", "hình như", "khả năng", "nghi là", "nguyên nhân là", "do ", "vì ",
)

_FLOW_REFERENCE_MARKERS = (
    "lỗi này", "bug này", "cái này", "vụ này", "task này", "nó", "agent", "gitpilot",
    "file nào", "đọc cái nào", "xong chưa", "sao rồi", "vừa rồi", "patch này", "diff này",
)


@dataclass(slots=True)
class SkillEstimate:
    """Estimate mềm, có confidence và evidence count để tránh phán bừa."""

    level: float = 0.5
    confidence: float = 0.10
    evidence_count: int = 0
    last_observed_at: float = 0.0
    last_calibrated_at: float = 0.0
    notes: list[str] = field(default_factory=list)
    independent_evidence_count: int = 0
    counter_evidence_count: int = 0
    evidence_keys: list[str] = field(default_factory=list)
    last_counter_evidence_at: float = 0.0

    def clamp(self) -> None:
        self.level = max(0.0, min(1.0, float(self.level)))
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        self.evidence_count = max(0, int(self.evidence_count))
        self.independent_evidence_count = max(0, int(self.independent_evidence_count or self.evidence_count))
        self.counter_evidence_count = max(0, int(self.counter_evidence_count))
        self.evidence_keys = [str(x)[:64] for x in self.evidence_keys[-24:] if str(x).strip()]
        self.notes = [str(x)[:180] for x in self.notes[-8:] if str(x).strip()]

    def to_dict(self) -> dict[str, Any]:
        self.clamp()
        return {
            "level": round(self.level, 4),
            "confidence": round(self.confidence, 4),
            "evidence_count": self.evidence_count,
            "independent_evidence_count": self.independent_evidence_count,
            "counter_evidence_count": self.counter_evidence_count,
            "evidence_keys": list(self.evidence_keys),
            "last_counter_evidence_at": self.last_counter_evidence_at,
            "last_observed_at": self.last_observed_at,
            "last_calibrated_at": self.last_calibrated_at,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SkillEstimate":
        item = cls(
            level=float(data.get("level", 0.5) or 0.5),
            confidence=float(data.get("confidence", 0.1) or 0.1),
            evidence_count=int(data.get("evidence_count", 0) or 0),
            last_observed_at=float(data.get("last_observed_at", 0.0) or 0.0),
            last_calibrated_at=float(data.get("last_calibrated_at", 0.0) or 0.0),
            notes=list(data.get("notes") or []),
            independent_evidence_count=int(data.get("independent_evidence_count", data.get("evidence_count", 0)) or 0),
            counter_evidence_count=int(data.get("counter_evidence_count", 0) or 0),
            evidence_keys=list(data.get("evidence_keys") or []),
            last_counter_evidence_at=float(data.get("last_counter_evidence_at", 0.0) or 0.0),
        )
        item.clamp()
        return item


@dataclass(slots=True)
class MentorDecision:
    relevant: bool = False
    mode: str = "none"  # none|discuss|explain|guide|hint|challenge|warn
    firmness: str = "normal"  # gentle|normal|firm
    reason: str = ""
    focus_skills: tuple[str, ...] = ()
    grounded_findings: tuple[str, ...] = ()
    user_signals: tuple[str, ...] = ()
    overreliance_risk: float = 0.0
    verbosity: str = "normal"  # compact|normal
    suppression_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.engineering_mentor.decision.v1",
            "relevant": self.relevant,
            "mode": self.mode,
            "firmness": self.firmness,
            "reason": self.reason,
            "focus_skills": list(self.focus_skills),
            "grounded_findings": list(self.grounded_findings),
            "user_signals": list(self.user_signals),
            "overreliance_risk": round(max(0.0, min(1.0, self.overreliance_risk)), 4),
            "verbosity": self.verbosity,
            "suppression_reason": self.suppression_reason,
        }


class MentorInterventionLedger:
    """V80.1 - chống mentor lắm mồm bằng state nhỏ, không thêm agent mới.

    Ledger chỉ lưu metadata về lần can thiệp/feedback. Nó KHÔNG lưu raw chat,
    source code hay chain-of-thought. Mục tiêu là biết khi nào nên im, khi nào
    user chủ động muốn học, và tránh giảng cùng một bài nhiều lần trong một buổi.
    """

    _OPTOUT_MARKERS = (
        "đừng giảng", "khỏi giảng", "đừng dạy", "khỏi dạy", "đừng giải thích",
        "không cần giải thích", "biết rồi", "cứ làm đi", "làm luôn đi",
    )
    _CONCISE_MARKERS = ("ngắn thôi", "nói ngắn", "gọn thôi", "dài quá", "bớt dài")
    _UNDERSTOOD_MARKERS = ("hiểu rồi", "à hiểu", "ra vậy", "ok hiểu", "ừ hiểu", "nắm rồi")

    def __init__(
        self,
        *,
        path: Path | None = None,
        evaluation_path: Path | None = None,
        logger: Any = None,
    ) -> None:
        root = default_eris_home() / "mentor"
        self.path = path or (root / "engineering_mentor_runtime.json")
        self.evaluation_path = evaluation_path or (root / "engineering_mentor_evaluation.jsonl")
        self.logger = logger
        self.session_idle_seconds = 2 * 3600.0
        self.signature_cooldown_seconds = 20 * 60.0
        self.proactive_window_seconds = 30 * 60.0
        self.max_proactive_per_window = 3
        self.feedback_window_seconds = 20 * 60.0
        self.evaluation_max_bytes = 2 * 1024 * 1024
        self.session_serial = 0
        self.session_started_at = 0.0
        self.last_user_at = 0.0
        self.proactive_pause_until = 0.0
        self.compact_until = 0.0
        self.recent_interventions: list[dict[str, Any]] = []
        self.recent_feedback: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._load()

    def _ensure_session_locked(self, ts: float) -> None:
        if self.last_user_at <= 0.0 or ts - self.last_user_at > self.session_idle_seconds:
            self.session_serial += 1
            self.session_started_at = ts
            self.proactive_pause_until = 0.0
            self.compact_until = 0.0
            self.recent_interventions = []
            self.recent_feedback = []
        self.last_user_at = ts

    @staticmethod
    def _signature(decision: MentorDecision) -> str:
        material = "|".join([
            decision.reason,
            *sorted(decision.focus_skills),
            *sorted(decision.grounded_findings),
        ]) or decision.mode
        return hashlib.sha1(material.encode("utf-8", errors="ignore")).hexdigest()[:12]

    def observe_feedback(
        self,
        user_text: str,
        *,
        prior_decision: MentorDecision | None,
        turn_id: str = "",
        now: float | None = None,
    ) -> tuple[str, ...]:
        """Đọc feedback rất hẹp từ turn kế tiếp; không suy thái độ từ mọi câu chat."""
        ts = float(now or time.time())
        text = " ".join(str(user_text or "").split()).casefold()
        with self._lock:
            self._ensure_session_locked(ts)
            prior = self.recent_interventions[-1] if self.recent_interventions else None
            if not prior or ts - float(prior.get("at", 0.0) or 0.0) > self.feedback_window_seconds:
                self._save_locked()
                return ()
            # recent_interventions mới là authority cho việc "đã từng dạy". Turn
            # ngay trước có thể chỉ là một mentor suppression/normal chat nhưng user
            # vẫn đang phản hồi bài giảng gần nhất, nên không phụ thuộc prior_decision.
            signals: list[str] = []
            if any(marker in text for marker in self._OPTOUT_MARKERS):
                signals.append("mentor_opt_out")
                # Pause chỉ trong phiên gần hiện tại; explicit learning vẫn bypass gate.
                self.proactive_pause_until = max(self.proactive_pause_until, ts + 30 * 60.0)
            if any(marker in text for marker in self._CONCISE_MARKERS):
                signals.append("mentor_prefer_concise")
                self.compact_until = max(self.compact_until, ts + self.session_idle_seconds)
            if any(marker in text for marker in self._UNDERSTOOD_MARKERS):
                signals.append("mentor_understood")
            if signals:
                item = {
                    "at": ts,
                    "turn_id": str(turn_id or "")[:120],
                    "signals": list(signals),
                    "intervention_signature": str(prior.get("signature") or ""),
                }
                self.recent_feedback.append(item)
                self.recent_feedback = self.recent_feedback[-30:]
                self._append_evaluation_locked("feedback", item)
            self._save_locked()
            return tuple(signals)

    def gate(
        self,
        decision: MentorDecision,
        *,
        signals: tuple[str, ...] = (),
        turn_id: str = "",
        now: float | None = None,
    ) -> MentorDecision:
        """Quyết định có inject mentor stance hay nên im.

        Explicit learning/verification luôn được quyền đi qua. Anti-annoyance chỉ
        áp dụng với proactive mentoring do Eris tự chen vào.
        """
        ts = float(now or time.time())
        if not decision.relevant:
            return decision
        explicit = "learning_intent" in signals or "verification_intent" in signals
        signature = self._signature(decision)
        with self._lock:
            self._ensure_session_locked(ts)
            if self.compact_until > ts:
                decision.verbosity = "compact"

            suppression = ""
            if not explicit and self.proactive_pause_until > ts:
                suppression = "user_paused_proactive_mentoring"
            if not explicit and not suppression:
                same_recent = any(
                    str(item.get("signature") or "") == signature
                    and ts - float(item.get("at", 0.0) or 0.0) <= self.signature_cooldown_seconds
                    for item in self.recent_interventions
                )
                if same_recent:
                    suppression = "same_lesson_cooldown"
            if not explicit and not suppression:
                proactive_recent = sum(
                    1 for item in self.recent_interventions
                    if bool(item.get("proactive", False))
                    and ts - float(item.get("at", 0.0) or 0.0) <= self.proactive_window_seconds
                )
                # Một warning thật sự quan trọng được phép vượt budget đúng một
                # lần; cooldown theo signature vẫn chặn việc mắng lặp liên tục.
                if decision.mode != "warn" and proactive_recent >= self.max_proactive_per_window:
                    suppression = "proactive_intervention_budget"

            if suppression:
                suppressed = MentorDecision(
                    relevant=False,
                    mode="none",
                    firmness="normal",
                    reason="mentor_intervention_suppressed",
                    focus_skills=decision.focus_skills,
                    grounded_findings=decision.grounded_findings,
                    user_signals=decision.user_signals,
                    overreliance_risk=decision.overreliance_risk,
                    verbosity=decision.verbosity,
                    suppression_reason=suppression,
                )
                self._append_evaluation_locked("decision", {
                    "at": ts, "turn_id": str(turn_id or "")[:120], "allowed": False,
                    "signature": signature, "mode": decision.mode, "reason": decision.reason,
                    "suppression_reason": suppression, "proactive": True,
                })
                self._save_locked()
                return suppressed

            record = {
                "at": ts,
                "turn_id": str(turn_id or "")[:120],
                "signature": signature,
                "mode": decision.mode,
                "reason": decision.reason,
                "proactive": not explicit,
            }
            self.recent_interventions.append(record)
            self.recent_interventions = self.recent_interventions[-40:]
            self._append_evaluation_locked("decision", {**record, "allowed": True})
            self._save_locked()
            return decision

    def record_role_selection(
        self,
        role: Mapping[str, Any],
        *,
        turn_id: str = "",
        now: float | None = None,
    ) -> None:
        """V84: log role metadata để eval, tuyệt đối không lưu raw user text."""
        if not isinstance(role, Mapping) or not bool(role.get("active", False)):
            return
        ts = float(now or time.time())
        payload = {
            "at": ts,
            "turn_id": str(turn_id or "")[:120],
            "primary_role": str(role.get("primary_role") or "")[:24],
            "supporting_functions": [str(x)[:24] for x in (role.get("supporting_functions") or [])[:2]],
            "reason": str(role.get("reason") or "")[:120],
            "surface_cue": str(role.get("surface_cue") or "")[:40],
            "mentor_intensity": str(role.get("mentor_intensity") or "")[:24],
            "confidence": float(role.get("confidence", 0.0) or 0.0),
        }
        with self._lock:
            self._append_evaluation_locked("role", payload)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": _RUNTIME_SCHEMA,
                "session_serial": self.session_serial,
                "session_started_at": self.session_started_at,
                "last_user_at": self.last_user_at,
                "proactive_pause_until": self.proactive_pause_until,
                "compact_until": self.compact_until,
                "recent_interventions": list(self.recent_interventions[-12:]),
                "recent_feedback": list(self.recent_feedback[-12:]),
                "evaluation_path": str(self.evaluation_path),
            }

    def feedback_for_turn(self, turn_id: str) -> tuple[str, ...]:
        with self._lock:
            for item in reversed(self.recent_feedback):
                if str(item.get("turn_id") or "") == str(turn_id or ""):
                    return tuple(str(x) for x in (item.get("signals") or []))
        return ()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or str(raw.get("schema") or "") != _RUNTIME_SCHEMA:
                return
            self.session_serial = int(raw.get("session_serial", 0) or 0)
            self.session_started_at = float(raw.get("session_started_at", 0.0) or 0.0)
            self.last_user_at = float(raw.get("last_user_at", 0.0) or 0.0)
            self.proactive_pause_until = float(raw.get("proactive_pause_until", 0.0) or 0.0)
            self.compact_until = float(raw.get("compact_until", 0.0) or 0.0)
            self.recent_interventions = [dict(x) for x in (raw.get("recent_interventions") or []) if isinstance(x, Mapping)][-40:]
            self.recent_feedback = [dict(x) for x in (raw.get("recent_feedback") or []) if isinstance(x, Mapping)][-30:]
        except Exception:
            return

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = self.snapshot()
            payload.pop("evaluation_path", None)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    def _append_evaluation_locked(self, event_type: str, payload: Mapping[str, Any]) -> None:
        try:
            self.evaluation_path.parent.mkdir(parents=True, exist_ok=True)
            # Evaluation là audit metadata, không phải telemetry vô hạn. Giữ một
            # file backup duy nhất để Eris không âm thầm ăn disk qua nhiều tháng.
            if self.evaluation_path.exists() and self.evaluation_path.stat().st_size >= self.evaluation_max_bytes:
                backup = self.evaluation_path.with_suffix(self.evaluation_path.suffix + ".1")
                try:
                    backup.unlink(missing_ok=True)
                except TypeError:
                    if backup.exists():
                        backup.unlink()
                self.evaluation_path.replace(backup)
            event = {"schema": _EVALUATION_SCHEMA, "event": event_type, **dict(payload)}
            with self.evaluation_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception:
            pass


class UserEngineeringSkillModel:
    """Projection specialist nhỏ, persistence riêng, evidence-backed.

    Đây không thay thế Living User Memory. Nó là state phục vụ policy dạy học, tương
    tự một model chuyên dụng. Chỉ lưu score + evidence summary, không lưu raw chat.
    """

    def __init__(self, *, path: Path | None = None) -> None:
        self.path = path or (default_eris_home() / "mentor" / "engineering_skill_model.json")
        self.skills: dict[str, SkillEstimate] = {name: SkillEstimate() for name in _SKILLS}
        self.overreliance_risk: float = 0.0
        self.last_risk_calibrated_at: float = 0.0
        self.recent_signals: list[dict[str, Any]] = []
        self.updated_at: float = 0.0
        self._lock = threading.RLock()
        self._load()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": _SCHEMA,
                "updated_at": self.updated_at,
                "overreliance_risk": round(self.overreliance_risk, 4),
                "last_risk_calibrated_at": self.last_risk_calibrated_at,
                "skills": {name: estimate.to_dict() for name, estimate in self.skills.items()},
                "recent_signals": list(self.recent_signals[-240:]),
            }

    def observe_user_turn(
        self,
        user_text: str,
        *,
        evidence_ref: str = "",
        now: float | None = None,
        suppress_blind_execution_risk: bool = False,
    ) -> tuple[str, ...]:
        """Rút signal thận trọng từ lời user.

        Quan trọng: nhờ AI làm việc KHÔNG làm tụt skill score. Nó chỉ có thể tăng
        overreliance_risk nếu lặp nhiều và không có dấu hiệu review/learning đi kèm.
        """
        ts = float(now or time.time())
        text = " ".join(str(user_text or "").split()).strip()
        low = f" {text.casefold()} "
        if not text:
            return ()
        signals: list[str] = []
        coding_related = any(marker in low for marker in _CODE_MARKERS)
        asks_learning = any(marker in low for marker in _LEARNING_MARKERS)
        asks_review = any(marker in low for marker in _REVIEW_MARKERS)
        blind_execute = any(marker in low for marker in _BLIND_EXECUTION_MARKERS)
        has_hypothesis = any(marker in low for marker in _HYPOTHESIS_MARKERS)
        has_code_ref = bool(re.search(r"(?:[A-Za-z0-9_./\\-]+\.(?:py|ts|tsx|js|gd|cs|cpp|h|json|toml|ya?ml))\b", text, flags=re.I))
        has_symbol_ref = bool(re.search(r"\b[A-Za-z_][A-Za-z0-9_]{2,}(?:\.[A-Za-z_][A-Za-z0-9_]*)?\(\)?", text))
        localization = coding_related and any(x in low for x in (" lỗi nằm ở ", " bug nằm ở ", " nằm trong ", " layer ", " module ", " file ")) and (has_code_ref or has_symbol_ref)
        boundary_reasoning = coding_related and any(x in low for x in (" boundary ", " ownership ", " ranh giới ", " layer nào ", " chịu trách nhiệm ", " tách module ", " tách service "))
        task_scoping = coding_related and any(x in low for x in (" scope ", " phạm vi ", " chỉ sửa ", " chỉ đụng ", " đừng đụng ", " file-change-only", " filechangeonly"))
        ai_evidence_review = asks_review and any(x in low for x in (" ai ", " agent ", " gitpilot ", " evidence ", " bằng chứng ", " output "))

        if asks_learning and coding_related:
            signals.append("learning_intent")
        if asks_review and coding_related:
            signals.append("verification_intent")
        if has_hypothesis and coding_related and (has_code_ref or has_symbol_ref):
            signals.append("grounded_hypothesis")
        elif has_hypothesis and coding_related:
            signals.append("hypothesis")
        if blind_execute and coding_related and not (asks_review or asks_learning or has_hypothesis):
            signals.append("blind_execution_request")
        if localization:
            signals.append("failure_localization")
        if boundary_reasoning:
            signals.append("system_boundary_reasoning")
        if task_scoping:
            signals.append("task_scoping")
        if ai_evidence_review:
            signals.append("ai_evidence_review")

        with self._lock:
            if "grounded_hypothesis" in signals:
                self._reinforce("debugging.root_cause_reasoning", +0.035, evidence_ref, ts, "User nêu hypothesis kèm code/file evidence.")
                self._reinforce("debugging.call_flow_tracing", +0.025, evidence_ref, ts, "User đang trace bằng reference kỹ thuật cụ thể.")
            elif "hypothesis" in signals:
                self._reinforce("debugging.root_cause_reasoning", +0.015, evidence_ref, ts, "User tự đề xuất hypothesis kỹ thuật.")
            if "verification_intent" in signals:
                self._reinforce("verification.diff_review", +0.022, evidence_ref, ts, "User chủ động yêu cầu review/verification.")
                self._reinforce("verification.testing", +0.015, evidence_ref, ts, "User chủ động quan tâm validation.")
                self.overreliance_risk = max(0.0, self.overreliance_risk - 0.07)
            if "failure_localization" in signals:
                self._reinforce("debugging.failure_localization", +0.028, evidence_ref, ts, "User khoanh vùng lỗi bằng reference kỹ thuật cụ thể.")
            if "system_boundary_reasoning" in signals:
                self._reinforce("architecture.system_boundaries", +0.024, evidence_ref, ts, "User reasoning về boundary/ownership của hệ thống.")
            if "task_scoping" in signals:
                self._reinforce("ai_operation.task_scoping", +0.024, evidence_ref, ts, "User chủ động khóa phạm vi thay đổi cho agent/task.")
            if "ai_evidence_review" in signals:
                self._reinforce("ai_operation.evidence_review", +0.025, evidence_ref, ts, "User yêu cầu evidence/review output của AI trước khi tin kết quả.")
            if "learning_intent" in signals:
                # Hỏi để học không chứng minh user yếu và cũng không tự nâng skill.
                self.overreliance_risk = max(0.0, self.overreliance_risk - 0.025)
            if "blind_execution_request" in signals and not suppress_blind_execution_risk:
                # Một lần nhờ làm nhanh là bình thường; tích lũy rất chậm. Nếu đây
                # chỉ là user từ chối một bài giảng vừa nhận thì không cộng risk.
                self.overreliance_risk = min(1.0, self.overreliance_risk + 0.045)

            if signals:
                self.recent_signals.append({
                    "at": ts,
                    "signals": list(signals),
                    "evidence_ref": str(evidence_ref or "")[:160],
                })
                self.recent_signals = self.recent_signals[-240:]
                self.updated_at = ts
                self._save_locked()
        return tuple(signals)

    def note_agent_flow(self, flow: Mapping[str, Any], *, now: float | None = None) -> None:
        """Flow của agent không phải evidence về user, kể cả cho overreliance.

        Agent retry ngu hoặc scope phình là dữ kiện để Mentor giảng về workflow,
        nhưng không chứng minh user phụ thuộc AI. V80 giữ boundary này tuyệt đối.
        """
        _ = (flow, now)
        return

    def recent_signal_count(self, signal: str, *, seconds: float = 7 * 86400.0, now: float | None = None) -> int:
        ts = float(now or time.time())
        with self._lock:
            return sum(
                1 for item in self.recent_signals
                if ts - float(item.get("at", 0.0) or 0.0) <= seconds
                and signal in tuple(item.get("signals") or ())
            )

    def _reinforce(self, skill: str, delta: float, evidence_ref: str, ts: float, note: str) -> None:
        item = self.skills.setdefault(skill, SkillEstimate())
        # V89: one turn/ref can contribute at most one independent evidence item per
        # skill. Repeating the same claim no longer inflates confidence/counts.
        raw_key = str(evidence_ref or f"anon:{int(ts // 86400)}")[:120]
        evidence_key = hashlib.sha1(f"{skill}|{raw_key}".encode("utf-8", errors="ignore")).hexdigest()[:20]
        independent = evidence_key not in item.evidence_keys
        if independent:
            if delta >= 0:
                item.level += delta * (1.0 - item.level)
            else:
                item.level += delta * item.level
            item.confidence = min(1.0, item.confidence + 0.035)
            item.evidence_count += 1
            item.independent_evidence_count += 1
            item.evidence_keys.append(evidence_key)
        item.last_observed_at = max(item.last_observed_at, ts)
        item.last_calibrated_at = ts
        if independent:
            suffix = f" [{evidence_ref[:80]}]" if evidence_ref else ""
            item.notes.append(note + suffix)
        item.clamp()

    def apply_counter_evidence(
        self, skills: tuple[str, ...] | list[str], *, evidence_ref: str = "", now: float | None = None
    ) -> None:
        """V89 bridge from adaptive-learning misconception evidence into capability belief.

        Counter-evidence is deliberately small: it lowers the estimate/confidence, but
        never turns one wrong heuristic into a durable judgement about the user.
        """
        ts = float(now or time.time())
        targets = [str(x) for x in skills if str(x) in self.skills][:4]
        if not targets:
            return
        with self._lock:
            for name in targets:
                item = self.skills[name]
                raw_key = str(evidence_ref or f"counter:{int(ts // 86400)}")[:120]
                key = hashlib.sha1(f"counter|{name}|{raw_key}".encode("utf-8", errors="ignore")).hexdigest()[:20]
                if key in item.evidence_keys:
                    continue
                item.evidence_keys.append(key)
                item.counter_evidence_count += 1
                item.level -= 0.035 * max(0.30, item.level)
                item.confidence = max(0.05, item.confidence - 0.025)
                item.last_counter_evidence_at = ts
                item.last_calibrated_at = ts
                suffix = f" [{str(evidence_ref)[:80]}]" if evidence_ref else ""
                item.notes.append("Candidate misconception supplied counter-evidence; estimate softened." + suffix)
                item.clamp()
            self.updated_at = ts
            self._save_locked()

    def decay_stale_confidence(self, *, now: float | None = None) -> None:
        """V85: evidence cũ giữ level nhưng confidence decay chậm theo thời gian.

        Half-life ~90 ngày. ``last_calibrated_at`` chống việc gọi snapshot nhiều lần
        làm decay lặp trên cùng khoảng thời gian.
        """
        ts = float(now or time.time())
        changed = False
        with self._lock:
            for item in self.skills.values():
                anchor = max(float(item.last_calibrated_at or 0.0), float(item.last_observed_at or 0.0))
                if anchor <= 0.0:
                    item.last_calibrated_at = ts
                    continue
                elapsed = max(0.0, ts - anchor)
                if elapsed < 24 * 3600.0:
                    item.last_calibrated_at = max(item.last_calibrated_at, anchor)
                    continue
                factor = 0.5 ** (elapsed / (90.0 * 86400.0))
                new_conf = max(0.05, item.confidence * factor)
                if abs(new_conf - item.confidence) >= 1e-6:
                    item.confidence = new_conf
                    changed = True
                item.last_calibrated_at = ts
            # V89: reasoning-ownership risk is recent behavior, not a permanent label.
            risk_anchor = float(self.last_risk_calibrated_at or self.updated_at or ts)
            risk_elapsed = max(0.0, ts - risk_anchor)
            if risk_elapsed >= 24 * 3600.0 and self.overreliance_risk > 0.0:
                risk_factor = 0.5 ** (risk_elapsed / (30.0 * 86400.0))
                new_risk = max(0.0, self.overreliance_risk * risk_factor)
                if abs(new_risk - self.overreliance_risk) >= 1e-6:
                    self.overreliance_risk = new_risk
                    changed = True
            self.last_risk_calibrated_at = ts
            if changed:
                self.updated_at = max(self.updated_at, ts)
                self._save_locked()

    def apply_user_correction(
        self,
        skills: tuple[str, ...] | list[str],
        *,
        kind: str,
        evidence_ref: str = "",
        now: float | None = None,
    ) -> None:
        """User sửa Mentor => hạ confidence của estimate liên quan, không tự buff mastery.

        Đây là correction của belief model, không phải proof user expert. Grounded
        technical evidence ở cùng turn vẫn được observe_user_turn() cộng riêng.
        """
        ts = float(now or time.time())
        targets = [str(x) for x in skills if str(x) in self.skills][:4]
        if not targets:
            return
        with self._lock:
            for name in targets:
                item = self.skills[name]
                penalty = 0.14 if kind == "user_corrected_eris" else 0.08
                item.confidence = max(0.05, item.confidence - penalty)
                item.last_calibrated_at = ts
                suffix = f" [{str(evidence_ref)[:80]}]" if evidence_ref else ""
                item.notes.append((
                    "User correction contradicted mentor/model estimate; confidence reduced."
                    if kind == "user_corrected_eris"
                    else "User stated prior knowledge; treat as correction/self-report, not mastery proof."
                ) + suffix)
                item.clamp()
            self.updated_at = ts
            self._save_locked()

    def reset_skill(self, skill_id: str) -> bool:
        """Reset một skill projection theo user control; không đụng Living User memory."""
        name = str(skill_id or "")
        if name not in self.skills:
            return False
        with self._lock:
            self.skills[name] = SkillEstimate()
            self.updated_at = time.time()
            self._save_locked()
        return True

    def reset_all(self) -> None:
        """Reset toàn bộ engineering skill projection + overreliance, local-only."""
        with self._lock:
            self.skills = {name: SkillEstimate() for name in _SKILLS}
            self.overreliance_risk = 0.0
            self.last_risk_calibrated_at = 0.0
            self.recent_signals = []
            self.updated_at = time.time()
            self._save_locked()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or str(raw.get("schema") or "") != _SCHEMA:
                return
            skills = raw.get("skills")
            if isinstance(skills, Mapping):
                for name in _SKILLS:
                    if isinstance(skills.get(name), Mapping):
                        self.skills[name] = SkillEstimate.from_dict(skills[name])
            self.overreliance_risk = max(0.0, min(1.0, float(raw.get("overreliance_risk", 0.0) or 0.0)))
            self.last_risk_calibrated_at = float(raw.get("last_risk_calibrated_at", 0.0) or 0.0)
            self.recent_signals = [dict(x) for x in (raw.get("recent_signals") or []) if isinstance(x, Mapping)][-240:]
            self.updated_at = float(raw.get("updated_at", 0.0) or 0.0)
        except Exception:
            return

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            # Mentor persistence không bao giờ được phép làm hỏng chat/coding.
            pass


class EngineeringTeachingPolicy:
    """Chọn cách dạy dựa trên user signal + flow có evidence.

    Policy này không tự viết câu trả lời hoàn chỉnh. Nó chỉ tạo directive cho public
    writer để personality của Eris vẫn là người nói, tránh một "mentor chatbot" thứ hai.
    """

    _HIGH_VALUE_FINDINGS = {
        "patch_before_inspection",
        "repeated_same_failure",
        "repeated_test_failure",
        "scope_expansion",
        "no_structured_verification",
        "review_not_observed",
    }

    def decide(
        self,
        user_text: str,
        *,
        model: UserEngineeringSkillModel,
        flow: Mapping[str, Any] | None = None,
        signals: tuple[str, ...] = (),
        now: float | None = None,
    ) -> MentorDecision:
        text = " ".join(str(user_text or "").split()).strip()
        low = f" {text.casefold()} "
        flow = flow or {}
        findings = [
            str(item.get("code") or "") for item in (flow.get("findings") or [])
            if isinstance(item, Mapping) and str(item.get("code") or "") in self._HIGH_VALUE_FINDINGS
        ]
        # Có flow gần đây không có nghĩa mọi câu chat sau đó đều là coding talk.
        # Relevance phải đến từ chính turn hiện tại; tránh Eris đem bài giảng code
        # chen vào chuyện ăn uống/chơi game chỉ vì GitPilot vừa chạy trước đó.
        candidate_misconception = "candidate_misconception" in signals
        coding_related = candidate_misconception or any(marker in low for marker in _CODE_MARKERS) or (
            bool(flow) and any(marker in low for marker in _FLOW_REFERENCE_MARKERS)
        )
        asks_learning = "learning_intent" in signals
        asks_review = "verification_intent" in signals
        blind = "blind_execution_request" in signals
        risk = model.overreliance_risk
        blind_week = model.recent_signal_count("blind_execution_request", now=now)

        if not coding_related:
            return MentorDecision()

        focus = self._focus_skills(findings, signals)
        trusted_level = self._trusted_focus_level(model, focus)
        if candidate_misconception:
            return MentorDecision(
                True, "guide", "firm", "candidate_misconception_needs_check",
                focus, tuple(findings), signals, risk,
            )
        if asks_learning:
            # Khi đã có evidence user khá, giảm spoon-feeding: đưa hint/challenge thay
            # vì giảng lại từ số 0. Khi chưa đủ confidence, chọn guide an toàn.
            if trusted_level is not None and trusted_level >= 0.72:
                mode = "hint" if not findings else "challenge"
            elif trusted_level is not None and trusted_level <= 0.38:
                mode = "explain"
            else:
                mode = "guide" if findings else "explain"
            return MentorDecision(True, mode, "normal", "user_requested_learning", focus, tuple(findings), signals, risk)

        if asks_review:
            return MentorDecision(True, "guide", "normal", "user_requested_verification", focus, tuple(findings), signals, risk)

        severe_flow = any(code in {"repeated_same_failure", "repeated_test_failure", "patch_before_inspection"} for code in findings)
        if blind and (risk >= 0.28 or blind_week >= 4 or severe_flow):
            return MentorDecision(
                True, "warn", "firm", "repeated_blind_delegation_with_review_risk",
                focus or ("ai_operation.evidence_review",), tuple(findings), signals, risk,
            )

        if severe_flow:
            return MentorDecision(True, "challenge", "firm", "coding_flow_contains_teachable_failure", focus, tuple(findings), signals, risk)

        if findings:
            return MentorDecision(True, "hint", "normal", "coding_flow_has_teachable_finding", focus, tuple(findings), signals, risk)

        if blind:
            # Một request làm nhanh vẫn được phép. Chỉ nhắc nhẹ về scope/verification.
            return MentorDecision(True, "discuss", "normal", "execution_request_without_learning_signal", focus, (), signals, risk)

        return MentorDecision(True, "discuss", "gentle", "coding_discussion", focus, (), signals, risk)

    @staticmethod
    def _trusted_focus_level(model: UserEngineeringSkillModel, focus: tuple[str, ...]) -> float | None:
        snap = model.snapshot().get("skills") or {}
        levels: list[float] = []
        for name in focus:
            raw = snap.get(name) if isinstance(snap, Mapping) else None
            if not isinstance(raw, Mapping):
                continue
            if int(raw.get("evidence_count", 0) or 0) < 2 or float(raw.get("confidence", 0.0) or 0.0) < 0.16:
                continue
            levels.append(float(raw.get("level", 0.5) or 0.5))
        if not levels:
            return None
        return sum(levels) / len(levels)

    @staticmethod
    def _focus_skills(findings: list[str], signals: tuple[str, ...]) -> tuple[str, ...]:
        out: list[str] = []
        mapping = {
            "patch_before_inspection": "debugging.failure_localization",
            "repeated_same_failure": "debugging.root_cause_reasoning",
            "repeated_test_failure": "debugging.root_cause_reasoning",
            "scope_expansion": "ai_operation.task_scoping",
            "no_structured_verification": "verification.testing",
            "review_not_observed": "verification.diff_review",
        }
        for code in findings:
            skill = mapping.get(code)
            if skill and skill not in out:
                out.append(skill)
        if "verification_intent" in signals and "verification.diff_review" not in out:
            out.append("verification.diff_review")
        if "grounded_hypothesis" in signals and "debugging.root_cause_reasoning" not in out:
            out.append("debugging.root_cause_reasoning")
        return tuple(out[:4])


class EngineeringMentorService:
    """Facade V80→V85 dùng bởi Coordinator + public chat orchestration."""

    def __init__(
        self,
        *,
        path: Path | None = None,
        runtime_path: Path | None = None,
        evaluation_path: Path | None = None,
        learning_path: Path | None = None,
        tool_literacy_path: Path | None = None,
        calibration_path: Path | None = None,
        learning_service: Any = None,
        logger: Any = None,
    ) -> None:
        self.logger = logger
        self.model = UserEngineeringSkillModel(path=path)
        self.policy = EngineeringTeachingPolicy()
        # Khi caller truyền custom skill path (tests/tooling), runtime/eval đi cùng
        # thư mục để tránh state mentor toàn máy làm nhiễu test hoặc profile khác.
        if path is not None:
            runtime_path = runtime_path or path.with_name("engineering_mentor_runtime.json")
            evaluation_path = evaluation_path or path.with_name("engineering_mentor_evaluation.jsonl")
            learning_path = learning_path or path.with_name("engineering_learning.json")
            tool_literacy_path = tool_literacy_path or path.with_name("engineering_tool_literacy.json")
            calibration_path = calibration_path or path.with_name("engineering_mentor_calibration.json")
        self.ledger = MentorInterventionLedger(
            path=runtime_path, evaluation_path=evaluation_path, logger=logger
        )
        self.learning = AdaptiveEngineeringLearning(
            path=learning_path,
            logger=logger,
            learning_service=learning_service,
        )
        # V82 chỉ tư vấn tool/evidence, không sở hữu hay thực thi bất kỳ tool nào.
        self.tool_literacy = EngineeringToolLiteracy(path=tool_literacy_path, logger=logger)
        # V85 chỉ đánh giá outcome observable của mentor; không gọi thêm model/agent.
        self.calibration = MentorCalibrationService(path=calibration_path, logger=logger)
        # V83 là correlator stateless: chỉ nối CodingFlow + WorldState evidence.
        self.cross_environment = CrossEnvironmentSupervisor()
        self._last_cross_environment = CrossEnvironmentDirective()
        # Companion Consolidation: role router V84 không còn nằm trong active path.
        # AdaptiveCompanion là owner duy nhất của stance/delivery. Field inactive
        # này chỉ giữ compatibility cho snapshot/UI cũ trong một chu kỳ migration.
        self._last_role_directive = CompanionMentorRoleDirective()
        self._last_flow: dict[str, Any] = {}
        self._last_companion_context: dict[str, Any] = {}
        self._last_policy_decision = MentorDecision()
        self._last_decision = MentorDecision()
        self._last_turn_id = ""
        self._last_turn_fingerprint = ""
        # Hint delivery được đề xuất ở Mentor nhưng chỉ được commit khi
        # AdaptiveCompanion xác nhận turn thật sự đang dạy/challenge.
        self._pending_delivery_turn_id = ""
        self._pending_learning_directive: Any = None
        self._pending_tool_directive: Any = None
        self._pending_session_serial = 0
        self._pending_focus_skills: tuple[str, ...] = ()
        self._pending_decision_mode = ""
        self._pending_decision_reason = ""
        self._lock = threading.RLock()

    def observe_coding_flow(self, flow: Mapping[str, Any]) -> None:
        if not isinstance(flow, Mapping) or not flow.get("task_id"):
            return
        clean = {
            "task_id": str(flow.get("task_id") or "")[:160],
            "workspace": str(flow.get("workspace") or "")[:520],
            "status": str(flow.get("status") or "")[:40],
            "started_at": float(flow.get("started_at", 0.0) or 0.0),
            "ended_at": float(flow.get("ended_at", 0.0) or 0.0),
            "changed_files": [str(x)[:520] for x in (flow.get("changed_files") or [])[:16]],
            "tests_seen": int(flow.get("tests_seen", 0) or 0),
            "failed_test_runs": int(flow.get("failed_test_runs", 0) or 0),
            "reviewer_seen": bool(flow.get("reviewer_seen", False)),
            "phase_summaries": {
                str(k)[:32]: str(v)[:900] for k, v in dict(flow.get("phase_summaries") or {}).items()
            },
            "findings": [
                {
                    "code": str(x.get("code") or "")[:80],
                    "severity": str(x.get("severity") or "")[:32],
                    "message": str(x.get("message") or "")[:320],
                }
                for x in (flow.get("findings") or [])[:12] if isinstance(x, Mapping)
            ],
        }
        with self._lock:
            self._last_flow = clean
        self.model.note_agent_flow(clean)
        self.learning.note_coding_flow(clean)

    def observe_companion_context(self, context: Mapping[str, Any]) -> None:
        """LEGACY V90 no-op: Desktop không nhận workspace telemetry từ extension.

        Giữ method để caller cũ không crash trong giai đoạn migration, nhưng dữ liệu
        từ VS Code/GitPilot không được phép đi vào companion/skill state nữa.
        """
        return None

    def observe_external_user_intent(
        self, user_text: str, *, evidence_ref: str = "", now: float | None = None
    ) -> tuple[str, ...]:
        """LEGACY V90 no-op: không học user qua hidden extension bridge.

        Muốn Eris hiểu một reasoning/task, user phải nói với Eris hoặc chủ động đưa
        evidence qua kênh Eris. Đây là boundary tách rời bằng code, không chỉ bằng
        convention rằng extension "hiện tại không gọi".
        """
        return ()

    def _relevant_companion_context(self, *, now: float | None = None) -> dict[str, Any]:
        """Legacy compatibility: hidden workspace context is intentionally disabled."""
        return {}

    def _relevant_flow(self, *, now: float | None = None) -> dict[str, Any]:
        """Chỉ mang flow gần đây vào bài giảng; journal cũ không được giả làm context hiện tại."""
        ts = float(now or time.time())
        with self._lock:
            flow = dict(self._last_flow)
        if not flow:
            return {}
        if str(flow.get("status") or "") == "running":
            return flow
        ended = float(flow.get("ended_at", 0.0) or 0.0)
        if ended > 0.0 and 0.0 <= ts - ended <= 6 * 3600.0:
            return flow
        return {}

    def observe_user_turn(self, user_text: str, *, turn_id: str = "", now: float | None = None) -> MentorDecision:
        """Tạo policy decision đúng một lần mỗi turn, chưa tính anti-annoyance delivery.

        Tách policy khỏi delivery là boundary quan trọng của V80.1: Eris có thể
        nhận ra một teachable moment nhưng intervention gate vẫn quyết định im.
        """
        ts = float(now or time.time())
        fingerprint = hashlib.sha1(str(user_text or "").encode("utf-8", errors="ignore")).hexdigest()[:16]
        with self._lock:
            if turn_id and turn_id == self._last_turn_id and fingerprint == self._last_turn_fingerprint:
                return self._last_policy_decision
            prior = self._last_decision

        # V85: confidence cũ tự decay theo thời gian. Level không tự tụt; chỉ độ
        # chắc chắn giảm nếu lâu không có evidence mới.
        self.model.decay_stale_confidence(now=ts)
        feedback = self.ledger.observe_feedback(
            user_text, prior_decision=prior, turn_id=turn_id, now=ts
        )
        signals = self.model.observe_user_turn(
            user_text,
            evidence_ref=turn_id,
            now=ts,
            suppress_blind_execution_risk="mentor_opt_out" in feedback,
        )
        learning_events = self.learning.observe_user_turn(
            user_text,
            signals=signals,
            feedback=feedback,
            evidence_ref=turn_id,
            now=ts,
        )
        # V85 resolve intervention trước đó bằng feedback explicit. User correction
        # có thể hạ confidence của skill estimate, nhưng không tự sửa level lên expert.
        outcome = self.calibration.observe_user_response(
            user_text, feedback=feedback, learning_events=learning_events, turn_id=turn_id, now=ts
        )
        if outcome is not None and outcome.kind in {"user_corrected_eris", "user_already_knew"}:
            self.model.apply_user_correction(
                outcome.focus_skills,
                kind=outcome.kind,
                evidence_ref=f"mentor_outcome:{outcome.intervention_id}",
                now=ts,
            )
        # V82 chỉ ghi tool-use demonstration khi user nói rõ đã dùng tool và thấy
        # evidence gì; nhắc tên debugger/diff trong câu hỏi không được tính mastery.
        self.tool_literacy.observe_user_turn(user_text, evidence_ref=turn_id, now=ts)
        if any(str(x).startswith("candidate_misconception:") for x in learning_events):
            signals = tuple((*signals, "candidate_misconception"))
        flow = self._relevant_flow(now=ts)
        policy_decision = self.policy.decide(
            user_text, model=self.model, flow=flow, signals=signals, now=ts
        )
        event_focus = self.learning.focus_skills_for_events(learning_events)
        if event_focus:
            self.model.apply_counter_evidence(event_focus, evidence_ref=f"learning:{turn_id}", now=ts)
            policy_decision.focus_skills = tuple(dict.fromkeys((*policy_decision.focus_skills, *event_focus)))[:4]
        with self._lock:
            self._last_policy_decision = policy_decision
            self._last_turn_id = str(turn_id or "")
            self._last_turn_fingerprint = fingerprint
        return policy_decision

    def prompt_context(
        self,
        user_text: str,
        *,
        turn_id: str = "",
        now: float | None = None,
        environment: Mapping[str, Any] | None = None,
    ) -> str:
        """Directive ngắn cho public writer; không giả competence nếu confidence thấp.

        ``environment`` phải là projection structured từ WorldState/EnvironmentAwareness,
        không phải raw screenshot/OCR. V83 chỉ dùng nó để correlate với coding flow.
        Sau consolidation, method này chỉ dựng technical evidence/hint; quyền chọn
        delivery/stance thuộc duy nhất AdaptiveCompanion.
        """
        policy_decision = self.observe_user_turn(user_text, turn_id=turn_id, now=now)
        calibration_directive = self.calibration.directive(now=now)
        # Nếu nhiều feedback gần đây nói mentor gây phiền, V85 giảm proactive friction
        # trước intervention gate. Explicit learning/verification vẫn không bị chặn.
        explicit = "learning_intent" in policy_decision.user_signals or "verification_intent" in policy_decision.user_signals
        if calibration_directive.reduce_proactive_friction and policy_decision.relevant and not explicit and policy_decision.mode not in {"warn"}:
            policy_decision = MentorDecision(
                relevant=False, mode="none", reason="mentor_calibration_reduced_friction",
                focus_skills=policy_decision.focus_skills, grounded_findings=policy_decision.grounded_findings,
                user_signals=policy_decision.user_signals, overreliance_risk=policy_decision.overreliance_risk,
                suppression_reason="recent_mentor_annoyance",
            )
        decision = self.ledger.gate(
            policy_decision, signals=policy_decision.user_signals, turn_id=turn_id, now=now
        )
        # Nếu turn này chỉ là feedback kiểu "đừng giảng", policy có thể không
        # coi nó là coding question. Vẫn ghi effective suppression để audit rõ vì sao im.
        if not decision.relevant:
            feedback = self.ledger.feedback_for_turn(turn_id)
            if "mentor_opt_out" in feedback:
                decision = MentorDecision(
                    relevant=False, mode="none", reason="mentor_intervention_suppressed",
                    overreliance_risk=self.model.overreliance_risk,
                    suppression_reason="user_paused_proactive_mentoring",
                )
        with self._lock:
            self._last_decision = decision
        if not decision.relevant:
            # Companion Consolidation: EngineeringMentor không còn tự route role.
            # Giữ object inactive chỉ để snapshot cũ không vỡ khi UI/test đọc field.
            with self._lock:
                self._last_role_directive = CompanionMentorRoleDirective()
            return ""
        flow = self._relevant_flow(now=now)
        snapshot = self.model.snapshot()
        skill_projection: dict[str, Any] = {}
        for name in decision.focus_skills:
            raw = dict((snapshot.get("skills") or {}).get(name) or {})
            # Chỉ expose estimate khi evidence đủ. Companion sẽ quyết định dùng
            # estimate này để nói như peer, dạy nhẹ hay bỏ qua phần nhập môn.
            if int(raw.get("evidence_count", 0) or 0) >= 2 and float(raw.get("confidence", 0.0) or 0.0) >= 0.16:
                skill_projection[name] = {
                    "level": raw.get("level"),
                    "confidence": raw.get("confidence"),
                    "evidence_count": raw.get("evidence_count"),
                    "independent_evidence_count": raw.get("independent_evidence_count", raw.get("evidence_count", 0)),
                }

        runtime_snapshot = self.ledger.snapshot()
        session_serial = int(runtime_snapshot.get("session_serial", 0) or 0)
        learning_directive = self.learning.plan(
            decision_mode=decision.mode,
            focus_skills=decision.focus_skills,
            findings=decision.grounded_findings,
            signals=decision.user_signals,
            session_serial=session_serial,
            now=now,
            compact=decision.verbosity == "compact",
        )
        learning_projection = self.learning.projection(
            [learning_directive.concept_id] if learning_directive.active else []
        )
        # Chưa register_prompt ở đây: Mentor chỉ ĐỀ XUẤT learning hint.
        # AdaptiveCompanion sẽ commit nếu delivery turn thật sự là teaching/challenge.

        # Cross-environment vẫn giữ như EVIDENCE helper độc lập extension. Nó chỉ
        # hoạt động khi có flow được cung cấp rõ + WorldState structured hiện tại;
        # không tự đọc VS Code/GitPilot và không có quyền chọn stance/delivery.
        cross_environment = self.cross_environment.analyze(
            user_text,
            coding_flow=flow,
            environment=environment or {},
            now=now,
        )
        with self._lock:
            self._last_cross_environment = cross_environment

        # Tool literacy vẫn là evidence helper hợp lệ vì nó dạy cách tự kiểm chứng,
        # nhưng không được tự đổi stance hay giả vờ đã mở tool. Không còn lấy hint
        # từ VS Code/GitPilot bridge hoặc cross-environment telemetry.
        tool_directive = self.tool_literacy.plan(
            user_text,
            decision_mode=decision.mode,
            findings=decision.grounded_findings,
            recent_flow=flow,
            environment_hint=cross_environment.to_dict() if cross_environment.active else None,
            compact=decision.verbosity == "compact",
            now=now,
        )
        tool_projection = self.tool_literacy.projection(
            [tool_directive.primary_tool, *tool_directive.supporting_tools] if tool_directive.active else []
        )

        # Calibration outcome vẫn được resolve trong observe_user_turn(), nhưng
        # evidence provider KHÔNG tự ghi "intervention đã xảy ra". Việc đó thuộc
        # AdaptiveCompanion sau khi nó thực sự cấp teaching/challenge permission.
        with self._lock:
            self._last_role_directive = CompanionMentorRoleDirective()
            self._pending_delivery_turn_id = str(turn_id or "")[:120]
            self._pending_learning_directive = learning_directive
            self._pending_tool_directive = tool_directive
            self._pending_session_serial = session_serial
            self._pending_focus_skills = tuple(decision.focus_skills)
            self._pending_decision_mode = str(decision.mode or "")
            self._pending_decision_reason = str(decision.reason or "")

        flow_summary = {
            "task_id": flow.get("task_id", ""),
            "status": flow.get("status", ""),
            "changed_files": list(flow.get("changed_files") or [])[:8],
            "tests_seen": flow.get("tests_seen", 0),
            "failed_test_runs": flow.get("failed_test_runs", 0),
            "reviewer_seen": flow.get("reviewer_seen", False),
            "findings": list(flow.get("findings") or [])[:8],
        } if flow else {}

        # Đây là block EVIDENCE, không còn là một bộ não delivery thứ hai.
        # Public writer phải lấy stance/teasing/depth từ ADAPTIVE_COMPANION_V91.
        return (
            "ENGINEERING_EVIDENCE:\n"
            + json.dumps({
                "role": "technical_evidence_provider",
                "delivery_owner": "adaptive_companion",
                "decision": decision.to_dict(),
                "skill_projection": skill_projection,
                "optional_recent_flow_evidence": flow_summary,
                "cross_environment": {
                    "authority": "evidence_only",
                    "directive": cross_environment.to_dict(),
                    "rule": "Correlation only: dùng để chọn bước kiểm chứng, không tự suy patch gây triệu chứng và không quyết định giọng nói.",
                },
                "adaptive_learning": {
                    "directive": learning_directive.to_dict(),
                    "projection": learning_projection,
                    "rule": "Đây là learning hint/evidence; candidate misconception chỉ là giả thuyết cần kiểm tra, không phải nhãn user. Không tự biến turn thành bài học nếu Companion không cấp teaching permission.",
                },
                "tool_literacy": {
                    "directive": tool_directive.to_dict(),
                    "projection": tool_projection,
                    "rule": "Chỉ gợi ý cách lấy evidence. Không claim đã mở/chạy tool và không điều khiển coding extension.",
                },
                "mentor_calibration": {
                    "directive": calibration_directive.to_dict(),
                    "metrics": self.calibration.snapshot().get("metrics", {}),
                    "rule": "Calibration dùng feedback user làm input để AdaptiveCompanion tự giảm/điều chỉnh teaching confidence; không tranh luận thắng-thua với correction.",
                },
                "hard_boundaries": [
                    "AdaptiveCompanion is the only delivery/stance authority.",
                    "EngineeringMentor may estimate skills and learning evidence but must not choose persona/role/teasing style.",
                    "Delegating to AI is not itself evidence of low competence.",
                    "Do not claim a skill weakness without evidence/confidence.",
                    "Never invent files, tests, tool actions, reviewer findings, or workspace state.",
                    "No coding-extension telemetry is required for mentor operation.",
                    "Do not seize GitPilot control; discuss/teach/interpret only.",
                    "Self-report of understanding is not mastery evidence; prefer retrieval/application evidence.",
                    "Tool literacy is advisory only and never executes tools.",
                    "Cross-environment evidence is correlation only and never becomes delivery authority.",
                ],
            }, ensure_ascii=False, separators=(",", ":"))
        )

    def note_companion_delivery(
        self,
        *,
        turn_id: str,
        stance: str,
        teaching_permission: str,
        reason: str = "",
        now: float | None = None,
    ) -> bool:
        """Commit learning/calibration sau khi Companion đã chọn delivery.

        Mentor có thể phát hiện teachable moment, nhưng chỉ Companion biết turn có
        thật sự nên dạy/challenge hay không. Method này là handshake một chiều:
        Companion -> evidence services. Nó không trả quyền chọn stance về Mentor.
        """
        ts = float(now or time.time())
        ident = str(turn_id or "")[:120]
        with self._lock:
            pending_id = self._pending_delivery_turn_id
            learning_directive = self._pending_learning_directive
            tool_directive = self._pending_tool_directive
            session_serial = self._pending_session_serial
            focus_skills = self._pending_focus_skills
            decision_mode = self._pending_decision_mode
            decision_reason = self._pending_decision_reason
            # Clear dù có commit hay không để hint turn cũ không rò sang turn sau.
            self._pending_delivery_turn_id = ""
            self._pending_learning_directive = None
            self._pending_tool_directive = None
            self._pending_session_serial = 0
            self._pending_focus_skills = ()
            self._pending_decision_mode = ""
            self._pending_decision_reason = ""

        if pending_id and ident and pending_id != ident:
            return False
        allowed = str(stance or "") in {"teach_light", "teach_guided", "challenge_light"}
        if not allowed or str(teaching_permission or "") not in {"light", "guided", "challenge"}:
            return False

        if getattr(learning_directive, "active", False):
            self.learning.register_prompt(learning_directive, session_serial=session_serial, now=ts)
        if getattr(tool_directive, "active", False):
            self.tool_literacy.register_recommendation(tool_directive, now=ts)

        intervention_id = ident
        if not intervention_id:
            material = f"{decision_reason}|{decision_mode}|{ts:.6f}"
            intervention_id = hashlib.sha1(material.encode("utf-8", errors="ignore")).hexdigest()[:16]
        self.calibration.register_intervention(
            intervention_id=intervention_id,
            mode={
                "teach_light": "hint",
                "teach_guided": "guide",
                "challenge_light": "challenge",
            }.get(str(stance or ""), decision_mode or "discuss"),
            reason=str(reason or decision_reason),
            focus_skills=focus_skills,
            primary_role=str(stance or "adaptive_companion"),
            proactive=str(stance or "") != "teach_guided",
            now=ts,
        )
        return True

    def mentor_profile(self, *, now: float | None = None) -> dict[str, Any]:
        """V85 user-visible projection read-only: evidence/confidence rõ, không lộ notes."""
        return build_user_visible_mentor_profile(
            self.model.snapshot(), self.calibration.snapshot(), now=now
        )

    def reset_skill_estimate(self, skill_id: str) -> bool:
        """In-process user control cho UI/chat layer đáng tin cậy; không expose qua Eris Link write API."""
        return self.model.reset_skill(skill_id)

    def apply_panel_skill_feedback(self, skill_id: str, feedback: str, *, now: float | None = None) -> bool:
        """V87 trusted control từ Capability Panel.

        Nút panel chỉ sửa *belief* của ERIS. ``already_knew``/``wrong`` làm
        giảm confidence, không tăng mastery; ``confirmed`` chỉ là xác nhận UI và
        không tự thưởng điểm. HTTP Eris Link vẫn không expose method này.
        """
        skill = str(skill_id or "").strip()
        action = str(feedback or "").strip().lower()
        if skill not in self.model.skills:
            return False
        if action == "reset":
            return self.reset_skill_estimate(skill)
        if action == "already_knew":
            self.model.apply_user_correction((skill,), kind="user_already_knew", evidence_ref="capability_panel:v87", now=now)
            return True
        if action == "wrong":
            self.model.apply_user_correction((skill,), kind="user_corrected_eris", evidence_ref="capability_panel:v87", now=now)
            return True
        if action == "confirmed":
            return True
        return False

    def reset_mentor_profile(self) -> None:
        """Reset projection kỹ năng theo yêu cầu user; calibration history giữ riêng để audit mentor."""
        self.model.reset_all()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": _SCHEMA,
                "model": self.model.snapshot(),
                "runtime": self.ledger.snapshot(),
                "adaptive_learning": self.learning.snapshot(),
                "tool_literacy": self.tool_literacy.snapshot(),
                "mentor_calibration": self.calibration.snapshot(),
                "mentor_profile": self.mentor_profile(),
                "last_cross_environment": self._last_cross_environment.to_dict(),
                "role_orchestration": {
                    "schema": "eris.companion_mentor_role.runtime.v1",
                    "deprecated": True,
                    "delivery_owner": "adaptive_companion",
                    "note_vi": "V84 role router đã rời active path; xem AdaptiveCompanion directive.",
                },
                "last_role_directive": self._last_role_directive.to_dict(),
                "last_flow": dict(self._last_flow),
                "last_companion_context": dict(self._last_companion_context),
                "last_policy_decision": self._last_policy_decision.to_dict(),
                "last_decision": self._last_decision.to_dict(),
            }


__all__ = [
    "EngineeringMentorService",
    "EngineeringTeachingPolicy",
    "MentorDecision",
    "MentorInterventionLedger",
    "SkillEstimate",
    "UserEngineeringSkillModel",
]

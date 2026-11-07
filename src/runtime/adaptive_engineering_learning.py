# -*- coding: utf-8 -*-
"""V81 - Adaptive Learning cho Engineering Mentor.

Module này KHÔNG phải một LMS và KHÔNG phải coding agent.
Nó chỉ giữ một projection nhỏ về việc user đã *thực sự thể hiện* hiểu những
khái niệm kỹ thuật nào, khái niệm nào nên kiểm tra lại, và có candidate
misconception nào đủ rõ để Eris hỏi lại.

Nguyên tắc evidence:
- GitPilot làm sai => chỉ là exposure/teachable moment, không chứng minh user yếu.
- User nói "hiểu rồi" => self-report, không tăng mastery.
- Mastery chỉ tăng từ hành vi có evidence (grounded hypothesis, chủ động verify,
  hoặc evidence tương đương mà mentor đã nhận diện).
- Misconception chỉ ghi dạng *candidate* khi user phát biểu một heuristic sai
  khá rõ; câu hỏi/hoài nghi không bị gán nhãn hiểu sai.
- Không lưu raw chat, source code hay chain-of-thought.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from runtime.workspace_manager import default_eris_home

_SCHEMA = "eris.engineering_learning.v1"


@dataclass(frozen=True, slots=True)
class ConceptDefinition:
    concept_id: str
    skill: str
    title_vi: str
    finding_codes: tuple[str, ...] = ()


_CONCEPTS: tuple[ConceptDefinition, ...] = (
    ConceptDefinition(
        "debugging.inspect_before_patch",
        "debugging.failure_localization",
        "Đọc evidence/call site trước khi sửa",
        ("patch_before_inspection",),
    ),
    ConceptDefinition(
        "debugging.root_cause_before_retry",
        "debugging.root_cause_reasoning",
        "Tìm root cause thay vì retry mù",
        ("repeated_same_failure", "repeated_test_failure"),
    ),
    ConceptDefinition(
        "debugging.trace_with_evidence",
        "debugging.call_flow_tracing",
        "Trace call-flow bằng file/symbol evidence",
    ),
    ConceptDefinition(
        "ai.scope_before_execution",
        "ai_operation.task_scoping",
        "Khóa scope trước khi cho agent execute",
        ("scope_expansion",),
    ),
    ConceptDefinition(
        "verification.targeted_tests",
        "verification.testing",
        "Dùng verification/test phù hợp để chứng minh fix",
        ("no_structured_verification",),
    ),
    ConceptDefinition(
        "verification.review_ai_diff",
        "verification.diff_review",
        "Review diff/evidence trước khi tin output AI",
        ("review_not_observed",),
    ),
)

_CONCEPT_BY_ID = {item.concept_id: item for item in _CONCEPTS}
_CONCEPTS_BY_SKILL: dict[str, tuple[str, ...]] = {}
for _concept in _CONCEPTS:
    _CONCEPTS_BY_SKILL.setdefault(_concept.skill, ())
    _CONCEPTS_BY_SKILL[_concept.skill] = (*_CONCEPTS_BY_SKILL[_concept.skill], _concept.concept_id)
_FINDING_TO_CONCEPT: dict[str, str] = {
    code: item.concept_id for item in _CONCEPTS for code in item.finding_codes
}

# Chỉ match assertion khá rõ. Nếu câu có dấu hỏi/question markers thì bỏ qua.
_MISCONCEPTION_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "tests_pass_means_done",
        "verification.targeted_tests",
        re.compile(r"(?:test|tests?).{0,18}(?:pass|xanh).{0,24}(?:là đủ|là xong|thế là xong|khỏi.*(?:review|diff))", re.I),
    ),
    (
        "ai_output_needs_no_review",
        "verification.review_ai_diff",
        re.compile(r"(?:ai|agent|gitpilot).{0,30}(?:sửa|code|patch).{0,30}(?:khỏi|không cần).{0,20}(?:đọc|xem|review).{0,12}diff", re.I),
    ),
    (
        "retry_until_green_is_debugging",
        "debugging.root_cause_before_retry",
        re.compile(r"(?:retry|chạy lại|sửa đại).{0,30}(?:đến khi|tới khi|miễn).{0,20}(?:pass|xanh|hết lỗi)", re.I),
    ),
)
_QUESTION_MARKERS = ("?", "có phải", "đúng không", "phải không", "hay là", "ổn không", "được không")
_MISCONCEPTION_CODE_TO_CONCEPT = {code: concept_id for code, concept_id, _ in _MISCONCEPTION_RULES}


@dataclass(slots=True)
class ConceptState:
    mastery: float = 0.30
    confidence: float = 0.08
    exposures: int = 0
    demonstrated_count: int = 0
    self_report_count: int = 0
    misconception_count: int = 0
    last_seen_at: float = 0.0
    last_demonstrated_at: float = 0.0
    next_review_at: float = 0.0
    last_prompted_at: float = 0.0
    last_prompted_session: int = 0
    misconception_codes: list[str] = field(default_factory=list)

    def clamp(self) -> None:
        self.mastery = max(0.0, min(1.0, float(self.mastery)))
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        self.exposures = max(0, int(self.exposures))
        self.demonstrated_count = max(0, int(self.demonstrated_count))
        self.self_report_count = max(0, int(self.self_report_count))
        self.misconception_count = max(0, int(self.misconception_count))
        self.misconception_codes = [str(x)[:80] for x in self.misconception_codes[-8:] if str(x).strip()]

    @property
    def stage(self) -> str:
        if self.demonstrated_count >= 4 and self.mastery >= 0.72 and self.confidence >= 0.25:
            return "retained"
        if self.demonstrated_count >= 2 and self.mastery >= 0.56:
            return "consolidating"
        if self.exposures > 0 or self.demonstrated_count > 0:
            return "practicing"
        return "new"

    def to_dict(self) -> dict[str, Any]:
        self.clamp()
        return {
            "mastery": round(self.mastery, 4),
            "confidence": round(self.confidence, 4),
            "stage": self.stage,
            "exposures": self.exposures,
            "demonstrated_count": self.demonstrated_count,
            "self_report_count": self.self_report_count,
            "misconception_count": self.misconception_count,
            "last_seen_at": self.last_seen_at,
            "last_demonstrated_at": self.last_demonstrated_at,
            "next_review_at": self.next_review_at,
            "last_prompted_at": self.last_prompted_at,
            "last_prompted_session": self.last_prompted_session,
            "misconception_codes": list(self.misconception_codes),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ConceptState":
        item = cls(
            mastery=float(raw.get("mastery", 0.30) or 0.30),
            confidence=float(raw.get("confidence", 0.08) or 0.08),
            exposures=int(raw.get("exposures", 0) or 0),
            demonstrated_count=int(raw.get("demonstrated_count", 0) or 0),
            self_report_count=int(raw.get("self_report_count", 0) or 0),
            misconception_count=int(raw.get("misconception_count", 0) or 0),
            last_seen_at=float(raw.get("last_seen_at", 0.0) or 0.0),
            last_demonstrated_at=float(raw.get("last_demonstrated_at", 0.0) or 0.0),
            next_review_at=float(raw.get("next_review_at", 0.0) or 0.0),
            last_prompted_at=float(raw.get("last_prompted_at", 0.0) or 0.0),
            last_prompted_session=int(raw.get("last_prompted_session", 0) or 0),
            misconception_codes=list(raw.get("misconception_codes") or []),
        )
        item.clamp()
        return item


@dataclass(slots=True)
class LearningDirective:
    kind: str = "none"  # none|teach_back|micro_practice|correct_candidate_misconception
    concept_id: str = ""
    title_vi: str = ""
    reason: str = ""
    prompt_style: str = ""
    due: bool = False

    @property
    def active(self) -> bool:
        return self.kind != "none" and bool(self.concept_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.engineering_learning.directive.v1",
            "kind": self.kind,
            "concept_id": self.concept_id,
            "title_vi": self.title_vi,
            "reason": self.reason,
            "prompt_style": self.prompt_style,
            "due": self.due,
        }


class AdaptiveEngineeringLearning:
    """Projection học tập nhẹ, dùng bởi V81 Engineering Mentor.

    State này tách khỏi user skill model V80:
    - skill model trả lời "user đang thể hiện năng lực ở mức nào";
    - learning model trả lời "khái niệm nào đã được luyện/chứng minh và khi nào nên
      kiểm tra lại".
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        logger: Any = None,
        learning_service: Any = None,
    ) -> None:
        self.path = path or (default_eris_home() / "mentor" / "engineering_learning.json")
        self.logger = logger
        # Khi được inject, LearningService là durable truth. JSON V81 chỉ còn là
        # projection/cache để code legacy tiếp tục đọc plan/snapshot không vỡ API.
        self.learning_service = learning_service
        self.concepts: dict[str, ConceptState] = {item.concept_id: ConceptState() for item in _CONCEPTS}
        self.recent_events: list[dict[str, Any]] = []
        self.updated_at = 0.0
        self._lock = threading.RLock()
        self._load()

    def note_coding_flow(self, flow: Mapping[str, Any], *, now: float | None = None) -> None:
        """GitPilot flow chỉ tạo exposure; tuyệt đối không tăng/giảm mastery user."""
        ts = float(now or flow.get("ended_at") or flow.get("started_at") or time.time())
        findings = [
            str(x.get("code") or "") for x in (flow.get("findings") or [])
            if isinstance(x, Mapping)
        ]
        touched: list[str] = []
        with self._lock:
            for code in findings:
                concept_id = _FINDING_TO_CONCEPT.get(code)
                if not concept_id:
                    continue
                state = self.concepts[concept_id]
                state.exposures += 1
                state.last_seen_at = max(state.last_seen_at, ts)
                # Nếu chưa có lịch review, để một khoảng thở. V81 không được thấy
                # flow fail xong quay sang quiz user ngay lập tức ngoài ngữ cảnh.
                if state.next_review_at <= 0.0:
                    state.next_review_at = ts + 24 * 3600.0
                touched.append(concept_id)
            if touched:
                self._event_locked("flow_exposure", ts, concepts=touched, ref=str(flow.get("task_id") or "")[:120])
                self.updated_at = ts
                self._save_locked()

    def observe_user_turn(
        self,
        user_text: str,
        *,
        signals: Sequence[str] = (),
        focus_skills: Sequence[str] = (),
        feedback: Sequence[str] = (),
        evidence_ref: str = "",
        now: float | None = None,
    ) -> tuple[str, ...]:
        """Cập nhật mastery bằng evidence hẹp và phát hiện candidate misconception.

        Không lưu user_text. Caller dùng turn_id/evidence_ref ngắn để audit.
        """
        ts = float(now or time.time())
        text = " ".join(str(user_text or "").split()).strip()
        low = text.casefold()
        signals = tuple(str(x) for x in signals)
        focus_skills = tuple(str(x) for x in focus_skills)
        feedback = tuple(str(x) for x in feedback)
        events: list[str] = []

        # Xác định candidate misconception trước positive evidence để một câu kiểu
        # "test pass là đủ, khỏi review" không vô tình được tính mastery chỉ vì
        # regex V80 nhìn thấy chữ "review".
        misconception_hits: list[tuple[str, str]] = []
        if text and not any(marker in low for marker in _QUESTION_MARKERS):
            for code, concept_id, pattern in _MISCONCEPTION_RULES:
                if pattern.search(text):
                    misconception_hits.append((code, concept_id))
        misconception_concepts = {concept_id for _, concept_id in misconception_hits}

        with self._lock:
            demonstrated: list[str] = []

            def demonstrate(concept_id: str, strength: float) -> None:
                if concept_id in demonstrated or concept_id in misconception_concepts:
                    return
                self._demonstrate_locked(concept_id, ts, strength=strength, evidence_ref=evidence_ref)
                demonstrated.append(concept_id)

            if "grounded_hypothesis" in signals:
                demonstrate("debugging.trace_with_evidence", 1.0)
                demonstrate("debugging.root_cause_before_retry", 1.0)
            if "verification_intent" in signals:
                demonstrate("verification.targeted_tests", 0.75)
                demonstrate("verification.review_ai_diff", 0.75)

            # Nếu turn này là câu trả lời cho adaptive prompt gần nhất, chỉ ghi
            # evidence khi câu trả lời có dấu hiệu áp dụng đúng loại hành vi. Đây
            # không phải semantic grading; nó chỉ là retrieval/practice evidence.
            prompted = self._most_recent_prompted_concept_locked(ts, max_age=30 * 60.0)
            prompted_evidence = self._prompted_practice_evidence(prompted, text, signals) if prompted else False
            if prompted_evidence:
                demonstrate(prompted, 0.85)
                events.append("teachback_evidence")

            # Nếu user nói "hiểu rồi", chỉ ghi self-report cho concept vừa được
            # mentor hỏi gần đây; không tăng mastery.
            if "mentor_understood" in feedback:
                recent = self._most_recent_prompted_concept_locked(ts, max_age=30 * 60.0)
                if recent:
                    state = self.concepts[recent]
                    state.self_report_count += 1
                    state.last_seen_at = max(state.last_seen_at, ts)
                    state.next_review_at = self._earlier_positive(state.next_review_at, ts + 12 * 3600.0)
                    events.append("self_report_understood")
                    self._event_locked("self_report", ts, concepts=[recent], ref=evidence_ref)

            # Misconception chỉ match assertion; câu hỏi không bị gán nhãn.
            for code, concept_id in misconception_hits:
                state = self.concepts[concept_id]
                state.misconception_count += 1
                state.misconception_codes.append(code)
                state.last_seen_at = max(state.last_seen_at, ts)
                if self.learning_service is not None:
                    # Candidate rõ từ assertion được ghi thành append-only evidence.
                    # Mastery thật sau đó được recompute bởi LearningService; adapter
                    # tuyệt đối không tự trừ mastery thêm lần nữa.
                    self.learning_service.commit_evidence(
                        concept_id,
                        evidence_type="misconception",
                        strength=1.0,
                        correct=False,
                        turn_id=evidence_ref,
                        error_code=code,
                        now=ts,
                    )
                    self._sync_from_kernel_locked(concept_id)
                else:
                    # Compatibility mode cho caller/test cũ chưa inject kernel.
                    state.mastery -= 0.05 * max(0.30, state.mastery)
                    state.confidence = min(1.0, state.confidence + 0.04)
                    state.next_review_at = self._earlier_positive(state.next_review_at, ts + 30 * 60.0)
                state.clamp()
                events.append(f"candidate_misconception:{code}")
                self._event_locked("candidate_misconception", ts, concepts=[concept_id], code=code, ref=evidence_ref)

            # Một focus skill chưa tạo evidence mới. Nó chỉ giúp chọn concept liên quan
            # cho review sau này, nên không mutate ở đây.
            if demonstrated:
                self._event_locked("demonstration", ts, concepts=sorted(set(demonstrated)), ref=evidence_ref)
                events.append("demonstrated_understanding")

            if demonstrated or misconception_hits or "mentor_understood" in feedback:
                self.updated_at = ts
                self._save_locked()
        return tuple(events)

    def plan(
        self,
        *,
        decision_mode: str,
        focus_skills: Sequence[str] = (),
        findings: Sequence[str] = (),
        signals: Sequence[str] = (),
        session_serial: int = 0,
        now: float | None = None,
        compact: bool = False,
    ) -> LearningDirective:
        """Chọn tối đa một teach-back/micro-practice liên quan turn hiện tại.

        Không có background reminder. `next_review_at` chỉ được dùng khi user đang
        nói đúng domain liên quan và mentor intervention đã được V80.1 cho phép.
        """
        ts = float(now or time.time())
        if decision_mode not in {"explain", "guide", "hint", "challenge", "warn"}:
            return LearningDirective()
        explicit_learning = "learning_intent" in tuple(signals)
        explicit_verification = "verification_intent" in tuple(signals)

        candidates = self._candidate_concepts(focus_skills=focus_skills, findings=findings)
        if not candidates:
            return LearningDirective()

        with self._lock:
            # Tối đa hai adaptive prompt trong cùng mentor session. User explicit
            # learning vẫn nhận lời giải V80 bình thường; chỉ không bị biến mỗi câu
            # hỏi thành quiz. Same-concept cooldown bên dưới còn chặt hơn.
            prompted_this_session = sum(
                1 for state in self.concepts.values()
                if state.last_prompted_session == int(session_serial) and session_serial > 0
            )
            if prompted_this_session >= 2:
                return LearningDirective()

            ranked: list[tuple[int, float, str]] = []
            for concept_id in candidates:
                state = self.concepts[concept_id]
                due = state.next_review_at > 0.0 and state.next_review_at <= ts
                has_candidate_misconception = state.misconception_count > 0 and (
                    not state.last_demonstrated_at or state.last_demonstrated_at < state.last_seen_at
                )
                # Không prompt lại cùng concept trong 6h kể cả session serial thay đổi bất thường.
                if state.last_prompted_at > 0.0 and ts - state.last_prompted_at < 6 * 3600.0:
                    continue
                priority = 0
                if has_candidate_misconception:
                    priority = 30
                elif due:
                    priority = 20
                elif explicit_learning and state.exposures > 0:
                    priority = 12
                elif explicit_verification and state.demonstrated_count == 0:
                    priority = 8
                if priority:
                    ranked.append((priority, -state.mastery, concept_id))
            if not ranked:
                return LearningDirective()
            ranked.sort(reverse=True)
            concept_id = ranked[0][2]
            state = self.concepts[concept_id]
            definition = _CONCEPT_BY_ID[concept_id]
            has_candidate_misconception = state.misconception_count > 0 and (
                not state.last_demonstrated_at or state.last_demonstrated_at < state.last_seen_at
            )
            due = state.next_review_at > 0.0 and state.next_review_at <= ts
            if has_candidate_misconception:
                kind = "correct_candidate_misconception"
                reason = "candidate_misconception_needs_check"
                style = "Hỏi user giải thích lại nguyên tắc bằng một ví dụ nhỏ; sửa misconception nếu câu trả lời vẫn sai."
            elif due:
                kind = "teach_back"
                reason = "spaced_review_due"
                style = "Hỏi một câu teach-back ngắn, yêu cầu user tự nêu bước/logic thay vì nhắc lại định nghĩa."
            else:
                kind = "micro_practice"
                reason = "fresh_learning_needs_retrieval"
                style = "Sau khi giải thích, cho đúng một micro-task/câu hỏi để user tự áp dụng ngay."
            if compact:
                style += " Giữ câu hỏi cực ngắn."
            return LearningDirective(kind, concept_id, definition.title_vi, reason, style, due)

    def register_prompt(self, directive: LearningDirective, *, session_serial: int, now: float | None = None) -> None:
        if not directive.active:
            return
        ts = float(now or time.time())
        with self._lock:
            state = self.concepts.get(directive.concept_id)
            if state is None:
                return
            state.last_prompted_at = ts
            state.last_prompted_session = int(session_serial)
            state.last_seen_at = max(state.last_seen_at, ts)
            self._event_locked("adaptive_prompt", ts, concepts=[directive.concept_id], code=directive.kind)
            self.updated_at = ts
            self._save_locked()

    def focus_skills_for_events(self, events: Sequence[str]) -> tuple[str, ...]:
        """Map learning event -> skill để TeachingPolicy biết nên nói về gì.

        Chỉ event code được map; không đọc lại raw user text.
        """
        out: list[str] = []
        for event in events:
            text = str(event or "")
            if not text.startswith("candidate_misconception:"):
                continue
            code = text.split(":", 1)[1]
            concept_id = _MISCONCEPTION_CODE_TO_CONCEPT.get(code)
            definition = _CONCEPT_BY_ID.get(concept_id or "")
            if definition and definition.skill not in out:
                out.append(definition.skill)
        return tuple(out)

    def projection(self, concept_ids: Sequence[str]) -> dict[str, Any]:
        with self._lock:
            out: dict[str, Any] = {}
            for concept_id in concept_ids:
                definition = _CONCEPT_BY_ID.get(str(concept_id))
                state = self.concepts.get(str(concept_id))
                if definition is None or state is None:
                    continue
                out[concept_id] = {
                    "title_vi": definition.title_vi,
                    "skill": definition.skill,
                    **state.to_dict(),
                }
            return out

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": _SCHEMA,
                "updated_at": self.updated_at,
                "concepts": {
                    item.concept_id: {
                        "title_vi": item.title_vi,
                        "skill": item.skill,
                        **self.concepts[item.concept_id].to_dict(),
                    }
                    for item in _CONCEPTS
                },
                "recent_events": list(self.recent_events[-30:]),
            }

    def _candidate_concepts(self, *, focus_skills: Sequence[str], findings: Sequence[str]) -> list[str]:
        out: list[str] = []
        for code in findings:
            concept_id = _FINDING_TO_CONCEPT.get(str(code))
            if concept_id and concept_id not in out:
                out.append(concept_id)
        for skill in focus_skills:
            for concept_id in _CONCEPTS_BY_SKILL.get(str(skill), ()):
                if concept_id not in out:
                    out.append(concept_id)
        return out

    @staticmethod
    def _prompted_practice_evidence(concept_id: str, text: str, signals: Sequence[str]) -> bool:
        low = " ".join(str(text or "").casefold().split())
        if not low or any(marker in low for marker in _QUESTION_MARKERS):
            return False
        signals = tuple(str(x) for x in signals)
        if concept_id in {"debugging.trace_with_evidence", "debugging.root_cause_before_retry"}:
            if "grounded_hypothesis" in signals:
                return True
        if concept_id == "debugging.inspect_before_patch":
            return (("đọc" in low or "inspect" in low or "trace" in low) and
                    ("trước" in low) and ("sửa" in low or "patch" in low or "edit" in low))
        if concept_id == "debugging.root_cause_before_retry":
            return (("root cause" in low or "nguyên nhân" in low) and
                    ("retry" in low or "chạy lại" in low or "sửa" in low))
        if concept_id == "ai.scope_before_execution":
            return (("scope" in low or "phạm vi" in low) and
                    ("file" in low or "task" in low or "không đụng" in low or "chỉ sửa" in low))
        if concept_id == "verification.targeted_tests":
            return ("test" in low and any(x in low for x in ("liên quan", "target", "regression", "verify", "chứng minh")))
        if concept_id == "verification.review_ai_diff":
            return ("diff" in low and any(x in low for x in ("review", "đọc", "xem", "soát", "kiểm tra")))
        return False

    def _demonstrate_locked(
        self,
        concept_id: str,
        ts: float,
        *,
        strength: float,
        evidence_ref: str = "",
    ) -> None:
        state = self.concepts[concept_id]
        state.demonstrated_count += 1
        state.exposures += 1
        state.last_seen_at = max(state.last_seen_at, ts)
        state.last_demonstrated_at = ts

        if self.learning_service is not None:
            # Kernel-authoritative mode: chỉ phát evidence, không mutate mastery.
            self.learning_service.commit_evidence(
                concept_id,
                evidence_type="application",
                strength=max(0.0, min(1.0, float(strength))),
                correct=True,
                turn_id=evidence_ref,
                now=ts,
            )
            self._sync_from_kernel_locked(concept_id)
        else:
            # Compatibility mode V81 cũ. Xóa branch này sau khi mọi caller đã
            # dùng LearningService và migration JSON -> SQLite hoàn tất.
            delta = 0.08 * max(0.35, min(1.0, float(strength)))
            state.mastery += delta * (1.0 - state.mastery)
            state.confidence = min(1.0, state.confidence + 0.055 * strength)
            state.next_review_at = ts + self._review_interval_seconds(state)
        state.clamp()

    def _sync_from_kernel_locked(self, concept_id: str) -> None:
        """Đồng bộ projection legacy từ durable truth mà không đọc store private."""
        if self.learning_service is None:
            return
        state = self.concepts[concept_id]
        record = self.learning_service.get_mastery(concept_id)
        state.mastery = record.mastery
        state.confidence = record.confidence
        reviews = self.learning_service.review_snapshot((concept_id,))
        review = reviews.get(concept_id)
        if isinstance(review, Mapping):
            state.next_review_at = float(review.get("due_at", state.next_review_at) or state.next_review_at)
        state.clamp()

    @staticmethod
    def _review_interval_seconds(state: ConceptState) -> float:
        count = state.demonstrated_count
        if count <= 1:
            return 24 * 3600.0
        if count == 2:
            return 3 * 86400.0
        if count == 3:
            return 7 * 86400.0
        if state.mastery >= 0.78:
            return 30 * 86400.0
        return 14 * 86400.0

    def _most_recent_prompted_concept_locked(self, ts: float, *, max_age: float) -> str:
        best_id = ""
        best_at = 0.0
        for concept_id, state in self.concepts.items():
            if state.last_prompted_at > best_at and 0.0 <= ts - state.last_prompted_at <= max_age:
                best_id = concept_id
                best_at = state.last_prompted_at
        return best_id

    @staticmethod
    def _earlier_positive(current: float, proposed: float) -> float:
        if current <= 0.0:
            return proposed
        return min(current, proposed)

    def _event_locked(
        self,
        event_type: str,
        ts: float,
        *,
        concepts: Sequence[str] = (),
        code: str = "",
        ref: str = "",
    ) -> None:
        self.recent_events.append({
            "at": ts,
            "event": str(event_type)[:80],
            "concepts": [str(x)[:120] for x in concepts[:6]],
            "code": str(code or "")[:80],
            "evidence_ref": str(ref or "")[:120],
        })
        self.recent_events = self.recent_events[-60:]

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping) or str(raw.get("schema") or "") != _SCHEMA:
                return
            concepts = raw.get("concepts")
            if isinstance(concepts, Mapping):
                for concept_id in self.concepts:
                    item = concepts.get(concept_id)
                    if isinstance(item, Mapping):
                        self.concepts[concept_id] = ConceptState.from_dict(item)
            self.recent_events = [dict(x) for x in (raw.get("recent_events") or []) if isinstance(x, Mapping)][-60:]
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
            # Adaptive learning chỉ là projection hỗ trợ; persistence lỗi không được
            # làm hỏng chat hay coding flow.
            pass


__all__ = [
    "AdaptiveEngineeringLearning",
    "ConceptDefinition",
    "ConceptState",
    "LearningDirective",
]

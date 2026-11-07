# -*- coding: utf-8 -*-
"""Tutor Executive: vòng điều hành sư phạm cấp cao của Eris.

Luồng chính:
    OBSERVE -> DIAGNOSE -> STRATEGIZE -> TEACHING MOVE -> WAIT -> ASSESS -> LOOP

Phân quyền cố ý rõ ràng:
- LearningService: nguồn sự thật về path/mastery/evidence/review.
- TutorReasoner: semantic judgement + chiến lược mềm.
- TutorGuardrailPolicy: giới hạn deterministic cho từng phase.
- TutorExecutive: orchestration + session hypothesis; không tự viết public text.
- PedagogicalRuntime: delivery/dwell/receipt (được nối ở integration layer sau).
"""
from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import replace
from typing import Any, Iterable, Mapping, Sequence

from runtime.learning_core import LearnerProfile, LearningService
from runtime.learning_core.contracts import EVIDENCE_TYPES, MasteryRecord
from runtime.learning_core.policy import TutorStep
from runtime.tutor.contracts import (
    ContextTurn,
    LearnerHypothesis,
    SemanticAssessment,
    TutorDecision,
    TutorObservation,
    TutorState,
)
from runtime.tutor.policy import TutorGuardrailPolicy
from runtime.tutor.adaptive_policy import choose_adaptive_step
from runtime.tutor.reasoner import ConservativeTutorReasoner, TutorReasoner
from runtime.tutor.session_store import TutorSessionLedger


class TutorExecutive:
    """Owner duy nhất của tutor cognition loop trong một Eris process."""

    def __init__(
        self,
        learning_service: LearningService,
        *,
        reasoner: TutorReasoner | None = None,
        policy: TutorGuardrailPolicy | None = None,
        semantic_commit_confidence: float = 0.68,
        session_ttl_seconds: float = 2 * 3600.0,
        session_ledger: TutorSessionLedger | None = None,
        logger: Any = None,
    ) -> None:
        if not isinstance(learning_service, LearningService):
            raise TypeError("learning_service must be LearningService")
        self.learning = learning_service
        self.reasoner = reasoner or ConservativeTutorReasoner()
        self.policy = policy or TutorGuardrailPolicy()
        self.semantic_commit_confidence = max(0.50, min(0.95, float(semantic_commit_confidence)))
        self.session_ttl_seconds = max(300.0, min(24 * 3600.0, float(session_ttl_seconds)))
        self.logger = logger
        self.session_ledger = session_ledger or TutorSessionLedger(logger=logger)
        restored = self.session_ledger.load()
        self._state = restored.state
        # Receipt/assessment đều có thể bị retry bởi transport/runtime. Hai ledger
        # nhỏ này giữ side-effect idempotent trong process hiện tại. Durable truth
        # vẫn do LearningStore dedupe bằng evidence_id.
        self._committed_move_ids: dict[str, None] = {item: None for item in restored.committed_move_ids}
        self._assessed_response_keys: dict[str, None] = {item: None for item in restored.assessed_response_keys}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Public cycle API
    # ------------------------------------------------------------------

    def plan_next_move(
        self,
        user_text: str,
        *,
        turn_id: str = "",
        learner: LearnerProfile | None = None,
        recent_history: Sequence[Mapping[str, Any] | ContextTurn] = (),
        signals: Sequence[str] = (),
        now: float | None = None,
    ) -> TutorDecision | None:
        """Lập teaching move tiếp theo, chưa commit delivery side-effect."""
        ts = float(now if now is not None else time.time())
        learner = learner or LearnerProfile()
        with self._lock:
            path = self.learning.active_path()
            if path is None:
                return None
            step = self.learning.next_tutor_step(learner=learner, now=ts)
            if step is None:
                return None
            # LEARN-04: đọc ledger đã commit rồi mới chọn cách dạy.
            # Root vẫn nắm quyền gọi capability; quyết định này không tự tạo turn.
            if step.kp_id:
                point = next((kp for kp in path.knowledge_points if kp.kp_id == step.kp_id), None)
                step = choose_adaptive_step(
                    step,
                    evidence=self.learning.recent_evidence(step.kp_id),
                    repeated_error_count=len(self.learning.repeated_error_patterns(step.kp_id)),
                    knowledge_type=point.kp_type if point is not None else "concept",
                )

            state = self._ensure_session(path.path_id, path.goal, ts)
            observation = self._build_observation(
                user_text=user_text,
                turn_id=turn_id,
                learner=learner,
                recent_history=recent_history,
                signals=signals,
                now=ts,
                step=step,
            )
            diagnosis = self.reasoner.diagnose(observation, state)
            state = self._merge_hypotheses(state, diagnosis.hypotheses, ts)
            envelope = self.policy.envelope(step, diagnosis, learner)
            proposal = self.reasoner.choose_move(observation, state, diagnosis, envelope)
            move = self.policy.enforce(proposal, envelope, kp_id=observation.kp_id)
            # Identity là runtime concern, không giao cho model tự phát ID.
            move = replace(
                move,
                move_id=self._move_id(state, observation, move.kind),
            )
            state = replace(
                state,
                kp_id=observation.kp_id,
                last_move=move,
                turn_count=state.turn_count + 1,
                updated_at=ts,
            )
            self._state = state
            self._persist_session_locked()
            return TutorDecision(step, diagnosis, envelope, move, state)

    def observe_response(
        self,
        user_text: str,
        *,
        turn_id: str = "",
        learner: LearnerProfile | None = None,
        recent_history: Sequence[Mapping[str, Any] | ContextTurn] = (),
        signals: Sequence[str] = (),
        now: float | None = None,
    ) -> SemanticAssessment | None:
        """Đánh giá một response *đã biết là trả lời tutor* rồi commit evidence nếu đủ chắc.

        Caller phải chỉ gọi method này khi conversation runtime biết user đang trả lời
        teaching move trước. Như vậy một câu user chen ngang sẽ không bị chấm nhầm.
        """
        ts = float(now if now is not None else time.time())
        learner = learner or LearnerProfile()
        with self._lock:
            previous = self._state.last_move
            if previous is None or not previous.requires_user_response:
                return None
            response_key = f"{previous.move_id}|{str(turn_id or '')[:120]}"
            if response_key in self._assessed_response_keys:
                return None
            path = self.learning.active_path()
            if path is None:
                return None
            if path.path_id != self._state.path_id or previous.kp_id not in path.kp_ids:
                return None
            step = self.learning.next_tutor_step(learner=learner, now=ts)
            if step is None:
                return None
            observation = self._build_observation(
                user_text=user_text,
                turn_id=turn_id,
                learner=learner,
                recent_history=recent_history,
                signals=signals,
                now=ts,
                step=step,
                forced_kp_id=previous.kp_id,
            )
            assessment = self.reasoner.assess_response(observation, self._state, previous)
            if assessment is None:
                return None
            if assessment.kp_id != previous.kp_id:
                return None
            committed = self._apply_semantic_assessment(
                assessment, interaction_id=previous.move_id, turn_id=turn_id, now=ts,
                review_recall=(previous.kind == "review" and
                               previous.move_id in self._committed_move_ids),
            )
            # Hypothesis chỉ được đổi lifecycle khi semantic judgement cũng đủ
            # chắc để trở thành learning evidence. Assessment yếu vẫn trả cho
            # caller quan sát nhưng không được đóng khung learner state.
            if committed:
                self._state = self._update_hypotheses_from_assessment(self._state, assessment, ts)
            self._assessed_response_keys[response_key] = None
            while len(self._assessed_response_keys) > 128:
                self._assessed_response_keys.pop(next(iter(self._assessed_response_keys)))
            self._persist_session_locked()
            return assessment

    def suspend_pending_move(self, *, reason: str = "user_interrupt", now: float | None = None) -> bool:
        """Hủy quyền chấm của TeachingMove đang chờ khi user đổi hướng.

        Nếu không clear ở đây, turn sau TutorRoleCoordinator sẽ nhìn ``last_move``
        cũ và có thể chấm một câu hoàn toàn không liên quan như câu trả lời bài học.
        Learning mastery/evidence không bị động tới; chỉ session interaction bị yield.
        """
        ts = float(now if now is not None else time.time())
        with self._lock:
            move = self._state.last_move
            if move is None or not move.requires_user_response:
                return False
            self._state = replace(
                self._state,
                last_move=None,
                last_delivery_text="",
                updated_at=ts,
            )
            self._persist_session_locked()
            self._log("debug", "[tutor] pending move suspended reason=%s", str(reason or "user_interrupt"))
            return True

    def process_turn(
        self,
        user_text: str,
        *,
        turn_id: str = "",
        response_to_previous_move: bool = False,
        learner: LearnerProfile | None = None,
        recent_history: Sequence[Mapping[str, Any] | ContextTurn] = (),
        signals: Sequence[str] = (),
        now: float | None = None,
    ) -> tuple[SemanticAssessment | None, TutorDecision | None]:
        """Convenience API cho foreground loop: assess response rồi lập move kế tiếp."""
        assessment = None
        if response_to_previous_move:
            assessment = self.observe_response(
                user_text,
                turn_id=turn_id,
                learner=learner,
                recent_history=recent_history,
                signals=signals,
                now=now,
            )
        decision = self.plan_next_move(
            user_text,
            turn_id=turn_id,
            learner=learner,
            recent_history=recent_history,
            signals=signals,
            now=now,
        )
        return assessment, decision

    def commit_delivery(
        self,
        decision: TutorDecision,
        *,
        successful: bool = True,
        public_text: str = "",
    ) -> None:
        """Commit side-effect chỉ sau khi public delivery thực sự thành công.

        Điều này giữ invariant: Eris *định nói* không đồng nghĩa Eris *đã dạy*.
        """
        if not successful:
            return
        with self._lock:
            step = decision.policy_step
            move = decision.move
            if move.move_id and move.move_id in self._committed_move_ids:
                return
            # Bind semantic move -> exact delivered wording. Turn sau assessor sẽ
            # nhìn cả TeachingMove lẫn câu public thật mà user đã nghe/đọc.
            if str(public_text or "").strip():
                self._state = replace(
                    self._state,
                    last_delivery_text=str(public_text),
                    updated_at=time.time(),
                )
            if move.kind in {"explain", "counterexample", "analogy", "worked_example", "repair"}:
                self.learning.note_taught(move.kp_id)
            if step.kind == "advance" and step.kp_id:
                self.learning.advance_kp(step.kp_id)
            if move.move_id:
                if move.requires_user_response:
                    self.learning._store.register_assistance(move.move_id, move.kp_id)
                self._committed_move_ids[move.move_id] = None
                while len(self._committed_move_ids) > 128:
                    self._committed_move_ids.pop(next(iter(self._committed_move_ids)))
            self._persist_session_locked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": "eris.tutor.executive.v1",
                "state": self._state.to_dict(),
                "learning": self.learning.snapshot(),
                "semantic_commit_confidence": self.semantic_commit_confidence,
                "committed_move_count": len(self._committed_move_ids),
                "assessed_response_count": len(self._assessed_response_keys),
            }

    def _persist_session_locked(self) -> None:
        """Persist bounded tutor session projection. Caller đang giữ ``_lock``.

        Failure ở đây không được phá public chat; durable learning truth vẫn nằm
        trong LearningStore. Ledger này chỉ phục vụ resume tự nhiên/idempotency.
        """
        try:
            self.session_ledger.commit(
                self._state,
                committed_move_ids=tuple(self._committed_move_ids),
                assessed_response_keys=tuple(self._assessed_response_keys),
            )
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "[tutor-session] persist degraded: %s", exc)

    # ------------------------------------------------------------------
    # Internal composition helpers
    # ------------------------------------------------------------------

    def _ensure_session(self, path_id: str, goal: str, ts: float) -> TutorState:
        expired = bool(
            self._state.updated_at
            and ts - self._state.updated_at > self.session_ttl_seconds
        )
        if self._state.path_id != path_id or expired or not self._state.session_id:
            digest = hashlib.sha256(f"{path_id}|{int(ts)}".encode()).hexdigest()[:12]
            self._state = TutorState(
                session_id=f"tutor:{digest}",
                path_id=path_id,
                session_goal=goal,
                started_at=ts,
                updated_at=ts,
            )
        return self._state

    def _build_observation(
        self,
        *,
        user_text: str,
        turn_id: str,
        learner: LearnerProfile,
        recent_history: Sequence[Mapping[str, Any] | ContextTurn],
        signals: Sequence[str],
        now: float,
        step: TutorStep,
        forced_kp_id: str = "",
    ) -> TutorObservation:
        path = self.learning.active_path()
        if path is None:
            raise RuntimeError("active_learning_path_disappeared")
        kp_id = forced_kp_id or step.kp_id
        kp = next((item for item in path.knowledge_points if item.kp_id == kp_id), None)
        if kp is None:
            # complete/wait có thể không có kp_id; dùng KP cuối chỉ làm context đọc.
            kp = path.knowledge_points[-1]
            kp_id = kp.kp_id
        mastery = self.learning.get_mastery(kp_id) if kp_id else MasteryRecord.blank("")
        history = tuple(self._normalize_history(recent_history))
        return TutorObservation(
            turn_id=turn_id,
            user_text=user_text,
            path_id=path.path_id,
            path_goal=path.goal,
            kp_id=kp_id,
            kp_title=kp.title,
            kp_description=kp.description,
            kp_type=kp.kp_type,
            policy_step=step,
            mastery=mastery,
            learner=learner,
            recent_history=history,
            signals=tuple(str(item) for item in signals),
            now=now,
            source_context=tuple({key: (value[:1600] if key == "excerpt" else value)
                                  for key, value in span.items()
                                  if key in {"span_id", "source_id", "locator", "excerpt", "source_kind", "content_hash", "origin"}}
                                 for span in self.learning.search_sources(kp.title + " " + kp.description, limit=3)),
        )

    @staticmethod
    def _normalize_history(
        history: Sequence[Mapping[str, Any] | ContextTurn],
    ) -> Iterable[ContextTurn]:
        for item in history[-6:]:
            if isinstance(item, ContextTurn):
                yield item
            elif isinstance(item, Mapping):
                yield ContextTurn(item.get("role", "user"), item.get("content", ""))

    def _merge_hypotheses(
        self,
        state: TutorState,
        incoming: Sequence[LearnerHypothesis],
        ts: float,
    ) -> TutorState:
        current = {item.hypothesis_id: item for item in state.hypotheses if item.hypothesis_id}
        for item in incoming:
            hypothesis_id = item.hypothesis_id or self._hypothesis_id(item.kind, item.kp_id, item.summary)
            previous = current.get(hypothesis_id)
            first_seen = previous.first_seen_at if previous else (item.first_seen_at or ts)
            current[hypothesis_id] = replace(
                item,
                hypothesis_id=hypothesis_id,
                first_seen_at=first_seen,
                last_seen_at=ts,
            )
        # Ưu tiên hypothesis active/confidence cao, nhưng giới hạn để state không phình vô hạn.
        ranked = sorted(
            current.values(),
            key=lambda item: (item.status != "active", -item.confidence, -item.last_seen_at),
        )[:8]
        return replace(state, hypotheses=tuple(ranked), updated_at=ts)

    def _apply_semantic_assessment(
        self,
        assessment: SemanticAssessment,
        *,
        interaction_id: str,
        turn_id: str,
        now: float,
        review_recall: bool = False,
    ) -> bool:
        if assessment.verdict == "uncertain" or assessment.confidence < self.semantic_commit_confidence:
            from runtime.learning_core.records import AssessmentAttempt, digest
            help_state = self.learning._store.assistance_for(interaction_id)
            self.learning.record_attempt(AssessmentAttempt(
                attempt_id="attempt:" + digest([assessment.kp_id, interaction_id, turn_id])[:32],
                kp_id=assessment.kp_id, result="ungraded", evidence_type="exposure",
                interaction_id=interaction_id, turn_id=turn_id,
                path_id=self._state.path_id, assistance=help_state["assistance"],
                hint_count=help_state.get("hint_count",0),
                assessor_version="semantic-v1", recorded_at=now,
            ))
            return False
        evidence_type = self._normalized_evidence_type(assessment)
        evidence = self.learning.commit_evidence(
            assessment.kp_id,
            evidence_type=evidence_type,
            strength=assessment.strength,
            correct=assessment.correct,
            turn_id=turn_id,
            interaction_id=interaction_id,
            error_code=assessment.error_code,
            review_recall=review_recall,
            item_family_id=("recall:" + hashlib.sha256(assessment.kp_id.encode()).hexdigest()[:32])
            if review_recall else interaction_id,
            result={"pass": "correct", "partial": "partial", "fail": "incorrect"}[assessment.verdict],
            now=now,
        )
        return evidence is not None

    @staticmethod
    def _normalized_evidence_type(assessment: SemanticAssessment) -> str:
        """Không cho semantic model tạo cặp verdict/evidence tự mâu thuẫn."""
        allowed_by_verdict = {
            "pass": {"feynman_pass", "application", "paraphrase", "quiz_correct"},
            "partial": {"application", "paraphrase"},
            "fail": {"feynman_fail", "misconception", "quiz_incorrect"},
        }
        allowed = allowed_by_verdict.get(assessment.verdict, set())
        if assessment.evidence_type in EVIDENCE_TYPES and assessment.evidence_type in allowed:
            return assessment.evidence_type
        if assessment.verdict == "pass":
            return "feynman_pass"
        if assessment.verdict == "partial":
            return "paraphrase"
        if assessment.verdict == "fail":
            return "misconception" if assessment.misconception else "feynman_fail"
        return "exposure"

    def _update_hypotheses_from_assessment(
        self,
        state: TutorState,
        assessment: SemanticAssessment,
        ts: float,
    ) -> TutorState:
        hypotheses = list(state.hypotheses)
        if assessment.verdict == "pass":
            # Khi user chứng minh hiểu, hạ các hypothesis active của cùng KP thay vì
            # giữ định kiến cũ bám dai sang các turn sau.
            hypotheses = [
                replace(item, status="resolved", confidence=min(item.confidence, 0.35), last_seen_at=ts)
                if item.kp_id == assessment.kp_id and item.status == "active"
                else item
                for item in hypotheses
            ]
        elif assessment.misconception and assessment.verdict in {"partial", "fail"}:
            candidate = LearnerHypothesis(
                hypothesis_id=self._hypothesis_id("misconception", assessment.kp_id, assessment.misconception),
                kind="misconception",
                kp_id=assessment.kp_id,
                summary=assessment.misconception,
                confidence=assessment.confidence,
                evidence_refs=(assessment.error_code,) if assessment.error_code else (),
                first_seen_at=ts,
                last_seen_at=ts,
            )
            return self._merge_hypotheses(replace(state, hypotheses=tuple(hypotheses)), (candidate,), ts)
        return replace(state, hypotheses=tuple(hypotheses), updated_at=ts)


    @staticmethod
    def _move_id(state: TutorState, observation: TutorObservation, kind: str) -> str:
        raw = (
            f"{state.session_id}|{observation.path_id}|{observation.kp_id}|"
            f"{observation.turn_id}|{state.turn_count}|{kind}"
        )
        digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]
        return f"tm:{observation.kp_id}:{digest}"[:120]

    @staticmethod
    def _hypothesis_id(kind: str, kp_id: str, summary: str) -> str:
        digest = hashlib.sha256(f"{kind}|{kp_id}|{summary}".encode("utf-8", errors="replace")).hexdigest()[:12]
        return f"hyp:{kp_id}:{digest}"[:120]


__all__ = ["TutorExecutive"]

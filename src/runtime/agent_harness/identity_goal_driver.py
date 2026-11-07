# -*- coding: utf-8 -*-
"""Cầu nối chính thức giữa Subject/Executive và Work Session.

Không tự chọn mục tiêu thay Executive. Một ý định chỉ được thực thi khi
SubjectService đã FINALIZE một MindDecision có action `pursue_goal`.
Cầu nối này không điều khiển phát ngôn và không tạo vòng agent nhận thức thứ hai.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass
import logging
import os
import sqlite3
import threading
import time
from typing import Any

from cortex.subject.contracts import ObservationOrigin, WorldObservation, WorldOutcome, OutcomeStatus
from runtime.autonomy.goal_events import GoalCandidate, GoalConflict, GoalEventLedger, SemanticCommitProof

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class GoalDecisionReceipt:
    """Quyết định thực đã được Subject finalize, không phải suy đoán từ prompt."""
    goal_id: str
    decision_id: str
    intention_id: str
    goal_title: str
    adoption_commit_id: str
    job_id: str = ""


class IdentityGoalDriver:
    """Adapter sự kiện trên heartbeat hiện hữu, KHÔNG là scheduler mới.

    Work Lane vẫn thuộc UnifiedAgentSessions. Deliberation dùng MindLoop hiện có.
    Một worker riêng tránh khóa chat khi Executive cần gọi LLM.
    """

    def __init__(self, subject: Any, mind_loop: Any, sessions: Any, *,
                 ledger: GoalEventLedger, max_workers: int = 1):
        self.subject = subject
        self.mind_loop = mind_loop
        self.sessions = sessions
        self.ledger = ledger
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ERIS-GoalDecision")
        self._future: Future | None = None
        self._lock = threading.RLock()
        self._considered: set[tuple[str, str]] = set()
        # Phản hồi được ghi theo outcome_id, không phát lại nhiều lần vào Subject.
        with self.ledger._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS autonomy_subject_feedback (
                outcome_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL,
                status TEXT NOT NULL, decision_id TEXT NOT NULL, updated_at REAL NOT NULL
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS autonomy_goal_reviews (
                outcome_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL,
                status TEXT NOT NULL, decision_id TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL
            )""")

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _intention(self):
        items = tuple(getattr(self.subject.initiative_state, "pending_intentions", ()) or ())
        choices = [i for i in items if str(getattr(getattr(i, "status", ""), "value", getattr(i, "status", ""))) == "pending"
                   and str(getattr(i, "semantic_goal", "") or "").strip()]
        choices.sort(key=lambda i: -(float(i.pressure) * (0.5 + 0.5 * float(i.commitment_strength))))
        for intention in choices:
            goal_id = "eris:intention:" + str(intention.intention_id)
            key = (str(intention.intention_id), str(intention.semantic_goal))
            if self.ledger.get(goal_id) is None and key not in self._considered:
                return intention
        return None

    def pump(self) -> dict:
        """Đánh thức bằng autonomous tick đã có; không tự tạo timer mới."""
        with self._lock:
            if self._future and not self._future.done():
                return {"status": "decision_running"}
            if self._future:
                try:
                    self._future.result()
                except Exception as exc:
                    _LOG.warning("[eris-goal] Lượt goal bị lỗi: %s", exc)
                self._future = None
            # Đưa outcome thật đã được goal ledger ghi về Subject trên owner loop.
            self._drain_feedback()
            review = self._next_review()
            if review is not None:
                self._future = self._pool.submit(self._review_outcome, *review)
                return {"status": "review_scheduled", "goal_id": review[0]}
            intention = self._intention()
            if intention is None:
                return {"status": "no_new_intention"}
            self._future = self._pool.submit(self._decide_and_start, intention)
            return {"status": "decision_scheduled", "intention_id": str(intention.intention_id)}

    def _decide_and_start(self, intention) -> GoalDecisionReceipt | None:
        """Chỉ commit sau khi MindLoop thật sự trả về winner `pursue_goal`."""
        intention_id = str(intention.intention_id)
        self._considered.add((intention_id, str(intention.semantic_goal)))
        revision = int(self.subject.initiative_state.revision)
        goal_id = "eris:intention:" + intention_id
        motive = str(getattr(getattr(intention, "motive_kind", ""), "value", getattr(intention, "motive_kind", "")))
        refs = tuple(getattr(intention, "evidence_refs", ()) or ())
        now = time.time()
        observation = WorldObservation(
            observation_id="goal-opportunity:" + intention_id + ":" + str(revision),
            subject_id=self.subject.config.subject_id, kind="autonomous_goal_opportunity",
            created_at=now, origin=ObservationOrigin.INTERNAL,
            context={
                "context_key": "autonomous_goal", "affected_need_ids": ("competence", "coherence"),
                "goal_relevance": 0.8, "novelty": 0.5, "controllability": 0.7,
                "identity_significance": 0.7, "uncertainty": 0.35,
                "primary_interpretation": "Subject có ý định muốn cân nhắc thành mục tiêu làm việc",
                "action_proposals": ({
                    "proposal_id": "goal-proposal:" + intention_id,
                    "action_kind": "pursue_goal",
                    "arguments": {"intention_id": intention_id, "goal_id": goal_id,
                                  "semantic_goal": str(intention.semantic_goal)},
                    "supporting_need_ids": ("competence", "coherence"),
                    "expected_need_relief": float(intention.pressure),
                    "expected_action_efficacy": max(0.45, float(intention.commitment_strength)),
                    "expected_epistemic_value": 0.65,
                    "value_congruence": float(intention.commitment_strength),
                    "substrate_cost": 0.12, "uncertainty": 0.25,
                    "project_contribution": 0.65,
                    "evidence_refs": refs or ("subject:intention:" + intention_id,),
                },),
            }, evidence_refs=refs or ("subject:intention:" + intention_id,),
            correlation_id="autonomous-goal:" + intention_id,
        )
        # Dùng MindLoop + committer_provider đang kết nối Executive, không
        # tự tạo một commit bằng heuristic/LLM text.
        decision = self.mind_loop.deliberate_sync(
            observation, mode="autonomous_goal", snapshot={"identity_goal_id": goal_id})
        if decision.proposed_action != "pursue_goal" or str(decision.arguments.get("goal_id", "")) != goal_id:
            return None
        # prepare_deliberation tự có thể tăng initiative revision khi cập nhật
        # motive; chỉ coi là đổi ý nếu chính intention đã mất/đổi nội dung.
        current = self.subject.initiative_state
        if not any(
            str(i.intention_id) == intention_id
            and str(getattr(i.status, "value", i.status)) == "pending"
            and str(i.semantic_goal) == str(intention.semantic_goal)
            for i in current.pending_intentions
        ):
            return None
        # Dùng receipt Executive THẬT được Subject finalize gắn vào decision.
        executive = dict(decision.arguments.get("executive_commit") or {})
        if (not executive.get("commit_id") or not executive.get("source") or
            executive.get("selected_proposal_ref") != "goal-proposal:" + intention_id):
            return None
        revision = int(current.revision)  # revision snapshot Subject tại thời điểm adopt
        candidate = GoalCandidate(
            goal_id=goal_id, title=str(intention.semantic_goal)[:800],
            success_criteria="Có kết quả công việc và bằng chứng được kiểm tra; Subject quyết định sau khi nhận outcome.",
            motive_ids=(motive,), identity_revision=revision,
            evidence_refs=(*refs, "subject-decision:" + decision.decision_id),
        )
        try:
            snap = self.ledger.propose(candidate, event_id="identity-propose:" + str(executive["commit_id"]))
            proof = SemanticCommitProof(
                commit_id=str(executive["commit_id"]), source=str(executive["source"]),
                identity_revision=revision, motive_ids=(motive,),
                evidence_refs=candidate.evidence_refs, episode_id="goal-episode:" + intention_id,
            )
            snap = self.ledger.transition(goal_id, expected_revision=snap.revision, status="active",
                                          event_id="identity-adopt:" + str(executive["commit_id"]), proof=proof)
        except GoalConflict:
            snap = self.ledger.get(goal_id)
            if not snap or snap.status != "active":
                raise
        _LOG.info("[eris-goal] Executive chấp nhận goal=%s commit=%s", goal_id, executive["commit_id"])
        response = self.sessions.execute({"operation": "start_goal", "goal_id": goal_id})
        if response.get("error"):
            _LOG.warning("[eris-goal] Goal đã adopt nhưng chưa thể chạy: %s", response)
        _LOG.info("[eris-goal] Goal=%s Work Session=%s status=%s", goal_id,
                  response.get("job_id", ""), response.get("status", response.get("error", "")))
        return GoalDecisionReceipt(goal_id, decision.decision_id, intention_id,
                                   candidate.title, str(executive["commit_id"]), str(response.get("job_id", "")))


    def _next_review(self) -> tuple[str, str] | None:
        """Chỉ đánh giá kết quả đã được Subject nhận; không suy diễn job complete là đạt."""
        for goal in self.ledger.list_goals(status="active"):
            outcome = str((goal.payload.get("last_outcome") or {}).get("outcome_ref") or "")
            if not outcome:
                continue
            with self.ledger._db() as db:
                learned = db.execute(
                    "SELECT status FROM autonomy_subject_feedback WHERE outcome_id=?", (outcome,)
                ).fetchone()
                previous = db.execute(
                    "SELECT 1 FROM autonomy_goal_reviews WHERE outcome_id=?", (outcome,)
                ).fetchone()
            if learned and learned[0] == "learned" and not previous:
                return (goal.goal_id, outcome)
        return None

    def _review_outcome(self, goal_id: str, outcome_ref: str) -> dict:
        """Subject/Executive tự chọn tiếp tục, tạm dừng hoặc kết thúc goal."""
        goal = self.ledger.get(goal_id)
        if not goal or goal.status != "active":
            return {"status": "goal_changed"}
        last = dict(goal.payload.get("last_outcome") or {})
        if str(last.get("outcome_ref")) != outcome_ref:
            return {"status": "outcome_stale"}
        job_id = str(last.get("action_id") or "")
        job = self.sessions.snapshot(job_id) if job_id else None
        evidence = str((job or {}).get("last_evidence") or "")
        status = str(last.get("outcome_status") or "")
        # Chỉ được đề xuất đạt mục tiêu nếu có bằng chứng từ công cụ, không
        # phải chỉ do worker viết chữ `complete`.
        # Nếu đã tới ngân sách work, để Subject quyết định tạm dừng;
        # không được tự khởi chạy thêm vô hạn khi worker lặp `complete`.
        maximum = max(1, min(50, int(os.getenv("ERIS_GOAL_MAX_JOBS", "6"))))
        link = getattr(self.sessions, "_goal_bridge", None)
        used = link.job_count(goal_id) if link is not None else 1
        choices = (["suspend_goal"] if used >= maximum else ["continue_goal", "suspend_goal"])
        if status == "completed" and evidence:
            choices.append("achieve_goal")
        raw = []
        for action in choices:
            raw.append({
                "proposal_id": f"review-proposal:{outcome_ref}:{action}",
                "action_kind": action,
                "arguments": {"goal_id": goal_id, "outcome_ref": outcome_ref},
                "supporting_need_ids": ("competence", "coherence"),
                "expected_need_relief": 0.76 if action == "achieve_goal" else 0.67 if action == "continue_goal" else 0.25,
                "expected_action_efficacy": 0.80 if action == "achieve_goal" else 0.70,
                "expected_epistemic_value": 0.58 if action == "continue_goal" else 0.30,
                "value_congruence": 0.75 if action != "suspend_goal" else 0.38,
                "substrate_cost": 0.14 if action != "continue_goal" else 0.23,
                "uncertainty": 0.18 if action == "achieve_goal" else 0.35,
                "evidence_refs": (outcome_ref,),
            })
        now = time.time()
        observation = WorldObservation(
            observation_id="goal-review:" + outcome_ref,
            subject_id=self.subject.config.subject_id, kind="autonomous_goal_outcome",
            created_at=now, origin=ObservationOrigin.INTERNAL,
            context={"context_key": "autonomous_goal_review", "affected_need_ids": ("competence", "coherence"),
                     "goal_relevance": .90, "controllability": .80, "uncertainty": .25,
                     "primary_interpretation": "Có kết quả công việc; Subject cần đánh giá mục tiêu tiếp theo",
                     "action_proposals": tuple(raw)},
            evidence_refs=(outcome_ref,), correlation_id=goal_id,
        )
        decision = self.mind_loop.deliberate_sync(observation, mode="autonomous_goal_review")
        act = str(decision.proposed_action)
        executive = dict(decision.arguments.get("executive_commit") or {})
        outcome = {"status": "wait", "goal_id": goal_id}
        if (act in choices and str(decision.arguments.get("goal_id")) == goal_id
            and executive.get("selected_proposal_ref") == f"review-proposal:{outcome_ref}:{act}"):
            proof = SemanticCommitProof(
                commit_id=str(executive["commit_id"]), source=str(executive["source"]),
                identity_revision=int(self.subject.initiative_state.revision),
                motive_ids=tuple(goal.payload.get("candidate", {}).get("motive_ids") or ("competence",)),
                evidence_refs=(outcome_ref,), episode_id="goal-review:" + goal_id,
            )
            current = self.ledger.get(goal_id)
            if current and current.status == "active" and str((current.payload.get("last_outcome") or {}).get("outcome_ref")) == outcome_ref:
                if act == "continue_goal":
                    outcome = self.sessions.execute({"operation": "continue_goal", "goal_id": goal_id})
                elif act == "suspend_goal":
                    self.ledger.transition(goal_id, expected_revision=current.revision, status="suspended",
                                           event_id="goal-review:" + decision.decision_id,
                                           proof=proof, evidence_ref=outcome_ref)
                    outcome = {"status": "suspended", "goal_id": goal_id}
                elif act == "achieve_goal" and evidence:
                    self.ledger.transition(goal_id, expected_revision=current.revision, status="achieved",
                                           event_id="goal-review:" + decision.decision_id,
                                           proof=proof, evidence_ref=outcome_ref)
                    outcome = {"status": "achieved", "goal_id": goal_id}
        _LOG.info("[eris-goal] Đánh giá goal=%s action=%s outcome=%s", goal_id, act, outcome.get("status"))
        with self.ledger._db() as db:
            db.execute("INSERT OR IGNORE INTO autonomy_goal_reviews VALUES(?,?,?,?,?)",
                       (outcome_ref, goal_id, str(outcome.get("status") or "wait"), decision.decision_id, time.time()))
        return outcome

    def _drain_feedback(self) -> None:
        """Work outcome → Subject learning. Tránh replay khi restart.

        Chỉ chuyển outcome khi có `subject-decision` thuộc Subject hiện tại.
        Sau crash giữa reserve và ghi Subject, giữ needs_review thay vì lặp
        learning hai lần. Goal Ledger vẫn giữ receipt để kiểm tra thủ công.
        """
        from runtime.mind_loop import MindLoop
        for goal in self.ledger.list_goals(status="active"):
            last = dict(goal.payload.get("last_outcome") or {})
            outcome_ref = str(last.get("outcome_ref", ""))
            if not outcome_ref:
                continue
            evidence = tuple(goal.payload.get("candidate", {}).get("evidence_refs") or ())
            decisions = [r.split(":", 1)[1] for r in evidence if str(r).startswith("subject-decision:")]
            if not decisions:
                continue
            with self.ledger._db() as db:
                existed = db.execute("SELECT status FROM autonomy_subject_feedback WHERE outcome_id=?", (outcome_ref,)).fetchone()
                if existed:
                    continue
                db.execute("INSERT INTO autonomy_subject_feedback VALUES(?,?,?,?,?)",
                           (outcome_ref, goal.goal_id, "needs_review", decisions[0], time.time()))
            state = str(last.get("outcome_status", ""))
            # Work job completed != goal achieved. Thành công công việc mới
            # là bằng chứng một bước; Subject cần tự đánh giá goal dài hạn.
            status = OutcomeStatus.PARTIAL if state == "completed" else OutcomeStatus.FAILED
            outcome = WorldOutcome(
                outcome_id=outcome_ref, action_id=str(last.get("action_id") or outcome_ref),
                decision_id=decisions[0], status=status,
                observed_changes=("work_session:" + state,), unintended_changes=(),
                denial_reason=None, evidence_refs=(outcome_ref,),
                started_at=time.time(), completed_at=time.time(),
            )
            try:
                MindLoop.run_sync(self.subject.observe_outcome(outcome))
            except (KeyError, ValueError, RuntimeError) as exc:
                _LOG.warning("[eris-goal] Outcome cần đối chiếu với Subject: %s", exc)
                continue
            _LOG.info("[eris-goal] Subject tiếp nhận kết quả goal=%s outcome=%s", goal.goal_id, outcome_ref)
            with self.ledger._db() as db:
                db.execute("UPDATE autonomy_subject_feedback SET status='learned',updated_at=? WHERE outcome_id=?",
                           (time.time(), outcome_ref))

# -*- coding: utf-8 -*-
"""Parallel deep-cognition continuation lane for direct chat.

The public chat path must never wait for remote Neuro deliberation.  A direct
user turn is committed locally first.  If the same frozen frame has unresolved action ambiguity, this runtime may ask
the bounded Neuro worker for an advisory second opinion and later expose only a
materially useful result as a normal typed affordance.

Important invariants:
- the advisory result never rewrites/finalizes the already committed turn;
- a new user turn cancels older work and invalidates pending callbacks;
- only materially new, still-current advice becomes an affordance;
- once such advice becomes a due packet, it is a self-owned expression
  commitment. Neuro may rank its realization, but may not reinterpret ordinary
  user silence as permission to revoke it.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from cortex.subject.contracts import SubjectDeliberationFrame, SubjectExecutiveCommit
from cortex.subject.neuro_control import NeuroDeliberationAdvice, SelectionResult
from cortex.subject.expression_commitment import commitment_payload
from runtime.ai_orchestrated.affordance import ExecutiveAffordance
from runtime.ai_orchestrated.social_candidate import SocialSpeechCandidate


def _compact(value: Any, limit: int = 1000) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


@dataclass(frozen=True, slots=True)
class NeuroContinuationJob:
    job_id: str
    origin_turn_id: str
    conversation_epoch: int
    cancel_scope: str
    frame: SubjectDeliberationFrame
    selection: SelectionResult
    baseline_commit: SubjectExecutiveCommit
    user_text: str
    snapshot: Mapping[str, Any]
    created_at: float


@dataclass(frozen=True, slots=True)
class NeuroContinuationPacket:
    packet_id: str
    origin_turn_id: str
    conversation_epoch: int
    revision: int
    semantic_target: str
    confidence: float
    urgency: float
    material_delta: float
    reason: str
    created_at: float
    expires_at: float


class NeuroContinuationRuntime:
    """Owns cancellable parallel Neuro jobs and one bounded continuation queue."""

    def __init__(
        self,
        *,
        committer_provider: Callable[[], Any | None],
        wake_callback: Callable[..., Any] | None = None,
        logger: Any | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._committer_provider = committer_provider
        self._wake_callback = wake_callback
        self._logger = logger
        self._clock = clock
        self._lock = threading.RLock()
        self._conversation_epoch = 0
        self._current_turn_id = ""
        self._jobs: dict[str, NeuroContinuationJob] = {}
        self._started_job_ids: set[str] = set()
        self._pending: NeuroContinuationPacket | None = None
        self._revision = 0

    def _log(self, level: str, message: str, *args: Any) -> None:
        logger = self._logger
        fn = getattr(logger, level, None) if logger is not None else None
        if callable(fn):
            try:
                fn(message, *args)
            except Exception:
                pass

    @property
    def conversation_epoch(self) -> int:
        with self._lock:
            return self._conversation_epoch

    def begin_user_turn(self, turn_id: str) -> int:
        """Advance the chat epoch and cooperatively cancel every older Neuro job."""
        actual_turn = str(turn_id or "").strip()
        with self._lock:
            self._conversation_epoch += 1
            epoch = self._conversation_epoch
            self._current_turn_id = actual_turn
            scopes = tuple(job.cancel_scope for job in self._jobs.values())
            self._jobs.clear()
            self._started_job_ids.clear()
            self._pending = None
            self._revision += 1

        if scopes:
            try:
                from runtime.inference_broker import GLOBAL_INFERENCE_BROKER

                for scope in scopes:
                    GLOBAL_INFERENCE_BROKER.cancel_scope(scope)
            except Exception as exc:
                self._log("debug", "[neuro-continuation] cancel old scope failed: %s", exc)
        return epoch

    def cancel_turn(self, turn_id: str) -> bool:
        """Cancel queued/running advisory work for the current public turn.

        Unlike ``begin_user_turn`` this does not advance the conversation epoch;
        it is used when the public writer failed to produce a reply, so there is
        nothing valid for a later "continuation" to continue.
        """
        actual_turn = str(turn_id or "").strip()
        with self._lock:
            if actual_turn != self._current_turn_id:
                return False
            doomed = tuple(
                job for job in self._jobs.values()
                if job.origin_turn_id == actual_turn
                and job.conversation_epoch == self._conversation_epoch
            )
            for job in doomed:
                self._jobs.pop(job.job_id, None)
                self._started_job_ids.discard(job.job_id)
            had_pending = bool(
                self._pending is not None and self._pending.origin_turn_id == actual_turn
            )
            if had_pending:
                self._pending = None
            scopes = tuple(job.cancel_scope for job in doomed)
            self._revision += 1
        if scopes:
            try:
                from runtime.inference_broker import GLOBAL_INFERENCE_BROKER

                for scope in scopes:
                    GLOBAL_INFERENCE_BROKER.cancel_scope(scope)
            except Exception as exc:
                self._log("debug", "[neuro-continuation] cancel turn scope failed: %s", exc)
        return bool(doomed or had_pending)

    def public_state(self, turn_id: str = "") -> dict[str, Any]:
        actual_turn = str(turn_id or "").strip()
        with self._lock:
            active = tuple(self._jobs.values())
            running = any(
                (not actual_turn or job.origin_turn_id == actual_turn)
                and job.conversation_epoch == self._conversation_epoch
                for job in active
            )
            return {
                "deep_thought_running": bool(running),
                "may_follow_up": bool(running),
                "conversation_epoch": self._conversation_epoch,
                "policy": "parallel_nonblocking",
            }

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        current = float(now if now is not None else self._clock())
        with self._lock:
            packet = self._pending
            due = bool(
                packet is not None
                and packet.conversation_epoch == self._conversation_epoch
                and current < packet.expires_at
            )
            active = any(
                job.conversation_epoch == self._conversation_epoch
                for job in self._jobs.values()
            )
            return {
                "pending": bool(packet is not None),
                "due": due,
                "deep_thought_running": active,
                "may_follow_up": active or due,
                "conversation_epoch": self._conversation_epoch,
                "origin_turn_id": packet.origin_turn_id if packet is not None else self._current_turn_id,
                "material_delta": packet.material_delta if packet is not None else 0.0,
                "policy": "parallel_nonblocking",
            }

    def schedule(
        self,
        *,
        frame: SubjectDeliberationFrame,
        selection: SelectionResult,
        baseline_commit: SubjectExecutiveCommit,
        origin_turn_id: str,
        user_text: str,
        snapshot: Mapping[str, Any] | None = None,
    ) -> bool:
        """Queue an advisory review without delaying the public decision.

        ``parallel_review_recommended`` is intentionally broader than blocking
        deliberation: a user-origin choice may deserve a second look merely
        because competing semantic actions are genuinely ambiguous. Since this
        lane is released only after the public writer, ambiguity no longer taxes
        human-facing latency.
        """
        if not selection.parallel_review_recommended:
            return False
        reviewer = self._committer_provider()
        review = getattr(reviewer, "review_subject_frame", None)
        if not callable(review):
            return False

        with self._lock:
            epoch = self._conversation_epoch
            if str(origin_turn_id or "") != self._current_turn_id:
                return False
            job_id = f"neuro-job:{uuid.uuid4()}"
            job = NeuroContinuationJob(
                job_id=job_id,
                origin_turn_id=str(origin_turn_id or ""),
                conversation_epoch=epoch,
                cancel_scope=f"neuro-continuation:{epoch}:{job_id}",
                frame=frame,
                selection=selection,
                baseline_commit=baseline_commit,
                user_text=str(user_text or ""),
                snapshot=dict(snapshot or {}),
                created_at=float(self._clock()),
            )
            self._jobs[job_id] = job

        # Queue only. The public writer releases the job after its own remote
        # generation has completed, so slow Neuro can never grab a scarce provider
        # slot before the reply that the human is actually waiting for.
        self._log(
            "info",
            "[neuro-continuation] planned turn=%s epoch=%s reason=%s",
            job.origin_turn_id,
            job.conversation_epoch,
            selection.reason,
        )
        return True

    def release(self, turn_id: str) -> int:
        """Start queued jobs for the current turn after public reply generation."""
        actual_turn = str(turn_id or "").strip()
        with self._lock:
            jobs = [
                job for job in self._jobs.values()
                if job.origin_turn_id == actual_turn
                and job.conversation_epoch == self._conversation_epoch
                and job.job_id not in self._started_job_ids
            ]
            for job in jobs:
                self._started_job_ids.add(job.job_id)
        for job in jobs:
            thread = threading.Thread(
                target=self._run_job,
                args=(job,),
                name=f"eris-neuro-continuation-{job.conversation_epoch}",
                daemon=True,
            )
            thread.start()
            self._log(
                "info",
                "[neuro-continuation] started turn=%s epoch=%s",
                job.origin_turn_id,
                job.conversation_epoch,
            )
        return len(jobs)

    def _run_job(self, job: NeuroContinuationJob) -> None:
        try:
            reviewer = self._committer_provider()
            review = getattr(reviewer, "review_subject_frame", None)
            if not callable(review):
                return
            from runtime.inference_broker import inference_context

            with inference_context(
                "neuro_continuation",
                optional=True,
                priority=40,
                timeout_seconds=0.0,
                cancel_scope=job.cancel_scope,
                metadata={
                    "origin_turn_id": job.origin_turn_id,
                    "conversation_epoch": job.conversation_epoch,
                    "parallel_cognition": True,
                },
            ):
                advice = review(
                    job.frame,
                    baseline_commit=job.baseline_commit,
                    user_text=job.user_text,
                    snapshot=job.snapshot,
                    mode="interactive_continuation",
                )
            if not isinstance(advice, NeuroDeliberationAdvice):
                return
            self._accept_advice(job, advice)
        except Exception as exc:
            self._log("debug", "[neuro-continuation] review failed turn=%s: %s", job.origin_turn_id, exc)
        finally:
            with self._lock:
                self._jobs.pop(job.job_id, None)
                self._started_job_ids.discard(job.job_id)

    def _accept_advice(self, job: NeuroContinuationJob, advice: NeuroDeliberationAdvice) -> None:
        with self._lock:
            if (
                job.conversation_epoch != self._conversation_epoch
                or job.origin_turn_id != self._current_turn_id
                or job.job_id not in self._jobs
            ):
                self._log(
                    "info",
                    "[neuro-continuation] dropped stale turn=%s epoch=%s current=%s",
                    job.origin_turn_id,
                    job.conversation_epoch,
                    self._conversation_epoch,
                )
                return

            changed_choice = (
                advice.commit.selected_coalition_id
                != job.baseline_commit.selected_coalition_id
            )
            material_delta = _clamp01(advice.material_delta)
            confidence = _clamp01(advice.commit.confidence)
            should_follow = bool(advice.should_follow_up)
            brief = _compact(advice.followup_brief, 1100)

            # Runtime has the last word on interruption. A worker cannot create a
            # callback merely because it can paraphrase the same conclusion.
            material = bool(
                brief
                and should_follow
                and confidence >= 0.62
                and (changed_choice or material_delta >= 0.58)
            )
            if not material:
                self._log(
                    "info",
                    "[neuro-continuation] no material callback turn=%s changed=%s delta=%.2f conf=%.2f",
                    job.origin_turn_id,
                    int(changed_choice),
                    material_delta,
                    confidence,
                )
                return

            self._revision += 1
            now = float(self._clock())
            ttl = max(15.0, min(180.0, float(os.getenv("ERIS_NEURO_CONTINUATION_TTL_S", "75") or 75.0)))
            semantic_target = _compact(
                "Continue the still-current user turn with one genuinely new insight from deeper review. "
                f"Deep-review brief: {brief} "
                "Do not mention Neuro, background workers, waiting, scores, or implementation details. "
                "If this changes the earlier provisional answer, correct it clearly and naturally; otherwise add only the new consequential point.",
                1500,
            )
            self._pending = NeuroContinuationPacket(
                packet_id=f"neuro-continuation:{uuid.uuid4()}",
                origin_turn_id=job.origin_turn_id,
                conversation_epoch=job.conversation_epoch,
                revision=self._revision,
                semantic_target=semantic_target,
                confidence=confidence,
                urgency=0.98 if changed_choice else max(0.72, material_delta),
                material_delta=material_delta,
                reason=_compact(advice.commit.reason, 360),
                created_at=now,
                expires_at=now + ttl,
            )

        self._log(
            "info",
            "[neuro-continuation] ready turn=%s delta=%.2f changed_choice=%s",
            job.origin_turn_id,
            material_delta,
            int(changed_choice),
        )
        wake = self._wake_callback
        if callable(wake):
            try:
                wake(reason="neuro_continuation_ready")
            except TypeError:
                wake("neuro_continuation_ready")
            except Exception as exc:
                self._log("debug", "[neuro-continuation] wake failed: %s", exc)

    def is_due(self, *, now: float | None = None, interaction_busy: bool = False) -> bool:
        current = float(now if now is not None else self._clock())
        with self._lock:
            packet = self._pending
            if packet is None:
                return False
            if current >= packet.expires_at:
                self._pending = None
                return False
            if packet.conversation_epoch != self._conversation_epoch:
                self._pending = None
                return False
            return not interaction_busy

    def peek_due(
        self,
        *,
        now: float | None = None,
        interaction_busy: bool = False,
    ) -> ExecutiveAffordance | None:
        current = float(now if now is not None else self._clock())
        with self._lock:
            if not self.is_due(now=current, interaction_busy=interaction_busy):
                return None
            packet = self._pending
            if packet is None:
                return None
            commitment = commitment_payload(
                origin="neuro_continuation",
                source_id=packet.packet_id,
                semantic_target=packet.semantic_target,
                motive="deep_thought_followup",
                pressure=max(packet.urgency, packet.material_delta),
                freshness=packet.confidence,
                persistence=packet.material_delta,
            )
            candidate = SocialSpeechCandidate(
                decision="candidate",
                kind="continuation",
                topic=packet.semantic_target,
                speech_act="deep_thought_followup",
                anchor_type="conversation",
                anchor_id=packet.origin_turn_id,
                evidence_refs=(packet.origin_turn_id, packet.packet_id),
                requires_grounding=False,
                confidence=packet.confidence,
                relevance=max(0.86, packet.material_delta),
                urgency=packet.urgency,
                tone="focused",
                reason=f"neuro_material_continuation:{packet.reason}",
                source="neuro_continuation_runtime",
                continuation_lane="neuro_continuation",
                idempotency_key=packet.packet_id,
                uncertainty=max(0.0, 1.0 - packet.confidence),
                seed_intrusion_risk=0.06,
                expression_commitment=commitment,
            )
            return ExecutiveAffordance(
                affordance_id=f"neuro_continuation:{packet.packet_id}:v{packet.revision}",
                owner="neuro_continuation",
                revision=packet.revision,
                candidate=candidate,
                claim_required=True,
                expires_at=packet.expires_at,
                claim_key=packet.packet_id,
                metadata={
                    "origin_turn_id": packet.origin_turn_id,
                    "conversation_epoch": packet.conversation_epoch,
                    "material_delta": packet.material_delta,
                },
            )

    def claim_affordance(
        self,
        affordance: ExecutiveAffordance,
        *,
        worker_id: str,
        now: float | None = None,
    ) -> SocialSpeechCandidate | None:
        del worker_id
        current = float(now if now is not None else self._clock())
        with self._lock:
            packet = self._pending
            if packet is None:
                return None
            if (
                packet.packet_id != str(getattr(affordance, "claim_key", "") or "")
                or packet.revision != int(getattr(affordance, "revision", -1) or -1)
                or packet.conversation_epoch != self._conversation_epoch
                or current >= packet.expires_at
            ):
                return None
            preview = self.peek_due(now=current, interaction_busy=False)
            if preview is None:
                return None
            candidate = preview.candidate
            self._pending = None
            self._revision += 1
            return candidate


__all__ = [
    "NeuroContinuationJob",
    "NeuroContinuationPacket",
    "NeuroContinuationRuntime",
]

# -*- coding: utf-8 -*-
"""Một vòng deliberation duy nhất cho chat, autonomous và runtime action.

MindLoop là coordinator, không phải một bộ não mới. Subject Core giữ state và luật
nhân quả; Subject Commit Port đóng quyết định từ frame hiện tại; cognitive adapter
thực hiện yêu cầu xem lại. MindLoop chỉ bảo đảm thứ tự, ngân sách và điều kiện dừng.
Không có một ``Executive state machine`` thứ hai nằm cạnh Subject Core.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Callable, Coroutine, Mapping, Protocol

from cortex.subject.contracts import (
    MindDecision,
    SubjectDeliberationFrame,
    SubjectExecutiveCommit,
    WorldObservation,
)
from cortex.subject.deliberation import CognitiveRequestKind
from cortex.subject.service import SubjectService
from cortex.subject.neuro_control import NeuroActionSelector, SelectionResult, commit_selection
from runtime.subject_cognition import BoundedSubjectCognition, SubjectCognitionPort


class DeliberationState(StrEnum):
    PREPARED = "prepared"
    REVISED = "revised"
    COMMITTED = "committed"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class FastMindResult:
    """Immediate deterministic result for availability-first direct chat."""

    decision: MindDecision
    commit: SubjectExecutiveCommit
    selection: SelectionResult


@dataclass(frozen=True, slots=True)
class DeliberationSession:
    session_id: str
    frame: SubjectDeliberationFrame
    state: DeliberationState
    mode: str
    user_text: str
    snapshot: Mapping[str, Any]
    review_count: int = 0
    max_reviews: int = 1

    @property
    def can_review(self) -> bool:
        return self.state in {DeliberationState.PREPARED, DeliberationState.REVISED} and self.review_count < self.max_reviews


class SubjectCommitPort(Protocol):
    @property
    def enabled(self) -> bool: ...

    def commit_subject_frame(
        self,
        frame: SubjectDeliberationFrame,
        *,
        user_text: str = "",
        snapshot: dict[str, Any] | None = None,
        mode: str = "runtime",
        allow_remote_deliberation: bool = True,
    ) -> SubjectExecutiveCommit | None: ...


class LocalNeuroCommitter:
    """Committer local dùng *cùng* NeuroActionSelector với đường có model.

    Provider mất kết nối không được phép đổi policy. Trường hợp đó chỉ mất khả năng
    deliberation đắt tiền; competition hiện tại vẫn chọn winner bằng cùng một gate.
    """

    enabled = True

    def __init__(
        self,
        service: SubjectService,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._service = service
        self._clock = clock
        self._selector = NeuroActionSelector()

    def select(self, frame: SubjectDeliberationFrame) -> SelectionResult:
        return self._selector.select(frame)

    def commit_subject_frame(
        self,
        frame: SubjectDeliberationFrame,
        *,
        user_text: str = "",
        snapshot: dict[str, Any] | None = None,
        mode: str = "runtime",
        allow_remote_deliberation: bool = True,
    ) -> SubjectExecutiveCommit:
        selection = self._selector.select(frame)
        return commit_selection(
            frame,
            selection,
            reason=selection.reason,
            source="neuro_fast_gate",
            clock=self._clock,
            review_conditions=("remote_deliberator_available",) if selection.requires_deliberation else (),
        )


class MindLoop:
    """Coordinator duy nhất của một phiên deliberation."""

    def __init__(
        self,
        *,
        subject: SubjectService,
        committer_provider: Callable[[], SubjectCommitPort | None] | None = None,
        cognition: SubjectCognitionPort | None = None,
        local_committer: SubjectCommitPort | None = None,
        max_reviews: int = 1,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.subject = subject
        self._committer_provider = committer_provider or (lambda: None)
        self._cognition = cognition or BoundedSubjectCognition()
        self._local_committer = local_committer or LocalNeuroCommitter(subject)
        self._max_reviews = max(0, int(max_reviews))
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def _commit_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """Bổ sung self-model dẫn xuất cho commit port mà không sửa frame causal.

        Self-model là view của cùng Subject Core, không phải evidence mới và không
        được phép tạo coalition/action ngoài frame.
        """
        payload = dict(snapshot or {})
        self_view = getattr(self.subject, "self_view", None)
        if callable(self_view):
            try:
                candidate = self_view()
                if isinstance(candidate, Mapping):
                    payload["subject_self"] = dict(candidate)
            except Exception:
                pass
        return payload

    def _committer(self, override: SubjectCommitPort | None = None) -> SubjectCommitPort:
        candidate = override or self._committer_provider()
        if candidate is not None and bool(getattr(candidate, "enabled", True)):
            return candidate
        return self._local_committer

    async def prepare(
        self,
        observation: WorldObservation,
        *,
        mode: str,
        user_text: str = "",
        snapshot: Mapping[str, Any] | None = None,
    ) -> DeliberationSession:
        frame = await self.subject.prepare_deliberation(observation)
        return DeliberationSession(
            session_id=f"mind-loop:{self._id_factory()}",
            frame=frame,
            state=DeliberationState.PREPARED,
            mode=str(mode or "runtime"),
            user_text=str(user_text or ""),
            snapshot=dict(snapshot or {}),
            max_reviews=self._max_reviews,
        )

    async def deliberate(
        self,
        observation: WorldObservation,
        *,
        mode: str,
        user_text: str = "",
        snapshot: Mapping[str, Any] | None = None,
        committer: SubjectCommitPort | None = None,
    ) -> MindDecision:
        session = await self.prepare(
            observation,
            mode=mode,
            user_text=user_text,
            snapshot=snapshot,
        )
        return await self.complete(session, committer=committer)

    async def complete_fast(self, session: DeliberationSession) -> FastMindResult:
        """Finalize one frame from the deterministic Neuro gate only.

        This is the direct-chat path. It intentionally bypasses the remote
        committer provider so public latency cannot inherit slow deliberation.
        The returned frozen frame/selection may still be reviewed advisory-only
        by a parallel continuation runtime after this commit.
        """
        if session.state not in {DeliberationState.PREPARED, DeliberationState.REVISED}:
            raise ValueError(f"cannot fast-complete deliberation from state {session.state}")
        local = self._local_committer
        selector = getattr(local, "select", None)
        selection = selector(session.frame) if callable(selector) else NeuroActionSelector().select(session.frame)
        commit = local.commit_subject_frame(
            session.frame,
            user_text=session.user_text,
            snapshot=dict(session.snapshot),
            mode=session.mode,
            allow_remote_deliberation=False,
        )
        if selection.requires_deliberation and "parallel_neuro_review" not in commit.review_conditions:
            commit = replace(commit, review_conditions=(*commit.review_conditions, "parallel_neuro_review"))
        decision = await self.subject.finalize_deliberation(session.frame, commit)
        return FastMindResult(decision=decision, commit=commit, selection=selection)

    @staticmethod
    def _review_locked_commit(
        frame: SubjectDeliberationFrame,
        prior: SubjectExecutiveCommit,
    ) -> SubjectExecutiveCommit | None:
        """Re-bind a reviewed frame to the semantic winner already committed.

        Cognitive review is allowed to revise evidence/cost/uncertainty, but it is
        not a second action election.  The proposal identity chosen by the semantic
        commit remains authoritative unless review structurally invalidates it
        (proposal/coalition disappears or conflict becomes effectively hard).
        """
        # V95: review lock chỉ được bảo toàn semantic identity nếu motive vẫn
        # hợp lệ theo lifecycle hiện tại. Cognitive review không được hồi sinh một
        # ``resume_shared_goal`` vừa bị goal lifecycle suspend/expire.
        try:
            from runtime.semantic_authority_v95 import validate_autonomous_motives
            validated = validate_autonomous_motives(frame)
            if validated.changed:
                valid_coalition_ids = {item.coalition_id for item in validated.frame.coalitions}
                if prior.selected_coalition_id not in valid_coalition_ids:
                    return None
        except Exception:
            # Validator là safety/authority refinement; adapter thiếu typed context
            # không được làm MindLoop crash. Executive sẽ kiểm lại trước commit.
            pass

        proposal = next(
            (item for item in frame.proposals if item.proposal_id == prior.selected_proposal_ref),
            None,
        )
        if proposal is None:
            return None
        # A review may discover a real incompatibility.  This is the narrow escape
        # hatch from the lock; ordinary score drift must not change the winner.
        if float(getattr(proposal, "commitment_conflict", 0.0) or 0.0) >= 0.95:
            return None
        coalition = next(
            (
                item
                for item in frame.coalitions
                if item.primary_proposal_ref == prior.selected_proposal_ref
            ),
            None,
        )
        if coalition is None:
            return None
        return replace(
            prior,
            commit_id=f"review-lock:{uuid.uuid4()}",
            frame_id=frame.frame_id,
            selected_coalition_id=coalition.coalition_id,
            selected_proposal_ref=prior.selected_proposal_ref,
            cognitive_requests=(),
            reason=f"review_lock_preserved:{prior.reason}"[:500],
            review_conditions=tuple(dict.fromkeys((*prior.review_conditions, "semantic_winner_locked"))),
            created_at=time.time(),
            source=f"{prior.source}:review_lock",
        )

    async def complete(
        self,
        session: DeliberationSession,
        *,
        committer: SubjectCommitPort | None = None,
        initial_commit: SubjectExecutiveCommit | None = None,
        commit_provider: Callable[[SubjectDeliberationFrame, str], SubjectExecutiveCommit | None] | None = None,
    ) -> MindDecision:
        if session.state not in {DeliberationState.PREPARED, DeliberationState.REVISED}:
            raise ValueError(f"cannot complete deliberation from state {session.state}")
        controller = self._committer(committer)
        current = session
        commit = initial_commit

        while True:
            if commit is None:
                step_mode = current.mode if current.review_count == 0 else f"{current.mode}_review"
                if commit_provider is not None:
                    commit = commit_provider(current.frame, step_mode)
                else:
                    commit = controller.commit_subject_frame(
                        current.frame,
                        user_text=current.user_text,
                        snapshot=self._commit_snapshot(current.snapshot),
                        mode=step_mode,
                        # A cognitive review may revise evidence and scores, but it
                        # must not open a second remote deliberation inside the same
                        # MindLoop session. Re-score the revised frame locally.
                        allow_remote_deliberation=(current.review_count == 0),
                    )
            if commit is None:
                await self.abandon(current, reason="subject_committer_returned_no_commit")
                raise RuntimeError("Subject commit port did not commit a prepared deliberation frame")

            requests = CognitiveRequestKind.normalize_many(commit.cognitive_requests)
            if requests and current.can_review:
                # The first semantic commit owns action identity. Review may revise
                # the selected proposal, but must not silently run a second election
                # and swap in another stale urge merely because local scores moved.
                semantic_commit = commit
                findings = self._cognition.execute(current.frame, requests)
                revised = await self.subject.integrate_cognitive_results(current.frame, findings)
                current = replace(
                    current,
                    frame=revised,
                    state=DeliberationState.REVISED,
                    review_count=current.review_count + 1,
                )
                locked = self._review_locked_commit(revised, semantic_commit)
                if locked is not None:
                    commit = locked
                    continue
                # Structural invalidation is the only case where review is allowed
                # to reopen competition.  The reviewed frame is then scored locally
                # (never another remote semantic election).
                commit = None
                continue

            decision = await self.subject.finalize_deliberation(current.frame, commit)
            return decision

    async def abandon(self, session: DeliberationSession, *, reason: str) -> None:
        if session.state in {DeliberationState.COMMITTED, DeliberationState.ABANDONED}:
            return
        await self.subject.abandon_deliberation(session.frame, reason=reason)

    @staticmethod
    def run_sync(coro: Coroutine[Any, Any, Any]) -> Any:
        """Chạy coroutine từ runtime đồng bộ, kể cả khi caller đang có event loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        result: list[Any] = []
        error: list[BaseException] = []

        def worker() -> None:
            try:
                result.append(asyncio.run(coro))
            except BaseException as exc:  # chuyển nguyên lỗi về caller thread
                error.append(exc)

        thread = threading.Thread(target=worker, name="eris-mind-loop", daemon=True)
        thread.start()
        thread.join()
        if error:
            raise error[0]
        return result[0]

    def prepare_sync(self, *args: Any, **kwargs: Any) -> DeliberationSession:
        return self.run_sync(self.prepare(*args, **kwargs))

    def deliberate_sync(self, *args: Any, **kwargs: Any) -> MindDecision:
        return self.run_sync(self.deliberate(*args, **kwargs))

    def complete_fast_sync(self, *args: Any, **kwargs: Any) -> FastMindResult:
        return self.run_sync(self.complete_fast(*args, **kwargs))

    def complete_sync(self, *args: Any, **kwargs: Any) -> MindDecision:
        return self.run_sync(self.complete(*args, **kwargs))

    def abandon_sync(self, *args: Any, **kwargs: Any) -> None:
        self.run_sync(self.abandon(*args, **kwargs))


__all__ = [
    "DeliberationSession",
    "DeliberationState",
    "FastMindResult",
    "LocalNeuroCommitter",
    "MindLoop",
    "SubjectCommitPort",
]

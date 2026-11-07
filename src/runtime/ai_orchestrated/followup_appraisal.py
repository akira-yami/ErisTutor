# -*- coding: utf-8 -*-
"""Event-derived follow-up appraisal.

The old implementation kept a second little state machine beside the public
turn ledger (question_started_at, reminder_spoken_at, abandoned_at, ...).  That
made correctness depend on remembering to reset those flags on every ingress.

V29 treats the public session ledger as the source of truth.  This module is a
pure projection:

    SessionTurnLedger + observer DiscourseState + now -> DiscourseState

No call to :meth:`evaluate` mutates lifecycle state.  A reminder exists because
a public assistant turn with ``speech_act=followup_reminder`` exists.  A user
answered because a later user turn exists.  Boring, deterministic bookkeeping is
precisely what language models should not be asked to improvise.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from core.di import resolve_dependency
from runtime.semantic_frame import validate_assistant_act
from runtime.session_turn_ledger import SessionTurn, SessionTurnLedger

from .discourse_state import DiscourseState


def _env_seconds(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True, slots=True)
class FollowUpPolicy:
    remind_after_seconds: float = 30.0
    abandon_after_reminder_seconds: float = 48.0
    abandon_without_reminder_seconds: float = 105.0
    reserved_distance: float = 0.20

    @classmethod
    def from_environment(cls) -> "FollowUpPolicy":
        return cls(
            remind_after_seconds=_env_seconds(
                "ERIS_FOLLOWUP_REMIND_AFTER_SEC", 30.0, minimum=12.0, maximum=180.0
            ),
            abandon_after_reminder_seconds=_env_seconds(
                "ERIS_FOLLOWUP_ABANDON_AFTER_REMINDER_SEC", 48.0, minimum=20.0, maximum=300.0
            ),
            abandon_without_reminder_seconds=_env_seconds(
                "ERIS_FOLLOWUP_ABANDON_WITHOUT_REMINDER_SEC", 105.0, minimum=45.0, maximum=600.0
            ),
            reserved_distance=max(
                0.0,
                min(
                    0.35,
                    _env_seconds(
                        "ERIS_FOLLOWUP_RESERVED_DISTANCE", 0.20, minimum=0.0, maximum=0.35
                    ),
                ),
            ),
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _speech_act(turn: SessionTurn) -> str:
    """Read typed public semantics without inspecting surface wording."""

    meta = _mapping(turn.meta)
    direct = str(meta.get("speech_act", "") or "").strip().lower()
    if direct:
        return direct
    plan = _mapping(meta.get("autonomous_plan"))
    semantics = _mapping(plan.get("semantics"))
    from_plan = str(semantics.get("speech_act", "") or "").strip().lower()
    if from_plan:
        return from_plan
    performance = _mapping(meta.get("performance_context"))
    return str(performance.get("speech_act", "") or "").strip().lower()


def _opens_question(turn: SessionTurn) -> bool:
    if turn.role != "assistant":
        return False
    act = validate_assistant_act(_mapping(turn.meta).get("assistant_act", {})).act
    if act.opens_question:
        return True
    # Autonomous semantics are typed independently from chat AssistantAct.
    # A follow-up reminder points back to an already-open question; treating it
    # as a fresh question would reset the temporal anchor and recursively create
    # another reminder cycle.
    return _speech_act(turn) in {
        "curiosity_bid",
        "clarifying_question",
        "social_check_in",
        "contact_bid",
        "user_probe",
    }


def _recent_turns(ledger: SessionTurnLedger | None) -> tuple[SessionTurn, ...]:
    if ledger is None:
        return ()
    try:
        return tuple(ledger.all_turns())
    except Exception:
        return ()


class FollowUpAppraisalLayer:
    """Pure temporal projection over the public event ledger.

    ``DiscourseState`` still owns the semantic judgment that a reply is expected.
    The ledger owns temporal truth: which public turn came after which.  This
    layer only combines them into wait/remind/abandon behavior.
    """

    def __init__(self, policy: FollowUpPolicy | None = None) -> None:
        self.policy = resolve_dependency(policy, FollowUpPolicy.from_environment)

    @staticmethod
    def _none(discourse: DiscourseState) -> DiscourseState:
        return replace(
            discourse,
            followup_state="none",
            followup_reminder_due=False,
            followup_reminder_count=0,
            followup_abandoned=False,
            suppress_same_topic=False,
            may_start_new_topic=False,
            social_distance=0.0,
        )

    def evaluate(
        self,
        discourse: DiscourseState,
        *,
        now: float,
        user_turn_at: float = 0.0,
        assistant_turn_at: float = 0.0,
        ledger: SessionTurnLedger | None = None,
    ) -> DiscourseState:
        now = max(0.0, float(now or 0.0))
        turns = _recent_turns(ledger)

        # Production path: derive lifecycle entirely from public events.
        if turns:
            users = [turn for turn in turns if turn.role == "user"]
            assistants = [turn for turn in turns if turn.role == "assistant"]
            latest_user = users[-1] if users else None
            latest_assistant = assistants[-1] if assistants else None

            # Observer may report waiting_user a little after the user already
            # replied. Temporal ordering wins over that stale semantic baton.
            if latest_user is not None and latest_assistant is not None:
                if latest_user.index > latest_assistant.index:
                    return self._none(replace(discourse, reply_expected=False, assistant_may_continue=False))

            # Find the public turn that owns the current wait. Prefer an explicit
            # question; if the observer says waiting but the turn has older/legacy
            # metadata, the latest assistant turn is still a bounded anchor.
            question: SessionTurn | None = None
            for turn in reversed(assistants):
                if _opens_question(turn):
                    question = turn
                    break
            if question is None and discourse.reply_expected:
                question = latest_assistant

            if question is None:
                return self._none(discourse)

            if latest_user is not None and latest_user.index > question.index:
                return self._none(replace(discourse, reply_expected=False, assistant_may_continue=False))

            later_assistant = [turn for turn in assistants if turn.index > question.index]
            reminder = next(
                (turn for turn in later_assistant if _speech_act(turn) == "followup_reminder"),
                None,
            )
            topic_shift = next(
                (
                    turn
                    for turn in later_assistant
                    if _speech_act(turn) in {"independent_topic_shift", "abandoned_topic_shift"}
                ),
                None,
            )
            if topic_shift is not None:
                return replace(
                    discourse,
                    reply_expected=False,
                    assistant_may_continue=False,
                    followup_state="completed",
                    followup_reminder_due=False,
                    followup_reminder_count=1 if reminder is not None else 0,
                    followup_abandoned=True,
                    suppress_same_topic=True,
                    may_start_new_topic=False,
                    social_distance=self.policy.reserved_distance,
                )

            question_at = max(0.0, float(question.timestamp or 0.0))
            reminder_at = max(0.0, float(reminder.timestamp or 0.0)) if reminder is not None else 0.0

            if reminder is not None:
                if now - reminder_at >= self.policy.abandon_after_reminder_seconds:
                    return replace(
                        discourse,
                        reply_expected=False,
                        assistant_may_continue=False,
                        followup_state="abandoned",
                        followup_reminder_due=False,
                        followup_reminder_count=1,
                        followup_abandoned=True,
                        suppress_same_topic=True,
                        may_start_new_topic=True,
                        social_distance=self.policy.reserved_distance,
                    )
                return replace(
                    discourse,
                    reply_expected=True,
                    assistant_may_continue=False,
                    followup_state="waiting_after_reminder",
                    followup_reminder_due=False,
                    followup_reminder_count=1,
                    followup_abandoned=False,
                    suppress_same_topic=False,
                    may_start_new_topic=False,
                    social_distance=0.0,
                )

            age = max(0.0, now - question_at)
            if age >= self.policy.abandon_without_reminder_seconds:
                return replace(
                    discourse,
                    reply_expected=False,
                    assistant_may_continue=False,
                    followup_state="abandoned",
                    followup_reminder_due=False,
                    followup_reminder_count=0,
                    followup_abandoned=True,
                    suppress_same_topic=True,
                    may_start_new_topic=True,
                    social_distance=self.policy.reserved_distance,
                )
            reminder_due = bool(age >= self.policy.remind_after_seconds)
            return replace(
                discourse,
                reply_expected=True,
                assistant_may_continue=False,
                followup_state="reminder_due" if reminder_due else "waiting",
                followup_reminder_due=reminder_due,
                followup_reminder_count=0,
                followup_abandoned=False,
                suppress_same_topic=False,
                may_start_new_topic=False,
                social_distance=0.0,
            )

        # Compatibility for isolated tests/adapters with no ledger. This path is
        # deliberately memoryless; it can wait/remind based on the current typed
        # baton, but cannot pretend to know whether an older reminder was spoken.
        user_turn_at = max(0.0, float(user_turn_at or 0.0))
        assistant_turn_at = max(0.0, float(assistant_turn_at or 0.0))
        if not discourse.reply_expected:
            return self._none(discourse)
        if user_turn_at > assistant_turn_at > 0.0:
            return self._none(replace(discourse, reply_expected=False, assistant_may_continue=False))
        started_at = assistant_turn_at or max(0.0, now - discourse.seconds_since_assistant)
        age = max(0.0, now - started_at)
        if age >= self.policy.abandon_without_reminder_seconds:
            return replace(
                discourse,
                reply_expected=False,
                assistant_may_continue=False,
                followup_state="abandoned",
                followup_reminder_due=False,
                followup_abandoned=True,
                suppress_same_topic=True,
                may_start_new_topic=True,
                social_distance=self.policy.reserved_distance,
            )
        due = age >= self.policy.remind_after_seconds
        return replace(
            discourse,
            reply_expected=True,
            assistant_may_continue=False,
            followup_state="reminder_due" if due else "waiting",
            followup_reminder_due=due,
            followup_reminder_count=0,
            followup_abandoned=False,
            suppress_same_topic=False,
            may_start_new_topic=False,
            social_distance=0.0,
        )

    def note_spoken(self, plan: Any, *, now: float) -> None:
        """Compatibility no-op.

        Public commit is already recorded in ``SessionTurnLedger``. Keeping a
        second mutable receipt here would recreate the bug this refactor removes.
        """

        _ = (plan, now)

    def reset(self) -> None:
        """Compatibility no-op: there is no hidden lifecycle state to reset."""


__all__ = ["FollowUpAppraisalLayer", "FollowUpPolicy"]

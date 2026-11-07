"""Deterministic behavior fixtures for the pedagogical controller v1."""
from __future__ import annotations

from dataclasses import replace

import pytest

from runtime.ai_orchestrated.atomic_state import SQLiteAtomicStateStore
from runtime.pedagogical_contracts import (
    ComprehensionState,
    TeachingChunk,
    TeachingConcept,
    TeachingEpisode,
    TeachingPlan,
    TeachingReceipt,
)
from runtime.pedagogical_controller import observe_user_turn, select_next_action, start_episode
from runtime.pedagogical_runtime import PedagogicalRuntime


class _Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _FakeTimer:
    def __init__(self, _delay: float, _callback: object) -> None:
        self.daemon = False

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        pass


def _plan() -> TeachingPlan:
    return TeachingPlan(
        global_goal="Distinguish motive from surface behavior.",
        concepts=(
            TeachingConcept("motive", "Motive is not surface behavior.", "Paraphrase the distinction."),
            TeachingConcept("contrast", "Contrast the two explanations.", "Contrast the concepts."),
            TeachingConcept("apply", "Use the distinction in a new example.", "Apply it to an example."),
        ),
        success_evidence=("correct paraphrase", "novel application"),
    )


def _episode(*, status: str = "ready") -> TeachingEpisode:
    episode = start_episode(_plan(), now=1000.0)
    return replace(episode, status=status)


def _runtime(tmp_path: object, clock: _Clock) -> PedagogicalRuntime:
    return PedagogicalRuntime(
        SQLiteAtomicStateStore(tmp_path / "pedagogy.sqlite", clock=clock),  # type: ignore[operator]
        clock=clock,
        timer_factory=_FakeTimer,
    )


def test_correct_paraphrase_then_novel_application_advance_mastery_and_current_concept() -> None:
    initial = _episode()
    paraphrase = observe_user_turn(
        initial,
        "The motive explains why someone acts; the behavior is only what we see.",
        evidence=("user_paraphrase",),
        now=1001.0,
    )
    application = observe_user_turn(
        paraphrase.episode,
        "In a new case, avoiding a meeting can look quiet while the motive is fear of conflict.",
        evidence=("user_application",),
        now=1002.0,
    )

    assert paraphrase.evidence_codes == ("demonstrated_understanding",)
    assert paraphrase.action.kind == "advance"
    assert paraphrase.episode.comprehension.current_concept_id == "contrast"
    assert application.evidence_codes == ("demonstrated_understanding",)
    assert application.action.kind == "advance"
    assert application.episode.comprehension.estimated_mastery > paraphrase.episode.comprehension.estimated_mastery
    assert application.episode.comprehension.current_concept_id == "apply"


def test_repeated_misunderstanding_enters_repair_without_granting_mastery() -> None:
    initial = replace(
        _episode(),
        comprehension=ComprehensionState(estimated_mastery=0.45, confidence=0.8, current_concept_id="motive"),
    )
    first = observe_user_turn(
        initial,
        "So motive is just another name for the behavior itself.",
        evidence=("misconception_assertion",),
        now=1001.0,
    )
    repeated = observe_user_turn(
        first.episode,
        "Then the behavior and motive still mean exactly the same thing.",
        evidence=("misconception_assertion",),
        now=1002.0,
    )

    assert first.evidence_codes == ("candidate_misconception",)
    assert first.episode.status == "repairing"
    assert first.action.kind == "repair"
    assert repeated.evidence_codes == ("candidate_misconception",)
    assert repeated.episode.status == "repairing"
    assert repeated.action.kind == "repair"
    assert repeated.episode.comprehension.estimated_mastery == initial.comprehension.estimated_mastery


def test_explicit_eris_correction_enters_repair_and_preserves_user_mastery() -> None:
    initial = replace(
        _episode(),
        comprehension=ComprehensionState(estimated_mastery=0.7, confidence=0.8, current_concept_id="motive"),
    )
    result = observe_user_turn(
        initial,
        "No, Eris, that interpretation of what I said is wrong.",
        evidence=("eris_corrected",),
        now=1001.0,
    )

    assert result.evidence_codes == ("eris_corrected",)
    assert result.episode.status == "repairing"
    assert result.action.kind == "repair"
    assert result.episode.comprehension.estimated_mastery == initial.comprehension.estimated_mastery


@pytest.mark.parametrize(
    ("user_text", "expected_evidence"),
    (("ok", "self_report_understood"), ("", "silence"), ("mm-hmm", "self_report_understood")),
)
def test_acknowledgement_or_silence_emits_nonmastery_evidence(
    user_text: str,
    expected_evidence: str,
) -> None:
    initial = _episode()
    result = observe_user_turn(initial, user_text, evidence=("user_paraphrase",), now=1001.0)

    assert result.evidence_codes == (expected_evidence,)
    assert result.episode.status == "waiting_user"
    assert result.episode.comprehension == initial.comprehension


def test_topic_change_abandons_without_granting_mastery() -> None:
    initial = _episode()
    result = observe_user_turn(
        initial,
        "Let us change topics and discuss a different project.",
        evidence=("topic_change", "user_application"),
        now=1001.0,
    )

    assert result.evidence_codes == ("topic_change",)
    assert result.action.kind == "abandon"
    assert result.episode.status == "abandoned"
    assert result.episode.pending_chunk is None
    assert result.episode.comprehension == initial.comprehension


def test_already_known_terminal_concept_completes_without_unsolicited_teaching() -> None:
    initial = _episode()
    known_terminal = replace(
        initial,
        comprehension=ComprehensionState(estimated_mastery=0.8, confidence=0.9, current_concept_id="apply"),
        pending_chunk=TeachingChunk("apply:r1", "apply", "Apply it to an example.", claim="Use the distinction."),
    )
    action = select_next_action(known_terminal, now=1001.0)

    assert action.kind == "complete"
    assert action.reason == "mastery_threshold"
    assert action.directive is None


def test_user_input_during_due_dwell_prevents_claim_or_autonomous_speech(tmp_path: object) -> None:
    clock = _Clock()
    runtime = _runtime(tmp_path, clock)
    assert runtime.start_episode(_episode(status="dwelling"))
    due_before_user_turn = runtime.snapshot(now=1001.0)
    assert due_before_user_turn["due"] is True
    assert due_before_user_turn["pending"] is True
    assert due_before_user_turn["active_episode"]["pending_chunk"]["chunk_id"] == "motive:r1"

    observed = runtime.observe_user_turn(
        "Wait, I need to clarify the distinction first.",
        evidence=("focused_question",),
        now=1001.0,
    )

    assert observed.evidence_codes == ("repeated_focused_question",)
    assert observed.action.kind == "wait"
    assert observed.action.reason == "stall_limit"
    assert observed.episode.status == "waiting_user"
    assert runtime.claim_due(worker_id="autonomous", now=1002.0) is None
    snapshot = runtime.snapshot(now=1002.0)
    assert snapshot["claimed"] is False
    assert snapshot["active_episode"]["status"] == "waiting_user"


def test_restart_clears_stale_dwell_claim_and_rearms_pending_chunk(tmp_path: object) -> None:
    clock = _Clock()
    store = SQLiteAtomicStateStore(tmp_path / "restart.sqlite", clock=clock)  # type: ignore[operator]
    first = PedagogicalRuntime(store, clock=clock, timer_factory=_FakeTimer)
    assert first.start_episode(_episode(status="dwelling"))
    claimed = first.claim_due(worker_id="first-worker", now=1001.0)
    assert claimed is not None
    first.shutdown()

    clock.value = 1003.0
    restarted = PedagogicalRuntime(store, clock=clock, timer_factory=_FakeTimer)
    rearmed = restarted.claim_due(worker_id="second-worker", now=1003.0)

    assert rearmed is not None
    assert (rearmed.episode_id, rearmed.plan_revision, rearmed.chunk_id) == (
        claimed.episode_id,
        claimed.plan_revision,
        claimed.chunk_id,
    )


def test_duplicate_or_stale_receipt_is_rejected_while_pending_chunk_remains(tmp_path: object) -> None:
    clock = _Clock()
    runtime = _runtime(tmp_path, clock)
    assert runtime.start_episode(_episode(status="dwelling"))
    directive = runtime.claim_due(worker_id="worker", now=1001.0)
    assert directive is not None

    stale = TeachingReceipt(directive.episode_id, directive.plan_revision + 1, directive.chunk_id, turn_id="stale")
    assert runtime.commit_receipt(stale, now=1001.1) is False
    after_stale = runtime.snapshot(now=1001.1)
    assert after_stale["active_episode"]["pending_chunk"]["chunk_id"] == directive.chunk_id
    assert after_stale["claimed"] is True

    accepted = TeachingReceipt(directive.episode_id, directive.plan_revision, directive.chunk_id, turn_id="accepted")
    assert runtime.commit_receipt(accepted, now=1001.2, claim_token=directive.claim_token) is True
    assert runtime.commit_receipt(accepted, now=1001.3, claim_token=directive.claim_token) is False
    after_duplicate = runtime.snapshot(now=1001.3)
    assert after_duplicate["active_episode"]["pending_chunk"]["chunk_id"] != directive.chunk_id
    assert after_duplicate["active_episode"]["status"] == "speaking"

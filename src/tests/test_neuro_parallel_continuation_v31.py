from __future__ import annotations

from dataclasses import replace
from threading import Event

from cortex.subject.neuro_control import NeuroActionSelector, NeuroDeliberationAdvice
from cortex.subject.service import SubjectService, SubjectServiceConfig
from runtime.neuro_continuation import NeuroContinuationRuntime
from runtime.subject_ingress import SubjectIngress


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        self.now += 0.01
        return self.now


class Reviewer:
    def review_subject_frame(self, *_args, **_kwargs):
        raise AssertionError("queueing must not start slow cognition before release")


def _fast_turn(text: str, turn_id: str = "turn-1"):
    clock = Clock()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=clock,
    )
    ingress = SubjectIngress(service)
    session = ingress.prepare_user_session(
        text, created_at=clock(), turn_id=turn_id, planning_context={}
    )
    fast = ingress.mind_loop.complete_fast_sync(session)
    return clock, service, session, fast


def test_user_ambiguity_can_recommend_parallel_review_without_blocking() -> None:
    clock, _service, _session, fast = _fast_turn("hôm nay em ăn gì?")
    assert fast.selection.requires_deliberation is False
    assert fast.selection.parallel_review_recommended is True
    assert fast.commit.source == "neuro_fast_gate"

    # High entropy by itself is still only telemetry. A short acknowledgement
    # with no local winner uncertainty does not create background work.
    _clock2, _service2, _session2, ack = _fast_turn("phải ha", "turn-2")
    assert ack.selection.entropy >= 0.82
    assert ack.selection.requires_deliberation is False
    assert ack.selection.parallel_review_recommended is False


def test_schedule_is_queue_only_until_public_writer_releases_it() -> None:
    clock, _service, session, fast = _fast_turn("hôm nay em ăn gì?")
    reviewer = Reviewer()
    runtime = NeuroContinuationRuntime(
        committer_provider=lambda: reviewer,
        clock=clock,
    )
    runtime.begin_user_turn("turn-1")
    assert runtime.schedule(
        frame=session.frame,
        selection=fast.selection,
        baseline_commit=fast.commit,
        origin_turn_id="turn-1",
        user_text="hôm nay em ăn gì?",
        snapshot={},
    )
    assert runtime.public_state("turn-1")["deep_thought_running"] is True

    started = Event()

    def fake_run(_job):
        started.set()

    runtime._run_job = fake_run  # type: ignore[method-assign]
    assert not started.is_set()
    assert runtime.release("turn-1") == 1
    assert started.wait(1.0)


def test_material_advice_becomes_claimable_continuation_but_trivia_does_not() -> None:
    clock, _service, session, fast = _fast_turn("hôm nay em ăn gì?")
    runtime = NeuroContinuationRuntime(
        committer_provider=lambda: Reviewer(),
        clock=clock,
    )
    runtime.begin_user_turn("turn-1")
    assert runtime.schedule(
        frame=session.frame,
        selection=fast.selection,
        baseline_commit=fast.commit,
        origin_turn_id="turn-1",
        user_text="hôm nay em ăn gì?",
        snapshot={},
    )
    job = next(iter(runtime._jobs.values()))

    # Agreement/paraphrase is intentionally dropped even if a worker asks to speak.
    trivial = NeuroDeliberationAdvice(
        commit=replace(fast.commit, confidence=0.91, reason="same conclusion"),
        followup_brief="same answer with more words",
        material_delta=0.2,
        should_follow_up=True,
    )
    runtime._accept_advice(job, trivial)
    assert runtime.is_due(now=clock()) is False

    # A materially new correction/constraint becomes a normal typed affordance.
    coalitions = list(session.frame.coalitions)
    changed_id = next(
        (item.coalition_id for item in coalitions if item.coalition_id != fast.commit.selected_coalition_id),
        fast.commit.selected_coalition_id,
    )
    material = NeuroDeliberationAdvice(
        commit=replace(
            fast.commit,
            selected_coalition_id=changed_id,
            confidence=0.88,
            reason="missed consequential constraint",
        ),
        followup_brief="the earlier answer missed a constraint that changes the recommendation",
        material_delta=0.82,
        should_follow_up=True,
    )
    runtime._accept_advice(job, material)
    affordance = runtime.peek_due(now=clock(), interaction_busy=False)
    assert affordance is not None
    assert affordance.owner == "neuro_continuation"
    assert affordance.claim_required is True
    assert affordance.candidate.expression_commitment["origin"] == "neuro_continuation"
    candidate = runtime.claim_affordance(affordance, worker_id="self-turn", now=clock())
    assert candidate is not None
    assert candidate.speech_act == "deep_thought_followup"
    assert runtime.peek_due(now=clock(), interaction_busy=False) is None


def test_new_user_turn_invalidates_old_neuro_result() -> None:
    clock, _service, session, fast = _fast_turn("hôm nay em ăn gì?")
    runtime = NeuroContinuationRuntime(
        committer_provider=lambda: Reviewer(),
        clock=clock,
    )
    runtime.begin_user_turn("turn-1")
    assert runtime.schedule(
        frame=session.frame,
        selection=fast.selection,
        baseline_commit=fast.commit,
        origin_turn_id="turn-1",
        user_text="hôm nay em ăn gì?",
        snapshot={},
    )
    old_job = next(iter(runtime._jobs.values()))
    runtime.begin_user_turn("turn-2")

    stale = NeuroDeliberationAdvice(
        commit=replace(fast.commit, confidence=0.95, reason="stale result"),
        followup_brief="this should never surface",
        material_delta=1.0,
        should_follow_up=True,
    )
    runtime._accept_advice(old_job, stale)
    assert runtime.snapshot(now=clock())["due"] is False
    assert runtime.peek_due(now=clock(), interaction_busy=False) is None


def test_cancel_turn_drops_queued_review_when_public_reply_failed() -> None:
    clock, _service, session, fast = _fast_turn("hôm nay em ăn gì?")
    runtime = NeuroContinuationRuntime(
        committer_provider=lambda: Reviewer(),
        clock=clock,
    )
    runtime.begin_user_turn("turn-1")
    assert runtime.schedule(
        frame=session.frame,
        selection=fast.selection,
        baseline_commit=fast.commit,
        origin_turn_id="turn-1",
        user_text="hôm nay em ăn gì?",
        snapshot={},
    )
    assert runtime.snapshot(now=clock())["deep_thought_running"] is True
    assert runtime.cancel_turn("turn-1") is True
    assert runtime.snapshot(now=clock())["deep_thought_running"] is False
    assert runtime.release("turn-1") == 0

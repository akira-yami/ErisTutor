from __future__ import annotations

from runtime.temporal_mind import TemporalMind, TemporalMindConfig, TemporalSignal


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_weak_repeated_revision_does_not_wake_forever() -> None:
    clock = Clock()
    mind = TemporalMind(clock=clock)
    signal = TemporalSignal("attention_pressure", 0.40, "attention", revision=7)
    decisions = []
    for _ in range(8):
        decisions.append(mind.observe((signal,), now=clock()))
        clock.advance(1.0)
    assert not any(item.wake for item in decisions)
    assert mind.state.activation < 0.45


def test_structured_due_wakes_without_waiting_for_threshold() -> None:
    clock = Clock()
    mind = TemporalMind(clock=clock)
    decision = mind.observe(
        (TemporalSignal("structured_due", 0.92, "continuation", revision=1),),
        now=clock(),
    )
    assert decision.wake is True
    assert decision.reason == "temporal:hard:structured_due"


def test_distinct_medium_signals_accumulate_into_wake() -> None:
    clock = Clock()
    mind = TemporalMind(clock=clock, config=TemporalMindConfig(wake_threshold=0.44))
    first = mind.observe((TemporalSignal("scene_delta", 0.75, "screen", revision=1),), now=clock())
    assert first.wake is False
    clock.advance(1.0)
    second = mind.observe((TemporalSignal("activity_event", 0.78, "activity", revision=2),), now=clock())
    assert second.wake is True
    assert second.reason == "temporal:pressure_threshold"


def test_silence_creates_cooldown_and_drops_activation() -> None:
    clock = Clock()
    mind = TemporalMind(clock=clock, config=TemporalMindConfig(wake_threshold=0.30))
    mind.observe((TemporalSignal("activity_event", 0.90, "activity", revision=1),), now=clock())
    before = mind.state.activation
    mind.mark_silence(now=clock())
    assert mind.state.activation < before
    clock.advance(1.0)
    decision = mind.observe((TemporalSignal("scene_delta", 0.90, "screen", revision=2),), now=clock())
    assert decision.wake is False
    assert decision.reason == "temporal:cooldown"


def test_user_turn_and_public_act_update_same_continuous_state() -> None:
    clock = Clock()
    mind = TemporalMind(clock=clock)
    mind.note_user_turn(now=clock(), focus_key="thread:abc")
    assert mind.state.last_user_at == clock()
    assert mind.state.focus_key == "thread:abc"
    assert mind.state.readiness >= 0.8
    clock.advance(2.0)
    mind.note_public_act(now=clock())
    assert mind.state.last_public_at == clock()
    assert mind.state.readiness < 0.8
    assert mind.state.quiet_until > clock()


def test_prompt_projection_is_small_and_non_semantic() -> None:
    clock = Clock()
    mind = TemporalMind(clock=clock)
    mind.observe((TemporalSignal("seed_available", 0.7, "seed", revision=3),), now=clock())
    context = mind.prompt_context(now=clock())
    assert "TEMPORAL MIND" in context
    assert "timing only" in context
    assert "seed_available" in context

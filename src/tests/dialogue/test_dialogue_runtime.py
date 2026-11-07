from __future__ import annotations

import asyncio

import pytest

from runtime.dialogue.contracts import (
    DialoguePlan,
    DiscourseState,
    PerformanceIntent,
    RollingThoughtBuffer,
    SocialRiskPolicy,
    ThoughtCandidate,
)
from runtime.dialogue.realizer import DialogueContractViolation, NaturalDialogueRealizer
from runtime.dialogue.rolling_thought import RollingThoughtService


def policy() -> SocialRiskPolicy:
    return SocialRiskPolicy("p", "general", 0.8, 0.8, 0.5, 0.5, 0.2, 0.4, 0.1, 0.0, 0.9, ())


def discourse() -> DiscourseState:
    return DiscourseState("s", 0, "", (), (), (), (), "", "", 1.0, 0.0, "normal", (), 0.0)


@pytest.mark.asyncio
async def test_realizer_adds_no_facts_outside_plan() -> None:
    plan = DialoguePlan(
        "plan", "decision", "inform", ("Build passed.",), ("All tests are green.",), ("secret",), (), 10, (), PerformanceIntent("plan"), ("test-log",)
    )
    thoughts = RollingThoughtBuffer("b", "s", 0, 5, (), 0.0)
    chunks = [chunk async for chunk in NaturalDialogueRealizer(id_factory=lambda: "chunk").realize(plan=plan, discourse=discourse(), thoughts=thoughts, social_policy=policy())]
    assert " ".join(chunk.text for chunk in chunks) == "Build passed. All tests are green."


@pytest.mark.asyncio
async def test_realizer_rejects_forbidden_fact() -> None:
    plan = DialoguePlan("plan", "decision", "inform", ("secret value is 3",), (), ("secret",), (), 10, (), PerformanceIntent("plan"), ("ref",))
    with pytest.raises(DialogueContractViolation):
        _ = [chunk async for chunk in NaturalDialogueRealizer().realize(plan=plan, discourse=discourse(), thoughts=RollingThoughtBuffer("b", "s", 0, 5, (), 0.0), social_policy=policy())]


def test_rolling_thought_buffer_is_bounded() -> None:
    now = [0.0]
    ids = iter(f"b{i}" for i in range(10))
    service = RollingThoughtService(id_factory=lambda: next(ids), clock=lambda: now[0], capacity=3)
    for index in range(5):
        service.add("s", ThoughtCandidate(f"t{index}", f"c{index}", f"k{index}", ("r",), index / 5, 0.5, True, 100.0))
    assert len(service.get("s").candidates) == 3

class FakeTTS:
    async def synthesize(self, *, chunk_id: str, text: str, prosody_hint: str = ""):
        return type("Receipt", (), {"status": "ok"})()


@pytest.mark.asyncio
async def test_incremental_speech_cancellation_stops_remaining_chunks() -> None:
    from runtime.dialogue.contracts import SpeechChunk
    from runtime.dialogue.incremental_speech import IncrementalSpeechCoordinator

    cancellation = asyncio.Event()

    async def stream():
        yield SpeechChunk("c1", "plan", 0, "one", True, True, "", ())
        cancellation.set()
        yield SpeechChunk("c2", "plan", 1, "two", True, True, "", ())

    coordinator = IncrementalSpeechCoordinator(tts_broker=FakeTTS(), clock=lambda: 1.0, id_factory=lambda: "delivery")
    receipt = await coordinator.speak(chunks=stream(), performance=PerformanceIntent("plan"), cancellation=cancellation)
    assert receipt.chunks_delivered == ("c1",)
    assert receipt.chunks_cancelled == ("c2",)

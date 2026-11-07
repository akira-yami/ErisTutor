from __future__ import annotations

from pathlib import Path

import pytest

from runtime.dialogue.body_adapter import SubjectPerformanceBodyAdapter
from runtime.dialogue.contracts import PerformanceIntent, SpeechChunk


def chunk(*, sequence: int = 3, cues=()) -> SpeechChunk:
    return SpeechChunk("chunk", "plan", sequence, "hello", True, True, "plain", tuple(cues))


def performance(*, cues=(), energy: float = 0.5) -> PerformanceIntent:
    return PerformanceIntent("plan", energy=energy, pace=0.4, vulnerability=0.2, restraint=0.8, gaze_target="user", body_cues=tuple(cues))


def test_python_body_frame_is_bounded_and_contains_no_private_state() -> None:
    cues = tuple(f"cue-{index}" for index in range(50))
    frame = SubjectPerformanceBodyAdapter.build_frame(
        plan_id="plan",
        chunk=chunk(cues=("cue-0", "extra")),
        performance=performance(cues=cues, energy=2.0),
        decision_id="decision",
        delivery_id="delivery",
    )
    assert SubjectPerformanceBodyAdapter.validate_frame(frame)
    assert frame["energy"] == 1.0
    assert len(frame["body_cues"]) == 32
    assert frame["decision_id"] == "decision"
    assert frame["delivery_id"] == "delivery"
    assert not ({"beliefs", "needs", "narrative", "private_state", "evidence"} & set(frame))


@pytest.mark.asyncio
async def test_transport_receives_correlated_frame() -> None:
    received = {}

    async def transport(frame):
        received.update(frame)
        return {"status": "accepted", "sync_error_ms": 4}

    receipt = await SubjectPerformanceBodyAdapter(transport).send_performance(
        plan_id="plan",
        chunk=chunk(),
        performance=performance(),
        decision_id="decision",
        delivery_id="delivery",
    )
    assert receipt.status == "accepted"
    assert received["decision_id"] == "decision"
    assert received["delivery_id"] == "delivery"


def test_python_and_csharp_contract_constants_match() -> None:
    root = Path(__file__).resolve().parents[2]
    csharp = (root / "body" / "SubjectPerformanceFrame.cs").read_text(encoding="utf-8")
    assert f'Protocol = "{SubjectPerformanceBodyAdapter.PROTOCOL}"' in csharp
    assert f"CurrentSchemaVersion = {SubjectPerformanceBodyAdapter.SCHEMA_VERSION}" in csharp
    assert f"MaxBodyCues = {SubjectPerformanceBodyAdapter.MAX_BODY_CUES}" in csharp
    assert f"MaxTextField = {SubjectPerformanceBodyAdapter.MAX_TEXT_FIELD}" in csharp
    for field in ("plan_id", "decision_id", "delivery_id", "sequence", "energy", "pace", "vulnerability", "restraint", "gaze_target", "body_cues"):
        assert f'"{field}"' in csharp

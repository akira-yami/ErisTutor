from __future__ import annotations

from types import SimpleNamespace

from runtime.public_turn_ledger import PublicTurnLedger
from runtime.public_turn_runtime import PublicTurnRuntime


def test_public_turn_runtime_records_links_and_marks_short_turn_collision() -> None:
    now = {"value": 100.0}
    owner = SimpleNamespace(
        public_turn_ledger=PublicTurnLedger(),
        reply_context=SimpleNamespace(previous_public_turn=None),
        chat_mode=True,
        _chat_processing=False,
        autonomous_turn_collision_until=0.0,
    )
    runtime = PublicTurnRuntime(owner, clock=lambda: now["value"])
    decision = SimpleNamespace(
        text="Một câu public có thể được trả lời.",
        should_speak=True,
        parse_mode="json",
        intent="reply",
        reasoning_tags=(),
    )

    turn = runtime.record(decision)
    assert turn is not None
    assert turn.timestamp == 100.0
    assert turn.channel == "chat"

    now["value"] = 115.0
    linked = runtime.link_previous_for_chat(115.0)
    assert linked is turn
    assert owner.reply_context.previous_public_turn is turn
    assert "age_seconds=15.0" in runtime.previous_turn_context(turn)

    runtime.note_turn_collision(seconds=25.0)
    # Requested 25s bị cap còn 5s: turn collision không được biến thành
    # "presence cooldown" dài chỉ vì user vừa có public turn.
    assert owner.autonomous_turn_collision_until == 120.0

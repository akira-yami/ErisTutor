# -*- coding: utf-8 -*-
"""V97 regression: một trace public thống nhất cho chat + autonomous + TTS."""
from __future__ import annotations

import json


def test_public_turn_trace_store_compacts_cross_layer_trace(tmp_path) -> None:
    from runtime.ai_orchestrated.autonomous_trace import PublicTurnTraceStore

    path = tmp_path / "public_turn_trace.jsonl"
    store = PublicTurnTraceStore(path=path)
    trace_id = "trace-chat-1"

    store.append("turn_input", {
        "trace_id": trace_id,
        "turn_id": "session_turn:9",
        "channel": "chat",
        "stage": "input",
        "owner": "user",
        "semantic_owner": "shared",
        "trigger_owner": "user",
        "source_kind": "user_turn",
    })
    store.append("authority_decision", {
        "trace_id": trace_id,
        "stage": "authority",
        "owner": "subject_core",
        "authority_action": "speak",
        "authority_reason": "direct_user_reply",
        "goal_state": "active",
        "proposal_source": "intent_planner",
    })
    store.append("public_commit", {
        "trace_id": trace_id,
        "stage": "public_commit",
        "owner": "executive_governor",
        "semantic_owner": "shared",
        "trigger_owner": "user",
        "motive": "answer",
        "speech_act": "answer",
        "public_committed": True,
        # Raw text/private prompt không được lọt vào trace debug.
        "text": "đây là nội dung không được ghi",
        "private_prompt": "secret",
    })
    store.append("tts_event", {
        "trace_id": trace_id,
        "stage": "tts",
        "owner": "tts",
        "tts_event": "audio_start",
    })
    store.append("outcome", {
        "trace_id": trace_id,
        "stage": "outcome",
        "owner": "public_lifecycle",
        "outcome": "delivered",
    })

    compact = store.compact_trace(trace_id)
    assert compact["trace_id"] == trace_id
    assert compact["channel"] == "chat"
    assert compact["semantic_owner"] == "shared"
    assert compact["trigger_owner"] == "user"
    assert compact["authority_action"] == "speak"
    assert compact["public_committed"] is True
    assert compact["tts_status"] == "started"
    assert compact["outcome"] == "delivered"

    raw = path.read_text(encoding="utf-8")
    assert "đây là nội dung" not in raw
    assert "secret" not in raw
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert all(row["schema"] == "eris.public_turn.trace.v97" for row in rows)


def test_legacy_autonomous_trace_wrapper_uses_same_public_store(tmp_path, monkeypatch) -> None:
    import runtime.ai_orchestrated.autonomous_trace as trace_mod

    store = trace_mod.PublicTurnTraceStore(path=tmp_path / "trace.jsonl")
    monkeypatch.setattr(trace_mod, "_GLOBAL_TRACE_STORE", store)

    trace_mod.append_autonomous_trace("outcome", {
        "trace_id": "auto-1",
        "turn_id": "plan-1",
        "stage": "outcome",
        "owner": "public_lifecycle",
        "outcome": "delivered",
    })

    rows = store.recent(limit=10)
    assert rows and rows[-1]["trace_id"] == "auto-1"
    assert rows[-1]["schema"] == "eris.public_turn.trace.v97"


def test_compact_trace_reports_failed_tts_without_claiming_delivery(tmp_path) -> None:
    from runtime.ai_orchestrated.autonomous_trace import PublicTurnTraceStore

    store = PublicTurnTraceStore(path=tmp_path / "trace.jsonl")
    store.append("public_commit", {
        "trace_id": "t-fail",
        "channel": "autonomous",
        "stage": "public_commit",
        "owner": "executive_governor",
        "public_committed": True,
    })
    store.append("tts_event", {
        "trace_id": "t-fail",
        "stage": "tts",
        "owner": "tts",
        "tts_event": "audio_failed",
    })
    store.append("outcome", {
        "trace_id": "t-fail",
        "stage": "outcome",
        "owner": "public_lifecycle",
        "outcome": "failed",
    })

    compact = store.compact_trace("t-fail")
    assert compact["tts_status"] == "failed"
    assert compact["outcome"] == "failed"
    assert compact["public_committed"] is True

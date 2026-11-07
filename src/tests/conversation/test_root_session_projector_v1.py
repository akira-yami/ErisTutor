# -*- coding: utf-8 -*-
"""Giai đoạn 1: chứng minh Root true state != prompt phái sinh.

Không có network, không giả vờ benchmark live DeepSeek. Test trên chính list
conversation_messages mà model router nhận và trên fake router ghi lại request.
"""
from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

from llm.token_budget import estimate_tokens
from runtime.conversation.root_session import RootConversationSession, _ingest_recent_turns
from runtime.conversation.root_session_projector import (
    RootSessionState, SelfReference, TurnRecord, bootstrap_content, project_root_messages,
)


class Client:
    base_url = "http://localhost:6011/v1"
    model_name = "deepseek-v4-flash"


class Router:
    def __init__(self):
        self.calls = []

    def invoke_structured_worker(self, _client, **kwargs):
        messages = copy.deepcopy(kwargs["conversation_messages"])
        self.calls.append(messages)
        if "root_session_bootstrap" in messages[-1]["content"]:
            reply = '{ "kind" : "no_reply" , "reason" : "self_loaded" }'
        else:
            reply = '{ "kind" : "move", "say" : "hi" }'
        kwargs["raw_reply_sink"].append(reply)
        return json.loads(reply)


class Host:
    def __init__(self, long_self=""):
        self.data = {"belief": {"one": 1, "two": 1}, "identity": long_self}
        self.ai_state_reducer = SimpleNamespace(snapshot=lambda: SimpleNamespace(to_dict=lambda: copy.deepcopy(self.data)))


def state(turns=(), current_step=None):
    raw = json.dumps({"identity": "tôi là Eris", "beliefs": ["a", "b"]}, ensure_ascii=False, separators=(",", ":"))
    return RootSessionState(
        self_ref=SelfReference(raw, 2, len(raw), hashlib.sha256(raw.encode()).hexdigest()[:16],
                              '{ "kind" : "no_reply" }'),
        turns=list(turns), system_prompt="[grounding]\nTurnFrame\nResponseContract\n[dynamic_policy]",
        current_step_payload=current_step or {"event": {"kind": "user_turn", "text": "hi"}},
    )


def records(n):
    return [TurnRecord(f"history:{i}", "user" if i % 2 == 0 else "assistant", f"utterance:{i}")
            for i in range(n)]


def test_projector_idempotent():
    before = state(records(18))
    original = copy.deepcopy(before)
    first = project_root_messages(before, window_k=12)
    assert first == project_root_messages(before, window_k=12)
    assert before == original


def test_projector_bounded():
    for n in (0, 1, 18, 24, 50, 100, 200):
        projected = project_root_messages(state(records(n)), window_k=12)
        assert len(projected) == 3 + min(24, n) + 1 + (1 if n > 24 else 0)
        assert projected[-1]["role"] == "user"


def test_status_flip_no_resend():
    pending = TurnRecord("a0", "assistant", "xin chào", "audio:1", "pending")
    session_state = state([TurnRecord("u0", "user", "ô"), pending])
    first = project_root_messages(session_state, window_k=12)
    snapshot = (pending.turn_id, pending.content, session_state.turns.index(pending))
    assert _ingest_recent_turns(session_state, [
        {"role": "user", "raw_content": "ô", "history_id": "u0"},
        {"role": "assistant", "raw_content": "xin chào", "history_id": "a0",
         "delivery_id": "audio:1", "delivery_status": "cancelled"},
    ]) == 0
    second = project_root_messages(session_state, window_k=12)
    assert len(first) == len(second)
    assert [i for i, (a, b) in enumerate(zip(first, second)) if a != b] == [4]
    assert (pending.turn_id, pending.content, session_state.turns.index(pending)) == snapshot
    assert second[4]["content"] == "xin chào\n[Đã hiển thị; âm thanh bị ngắt giữa chừng]"


def test_window_drop_carries_identity():
    session_state = state(records(40))
    projected = project_root_messages(session_state, window_k=12)
    assert projected[3]["role"] == "system"
    assert "16 lượt" in projected[3]["content"]
    assert len([m for m in projected if "lượt trước đó trong phiên" in m["content"]]) == 1
    assert [m["content"] for m in projected[4:-1]] == [t.content for t in session_state.turns[-24:]]


def test_self_invariant():
    session_state = state(records(4))
    first = project_root_messages(session_state, window_k=12)
    session_state.turns.extend(records(60))
    second = project_root_messages(session_state, window_k=12)
    assert second[:3] == first[:3]
    assert second[1]["content"] == bootstrap_content(session_state.self_ref.content_json)


def test_self_overlay_is_cumulative_from_bootstrap():
    host, router, client = Host(), Router(), Client()
    session = RootConversationSession()
    session._host = host
    assert session._prime(router=router, client=client, system="system", schema={})
    host.data["belief"]["one"] = 9
    assert session.ask(router=router, client=client, system="system", schema={},
                       payload={"event": {"text": "1"}}, runtime_context={}, recent_history=[])
    first = json.loads(router.calls[-1][-1]["content"])
    assert first["self_changes_since_bootstrap"] == {"mind_state": {"belief": {"one": 9}}}
    host.data["belief"]["two"] = 8
    assert session.ask(router=router, client=client, system="system", schema={},
                       payload={"event": {"text": "2"}}, runtime_context={}, recent_history=[])
    second = json.loads(router.calls[-1][-1]["content"])
    assert second["self_changes_since_bootstrap"] == {"mind_state": {"belief": {"one": 9, "two": 8}}}


def test_status_single_source():
    session_state = state([TurnRecord("a", "assistant", "câu nói", "d", "cancelled")],
                          current_step={"event": "next"})
    projected = project_root_messages(session_state, window_k=12)
    assert "âm thanh bị ngắt" in projected[-2]["content"]
    assert "recent_public_delivery_updates" not in projected[-1]["content"]
    assert "recent_public_history_reconcile" not in projected[-1]["content"]


def test_window_is_raw_turns():
    all_turns = [TurnRecord(str(i), "assistant" if i % 4 else "user", f"turn:{i}",
                            kind="autonomous" if i % 4 else "public") for i in range(35)]
    projected = project_root_messages(state(all_turns), window_k=3)
    assert [(x["role"], x["content"]) for x in projected[4:-1]] == [
        (x.role, x.content) for x in all_turns[-6:]
    ]


def test_turn_id_stable_on_reload():
    session_state = state()
    packet = [{"role": "user", "content": "xin chào"},
              {"role": "assistant", "raw_content": "ừ", "delivery_id": "audio:1", "delivery_status": "pending"}]
    assert _ingest_recent_turns(session_state, packet) == 2
    ids = [t.turn_id for t in session_state.turns]
    assert _ingest_recent_turns(session_state, copy.deepcopy(packet)) == 0
    assert [t.turn_id for t in session_state.turns] == ids
    packet[-1]["delivery_status"] = "cancelled"
    assert _ingest_recent_turns(session_state, copy.deepcopy(packet)) == 0
    assert [t.turn_id for t in session_state.turns] == ids
    assert session_state.turns[-1].status == "cancelled"


def test_repeated_same_text_is_not_collapsed():
    session_state = state()
    assert _ingest_recent_turns(session_state, [{"role": "user", "content": "ok"}]) == 1
    assert _ingest_recent_turns(session_state, [
        {"role": "user", "content": "ok"}, {"role": "user", "content": "ok"},
    ]) == 1
    assert len({t.turn_id for t in session_state.turns}) == 2
    assert _ingest_recent_turns(session_state, [
        {"role": "user", "content": "ok"}, {"role": "user", "content": "ok"},
    ]) == 0


def test_runtime_overflow_shrinks_window_without_mutating_turns(monkeypatch):
    monkeypatch.setenv("ERIS_ROOT_SESSION_WINDOW_PAIRS", "12")
    host, router, client = Host(), Router(), Client()
    session = RootConversationSession()
    session._host = host
    assert session._prime(router=router, client=client, system="system", schema={})
    previous = [
        {"history_id": f"h:{i}", "role": "user", "raw_content": f"content:{i}"}
        for i in range(40)
    ]
    original = session._fits
    session._fits = lambda _client, messages: len(messages) <= 10
    assert session.ask(router=router, client=client, system="system", schema={},
                       payload={"event": {}}, runtime_context={}, recent_history=previous)
    assert len(router.calls[-1]) <= 10
    assert len(session._state.turns) == 40
    session._fits = original
    assert session.ask(router=router, client=client, system="system", schema={},
                       payload={"event": {}}, runtime_context={}, recent_history=previous)
    assert len(router.calls[-1]) == 3 + 24 + 1 + 1


def test_self_over_cap_returns_one_shot_without_truncation():
    host, router, client = Host(), Router(), Client()
    session = RootConversationSession()
    session._host = host
    assert session._prime(router=router, client=client, system="system", schema={})
    old = session._state.self_ref.content_json
    original_calls = len(router.calls)
    session._fits = lambda _client, messages: len(messages) != 2
    assert session.ask(router=router, client=client, system="system", schema={},
                       payload={"event": {}}, runtime_context={}, recent_history=[]) is None
    assert len(router.calls) == original_calls
    assert session._state.self_ref.content_json == old


def test_fake_router_twenty_rounds_has_bounded_token_cost():
    # Đây là số ước lượng IN-PROCESS, tuyệt đối không phải số đo DeepSeek Web.
    host, router, client = Host("x" * 100_000), Router(), Client()
    session = RootConversationSession()
    session._host = host
    assert session._prime(router=router, client=client, system="system", schema={})
    history, tokens, msg_counts = [], [], []
    for i in range(20):
        history.extend([
            {"role": "user", "history_id": f"u:{i}", "raw_content": f"câu hỏi {i}"},
            {"role": "assistant", "history_id": f"a:{i}", "raw_content": f"đáp {i}",
             "delivery_id": f"d:{i}", "delivery_status": "pending"},
        ])
        # Ba lần flip status trên turn còn trong cửa sổ.
        if i in (5, 10, 15):
            history[-2]["content"] = "some original words from model"
            history[-1]["delivery_status"] = "cancelled"
        result = session.ask(
            router=router, client=client, system="system", schema={},
            payload={"event": {"kind": "user_turn", "text": "xin chào"}, "tool_results": []},
            runtime_context={}, recent_history=history[-32:],
        )
        assert result is not None
        messages = router.calls[-1]
        tokens.append(estimate_tokens(messages))
        msg_counts.append(len(messages))
        assert "recent_public_history_reconcile" not in messages[-1]["content"]
        assert "recent_public_delivery_updates" not in messages[-1]["content"]
        assert len(messages) <= 3 + 24 + 1 + 1
    assert max(msg_counts) <= 29
    assert tokens[19] <= tokens[4] * 1.10
    assert max(tokens[13:]) <= min(tokens[13:]) * 1.05
    assert all(tokens[i] <= tokens[i - 1] * 1.15 for i in (5, 10, 15))


def test_sliding_same_text_with_real_history_ids_keeps_every_turn():
    session_state = state()
    first = [{"history_id": f"history:{i}", "role": "user", "raw_content": "ok"} for i in range(32)]
    assert _ingest_recent_turns(session_state, first) == 32
    next_window = first[1:] + [{"history_id": "history:32", "role": "user", "raw_content": "ok"}]
    assert _ingest_recent_turns(session_state, next_window) == 1
    assert len(session_state.turns) == 33
    assert session_state.turns[-1].turn_id == "history:32"

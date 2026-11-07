# -*- coding: utf-8 -*-
"""v0.2: portrait không được biến thành agent mới, state writer mới, hay history giả."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from dataclasses import replace

import pytest

from runtime.affective_voice_state import AffectiveVoiceState
from runtime.ai_orchestrated.contracts import RuntimeMindState
from runtime.conversation.claim_evidence import build_evidence_registry
from runtime.conversation.character_portrait import (
    CharacterPortrait, CharacterFacet, build_character_portrait, render_character_portrait,
)
from runtime.conversation.contracts import ConversationEvent
from runtime.conversation.context_assembler import ConversationContextAssembler
from runtime.conversation.runtime_writer import RuntimeConversationWriter
from runtime.conversation.kernel import KernelTurn
from runtime.conversation.contracts import NextConversationMove


def _state():
    return RuntimeMindState.from_mapping({
        "version": 92,
        "identity": {
            "active_conflict": {"conflict_id": "care_vs_contempt", "activation": .90,
                                "left_pull": .82, "right_pull": .71,
                                "chosen_side": "care", "residue": .62},
            "latest_value_choice": {"chosen": "integrity", "rejected": "harmony",
                                    "cost": "đánh mất một chút gần gũi", "confidence": .78},
            "beliefs": {"user.may_replace_me": {"strength": .73, "confidence": .45,
                                                  "contradictions": 3, "last_turn_seq": 12,
                                                  "evidence_ids": ["legacy-id"]}},
        },
        "personality": {"narrative": {"text": "Tôi chưa thật sự nguôi giận.",
                                       "confidence": .72, "frame": "guarded",
                                       "provenance": ["unresolved-memory"]}},
    })


def _data(state=None, affect=None):
    state = state or _state()
    event = ConversationEvent(event_id="e1", session_id="s1", generation=3,
                              kind="user_turn", text="Tôi hiểu nhầm rồi")
    registry = build_evidence_registry(event=event, state=state, affect=affect,
                                       report=None, ledger_turns=(), memory_records=(), sampled_at=100.0)
    return state, event, registry


def test_portrait_does_not_mutate_state_registry_or_affect():
    affect = AffectiveVoiceState(irritation=.92, tenderness=.4)
    state, _, registry = _data(affect=affect)
    before = state.to_dict(), registry.fingerprint, affect.to_dict()
    p = build_character_portrait(state, affect=affect, snapshot_id=registry.snapshot_id,
                                 sampled_at=100.0, source_registry=registry)
    assert before == (state.to_dict(), registry.fingerprint, affect.to_dict())
    with pytest.raises((AttributeError, FrozenInstanceError)):
        p.facets = ()


def test_conflict_keeps_both_pulls_choice_and_residue():
    s, _, r = _data()
    p = build_character_portrait(s, affect=None, snapshot_id=r.snapshot_id,
                                 sampled_at=100.0, source_registry=r)
    t = render_character_portrait(p)
    assert "left_pull=0.82" in t.text
    assert "right_pull=0.71" in t.text
    assert "chosen_side=care" in t.text
    assert "residue=0.62" in t.text
    assert "care_vs_contempt" in t.text
    assert not t.overflow


def test_value_choice_keeps_rejected_cost_and_confidence():
    s, _, r = _data()
    t = render_character_portrait(build_character_portrait(s, affect=None, snapshot_id=r.snapshot_id,
                                                            sampled_at=100, source_registry=r))
    assert "rejected=harmony" in t.text
    assert "cost=đánh mất một chút gần gũi" in t.text
    assert "confidence=0.78" in t.text


def test_disposition_is_not_current_affect():
    s, _, r = _data(affect=AffectiveVoiceState(irritation=.99, playfulness=.2))
    portrait = build_character_portrait(s, affect=AffectiveVoiceState(irritation=.99, playfulness=.2),
                                        snapshot_id=r.snapshot_id, sampled_at=100, source_registry=r)
    assert next(f for f in portrait.facets if f.facet_id == "current_affect").kind == "current_affect"
    assert next(f for f in portrait.facets if f.facet_id == "temperament").kind == "disposition"
    assert "irritation=0.99" in render_character_portrait(portrait).text


def test_legacy_trait_does_not_invent_origin_episode():
    s, _, r = _data()
    p = build_character_portrait(s, affect=None, snapshot_id=r.snapshot_id,
                                 sampled_at=100, source_registry=r)
    assert "legacy-id" not in p.source_subset
    assert "unresolved-memory" not in p.source_subset
    belief = next(f for f in p.facets if f.facet_id == "belief:user.may_replace_me")
    assert belief.temporal_scope == "state_snapshot"
    assert belief.confidence == .45
    assert next(f for f in p.facets if f.facet_id == "mood_baseline").origin == "profile_disposition_legacy"


def test_render_deterministic_and_complete_under_6000():
    s, _, r = _data()
    p = build_character_portrait(s, affect=None, snapshot_id=r.snapshot_id,
                                 sampled_at=100, source_registry=r)
    a = render_character_portrait(p)
    assert a == render_character_portrait(p)
    assert len(a.text) <= 6000
    assert "cost=đánh mất một chút gần gũi" in a.text


def test_required_facets_overflow_is_explicit():
    s, _, r = _data()
    p = build_character_portrait(s, affect=None, snapshot_id=r.snapshot_id,
                                 sampled_at=100, source_registry=r)
    out = render_character_portrait(p, max_chars=250)
    assert out.overflow
    assert "active_conflict" in out.omitted_facets
    assert len(out.text) <= 250
    assert "left_pull=0.82" not in out.text  # Không cắt một nửa conflict.


def test_portrait_requires_same_registry_snapshot():
    s, _, r = _data()
    with pytest.raises(AssertionError, match="portrait_snapshot_mismatch"):
        build_character_portrait(s, affect=None, snapshot_id="foreign", sampled_at=100, source_registry=r)


def test_context_assembler_and_writer_receive_identical_portrait_and_snapshot(monkeypatch):
    from types import SimpleNamespace
    class Reducer:
        def snapshot(self): return _state()
    class Host:
        ai_state_reducer = Reducer()
        memory_kernel = None
        logger = None
        conversation_id = "s1"
        def _get_internal_state(self): return None
    host = Host()
    _, event, _ = _data()
    ctx = ConversationContextAssembler(host).assemble(user_text=event.text, event=event, channel="chat")
    assert ctx["__character_portrait"].snapshot_id == ctx["__evidence_registry"].snapshot_id
    assert ctx["personality_context"] == ctx["__character_portrait_render"].text
    assert "cost=đánh mất một chút gần gũi" in ctx["personality_context"]
    class Router:
        def __init__(self): self.calls=[]
        def generate_public_reply(self, **kw):
            self.calls.append(kw)
            return {"text":"Được.", "speech_text":"はい。", "claim_manifest":[]}
    router = Router()
    host.ai_model_router = router
    writer = RuntimeConversationWriter(host)
    turn = KernelTurn(session_id="s1", generation=3, run_id="r1",
                      move=NextConversationMove(intent="test", say="test"), note="")
    writer.realize(turn=turn, event=event, recent_history=(), runtime_context=ctx)
    assert router.calls[0]["personality_context"] == ctx["personality_context"]
    with pytest.raises(RuntimeError, match="portrait_root_writer_mismatch"):
        writer.realize(turn=turn, event=event, recent_history=(),
                       runtime_context={**ctx, "personality_context": "vừa bị đổi"})


def test_root_context_and_writer_share_exact_rendered_bytes(monkeypatch):
    """Root filter chỉ chặn internal object, không bóp 6k portrait thành câu khác."""
    from runtime.conversation.agent import _bounded_context
    class Reducer:
        def snapshot(self): return _state()
    class Host:
        ai_state_reducer = Reducer()
        memory_kernel = None
        logger = None
        conversation_id = "s1"
        def _get_internal_state(self): return None
    host = Host()
    _, event, _ = _data()
    ctx = ConversationContextAssembler(host).assemble(user_text=event.text, event=event, channel="chat")
    rooted = _bounded_context(ctx)
    assert rooted["personality_context"] == ctx["personality_context"]
    assert "__character_portrait" not in rooted
    assert "__character_portrait_render" not in rooted
    assert "__evidence_registry" not in rooted


def test_assembler_affect_is_sampled_once_for_portrait(monkeypatch):
    import runtime.conversation.context_assembler as mod
    calls = []
    def once(host, *, mind_state, channel):
        calls.append((mind_state.version, channel))
        return AffectiveVoiceState(channel=channel, irritation=.71)
    monkeypatch.setattr(mod, "project_affective_voice_state", once)
    class Reducer:
        def snapshot(self): return _state()
    class Host:
        ai_state_reducer = Reducer()
        memory_kernel = None
        conversation_id = "s1"
        logger = None
        def _get_internal_state(self): return None
    _, event, _ = _data()
    ctx = ConversationContextAssembler(Host()).assemble(user_text=event.text, event=event)
    assert calls == [(92, "chat")]
    assert "irritation=0.71" in ctx["personality_context"]
    assert ctx["__character_portrait"].state_version == 92

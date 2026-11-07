# -*- coding: utf-8 -*-
"""Hồi quy: Self đã có phải đến ĐÚNG Root/Writer, không sinh moral-agent."""
from __future__ import annotations

from types import SimpleNamespace

from runtime.ai_orchestrated.contracts import RuntimeMindState
from runtime.conversation.agent import _bounded_context
from runtime.conversation.context_assembler import ConversationContextAssembler
from runtime.conversation.contracts import ConversationEvent


def _state():
    # Belief về lòng tốt được cố ý đặt DƯỚI 5 belief có score cao hơn.
    # Trước bản vá, CharacterPortrait chỉ lấy 5 đầu nên world belief biến mất.
    return RuntimeMindState.from_mapping({
        "version": 42,
        "identity": {"beliefs": {
            "self.inner_process_should_remain_private": {"strength": .99, "confidence": .99},
            "self.competence_protects_my_worth": {"strength": .99, "confidence": .98},
            "self.being_understood_is_rare": {"strength": .98, "confidence": .98},
            "user.respects_independence": {"strength": .98, "confidence": .97},
            "user.tolerates_disagreement": {"strength": .96, "confidence": .97},
            "world.generic_kindness_is_often_empty": {"strength": .55, "confidence": .52},
        }},
    })


class _Subject:
    def __init__(self):
        self.calls = 0

    def self_view(self):
        self.calls += 1
        return {
            "revision_key": "self-owned-42",
            "facets": [
                {"facet_id": "self.value.honesty", "kind": "value",
                 "statement": "Không đánh đồng an ủi dễ dãi với sự chân thành.",
                 "confidence": .83, "stability": .70, "pressure": .82,
                 "evidence_refs": ["internal.decision.123", "external-fake-id"]},
                {"facet_id": "self.narrative.unsupported", "kind": "narrative",
                 "statement": "Bịa ra lịch sử không có nguồn.",
                 "confidence": .99, "stability": .99, "pressure": .99,
                 "evidence_refs": []},
            ],
        }


class _Host:
    def __init__(self, with_subject=True):
        state = _state()
        self.ai_state_reducer = SimpleNamespace(snapshot=lambda: state)
        self.subject_service = _Subject() if with_subject else None
        self.memory_kernel = None
        self.conversation_id = "s1"
        self.logger = None
        self.state = state


def _assemble(host, text, history=()):
    return ConversationContextAssembler(host).assemble(
        user_text=text,
        channel="chat",
        event=ConversationEvent(event_id="e1", session_id="s1", generation=3,
                                kind="user_turn", text=text),
        recent_history=history,
    )


def test_world_conviction_selected_even_if_not_top_five():
    host = _Host()
    before = host.state.to_dict()
    result = _assemble(host, "Eris, thế giới này có đáng sống không?")
    portrait = result["__character_portrait"]
    names = {f.facet_id for f in portrait.facets}
    assert "belief:world.generic_kindness_is_often_empty" in names
    assert "subject:self.value.honesty" in names
    assert "subject:self.narrative.unsupported" not in names
    assert host.state.to_dict() == before  # không mutate belief để làm nó mạnh hơn
    assert host.subject_service.calls == 1
    assert result["context_assembler"]["self_projection"]["subject_available"] is True
    assert "subject:self.value.honesty" in result["context_assembler"]["self_projection"]["subject_facets"]


def test_root_writer_read_exact_same_personality_and_no_unsupported_refs():
    result = _assemble(_Host(), "Những lời an ủi đạo đức giả thì sao?")
    rooted = _bounded_context(result)
    assert rooted["personality_context"] == result["personality_context"]
    assert rooted["personality_context"] == result["__character_portrait_render"].text
    assert "subject:self.value.honesty" in rooted["personality_context"]
    assert "internal.decision.123" not in result["__character_portrait"].source_subset
    assert "external-fake-id" not in result["__character_portrait"].source_subset
    assert "__character_portrait" not in rooted
    assert "__evidence_registry" not in rooted
    assert not result["__character_portrait_render"].overflow
    assert len(rooted["personality_context"]) <= 6000


def test_different_topic_does_not_force_worldview_into_top_five():
    result = _assemble(_Host(with_subject=False), "Cách tính đạo hàm logarit của x là gì?")
    names = {f.facet_id for f in result["__character_portrait"].facets}
    assert "belief:world.generic_kindness_is_often_empty" not in names


def test_follow_up_can_retrieve_previous_user_topic_without_copying_it_into_self():
    result = _assemble(_Host(), "Vậy còn điều đó?", history=(
        {"role": "user", "content": "Thế giới này có đáng sống không?"},
        {"role": "assistant", "content": "Tớ không chắc."},
    ))
    assert "belief:world.generic_kindness_is_often_empty" in {
        f.facet_id for f in result["__character_portrait"].facets
    }
    assert "Thế giới này có đáng sống không?" not in result["personality_context"]


def test_switching_to_math_does_not_reactivate_old_world_topic():
    result = _assemble(_Host(with_subject=False), "Cách tính đạo hàm logarit của x là gì?", history=(
        {"role": "user", "content": "Thế giới này có đáng sống không?"},
        {"role": "assistant", "content": "Tớ không chắc."},
    ))
    assert "belief:world.generic_kindness_is_often_empty" not in {
        f.facet_id for f in result["__character_portrait"].facets
    }


def test_subject_projection_failure_does_not_block_root_or_invent_self():
    class BadSubject:
        def self_view(self):
            raise RuntimeError("source unavailable")

    host = _Host()
    host.subject_service = BadSubject()
    result = _assemble(host, "Cậu nghĩ cuộc sống thế nào?")
    assert "belief:world.generic_kindness_is_often_empty" in {
        f.facet_id for f in result["__character_portrait"].facets
    }
    assert not any(f.facet_id.startswith("subject:") for f in result["__character_portrait"].facets)

from __future__ import annotations

from pathlib import Path

from memory.understanding.semantic_parser import _extract_gender_identity
from runtime.addressing_policy import clean_public_addressing
from runtime.ai_orchestrated.cognitive_blackboard import TypedCognitiveBlackboard
from runtime.prompt_context_builder import PromptContextBuilderMixin
from runtime.user_addressing_state import (
    UserAddressingStore,
    set_global_user_addressing_store,
)


class _PromptHost(PromptContextBuilderMixin):
    def __init__(self) -> None:
        self.chat_history = []
        self.memory_kernel = None
        self.logger = None


def test_explicit_gender_with_trailing_particle_updates_shared_state(tmp_path: Path) -> None:
    store = UserAddressingStore(tmp_path / "addressing.json")
    set_global_user_addressing_store(store)
    try:
        host = _PromptHost()
        host._append_chat_history("user", "trước tiên tui là nam nghen")

        state = store.snapshot()
        assert state.gender_identity == "male"
        assert state.user_vocative == "anh"
        assert state.forbidden_user_vocatives == ("chị",)
        assert "address_user_as: anh" in host._merge_conversation_context("")
        assert "tui là nam nghen" in host._merge_conversation_context("")
    finally:
        set_global_user_addressing_store(None)


def test_dynamic_surface_guard_repairs_wrong_vocative(tmp_path: Path) -> None:
    store = UserAddressingStore(tmp_path / "addressing.json")
    set_global_user_addressing_store(store)
    try:
        assert store.observe_user_text("tui là nam mà", turn_id="turn:1") is True
        output = clean_public_addressing(
            "Chị đang mở Facebook. Thế giới của chị đa diện thật; tôi nói với chị đó."
        )
        assert output == "Anh đang mở Facebook. Thế giới của anh đa diện thật; tôi nói với anh đó."
    finally:
        set_global_user_addressing_store(None)


def test_semantic_gender_fallback_accepts_vietnamese_particles() -> None:
    assert _extract_gender_identity("trước tiên tui là nam nghen") == "nam"
    assert _extract_gender_identity("tôi là nữ mà") == "nữ"


def test_blackboard_prefers_grounded_playing_media_over_app_inventory() -> None:
    snapshot = {
        "time": 1000.0,
        "telemetry": {
            "content_metadata": {
                "source": "accessibility",
                "privacy_level": "normal",
                "page_kind": "video",
                "title": "Lạc Trôi - Sơn Tùng M-TP",
                "creator": "Sơn Tùng M-TP",
                "media_state": "playing",
                "confidence": 0.94,
            }
        },
        "screen_context": {
            "fresh": True,
            "privacy_blocked": False,
            "meaningful_delta": True,
            "window_changed": True,
            "window_title": "Lạc Trôi - Sơn Tùng M-TP - YouTube",
            "changed_text": ["Lạc Trôi - Sơn Tùng M-TP"],
            "evidence_refs": ["screen:apps", "screen:music"],
        },
        "vision_grounding": {
            "recommendation": "speak",
            "evidence_strength": 0.94,
            "claims": [
                {
                    "text": "Facebook, Zalo, Google Meet, YouTube và Bandicam đang mở.",
                    "grounded": True,
                    "stale": False,
                    "confidence": 0.91,
                    "evidence_ids": ["screen:apps"],
                },
                {
                    "text": "Bài Lạc Trôi của Sơn Tùng M-TP đang phát trên YouTube.",
                    "grounded": True,
                    "stale": False,
                    "confidence": 0.91,
                    "evidence_ids": ["screen:music"],
                },
            ],
        },
        "conversation_baton": {
            "reply_expected": False,
            "assistant_may_continue": False,
            "thread_fresh": False,
            "thread_key": "none",
        },
        "activity_session": {"new_event_keys": ["window_changed"], "has_new_event": True},
        "self_context": {
            "has_public_seed": False,
            "strongest_seed_score": 0.0,
            "attention": {},
            "agenda": [],
            "private_mind_surface": [],
            "private_life_surface": [],
        },
        "discovery": {},
        "commentary_expectation": {},
        "engagement_context": {"active": False},
        "engagement_interaction": {},
        "latest_observation": {},
        "seconds_since_user": 120.0,
        "seconds_since_assistant": 120.0,
        "memory_context": "",
    }

    board = TypedCognitiveBlackboard.from_snapshot(snapshot, now=1000.0)
    media_anchor = next(anchor for anchor in board.anchors if anchor.kind == "media")

    assert media_anchor.anchor_id == "screen:music"
    assert "Lạc Trôi" in media_anchor.topic_seed
    assert media_anchor.metadata["selected_focus_kind"] == "media_playback"
    assert media_anchor.evidence_refs == ("screen:music",)

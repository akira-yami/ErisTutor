# -*- coding: utf-8 -*-
"""Regression cho các lỗi quan sát được trong run 2026-09-15 13:24.

Các test này cố ý bám vào hành vi public/runtime thay vì tên GitPilot cụ thể:
- Self-turn phải giữ được timestamp xuyên refactor bundle.
- Câu hỏi trạng thái một entity đang active phải bind vào Deep Environment.
- Gate fail-closed của direct chat không được biến một user turn thành im lặng.
- Environment event storm không được đánh thức autonomous loop liên tục.
"""
from __future__ import annotations

from types import SimpleNamespace


def test_current_environment_entity_status_binding_is_generic() -> None:
    from runtime.intent_model import matches_current_environment_entity_status

    labels = (
        "Visual Studio Code",
        "Code.exe",
        "GitPilot - agent panel",
        "debugging GitPilot integration",
    )
    assert matches_current_environment_entity_status("gitpilot đang bị sao thế", labels) is True
    assert matches_current_environment_entity_status("gitpilot ổn không", labels) is True
    assert matches_current_environment_entity_status("docker đang bị sao thế", labels) is False
    assert matches_current_environment_entity_status("gitpilot là gì", labels) is False


def test_fail_closed_direct_chat_has_a_public_repair_surface() -> None:
    from runtime.ai_orchestrated.orchestrator import _deterministic_fail_closed_chat_repair

    text = _deterministic_fail_closed_chat_repair(
        user_text="ý là sao bạn lại nghĩ về đặt ranh giới, bạn đang muốn nói gì",
        previous_assistant_text="Vạch ranh giới là nói rõ cái gì mình chấp nhận.",
        violation_codes=("CLAIM_DOMAIN_OUTSIDE_INTENT",),
    )

    assert text.strip()
    assert "không" in text.lower()
    assert "bịa" not in text.lower() or "không" in text.lower()


def test_environment_wake_is_suppressed_during_chat_and_short_bursts() -> None:
    from runtime.ai_orchestrated.semantic_environment_worker import should_emit_semantic_wake

    assert should_emit_semantic_wake(
        now_mono=10.0,
        last_wake_at=0.0,
        duplicate_signature=False,
        chat_mode=False,
        min_interval_seconds=1.2,
    ) is True
    assert should_emit_semantic_wake(
        now_mono=10.4,
        last_wake_at=10.0,
        duplicate_signature=False,
        chat_mode=False,
        min_interval_seconds=1.2,
    ) is False
    assert should_emit_semantic_wake(
        now_mono=20.0,
        last_wake_at=10.0,
        duplicate_signature=False,
        chat_mode=True,
        min_interval_seconds=1.2,
    ) is False
    assert should_emit_semantic_wake(
        now_mono=20.0,
        last_wake_at=10.0,
        duplicate_signature=True,
        chat_mode=False,
        min_interval_seconds=1.2,
    ) is False


def test_chat_cycle_binds_status_question_to_active_environment_entity() -> None:
    from runtime.chat_cycle import ChatCycleMixin

    class _Logger:
        def debug(self, *_args, **_kwargs) -> None:
            return None

    class _Host(ChatCycleMixin):
        logger = _Logger()

        def _chat_current_environment_labels(self, snapshot=None):
            return ("Visual Studio Code", "GitPilot agent panel", "debugging GitPilot integration")

    host = _Host()
    assert host._looks_like_deep_environment_request("gitpilot đang bị sao thế") is True
    assert host._looks_like_deep_environment_request("docker đang bị sao thế") is False

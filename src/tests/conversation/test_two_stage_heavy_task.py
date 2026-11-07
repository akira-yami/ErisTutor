from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from runtime.ai_orchestrated.orchestrator import _ensure_root_capability_registry
from runtime.conversation.contracts import (
    ConversationAfter,
    ConversationDecision,
    ConversationEvent,
    NextConversationMove,
)
from runtime.conversation.root_prompt_contract import ROOT_SYSTEM_CORE


def test_root_prompt_contains_two_stage_guidance() -> None:
    assert "Two-Stage Move" in ROOT_SYSTEM_CORE
    assert "after=continue_self" in ROOT_SYSTEM_CORE
    assert "continuation_due" in ROOT_SYSTEM_CORE


def test_ensure_root_capability_registry_registers_all_capabilities() -> None:
    host = SimpleNamespace(
        tutor_capability=None,
        tutor_executive=MagicMock(),
        work_capability=None,
        agent_harness=MagicMock(),
        research_capability=None,
        action_capability=None,
        action_executor=MagicMock(),
        learning_canvas_service=MagicMock(),
        logger=None,
    )
    registry = _ensure_root_capability_registry(host)
    names = registry.available_names()
    assert "canvas" in names
    assert "tutor" in names
    assert "work" in names
    assert "research" in names
    assert "action" in names

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_autonomous_pipeline_collects_before_claim_and_has_no_legacy_fallback() -> None:
    """V12: self-turn pipeline cũ phải biến mất hoàn toàn, không được giữ shim.

    Continuation/autonomous wake chỉ được quay lại ConversationKernel; test cũ
    từng soi implementation của self_turn_pipeline nên sau hard cutover phải đổi
    thành architecture guard chống hồi sinh module này.
    """
    assert not (ROOT / "runtime/ai_orchestrated/self_turn_pipeline.py").exists()
    continuation = _source("runtime/conversation/continuation.py")
    assert "class ContinuationLeaseScheduler" in continuation
    assert "speech_content" not in continuation


def test_runtime_scheduler_uses_temporal_mind_as_single_wake_authority() -> None:
    """V12: chỉ ContinuationLeaseScheduler được tạo continuation wake public."""
    assert not (ROOT / "runtime/ai_orchestrated/self_turn_pipeline.py").exists()
    source = _source("runtime/conversation/continuation.py")
    assert "class ContinuationLeaseScheduler" in source
    assert "claim_due" in source
    assert "schedule(" in source


def test_semantic_marker_router_is_deleted_not_shadowed():
    assert not (ROOT / "actions/router.py").exists()
    exports = _source("actions/__init__.py")
    bootstrap = _source("runtime/coordinator_bootstrap.py")
    assert "ActionRouter" not in exports
    assert "action_router" not in bootstrap


def test_capability_contracts_have_typed_effects_and_no_language_markers():
    registry = _source("actions/capability_registry.py")
    tools = _source("runtime/ai_orchestrated/executive_tools.py")
    environment = _source("runtime/environment_capabilities.py")
    cognition = _source("runtime/internal_cognitive_capabilities.py")
    assert "example_phrases" not in registry
    assert "capability_surface_keywords" not in registry
    assert "render_capability_registry_prompt" not in registry
    assert "_ACTION_ALIASES" not in registry
    assert "effect: str" in registry
    assert "resolve_intention" not in environment
    assert "resolve_intention" not in cognition
    assert "capability_input_schema" in tools


def test_deepseek_planners_cannot_select_capabilities():
    model_router = _source("runtime/ai_orchestrated/model_router.py")
    assert "capability authority reserved for runtime Capability Planner" in model_router
    assert "VALID_SYSTEM_ACTIONS" not in model_router
    assert 'data["tool_intent"] = ""' in model_router


def test_executive_uses_model_owned_shortlisting_not_registry_order_top_n():
    source = _source("runtime/ai_orchestrated/executive_cortex.py")
    tools = _source("runtime/ai_orchestrated/executive_tools.py")
    assert "capability_shortlist" in source
    assert "shortlist_capabilities" in source
    assert "filter_executive_tools" in source
    assert "select_tools_by_id" in source
    assert "max_items=8" not in source
    assert "selected[:" not in tools
    assert "capability_candidates" in source


def test_capability_arguments_are_typed_and_validated_before_legacy_adapter():
    """V12 giữ capability arguments ở capability layer, không đẩy vào orchestrator public."""
    cortex = _source("runtime/ai_orchestrated/executive_cortex.py")
    registry = _source("actions/capability_registry.py")
    orchestrator = _source("runtime/ai_orchestrated/orchestrator.py")
    assert "capability_arguments" in cortex
    assert "validate_arguments_against_schema" in cortex
    executor = _source("actions/executor.py")
    assert "execute_capability" in executor
    assert "encode_capability_arguments" in executor
    assert "encode_capability_arguments" not in cortex
    assert "_CAPABILITY_INPUT_CONTRACTS" in registry
    assert "capability_input" not in cortex
    # Root/orchestrator không còn parse typed capability arguments như public plan.
    assert "capability_arguments" not in orchestrator


def test_generic_liveness_fallback_cannot_be_resurrected_by_old_flag() -> None:
    """V12 retire social_initiative semantic lane thay vì shadow bằng feature flag."""
    assert not (ROOT / "runtime/ai_orchestrated/social_initiative.py").exists()
    assert "legacy_social_fallback_enabled" not in _source("runtime/architecture_flags.py")


def test_llm_parser_accepts_only_exact_capability_ids() -> None:
    service = _source("llm/service.py")
    assert "_SYSTEM_ACTION_ALIASES" not in service
    assert "volume_up" not in service
    assert "raw_action in VALID_SYSTEM_ACTIONS" in service

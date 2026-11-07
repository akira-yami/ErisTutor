from __future__ import annotations

from pathlib import Path

from actions.capability_registry import (
    capability_argument_mode,
    iter_public_capabilities,
    validate_registry_contract_coverage,
)
from runtime.ai_orchestrated.executive_cortex import EXECUTIVE_SCHEMA, ExecutiveCortex
from runtime.capability_claim_guard import guard_public_capability_claims


ROOT = Path(__file__).resolve().parents[1]


class _Owner:
    pass


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v30_capability_schema_has_no_conversation_policy_or_runtime_identity():
    """Planner chỉ được chọn capability; state hội thoại không đi qua model boundary."""
    properties = EXECUTIVE_SCHEMA["properties"]
    forbidden = {
        "reconsider_on",
        "review_after_seconds",
        "reply_baton",
        "trajectory",
        "subject_coalition_id",
        "subject_proposal_ref",
        "semantic_commitments",
        "cognitive_requests",
    }
    assert forbidden.isdisjoint(properties)


def test_v30_executive_object_has_no_hidden_temporal_policy_state():
    cortex = ExecutiveCortex(_Owner(), None, invoke_structured=lambda *_a, **_k: None)
    assert not hasattr(cortex, "state")
    assert not hasattr(cortex, "should_reconsider")
    assert not hasattr(cortex, "_update_state")


def test_v30_bootstrap_does_not_restore_or_persist_executive_trajectory():
    source = _source("runtime/ai_orchestrated/bootstrap.py")
    assert "executive_cortex_state" not in source
    assert "persist_executive_state" not in source
    start = source.index("ExecutiveCortex(")
    end = source.index(")", start) + 1
    constructor = source[start:end]
    assert "initial_state=" not in constructor
    assert "persist=" not in constructor
    assert "wall_clock=" not in constructor


def test_v30_temporal_mind_is_the_single_wake_gate() -> None:
    """V12 thay wake gate cũ bằng continuation lease cơ học duy nhất."""
    assert not (ROOT / "runtime/ai_orchestrated/self_turn_pipeline.py").exists()
    source = _source("runtime/conversation/continuation.py")
    assert "class ContinuationLeaseScheduler" in source
    assert "consecutive_self_wakes" in source
    assert "chain_started_at" in source


def test_v30_capability_planner_requests_use_explicit_sessionless_lane_names():
    cortex_source = _source("runtime/ai_orchestrated/executive_cortex.py")
    router_source = _source("runtime/ai_orchestrated/model_router.py")
    assert 'name=f"eris_capability_planner_{stage}"' in cortex_source
    assert '"capability_planner" in reason_key' in router_source
    assert 'lane = "capability_deliberation_required"' in router_source
    assert 'lane = "capability_deliberation"' in router_source


def test_capability_contract_coverage_is_complete_and_context_sensors_are_not_callable():
    valid, missing = validate_registry_contract_coverage()
    definitions = tuple(iter_public_capabilities())
    assert valid is True
    assert missing == ()
    # Không khóa cứng tổng số capability: registry được phép tăng mà không làm
    # test kiến trúc cũ báo lỗi giả. Boundary context_only mới là invariant.
    assert len(definitions) >= 46
    context_only = [item for item in definitions if item.exposure == "context_only"]
    assert len(context_only) == 8
    assert all(item.callable is False for item in context_only)
    assert all(capability_argument_mode(item.id) == "context_only" for item in context_only)


def test_capability_claim_guard_fails_closed_on_false_denial(monkeypatch):
    monkeypatch.setattr(
        "runtime.capability_claim_guard.runtime_capability_snapshot",
        lambda **_kwargs: {"statuses": {"read_visible_ui_text": {"available": True}}},
    )
    payload = {
        "should_speak": True,
        "text": "Tôi không thể nhìn thấy màn hình.",
        "speech_text": "Tôi không thể nhìn thấy màn hình.",
        "self_capability_claims": [
            {"capability_id": "read_visible_ui_text", "state": "unavailable", "basis": "model"}
        ],
    }
    result = guard_public_capability_claims(payload, owner=_Owner())
    assert result.allowed is False
    assert result.payload["should_speak"] is False
    assert result.payload["text"] == ""
    assert result.violations == ("false_unavailable_claim:read_visible_ui_text",)


def test_capability_claim_guard_accepts_exact_selected_capability(monkeypatch):
    monkeypatch.setattr(
        "runtime.capability_claim_guard.runtime_capability_snapshot",
        lambda **_kwargs: {"statuses": {}},
    )
    payload = {
        "should_speak": True,
        "text": "Em đọc được phần chữ đang hiện.",
        "self_capability_claims": [
            {"capability_id": "read_visible_ui_text", "state": "available", "basis": "verified_tool"}
        ],
    }
    result = guard_public_capability_claims(
        payload,
        owner=_Owner(),
        executive_directive={"status": "selected", "capability_id": "read_visible_ui_text"},
    )
    assert result.allowed is True
    assert result.payload["text"] == payload["text"]

# -*- coding: utf-8 -*-
"""Invariant V30: kiến trúc mới phải thay thế legacy, không chỉ đứng cạnh nó."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from runtime.ai_orchestrated.executive_cortex import EXECUTIVE_SCHEMA, ExecutiveCortex
from runtime.mind_loop import LocalNeuroCommitter, SubjectCommitPort
from runtime.subject_ingress import SubjectIngress


ROOT = Path(__file__).resolve().parents[1]


def test_subject_committer_stays_alive_without_model_client() -> None:
    """Mất provider chỉ làm mất deliberator/tool planner, không làm mất Neuro authority."""
    cortex = ExecutiveCortex(SimpleNamespace(action_executor=None), None, invoke_structured=lambda *_a, **_k: None)
    assert cortex.enabled is True
    assert cortex.neuro_deliberator_enabled is False
    assert cortex.capability_planner_enabled is False


def test_capability_model_schema_cannot_select_runtime_social_actions() -> None:
    """Capability Planner không được có cửa schema để quay lại làm Executive xã hội."""
    actions = set(EXECUTIVE_SCHEMA["properties"]["action"]["enum"])
    assert "select_affordance" not in actions
    assert "body_only" not in actions
    assert actions == {
        "silence",
        "discover_capabilities",
        "shortlist_capabilities",
        "invoke_capability",
        "speak_from_capability",
        "select_capability",
    }


def test_capability_stage_rejects_cross_stage_action() -> None:
    """Action đúng schema nhưng sai stage vẫn phải bị runtime từ chối."""
    def fake_structured(*_args, **_kwargs):
        return {
            "action": "discover_capabilities",  # hợp schema, nhưng sai quyền của stage=result
            "affordance_id": "",
            "semantic_target": "",
            "speech_act": "",
            "tone": "",
            "capability_goal": "",
            "required_effect": "",
            "maximum_risk": "low",
            "capability_domain": "",
            "capability_ids": [],
            "capability_id": "",
            "capability_arguments": {},
            "reason": "cross-stage attempt",
            "confidence": 0.8,
        }

    cortex = ExecutiveCortex(SimpleNamespace(action_executor=None), object(), invoke_structured=fake_structured)
    assert cortex._invoke(stage="result", payload={}, priority=1, max_tokens=64) is None


def test_subject_ingress_no_longer_exposes_frame_or_outcome_legacy_adapters() -> None:
    for name in (
        "prepare_user_turn",
        "prepare_autonomous_turn",
        "complete_autonomous_turn",
        "finalize",
        "abandon",
    ):
        assert not hasattr(SubjectIngress, name)


def test_mind_loop_contract_uses_commit_language_not_legacy_executive_types() -> None:
    assert SubjectCommitPort is not None
    assert LocalNeuroCommitter is not None
    source = (ROOT / "runtime" / "mind_loop.py").read_text(encoding="utf-8")
    assert "SubjectExecutivePort" not in source
    assert "FallbackSubjectExecutive" not in source
    assert "FallbackSubjectCommitter" not in source


def test_retired_legacy_markers_do_not_reappear_in_active_runtime() -> None:
    markers = (
        "ERIS_NEURO_CONTROL_ENABLED",
        "SUBJECT_COMMIT_SCHEMA",
        "subject_commit_from_outcome",
        "executive_cortex_state",
        "rebound stale coalition",
        "rebound stale proposal",
        "may_release_optional_reply_baton",
        "class ExecutiveState",
        "class ExecutiveEvent",
        "class ReviewCondition",
    )
    roots = [ROOT / "runtime", ROOT / "cortex"]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for root in roots
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    for marker in markers:
        assert marker not in text, marker

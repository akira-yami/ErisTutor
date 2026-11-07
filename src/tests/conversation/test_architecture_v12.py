# -*- coding: utf-8 -*-
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[2]


def test_v12_session_state_has_no_retired_semantic_fields():
    text = (ROOT / "runtime/conversation/contracts.py").read_text(encoding="utf-8")
    session_block = text.split("class ConversationSessionState", 1)[1].split("class ContinuationLease", 1)[0]
    for symbol in ("planned_threads", "desired_shape", "continuation_feel", "verbal_bridge_hint"):
        assert symbol not in session_block


def test_root_agent_schema_is_small():
    text = (ROOT / "runtime/conversation/agent.py").read_text(encoding="utf-8")
    schema = text.split("NEXT_MOVE_SCHEMA", 1)[1].split("class RootAgent", 1)[0]
    for retired in ("public_beats", "speech_plan", "turn_projection", "max_sentences", "desired_shape"):
        assert retired not in schema
    for required in ("intent", "say", "delivery", "after"):
        assert required in schema


def test_production_bootstrap_uses_conversation_kernel():
    bootstrap = (ROOT / "runtime/conversation/bootstrap.py").read_text(encoding="utf-8")
    assert "def ensure_conversation_kernel" in bootstrap
    assert "ConversationKernel(" in bootstrap
    assert "RuntimeConversationWriter(host)" in bootstrap
    assert "RuntimeConversationDeliveryAdapter(host)" in bootstrap

    orchestrator = (ROOT / "runtime/ai_orchestrated/orchestrator.py").read_text(encoding="utf-8")
    public_run = orchestrator.split("    def run(", 1)[1].split("    def _run_v12(", 1)[0]
    assert "self._run_v12(" in public_run
    assert "self._run_unscoped(" not in public_run


def test_v7_v11_speech_architecture_is_absent():
    retired = (
        "SpeechPlanLedger", "SpeechPlanSnapshot", "TurnProjection", "TurnFloor",
        "DialogueBeat", "ConversationSpeechPlan", "SpeechContinuationMode",
        "TurnAgentRuntime", "V12AgentTurnRuntime",
    )
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "runtime").rglob("*.py")
    )
    for symbol in retired:
        assert symbol not in production

    assert not any((ROOT / "runtime" / "agent_loop").glob("*.py"))
    for module in (
        "runtime.turn_projection_runtime",
        "runtime.conversation.legacy_adapter",
        "runtime.ai_orchestrated.social_continuation",
        "runtime.ai_orchestrated.conversation_arc",
    ):
        assert importlib.util.find_spec(module) is None


def test_bootstrap_has_no_legacy_social_semantic_runtime():
    source = (ROOT / "runtime/ai_orchestrated/bootstrap.py").read_text(encoding="utf-8")

    for forbidden in (
        "SocialAgendaStore",
        "SocialInitiativeEngine",
        "social_execution",
        "social_agenda_state",
        "ai_social_agenda_store",
        "ai_social_agenda_selector",
    ):
        assert forbidden not in source


def test_public_writer_has_no_legacy_semantic_contract():
    source = (ROOT / "runtime/ai_orchestrated/model_router.py").read_text(encoding="utf-8")

    for forbidden in (
        "AGENT_TURN_LOOP_V10",
        "AGENT_TURN_LOOP_V11",
        "public_beats",
        "conversation_speech_plan",
        "turn_projection",
        "normalize_social_plan",
        '"social_plan"',
        "SOCIAL_INITIATIVE_SCHEMA",
        "propose_social_initiative",
        "eris_social_agenda_selector",
        "_surface_realizer_prompt",
        "build_surface_public_payload",
    ):
        assert forbidden not in source


def test_retired_self_turn_and_social_modules_are_absent():
    for module in (
        "runtime.ai_orchestrated.self_turn_pipeline",
        "runtime.ai_orchestrated.social_agenda",
        "runtime.ai_orchestrated.social_execution",
        "runtime.ai_orchestrated.social_initiative",
        "runtime.ai_orchestrated.social_plan",
        "runtime.ai_orchestrated.social_rollout",
    ):
        assert importlib.util.find_spec(module) is None


def test_hard_cutover_has_no_retired_social_lanes_or_consumers():
    # Các label này từng tạo authority song song (arc/afterbeat/exposure/agenda).
    # Sau hard cutover chúng không được tồn tại trong production control path.
    paths = [
        ROOT / "runtime/user_control_runtime.py",
        ROOT / "runtime/ai_orchestrated/initiative.py",
        ROOT / "runtime/ai_orchestrated/semantic_contracts.py",
        ROOT / "runtime/nervous_system_v103_final.py",
        ROOT / "runtime/expression_urge_runtime.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for forbidden in (
        "ai_social_continuation_runtime",
        "ai_conversation_arc_runtime",
        '"conversation_arc"',
        '"afterbeat"',
        '"social_exposure"',
        '"social_exposure_recovery"',
        '"social_agenda"',
    ):
        assert forbidden not in source


def test_root_is_capability_on_demand_and_writer_is_wording_only():
    orchestrator = (ROOT / "runtime/ai_orchestrated/orchestrator.py").read_text(encoding="utf-8")
    root = (ROOT / "runtime/conversation/agent.py").read_text(encoding="utf-8")
    writer = (ROOT / "runtime/ai_orchestrated/model_router.py").read_text(encoding="utf-8")

    assert "RootCapabilityRegistry" in orchestrator
    assert "tutor_capability.advise" not in orchestrator
    assert '"use_capability"' in root
    assert '"set_note"' in root
    assert '"clear_note"' in root

    # Writer không được tự phát minh bridge/CTA/topic chỉ vì history trông hợp.
    for forbidden in (
        "ưu tiên một câu hỏi nhẹ",
        "tạo một cầu nối hội thoại tự nhiên",
        "giữ continuity từ history",
    ):
        assert forbidden not in writer


def test_force_speak_is_event_ingress_not_autonomous_flag():
    source = (ROOT / "runtime/user_control_runtime.py").read_text(encoding="utf-8")
    method = source.split("    def _handle_force_speak_request", 1)[1].split("    def _handle_stage_inbound_event", 1)[0]
    assert "run_force_speak" in method
    assert "_autonomous_force_speak_pending" not in method
    assert "_wake_autonomous_loop" not in method


def test_handoff_is_observer_only_not_a_semantic_authority():
    """Khóa hard-cut cuối: handoff chỉ còn causal/provenance observation."""
    paths = [
        ROOT / "runtime/conversation_handoff.py",
        ROOT / "runtime/ai_orchestrated/executive_cortex.py",
        ROOT / "runtime/life_horizon.py",
        ROOT / "runtime/semantic_authority_v95.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for forbidden in (
        "next_move_owner",
        "pending_shared_goal",
        "unresolved_intent",
        "conversation_move_hint",
        "repair_action_proposal",
        "handoff_authority_v71",
        "preserve_authoritative_conversation_handoff",
    ):
        assert forbidden not in source

    handoff = (ROOT / "runtime/conversation_handoff.py").read_text(encoding="utf-8")
    assert "evidence only; NOT a planner or obligation" in handoff
    assert "RootAgent alone decides whether/how to respond" in handoff


def test_conversation_bootstrap_wires_durable_mechanical_state():
    source = (ROOT / "runtime/conversation/bootstrap.py").read_text(encoding="utf-8")
    assert "atomic_store_from_env" in source
    assert "ConversationSessionStore(atomic_store=atomic_store" in source
    assert "ContinuationLeaseScheduler(atomic_store=atomic_store" in source
    assert "DeliveryReceiptLedger(atomic_store=atomic_store" in source


def test_intention_planner_compatibility_module_is_retired():
    bootstrap = (ROOT / "runtime/ai_orchestrated/bootstrap.py").read_text(encoding="utf-8")
    assert "from .planner import IntentionPlanner" not in bootstrap
    assert "planner = IntentionPlanner(" not in bootstrap
    assert "ai_intention_planner =" not in bootstrap
    assert not (ROOT / "runtime/ai_orchestrated/planner.py").exists()

    orchestrator = (ROOT / "runtime/ai_orchestrated/orchestrator.py").read_text(encoding="utf-8")
    # Dead compatibility helpers cũng bị cấm: để chúng nằm lại rất dễ bị caller
    # mới vô tình hồi sinh một planner/public authority song song.
    assert "_StaticIntentionPlanner" not in orchestrator
    assert "_typed_startup_persona_plan" not in orchestrator
    assert "_submit_local_startup_plan" not in orchestrator


def test_active_root_docs_no_longer_keep_v6_v7_v11_architecture_files():
    for name in (
        "ERIS_WRITER_SURFACE_IMMUTABLE_V6_VI.md",
        "ERIS_AGENT_DISCLOSURE_AUTHORITY_V7_VI.md",
        "README_V7_AGENT_OWNS_DISCLOSURE_VI.md",
        "ERIS_AGENT_TURN_PROJECTION_V11_VI.md",
        "ERIS_AGENT_SPEECH_PLAN_V8_VI.md",
        "README_V8_AGENT_SPEECH_PLAN_VI.md",
        "README_V6_NO_SPEECH_CAP_VI.md",
    ):
        assert not (ROOT / name).exists()


def test_foreground_history_excludes_current_user_turn_before_root_and_writer():
    source = (ROOT / "runtime/ai_orchestrated/orchestrator.py").read_text(encoding="utf-8")
    block = source.split("    def _run_v12(", 1)[1].split("class AIAutonomousOrchestrator", 1)[0]
    history_pos = block.index("history = tuple(")
    append_pos = block.index('append_history("user", str(user_text or ""))')
    assert history_pos < append_pos


def test_v12_uses_projection_context_assembler_not_dead_last_context_as_authority():
    source = (ROOT / "runtime/ai_orchestrated/orchestrator.py").read_text(encoding="utf-8")
    block = source.split("    def _run_v12(", 1)[1].split("class AIAutonomousOrchestrator", 1)[0]
    assert "ensure_conversation_context_assembler(host).assemble" in block
    for dead_read in (
        'getattr(host, "last_personality_context"',
        'getattr(host, "last_relationship_context"',
        'getattr(host, "last_memory_context"',
        'getattr(host, "last_grounding_context"',
    ):
        assert dead_read not in block

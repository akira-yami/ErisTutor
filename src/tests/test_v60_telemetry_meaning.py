# -*- coding: utf-8 -*-
"""Acceptance V60: telemetry structured phải đi được tới meaning -> initiative.

Các test cố ý dùng browser event attention=1 để chứng minh ta không gian lận bằng
cách nâng mọi Alt-Tab lên level cao. Chỉ chuỗi có nghĩa mới được phép tạo concern.
"""
from __future__ import annotations

from types import SimpleNamespace

from cortex.subject.contracts import NeedActivation, ObservationOrigin, WorldObservation
from cortex.subject.environment_meaning import EnvironmentMeaningDeriver, EnvironmentMeaningKind
from cortex.subject.initiative import InitiativeEngine, InitiativeState, MotiveKind
from cortex.subject.initiative_control import InitiativeArbiter, InitiativeControlAction
from runtime.ai_orchestrated.desktop_semantics import DesktopSemanticEvent, UserSituation
from runtime.subject_initiative_runtime import build_initiative_runtime_context, build_subject_initiative_affordances


def _event(kind: str, *, source: str, level: int, detail: str, ref: str, confidence: float = 0.92):
    return DesktopSemanticEvent(
        timestamp=1000.0,
        kind=kind,
        category="ui_state",
        semantic=level > 0,
        attention_level=level,
        confidence=confidence,
        novelty=0.7,
        importance=0.3,
        source=source,
        app="chrome.exe" if "browser" in kind else "Code.exe",
        object_ref="",
        detail=detail,
        evidence_refs=(ref,),
        privacy_level="semantic",
        event_key=ref,
    )


def _research_situation(*, busy: float = 0.08, interruption: float = 0.08, attention_state: str = "paused") -> UserSituation:
    return UserSituation(
        timestamp=1010.0,
        activity="documentation_research",
        task_hint="debugging memory replay continuity",
        phase="diagnosis",
        focused_app="chrome.exe",
        focused_process="chrome.exe",
        focused_object="GitHub issue: replay state lost after restart",
        last_action="browser_tab_activated",
        last_outcome="test_failed",
        attention_state=attention_state,
        interruption_cost=interruption,
        busy_score=busy,
        confidence=0.91,
        semantic_delta=False,   # raw browser tab vẫn level=1
        wake_recommended=False,
        max_attention_level=1,
        recent_events=(
            _event("test_failed", source="terminal_semantics", level=3, detail="test_memory_replay failed", ref="terminal:test_failed:1"),
            _event("browser_tab_activated", source="chromium_ia2", level=1, detail="GitHub issue: replay state lost after restart", ref="chromium:tab:1"),
        ),
        evidence_refs=("terminal:test_failed:1", "chromium:tab:1"),
    )


def test_v60_level1_browser_event_becomes_meaning_only_when_linked_to_failure():
    meanings = EnvironmentMeaningDeriver().derive(_research_situation())
    by_kind = {item.kind: item for item in meanings}
    assert EnvironmentMeaningKind.RESEARCH_AFTER_FAILURE in by_kind
    item = by_kind[EnvironmentMeaningKind.RESEARCH_AFTER_FAILURE]
    assert item.public_claim_safe is True
    assert item.source_grade == "structured"
    assert item.actionability >= 0.55
    assert "chromium:tab:1" in item.evidence_refs


def test_v60_plain_tab_switch_does_not_magically_create_help_desire():
    situation = UserSituation(
        timestamp=1000.0,
        activity="documentation_research",
        task_hint="unknown",
        phase="active",
        focused_object="News tab",
        attention_state="paused",
        busy_score=0.05,
        interruption_cost=0.05,
        confidence=0.9,
        max_attention_level=1,
        recent_events=(
            _event("browser_tab_activated", source="chromium_ia2", level=1, detail="News tab", ref="chromium:tab:news"),
        ),
        evidence_refs=("chromium:tab:news",),
    )
    meanings = EnvironmentMeaningDeriver().derive(situation)
    assert not any(item.kind in {EnvironmentMeaningKind.RESEARCH_AFTER_FAILURE, EnvironmentMeaningKind.WORK_FRICTION} for item in meanings)


def test_v60_ocr_only_never_authorizes_public_screen_claim():
    situation = UserSituation(
        timestamp=1000.0,
        activity="unknown",
        task_hint="unknown",
        phase="unknown",
        focused_object="",
        attention_state="paused",
        busy_score=0.0,
        interruption_cost=0.0,
        confidence=0.45,
        recent_events=(
            _event("screen_content_changed", source="ocr", level=2, detail="existential_desire.py", ref="ocr:frame:1", confidence=0.5),
        ),
        evidence_refs=("ocr:frame:1",),
    )
    meanings = EnvironmentMeaningDeriver().derive(situation)
    assert meanings
    self_item = next(item for item in meanings if item.kind is EnvironmentMeaningKind.SELF_RELEVANT_INSPECTION)
    assert self_item.public_claim_safe is False
    assert self_item.actionability <= 0.18


def test_v60_runtime_context_reads_real_user_situation_and_exposes_grounded_signal():
    host = SimpleNamespace(
        last_user_situation=_research_situation(),
        ai_state_reducer=None,
        private_mind_daemon=None,
        private_mind_system=None,
        private_mind_loop=None,
        subject_service=None,
        _startup_greeting_spoken_at=0.0,
        _temporal_last_user_turn_at=0.0,
        _temporal_last_assistant_turn_at=0.0,
        last_autonomous_spoken_at=0.0,
    )
    context = build_initiative_runtime_context(host)
    assert context["environment_meaning_kind"] == "research_after_failure"
    assert context["environment_contribution_signal"] >= 0.60
    assert context["environment_meaning_actionability"] >= 0.55
    assert "chromium:tab:1" in context["environment_evidence_refs"]


def _activation(need_id: str, pressure: float, opportunity: float = 0.8) -> NeedActivation:
    return NeedActivation(
        need_id=need_id,
        pressure=pressure,
        opportunity=opportunity,
        urgency=pressure,
        persistence=0.7,
        evidence_refs=(f"need:{need_id}",),
    )


def test_v60_environment_meaning_becomes_persistent_contribution_intention():
    seq = iter(range(100))
    engine = InitiativeEngine(lambda: f"v60-{next(seq)}")
    observation = WorldObservation(
        observation_id="obs:v60:environment",
        subject_id="eris",
        kind="autonomous_opportunity",
        created_at=1200.0,
        origin=ObservationOrigin.WORLD,
        context={"context_key": "autonomous"},
        evidence_refs=("chromium:tab:1", "terminal:test_failed:1"),
    )
    state = engine.update(
        InitiativeState(),
        activations=(
            _activation("connection", 0.55),
            _activation("competence", 0.72),
            _activation("coherence", 0.62),
            _activation("sovereignty", 0.10),
        ),
        relational={"opportunity": 0.84, "social_curiosity": 0.2, "evidence_refs": ("rel:v60",)},
        observation=observation,
        runtime_context={
            "user_model_uncertainty": 0.2,
            "environment_contribution_signal": 0.82,
            "environment_target_text": "sau failure user đang xem GitHub issue về replay state lost after restart",
            "environment_evidence_refs": ("chromium:tab:1", "terminal:test_failed:1"),
        },
    )
    assert any(
        item.motive_kind is MotiveKind.CONTRIBUTE and item.target == "environment_shared_task"
        for item in state.pending_intentions
    )


def _env_intention(**overrides):
    data = {
        "intention_id": "intention:environment:v60",
        "motive_kind": "contribute",
        "semantic_goal": "đóng góp một bước nhỏ dựa trên telemetry có provenance: user đang diagnosis sau test failure và mở GitHub issue về replay restart",
        "target": "environment_shared_task",
        "pressure": 0.82,
        "commitment_strength": 0.80,
        "created_at": 1000.0,
        "updated_at": 1000.0,
        "evidence_refs": ["evidence:environment:testv60", "terminal:test_failed:1", "chromium:tab:1"],
        "status": "pending",
    }
    data.update(overrides)
    return data


def _relational():
    return {
        "opportunity": 0.86,
        "social_curiosity": 0.18,
        "interaction_satiety": 0.05,
        "interruption_cost": 0.05,
    }


def _runtime(**overrides):
    data = {
        "seconds_since_last_user_turn": 180.0,
        "user_response_rate": 0.9,
        "social_refractory_cost": 0.0,
        "recent_unsolicited_speech": 0.0,
        "last_public_turn_unanswered": False,
        "startup_social_cooldown_active": False,
        "environment_meaning_actionability": 0.84,
    }
    data.update(overrides)
    return data


def test_v60_grounded_environment_intention_can_speak_when_user_paused():
    decision = InitiativeArbiter().decide(
        _env_intention(), relational=_relational(), runtime_context=_runtime(),
        now=1120.0, contentful=True,
    )
    assert decision.action is InitiativeControlAction.SPEAK


def test_v66_environment_intention_ignores_inferred_user_busy_as_boundary():
    decision = InitiativeArbiter().decide(
        _env_intention(), relational=_relational(), runtime_context=_runtime(),
        now=1120.0, contentful=True,
    )
    assert decision.action is InitiativeControlAction.SPEAK
    assert decision.reason == "self_owned_expression"


def test_v60_bridge_materializes_grounded_screen_comment_not_generic_checkin():
    class Subject:
        def initiative_view(self):
            return {"revision": 60, "pending_intentions": [_env_intention()]}
        def relational_view(self, *, now=None):
            return _relational()
        def note_initiative_control(self, *args, **kwargs):
            return None

    affordances = build_subject_initiative_affordances(
        Subject(), runtime_context=_runtime(), now=1120.0,
    )
    assert len(affordances) == 1
    candidate = affordances[0].candidate
    assert candidate.kind == "screen_comment"
    assert candidate.speech_act == "environment_help_offer"
    assert candidate.requires_grounding is True
    assert candidate.anchor_type == "environment"
    assert any(ref.startswith("evidence:environment:") for ref in candidate.evidence_refs)
    assert "chromium:tab:1" in candidate.evidence_refs


def test_v60_structured_self_inspection_can_become_restrained_self_expression():
    situation = UserSituation(
        timestamp=1010.0,
        activity="documentation_research",
        task_hint="reviewing Eris initiative",
        phase="active",
        focused_object="existential_desire.py - Eris",
        attention_state="paused",
        busy_score=0.05,
        interruption_cost=0.05,
        confidence=0.92,
        max_attention_level=1,
        recent_events=(
            _event("browser_tab_activated", source="chromium_ia2", level=1, detail="existential_desire.py - Eris", ref="chromium:self:1"),
        ),
        evidence_refs=("chromium:self:1",),
    )
    meanings = EnvironmentMeaningDeriver().derive(situation)
    item = next(item for item in meanings if item.kind is EnvironmentMeaningKind.SELF_RELEVANT_INSPECTION)
    assert item.public_claim_safe is True
    assert item.actionability >= 0.45


def test_v60_high_actionability_meaning_can_wake_temporal_mind_without_raw_level2_event():
    from runtime.temporal_mind import TemporalMind, TemporalSignal

    mind = TemporalMind()
    decision = mind.observe(
        (TemporalSignal("environment_meaning", 0.82, "perception_to_meaning_v60", revision=601),),
        now=2000.0,
        focus_key="debugging",
    )
    assert decision.wake is True
    assert decision.dominant_kind == "environment_meaning"


def test_v60_self_window_filtering_filters_godot_and_live2d():
    from telemetry.system import _is_self_window, _self_window_titles

    titles = _self_window_titles()
    assert "eris" in titles
    assert "eris live2d" in titles

    assert _is_self_window("Eris") is True
    assert _is_self_window("Eris Live2D") is True
    assert _is_self_window("eris live2d") is True
    assert _is_self_window("Eris: Settings") is True
    assert _is_self_window("Visual Studio Code") is False
    assert _is_self_window("Google Chrome") is False
    assert _is_self_window("Desktop") is False

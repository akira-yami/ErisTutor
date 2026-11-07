# -*- coding: utf-8 -*-
"""Regression tests for the heavy run on 2026-08-11."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace


def test_plain_nhac_lai_is_not_routed_as_music_or_screen() -> None:
    from screen.screen_evidence_store import ScreenEvidenceStore
    from screen.screen_relevance_router import ScreenRelevanceRouter

    router = ScreenRelevanceRouter(ScreenEvidenceStore())
    decision = router.route("T2 nhắc lại chủ đề chính của tôi trong một câu ngắn.")
    assert decision.mode == "none"
    assert decision.references_screen is False


def test_followup_projection_does_not_resurrect_closed_topic() -> None:
    from runtime.ai_orchestrated.discourse_state import DiscourseState
    from runtime.ai_orchestrated.followup_appraisal import FollowUpAppraisalLayer

    discourse = DiscourseState(
        topic_closed=True,
        reply_expected=True,
        assistant_may_continue=True,
        followup_reminder_due=True,
        may_start_new_topic=True,
    )
    result = FollowUpAppraisalLayer().evaluate(discourse, now=time.time())
    assert result.topic_closed is True
    assert result.reply_expected is False
    assert result.assistant_may_continue is False
    assert result.followup_state == "none"
    assert result.may_start_new_topic is False


def test_governor_blocks_unsolicited_social_candidate_behind_boundary(tmp_path) -> None:
    from runtime.ai_orchestrated.cognitive_blackboard import TypedCognitiveBlackboard
    from runtime.ai_orchestrated.initiative import InitiativeAction, InitiativeController
    from runtime.ai_orchestrated.social_candidate import SocialSpeechCandidate

    controller = InitiativeController(state_path=tmp_path / "initiative.json")
    candidate = SocialSpeechCandidate(
        decision="candidate",
        kind="random_chat",
        topic="Một ý nghĩ ngẫu nhiên.",
        speech_act="independent_topic_shift",
        anchor_type="idle",
        anchor_id="idle",
        confidence=0.8,
        relevance=0.8,
        source="test",
    )
    decision = controller.decide(
        TypedCognitiveBlackboard(now=100.0, explicit_social_boundary=True),
        candidate,
    )
    assert decision.action is InitiativeAction.NO_OP
    assert decision.reason == "explicit_social_boundary"


def test_situated_environment_claims_require_evidence() -> None:
    from runtime.source_grounding import SourceGroundingEngine

    engine = SourceGroundingEngine()
    report = SimpleNamespace(
        missing_evidence=["screen_evidence"],
        screen_evidence_present=False,
        screen_facts_are_stale=True,
        evidence_ids=[],
    )
    bad = (
        "Đêm nay yên tĩnh thật.",
        "Có tiếng gió nhẹ bên ngoài.",
        "Ánh đèn ở góc phòng hơi nhấp nháy.",
    )
    for text in bad:
        annotations = engine.annotate_claims(text, report=report)
        assert any(item.claim_status == "ungrounded" for item in annotations), text

    neutral = engine.annotate_claims("Thời gian trôi nhanh thật.", report=report)
    assert all(item.claim_status == "grounded" for item in neutral)


def test_forced_ocr_refresh_does_not_wait_forever_on_busy_capture_lock(monkeypatch) -> None:
    from screen.ocr_worker import IncrementalScreenTextWorker

    class Sink:
        def publish(self, snapshot):
            self.snapshot = snapshot

    worker = IncrementalScreenTextWorker(Sink())
    monkeypatch.setenv("ERIS_SCREEN_CHAT_REFRESH_LOCK_TIMEOUT_SEC", "0.01")
    assert worker._capture_lock.acquire(blocking=False)
    started = time.perf_counter()
    try:
        assert worker.refresh_now(force_ocr=True) is None
    finally:
        worker._capture_lock.release()
    assert time.perf_counter() - started < 0.2

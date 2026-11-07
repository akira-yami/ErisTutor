from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from llm.deepseek_workers import normalize_worker_role
from runtime.ai_orchestrated.cognitive_blackboard import TypedCognitiveBlackboard
from runtime.semantic_tutor_runtime import EventDrivenSemanticTutor, SceneChangeDetector, SceneIdentity


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    info = debug
    warning = debug


class _Host:
    logger = _Logger()

    def request_tutor_answer(self, *args, **kwargs):
        return ""


class _Client:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        content = self.outputs.pop(0)
        return {"choices": [{"message": {"content": content}}]}


def _packet_json(*, activity: str, foreground: str) -> str:
    return json.dumps(
        {
            "activity": activity,
            "foreground_summary": foreground,
            "facts": [f"Fact về {foreground}"],
            "inferences": ["User có thể đang chú ý tới foreground"],
            "unknowns": ["Chưa biết user có thích nội dung này không"],
            "conversation_options": [f"Bàn nhẹ về {foreground}"],
            "research_queries": [],
            "response_mode_scores": {
                "screen_analysis": 0.84,
                "screen_related_small_talk": 0.71,
                "deep_analysis": 0.40,
                "cross_context_connection": 0.32,
                "unrelated_small_talk": 0.05,
                "silence": 0.20,
            },
        },
        ensure_ascii=False,
    )


def _wait_ready(runtime: EventDrivenSemanticTutor, *, timeout: float = 2.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        context = runtime.planner_context()
        if context["packet"]:
            return context
        time.sleep(0.01)
    raise AssertionError("Semantic Tutor không commit packet trong thời hạn test")


def test_scene_detector_does_not_reanalyze_timer_like_title_changes() -> None:
    detector = SceneChangeDetector()
    first = SceneIdentity(app="chrome", window_title="YouTube 00:01", url="https://youtube.test/watch?v=1")
    assert detector.evaluate(first) == ("initial_scene",)

    # Cùng URL/tab/media; title chỉ đổi timer thì không được gọi model lại.
    second = SceneIdentity(app="chrome", window_title="YouTube 00:02", url="https://youtube.test/watch?v=1")
    assert detector.evaluate(second, significant_delta=False) == ()
    assert first.scene_id == second.scene_id

    # Bài mới là semantic invalidation thật.
    third = SceneIdentity(
        app="chrome",
        window_title="YouTube 00:03",
        url="https://youtube.test/watch?v=1",
        media_id="new-song",
    )
    assert detector.evaluate(third) == ("media_item_changed",)


def test_semantic_tutor_calls_model_only_for_meaningful_scene_changes(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_SEMANTIC_TUTOR_AUTO_SEARCH", "0")
    client = _Client(
        [
            _packet_json(activity="listening_music", foreground="Bài A"),
            _packet_json(activity="listening_music", foreground="Bài B"),
        ]
    )
    runtime = EventDrivenSemanticTutor(_Host(), client=client, logger=_Logger())
    try:
        telemetry_a = {
            "active_app": "chrome",
            "active_window_title": "YouTube",
            "browser": {"tab_id": "tab-1", "url": "https://youtube.test/watch?v=a"},
            "media": {"item_id": "song-a", "title": "Bài A", "state": "playing"},
        }
        assert runtime.observe(channel="chat", telemetry=telemetry_a) == ("initial_scene",)
        first = _wait_ready(runtime)
        assert first["packet"]["foreground_summary"] == "Bài A"
        assert len(client.calls) == 1

        # Poll/tick cùng scene chỉ cập nhật raw cache, không gọi DeepSeek lại.
        assert runtime.observe(channel="autonomous", telemetry=telemetry_a) == ()
        # Hỏi lại về cùng scene dùng packet nền, không phân tích sâu lần nữa.
        assert runtime.observe(channel="chat", telemetry=telemetry_a, explicit=True) == ()
        time.sleep(0.05)
        assert len(client.calls) == 1

        telemetry_b = {
            **telemetry_a,
            "media": {"item_id": "song-b", "title": "Bài B", "state": "playing"},
        }
        assert "media_item_changed" in runtime.observe(channel="autonomous", telemetry=telemetry_b)
        second = _wait_ready(runtime)
        assert second["packet"]["foreground_summary"] == "Bài B"
        assert len(client.calls) == 2
    finally:
        runtime.close()


def test_chat_gets_facts_only_fallback_when_tutor_is_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_SEMANTIC_TUTOR_AUTO_SEARCH", "0")
    runtime = EventDrivenSemanticTutor(_Host(), client=None, logger=_Logger())
    try:
        runtime.observe(
            channel="chat",
            telemetry={
                "active_app": "chrome",
                "active_window_title": "Trang sản phẩm",
                "browser": {"url": "https://shop.test/product/1"},
                "content_metadata": {"title": "Áo khoác", "visible_text_sample": "399.000đ"},
            },
            explicit=True,
        )
        prompt = runtime.prompt_context(channel="chat", wait_ms=0)
        assert "facts_only" in prompt
        assert "Không suy đoán user muốn gì" in prompt
        assert runtime.planner_context()["packet"] == {}
    finally:
        runtime.close()


def _base_blackboard_snapshot(packet: dict) -> dict:
    return {
        "time": packet["generated_at"] + 1.0,
        "telemetry": {},
        "screen_context": {"fresh": True, "privacy_blocked": False, "meaningful_delta": False},
        "vision_grounding": {"recommendation": "silence", "claims": []},
        "shared_semantic_context": {"scene_id": packet["scene_id"], "packet": packet},
        "conversation_baton": {
            "reply_expected": False,
            "assistant_may_continue": False,
            "thread_fresh": False,
            "thread_key": "none",
        },
        "activity_session": {},
        "self_context": {
            "has_public_seed": False,
            "strongest_seed_score": 0.0,
            "attention": {},
            "agenda": [],
            "private_mind_surface": [],
            "private_life_surface": [],
        },
        "discovery": {},
        "commentary_expectation": {"due": True, "required": False},
        "engagement_context": {"active": False},
        "engagement_interaction": {},
        "latest_observation": {},
        "seconds_since_user": 80.0,
        "seconds_since_assistant": 80.0,
        "memory_context": "",
    }


def test_blackboard_promotes_shared_semantic_packet_over_raw_ocr() -> None:
    now = time.time()
    packet = {
        "scene_id": "scene-product",
        "version": 3,
        "generated_at": now,
        "expires_at": now + 900,
        "status": "ready",
        "activity": "viewing_product",
        "foreground_summary": "Áo khoác denim giá 399.000đ",
        "facts": ["Giá hiện tại hiển thị là 399.000đ"],
        "inferences": ["User có thể đang cân nhắc sản phẩm"],
        "unknowns": ["Chưa biết user muốn mua hay chỉ xem"],
        "conversation_options": ["Bàn về giá và form áo"],
        "response_mode_scores": {"screen_analysis": 0.9, "unrelated_small_talk": 0.05},
    }
    board = TypedCognitiveBlackboard.from_snapshot(_base_blackboard_snapshot(packet), now=now + 1)

    anchor = board.strongest_anchor()
    assert anchor is not None
    assert anchor.kind == "semantic_scene"
    assert anchor.anchor_id == "scene-product"
    assert anchor.evidence_refs == ("semantic:scene-product:fact:0",)
    assert board.public_view()["shared_semantic_context"]["foreground"].startswith("Áo khoác")


def test_semantic_scene_does_not_gain_public_speech_without_social_plan_candidate() -> None:
    now = time.time()
    packet = {
        "scene_id": "scene-music",
        "version": 1,
        "generated_at": now,
        "expires_at": now + 900,
        "status": "ready",
        "activity": "listening_music",
        "foreground_summary": "Bài Lạc Trôi đang phát",
        "facts": ["Bài Lạc Trôi đang phát"],
        "inferences": [],
        "unknowns": ["Chưa biết user có thích bài này không"],
        "conversation_options": ["Bình luận nhẹ về bài đang phát"],
        "response_mode_scores": {"screen_related_small_talk": 0.88, "silence": 0.2},
    }
    board = TypedCognitiveBlackboard.from_snapshot(_base_blackboard_snapshot(packet), now=now + 1)
    from runtime.ai_orchestrated.initiative import InitiativeAction, InitiativeController

    decision = InitiativeController().decide(board, None)
    assert decision.action is InitiativeAction.NO_OP
    assert decision.reason == "no_speech_candidate"

def test_tutor_worker_role_is_retired() -> None:
    with pytest.raises(ValueError):
        normalize_worker_role("tutor")

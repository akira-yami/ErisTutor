from __future__ import annotations

import time
from types import SimpleNamespace

from runtime.chat_cycle import ChatCycleMixin
from runtime.chess import ChessCompanionService, Position
from runtime.engagements import EngagementRuntime
from screen.evidence_models import ActiveWindowInfo, ScreenTextBlock, ScreenTextSnapshot
from screen.screen_evidence_store import ScreenEvidenceStore
from screen.screen_relevance_router import ScreenRelevanceRouter


class _Logger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None

    def debug(self, *_args, **_kwargs) -> None:
        return None


class _Host(ChatCycleMixin):
    pass


def _store_with_blocks(*blocks: ScreenTextBlock) -> ScreenEvidenceStore:
    store = ScreenEvidenceStore()
    store.publish(
        ScreenTextSnapshot(
            window=ActiveWindowInfo(title="Test Window", hwnd=7, process_name="test.exe"),
            blocks=tuple(blocks),
            captured_at=time.time(),
            revision=3,
            backend="rapidocr",
        )
    )
    return store


def test_ambient_ocr_is_injected_even_when_router_mode_is_none() -> None:
    now = time.time()
    block = ScreenTextBlock(
        block_id="ambient-title",
        text="Project Eris - runtime/chat_cycle.py",
        source="ocr",
        confidence=0.91,
        window_title="Test Window",
        captured_at=now,
    )
    store = _store_with_blocks(block)
    host = _Host()
    host.logger = _Logger()
    host.screen_evidence_store = store
    host.screen_relevance_router = ScreenRelevanceRouter(store)
    host.screen_text_worker = None
    host.chess_bridge_service = None
    host._chat_screen_route_decision = host.screen_relevance_router.route("xin chào")
    host._chat_screen_text_evidence = ()

    report = host._build_chat_source_grounding_report(
        user_text="xin chào",
        frame=None,
        snapshot=SimpleNamespace(),
        memory_resonance=None,
        screen_image_data_uri="",
    )

    assert report.primary_source == "user_text"
    assert any("runtime/chat_cycle.py" in item for item in report.screen_facts_preview)
    assert any(item.source == "current_screen" and item.role == "ambient" for item in report.evidence_items)


def test_ambiguous_ocr_queues_vision_for_next_turn(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_CHAT_MAIN_VISION", "0")
    monkeypatch.setenv("ERIS_SCREEN_OCR_ENABLED", "1")
    store = _store_with_blocks()
    captures: list[str] = []

    class Host(_Host):
        def _refresh_screen_facts_from_data_uri(self, **_kwargs):
            self.last_vision_grounding_report = SimpleNamespace(
                grounded_claims=("visible test window",),
                recommendation="speak",
                source="test-vision",
                stale=False,
                to_evidence_items=lambda grounded_only=True: ({
                    "id": "vision:test",
                    "source": "vision",
                    "claim": "visible test window",
                    "confidence": 0.9,
                    "grounded": True,
                    "stale": False,
                    "tags": ["vision", "screen"],
                },),
            )
            return ("visible test window",)

    host = Host()
    host.logger = _Logger()
    host.screen_evidence_store = store
    host.screen_relevance_router = ScreenRelevanceRouter(store)
    host.screen_text_worker = None
    host.chess_bridge_service = None
    host.reply_context = SimpleNamespace(previous_public_turn=None)
    host.telemetry_service = SimpleNamespace(
        capture_screen_frame_data_uri=lambda: captures.append("capture") or "data:image/png;base64,AAAA"
    )
    host._chat_snapshot_for_vision = SimpleNamespace()

    first = host._capture_screen_frame_for_chat("xem giúp màn hình")
    assert first == ""
    assert captures == []
    assert host._chat_pending_vision_escalation.pending is True

    second = host._capture_screen_frame_for_chat("rồi sao")
    assert second == "data:image/png;base64,AAAA"
    assert captures == ["capture"]
    assert host._chat_pending_vision_escalation.pending is False
    assert host._chat_screen_capture_interpreted is True


def test_chess_bridge_is_primary_and_skips_screen_vision() -> None:
    clock_value = [20.0]
    runtime = EngagementRuntime(clock=lambda: clock_value[0])
    bridge = ChessCompanionService(runtime, analysis_depth=1, clock=lambda: clock_value[0])
    bridge.handle_snapshot({
        "platform": "chess.com",
        "game_id": "grounded-game",
        "board_fen": Position.initial().board_fen,
        "page_url": "https://www.chess.com/play/computer",
        "ply": 0,
        "captured_at": 20.0,
    })

    store = _store_with_blocks()
    host = _Host()
    host.logger = _Logger()
    host.screen_evidence_store = store
    host.screen_relevance_router = ScreenRelevanceRouter(store)
    host.screen_text_worker = None
    host.chess_bridge_service = bridge
    host.reply_context = SimpleNamespace(previous_public_turn=None)
    host.telemetry_service = SimpleNamespace(
        capture_screen_frame_data_uri=lambda: (_ for _ in ()).throw(
            AssertionError("fresh chess FEN must supersede screenshot vision")
        )
    )

    assert host._capture_screen_frame_for_chat("chỉ tui đi gì") == ""
    report = host._build_chat_source_grounding_report(
        user_text="chỉ tui đi gì",
        frame=None,
        snapshot=SimpleNamespace(),
        memory_resonance=None,
        screen_image_data_uri="",
    )

    assert report.primary_source == "chess_bridge"
    assert report.grounding_source == "chess_bridge"
    assert "chess_bridge" in report.allowed_sources
    assert any(Position.initial().board_fen in item for item in report.structured_facts_preview)
    assert any(item.source == "chess_bridge" and item.role == "primary" for item in report.evidence_items)


def test_chess_bridge_stays_ambient_on_unrelated_turn() -> None:
    runtime = EngagementRuntime(clock=lambda: 30.0)
    bridge = ChessCompanionService(runtime, analysis_depth=1, clock=lambda: 30.0)
    bridge.handle_snapshot({
        "platform": "chess.com",
        "game_id": "ambient-game",
        "board_fen": Position.initial().board_fen,
        "page_url": "https://www.chess.com/play/computer",
        "ply": 0,
        "captured_at": 30.0,
    })

    host = _Host()
    host.logger = _Logger()
    host.screen_evidence_store = _store_with_blocks()
    host.screen_relevance_router = ScreenRelevanceRouter(host.screen_evidence_store)
    host.screen_text_worker = None
    host.chess_bridge_service = bridge

    report = host._build_chat_source_grounding_report(
        user_text="hôm nay ăn gì",
        frame=None,
        snapshot=SimpleNamespace(),
        memory_resonance=None,
        screen_image_data_uri="",
    )

    assert report.primary_source == "user_text"
    assert any(item.source == "chess_bridge" and item.role == "ambient" for item in report.evidence_items)


def test_deep_environment_is_primary_for_current_activity_question() -> None:
    now = time.time()
    situation = SimpleNamespace(
        timestamp=now,
        activity="debugging",
        task_hint="debug shader in Godot",
        phase="diagnosis",
        focused_app="Godot Engine",
        focused_process="Godot_v4.5.exe",
        focused_object="main.tscn - Godot Engine",
        last_action="focus_changed",
        last_outcome="unknown",
        attention_state="focused",
        confidence=0.91,
    )
    worker = SimpleNamespace(
        latest_situation=situation,
        update_telemetry=lambda _payload: None,
    )
    host = _Host()
    host.logger = _Logger()
    host.semantic_environment_worker = worker
    host.last_user_situation = situation
    host.screen_evidence_store = _store_with_blocks()
    host.screen_relevance_router = ScreenRelevanceRouter(host.screen_evidence_store)
    host.screen_text_worker = None
    host.chess_bridge_service = None

    snapshot = SimpleNamespace(
        to_machine_context=lambda: {
            "active_app": "Godot Engine",
            "active_process": "Godot_v4.5.exe",
            "active_window_title": "main.tscn - Godot Engine",
        }
    )
    report = host._build_chat_source_grounding_report(
        user_text="ehm. hiện tại em biết tôi đang làm gì không",
        frame=None,
        snapshot=snapshot,
        memory_resonance=None,
        screen_image_data_uri="",
    )

    assert report.primary_source == "runtime_state"
    assert report.grounding_source == "runtime_state"
    assert report.preempt_recommended is False
    assert report.confidence_cap >= 0.78
    assert any("Deep Environment current focus" in item for item in report.runtime_facts_preview)
    assert any("debug shader in Godot" in item for item in report.runtime_facts_preview)
    assert "deep_environment_runtime_primary" in report.notes


def test_deep_environment_route_skips_ocr_and_screenshot_for_activity_question() -> None:
    now = time.time()
    situation = SimpleNamespace(
        timestamp=now,
        activity="editing",
        task_hint="editing Eris runtime",
        phase="implementation",
        focused_app="Visual Studio Code",
        focused_process="Code.exe",
        focused_object="runtime/chat_cycle.py",
        last_action="focus_changed",
        last_outcome="unknown",
        attention_state="focused",
        confidence=0.94,
    )

    class _Worker:
        latest_situation = situation

        def update_telemetry(self, _payload):
            return None

    class _FailOCR:
        def start(self):
            raise AssertionError("Deep Environment request must not start OCR")

        def refresh_now(self, *_args, **_kwargs):
            raise AssertionError("Deep Environment request must not refresh OCR")

    host = _Host()
    host.logger = _Logger()
    host.semantic_environment_worker = _Worker()
    host.last_user_situation = situation
    host.screen_evidence_store = _store_with_blocks()
    host.screen_relevance_router = ScreenRelevanceRouter(host.screen_evidence_store)
    host.screen_text_worker = _FailOCR()
    host.chess_bridge_service = None
    host.reply_context = SimpleNamespace(previous_public_turn=None)
    host._chat_snapshot_for_vision = SimpleNamespace(
        to_machine_context=lambda: {
            "active_app": "Visual Studio Code",
            "active_process": "Code.exe",
            "active_window_title": "runtime/chat_cycle.py - Eris",
        }
    )
    host.telemetry_service = SimpleNamespace(
        capture_screen_frame_data_uri=lambda: (_ for _ in ()).throw(
            AssertionError("Deep Environment request must not capture screenshot")
        )
    )

    assert host._capture_screen_frame_for_chat("em biết tôi đang làm gì không") == ""

from __future__ import annotations

from pathlib import Path

from runtime.chess import (
    BoardSnapshot,
    ChessAssistMode,
    ChessCompanionService,
    ChessGameTracker,
    ChessObservationPhase,
    Position,
    apply_move,
    classify_mode,
    legal_moves,
    move_san,
)
from runtime.engagements import EngagementRuntime
from runtime.policy_settings import ChessBridgeSettings
from runtime.runtime_settings_store import RuntimeSettingsStore


def _after_e4_board_fen() -> str:
    position = Position.initial()
    move = next(item for item in legal_moves(position) if item.uci == "e2e4")
    return apply_move(position, move).board_fen


def test_chess_engine_generates_legal_opening_and_infers_move() -> None:
    position = Position.initial()
    moves = legal_moves(position)
    assert len(moves) == 20
    e4 = next(item for item in moves if item.uci == "e2e4")
    assert move_san(position, e4) == "e4"
    assert apply_move(position, e4).board_fen == _after_e4_board_fen()


def test_mode_policy_allows_full_assistance_only_for_explicit_safe_modes() -> None:
    base = dict(platform="chess.com", game_id="g", board_fen=Position.initial().board_fen)
    assert classify_mode(BoardSnapshot(page_url="https://www.chess.com/analysis", **base)) == ChessAssistMode.ANALYSIS
    assert classify_mode(BoardSnapshot(page_url="https://www.chess.com/play/computer", **base)) == ChessAssistMode.BOT
    assert classify_mode(BoardSnapshot(page_url="https://www.chess.com/game/live/123", **base)) == ChessAssistMode.LIVE_RESTRICTED
    assert classify_mode(BoardSnapshot(page_url="https://www.chess.com/game/live/123", mode_override="private", **base)) == ChessAssistMode.PRIVATE


def test_tracker_full_assistance_for_live_mode() -> None:
    """Live pages now get the same full analysis as analysis/bot/private modes."""
    tracker = ChessGameTracker(depth=1, direct_move_rate=1.0)
    analysis = tracker.observe(BoardSnapshot(
        platform="chess.com",
        game_id="analysis-game",
        board_fen=Position.initial().board_fen,
        page_url="https://www.chess.com/analysis",
        ply=0,
    ))
    assert analysis.mode.full_assistance is True
    assert analysis.suggestion is not None
    assert analysis.hint_focus
    assert analysis.suggestion.uci in {move.uci for move in legal_moves(Position.initial())}

    # Live pages receive the same full assistance — no more restriction.
    live = tracker.observe(BoardSnapshot(
        platform="chess.com",
        game_id="live-game",
        board_fen=Position.initial().board_fen,
        page_url="https://www.chess.com/game/live/123",
        ply=0,
    ))
    assert live.mode == ChessAssistMode.LIVE_RESTRICTED
    assert live.mode.full_assistance is True  # live is now fully assisted
    assert live.evaluation_cp is not None
    assert live.hint_focus != ""
    assert live.suggestion is not None


def test_bridge_updates_durable_chess_engagement() -> None:
    runtime = EngagementRuntime(clock=lambda: 10.0)
    service = ChessCompanionService(runtime, analysis_depth=1, direct_move_rate=1.0, clock=lambda: 10.0)
    started = service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "abc",
        "board_fen": Position.initial().board_fen,
        "page_url": "https://www.chess.com/analysis",
        "ply": 0,
        "captured_at": 10.0,
    })
    # Snapshot đầu ở thế xuất phát chỉ có nghĩa là bridge đã thấy bàn.
    assert started.game_started is False
    assert started.phase == ChessObservationPhase.WAITING_FOR_START
    engagement = runtime.registry.foreground()
    assert engagement is not None
    assert engagement.kind == "chess"
    assert engagement.evidence_binding.resource_id == "chess:chess.com:abc"
    assert engagement.domain_state["chess_phase"] == "waiting_for_start"
    assert engagement.last_event_kind == "chess.board_detected"
    assert engagement.domain_state["full_assistance"] is True
    assert engagement.domain_state["suggested_move_uci"]

    moved = service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "abc",
        "board_fen": _after_e4_board_fen(),
        "page_url": "https://www.chess.com/analysis",
        "ply": 1,
        "captured_at": 11.0,
    })
    assert moved.last_move_uci == "e2e4"
    assert moved.last_move_san == "e4"
    assert moved.game_started is True
    assert moved.phase == ChessObservationPhase.ACTIVE
    assert runtime.registry.foreground().last_event_kind == "chess.move_played"


def test_bridge_exports_fresh_structured_grounding_evidence() -> None:
    now = [40.0]
    runtime = EngagementRuntime(clock=lambda: now[0])
    service = ChessCompanionService(runtime, analysis_depth=1, clock=lambda: now[0])
    service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "grounding-export",
        "board_fen": Position.initial().board_fen,
        "page_url": "https://www.chess.com/play/computer",
        "ply": 0,
        "captured_at": 40.0,
    })

    evidence = service.current_grounding_evidence(relevant_to_turn=True)
    assert len(evidence) == 1
    assert evidence[0]["source"] == "chess_bridge"
    assert evidence[0]["grounded"] is True
    assert evidence[0]["relevant_to_turn"] is True
    assert evidence[0]["board_fen"] == Position.initial().board_fen

    now[0] = 60.0
    assert service.current_grounding_evidence(max_age_seconds=8.0) == ()


def test_user_chess_task_is_reused_when_bridge_sees_board() -> None:
    """Không được tạo hai engagement: một cái hỏi bàn đâu, một cái tự bình luận."""

    runtime = EngagementRuntime(clock=lambda: 10.0)
    requested = runtime.observe_user_message("Eris xem tôi đánh cờ nhé", now=10.0)
    assert requested is not None
    assert requested.domain_state["chess_phase"] == "waiting_for_board"

    service = ChessCompanionService(runtime, analysis_depth=1, clock=lambda: 12.0)
    observation = service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "reuse-me",
        "board_fen": Position.initial().board_fen,
        "page_url": "https://www.chess.com/game/live/reuse-me",
        "ply": 0,
        "status": "ready",
        "captured_at": 12.0,
    })

    active = runtime.registry.active()
    assert len(active) == 1
    assert active[0].engagement_id == requested.engagement_id
    assert active[0].evidence_binding.resource_id == "chess:chess.com:reuse-me"
    assert active[0].domain_state["chess_phase"] == "waiting_for_start"
    assert observation.phase == ChessObservationPhase.WAITING_FOR_START


def test_waiting_chess_task_prompts_for_board_after_short_grace() -> None:
    runtime = EngagementRuntime(clock=lambda: 10.0)
    engagement = runtime.observe_user_message("xem tôi chơi cờ vua", now=10.0)
    assert engagement is not None

    decision = runtime.interaction_decision(
        now=19.0,
        seconds_since_user=9.0,
        seconds_since_assistant=999.0,
    )
    assert decision.due is True
    assert decision.engagement_id == engagement.engagement_id
    assert decision.reason == "engagement_event:engagement.started"
    context = runtime.prompt_context(now=19.0)
    assert "waiting_for_board" in context
    assert "opened the game" in context


def test_waiting_chess_task_asks_again_without_spamming() -> None:
    runtime = EngagementRuntime(clock=lambda: 10.0)
    engagement = runtime.observe_user_message("xem tôi chơi cờ vua", now=10.0)
    assert engagement is not None

    # Giả lập câu đáp đầu tiên của ERIS. Event engagement.started được xem là
    # đã giao, nên lần nhắc tiếp theo phải dựa vào cadence của pha chờ.
    runtime.observe_assistant_output(
        "Được, mở bàn đi rồi tôi xem.",
        now=11.0,
        engagement_id=engagement.engagement_id,
    )
    early = runtime.interaction_decision(
        now=45.0,
        seconds_since_user=35.0,
        seconds_since_assistant=34.0,
    )
    assert early.due is False

    due = runtime.interaction_decision(
        now=46.0,
        seconds_since_user=36.0,
        seconds_since_assistant=35.0,
    )
    assert due.due is True
    assert due.action.value == "ask"
    assert due.reason == "engagement_state_cadence"


def test_first_snapshot_midgame_is_recognized_as_started() -> None:
    tracker = ChessGameTracker(depth=1, direct_move_rate=0.0)
    observation = tracker.observe(BoardSnapshot(
        platform="chess.com",
        game_id="already-playing",
        board_fen=_after_e4_board_fen(),
        page_url="https://www.chess.com/game/live/123",
        ply=1,
        status="active",
    ))
    assert observation.game_started is True
    assert observation.phase == ChessObservationPhase.ACTIVE


def test_game_result_closes_temporary_chess_task() -> None:
    runtime = EngagementRuntime(clock=lambda: 20.0)
    service = ChessCompanionService(runtime, analysis_depth=1, clock=lambda: 20.0)
    service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "finished-game",
        "board_fen": _after_e4_board_fen(),
        "page_url": "https://www.chess.com/game/live/finished-game",
        "ply": 1,
        "status": "active",
        "captured_at": 20.0,
    })
    ended = service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "finished-game",
        "board_fen": _after_e4_board_fen(),
        "page_url": "https://www.chess.com/game/live/finished-game",
        "ply": 1,
        "result": "1-0",
        "status": "completed",
        "captured_at": 21.0,
    })
    engagement = runtime.registry.latest(kind="chess")
    assert ended.phase == ChessObservationPhase.COMPLETED
    assert engagement is not None
    assert engagement.lifecycle.value == "completed"
    assert engagement.domain_state["chess_phase"] == "completed"


def test_slang_starts_chess_engagement() -> None:
    runtime = EngagementRuntime()
    first = runtime.observe_user_message("Eris hỗ trợ pem cờ")
    assert first is not None and first.kind == "chess"
    second = EngagementRuntime().observe_user_message("pém thôi")
    assert second is not None and second.kind == "chess"


def test_settings_v13_migrate_chess_bridge_defaults(monkeypatch) -> None:
    migrated, changed = RuntimeSettingsStore._migrate_payload({"version": 13})
    assert changed is True
    assert migrated["version"] == 18
    assert migrated["chess_bridge"]["port"] == 8765

    monkeypatch.setenv("ERIS_CHESS_BRIDGE_PORT", "8877")
    monkeypatch.setenv("ERIS_CHESS_DIRECT_MOVE_RATE", "0.25")
    settings = RuntimeSettingsStore.from_env_compat()["chess_bridge"]
    assert settings["port"] == 8877
    assert settings["direct_move_rate"] == 0.25


def test_extension_bundle_is_present_and_local_only() -> None:
    root = Path(__file__).resolve().parents[1] / "integrations" / "chesscom_bridge"
    manifest = (root / "manifest.json").read_text(encoding="utf-8")
    content = (root / "content.js").read_text(encoding="utf-8")
    background = (root / "background.js").read_text(encoding="utf-8")
    popup = (root / "popup.js").read_text(encoding="utf-8")
    assert '"manifest_version": 3' in manifest
    assert "127.0.0.1:8765" in manifest
    assert "MutationObserver" in content
    assert 'status: result ? "completed" : (started ? "active" : "ready")' in content
    assert "game_started: started" in content
    assert "lastStatus" in background
    assert "lastBoardFen" in popup
    assert "eval(" not in content


def test_http_bridge_accepts_local_snapshot_with_token() -> None:
    import json
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    runtime = EngagementRuntime(clock=lambda: 20.0)
    service = ChessCompanionService(
        runtime,
        host="127.0.0.1",
        port=0,
        token="secret",
        analysis_depth=1,
        direct_move_rate=1.0,
        clock=lambda: 20.0,
    ).start()
    try:
        endpoint = f"http://127.0.0.1:{service.port}/v1/chess/snapshot"
        body = json.dumps({
            "platform": "chess.com",
            "game_id": "http-game",
            "board_fen": Position.initial().board_fen,
            "page_url": "https://www.chess.com/analysis",
            "ply": 0,
        }).encode("utf-8")
        try:
            urlopen(Request(endpoint, data=body, headers={"Content-Type": "application/json"}), timeout=2)
        except HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("bridge accepted a missing token")
        request = Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json", "X-Eris-Bridge-Token": "secret"},
        )
        with urlopen(request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"] is True
        assert payload["observation"]["mode"] == "analysis"
        diagnostics = service.diagnostics()
        assert diagnostics["last_snapshot"]["game_id"] == "http-game"
        assert diagnostics["last_snapshot"]["board_fen"] == Position.initial().board_fen
        assert runtime.registry.foreground().kind == "chess"
    finally:
        service.stop()

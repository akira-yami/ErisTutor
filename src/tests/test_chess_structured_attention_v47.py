from __future__ import annotations

from runtime.ai_orchestrated.cognitive_blackboard import TypedCognitiveBlackboard
from runtime.chess import (
    BoardSnapshot,
    ChessAssistMode,
    ChessCompanionService,
    ChessObservation,
    ChessObservationPhase,
    Position,
    apply_move,
    build_companion_signal,
    legal_moves,
)
from runtime.engagements import EngagementRuntime


def _play(position: Position, uci: str) -> Position:
    move = next(item for item in legal_moves(position) if item.uci == uci)
    return apply_move(position, move)


def _observation(**updates) -> ChessObservation:
    data = {
        "game_id": "g",
        "mode": ChessAssistMode.BOT,
        "fen": Position.initial().fen,
        "board_fen": Position.initial().board_fen,
        "side_to_move": "black",
        "move_number": 1,
        "user_side": "white",
        "mover_side": "white",
        "actor_is_user": True,
        "last_move_uci": "e2e4",
        "last_move_san": "e4",
        "evaluation_cp": 0,
        "evaluation_label": "equal",
        "evaluation_swing_cp": 0,
        "move_quality": "normal",
        "hint_focus": "develop_piece",
        "phase": ChessObservationPhase.ACTIVE,
        "board_detected": True,
        "game_started": False,
        "game_over": False,
        "confidence": 1.0,
    }
    data.update(updates)
    return ChessObservation(**data)


def test_spectator_mode_reacts_selectively_and_never_volunteers_moves() -> None:
    snapshot = BoardSnapshot(
        platform="chess.com",
        game_id="g",
        board_fen=Position.initial().board_fen,
        orientation="white",
        metadata={"player_side": "white"},
    )

    routine_opponent = build_companion_signal(
        _observation(
            mover_side="black",
            actor_is_user=False,
            last_move_uci="e7e5",
            last_move_san="e5",
            side_to_move="white",
            move_quality="normal",
        ),
        snapshot,
        {"companion_mode": "spectator", "user_side": "white"},
    )
    assert routine_opponent.actor == "opponent"
    assert routine_opponent.should_speak is False
    assert routine_opponent.should_wake is False
    assert routine_opponent.suggestions_allowed is False

    user_blunder = build_companion_signal(
        _observation(move_quality="blunder", evaluation_swing_cp=-420),
        snapshot,
        {"companion_mode": "spectator", "user_side": "white"},
    )
    assert user_blunder.actor == "user"
    assert user_blunder.should_speak is True
    assert user_blunder.speech_priority >= 0.94
    assert "Do not recommend a move" in user_blunder.commentary_focus


def test_extension_chess_focus_suppresses_ocr_and_generic_presence() -> None:
    snapshot = {
        "time": 100.0,
        "telemetry": {"content_metadata": {"title": "Random song", "media_state": "playing", "confidence": 0.9}},
        "screen_context": {
            "fresh": True,
            "privacy_blocked": False,
            "meaningful_delta": True,
            "window_changed": True,
            "window_title": "YouTube",
            "evidence_refs": ["screen:music"],
        },
        "vision_grounding": {
            "recommendation": "speak",
            "evidence_strength": 0.95,
            "claims": [{
                "text": "Một bài nhạc đang phát trên YouTube.",
                "grounded": True,
                "stale": False,
                "confidence": 0.95,
                "evidence_ids": ["screen:music"],
            }],
        },
        "conversation_baton": {"reply_expected": False, "assistant_may_continue": False, "thread_fresh": False},
        "activity_session": {"new_event_keys": ["window_changed"], "has_new_event": True},
        "self_context": {
            "has_public_seed": True,
            "strongest_seed_score": 0.9,
            "attention": {},
            "agenda": [{"id": "self:1", "topic_seed": "unrelated thought", "score": 0.9}],
            "private_mind_surface": [],
            "private_life_surface": [],
        },
        "discovery": {"ready_to_share": [{"id": "d1", "summary": "unrelated discovery"}]},
        "commentary_expectation": {"due": True, "required": True},
        "engagement_context": {
            "active": True,
            "foreground": {
                "kind": "chess",
                "focus": "foreground",
                "current_state_summary": "spectator chess; last=e4; actor=user",
                "current_state": {
                    "exclusive_focus": True,
                    "companion_mode": "spectator",
                    "speech_reason": "user_move_opens_game",
                    "commentary_focus": "React to the user's e4.",
                },
                "last_event_kind": "chess.move_played",
            },
        },
        "engagement_interaction": {
            "due": True,
            "required": False,
            "engagement_id": "chess:1",
            "engagement_kind": "chess",
            "reason": "engagement_event:chess.move_played",
            "evidence_refs": ["chess:g:1"],
        },
        "latest_observation": {},
        "seconds_since_user": 20.0,
        "seconds_since_assistant": 20.0,
        "memory_context": "",
    }

    board = TypedCognitiveBlackboard.from_snapshot(snapshot, now=100.0)
    assert board.engagement_exclusive_focus is True
    assert [anchor.kind for anchor in board.anchors] == ["task"]
    assert board.anchors[0].metadata["commentary_focus"] == "React to the user's e4."


def test_bridge_records_every_move_but_wakes_only_meaningful_beats() -> None:
    now = [10.0]
    wake_reasons: list[str] = []
    runtime = EngagementRuntime(clock=lambda: now[0])
    engagement = runtime.observe_user_message("em làm khán giả, xem tui chơi cờ", now=10.0)
    assert engagement is not None
    assert engagement.domain_state["companion_mode"] == "spectator"

    service = ChessCompanionService(
        runtime,
        analysis_depth=1,
        direct_move_rate=0.0,
        clock=lambda: now[0],
        wake_callback=wake_reasons.append,
    )
    initial = Position.initial()
    service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "selective-wake",
        "board_fen": initial.board_fen,
        "page_url": "https://www.chess.com/play/computer",
        "orientation": "white",
        "ply": 0,
        "status": "ready",
        "captured_at": now[0],
        "metadata": {"player_side": "white"},
    })
    assert wake_reasons

    now[0] = 11.0
    after_e4 = _play(initial, "e2e4")
    service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "selective-wake",
        "board_fen": after_e4.board_fen,
        "page_url": "https://www.chess.com/play/computer",
        "orientation": "white",
        "ply": 1,
        "status": "active",
        "captured_at": now[0],
        "metadata": {"player_side": "white", "game_started": True},
    })
    wakes_after_first_move = len(wake_reasons)
    assert wakes_after_first_move >= 2

    now[0] = 12.0
    after_e5 = _play(after_e4, "e7e5")
    service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "selective-wake",
        "board_fen": after_e5.board_fen,
        "page_url": "https://www.chess.com/play/computer",
        "orientation": "white",
        "ply": 2,
        "status": "active",
        "captured_at": now[0],
        "metadata": {"player_side": "white", "game_started": True},
    })

    assert len(wake_reasons) == wakes_after_first_move
    state = runtime.registry.foreground().domain_state
    assert state["last_move_san"] == "e5"
    assert state["actor"] == "opponent"
    assert state["should_speak"] is False
    assert state["preferred_interaction_action"] == "wait"


def test_mid_session_user_correction_switches_to_spectator_mode() -> None:
    runtime = EngagementRuntime(clock=lambda: 10.0)
    engagement = runtime.observe_user_message("chơi cờ nghen", now=10.0)
    assert engagement is not None
    assert engagement.domain_state["companion_mode"] == "companion"

    updated = runtime.observe_user_message("em làm khán giả cơ, chỉ xem tui chơi thôi", now=11.0)
    assert updated is not None
    assert updated.engagement_id == engagement.engagement_id
    assert updated.domain_state["companion_mode"] == "spectator"
    assert updated.domain_state["suggestions_allowed"] is False


def test_active_chess_does_not_generate_generic_clock_based_chatter() -> None:
    now = [10.0]
    runtime = EngagementRuntime(clock=lambda: now[0])
    engagement = runtime.observe_user_message("xem tui chơi cờ", now=10.0)
    assert engagement is not None

    # Move the engagement to active, then acknowledge that event so only the
    # generic clock could possibly trigger another line.
    runtime.emit_domain_event(
        "chess.game_started",
        payload={"phase": "active", "should_speak": True, "speech_priority": 0.8},
        engagement_id=engagement.engagement_id,
        now=11.0,
    )
    runtime.observe_assistant_output(
        "Tôi xem đây.",
        now=12.0,
        engagement_id=engagement.engagement_id,
    )
    current = runtime.registry.get(engagement.engagement_id)
    assert current is not None
    assert current.domain_state["chess_phase"] == "active"
    # The adapter is event-driven during active chess; a long quiet interval alone
    # must not summon unrelated companion chatter.
    decision = runtime.interaction_decision(
        now=500.0,
        seconds_since_user=490.0,
        seconds_since_assistant=489.0,
    )
    assert decision.due is False

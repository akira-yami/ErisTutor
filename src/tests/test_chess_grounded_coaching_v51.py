# -*- coding: utf-8 -*-
from __future__ import annotations

from runtime.chat_cycle import ChatCycleMixin
from runtime.chess import ChessCompanionService, Position, apply_move, legal_moves
from runtime.chess.surface import (
    ChessSurfaceState,
    authoritative_chess_answer_text,
    chess_surface_text_grounded,
    describe_chess_move_vi,
)
from runtime.engagements import EngagementRuntime


class _Logger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None

    def debug(self, *_args, **_kwargs) -> None:
        return None


class _Host(ChatCycleMixin):
    pass


def _play(position: Position, uci: str) -> Position:
    move = next(item for item in legal_moves(position) if item.uci == uci)
    return apply_move(position, move)


def test_active_unchanged_board_remains_chat_relevant_after_several_minutes() -> None:
    now = [10.0]
    runtime = EngagementRuntime(clock=lambda: now[0])
    runtime.observe_user_message("xem tui đánh cờ nghen", now=now[0])
    service = ChessCompanionService(
        runtime,
        analysis_depth=1,
        direct_move_rate=0.0,
        clock=lambda: now[0],
    )
    service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "long-think",
        "board_fen": Position.initial().board_fen,
        "page_url": "https://www.chess.com/play/computer",
        "orientation": "white",
        "ply": 0,
        "status": "active",
        "captured_at": now[0],
        "metadata": {"player_side": "white", "game_started": True},
    })

    now[0] = 610.0
    host = _Host()
    host.logger = _Logger()
    host.chess_bridge_service = service

    assert host._looks_like_chess_context_request("em nghĩ tui nên đánh gì, căng nhỉ") is True
    facts = host._chat_structured_grounding_facts("em nghĩ tui nên đánh gì, căng nhỉ")
    assert facts and facts[0]["relevant_to_turn"] is True
    assert facts[0]["suggested_move_uci"]


def test_direct_move_answer_uses_beginner_friendly_vietnamese_not_raw_uci() -> None:
    state = ChessSurfaceState(
        suggested_move_uci="f2e4",
        suggested_move_san="Ne4",
        hint_focus="develop_piece",
    )

    text = authoritative_chess_answer_text(state, user_text="tui nên đi gì")

    assert text.startswith("Mã lên e4.")
    assert "f2e4" not in text
    assert describe_chess_move_vi(san="Bxf7+", uci="c4f7") == "Tượng ăn quân ở f7, chiếu vua"


def test_invented_raw_coordinate_is_rejected_by_typed_state_guard() -> None:
    state = ChessSurfaceState(
        last_move_uci="e7e5",
        suggested_move_uci="g1f3",
        suggested_move_san="Nf3",
    )

    assert chess_surface_text_grounded("Mã lên f3, tức g1f3.", state, suggestion_only=True) is True
    assert chess_surface_text_grounded("d2d4 cũng ổn.", state) is False
    assert chess_surface_text_grounded("Hay là mã lên e4.", state, suggestion_only=True) is False


def test_followed_public_suggestion_is_carried_into_next_position() -> None:
    now = [10.0]
    runtime = EngagementRuntime(clock=lambda: now[0])
    runtime.observe_user_message("chỉ tui đánh cờ", now=now[0])
    service = ChessCompanionService(
        runtime,
        analysis_depth=1,
        direct_move_rate=0.0,
        clock=lambda: now[0],
    )
    initial = Position.initial()
    service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "follow-through",
        "board_fen": initial.board_fen,
        "page_url": "https://www.chess.com/play/computer",
        "orientation": "white",
        "ply": 0,
        "status": "active",
        "captured_at": now[0],
        "metadata": {"player_side": "white", "game_started": True},
    })

    evidence = service.current_grounding_evidence(
        max_age_seconds=300.0,
        relevant_to_turn=True,
        request_move_suggestion=True,
    )[0]
    suggested_uci = evidence["state"]["suggested_move_uci"]
    suggested_san = evidence["state"]["suggested_move_san"]
    public_line = authoritative_chess_answer_text(evidence["state"], user_text="nên đi gì")
    assert service.record_public_suggestion(public_line, authoritative=True) is True

    now[0] = 11.0
    child = _play(initial, suggested_uci)
    observation = service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "follow-through",
        "board_fen": child.board_fen,
        "page_url": "https://www.chess.com/play/computer",
        "orientation": "white",
        "ply": 1,
        "status": "active",
        "captured_at": now[0],
        "metadata": {"player_side": "white", "game_started": True},
    })

    assert observation.user_followed_previous_suggestion is True
    assert observation.previous_suggested_move_uci == suggested_uci
    assert observation.previous_suggested_move_san == suggested_san
    assert observation.suggestion_outcome == "followed"
    surface = service.current_surface_state()
    assert surface["user_followed_previous_suggestion"] is True
    assert surface["suggestion_outcome"] == "followed"

    # Bot đi xong rồi user mới phản ứng thì causal trace vẫn phải còn. Đây là
    # đúng tình huống log đã làm Eris chối trách nhiệm một cách rất máy móc.
    now[0] = 12.0
    reply = _play(child, "e7e5")
    later = service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "follow-through",
        "board_fen": reply.board_fen,
        "page_url": "https://www.chess.com/play/computer",
        "orientation": "white",
        "ply": 2,
        "status": "active",
        "captured_at": now[0],
        "metadata": {"player_side": "white", "game_started": True},
    })
    assert later.user_followed_previous_suggestion is True
    assert later.previous_suggested_move_uci == suggested_uci
    assert service.current_surface_state()["suggestion_outcome"] == "followed"


def test_chess_complaint_does_not_trigger_hidden_on_demand_move_analysis() -> None:
    now = [20.0]
    runtime = EngagementRuntime(clock=lambda: now[0])
    runtime.observe_user_message("xem tui chơi cờ", now=now[0])
    service = ChessCompanionService(
        runtime,
        analysis_depth=1,
        direct_move_rate=0.0,
        clock=lambda: now[0],
    )
    service.handle_snapshot({
        "platform": "chess.com",
        "game_id": "no-hidden-hint",
        "board_fen": Position.initial().board_fen,
        "page_url": "https://www.chess.com/play/computer",
        "orientation": "white",
        "ply": 0,
        "status": "active",
        "captured_at": now[0],
        "metadata": {"player_side": "white", "game_started": True},
    })

    evidence = service.current_grounding_evidence(
        max_age_seconds=300.0,
        relevant_to_turn=True,
        request_move_suggestion=False,
    )[0]
    assert evidence["state"]["suggested_move_uci"] == ""

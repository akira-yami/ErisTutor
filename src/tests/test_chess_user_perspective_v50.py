# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import replace

from runtime.chess import BoardSnapshot, ChessGameTracker, Position, build_companion_signal
from runtime.chess.analyzer import ChessAnalysis
from runtime.chess.engine import evaluation_label
from runtime.chess.surface import ChessSurfaceState


class _FixedAnalyzer:
    """Analyzer giả để kiểm tra đúng hệ quy chiếu, không phụ thuộc Stockfish."""

    def __init__(self, score_cp_white: int) -> None:
        self.score_cp_white = int(score_cp_white)

    def analyze_position(self, position, *, top_n=3, on_demand=False):
        return ChessAnalysis(
            provider="fixed",
            score_cp=self.score_cp_white,
            evaluation_label=evaluation_label(self.score_cp_white),
            depth=12,
        )

    def close(self) -> None:
        return None

    def diagnostics(self):
        return {"provider": "fixed", "ready": True}


def _observe(*, score_cp_white: int, player_side: str):
    tracker = ChessGameTracker(depth=1, direct_move_rate=0.0, analyzer=_FixedAnalyzer(score_cp_white))
    try:
        return tracker.observe(
            BoardSnapshot(
                platform="chess.com",
                game_id=f"perspective-{player_side or 'unknown'}",
                board_fen=Position.initial().board_fen,
                page_url="https://www.chess.com/play/computer",
                orientation=player_side or "unknown",
                ply=0,
                metadata={"game_started": True, "player_side": player_side},
            )
        )
    finally:
        tracker.close()


def test_black_user_gets_inverted_public_evaluation() -> None:
    observation = _observe(score_cp_white=185, player_side="black")

    # Chuẩn engine/history vẫn theo Trắng.
    assert observation.evaluation_cp == 185
    assert observation.evaluation_cp_white == 185
    assert observation.evaluation_label_white == "clear_white"
    assert observation.advantage_side == "white"

    # Public wording phải hiểu người dùng cầm Đen đang bất lợi.
    assert observation.evaluation_cp_user == -185
    assert observation.evaluation_label_user == "clearly_worse"
    assert observation.user_position == "clearly_worse"


def test_black_user_gets_positive_score_when_black_is_better() -> None:
    observation = _observe(score_cp_white=-240, player_side="black")

    assert observation.advantage_side == "black"
    assert observation.evaluation_cp_user == 240
    assert observation.user_position == "clearly_better"


def test_white_user_keeps_same_sign() -> None:
    observation = _observe(score_cp_white=95, player_side="white")

    assert observation.evaluation_cp_white == 95
    assert observation.evaluation_cp_user == 95
    assert observation.user_position == "slightly_better"


def test_unknown_user_side_never_claims_user_advantage() -> None:
    observation = _observe(score_cp_white=-150, player_side="")

    assert observation.advantage_side == "black"
    assert observation.evaluation_cp_user is None
    assert observation.evaluation_label_user == "unknown"
    assert observation.user_position == "unknown"


def test_companion_prompt_carries_both_perspectives() -> None:
    observation = replace(
        _observe(score_cp_white=185, player_side="black"),
        game_started=False,
        last_move_uci="e2e4",
        last_move_san="e4",
        mover_side="white",
        actor_is_user=False,
    )
    signal = build_companion_signal(
        observation,
        BoardSnapshot(
            platform="chess.com",
            game_id="perspective-black",
            board_fen=Position.initial().board_fen,
            orientation="black",
            metadata={"player_side": "black"},
        ),
        {"companion_mode": "spectator", "user_side": "black"},
    )

    assert signal.user_side == "black"
    assert "evaluation_cp_white=185" in signal.commentary_focus
    assert "evaluation_cp_user=-185" in signal.commentary_focus
    assert "user_position=clearly_worse" in signal.commentary_focus
    payload = observation.to_event_payload()
    assert payload["evaluation_cp_white"] == 185
    assert payload["evaluation_cp_user"] == -185
    assert payload["advantage_side"] == "white"
    assert payload["user_position"] == "clearly_worse"


def test_surface_state_preserves_explicit_perspectives() -> None:
    state = ChessSurfaceState.from_mapping({
        "evaluation_cp": 185,
        "evaluation_label": "clear_white",
        "evaluation_cp_white": 185,
        "evaluation_label_white": "clear_white",
        "evaluation_cp_user": -185,
        "evaluation_label_user": "clearly_worse",
        "advantage_side": "white",
        "user_position": "clearly_worse",
        "user_side": "black",
        "board_fen": Position.initial().board_fen,
    })

    assert state.evaluation_cp_white == 185
    assert state.evaluation_cp_user == -185
    assert state.user_position == "clearly_worse"
    assert state.advantage_side == "white"

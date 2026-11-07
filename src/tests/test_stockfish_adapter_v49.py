# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

from runtime.chess import (
    BoardSnapshot,
    ChessGameTracker,
    HybridChessAnalyzer,
    LocalChessAnalyzer,
    Position,
    StockfishConfig,
    StockfishUCIAnalyzer,
)


import sys


def _fake_stockfish(tmp_path: Path) -> str:
    script = tmp_path / "stockfish-fake.py"
    script.write_text(
        """#!/usr/bin/env python3
import sys
for raw in sys.stdin:
    command = raw.strip()
    if command == 'uci':
        print('id name ERIS Fake Stockfish', flush=True)
        print('option name Threads type spin default 1 min 1 max 32', flush=True)
        print('option name Hash type spin default 16 min 1 max 4096', flush=True)
        print('option name MultiPV type spin default 1 min 1 max 8', flush=True)
        print('uciok', flush=True)
    elif command == 'isready':
        print('readyok', flush=True)
    elif command.startswith('go '):
        print('info depth 12 seldepth 18 multipv 1 score cp 34 nodes 1200 pv e2e4 e7e5 g1f3', flush=True)
        print('info depth 11 seldepth 16 multipv 2 score cp 21 nodes 900 pv d2d4 d7d5', flush=True)
        print('bestmove e2e4 ponder e7e5', flush=True)
    elif command == 'quit':
        break
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return f'"{sys.executable}" "{script}"'


def test_stockfish_uci_parses_multipv_and_provenance(tmp_path: Path) -> None:
    binary = _fake_stockfish(tmp_path)
    analyzer = StockfishUCIAnalyzer(
        StockfishConfig(
            path=str(binary),
            threads=1,
            hash_mb=32,
            multipv=2,
            movetime_ms=30,
            startup_timeout_seconds=1.0,
            analysis_timeout_seconds=1.0,
        )
    )
    try:
        result = analyzer.analyze_position(Position.initial(), top_n=2)
    finally:
        analyzer.close()

    assert result.provider == "stockfish"
    assert result.score_cp == 34
    assert result.depth == 12
    assert [item.move.uci for item in result.candidates] == ["e2e4", "d2d4"]
    assert result.candidates[0].pv[:2] == ("e2e4", "e7e5")


def test_stockfish_score_is_normalized_to_white_perspective(tmp_path: Path) -> None:
    binary = _fake_stockfish(tmp_path)
    analyzer = StockfishUCIAnalyzer(
        StockfishConfig(path=str(binary), multipv=1, movetime_ms=30, analysis_timeout_seconds=1.0)
    )
    # Fake engine luôn trả +34 theo góc nhìn bên tới lượt. Với Đen tới lượt,
    # contract chung phải đổi thành -34 theo góc nhìn Trắng.
    black_to_move = Position.from_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1")
    try:
        result = analyzer.analyze_position(black_to_move, top_n=1)
    finally:
        analyzer.close()
    assert result.score_cp == -34


def test_hybrid_falls_back_when_stockfish_is_not_connected() -> None:
    primary = StockfishUCIAnalyzer(StockfishConfig(path="/definitely/missing/stockfish"))
    hybrid = HybridChessAnalyzer(
        primary=primary if primary.available else None,
        fallback=LocalChessAnalyzer(depth=1),
    )
    result = hybrid.analyze_position(Position.initial(), top_n=1)
    assert result.provider == "local"
    assert result.candidates


def test_tracker_exposes_stockfish_metadata(tmp_path: Path) -> None:
    binary = _fake_stockfish(tmp_path)
    analyzer = StockfishUCIAnalyzer(
        StockfishConfig(path=str(binary), multipv=2, movetime_ms=30, analysis_timeout_seconds=1.0)
    )
    tracker = ChessGameTracker(depth=1, direct_move_rate=1.0, analyzer=analyzer)
    try:
        observation = tracker.observe(
            BoardSnapshot(
                platform="chess.com",
                game_id="bot-1",
                board_fen=Position.initial().board_fen,
                page_url="https://www.chess.com/play/computer",
                ply=0,
                metadata={"game_started": True, "player_side": "white"},
            )
        )
    finally:
        tracker.close()

    assert observation.analysis_provider == "stockfish"
    assert observation.analysis_depth == 12
    assert observation.principal_variation[:2] == ("e2e4", "e7e5")
    assert observation.suggestion is not None
    assert observation.suggestion.provider == "stockfish"
    assert observation.suggestion.uci == "e2e4"

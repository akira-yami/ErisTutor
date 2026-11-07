# -*- coding: utf-8 -*-
"""Chess companion bridge and lightweight analysis support."""
from .analyzer import AnalyzedMove, ChessAnalysis, HybridChessAnalyzer, LocalChessAnalyzer
from .stockfish_adapter import StockfishConfig, StockfishUCIAnalyzer, discover_stockfish_path
from .contracts import (
    BoardSnapshot,
    ChessAssistMode,
    ChessObservation,
    ChessObservationPhase,
    MoveSuggestion,
)
from .engine import Move, Position, analyze, apply_move, evaluate, legal_moves, move_san
from .companion_policy import ChessCompanionSignal, build_companion_signal, infer_companion_mode
from .mode_policy import classify_mode
from .service import ChessCompanionService
from .tracker import ChessGameTracker

__all__ = [
    "AnalyzedMove", "ChessAnalysis", "HybridChessAnalyzer", "LocalChessAnalyzer",
    "StockfishConfig", "StockfishUCIAnalyzer", "discover_stockfish_path",
    "BoardSnapshot", "ChessAssistMode", "ChessCompanionService", "ChessCompanionSignal", "ChessGameTracker",
    "ChessObservation", "ChessObservationPhase", "Move", "MoveSuggestion", "Position", "analyze", "apply_move",
    "build_companion_signal", "classify_mode", "evaluate", "infer_companion_mode", "legal_moves", "move_san",
]

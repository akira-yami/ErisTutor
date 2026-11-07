# -*- coding: utf-8 -*-
"""Stateful board tracker và analyzer cờ có thể thay nóng bằng Stockfish."""
from __future__ import annotations
from core.di import resolve_dependency

import hashlib
import threading
from dataclasses import dataclass

from .analyzer import ChessAnalysis, ChessAnalyzer, HybridChessAnalyzer, LocalChessAnalyzer
from .contracts import (
    BoardSnapshot,
    ChessObservation,
    ChessObservationPhase,
    MoveSuggestion,
)
from .engine import Position, apply_move, evaluation_label, in_check, infer_transition, move_san
from .mode_policy import classify_mode


def _user_position_label(score_cp_user: int | None) -> str:
    """Nhãn lợi thế theo góc nhìn người dùng, không gắn nhầm màu quân.

    Score dương nghĩa là user tốt hơn, score âm nghĩa là user xấu hơn. Khi
    chưa biết user cầm màu nào, trả ``unknown`` thay vì tự nhận một phía.
    """

    if score_cp_user is None:
        return "unknown"
    score = int(score_cp_user)
    absolute = abs(score)
    if absolute < 40:
        return "balanced"
    degree = (
        "slightly" if absolute < 120 else
        "clearly" if absolute < 300 else
        "winning" if absolute < 700 else
        "decisively"
    )
    relation = "better" if score > 0 else "worse"
    return f"{degree}_{relation}"


def _advantage_side(score_cp_white: int | None) -> str:
    """Bên đang lợi thế theo score chuẩn Trắng."""

    if score_cp_white is None or abs(int(score_cp_white)) < 40:
        return "equal"
    return "white" if int(score_cp_white) > 0 else "black"


@dataclass(slots=True)
class _GameState:
    position: Position
    seen: int = 0
    last_evaluation_cp: int | None = None
    phase: ChessObservationPhase = ChessObservationPhase.WAITING_FOR_START
    last_ply: int = 0
    # Chess.com /play/computer có thể giữ nguyên URL/game_id và còn gửi status
    # ``completed`` của ván trước trong vài snapshot đầu ván mới.
    ignore_stale_terminal_status: bool = False


class ChessGameTracker:
    def __init__(
        self,
        *,
        depth: int = 2,
        direct_move_rate: float = 0.18,
        analyzer: ChessAnalyzer | None = None,
    ) -> None:
        self.depth = max(1, min(4, int(depth)))
        self.direct_move_rate = max(0.0, min(1.0, float(direct_move_rate)))
        # Default vẫn là local để unit test và máy chưa có Stockfish không phụ
        # thuộc executable ngoài. Service runtime sẽ truyền Hybrid analyzer.
        self.analyzer: ChessAnalyzer = resolve_dependency(analyzer, lambda: LocalChessAnalyzer(depth=self.depth))
        self._games: dict[str, _GameState] = {}
        self._lock = threading.RLock()

    def observe(self, snapshot: BoardSnapshot) -> ChessObservation:
        if not snapshot.board_fen:
            raise ValueError("board_fen is required")
        mode = classify_mode(snapshot)
        key = f"{snapshot.platform}:{snapshot.game_id}"
        with self._lock:
            state = self._games.get(key)
            game_started = False
            last_move = None
            last_san = ""
            resynced = False
            mover = ""
            previous_score = state.last_evaluation_cp if state is not None else None

            # Trang chơi với máy thường tái sử dụng cùng game_id. Dấu hiệu chắc
            # nhất của ván mới là tracker đã COMPLETED nhưng ply quay lùi mạnh.
            reset_completed_game = bool(
                state is not None
                and state.phase is ChessObservationPhase.COMPLETED
                and state.position.board_fen != snapshot.board_fen
                and (snapshot.ply < state.last_ply or snapshot.ply <= 1)
            )
            if state is None or reset_completed_game:
                turn = "w" if snapshot.ply % 2 == 0 else "b"
                position = Position.from_board_fen(snapshot.board_fen, turn=turn, ply=snapshot.ply)
                phase = (
                    ChessObservationPhase.ACTIVE
                    if _snapshot_has_started(snapshot)
                    else ChessObservationPhase.WAITING_FOR_START
                )
                state = _GameState(
                    position=position,
                    seen=1,
                    phase=phase,
                    last_ply=snapshot.ply,
                    ignore_stale_terminal_status=reset_completed_game,
                )
                previous_score = None
                game_started = phase is ChessObservationPhase.ACTIVE
                self._games[key] = state
                resynced = reset_completed_game
            elif state.position.board_fen != snapshot.board_fen:
                last_move, child = infer_transition(state.position, snapshot.board_fen)
                if child is not None and last_move is not None:
                    mover = state.position.turn
                    last_san = move_san(state.position, last_move)
                    state.position = child
                else:
                    turn = "w" if snapshot.ply % 2 == 0 else "b"
                    state.position = Position.from_board_fen(snapshot.board_fen, turn=turn, ply=snapshot.ply)
                    resynced = True
                state.seen += 1

            # Snapshot đầu tiên chỉ chứng minh đã thấy bàn. Chỉ chuyển ACTIVE khi
            # có nước đi, đồng hồ chạy hoặc bridge gửi cờ game_started rõ ràng.
            if state.phase is ChessObservationPhase.WAITING_FOR_START:
                if last_move is not None or _snapshot_has_started(snapshot):
                    state.phase = ChessObservationPhase.ACTIVE
                    game_started = True
            state.last_ply = snapshot.ply
            position = state.position

            score = None
            label = ""
            evaluation_cp_user = None
            evaluation_label_user = "unknown"
            advantage_side = "unknown"
            swing = None
            quality = ""
            suggestion = None
            hint_focus = ""
            analysis_provider = "none"
            analysis_depth = 0
            analysis_nodes = 0
            analysis_elapsed_ms = 0
            analysis_degraded = False
            analysis_error = ""
            mate_in = None
            principal_variation: tuple[str, ...] = ()

            if mode.full_assistance:
                analysis = self.analyzer.analyze_position(position, top_n=3, on_demand=False)
                score = int(analysis.score_cp)
                label = analysis.evaluation_label
                analysis_provider = analysis.provider
                analysis_depth = analysis.depth
                analysis_nodes = analysis.nodes
                analysis_elapsed_ms = analysis.elapsed_ms
                analysis_degraded = analysis.degraded
                analysis_error = analysis.error
                mate_in = analysis.mate_in
                if analysis.candidates:
                    principal_variation = analysis.candidates[0].pv
                    hint_focus = _hint_focus(position, analysis.candidates[0].move)
                if previous_score is not None and last_move is not None and mover:
                    swing = score - previous_score if mover == "w" else previous_score - score
                    quality = _move_quality(swing)
                state.last_evaluation_cp = score

                if analysis.candidates and self._should_reveal_direct_move(snapshot, state.seen):
                    candidate = analysis.candidates[0]
                    suggestion = MoveSuggestion(
                        uci=candidate.move.uci,
                        san=move_san(position, candidate.move),
                        score_cp=candidate.score_cp,
                        depth=candidate.depth or analysis.depth,
                        provider=analysis.provider,
                        mate_in=candidate.mate_in,
                        pv=candidate.pv,
                    )

            result = snapshot.result
            terminal_status = snapshot.status in {"completed", "finished", "game_over", "checkmate", "draw"}
            if state.ignore_stale_terminal_status and not result:
                if terminal_status:
                    terminal_status = False
                else:
                    state.ignore_stale_terminal_status = False
            game_over = bool(result or terminal_status)
            if game_over:
                state.phase = ChessObservationPhase.COMPLETED

            metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else dict(snapshot.metadata or {})
            raw_user_side = str(
                metadata.get("player_side") or metadata.get("user_side") or snapshot.orientation or ""
            ).strip().lower()
            user_side = "white" if raw_user_side in {"w", "white"} else "black" if raw_user_side in {"b", "black"} else ""
            mover_side = "white" if mover == "w" else "black" if mover == "b" else ""

            # Engine lưu score theo Trắng để swing/history có một chuẩn ổn định.
            # Chỉ ở biên public mới đổi sang góc nhìn user. Nếu chưa biết màu,
            # tuyệt đối không suy đoán rằng score dương là user đang hơn.
            advantage_side = _advantage_side(score) if score is not None else "unknown"
            if score is not None and user_side:
                evaluation_cp_user = int(score) if user_side == "white" else -int(score)
                evaluation_label_user = _user_position_label(evaluation_cp_user)

            return ChessObservation(
                game_id=snapshot.game_id,
                mode=mode,
                fen=position.fen,
                board_fen=position.board_fen,
                side_to_move="white" if position.turn == "w" else "black",
                move_number=position.fullmove,
                user_side=user_side,
                mover_side=mover_side,
                actor_is_user=bool(user_side and mover_side and user_side == mover_side),
                last_move_uci=last_move.uci if last_move else "",
                last_move_san=last_san,
                evaluation_cp=score,
                evaluation_label=label,
                evaluation_cp_white=score,
                evaluation_label_white=label,
                evaluation_cp_user=evaluation_cp_user,
                evaluation_label_user=evaluation_label_user,
                advantage_side=advantage_side,
                user_position=evaluation_label_user,
                evaluation_swing_cp=swing,
                move_quality=quality,
                hint_focus=hint_focus,
                suggestion=suggestion,
                analysis_provider=analysis_provider,
                analysis_depth=analysis_depth,
                analysis_nodes=analysis_nodes,
                analysis_elapsed_ms=analysis_elapsed_ms,
                analysis_degraded=analysis_degraded,
                analysis_error=analysis_error,
                mate_in=mate_in,
                principal_variation=principal_variation,
                phase=state.phase,
                board_detected=True,
                game_started=game_started,
                game_over=game_over,
                result=result,
                resynced=resynced,
                confidence=0.65 if resynced else 1.0,
            )

    def analyze_position(
        self,
        position: Position,
        *,
        top_n: int = 3,
        on_demand: bool = False,
    ) -> ChessAnalysis:
        """Public boundary cho câu hỏi trực tiếp và post-game analysis."""

        with self._lock:
            return self.analyzer.analyze_position(
                position,
                top_n=max(1, int(top_n)),
                on_demand=bool(on_demand),
            )

    def close(self) -> None:
        try:
            self.analyzer.close()
        except Exception:
            pass

    def diagnostics(self) -> dict[str, object]:
        try:
            analyzer = self.analyzer.diagnostics()
        except Exception as exc:
            analyzer = {"ready": False, "error": f"{type(exc).__name__}: {exc}"}
        return {
            "games": len(self._games),
            "depth": self.depth,
            "direct_move_rate": self.direct_move_rate,
            "analyzer": analyzer,
        }

    def _should_reveal_direct_move(self, snapshot: BoardSnapshot, seen: int) -> bool:
        if self.direct_move_rate <= 0.0:
            return False
        seed = f"{snapshot.game_id}:{snapshot.board_fen}:{seen}".encode("utf-8", "ignore")
        value = int(hashlib.sha256(seed).hexdigest()[:12], 16) / float(0xFFFFFFFFFFFF)
        return value < self.direct_move_rate


def _hint_focus(position: Position, move) -> str:
    piece = position.board[move.from_sq].lower()
    target = position.board[move.to_sq]
    if move.castle:
        return "king_safety"
    child = apply_move(position, move)
    if in_check(child, child.turn):
        return "forcing_check"
    if target != "." or move.en_passant:
        return "forcing_capture"
    if piece in {"n", "b"} and move.from_sq // 8 in {0, 7}:
        return "develop_piece"
    if piece == "p" and move.to_sq % 8 in {3, 4}:
        return "central_control"
    return "improve_piece_activity"


def _move_quality(swing_cp: int) -> str:
    if swing_cp <= -300:
        return "blunder"
    if swing_cp <= -140:
        return "mistake"
    if swing_cp <= -60:
        return "inaccuracy"
    if swing_cp >= 180:
        return "strong"
    if swing_cp >= 80:
        return "good"
    return "normal"


def _snapshot_has_started(snapshot: BoardSnapshot) -> bool:
    """Suy ra ván đã bắt đầu từ bằng chứng mạnh, không từ chữ active mơ hồ."""

    metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else dict(snapshot.metadata or {})
    if bool(metadata.get("game_started") or metadata.get("clock_running")):
        return True
    if snapshot.ply > 0:
        return True
    if snapshot.board_fen != Position.initial().board_fen:
        return True
    return snapshot.status in {"playing", "in_progress", "started"}


__all__ = ["ChessGameTracker"]

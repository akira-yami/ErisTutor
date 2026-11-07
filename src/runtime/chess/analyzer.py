# -*- coding: utf-8 -*-
"""Lớp phân tích cờ hợp nhất giữa Stockfish và engine nội bộ.

Mục tiêu của module này là giữ một contract duy nhất cho tracker:
- Có Stockfish thì dùng Stockfish làm nguồn đánh giá chính.
- Chưa đặt binary, binary lỗi hoặc timeout thì rơi về engine nội bộ.
- Caller luôn nhận kết quả typed, không phải tự đoán provider nào vừa trả lời.

Stockfish chỉ hiểu bàn cờ. Việc có nên nói, nói nhẹ hay nói thẳng vẫn thuộc
companion policy / governor. Tách như vậy để một engine cờ không vô tình được
thăng chức thành chuyên gia quan hệ con người, một nghề vốn đã đủ đông người làm
không tốt.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Protocol

from .engine import Move, Position, analyze, evaluate, evaluation_label


@dataclass(frozen=True, slots=True)
class AnalyzedMove:
    """Một candidate move đã chuẩn hóa cho tracker và Surface."""

    move: Move
    score_cp: int
    mate_in: int | None = None
    depth: int = 0
    pv: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChessAnalysis:
    """Kết quả phân tích một thế cờ, quy ước score theo góc nhìn Trắng."""

    provider: str
    score_cp: int
    evaluation_label: str
    candidates: tuple[AnalyzedMove, ...] = ()
    mate_in: int | None = None
    depth: int = 0
    nodes: int = 0
    elapsed_ms: int = 0
    degraded: bool = False
    error: str = ""


class ChessAnalyzer(Protocol):
    """Contract tối thiểu để tracker không phụ thuộc trực tiếp vào Stockfish."""

    def analyze_position(
        self,
        position: Position,
        *,
        top_n: int = 3,
        on_demand: bool = False,
    ) -> ChessAnalysis:
        ...

    def close(self) -> None:
        ...

    def diagnostics(self) -> dict[str, object]:
        ...


class LocalChessAnalyzer:
    """Adapter cho engine nội bộ cũ, dùng làm fallback luôn sẵn sàng."""

    def __init__(self, *, depth: int = 2) -> None:
        self.depth = max(1, min(4, int(depth)))

    def analyze_position(
        self,
        position: Position,
        *,
        top_n: int = 3,
        on_demand: bool = False,
    ) -> ChessAnalysis:
        raw = int(evaluate(position))
        candidates = tuple(
            AnalyzedMove(move=move, score_cp=int(score), depth=self.depth, pv=(move.uci,))
            for move, score in analyze(position, depth=self.depth, top_n=max(1, int(top_n)))
        )
        return ChessAnalysis(
            provider="local",
            score_cp=raw,
            evaluation_label=evaluation_label(raw),
            candidates=candidates,
            depth=self.depth,
        )

    def close(self) -> None:
        return None

    def diagnostics(self) -> dict[str, object]:
        return {"provider": "local", "ready": True, "depth": self.depth}


class HybridChessAnalyzer:
    """Ưu tiên Stockfish nhưng fail-open sang engine nội bộ.

    Adapter Stockfish được truyền từ ngoài để module này không tự đọc settings
    hay env. Nhờ vậy test có thể dùng fake analyzer và runtime giữ quyền sở hữu
    việc tìm executable.
    """

    def __init__(
        self,
        *,
        primary: ChessAnalyzer | None,
        fallback: ChessAnalyzer,
        logger=None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.logger = logger
        self._primary_failures = 0
        self._last_error = ""
        self._last_provider = "fallback"
        self._primary_disabled_until = 0.0

    def analyze_position(
        self,
        position: Position,
        *,
        top_n: int = 3,
        on_demand: bool = False,
    ) -> ChessAnalysis:
        now = time.monotonic()
        if self.primary is not None and now >= self._primary_disabled_until:
            try:
                result = self.primary.analyze_position(
                    position,
                    top_n=top_n,
                    on_demand=on_demand,
                )
                self._last_provider = result.provider
                self._last_error = ""
                self._primary_disabled_until = 0.0
                return result
            except Exception as exc:  # noqa: BLE001 - external process boundary
                self._primary_failures += 1
                self._last_error = f"{type(exc).__name__}: {exc}"[:500]
                # Backoff ngắn để binary lỗi không bị spawn lại ở từng nước.
                # Sau cooldown runtime tự thử lại, nên người dùng có thể sửa path
                # hoặc thay binary rồi tiếp tục mà không cần một circuit vĩnh viễn.
                cooldown = min(60.0, 5.0 * (2 ** min(3, self._primary_failures - 1)))
                self._primary_disabled_until = time.monotonic() + cooldown
                if self.logger is not None:
                    try:
                        self.logger.warning(
                            "[chess/stockfish] phân tích lỗi, dùng engine nội bộ: %s",
                            exc,
                        )
                    except Exception:
                        pass

        fallback = self.fallback.analyze_position(
            position,
            top_n=top_n,
            on_demand=on_demand,
        )
        self._last_provider = fallback.provider
        if self._last_error:
            return ChessAnalysis(
                provider=fallback.provider,
                score_cp=fallback.score_cp,
                evaluation_label=fallback.evaluation_label,
                candidates=fallback.candidates,
                mate_in=fallback.mate_in,
                depth=fallback.depth,
                nodes=fallback.nodes,
                elapsed_ms=fallback.elapsed_ms,
                degraded=True,
                error=self._last_error,
            )
        return fallback

    def close(self) -> None:
        for analyzer in (self.primary, self.fallback):
            if analyzer is None:
                continue
            try:
                analyzer.close()
            except Exception:
                pass

    def diagnostics(self) -> dict[str, object]:
        primary = self.primary.diagnostics() if self.primary is not None else {"ready": False, "reason": "not_configured"}
        fallback = self.fallback.diagnostics()
        return {
            "provider": "hybrid",
            "last_provider": self._last_provider,
            "primary_failures": self._primary_failures,
            "last_error": self._last_error,
            "primary_cooldown_seconds": round(max(0.0, self._primary_disabled_until - time.monotonic()), 3),
            "primary": primary,
            "fallback": fallback,
        }


__all__ = [
    "AnalyzedMove",
    "ChessAnalysis",
    "ChessAnalyzer",
    "HybridChessAnalyzer",
    "LocalChessAnalyzer",
]

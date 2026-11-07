# -*- coding: utf-8 -*-
"""Typed chess bridge contracts used by ERIS and browser integrations."""
# Live-human pages (previously "live_restricted") now receive the same full
# companion assistance as analysis, bot, and private sessions: position
# evaluation, move hints and the occasional direct move suggestion.
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class ChessAssistMode(str, Enum):
    ANALYSIS = "analysis"
    BOT = "bot"
    PRIVATE = "private"
    LIVE_RESTRICTED = "live_restricted"
    UNKNOWN = "unknown"

    @property
    def full_assistance(self) -> bool:
        """All known modes get full assistance; unknown pages are the only exception."""
        return self is not self.UNKNOWN


class ChessObservationPhase(str, Enum):
    """Vòng đời quan sát tạm thời của một ván cờ.

    Trạng thái này tách chuyện "đã nhận lời xem cờ" khỏi chuyện "đã thật sự
    nhìn thấy bàn". Nhờ vậy ERIS không còn nhảy thẳng sang đang chơi chỉ vì
    bridge gửi snapshot đầu tiên.
    """

    WAITING_FOR_BOARD = "waiting_for_board"
    WAITING_FOR_START = "waiting_for_start"
    ACTIVE = "active"
    POSSIBLY_ENDED = "possibly_ended"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class BoardSnapshot:
    platform: str
    game_id: str
    board_fen: str
    page_url: str = ""
    orientation: str = "white"
    ply: int = 0
    result: str = ""
    status: str = ""
    mode_hint: str = ""
    mode_override: str = ""
    captured_at: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "BoardSnapshot":
        metadata = data.get("metadata") if isinstance(data.get("metadata"), Mapping) else {}
        return cls(
            platform=str(data.get("platform") or "chess.com").strip().lower()[:64],
            game_id=str(data.get("game_id") or data.get("gameId") or "unknown").strip()[:160],
            board_fen=" ".join(str(data.get("board_fen") or data.get("boardFen") or "").split())[:160],
            page_url=str(data.get("page_url") or data.get("url") or "").strip()[:1000],
            orientation=str(data.get("orientation") or "white").strip().lower()[:16],
            ply=max(0, int(data.get("ply", 0) or 0)),
            result=str(data.get("result") or "").strip()[:32],
            status=str(data.get("status") or "").strip().lower()[:64],
            mode_hint=str(data.get("mode_hint") or data.get("mode") or "").strip().lower()[:64],
            mode_override=str(data.get("mode_override") or "").strip().lower()[:64],
            captured_at=max(0.0, float(data.get("captured_at", 0.0) or 0.0)),
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class MoveSuggestion:
    uci: str = ""
    san: str = ""
    score_cp: int = 0
    depth: int = 0
    # Provenance để Surface/Tutor biết đây là Stockfish hay fallback nội bộ.
    provider: str = ""
    mate_in: int | None = None
    pv: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ChessObservation:
    game_id: str
    mode: ChessAssistMode
    fen: str
    board_fen: str
    side_to_move: str
    move_number: int
    user_side: str = ""
    mover_side: str = ""
    actor_is_user: bool = False
    last_move_uci: str = ""
    last_move_san: str = ""
    # Theo dõi quan hệ nhân quả giữa một nước ERIS đã thật sự nêu công khai và
    # nước người dùng đi ngay sau đó. Các field này không suy đoán từ engine.
    previous_suggested_move_uci: str = ""
    previous_suggested_move_san: str = ""
    user_followed_previous_suggestion: bool = False
    suggestion_outcome: str = ""
    # ``evaluation_cp``/``evaluation_label`` được giữ tương thích ngược và
    # luôn mang góc nhìn Trắng. Các lane public phải ưu tiên nhóm *_user khi
    # biết màu của người dùng, tránh khen nhầm khi họ đang cầm Đen.
    evaluation_cp: int | None = None
    evaluation_label: str = ""
    evaluation_cp_white: int | None = None
    evaluation_label_white: str = ""
    evaluation_cp_user: int | None = None
    evaluation_label_user: str = ""
    advantage_side: str = ""
    user_position: str = "unknown"
    evaluation_swing_cp: int | None = None
    move_quality: str = ""
    hint_focus: str = ""
    suggestion: MoveSuggestion | None = None
    analysis_provider: str = ""
    analysis_depth: int = 0
    analysis_nodes: int = 0
    analysis_elapsed_ms: int = 0
    analysis_degraded: bool = False
    analysis_error: str = ""
    mate_in: int | None = None
    principal_variation: tuple[str, ...] = ()
    phase: ChessObservationPhase = ChessObservationPhase.WAITING_FOR_START
    board_detected: bool = True
    game_started: bool = False
    game_over: bool = False
    result: str = ""
    resynced: bool = False
    confidence: float = 1.0

    def to_event_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mode"] = self.mode.value
        payload["phase"] = self.phase.value
        payload["full_assistance"] = self.mode.full_assistance
        payload["suggestion"] = self.suggestion.to_dict() if self.suggestion else None
        # None ở đây là lệnh xóa thật, không phải “provider quên gửi”. Reducer
        # tri-state sẽ dọn suggestion của nước trước khi snapshot mới không có.
        payload["suggested_move_uci"] = self.suggestion.uci if self.suggestion else None
        payload["suggested_move_san"] = self.suggestion.san if self.suggestion else None
        return payload


__all__ = [
    "BoardSnapshot",
    "ChessAssistMode",
    "ChessObservationPhase",
    "ChessObservation",
    "MoveSuggestion",
]

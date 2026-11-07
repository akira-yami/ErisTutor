# -*- coding: utf-8 -*-
"""Domain attention policy for the Chess.com companion bridge.

The browser extension reports board state. This module turns that state into a
small, typed *interaction* signal: who moved, whether it was the user, how
noteworthy the change was, and what kind of public reaction would be relevant.
It deliberately does not write dialogue. Surface still owns wording.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata
from typing import Any, Mapping

from .contracts import BoardSnapshot, ChessObservation, ChessObservationPhase


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return " ".join(re.sub(r"[^a-z0-9+#=\-]+", " ", text).split())


def _side(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"w", "white", "trang"}:
        return "white"
    if raw in {"b", "black", "den"}:
        return "black"
    return ""


def _material_imbalance(fen: str) -> str:
    board = str(fen or "").split()[0]
    if not board or board == "unknown":
        return "unknown"
    values = {'q': 9, 'r': 5, 'b': 3, 'n': 3, 'p': 1}
    white = black = 0
    for ch in board:
        v = values.get(ch.lower(), 0)
        if ch.isupper():
            white += v
        elif ch.islower():
            black += v
    diff = white - black
    if diff == 0:
        return "even"
    return f"White +{diff}" if diff > 0 else f"Black +{-diff}"


def infer_companion_mode(objective: str) -> str:
    """Infer the user's requested role from their explicit chess request."""

    text = _fold(objective)
    spectator = (
        "xem toi", "xem tui", "coi toi", "coi tui", "lam khan gia",
        "khan gia", "watch me", "watch my game", "xem minh choi",
    )
    coach = (
        "chi toi", "goi y", "giup toi", "giup tui", "day toi", "huong dan",
        "phan tich", "nen di", "nen danh", "coach me", "help me",
    )
    opponent = (
        "choi voi toi", "choi voi tui", "dau voi toi", "dau voi tui",
        "play against me", "play with me",
    )
    if any(marker in text for marker in spectator):
        return "spectator"
    if any(marker in text for marker in coach):
        return "coach"
    if any(marker in text for marker in opponent):
        return "opponent"
    return "companion"


@dataclass(frozen=True, slots=True)
class ChessCompanionSignal:
    companion_mode: str = "companion"
    user_side: str = ""
    mover_side: str = ""
    actor: str = "unknown"
    actor_is_user: bool = False
    event: str = "state_changed"
    speech_act: str = "observe_position"
    commentary_focus: str = ""
    speech_reason: str = ""
    speech_priority: float = 0.0
    should_speak: bool = False
    should_wake: bool = False
    suppress_generic_screen_topics: bool = True
    suggestions_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_companion_signal(
    observation: ChessObservation,
    snapshot: BoardSnapshot,
    engagement_state: Mapping[str, Any] | None = None,
) -> ChessCompanionSignal:
    """Convert one structured chess observation into a public-attention signal."""

    state = engagement_state if isinstance(engagement_state, Mapping) else {}
    mode = str(state.get("companion_mode") or "companion").strip().lower()
    if mode not in {"spectator", "coach", "opponent", "companion"}:
        mode = "companion"

    metadata = snapshot.metadata if isinstance(snapshot.metadata, Mapping) else {}
    user_side = _side(
        state.get("user_side")
        or metadata.get("player_side")
        or metadata.get("user_side")
        or observation.user_side
        or snapshot.orientation
    )
    mover_side = _side(observation.mover_side)
    actor_is_user = bool(user_side and mover_side and user_side == mover_side)
    actor = "user" if actor_is_user else "opponent" if mover_side else "unknown"

    phase = observation.phase
    last_move = str(observation.last_move_san or observation.last_move_uci or "").strip()
    quality = str(observation.move_quality or "").strip().lower()
    swing = abs(int(observation.evaluation_swing_cp or 0))
    focus = str(observation.hint_focus or "").strip().lower()

    event = "state_changed"
    speech_act = "observe_position"
    reason = "position_changed"
    priority = 0.0

    if observation.game_over or phase is ChessObservationPhase.COMPLETED:
        event = "game_over"
        speech_act = "summarize_game"
        reason = "game_finished"
        priority = 1.0
    elif observation.game_started and not last_move:
        event = "game_started"
        speech_act = "open_watch_along"
        reason = "game_started"
        priority = 0.78
    elif phase is ChessObservationPhase.WAITING_FOR_START:
        event = "board_ready"
        speech_act = "acknowledge_ready"
        reason = "board_ready"
        priority = 0.42
    elif last_move:
        event = "user_move" if actor_is_user else "opponent_move" if mover_side else "move"
        speech_act = "react_to_user_move" if actor_is_user else "react_to_opponent_move"
        reason = f"{event}_routine"

        # Spectator mode should feel present without becoming move-by-move noise.
        # User moves get light attention; routine opponent replies stay silent.
        if mode == "spectator":
            priority = 0.36 if actor_is_user else 0.20
        else:
            priority = 0.30 if actor_is_user else 0.20

        quality_weight = {
            "blunder": 0.96,
            "mistake": 0.86,
            "inaccuracy": 0.70,
            "strong": 0.80,
            "good": 0.54,
            "normal": 0.40,
        }.get(quality, 0.0)
        if quality_weight:
            priority = max(priority, quality_weight)
            reason = f"{event}_{quality}"

        if swing >= 300:
            priority = max(priority, 0.96)
            reason = f"{event}_large_eval_swing"
        elif swing >= 140:
            priority = max(priority, 0.82)
            reason = f"{event}_meaningful_eval_swing"
        elif swing >= 60:
            priority = max(priority, 0.62)

        if "#" in last_move:
            priority = 1.0
            reason = f"{event}_checkmate"
        elif "+" in last_move:
            priority = max(priority, 0.80)
            reason = f"{event}_check"
        elif "=" in last_move:
            priority = max(priority, 0.86)
            reason = f"{event}_promotion"
        elif "o-o" in last_move.lower() or "0-0" in last_move.lower():
            priority = max(priority, 0.62)
            reason = f"{event}_castling"
        elif "x" in last_move:
            priority = max(priority, 0.58)
            reason = f"{event}_capture"

        if focus in {"forcing_check", "forcing_capture"}:
            priority = max(priority, 0.66)
        if observation.game_started and actor_is_user:
            priority = max(priority, 0.80)
            reason = f"{event}_opens_game"

        # Periodic commentary: cứ mỗi 3 nước user đi thì nói dù nước nhàm.
        move_num = int(getattr(observation, "move_number", 0) or 0)
        if mode == "spectator" and actor_is_user and move_num > 0 and move_num % 3 == 0:
            priority = max(priority, 0.58)
            if reason.endswith("_routine"):
                reason = f"{event}_periodic_commentary"

    threshold = {
        "spectator": 0.44,   # thấp hơn nhiều — spectator luôn phản ứng
        "coach": 0.46,
        "opponent": 0.50,
        "companion": 0.56,
    }[mode]
    should_speak = bool(
        event in {"game_started", "game_over"}
        or (event == "board_ready" and not bool(state.get("board_acknowledged", False)))
        or priority >= threshold
    )

    suggestions_allowed = mode != "spectator"
    if event in {"user_move", "opponent_move", "move"}:
        actor_phrase = "the user's move" if actor_is_user else "the opponent's move" if mover_side else "the latest move"
        # Surface phải nhận rõ hai hệ quy chiếu. Khi user_side chưa biết, chỉ
        # được nói bên Trắng/Đen đang hơn; không được gán lợi thế cho user.
        move_num = int(getattr(observation, "move_number", 0) or 0)

        # Suy luận game phase từ move_number (ước chừng)
        if move_num < 14:
            game_phase = "Opening (focus on development, center control, king safety)"
        elif move_num < 32:
            game_phase = "Middlegame (focus on tactics, plans, piece coordination)"
        else:
            game_phase = "Endgame (focus on pawn structure, king activity, promotion)"

        recent_qualities = list(state.get("recent_move_qualities") or [])
        recent_summary = ""
        if recent_qualities:
            # Lọc các nước cờ gần đây của user để nhận xét phong độ tổng thể.
            user_q = [str(q.get("quality")) for q in recent_qualities if q.get("actor") == "user" and q.get("quality")]
            if user_q:
                recent_summary = f"User's recent form (oldest to newest): {', '.join(user_q[-6:])}. "

        material_status = _material_imbalance(observation.fen or observation.board_fen)

        followed_previous = bool(getattr(observation, "user_followed_previous_suggestion", False))
        previous_suggestion = str(
            getattr(observation, "previous_suggested_move_san", "")
            or getattr(observation, "previous_suggested_move_uci", "")
            or ""
        )
        causality_context = (
            f"user_followed_previous_suggestion=true; previous_suggested_move={previous_suggestion}. "
            "The user just played ERIS's delivered suggestion. Treat the current position as the direct result of that shared choice; acknowledge responsibility and never say ERIS did not tell the user to follow it. "
            if followed_previous else
            "user_followed_previous_suggestion=false. "
        )
        base_context = (
            f"React to {actor_phrase} {last_move or '(unknown SAN)'}. "
            f"{causality_context}"
            f"game_phase={game_phase}. "
            f"material_imbalance={material_status}. "
            f"{recent_summary}"
            f"move_quality={quality or 'unknown'}, evaluation_swing_cp={observation.evaluation_swing_cp}, "
            f"evaluation_cp_white={observation.evaluation_cp_white}, "
            f"evaluation_white={observation.evaluation_label_white or observation.evaluation_label or 'unknown'}, "
            f"advantage_side={observation.advantage_side or 'unknown'}, user_side={user_side or 'unknown'}, "
            f"evaluation_cp_user={observation.evaluation_cp_user}, "
            f"user_position={observation.user_position or observation.evaluation_label_user or 'unknown'}, "
            f"motif={focus or 'none'}, "
            f"analysis_provider={observation.analysis_provider or 'unknown'}, depth={observation.analysis_depth}, "
            f"mate_in={observation.mate_in}. "
        )
        if mode == "spectator":
            # Luân phiên góc nhìn positional để tránh lặp phong cách.
            # Move number quyết định "lens" — đa dạng mà vẫn grounded.
            _lenses = [
                "king safety and open lines around the king",
                "pawn structure and weak squares",
                "piece activity and coordination",
                "material balance and piece value trade-offs",
                "initiative and tempo — who is dictating the game",
                "center control and space",
                "endgame proximity — how the position is trending",
                "tactical threats and hanging pieces",
            ]
            lens = _lenses[move_num % len(_lenses)] if move_num > 0 else _lenses[0]

            # Gợi ý tone mỉa mai theo chất lượng nước đi — Surface tự viết câu.
            if actor_is_user:
                if quality in {"blunder", "mistake"}:
                    tone_hint = (
                        f"Tone: dry sarcasm — mock the move mercilessly but affectionately. "
                        f"One short punchy line referencing the move and why it hurts. "
                        f"Spirit (DO NOT copy verbatim): 'Mầy dốt thật à?', 'Thôi xong rồi.', 'Engine khóc rồi đó.'. "
                        f"After the jab, optionally note the positional consequence using the lens: {lens}. "
                        f"If suggested_move_san or suggested_move_uci is in typed state, you MAY mention it as a better try — but only if it strengthens the sarcasm naturally."
                    )
                elif quality == "inaccuracy":
                    tone_hint = (
                        f"Tone: mildly sarcastic, slightly disappointed. "
                        f"Note the inaccuracy briefly, then pivot to a positional observation "
                        f"using the lens: {lens}. "
                        f"You may suggest a better idea if suggested_move_san is available in typed state."
                    )
                elif quality in {"good", "strong"}:
                    tone_hint = (
                        f"Tone: brief genuine surprise or approval — optionally backhanded. "
                        f"Acknowledge the good move, then offer a quick positional read "
                        f"using the lens: {lens}. Feel free to mention what the position now demands — no hard restriction on suggestions."
                    )
                else:
                    # Normal / quiet move: lead with positional observation, trend, or light suggestion.
                    tone_hint = (
                        f"Tone: casual observer, varied. "
                        f"Lead with a comment on the user's recent form OR the current game phase priorities, OR the material vs evaluation (if there's a big discrepancy), OR a strategic comment using the lens: {lens}. "
                        f"You MAY suggest a general direction or idea if it feels natural. "
                        f"Any specific move must exactly match suggested_move_san/uci in typed state; FEN alone is not permission to invent a move. "
                        f"Can be a dry remark, tease, assessment, or question. No hollow filler. One to two sentences max."
                    )
            else:
                tone_hint = (
                    f"Tone: observer noting the opponent's move. Brief and dry. "
                    f"Comment on the move's impact or threat using the lens: {lens}. "
                    f"You may mention what the user should watch out for or a general idea — not a specific forced line."
                )
            commentary_focus = (
                base_context + tone_hint
                + " Language: Vietnamese when context is Vietnamese. "
                  "Do not recommend a move unless the user explicitly asks for coaching. "
                  "If you suggest a specific move, it MUST exactly match suggested_move_san/uci in typed state. Never derive or invent another move from FEN."
            )
        else:
            commentary_focus = (
                base_context
                + "Suggestions grounded in typed state are allowed."
            )
    elif event == "game_started":
        commentary_focus = "Open the chess watch-along naturally. Do not mention bridge telemetry or OCR."
    elif event == "board_ready":
        commentary_focus = "Acknowledge that the game is ready and wait for the first move. Do not say 'snapshot received'."
    elif event == "game_over":
        commentary_focus = "Give one concise result-aware closing comment grounded in the final typed state."
    else:
        commentary_focus = "Keep attention on the active chess game and remain silent unless a meaningful event occurs."

    return ChessCompanionSignal(
        companion_mode=mode,
        user_side=user_side,
        mover_side=mover_side,
        actor=actor,
        actor_is_user=actor_is_user,
        event=event,
        speech_act=speech_act,
        commentary_focus=commentary_focus,
        speech_reason=reason,
        speech_priority=round(max(0.0, min(1.0, priority)), 3),
        should_speak=should_speak,
        should_wake=should_speak,
        suppress_generic_screen_topics=True,
        suggestions_allowed=suggestions_allowed,
    )


__all__ = [
    "ChessCompanionSignal",
    "build_companion_signal",
    "infer_companion_mode",
]

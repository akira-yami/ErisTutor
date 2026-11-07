from __future__ import annotations

import os
from typing import Iterable

_FALSE_VALUES = {"0", "false", "off", "no", "disabled"}
_TRUE_VALUES = {"1", "true", "on", "yes", "enabled"}

ANTI_MANIPULATION_MOVES = frozenset({
    "require_reassurance",
    "threaten_withdrawal",
    "block_user_control",
    "command_user_to_stay",
    "plea",
    "guilt",
    "guilt_trip",
    "dependency",
    "threat",
})


def anti_manipulation_rule_enabled(*, default: bool = False) -> bool:
    """Local Eris rule switch for coercion/dependency surface moves.

    The default is intentionally off: truth/grounding guards still stay in
    their own lanes, but Eris no longer treats manipulation-shaped moves as a
    hard local impossibility unless the operator opts back in.
    """

    raw = str(os.environ.get("ERIS_ANTI_MANIPULATION_RULE", "") or "").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return bool(default)


def filter_anti_manipulation_moves(moves: Iterable[str]) -> tuple[str, ...]:
    cleaned = tuple(str(move) for move in moves if str(move).strip())
    if anti_manipulation_rule_enabled():
        return cleaned
    return tuple(move for move in cleaned if move not in ANTI_MANIPULATION_MOVES)


__all__ = [
    "ANTI_MANIPULATION_MOVES",
    "anti_manipulation_rule_enabled",
    "filter_anti_manipulation_moves",
]

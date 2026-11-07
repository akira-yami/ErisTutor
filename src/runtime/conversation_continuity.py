from __future__ import annotations

"""Facade continuity giữ API bootstrap cũ, bên trong dùng contract typed V44."""

from dataclasses import dataclass
from typing import Any

from runtime.discourse_state import DiscourseResolution, DiscourseResolver, DiscourseStateBuilder
from runtime.semantic_frame import SemanticFrame


@dataclass(slots=True)
class ConversationContinuityAdapter:
    ledger: Any

    def prompt_context(self, *, current_turn_index: int) -> str:
        return DiscourseStateBuilder(self.ledger).prompt_context(
            current_turn_index=current_turn_index,
        )

    def resolve(
        self,
        frame: SemanticFrame | dict[str, Any] | None,
        *,
        current_user_turn_index: int,
        model_text: str = "",
    ) -> DiscourseResolution:
        # ``model_text`` được giữ trong signature để caller cũ không gãy; resolver
        # V44 không đọc surface text nữa.
        _ = model_text
        return DiscourseResolver(self.ledger).resolve(
            frame,
            current_user_turn_index=current_user_turn_index,
        )


__all__ = ["ConversationContinuityAdapter"]

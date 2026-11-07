# -*- coding: utf-8 -*-
"""Compatibility projections từ authority V12, không hồi sinh SemanticFrame cũ."""
from __future__ import annotations

from typing import Any, Mapping


def current_intent_projection(host: Any) -> dict[str, Any]:
    intent = getattr(host, "current_world_intent", None)
    to_dict = getattr(intent, "to_dict", None)
    if callable(to_dict):
        try:
            raw = to_dict()
            if isinstance(raw, Mapping):
                return dict(raw)
        except Exception:
            pass
    if isinstance(intent, Mapping):
        return dict(intent)
    return {}


def current_move_projection(host: Any) -> dict[str, Any]:
    raw = getattr(host, "last_agent_turn", None)
    return dict(raw) if isinstance(raw, Mapping) else {}



def legacy_semantic_projection(host: Any) -> dict[str, Any]:
    """Read-only compatibility shape cho panel/metrics cũ.

    Không có consumer nào được dùng projection này để chọn lời nói. Nó chỉ ánh
    xạ authority mới (WorldIntent + NextConversationMove) sang vài field debug cũ.
    """
    intent = current_intent_projection(host)
    move = current_move_projection(host)
    domain = str(intent.get("domain", "conversation") or "conversation")
    operation = str(intent.get("operation", "respond") or "respond")
    granularity = str(intent.get("granularity", "general") or "general")
    tool_intent = domain if domain in {"screen", "environment"} else "none"
    return {
        "version": "conversation_kernel_v12_projection",
        "user_act": operation,
        "discourse_relation": "current_turn",
        "target": granularity,
        "ambiguity_owner": "root_agent",
        "tool_intent": tool_intent,
        "boundary_action": str(move.get("after", "") or ""),
        "intent": str(move.get("intent", "") or ""),
        "delivery": str(move.get("delivery", "") or ""),
    }

def conversation_debug_projection(host: Any) -> dict[str, Any]:
    return {
        "schema": "eris.conversation_projection.v12",
        "intent": current_intent_projection(host),
        "move": current_move_projection(host),
    }


def infer_turn_category(host: Any) -> str:
    intent = current_intent_projection(host)
    domain = str(intent.get("domain", "") or "").strip().lower()
    operation = str(intent.get("operation", "") or "").strip().lower()
    if domain in {"screen", "environment"} or operation.startswith("inspect_"):
        return "tool_or_action"
    move = current_move_projection(host)
    if move:
        return "general_chat"
    return "unknown"


__all__ = [
    "conversation_debug_projection",
    "current_intent_projection",
    "current_move_projection",
    "infer_turn_category",
    "legacy_semantic_projection",
]

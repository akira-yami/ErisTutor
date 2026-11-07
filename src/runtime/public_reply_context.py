# -*- coding: utf-8 -*-
"""Bounded context compiler for the public ERIS writer.

The runtime owns many useful state stores. That does not mean every store earns a
seat in every prompt. This compiler converts raw context blocks into a small,
route-aware packet so a greeting is not forced to read a psychological census,
OCR cache and memory archive before saying hello.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class CompiledPublicReplyContext:
    personality: str = ""
    relationship: str = ""
    grounding: str = ""
    memory: str = ""
    interrupt: str = ""
    engagement: str = ""
    routing: str = ""
    discourse: str = ""
    total_chars: int = 0
    dropped: tuple[str, ...] = ()
    truncated: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "personality_chars": len(self.personality),
            "relationship_chars": len(self.relationship),
            "grounding_chars": len(self.grounding),
            "memory_chars": len(self.memory),
            "interrupt_chars": len(self.interrupt),
            "engagement_chars": len(self.engagement),
            "routing_chars": len(self.routing),
            "discourse_chars": len(self.discourse),
            "total_chars": self.total_chars,
            "dropped": list(self.dropped),
            "truncated": list(self.truncated),
        }


def _clean(text: object) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    # Preserve line boundaries because evidence/memory renderers use them, while
    # removing accidental blank-page inflation.
    lines = [re.sub(r"[ \t]+", " ", line).rstrip() for line in value.splitlines()]
    compacted: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        compacted.append(line)
        previous_blank = blank
    return "\n".join(compacted).strip()


def _clip(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0 or not text:
        return "", bool(text)
    if len(text) <= limit:
        return text, False
    cut = text[: max(0, limit - 24)].rstrip()
    # Prefer a line/sentence boundary near the end instead of amputating JSON or
    # a Vietnamese word wherever the character counter happened to get bored.
    boundary = max(cut.rfind("\n"), cut.rfind(". "), cut.rfind("; "))
    if boundary >= int(limit * 0.65):
        cut = cut[: boundary + 1].rstrip()
    return cut + "\n[context truncated]", True


def _route_reasons(route: Any) -> set[str]:
    if route is None:
        return set()
    if isinstance(route, Mapping):
        values = route.get("reasons", ())
    else:
        values = getattr(route, "reasons", ())
    return {str(item or "").strip().lower() for item in tuple(values or ()) if str(item or "").strip()}


def compile_public_reply_context(
    *,
    route: Any = None,
    personality: str = "",
    relationship: str = "",
    grounding: str = "",
    memory: str = "",
    interrupt: str = "",
    engagement: str = "",
    routing: str = "",
    discourse: str = "",
    total_limit: int = 9000,
    budget_mode: str = "compact",
) -> CompiledPublicReplyContext:
    """Compile context according to the turn's already-selected model route.

    This is a budget/ownership layer, not a second semantic classifier. It only
    consumes typed route reasons produced earlier in the public pipeline.
    """

    reasons = _route_reasons(route)
    simple_social = "simple_social_turn" in reasons
    simple_command = "clear_simple_command" in reasons
    relational = bool(reasons & {"relational_subtext", "emotional_nuance", "expressive_style_request"})
    contextual = "contextual_ambiguity" in reasons
    technical = "technical_depth" in reasons

    # V12 chạy trên DS2API/local context rộng nên foreground conversation không
    # được tự bóp state đã retrieve xuống vài trăm ký tự. ``wide`` chỉ nới budget
    # cơ học; nó KHÔNG thay đổi semantic routing hay tự inject store không liên quan.
    # ``compact`` giữ nguyên contract cho các caller/test legacy còn cần prompt nhỏ.
    wide = str(budget_mode or "compact").strip().lower() == "wide"
    budgets = {
        "personality": 12000 if wide else 900,
        "relationship": 12000 if wide else 700,
        "grounding": 32000 if wide else 1700,
        "memory": 32000 if wide else 1400,
        "interrupt": 6000 if wide else 650,
        "engagement": 8000 if wide else 900,
        "routing": 6000 if wide else 600,
        "discourse": 16000 if wide else 1300,
    }
    if not wide and simple_social:
        budgets.update(
            personality=620,
            relationship=320,
            grounding=0,
            memory=0,
            interrupt=300,
            engagement=420,
            routing=300,
            discourse=520,
        )
    elif not wide and simple_command:
        budgets.update(
            personality=360,
            relationship=0,
            memory=0,
            grounding=2100,
            discourse=650,
        )
    elif not wide and technical:
        budgets.update(
            personality=520,
            relationship=0,
            grounding=2300,
            memory=900 if contextual else 0,
            discourse=1100,
        )
    elif not wide and relational:
        budgets.update(
            personality=1450,
            relationship=1250,
            grounding=700,
            memory=1000 if contextual else 350,
            discourse=1500,
        )

    raw = {
        "personality": _clean(personality),
        "relationship": _clean(relationship),
        "grounding": _clean(grounding),
        "memory": _clean(memory),
        "interrupt": _clean(interrupt),
        "engagement": _clean(engagement),
        "routing": _clean(routing),
        "discourse": _clean(discourse),
    }
    values: dict[str, str] = {}
    dropped: list[str] = []
    truncated: list[str] = []
    for name, text in raw.items():
        clipped, was_truncated = _clip(text, int(budgets[name]))
        if text and not clipped:
            dropped.append(name)
        elif was_truncated:
            truncated.append(name)
        values[name] = clipped

    # Hard total cap. Drop lowest-authority enrichment first; never drop routing,
    # discourse or grounding before personality/memory decoration.
    total_limit = max(1200, int(total_limit))
    drop_order = ("memory", "personality", "relationship", "engagement", "interrupt", "discourse", "grounding", "routing")
    total = sum(len(value) for value in values.values())
    for name in drop_order:
        if total <= total_limit:
            break
        if not values[name]:
            continue
        total -= len(values[name])
        values[name] = ""
        if name not in dropped:
            dropped.append(name)

    return CompiledPublicReplyContext(
        personality=values["personality"],
        relationship=values["relationship"],
        grounding=values["grounding"],
        memory=values["memory"],
        interrupt=values["interrupt"],
        engagement=values["engagement"],
        routing=values["routing"],
        discourse=values["discourse"],
        total_chars=sum(len(value) for value in values.values()),
        dropped=tuple(dropped),
        truncated=tuple(truncated),
    )


__all__ = ["CompiledPublicReplyContext", "compile_public_reply_context"]

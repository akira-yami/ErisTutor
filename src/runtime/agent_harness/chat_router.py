# -*- coding: utf-8 -*-
"""Foreground chat -> Unified Agent Harness routing.

This module deliberately does *not* replace Eris' normal capability planner.
Cheap/single-step desktop actions remain native capabilities.  The harness owns
multi-step execution work where inspect -> act -> observe -> replan is useful.

The route is deterministic and conservative.  A vague request that can branch
into materially different implementations is surfaced back to the public writer
as a clarification obligation instead of letting an executor guess expensively.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class AgentChatRoute:
    action: str = "none"  # none | execute | consult | clarify
    goal: str = ""
    reason: str = ""
    confidence: float = 0.0
    clarification: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "goal": self.goal,
            "reason": self.reason,
            "confidence": float(self.confidence),
            "clarification": self.clarification,
        }


def _fold(text: str) -> str:
    raw = unicodedata.normalize("NFD", str(text or "").lower())
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = raw.replace("đ", "d")
    return re.sub(r"\s+", " ", raw).strip()


_EXECUTION_VERBS = (
    "sua", "fix", "debug", "refactor", "trien khai", "implement", "viet code",
    "patch", "chay test", "test lai", "build", "compile", "kiem tra repo",
    "doc repo", "tim loi", "sua loi", "lam tiep", "lam di", "cap nhat code",
    "doi code", "them chuc nang", "bo chuc nang", "toi uu code", "optimize",
)
_CODE_TARGETS = (
    "repo", "code", "source", "file", "module", "class", "function", "func",
    "tool", "skill", "agent", "langgraph", "gemini", "router", "routing",
    "backend", "frontend", "godot", "python", "c#", "cs", "test", "bug", "loi",
    "panel", "ui", "runtime", "capability", "memory", "tts", "live2d", "git",
)
_SIMPLE_DESKTOP = (
    "mo chrome", "mo app", "mo ung dung", "chinh am luong", "tang am luong",
    "giam am luong", "mute", "pause nhac", "phat nhac", "bai gi", "nghe nhac gi",
    "dang nghe gi", "copy", "clipboard", "focus window", "mo link", "thoi tiet",
)
_CAPABILITY_QUERY = (
    "em lam duoc gi", "co lam duoc", "co the lam", "nang luc", "kha nang",
    "biet lam", "tool gi", "skill gi", "co tool", "co skill",
)
_GENERIC_CONTINUATIONS = (
    "lam di", "lam tiep di", "trien khai di", "sua di", "fix di", "tiep di",
    "lam luon", "lam no di", "oke lam di", "ok lam di",
)
_BROAD_QUALITY = (
    "ngon hon", "tot hon", "xin hon", "elegant hon", "dep hon", "thong minh hon",
    "toi uu hon", "hoan thien hon", "cai thien", "nang cap",
)
_SPECIFICITY_HINTS = (
    ".py", ".cs", ".gd", ".json", "::", "()", "->", "capability_", "project_",
    "get_", "set_", "src/", "runtime/", "actions/", "tests/", "error", "traceback",
    "khong chay", "bi loi", "fail", "die", "route", "latency", "timeout",
)


def _contains_any(text: str, items: tuple[str, ...]) -> bool:
    return any(item in text for item in items)


def _compact(text: str, limit: int = 1800) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def classify_agent_chat_route(
    user_text: str,
    *,
    previous_assistant_text: str = "",
    self_model=None,
) -> AgentChatRoute:
    """Classify one direct user turn for the execution harness.

    Rules intentionally bias against accidental execution.  `/agent ...` is the
    explicit escape hatch; normal Vietnamese coding imperatives are auto-routed
    when both an execution verb and a concrete technical target are present.
    """

    raw = str(user_text or "").strip()
    folded = _fold(raw)
    previous = str(previous_assistant_text or "").strip()
    previous_folded = _fold(previous)
    if not folded:
        return AgentChatRoute(reason="empty")

    explicit = False
    explicit_goal = raw
    for prefix in ("/agent ", "agent: ", "execute: ", "/execute "):
        if folded.startswith(prefix):
            explicit = True
            # Slice original by approximate prefix length; harmless if accents differ.
            explicit_goal = raw[len(prefix):].strip() or raw
            break
    if explicit:
        return AgentChatRoute("execute", _compact(explicit_goal), "explicit_agent_command", 1.0)

    # Capability Self-Model gets first refusal after explicit commands.  This is
    # the layer that knows whether Eris should use a cheap native action, ask
    # the dedicated coding subbrain to inspect, or hand off a long-running coding task.
    assessment = None
    if self_model is not None:
        try:
            assessment = self_model.assess(raw)
        except Exception:
            assessment = None
    lane = str(getattr(assessment, "lane", "") or "").strip().lower()
    if lane == "instant":
        return AgentChatRoute(reason="self_model_instant_native", confidence=float(getattr(assessment, "confidence", 0.9) or 0.9))
    if lane == "clarify":
        return AgentChatRoute(
            "clarify",
            _compact(raw),
            str(getattr(assessment, "reason", "self_model_ambiguity") or "self_model_ambiguity"),
            float(getattr(assessment, "confidence", 0.86) or 0.86),
            str(getattr(assessment, "clarification", "") or ""),
        )
    if lane == "consult":
        goal = (
            "INSPECT-ONLY backstage consultation. Do not modify files or run destructive/control tools. "
            "Investigate the technical question, gather evidence, and return a concise finding for Eris.\n"
            f"User context: {raw}"
        )
        return AgentChatRoute(
            "consult", _compact(goal),
            str(getattr(assessment, "reason", "technical_backstage_consult") or "technical_backstage_consult"),
            float(getattr(assessment, "confidence", 0.78) or 0.78),
        )
    if lane == "long_execution":
        return AgentChatRoute(
            "execute", _compact(raw),
            str(getattr(assessment, "reason", "self_model_long_execution") or "self_model_long_execution"),
            float(getattr(assessment, "confidence", 0.86) or 0.86),
        )

    # Questions about what Eris can do are conversation, not permission to start work.
    if _contains_any(folded, _CAPABILITY_QUERY) and not _contains_any(folded, _EXECUTION_VERBS):
        return AgentChatRoute(reason="capability_query", confidence=0.93)

    # One-step desktop intents stay on the cheap native capability path.
    if _contains_any(folded, _SIMPLE_DESKTOP) and not _contains_any(folded, _CODE_TARGETS):
        return AgentChatRoute(reason="single_step_native_capability", confidence=0.92)

    has_verb = _contains_any(folded, _EXECUTION_VERBS)
    has_target = _contains_any(folded, _CODE_TARGETS)
    historical_statement = any(marker in folded for marker in ("toi da ", "minh da ", "vua sua ", "da fix ", "da sua "))
    if historical_statement and not any(marker in folded for marker in ("giup", "ho", "cho toi", "di", "luon")):
        return AgentChatRoute(reason="describing_completed_work", confidence=0.84)
    has_specific = _contains_any(folded, _SPECIFICITY_HINTS) or bool(re.search(r"\b[a-zA-Z_][a-zA-Z0-9_]{3,}\.(?:py|cs|gd|json|md|txt)\b", raw))
    broad_quality = _contains_any(folded, _BROAD_QUALITY)
    generic_continuation = folded in _GENERIC_CONTINUATIONS or any(folded.startswith(x + " ") for x in _GENERIC_CONTINUATIONS)

    # "làm đi" can be perfectly clear when the immediately preceding assistant
    # turn already committed to a concrete code plan. Carry only a compact piece
    # of that public context, never private reasoning.
    previous_is_technical = _contains_any(previous_folded, _CODE_TARGETS) and (
        _contains_any(previous_folded, _EXECUTION_VERBS)
        or _contains_any(previous_folded, _SPECIFICITY_HINTS)
        or "buoc" in previous_folded
        or "se " in previous_folded
    )
    if generic_continuation and previous_is_technical:
        goal = (
            "Tiếp tục và thực thi quyết định kỹ thuật đã thống nhất trong hội thoại.\n"
            f"Yêu cầu mới của user: {raw}\n"
            f"Ngữ cảnh công khai ngay trước đó: {_compact(previous, 1400)}"
        )
        return AgentChatRoute("execute", goal, "technical_continuation", 0.91)

    # Multi-step code work with a concrete target is safe to start.
    if has_verb and has_target and (has_specific or not broad_quality):
        return AgentChatRoute("execute", _compact(raw), "concrete_execution_request", 0.88)

    # The expensive failure mode is guessing among architecture/UI/performance
    # branches when the user only says "make X better". Ask once, concretely.
    if has_target and (has_verb or broad_quality) and broad_quality and not has_specific:
        return AgentChatRoute(
            "clarify",
            _compact(raw),
            "materially_branching_ambiguity",
            0.86,
            (
                "Yêu cầu này có nhiều hướng triển khai khác nhau. Hỏi user chốt phần ưu tiên trước khi execute: "
                "hành vi/chức năng cần đạt, kiến trúc bên trong, hiệu năng, hay giao diện/trải nghiệm. "
                "Nếu ngữ cảnh ngay trước đã nêu vài phương án cụ thể, hãy biến chúng thành lựa chọn ngắn thay vì hỏi chung chung."
            ),
        )

    # Strong code verb + target even without special markers is still likely work.
    if has_verb and has_target:
        return AgentChatRoute("execute", _compact(raw), "execution_request", 0.78)

    return AgentChatRoute(reason="normal_chat", confidence=0.65)


__all__ = ["AgentChatRoute", "classify_agent_chat_route"]

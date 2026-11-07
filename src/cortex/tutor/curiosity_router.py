# -*- coding: utf-8 -*-
"""
cortex/tutor/curiosity_router.py
================================

Định tuyến một Knowledge_Gap khi Eris không hiểu/không chắc:
- skip       : đã biết rồi hoặc không đáng hỏi → im, đỡ nhiễu.
- ask_user   : lỗ về BẠN / việc của bạn / mơ hồ → Eris hỏi lại bạn (nói ra).
- ask_gemini : kiến thức THẾ GIỚI → hỏi gia sư Gemini (âm thầm) rồi học.

Local LLM quyết (khôn), heuristic làm lưới đỡ. Đây là phần "cho nó khôn ra": không lén
Google chuyện riêng của bạn, không hỏi lại điều đã biết.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Optional, Tuple

from .contracts import KnowledgeGap

RouteAction = Literal["skip", "ask_user", "ask_gemini"]

GenerateFn = Callable[[str], str]
KnownFn = Callable[[str], bool]

_VALID = ("skip", "ask_user", "ask_gemini")

_PROMPT = (
    "ERIS (một AI đồng hành) không chắc về điều sau. Nên xử lý thế nào?\n"
    "- ask_user: nếu nó về NGƯỜI DÙNG, việc/ý của họ, hoặc câu họ vừa nói chưa rõ "
    "→ nên hỏi lại chính người dùng.\n"
    "- ask_gemini: nếu là KIẾN THỨC CHUNG về thế giới → nên tra cứu.\n"
    "- skip: nếu vụn vặt/không đáng hỏi.\n"
    "Điều chưa chắc: {description}\n"
    "Ngữ cảnh: {context}\n"
    "Chỉ trả JSON {{\"route\": \"ask_user|ask_gemini|skip\"}}."
)


class CuriosityRouter:
    def __init__(
        self,
        *,
        generate_fn: Optional[GenerateFn] = None,
        already_known_fn: Optional[KnownFn] = None,
    ) -> None:
        self._generate_fn = generate_fn
        self._already_known_fn = already_known_fn

    def route(self, gap: KnowledgeGap) -> Tuple[RouteAction, str]:
        # 1. Đã biết rồi → skip (chống hỏi lại + nhiễu).
        if self._is_known(gap.description):
            return ("skip", "already_known")

        # 2. Hint tường minh thắng.
        # Lưu ý lịch sử: kind_hint vốn là durable/context/banter (loại tri thức),
        # nhưng wiring cũ lại truyền "durable" như thể đó là route. Nếu chỉ so
        # với _VALID thì hint bị bỏ, route rơi về heuristic/LLM và trông như
        # "hỏi Gemini tật tật". Ở đây tách route hint và knowledge-kind hint.
        hint = str(getattr(gap, "kind_hint", "") or "").strip().lower()
        if hint in _VALID:
            forced = hint  # type: ignore[assignment]
        elif hint in {"durable", "context", "banter", "world", "knowledge"}:
            forced = "ask_gemini"
        elif hint in {"personal", "user", "clarify"}:
            forced = "ask_user"
        else:
            forced = None

        # 3. Heuristic nền theo nguồn.
        if gap.source == "expectation_violation":
            base: RouteAction = "ask_user"      # bất ngờ thường là về hành vi của bạn
        elif gap.source == "observer_unknown":
            base = "ask_gemini"                 # học app/site đó là gì
        else:
            base = "ask_gemini"

        if forced is not None:
            base = forced  # type: ignore[assignment]

        # 4. LLM tinh chỉnh (nếu có). Hint tường minh thì tin hint, không hỏi LLM.
        if forced is None:
            llm = self._llm_route(gap)
            if llm in _VALID:
                return (llm, "llm")  # type: ignore[return-value]
        return (base, "heuristic" if forced is None else "hint")

    # ------------------------------------------------------------------

    def _is_known(self, topic: str) -> bool:
        fn = self._already_known_fn
        if fn is None:
            return False
        try:
            return bool(fn(topic))
        except Exception:
            return False

    def _llm_route(self, gap: KnowledgeGap) -> str:
        fn = self._generate_fn
        if fn is None:
            return ""
        try:
            out = fn(_PROMPT.format(description=gap.description, context=gap.context))
        except Exception:
            return ""
        text = str(out or "").lower()
        for action in _VALID:
            if f'"{action}"' in text or f"route: {action}" in text or text.strip() == action:
                return action
        return ""


__all__ = ["CuriosityRouter", "RouteAction"]

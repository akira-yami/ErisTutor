# src/cortex/background_observer.py
"""
BackgroundObserver cho ERIS.

Bản này có 2 tầng:
1. Rule-based observer luôn chạy được, không phụ thuộc model.
2. Optional injected observer adapter de phan loai mem hon ve sau.

Luật cứng:
- Không trả lời user.
- Không commit memory.
- Không tạo Decision public.
- Chỉ trả ObserverFrame để Attention/WorkingMemory/BodyBias dùng.
"""

from __future__ import annotations
from core.di import resolve_dependency

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    from cortex.observer_contracts import (
        ActivityIntent,
        FocusKind,
        ObserverFrame,
        SignalDiff,
        SignalSnapshot,
        clamp01,
        coerce_activity_intent,
        coerce_focus_kind,
        coerce_observer_action,
        compact_text,
        normalize_ascii_fold,
    )
except Exception:
    from src.cortex.observer_contracts import (  # type: ignore
        ActivityIntent,
        FocusKind,
        ObserverFrame,
        SignalDiff,
        SignalSnapshot,
        clamp01,
        coerce_activity_intent,
        coerce_focus_kind,
        coerce_observer_action,
        compact_text,
        normalize_ascii_fold,
    )



_ERROR_PATTERNS = [
    r"\berror\b",
    r"\bexception\b",
    r"\btraceback\b",
    r"\bassertionerror\b",
    r"\bfailed\b",
    r"\bfail(?:ed|ure)?\b",
    r"\bpytest\b.*\bfailed\b",
    r"\bnulll?reference\b",
    r"\bsegmentation fault\b",
    r"\bpanic\b",
    r"\bcrash\b",
    r"\bloi\b",
    r"\bbug\b",
    r"\bsai\b",
]

_CODE_PATTERNS = [
    r"\bvscode\b",
    r"\bvisual studio code\b",
    r"\bpycharm\b",
    r"\brider\b",
    r"\bvisual studio\b",
    r"\bterminal\b",
    r"\bpowershell\b",
    r"\bcmd\.exe\b",
    r"\bpytest\b",
    r"\bpython\b",
    r"\bgodot\b",
    r"\b\.py\b",
    r"\b\.cs\b",
    r"\b\.gd\b",
    r"\b\.json\b",
    r"\b\.xml\b",
    r"\bgithub\b",
]

_DOC_PATTERNS = [
    r"\bpdf\b",
    r"\bword\b",
    r"\bdocs\b",
    r"\bdocument\b",
    r"\bnotion\b",
    r"\bobsidian\b",
    r"\breadme\b",
    r"\bmarkdown\b",
]

_BROWSER_PATTERNS = [
    r"\bchrome\b",
    r"\bedge\b",
    r"\bfirefox\b",
    r"\bbrave\b",
    r"\bgoogle\b",
    r"\bstackoverflow\b",
    r"\bchatgpt\b",
]

_MEDIA_PATTERNS = [
    r"\byoutube\b",
    r"\bspotify\b",
    r"\bnetflix\b",
    r"\btiktok\b",
    r"\bvideo\b",
    r"\bmusic\b",
]

_CHAT_PATTERNS = [
    r"\bdiscord\b",
    r"\btelegram\b",
    r"\bmessenger\b",
    r"\bzalo\b",
    r"\bchat\b",
]

_GAME_PATTERNS = [
    r"\bsteam\b",
    r"\brimworld\b",
    r"\bskyrim\b",
    r"\bgenshin\b",
    r"\bwuthering waves\b",
    r"\btoram\b",
]

_FRAGMENTED_EVENT_PATTERNS = [
    r"window_switch",
    r"alt_tab",
    r"switch",
    r"focus_changed",
]


@dataclass
class BackgroundObserverConfig:
    enabled: bool = field(default_factory=lambda: os.getenv("ERIS_BACKGROUND_OBSERVER", "1") == "1")
    llm_enabled: bool = field(
        default_factory=lambda: str(
            os.getenv("ERIS_SENSORY_ENABLED", os.getenv("ERIS_BACKGROUND_OBSERVER_LLM", "0"))
        ).strip().lower() in {"1", "true", "yes", "on"}
    )
    min_diff_for_llm: float = field(default_factory=lambda: float(os.getenv("ERIS_OBSERVER_LLM_MIN_DIFF", "0.18")))
    llm_cooldown_seconds: float = field(default_factory=lambda: float(os.getenv("ERIS_OBSERVER_LLM_COOLDOWN", "8")))

    vision_min_salience: float = field(default_factory=lambda: float(os.getenv("ERIS_VISION_COMPRESSOR_MIN_SALIENCE", "0.70")))
    vision_min_diff: float = field(default_factory=lambda: float(os.getenv("ERIS_VISION_COMPRESSOR_MIN_DIFF", "0.20")))

    wake_main_min_salience: float = field(default_factory=lambda: float(os.getenv("ERIS_WAKE_MAIN_MIN_SALIENCE", "0.88")))
    wake_main_min_error: float = field(default_factory=lambda: float(os.getenv("ERIS_WAKE_MAIN_MIN_ERROR", "0.90")))

    max_llm_confidence: float = 0.82
    max_observer_interrupt: bool = False  # observer không được tự interrupt ở bản này


class BackgroundObserver:
    def __init__(
        self,
        config: Optional[BackgroundObserverConfig] = None,
        sensory_adapter: Any = None,
    ):
        self.config = resolve_dependency(config, lambda: BackgroundObserverConfig())
        self.sensory_adapter = sensory_adapter
        self._last_llm_time: float = 0.0

    def observe(
        self,
        snapshot: SignalSnapshot,
        diff: Optional[SignalDiff] = None,
        *,
        recent_summary: str = "",
    ) -> ObserverFrame:
        """Trả ObserverFrame.

        Nếu LLM bị lỗi, fallback rule-based. Runtime không nên chết vì con model gác cổng
        ho khan một cái, đời đã đủ phiền.
        """

        if not self.config.enabled:
            return ObserverFrame(notes=["background_observer_disabled"])

        diff = diff or SignalDiff()
        rule_frame = self._rule_observe(snapshot, diff)
        final_frame = rule_frame

        if self._should_use_llm(diff):
            llm_frame = self._llm_observe(snapshot, diff, recent_summary=recent_summary)
            if llm_frame:
                final_frame = self._merge_frames(rule_frame, llm_frame)
                final_frame.debug["llm_used"] = True
            else:
                final_frame.debug["llm_used"] = False

        self._apply_action_gates(final_frame, diff)
        return final_frame

    def _rule_observe(self, snapshot: SignalSnapshot, diff: SignalDiff) -> ObserverFrame:
        combined = "\n".join(
            [
                snapshot.active_app,
                snapshot.active_window_title,
                snapshot.focus_kind_hint,
                snapshot.url_or_path_hint,
                snapshot.ocr_text,
                "\n".join(snapshot.recent_events[-10:]),
            ]
        )
        text = normalize_ascii_fold(combined)

        focus_kind = self._classify_focus(snapshot, text)
        possible_error = self._score_possible_error(text)
        fragmented = self._score_fragmentation(snapshot, text)
        user_presence = self._score_user_presence(snapshot)
        activity_intent = self._classify_activity(snapshot, focus_kind, possible_error, fragmented, text)

        novelty = clamp01(max(diff.novelty, diff.diff_score))
        repetition = clamp01(diff.repetition)
        ambiguity = self._score_ambiguity(snapshot, focus_kind, activity_intent, diff)

        salience = self._score_salience(
            snapshot=snapshot,
            diff=diff,
            focus_kind=focus_kind,
            activity_intent=activity_intent,
            possible_error=possible_error,
            fragmented=fragmented,
        )

        recommended_action = "observe_only"
        if salience >= 0.42:
            recommended_action = "update_body_bias"
        if salience >= self.config.vision_min_salience and diff.should_consider_vision(self.config.vision_min_diff):
            recommended_action = "call_vision_compressor"
        if salience >= self.config.wake_main_min_salience and possible_error >= self.config.wake_main_min_error:
            recommended_action = "wake_main_cognition"

        recommended_budget = "fast"
        if salience >= 0.55 or possible_error >= 0.55:
            recommended_budget = "normal"
        if salience >= 0.82 or possible_error >= 0.82:
            recommended_budget = "deep"

        evidence = list(diff.reason)
        if possible_error >= 0.55:
            evidence.append("possible_error_pattern")
        if fragmented >= 0.55:
            evidence.append("fragmented_activity_pattern")
        if focus_kind != "unknown":
            evidence.append(f"focus:{focus_kind}")

        confidence = 0.45
        if focus_kind != "unknown":
            confidence += 0.10
        if diff.diff_score >= 0.20:
            confidence += 0.08
        if possible_error >= 0.60:
            confidence += 0.08
        if ambiguity >= 0.60:
            confidence -= 0.10

        return ObserverFrame(
            focus_kind=focus_kind,
            activity_intent=activity_intent,
            salience=salience,
            novelty=novelty,
            repetition=repetition,
            ambiguity=ambiguity,
            possible_error=possible_error,
            user_presence=user_presence,
            recommended_action=recommended_action,  # type: ignore[arg-type]
            recommended_budget=recommended_budget,  # type: ignore[arg-type]
            confidence=clamp01(confidence),
            should_call_vision=recommended_action == "call_vision_compressor",
            should_wake_main=recommended_action == "wake_main_cognition",
            should_interrupt_user=False,
            evidence=evidence,
            notes=["rule_based_observer"],
            debug={
                "diff_score": diff.diff_score,
                "fragmented": fragmented,
                "idle_transition": diff.idle_transition,
            },
        )

    def _should_use_llm(self, diff: SignalDiff) -> bool:
        if not self.config.llm_enabled:
            return False
        if self.sensory_adapter is None:
            return False
        if diff.diff_score < self.config.min_diff_for_llm and not diff.ocr_changed and not diff.event_changed:
            return False
        now = time.time()
        if now - self._last_llm_time < self.config.llm_cooldown_seconds:
            return False
        return True

    def _llm_observe(
        self,
        snapshot: SignalSnapshot,
        diff: SignalDiff,
        *,
        recent_summary: str = "",
    ) -> Optional[ObserverFrame]:
        if self.sensory_adapter is not None and callable(getattr(self.sensory_adapter, "observe", None)):
            data = self.sensory_adapter.observe(
                snapshot=snapshot,
                diff=diff,
                recent_summary=recent_summary,
            )
            self._last_llm_time = time.time()
            if isinstance(data, dict):
                return self._frame_from_llm_data(data)
            return None
        return None

    def _frame_from_llm_data(self, data: dict[str, Any]) -> ObserverFrame:
        notes = data.get("notes") or []
        if isinstance(notes, str):
            notes = [notes]
        if not isinstance(notes, list):
            notes = []

        return ObserverFrame(
            focus_kind=coerce_focus_kind(data.get("focus_kind")),
            activity_intent=coerce_activity_intent(data.get("activity_intent")),
            salience=clamp01(data.get("salience")),
            novelty=clamp01(data.get("novelty")),
            repetition=clamp01(data.get("repetition")),
            ambiguity=clamp01(data.get("ambiguity")),
            possible_error=clamp01(data.get("possible_error")),
            user_presence=clamp01(data.get("user_presence")),
            recommended_action=coerce_observer_action(data.get("recommended_action")),
            confidence=min(clamp01(data.get("confidence")), self.config.max_llm_confidence),
            should_interrupt_user=False,
            notes=[compact_text(str(x), 160) for x in notes[:6]] + ["llm_observer"],
            debug={"llm_raw": data},
        )

    def _merge_frames(self, rule: ObserverFrame, llm: ObserverFrame) -> ObserverFrame:
        """Merge rule và LLM theo kiểu rule là xương sống an toàn."""

        focus_kind = llm.focus_kind if llm.focus_kind != "unknown" else rule.focus_kind
        activity_intent = llm.activity_intent if llm.activity_intent != "unknown" else rule.activity_intent

        possible_error = max(rule.possible_error, llm.possible_error)
        novelty = max(rule.novelty * 0.75, llm.novelty * 0.90)
        repetition = max(rule.repetition, llm.repetition * 0.85)
        ambiguity = max(rule.ambiguity, llm.ambiguity)
        user_presence = max(rule.user_presence * 0.70, llm.user_presence * 0.85)

        # LLM được ảnh hưởng salience, nhưng không được tự thổi quá xa rule.
        salience = max(rule.salience, min(1.0, llm.salience * 0.92))
        if possible_error >= 0.75:
            salience = max(salience, rule.salience + 0.08)
        salience = clamp01(salience)

        action = rule.recommended_action
        if llm.recommended_action == "call_vision_compressor" and salience >= self.config.vision_min_salience:
            action = "call_vision_compressor"
        elif llm.recommended_action == "wake_main_cognition" and salience >= self.config.wake_main_min_salience:
            # Wake main chỉ khi rule cũng thấy error/salience cao. Không cho model nhỏ lên đồng.
            if possible_error >= self.config.wake_main_min_error or rule.should_wake_main:
                action = "wake_main_cognition"
        elif action == "observe_only" and salience >= 0.42:
            action = "update_body_bias"

        confidence = min(max(rule.confidence, llm.confidence * 0.95), self.config.max_llm_confidence)

        return ObserverFrame(
            focus_kind=focus_kind,
            activity_intent=activity_intent,
            salience=salience,
            novelty=novelty,
            repetition=repetition,
            ambiguity=ambiguity,
            possible_error=possible_error,
            user_presence=user_presence,
            recommended_action=action,  # type: ignore[arg-type]
            recommended_budget=rule.recommended_budget,
            confidence=confidence,
            should_interrupt_user=False,
            evidence=list(dict.fromkeys(rule.evidence + llm.evidence)),
            notes=list(dict.fromkeys(rule.notes + llm.notes)),
            debug={"rule": rule.debug, "llm": llm.debug},
        )

    def _apply_action_gates(self, frame: ObserverFrame, diff: SignalDiff) -> None:
        """Gắn các cờ cuối sau khi merge."""

        frame.should_call_vision = (
            frame.recommended_action == "call_vision_compressor"
            and frame.salience >= self.config.vision_min_salience
            and diff.should_consider_vision(self.config.vision_min_diff)
        )

        frame.should_wake_main = (
            frame.recommended_action == "wake_main_cognition"
            and frame.salience >= self.config.wake_main_min_salience
            and frame.possible_error >= self.config.wake_main_min_error
        )

        # Ở bản này observer tuyệt đối không tự chen lời public.
        frame.should_interrupt_user = False

        if frame.should_call_vision:
            frame.recommended_action = "call_vision_compressor"
        elif frame.should_wake_main:
            frame.recommended_action = "wake_main_cognition"
        elif frame.salience >= 0.42:
            frame.recommended_action = "update_body_bias"
        else:
            frame.recommended_action = "observe_only"

        if frame.salience >= 0.82 or frame.possible_error >= 0.82:
            frame.recommended_budget = "deep"
        elif frame.salience >= 0.55 or frame.possible_error >= 0.55:
            frame.recommended_budget = "normal"
        else:
            frame.recommended_budget = "fast"

    def _classify_focus(self, snapshot: SignalSnapshot, text: str) -> FocusKind:
        hint = coerce_focus_kind(snapshot.focus_kind_hint)
        if hint != "unknown":
            return hint
        if self._any_pattern(text, _CODE_PATTERNS):
            return "code"
        if self._any_pattern(text, _DOC_PATTERNS):
            return "docs"
        if self._any_pattern(text, _MEDIA_PATTERNS):
            return "media"
        if self._any_pattern(text, _CHAT_PATTERNS):
            return "chat"
        if self._any_pattern(text, _GAME_PATTERNS):
            return "game"
        if self._any_pattern(text, _BROWSER_PATTERNS):
            return "browser"
        if any(x in text for x in ["explorer", "settings", "task manager", "control panel"]):
            return "system"
        return "unknown"

    def _classify_activity(
        self,
        snapshot: SignalSnapshot,
        focus_kind: FocusKind,
        possible_error: float,
        fragmented: float,
        text: str,
    ) -> ActivityIntent:
        if fragmented >= 0.65:
            return "fragmented"
        if focus_kind == "code" and possible_error >= 0.55:
            return "debugging"
        if focus_kind == "code":
            if any(x in text for x in ["design", "architecture", "schema", "pipeline", "class ", "def "]):
                return "designing"
            return "debugging" if possible_error >= 0.35 else "designing"
        if focus_kind in {"docs", "browser"}:
            if any(x in text for x in ["search", "google", "stackoverflow", "github issue"]):
                return "searching"
            return "reading"
        if focus_kind == "chat":
            return "communicating"
        if focus_kind in {"media", "game"}:
            return "resting"
        if snapshot.idle_seconds >= 120:
            return "idle"
        return "unknown"

    def _score_possible_error(self, text: str) -> float:
        hits = sum(1 for pattern in _ERROR_PATTERNS if re.search(pattern, text, flags=re.IGNORECASE))
        if hits <= 0:
            return 0.0
        if hits == 1:
            return 0.58
        if hits == 2:
            return 0.76
        return 0.90

    def _score_fragmentation(self, snapshot: SignalSnapshot, text: str) -> float:
        event_text = normalize_ascii_fold("\n".join(snapshot.recent_events[-12:]))
        hits = sum(1 for pattern in _FRAGMENTED_EVENT_PATTERNS if re.search(pattern, event_text))
        if hits >= 3:
            return 0.85
        if hits == 2:
            return 0.65
        if hits == 1:
            return 0.38
        if "alt tab" in text or "window switch" in text:
            return 0.45
        return 0.0

    def _score_user_presence(self, snapshot: SignalSnapshot) -> float:
        if snapshot.chat_visible:
            return 0.78
        idle = snapshot.idle_seconds
        recent_text = normalize_ascii_fold("\n".join(snapshot.recent_events[-8:]))
        interaction = any(x in recent_text for x in ["key", "keyboard", "mouse", "click", "scroll", "selection", "edit"])
        if interaction and idle < 60:
            return 0.76
        if idle < 8:
            return 0.68
        if idle < 30:
            return 0.58
        if idle < 120:
            return 0.38
        return 0.18

    def _score_ambiguity(
        self,
        snapshot: SignalSnapshot,
        focus_kind: FocusKind,
        activity_intent: ActivityIntent,
        diff: SignalDiff,
    ) -> float:
        ambiguity = 0.20
        if focus_kind == "unknown":
            ambiguity += 0.28
        if activity_intent == "unknown":
            ambiguity += 0.20
        if not snapshot.ocr_text and not snapshot.active_window_title:
            ambiguity += 0.20
        if diff.diff_score < 0.08:
            ambiguity += 0.10
        return clamp01(ambiguity)

    def _score_salience(
        self,
        *,
        snapshot: SignalSnapshot,
        diff: SignalDiff,
        focus_kind: FocusKind,
        activity_intent: ActivityIntent,
        possible_error: float,
        fragmented: float,
    ) -> float:
        salience = 0.08
        salience += 0.55 * diff.diff_score
        salience += 0.30 * diff.novelty
        salience += 0.34 * possible_error
        salience += 0.12 * fragmented

        if focus_kind == "code":
            salience += 0.10
        elif focus_kind in {"media", "game"}:
            salience -= 0.08

        if activity_intent == "debugging":
            salience += 0.08
        elif activity_intent in {"resting", "idle"}:
            salience -= 0.06

        if snapshot.idle_seconds >= 25 and possible_error >= 0.55:
            salience += 0.12

        # Lặp cao thì novelty giảm, nhưng error loop vẫn giữ một phần salience.
        salience -= 0.18 * diff.repetition
        if possible_error >= 0.70 and diff.repetition >= 0.50:
            salience += 0.08

        return clamp01(salience)

    @staticmethod
    def _any_pattern(text: str, patterns: list[str]) -> bool:
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

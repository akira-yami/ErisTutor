"""Activation policy: when should Eris autonomously enter a tutor mode?

This is the layer that DeepTutor lacks and Eris companion loop enables.
It decides *when* to step into learning mode, not *what* to teach.

Four activation levels:
  normal       – just answer; no teaching episode
  microteach   – one inline hint/clarification, no path created
  episode      – open a multi-step loop for 1–3 concepts
  mastery_path – long-term path (requires user consent)
  due_review   – SRS-driven proactive review (companion autonomous)

Decision inputs:
  - User signals (repeated question, misunderstanding, explicit learning)
  - Conversation trajectory (topic repetition, depth escalation)
  - LearningService state (due reviews, open interactions)
  - CompanionMentorRoleOrchestrator directive (existing stance)
  - Cooldown / snooze state

This module is pure logic; no LLM calls, no I/O.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence


ActivationLevel = Literal["normal", "microteach", "episode", "mastery_path", "due_review"]

_ACTIVATION_PRIORITY: dict[str, int] = {
    "normal": 0,
    "microteach": 1,
    "episode": 2,
    "due_review": 3,
    "mastery_path": 4,
}

# User signals that strongly indicate teaching opportunity
_LEARNING_SIGNALS = frozenset({
    "learning_intent",
    "repeated_question",
    "misconception_detected",
    "depth_escalation",
    "feynman_attempt",
})

# Signals that should suppress teaching (user wants quick answer / exit)
_SUPPRESS_SIGNALS = frozenset({
    "topic_change",
    "fast_execution_request",
    "blind_execution_request",
    "dismissal",
    "time_pressure",
})

# Minimum seconds between autonomous teaching initiations
_DEFAULT_COOLDOWN = 10 * 60.0        # 10 min
_MICROTEACH_COOLDOWN = 2 * 60.0      # 2 min
_REVIEW_COOLDOWN = 30 * 60.0         # 30 min


@dataclass(frozen=True, slots=True)
class ActivationDecision:
    level: ActivationLevel
    reason: str
    kp_id: str = ""           # primary KP for episode/review; empty for normal
    path_id: str = ""
    confidence: float = 0.0   # [0, 1]
    requires_consent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "reason": self.reason,
            "kp_id": self.kp_id,
            "path_id": self.path_id,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "requires_consent": self.requires_consent,
        }


@dataclass(slots=True)
class ActivationCooldown:
    """Mutable cooldown tracker (per-service, in-memory)."""

    last_episode_at: float = 0.0
    last_microteach_at: float = 0.0
    last_review_at: float = 0.0
    snoozed_until: float = 0.0

    def snoozed(self, now: float) -> bool:
        return self.snoozed_until > now

    def cooldown_ok(self, level: ActivationLevel, now: float) -> bool:
        if self.snoozed(now):
            return False
        if level == "microteach":
            return now - self.last_microteach_at >= _MICROTEACH_COOLDOWN
        if level in {"episode", "mastery_path"}:
            return now - self.last_episode_at >= _DEFAULT_COOLDOWN
        if level == "due_review":
            return now - self.last_review_at >= _REVIEW_COOLDOWN
        return True

    def record(self, level: ActivationLevel, now: float) -> None:
        if level == "microteach":
            self.last_microteach_at = now
        elif level in {"episode", "mastery_path"}:
            self.last_episode_at = now
        elif level == "due_review":
            self.last_review_at = now

    def snooze(self, seconds: float, now: float) -> None:
        self.snoozed_until = now + max(60.0, float(seconds))


def decide_activation(
    user_text: str,
    *,
    user_signals: Sequence[str] = (),
    companion_primary_role: str = "companion",
    companion_mentor_intensity: str = "none",
    has_due_reviews: bool = False,
    due_review_kp_id: str = "",
    due_review_path_id: str = "",
    due_review_risk: float = 0.0,
    active_path_id: str = "",
    active_kp_id: str = "",
    cooldown: ActivationCooldown | None = None,
    now: float | None = None,
) -> ActivationDecision:
    """Pure function: decide activation level for current turn.

    Parameters mirror what the orchestrator has available without pulling in
    any runtime subsystem dependencies.
    """
    ts = float(now if now is not None else time.time())
    cd = cooldown or ActivationCooldown()
    signals = frozenset(str(s) for s in user_signals)
    low = " ".join(str(user_text or "").split()).casefold()

    # ── Suppress teaching entirely ──────────────────────────────────────────
    if signals & _SUPPRESS_SIGNALS:
        return ActivationDecision(level="normal", reason="suppressed_by_signal", confidence=0.95)

    # ── Explicit learning intent: companion already set to mentor ───────────
    if "learning_intent" in signals or companion_primary_role == "mentor":
        explicit = True
        # Prefer episode if we have an active path
        if active_path_id and cd.cooldown_ok("episode", ts):
            return ActivationDecision(
                level="episode",
                reason="explicit_learning_with_active_path",
                kp_id=active_kp_id,
                path_id=active_path_id,
                confidence=0.92,
            )
        # Otherwise microteach: inline without opening a formal path
        if cd.cooldown_ok("microteach", ts):
            return ActivationDecision(
                level="microteach",
                reason="explicit_learning_no_path",
                confidence=0.88,
            )

    # ── Due review from SRS (companion autonomous) ──────────────────────────
    if has_due_reviews and due_review_risk >= 0.6 and cd.cooldown_ok("due_review", ts):
        return ActivationDecision(
            level="due_review",
            reason="spaced_review_due",
            kp_id=due_review_kp_id,
            path_id=due_review_path_id,
            confidence=min(0.95, 0.60 + due_review_risk * 0.35),
        )

    # ── Misconception detected: open repair episode ─────────────────────────
    if "misconception_detected" in signals and cd.cooldown_ok("episode", ts):
        return ActivationDecision(
            level="episode",
            reason="misconception_detected",
            kp_id=active_kp_id,
            path_id=active_path_id,
            confidence=0.87,
        )

    # ── Repeated question / depth escalation: microteach ───────────────────
    if ("repeated_question" in signals or "depth_escalation" in signals) and cd.cooldown_ok("microteach", ts):
        return ActivationDecision(
            level="microteach",
            reason="repeated_question_or_depth",
            confidence=0.80,
        )

    # ── Companion hint/challenge mode: microteach inline ───────────────────
    if companion_mentor_intensity in {"light", "normal"} and cd.cooldown_ok("microteach", ts):
        return ActivationDecision(
            level="microteach",
            reason="companion_hint_mode",
            confidence=0.72,
        )

    # ── Default: normal turn ────────────────────────────────────────────────
    return ActivationDecision(level="normal", reason="no_trigger", confidence=0.90)


__all__ = [
    "ActivationCooldown",
    "ActivationDecision",
    "ActivationLevel",
    "decide_activation",
]

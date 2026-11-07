"""Edge-triggered INFJ native assets; stage/audio retain their parameter ownership."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EXPRESSION_ALIASES = {
    "neutral": "calm", "flat_calm": "calm", "calm": "calm",
    "warm_soft": "warm", "soft_near": "warm", "warm": "warm",
    "small_smile": "warm", "warm_playful": "amused", "amused": "amused",
    "dry": "dry", "deadpan": "dry", "dry_tease": "dry",
    "mischief": "teasing", "mischief_soft": "teasing", "teasing": "teasing",
    "curious": "curious", "curious_soft": "curious", "thinking": "curious",
    "focused": "focused", "focus": "focused", "flat_cold": "focused",
    "quiet_low": "low", "quiet_soft": "low", "tired_soft": "low",
    "distant": "low", "low": "low", "skeptical": "skeptical",
    "concerned": "concerned", "annoyed_soft": "annoyed_soft",
    "surprise": "surprised_soft", "surprised_soft": "surprised_soft",
    # BIEUCAM: tên preset mới phải đi trực tiếp đến native SetExpression.
    "smirk_minor": "smirk_minor", "small_smirk": "smirk_minor",
    "smirk_disdain": "smirk_disdain", "disdain_smirk": "smirk_disdain",
    "blush_affection": "blush_affection", "blush": "blush_affection",
    "warm_embarrassed": "blush_affection", "embarrassed": "blush_affection", "shy": "blush_affection",
    "cat_mouth": "cat_mouth", "cat_smile": "cat_mouth",
    "sad_empathy": "sad_empathy", "empathetic_sad": "sad_empathy",
    "wink_correct": "wink_correct", "wink_success": "wink_correct",
    "teehee": "teehee", "playful_teehee": "teehee",
    "wink_correct_right": "wink_correct_right",
    "teehee_right": "teehee_right",
    "yan_warning": "yan_warning", "yandere_warning": "yan_warning",
    "lazy_deadpan": "lazy_deadpan", "lazy_stare": "lazy_deadpan",
}

MOTION_ALIASES = {
    "stand": "settle", "idle": "settle", "settle": "settle",
    "thinking": "ponder_left", "ponder": "ponder_left",
    "thinking_small": "ponder_left", "read": "look_down_soft",
    "look_away": "glance_left", "look_left": "glance_left",
    "look_aside": "glance_left", "sad": "look_down_soft",
    "look_right": "glance_right", "look_down": "look_down_soft",
    "look_up": "look_up_soft", "scan": "scan_short",
    "tilt_head": "tilt_right", "tilt": "tilt_right",
    "shake_head": "shake_small", "shake": "shake_small",
    # These cues have procedural stage/speech choreography, not an invented clip.
    "still": None, "focus": None, "small_smile": None,
    "blink_slow": None, "slow_blink": None, "wink": None,
    "nod": None, "nod_small": None, "lean_back_small": None, "lean_in_small": None, "soften": None,
}

TERMINAL_EVENTS = frozenset({
    "audio_done", "audio_playback_done", "audio_cancel", "audio_cancelled",
    "audio_playback_cancelled", "audio_failed", "audio_localization_failed",
    "stage_segment_cancelled", "stage_segment_failed", "continuation_cancelled",
    "cancel", "turn_cancelled", "interrupted", "done", "degraded",
})
SEMANTIC_EVENTS = frozenset({
    "stage_segment", "speaking", "thinking", "received", "thinking_started",
    "thinking_finished", "content_committed", "audio_buffering", "turn_projection",
    "audio_start", "audio_playback_started",
})


class NativeAssetPlayback:
    def __init__(self, model_path: Path) -> None:
        try:
            descriptor = json.loads(model_path.read_text(encoding="utf-8-sig"))
            refs = descriptor.get("FileReferences", {})
            self.jaw_linked_tongue = descriptor.get("ErisMouthRig") == "V13.2"
        except (OSError, ValueError, AttributeError):
            refs = {}
            self.jaw_linked_tongue = False
        self.expressions = {item["Name"] for item in refs.get("Expressions", [])}
        self.motions = {
            Path(item["File"]).name.removesuffix(".motion3.json"): (group, index)
            for group, entries in refs.get("Motions", {}).items()
            for index, item in enumerate(entries)
        }
        self._expression: str | None = None
        self._motion_key: tuple | None = None
        self._event_key: tuple | None = None
        self._semantic_key: tuple | None = None
        self._speaking = False
        self._terminal = False

    @property
    def terminal(self) -> bool:
        return self._terminal

    def advance(self, model: Any, snapshot: Any) -> None:
        event = snapshot.last_event_type
        event_key = (event, getattr(snapshot, "last_event_at", 0.0))
        event_edge = event_key != self._event_key
        # The stage has already resolved the audio owner. An ignored stale terminal
        # mirror leaves speaking true and must not cancel its expression/motion.
        ended = not snapshot.speaking and ((event_edge and event in TERMINAL_EVENTS) or self._speaking)
        if ended:
            model.StopAllMotions()
            reset = getattr(model, "ResetExpressions", None)
            if callable(reset):
                reset()
            self._expression = None
            self._motion_key = None
            self._terminal = True
        elif event_edge and event in SEMANTIC_EVENTS:
            self._terminal = False
        elif snapshot.speaking:
            self._terminal = False
        if event_edge and event in SEMANTIC_EVENTS:
            self._semantic_key = event_key

        expression = "calm" if self._terminal else EXPRESSION_ALIASES.get(snapshot.emotion, "calm")
        if expression != self._expression and expression in self.expressions:
            model.SetExpression(expression)
            self._expression = expression

        label = snapshot.motion
        idle = not snapshot.speaking and label in {"stand", "idle", "settle"}
        if idle:
            label = snapshot.idle_action
        name = MOTION_ALIASES.get(label, label)
        # Affect/heartbeat events cannot restart the same choreography every frame.
        trigger = self._semantic_key if not idle else None
        motion_key = (name, trigger)
        if not self._terminal and motion_key != self._motion_key:
            if name in self.motions:
                group, index = self.motions[name]
                model.StartMotion(group, index, 3)
            elif name is None:
                model.StopAllMotions()
            self._motion_key = motion_key
        self._event_key = event_key
        self._speaking = snapshot.speaking

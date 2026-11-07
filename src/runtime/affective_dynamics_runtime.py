# -*- coding: utf-8 -*-
"""Runtime owner cho event-driven affect V43.

Model có thể giúp hiểu semantic ở các lane khác, nhưng module này giữ authority
state: event nào đã áp dụng, decay bao nhiêu và PersistentInternalState đổi ra
sao đều là deterministic/runtime-owned.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Mapping

from cortex.affective_dynamics import (
    AFFECTIVE_DYNAMICS_VERSION,
    AffectEventReceipt,
    AffectiveDynamicsEngine,
)
from cortex.cognitive_event import CognitiveEvent, CognitiveEventLedger
from runtime.social_appraisal import ensure_social_appraisal_engine


class AffectiveDynamicsRuntime:
    def __init__(self, host: Any, *, logger: Any | None = None) -> None:
        self.host = host
        self.logger = logger or getattr(host, "logger", None)
        self.engine = AffectiveDynamicsEngine()
        self._ledger = CognitiveEventLedger(max_events=2048, ttl_seconds=24.0 * 3600.0)
        self._lock = threading.RLock()

    def _disposition(self) -> Mapping[str, Any]:
        # Personality mood là nền chậm. Nó không được gọi là current affect và
        # không được overwrite event impulse. Profile chưa sẵn sàng thì engine
        # dùng baseline neutral/default của chính PersistentInternalState.
        raw = getattr(self.host, "latest_personality_profile", {})
        return raw if isinstance(raw, Mapping) else {}

    def decay_state(self, state: Any, *, now: float | None = None) -> Any:
        """Chỉ cho thời gian trôi. Không tạo event, không thay nghĩa conversation."""
        return self.engine.decay(state, now=now, disposition=self._disposition())

    def ingest_user_turn(
        self,
        user_text: str,
        *,
        turn_id: str,
        previous_assistant_text: str = "",
        previous_user_text: str = "",
        now: float | None = None,
    ) -> AffectEventReceipt:
        current = float(now if now is not None else time.time())
        event = CognitiveEvent.from_user_turn(
            user_text,
            turn_id=turn_id,
            now=current,
        )
        if event is None:
            return AffectEventReceipt("", "user_turn", False, reason="empty_user_event")
        with self._lock:
            if not self._ledger.accept(event, now=current):
                return AffectEventReceipt(event.event_id, event.kind, False, reason="duplicate_event")
            try:
                state = self.host._get_internal_state()
                social = ensure_social_appraisal_engine(self.host).appraise(
                    user_text=user_text,
                    previous_assistant_text=previous_assistant_text,
                    previous_user_text=previous_user_text,
                    phase="affect_v43",
                )
                delta = self.engine.appraise_user_event(event, social_appraisal=social, state=state)
                updated = self.engine.apply_event(state, event, delta=delta, now=current, disposition=self._disposition())
                self.host._set_internal_state(updated)
            except Exception as exc:
                self._log("warning", "[affect event] user ingest failed id=%s error=%s", event.event_id, exc)
                return AffectEventReceipt(event.event_id, event.kind, False, reason="runtime_error")
        self._log_receipt(event, delta, updated)
        return AffectEventReceipt(event.event_id, event.kind, True, delta=delta, reason="applied")

    def ingest_subject_appraisal(self, appraisal: Any, *, now: float | None = None) -> AffectEventReceipt:
        """Áp một SubjectAppraisal đúng một lần vào current affect.

        Event ID dựa trên appraisal_id nên retry cùng frame không được cộng mood
        lần hai. Đây là modulation hậu-appraisal, nhỏ hơn social event gốc.
        """
        appraisal_id = str(getattr(appraisal, "appraisal_id", "") or "").strip()
        if not appraisal_id:
            return AffectEventReceipt("", "goal_appraisal", False, reason="missing_appraisal_id")
        current = float(now if now is not None else time.time())
        refs = tuple(str(item) for item in getattr(appraisal, "evidence_refs", ()) if str(item or "").strip())
        event = CognitiveEvent(
            event_id=f"cogevt:appraisal:{appraisal_id}",
            origin="subject_appraisal",
            kind="goal_appraisal",
            subject=str(getattr(appraisal, "primary_interpretation", "") or "goal-relative appraisal")[:360],
            occurred_at=current,
            evidence_refs=refs or (appraisal_id,),
            source_ref=appraisal_id,
            novelty_key=f"subject-appraisal:{appraisal_id}",
            metadata={
                "goal_congruence": float(getattr(appraisal, "goal_congruence", 0.0) or 0.0),
                "goal_progress_delta": float(getattr(appraisal, "goal_progress_delta", 0.0) or 0.0),
                "uncertainty": float(getattr(appraisal, "uncertainty", 0.5) or 0.5),
            },
        )
        with self._lock:
            if not self._ledger.accept(event, now=current):
                return AffectEventReceipt(event.event_id, event.kind, False, reason="duplicate_event")
            try:
                state = self.host._get_internal_state()
                delta = self.engine.appraise_goal_appraisal(appraisal)
                if delta.strength < 0.003:
                    return AffectEventReceipt(event.event_id, event.kind, False, delta=delta, reason="no_affect_delta")
                updated = self.engine.apply_event(
                    state, event, delta=delta, now=current, disposition=self._disposition()
                )
                self.host._set_internal_state(updated)
            except Exception as exc:
                self._log("warning", "[affect event] subject appraisal failed id=%s error=%s", event.event_id, exc)
                return AffectEventReceipt(event.event_id, event.kind, False, reason="runtime_error")
        self._log_receipt(event, delta, updated)
        return AffectEventReceipt(event.event_id, event.kind, True, delta=delta, reason="applied")

    def ingest_cognitive_event(self, event: CognitiveEvent, *, now: float | None = None) -> AffectEventReceipt:
        """Nhận seed/internal event đã có identity V42, không tái diễn giải câu Surface."""
        if not isinstance(event, CognitiveEvent):
            return AffectEventReceipt("", "", False, reason="invalid_event")
        current = float(now if now is not None else event.occurred_at or time.time())
        with self._lock:
            if not self._ledger.accept(event, now=current):
                return AffectEventReceipt(event.event_id, event.kind, False, reason="duplicate_event")
            try:
                state = self.host._get_internal_state()
                delta = self.engine.appraise_cognitive_event(event)
                # Event không có affect meaning thì vẫn được receipt, nhưng không
                # cần ghi state xuống đĩa chỉ để tăng một counter vô nghĩa.
                if delta.strength < 0.005:
                    return AffectEventReceipt(event.event_id, event.kind, False, delta=delta, reason="no_affect_delta")
                updated = self.engine.apply_event(state, event, delta=delta, now=current, disposition=self._disposition())
                self.host._set_internal_state(updated)
            except Exception as exc:
                self._log("warning", "[affect event] cognitive ingest failed id=%s error=%s", event.event_id, exc)
                return AffectEventReceipt(event.event_id, event.kind, False, reason="runtime_error")
        self._log_receipt(event, delta, updated)
        return AffectEventReceipt(event.event_id, event.kind, True, delta=delta, reason="applied")

    def snapshot(self) -> dict[str, Any]:
        try:
            state = self.host._get_internal_state()
        except Exception:
            return {"version": AFFECTIVE_DYNAMICS_VERSION, "available": False}
        payload = dict(getattr(state, "affective_state", {}) or {})
        return {
            "version": AFFECTIVE_DYNAMICS_VERSION,
            "available": True,
            "event_count": int(payload.get("event_count", 0) or 0),
            "last_event_id": str(payload.get("last_event_id", "") or ""),
            "last_event_kind": str(payload.get("last_event_kind", "") or ""),
            "last_labels": tuple(payload.get("last_labels", ()) or ()),
            "last_delta_strength": float(payload.get("last_delta_strength", 0.0) or 0.0),
            "ledger": self._ledger.snapshot(),
        }

    def _log_receipt(self, event: CognitiveEvent, delta: Any, state: Any) -> None:
        affect = dict(getattr(state, "affective_state", {}) or {})
        impulse = affect.get("impulse") if isinstance(affect.get("impulse"), dict) else {}
        self._log(
            "info",
            "[affect event] applied id=%s origin=%s kind=%s strength=%.2f labels=%s delta=%s",
            event.event_id,
            event.origin,
            event.kind,
            float(getattr(delta, "strength", 0.0) or 0.0),
            ",".join(tuple(getattr(delta, "labels", ()) or ())) or "none",
            delta.compact() if callable(getattr(delta, "compact", None)) else "none",
        )
        self._log(
            "info",
            "[affect state] valence=%+.2f arousal=%.2f curiosity=%.2f social=%.2f irritation=%.2f tenderness=%.2f play_pressure=%.2f impulse_play=%+.2f impulse_delight=%+.2f",
            float(getattr(state, "valence", 0.0) or 0.0),
            float(getattr(state, "arousal", 0.0) or 0.0),
            float(getattr(state, "curiosity", 0.0) or 0.0),
            float(getattr(state, "social_energy", 0.0) or 0.0),
            float(getattr(state, "irritation", 0.0) or 0.0),
            float(getattr(state, "tenderness", 0.0) or 0.0),
            float(getattr(state, "playful_pressure", 0.0) or 0.0),
            float(impulse.get("playfulness", 0.0) or 0.0),
            float(impulse.get("delight", 0.0) or 0.0),
        )
        # Body là consumer của cùng state. Emit fail-soft ngay sau event để
        # avatar không phải chờ tick nền kế tiếp mới biết Eris vừa đổi mood.
        try:
            from cortex.affective_dynamics import effective_affect_values
            from runtime.live2d_affect_bridge import emit_runtime_affect
            emit_runtime_affect(
                self.host,
                affect=effective_affect_values(state),
                source="affect_v43",
                confidence=min(1.0, 0.45 + float(getattr(delta, "strength", 0.0) or 0.0)),
                force=True,
            )
        except Exception:
            pass

    def _log(self, level: str, message: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            fn(message, *args)


__all__ = ["AffectiveDynamicsRuntime"]

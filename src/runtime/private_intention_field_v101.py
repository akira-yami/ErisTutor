# -*- coding: utf-8 -*-
"""ERIS V101 - Private Intention Field.

V101 tách giai đoạn "material sẵn sàng biểu đạt" khỏi "projection sang V44 urge".
Production không đưa mental material thẳng sang urge nữa. PrivateIntentionField
tích hợp cùng semantic object qua nhiều tick, giữ trạng thái riêng:

    forming     – material mới nhận, chưa đủ tích lũy
    considering – đang tích hợp, semantic target ổn định
    ready       – đủ điều kiện projection sang V44 urge runtime
    cooling     – đã projection hoặc bị cancel, giảm charge

Các invariant:
- không thread, không LLM/TTS, không public SPEAK authority;
- một observation không đủ để ready, dù pressure rất cao;
- semantic target thay đổi mạnh reset maturity về forming;
- snapshot() trả state hiện tại để diagnostic; không mutate từ bên ngoài.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any


INTENTION_FIELD_SCHEMA = "eris.private_intention_field.v101"
INTENTION_FIELD_OWNER = "private_intention_field_v101"

_STAGES = ("forming", "considering", "ready", "cooling")


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _compact(value: Any, limit: int = 900) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


@dataclass(slots=True)
class IntentionSlotV101:
    """State cục bộ của một intention đang tích hợp."""

    slot_id: str
    semantic_target: str
    motive: str
    charge: float = 0.0
    observations: int = 0
    stage: str = "forming"
    created_at: float = 0.0
    updated_at: float = 0.0
    last_seen_at: float = 0.0
    last_projected_at: float = 0.0
    projection_count: int = 0
    semantic_shifts: int = 0

    def to_dict(self, *, now: float) -> dict[str, Any]:
        return {
            "schema": INTENTION_FIELD_SCHEMA,
            "slot_id": self.slot_id,
            "semantic_target": self.semantic_target[:280],
            "motive": self.motive,
            "charge": round(_clamp01(self.charge), 3),
            "observations": self.observations,
            "stage": self.stage,
            "age_seconds": round(max(0.0, now - self.created_at), 2),
            "projection_count": self.projection_count,
            "semantic_shifts": self.semantic_shifts,
        }


class PrivateIntentionFieldV101:
    """Tầng tích hợp intention private — V101 authority.

    Host gọi ``observe(material, ...)`` mỗi tick cognitive. Runtime giữ
    slot-per-material và chỉ projection sang urge khi slot đạt ``ready``.
    """

    # Ngưỡng chuyển stage
    CHARGE_CONSIDERING = 0.30
    CHARGE_READY = 0.58
    CHARGE_RELEASE = 0.22
    MIN_OBSERVATIONS = 2
    MIN_DWELL_SECONDS = 1.2

    def __init__(self, host: Any, *, logger: Any | None = None) -> None:
        self.host = host
        self.logger = logger or getattr(host, "logger", None)
        self._slots: dict[str, IntentionSlotV101] = {}
        self._last_log_sig = ""

    # ── public API ────────────────────────────────────────────────────────

    def observe(
        self,
        material: Any,
        *,
        pressure: float = 0.5,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Tích hợp một mental material. Trả slot dict khi ``ready``, else None."""
        current = float(now if now is not None else time.time())
        slot_id = _compact(str(getattr(material, "material_id", "") or id(material)), 180)
        target = _compact(str(getattr(material, "content", "") or getattr(material, "text", "")), 1200)
        motive = _compact(str(getattr(material, "motive", "express") or "express"), 80).lower()
        if not slot_id or not target:
            return None

        pressure = _clamp01(pressure)
        slot = self._slots.get(slot_id)
        if slot is None:
            slot = IntentionSlotV101(
                slot_id=slot_id,
                semantic_target=target,
                motive=motive,
                charge=_clamp01(0.08 + 0.20 * pressure),
                observations=1,
                stage="forming",
                created_at=current,
                updated_at=current,
                last_seen_at=current,
            )
            self._slots[slot_id] = slot
        else:
            elapsed = max(0.0, current - slot.last_seen_at)
            # Detect semantic shift
            if target != slot.semantic_target:
                old_words = set(slot.semantic_target.casefold().split())
                new_words = set(target.casefold().split())
                overlap = len(old_words & new_words) / max(1, len(old_words | new_words))
                if overlap < 0.30:
                    slot.semantic_shifts += 1
                    slot.semantic_target = target
                    slot.motive = motive
                    slot.charge = min(0.25, slot.charge * 0.40)
                    slot.observations = 1
                    slot.stage = "forming"
                else:
                    slot.semantic_target = target
                    slot.motive = motive
                    slot.observations += 1
            else:
                slot.observations += 1

            # Charge integrator
            decay = 0.5 ** (elapsed / 300.0) if elapsed > 0.0 else 1.0
            slot.charge = _clamp01(slot.charge * decay)
            if pressure >= 0.40:
                dt = max(0.30, min(1.30, (elapsed if elapsed > 0.0 else 1.0) / 4.0))
                slot.charge = _clamp01(slot.charge + (0.10 + 0.20 * pressure) * dt)
            elif pressure < 0.25:
                slot.charge = _clamp01(slot.charge - 0.06)

            slot.updated_at = current
            slot.last_seen_at = current

        # Stage transition
        age = max(0.0, current - slot.created_at)
        if slot.stage in ("forming", "considering"):
            if slot.charge >= self.CHARGE_READY and slot.observations >= self.MIN_OBSERVATIONS and age >= self.MIN_DWELL_SECONDS:
                slot.stage = "ready"
            elif slot.charge >= self.CHARGE_CONSIDERING:
                slot.stage = "considering"
        elif slot.stage == "ready":
            if slot.charge < self.CHARGE_RELEASE:
                slot.stage = "cooling"

        self._log_slot(slot, current)

        if slot.stage == "ready":
            slot.projection_count += 1
            slot.last_projected_at = current
            return slot.to_dict(now=current)
        return None

    def note_projected(self, slot_id: str, *, now: float | None = None) -> bool:
        """Đánh dấu đã projection ra ngoài, chuyển slot về cooling."""
        slot = self._slots.get(str(slot_id or "").strip())
        if slot is None:
            return False
        current = float(now if now is not None else time.time())
        slot.stage = "cooling"
        slot.charge = min(slot.charge, 0.15)
        slot.updated_at = current
        slot.last_seen_at = current
        return True

    def snapshot(self, *, now: float | None = None, limit: int = 16) -> tuple[dict[str, Any], ...]:
        current = float(now if now is not None else time.time())
        self._prune(current)
        rows = sorted(
            self._slots.values(),
            key=lambda s: (s.stage == "ready", s.charge, s.updated_at),
            reverse=True,
        )
        return tuple(s.to_dict(now=current) for s in rows[: max(0, int(limit))])

    # ── private ───────────────────────────────────────────────────────────

    def _prune(self, now: float) -> None:
        dead = [
            sid for sid, slot in self._slots.items()
            if max(0.0, now - slot.last_seen_at) > 900.0
            or (slot.stage == "cooling" and max(0.0, now - slot.last_seen_at) > 180.0)
        ]
        for sid in dead:
            self._slots.pop(sid, None)

    def _log_slot(self, slot: IntentionSlotV101, now: float) -> None:
        sig = f"{slot.slot_id}:{slot.stage}:{round(slot.charge, 1)}:{slot.observations}"
        if sig == self._last_log_sig:
            return
        self._last_log_sig = sig
        fn = getattr(self.logger, "debug", None) if self.logger is not None else None
        if callable(fn):
            try:
                fn(
                    "[private-intention-v101] slot=%s stage=%s charge=%.2f obs=%s target=%s",
                    slot.slot_id,
                    slot.stage,
                    slot.charge,
                    slot.observations,
                    _compact(slot.semantic_target, 120),
                )
            except Exception:
                pass


__all__ = [
    "INTENTION_FIELD_SCHEMA",
    "INTENTION_FIELD_OWNER",
    "IntentionSlotV101",
    "PrivateIntentionFieldV101",
]

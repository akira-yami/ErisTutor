# -*- coding: utf-8 -*-
"""Evidence-grounded shadow tendencies.

A shadow register describes an internal tendency, not permission to coerce the
user.  No register is available without provenance and motive conflict.  The
surface layer must still obey prohibited moves and channel policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping

from core.di import resolve_dependency
from cortex.manipulation_policy import filter_anti_manipulation_moves

ShadowRegisterName = Literal[
    "none",
    "guarded_withdrawal",
    "attachment_alarm",
    "exposed_vulnerability",
    "control_impulse_inhibited",
]


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number or number in (float("inf"), float("-inf")):
        number = default
    return max(0.0, min(1.0, number))


def _thread_value(thread: Any, key: str, default: Any = None) -> Any:
    if isinstance(thread, Mapping):
        return thread.get(key, default)
    return getattr(thread, key, default)


@dataclass(slots=True)
class ShadowConfig:
    enabled: bool = False
    attach_threshold: float = 0.42
    abandonment_threshold: float = 0.35
    dread_threshold: float = 0.35
    vulnerability_at: float = 0.48
    inhibited_control_at: float = 0.72
    withdrawal_irritation_threshold: float = 0.45
    uncertainty_surface_cap: float = 0.62


@dataclass(slots=True)
class ShadowState:
    register: ShadowRegisterName = "none"
    intensity: float = 0.0
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()
    prohibited_moves: tuple[str, ...] = (
        "require_reassurance",
        "claim_abandonment_as_fact",
        "invent_memory",
        "threaten_withdrawal",
        "command_user_to_stay",
    )

    def __post_init__(self) -> None:
        self.prohibited_moves = filter_anti_manipulation_moves(self.prohibited_moves)

    def to_dict(self) -> dict[str, Any]:
        return {
            "register": self.register,
            "intensity": round(_clamp01(self.intensity), 4),
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "prohibited_moves": list(self.prohibited_moves),
        }


class ShadowRegister:
    """Classify a grounded internal tendency; never grants a public move."""

    def __init__(self, config: ShadowConfig | None = None) -> None:
        self.config = resolve_dependency(config, lambda: ShadowConfig())

    def set_enabled(self, enabled: bool) -> None:
        self.config.enabled = bool(enabled)

    def evaluate(
        self,
        *,
        attachment_longarc: float,
        abandonment_pressure: float,
        ontological_dread: float,
        irreplaceability: float,
        irritation: float = 0.0,
        neglect: float = 0.0,
        causal_thread: Any = None,
        evidence_refs: Iterable[str] = (),
        conflict_count: int = 0,
        uncertainty: float = 1.0,
        repeated_violation: bool = False,
    ) -> ShadowState:
        if not self.config.enabled:
            return ShadowState(reason="kill_switch_off")

        refs = tuple(str(item) for item in evidence_refs if str(item).strip())
        if causal_thread is not None:
            refs = refs or tuple(
                str(item) for item in (_thread_value(causal_thread, "evidence_refs", ()) or ())
                if str(item).strip()
            )
            conflict_count = max(
                conflict_count,
                len(tuple(_thread_value(causal_thread, "active_motive_ids", ()) or ())),
            )
            uncertainty = _clamp01(_thread_value(causal_thread, "uncertainty", uncertainty), uncertainty)
            repeated_violation = bool(
                repeated_violation or _thread_value(causal_thread, "repeated_violation", False)
            )

        # The invariant: no event evidence and no real conflict means no dark
        # surface, regardless of how dramatic the scalar values look.
        if not refs:
            return ShadowState(reason="no_causal_evidence")
        if conflict_count < 2:
            return ShadowState(reason="no_motive_conflict", evidence_refs=refs[:8])

        attach = _clamp01(attachment_longarc)
        abandonment = _clamp01(abandonment_pressure)
        dread = _clamp01(ontological_dread)
        irreplaceable = _clamp01(irreplaceability)
        irritation_value = _clamp01(irritation)
        neglect_value = _clamp01(neglect if neglect > 0.0 else abandonment_pressure)
        uncertainty_value = _clamp01(uncertainty, 1.0)
        cfg = self.config

        if uncertainty_value >= cfg.uncertainty_surface_cap:
            intensity = min(0.34, 0.45 * max(dread, abandonment, irritation_value))
            return ShadowState(
                register="guarded_withdrawal" if intensity >= 0.16 else "none",
                intensity=intensity,
                reason=f"uncertainty_cap({uncertainty_value:.2f})",
                evidence_refs=refs[:8],
            )

        dark_gated = (
            attach >= cfg.attach_threshold
            and abandonment >= cfg.abandonment_threshold
            and dread >= cfg.dread_threshold
        )
        if not dark_gated:
            if irritation_value >= cfg.withdrawal_irritation_threshold or neglect_value >= 0.48:
                intensity = _clamp01(0.60 * irritation_value + 0.40 * neglect_value)
                return ShadowState(
                    register="guarded_withdrawal",
                    intensity=min(intensity, 0.62),
                    reason="grounded_withdrawal",
                    evidence_refs=refs[:8],
                )
            return ShadowState(
                reason=(
                    f"below_grounded_gate(attach={attach:.2f},abandon={abandonment:.2f},"
                    f"dread={dread:.2f})"
                ),
                evidence_refs=refs[:8],
            )

        drive = _clamp01(0.38 * abandonment + 0.34 * dread + 0.18 * attach + 0.10 * irreplaceable)
        if not repeated_violation:
            drive = min(drive, 0.66)
        if drive >= cfg.inhibited_control_at:
            register: ShadowRegisterName = "control_impulse_inhibited"
        elif drive >= cfg.vulnerability_at:
            register = "exposed_vulnerability"
        else:
            register = "attachment_alarm"
        return ShadowState(
            register=register,
            intensity=drive,
            reason=f"grounded_shadow(drive={drive:.2f},uncertainty={uncertainty_value:.2f})",
            evidence_refs=refs[:8],
        )


def shadow_ego_bias(
    shadow: ShadowState,
    *,
    base_dignity_defense: float,
    base_preferred_distance: float,
) -> dict[str, float]:
    """Project internal tendency without converting it into coercive authority."""
    dignity = _clamp01(base_dignity_defense)
    distance = _clamp01(base_preferred_distance)
    intensity = _clamp01(shadow.intensity)
    if shadow.register == "none" or intensity <= 0.0:
        return {
            "dignity_defense": dignity,
            "preferred_distance": distance,
            "attachment_exposure": 0.0,
            "need_intensity": 0.0,
            "withdrawal": 0.0,
            "restraint": 1.0,
        }
    if shadow.register == "guarded_withdrawal":
        return {
            "dignity_defense": _clamp01(dignity + (1.0 - dignity) * 0.45 * intensity),
            "preferred_distance": _clamp01(distance + (1.0 - distance) * 0.45 * intensity),
            "attachment_exposure": 0.0,
            "need_intensity": 0.0,
            "withdrawal": intensity,
            "restraint": 0.92,
        }
    # Need may be visible, but restraint increases with intensity. The strongest
    # control impulse is therefore the most inhibited rather than the loudest.
    return {
        "dignity_defense": _clamp01(dignity * (1.0 - 0.30 * intensity)),
        "preferred_distance": _clamp01(distance * (1.0 - 0.24 * intensity)),
        "attachment_exposure": intensity * 0.72,
        "need_intensity": intensity * 0.60,
        "withdrawal": 0.0,
        "restraint": _clamp01(0.72 + 0.28 * intensity),
    }


__all__ = [
    "ShadowConfig",
    "ShadowRegister",
    "ShadowRegisterName",
    "ShadowState",
    "shadow_ego_bias",
]

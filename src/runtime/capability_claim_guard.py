# -*- coding: utf-8 -*-
"""Typed precommit guard for ERIS self-capability claims.

This module never chooses a capability from prose. It only validates exact
capability IDs already emitted in a structured public-reply field against the
live runtime registry. Contradictory self-claims fail closed before execution or
public commit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from actions.capability_registry import capability_definition
from actions.capability_runtime import runtime_capability_snapshot

_ALLOWED_STATES = {"available", "unavailable", "uncertain"}


def normalize_self_capability_claims(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        capability_id = str(raw.get("capability_id", "") or "").strip()
        state = str(raw.get("state", "uncertain") or "uncertain").strip().lower()
        basis = str(raw.get("basis", "model") or "model").strip().lower()
        if not capability_id or state not in _ALLOWED_STATES:
            continue
        key = (capability_id, state)
        if key in seen:
            continue
        seen.add(key)
        output.append({"capability_id": capability_id, "state": state, "basis": basis[:80]})
        if len(output) >= 8:
            break
    return output


@dataclass(frozen=True, slots=True)
class CapabilityClaimGuardResult:
    payload: Mapping[str, Any]
    allowed: bool
    violations: tuple[str, ...] = ()


def _runtime_statuses(owner: Any | None) -> Mapping[str, Any]:
    try:
        snapshot = runtime_capability_snapshot(refresh=True, owner=owner)
    except TypeError:
        snapshot = runtime_capability_snapshot(refresh=True)
    except Exception:
        snapshot = {}
    statuses = snapshot.get("statuses", {}) if isinstance(snapshot, Mapping) else {}
    return statuses if isinstance(statuses, Mapping) else {}


def _blank_public_surface(payload: Mapping[str, Any]) -> dict[str, Any]:
    repaired = dict(payload)
    repaired["should_speak"] = False
    repaired["text"] = ""
    repaired["speech_text"] = ""
    utterance = repaired.get("utterance")
    if isinstance(utterance, Mapping):
        updated = dict(utterance)
        display = updated.get("display")
        if isinstance(display, Mapping):
            display_data = dict(display)
            display_data["text"] = ""
            updated["display"] = display_data
        speech = updated.get("speech")
        if isinstance(speech, Mapping):
            speech_data = dict(speech)
            speech_data["text"] = ""
            updated["speech"] = speech_data
        repaired["utterance"] = updated
    return repaired


def guard_public_capability_claims(
    payload: Mapping[str, Any],
    *,
    owner: Any | None = None,
    executive_directive: Mapping[str, Any] | None = None,
) -> CapabilityClaimGuardResult:
    claims = normalize_self_capability_claims(payload.get("self_capability_claims", ()))
    if not claims:
        return CapabilityClaimGuardResult(dict(payload), True, ())
    statuses = _runtime_statuses(owner)
    selected_id = str((executive_directive or {}).get("capability_id", "") or "").strip()
    selected_status = str((executive_directive or {}).get("status", "") or "").strip().lower()
    violations: list[str] = []
    for claim in claims:
        capability_id = claim["capability_id"]
        state = claim["state"]
        definition = capability_definition(capability_id)
        status = statuses.get(capability_id, {}) if isinstance(statuses, Mapping) else {}
        explicit_available = (
            bool(status.get("available"))
            if isinstance(status, Mapping) and "available" in status
            else bool(getattr(definition, "live_available", False)) if definition is not None else None
        )
        if selected_status == "selected" and capability_id == selected_id:
            explicit_available = True
        if definition is None:
            violations.append(f"unknown_capability_id:{capability_id}")
        elif state == "available" and explicit_available is not True:
            violations.append(f"false_available_claim:{capability_id}")
        elif state == "unavailable" and explicit_available is not False:
            violations.append(f"false_unavailable_claim:{capability_id}")
    if not violations:
        updated = dict(payload)
        updated["self_capability_claims"] = claims
        return CapabilityClaimGuardResult(updated, True, ())
    repaired = _blank_public_surface(payload)
    repaired["self_capability_claims"] = claims
    repaired["capability_claim_guard"] = {
        "allowed": False,
        "violations": list(violations),
        "policy": "fail_closed_before_public_commit",
    }
    return CapabilityClaimGuardResult(repaired, False, tuple(violations))


__all__ = [
    "CapabilityClaimGuardResult",
    "guard_public_capability_claims",
    "normalize_self_capability_claims",
]

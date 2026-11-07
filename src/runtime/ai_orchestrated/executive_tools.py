# -*- coding: utf-8 -*-
"""Live capability contracts for the Gemma Executive Cortex.

Runtime performs only hard filtering: availability, exposure, effect, domain,
risk, and permission. Gemma performs semantic shortlisting from compact cards,
then receives full schemas only for the exact IDs it shortlisted. No phrase
router, synonym table, registry-order top-N, or capability reinterpretation is
allowed here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from actions.capability_registry import capability_input_schema, iter_public_capabilities
from actions.capability_runtime import refresh_runtime_capabilities, runtime_capability_snapshot

_EFFECTS = frozenset({"observe", "read", "write", "control", "inspect", "communicate"})
_RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True, slots=True)
class ExecutiveTool:
    capability_id: str
    title: str
    description: str
    effect: str
    domain: str
    available: bool
    availability_reason: str
    risk: str
    permission_mode: str
    auto_callable: bool
    input_schema: Mapping[str, Any]
    source: str = "capability_registry"
    exposure: str = "executive"

    def to_card(self) -> dict[str, Any]:
        """Compact semantic card used only for Gemma shortlisting."""
        return {
            "capability_id": self.capability_id,
            "title": self.title,
            "description": self.description,
            "effect": self.effect,
            "domain": self.domain,
            "risk": self.risk,
            "permission_mode": self.permission_mode,
            "auto_callable": self.auto_callable,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["input_schema"] = dict(self.input_schema)
        return payload


@dataclass(frozen=True, slots=True)
class CapabilityIndex:
    revision: int
    live_count: int
    effects: Mapping[str, int]
    domains: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": int(self.revision),
            "live_count": int(self.live_count),
            "effects": dict(self.effects),
            "domains": dict(self.domains),
        }


def _contract_metadata(capability_id: str) -> tuple[str, str, str]:
    """Return risk, permission, and provenance from the strongest contract."""
    try:
        from runtime.internal_cognitive_capabilities import COGNITIVE_CAPABILITY_CONTRACTS

        contract = COGNITIVE_CAPABILITY_CONTRACTS.get(capability_id)
        if contract is not None:
            permission = getattr(contract.permission_mode, "value", contract.permission_mode)
            return "low", str(permission or "read_only_auto"), "internal_cognitive_contract"
    except Exception:
        pass
    try:
        from runtime.environment_capabilities import ENVIRONMENT_CAPABILITY_CONTRACTS

        contract = ENVIRONMENT_CAPABILITY_CONTRACTS.get(capability_id)
        if contract is not None:
            return "low", str(contract.permission_mode or "read_only_auto"), "environment_contract"
    except Exception:
        pass
    try:
        from runtime.project_operations import PROJECT_CAPABILITY_CONTRACTS

        contract = PROJECT_CAPABILITY_CONTRACTS.get(capability_id)
        if contract is not None:
            return (
                str(contract.risk_level or "medium"),
                str(contract.permission_mode or "autonomous_project_scope"),
                "project_contract",
            )
    except Exception:
        pass
    return "low", "runtime_policy", "capability_registry"


def _auto_callable(*, available: bool, risk: str, permission_mode: str) -> bool:
    if not available:
        return False
    permission = str(permission_mode or "").strip().lower()
    risk_key = str(risk or "").strip().lower()
    return permission in {"read_only_auto", "read_only", "auto_read"} and risk_key in {"", "none", "low"}


def _snapshot(owner: Any | None, *, refresh: bool) -> tuple[Mapping[str, Any], int]:
    try:
        if refresh:
            refresh_runtime_capabilities(owner=owner)
        snapshot = runtime_capability_snapshot(refresh=False)
    except Exception:
        snapshot = {"statuses": {}}
    if not isinstance(snapshot, Mapping):
        snapshot = {"statuses": {}}
    try:
        revision = int(snapshot.get("revision", 0) or 0)
    except (TypeError, ValueError):
        revision = 0
    return snapshot, revision


def all_executive_tools(owner: Any | None, *, refresh: bool = False) -> tuple[ExecutiveTool, ...]:
    snapshot, _ = _snapshot(owner, refresh=refresh)
    statuses = snapshot.get("statuses", {}) if isinstance(snapshot, Mapping) else {}
    output: list[ExecutiveTool] = []
    for definition in iter_public_capabilities(include_unavailable=True):
        status = statuses.get(definition.id, {}) if isinstance(statuses, Mapping) else {}
        if isinstance(status, Mapping):
            available = bool(status.get("available", definition.live_available))
            reason = str(status.get("reason", "") or "")
        else:
            available = bool(definition.live_available)
            reason = ""
        risk, permission_mode, source = _contract_metadata(definition.id)
        risk = str(risk or definition.risk_level or "low").strip().lower()
        domain = str(definition.visible_in_ui_group or "runtime").strip().lower()
        effect = str(getattr(definition, "effect", "") or "").strip().lower()
        if effect not in _EFFECTS:
            continue
        exposure = str(getattr(definition, "exposure", "executive") or "executive").strip().lower()
        callable_capability = bool(getattr(definition, "callable", True))
        output.append(
            ExecutiveTool(
                capability_id=definition.id,
                title=definition.public_name_vi,
                description=definition.description_vi,
                effect=effect,
                domain=domain,
                available=available,
                availability_reason=reason,
                risk=risk,
                permission_mode=permission_mode,
                auto_callable=(
                    callable_capability
                    and exposure == "executive"
                    and _auto_callable(
                        available=available,
                        risk=risk,
                        permission_mode=permission_mode,
                    )
                ),
                input_schema=capability_input_schema(definition.id),
                source=source,
                exposure=exposure,
            )
        )
    return tuple(output)


def build_capability_index(owner: Any | None, *, refresh: bool = True) -> CapabilityIndex:
    """Return category counts only, never capability schemas."""
    _, revision = _snapshot(owner, refresh=refresh)
    live = [item for item in all_executive_tools(owner, refresh=False) if item.available and item.exposure == "executive"]
    effects: dict[str, int] = {}
    domains: dict[str, int] = {}
    for item in live:
        effects[item.effect] = effects.get(item.effect, 0) + 1
        domains[item.domain] = domains.get(item.domain, 0) + 1
    return CapabilityIndex(revision=revision, live_count=len(live), effects=effects, domains=domains)


def filter_executive_tools(
    owner: Any | None,
    *,
    required_effect: str,
    maximum_risk: str = "low",
    domain: str = "",
    include_unavailable: bool = False,
    refresh: bool = False,
) -> tuple[ExecutiveTool, ...]:
    """Apply hard runtime constraints without semantic ranking or top-N truncation."""
    effect = str(required_effect or "").strip().lower()
    if effect not in _EFFECTS:
        return ()
    risk_ceiling = _RISK_ORDER.get(str(maximum_risk or "low").strip().lower(), 1)
    domain_key = str(domain or "").strip().lower()
    selected = [
        item
        for item in all_executive_tools(owner, refresh=refresh)
        if item.effect == effect
        and (not domain_key or item.domain == domain_key)
        and (include_unavailable or item.available)
        and _RISK_ORDER.get(item.risk, 2) <= risk_ceiling
        and item.exposure == "executive"
    ]
    # Stable ordering is only for deterministic payloads/tests. It has no
    # semantic authority because Gemma sees the entire filtered set and returns
    # exact IDs.
    selected.sort(key=lambda item: item.capability_id)
    return tuple(selected)



def retrieve_executive_tools(
    owner: Any | None,
    *,
    required_effect: str,
    maximum_risk: str = "low",
    domain: str = "",
    include_unavailable: bool = False,
    refresh: bool = False,
    max_items: int | None = None,
) -> tuple[ExecutiveTool, ...]:
    """Compatibility facade for the V53 bounded retrieval contract.

    V54 removed runtime semantic ranking. This facade therefore delegates to
    the hard filter and only applies an explicit caller-provided safety bound.
    The default returns the complete filtered set.
    """
    selected = filter_executive_tools(
        owner,
        required_effect=required_effect,
        maximum_risk=maximum_risk,
        domain=domain,
        include_unavailable=include_unavailable,
        refresh=refresh,
    )
    if max_items is None:
        return selected
    try:
        limit = max(1, min(64, int(max_items)))
    except (TypeError, ValueError):
        return selected
    return tuple(item for index, item in enumerate(selected) if index < limit)

def select_tools_by_id(
    candidates: Sequence[ExecutiveTool],
    capability_ids: Sequence[str],
    *,
    max_items: int = 6,
) -> tuple[ExecutiveTool, ...]:
    """Materialize full schemas for exact IDs already shortlisted by Gemma."""
    index = {item.capability_id: item for item in candidates}
    output: list[ExecutiveTool] = []
    seen: set[str] = set()
    for raw_id in capability_ids:
        capability_id = str(raw_id or "").strip()
        if not capability_id or capability_id in seen:
            continue
        tool = index.get(capability_id)
        if tool is None:
            continue
        seen.add(capability_id)
        output.append(tool)
        if len(output) >= max(1, min(12, int(max_items))):
            break
    return tuple(output)


def catalog_index(catalog: Sequence[ExecutiveTool]) -> dict[str, ExecutiveTool]:
    return {item.capability_id: item for item in catalog}


__all__ = [
    "CapabilityIndex",
    "ExecutiveTool",
    "all_executive_tools",
    "build_capability_index",
    "catalog_index",
    "filter_executive_tools",
    "retrieve_executive_tools",
    "select_tools_by_id",
]

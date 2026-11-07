"""Project V94 goals without assigning goal adoption or speech authority."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from runtime.autonomy.goal_contracts import ShadowDiff

# V94's user-turn and TTL decay are NOT evidence that Eris abandoned a goal.
UNCONFIRMED_DECAY = frozenset({
    "not_reconfirmed", "unconfirmed_user_turn", "user_moved_on", "ttl_expired",
})
TERMINAL_INTENTIONAL = frozenset({"abandoned", "resolved"})


def as_legacy_record(item: Any) -> dict[str, Any]:
    """Defensive copy; never mutate source RuntimeMindState/GoalLifecycleRecord."""
    to_dict = getattr(item, "to_dict", None)
    raw = to_dict() if callable(to_dict) else item
    if not isinstance(raw, Mapping):
        raise TypeError("legacy goal must be a mapping or implement to_dict()")
    goal_id = str(raw.get("goal_id", "") or "").strip()
    if not goal_id:
        raise ValueError("legacy goal missing goal_id")
    return {
        "goal_id": goal_id,
        "owner": str(raw.get("owner", "unknown") or "unknown"),
        "text": str(raw.get("text", "") or ""),
        "state": str(raw.get("state", "") or "").lower(),
        "reason": str(raw.get("reason", "") or ""),
        "evidence_refs": list(str(x) for x in (raw.get("evidence_refs") or ())),
    }


def project_goal(
    current: Mapping[str, Any] | None,
    legacy: Mapping[str, Any],
    *,
    source_revision: int,
    observed_at: float,
) -> tuple[dict[str, Any], tuple[ShadowDiff, ...]]:
    """Track the observation; make ONLY a diagnostic expected-state estimate.

    No autonomous goal is adopted by this function. If V94 expires a self goal
    by its chat-turn decay heuristic, retain its previous SHADOW expectation
    and emit a divergence. Genuine intentional terminal transitions are accepted.
    """
    before = dict(current or {})
    owner = str(legacy["owner"])
    legacy_state = str(legacy["state"])
    reason = str(legacy["reason"])
    expected = legacy_state
    diffs: list[ShadowDiff] = []
    prior_expected = str(before.get("shadow_expected_state", ""))
    if before and str(before.get("owner", "")) != owner:
        diffs.append(ShadowDiff(
            code="owner_changed_in_legacy",
            goal_id=str(legacy["goal_id"]),
            detail=f"{before.get('owner', 'unknown')} -> {owner}; requires adoption provenance at cutover",
        ))
    if owner == "eris" and reason in UNCONFIRMED_DECAY and prior_expected in {"active", "paused"}:
        expected = prior_expected
        if legacy_state != expected:
            diffs.append(ShadowDiff(
                code="self_goal_legacy_decay",
                goal_id=str(legacy["goal_id"]),
                detail=f"V94={legacy_state} due to {reason}; shadow expectation={expected}",
            ))
    elif owner == "eris" and legacy_state in TERMINAL_INTENTIONAL:
        expected = legacy_state

    result = {
        "goal_id": str(legacy["goal_id"]),
        "owner": owner,
        "text": str(legacy["text"]),
        "legacy_state": legacy_state,
        "shadow_expected_state": expected,
        "reason": reason,
        "first_seen_at": float(before.get("first_seen_at", observed_at)),
        "last_seen_at": float(observed_at),
        "last_seen_source_revision": int(source_revision),
        "missing_since_revision": None,
        "evidence_refs": list(legacy["evidence_refs"]),
    }
    return result, tuple(diffs)


def detect_missing(
    prior: Mapping[str, Mapping[str, Any]],
    seen_ids: set[str],
    *,
    source_revision: int,
) -> tuple[dict[str, dict[str, Any]], tuple[ShadowDiff, ...]]:
    """A missing V94 projection may be truncation; NEVER treat it as deletion."""
    retained: dict[str, dict[str, Any]] = {}
    diffs: list[ShadowDiff] = []
    for goal_id, previous in prior.items():
        if goal_id in seen_ids:
            continue
        record = dict(previous)
        if record.get("missing_since_revision") is None:
            record["missing_since_revision"] = int(source_revision)
            diffs.append(ShadowDiff(
                code="missing_from_legacy_projection",
                goal_id=goal_id,
                detail="not present in bounded V94 snapshot; retained in shadow, not treated as deleted",
            ))
        retained[goal_id] = record
    return retained, tuple(diffs)

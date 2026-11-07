from __future__ import annotations

import math
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from cortex.development_profile import AxisDelta, ProfileVersion, baseline_axes
from cortex.agent_mind_persistence import PersistentBeliefRecord
from cortex.private_mind.user_model import UserBelief

from .contracts import Belief


class EpistemicUserModelProjection:
    """Legacy read API backed exclusively by V2 beliefs."""

    def __init__(self, kernel: Any) -> None:
        self.kernel = kernel
        self.path = Path("memory_core/epistemic_v2.json")

    def all(self) -> tuple[UserBelief, ...]:
        return tuple(self._to_user_belief(item) for item in self.kernel.recall(scope="user", limit=256))

    def by_facet(self, facet: str) -> tuple[UserBelief, ...]:
        return tuple(item for item in self.all() if item.facet == facet)

    def model_thickness(self) -> float:
        return float(self.kernel.user_model_snapshot(limit=256).get("model_thickness", 0.0))

    def snapshot_for_dossier(self, *, limit: int = 12) -> dict[str, Any]:
        return self.kernel.user_model_snapshot(limit=limit)

    def update_from_fragments(self, fragments: Iterable[Any]) -> tuple[()]:
        self.kernel.ingest_private_hypotheses(fragments)
        return ()

    def decay(self, **_: Any) -> None:
        return None

    def reload_if_changed(self) -> None:
        return None

    def save(self) -> None:
        self.kernel.store.save()

    def checkpoint(self) -> int:
        history = list(self.kernel.get_projection("user_model_history").get("entries", ()))
        version = int(history[-1].get("version", 0)) + 1 if history else 1
        history.append({
            "version": version,
            "at": time.time(),
            "beliefs": [item.to_dict() for item in self.kernel.recall(scope="user", limit=256)],
        })
        self.kernel.set_projection("user_model_history", {"entries": history[-20:]}, reason="checkpoint")
        return version

    def revert_to(self, version: int) -> bool:
        history = self.kernel.get_projection("user_model_history").get("entries", ())
        target = next((item for item in history if int(item.get("version", -1)) == int(version)), None)
        if target is None:
            return False
        self.kernel.reset_subject("user")
        for raw in target.get("beliefs", ()):
            belief = Belief.from_mapping(raw)
            self.kernel.store.beliefs[belief.id] = belief
        self.kernel.store.save()
        return True

    def reset_to_baseline(self) -> None:
        self.kernel.reset_subject("user")

    def __len__(self) -> int:
        return len(self.kernel.recall(scope="user", limit=256))

    @staticmethod
    def _to_user_belief(item: Belief) -> UserBelief:
        return UserBelief(
            id=item.id,
            facet=item.facet,
            claim=item.claim,
            evidence_ids=tuple(ref.id for ref in item.positive_evidence),
            confidence=item.confidence,
            uncertainty=item.uncertainty,
            recurrence=max(1, len(item.independent_positive_keys)),
            created_at=item.created_at,
            updated_at=item.updated_at,
            last_reinforced_at=item.updated_at,
            metadata={"horizon": item.horizon.value, "source": "epistemic_v2"},
        )


class EpistemicAgentMindProjection:
    """Belief/procedural read API with no writable V1 backing store."""

    def __init__(self, kernel: Any) -> None:
        self.kernel = kernel

    @property
    def last_feedback_events(self) -> list[dict[str, Any]]:
        return list(self.kernel.get_projection("feedback_events").get("items", ()))

    def reload_if_changed(self) -> None:
        return None

    def relevant_beliefs(self, *, evidence_tags=(), focus_kind="", limit=10) -> tuple[PersistentBeliefRecord, ...]:
        del evidence_tags, focus_kind
        records = []
        for belief in self.kernel.recall(limit=limit):
            if belief.subject == "user":
                continue
            records.append(PersistentBeliefRecord(
                key=f"v2.{belief.subject}.{belief.facet}.{belief.id[-8:]}",
                claim=belief.claim,
                domain=belief.subject,
                confidence=belief.confidence,
                source_ids=tuple(ref.id for ref in belief.positive_evidence),
                contradiction_ids=tuple(ref.id for ref in belief.contradictory_evidence),
                positive_evidence_count=len(belief.independent_positive_keys),
                negative_evidence_count=len(belief.independent_contradictory_keys),
                decay_rate=0.0,
                created_at=belief.created_at,
                updated_at=belief.updated_at,
                last_seen=belief.updated_at,
            ))
        return tuple(records)

    def preference_strength(self, key: str, default: float) -> float:
        values = self.kernel.get_projection("self_preferences").get("values", {})
        try:
            return max(0.0, min(1.0, float(values.get(key, default))))
        except (TypeError, ValueError):
            return default

    def habit_bias(self, *, evidence_tags=(), action="", impulse_name="", family="") -> float:
        del evidence_tags, impulse_name, family
        policy = self.kernel.store.procedural_policies.get(f"tone:{action}", {})
        success = float(policy.get("success", 0.0) or 0.0)
        failure = float(policy.get("failure", 0.0) or 0.0)
        return max(-0.22, min(0.22, (success - failure) / max(5.0, success + failure + 2.0)))

    def record_user_feedback(self, text: str, *, action_plan: Any = None, context_tags=()) -> tuple[str, ...]:
        clean = str(text or "").strip()
        if not clean:
            return ()
        action = str(getattr(action_plan, "speech_act", "") or getattr(action_plan, "speech_intent", "") or "unknown")
        items = self.last_feedback_events
        event = {
            "id": f"feedback:{time.time_ns()}",
            "text": clean,
            "timestamp": time.time(),
            "action": action,
            "tags": list(dict.fromkeys(str(item) for item in context_tags if str(item or "").strip())),
            "source": "user_feedback",
        }
        items.append(event)
        self.kernel.set_projection("feedback_events", {"items": items[-100:]}, reason="user_feedback")
        key = f"tone:{action}"
        policy = self.kernel.store.procedural_policies.setdefault(key, {"success": 0.0, "failure": 0.0})
        normalized = clean.lower()
        negative = any(token in normalized for token in ("đừng", "dung", "không", "khong", "sai", "lạnh", "lanh", "robot", "camera"))
        field = "failure" if negative else "success"
        policy[field] = float(policy.get(field, 0.0) or 0.0) + 1.0
        self.kernel.store.save()
        return (f"epistemic_v2.feedback.{field}",)


class EpistemicDevelopmentProfile:
    """DevelopmentProfileStore-compatible projection persisted inside V2."""

    def __init__(self, kernel: Any) -> None:
        self.kernel = kernel
        self.path = Path("memory_core/epistemic_v2.json")
        self.version = 0
        self.axes = baseline_axes()
        self.load()

    def load(self) -> None:
        raw = self.kernel.get_projection("development")
        self.version = int(raw.get("version", 0) or 0)
        axes = baseline_axes()
        for axis, value in dict(raw.get("axes") or {}).items():
            if axis in axes:
                axes[axis] = AxisDelta.from_dict(value, axis=axis)
        self.axes = axes

    reload_if_changed = load

    def save(self) -> None:
        self.kernel.set_projection("development", {
            "version": self.version,
            "axes": {axis: value.to_dict() for axis, value in self.axes.items()},
        }, reason="development_update")

    def delta(self, axis: str) -> float:
        value = self.axes.get(axis)
        return float(value.delta) if value is not None else 0.0

    def identity_distance(self, bounds=None) -> float:
        total = 0.0
        for axis, item in self.axes.items():
            value = float(item.delta)
            if bounds and axis in bounds:
                lo, hi = bounds[axis]
                value /= max(abs(lo), abs(hi)) or 1.0
            total += value * value
        return math.sqrt(total)

    def snapshot(self) -> ProfileVersion:
        return ProfileVersion(self.version, {axis: replace(value) for axis, value in self.axes.items()}, time.time())

    def restore(self, snapshot: ProfileVersion) -> None:
        self.version = snapshot.version
        self.axes = {axis: replace(value) for axis, value in snapshot.axes.items()}

    def commit(self, axis: str, clamped_delta: float, *, evidence_ids=(), now=None) -> None:
        current = self.axes.get(axis, AxisDelta(axis=axis))
        self.axes[axis] = replace(
            current,
            delta=max(-1.0, min(1.0, float(clamped_delta))),
            last_evidence_ids=tuple(evidence_ids),
            updated_at=float(now or time.time()),
        )
        self.version += 1

    def reset_to_baseline(self) -> None:
        self.version = 0
        self.axes = baseline_axes()


class EpistemicDevelopmentLog:
    def __init__(self, kernel: Any) -> None:
        self.kernel = kernel
        self.entries = []
        self._load()

    def _load(self) -> None:
        from cortex.development_profile import LogEntry
        raw = self.kernel.get_projection("development_log")
        self.entries = [LogEntry.from_dict(item) for item in raw.get("entries", ())]

    def record(self, entry: Any) -> bool:
        self.entries.append(entry)
        self.kernel.set_projection(
            "development_log",
            {"entries": [item.to_dict() for item in self.entries[-200:]]},
            reason="development_audit",
        )
        return True

    def count_applied_in_window(self, *, now: float, window_seconds: float) -> int:
        floor = now - max(0.0, window_seconds)
        return sum(1 for item in self.entries if item.action == "applied" and item.timestamp >= floor)

    def last_applied(self) -> Any:
        return next((item for item in reversed(self.entries) if item.action == "applied"), None)


def epistemic_development_system(kernel: Any) -> Any:
    from cortex.development_profile import IdentityGate
    from cortex.development_system import DevelopmentSystem
    return DevelopmentSystem(
        profile=EpistemicDevelopmentProfile(kernel),
        log=EpistemicDevelopmentLog(kernel),
        gate=IdentityGate(),
    )

# -*- coding: utf-8 -*-
"""V94 - lifecycle/backoff cho autonomous proposal.

Neuro chọn ``silence`` là một outcome thật, không phải "không có gì xảy ra". Nếu
proposal y hệt quay lại ngay tick kế tiếp, runtime đang tự hỏi cùng một câu mãi.
Module này ghi receipt cơ học cho proposal và áp exponential backoff. Evidence
mới sẽ reset backoff ngay, vì khi thế giới/user đổi thì proposal không còn là bản
sao cũ nữa.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import threading
import time
from typing import Any, Mapping, Sequence


def _compact(value: Any, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[: max(16, int(limit))]


def _refs(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (_compact(value, 160),) if value.strip() else ()
    if isinstance(value, Sequence):
        return tuple(dict.fromkeys(_compact(item, 160) for item in value if _compact(item, 160)))[:12]
    return ()


def _fingerprint(topic: Any, evidence_refs: Any) -> str:
    raw = f"{_compact(topic, 520).casefold()}|{'|'.join(_refs(evidence_refs))}".encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()[:16]


def _key(source: Any, speech_act: Any, anchor: Any, topic: Any = "") -> str:
    stable_anchor = _compact(anchor, 220) or _compact(topic, 220).casefold()
    raw = f"{_compact(source, 80).casefold()}|{_compact(speech_act, 80).casefold()}|{stable_anchor}".encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()[:20]


class ProposalLifecycleState(StrEnum):
    ACTIVE = "active"
    DEFERRED = "deferred"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class ProposalEligibility:
    allowed: bool
    reason: str
    proposal_key: str
    next_eligible_at: float = 0.0


@dataclass(frozen=True, slots=True)
class ProposalLifecycleRecord:
    proposal_key: str
    state: ProposalLifecycleState
    evidence_fingerprint: str
    created_at: float
    updated_at: float
    next_eligible_at: float = 0.0
    silence_count: int = 0
    failure_count: int = 0
    outcome: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.proposal_lifecycle.v94",
            "proposal_key": self.proposal_key,
            "state": self.state.value,
            "evidence_fingerprint": self.evidence_fingerprint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "next_eligible_at": self.next_eligible_at,
            "silence_count": self.silence_count,
            "failure_count": self.failure_count,
            "outcome": self.outcome,
        }


class ProposalLifecycleRuntime:
    """Session-local state machine cho proposal autonomous.

    Backoff mặc định: 8s -> 20s -> 45s -> 90s -> 180s. Evidence mới luôn được
    thử ngay và reset counters. ``spoken`` retire ngắn; lifecycle intention/goal
    ở tầng trên vẫn là authority semantic dài hạn.
    """

    SILENCE_BACKOFF = (8.0, 20.0, 45.0, 90.0, 180.0)

    def __init__(self) -> None:
        self._records: dict[str, ProposalLifecycleRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def describe_affordance(affordance: Any) -> tuple[str, str]:
        candidate = getattr(affordance, "candidate", None)
        if candidate is None:
            return "", ""
        source = getattr(candidate, "source", "") or getattr(candidate, "source_kind", "") or getattr(affordance, "owner", "")
        speech_act = getattr(candidate, "speech_act", "")
        anchor = getattr(candidate, "anchor_id", "") or getattr(affordance, "affordance_id", "")
        topic = getattr(candidate, "topic", "")
        return _key(source, speech_act, anchor, topic), _fingerprint(topic, getattr(candidate, "evidence_refs", ()))

    @staticmethod
    def describe_plan(plan: Any) -> tuple[str, str]:
        semantics = getattr(plan, "semantics", None)
        speech_act = getattr(semantics, "speech_act", "") if semantics is not None else ""
        source = getattr(plan, "source_kind", "") or getattr(plan, "owner", "")
        anchor = getattr(plan, "source_ref", "") or getattr(plan, "plan_id", "")
        topic = getattr(plan, "topic", "")
        return _key(source, speech_act, anchor, topic), _fingerprint(topic, getattr(plan, "evidence_refs", ()))

    def allow_affordance(self, affordance: Any, *, now: float | None = None) -> ProposalEligibility:
        current = float(time.time() if now is None else now)
        proposal_key, evidence = self.describe_affordance(affordance)
        if not proposal_key:
            return ProposalEligibility(True, "untracked", "")
        with self._lock:
            record = self._records.get(proposal_key)
            if record is None:
                return ProposalEligibility(True, "new", proposal_key)
            if evidence and evidence != record.evidence_fingerprint:
                # Có evidence mới: proposal cùng semantic object được quyền quay
                # lại ngay, vì đây không còn là retry mù của tick trước.
                self._records[proposal_key] = replace(
                    record,
                    state=ProposalLifecycleState.ACTIVE,
                    evidence_fingerprint=evidence,
                    updated_at=current,
                    next_eligible_at=0.0,
                    silence_count=0,
                    failure_count=0,
                    outcome="evidence_changed",
                )
                return ProposalEligibility(True, "evidence_changed", proposal_key)
            if record.state == ProposalLifecycleState.RETIRED and current < record.next_eligible_at:
                return ProposalEligibility(False, "retired", proposal_key, record.next_eligible_at)
            if record.state == ProposalLifecycleState.DEFERRED and current < record.next_eligible_at:
                return ProposalEligibility(False, "deferred_backoff", proposal_key, record.next_eligible_at)
            return ProposalEligibility(True, "backoff_elapsed", proposal_key, record.next_eligible_at)

    def note_affordance_outcome(
        self,
        affordance: Any,
        *,
        outcome: str,
        now: float | None = None,
    ) -> ProposalLifecycleRecord | None:
        """Ghi outcome khi Neuro chọn silence trước khi một plan speech được materialize.

        Đây là đường quan trọng cho V94: khi Subject frame chứa proposal speech
        nhưng coalition thắng là ``silence``, chưa có ``IntentionPlan`` của proposal
        bị từ chối. Ta dùng chính affordance identity/evidence để backoff.
        """
        current = float(time.time() if now is None else now)
        proposal_key, evidence = self.describe_affordance(affordance)
        if not proposal_key:
            return None
        candidate = getattr(affordance, "candidate", None)
        speech_act = getattr(candidate, "speech_act", "") if candidate is not None else ""
        source = getattr(candidate, "source", "") if candidate is not None else getattr(affordance, "owner", "")
        anchor = getattr(candidate, "anchor_id", "") if candidate is not None else getattr(affordance, "affordance_id", "")
        topic = getattr(candidate, "topic", "") if candidate is not None else ""
        plan_like = type("_ProposalPlan", (), {
            "source_kind": source,
            "source_ref": anchor,
            "evidence_refs": getattr(candidate, "evidence_refs", ()) if candidate is not None else (),
            "semantics": type("_Semantics", (), {"speech_act": speech_act})(),
            "topic": topic,
        })()
        return self.note_plan_outcome(plan_like, outcome=outcome, now=current)

    def note_plan_outcome(
        self,
        plan: Any,
        *,
        outcome: str,
        now: float | None = None,
    ) -> ProposalLifecycleRecord:
        current = float(time.time() if now is None else now)
        proposal_key, evidence = self.describe_plan(plan)
        normalized = str(outcome or "").strip().lower()
        if not proposal_key:
            proposal_key = f"untracked:{int(current * 1000)}"
        with self._lock:
            previous = self._records.get(proposal_key)
            created_at = previous.created_at if previous is not None else current
            silence_count = previous.silence_count if previous is not None and previous.evidence_fingerprint == evidence else 0
            failure_count = previous.failure_count if previous is not None and previous.evidence_fingerprint == evidence else 0
            if normalized in {"silence", "body_only"}:
                silence_count += 1
                delay = self.SILENCE_BACKOFF[min(silence_count - 1, len(self.SILENCE_BACKOFF) - 1)]
                record = ProposalLifecycleRecord(
                    proposal_key=proposal_key,
                    state=ProposalLifecycleState.DEFERRED,
                    evidence_fingerprint=evidence,
                    created_at=created_at,
                    updated_at=current,
                    next_eligible_at=current + delay,
                    silence_count=silence_count,
                    failure_count=failure_count,
                    outcome=normalized,
                )
            elif normalized in {"realization_failed", "no_public_text", "stage_silence"}:
                failure_count += 1
                delay = min(60.0, 10.0 * (2 ** max(0, failure_count - 1)))
                record = ProposalLifecycleRecord(
                    proposal_key=proposal_key,
                    state=ProposalLifecycleState.DEFERRED,
                    evidence_fingerprint=evidence,
                    created_at=created_at,
                    updated_at=current,
                    next_eligible_at=current + delay,
                    silence_count=silence_count,
                    failure_count=failure_count,
                    outcome=normalized,
                )
            elif normalized in {"spoken", "committed"}:
                record = ProposalLifecycleRecord(
                    proposal_key=proposal_key,
                    state=ProposalLifecycleState.RETIRED,
                    evidence_fingerprint=evidence,
                    created_at=created_at,
                    updated_at=current,
                    next_eligible_at=current + 120.0,
                    silence_count=0,
                    failure_count=0,
                    outcome=normalized,
                )
            else:
                record = ProposalLifecycleRecord(
                    proposal_key=proposal_key,
                    state=ProposalLifecycleState.ACTIVE,
                    evidence_fingerprint=evidence,
                    created_at=created_at,
                    updated_at=current,
                    next_eligible_at=0.0,
                    silence_count=silence_count,
                    failure_count=failure_count,
                    outcome=normalized or "active",
                )
            self._records[proposal_key] = record
            # Bounded GC: proposal rất cũ không còn ảnh hưởng decision.
            cutoff = current - 3600.0
            stale = [key for key, item in self._records.items() if item.updated_at < cutoff]
            for key in stale:
                self._records.pop(key, None)
            return record

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            items = sorted(self._records.values(), key=lambda item: item.updated_at, reverse=True)
        return tuple(item.to_dict() for item in items[:32])


def ensure_proposal_lifecycle_runtime(host: Any) -> ProposalLifecycleRuntime:
    runtime = getattr(host, "proposal_lifecycle_runtime", None)
    if isinstance(runtime, ProposalLifecycleRuntime):
        return runtime
    runtime = ProposalLifecycleRuntime()
    try:
        setattr(host, "proposal_lifecycle_runtime", runtime)
    except Exception:
        pass
    return runtime


__all__ = [
    "ProposalEligibility",
    "ProposalLifecycleRecord",
    "ProposalLifecycleRuntime",
    "ProposalLifecycleState",
    "ensure_proposal_lifecycle_runtime",
]

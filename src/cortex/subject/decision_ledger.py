# -*- coding: utf-8 -*-
"""Sổ quyết định tích hợp cho Subject Core V56.

PDF yêu cầu một trace có thể đi từ event -> state -> appraisal -> intention ->
decision -> outcome -> hindsight. Module này chỉ là *projection kiểm toán* của
các authority đã tồn tại. Nó không chọn hành động, không sửa Need, không tạo ký
ức và tuyệt đối không lưu chain-of-thought.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping, Sequence


def _copy_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


def _bounded_tuple(values: Sequence[str] | None, *, limit: int = 16) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item) for item in (values or ()) if str(item or "").strip()))[:limit]


@dataclass(frozen=True, slots=True)
class DecisionLedgerRecord:
    """Một causal trace xuyên suốt một observation.

    Các state snapshot đều là dữ liệu typed/public-safe. ``reason_summary`` chỉ
    chứa reason đã được Executive công khai trong commit, không chứa suy nghĩ ẩn.
    """

    trace_id: str
    observation_id: str
    correlation_id: str
    started_at: float
    observation_kind: str = ""
    frame_id: str = ""
    decision_id: str = ""
    outcome_id: str = ""
    stage: str = "observed"
    before_state: Mapping[str, Any] = field(default_factory=dict)
    appraisal_state: Mapping[str, Any] = field(default_factory=dict)
    decision_state: Mapping[str, Any] = field(default_factory=dict)
    outcome_state: Mapping[str, Any] = field(default_factory=dict)
    after_state: Mapping[str, Any] = field(default_factory=dict)
    causal_refs: tuple[str, ...] = ()
    completed_at: float = 0.0

    def __post_init__(self) -> None:
        if not str(self.trace_id or "").strip():
            raise ValueError("trace_id must not be empty")
        if not str(self.observation_id or "").strip():
            raise ValueError("observation_id must not be empty")
        object.__setattr__(self, "before_state", _copy_mapping(self.before_state))
        object.__setattr__(self, "appraisal_state", _copy_mapping(self.appraisal_state))
        object.__setattr__(self, "decision_state", _copy_mapping(self.decision_state))
        object.__setattr__(self, "outcome_state", _copy_mapping(self.outcome_state))
        object.__setattr__(self, "after_state", _copy_mapping(self.after_state))
        object.__setattr__(self, "causal_refs", _bounded_tuple(self.causal_refs, limit=24))

    @property
    def complete(self) -> bool:
        return bool(self.outcome_id and self.completed_at > 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.integrated_decision_ledger.v1",
            "trace_id": self.trace_id,
            "observation_id": self.observation_id,
            "correlation_id": self.correlation_id,
            "observation_kind": self.observation_kind,
            "started_at": self.started_at,
            "frame_id": self.frame_id,
            "decision_id": self.decision_id,
            "outcome_id": self.outcome_id,
            "stage": self.stage,
            "before_state": dict(self.before_state),
            "appraisal_state": dict(self.appraisal_state),
            "decision_state": dict(self.decision_state),
            "outcome_state": dict(self.outcome_state),
            "after_state": dict(self.after_state),
            "causal_refs": list(self.causal_refs),
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DecisionLedgerRecord":
        return cls(
            trace_id=str(payload.get("trace_id") or "trace:restored"),
            observation_id=str(payload.get("observation_id") or "observation:restored"),
            correlation_id=str(payload.get("correlation_id") or ""),
            observation_kind=str(payload.get("observation_kind") or ""),
            started_at=float(payload.get("started_at", 0.0) or 0.0),
            frame_id=str(payload.get("frame_id") or ""),
            decision_id=str(payload.get("decision_id") or ""),
            outcome_id=str(payload.get("outcome_id") or ""),
            stage=str(payload.get("stage") or "observed"),
            before_state=dict(payload.get("before_state") or {}),
            appraisal_state=dict(payload.get("appraisal_state") or {}),
            decision_state=dict(payload.get("decision_state") or {}),
            outcome_state=dict(payload.get("outcome_state") or {}),
            after_state=dict(payload.get("after_state") or {}),
            causal_refs=tuple(str(item) for item in payload.get("causal_refs", ())),
            completed_at=float(payload.get("completed_at", 0.0) or 0.0),
        )


class IntegratedDecisionLedger:
    """Bounded in-memory projection, có thể persist cùng Subject projection."""

    def __init__(self, *, max_records: int = 128) -> None:
        self.max_records = max(16, min(2048, int(max_records)))
        self._records: list[DecisionLedgerRecord] = []
        self._by_frame: dict[str, int] = {}
        self._by_decision: dict[str, int] = {}

    def _reindex(self) -> None:
        self._by_frame.clear()
        self._by_decision.clear()
        for index, record in enumerate(self._records):
            if record.frame_id:
                self._by_frame[record.frame_id] = index
            if record.decision_id:
                self._by_decision[record.decision_id] = index

    def _trim(self) -> None:
        overflow = len(self._records) - self.max_records
        if overflow > 0:
            del self._records[:overflow]
            self._reindex()

    def begin(
        self,
        *,
        trace_id: str,
        observation_id: str,
        observation_kind: str,
        correlation_id: str,
        started_at: float,
        frame_id: str,
        before_state: Mapping[str, Any],
        appraisal_state: Mapping[str, Any],
        causal_refs: Sequence[str] = (),
    ) -> DecisionLedgerRecord:
        record = DecisionLedgerRecord(
            trace_id=trace_id,
            observation_id=observation_id,
            observation_kind=observation_kind,
            correlation_id=correlation_id,
            started_at=float(started_at),
            frame_id=frame_id,
            stage="appraised",
            before_state=before_state,
            appraisal_state=appraisal_state,
            causal_refs=tuple(causal_refs),
        )
        self._records.append(record)
        self._trim()
        self._reindex()
        return record

    def note_decision(
        self,
        *,
        frame_id: str,
        decision_id: str,
        decision_state: Mapping[str, Any],
        causal_refs: Sequence[str] = (),
    ) -> DecisionLedgerRecord | None:
        index = self._by_frame.get(frame_id)
        if index is None:
            return None
        current = self._records[index]
        updated = replace(
            current,
            decision_id=str(decision_id or ""),
            stage="decided",
            decision_state=decision_state,
            causal_refs=_bounded_tuple((*current.causal_refs, *tuple(causal_refs)), limit=24),
        )
        self._records[index] = updated
        self._reindex()
        return updated

    def note_outcome(
        self,
        *,
        decision_id: str,
        outcome_id: str,
        outcome_state: Mapping[str, Any],
        after_state: Mapping[str, Any],
        completed_at: float,
        causal_refs: Sequence[str] = (),
    ) -> DecisionLedgerRecord | None:
        index = self._by_decision.get(decision_id)
        if index is None:
            return None
        current = self._records[index]
        updated = replace(
            current,
            outcome_id=str(outcome_id or ""),
            stage="learned",
            outcome_state=outcome_state,
            after_state=after_state,
            completed_at=float(completed_at),
            causal_refs=_bounded_tuple((*current.causal_refs, *tuple(causal_refs)), limit=24),
        )
        self._records[index] = updated
        self._reindex()
        return updated

    def latest(self, *, limit: int = 20) -> tuple[DecisionLedgerRecord, ...]:
        count = max(1, min(self.max_records, int(limit)))
        return tuple(self._records[-count:])

    def to_projection(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        records = self._records if limit is None else self._records[-max(1, int(limit)):]
        return [item.to_dict() for item in records]

    def restore(self, payload: Sequence[Mapping[str, Any]] | None) -> None:
        restored = [DecisionLedgerRecord.from_dict(item) for item in (payload or ()) if isinstance(item, Mapping)]
        self._records = restored[-self.max_records:]
        self._reindex()


__all__ = ["DecisionLedgerRecord", "IntegratedDecisionLedger"]

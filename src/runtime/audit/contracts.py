# -*- coding: utf-8 -*-
"""Append-only audit contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class AuditEventKind(StrEnum):
    OBSERVATION = "observation"
    APPRAISAL = "appraisal"
    NEED_ACTIVATION = "need_activation"
    DECISION = "decision"
    AUTHORSHIP = "authorship"
    ACTION_REQUEST = "action_request"
    WORLD_OUTCOME = "world_outcome"
    LEARNING = "learning"
    POLICY = "policy"
    DIALOGUE = "dialogue"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    subject_id: str
    kind: AuditEventKind | str
    entity_id: str
    created_at: float
    payload: Mapping[str, Any]
    causal_refs: tuple[str, ...] = ()
    correlation_id: str = ""
    algorithm_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value if isinstance(self.kind, StrEnum) else str(self.kind)
        data["payload"] = dict(self.payload)
        return data


@dataclass(frozen=True, slots=True)
class CausalTrace:
    entity_id: str
    events: tuple[AuditEvent, ...]
    missing_refs: tuple[str, ...]
    chain_valid: bool


@dataclass(frozen=True, slots=True)
class ReplayResult:
    subject_id: str
    event_count: int
    projection: Mapping[str, Any]
    projection_hash: str
    chain_valid: bool

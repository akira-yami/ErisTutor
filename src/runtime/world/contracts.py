# -*- coding: utf-8 -*-
"""Typed actuator and sandbox contracts.

Execution-facing data crosses this boundary, private mind state does not.  V6
also makes capability tokens signed, audience-bound and request-scoped.  Trust
based on "this object probably came from the right function" was charming for
about five minutes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from cortex.subject.contracts import ActionRequest, WorldOutcome


class SandboxCommandKind(StrEnum):
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    RUN_COMMAND = "run_command"
    HTTP_REQUEST = "http_request"
    BODY_FRAME = "body_frame"
    TTS = "tts"
    SYSTEM_ACTION = "system_action"
    SIMULATE = "simulate"


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class CapabilityToken:
    token_id: str
    subject_id: str
    capabilities: frozenset[str]
    issued_at: float
    expires_at: float
    policy_version: str
    issuer: str
    audience: str
    key_id: str
    signature: str
    scope: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "token_id",
            "subject_id",
            "policy_version",
            "issuer",
            "audience",
            "key_id",
            "signature",
        ):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} cannot be empty")
        if self.expires_at < self.issued_at:
            raise ValueError("expires_at cannot precede issued_at")
        object.__setattr__(self, "capabilities", frozenset(str(item) for item in self.capabilities if str(item)))
        object.__setattr__(self, "scope", _frozen_mapping(self.scope))

    def permits(self, capability: str, *, now: float) -> bool:
        return self.issued_at <= now <= self.expires_at and capability in self.capabilities

    def signing_payload(self) -> dict[str, Any]:
        """Return the canonical fields covered by the capability signature."""
        return {
            "token_id": self.token_id,
            "subject_id": self.subject_id,
            "capabilities": sorted(self.capabilities),
            "issued_at": float(self.issued_at),
            "expires_at": float(self.expires_at),
            "policy_version": self.policy_version,
            "issuer": self.issuer,
            "audience": self.audience,
            "key_id": self.key_id,
            "scope": dict(self.scope),
        }


@dataclass(frozen=True, slots=True)
class SandboxCommand:
    command_id: str
    action_id: str
    decision_id: str
    kind: SandboxCommandKind | str
    arguments: Mapping[str, Any]
    requested_capability: str
    idempotency_key: str
    created_at: float
    simulate: bool = False
    subject_id: str = ""
    thread_id: str = ""
    delivery_id: str = ""

    def __post_init__(self) -> None:
        for name in ("command_id", "action_id", "decision_id", "requested_capability", "idempotency_key"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} cannot be empty")
        if not isinstance(self.created_at, (int, float)):
            raise TypeError("created_at must be numeric")
        object.__setattr__(self, "arguments", _frozen_mapping(self.arguments))


@dataclass(frozen=True, slots=True)
class ActionReceipt:
    request: ActionRequest
    outcome: WorldOutcome
    policy_transforms: tuple[str, ...] = ()
    rollback_handle: str | None = None

    def to_world_outcome(self) -> WorldOutcome:
        return self.outcome


class SandboxPort(Protocol):
    async def apply(self, command: SandboxCommand, capability: CapabilityToken) -> WorldOutcome: ...


class ActuatorPort(Protocol):
    async def execute(self, request: ActionRequest) -> ActionReceipt: ...

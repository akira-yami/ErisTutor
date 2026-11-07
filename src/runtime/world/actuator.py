# -*- coding: utf-8 -*-
"""Actuator adapter from Subject Core requests to sandbox commands."""
from __future__ import annotations

import time
import uuid
from typing import Callable, Mapping

from cortex.subject.contracts import ActionRequest
from .contracts import ActionReceipt, CapabilityToken, SandboxCommand, SandboxCommandKind
from .sandbox import SandboxWorld


TokenProvider = Callable[[ActionRequest, str], CapabilityToken]


class SandboxActuator:
    def __init__(
        self,
        *,
        sandbox: SandboxWorld,
        token_provider: TokenProvider,
        kind_map: Mapping[str, SandboxCommandKind | str] | None = None,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not callable(token_provider):
            raise TypeError("token_provider is required")
        self._sandbox = sandbox
        self._token_provider = token_provider
        self._kind_map = dict(kind_map or {})
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def _command_kind(self, request: ActionRequest) -> SandboxCommandKind | str:
        mapped = self._kind_map.get(request.kind)
        if mapped is not None:
            return mapped
        if request.kind == "speak":
            return SandboxCommandKind.TTS
        if request.kind in {"observe", "investigate", "preserve_commitment", "practice", "request_capability"}:
            return SandboxCommandKind.SIMULATE
        return request.kind

    async def execute(self, request: ActionRequest) -> ActionReceipt:
        command_id = self._id_factory()
        token = self._token_provider(request, command_id)
        requested_capability = request.requested_capabilities[0] if request.requested_capabilities else request.kind
        command_kind = self._command_kind(request)
        command = SandboxCommand(
            command_id=command_id,
            action_id=request.action_id,
            decision_id=request.decision_id,
            kind=command_kind,
            arguments=request.arguments,
            requested_capability=requested_capability,
            idempotency_key=request.idempotency_key,
            created_at=request.created_at,
            simulate=command_kind is SandboxCommandKind.SIMULATE,
            subject_id=token.subject_id,
            thread_id=request.thread_id,
            delivery_id=request.delivery_id,
        )
        outcome = await self._sandbox.apply(command, token)
        return ActionReceipt(request=request, outcome=outcome)

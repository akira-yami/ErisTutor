# -*- coding: utf-8 -*-
"""Composition root duy nhất của Subject Core V11.

Không feature flag, không fallback cognition cũ. Nếu Subject Core hoặc Sandbox
không khởi tạo được thì runtime phải dừng, thay vì lén đổi sang một hợp đồng hành
vi khác mà audit không biết.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from actions.contracts import VALID_SYSTEM_ACTIONS
from cortex.subject import SubjectService, SubjectServiceConfig
from cortex.subject.contracts import ActionRequest
from memory.subject import SubjectRepository
from runtime.audit import ForensicStore
from runtime.mind_loop import MindLoop, SubjectCommitPort
from runtime.subject_runtime import SubjectRuntime
from runtime.world import (
    CapabilityAuthority,
    CapabilityPolicy,
    CapabilityToken,
    CommandPolicy,
    SafeHttpConfig,
    SafeHttpTransport,
    SandboxActuator,
    SandboxWorld,
)

FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


@dataclass(slots=True)
class SubjectCoreComponents:
    repository: SubjectRepository
    forensic_store: ForensicStore
    subject_service: SubjectService
    sandbox_world: SandboxWorld
    actuator: SandboxActuator
    mind_loop: MindLoop
    runtime: SubjectRuntime
    capability_authority: CapabilityAuthority
    token_provider: Callable[[ActionRequest, str], CapabilityToken]

    def close(self) -> None:
        self.forensic_store.close()
        self.repository.close()


def _csv_env(name: str, default: str) -> frozenset[str]:
    return frozenset(
        item.strip()
        for item in str(os.getenv(name, default) or "").split(",")
        if item.strip()
    )


def _bool_env(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default) or default).lower() not in FALSE_VALUES


def _request_network_host(request: ActionRequest) -> str | None:
    """Lấy hostname canonical để khóa token đúng request HTTP."""
    if request.kind != "http_request" and "http_request" not in request.requested_capabilities:
        return None
    try:
        url = SafeHttpTransport.canonical_url(request.arguments)
        host = (urlsplit(url).hostname or "").strip().lower().rstrip(".")
        return host.encode("idna").decode("ascii") if host else None
    except (ValueError, UnicodeError):
        return None


def build_subject_core_components(
    *,
    data_root: str | os.PathLike[str] | None = None,
    workspace_root: str | os.PathLike[str] | None = None,
    action_executor: Any = None,
    tts_handler: Any = None,
    body_handler: Any = None,
    network_handler: Any = None,
    committer_provider: Callable[[], SubjectCommitPort | None] | None = None,
) -> SubjectCoreComponents:
    """Dựng đường duy nhất Mind → Actuator → Sandbox → Audit."""

    configured_root = data_root or os.getenv("ERIS_SUBJECT_CORE_DATA_DIR")
    if configured_root is None and os.getenv("PYTEST_CURRENT_TEST"):
        configured_root = tempfile.mkdtemp(prefix="eris-subject-core-v8-test-")
    root = Path(configured_root or "data/subject_core").resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = Path(workspace_root or os.getenv("ERIS_SUBJECT_WORKSPACE") or os.getcwd()).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    repository = SubjectRepository(root / "subject.db")
    forensic = ForensicStore(root / "audit.db")
    service = SubjectService(
        config=SubjectServiceConfig(
            subject_id=str(os.getenv("ERIS_SUBJECT_ID", "eris") or "eris"),
            deterministic=_bool_env("ERIS_SUBJECT_DETERMINISTIC"),
            random_seed=int(os.getenv("ERIS_SUBJECT_RANDOM_SEED", "0") or 0),
            algorithm_version="subject-service-v12-validated-agency-reflection",
        ),
        repository=repository,
        audit=forensic,
    )

    # Secret không còn là một file đơn. Keyring giữ khóa active và verify-only.
    authority = CapabilityAuthority.from_keyring_directory(
        os.getenv("ERIS_CAPABILITY_KEYRING_DIR") or (root / "capability_keyring"),
        issuer=str(os.getenv("ERIS_CAPABILITY_ISSUER", "eris-subject-core") or "eris-subject-core"),
        audience=str(os.getenv("ERIS_CAPABILITY_AUDIENCE", "eris-sandbox") or "eris-sandbox"),
        initial_key_id=str(os.getenv("ERIS_CAPABILITY_INITIAL_KEY_ID", "local-hmac-v1") or "local-hmac-v1"),
    )

    network_enabled = _bool_env("ERIS_SANDBOX_NETWORK_ENABLED")
    allowed_hosts = _csv_env("ERIS_SANDBOX_NETWORK_HOSTS", "")
    allowed_methods = _csv_env("ERIS_SANDBOX_HTTP_METHODS", "GET,HEAD,POST")
    allowed_headers = _csv_env("ERIS_SANDBOX_HTTP_HEADERS", "accept,content-type,user-agent,if-none-match")
    max_runtime_seconds = max(1, int(os.getenv("ERIS_SANDBOX_MAX_RUNTIME_SECONDS", "30") or 30))
    max_output_bytes = max(1, int(os.getenv("ERIS_SANDBOX_MAX_OUTPUT_BYTES", "1000000") or 1_000_000))
    max_http_request_bytes = max(0, int(os.getenv("ERIS_SANDBOX_HTTP_MAX_REQUEST_BYTES", "256000") or 256_000))
    max_http_redirects = min(10, max(0, int(os.getenv("ERIS_SANDBOX_HTTP_MAX_REDIRECTS", "0") or 0)))
    require_https = _bool_env("ERIS_SANDBOX_HTTP_REQUIRE_HTTPS", "1")
    allow_private_networks = _bool_env("ERIS_SANDBOX_ALLOW_PRIVATE_NETWORKS", "0")

    command_policy = CommandPolicy(
        executable_allowlist=_csv_env("ERIS_SANDBOX_EXECUTABLES", "python,python3,git"),
        workspace_root=str(workspace),
        network_enabled=network_enabled,
        allowed_network_hosts=allowed_hosts,
        allowed_http_methods=allowed_methods,
        allowed_http_headers=allowed_headers,
        environment_allowlist=_csv_env("ERIS_SANDBOX_ENVIRONMENT", ""),
        max_runtime_seconds=max_runtime_seconds,
        max_output_bytes=max_output_bytes,
        max_memory_mb=max(16, int(os.getenv("ERIS_SANDBOX_MAX_MEMORY_MB", "512") or 512)),
        max_http_request_bytes=max_http_request_bytes,
        max_http_redirects=max_http_redirects,
        require_https=require_https,
        allow_private_networks=allow_private_networks,
        policy_version="sandbox-policy-v7-inner-core",
    )
    policy = CapabilityPolicy(command_policy, token_verifier=authority.verify)

    if network_enabled and network_handler is None:
        # Transport mặc định tự chống SSRF; caller vẫn có thể inject transport đã audit riêng.
        network_handler = SafeHttpTransport(
            SafeHttpConfig(
                allowed_hosts=allowed_hosts,
                allowed_methods=allowed_methods,
                allowed_request_headers=allowed_headers,
                timeout_seconds=float(os.getenv("ERIS_SANDBOX_HTTP_TIMEOUT_SECONDS", "15") or 15),
                max_response_bytes=max_output_bytes,
                max_request_bytes=max_http_request_bytes,
                max_redirects=max_http_redirects,
                require_https=require_https,
                allow_private_networks=allow_private_networks,
            )
        )

    sandbox = SandboxWorld(
        policy=policy,
        repository=repository,
        audit=forensic,
        tts_handler=tts_handler,
        body_handler=body_handler,
        network_handler=network_handler,
    )

    allowed_capabilities = frozenset(VALID_SYSTEM_ACTIONS) | _csv_env(
        "ERIS_SANDBOX_CAPABILITIES",
        "observe,investigate,practice,preserve_commitment,request_capability,speak,simulate,"
        "read_file,write_file,run,http_request,system_action",
    )
    token_ttl = min(300.0, max(1.0, float(os.getenv("ERIS_CAPABILITY_TTL_SECONDS", "60") or 60)))

    def token_provider(request: ActionRequest, command_id: str) -> CapabilityToken:
        if request is None or not command_id:
            raise ValueError("request and command_id are required for capability issuance")
        requested = frozenset(request.requested_capabilities or (request.kind,))
        disallowed = requested - allowed_capabilities
        if disallowed:
            raise PermissionError(f"capability not configured: {sorted(disallowed)}")
        scope: dict[str, Any] = {
            "command_ids": (command_id,),
            "action_ids": (request.action_id,),
            "decision_ids": (request.decision_id,),
            "idempotency_keys": (request.idempotency_key,),
            "max_output_bytes": policy.command_policy.max_output_bytes,
        }
        network_host = _request_network_host(request)
        if network_host:
            # Token HTTP chỉ có quyền tới đúng host của request hiện tại.
            scope["network_hosts"] = (network_host,)
        return authority.issue(
            subject_id=service.config.subject_id,
            capabilities=requested,
            policy_version=policy.command_policy.policy_version,
            scope=scope,
            ttl_seconds=token_ttl,
        )

    actuator = SandboxActuator(sandbox=sandbox, token_provider=token_provider)
    mind_loop = MindLoop(
        subject=service,
        committer_provider=committer_provider,
        max_reviews=1,
    )
    runtime = SubjectRuntime(
        mind=service,
        mind_loop=mind_loop,
        actuator=actuator,
        dialogue=None,
        audit=forensic,
    )

    if action_executor is not None:
        bind = getattr(action_executor, "bind_sandbox_world", None)
        if not callable(bind):
            raise TypeError("action_executor must expose bind_sandbox_world")
        bind_subject = getattr(action_executor, "bind_subject_service", None)
        if not callable(bind_subject):
            raise TypeError("action_executor must expose bind_subject_service")
        bind_subject(service)
        bind(sandbox, token_provider)

    return SubjectCoreComponents(
        repository=repository,
        forensic_store=forensic,
        subject_service=service,
        sandbox_world=sandbox,
        actuator=actuator,
        mind_loop=mind_loop,
        runtime=runtime,
        capability_authority=authority,
        token_provider=token_provider,
    )


__all__ = ["SubjectCoreComponents", "build_subject_core_components"]

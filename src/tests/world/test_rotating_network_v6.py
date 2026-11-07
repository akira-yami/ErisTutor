from __future__ import annotations

from pathlib import Path

import pytest

from cortex.subject.contracts import ActionRequest
from runtime.subject_bootstrap import build_subject_core_components
from runtime.world import (
    CapabilityAuthority,
    CapabilityKeyRing,
    CapabilityPolicy,
    CommandPolicy,
    HttpResponseSnapshot,
    SafeHttpConfig,
    SafeHttpTransport,
    SandboxCommand,
    SandboxCommandKind,
)


class MutableClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_keyring_rotation_keeps_old_tokens_valid_and_live_reloads(tmp_path: Path) -> None:
    clock = MutableClock()
    directory = tmp_path / "keyring"
    ring_a = CapabilityKeyRing.load_or_create(directory, initial_key_id="key-a", clock=clock)
    ring_b = CapabilityKeyRing.load_or_create(directory, initial_key_id="key-a", clock=clock)
    authority_a = CapabilityAuthority(keyring=ring_a, issuer="issuer", audience="sandbox", clock=clock)
    authority_b = CapabilityAuthority(keyring=ring_b, issuer="issuer", audience="sandbox", clock=clock)

    old_token = authority_a.issue(
        subject_id="eris",
        capabilities={"simulate"},
        policy_version="v6",
        scope={"command_ids": ("old",)},
    )
    clock.value += 1.0
    assert ring_b.rotate(new_key_id="key-b") == "key-b"

    # authority_a đang sống vẫn tự nạp manifest mới khi phát token tiếp theo.
    new_token = authority_a.issue(
        subject_id="eris",
        capabilities={"simulate"},
        policy_version="v6",
        scope={"command_ids": ("new",)},
    )

    assert old_token.key_id == "key-a"
    assert new_token.key_id == "key-b"
    assert authority_a.verify(old_token) == (True, "")
    assert authority_b.verify(new_token) == (True, "")
    assert CapabilityKeyRing.load_or_create(directory).key_ids == ("key-a", "key-b")


def test_safe_http_rejects_private_dns_result() -> None:
    transport = SafeHttpTransport(
        SafeHttpConfig(allowed_hosts=frozenset({"api.example.com"})),
        resolver=lambda host, port: ("127.0.0.1",),
    )

    with pytest.raises(PermissionError, match="dns_private_or_special_address_denied"):
        transport.prepare({"url": "https://api.example.com/data", "method": "GET"})


def test_safe_http_prepares_global_ip_and_filters_headers() -> None:
    transport = SafeHttpTransport(
        SafeHttpConfig(allowed_hosts=frozenset({"api.example.com"})),
        resolver=lambda host, port: ("93.184.216.34",),
    )

    prepared = transport.prepare(
        {
            "url": "https://api.example.com/v1/items?q=eris",
            "method": "GET",
            "headers": {"Accept": "application/json"},
        }
    )

    assert prepared.host == "api.example.com"
    assert prepared.resolved_ip == "93.184.216.34"
    assert prepared.target == "/v1/items?q=eris"
    assert prepared.headers == {"accept": "application/json"}

    with pytest.raises(PermissionError, match="http_header_denied"):
        transport.prepare({"url": "https://api.example.com/", "headers": {"Host": "evil.invalid"}})


def test_safe_http_revalidates_redirect_target() -> None:
    def resolver(host: str, port: int):
        if host == "public.example":
            return ("93.184.216.34",)
        return ("10.0.0.7",)

    transport = SafeHttpTransport(
        SafeHttpConfig(
            allowed_hosts=frozenset({"public.example", "internal.example"}),
            max_redirects=1,
        ),
        resolver=resolver,
    )
    transport._send_once = lambda request: HttpResponseSnapshot(  # type: ignore[method-assign]
        status=302,
        headers={"location": "https://internal.example/secret"},
        body=b"",
        truncated=False,
    )

    with pytest.raises(PermissionError, match="dns_private_or_special_address_denied"):
        transport._perform({"url": "https://public.example/start", "method": "GET"})


def test_http_policy_requires_https_and_exact_token_host(tmp_path: Path) -> None:
    authority = CapabilityAuthority(
        secret=b"v6-network-policy-secret-at-least-32-bytes",
        issuer="issuer",
        audience="sandbox",
    )
    policy = CapabilityPolicy(
        CommandPolicy(
            executable_allowlist=frozenset(),
            workspace_root=str(tmp_path),
            network_enabled=True,
            allowed_network_hosts=frozenset({"api.example.com", "other.example.com"}),
            policy_version="v6",
        ),
        token_verifier=authority.verify,
    )
    token = authority.issue(
        subject_id="eris",
        capabilities={"http_request"},
        policy_version="v6",
        scope={
            "command_ids": ("command",),
            "action_ids": ("action",),
            "decision_ids": ("decision",),
            "idempotency_keys": ("idem",),
            "network_hosts": ("api.example.com",),
        },
    )

    def command(url: str) -> SandboxCommand:
        return SandboxCommand(
            command_id="command",
            action_id="action",
            decision_id="decision",
            kind=SandboxCommandKind.HTTP_REQUEST,
            arguments={"url": url, "method": "GET"},
            requested_capability="http_request",
            idempotency_key="idem",
            created_at=token.issued_at,
            subject_id="eris",
        )

    assert policy.authorize(command("http://api.example.com/"), token, now=token.issued_at).reason == "https_required"
    assert policy.authorize(command("https://other.example.com/"), token, now=token.issued_at).reason == "host_not_allowlisted"
    assert policy.authorize(command("https://api.example.com/"), token, now=token.issued_at).allowed


def test_bootstrap_scopes_http_token_and_creates_keyring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ERIS_SANDBOX_NETWORK_ENABLED", "1")
    monkeypatch.setenv("ERIS_SANDBOX_NETWORK_HOSTS", "api.example.com")
    components = build_subject_core_components(
        data_root=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    try:
        request = ActionRequest(
            action_id="action",
            decision_id="decision",
            kind="http_request",
            arguments={"url": "https://api.example.com/v1", "method": "GET"},
            expected_outcomes=(),
            requested_capabilities=("http_request",),
            idempotency_key="idem",
            authorship_draft_id="draft",
            created_at=1.0,
        )
        token = components.token_provider(request, "command")
        assert token.scope["network_hosts"] == ("api.example.com",)
        assert (tmp_path / "data" / "capability_keyring" / "keyring.json").is_file()
        assert components.sandbox_world._network_handler is not None
    finally:
        components.close()

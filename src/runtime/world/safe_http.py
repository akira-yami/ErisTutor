# -*- coding: utf-8 -*-
"""HTTP transport chống SSRF cho Sandbox World.

Transport này không dùng proxy từ biến môi trường, không theo redirect một cách
mù quáng và ghim kết nối vào địa chỉ IP đã được kiểm tra. Mỗi redirect lại được
phân tích, allowlist và phân giải DNS từ đầu. Đây không phải firewall thần thánh,
nhưng ít nhất nó không gọi một lệnh HTTP tiện tay nhận URL trực tiếp rồi cầu nguyện.
"""
from __future__ import annotations

import asyncio
import hashlib
import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urljoin, urlsplit, urlunsplit


Resolver = Callable[[str, int], Sequence[str]]


@dataclass(frozen=True, slots=True)
class SafeHttpConfig:
    allowed_hosts: frozenset[str]
    allowed_methods: frozenset[str] = frozenset({"GET", "HEAD", "POST"})
    allowed_request_headers: frozenset[str] = frozenset({"accept", "content-type", "user-agent", "if-none-match"})
    timeout_seconds: float = 15.0
    max_response_bytes: int = 1_000_000
    max_request_bytes: int = 256_000
    max_redirects: int = 0
    require_https: bool = True
    allow_private_networks: bool = False

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_response_bytes <= 0 or self.max_request_bytes < 0:
            raise ValueError("HTTP byte limits are invalid")
        if self.max_redirects < 0 or self.max_redirects > 10:
            raise ValueError("max_redirects must be in [0, 10]")
        object.__setattr__(self, "allowed_hosts", frozenset(_normalize_host(item) for item in self.allowed_hosts))
        object.__setattr__(self, "allowed_methods", frozenset(str(item).upper() for item in self.allowed_methods))
        object.__setattr__(self, "allowed_request_headers", frozenset(str(item).lower() for item in self.allowed_request_headers))


@dataclass(frozen=True, slots=True)
class PreparedHttpRequest:
    url: str
    scheme: str
    host: str
    port: int
    target: str
    method: str
    headers: Mapping[str, str]
    body: bytes | None
    resolved_ip: str


@dataclass(frozen=True, slots=True)
class HttpResponseSnapshot:
    status: int
    headers: Mapping[str, str]
    body: bytes
    truncated: bool


def _normalize_host(value: str) -> str:
    host = str(value or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("host required")
    try:
        # IDNA làm cùng một hostname Unicode có đúng một dạng so sánh.
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("invalid internationalized hostname") from exc


def _default_resolver(host: str, port: int) -> tuple[str, ...]:
    addresses: set[str] = set()
    for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        sockaddr = item[4]
        if sockaddr:
            addresses.add(str(sockaddr[0]))
    if not addresses:
        raise OSError(f"DNS returned no address for {host}")
    return tuple(sorted(addresses))


def _is_forbidden_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    # is_global loại loopback, private, link-local, multicast, reserved và unspecified.
    return not ip.is_global


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection đi thẳng tới IP đã kiểm tra, không phân giải DNS lần hai."""

    def __init__(self, logical_host: str, resolved_ip: str, port: int, *, timeout: float) -> None:
        super().__init__(logical_host, port=port, timeout=timeout)
        self._resolved_ip = resolved_ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._resolved_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection ghim IP nhưng vẫn xác minh chứng chỉ theo hostname gốc."""

    def __init__(
        self,
        logical_host: str,
        resolved_ip: str,
        port: int,
        *,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(logical_host, port=port, timeout=timeout, context=context)
        self._resolved_ip = resolved_ip
        self._logical_host = logical_host

    def connect(self) -> None:
        raw_sock = socket.create_connection((self._resolved_ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw_sock, server_hostname=self._logical_host)
        except Exception:
            raw_sock.close()
            raise


class SafeHttpTransport:
    """Callable async dùng trực tiếp làm ``SandboxWorld.network_handler``."""

    REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

    def __init__(
        self,
        config: SafeHttpConfig,
        *,
        resolver: Resolver = _default_resolver,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.config = config
        self._resolver = resolver
        self._ssl_context = ssl_context or ssl.create_default_context()

    @staticmethod
    def canonical_url(arguments: Mapping[str, Any]) -> str:
        raw_url = str(arguments.get("url") or "").strip()
        if raw_url:
            return raw_url
        host = _normalize_host(str(arguments.get("host") or ""))
        scheme = str(arguments.get("scheme") or "https").lower()
        raw_path = str(arguments.get("path") or "/")
        path = raw_path if raw_path.startswith("/") else f"/{raw_path}"
        return urlunsplit((scheme, host, path, str(arguments.get("query") or ""), ""))

    def _choose_ip(self, host: str, port: int) -> str:
        addresses = tuple(str(item) for item in self._resolver(host, port))
        if not addresses:
            raise PermissionError("dns_no_address")
        parsed: list[tuple[int, int, str]] = []
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise PermissionError("dns_invalid_address") from exc
            if not self.config.allow_private_networks and _is_forbidden_ip(address):
                raise PermissionError("dns_private_or_special_address_denied")
            parsed.append((ip.version, int(ip), str(ip)))
        return sorted(parsed)[0][2]

    def prepare(self, arguments: Mapping[str, Any], *, override_url: str | None = None, override_method: str | None = None) -> PreparedHttpRequest:
        url = str(override_url or self.canonical_url(arguments)).strip()
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise PermissionError("http_scheme_denied")
        if self.config.require_https and scheme != "https":
            raise PermissionError("https_required")
        if parsed.username is not None or parsed.password is not None:
            raise PermissionError("url_credentials_denied")
        if parsed.fragment:
            raise PermissionError("url_fragment_denied")
        host = _normalize_host(parsed.hostname or "")
        if host not in self.config.allowed_hosts:
            raise PermissionError("host_not_allowlisted")
        try:
            port = parsed.port or (443 if scheme == "https" else 80)
        except ValueError as exc:
            raise PermissionError("port_invalid") from exc
        if port < 1 or port > 65535:
            raise PermissionError("port_invalid")

        method = str(override_method or arguments.get("method") or "GET").upper()
        if method not in self.config.allowed_methods:
            raise PermissionError("http_method_not_allowlisted")

        raw_headers = arguments.get("headers") or {}
        if not isinstance(raw_headers, Mapping):
            raise ValueError("headers must be a mapping")
        headers: dict[str, str] = {}
        for raw_name, raw_value in raw_headers.items():
            name = str(raw_name).strip().lower()
            if name in {"host", "connection", "proxy-authorization", "proxy-connection", "transfer-encoding"}:
                raise PermissionError("http_header_denied")
            if name not in self.config.allowed_request_headers:
                raise PermissionError("http_header_not_allowlisted")
            value = str(raw_value)
            if "\r" in value or "\n" in value:
                raise PermissionError("http_header_injection_denied")
            headers[name] = value

        raw_body = arguments.get("body")
        if raw_body is None:
            body = None
        elif isinstance(raw_body, bytes):
            body = raw_body
        else:
            body = str(raw_body).encode("utf-8")
        if body is not None and len(body) > self.config.max_request_bytes:
            raise ValueError("http_request_body_too_large")
        if method in {"GET", "HEAD"} and body:
            raise ValueError("http_body_not_allowed_for_method")

        target = parsed.path or "/"
        if parsed.query:
            target = f"{target}?{parsed.query}"
        if len(target) > 8192:
            raise ValueError("http_target_too_large")
        resolved_ip = self._choose_ip(host, port)
        clean_url = urlunsplit((scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))
        return PreparedHttpRequest(
            url=clean_url,
            scheme=scheme,
            host=host,
            port=port,
            target=target,
            method=method,
            headers=headers,
            body=body,
            resolved_ip=resolved_ip,
        )

    def _send_once(self, request: PreparedHttpRequest) -> HttpResponseSnapshot:
        if request.scheme == "https":
            connection: http.client.HTTPConnection = _PinnedHTTPSConnection(
                request.host,
                request.resolved_ip,
                request.port,
                timeout=self.config.timeout_seconds,
                context=self._ssl_context,
            )
        else:
            connection = _PinnedHTTPConnection(
                request.host,
                request.resolved_ip,
                request.port,
                timeout=self.config.timeout_seconds,
            )
        host_header = request.host
        default_port = 443 if request.scheme == "https" else 80
        if request.port != default_port:
            host_header = f"{host_header}:{request.port}"
        headers = {"Host": host_header, "Connection": "close", **dict(request.headers)}
        try:
            connection.request(request.method, request.target, body=request.body, headers=headers)
            response = connection.getresponse()
            data = response.read(self.config.max_response_bytes + 1)
            truncated = len(data) > self.config.max_response_bytes
            return HttpResponseSnapshot(
                status=int(response.status),
                headers={str(key).lower(): str(value) for key, value in response.getheaders()},
                body=data[: self.config.max_response_bytes],
                truncated=truncated,
            )
        finally:
            connection.close()

    def _perform(self, arguments: Mapping[str, Any]) -> tuple[str, ...]:
        request = self.prepare(arguments)
        redirects = 0
        while True:
            response = self._send_once(request)
            if response.truncated:
                raise ValueError("http_response_too_large")
            location = response.headers.get("location")
            if response.status not in self.REDIRECT_STATUSES or not location:
                digest = hashlib.sha256(response.body).hexdigest()
                return (
                    f"http_status:{response.status}",
                    f"response_bytes:{len(response.body)}",
                    f"response_sha256:{digest}",
                    f"final_host:{request.host}",
                    f"resolved_ip:{request.resolved_ip}",
                    f"redirects:{redirects}",
                )
            if redirects >= self.config.max_redirects:
                raise PermissionError("http_redirect_denied")
            if request.method not in {"GET", "HEAD"}:
                raise PermissionError("http_redirect_for_method_denied")
            redirects += 1
            next_url = urljoin(request.url, location)
            # Redirect phải trải qua toàn bộ allowlist, DNS và chặn private IP lần nữa.
            request = self.prepare(arguments, override_url=next_url, override_method=request.method)

    async def __call__(self, arguments: Mapping[str, Any]) -> tuple[str, ...]:
        return await asyncio.to_thread(self._perform, dict(arguments))


__all__ = [
    "HttpResponseSnapshot",
    "PreparedHttpRequest",
    "SafeHttpConfig",
    "SafeHttpTransport",
]

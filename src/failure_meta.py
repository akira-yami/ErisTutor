from __future__ import annotations

from typing import Any


FAILURE_KINDS = {
    "provider_unavailable",
    "rate_limited",
    "format_violation",
    "prompt_leak",
    "safety_block",
    "context_oversize",
    "transport_truncated",
    "unknown",
}

FAILURE_PUBLIC_SURFACES = {
    "silent",
    "service_fallback",
    "blocked",
    "protocol_repair",
}

TECHNICAL_FAILURE_KINDS = FAILURE_KINDS - {"prompt_leak"}


def build_failure_meta(
    *,
    kind: str = "unknown",
    stage: str = "",
    origin: str = "",
    public_surface: str = "service_fallback",
    recoverable: bool = False,
    transport_truncated: bool = False,
) -> dict[str, Any]:
    normalized_kind = kind if kind in FAILURE_KINDS else "unknown"
    normalized_surface = (
        public_surface if public_surface in FAILURE_PUBLIC_SURFACES else "service_fallback"
    )
    return {
        "kind": normalized_kind,
        "stage": str(stage or "")[:80],
        "origin": str(origin or "")[:80],
        "public_surface": normalized_surface,
        "recoverable": bool(recoverable),
        "transport_truncated": bool(transport_truncated),
    }


def normalize_failure_meta(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return build_failure_meta(
        kind=str(value.get("kind", "") or "unknown"),
        stage=str(value.get("stage", "") or ""),
        origin=str(value.get("origin", "") or ""),
        public_surface=str(value.get("public_surface", "") or "service_fallback"),
        recoverable=bool(value.get("recoverable", False)),
        transport_truncated=bool(value.get("transport_truncated", False)),
    )


def mark_transport_truncated(value: Any) -> dict[str, Any]:
    meta = normalize_failure_meta(value)
    if meta is None:
        meta = build_failure_meta(
            kind="transport_truncated",
            stage="udp_send",
            origin="transport",
            public_surface="silent",
            recoverable=False,
        )
    meta["transport_truncated"] = True
    if meta.get("kind") not in FAILURE_KINDS:
        meta["kind"] = "transport_truncated"
    return meta


def is_technical_failure_meta(value: Any) -> bool:
    meta = normalize_failure_meta(value)
    if meta is None:
        return False
    return str(meta.get("kind", "")) in TECHNICAL_FAILURE_KINDS

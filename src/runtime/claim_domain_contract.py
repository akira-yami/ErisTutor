# -*- coding: utf-8 -*-
"""Typed claim-manifest của writer: không được rửa lỗi bằng normalization.

V0.2 chỉ kiểm cấu trúc/domain/ref theo allowlist runtime cấp. Semantic entailment
(câu nói có thật sự được nguồn chứng minh?) thuộc verifier v0.3, KHÔNG ở đây.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Sequence

CLAIM_DOMAIN_VERSION = "eris.claim_domains.v42.typed_surface"
KNOWN_CLAIM_DOMAINS = frozenset({
    "internal_state", "conversation_history", "user_statement", "user_profile",
    "shared_episode", "memory", "verified_runtime_clock", "grounded_environment",
    "engagement_state", "discovery_artifact", "self_capability", "self_preference",
    "self_model", "self_history", "opinion", "hypothetical",
})
EVIDENCE_REQUIRED_DOMAINS = frozenset({
    "verified_runtime_clock", "grounded_environment", "conversation_history",
    "user_statement", "user_profile", "shared_episode", "memory", "self_model", "self_history",
})
EVIDENCE_EXEMPT_DOMAINS = frozenset({"opinion", "hypothetical", "internal_state", "self_preference"})

_MISSING = object()


def _freeze(raw: Any) -> Any:
    """Copy cấu trúc model trả về để nó không đổi được sau validation."""
    if isinstance(raw, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in raw.items()})
    if isinstance(raw, (tuple, list)):
        return tuple(_freeze(v) for v in raw)
    return raw


@dataclass(frozen=True, slots=True)
class ClaimManifestEnvelope:
    present: bool
    raw: Any
    structural_status: str  # valid / missing / invalid / oversize
    normalized: tuple[Mapping[str, Any], ...]
    violations: tuple[str, ...]


def inspect_claim_manifest(payload: Any) -> ClaimManifestEnvelope:
    """Chặn lỗi trên RAW trước mọi cắt/ngầm bỏ phần tử.

    Nếu truyền Mapping response, kiểm sự tồn tại của key claim_manifest;
    nếu truyền Sequence, xem như một manifest đã được cấp trực tiếp.
    """
    if isinstance(payload, Mapping):
        present = "claim_manifest" in payload
        raw = payload.get("claim_manifest", _MISSING)
    else:
        present = payload is not None
        raw = payload if present else _MISSING
    if not present:
        return ClaimManifestEnvelope(False, None, "missing", (), ("MANIFEST_MISSING",))

    frozen = _freeze(raw)
    if not isinstance(raw, (list, tuple)):
        return ClaimManifestEnvelope(True, frozen, "invalid", (), ("manifest_not_array",))
    bad: list[str] = []
    too_many: list[str] = []
    if len(raw) > 10:
        too_many.append("claims_exceed_10")
    normalized: list[Mapping[str, Any]] = []
    for idx, entry in enumerate(raw):
        prefix = f"claim[{idx}]"
        if not isinstance(entry, Mapping):
            bad.append(f"{prefix}:not_object")
            continue
        for required in ("domain", "claim", "evidence_refs"):
            if required not in entry:
                bad.append(f"{prefix}:missing_{required}")
        if set(entry) - {"domain", "claim", "evidence_refs"}:
            bad.append(f"{prefix}:unknown_field")
        domain, claim, refs = entry.get("domain"), entry.get("claim"), entry.get("evidence_refs")
        if not isinstance(domain, str) or not domain.strip():
            bad.append(f"{prefix}:invalid_domain")
        if not isinstance(claim, str) or not claim.strip():
            bad.append(f"{prefix}:invalid_claim")
        if not isinstance(refs, (list, tuple)):
            bad.append(f"{prefix}:invalid_evidence_refs")
        else:
            if len(refs) > 8:
                too_many.append(f"{prefix}:refs_exceed_8")
            if any(not isinstance(ref, str) or not ref.strip() for ref in refs):
                bad.append(f"{prefix}:invalid_ref")
        if (isinstance(domain, str) and domain.strip() and isinstance(claim, str)
                and claim.strip() and isinstance(refs, (list, tuple))
                and all(isinstance(ref, str) and ref.strip() for ref in refs)):
            normalized.append(MappingProxyType({
                "domain": domain.strip().lower(),
                "claim": claim,   # KHÔNG cắt 320 ký tự
                "evidence_refs": tuple(refs),  # KHÔNG cắt 8 refs hoặc 140 ký tự
            }))
    status = "invalid" if bad else "oversize" if too_many else "valid"
    return ClaimManifestEnvelope(True, frozen, status,
                                 tuple(normalized), tuple(bad + too_many))


def normalize_claim_manifest(value: Any) -> tuple[dict[str, Any], ...]:
    """Legacy accessor. KHÔNG dùng output này thay inspect trước khi chặn lỗi.

    Invalid trả (), nhưng caller kiểm an toàn phải dùng inspect_claim_manifest
    hoặc validate_claim_manifest để phân biệt nó với manifest rỗng hợp lệ.
    """
    envelope = inspect_claim_manifest(value)
    return tuple({"domain": e["domain"], "claim": e["claim"],
                  "evidence_refs": tuple(e["evidence_refs"])} for e in envelope.normalized) if envelope.structural_status == "valid" else ()


@dataclass(frozen=True, slots=True)
class ClaimDomainDecision:
    allowed: bool
    reason: str
    added_claim_count: int = 0
    violating_domains: tuple[str, ...] = ()
    unknown_evidence_refs: tuple[str, ...] = ()
    manifest_missing: bool = False
    structural_violations: tuple[str, ...] = ()
    evidence_failure_codes: tuple[str, ...] = ()  # HOTFIX 03: không log ref/content nhạy cảm


def validate_claim_manifest(
    manifest: Any, *, allowed_domains: Sequence[str],
    allowed_evidence_refs: Sequence[str] = (), text_present: bool = True,
    evidence_domains_by_ref: Mapping[str, Sequence[str]] | None = None,
) -> ClaimDomainDecision:
    """Runtime allowlist là nguồn quyền, không tin evidence ref do model khai."""
    envelope = manifest if isinstance(manifest, ClaimManifestEnvelope) else inspect_claim_manifest(manifest)
    if envelope.structural_status != "valid":
        status = envelope.structural_status
        # Không yêu cầu manifest khi không có surface để phát (legacy silence).
        if status == "missing" and not text_present:
            return ClaimDomainDecision(True, "no_public_text")
        reason = {"missing": "claim_manifest_missing", "invalid": "claim_manifest_invalid",
                  "oversize": "claim_manifest_oversize"}[status]
        return ClaimDomainDecision(False, reason, len(envelope.normalized),
                                   manifest_missing=status == "missing",
                                   structural_violations=envelope.violations)

    allowed = {str(x).strip().lower() for x in allowed_domains if isinstance(x, str) and x.strip()}
    evidence = {x for x in allowed_evidence_refs if isinstance(x, str) and x}
    bad_domains: list[str] = []
    bad_refs: list[str] = []
    failure_codes: list[str] = []
    # Domain chỉ cấp quyền ở loại nguồn tương ứng, không được lấy ref screen
    # để chứng minh chuyện user nói, hoặc ref assistant để xác nhận action.
    per_ref_domains = ({ref: {domain for domain, refs in evidence_domains_by_ref.items()
                             if ref in refs} for ref in evidence}
                       if evidence_domains_by_ref is not None else None)
    for item in envelope.normalized:
        domain = item["domain"]
        if domain not in KNOWN_CLAIM_DOMAINS or domain not in allowed:
            bad_domains.append(domain)
            continue
        refs = item["evidence_refs"]
        if domain in EVIDENCE_REQUIRED_DOMAINS and not refs:
            bad_refs.append(f"{domain}:missing")
            failure_codes.append("MISSING_REF")
        # Ref nào model đã khai cũng phải là ref được runtime cấp, kể cả domain
        # opinion/hypothetical. Ý kiến MỚI không cần ref, nhưng ref giả không được pass.
        for ref in refs:
            if ref not in evidence:
                bad_refs.append(ref)
                failure_codes.append("UNKNOWN_REF")
            elif (per_ref_domains is not None and domain not in EVIDENCE_EXEMPT_DOMAINS
                  and domain not in per_ref_domains[ref]):
                bad_refs.append(ref)
                failure_codes.append("WRONG_SCOPE")
    if bad_domains:
        return ClaimDomainDecision(False, "claim_domain_not_allowed", len(envelope.normalized),
                                   tuple(dict.fromkeys(bad_domains)), tuple(dict.fromkeys(bad_refs)))
    if bad_refs:
        return ClaimDomainDecision(False, "claim_evidence_not_allowed", len(envelope.normalized),
                                   (), tuple(dict.fromkeys(bad_refs)),
                                   evidence_failure_codes=tuple(dict.fromkeys(failure_codes)))
    return ClaimDomainDecision(True, "claim_domains_grounded", len(envelope.normalized))


__all__ = [
    "CLAIM_DOMAIN_VERSION", "EVIDENCE_EXEMPT_DOMAINS", "EVIDENCE_REQUIRED_DOMAINS",
    "KNOWN_CLAIM_DOMAINS", "ClaimDomainDecision", "ClaimManifestEnvelope",
    "inspect_claim_manifest", "normalize_claim_manifest", "validate_claim_manifest",
]

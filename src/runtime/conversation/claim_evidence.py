# -*- coding: utf-8 -*-
"""Character V1 / v0.1 — registry bằng chứng bất biến của MỘT lượt hội thoại.

Đây là projection CHỈ ĐỌC. Không chấm đúng/sai ngữ nghĩa (Task 5), không ghi
memory (Task 7), không xem ID hoặc lời assistant là bằng chứng thế giới.
Tất cả refs phải xuất phát từ owner nguồn; tuyệt đối không nhận ID do writer tạo.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping, Sequence


# Các domain này là nhãn nguồn, KHÔNG phải quyền khẳng định câu hỏi bất kỳ.
DOMAINS = frozenset({"user_statement", "assistant_utterance", "memory", "runtime_state",
                     "pasted_code", "uploaded_file", "current_screen", "live_vision",
                     "web", "chess_bridge", "capability_result", "fiction"})


def _freeze(value: Any) -> Any:
    """Copy dữ liệu trước khi đóng băng; không giữ reference mutable của store."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items() if not str(k).startswith("__")})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, (int, float, bool, str)) or value is None:
        return value
    # Không serialize repr(object) thành 'fact'. Giá trị không biết kiểu là unknown.
    return None


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    ref: str
    source_kind: str
    owner: str
    subject: str
    scope: str
    recorded_at: float
    temporal_scope: str
    status: str
    content: Any
    parent_refs: tuple[str, ...] = ()
    revision: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"supported", "unknown", "stale", "superseded"}:
            raise ValueError("invalid_evidence_status")
        if not self.ref or not self.owner or not self.scope:
            raise ValueError("evidence_requires_ref_owner_scope")
        object.__setattr__(self, "content", _freeze(self.content))
        object.__setattr__(self, "parent_refs", tuple(str(x) for x in self.parent_refs if str(x)))


def _fingerprint(event_id: str, snapshot_id: str, entries: Sequence[EvidenceEntry]) -> str:
    payload = {
        "event": event_id, "snapshot": snapshot_id,
        "entries": [
            {"ref": e.ref, "kind": e.source_kind, "owner": e.owner,
             "subject": e.subject, "scope": e.scope, "recorded_at": e.recorded_at,
             "temporal_scope": e.temporal_scope, "status": e.status,
             "content": _plain(e.content), "parents": list(e.parent_refs), "revision": e.revision}
            for e in entries
        ],
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EvidenceRegistry:
    event_id: str
    snapshot_id: str
    state_version: int
    entries: tuple[EvidenceEntry, ...]
    allowed_domains: frozenset[str]
    fingerprint: str
    # Token runtime-only: ràng buộc capability receipts với đúng execution.
    session_id: str = ""
    generation: int = 0
    run_id: str = ""
    degraded: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(self, "allowed_domains", frozenset(self.allowed_domains))


def build_evidence_registry(*, event: Any, state: Any | None, affect: Any | None,
                            report: Any | None, ledger_turns: Sequence[Any],
                            memory_records: Sequence[Any], sampled_at: float) -> EvidenceRegistry:
    """Lấy bản sao typed từ report/ledger/canonical records; không parse prompt prose."""
    entries: list[EvidenceEntry] = []
    seen: set[str] = set()

    def add(entry: EvidenceEntry) -> None:
        if entry.ref not in seen:
            seen.add(entry.ref)
            entries.append(entry)

    event_id = str(getattr(event, "event_id", "") or "")
    session_id = str(getattr(event, "session_id", "") or "")
    generation = int(getattr(event, "generation", 0) or 0)
    version = int(getattr(state, "version", 0) or 0)
    snapshot_id = f"state:{session_id}:{version}:{event_id}"
    if state is not None:
        add(EvidenceEntry(ref=snapshot_id, source_kind="runtime_state", owner="StateReducer",
                          subject="eris", scope="state_snapshot", recorded_at=sampled_at,
                          temporal_scope="snapshot_only", status="supported",
                          content={"revision": version,
                                   "last_turn_seq": int(getattr(state, "last_applied_turn_seq", 0) or 0)},
                          revision=str(version)))
    if affect is not None:
        # Affective snapshot chỉ xác nhận sample, không chứng minh sự kiện đời thực.
        add(EvidenceEntry(ref=f"affect:{snapshot_id}", source_kind="runtime_state",
                          owner="AffectiveVoiceState", subject="eris", scope="affect_snapshot",
                          recorded_at=sampled_at, temporal_scope="current_turn",
                          status="supported", content={"channel": str(getattr(affect, "channel", ""))},
                          revision=str(version)))

    # User hiện tại là lời do user NÓI. Nó không chứng minh nội dung đã diễn ra.
    if getattr(event, "kind", "") == "user_turn":
        add(EvidenceEntry(ref=f"event:{event_id}", source_kind="user_statement",
                          owner="ConversationEvent", subject="user", scope="utterance",
                          recorded_at=sampled_at, temporal_scope="current_turn", status="supported",
                          content={"text": str(getattr(event, "text", "") or "")},
                          revision=str(generation)))
    for turn in ledger_turns:
        role = str(getattr(turn, "role", "") or "")
        meta = getattr(turn, "meta", {})
        if not isinstance(meta, Mapping):
            meta = {}
        index = getattr(turn, "index", None)
        if role not in {"user", "assistant"} or not isinstance(index, int):
            continue
        # Session + index là identity thật của ledger; meta không được đổi nó.
        scope = "fiction" if meta.get("scope") == "fiction" else "utterance"
        add(EvidenceEntry(ref=f"ledger:{session_id}:{index}",
                          source_kind="user_statement" if role == "user" else "assistant_utterance",
                          owner="SessionTurnLedger", subject=role, scope=scope,
                          recorded_at=float(getattr(turn, "timestamp", 0) or 0),
                          temporal_scope="session_history", status="supported",
                          content={"text": str(getattr(turn, "text", "") or ""), "role": role},
                          parent_refs=tuple(str(p) for p in (meta.get("parent_refs") or ()) if isinstance(p, str)),
                          revision=str(index)))

    for item in tuple(getattr(report, "evidence_items", ()) or ()) if report is not None else ():
        eid = str(getattr(item, "evidence_id", "") or "")
        source = str(getattr(item, "source", "unknown") or "unknown")
        body = str(getattr(item, "text", "") or "")
        if not eid:
            continue  # Không tạo fallback synthetic ID từ thứ tự phần tử.
        # Mapping sang source_kind Character V1 cần làm TRƯỚC khi kiểm domain.
        if source == "user_text":
            source = "user_statement"
        valid_source = source in DOMAINS
        # filename_only/unknown không chứng minh file. memory preview thiếu record
        # gốc cũng không có quyền được gắn supported. Vision không tươi -> unknown.
        supported = valid_source and source != "memory" and bool(body)
        if source in {"current_screen", "live_vision"} and not bool(getattr(report, "screen_evidence_present", False)):
            supported = False
        if source == "user_statement":
            scope = "utterance"
            subject = "user"
        else:
            scope = "source_excerpt" if supported else "unresolved"
            subject = "environment" if source in {"current_screen", "live_vision"} else "source"
        add(EvidenceEntry(ref=eid, source_kind=source, owner="SourceGroundingReport",
                          subject=subject, scope=scope, recorded_at=sampled_at,
                          temporal_scope="source_report_turn", status="supported" if supported else "unknown",
                          content={"text": body, "role": str(getattr(item, "role", "") or "")},
                          revision=event_id))

    for record in memory_records:
        ref = str(getattr(record, "id", "") or "")
        if not ref:
            continue
        meta = getattr(record, "metadata", {})
        meta = meta if isinstance(meta, Mapping) else {}
        provenance = meta.get("claim_provenance", {})
        provenance = provenance if isinstance(provenance, Mapping) else {}
        origin = str(provenance.get("origin_kind", "") or "")
        scope = str(provenance.get("scope", "") or "")
        supported = bool(origin and scope and provenance.get("root_event_ids"))
        if origin in {"assistant_utterance", "observer_summary"}:
            # Assistant/summary chỉ xác nhận đã có utterance/summary.
            scope = "utterance" if origin == "assistant_utterance" else "summary"
        add(EvidenceEntry(ref=f"memory:{ref}", source_kind="memory", owner="MemoryCognitionCore",
                          subject=str(provenance.get("subject", "unknown") or "unknown"),
                          scope=scope or "unresolved", recorded_at=float(getattr(record, "created_at", 0) or 0),
                          temporal_scope=str(provenance.get("temporal_scope", "legacy_unknown") or "legacy_unknown"),
                          status="supported" if supported else "unknown",
                          content={"text": str(getattr(record, "text", "") or ""),
                                   "layer": str(getattr(record, "layer", "") or "")},
                          parent_refs=tuple(str(x) for x in provenance.get("root_event_ids", ()) if isinstance(x, str)),
                          revision=str(meta.get("revision", "") or "")))

    entries.sort(key=lambda e: e.ref)
    return EvidenceRegistry(event_id=event_id, snapshot_id=snapshot_id, state_version=version,
                            entries=tuple(entries), allowed_domains=frozenset(e.source_kind for e in entries
                                                                              if e.status == "supported"),
                            fingerprint=_fingerprint(event_id, snapshot_id, entries),
                            session_id=session_id, generation=generation)


def extend_evidence_registry(registry: EvidenceRegistry, receipts: Sequence[Any]) -> EvidenceRegistry:
    """Chỉ nối result thực của đúng event/run/generation; status error không thành success."""
    new_entries = list(registry.entries)
    seen = {e.ref for e in new_entries}
    for receipt in receipts:
        if (str(getattr(receipt, "event_id", "")) != registry.event_id
                or str(getattr(receipt, "session_id", "")) != registry.session_id
                or int(getattr(receipt, "generation", -1)) != registry.generation
                or not registry.run_id
                or str(getattr(receipt, "run_id", "")) != registry.run_id):
            continue
        ref = f"capability:{getattr(receipt, 'invocation_id', '')}"
        if not getattr(receipt, "invocation_id", "") or ref in seen:
            continue
        status = str(getattr(receipt, "status", "unknown") or "unknown").lower()
        new_entries.append(EvidenceEntry(
            ref=ref, source_kind="capability_result", owner="RootCapabilityRegistry",
            subject=str(getattr(receipt, "name", "")), scope="capability_execution",
            recorded_at=0.0, temporal_scope="current_run",
            status="supported" if status in {"completed", "success", "succeeded", "ok"} else "unknown",
            content={"status": status, "result": getattr(receipt, "result", {})},
            parent_refs=tuple(getattr(receipt, "source_refs", ()) or ()),
            revision=registry.run_id))
        seen.add(ref)
    new_entries.sort(key=lambda e: e.ref)
    return replace(registry, entries=tuple(new_entries),
                   allowed_domains=frozenset(e.source_kind for e in new_entries if e.status == "supported"),
                   fingerprint=_fingerprint(registry.event_id, registry.snapshot_id, new_entries))


def render_evidence_context(registry: EvidenceRegistry) -> str:
    """Render phần cần thiết cho verifier/writer; hạn chế private content và size.

    V0.1 không gọi hàm này tự động cho Root prompt. Không đem full memory
    registry vào model; Task 2–6 sẽ sử dụng subset khi verifier có quyền kiểm.
    """
    lines = []
    used_chars = 0
    for entry in registry.entries:
        body = entry.content
        if isinstance(body, Mapping):
            body = body.get("text", body.get("status", ""))
        if entry.source_kind == "memory" and entry.status != "supported":
            body = "[legacy provenance unresolved]"
        line = (f"ref={entry.ref} kind={entry.source_kind} scope={entry.scope} "
                f"status={entry.status} text={str(body or '')[:240]}")
        # Không cắt giữa một entry; bỏ nguyên dòng nếu quá ngân sách.
        if used_chars + len(line) + 1 > 6000:
            break
        lines.append(line)
        used_chars += len(line) + 1
    return "\n".join(lines)


# HOTFIX 03: CATALOG HIỂN THỊ CHO PUBLIC WRITER
# Registry là kho chứng cứ typed toàn lượt, còn catalog là phần writer được PHÉP
# viện dẫn. Hai khái niệm không đồng nhất: unknown/legacy vẫn có trong registry
# để verifier phát hiện vấn đề, nhưng không phải bằng chứng hợp lệ cho writer.
_WRITER_KIND_DOMAINS = {
    "runtime_state": frozenset({"self_model"}),
    "user_statement": frozenset({"user_statement", "conversation_history"}),
    "assistant_utterance": frozenset({"conversation_history"}),
    "memory": frozenset({"memory", "user_profile", "shared_episode"}),
    "current_screen": frozenset({"grounded_environment"}),
    "live_vision": frozenset({"grounded_environment"}),
    "pasted_code": frozenset({"discovery_artifact"}),
    "uploaded_file": frozenset({"discovery_artifact"}),
    "web": frozenset({"discovery_artifact"}),
    "capability_result": frozenset({"discovery_artifact", "self_capability"}),
}
_WRITER_FREE_DOMAINS = frozenset({"opinion", "hypothetical", "internal_state", "self_preference"})


def _writer_domains(entry: EvidenceEntry) -> frozenset[str]:
    """Xác định DOMAIN mà nguồn có thể phục vụ; KHÔNG tự xác nhận nghĩa claim.

    Một câu assistant, fiction hay summary không chứng minh một sự kiện ngoài đời.
    Verifier vẫn phải kiểm entailment trên từng claim ngay cả khi domain/ref đúng.
    """
    if entry.status != "supported":
        return frozenset()
    if entry.scope == "fiction":
        return frozenset({"hypothetical"})
    domains = _WRITER_KIND_DOMAINS.get(entry.source_kind, frozenset())
    if entry.source_kind == "memory":
        if entry.scope in {"summary", "unresolved"}:
            return frozenset()
        if entry.scope == "utterance":
            # Ký ức về phát ngôn chỉ chứng minh ai đó ĐÃ NÓI câu này.
            return frozenset({"conversation_history"})
    return domains


@dataclass(frozen=True, slots=True)
class WriterEvidencePolicy:
    """Snapshot nguồn hiển thị và quyền viện dẫn đúng cùng một lượt.

    `refs_by_domain` được runtime cấp, không suy luận từ manifest model trả về.
    Catalog chỉ có nguồn hiện rõ trong prompt; registry còn đầy đủ cho verifier.
    """
    rendered: str
    refs_by_domain: Mapping[str, tuple[str, ...]]
    allowed_domains: frozenset[str]
    allowed_refs: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "refs_by_domain", MappingProxyType({
            str(domain): tuple(refs) for domain, refs in self.refs_by_domain.items()
        }))


def build_writer_evidence_policy(registry: EvidenceRegistry, *, max_chars: int = 6000) -> WriterEvidencePolicy:
    """Render nguyên ref trước, rồi mới cho nó vào allowlist.

    Tránh lỗi cũ: registry có ref `unknown`/không render nhưng writer lại
    tưởng có quyền viện dẫn. Không chia cắt một dòng hoặc xuất nội dung private
    của nguồn không được hỗ trợ. max_chars chỉ giới hạn context hiển thị.
    """
    lines: list[str] = []
    selected: dict[str, list[str]] = {}
    used = 0
    for entry in registry.entries:
        domains = _writer_domains(entry)
        if not domains:
            continue
        body = entry.content
        if isinstance(body, Mapping):
            body = body.get("text", body.get("status", ""))
        # Ghi rõ phạm vi: user_statement = user ĐÃ NÓI, không phải sự kiện xảy ra.
        line = (f"ref={entry.ref} kind={entry.source_kind} scope={entry.scope} "
                f"domains={','.join(sorted(domains))} text={str(body or '')[:240]}")
        if used + len(line) + 1 > max_chars:
            continue
        lines.append(line)
        used += len(line) + 1
        for domain in domains:
            selected.setdefault(domain, []).append(entry.ref)
    # Giữ một dấu hiệu về nguồn bị loại để đối chiếu (không đưa ref bị loại).
    # HOTFIX 03: test legacy vẫn có thể thấy `status=unknown`, nhưng model
    # KHÔNG thể copy một ref unknown vào claim_manifest rồi tự nghĩ là valid.
    excluded = {}
    for entry in registry.entries:
        if entry.status != "supported":
            key = (entry.source_kind, entry.status)
            excluded[key] = excluded.get(key, 0) + 1
    if excluded:
        notice = "NON_CITABLE_SOURCES (NO ref IDs): " + "; ".join(
            f"kind={kind} status={status} count={count}"
            for (kind, status), count in sorted(excluded.items())
        )
        if used + len(notice) + 1 <= max_chars:
            lines.append(notice)
    return WriterEvidencePolicy(
        rendered="\n".join(lines),
        refs_by_domain={domain: tuple(refs) for domain, refs in selected.items()},
        allowed_domains=_WRITER_FREE_DOMAINS | frozenset(selected),
        allowed_refs=frozenset(ref for refs in selected.values() for ref in refs),
    )


__all__ = ["EvidenceEntry", "EvidenceRegistry", "build_evidence_registry",
           "extend_evidence_registry", "render_evidence_context",
           "WriterEvidencePolicy", "build_writer_evidence_policy"]

# -*- coding: utf-8 -*-
"""Context cơ học dành cho RootAgent V12.

Module này cố ý không chứa social state, agenda hay conversation plan. Nó chỉ
cung cấp hai primitive runtime mà root agent được phép dùng trong lúc suy nghĩ:

- ``ConversationNoteTools``: ghi/xóa plain-text working note do agent sở hữu;
- ``RootCapabilityRegistry``: registry các capability domain. Registry không tự
  chọn capability và không materialize public speech.

Mọi quyết định *có gọi capability hay không* vẫn thuộc RootAgent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping


def normalize_note(note: str) -> str:
    """Giữ line-break do agent viết; chỉ cắt runaway payload cơ học."""

    return str(note or "").strip()[:12000]


@dataclass(frozen=True, slots=True)
class ConversationNoteTools:
    """Hai thao tác note tối thiểu mà RootAgent có thể gọi theo nhu cầu.

    Runtime không parse nội dung note, không merge semantic và không dùng note
    để tự tạo continuation. ``set_note``/``clear_note`` chỉ ghi state bền vững.
    """

    set_note: Callable[[str], None]
    clear_note: Callable[[], None]

    def set(self, text: str) -> None:
        self.set_note(normalize_note(text))

    def clear(self) -> None:
        self.clear_note()


@dataclass(frozen=True, slots=True)
class RootMemoryRecallTools:
    """Cổng đọc ký ức theo yêu cầu *của Root*, không tự chọn/chèn lời thoại.

    Callback luôn trả kết quả public-safe từ MemoryKernel; không cho model gọi
    trực tiếp filesystem hoặc sửa ký ức. Công cụ này không ghi memory mới.
    """

    recall: Callable[[str], Mapping[str, Any]]

    def query(self, text: str) -> Mapping[str, Any]:
        # Tránh truy vấn rỗng hoặc input dài bất thường từ model.
        query = " ".join(str(text or "").strip().split())[:600]
        if not query:
            return {"status": "invalid", "reason": "empty_memory_query"}
        return self.recall(query)


def memory_recall_tools_for_host(host: Any, *, session_id: str) -> RootMemoryRecallTools:
    """Dùng MemoryKernel hiện hữu; không tạo thêm kho nhớ hoặc semantic planner.

    ``build_public_context`` vốn ghép cả đoạn văn và record provenance trong
    cùng một lần selection. Ta chỉ trao Root đoạn văn + cờ degraded; không tự
    biến một ký ức không rõ nguồn thành fact chắc chắn.
    """
    sid = str(session_id or "foreground:primary")[:180]

    def recall(query: str) -> Mapping[str, Any]:
        kernel = getattr(host, "memory_kernel", None)
        build = getattr(kernel, "build_public_context", None)
        if not callable(build):
            return {"status": "unavailable", "reason": "memory_kernel_unavailable"}
        try:
            packet = build(query, session_id=sid, user_context_token_budget=1500, max_lines=24)
        except Exception as exc:  # noqa: BLE001
            # Không nhét traceback hoặc nội dung exception có thể chứa dữ liệu
            # cá nhân vào context trả cho LLM.
            return {"status": "failed", "reason": type(exc).__name__}
        text = str(getattr(packet, "text", "") or "").strip()[:12000]
        return {
            "status": "found" if text else "empty",
            "text": text,
            "degraded": bool(getattr(packet, "degraded", True)),
            "source_count": len(tuple(getattr(packet, "records", ()) or ())),
        }

    return RootMemoryRecallTools(recall=recall)


@dataclass(frozen=True, slots=True)
class CapabilityResultReceipt:
    """Kết quả executor theo một invocation; Root/model không được tự ký receipt.

    ``result`` là snapshot readonly, không giữ mapping mutable trả về từ tool.
    """
    invocation_id: str
    name: str
    event_id: str
    session_id: str
    generation: int
    run_id: str
    status: str
    result: Mapping[str, Any]
    source_refs: tuple[str, ...] = ()


@dataclass(slots=True)
class RootCapabilityRegistry:
    """Registry data/runtime-only cho capability mà RootAgent có thể gọi.

    Registry chỉ là dependency container. Nó không route dựa keyword, không tự
    gọi tutor ở mọi turn và không quyết định public response.
    """

    capabilities: Mapping[str, Any]
    invocation_log: list[tuple[str, str]] = field(default_factory=list)
    result_log: list[CapabilityResultReceipt] = field(default_factory=list)

    def get(self, name: str) -> Any | None:
        return self.capabilities.get(str(name or "").strip().lower())

    def available_names(self) -> tuple[str, ...]:
        return tuple(sorted(str(name) for name, value in self.capabilities.items() if value is not None))

    def record_invocation(self, name: str, event_id: str) -> None:
        self.invocation_log.append((str(event_id or "")[:240], str(name or "").strip().lower()[:120]))
        if len(self.invocation_log) > 64:
            del self.invocation_log[:-64]

    def invocations_for(self, event_id: str) -> tuple[str, ...]:
        eid = str(event_id or "")[:240]
        return tuple(name for item_event, name in self.invocation_log if item_event == eid and name)

    def record_result(self, *, name: str, invocation_id: str, event: Any,
                      run_id: str, result: Mapping[str, Any]) -> CapabilityResultReceipt:
        """Chỉ gọi từ adapter sau khi executor trả về; started != completed.

        Không cho phép cùng invocation_id bị ghi đè bằng một kết quả khác.
        Event/session/run/generation đều có mặt để ngăn tái sử dụng ở lượt sau.
        """
        from .claim_evidence import _freeze

        if not invocation_id or not run_id or not getattr(event, "event_id", ""):
            raise ValueError("capability_result_missing_identity")
        for existing in self.result_log:
            if existing.invocation_id == invocation_id:
                return existing
        status = str(result.get("status", "unknown") or "unknown").lower()
        refs = result.get("source_refs", ())
        refs = refs if isinstance(refs, (tuple, list)) else ()
        receipt = CapabilityResultReceipt(
            invocation_id=str(invocation_id), name=str(name).strip().lower(),
            event_id=str(event.event_id), session_id=str(event.session_id),
            generation=int(event.generation), run_id=str(run_id), status=status,
            result=_freeze(result),
            source_refs=tuple(str(ref) for ref in refs if isinstance(ref, str) and ref),
        )
        self.result_log.append(receipt)
        if len(self.result_log) > 64:
            del self.result_log[:-64]
        return receipt

    def result_receipts_for(self, event_id: str, *, run_id: str,
                            generation: int) -> tuple[CapabilityResultReceipt, ...]:
        return tuple(r for r in self.result_log if r.event_id == event_id and
                     r.run_id == run_id and r.generation == generation)


__all__ = [
    "ConversationNoteTools",
    "RootMemoryRecallTools",
    "memory_recall_tools_for_host",
    "RootCapabilityRegistry",
    "CapabilityResultReceipt",
    "normalize_note",
]

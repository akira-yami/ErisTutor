# -*- coding: utf-8 -*-
"""V6: Bàn làm việc sống cho Eris.

Giọng nói và tài liệu là hai luồng độc lập. Các thao tác tài liệu có revision,
định danh và ACK, để nhánh trình bày cùng Writer có thể chỉnh trong lúc TTS chạy.
Module này KHÔNG quyết định nội dung/thái độ của Eris hay gọi TTS.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
import re
import socket
import threading
from typing import Any, Callable, Mapping

from .structured_output import parse_markdown, text_digest, _render_math
from pathlib import Path

_MAX_PACKET = 42_000
_MAX_BLOCKS = 80
_MAX_TEXT = 24_000
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ERIS-LiveWorkspace")


class RevisionConflict(ValueError):
    """Có một tác vụ đã sửa tài liệu sau revision mà người gọi nhìn thấy."""


def _bounded_block(value: Mapping[str, Any]) -> dict[str, Any]:
    """Không cho nhánh trình bày tạo payload khổng lồ hoặc field lạ."""
    if not isinstance(value, Mapping):
        raise ValueError("Khối tài liệu phải là object")
    kind = str(value.get("type", ""))
    if kind not in {"paragraph", "heading", "formula", "code", "table", "list", "quote", "image"}:
        raise ValueError("Loại khối tài liệu không được hỗ trợ")
    raw = {str(k): v for k, v in value.items()
           if k in {"type", "text", "latex", "language", "level", "items", "headers", "rows", "path", "sha256"}}
    if len(json.dumps(raw, ensure_ascii=False)) > 11_000:
        raise ValueError("Khối tài liệu quá lớn")
    if kind in {"paragraph", "heading", "code", "quote"} and not isinstance(raw.get("text", ""), str):
        raise ValueError("Văn bản không hợp lệ")
    if kind == "formula" and not isinstance(raw.get("latex", ""), str):
        raise ValueError("LaTeX không hợp lệ")
    return raw


@dataclass
class LiveDocument:
    """Một tài liệu duy nhất, chỉ sửa qua thao tác có revision."""
    session_id: str
    generation: int
    run_id: str
    delivery_id: str
    turn_id: str
    source_text: str
    reference_text: str = ""  # Câu Bubble dùng để ghép hash; không dùng làm tài liệu.
    revision: int = 0
    blocks: dict[str, dict] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    finished: bool = False
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    writer_touched: set[str] = field(default_factory=set, repr=False)

    @property
    def document_id(self) -> str:
        raw = f"{self.session_id}:{self.generation}:{self.run_id}:{self.delivery_id}"
        return sha256(raw.encode()).hexdigest()[:32]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"document_id": self.document_id, "revision": self.revision,
                    "blocks": [{"block_id": k, **self.blocks[k]} for k in self.order],
                    "finished": self.finished}

    def operate(self, operation: str, *, block_id: str = "", block: Mapping[str, Any] | None = None,
                after_id: str = "", base_revision: int | None = None, owner: str = "presenter") -> dict:
        """Sửa state và cấp số phiên bản trong cùng một critical section."""
        if operation not in {"begin", "insert_block", "replace_block", "remove_block", "finalize"}:
            raise ValueError("Thao tác không hỗ trợ")
        if owner not in {"presenter", "writer", "tool"}:
            raise ValueError("Nguồn sửa tài liệu không hợp lệ")
        with self._lock:
            if base_revision is not None and base_revision != self.revision:
                raise RevisionConflict(f"revision cũ {base_revision}; hiện tại {self.revision}")
            if not self.revision and operation != "begin":
                raise ValueError("Phải bắt đầu tài liệu trước")
            if operation == "begin" and self.revision:
                raise ValueError("Tài liệu đã bắt đầu")
            if operation in {"insert_block", "replace_block", "remove_block"}:
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", block_id):
                    raise ValueError("block_id không hợp lệ")
            normalized = None
            if operation in {"insert_block", "replace_block"}:
                normalized = _bounded_block(block or {})
            if operation == "insert_block":
                if block_id in self.blocks or len(self.order) >= _MAX_BLOCKS:
                    raise ValueError("Khối bị trùng hoặc vượt giới hạn")
                if after_id and after_id not in self.blocks:
                    raise ValueError("Khối đứng trước không tồn tại")
                index = self.order.index(after_id) + 1 if after_id else len(self.order)
                self.order.insert(index, block_id)
                self.blocks[block_id] = normalized
                if owner == "writer":
                    self.writer_touched.add(block_id)
            elif operation == "replace_block":
                if owner == "presenter" and block_id in self.writer_touched:
                    raise RevisionConflict("Writer đã sửa khối này; Presenter không được ghi đè")
                if block_id not in self.blocks:
                    raise ValueError("Không tìm thấy khối cần sửa")
                self.blocks[block_id] = normalized
                if owner == "writer":
                    self.writer_touched.add(block_id)
            elif operation == "remove_block":
                if block_id not in self.blocks:
                    raise ValueError("Không tìm thấy khối cần xóa")
                self.blocks.pop(block_id)
                self.order.remove(block_id)
                self.writer_touched.discard(block_id)
            elif operation == "finalize":
                self.finished = True
            previous = self.revision
            self.revision += 1
            event: dict[str, Any] = {
                "parse_mode": "workspace_live_op", "workspace_version": 2,
                "document_id": self.document_id, "turn_id": self.turn_id[:150],
                "session_id": self.session_id[:150], "generation": self.generation,
                "text_hash": text_digest(self.reference_text or self.source_text),
                "revision": self.revision, "previous_revision": previous,
                "operation": operation, "owner": owner,
                "block_id": block_id,
            }
            if normalized is not None:
                event["block"] = normalized
            if after_id:
                event["after_id"] = after_id
            event["event_id"] = sha256(f"{self.document_id}:{self.revision}".encode()).hexdigest()[:32]
            if len(json.dumps(event, ensure_ascii=False).encode("utf-8")) > _MAX_PACKET:
                # Khối đã được giới hạn; kiểm tra này bảo vệ cả envelope.
                raise ValueError("Gói sửa tài liệu quá lớn")
            return event


def send_live_operation(event: Mapping[str, Any], *, host: str = "127.0.0.1", port: int = 4242,
                        timeout: float = 0.3, retries: int = 3) -> bool:
    """Gửi tuần tự; ACK chỉ nói 'đã áp dụng state', không nhận là đã hiện trên màn hình."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("V6 chỉ gửi trực tiếp đến Godot cục bộ")
    raw = json.dumps(dict(event), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > _MAX_PACKET:
        raise ValueError("Datagram quá lớn")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        for _ in range(max(1, min(4, retries))):
            sock.sendto(raw, (host, port))
            try:
                reply, _ = sock.recvfrom(2048)
                ack = json.loads(reply)
                if (ack.get("parse_mode") == "workspace_live_ack"
                    and ack.get("document_id") == event["document_id"]
                    and ack.get("revision") == event["revision"]):
                    return ack.get("status") in {"applied", "duplicate"}
            except (socket.timeout, ValueError, OSError):
                continue
    return False


_PRESENTATION_SCHEMA = {
    "type": "object", "properties": {
        "blocks": {"type": "array", "maxItems": 40, "items": {
            "type": "object", "properties": {
                "type": {"type": "string"}, "text": {"type": "string"},
                "latex": {"type": "string"}, "level": {"type": "integer"},
                "language": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}},
            }, "required": ["type"], "additionalProperties": True,
        }}}, "required": ["blocks"], "additionalProperties": False,
}


def compose_with_model(router: Any, text: str) -> list[dict] | None:
    """Nhánh AI trình bày độc lập, không thay Root và không phát ngôn.

    Thử nếu có router và model; thất bại thì giữ nguyên các khối gốc.
    """
    if router is None or os.getenv("ERIS_V6_PRESENTATION_AGENT", "0") == "0":
        return None
    client = (getattr(router, "deep_client", None)
              or getattr(router, "surface_client", None)
              or getattr(getattr(router, "llm_service", None), "client", None))
    call = getattr(router, "invoke_structured_worker", None)
    if client is None or not callable(call):
        return None
    result = call(
        client,
        system=("Bạn là nhánh DÀN TRANG của ERIS, không phải Root hay người trả lời mới. "
                "Chuyển nguyên văn nội dung được cung cấp thành các block heading, paragraph, formula, "
                "code, list, quote. Chỉ đổi cấu trúc, KHÔNG thêm dữ kiện, phép tính, đáp số, "
                "phát biểu hay cam kết mới. Giữ nguyên ngôn ngữ. Trả JSON blocks. "
                "Nếu nội dung là một câu ngắn thì giữ một paragraph."),
        payload={"nguon_da_duoc_root_quyet_dinh": text[:12_000]},
        schema=_PRESENTATION_SCHEMA, name="eris_v6_presentation_agent", max_tokens=2200,
        inference_tier="subconscious_4b", priority=30, timeout_seconds=25.0,
        cognitive_lane="workspace_presentation", temperature=0,
    )
    raw = result.get("blocks") if isinstance(result, dict) else None
    if not isinstance(raw, list) or not raw or len(raw) > 40:
        return None
    try:
        blocks = [_bounded_block(x) for x in raw]
    except (ValueError, TypeError):
        return None
    # Nhánh AI biên tập chỉ được thêm bố cục, không có quyền quyết định nội dung.
    # Không thể chứng minh tương đương ngữ nghĩa bằng schema: có provenance và
    # fallback để không coi đầu ra mới là sự thật được Root xác nhận.
    return blocks


class LiveWorkspaceService:
    """Điều phối thao tác theo tài liệu, không phải coordinator nhận thức."""
    def __init__(self, *, sender: Callable[..., bool] = send_live_operation,
                 is_current: Callable[[], bool] | None = None,
                 workspace: str | Path | None = None):
        self.sender = sender
        self.is_current = is_current or (lambda: True)
        self.workspace = Path(workspace or os.environ.get("ERIS_ARTIFACT_WORKSPACE", "logs/eris_artifacts"))
        self._documents: dict[str, LiveDocument] = {}
        self._lock = threading.RLock()
        self._send_lock = threading.RLock()

    def _send(self, doc: LiveDocument, event: dict, *, host: str, port: int) -> bool:
        if not self.is_current():
            return False
        return self.sender(event, host=host, port=port)

    def open(self, *, text: str, session_id: str, generation: int, run_id: str,
             delivery_id: str, turn_id: str, host: str = "127.0.0.1", port: int = 4242,
             bubble_text: str = "") -> LiveDocument | None:
        if not text.strip() or len(text) > _MAX_TEXT or not self.is_current():
            return None
        doc = LiveDocument(session_id, generation, run_id, delivery_id, turn_id, text,
                           reference_text=bubble_text)
        with self._lock:
            self._documents[doc.document_id] = doc
            if len(self._documents) > 50:
                self._documents.pop(next(iter(self._documents)))
        with self._send_lock:
            begin = doc.operate("begin")
            if not self._send(doc, begin, host=host, port=port):
                return None
        register_live_document(self, doc, host=host, port=port)
        return doc

    def revise(self, doc: LiveDocument, *, operation: str, host: str = "127.0.0.1",
               port: int = 4242, **kwargs: Any) -> bool:
        if not self.is_current():
            return False
        with self._send_lock:
            event = doc.operate(operation, **kwargs)
            return self._send(doc, event, host=host, port=port)

    def present(self, doc: LiveDocument, *, router: Any = None,
                host: str = "127.0.0.1", port: int = 4242) -> bool:
        """Hiện thô trước, AI có thể chỉnh ngay trên những khối đã được hiện."""
        try:
            blocks = parse_markdown(doc.source_text)
            for index, block in enumerate(blocks):
                if not self.is_current():
                    return False
                block = dict(block)
                if block.get("type") == "formula":
                    # Mỗi block có PNG riêng; dùng cùng document_id sẽ ghi đè math_0.png
                    # và khiến các block trước không còn khớp SHA-256 khi Godot đọc.
                    try:
                        _render_math([block], self.workspace, f"{doc.document_id}_b{index:03d}")
                    except (ImportError, OSError, ValueError):
                        pass
                if not self.revise(doc, operation="insert_block", block_id=f"b{index:03d}", block=block,
                                   owner="presenter", host=host, port=port):
                    return False
            # LLM bố cục chạy ở worker; không tác động TTS và không quyết nội dung Root.
            improved = compose_with_model(router, doc.source_text)
            if improved and self.is_current():
                for index, block in enumerate(improved):
                    if not self.is_current():
                        return False
                    item = dict(block)
                    if item.get("type") == "formula":
                        try:
                            _render_math([item], self.workspace, f"{doc.document_id}_b{index:03d}")
                        except (ImportError, OSError, ValueError):
                            pass
                    if index < len(doc.order):
                        op, key = "replace_block", doc.order[index]
                        if key in doc.writer_touched:
                            continue
                    else:
                        op, key = "insert_block", f"b{index:03d}"
                    if not self.revise(doc, operation=op, block_id=key, block=item,
                                       owner="presenter", host=host, port=port):
                                           return False
                while len(doc.order) > len(improved):
                    if not self.revise(doc, operation="remove_block", block_id=doc.order[-1],
                                       owner="presenter", host=host, port=port):
                                           return False
            if router is not None and self.is_current():
                edits = review_with_writer(router, doc.source_text, doc.snapshot())
                for item in edits:
                    if not self.is_current():
                        return False
                    revision = doc.revision
                    block = item['block']
                    if block.get('type') == 'formula':
                        try:
                            _render_math([block], self.workspace, f"{doc.document_id}_{item['block_id']}_writer")
                        except (ImportError, OSError, ValueError):
                            pass
                    try:
                        if not self.revise(doc, operation='replace_block', block_id=item['block_id'],
                                           block=block, base_revision=revision, owner='writer',
                                           host=host, port=port):
                                               return False
                    except RevisionConflict:
                        # Có chỉnh sửa đồng thời: giữ bản mới, không ghi đè mù.
                        continue
            if not self.is_current():
                return False
            return self.revise(doc, operation="finalize", host=host, port=port)
        except Exception:
            # Mọi lỗi của nhánh trình bày đều không làm hỏng cuộc thoại/TTS.
            return False


def dispatch_live_document(*, text: str, session_id: str, generation: int,
                           run_id: str, delivery_id: str, turn_id: str,
                           transport: Any = None, router: Any = None,
                           is_current: Callable[[], bool] | None = None,
                           logger: Any = None, bubble_text: str = "") -> LiveWorkspaceService | None:
    """Bắt đầu sau public text release, đồng thời với giọng nói đang phát."""
    if not text.strip() or len(text) > _MAX_TEXT:
        return None
    target = getattr(transport, "_send_target", ("127.0.0.1", 4242))
    host, port = target if isinstance(target, (tuple, list)) and len(target) == 2 else ("127.0.0.1", 4242)
    service = LiveWorkspaceService(sender=send_live_operation, is_current=is_current)

    def work() -> None:
        try:
            doc = service.open(text=text, session_id=session_id, generation=generation,
                               run_id=run_id, delivery_id=delivery_id,
                               turn_id=turn_id, host=host, port=port, bubble_text=bubble_text)
            if doc is not None:
                service.present(doc, router=router, host=host, port=port)
        except Exception as exc:
            if logger is not None:
                logger.debug("[workspace-v6] không phát được bản cập nhật: %s", exc)
    _EXECUTOR.submit(work)
    return service

# Bộ tra cứu tài liệu đang chỉnh để Writer/Root có thể gửi lệnh sửa qua tool.
# Đây là index kỹ thuật có giới hạn, KHÔNG phải nguồn quyết định nội dung mới.
_ACTIVE_DOCUMENTS: dict[str, tuple[LiveWorkspaceService, LiveDocument, str, int]] = {}
_ACTIVE_LOCK = threading.RLock()


def register_live_document(service: LiveWorkspaceService, document: LiveDocument,
                           *, host: str = '127.0.0.1', port: int = 4242) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_DOCUMENTS[document.document_id] = (service, document, host, port)
        while len(_ACTIVE_DOCUMENTS) > 48:
            _ACTIVE_DOCUMENTS.pop(next(iter(_ACTIVE_DOCUMENTS)))


def inspect_live_document(document_id: str) -> dict[str, Any] | None:
    """Cho Writer đọc revision và block_id trước khi sửa."""
    with _ACTIVE_LOCK:
        target = _ACTIVE_DOCUMENTS.get(document_id)
    if target is None:
        return None
    return target[1].snapshot()


def writer_edit_live_document(document_id: str, operation: str, *, block_id: str = '',
                              block: Mapping[str, Any] | None = None,
                              base_revision: int | None = None) -> dict[str, Any]:
    """Cổng thao tác thật của Writer; tuân theo generation và revision."""
    with _ACTIVE_LOCK:
        target = _ACTIVE_DOCUMENTS.get(document_id)
    if target is None:
        return {'ok': False, 'reason': 'document_not_found'}
    service, doc, host, port = target
    if operation == 'inspect':
        return {'ok': True, 'state': doc.snapshot()}
    try:
        ok = service.revise(doc, operation=operation, block_id=block_id, block=block,
                            base_revision=base_revision, owner='writer',host=host,port=port)
        return {'ok': bool(ok), 'revision': doc.revision,
                'reason': 'applied' if ok else 'preempted_or_no_ack'}
    except (RevisionConflict, ValueError) as exc:
        return {'ok': False, 'revision': doc.revision, 'reason': str(exc)[:160]}


def review_with_writer(router: Any, source: str, snapshot: Mapping[str, Any]) -> list[dict]:
    """Lượt sửa tài liệu của Writer, sau nhánh trình bày; không phát thoại mới.

    Đây là một call phụ có thể tắt bằng cấu hình. Chỉ nhận chỉnh sửa khối có sẵn.
    """
    if router is None or os.getenv('ERIS_V6_WRITER_REVIEW','0') == '0':
        return []
    client = (getattr(router, 'surface_client', None)
              or getattr(router, 'deep_client', None)
              or getattr(getattr(router, 'llm_service', None), 'client', None))
    call = getattr(router, 'invoke_structured_worker', None)
    if client is None or not callable(call):
        return []
    schema = {
        'type': 'object', 'properties': {
            'edits': {'type': 'array', 'maxItems': 12, 'items': {
                'type': 'object', 'properties': {
                    'block_id': {'type': 'string'}, 'type': {'type': 'string'},
                    'text': {'type': 'string'}, 'latex': {'type': 'string'},
                    'level': {'type': 'integer'}, 'language': {'type': 'string'},
                }, 'required': ['block_id','type'], 'additionalProperties': False,
            }}}, 'required': ['edits'], 'additionalProperties': False,
    }
    result = call(
        client,
        system=('Bạn là Writer đang duyệt lại bố cục hiển thị của chính câu mình vừa nói. '
                'Chỉ sửa khối nếu cần tránh lỗi hiển thị hoặc sai ký hiệu so với NGUỒN GỐC. '
                'Không tự thay đáp án, không thêm luận điểm, không tạo ý định hoặc lời thoại mới. '
                'Trả JSON edits theo block_id có sẵn, không cần sửa thì edits rỗng.'),
        payload={'cau_goc_da_commit': source[:10000],
                 'tai_lieu_dang_hien': list(snapshot.get('blocks', []))[:45]},
        schema=schema, name='eris_v6_writer_workspace_revision', max_tokens=1200,
        inference_tier='subconscious_4b', priority=35, timeout_seconds=20.,
        cognitive_lane='workspace_writer_revision', temperature=0,
    )
    edits = result.get('edits') if isinstance(result, dict) else None
    if not isinstance(edits, list) or len(edits)>12:
        return []
    clean=[]
    valid_ids={b.get('block_id') for b in snapshot.get('blocks', [])}
    for entry in edits:
        if not isinstance(entry, Mapping) or entry.get('block_id') not in valid_ids:
            continue
        block={k:v for k,v in entry.items() if k!='block_id'}
        try:
            block=_bounded_block(block)
        except (ValueError, TypeError):
            continue
        clean.append({'block_id':entry['block_id'], 'block':block})
    return clean

# -*- coding: utf-8 -*-
"""Nhật ký HTTP nguyên văn của các worker DeepSeek (chỉ dùng khi chủ động bật).

Mục tiêu: lần theo *request thực sự gửi tới gateway* và *response thô* trước
khi JSONL worker sửa/validate. Không ghi Authorization, cookie hay các header
chứa credential; nội dung prompt/memory thì giữ NGUYÊN VĂN để điều tra.

Bật bằng ERIS_DEEPSEEK_RAW_TRACE=1. Cẩn thận: file chứa dữ liệu cá nhân,
không commit, không gửi public, xóa sau khi chẩn đoán. Không có cắt bớt body.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from llm.raw_trace_store import store_payload
from requests.models import complexjson as requests_json


_ROLE: contextvars.ContextVar[str] = contextvars.ContextVar("eris_raw_trace_worker_role", default="")
_WRITE_LOCK = threading.Lock()
_RUN_TAG = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
_FAILED_WARNING_EMITTED = False


def enabled() -> bool:
    """Mặc định TẮT: đây là log nhạy cảm, không phải telemetry thường."""
    return str(os.getenv("ERIS_DEEPSEEK_RAW_TRACE", "0")).strip().lower() in {"1", "true", "yes", "on"}


@contextlib.contextmanager
def worker_role_context(role: str) -> Iterator[None]:
    """Gắn tên worker vào request mà không sửa/mutate client dùng chung."""
    token = _ROLE.set(str(role or "").strip().lower())
    try:
        yield
    finally:
        _ROLE.reset(token)


def active_worker_role() -> str:
    """Đọc role ở caller trước khi broker có thể chuyển execution context."""
    return _ROLE.get() or "unclassified"


def _safe_endpoint(url: str) -> str:
    # Không lưu username/password trong URL hay query token nếu có.
    try:
        parsed = urlsplit(str(url or ""))
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urlunsplit((parsed.scheme, host + port, parsed.path, "", ""))
    except (ValueError, TypeError):
        return "<endpoint_hidden>"


def _hashed_session(headers: Any) -> str:
    """Có thể so session cũ/mới nhưng không ghi session id gốc."""
    if not isinstance(headers, dict):
        return ""
    session_id = next((str(v) for k, v in headers.items() if str(k).lower() == "x-session-id"), "")
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:20] if session_id else ""


def _write(event: dict[str, Any], *, payload: bytes | None = None, payload_key: str = "",
           payload_kind: str = "") -> None:
    """Nhật ký là best-effort; lỗi đĩa tuyệt đối không được làm hỏng hội thoại."""
    global _FAILED_WARNING_EMITTED
    try:
        directory = Path(os.getenv("ERIS_DEEPSEEK_RAW_TRACE_DIR", "") or "logs/deepseek_raw").expanduser()
        with _WRITE_LOCK:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Ghi và công bố object trước index. Crash ở giữa chỉ để lại object mồ côi,
            # chứ không bao giờ tạo event trỏ tới object chưa tồn tại.
            if payload is not None:
                event[payload_key] = store_payload(directory, payload, kind=payload_kind)
            serialized = (json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode('utf-8')
            filename = directory / f"events_{_RUN_TAG}_pid{os.getpid()}.jsonl"
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            # Trên POSIX, file 0600. Windows dùng ACL của thư mục/người dùng.
            fd = os.open(str(filename), flags, 0o600)
            try:
                with os.fdopen(fd, "ab") as stream:
                    stream.write(serialized)
            except BaseException:
                # fdopen có thể lỗi trước khi sở hữu fd.
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
    except Exception as exc:  # noqa: BLE001
        if not _FAILED_WARNING_EMITTED:
            _FAILED_WARNING_EMITTED = True
            # Chỉ loại lỗi, không in path/prompt/API-key ra log thường.
            import logging
            logging.getLogger("LLM").warning("[deepseek raw trace] write failed: %s", type(exc).__name__)


class RawHttpAttempt:
    """Một lần gọi requests.post: bắt đủ request, response và lỗi transport."""

    def __init__(self, *, request_id: str, timestamp: str, started: float, metadata: dict[str, Any]):
        self.request_id = request_id
        self.timestamp = timestamp
        self.started = started
        self.metadata = metadata

    def response(self, response: Any) -> None:
        try:
            raw_text = response.text  # Đọc nguyên văn body, trước JSONL canonicalization.
        except Exception as exc:  # noqa: BLE001
            raw_text = f"<response_text_read_failed:{type(exc).__name__}>"
        # response.content là byte sau khi requests xử lý HTTP transfer encoding;
        # response.text chỉ là bản giải mã để đọc. Ưu tiên byte để export chính xác.
        actual_bytes = getattr(response, "content", None)
        if not isinstance(actual_bytes, bytes):
            actual_bytes = raw_text.encode('utf-8')
        _write({
            "event": "response", "request_id": self.request_id,
            "at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": round((time.monotonic() - self.started) * 1000, 1),
            "http_status": getattr(response, "status_code", None),
            "response_chars": len(raw_text),
            "response_encoding": str(getattr(response, "encoding", "") or "utf-8"),
            "worker_role": self.metadata["worker_role"],
            "lane": self.metadata["lane"],
            "cognitive_lane": self.metadata["cognitive_lane"],
        }, payload=actual_bytes, payload_key="raw_response_ref", payload_kind="http_body_bytes")

    def error(self, error: BaseException) -> None:
        _write({
            "event": "transport_error", "request_id": self.request_id,
            "at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": round((time.monotonic() - self.started) * 1000, 1),
            "exception_type": type(error).__name__,
            "worker_role": self.metadata["worker_role"],
            "lane": self.metadata["lane"],
            "cognitive_lane": self.metadata["cognitive_lane"],
            # Không ghi str(error): requests có thể chứa URL kèm query/token.
        })


def begin_http_attempt(*, url: str, model: str, body: Any, headers: Any, lane: str,
                       cognitive_lane: str = "", router_reason: str = "",
                       worker_role: str = "") -> RawHttpAttempt | None:
    """Gọi ngay trước requests.post, sau mọi bước preflight/broker/trim.

    ``body`` là Python object được truyền nguyên vào requests.post(json=...),
    không phải prompt nháp trước khi worker hoặc broker sửa. API key/cookie
    chỉ nằm ở headers nên cố ý không lưu headers.
    """
    if not enabled():
        return None
    request_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    meta = {
        "worker_role": str(worker_role or active_worker_role()),
        "lane": str(lane or "transport"),
        "cognitive_lane": str(cognitive_lane or ""),
        "router_reason": str(router_reason or ""),
        "model": str(model or ""),
        "endpoint": _safe_endpoint(url),
        "session_hash": _hashed_session(headers),
    }
    attempt = RawHttpAttempt(request_id=request_id, timestamp=now, started=time.monotonic(), metadata=meta)
    # requests.post(json=body) dùng complexjson.dumps(..., allow_nan=False).
    # Lưu đúng UTF-8 byte được gửi, kể cả escape Unicode và whitespace.
    # Nếu payload không hợp lệ thì transport cũng sẽ báo lỗi như bình thường.
    try:
        payload = requests_json.dumps(body, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError):
        payload = json.dumps(body, ensure_ascii=False, default=str).encode('utf-8')
    _write({"event": "request", "request_id": request_id, "at_utc": now, "origin": "eris_client", **meta,
            "request_chars": len(payload.decode('utf-8'))},
           payload=payload, payload_key="raw_request_ref", payload_kind="json")
    return attempt


def record_observer_diagnostic(*, turn_id: str, turn_seq: int, status: str,
                               source: str, detail: str, max_tokens: int) -> None:
    """Index trạng thái parse/validation để ghép với raw HTTP trace theo lượt.

    Chỉ ghi khi raw-trace đã bật. KHÔNG ghi prompt/response ở đây; chúng đã nằm
    trong object store của HTTP trace. Detail chỉ là mã an toàn từ router.
    """
    if not enabled():
        return
    _write({
        "event": "observer_diagnostic",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "worker_role": "observer",
        "cognitive_lane": "post_observer",
        "turn_id": str(turn_id or "")[:100],
        "turn_seq": int(turn_seq or 0),
        "status": str(status or "")[:40],
        "source": str(source or "")[:80],
        "detail": str(detail or "")[:100],
        "max_tokens": int(max_tokens or 0),
    })

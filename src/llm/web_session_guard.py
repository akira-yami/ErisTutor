"""Chốt điều kiện tái sử dụng phiên DeepSeek Web ở ranh giới transport.

Gateway cũ có thể chỉ chuyển user message cuối khi ``X-Session-ID`` đã tồn tại.
Vì vậy Eris KHÔNG được tái sử dụng một phiên chỉ dựa vào role/lane: hai
request có cùng role vẫn có thể chứa system prompt, Root directive, schema và
lịch sử khác nhau. Chỉ tiếp tục khi request mới đúng bằng transcript đã được
xác nhận thành công cộng thêm MỘT user turn mới.

Đây là lớp phòng vệ phía client; gateway vẫn phải tự kiểm chứng trạng thái và
không được đánh rơi FinalPrompt. Nếu gateway không có logic đó, chế độ
ERIS_WEB_SESSION_MODE=off là cách bắt buộc để bảo toàn ý nghĩa mọi request.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


def _canonical(value: Any) -> str:
    """Snapshot nội dung request, không phụ thuộc thứ tự key của dict."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class SessionLease:
    """Đại diện cho MỘT lần HTTP attempt, không phải một lượt trò chuyện."""

    scope: str
    session_id: str
    messages_fingerprint: str
    request_messages: tuple[dict, ...]
    contract_fingerprint: str


@dataclass
class _SavedSession:
    session_id: str
    # Transcript được xác nhận từ request/response thật, không đoán theo lịch sử.
    transcript_fingerprint: str
    transcript: tuple[dict, ...]
    contract_fingerprint: str
    in_flight: bool = False


class WebSessionGuard:
    """Quản lý phiên trong một process, fail-closed với concurrent/retry.

    Không bao giờ tự tin tưởng session cũ tồn tại trên server sau khi Eris
    restart: phiên mới có nonce, tránh tiếp nối nhầm các request cũ.
    """

    def __init__(self, max_scopes: int = 128) -> None:
        self._lock = threading.Lock()
        self._saved: OrderedDict[str, _SavedSession] = OrderedDict()
        self._max_scopes = max(1, int(max_scopes))

    @staticmethod
    def _fingerprint(value: Any) -> str:
        return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()

    def reserve(
        self,
        *,
        base_session_id: str,
        base_url: str,
        model: str,
        credential: str,
        messages: list[dict],
        request_contract: dict,
    ) -> SessionLease:
        """Chỉ tái sử dụng nếu transcript khớp chính xác và chỉ thêm user turn.

        Mọi thay đổi system/developer message, schema, memory, nhánh hội thoại,
        hay lịch sử bị rút gọn đều buộc mở phiên mới. Không cắt bớt messages.
        """
        request_messages = tuple(dict(message) for message in messages)
        contract_hash = self._fingerprint(request_contract)
        # Bao gồm provider key để khi pool chuyển account không kế thừa remote
        # session của account khác. Chỉ giữ digest, không lưu credential gốc.
        credential_hash = self._fingerprint(credential)
        scope = self._fingerprint([base_url, model, credential_hash, base_session_id])
        request_fp = self._fingerprint(request_messages)

        with self._lock:
            previous = self._saved.get(scope)
            can_continue = bool(
                previous is not None
                and not previous.in_flight
                and previous.contract_fingerprint == contract_hash
                and len(request_messages) == len(previous.transcript) + 1
                and request_messages[:-1] == previous.transcript
                and str(request_messages[-1].get("role", "")).lower() == "user"
            )
            if can_continue:
                session_id = previous.session_id
                previous.in_flight = True
                self._saved.move_to_end(scope)
            else:
                # Tạo generation khác thay vì dùng lại key theo role: nếu gateway
                # còn phiên cũ, không thể âm thầm nuốt Root directive mới.
                session_id = f"{base_session_id[:98]}:g{secrets.token_hex(9)}"
                self._saved[scope] = _SavedSession(
                    session_id=session_id,
                    transcript_fingerprint="",
                    transcript=(),
                    contract_fingerprint=contract_hash,
                    in_flight=True,
                )
                self._saved.move_to_end(scope)
                while len(self._saved) > self._max_scopes:
                    self._saved.popitem(last=False)

        return SessionLease(
            scope=scope,
            session_id=session_id,
            messages_fingerprint=request_fp,
            request_messages=request_messages,
            contract_fingerprint=contract_hash,
        )

    def commit(self, lease: SessionLease, assistant_content: str) -> None:
        """Chỉ sau response thành công có lời thật mới xác nhận remote state."""
        if not isinstance(assistant_content, str) or not assistant_content.strip():
            self.abort(lease)
            return
        with self._lock:
            saved = self._saved.get(lease.scope)
            if saved is None or saved.session_id != lease.session_id:
                return  # request cũ trả về sau request mới, không ghi đè.
            saved.transcript = lease.request_messages + ({"role": "assistant", "content": assistant_content},)
            saved.transcript_fingerprint = self._fingerprint(saved.transcript)
            saved.contract_fingerprint = lease.contract_fingerprint
            saved.in_flight = False
            self._saved.move_to_end(lease.scope)

    def abort(self, lease: SessionLease) -> None:
        """Không nối một phiên khi HTTP lỗi/hủy hoặc không rõ đã commit chưa."""
        with self._lock:
            saved = self._saved.get(lease.scope)
            if saved is not None and saved.session_id == lease.session_id:
                del self._saved[lease.scope]


GLOBAL_WEB_SESSION_GUARD = WebSessionGuard()

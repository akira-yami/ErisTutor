# -*- coding: utf-8 -*-
"""InputRevisionBuffer — gom và cập nhật trạng thái câu nhắn của user theo thời gian thực.

Tình huống chồng lấn:
Khi User nhắn thêm lúc Root đang nghĩ (``_chat_processing=True``), thay vì chặn cứng
và xếp hàng chờ xử lý tuần tự khiến Root trả lời cho câu cũ đã lỗi thời,
``InputRevisionBuffer`` thực hiện:
1. Tăng ``input_revision`` và đánh dấu ``stale`` ngay lập tức cho lượt chạy (run)
   hiện hành của Root (thông qua callback hủy/preempt).
2. Gom (coalesce) các câu mới của user trong một cửa sổ debounce ngắn (không
   restart Root vô tội vạ theo từng ký tự hay từng tin nhắn rời rạc trong một burst).
3. Cho phép vòng xử lý restart Root đúng 1 lần duy nhất trên trạng thái câu nhắn
   mới nhất (canonical latest user state), đảm bảo chỉ lượt chạy của revision mới
   nhất mới có quyền commit đầu ra công khai (latest-wins).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class InputRevisionEntry:
    """Một mảnh tin nhắn được đẩy vào bộ đệm trong cùng chuỗi chỉnh sửa/bổ sung."""

    revision: int
    text: str
    timestamp: float
    is_correction: bool = False


@dataclass(frozen=True, slots=True)
class InputRevisionSnapshot:
    """Ảnh chụp trạng thái câu nhắn đã gộp tại một revision xác định."""

    session_id: str
    revision: int
    canonical_text: str
    entries: tuple[InputRevisionEntry, ...] = ()
    stale_marked_revisions: tuple[int, ...] = ()
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.input_revision_snapshot.v1",
            "session_id": self.session_id,
            "revision": self.revision,
            "canonical_text": self.canonical_text,
            "entry_count": len(self.entries),
            "message_count": len(self.entries),
            "first_message_at": min((e.timestamp for e in self.entries), default=self.updated_at),
            "last_message_at": max((e.timestamp for e in self.entries), default=self.updated_at),
            "wait_seconds": max(0.0, self.updated_at - min((e.timestamp for e in self.entries), default=self.updated_at)),
            "message_timestamps": [e.timestamp for e in self.entries],
            "stale_marked_revisions": list(self.stale_marked_revisions),
            "updated_at": self.updated_at,
        }


_CORRECTION_PREFIXES = (
    "à không",
    "a khong",
    "ý tôi là",
    "y toi la",
    "ý mình là",
    "y minh la",
    "nhầm",
    "nham",
    "sửa lại",
    "sua lai",
    "thôi đổi",
    "thoi doi",
    "không phải",
    "khong phai",
)


def _looks_like_correction(text: str) -> bool:
    """Kiểm tra nhanh xem tin nhắn mới có mang tính đính chính/sửa ý câu trước không."""
    clean = " ".join(str(text or "").strip().lower().split())
    return any(clean.startswith(prefix) or f", {prefix}" in clean for prefix in _CORRECTION_PREFIXES)


class InputRevisionBuffer:
    """Bộ đệm quản lý revision đầu vào khi user nhắn chồng lấn lúc Root đang suy nghĩ.

    Thiết kế tuân thủ nguyên tắc Single Semantic Authority:
    - Không tạo nhiều Root chạy song song tranh quyền phát ngôn.
    - Khi có tin nhắn mới lúc Root chưa commit ra ngoài, lập tức đánh dấu stale
      cho run cũ, gom các tin nhắn qua cửa sổ debounce ngắn, và chỉ restart Root
      đúng 1 lần trên dữ liệu canonical mới nhất.
    """

    def __init__(
        self,
        *,
        debounce_seconds: float = 0.25,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._session_id: str = "default"
        self._current_revision: int = 0
        self._active_run_revision: int = 0
        self._entries: list[InputRevisionEntry] = []
        self._stale_revisions: set[int] = set()
        self._pending_restart: bool = False
        self._last_submit_at: float = 0.0

    @property
    def current_revision(self) -> int:
        """Số thứ tự revision mới nhất của input user."""
        with self._lock:
            return self._current_revision

    @property
    def active_run_revision(self) -> int:
        """Revision mà lượt chạy Root hiện tại đang xử lý."""
        with self._lock:
            return self._active_run_revision

    def begin_turn(
        self,
        text: str,
        *,
        timestamp: float | None = None,
        session_id: str = "default",
        entries: list[tuple[float, str]] | None = None,
    ) -> InputRevisionSnapshot:
        """Khởi tạo một lượt suy nghĩ mới của Root với câu nhắn đầu tiên của user."""
        clean = str(text or "").strip()
        now = float(self.clock() if timestamp is None else timestamp)
        with self._condition:
            self._session_id = str(session_id or "default")[:180]
            self._current_revision += 1
            rev = self._current_revision
            self._active_run_revision = rev
            entry = InputRevisionEntry(
                revision=rev,
                text=clean,
                timestamp=now,
                is_correction=_looks_like_correction(clean),
            )
            self._entries = ([InputRevisionEntry(revision=rev, text=str(t).strip(), timestamp=float(ts),
                                                is_correction=_looks_like_correction(str(t)))
                              for ts, t in entries if str(t).strip()]
                             if entries is not None else ([entry] if clean else []))
            self._pending_restart = False
            self._last_submit_at = now
            return self._snapshot_unlocked(now=now)

    def submit_revision(
        self,
        text: str,
        *,
        timestamp: float | None = None,
        on_mark_stale: Callable[[int, int], None] | None = None,
        invalidate_active: bool = True,
    ) -> InputRevisionSnapshot:
        """Nhận tin nhắn bổ sung/chỉnh sửa khi Root đang nghĩ (chưa public commit).

        Đánh dấu stale ngay lập tức cho revision đang chạy để hủy LLM/discovery cũ,
        đồng thời ghi nhận có bản cập nhật đang chờ restart sau cửa sổ debounce.
        """
        clean = str(text or "").strip()
        now = float(self.clock() if timestamp is None else timestamp)
        stale_target = 0
        new_rev = 0
        with self._condition:
            if not clean:
                return self._snapshot_unlocked(now=now)
            old_active = self._active_run_revision
            self._current_revision += 1
            new_rev = self._current_revision
            if invalidate_active and old_active > 0 and old_active < new_rev:
                self._stale_revisions.add(old_active)
                stale_target = old_active
            entry = InputRevisionEntry(
                revision=new_rev,
                text=clean,
                timestamp=now,
                is_correction=_looks_like_correction(clean),
            )
            self._entries.append(entry)
            self._pending_restart = True
            self._last_submit_at = now
            snapshot = self._snapshot_unlocked(now=now)
            self._condition.notify_all()

        # Gọi callback đánh dấu stale bên ngoài lock để tránh deadlock với Kernel/LLM.
        if stale_target > 0 and callable(on_mark_stale):
            try:
                on_mark_stale(stale_target, new_rev)
            except Exception:
                pass
        return snapshot

    def snapshot(self, *, claim: bool = False) -> InputRevisionSnapshot:
        """Capture all input at a request boundary without cancelling delivery."""
        with self._condition:
            if claim:
                self._active_run_revision = self._current_revision
                self._pending_restart = False
            return self._snapshot_unlocked(now=self.clock())

    def is_revision_stale(self, revision: int) -> bool:
        """Kiểm tra xem một revision có bị thay thế bởi câu nhắn mới hơn hay chưa."""
        with self._lock:
            rev = int(revision or 0)
            if rev <= 0:
                return False
            return rev in self._stale_revisions or rev < self._current_revision

    def has_pending_revision(self) -> bool:
        """True nếu có tin nhắn mới được gửi thêm trong lúc Root đang chạy."""
        with self._lock:
            return bool(self._pending_restart and self._current_revision > self._active_run_revision)

    def coalesce_and_claim_latest(
        self,
        *,
        debounce_seconds: float | None = None,
        poll_external_fn: Callable[[], list[tuple[float, str]]] | None = None,
    ) -> InputRevisionSnapshot | None:
        """Chờ cửa sổ debounce ngắn để gom hết chuỗi tin nhắn rồi claim revision mới nhất.

        Khi hàm này trả về một ``InputRevisionSnapshot``, ``active_run_revision`` được
        nâng lên bằng ``current_revision`` và cờ ``_pending_restart`` được hạ xuống.
        Nhờ vậy, dù user gửi 2 hay 5 tin nhắn liên tiếp trong lúc Root đang dừng,
        Root cũng chỉ restart đúng 1 lần trên toàn bộ nội dung đã gộp.
        """
        window = self.debounce_seconds if debounce_seconds is None else max(0.0, float(debounce_seconds))
        with self._condition:
            if not self._pending_restart or self._current_revision <= self._active_run_revision:
                return None

        # Chờ cửa sổ debounce ngắn để gom hết các câu user gõ nối tiếp nhau.
        if window > 0.0:
            deadline = self.clock() + window
            while True:
                if callable(poll_external_fn):
                    try:
                        extra = poll_external_fn() or []
                    except Exception:
                        extra = []
                    for ts, raw_text in extra:
                        self.submit_revision(raw_text, timestamp=ts)
                with self._condition:
                    deadline = max(deadline, self._last_submit_at + window)
                    remaining = deadline - self.clock()
                    if remaining <= 0.0:
                        break
                    self._condition.wait(timeout=min(0.05, remaining))
        elif callable(poll_external_fn):
            try:
                extra = poll_external_fn() or []
            except Exception:
                extra = []
            for ts, raw_text in extra:
                self.submit_revision(raw_text, timestamp=ts)

        with self._condition:
            if not self._pending_restart or self._current_revision <= self._active_run_revision:
                return None
            if self._active_run_revision > 0:
                self._stale_revisions.add(self._active_run_revision)
            self._active_run_revision = self._current_revision
            self._pending_restart = False
            return self._snapshot_unlocked(now=self.clock())

    def clear_turn(self) -> None:
        """Dọn trạng thái buffer sau khi lượt chat đã hoàn tất và commit xong."""
        with self._condition:
            self._entries.clear()
            self._pending_restart = False
            self._active_run_revision = self._current_revision

    def _compose_canonical_text_unlocked(self) -> str:
        """Hợp nhất các mảnh tin nhắn thành một trạng thái câu nhắn chuẩn (canonical).

        Nếu chỉ có 1 câu thì giữ nguyên. Nếu có nhiều câu (ví dụ user bổ sung hoặc
        đính chính "à không, ý tôi là..."), nối theo thứ tự thời gian và giữ nguyên
        mạch ngữ nghĩa để Root hiểu được sự tiến hóa của ý định user.
        """
        if not self._entries:
            return ""
        if len(self._entries) == 1:
            return self._entries[0].text
        ordered = sorted(self._entries, key=lambda item: (item.timestamp, item.revision))
        lines: list[str] = []
        for item in ordered:
            if item.text:
                lines.append(item.text)
        return "\n".join(lines)

    def _snapshot_unlocked(self, *, now: float) -> InputRevisionSnapshot:
        return InputRevisionSnapshot(
            session_id=self._session_id,
            revision=self._current_revision,
            canonical_text=self._compose_canonical_text_unlocked(),
            entries=tuple(self._entries),
            stale_marked_revisions=tuple(sorted(self._stale_revisions)[-16:]),
            updated_at=now,
        )


__all__ = [
    "InputRevisionBuffer",
    "InputRevisionEntry",
    "InputRevisionSnapshot",
]

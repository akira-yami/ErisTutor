# -*- coding: utf-8 -*-
"""OpenLoopStore & SuspendedMove — lưu giữ ý định đang nói dở khi bị user cắt ngang.

Tình huống chồng lấn:
Khi Eris đang trả lời mà bị user cắt ngang, ``interrupt_ledger.py`` đã tách được
``spoken_text_so_far`` (phần user đã nghe) và ``pending_text`` (phần chưa nói),
nhưng ``continuation.py`` lại xóa sạch lease/speculative candidate để chống ghost speech.
Hệ quả là Eris dễ "mất trí nhớ" về ý mình đang định nói.

Giải pháp trong module này:
1. Tạo ``SuspendedMove`` chứa phần đã nói (``spoken_text``), phần chưa nói dưới
   dạng gợi ý (``pending_text_hint`` — tuyệt đối không phải hàng đợi phát lại nguyên văn),
   và mục đích ban đầu của Root (``intent``).
2. Lưu vào ``OpenLoopStore`` (working memory ngắn hạn có TTL và giới hạn kích thước
   kiểu deque nhỏ, sống qua các lần đổi generation).
3. Ở lượt Root kế tiếp, truyền open-loop dưới dạng hint để Root tự đánh giá lại
   ngữ cảnh mới và quyết định ``RESUME`` (nói tiếp), ``ADAPT_AND_RESUME`` (điều chỉnh
   rồi nói tiếp), ``DEFER`` (hoãn lại) hay ``RETIRE`` (bỏ hẳn khi user đã đổi chủ đề
   hoặc phủ định tiền đề cũ).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
import re
import threading
import time
import uuid
import unicodedata
from typing import Any, Callable, Mapping


class OpenLoopDecision(str, Enum):
    """Quyết định của Root hoặc bộ điều phối đối với một ý đang nói dở."""

    RESUME = "RESUME"
    ADAPT_AND_RESUME = "ADAPT_AND_RESUME"
    DEFER = "DEFER"
    RETIRE = "RETIRE"


@dataclass(frozen=True, slots=True)
class SuspendedMove:
    """Bản ghi một lượt nói của Eris bị ngắt giữa chừng (Open Loop ngắn hạn).

    Lưu ý quan trọng: ``pending_text_hint`` chỉ là gợi ý ngữ nghĩa về phần chưa
    kịp phát để Root tham khảo ở lượt sau, KHÔNG BAO GIỜ được phát lại nguyên văn
    (tránh lỗi ghost speech khi user vừa phủ định nội dung đó).
    """

    loop_id: str
    session_id: str = "default"
    source_move_id: str = ""
    source_generation: int = 0
    intent: str = ""                 # Root định đạt mục đích gì ở lượt bị cắt
    spoken_text: str = ""            # Phần user thực tế đã nghe qua loa
    pending_text_hint: str = ""      # Phần chưa kịp nói, chỉ đóng vai trò hint cho Root
    pending_segments: tuple[str, ...] = ()
    interrupted_by: str = ""         # Câu nói của user đã cắt ngang lượt này
    interruption_kind: str = "steering"
    status: str = "suspended"        # suspended | resumed | retired | expired
    recommended_action: str = "RESUME"
    needs_revalidation: bool = True
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    turns_elapsed: int = 0

    def is_expired(self, now: float | None = None, *, max_turns: int = 4) -> bool:
        """Kiểm tra xem open loop đã hết hạn theo thời gian hoặc số lượt hội thoại chưa."""
        ts = float(time.time() if now is None else now)
        if self.status in {"retired", "resumed", "expired"}:
            return True
        if self.expires_at > 0.0 and ts >= self.expires_at:
            return True
        if max_turns > 0 and self.turns_elapsed >= max_turns:
            return True
        return False

    def to_prompt_hint(self) -> str:
        """Biểu diễn open loop dưới dạng hint cho lượt Root kế tiếp.

        Hướng dẫn rõ Root tự quyết định ``RESUME`` hay ``RETIRE`` dựa trên câu nói
        mới của user, không ép buộc lặp lại câu chữ cũ.
        """
        spoken = " ".join(str(self.spoken_text or "").split()).strip()[:260] or "(chưa kịp phát thành tiếng)"
        pending = " ".join(str(self.pending_text_hint or "").split()).strip()[:360] or "(không còn phần dư)"
        intent_str = " ".join(str(self.intent or "").split()).strip()[:240] or "Đang trả lời dở ý trước đó"
        user_cut = " ".join(str(self.interrupted_by or "").split()).strip()[:240]
        return (
            "OPEN_LOOP_HINT (SuspendedMove):\n"
            f"- loop_id={self.loop_id}\n"
            f"- intended_move={intent_str}\n"
            f"- spoken_text_so_far={spoken}\n"
            f"- pending_text_hint={pending}\n"
            f"- interrupted_by_user={user_cut}\n"
            f"- recommended_decision={self.recommended_action}\n"
            "Chỉ dẫn: Đây là ý bạn đang nói dở trước khi bị ngắt (pending_text_hint chỉ là gợi ý, "
            "KHÔNG tự động đọc lại nguyên văn). Hãy đánh giá ngữ cảnh mới từ user để quyết định "
            "RESUME (tiếp tục ý còn dở sau khi đáp lại câu chen ngang) hoặc RETIRE (bỏ hẳn ý cũ "
            "nếu user đã bác bỏ tiền đề hoặc đổi sang chủ đề khác)."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.suspended_move.v1",
            "loop_id": self.loop_id,
            "session_id": self.session_id,
            "source_move_id": self.source_move_id,
            "source_generation": self.source_generation,
            "intent": self.intent,
            "spoken_text": self.spoken_text,
            "pending_text_hint": self.pending_text_hint,
            "pending_segments": list(self.pending_segments),
            "interrupted_by": self.interrupted_by,
            "interruption_kind": self.interruption_kind,
            "status": self.status,
            "recommended_action": self.recommended_action,
            "needs_revalidation": self.needs_revalidation,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "turns_elapsed": self.turns_elapsed,
        }


_RETIRE_TOPIC_MARKERS = (
    "bỏ đoạn đó",
    "bo doan do",
    "bỏ ý đó",
    "bo y do",
    "bỏ hướng đó",
    "bo huong do",
    "sang chuyện khác",
    "sang chuyen khac",
    "đổi chủ đề",
    "doi chu de",
    "đổi đề tài",
    "doi de tai",
    "thôi bỏ đi",
    "thoi bo di",
    "không dùng",
    "khong dung",
    "bỏ qua phần",
    "bo qua phan",
)

_CORRECTION_CONFLICT_MARKERS = (
    "sai rồi",
    "sai roi",
    "không phải vậy",
    "khong phai vay",
    "nhầm rồi",
    "nham roi",
    "premise đó sai",
    "không đúng",
    "khong dung",
)

_EXPLICIT_RESUME_MARKERS = (
    "nói tiếp đi",
    "noi tiep di",
    "nãy nói tiếp",
    "nay noi tiep",
    "tiếp tục đi",
    "tiep tuc di",
    "quay lại đoạn nãy",
    "quay lai doan nay",
    "kể tiếp đi",
    "ke tiep di",
    "nói nốt đi",
    "noi not di",
)


def _normalize_vi_simple(text: str) -> str:
    value = str(text or "").strip().lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^\w\s]+", " ", value)
    return " ".join(value.split())


def evaluate_open_loop_action(loop: SuspendedMove, latest_user_text: str) -> OpenLoopDecision:
    """Ước lượng quyết định ngữ nghĩa (RESUME hay RETIRE) cho một SuspendedMove.

    Hàm này cung cấp gợi ý kèm theo open loop cho Root và tự động dọn (RETIRE)
    những loop đã mâu thuẫn trực tiếp với lời đính chính hoặc yêu cầu đổi chủ đề
    của người dùng.
    """
    raw = str(latest_user_text or "").strip().lower()
    norm = _normalize_vi_simple(raw)
    if not norm:
        return OpenLoopDecision.RESUME

    if any(marker in raw or marker in norm for marker in _EXPLICIT_RESUME_MARKERS):
        return OpenLoopDecision.RESUME

    if any(marker in raw or marker in norm for marker in _RETIRE_TOPIC_MARKERS):
        return OpenLoopDecision.RETIRE

    if any(marker in raw or marker in norm for marker in _CORRECTION_CONFLICT_MARKERS):
        return OpenLoopDecision.RETIRE

    # Kiểm tra xem user có bác bỏ trực tiếp một thực thể trong pending_text_hint không
    # Ví dụ: "bỏ Redis đi, mình không dùng Redis nữa" khi pending_text_hint đang nói về Redis.
    pending_norm = _normalize_vi_simple(loop.pending_text_hint)
    if pending_norm and ("bo " in norm or "khong dung " in norm or "khong xai " in norm):
        pending_tokens = {tok for tok in pending_norm.split() if len(tok) >= 3}
        user_tokens = set(norm.split())
        if pending_tokens & user_tokens:
            return OpenLoopDecision.RETIRE

    return OpenLoopDecision.RESUME


class OpenLoopStore:
    """Kho lưu trữ ngắn hạn (working state) cho các ``SuspendedMove`` qua các generation.

    Không phải long-term memory và không ghi phần chưa nói thành lịch sử đã phát.
    Sử dụng cấu trúc deque nhỏ (mặc định tối đa 3 phần tử mỗi phiên) để tránh biến
    các lần ngắt lời thành một backlog vô tận.
    """

    def __init__(
        self,
        *,
        max_loops_per_session: int = 3,
        default_ttl_seconds: float = 180.0,
        max_turns_alive: int = 4,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.max_loops_per_session = max(1, int(max_loops_per_session))
        self.default_ttl_seconds = max(10.0, float(default_ttl_seconds))
        self.max_turns_alive = max(1, int(max_turns_alive))
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._store: dict[str, deque[SuspendedMove]] = {}

    @staticmethod
    def _sid(session_id: str) -> str:
        return str(session_id or "default").strip()[:180] or "default"

    def capture_from_interrupt(
        self,
        event: Any,
        *,
        session_id: str = "default",
        source_move_id: str = "",
        source_generation: int = 0,
        intent: str = "",
        interruption_kind: str = "steering",
        pending_segments: tuple[str, ...] = (),
        now: float | None = None,
    ) -> SuspendedMove | None:
        """Tạo và lưu một ``SuspendedMove`` từ ``InterruptEvent`` khi bị user cắt ngang."""
        if event is None:
            return None
        pending_hint = str(getattr(event, "pending_text", "") or "").strip()
        if not pending_hint:
            return None
        spoken = str(getattr(event, "spoken_text_so_far", "") or "").strip()
        user_text = str(getattr(event, "user_text", "") or "").strip()
        ts = float(self.clock() if now is None else now)
        sid = self._sid(session_id)
        turn_id = str(getattr(event, "interrupted_turn_id", "") or "")

        move = SuspendedMove(
            loop_id=f"openloop:{uuid.uuid4().hex[:12]}",
            session_id=sid,
            source_move_id=str(source_move_id or turn_id)[:180],
            source_generation=max(0, int(source_generation)),
            intent=str(intent or "Tiếp tục giải thích ý còn đang dở ở lượt trước.")[:600],
            spoken_text=spoken[:2000],
            pending_text_hint=pending_hint[:3000],
            pending_segments=tuple(str(seg)[:800] for seg in pending_segments if str(seg).strip()),
            interrupted_by=user_text[:1000],
            interruption_kind=str(interruption_kind or "steering")[:60],
            status="suspended",
            recommended_action="RESUME",
            needs_revalidation=True,
            created_at=ts,
            expires_at=ts + self.default_ttl_seconds,
            turns_elapsed=0,
        )
        action = evaluate_open_loop_action(move, user_text)
        move = replace(
            move,
            recommended_action=action.value,
            status="retired" if action == OpenLoopDecision.RETIRE else "suspended",
        )
        with self._lock:
            self._prune_expired_unlocked(sid, now=ts)
            bucket = self._store.get(sid)
            if bucket is None:
                bucket = deque(maxlen=self.max_loops_per_session)
                self._store[sid] = bucket
            if move.status != "retired":
                bucket.appendleft(move)
            else:
                # Nếu chính câu cắt ngang đã bác bỏ tiền đề, retire luôn cả các loop cũ liên quan.
                self.retire_all(sid, reason="user_correction_or_topic_shift")
        return move

    def add(self, move: SuspendedMove) -> SuspendedMove:
        """Thêm trực tiếp một ``SuspendedMove`` vào kho lưu trữ của phiên."""
        sid = self._sid(move.session_id)
        ts = float(self.clock())
        with self._lock:
            self._prune_expired_unlocked(sid, now=ts)
            bucket = self._store.get(sid)
            if bucket is None:
                bucket = deque(maxlen=self.max_loops_per_session)
                self._store[sid] = bucket
            bucket.appendleft(move)
        return move

    def active_loops(self, session_id: str = "default", *, now: float | None = None) -> tuple[SuspendedMove, ...]:
        """Trả về danh sách các open loop còn hiệu lực (từ mới nhất đến cũ nhất)."""
        sid = self._sid(session_id)
        ts = float(self.clock() if now is None else now)
        with self._lock:
            self._prune_expired_unlocked(sid, now=ts)
            bucket = self._store.get(sid)
            if not bucket:
                return ()
            return tuple(item for item in bucket if item.status == "suspended")

    def latest_active(self, session_id: str = "default", *, now: float | None = None) -> SuspendedMove | None:
        """Lấy open loop mới nhất còn khả năng resume."""
        loops = self.active_loops(session_id, now=now)
        return loops[0] if loops else None

    def prepare_hints_for_root(
        self,
        session_id: str = "default",
        *,
        latest_user_text: str = "",
        now: float | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Chuẩn bị prompt hint và danh sách metadata của các open loop cho lượt Root kế tiếp.

        Nếu câu mới của user yêu cầu đổi chủ đề hoặc bác bỏ tiền đề cũ, loop tương ứng
        sẽ được chuyển sang ``RETIRE`` ngay và báo rõ để Root không nhắc lại.
        Nếu user nói "nói tiếp đi", loop mới nhất được đánh dấu ``RESUME`` ưu tiên cao.
        """
        sid = self._sid(session_id)
        ts = float(self.clock() if now is None else now)
        with self._lock:
            self._prune_expired_unlocked(sid, now=ts)
            bucket = self._store.get(sid)
            if not bucket:
                return "", []

            updated_bucket: deque[SuspendedMove] = deque(maxlen=self.max_loops_per_session)
            hints: list[str] = []
            payloads: list[dict[str, Any]] = []

            for item in bucket:
                if item.status != "suspended":
                    continue
                decision = (
                     evaluate_open_loop_action(item, latest_user_text)
                    if latest_user_text
                    else OpenLoopDecision(item.recommended_action)
                )
                next_turns = item.turns_elapsed + 1
                if decision == OpenLoopDecision.RETIRE:
                    retired_item = replace(
                        item,
                        status="retired",
                        recommended_action=OpenLoopDecision.RETIRE.value,
                        turns_elapsed=next_turns,
                    )
                    payloads.append(retired_item.to_dict())
                    continue

                active_item = replace(
                    item,
                    recommended_action=decision.value,
                    turns_elapsed=next_turns,
                )
                hints.append(active_item.to_prompt_hint())
                payloads.append(active_item.to_dict())
                if next_turns < self.max_turns_alive:
                    updated_bucket.append(active_item)

            self._store[sid] = updated_bucket
            return "\n\n".join(hints), payloads

    def apply_decision(
        self,
        loop_id: str,
        decision: OpenLoopDecision | str,
        *,
        session_id: str = "default",
    ) -> bool:
        """Cập nhật trạng thái của một open loop sau khi Root đã xử lý (RESUME hoặc RETIRE)."""
        sid = self._sid(session_id)
        target_id = str(loop_id or "").strip()
        if not target_id:
            return False
        raw_dec = decision.value if isinstance(decision, OpenLoopDecision) else str(decision or "").strip().upper()
        with self._lock:
            bucket = self._store.get(sid)
            if not bucket:
                return False
            new_bucket: deque[SuspendedMove] = deque(maxlen=self.max_loops_per_session)
            changed = False
            for item in bucket:
                if item.loop_id == target_id:
                    changed = True
                    if raw_dec in {"RETIRE", "RESUME", "ADAPT_AND_RESUME"}:
                        # Khi đã RESUME hoặc RETIRE thì lấy ra khỏi hàng đợi đang chờ.
                        continue
                    new_bucket.append(replace(item, recommended_action=raw_dec))
                else:
                    new_bucket.append(item)
            self._store[sid] = new_bucket
            return changed

    def retire_all(self, session_id: str = "default", *, reason: str = "topic_shift") -> int:
        """Xóa toàn bộ các open loop đang treo của một phiên khi đổi chủ đề."""
        del reason
        sid = self._sid(session_id)
        with self._lock:
            bucket = self._store.pop(sid, None)
            return len(bucket) if bucket else 0

    def _prune_expired_unlocked(self, session_id: str, *, now: float) -> None:
        bucket = self._store.get(session_id)
        if not bucket:
            return
        kept: deque[SuspendedMove] = deque(maxlen=self.max_loops_per_session)
        for item in bucket:
            if not item.is_expired(now, max_turns=self.max_turns_alive):
                kept.append(item)
        self._store[session_id] = kept


def ensure_open_loop_store(host: Any) -> OpenLoopStore:
    """Lấy hoặc khởi tạo ``OpenLoopStore`` gắn trên host runtime."""
    store = getattr(host, "open_loop_store", None)
    if isinstance(store, OpenLoopStore):
        return store
    store = OpenLoopStore()
    try:
        host.open_loop_store = store
    except Exception:
        pass
    return store


__all__ = [
    "OpenLoopDecision",
    "OpenLoopStore",
    "SuspendedMove",
    "ensure_open_loop_store",
    "evaluate_open_loop_action",
]

# -*- coding: utf-8 -*-
"""Lane nền cho Living User Memory: tra khi cần, không chẹn critical path.

Vì sao có file này: `build_public_context` (recall + compile Living User Memory)
chạy ĐỒNG BỘ trên đường user->Root, thỉnh thoảng chẹn ~42s (đo được
`memory_context_ms~43000`). Memory là thứ "tra khi cần", không phải thuế phải
đóng mỗi lượt.

Cơ chế: giữ SNAPSHOT (text, records, degraded) của lần build gần nhất. Mỗi lượt
Root DÙNG snapshot ngay (0 chờ) và kick một REFRESH NỀN ghi đè snapshot cho lượt
sau. Lượt đầu chưa có snapshot thì build đồng bộ MỘT lần có deadline (seed), quá
hạn trả degraded rỗng để không bao giờ treo.

Bảo toàn vùng nhạy cảm: snapshot là OUTPUT THẬT của một lần `build_public_context`
(giữ nguyên `records`/`degraded`), KHÔNG bịa record, KHÔNG suy provenance từ prose.
Đổi lại Root có thể đọc nhớ trễ MỘT lượt — chấp nhận cho chat; khi user hỏi thẳng
về trí nhớ, Root vẫn có tool recall on-demand lấy bản tươi.

Không planner, không LLM thứ hai; thuần hạ tầng cache + thread nền fail-soft.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# Kiểu hàm build: (user_text) -> (text, records, degraded)
BuildFn = Callable[[str], "tuple[str, tuple[Any, ...], bool]"]

# Deadline cho lần SEED đồng bộ (lượt đầu, chưa có snapshot). Quá hạn -> degraded
# rỗng; refresh nền vẫn chạy tiếp để lượt sau có bản thật. Không hủy thread nền
# (recall read-only nên để nó hoàn tất ghi cache là an toàn).
_SEED_DEADLINE_SEC = 2.0


@dataclass
class _Snapshot:
    text: str = ""
    records: tuple[Any, ...] = ()
    degraded: bool = True
    built_at: float = 0.0
    key: str = ""


@dataclass
class MemoryLane:
    """Giữ snapshot memory gần nhất + điều phối refresh nền (một luồng/host)."""
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _snapshot: _Snapshot = field(default_factory=_Snapshot)
    _refreshing: bool = False

    def current(self) -> tuple[str, tuple[Any, ...], bool] | None:
        """Snapshot gần nhất, hoặc None nếu chưa từng build xong."""
        with self._lock:
            snap = self._snapshot
            if snap.built_at <= 0.0:
                return None
            return (snap.text, snap.records, snap.degraded)

    def _commit(self, result: tuple[str, tuple[Any, ...], bool], key: str) -> None:
        with self._lock:
            self._snapshot = _Snapshot(
                text=result[0], records=tuple(result[1] or ()),
                degraded=bool(result[2]), built_at=time.time(), key=key,
            )

    def _kick_background(self, build: BuildFn, user_text: str, key: str) -> None:
        """Chạy refresh nền đúng một luồng tại một thời điểm; fail-soft."""
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True

        def _run() -> None:
            try:
                result = build(user_text)
                self._commit(result, key)
            except Exception:
                # Lỗi refresh không được làm hỏng lượt; giữ snapshot cũ.
                pass
            finally:
                with self._lock:
                    self._refreshing = False

        threading.Thread(target=_run, name="eris-memory-lane", daemon=True).start()

    def _seed_sync(self, build: BuildFn, user_text: str, key: str) -> tuple[str, tuple[Any, ...], bool]:
        """Lượt đầu: build trong một thread có deadline. Quá hạn -> degraded rỗng.

        Thread tiếp tục chạy để ghi snapshot cho lượt sau (không hủy giữa chừng).
        """
        box: dict[str, tuple[str, tuple[Any, ...], bool]] = {}

        def _run() -> None:
            try:
                result = build(user_text)
                box["result"] = result
                self._commit(result, key)
            except Exception:
                pass
            finally:
                with self._lock:
                    self._refreshing = False

        with self._lock:
            self._refreshing = True
        worker = threading.Thread(target=_run, name="eris-memory-lane-seed", daemon=True)
        worker.start()
        worker.join(timeout=_SEED_DEADLINE_SEC)
        if "result" in box:
            return box["result"]
        # Quá hạn: trả degraded rỗng, thread nền vẫn hoàn tất cho lượt sau.
        return ("", (), True)

    def get(self, build: BuildFn, user_text: str) -> tuple[str, tuple[Any, ...], bool]:
        """Trả memory cho lượt hiện tại: snapshot gần nhất + kick refresh nền.

        - Có snapshot: dùng ngay (0 chờ), kick refresh nền cho lượt sau.
        - Chưa có snapshot: seed đồng bộ có deadline, không treo quá ~2s.
        """
        key = str(user_text or "")[:200]
        snap = self.current()
        if snap is not None:
            self._kick_background(build, user_text, key)
            return snap
        return self._seed_sync(build, user_text, key)


def ensure_memory_lane(host: Any) -> MemoryLane:
    """Một MemoryLane dùng lại trên mỗi host (giữ snapshot xuyên lượt)."""
    lane = getattr(host, "_memory_lane", None)
    if not isinstance(lane, MemoryLane):
        lane = MemoryLane()
        try:
            host._memory_lane = lane
        except Exception:
            pass
    return lane

# -*- coding: utf-8 -*-
"""
llm/key_pool.py

Pool API key dùng CHUNG cho mọi mạch gọi Gemini (main / speech / private_mind).

Mỗi key thường thuộc một Google project khác nhau (vd nhiều Gmail) → mỗi key có
hũ free quota riêng. Free tier tính theo PROJECT chứ không theo key, nên cách
duy nhất để "buff free" là xoay vòng qua các key thuộc project khác nhau.

Pool này giữ MỘT index dùng chung cho cả ba mạch: khi một mạch gặp 429 và xoay
key, các mạch còn lại cũng dùng key mới đó. Vắt cạn project này thì cả hệ nhảy
sang project kế tiếp cùng lúc.

Thread-safe. Không bao giờ ném lỗi ra ngoài (đọc secrets hỏng → pool rỗng).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable, List

from runtime.diagnostic_logging import safe_log

_LOGGER = logging.getLogger("LLM")

@dataclass(frozen=True, slots=True)
class ApiKeyLease:
    """Một key cụ thể đã được cấp cho đúng một request.

    Request phải giữ lease này cho tới khi thành công/thất bại. Khi báo lỗi,
    pool đánh dấu chính ``lease.index`` thay vì con trỏ toàn cục có thể đã bị
    thread khác xoay mất.
    """

    index: int
    key: str


class ApiKeyPool:
    """Danh sách key + một con trỏ rotation dùng chung, có theo dõi key "chết".

    Mỗi key có thể bị đánh dấu nghỉ (dead) trong N giây khi gặp 429/413/5xx; pool
    tự bỏ qua key nghỉ khi xoay/đọc, và tự "hồi" sau khi hết cooldown. Nhờ vậy các
    mạch chạy song song không cùng đập vào một key đã hết quota.
    """

    def __init__(self, keys: List[str], *, provider: str = "") -> None:
        seen: set[str] = set()
        ordered: list[str] = []
        for k in keys:
            k = str(k or "").strip()
            if k and k not in seen:
                seen.add(k)
                ordered.append(k)
        self._keys = ordered
        self.provider = str(provider or "unknown").strip().lower() or "unknown"
        self._dead_until: list[float] = [0.0] * len(ordered)
        self._quarantine_reasons: list[str] = [""] * len(ordered)
        self._index = 0
        self._lock = threading.Lock()
        self._rotations = 0
        # Một request không được phép biến cả pool thành nghĩa địa chỉ vì provider
        # đang rate-limit theo project/model. Ghi nhận burst 429 trên nhiều key và
        # mở cooldown toàn pool ngắn để chặn vòng xoay dây chuyền.
        self._rate_limit_events: list[tuple[float, int, str]] = []
        self._global_cooldown_until = 0.0
        self._rate_limit_bursts = 0

    @property
    def size(self) -> int:
        return len(self._keys)

    def _alive_locked(self, idx: int, now: float) -> bool:
        return self._dead_until[idx] <= now

    def alive_count(self) -> int:
        now = time.time()
        with self._lock:
            if self._global_cooldown_until > now:
                return 0
            return sum(1 for i in range(len(self._keys)) if self._alive_locked(i, now))

    def _next_alive_locked(self, start: int, now: float) -> int:
        """Tìm index key sống kế tiếp (gồm cả start). -1 nếu không có key sống."""
        n = len(self._keys)
        for offset in range(n):
            idx = (start + offset) % n
            if self._alive_locked(idx, now):
                return idx
        return -1

    def acquire(
        self,
        *,
        exclude_indices: Iterable[int] = (),
        allow_cooling: bool = False,
    ) -> ApiKeyLease | None:
        """Cấp một lease cho key sống, bỏ qua các index request đã thử.

        ``allow_cooling=False`` là đường request chuẩn: nếu toàn pool đang cooldown
        thì trả ``None`` để caller dừng, không lấy key "ít chết nhất" rồi tiếp tục
        bắn API. ``current_key`` giữ hành vi tương thích cũ cho lúc khởi tạo client.
        """

        excluded = {int(i) for i in exclude_indices}
        now = time.time()
        with self._lock:
            if not self._keys:
                return None
            if self._global_cooldown_until > now and not allow_cooling:
                return None
            n = len(self._keys)
            for offset in range(n):
                idx = (self._index + offset) % n
                if idx in excluded or not self._alive_locked(idx, now):
                    continue
                if idx != self._index:
                    self._rotations += 1
                self._index = idx
                return ApiKeyLease(index=idx, key=self._keys[idx])

            if not allow_cooling:
                return None
            candidates = [i for i in range(n) if i not in excluded]
            if not candidates:
                candidates = list(range(n))
            idx = min(candidates, key=lambda i: self._dead_until[i])
            if idx != self._index:
                self._rotations += 1
            self._index = idx
            return ApiKeyLease(index=idx, key=self._keys[idx])

    def current_key(self) -> str:
        lease = self.acquire(allow_cooling=True)
        return lease.key if lease is not None else ""

    def rotate(
        self,
        *,
        from_index: int | None = None,
        lease: ApiKeyLease | None = None,
    ) -> bool:
        """Xoay từ đúng key đã dùng, không mặc định đổ lỗi cho con trỏ hiện tại."""

        now = time.time()
        with self._lock:
            if len(self._keys) <= 1:
                return False
            base = lease.index if lease is not None else from_index
            if base is None or not (0 <= int(base) < len(self._keys)):
                base = self._index
            base = int(base)
            nxt = self._next_alive_locked((base + 1) % len(self._keys), now)
            if nxt < 0 or nxt == base:
                return False
            if nxt != self._index:
                self._rotations += 1
            self._index = nxt
            return True

    def mark_success(self, *, lease: ApiKeyLease | None = None, index: int | None = None) -> None:
        """Xóa dấu burst sau một request thành công.

        Thành công trên bất kỳ key nào chứng minh provider không còn bị chặn toàn
        cục. Cooldown riêng của các key khác vẫn được giữ nguyên.
        """

        now = time.time()
        with self._lock:
            target = lease.index if lease is not None else index
            if target is not None and 0 <= int(target) < len(self._dead_until):
                self._dead_until[int(target)] = 0.0
                self._quarantine_reasons[int(target)] = ""
            self._global_cooldown_until = 0.0
            self._rate_limit_events = [
                (ts, idx, scope)
                for ts, idx, scope in self._rate_limit_events
                if now - ts <= 2.0 and idx != target
            ]

    def mark_rate_limited(
        self,
        seconds: float = 60.0,
        *,
        index: int | None = None,
        lease: ApiKeyLease | None = None,
        reason: str = "http_429",
    ) -> bool:
        """Cooldown key bị 429 và quyết định có nên xoay sang key khác hay không.

        Vẫn tôn trọng giả định mỗi key có quota project riêng, nhưng không xoay
        sạch cả pool trong một burst. Sau vài key liên tiếp cùng lane/model đều
        429, breaker mở cooldown ngắn để main loop/fallback có cơ hội đổi hướng.
        """

        now = time.time()
        cooldown = max(1.0, float(seconds))
        try:
            window_s = max(1.0, float(os.environ.get("ERIS_KEY_POOL_429_BURST_WINDOW_S", "8")))
        except ValueError:
            window_s = 8.0
        raw_threshold = str(os.environ.get("ERIS_KEY_POOL_429_BURST_KEYS", "") or "").strip()
        try:
            threshold = max(2, int(raw_threshold)) if raw_threshold else max(
                3,
                min(5, (len(self._keys) + 2) // 3),
            )
        except ValueError:
            threshold = max(3, min(5, (len(self._keys) + 2) // 3))
        try:
            global_cooldown_s = max(2.0, float(os.environ.get("ERIS_KEY_POOL_429_GLOBAL_COOLDOWN_S", "30")))
        except ValueError:
            global_cooldown_s = 30.0
        # Adaptive guard mặc định bật: vẫn cho thử vài project khác nhau, nhưng
        # không biến một lượt background thành cuộc hành quân qua 9-12 key.
        adaptive_guard = str(os.environ.get("ERIS_KEY_POOL_429_ADAPTIVE_GUARD", "1") or "1").strip().lower() in {
            "1", "true", "yes", "on"
        }
        global_guard = str(os.environ.get("ERIS_KEY_POOL_429_GLOBAL_GUARD", "0") or "0").strip().lower() in {
            "1", "true", "yes", "on"
        }
        scope = str(reason or "http_429").strip().lower() or "http_429"
        with self._lock:
            if not self._keys:
                return False
            target = lease.index if lease is not None else index
            if target is None or not (0 <= int(target) < len(self._keys)):
                target = self._index
            target = int(target)
            self._dead_until[target] = max(self._dead_until[target], now + cooldown)

            self._rate_limit_events = [
                (ts, idx, event_scope)
                for ts, idx, event_scope in self._rate_limit_events
                if now - ts <= window_s
            ]
            self._rate_limit_events.append((now, target, scope))
            unique_keys = {
                idx for _ts, idx, event_scope in self._rate_limit_events if event_scope == scope
            }
            threshold = min(max(2, threshold), max(2, len(self._keys)))
            burst = (adaptive_guard or global_guard) and len(self._keys) > 1 and len(unique_keys) >= threshold

            alive = sum(1 for i in range(len(self._keys)) if self._dead_until[i] <= now)
            if burst:
                # Tôn trọng Retry-After nhưng không phạt toàn pool vô hạn. Cooldown
                # riêng của từng key vẫn giữ nguyên ở _dead_until.
                shared_wait = max(global_cooldown_s, min(cooldown, 120.0))
                was_open = self._global_cooldown_until > now
                self._global_cooldown_until = max(self._global_cooldown_until, now + shared_wait)
                if not was_open:
                    self._rate_limit_bursts += 1
                    _LOGGER.warning(
                        "[KEY POOL] 429 burst scope=%s trên %d key/%ds; dừng xoay, cooldown pool %.0fs (alive=%d/%d)",
                        scope,
                        len(unique_keys),
                        int(window_s),
                        shared_wait,
                        alive,
                        len(self._keys),
                    )
                return False

            _LOGGER.warning(
                "[KEY POOL] cooldown key index=%d %.0fs vì %s; alive=%d/%d",
                target,
                cooldown,
                reason,
                alive,
                len(self._keys),
            )
            return alive > 0

    def mark_dead(
        self,
        seconds: float = 60.0,
        *,
        index: int | None = None,
        lease: ApiKeyLease | None = None,
        reason: str = "",
    ) -> None:
        """Đánh dấu chính key gây lỗi, kể cả khi thread khác đã xoay pool."""

        now = time.time()
        with self._lock:
            if not self._keys:
                return
            target = lease.index if lease is not None else index
            if target is None or not (0 <= int(target) < len(self._keys)):
                target = self._index
            target = int(target)
            cooldown = max(1.0, float(seconds))
            self._dead_until[target] = max(self._dead_until[target], now + cooldown)
            alive = sum(1 for i in range(len(self._keys)) if self._dead_until[i] <= now)
            suffix = f" reason={reason}" if reason else ""
            _LOGGER.warning(
                "[KEY POOL] mark dead index=%d cooldown=%.0fs alive=%d/%d%s",
                target,
                cooldown,
                alive,
                len(self._keys),
                suffix,
            )

    def mark_permanently_dead(
        self,
        *,
        index: int | None = None,
        lease: ApiKeyLease | None = None,
        reason: str = "key_revoked",
    ) -> None:
        """Quarantine key lỗi auth/leaked gần như vĩnh viễn trong process.

        Google có lỗi 403 tạm do permission/project, nhưng thông báo leaked/revoked
        thì không nên thử lại ở câu sau. 30 ngày ở runtime coi như chết cho tới
        khi user sửa config/restart.
        """

        try:
            seconds = float(os.environ.get("ERIS_KEY_POOL_PERMANENT_DEAD_S", str(30 * 24 * 3600)))
        except ValueError:
            seconds = float(30 * 24 * 3600)
        target = lease.index if lease is not None else index
        self.quarantine(index=target, seconds=seconds, reason=reason)

    def find_index_by_fingerprint(self, fingerprint: str) -> int | None:
        needle = str(fingerprint or "").strip().lower()
        if not needle:
            return None
        with self._lock:
            for idx, raw_key in enumerate(self._keys):
                current = hashlib.sha256(raw_key.encode("utf-8", "ignore")).hexdigest()[:12]
                if current.lower() == needle:
                    return idx
        return None

    def quarantine(
        self,
        *,
        index: int | None = None,
        fingerprint: str = "",
        seconds: float | None = None,
        reason: str = "manual_quarantine",
    ) -> bool:
        """Disable một key trong runtime mà không xóa secret khỏi đĩa."""

        target = index if index is not None else self.find_index_by_fingerprint(fingerprint)
        if target is None:
            return False
        try:
            duration = float(seconds) if seconds is not None else float(30 * 24 * 3600)
        except Exception:
            duration = float(30 * 24 * 3600)
        now = time.time()
        with self._lock:
            if not (0 <= int(target) < len(self._keys)):
                return False
            target = int(target)
            self._dead_until[target] = max(self._dead_until[target], now + max(1.0, duration))
            self._quarantine_reasons[target] = str(reason or "manual_quarantine")[:120]
            if target == self._index:
                nxt = self._next_alive_locked((target + 1) % max(1, len(self._keys)), now)
                if nxt >= 0 and nxt != target:
                    self._index = nxt
                    self._rotations += 1
        _LOGGER.warning("[KEY POOL] quarantine index=%d reason=%s", target, reason)
        self._record_health("quarantine")
        return True

    def restore(
        self, *, index: int | None = None, fingerprint: str = ""
    ) -> bool:
        """Bỏ quarantine/cooldown một key sau khi user đã kiểm tra nó."""

        target = index if index is not None else self.find_index_by_fingerprint(fingerprint)
        if target is None:
            return False
        with self._lock:
            if not (0 <= int(target) < len(self._keys)):
                return False
            target = int(target)
            self._dead_until[target] = 0.0
            self._quarantine_reasons[target] = ""
            self._global_cooldown_until = 0.0
        _LOGGER.info("[KEY POOL] restore index=%d", target)
        self._record_health("restore")
        return True

    def force_rotate(self, *, from_index: int | None = None) -> bool:
        """Chuyển active pointer sang key sống kế tiếp, không đánh dấu key cũ lỗi."""

        changed = self.rotate(from_index=from_index)
        if changed:
            self._record_health("manual_rotate")
        return changed

    def _record_health(self, event: str) -> None:
        try:
            from runtime.observability import GLOBAL_OBSERVABILITY
            GLOBAL_OBSERVABILITY.record_provider_pool(
                provider=self.provider, snapshot=self.snapshot(), event=event
            )
        except Exception as exc:
            safe_log(_LOGGER, "debug", "Provider-pool telemetry failed: %s", exc)

    def next_available_in(self) -> float:
        """Số giây còn lại tới lúc ít nhất một key có thể được cấp."""

        now = time.time()
        with self._lock:
            if not self._keys:
                return 0.0
            waits = [max(0.0, deadline - now) for deadline in self._dead_until]
            key_wait = min(waits) if waits else 0.0
            shared_wait = max(0.0, self._global_cooldown_until - now)
            return max(shared_wait, key_wait)

    def index(self) -> int:
        with self._lock:
            return self._index

    def stats(self) -> dict[str, int]:
        now = time.time()
        with self._lock:
            shared_cooling = self._global_cooldown_until > now
            alive_raw = sum(1 for i in range(len(self._keys)) if self._dead_until[i] <= now)
            return {
                "size": len(self._keys),
                # alive/dead mô tả trạng thái từng key; available mới phản ánh
                # cooldown chung. Nhờ vậy alive + dead luôn bằng size.
                "alive": alive_raw,
                "available": 0 if shared_cooling else alive_raw,
                "dead": len(self._keys) - alive_raw,
                "rotations": self._rotations,
                "rate_limit_bursts": self._rate_limit_bursts,
                "global_cooldown_ms": int(max(0.0, self._global_cooldown_until - now) * 1000),
            }

    def health_snapshot(self) -> dict[str, object]:
        """Alias rõ nghĩa cho Settings UI; không bao giờ trả raw API key."""
        return self.snapshot()

    def snapshot(self) -> dict[str, object]:
        """Trả health an toàn cho Settings UI, tuyệt đối không lộ raw API key.

        UI chỉ nhận SHA-256 fingerprint 12 ký tự để ghép trạng thái runtime với
        key đang hiện trong editor. Nhờ vậy có thể chỉ đúng key đang dùng/cooldown
        mà không gửi bí mật ngược qua transport hoặc ghi chúng vào log.
        """

        now = time.time()
        with self._lock:
            shared_cooling = self._global_cooldown_until > now
            alive_raw = sum(1 for i in range(len(self._keys)) if self._dead_until[i] <= now)
            active_index = -1 if shared_cooling else self._next_alive_locked(self._index, now)
            keys: list[dict[str, object]] = []
            for idx, raw_key in enumerate(self._keys):
                cooldown_ms = int(max(0.0, self._dead_until[idx] - now) * 1000)
                quarantine_reason = self._quarantine_reasons[idx] if idx < len(self._quarantine_reasons) else ""
                status = "quarantined" if cooldown_ms > 0 and quarantine_reason else ("cooldown" if cooldown_ms > 0 else "ready")
                keys.append(
                    {
                        "index": idx,
                        "fingerprint": hashlib.sha256(raw_key.encode("utf-8", "ignore")).hexdigest()[:12],
                        "status": status,
                        "cooldown_ms": cooldown_ms,
                        "active": idx == active_index,
                        "reason": quarantine_reason,
                    }
                )

            return {
                "size": len(self._keys),
                "alive": alive_raw,
                "available": 0 if shared_cooling else alive_raw,
                "dead": len(self._keys) - alive_raw,
                "rotations": self._rotations,
                "rate_limit_bursts": self._rate_limit_bursts,
                "global_cooldown_ms": int(max(0.0, self._global_cooldown_until - now) * 1000),
                "keys": keys,
            }


_GROQ_PROVIDERS = {"groq"}
_REMOTE_MAIN_KINDS = {"main", "main_brain", "remote_main", "deepseek", "openai", "openai_compatible", "compatible", "remote"}

# Có ba pool tách biệt:
# - remote: credential dành riêng cho não chính;
# - gemini: speech/private-mind và các lane Gemini phụ;
# - groq: parser/fallback/social lane.
#
# Tách pool não chính là bắt buộc. Trước đây OpenAI-compatible bị gộp vào Groq,
# khiến UI có thể ghi key Groq nhưng người dùng tưởng đó là key GPT/Terra.
_pools: dict[str, ApiKeyPool] = {}
_pools_lock = threading.Lock()


def _normalize_kind(kind: str) -> str:
    k = str(kind or "").strip().lower()
    if k in _REMOTE_MAIN_KINDS:
        return "remote"
    return "groq" if k in _GROQ_PROVIDERS else "gemini"


def _is_placeholder(value: str) -> bool:
    v = str(value or "").strip()
    return (not v) or v.upper().startswith("PASTE_")


def _split_keys_env(*names: str) -> list[str]:
    """Tách danh sách key từ env (phân tách bằng dấu phẩy / xuống dòng / ;)."""
    out: list[str] = []
    for name in names:
        raw = os.environ.get(name, "") or ""
        for chunk in raw.replace("\n", ",").replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk and not _is_placeholder(chunk):
                out.append(chunk)
    return out


def _collect_gemini_keys() -> list[str]:
    """Retained empty slot for the retired provider."""
    return []


def _collect_remote_keys() -> list[str]:
    """Gom credential dành riêng cho não chính theo đúng thứ tự ưu tiên.

    ``providers.<provider>.remote_main.api_key`` là authority mới. Các biến
    ERIS_REMOTE_* vẫn được đọc để tương thích repo cũ. Không gom key Groq hay
    Gemini phụ vào đây, tránh dùng nhầm project cho main cognition.
    """

    ordered: list[str] = []
    try:
        from eris_secrets import _load_secrets, get_provider_profile, get_secret

        provider = str(os.environ.get("ERIS_REMOTE_PROVIDER", "deepseek") or "").strip().lower()
        if provider in {"", "deepseek"}:
            provider = "deepseek"
        elif provider in {"openai", "compatible"}:
            provider = "openai_compatible"
        profile = get_provider_profile(provider, slot="remote_main")
        profile_key = str(profile.get("api_key") or "").strip()
        if not _is_placeholder(profile_key):
            ordered.append(profile_key)

        primary = get_secret("DEEPSEEK_API_KEY", "ERIS_REMOTE_API_KEY")
        if not _is_placeholder(primary):
            ordered.append(primary)

        payload = _load_secrets() or {}
        api_keys = payload.get("api_keys", {}) if isinstance(payload, dict) else {}
        if isinstance(api_keys, dict):
            fallbacks = api_keys.get("ERIS_REMOTE_FALLBACK_API_KEYS")
            if isinstance(fallbacks, list):
                ordered.extend(str(k) for k in fallbacks if not _is_placeholder(k))
    except Exception as exc:
        safe_log(_LOGGER, "debug", "Remote fallback key source unavailable: %s", exc)

    ordered.extend(_split_keys_env("ERIS_REMOTE_FALLBACK_API_KEYS"))
    unique: list[str] = []
    seen: set[str] = set()
    for key in ordered:
        clean = str(key or "").strip()
        if clean and not _is_placeholder(clean) and clean not in seen:
            seen.add(clean)
            unique.append(clean)
    return unique


def _collect_groq_keys() -> list[str]:
    """Retained empty slot for the retired provider."""
    return []


def get_key_pool(kind: str = "gemini") -> ApiKeyPool:
    """Trả singleton pool theo lane ``remote`` / ``gemini`` / ``groq``.

    ``remote_main`` luôn phân giải sang pool ``remote`` riêng, bất kể endpoint
    là DeepSeek, Gemini, Groq hay OpenAI-compatible. Nhờ vậy não chính không âm
    thầm mượn pool của parser, TTS hoặc private-mind.
    """
    k = _normalize_kind(kind)
    pool = _pools.get(k)
    if pool is not None:
        return pool
    with _pools_lock:
        if k not in _pools:
            if k == "remote":
                keys = _collect_remote_keys()
            elif k == "groq":
                keys = _collect_groq_keys()
            else:
                keys = _collect_gemini_keys()
            _pools[k] = ApiKeyPool(keys, provider=k)
        return _pools[k]


def get_shared_key_pool() -> ApiKeyPool:
    """Tương thích ngược cho main cognition: luôn trả pool não chính."""
    return get_key_pool("remote_main")


def reset_shared_key_pool() -> None:
    """Chỉ dùng cho test: xóa toàn bộ pool để build lại từ secrets."""
    with _pools_lock:
        _pools.clear()


# ── T4.3: Giới hạn đồng thời per-provider ────────────────────────────────────
# Worker song song (Phase 2 DDS) qua đây để không tự bắn vượt RPM mỗi key.
_semaphores: dict[str, threading.BoundedSemaphore] = {}
_sem_lock = threading.Lock()


def _max_concurrency(provider: str) -> int:
    kind = _normalize_kind(provider)
    specific = os.environ.get(f"ERIS_LLM_{kind.upper()}_MAX_CONCURRENCY", "").strip()
    if specific:
        raw = specific
    else:
        try:
            from runtime.runtime_settings_store import runtime_int

            value = runtime_int("chat", "max_concurrency", default=4)
            return max(1, value)
        except Exception:
            raw = os.environ.get("ERIS_LLM_MAX_CONCURRENCY", "").strip()
    try:
        value = int(raw) if raw else 4
    except ValueError:
        value = 4
    return max(1, value)


def _get_semaphore(provider: str) -> threading.BoundedSemaphore:
    kind = _normalize_kind(provider)
    sem = _semaphores.get(kind)
    if sem is not None:
        return sem
    with _sem_lock:
        if kind not in _semaphores:
            _semaphores[kind] = threading.BoundedSemaphore(_max_concurrency(kind))
        return _semaphores[kind]


@contextmanager
def concurrency_slot(provider: str = "gemini", *, timeout: float | None = None):
    """Context manager giới hạn số call đồng thời per-provider.

    Dùng trong worker runner song song. Nếu hết slot trong ``timeout`` giây thì
    vẫn chạy (best-effort, không chặn cứng) để không treo hot path.
    """
    sem = _get_semaphore(provider)
    effective_timeout = timeout if timeout is not None else 30.0
    acquired = sem.acquire(timeout=effective_timeout)
    try:
        yield acquired
    finally:
        if acquired:
            try:
                sem.release()
            except ValueError:
                pass


def reset_concurrency_limits() -> None:
    """Test/hot-reload: dựng lại semaphore theo env hiện tại."""
    with _sem_lock:
        _semaphores.clear()

# ── Provider circuit breaker ─────────────────────────────────────────────────
# 5xx là lỗi provider/model, không phải lỗi API key. Nếu cứ xoay key khi 503,
# ta chỉ biến một outage chung thành nghĩa địa key. Circuit này dùng chung theo
# provider endpoint và chặn các request nền trong một khoảng ngắn.

class ProviderCircuitOpen(RuntimeError):
    pass


class ProviderCircuitBreaker:
    def __init__(self, *, failure_threshold: int = 2, cooldown_s: float = 20.0) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_s = max(1.0, float(cooldown_s))
        self._failures = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def before_request(self, provider: str = "provider") -> None:
        now = time.monotonic()
        with self._lock:
            if self._open_until > now:
                raise ProviderCircuitOpen(
                    f"{provider} circuit đang mở; thử lại sau khoảng {self._open_until - now:.1f}s"
                )
            if self._open_until and self._open_until <= now:
                # Half-open: cho đúng một request đi qua; thành công sẽ reset.
                self._open_until = 0.0
                self._failures = max(0, self.failure_threshold - 1)

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0

    def record_failure(self, *, provider: str = "provider", status: int | None = None) -> None:
        now = time.monotonic()
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._open_until = max(self._open_until, now + self.cooldown_s)
                _LOGGER.warning(
                    "[PROVIDER CIRCUIT] open provider=%s status=%s cooldown=%.0fs failures=%d",
                    provider,
                    status if status is not None else "?",
                    self.cooldown_s,
                    self._failures,
                )

    def remaining_s(self) -> float:
        with self._lock:
            return max(0.0, self._open_until - time.monotonic())


_provider_circuits: dict[str, ProviderCircuitBreaker] = {}
_provider_circuits_lock = threading.Lock()


def get_provider_circuit(provider_id: str) -> ProviderCircuitBreaker:
    key = str(provider_id or "provider").strip().lower()
    circuit = _provider_circuits.get(key)
    if circuit is not None:
        return circuit
    with _provider_circuits_lock:
        circuit = _provider_circuits.get(key)
        if circuit is None:
            try:
                threshold = int(os.environ.get("ERIS_PROVIDER_CIRCUIT_FAILURES", "4"))
            except ValueError:
                threshold = 4
            try:
                cooldown = float(os.environ.get("ERIS_PROVIDER_CIRCUIT_COOLDOWN_S", "10"))
            except ValueError:
                cooldown = 10.0
            circuit = ProviderCircuitBreaker(failure_threshold=threshold, cooldown_s=cooldown)
            _provider_circuits[key] = circuit
        return circuit

def reset_provider_circuits() -> None:
    with _provider_circuits_lock:
        _provider_circuits.clear()


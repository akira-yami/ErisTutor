# -*- coding: utf-8 -*-
from __future__ import annotations
"""elevenlabs_pool_db.py — Quản lý SQLite Database & Token Pool cho ElevenLabs.

Hệ thống lưu trữ danh sách ElevenLabs Refresh Tokens, tự động đổi Firebase Token,
đồng bộ số ký tự đã dùng / hạn mức tổng và thời gian reset quota định kỳ.
"""

from contextlib import contextmanager
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_LOGGER = logging.getLogger("ElevenPool")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Tài khoản còn dưới ngưỡng này bị loại khỏi vòng xoay.
# ElevenLabs gọi đơn vị này là credits/characters tùy endpoint; runtime
# chỉ cần một luật duy nhất để không cố dùng account gần cạn.
ELEVENLABS_MIN_REMAINING_QUOTA = 100

# Ngưỡng "sắp cạn": account dưới mức này sẽ được auto-sync để kiểm tra quota thực.
# Phòng trường hợp user dùng ElevenLabs từ web/app khác làm lệch character_count nội bộ.
ELEVENLABS_LOW_QUOTA_SYNC_THRESHOLD = 500

# Timeout connect/read (giây) cho request refresh token + sync subscription.
# Connect ngắn để nếu một địa chỉ chết (vd IPv6 không route được) thì bỏ nhanh,
# không ngồi chờ ~12s mỗi địa chỉ. Read dài hơn vì API có thể trả chậm.
_ELEVEN_HTTP_CONNECT_TIMEOUT = float(os.environ.get("ERIS_ELEVENLABS_SYNC_CONNECT_TIMEOUT_S", "4") or "4")
_ELEVEN_HTTP_READ_TIMEOUT = float(os.environ.get("ERIS_ELEVENLABS_SYNC_READ_TIMEOUT_S", "12") or "12")

# Cho phép tắt ưu tiên IPv4 nếu cần (mặc định bật).
_ELEVEN_PREFER_IPV4 = str(os.environ.get("ERIS_ELEVENLABS_PREFER_IPV4", "1") or "1").strip().lower() not in {
    "0", "false", "off", "no",
}

_HTTP_SESSION: Optional[Any] = None
_HTTP_SESSION_LOCK = threading.Lock()


def _build_ipv4_adapter():
    """HTTPAdapter ép phân giải IPv4 trước.

    `requests`/`urllib3` thử các địa chỉ DNS TUẦN TỰ (không Happy Eyeballs như
    trình duyệt). Nếu host trả IPv6 trước mà mạng không ra được IPv6, mỗi địa
    chỉ phải chờ hết connect-timeout rồi mới sang địa chỉ kế — cộng dồn thành
    hàng chục giây. Ta ép getaddrinfo chỉ trả IPv4 cho các connection của
    adapter này để tránh hình phạt đó.
    """
    import socket
    from requests.adapters import HTTPAdapter
    from urllib3.util import connection as urllib3_connection

    _orig_create_connection = urllib3_connection.create_connection

    def _ipv4_create_connection(address, *args, **kwargs):
        host, port = address
        # Ép family AF_INET: getaddrinfo chỉ trả IPv4, bỏ qua IPv6.
        kwargs = dict(kwargs)
        # urllib3 create_connection không nhận socket_options qua kwargs ở mọi
        # phiên bản; cách an toàn nhất là tạm vá getaddrinfo cho riêng lời gọi.
        _orig_getaddrinfo = socket.getaddrinfo

        def _ipv4_getaddrinfo(a_host, a_port, family=0, *a, **kw):
            return _orig_getaddrinfo(a_host, a_port, socket.AF_INET, *a, **kw)

        socket.getaddrinfo = _ipv4_getaddrinfo
        try:
            return _orig_create_connection(address, *args, **kwargs)
        finally:
            socket.getaddrinfo = _orig_getaddrinfo

    class _IPv4FirstAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            return super().init_poolmanager(*args, **kwargs)

        def send(self, *args, **kwargs):
            _prev = urllib3_connection.create_connection
            urllib3_connection.create_connection = _ipv4_create_connection
            try:
                return super().send(*args, **kwargs)
            finally:
                urllib3_connection.create_connection = _prev

    return _IPv4FirstAdapter(pool_connections=5, pool_maxsize=10, max_retries=0)


def _get_http_session():
    """Session dùng chung cho refresh/sync; ép IPv4-first nếu bật."""
    global _HTTP_SESSION
    if _HTTP_SESSION is not None:
        return _HTTP_SESSION
    with _HTTP_SESSION_LOCK:
        if _HTTP_SESSION is not None:
            return _HTTP_SESSION
        import requests
        session = requests.Session()
        if _ELEVEN_PREFER_IPV4:
            try:
                adapter = _build_ipv4_adapter()
                session.mount("https://", adapter)
                session.mount("http://", adapter)
            except Exception as exc:  # không chặn nếu môi trường thiếu urllib3 nội bộ
                _LOGGER.warning("[ElevenPool] Không mount IPv4-first adapter được: %s", exc)
        _HTTP_SESSION = session
        return _HTTP_SESSION


def _resolve_db_path() -> Path:
    env_root = os.environ.get("ERIS_PROJECT_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root) / "config" / "elevenlabs_pool.db"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    target = _PROJECT_ROOT / "config" / "elevenlabs_pool.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@dataclass
class ElevenAccount:
    id: int
    label: str
    refresh_token: str
    bearer_token: str
    bearer_expires_at: float
    character_count: int
    character_limit: int
    status: str  # active | cooling | exhausted | disabled
    created_at: float
    cooldown_until: float
    reset_at: float
    last_used_at: float
    notes: str

    @property
    def remaining_characters(self) -> int:
        return max(0, self.character_limit - self.character_count)

    @property
    def usage_percent(self) -> float:
        if self.character_limit <= 0:
            return 100.0
        return min(100.0, round((self.character_count / self.character_limit) * 100.0, 1))

    def to_dict(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "id": self.id,
            "label": self.label,
            "refresh_token_masked": self.refresh_token[:8] + "..." + self.refresh_token[-6:] if len(self.refresh_token) > 16 else self.refresh_token,
            "has_bearer": bool(self.bearer_token and now < self.bearer_expires_at),
            "character_count": self.character_count,
            "character_limit": self.character_limit,
            "remaining_characters": self.remaining_characters,
            "usage_percent": self.usage_percent,
            "status": self.status,
            "created_at": self.created_at,
            "created_at_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created_at)),
            "cooldown_until": self.cooldown_until,
            "cooldown_remaining_s": max(0, int(self.cooldown_until - now)),
            "reset_at": self.reset_at,
            "reset_at_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.reset_at)) if self.reset_at > 0 else "Chưa xác định",
            "reset_remaining_s": max(0, int(self.reset_at - now)) if self.reset_at > 0 else 0,
            "last_used_at": self.last_used_at,
            "last_used_at_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_used_at)) if self.last_used_at > 0 else "Chưa từng dùng",
            "notes": self.notes,
        }


class ElevenPoolDB:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _resolve_db_path()
        self._lock = threading.Lock()
        self._init_db()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock:
            with self._connection() as conn:
                conn.execute("""
                CREATE TABLE IF NOT EXISTS elevenlabs_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    refresh_token TEXT UNIQUE NOT NULL,
                    bearer_token TEXT DEFAULT '',
                    bearer_expires_at REAL DEFAULT 0.0,
                    character_count INTEGER DEFAULT 0,
                    character_limit INTEGER DEFAULT 10000,
                    status TEXT DEFAULT 'active',
                    created_at REAL NOT NULL,
                    cooldown_until REAL DEFAULT 0.0,
                    reset_at REAL DEFAULT 0.0,
                    last_used_at REAL DEFAULT 0.0,
                    notes TEXT DEFAULT ''
                );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_eleven_status ON elevenlabs_accounts(status);")
                conn.commit()

    def _row_to_account(self, row: sqlite3.Row) -> ElevenAccount:
        return ElevenAccount(
            id=row["id"],
            label=row["label"],
            refresh_token=row["refresh_token"],
            bearer_token=row["bearer_token"] or "",
            bearer_expires_at=float(row["bearer_expires_at"] or 0.0),
            character_count=int(row["character_count"] or 0),
            character_limit=int(row["character_limit"] or 10000),
            status=str(row["status"] or "active"),
            created_at=float(row["created_at"] or time.time()),
            cooldown_until=float(row["cooldown_until"] or 0.0),
            reset_at=float(row["reset_at"] or 0.0),
            last_used_at=float(row["last_used_at"] or 0.0),
            notes=str(row["notes"] or ""),
        )

    def add_account(
        self,
        refresh_token: str,
        label: str = "",
        notes: str = "",
        auto_sync: bool = True,
    ) -> Tuple[bool, str, Optional[ElevenAccount]]:
        token = str(refresh_token or "").strip()
        if not token:
            return False, "Refresh Token không được để trống", None

        now = time.time()
        lbl = str(label or "").strip()
        if not lbl:
            lbl = f"Acc-{token[:6]}"

        with self._lock:
            try:
                with self._connection() as conn:
                    cur = conn.execute(
                        """
                        INSERT INTO elevenlabs_accounts (label, refresh_token, created_at, notes, status)
                        VALUES (?, ?, ?, ?, 'active')
                        """,
                        (lbl, token, now, str(notes or "").strip()),
                    )
                    conn.commit()
                    acc_id = cur.lastrowid
            except sqlite3.IntegrityError:
                return False, "Refresh Token này đã tồn tại trong danh sách", None
            except Exception as exc:
                return False, f"Lỗi cơ sở dữ liệu: {exc}", None

        acc = self.get_account_by_id(acc_id)
        if auto_sync and acc:
            self.sync_subscription(acc.id)
            acc = self.get_account_by_id(acc_id)

        return True, "Đã thêm tài khoản mới thành công", acc

    def rename_account(self, account_id: int, new_label: str) -> Tuple[bool, str]:
        lbl = str(new_label or "").strip()
        if not lbl:
            return False, "Tên giọng không được để trống"
        with self._lock:
            with self._connection() as conn:
                conn.execute("UPDATE elevenlabs_accounts SET label = ? WHERE id = ?", (lbl, account_id))
                conn.commit()
                return True, f"Đã đổi tên thành '{lbl}'"

    def upsert_account(
        self,
        refresh_token: str,
        label: str = "",
        notes: str = "",
        auto_sync: bool = True,
    ) -> Tuple[bool, str, Optional[ElevenAccount]]:
        token = str(refresh_token or "").strip()
        if not token:
            return False, "Refresh Token không được để trống", None

        now = time.time()
        lbl = str(label or "").strip()
        if not lbl:
            lbl = f"Acc-{token[:6]}"

        acc_id = None
        is_update = False
        with self._lock:
            try:
                with self._connection() as conn:
                    # Check if exists by refresh_token
                    existing = conn.execute(
                        "SELECT id, label FROM elevenlabs_accounts WHERE refresh_token = ?", (token,)
                    ).fetchone()
                    if existing:
                        acc_id = existing["id"]
                        is_update = True
                        update_lbl = lbl if label.strip() else existing["label"]
                        conn.execute(
                            """
                            UPDATE elevenlabs_accounts
                            SET label = ?, notes = COALESCE(NULLIF(?, ''), notes), status = 'active'
                            WHERE id = ?
                            """,
                            (update_lbl, str(notes or "").strip(), acc_id),
                        )
                        conn.commit()
                    else:
                        # Check if exists by exact label
                        existing_lbl = conn.execute(
                            "SELECT id FROM elevenlabs_accounts WHERE label = ?", (lbl,)
                        ).fetchone() if label.strip() else None

                        if existing_lbl:
                            acc_id = existing_lbl["id"]
                            is_update = True
                            conn.execute(
                                """
                                UPDATE elevenlabs_accounts
                                SET refresh_token = ?, notes = COALESCE(NULLIF(?, ''), notes), status = 'active', bearer_token = '', bearer_expires_at = 0.0
                                WHERE id = ?
                                """,
                                (token, str(notes or "").strip(), acc_id),
                            )
                            conn.commit()
                        else:
                            cur = conn.execute(
                                """
                                INSERT INTO elevenlabs_accounts (label, refresh_token, created_at, notes, status)
                                VALUES (?, ?, ?, ?, 'active')
                                """,
                                (lbl, token, now, str(notes or "").strip()),
                            )
                            conn.commit()
                            acc_id = cur.lastrowid
            except Exception as exc:
                return False, f"Lỗi cơ sở dữ liệu: {exc}", None

        acc = self.get_account_by_id(acc_id)
        if auto_sync and acc:
            self.sync_subscription(acc.id)
            acc = self.get_account_by_id(acc_id)

        msg = f"Đã cập nhật token cho '{acc.label}'" if (acc and is_update) else (f"Đã thêm giọng '{acc.label}' thành công" if acc else "Thành công")
        return True, msg, acc


    def get_account_by_id(self, account_id: int) -> Optional[ElevenAccount]:
        with self._lock:
            with self._connection() as conn:
                row = conn.execute("SELECT * FROM elevenlabs_accounts WHERE id = ?", (account_id,)).fetchone()
                return self._row_to_account(row) if row else None

    def has_accounts(self) -> bool:
        """Cho biết pool có credential được cấu hình hay chưa."""
        with self._lock:
            with self._connection() as conn:
                row = conn.execute("SELECT 1 FROM elevenlabs_accounts LIMIT 1").fetchone()
                return row is not None

    def list_accounts(self) -> List[ElevenAccount]:
        now = time.time()
        # Auto-heal: reset exhausted accounts whose reset_at has passed
        self._check_auto_resets(now)
        with self._lock:
            with self._connection() as conn:
                rows = conn.execute("SELECT * FROM elevenlabs_accounts ORDER BY id ASC").fetchall()
                return [self._row_to_account(row) for row in rows]

    def _check_auto_resets(self, now: float) -> None:
        with self._lock:
            with self._connection() as conn:
                # If reset_at is set and passed, reset character count to 0 and set status to active
                conn.execute(
                    """
                    UPDATE elevenlabs_accounts
                    SET character_count = 0, status = 'active', cooldown_until = 0.0
                    WHERE status IN ('exhausted', 'cooling') AND reset_at > 0 AND reset_at <= ?
                    """,
                    (now,),
                )
                # If cooldown_until passed and not exhausted
                conn.execute(
                    """
                    UPDATE elevenlabs_accounts
                    SET status = 'active'
                    WHERE status = 'cooling' AND cooldown_until <= ? AND (reset_at == 0 OR reset_at > ?)
                    """,
                    (now, now),
                )
                conn.commit()

    def get_active_account(
        self,
        *,
        required_characters: int = 0,
        exclude_account_ids: Optional[set[int]] = None,
    ) -> Optional[ElevenAccount]:
        """Lấy account đủ quota cho chính request hiện tại.

        Luật cố ý cứng: account còn dưới 100 quota bị loại hẳn, kể cả request
        rất ngắn. ``required_characters`` còn bảo đảm một đoạn dài không bị giao
        cho account chỉ vừa đủ vượt ngưỡng rồi chết giữa request.
        """
        now = time.time()
        self._check_auto_resets(now)
        excluded = {int(item) for item in (exclude_account_ids or set()) if int(item) > 0}
        required = max(ELEVENLABS_MIN_REMAINING_QUOTA, int(required_characters or 0))

        with self._lock:
            with self._connection() as conn:
                # Dọn account gần cạn trước khi chọn. Không để một account còn
                # 52 credits cứ được "thử vận may" ở mọi lượt TTS.
                conn.execute(
                    """
                    UPDATE elevenlabs_accounts
                    SET status = 'exhausted'
                    WHERE status = 'active'
                      AND character_limit > 0
                      AND (character_limit - character_count) < ?
                    """,
                    (ELEVENLABS_MIN_REMAINING_QUOTA,),
                )
                rows = conn.execute(
                    """
                    SELECT * FROM elevenlabs_accounts
                    WHERE status = 'active'
                      AND cooldown_until <= ?
                      AND (
                            character_limit == 0
                            OR (character_limit - character_count) >= ?
                      )
                    ORDER BY
                        CASE WHEN character_limit == 0 THEN 1 ELSE 0 END ASC,
                        (character_limit - character_count) DESC,
                        last_used_at ASC
                    """,
                    (now, required),
                ).fetchall()
                conn.commit()

                for row in rows:
                    account = self._row_to_account(row)
                    if account.id not in excluded:
                        return account
                return None

    def update_bearer_token(self, account_id: int, bearer_token: str, expires_in_seconds: float = 3000.0) -> None:
        now = time.time()
        expires_at = now + expires_in_seconds
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE elevenlabs_accounts SET bearer_token = ?, bearer_expires_at = ? WHERE id = ?",
                    (bearer_token, expires_at, account_id),
                )
                conn.commit()

    def record_usage(self, account_id: int, char_count: int) -> None:
        now = time.time()
        with self._lock:
            with self._connection() as conn:
                row = conn.execute("SELECT character_count, character_limit FROM elevenlabs_accounts WHERE id = ?", (account_id,)).fetchone()
                if row:
                    new_count = int(row["character_count"] or 0) + max(0, char_count)
                    limit = int(row["character_limit"] or 10000)
                    remaining = max(0, limit - new_count) if limit > 0 else ELEVENLABS_MIN_REMAINING_QUOTA
                    new_status = (
                        "exhausted"
                        if limit > 0 and remaining < ELEVENLABS_MIN_REMAINING_QUOTA
                        else "active"
                    )
                    conn.execute(
                        """
                        UPDATE elevenlabs_accounts
                        SET character_count = ?, last_used_at = ?, status = CASE WHEN status = 'disabled' THEN 'disabled' ELSE ? END
                        WHERE id = ?
                        """,
                        (new_count, now, new_status, account_id),
                    )
                    conn.commit()

    def mark_exhausted(self, account_id: int, cooldown_seconds: float = 86400.0, reset_timestamp: float = 0.0) -> None:
        now = time.time()
        cooldown_until = now + max(60.0, cooldown_seconds)
        with self._lock:
            with self._connection() as conn:
                if reset_timestamp > 0:
                    conn.execute(
                        "UPDATE elevenlabs_accounts SET status = 'exhausted', cooldown_until = ?, reset_at = ? WHERE id = ?",
                        (cooldown_until, reset_timestamp, account_id),
                    )
                else:
                    conn.execute(
                        "UPDATE elevenlabs_accounts SET status = 'exhausted', cooldown_until = ? WHERE id = ?",
                        (cooldown_until, account_id),
                    )
                conn.commit()

    def mark_cooling(self, account_id: int, seconds: float = 300.0) -> None:
        now = time.time()
        cooldown_until = now + max(10.0, seconds)
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    "UPDATE elevenlabs_accounts SET status = 'cooling', cooldown_until = ? WHERE id = ? AND status != 'disabled'",
                    (cooldown_until, account_id),
                )
                conn.commit()

    def toggle_account(self, account_id: int, enabled: Optional[bool] = None) -> Tuple[bool, str]:
        with self._lock:
            with self._connection() as conn:
                row = conn.execute("SELECT status FROM elevenlabs_accounts WHERE id = ?", (account_id,)).fetchone()
                if not row:
                    return False, "Không tìm thấy tài khoản"
                cur_status = row["status"]
                if enabled is None:
                    new_status = "active" if cur_status == "disabled" else "disabled"
                else:
                    new_status = "active" if enabled else "disabled"
                conn.execute("UPDATE elevenlabs_accounts SET status = ? WHERE id = ?", (new_status, account_id))
                conn.commit()
                msg = "Đã bật tài khoản" if new_status == "active" else "Đã tắt tài khoản"
                return True, msg

    def delete_account(self, account_id: int) -> Tuple[bool, str]:
        with self._lock:
            with self._connection() as conn:
                cur = conn.execute("DELETE FROM elevenlabs_accounts WHERE id = ?", (account_id,))
                conn.commit()
                if cur.rowcount > 0:
                    return True, "Đã xóa tài khoản thành công"
                return False, "Không tìm thấy tài khoản để xóa"

    def sync_subscription(self, account_id: int) -> Tuple[bool, str]:
        """Đổi Firebase Bearer token và đồng bộ quota từ ElevenLabs API."""
        acc = self.get_account_by_id(account_id)
        if not acc:
            return False, "Không tìm thấy tài khoản"

        from eris_secrets import get_secret

        session = _get_http_session()
        _timeout = (_ELEVEN_HTTP_CONNECT_TIMEOUT, _ELEVEN_HTTP_READ_TIMEOUT)

        firebase_key = str(get_secret("FIREBASE_API_KEY") or "").strip()
        if not firebase_key:
            return False, "Thiếu FIREBASE_API_KEY trong secrets.json/env"

        # 1. Exchange Refresh Token -> Firebase id_token
        url = f"https://securetoken.googleapis.com/v1/token?key={firebase_key}"
        headers = {
            "Referer": "https://elevenlabs.io/",
            "Origin": "https://elevenlabs.io",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {"grant_type": "refresh_token", "refresh_token": acc.refresh_token}

        try:
            r = session.post(url, headers=headers, data=data, timeout=_timeout)
            if r.status_code != 200:
                err_msg = r.json().get("error", {}).get("message", r.text)
                self.mark_cooling(account_id, 600)
                return False, f"Lỗi đổi token từ Firebase [{r.status_code}]: {err_msg}"

            bearer_token = str(r.json().get("id_token") or "").strip()
            if not bearer_token:
                return False, "Firebase không trả về id_token"

            self.update_bearer_token(account_id, bearer_token, 3000.0)
        except Exception as exc:
            return False, f"Lỗi kết nối Firebase: {exc}"

        # 2. Fetch subscription details from ElevenLabs API
        sub_url = "https://api.elevenlabs.io/v1/user/subscription"
        sub_headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Referer": "https://elevenlabs.io/",
            "Origin": "https://elevenlabs.io",
            "User-Agent": "Mozilla/5.0",
        }

        try:
            r2 = session.get(sub_url, headers=sub_headers, timeout=_timeout)
            if r2.status_code == 200:
                info = r2.json()
                char_count = int(info.get("character_count", 0))
                char_limit = int(info.get("character_limit", 10000))
                reset_unix = float(info.get("next_character_count_reset_unix", 0) or 0.0)

                new_status = "active"
                remaining = max(0, char_limit - char_count) if char_limit > 0 else ELEVENLABS_MIN_REMAINING_QUOTA
                if char_limit > 0 and remaining < ELEVENLABS_MIN_REMAINING_QUOTA:
                    new_status = "exhausted"

                with self._lock:
                    with self._connection() as conn:
                        conn.execute(
                            """
                            UPDATE elevenlabs_accounts
                            SET character_count = ?, character_limit = ?, reset_at = ?, status = CASE WHEN status = 'disabled' THEN 'disabled' ELSE ? END
                            WHERE id = ?
                            """,
                            (char_count, char_limit, reset_unix, new_status, account_id),
                        )
                        conn.commit()

                return True, f"Đồng bộ thành công: {char_count}/{char_limit} ký tự"
            else:
                return False, f"ElevenLabs API trả về mã [{r2.status_code}]: {r2.text[:100]}"
        except Exception as exc:
            return False, f"Lỗi gọi ElevenLabs API: {exc}"

    def get_summary_stats(self) -> Dict[str, Any]:
        accounts = self.list_accounts()
        total_accounts = len(accounts)
        active_accounts = sum(1 for a in accounts if a.status == "active")
        exhausted_accounts = sum(1 for a in accounts if a.status == "exhausted")
        disabled_accounts = sum(1 for a in accounts if a.status == "disabled")

        total_characters_limit = sum(a.character_limit for a in accounts if a.status != "disabled")
        total_characters_used = sum(a.character_count for a in accounts if a.status != "disabled")
        total_characters_remaining = max(0, total_characters_limit - total_characters_used)

        return {
            "total_accounts": total_accounts,
            "active_accounts": active_accounts,
            "exhausted_accounts": exhausted_accounts,
            "disabled_accounts": disabled_accounts,
            "total_characters_limit": total_characters_limit,
            "total_characters_used": total_characters_used,
            "total_characters_remaining": total_characters_remaining,
        }
    def start_background_sync(
        self,
        *,
        interval_seconds: float = 1800.0,
        low_quota_threshold: int | None = None,
    ) -> threading.Thread:
        """Chạy background thread tự động sync quota cho các account sắp cạn.

        Mục tiêu: phát hiện sớm account đã cạn quota do user dùng từ web/app
        khác (làm lệch ``character_count`` nội bộ) trước khi TTS thật sự thất
        bại và gây mất tiếng một lượt.

        Args:
            interval_seconds: Khoảng thời gian giữa hai lần quét (mặc định 30 phút).
            low_quota_threshold: Account còn dưới ngưỡng này sẽ được sync ngay.
                Mặc định đọc từ ``ERIS_ELEVEN_LOW_QUOTA_SYNC_THRESHOLD`` env hoặc
                hằng số ``ELEVENLABS_LOW_QUOTA_SYNC_THRESHOLD`` (500).
        """

        try:
            env_threshold = int(
                os.environ.get("ERIS_ELEVEN_LOW_QUOTA_SYNC_THRESHOLD", "").strip() or 0
            )
        except (ValueError, TypeError):
            env_threshold = 0
        threshold = (
            low_quota_threshold
            if low_quota_threshold is not None
            else (env_threshold if env_threshold > 0 else ELEVENLABS_LOW_QUOTA_SYNC_THRESHOLD)
        )

        try:
            env_interval = float(
                os.environ.get("ERIS_ELEVEN_AUTO_SYNC_INTERVAL_S", "").strip() or 0
            )
        except (ValueError, TypeError):
            env_interval = 0.0
        effective_interval = max(60.0, env_interval if env_interval > 0 else interval_seconds)

        def _run_sync_loop():
            _LOGGER.info(
                "[ElevenPool] Background auto-sync khởi động: interval=%.0fs low_quota_threshold=%d",
                effective_interval,
                threshold,
            )
            while True:
                time.sleep(effective_interval)
                try:
                    self._run_one_auto_sync(threshold)
                except Exception as exc:
                    _LOGGER.debug("[ElevenPool] Auto-sync lỗi không ngờ: %s", exc)

        thread = threading.Thread(
            target=_run_sync_loop,
            daemon=True,
            name="ElevenPool-AutoSync",
        )
        thread.start()
        return thread

    def _run_one_auto_sync(self, low_quota_threshold: int) -> None:
        """Một lượt quét: sync mọi account active có remaining < threshold.

        Không sync disabled/exhausted (vô nghĩa và tốn quota Firebase).
        Sync lặp lại cùng account trong cùng interval nếu còn dưới ngưỡng sau
        sync trước không được phòng tránh ở đây — interval đủ dài để không spam.
        """
        now = time.time()
        self._check_auto_resets(now)
        with self._lock:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM elevenlabs_accounts
                    WHERE status = 'active'
                      AND character_limit > 0
                      AND (character_limit - character_count) < ?
                    ORDER BY (character_limit - character_count) ASC
                    """,
                    (low_quota_threshold,),
                ).fetchall()
        candidates = [self._row_to_account(row) for row in rows]
        if not candidates:
            return
        _LOGGER.info(
            "[ElevenPool] Auto-sync %d account(s) sắp cạn quota (remaining < %d ký tự)...",
            len(candidates),
            low_quota_threshold,
        )
        for acc in candidates:
            try:
                ok, msg = self.sync_subscription(acc.id)
                if ok:
                    refreshed = self.get_account_by_id(acc.id)
                    if refreshed and refreshed.remaining_characters < ELEVENLABS_MIN_REMAINING_QUOTA:
                        _LOGGER.warning(
                            "[ElevenPool] Account %d (%s) đã cạn quota thực tế (%d ký tự còn lại); đánh dấu exhausted.",
                            acc.id,
                            acc.label,
                            refreshed.remaining_characters,
                        )
                    else:
                        _LOGGER.info(
                            "[ElevenPool] Account %d (%s) sync OK: %d ký tự còn lại.",
                            acc.id,
                            acc.label,
                            refreshed.remaining_characters if refreshed else -1,
                        )
                else:
                    _LOGGER.warning("[ElevenPool] Account %d (%s) sync thất bại: %s", acc.id, acc.label, msg)
            except Exception as exc:
                _LOGGER.debug("[ElevenPool] Sync account %d lỗi: %s", acc.id, exc)


# Singleton Pool DB Instance
GLOBAL_ELEVEN_POOL = ElevenPoolDB()
ElevenLabsPoolDB = ElevenPoolDB

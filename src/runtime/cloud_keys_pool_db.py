# -*- coding: utf-8 -*-
from __future__ import annotations
"""cloud_keys_pool_db.py — Quản lý SQLite Database & Key Pool xoay vòng cho Gemma / Gemini & Groq.

Cung cấp:
- Lưu trữ persistent danh sách Cloud API Keys trong SQLite (config/cloud_keys_pool.db).
- Xoay vòng thông minh (Least Recently Used / Round Robin).
- Cơ chế Cooldown tự động khi dính Rate Limit (HTTP 429).
- Xuất dữ liệu ra file JSONL (config/cloud_keys.jsonl) cho worker/caller tiêu thụ.
- Đồng bộ 2 chiều với config/secrets.json để tương thích ngược 100%.
"""

from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

_LOGGER = logging.getLogger("CloudKeysPool")
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_db_path() -> Path:
    target = _PROJECT_ROOT / "config" / "cloud_keys_pool.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@dataclass
class CloudKey:
    id: int
    provider: str  # 'gemini' (Google AI Studio / Gemma) | 'groq' (Groq Cloud)
    label: str
    api_key: str
    status: str  # 'active' | 'cooling' | 'exhausted' | 'disabled'
    latency_ms: int
    models_supported: str
    usage_count: int
    error_count: int
    cooldown_until: float
    last_used_at: float
    created_at: float
    notes: str

    @property
    def is_cooling(self) -> bool:
        return self.status == "cooling" and time.time() < self.cooldown_until

    @property
    def is_available(self) -> bool:
        if self.status != "active":
            if self.status == "cooling" and time.time() >= self.cooldown_until:
                return True
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        now = time.time()
        cooldown_rem = max(0, int(self.cooldown_until - now)) if self.cooldown_until > now else 0
        current_status = self.status
        if self.status == "cooling" and cooldown_rem == 0:
            current_status = "active"

        masked = ""
        if self.api_key:
            if len(self.api_key) > 12:
                masked = f"{self.api_key[:6]}...{self.api_key[-4:]}"
            else:
                masked = f"{self.api_key[:3]}..."

        models_list = [m.strip() for m in self.models_supported.split(",") if m.strip()]

        return {
            "id": self.id,
            "provider": self.provider,
            "label": self.label or f"{self.provider.capitalize()} Key #{self.id}",
            "api_key_masked": masked,
            "status": current_status,
            "is_available": self.is_available,
            "latency_ms": self.latency_ms,
            "models_supported": models_list,
            "usage_count": self.usage_count,
            "error_count": self.error_count,
            "cooldown_until": self.cooldown_until,
            "cooldown_remaining_s": cooldown_rem,
            "last_used_at": self.last_used_at,
            "last_used_at_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.last_used_at)) if self.last_used_at > 0 else "Chưa dùng",
            "created_at": self.created_at,
            "created_at_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created_at)),
            "notes": self.notes,
        }


class CloudKeysPoolDB:
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
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cloud_api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    label TEXT,
                    api_key TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    latency_ms INTEGER DEFAULT 0,
                    models_supported TEXT DEFAULT '',
                    usage_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    cooldown_until REAL DEFAULT 0.0,
                    last_used_at REAL DEFAULT 0.0,
                    created_at REAL DEFAULT 0.0,
                    notes TEXT DEFAULT ''
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cloud_provider_status ON cloud_api_keys(provider, status);")
            conn.commit()

    def list_keys(self, provider: Optional[str] = None) -> List[CloudKey]:
        with self._connection() as conn:
            if provider:
                rows = conn.execute(
                    "SELECT * FROM cloud_api_keys WHERE provider = ? ORDER BY id ASC",
                    (provider.strip().lower(),)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM cloud_api_keys ORDER BY provider ASC, id ASC").fetchall()

        keys: List[CloudKey] = []
        now = time.time()
        for r in rows:
            k = CloudKey(
                id=r["id"],
                provider=r["provider"],
                label=r["label"] or "",
                api_key=r["api_key"],
                status=r["status"],
                latency_ms=r["latency_ms"] or 0,
                models_supported=r["models_supported"] or "",
                usage_count=r["usage_count"] or 0,
                error_count=r["error_count"] or 0,
                cooldown_until=r["cooldown_until"] or 0.0,
                last_used_at=r["last_used_at"] or 0.0,
                created_at=r["created_at"] or 0.0,
                notes=r["notes"] or "",
            )
            # Tự động gỡ cooling nếu đã hết thời gian cooldown
            if k.status == "cooling" and k.cooldown_until <= now:
                k.status = "active"
                self.update_key_status(k.id, "active")
            keys.append(k)
        return keys

    def get_key_by_id(self, key_id: int) -> Optional[CloudKey]:
        with self._connection() as conn:
            r = conn.execute("SELECT * FROM cloud_api_keys WHERE id = ?", (key_id,)).fetchone()
            if not r:
                return None
            return CloudKey(
                id=r["id"],
                provider=r["provider"],
                label=r["label"] or "",
                api_key=r["api_key"],
                status=r["status"],
                latency_ms=r["latency_ms"] or 0,
                models_supported=r["models_supported"] or "",
                usage_count=r["usage_count"] or 0,
                error_count=r["error_count"] or 0,
                cooldown_until=r["cooldown_until"] or 0.0,
                last_used_at=r["last_used_at"] or 0.0,
                created_at=r["created_at"] or 0.0,
                notes=r["notes"] or "",
            )

    def add_key(self, provider: str, api_key: str, label: str = "", notes: str = "") -> int:
        clean_key = api_key.strip()
        if not clean_key:
            raise ValueError("API Key không được để trống")
        clean_prov = provider.strip().lower()
        now = time.time()
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO cloud_api_keys (provider, label, api_key, status, created_at, notes)
                VALUES (?, ?, ?, 'active', ?, ?)
                ON CONFLICT(api_key) DO UPDATE SET
                    label = COALESCE(NULLIF(excluded.label, ''), cloud_api_keys.label),
                    status = 'active',
                    notes = COALESCE(NULLIF(excluded.notes, ''), cloud_api_keys.notes)
                RETURNING id;
                """,
                (clean_prov, label.strip(), clean_key, now, notes.strip())
            )
            row = cur.fetchone()
            conn.commit()
            return row["id"] if row else -1

    def batch_import(self, provider: str, raw_text: str, label_prefix: str = "") -> Dict[str, Any]:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        clean_prov = provider.strip().lower()
        added = 0
        updated = 0
        now = time.time()
        with self._lock, self._connection() as conn:
            for idx, key in enumerate(lines, 1):
                lbl = f"{label_prefix} #{idx}" if label_prefix else f"{clean_prov.capitalize()} #{idx}"
                cur = conn.execute("SELECT id FROM cloud_api_keys WHERE api_key = ?", (key,))
                exists = cur.fetchone()
                if exists:
                    conn.execute("UPDATE cloud_api_keys SET status = 'active' WHERE id = ?", (exists["id"],))
                    updated += 1
                else:
                    conn.execute(
                        """
                        INSERT INTO cloud_api_keys (provider, label, api_key, status, created_at)
                        VALUES (?, ?, ?, 'active', ?)
                        """,
                        (clean_prov, lbl, key, now)
                    )
                    added += 1
            conn.commit()
        return {"total_lines": len(lines), "added": added, "updated": updated}

    def update_key_status(self, key_id: int, status: str, cooldown_seconds: float = 0.0) -> bool:
        now = time.time()
        cooldown_until = (now + cooldown_seconds) if cooldown_seconds > 0 else 0.0
        with self._lock, self._connection() as conn:
            cur = conn.execute(
                "UPDATE cloud_api_keys SET status = ?, cooldown_until = ? WHERE id = ?",
                (status.strip().lower(), cooldown_until, key_id)
            )
            conn.commit()
            return cur.rowcount > 0

    def update_key_metrics(
        self,
        key_id: int,
        latency_ms: Optional[int] = None,
        models_supported: Optional[str] = None,
        is_error: bool = False,
    ) -> bool:
        with self._lock, self._connection() as conn:
            updates = []
            params = []
            if latency_ms is not None:
                updates.append("latency_ms = ?")
                params.append(latency_ms)
            if models_supported is not None:
                updates.append("models_supported = ?")
                params.append(models_supported)
            if is_error:
                updates.append("error_count = error_count + 1")
            if not updates:
                return False
            params.append(key_id)
            query = f"UPDATE cloud_api_keys SET {', '.join(updates)} WHERE id = ?"
            cur = conn.execute(query, tuple(params))
            conn.commit()
            return cur.rowcount > 0

    def delete_key(self, key_id: int) -> bool:
        with self._lock, self._connection() as conn:
            cur = conn.execute("DELETE FROM cloud_api_keys WHERE id = ?", (key_id,))
            conn.commit()
            return cur.rowcount > 0

    def get_next_key(self, provider: str) -> Optional[CloudKey]:
        """Lấy key tốt nhất tiếp theo theo thuật toán Least Recently Used (LRU).
        
        - Key có status = 'active'
        - Không nằm trong thời gian cooldown
        - Ưu tiên: ít lỗi nhất, dùng xa nhất trong quá khứ (last_used_at ASC)
        """
        now = time.time()
        clean_prov = provider.strip().lower()
        with self._lock, self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM cloud_api_keys
                WHERE provider = ?
                  AND status != 'disabled'
                  AND status != 'exhausted'
                  AND (status != 'cooling' OR cooldown_until <= ?)
                ORDER BY (error_count > 5) ASC, last_used_at ASC, latency_ms ASC
                LIMIT 1;
                """,
                (clean_prov, now)
            ).fetchone()

            if not row:
                return None

            key_id = row["id"]
            conn.execute(
                """
                UPDATE cloud_api_keys
                SET status = 'active',
                    last_used_at = ?,
                    usage_count = usage_count + 1
                WHERE id = ?;
                """,
                (now, key_id)
            )
            conn.commit()

            return CloudKey(
                id=row["id"],
                provider=row["provider"],
                label=row["label"] or "",
                api_key=row["api_key"],
                status="active",
                latency_ms=row["latency_ms"] or 0,
                models_supported=row["models_supported"] or "",
                usage_count=(row["usage_count"] or 0) + 1,
                error_count=row["error_count"] or 0,
                cooldown_until=row["cooldown_until"] or 0.0,
                last_used_at=now,
                created_at=row["created_at"] or 0.0,
                notes=row["notes"] or "",
            )

    def mark_rate_limited(self, key_id: int, cooldown_seconds: float = 60.0) -> None:
        """Đưa key vào trạng thái hạ nhiệt (cooling) khi dính HTTP 429."""
        self.update_key_status(key_id, "cooling", cooldown_seconds=cooldown_seconds)

    def export_jsonl(self, target_path: Optional[Path] = None) -> Path:
        """Xuất toàn bộ pool ra file JSONL (mỗi dòng 1 key kèm metadata) cho worker/caller."""
        out = target_path or (_PROJECT_ROOT / "config" / "cloud_keys.jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        keys = self.list_keys()
        with open(out, "w", encoding="utf-8") as f:
            for k in keys:
                line_data = {
                    "id": k.id,
                    "provider": k.provider,
                    "label": k.label,
                    "api_key": k.api_key,
                    "status": k.status,
                    "is_available": k.is_available,
                    "latency_ms": k.latency_ms,
                    "models_supported": [m.strip() for m in k.models_supported.split(",") if m.strip()],
                    "usage_count": k.usage_count,
                    "error_count": k.error_count,
                    "last_used_at": k.last_used_at,
                }
                f.write(json.dumps(line_data, ensure_ascii=False) + "\n")
        return out

    def sync_to_secrets(self, secrets_path: Optional[Path] = None) -> None:
        """Đồng bộ key active hàng đầu sang config/secrets.json để tương thích ngược 100%."""
        sec_path = secrets_path or (_PROJECT_ROOT / "config" / "secrets.json")
        if not sec_path.exists():
            return

        try:
            with open(sec_path, "r", encoding="utf-8") as f:
                sec = json.load(f)
        except Exception:
            return

        sec.setdefault("api_keys", {})
        gemini_keys = [k.api_key for k in self.list_keys("gemini") if k.status != "disabled"]
        groq_keys = [k.api_key for k in self.list_keys("groq") if k.status != "disabled"]

        if gemini_keys:
            sec["api_keys"]["GEMINI_API_KEY"] = gemini_keys[0]
            sec["api_keys"]["ERIS_GEMINI_FALLBACK_API_KEYS"] = gemini_keys[1:]
            os.environ["GEMINI_API_KEY"] = gemini_keys[0]

        if groq_keys:
            sec["api_keys"]["ERIS_GROQ_API_KEY"] = groq_keys[0]
            sec["api_keys"]["ERIS_GROQ_FALLBACK_API_KEYS"] = groq_keys[1:]
            os.environ["ERIS_GROQ_API_KEY"] = groq_keys[0]

        with open(sec_path, "w", encoding="utf-8") as f:
            json.dump(sec, f, indent=4, ensure_ascii=False)

    def seed_from_secrets_if_empty(self, secrets_path: Optional[Path] = None) -> int:
        """Nạp key ban đầu từ secrets.json vào DB nếu bảng DB đang rỗng."""
        with self._connection() as conn:
            cnt = conn.execute("SELECT COUNT(*) as c FROM cloud_api_keys").fetchone()["c"]
            if cnt > 0:
                return 0

        sec_path = secrets_path or (_PROJECT_ROOT / "config" / "secrets.json")
        if not sec_path.exists():
            return 0

        try:
            with open(sec_path, "r", encoding="utf-8") as f:
                sec = json.load(f)
        except Exception:
            return 0

        api_keys = sec.get("api_keys", {})
        count = 0

        # Gemini
        g_main = (api_keys.get("GEMINI_API_KEY") or "").strip()
        if g_main:
            self.add_key("gemini", g_main, label="Google AI Studio (Chính)")
            count += 1
        for idx, fb in enumerate(api_keys.get("ERIS_GEMINI_FALLBACK_API_KEYS", []), 1):
            if str(fb).strip():
                self.add_key("gemini", str(fb).strip(), label=f"Google AI Studio (Dự phòng #{idx})")
                count += 1

        # Groq
        groq_main = (api_keys.get("ERIS_GROQ_API_KEY") or "").strip()
        if groq_main:
            self.add_key("groq", groq_main, label="Groq Cloud (Chính)")
            count += 1
        for idx, fb in enumerate(api_keys.get("ERIS_GROQ_FALLBACK_API_KEYS", []), 1):
            if str(fb).strip():
                self.add_key("groq", str(fb).strip(), label=f"Groq Cloud (Dự phòng #{idx})")
                count += 1

        return count

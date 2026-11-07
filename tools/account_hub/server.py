# -*- coding: utf-8 -*-
"""tools/account_hub/server.py — Eris Account & Resource Hub Backend.

FastAPI unified server for managing DeepSeek accounts, ElevenLabs tokens,
Gemini session cookies, Groq/Gemini API keys, and overall system readiness.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STATIC_DIR = Path(__file__).resolve().parent / "static"

# Đảm bảo đường dẫn import cho src
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
sys.path.insert(0, str(_PROJECT_ROOT))

# Paths quan trọng
SECRETS_FILE = _PROJECT_ROOT / "config" / "secrets.json"
SECRETS_EXAMPLE_FILE = _PROJECT_ROOT / "config" / "secrets.example.json"
ELEVEN_DB_FILE = _PROJECT_ROOT / "config" / "elevenlabs_pool.db"
WHALE_DB_FILE = _PROJECT_ROOT / "tools" / "whale2api" / "docker-data" / "whale2api" / "whale2api.db"
WHALE_CSV_FILE = _PROJECT_ROOT / "tools" / "whale2api" / "accounts.csv"
GEMINI_CRED_FILE = _PROJECT_ROOT / "tools" / "gemini_vision_docker" / "data_cache" / "gemini_credentials.json"

PORTS_TO_CHECK = {
    "DeepSeek Main (9090)": 9090,
    "DeepSeek Chat (6011)": 6011,
    "DeepSeek Surface (6012)": 6012,
    "DeepSeek Observer (6013)": 6013,
    "DeepSeek Critic (6014)": 6014,
    "DeepSeek Seed (6015)": 6015,
    "DeepSeek Exec (6016)": 6016,
    "DeepSeek Pool UI (5010)": 5010,
    "ElevenLabs Pool (18888)": 18888,
    "Gemini Docker (8000)": 8000,
    "Cline Bridge (7832)": 7832,
}

app = FastAPI(title="Eris Account & Resource Hub", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def port_listening(port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except Exception:
        return False


def mask_string(val: str, prefix_len: int = 4, suffix_len: int = 4) -> str:
    s = str(val or "").strip()
    if not s:
        return ""
    if len(s) <= prefix_len + suffix_len + 2:
        return s[:2] + "***" + (s[-2:] if len(s) >= 4 else "")
    return f"{s[:prefix_len]}...{s[-suffix_len:]}"


def load_secrets_dict() -> dict[str, Any]:
    if SECRETS_FILE.is_file():
        try:
            return json.loads(SECRETS_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
    if SECRETS_EXAMPLE_FILE.is_file():
        try:
            return json.loads(SECRETS_EXAMPLE_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
    return {"api_keys": {}}


def save_secrets_dict(data: dict[str, Any]) -> None:
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRETS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ── Overview API ─────────────────────────────────────────────────────────────

@app.get("/api/overview")
def get_overview():
    # 1. DeepSeek accounts & ports
    ds_total = 0
    ds_active = 0
    ds_muted = 0
    if WHALE_DB_FILE.is_file():
        try:
            conn = sqlite3.connect(str(WHALE_DB_FILE))
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM pool_accounts")
            ds_total = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM pool_accounts WHERE discarded = 0 AND (mute_until IS NULL OR mute_until = '')")
            ds_active = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM pool_accounts WHERE discarded = 1 OR (mute_until IS NOT NULL AND mute_until != '')")
            ds_muted = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass

    # Check key deepseek ports
    ds_ports_ok = port_listening(6011) and port_listening(6012)
    ds_ready = (ds_total > 0 and ds_active > 0) or ds_ports_ok

    # 2. ElevenLabs accounts & quota
    el_total = 0
    el_active = 0
    el_quota_rem = 0
    el_quota_used = 0
    if ELEVEN_DB_FILE.is_file():
        try:
            conn = sqlite3.connect(str(ELEVEN_DB_FILE))
            cur = conn.cursor()
            cur.execute("SELECT count(*), sum(character_limit), sum(character_count) FROM elevenlabs_accounts")
            row = cur.fetchone()
            if row:
                el_total = row[0] or 0
                lim = row[1] or 0
                used = row[2] or 0
                el_quota_used = used
                el_quota_rem = max(0, lim - used)
            cur.execute("SELECT count(*) FROM elevenlabs_accounts WHERE status = 'active'")
            el_active = cur.fetchone()[0] or 0
            conn.close()
        except Exception:
            pass
    el_ready = el_total > 0 and el_quota_rem > 500

    # 3. Gemini Cookie status
    gemini_ready = False
    gemini_mtime = None
    gemini_time_str = "Chưa có"
    gemini_age_hours = None
    if GEMINI_CRED_FILE.is_file():
        try:
            data = json.loads(GEMINI_CRED_FILE.read_text(encoding="utf-8"))
            if data.get("psid"):
                mtime = GEMINI_CRED_FILE.stat().st_mtime
                gemini_mtime = mtime
                gemini_time_str = datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M")
                gemini_age_hours = round((time.time() - mtime) / 3600.0, 1)
                # Coi là ready nếu dưới 48 giờ
                gemini_ready = gemini_age_hours < 72.0
        except Exception:
            pass

    # 4. Groq & AI Studio
    sec = load_secrets_dict()
    api_keys = sec.get("api_keys", {})
    groq_key = api_keys.get("ERIS_GROQ_API_KEY") or os.environ.get("ERIS_GROQ_API_KEY", "")
    gemini_key = api_keys.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    groq_ready = bool(groq_key.strip())
    gemini_api_ready = bool(gemini_key.strip())

    # 5. Runtime check
    runtime_running = False
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or []).lower()
            if "vision.py" in cmd or "eris.exe" in (p.info.get("name") or "").lower():
                runtime_running = True
                break
        except Exception:
            pass

    # Calculate overall readiness (0 - 100%)
    score = 0
    if ds_ready: score += 40
    if el_ready: score += 30
    if gemini_ready or gemini_api_ready: score += 20
    if groq_ready: score += 10

    return {
        "readiness_percent": score,
        "is_ready": score >= 70,
        "runtime_running": runtime_running,
        "deepseek": {
            "total": ds_total,
            "active": ds_active,
            "muted": ds_muted,
            "ports_ok": ds_ports_ok,
            "ready": ds_ready,
        },
        "elevenlabs": {
            "total": el_total,
            "active": el_active,
            "quota_remaining": el_quota_rem,
            "quota_used": el_quota_used,
            "ready": el_ready,
        },
        "gemini": {
            "has_cookie": GEMINI_CRED_FILE.is_file(),
            "last_synced": gemini_time_str,
            "age_hours": gemini_age_hours,
            "ready": gemini_ready,
            "docker_running": port_listening(8000),
        },
        "cloud_keys": {
            "groq_configured": groq_ready,
            "groq_masked": mask_string(groq_key),
            "gemini_api_configured": gemini_api_ready,
            "gemini_api_masked": mask_string(gemini_key),
        }
    }


# ── DeepSeek Management API ──────────────────────────────────────────────────

def format_mute_until(iso_str: str) -> tuple[str, str, bool]:
    """Trả về (formatted_datetime, human_remaining, is_expired)."""
    if not iso_str or not str(iso_str).strip():
        return "", "", False
    try:
        clean = str(iso_str).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        local_tz = timezone(timedelta(hours=7))
        local_dt = dt.astimezone(local_tz)
        local_str = local_dt.strftime("%d/%m %H:%M")

        now = datetime.now(timezone.utc)
        diff_sec = (dt - now).total_seconds()

        if diff_sec <= 0:
            return local_str, "Đã hết hạn", True

        hours = int(diff_sec // 3600)
        minutes = int((diff_sec % 3600) // 60)
        if hours >= 24:
            days = hours // 24
            rem_h = hours % 24
            human = f"Còn {days} ngày {rem_h}h"
        elif hours > 0:
            human = f"Còn {hours}h {minutes}p"
        else:
            human = f"Còn {minutes} phút"
        return local_str, human, False
    except Exception:
        return str(iso_str), "", False


def get_whale2api_key() -> str:
    if not WHALE_DB_FILE.is_file():
        return ""
    try:
        conn = sqlite3.connect(str(WHALE_DB_FILE))
        cur = conn.cursor()
        cur.execute("SELECT api_key FROM gateway_api_keys WHERE enabled = 1 ORDER BY created_at ASC LIMIT 1")
        row = cur.fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception:
        return ""


@app.get("/api/deepseek/accounts")
def list_deepseek_accounts():
    if not WHALE_DB_FILE.is_file():
        return {"accounts": [], "db_exists": False, "expired_mute_count": 0, "has_probe_engine": False}

    accounts = []
    expired_mute_count = 0
    try:
        conn = sqlite3.connect(str(WHALE_DB_FILE))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, identifier, password, token, discarded, discard_reason, mute_until, created_at, device_id FROM pool_accounts ORDER BY id ASC")
        for row in cur.fetchall():
            raw_id = row["identifier"] or ""
            raw_tok = row["token"] or ""
            raw_mute = row["mute_until"] or ""
            device_id = row["device_id"] or ""
            discarded = bool(row["discarded"])
            reason = row["discard_reason"] or ""

            mute_dt_str, mute_rem_str, is_expired = format_mute_until(raw_mute)
            if is_expired and discarded:
                expired_mute_count += 1

            # Xác định nhãn và mã trạng thái trực quan
            if not discarded:
                status_label = "Hoạt động"
                status_code = "active"
            elif is_expired:
                status_label = "Hết hạn mute (mở ngay)"
                status_code = "expired_mute"
            elif reason == "muted":
                status_label = f"Tạm ngưng ({mute_rem_str})"
                status_code = "muted"
            elif reason == "manual":
                status_label = "Khóa thủ công"
                status_code = "manual"
            else:
                status_label = reason or "Đã vô hiệu"
                status_code = "discarded"

            accounts.append({
                "id": row["id"],
                "identifier": raw_id,
                "has_token": bool(raw_tok.strip()),
                "token_masked": mask_string(raw_tok, 8, 8) if raw_tok else "Chưa có token",
                "auth_type": "User Token" if raw_tok else ("Password" if row["password"] else "None"),
                "has_device_id": bool(device_id.strip()),
                "device_id_masked": mask_string(device_id, 6, 6) if device_id else "",
                "discarded": discarded,
                "discard_reason": reason,
                "mute_until_raw": raw_mute,
                "mute_until_formatted": mute_dt_str,
                "mute_remaining_human": mute_rem_str,
                "is_mute_expired": is_expired,
                "status_label": status_label,
                "status_code": status_code,
                "created_at": row["created_at"] or "",
            })
        conn.close()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi đọc whale2api.db: {exc}")

    return {
        "accounts": accounts,
        "db_exists": True,
        "expired_mute_count": expired_mute_count,
        "has_probe_engine": port_listening(5010),
    }


class AddDeepSeekAccountsPayload(BaseModel):
    accounts_text: str


@app.post("/api/deepseek/accounts")
def add_deepseek_accounts(payload: AddDeepSeekAccountsPayload):
    text = payload.accounts_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Danh sách tokens / tài khoản không được để trống")

    added = 0
    errors = []
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    if not WHALE_DB_FILE.is_file():
        raise HTTPException(status_code=404, detail="Không tìm thấy whale2api.db")

    conn = sqlite3.connect(str(WHALE_DB_FILE))
    cur = conn.cursor()

    cur.execute("SELECT api_key FROM gateway_api_keys WHERE enabled = 1")
    gateway_keys = [r[0] for r in cur.fetchall()]

    for idx, line in enumerate(lines, 1):
        ident = ""
        token = ""
        password = ""

        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
                token = str(parsed.get("value") or parsed.get("token") or "").strip()
            except Exception:
                pass

        if not token:
            clean_line = line
            if clean_line.lower().startswith("bearer "):
                clean_line = clean_line[7:].strip()

            if "," in clean_line:
                parts = [p.strip() for p in clean_line.split(",", 1)]
                ident = parts[0]
                second_part = parts[1]
                if second_part.lower().startswith("bearer "):
                    second_part = second_part[7:].strip()
                if len(second_part) > 20 or second_part.startswith("eyJ"):
                    token = second_part
                else:
                    password = second_part
            else:
                token = clean_line

        if not ident:
            ident = f"Token-{token[:8]}" if token else f"Acc-{idx}"

        try:
            cur.execute(
                """
                INSERT INTO pool_accounts (identifier, password, token, discarded, discard_reason, mute_until, created_at)
                VALUES (?, ?, ?, 0, '', '', ?)
                """,
                (ident, password, token, now_str),
            )
            new_id = cur.lastrowid

            for gkey in gateway_keys:
                cur.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM pool_bindings WHERE api_key = ?", (gkey,))
                next_pos = cur.fetchone()[0]
                cur.execute(
                    "INSERT OR IGNORE INTO pool_bindings (api_key, account_id, position) VALUES (?, ?, ?)",
                    (gkey, new_id, next_pos),
                )

            added += 1
        except Exception as e:
            errors.append(f"Dòng {idx}: {e}")

    conn.commit()
    conn.close()

    return {"added": added, "errors": errors, "total_lines": len(lines)}


# ── Probe & Ping Test API (Kế thừa Whale2API Probe) ──────────────────────────

@app.post("/api/deepseek/test/{identifier}")
def test_deepseek_account(identifier: str):
    """Gửi test probe qua Whale2API cổng 5010 và đợi kết quả."""
    if not port_listening(5010):
        raise HTTPException(status_code=503, detail="Whale2API cổng 5010 chưa bật để chạy probe test")

    api_key = get_whale2api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="Chưa có Gateway Key nào được kích hoạt")

    try:
        r = requests.post(
            f"http://127.0.0.1:5010/api/keys/{api_key}/accounts/{identifier}/test",
            headers={"Authorization": "Bearer eris-pool-admin"},
            timeout=5.0,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể kết nối Whale2API: {e}")

    if r.status_code not in (200, 202):
        raise HTTPException(status_code=r.status_code, detail=r.text)

    # Chờ kết quả probe trong tối đa 12 giây
    for _ in range(12):
        time.sleep(1.0)
        try:
            r_job = requests.get(
                f"http://127.0.0.1:5010/api/keys/{api_key}/accounts/test",
                headers={"Authorization": "Bearer eris-pool-admin"},
                timeout=3.0,
            )
            if r_job.status_code == 200:
                job_data = r_job.json()
                results = job_data.get("results", [])
                for res in results:
                    if res.get("identifier") == identifier:
                        return {
                            "status": "completed",
                            "ok": res.get("ok", False),
                            "message": res.get("message", "available"),
                            "pool_status": res.get("pool_status", "active"),
                            "token_updated": res.get("token_updated", False),
                        }
                if job_data.get("status") == "completed":
                    break
        except Exception:
            pass

    return {"status": "started", "message": "Đang chạy test ngầm, vui lòng làm mới sau ít giây"}


@app.post("/api/deepseek/test-all")
def test_all_deepseek_accounts():
    """Bắt đầu chạy kiểm tra toàn bộ tài khoản trong Pool."""
    if not port_listening(5010):
        raise HTTPException(status_code=503, detail="Whale2API cổng 5010 chưa bật")
    api_key = get_whale2api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="Chưa có Gateway Key")

    try:
        r = requests.post(
            f"http://127.0.0.1:5010/api/keys/{api_key}/accounts/test",
            headers={"Authorization": "Bearer eris-pool-admin"},
            json={},
            timeout=5.0,
        )
        if r.status_code not in (200, 202):
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi gọi Whale2API test: {exc}")


@app.get("/api/deepseek/test-status")
def get_deepseek_test_status():
    if not port_listening(5010):
        return {"running": False, "status": "offline"}
    api_key = get_whale2api_key()
    if not api_key:
        return {"running": False}
    try:
        r = requests.get(
            f"http://127.0.0.1:5010/api/keys/{api_key}/accounts/test",
            headers={"Authorization": "Bearer eris-pool-admin"},
            timeout=3.0,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {"running": False}


# ── Mute, Unmute & Clean API ─────────────────────────────────────────────────

class MuteCustomPayload(BaseModel):
    account_id: int
    duration_hours: int = 24
    reason: str = "muted"


@app.post("/api/deepseek/mute-custom")
def mute_custom_account(payload: MuteCustomPayload):
    if not WHALE_DB_FILE.is_file():
        raise HTTPException(status_code=404, detail="whale2api.db không tồn tại")

    conn = sqlite3.connect(str(WHALE_DB_FILE))
    cur = conn.cursor()
    if payload.duration_hours > 0:
        until_dt = datetime.now(timezone.utc) + timedelta(hours=payload.duration_hours)
        mute_until = until_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        reason = payload.reason or "muted"
    else:
        mute_until = ""
        reason = "manual"

    cur.execute(
        """
        UPDATE pool_accounts
        SET discarded = 1, discard_reason = ?, mute_until = ?
        WHERE id = ?
        """,
        (reason, mute_until, payload.account_id),
    )
    conn.commit()
    conn.close()
    return {"message": "Đã cập nhật trạng thái tạm ngưng"}


@app.post("/api/deepseek/unmute-expired")
def unmute_expired_deepseek_accounts():
    """Mở khóa 1-click cho tất cả các tài khoản đã hết hạn mute."""
    if not WHALE_DB_FILE.is_file():
        raise HTTPException(status_code=404, detail="whale2api.db không tồn tại")

    now_utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = sqlite3.connect(str(WHALE_DB_FILE))
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE pool_accounts
        SET discarded = 0, discard_reason = '', mute_until = ''
        WHERE discarded = 1 AND discard_reason = 'muted' AND mute_until != '' AND mute_until <= ?
        """,
        (now_utc_str,),
    )
    unmuted = cur.rowcount
    conn.commit()
    conn.close()
    return {"unmuted_count": unmuted, "message": f"Đã mở khóa thành công {unmuted} tài khoản hết hạn mute!"}


class UnmuteAccountPayload(BaseModel):
    account_id: Optional[int] = None
    unmute_all: bool = False


@app.post("/api/deepseek/unmute")
def unmute_deepseek_account(payload: UnmuteAccountPayload):
    if not WHALE_DB_FILE.is_file():
        raise HTTPException(status_code=404, detail="whale2api.db không tồn tại")

    conn = sqlite3.connect(str(WHALE_DB_FILE))
    cur = conn.cursor()
    if payload.unmute_all:
        cur.execute("UPDATE pool_accounts SET discarded = 0, mute_until = '', discard_reason = ''")
    elif payload.account_id:
        cur.execute("UPDATE pool_accounts SET discarded = 0, mute_until = '', discard_reason = '' WHERE id = ?", (payload.account_id,))
    conn.commit()
    conn.close()
    return {"message": "Đã mở khóa tài khoản thành công"}


@app.post("/api/deepseek/clear-device-id/{account_id}")
def clear_device_id(account_id: int):
    if not WHALE_DB_FILE.is_file():
        raise HTTPException(status_code=404, detail="whale2api.db không tồn tại")
    conn = sqlite3.connect(str(WHALE_DB_FILE))
    cur = conn.cursor()
    cur.execute("UPDATE pool_accounts SET device_id = '', device_id_updated_at = '' WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()
    return {"message": "Đã xóa Device Token thành công"}


@app.get("/api/deepseek/export-csv")
def export_deepseek_csv():
    if not WHALE_DB_FILE.is_file():
        raise HTTPException(status_code=404, detail="whale2api.db không tồn tại")
    conn = sqlite3.connect(str(WHALE_DB_FILE))
    cur = conn.cursor()
    cur.execute("SELECT identifier, password, token, device_id, discarded, discard_reason, mute_until FROM pool_accounts ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()

    csv_lines = ["identifier,password,token,device_id,discarded,discard_reason,mute_until"]
    for r in rows:
        ident = (r[0] or "").replace('"', '""')
        pwd = (r[1] or "").replace('"', '""')
        tok = (r[2] or "").replace('"', '""')
        dev = (r[3] or "").replace('"', '""')
        disc = "1" if r[4] else "0"
        reason = (r[5] or "").replace('"', '""')
        mute = (r[6] or "").replace('"', '""')
        csv_lines.append(f'"{ident}","{pwd}","{tok}","{dev}",{disc},"{reason}","{mute}"')
    content = "\ufeff" + "\n".join(csv_lines)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=deepseek_pool_accounts.csv"},
    )


@app.delete("/api/deepseek/accounts/{account_id}")
def delete_deepseek_account(account_id: int):
    if not WHALE_DB_FILE.is_file():
        raise HTTPException(status_code=404, detail="whale2api.db không tồn tại")

    conn = sqlite3.connect(str(WHALE_DB_FILE))
    cur = conn.cursor()
    cur.execute("DELETE FROM pool_bindings WHERE account_id = ?", (account_id,))
    cur.execute("DELETE FROM pool_accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()
    return {"message": f"Đã xóa tài khoản ID {account_id}"}


# ── ElevenLabs Management API ────────────────────────────────────────────────

@app.get("/api/elevenlabs/accounts")
def list_elevenlabs_accounts():
    if not ELEVEN_DB_FILE.is_file():
        return {"accounts": [], "db_exists": False}

    accounts = []
    try:
        conn = sqlite3.connect(str(ELEVEN_DB_FILE))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, label, refresh_token, character_count, character_limit, status, reset_at, notes, last_used_at FROM elevenlabs_accounts ORDER BY id ASC")
        for row in cur.fetchall():
            raw_token = row["refresh_token"] or ""
            count = int(row["character_count"] or 0)
            limit = int(row["character_limit"] or 10000)
            reset_at = float(row["reset_at"] or 0.0)
            reset_str = datetime.fromtimestamp(reset_at).strftime("%d/%m/%Y") if reset_at > 0 else "Chưa sync"

            accounts.append({
                "id": row["id"],
                "label": row["label"] or f"Acc #{row['id']}",
                "refresh_token_masked": mask_string(raw_token, 8, 8),
                "character_count": count,
                "character_limit": limit,
                "remaining": max(0, limit - count),
                "status": row["status"] or "active",
                "reset_at_str": reset_str,
                "notes": row["notes"] or "",
            })
        conn.close()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi đọc elevenlabs_pool.db: {exc}")

    return {"accounts": accounts, "db_exists": True}


def parse_elevenlabs_input(text: str, default_label: str = "") -> tuple[list[dict[str, str]], Optional[str]]:
    """Tự động nhận diện chuỗi JSON Firebase auth, CSV hoặc raw token."""
    entries = []
    found_api_key = None

    clean_text = text.strip()
    if not clean_text:
        return entries, found_api_key

    # Tìm Firebase API key nếu có trong văn bản
    ak_match = re.search(r'["\']apiKey["\']\s*:\s*["\'](AIzaSy[A-Za-z0-9_-]+)["\']', clean_text)
    if ak_match:
        found_api_key = ak_match.group(1)

    # 1. Thử parse toàn bộ chuỗi dưới dạng JSON object đơn lẻ
    if clean_text.startswith("{") and clean_text.endswith("}"):
        try:
            data = json.loads(clean_text)
            token = data.get("stsTokenManager", {}).get("refreshToken") or data.get("refreshToken")
            if not token:
                tok_m = re.search(r'AMf-[A-Za-z0-9_-]+', clean_text)
                if tok_m:
                    token = tok_m.group(0)

            label = default_label.strip()
            if not label:
                dname = (data.get("displayName") or "").strip()
                email = (data.get("email") or "").strip()
                if dname and email:
                    label = f"{dname} ({email})"
                elif dname:
                    label = dname
                elif email:
                    label = email
                else:
                    label = f"Acc-{token[:6]}" if token else "Acc-Eleven"

            notes = f"email: {data.get('email', '')}, uid: {data.get('uid', '')}"
            if token:
                entries.append({"token": token, "label": label, "notes": notes})
                return entries, found_api_key
        except Exception:
            pass

    # 2. Xử lý theo từng dòng (cho trường hợp dán nhiều token hoặc JSON theo dòng)
    lines = [l.strip() for l in clean_text.splitlines() if l.strip() and not l.startswith("#")]
    for idx, line in enumerate(lines, 1):
        # Dòng là JSON object
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                token = data.get("stsTokenManager", {}).get("refreshToken") or data.get("refreshToken")
                if not token:
                    tok_m = re.search(r'AMf-[A-Za-z0-9_-]+', line)
                    if tok_m:
                        token = tok_m.group(0)
                label = default_label.strip() if len(lines) == 1 else ""
                if not label:
                    dname = (data.get("displayName") or "").strip()
                    email = (data.get("email") or "").strip()
                    if dname and email:
                        label = f"{dname} ({email})"
                    elif dname:
                        label = dname
                    elif email:
                        label = email
                    else:
                        label = f"Acc-{token[:6]}" if token else f"Acc-{idx}"
                notes = f"email: {data.get('email', '')}, uid: {data.get('uid', '')}"
                if token:
                    entries.append({"token": token, "label": label, "notes": notes})
                    continue
            except Exception:
                pass

        # Dạng CSV: "Tên_Giọng, Token"
        if "," in line:
            parts = [p.strip() for p in line.split(",", 1)]
            label = parts[0]
            token = parts[1]
            if token.startswith("AMf-") or len(token) > 40:
                entries.append({"token": token, "label": label, "notes": ""})
                continue

        # Dạng raw token bắt đầu bằng AMf-
        tok_m = re.search(r'AMf-[A-Za-z0-9_-]+', line)
        if tok_m:
            token = tok_m.group(0)
            label = default_label.strip() if len(lines) == 1 and default_label.strip() else f"Acc-{token[:6]}"
            entries.append({"token": token, "label": label, "notes": ""})
        elif len(line) > 30 and not line.startswith("{"):
            label = default_label.strip() if len(lines) == 1 and default_label.strip() else f"Acc-{line[:6]}"
            entries.append({"token": line, "label": label, "notes": ""})

    return entries, found_api_key


class AddElevenTokensPayload(BaseModel):
    tokens_text: str
    voice_name: Optional[str] = None
    firebase_api_key: Optional[str] = None


@app.post("/api/elevenlabs/tokens")
def add_elevenlabs_tokens(payload: AddElevenTokensPayload):
    text = payload.tokens_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Danh sách token không được để trống")

    entries, found_api_key = parse_elevenlabs_input(text, default_label=payload.voice_name or "")
    if not entries:
        raise HTTPException(status_code=400, detail="Không tìm thấy Refresh Token hợp lệ trong nội dung đã dán")

    # Tự động cập nhật FIREBASE_API_KEY nếu phát hiện trong JSON hoặc người dùng nhập
    active_fb_key = (payload.firebase_api_key or "").strip() or (found_api_key or "").strip()
    if active_fb_key:
        sec = load_secrets_dict()
        sec.setdefault("api_keys", {})["FIREBASE_API_KEY"] = active_fb_key
        save_secrets_dict(sec)
        os.environ["FIREBASE_API_KEY"] = active_fb_key

    from runtime.elevenlabs_pool_db import ElevenPoolDB
    pool_db = ElevenPoolDB()

    added = 0
    updated = 0
    errors = []

    for idx, item in enumerate(entries, 1):
        ok, msg, acc = pool_db.upsert_account(
            refresh_token=item["token"],
            label=item["label"],
            notes=item["notes"],
            auto_sync=True,
        )
        if ok:
            if "cập nhật" in msg.lower():
                updated += 1
            else:
                added += 1
        else:
            errors.append(f"Mục #{idx}: {msg}")

    return {
        "added": added,
        "updated": updated,
        "errors": errors,
        "total_parsed": len(entries),
        "api_key_detected": bool(active_fb_key),
    }


class RenameVoicePayload(BaseModel):
    account_id: int
    new_name: str


@app.post("/api/elevenlabs/rename")
def rename_elevenlabs_voice(payload: RenameVoicePayload):
    new_name = payload.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Tên giọng không được để trống")

    from runtime.elevenlabs_pool_db import ElevenPoolDB
    pool_db = ElevenPoolDB()
    ok, msg = pool_db.rename_account(payload.account_id, new_name)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"message": msg}


@app.post("/api/elevenlabs/sync/{account_id}")
def sync_single_elevenlabs_voice(account_id: int):
    from runtime.elevenlabs_pool_db import ElevenPoolDB
    pool_db = ElevenPoolDB()
    ok, msg = pool_db.sync_subscription(account_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    acc = pool_db.get_account_by_id(account_id)
    return {"message": msg, "account": acc.to_dict() if acc else None}


@app.post("/api/elevenlabs/sync")
def sync_elevenlabs_quotas():
    if not ELEVEN_DB_FILE.is_file():
        raise HTTPException(status_code=404, detail="elevenlabs_pool.db chưa được tạo")

    from runtime.elevenlabs_pool_db import ElevenPoolDB
    pool_db = ElevenPoolDB()
    accounts = pool_db.list_accounts()
    synced = 0
    errors = []

    for acc in accounts:
        ok, msg = pool_db.sync_subscription(acc.id)
        if ok:
            synced += 1
        else:
            errors.append(f"{acc.label}: {msg}")

    return {
        "message": f"Đã đồng bộ {synced}/{len(accounts)} tài khoản giọng nói",
        "synced": synced,
        "total": len(accounts),
        "errors": errors,
    }


@app.delete("/api/elevenlabs/accounts/{account_id}")
def delete_elevenlabs_account(account_id: int):
    if not ELEVEN_DB_FILE.is_file():
        raise HTTPException(status_code=404, detail="elevenlabs_pool.db không tồn tại")

    conn = sqlite3.connect(str(ELEVEN_DB_FILE))
    cur = conn.cursor()
    cur.execute("DELETE FROM elevenlabs_accounts WHERE id = ?", (account_id,))
    conn.commit()
    conn.close()
    return {"message": f"Đã xóa tài khoản ID {account_id}"}


# ── Gemini / Google Session API ──────────────────────────────────────────────

@app.get("/api/gemini/status")
def get_gemini_status():
    cred_exists = GEMINI_CRED_FILE.is_file()
    psid_masked = ""
    psidts_masked = ""
    last_synced = "Chưa có"
    age_hours = 0.0

    if cred_exists:
        try:
            data = json.loads(GEMINI_CRED_FILE.read_text(encoding="utf-8"))
            psid = data.get("psid") or ""
            psidts = data.get("psidts") or ""
            psid_masked = mask_string(psid, 10, 10)
            psidts_masked = mask_string(psidts, 10, 10)
            mtime = GEMINI_CRED_FILE.stat().st_mtime
            last_synced = datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M:%S")
            age_hours = round((time.time() - mtime) / 3600.0, 1)
        except Exception:
            pass

    return {
        "exists": cred_exists,
        "psid_masked": psid_masked,
        "psidts_masked": psidts_masked,
        "last_synced": last_synced,
        "age_hours": age_hours,
        "is_stale": age_hours > 72.0 if cred_exists else True,
        "docker_running": port_listening(8000),
    }


class CookiePayload(BaseModel):
    psid: Optional[str] = None
    psidts: Optional[str] = None
    raw_cookie: Optional[str] = None


@app.post("/api/cookies")
def save_gemini_cookies(payload: CookiePayload):
    psid = (payload.psid or "").strip()
    psidts = (payload.psidts or "").strip()

    # Nếu gửi chuỗi thô từ header
    if payload.raw_cookie:
        raw = payload.raw_cookie
        m_psid = re.search(r"__Secure-1PSID=([^;]+)", raw)
        if m_psid:
            psid = m_psid.group(1).strip()
        m_psidts = re.search(r"__Secure-1PSIDTS=([^;]+)", raw)
        if m_psidts:
            psidts = m_psidts.group(1).strip()

    if not psid:
        raise HTTPException(status_code=400, detail="Không tìm thấy cookie __Secure-1PSID hợp lệ")

    # Lưu vào data_cache của gemini_vision_docker
    GEMINI_CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
    cred_data = {
        "psid": psid,
        "psidts": psidts,
        "updated_at": time.time(),
    }
    GEMINI_CRED_FILE.write_text(json.dumps(cred_data, indent=2), encoding="utf-8")

    # Chuyển tiếp tới Docker port 8000 nếu nó đang bật
    forwarded = False
    if port_listening(8000):
        try:
            requests.post("http://127.0.0.1:8000/api/cookies", json={"psid": psid, "psidts": psidts}, timeout=2.0)
            forwarded = True
        except Exception:
            pass

    return {
        "success": True,
        "message": "Đã đồng bộ Cookie Gemini thành công!",
        "forwarded_to_docker": forwarded,
    }


@app.post("/api/gemini/launch-tutor")
def launch_tutor_login():
    bat_file = _PROJECT_ROOT / "tutor_login.bat"
    if not bat_file.is_file():
        raise HTTPException(status_code=404, detail="tutor_login.bat không tìm thấy")
    try:
        subprocess.Popen(f'cmd.exe /c start "Google Tutor Login" cmd /k "{bat_file}"', cwd=str(_PROJECT_ROOT), shell=True)
        return {"message": "Đã mở cửa sổ đăng nhập Google Tutor"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Cloud Keys & Secrets API ─────────────────────────────────────────────────

class TestKeyPayload(BaseModel):
    key: Optional[str] = None


@app.get("/api/secrets")
def get_secrets():
    sec = load_secrets_dict()
    api_keys = sec.get("api_keys", {})
    groq = api_keys.get("ERIS_GROQ_API_KEY") or os.environ.get("ERIS_GROQ_API_KEY", "")
    gemini = api_keys.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    firebase = api_keys.get("FIREBASE_API_KEY") or os.environ.get("FIREBASE_API_KEY", "")
    deepseek_api = api_keys.get("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")

    gemini_fallbacks = [k.strip() for k in api_keys.get("ERIS_GEMINI_FALLBACK_API_KEYS", []) if str(k).strip()]
    groq_fallbacks = [k.strip() for k in api_keys.get("ERIS_GROQ_FALLBACK_API_KEYS", []) if str(k).strip()]

    return {
        "groq_key_masked": mask_string(groq),
        "gemini_key_masked": mask_string(gemini),
        "firebase_key_masked": mask_string(firebase),
        "deepseek_key_masked": mask_string(deepseek_api),
        "has_groq": bool(groq.strip()),
        "has_gemini": bool(gemini.strip()),
        "has_firebase": bool(firebase.strip()),
        "has_deepseek": bool(deepseek_api.strip()),
        "gemini_fallbacks_count": len(gemini_fallbacks),
        "gemini_fallbacks_masked": [mask_string(k) for k in gemini_fallbacks],
        "gemini_fallbacks_raw": "\n".join(gemini_fallbacks),
        "groq_fallbacks_count": len(groq_fallbacks),
        "groq_fallbacks_masked": [mask_string(k) for k in groq_fallbacks],
        "groq_fallbacks_raw": "\n".join(groq_fallbacks),
    }


class UpdateSecretsPayload(BaseModel):
    groq_key: Optional[str] = None
    groq_fallback_text: Optional[str] = None
    gemini_key: Optional[str] = None
    gemini_fallback_text: Optional[str] = None
    firebase_key: Optional[str] = None
    deepseek_key: Optional[str] = None


@app.post("/api/secrets")
def update_secrets(payload: UpdateSecretsPayload):
    sec = load_secrets_dict()
    sec.setdefault("api_keys", {})

    if payload.groq_key is not None and payload.groq_key.strip():
        sec["api_keys"]["ERIS_GROQ_API_KEY"] = payload.groq_key.strip()
        os.environ["ERIS_GROQ_API_KEY"] = payload.groq_key.strip()

    if payload.groq_fallback_text is not None:
        lines = [l.strip() for l in payload.groq_fallback_text.splitlines() if l.strip()]
        sec["api_keys"]["ERIS_GROQ_FALLBACK_API_KEYS"] = lines

    if payload.gemini_key is not None and payload.gemini_key.strip():
        sec["api_keys"]["GEMINI_API_KEY"] = payload.gemini_key.strip()
        os.environ["GEMINI_API_KEY"] = payload.gemini_key.strip()

    if payload.gemini_fallback_text is not None:
        lines = [l.strip() for l in payload.gemini_fallback_text.splitlines() if l.strip()]
        sec["api_keys"]["ERIS_GEMINI_FALLBACK_API_KEYS"] = lines

    if payload.firebase_key is not None and payload.firebase_key.strip():
        sec["api_keys"]["FIREBASE_API_KEY"] = payload.firebase_key.strip()
        os.environ["FIREBASE_API_KEY"] = payload.firebase_key.strip()

    if payload.deepseek_key is not None and payload.deepseek_key.strip():
        sec["api_keys"]["DEEPSEEK_API_KEY"] = payload.deepseek_key.strip()
        os.environ["DEEPSEEK_API_KEY"] = payload.deepseek_key.strip()

    save_secrets_dict(sec)
    return {"message": "Đã lưu cài đặt API Keys vào config/secrets.json thành công"}


@app.post("/api/secrets/test-groq")
def test_groq(payload: Optional[TestKeyPayload] = None):
    sec = load_secrets_dict()
    key = ""
    if payload and payload.key and payload.key.strip():
        key = payload.key.strip()
    else:
        key = sec.get("api_keys", {}).get("ERIS_GROQ_API_KEY") or os.environ.get("ERIS_GROQ_API_KEY", "")

    if not key.strip():
        raise HTTPException(status_code=400, detail="Chưa có Groq API Key để test")

    t0 = time.time()
    try:
        r = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {key.strip()}"},
            timeout=6.0,
        )
        latency = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            data = r.json()
            models_data = data.get("data", [])
            gemma_models = [m.get("id") for m in models_data if "gemma" in (m.get("id") or "").lower()]
            other_models = [m.get("id") for m in models_data if "llama" in (m.get("id") or "").lower()][:4]
            return {
                "ok": True,
                "latency_ms": latency,
                "message": f"Groq API kết nối thành công ({latency}ms)!",
                "gemma_models": gemma_models,
                "other_models": other_models,
            }
        return {"ok": False, "latency_ms": latency, "error": f"Lỗi HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/secrets/test-gemini")
def test_gemini(payload: Optional[TestKeyPayload] = None):
    sec = load_secrets_dict()
    key = ""
    if payload and payload.key and payload.key.strip():
        key = payload.key.strip()
    else:
        key = sec.get("api_keys", {}).get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")

    if not key.strip():
        raise HTTPException(status_code=400, detail="Chưa có Gemini API Key để test")

    t0 = time.time()
    try:
        r = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key.strip()}",
            timeout=6.0,
        )
        latency = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            data = r.json()
            models_data = data.get("models", [])
            gemma_models = [
                m.get("name", "").replace("models/", "")
                for m in models_data
                if "gemma" in (m.get("name") or "").lower()
            ]
            gemini_models = [
                m.get("name", "").replace("models/", "")
                for m in models_data
                if "gemini" in (m.get("name") or "").lower()
            ][:4]
            return {
                "ok": True,
                "latency_ms": latency,
                "message": f"Google AI Studio kết nối thành công ({latency}ms)!",
                "gemma_models": gemma_models,
                "gemini_models": gemini_models,
            }
        return {"ok": False, "latency_ms": latency, "error": f"Lỗi HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
# ── Gemma & Cloud Keys Pool DB API ──────────────────────────────────────────

class AddCloudKeyPayload(BaseModel):
    provider: str  # 'gemini' | 'groq'
    api_key: Optional[str] = None
    label: Optional[str] = ""
    batch_text: Optional[str] = None


class KeyStatusPayload(BaseModel):
    status: str
    cooldown_seconds: Optional[float] = 0.0


@app.get("/api/cloud-keys")
def get_cloud_keys():
    from runtime.cloud_keys_pool_db import CloudKeysPoolDB
    db = CloudKeysPoolDB()
    db.seed_from_secrets_if_empty()
    gemini_keys = [k.to_dict() for k in db.list_keys("gemini")]
    groq_keys = [k.to_dict() for k in db.list_keys("groq")]
    all_keys = gemini_keys + groq_keys
    return {
        "gemini_keys": gemini_keys,
        "groq_keys": groq_keys,
        "total_keys": len(all_keys),
        "active_keys": sum(1 for k in all_keys if k["is_available"]),
    }


@app.post("/api/cloud-keys")
def add_cloud_key(payload: AddCloudKeyPayload):
    from runtime.cloud_keys_pool_db import CloudKeysPoolDB
    db = CloudKeysPoolDB()
    prov = payload.provider.strip().lower()
    if prov not in ("gemini", "groq"):
        raise HTTPException(status_code=400, detail="Provider chỉ hỗ trợ 'gemini' hoặc 'groq'")

    if payload.batch_text and payload.batch_text.strip():
        res = db.batch_import(prov, payload.batch_text, label_prefix=payload.label or "")
        db.sync_to_secrets()
        db.export_jsonl()
        return {"message": f"Đã nạp hàng loạt: thêm mới {res['added']}, cập nhật {res['updated']} key", "result": res}

    if not payload.api_key or not payload.api_key.strip():
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp API Key hoặc danh sách batch_text")

    try:
        key_id = db.add_key(prov, payload.api_key.strip(), label=payload.label or "")
        db.sync_to_secrets()
        db.export_jsonl()
        return {"message": "Đã thêm API Key thành công vào SQLite DB", "key_id": key_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/cloud-keys/{key_id}/test")
def test_cloud_key_by_id(key_id: int):
    from runtime.cloud_keys_pool_db import CloudKeysPoolDB
    db = CloudKeysPoolDB()
    k = db.get_key_by_id(key_id)
    if not k:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy Key ID {key_id}")

    t0 = time.time()
    try:
        if k.provider == "gemini":
            r = requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={k.api_key.strip()}",
                timeout=6.0,
            )
            latency = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                data = r.json()
                models_data = data.get("models", [])
                gemma_models = [
                    m.get("name", "").replace("models/", "")
                    for m in models_data
                    if "gemma" in (m.get("name") or "").lower()
                ]
                models_str = ", ".join(gemma_models)
                db.update_key_metrics(k.id, latency_ms=latency, models_supported=models_str)
                db.update_key_status(k.id, "active")
                return {
                    "ok": True,
                    "latency_ms": latency,
                    "message": f"Google AI Studio kết nối thành công ({latency}ms)!",
                    "gemma_models": gemma_models,
                }
            elif r.status_code == 429:
                db.mark_rate_limited(k.id, cooldown_seconds=60)
                return {"ok": False, "latency_ms": latency, "error": "HTTP 429: Bị giới hạn tần suất (đã đưa vào hạ nhiệt 60s)"}
            else:
                db.update_key_metrics(k.id, latency_ms=latency, is_error=True)
                return {"ok": False, "latency_ms": latency, "error": f"Lỗi HTTP {r.status_code}: {r.text[:200]}"}

        elif k.provider == "groq":
            r = requests.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {k.api_key.strip()}"},
                timeout=6.0,
            )
            latency = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                data = r.json()
                models_data = data.get("data", [])
                gemma_models = [m.get("id") for m in models_data if "gemma" in (m.get("id") or "").lower()]
                models_str = ", ".join(gemma_models)
                db.update_key_metrics(k.id, latency_ms=latency, models_supported=models_str)
                db.update_key_status(k.id, "active")
                return {
                    "ok": True,
                    "latency_ms": latency,
                    "message": f"Groq API kết nối thành công ({latency}ms)!",
                    "gemma_models": gemma_models,
                }
            elif r.status_code == 429:
                db.mark_rate_limited(k.id, cooldown_seconds=60)
                return {"ok": False, "latency_ms": latency, "error": "HTTP 429: Bị giới hạn tần suất (đã đưa vào hạ nhiệt 60s)"}
            else:
                db.update_key_metrics(k.id, latency_ms=latency, is_error=True)
                return {"ok": False, "latency_ms": latency, "error": f"Lỗi HTTP {r.status_code}: {r.text[:200]}"}

        else:
            raise HTTPException(status_code=400, detail="Provider không hỗ trợ")

    except Exception as exc:
        db.update_key_metrics(k.id, is_error=True)
        return {"ok": False, "error": str(exc)}


@app.post("/api/cloud-keys/{key_id}/status")
def toggle_cloud_key_status(key_id: int, payload: KeyStatusPayload):
    from runtime.cloud_keys_pool_db import CloudKeysPoolDB
    db = CloudKeysPoolDB()
    success = db.update_key_status(key_id, payload.status, cooldown_seconds=payload.cooldown_seconds or 0.0)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy key để cập nhật")
    db.sync_to_secrets()
    db.export_jsonl()
    return {"message": f"Đã cập nhật trạng thái key #{key_id} thành '{payload.status}'"}


@app.delete("/api/cloud-keys/{key_id}")
def delete_cloud_key(key_id: int):
    from runtime.cloud_keys_pool_db import CloudKeysPoolDB
    db = CloudKeysPoolDB()
    success = db.delete_key(key_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy key để xoá")
    db.sync_to_secrets()
    db.export_jsonl()
    return {"message": f"Đã xoá key #{key_id} khỏi database"}


@app.post("/api/cloud-keys/sync")
def sync_cloud_keys():
    from runtime.cloud_keys_pool_db import CloudKeysPoolDB
    db = CloudKeysPoolDB()
    db.sync_to_secrets()
    jsonl_path = db.export_jsonl()
    return {
        "message": f"Đã đồng bộ xoay vòng vào config/secrets.json và xuất file JSONL: {jsonl_path.name}",
        "jsonl_file": str(jsonl_path),
    }


@app.get("/api/cloud-keys/export-jsonl")
def export_cloud_keys_jsonl():
    from runtime.cloud_keys_pool_db import CloudKeysPoolDB
    from fastapi.responses import PlainTextResponse
    db = CloudKeysPoolDB()
    jsonl_path = db.export_jsonl()
    content = jsonl_path.read_text(encoding="utf-8")
    return PlainTextResponse(content, media_type="application/x-jsonlines")

# ── System Control API ───────────────────────────────────────────────────────

@app.get("/api/system/ports")
def check_ports():
    statuses = []
    for name, port in PORTS_TO_CHECK.items():
        listening = port_listening(port)
        statuses.append({
            "name": name,
            "port": port,
            "listening": listening,
            "status": "Online" if listening else "Offline",
        })
    return statuses


@app.post("/api/system/start-eris")
def start_eris():
    bat = _PROJECT_ROOT / "run_eris.bat"
    if not bat.is_file():
        raise HTTPException(status_code=404, detail="run_eris.bat không tìm thấy")
    try:
        subprocess.Popen(f'cmd.exe /c start "Eris AI" cmd /k "{bat}"', cwd=str(_PROJECT_ROOT), shell=True)
        return {"message": "Đã gửi lệnh khởi động Eris"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/system/stop-eris")
def stop_eris():
    terminated = 0
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or []).lower()
            name = (p.info.get("name") or "").lower()
            if "vision.py" in cmd or name in ("eris.exe", "godot.exe"):
                p.terminate()
                terminated += 1
        except Exception:
            pass
    return {"message": f"Đã dừng {terminated} tiến trình Eris"}


# Mount static files cho Web UI
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)

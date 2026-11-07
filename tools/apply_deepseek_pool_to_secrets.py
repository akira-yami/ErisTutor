from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any


ROLE_TO_SECRET = {
    "companion": "ERIS_DS_COMPANION_API_KEY",
    "surface": "ERIS_DS_SURFACE_API_KEY",
    "seed": "ERIS_DS_SEED_API_KEY",
    "executive": "ERIS_DS_EXECUTIVE_API_KEY",
    "curator": "ERIS_DS_CURATOR_API_KEY",
    "observer": "ERIS_DS_OBSERVER_API_KEY",
    "critic": "ERIS_DS_CRITIC_API_KEY",
    "inner": "ERIS_DS_INNER_API_KEY",
}

ROLE_HINTS = {
    "companion": ("companion", "main", "brain", "chat"),
    "surface": ("surface", "public", "reply", "wording"),
    "seed": ("seed", "random", "reservoir"),
    "executive": ("executive", "execute", "cortex"),
    "curator": ("curator", "thu thu", "thuthu", "memory", "librarian"),
    "observer": ("observer", "observe"),
    "critic": ("critic", "format", "repair", "style", "speech"),
    "inner": ("inner", "diary", "reflection"),
}

RETIRED_API_KEYS = (
    "ERIS_DS_STYLE_API_KEY",
    "ERIS_DS_TUTOR_API_KEY",
)

RETIRED_ENV_KEYS = (
    "ERIS_DS_STYLE_BASE_URL",
    "ERIS_DS_TUTOR_BASE_URL",
)

DEFAULT_ENV = {
    "ERIS_REMOTE_API_ENABLED": "1",
    "ERIS_REMOTE_PROVIDER": "deepseek_web",
    "ERIS_REMOTE_MODEL": "deepseek-v4-flash",
    "ERIS_DS2API_ENABLED": "1",
    "ERIS_DS2API_PORTS": "6011,6012,6013,6014,6016",
    "ERIS_DS_MODEL": "deepseek-v4-flash",
    "ERIS_DS_SURFACE_ALLOW_SHARED_KEY": "0",
    "ERIS_DS_COMPANION_BASE_URL": "http://127.0.0.1:6011/v1",
    "ERIS_DS_SURFACE_BASE_URL": "http://127.0.0.1:6012/v1",
    "ERIS_DS_OBSERVER_BASE_URL": "http://127.0.0.1:6013/v1",
    "ERIS_DS_CRITIC_BASE_URL": "http://127.0.0.1:6014/v1",
    "ERIS_DS_INNER_BASE_URL": "http://127.0.0.1:6014/v1",
    "ERIS_DS_CURATOR_BASE_URL": "http://127.0.0.1:6014/v1",
    "ERIS_DS_SEED_BASE_URL": "http://127.0.0.1:6015/v1",
    "ERIS_DS_EXECUTIVE_BASE_URL": "http://127.0.0.1:6016/v1",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def strip_accents(value: str) -> str:
    text = unicodedata.normalize("NFD", value or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.lower()


def mask_key(value: str) -> str:
    value = str(value or "").strip()
    if len(value) <= 12:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def find_db(root: Path) -> Path | None:
    env_path = os.environ.get("WHALE2API_DATABASE_PATH", "").strip()
    candidates: list[Path] = []
    if env_path:
        p = Path(env_path)
        candidates.append(p if p.is_absolute() else root / "tools" / "whale2api" / p)
    candidates.extend(
        [
            root / "tools" / "whale2api" / "docker-data" / "whale2api" / "whale2api.db",
            root / "tools" / "whale2api" / "whale2api.db",
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def ensure_secrets(root: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    secrets = config_dir / "secrets.json"
    example = config_dir / "secrets.example.json"
    if not secrets.exists():
        if not example.is_file():
            raise FileNotFoundError("Missing config/secrets.example.json")
        shutil.copy2(example, secrets)
        print("[OK] Created config\\secrets.json from template.")
    return secrets


def read_gateway_keys(db_path: Path) -> list[dict[str, str]]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT api_key, name, remark, enabled
            FROM gateway_api_keys
            WHERE COALESCE(enabled, 1) = 1
            ORDER BY created_at ASC
            """
        ).fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "api_key": str(row["api_key"] or "").strip(),
                "name": str(row["name"] or "").strip(),
                "remark": str(row["remark"] or "").strip(),
            }
        )
    return [item for item in out if item["api_key"]]


def detect_role(row: dict[str, str]) -> str:
    hay = strip_accents(f"{row.get('name', '')} {row.get('remark', '')}")
    for role, hints in ROLE_HINTS.items():
        if any(hint in hay for hint in hints):
            return role
    return ""


def main() -> int:
    root = project_root()
    db_path = find_db(root)
    if db_path is None:
        print("[ERROR] Khong tim thay Whale2API database.")
        print(r"Hay mo DeepSeek Pool UI truoc: run_deepseek_pool_ui.bat")
        return 1

    try:
        rows = read_gateway_keys(db_path)
    except sqlite3.Error as exc:
        print(f"[ERROR] Khong doc duoc database Pool UI: {exc}")
        return 1

    if not rows:
        print("[ERROR] Pool UI chua co Gateway Key nao.")
        print("Mo DeepSeek Pool UI -> + Tao moi -> dat ten pool theo role.")
        return 1

    secrets_path = ensure_secrets(root)
    data = load_json(secrets_path)
    api_keys = data.setdefault("api_keys", {})
    env = data.setdefault("env", {})
    if not isinstance(api_keys, dict) or not isinstance(env, dict):
        print("[ERROR] config\\secrets.json sai format; can co object api_keys va env.")
        return 1

    removed_retired = 0
    for key in RETIRED_API_KEYS:
        if key in api_keys:
            api_keys.pop(key, None)
            removed_retired += 1
    for key in RETIRED_ENV_KEYS:
        if key in env:
            env.pop(key, None)
            removed_retired += 1

    for key, value in DEFAULT_ENV.items():
        env.setdefault(key, value)

    role_rows: dict[str, dict[str, str]] = {}
    skipped = []
    duplicate_roles = []
    retired_markers = {strip_accents(key) for key in RETIRED_API_KEYS}
    for row in rows:
        label = strip_accents(f"{row.get('name', '')} {row.get('remark', '')}")
        if any(marker in label for marker in retired_markers):
            title = row["name"] or row["remark"] or "(khong ten)"
            print(f"[SKIP] retired pool {title}; dung role canonical thay the.")
            continue
        role = detect_role(row)
        if not role:
            skipped.append(row)
            continue
        existing = role_rows.get(role)
        if existing is None:
            role_rows[role] = row
            continue
        secret_name = ROLE_TO_SECRET[role]
        exact_new = strip_accents(row.get("name", "")) == strip_accents(secret_name)
        exact_existing = strip_accents(existing.get("name", "")) == strip_accents(secret_name)
        if exact_new and not exact_existing:
            duplicate_roles.append(existing)
            role_rows[role] = row
        else:
            duplicate_roles.append(row)

    mapped = 0
    for role, row in role_rows.items():
        secret_name = ROLE_TO_SECRET[role]
        current = str(api_keys.get(secret_name) or "").strip()
        if current and current != row["api_key"]:
            print(f"[UPDATE] {secret_name} {mask_key(current)} -> {mask_key(row['api_key'])}")
        api_keys[secret_name] = row["api_key"]
        mapped += 1
        title = row["name"] or row["remark"] or role
        print(f"[OK] {title} -> {secret_name} ({mask_key(row['api_key'])})")

    secrets_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print()
    print(f"[OK] Updated {mapped} DeepSeek pool key(s) in config\\secrets.json")
    if removed_retired:
        print(f"[OK] Removed {removed_retired} retired DeepSeek config field(s).")
    if duplicate_roles:
        print()
        print("[WARN] Cac pool trung role da duoc bo qua:")
        for row in duplicate_roles:
            label = row["name"] or row["remark"] or "(khong ten)"
            print(f"       - {label}")
    if skipped:
        print()
        print("[WARN] Cac pool chua nhan ra role, hay doi Ten Pool/Remark thanh:")
        print("       companion, surface, seed, executive, curator, observer, critic, inner")
        for row in skipped:
            label = row["name"] or row["remark"] or "(khong ten)"
            print(f"       - {label}")
    return 0 if mapped else 1


if __name__ == "__main__":
    raise SystemExit(main())

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_LOGGER = logging.getLogger("Config")

# Theo dõi nguồn thắng của mỗi biến config: "os-env" | ".env" | "secrets".
_CONFIG_SOURCE: dict[str, str] = {}
_ENV_FILE_LOADED = False


def _versioned_secrets_path() -> Path | None:
    """Trả path version cụ thể khi manifest đã được cấu hình."""
    try:
        from secret_versions import SecretVersionManager
        manager = SecretVersionManager()
        return manager.active_path()
    except Exception:
        return None


def _resolve_secrets_path() -> Path:
    versioned = _versioned_secrets_path()
    if versioned is not None:
        return versioned
    # Ban export (PyInstaller): __file__ nam trong bundle nen parents[1] sai.
    # C# truyen ERIS_PROJECT_ROOT = thu muc chua .exe; uu tien dung no.
    env_root = os.environ.get("ERIS_PROJECT_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root) / "config" / "secrets.json"
        if candidate.is_file():
            return candidate
    return _PROJECT_ROOT / "config" / "secrets.json"


_SECRETS_PATH = _resolve_secrets_path()


def _resolve_env_file_path() -> Path:
    env_root = os.environ.get("ERIS_PROJECT_ROOT", "").strip()
    if env_root:
        candidate = Path(env_root) / ".env"
        if candidate.is_file():
            return candidate
    return _PROJECT_ROOT / ".env"


def _parse_env_file(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lstrip("\ufeff")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def load_env_file(*, force: bool = False) -> dict[str, str]:
    """Nạp ``.env`` vào ``os.environ`` với precedence: OS-env > .env > secrets.

    Chỉ set biến CHƯA có trong ``os.environ`` (real OS env luôn thắng). Chạy MỘT
    lần (idempotent) trừ khi ``force=True``. Bỏ qua khi ``ERIS_DISABLE_DOTENV``
    bật (dùng để cô lập test). Không bao giờ raise.
    """

    global _ENV_FILE_LOADED
    if _ENV_FILE_LOADED and not force:
        return {}
    _ENV_FILE_LOADED = True

    if (os.environ.get("ERIS_DISABLE_DOTENV", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return {}

    path = _resolve_env_file_path()
    if not path.is_file():
        return {}
    try:
        parsed = _parse_env_file(path.read_text(encoding="utf-8"))
    except OSError:
        return {}

    applied: dict[str, str] = {}
    for key, value in parsed.items():
        if key in os.environ:
            _CONFIG_SOURCE.setdefault(key, "os-env")
            continue
        os.environ[key] = value
        _CONFIG_SOURCE[key] = ".env"
        applied[key] = value

    if applied:
        _LOGGER.info("[CONFIG] loaded %d setting(s) from %s", len(applied), path)
    return applied


def refresh_runtime_preferences(*names: str) -> dict[str, str]:
    """Nạp lại các preference do UI/.env quản lý ngay lúc runtime đang chạy.

    Launcher có thể chèn default vào ``os.environ`` trước khi Python khởi động,
    nên ``load_env_file`` đúng chuẩn OS-env precedence sẽ không ghi đè được. Với
    các preference được gọi đích danh (ví dụ ``ERIS_TTS_LANGUAGE``), file .env là
    nguồn cấu hình người dùng và được phép thắng default của launcher. API key và
    secret không đi qua hàm này.
    """

    requested = tuple(dict.fromkeys(str(name or "").strip() for name in names if str(name or "").strip()))
    if not requested:
        return {}
    if (os.environ.get("ERIS_DISABLE_DOTENV", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return {}

    path = _resolve_env_file_path()
    if not path.is_file():
        return {}
    try:
        # utf-8-sig nuốt BOM chuẩn; _parse_env_file vẫn lstrip BOM để phòng text
        # được truyền từ nguồn khác. Con người đã đủ việc, không cần thêm ký tự ma.
        parsed = _parse_env_file(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return {}

    applied: dict[str, str] = {}
    for name in requested:
        if name not in parsed:
            continue
        value = str(parsed[name])
        os.environ[name] = value
        _CONFIG_SOURCE[name] = ".env(runtime-preference)"
        applied[name] = value
    return applied


def config_source(name: str) -> str:
    """Nguồn thắng của một biến config: ".env" | "secrets" | "os-env" | "default"."""

    if name in _CONFIG_SOURCE:
        return _CONFIG_SOURCE[name]
    if name in os.environ:
        return "os-env"
    return "default"


def log_config_sources(*names: str) -> None:
    """Log nguồn thắng của các biến quan trọng để debug R3/E7."""

    for name in names:
        _LOGGER.info("[CONFIG] %s=%r source=%s", name, os.environ.get(name, ""), config_source(name))


@lru_cache(maxsize=1)
def _load_secrets() -> dict[str, Any]:
    path = _resolve_secrets_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _lookup(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()

    for section_name in ("api_keys", "env", "secrets"):
        section = payload.get(section_name)
        if not isinstance(section, dict):
            continue
        value = section.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _lookup_path(payload: dict[str, Any], path: str, *, default: Any = None) -> Any:
    if not path:
        return default

    current: Any = payload
    for part in str(path).split("."):
        if not isinstance(current, dict):
            return default
        current = current.get(part)
        if current is None:
            return default
    return current


def _provider_slot_payload(payload: dict[str, Any], provider: str, slot: str) -> dict[str, Any]:
    providers = payload.get("providers")
    if not isinstance(providers, dict):
        return {}
    provider_payload = providers.get(str(provider).strip().lower())
    if not isinstance(provider_payload, dict):
        return {}

    slot_name = str(slot or "default").strip()
    direct = provider_payload.get(slot_name)
    if isinstance(direct, dict):
        return direct

    # Backward-safe: cho phép providers.gemini = {"api_key": "...", ...}
    if any(key in provider_payload for key in ("api_key", "base_url", "model", "organization", "project")):
        return provider_payload
    return {}


def get_secret(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value

    payload = _load_secrets()
    for name in names:
        value = _lookup(payload, name)
        if value:
            return value

    return default


def get_config(path: str, default: Any = None) -> Any:
    """Đọc config lồng nhau từ env hoặc secrets.json.

    Ưu tiên env nếu path không có dấu chấm hoặc nếu biến env cùng tên tồn tại.
    Ví dụ:
    - get_config("ERIS_REMOTE_MODEL")
    - get_config("subconscious_runtime.lanes.lane3.model")
    """

    env_value = os.environ.get(path, None)
    if isinstance(env_value, str) and env_value.strip():
        return env_value.strip()

    payload = _load_secrets()
    if "." not in path:
        direct = _lookup(payload, path)
        if direct:
            return direct

    value = _lookup_path(payload, path, default=default)
    return value


def get_provider_profile(provider: str, *, slot: str = "default", default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lấy cấu hình provider theo slot riêng.

    Ví dụ:
    - get_provider_profile("gemini", slot="remote_main")
    - get_provider_profile("openai", slot="lane3_synthesis")
    """

    payload = _load_secrets()
    profile = _provider_slot_payload(payload, provider, slot)
    if profile:
        return dict(profile)
    return dict(default or {})


def get_agent_profile(
    agent_name: str,
    *,
    default_provider: str = "",
    default_slot: str = "default",
    default: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve provider profile cho một job/agent cụ thể.

    Schema:
    {
      "agent_keys": {
        "get_weather": {"provider": "gemini", "slot": "weather"}
      }
    }
    """

    payload = _load_secrets()
    agent_keys = payload.get("agent_keys")
    provider = default_provider
    slot = default_slot
    if isinstance(agent_keys, dict):
        agent_payload = agent_keys.get(agent_name)
        if isinstance(agent_payload, dict):
            provider = str(agent_payload.get("provider") or provider or "").strip().lower()
            slot = str(agent_payload.get("slot") or slot or "default").strip()
            direct_profile = agent_payload.get("profile")
            if isinstance(direct_profile, dict):
                return dict(direct_profile)

    if provider:
        profile = get_provider_profile(provider, slot=slot, default=default)
        if profile:
            merged = dict(profile)
            merged.setdefault("provider", provider)
            merged.setdefault("slot", slot)
            return merged
    return dict(default or {})


def reload_secrets() -> None:
    """Xóa cache sau activate/rollback secret version."""
    _load_secrets.cache_clear()


def apply_secrets_to_environ() -> None:
    # Nạp .env trước (precedence: OS-env > .env > secrets) rồi mới đổ secrets.
    load_env_file()
    payload = _load_secrets()
    sections: list[dict[str, Any]] = [payload]
    for section_name in ("api_keys", "env", "secrets"):
        section = payload.get(section_name)
        if isinstance(section, dict):
            sections.append(section)

    for section in sections:
        for key, value in section.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            if key and value.strip() and not os.environ.get(key):
                os.environ[key] = value.strip()
                _CONFIG_SOURCE[key] = "secrets"

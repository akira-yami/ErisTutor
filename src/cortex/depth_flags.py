# -*- coding: utf-8 -*-
"""
cortex/depth_flags.py
=====================

Một nguồn đọc cờ chiều-sâu (N1–N3) cho phép Panel bật/tắt LIVE mà không restart.

Vấn đề: các helper cờ đọc ``os.getenv`` mỗi lần gọi — tốt cho live-trong-process,
nhưng Panel chạy *process khác*, không thể đổi env của runtime Eris. Giải: một
control-file JSON dùng chung (``memory_core/depth_flags.json``). Cả Panel (ghi) lẫn
runtime (đọc) cùng nhìn file này. Cache theo mtime nên đọc gần như free.

Thứ tự ưu tiên khi quyết một cờ:
  1. control-file có khóa đó  -> dùng giá trị control-file (Panel đang điều khiển),
  2. ngược lại                -> ``os.getenv`` (hành vi cũ, .env / shell).

Mặc định mọi cờ TẮT khi cả hai vắng. Mọi lỗi -> fallback env, không bao giờ ném.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

# Đăng ký cờ: khóa thân thiện -> tên biến môi trường.
KNOWN_FLAGS: dict[str, str] = {
    "emotional_consequence": "ERIS_EMOTIONAL_CONSEQUENCE",
    "development_growth": "ERIS_DEVELOPMENT_GROWTH_ENABLED",
    "interior_surface": "ERIS_INTERIOR_SURFACE_NONANCHORED",
    "private_mind": "ERIS_PRIVATE_MIND_API_ENABLED",
}

# Cờ chiều-sâu nên SỐNG mặc định: người dùng không cần bật tay nữa. Runtime sẽ
# `bootstrap_defaults()` lúc khởi động để ghi các khóa còn thiếu vào control-file.
# Một khi đã ghi, lựa chọn tắt/bật của người dùng thắng (không seed đè nữa).
DEFAULT_ON_FLAGS: tuple[str, ...] = tuple(KNOWN_FLAGS.keys())

_TRUE = {"1", "true", "yes", "on"}
_lock = threading.RLock()
_cache: dict[str, Any] = {"mtime": -1.0, "data": {}}


def control_file_path() -> Path:
    raw = os.getenv("ERIS_DEPTH_FLAGS_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2] / "memory_core" / "depth_flags.json"


def _load_control() -> dict[str, Any]:
    """Đọc control-file, cache theo mtime. Trả {} nếu vắng/hỏng."""
    path = control_file_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        with _lock:
            _cache["mtime"] = -1.0
            _cache["data"] = {}
        return {}
    with _lock:
        if mtime == _cache["mtime"]:
            return dict(_cache["data"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    with _lock:
        _cache["mtime"] = mtime
        _cache["data"] = data
    return dict(data)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE


def flag_enabled(env_name: str, *, default: bool = False) -> bool:
    """Cờ bật? control-file > env > default. Không bao giờ ném."""
    try:
        control = _load_control()
        if env_name in control:
            return _coerce_bool(control[env_name])
        raw = os.getenv(env_name)
        if raw is None or not str(raw).strip():
            return default
        return str(raw).strip().lower() in _TRUE
    except Exception:
        return default


def all_states() -> dict[str, bool]:
    """Trạng thái hiện tại của mọi cờ đã đăng ký (cho Panel hiển thị)."""
    return {key: flag_enabled(env) for key, env in KNOWN_FLAGS.items()}


def set_flags(updates: dict[str, bool]) -> dict[str, bool]:
    """Ghi đè control-file cho các khóa thân thiện đưa vào. Trả trạng thái mới.

    ``updates`` dùng khóa thân thiện; chỉ khóa hợp lệ được nhận.
    """
    path = control_file_path()
    control = _load_control()
    for key, value in (updates or {}).items():
        env = KNOWN_FLAGS.get(str(key))
        if env is None:
            continue
        control[env] = bool(value)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(control, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        with _lock:
            _cache["mtime"] = path.stat().st_mtime
            _cache["data"] = control
    except Exception:
        pass
    return all_states()


def bootstrap_defaults(*, flags: tuple[str, ...] = DEFAULT_ON_FLAGS) -> dict[str, bool]:
    """Tự BẬT các cờ chiều-sâu lúc khởi động — người dùng không phải gạt tay.

    Chỉ seed những khóa CHƯA có trong control-file: nếu người dùng từng tắt một cờ,
    lựa chọn đó được giữ nguyên (key đã tồn tại nên không bị đè). Tắt toàn bộ hành vi
    này bằng ``ERIS_DEPTH_AUTO_ENABLE=0``. Không bao giờ ném.

    Trả về trạng thái mới của mọi cờ.
    """
    try:
        if str(os.getenv("ERIS_DEPTH_AUTO_ENABLE", "1")).strip().lower() not in _TRUE:
            return all_states()
        control = _load_control()
        seed: dict[str, bool] = {}
        for key in flags:
            env = KNOWN_FLAGS.get(str(key))
            if env is None:
                continue
            if env not in control:  # chưa có lựa chọn nào -> bật mặc định
                seed[key] = True
        if seed:
            return set_flags(seed)
    except Exception:
        pass
    return all_states()

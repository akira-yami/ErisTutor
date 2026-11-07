# -*- coding: utf-8 -*-
"""Cấu hình nhỏ, không lôi toàn bộ settings cognition vào renderer."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "off", "no", "none"}


DEFAULT_LIVE2D_SCALE = 0.85
MIN_LIVE2D_SCALE = 0.55
MAX_LIVE2D_SCALE = 1.35


def read_persisted_scale() -> float | None:
    """Đọc scale Live2D đã lưu từ phiên chạy trước nếu có, đảm bảo kích thước ổn định."""
    targets: list[Path] = []
    state_dir = os.getenv("ERIS_STATE_DIR")
    if state_dir:
        targets.append(Path(state_dir) / "live2d_scale.txt")
    appdata = os.getenv("APPDATA")
    if appdata:
        targets.append(Path(appdata) / "Godot" / "app_userdata" / "Eris" / "live2d_scale.txt")
    project_root = os.getenv("ERIS_PROJECT_ROOT")
    if project_root:
        targets.append(Path(project_root) / ".eris_state" / "live2d_scale.txt")
    for path in targets:
        try:
            if path.is_file():
                val = float(path.read_text(encoding="utf-8").strip())
                if MIN_LIVE2D_SCALE <= val <= MAX_LIVE2D_SCALE:
                    return val
                return DEFAULT_LIVE2D_SCALE
        except Exception:
            pass
    return DEFAULT_LIVE2D_SCALE


def save_persisted_scale(scale: float) -> None:
    """Lưu scale Live2D để giữ nguyên khi khởi động lại Eris."""
    clamped_scale = max(MIN_LIVE2D_SCALE, min(MAX_LIVE2D_SCALE, float(scale)))
    targets: list[Path] = []
    state_dir = os.getenv("ERIS_STATE_DIR")
    if state_dir:
        targets.append(Path(state_dir) / "live2d_scale.txt")
    appdata = os.getenv("APPDATA")
    if appdata:
        targets.append(Path(appdata) / "Godot" / "app_userdata" / "Eris" / "live2d_scale.txt")
    project_root = os.getenv("ERIS_PROJECT_ROOT")
    if project_root:
        targets.append(Path(project_root) / ".eris_state" / "live2d_scale.txt")
    for path in targets:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{clamped_scale:.3f}", encoding="utf-8")
        except Exception:
            pass


@dataclass(frozen=True, slots=True)
class Live2DBodyConfig:
    model_path: Path
    listen_host: str = "127.0.0.1"
    listen_port: int = 4252
    backend_host: str = "127.0.0.1"
    backend_port: int = 4243
    body_host: str = "127.0.0.1"
    body_port: int = 4242
    width: int = 600
    height: int = 960
    scale: float = DEFAULT_LIVE2D_SCALE
    offset_x: float = 0.0
    offset_y: float = -0.02
    transparent: bool = True
    topmost: bool = True
    decorated: bool = False
    x: int | None = None
    y: int | None = None
    godot_pid: int | None = None
    eye_tracking: bool = True
    eye_strength_x: float = 0.44
    eye_strength_y: float = 0.30
    eye_right_x_sign: float = -1.0
    eye_inner_ratio: float = 0.82
    head_follow_degrees: float = 2.4
    head_follow_left_degrees: float = 4.8
    head_follow_right_degrees: float = 4.8
    face_anchor_y: float = 0.17
    z_order_sync: bool = True

    @classmethod
    def from_env(cls, *, root: Path | None = None) -> "Live2DBodyConfig":
        project_root = root or Path(__file__).resolve().parents[1]
        default_model = project_root / "assets" / "live2d" / "infj" / "infj.model3.json"
        model_path = Path(os.getenv("ERIS_LIVE2D_MODEL", str(default_model))).expanduser()
        persisted_scale = read_persisted_scale()
        default_scale = persisted_scale if persisted_scale is not None else DEFAULT_LIVE2D_SCALE
        return cls(
            model_path=model_path,
            listen_host=os.getenv("ERIS_LIVE2D_HOST", "127.0.0.1").strip() or "127.0.0.1",
            listen_port=max(1, min(65535, _int_env("ERIS_LIVE2D_PORT", 4252))),
            backend_host=os.getenv("ERIS_LIVE2D_BACKEND_HOST", "127.0.0.1").strip() or "127.0.0.1",
            backend_port=max(1, min(65535, _int_env("ERIS_LIVE2D_BACKEND_PORT", 4243))),
            body_host=os.getenv("ERIS_LIVE2D_BODY_HOST", "127.0.0.1").strip() or "127.0.0.1",
            body_port=max(1, min(65535, _int_env("ERIS_LIVE2D_BODY_PORT", 4242))),
            width=max(400, _int_env("ERIS_LIVE2D_WIDTH", 600)),
            height=max(600, _int_env("ERIS_LIVE2D_HEIGHT", 960)),
            scale=max(0.55, min(1.35, _float_env("ERIS_LIVE2D_SCALE", default_scale))),
            offset_x=max(-2.0, min(2.0, _float_env("ERIS_LIVE2D_OFFSET_X", 0.0))),
            offset_y=max(-2.0, min(2.0, _float_env("ERIS_LIVE2D_OFFSET_Y", -0.02))),
            transparent=_bool_env("ERIS_LIVE2D_TRANSPARENT", True),
            topmost=_bool_env("ERIS_LIVE2D_TOPMOST", True),
            decorated=_bool_env("ERIS_LIVE2D_DECORATED", False),
            x=_int_env("ERIS_LIVE2D_X", -1) if _int_env("ERIS_LIVE2D_X", -1) >= 0 else None,
            y=_int_env("ERIS_LIVE2D_Y", -1) if _int_env("ERIS_LIVE2D_Y", -1) >= 0 else None,
            godot_pid=_int_env("ERIS_GODOT_PID", 0) or None,
            eye_tracking=_bool_env("ERIS_LIVE2D_EYE_TRACKING", True),
            eye_strength_x=max(0.0, min(0.65, _float_env("ERIS_LIVE2D_EYE_STRENGTH_X", 0.44))),
            eye_strength_y=max(0.0, min(0.65, _float_env("ERIS_LIVE2D_EYE_STRENGTH_Y", 0.30))),
            eye_right_x_sign=-1.0 if _float_env("ERIS_LIVE2D_EYE_RIGHT_X_SIGN", -1.0) < 0.0 else 1.0,
            eye_inner_ratio=max(0.55, min(1.0, _float_env("ERIS_LIVE2D_EYE_INNER_RATIO", 0.82))),
            head_follow_degrees=max(0.0, min(8.0, _float_env("ERIS_LIVE2D_HEAD_FOLLOW_DEGREES", 2.4))),
            head_follow_left_degrees=max(0.0, min(12.0, _float_env("ERIS_LIVE2D_HEAD_FOLLOW_LEFT_DEGREES", 4.8))),
            head_follow_right_degrees=max(0.0, min(12.0, _float_env("ERIS_LIVE2D_HEAD_FOLLOW_RIGHT_DEGREES", 4.8))),
            face_anchor_y=max(0.05, min(0.45, _float_env("ERIS_LIVE2D_FACE_ANCHOR_Y", 0.17))),
            z_order_sync=_bool_env("ERIS_LIVE2D_Z_ORDER_SYNC", True),
        )

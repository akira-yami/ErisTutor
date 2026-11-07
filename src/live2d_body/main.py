# -*- coding: utf-8 -*-
"""Entry point: ``python -m live2d_body.main``."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from .config import Live2DBodyConfig
from .renderer import Live2DDependencyError, run_renderer


def _enable_windows_dpi_awareness() -> None:
    """Đặt process DPI-aware trên Windows TRƯỚC khi GLFW/OpenGL khởi tạo.

    Godot gửi tọa độ cửa sổ Live2D theo không gian pixel vật lý của desktop.
    Nếu process renderer không DPI-aware, Windows ảo hóa tọa độ theo mức scale
    của màn hình (vd 125%), khiến cùng một con số X lại trỏ sang màn hình khác
    -> model hiện lệch khỏi overlay. Bật Per-Monitor-V2 để GLFW dùng đúng tọa
    độ vật lý, khớp với hệ tọa độ Godot.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Win10 1703+).
        try:
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
        except (AttributeError, OSError):
            pass
        # Fallback Win8.1+: 2 = PROCESS_PER_MONITOR_DPI_AWARE.
        try:
            if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
                return
        except (AttributeError, OSError):
            pass
        # Fallback cuối cùng Win Vista+: system-DPI aware.
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass
    except Exception:
        # Không được để lỗi DPI chặn khởi chạy renderer.
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eris Live2D body")
    parser.add_argument("--model", type=Path, help="Đường dẫn *.model3.json")
    parser.add_argument("--port", type=int, help="UDP stage mirror port, mặc định 4252")
    parser.add_argument("--decorated", action="store_true", help="Hiện title bar để debug")
    parser.add_argument("--opaque", action="store_true", help="Tắt nền trong suốt để debug")
    parser.add_argument("--not-topmost", action="store_true", help="Không ghim cửa sổ lên trên")
    parser.add_argument("--width", type=int, help="Chiều rộng cửa sổ Live2D")
    parser.add_argument("--height", type=int, help="Chiều cao cửa sổ Live2D")
    parser.add_argument("--scale", type=float, help="Scale model")
    parser.add_argument("--offset-x", type=float, help="Offset ngang của model")
    parser.add_argument("--offset-y", type=float, help="Offset dọc của model")
    parser.add_argument("--x", type=int, help="Tọa độ X cửa sổ")
    parser.add_argument("--y", type=int, help="Tọa độ Y cửa sổ")
    return parser


def main(argv: list[str] | None = None) -> int:
    _enable_windows_dpi_awareness()
    args = build_parser().parse_args(argv)
    config = Live2DBodyConfig.from_env()
    if args.model is not None:
        config = replace(config, model_path=args.model)
    if args.port is not None:
        config = replace(config, listen_port=max(1, min(65535, args.port)))
    if args.decorated:
        config = replace(config, decorated=True)
    if args.opaque:
        config = replace(config, transparent=False)
    if args.not_topmost:
        config = replace(config, topmost=False)
    if args.width is not None:
        config = replace(config, width=max(320, args.width))
    if args.height is not None:
        config = replace(config, height=max(480, args.height))
    if args.scale is not None:
        config = replace(config, scale=max(0.2, min(3.0, args.scale)))
    if args.offset_x is not None:
        config = replace(config, offset_x=max(-2.0, min(2.0, args.offset_x)))
    if args.offset_y is not None:
        config = replace(config, offset_y=max(-2.0, min(2.0, args.offset_y)))
    if args.x is not None:
        config = replace(config, x=args.x)
    if args.y is not None:
        config = replace(config, y=args.y)
    try:
        run_renderer(config)
    except KeyboardInterrupt:
        return 0
    except (Live2DDependencyError, FileNotFoundError, RuntimeError, OSError) as exc:
        print(f"[Live2D][ERROR] {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

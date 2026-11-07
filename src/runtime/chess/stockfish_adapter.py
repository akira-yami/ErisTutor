# -*- coding: utf-8 -*-
"""Stockfish UCI adapter chạy local, không cần thư viện Python bên ngoài.

Binary KHÔNG được đóng gói cùng source. Runtime tìm theo thứ tự:
1. path cấu hình / ERIS_STOCKFISH_PATH;
2. tools/stockfish trong project;
3. lệnh ``stockfish`` có trên PATH.

Process được mở lười ở lần phân tích đầu tiên và giữ sống cho các nước sau. Một
reader thread gom stdout vào queue để timeout vẫn hoạt động trên Windows, nơi
``select`` với pipe không phải lúc nào cũng cư xử như con người mong muốn.
"""
from __future__ import annotations

import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .analyzer import AnalyzedMove, ChessAnalysis
from .engine import MATE_SCORE, Position, evaluation_label, legal_moves


class StockfishError(RuntimeError):
    """Lỗi boundary của UCI process."""


@dataclass(frozen=True, slots=True)
class StockfishConfig:
    enabled: bool = True
    path: str = ""
    threads: int = 1
    hash_mb: int = 96
    multipv: int = 3
    movetime_ms: int = 180
    on_demand_movetime_ms: int = 700
    startup_timeout_seconds: float = 4.0
    analysis_timeout_seconds: float = 2.5


@dataclass(slots=True)
class _ParsedLine:
    multipv: int = 1
    depth: int = 0
    nodes: int = 0
    score_cp_root: int | None = None
    mate_root: int | None = None
    pv: tuple[str, ...] = ()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def discover_stockfish_path(explicit: str = "") -> str:
    """Tìm Stockfish mà không tải mạng hoặc đoán tên file vô hạn."""

    candidates: list[Path] = []
    raw = str(explicit or os.getenv("ERIS_STOCKFISH_PATH", "") or "").strip()
    if raw:
        candidates.append(Path(raw).expanduser())

    root = _project_root() / "tools" / "stockfish"
    names = (
        "stockfish.exe",
        "stockfish-windows-x86-64-avx2.exe",
        "stockfish-windows-x86-64-sse41-popcnt.exe",
        "stockfish-windows-x86-64.exe",
        "stockfish",
    )
    candidates.extend(root / name for name in names)
    if root.is_dir():
        # Hỗ trợ tên có version, ví dụ stockfish-windows-x86-64-avx2-18.exe.
        candidates.extend(sorted(root.glob("stockfish*")))

    for item in candidates:
        try:
            if item.is_file():
                return str(item.resolve())
        except OSError:
            continue
    found = shutil.which("stockfish")
    return str(found or raw)


def _parse_uci_move(position: Position, uci: str):
    text = str(uci or "").strip().lower()
    for move in legal_moves(position):
        if move.uci == text:
            return move
    return None


class StockfishUCIAnalyzer:
    """Một Stockfish process dùng chung, tuần tự hóa request bằng lock."""

    def __init__(self, config: StockfishConfig, *, logger=None) -> None:
        self.config = StockfishConfig(
            enabled=bool(config.enabled),
            path=str(config.path or ""),
            threads=max(1, min(32, int(config.threads))),
            hash_mb=max(16, min(4096, int(config.hash_mb))),
            multipv=max(1, min(8, int(config.multipv))),
            movetime_ms=max(25, min(10000, int(config.movetime_ms))),
            on_demand_movetime_ms=max(50, min(30000, int(config.on_demand_movetime_ms))),
            startup_timeout_seconds=max(2.0, min(30.0, float(config.startup_timeout_seconds))),
            analysis_timeout_seconds=max(0.5, min(60.0, float(config.analysis_timeout_seconds))),
        )
        self.logger = logger
        self.path = discover_stockfish_path(self.config.path) if self.config.enabled else ""
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.RLock()
        self._ready = False
        self._last_error = ""
        self._analyses = 0
        self._restarts = 0
        self._last_depth = 0
        self._last_elapsed_ms = 0

    @property
    def available(self) -> bool:
        return bool(self.config.enabled and self.path)

    def _command(self) -> list[str]:
        # Cho phép path cấu hình chứa thêm argument trong môi trường dev/test.
        # File Windows bình thường vẫn được giữ nguyên nếu tồn tại thật.
        if Path(self.path).is_file():
            return [self.path]
        tokens = shlex.split(self.path, posix=os.name != "nt")
        return [t.strip('"\'') for t in tokens if t.strip('"\'')]

    def _start(self) -> None:
        if self._ready and self._process is not None and self._process.poll() is None:
            return
        if not self.available:
            raise StockfishError("Stockfish chưa được cấu hình hoặc không tìm thấy executable")
        self.close()

        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self._lines = queue.Queue()
        self._process = subprocess.Popen(
            self._command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        self._reader = threading.Thread(
            target=self._reader_loop,
            name="eris-stockfish-stdout",
            daemon=True,
        )
        self._reader.start()
        self._send("uci")
        self._wait_for("uciok", self.config.startup_timeout_seconds)
        self._send(f"setoption name Threads value {self.config.threads}")
        self._send(f"setoption name Hash value {self.config.hash_mb}")
        self._send("setoption name UCI_AnalyseMode value true")
        self._send("isready")
        self._wait_for("readyok", self.config.startup_timeout_seconds)
        self._ready = True
        self._restarts += 1
        self._last_error = ""

    def _reader_loop(self) -> None:
        process = self._process
        stdout = process.stdout if process is not None else None
        if stdout is None:
            self._lines.put(None)
            return
        try:
            for line in iter(stdout.readline, ""):
                self._lines.put(line.rstrip("\r\n"))
        finally:
            self._lines.put(None)

    def _send(self, command: str) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise StockfishError("Stockfish process không còn sống")
        process.stdin.write(command + "\n")
        process.stdin.flush()

    def _wait_for(self, token: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StockfishError(f"timeout chờ {token}")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty as exc:
                raise StockfishError(f"timeout chờ {token}") from exc
            if line is None:
                raise StockfishError("Stockfish đóng stdout trước khi sẵn sàng")
            if line.strip() == token or line.strip().endswith(token):
                return

    def _drain(self) -> None:
        while True:
            try:
                self._lines.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _parse_info(line: str) -> _ParsedLine | None:
        tokens = str(line or "").split()
        if not tokens or tokens[0] != "info" or "pv" not in tokens or "score" not in tokens:
            return None
        parsed = _ParsedLine()
        try:
            if "multipv" in tokens:
                parsed.multipv = max(1, int(tokens[tokens.index("multipv") + 1]))
            if "depth" in tokens:
                parsed.depth = max(0, int(tokens[tokens.index("depth") + 1]))
            if "nodes" in tokens:
                parsed.nodes = max(0, int(tokens[tokens.index("nodes") + 1]))
            score_at = tokens.index("score")
            score_kind = tokens[score_at + 1]
            score_value = int(tokens[score_at + 2])
            if score_kind == "cp":
                parsed.score_cp_root = score_value
            elif score_kind == "mate":
                parsed.mate_root = score_value
            pv_at = tokens.index("pv")
            parsed.pv = tuple(tokens[pv_at + 1 :])
        except (ValueError, IndexError):
            return None
        return parsed if parsed.pv else None

    def analyze_position(
        self,
        position: Position,
        *,
        top_n: int = 3,
        on_demand: bool = False,
    ) -> ChessAnalysis:
        with self._lock:
            started = time.monotonic()
            try:
                self._start()
                count = max(1, min(self.config.multipv, int(top_n)))
                movetime = self.config.on_demand_movetime_ms if on_demand else self.config.movetime_ms
                self._drain()
                self._send(f"setoption name MultiPV value {count}")
                self._send(f"position fen {position.fen}")
                self._send(f"go movetime {movetime}")

                latest: dict[int, _ParsedLine] = {}
                bestmove = ""
                deadline = time.monotonic() + max(
                    self.config.analysis_timeout_seconds,
                    movetime / 1000.0 + 0.75,
                )
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        try:
                            self._send("stop")
                        except Exception:
                            pass
                        raise StockfishError("timeout phân tích Stockfish")
                    try:
                        line = self._lines.get(timeout=remaining)
                    except queue.Empty as exc:
                        raise StockfishError("timeout phân tích Stockfish") from exc
                    if line is None:
                        raise StockfishError("Stockfish dừng giữa lúc phân tích")
                    if line.startswith("info "):
                        parsed = self._parse_info(line)
                        if parsed is not None:
                            current = latest.get(parsed.multipv)
                            if current is None or parsed.depth >= current.depth:
                                latest[parsed.multipv] = parsed
                    elif line.startswith("bestmove"):
                        parts = line.split()
                        bestmove = parts[1] if len(parts) > 1 else ""
                        break

                ordered = [latest[index] for index in sorted(latest) if index in latest]
                if not ordered and bestmove:
                    ordered = [_ParsedLine(pv=(bestmove,))]
                if not ordered:
                    raise StockfishError("Stockfish không trả principal variation")

                root = ordered[0]
                root_sign = 1 if position.turn == "w" else -1
                if root.score_cp_root is not None:
                    white_score = int(root.score_cp_root * root_sign)
                elif root.mate_root is not None:
                    white_score = int((MATE_SCORE - min(999, abs(root.mate_root))) * (1 if root.mate_root > 0 else -1) * root_sign)
                else:
                    white_score = 0
                white_mate = root.mate_root * root_sign if root.mate_root is not None else None

                candidates: list[AnalyzedMove] = []
                for item in ordered[:count]:
                    move = _parse_uci_move(position, item.pv[0] if item.pv else "")
                    if move is None:
                        continue
                    root_cp = item.score_cp_root
                    if root_cp is None and item.mate_root is not None:
                        root_cp = (MATE_SCORE - min(999, abs(item.mate_root))) * (1 if item.mate_root > 0 else -1)
                    candidates.append(
                        AnalyzedMove(
                            move=move,
                            score_cp=int(root_cp or 0),
                            mate_in=item.mate_root,
                            depth=item.depth,
                            pv=item.pv,
                        )
                    )

                elapsed_ms = int((time.monotonic() - started) * 1000)
                self._analyses += 1
                self._last_depth = max((item.depth for item in ordered), default=0)
                self._last_elapsed_ms = elapsed_ms
                self._last_error = ""
                return ChessAnalysis(
                    provider="stockfish",
                    score_cp=white_score,
                    evaluation_label=evaluation_label(white_score),
                    candidates=tuple(candidates),
                    mate_in=white_mate,
                    depth=self._last_depth,
                    nodes=max((item.nodes for item in ordered), default=0),
                    elapsed_ms=elapsed_ms,
                )
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"[:500]
                # Đóng process hỏng để request sau có thể khởi động sạch lại.
                self.close()
                raise

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._ready = False
            if process is None:
                return
            try:
                if process.poll() is None and process.stdin is not None:
                    process.stdin.write("quit\n")
                    process.stdin.flush()
                    process.wait(timeout=0.8)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
            finally:
                try:
                    if process.stdin is not None:
                        process.stdin.close()
                except Exception:
                    pass
                try:
                    if process.stdout is not None:
                        process.stdout.close()
                except Exception:
                    pass

    def diagnostics(self) -> dict[str, object]:
        process_alive = self._process is not None and self._process.poll() is None
        return {
            "provider": "stockfish",
            "enabled": self.config.enabled,
            "available": self.available,
            "ready": bool(self._ready and process_alive),
            "path": self.path,
            "threads": self.config.threads,
            "hash_mb": self.config.hash_mb,
            "multipv": self.config.multipv,
            "movetime_ms": self.config.movetime_ms,
            "on_demand_movetime_ms": self.config.on_demand_movetime_ms,
            "analyses": self._analyses,
            "restarts": self._restarts,
            "last_depth": self._last_depth,
            "last_elapsed_ms": self._last_elapsed_ms,
            "last_error": self._last_error,
        }


__all__ = [
    "StockfishConfig",
    "StockfishError",
    "StockfishUCIAnalyzer",
    "discover_stockfish_path",
]

from __future__ import annotations

"""Worker OCR tăng dần: đổi cửa sổ/đổi pixel mới đọc, không block chat."""

import hashlib
import logging
import json
import os
import subprocess
import sys
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Protocol

try:
    import mss
except Exception:  # pragma: no cover - optional on headless CI
    mss = None  # type: ignore
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .active_window_provider import ActiveWindowProvider
from .change_detector import ScreenChangeDetector
from .evidence_models import ActiveWindowInfo, ScreenTextBlock, ScreenTextSnapshot
from .ocr_config import screen_ocr_enabled
from .windows_uia_reader import WindowsUIAReader


_LOG = logging.getLogger(__name__)


class SnapshotSink(Protocol):
    def publish(self, snapshot: ScreenTextSnapshot) -> None: ...


class _OCRBackend:
    name = "none"
    available = False
    def read(self, image: Image.Image, *, window_title: str, captured_at: float) -> tuple[ScreenTextBlock, ...]:
        return ()


class _RapidOCRBackend(_OCRBackend):
    name = "rapidocr"
    def __init__(self) -> None:
        self.available = False
        try:
            import numpy as np  # type: ignore
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
            self._np = np
            self._engine = RapidOCR()
            self.available = True
        except Exception:
            self.available = False

    def read(self, image: Image.Image, *, window_title: str, captured_at: float) -> tuple[ScreenTextBlock, ...]:
        if not self.available:
            return ()
        try:
            result, _elapsed = self._engine(self._np.asarray(image.convert("RGB")))
        except Exception:
            return ()
        blocks: list[ScreenTextBlock] = []
        for index, item in enumerate(result or []):
            try:
                box, raw_text, raw_score = item
                text = re.sub(r"\s+", " ", str(raw_text or "")).strip()
                score = float(raw_score or 0.0)
                if len(text) < 2 or score < 0.35:
                    continue
                xs = [int(point[0]) for point in box]; ys = [int(point[1]) for point in box]
                bounds = (min(xs), min(ys), max(xs)-min(xs), max(ys)-min(ys))
                digest = hashlib.sha1(f"rapidocr|{window_title}|{index}|{text}".encode("utf-8", "ignore")).hexdigest()[:16]
                blocks.append(ScreenTextBlock(digest, text[:1000], "ocr", max(0.35, min(0.92, score)), window_title, captured_at, bounds))
            except (IndexError, TypeError, ValueError) as exc:
                _LOG.debug("rapidocr block ignored index=%s error=%s", index, exc)
                continue
        return tuple(blocks)


class _PytesseractBackend(_OCRBackend):
    name = "pytesseract"
    def __init__(self) -> None:
        self.available = False
        try:
            import pytesseract  # type: ignore
            command = os.getenv("ERIS_TESSERACT_CMD", "").strip() or shutil.which("tesseract")
            if command:
                pytesseract.pytesseract.tesseract_cmd = command
                self._module = pytesseract
                self.available = True
        except Exception:
            self.available = False

    def read(self, image: Image.Image, *, window_title: str, captured_at: float) -> tuple[ScreenTextBlock, ...]:
        if not self.available:
            return ()
        lang = os.getenv("ERIS_SCREEN_OCR_LANG", "vie+eng").strip() or "eng"
        config = os.getenv("ERIS_SCREEN_OCR_TESSERACT_CONFIG", "--psm 6")
        try:
            data = self._module.image_to_data(image, lang=lang, config=config, output_type=self._module.Output.DICT)
        except Exception:
            try:
                data = self._module.image_to_data(image, lang="eng", config=config, output_type=self._module.Output.DICT)
            except Exception:
                return ()
        lines: dict[tuple[int, int, int], list[tuple[str, float, tuple[int, int, int, int]]]] = {}
        count = len(data.get("text", []))
        for index in range(count):
            text = str(data["text"][index] or "").strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][index]) / 100.0
            except Exception:
                conf = 0.55
            if conf < 0.30:
                continue
            key = (int(data.get("block_num", [0] * count)[index]), int(data.get("par_num", [0] * count)[index]), int(data.get("line_num", [0] * count)[index]))
            bounds = (int(data["left"][index]), int(data["top"][index]), int(data["width"][index]), int(data["height"][index]))
            lines.setdefault(key, []).append((text, conf, bounds))
        blocks: list[ScreenTextBlock] = []
        for key, words in lines.items():
            text = re.sub(r"\s+", " ", " ".join(word for word, _, _ in words)).strip()
            if len(text) < 2:
                continue
            confidence = sum(conf for _, conf, _ in words) / max(1, len(words))
            left = min(b[0] for _, _, b in words); top = min(b[1] for _, _, b in words)
            right = max(b[0] + b[2] for _, _, b in words); bottom = max(b[1] + b[3] for _, _, b in words)
            digest = hashlib.sha1(f"ocr|{window_title}|{key}|{text}".encode("utf-8", "ignore")).hexdigest()[:16]
            blocks.append(ScreenTextBlock(digest, text[:1000], "ocr", max(0.35, min(0.90, confidence)), window_title, captured_at, (left, top, right-left, bottom-top)))
        return tuple(blocks)


def _eris_managed_python_kind() -> str:
    """Return the Eris-owned Python runtime kind, or an empty string.

    Eris ships with two supported layouts on Windows:
    - ``.venv/Scripts/python.exe`` for developer installs;
    - ``python/python.exe`` for the portable release.

    The old OCR healer accepted only the first layout, while the launcher prefers
    the portable runtime when no venv exists.  That made OCR permanently disable
    itself on the normal packaged build.
    """

    if getattr(sys, "prefix", "") != getattr(sys, "base_prefix", ""):
        return "venv"

    try:
        executable = Path(sys.executable).resolve()
        if executable.parent.name.lower() != "python":
            return ""
        eris_root = executable.parent.parent
        # Be conservative: only mutate a portable interpreter that visibly lives
        # inside an Eris tree.  Never pip-install into an arbitrary system Python.
        markers = (
            eris_root / "Eris.exe",
            eris_root / "src" / "vision.py",
            eris_root / "vision.py",
        )
        if any(marker.exists() for marker in markers):
            return "portable"
    except Exception:
        return ""
    return ""


def _ensure_pip_available(*, logger=None) -> bool:
    """Ensure the current Eris-managed interpreter can execute ``pip``."""

    probe = [sys.executable, "-m", "pip", "--version"]
    try:
        result = subprocess.run(probe, capture_output=True, text=True, timeout=30, check=False)
        if result.returncode == 0:
            return True
    except Exception:
        pass

    # Some portable Python bundles omit pip but still ship ensurepip.  Bootstrap it
    # locally into that same runtime; if ensurepip is absent we simply fall through
    # to the explicit warning instead of touching a global Python installation.
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ensurepip", "--upgrade"],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode == 0:
            verify = subprocess.run(probe, capture_output=True, text=True, timeout=30, check=False)
            if verify.returncode == 0:
                if logger:
                    logger.info("[screen text] pip bootstrapped into the Eris Python runtime.")
                return True
    except Exception as exc:
        if logger:
            logger.debug("[screen text] ensurepip bootstrap unavailable: %s", exc)
    return False


def _auto_install_rapidocr(*, logger=None) -> bool:
    """Self-heal RapidOCR inside the Python runtime owned by Eris.

    Works for both the developer venv and the packaged ``python/python.exe``
    runtime.  It deliberately refuses an unrelated system interpreter.
    """

    if not screen_ocr_enabled():
        return False
    enabled = os.getenv("ERIS_SCREEN_OCR_AUTO_INSTALL", "1").strip().lower() not in {"0", "false", "off", "no"}
    if not enabled or os.getenv("PYTEST_CURRENT_TEST"):
        return False
    if getattr(_auto_install_rapidocr, "attempted", False):
        return False
    _auto_install_rapidocr.attempted = True

    runtime_kind = _eris_managed_python_kind()
    if not runtime_kind:
        if logger:
            logger.warning(
                "[screen text] OCR auto-install skipped: Python runtime is not owned by Eris "
                "(expected .venv or <eris>/python/python.exe)."
            )
        return False

    if sys.version_info >= (3, 13):
        if logger:
            logger.warning(
                "[screen text] OCR auto-install skipped: rapidocr-onnxruntime is not available for Python %s.%s. "
                "Use the bundled Python 3.12 runtime or install Tesseract + pytesseract for OCR.",
                sys.version_info.major,
                sys.version_info.minor,
            )
        return False

    if not _ensure_pip_available(logger=logger):
        if logger:
            logger.warning(
                "[screen text] OCR self-heal could not find/bootstrap pip in Eris %s Python runtime.",
                runtime_kind,
            )
        return False

    requirements = Path(__file__).resolve().parents[1] / "requirements-screen-ocr.txt"
    command = [
        sys.executable, "-m", "pip", "install",
        "--disable-pip-version-check", "--no-warn-script-location",
    ]
    if requirements.exists():
        command.extend(["-r", str(requirements)])
    else:
        command.append("rapidocr-onnxruntime>=1.3.24,<2")

    if logger:
        logger.info("[screen text] OCR self-heal starting runtime=%s python=%s", runtime_kind, sys.executable)
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=300, check=False,
        )
    except Exception as exc:
        if logger:
            logger.warning("[screen text] OCR auto-install failed: %s", exc)
        return False
    if result.returncode != 0:
        if logger:
            detail = (result.stderr or result.stdout or "pip returned non-zero")[-1200:]
            logger.warning("[screen text] OCR auto-install failed rc=%s detail=%s", result.returncode, detail)
        return False

    try:
        import importlib
        importlib.invalidate_caches()
    except Exception:
        pass
    if logger:
        logger.info(
            "[screen text] OCR dependency installed into Eris %s runtime; resolving backend again.",
            runtime_kind,
        )
    return True


_auto_install_rapidocr.attempted = False

def _choose_ocr_backend() -> _OCRBackend:
    if not screen_ocr_enabled():
        return _OCRBackend()
    preferred = os.getenv("ERIS_SCREEN_OCR_BACKEND", "auto").strip().lower() or "auto"
    candidates = []
    if preferred in {"auto", "rapidocr"}:
        candidates.append(_RapidOCRBackend())
    if preferred in {"auto", "pytesseract", "tesseract"}:
        candidates.append(_PytesseractBackend())
    for candidate in candidates:
        if candidate.available:
            return candidate
    return _OCRBackend()


class IncrementalScreenTextWorker:
    def __init__(self, sink: SnapshotSink, *, logger=None) -> None:
        self.sink = sink
        self.logger = logger
        self.enabled = os.getenv("ERIS_SCREEN_TEXT_INDEX_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"}
        self.poll_sec = max(0.5, float(os.getenv("ERIS_SCREEN_TEXT_POLL_SEC", "1.2") or 1.2))
        self.triggered_mode = os.getenv("ERIS_OCR_TRIGGERED_MODE", "1").strip().lower() not in {"0", "false", "off", "no"}
        self.reconcile_sec = max(3.0, float(os.getenv("ERIS_SCREEN_RECONCILE_SEC", "15") or 15.0))
        self.min_ocr_interval = max(0.5, float(os.getenv("ERIS_SCREEN_OCR_MIN_INTERVAL_SEC", "1.5") or 1.5))
        self.full_refresh_sec = max(3.0, float(os.getenv("ERIS_SCREEN_OCR_FULL_REFRESH_SEC", "15") or 15.0))
        self.window_provider = ActiveWindowProvider()
        self.uia_reader = WindowsUIAReader()
        self.detector = ScreenChangeDetector(threshold=float(os.getenv("ERIS_SCREEN_CHANGE_THRESHOLD", "0.025") or 0.025))
        self.backend: _OCRBackend | None = None
        self._last_backend_probe_at = 0.0
        self._backend_warning_emitted = False
        self._stop = threading.Event()
        self._trigger = threading.Event()
        self._structured_coverage_until = 0.0
        # OCR nền và OCR cưỡng bức từ chat dùng chung capture/backend state.
        # Lock này ngăn hai luồng cùng chụp rồi ghi revision lộn thứ tự.
        self._capture_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._revision = 0
        self._last_ocr_at = 0.0
        self._last_full_at = 0.0
        self._last_blocks: tuple[ScreenTextBlock, ...] = ()
        self.cache_path = self._resolve_cache_path()

    def _resolve_cache_path(self) -> Path:
        explicit = os.getenv("ERIS_LIGHT_OCR_CACHE_PATH", "").strip()
        if explicit:
            return Path(explicit).expanduser()
        local = os.getenv("LOCALAPPDATA", "").strip()
        if local:
            return Path(local) / "ERIS" / "ocr_state.json"
        return Path.cwd() / "ocr_state.json"

    @property
    def ocr_enabled(self) -> bool:
        """OCR chỉ chạy khi ``ERIS_SCREEN_OCR_ENABLED`` bật; mặc định tắt (vision + UIA)."""

        return screen_ocr_enabled()

    def start(self) -> None:
        if not self.enabled or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()
        if self.ocr_enabled:
            # Warm backend ngay tại start — RapidOCR phải load ONNX model lần đầu,
            # nếu để lazy thì lần OCR thật đầu tiên bị thêm ~150-300ms delay.
            # Chạy trước khi mở thread để backend sẵn sàng ngay vòng poll đầu.
            self._ensure_ocr_backend(time.time())
        elif self.logger:
            self.logger.info("[screen text] OCR disabled (ERIS_SCREEN_OCR_ENABLED=0); UIA only")
        # Đặt _last_full_at = 0 để vòng poll đầu tiên force full OCR ngay,
        # thay vì phải chờ full_refresh_sec (15s) mới có dữ liệu tươi.
        self._last_full_at = 0.0
        self._thread = threading.Thread(target=self._run, name="ERIS-ScreenTextIndex", daemon=True)
        self._thread.start()
        if self.logger:
            self.logger.info(
                "[screen text] worker started backend=%s mode=%s interval=%.1fs",
                self.backend.name if self.backend is not None and self.backend.available else "unavailable",
                "triggered" if self.triggered_mode else "poll",
                self.reconcile_sec if self.triggered_mode else self.poll_sec,
            )

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def notify_environment_event(self, event_type: str, *, structured_coverage: bool = True) -> None:
        """Wake the worker after a native/UI event without forcing OCR.

        Strong structured coverage suppresses OCR for a short window; the
        worker still refreshes UIA/snapshot metadata. Callers may pass
        ``structured_coverage=False`` for canvas/game/unknown visual regions.
        """
        del event_type
        now = time.time()
        if structured_coverage:
            self._structured_coverage_until = max(self._structured_coverage_until, now + 3.0)
        self._trigger.set()

    def _capture(self, window: ActiveWindowInfo) -> Image.Image | None:
        if mss is None:
            return None
        try:
            with mss.mss() as sct:
                if window.bounds:
                    left, top, width, height = window.bounds
                    raw = sct.grab({"left": left, "top": top, "width": width, "height": height})
                else:
                    raw = sct.grab(sct.monitors[0])
            return Image.frombytes("RGB", raw.size, raw.rgb)
        except Exception as exc:
            _LOG.debug("screen capture failed: %s", exc, exc_info=True)
            return None

    def _prepare(self, image: Image.Image) -> Image.Image:
        gray = ImageOps.grayscale(image)
        gray = ImageOps.autocontrast(gray)
        gray = ImageEnhance.Contrast(gray).enhance(1.35)
        gray = gray.filter(ImageFilter.SHARPEN)
        if gray.width < 1400:
            scale = min(2.0, 1400.0 / max(1, gray.width))
            gray = gray.resize((int(gray.width * scale), int(gray.height * scale)), Image.Resampling.LANCZOS)
        return gray

    def _merge_blocks(self, uia: tuple[ScreenTextBlock, ...], ocr: tuple[ScreenTextBlock, ...]) -> tuple[ScreenTextBlock, ...]:
        merged: list[ScreenTextBlock] = []
        seen: set[str] = set()
        for block in (*uia, *ocr):
            key = re.sub(r"\W+", " ", block.text.lower()).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(block)
        return tuple(merged[:240])

    def _ensure_ocr_backend(self, now: float) -> None:
        if not self.ocr_enabled:
            return
        if self.backend is not None and self.backend.available:
            return
        retry_sec = max(30.0, float(os.getenv("ERIS_SCREEN_OCR_BACKEND_RETRY_SEC", "120") or 120.0))
        if now - self._last_backend_probe_at < retry_sec:
            return
        self._last_backend_probe_at = now
        self.backend = _choose_ocr_backend()
        if self.backend.available:
            self._backend_warning_emitted = False
            if self.logger:
                self.logger.info("[screen text] OCR backend resolved=%s", self.backend.name)
            return
        if _auto_install_rapidocr(logger=self.logger):
            self.backend = _choose_ocr_backend()
            if self.backend.available:
                self._backend_warning_emitted = False
                if self.logger:
                    self.logger.info("[screen text] OCR backend resolved=%s after self-heal", self.backend.name)
                return
        if self.logger and not self._backend_warning_emitted:
            self._backend_warning_emitted = True
            self.logger.warning(
                "[screen text] OCR backend unavailable; running UIA + vision fallback. "
                "Self-heal failed or was disabled; install requirements-screen-ocr.txt with the same Eris Python runtime."
            )

    def refresh_now(self, *, force_ocr: bool = True) -> ScreenTextSnapshot | None:
        """Làm mới UIA/OCR ngay cho một yêu cầu chat có nhắc màn hình.

        Worker nền được giữ lazy để tiết kiệm tài nguyên. Nhưng khi user bảo
        ``xem đi`` thì chờ vòng poll kế tiếp rồi lập tức chuyển sang vision là
        một nhịp khá vô duyên. Lượt cưỡng bức này dùng cùng pipeline và privacy
        gate với worker nền; nó không có đường chụp riêng lén lút.
        """

        if not self.enabled:
            return None
        try:
            with self._capture_lock:
                return self._capture_snapshot(force_ocr=bool(force_ocr and self.ocr_enabled))
        except Exception as exc:
            if self.logger:
                self.logger.warning("[screen text] forced refresh failed: %s", exc)
            return None

    def _capture_snapshot(self, *, force_ocr: bool = False) -> ScreenTextSnapshot:
        window = self.window_provider.get()
        privacy_blocked = self.uia_reader.is_sensitive(window.title) and not self.uia_reader.allow_sensitive
        image = None if privacy_blocked else self._capture(window)
        errors: list[str] = []
        changed_ratio = 0.0
        uia_blocks = () if privacy_blocked else self.uia_reader.read(window)
        ocr_blocks: tuple[ScreenTextBlock, ...] = ()
        should_ocr = False
        if image is not None:
            change = self.detector.inspect(image, key=f"{window.hwnd}:{window.title}")
            changed_ratio = change.ratio
            now = time.time()
            structured_coverage_high = self.triggered_mode and now < self._structured_coverage_until
            should_ocr = bool(
                self.ocr_enabled
                and (
                    force_ocr
                    or (
                        not structured_coverage_high
                        and (
                            (change.changed and (now - self._last_ocr_at) >= self.min_ocr_interval)
                            or (now - self._last_full_at) >= self.full_refresh_sec
                        )
                    )
                )
            )
            if should_ocr:
                self._ensure_ocr_backend(now)
            if should_ocr and self.backend is not None and self.backend.available:
                # Lượt chat cưỡng bức dùng toàn cửa sổ. ROI của detector rất tốt
                # cho nền, nhưng có thể cắt mất tiêu đề video/tên bài mà đại từ
                # ``nó`` đang trỏ tới.
                target = image
                if (
                    not force_ocr
                    and change.roi
                    and not change.full_refresh
                    and (now - self._last_full_at) < self.full_refresh_sec
                ):
                    target = image.crop(change.roi)
                ocr_blocks = self.backend.read(
                    self._prepare(target),
                    window_title=window.title,
                    captured_at=now,
                )
                self._last_ocr_at = now
                if force_ocr or change.full_refresh or (now - self._last_full_at) >= self.full_refresh_sec:
                    self._last_full_at = now
        elif not privacy_blocked:
            errors.append("capture_unavailable")

        blocks = self._merge_blocks(uia_blocks, ocr_blocks)
        if not blocks and not privacy_blocked:
            blocks = self._last_blocks
        elif blocks:
            self._last_blocks = blocks
        self._revision += 1
        snapshot = ScreenTextSnapshot(
            window=window,
            blocks=blocks,
            captured_at=time.time(),
            changed_ratio=changed_ratio,
            revision=self._revision,
            backend=(
                self.backend.name
                if self.backend is not None and self.backend.available
                else "uia_only"
            ),
            privacy_blocked=privacy_blocked,
            errors=tuple(errors),
        )
        self.sink.publish(snapshot)
        self._write_cache(snapshot)
        return snapshot

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.triggered_mode:
                # Native/UI events provide the fast path. A slow reconciliation
                # tick repairs missed events without returning to 1.2s OCR polling.
                self._trigger.wait(self.reconcile_sec)
                self._trigger.clear()
                if self._stop.is_set():
                    break
            started = time.time()
            try:
                with self._capture_lock:
                    self._capture_snapshot(force_ocr=False)
            except Exception as exc:
                if self.logger:
                    self.logger.warning("[screen text] background refresh failed: %s", exc)
            if not self.triggered_mode:
                elapsed = time.time() - started
                self._stop.wait(max(0.05, self.poll_sec - elapsed))

    def _write_cache(self, snapshot: ScreenTextSnapshot) -> None:
        if snapshot.privacy_blocked:
            return
        persist_raw = os.getenv("ERIS_OCR_CACHE_RAW_TEXT", "0").strip().lower() in {"1", "true", "yes", "on"}
        digest_source = "\n".join(block.text for block in snapshot.blocks[:80])[:6000]
        payload = {
            "source": "accessibility" if any(b.source == "uia" for b in snapshot.blocks) else "ocr",
            "confidence": snapshot.best_confidence,
            "content_fingerprint": hashlib.sha256(digest_source.encode("utf-8", "ignore")).hexdigest()[:24] if digest_source else "",
            "title": snapshot.window.title,
            "process_name": snapshot.window.process_name,
            "captured_at": snapshot.captured_at,
            "updated_at": snapshot.captured_at,
            "backend": snapshot.backend,
        }
        if persist_raw:
            payload["visible_text"] = digest_source
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.cache_path)
        except Exception as exc:
            _LOG.debug("ocr snapshot cache write failed path=%s error=%s", self.cache_path, exc, exc_info=True)

"""Công tắc ``ERIS_SCREEN_OCR_ENABLED``: mặc định tắt OCR, vision thay thế."""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest
from PIL import Image

import screen.ocr_worker as ocr_worker
from actions.capability_runtime import runtime_sensor_health_snapshot
from runtime.chat_perception import ChatPerceptionCoordinator
from screen.evidence_models import ActiveWindowInfo, ScreenTextBlock
from screen.ocr_config import screen_ocr_enabled
from screen.screen_evidence_store import ScreenEvidenceStore
from screen.screen_relevance_router import ScreenRelevanceRouter

_OCR_MODULES = ("rapidocr_onnxruntime", "onnxruntime", "pytesseract")
_EXPLICIT = "nhìn màn hình đi"
_PREVIOUS = "Em thấy anh đang mở video YouTube đó."


class _Sink:
    def __init__(self) -> None:
        self.snapshots = []

    def publish(self, snapshot) -> None:
        self.snapshots.append(snapshot)


class _Window:
    def get(self) -> ActiveWindowInfo:
        return ActiveWindowInfo(title="Notepad", hwnd=11, process_name="notepad.exe")


class _UIA:
    allow_sensitive = False

    def is_sensitive(self, _title: str) -> bool:
        return False

    def read(self, _window) -> tuple:
        return ()


class _FakeBackend(ocr_worker._OCRBackend):
    name = "fake-ocr"
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def read(self, image, *, window_title: str, captured_at: float):
        self.calls += 1
        return (ScreenTextBlock("ocr-1", "dòng chữ từ OCR giả", "ocr", 0.9, window_title, captured_at),)


class _ImportRecorder:
    """Finder ghi lại mọi lần import module OCR mà không chặn chúng."""

    def __init__(self) -> None:
        self.hits: list[str] = []

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in _OCR_MODULES:
            self.hits.append(name)
        return None


def _worker(monkeypatch) -> tuple[ocr_worker.IncrementalScreenTextWorker, _Sink]:
    sink = _Sink()
    worker = ocr_worker.IncrementalScreenTextWorker(sink)
    worker.window_provider = _Window()
    worker.uia_reader = _UIA()
    monkeypatch.setattr(worker, "_capture", lambda _window: Image.new("RGB", (64, 64), "white"))
    return worker, sink


def _stop(worker) -> None:
    worker.stop()
    worker._trigger.set()  # đánh thức thread đang chờ reconcile để nó thoát ngay


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ERIS_SCREEN_OCR_ENABLED", raising=False)
    for name in _OCR_MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)


def _stub_provider():
    return SimpleNamespace(
        contexts_for_turn=lambda *_a, **_k: (),
        evidence_for_turn=lambda *_a, **_k: (),
        has_fresh_relevant_state=lambda *_a, **_k: False,
        active_engagement_id=lambda **_k: "",
    )


class _RecordingWorker:
    def __init__(self) -> None:
        self.started = 0
        self.refresh_kwargs: list[dict] = []

    def start(self) -> None:
        self.started += 1

    def refresh_now(self, **kwargs):
        self.refresh_kwargs.append(kwargs)
        return None


def _coordinator(worker=None) -> ChatPerceptionCoordinator:
    store = ScreenEvidenceStore()
    return ChatPerceptionCoordinator(
        screen_evidence_store=store,
        screen_text_worker=worker if worker is not None else _RecordingWorker(),
        screen_relevance_router=ScreenRelevanceRouter(store),
        structured_provider=_stub_provider(),
    )


def test_switch_defaults_off_and_reads_env_at_call_time(monkeypatch) -> None:
    assert screen_ocr_enabled() is False
    for value in ("1", "true", "YES", " on "):
        monkeypatch.setenv("ERIS_SCREEN_OCR_ENABLED", value)
        assert screen_ocr_enabled() is True
    for value in ("0", "", "off", "no", "false"):
        monkeypatch.setenv("ERIS_SCREEN_OCR_ENABLED", value)
        assert screen_ocr_enabled() is False


def test_worker_off_imports_nothing_runs_no_pip_and_never_uses_backend(monkeypatch) -> None:
    recorder = _ImportRecorder()
    monkeypatch.setattr(sys, "meta_path", [recorder, *sys.meta_path])
    pip_calls: list = []
    monkeypatch.setattr(ocr_worker.subprocess, "run", lambda *a, **k: pip_calls.append(a) or None)
    # Nếu code lỡ chọn backend khi đang tắt thì fail rõ ràng.
    monkeypatch.setattr(
        ocr_worker, "_RapidOCRBackend", lambda: pytest.fail("RapidOCR backend must not be built")
    )
    monkeypatch.setattr(
        ocr_worker, "_PytesseractBackend", lambda: pytest.fail("Tesseract backend must not be built")
    )
    worker, sink = _worker(monkeypatch)
    assert worker.ocr_enabled is False
    worker.start()
    try:
        snapshot = worker.refresh_now(force_ocr=True)
    finally:
        _stop(worker)

    assert recorder.hits == []
    assert pip_calls == []
    assert worker.backend is None
    assert snapshot is not None and snapshot.backend == "uia_only"
    assert not any(block.source == "ocr" for block in snapshot.blocks)
    assert sink.snapshots[-1] is snapshot


def test_backend_helpers_are_inert_when_off(monkeypatch) -> None:
    recorder = _ImportRecorder()
    monkeypatch.setattr(sys, "meta_path", [recorder, *sys.meta_path])
    monkeypatch.setattr(ocr_worker.subprocess, "run", lambda *a, **k: pytest.fail("pip must not run"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(ocr_worker._auto_install_rapidocr, "attempted", False, raising=False)

    backend = ocr_worker._choose_ocr_backend()
    assert backend.available is False
    assert ocr_worker._auto_install_rapidocr() is False
    assert ocr_worker._auto_install_rapidocr.attempted is False
    worker, _sink = _worker(monkeypatch)
    worker._ensure_ocr_backend(time.time())
    assert worker.backend is None
    assert recorder.hits == []


def test_switch_on_selects_backend_and_produces_ocr_blocks(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_SCREEN_OCR_ENABLED", "1")
    backend = _FakeBackend()
    monkeypatch.setattr(ocr_worker, "_choose_ocr_backend", lambda: backend)
    worker, _sink = _worker(monkeypatch)
    assert worker.ocr_enabled is True
    worker.start()
    try:
        assert worker.backend is backend
        snapshot = worker.refresh_now(force_ocr=True)
    finally:
        _stop(worker)

    assert backend.calls >= 1
    assert snapshot is not None and snapshot.backend == "fake-ocr"
    assert any(block.source == "ocr" and "OCR giả" in block.text for block in snapshot.blocks)


def test_refresh_screen_text_never_forces_ocr_when_off_and_forces_when_on(monkeypatch) -> None:
    worker = _RecordingWorker()
    coordinator = _coordinator(worker)
    coordinator.refresh_screen_text()
    assert worker.refresh_kwargs == [{"force_ocr": False}]

    monkeypatch.setenv("ERIS_SCREEN_OCR_ENABLED", "1")
    coordinator.refresh_screen_text()
    assert worker.refresh_kwargs[-1] == {"force_ocr": True}


def test_router_explicit_reference_wants_vision_now_only_when_ocr_off(monkeypatch) -> None:
    router = ScreenRelevanceRouter(ScreenEvidenceStore())

    off = router.route(_EXPLICIT)
    assert off.mode == "vision"
    assert off.references_screen is True
    assert off.needs_visual is True

    monkeypatch.setenv("ERIS_SCREEN_OCR_ENABLED", "1")
    on = router.route(_EXPLICIT)
    assert on.mode == "vision"
    assert on.needs_visual is False


def test_router_direct_vision_marker_wants_vision_now_when_off() -> None:
    router = ScreenRelevanceRouter(ScreenEvidenceStore())
    decision = router.route("tự xem đi", previous_assistant_text=_PREVIOUS)
    assert decision.mode == "vision"
    assert decision.needs_visual is True


def test_router_inherited_anaphora_keeps_next_turn_path_when_off() -> None:
    router = ScreenRelevanceRouter(ScreenEvidenceStore())
    decision = router.route("cái đó kìa", previous_assistant_text=_PREVIOUS)
    assert decision.references_screen is True
    assert decision.debug["anaphora_inherited"] is True
    assert decision.mode == "vision"
    assert decision.needs_visual is False


def test_inherited_anaphora_queues_pending_vision_instead_of_capturing_now() -> None:
    coordinator = _coordinator()
    captures: list[str] = []
    result = coordinator.prepare_turn(
        "cái đó kìa",
        previous_assistant_text=_PREVIOUS,
        capture_fn=lambda: captures.append("capture") or "data:image/png;base64,AAAA",
    )
    assert captures == []
    assert result.data_uri == ""
    assert coordinator.pending_vision.pending is True


def test_explicit_reference_captures_vision_on_same_turn_and_grounds_from_vision() -> None:
    worker = _RecordingWorker()
    coordinator = _coordinator(worker)
    captures: list[str] = []
    report = SimpleNamespace(
        grounded_claims=("cửa sổ Notepad đang mở",),
        recommendation="speak",
        source="test-vision",
        stale=False,
        to_evidence_items=lambda grounded_only=True: ({
            "id": "vision:notepad",
            "source": "vision",
            "claim": "cửa sổ Notepad đang mở",
            "confidence": 0.9,
            "grounded": True,
            "stale": False,
            "tags": ["vision", "screen"],
        },),
    )

    result = coordinator.prepare_turn(
        _EXPLICIT,
        capture_fn=lambda: captures.append("capture") or "data:image/png;base64,AAAA",
        interpret_fn=lambda _uri: report,
    )

    assert captures == ["capture"]
    assert result.route.mode == "vision" and result.route.needs_visual is True
    assert result.capture_present is True and result.capture_interpreted is True
    assert coordinator.pending_vision.pending is False
    assert all(kwargs.get("force_ocr") is not True for kwargs in worker.refresh_kwargs)

    items = coordinator.build_screen_grounding_items(
        screen_blocks=result.screen_blocks,
        route=result.route,
        vision_report=report,
        screen_image_data_uri=result.data_uri,
        capture_interpreted=result.capture_interpreted,
    )
    assert any(
        (item.get("id") if isinstance(item, dict) else getattr(item, "id", "")) == "vision:notepad"
        for item in items
    )


def test_explicit_reference_with_ocr_on_keeps_old_forced_refresh_and_next_turn_path(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_SCREEN_OCR_ENABLED", "1")
    worker = _RecordingWorker()
    coordinator = _coordinator(worker)
    captures: list[str] = []
    result = coordinator.prepare_turn(
        _EXPLICIT,
        capture_fn=lambda: captures.append("capture") or "data:image/png;base64,AAAA",
    )
    assert {"force_ocr": True} in worker.refresh_kwargs
    assert captures == []
    assert result.data_uri == ""
    assert coordinator.pending_vision.pending is True


def test_capability_snapshot_reports_ocr_disabled_when_off(monkeypatch) -> None:
    store = SimpleNamespace(snapshot=lambda: SimpleNamespace(backend="uia_only"))
    host = SimpleNamespace(
        semantic_environment_worker=object(),
        screen_text_worker=object(),
        screen_evidence_store=store,
        browser_bridge_server=None,
        vision_semantic_compressor=None,
        project_operations=object(),
    )
    health = runtime_sensor_health_snapshot(owner=host)
    assert health["ocr"] == "disabled"
    assert health["uia"] == "healthy"

    monkeypatch.setenv("ERIS_SCREEN_OCR_ENABLED", "1")
    assert runtime_sensor_health_snapshot(owner=host)["ocr"] == "degraded"

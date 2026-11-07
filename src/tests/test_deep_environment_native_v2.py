import json
import time
from pathlib import Path

from native.chromium_accessibility import ChromiumAccessibilityAdapter, ChromiumTab, ChromiumWindowSnapshot
from native.clipboard_monitor import ClipboardMonitor
from native.filesystem_monitor import FilesystemMonitor
from native.windows_event_broker import EVENT_OBJECT_NAMECHANGE, EVENT_SYSTEM_FOREGROUND, WindowsEventBroker
from runtime.ai_orchestrated.desktop_semantics import DesktopSemanticRuntime, UserSituation
from runtime.ai_orchestrated.environment_event_bus import EnvironmentEventBus, NormalizedEnvironmentEvent
from runtime.ai_orchestrated.region_memory import RegionMemory
from runtime.ai_orchestrated.task_graph import TaskGraph
from runtime.privacy.environment_policy import EnvironmentPolicy, PrivacyScope
from screen.evidence_models import ActiveWindowInfo, ScreenTextBlock, ScreenTextSnapshot
from screen.ocr_worker import IncrementalScreenTextWorker


def _event(kind: str, *, level: int = 1, title: str = "", source: str = "test", now: float | None = None, **metadata):
    now = time.time() if now is None else now
    return NormalizedEnvironmentEvent(
        timestamp=now,
        source=source,
        event_type=kind,
        process_name="Code.exe",
        pid=42,
        hwnd=7,
        region_id="code:42:hwnd_7:editor",
        role="editor",
        title=title,
        attention_level=level,
        source_confidence=0.98,
        confidence=0.96,
        novelty=0.7,
        importance=0.7 if level >= 2 else 0.2,
        user_relevance=0.8,
        metadata=metadata,
    )


def test_event_bus_coalesces_repeated_namechange() -> None:
    bus = EnvironmentEventBus(coalesce_ms=500)
    now = time.time()
    first = bus.publish(_event("name_changed", title="same", now=now))
    second = bus.publish(_event("name_changed", title="same", now=now + 0.1))
    assert first is not None
    assert second is None
    assert len(bus.latest()) == 1


def test_private_messaging_event_is_metadata_only() -> None:
    bus = EnvironmentEventBus(policy=EnvironmentPolicy())
    event = NormalizedEnvironmentEvent(
        timestamp=time.time(), source="uia", event_type="focus_changed",
        process_name="Discord.exe", title="private conversation", value="secret body",
        role="textbox", metadata={"text": "secret body", "channel": "someone"},
    )
    accepted = bus.publish(event)
    assert accepted is not None
    assert accepted.privacy_scope == "METADATA"
    assert accepted.title == ""
    assert accepted.value == ""
    assert "text" not in accepted.metadata


def test_password_surface_is_hard_blocked() -> None:
    bus = EnvironmentEventBus(policy=EnvironmentPolicy())
    blocked = bus.publish(NormalizedEnvironmentEvent(
        timestamp=time.time(), source="uia", event_type="focus_changed", process_name="chrome.exe",
        title="Password", metadata={"is_password": True, "text": "hunter2"},
    ))
    assert blocked is None


def test_clipboard_monitor_emits_metadata_not_plaintext() -> None:
    received = []
    monitor = ClipboardMonitor(received.append)
    event = monitor.emit_metadata(formats=(1, 13), size_bucket="small")
    payload = event.to_dict()
    assert payload["privacy_scope"] == "METADATA"
    assert payload["metadata"]["formats"] == (1, 13)
    assert "text" not in json.dumps(payload).lower()
    assert received[-1] == event


def test_windows_event_broker_filters_duplicate_callback() -> None:
    received = []
    broker = WindowsEventBroker(received.append, coalesce_ms=500)
    now = time.time()
    first = broker.emit_raw(EVENT_SYSTEM_FOREGROUND, 123, timestamp=now)
    second = broker.emit_raw(EVENT_SYSTEM_FOREGROUND, 123, timestamp=now + 0.1)
    third = broker.emit_raw(EVENT_OBJECT_NAMECHANGE, 123, timestamp=now + 0.1)
    assert first is not None
    assert second is None
    assert third is not None
    assert [item.event_type for item in received] == ["foreground_changed", "name_changed"]


def test_chromium_tab_selection_and_title_change_are_structured() -> None:
    adapter = ChromiumAccessibilityAdapter()
    base = ChromiumWindowSnapshot(
        hwnd=9, pid=100, process_name="chrome.exe",
        tabs=(ChromiumTab("Docs", selected=True), ChromiumTab("Issues")), selected_tab="Docs",
    )
    first = adapter.diff_events(base, now=time.time())
    assert any(item.event_type == "browser_tab_activated" for item in first)

    changed = ChromiumWindowSnapshot(
        hwnd=9, pid=100, process_name="chrome.exe",
        tabs=(ChromiumTab("Docs"), ChromiumTab("Issue #42", selected=True)), selected_tab="Issue #42",
    )
    second = adapter.diff_events(changed, now=time.time() + 1)
    kinds = {item.event_type for item in second}
    assert "browser_tab_activated" in kinds
    assert "browser_tab_title_changed" in kinds
    assert all(item.attention_level == 1 for item in second)


def test_region_memory_requires_stability_before_promoting_new_fingerprint() -> None:
    memory = RegionMemory(min_samples=2, stable_ms=250)
    state, stable = memory.update("terminal", fingerprint="a", now_ms=1000)
    assert stable is True
    state, stable = memory.update("terminal", fingerprint="b", now_ms=1100, semantic=True)
    assert stable is False
    state, stable = memory.update("terminal", fingerprint="b", now_ms=1400, semantic=True)
    assert stable is True
    assert state.stable_fingerprint == "b"


def test_task_graph_links_edit_failure_research_return_sequence() -> None:
    graph = TaskGraph()
    now = time.time()
    sequence = [
        _event("file_saved", title="src/vision.py", now=now),
        _event("command_started", title="pytest", now=now + 1),
        _event("command_failed", level=3, title="pytest", now=now + 2),
        NormalizedEnvironmentEvent(
            timestamp=now + 3, source="chromium_uia", event_type="browser_tab_activated",
            process_name="chrome.exe", pid=8, hwnd=10, role="tab", title="Python asyncio timeout docs",
            attention_level=1, confidence=0.9, source_confidence=0.9, user_relevance=0.8,
        ),
        _event("editor_focused", title="src/vision.py", now=now + 4),
    ]
    state = graph.ingest(sequence, now=now + 4)
    assert state.activity == "software_development"
    assert state.phase == "implementation"  # returned to editor, same graph/task
    assert any(ep.outcome == "failed" for ep in state.episodes)
    assert "src/vision.py" in state.objects
    assert "Python asyncio timeout docs" in state.objects


def test_task_completion_reduces_interruption_cost() -> None:
    graph = TaskGraph()
    now = time.time()
    graph.ingest([_event("command_failed", level=3, title="pytest", now=now)])
    before = graph.interruption_cost(idle_seconds=1, busy_score=0.8)
    graph.ingest([_event("command_succeeded", level=2, title="pytest", now=now + 1)])
    after = graph.interruption_cost(idle_seconds=25, busy_score=0.3)
    assert after < before


def test_desktop_semantics_structured_failure_wakes_but_counter_does_not() -> None:
    runtime = DesktopSemanticRuntime()
    base = UserSituation(timestamp=time.time(), focused_process="Code.exe")
    now = time.time()
    mechanical = runtime.observe_events([
        _event("counter_update", level=0, title="CPU 31%", now=now)
    ], base_situation=base, telemetry={"active_process": "Code.exe", "idle_seconds": 1}, now=now)
    assert mechanical.max_attention_level == 0
    assert mechanical.wake_recommended is False

    failed = runtime.observe_events([
        _event("command_failed", level=3, title="pytest", now=now + 1)
    ], base_situation=mechanical, telemetry={"active_process": "Code.exe", "idle_seconds": 1}, now=now + 1)
    assert failed.max_attention_level == 3
    assert failed.semantic_delta is True
    assert failed.wake_recommended is True
    assert failed.phase == "diagnosis"


def test_filesystem_monitor_only_emits_relative_path_metadata(tmp_path: Path) -> None:
    received = []
    monitor = FilesystemMonitor(tmp_path, received.append)
    event = monitor.emit_path("src/main.py", "modified")
    assert event.event_type == "file_saved"
    assert event.title == "src/main.py"
    assert event.metadata["extension"] == ".py"
    assert "content" not in event.metadata


def test_ocr_cache_is_fingerprint_only_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ERIS_OCR_CACHE_RAW_TEXT", raising=False)
    class Sink:
        def publish(self, snapshot):
            pass
    worker = IncrementalScreenTextWorker(Sink())
    worker.cache_path = tmp_path / "ocr_state.json"
    snapshot = ScreenTextSnapshot(
        window=ActiveWindowInfo(title="Editor", process_name="Code.exe"),
        blocks=(ScreenTextBlock("b1", "super secret visible source", "ocr", 0.9, "Editor", time.time()),),
        captured_at=time.time(), backend="ocr",
    )
    worker._write_cache(snapshot)
    payload = json.loads(worker.cache_path.read_text(encoding="utf-8"))
    assert "visible_text" not in payload
    assert "content_fingerprint" in payload
    assert "super secret visible source" not in worker.cache_path.read_text(encoding="utf-8")


def test_acceptance_replay_has_zero_mechanical_wakes() -> None:
    from tools.deep_environment_acceptance import mechanical_smoke_events, replay
    metrics = replay(mechanical_smoke_events(count=80))
    assert metrics.mechanical_events == 80
    assert metrics.mechanical_wakes == 0
    assert metrics.false_wake_rate == 0.0


def test_chromium_prefers_ia2_when_provider_confirms() -> None:
    from native.ia2_accessibility import IA2NodeSnapshot
    from native.process_resolver import ProcessIdentity

    class Resolver:
        def resolve(self, hwnd):
            return ProcessIdentity(hwnd=int(hwnd), pid=55, process_name="chrome.exe", window_class="Chrome_WidgetWin_1")

    class IA2:
        def snapshot(self, hwnd):
            return (
                (
                    IA2NodeSnapshot("1", "page tab", "Docs", selected=True),
                    IA2NodeSnapshot("2", "page tab", "Issues", selected=False),
                    IA2NodeSnapshot("3", "document", "Python docs"),
                ),
                True,
            )

    class UIA:
        def start(self):
            return self
        def stop(self):
            pass
        def invalidate(self, hwnd):
            pass
        def snapshot(self, hwnd, force=False):
            raise AssertionError("UIA không được gọi khi IA2 đã thành công")

    adapter = ChromiumAccessibilityAdapter(resolver=Resolver(), ia2=IA2(), uia=UIA())
    snap = adapter.snapshot_window(100)
    assert snap is not None
    assert snap.provider == "ia2"
    assert snap.selected_tab == "Docs"
    events = adapter.diff_events(snap, now=time.time())
    assert events[0].source == "chromium_ia2"


def test_uia_cache_only_refreshes_after_invalidation() -> None:
    from native.uia_event_client import UIAEventClient

    class Root:
        ControlTypeName = "EditControl"
        AutomationId = "editor"
        BoundingRectangle = None
        IsPassword = False
        HasKeyboardFocus = True
        IsSelected = False
        IsEnabled = True

        def __init__(self, name):
            self.Name = name

        def GetValuePattern(self):
            raise RuntimeError("không có value pattern")

    client = UIAEventClient(reconcile_seconds=999)
    first = client.snapshot(7, root=Root("A"))
    cached = client.snapshot(7, root=Root("B"))
    assert first[0].name == "A"
    assert cached[0].name == "A"
    client.invalidate(7)
    refreshed = client.snapshot(7, root=Root("B"))
    assert refreshed[0].name == "B"


def test_task_graph_has_semantic_edges_and_component_confidence() -> None:
    graph = TaskGraph()
    now = time.time()
    state = graph.ingest([
        _event("file_saved", title="src/a.py", now=now, source="filesystem"),
        _event("command_started", title="pytest", now=now + 1, source="owned_terminal"),
        _event("command_failed", level=3, title="pytest", now=now + 2, source="owned_terminal"),
        NormalizedEnvironmentEvent(
            timestamp=now + 3, source="chromium_ia2", event_type="browser_tab_activated",
            process_name="chrome.exe", pid=2, hwnd=3, role="tab", title="pytest timeout docs",
            attention_level=1, source_confidence=.96, confidence=.94, user_relevance=.8,
        ),
    ], now=now + 3)
    relations = {edge.relation for edge in state.edges}
    assert "TESTS" in relations
    assert "RESEARCHES" in relations
    assert set(state.confidence_components) >= {
        "structured_source_quality", "temporal_continuity", "semantic_similarity",
        "cross_source_agreement", "outcome_link_strength", "contradiction",
    }


def test_owned_terminal_session_emits_exact_exit_code() -> None:
    import sys
    from native.conpty_session import OwnedTerminalSession

    received = []
    session = OwnedTerminalSession(received.append)
    result = session.run([sys.executable, "-c", "print('ok')"])
    assert result.returncode == 0
    assert "ok" in (result.stdout or "")
    assert received[0].event_type == "command_started"
    assert received[-1].event_type == "command_succeeded"
    assert received[-1].metadata["exit_code"] == 0


def test_uiaccess_protocol_rejects_broad_operation() -> None:
    import pytest
    from native.uiaccess_broker.protocol import BrokerRequest

    with pytest.raises(ValueError):
        BrokerRequest(operation="run_command", hwnd=10).validate()


def test_visual_error_candidate_must_stabilize_before_wake() -> None:
    runtime = DesktopSemanticRuntime()
    now = time.time()
    first = NormalizedEnvironmentEvent(
        timestamp=now, source="ocr", event_type="error_candidate", process_name="Code.exe",
        pid=42, hwnd=7, region_id="code:42:hwnd_7:terminal", role="terminal",
        title="FAILED test_x", attention_level=2, semantic=True, confidence=.8,
        source_confidence=.75, importance=.7, user_relevance=.8,
    )
    situation1 = runtime.observe_events([first], telemetry={"active_process": "Code.exe", "idle_seconds": 1}, now=now)
    # Visual candidate đầu tiên chỉ thiết lập baseline; chưa được wake cognition.
    assert situation1.max_attention_level == 0
    # Candidate đổi nội dung tiếp theo phải ổn định đủ mẫu.
    changed = NormalizedEnvironmentEvent(
        timestamp=now + .1, source="ocr", event_type="error_candidate", process_name="Code.exe",
        pid=42, hwnd=7, region_id="code:42:hwnd_7:terminal", role="terminal",
        title="FAILED test_y", attention_level=2, semantic=True, confidence=.8,
        source_confidence=.75, importance=.7, user_relevance=.8,
    )
    situation2 = runtime.observe_events([changed], base_situation=UserSituation(timestamp=now + .1), telemetry={"active_process": "Code.exe", "idle_seconds": 1}, now=now + .1)
    assert situation2.max_attention_level == 0
    changed2 = NormalizedEnvironmentEvent(
        timestamp=now + .5, source="ocr", event_type="error_candidate", process_name="Code.exe",
        pid=42, hwnd=7, region_id="code:42:hwnd_7:terminal", role="terminal",
        title="FAILED test_y", attention_level=2, semantic=True, confidence=.8,
        source_confidence=.75, importance=.7, user_relevance=.8,
    )
    situation3 = runtime.observe_events([changed2], base_situation=UserSituation(timestamp=now + .5), telemetry={"active_process": "Code.exe", "idle_seconds": 1}, now=now + .5)
    assert situation3.max_attention_level == 2


def test_uia_semantic_adapter_enriches_editor_focus_without_value() -> None:
    from native.process_resolver import ProcessIdentity
    from native.uia_event_client import UIANodeSnapshot
    from native.uia_semantic_adapter import UIASemanticAdapter

    class FakeUIA:
        def start(self):
            return self
        def stop(self):
            pass
        def invalidate(self, hwnd):
            pass
        def snapshot(self, hwnd, force=False):
            return (UIANodeSnapshot("node1", "EditControl", name="vision.py", value="secret source", has_focus=True),)

    class Resolver:
        def resolve(self, hwnd):
            return ProcessIdentity(hwnd=int(hwnd), pid=42, process_name="Code.exe", window_class="Chrome_WidgetWin_1")

    adapter = UIASemanticAdapter(uia=FakeUIA(), resolver=Resolver())
    raw = NormalizedEnvironmentEvent(
        timestamp=time.time(), source="winevent", event_type="focus_changed",
        process_name="Code.exe", pid=42, hwnd=7, attention_level=1,
    )
    enriched = adapter.enrich(raw)
    assert enriched is not None
    assert enriched.event_type == "editor_focused"
    assert enriched.title == "vision.py"
    assert enriched.value == ""
    assert enriched.metadata["is_password"] is False


def test_windows_event_broker_filters_own_process() -> None:
    import os
    from native.process_resolver import ProcessIdentity

    class Resolver:
        def resolve(self, hwnd):
            return ProcessIdentity(hwnd=int(hwnd), pid=os.getpid(), process_name="eris.exe")

    received = []
    broker = WindowsEventBroker(received.append, resolver=Resolver())
    assert broker.emit_raw(EVENT_SYSTEM_FOREGROUND, 999, timestamp=time.time()) is None
    assert received == []


def test_media_monitor_is_metadata_only_by_default() -> None:
    from native.media_monitor import MediaMonitor

    received = []
    monitor = MediaMonitor(received.append, include_content_metadata=False)
    event = monitor.emit_session({
        "source_app": "Spotify.exe", "playback": "playing",
        "title": "private song", "artist": "private artist", "timeline_position_ms": 12345,
    })
    assert event.title == ""
    assert "artist" not in event.metadata
    assert "timeline_position_ms" not in event.metadata
    assert event.metadata["playback"] == "playing"


def test_chromium_omnibox_keeps_domain_not_raw_url() -> None:
    from native.process_resolver import ProcessIdentity
    from native.uia_event_client import UIANodeSnapshot

    class Resolver:
        def resolve(self, hwnd):
            return ProcessIdentity(hwnd=int(hwnd), pid=55, process_name="chrome.exe")

    class IA2:
        def snapshot(self, hwnd):
            return (), False

    class UIA:
        def start(self):
            return self
        def stop(self):
            pass
        def invalidate(self, hwnd):
            pass
        def snapshot(self, hwnd, force=False):
            return (
                UIANodeSnapshot("tab", "TabItemControl", name="Docs", selected=True),
                UIANodeSnapshot("omnibox", "EditControl", name="Address and search bar", value="https://example.com/private?q=token", automation_id="address"),
            )

    adapter = ChromiumAccessibilityAdapter(resolver=Resolver(), ia2=IA2(), uia=UIA())
    snap = adapter.snapshot_window(100)
    assert snap is not None
    assert snap.active_domain == "example.com"
    event = adapter.diff_events(snap, now=time.time())[0]
    assert event.metadata["active_domain"] == "example.com"
    assert "private" not in json.dumps(event.to_dict()).lower()
    assert "token" not in json.dumps(event.to_dict()).lower()


def test_privacy_policy_loads_explicit_domain_override_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_ENVIRONMENT_PRIVACY_OVERRIDES", '{"learn.microsoft.com":"CONTENT_EPHEMERAL"}')
    policy = EnvironmentPolicy()
    decision = policy.scope_for(process_name="chrome.exe", url="https://learn.microsoft.com/windows")
    assert decision.scope == PrivacyScope.CONTENT_EPHEMERAL
    assert decision.ttl_ms > 0


def test_semantic_environment_worker_processes_without_self_turn_tick() -> None:
    """Structured event phải thành UserSituation ngay cả khi self-turn chưa chạy."""
    from types import SimpleNamespace
    from runtime.ai_orchestrated.semantic_environment_worker import SemanticEnvironmentWorker

    class Logger:
        def debug(self, *_args, **_kwargs):
            pass
        def warning(self, *_args, **_kwargs):
            pass

    class Observability:
        def __init__(self):
            self.names = []
        def observe(self, name, _value, **_kwargs):
            self.names.append(name)
        def increment(self, name, *_args, **_kwargs):
            self.names.append(name)

    wakes = []
    host = SimpleNamespace(
        logger=Logger(),
        observability=Observability(),
        _wake_autonomous_loop=wakes.append,
    )
    runtime = DesktopSemanticRuntime()
    worker = SemanticEnvironmentWorker(
        host=host,
        runtime=runtime,
        publish_event=lambda _event: None,
        batch_window_ms=0,
    ).start()
    try:
        started_revision = worker.revision
        worker.submit(_event("command_failed", level=3, title="pytest", now=time.time()))
        assert worker.wait_until_revision(started_revision + 1, timeout=1.0)
        situation = worker.latest_situation
        assert situation is not None
        assert situation.semantic_delta is True
        assert situation.max_attention_level == 3
        assert wakes and wakes[-1].startswith("environment_semantic:")
        assert "environment_ingress_to_semantic_ms" in host.observability.names
        assert "environment_event_to_semantic_ms" in host.observability.names
    finally:
        worker.stop()


def test_semantic_worker_attention_is_consumed_once_per_perception() -> None:
    """Event level cao không được đánh thức self-turn lặp lại sau khi đã consume."""
    from types import SimpleNamespace
    from runtime.ai_orchestrated.ambient_screen_context import AmbientScreenContext
    from runtime.ai_orchestrated.semantic_environment_worker import SemanticEnvironmentWorker

    class Logger:
        def debug(self, *_args, **_kwargs):
            pass
        def warning(self, *_args, **_kwargs):
            pass

    host = SimpleNamespace(logger=Logger(), observability=None, _wake_autonomous_loop=lambda _reason: None)
    runtime = DesktopSemanticRuntime()
    worker = SemanticEnvironmentWorker(
        host=host,
        runtime=runtime,
        publish_event=lambda _event: None,
        batch_window_ms=0,
    ).start()
    try:
        worker.submit(_event("command_failed", level=3, title="pytest", now=time.time()))
        assert worker.wait_until_revision(1, timeout=1.0)
        screen = AmbientScreenContext(
            revision=1,
            window_title="main.py - Visual Studio Code",
            process_name="Code.exe",
            age_seconds=0.1,
        )
        events1, situation1 = worker.build_perception_situation(
            screen,
            telemetry={"active_process": "Code.exe", "idle_seconds": 1},
            now=time.time(),
        )
        events2, situation2 = worker.build_perception_situation(
            screen,
            telemetry={"active_process": "Code.exe", "idle_seconds": 1},
            now=time.time() + 0.1,
        )
        assert len(events1) == 1
        assert situation1.max_attention_level == 3
        assert situation1.semantic_delta is True
        assert events2 == ()
        assert situation2.max_attention_level == 0
        assert situation2.semantic_delta is False
        # Task state vẫn còn dù attention của event đã được consume.
        assert situation2.task_hint != "unknown"
    finally:
        worker.stop()


def test_v61_semantic_worker_coalesces_same_meaning_wake_storm():
    from types import SimpleNamespace
    from runtime.ai_orchestrated.desktop_semantics import UserSituation
    from runtime.ai_orchestrated.environment_ingress_scheduler import ScheduledEnvironmentEvent, scheduling_policy_for
    from runtime.ai_orchestrated.semantic_environment_worker import SemanticEnvironmentWorker

    class Runtime:
        def observe_events(self, events, telemetry=None, base_situation=None, now=None):
            return UserSituation(
                timestamp=float(now or time.time()),
                task_hint="inspect eris source",
                phase="inspection",
                confidence=0.90,
                semantic_delta=True,
                max_attention_level=3,
            )

    wakes = []
    host = SimpleNamespace(logger=None, observability=None, _wake_autonomous_loop=wakes.append)
    worker = SemanticEnvironmentWorker(host=host, runtime=Runtime(), publish_event=lambda _event: None, batch_window_ms=0)
    worker._record_semantic_metrics = lambda *args, **kwargs: None
    worker._schedule_post_semantic_enrichment = lambda _batch: None

    event = _event("dialog_candidate", level=3, title="Eris source", now=time.time())
    policy = scheduling_policy_for(event)
    item = ScheduledEnvironmentEvent(
        event=event,
        ingress_monotonic_ns=time.monotonic_ns(),
        ingress_wall_time=time.time(),
        enqueue_monotonic_ns=time.monotonic_ns(),
        policy=policy,
        sequence=1,
        coalesce_key="",
    )
    worker._process_batch([item])
    worker._process_batch([item])
    worker._process_batch([item])
    assert len(wakes) == 1

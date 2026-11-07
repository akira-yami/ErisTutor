# -*- coding: utf-8 -*-
"""Regression tests cho observer single-flight và PrivateMind dirty/latest."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from runtime.ai_orchestrated.contracts import CognitiveObservation
from runtime.ai_orchestrated.observer import PostTurnObserver
from runtime.observer_private_runtime import ObserverPrivateRuntimeMixin


class _Reducer:
    def __init__(self) -> None:
        self.events = []

    def snapshot(self):
        return SimpleNamespace(version=0)

    def submit(self, event) -> None:
        self.events.append(event)


class _Router:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.timeouts: dict[str, float] = {}
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def observe_completed_turn(self, *, turn_id: str, turn_seq: int, timeout_seconds: float, **_kwargs):
        self.calls.append(turn_id)
        self.timeouts[turn_id] = float(timeout_seconds)
        if turn_id == "turn-1":
            self.first_started.set()
            assert self.release_first.wait(3.0)
        return CognitiveObservation(
            turn_id=turn_id,
            turn_seq=turn_seq,
            user_act="test",
            relationship_meaning="test",
            source="test",
            latency_ms=1.0,
        )


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


def test_observer_singleflight_keeps_only_latest_pending(monkeypatch):
    import runtime.inference_broker as broker

    monkeypatch.setenv("ERIS_POST_OBSERVER_IDLE_WAIT_SEC", "0")
    monkeypatch.setenv("ERIS_POST_OBSERVER_HTTP_TIMEOUT_SEC", "2")
    monkeypatch.setenv("ERIS_POST_OBSERVER_DEADLINE_SEC", "8")
    monkeypatch.setattr(broker.GLOBAL_INFERENCE_BROKER, "wait_for_user_idle", lambda _timeout: True)

    router = _Router()
    reducer = _Reducer()
    observer = PostTurnObserver(router, reducer)
    try:
        first = observer.submit(
            turn_id="turn-1",
            turn_seq=1,
            user_text="u1",
            assistant_text="a1",
        )
        assert router.first_started.wait(2.0)

        second = observer.submit(
            turn_id="turn-2",
            turn_seq=2,
            user_text="u2",
            assistant_text="a2",
        )
        third = observer.submit(
            turn_id="turn-3",
            turn_seq=3,
            user_text="u3",
            assistant_text="a3",
        )

        # Request cũ vẫn đang chiếm account, nhưng request mới không tranh role lock.
        assert router.calls == ["turn-1"]
        assert first.future.cancelled()
        assert second.future.cancelled()
        assert not third.future.done()

        router.release_first.set()
        _wait_until(lambda: third.future.done())

        assert router.calls == ["turn-1", "turn-3"]
        assert [event.turn_id for event in reducer.events] == ["turn-3"]
    finally:
        router.release_first.set()
        observer.shutdown()


def test_observer_pending_gets_fresh_inference_budget(monkeypatch):
    import runtime.inference_broker as broker

    monkeypatch.setenv("ERIS_POST_OBSERVER_IDLE_WAIT_SEC", "0")
    monkeypatch.setenv("ERIS_POST_OBSERVER_HTTP_TIMEOUT_SEC", "1.5")
    monkeypatch.setenv("ERIS_POST_OBSERVER_DEADLINE_SEC", "6")
    monkeypatch.setattr(broker.GLOBAL_INFERENCE_BROKER, "wait_for_user_idle", lambda _timeout: True)

    router = _Router()
    reducer = _Reducer()
    observer = PostTurnObserver(router, reducer)
    try:
        observer.submit(
            turn_id="turn-1",
            turn_seq=1,
            user_text="u1",
            assistant_text="a1",
        )
        assert router.first_started.wait(2.0)
        latest = observer.submit(
            turn_id="turn-2",
            turn_seq=2,
            user_text="u2",
            assistant_text="a2",
        )
        time.sleep(0.25)
        router.release_first.set()
        _wait_until(lambda: latest.future.done())

        # Queue wait ngắn không được trừ thẳng khỏi timeout HTTP cấu hình.
        assert router.timeouts["turn-2"] >= 1.35
    finally:
        router.release_first.set()
        observer.shutdown()


class _Logger:
    def debug(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


class _PrivateMindDaemon:
    def __init__(self) -> None:
        self.snapshots: list[str] = []
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.surface_queue = []

    def tick(self, **kwargs):
        marker = str(kwargs["snapshot"])
        self.snapshots.append(marker)
        if marker == "snapshot-1":
            self.first_started.set()
            assert self.release_first.wait(3.0)
        return SimpleNamespace(to_dict=lambda: {"marker": marker})

    def digest(self, **_kwargs):
        return {"marker": self.snapshots[-1] if self.snapshots else ""}


class _PrivateMindHost(ObserverPrivateRuntimeMixin):
    def __init__(self, daemon) -> None:
        self.private_mind_daemon = daemon
        self.private_mind_loop = None
        self.logger = _Logger()
        self._chat_processing = False
        self.state_committer = None
        self.memory_core = None
        self.session_turn_ledger = None
        self.internal_agenda = None
        self.thought_stream = None
        self.private_mind_surface_queue = []
        self.private_mind_digest = {}

    def _sync_private_mind_future_bias(self):
        return None

    def _persist_inner_diary(self):
        return None


def _private_tick(host: _PrivateMindHost, marker: str) -> None:
    host._update_private_mind_state(
        signal_snapshot=marker,
        observer_frame=None,
        attention_signal=None,
        channel="autonomous",
        user_text=marker,
    )


def test_private_mind_dirty_coalesces_to_latest_snapshot(monkeypatch):
    monkeypatch.setenv("ERIS_PRIVATE_MIND_MAX_CATCHUP_TICKS", "1")
    daemon = _PrivateMindDaemon()
    host = _PrivateMindHost(daemon)

    _private_tick(host, "snapshot-1")
    assert daemon.first_started.wait(2.0)

    # Nhiều update trong lúc bận chỉ giữ snapshot cuối cùng.
    _private_tick(host, "snapshot-2")
    _private_tick(host, "snapshot-3")
    daemon.release_first.set()

    _wait_until(lambda: host._private_mind_future.done())
    # Poll/apply kết quả; call mới có thể khởi động tick kế tiếp, nên chỉ kiểm tra
    # hai pass đầu của actor đã coalesce đúng snapshot mới nhất.
    assert daemon.snapshots[:2] == ["snapshot-1", "snapshot-3"]


def test_private_mind_preempt_discards_stale_generation(monkeypatch):
    monkeypatch.setenv("ERIS_PRIVATE_MIND_MAX_CATCHUP_TICKS", "1")
    daemon = _PrivateMindDaemon()
    host = _PrivateMindHost(daemon)

    _private_tick(host, "snapshot-1")
    assert daemon.first_started.wait(2.0)
    host._chat_processing = True
    host._preempt_background_for_chat("test-user-turn")
    daemon.release_first.set()
    _wait_until(lambda: host._private_mind_future.done())

    # Poll khi chat đã xong. Kết quả generation cũ không được apply.
    host._chat_processing = False
    _private_tick(host, "snapshot-after-chat")
    assert host.private_mind_digest == {}

"""Tóm tắt episode chạy nền: không model call dưới lock chat, giữ turn khi Gemini lỗi."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from memory.living_user import LivingUserMemoryService
from memory.living_user import service as service_module
from memory.living_user.episode_rollup import EpisodeRollupStore
from memory.living_user.episode_store import UserEpisodeStore
from memory.living_user.models import UserEpisode, UserMemoryScope
from memory.living_user.runtime_adapter import CuratorInferenceError
from memory.living_user.service import LivingUserMemoryConfig

ORIGINAL_IDS = ("u1", "a1", "u2", "a2")


class FakeAdapter:
    """summarize_episode chặn tới khi ``release`` (hoặc hết ``hold`` giây)."""

    def __init__(self, mode="ok", hold=30.0):
        self.mode = mode
        self.hold = hold
        self.calls = 0
        self.timeouts = []
        self.in_call = threading.Event()
        self.release = threading.Event()

    def summarize_episode(self, payload, *, timeout=None):
        self.calls += 1
        self.timeouts.append(timeout)
        self.in_call.set()
        if self.mode == "fail":
            raise CuratorInferenceError("provider_error")
        if self.mode in {"slow", "hang"}:
            self.release.wait(self.hold)
        return {"summary": "Tóm tắt giả lập từ Gemini.", "title": "Episode giả", "importance": 0.7}

    def propose_profile_operations(self, payload):
        return {"operations": [], "action_operations": []}


def make_service(tmp_path, adapter, **config):
    service = LivingUserMemoryService(tmp_path, model_adapter=adapter, config=LivingUserMemoryConfig(**config))
    return service


def seed(service, session="s1"):
    rows = (("user", "u1", "xin chào về dự án eris"), ("assistant", "a1", "chào anh, hôm nay làm gì"),
            ("user", "u2", "sửa bộ nhớ cho khỏi treo"), ("assistant", "a2", "ok, tách việc tóm tắt ra riêng"))
    for role, turn_id, text in rows:
        service.record_conversation_turn(role, text, session_id=session, turn_id=turn_id)


def wait_until(condition, timeout=8.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return bool(condition())


def buffer_ids(service):
    return [row["id"] for row in list(service._turn_buffer)]


def timed(fn):
    started = time.perf_counter()
    result = fn()
    return time.perf_counter() - started, result


@pytest.fixture
def made(tmp_path):
    created = []

    def factory(adapter, subdir="", **config):
        path = tmp_path / subdir if subdir else tmp_path
        service = make_service(path, adapter, **config)
        created.append((service, adapter))
        return service

    yield factory
    for service, adapter in created:
        if adapter is not None:
            adapter.release.set()
        service.stop_consolidation(timeout=3.0)


def test_hot_path_is_not_blocked_by_slow_summary(made):
    adapter = FakeAdapter("slow")
    service = made(adapter)
    seed(service)
    assert service.request_consolidation("test")
    assert adapter.in_call.wait(5)
    elapsed_capture, context = timed(lambda: service.capture_interpretation_context(evidence_ids=("u9",)))
    elapsed_record, _ = timed(lambda: service.record_conversation_turn("user", "đang tóm tắt", session_id="s1", turn_id="u9"))
    assert elapsed_capture < 0.5 and elapsed_record < 0.5
    assert service.consolidation_status()["running"]
    assert "recent_conversation" in context


def test_late_turns_survive_and_episode_is_persisted_before_trim(made, tmp_path):
    adapter = FakeAdapter("slow")
    service = made(adapter)
    seed(service)
    observed = []
    original_save = service._save_turn_buffer_locked

    def spy_save():
        observed.append((tuple(buffer_ids(service)), len(service.episode_store.all())))
        original_save()

    service._save_turn_buffer_locked = spy_save
    service.request_consolidation("test")
    assert adapter.in_call.wait(5)
    for turn_id in ("late1", "late2"):
        service.record_conversation_turn("user", f"turn muộn {turn_id}", session_id="s1", turn_id=turn_id)
    adapter.release.set()
    assert wait_until(lambda: service.episode_store.all() and not any(x in buffer_ids(service) for x in ORIGINAL_IDS))
    assert buffer_ids(service) == ["late1", "late2"]
    trim_saves = [(ids, count) for ids, count in observed if "u1" not in ids]
    assert trim_saves and all(count >= 1 for _, count in trim_saves)
    restored = LivingUserMemoryService(tmp_path)
    assert restored.episode_store.all()
    assert [row["id"] for row in restored._turn_buffer] == ["late1", "late2"]
    assert service.consolidation_status()["failures"] == 0
    assert adapter.timeouts and adapter.timeouts[0] == pytest.approx(60.0)


def test_failing_adapter_keeps_turns_and_schedules_backoff(made):
    adapter = FakeAdapter("fail")
    service = made(adapter)
    seed(service)
    service.request_consolidation("test")
    assert wait_until(lambda: service.consolidation_status()["failures"] == 1)
    assert wait_until(lambda: service.consolidation_status()["retry_pending"])
    status = service.consolidation_status()
    assert buffer_ids(service) == list(ORIGINAL_IDS)
    assert service.episode_store.all(include_archived=True) == ()
    assert status["next_retry_in_s"] > 0 and status["last_error"] == "provider_error"
    assert not status["pending"]
    events = service.trace_journal.read(limit=50, kinds=["episode_consolidation"])
    failed = [e for e in events if e.payload.get("status") == "failed"]
    assert failed and failed[-1].payload["failures"] == 1 and failed[-1].payload["retry_in_s"] > 0


def test_hanging_adapter_hits_hard_timeout(made, monkeypatch):
    monkeypatch.setattr(service_module, "_DEADLINE_GRACE_SEC", 0.2)
    adapter = FakeAdapter("hang")
    service = made(adapter, episode_timeout_sec=1.0)
    seed(service)
    service.request_consolidation("test")
    assert adapter.in_call.wait(5)
    elapsed, _ = timed(lambda: service.capture_interpretation_context())
    assert elapsed < 0.5
    assert wait_until(lambda: service.consolidation_status()["failures"] == 1, timeout=6.0)
    status = service.consolidation_status()
    assert "timeout" in status["last_error"]
    assert not status["running"]
    assert buffer_ids(service) == list(ORIGINAL_IDS)
    assert service.episode_store.all(include_archived=True) == ()


def test_requests_while_running_are_single_flight(made):
    adapter = FakeAdapter("slow")
    service = made(adapter)
    seed(service)
    service.request_consolidation("a")
    assert adapter.in_call.wait(5)
    service.request_consolidation("b")
    service.request_consolidation("c")
    adapter.release.set()
    assert wait_until(lambda: service.episode_store.all())
    time.sleep(0.3)
    assert adapter.calls == 1


def test_active_user_turn_defers_consolidation_without_failure(made):
    adapter = FakeAdapter("ok")
    service = made(adapter)
    gate = {"active": True}
    service._user_turn_active = lambda: gate["active"]
    seed(service)
    service.request_consolidation("test")
    time.sleep(0.4)
    assert adapter.calls == 0
    assert service.consolidation_status()["failures"] == 0
    assert buffer_ids(service) == list(ORIGINAL_IDS)
    gate["active"] = False
    service._cons_wake.set()
    assert wait_until(lambda: service.episode_store.all(), timeout=8.0)
    assert adapter.calls == 1


def test_buffer_pressure_persists_heuristic_episode_with_source_turns(made):
    adapter = FakeAdapter("fail")
    service = made(adapter)
    for index in range(40):
        service.record_conversation_turn("user", f"dòng số {index} về bộ nhớ", session_id="s1", turn_id=f"p{index}")
    assert service.consolidation_status()["failures"] == 0
    service.request_consolidation("pressure")
    assert wait_until(lambda: service.episode_store.all())
    episode = service.episode_store.all()[0]
    assert episode.source == "episode_fallback"
    assert episode.metadata["source_turns"]
    assert buffer_ids(service) == []
    assert service.consolidation_status()["failures"] == 0


def test_without_model_adapter_a_heuristic_episode_is_still_made(made):
    service = made(None)
    seed(service)
    service.request_consolidation("test")
    assert wait_until(lambda: service.episode_store.all())
    assert service.episode_store.all()[0].source == "episode_fallback"
    assert buffer_ids(service) == []


def test_session_change_consolidates_old_session_only(made):
    adapter = FakeAdapter("ok")
    service = made(adapter)
    seed(service, session="s1")
    service.record_conversation_turn("user", "phiên mới bắt đầu", session_id="s2", turn_id="n1")
    assert wait_until(lambda: service.episode_store.all())
    assert wait_until(lambda: buffer_ids(service) == ["n1"])
    assert service.episode_store.all()[0].session_id == "s1"


def test_flush_session_legacy_api_still_returns_episode(made):
    adapter = FakeAdapter("ok")
    service = made(adapter)
    seed(service)
    episode = service.flush_session(run_dream=False)
    assert episode is not None and episode.source == "episode_model"
    assert buffer_ids(service) == []


def test_flush_session_legacy_api_falls_back_when_model_fails(made):
    service = made(FakeAdapter("fail"))
    seed(service)
    episode = service.flush_session(run_dream=False)
    assert episode is not None and episode.source == "episode_fallback"


def test_rollup_failure_reports_detail(tmp_path):
    store = UserEpisodeStore(tmp_path / "episodes.json")
    now = time.time()
    store.add(UserEpisode(id="old", session_id="old", title="old", summary="Cũ", evidence_ids=("e:old",),
                          scope=UserMemoryScope(), started_at=now - 14 * 86400, ended_at=now - 14 * 86400))
    rollups = EpisodeRollupStore(tmp_path / "rollups.json", store)
    adapter = SimpleNamespace(summarize_episode_rollup=lambda payload: {"segments": [{"anchor": "1999-01-01", "text": "x"}]})
    result = rollups.run(adapter, now=now)
    assert result["status"] == "failed" and result["error"] == "ValueError"
    assert result["detail"] == "invalid_rollup_segment"

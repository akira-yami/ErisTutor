"""Rolling memory lifecycle contracts, with isolated files and fake inference."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from memory.living_user import LivingUserMemoryService
from memory.living_user.episode_projection import anchor_label, relative_time, time_anchor, token_cost
from memory.living_user.episode_store import UserEpisodeStore
from memory.living_user.models import ProfileOperation, UserEpisode, UserMemoryScope
from memory.episodic_memory import EpisodicMemoryItem, EpisodicMemoryStore
from memory.memory_ai_curator import MemoryAICurator
from runtime.conversation.root_session import RootConversationSession, _read_self
from runtime.conversation.self_prompt_view import project_runtime_context

NOW = time.time()
DAY = 86400


def ep(key, *, days=14, scope=None, summary=None, evidence=None, session=None):
    return UserEpisode(id=key, session_id=session or key, title=key,
        summary=summary or f"User discussed {key}; outcome remains unknown.",
        open_loops=("Still unresolved",), importance=0.8,
        evidence_ids=(evidence or f"e:{key}",), scope=scope or UserMemoryScope(),
        started_at=NOW - days * DAY, ended_at=NOW - days * DAY)


class Adapter:
    def __init__(self):
        self.calls = []
        self.profile_calls = []
        self.callback = None

    def summarize_episode_rollup(self, payload):
        self.calls.append(payload)
        if self.callback:
            self.callback()
        texts = {row["anchor"]: row["text"] for row in payload["previous_segments"]}
        for row in payload["new_episodes"]:
            texts[row["anchor"]] = (texts.get(row["anchor"], "") + f" {row['title']} chưa xong.").strip()
        return {"segments": [{"anchor": anchor, "text": text} for anchor, text in texts.items()]}

    def propose_profile_operations(self, payload):
        self.profile_calls.append(payload)
        return {"operations": [], "action_operations": []}


def setup(tmp_path, count=1):
    adapter = Adapter()
    service = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    for i in range(count):
        service.episode_store.add(ep(f"old{i}", days=14 + i))
    return service, adapter


def test_restart_reuses_recap_and_sends_only_new_delta(tmp_path):
    service, adapter = setup(tmp_path)
    assert service.rollup_episodes(now=NOW)["committed"] == 1
    original = service.episode_rollups.path.read_bytes()
    restored = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    assert restored.rollup_episodes(now=NOW)["calls"] == 0
    assert restored.episode_rollups.path.read_bytes() == original
    restored.episode_store.add(ep("next", days=8))
    assert restored.rollup_episodes(now=NOW)["calls"] == 1
    payload = adapter.calls[-1]
    assert payload["previous_segments"]
    assert [x["title"] for x in payload["new_episodes"]] == ["next"]
    recap = restored.episode_prompt_view(now=NOW)["episode_recap"]
    assert "old0" in recap and "next" in recap and "chưa xong" in recap
    assert "e:old0" not in recap


def test_same_id_content_change_invalidates_then_rebuilds(tmp_path):
    service, adapter = setup(tmp_path)
    service.rollup_episodes(now=NOW)
    service.episode_store.add(ep("old0", summary="Corrected episode: rejected, never completed."))
    assert service.episode_prompt_view(now=NOW)["episode_recap"] == ""
    assert service.rollup_episodes(now=NOW)["committed"] == 1
    assert adapter.calls[-1]["previous_segments"] == []
    assert "never completed" in adapter.calls[-1]["new_episodes"][0]["summary"]


def test_backfill_is_bounded_and_oldest_first(tmp_path):
    service, adapter = setup(tmp_path, 10)
    assert service.rollup_episodes(now=NOW)["committed"] == 1
    assert len(adapter.calls[0]["new_episodes"]) == 8
    assert adapter.calls[0]["new_episodes"][0]["title"] == "old9"
    assert service.rollup_episodes(now=NOW)["committed"] == 1
    assert len(adapter.calls[1]["new_episodes"]) == 2
    assert service.rollup_episodes(now=NOW)["calls"] == 0


@pytest.mark.parametrize("failure", ["empty", "bad_anchor", "oversize", "timeout"])
def test_bad_response_does_not_advance_coverage_and_retries(tmp_path, failure):
    service, adapter = setup(tmp_path)
    original = adapter.summarize_episode_rollup

    def fail(payload):
        if failure == "timeout":
            raise TimeoutError()
        if failure == "empty":
            return {"segments": []}
        return {"segments": [{"anchor": "1900-01-01" if failure == "bad_anchor" else payload["allowed_anchors"][0],
                              "text": "x" * 2000 if failure == "oversize" else "text"}]}

    adapter.summarize_episode_rollup = fail
    assert service.rollup_episodes(now=NOW)["status"] == "failed"
    assert not service.episode_rollups.path.exists()
    adapter.summarize_episode_rollup = original
    assert service.rollup_episodes(now=NOW + 1)["status"] == "deferred"
    assert service.rollup_episodes(now=NOW + 301)["committed"] == 1


def test_atomic_write_failure_keeps_previous_summary_and_coverage(tmp_path, monkeypatch):
    service, _ = setup(tmp_path)
    service.rollup_episodes(now=NOW)
    before = service.episode_rollups.path.read_bytes()
    service.episode_store.add(ep("new", days=8))
    import memory.living_user.episode_rollup as module
    write = module.atomic_write_json
    monkeypatch.setattr(module, "atomic_write_json", Mock(side_effect=OSError("disk full")))
    assert service.rollup_episodes(now=NOW)["status"] == "failed"
    assert service.episode_rollups.path.read_bytes() == before
    monkeypatch.setattr(module, "atomic_write_json", write)
    assert service.rollup_episodes(now=NOW + 301)["committed"] == 1


def test_source_changes_during_inference_reject_stale_candidate(tmp_path):
    service, adapter = setup(tmp_path)
    adapter.callback = lambda: service.episode_store.add(ep("arrived", days=9))
    assert service.rollup_episodes(now=NOW)["status"] == "stale"
    assert not service.episode_rollups.path.exists()


def test_single_flight_does_not_hold_source_lock(tmp_path):
    service, adapter = setup(tmp_path)
    entered, finish = threading.Event(), threading.Event()

    def callback():
        entered.set()
        assert finish.wait(5)

    adapter.callback = callback
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(service.rollup_episodes, now=NOW)
        assert entered.wait(5)
        try:
            assert service.rollup_episodes(now=NOW)["status"] == "deferred"
            # Mutation must finish while the model is still waiting.
            mutation = pool.submit(service.episode_store.add, ep("fresh", days=0))
            mutation.result(timeout=2)
        finally:
            finish.set()
        assert future.result(timeout=5)["status"] == "stale"


def test_exact_scope_partition_and_private_sources_not_in_default(tmp_path):
    service, adapter = setup(tmp_path)
    service.episode_store.add(ep("project_secret", scope=UserMemoryScope("project", "A")))
    service.episode_store.add(ep("private_secret", scope=UserMemoryScope("private")))
    assert service.rollup_episodes(now=NOW)["calls"] == 1
    assert service.rollup_episodes(now=NOW)["calls"] == 1
    assert len(adapter.calls) == 2
    default = service.episode_prompt_view(now=NOW)["episode_recap"]
    project = service.episode_prompt_view(scope=UserMemoryScope("project", "A"), now=NOW)["episode_recap"]
    assert "old0" in default and "project_secret" not in default
    assert "project_secret" in project
    assert "private_secret" not in project
    assert all(not ({x["title"] for x in p["new_episodes"]} >= {"old0", "project_secret"}) for p in adapter.calls)


def test_fresh_window_and_conservative_budget(tmp_path):
    service, _ = setup(tmp_path)
    for i in range(6):
        service.episode_store.add(ep(f"fresh{i}", days=i / 20))
    service.rollup_episodes(now=NOW)
    view = service.episode_prompt_view(now=NOW)
    assert len(view["recent_episodes"]) == 4
    assert token_cost(view) <= 3000
    for row in view["recent_episodes"]:
        assert set(row) == {"title", "summary", "outcome", "open_loops", "when", "scope"}
        assert row["open_loops"] == ["Still unresolved"]
    service.episode_store.add(ep("large", days=0, summary="漢" * 1200))
    assert token_cost(service.episode_prompt_view(now=NOW)) <= 3000


def test_read_paths_do_not_call_model_and_root_refreshes_stale_host_snapshot(tmp_path):
    service, adapter = setup(tmp_path)
    stale = {"living_user": {"recent_episodes": [{"title": "old legacy payload"}]}}
    service.rollup_episodes(now=NOW)
    service.episode_store.add(ep("just_now", days=0))
    host = SimpleNamespace(memory_state=stale, memory_kernel=SimpleNamespace(living_user=service))
    before = len(adapter.calls)
    service.summary()
    packet = service.compile_context("old0", token_budget=3000)
    view = _read_self(host)
    living = view["memory_state"]["living_user"]
    assert "old0" in living["episode_recap"]
    assert living["recent_episodes"][0]["title"] == "just_now"
    assert host.memory_state is stale and stale["living_user"]["recent_episodes"][0]["title"] == "old legacy payload"
    assert packet.episodes and len(packet.episodes) <= 2
    assert len(adapter.calls) == before
    context = project_runtime_context({"memory_context": packet.render_prompt_context()}, view)
    assert "[EPISODE_RECAP]" not in context["memory_context"]
    assert "old0" in context["memory_context"]  # Specific old-source recall remains.


def timestamp(text):
    return datetime.fromisoformat(text + "+07:00").timestamp()


def test_time_anchors_age_across_month_and_year_without_reinference(tmp_path):
    end = timestamp("2026-11-01T12:00:00")
    anchor = time_anchor(end)
    assert anchor == "2026-11-01"  # Do not mislabel Nov 1 as October.
    assert anchor_label(anchor, now=timestamp("2026-11-02T12:00:00")) == "tuần trước"
    assert anchor_label(anchor, now=timestamp("2027-01-02T12:00:00")) == "hồi tháng 11/2026"
    assert relative_time(end, now=timestamp("2026-11-02T12:00:00")) == "hôm qua"
    service, adapter = setup(tmp_path)
    service.rollup_episodes(now=NOW)
    before = len(adapter.calls)
    service.episode_prompt_view(now=NOW + 80 * DAY)
    assert len(adapter.calls) == before


def test_root_wire_ages_episode_out_without_retaining_detail_in_frozen_bootstrap(tmp_path, monkeypatch):
    adapter = Adapter()
    service = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    service.episode_store.add(ep("fresh", days=0, summary="UNIQUE_FRESH_EPISODE_DETAIL"))
    host = SimpleNamespace(memory_kernel=SimpleNamespace(living_user=service),
                           memory_state={"living_user": {"profile": {"records": []}}})

    class Router:
        def __init__(self):
            self.calls = []

        def invoke_structured_worker(self, _client, **kwargs):
            self.calls.append(kwargs["conversation_messages"])
            bootstrap = "root_session_bootstrap" in self.calls[-1][-1]["content"]
            reply = {"kind": "no_reply", "reason": "self_loaded"} if bootstrap else {"kind": "move", "say": "hi"}
            kwargs["raw_reply_sink"].append(json.dumps(reply))
            return reply

    client = SimpleNamespace(base_url="http://localhost:6011/v1", model_name="deepseek-v4-flash")
    router, session = Router(), RootConversationSession()
    session._host = host
    args = dict(router=router, client=client, system="system", schema={}, payload={"event": {}},
                runtime_context={}, recent_history=[])
    assert session.ask(**args)
    assert "UNIQUE_FRESH_EPISODE_DETAIL" in json.dumps(router.calls[-1])
    prefix = session._state.self_ref.content_json
    assert "UNIQUE_FRESH_EPISODE_DETAIL" not in prefix
    monkeypatch.setattr(time, "time", lambda: NOW + 9 * DAY)
    assert service.rollup_episodes()["committed"] == 1
    assert session.ask(**args)
    request = json.dumps(router.calls[-1], ensure_ascii=False)
    assert "UNIQUE_FRESH_EPISODE_DETAIL" not in request
    assert "episode_recap" in request and "fresh chưa xong" in request
    assert session._state.self_ref.content_json == prefix
    assert len(router.calls) == 3  # One warm-up, two steps; no extra inference for aging.


def link_profile(service):
    service.profile_store.apply([ProfileOperation(op="update", path="user.note", value="secret",
        evidence_ids=("e:old0",), explicitness="explicit", confidence=1.0)])


@pytest.mark.parametrize("action", ["correct", "soft_forget", "hard_forget"])
def test_authority_retires_old_recap_recall_and_late_reimport(tmp_path, action):
    service, adapter = setup(tmp_path)
    link_profile(service)
    service.rollup_episodes(now=NOW)
    if action == "correct":
        service.correct("user.note", "corrected", evidence_ids=("new:evidence",))
    else:
        service.forget("user.note", hard=action == "hard_forget")
    assert service.episode_prompt_view(now=NOW)["episode_recap"] == ""
    assert not service.episode_store.search("old0")
    assert "old0 chưa xong" not in service.episode_rollups.path.read_text(encoding="utf-8")
    service.episode_store.add(ep("late_duplicate", evidence="e:old0"))
    restored = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    assert not restored.episode_store.search("late_duplicate")
    assert restored.rollup_episodes(now=NOW)["calls"] == 0
    if action == "hard_forget":
        assert "outcome remains unknown" not in service.episode_store.path.read_text(encoding="utf-8")


def test_hard_forget_during_inference_cannot_resurrect_candidate(tmp_path):
    service, adapter = setup(tmp_path)
    link_profile(service)
    adapter.callback = lambda: service.forget("user.note", hard=True)
    assert service.rollup_episodes(now=NOW)["status"] == "stale"
    assert not service.episode_prompt_view(now=NOW)["episode_recap"]


def test_archive_survives_trim_compact_restart_and_hard_delete(tmp_path):
    path = tmp_path / "episodes.json"
    store = UserEpisodeStore(path, max_items=20)
    for i in range(22):
        store.add(ep(f"archive{i}", days=60 + i, summary=f"shared topic memory summary unique{i}"))
    assert len(store.all()) == 20
    assert len(store.all(include_archived=True)) == 22
    assert store.search("unique21")[0].id == "archive21"
    store.compact(now=NOW, max_merges=2)
    restored = UserEpisodeStore(path)
    assert len(restored.all(include_archived=True)) == 22
    assert any(x.summary == "shared topic memory summary unique0" for x in restored.versions("archive0"))
    restored.scrub_evidence(("e:archive0",))
    assert not restored.versions("archive0")
    assert "e:archive0" not in path.read_text(encoding="utf-8")


def test_store_write_failure_restores_in_memory_state(tmp_path, monkeypatch):
    store = UserEpisodeStore(tmp_path / "episodes.json")
    store.add(ep("one"))
    import memory.living_user.episode_store as module
    monkeypatch.setattr(module, "atomic_write_json", Mock(side_effect=OSError()))
    with pytest.raises(OSError):
        store.add(ep("two"))
    assert {x.id for x in store.all()} == {"one"}


def test_corrupt_source_file_is_not_overwritten_as_empty(tmp_path):
    path = tmp_path / "episodes.json"
    path.write_text("broken json", encoding="utf-8")
    with pytest.raises(ValueError, match="episode_store_load_failed"):
        UserEpisodeStore(path)
    assert path.read_text() == "broken json"


def test_profile_consumer_checkpoint_is_independent_of_recap_and_restart(tmp_path):
    service, adapter = setup(tmp_path)
    service.rollup_episodes(now=NOW)
    service.dream(reason="test")
    assert len(adapter.profile_calls) == 1
    restored = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    restored.dream(reason="again")
    assert len(adapter.profile_calls) == 1
    restored.episode_store.add(ep("old0", summary="Actually user rejected this."))
    restored.dream(reason="changed")
    assert len(adapter.profile_calls) == 2


def test_profile_pending_proposals_resume_without_another_model_call(tmp_path, monkeypatch):
    service, adapter = setup(tmp_path)
    adapter.propose_profile_operations = Mock(return_value={"operations": [
        {"op": "update", "path": "communication.response_style", "value": "brief",
         "confidence": 0.95, "explicitness": "explicit", "evidence_ids": ["e:old0"]}]})
    apply = service.profile_store.apply
    monkeypatch.setattr(service.profile_store, "apply", Mock(side_effect=OSError("interrupted profile commit")))
    with pytest.raises(OSError):
        service.dream()
    assert json.loads(service.dreamer.checkpoint_path.read_text())["pending"]
    monkeypatch.setattr(service.profile_store, "apply", apply)
    restored = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    restored.dream()
    assert adapter.propose_profile_operations.call_count == 1
    assert restored.get_memory("communication.response_style").value == "brief"
    assert json.loads(service.dreamer.checkpoint_path.read_text())["pending"] == []


def test_dream_archiving_does_not_invalidate_recap_or_remove_sources(tmp_path):
    service, _ = setup(tmp_path, 2)
    service.rollup_episodes(now=NOW)
    before = service.episode_prompt_view(now=NOW)["episode_recap"]
    assert before
    service.dream()
    assert not service.episode_store.all()
    assert len(service.episode_store.all(include_archived=True)) == 2
    assert service.episode_prompt_view(now=NOW)["episode_recap"] == before
    assert service.rollup_episodes(now=NOW)["calls"] == 0


def test_irrelevant_query_does_not_rehydrate_high_importance_old_episode(tmp_path):
    service, _ = setup(tmp_path)
    source = ep("unrelated")
    source.importance = 1.0
    service.episode_store.add(source)
    assert service.episode_store.search("unique_unmatched_query") == ()


def test_maintenance_wires_rollup_even_when_profile_dream_disabled(tmp_path, monkeypatch):
    from memory.memory_kernel import MemoryKernel
    import memory.memory_kernel as module
    service, adapter = setup(tmp_path)
    service.config.auto_dream_on_maintenance = False
    kernel = MemoryKernel(core=SimpleNamespace(living_user=service))
    report = SimpleNamespace(notes=[], warnings=[])
    monkeypatch.setattr(module, "MemoryMaintenanceRunner", Mock(return_value=SimpleNamespace(run=lambda **_: report)))
    assert kernel.maintain() is report
    assert len(adapter.calls) == 1
    assert any("episode_rollup:committed" in x for x in report.notes)


def test_pending_profile_proposal_is_not_replayed_after_source_changed(tmp_path, monkeypatch):
    service, adapter = setup(tmp_path)
    adapter.propose_profile_operations = Mock(return_value={"operations": [
        {"op": "update", "path": "user.note", "value": "wrong", "confidence": 0.99,
         "explicitness": "explicit", "evidence_ids": ["e:old0"]}]})
    monkeypatch.setattr(service.profile_store, "apply", Mock(side_effect=OSError()))
    with pytest.raises(OSError):
        service.dream()
    service.episode_store.add(ep("old0", summary="corrected source"))
    adapter.propose_profile_operations = Mock(return_value={"operations": []})
    restored = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    restored.dream()
    assert restored.get_memory("user.note") is None
    assert adapter.propose_profile_operations.call_count == 1


def test_hard_forget_scrubs_unapplied_profile_proposal_and_its_source(tmp_path, monkeypatch):
    service, adapter = setup(tmp_path)
    adapter.propose_profile_operations = Mock(return_value={"operations": [
        {"op": "update", "path": "user.note", "value": "sensitive_pending_value",
         "confidence": 0.99, "explicitness": "explicit", "evidence_ids": ["e:old0"]}]})
    monkeypatch.setattr(service.profile_store, "apply", Mock(side_effect=OSError()))
    with pytest.raises(OSError):
        service.dream()
    service.forget("user.note", hard=True)
    checkpoint = service.dreamer.checkpoint_path.read_text()
    assert "sensitive_pending_value" not in checkpoint
    assert json.loads(checkpoint)["pending"] == []
    assert "old0" not in json.loads(checkpoint)["coverage"]
    assert not service.episode_store.all(include_archived=True)


def episodic_core(tmp_path):
    store = EpisodicMemoryStore(str(tmp_path / "episodic.json"))
    store.save([EpisodicMemoryItem(id=str(i), timestamp="2026-10-01T12:00:00+07:00",
        summary="user prefers brief concise answers", evidence_ids=[f"src{i}"]) for i in range(3)])
    return SimpleNamespace(episodic=store, commit=Mock(return_value=SimpleNamespace(outcome="accepted")))


def test_semantic_distill_marks_sources_persistently_and_reopens_changed_content(tmp_path):
    core = episodic_core(tmp_path)
    curator = MemoryAICurator(memory_core=core, llm_service=object())
    curator._distill_cluster = Mock(return_value={"claim": "Prefer concise answers", "confidence": 0.8})
    assert curator.run_distillation().distilled == 1
    before = core.episodic.load()
    assert all("distilled" in x.tags for x in before)
    again = MemoryAICurator(memory_core=core, llm_service=object())
    again._distill_cluster = Mock(side_effect=AssertionError("must reuse checkpoint"))
    assert again.run_distillation().distilled == 0
    for row in before:
        row.summary += " with examples"
    core.episodic.save(before)
    assert curator.run_distillation().distilled == 1
    assert curator._distill_cluster.call_count == 2


def test_rejected_semantic_commit_does_not_mark_sources(tmp_path):
    core = episodic_core(tmp_path)
    core.commit.return_value = SimpleNamespace(outcome="rejected")
    curator = MemoryAICurator(memory_core=core, llm_service=object())
    curator._distill_cluster = Mock(return_value={"claim": "fact", "confidence": 0.8})
    assert curator.run_distillation().distilled == 0
    assert all("distilled" not in x.tags for x in core.episodic.load())


def test_semantic_skip_is_a_persistent_decision_not_an_infinite_retry(tmp_path):
    core = episodic_core(tmp_path)
    curator = MemoryAICurator(memory_core=core, llm_service=object())
    curator._distill_cluster = Mock(return_value={"skip": True})
    curator.run_distillation()
    curator.run_distillation()
    assert curator._distill_cluster.call_count == 1
    core.commit.assert_not_called()
    assert all(x.metadata["distill_decision"] == "skipped" for x in core.episodic.load())


def test_semantic_checkpoint_covers_only_sources_the_model_read(tmp_path):
    core = episodic_core(tmp_path)
    rows = core.episodic.load()
    for i in range(3, 10):
        rows.append(EpisodicMemoryItem(id=str(i), timestamp=rows[0].timestamp, summary=rows[0].summary))
    core.episodic.save(rows)
    curator = MemoryAICurator(memory_core=core, llm_service=object())
    curator._distill_cluster = Mock(return_value={"claim": "brief answers", "confidence": 0.8})
    curator.run_distillation()
    assert len(curator._distill_cluster.call_args.args[0]) == 8
    assert sum("distilled" in row.tags for row in core.episodic.load()) == 8


def test_semantic_candidate_is_rejected_if_source_changes_during_model_call(tmp_path):
    core = episodic_core(tmp_path)
    curator = MemoryAICurator(memory_core=core, llm_service=object())

    def infer(_):
        rows = core.episodic.load()
        rows[0].summary = "Corrected: the earlier claim was wrong."
        core.episodic.save(rows)
        return {"claim": "stale claim", "confidence": 0.8}

    curator._distill_cluster = infer
    assert "distillation_sources_changed" in curator.run_distillation().notes
    core.commit.assert_not_called()
    assert all("distilled" not in row.tags for row in core.episodic.load())


def test_access_save_cannot_erase_semantic_checkpoint_or_new_episode(tmp_path):
    core = episodic_core(tmp_path)
    stale = core.episodic.load()
    core.episodic.mark_distilled([x.to_dict() for x in stale])
    core.episodic.save(stale)
    assert all("distilled" in x.tags for x in core.episodic.load())
    snapshots = [x.to_dict() for x in core.episodic.load()]
    core.episodic.apply_commit(SimpleNamespace(content="a wholly different event", evidence_ids=["later"]))
    core.episodic.mark_distilled(snapshots)
    assert len(core.episodic.load()) == 4

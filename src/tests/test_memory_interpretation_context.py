from copy import deepcopy
from types import SimpleNamespace

from memory.living_user import LivingUserMemoryService
from memory.living_user.evidence_context import build_evidence_context
from memory.living_user.episode_builder import UserEpisodeBuilder
from memory.living_user.episode_store import UserEpisodeStore
from memory.living_user.models import ProfileOperation, UserEpisode, UserMemoryScope
from memory.living_user.curator import LivingUserMemoryCurator
from runtime.turn_memory_pipeline import TurnMemoryPipeline, TurnMemoryEnvelope, TurnMemoryPipelineConfig
from runtime.conversation.self_prompt_view import project_self_view


def test_frozen_context_survives_delayed_worker_and_durable_inbox(tmp_path):
    seen = []
    adapter = SimpleNamespace(propose_profile_operations=lambda payload: (
        seen.append(deepcopy(payload)) or {"operations": [], "action_operations": []}))
    service = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    service.record_conversation_turn("user", "Đang đùa về video thôi", session_id="s", turn_id="u1")
    pipeline = TurnMemoryPipeline(memory_kernel=SimpleNamespace(core=SimpleNamespace(base_dir=str(tmp_path))),
                                  living_user=service, config=TurnMemoryPipelineConfig())
    envelope = TurnMemoryEnvelope(text="MỸ NỮ!", turn_id="u2", evidence_ids=("u2",), context={"role":"user"})
    assert pipeline._enqueue_curator(envelope)
    queued = pipeline._curator_queue.get_nowait()
    service._turn_buffer.clear()  # session rollover after enqueue
    service._turn_buffer.append({"id":"future","role":"user","text":"ngữ cảnh khác", "timestamp":envelope.created_at+1})
    pipeline._process_curator(queued)
    assert seen[0]["context"]["recent_conversation"][0]["text"] == "Đang đùa về video thôi"
    assert "future" not in str(seen[0]["context"]["interpretation_context"])
    # A second envelope is read back from SQLite, not the service's mutable tail.
    assert pipeline._enqueue_curator(TurnMemoryEnvelope(text="again", turn_id="u3", evidence_ids=("u3",)))
    due = pipeline._curator_inbox.due()
    assert isinstance(due[1]["context"]["interpretation_context"], dict)


def test_old_episodes_are_secondary_and_legacy_ids_stay_ambiguous():
    episodes = [{"id":str(i), "session_id":str(i), "evidence_ids":["session_turn:1"],
                 "summary":"Tóm tắt cũ", "ended_at":i+1, "metadata":{}} for i in range(2)]
    ctx = build_evidence_context(turns=[], episodes=episodes, evidence_ids=["session_turn:1"])
    assert ctx["ambiguous_evidence_ids"] == ["session_turn:1"]
    assert ctx["missing_evidence_ids"] == ["session_turn:1"]
    assert all(e["basis"] == "secondary_summary_only" for e in ctx["episodes"])


def test_source_context_budget_and_causal_cutoff():
    turns = [{"id":str(i),"role":"user","timestamp":i,"text":"x"*1800} for i in range(30)]
    ctx = build_evidence_context(turns=turns, episodes=[], cutoff=15, max_chars=4000)
    assert sum(len(t["text"]) for t in ctx["recent_conversation"]) <= 4000
    assert all(t["timestamp"] <= 15 for t in ctx["recent_conversation"])
    assert ctx["omitted_items"] > 0


def test_old_candidate_source_is_not_starved_by_busy_current_chat():
    turns = [{"id":str(i), "role":"user", "text":"x"*1800} for i in range(12)]
    source = [{"id":f"old-{i}", "role":"user", "text":"y"*1800} for i in range(32)]
    episode = {"id":"old", "evidence_ids":["old-30"], "summary":"Older context",
               "metadata":{"source_turns":source}}
    ctx = build_evidence_context(turns=turns, episodes=[episode], evidence_ids=["old-30"])
    assert "old-30" not in ctx["missing_evidence_ids"]
    assert any(row["id"] == "old-30" for row in ctx["episodes"][0]["source_turns"])
    used = sum(len(row["text"]) for row in ctx["recent_conversation"])
    used += sum(len(e["summary"])+sum(len(row["text"]) for row in e["source_turns"]) for e in ctx["episodes"])
    assert used <= 20000


def test_episode_sources_survive_reconsolidation_but_stay_out_of_root(tmp_path):
    adapter = SimpleNamespace(summarize_episode=lambda payload:{"title":"Trêu vui","summary":"Đùa về video"})
    builder = UserEpisodeBuilder(model_adapter=adapter)
    store = UserEpisodeStore(tmp_path/"episodes.json")
    first = builder.build([{"id":"u1","role":"user","text":"Tôi chỉ đang đùa"},
                           {"id":"a1","role":"assistant","text":"Tôi hiểu"}], session_id="s")
    store.add(first)
    store.add(builder.build([{"id":"a1","role":"assistant","text":"Tôi hiểu"},
                             {"id":"u2","role":"user","text":"Ừ"}], session_id="s"))
    saved = UserEpisodeStore(store.path).all()[0]
    assert [r["id"] for r in saved.metadata["source_turns"]] == ["u1","a1","u2"]
    snapshot = {"memory_state":{"living_user":{"recent_episodes":[saved.to_dict()]}}}
    before = deepcopy(snapshot)
    projected = project_self_view(snapshot)
    projected_episode = projected["memory_state"]["living_user"]["recent_episodes"][0]
    # The Root projection drops audit/provenance entirely but keeps the telling.
    for cut in ("evidence_ids", "metadata", "started_at", "ended_at", "session_id"):
        assert cut not in projected_episode, cut
    assert projected_episode["summary"] == "Đùa về video"
    assert projected_episode["title"] == "Trêu vui"
    assert snapshot == before
    # The canonical store still holds source_turns (to_dict is untouched), so
    # scrub_evidence operates on the full record, not the projection.
    assert store.scrub_evidence(["u1"]) == 1
    assert not UserEpisodeStore(store.path).all()


def test_gc_gets_evidence_context_not_only_profile_labels(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply([ProfileOperation(op="add", path="communication.style", value="thích trêu",
                                                confidence=.8, evidence_ids=("u1",))])
    service.record_conversation_turn("user", "Tôi đang đùa thôi", session_id="s", turn_id="u1")
    seen = []
    def reviewer(system, user, max_tokens):
        seen.append(user)
        return '{"operations":[]}'
    LivingUserMemoryCurator(service, reviewer=reviewer).run()
    assert seen and "Tôi đang đùa thôi" in seen[0]
    assert '"role":"user"' in seen[0]


def test_source_window_is_not_duplicated_into_mutation_trace(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.apply_turn_operations((), trace_context={"turn_id":"u1",
        "interpretation_context":{"recent_conversation":[{"text":"private-source-window"}]},
        "recent_conversation":[{"text":"private-source-window"}]})
    trace = service.trace_journal.path.read_text(encoding="utf-8")
    assert "private-source-window" not in trace
    assert '"turn_id":"u1"' in trace


def test_context_capture_failure_still_preserves_current_turn_in_inbox(tmp_path, monkeypatch):
    service = LivingUserMemoryService(tmp_path)
    def unavailable(**kwargs):
        raise RuntimeError("unavailable-source-detail")
    monkeypatch.setattr(service, "capture_interpretation_context", unavailable)
    pipeline = TurnMemoryPipeline(memory_kernel=SimpleNamespace(core=SimpleNamespace(base_dir=str(tmp_path))),
                                  living_user=service, config=TurnMemoryPipelineConfig())
    assert pipeline._enqueue_curator(TurnMemoryEnvelope(text="Keep this evidence", turn_id="u1", evidence_ids=("u1",)))
    queued = pipeline._curator_inbox.due()[1]
    assert queued["text"] == "Keep this evidence"
    assert queued["context"]["interpretation_context"]["source_context_available"] is False
    assert "unavailable-source-detail" not in str(queued)


def test_interpretation_context_excludes_other_project_episodes(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    for project in ["a", "b"]:
        service.episode_store.add(UserEpisode(id="e"+project, session_id=project, title=project,
            summary="private project "+project, scope=UserMemoryScope("project", project)))
    ctx = service.capture_interpretation_context(context={"project_id":"a"})
    assert [row["episode_id"] for row in ctx["episodes"]] == ["ea"]

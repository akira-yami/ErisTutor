from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from runtime.conversation.agent import ROOT_BATCH_STEP_SCHEMA, RuntimeModelRootAgent
from runtime.conversation.context import RootCapabilityRegistry
from runtime.conversation.contracts import ConversationEvent
from runtime.conversation.root_session import RootConversationSession, _read_self
from runtime.conversation.self_prompt_view import compact_profile, project_self_view, project_runtime_context
from runtime.conversation.self_prompt_view import (
    compact_agent_mind,
    compact_active_state,
    compact_mind_state,
    compact_semantic_item,
)
from runtime.conversation.self_prompt_view import state_difference
from runtime.conversation.tool_guides import TOOL_GUIDES, read_tool_guide


class Router:
    def __init__(self, *steps):
        self.executive_client = SimpleNamespace(base_url="http://localhost:6011/v1", model_name="deepseek-v4-flash")
        self.calls = []
        self.steps = iter(steps)

    def invoke_structured_worker(self, client, **kwargs):
        self.calls.append(copy.deepcopy({k: v for k, v in kwargs.items() if k != "raw_reply_sink"}))
        messages = kwargs.get("conversation_messages")
        bootstrap = messages and json.loads(messages[-1]["content"]).get("kind") == "root_session_bootstrap"
        reply = {"kind": "no_reply", "reason": "self_loaded"} if bootstrap else next(self.steps, {"kind": "no_reply", "reason": "done"})
        if "raw_reply_sink" in kwargs:
            kwargs["raw_reply_sink"].append(json.dumps(reply))
        return reply


def apply_overlay(original, patch):
    """Independent wire consumer, including null vs deletion and array replacement."""
    if patch == {"__value_now_null": True}:
        return None
    if not isinstance(original, dict) or not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = copy.deepcopy(original)
    for key, value in patch.items():
        if value == {"__erased_from_self": True}:
            out.pop(key, None)
        else:
            out[key] = apply_overlay(out.get(key), value)
    return out


def ask(session, router, context):
    result = session.ask(router=router, client=router.executive_client, system="system", schema=ROOT_BATCH_STEP_SCHEMA,
                         payload={"event": {"kind": "user_turn", "text": "next"}, "runtime_context": context},
                         runtime_context=context, recent_history=[])
    assert result is not None
    messages = router.calls[-1]["conversation_messages"]
    bootstrap = json.loads(messages[1]["content"])["eris_self_full"]
    step = json.loads(messages[-1]["content"])
    return apply_overlay(bootstrap, step["self_changes_since_bootstrap"]), step


def test_every_request_can_reconstruct_current_self_and_context(monkeypatch):
    monkeypatch.setenv("ERIS_ROOT_SESSION_BOOTSTRAP", "1")
    monkeypatch.setenv("ERIS_WEB_SESSION_MODE", "isolated")
    host = SimpleNamespace(memory_state={"fact": "A", "nullable": "x", "remove": 1, "items": [1, 2]})
    router, session = Router(), RootConversationSession()
    session._host = host
    assert session._prime(router=router, client=router.executive_client, system="system", schema=ROOT_BATCH_STEP_SCHEMA)
    prefix = session._state.self_ref.content_json
    host.memory_state = {"fact": "B", "nullable": None, "items": [], "added": {"weak": 0.01}}
    context = {"grounding_context": "screen context stays byte-for-byte", "flag": False}
    for _ in range(3):
        current, step = ask(session, router, context)
        assert current == _read_self(host)
        assert step["runtime_context"] == context
        assert step["self_base_fingerprint"] == session._state.self_ref.fingerprint
        assert "self_changes_since_last_step" not in step
    host.memory_state["fact"] = "A"
    del host.memory_state["added"]
    current, step = ask(session, router, {})
    assert current == _read_self(host)
    assert step["runtime_context"] == {}
    assert session._state.self_ref.content_json == prefix


def test_overflow_never_sends_a_partial_overlay(monkeypatch):
    monkeypatch.setenv("ERIS_ROOT_SESSION_BOOTSTRAP", "1")
    monkeypatch.setenv("ERIS_WEB_SESSION_MODE", "isolated")
    router, session = Router(), RootConversationSession()
    session._host = SimpleNamespace(memory_state={"old": 1})
    assert session._prime(router=router, client=router.executive_client, system="system", schema={})
    session._host.memory_state = {"large": "x" * 10000}
    session._fits = lambda client, messages: len(messages) <= 3
    assert session.ask(router=router, client=router.executive_client, system="system", schema={},
                       payload={"runtime_context": {}}, runtime_context={}, recent_history=[]) is None
    assert len(router.calls) == 1  # No truncated request reaches the router.


def test_overlay_preserves_bool_number_type_changes():
    patch = state_difference({"flag": False, "rows": [True]}, {"flag": 0, "rows": [1]})
    assert json.dumps(apply_overlay({"flag": False, "rows": [True]}, patch)) == '{"flag": 0, "rows": [1]}'


def test_compact_profile_cuts_audit_keeps_telling_and_qualifiers():
    rows = [
        {"id": str(i), "path": "communication.style", "value": {"when": "roleplay", "style": "cute"},
         "scope": {"kind": "session", "key": "s"}, "status": "active", "sensitivity": "normal",
         "source": "profile_model", "value_type": "object", "cardinality": "multi", "tier": "working",
         "recurrence": 1, "confidence": 0.4 + i / 10, "explicitness": "inferred", "evidence_ids": [f"u:{i}"],
         "valid_from": 10, "created_at": 10 if i != 1 else 9, "updated_at": 20,
         "last_confirmed_at": 20 if i != 2 else 18, "valid_until": None, "expires_at": None,
         "metadata": {"reason": "Only in this scene; not a permanent preference", "unrecognised_qualifier": i}}
        for i in range(3)
    ]
    rows[2]["scope"] = {"kind": "project", "key": "other"}
    source = {"version": 1, "record_count": 3, "records": rows}
    before = copy.deepcopy(source)
    projected = compact_profile(source)
    # Audit/provenance + technical qualifiers are gone from every record (and
    # not promoted to defaults).
    for cut in ("evidence_ids", "metadata", "created_at", "updated_at", "last_confirmed_at", "valid_from",
                "id", "value_type", "cardinality", "recurrence", "tier", "source"):
        assert all(cut not in row for row in projected["records"]), cut
        assert cut not in projected["defaults"], cut
    assert projected["aliases"] == {}
    # The telling and kept qualifiers (scope/sensitivity) are reconstructable.
    for row, original in zip(projected["records"], rows):
        full = {**copy.deepcopy(projected["defaults"]), **copy.deepcopy(row)}
        assert full["path"] == original["path"]
        assert full["value"] == original["value"]
        assert full["explicitness"] == original["explicitness"]
        assert full["status"] == original["status"]
        assert full["confidence"] == original["confidence"]
        assert full["scope"] == original["scope"]
        assert full["sensitivity"] == original["sensitivity"]
    # Input is never mutated and the projection is idempotent.
    assert source == before
    assert compact_profile(projected) == projected


def test_compact_agent_mind_cuts_audit_keeps_beliefs_and_contradictions():
    source = {
        "schema_version": 1,
        "updated_at": 1787.0,
        "beliefs": {
            "b.keep": {
                "key": "b.keep", "claim": "User tolerates dry humor.", "domain": "user",
                "confidence": 0.62, "source_ids": ["seed"], "tags": ["global"],
                "positive_evidence_count": 1, "negative_evidence_count": 0,
                "contradiction_ids": [], "decay_rate": 0.012,
                "created_at": 10.0, "updated_at": 20.0, "last_seen": 20.0,
            },
            "b.conflict": {
                "key": "b.conflict", "claim": "User prefers silence here.", "domain": "self",
                "confidence": 0.5, "source_ids": ["u:1"], "tags": ["silence"],
                "positive_evidence_count": 2, "negative_evidence_count": 1,
                "contradiction_ids": ["b.keep"], "decay_rate": 0.015,
                "created_at": 11.0, "updated_at": 21.0, "last_seen": 21.0,
            },
        },
        "preferences": {
            "p.one": {
                "key": "p.one", "polarity": "like", "strength": 0.68, "stability": 0.74,
                "source": "seed", "tags": ["style"],
                "created_at": 12.0, "updated_at": 22.0, "last_reinforced": 22.0,
            },
        },
        "habits": {},
        "last_feedback_events": [],
    }
    before = copy.deepcopy(source)
    projected = compact_agent_mind(source)
    # Telling/content survives on beliefs and preferences.
    for belief in projected["beliefs"].values():
        assert set(("claim", "confidence", "tags")) <= belief.keys()
        for cut in ("created_at", "updated_at", "last_seen", "source_ids",
                    "positive_evidence_count", "negative_evidence_count", "decay_rate"):
            assert cut not in belief, cut
    assert projected["preferences"]["p.one"]["polarity"] == "like"
    assert projected["preferences"]["p.one"]["strength"] == 0.68
    assert projected["preferences"]["p.one"]["stability"] == 0.74
    for cut in ("created_at", "updated_at", "last_reinforced", "source"):
        assert cut not in projected["preferences"]["p.one"], cut
    # Conflicting belief is kept; empty contradiction_ids is dropped.
    assert projected["beliefs"]["b.conflict"]["contradiction_ids"] == ["b.keep"]
    assert "contradiction_ids" not in projected["beliefs"]["b.keep"]
    # Empty habits / feedback log and the top-level epoch are gone.
    assert "habits" not in projected
    assert "last_feedback_events" not in projected
    assert "updated_at" not in projected
    assert projected["schema_version"] == 1
    # Input untouched, projection idempotent.
    assert source == before
    assert compact_agent_mind(projected) == projected


def test_compact_active_state_drops_empty_zero_keeps_meaningful():
    source = {
        "version": 1,
        "updated_at": "2026-10-24T22:42:36+0700",
        "current_goal": "",
        "current_goal_confidence": 0.0,
        "session_arc": "unknown",
        "active_project": "",
        "relation_state": {},
        "metadata": {
            "internal_state": {
                "valence": 0.02,
                "curiosity": 0.576,
                "silence_ticks": 0,
                "last_channel": "",
                "recent_notes": [],
                "last_social_presence_present": False,
                "desire_dynamics": {
                    "want": 0.16, "attraction_charge": 0.0,
                    "attachment_drift": "guarded", "recurring_aversion": "",
                    "recent_patterns": [],
                },
                "goal_persistence": {
                    "status": "empty", "strength": 0.0, "goal_id": "", "evidence": [],
                },
                "relationship_dynamics": {
                    "trust": 0.25, "familiarity": 0.0, "preferred_distance": 0.58,
                    "last_event": "maintenance_reset", "last_update_turn": 0,
                },
                "action_memory": {
                    "traces": [], "rhythm_phase": "settled", "last_spoken_ts": 0.0,
                },
            }
        },
    }
    before = copy.deepcopy(source)
    projected = compact_active_state(source)
    internal = projected["metadata"]["internal_state"]
    # Meaningful (non-default) values survive.
    assert internal["valence"] == 0.02
    assert internal["curiosity"] == 0.576
    assert internal["desire_dynamics"]["want"] == 0.16
    assert internal["desire_dynamics"]["attachment_drift"] == "guarded"
    assert internal["relationship_dynamics"]["trust"] == 0.25
    assert internal["relationship_dynamics"]["preferred_distance"] == 0.58
    assert internal["relationship_dynamics"]["last_event"] == "maintenance_reset"
    assert internal["action_memory"]["rhythm_phase"] == "settled"
    # Bool False is a real value, kept (not treated as zero).
    assert internal["last_social_presence_present"] is False
    assert projected["version"] == 1
    # Empty / zero / 'unknown'-default fields are gone.
    for cut in ("current_goal", "current_goal_confidence", "active_project",
                "relation_state", "session_arc"):
        assert cut not in projected, cut
    assert "silence_ticks" not in internal
    assert "last_channel" not in internal
    assert "recent_notes" not in internal
    assert "attraction_charge" not in internal["desire_dynamics"]
    assert "recurring_aversion" not in internal["desire_dynamics"]
    # goal_persistence keeps its only non-empty field (status='empty' is a real
    # string), and its zero/empty siblings are cut (safety rule: keep strings).
    assert internal["goal_persistence"] == {"status": "empty"}
    assert "familiarity" not in internal["relationship_dynamics"]
    assert "last_update_turn" not in internal["relationship_dynamics"]
    assert "traces" not in internal["action_memory"]
    # Input untouched, projection idempotent.
    assert source == before
    assert compact_active_state(projected) == projected


def test_compact_semantic_item_drops_raw_content_dupe_and_audit():
    claim = "Tôi có xu hướng duy trì sự thụ động trong tương tác."
    source = {
        "id": "sem_b1512e2cd608",
        "claim": claim,
        "domain": "unknown",
        "confidence": 0.82,
        "source": "state_commit",
        "evidence_ids": ["dossier.user.history.0", "epi_6f25e6a2bedf"],
        "observations": 1,
        "status": "active",
        "first_seen_at": "2026-08-19T09:18:13+0700",
        "updated_at": "2026-08-19T09:18:13+0700",
        "decay_policy": "slow",
        "contradiction_ids": [],
        "tags": ["commit", "typed_fact"],
        "metadata": {
            "fact_key": "active_passive_tendency",
            "domain": "self",
            "importance": 0.697,
            "source": "ai_curator_concern_bridge",
            "raw_content": claim,
            "reason": "concern_bridge_by_ai_curator",
            "aliases": [],
            "fact_profile_evidence": ["metadata.fact_key"],
            "embedding_text_hash": "40b957a3fcdd041c",
            "embedding_version": 1,
            "access_count": 0,
            "last_accessed_at": "",
        },
    }
    before = copy.deepcopy(source)
    projected = compact_semantic_item(source)
    # Telling + light qualifiers survive.
    assert projected["claim"] == claim
    assert projected["confidence"] == 0.82
    assert projected["status"] == "active"
    assert projected["tags"] == ["commit", "typed_fact"]
    assert projected["metadata"]["fact_key"] == "active_passive_tendency"
    assert projected["metadata"]["importance"] == 0.697
    assert projected["metadata"]["domain"] == "self"
    # Audit is gone (raw_content was a verbatim claim duplicate).
    for cut in ("evidence_ids", "first_seen_at", "updated_at"):
        assert cut not in projected, cut
    for cut in ("raw_content", "embedding_text_hash", "embedding_version",
                "access_count", "last_accessed_at", "fact_profile_evidence"):
        assert cut not in projected["metadata"], cut
    # Empty contradiction_ids dropped.
    assert "contradiction_ids" not in projected
    # Input untouched, projection idempotent.
    assert source == before
    assert compact_semantic_item(projected) == projected


def test_compact_mind_state_cuts_telemetry_keeps_cognition():
    source = {
        "schema": "eris.mind.v1",
        "version": 7,
        "last_applied_turn_seq": 42,
        "updated_at": 1787.0,
        "relationship": {"trust": 0.3, "closeness": 0.2, "rupture": 0.0},
        "appraisal": {"pleasantness": 0.1, "curiosity": 0.5, "pressure": 0.0},
        "personality": {
            "schema": "personality.v1", "version": 3,
            "core": {"warmth": 0.6}, "temperament": {"reactivity": 0.4},
            "mood": {"valence": 0.1}, "core_anchor": {"warmth": 0.55},
            "temperament_attractor": {"reactivity": 0.42}, "mood_baseline": {"valence": 0.05},
            "strategy_reinforcement": {"mirror": 0.3},
            "narrative": {"text": "settled", "confidence": 0.7},
            "relationship_profiles": {"primary": {"stance": "warm"}},
            "trajectory": [{"t": i} for i in range(96)],
            "consolidation_ledger": [{"c": i} for i in range(24)],
            "corroboration_records": {"warmth": [{"r": i} for i in range(16)]},
            "corroboration": {"warmth": 3}, "streaks": {"warmth": {"n": 5}},
            "day_usage": {"2026-10-14": 12},
            "last_consolidated_at": 1700.0, "last_decay_at": 1710.0, "last_updated_at": 1787.0,
        },
        "identity": {
            "schema": "identity.v1", "version": 2,
            "values": {"honesty": 0.8}, "motives": {"connect": 0.6},
            "defenses": {"withdraw": 0.2},
            "beliefs": {
                "b.keep": {"claim": "User likes directness.", "confidence": 0.7},
                "b.conflict": {"claim": "User wants silence now.", "confidence": 0.5,
                               "contradiction_ids": ["b.keep"]},
            },
            "emergent_values": {"playfulness": 0.4},
            "relationships": {"primary": {"trust": 0.3}},
            "strategies": {"mirror": {"weight": 0.3}},
            "pending_strategies": [{"name": "soften"}],
            "imprints": [{"moment": "first laugh"}],
            "active_motives": [{"name": "connect"}],
            "active_defenses": [{"name": "withdraw"}],
            "active_conflict": {"between": ["honesty", "kindness"]},
            "latest_value_choice": {"chose": "honesty"},
            "ledger": [{"l": i} for i in range(30)],
            "corroboration": {"honesty": 4},
            "last_updated_at": 1787.0,
        },
        "identity_learning_enabled": True,
        "latest_observation": {"turn_id": "t1", "confidence": 0.8},
        "latest_intention": {"plan": "listen"},
        "open_loops": [{"loop": "ask about trip"}],
        "goal_records": [{"goal": "support the user"}],
        "memory_candidates": [{"candidate": "user travels soon"}],
    }
    before = copy.deepcopy(source)
    projected = compact_mind_state(source)
    # Top-level telemetry/identifiers are gone.
    for cut in ("schema", "version", "last_applied_turn_seq", "updated_at"):
        assert cut not in projected, cut
    # Cognitive top-level branches survive untouched.
    assert projected["relationship"] == source["relationship"]
    assert projected["appraisal"] == source["appraisal"]
    assert projected["identity_learning_enabled"] is True
    assert projected["latest_observation"] == source["latest_observation"]
    assert projected["latest_intention"] == source["latest_intention"]
    assert projected["open_loops"] == source["open_loops"]
    assert projected["goal_records"] == source["goal_records"]
    assert projected["memory_candidates"] == source["memory_candidates"]
    # Personality: telemetry/history cut, cognition/anchors kept.
    pers = projected["personality"]
    for cut in ("trajectory", "consolidation_ledger", "corroboration_records",
                "corroboration", "streaks", "day_usage", "last_consolidated_at",
                "last_decay_at", "last_updated_at", "schema", "version"):
        assert cut not in pers, cut
    for keep in ("core", "temperament", "mood", "core_anchor", "temperament_attractor",
                 "mood_baseline", "strategy_reinforcement", "narrative", "relationship_profiles"):
        assert pers[keep] == source["personality"][keep], keep
    # Identity: audit cut, cognition + conflicting belief kept.
    ident = projected["identity"]
    for cut in ("ledger", "corroboration", "last_updated_at", "schema", "version"):
        assert cut not in ident, cut
    for keep in ("values", "motives", "defenses", "beliefs", "emergent_values",
                 "relationships", "strategies", "pending_strategies", "imprints",
                 "active_motives", "active_defenses", "active_conflict", "latest_value_choice"):
        assert ident[keep] == source["identity"][keep], keep
    # Conflicting belief (belief trái chiều) is preserved.
    assert ident["beliefs"]["b.conflict"]["contradiction_ids"] == ["b.keep"]
    assert ident["active_conflict"] == {"between": ["honesty", "kindness"]}
    # Unverified-but-kept branches are NOT cut.
    assert ident["imprints"] == [{"moment": "first laugh"}]
    assert ident["pending_strategies"] == [{"name": "soften"}]
    assert projected["memory_candidates"] == [{"candidate": "user travels soon"}]
    # Input untouched, projection idempotent.
    assert source == before
    assert compact_mind_state(projected) == projected


def test_self_view_removes_debug_and_clones_without_changing_owner():
    mind = {"identity": {"beliefs": ["uncertain", "opposing"]}, "relationship": {"last_event": "current"},
            "latest_observation": {"turn_id": "old", "confidence": 0.4}}
    persisted = copy.deepcopy(mind)
    persisted["relationship"]["last_event"] = "persisted variant"
    source = {"mind_state": mind, "memory_state": {
        "ai_orchestrated_state": persisted, "internal_state": {"energy": 0.1},
        "active_state": {"metadata": {"internal_state": {"energy": 0.1}}},
        "living_user": {"replay": {"timeline": ["raw debug"]}, "pending_turns": 3,
                        "recent_episodes": [{"title": "T", "summary": "real memory", "evidence_ids": ["u:1"],
                                             "session_id": "s", "started_at": 10, "ended_at": 20,
                                             "metadata": {"source_turns": [{"text": "raw"}]}}],
                        "last_dream_report": {"applied": 1}},
    }}
    original = copy.deepcopy(source)
    view = project_self_view(source)
    assert source == original
    # mind_state is now compacted: identity gets its own marker, the top-level
    # gets eris.mind_state.v1; the cognitive content (beliefs/relationship) is
    # unchanged. No telemetry fields existed in this fixture to drop.
    expected_mind = {
        "identity": {"beliefs": ["uncertain", "opposing"], "projection_schema": "eris.identity.v1"},
        "relationship": {"last_event": "current"},
        "latest_observation": {"turn_id": "old", "confidence": 0.4},
        "projection_schema": "eris.mind_state.v1",
    }
    assert view["mind_state"] == expected_mind
    # persisted stays raw, so state_difference surfaces the projection markers
    # (absent from persisted -> tombstones) plus the real persisted variant.
    assert view["memory_state"]["persisted_mind_variants"] == {
        "relationship": {"last_event": "persisted variant"},
        "projection_schema": {"__erased_from_self": True},
        "identity": {"projection_schema": {"__erased_from_self": True}},
    }
    # The duplicate internal_state is popped; the now-empty metadata (and the
    # emptied active_state body) is then pruned by compact_active_state, leaving
    # only the projection marker. internal_state no longer leaks into the prompt.
    active_view = view["memory_state"]["active_state"]
    assert "metadata" not in active_view
    assert active_view == {"projection_schema": "eris.active_state.v1"}
    episode = view["memory_state"]["living_user"]["recent_episodes"][0]
    assert episode["title"] == "T" and episode["summary"] == "real memory"
    for cut in ("evidence_ids", "metadata", "session_id", "started_at", "ended_at"):
        assert cut not in episode, cut
    assert view["memory_state"]["living_user"].keys() == {"recent_episodes"}
    assert project_self_view(view) == view
    context = {"conversation_status": {"latest_observation": mind["latest_observation"]},
               "grounding_context": "vision detail unchanged", "other": 1}
    projected = project_runtime_context(context, view)
    assert projected["conversation_status"] == {"latest_observation_ref": "mind_state.latest_observation"}
    assert projected["grounding_context"] == context["grounding_context"]
    context["conversation_status"]["latest_observation"] = {"turn_id": "different"}
    assert project_runtime_context(context, view) == context


def test_root_loads_guide_then_executes_with_same_schema_and_no_fake_receipt(monkeypatch):
    monkeypatch.setenv("ERIS_ROOT_SESSION_BOOTSTRAP", "1")
    monkeypatch.setenv("ERIS_WEB_SESSION_MODE", "isolated")
    executions = []
    class Action:
        def invoke_for_root(self, **kwargs):
            executions.append(kwargs)
            return {"status": "success"}
    registry = RootCapabilityRegistry({"action": Action()})
    router = Router(
        {"kind": "read_tool_guide", "guide": "math_workspace"},
        {"kind": "batch", "actions": [{"kind": "use_capability", "capability": "action", "arguments": {"action_id": "math_workspace", "arguments": {"kind": "simplify", "expression": "1+1"}}}]},
        {"kind": "no_reply", "reason": "done"},
    )
    agent = RuntimeModelRootAgent(router)
    agent._root_session._host = SimpleNamespace(memory_state={"fact": "x"})
    decision = agent.decide(event=ConversationEvent(event_id="e1", session_id="s", kind="user_turn", text="calculate"), recent_history=[], note="",
                            runtime_context={"__root_capabilities": registry})
    assert decision.reason == "done"
    assert len(executions) == 1
    from runtime.conversation.agent import root_step_schema
    assert all(call["schema"] == root_step_schema() for call in router.calls)
    step = json.loads(router.calls[2]["conversation_messages"][-1]["content"])
    guide = step["tool_results"][0]
    assert guide["documentation"] == TOOL_GUIDES["math_workspace"]
    assert guide["authority"] == "documentation_only_not_execution"
    assert "receipt_ref" not in guide
    assert "Đặc biệt khi đề bài" not in router.calls[0]["system"]


def test_loaded_guides_survive_more_than_six_tool_results():
    guide = {"kind": "tool_guide_result", "status": "found", "guide": "work", "documentation": "manual"}
    payload = RuntimeModelRootAgent._payload(
        event=ConversationEvent(event_id="e", session_id="s", kind="user_turn", text="next"), recent_history=[], note="", runtime_context={},
        tool_results=[guide] + [{"kind": "capability_result", "i": n} for n in range(10)], step_index=10,
    )
    assert payload["tool_results"][0] == guide
    assert len(payload["tool_results"]) == 7


def test_unknown_guide_does_not_count_as_progress():
    result = read_tool_guide("unknown")
    assert result["status"] == "unavailable"
    assert not RuntimeModelRootAgent._is_meaningful_result(result, prior_fingerprints=set())

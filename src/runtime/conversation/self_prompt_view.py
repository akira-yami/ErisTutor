"""Pure transport views for Root; canonical stores and beliefs stay unchanged."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from typing import Any, Mapping, Sequence


def _same_json(left: Any, right: Any) -> bool:
    # Python considers False == 0; a transport patch must preserve JSON types.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right, strict=False))
    return left == right


def state_difference(previous: Any, current: Any) -> Any:
    """Merge patch: dictionaries merge, arrays/scalars replace, explicit tombstones."""
    if _same_json(previous, current):
        return None
    if isinstance(previous, dict) and isinstance(current, dict):
        out = {key: {"__erased_from_self": True} for key in previous.keys() - current.keys()}
        for key, value in current.items():
            if key not in previous:
                out[key] = value
            else:
                diff = state_difference(previous[key], value)
                if diff is not None:
                    out[key] = diff
        return out or None
    return {"__value_now_null": True} if current is None else current


# Pure audit/provenance fields dropped from each profile record on the Root
# prompt path. They never steer a response and are the heaviest part of the
# block: evidence_ids (up to 96 ids/record) plus four raw epoch timestamps and
# the free-text metadata dict. Also dropped are the technical qualifiers that no
# Root-prompt consumer reads: id/value_type/cardinality/recurrence/tier/source
# (internal identifiers and book-keeping, not the telling). The canonical store
# keeps them all via to_dict(); only this projection omits them. valid_until/
# expires_at are intentionally left for the defaults pass (retention qualifiers,
# not audit ids). scope/sensitivity are KEPT (scope/redaction downstream).
_PROFILE_AUDIT_FIELDS = (
    "evidence_ids", "metadata",
    "created_at", "updated_at", "last_confirmed_at", "valid_from",
    "id", "value_type", "cardinality", "recurrence", "tier", "source",
)

_CORE_PROFILE_PREFIXES = (
    "identity.", "communication.", "technical.profile", "technical.projects", "personality.", "preferences.", "relationship."
)


def _synthesize_narrative_summary(records: Sequence[Mapping[str, Any]]) -> str:
    # 1. Try reading saved Gemini portrait from disk if available
    candidates = []
    import os
    from pathlib import Path
    eris_home = os.environ.get("ERIS_HOME", "").strip()
    if eris_home:
        candidates.append(Path(eris_home) / "memory" / "core" / "living_user_portrait.json")
    candidates.append(Path.home() / "Documents" / "eris" / ".eris_home" / "memory" / "core" / "living_user_portrait.json")
    for cand in candidates:
        try:
            if cand.is_file():
                doc = json.loads(cand.read_text(encoding="utf-8"))
                opening = str(doc.get("portrait", {}).get("opening") or "").strip()
                if opening:
                    return opening
        except Exception:
            pass

    # 2. Fallback to deterministic synthesis from records
    unique: dict[str, Any] = {}
    for r in records:
        p = r.get("path")
        if p and (p not in unique or r.get("explicitness") == "explicit"
                  or float(r.get("confidence", 0) or 0) > float(unique[p].get("confidence", 0) or 0)):
            unique[p] = r
    name = unique.get("identity.name", {}).get("value", "Hoà")
    rel = unique.get("identity.relationship_to_eris", {}).get("value", "Người tạo ra ERIS")
    mbti = unique.get("identity.personality_typology.mbti_self_identification", {}).get("value", "")
    comm = unique.get("communication.interaction_preferences", {}).get("value", "")
    style = unique.get("communication.desired_response_style", {}).get("value", "")

    parts = [f"Người dùng là {name} ({rel})."]
    if mbti:
        parts.append(f"MBTI: {mbti}.")
    if comm:
        parts.append(f"Phong cách: {comm}.")
    elif style:
        parts.append(f"Kỳ vọng phản hồi: {style}.")
    return " ".join(parts)


def compact_profile(profile: Mapping[str, Any], max_records: int = 16) -> dict[str, Any]:
    """Transport projection for Root: drop audit/provenance, keep the telling.

    Keeps path/value and the telling qualifiers (explicitness, status,
    confidence) plus scope/sensitivity. Drops evidence_ids, the raw epoch
    timestamps, the metadata dict and the technical qualifiers (id/value_type/
    cardinality/recurrence/tier/source) because no Root-prompt consumer reads
    them and they dominate the block size. Not lossless; the canonical store
    is untouched (store.save() uses ProfileRecord.to_dict(), not this).
    """
    result = deepcopy(dict(profile))
    result.pop("history", None)
    result.pop("audit", None)
    records = result.get("records")
    if not isinstance(records, list) or not records or not all(isinstance(row, dict) for row in records):
        return result
    if result.get("projection_schema") == "eris.root_profile.v1":
        return result
    for row in records:
        for field in _PROFILE_AUDIT_FIELDS:
            row.pop(field, None)
    if "summary" not in result and records:
        result["summary"] = _synthesize_narrative_summary(records)
    if len(records) > max_records:
        unique: dict[str, dict[str, Any]] = {}
        for row in records:
            p = str(row.get("path") or "")
            if p:
                if p not in unique:
                    unique[p] = row
                elif row.get("explicitness") == "explicit" or float(row.get("confidence", 0) or 0) > float(unique[p].get("confidence", 0) or 0):
                    unique[p] = row

        def rank(r: dict[str, Any]) -> tuple[int, int, float]:
            p = str(r.get("path") or "")
            is_core = any(p.startswith(pfx) for pfx in _CORE_PROFILE_PREFIXES)
            is_explicit = r.get("explicitness") == "explicit"
            conf = float(r.get("confidence", 0.0) or 0.0)
            return (0 if is_core else 1, 0 if is_explicit else 1, -conf)

        sorted_records = sorted(unique.values(), key=rank)
        records = sorted_records[:max_records]
        result["records"] = records
        result["record_count"] = len(records)
    # cardinality/recurrence/tier/source/value_type are popped at the record
    # level above (audit qualifiers), so they are no longer candidates to
    # promote. Only the kept qualifiers factor into defaults.
    defaults: dict[str, Any] = {}
    for key in ("scope", "status", "sensitivity", "valid_until", "expires_at"):
        if not all(key in row for row in records):
            continue
        encoded = [json.dumps(row[key], ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in records]
        value, count = Counter(encoded).most_common(1)[0]
        if count < 2:
            continue
        defaults[key] = json.loads(value)
        for row in records:
            if _same_json(row[key], defaults[key]):
                del row[key]
    # Timestamp aliases are gone: both alias sources (updated_at, created_at)
    # and one target (valid_from) are now cut above, so no alias can form.
    result.update(projection_schema="eris.root_profile.v1", defaults=defaults, aliases={})
    return result


# Audit/provenance fields dropped from each episode on the Root prompt path.
# evidence_ids runs up to 128 ids/episode; session_id/source are internal
# plumbing; started_at/ended_at are raw epochs; metadata carried source_turns.
# The telling (title/summary/outcome and the signal lists) is kept intact.
_EPISODE_AUDIT_FIELDS = (
    "evidence_ids", "metadata", "started_at", "ended_at", "session_id", "source",
)


def compact_episode(episode: Mapping[str, Any]) -> dict[str, Any]:
    """Transport projection for one Root episode: drop audit, keep the telling.

    Keeps title/summary/outcome/topics/entities/decisions/open_loops/
    user_signals/importance/scope/id (length caps already applied by
    UserEpisode.__post_init__). Idempotent via a projection marker so
    state_difference never sees a phantom delta between turns.
    """
    if not isinstance(episode, Mapping):
        return episode  # type: ignore[return-value]
    result = deepcopy(dict(episode))
    if result.get("projection_schema") == "eris.root_episode.v1":
        return result
    for field in _EPISODE_AUDIT_FIELDS:
        result.pop(field, None)
    result["projection_schema"] = "eris.root_episode.v1"
    return result


# Audit/provenance popped from each AgentMind belief/preference on the Root
# prompt path. Beliefs/preferences are Eris' personality (10 beliefs + 12
# preferences) and MUST stay — only the per-record bookkeeping is dropped: raw
# epoch timestamps, feedback-count integers, source id lists and the decay knob.
# The canonical store keeps them via PersistentAgentMindStore.to_dict(); only
# this projection omits them. contradiction_ids is cut ONLY when empty (a
# non-empty one is a conflicting belief, a product requirement to keep).
_BELIEF_AUDIT_FIELDS = (
    "created_at", "updated_at", "last_seen",
    "source_ids", "positive_evidence_count", "negative_evidence_count", "decay_rate",
    "key",
)
_PREFERENCE_AUDIT_FIELDS = (
    "created_at", "updated_at", "last_reinforced", "source", "key", "tags",
)


def compact_agent_mind(agent_mind: Mapping[str, Any]) -> dict[str, Any]:
    """Transport projection for Root: drop belief/preference audit, keep the telling.

    Keeps claim/confidence/tags/domain/key on beliefs and
    key/polarity/strength/stability/tags on preferences; keeps non-empty
    contradiction_ids (conflicting beliefs). Drops the raw epoch timestamps,
    the evidence-count integers, source id lists and the decay knob because no
    Root-prompt consumer reads them and they dominate the block. Not lossless;
    the canonical store (store.save() -> to_dict()) is untouched. Idempotent via
    a projection marker so state_difference sees no phantom delta between turns.
    """
    result = deepcopy(dict(agent_mind))
    if result.get("projection_schema") == "eris.agent_mind.v1":
        return result
    beliefs = result.get("beliefs")
    if isinstance(beliefs, dict):
        for record in beliefs.values():
            if not isinstance(record, dict):
                continue
            for field in _BELIEF_AUDIT_FIELDS:
                record.pop(field, None)
            if not record.get("contradiction_ids"):
                record.pop("contradiction_ids", None)
    preferences = result.get("preferences")
    if isinstance(preferences, dict):
        for record in preferences.values():
            if not isinstance(record, dict):
                continue
            for field in _PREFERENCE_AUDIT_FIELDS:
                record.pop(field, None)
    # Empty habits / feedback log and the top-level epoch carry no telling.
    if result.get("habits") == {}:
        result.pop("habits", None)
    if result.get("last_feedback_events") == []:
        result.pop("last_feedback_events", None)
    result.pop("updated_at", None)
    result["projection_schema"] = "eris.agent_mind.v1"
    return result


def _is_empty_default(value: Any) -> bool:
    """True for the empty/zero/None defaults that dominate active_state.

    Bools are never treated as zero (False is a real value, kept); only real
    numeric zero, empty string, empty list and empty dict count as empties.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        return value == ""
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    if isinstance(value, dict):
        return len(value) == 0
    return False


def _prune_empty(value: Any) -> Any:
    """Recursively drop empty/zero/None keys; keep everything meaningful.

    Dicts lose keys whose (recursively pruned) value is an empty default; a dict
    that becomes empty is itself an empty default and is dropped by its parent.
    Lists keep their items (order/shape are telling) but recurse into them.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, inner in value.items():
            pruned = _prune_empty(inner)
            if not _is_empty_default(pruned):
                out[key] = pruned
        return out
    if isinstance(value, list):
        return [_prune_empty(item) for item in value]
    return value


def compact_active_state(active_state: Mapping[str, Any]) -> dict[str, Any]:
    """Transport projection for Root: drop empty/zero/default active_state fields.

    active_state is almost entirely metadata.internal_state with dozens of empty
    last_*/recent_* strings/lists and zeroed mental_processes / *_dynamics. This
    keeps every field that differs from the empty default (trust=0.25,
    valence=0.02, curiosity=0.576, non-empty strings like rhythm_phase='settled',
    mode='settled', attachment_drift='guarded', version strings) and drops the
    rest. Safety rule: only empties/zeros go; bools and real numbers stay. The
    canonical ActiveStateSnapshot.to_dict() / store.save() is untouched.
    Idempotent via a projection marker.
    """
    result = deepcopy(dict(active_state))
    if result.get("projection_schema") == "eris.active_state.v1":
        return result
    # session_arc defaults to the literal 'unknown'; treat it as an empty default.
    if result.get("session_arc") == "unknown":
        result.pop("session_arc", None)
    pruned = _prune_empty(result)
    pruned["projection_schema"] = "eris.active_state.v1"
    return pruned


# Audit/provenance popped from each SemanticMemoryItem on the Root prompt path.
# Top-level evidence ids and the raw iso timestamps, plus the metadata audit set
# (raw_content duplicates claim verbatim; the embedding hash/version and access
# counters never steer a response). The telling (claim/confidence/status/tags
# and the light qualifiers fact_key/domain/importance) is kept.
_SEMANTIC_TOP_AUDIT_FIELDS = (
    "evidence_ids", "first_seen_at", "updated_at",
    "decay_policy", "observations",
)
_SEMANTIC_META_AUDIT_FIELDS = (
    "raw_content", "embedding_text_hash", "embedding_version",
    "access_count", "last_accessed_at", "fact_profile_evidence",
    "semantic_claim", "claim_provenance", "memory_kernel_context",
    "lineage_root_ids", "retention_class", "revisit_after_seconds",
    "prune_after_seconds", "promote_if_observed", "demote_if_unaccessed_days",
    "retention_reason", "retention_warnings", "memory_kind", "surface_mode",
    "permission", "commit_kind", "memory_kernel_mode", "memory_kernel_source",
    "predicate", "subject", "object_type", "assertion", "modality", "temporal",
    "scope", "source_grounded", "semantic_source", "extraction", "raw_user_text",
    "reason", "source", "query_examples", "aliases",
)


def compact_semantic_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Transport projection for one Root semantic record: drop audit, keep claim.

    Keeps id/claim/confidence/status/tags plus the light metadata qualifiers
    (fact_key/domain/importance); keeps non-empty contradiction_ids (conflicting
    facts). Drops evidence_ids, the iso timestamps and the metadata audit set
    (raw_content dup, embedding hash/version, access counters). Not lossless; the
    canonical SemanticMemoryItem.to_dict() is untouched. Idempotent via a marker.
    """
    if not isinstance(item, Mapping):
        return item  # type: ignore[return-value]
    result = deepcopy(dict(item))
    if result.get("projection_schema") == "eris.semantic_item.v1":
        return result
    for field in _SEMANTIC_TOP_AUDIT_FIELDS:
        result.pop(field, None)
    if not result.get("contradiction_ids"):
        result.pop("contradiction_ids", None)
    metadata = result.get("metadata")
    if isinstance(metadata, dict):
        for field in _SEMANTIC_META_AUDIT_FIELDS:
            metadata.pop(field, None)
        if metadata == {}:
            result.pop("metadata", None)
    result["projection_schema"] = "eris.semantic_item.v1"
    return result


# Telemetry/history popped from the mind_state block on the Root prompt path.
# These three lists are the per-session growth source (trajectory loads up to 96
# records, corroboration_records 16/key, consolidation_ledger 24) plus the
# counter dicts (corroboration/streaks/day_usage) and the raw epoch timestamps.
# No Root-prompt consumer reads them: prompt_summary()/prompt_context() never
# touch them, and SELF_BOOTSTRAP_INSTRUCTION points Root at belief/value/stance,
# not at trajectory/ledger/counters. The cognitive content (core/temperament/
# mood and the *_anchor/*_baseline attractors) is kept intact.
_PERSONALITY_AUDIT_FIELDS = (
    "trajectory", "consolidation_ledger", "corroboration_records",
    "corroboration", "streaks", "day_usage",
    "last_consolidated_at", "last_decay_at", "last_updated_at",
    "schema", "version",
)
# Audit/book-keeping popped from the identity block on the Root prompt path.
# ledger/corroboration are the accumulating audit trail; last_updated_at is a
# raw epoch; schema/version are identifiers. The cognitive content stays:
# values/beliefs/motives/defenses/emergent_values/relationships, active_conflict
# and latest_value_choice (conflicting beliefs), plus pending_strategies/imprints
# and active_motives/active_defenses (kept per the "keep when unverified" rule).
_IDENTITY_AUDIT_FIELDS = (
    "ledger", "corroboration", "last_updated_at", "schema", "version",
)
# Top-level RuntimeMindState identifiers/telemetry. schema/version are version
# ids, last_applied_turn_seq is a sequence counter and updated_at is a raw epoch;
# none steer a response. The telling branches stay: relationship, appraisal,
# personality (compacted), identity (compacted), identity_learning_enabled,
# latest_observation, latest_intention, open_loops, goal_records (the user's
# goals) and memory_candidates.
_MIND_TOP_AUDIT_FIELDS = (
    "schema", "version", "last_applied_turn_seq", "updated_at",
)


def compact_personality(personality: Mapping[str, Any]) -> dict[str, Any]:
    """Transport projection for mind_state.personality: drop telemetry/history.

    Keeps core/temperament/mood and the *_anchor/*_baseline attractors plus
    strategy_reinforcement/narrative/relationship_profiles (the cognitive
    content). Drops trajectory/consolidation_ledger/corroboration_records and
    the counter dicts/timestamps because no Root-prompt consumer reads them and
    they are the per-session growth source. The canonical
    PersonalityProfile.to_dict() is untouched. Idempotent via a marker.
    """
    result = deepcopy(dict(personality))
    if result.get("projection_schema") == "eris.personality.v1":
        return result
    for field in _PERSONALITY_AUDIT_FIELDS:
        result.pop(field, None)
    result["projection_schema"] = "eris.personality.v1"
    return result


def compact_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    """Transport projection for mind_state.identity: drop audit/book-keeping.

    Keeps values/beliefs/motives/defenses/emergent_values/relationships,
    active_conflict and latest_value_choice (conflicting beliefs the product
    requires), plus pending_strategies/imprints/active_motives/active_defenses.
    Drops the ledger/corroboration audit trail and the raw timestamp/ids. The
    canonical IdentityProfile.to_dict() is untouched. Idempotent via a marker.
    """
    result = deepcopy(dict(identity))
    if result.get("projection_schema") == "eris.identity.v1":
        return result
    for field in _IDENTITY_AUDIT_FIELDS:
        result.pop(field, None)
    result["projection_schema"] = "eris.identity.v1"
    return result


def compact_mind_state(mind_state: Mapping[str, Any]) -> dict[str, Any]:
    """Transport projection for the Root mind_state block: cut telemetry/history.

    Compacts the personality/identity sub-blocks (see compact_personality /
    compact_identity) and drops the top-level version/schema/sequence/epoch
    identifiers. Keeps every cognitive branch (relationship, appraisal,
    latest_observation/intention, open_loops, goal_records, memory_candidates).
    The reducer's RuntimeMindState.to_dict() / persistence is untouched.
    Idempotent via a marker so state_difference sees no phantom delta between
    the bootstrap snapshot and self-now (both run through project_self_view).
    """
    result = deepcopy(dict(mind_state))
    if result.get("projection_schema") == "eris.mind_state.v1":
        return result
    if isinstance(result.get("personality"), Mapping):
        result["personality"] = compact_personality(result["personality"])
    if isinstance(result.get("identity"), Mapping):
        result["identity"] = compact_identity(result["identity"])
    for field in _MIND_TOP_AUDIT_FIELDS:
        result.pop(field, None)
    result["projection_schema"] = "eris.mind_state.v1"
    return result


_INTERNAL_RESIDUE_FIELDS = (
    "recent_subconscious_seeds", "recent_thought_seeds", "recent_stances",
    "recent_speech_acts", "recent_inner_intents", "recent_self_state_labels",
    "recent_social_presence", "recent_notes", "recent_channels", "recent_focus_kinds",
    "mental_processes", "desire_dynamics", "goal_persistence",
    "cycle_count", "silence_ticks", "spoken_ticks", "chat_ticks", "force_speak_ticks",
    "last_cognition_at", "last_elapsed_seconds", "last_session_ritual_at", "last_session_greeting_at",
    "action_memory", "conversation_dynamics",
)


def compact_actions(actions: Mapping[str, Any]) -> dict[str, Any]:
    """Transport projection for actions: keep active goals/workflows, drop audit."""
    if not isinstance(actions, Mapping):
        return {}
    if actions.get("projection_schema") == "eris.root_actions.v1":
        return dict(actions)
    active_goals = [
        {
            "id": str(g.get("id") or "")[:64],
            "goal": str(g.get("title") or g.get("goal") or g.get("description") or "")[:200],
            "status": str(g.get("status") or "active")[:32],
        }
        for g in actions.get("goals", [])
        if isinstance(g, Mapping) and str(g.get("status", "")).lower() in ("active", "in_progress", "pending")
    ][:3]
    active_workflows = [
        {
            "id": str(w.get("id") or "")[:64],
            "title": str(w.get("title") or w.get("name") or "")[:200],
            "status": str(w.get("status") or "active")[:32],
        }
        for w in actions.get("workflows", [])
        if isinstance(w, Mapping) and str(w.get("status", "")).lower() in ("active", "in_progress")
    ][:2]
    out: dict[str, Any] = {"projection_schema": "eris.root_actions.v1"}
    if active_goals:
        out["goals"] = active_goals
    if active_workflows:
        out["workflows"] = active_workflows
    return out


def compact_internal_state(internal_state: Mapping[str, Any]) -> dict[str, Any]:
    """Transport projection for internal_state: drop subconscious residue and ticks."""
    if not isinstance(internal_state, Mapping):
        return {}
    if internal_state.get("projection_schema") == "eris.root_internal.v1":
        return dict(internal_state)
    out: dict[str, Any] = {}
    for k, v in internal_state.items():
        if k in _INTERNAL_RESIDUE_FIELDS:
            continue
        if v in ("", 0, 0.0, None, [], {}):
            continue
        if k == "affective_state" and isinstance(v, Mapping):
            mood = {mk: mv for mk, mv in v.get("mood", {}).items() if mv}
            impulse = {ik: iv for ik, iv in v.get("impulse", {}).items() if iv}
            aff: dict[str, Any] = {"mood": mood}
            if impulse:
                aff["impulse"] = impulse
            out[k] = aff
        elif k == "relationship_dynamics" and isinstance(v, Mapping):
            rd = {rk: rv for rk, rv in v.items() if rv not in ("", 0, 0.0, None, [], {}) and rk not in ("last_event", "last_update_turn")}
            if rd:
                out[k] = rd
        else:
            out[k] = v
    out["projection_schema"] = "eris.root_internal.v1"
    return out


def project_self_view(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Remove diagnostic views and exact clones, preserving differing source data."""
    result = deepcopy(dict(snapshot))
    memory = result.get("memory_state")
    if not isinstance(memory, dict):
        return result
    memory.pop("concept_graph", None)
    living = memory.get("living_user")
    if isinstance(living, dict):
        for key in ("replay", "last_dream_report", "pending_turns", "active_session_id"):
            living.pop(key, None)
        if isinstance(living.get("profile"), Mapping):
            living["profile"] = compact_profile(living["profile"])
            if "summary" in living["profile"] and "summary" not in living:
                living["summary"] = living["profile"]["summary"]
        if isinstance(living.get("actions"), Mapping):
            living["actions"] = compact_actions(living["actions"])
        # Episodes carry audit refs (evidence_ids/source_turns), internal ids
        # and raw epochs that no Root-prompt consumer reads. Keep the telling
        # (title/summary/outcome/signal lists); drop the audit/provenance.
        episodes = living.get("recent_episodes")
        if isinstance(episodes, list):
            living["recent_episodes"] = [
                compact_episode(episode) if isinstance(episode, Mapping) else episode
                for episode in episodes
            ]
    active = memory.get("active_state")
    metadata = active.get("metadata") if isinstance(active, dict) else None
    if isinstance(metadata, dict) and "internal_state" in memory and _same_json(metadata.get("internal_state"), memory["internal_state"]):
        metadata.pop("internal_state", None)
    if isinstance(memory.get("internal_state"), Mapping):
        memory["internal_state"] = compact_internal_state(memory["internal_state"])
    # agent_mind / active_state / semantic_memory are the next heavy Self
    # branches: cut per-record audit/provenance and empty/zero defaults, keep
    # the belief/preference/claim content (and conflicting beliefs). The
    # active_state pop above runs first so the compact pass never has to prune
    # a now-removed internal_state; compact_active_state is idempotent anyway.
    if isinstance(memory.get("agent_mind"), Mapping):
        memory["agent_mind"] = compact_agent_mind(memory["agent_mind"])
    if isinstance(memory.get("active_state"), dict):
        memory["active_state"] = compact_active_state(memory["active_state"])
    if isinstance(memory.get("semantic_memory"), list):
        memory["semantic_memory"] = [
            compact_semantic_item(entry) if isinstance(entry, Mapping) else entry
            for entry in memory["semantic_memory"]
        ]
    # mind_state is the top-level Self prefix branch (set by _read_self, NOT in
    # memory_state). Cut its telemetry/history/identifiers here, before the
    # persisted-variant diff below reads it as `primary`, so both bootstrap and
    # self-now go through the same compacted shape (no phantom delta). persisted
    # (ai_orchestrated_state) stays raw, so state_difference will surface the
    # cut fields under persisted_mind_variants; that is persistence evidence,
    # not cognitive content, and stays small.
    if isinstance(result.get("mind_state"), Mapping):
        result["mind_state"] = compact_mind_state(result["mind_state"])
    persisted = memory.get("ai_orchestrated_state")
    primary = result.get("mind_state")
    if isinstance(persisted, dict) and isinstance(primary, dict):
        differences = state_difference(primary, persisted)
        memory.pop("ai_orchestrated_state")
        if differences is not None:
            # Reducer remains authoritative. Keep unique persistence evidence as
            # a labelled alternative, not a second full current MindState.
            memory["persisted_mind_variants"] = differences
    return result


def project_bootstrap_self(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Keep fading episode views in the current overlay, never a frozen prefix.

    Other Self fields retain their immutable bootstrap + cumulative delta
    contract. Modern episode fields are added in full by each current overlay.
    """
    result = deepcopy(dict(snapshot))
    memory = result.get("memory_state")
    living = memory.get("living_user") if isinstance(memory, dict) else None
    if isinstance(living, dict) and "episode_recap" in living:
        living.pop("recent_episodes", None)
        living.pop("episode_recap", None)
    return result


def project_runtime_context(context: Mapping[str, Any], self_view: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(context))
    status = result.get("conversation_status")
    mind = self_view.get("mind_state")
    if isinstance(status, dict) and isinstance(mind, dict) and "latest_observation" in status and "latest_observation" in mind:
        if _same_json(status["latest_observation"], mind["latest_observation"]):
            status.pop("latest_observation")
            status["latest_observation_ref"] = "mind_state.latest_observation"
    memory = self_view.get("memory_state")
    living = memory.get("living_user", {}) if isinstance(memory, Mapping) else {}
    recap = living.get("episode_recap") if isinstance(living, Mapping) else None
    memory_text = result.get("memory_context")
    if recap and isinstance(memory_text, str):
        start, end = "[EPISODE_RECAP]", "[/EPISODE_RECAP]"
        left, right = memory_text.find(start), memory_text.find(end)
        if left >= 0 and right >= left and recap in memory_text[left:right]:
            result["memory_context"] = (memory_text[:left] + memory_text[right + len(end):]).strip()
    # Grounding/vision is deliberately untouched (step 5 is deferred).
    return result

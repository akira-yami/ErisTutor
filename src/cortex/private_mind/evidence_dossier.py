# -*- coding: utf-8 -*-
"""
cortex/private_mind/evidence_dossier.py
=======================================

EvidenceDossierBuilder gom hồ sơ cho private mind:
- recent user turns;
- runtime state;
- memory hits;
- repair failures;
- active goal/current task;
- surface outcome;
- concern snapshots.

Tất cả evaluator đọc cùng dossier. Không evaluator nào được lén thò tay vào
telemetry/memory để tự bói riêng. Một module một sự thật riêng là cách repo biến
thành hội đồng mê tín có type hint.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping

from .contracts import EvidenceDossier, clamp01, compact_text, stable_id, to_plain


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _call(obj: Any, name: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    fn = getattr(obj, name, None)
    if not callable(fn):
        return default
    try:
        return fn(*args, **kwargs)
    except Exception:
        return default


class EvidenceDossierBuilder:
    """Build context packet an toàn cho private mind."""

    def __init__(self, *, max_user_turns: int = 8, max_memory_hits: int = 8, max_repairs: int = 6) -> None:
        self.max_user_turns = max(1, int(max_user_turns or 8))
        self.max_memory_hits = max(0, int(max_memory_hits or 8))
        self.max_repairs = max(0, int(max_repairs or 6))

    def build(
        self,
        *,
        user_text: str = "",
        channel: str = "autonomous",
        snapshot: Any = None,
        observer_frame: Any = None,
        attention_signal: Any = None,
        memory_core: Any = None,
        memory_hits: Iterable[Any] = (),
        session_turn_ledger: Any = None,
        internal_agenda: Any = None,
        concern_store: Any = None,
        thought_stream: Any = None,
        surface_candidate_queue: Iterable[Any] = (),
        runtime_owner: Any = None,
        surface_outcome: Mapping[str, Any] | None = None,
        active_goal: str = "",
        current_task: str = "",
        user_model_snapshot: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> EvidenceDossier:
        created_at = time.time()
        evidence_index: dict[str, Any] = {}
        recent_turns = self._recent_user_turns(user_text=user_text, ledger=session_turn_ledger, evidence_index=evidence_index)
        runtime_state = self._runtime_state(
            channel=channel,
            snapshot=snapshot,
            observer_frame=observer_frame,
            attention_signal=attention_signal,
            internal_agenda=internal_agenda,
            thought_stream=thought_stream,
            surface_candidate_queue=surface_candidate_queue,
            runtime_owner=runtime_owner,
            evidence_index=evidence_index,
        )
        resolved_memory_hits = tuple(self._memory_hits(memory_core=memory_core, explicit_hits=memory_hits, evidence_index=evidence_index))
        repair_failures = tuple(self._repair_failures(memory_core=memory_core, evidence_index=evidence_index))
        concerns = tuple(_call(concern_store, "snapshots", limit=8, default=()) or ())
        for idx, concern in enumerate(concerns):
            eid = f"dossier.concern.{idx}"
            evidence_index[eid] = concern
        goal = active_goal or self._active_goal_from_agenda(internal_agenda)
        task = current_task or self._task_from_snapshot(snapshot, observer_frame, recent_turns)
        evidence_metrics = self._evidence_metrics(
            recent_turns=recent_turns,
            runtime_state=runtime_state,
            evidence_index=evidence_index,
            memory_hits=resolved_memory_hits,
            repair_failures=repair_failures,
            concerns=concerns,
        )
        if surface_outcome:
            evidence_index["dossier.surface.outcome"] = dict(surface_outcome)
        dossier_id = stable_id("dossier", created_at, user_text, channel, task, goal)
        return EvidenceDossier(
            dossier_id=dossier_id,
            created_at=created_at,
            recent_user_turns=tuple(recent_turns),
            runtime_state=runtime_state,
            memory_hits=resolved_memory_hits,
            prior_repair_failures=repair_failures,
            active_goal=compact_text(goal, 280),
            current_task=compact_text(task, 320),
            surface_outcome=dict(surface_outcome or {}),
            concern_snapshots=concerns,
            user_model_snapshot=dict(user_model_snapshot or {}),
            evidence_index=evidence_index,
            metadata={"channel": channel, **evidence_metrics, **dict(metadata or {})},
        )

    def _recent_user_turns(self, *, user_text: str, ledger: Any, evidence_index: dict[str, Any]) -> list[dict[str, Any]]:
        turns: list[dict[str, Any]] = []
        if user_text.strip():
            eid = "dossier.user.current"
            item = {"evidence_id": eid, "text": compact_text(user_text, 1200), "source": "current_user_text", "timestamp": time.time()}
            turns.append(item)
            evidence_index[eid] = item

        # Hỗ trợ nhiều shape ledger khác nhau. Repo đang tiến hóa, nghĩa là API
        # nội bộ cũng thích thay áo giữa chợ.
        raw_turns = []
        for attr in ("turns", "items", "records", "messages"):
            value = _get(ledger, attr)
            if isinstance(value, list):
                raw_turns = value
                break
        if not raw_turns:
            raw_turns = _call(ledger, "recent_turns", limit=self.max_user_turns, default=[]) or _call(ledger, "last_turns", limit=self.max_user_turns, default=[]) or []
        for idx, raw in enumerate(list(raw_turns)[-self.max_user_turns:]):
            role = str(_get(raw, "role", _get(raw, "speaker", "")) or "").lower()
            if role and role not in {"user", "human", "bạn"}:
                continue
            text = compact_text(_get(raw, "text", _get(raw, "content", raw)), 1200)
            if not text or (user_text and text == compact_text(user_text, 1200)):
                continue
            eid = str(_get(raw, "evidence_id", _get(raw, "id", "")) or f"dossier.user.history.{idx}")
            item = {"evidence_id": eid, "text": text, "source": "session_turn_ledger", "timestamp": _get(raw, "timestamp", None)}
            turns.append(item)
            evidence_index[eid] = item
        return turns[-self.max_user_turns :]

    def _runtime_state(
        self,
        *,
        channel: str,
        snapshot: Any,
        observer_frame: Any,
        attention_signal: Any,
        internal_agenda: Any,
        thought_stream: Any,
        surface_candidate_queue: Iterable[Any],
        runtime_owner: Any,
        evidence_index: dict[str, Any],
    ) -> dict[str, Any]:
        thought_state = self._thought_stream_state(thought_stream)
        private_state = self._private_runtime_state(runtime_owner)
        surface_queue_state = self._surface_queue_state(surface_candidate_queue)
        state = {
            "channel": channel,
            "active_app": compact_text(_get(snapshot, "active_app", _get(snapshot, "active_window", "")), 160),
            "active_window_kind": compact_text(_get(snapshot, "active_window_kind", _get(observer_frame, "focus_kind", "")), 80),
            "focus_summary": compact_text(_get(snapshot, "focus_summary", _get(observer_frame, "summary", "")), 320),
            "idle_seconds": _get(snapshot, "idle_seconds", 0.0),
            "recent_events": list(_get(snapshot, "recent_events", ()) or ())[:8],
            "vision_facts": list(_get(snapshot, "vision_facts", ()) or ())[:6],
            "observer": {
                "recommended_action": _get(observer_frame, "recommended_action", ""),
                "salience": _get(observer_frame, "salience", 0.0),
                "novelty": _get(observer_frame, "novelty", 0.0),
                "repetition": _get(observer_frame, "repetition", 0.0),
                "possible_error": _get(observer_frame, "possible_error", 0.0),
                "user_presence": _get(observer_frame, "user_presence", 0.0),
                "focus_kind": _get(observer_frame, "focus_kind", ""),
            },
            "attention": {
                "level": _get(attention_signal, "level", "quiet"),
                "external_pressure": _get(attention_signal, "external_pressure", 0.0),
                "internal_drive": _get(attention_signal, "internal_drive", 0.0),
                "interrupt_cost": _get(attention_signal, "interrupt_cost", 0.0),
                "resource_caution": _get(attention_signal, "resource_caution", 0.0),
                "vision_refresh_recommended": _get(attention_signal, "vision_refresh_recommended", False),
                "budget_reason": _get(attention_signal, "budget_reason", ""),
                "semantic_authority": "none",
            },
            "agenda_top": [to_plain(x) for x in (_call(internal_agenda, "top_items", limit=5, default=()) or ())],
            # ThoughtStream chỉ là substrate: đưa pressure/momentum vào dossier,
            # không cho nó tự giả làm mind hoàn chỉnh. Đúng vai thì hữu ích, sai vai thì thành sân khấu khói.
            "thought_stream": thought_state,
            "private_runtime": private_state,
            "surface_queue": surface_queue_state,
        }
        eid = "dossier.runtime.state"
        evidence_index[eid] = state
        if thought_state:
            evidence_index["dossier.runtime.thought_stream"] = thought_state
        if private_state:
            evidence_index["dossier.runtime.private_state"] = private_state
        if surface_queue_state.get("size", 0):
            evidence_index["dossier.runtime.surface_queue"] = surface_queue_state
        return state

    def _thought_stream_state(self, thought_stream: Any) -> dict[str, Any]:
        if thought_stream is None:
            return {}
        state: dict[str, Any] = {
            "private_mind_pressure_bias": clamp01(_get(thought_stream, "private_mind_pressure_bias", 0.0)),
            "cognitive_momentum": clamp01(_get(thought_stream, "cognitive_momentum", _get(thought_stream, "momentum", 0.0))),
            "unresolved_pressure": clamp01(_get(thought_stream, "unresolved_pressure", _get(thought_stream, "tension", 0.0))),
        }
        candidates = []
        getter = getattr(thought_stream, "get_surface_candidates", None)
        if callable(getter):
            try:
                candidates = list(getter(limit=5) or [])
            except TypeError:
                try:
                    candidates = list(getter() or [])[:5]
                except Exception:
                    candidates = []
            except Exception:
                candidates = []
        if candidates:
            state["surface_candidates"] = [self._compact_surface_like(item) for item in candidates[:5]]
        for attr in ("active_threads", "unresolved_loops", "recent_thoughts", "recent_seeds"):
            value = _get(thought_stream, attr, None)
            if value:
                state[attr] = to_plain(value)
        return {key: value for key, value in state.items() if value not in (None, "", [], {}, ())}

    def _private_runtime_state(self, runtime_owner: Any) -> dict[str, Any]:
        if runtime_owner is None:
            return {}
        # PrivateMind được thấy bounded self-model của Subject Core, không được
        # thấy raw drive vector hay event ledger toàn tri. Nhờ vậy nó có thể nghĩ
        # "về chính mình" trên cùng causal substrate thay vì dựng một Eris thứ hai.
        subject_self: dict[str, Any] = {}
        subject_service = _get(runtime_owner, "subject_service", None)
        self_view = getattr(subject_service, "self_view", None)
        if callable(self_view):
            try:
                candidate = self_view()
                if isinstance(candidate, Mapping):
                    subject_self = to_plain(candidate)
            except Exception:
                subject_self = {}
        return {
            "subject_self": subject_self,
            "speech_bias": to_plain(_get(runtime_owner, "private_mind_speech_bias", {})),
            "inquiry_plan": to_plain(list(_get(runtime_owner, "private_mind_inquiry_plan", []) or [])[-8:]),
            "active_state": to_plain(_get(runtime_owner, "private_mind_active_state", {})),
            "thought_pressure": clamp01(_get(runtime_owner, "private_mind_thought_pressure", 0.0)),
            "belief_core_last": to_plain(_get(runtime_owner, "private_mind_belief_core_last", {})),
        }

    def _surface_queue_state(self, queue: Iterable[Any]) -> dict[str, Any]:
        # Convert một lần thôi. Iterator mà bị đọc hai lần là tự tạo bằng chứng rỗng,
        # rồi lại đổ lỗi cho model. Một truyền thống debug không đáng giữ.
        all_items = list(queue or [])
        return {
            "size": len(all_items),
            "preview": [self._compact_surface_like(item) for item in all_items[:8]],
        }

    def _compact_surface_like(self, item: Any) -> dict[str, Any]:
        if callable(getattr(item, "to_context", None)):
            try:
                data = item.to_context()
            except Exception:
                data = to_plain(item)
        elif callable(getattr(item, "to_dict", None)):
            data = item.to_dict()
        elif isinstance(item, Mapping):
            data = dict(item)
        else:
            data = {"value": to_plain(item)}
        if not isinstance(data, Mapping):
            data = {"value": to_plain(data)}
        return {
            "id": compact_text(data.get("id", ""), 96),
            "kind": compact_text(data.get("kind", ""), 96),
            "source": compact_text(data.get("source", data.get("root_source", "")), 96),
            "topic_seed": compact_text(data.get("topic_seed", data.get("seed", data.get("claim", ""))), 260),
            "confidence": data.get("confidence", 0.0),
            "score": data.get("score", data.get("usefulness", 0.0)),
        }

    def _evidence_metrics(
        self,
        *,
        recent_turns: list[dict[str, Any]],
        runtime_state: Mapping[str, Any],
        evidence_index: Mapping[str, Any],
        memory_hits: Iterable[Mapping[str, Any]] = (),
        repair_failures: Iterable[Mapping[str, Any]] = (),
        concerns: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        observer = runtime_state.get("observer", {}) if isinstance(runtime_state, Mapping) else {}
        novelty = clamp01(observer.get("novelty", 0.0) if isinstance(observer, Mapping) else 0.0)
        repetition = clamp01(observer.get("repetition", 0.0) if isinstance(observer, Mapping) else 0.0)
        possible_error = clamp01(observer.get("possible_error", 0.0) if isinstance(observer, Mapping) else 0.0)
        has_recent_user_turn = bool(recent_turns)
        user_turn_age_sec = self._latest_user_turn_age(recent_turns)
        public_anchor_count = self._public_anchor_count(evidence_index)
        memory_hit_count = len(tuple(memory_hits or ()))
        repair_count = len(tuple(repair_failures or ()))
        concern_count = len(tuple(concerns or ()))
        runtime_anchor_count = 0
        if isinstance(runtime_state, Mapping):
            if runtime_state.get("thought_stream"):
                runtime_anchor_count += 1
            private_runtime = runtime_state.get("private_runtime")
            if isinstance(private_runtime, Mapping) and any(private_runtime.values()):
                runtime_anchor_count += 1
            surface_queue = runtime_state.get("surface_queue")
            if isinstance(surface_queue, Mapping) and surface_queue.get("size"):
                runtime_anchor_count += 1
        dossier_thickness = clamp01(
            0.25 * (1.0 if has_recent_user_turn else 0.0)
            + 0.20 * clamp01(memory_hit_count / 4.0)
            + 0.20 * clamp01(concern_count / 4.0)
            + 0.15 * clamp01(repair_count / 3.0)
            + 0.20 * clamp01((public_anchor_count + runtime_anchor_count) / 5.0)
        )
        evidence_strength = clamp01(
            0.26 * novelty
            + 0.19 * possible_error
            + 0.20 * (1.0 if has_recent_user_turn else 0.0)
            + 0.15 * clamp01(public_anchor_count / 3.0)
            + 0.20 * dossier_thickness
            - 0.30 * repetition
        )
        return {
            "novelty": novelty,
            "repetition": repetition,
            "has_recent_user_turn": has_recent_user_turn,
            "user_turn_age_sec": user_turn_age_sec,
            "public_anchor_count": public_anchor_count,
            "runtime_anchor_count": runtime_anchor_count,
            "memory_hit_count": memory_hit_count,
            "repair_failure_count": repair_count,
            "concern_count": concern_count,
            "dossier_thickness": dossier_thickness,
            "evidence_strength": evidence_strength,
        }

    def _latest_user_turn_age(self, recent_turns: list[dict[str, Any]]) -> float | None:
        timestamps: list[float] = []
        for turn in recent_turns:
            try:
                timestamp = float(turn.get("timestamp") or 0.0)
            except Exception:
                timestamp = 0.0
            if timestamp > 0:
                timestamps.append(timestamp)
        if not timestamps:
            return None
        return max(0.0, time.time() - max(timestamps))

    def _public_anchor_count(self, evidence_index: Mapping[str, Any]) -> int:
        markers = (
            "dossier.user.",
            "vision_fact",
            "screen_fact",
            "scene_evidence",
            "active_window",
            "active_app",
            "recent_events",
            "focus_summary",
        )
        count = 0
        for eid, value in evidence_index.items():
            text = f"{eid} {to_plain(value)}".lower()
            if any(marker in text for marker in markers):
                count += 1
        return min(8, count)

    def _memory_hits(self, *, memory_core: Any, explicit_hits: Iterable[Any], evidence_index: dict[str, Any]) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for idx, hit in enumerate(tuple(explicit_hits or ())[: self.max_memory_hits]):
            eid = str(_get(hit, "id", _get(hit, "record_id", "")) or f"dossier.memory.explicit.{idx}")
            item = {"evidence_id": eid, "text": compact_text(_get(hit, "text", _get(hit, "claim", hit)), 700), "source": "explicit_memory_hit"}
            hits.append(item)
            evidence_index[eid] = item
        if hits or memory_core is None:
            return hits[: self.max_memory_hits]

        snapshot = _call(memory_core, "load", default={})
        if not isinstance(snapshot, Mapping):
            return hits
        for store_name in ("semantic_memory", "repair_memory", "relational_memory", "episodic_memory", "procedural_memory"):
            records = snapshot.get(store_name)
            if not isinstance(records, list):
                continue
            for idx, record in enumerate(records[-3:]):
                if not isinstance(record, Mapping):
                    continue
                text = compact_text(record.get("summary") or record.get("claim") or record.get("text") or record.get("content") or "", 700)
                if not text:
                    continue
                eid = str(record.get("id") or f"dossier.memory.{store_name}.{idx}")
                item = {"evidence_id": eid, "text": text, "source": store_name, "confidence": record.get("confidence", 0.55)}
                hits.append(item)
                evidence_index[eid] = item
                if len(hits) >= self.max_memory_hits:
                    return hits
        return hits

    def _repair_failures(self, *, memory_core: Any, evidence_index: dict[str, Any]) -> list[dict[str, Any]]:
        if memory_core is None:
            return []
        snapshot = _call(memory_core, "load", default={})
        if not isinstance(snapshot, Mapping):
            return []
        records = snapshot.get("repair_memory") or []
        if not isinstance(records, list):
            return []
        failures: list[dict[str, Any]] = []
        for idx, record in enumerate(records[-self.max_repairs :]):
            if not isinstance(record, Mapping):
                continue
            eid = str(record.get("id") or f"dossier.repair.{idx}")
            item = {
                "evidence_id": eid,
                "failure_type": record.get("failure_type") or record.get("pattern") or "unknown",
                "summary": compact_text(record.get("repair_strategy") or record.get("text") or record.get("summary") or record, 700),
                "confidence": record.get("confidence", 0.55),
            }
            failures.append(item)
            evidence_index[eid] = item
        return failures

    def _active_goal_from_agenda(self, agenda: Any) -> str:
        items = _call(agenda, "top_items", limit=3, default=()) or ()
        for item in items:
            kind = str(_get(item, "kind", "") or "")
            if kind in {"technical_followup", "open_question", "repair"}:
                return compact_text(_get(item, "summary", ""), 280)
        return ""

    def _task_from_snapshot(self, snapshot: Any, observer_frame: Any, turns: list[dict[str, Any]]) -> str:
        for turn in reversed(turns):
            text = str(turn.get("text") or "")
            if text:
                return compact_text(text, 320)
        summary = _get(observer_frame, "summary", "") or _get(snapshot, "focus_summary", "")
        return compact_text(summary, 320)

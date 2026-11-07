# -*- coding: utf-8 -*-
"""
cortex/private_mind/state_mutation_bus.py
=========================================

StateMutationBus biến InternalJudgment thành ảnh hưởng thật lên hành vi tương lai:
- agenda_stack;
- repair_memory / commit request;
- belief_core;
- speech_bias;
- inquiry_plan;
- thought_stream_pressure;
- surface_candidate_queue.

Đây là phần chống "nhật ký nội tâm cho đẹp". Nghĩ mà không đổi state thì khác gì
đóng dấu công văn rồi vứt vào hư vô. Rất hành chính, rất buồn.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import InternalJudgment, StateMutation, SurfaceCandidate, clamp01, compact_text, to_plain


@dataclass(slots=True)
class MutationApplyReport:
    mutation_id: str
    target: str
    operation: str
    applied: bool
    reason: str
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutation_id": self.mutation_id,
            "target": self.target,
            "operation": self.operation,
            "applied": self.applied,
            "reason": self.reason,
            "value": to_plain(self.value),
        }


@dataclass(slots=True)
class PrivateRuntimeBiasState:
    """Store nhẹ cho bias nếu runtime chưa có field tương ứng."""

    speech_bias: dict[str, Any] = field(default_factory=dict)
    inquiry_plan: list[dict[str, Any]] = field(default_factory=list)
    thought_stream_pressure: float = 0.0
    belief_core: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "speech_bias": to_plain(self.speech_bias),
            "inquiry_plan": to_plain(self.inquiry_plan[-12:]),
            "thought_stream_pressure": round(self.thought_stream_pressure, 4),
            "belief_core": to_plain(self.belief_core),
            "updated_at": self.updated_at,
        }


class StateMutationBus:
    def __init__(self, *, min_confidence: float = 0.35, logger: Any | None = None) -> None:
        self.min_confidence = clamp01(min_confidence)
        self.logger = logger
        self.bias_state = PrivateRuntimeBiasState()
        self.last_reports: tuple[MutationApplyReport, ...] = ()

    def apply(
        self,
        judgment: InternalJudgment | None,
        *,
        agenda_stack: Any = None,
        memory_core: Any = None,
        state_committer: Any = None,
        thought_stream: Any = None,
        surface_candidate_queue: list[Any] | None = None,
        concern_store: Any = None,
        runtime_owner: Any = None,
    ) -> tuple[dict[str, Any], ...]:
        if judgment is None:
            return ()
        reports: list[MutationApplyReport] = []
        for mutation in judgment.state_mutations:
            if mutation.confidence < self.min_confidence:
                reports.append(self._report(mutation, False, "confidence_below_threshold"))
                continue
            try:
                report = self._apply_one(
                    mutation,
                    agenda_stack=agenda_stack,
                    memory_core=memory_core,
                    state_committer=state_committer,
                    thought_stream=thought_stream,
                    surface_candidate_queue=surface_candidate_queue,
                    concern_store=concern_store,
                    runtime_owner=runtime_owner,
                )
            except Exception as exc:
                report = self._report(mutation, False, f"error:{type(exc).__name__}:{exc}")
            reports.append(report)
        # Không tự enqueue `judgment.surface_candidate` ở đây. SurfaceValve mới là
        # cửa lọc private-chain -> public-safe impulse. Nếu bus tự ném candidate
        # thẳng vào queue, nó bypass một nửa lý do tồn tại của private mind. Rất
        # sáng tạo, theo nghĩa một cái bug cũng sáng tạo.
        self.bias_state.updated_at = time.time()
        self.last_reports = tuple(reports)
        return tuple(report.to_dict() for report in reports)

    def _apply_one(self, mutation: StateMutation, **targets: Any) -> MutationApplyReport:
        target = str(mutation.target or "")
        if target == "agenda_stack":
            return self._apply_agenda(mutation, targets.get("agenda_stack"))
        if target == "surface_candidate_queue":
            queue = targets.get("surface_candidate_queue")
            value = mutation.value
            if isinstance(value, Mapping):
                return self._enqueue_surface_candidate(SurfaceCandidate.from_mapping(value), queue)
            return self._report(mutation, False, "surface_value_not_mapping")
        if target == "speech_bias":
            key = compact_text(mutation.value, 120) if isinstance(mutation.value, str) else compact_text((mutation.value or {}).get("key", "bias") if isinstance(mutation.value, Mapping) else "bias", 120)
            payload = mutation.value if not isinstance(mutation.value, str) else {"preference": mutation.value}
            self.bias_state.speech_bias[key] = {
                "operation": mutation.operation,
                "value": to_plain(payload),
                "confidence": mutation.confidence,
                "reason": mutation.reason,
                "evidence_ids": list(mutation.evidence_ids),
                "updated_at": time.time(),
            }
            owner = targets.get("runtime_owner")
            if owner is not None:
                setattr(owner, "private_mind_speech_bias", dict(self.bias_state.speech_bias))
            return self._report(mutation, True, "speech_bias_updated", self.bias_state.speech_bias[key])
        if target == "inquiry_plan":
            item = {
                "operation": mutation.operation,
                "value": to_plain(mutation.value),
                "confidence": mutation.confidence,
                "reason": mutation.reason,
                "evidence_ids": list(mutation.evidence_ids),
                "created_at": time.time(),
            }
            self.bias_state.inquiry_plan.append(item)
            self.bias_state.inquiry_plan = self.bias_state.inquiry_plan[-24:]
            owner = targets.get("runtime_owner")
            if owner is not None:
                setattr(owner, "private_mind_inquiry_plan", list(self.bias_state.inquiry_plan))
            return self._report(mutation, True, "inquiry_plan_appended", item)
        if target == "thought_stream_pressure":
            return self._apply_thought_pressure(mutation, targets.get("thought_stream"), targets.get("runtime_owner"))
        if target == "belief_core":
            return self._apply_belief_core(
                mutation,
                memory_core=targets.get("memory_core"),
                runtime_owner=targets.get("runtime_owner"),
            )
        if target == "repair_memory":
            return self._apply_repair_memory(mutation, memory_core=targets.get("memory_core"), state_committer=targets.get("state_committer"))
        if target == "private_concern":
            store = targets.get("concern_store")
            if store is not None and callable(getattr(store, "update_from_judgment", None)):
                concern = store.update_from_judgment(mutation.value if isinstance(mutation.value, Mapping) else {"theme": str(mutation.value), "evidence_ids": mutation.evidence_ids})
                return self._report(mutation, bool(concern), "private_concern_updated", concern.to_dict() if concern else None)
            return self._report(mutation, False, "missing_concern_store")
        if target == "active_state":
            owner = targets.get("runtime_owner")
            if owner is not None:
                current = getattr(owner, "private_mind_active_state", {}) or {}
                if isinstance(current, dict) and isinstance(mutation.value, Mapping):
                    current.update(to_plain(mutation.value))
                else:
                    current = {"value": to_plain(mutation.value)}
                current["updated_at"] = time.time()
                setattr(owner, "private_mind_active_state", current)
                return self._report(mutation, True, "runtime_active_state_updated", current)
            return self._report(mutation, False, "missing_runtime_owner")
        return self._report(mutation, False, f"unsupported_target:{target}")

    def _apply_agenda(self, mutation: StateMutation, agenda_stack: Any) -> MutationApplyReport:
        if agenda_stack is None or not callable(getattr(agenda_stack, "add", None)):
            return self._report(mutation, False, "missing_agenda_stack")
        value = mutation.value if isinstance(mutation.value, Mapping) else {"summary": str(mutation.value)}
        item = agenda_stack.add(
            kind=str(value.get("kind") or "technical_followup"),
            summary=compact_text(value.get("summary") or value.get("text") or mutation.reason or mutation.value, 520),
            evidence_ids=tuple(mutation.evidence_ids),
            pressure=clamp01(value.get("pressure", mutation.priority or 0.55)),
            urgency=clamp01(value.get("urgency", mutation.priority * 0.55)),
            confidence=mutation.confidence,
            speech_value=clamp01(value.get("speech_value", 0.0)),
            source="private_mind",
            ttl_seconds=float(value.get("ttl_seconds") or mutation.ttl_seconds or 3600.0),
            payload={"mutation_id": mutation.id, "operation": mutation.operation, "reason": mutation.reason, "value": to_plain(value)},
        )
        return self._report(mutation, True, "agenda_item_added", getattr(item, "to_dict", lambda: item)())

    def _apply_thought_pressure(self, mutation: StateMutation, thought_stream: Any, runtime_owner: Any) -> MutationApplyReport:
        amount = 0.08
        if isinstance(mutation.value, Mapping):
            amount = float(mutation.value.get("amount", amount) or amount)
        elif isinstance(mutation.value, (int, float)):
            amount = float(mutation.value)
        if mutation.operation == "decrease":
            amount = -abs(amount)
        elif mutation.operation == "increase":
            amount = abs(amount)
        self.bias_state.thought_stream_pressure = clamp01(self.bias_state.thought_stream_pressure + amount)
        applied_to_stream = False
        if thought_stream is not None:
            # Không assume ThoughtStream API. Gắn shadow field vẫn đủ để future code đọc.
            try:
                current = float(getattr(thought_stream, "private_mind_pressure_bias", 0.0) or 0.0)
                setattr(thought_stream, "private_mind_pressure_bias", clamp01(current + amount))
                applied_to_stream = True
            except Exception:
                applied_to_stream = False
        if runtime_owner is not None:
            setattr(runtime_owner, "private_mind_thought_pressure", self.bias_state.thought_stream_pressure)
        return self._report(mutation, True, "thought_stream_pressure_updated" + ("_on_stream" if applied_to_stream else "_shadow"), self.bias_state.thought_stream_pressure)

    def _apply_belief_core(self, mutation: StateMutation, *, memory_core: Any, runtime_owner: Any = None) -> MutationApplyReport:
        kernel = getattr(runtime_owner, "epistemic_kernel", None) if runtime_owner is not None else None
        if kernel is not None and getattr(kernel, "authoritative", False):
            payload = kernel.ingest_belief_mutation(mutation)
            if runtime_owner is not None:
                setattr(runtime_owner, "private_mind_belief_core_last", payload)
            return self._report(mutation, True, "belief_core_routed_to_epistemic_v2_hypothesis", payload)

        value = mutation.value if isinstance(mutation.value, Mapping) else {"claim": str(mutation.value)}
        claim = compact_text(
            value.get("claim")
            or value.get("text")
            or value.get("summary")
            or value.get("belief")
            or mutation.reason
            or "",
            520,
        )
        if not claim:
            return self._report(mutation, False, "belief_core_missing_claim")
        key_seed = value.get("key") or value.get("id") or claim
        key = compact_text(str(key_seed or claim).strip().lower().replace(" ", "_"), 120) or f"private_mind_{mutation.id}"
        domain = compact_text(value.get("domain") or "private_mind", 80)
        raw_tags = value.get("tags") or ()
        if isinstance(raw_tags, str):
            raw_tags = (raw_tags,)
        tags = tuple(dict.fromkeys((
            "private_mind",
            "tentative",
            "belief_core",
            *(compact_text(item, 64) for item in raw_tags if compact_text(item, 64)),
        )))
        payload = {
            "key": key,
            "claim": claim,
            "domain": domain,
            "confidence": mutation.confidence,
            "evidence_ids": list(mutation.evidence_ids),
            "reason": mutation.reason,
            "tags": list(tags),
            "operation": mutation.operation,
            "updated_at": time.time(),
        }

        agent_mind = getattr(memory_core, "agent_mind", None) if memory_core is not None else None
        if agent_mind is not None and isinstance(getattr(agent_mind, "beliefs", None), dict):
            try:
                from cortex.agent_mind_persistence import PersistentBeliefRecord

                beliefs = getattr(agent_mind, "beliefs")
                existing = beliefs.get(key)
                if existing is not None:
                    reinforce = getattr(existing, "reinforce", None)
                    if callable(reinforce):
                        reinforce(min(0.10, max(0.03, mutation.confidence * 0.10)), source_id=mutation.id, positive=True)
                    try:
                        existing.claim = claim or existing.claim
                        existing.domain = domain or existing.domain
                        existing.tags = tuple(dict.fromkeys((*tuple(getattr(existing, "tags", ()) or ()), *tags)))[:12]
                    except Exception:
                        pass
                    reason = "belief_core_tentative_reinforced"
                else:
                    beliefs[key] = PersistentBeliefRecord(
                        key=key,
                        claim=claim,
                        domain=domain,
                        confidence=clamp01(max(0.34, min(0.64, mutation.confidence))),
                        source_ids=tuple(dict.fromkeys((mutation.id, *tuple(mutation.evidence_ids or ())))),
                        tags=tags,
                        positive_evidence_count=1,
                        decay_rate=0.035,
                    )
                    reason = "belief_core_tentative_committed"
                save = getattr(agent_mind, "save", None)
                if callable(save):
                    save()
                if runtime_owner is not None:
                    setattr(runtime_owner, "private_mind_belief_core_last", payload)
                return self._report(mutation, True, reason, payload)
            except Exception as exc:
                if self.logger is not None and callable(getattr(self.logger, "warning", None)):
                    try:
                        self.logger.warning("PrivateMind belief_core commit failed: %s", exc)
                    except Exception:
                        pass

        self.bias_state.belief_core[key] = payload
        if runtime_owner is not None:
            setattr(runtime_owner, "private_mind_belief_core_last", payload)
        return self._report(mutation, True, "belief_core_shadow_updated_missing_agent_mind", payload)

    def _apply_repair_memory(self, mutation: StateMutation, *, memory_core: Any, state_committer: Any) -> MutationApplyReport:
        content = mutation.value if isinstance(mutation.value, Mapping) else {"text": str(mutation.value)}
        request = {
            "target": "repair_memory",
            "content": {
                "failure_type": content.get("failure_type", "private_mind_adjustment"),
                "repair_strategy": content.get("repair_strategy", content.get("text", mutation.reason)),
                "future_suppression_rule": content.get("future_suppression_rule", "Do not surface raw private chain; mutate future behavior instead."),
                "source": "private_mind",
            },
            "confidence": mutation.confidence,
            "evidence_ids": list(mutation.evidence_ids),
            "permission_required": False,
            "storage_permission": True,
            "decay_policy": "slow",
            "reason": mutation.reason or "private mind mutation repair memory",
            "source_module": "StateMutationBus",
            "tags": ["private_mind", "repair"],
        }
        # Ưu tiên state_committer nếu runtime đưa vào. Nếu không, thử memory_core.commit
        # mềm. Không có cả hai thì trả report nhưng không crash.
        if state_committer is not None and callable(getattr(state_committer, "commit_requests", None)):
            state_committer.commit_requests([request])
            return self._report(mutation, True, "repair_request_committed_via_state_committer", request)
        commit = getattr(memory_core, "commit", None)
        if callable(commit):
            try:
                commit(request)
                return self._report(mutation, True, "repair_request_committed_via_memory_core", request)
            except TypeError:
                pass
        return self._report(mutation, False, "no_repair_commit_adapter", request)

    def _enqueue_surface_candidate(self, candidate: SurfaceCandidate, queue: Any) -> MutationApplyReport:
        fake_mutation = StateMutation(
            target="surface_candidate_queue",
            operation="append",
            value=candidate.to_dict(),
            confidence=candidate.confidence,
            evidence_ids=candidate.evidence_ids,
            reason=candidate.reason,
            priority=candidate.usefulness,
        )
        if queue is None or not hasattr(queue, "append"):
            return self._report(fake_mutation, False, "missing_surface_queue")
        if not candidate.allowed:
            return self._report(fake_mutation, False, "candidate_not_allowed", candidate.to_dict())
        # Dedupe theo id để private mind không ném cùng một seed vào SurfaceArbiter mãi.
        existing_ids = {str(getattr(x, "id", "") or (x.get("id") if isinstance(x, Mapping) else "")) for x in list(queue)}
        if candidate.id in existing_ids:
            return self._report(fake_mutation, False, "candidate_duplicate", candidate.to_dict())
        queue.append(candidate)
        while len(queue) > 12:
            try:
                queue.pop(0)
            except Exception:
                break
        return self._report(fake_mutation, True, "surface_candidate_enqueued", candidate.to_dict())

    def _report(self, mutation: StateMutation, applied: bool, reason: str, value: Any = None) -> MutationApplyReport:
        return MutationApplyReport(
            mutation_id=mutation.id,
            target=str(mutation.target),
            operation=str(mutation.operation),
            applied=bool(applied),
            reason=reason,
            value=value,
        )

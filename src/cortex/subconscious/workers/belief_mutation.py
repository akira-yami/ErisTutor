# -*- coding: utf-8 -*-
"""Phase 6 - Core Belief Mutation.

Phase 6 không được sửa thẳng ``agent_mind.json``. Worker này chỉ gom tín hiệu dài
hạn thành proposal, rồi đưa qua MutationCommitter. Committer mặc định là in-memory
và cực kỳ bảo thủ: confidence đủ cao, cửa sổ quan sát đủ dài, evidence đủ nhiều,
không contradiction nặng, delta mỗi lần bị clamp <= 0.08, và luôn có rollback
record.

Nói ngắn: ERIS được phép đổi nhẹ qua thời gian, không được tái sinh nhân cách sau
một buổi lỗi build. Máy cũng cần kỷ luật, không thì lại giống con người quá mức.
"""

from __future__ import annotations
from core.di import resolve_dependency

from dataclasses import dataclass, field, replace
import hashlib
import time
from typing import Any, Iterable, Literal, Mapping

from ..blackboard import BlackboardSnapshot
from ..thought_fragment import ThoughtFragment, clamp01, clean_text, normalize_string_list
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import (
    collect_texts,
    context_evidence_ids,
    first_present,
    memory_texts_from_context,
    relation_level,
    safe_get,
)

MutationDomain = Literal["belief", "preference", "habit"]
MutationSeverity = Literal["low", "medium", "high"]
MutationStatus = Literal["pending", "committed", "rejected"]


@dataclass(slots=True, frozen=True)
class BeliefMutationProposal:
    """Proposal đổi belief lõi, chưa phải commit.

    target: khóa belief/preference/habit cần chỉnh.
    delta: mức thay đổi đề xuất, luôn bị clamp về [-0.08, 0.08].
    reason: lý do đọc được trong debug, không phải public speech.
    evidence_ids: tối thiểu 5 id khi muốn commit thật.
    confidence: độ tin tổng hợp.
    observation_window_days: cửa sổ quan sát. Commit cần >= 7 ngày.
    reversibility: khả năng rollback/đổi lại. Thấp quá thì không commit.
    severity: low/medium/high. High mặc định không auto commit.
    """

    target: str
    delta: float
    reason: str
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    observation_window_days: int = 0
    reversibility: float = 1.0
    severity: MutationSeverity = "low"
    domain: MutationDomain = "belief"
    created_at: float = 0.0

    def __post_init__(self) -> None:
        target = clean_text(self.target, limit=120)
        reason = clean_text(self.reason, limit=500)
        if not target:
            raise ValueError("BeliefMutationProposal.target không được rỗng.")
        if not reason:
            raise ValueError("BeliefMutationProposal.reason không được rỗng.")
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "evidence_ids", normalize_string_list(self.evidence_ids, limit=32, item_limit=140))
        object.__setattr__(self, "confidence", clamp01(self.confidence))
        object.__setattr__(self, "reversibility", clamp01(self.reversibility, default=1.0))
        object.__setattr__(self, "delta", clamp_delta(self.delta))
        object.__setattr__(self, "observation_window_days", max(0, int(self.observation_window_days or 0)))
        severity = str(self.severity or "low").lower()
        if severity not in {"low", "medium", "high"}:
            severity = "low"
        object.__setattr__(self, "severity", severity)  # type: ignore[arg-type]
        domain = str(self.domain or "belief").lower()
        if domain not in {"belief", "preference", "habit"}:
            domain = "belief"
        object.__setattr__(self, "domain", domain)  # type: ignore[arg-type]
        created = float(self.created_at or time.time())
        object.__setattr__(self, "created_at", created)

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_ids)

    @property
    def id(self) -> str:
        raw = f"{self.domain}|{self.target}|{self.delta:.4f}|{'|'.join(self.evidence_ids[:8])}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"bmp.{self.domain}.{digest}"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "domain": self.domain,
            "target": self.target,
            "delta": round(float(self.delta), 4),
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids),
            "confidence": round(float(self.confidence), 3),
            "observation_window_days": int(self.observation_window_days),
            "reversibility": round(float(self.reversibility), 3),
            "severity": self.severity,
            "created_at": round(float(self.created_at), 3),
        }

    def to_fragment(
        self,
        *,
        source_agent: str = "belief_mutation_engine",
        status: MutationStatus = "pending",
        extra_tags: tuple[str, ...] = (),
    ) -> ThoughtFragment:
        """Biến proposal thành fragment private để Blackboard/audit nhìn được."""

        kind = {
            "belief": "belief_mutation_proposal",
            "preference": "preference_delta_proposal",
            "habit": "habit_delta_proposal",
        }.get(self.domain, "belief_mutation_proposal")
        text = (
            f"{kind} {status}: target={self.target}; delta={self.delta:+.3f}; "
            f"window={self.observation_window_days}d; evidence={self.evidence_count}; "
            f"confidence={self.confidence:.2f}; reason={self.reason}"
        )
        return ThoughtFragment.create(
            source_agent=source_agent,
            kind=kind,
            content=text,
            evidence_ids=self.evidence_ids,
            confidence=self.confidence,
            urgency=clamp01(0.18 + abs(self.delta) * 5.0 + self.confidence * 0.16),
            affect_charge=clamp01(0.12 + abs(self.delta) * 4.0),
            privacy_level="private",
            surface_allowed=False,
            ttl_seconds=900,
            tags=("belief_mutation", self.domain, status, *extra_tags),
            now=self.created_at,
        )


@dataclass(slots=True, frozen=True)
class PreferenceDeltaProposal(BeliefMutationProposal):
    """Proposal chỉnh preference, kế thừa contract belief mutation."""

    domain: MutationDomain = "preference"


@dataclass(slots=True, frozen=True)
class HabitDeltaProposal(BeliefMutationProposal):
    """Proposal chỉnh semantic habit/procedural habit."""

    domain: MutationDomain = "habit"


@dataclass(slots=True, frozen=True)
class MutationCommitRecord:
    """Record commit có rollback token.

    previous_value/new_value chỉ là số pressure nếu target store là dict thường.
    Integration thật với agent_mind có thể map target này sang schema sâu hơn.
    """

    proposal_id: str
    domain: MutationDomain
    target: str
    delta: float
    previous_value: float
    new_value: float
    evidence_ids: tuple[str, ...]
    reason: str
    rollback_token: str
    committed_at: float

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "domain": self.domain,
            "target": self.target,
            "delta": round(float(self.delta), 4),
            "previous_value": round(float(self.previous_value), 4),
            "new_value": round(float(self.new_value), 4),
            "evidence_ids": list(self.evidence_ids),
            "reason": self.reason,
            "rollback_token": self.rollback_token,
            "committed_at": round(float(self.committed_at), 3),
        }


@dataclass(slots=True, frozen=True)
class MutationDecision:
    """Kết quả commit/reject một proposal."""

    proposal: BeliefMutationProposal
    status: MutationStatus
    reason: str
    record: MutationCommitRecord | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal": self.proposal.to_dict(),
            "status": self.status,
            "reason": self.reason,
            "record": self.record.to_dict() if self.record else None,
        }


def clamp_delta(value: object, *, limit: float = 0.08) -> float:
    """Clamp delta mutation mỗi lần về [-0.08, 0.08]."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if number > limit:
        return limit
    if number < -limit:
        return -limit
    return number


class MutationCommitter:
    """Committer bảo thủ cho Phase 6.

    Store mặc định là dict lồng theo domain:
        {"belief": {target: 0.5}, "preference": {...}, "habit": {...}}

    Nó không biết schema agent_mind thật, cố ý. Integration muốn ghi file thật phải
    truyền adapter rõ ràng sau này. Không có adapter mà sửa thẳng JSON là kiểu
    phẫu thuật bằng muỗng.
    """

    def __init__(
        self,
        *,
        store: dict[str, dict[str, float]] | None = None,
        min_confidence: float = 0.75,
        min_window_days: int = 7,
        min_evidence_count: int = 5,
        max_delta: float = 0.08,
        min_reversibility: float = 0.40,
        allow_high_severity: bool = False,
    ) -> None:
        self.store: dict[str, dict[str, float]] = store if store is not None else {
            "belief": {},
            "preference": {},
            "habit": {},
        }
        self.min_confidence = clamp01(min_confidence)
        self.min_window_days = max(0, int(min_window_days))
        self.min_evidence_count = max(1, int(min_evidence_count))
        self.max_delta = max(0.0, min(0.20, float(max_delta)))
        self.min_reversibility = clamp01(min_reversibility)
        self.allow_high_severity = bool(allow_high_severity)
        self.commit_records: list[MutationCommitRecord] = []
        self.rollback_records: dict[str, MutationCommitRecord] = {}

    def evaluate(
        self,
        proposal: BeliefMutationProposal,
        *,
        contradiction_severity: float = 0.0,
        now: float | None = None,
    ) -> MutationDecision:
        """Reject hoặc commit proposal theo rule Phase 6."""

        reasons = self._blocking_reasons(proposal, contradiction_severity=contradiction_severity)
        if reasons:
            return MutationDecision(proposal=proposal, status="rejected", reason="; ".join(reasons))
        record = self.commit(proposal, now=now)
        return MutationDecision(proposal=proposal, status="committed", reason="commit_rules_satisfied", record=record)

    def commit(self, proposal: BeliefMutationProposal, *, now: float | None = None) -> MutationCommitRecord:
        """Commit sau khi đã qua evaluate. Vẫn clamp delta lần nữa cho chắc."""

        current = float(time.time() if now is None else now)
        domain_store = self.store.setdefault(proposal.domain, {})
        previous = clamp01(domain_store.get(proposal.target, 0.50), default=0.50)
        delta = max(-self.max_delta, min(self.max_delta, proposal.delta))
        new_value = clamp01(previous + delta, default=previous)
        token_raw = f"{proposal.id}|{previous:.4f}|{new_value:.4f}|{current:.3f}"
        rollback_token = "rb." + hashlib.sha1(token_raw.encode("utf-8")).hexdigest()[:14]
        record = MutationCommitRecord(
            proposal_id=proposal.id,
            domain=proposal.domain,
            target=proposal.target,
            delta=delta,
            previous_value=previous,
            new_value=new_value,
            evidence_ids=proposal.evidence_ids,
            reason=proposal.reason,
            rollback_token=rollback_token,
            committed_at=current,
        )
        domain_store[proposal.target] = new_value
        self.commit_records.append(record)
        self.rollback_records[rollback_token] = record
        return record

    def rollback(self, rollback_token: str) -> bool:
        """Rollback một commit đã ghi trong store in-memory."""

        record = self.rollback_records.get(str(rollback_token))
        if record is None:
            return False
        self.store.setdefault(record.domain, {})[record.target] = record.previous_value
        return True

    def _blocking_reasons(self, proposal: BeliefMutationProposal, *, contradiction_severity: float) -> tuple[str, ...]:
        reasons: list[str] = []
        if proposal.confidence < self.min_confidence:
            reasons.append(f"confidence<{self.min_confidence:.2f}")
        if proposal.observation_window_days < self.min_window_days:
            reasons.append(f"window<{self.min_window_days}d")
        if proposal.evidence_count < self.min_evidence_count:
            reasons.append(f"evidence_count<{self.min_evidence_count}")
        if clamp01(contradiction_severity) >= 0.55:
            reasons.append("contradiction_heavy")
        if proposal.reversibility < self.min_reversibility:
            reasons.append(f"reversibility<{self.min_reversibility:.2f}")
        if proposal.severity == "high" and not self.allow_high_severity:
            reasons.append("high_severity_requires_manual_commit")
        if abs(proposal.delta) > self.max_delta + 1e-9:
            reasons.append("delta_exceeds_commit_limit")
        return tuple(reasons)


class BeliefMutationEngine(RuleBasedWorker):
    """Sinh mutation proposals từ residue dài hạn + repair/outcome signals."""

    name = "belief_mutation_engine"
    run_after_synthesis = True

    def __init__(
        self,
        *,
        committer: MutationCommitter | None = None,
        enable_commit: bool = False,
    ) -> None:
        self.committer = resolve_dependency(committer, lambda: MutationCommitter())
        self.enable_commit = bool(enable_commit)
        self.last_proposals: tuple[BeliefMutationProposal, ...] = ()
        self.last_decisions: tuple[MutationDecision, ...] = ()

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        snapshot = self._snapshot(context)
        now = float(context.now or time.time())
        proposals = self._build_proposals(context, snapshot=snapshot, now=now)
        self.last_proposals = proposals
        if not proposals:
            self.last_decisions = ()
            return ()

        contradiction = self._contradiction_severity(context, snapshot)
        fragments: list[ThoughtFragment] = []
        decisions: list[MutationDecision] = []
        for proposal in proposals:
            if self.enable_commit:
                decision = self.committer.evaluate(proposal, contradiction_severity=contradiction, now=now)
            else:
                decision = MutationDecision(
                    proposal=proposal,
                    status="pending",
                    reason="proposal_only_commit_disabled",
                    record=None,
                )
            decisions.append(decision)
            fragments.append(proposal.to_fragment(source_agent=self.name, status=decision.status))
            if decision.status == "committed" and decision.record is not None:
                fragments.append(self._commit_fragment(decision.record, now=now))
            elif decision.status == "rejected":
                fragments.append(self._rejection_fragment(proposal, decision.reason, now=now))
        self.last_decisions = tuple(decisions)
        return tuple(fragments)

    def _build_proposals(
        self,
        context: WorkerContext,
        *,
        snapshot: BlackboardSnapshot | None,
        now: float,
    ) -> tuple[BeliefMutationProposal, ...]:
        evidence_ids = self._mutation_evidence(context, snapshot)
        window_days = self._observation_window_days(context, snapshot=snapshot, evidence_count=len(evidence_ids))
        contradiction = self._contradiction_severity(context, snapshot)
        repair_need = relation_level(context, "repair_need", default=0.0)
        rupture = relation_level(context, "rupture", default=0.0)
        trust = relation_level(context, "trust", default=0.50)
        social_load = relation_level(context, "social_load", default=0.0)
        restraint = max(
            relation_level(context, "restraint", default=0.0),
            relation_level(context, "silence_recovery_need", default=0.0),
        )

        suppressed_count = self._count_fragment_signals(snapshot, {"suppressed_impulse", "rehearsal_result"}, tag="suppressed_impulse")
        repair_count = self._count_fragment_signals(snapshot, {"repair_aftershock", "repair_needed"}, tag="repair_aftershock")
        curiosity_count = self._count_fragment_signals(snapshot, {"curiosity_seed", "knowledge_gap", "ask_later_candidate"}, tag="curiosity")
        over_attention_count = self._count_fragment_signals(snapshot, {"over_attention_warning", "restraint_drive"}, tag="restraint")
        outcome_pressure = self._outcome_pressure(context)

        proposals: list[BeliefMutationProposal] = []
        # 1. Belief: speech should be earned, not automatic, khi nhiều suppressed/repair.
        if suppressed_count >= 2 or repair_need >= 0.55 or rupture >= 0.45:
            confidence = clamp01(
                0.42
                + min(0.18, suppressed_count * 0.04)
                + repair_need * 0.18
                + rupture * 0.12
                + min(0.12, len(evidence_ids) * 0.015)
                + min(0.08, window_days / 60.0)
                - contradiction * 0.20
            )
            proposals.append(
                BeliefMutationProposal(
                    target="belief.speech_requires_grounded_timing",
                    delta=0.035 + min(0.045, repair_need * 0.04 + suppressed_count * 0.006),
                    reason="Lặp suppressed/repair signals cho thấy proactive speech cần grounding và timing chặt hơn.",
                    evidence_ids=evidence_ids,
                    confidence=confidence,
                    observation_window_days=window_days,
                    reversibility=0.82,
                    severity="low" if rupture < 0.70 else "medium",
                    domain="belief",
                    created_at=now,
                )
            )

        # 2. Preference: hỏi muộn/đợi evidence nếu curiosity cao nhưng risk cũng có.
        if curiosity_count >= 1 and (repair_need >= 0.30 or social_load >= 0.45 or restraint >= 0.45):
            confidence = clamp01(
                0.40
                + min(0.16, curiosity_count * 0.05)
                + social_load * 0.14
                + restraint * 0.12
                + min(0.13, len(evidence_ids) * 0.015)
                + min(0.07, window_days / 70.0)
                - contradiction * 0.18
            )
            proposals.append(
                PreferenceDeltaProposal(
                    target="preference.defer_intrusive_curiosity",
                    delta=0.030 + min(0.050, curiosity_count * 0.008 + social_load * 0.025),
                    reason="Curiosity có giá trị nhưng cần chờ evidence/low-interruption window trước khi hỏi user.",
                    evidence_ids=evidence_ids,
                    confidence=confidence,
                    observation_window_days=window_days,
                    reversibility=0.86,
                    severity="low",
                    created_at=now,
                )
            )

        # 3. Habit: giảm micro-surface khi over-attention/restraint lặp lại.
        if over_attention_count >= 1 or social_load >= 0.60:
            confidence = clamp01(
                0.38
                + min(0.16, over_attention_count * 0.055)
                + social_load * 0.16
                + (1.0 - trust) * 0.08
                + min(0.14, len(evidence_ids) * 0.015)
                + min(0.08, window_days / 60.0)
                - contradiction * 0.16
            )
            proposals.append(
                HabitDeltaProposal(
                    target="habit.lower_presence_ping_frequency",
                    delta=0.025 + min(0.050, social_load * 0.030 + over_attention_count * 0.007),
                    reason="Over-attention/restraint signals lặp lại; presence ping nên thưa hơn và ưu tiên body/observe.",
                    evidence_ids=evidence_ids,
                    confidence=confidence,
                    observation_window_days=window_days,
                    reversibility=0.90,
                    severity="low",
                    created_at=now,
                )
            )

        # 4. Repair outcome: nếu repair đang tốt thì giảm rupture bias, delta âm nhẹ.
        if outcome_pressure >= 0.55 and rupture >= 0.20:
            confidence = clamp01(
                0.44
                + outcome_pressure * 0.18
                + trust * 0.12
                + min(0.12, len(evidence_ids) * 0.015)
                + min(0.08, window_days / 60.0)
                - contradiction * 0.18
            )
            proposals.append(
                BeliefMutationProposal(
                    target="belief.shadow_should_wait_for_repair_outcome",
                    delta=-0.025 - min(0.035, outcome_pressure * 0.03),
                    reason="Repair/outcome tốt cho thấy shadow pressure nên hạ bằng evidence thay vì giữ phòng thủ lâu.",
                    evidence_ids=evidence_ids,
                    confidence=confidence,
                    observation_window_days=window_days,
                    reversibility=0.88,
                    severity="low",
                    domain="belief",
                    created_at=now,
                )
            )

        # Không tạo proposal từ 1-2 evidence quá mỏng, trừ khi context đã truyền window/evidence rõ.
        return tuple(proposal for proposal in proposals if proposal.evidence_count >= 2)

    def _mutation_evidence(self, context: WorkerContext, snapshot: BlackboardSnapshot | None) -> tuple[str, ...]:
        ids: list[str] = []
        if snapshot is not None:
            for fragment in snapshot.fragments:
                if fragment.kind in {
                    "suppressed_impulse",
                    "rehearsal_result",
                    "repair_aftershock",
                    "repair_needed",
                    "curiosity_seed",
                    "knowledge_gap",
                    "ask_later_candidate",
                    "over_attention_warning",
                    "restraint_drive",
                    "shadow_pressure",
                    "rupture_risk",
                } or any(tag in {"suppressed_impulse", "repair_aftershock", "curiosity", "restraint", "shadow"} for tag in fragment.tags):
                    ids.extend(fragment.evidence_ids or (fragment.id,))
                if len(ids) >= 32:
                    break
        # Memory/repair/outcome có thể là nguồn dài hạn thật hơn blackboard vừa sinh.
        for source in (
            safe_get(context, "memory_packet.repair"),
            safe_get(context, "memory_packet.repair_memory"),
            safe_get(context, "frame.repair_memory"),
            safe_get(context, "frame.outcome_history"),
            safe_get(context, "frame.relation_history"),
        ):
            for idx, text in enumerate(collect_texts(source, limit=8)):
                ids.append(f"phase6.memory.{abs(hash(text)) % 100000}.{idx}")
        ids.extend(context_evidence_ids(context, fallback_prefix="mutation"))
        return normalize_string_list(ids, limit=32, item_limit=140)

    def _observation_window_days(self, context: WorkerContext, *, snapshot: BlackboardSnapshot | None, evidence_count: int) -> int:
        explicit = first_present(
            safe_get(context, "frame.observation_window_days"),
            safe_get(context, "frame.mutation_window_days"),
            safe_get(context, "memory_packet.observation_window_days"),
            safe_get(context, "relation.observation_window_days"),
            default=None,
        )
        try:
            if explicit is not None:
                return max(0, int(float(explicit)))
        except (TypeError, ValueError):
            pass

        timestamps: list[float] = []
        if snapshot is not None:
            for fragment in snapshot.fragments:
                if fragment.created_at:
                    timestamps.append(float(fragment.created_at))
        if len(timestamps) >= 2:
            span_seconds = max(timestamps) - min(timestamps)
            if span_seconds > 0:
                return max(0, int(span_seconds // 86400))
        # Fallback chỉ là estimate bảo thủ. Nhiều evidence không tự biến thành nhiều ngày.
        return min(6, max(0, evidence_count // 4))

    def _contradiction_severity(self, context: WorkerContext, snapshot: BlackboardSnapshot | None) -> float:
        explicit = first_present(
            safe_get(context, "frame.contradiction_severity"),
            safe_get(context, "memory_packet.contradiction_severity"),
            safe_get(context, "audit_report.contradiction_severity"),
            default=None,
        )
        if explicit is not None:
            return clamp01(explicit)
        if snapshot is None:
            return 0.0
        contradiction_fragments = [
            fragment for fragment in snapshot.fragments
            if fragment.kind in {"contradiction", "contradiction_warning"} or "contradiction" in fragment.tags
        ]
        if not contradiction_fragments:
            return 0.0
        return clamp01(max(fragment.urgency + fragment.affect_charge * 0.35 for fragment in contradiction_fragments))

    def _count_fragment_signals(self, snapshot: BlackboardSnapshot | None, kinds: set[str], *, tag: str) -> int:
        if snapshot is None:
            return 0
        count = 0
        for fragment in snapshot.fragments:
            if fragment.kind in kinds or tag in fragment.tags:
                count += max(1, len(fragment.evidence_ids))
        return count

    def _outcome_pressure(self, context: WorkerContext) -> float:
        outcome = first_present(
            safe_get(context, "frame.outcome"),
            safe_get(context, "frame.outcome_report"),
            safe_get(context, "memory_packet.outcome_history"),
            default=None,
        )
        if outcome is None:
            return 0.0
        if isinstance(outcome, dict):
            accepted = 1.0 if outcome.get("user_accepted") or outcome.get("relation_improved") else 0.0
            corrected = 1.0 if outcome.get("user_corrected") or outcome.get("relation_ruptured") else 0.0
            return clamp01(accepted * 0.65 + max(0.0, 1.0 - corrected) * 0.15)
        texts = " ".join(collect_texts(outcome, limit=8)).lower()
        positive_terms = ("accepted", "improved", "repair", "ổn", "đúng", "ok", "pass")
        negative_terms = ("corrected", "ruptured", "sai", "lệch", "fail")
        pos = sum(1 for term in positive_terms if term in texts)
        neg = sum(1 for term in negative_terms if term in texts)
        return clamp01(pos * 0.18 - neg * 0.16)

    def _snapshot(self, context: WorkerContext) -> BlackboardSnapshot | None:
        snapshot = safe_get(context, "blackboard_snapshot")
        if isinstance(snapshot, BlackboardSnapshot):
            return snapshot
        if snapshot is not None and hasattr(snapshot, "fragments"):
            return snapshot  # type: ignore[return-value]
        return None

    def _commit_fragment(self, record: MutationCommitRecord, *, now: float) -> ThoughtFragment:
        return ThoughtFragment.create(
            source_agent=self.name,
            kind="mutation_commit_record",
            content=(
                f"Mutation committed: {record.domain}.{record.target} {record.delta:+.3f}; "
                f"rollback={record.rollback_token}; previous={record.previous_value:.2f}; new={record.new_value:.2f}."
            ),
            evidence_ids=record.evidence_ids,
            confidence=0.82,
            urgency=0.42,
            affect_charge=0.20,
            privacy_level="private",
            surface_allowed=False,
            ttl_seconds=1200,
            tags=("belief_mutation", "committed", record.domain, "rollback_ready"),
            now=now,
        )

    def _rejection_fragment(self, proposal: BeliefMutationProposal, reason: str, *, now: float) -> ThoughtFragment:
        return ThoughtFragment.create(
            source_agent=self.name,
            kind="mutation_rejected",
            content=f"Mutation rejected: target={proposal.target}; reason={reason}.",
            evidence_ids=proposal.evidence_ids,
            confidence=0.66,
            urgency=0.24,
            affect_charge=0.12,
            privacy_level="private",
            surface_allowed=False,
            ttl_seconds=600,
            tags=("belief_mutation", "rejected", proposal.domain),
            now=now,
        )

# -*- coding: utf-8 -*-
"""Ranh giới điều khiển giữa Subject cognition và hành động runtime.

Subject Core + ``NeuroActionSelector`` sở hữu việc chọn hành động. Model từ xa chỉ
được mời như một chuyên gia cân nhắc one-shot khi các lựa chọn thực sự mơ hồ; model
không sở hữu ``coalition_id``, ``proposal_ref`` hay identity tạm của runtime.

Module này còn giữ capability planner vì tên capability là API identity ổn định và
có thể đi qua model boundary. Runtime vẫn sở hữu availability, permission, validate
argument, concurrency và execution evidence; Surface chỉ sở hữu cách diễn đạt.

V30 cố ý không còn rollback sang LLM Executive selector cũ. Một hệ thống có hai
policy chọn motive song song không phải dự phòng, mà là hai nguồn sự thật.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from cortex.subject.contracts import MindDecision, SubjectDeliberationFrame, SubjectExecutiveCommit
from cortex.subject.expression_commitment import hard_expression_boundary, read_commitment
from cortex.subject.neuro_control import (
    ChoiceSemanticAppraisal,
    NeuroActionSelector,
    NeuroDeliberationAdvice,
    NeuroSemanticAppraisal,
    SelectionResult,
    commit_selection,
)
from runtime.life_horizon import ensure_life_horizon_manager
from runtime.horizon_arbiter import HorizonArbiter
from runtime.review_lock import ReviewLock
from runtime.semantic_authority_v95 import validate_autonomous_motives
from runtime.interaction_ecology import classify_interaction_mode, response_expectation_for_mode

from actions.capability_registry import (
    validate_capability_arguments,
    validate_arguments_against_schema,
)

from .affordance import ExecutiveAffordance
from .executive_tools import (
    ExecutiveTool,
    build_capability_index,
    catalog_index,
    filter_executive_tools,
    retrieve_executive_tools,
    select_tools_by_id,
)

_DEFAULT_FILTER_EXECUTIVE_TOOLS = filter_executive_tools
_DEFAULT_RETRIEVE_EXECUTIVE_TOOLS = retrieve_executive_tools

# Chỉ các action mà *model capability planner* được phép phát ra.
# ``body_only`` và ``select_affordance`` là nhãn materialization nội bộ của runtime,
# tuyệt đối không đưa vào schema model để planner không lén trở thành Executive cũ.
_MODEL_ACTIONS = {
    "silence",
    "discover_capabilities",
    "shortlist_capabilities",
    "invoke_capability",
    "speak_from_capability",
    "select_capability",
}
_STAGE_MODEL_ACTIONS = {
    "initial": {"silence", "discover_capabilities"},
    "capability_shortlist": {"silence", "shortlist_capabilities"},
    "capabilities": {"silence", "invoke_capability"},
    "result": {"silence", "speak_from_capability"},
    "interactive_initial": {"silence", "discover_capabilities"},
    "interactive_shortlist": {"silence", "shortlist_capabilities"},
    "interactive_capabilities": {"silence", "select_capability"},
}
_EFFECTS = {"observe", "read", "write", "control", "inspect", "communicate"}
_RISKS = {"none", "low", "medium", "high"}
EXECUTIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action", "affordance_id", "semantic_target", "speech_act", "tone",
        "capability_goal", "required_effect", "maximum_risk", "capability_domain",
        "capability_ids", "capability_id", "capability_arguments",
        "reason", "confidence",
    ],
    "properties": {
        "action": {"type": "string", "enum": sorted(_MODEL_ACTIONS)},
        "affordance_id": {"type": "string"},
        "semantic_target": {"type": "string"},
        "speech_act": {"type": "string"},
        "tone": {"type": "string"},
        "capability_goal": {"type": "string"},
        "required_effect": {"type": "string", "enum": sorted(_EFFECTS | {""})},
        "maximum_risk": {"type": "string", "enum": sorted(_RISKS)},
        "capability_domain": {"type": "string"},
        "capability_ids": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 6,
        },
        "capability_id": {"type": "string"},
        "capability_arguments": {"type": "object"},
        "reason": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}


def _flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _compact(value: Any, limit: int = 800) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def _clean_ids(value: Any, *, limit: int = 6) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    output: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = _compact(raw, 140)
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
        if len(output) >= limit:
            break
    return tuple(output)


@dataclass(frozen=True, slots=True)
class ExecutiveDecision:
    action: str = "unavailable"
    affordance_id: str = ""
    semantic_target: str = ""
    speech_act: str = ""
    tone: str = ""
    capability_goal: str = ""
    required_effect: str = ""
    maximum_risk: str = "low"
    capability_domain: str = ""
    capability_ids: tuple[str, ...] = ()
    capability_id: str = ""
    capability_arguments: Mapping[str, Any] = field(default_factory=dict)
    phase: str = ""
    reason: str = "executive_unavailable"
    confidence: float = 0.0
    # Risk này là metadata runtime khi materialize candidate, không phải quyền
    # quyết định discourse của capability planner. Model schema không chứa field này.
    intrusion_risk: float = 0.0
    subject_coalition_id: str = ""
    subject_proposal_ref: str = ""
    semantic_commitments: tuple[str, ...] = ()
    cognitive_requests: tuple[str, ...] = ()
    capability_result: Mapping[str, Any] | None = None

    @classmethod
    def parse(cls, value: Mapping[str, Any] | None) -> "ExecutiveDecision | None":
        if not isinstance(value, Mapping) or "action" not in value:
            return None
        action = str(value.get("action", "") or "").strip().lower()
        if action not in _MODEL_ACTIONS:
            return None
        effect = str(value.get("required_effect", "") or "").strip().lower()
        if effect not in (_EFFECTS | {""}):
            return None
        risk = str(value.get("maximum_risk", "low") or "low").strip().lower()
        if risk not in _RISKS:
            return None
        arguments = value.get("capability_arguments", {})
        if not isinstance(arguments, Mapping):
            return None
        return cls(
            action=action,
            affordance_id=_compact(value.get("affordance_id", ""), 220),
            semantic_target=_compact(value.get("semantic_target", ""), 1600),
            speech_act=_compact(value.get("speech_act", ""), 100).lower(),
            tone=_compact(value.get("tone", ""), 100).lower(),
            capability_goal=_compact(value.get("capability_goal", ""), 800),
            required_effect=effect,
            maximum_risk=risk,
            capability_domain=_compact(value.get("capability_domain", ""), 80).lower(),
            capability_ids=_clean_ids(value.get("capability_ids", ())),
            capability_id=_compact(value.get("capability_id", ""), 140),
            capability_arguments=dict(arguments),
            phase=_compact(value.get("phase", ""), 100).lower(),
            reason=_compact(value.get("reason", "executive_decision"), 360) or "executive_decision",
            confidence=_clamp01(value.get("confidence", 0.0)),
        )

    @classmethod
    def unavailable(cls, reason: str) -> "ExecutiveDecision":
        return cls(action="unavailable", reason=_compact(reason, 300) or "executive_unavailable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.executive_decision.v4",
            "action": self.action,
            "affordance_id": self.affordance_id,
            "semantic_target": self.semantic_target,
            "speech_act": self.speech_act,
            "tone": self.tone,
            "capability_goal": self.capability_goal,
            "required_effect": self.required_effect,
            "maximum_risk": self.maximum_risk,
            "capability_domain": self.capability_domain,
            "capability_ids": list(self.capability_ids),
            "capability_id": self.capability_id,
            "capability_arguments": dict(self.capability_arguments),
            "phase": self.phase,
            "reason": self.reason,
            "confidence": self.confidence,
            "intrusion_risk": self.intrusion_risk,
            "subject_coalition_id": self.subject_coalition_id,
            "subject_proposal_ref": self.subject_proposal_ref,
            "semantic_commitments": list(self.semantic_commitments),
            "cognitive_requests": list(self.cognitive_requests),
            "capability_result": dict(self.capability_result or {}),
        }


@dataclass(frozen=True, slots=True)
class InteractiveExecutiveDirective:
    status: str = "none"
    capability_id: str = ""
    capability_arguments: Mapping[str, Any] = field(default_factory=dict)
    semantic_goal: str = ""
    reason: str = "no_runtime_capability_needed"
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.interactive_executive_directive.v3",
            "status": self.status,
            "capability_id": self.capability_id,
            "capability_arguments": dict(self.capability_arguments),
            "semantic_goal": self.semantic_goal,
            "reason": self.reason,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class ExecutiveOutcome:
    candidate: Any | None
    selected_affordance_id: str
    action: str
    reason: str
    decision: ExecutiveDecision



class ExecutiveCortex:
    """Adapter cho neuro deliberation và capability planning.

    Tên class được giữ để không làm vỡ wiring bên ngoài, nhưng V30 không còn xem
    object này là một policy layer thứ hai cho conversational trajectory.
    """

    def __init__(
        self,
        owner: Any,
        client: Any,
        *,
        invoke_structured: Any,
        logger: Any | None = None,
        clock: Any = time.time,
    ) -> None:
        self.owner = owner
        self.client = client
        self.invoke_structured = invoke_structured
        self.logger = logger
        self._clock = clock
        # Neuro control is intentionally tiny: Subject Core already owns causal
        # scoring. This layer resolves routine competition locally and asks the LLM
        # only when ambiguity is materially consequential or commitment-conflicted.
        self._neuro_selector = NeuroActionSelector()
        # V74: giữ reference riêng để test/degraded owner không cần full coordinator.
        self._horizon_arbiter = getattr(owner, "horizon_arbiter", None) or HorizonArbiter(logger=logger)
        self._review_lock = getattr(owner, "review_lock", None) or ReviewLock(logger=logger)

    @property
    def enabled(self) -> bool:
        """Subject commit authority luôn khả dụng, kể cả khi LLM rớt.

        MindLoop dùng property này để chọn commit port. Nếu buộc nó vào trạng thái
        provider, mất mạng sẽ vô tình đổi luôn policy chọn hành động. V30 tách hai
        dependency model ra khỏi authority deterministic của Subject+Neuro.
        """
        return True

    @property
    def neuro_deliberator_enabled(self) -> bool:
        return bool(_flag("ERIS_NEURO_DELIBERATOR_ENABLED", True) and self.client is not None)

    @property
    def capability_planner_enabled(self) -> bool:
        return bool(_flag("ERIS_CAPABILITY_PLANNER_ENABLED", True) and self.client is not None)

    @staticmethod
    def _system_prompt(*, stage: str) -> str:
        stage_rule = {
            "initial": (
                "No capability schemas are supplied. If runtime capability may help, choose discover_capabilities "
                "with a semantic capability_goal, one required_effect, optional domain, and risk ceiling."
            ),
            "capability_shortlist": (
                "Compact live capability cards are supplied without input schemas. Semantically shortlist up to six "
                "exact capability_ids that best satisfy capability_goal. Return shortlist_capabilities. Never invent an ID."
            ),
            "capabilities": (
                "Full schemas are supplied only for shortlisted live capabilities. Select exactly one offered "
                "capability_id and provide capability_arguments matching its input_schema, or choose silence."
            ),
            "result": (
                "A verified capability result is supplied. Do not request another capability. Choose only silence "
                "or speak_from_capability."
            ),
            "interactive_initial": (
                "This is a direct user turn. Decide only whether runtime capability discovery is needed. Choose "
                "discover_capabilities or silence. Do not answer the user."
            ),
            "interactive_shortlist": (
                "Compact live capability cards are supplied for a direct user turn. Semantically shortlist up to six exact "
                "capability_ids. Return shortlist_capabilities or silence."
            ),
            "interactive_capabilities": (
                "Full schemas are supplied only for shortlisted capabilities. Choose select_capability with one exact "
                "offered capability_id and schema-valid capability_arguments, or silence."
            ),
        }[stage]
        return (
            "You are ERIS Capability Planner. DeepSeek owns observation, private reflection, seed generation and public "
            "wording. Runtime owns availability, permission, argument validation, execution, concurrency and evidence. "
            "For direct chat, subject_decision is already committed: capability selection may realize it but must not replace "
            "its motive, action family or semantic commitments. "
            "Subject Core plus the neuro action gate already own conversational trajectory. You are a bounded capability "
            "planner for a committed semantic goal, not a second Executive over motive or dialogue state. Silence is a first-class "
            "action. Under social ambiguity prefer reversible low-pressure moves. An optional invitation is weak evidence, "
            "not user debt. semantic_target is a meaning brief, never final wording. Timing and wake policy belong to "
            "TemporalMind/runtime, not to this capability planner. "
            "When committed_subject_context is supplied, preserve that action family and semantic goal. Never invent or "
            "request coalition/proposal/thread identities; those are runtime-private. "
            + stage_rule + " Return exactly one JSON object."
        )

    def _candidate_capabilities(
        self,
        *,
        required_effect: str,
        maximum_risk: str,
        domain: str,
    ) -> tuple[ExecutiveTool, ...]:
        """Use V54 hard filtering, while honoring an injected V53 facade."""
        retrieve_overridden = retrieve_executive_tools is not _DEFAULT_RETRIEVE_EXECUTIVE_TOOLS
        filter_overridden = filter_executive_tools is not _DEFAULT_FILTER_EXECUTIVE_TOOLS
        if retrieve_overridden and not filter_overridden:
            return retrieve_executive_tools(
                self.owner,
                required_effect=required_effect,
                maximum_risk=maximum_risk,
                domain=domain,
                include_unavailable=False,
                refresh=False,
            )
        return filter_executive_tools(
            self.owner,
            required_effect=required_effect,
            maximum_risk=maximum_risk,
            domain=domain,
            include_unavailable=False,
            refresh=False,
        )

    def plan_interactive(self, user_text: str, *, snapshot: Mapping[str, Any] | None = None) -> InteractiveExecutiveDirective:
        source = snapshot or {}
        subject = source.get("subject_decision", {})
        arguments: Mapping[str, Any] = {}
        if isinstance(subject, Mapping) and subject:
            arguments = subject.get("arguments", {})
            arguments = arguments if isinstance(arguments, Mapping) else {}
            # Subject đã commit một câu hội thoại thuần túy thì không gọi LLM chỉ
            # để hỏi "có cần capability không". Tool planner chỉ được chạy khi
            # capability_route được commit rõ ràng.
            if not bool(arguments.get("capability_route", False)):
                return InteractiveExecutiveDirective(
                    status="none",
                    semantic_goal=str(arguments.get("semantic_target", "") or ""),
                    reason="subject_commit_requires_no_capability",
                    confidence=1.0,
                )
        if not self.capability_planner_enabled:
            return InteractiveExecutiveDirective(status="unavailable", reason="capability_planner_unavailable")
        index = build_capability_index(self.owner, refresh=True)
        if not arguments:
            discovered = self._decide_interactive(
                user_text,
                snapshot=snapshot,
                stage="interactive_initial",
                capability_index=index.to_dict(),
                candidate_tools=(),
                offered_tools=(),
            )
            if discovered is None:
                return InteractiveExecutiveDirective(status="unavailable", reason="interactive_capability_discovery_failure")
            if discovered.action != "discover_capabilities":
                return InteractiveExecutiveDirective(
                    status="none",
                    semantic_goal=discovered.semantic_target or discovered.capability_goal,
                    reason=discovered.reason,
                    confidence=discovered.confidence,
                )
            arguments = {
                "required_effect": discovered.required_effect,
                "maximum_risk": discovered.maximum_risk,
                "capability_domain": discovered.capability_domain,
            }

        required_effect = str(arguments.get("required_effect", "") or "")
        try:
            maximum_risk = float(arguments.get("maximum_risk", 1.0))
        except (TypeError, ValueError):
            maximum_risk = 1.0
        capability_domain = str(arguments.get("capability_domain", "") or "")
        candidates = self._candidate_capabilities(
            required_effect=required_effect,
            maximum_risk=maximum_risk,
            domain=capability_domain,
        )
        offered = self._semantic_shortlist_interactive(
            user_text, snapshot=snapshot, index=index.to_dict(), candidates=candidates,
        )
        if not offered:
            return InteractiveExecutiveDirective(status="none", reason="no_matching_live_capability")
        selected = self._decide_interactive(
            user_text, snapshot=snapshot, stage="interactive_capabilities",
            capability_index=index.to_dict(), candidate_tools=(), offered_tools=offered,
        )
        if selected is None:
            return InteractiveExecutiveDirective(status="unavailable", reason="interactive_capability_selection_failure")
        if selected.action != "select_capability":
            return InteractiveExecutiveDirective(
                status="none", semantic_goal=selected.semantic_target,
                reason=selected.reason, confidence=selected.confidence,
            )
        tool = catalog_index(offered).get(selected.capability_id)
        if tool is None or not tool.available:
            return InteractiveExecutiveDirective(
                status="unavailable", semantic_goal=selected.semantic_target,
                reason="interactive_capability_not_offered", confidence=selected.confidence,
            )
        selected_arguments = dict(selected.capability_arguments)
        valid, argument_reason = validate_arguments_against_schema(
            selected_arguments, tool.input_schema, capability_id=tool.capability_id
        )
        if not valid:
            return InteractiveExecutiveDirective(
                status="unavailable", semantic_goal=selected.semantic_target,
                reason=f"interactive_capability_arguments_invalid:{argument_reason}", confidence=selected.confidence,
            )
        return InteractiveExecutiveDirective(
            status="selected",
            capability_id=tool.capability_id,
            capability_arguments=selected_arguments,
            semantic_goal=selected.semantic_target or selected.capability_goal,
            reason=selected.reason,
            confidence=selected.confidence,
        )

    def _commit_from_selection(
        self,
        frame: SubjectDeliberationFrame,
        selection: SelectionResult,
        *,
        reason: str,
        source: str,
        semantic_commitments: Sequence[str] = (),
        cognitive_requests: Sequence[str] = (),
        confidence: float | None = None,
        review_conditions: Sequence[str] = (),
    ) -> SubjectExecutiveCommit:
        # V103: Subject/Neuro vẫn chấm frame hiện tại, nhưng khi *nhiều*
        # ExpressionCommitment tự thân cùng cạnh tranh thì winner public cần một
        # chút continuity qua frame. Stabilizer không dựng coalition mới và sẽ
        # fail-open về selection gốc nếu frame nằm ngoài scope của nó.
        try:
            # V103 FINAL: candidate_bypass đã được continuous motor commit action
            # family. Không cho migration workspace cũ "ổn định" lại một quyết
            # định mà nó không còn sở hữu authority. Structured/legacy path vẫn
            # dùng stabilizer như trước để backward compatibility.
            _nervous_bypass = str(getattr(selection, "reason", "") or "").startswith(
                "candidate_bypass:nervous_motor:"
            )
            competition_v103 = getattr(self.owner, "expression_competition_workspace_v103", None)
            stabilize = getattr(competition_v103, "stabilize", None)
            if callable(stabilize) and not _nervous_bypass:
                selection = stabilize(
                    frame,
                    selection,
                    source=source,
                    now=float(self._clock()),
                )
        except Exception as exc:
            self._log("debug", "[expression workspace v103] stabilize degraded: %s", exc)

        # V74 ReviewLock được ghi sau khi winner cuối cùng đã qua merge/review.
        # Nó không chọn winner; nó chỉ bảo toàn semantic identity cho các review
        # downstream và audit, trừ khi một boundary mới invalidates quyết định.
        try:
            self._review_lock.lock(frame, selection, now=float(self._clock()))
        except Exception as exc:
            self._log("debug", "[review-lock-v74] lock degraded: %s", exc)

        # Một mapping duy nhất nằm ở neuro_control. ExecutiveCortex chỉ điều phối
        # việc có cần mời deliberator hay không, không sở hữu cách bind runtime ID.
        return commit_selection(
            frame,
            selection,
            reason=reason,
            source=source,
            clock=self._clock,
            semantic_commitments=semantic_commitments,
            cognitive_requests=cognitive_requests,
            confidence=confidence,
            review_conditions=review_conditions,
        )

    @staticmethod
    def _selection_action_probabilities(
        frame: SubjectDeliberationFrame,
        selection: SelectionResult,
    ) -> dict[str, float]:
        totals: dict[str, float] = {}
        for index, coalition in enumerate(frame.coalitions):
            action = str(coalition.action_kind or "unknown").strip().lower() or "unknown"
            probability = float(selection.probabilities[index]) if index < len(selection.probabilities) else 0.0
            totals[action] = totals.get(action, 0.0) + probability
        return {key: round(value, 4) for key, value in sorted(totals.items())}

    def _log_neuro_selection(
        self,
        label: str,
        frame: SubjectDeliberationFrame,
        selection: SelectionResult,
        *,
        mode: str,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        winner = selection.winner(frame)
        self._log(
            "info",
            "[%s] mode=%s winner=%s action=%s probs=%s scores=%s margin=%.3f entropy=%.3f reason=%s extra=%s",
            label,
            mode,
            selection.winner_index,
            str(winner.action_kind or "unknown"),
            self._selection_action_probabilities(frame, selection),
            [round(float(value), 4) for value in selection.scores],
            selection.margin,
            selection.entropy,
            selection.reason,
            dict(extra or {}),
        )

    def _log_neuro_commit(
        self,
        frame: SubjectDeliberationFrame,
        commit: SubjectExecutiveCommit,
        *,
        mode: str,
    ) -> None:
        coalition = next(
            (item for item in frame.coalitions if item.coalition_id == commit.selected_coalition_id),
            None,
        )
        self._log(
            "info",
            "[neuro-commit] mode=%s action=%s coalition=%s proposal=%s source=%s confidence=%.2f reason=%s",
            mode,
            str(getattr(coalition, "action_kind", "unknown") or "unknown"),
            commit.selected_coalition_id,
            commit.selected_proposal_ref,
            commit.source,
            commit.confidence,
            commit.reason,
        )

    @staticmethod
    def _deliberation_choice_schema(frame: SubjectDeliberationFrame) -> dict[str, Any]:
        indices = list(range(len(frame.coalitions)))
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "choice_index", "semantic_commitments", "cognitive_requests",
                "reason", "confidence", "review_conditions",
            ],
            "properties": {
                # Ephemeral indices are local to this frame. Unlike coalition IDs,
                # they cannot leak meaningfully from a previous provider session.
                "choice_index": {"type": "integer", "enum": indices},
                "semantic_commitments": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                "cognitive_requests": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                "reason": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "review_conditions": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            },
        }

    @staticmethod
    def _deliberation_advice_schema(frame: SubjectDeliberationFrame) -> dict[str, Any]:
        schema = ExecutiveCortex._deliberation_choice_schema(frame)
        properties = dict(schema["properties"])
        properties.update({
            "should_follow_up": {"type": "boolean"},
            "material_delta": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "followup_brief": {"type": "string"},
        })
        return {
            **schema,
            "required": [
                *schema["required"],
                "should_follow_up",
                "material_delta",
                "followup_brief",
            ],
            "properties": properties,
        }

    @staticmethod
    def _subject_deliberation_payload(
        frame: SubjectDeliberationFrame,
        selection: SelectionResult,
        *,
        user_text: str,
        snapshot: Mapping[str, Any] | None,
        mode: str,
    ) -> dict[str, Any]:
        source = snapshot or {}
        choices = []
        for index, coalition in enumerate(frame.coalitions):
            choices.append({
                "index": index,
                "action_kind": coalition.action_kind,
                "arguments": {
                    key: value
                    for key, value in dict(coalition.arguments).items()
                    if key in {
                        "semantic_target", "speech_act", "tone", "capability_route", "initiative_motive",
                        "candidate_kind", "source_owner", "intrusion_risk", "seed_id", "seed_quality",
                        "seed_specificity", "seed_motive_strength", "seed_semantic_completeness",
                        "seed_epistemic_mode", "seed_temporal_context", "claim_contract",
                    }
                },
                "supporting_need_ids": list(getattr(coalition, "supporting_need_ids", ()) or ()),
                "channel_score": selection.scores[index],
                "pragmatic_value": getattr(coalition, "expected_pragmatic_value", 0.0),
                "epistemic_value": getattr(coalition, "expected_epistemic_value", 0.0),
                "uncertainty": getattr(coalition, "uncertainty", 0.5),
            })
        return {
            "mode": str(mode or "runtime"),
            "user_text": str(user_text or ""),
            "workspace_broadcast": selection.broadcast.to_dict(),
            "fast_gate": {
                "provisional_winner_index": selection.winner_index,
                "margin": selection.margin,
                "entropy": selection.entropy,
                "reason": selection.reason,
            },
            "choices": choices,
            "recent_public_history": list(source.get("recent_public_history", ()))[-6:],
            "relationship": source.get("relationship_summary", source.get("relationship", {})),
            "grounding_context": _compact(source.get("grounding_context", ""), 1400),
            "runtime_context": dict(
                (getattr(frame.observation, "context", {}) or {}).get("runtime_context", {})
            ) if isinstance((getattr(frame.observation, "context", {}) or {}).get("runtime_context", {}), Mapping) else {},
        }

    @staticmethod
    def _autonomous_semantic_appraisal_schema(frame: SubjectDeliberationFrame) -> dict[str, Any]:
        indices = list(range(len(frame.coalitions)))
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "choice_appraisals", "boundary_pressure", "interruption_cost",
                "semantic_commitments", "cognitive_requests", "review_conditions",
                "reason", "confidence",
            ],
            "properties": {
                "choice_appraisals": {
                    "type": "array",
                    "minItems": len(indices),
                    "maxItems": len(indices),
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "index", "motive_validity", "novelty", "continuity_fit",
                            "epistemic_support", "reversibility",
                        ],
                        "properties": {
                            "index": {"type": "integer", "enum": indices},
                            "motive_validity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            "novelty": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            "continuity_fit": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            "epistemic_support": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            "reversibility": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        },
                    },
                },
                "boundary_pressure": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "interruption_cost": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "semantic_commitments": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                "cognitive_requests": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                "review_conditions": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                "reason": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
        }

    @staticmethod
    def _autonomous_semantic_review_needed(
        frame: SubjectDeliberationFrame,
        selection: SelectionResult,
    ) -> bool:
        winner = selection.winner(frame)
        if str(winner.action_kind or "").strip().lower() != "speak":
            return False
        arguments = dict(winner.arguments or {})
        kind = str(arguments.get("candidate_kind", "") or "").strip().lower()
        source = str(arguments.get("source_owner", "") or "").strip().lower()
        # Social/self-originated speech is exactly where semantic motive, recent
        # user boundaries and epistemic support matter more than raw scheduler
        # salience. Task/capability speech already carries stronger typed evidence.
        return bool(
            arguments.get("seed_id")
            or kind in {
                "random_chat", "social_check_in", "casual_curiosity", "playful_prompt", "inner_thought",
                "conversation_repair", "continuation", "followup_reminder",
            }
            or source in {"random_chat_seed", "subject_initiative", "relational", "conversation_handoff"}
        )

    @staticmethod
    def _conversation_trajectory_from_frame(frame: SubjectDeliberationFrame) -> dict[str, Any]:
        """Lấy trajectory typed cùng revision mà Subject đã nhìn thấy."""
        observation_context = dict(getattr(getattr(frame, "observation", None), "context", {}) or {})
        runtime_context = observation_context.get("runtime_context")
        runtime_context = dict(runtime_context) if isinstance(runtime_context, Mapping) else {}
        trajectory = runtime_context.get("conversation_trajectory")
        return dict(trajectory) if isinstance(trajectory, Mapping) else {}

    @classmethod
    def _apply_conversation_trajectory_appraisal(
        cls,
        frame: SubjectDeliberationFrame,
        appraisal: NeuroSemanticAppraisal,
    ) -> NeuroSemanticAppraisal:
        """Ưu tiên semantic development cùng topic, không ép ERIS phải nói.

        Trajectory chỉ là appraisal cũ cho các candidate nội bộ. Nó không được dùng
        handoff để tạo "món nợ lượt nói" hay ép một public action; public semantic
        authority thuộc RootAgent của ConversationKernel.
        """
        trajectory = cls._conversation_trajectory_from_frame(frame)
        if not trajectory or not bool(trajectory.get("active", False)) or bool(trajectory.get("closed", False)):
            return appraisal
        topic_id = _compact(trajectory.get("topic_id", ""), 100)
        momentum = _clamp01(trajectory.get("momentum", 0.0))
        if not topic_id or momentum < 0.42:
            return appraisal

        observation_context = dict(getattr(getattr(frame, "observation", None), "context", {}) or {})
        runtime_context = observation_context.get("runtime_context")
        runtime_context = dict(runtime_context) if isinstance(runtime_context, Mapping) else {}
        if bool(runtime_context.get("courtesy_hold", False) or runtime_context.get("awaiting_user_answer", False)):
            return appraisal
        aligned: set[int] = set()
        mapped: list[ChoiceSemanticAppraisal] = []
        ref_prefix = f"trajectory:{topic_id}"
        for item in appraisal.choices:
            if item.index < 0 or item.index >= len(frame.coalitions):
                mapped.append(item)
                continue
            coalition = frame.coalitions[item.index]
            action = str(getattr(coalition, "action_kind", "") or "").strip().lower()
            args = dict(getattr(coalition, "arguments", {}) or {})
            refs = tuple(str(ref or "").strip() for ref in (getattr(coalition, "evidence_refs", ()) or ()))
            is_aligned = any(ref == ref_prefix or ref.startswith(ref_prefix + ":") for ref in refs)
            if is_aligned and action == "speak":
                aligned.add(item.index)
                mapped.append(replace(
                    item,
                    motive_validity=_clamp01(max(item.motive_validity, 0.70 + 0.22 * momentum)),
                    continuity_fit=_clamp01(max(item.continuity_fit, 0.82 + 0.16 * momentum)),
                    novelty=_clamp01(max(item.novelty, 0.48)),
                ))
                continue

            if action == "speak":
                kind = str(args.get("candidate_kind", "") or "").strip().lower()
                source = str(args.get("source_owner", "") or "").strip().lower()
                speech_act = str(args.get("speech_act", "") or "").strip().lower()
                randomish = bool(
                    kind in {"random_chat", "social_check_in", "casual_curiosity", "playful_prompt"}
                    or source in {"mental_material", "expression_urge", "random_chat_seed"}
                )
                explicit_shift = speech_act in {"independent_topic_shift", "topic_shift", "shift_topic"}
                penalty = (0.58 if randomish and not explicit_shift else 0.34) * momentum
                motive = max(0.05, item.motive_validity * (1.0 - penalty))
                continuity = max(0.05, item.continuity_fit * (1.0 - min(0.78, penalty + 0.12)))
                if momentum >= 0.62 and randomish and not explicit_shift:
                    # Khi một topic đang có đà, generic small-talk không được thắng
                    # chỉ vì seed continuation chưa materialize kịp ở tick này.
                    # WAIT tốt hơn teleport sang "niềm vui nhỏ hôm nay".
                    motive = min(motive, 0.44)
                    continuity = min(continuity, 0.34)
                mapped.append(replace(
                    item,
                    motive_validity=motive,
                    continuity_fit=continuity,
                ))
            else:
                # Không phạt silence/body_only. V75 định hướng *nói về gì* nếu đã
                # nói, chứ không biến trajectory thành máy ép chatter.
                mapped.append(item)

        if not aligned:
            return replace(
                appraisal,
                choices=tuple(mapped),
                reason=f"trajectory_v75:no_aligned_candidate:{_compact(appraisal.reason, 420)}",
            )
        commitments = tuple(dict.fromkeys((
            *appraisal.semantic_commitments,
            "preserve_conversation_trajectory",
            "prefer_deepen_or_connect_before_unrelated_topic_shift",
        )))
        return replace(
            appraisal,
            choices=tuple(mapped),
            reason=f"trajectory_v75:topic={topic_id}:momentum={momentum:.2f}:{_compact(appraisal.reason, 360)}",
            semantic_commitments=commitments[:8],
        )

    @staticmethod
    def _persistent_individual_from_frame(frame: SubjectDeliberationFrame) -> dict[str, Any]:
        observation_context = dict(getattr(getattr(frame, "observation", None), "context", {}) or {})
        runtime_context = observation_context.get("runtime_context")
        runtime_context = dict(runtime_context) if isinstance(runtime_context, Mapping) else {}
        value = runtime_context.get("persistent_individual")
        return dict(value) if isinstance(value, Mapping) else {}

    @classmethod
    def _apply_interaction_ecology_appraisal(
        cls,
        frame: SubjectDeliberationFrame,
        appraisal: NeuroSemanticAppraisal,
    ) -> NeuroSemanticAppraisal:
        """V76: chấm *cách hiện diện*, không chỉ semantic topic.

        Logic này cố ý deterministic. LLM critic có thể đánh giá nội dung hay/dở,
        nhưng không được quên rằng ASK lặp liên tục tạo fatigue còn COMMENT/MUTTER
        response_expectation=none không tạo nợ trả lời. Task/repair luôn được nới.
        """
        root = cls._persistent_individual_from_frame(frame)
        if not root:
            return appraisal
        ecology = root.get("interaction_ecology")
        ecology = dict(ecology) if isinstance(ecology, Mapping) else {}
        plan = root.get("relational_plan")
        plan = dict(plan) if isinstance(plan, Mapping) else {}
        self_model = root.get("operational_self_model")
        self_model = dict(self_model) if isinstance(self_model, Mapping) else {}
        if not ecology:
            return appraisal

        qfat = _clamp01(ecology.get("question_fatigue", 0.0))
        pfat = _clamp01(ecology.get("public_fatigue", 0.0))
        streak = max(0, int(ecology.get("unsolicited_streak", 0) or 0))
        qsince = max(0, int(ecology.get("questions_since_user_contribution", 0) or 0))
        habituation = dict(ecology.get("mode_habituation", {}) or {})
        preferred = set(str(x) for x in (plan.get("effective_preferred_modes", ()) or ())) if plan.get("active") else set()
        avoided = set(str(x) for x in (plan.get("avoid_modes", ()) or ())) if plan.get("active") else set()
        over_q = _clamp01(self_model.get("over_questioning", 0.0))

        mapped: list[ChoiceSemanticAppraisal] = []
        for item in appraisal.choices:
            if item.index < 0 or item.index >= len(frame.coalitions):
                mapped.append(item)
                continue
            coalition = frame.coalitions[item.index]
            action = str(getattr(coalition, "action_kind", "") or "").strip().lower()
            args = dict(getattr(coalition, "arguments", {}) or {})
            act = str(args.get("speech_act", "") or "").strip().lower()
            kind = str(args.get("candidate_kind", "") or "").strip().lower()
            target = _compact(args.get("semantic_target", ""), 1000)
            mode = classify_interaction_mode(
                speech_act=act, candidate_kind=kind, action_kind=action, text=target
            )
            expectation = str(args.get("response_expectation", "") or "").strip().lower() or response_expectation_for_mode(mode, speech_act=act)

            if action in {"silence", "body_only"}:
                boost = min(0.24, 0.18 * max(qfat, pfat))
                mapped.append(replace(
                    item,
                    motive_validity=_clamp01(item.motive_validity + boost),
                    reversibility=_clamp01(max(item.reversibility, 0.92)),
                ))
                continue

            multiplier = 1.0
            hab = _clamp01(habituation.get(mode, 0.0))
            multiplier *= max(0.34, 1.0 - 0.55 * hab)
            if mode == "ask":
                multiplier *= max(0.12, 1.0 - 0.78 * qfat)
                multiplier *= max(0.48, 1.0 - 0.30 * over_q)
                if qsince >= 2:
                    multiplier *= 0.22
            if streak > 0 and mode not in {"task", "repair"}:
                multiplier *= max(0.20, 1.0 - 0.26 * streak)
            if mode in preferred:
                multiplier *= 1.24
            if mode in avoided:
                multiplier *= 0.28
            if expectation == "invited":
                multiplier *= 0.88
            elif expectation == "required" and mode not in {"task", "repair"}:
                multiplier *= 0.60
            if pfat >= 0.88 and mode not in {"task", "repair", "screen_react"}:
                multiplier = min(multiplier, 0.12)

            mapped.append(replace(
                item,
                motive_validity=_clamp01(item.motive_validity * multiplier),
                continuity_fit=_clamp01(item.continuity_fit * (0.92 + 0.08 * min(1.0, multiplier))),
            ))

        commitments = tuple(dict.fromkeys((*appraisal.semantic_commitments,
            "respect_interaction_ecology",
            "questions_are_not_default_connection",
            "response_expectation_none_creates_no_reply_debt",
        )))
        return replace(
            appraisal, choices=tuple(mapped),
            reason=f"interaction_ecology_v76:qfat={qfat:.2f}:pfat={pfat:.2f}:streak={streak}:" + _compact(appraisal.reason, 360),
            semantic_commitments=commitments[:8],
        )

    @classmethod
    def _apply_continuous_self_appraisal(
        cls,
        frame: SubjectDeliberationFrame,
        appraisal: NeuroSemanticAppraisal,
    ) -> NeuroSemanticAppraisal:
        """V77: biến self-process thành bias trước Neuro, không thành hard winner.

        Chỉ áp mạnh ở autonomous opportunity. Direct user turn vẫn phải được trả lời
        theo task/repair/semantic intent của user, không để "tôi đang mệt" giả lập
        trở thành lý do né một yêu cầu thật. Private cognition có thể cao trong khi
        public_action_pressure thấp; khi đó silence được tăng utility.
        """
        observation = getattr(frame, "observation", None)
        if str(getattr(observation, "kind", "") or "") == "user_turn":
            return appraisal
        root = cls._persistent_individual_from_frame(frame)
        self_state = root.get("continuous_self") if isinstance(root, Mapping) else None
        self_state = dict(self_state) if isinstance(self_state, Mapping) else {}
        if not self_state:
            return appraisal

        public_pressure = _clamp01(self_state.get("public_action_pressure", 0.0))
        private_pressure = _clamp01(self_state.get("private_cognition_pressure", 0.0))
        social_availability = _clamp01(self_state.get("social_availability", 0.5), 0.5)
        orientation = str(self_state.get("orientation", "") or "")
        commitment = self_state.get("active_commitment")
        commitment = dict(commitment) if isinstance(commitment, Mapping) else {}
        preferred = set(str(x) for x in (commitment.get("preferred_modes", ()) or ()))
        avoided = set(str(x) for x in (commitment.get("avoid_modes", ()) or ()))

        mapped: list[ChoiceSemanticAppraisal] = []
        for item in appraisal.choices:
            if item.index < 0 or item.index >= len(frame.coalitions):
                mapped.append(item)
                continue
            coalition = frame.coalitions[item.index]
            action = str(getattr(coalition, "action_kind", "") or "").strip().lower()
            args = dict(getattr(coalition, "arguments", {}) or {})
            act = str(args.get("speech_act", "") or "").strip().lower()
            kind = str(args.get("candidate_kind", "") or "").strip().lower()
            target = _compact(args.get("semantic_target", ""), 1000)
            mode = classify_interaction_mode(
                speech_act=act, candidate_kind=kind, action_kind=action, text=target
            )

            if action in {"silence", "body_only"}:
                # Khi self-process không có lý do public đủ mạnh, silence là hành
                # động tích cực chứ không phải candidate thất bại.
                boost = min(0.32, 0.10 + 0.30 * (1.0 - public_pressure))
                if private_pressure >= 0.52:
                    boost += 0.04  # nghĩ tiếp trong private, không cần phát loa.
                mapped.append(replace(
                    item,
                    motive_validity=_clamp01(item.motive_validity + boost),
                    reversibility=_clamp01(max(item.reversibility, 0.94)),
                ))
                continue

            if mode in {"task", "repair"}:
                mapped.append(item)
                continue

            multiplier = 0.48 + 0.78 * public_pressure
            if social_availability < 0.34:
                multiplier *= 0.66
            if mode in preferred:
                multiplier *= 1.24
            if mode in avoided:
                multiplier *= 0.26
            if mode == "ask" and public_pressure < 0.48:
                multiplier *= 0.52
            if orientation in {"quiet_presence", "quiet_company", "quiet_recovery", "private_concern"}:
                if mode not in {"screen_react", "mutter", "comment"}:
                    multiplier *= 0.72

            mapped.append(replace(
                item,
                motive_validity=_clamp01(item.motive_validity * max(0.05, min(1.55, multiplier))),
                continuity_fit=_clamp01(item.continuity_fit * (0.94 + 0.06 * min(1.0, multiplier))),
            ))

        commitments = tuple(dict.fromkeys((*appraisal.semantic_commitments,
            "respect_continuous_self_state",
            "private_cognition_does_not_imply_public_speech",
            "assistant_output_cannot_self_recharge_motive",
        )))
        return replace(
            appraisal, choices=tuple(mapped),
            reason=(f"continuous_self_v77:{orientation}:public={public_pressure:.2f}:"
                    f"private={private_pressure:.2f}:" + _compact(appraisal.reason, 330)),
            semantic_commitments=commitments[:8],
        )

    def _local_autonomous_semantic_appraisal(
        self,
        frame: SubjectDeliberationFrame,
        selection: SelectionResult,
    ) -> NeuroSemanticAppraisal:
        """Fallback typed khi semantic reviewer từ xa không sẵn sàng.

        V42 không cho timeout biến thành "fast speak cứ thế đi". Local fallback
        không giả vờ hiểu ngôn ngữ như DeepSeek; nó chỉ đọc những signal mà runtime
        *thật sự biết*: policy cost, reply baton, interruption, concern novelty,
        uncertainty và grounding requirement. Sau đó chính Neuro merge lại một lần.
        """

        observation_context = dict(getattr(getattr(frame, "observation", None), "context", {}) or {})
        runtime_context = observation_context.get("runtime_context")
        runtime_context = dict(runtime_context) if isinstance(runtime_context, Mapping) else {}
        blocked, boundary_reason = hard_expression_boundary(runtime_context)
        boundary = 1.0 if blocked else 0.0
        interruption = 1.0 if blocked else 0.0
        choices: list[ChoiceSemanticAppraisal] = []
        for index, candidate in enumerate(frame.coalitions):
            action = str(getattr(candidate, "action_kind", "") or "").strip().lower()
            arguments = dict(getattr(candidate, "arguments", {}) or {})
            pragmatic = _clamp01(getattr(candidate, "expected_pragmatic_value", 0.5), 0.5)
            uncertainty = _clamp01(getattr(candidate, "uncertainty", 0.5), 0.5)
            committed = read_commitment(arguments) is not None
            policy = 0.0 if committed else _clamp01(arguments.get("initiative_policy_cost", 0.0))
            concern_novelty = _clamp01(arguments.get("concern_novelty", 0.45), 0.45)
            seed_quality = _clamp01(arguments.get("seed_quality", concern_novelty), concern_novelty)
            novelty = max(0.08, min(0.92, 0.58 * concern_novelty + 0.42 * seed_quality))
            motive = max(0.08, min(0.92, 0.22 + 0.68 * pragmatic - 0.18 * policy))
            continuity = max(0.05, min(0.95, 0.78 - 0.56 * policy - 0.22 * interruption))
            if action in {"silence", "body_only"}:
                continuity = max(continuity, 0.72 if blocked else 0.58)
                novelty = 0.50

            # Local lane chỉ hạ epistemic support khi runtime biết candidate đòi
            # grounding nhưng không có direct evidence. Nó không phân tích từ ngữ
            # và không đoán claim từ topic.
            requires_grounding = bool(arguments.get("requires_grounding", False))
            evidence_refs = tuple(getattr(candidate, "evidence_refs", ()) or ())
            direct_refs = tuple(
                ref for ref in evidence_refs
                if str(ref).startswith(("vision:", "screen:", "grounded:", "evidence:"))
            )
            if requires_grounding:
                epistemic = 0.72 if direct_refs else 0.16
            else:
                epistemic = max(0.28, min(0.78, 0.66 - 0.34 * uncertainty))
            reversible = 0.96 if action == "silence" else (0.90 if action == "body_only" else max(0.40, 0.78 - 0.28 * policy))
            choices.append(ChoiceSemanticAppraisal(
                index=index,
                motive_validity=motive,
                novelty=novelty,
                continuity_fit=continuity,
                epistemic_support=epistemic,
                reversibility=reversible,
            ))
        appraisal = NeuroSemanticAppraisal(
            choices=tuple(choices),
            boundary_pressure=boundary,
            interruption_cost=interruption,
            confidence=0.58,
            reason=f"local_typed_semantic_critic:{boundary_reason or 'no_hard_boundary'}",
            semantic_commitments=("preserve_typed_evidence_scope",),
            cognitive_requests=("reduce_intrusion",) if interruption >= 0.55 else (),
            review_conditions=("remote_semantic_appraisal_unavailable",),
        )
        appraisal = self._apply_conversation_trajectory_appraisal(frame, appraisal)
        appraisal = self._apply_interaction_ecology_appraisal(frame, appraisal)
        return self._apply_continuous_self_appraisal(frame, appraisal)

    def _appraise_autonomous_frame(
        self,
        frame: SubjectDeliberationFrame,
        selection: SelectionResult,
        *,
        snapshot: Mapping[str, Any] | None,
        mode: str,
    ) -> NeuroSemanticAppraisal | None:
        payload = self._subject_deliberation_payload(
            frame, selection, user_text="", snapshot=snapshot, mode=mode
        )
        choice_contract = (
            f"This runtime frame contains exactly {len(frame.coalitions)} ephemeral choices indexed "
            f"0..{max(0, len(frame.coalitions) - 1)}. Appraise exactly those choices once. "
            "Do not discuss, reinterpret, or invent a different schema/contract size in reason. "
        )
        system = (
            "You are ERIS's slow semantic appraisal lane for autonomous action. You do NOT choose an action and you do "
            "NOT return a winner. Appraise every ephemeral choice_index on bounded dimensions only; local Neuro will merge "
            "the measurements into one final competition. motive_validity asks whether there is a concrete reason to act now, "
            "not merely a scheduler opportunity. novelty asks whether the semantic content adds something nontrivial. "
            "continuity_fit asks whether the semantic target remains coherent with the current thread, not whether ERIS should be quiet. "
            "runtime_context.conversation_handoff is observational causal/provenance evidence only; it never assigns a required next speaker or public action. "
            "runtime_context.conversation_trajectory is the authoritative semantic spine of the current topic. When it is active with high momentum, "
            "runtime_context.persistent_individual contains Interaction Ecology, multi-turn Relational Plan, and V77 continuous_self. "
            "continuous_self is operational causal state, NOT a consciousness claim: private_cognition_pressure may be high while public_action_pressure is low. "
            "Questions are not the default social action; "
            "respect question fatigue, public pacing, response_expectation and plan-preferred COMMENT/MUTTER/SCREEN_REACT/STORY/SILENCE modes. "
            "score trajectory-linked DEEPEN/CONNECT/BRANCH material above unrelated random_chat novelty; do not infer that every idle tick needs a new topic. "
            "In autonomous mode user_text may be intentionally empty; use conversation history/note through the RootAgent path for semantic continuity. "
            "If handoff evidence reports repair_needed or a likely self-caused reaction, treat that only as uncertain evidence; do not materialize or prefer a speech action here. "
            "boundary_pressure is high ONLY for an explicit user quiet/closure boundary or an active turn collision with direct runtime evidence. "
            "User silence, low familiarity, low response rate, generic cooldown, or merely appearing busy are NOT boundaries. "
            "interruption_cost is ONLY turn-collision cost; ordinary quiet has zero interruption cost. epistemic_support is support for the "
            "specific semantic target: present-world claims require direct evidence. A runtime clock/seed_temporal_context proves "
            "local time/date/weekday/daypart by clock only; it never proves weather, darkness, room lighting, ambient sound, user "
            "activity, emotion, or fatigue. A seed premise is semantic material, not permission to invent a current scene. "
            "reversibility measures how easy the wording is to revise. If an ExpressionCommitment exists, you are a critic only: "
            "you may request grounding/revision or rank speech realizations, but you must not reinterpret quiet as a reason to revoke expression. "
            "Write the JSON reason and semantic_commitments in natural Vietnamese so runtime traces stay readable; do not switch to Chinese. "
            "semantic_commitments are meaning constraints "
            "for the final chosen action, never public wording. cognitive_requests may use only verify_evidence, reduce_intrusion, "
            "preserve_continuity, estimate_capability, inspect_dissent. "
            + choice_contract
            + "Return exactly one JSON object."
        )
        try:
            raw = self.invoke_structured(
                self.client,
                system=system,
                payload=payload,
                schema=self._autonomous_semantic_appraisal_schema(frame),
                name="eris_neuro_autonomous_semantic_appraisal",
                max_tokens=620,
                reasoning_effort="low",
                inference_tier="main_cognition",
                priority=70,
                timeout_seconds=float(os.getenv("ERIS_NEURO_AUTONOMOUS_REVIEW_TIMEOUT_S", "5.0") or "5.0"),
            )
        except Exception as exc:
            self._log("warning", "[neuro-control] autonomous semantic appraisal failed: %s", exc)
            return None
        if not isinstance(raw, Mapping):
            return None
        seen: set[int] = set()
        choices: list[ChoiceSemanticAppraisal] = []
        raw_items = raw.get("choice_appraisals", ())
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, bytearray)):
            return None
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= len(frame.coalitions) or index in seen:
                continue
            seen.add(index)
            choices.append(ChoiceSemanticAppraisal(
                index=index,
                motive_validity=_clamp01(item.get("motive_validity", 0.5), 0.5),
                novelty=_clamp01(item.get("novelty", 0.5), 0.5),
                continuity_fit=_clamp01(item.get("continuity_fit", 0.5), 0.5),
                epistemic_support=_clamp01(item.get("epistemic_support", 0.5), 0.5),
                reversibility=_clamp01(item.get("reversibility", 0.5), 0.5),
            ))
        if len(seen) != len(frame.coalitions):
            return None
        appraisal = NeuroSemanticAppraisal(
            choices=tuple(choices),
            boundary_pressure=_clamp01(raw.get("boundary_pressure", 0.0)),
            interruption_cost=_clamp01(raw.get("interruption_cost", 0.0)),
            confidence=_clamp01(raw.get("confidence", 0.0)),
            reason=_compact(raw.get("reason", "autonomous_semantic_appraisal"), 500),
            semantic_commitments=tuple(
                _compact(item, 500) for item in raw.get("semantic_commitments", ()) if _compact(item, 500)
            )[:8],
            cognitive_requests=tuple(
                _compact(item, 160).lower() for item in raw.get("cognitive_requests", ()) if _compact(item, 160)
            )[:6],
            review_conditions=tuple(
                _compact(item, 180).lower() for item in raw.get("review_conditions", ()) if _compact(item, 180)
            )[:8],
        )
        appraisal = self._apply_conversation_trajectory_appraisal(frame, appraisal)
        appraisal = self._apply_interaction_ecology_appraisal(frame, appraisal)
        return self._apply_continuous_self_appraisal(frame, appraisal)

    def review_subject_frame(
        self,
        frame: SubjectDeliberationFrame,
        *,
        baseline_commit: SubjectExecutiveCommit,
        user_text: str = "",
        snapshot: Mapping[str, Any] | None = None,
        mode: str = "interactive_continuation",
    ) -> NeuroDeliberationAdvice | None:
        """Run slow Neuro as an advisory continuation, never as a blocking commit.

        The direct chat turn has already been finalized from the deterministic
        fast gate.  This method therefore never calls SubjectService and cannot
        retroactively rewrite authorship.  It only returns a semantic second
        opinion that a continuation runtime may later discard or surface.
        """
        selection = self._neuro_selector.select(frame)
        if not selection.parallel_review_recommended or not self.neuro_deliberator_enabled:
            return None
        payload = self._subject_deliberation_payload(
            frame, selection, user_text=user_text, snapshot=snapshot, mode=mode
        )
        payload["baseline_commit"] = {
            "provisional_winner_index": selection.winner_index,
            "semantic_commitments": list(baseline_commit.semantic_commitments),
        }
        system = (
            "You are ERIS's slow, bounded Neuro reviewer running in parallel with an already-served direct chat reply. "
            "The fast path has already committed and may already be speaking, so you are advisory only. Compare the offered "
            "semantic choices for consequential conflict, missed constraints, or a genuinely new insight. Return one current "
            "choice_index using only the ephemeral indices in this frame. Never mention or invent runtime IDs. "
            "followup_brief is a concise semantic brief for a possible later continuation, never final public wording. "
            "Set should_follow_up=false and followup_brief='' when the slow review merely agrees, paraphrases, or adds trivia. "
            "Set should_follow_up=true only when a still-current user would materially benefit from a correction or consequential "
            "new point. material_delta measures that difference from 0 to 1. cognitive_requests may use only verify_evidence, "
            "reduce_intrusion, preserve_continuity, estimate_capability, inspect_dissent. Return exactly one JSON object."
        )
        try:
            raw = self.invoke_structured(
                self.client,
                system=system,
                payload=payload,
                schema=self._deliberation_advice_schema(frame),
                name="eris_neuro_continuation_interactive",
                max_tokens=440,
                reasoning_effort="low",
                inference_tier="main_cognition",
                priority=72,
                timeout_seconds=0.0,
            )
        except Exception as exc:
            self._log("warning", "[neuro-continuation] reviewer failed: %s", exc)
            return None
        if not isinstance(raw, Mapping):
            return None
        try:
            choice_index = int(raw.get("choice_index"))
        except (TypeError, ValueError):
            return None
        if choice_index < 0 or choice_index >= len(frame.coalitions):
            return None
        deliberated = SelectionResult(
            winner_index=choice_index,
            scores=selection.scores,
            probabilities=selection.probabilities,
            margin=selection.margin,
            entropy=selection.entropy,
            requires_deliberation=False,
            parallel_review_recommended=False,
            reason="parallel_neuro_review",
            broadcast=selection.broadcast,
        )
        commit = self._commit_from_selection(
            frame,
            deliberated,
            reason=_compact(raw.get("reason", "parallel_neuro_review"), 500) or "parallel_neuro_review",
            source="neuro_parallel_advisory",
            semantic_commitments=raw.get("semantic_commitments", ()),
            cognitive_requests=raw.get("cognitive_requests", ()),
            confidence=_clamp01(raw.get("confidence", 0.0)),
            review_conditions=raw.get("review_conditions", ()),
        )
        return NeuroDeliberationAdvice(
            commit=commit,
            followup_brief=_compact(raw.get("followup_brief", ""), 1200),
            material_delta=_clamp01(raw.get("material_delta", 0.0)),
            should_follow_up=bool(raw.get("should_follow_up", False)),
        )

    def _deliberate_subject_choice(
        self,
        frame: SubjectDeliberationFrame,
        selection: SelectionResult,
        *,
        user_text: str,
        snapshot: Mapping[str, Any] | None,
        mode: str,
    ) -> SubjectExecutiveCommit:
        """Invite the LLM as a deliberative specialist, never as state owner.

        The model sees semantic action channels with *local indices only*. It may
        compare consequences or request one bounded cognitive review, but runtime
        alone maps the result back to coalition/proposal identity.
        """
        payload = self._subject_deliberation_payload(
            frame, selection, user_text=user_text, snapshot=snapshot, mode=mode
        )
        system = (
            "You are ERIS's bounded deliberative cortex. Routine action selection has already happened in a fast "
            "competition layer. You are called only because the remaining choice is materially consequential, surprising "
            "in a consequential context, or commitment-conflicted. "
            "Compare the offered semantic choices and their likely consequences. Return exactly one current choice_index. "
            "The indices are ephemeral; do not mention or invent runtime IDs, thread IDs, coalition IDs or proposal IDs. "
            "semantic_commitments are concise meanings for Surface, never final wording. cognitive_requests may contain "
            "only verify_evidence, reduce_intrusion, preserve_continuity, estimate_capability, inspect_dissent. "
            "Prefer reversible/epistemic actions when uncertainty is material. Return one JSON object."
        )
        try:
            raw = self.invoke_structured(
                self.client,
                system=system,
                payload=payload,
                schema=self._deliberation_choice_schema(frame),
                name=f"eris_neuro_deliberation_{mode}",
                max_tokens=360,
                reasoning_effort="low",
                inference_tier="main_cognition",
                priority=98 if mode == "interactive" else 66,
                timeout_seconds=float(
                    os.getenv(
                        (
                            "ERIS_NEURO_DELIBERATION_TIMEOUT_S"
                            if str(mode).startswith("interactive")
                            else "ERIS_NEURO_BACKGROUND_TIMEOUT_S"
                        ),
                        "10" if str(mode).startswith("interactive") else "0",
                    )
                    or ("10" if str(mode).startswith("interactive") else "0")
                ),
            )
        except Exception as exc:
            self._log("warning", "[neuro-control] deliberator failed mode=%s: %s", mode, exc)
            return self._commit_from_selection(
                frame, selection,
                reason="neuro_deliberator_failure_use_fast_gate",
                source="neuro_fast_gate_degraded",
                review_conditions=("deliberator_available",),
            )
        if not isinstance(raw, Mapping):
            return self._commit_from_selection(
                frame, selection,
                reason="neuro_deliberator_invalid_payload_use_fast_gate",
                source="neuro_fast_gate_degraded",
            )
        try:
            choice_index = int(raw.get("choice_index"))
        except (TypeError, ValueError):
            choice_index = -1
        if choice_index < 0 or choice_index >= len(frame.coalitions):
            self._log(
                "warning",
                "[neuro-control] invalid ephemeral choice_index=%r; using fast gate winner=%s",
                raw.get("choice_index"), selection.winner_index,
            )
            return self._commit_from_selection(
                frame, selection,
                reason="neuro_deliberator_out_of_frame_use_fast_gate",
                source="neuro_fast_gate_degraded",
            )
        deliberated = SelectionResult(
            winner_index=choice_index,
            scores=selection.scores,
            probabilities=selection.probabilities,
            margin=selection.margin,
            entropy=selection.entropy,
            requires_deliberation=False,
            parallel_review_recommended=False,
            reason="deliberator_resolved_competition",
            broadcast=selection.broadcast,
        )
        return self._commit_from_selection(
            frame,
            deliberated,
            reason=_compact(raw.get("reason", "neuro_deliberator"), 500) or "neuro_deliberator",
            source="neuro_deliberator",
            semantic_commitments=raw.get("semantic_commitments", ()),
            cognitive_requests=raw.get("cognitive_requests", ()),
            confidence=_clamp01(raw.get("confidence", 0.0)),
            review_conditions=raw.get("review_conditions", ()),
        )

    def _outcome_from_subject_commit(
        self,
        frame: SubjectDeliberationFrame,
        commit: SubjectExecutiveCommit,
        affordances: Sequence[ExecutiveAffordance],
    ) -> ExecutiveOutcome | None:
        """Materialize a committed non-tool coalition without another LLM call."""
        coalition = next(
            (item for item in frame.coalitions if item.coalition_id == commit.selected_coalition_id),
            None,
        )
        if coalition is None:
            return None
        arguments = dict(coalition.arguments)
        if bool(arguments.get("capability_route", False)):
            # Capability route đã được Subject commit; phần chọn API cụ thể thuộc
            # Capability Planner, không phải một vòng chọn motive thứ hai.
            return None
        action_kind = str(coalition.action_kind or "silence").strip().lower()
        offered = {
            item.affordance_id: item
            for item in affordances
            if isinstance(item, ExecutiveAffordance) and item.available
        }
        selected_affordance = None
        decision_action = action_kind
        affordance_id = ""
        if action_kind == "speak":
            affordance_id = str(arguments.get("affordance_id", "") or "").strip()
            selected_affordance = offered.get(affordance_id)
            if selected_affordance is None:
                return None
            decision_action = "select_affordance"
        elif action_kind not in {"silence", "body_only"}:
            return None

        decision = ExecutiveDecision(
            action=decision_action,
            affordance_id=affordance_id,
            semantic_target=_compact(arguments.get("semantic_target", ""), 1600),
            speech_act=_compact(arguments.get("speech_act", ""), 100).lower(),
            tone=_compact(arguments.get("tone", ""), 100).lower(),
            phase="neuro_action_gate",
            reason=commit.reason,
            confidence=commit.confidence,
            subject_coalition_id=coalition.coalition_id,
            subject_proposal_ref=coalition.primary_proposal_ref,
            semantic_commitments=commit.semantic_commitments or tuple(
                item for item in (
                    _compact(arguments.get("semantic_target", ""), 500),
                    _compact(arguments.get("speech_act", ""), 160),
                ) if item
            ),
            cognitive_requests=commit.cognitive_requests,
        )
        if action_kind == "silence":
            return ExecutiveOutcome(None, "", "silence", decision.reason, decision)
        if action_kind == "body_only":
            return ExecutiveOutcome(None, "", "body_only", decision.reason, decision)
        assert selected_affordance is not None
        return ExecutiveOutcome(
            self._rewrite_candidate(selected_affordance.candidate, decision),
            selected_affordance.affordance_id,
            "use_candidate",
            decision.reason,
            decision,
        )

    def _fallback_subject_commit(
        self,
        frame: SubjectDeliberationFrame,
        *,
        reason: str,
    ) -> SubjectExecutiveCommit:
        """Fallback vẫn dùng đúng competition của đường khỏe mạnh.

        Đây không phải policy dự phòng thứ hai. Nếu deliberator hỏng/vắng mặt,
        kết quả đã grounded của ``NeuroActionSelector`` tiếp tục giữ quyền chọn.
        Vì vậy không có ``coalitions[0]`` ma thuật và không có đổi authority.
        """
        selection = self._neuro_selector.select(frame)
        return self._commit_from_selection(
            frame,
            selection,
            reason=str(reason or "neuro_fast_gate_fallback"),
            source="neuro_fast_gate_degraded",
            review_conditions=("deliberator_available",),
        )

    def commit_subject_frame(
        self,
        frame: SubjectDeliberationFrame,
        *,
        user_text: str = "",
        snapshot: Mapping[str, Any] | None = None,
        mode: str = "interactive",
        allow_remote_deliberation: bool = True,
    ) -> SubjectExecutiveCommit | None:
        """Chọn một hành động từ frame hiện tại bằng đúng một authority.

        V31 invariant:
        - runtime giữ identity của coalition/proposal; model không nhìn thấy ID đó;
        - fast gate xử lý mọi lựa chọn routine/reversible, kể cả khi score sát nhau;
        - direct chat luôn fast-commit; slow Neuro chạy qua continuation lane riêng;
        - autonomous có thể remote-deliberate khi ambiguity gắn với hệ quả vật chất/conflict;
        - mỗi blocking MindLoop session chỉ được mở tối đa một remote deliberation;
        - nếu deliberator không dùng được, fast gate vẫn là authority duy nhất.

        Không còn rollback sang LLM selector cũ và không có rebound/remap ID.
        """
        # V74 Life-Horizon chạy ngay trước Neuro competition. Nó chỉ sửa utility
        # của coalition đã có, giữ nguyên frame/coalition/proposal identity.
        try:
            manager = ensure_life_horizon_manager(self.owner)
            if manager.enabled:
                horizon_snapshot = manager.refresh_from_runtime(now=float(self._clock()))
                observation_context = dict(getattr(getattr(frame, "observation", None), "context", {}) or {})
                raw_runtime = observation_context.get("runtime_context")
                raw_runtime = dict(raw_runtime) if isinstance(raw_runtime, Mapping) else {}
                courtesy_wait = bool(raw_runtime.get("courtesy_hold") or raw_runtime.get("awaiting_user_answer"))
                frame = self._horizon_arbiter.adjust_frame(
                    frame, horizon_snapshot, courtesy_wait=courtesy_wait
                )
        except Exception as exc:
            self._log("debug", "[life-horizon-v74] arbiter degraded: %s", exc)

        autonomous_mode = str(mode or "").startswith("autonomous")
        # P2: V103 motor chỉ sở hữu *public expression* (speech/body/silence).
        # Goal adoption/review là quyết định công việc có cấu trúc của Subject;
        # không cho một lệnh SILENCE của expression motor phủ quyết Work Lane.
        goal_work_mode = str(mode or "").startswith("autonomous_goal")
        if autonomous_mode:
            # V95: lifecycle validity đứng *trước* utility competition. Một motive
            # stale/self-owned không được quyền sống sót chỉ vì pressure/score cao.
            # Validator chỉ loại coalition public-speech sai lifecycle; silence và
            # body_only luôn còn làm escape family cho Neuro.
            validation = validate_autonomous_motives(frame)
            if validation.changed:
                self._log(
                    "info",
                    "[semantic authority v95] motive_rejected count=%s reasons=%s",
                    len(validation.rejections),
                    [
                        {
                            "coalition": item.coalition_id,
                            "speech_act": item.speech_act,
                            "code": item.code,
                            "detail": item.detail,
                        }
                        for item in validation.rejections
                    ],
                )
                frame = validation.frame

        # V103 FINAL: normal autonomous propositions đã cut over sang continuous
        # motor. Neuro frame vẫn tồn tại để giữ typed contracts V93-V97, nhưng
        # motor trả SelectionResult one-hot theo action family đã commit; không
        # đọc coalition.score/softmax và không mời semantic critic vote winner.
        if autonomous_mode and not goal_work_mode:
            _nervous_authority = getattr(self.owner, "nervous_system_v103", None)
            _nervous_select = getattr(_nervous_authority, "selection_for_frame", None)
            if callable(_nervous_select):
                try:
                    _motor_selection = _nervous_select(frame)
                except Exception as exc:
                    _motor_selection = None
                    self._log("warning", "[nervous-v103] candidate bypass degraded: %s", exc)
                if _motor_selection is not None:
                    winner = _motor_selection.winner(frame)
                    # Semantic critic semantics mới: measurement -> modulation.
                    # Không re-rank candidate. Hard safety/grounding veto vẫn chạy
                    # downstream trước public commit như cũ.
                    if str(getattr(winner, "action_kind", "") or "").strip().lower() == "speak":
                        _modulate = getattr(_nervous_authority, "inject_semantic_modulation", None)
                        if callable(_modulate):
                            try:
                                _modulate(
                                    source_id=str(getattr(winner, "primary_proposal_ref", "") or ""),
                                    uncertainty=float(getattr(winner, "uncertainty", 0.5) or 0.5),
                                    epistemic_support=float(getattr(winner, "expected_epistemic_value", 0.5) or 0.5),
                                    hard_boundary=False,
                                    now=float(self._clock()),
                                )
                            except Exception as exc:
                                self._log("debug", "[semantic-modulation-v103] degraded: %s", exc)
                    from runtime.autonomy.authority_trace import emit as autonomy_trace
                    autonomy_trace("executive.v103_winner", mode=mode,
                                   frame_id=str(getattr(frame, "frame_id", "")),
                                   selection_source="v103")
                    self._log_neuro_selection("motor-authority", frame, _motor_selection, mode=mode)
                    commit = self._commit_from_selection(
                        frame,
                        _motor_selection,
                        reason=_motor_selection.reason,
                        source="nervous_motor_authority",
                        semantic_commitments=("motor_action_family_committed_before_subject_envelope",),
                        review_conditions=("hard_safety_and_grounding_remain_absolute",),
                    )
                    self._log_neuro_commit(frame, commit, mode=mode)
                    return commit

        selection = self._neuro_selector.select(frame)
        from runtime.autonomy.authority_trace import emit as autonomy_trace
        autonomy_trace("executive.neuro_fallback", mode=mode,
                       frame_id=str(getattr(frame, "frame_id", "")),
                       selection_source="neuro")
        self._log_neuro_selection("neuro-fast", frame, selection, mode=mode)

        # Autonomous social speech gets a slow semantic appraisal before public
        # commit when the fast winner wants to speak. The model does not vote; it
        # supplies bounded measurements and local Neuro re-ranks exactly once.
        semantic_review_needed = bool(
            autonomous_mode and self._autonomous_semantic_review_needed(frame, selection)
        )
        if semantic_review_needed and allow_remote_deliberation and self.neuro_deliberator_enabled:
            appraisal = self._appraise_autonomous_frame(
                frame, selection, snapshot=snapshot, mode=mode
            )
            if appraisal is not None:
                self._log(
                    "info",
                    "[executive semantic critic] mode=%s boundary_pressure=%.2f interruption_cost=%.2f confidence=%.2f choices=%s reason=%s",
                    mode,
                    appraisal.boundary_pressure,
                    appraisal.interruption_cost,
                    appraisal.confidence,
                    [
                        {
                            "index": item.index,
                            "motive_validity": round(item.motive_validity, 3),
                            "novelty": round(item.novelty, 3),
                            "continuity_fit": round(item.continuity_fit, 3),
                            "epistemic_support": round(item.epistemic_support, 3),
                            "reversibility": round(item.reversibility, 3),
                        }
                        for item in appraisal.choices
                    ],
                    appraisal.reason,
                )
                merged = self._neuro_selector.merge_semantic_appraisal(frame, selection, appraisal)
                self._log(
                    "info",
                    "[neuro-control] autonomous semantic merge fast=%s final=%s action=%s boundary=%.2f interrupt=%.2f margin=%.3f",
                    selection.winner_index, merged.winner_index, merged.winner(frame).action_kind,
                    appraisal.boundary_pressure, appraisal.interruption_cost, merged.margin,
                )
                self._log_neuro_selection(
                    "neuro-merge",
                    frame,
                    merged,
                    mode=mode,
                    extra={
                        "boundary_pressure": round(appraisal.boundary_pressure, 3),
                        "interruption_cost": round(appraisal.interruption_cost, 3),
                    },
                )
                commit = self._commit_from_selection(
                    frame,
                    merged,
                    reason=merged.reason,
                    source="neuro_parallel_semantic_merge",
                    semantic_commitments=appraisal.semantic_commitments,
                    cognitive_requests=appraisal.cognitive_requests,
                    confidence=appraisal.confidence,
                    review_conditions=appraisal.review_conditions,
                )
                self._log_neuro_commit(frame, commit, mode=mode)
                return commit
            # V42: remote semantic lane hỏng không được biến thành fail-open speak.
            # Local typed appraisal không giả ngôn ngữ; nó merge policy/evidence/
            # interruption hiện có rồi Neuro tự commit lại đúng một lần.
            local_appraisal = self._local_autonomous_semantic_appraisal(frame, selection)
            merged = self._neuro_selector.merge_semantic_appraisal(frame, selection, local_appraisal)
            self._log(
                "info",
                "[neuro-control] autonomous local semantic fallback fast=%s final=%s action=%s boundary=%.2f interrupt=%.2f",
                selection.winner_index, merged.winner_index, merged.winner(frame).action_kind,
                local_appraisal.boundary_pressure, local_appraisal.interruption_cost,
            )
            commit = self._commit_from_selection(
                frame,
                merged,
                reason=f"local_semantic_fallback:{merged.reason}",
                source="neuro_local_semantic_merge",
                semantic_commitments=local_appraisal.semantic_commitments,
                cognitive_requests=local_appraisal.cognitive_requests,
                confidence=local_appraisal.confidence,
                review_conditions=local_appraisal.review_conditions,
            )
            self._log_neuro_commit(frame, commit, mode=mode)
            return commit

        if not selection.requires_deliberation:
            commit = self._commit_from_selection(
                frame,
                selection,
                reason=selection.reason,
                source="neuro_fast_gate",
            )
            self._log(
                "info",
                "[neuro-control] fast commit mode=%s choice=%s action=%s margin=%.3f entropy=%.3f",
                mode,
                selection.winner_index,
                selection.winner(frame).action_kind,
                selection.margin,
                selection.entropy,
            )
            self._log_neuro_commit(frame, commit, mode=mode)
            return commit

        # Direct chat is availability-first: slow Neuro is never allowed to sit
        # on the public reply's critical path.  The interactive caller commits
        # this deterministic winner now; NeuroContinuationRuntime may review the
        # same frozen frame in parallel and later offer a cancellable follow-up.
        if str(mode or "").startswith("interactive"):
            commit = self._commit_from_selection(
                frame,
                selection,
                reason=f"parallel_review_deferred:{selection.reason}",
                source="neuro_fast_gate",
                review_conditions=("parallel_neuro_review",),
            )
            self._log_neuro_commit(frame, commit, mode=mode)
            return commit

        if not allow_remote_deliberation:
            # Cognitive review has already spent this session's remote-deliberation
            # budget. The revised frame is still re-scored, but the deterministic
            # Neuro gate owns the recommit instead of recursively asking the model.
            commit = self._commit_from_selection(
                frame,
                selection,
                reason=f"review_recommit_without_second_remote_round:{selection.reason}",
                source="neuro_fast_gate",
            )
            self._log_neuro_commit(frame, commit, mode=mode)
            return commit

        if not self.neuro_deliberator_enabled:
            # Không tạo một policy khẩn cấp thứ hai. Khi deliberator vắng mặt,
            # chính competition đã tạo ra frame này tiếp tục sở hữu quyết định.
            commit = self._fallback_subject_commit(frame, reason="neuro_deliberator_unavailable")
            self._log_neuro_commit(frame, commit, mode=mode)
            return commit

        self._log(
            "info",
            "[neuro-control] deliberation invited mode=%s reason=%s margin=%.3f entropy=%.3f",
            mode, selection.reason, selection.margin, selection.entropy,
        )
        commit = self._deliberate_subject_choice(
            frame,
            selection,
            user_text=user_text,
            snapshot=snapshot,
            mode=mode,
        )
        self._log_neuro_commit(frame, commit, mode=mode)
        return commit

    def _semantic_shortlist_interactive(
        self,
        user_text: str,
        *,
        snapshot: Mapping[str, Any] | None,
        index: Mapping[str, Any],
        candidates: Sequence[ExecutiveTool],
    ) -> tuple[ExecutiveTool, ...]:
        if len(candidates) <= 6:
            return tuple(candidates)
        decision = self._decide_interactive(
            user_text, snapshot=snapshot, stage="interactive_shortlist",
            capability_index=index, candidate_tools=candidates, offered_tools=(),
        )
        if decision is None or decision.action != "shortlist_capabilities":
            return ()
        return select_tools_by_id(candidates, decision.capability_ids, max_items=6)

    def _decide_interactive(
        self,
        user_text: str,
        *,
        snapshot: Mapping[str, Any] | None,
        stage: str,
        capability_index: Mapping[str, Any],
        candidate_tools: Sequence[ExecutiveTool],
        offered_tools: Sequence[ExecutiveTool],
    ) -> ExecutiveDecision | None:
        source = snapshot or {}
        payload = {
            "user_text": str(user_text or ""),
            "recent_public_history": list(source.get("recent_public_history", ()))[-8:],
            "relationship": source.get("relationship_summary", source.get("relationship", {})),
            "grounding_context": _compact(source.get("grounding_context", ""), 1800),
            "subject_decision": self._interactive_subject_brief(source.get("subject_decision", {})),
            "capability_index": dict(capability_index),
            "capability_candidates": [item.to_card() for item in candidate_tools],
            "offered_capabilities": [item.to_dict() for item in offered_tools],
            "contract": {
                "authority": "exact_capability_that_realizes_subject_executive_commit",
                "public_wording_owner": "deepseek_public_reply",
                "execution_owner": "runtime",
                "stage": stage,
            },
        }
        return self._invoke(stage=stage, payload=payload, priority=96, max_tokens=420)

    def materialize_subject_decision(
        self,
        subject_decision: MindDecision,
        affordances: Sequence[ExecutiveAffordance],
        *,
        subject_frame: SubjectDeliberationFrame | None = None,
    ) -> ExecutiveOutcome | None:
        """Đóng một MindDecision đã commit thành outcome runtime, không quyết định lại.

        V30 dùng cùng ``commit_subject_frame`` cho chat và self-turn. Method này
        chỉ materialize hành động đã được Subject+Neuro cam kết thành candidate
        mà InitiativeGovernor hiểu. Nó không được chọn coalition hay motive khác.

        ``None`` chỉ có nghĩa action cần capability execution riêng; caller có thể
        chạy execution planner nhưng phải giữ nguyên Subject decision.
        """
        action_kind = str(subject_decision.proposed_action or "silence").strip().lower()
        arguments = dict(subject_decision.arguments or {})
        coalition = None
        if subject_frame is not None:
            coalition = next(
                (item for item in subject_frame.coalitions if item.coalition_id == subject_decision.coalition_id),
                None,
            )
        proposal_ref = str(getattr(coalition, "primary_proposal_ref", "") or "")
        commit_trace = arguments.get("executive_commit") if isinstance(arguments.get("executive_commit"), Mapping) else {}
        reason = _compact(commit_trace.get("reason", "") or "subject_decision_materialized", 360)
        confidence = _clamp01(commit_trace.get("confidence", 0.55), 0.55)
        semantic_target = _compact(arguments.get("semantic_target", ""), 1600)
        speech_act = _compact(arguments.get("speech_act", ""), 100).lower()
        tone = _compact(arguments.get("tone", ""), 100).lower()
        expression_commitment = read_commitment(arguments)

        base = ExecutiveDecision(
            action="silence",
            semantic_target=semantic_target,
            speech_act=speech_act,
            tone=tone,
            phase="subject_committed",
            reason=reason,
            confidence=confidence,
            subject_coalition_id=subject_decision.coalition_id,
            subject_proposal_ref=proposal_ref,
            semantic_commitments=tuple(
                str(item) for item in commit_trace.get("semantic_commitments", ())
                if str(item or "").strip()
            )[:8],
        )

        if action_kind == "silence":
            decision = replace(base, action="silence")
            return ExecutiveOutcome(None, "", "silence", reason, decision)
        if action_kind == "body_only":
            decision = replace(base, action="body_only")
            return ExecutiveOutcome(None, "", "body_only", reason, decision)
        if action_kind != "speak":
            return None
        if expression_commitment is None:
            unavailable = replace(base, action="silence", reason="speak_without_expression_commitment")
            return ExecutiveOutcome(None, "", "silence", unavailable.reason, unavailable)
        if bool(arguments.get("capability_route", False)):
            return None

        options = tuple(
            item for item in affordances
            if isinstance(item, ExecutiveAffordance) and item.available
        )
        wanted = str(arguments.get("affordance_id", "") or "").strip()
        selected = next((item for item in options if item.affordance_id == wanted), None)
        if selected is not None:
            decision = replace(
                base,
                action="select_affordance",
                affordance_id=selected.affordance_id,
                intrusion_risk=_clamp01(getattr(selected.candidate, "seed_intrusion_risk", 0.0)),
            )
            return ExecutiveOutcome(
                self._rewrite_candidate(selected.candidate, decision),
                selected.affordance_id,
                "use_candidate",
                reason,
                decision,
            )

        # Subject-owned initiative có thể được commit từ pending intention ngay cả
        # khi affordance preview hết TTL giữa prepare và materialize. Tạo candidate
        # từ đúng semantic target đã commit, không chọn topic mới.
        if semantic_target and speech_act:
            from .social_candidate import SocialSpeechCandidate
            refs = tuple(str(item) for item in subject_decision.proposal_refs if str(item or "").strip())[:5]
            candidate = SocialSpeechCandidate(
                decision="candidate",
                kind="social_check_in",
                topic=semantic_target,
                speech_act=speech_act,
                anchor_type="internal",
                anchor_id=str(arguments.get("intention_id") or subject_decision.decision_id),
                evidence_refs=refs or (subject_decision.observation_ref,),
                requires_grounding=False,
                confidence=confidence,
                relevance=max(0.30, _clamp01(subject_decision.expected_pragmatic_value)),
                urgency=max(0.18, _clamp01(subject_decision.expected_epistemic_value)),
                tone=tone or "casual_warm",
                reason=f"subject_committed:{arguments.get('initiative_motive', 'initiative')}",
                source="subject_initiative_committed",
                eris_motive_kind=str(arguments.get("initiative_motive") or arguments.get("expression_motive") or "initiative"),
                eris_motive_why_now="Subject Core and Neuro Control committed a contentful intention",
                personal_stake=_clamp01(subject_decision.expected_pragmatic_value),
                authenticity=1.0,
                uncertainty=subject_decision.uncertainty,
                seed_intrusion_risk=0.12,
                face_risk=0.12,
                recovery_available=True,
                expression_urge_id=str(arguments.get("expression_urge_id") or ""),
                expression_pressure=_clamp01(arguments.get("expression_pressure", 0.0)),
                expression_freshness=_clamp01(arguments.get("expression_freshness", 0.0)),
                expression_satisfaction=_clamp01(arguments.get("expression_satisfaction", 0.0)),
                expression_motive=str(arguments.get("expression_motive") or ""),
                expression_commitment=expression_commitment.to_dict(),
            )
            decision = replace(base, action="select_affordance", affordance_id=wanted)
            return ExecutiveOutcome(candidate, wanted, "use_candidate", reason, decision)
        return None

    def deliberate(
        self,
        blackboard: Any,
        affordances: Sequence[ExecutiveAffordance],
        *,
        snapshot: Mapping[str, Any] | None = None,
        subject_frame: SubjectDeliberationFrame | None = None,
    ) -> ExecutiveOutcome:
        """Lập kế hoạch capability *sau* khi Subject đã commit motive.

        V30 không chọn hành động lần hai ở đây. Caller chỉ được đi vào method này
        cho một capability-route đã commit và frame đã được thu hẹp về đúng coalition
        đó. Nếu boundary không thỏa invariant, fail closed thay vì tự rerank.
        """
        options = tuple(item for item in affordances if isinstance(item, ExecutiveAffordance) and item.available)

        if subject_frame is None or len(subject_frame.coalitions) != 1:
            decision = ExecutiveDecision.unavailable("capability_planner_requires_one_committed_coalition")
            return ExecutiveOutcome(None, "", "silence", decision.reason, decision)

        committed = subject_frame.coalitions[0]
        committed_args = dict(getattr(committed, "arguments", {}) or {})
        expression_commitment = read_commitment(committed_args)
        if expression_commitment is None:
            decision = ExecutiveDecision.unavailable("capability_route_without_expression_commitment")
            return ExecutiveOutcome(None, "", "silence", decision.reason, decision)
        if not bool(committed_args.get("capability_route", False)):
            decision = ExecutiveDecision.unavailable("capability_planner_without_committed_route")
            return ExecutiveOutcome(None, "", "silence", decision.reason, decision)

        if not self.capability_planner_enabled:
            # Capability execution cần model planner để chọn API cụ thể. Mất planner
            # thì im/fail closed; tuyệt đối không dùng nó như cớ để chọn motive khác.
            decision = ExecutiveDecision.unavailable("capability_planner_unavailable")
            return ExecutiveOutcome(None, "", "silence", decision.reason, decision)

        capability_index = build_capability_index(self.owner, refresh=True)
        decision = self._decide(
            blackboard, options, snapshot=snapshot, subject_frame=subject_frame, stage="initial",
            capability_index=capability_index.to_dict(), candidate_tools=(), offered_tools=(), capability_result=None,
        )
        if decision is None:
            unavailable = ExecutiveDecision.unavailable("capability_planner_model_or_schema_failure")
            return ExecutiveOutcome(None, "", "silence", unavailable.reason, unavailable)

        offered: tuple[ExecutiveTool, ...] = ()
        if decision.action == "discover_capabilities":
            candidates = self._candidate_capabilities(
                required_effect=decision.required_effect,
                maximum_risk=decision.maximum_risk,
                domain=decision.capability_domain,
            )
            offered = self._semantic_shortlist(
                blackboard, options, snapshot=snapshot, subject_frame=subject_frame,
                capability_index=capability_index.to_dict(), candidates=candidates,
            )
            if not offered:
                silent = replace(decision, action="silence", reason="no_matching_live_capability")
                return ExecutiveOutcome(None, "", "silence", silent.reason, silent)
            decision = self._decide(
                blackboard, options, snapshot=snapshot, subject_frame=subject_frame, stage="capabilities",
                capability_index=capability_index.to_dict(), candidate_tools=(), offered_tools=offered, capability_result=None,
            )
            if decision is None:
                unavailable = ExecutiveDecision.unavailable("capability_planner_selection_failure")
                return ExecutiveOutcome(None, "", "silence", unavailable.reason, unavailable)

        if decision.action == "invoke_capability":
            result = self._execute_exact_capability(decision, offered)
            if result is None:
                rejected = replace(decision, action="silence", reason="capability_planner_rejected_execution")
                return ExecutiveOutcome(None, "", "silence", rejected.reason, rejected)
            decision = self._decide(
                blackboard, options, snapshot=snapshot, subject_frame=subject_frame, stage="result",
                capability_index=capability_index.to_dict(), candidate_tools=(), offered_tools=offered,
                capability_result=result,
            )
            if decision is None:
                unavailable = ExecutiveDecision.unavailable("capability_planner_result_failure")
                return ExecutiveOutcome(None, "", "silence", unavailable.reason, unavailable)
            decision = replace(decision, capability_result=result)
        if decision.action == "speak_from_capability":
            candidate = self._candidate_from_capability(
                decision,
                expression_commitment=expression_commitment.to_dict(),
            )
            if candidate is None:
                silent = replace(decision, action="silence", reason="capability_result_not_grounded")
                return ExecutiveOutcome(None, "", "silence", silent.reason, silent)
            return ExecutiveOutcome(candidate, "", "use_candidate", decision.reason, decision)

        # Capability planner không có đường quay lại chọn affordance/social action.
        # Mọi action chưa kết thúc bằng grounded capability speech đều fail closed.
        return ExecutiveOutcome(None, "", "silence", decision.reason, replace(decision, action="silence"))

    def _semantic_shortlist(
        self,
        blackboard: Any,
        affordances: Sequence[ExecutiveAffordance],
        *,
        snapshot: Mapping[str, Any] | None,
        subject_frame: SubjectDeliberationFrame | None,
        capability_index: Mapping[str, Any],
        candidates: Sequence[ExecutiveTool],
    ) -> tuple[ExecutiveTool, ...]:
        if len(candidates) <= 6:
            return tuple(candidates)
        decision = self._decide(
            blackboard, affordances, snapshot=snapshot, subject_frame=subject_frame, stage="capability_shortlist",
            capability_index=capability_index, candidate_tools=candidates, offered_tools=(), capability_result=None,
        )
        if decision is None or decision.action != "shortlist_capabilities":
            return ()
        return select_tools_by_id(candidates, decision.capability_ids, max_items=6)

    @staticmethod
    def _subject_capability_brief(frame: SubjectDeliberationFrame | None) -> dict[str, Any]:
        """Project a committed capability motive without cognitive runtime IDs.

        Capability names are stable executable symbols and may cross the model
        boundary. Coalition/proposal IDs are ephemeral identities and may not.
        """
        if frame is None or not frame.coalitions:
            return {}
        # Capability path chỉ chạy sau khi Subject Core đã commit; caller truyền
        # frame đã thu hẹp về đúng coalition đó. Không rerank ở boundary này.
        candidate = frame.coalitions[0]
        arguments = dict(getattr(candidate, "arguments", {}) or {})
        safe_keys = {
            "semantic_target", "speech_act", "tone", "capability_route",
            "capability_goal", "required_effect", "maximum_risk",
            "capability_domain", "initiative_motive", "context",
        }
        return {
            "action_kind": str(getattr(candidate, "action_kind", "") or ""),
            "arguments": {key: arguments[key] for key in safe_keys if key in arguments},
            "expected_pragmatic_value": float(getattr(candidate, "expected_pragmatic_value", 0.0) or 0.0),
            "expected_epistemic_value": float(getattr(candidate, "expected_epistemic_value", 0.0) or 0.0),
            "uncertainty": float(getattr(candidate, "uncertainty", 0.0) or 0.0),
        }

    @staticmethod
    def _interactive_subject_brief(value: Any) -> dict[str, Any]:
        source = dict(value) if isinstance(value, Mapping) else {}
        arguments = dict(source.get("arguments", {}) or {}) if isinstance(source.get("arguments", {}), Mapping) else {}
        safe_keys = {
            "semantic_target", "speech_act", "tone", "capability_route",
            "capability_goal", "required_effect", "maximum_risk",
            "capability_domain", "initiative_motive", "context",
        }
        return {
            "proposed_action": str(source.get("proposed_action", "") or ""),
            "arguments": {key: arguments[key] for key in safe_keys if key in arguments},
            "expected_pragmatic_value": _clamp01(source.get("expected_pragmatic_value", 0.0)),
            "expected_epistemic_value": _clamp01(source.get("expected_epistemic_value", 0.0)),
            "uncertainty": _clamp01(source.get("uncertainty", 0.0)),
        }

    def _decide(
        self,
        blackboard: Any,
        affordances: Sequence[ExecutiveAffordance],
        *,
        snapshot: Mapping[str, Any] | None,
        subject_frame: SubjectDeliberationFrame | None,
        stage: str,
        capability_index: Mapping[str, Any],
        candidate_tools: Sequence[ExecutiveTool],
        offered_tools: Sequence[ExecutiveTool],
        capability_result: Mapping[str, Any] | None,
    ) -> ExecutiveDecision | None:
        public_view = blackboard.public_view() if callable(getattr(blackboard, "public_view", None)) else {}
        source = snapshot or {}
        payload = {
            "blackboard": public_view,
            # Affordance runtime không được đi qua capability-planner boundary.
            # Motive/action đã được Subject+Neuro commit trước khi tới đây.
            "committed_subject_context": self._subject_capability_brief(subject_frame),
            "recent_public_history": list(source.get("recent_public_history", ()))[-8:],
            "deepseek_lane_digests": {
                "observer": source.get("observer_frame", source.get("last_observer_frame", {})),
                "inner": source.get("self_context", {}),
                "relationship": source.get("relationship", {}),
                "recent_self_turn_semantics": list(source.get("recent_self_turn_semantics", ()))[-6:],
                "shared_semantic_context": source.get("shared_semantic_context", {}),
            },
            "capability_index": dict(capability_index),
            "capability_candidates": [item.to_card() for item in candidate_tools],
            "offered_capabilities": [item.to_dict() for item in offered_tools],
            "verified_capability_result": dict(capability_result or {}),
            "contract": {
                "authority": "exact_capability_for_committed_subject_goal",
                "surface_owner": "deepseek_surface",
                "runtime_owner": "availability_permission_argument_validation_execution_concurrency",
                "stage": stage,
            },
        }
        return self._invoke(stage=stage, payload=payload, priority=58, max_tokens=500)

    def _invoke(self, *, stage: str, payload: Mapping[str, Any], priority: int, max_tokens: int) -> ExecutiveDecision | None:
        try:
            raw = self.invoke_structured(
                self.client,
                system=self._system_prompt(stage=stage),
                payload=payload,
                schema=EXECUTIVE_SCHEMA,
                name=f"eris_capability_planner_{stage}",
                max_tokens=max_tokens,
                reasoning_effort="medium",
                inference_tier="main_cognition",
                priority=priority,
                timeout_seconds=float(os.getenv("ERIS_EXECUTIVE_TIMEOUT_S", "18") or 18),
            )
        except Exception as exc:
            self._log("warning", "[capability-planner] model call failed stage=%s: %s", stage, exc)
            return None
        decision = ExecutiveDecision.parse(raw)
        if decision is None:
            self._log("warning", "[capability-planner] invalid response stage=%s raw=%r", stage, raw)
            return None
        allowed = _STAGE_MODEL_ACTIONS.get(stage, set())
        if decision.action not in allowed:
            # Schema chung giữ payload nhỏ, còn invariant theo stage được runtime khóa cứng.
            # Model không thể dùng một action hợp schema nhưng sai quyền hạn của stage.
            self._log(
                "warning",
                "[capability-planner] rejected cross-stage action stage=%s action=%s allowed=%s",
                stage, decision.action, sorted(allowed),
            )
            return None
        self._log(
            "info",
            "[capability-planner] stage=%s action=%s affordance=%s capability=%s confidence=%.2f risk=%.2f reason=%s",
            stage, decision.action, decision.affordance_id or "none", decision.capability_id or "none",
            decision.confidence, decision.intrusion_risk, decision.reason,
        )
        return decision

    def _execute_exact_capability(
        self,
        decision: ExecutiveDecision,
        offered: Sequence[ExecutiveTool],
    ) -> Mapping[str, Any] | None:
        tool = catalog_index(offered).get(decision.capability_id)
        if tool is None or not tool.available or not tool.auto_callable:
            self._log(
                "warning", "[capability-planner] rejected capability=%s offered=%s auto_callable=%s",
                decision.capability_id, bool(tool), bool(tool and tool.auto_callable),
            )
            return None

        arguments = dict(decision.capability_arguments)
        valid, reason = validate_capability_arguments(tool.capability_id, arguments)
        if not valid:
            valid, reason = validate_arguments_against_schema(
                arguments, tool.input_schema, capability_id=tool.capability_id
            )
        if not valid:
            self._log("warning", "[executive] invalid arguments capability=%s reason=%s", tool.capability_id, reason)
            return None

        executor = getattr(self.owner, "action_executor", None)
        if executor is None:
            return None
        try:
            execute_typed = getattr(executor, "execute_capability", None)
            if not callable(execute_typed):
                self._log("warning", "[executive] typed execution boundary unavailable")
                return None
            result = execute_typed(tool.capability_id, arguments)
        except Exception as exc:
            self._log("warning", "[executive] capability=%s failed: %s", tool.capability_id, exc)
            return None
        recorder = getattr(self.owner, "_record_tool_grounding", None)
        if callable(recorder):
            try:
                recorder(result, action_name=tool.capability_id)
            except Exception:
                pass
        return {
            "capability_id": tool.capability_id,
            "arguments": arguments,
            "effect": tool.effect,
            "success": bool(getattr(result, "success", False)),
            "observed": bool(getattr(result, "observed", False)),
            "status": str(getattr(getattr(result, "status", ""), "value", getattr(result, "status", "")) or ""),
            "message": _compact(getattr(result, "message", ""), 1600),
            "data": dict(getattr(result, "data", {}) or {}),
            "evidence": list(getattr(result, "evidence", ()) or ()),
            "error_kind": str(getattr(result, "error_kind", "") or ""),
        }

    def materialize_candidate(self, candidate: Any, decision: ExecutiveDecision) -> Any:
        return self._rewrite_candidate(candidate, decision)

    @staticmethod
    def _rewrite_candidate(candidate: Any, decision: ExecutiveDecision) -> Any:
        changes: dict[str, Any] = {}
        if decision.semantic_target:
            changes["topic"] = decision.semantic_target
        if decision.speech_act:
            changes["speech_act"] = decision.speech_act
        if decision.tone:
            changes["tone"] = decision.tone
        authority = "neuro_control" if decision.phase == "neuro_action_gate" else "capability_planner"
        changes["reason"] = _compact(
            f"{authority}:{decision.reason}; upstream={getattr(candidate, 'reason', '')}", 300
        )
        changes["source"] = f"{authority}:{getattr(candidate, 'source', 'affordance')}"
        try:
            return replace(candidate, **changes)
        except Exception:
            return candidate

    @staticmethod
    def _candidate_from_capability(
        decision: ExecutiveDecision,
        *,
        expression_commitment: Mapping[str, Any],
    ) -> Any | None:
        result = decision.capability_result or {}
        if not bool(result.get("success")) or not bool(result.get("observed")):
            return None
        target = decision.semantic_target or str(result.get("message", "") or "")
        if not target:
            return None
        try:
            from .social_candidate import SocialSpeechCandidate

            capability_id = str(result.get("capability_id", "") or "runtime_capability")
            return SocialSpeechCandidate(
                decision="candidate",
                kind="grounded_tool_finding",
                topic=target,
                speech_act=decision.speech_act or "share_grounded_runtime_finding",
                anchor_type="runtime_capability",
                anchor_id=f"capability:{capability_id}",
                evidence_refs=tuple(str(item) for item in result.get("evidence", ()) if str(item or "").strip()),
                requires_grounding=False,
                confidence=max(0.45, decision.confidence),
                relevance=max(0.45, 1.0 - decision.intrusion_risk),
                urgency=0.2,
                tone=decision.tone or "matter_of_fact_warm",
                reason=f"capability_planner:verified_capability:{capability_id}:{decision.reason}",
                source="capability_planner",
                expression_commitment=dict(expression_commitment),
            )
        except Exception:
            return None

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


__all__ = [
    "EXECUTIVE_SCHEMA",
    "ExecutiveCortex",
    "ExecutiveDecision",
    "ExecutiveOutcome",
    "InteractiveExecutiveDirective",
]

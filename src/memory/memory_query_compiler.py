from __future__ import annotations
from core.di import resolve_dependency

"""
memory_query_compiler.py

Compiler chính:
User text
  -> SlotParser
  -> SemanticHintEngine
  -> RepairPrior
  -> LLM structured parser nếu ambiguity cao
  -> MemoryQueryIR tốt nhất

Compiler không execute memory.
Compiler không trả lời user.
"""

from dataclasses import dataclass
from typing import Optional

from .memory_query_ir import (
    ActRef,
    MemoryQueryIR,
    MemoryRelation,
    MemorySelector,
    QueryHypothesis,
    QueryIntent,
    RelationRef,
    RoleRef,
    SurfaceMode,
    clamp01,
)
from .memory_query_slot_parser import MemoryQuerySlotParser, user_is_declaring_or_updating_fact
from .memory_query_semantics import MemoryQuerySemanticHintEngine, SemanticQueryHint
from .memory_query_llm_parser import MemoryQueryLLMParser
from .memory_query_repair import MemoryQueryRepairStore
from .predicate_registry import PredicateRegistry, get_default_registry


@dataclass(slots=True)
class CompileDebug:
    slot_hypotheses: int
    semantic_confidence: float
    semantic_ambiguity: float
    llm_called: bool
    llm_reason: str
    repair_applied: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(slots=True)
class MemoryQueryCompileResult:
    best: Optional[MemoryQueryIR]
    hypotheses: tuple[QueryHypothesis, ...]
    debug: CompileDebug

    @property
    def has_query(self) -> bool:
        return self.best is not None and self.best.intent != QueryIntent.UNKNOWN


class MemoryQueryCompiler:
    """Blend slot + semantic + repair + optional LLM parser thành IR."""

    def __init__(
        self,
        *,
        slot_parser: Optional[MemoryQuerySlotParser] = None,
        semantic_engine: Optional[MemoryQuerySemanticHintEngine] = None,
        llm_parser: Optional[MemoryQueryLLMParser] = None,
        repair_store: Optional[MemoryQueryRepairStore] = None,
        registry: PredicateRegistry | None = None,
    ) -> None:
        self.registry = registry or get_default_registry()
        self.slot_parser = resolve_dependency(slot_parser, lambda: MemoryQuerySlotParser(registry=self.registry))
        if semantic_engine is None:
            provider = None
            try:
                from .embedding_index import get_shared_embedding_index

                provider = get_shared_embedding_index()
            except Exception:
                # Query compiler vẫn chạy lexical nếu dependency/model chưa sẵn.
                provider = None
            semantic_engine = MemoryQuerySemanticHintEngine(
                embedding_provider=provider,
                registry=self.registry,
            )
        self.semantic_engine = semantic_engine
        self.llm_parser = resolve_dependency(llm_parser, lambda: MemoryQueryLLMParser())
        self.repair_store = resolve_dependency(repair_store, lambda: MemoryQueryRepairStore())

    def compile(self, text: str) -> MemoryQueryCompileResult:
        slot_result = self.slot_parser.parse(text)
        semantic_hint = self.semantic_engine.analyze(text)
        notes: list[str] = []

        if slot_result.has_self_disclosure or user_is_declaring_or_updating_fact(text):
            debug = CompileDebug(
                slot_hypotheses=len(slot_result.hypotheses),
                semantic_confidence=semantic_hint.confidence,
                semantic_ambiguity=semantic_hint.ambiguity,
                llm_called=False,
                llm_reason="fact_disclosure_not_recall",
                repair_applied=(),
                notes=("fact_disclosure_not_recall",),
            )
            return MemoryQueryCompileResult(best=None, hypotheses=(), debug=debug)

        hypotheses = list(slot_result.hypotheses)
        hypotheses = self._inject_semantic_hypotheses(text, hypotheses, semantic_hint)
        hypotheses = self._apply_semantic_scores(hypotheses, semantic_hint)
        hypotheses = sorted((h.normalized() for h in hypotheses), key=lambda h: h.score, reverse=True)

        repair_result = self.repair_store.apply_prior(user_text=text, hypotheses=hypotheses)
        hypotheses = list(repair_result.hypotheses)

        ambiguity = self._ambiguity(hypotheses, semantic_hint)
        top_margin = self._top_margin(hypotheses)
        has_nested_relation = any(
            h.ir.relation.relation in {RelationRef.ANSWER_TO, RelationRef.AFTER, RelationRef.BEFORE}
            for h in hypotheses[:2]
        )

        llm_decision = self.llm_parser.should_use_llm(
            ambiguity=ambiguity,
            top_margin=top_margin,
            has_nested_relation=has_nested_relation,
        )
        llm_called = False
        if llm_decision.should_call:
            llm_ir = self.llm_parser.parse(text)
            llm_called = llm_ir is not None
            if llm_ir:
                hypotheses.append(
                    QueryHypothesis(
                        ir=llm_ir,
                        score=llm_ir.confidence,
                        source="llm_structured_parser",
                        reasons=("llm_parse",),
                    ).normalized()
                )
                hypotheses.sort(key=lambda h: h.score, reverse=True)

        best = hypotheses[0].ir if hypotheses else None
        if best:
            # Update ambiguity dựa trên margin cuối cùng.
            best = MemoryQueryIR(
                intent=best.intent,
                target=best.target,
                relation=best.relation,
                surface=best.surface,
                confidence=best.confidence,
                ambiguity=self._ambiguity(hypotheses, semantic_hint),
                intercept_permission=best.intercept_permission,
                evidence=best.evidence,
                warnings=best.warnings,
                raw_text=best.raw_text,
            )
            suppression_note = self._suppression_note(hypotheses, semantic_hint, best.ambiguity)
            if suppression_note:
                notes.append(suppression_note)
                best = None

        debug = CompileDebug(
            slot_hypotheses=len(slot_result.hypotheses),
            semantic_confidence=semantic_hint.confidence,
            semantic_ambiguity=semantic_hint.ambiguity,
            llm_called=llm_called,
            llm_reason=llm_decision.reason,
            repair_applied=repair_result.applied_items,
            notes=tuple((*repair_result.notes, *notes)),
        )

        return MemoryQueryCompileResult(
            best=best,
            hypotheses=tuple(hypotheses),
            debug=debug,
        )

    # ------------------------------------------------------------------
    # Semantic blend
    # ------------------------------------------------------------------

    def _inject_semantic_hypotheses(
        self,
        text: str,
        hypotheses: list[QueryHypothesis],
        hint: SemanticQueryHint,
    ) -> list[QueryHypothesis]:
        """
        Nếu slot parser không sinh được gì, semantic hint có thể sinh hypothesis mềm.

        Semantic không được execute exact ordinal. Nếu không có ordinal/quantity, surface
        sẽ nghiêng GIST hoặc fact lookup.
        """
        if hypotheses:
            return hypotheses

        top_intent = hint.top_intent()
        if not top_intent or top_intent[1] < 0.32:
            return hypotheses

        intent, intent_score = top_intent
        role = _best_or(hint.role_scores, RoleRef.ANY)
        act = _best_or(hint.act_scores, ActRef.ANY)
        relation = _best_or(hint.relation_scores, RelationRef.SELF)
        surface = _best_or(hint.surface_scores, SurfaceMode.GIST)

        if intent == QueryIntent.RECALL_FACT:
            fact = hint.top_fact_key()
            if not fact:
                return hypotheses
            fact_key, fact_score = fact
            conf = min(intent_score, fact_score)
            ir = MemoryQueryIR(
                intent=QueryIntent.RECALL_FACT,
                target=MemorySelector(role=RoleRef.USER, act=ActRef.FACT_DISCLOSURE, fact_key=fact_key),
                surface=SurfaceMode.EXACT,
                confidence=conf,
                ambiguity=1.0 - conf,
                intercept_permission=False,
                evidence=("semantic:fact_key", *hint.evidence),
                raw_text=text,
            )
            hypotheses.append(QueryHypothesis(ir=ir, score=conf, source="semantic_hint", reasons=("semantic_fact",)))
            return hypotheses

        if intent in {QueryIntent.RECALL_TURN, QueryIntent.RECALL_SUMMARY, QueryIntent.RECALL_COUNT}:
            conf = min(intent_score, hint.confidence)
            ir = MemoryQueryIR(
                intent=intent,
                target=MemorySelector(role=role, act=act),
                relation=MemoryRelation(relation),
                surface=surface,
                confidence=conf,
                ambiguity=hint.ambiguity,
                intercept_permission=False,
                evidence=("semantic:generic_query", *hint.evidence),
                warnings=("semantic_hint_no_exact_anchor",),
                raw_text=text,
            )
            hypotheses.append(QueryHypothesis(ir=ir, score=conf, source="semantic_hint", reasons=("semantic_generic",)))

        return hypotheses

    def _apply_semantic_scores(
        self,
        hypotheses: list[QueryHypothesis],
        hint: SemanticQueryHint,
    ) -> list[QueryHypothesis]:
        if not hypotheses or hint.confidence <= 0.0:
            return hypotheses

        out: list[QueryHypothesis] = []
        for hypo in hypotheses:
            ir = hypo.ir
            score = hypo.score
            reasons = list(hypo.reasons)
            penalties = list(hypo.penalties)

            # Boost khi semantic hint đồng ý với intent/role/act/relation.
            agreement = 0.0
            agreement += hint.intent_scores.get(ir.intent, 0.0) * 0.20
            agreement += hint.role_scores.get(ir.target.role, 0.0) * 0.12
            agreement += hint.act_scores.get(ir.target.act, 0.0) * 0.12
            agreement += hint.relation_scores.get(ir.relation.relation, 0.0) * 0.12
            if ir.target.fact_key:
                agreement += hint.fact_key_scores.get(ir.target.fact_key, 0.0) * 0.22

            if agreement > 0:
                score = clamp01(score + agreement)
                reasons.append(f"semantic_agreement:{agreement:.2f}")

            # Penalty nhẹ nếu semantic mạnh nhưng hypothesis đi ngược target rõ.
            if hint.confidence > 0.55:
                role_top = _best_or(hint.role_scores, None)
                act_top = _best_or(hint.act_scores, None)
                if role_top and ir.target.role != RoleRef.ANY and role_top != ir.target.role:
                    score = clamp01(score - 0.06)
                    penalties.append("semantic_role_disagreement")
                if act_top and ir.target.act != ActRef.ANY and act_top != ir.target.act:
                    score = clamp01(score - 0.06)
                    penalties.append("semantic_act_disagreement")

            updated_ir = MemoryQueryIR(
                intent=ir.intent,
                target=ir.target,
                relation=ir.relation,
                surface=ir.surface,
                confidence=clamp01(max(ir.confidence, score * 0.94)),
                ambiguity=ir.ambiguity,
                intercept_permission=ir.intercept_permission,
                evidence=tuple((*ir.evidence, *hint.evidence[:4])),
                warnings=ir.warnings,
                raw_text=ir.raw_text,
            )
            out.append(
                QueryHypothesis(
                    ir=updated_ir,
                    score=score,
                    source=hypo.source,
                    reasons=tuple(reasons),
                    penalties=tuple(penalties),
                )
            )
        return out

    # ------------------------------------------------------------------
    # Ranking metrics
    # ------------------------------------------------------------------

    def _top_margin(self, hypotheses: list[QueryHypothesis]) -> float:
        if len(hypotheses) < 2:
            return 1.0 if hypotheses else 0.0
        return max(0.0, hypotheses[0].score - hypotheses[1].score)

    def _ambiguity(self, hypotheses: list[QueryHypothesis], hint: SemanticQueryHint) -> float:
        if not hypotheses:
            return 1.0
        margin = self._top_margin(hypotheses)
        best = hypotheses[0].score
        structural = 1.0 - best
        margin_ambiguity = 1.0 - min(1.0, margin * 2.5)
        ambiguity = clamp01(structural * 0.55 + margin_ambiguity * 0.30 + hint.ambiguity * 0.15)
        top = hypotheses[0]
        if top.source == "semantic_hint":
            ambiguity = max(ambiguity, hint.ambiguity)
        if "generic_slot_query" in set(top.ir.warnings):
            ambiguity = max(ambiguity, clamp01(hint.ambiguity * 0.85 + 0.12))
        return clamp01(ambiguity)

    def _suppression_note(
        self,
        hypotheses: list[QueryHypothesis],
        hint: SemanticQueryHint,
        ambiguity: float,
    ) -> str | None:
        if not hypotheses:
            return None
        top = hypotheses[0]
        if top.ir.intent not in {
            QueryIntent.RECALL_TURN,
            QueryIntent.RECALL_FACT,
            QueryIntent.RECALL_COUNT,
            QueryIntent.RECALL_SUMMARY,
        }:
            return None
        warnings = set(top.ir.warnings)
        if top.source == "semantic_hint" and hint.ambiguity >= 0.78:
            return "semantic_recall_ambiguity_too_high"
        if "generic_slot_query" in warnings and ambiguity >= 0.70:
            return "generic_recall_ambiguity_too_high"
        if ambiguity >= 0.78:
            return "recall_ambiguity_too_high"
        return None


def _best_or(scores: dict, default):
    if not scores:
        return default
    return max(scores.items(), key=lambda item: item[1])[0]

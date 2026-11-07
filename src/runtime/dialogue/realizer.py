# -*- coding: utf-8 -*-
"""Natural dialogue realization constrained by semantic commitments."""
from __future__ import annotations

import re
import uuid
from typing import AsyncIterator, Callable

from .contracts import DialoguePlan, DiscourseState, RollingThoughtBuffer, SocialRiskPolicy, SpeechChunk


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


class DialogueContractViolation(ValueError):
    pass


class NaturalDialogueRealizer:
    def __init__(self, id_factory: Callable[[], str] | None = None) -> None:
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    @staticmethod
    def _word_count(text: str) -> int:
        return len([token for token in text.split() if token])

    def _material(self, plan: DialoguePlan, thoughts: RollingThoughtBuffer, social_policy: SocialRiskPolicy) -> list[str]:
        material = [item.strip() for item in plan.semantic_commitments if item.strip()]
        if social_policy.relationship_license >= 0.5:
            material.extend(item.strip() for item in plan.optional_material if item.strip())
        eligible_keys = {candidate.continuity_key for candidate in thoughts.candidates if candidate.public_eligibility}
        for hook in plan.continuation_hooks:
            if hook in eligible_keys and hook not in material:
                # A continuation hook may select an existing commitment, never
                # invent a factual statement from a hidden thought.
                match = next((item.semantic_content for item in thoughts.candidates if item.continuity_key == hook and item.public_eligibility), "")
                if match and match in plan.semantic_commitments:
                    material.append(match)
        return material

    async def realize(
        self,
        *,
        plan: DialoguePlan,
        discourse: DiscourseState,
        thoughts: RollingThoughtBuffer,
        social_policy: SocialRiskPolicy,
    ) -> AsyncIterator[SpeechChunk]:
        del discourse
        if plan.speech_act in {"silence", "nonverbal"}:
            return
        material = self._material(plan, thoughts, social_policy)
        text = " ".join(material).strip()
        for forbidden in plan.forbidden_factual_additions:
            if forbidden and forbidden.lower() in text.lower():
                raise DialogueContractViolation(f"forbidden factual addition present: {forbidden}")
        if self._word_count(text) > plan.hard_max_words:
            raise DialogueContractViolation("realized text exceeds hard_max_words")
        # Every emitted token originates from commitments or approved optional material.
        allowed_text = " ".join((*plan.semantic_commitments, *plan.optional_material))
        if text and text not in allowed_text:
            raise DialogueContractViolation("realizer added material outside plan")
        clauses = [clause.strip() for clause in _SENTENCE_SPLIT.split(text) if clause.strip()] or ([text] if text else [])
        for sequence, clause in enumerate(clauses):
            yield SpeechChunk(
                chunk_id=self._id_factory(),
                plan_id=plan.plan_id,
                sequence=sequence,
                text=clause,
                clause_complete=True,
                may_start_tts=True,
                prosody_hint="measured" if social_policy.willingness_to_be_vulnerable < 0.5 else "open",
                body_cue_refs=plan.performance_intent.body_cues,
            )

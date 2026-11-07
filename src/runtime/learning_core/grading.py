"""Deterministic grading for learner interactions.

Ported from DeepTutor learning/grading.py (Apache 2.0).

Rules:
- Multiple choice / true-false: exact index match; deterministic.
- Short answer / application: NOT graded here. These require a semantic
  assessor (LLM call) that lives in LearningService, not in the kernel.
- Feynman explanation: NOT graded here. Qualitative assessment is an LLM
  call; result is committed as LearningEvidence by LearningService.

Only `grade_choice` is deterministic and lives in this module.
Semantic grading stubs raise NotImplementedError to prevent accidental
coupling of LLM calls into the pure kernel.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.learning_core.contracts import PendingInteraction


@dataclass(frozen=True, slots=True)
class GradingResult:
    """Result of grading one interaction."""

    interaction_id: str
    correct: bool
    quality: int        # 0–4 SM-2 quality score
    error_code: str = ""   # non-empty if incorrect
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction_id": self.interaction_id,
            "correct": self.correct,
            "quality": self.quality,
            "error_code": self.error_code,
            "notes": self.notes,
        }


def grade_choice(
    interaction: PendingInteraction,
    user_answer: str | int,
) -> GradingResult:
    """Deterministically grade a multiple_choice or true_false interaction.

    Parameters
    ----------
    interaction:
        The pending interaction (must be is_choice == True).
    user_answer:
        Either an integer index into interaction.options, or a string
        representation of the index or the option text itself.
    """
    if not interaction.is_choice:
        raise ValueError(
            f"grade_choice called on non-choice interaction type: {interaction.interaction_type}"
        )
    if interaction.correct_index < 0 or not interaction.options:
        return GradingResult(
            interaction_id=interaction.interaction_id,
            correct=False,
            quality=0,
            error_code="no_correct_index",
            notes="Interaction has no valid correct_index.",
        )

    # Normalise answer to index
    given_index = _normalise_answer_to_index(user_answer, interaction.options)

    if given_index < 0:
        return GradingResult(
            interaction_id=interaction.interaction_id,
            correct=False,
            quality=0,
            error_code="invalid_answer_format",
            notes=f"Could not parse answer: {str(user_answer)[:80]}",
        )

    correct = (given_index == interaction.correct_index)
    quality = 3 if correct else 1   # SM-2: 3 = correct recall, 1 = fail

    return GradingResult(
        interaction_id=interaction.interaction_id,
        correct=correct,
        quality=quality,
        error_code="" if correct else "wrong_choice",
    )


def _normalise_answer_to_index(answer: str | int, options: tuple[str, ...]) -> int:
    """Convert a user answer to a 0-based index into options.

    Accepts:
    - An integer directly
    - A string digit ("0", "1", …)
    - A letter ("a", "b", "c", …)
    - The option text itself (case-insensitive prefix match)

    Returns -1 if no match.
    """
    if isinstance(answer, int):
        return answer if 0 <= answer < len(options) else -1

    text = str(answer).strip()

    # Integer string
    if text.isdigit():
        idx = int(text)
        return idx if 0 <= idx < len(options) else -1

    # Letter (a=0, b=1, …)
    if len(text) == 1 and text.lower() in "abcdefghij":
        idx = ord(text.lower()) - ord("a")
        return idx if idx < len(options) else -1

    # Exact match
    low = text.casefold()
    for i, opt in enumerate(options):
        if opt.casefold() == low:
            return i

    # Prefix match (min 3 chars to avoid false positives)
    if len(low) >= 3:
        for i, opt in enumerate(options):
            if opt.casefold().startswith(low):
                return i

    return -1


def quality_from_evidence_type(evidence_type: str, correct: bool) -> int:
    """Convert evidence_type + correct flag to SM-2 quality score."""
    if evidence_type == "misconception":
        return 0
    if not correct:
        return 1
    if evidence_type in {"quiz_correct", "feynman_pass"}:
        return 3
    if evidence_type in {"application", "paraphrase"}:
        return 3
    if evidence_type == "self_report":
        return 2
    return 2


__all__ = [
    "GradingResult",
    "grade_choice",
    "quality_from_evidence_type",
]

"""Pure state transitions for bounded pedagogical conversations."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from runtime.cognition.normalization import normalize_vi
from runtime.pedagogical_contracts import (
    ComprehensionState,
    TeachingConcept,
    TeachingChunk,
    TeachingDirective,
    TeachingEpisode,
    TeachingPlan,
    TeachingReceipt,
)


ActionKind = Literal[
    "answer_user", "teach_chunk", "repair", "probe", "advance", "wait", "complete", "abandon",
]
AUTONOMOUS_CHUNK_LIMIT = 2
REPAIR_LIMIT = 1
STALL_LIMIT = 2
EPISODE_SECONDS = 10 * 60
MASTERY_INCREMENT = 0.35
ACKNOWLEDGEMENTS = frozenset({
    "ok", "okay", "yes", "yeah", "yep", "yup", "uh", "uh huh", "mm hmm", "mhm", "hmm", "ừ",
    "got it", "i got it", "understood", "i understand", "makes sense", "that makes sense", "sure",
    "yes got it", "yeah got it", "yes understood", "ừ hiểu rồi", "hiểu rồi",
})
_USER_EVIDENCE_CODES = frozenset({
    "topic_change",
    "eris_corrected",
    "misconception_assertion",
    "focused_question",
    "user_paraphrase",
    "user_application",
})
_KEYED_MISCONCEPTION_PREFIXES = (
    "misconception:",
    "misconception_assertion:",
    "candidate_misconception:",
)
_SEMANTIC_STOPWORDS = frozenset({
    "about", "after", "also", "and", "are", "because", "before", "but", "by",
    "does", "for", "from", "have", "how", "into", "its", "need", "other", "that",
    "the", "their", "then", "this", "through", "what", "when", "which", "with", "would",
})


@dataclass(frozen=True, slots=True)
class PedagogicalLimits:
    """Typed, hard-bounded policy inputs for otherwise pure transitions."""

    max_autonomous_chunks: int = AUTONOMOUS_CHUNK_LIMIT
    max_repair_attempts: int = REPAIR_LIMIT
    max_consecutive_stalls: int = STALL_LIMIT
    episode_expiry_seconds: float = float(EPISODE_SECONDS)

    def __post_init__(self) -> None:
        def bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
            try:
                number = int(value)
            except (TypeError, ValueError, OverflowError):
                number = default
            return max(minimum, min(maximum, number))

        object.__setattr__(self, "max_autonomous_chunks", bounded_int(self.max_autonomous_chunks, 2, 0, 8))
        object.__setattr__(self, "max_repair_attempts", bounded_int(self.max_repair_attempts, 1, 0, 5))
        object.__setattr__(self, "max_consecutive_stalls", bounded_int(self.max_consecutive_stalls, 2, 1, 8))
        try:
            expiry = float(self.episode_expiry_seconds)
        except (TypeError, ValueError):
            expiry = float(EPISODE_SECONDS)
        object.__setattr__(self, "episode_expiry_seconds", max(60.0, min(86400.0, expiry)))


DEFAULT_LIMITS = PedagogicalLimits()


@dataclass(frozen=True, slots=True)
class ControllerAction:
    kind: ActionKind
    reason: str
    directive: TeachingDirective | None = None


@dataclass(frozen=True, slots=True)
class ControllerResult:
    episode: TeachingEpisode
    action: ControllerAction
    evidence_codes: tuple[str, ...] = ()


def _chunk_for(episode: TeachingEpisode, concept_id: str | None = None) -> TeachingChunk:
    concept = next((item for item in episode.concepts if item.concept_id == concept_id), episode.concepts[0])
    return TeachingChunk(
        chunk_id=f"{concept.concept_id}:r{episode.plan_revision}",
        concept_id=concept.concept_id,
        objective=concept.objective or concept.description,
        claim=concept.description,
        max_ideas=2,
        max_sentences=4,
    )


def _directive(episode: TeachingEpisode, limits: PedagogicalLimits) -> TeachingDirective:
    chunk = episode.pending_chunk or _chunk_for(episode, episode.comprehension.current_concept_id)
    return TeachingDirective(
        episode_id=episode.episode_id,
        plan_revision=episode.plan_revision,
        chunk_id=chunk.chunk_id,
        global_goal=episode.global_goal,
        current_objective=chunk.objective,
        claim=chunk.claim,
        contrast=chunk.contrast,
        example_policy=chunk.example_policy,
        max_ideas=chunk.max_ideas,
        max_sentences=chunk.max_sentences,
        allow_question=True,
        stop_after_chunk=True,
        allow_autonomous_resume=episode.autonomous_chunks_used < limits.max_autonomous_chunks,
    )


def _action(
    kind: ActionKind, reason: str, episode: TeachingEpisode, *, directive: bool = False,
    limits: PedagogicalLimits = DEFAULT_LIMITS,
) -> ControllerAction:
    return ControllerAction(kind, reason, _directive(episode, limits) if directive else None)


def start_episode(plan: TeachingPlan, now: float, *, limits: PedagogicalLimits = DEFAULT_LIMITS) -> TeachingEpisode:
    """Create a ready episode with a deterministic initial teaching chunk."""
    first = plan.concepts[0]
    episode = TeachingEpisode.new(
        episode_id=f"teaching:{first.concept_id}:{int(now)}",
        topic_key=first.concept_id,
        global_goal=plan.global_goal,
        concepts=plan.concepts,
        now=now,
        expires_at=now + limits.episode_expiry_seconds,
    )
    current = ComprehensionState(current_concept_id=first.concept_id)
    return replace(episode, status="ready", comprehension=current, pending_chunk=_chunk_for(episode, first.concept_id))


def expire_if_needed(
    episode: TeachingEpisode, *, now: float, limits: PedagogicalLimits = DEFAULT_LIMITS,
) -> TeachingEpisode:
    deadline = min(episode.expires_at, episode.last_activity_at + limits.episode_expiry_seconds)
    if episode.status not in {"completed", "abandoned", "expired"} and now >= deadline:
        return replace(episode, status="expired")
    return episode


def _refresh_activity(episode: TeachingEpisode, now: float, limits: PedagogicalLimits) -> TeachingEpisode:
    return replace(episode, last_activity_at=now, expires_at=now + limits.episode_expiry_seconds)


def _is_acknowledgement(user_text: str) -> bool:
    normalized = user_text.strip().casefold()
    canonical = " ".join(normalized.replace("-", " ").replace(",", " ").replace(".", " ")
                         .replace("!", " ").replace("?", " ").split())
    return not canonical or canonical in ACKNOWLEDGEMENTS


def _semantic_terms(value: str) -> frozenset[str]:
    return frozenset(
        word
        for word in normalize_vi(value)[:1200].split()
        if len(word) >= 3 and word not in _SEMANTIC_STOPWORDS
    )


def _is_concept_linked_paraphrase(user_text: str, current_concept: TeachingConcept | None) -> bool:
    if not isinstance(current_concept, TeachingConcept):
        return False
    concept_terms = _semantic_terms(f"{current_concept.description} {current_concept.objective}")
    user_terms = _semantic_terms(user_text)
    return len(concept_terms & user_terms) >= 2


def infer_user_evidence(
    user_text: str,
    *,
    explicit_evidence: tuple[str, ...] | None = None,
    current_concept: TeachingConcept | None = None,
) -> tuple[str, ...]:
    """Infer only high-confidence controller evidence from one bounded user turn."""
    if explicit_evidence is not None:
        accepted: list[str] = []
        for item in explicit_evidence:
            code = str(item or "").strip()
            if code in _USER_EVIDENCE_CODES:
                accepted.append(code)
                continue
            prefix = next((value for value in _KEYED_MISCONCEPTION_PREFIXES if code.startswith(value)), "")
            key = code[len(prefix):].strip()[:120] if prefix else ""
            if key and all(character.isalnum() or character in {"-", "_", "."} for character in key):
                accepted.append(prefix + key)
        return tuple(dict.fromkeys(accepted))
    if _is_acknowledgement(user_text):
        return ()

    text = normalize_vi(user_text)
    if not text:
        return ()
    if any(marker in text for marker in (
        "change the topic", "new topic", "another topic", "something else",
        "chuyen chu de", "doi chu de", "chu de khac", "noi chuyen khac",
    )):
        return ("topic_change",)
    if any(marker in text for marker in (
        "you misunderstood", "you are wrong", "that is not what i meant",
        "you got it wrong", "ban hieu sai", "ban sai", "khong phai y minh",
    )):
        return ("eris_corrected",)
    if "?" in str(user_text) or any(marker in text for marker in (
        "why does", "what do you mean", "how does", "does that mean",
        "tai sao", "vi sao", "y la gi", "the nao", "co phai",
    )):
        return ("focused_question",)
    if any(marker in text for marker in (
        "just means", "only means", "is simply", "is the same",
        "chi la", "chi co nghia", "don gian la", "giong het",
    )):
        return ("misconception_assertion",)
    if any(marker in text for marker in (
        "if i understand", "if i got this right", "if im understanding",
        "neu minh hieu", "neu toi hieu",
    )):
        return ()
    if any(marker in text for marker in (
        "i would apply", "i can apply", "i will apply", "i would use",
        "i can use", "i will use", "i used this", "i tried this",
        "minh se ap dung", "toi se ap dung", "ap dung vao",
    )):
        return ("user_application",)
    if any(marker in text for marker in (
        "in other words", "what i understand", "so it means", "that means",
        "nghia la", "noi cach khac", "minh hieu la", "tuc la",
    )):
        return ("user_paraphrase",) if _is_concept_linked_paraphrase(user_text, current_concept) else ()
    return ()


def _misconception_key(evidence: set[str], episode: TeachingEpisode) -> str:
    for code in sorted(evidence):
        if code.startswith(_KEYED_MISCONCEPTION_PREFIXES):
            return code.split(":", 1)[1] or episode.comprehension.current_concept_id
    return episode.comprehension.current_concept_id


def _has_misconception_evidence(evidence: set[str]) -> bool:
    return "misconception_assertion" in evidence or any(
        code.startswith(_KEYED_MISCONCEPTION_PREFIXES) for code in evidence
    )


def _begin_repair(episode: TeachingEpisode, key: str) -> TeachingEpisode:
    is_new_candidate = key != episode.repair_concept_id
    return replace(
        episode,
        status="repairing",
        pending_chunk=_chunk_for(episode, episode.comprehension.current_concept_id),
        repair_attempts=0 if is_new_candidate else episode.repair_attempts,
        repair_concept_id=key,
        consecutive_stalls=0,
    )


def _next_concept_id(episode: TeachingEpisode) -> str | None:
    current = episode.comprehension.current_concept_id
    for index, concept in enumerate(episode.concepts):
        if concept.concept_id == current:
            return episode.concepts[index + 1].concept_id if index + 1 < len(episode.concepts) else None
    return None


def select_next_action(
    episode: TeachingEpisode, *, now: float, limits: PedagogicalLimits = DEFAULT_LIMITS,
) -> ControllerAction:
    episode = expire_if_needed(episode, now=now, limits=limits)
    if episode.status == "completed":
        return _action("complete", "all_concepts_mastered", episode, limits=limits)
    if episode.status in {"abandoned", "expired", "failed"}:
        return _action("abandon", "episode_expired" if episode.status == "expired" else "episode_unavailable", episode, limits=limits)
    # Selecting another autonomous action after one recorded stall would create
    # the second consecutive stall, so yield the floor before doing so.
    if episode.consecutive_stalls >= limits.max_consecutive_stalls - 1:
        return _action("wait", "stall_limit", episode, limits=limits)
    if episode.autonomous_chunks_used >= limits.max_autonomous_chunks:
        return _action("wait", "autonomous_budget_exhausted", episode, limits=limits)
    if episode.status == "repairing":
        if episode.repair_attempts >= limits.max_repair_attempts:
            return _action("wait", "repair_limit", episode, limits=limits)
        return _action("repair", "candidate_misconception", episode, directive=True, limits=limits)
    if episode.status == "speaking":
        return _action("wait", "audio_in_progress", episode, limits=limits)
    if episode.status in {"waiting_user", "observing", "dwelling"}:
        return _action("probe", "awaiting_demonstration", episode, directive=True, limits=limits)
    if episode.comprehension.estimated_mastery >= 0.8 and _next_concept_id(episode) is None:
        return _action("complete", "mastery_threshold", episode, limits=limits)
    return _action("teach_chunk", "current_concept_ready", episode, directive=True, limits=limits)


def observe_user_turn(
    episode: TeachingEpisode, user_text: str, *, evidence: tuple[str, ...], now: float,
    limits: PedagogicalLimits = DEFAULT_LIMITS,
) -> ControllerResult:
    episode = expire_if_needed(episode, now=now, limits=limits)
    if episode.status == "expired":
        return ControllerResult(episode, _action("abandon", "episode_expired", episode, limits=limits), ("episode_expired",))

    supplied = {str(code) for code in evidence}
    codes: list[str] = []
    acknowledgement = _is_acknowledgement(user_text)
    if acknowledgement:
        codes.append("silence" if not user_text.strip() else "self_report_understood")
    if "topic_change" in supplied:
        codes.append("topic_change")
        updated = replace(episode, status="abandoned", pending_chunk=None)
        return ControllerResult(updated, _action("abandon", "topic_changed", updated, limits=limits), tuple(codes))

    if not acknowledgement:
        episode = _refresh_activity(episode, now, limits)

    if "eris_corrected" in supplied:
        updated = _begin_repair(episode, episode.comprehension.current_concept_id)
        codes.append("eris_corrected")
        return ControllerResult(updated, select_next_action(updated, now=now, limits=limits), tuple(codes))

    if _has_misconception_evidence(supplied):
        updated = _begin_repair(episode, _misconception_key(supplied, episode))
        codes.append("candidate_misconception")
        return ControllerResult(updated, select_next_action(updated, now=now, limits=limits), tuple(codes))

    if "focused_question" in supplied:
        state = replace(episode.comprehension, confidence=max(0.0, episode.comprehension.confidence - 0.2))
        updated = replace(episode, status="waiting_user", comprehension=state, consecutive_stalls=episode.consecutive_stalls + 1)
        codes.append("repeated_focused_question")
        return ControllerResult(updated, select_next_action(updated, now=now, limits=limits), tuple(codes))

    if not acknowledgement and "topic_change" not in supplied and {"user_paraphrase", "user_application"} & supplied:
        state = replace(
            episode.comprehension,
            estimated_mastery=min(1.0, episode.comprehension.estimated_mastery + MASTERY_INCREMENT),
            confidence=min(1.0, episode.comprehension.confidence + 0.2),
        )
        next_id = _next_concept_id(episode)
        if next_id is None:
            updated = replace(episode, status="completed", comprehension=state, autonomous_chunks_used=0, consecutive_stalls=0)
        else:
            state = replace(state, current_concept_id=next_id)
            updated = replace(
                episode, status="ready", comprehension=state, pending_chunk=_chunk_for(episode, next_id),
                autonomous_chunks_used=0, consecutive_stalls=0,
            )
        codes.append("demonstrated_understanding")
        return ControllerResult(updated, _action("complete", "all_concepts_mastered", updated) if updated.status == "completed"
                                else _action("advance", "demonstrated_understanding", updated, limits=limits), tuple(codes))

    updated = replace(episode, status="waiting_user") if acknowledgement else episode
    return ControllerResult(updated, select_next_action(updated, now=now, limits=limits), tuple(codes))


def note_public_commit(
    episode: TeachingEpisode, receipt: TeachingReceipt, *, autonomous: bool = True,
) -> TeachingEpisode:
    """Accept a successful receipt only for the current pending chunk."""
    chunk = episode.pending_chunk
    if (
        chunk is None
        or episode.status not in {"ready", "repairing"}
        or not receipt.successful
        or receipt.episode_id != episode.episode_id
        or receipt.plan_revision != episode.plan_revision
        or receipt.chunk_id != chunk.chunk_id
    ):
        return episode
    next_revision = episode.plan_revision + 1
    next_episode = replace(episode, plan_revision=next_revision)
    next_chunk = _chunk_for(next_episode, episode.comprehension.current_concept_id)
    if episode.status == "repairing":
        return replace(
            next_episode,
            status="speaking",
            pending_chunk=next_chunk,
            repair_attempts=episode.repair_attempts + 1,
        )
    return replace(
        next_episode,
        status="speaking",
        pending_chunk=next_chunk,
        autonomous_chunks_used=episode.autonomous_chunks_used + int(bool(autonomous)),
    )


def note_audio_finished(
    episode: TeachingEpisode, *, now: float, limits: PedagogicalLimits = DEFAULT_LIMITS,
) -> TeachingEpisode:
    episode = expire_if_needed(episode, now=now, limits=limits)
    if episode.status == "speaking":
        return replace(episode, status="waiting_user")
    return episode


__all__ = [
    "ControllerAction", "ControllerResult", "PedagogicalLimits", "expire_if_needed", "infer_user_evidence", "note_audio_finished",
    "note_public_commit", "observe_user_turn", "select_next_action", "start_episode",
]

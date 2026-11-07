# -*- coding: utf-8 -*-
"""Typed evidence enrichment before public Surface realization.

The public writer should not invent a premise and then ask a validator whether it
was lucky enough to be true.  This module flips the flow:

    user/self/history stores -> auditable evidence bundle -> semantic intent -> Surface

The compiler is deliberately deterministic and side-effect free.  It does not ask
an LLM what the user is like and it does not mutate memory.  It only selects facts
that already carry provenance and one bounded information gap for ``user_probe``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any, Mapping, Sequence

EVIDENCE_ENRICHMENT_VERSION = "eris.evidence_enrichment.v1"


def _compact(value: Any, limit: int = 360) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _uniq(values: Sequence[str], limit: int = 64) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item) for item in values if str(item or "").strip()))[:limit]


@dataclass(frozen=True, slots=True)
class EvidenceFact:
    kind: str
    text: str
    evidence_refs: tuple[str, ...]
    confidence: float
    claim_domain: str
    source: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": _compact(self.text, 520),
            "evidence_refs": list(self.evidence_refs),
            "confidence": round(_clamp01(self.confidence), 4),
            "claim_domain": self.claim_domain,
            "source": self.source,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True, slots=True)
class UserGap:
    key: str
    focus: str
    reason: str
    score: float
    importance: float
    intrusiveness: float
    expected_information_gain: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "focus": self.focus,
            "reason": self.reason,
            "score": round(_clamp01(self.score), 4),
            "importance": round(_clamp01(self.importance), 4),
            "intrusiveness": round(_clamp01(self.intrusiveness), 4),
            "expected_information_gain": round(_clamp01(self.expected_information_gain), 4),
        }


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    user_facts: tuple[EvidenceFact, ...] = ()
    recent_user_turns: tuple[EvidenceFact, ...] = ()
    shared_episodes: tuple[EvidenceFact, ...] = ()
    self_facts: tuple[EvidenceFact, ...] = ()
    open_user_gap: UserGap | None = None
    query: str = ""

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        refs: list[str] = []
        for fact in (*self.user_facts, *self.recent_user_turns, *self.shared_episodes, *self.self_facts):
            refs.extend(fact.evidence_refs)
        return _uniq(refs, 96)

    @property
    def claim_domains(self) -> tuple[str, ...]:
        return _uniq(
            [fact.claim_domain for fact in (*self.user_facts, *self.recent_user_turns, *self.shared_episodes, *self.self_facts)],
            16,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EVIDENCE_ENRICHMENT_VERSION,
            "query": _compact(self.query, 700),
            "user_facts": [item.to_dict() for item in self.user_facts],
            "recent_user_turns": [item.to_dict() for item in self.recent_user_turns],
            "shared_episodes": [item.to_dict() for item in self.shared_episodes],
            "self_facts": [item.to_dict() for item in self.self_facts],
            "open_user_gap": self.open_user_gap.to_dict() if self.open_user_gap else None,
            "allowed_evidence_refs": list(self.evidence_refs),
            "claim_domains": list(self.claim_domains),
            "rules": [
                "Facts are inputs, not mandatory talking points.",
                "Never transform an unknown into a remembered fact.",
                "For user_probe, choose the supplied open_user_gap unless a supplied fact already answers it.",
                "Do not mention provenance ids or internal memory systems in public wording.",
            ],
        }


_GAPS: tuple[dict[str, Any], ...] = (
    {
        "key": "collaboration_preference",
        "focus": "how the user prefers Eris to collaborate on debugging/building work, such as acting directly versus explaining trade-offs first",
        "importance": 0.88,
        "intrusiveness": 0.08,
        "gain": 0.90,
        "keywords": ("eris", "repo", "code", "bug", "fix", "patch", "source", "tts", "runtime", "architecture", "test", "debug"),
        "known": ("workflow", "communication", "technical", "collaboration"),
    },
    {
        "key": "decision_style",
        "focus": "which trade-offs the user tends to prioritize when there are several technically valid directions",
        "importance": 0.80,
        "intrusiveness": 0.10,
        "gain": 0.82,
        "keywords": ("architecture", "tradeoff", "design", "muc tieu", "goal", "ưu tiên", "uu tien", "chon", "choose"),
        "known": ("decision", "priorit", "tradeoff"),
    },
    {
        "key": "learning_style",
        "focus": "what kind of explanation or exploration helps the user understand a difficult technical idea best",
        "importance": 0.69,
        "intrusiveness": 0.08,
        "gain": 0.72,
        "keywords": ("nghien cuu", "research", "giai thich", "explain", "hieu", "learn", "kien truc", "architecture"),
        "known": ("learning", "explanation", "communication"),
    },
    {
        "key": "current_interest",
        "focus": "one concrete thing the user is currently curious about or wants to explore next",
        "importance": 0.72,
        "intrusiveness": 0.06,
        "gain": 0.74,
        "keywords": (),
        "known": ("current_interest", "interests.current", "goals.current"),
    },
    {
        "key": "creative_taste",
        "focus": "a concrete creative or entertainment preference that would help Eris understand the user's taste",
        "importance": 0.56,
        "intrusiveness": 0.07,
        "gain": 0.60,
        "keywords": ("music", "nhac", "film", "phim", "game", "art", "ve", "viet", "story"),
        "known": ("creative", "music", "movies", "games", "entertainment"),
    },
    {
        "key": "taste_preference",
        "focus": "one ordinary taste preference, only when food/drink is already relevant in recent conversation",
        "importance": 0.44,
        "intrusiveness": 0.05,
        "gain": 0.48,
        "keywords": ("coffee", "cafe", "ca phe", "food", "an", "uong", "tra", "tea"),
        "known": ("taste", "food", "drink", "coffee", "cafe"),
    },
    {
        "key": "recovery_style",
        "focus": "what tends to help the user reset after a demanding stretch of work",
        "importance": 0.62,
        "intrusiveness": 0.18,
        "gain": 0.66,
        "keywords": ("met", "stress", "ngu", "tired", "burnout", "nghi", "rest"),
        "known": ("recovery", "rest", "stress", "sleep"),
    },
)


class EvidenceEnrichmentCompiler:
    """Compile bounded, auditable context for public semantic realization."""

    def __init__(self, host: Any, *, logger: Any | None = None) -> None:
        self.host = host
        self.logger = logger if logger is not None else getattr(host, "logger", None)

    def compile(
        self,
        *,
        speech_act: str = "",
        semantic_goal: str = "",
        topic: str = "",
        current_user_text: str = "",
        recent_history: Sequence[Any] = (),
    ) -> EvidenceBundle:
        recent_turns = self._recent_user_turn_facts(limit=5)
        query = self._query(
            speech_act=speech_act,
            semantic_goal=semantic_goal,
            topic=topic,
            current_user_text=current_user_text,
            recent_turns=recent_turns,
            recent_history=recent_history,
        )
        profile_facts = self._user_profile_facts(query=query, limit=10)
        episodes = self._shared_episode_facts(query=query, limit=3)
        self_facts = self._self_facts(limit=6)
        gap = None
        if str(speech_act or "").strip().lower() == "user_probe":
            gap = self._select_user_gap(
                query=query,
                profile_facts=profile_facts,
                recent_turns=recent_turns,
            )
        return EvidenceBundle(
            user_facts=profile_facts,
            recent_user_turns=recent_turns,
            shared_episodes=episodes,
            self_facts=self_facts,
            open_user_gap=gap,
            query=query,
        )

    def _log_debug(self, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            self.logger.debug("[evidence enrichment] " + message, *args)
        except Exception:
            pass

    def _living_user(self) -> Any | None:
        candidates = (
            getattr(self.host, "living_user", None),
            getattr(getattr(self.host, "memory_kernel", None), "living_user", None),
            getattr(getattr(self.host, "memory_core", None), "living_user", None),
        )
        for item in candidates:
            if item is not None:
                return item
        kernel_fn = getattr(self.host, "_memory_kernel", None)
        if callable(kernel_fn):
            try:
                return getattr(kernel_fn(), "living_user", None)
            except Exception:
                return None
        return None

    def _recent_user_turn_facts(self, *, limit: int) -> tuple[EvidenceFact, ...]:
        ledger_fn = getattr(self.host, "_ensure_session_turn_ledger", None)
        if not callable(ledger_fn):
            return ()
        try:
            turns = ledger_fn().user_turns()[-max(1, int(limit)):]
        except Exception:
            return ()
        output: list[EvidenceFact] = []
        for turn in turns:
            text = _compact(getattr(turn, "text", ""), 520)
            index = int(getattr(turn, "index", 0) or 0)
            if not text or index <= 0:
                continue
            output.append(EvidenceFact(
                kind="recent_user_turn",
                text=text,
                evidence_refs=(f"session-user-turn:{index}",),
                confidence=1.0,
                claim_domain="conversation_history",
                source="session_turn_ledger",
                metadata={"turn_index": index},
            ))
        return tuple(output)

    def _user_profile_facts(self, *, query: str, limit: int) -> tuple[EvidenceFact, ...]:
        living = self._living_user()
        compile_context = getattr(living, "compile_context", None)
        if callable(compile_context):
            try:
                packet = compile_context(query or "current user preferences and working style", token_budget=520, include_sensitive=False)
                selected: list[EvidenceFact] = []
                for item in tuple(getattr(packet, "items", ()) or ())[: max(1, int(limit))]:
                    use_mode = str(getattr(item, "use_mode", "") or "").lower()
                    explicitness = str(getattr(item, "explicitness", "") or "").lower()
                    refs = _uniq(tuple(getattr(item, "evidence_ids", ()) or ()), 8)
                    if use_mode != "direct" or explicitness not in {"explicit", "observed"} or not refs:
                        continue
                    path = _compact(getattr(item, "path", ""), 120)
                    value = _compact(getattr(item, "value", ""), 320)
                    score = _clamp01(getattr(item, "score", 0.0))
                    if not path or not value:
                        continue
                    selected.append(EvidenceFact(
                        kind="user_profile",
                        text=f"{path}: {value}",
                        evidence_refs=refs,
                        confidence=_clamp01(0.62 + 0.34 * score),
                        claim_domain="user_profile",
                        source="living_user_context_compiler",
                        metadata={"path": path, "explicitness": explicitness, "relevance_score": round(score, 4)},
                    ))
                if selected:
                    return tuple(selected[: max(1, int(limit))])
            except Exception as exc:
                self._log_debug("context compiler fallback: %s", exc)

        list_memories = getattr(living, "list_memories", None)
        if not callable(list_memories):
            return ()
        try:
            rows = list_memories(include_inferred=False, include_sensitive=False, limit=max(20, limit * 3))
        except Exception:
            return ()
        ranked: list[tuple[float, EvidenceFact]] = []
        for record in rows:
            if not bool(getattr(record, "is_current", True)):
                continue
            explicitness = str(getattr(record, "explicitness", "") or "").lower()
            confidence = _clamp01(getattr(record, "confidence", 0.0))
            if explicitness == "observed" and confidence < 0.72:
                continue
            if explicitness not in {"explicit", "observed"}:
                continue
            refs = _uniq(tuple(getattr(record, "evidence_ids", ()) or ()), 8)
            if not refs:
                # A profile value without provenance may be useful for diagnostics,
                # but it must not become public evidence.
                continue
            path = _compact(getattr(record, "path", ""), 120)
            value = _compact(getattr(record, "value", ""), 320)
            if not path or not value:
                continue
            trust = _clamp01(getattr(record, "trust_weight", confidence), confidence)
            ranked.append((trust, EvidenceFact(
                kind="user_profile",
                text=f"{path}: {value}",
                evidence_refs=refs,
                confidence=trust,
                claim_domain="user_profile",
                source=str(getattr(record, "source", "living_user_profile") or "living_user_profile"),
                metadata={"path": path, "explicitness": explicitness},
            )))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return tuple(fact for _, fact in ranked[: max(1, int(limit))])

    def _shared_episode_facts(self, *, query: str, limit: int) -> tuple[EvidenceFact, ...]:
        living = self._living_user()
        store = getattr(living, "episode_store", None)
        if store is None:
            return ()
        search = getattr(store, "search", None)
        recent = getattr(store, "recent", None)
        try:
            episodes = search(query, limit=max(1, int(limit))) if callable(search) and query else ()
            if not episodes and callable(recent):
                episodes = recent(limit=max(1, int(limit)))
        except Exception:
            return ()
        output: list[EvidenceFact] = []
        for episode in tuple(episodes or ())[: max(1, int(limit))]:
            scope = getattr(episode, "scope", None)
            if str(getattr(scope, "kind", "global") or "global") in {"private"}:
                continue
            refs = _uniq(tuple(getattr(episode, "evidence_ids", ()) or ()), 12)
            if not refs:
                continue
            title = _compact(getattr(episode, "title", ""), 160)
            summary = _compact(getattr(episode, "summary", ""), 460)
            if not summary:
                continue
            output.append(EvidenceFact(
                kind="shared_episode",
                text=f"{title}: {summary}" if title else summary,
                evidence_refs=refs,
                confidence=_clamp01(0.62 + 0.28 * _clamp01(getattr(episode, "importance", 0.5))),
                claim_domain="shared_episode",
                source="living_user_episode",
                metadata={"episode_id": str(getattr(episode, "id", "") or "")},
            ))
        return tuple(output)

    def _self_facts(self, *, limit: int) -> tuple[EvidenceFact, ...]:
        service = getattr(self.host, "subject_service", None)
        view_fn = getattr(service, "self_view", None)
        if not callable(view_fn):
            return ()
        try:
            payload = view_fn()
        except Exception:
            return ()
        if not isinstance(payload, Mapping):
            return ()
        rows = payload.get("dominant_facets", ())
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            return ()
        output: list[EvidenceFact] = []
        for raw in rows[: max(1, int(limit))]:
            if not isinstance(raw, Mapping):
                continue
            statement = _compact(raw.get("statement", ""), 460)
            refs = _uniq(tuple(raw.get("evidence_refs", ()) or ()), 10)
            confidence = _clamp01(raw.get("confidence", 0.0))
            if not statement or not refs or confidence < 0.45:
                continue
            output.append(EvidenceFact(
                kind="self_model",
                text=statement,
                evidence_refs=refs,
                confidence=confidence,
                claim_domain="self_model",
                source="subject_self_projection",
                metadata={"facet_id": str(raw.get("facet_id", "") or ""), "facet_kind": str(raw.get("kind", "") or "")},
            ))
        return tuple(output)

    def _query(
        self,
        *,
        speech_act: str,
        semantic_goal: str,
        topic: str,
        current_user_text: str,
        recent_turns: Sequence[EvidenceFact],
        recent_history: Sequence[Any],
    ) -> str:
        pieces = [speech_act, semantic_goal, topic, current_user_text]
        pieces.extend(item.text for item in recent_turns[-3:])
        for item in tuple(recent_history or ())[-4:]:
            if isinstance(item, Mapping):
                pieces.append(str(item.get("content", item.get("text", "")) or ""))
            else:
                pieces.append(str(item or ""))
        return _compact(" | ".join(part for part in pieces if str(part or "").strip()), 1200)

    def _select_user_gap(
        self,
        *,
        query: str,
        profile_facts: Sequence[EvidenceFact],
        recent_turns: Sequence[EvidenceFact],
    ) -> UserGap:
        folded = _fold(" ".join((query, *(item.text for item in recent_turns))))
        known_paths = _fold(" ".join(
            str(item.metadata.get("path", ""))
            for item in profile_facts
            if isinstance(item.metadata, Mapping)
        ))
        best: tuple[float, dict[str, Any], str] | None = None
        for spec in _GAPS:
            keywords = tuple(_fold(item) for item in spec.get("keywords", ()))
            hits = sum(1 for cue in keywords if cue and cue in folded)
            contextual = min(0.42, 0.16 * hits)
            known_tokens = tuple(_fold(item) for item in spec.get("known", ()))
            known_penalty = 0.34 if any(token and token in known_paths for token in known_tokens) else 0.0
            # Ordinary taste is intentionally context-gated. It should not beat
            # a work/relationship gap while the conversation is about a repo.
            if spec["key"] == "taste_preference" and hits == 0:
                contextual -= 0.26
            importance = float(spec["importance"])
            intrusiveness = float(spec["intrusiveness"])
            gain = float(spec["gain"])
            score = _clamp01(0.48 * importance + 0.36 * gain + contextual - known_penalty - 0.22 * intrusiveness)
            reason = f"context_hits={hits}; known_penalty={known_penalty:.2f}; recent_context={'present' if folded else 'thin'}"
            if best is None or score > best[0]:
                best = (score, spec, reason)
        assert best is not None
        score, spec, reason = best
        return UserGap(
            key=str(spec["key"]),
            focus=str(spec["focus"]),
            reason=reason,
            score=score,
            importance=float(spec["importance"]),
            intrusiveness=float(spec["intrusiveness"]),
            expected_information_gain=float(spec["gain"]),
        )


def compiler_for(host: Any) -> EvidenceEnrichmentCompiler:
    current = getattr(host, "evidence_enrichment_compiler", None)
    if isinstance(current, EvidenceEnrichmentCompiler):
        return current
    current = EvidenceEnrichmentCompiler(host)
    try:
        setattr(host, "evidence_enrichment_compiler", current)
    except Exception:
        pass
    return current


__all__ = [
    "EVIDENCE_ENRICHMENT_VERSION",
    "EvidenceBundle",
    "EvidenceEnrichmentCompiler",
    "EvidenceFact",
    "UserGap",
    "compiler_for",
]

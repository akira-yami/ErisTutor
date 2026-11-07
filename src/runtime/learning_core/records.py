"""Versioned learning records. Pure data; no model, storage, or delivery authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
from typing import Any

RESULTS = frozenset({"correct", "incorrect", "partial", "ungraded"})
ASSISTANCE = ("independent", "hinted", "worked_with_eris", "answer_revealed")


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", value):
        raise ValueError("invalid_learning_identifier")
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def evidence_metadata(evidence) -> dict:
    try:
        value = json.loads(evidence.metadata or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def independent(evidence) -> bool:
    data = evidence_metadata(evidence)
    # Legacy evidence retains its historical semantics; newly recorded events
    # always include assistance/result explicitly.
    return (
        data.get("assistance", "independent") == "independent"
        and not data.get("answer_revealed", False)
        and data.get("hint_count", 0) == 0
        and data.get("result") not in {"partial", "ungraded"}
    )


@dataclass(frozen=True, slots=True)
class DomainSpec:
    domain_id: str
    title: str
    skill_namespace: str
    version: str = "1"
    default_source_ids: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ("choice", "short_answer")

    def __post_init__(self):
        for value in (self.domain_id, self.skill_namespace, self.version):
            identifier(value)
        if not self.title.strip() or len(self.title) > 240:
            raise ValueError("invalid_domain_title")
        for value in (*self.default_source_ids, *self.capabilities):
            identifier(value)
        if len(self.default_source_ids) > 32 or len(self.capabilities) > 16:
            raise ValueError("domain_budget_exceeded")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SourceSpan:
    span_id: str
    source_id: str
    domain_id: str
    content_hash: str
    locator: str
    excerpt: str
    origin: str = ""
    parser_version: str = "text-v1"
    source_kind: str = "original"
    imported_at: float = 0.0

    def __post_init__(self):
        for value in (self.span_id, self.source_id, self.domain_id):
            identifier(value)
        if not re.fullmatch(r"[a-f0-9]{64}", self.content_hash):
            raise ValueError("invalid_source_hash")
        if (
            not self.locator
            or len(self.locator) > 500
            or not self.excerpt.strip()
            or len(self.excerpt) > 12000
            or len(self.origin) > 2000
            or len(self.parser_version) > 120
        ):
            raise ValueError("invalid_source_span")
        if self.source_kind not in {"original", "user_note", "model_summary"}:
            raise ValueError("invalid_source_kind")
        if not math.isfinite(self.imported_at) or self.imported_at < 0:
            raise ValueError("invalid_import_time")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AssessmentAttempt:
    attempt_id: str
    kp_id: str
    result: str
    evidence_type: str
    strength: float = 0.5
    interaction_id: str = ""
    turn_id: str = ""
    path_id: str = ""
    item_id: str = ""
    item_family_id: str = ""
    activity_id: str = ""
    assistance: str = "independent"
    hint_count: int = 0
    answer_revealed: bool = False
    rubric_version: str = "legacy-v1"
    assessor_version: str = "semantic-v1"
    source_refs: tuple[str, ...] = ()
    recorded_at: float = 0.0
    review_recall: bool = False
    rating: int = 0
    error_code: str = ""
    supersedes_attempt_id: str = ""
    teaching_policy: str = "eris-tutor-v1"
    phase: str = "practice"
    response_ms: int | None = None
    answer_ref: str = ""
    corrected_at: float = 0.0

    def __post_init__(self):
        for value in (self.attempt_id, self.kp_id):
            identifier(value)
        for value in (
            self.interaction_id,
            self.turn_id,
            self.path_id,
            self.item_id,
            self.item_family_id,
            self.activity_id,
            self.supersedes_attempt_id,
        ):
            if value:
                identifier(value)
        if self.result not in RESULTS or self.assistance not in ASSISTANCE:
            raise ValueError("invalid_attempt_result_or_assistance")
        if (
            self.result == "correct"
            and self.evidence_type in {"quiz_incorrect", "feynman_fail", "misconception"}
            or self.result == "incorrect"
            and self.evidence_type in {"quiz_correct", "feynman_pass", "application", "paraphrase"}
        ):
            raise ValueError("attempt_result_evidence_mismatch")
        if type(self.hint_count) is not int or not 0 <= self.hint_count <= 100:
            raise ValueError("invalid_hint_count")
        if type(self.answer_revealed) is not bool or type(self.review_recall) is not bool:
            raise ValueError("invalid_attempt_flags")
        assistance = self.assistance
        if self.answer_revealed:
            assistance = "answer_revealed"
        elif self.hint_count and assistance == "independent":
            assistance = "hinted"
        object.__setattr__(self, "assistance", assistance)
        if type(self.rating) is not int or self.rating not in range(5):
            raise ValueError("invalid_recall_rating")
        if not math.isfinite(self.strength) or not 0 <= self.strength <= 1:
            raise ValueError("invalid_attempt_strength")
        if not math.isfinite(self.recorded_at) or self.recorded_at < 0:
            raise ValueError("invalid_attempt_time")
        if not math.isfinite(self.corrected_at) or self.corrected_at < 0:
            raise ValueError("invalid_correction_time")
        if self.phase not in {"practice", "transfer", "review"}:
            raise ValueError("invalid_attempt_phase")
        if self.response_ms is not None and (
            type(self.response_ms) is not int or not 0 <= self.response_ms <= 86400000
        ):
            raise ValueError("invalid_response_duration")
        for value in (self.rubric_version, self.assessor_version, self.teaching_policy):
            identifier(value)
        if len(self.source_refs) > 16 or len(self.answer_ref) > 500:
            raise ValueError("attempt_budget_exceeded")
        for value in self.source_refs:
            identifier(value)

    @property
    def independently_correct(self):
        return self.result == "correct" and self.assistance == "independent"

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_mapping(cls, raw):
        value = dict(raw)
        value["source_refs"] = tuple(value.get("source_refs", ()))
        return cls(**value)

    def fingerprint(self):
        value = self.to_dict()
        value.pop("recorded_at")
        value.pop("corrected_at")
        return digest(value)

    def evidence_metadata(self):
        return json.dumps(
            {
                "attempt_id": self.attempt_id,
                "result": self.result,
                "assistance": self.assistance,
                "hint_count": self.hint_count,
                "answer_revealed": self.answer_revealed,
                "review_recall": self.review_recall,
                "item_family_id": self.item_family_id,
                "rating": self.rating,
            },
            separators=(",", ":"),
        )

from __future__ import annotations

"""
memory_commit_policy.py
=======================

Memory intake policy cho user turn.

File này đi theo 3 tầng:
    1. Importance detection: câu này có đáng giữ không?
    2. Extraction attempt: có parse được thành fact_key/value/procedure không?
    3. Fallback capture: nếu quan trọng nhưng chưa parse được, lưu memory_candidate.

Nó KHÔNG ghi thẳng memory.
Nó KHÔNG gọi store.
Nó KHÔNG dựa vào LLM để "cảm thấy đã nhớ".

Nếu một câu đáng nhớ nhưng chưa parse được, policy tạo CANDIDATE_COMMIT.
Public layer sau đó chỉ nên nói kiểu: đã giữ dấu vết, chưa chưng cất thành
ký ức ổn định. Không nói "đã nhớ chắc". Văn minh tối thiểu, thật đáng ngạc nhiên.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .memory_surface_filter import should_exclude_from_persistent_memory

try:  # package path: src/memory/memory_commit_policy.py
    from .memory_models import (
        DecayPolicy,
        MemoryCommitDecision,
        MemoryKind,
        MemoryTarget,
        SurfaceMode,
    )
    from .claim_extractor import ClaimExtractor
    from .claims.claim_builder import ClaimBuilder
    from .predicate_registry import PredicateRegistry, get_default_registry
    from .memory_worthiness import MemoryWorthinessPolicy
    from .memory_retention_policy import MemoryRetentionPolicy, retention_metadata
    from .predicate_ontology import fact_key_for_predicate, get_predicate, predicate_aliases
    from .semantic_claim import SemanticClaim
    from .name_validation import is_valid_name_claim
except Exception:  # pragma: no cover - chạy file rời khi test
    from memory_models import (  # type: ignore
        DecayPolicy,
        MemoryCommitDecision,
        MemoryKind,
        MemoryTarget,
        SurfaceMode,
    )
    from claim_extractor import ClaimExtractor  # type: ignore
    from claims.claim_builder import ClaimBuilder  # type: ignore
    from predicate_registry import PredicateRegistry, get_default_registry  # type: ignore
    from memory_worthiness import MemoryWorthinessPolicy  # type: ignore
    from memory_retention_policy import MemoryRetentionPolicy, retention_metadata  # type: ignore
    from predicate_ontology import fact_key_for_predicate, get_predicate, predicate_aliases  # type: ignore
    from semantic_claim import SemanticClaim  # type: ignore
    from name_validation import is_valid_name_claim  # type: ignore


# ---------------------------------------------------------------------------
# Normalize helpers
# ---------------------------------------------------------------------------

def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", str(text or ""))
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_vi(text: str) -> str:
    """Normalize tiếng Việt để match rule ổn định hơn.

    Bản normalize chỉ dùng để nhận diện. Không dùng nó làm surface public,
    vì làm mất dấu tên riêng kiểu "Tín" thì hơi tội nghiệp dữ liệu.
    """

    lowered = _strip_accents(str(text or "").lower()).replace("đ", "d").replace("Đ", "D")
    lowered = re.sub(r"[^\w\s:/@.+#-]", " ", lowered, flags=re.UNICODE)
    return " ".join(lowered.split())


def _clean_value(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    text = text.strip(" .,:;!?\"'“”‘’`")
    # Cắt vài hạt đệm hay dính vào cuối self-disclosure ngắn.
    text = re.sub(r"\s+(nha|nhé|nhe|đấy|day|thôi|thoi|ạ|a)$", "", text, flags=re.IGNORECASE)
    return text.strip(" .,:;!?\"'“”‘’`")


def _evidence(turn_id: str | None) -> list[str]:
    return [str(turn_id)] if str(turn_id or "").strip() else []


def _has_self_marker(norm: str) -> bool:
    return bool(re.search(r"\b(toi|minh|t|tui|tao|em|i|me|my)\b", norm))


def _looks_like_question(norm: str, raw: str) -> bool:
    """Chặn recall query khỏi bị commit nhầm thành fact.

    "tôi tên gì", "tôi bao nhiêu tuổi", "tôi đang ở đâu" là câu hỏi.
    "nhớ rằng tôi tên là X" dù có dấu ? kỳ quặc vẫn được xử lý nếu có marker nhớ.
    """

    if any(marker in norm for marker in ("nho rang", "hay nho", "remember that")):
        return False
    if "?" in str(raw or ""):
        return True
    question_markers = (
        "toi ten gi",
        "ten toi la gi",
        "toi la ai",
        "toi bao nhieu tuoi",
        "toi may tuoi",
        "toi sinh nam nao",
        "toi sinh thang may",
        "toi dang o dau",
        "toi hien o dau",
        "hom qua toi o dau",
        "truoc day toi o dau",
        "em nho",
        "co nho",
    )
    return any(re.search(r"(?:^|\s)" + re.escape(marker) + r"(?:\s|$)", norm) for marker in question_markers)


def _extract_with_patterns(raw: str, patterns: Iterable[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE | re.UNICODE)
        if not match:
            continue
        value = _clean_value(match.group(1))
        if value:
            return value
    return ""


def _year_from_2k(token: str) -> int | None:
    match = re.fullmatch(r"2k(\d{1,2})", str(token or "").strip().lower())
    if not match:
        return None
    suffix = int(match.group(1))
    # 2k6 -> 2006, 2k06 -> 2006, 2k24 -> 2024.
    return 2000 + suffix


def _bounded_month(value: str) -> int | None:
    raw = normalize_vi(value)
    month_words = {
        "mot": 1, "một": 1,
        "hai": 2,
        "ba": 3,
        "bon": 4, "bốn": 4, "tu": 4, "tư": 4,
        "nam": 5, "năm": 5,
        "sau": 6, "sáu": 6,
        "bay": 7, "bảy": 7,
        "tam": 8, "tám": 8,
        "chin": 9, "chín": 9,
        "muoi": 10, "mười": 10,
        "muoi mot": 11, "mười một": 11,
        "muoi hai": 12, "mười hai": 12,
    }
    if raw in month_words:
        return month_words[raw]
    match = re.search(r"\d{1,2}", raw)
    if not match:
        return None
    month = int(match.group(0))
    return month if 1 <= month <= 12 else None


EXPLICIT_MEMORY_MARKERS: tuple[str, ...] = (
    "nho",
    "nho rang",
    "hay nho",
    "ghi lai",
    "luu",
    "luu lai",
    "quan trong",
    "tu gio",
    "sau nay",
    "ve sau",
    "dung quen",
    "do not forget",
    "remember that",
    "from now on",
)


def _explicit_importance(norm: str) -> tuple[float, list[str]]:
    reasons: list[str] = []
    if any(marker in norm for marker in ("nho", "ghi lai", "luu", "remember", "dung quen")):
        reasons.append("explicit_memory_marker")
    if any(marker in norm for marker in ("quan trong", "important")):
        reasons.append("importance_marker")
    if any(marker in norm for marker in ("tu gio", "sau nay", "ve sau", "from now on")):
        reasons.append("future_or_convention_marker")
    if any(marker in norm for marker in ("vibe nay", "logic nay", "y nay", "kieu nay", "doan nay", "pattern nay")):
        reasons.append("loose_memory_candidate_marker")
    if not reasons:
        return 0.0, []
    score = 0.85
    if "importance_marker" in reasons:
        score = max(score, 0.90)
    if "future_or_convention_marker" in reasons:
        score = max(score, 0.88)
    return score, reasons


def _looks_like_convention(norm: str) -> bool:
    return bool(
        any(marker in norm for marker in ("tu gio", "sau nay", "ve sau", "neu", "khi", "luc nao"))
        and any(marker in norm for marker in ("hieu la", "tuc la", "coi la", "xem la", "nghia la", "thi hieu", "handling", "interpret"))
    )


def _guess_domain(norm: str) -> str:
    if _looks_like_convention(norm):
        return "user_convention"
    if any(marker in norm for marker in ("vibe", "tone", "giong", "style", "khong khi")):
        return "style_or_vibe"
    if any(marker in norm for marker in ("logic", "kien truc", "architecture", "pipeline", "memory", "eris")):
        return "project_logic"
    if any(marker in norm for marker in ("cach xu ly", "xu ly", "rule", "quy tac", "policy")):
        return "procedural_rule"
    return "general_memory_candidate"


def _candidate_fact_keys(norm: str, domain: str) -> list[str]:
    keys: list[str] = []
    if domain == "user_convention":
        keys.extend(["user.convention", "project.vocabulary", "procedural_rule"])
    if domain == "style_or_vibe":
        keys.extend(["user.preference.vibe", "style.memory_candidate"])
    if domain == "project_logic":
        keys.extend(["project.logic", "project.architecture", "procedural_rule"])
    if domain == "procedural_rule":
        keys.extend(["procedural_rule", "user.handling_rule"])
    if any(marker in norm for marker in ("eris", "trang thai loi", "core status", "status")):
        keys.extend(["project.eris", "user.convention.core_status_phrase"])
    if not keys:
        keys.append("memory_candidate.unresolved")
    # dedupe giữ thứ tự, vì một dòng dict mà lặp key thì đúng là nghệ thuật tệ.
    return list(dict.fromkeys(keys))[:6]


def _slug_key(value: str, *, limit: int = 48) -> str:
    slug = normalize_vi(value)
    slug = re.sub(r"[^a-z0-9_]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return (slug or "rule")[:limit]


# ---------------------------------------------------------------------------
# Main policy
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MemoryCommitPolicy:
    """Memory intake 3 tầng cho user turn.

    Tầng 1: phát hiện importance.
    Tầng 2: cố extract semantic/episodic/procedural fact rõ.
    Tầng 3: nếu explicit important nhưng chưa parse được, tạo memory_candidate.

    Output vẫn chỉ là request-level decision. Commit thật đi qua
    StateCommitter/MemoryCognitionCore.
    """

    min_confidence: float = 0.62
    # Candidate không cần chắc như semantic. User đã có ý định lưu, nhưng câu còn
    # lủng củng thì phải giữ dấu vết để consolidator xử lý sau. Nếu đặt ngưỡng
    # quá cao, candidate store lại thành cái bình hoa JSON, nhìn có vẻ sang.
    explicit_candidate_min_importance: float = 0.72
    enable_semantic_claim_pipeline: bool = True
    # Có thể inject parser model.gguf từ runtime. Nếu None, ClaimExtractor dùng fallback surface.
    semantic_parser: Any | None = None
    registry: PredicateRegistry = field(default_factory=get_default_registry)
    claim_extractor: Any = field(init=False, repr=False)
    worthiness_policy: Any = field(init=False, repr=False)
    retention_policy: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Ba lớp mới dùng chung cho toàn bộ intake.
        # Để trong instance thay vì global để test có thể override nếu cần.
        builder = ClaimBuilder(registry=self.registry)
        self.claim_extractor = (
            ClaimExtractor(parser=self.semantic_parser, builder=builder)
            if self.semantic_parser is not None
            else ClaimExtractor(builder=builder)
        )
        self.worthiness_policy = MemoryWorthinessPolicy()
        self.retention_policy = MemoryRetentionPolicy()

    def set_semantic_parser(self, parser: Any) -> None:
        """Gắn parser model.gguf/hybrid sau khi khởi tạo runtime.

        Parser chỉ đọc nghĩa và trả MeaningFrame. Nó không được commit memory.
        Đây là điểm nối sạch cho model local của bạn, thay vì nhét llama call
        vào giữa policy rồi lại sinh thêm món spaghetti có GPU.
        """

        self.semantic_parser = parser
        self.claim_extractor = ClaimExtractor(parser=parser, builder=ClaimBuilder(registry=self.registry))

    def decide_from_user_turn(
        self,
        user_text: str,
        *,
        turn_id: str | None = None,
        allow_candidate: bool = True,
        llm_structured: Mapping[str, Any] | None = None,
    ) -> list[MemoryCommitDecision]:
        raw = str(user_text or "").strip()
        if not raw:
            return []
        # Permission/revoke là state phiên, không phải fact bền. Chặn ngay ở cổng
        # chung để cả fast path lẫn semantic digest đều không lén ghi nó vào memory.
        if should_exclude_from_persistent_memory(raw):
            return []

        norm = normalize_vi(raw)
        if not norm:
            return []
        is_question = _looks_like_question(norm, raw)
        if is_question and self.semantic_parser is None:
            return []

        importance, importance_reasons = _explicit_importance(norm)
        decisions: list[MemoryCommitDecision] = []

        # Pipeline chuẩn: user_text -> MeaningFrame -> SemanticClaim -> Worthiness -> Retention.
        # Parser/model chỉ đọc nghĩa; judgement/retention mới quyết định layer lưu.
        # Với câu hỏi recall/session, pipeline có thể nhận ra `asked_about`, nhưng
        # không được tạo long-term memory. SessionTurnLedger xử lý phần đó.
        if self.enable_semantic_claim_pipeline:
            claim_decisions = self._decide_with_semantic_claims(raw, turn_id, llm_structured=llm_structured)
            if claim_decisions and not is_question:
                return self._dedupe(claim_decisions)
            if is_question:
                return []

        if is_question:
            return []

        for builder in (
            self._extract_user_name,
            self._extract_birth_year,
            self._extract_birth_month,
            self._extract_age_observed,
            self._extract_current_residence,
            self._extract_temporal_residence,
            self._extract_user_convention,
            self._extract_response_style,
            self._extract_memory_test_hint,
            self._extract_general_preference,
        ):
            decision = builder(raw, norm, turn_id)
            if decision is not None and decision.confidence >= self.min_confidence:
                decisions.append(decision)

        if decisions:
            return self._dedupe(decisions)

        if allow_candidate and importance >= self.explicit_candidate_min_importance:
            candidate = self._build_unresolved_candidate(raw, norm, turn_id, importance, importance_reasons)
            return [candidate] if candidate is not None else []

        return []


    # ------------------------------------------------------------------
    # SemanticClaim pipeline mới
    # ------------------------------------------------------------------

    def _decide_with_semantic_claims(self, raw: str, turn_id: str | None, *, llm_structured: Mapping[str, Any] | None = None) -> list[MemoryCommitDecision]:
        """Chạy pipeline claim/ontology trước các rule legacy.

        Nếu claim đến từ AI classification (extraction_method=ai_classification),
        commit_hint được ưu tiên để route đúng store mà không qua Worthiness/Retention.
        Nếu claim đến từ predicate cũ, vẫn qua Worthiness/Retention như cũ.
        """

        extracted = self.claim_extractor.extract(raw, evidence_ids=_evidence(turn_id), llm_structured=llm_structured)
        out: list[MemoryCommitDecision] = []
        for item in extracted:
            claim = item.claim

            # Path mới: AI đã phân loại xong, dùng commit_hint trực tiếp
            if item.extraction_method == "ai_classification":
                decision = self._decision_from_ai_classification(item, claim)
                if decision is not None and decision.confidence >= max(0.45, self.min_confidence - 0.15):
                    out.append(decision)
                continue

            # Path cũ: qua Worthiness/Retention
            worthiness = self.worthiness_policy.decide(claim)
            retention = self.retention_policy.decide(claim, worthiness)
            if not retention.accept:
                continue
            decision = self._decision_from_semantic_claim(claim, retention)
            if decision is not None and decision.confidence >= self.min_confidence:
                out.append(decision)
        return out

    def _decision_from_ai_classification(self, extracted: Any, claim: Any) -> "MemoryCommitDecision | None":
        """Tạo MemoryCommitDecision trực tiếp từ AI classification.

        AI đã quyết định storage_target, importance, decay — không cần qua map cứng.
        """
        qualifiers = dict(getattr(claim, "qualifiers", {}) or {})
        memory_type = str(qualifiers.get("memory_type") or "context")
        storage_target = str(qualifiers.get("storage_target") or "semantic")
        importance = float(qualifiers.get("importance") or 0.5)
        decay_policy = str(qualifiers.get("decay_policy") or "normal")
        surface_fn = str(qualifiers.get("surface_fn") or "background")
        fact_key = str(qualifiers.get("fact_key") or "")
        value = qualifiers.get("value")
        why = str(qualifiers.get("why") or "")

        if fact_key in {"user.name", "user.nickname"}:
            if value is None or not is_valid_name_claim(
                value,
                str(getattr(claim, "evidence_text", "") or ""),
                nickname=(fact_key == "user.nickname"),
            ):
                return None

        # commit_hint → MemoryTarget
        target_map = {
            "semantic": MemoryTarget.SEMANTIC,
            "episodic": MemoryTarget.EPISODIC,
            "procedural": MemoryTarget.PROCEDURAL,
            "affective_residue": MemoryTarget.AFFECTIVE if hasattr(MemoryTarget, "AFFECTIVE") else MemoryTarget.SEMANTIC,
            "relational": MemoryTarget.RELATIONAL if hasattr(MemoryTarget, "RELATIONAL") else MemoryTarget.SEMANTIC,
            "candidate": MemoryTarget.CANDIDATE,
        }
        target = target_map.get(storage_target, MemoryTarget.SEMANTIC)

        # surface_fn → surface_mode
        from .memory_models import SurfaceMode
        surface_map = {
            "can_answer": SurfaceMode.EXACT,
            "background": SurfaceMode.SEMANTIC,
            "never": SurfaceMode.UNCERTAIN,
        }
        surface_mode = surface_map.get(surface_fn, SurfaceMode.SEMANTIC)

        # decay → DecayPolicy
        from .memory_models import DecayPolicy
        decay_obj_map = {
            "slow": DecayPolicy.STABLE,
            "normal": DecayPolicy.DECAYING,
            "fast": DecayPolicy.SHORT,
            "session_only": DecayPolicy.SESSION,
            "candidate": DecayPolicy.CANDIDATE,
        }
        decay_obj = decay_obj_map.get(decay_policy, DecayPolicy.DECAYING)

        metadata = {
            "fact_key": fact_key,
            "value": value if value is not None else claim.object_text,
            "memory_type": memory_type,
            "storage_target": storage_target,
            "importance": importance,
            "decay": qualifiers.get("decay"),
            "surface_fn": surface_fn,
            "why": why,
            "raw_user_text": claim.evidence_text,
            "extraction": "ai_classification",
            "semantic_source": "ai_classification",
            "source_grounded": True,
            "predicate": claim.predicate,
        }
        if target == MemoryTarget.CANDIDATE:
            metadata["extraction_status"] = "unresolved"
            metadata["needs_consolidation"] = True

        # Kind
        from .memory_models import MemoryKind
        kind_map = {
            "fact": MemoryKind.USER_GENERAL_TOPIC,
            "knowledge": MemoryKind.USER_GENERAL_TOPIC,
            "convention": MemoryKind.USER_CONVENTION,
            "feedback": MemoryKind.USER_RESPONSE_STYLE,
        }
        kind = kind_map.get(memory_type, MemoryKind.MEMORY_CANDIDATE if target == MemoryTarget.CANDIDATE else MemoryKind.USER_GENERAL_TOPIC)
        if fact_key == "user.name":
            kind = MemoryKind.USER_NAME
        elif fact_key == "user.response_style":
            kind = MemoryKind.USER_RESPONSE_STYLE

        return MemoryCommitDecision(
            target=target,
            kind=kind,
            claim=claim.object_text or claim_text if (claim_text := str(getattr(claim, "claim_text", "") or "")) else claim.object_text,
            surface=str(value or claim.object_text),
            confidence=float(claim.confidence),
            importance=importance,
            source=claim.source,
            permission="user_provided",
            surface_mode=surface_mode,
            decay=decay_obj,
            evidence=list(claim.evidence_ids),
            metadata=metadata,
        )

    def _decision_from_semantic_claim(self, claim: SemanticClaim, retention: Any) -> MemoryCommitDecision | None:
        target_map = {
            "semantic_memory": MemoryTarget.SEMANTIC,
            "procedural_memory": MemoryTarget.PROCEDURAL,
            "episodic_memory": MemoryTarget.EPISODIC,
            "repair_memory": MemoryTarget.REPAIR,
            "memory_candidate": MemoryTarget.CANDIDATE,
        }
        target = target_map.get(str(retention.target_layer or ""))
        if target is None:
            return None

        fact_key = fact_key_for_predicate(claim.predicate)
        spec = get_predicate(claim.predicate)
        aliases = list(predicate_aliases(claim.predicate))
        value: Any = claim.object_value if claim.object_value is not None else claim.object_text
        if claim.predicate in {"name_is", "nickname_is"}:
            if not is_valid_name_claim(
                value,
                str(claim.evidence_text or ""),
                nickname=(claim.predicate == "nickname_is"),
            ):
                return None
        metadata = {
            "fact_key": fact_key,
            "value": value,
            "aliases": aliases,
            "raw_user_text": claim.evidence_text,
            "semantic_claim": claim.to_dict(),
            "predicate": claim.predicate,
            "subject": claim.subject,
            "object_type": claim.object_type,
            "assertion": claim.assertion,
            "modality": claim.modality,
            "temporal": claim.temporal,
            "scope": claim.scope,
            "source_grounded": True,
            "semantic_source": "semantic_claim_extractor",
            "extraction": "semantic_claim_pipeline",
            "importance": float(retention.importance),
            **retention_metadata(retention),
        }

        if target == MemoryTarget.CANDIDATE:
            qualifiers = dict(getattr(claim, "qualifiers", {}) or {})
            frame = dict(qualifiers.get("meaning_frame") or {})
            slots = dict(frame.get("slots") or {})
            raw_keys = (
                slots.get("candidate_fact_keys")
                or slots.get("fact_keys")
                or qualifiers.get("candidate_fact_keys")
                or ([fact_key] if fact_key else [])
            )
            if isinstance(raw_keys, str):
                candidate_keys = [raw_keys]
            else:
                try:
                    candidate_keys = list(raw_keys or [])
                except Exception:
                    candidate_keys = []
            metadata.update(
                {
                    "candidate_fact_keys": list(dict.fromkeys(str(x) for x in candidate_keys if str(x).strip()))[:8],
                    "domain_guess": str(slots.get("domain_guess") or qualifiers.get("domain_guess") or claim.predicate or "unknown"),
                    "extraction_status": "unresolved",
                    "needs_consolidation": True,
                    "public_ack": "held_trace_not_stable_memory",
                }
            )

        claim_text = self._surface_claim_for_semantic_claim(claim)
        kind = self._memory_kind_for_semantic_claim(claim)
        surface_mode = SurfaceMode.EXACT if claim.predicate in {"name_is", "nickname_is", "birth_month_is", "birth_year_is"} else SurfaceMode.SEMANTIC
        decay = self._decay_for_retention(str(retention.decay_policy))

        return MemoryCommitDecision(
            target=target,
            kind=kind,
            claim=claim_text,
            surface=str(claim.object_text),
            confidence=float(claim.confidence),
            importance=float(retention.importance),
            source=claim.source,
            permission="user_provided",
            surface_mode=surface_mode,
            decay=decay,
            evidence=list(claim.evidence_ids),
            metadata=metadata,
        )

    def _surface_claim_for_semantic_claim(self, claim: SemanticClaim) -> str:
        obj = claim.object_text
        if claim.predicate == "name_is":
            return f"User's name is {obj}"
        if claim.predicate == "nickname_is":
            return f"User's nickname is {obj}"
        if claim.predicate == "birth_month_is":
            return f"User's birth month is {obj}"
        if claim.predicate == "birth_year_is":
            return f"User was born in {obj}."
        if claim.predicate == "age_observed_is":
            return f"Người dùng khai báo đang {obj} tuổi tại thời điểm nói."
        if claim.predicate == "current_location_is":
            return f"Người dùng nói hiện đang ở {obj}."
        if claim.predicate == "living_location_is":
            return f"Người dùng nói đang sống ở {obj}."
        if claim.predicate == "hometown_is":
            return f"Quê của người dùng là {obj}."
        if claim.predicate == "preference_location":
            verb = "không thích" if claim.assertion == "denied" else "thích"
            return f"Người dùng {verb} địa điểm {obj}."
        if claim.predicate == "study_field_is":
            return f"Người dùng nói đang học/lĩnh vực học là {obj}."
        if claim.predicate == "response_style_prefers":
            return f"Người dùng nêu preference về cách trả lời: {obj}"
        if claim.predicate == "general_preference":
            return f"Người dùng nêu preference: {obj}"
        if claim.predicate == "remember_unresolved":
            return f"Unresolved memory candidate: {obj}"
        return f"{claim.subject}.{claim.predicate}: {obj}"

    def _memory_kind_for_semantic_claim(self, claim: SemanticClaim) -> MemoryKind:
        if claim.predicate == "name_is":
            return MemoryKind.USER_NAME
        if claim.predicate == "nickname_is":
            return getattr(MemoryKind, "USER_NICKNAME", MemoryKind.USER_NAME)
        if claim.predicate == "birth_month_is":
            return MemoryKind.USER_BIRTH_MONTH
        if claim.predicate == "study_field_is":
            return getattr(MemoryKind, "USER_STUDY_FIELD", MemoryKind.USER_GENERAL_TOPIC)
        if claim.predicate == "response_style_prefers":
            return MemoryKind.USER_RESPONSE_STYLE
        if claim.predicate == "remember_unresolved":
            return MemoryKind.MEMORY_CANDIDATE
        return MemoryKind.USER_GENERAL_TOPIC

    def _decay_for_retention(self, decay_policy: str) -> DecayPolicy:
        if decay_policy == "slow":
            return DecayPolicy.STABLE
        if decay_policy == "fast":
            return DecayPolicy.SHORT
        if decay_policy == "session_only":
            return DecayPolicy.SESSION
        if decay_policy == "candidate":
            return DecayPolicy.CANDIDATE
        return DecayPolicy.DECAYING

    # ------------------------------------------------------------------
    # Specific extractors
    # ------------------------------------------------------------------

    def _extract_user_name(self, raw: str, norm: str, turn_id: str | None) -> MemoryCommitDecision | None:
        value = _extract_with_patterns(
            raw,
            (
                r"(?:^|\b)(?:tôi|toi|mình|minh|tớ|to|tui|em)\s+t[eê]n\s+(?:đầy\s+đủ|day\s+du)\s+(?:là|la)\s+(.+?)(?=$|[.,;!?]|\s+(?:và|va|nhưng|nhung)\b)",
                r"(?:^|\b)t[eê]n\s+(?:thật\s+)?(?:đầy\s+đủ|day\s+du)\s+(?:của\s+)?(?:tôi|toi|mình|minh|tớ|to|tui|em)\s+(?:là|la)\s+(.+?)(?=$|[.,;!?]|\s+(?:và|va|nhưng|nhung)\b)",
                r"(?:^|\b)(?:tôi|toi|mình|minh|tớ|to|tui|em)\s+t[eê]n\s+(?:là|la)\s+(.+?)(?=$|[.,;!?]|\s+(?:và|va|nhưng|nhung)\b)",
                # Dạng rút gọn trong chat: "tôi tên Tín". Negative lookahead chặn
                # các câu hỏi như "tôi tên gì" / "tôi tên là gì" để không lưu rác.
                r"(?:^|\b)(?:tôi|toi|mình|minh|tớ|to|tui|em)\s+t[eê]n\s+(?!(?:là|la|gì|gi)\b)(.+?)(?=$|[.,;!?]|\s+(?:và|va|nhưng|nhung)\b)",
                r"(?:^|\b)t[eê]n\s+(?:của\s+)?(?:tôi|toi|mình|minh|tớ|to|tui|em)\s+(?:là|la)\s+(.+?)(?=$|[.,;!?]|\s+(?:và|va|nhưng|nhung)\b)",
                r"(?:^|\b)(?:gọi|goi|cứ gọi|cu goi)\s+(?:tôi|toi|mình|minh|tớ|to|tui|em)\s+(?:là|la)\s+(.+?)(?=$|[.,;!?]|\s+(?:và|va|nhưng|nhung)\b)",
                r"(?:^|\b)(?:tôi|toi|mình|minh|tớ|to|tui|em)\s+mới\s+(?:đổi|doi)\s+t[eê]n\s+(?:sang|thành|thanh)\s+(.+?)(?=$|[.,;!?]|\s+(?:và|va|nhưng|nhung|sáng|sang nay|hôm|hom)\b)",
                r"(?:^|\b)(?:đổi|doi)\s+t[eê]n\s+(?:sang|thành|thanh)\s+(.+?)(?=$|[.,;!?]|\s+(?:và|va|nhưng|nhung|sáng|sang nay|hôm|hom)\b)",
                r"(?:^|\b)(?:từ|tu)\s+giờ\s+(?:cứ\s+|cu\s+)?(?:gọi|goi)\s+(?:tôi|toi|mình|minh|tớ|to|tui|em)\s+(?:là|la)\s+(.+?)(?=$|[.,;!?]|\s+(?:và|va|nhưng|nhung)\b)",
                r"(?:^|\b)(?:nhớ|nho|hãy nhớ|hay nho)\s+(?:là|la|rằng|rang)\s+(?:tôi|toi|mình|minh|tớ|to|tui|em)\s+(?:t[eê]n\s+(?:là|la)|(?:là|la))\s+(.+?)(?=$|[.,;!?]|\s+(?:và|va|nhưng|nhung)\b)",
                r"(?:^|\b)my\s+name\s+is\s+(.+?)(?=$|[.,;!?])",
                r"(?:^|\b)call\s+me\s+(.+?)(?=$|[.,;!?])",
            ),
        )
        if not value or len(value) > 60 or not is_valid_name_claim(value, raw):
            return None

        return MemoryCommitDecision(
            target=MemoryTarget.SEMANTIC,
            kind=MemoryKind.USER_NAME,
            claim=f"User's name is {value}",
            surface=value,
            confidence=0.94,
            importance=0.96,
            surface_mode=SurfaceMode.EXACT,
            decay=DecayPolicy.STABLE,
            evidence=_evidence(turn_id),
            metadata={
                "fact_key": "user.name",
                "value": value,
                "aliases": ["tên", "ten", "name", "preferred name", "gọi tên", "goi ten"],
                "raw_user_text": raw,
                "extraction": "user_name_rule",
            },
        )

    def _extract_birth_year(self, raw: str, norm: str, turn_id: str | None) -> MemoryCommitDecision | None:
        year: int | None = None

        match = re.search(r"\b(?:toi|minh|tui|em|i)\s+(?:sinh\s+nam|sinh)\s+((?:19|20)\d{2}|2k\d{1,2})\b", norm)
        if not match:
            match = re.search(r"\b(?:sinh\s+nam|sinh)\s+((?:19|20)\d{2}|2k\d{1,2})\b", norm)
        if not match and _has_self_marker(norm):
            match = re.search(r"\b(2k\d{1,2})\b", norm)

        if match:
            token = match.group(1)
            year = _year_from_2k(token) if token.startswith("2k") else int(token)

        if year is None or not (1900 <= year <= 2100):
            return None

        return MemoryCommitDecision(
            target=MemoryTarget.SEMANTIC,
            kind=MemoryKind.UNKNOWN,
            claim=f"User was born in {year}.",
            surface=str(year),
            confidence=0.92,
            importance=0.92,
            surface_mode=SurfaceMode.EXACT,
            decay=DecayPolicy.STABLE,
            evidence=_evidence(turn_id),
            metadata={
                "fact_key": "user.birth_year",
                "value": year,
                "aliases": ["năm sinh", "nam sinh", "birth year", "2k", "tuổi", "tuoi"],
                "raw_user_text": raw,
                "extraction": "birth_year_rule",
            },
        )

    def _extract_birth_month(self, raw: str, norm: str, turn_id: str | None) -> MemoryCommitDecision | None:
        value = ""
        match = re.search(r"\b(?:toi|minh|tui|em|i)\s+sinh\s+thang\s+([a-z0-9 ]{1,16})\b", norm)
        if not match:
            match = re.search(r"\bthang\s+sinh\s+(?:cua\s+)?(?:toi|minh|tui|em)\s+(?:la\s+)?([a-z0-9 ]{1,16})\b", norm)
        if match:
            value = match.group(1)

        month = _bounded_month(value)
        if month is None:
            return None

        return MemoryCommitDecision(
            target=MemoryTarget.SEMANTIC,
            kind=MemoryKind.USER_BIRTH_MONTH,
            claim=f"User's birth month is {month}",
            surface=str(month),
            confidence=0.88,
            importance=0.86,
            surface_mode=SurfaceMode.EXACT,
            decay=DecayPolicy.STABLE,
            evidence=_evidence(turn_id),
            metadata={
                "fact_key": "user.birth_month",
                "value": month,
                "aliases": ["tháng sinh", "thang sinh", "birth month", "birthday"],
                "raw_user_text": raw,
                "extraction": "birth_month_rule",
            },
        )

    def _extract_age_observed(self, raw: str, norm: str, turn_id: str | None) -> MemoryCommitDecision | None:
        match = re.search(r"\b(?:toi|minh|tui|em|i)\s+(?:nam\s+nay\s+)?(?:duoc\s+)?(\d{1,2})\s+tuoi\b", norm)
        if not match:
            match = re.search(r"\b(?:i\s+am|im|i'm)\s+(\d{1,2})\s*(?:years?\s+old)?\b", norm)
        if not match:
            return None

        age = int(match.group(1))
        if not (3 <= age <= 120):
            return None

        return MemoryCommitDecision(
            target=MemoryTarget.SEMANTIC,
            kind=MemoryKind.UNKNOWN,
            claim=f"Người dùng khai báo đang {age} tuổi tại thời điểm nói.",
            surface=str(age),
            confidence=0.82,
            importance=0.78,
            surface_mode=SurfaceMode.UNCERTAIN,
            decay=DecayPolicy.DECAYING,
            evidence=_evidence(turn_id),
            metadata={
                "fact_key": "user.age_observed",
                "value": age,
                "aliases": ["tuổi", "tuoi", "age", "years old"],
                "raw_user_text": raw,
                "extraction": "age_observed_rule",
                "time_bound": True,
            },
        )

    def _extract_current_residence(self, raw: str, norm: str, turn_id: str | None) -> MemoryCommitDecision | None:
        # Chặn past/future để không overwrite current residence.
        if re.search(r"\b(hom\s+qua|ngay\s+mai|hai\s+ngay|2\s+ngay|tuan\s+truoc|nam\s+truoc|truoc\s+day|sap|se)\b", norm):
            return None

        value = _extract_with_patterns(
            raw,
            (
                r"(?:^|\b)(?:tôi|toi|mình|minh|tui|em)\s+(?:hiện\s+)?(?:đang\s+)?(?:sống|song|ở|o|cư\s+trú|cu\s+tru)\s+(?:tại|tai|ở|o)?\s+(.+?)(?=$|[.,;!?]|\s+(?:và|va|nhưng|nhung)\b)",
                r"(?:^|\b)hiện\s+(?:tôi|toi|mình|minh|tui|em)\s+(?:đang\s+)?(?:sống|song|ở|o|cư\s+trú|cu\s+tru)\s+(?:tại|tai|ở|o)?\s+(.+?)(?=$|[.,;!?])",
            ),
        )
        if not value or len(value) > 90:
            return None

        return MemoryCommitDecision(
            target=MemoryTarget.SEMANTIC,
            kind=MemoryKind.UNKNOWN,
            claim=f"Người dùng hiện đang sống ở {value}.",
            surface=value,
            confidence=0.86,
            importance=0.82,
            surface_mode=SurfaceMode.SEMANTIC,
            decay=DecayPolicy.DECAYING,
            evidence=_evidence(turn_id),
            metadata={
                "fact_key": "user.living_location",
                "value": value,
                "aliases": ["nơi ở hiện tại", "noi o hien tai", "current residence", "đang sống ở đâu", "dang song o dau", "living location"],
                "predicate": "living_location_is",
                "raw_user_text": raw,
                "extraction": "current_residence_rule",
                "privacy_level": "private",
                "time_bound": True,
            },
        )

    def _extract_temporal_residence(self, raw: str, norm: str, turn_id: str | None) -> MemoryCommitDecision | None:
        if not any(marker in norm for marker in ("truoc", "hom qua", "ngay mai", "sap", " se ", "tuong lai", "2 ngay", "hai ngay")):
            return None
        if not any(marker in norm for marker in ("song", "cu tru", " o ", " tai ")):
            return None
        if not _has_self_marker(norm):
            return None

        # Lấy location sau cụm tại/ở cuối câu. Không hoàn hảo, nhưng đủ làm episodic trace.
        value = _extract_with_patterns(
            raw,
            (
                r"(?:ở|o|tại|tai)\s+(.+?)(?=$|[.,;!?])",
            ),
        )
        value = _clean_value(value)
        if not value or len(value) > 90:
            return None

        if any(marker in norm for marker in ("ngay mai", "sap", " se ", "2 ngay", "hai ngay", "tuong lai")):
            time_label = "future"
            claim = f"Người dùng nói sẽ sống/cư trú ở {value} trong một mốc tương lai."
        else:
            time_label = "past"
            claim = f"Người dùng nói từng sống/cư trú ở {value} trong một mốc quá khứ."

        return MemoryCommitDecision(
            target=MemoryTarget.EPISODIC,
            kind=MemoryKind.USER_GENERAL_TOPIC,
            claim=claim,
            surface=value,
            confidence=0.76,
            importance=0.70,
            surface_mode=SurfaceMode.GIST,
            decay=DecayPolicy.DECAYING,
            evidence=_evidence(turn_id),
            metadata={
                "fact_key": f"user.{time_label}_residence_claim",
                "value": value,
                "time_label": time_label,
                "raw_user_text": raw,
                "extraction": "temporal_residence_rule",
                "privacy_level": "private",
            },
        )

    def _extract_user_convention(self, raw: str, norm: str, turn_id: str | None) -> MemoryCommitDecision | None:
        if not _looks_like_convention(norm):
            return None

        trigger = ""
        handling_rule = ""

        quoted = re.search(
            r"(?:nếu|neu|từ giờ|tu gio|sau này|sau nay|về sau|ve sau).*?[\"'“”‘’`](.+?)[\"'“”‘’`].*?(?:hiểu|hieu|tức là|tuc la|nghĩa là|nghia la|coi là|coi la|xem là|xem la)\s+(.+?)(?=$|[.!?])",
            raw,
            flags=re.IGNORECASE | re.UNICODE,
        )
        if quoted:
            trigger = _clean_value(quoted.group(1))
            handling_rule = _clean_value(quoted.group(2))
        else:
            match = re.search(
                r"(?:tu gio|sau nay|ve sau)?\s*(?:neu|khi)?\s*(?:toi|minh|tui|em)?\s*(?:noi|hoi|go|nhap)?\s+(.+?)\s+(?:thi\s+)?(?:hieu la|tuc la|nghia la|coi la|xem la|interpret as)\s+(.+)$",
                norm,
                flags=re.IGNORECASE,
            )
            if match:
                trigger = _clean_value(match.group(1))
                handling_rule = _clean_value(match.group(2))

        if not trigger or not handling_rule:
            return None
        if len(trigger) > 100 or len(handling_rule) > 240:
            return None

        fact_key = f"user.convention.{_slug_key(trigger)}"
        return MemoryCommitDecision(
            target=MemoryTarget.PROCEDURAL,
            kind=MemoryKind.USER_CONVENTION,
            claim=f"Khi người dùng dùng trigger '{trigger}', hiểu/xử lý là: {handling_rule}.",
            surface=f"{trigger} -> {handling_rule}",
            confidence=0.86,
            importance=0.92,
            surface_mode=SurfaceMode.SEMANTIC,
            decay=DecayPolicy.STABLE,
            evidence=_evidence(turn_id),
            metadata={
                "fact_key": fact_key,
                "trigger": trigger,
                "handling_rule": handling_rule,
                "value": {"trigger": trigger, "handling_rule": handling_rule},
                "aliases": [trigger, "quy ước", "quy uoc", "convention", "handling rule"],
                "raw_user_text": raw,
                "extraction": "user_convention_rule",
            },
        )

    def _extract_response_style(self, raw: str, norm: str, turn_id: str | None) -> MemoryCommitDecision | None:
        if not any(marker in norm for marker in ("tra loi", "giai thich", "noi ngan", "noi dai", "viet ngan", "viet dai", "chu thich", "comment")):
            return None
        if not any(marker in norm for marker in ("toi thich", "minh thich", "toi muon", "hay", "dung", "nen", "tu gio", "ve sau")):
            return None

        return MemoryCommitDecision(
            target=MemoryTarget.SEMANTIC,
            kind=MemoryKind.USER_RESPONSE_STYLE,
            claim=f"Người dùng nêu preference về cách trả lời: {raw}",
            surface=raw,
            confidence=0.74,
            importance=0.74,
            surface_mode=SurfaceMode.GIST,
            decay=DecayPolicy.STABLE,
            evidence=_evidence(turn_id),
            metadata={
                "fact_key": "user.response_style",
                "value": raw,
                "aliases": ["response style", "reply style", "cách trả lời", "cach tra loi"],
                "raw_user_text": raw,
                "extraction": "response_style_rule",
            },
        )

    def _extract_memory_test_hint(self, raw: str, norm: str, turn_id: str | None) -> MemoryCommitDecision | None:
        if not any(marker in norm for marker in ("test tri nho", "test memory", "nho lai", "hoi lai")):
            return None
        return MemoryCommitDecision(
            target=MemoryTarget.EPISODIC,
            kind=MemoryKind.SESSION_MEMORY_TEST,
            claim=f"Người dùng đang kiểm tra hành vi trí nhớ/recall: {raw}",
            surface="memory_test",
            confidence=0.70,
            importance=0.62,
            surface_mode=SurfaceMode.GIST,
            decay=DecayPolicy.SESSION,
            evidence=_evidence(turn_id),
            metadata={
                "raw_user_text": raw,
                "extraction": "memory_test_hint_rule",
            },
        )

    def _extract_general_preference(self, raw: str, norm: str, turn_id: str | None) -> MemoryCommitDecision | None:
        if not any(marker in norm for marker in ("toi thich", "toi khong thich", "minh thich", "minh khong thich", "i prefer", "i dont like", "i don't like")):
            return None
        # Nếu đã là preference về cách trả lời, để _extract_response_style xử lý.
        # Không commit hai semantic item cho cùng một câu, vì bộ nhớ không cần tự nhân bản.
        if any(marker in norm for marker in ("tra loi", "giai thich", "noi ngan", "noi dai", "viet ngan", "viet dai", "chu thich", "comment")):
            return None

        return MemoryCommitDecision(
            target=MemoryTarget.SEMANTIC,
            kind=MemoryKind.USER_GENERAL_TOPIC,
            claim=f"Người dùng nêu preference: {raw}",
            surface=raw,
            confidence=0.68,
            importance=0.66,
            surface_mode=SurfaceMode.GIST,
            decay=DecayPolicy.DECAYING,
            evidence=_evidence(turn_id),
            metadata={
                "fact_key": "user.preference.general",
                "value": raw,
                "aliases": ["preference", "sở thích", "so thich", "không thích", "khong thich"],
                "raw_user_text": raw,
                "extraction": "general_preference_rule",
            },
        )

    def _build_unresolved_candidate(
        self,
        raw: str,
        norm: str,
        turn_id: str | None,
        importance: float,
        importance_reasons: list[str],
    ) -> MemoryCommitDecision | None:
        raw_text = _clean_value(raw)
        if not raw_text:
            return None
        domain = _guess_domain(norm)
        candidate_keys = _candidate_fact_keys(norm, domain)
        confidence = max(0.60, min(0.78, importance - 0.10))
        return MemoryCommitDecision(
            target=MemoryTarget.CANDIDATE,
            kind=MemoryKind.MEMORY_CANDIDATE,
            claim=f"Unresolved memory candidate: {raw_text}",
            surface=raw_text,
            confidence=confidence,
            importance=importance,
            surface_mode=SurfaceMode.UNCERTAIN,
            decay=DecayPolicy.CANDIDATE,
            evidence=_evidence(turn_id),
            metadata={
                "raw_user_text": raw_text,
                "domain_guess": domain,
                "candidate_fact_keys": candidate_keys,
                "extraction_status": "unresolved",
                "needs_consolidation": True,
                "importance_reasons": list(importance_reasons),
                "fallback_reason": "explicit_memory_marker_without_structured_extraction",
                "public_ack": "held_trace_not_stable_memory",
            },
        )

    # ------------------------------------------------------------------
    # Dedupe
    # ------------------------------------------------------------------

    def _dedupe(self, decisions: list[MemoryCommitDecision]) -> list[MemoryCommitDecision]:
        out: list[MemoryCommitDecision] = []
        seen: set[tuple[str, str, str]] = set()
        for decision in decisions:
            fact_key = str(decision.metadata.get("fact_key", ""))
            value = normalize_vi(str(decision.metadata.get("value", decision.surface)))
            key = (decision.target.value, fact_key or decision.kind.value, value)
            if key in seen:
                continue
            seen.add(key)
            out.append(decision)
        return out


# Tên mới đúng nghĩa hơn. Giữ MemoryCommitPolicy để code cũ không gãy.
MemoryIntakePolicy = MemoryCommitPolicy

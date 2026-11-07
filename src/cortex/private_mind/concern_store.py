# -*- coding: utf-8 -*-
"""
cortex/private_mind/concern_store.py
====================================

Kho lưu PrivateConcern.

ConcernStore là phần bị thiếu lớn nhất của private cognition: nó giữ lại thứ
runtime còn bận tâm qua nhiều tick, thay vì để mỗi mảnh suy nghĩ chết ngay sau
một log line. Không có store này, ERIS chỉ có trí nhớ cá vàng đội mũ kiến trúc.
"""

from __future__ import annotations
from core.di import resolve_dependency

import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import PrivateConcern, ThoughtFragment, compact_text, stable_id, to_plain, clamp01
from cortex.rumination_dynamics import derive_rumination_state


def _locked(method):
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


# Tiền tố "văn phong evaluator" hay bám vào theme — tước để so khớp phần lõi.
_NOISE_PREFIXES = (
    "người dùng liên tục lặp lại mối lo ngại về ",
    "người dùng thể hiện sự lo ngại sâu sắc về ",
    "người dùng thể hiện sự lo ngại sâu sắc và lặp lại về ",
    "người dùng lặp lại nhiều lần về ",
    "hệ thống có nguy cơ cao về ",
)

# Stopword (đã bỏ dấu, thường) gồm đại từ / động từ tri giác / bổ nghĩa / hư từ.
# Mục tiêu: sau khi lọc chỉ còn các TOKEN KHÁI NIỆM để hai câu paraphrase cùng ý
# (vd "tôi thấy khoảng cách trong cách nói" vs "tôi tạo khoảng cách vô hình") lộ ra
# phần lõi trùng nhau, thay vì chìm trong từ đệm ngôi-thứ-nhất.
_CONCERN_STOPWORDS = frozenset({
    "toi", "minh", "ban", "ban", "ho", "nguoi", "dung", "chung", "ta",
    "cam", "thay", "giac", "nhan", "biet", "nghi",
    "thuc", "su", "rat", "qua", "lam", "kha", "hoi", "that",
    "la", "mot", "cac", "nhung", "nay", "do", "kia", "moi", "ca",
    "duong", "nhu", "dang", "se", "da", "con", "cung", "van", "luon", "hay", "dan",
    "rang", "khi", "ma", "va", "voi", "trong", "ve", "cua", "cho", "den", "tu", "ra", "vao", "theo",
    "cach", "kieu", "chi", "co", "khong", "duoc", "bi", "gi", "nao", "the", "hon",
    "lien", "tuc", "lap", "lai", "nhieu", "lan", "hien", "sau", "sac", "nguy", "cao",
    "user", "eris", "runtime", "this", "that",
})

# Ngưỡng overlap-coefficient tối thiểu để coi hai theme là CÙNG mối bận tâm.
# Đủ thấp để gom các câu paraphrase dài cùng ý (kết nối/khoảng cách/cảm xúc),
# nhưng vẫn cần ≥2 khái niệm chung nên không gộp bừa hai lo lắng chỉ chạm 1 từ.
_CONCEPT_MATCH_MIN = 0.34

# V42: event-driven ingress dùng ngưỡng riêng, chặt hơn rất nhiều. Semantic gần
# nhau chỉ đủ để tạo LINK, không đủ để hai câu hỏi khác nhau biến thành cùng một
# mega-concern. Merge event chỉ xảy ra khi hai theme thực sự gần đồng nhất hoặc
# có provenance/evidence chung độc lập.
_STRICT_EVENT_CONCEPT_MIN = 0.78
_STRICT_EVENT_JACCARD_MIN = 0.46
_RELATED_CONCEPT_MIN = 0.34


def _ascii_fold(text: str) -> str:
    """Bỏ dấu tiếng Việt + 'đ'→'d' để token hoá ổn định, không lệ thuộc dấu."""
    s = (text or "").replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def _strip_noise_prefix(theme_lower: str) -> str:
    for prefix in _NOISE_PREFIXES:
        if theme_lower.startswith(prefix):
            return theme_lower[len(prefix):]
    return theme_lower


def _concept_signature(theme_lower: str) -> frozenset[str]:
    """Tập token KHÁI NIỆM của theme: bỏ dấu, bỏ stopword, giữ token ≥3 ký tự."""
    folded = _ascii_fold(theme_lower).lower()
    folded = re.sub(r"[^a-z0-9\s]", " ", folded)
    return frozenset(
        tok for tok in folded.split()
        if len(tok) >= 3 and tok not in _CONCERN_STOPWORDS
    )


@dataclass(slots=True)
class ConcernStoreConfig:
    max_concerns: int = 24  # Giảm từ 96 → 24: concern thật không cần nhiều hơn thế
    stale_after_seconds: float = 60.0 * 60.0 * 24.0 * 14.0
    autosave: bool = True
    # Q1 — vòng đời nguôi: concern đã dịu (watching, áp lực+bất định thấp) đủ lâu
    # thì tự khép lại, để lại dấu "đã nguôi" cho diary/chat thay vì biến mất câm.
    settle_window_seconds: float = 60.0 * 30.0          # dịu liên tục 30 phút → khép
    settle_pressure_max: float = 0.18
    settle_uncertainty_max: float = 0.32
    resolved_visible_seconds: float = 60.0 * 60.0 * 24.0  # "đã nguôi" còn nhắc được 1 ngày
    # Buông khi nghĩ mãi không ra: concern bị nuôi bằng echo (không evidence mới)
    # đủ nhiều lần và áp lực đã nguội → Eris thôi đào sâu, khép lại với dấu khác
    # với "đã nguôi êm". Đây là lối thoát cho rumination, không để nó nằm open lê thê.
    rumination_burnout_echoes: int = 6
    rumination_burnout_pressure: float = 0.34


@dataclass(frozen=True, slots=True)
class ConcernEventReceipt:
    """Kết quả ingest một CognitiveEvent vào concern state.

    ``duplicate`` nghĩa là event này đã được tiêu thụ trước đó và KHÔNG làm
    state thay đổi lần nữa. ``related`` chỉ nối graph, không merge identity.
    """

    concern: PrivateConcern | None
    outcome: str
    mutation: bool = False
    related_ids: tuple[str, ...] = ()


class ConcernStore:
    """CRUD + merge logic cho PrivateConcern."""

    def __init__(
        self,
        *,
        path: str | os.PathLike[str] | None = None,
        config: ConcernStoreConfig | None = None,
        logger: Any | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.config = resolve_dependency(config, lambda: ConcernStoreConfig())
        self.logger = logger
        self._concerns: dict[str, PrivateConcern] = {}
        self._lock = threading.RLock()
        if self.path:
            self.load()

    def __len__(self) -> int:
        return len(self._concerns)

    # ------------------------------------------------------------------
    # Degenerate text guard
    # ------------------------------------------------------------------

    @staticmethod
    def _is_degenerate_theme(theme: str) -> bool:
        """Trả True nếu theme là rác do LLM loop hoặc hallucination.

        Các dấu hiệu:
        - Cùng một token xuất hiện ≥4 lần liên tiếp (ví dụ "lặp lại lặp lại lặp lại lặp")
        - Có chuỗi repeat kỳ lạ của ký tự không phải tiếng Việt thường (block-block, policy-policy)
        - Theme dài hơn 500 ký tự (degenerate thường rất dài)
        - Chứa HTML tags
        - Chứa version string kiểu [v1.0][v1.1]
        """
        if not theme or not theme.strip():
            return True
        if len(theme) > 500:
            return True
        # HTML tags
        if "<" in theme and ">" in theme:
            return True
        # Version string spam
        if theme.count("[v") >= 3:
            return True
        # Repeated token: split by spaces/dashes, find any token repeated ≥4 times consecutively
        tokens = re.split(r"[\s\-/|]+", theme.lower())
        if len(tokens) >= 4:
            for i in range(len(tokens) - 3):
                if tokens[i] and tokens[i] == tokens[i+1] == tokens[i+2] == tokens[i+3]:
                    return True
        # Repeated short phrases (e.g. "rò rỉ rò rỉ rò rỉ")
        words = theme.split()
        if len(words) >= 6:
            for i in range(len(words) - 5):
                phrase2 = " ".join(words[i:i+2])
                if phrase2 == " ".join(words[i+2:i+4]) == " ".join(words[i+4:i+6]):
                    return True
        return False

    @_locked
    def all(self) -> tuple[PrivateConcern, ...]:
        return tuple(sorted(self._concerns.values(), key=lambda c: (c.status != "open", -c.pressure, -c.updated_at)))

    @_locked
    def open_concerns(self, *, limit: int = 12) -> tuple[PrivateConcern, ...]:
        self.prune_and_decay()
        items = [c for c in self._concerns.values() if c.status in {"open", "watching", "blocked"}]
        return tuple(sorted(items, key=lambda c: (c.status != "open", -c.pressure, -c.recurrence, -c.updated_at))[: max(0, limit)])

    def snapshots(self, *, limit: int = 8) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "id": c.id,
                "theme": c.theme,
                "pressure": round(c.pressure, 3),
                "uncertainty": round(c.uncertainty, 3),
                "recurrence": c.recurrence,
                "status": c.status,
                "open_questions": list(c.open_questions[:4]),
                "evidence_ids": list(c.evidence_ids[:8]),
                "future_biases": list(c.future_biases[:4]),
            }
            for c in self.open_concerns(limit=limit)
        )

    def get(self, concern_id: str) -> PrivateConcern | None:
        return self._concerns.get(str(concern_id or ""))

    @_locked
    def add_or_update(self, concern: PrivateConcern) -> PrivateConcern:
        if self._is_degenerate_theme(concern.theme):
            # Trả concern giả không lưu, không phình store.
            return concern
        existing = self._concerns.get(concern.id)
        if existing is None:
            self._concerns[concern.id] = concern
            self._trim()
            self.save()
            return concern

        # Merge mềm thay vì overwrite. Past evidence không bị mất, vì debug mất
        # evidence thì khác gì tự chọc mù rồi kêu thị giác yếu.
        evidence = list(existing.evidence_ids)
        for eid in concern.evidence_ids:
            if eid not in evidence:
                evidence.append(eid)
        questions = list(existing.open_questions)
        for q in concern.open_questions:
            if q not in questions:
                questions.append(q)
        biases = list(existing.future_biases)
        for bias in concern.future_biases:
            if bias not in biases:
                biases.append(bias)
        existing.theme = existing.theme or concern.theme
        existing.evidence_ids = tuple(evidence[:64])
        existing.open_questions = tuple(questions[:12])
        existing.future_biases = tuple(biases[:12])
        existing.pressure = clamp01(max(existing.pressure * 0.92, concern.pressure))
        existing.uncertainty = clamp01(min(existing.uncertainty or 1.0, concern.uncertainty or 1.0))
        existing.recurrence = max(existing.recurrence, concern.recurrence)
        existing.status = concern.status if concern.status != "open" else existing.status
        existing.updated_at = time.time()
        existing.last_reviewed_at = time.time()
        existing.metadata.update(dict(concern.metadata or {}))
        self._trim()
        self.save()
        return existing

    @_locked
    def note_private_return(
        self,
        concern_id: str,
        *,
        material_id: str,
        development_mode: str = "",
        development_score: float = 0.0,
        now: float | None = None,
    ) -> bool:
        """Ghi một lần attention thật sự quay lại concern trong private cognition.

        Đây KHÔNG phải evidence mới và KHÔNG tăng pressure. Nó chỉ cho concern
        hình thành ``stickiness`` và ``rumination_awareness`` từ việc Eris thực sự
        quay lại nghĩ về nó. ``material_id`` là idempotency key để scheduler không
        được giả vờ rằng cùng một thought vừa xảy ra thêm lần nữa.
        """

        cid = str(concern_id or "").strip()
        mid = str(material_id or "").strip()
        concern = self._concerns.get(cid)
        if concern is None or not mid:
            return False
        meta = dict(concern.metadata or {})
        seen = [str(x) for x in (meta.get("private_return_material_ids") or ()) if str(x).strip()]
        if mid in seen:
            return False
        current = float(now or time.time())
        seen.append(mid)
        meta["private_return_material_ids"] = seen[-24:]
        meta["private_return_count"] = int(meta.get("private_return_count", 0) or 0) + 1
        meta["last_private_return_at"] = current

        completion = dict(meta.get("completion") or {})
        state = derive_rumination_state(
            pressure=concern.pressure,
            uncertainty=concern.uncertainty,
            recurrence=concern.recurrence,
            mutation_count=int(meta.get("mutation_count", 0) or 0),
            private_return_count=int(meta.get("private_return_count", 0) or 0),
            echo_streak=int(meta.get("echo_streak", 0) or 0),
            habituation=min(1.0, 0.16 * int(meta.get("surfaced_count", 0) or 0) + 0.10 * int(meta.get("echo_streak", 0) or 0)),
            epistemic_completion=float(completion.get("epistemic", 0.0) or 0.0),
            expressive_completion=float(completion.get("expressive", 0.0) or 0.0),
            relational_completion=float(completion.get("relational", 0.0) or 0.0),
            stored_stickiness=float(meta.get("stickiness", 0.0) or 0.0),
        )
        # Attention quay lại có thể làm concern "lì" hơn một chút, nhưng ceiling
        # mềm giữ nó ở mức ám ảnh nhẹ chứ không chiếm toàn bộ cognition.
        epistemic_residual = clamp01(1.0 - float(completion.get("epistemic", 0.0) or 0.0))
        bump = 0.012 + 0.022 * epistemic_residual
        if float(development_score or 0.0) >= 0.62:
            bump += 0.012
            meta["semantic_development_count"] = int(meta.get("semantic_development_count", 0) or 0) + 1
            meta["last_semantic_development_at"] = current
            meta["last_development_mode"] = str(development_mode or "")[:32]
        meta["stickiness"] = min(0.88, max(state.stickiness, float(meta.get("stickiness", 0.0) or 0.0)) + bump)
        meta["rumination_awareness"] = state.rumination_awareness
        concern.metadata = meta
        concern.last_reviewed_at = current
        concern.updated_at = max(float(concern.updated_at or 0.0), current)
        self.save()
        return True

    def _event_ids(self, concern: PrivateConcern) -> tuple[str, ...]:
        raw = concern.metadata.get("cognitive_event_ids", ())
        if not isinstance(raw, (list, tuple, set)):
            return ()
        return tuple(str(item) for item in raw if str(item or "").strip())

    def _match_event_theme(
        self,
        theme: str,
        evidence_ids: tuple[str, ...],
        *,
        event_kind: str,
        source_ref: str,
    ) -> PrivateConcern | None:
        """Merge CHẶT cho cognitive event.

        V40 từng dùng semantic similarity như giấy đăng ký kết hôn: hai ý cùng
        nhắc "im lặng / khoảng trống" là bị nhập hộ khẩu chung. V42 chỉ merge khi
        gần như cùng một chuyện hoặc có provenance độc lập thật sự trùng. Những
        ý chỉ có liên quan sẽ được link ở ``_link_related_concerns``.
        """

        f_clean = _strip_noise_prefix(str(theme or "").lower())
        f_sig = _concept_signature(f_clean)
        f_tokens = {t for t in f_clean.replace("_", " ").split() if len(t) >= 4}
        # seed ID/event ID chỉ là provenance của event, không phải bằng chứng độc lập
        # rằng hai concern là cùng một chuyện.
        independent = {
            str(item) for item in evidence_ids
            if str(item).strip()
            and str(item) != str(source_ref or "")
            and not str(item).startswith(("seed.", "mental.seed.", "cogevt:"))
        }
        best: tuple[float, PrivateConcern] | None = None
        for concern in self._concerns.values():
            if concern.status in {"resolved", "rejected"}:
                continue
            c_clean = _strip_noise_prefix(concern.theme.lower())
            if c_clean == f_clean:
                return concern
            c_sig = _concept_signature(c_clean)
            shared = len(f_sig & c_sig) if f_sig and c_sig else 0
            coeff = shared / max(1, min(len(f_sig), len(c_sig))) if shared else 0.0
            c_tokens = {t for t in c_clean.replace("_", " ").split() if len(t) >= 4}
            jaccard = len(f_tokens & c_tokens) / max(1, len(f_tokens | c_tokens))
            existing_independent = {
                str(item) for item in concern.evidence_ids
                if str(item).strip()
                and not str(item).startswith(("seed.", "mental.seed.", "cogevt:"))
            }
            evidence_overlap = (
                len(independent & existing_independent) / max(1, len(independent))
                if independent else 0.0
            )
            last_kind = str(concern.metadata.get("last_event_kind", "") or "")
            same_kind = not last_kind or last_kind == str(event_kind or "")

            # Hai đường merge hợp lệ:
            # 1) semantic gần đồng nhất + cùng event kind;
            # 2) có provenance độc lập chung + semantic vẫn đủ gần.
            semantic_same = (
                same_kind
                and shared >= 3
                and coeff >= _STRICT_EVENT_CONCEPT_MIN
                and jaccard >= _STRICT_EVENT_JACCARD_MIN
            )
            evidence_same = evidence_overlap >= 0.50 and shared >= 2 and coeff >= 0.52
            if not (semantic_same or evidence_same):
                continue
            score = max(coeff * 0.72 + jaccard * 0.28, 0.72 * evidence_overlap + 0.28 * coeff)
            if best is None or score > best[0]:
                best = (score, concern)
        return best[1] if best else None

    def _link_related_concerns(self, concern: PrivateConcern) -> tuple[str, ...]:
        """Nối graph cho ý liên quan nhưng không nhập identity."""

        base = _concept_signature(_strip_noise_prefix(concern.theme.lower()))
        if not base:
            return ()
        related: list[tuple[float, PrivateConcern]] = []
        for other in self._concerns.values():
            if other.id == concern.id or other.status in {"resolved", "rejected"}:
                continue
            sig = _concept_signature(_strip_noise_prefix(other.theme.lower()))
            if not sig:
                continue
            shared = len(base & sig)
            if shared < 2:
                continue
            coeff = shared / max(1, min(len(base), len(sig)))
            if coeff >= _RELATED_CONCEPT_MIN:
                related.append((coeff, other))
        related.sort(key=lambda pair: pair[0], reverse=True)
        ids = tuple(item.id for _score, item in related[:6])
        concern.metadata["related_concern_ids"] = list(ids)
        for _score, other in related[:6]:
            backlinks = list(other.metadata.get("related_concern_ids") or ())
            if concern.id not in backlinks:
                backlinks.append(concern.id)
                other.metadata["related_concern_ids"] = backlinks[-6:]
        return ids

    @_locked
    def ingest_cognitive_event(self, data: Mapping[str, Any] | None) -> ConcernEventReceipt:
        """Áp đúng MỘT cognitive event vào concern state.

        Đây là ingress event-driven của V42. Gọi lại cùng ``event_id`` là no-op
        tuyệt đối: không recurrence, không activation, không updated_at, không gì
        cả. Tick scheduler vì thế không còn quyền "nghĩ lại" hộ Eris.
        """

        if not data:
            return ConcernEventReceipt(None, "invalid")
        event_id = compact_text(data.get("event_id") or "", 220)
        theme = compact_text(data.get("subject") or data.get("theme") or "", 360)
        if not event_id or not theme or self._is_degenerate_theme(theme):
            return ConcernEventReceipt(None, "invalid")
        current = float(data.get("occurred_at") or time.time())
        event_kind = compact_text(data.get("kind") or "thought", 64)
        source_ref = compact_text(data.get("source_ref") or "", 220)
        incoming_meta = dict(data.get("metadata") or {}) if isinstance(data.get("metadata"), Mapping) else {}
        evidence = tuple(dict.fromkeys(
            str(item).strip() for item in (data.get("evidence_refs") or ()) if str(item).strip()
        ))[:32]

        # Persisted idempotency: runtime object có restart thì cùng event vẫn không
        # được bơm lại state nếu store còn giữ receipt.
        for existing in self._concerns.values():
            if event_id in self._event_ids(existing):
                return ConcernEventReceipt(existing, "duplicate")

        # V70: semantic development có typed parent identity. Khi upstream đã
        # chứng minh development bằng deterministic score, đừng bắt fuzzy matcher
        # đoán parent từ câu insight mới rồi vô tình tách nó thành concern khác.
        parent_concern_id = compact_text(incoming_meta.get("semantic_parent_concern_id") or "", 140)
        concern = None
        if bool(incoming_meta.get("semantic_mutation", False)) and parent_concern_id:
            concern = self._concerns.get(parent_concern_id)
        if concern is None:
            concern = self._match_event_theme(
                theme,
                evidence,
                event_kind=event_kind,
                source_ref=source_ref,
            )
        created = concern is None
        mutation = False
        if concern is None:
            question = theme if event_kind in {"curiosity_question", "knowledge_gap"} else ""
            concern = PrivateConcern(
                id=stable_id("concern", "event", event_kind, theme),
                theme=theme,
                evidence_ids=evidence,
                open_questions=((question,) if question else ()),
                pressure=clamp01(0.18 + 0.30 * clamp01(incoming_meta.get("seed_quality", 0.5), 0.5)
                                 + 0.20 * clamp01(incoming_meta.get("seed_motive_strength", 0.4), 0.4)),
                uncertainty=clamp01(incoming_meta.get("uncertainty", 0.45), 0.45),
                recurrence=1,
                status="open",
                metadata={
                    "cognitive_event_ids": [event_id],
                    "event_count": 1,
                    "echo_streak": 0,
                    "mutation_count": 0,
                    "reactivation_count": 0,
                    "surfaced_count": 0,
                    "last_event_at": current,
                    "last_event_kind": event_kind,
                    "last_event_origin": compact_text(data.get("origin") or "internal", 48),
                    "last_mutation_at": current,
                    **{k: v for k, v in incoming_meta.items() if k not in {
                        "mutation_count", "reactivation_count", "surfaced_count", "echo_streak"
                    }},
                },
                created_at=current,
                updated_at=current,
                last_reviewed_at=current,
            )
            self._concerns[concern.id] = concern
            outcome = "created"
        else:
            old_theme = concern.theme
            old_sig = _concept_signature(_strip_noise_prefix(old_theme.lower()))
            new_sig = _concept_signature(_strip_noise_prefix(theme.lower()))
            shared = len(old_sig & new_sig) if old_sig and new_sig else 0
            semantic_overlap = shared / max(1, min(len(old_sig), len(new_sig))) if shared else 0.0
            old_evidence = set(concern.evidence_ids)
            new_independent = {
                ref for ref in evidence
                if ref not in old_evidence
                and ref != source_ref
                and not ref.startswith(("seed.", "mental.seed.", "cogevt:"))
            }
            # V42: paraphrase từ seed generator KHÔNG phải insight. Mutation chỉ
            # được công nhận khi có evidence độc lập mới hoặc upstream thought
            # engine đánh dấu rõ đây là semantic mutation. Không dùng độ lệch câu
            # chữ để tự phong một cách diễn đạt khác thành suy nghĩ mới.
            explicit_mutation = bool(incoming_meta.get("semantic_mutation", False))
            mutation = bool(new_independent or explicit_mutation)
            merged_evidence = list(concern.evidence_ids)
            for ref in evidence:
                if ref not in merged_evidence:
                    merged_evidence.append(ref)
            concern.evidence_ids = tuple(merged_evidence[:64])
            event_ids = list(self._event_ids(concern))
            event_ids.append(event_id)
            concern.metadata["cognitive_event_ids"] = event_ids[-64:]
            concern.metadata["event_count"] = int(concern.metadata.get("event_count", 1) or 1) + 1
            concern.metadata["reactivation_count"] = int(concern.metadata.get("reactivation_count", 0) or 0) + 1
            concern.metadata["last_reactivated_at"] = current
            concern.metadata["last_event_at"] = current
            concern.metadata["last_event_kind"] = event_kind
            concern.metadata["last_event_origin"] = compact_text(data.get("origin") or "internal", 48)
            concern.recurrence = max(1, int(concern.recurrence or 1)) + 1
            concern.last_reviewed_at = current
            concern.updated_at = current
            if mutation:
                concern.metadata["mutation_count"] = int(concern.metadata.get("mutation_count", 0) or 0) + 1
                concern.metadata["last_mutation_at"] = current
                concern.metadata["echo_streak"] = 0
                # V69: evidence/semantic mutation thật mở lại phần chưa hoàn tất.
                # Chỉ reset completion khi có mutation, không vì scheduler tick hay
                # paraphrase. Concern identity và private pressure vẫn được giữ.
                completion = dict(concern.metadata.get("completion") or {})
                if completion:
                    completion["expressive"] = clamp01(float(completion.get("expressive", 0.0) or 0.0) * 0.42)
                    completion["epistemic"] = clamp01(float(completion.get("epistemic", 0.0) or 0.0) * 0.58)
                    completion["relational"] = clamp01(float(completion.get("relational", 0.0) or 0.0) * 0.82)
                    completion["updated_at"] = current
                    concern.metadata["completion"] = completion
                concern.pressure = clamp01(max(concern.pressure * 0.94, 0.34) + 0.06)
                # Giữ theme identity; mutation mới chỉ được lưu như event, không để
                # một câu mới từ từ rewrite concern thành blob tổng hợp.
                mutations = list(concern.metadata.get("mutation_subjects") or ())
                if theme != old_theme and theme not in mutations:
                    mutations.append(theme)
                    concern.metadata["mutation_subjects"] = mutations[-6:]
                outcome = "mutated"
            else:
                concern.metadata["echo_streak"] = int(concern.metadata.get("echo_streak", 0) or 0) + 1
                # Event mới nhưng không thêm gì: nhận ra nó rồi lờn đi. Không pump.
                concern.pressure = clamp01(concern.pressure * 0.90)
                concern.uncertainty = clamp01(concern.uncertainty + 0.015)
                outcome = "echo"

        related = self._link_related_concerns(concern)
        self._trim()
        self.save()
        return ConcernEventReceipt(concern, outcome, mutation=mutation, related_ids=related)

    @_locked
    def update_from_fragments(self, fragments: Iterable[ThoughtFragment]) -> tuple[PrivateConcern, ...]:
        """Nhận fragment như INTERNAL EVENT, không như một tick mới.

        V40 gọi ``merge_fragment`` mỗi lần evaluator trả cùng ý, nên evaluator lặp
        lại cùng claim/evidence cũng vô tình làm concern "sống hơn". V42 cấp cho
        mỗi fragment một event id ổn định. Cùng fragment quay lại là no-op; một
        paraphrase mới nhưng không có evidence/detail mới chỉ là echo và phải lờn.
        """

        updated: list[PrivateConcern] = []
        for fragment in fragments:
            if not fragment.claim or fragment.score < 0.28:
                continue
            theme = self._theme_from_fragment(fragment)
            if self._is_degenerate_theme(theme):
                continue
            receipt = self.ingest_cognitive_event({
                "event_id": f"cogevt:fragment:{fragment.id}",
                "origin": "private_mind",
                "kind": "private_thought",
                "subject": theme,
                "occurred_at": float(fragment.created_at or time.time()),
                "evidence_refs": list(fragment.evidence_ids),
                "source_ref": fragment.id,
                "metadata": {
                    "seed_quality": fragment.score,
                    "seed_motive_strength": fragment.pressure,
                    "uncertainty": 1.0 - fragment.confidence,
                    # Chỉ thought-engine nào biết mình thực sự tạo semantic delta
                    # mới được bật cờ này. Khác wording tự nó không phải insight.
                    "semantic_mutation": bool(fragment.metadata.get("semantic_mutation", False)),
                    "fragment_source": fragment.source,
                },
            })
            concern = receipt.concern
            if concern is None or receipt.outcome in {"invalid", "duplicate"}:
                continue

            before_q = set(concern.open_questions)
            before_h = {h.label.lower() for h in concern.hypotheses}
            before_b = set(concern.future_biases)

            if fragment.suggested_question and fragment.suggested_question not in before_q:
                concern.open_questions = tuple([*concern.open_questions, fragment.suggested_question][:12])

            merged_hypotheses = list(concern.hypotheses)
            by_label = {h.label.lower(): idx for idx, h in enumerate(merged_hypotheses)}
            for hyp in fragment.hypotheses:
                key = hyp.label.lower()
                if key in by_label:
                    old = merged_hypotheses[by_label[key]]
                    # Detail merge không được tự tăng recurrence/pressure.
                    if hyp.score > old.score:
                        merged_hypotheses[by_label[key]] = hyp
                else:
                    by_label[key] = len(merged_hypotheses)
                    merged_hypotheses.append(hyp)
            concern.hypotheses = tuple(sorted(merged_hypotheses, key=lambda h: h.score, reverse=True)[:12])

            biases = list(concern.future_biases)
            for bias in tuple(fragment.metadata.get("future_biases") or ()):
                text = compact_text(bias, 160)
                if text and text not in biases:
                    biases.append(text)
            concern.future_biases = tuple(biases[:12])

            detail_mutation = (
                set(concern.open_questions) != before_q
                or {h.label.lower() for h in concern.hypotheses} != before_h
                or set(concern.future_biases) != before_b
            )
            if detail_mutation and not receipt.mutation:
                concern.metadata["mutation_count"] = int(concern.metadata.get("mutation_count", 0) or 0) + 1
                concern.metadata["last_mutation_at"] = float(fragment.created_at or time.time())
                concern.metadata["echo_streak"] = 0
                concern.pressure = clamp01(max(concern.pressure, fragment.pressure * 0.78))

            sources = list(concern.metadata.get("fragment_sources") or ())
            if fragment.source not in sources:
                sources.append(fragment.source)
            concern.metadata["fragment_sources"] = sources[-12:]
            updated.append(concern)

        if updated:
            self._trim()
            self.save()
        return tuple(updated)

    @_locked
    def update_from_judgment(self, data: Mapping[str, Any] | None) -> PrivateConcern | None:
        if not data:
            return None
        theme = compact_text(data.get("theme") or data.get("claim") or data.get("dominant_judgment") or "", 360)
        if not theme:
            return None
        concern_id = str(data.get("id") or stable_id("concern", theme))
        concern = self._concerns.get(concern_id)
        if concern is None:
            # Theme có thể là paraphrase của một concern đang mở (id khác vì khác
            # chữ) → gộp vào nó thay vì đẻ bản sao.
            concern = self._match_theme(
                theme, tuple(str(x) for x in (data.get("evidence_ids") or ()) if str(x).strip())
            )
        if concern is None:
            concern = PrivateConcern(
                id=concern_id,
                theme=theme,
                evidence_ids=tuple(str(x) for x in (data.get("evidence_ids") or ()) if str(x).strip()),
                open_questions=tuple(str(x) for x in (data.get("open_questions") or ()) if str(x).strip()),
                pressure=clamp01(data.get("pressure", 0.5)),
                uncertainty=clamp01(data.get("uncertainty", 0.5)),
                status=str(data.get("status") or "open"),  # type: ignore[arg-type]
                future_biases=tuple(str(x) for x in (data.get("future_biases") or ()) if str(x).strip()),
                metadata=dict(data.get("metadata") or {}),
            )
            self._concerns[concern.id] = concern
        else:
            before_evidence = set(concern.evidence_ids)
            before_questions = set(concern.open_questions)
            concern.pressure = clamp01(max(concern.pressure * 0.90, data.get("pressure", concern.pressure)))
            concern.uncertainty = clamp01(data.get("uncertainty", concern.uncertainty))
            concern.status = str(data.get("status") or concern.status)  # type: ignore[assignment]
            concern.last_reviewed_at = time.time()
            concern.updated_at = time.time()
            for key in ("open_questions", "evidence_ids", "future_biases"):
                existing = list(getattr(concern, key))
                for item in data.get(key, ()) or ():
                    text = compact_text(item, 260)
                    if text and text not in existing:
                        existing.append(text)
                setattr(concern, key, tuple(existing[:64 if key == "evidence_ids" else 12]))
            # V40: mỗi lần cùng một concern quay lại vẫn là recurrence/reactivation,
            # nhưng CHỈ evidence/câu hỏi mới mới được tính là mutation. Nếu không
            # tách hai khái niệm này, hệ sẽ tưởng "nghĩ lại" đồng nghĩa "có ý mới".
            concern.recurrence = max(1, int(concern.recurrence or 0)) + 1
            concern.metadata["reactivation_count"] = int(concern.metadata.get("reactivation_count", 0) or 0) + 1
            concern.metadata["last_reactivated_at"] = concern.updated_at
            if set(concern.evidence_ids) != before_evidence or set(concern.open_questions) != before_questions:
                concern.metadata["mutation_count"] = int(concern.metadata.get("mutation_count", 0) or 0) + 1
                concern.metadata["last_mutation_at"] = concern.updated_at
            # Metadata có provenance mới được merge nhưng không được xóa lifecycle cũ.
            # Seed V40 dùng đường này để các seed paraphrase khác ID vẫn nhập chung
            # concern semantic thay vì đẻ một kho bản sao rất "sáng tạo".
            incoming_meta = data.get("metadata")
            if isinstance(incoming_meta, Mapping):
                for meta_key, meta_value in incoming_meta.items():
                    if meta_key in {"mutation_count", "reactivation_count", "surfaced_count"}:
                        continue
                    concern.metadata[str(meta_key)] = meta_value
        self._trim()
        self.save()
        return concern

    @_locked
    def update_from_outcome(self, outcome: Any, *, surface_outcome: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], ...]:
        data: dict[str, Any]
        if isinstance(outcome, Mapping):
            data = dict(outcome)
        elif callable(getattr(outcome, "to_dict", None)):
            try:
                data = dict(outcome.to_dict())
            except Exception:
                data = {}
        else:
            data = {}
        if surface_outcome:
            data.setdefault("surface_outcome", dict(surface_outcome))
        if not data:
            return ()

        def truthy(name: str) -> bool:
            return bool(data.get(name))

        def score(name: str, default: float = 1.0) -> float:
            return clamp01(data.get(name, default))

        negative_flags = tuple(
            name
            for name in ("user_corrected", "user_confused", "user_felt_intruded", "relation_ruptured")
            if truthy(name)
        )
        positive_flags = tuple(
            name
            for name in ("user_accepted", "user_laughed", "relation_improved")
            if truthy(name)
        )
        action_success = score("action_success", 1.0)
        body_success = score("body_match_success", 1.0)
        # Quyết định "được dỗ" KHÔNG do store tự đoán. Nó đến từ ComfortAppraiser
        # (agent nhỏ + gate bằng disposition của Eris) dưới dạng payload `comfort`.
        # Store chỉ áp con số. Không có payload → không tự nhiên giảm stress.
        comfort = dict(data.get("comfort") or {}) if isinstance(data.get("comfort"), Mapping) else {}
        comfort_stance = str(comfort.get("stance") or "").strip().lower()
        comfort_relief = clamp01(comfort.get("relief", 0.0))
        soothed = comfort_stance == "soothed" and comfort_relief > 0.0
        hardened = comfort_stance == "hardened"
        # action_success/body_success ~0.5 nghĩa là "không rõ", KHÔNG phải thất bại.
        # Trước đây ngưỡng 0.52 khiến mọi turn trung tính bị tính là negative và bơm
        # +0.10 pressure mỗi tick — một trong các máy bơm nuôi vòng lặp. Chỉ coi là
        # negative khi có cờ xấu rõ hoặc success thực sự thấp.
        clearly_failed = action_success < 0.45 or body_success < 0.45
        negative = bool(negative_flags or clearly_failed)
        positive = bool(positive_flags and not negative and action_success >= 0.70 and body_success >= 0.70)
        if not negative and not positive and not soothed and not hardened:
            return ()

        correction = compact_text(data.get("correction_text") or data.get("rupture_reason") or data.get("ignored_reason") or "", 260)
        evidence = tuple(str(item) for item in (data.get("evidence") or ()) if str(item).strip())[:8]
        targets = list(self.open_concerns(limit=4))
        if not targets and negative:
            theme = correction or "; ".join(negative_flags) or "outcome feedback needs hypothesis revision"
            concern = PrivateConcern(
                id=stable_id("concern", "outcome", theme),
                theme=compact_text(theme, 320),
                evidence_ids=tuple(evidence),
                open_questions=("Which private hypothesis led to this bad outcome?",),
                hypotheses=(),
                pressure=0.54,
                uncertainty=0.72,
                recurrence=1,
                status="open",
                future_biases=("revise private hypothesis before repeating this behavior",),
                metadata={"created_from_outcome": True, "outcome": to_plain(data)},
            )
            self._concerns[concern.id] = concern
            targets = [concern]

        reports: list[dict[str, Any]] = []
        now = time.time()
        for concern in targets:
            before_pressure = concern.pressure
            before_uncertainty = concern.uncertainty
            questions = list(concern.open_questions)
            biases = list(concern.future_biases)
            if negative:
                concern.pressure = clamp01(max(concern.pressure + 0.10, concern.pressure * 1.12))
                concern.uncertainty = clamp01(max(concern.uncertainty + 0.08, 0.44))
                concern.recurrence += 1
                concern.status = "open"
                question = correction or "; ".join(negative_flags) or "negative outcome"
                revision = compact_text(f"Revise this hypothesis after outcome: {question}", 260)
                if revision and revision not in questions:
                    questions.append(revision)
                bias = "lower confidence in this concern's current hypothesis until new evidence supports it"
                if bias not in biases:
                    biases.append(bias)
            elif soothed:
                # Lời dỗ đã được ComfortAppraiser xác nhận: đúng trọng tâm, có thực
                # chất, và Eris đang ở thế chịu nhận. Nguôi tỉ lệ với relief.
                factor = clamp01(1.0 - comfort_relief * 0.7)
                concern.pressure = clamp01(concern.pressure * factor)
                concern.uncertainty = clamp01(concern.uncertainty * 0.85)
                if concern.pressure < 0.18 and concern.uncertainty < 0.28:
                    concern.status = "watching"
                bias = "this concern eased because the user's reassurance actually landed"
                if bias not in biases:
                    biases.append(bias)
            elif positive:
                concern.pressure = clamp01(concern.pressure * 0.82)
                concern.uncertainty = clamp01(concern.uncertainty * 0.88)
                if concern.pressure < 0.18 and concern.uncertainty < 0.28:
                    concern.status = "watching"
                bias = "this concern was softened by a positive outcome"
                if bias not in biases:
                    biases.append(bias)
            elif hardened:
                # Bị dỗ bậy, dỗ lệch trọng tâm, hoặc bị dỗ khi đang phòng thủ/ghét.
                # Eris không nguôi — còn cứng lại. Giữ áp lực, gài bias lạnh/khắt khe.
                concern.pressure = clamp01(concern.pressure * 1.04)
                concern.uncertainty = clamp01(concern.uncertainty + 0.03)
                bias = "user tried to soothe but it missed; keep guard up, answer colder and stricter"
                if bias not in biases:
                    biases.append(bias)
            concern.open_questions = tuple(questions[:12])
            concern.future_biases = tuple(biases[:12])
            concern.evidence_ids = tuple(dict.fromkeys((*concern.evidence_ids, *evidence)))[:64]
            concern.metadata["last_outcome_feedback"] = {
                "negative": negative,
                "positive": positive,
                "soothed": soothed,
                "hardened": hardened,
                "comfort_stance": comfort_stance,
                "flags": list(negative_flags or positive_flags),
                "action_success": round(action_success, 3),
                "body_match_success": round(body_success, 3),
                "correction_text": correction,
                "at": now,
            }
            concern.updated_at = now
            concern.last_reviewed_at = now
            reports.append({
                "concern_id": concern.id,
                "negative": negative,
                "positive": positive,
                "soothed": soothed,
                "hardened": hardened,
                # Mức độ "chữa trị": pressure đã nguôi được bao nhiêu trong lần này
                # (âm nghĩa là cứng thêm vì bị dỗ bậy).
                "relief": round(before_pressure - concern.pressure, 3),
                "pressure": [round(before_pressure, 3), round(concern.pressure, 3)],
                "uncertainty": [round(before_uncertainty, 3), round(concern.uncertainty, 3)],
                "flags": list(negative_flags or positive_flags),
            })
        if reports:
            self._trim()
            self.save()
        return tuple(reports)

    @_locked
    def mark_resolved(self, concern_id: str, *, reason: str = "", closure_note: str = "") -> bool:
        concern = self._concerns.get(str(concern_id or ""))
        if concern is None:
            return False
        self._apply_closure(concern, reason=reason, closure_note=closure_note)
        self.save()
        return True

    def _apply_closure(self, concern: PrivateConcern, *, reason: str = "", closure_note: str = "") -> None:
        """Khép một concern và để lại dấu 'đã nguôi' (Q1)."""
        now = time.time()
        concern.status = "resolved"
        concern.updated_at = now
        concern.metadata["resolved_at"] = now
        if reason:
            concern.metadata["resolved_reason"] = compact_text(reason, 240)
        concern.metadata["closure_note"] = compact_text(
            closure_note or "Chuyện này đã nguôi dần trong tôi.", 200
        )

    @_locked
    def auto_resolve_settled(self, *, now: float | None = None) -> tuple[PrivateConcern, ...]:
        """Tự khép các concern đã dịu đủ lâu (Q1).

        Đây là đường 'giải quyết xong thì dọn' mà trước đây thiếu: concern ở trạng
        thái watching với áp lực + bất định thấp, không được khơi lại trong
        settle_window → tự nguôi, có dấu để eris nhắc 'chuyện đó tôi đã nguôi'.
        """
        current = float(now or time.time())
        cfg = self.config
        closed: list[PrivateConcern] = []
        for concern in list(self._concerns.values()):
            if concern.status not in {"watching", "open"}:
                continue
            quiet_for = current - float(concern.last_reviewed_at or concern.updated_at or current)
            # Buông do rumination: nghĩ lại quá nhiều lần mà không có gì mới, áp lực
            # đã nguội. Khép với dấu "thôi đào sâu" thay vì "đã nguôi êm".
            echo_streak = int(concern.metadata.get("echo_streak", 0) or 0)
            if (
                echo_streak >= cfg.rumination_burnout_echoes
                and concern.pressure <= cfg.rumination_burnout_pressure
            ):
                self._apply_closure(
                    concern,
                    reason="rumination_burnout",
                    closure_note="Tôi thôi đào sâu chuyện này; nghĩ mãi cũng không tới đâu.",
                )
                closed.append(concern)
                continue
            if (
                concern.status == "watching"
                and concern.pressure <= cfg.settle_pressure_max
                and concern.uncertainty <= cfg.settle_uncertainty_max
                and quiet_for >= cfg.settle_window_seconds
            ):
                self._apply_closure(concern, reason="settled_quietly")
                closed.append(concern)
        if closed:
            self.save()
        return tuple(closed)

    @_locked
    def recently_resolved(self, *, limit: int = 5, now: float | None = None) -> tuple[dict[str, Any], ...]:
        """Các concern vừa nguôi gần đây — để diary/chat nói 'đã nguôi' (Q1)."""
        current = float(now or time.time())
        window = self.config.resolved_visible_seconds
        items: list[tuple[float, dict[str, Any]]] = []
        for concern in self._concerns.values():
            if concern.status != "resolved":
                continue
            resolved_at = float(concern.metadata.get("resolved_at", concern.updated_at) or 0.0)
            if resolved_at <= 0.0 or current - resolved_at > window:
                continue
            items.append(
                (
                    resolved_at,
                    {
                        "theme": concern.theme,
                        "closure_note": str(concern.metadata.get("closure_note", "") or ""),
                        "resolved_at": resolved_at,
                    },
                )
            )
        items.sort(key=lambda pair: pair[0], reverse=True)
        return tuple(item for _, item in items[: max(0, limit)])

    @_locked
    def prune_and_decay(self, *, now: float | None = None) -> None:
        current = float(now or time.time())
        changed = False
        for concern in list(self._concerns.values()):
            age = current - float(concern.updated_at or current)
            if concern.status == "resolved" and age > self.config.stale_after_seconds * 0.5:
                self._concerns.pop(concern.id, None)
                changed = True
                continue
            if age > self.config.stale_after_seconds and concern.status == "open":
                concern.status = "stale"
                concern.pressure = clamp01(concern.pressure * 0.62)
                changed = True
            elif age > 3600:
                factor = 0.995 if concern.pressure > 0.72 else 0.985
                concern.pressure = clamp01(concern.pressure * factor)
                changed = True
        if changed:
            self._trim()
            self.save()

    @_locked
    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            records = raw.get("concerns") if isinstance(raw, Mapping) else raw
            if not isinstance(records, list):
                records = []
            self._concerns = {}
            for item in records:
                if isinstance(item, Mapping):
                    concern = PrivateConcern.from_mapping(item)
                    self._concerns[concern.id] = concern
            self._trim()
        except Exception as exc:
            self._log("warning", "ConcernStore load failed: %s", exc)
            self._concerns = {}

    @_locked
    def save(self) -> None:
        if not (self.path and self.config.autosave):
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "saved_at": time.time(),
                "concerns": [c.to_dict() for c in self.all()],
            }
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(to_plain(payload), ensure_ascii=False, indent=2), encoding="utf-8")
            # On Windows, replacing can raise PermissionError if another thread or process holds a handle.
            # Retry up to 5 times to prevent WinError 5.
            for attempt in range(5):
                try:
                    tmp.replace(self.path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.01)
        except Exception as exc:
            self._log("warning", "ConcernStore save failed: %s", exc)

    def _find_matching_concern(self, fragment: ThoughtFragment) -> PrivateConcern | None:
        return self._match_theme(self._theme_from_fragment(fragment), fragment.evidence_ids)

    def _match_theme(self, theme: str, evidence_ids: tuple[str, ...] = ()) -> PrivateConcern | None:
        """Tìm concern đang mở khớp với một theme (paraphrase-aware).

        Điểm khớp = max của hai tín hiệu, lấy concern điểm cao nhất ≥ ngưỡng:
        - concept-overlap: overlap-coefficient trên token khái niệm (mạnh với câu
          dài diễn đạt khác nhau cùng ý — chính là ca "khoảng cách / kết nối" lặp).
        - token-Jaccard + evidence-overlap: giữ hành vi cũ cho câu ngắn/định danh.
        """
        f_clean = _strip_noise_prefix(str(theme or "").lower())
        f_sig = _concept_signature(f_clean)
        f_tokens = {t for t in f_clean.replace("_", " ").split() if len(t) >= 4}
        ev = set(evidence_ids or ())

        best: tuple[float, PrivateConcern] | None = None
        for concern in self._concerns.values():
            if concern.status in {"resolved", "rejected"}:
                continue
            c_clean = _strip_noise_prefix(concern.theme.lower())
            if c_clean == f_clean:
                return concern

            concept = 0.0
            if f_sig:
                c_sig = _concept_signature(c_clean)
                shared = len(f_sig & c_sig)
                # Cần ≥2 khái niệm chung + tỉ lệ đủ cao để tránh gộp bừa.
                if shared >= 2 and c_sig:
                    coeff = shared / min(len(f_sig), len(c_sig))
                    if coeff >= _CONCEPT_MATCH_MIN:
                        concept = coeff

            c_tokens = {t for t in c_clean.replace("_", " ").split() if len(t) >= 4}
            jaccard = len(f_tokens & c_tokens) / max(1, len(f_tokens | c_tokens))
            evidence_overlap = (len(ev & set(concern.evidence_ids)) / max(1, len(ev))) if ev else 0.0
            score = max(0.68 * jaccard + 0.32 * evidence_overlap, concept)

            if best is None or score > best[0]:
                best = (score, concern)

        if best and best[0] >= 0.22:
            return best[1]
        return None

    def _theme_from_fragment(self, fragment: ThoughtFragment) -> str:
        explicit = compact_text(fragment.metadata.get("theme") or "", 300)
        if explicit:
            return explicit
        claim = compact_text(fragment.claim, 320)
        # Loại vài cụm mở đầu để theme ít nhiễu văn phong evaluator.
        for prefix in ("User ", "ERIS ", "Runtime ", "The user "):
            if claim.startswith(prefix) and len(claim) > len(prefix) + 8:
                return claim
        return claim or fragment.source

    def _trim(self) -> None:
        limit = max(8, int(self.config.max_concerns or 96))
        if len(self._concerns) <= limit:
            return
        ranked = sorted(
            self._concerns.values(),
            key=lambda c: (c.status == "open", c.pressure, c.recurrence, c.updated_at),
            reverse=True,
        )[:limit]
        self._concerns = {c.id: c for c in ranked}

    def _log(self, level: str, msg: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            fn(msg, *args)

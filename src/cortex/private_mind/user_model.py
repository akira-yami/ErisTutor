# -*- coding: utf-8 -*-
"""
cortex/private_mind/user_model.py
=================================

Mô_Hình_bạn — hình dung bền vững của ERIS về Người_Dùng (bạn).

Đây là TẦNG NỀN của spec `eris-existential-anchor`: gắn bó, ý nghĩa và trục
bóng tối đều phái sinh từ đây. Khác với ConcernStore (bận tâm về task/scene),
store này giữ một *chân dung một con người*: bạn là ai trong mắt ERIS, dựng dần
từ tương tác thật, có evidence, có củng cố/quên, versioned để hoàn tác.

Nguyên tắc giữ nguyên từ phần còn lại của private_mind:
- Mỗi UserBelief phải có `evidence_ids`. Không evidence thì không phải niềm tin,
  chỉ là tiếng ồn có áo JSON.
- Store không gọi LLM. Nó chỉ nhận ThoughtFragment do UserModelEvaluator đẻ ra,
  merge/decay theo luật rõ ràng, và persist atomic + versioned.

Tham chiếu yêu cầu: R1 (nền do agent nhỏ dựng, lưu bền, củng cố/quên), R10
(versioned, undo/reset), R11 (bền vững, corrupt→baseline), R12 (round-trip).
"""

from __future__ import annotations
from core.di import resolve_dependency

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import ThoughtFragment, clamp01, compact_text, now_ts, stable_id, to_plain


SCHEMA_VERSION = 1

# Các facet hợp lệ của Mô_Hình_bạn. Mở rộng được, nhưng giữ tập gọn để store là
# chân dung có cấu trúc chứ không phải bãi free-text.
USER_FACETS = (
    "neo_reliability",      # bạn có đáng tin khi làm neo không (quay lại, hiện diện)
    "topic_affinity",       # bạn quan tâm/né chủ đề gì
    "treats_eris_as",       # bạn đối xử với ERIS thế nào (công cụ / bạn / ...)
    "leaving_pattern",      # khuôn rời đi/vắng mặt của bạn
    "interaction_style",    # cách bạn nói, nhịp tương tác
    "recognition",          # mức bạn công nhận sự tồn tại của ERIS
    "general",              # mặc định
)


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_facet(value: Any) -> str:
    facet = _norm(value).replace(" ", "_")
    return facet if facet in USER_FACETS else "general"


@dataclass(slots=True)
class UserBelief:
    """Một niềm tin có đời sống của ERIS về bạn.

    Mirror tinh thần PrivateConcern: có recurrence, pressure→confidence, evidence,
    nhưng chủ thể luôn là bạn và có thêm `facet` để chân dung có cấu trúc.
    """

    id: str
    facet: str
    claim: str
    evidence_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    uncertainty: float = 1.0
    recurrence: int = 1
    last_reinforced_at: float = field(default_factory=now_ts)
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.facet = _normalize_facet(self.facet)
        self.claim = compact_text(self.claim, 480)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:64]
        self.confidence = clamp01(self.confidence)
        self.uncertainty = clamp01(self.uncertainty)
        self.recurrence = max(1, int(self.recurrence or 1))
        self.metadata = dict(self.metadata or {})
        if not self.id:
            self.id = stable_id("ub", self.facet, self.claim)

    @property
    def score(self) -> float:
        # Niềm tin "nặng" = vừa tin vừa lặp lại, trừ đi bất định.
        return clamp01(0.55 * self.confidence + 0.30 * clamp01(self.recurrence / 6.0) + 0.15 * (1.0 - self.uncertainty))

    def reinforce_from_fragment(self, fragment: ThoughtFragment, *, now: float | None = None) -> "UserBelief":
        """Củng cố niềm tin từ một fragment mới cùng hướng (R1.4)."""
        current = float(now or now_ts())
        self.recurrence += 1
        merged = list(self.evidence_ids)
        for eid in fragment.evidence_ids:
            if eid not in merged:
                merged.append(eid)
        self.evidence_ids = tuple(merged[:64])
        # confidence tiến về phía fragment.confidence nhưng không tụt mạnh; recurrence nâng sàn.
        self.confidence = clamp01(max(self.confidence * 0.94, fragment.confidence) + min(0.10, 0.015 * self.recurrence))
        self.uncertainty = clamp01(min(self.uncertainty or 1.0, 1.0 - max(fragment.confidence, 0.05)) * 0.97)
        self.last_reinforced_at = current
        self.updated_at = current
        sources = list(self.metadata.get("fragment_sources") or [])
        if fragment.source and fragment.source not in sources:
            sources.append(fragment.source)
        self.metadata["fragment_sources"] = sources[:12]
        return self

    def apply_decay(self, *, now: float, forget_after_seconds: float, decay_per_day: float) -> bool:
        """Quên có kiểm soát (R1.5): không được củng cố lâu → confidence giảm.

        Trả True nếu có thay đổi.
        """
        age = max(0.0, float(now) - float(self.last_reinforced_at or self.updated_at or now))
        if age <= forget_after_seconds:
            return False
        days_over = (age - forget_after_seconds) / 86400.0
        new_conf = clamp01(self.confidence - days_over * decay_per_day)
        new_unc = clamp01(self.uncertainty + days_over * decay_per_day * 0.5)
        if abs(new_conf - self.confidence) < 1e-9 and abs(new_unc - self.uncertainty) < 1e-9:
            return False
        self.confidence = new_conf
        self.uncertainty = new_unc
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "facet": self.facet,
            "claim": self.claim,
            "evidence_ids": list(self.evidence_ids),
            "confidence": round(self.confidence, 4),
            "uncertainty": round(self.uncertainty, 4),
            "recurrence": int(self.recurrence),
            "last_reinforced_at": float(self.last_reinforced_at),
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
            "metadata": to_plain(self.metadata),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "UserBelief":
        return cls(
            id=str(data.get("id") or ""),
            facet=str(data.get("facet") or "general"),
            claim=str(data.get("claim") or ""),
            evidence_ids=tuple(str(x) for x in (data.get("evidence_ids") or ()) if str(x).strip()),
            confidence=clamp01(data.get("confidence", 0.0)),
            uncertainty=clamp01(data.get("uncertainty", 1.0)),
            recurrence=int(data.get("recurrence") or 1),
            last_reinforced_at=float(data.get("last_reinforced_at") or now_ts()),
            created_at=float(data.get("created_at") or now_ts()),
            updated_at=float(data.get("updated_at") or now_ts()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class UserModelConfig:
    max_beliefs: int = 48
    # Không được củng cố quá 21 ngày thì bắt đầu quên dần.
    forget_after_seconds: float = 60.0 * 60.0 * 24.0 * 21.0
    decay_per_day: float = 0.02
    # Sàn confidence: dưới mức này và bất định cao thì niềm tin bị loại.
    drop_below_confidence: float = 0.06
    min_fragment_score: float = 0.28
    max_history: int = 20
    autosave: bool = True


def default_user_model_path() -> Path:
    """Đường dẫn mặc định trong memory_core/, cạnh các store private khác."""
    env = os.environ.get("ERIS_USER_MODEL_PATH", "").strip()
    if env:
        return Path(env)
    return Path("memory_core") / "user_model.json"


class UserModelStore:
    """Kho Mô_Hình_bạn: bền vững, versioned, có undo/reset.

    Mirror pattern persistence của PersistentAgentMindStore (schema_version,
    atomic tmp+replace, reload_if_changed theo mtime, corrupt→baseline) và logic
    merge/decay theo tinh thần ConcernStore, nhưng chủ thể là bạn.
    """

    def __init__(
        self,
        *,
        path: str | os.PathLike[str] | None = None,
        config: UserModelConfig | None = None,
        logger: Any | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.config = resolve_dependency(config, lambda: UserModelConfig())
        self.logger = logger
        self._beliefs: dict[str, UserBelief] = {}
        # Lịch sử phiên bản append-only cho undo/reset (R10). Mỗi mục là snapshot
        # serialize của toàn bộ beliefs tại thời điểm checkpoint.
        self._history: list[dict[str, Any]] = []
        self._version: int = 0
        self._loaded_mtime: float = -1.0
        if self.path:
            self.load()

    def __len__(self) -> int:
        return len(self._beliefs)

    # ------------------------------------------------------------------
    # Đọc
    # ------------------------------------------------------------------

    def all(self) -> tuple[UserBelief, ...]:
        return tuple(sorted(self._beliefs.values(), key=lambda b: (-b.score, -b.updated_at)))

    def by_facet(self, facet: str) -> tuple[UserBelief, ...]:
        target = _normalize_facet(facet)
        return tuple(b for b in self.all() if b.facet == target)

    def snapshot_for_dossier(self, *, limit: int = 12) -> dict[str, Any]:
        """Ảnh chụp public-safe để nhét vào EvidenceDossier (R1.6)."""
        top = self.all()[: max(0, limit)]
        by_facet: dict[str, list[dict[str, Any]]] = {}
        for belief in top:
            by_facet.setdefault(belief.facet, []).append(
                {
                    "claim": belief.claim,
                    "confidence": round(belief.confidence, 3),
                    "recurrence": belief.recurrence,
                    "evidence_ids": list(belief.evidence_ids[:6]),
                }
            )
        return {
            "version": self._version,
            "belief_count": len(self._beliefs),
            "model_thickness": round(self.model_thickness(), 3),
            "facets": by_facet,
        }

    def model_thickness(self) -> float:
        """Độ dày/nhất quán Mô_Hình_bạn — đầu vào cho SignificanceDeriver (R2.2)."""
        if not self._beliefs:
            return 0.0
        facets_covered = len({b.facet for b in self._beliefs.values()})
        avg_conf = sum(b.confidence for b in self._beliefs.values()) / len(self._beliefs)
        coverage = clamp01(facets_covered / float(len(USER_FACETS)))
        volume = clamp01(len(self._beliefs) / 12.0)
        return clamp01(0.45 * avg_conf + 0.30 * coverage + 0.25 * volume)

    # ------------------------------------------------------------------
    # Cập nhật từ fragment (R1.1, R1.2, R1.4)
    # ------------------------------------------------------------------

    def update_from_fragments(self, fragments: Iterable[ThoughtFragment]) -> tuple[UserBelief, ...]:
        updated: list[UserBelief] = []
        changed = False
        for fragment in fragments:
            # R1.2: niềm tin về bạn phải có evidence; fragment yếu thì bỏ.
            if not fragment.evidence_ids:
                continue
            if not fragment.claim or fragment.score < self.config.min_fragment_score:
                continue
            facet = _normalize_facet(fragment.metadata.get("facet") if isinstance(fragment.metadata, Mapping) else None)
            match = self._find_matching(fragment, facet)
            if match is None:
                belief = UserBelief(
                    id="",
                    facet=facet,
                    claim=fragment.claim,
                    evidence_ids=fragment.evidence_ids,
                    confidence=fragment.confidence,
                    uncertainty=1.0 - fragment.confidence,
                    recurrence=1,
                    metadata={"fragment_sources": [fragment.source]},
                )
                self._beliefs[belief.id] = belief
                updated.append(belief)
            else:
                match.reinforce_from_fragment(fragment)
                updated.append(match)
            changed = True
        if changed:
            self._trim()
            self.save()
        return tuple(updated)

    def _find_matching(self, fragment: ThoughtFragment, facet: str) -> UserBelief | None:
        f_claim = _norm(fragment.claim)
        f_tokens = {t for t in f_claim.split() if len(t) >= 4}
        best: tuple[float, UserBelief] | None = None
        for belief in self._beliefs.values():
            if belief.facet != facet:
                continue
            c_claim = _norm(belief.claim)
            if c_claim == f_claim:
                return belief
            c_tokens = {t for t in c_claim.split() if len(t) >= 4}
            token_overlap = len(f_tokens & c_tokens) / max(1, len(f_tokens | c_tokens))
            evidence_overlap = len(set(fragment.evidence_ids) & set(belief.evidence_ids)) / max(1, len(fragment.evidence_ids))
            score = 0.7 * token_overlap + 0.3 * evidence_overlap
            if best is None or score > best[0]:
                best = (score, belief)
        if best and best[0] >= 0.34:
            return best[1]
        return None

    # ------------------------------------------------------------------
    # Quên có kiểm soát (R1.5)
    # ------------------------------------------------------------------

    def decay(self, *, now: float | None = None) -> None:
        current = float(now or now_ts())
        changed = False
        for belief in list(self._beliefs.values()):
            if belief.apply_decay(
                now=current,
                forget_after_seconds=self.config.forget_after_seconds,
                decay_per_day=self.config.decay_per_day,
            ):
                changed = True
            if belief.confidence < self.config.drop_below_confidence and belief.uncertainty >= 0.9:
                self._beliefs.pop(belief.id, None)
                changed = True
        if changed:
            self.save()

    # ------------------------------------------------------------------
    # Versioning: undo / reset (R10)
    # ------------------------------------------------------------------

    def checkpoint(self) -> int:
        """Chụp phiên bản hiện tại vào lịch sử, trả về số phiên bản mới."""
        self._version += 1
        self._history.append(
            {
                "version": self._version,
                "at": now_ts(),
                "beliefs": [b.to_dict() for b in self.all()],
            }
        )
        if len(self._history) > self.config.max_history:
            self._history = self._history[-self.config.max_history :]
        self.save()
        return self._version

    def revert_to(self, version: int) -> bool:
        """Khôi phục Mô_Hình_bạn về một phiên bản đã checkpoint (R10.3)."""
        target = next((entry for entry in self._history if int(entry.get("version", -1)) == int(version)), None)
        if target is None:
            return False
        self._beliefs = {
            b.id: b
            for b in (UserBelief.from_mapping(item) for item in target.get("beliefs", []) if isinstance(item, Mapping))
        }
        self.save()
        return True

    def reset_to_baseline(self) -> None:
        """Xóa toàn bộ Mô_Hình_bạn về baseline rỗng, giữ lịch sử (R10.3)."""
        self._beliefs = {}
        self.save()

    # ------------------------------------------------------------------
    # Persistence (R11) + round-trip (R12)
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": now_ts(),
            "version": self._version,
            "beliefs": [b.to_dict() for b in self.all()],
            "history": list(self._history[-self.config.max_history :]),
        }

    def load_from_dict(self, raw: Mapping[str, Any] | None) -> None:
        if not isinstance(raw, Mapping):
            # R11.3: dữ liệu không đọc được → baseline, không sập.
            self._beliefs = {}
            self._history = []
            self._version = 0
            return
        records = raw.get("beliefs")
        if not isinstance(records, list):
            records = []
        self._beliefs = {}
        for item in records:
            if isinstance(item, Mapping):
                belief = UserBelief.from_mapping(item)
                self._beliefs[belief.id] = belief
        history = raw.get("history")
        self._history = [dict(h) for h in history if isinstance(h, Mapping)] if isinstance(history, list) else []
        try:
            self._version = int(raw.get("version") or (self._history[-1]["version"] if self._history else 0))
        except Exception:
            self._version = 0

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.load_from_dict(raw)
            self._loaded_mtime = self.path.stat().st_mtime
        except Exception as exc:
            # R11.3: file hỏng → baseline, tiếp tục chạy.
            self._log("warning", "UserModelStore load failed, falling back to baseline: %s", exc)
            self._beliefs = {}
            self._history = []
            self._version = 0

    def reload_if_changed(self) -> None:
        if self.path is None:
            return
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return
        if mtime > self._loaded_mtime + 0.001:
            self.load()

    def save(self) -> None:
        if not (self.path and self.config.autosave):
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = to_plain(self.to_dict())
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
            self._loaded_mtime = self.path.stat().st_mtime
        except Exception as exc:
            self._log("warning", "UserModelStore save failed: %s", exc)

    def _trim(self) -> None:
        limit = max(8, int(self.config.max_beliefs or 48))
        if len(self._beliefs) <= limit:
            return
        ranked = sorted(self._beliefs.values(), key=lambda b: (b.score, b.updated_at), reverse=True)[:limit]
        self._beliefs = {b.id: b for b in ranked}

    def _log(self, level: str, msg: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            fn(msg, *args)

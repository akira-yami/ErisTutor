# -*- coding: utf-8 -*-
"""
memory/memory_ai_curator.py
===========================

AI curator cho memory — gọi Gemini để làm 2 việc rule không làm được:

1. Episodic distillation:
   Nhiều episode tương đồng → 1 semantic fact ổn định.
   Ví dụ: 5 lần user đề cập "thích câu ngắn" → semantic: user.response_style = ngắn gọn.

2. Concern → Long-term bridge:
   PrivateConcern đã chín (pressure cao, recurrence cao) → đánh giá xem có nên
   kết tinh thành semantic/relational memory không.

Cả hai đều là "học từ pattern", không phải lưu raw event.
Curator không được bịa fact. Nó chỉ được tóm tắt/kết tinh thứ đã có evidence.

Chạy trong background maintenance (budget=normal/deep), không phải hot path.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from contextlib import nullcontext
from typing import Any, Mapping

from .episodic_memory import distillation_hash
from .json_store_io import lock_for_path, locked_path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compact(value: Any, limit: int = 400) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ---------------------------------------------------------------------------
# Kết quả
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CuratorResult:
    """Kết quả một lượt curator chạy."""

    distilled: int = 0       # số semantic fact mới từ episodic distillation
    bridged: int = 0         # số concern → long-term memory
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "distilled": self.distilled,
            "bridged": self.bridged,
            "skipped": self.skipped,
            "warnings": list(self.warnings),
            "notes": list(self.notes[:12]),
        }


# ---------------------------------------------------------------------------
# Distillation prompt
# ---------------------------------------------------------------------------

_DISTILL_SYSTEM = """You are a memory distillation engine for ERIS.
You receive a cluster of similar episodic memory items and must distill them into ONE stable semantic fact.

Rules:
- Output ONLY a JSON object, no prose.
- Base the fact entirely on the provided episodes. Do not invent.
- If the episodes do not support a clear stable fact, output {"skip": true}.
- Keep the claim under 200 characters.
- Identify the correct fact_key from the list of known keys if applicable.
- Use Vietnamese for the claim if the evidence is in Vietnamese.

Known fact_keys: user.name, user.birth_year, user.birth_month, user.living_location,
user.hometown, user.response_style, user.preference.general, user.convention,
relationship.pattern, project.eris, procedural_rule

Output schema:
{
  "claim": "string — the distilled stable fact",
  "fact_key": "string — best matching key or empty string",
  "domain": "user|self|world|technical|project|relationship|unknown",
  "confidence": 0.0-1.0,
  "decay_policy": "slow|normal|fast",
  "skip": false
}"""

_DISTILL_USER = """Episodic cluster ({count} episodes):
{episodes}

Distill into one stable semantic fact."""


# ---------------------------------------------------------------------------
# Concern bridge prompt
# ---------------------------------------------------------------------------

_BRIDGE_SYSTEM = """You are a memory bridge engine for ERIS.
You receive a PrivateMind concern (internal worry/observation) and must decide if it should
become a stable long-term memory entry.

Rules:
- Output ONLY a JSON object, no prose.
- Only bridge if the concern reflects a real, stable pattern — not a one-time event.
- If not suitable for long-term memory, output {"skip": true}.
- Target layer: "semantic_memory" for user/self facts, "relational_memory" for relationship patterns.
- Claim must be factual and grounded, not speculative. Under 200 characters.
- Use Vietnamese for the claim if the concern is in Vietnamese.

Output schema:
{
  "claim": "string",
  "fact_key": "string or empty",
  "domain": "user|self|relationship|project|unknown",
  "target": "semantic_memory|relational_memory",
  "confidence": 0.0-1.0,
  "decay_policy": "slow|normal",
  "skip": false
}"""

_BRIDGE_USER = """Concern:
theme: {theme}
pressure: {pressure}
recurrence: {recurrence}
open_questions: {questions}
future_biases: {biases}

Should this become a long-term memory entry?"""


# ---------------------------------------------------------------------------
# Curator chính
# ---------------------------------------------------------------------------

class MemoryAICurator:
    """AI curator chạy trong background maintenance."""

    # Episodic: cần ít nhất N episodes tương đồng để gọi AI
    MIN_CLUSTER_SIZE = 3
    # Concern: cần đủ chín mới bridge
    MIN_CONCERN_PRESSURE = 0.72
    MIN_CONCERN_RECURRENCE = 3
    MIN_CONCERN_CONFIDENCE = 0.75  # từ InternalJudgment, không phải concern trực tiếp

    def __init__(self, *, llm_service: Any = None, memory_core: Any = None) -> None:
        self.llm_service = llm_service
        self.memory_core = memory_core

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def run_distillation(self, *, budget: str = "normal") -> CuratorResult:
        store = getattr(self.memory_core, "episodic", None)
        job_lock = lock_for_path(str(getattr(store, "path", id(self.memory_core))) + ".distillation")
        if not job_lock.acquire(blocking=False):
            return CuratorResult(skipped=1, notes=["distillation_busy"])
        try:
            return self._run_distillation(budget=budget)
        finally:
            job_lock.release()

    def _run_distillation(self, *, budget: str = "normal") -> CuratorResult:
        """Distill episodic clusters → semantic facts."""
        result = CuratorResult()
        if self.memory_core is None or self.llm_service is None:
            result.skipped += 1
            result.warnings.append("no_memory_core_or_llm")
            return result

        episodic_store = getattr(self.memory_core, "episodic", None)
        if episodic_store is None:
            result.skipped += 1
            return result

        items = list(getattr(episodic_store, "load", lambda: [])())
        if not items:
            return result

        limit = {"small": 2, "normal": 4, "deep": 8}.get(budget, 4)
        clusters = self._cluster_episodes(items, limit=limit)

        for cluster in clusters:
            # The model prompt reads at most eight sources. Do not mark unseen
            # members of a larger cluster as processed.
            cluster = cluster[:8]
            if len(cluster) < self.MIN_CLUSTER_SIZE:
                result.skipped += 1
                continue
            try:
                fact = self._distill_cluster(cluster)
                if fact is None:
                    result.skipped += 1
                    continue
                path = getattr(episodic_store, "path", None)
                with locked_path(path) if path else nullcontext():
                    validate = getattr(episodic_store, "distillation_sources_current", None)
                    if callable(validate) and not validate(cluster):
                        result.skipped += 1
                        result.notes.append("distillation_sources_changed")
                        continue
                    if fact.get("skip") is True:
                        marker = getattr(episodic_store, "mark_distilled", None)
                        if callable(marker):
                            marker(cluster, decision="skipped")
                        result.skipped += 1
                        continue
                    committed = self._commit_semantic_fact(fact, evidence_ids=[item.get("id", "") for item in cluster])
                    if committed:
                        marker = getattr(episodic_store, "mark_distilled", None)
                        if callable(marker):
                            marker(cluster)
                        result.distilled += 1
                        result.notes.append(f"distilled:{fact.get('fact_key') or fact.get('domain')}:{_compact(fact.get('claim',''), 60)}")
                    else:
                        result.skipped += 1
            except Exception as exc:
                result.warnings.append(f"distill_error:{type(exc).__name__}:{exc}")

        return result

    def run_concern_bridge(self, *, concern_store_path: str | None = None, budget: str = "normal") -> CuratorResult:
        """Bridge concern chín → long-term memory."""
        result = CuratorResult()
        if self.memory_core is None or self.llm_service is None:
            result.skipped += 1
            result.warnings.append("no_memory_core_or_llm")
            return result

        concerns = self._load_concerns(concern_store_path)
        if not concerns:
            return result

        limit = {"small": 1, "normal": 3, "deep": 6}.get(budget, 3)
        ripe = self._ripe_concerns(concerns, limit=limit)

        for concern in ripe:
            try:
                decision = self._bridge_concern(concern)
                if decision is None or decision.get("skip"):
                    result.skipped += 1
                    continue
                committed = self._commit_from_bridge(decision, evidence_ids=list(concern.get("evidence_ids", []))[:8])
                if committed:
                    result.bridged += 1
                    result.notes.append(
                        f"bridged_concern:{concern.get('id','')[:16]}→{decision.get('target')}:{_compact(decision.get('claim',''), 60)}"
                    )
                    # Đánh dấu concern đã được kết tinh để tránh bridge lại
                    self._mark_concern_bridged(concern, concern_store_path)
                else:
                    result.skipped += 1
            except Exception as exc:
                result.warnings.append(f"bridge_error:{type(exc).__name__}:{exc}")

        return result

    # ------------------------------------------------------------------
    # Episodic clustering
    # ------------------------------------------------------------------

    def _cluster_episodes(self, items: list[Any], *, limit: int) -> list[list[dict]]:
        """Nhóm episode tương đồng theo summary token overlap."""
        dicts = [
            item.to_dict() if callable(getattr(item, "to_dict", None)) else dict(item)
            for item in items
        ]
        # Chỉ xét episode chưa được distill
        undistilled = [d for d in dicts if "distilled" not in (d.get("tags") or [])
                       or (d.get("metadata", {}).get("distilled_hash") is not None
                           and d["metadata"]["distilled_hash"] != distillation_hash(d))]
        if not undistilled:
            return []

        clusters: list[list[dict]] = []
        used: set[str] = set()

        for i, base in enumerate(undistilled):
            if base.get("id") in used:
                continue
            cluster = [base]
            base_tokens = set(_compact(base.get("summary", "")).lower().split())
            if len(base_tokens) < 3:
                continue
            for j, other in enumerate(undistilled):
                if i == j or other.get("id") in used:
                    continue
                other_tokens = set(_compact(other.get("summary", "")).lower().split())
                if not other_tokens:
                    continue
                overlap = len(base_tokens & other_tokens) / max(1, len(base_tokens | other_tokens))
                if overlap >= 0.40:
                    cluster.append(other)
                    used.add(other.get("id", ""))
            if len(cluster) >= self.MIN_CLUSTER_SIZE:
                used.add(base.get("id", ""))
                clusters.append(cluster)
                if len(clusters) >= limit:
                    break

        return clusters

    # ------------------------------------------------------------------
    # Distillation call
    # ------------------------------------------------------------------

    def _distill_cluster(self, cluster: list[dict]) -> dict | None:
        episode_texts = "\n".join(
            f"- [{item.get('timestamp','')[:10]}] {_compact(item.get('summary',''), 160)}"
            for item in cluster[:8]
        )
        user_prompt = _DISTILL_USER.format(count=len(cluster), episodes=episode_texts)
        raw = self._call_llm(_DISTILL_SYSTEM, user_prompt, max_tokens=256)
        if not raw:
            return None
        return self._parse_json(raw)

    # ------------------------------------------------------------------
    # Concern loading + ripeness
    # ------------------------------------------------------------------

    def _load_concerns(self, path: str | None) -> list[dict]:
        import os
        import json as _json
        if not path:
            path = os.path.join(os.getenv("ERIS_MEMORY_CORE_DIR", "memory_core"), "private_concerns.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            return list(data.get("concerns", []) if isinstance(data, dict) else [])
        except Exception:
            return []

    def _ripe_concerns(self, concerns: list[dict], *, limit: int) -> list[dict]:
        """Lọc concern đủ chín để bridge."""
        ripe = []
        for c in concerns:
            if c.get("status") not in {"open", "watching"}:
                continue
            if c.get("metadata", {}).get("bridged_to_memory"):
                continue
            pressure = _safe_float(c.get("pressure", 0))
            recurrence = int(c.get("recurrence", 0) or 0)
            theme = str(c.get("theme", ""))
            # Bỏ qua theme degenerate (đã có filter trong ConcernStore, nhưng phòng thủ thêm)
            if len(theme) > 400 or theme.count("lặp") > 3:
                continue
            if pressure >= self.MIN_CONCERN_PRESSURE and recurrence >= self.MIN_CONCERN_RECURRENCE:
                ripe.append(c)
        # Sort theo pressure × recurrence để bridge concern quan trọng nhất trước
        ripe.sort(key=lambda c: _safe_float(c.get("pressure", 0)) * int(c.get("recurrence", 1) or 1), reverse=True)
        return ripe[:limit]

    # ------------------------------------------------------------------
    # Bridge call
    # ------------------------------------------------------------------

    def _bridge_concern(self, concern: dict) -> dict | None:
        theme = _compact(concern.get("theme", ""), 300)
        pressure = _safe_float(concern.get("pressure", 0))
        recurrence = int(concern.get("recurrence", 0) or 0)
        questions = "; ".join(str(q) for q in (concern.get("open_questions") or [])[:4])
        biases = "; ".join(str(b) for b in (concern.get("future_biases") or [])[:4])

        user_prompt = _BRIDGE_USER.format(
            theme=theme,
            pressure=f"{pressure:.2f}",
            recurrence=recurrence,
            questions=questions or "(none)",
            biases=biases or "(none)",
        )
        raw = self._call_llm(_BRIDGE_SYSTEM, user_prompt, max_tokens=256)
        if not raw:
            return None
        return self._parse_json(raw)

    # ------------------------------------------------------------------
    # Commit helpers
    # ------------------------------------------------------------------

    def _commit_semantic_fact(self, fact: dict, *, evidence_ids: list[str]) -> bool:
        claim = _compact(fact.get("claim", ""), 400)
        if not claim:
            return False
        confidence = min(0.92, max(0.55, _safe_float(fact.get("confidence", 0.72))))
        request = {
            "target": "semantic_memory",
            "content": claim,
            "confidence": confidence,
            "evidence_ids": [str(e) for e in evidence_ids if str(e).strip()][:8],
            "storage_permission": True,
            "permission_required": False,
            "decay_policy": fact.get("decay_policy", "slow"),
            "reason": "episodic_distillation_by_ai_curator",
            "metadata": {
                "fact_key": str(fact.get("fact_key", "")),
                "domain": str(fact.get("domain", "unknown")),
                "importance": confidence * 0.9,
                "source": "ai_curator_distillation",
            },
        }
        try:
            result = self.memory_core.commit(self._to_namespace(request))
            outcome = str(getattr(result, "outcome", "") or "")
            return outcome in {"accepted", "tentative"}
        except Exception as exc:
            logger.warning("AI curator commit failed: %s", exc)
            return False

    def _commit_from_bridge(self, decision: dict, *, evidence_ids: list[str]) -> bool:
        claim = _compact(decision.get("claim", ""), 400)
        if not claim:
            return False
        target = str(decision.get("target", "semantic_memory"))
        if target not in {"semantic_memory", "relational_memory"}:
            target = "semantic_memory"
        confidence = min(0.88, max(0.52, _safe_float(decision.get("confidence", 0.68))))
        request = {
            "target": target,
            "content": claim,
            "confidence": confidence,
            "evidence_ids": evidence_ids[:8],
            "storage_permission": True,
            "permission_required": False,
            "decay_policy": decision.get("decay_policy", "normal"),
            "reason": "concern_bridge_by_ai_curator",
            "metadata": {
                "fact_key": str(decision.get("fact_key", "")),
                "domain": str(decision.get("domain", "unknown")),
                "importance": confidence * 0.85,
                "source": "ai_curator_concern_bridge",
            },
        }
        try:
            result = self.memory_core.commit(self._to_namespace(request))
            outcome = str(getattr(result, "outcome", "") or "")
            return outcome in {"accepted", "tentative"}
        except Exception as exc:
            logger.warning("AI curator bridge commit failed: %s", exc)
            return False

    def _mark_concern_bridged(self, concern: dict, path: str | None) -> None:
        """Đánh dấu concern đã được bridge để không gọi lại."""
        import os
        import json as _json
        if not path:
            path = os.path.join(os.getenv("ERIS_MEMORY_CORE_DIR", "memory_core"), "private_concerns.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            concerns = data.get("concerns", [])
            for c in concerns:
                if c.get("id") == concern.get("id"):
                    if "metadata" not in c:
                        c["metadata"] = {}
                    c["metadata"]["bridged_to_memory"] = True
                    c["metadata"]["bridged_at"] = time.time()
                    break
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                _json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as exc:
            logger.warning("mark_concern_bridged failed: %s", exc)

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(self, system: str, user: str, *, max_tokens: int = 256) -> str | None:
        """Chỉ dùng Gemini Web curator, không mượn inner/chat chính."""
        from memory.living_user.gemini_adapter import GeminiWebUserProfileAdapter
        from memory.living_user.runtime_adapter import _extract_text

        client = getattr(self.llm_service, "curator_client", None)
        if not isinstance(client, GeminiWebUserProfileAdapter):
            client = GeminiWebUserProfileAdapter.create_if_available()
        if client is None:
            return None
        try:
            response = client.create_chat_completion(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=max_tokens,
            )
            return _extract_text(response)
        except Exception as exc:
            logger.warning("AI curator LLM call failed: %s", exc)
            return None

    def _parse_json(self, raw: str) -> dict | None:
        text = str(raw or "").strip()
        # Tìm JSON object trong text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start:end])
        except Exception:
            return None

    @staticmethod
    def _to_namespace(d: dict) -> Any:
        from types import SimpleNamespace
        return SimpleNamespace(**d)


__all__ = ["MemoryAICurator", "CuratorResult"]

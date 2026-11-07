# -*- coding: utf-8 -*-
"""
cortex/private_mind/mini_evaluators.py

4 mini-evaluator gọi API. Không có rule fallback — nếu API fail thì tick đó
không có fragment từ evaluator đó. Pool API dự phòng lo việc resilience.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Iterable, Mapping, Protocol
from concurrent.futures import ThreadPoolExecutor, as_completed

from .contracts import (
    EvidenceDossier,
    ThoughtFragment,
    clamp01,
    compact_text,
)


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "none"}


def _relevance_sig(*parts: str) -> str:
    """Chữ ký gọn cho trạng thái bằng chứng. Dùng để edge-trigger: chỉ nghĩ lại
    khi bằng chứng đổi, không nghĩ lại đúng cùng một thứ."""
    raw = "|".join(p for p in parts if p)
    if not raw:
        return ""
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16]


class MiniEvaluatorModelAdapter(Protocol):
    def evaluate_fragment(self, evaluator_name: str, dossier: EvidenceDossier) -> Mapping[str, Any] | None:
        ...


def _evidence_ids(dossier: EvidenceDossier, *extra: str) -> tuple[str, ...]:
    ids = list(dossier.evidence_ids[:16])
    for e in extra:
        if e and e not in ids:
            ids.append(e)
    return tuple(ids[:20])


def _from_model(adapter: Any, evaluator_name: str, dossier: EvidenceDossier) -> ThoughtFragment | None:
    """Gọi model adapter, trả None nếu fail hoặc result yếu."""
    fn = getattr(adapter, "evaluate_fragment", None)
    if not callable(fn):
        return None
    try:
        raw = fn(evaluator_name, dossier)
    except Exception:
        return None
    if not isinstance(raw, Mapping):
        return None
    claim = compact_text(str(raw.get("claim") or ""), 480)
    if not claim:
        return None
    confidence = clamp01(raw.get("confidence", 0.0))
    pressure = clamp01(raw.get("pressure", 0.0))
    if confidence < 0.15 and pressure < 0.15:
        return None
    from .contracts import RankedHypothesis, ThoughtFragment
    hypotheses = tuple(
        RankedHypothesis.from_mapping(h)
        for h in (raw.get("hypotheses") or [])
        if isinstance(h, Mapping)
    )
    theme = str(raw.get("theme") or raw.get("metadata", {}).get("theme") or "")
    return ThoughtFragment(
        source=evaluator_name,
        claim=claim,
        confidence=confidence,
        pressure=pressure,
        evidence_ids=_evidence_ids(dossier),
        suggested_question=compact_text(str(raw.get("suggested_question") or ""), 200),
        hypotheses=hypotheses,
        metadata={"theme": theme} if theme else {},
    )


class BaseMiniEvaluator:
    # Evaluator không khai báo cổng relevance thì luôn chạy (fail-open, an toàn cho
    # evaluator tùy biến được truyền vào ngoài 4 cái mặc định).
    supports_relevance_gate = False

    def __init__(self, name: str, *, model_adapter: Any = None) -> None:
        self.name = name
        self.model_adapter = model_adapter

    def evaluate(self, dossier: EvidenceDossier) -> ThoughtFragment | None:
        return _from_model(self.model_adapter, self.name, dossier)

    def relevance_signature(self, dossier: EvidenceDossier) -> str | None:
        """Trả None nếu lúc này KHÔNG có gì đáng để mảnh này nghĩ.
        Trả chữ ký nếu CÓ — chữ ký đổi khi bằng chứng liên quan đổi."""
        return None


class IntentEvaluator(BaseMiniEvaluator):
    supports_relevance_gate = True

    def __init__(self, *, model_adapter: Any = None) -> None:
        super().__init__("intent_evaluator", model_adapter=model_adapter)

    def relevance_signature(self, dossier: EvidenceDossier) -> str | None:
        # Đáng nghĩ khi có lời người dùng mới, hoặc có goal/task đang bám.
        goal = (dossier.active_goal or "").strip()
        task = (dossier.current_task or "").strip()
        last_text = last_intent = ""
        if dossier.recent_user_turns:
            t = dossier.recent_user_turns[-1]
            last_text = str(t.get("text") or "").strip()
            last_intent = str(t.get("intent") or "").strip()
        if not last_text and not goal and not task:
            return None
        return _relevance_sig("intent", last_text[:200], last_intent[:60], goal[:160], task[:120])


class MemoryResonanceEvaluator(BaseMiniEvaluator):
    supports_relevance_gate = True

    def __init__(self, *, model_adapter: Any = None) -> None:
        super().__init__("memory_resonance_evaluator", model_adapter=model_adapter)

    def relevance_signature(self, dossier: EvidenceDossier) -> str | None:
        # Cộng hưởng ký ức chỉ đáng nghĩ khi có ký ức trồi lên hoặc có lỗi cần sửa.
        hits = list(dossier.memory_hits)[:6]
        repairs = list(dossier.prior_repair_failures)[:4]
        if not hits and not repairs:
            return None
        hit_ids = ",".join(str(h.get("evidence_id") or h.get("text") or "")[:40] for h in hits)
        repair_ids = ",".join(str(r.get("summary") or "")[:40] for r in repairs)
        return _relevance_sig("memory", hit_ids, repair_ids)


class RiskAndPermissionEvaluator(BaseMiniEvaluator):
    supports_relevance_gate = True

    def __init__(self, *, model_adapter: Any = None) -> None:
        super().__init__("risk_and_permission_evaluator", model_adapter=model_adapter)

    def relevance_signature(self, dossier: EvidenceDossier) -> str | None:
        # Chỉ soi rủi ro/quyền khi thật sự có hành động sắp/đã làm hoặc có cờ quyền.
        surface = dossier.surface_outcome or {}
        runtime = dossier.runtime_state if isinstance(dossier.runtime_state, dict) else {}
        perm = runtime.get("permission_flags")
        has_action = bool(surface.get("kind") or surface.get("text") or surface.get("should_speak"))
        has_perm = bool(perm) or ("api_cost_budget" in runtime)
        if not has_action and not has_perm:
            return None
        return _relevance_sig(
            "risk",
            str(surface.get("kind") or ""),
            str(surface.get("text") or "")[:120],
            str(surface.get("should_speak") or ""),
            str(perm or ""),
            str(runtime.get("api_cost_budget") or ""),
        )


class FutureBiasEvaluator(BaseMiniEvaluator):
    supports_relevance_gate = True

    def __init__(self, *, model_adapter: Any = None) -> None:
        super().__init__("future_bias_evaluator", model_adapter=model_adapter)

    def relevance_signature(self, dossier: EvidenceDossier) -> str | None:
        # Chỉ nắn bias tương lai khi có concern đủ áp lực; lọc nhiễu áp lực nhỏ.
        concerns = [c for c in (dossier.concern_snapshots or ()) if isinstance(c, dict)]
        meaningful = [c for c in concerns if float(c.get("pressure", 0.0) or 0.0) >= 0.25]
        if not meaningful:
            return None
        meaningful.sort(key=lambda c: float(c.get("pressure", 0.0) or 0.0), reverse=True)
        parts = []
        for c in meaningful[:4]:
            theme = str(c.get("theme") or "")[:60]
            # Bucket áp lực để dao động nhỏ không sinh chữ ký mới liên tục.
            bucket = round(float(c.get("pressure", 0.0) or 0.0) * 4) / 4
            parts.append(f"{theme}:{bucket}")
        task = (dossier.current_task or "").strip()[:80]
        return _relevance_sig("future_bias", ",".join(parts), task)


USER_MODEL_EVALUATOR_NAME = "user_model_evaluator"


def _infer_user_facet(claim: str) -> str:
    """Đoán nhẹ facet từ claim; UserModelStore sẽ chuẩn hóa lại nếu sai.

    Cố ý hẹp: chỉ chạm vài tín hiệu rõ. Mặc định 'general' để store tự lo.
    """
    text = str(claim or "").lower()
    if any(k in text for k in ("rời", "vắng", "bỏ đi", "biến mất", "không quay lại", "cuối tuần", "đi đâu")):
        return "leaving_pattern"
    if any(k in text for k in ("coi eris", "xem eris", "đối xử", "công cụ", "như một", "trợ lý")):
        return "treats_eris_as"
    if any(k in text for k in ("thích", "quan tâm", "ghét", "né", "chủ đề", "hứng thú")):
        return "topic_affinity"
    if any(k in text for k in ("công nhận", "thừa nhận", "gọi tên", "nhìn nhận", "tồn tại")):
        return "recognition"
    if any(k in text for k in ("nói", "nhịp", "trả lời", "im lặng", "gõ", "cách")):
        return "interaction_style"
    if any(k in text for k in ("tin", "đáng tin", "ổn định", "thất thường", "neo")):
        return "neo_reliability"
    return "general"


class UserModelEvaluator(BaseMiniEvaluator):
    """Agent nhỏ hướng-bạn: dựng Mô_Hình_bạn (spec eris-existential-anchor R1).

    Không sinh lời thoại. Mỗi fragment mang `metadata['facet']` để UserModelStore
    định tuyến, và buộc có `evidence_ids` (kỷ luật fragment chung — R1.2).
    """

    supports_relevance_gate = True

    def __init__(self, *, model_adapter: Any = None) -> None:
        super().__init__(USER_MODEL_EVALUATOR_NAME, model_adapter=model_adapter)

    def relevance_signature(self, dossier: EvidenceDossier) -> str | None:
        # Học về bạn khi có lời/hành vi của bạn. Không có turn người dùng thì
        # không có gì mới để cập nhật chân dung.
        if not dossier.recent_user_turns:
            return None
        last = dossier.recent_user_turns[-1]
        last_text = str(last.get("text") or "").strip()
        if not last_text:
            return None
        runtime = dossier.runtime_state if isinstance(dossier.runtime_state, dict) else {}
        idle = str(runtime.get("idle_seconds") or "")
        return _relevance_sig("user_model", last_text[:200], str(idle)[:8])

    def evaluate(self, dossier: EvidenceDossier) -> ThoughtFragment | None:
        fn = getattr(self.model_adapter, "evaluate_fragment", None)
        if not callable(fn):
            return None
        try:
            raw = fn(self.name, dossier)
        except Exception:
            return None
        if not isinstance(raw, Mapping):
            return None
        claim = compact_text(str(raw.get("claim") or ""), 480)
        if not claim:
            return None
        confidence = clamp01(raw.get("confidence", 0.0))
        pressure = clamp01(raw.get("pressure", 0.0))
        if confidence < 0.15 and pressure < 0.15:
            return None
        facet = str(raw.get("facet") or "").strip() or _infer_user_facet(claim)
        return ThoughtFragment(
            source=self.name,
            claim=claim,
            confidence=confidence,
            pressure=pressure,
            evidence_ids=_evidence_ids(dossier),
            suggested_question=compact_text(str(raw.get("suggested_question") or ""), 200),
            metadata={"facet": facet},
        )


class MiniEvaluatorPipeline:
    def __init__(
        self,
        *,
        model_adapter: Any = None,
        evaluators: Iterable[BaseMiniEvaluator] | None = None,
        relevance_gate_enabled: bool | None = None,
    ) -> None:
        self.model_adapter = model_adapter
        _default_evaluators = (
            IntentEvaluator(model_adapter=model_adapter),
            MemoryResonanceEvaluator(model_adapter=model_adapter),
            RiskAndPermissionEvaluator(model_adapter=model_adapter),
            FutureBiasEvaluator(model_adapter=model_adapter),
            UserModelEvaluator(model_adapter=model_adapter),
        )
        if evaluators is not None:
            chosen = tuple(evaluators)
        else:
            chosen = _default_evaluators
            # Tốc độ: cho phép giới hạn tập evaluator qua env (giảm số fragment local
            # /tick → chu kỳ ngắn, ít giữ GPU lock). VD ERIS_PRIVATE_MIND_EVALUATORS=
            # "intent_evaluator,memory_resonance_evaluator". Để trống = chạy đủ 5.
            raw = (os.getenv("ERIS_PRIVATE_MIND_EVALUATORS", "") or "").strip()
            if raw:
                wanted = {x.strip() for x in raw.split(",") if x.strip()}
                filtered = tuple(ev for ev in _default_evaluators if getattr(ev, "name", "") in wanted)
                if filtered:
                    chosen = filtered
        self.evaluators = tuple(chosen)
        # Cổng relevance: chỉ gọi mảnh nào có bằng chứng đáng nghĩ và vừa đổi.
        # Tham số tường minh thắng env; mặc định đọc ERIS_PRIVATE_MIND_RELEVANCE_GATE.
        self.relevance_gate_enabled = (
            _env_flag("ERIS_PRIVATE_MIND_RELEVANCE_GATE", True)
            if relevance_gate_enabled is None
            else bool(relevance_gate_enabled)
        )
        self._last_signatures: dict[str, str] = {}

    def _evaluators_to_run(self, dossier: EvidenceDossier) -> tuple[list[BaseMiniEvaluator], dict[str, str]]:
        """Quyết định mảnh nào đáng gọi lúc này (không tốn API).

        Bỏ qua mảnh khi: không khai báo cổng → luôn chạy; có cổng nhưng không có
        bằng chứng (None) → im; hoặc bằng chứng trùng đúng lần nghĩ trước → im.
        """
        if not self.relevance_gate_enabled:
            return list(self.evaluators), {}
        to_run: list[BaseMiniEvaluator] = []
        fresh_sigs: dict[str, str] = {}
        for ev in self.evaluators:
            if not getattr(ev, "supports_relevance_gate", False):
                to_run.append(ev)
                continue
            try:
                sig = ev.relevance_signature(dossier)
            except Exception:
                sig = None
            if not sig:
                continue  # không có gì đáng nghĩ cho mảnh này
            if self._last_signatures.get(ev.name) == sig:
                continue  # đã nghĩ đúng trạng thái này rồi, khỏi nghĩ lại
            to_run.append(ev)
            fresh_sigs[ev.name] = sig
        return to_run, fresh_sigs

    def evaluate(self, dossier: EvidenceDossier) -> tuple[ThoughtFragment, ...]:
        to_run, fresh_sigs = self._evaluators_to_run(dossier)
        if not to_run:
            return ()

        def _run(evaluator: BaseMiniEvaluator) -> ThoughtFragment | None:
            try:
                return evaluator.evaluate(dossier)
            except Exception:
                return None

        fragments: list[ThoughtFragment] = []
        successful_evaluators: set[str] = set()
        parallel = _env_flag("ERIS_PRIVATE_MIND_PARALLEL_EVAL", False)
        max_workers = len(to_run) if parallel else 1
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run, ev): ev for ev in to_run}
            for future in as_completed(futures):
                ev = futures[future]
                result = future.result()
                # Nếu API throttle, ta vẫn có thể muốn giãn cách thay vì spam,
                # nhưng với drop_if_busy thì ta nên thử lại lượt sau.
                # Chấp nhận thử lại nếu fail hoàn toàn.
                if result is not None:
                    successful_evaluators.add(ev.name)
                    if result.claim and result.score >= 0.24:
                        fragments.append(result)

        # Chỉ ghi nhận chữ ký cho những mảnh đã CHẠY THÀNH CÔNG (dù có ra fragment hay không,
        # miễn là API trả về kết quả hợp lệ, result != None).
        for name in successful_evaluators:
            if name in fresh_sigs:
                self._last_signatures[name] = fresh_sigs[name]

        return tuple(sorted(fragments, key=lambda f: f.score, reverse=True)[:8])

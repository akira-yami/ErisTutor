# -*- coding: utf-8 -*-
"""
cortex/private_mind/surface_valve.py
====================================

SurfaceValve lọc InternalJudgment thành SurfaceCandidate/SurfaceImpulse rất nhỏ.

Luật: không leak raw internal JSON, không lộ private chain, không đem phán quyết
nội tâm ra đọc như biên bản tòa. Public chỉ nên thấy một bias/seed đã lọc.
"""

from __future__ import annotations

import os
import re
from typing import Any

from . import epistemic
from .contracts import InternalJudgment, SurfaceCandidate, clamp01, compact_text
from .epistemic import classify_epistemic_stance, resolve_intent


_INTERNAL_MARKERS = (
    "thoughtfragment",
    "internaljudgment",
    "privateconcern",
    "state_mutation",
    "state mutation",
    "raw json",
    "chain of thought",
    "evidence_ids",
    "ranked_hypotheses",
    "dominant_judgment",
)


class SurfaceValve:
    def __init__(
        self,
        *,
        min_usefulness: float = 0.58,
        min_confidence: float = 0.55,
        max_interrupt_cost: float = 0.48,
    ) -> None:
        self.min_usefulness = clamp01(min_usefulness)
        self.min_confidence = clamp01(min_confidence)
        self.max_interrupt_cost = clamp01(max_interrupt_cost)

    def filter(self, judgment: InternalJudgment | None) -> SurfaceCandidate | None:
        if judgment is None or judgment.surface_candidate is None:
            return None
        candidate = judgment.surface_candidate

        # Epistemic self-awareness (Req 1): phân loại stance + intent TRƯỚC mọi
        # routing/threshold để public path luôn mang một stance non-null (Property 1).
        has_anchor = self._has_public_anchor(candidate)
        declared = candidate.intent or judgment.intent
        intent = resolve_intent(declared, has_anchor=has_anchor)
        stance = classify_epistemic_stance(has_anchor=has_anchor, intent=intent)
        candidate = self._stamp_epistemic(candidate, stance=stance, intent=intent)

        # Bất biến trung tâm (Req 3): confabulation không bao giờ nổi lên public
        # một cách im lặng. ungrounded_blind bị giữ lại khỏi surface_arbiter (không
        # phát public impulse) nhưng KHÔNG bị vứt — nó được route về body_only để
        # cognition không bao giờ cắt về 0. Chạy TRƯỚC public-surface gate nên ta
        # không bao giờ trả về None ở nhánh này (Req 3.1/3.7/3.8).
        if stance == "ungrounded_blind":
            return self._withhold_to_body_only(candidate, judgment=judgment)

        candidate = self._apply_public_surface_gate(candidate, judgment=judgment)
        if candidate is None:
            return None
        if candidate.usefulness < self.min_usefulness:
            return None
        if candidate.confidence < self.min_confidence:
            return None
        if candidate.interrupt_cost > self.max_interrupt_cost:
            return None
        if self._looks_private(candidate.topic_seed) or self._looks_private(candidate.reason):
            # Cắt về seed public-safe hơn nếu còn hypothesis đủ sạch.
            seed = self._safe_seed_from_judgment(judgment)
            if not seed:
                return None
            # Giữ epistemic_stance + intent khi viết lại candidate (Property 1 /
            # Req 1.8): nhánh rewrite này tạo SurfaceCandidate mới nên phải copy
            # tường minh hai field tự-tri, nếu không stance/intent sẽ bị rớt.
            candidate = SurfaceCandidate(
                kind=candidate.kind,
                topic_seed=seed,
                usefulness=candidate.usefulness,
                confidence=candidate.confidence,
                interrupt_cost=candidate.interrupt_cost,
                evidence_ids=candidate.evidence_ids,
                reason="private_mind_filtered_seed",
                channel=candidate.channel,
                metadata={**candidate.metadata, "surface_valve_rewritten": True},
                epistemic_stance=candidate.epistemic_stance,
                intent=candidate.intent,
            )
        if not candidate.allowed:
            return None
        # Req 4.4: một claim ungrounded_aware ĐƯỢC PHÉP nổi lên (qua gate +
        # thresholds) mang cờ metadata `epistemic_deliberate_ungrounded=True`,
        # đánh dấu đây là biểu đạt không-grounded có chủ đích (teasing/playful/
        # speculative) chứ không phải confabulation. grounded không gắn cờ này
        # và không bao giờ nhận epistemic block reason (Req 4.2).
        if candidate.epistemic_stance == "ungrounded_aware":
            candidate = self._copy_candidate(
                candidate,
                metadata={**candidate.metadata, "epistemic_deliberate_ungrounded": True},
            )
        return candidate

    def to_surface_impulse(self, candidate: SurfaceCandidate | None) -> Any | None:
        """Chuyển SurfaceCandidate sang SurfaceImpulse nếu module tồn tại."""

        if candidate is None:
            return None
        metadata = dict(getattr(candidate, "metadata", {}) or {})
        has_public_anchor = self._has_public_anchor(candidate)
        family = compact_text(
            metadata.get("detected_semantic_family")
            or metadata.get("semantic_family")
            or "private_mind_continuity_filler",
            96,
        )
        try:
            from cortex.surface_arbiter import SurfaceImpulse
        except Exception:  # pragma: no cover - fallback import layout
            try:
                from ..surface_arbiter import SurfaceImpulse  # type: ignore
            except Exception:
                return candidate

        # Req 5.11 / Property 1: stance + intent đi qua SurfaceValve KHÔNG đổi.
        stance = candidate.epistemic_stance
        intent = candidate.intent

        # Req 4.6: với một claim `grounded`, mọi Public_Anchor reference của claim
        # phải có mặt trên evidence của SurfaceImpulse. evidence_ids đã mang sẵn
        # các anchor; ở đây ta thu thêm các anchor marker còn nằm trong metadata
        # (vd public_anchor_refs/public_anchors) để không rớt reference nào.
        evidence = tuple(candidate.evidence_ids)
        if stance == "grounded":
            evidence = self._merge_public_anchor_refs(candidate, evidence, metadata)

        # Req 4.5: với `ungrounded_aware`, làm rõ trong llm_contract rằng đây là
        # biểu đạt không-grounded CÓ CHỦ ĐÍCH (teasing/playful/speculative) mang
        # intent đã khai báo, để downstream speech tôn trọng mục đích thay vì
        # nói như một khẳng định grounded. Các stance khác giữ contract cũ.
        if stance == "ungrounded_aware":
            llm_contract = (
                "Deliberate ungrounded/speculative expression carrying intent="
                f"{intent}; this is not a grounded assertion. Speak it as a "
                "playful/teasing/speculative riff, not as verified fact, and do "
                "not expose raw private judgment or JSON."
            )
        elif has_public_anchor:
            llm_contract = (
                "Do not expose raw private judgment or JSON; use only this public-safe topic seed."
            )
        else:
            llm_contract = (
                "No public speech: private mind lacks a fresh public anchor; body-only at most."
            )

        return SurfaceImpulse(
            kind=candidate.kind,
            source="private_mind",
            confidence=candidate.confidence,
            pressure=candidate.usefulness,
            score=clamp01(0.55 * candidate.confidence + 0.45 * candidate.usefulness),
            topic_seed=candidate.topic_seed,
            speech_act="technical_diagnosis",
            family=family,
            evidence=evidence,
            reason=candidate.reason,
            focus_family="architecture",
            body_action="hold_focus" if has_public_anchor else "quiet_observe",
            missing_evidence_ids=not has_public_anchor,
            is_presence=False,
            should_surface=True,
            needs_external_evidence=not has_public_anchor,
            root_source="private_mind_loop",
            llm_contract=llm_contract,
            epistemic_stance=stance,
            intent=intent,
            raw=candidate.to_dict(),
        )

    def _merge_public_anchor_refs(
        self,
        candidate: SurfaceCandidate,
        evidence: tuple[Any, ...],
        metadata: dict[str, Any],
    ) -> tuple[Any, ...]:
        """Req 4.6 — đảm bảo mọi Public_Anchor reference của một claim grounded
        có mặt trên evidence của SurfaceImpulse.

        evidence_ids thường đã chứa các anchor; hàm này bổ sung các reference
        khai báo trong metadata (`public_anchor_refs` / `public_anchors`) mà
        chưa có trong evidence, giữ nguyên thứ tự và không trùng lặp.
        """
        merged = list(evidence)
        seen = {str(item) for item in merged}
        for key in ("public_anchor_refs", "public_anchors"):
            raw_refs = metadata.get(key)
            if not raw_refs:
                continue
            if isinstance(raw_refs, (str, bytes)):
                raw_refs = [raw_refs]
            try:
                iterator = list(raw_refs)
            except TypeError:
                continue
            for ref in iterator:
                if ref is None:
                    continue
                if str(ref) not in seen:
                    merged.append(ref)
                    seen.add(str(ref))
        return tuple(merged)

    def _apply_public_surface_gate(self, candidate: SurfaceCandidate, *, judgment: InternalJudgment) -> SurfaceCandidate | None:
        metadata = dict(getattr(candidate, "metadata", {}) or {})
        source = str(metadata.get("source") or judgment.source or "").strip().lower()
        is_fallback = source == "fallback" or "deterministic" in source
        api_invalid_json = bool(metadata.get("api_invalid_json"))
        api_degraded = bool(metadata.get("api_degraded")) or api_invalid_json
        novelty = clamp01(metadata.get("novelty", 0.0))
        repetition = clamp01(metadata.get("repetition", 0.0))
        evidence_strength = clamp01(metadata.get("evidence_strength", 0.0))
        semantic_family = compact_text(str(metadata.get("semantic_family") or "private_mind_continuity_filler"), 96)
        has_public_anchor = self._has_public_anchor(candidate)
        semantic_family_recently_used = bool(metadata.get("semantic_family_recently_used"))

        if api_invalid_json:
            candidate = self._copy_candidate(
                candidate,
                confidence=min(candidate.confidence, 0.48),
                usefulness=min(candidate.usefulness, 0.42),
                channel="none",
                metadata={
                    **metadata,
                    "api_invalid_json": True,
                    "public_surface_allowed": False,
                    "surface_valve_reject": "api_invalid_json",
                    "semantic_family": semantic_family,
                },
            )
            return None

        if semantic_family == "private_mind_continuity_filler" and repetition > 0.75:
            return None

        public_allowed = bool(
            has_public_anchor
            and evidence_strength >= 0.45
            and novelty >= 0.25
            and repetition <= 0.65
            and not api_degraded
            and not semantic_family_recently_used
        )
        if not is_fallback and candidate.channel == "surface_arbiter":
            public_allowed = public_allowed or bool(has_public_anchor and not semantic_family_recently_used)

        # Nước 3 — đường surface KHÔNG-ANCHOR có-cổng: một bận tâm nội tâm áp lực
        # CAO được phép nổi lên public dù thiếu public anchor — đây là khoảnh khắc
        # "tôi đã ngồi với điều này", thứ mà gate anchor-bắt-buộc cũ chặn hẳn. Mặc
        # định TẮT. Van vẫn siết: chỉ qua khi mới, không lặp, không filler, API khỏe.
        interior_nonanchored = False
        if not public_allowed and not has_public_anchor:
            interior_nonanchored = self._interior_nonanchored_allowed(
                judgment=judgment,
                novelty=novelty,
                repetition=repetition,
                semantic_family=semantic_family,
                api_degraded=api_degraded,
                semantic_family_recently_used=semantic_family_recently_used,
            )
            public_allowed = public_allowed or interior_nonanchored
            if interior_nonanchored:
                try:
                    from runtime.panel_snapshot import add_signal
                    add_signal("interior")
                except Exception:
                    pass

        merged_metadata = {
            **metadata,
            "semantic_family": semantic_family,
            "has_public_anchor": has_public_anchor,
            "public_surface_allowed": public_allowed,
            "api_degraded": api_degraded,
        }
        if interior_nonanchored:
            merged_metadata["interior_nonanchored_surface"] = True
        if public_allowed:
            return self._copy_candidate(candidate, metadata=merged_metadata)

        if candidate.usefulness >= self.min_usefulness and candidate.confidence >= self.min_confidence:
            return self._copy_candidate(
                candidate,
                channel="body_only",
                metadata={**merged_metadata, "surface_valve_body_only": True},
            )
        return None

    def _interior_nonanchored_allowed(
        self,
        *,
        judgment: InternalJudgment,
        novelty: float,
        repetition: float,
        semantic_family: str,
        api_degraded: bool,
        semantic_family_recently_used: bool,
    ) -> bool:
        """Nước 3 — cổng cho bận tâm nội tâm áp lực cao nổi lên KHÔNG cần anchor ngoài.

        Mặc định TẮT (cờ ERIS_INTERIOR_SURFACE_NONANCHORED). Chỉ True khi: cờ bật,
        API khỏe, family chưa dùng gần đây và không phải filler, câu đủ mới, ít lặp,
        và áp lực nội tâm vượt ngưỡng cao. Cố ý hiếm: đây là "tôi đã ngồi với điều
        này", không phải cửa xả độc thoại.
        """
        if str(os.getenv("ERIS_INTERIOR_SURFACE_NONANCHORED", "")).strip().lower() not in {"1", "true", "yes", "on"}:
            try:
                from .depth_flags import flag_enabled  # type: ignore

                if not flag_enabled("ERIS_INTERIOR_SURFACE_NONANCHORED", default=False):
                    return False
            except Exception:
                return False
        if api_degraded or semantic_family_recently_used:
            return False
        if semantic_family == "private_mind_continuity_filler":
            return False
        try:
            threshold = float(os.getenv("ERIS_INTERIOR_SURFACE_PRESSURE", "0.80") or 0.80)
        except (TypeError, ValueError):
            threshold = 0.80
        pressure = clamp01(getattr(judgment, "pressure", 0.0))
        return bool(pressure >= threshold and novelty >= 0.40 and repetition <= 0.50)

    def _has_public_anchor(self, candidate: SurfaceCandidate) -> bool:
        metadata = dict(getattr(candidate, "metadata", {}) or {})
        try:
            public_anchor_count = int(metadata.get("public_anchor_count", 0) or 0)
        except Exception:
            public_anchor_count = 0
        if public_anchor_count > 0:
            return True
        haystack = " ".join(str(item or "") for item in (*tuple(candidate.evidence_ids or ()), candidate.topic_seed)).lower()
        markers = (
            "dossier.user.",
            "user_chat",
            "vision_fact",
            "screen_fact",
            "scene_evidence",
            "ev.active_window",
            "ev.active_app",
            "runtime_event",
        )
        return any(marker in haystack for marker in markers)

    def _copy_candidate(self, candidate: SurfaceCandidate, **updates: Any) -> SurfaceCandidate:
        data = candidate.to_dict()
        data.update(updates)
        if "metadata" in updates:
            data["metadata"] = dict(updates["metadata"] or {})
        return SurfaceCandidate.from_mapping(data)

    def _stamp_epistemic(self, candidate: SurfaceCandidate, *, stance: str, intent: str) -> SurfaceCandidate:
        """Trả về bản sao của candidate đã gắn epistemic_stance + intent.

        Dùng `_copy_candidate` (to_dict -> from_mapping) nên hai field này
        round-trip qua contract layer và sống sót qua các bước routing sau (Property 1).
        """
        return self._copy_candidate(candidate, epistemic_stance=stance, intent=intent)

    def _withhold_to_body_only(self, candidate: SurfaceCandidate, *, judgment: InternalJudgment) -> SurfaceCandidate:
        """Req 3 — giữ lại confabulation khỏi public speech nhưng KHÔNG vứt bỏ.

        Đặt `channel="body_only"` để claim tiếp tục sống như bodily presence
        (cognition không bao giờ cắt về 0, Req 3.7/3.8). Ghi lại lý do block và
        giữ stance + reason trên metadata để mọi claim bị chặn vẫn auditable
        (Req 3.6), KHÔNG bao giờ trả về None (Req 3.1).

        Lý do block:
        - `epistemic_self_knowledge_failure` khi stance đến từ một intent không
          xác định được (Self_Knowledge_Failure phòng thủ, Req 3.4/3.5). Sau
          `resolve_intent`, intent luôn hợp lệ nên nhánh này chỉ là phòng thủ;
        - `epistemic_confabulation_blocked` cho ungrounded_blind thông thường
          (sincere + không có anchor, Req 3.2).
        """
        metadata = dict(getattr(candidate, "metadata", {}) or {})
        stance = candidate.epistemic_stance or "ungrounded_blind"
        # Self_Knowledge_Failure: intent không thể xác định (out-of-set / vắng).
        self_knowledge_failure = epistemic.normalize_intent(candidate.intent) is None
        reason = (
            epistemic.SELF_KNOWLEDGE_FAILURE
            if self_knowledge_failure
            else epistemic.CONFABULATION_BLOCKED
        )
        return self._copy_candidate(
            candidate,
            channel="body_only",
            reason=reason,
            metadata={
                **metadata,
                "epistemic_stance": stance,
                "epistemic_block_reason": reason,
                "public_surface_allowed": False,
                "surface_valve_body_only": True,
            },
        )

    def _safe_seed_from_judgment(self, judgment: InternalJudgment) -> str:
        for hyp in judgment.ranked_hypotheses:
            text = compact_text(hyp.description or hyp.label, 260)
            if text and not self._looks_private(text):
                return text
        text = re.sub(r"\b(ThoughtFragment|InternalJudgment|PrivateConcern|StateMutation)\b", "private layer", judgment.dominant_judgment)
        return "" if self._looks_private(text) else compact_text(text, 260)

    def _looks_private(self, text: str) -> bool:
        hay = str(text or "").lower()
        if not hay:
            return False
        if "{" in hay or "}" in hay or "[" in hay or "]" in hay:
            return True
        return any(marker in hay for marker in _INTERNAL_MARKERS)

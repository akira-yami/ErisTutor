# -*- coding: utf-8 -*-
"""Phase 5 - Meta-Cognitive Audit Worker.

MetaCognitiveAuditAgent là hệ miễn dịch của Blackboard. Nó không làm ERIS đạo đức
hơn, dịu hơn, hay giống assistant ngoan hơn. Nó chỉ bắt các lỗi nhận thức nền:
loop, spam fragment, liên kết bịa, thiếu evidence, shadow chạy quá đà, và overfit
vào khoảng im của user.

Điểm quan trọng: audit có quyền sửa Blackboard thật qua `audit_blackboard()`.
Nếu chỉ sinh một fragment báo cáo rồi để rác sống tiếp, đó không phải audit. Đó là
một biên bản họp, và repo này không cần thêm nghi lễ hành chính.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Iterable

from ..blackboard import BlackboardSnapshot, SubconsciousBlackboard
from ..thought_fragment import ThoughtFragment, clamp01, clean_text
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import context_evidence_ids, first_present, safe_get


@dataclass(slots=True, frozen=True)
class MetaAuditFinding:
    """Một phát hiện audit đủ nhỏ để log/test."""

    kind: str
    severity: float
    action: str
    fragment_ids: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "severity": round(float(self.severity), 3),
            "action": self.action,
            "fragment_ids": list(self.fragment_ids),
            "reason": self.reason,
        }


@dataclass(slots=True, frozen=True)
class MetaAuditResult:
    """Kết quả audit một BlackboardSnapshot."""

    findings: tuple[MetaAuditFinding, ...]
    quarantine_ids: tuple[str, ...]
    softened_fragments: tuple[ThoughtFragment, ...]
    audit_fragments: tuple[ThoughtFragment, ...]
    reset_slice: bool = False

    @property
    def has_blocking_issue(self) -> bool:
        return any(finding.severity >= 0.70 for finding in self.findings)

    @property
    def hallucination_detected(self) -> bool:
        return any(finding.kind == "hallucinated_link" for finding in self.findings)

    @property
    def runaway_shadow_detected(self) -> bool:
        return any(finding.kind == "runaway_shadow" for finding in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "findings": [finding.to_dict() for finding in self.findings],
            "quarantine_ids": list(self.quarantine_ids),
            "softened_fragment_ids": [fragment.id for fragment in self.softened_fragments],
            "audit_fragment_ids": [fragment.id for fragment in self.audit_fragments],
            "reset_slice": bool(self.reset_slice),
            "has_blocking_issue": self.has_blocking_issue,
            "hallucination_detected": self.hallucination_detected,
            "runaway_shadow_detected": self.runaway_shadow_detected,
        }


class MetaCognitiveAuditAgent(RuleBasedWorker):
    """Bắt loop/spam/hallucinated link/evidence missing/runaway shadow.

    Worker này chạy qua đường riêng `audit_blackboard`, không qua pass tạo fragment
    bình thường. Lý do rất đơn giản: nó cần quyền nhìn cả Blackboard và dọn rác.
    Một cảnh sát không được chạm vào hiện trường thì chỉ là decor.
    """

    name = "meta_cognitive_audit"
    audit_blackboard_worker = True
    run_after_synthesis = False

    def __init__(
        self,
        *,
        spam_threshold: int = 8,
        loop_threshold: int = 6,
        keep_per_bad_group: int = 2,
    ) -> None:
        self.spam_threshold = max(3, int(spam_threshold or 8))
        self.loop_threshold = max(3, int(loop_threshold or 6))
        self.keep_per_bad_group = max(0, int(keep_per_bad_group or 2))
        self.last_result = MetaAuditResult((), (), (), ())

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        # Loop chính gọi audit_blackboard(). Method này chỉ để giữ interface worker.
        return ()

    def audit_blackboard(
        self,
        blackboard: SubconsciousBlackboard,
        snapshot: BlackboardSnapshot,
        context: WorkerContext,
    ) -> MetaAuditResult:
        """Inspect + mutate Blackboard an toàn.

        - quarantine: xóa fragment quá rủi ro.
        - soften: hạ confidence/tắt surface của fragment thiếu evidence.
        - reset_slice: dọn nhóm spam/loop, giữ lại vài mảnh mạnh nhất để không mất
          hoàn toàn tín hiệu.
        """

        result = self.inspect(snapshot, context)
        if result.quarantine_ids:
            blackboard.remove_many(result.quarantine_ids)
        for fragment in result.softened_fragments:
            blackboard.replace(fragment)
        for fragment in result.audit_fragments:
            try:
                blackboard.submit(fragment)
            except Exception:
                continue
        self.last_result = result
        return result

    def inspect(self, snapshot: BlackboardSnapshot, context: WorkerContext) -> MetaAuditResult:
        fragments = tuple(snapshot.fragments)
        now = float(snapshot.created_at or safe_get(context, "now", 0.0) or 0.0) or None
        findings: list[MetaAuditFinding] = []
        quarantine: list[str] = []
        soften: list[ThoughtFragment] = []

        spam_ids = self._detect_spam_or_loop(fragments, findings)
        quarantine.extend(spam_ids)

        for fragment in fragments:
            if self._needs_evidence(fragment):
                severity = 0.62 if fragment.surface_allowed else 0.46
                findings.append(MetaAuditFinding(
                    kind="evidence_missing",
                    severity=severity,
                    action="request_evidence_and_lower_confidence",
                    fragment_ids=(fragment.id,),
                    reason="surface/claim fragment thiếu evidence_ids",
                ))
                soften.append(self._soften_fragment(fragment, now=now, tag="audit_evidence_missing"))

            if self._looks_like_hallucinated_link(fragment):
                findings.append(MetaAuditFinding(
                    kind="hallucinated_link",
                    severity=0.78 if fragment.surface_allowed else 0.64,
                    action="quarantine_fragment",
                    fragment_ids=(fragment.id,),
                    reason="fragment nối quan hệ nhân-quả/ẩn ý nhưng evidence quá mỏng",
                ))
                quarantine.append(fragment.id)

            if self._looks_like_overfit_silence(fragment):
                findings.append(MetaAuditFinding(
                    kind="overfit_to_user_silence",
                    severity=0.74,
                    action="reset_blackboard_slice",
                    fragment_ids=(fragment.id,),
                    reason="fragment đọc trạng thái user từ im lặng như truth",
                ))
                quarantine.append(fragment.id)

            if self._looks_like_runaway_shadow(fragment):
                findings.append(MetaAuditFinding(
                    kind="runaway_shadow",
                    severity=0.82,
                    action="quarantine_fragment",
                    fragment_ids=(fragment.id,),
                    reason="shadow/rupture pressure cao nhưng thiếu tag audit_clear hoặc evidence đủ mạnh",
                ))
                quarantine.append(fragment.id)

        contradiction_ids = self._detect_contradiction(fragments)
        if contradiction_ids:
            findings.append(MetaAuditFinding(
                kind="contradiction",
                severity=0.66,
                action="lower_confidence",
                fragment_ids=contradiction_ids,
                reason="cùng cụm vừa đẩy nói ngay vừa đẩy suppress/body_only",
            ))
            by_id = {fragment.id: fragment for fragment in fragments}
            for fragment_id in contradiction_ids:
                fragment = by_id.get(fragment_id)
                if fragment is not None:
                    soften.append(self._soften_fragment(fragment, now=now, tag="audit_contradiction"))

        quarantine_ids = tuple(dict.fromkeys(quarantine))
        softened = tuple(self._dedupe_softened(soften, quarantine_ids))
        audit_fragments = self._build_audit_fragments(findings, context, now=now)
        return MetaAuditResult(
            findings=tuple(findings),
            quarantine_ids=quarantine_ids,
            softened_fragments=softened,
            audit_fragments=audit_fragments,
            reset_slice=any(f.action == "reset_blackboard_slice" for f in findings),
        )

    def _detect_spam_or_loop(self, fragments: tuple[ThoughtFragment, ...], findings: list[MetaAuditFinding]) -> tuple[str, ...]:
        groups: dict[str, list[ThoughtFragment]] = {}
        for fragment in fragments:
            key = f"{fragment.kind}|{self._semantic_seed(fragment.content)}"
            groups.setdefault(key, []).append(fragment)

        to_quarantine: list[str] = []
        for key, group in groups.items():
            threshold = self.loop_threshold if self._loopish_key(key) else self.spam_threshold
            if len(group) < threshold:
                continue
            ranked = sorted(group, key=lambda item: item.priority_score(), reverse=True)
            bad = ranked[self.keep_per_bad_group:]
            ids = tuple(fragment.id for fragment in bad)
            if not ids:
                continue
            kind = "thought_looping" if self._loopish_key(key) else "fragment_spam"
            findings.append(MetaAuditFinding(
                kind=kind,
                severity=0.80 if kind == "thought_looping" else 0.72,
                action="reset_blackboard_slice",
                fragment_ids=ids,
                reason=f"nhóm fragment lặp quá ngưỡng: {key[:90]}",
            ))
            to_quarantine.extend(ids)
        return tuple(dict.fromkeys(to_quarantine))

    def _needs_evidence(self, fragment: ThoughtFragment) -> bool:
        if fragment.evidence_ids:
            return False
        if fragment.kind in {"shadow_pressure", "shadow_theme", "rehearsal_result"}:
            return False
        claimish = fragment.surface_allowed or fragment.urgency >= 0.64 or fragment.confidence >= 0.70
        return bool(claimish)

    def _looks_like_hallucinated_link(self, fragment: ThoughtFragment) -> bool:
        text = fragment.content.lower()
        causal_markers = ("vì", "nên", "chứng tỏ", "suy ra", "liên quan", "ẩn ý", "nghĩa là")
        daydreamish = any(tag in {"daydream", "poetic", "association", "weird_link"} for tag in fragment.tags)
        # Không có evidence hoàn toàn thì để evidence_missing xử bằng soften/request_evidence.
        # Hallucinated_link dành cho trường hợp nguy hiểm hơn: có một neo quá mỏng
        # nhưng đã dám nối quan hệ nhân-quả hoặc daydream thành surface.
        weak_evidence = 0 < len(fragment.evidence_ids) < 2 and fragment.confidence >= 0.58
        return (daydreamish or any(marker in text for marker in causal_markers)) and weak_evidence and fragment.surface_allowed

    def _looks_like_overfit_silence(self, fragment: ThoughtFragment) -> bool:
        text = fragment.content.lower()
        silence_terms = ("im lặng", "silent", "silence", "idle", "không nói")
        overread_terms = ("mệt", "buồn", "ghét", "bỏ mặc", "không cần", "chán", "giận")
        has_silence = any(term in text for term in silence_terms) or "silence" in fragment.tags
        has_overread = any(term in text for term in overread_terms)
        no_real_anchor = not fragment.evidence_ids or all(str(e).startswith(("subconscious.", "silence.")) for e in fragment.evidence_ids)
        return bool(has_silence and has_overread and no_real_anchor)

    def _looks_like_runaway_shadow(self, fragment: ThoughtFragment) -> bool:
        shadowish = fragment.kind in {"rupture_candidate", "rupture_mode_bias", "shadow_pressure"} or any(
            tag in {"shadow", "rupture", "dignity_defense"} for tag in fragment.tags
        )
        if not shadowish:
            return False
        if "audit_clear" in fragment.tags:
            return False
        if fragment.kind in {"shadow_pressure", "shadow_theme", "rupture_risk"} and fragment.privacy_level == "private":
            return False
        too_hot = fragment.urgency >= 0.78 or fragment.affect_charge >= 0.78 or fragment.surface_allowed
        return bool(too_hot and len(fragment.evidence_ids) < 2)

    def _detect_contradiction(self, fragments: tuple[ThoughtFragment, ...]) -> tuple[str, ...]:
        by_theme: dict[str, list[ThoughtFragment]] = {}
        for fragment in fragments:
            key = fragment.tags[0] if fragment.tags else self._semantic_seed(fragment.content)
            by_theme.setdefault(key, []).append(fragment)
        ids: list[str] = []
        for group in by_theme.values():
            texts = "\n".join(fragment.content.lower() for fragment in group)
            says_speak = any(term in texts for term in ("say_now", "nên nói", "surface", "speak"))
            says_suppress = any(term in texts for term in ("suppress", "body_only", "stay silent", "im tiếp", "giữ lại"))
            if says_speak and says_suppress and len(group) >= 2:
                ids.extend(fragment.id for fragment in group)
        return tuple(dict.fromkeys(ids))

    def _soften_fragment(self, fragment: ThoughtFragment, *, now: float | None, tag: str) -> ThoughtFragment:
        return replace(
            fragment,
            confidence=clamp01(fragment.confidence * 0.58),
            urgency=clamp01(fragment.urgency * 0.62),
            affect_charge=clamp01(fragment.affect_charge * 0.72),
            surface_allowed=False,
            tags=tuple(dict.fromkeys((*fragment.tags, tag, "meta_audit_softened")))[:24],
            updated_at=float(now or fragment.updated_at),
        )

    def _dedupe_softened(self, fragments: list[ThoughtFragment], quarantine_ids: tuple[str, ...]) -> tuple[ThoughtFragment, ...]:
        quarantine_set = set(quarantine_ids)
        by_id: dict[str, ThoughtFragment] = {}
        for fragment in fragments:
            if fragment.id in quarantine_set:
                continue
            by_id[fragment.id] = fragment
        return tuple(by_id.values())

    def _build_audit_fragments(
        self,
        findings: list[MetaAuditFinding],
        context: WorkerContext,
        *,
        now: float | None,
    ) -> tuple[ThoughtFragment, ...]:
        if not findings:
            return ()
        evidence_ids = context_evidence_ids(context, fallback_prefix="meta_audit")
        by_kind = {finding.kind for finding in findings}
        max_severity = max(finding.severity for finding in findings)
        actions = ", ".join(sorted({finding.action for finding in findings}))
        summary = "; ".join(f"{finding.kind}:{finding.action}" for finding in findings[:5])
        fragments: list[ThoughtFragment] = [
            ThoughtFragment.create(
                source_agent=self.name,
                kind="meta_audit_report",
                content=f"MetaAudit phát hiện {len(findings)} vấn đề ({summary}); actions={actions}.",
                evidence_ids=evidence_ids,
                confidence=clamp01(0.52 + max_severity * 0.30),
                urgency=clamp01(0.28 + max_severity * 0.44),
                affect_charge=clamp01(0.16 + max_severity * 0.22),
                privacy_level="private",
                surface_allowed=False,
                ttl_seconds=360,
                tags=("meta_audit", *tuple(sorted(by_kind))[:8]),
                now=now,
            )
        ]
        if any(f.action in {"request_evidence_and_lower_confidence", "quarantine_fragment"} for f in findings):
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="repair_needed",
                    content="Một số fragment cần evidence/repair trước khi được rehearse hoặc surface; không cho chúng thành public speech trong tick này.",
                    evidence_ids=evidence_ids,
                    confidence=0.72,
                    urgency=clamp01(0.40 + max_severity * 0.32),
                    affect_charge=0.24,
                    privacy_level="private",
                    surface_allowed=False,
                    ttl_seconds=420,
                    tags=("meta_audit", "repair_needed"),
                    now=now,
                )
            )
        return tuple(fragments)

    @staticmethod
    def _semantic_seed(content: str) -> str:
        text = clean_text(content, limit=220).lower()
        text = re.sub(r"\b\d+(?:\.\d+)?\b", "#", text)
        text = re.sub(r"[^\w\s#]+", " ", text)
        tokens = [token for token in text.split() if len(token) >= 2]
        # Bỏ một ít số thứ tự/nhiễu phổ biến để "bạn ấy đang mệt 1..20" gom được.
        stop = {"fragment", "seed", "lần", "thu", "thứ"}
        tokens = [token for token in tokens if token not in stop]
        return "_".join(tokens[:10]) or "empty"

    @staticmethod
    def _loopish_key(key: str) -> bool:
        lowered = key.lower()
        return any(term in lowered for term in ("mệt", "im_lặng", "silent", "loop", "suppressed", "rupture"))

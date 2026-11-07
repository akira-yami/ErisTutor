# -*- coding: utf-8 -*-
"""Cognitive capabilities bounded phục vụ vòng điều khiển của Executive.

Đây là adapter runtime: nó đọc projection công khai của deliberation frame và trả
về finding có kiểu. Nó không được trực tiếp sửa Subject Core. Khi có worker/model
chuyên biệt hơn, chỉ cần thay implementation của ``SubjectCognitionPort`` mà không
đổi luật tích hợp domain.
"""
from __future__ import annotations

import time
import uuid
from typing import Callable, Protocol, Sequence

from cortex.subject.contracts import SubjectDeliberationFrame
from cortex.subject.deliberation import (
    CognitiveRequestKind,
    CognitiveReviewResult,
    ProposalAdjustment,
)


class SubjectCognitionPort(Protocol):
    def execute(
        self,
        frame: SubjectDeliberationFrame,
        requests: Sequence[CognitiveRequestKind],
    ) -> tuple[CognitiveReviewResult, ...]: ...


class BoundedSubjectCognition:
    """Implementation deterministic ban đầu cho các cognitive request.

    Các con số là policy versioned, không phải "cảm xúc thật". Điểm quan trọng là
    chúng nằm ở cognitive adapter và trả finding minh bạch, thay vì ẩn trong
    SubjectService như thể domain tự nảy ra phép cộng 0.18 từ hư không.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def execute(
        self,
        frame: SubjectDeliberationFrame,
        requests: Sequence[CognitiveRequestKind],
    ) -> tuple[CognitiveReviewResult, ...]:
        return tuple(self._execute_one(frame, request) for request in requests)

    def _execute_one(
        self,
        frame: SubjectDeliberationFrame,
        request: CognitiveRequestKind,
    ) -> CognitiveReviewResult:
        adjustments: list[ProposalAdjustment] = []
        dissent_need_ids = tuple(
            dict.fromkeys(
                need
                for coalition in frame.coalitions[1:]
                for need in coalition.supporting_need_ids
            )
        )

        for proposal in frame.proposals:
            args = dict(proposal.arguments)
            capability_path = proposal.action_kind in {"investigate", "request_capability"} or bool(
                args.get("capability_route", False)
            )
            if request is CognitiveRequestKind.VERIFY_EVIDENCE:
                if capability_path:
                    adjustments.append(ProposalAdjustment(
                        proposal_ref=proposal.proposal_id,
                        expected_epistemic_value_delta=0.18,
                        expected_action_efficacy_delta=0.06,
                        argument_annotations={"finding": "epistemic_path_strengthened"},
                    ))
                elif proposal.action_kind == "speak":
                    adjustments.append(ProposalAdjustment(
                        proposal_ref=proposal.proposal_id,
                        uncertainty_delta=0.08,
                        argument_annotations={"finding": "ungrounded_speech_penalized"},
                    ))
            elif request is CognitiveRequestKind.REDUCE_INTRUSION:
                if proposal.action_kind == "speak":
                    adjustments.append(ProposalAdjustment(
                        proposal_ref=proposal.proposal_id,
                        substrate_cost_delta=0.12,
                        commitment_conflict_delta=0.10,
                        argument_annotations={"finding": "public_move_cost_increased"},
                    ))
                elif proposal.action_kind in {"silence", "body_only"}:
                    adjustments.append(ProposalAdjustment(
                        proposal_ref=proposal.proposal_id,
                        expected_need_relief_delta=0.12,
                        argument_annotations={"finding": "reversible_move_strengthened"},
                    ))
            elif request is CognitiveRequestKind.PRESERVE_CONTINUITY:
                if "continuity" in proposal.supporting_need_ids:
                    adjustments.append(ProposalAdjustment(
                        proposal_ref=proposal.proposal_id,
                        expected_need_relief_delta=0.18,
                        value_congruence_delta=0.10,
                        argument_annotations={"finding": "continuity_support_strengthened"},
                    ))
            elif request is CognitiveRequestKind.ESTIMATE_CAPABILITY:
                if capability_path:
                    adjustments.append(ProposalAdjustment(
                        proposal_ref=proposal.proposal_id,
                        expected_action_efficacy_delta=0.10,
                        expected_epistemic_value_delta=0.10,
                        argument_annotations={"finding": "capability_path_recalibrated"},
                    ))
            elif request is CognitiveRequestKind.INSPECT_DISSENT:
                adjustments.append(ProposalAdjustment(
                    proposal_ref=proposal.proposal_id,
                    argument_annotations={"dissenting_need_ids": list(dissent_need_ids)},
                ))

        summary_by_kind = {
            CognitiveRequestKind.VERIFY_EVIDENCE: "đã so sánh đường phát ngôn với đường kiểm chứng",
            CognitiveRequestKind.REDUCE_INTRUSION: "đã đánh giá lại chi phí xâm lấn của hành động công khai",
            CognitiveRequestKind.PRESERVE_CONTINUITY: "đã đo lại đóng góp của từng proposal cho continuity",
            CognitiveRequestKind.ESTIMATE_CAPABILITY: "đã hiệu chỉnh đường dùng capability theo efficacy hiện tại",
            CognitiveRequestKind.INSPECT_DISSENT: "đã đưa nhu cầu bất đồng vào projection review",
        }
        evidence_refs = tuple(
            dict.fromkeys(
                ref
                for proposal in frame.proposals
                for ref in proposal.evidence_refs
            )
        )[:24]
        return CognitiveReviewResult(
            result_id=f"cognitive-review:{self._id_factory()}",
            frame_id=frame.frame_id,
            request_kind=request,
            summary=summary_by_kind[request],
            confidence=0.90,
            adjustments=tuple(adjustments),
            evidence_refs=evidence_refs,
        )


__all__ = ["BoundedSubjectCognition", "SubjectCognitionPort"]

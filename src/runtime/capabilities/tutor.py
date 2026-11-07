# -*- coding: utf-8 -*-
"""TutorCapability V12.

``TutorExecutive`` vẫn giữ intelligence sư phạm/mastery/evidence, nhưng adapter
này chỉ trả *advice/evidence* cho RootAgent. Nó không có API phát public text.

Khác kiến trúc cũ, capability này KHÔNG được gọi ở mọi user turn. RootAgent chỉ
invoke nó khi chính agent nhận thấy tình huống cần dạy, đánh giá hoặc tiếp tục
một learning interaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from runtime.conversation.contracts import DeliveryReceipt
from runtime.tutor.learner_context import learner_profile_from_living_user, learner_continuity_snapshot


@dataclass(frozen=True, slots=True)
class TutorAdvice:
    active: bool = False
    assessment: Mapping[str, Any] | None = None
    teaching_move: Mapping[str, Any] | None = None
    expects_learner_response: bool = False
    learner_belief: Mapping[str, Any] | None = None
    learner_state: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "assessment": dict(self.assessment or {}),
            "teaching_move": dict(self.teaching_move or {}),
            "expects_learner_response": self.expects_learner_response,
            "learner_belief": dict(self.learner_belief or {}),
            "learner_state": dict(self.learner_state or {}),
        }


class TutorCapability:
    def __init__(self, executive: Any | None, *, logger: Any = None, living_user_source: Any = None) -> None:
        self.executive = executive
        self.logger = logger
        self.living_user_source = living_user_source
        self._last_decision = None
        # Đây là state *domain* của learning interaction, không phải public
        # conversation lifecycle. RootAgent vẫn có quyền bỏ qua tutor nếu user
        # đã đổi chủ đề.
        self._waiting_for_response = False
        self._pending_expects = False
        self._activity_workspace = None
        self._pending_activity_receipts = []
        self._settled_activity_receipts: list[tuple[str, int, str, str]] = []

    def advise(
        self,
        *,
        user_text: str,
        turn_id: str,
        recent_history: Sequence[Mapping[str, Any]] = (),
        response_to_previous: bool | None = None,
    ) -> TutorAdvice:
        if self.executive is None:
            return TutorAdvice()
        if response_to_previous is None:
            response_to_previous = self._waiting_for_response
        assessment = None
        # Mỗi invocation đọc profile hiện hành. Không cache để correction/forget có hiệu lực.
        learner = learner_profile_from_living_user(self.living_user_source)
        learner_kwargs = {"learner": learner} if self.living_user_source is not None else {}
        try:
            if response_to_previous:
                assessment_obj = self.executive.observe_response(
                    user_text, turn_id=turn_id, recent_history=recent_history, **learner_kwargs
                )
                assessment = assessment_obj.to_dict() if assessment_obj is not None else None
            decision = self.executive.plan_next_move(
                user_text, turn_id=turn_id, recent_history=recent_history, **learner_kwargs
            )
            self._last_decision = decision
            belief = {}
            if decision is not None:
                try:
                    kp_id = str(getattr(getattr(decision, "move", None), "kp_id", "") or "")
                    if kp_id:
                        belief = self.executive.learning.learner_belief(kp_id).to_dict()
                except Exception:
                    pass  # profile tùy chọn; không làm gián đoạn Root
            if decision is None:
                self._waiting_for_response = False
                self._pending_expects = False
                return TutorAdvice(
                    active=bool(assessment),
                    assessment=assessment,
                    learner_state=learner_continuity_snapshot(
                        getattr(self.executive, "learning", None), source=self.living_user_source
                    ),
                )
            writer_context = decision.to_writer_context()
            move = writer_context.get("move") if isinstance(writer_context, Mapping) else None
            constraints = writer_context.get("delivery_constraints") if isinstance(writer_context, Mapping) else None
            expects = bool(constraints.get("requires_user_response")) if isinstance(constraints, Mapping) else False
            # Root mới dự định phát câu hỏi. Chỉ delivery receipt THÀNH CÔNG
            # mới biến nó thành câu hỏi user thực sự đã nhận để có thể chấm.
            self._pending_expects = expects
            self._waiting_for_response = False
            return TutorAdvice(
                active=True,
                assessment=assessment,
                teaching_move=dict(move or {}) if isinstance(move, Mapping) else {},
                expects_learner_response=expects,
                learner_belief=belief,
                learner_state=learner_continuity_snapshot(
                    getattr(self.executive, "learning", None), source=self.living_user_source
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "[tutor-capability-v12] degraded: %s", exc)
            return TutorAdvice(active=bool(assessment), assessment=assessment)

    def invoke_for_root(
        self,
        *,
        arguments: Mapping[str, Any],
        event: Any,
        recent_history: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Entry point data-only cho RootAgent capability loop.

        RootAgent không cần biết ``TutorExecutive`` bên dưới có những class nào;
        nó chỉ gửi input semantic cần tư vấn và nhận lại structured evidence.
        """

        operation = str(arguments.get("operation") or "advise")
        if operation != "advise":
            try:
                return self._learning_operation(operation, arguments, event)
            except (ValueError, TypeError, RuntimeError, KeyError, OSError) as exc:
                return {"status": "failed", "operation": operation, "error": str(exc)[:300]}
        user_text = str(arguments.get("user_text") or getattr(event, "text", "") or "")[:16000]
        response_hint = arguments.get("response_to_previous")
        # Root có thể ĐỀ XUẤT chấm response, nhưng không thể tự tuyên bố
        # câu hỏi đã phát thành công. Receipt mới là evidence của delivery.
        response_to_previous = (
            self._waiting_for_response if response_hint is None else bool(response_hint) and self._waiting_for_response
        )
        advice = self.advise(
            user_text=user_text,
            turn_id=str(getattr(event, "event_id", "") or "root:tutor")[:240],
            recent_history=recent_history,
            response_to_previous=response_to_previous,
        )
        result = advice.to_dict()
        if self.executive is not None and advice.active:
            objective = str((advice.teaching_move or {}).get("objective") or user_text)
            result["sources"] = [
                {**span, "excerpt": span["excerpt"][:1600]}
                for span in self.executive.learning.search_sources(objective, limit=3)
            ]
        return result

    def bind_pending_delivery(self, *, event_id: str, session_id: str, generation: int,
                              delivery_id: str, move_id: str, artifact_bundle_ids=()) -> None:
        """Ràng buộc evidence với một public move đã COMMIT, không tin ID từ LLM.

        Gọi tại delivery adapter sau khi transport đã nhận nội dung. Activity
        bắt buộc có artifact bundle đúng trong move Root đã xác thực.
        """
        if not all((event_id, session_id, delivery_id, move_id)):
            return
        bundles = set(artifact_bundle_ids or ())
        for pending in self._pending_activity_receipts:
            if (pending.get("event_id") != event_id
                    or pending["session_id"] != session_id
                    or pending["generation"] != generation
                    or pending.get("delivery_id")):
                continue
            if pending["kind"] == "activity" and pending.get("artifact_bundle_id") not in bundles:
                continue
            pending["delivery_id"] = delivery_id
            pending["move_id"] = move_id

    def observe_delivery_receipt(self, receipt: DeliveryReceipt) -> None:
        identity = (receipt.session_id, receipt.generation, receipt.delivery_id, receipt.move_id)
        if identity in self._settled_activity_receipts:
            return  # Receipt lặp tuyệt đối không được commit lần hai.
        if self._activity_workspace is not None:
            remaining = []
            handled_operation = False
            for pending in self._pending_activity_receipts:
                # Thiếu bind hoặc sai identity => KHÔNG chấp nhận receipt.
                # Đối chiếu cả delivery_id và move_id do Kernel cấp phát.
                if (receipt.session_id != pending["session_id"]
                        or receipt.generation != pending["generation"]
                        or not pending.get("delivery_id")
                        or not pending.get("move_id")
                        or receipt.delivery_id != pending["delivery_id"]
                        or receipt.move_id != pending["move_id"]):
                    remaining.append(pending)
                    continue
                handled_operation = True
                if receipt.successful:
                    try:
                        if pending["kind"] == "activity":
                            self._activity_workspace.mark_delivered(pending["activity_id"], receipt=receipt)
                        elif pending["kind"] == "help":
                            self._activity_workspace.commit_help(pending["data"])
                        else:
                            self.executive.learning._store.record_assistance(
                                pending["interaction_id"],
                                event_id=pending["event_id"],
                                assistance=pending["assistance"],
                                hint_count=pending["hint_count"],
                            )
                    except (ValueError, RuntimeError) as exc:
                        self._log("debug", "[tutor] stale learning receipt: %s", exc)
            self._pending_activity_receipts = remaining[-32:]
            if handled_operation:
                self._settled_activity_receipts.append(identity)
                self._settled_activity_receipts = self._settled_activity_receipts[-128:]
                return
            # Có hoạt động đang chờ trong đúng session/generation nhưng receipt
            # không thuộc về nó: không fallback sang commit quyết định Tutor cũ.
            if any(p["session_id"] == receipt.session_id and p["generation"] == receipt.generation
                   for p in self._pending_activity_receipts):
                return
        if self.executive is None or self._last_decision is None:
            return
        try:
            self.executive.commit_delivery(self._last_decision, successful=bool(receipt.successful))
            self._waiting_for_response = bool(receipt.successful and self._pending_expects)
            self._pending_expects = False
            if not receipt.successful:
                suspend = getattr(self.executive, "suspend_pending_move", None)
                if callable(suspend):
                    suspend(reason="delivery_failed")
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "[tutor-capability-v12] receipt degraded: %s", exc)

    def _learning_operation(self, operation, arguments, event):
        if self.executive is None:
            return {"status": "unavailable"}
        learning = self.executive.learning
        from runtime.learning_core.records import digest
        from runtime.tutor.activities import LearningActivities

        if self._activity_workspace is None:
            self._activity_workspace = LearningActivities(learning)
        workspace = self._activity_workspace
        pending = {"event_id": event.event_id, "session_id": event.session_id,
                   "generation": event.generation}
        if operation == "start_path":
            from runtime.tutor.auto_path import propose_learning_path
            from runtime.learning_core.records import DomainSpec

            router = getattr(self.executive.reasoner, "router", None)
            if router is None:
                return {"status": "unavailable", "reason": "curriculum_model_unavailable"}
            path = propose_learning_path(router, topic_text=str(event.text))
            if path is None:
                return {"status": "unavailable", "reason": "curriculum_proposal_unavailable"}
            saved = learning._store.load_path(path.path_id)
            if saved is None:
                learning.register_domain(DomainSpec(path.domain_id, path.title, path.domain_id))
                learning.register_path(path)
            if not learning.activate_path(path.path_id):
                raise RuntimeError("curriculum_activation_failed")
            self.executive.suspend_pending_move(reason="path_changed")
            self._last_decision = None
            self._waiting_for_response = False
            self._pending_expects = False
            self._pending_activity_receipts.clear()
            return {"status": "completed", "resumed": saved is not None, **learning.curriculum_snapshot()}
        if operation == "spaces":
            return {"status": "completed", **learning.learning_spaces()}
        if operation == "switch_path":
            # Không để Root tự đoán ID rồi âm thầm chuyển môn: người dùng phải
            # thực sự nhắc đến tên môn/lộ trình hoặc chính mã của nó trong lượt này.
            requested = str(arguments.get("path_id") or "")
            known = next((row for row in learning.learning_spaces()["paths"]
                          if row["path_id"] == requested), None)
            if known is None:
                raise ValueError("unknown_learning_space")
            user_text = " ".join(str(event.text or "").casefold().split())
            if (known["title"].casefold() not in user_text and requested.casefold() not in user_text):
                raise ValueError("learning_space_not_requested_by_user")
            result = learning.switch_learning_space(requested)
            self.executive.suspend_pending_move(reason="path_changed")
            self._last_decision = None
            self._waiting_for_response = False
            self._pending_expects = False
            self._pending_activity_receipts.clear()
            return {"status": "completed", **result}
        if operation == "activity":
            item = workspace.create(event=event, preset=str(arguments.get("preset") or "python.range"))
            bundle_id = workspace.bundle(item)
            self.executive.suspend_pending_move(reason="activity_started")
            self._last_decision = None
            self._waiting_for_response = False
            self._pending_activity_receipts.append({**pending, "kind": "activity", "activity_id": item["activity_id"],
                                                    "artifact_bundle_id": bundle_id})
            return {
                "status": "completed",
                "operation": "activity",
                "artifact_bundle_id": bundle_id,
                "activity_id": item["activity_id"],
                "title": item["title"],
                "instruction": "Bài nằm trong bàn làm việc; mời người học tự thử. Chưa có kết quả làm bài.",
            }
        if operation == "activity_event":
            result = workspace.handle_event(event)
            if result.get("help_kind"):
                self._pending_activity_receipts.append({**pending, "kind": "help", "data": result})
            return result
        if operation == "readiness":
            return {
                "status": "completed",
                "capabilities": workspace.readiness(),
                "retention": learning.retention_snapshot()["readiness"],
            }
        if operation == "insights":
            # Projection read-only; Root có thể giải thích mà không coi ước lượng
            # là kết luận chắc chắn về năng lực của người dùng.
            path_id = str(arguments.get("path_id") or "")[:120]
            return {"status": "completed", **learning.learning_insights(path_id=path_id)}
        if operation == "dashboard":
            text = str(getattr(event, "text", "") or "").casefold()
            if not any(x in text for x in ("bảng", "tổng quan", "dashboard", "tiến độ", "bao cao", "báo cáo")):
                raise ValueError("dashboard_must_be_requested")
            return learning.export_learning_dashboard(path_id=str(arguments.get("path_id") or "")[:120])
        if operation == "set_retention_mode":
            # Không đổi lịch theo suy đoán của model. Yêu cầu cấu hình rõ ràng
            # phải có trong tin người dùng ở chính lượt này.
            mode = str(arguments.get("mode") or "").lower()
            text = str(getattr(event, "text", "") or "").casefold()
            requested = "fsrs" if mode == "fsrs" else "heuristic" if mode == "heuristic" else "shadow"
            if requested not in text or not any(w in text for w in ("bật", "chuyển", "dùng", "tắt", "enable", "switch", "use")):
                raise ValueError("explicit_retention_change_required")
            return {"status": "completed", **learning.set_retention_mode(mode)}
        if operation in {"preview_expansion", "apply_expansion"}:
            additions = arguments.get("additions")
            refs = arguments.get("source_refs", ())
            if not isinstance(refs, (list, tuple)) or len(refs) > 16 or any(not isinstance(x, str) for x in refs):
                raise ValueError("invalid_source_refs")
            reason = str(arguments.get("reason") or "")[:240]
            path_id = str(arguments.get("path_id") or "")[:120]
            if operation == "preview_expansion":
                return {"status": "completed", **learning.preview_expansion(
                    additions, path_id=path_id, source_refs=refs, reason=reason)}
            import unicodedata
            message = unicodedata.normalize("NFD", str(getattr(event, "text", "") or "").casefold())
            message = "".join(c for c in message if unicodedata.category(c) != "Mn")
            if not any(x in message for x in ("xac nhan", "ap dung", "them vao lo trinh", "luu lo trinh")):
                raise ValueError("explicit_expansion_approval_required")
            base_revision = str(arguments.get("base_revision") or "")
            return {"status": "completed", **learning.apply_expansion(
                additions, path_id=path_id, base_revision=base_revision,
                source_refs=refs, reason=reason)}
        if operation == "attempts":
            return {"status": "completed", "attempts": learning.attempts(str(arguments.get("kp_id") or ""))}
        if operation == "curriculum":
            return {"status": "completed", **learning.curriculum_snapshot()}
        if operation in {"preview_curriculum", "revise_curriculum"}:
            from runtime.learning_core.contracts import LearningPath, KnowledgePoint

            raw = arguments.get("path")
            if (
                not isinstance(raw, Mapping)
                or not isinstance(raw.get("knowledge_points"), list)
                or len(raw["knowledge_points"]) > 64
            ):
                raise ValueError("invalid_curriculum_path")
            path = LearningPath(
                raw["path_id"],
                raw["title"],
                raw["goal"],
                tuple(KnowledgePoint.from_mapping(p) for p in raw["knowledge_points"]),
                source="eris_auto",
                domain_id=raw.get("domain_id", ""),
            )
            preview = learning.preview_curriculum(
                path, source_refs=tuple(arguments.get("source_refs", ())), reason=str(arguments.get("reason") or "")
            )
            if operation == "preview_curriculum":
                return {"status": "completed", **preview}
            revision = learning.revise_path(
                path,
                base_revision=str(arguments.get("base_revision") or ""),
                reason=preview["reason"],
                source_refs=preview["source_refs"],
            )
            return {"status": "completed", "revision": revision, "changes": preview["changes"]}
        if operation == "evaluation":
            return {"status": "completed", **learning.evaluation_report()}
        if operation == "sources":
            return {
                "status": "completed",
                "sources": learning.search_sources(
                    str(arguments.get("query") or event.text), source_ids=arguments.get("source_ids", ())
                ),
            }
        if operation in {"import_notes", "import_source"}:
            import time
            from pathlib import Path
            from runtime.tutor.sources import text_spans, import_document

            path = learning.active_path()
            if path is None or not path.domain_id:
                raise ValueError("active_learning_domain_required")
            source_id = "source:" + digest([event.session_id, event.event_id])[:32]
            if operation == "import_notes":
                spans = text_spans(
                    domain_id=path.domain_id,
                    source_id=source_id,
                    text=event.text,
                    source_kind="user_note",
                    now=time.time(),
                )
            else:
                named = str(arguments.get("path") or "")
                if (
                    len(named) < 4
                    or named.casefold() not in str(event.text).casefold()
                    or not Path(named).is_absolute()
                ):
                    raise ValueError("source_path_must_be_explicitly_named_by_user")
                import os

                source_id = "source:" + digest([path.domain_id, os.path.normcase(str(Path(named).resolve()))])[:32]
                spans = import_document(Path(named), domain_id=path.domain_id, source_id=source_id, now=time.time())
            learning.import_source_spans(spans)
            return {
                "status": "completed",
                "source_id": source_id,
                "span_count": len(spans),
                "source_refs": [span.span_id for span in spans[:32]],
            }
        if operation == "retention":
            return {"status": "completed", **learning.retention_snapshot(str(arguments.get("kp_id") or ""))}
        if operation == "dispute_attempt":
            changed = learning.correct_attempt(
                str(arguments.get("attempt_id") or ""), correction_id="correction:" + digest(event.event_id)[:32]
            )
            return {"status": "completed", "changed": changed, "result": "ungraded"}
        if operation == "forget_attempt":
            return {"status": "completed", "forgotten": learning.forget_attempt(str(arguments.get("attempt_id") or ""))}
        if operation == "help":
            move = getattr(getattr(self.executive, "_state", None), "last_move", None)
            if move is None or not self._waiting_for_response:
                raise ValueError("no_delivered_question_waiting_for_help")
            assistance = "answer_revealed" if arguments.get("reveal") is True else "hinted"
            state = learning._store.assistance_for(move.move_id)
            self._pending_activity_receipts.append(
                {
                    **pending,
                    "kind": "chat_help",
                    "interaction_id": move.move_id,
                    "event_id": "help:" + digest(event.event_id)[:32],
                    "assistance": assistance,
                    "hint_count": state.get("hint_count", 0) + 1,
                }
            )
            return {
                "status": "completed",
                "help_for": move.move_id,
                "instruction": "Giải thích lời giải khi người dùng yêu cầu."
                if assistance == "answer_revealed"
                else "Gợi ý thêm đúng một bậc; giữ một bước cho người học tự thử.",
                "recorded_after_delivery": True,
            }
        raise ValueError("unsupported_tutor_operation")

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


__all__ = ["TutorAdvice", "TutorCapability"]

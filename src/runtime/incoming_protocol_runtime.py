# -*- coding: utf-8 -*-
"""Inbound protocol dispatch and non-chat control messages.

Extracted from RuntimeCoordinator so the composition root stops owning every policy.
"""
from __future__ import annotations

import os
import time

from runtime.stage_protocol import is_stage_inbound_type


class IncomingProtocolRuntimeMixin:
    """Coordinator-facing mixin with no independent lifecycle."""

    def _ensure_transport_boundary(self):
        boundary = getattr(self, "transport_boundary", None)
        if boundary is None:
            from runtime.transport_boundary import RuntimeTransportBoundary

            boundary = RuntimeTransportBoundary(
                self.transport,
                health_registry=getattr(self, "subsystem_health_registry", None),
                logger=getattr(self, "logger", None),
            )
            self.transport_boundary = boundary
        return boundary

    def _send_runtime_json(
        self,
        payload: dict,
        *,
        channel: str = "control",
        required: bool = False,
    ) -> bool:
        return bool(
            self._ensure_transport_boundary().send_json(
                payload, channel=channel, required=required
            )
        )

    def _ensure_incoming_dispatcher(self):
        dispatcher = getattr(self, "incoming_dispatcher", None)
        if dispatcher is None:
            from runtime.incoming_dispatcher import IncomingMessageDispatcher

            dispatcher = IncomingMessageDispatcher(
                self,
                settings=getattr(getattr(self, "runtime_policy_settings", None), "protocol", None),
            )
            self.incoming_dispatcher = dispatcher
        return dispatcher

    def _handle_incoming(self, msg: dict):
        return self._ensure_incoming_dispatcher().dispatch(msg)


    def _handle_panel_state_ack(self, msg: dict) -> None:
        """Nhận receipt từ Body cho lane panel.

        UDP không đảm bảo giao hàng. Trước Phase 7, Brain chỉ biết đã ``sendto``
        chứ không biết C# có parse được hay không, nên panel trống vẫn có thể bị
        ghi thành “gửi thành công”. Receipt này biến visibility thành evidence.
        """

        packet_id = str(msg.get("packet_id", "") or "").strip()
        complete = bool(msg.get("complete", False))
        has_private_mind = bool(msg.get("has_private_mind", False))
        has_emotion = bool(msg.get("has_emotion", False))
        has_depth = bool(msg.get("has_depth", False))
        try:
            sent_at = float(msg.get("sent_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            sent_at = 0.0
        latency_ms = max(0.0, (time.time() - sent_at) * 1000.0) if sent_at > 0 else 0.0
        obs = getattr(self, "observability", None)
        if obs is not None:
            obs.increment(
                "panel_state_ack_total",
                tags={"complete": str(complete).lower()},
                fields={
                    "packet_id": packet_id,
                    "has_private_mind": has_private_mind,
                    "has_emotion": has_emotion,
                    "has_depth": has_depth,
                },
            )
            if complete:
                obs.increment("panel_state_ack_complete_total", fields={"packet_id": packet_id})
            if latency_ms > 0:
                obs.observe("panel_state_ack_latency_ms", latency_ms, fields={"packet_id": packet_id})
        phase7 = getattr(self, "phase7_validation", None)
        if phase7 is not None:
            try:
                phase7.mark_scenario(
                    "panel_delivery",
                    passed=complete,
                    detail=(
                        f"packet={packet_id}; private_mind={has_private_mind}; "
                        f"emotion={has_emotion}; depth={has_depth}; latency_ms={latency_ms:.1f}"
                    ),
                )
            except Exception as exc:
                logger = getattr(self, "logger", None)
                debug = getattr(logger, "debug", None)
                if callable(debug):
                    debug("[phase7] panel delivery mark failed: %s", exc)


    def _handle_agent_control(self, msg: dict) -> None:
        """Control plane cho Agent Console (Body -> Brain).

        Panel chỉ gửi intent có cấu trúc. Task store giữ intent đó cho AgentHarness
        consume ở boundary giữa các node; UI không được tự sửa graph state.
        """

        from runtime.agent_harness.task_store import AgentTaskStore

        action = str(msg.get("action", "snapshot") or "snapshot").strip().lower()
        task_id = str(msg.get("task_id", "") or "").strip()
        request_id = str(msg.get("request_id", "") or "").strip()
        selected_task_id = task_id or str(msg.get("selected_task_id", "") or "").strip()
        status = "ok"
        message = "Agent Console đã đồng bộ."
        try:
            store = getattr(self, "agent_task_store", None) or AgentTaskStore()
            if action == "snapshot":
                pass
            elif action in {"workspace_open", "workspace_switch", "workspace_favorite", "workspace_unfavorite"}:
                manager = getattr(self, "workspace_manager", None)
                if manager is None:
                    raise RuntimeError("workspace_manager_unavailable")
                if action == "workspace_open":
                    source = str(msg.get("workspace_ref", "") or msg.get("text", "") or "").strip()
                    if not source:
                        raise ValueError("workspace_path_required")
                    record = manager.join(source, activate=True)
                    message = f"Đã mở workspace {record.display_name}."
                elif action == "workspace_switch":
                    ref = str(msg.get("workspace_ref", "") or "").strip()
                    if not ref:
                        raise ValueError("workspace_ref_required")
                    record = manager.switch(ref)
                    message = f"Đã chuyển sang workspace {record.display_name}."
                else:
                    ref = str(msg.get("workspace_ref", "") or "").strip()
                    if not ref:
                        raise ValueError("workspace_ref_required")
                    favorite = action == "workspace_favorite"
                    record = manager.set_favorite(ref, favorite=favorite)
                    message = (
                        f"Đã lưu {record.display_name} vào workspace thường dùng."
                        if favorite else f"Đã bỏ ghim {record.display_name}."
                    )
            elif action == "create_task":
                text = str(msg.get("text", "") or "").strip()
                if not text:
                    raise ValueError("task_goal_required")
                starter = getattr(self, "start_agent_task_async", None)
                if not callable(starter):
                    raise RuntimeError("agent_task_starter_unavailable")
                task_mode = "execute"
                model = str(msg.get("model", "") or "").strip()
                started = starter(
                    text,
                    clarified_goal=text,
                    source_turn_id=f"agent_console:{request_id or int(time.time() * 1000)}",
                    source="agent_console",
                    task_mode=task_mode,
                    workspace_ref=str(msg.get("workspace_ref", "") or "").strip(),
                    model=model,
                )
                if str(started.get("status", "") or "") != "started":
                    raise RuntimeError(str(started.get("reason", "agent_task_start_failed") or "agent_task_start_failed"))
                selected_task_id = str(started.get("task_id", "") or "")
                message = "Đã giao task mới cho Gemini."
            elif action in {"pause", "resume", "stop", "rollback", "directive", "choice"}:
                if not task_id:
                    raise ValueError("task_id_required")
                task = store.get_task(task_id, event_limit=0)
                if task is None:
                    raise KeyError(f"unknown_agent_task:{task_id}")

                text = str(msg.get("text", "") or "").strip()
                choice_id = str(msg.get("choice_id", "") or "").strip()
                choice_event_id = str(msg.get("choice_event_id", "") or "").strip()
                checkpoint_id = str(msg.get("checkpoint_id", "") or "").strip()
                task_status = str(task.get("status", "") or "").strip().lower()
                terminal = task_status in {"completed", "failed", "stopped"}
                if action in {"directive", "choice"} and not text:
                    raise ValueError("choice_text_required" if action == "choice" else "directive_text_required")

                # LangGraph interrupt thật đã nhả worker. Với task đang chờ user,
                # phải resume checkpoint trực tiếp; enqueue như kiến trúc UDP cũ sẽ
                # tạo một control không còn worker nào thức để consume.
                direct_resume = False
                controller = getattr(self, "control_agent_task", None)
                resumer = getattr(self, "resume_agent_task_async", None)
                if callable(controller) and task_status in {"waiting_user", "waiting_permission", "paused"} and action in {"stop", "rollback"}:
                    result = controller(
                        task_id,
                        action=action,
                        text=text,
                        checkpoint_id=checkpoint_id,
                        source="agent_console",
                    )
                    result_status = str(result.get("status", "") or "")
                    if result_status in {"failed", "rejected", "unavailable", "not_found"}:
                        raise RuntimeError(str(result.get("reason", "agent_control_failed") or "agent_control_failed"))
                    control = None
                    direct_resume = True
                elif callable(resumer) and task_status in {"waiting_user", "waiting_permission"} and action in {"choice", "directive", "resume"}:
                    response = {
                        "choiceId": choice_id or ("other" if action == "directive" else "resume"),
                        "text": text,
                        "value": text,
                        "source": "agent_console",
                    }
                    resumed = resumer(task_id, response=response, source="agent_console")
                    if str(resumed.get("status", "") or "") not in {"resuming", "started"}:
                        raise RuntimeError(str(resumed.get("reason", "agent_resume_failed") or "agent_resume_failed"))
                    direct_resume = True
                    control = None
                elif callable(resumer) and task_status == "paused" and action in {"resume", "directive"}:
                    resumed = resumer(task_id, response=None, source="agent_console", directive=text)
                    if str(resumed.get("status", "") or "") not in {"resuming", "started"}:
                        raise RuntimeError(str(resumed.get("reason", "agent_resume_failed") or "agent_resume_failed"))
                    direct_resume = True
                    control = None
                # A terminal task has no worker left to consume queued controls.
                # Rollback it synchronously through AgentHarness instead of adding
                # a request that would sit in TaskStore forever looking important.
                elif action == "rollback" and terminal:
                    harness = getattr(self, "agent_harness", None)
                    restore = getattr(harness, "restore_task_checkpoint", None)
                    if not callable(restore):
                        raise RuntimeError("agent_terminal_rollback_unavailable")
                    record = restore(task_id, checkpoint_id or "latest")
                    message = f"Đã rollback task về checkpoint {record.graph_checkpoint_id}."
                    control = None
                else:
                    control = store.enqueue_control(
                        task_id,
                        action,
                        text=text,
                        checkpoint_id=checkpoint_id,
                        request_id=request_id,
                        data={
                            "choice_id": choice_id,
                            "choice_event_id": choice_event_id,
                        } if action == "choice" else None,
                    )
                    if control is None:
                        raise RuntimeError("agent_control_enqueue_failed")

                control_id = str((control or {}).get("control_id", "") or "")
                if action == "directive":
                    store.append_event(
                        task_id,
                        "user_directive",
                        actor="user",
                        summary=text,
                        importance=0.92,
                        data={"control_id": control_id, "resume_mode": "langgraph" if direct_resume else "queued"},
                    )
                    message = ("Đã resume LangGraph với chỉ thị mới." if direct_resume else "Đã gửi chỉ thị vào task; executor sẽ nhận ở boundary kế tiếp.")
                elif action == "choice":
                    store.append_event(
                        task_id,
                        "user_choice",
                        actor="user",
                        summary=text,
                        importance=0.96,
                        data={
                            "control_id": control_id,
                            "choice_id": choice_id,
                            "resume_mode": "langgraph" if direct_resume else "queued",
                            "choice_event_id": choice_event_id,
                        },
                    )
                    message = "Đã gửi lựa chọn; LangGraph tiếp tục từ đúng checkpoint đang chờ."
                elif action == "rollback":
                    if not terminal:
                        store.append_event(
                            task_id,
                            "rollback_requested",
                            actor="user",
                            summary=(
                                f"Yêu cầu quay lại checkpoint {checkpoint_id}."
                                if checkpoint_id else "Yêu cầu quay lại checkpoint gần nhất."
                            ),
                            importance=0.96,
                            data={"checkpoint_id": checkpoint_id, "control_id": control_id},
                        )
                        message = "Đã xếp yêu cầu rollback; AgentHarness sẽ xử lý ở điểm an toàn kế tiếp."
                else:
                    store.append_event(
                        task_id,
                        f"{action}_requested",
                        actor="user",
                        summary=f"Người dùng yêu cầu {action} task.",
                        importance=0.86,
                        data={"control_id": control_id, "resume_mode": "langgraph" if direct_resume else "queued"},
                    )
                    message = f"Đã gửi yêu cầu {action}."
            else:
                raise ValueError(f"unsupported_agent_action:{action}")

            snapshot = store.snapshot(
                selected_task_id=selected_task_id,
                task_limit=12,
                event_limit=48,
            )
            manager = getattr(self, "workspace_manager", None)
            if manager is not None:
                snapshot["workspace_state"] = manager.workspace_snapshot(limit=16)
        except Exception as exc:
            status = "error"
            message = str(exc)[:320]
            try:
                snapshot = AgentTaskStore().snapshot(
                    selected_task_id=selected_task_id,
                    task_limit=12,
                    event_limit=32,
                )
            except Exception:
                snapshot = {"version": "agent_console.v1", "selected_task_id": selected_task_id, "tasks": []}

        self._send_runtime_json({
            "type": "agent_console_ack",
            "parse_mode": "agent_console_ack",
            "should_speak": False,
            "request_id": request_id,
            "action": action,
            "status": status,
            "message": message,
            "agent_console": snapshot,
        }, channel="agent_console")


    def _handle_learning_canvas_request(self, msg: dict) -> None:
        """UI-only lane: no user turn, generation bump, kernel call or audio cancel."""
        canvas = getattr(self, "learning_canvas_service", None)
        try:
            if canvas is None:
                result = {"type": "learning_canvas_ack", "parse_mode": "learning_canvas_ack",
                          "should_speak": False, "request_id": str(msg.get("request_id", ""))[:120],
                          "status": "unavailable", "message": "Learning Canvas chưa sẵn sàng."}
            else:
                result = canvas.handle(msg)
        except Exception as exc:
            result = {"type": "learning_canvas_ack", "parse_mode": "learning_canvas_ack",
                      "should_speak": False, "request_id": str(msg.get("request_id", ""))[:120],
                      "status": "error", "message": f"Không đọc được Canvas: {type(exc).__name__}"}
        self._send_runtime_json(result, channel="learning_canvas")

    def _handle_memory_panel_request(self, msg: dict) -> None:
        """Đọc/sửa Living User Memory qua panel Godot.

        Handler luôn gửi snapshot mới sau thao tác để UI không phải tự đoán state.
        Payload nhạy cảm bị loại tại runtime, không chỉ ẩn bằng CSS phía client.
        """
        from runtime.living_user_panel import build_memory_expression_lens, handle_memory_panel_request

        request_id = str(msg.get("request_id", "") or "")
        kernel = getattr(self, "memory_kernel", None)
        living_user = getattr(kernel, "living_user", None)
        try:
            internal_state = self._get_internal_state() if callable(getattr(self, "_get_internal_state", None)) else None
        except Exception:
            internal_state = None
        personality_profile = None
        try:
            reducer = getattr(self, "ai_state_reducer", None)
            snapshot = getattr(reducer, "snapshot", None)
            mind_state = snapshot() if callable(snapshot) else None
            personality_profile = getattr(mind_state, "personality", None)
        except Exception:
            personality_profile = None
        result = handle_memory_panel_request(
            living_user,
            msg,
            cleanup_service=getattr(self, "memory_gc_service", None),
            portrait_presenter=getattr(self, "memory_portrait_presenter", None),
            expression_lens=build_memory_expression_lens(internal_state, personality_profile),
        )
        self._send_runtime_json({
            "type": "memory_panel_ack",
            "parse_mode": "memory_panel_ack",
            "should_speak": False,
            "request_id": request_id,
            "action": str(msg.get("action", "snapshot") or "snapshot"),
            "status": result.status,
            "message": result.message,
            "memory_panel": result.payload,
        }, channel="memory_panel")


    def _handle_capability_panel_request(self, msg: dict) -> None:
        """V87 - snapshot + user control cho Capability Panel Desktop.

        Đây là kênh trusted giữa UI Godot và runtime cùng máy. Eris Link HTTP vẫn
        read-only: không có POST reset skill/follow goal từ process ngoài.
        """
        request_id = str(msg.get("request_id", "") or "")
        action = str(msg.get("action", "snapshot") or "snapshot").strip().lower()
        if action == "portrait_snapshot":
            try:
                service = getattr(self, "learner_portrait_service", None)
                if service is None:
                    from runtime.learner_portrait import LearnerPortraitService
                    service = LearnerPortraitService()
                    self.learner_portrait_service = service
                mentor_service = getattr(self, "engineering_mentor_service", None)
                mentor_projection = getattr(self, "capability_projection_service", None)
                mentor = (mentor_projection.build(mentor_service.snapshot())
                          if mentor_service is not None and mentor_projection is not None else None)
                payload = service.build(
                    learning=getattr(self, "learning_service", None), mentor=mentor,
                    path_id=str(msg.get("path_id") or "")[:120],
                    path_page=max(0, min(20, int(msg.get("path_page") or 0))),
                )
                status, message = "ok", "Đã đọc dữ liệu hồ sơ."
            except Exception as exc:
                payload, status, message = {}, "error", "Không đọc được Learner Portrait: " + type(exc).__name__
            self._send_runtime_json({
                "type": "capability_panel_ack", "parse_mode": "capability_panel_ack",
                "should_speak": False, "request_id": request_id, "action": action,
                "status": status, "message": message, "learner_portrait": payload,
            }, channel="capability_panel")
            return
        mentor = getattr(self, "engineering_mentor_service", None)
        projection = getattr(self, "capability_projection_service", None)
        controls = getattr(self, "development_control_service", None)
        if mentor is None or projection is None:
            self._send_runtime_json({
                "type": "capability_panel_ack",
                "parse_mode": "capability_panel_ack",
                "should_speak": False,
                "request_id": request_id,
                "status": "unavailable",
                "message": "Mentor chưa sẵn sàng.",
                "capability_panel": {},
            }, channel="capability_panel")
            return

        status, message = "ok", "Đã cập nhật thống kê năng lực."
        try:
            # Build một snapshot trước action để goal lấy baseline từ đúng số user
            # đang nhìn thấy, không đọc private state bằng đường tắt.
            before = projection.build(mentor.snapshot())
            if controls is not None:
                before = controls.decorate(before)

            if action == "skill_feedback":
                skill_id = str(msg.get("skill_id", "") or "").strip()
                feedback = str(msg.get("feedback", "") or "").strip().lower()
                ok = bool(getattr(mentor, "apply_panel_skill_feedback", lambda *_a, **_k: False)(skill_id, feedback))
                if ok and controls is not None:
                    controls.record_skill_feedback(skill_id, feedback)
                if not ok:
                    status, message = "error", "Không áp dụng được phản hồi kỹ năng."
                else:
                    labels = {
                        "confirmed": "Đã ghi nhận: bạn thấy đánh giá này hợp lý.",
                        "already_knew": "Đã ghi nhận: bạn đã biết phần này; ERIS không tự tăng điểm thành thạo.",
                        "wrong": "Đã ghi nhận: ERIS có thể đánh giá sai và đã hạ độ tin cậy.",
                        "reset": "Đã đặt lại estimate của kỹ năng này.",
                    }
                    message = labels.get(feedback, "Đã ghi nhận phản hồi.")

            elif action == "follow_recommendation":
                rec_id = str(msg.get("recommendation_id", "") or "").strip()
                rec = next((x for x in before.get("de_xuat_dai_han", []) if isinstance(x, dict) and str(x.get("id") or "") == rec_id), None)
                if controls is None or rec is None or controls.follow_recommendation(rec, before) is None:
                    status, message = "error", "Không theo dõi được đề xuất này."
                else:
                    message = "Đã thêm vào mục tiêu đang theo dõi."

            elif action == "stop_goal":
                goal_id = str(msg.get("goal_id", "") or "").strip()
                if controls is None or not controls.stop_goal(goal_id):
                    status, message = "error", "Không dừng được mục tiêu này."
                else:
                    message = "Đã dừng theo dõi mục tiêu."

            elif action == "reset_profile":
                # Reset toàn bộ chỉ khi user bấm explicit trong Desktop; không mở qua HTTP.
                mentor.reset_mentor_profile()
                message = "Đã đặt lại mô hình kỹ năng kỹ thuật; các mục tiêu đang theo dõi vẫn được giữ."

            elif action != "snapshot":
                status, message = "error", "Thao tác Capability Panel không hợp lệ."

            payload = projection.build(mentor.snapshot())
            if controls is not None:
                payload = controls.decorate(payload)
        except Exception as exc:
            payload = {}
            status, message = "error", f"Không xử lý được Capability Panel: {type(exc).__name__}"

        self._send_runtime_json({
            "type": "capability_panel_ack",
            "parse_mode": "capability_panel_ack",
            "should_speak": False,
            "request_id": request_id,
            "action": action,
            "status": status,
            "message": message,
            "capability_panel": payload,
        }, channel="capability_panel")


    def _handle_phase7_validation_control(self, msg: dict) -> None:
        """Control protocol nhỏ để chạy Phase 7 trên máy thật qua UDP 4243."""

        action = str(msg.get("action", "status") or "status").strip().lower()
        manager = getattr(self, "phase7_validation", None)
        result: dict[str, object]
        try:
            manager_disabled = not bool(
                getattr(getattr(manager, "config", None), "production_validation_enabled", False)
            )
            if action == "start" and self.observability is not None and (manager is None or manager_disabled):
                from dataclasses import replace
                from runtime.hardening_config import HardeningConfig
                from runtime.phase7_validation import Phase7ValidationManager

                base_cfg = getattr(self, "hardening_config", None) or HardeningConfig.load()
                cfg = replace(base_cfg, production_validation_enabled=True)
                manager = Phase7ValidationManager(
                    observability=self.observability,
                    provider_health_fn=self.provider_health_snapshot,
                    rollout_status_fn=(
                        self.rollout_controller.status
                        if self.rollout_controller is not None and callable(getattr(self.rollout_controller, "status", None))
                        else (lambda: {})
                    ),
                    config=cfg,
                    release_id=str(msg.get("release_id", "") or os.getenv("ERIS_RELEASE_ID", "") or ""),
                    environment_fn=self._phase7_environment_snapshot,
                )
                self.phase7_validation = manager
            if manager is None:
                raise RuntimeError("phase7_validation_unavailable")

            if action == "start":
                state = manager.start(validation_id=str(msg.get("validation_id", "") or ""))
                result = state.to_dict() if state is not None else manager.status()
            elif action == "mark":
                manager.mark_scenario(
                    str(msg.get("scenario", "") or ""),
                    passed=bool(msg.get("passed", False)),
                    turn_id=str(msg.get("turn_id", "") or ""),
                    detail=str(msg.get("detail", "") or ""),
                )
                result = manager.status()
            elif action == "finalize":
                report = manager.finalize(reason=str(msg.get("reason", "manual") or "manual"))
                result = report.to_dict() if report is not None else manager.status()
            else:
                result = manager.status()
            status = "ok"
        except Exception as exc:
            status = "error"
            result = {"error": type(exc).__name__, "message": str(exc)[:240]}
        self._send_runtime_json({
            "type": "phase7_validation_ack",
            "parse_mode": "phase7_validation_ack",
            "should_speak": False,
            "action": action,
            "status": status,
            "phase7_validation": result,
        }, channel="phase7")


    def _phase7_environment_snapshot(self) -> dict[str, object]:
        import platform
        import sys

        tts = getattr(self, "tts_engine", None)
        signature = ""
        if tts is not None and callable(getattr(tts, "cache_signature", None)):
            try:
                signature = str(tts.cache_signature() or "")
            except Exception:
                signature = ""
        try:
            from runtime.hardening_config import HardeningConfig
            hardening = HardeningConfig.load().to_dict()
        except Exception:
            hardening = {}
        return {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "runtime_session_id": str(getattr(self, "runtime_session_id", "") or ""),
            "tts_signature": signature,
            "provider_health": self.provider_health_snapshot(),
            "subsystem_health": (
                getattr(self, "subsystem_health_registry").snapshot(include_events=False)
                if callable(getattr(getattr(self, "subsystem_health_registry", None), "snapshot", None))
                else {}
            ),
            "hardening_config": hardening,
            "runtime_diagnostics": (
                self.runtime_diagnostics.snapshot(include_events=False)
                if callable(getattr(getattr(self, "runtime_diagnostics", None), "snapshot", None))
                else {}
            ),
        }


    def _handle_maintenance_catalog(self, msg: dict | None = None) -> None:
        """Trả danh sách thao tác Danger Zone và thống kê cache hiện tại."""

        service = getattr(self, "maintenance_service", None)
        try:
            payload = service.catalog() if service is not None else {"actions": [], "error": "maintenance_service_unavailable"}
            self._send_runtime_json({
                "type": "maintenance_catalog_ack",
                "parse_mode": "maintenance_catalog_ack",
                "should_speak": False,
                "maintenance_catalog_ack": payload,
            })
        except Exception as exc:
            self.logger.warning("[maintenance] catalog failed: %s", exc)


    def _handle_maintenance_request(self, msg: dict) -> None:
        """Preview hoặc thực thi command Danger Zone.

        Protocol hai bước ngăn một click lệch tay xóa state ngay lập tức:
        ``dry_run=true`` trả manifest + token; Body chỉ execute bằng token đó.
        """

        service = getattr(self, "maintenance_service", None)
        request_id = str(msg.get("request_id", "") or "")
        action = str(msg.get("action", "") or "").strip().lower()
        dry_run = str(msg.get("type", "") or "") != "maintenance_execute" and bool(msg.get("dry_run", True))
        options = msg.get("options") if isinstance(msg.get("options"), dict) else {}
        try:
            if service is None:
                raise RuntimeError("maintenance_service_unavailable")
            if dry_run:
                result = service.preview(action, options=options)
                envelope_type = "maintenance_preview_ack"
                field = "maintenance_preview_ack"
            else:
                token = str(msg.get("confirmation_token", "") or "")
                typed_confirmation = str(msg.get("typed_confirmation", "") or "")
                result = service.execute(
                    action, confirmation_token=token,
                    typed_confirmation=typed_confirmation, options=options,
                )
                envelope_type = "maintenance_execute_ack"
                field = "maintenance_execute_ack"
            self._send_runtime_json({
                "type": envelope_type,
                "parse_mode": envelope_type,
                "should_speak": False,
                field: {
                    "request_id": request_id,
                    "protocol_phase": "preview" if dry_run else "execute",
                    **result,
                },
            })
        except Exception as exc:
            self.logger.warning("[maintenance] request action=%s dry_run=%s failed: %s", action, dry_run, exc)
            self._send_runtime_json({
                "type": "maintenance_execute_ack",
                "parse_mode": "maintenance_execute_ack",
                "should_speak": False,
                "maintenance_execute_ack": {
                    "request_id": request_id,
                    "action": action,
                    "ok": False,
                    "changed": [],
                    "warnings": [],
                    "errors": [str(exc)[:320]],
                    "backup_dir": "",
                },
            }, channel="maintenance")


__all__ = ["IncomingProtocolRuntimeMixin"]

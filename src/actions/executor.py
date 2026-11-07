import asyncio
import ctypes
import json
import re
import shlex
import subprocess
import time
import threading
import uuid
import webbrowser
import os # Thêm import os để sử dụng os.startfile trên Windows
import requests # Thêm import requests để thực hiện các yêu cầu HTTP khi cần thiết

from eris_secrets import get_agent_profile, get_secret
from .contracts import ActionExecutionResult, VALID_SYSTEM_ACTIONS
from .capability_registry import canonicalize_action_id, encode_capability_arguments, validate_capability_arguments
from .observer import DefaultActionObserver, apply_observation
# ActionExecutor là một lớp để thực thi các hành động hệ thống được định nghĩa
# trong VALID_SYSTEM_ACTIONS.
# Lớp này cung cấp một phương thức execute để thực thi một hành động cụ thể dựa
#  trên tên hành động và đầu vào được cung cấp.
# Mỗi hành động có thể có một phương thức riêng để xử lý logic thực thi của nó,
# như lấy thông tin bài

class ActionExecutor:
    _VK_MEDIA_PLAY_PAUSE = 0xB3
    _VK_VOLUME_MUTE = 0xAD
    _VK_VOLUME_DOWN = 0xAE
    _VK_VOLUME_UP = 0xAF

    def __init__(self, logger=None, observer=None):
        self.logger = logger
        self.observer = observer if observer is not None else DefaultActionObserver()
        self.cognitive_runtime = None
        self.environment_runtime = None
        self.project_runtime = None
        self.sandbox_world = None
        self.sandbox_capability_provider = None
        self.subject_service = None
        self.subject_parent_decision_id = ""

    def bind_cognitive_runtime(self, runtime) -> None:
        """Nối executor với runtime nhận thức sau khi Coordinator đã dựng xong.

        ActionExecutor được tạo trước Coordinator trong vision.py, nên dependency
        này phải bind muộn. Không dùng global singleton, đỡ thêm một bóng ma nữa
        vào repo vốn đã đông dân.
        """
        self.cognitive_runtime = runtime

    def bind_environment_runtime(self, runtime) -> None:
        """Nối executor với runtime đọc môi trường sau khi screen store đã dựng."""
        self.environment_runtime = runtime

    def bind_project_runtime(self, runtime) -> None:
        """Nối executor với runtime thao tác project autonomous."""
        self.project_runtime = runtime

    def bind_subject_service(self, subject_service) -> None:
        if subject_service is None or not callable(getattr(subject_service, "deliberate", None)):
            raise TypeError("subject_service must expose deliberate")
        self.subject_service = subject_service

    def set_subject_parent_decision(self, decision) -> None:
        self.subject_parent_decision_id = str(getattr(decision, "decision_id", "") or "")

    def bind_sandbox_world(self, sandbox_world, capability_provider) -> None:
        """Bind the only execution authority used by V6.

        The executor no longer performs a policy preview and then executes on
        the side.  Its unchecked handlers are registered inside SandboxWorld,
        so the actual side effect happens after the typed policy decision.
        """
        if sandbox_world is None:
            raise ValueError("sandbox_world is required")
        if not callable(capability_provider):
            raise TypeError("capability_provider must be callable")
        self.sandbox_world = sandbox_world
        self.sandbox_capability_provider = capability_provider
        bind_handler = getattr(sandbox_world, "bind_system_action_handler", None)
        if not callable(bind_handler):
            raise TypeError("sandbox_world must support system action handlers")
        bind_handler(self._execute_unchecked)

    @staticmethod
    def _run_async(coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        result = []
        error = []

        def worker():
            try:
                result.append(asyncio.run(coro))
            except BaseException as exc:
                error.append(exc)

        thread = threading.Thread(target=worker, name="eris-action-sandbox", daemon=True)
        thread.start()
        thread.join()
        if error:
            raise error[0]
        return result[0]

    #execute() là trung tâm điều phối
    '''
    AI output
   ↓
    execute()
    ↓
    validate action
    ↓
    tìm handler (_set_volume, _open_url...)
    ↓
    try execute
    ↓
    return ActionExecutionResult
    '''
    def execute_capability(self, capability_id: str, arguments) -> ActionExecutionResult:
        """Validate typed arguments, then adapt once at the exact handler boundary."""
        capability_key = canonicalize_action_id(capability_id)
        valid, reason = validate_capability_arguments(capability_key, arguments)
        if not valid:
            return ActionExecutionResult(
                action_name=capability_key,
                action_input="",
                success=False,
                message="Đối số capability không hợp lệ.",
                error_kind=f"CapabilityArgumentsInvalid:{reason}",
            )
        try:
            action_input = encode_capability_arguments(capability_key, arguments)
        except Exception as exc:
            return ActionExecutionResult(
                action_name=capability_key,
                action_input="",
                success=False,
                message="Không chuyển được đối số capability sang handler.",
                error_kind=type(exc).__name__,
            )
        return self.execute(capability_key, action_input)

    def execute(self, action_name: str, action_input: str = "") -> ActionExecutionResult:
        original_action_name = (action_name or "").strip()
        action_name = canonicalize_action_id(original_action_name)
        action_input = (action_input or "").strip()
        if action_name not in VALID_SYSTEM_ACTIONS:
            return ActionExecutionResult(
                action_name=action_name,
                action_input=action_input,
                success=False,
                message="Action không hợp lệ hoặc chưa được cấp quyền.",
            )
        if self.sandbox_world is None or self.sandbox_capability_provider is None:
            return ActionExecutionResult(
                action_name=action_name,
                action_input=action_input,
                success=False,
                message="Subject Core chưa khởi tạo; V6 không có đường thực thi dự phòng.",
                error_kind="SubjectCoreUnavailable",
            )

        from cortex.subject.contracts import WorldObservation
        from runtime.world.contracts import SandboxCommand, SandboxCommandKind

        try:
            if self.subject_service is None:
                raise RuntimeError("SubjectServiceUnavailable")
            observation_id = f"capability-observation:{uuid.uuid4()}"
            causal_refs = (self.subject_parent_decision_id,) if self.subject_parent_decision_id else ()
            observation = WorldObservation(
                observation_id=observation_id,
                subject_id=self.subject_service.config.subject_id,
                kind="capability_request",
                created_at=time.time(),
                context={
                    "context_key": "runtime_capability",
                    "affected_need_ids": ("competence", "coherence", "sovereignty"),
                    "goal_relevance": 0.95,
                    "controllability": 0.85,
                    "uncertainty": 0.25,
                    "primary_interpretation": f"runtime capability requested: {action_name}",
                    "action_proposals": ({
                        "action_kind": action_name,
                        "arguments": {"action_name": action_name, "action_input": action_input},
                        "supporting_need_ids": ("competence", "coherence"),
                        "expected_need_relief": 0.70,
                        "expected_action_efficacy": 0.80,
                        "expected_epistemic_value": 0.35,
                        "value_congruence": 0.70,
                        "substrate_cost": 0.12,
                        "uncertainty": 0.25,
                        "historical_success": 0.65,
                        "evidence_refs": (observation_id,),
                    },),
                },
                evidence_refs=(observation_id,),
                causal_refs=causal_refs,
                correlation_id=self.subject_parent_decision_id or observation_id,
            )
            decision = self._run_async(self.subject_service.deliberate(observation))
            request = self.subject_service.to_action_request(
                decision,
                requested_capabilities=(action_name,),
                expected_outcomes=("runtime_observation",),
                idempotency_key=f"system-action:{uuid.uuid4()}",
            )
            command_id = f"system-command:{uuid.uuid4()}"
            token = self.sandbox_capability_provider(request, command_id)
            command = SandboxCommand(
                command_id=command_id,
                action_id=request.action_id,
                decision_id=request.decision_id,
                kind=SandboxCommandKind.SYSTEM_ACTION,
                arguments={"action_name": action_name, "action_input": action_input},
                requested_capability=action_name,
                idempotency_key=request.idempotency_key,
                created_at=request.created_at,
                subject_id=str(getattr(token, "subject_id", "") or self.subject_service.config.subject_id),
                thread_id=request.thread_id,
                delivery_id=request.delivery_id,
            )
            outcome = self._run_async(self.sandbox_world.apply(command, token))
            self._run_async(self.subject_service.observe_outcome(outcome))
            actual = self.sandbox_world.take_execution_result(command_id)
            if actual is not None:
                return actual
            status = str(getattr(getattr(outcome, "status", ""), "value", getattr(outcome, "status", "")) or "")
            return ActionExecutionResult(
                action_name=action_name,
                action_input=action_input,
                success=status == "succeeded",
                message=(
                    "Action đã được Sandbox thực thi."
                    if status == "succeeded"
                    else "Sandbox từ chối hoặc không hoàn tất action."
                ),
                error_kind="" if status == "succeeded" else f"SandboxOutcome:{status}:{getattr(outcome, 'denial_reason', '') or ''}",
            )
        except Exception as exc:
            self._log("warning", "Sandbox action %s thất bại: %s", action_name, exc)
            return ActionExecutionResult(
                action_name=action_name,
                action_input=action_input,
                success=False,
                message=f"Sandbox không thực thi được action {action_name}.",
                error_kind=type(exc).__name__,
            )

    def _execute_unchecked(self, action_name: str, action_input: str = "") -> ActionExecutionResult:
        original_action_name = (action_name or "").strip()
        action_name = canonicalize_action_id(original_action_name)
        action_input = (action_input or "").strip()
        if action_name not in VALID_SYSTEM_ACTIONS:
            return ActionExecutionResult(
                action_name=action_name,
                action_input=action_input,
                success=False,
                message="Action không hợp lệ hoặc chưa được cấp quyền.",
            )

        #Dynamic dispatch.
        handler = getattr(self, f"_{action_name}", None)
        if handler is None:
            return ActionExecutionResult(
                action_name=action_name,
                action_input=action_input,
                success=False,
                message="Action chưa có implementation.",
            )

        try:
            result = handler(action_input)
        except Exception as exc:
            self._log("warning", "Action %s thất bại: %s", action_name, exc)
            return ActionExecutionResult(
                action_name=action_name,
                action_input=action_input,
                success=False,
                message=f"Không thực thi được action {action_name}.",
                error_kind=type(exc).__name__,
            )

        # Executor mới chỉ có dispatch evidence. Observer đọc hậu điều kiện và
        # chỉ nâng lên observed khi thật sự nhìn thấy trạng thái đích.
        if result.success and result.dispatched and not result.observed and self.observer is not None:
            try:
                observation = self.observer.observe(result)
                result = apply_observation(result, observation)
            except Exception as exc:
                result.observer_reason = f"observer_error:{type(exc).__name__}"
        try:
            from runtime.hardening_metrics import record_action_confirmation
            record_action_confirmation(result)
        except Exception:
            pass
        self._log("info", "[action] %s(%s) -> %s | status=%s | observer=%s",
                  action_name, action_input or "<empty>", result.message,
                  getattr(result.status, "value", result.status), result.observer_reason or "none")
        return result


    def _execute_cognitive(self, capability_id: str, action_input: str) -> ActionExecutionResult:
        runtime = self.cognitive_runtime
        if runtime is None:
            return ActionExecutionResult(
                action_name=capability_id, action_input=action_input, success=False,
                message="Runtime nhận thức nội bộ chưa được nối.", error_kind="CognitiveRuntimeUnavailable",
            )
        result = runtime.execute(capability_id, action_input)
        return result.to_action_result(action_input=action_input)

    def _review_discourse_claim(self, action_input: str) -> ActionExecutionResult:
        return self._execute_cognitive("review_discourse_claim", action_input)

    def _inspect_unresolved_questions(self, action_input: str) -> ActionExecutionResult:
        return self._execute_cognitive("inspect_unresolved_questions", action_input)

    def _inspect_memory_candidates(self, action_input: str) -> ActionExecutionResult:
        return self._execute_cognitive("inspect_memory_candidates", action_input)

    def _compare_recent_turns(self, action_input: str) -> ActionExecutionResult:
        return self._execute_cognitive("compare_recent_turns", action_input)

    def _inspect_social_state(self, action_input: str) -> ActionExecutionResult:
        return self._execute_cognitive("inspect_social_state", action_input)

    def _detect_internal_contradictions(self, action_input: str) -> ActionExecutionResult:
        return self._execute_cognitive("detect_internal_contradictions", action_input)

    def _summarize_open_loops(self, action_input: str) -> ActionExecutionResult:
        return self._execute_cognitive("summarize_open_loops", action_input)

    def _inspect_capability_health(self, action_input: str) -> ActionExecutionResult:
        return self._execute_cognitive("inspect_capability_health", action_input)

    def _execute_environment(self, capability_id: str, action_input: str) -> ActionExecutionResult:
        runtime = self.environment_runtime
        if runtime is None:
            return ActionExecutionResult(
                action_name=capability_id, action_input=action_input, success=False,
                message="Runtime đọc môi trường chưa được nối.", error_kind="EnvironmentRuntimeUnavailable",
            )
        result = runtime.execute(capability_id, action_input)
        return result.to_action_result(action_input=action_input)

    def _get_active_application(self, action_input: str) -> ActionExecutionResult:
        return self._execute_environment("get_active_application", action_input)

    def _read_visible_ui_text(self, action_input: str) -> ActionExecutionResult:
        return self._execute_environment("read_visible_ui_text", action_input)

    def _inspect_runtime_log(self, action_input: str) -> ActionExecutionResult:
        return self._execute_environment("inspect_runtime_log", action_input)

    def _read_current_error(self, action_input: str) -> ActionExecutionResult:
        return self._execute_environment("read_current_error", action_input)

    def _inspect_selected_file(self, action_input: str) -> ActionExecutionResult:
        return self._execute_environment("inspect_selected_file", action_input)

    def _get_project_status(self, action_input: str) -> ActionExecutionResult:
        return self._execute_environment("get_project_status", action_input)

    def _execute_project(self, capability_id: str, action_input: str) -> ActionExecutionResult:
        runtime = self.project_runtime
        if runtime is None:
            return ActionExecutionResult(
                action_name=capability_id, action_input=action_input, success=False,
                message="Runtime thao tác project chưa được nối.", error_kind="ProjectRuntimeUnavailable",
            )
        result = runtime.execute(capability_id, action_input)
        return result.to_action_result(action_input=action_input)

    def _python_workspace(self, action_input: str) -> ActionExecutionResult:
        """Chạy code Python trong Docker; không bao giờ fallback sang host Python."""
        from runtime.workspace.python_docker import DockerPythonWorkspace
        from runtime.workspace import send_artifacts_udp
        from pathlib import Path
        request = json.loads(action_input)
        runner = DockerPythonWorkspace(Path(os.getenv('ERIS_ARTIFACT_WORKSPACE', 'logs/eris_artifacts')),
                                       image=os.getenv('ERIS_ARTIFACT_DOCKER_IMAGE', 'python:3.12-slim'))
        payload = runner.execute(str(request.get('code') or ''))
        shown = False
        try:
            shown = send_artifacts_udp(payload)
        except OSError:
            pass
        return ActionExecutionResult(
            action_name='python_workspace',action_input='[code Python đã gửi sang container]',
            success=True,observed=True,
            message='Đã thực thi mã Python trong Docker và lưu artifact.',
            data={'job_id':payload['job_id'], 'artifact_count':str(len(payload['artifacts'])),
                  'panel_rendered':shown},
            evidence=(f"artifact-job:{payload['job_id']}",),
        )

    def _live_workspace_edit(self, action_input: str) -> ActionExecutionResult:
        """Writer chủ động đọc hoặc sửa tài liệu đang có, không động transcript."""
        from runtime.workspace.live_document import writer_edit_live_document
        payload = json.loads(action_input)
        result = writer_edit_live_document(
            str(payload.get('document_id', '')),
            str(payload.get('operation', '')),
            block_id=str(payload.get('block_id', '')),
            block=payload.get('block'),
            base_revision=payload.get('base_revision'),
        )
        return ActionExecutionResult(
            action_name='live_workspace_edit', action_input=action_input,
            success=bool(result.get('ok')), observed=bool(result.get('ok')),
            message='Đã cập nhật tài liệu.' if result.get('ok') else
                    'Không cập nhật được tài liệu: ' + str(result.get('reason', 'unknown')),
            data={'revision': str(result.get('revision', '')),
                  'snapshot': json.dumps(result.get('state', {}), ensure_ascii=False)[:8000]},
        )

    def _parallel_work(self, action_input: str) -> ActionExecutionResult:
        """Giao/đọc/hủy work job, không đợi kết quả suy luận dài."""
        from pathlib import Path
        from runtime.workspace.parallel_work import plan_with_router
        from runtime.agent_harness.unified_sessions import UnifiedAgentSessions
        from runtime.workspace import send_artifacts_udp
        request = json.loads(action_input)
        op = str(request.get("operation", "") or "")
        service = getattr(self, "_parallel_work_service", None)
        if service is None:
            owner = self.cognitive_runtime
            router = getattr(owner, "ai_model_router", None)
            planner = (lambda ctx: plan_with_router(router, ctx)) if router is not None else None
            def publish(event):
                # Work chỉ đánh thức Root khi có kết quả/đề nghị nói mới;
                # callback này KHÔNG được tự phát giọng hay tạo lượt công khai.
                if event.get("kind") in {"tool_result", "complete", "wait_user", "speech_suggestion"}:
                    wake = getattr(self.cognitive_runtime, "_wake_autonomous_loop", None)
                    if callable(wake):
                        try:
                            wake("work_session_" + str(event["kind"]))
                        except Exception:
                            pass
                # Work Lane chỉ gửi trạng thái ra Workspace; TUYỆT ĐỐI không phát TTS.
                content = str(event.get("text", "") or "").strip()
                if content and event.get("kind") != "speech_suggestion":
                    from runtime.workspace import send_artifacts_udp
                    artifacts = event.get("artifacts")
                    if artifacts is None:
                        artifacts = [{"type": "section", "text": "Bước " + str(event["revision"])},
                                     {"type": "text", "text": content[:3000]}]
                    else:
                        # MathText render ảnh cho công thức, fallback giữ LaTeX để sao chép.
                        artifacts = [dict(item) for item in artifacts]
                        if any(item.get("type") == "formula" for item in artifacts):
                            try:
                                from runtime.workspace.structured_output import _render_math
                                root_dir = Path(os.getenv("ERIS_ARTIFACT_WORKSPACE", "logs/eris_artifacts"))
                                path_key = str(event["job_id"]).replace("-", "_") + "_" + str(event.get("document_revision", 0))
                                _render_math(artifacts, root_dir, path_key)
                            except (OSError, ImportError, ValueError):
                                pass
                    ui_revision = int(event["revision"]) * 1000 + int(event.get("document_revision", 0))
                    payload = {"parse_mode": "workspace_artifact", "workspace_version": 1,
                               "job_id": f"{event['job_id']}-{ui_revision}",
                               "work_id": event["job_id"], "work_revision": ui_revision,
                               "document_revision": event.get("document_revision", 0),
                               "title": "Eris đang làm việc · " + event["status"],
                               "artifacts": artifacts}
                    send_artifacts_udp(payload, timeout=.12, retries=1)
            root = Path(os.getenv("ERIS_WORK_LANE_DB", "logs/work_lane/jobs.sqlite"))
            service = UnifiedAgentSessions(root, planner=planner, tool=self.execute_capability,
                                           publish=publish,
                                           goal_ledger_path=os.getenv("ERIS_AUTONOMY_V3_DB", "logs/eris_goals_v3.sqlite"))
            self._parallel_work_service = service
        result = service.execute(request)
        # Đường chỉnh tài liệu từ Root/Writer cũng phát bản cập nhật đã commit.
        if op == "edit_document" and "error" not in result:
            work_store = getattr(service, "work", service)
            event = {"job_id": result["job_id"],
                     "revision": (service.snapshot(result["job_id"]) or {}).get("revision", 0),
                     "status": "working", "kind": "workspace",
                     "text": "Đã sửa tài liệu đang hiển thị.",
                     "document_revision": result["revision"],
                     "artifacts": work_store._artifacts(result["blocks"])}
            publisher = getattr(work_store, "publish", None)
            if callable(publisher):
                publisher(event)
        ok = "error" not in result
        return ActionExecutionResult(
            action_name="parallel_work", action_input=action_input,
            success=ok, observed=ok,
            message=("Đã tiếp nhận work job, có thể tiếp tục trò chuyện." if op == "start"
                     else "Đã đọc/cập nhật trạng thái công việc." if ok else "Không tìm thấy công việc."),
            data={"job": json.dumps(result, ensure_ascii=False)[:7500]},
            evidence=(("work-job:" + str(result.get("job_id"))) if result.get("job_id") else "",),
        )

    def _math_workspace(self, action_input: str) -> ActionExecutionResult:
        """Tính toán thật trong worker riêng, gửi artifact riêng cho Godot."""
        from pathlib import Path
        from runtime.workspace import MathWorkspaceService, send_artifacts_udp
        request = json.loads(action_input)
        service = getattr(self, '_math_workspace_service', None)
        if service is None:
            root = os.getenv('ERIS_ARTIFACT_WORKSPACE', 'logs/eris_artifacts')
            service = MathWorkspaceService(Path(root))
            self._math_workspace_service = service
        payload = service.execute_sync(request)
        # P0 sidecar: schema chuẩn, chưa publish sang Rich Chat (P1).
        # Failure ở adapter KHÔNG được biến thành thất bại của math tool/TTS.
        protocol_data = {}
        from runtime.workspace.artifact_protocol import (
            protocol_enabled, adapter_math_workspace, write_bundle_sidecar,
        )
        if protocol_enabled():
            try:
                bundle = adapter_math_workspace(payload)
                write_bundle_sidecar(bundle, service.workspace)
                protocol_data = {
                    'artifact_bundle_id': bundle['intent_id'],
                    'artifact_refs': json.dumps(
                        [a['artifact_id'] for a in bundle['artifacts']], ensure_ascii=False),
                    'artifact_turn_binding': 'job_provisional',
                }
            except (ValueError, OSError, TypeError) as exc:
                protocol_data = {'artifact_protocol_error': type(exc).__name__ + ': ' + str(exc)[:250]}
                if self.logger is not None:
                    self.logger.warning('[presentation-p0] math sidecar failed: %s', exc)
        shown = False
        try:
            shown = send_artifacts_udp(payload)
        except OSError:
            # Khi Godot chưa khởi chạy, các artifact vẫn được lưu trên đĩa.
            pass
        return ActionExecutionResult(
            action_name='math_workspace', action_input=action_input, success=True,
            observed=True,
            message=f"Đã tính toán và tạo {len(payload['artifacts'])} artifact: {payload['title']}",
            data={'job_id':payload['job_id'], 'title':payload['title'],
                  'artifact_count':str(len(payload['artifacts'])), 'panel_rendered':shown,
                  **protocol_data},
            evidence=(f"artifact-job:{payload['job_id']}",),
        )

    def _project_list_files(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_list_files", action_input)

    def _project_read_file(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_read_file", action_input)

    def _project_search_text(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_search_text", action_input)

    def _project_find_symbol(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_find_symbol", action_input)

    def _project_semantic_search(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_semantic_search", action_input)

    def _project_git_log(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_git_log", action_input)

    def _project_dependency_summary(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_dependency_summary", action_input)

    def _project_test_inventory(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_test_inventory", action_input)

    def _project_git_diff(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_git_diff", action_input)

    def _project_apply_patch(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_apply_patch", action_input)

    def _project_run_tests(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_run_tests", action_input)

    def _project_run_command(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_run_command", action_input)

    def _project_rollback(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_rollback", action_input)

    def _project_package_patch(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_package_patch", action_input)

    def _project_execute_task(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("project_execute_task", action_input)

    def _workspace_join(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("workspace_join", action_input)

    def _workspace_switch(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("workspace_switch", action_input)

    def _workspace_list(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("workspace_list", action_input)

    def _workspace_leave(self, action_input: str) -> ActionExecutionResult:
        return self._execute_project("workspace_leave", action_input)

    def _get_local_time(self, action_input: str) -> ActionExecutionResult:
        now = time.localtime()
        hhmm = time.strftime("%H:%M", now)
        date_text = time.strftime("%Y-%m-%d", now)
        return ActionExecutionResult(
            action_name="get_local_time",
            action_input=action_input,
            success=True,
            message=f"Bây giờ là {hhmm}, ngày {date_text} theo đồng hồ máy.",
            data={"time": hhmm, "date": date_text},
        )

    # Nó gọi PowerShell:
    # subprocess.run(["powershell", ...])
    # và dùng Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager để đọc media session Windows.
    def _get_now_playing(self, action_input: str) -> ActionExecutionResult:
        script = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType=WindowsRuntime]
$null = [System.WindowsRuntimeSystemExtensions, System.Runtime.WindowsRuntime]
function Await($op) {
  $task = [System.WindowsRuntimeSystemExtensions]::AsTask($op)
  $task.Wait()
  return $task.Result
}
$manager = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync())
$session = $manager.GetCurrentSession()
if ($null -eq $session) {
  [pscustomobject]@{ available = $false; reason = 'no_active_session' } | ConvertTo-Json -Compress
  exit
}
$props = Await ($session.TryGetMediaPropertiesAsync())
[pscustomobject]@{
  available = $true
  title = [string]$props.Title
  artist = [string]$props.Artist
  album = [string]$props.AlbumTitle
  source = [string]$session.SourceAppUserModelId
} | ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0 or not output:
            return ActionExecutionResult(
                action_name="get_now_playing",
                success=False,
                message="Không đọc được media session hiện tại.",
            )

        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return ActionExecutionResult(
                action_name="get_now_playing",
                success=False,
                message="Phản hồi media session không hợp lệ.",
            )

        if not payload.get("available"):
            return ActionExecutionResult(
                action_name="get_now_playing",
                success=False,
                message="Chưa thấy media session nào đang phát.",
                data={"reason": str(payload.get("reason", ""))},
            )

        title = str(payload.get("title", "")).strip()
        artist = str(payload.get("artist", "")).strip()
        album = str(payload.get("album", "")).strip()
        source = str(payload.get("source", "")).strip()
        if title and artist:
            message = f"Đang phát: {title} - {artist}."
        elif title:
            message = f"Đang phát: {title}."
        else:
            message = "Đã thấy media session đang phát nhưng không lấy được tiêu đề bài."
        return ActionExecutionResult(
            action_name="get_now_playing",
            success=bool(title or artist or source),
            message=message,
            data={
                "title": title,
                "artist": artist,
                "album": album,
                "source": source,
            },
        )

    #Dùng Win32 API qua ctypes: Nó enumerate tất cả window:rồi tìm title match keyword.
    def _focus_window(self, action_input: str) -> ActionExecutionResult:
        title_hint = (action_input or "").strip().lower()
        if not title_hint:
            return ActionExecutionResult(
                action_name="focus_window",
                action_input=action_input,
                success=False,
                message="Thiếu tiêu đề hoặc từ khóa của cửa sổ cần focus.",
            )

        user32 = ctypes.windll.user32
        hwnds: list[tuple[int, str]] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value.strip()
            if title:
                hwnds.append((hwnd, title))
            return True

        user32.EnumWindows(callback_type(_callback), 0)
        for hwnd, title in hwnds:
            if title_hint not in title.lower():
                continue
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            return ActionExecutionResult(
                action_name="focus_window",
                action_input=action_input,
                success=True,
                message=f"Đã đưa cửa sổ '{title}' lên foreground.",
                data={"matched_title": title},
            )

        return ActionExecutionResult(
            action_name="focus_window",
            action_input=action_input,
            success=False,
            message=f"Không tìm thấy cửa sổ phù hợp với '{action_input}'.",
        )

    def _open_url(self, action_input: str) -> ActionExecutionResult:
        url = self._normalize_url(action_input)
        if not url:
            return ActionExecutionResult(
                action_name="open_url",
                action_input=action_input,
                success=False,
                message="URL không hợp lệ.",
            )

        opened = webbrowser.open(url)
        return ActionExecutionResult(
            action_name="open_url",
            action_input=url,
            success=bool(opened),
            message=f"Đã yêu cầu hệ thống mở {url}.",
            data={"url": url},
        )
    '''
    press volume down 50 lần
    press volume up X lần
    Thông qua:
    keybd_event()
    '''
    def _set_volume(self, action_input: str) -> ActionExecutionResult:
        lowered = (action_input or "").strip().lower()
        if lowered in {"mute", "silent", "tat am", "tat tieng"}:
            self._press_virtual_key(self._VK_VOLUME_MUTE)
            return ActionExecutionResult(
                action_name="set_volume",
                action_input=action_input,
                success=True,
                message="Đã gửi lệnh mute/unmute âm lượng hệ thống.",
                status="dispatched",
            )

        max_words = {"max", "maximum", "full", "100", "toi da", "tối đa", "het co", "hết cỡ", "to nhat", "to nhất"}
        min_words = {"min", "minimum", "0", "nho nhat", "nhỏ nhất", "im lang", "im lặng"}
        if lowered in max_words or any(word in lowered for word in max_words):
            level = 100
        elif lowered in min_words or any(word in lowered for word in min_words):
            level = 0
        else:
            match = re.search(r"-?\d+", lowered)
            if not match:
                return ActionExecutionResult(
                    action_name="set_volume",
                    action_input=action_input,
                    success=False,
                    message="Cần truyền mức âm lượng từ 0 đến 100.",
                )
            level = max(0, min(100, int(match.group(0))))
        for _ in range(50):
            self._press_virtual_key(self._VK_VOLUME_DOWN, pause=0.005)
        for _ in range(round(level / 2)):
            self._press_virtual_key(self._VK_VOLUME_UP, pause=0.005)

        return ActionExecutionResult(
            action_name="set_volume",
            action_input=str(level),
            success=True,
            message=f"Đã gửi yêu cầu đưa âm lượng hệ thống về khoảng {level} phần trăm.",
            data={"level": str(level)},
            status="dispatched",
        )


    def _get_weather(self, action_input: str) -> ActionExecutionResult:
        return ActionExecutionResult(
            action_name="get_weather", action_input=action_input, success=False,
            message="Công cụ thời tiết cũ đã ngừng sử dụng cùng provider Gemini.",
        )

    def _play_pause_media(self, action_input: str) -> ActionExecutionResult:
        self._press_virtual_key(self._VK_MEDIA_PLAY_PAUSE)
        return ActionExecutionResult(
            action_name="play_pause_media",
            action_input=action_input,
            success=True,
            message="Đã gửi phím play/pause media.",
        )

    # Dùng Win32 clipboard API trực tiếp.
    def _copy_text(self, action_input: str) -> ActionExecutionResult:
        text = action_input or ""
        if not text:
            return ActionExecutionResult(
                action_name="copy_text",
                action_input=action_input,
                success=False,
                message="Không có nội dung để copy.",
            )

        self._set_clipboard_text(text)
        return ActionExecutionResult(
            action_name="copy_text",
            action_input=action_input,
            success=True,
            message="Đã đưa nội dung vào clipboard.",
            data={"text_length": str(len(text))},
        )

    def _launch_app(self, action_input: str) -> ActionExecutionResult:
        command = (action_input or "").strip()
        if not command:
            return ActionExecutionResult(
                action_name="launch_app",
                action_input=action_input,
                success=False,
                message="Thiếu tên app hoặc command cần chạy.",
            )

        alias_map = {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "calc": "calc.exe",
            "paint": "mspaint.exe",
            "code": "Code.exe",
            "vscode": "Code.exe",
            "spotify": "Spotify.exe",
            "chrome": "chrome.exe",
            "edge": "msedge.exe",
        }
        normalized = alias_map.get(command.lower(), command)
        args = shlex.split(normalized, posix=False)
        subprocess.Popen(args, close_fds=True)
        return ActionExecutionResult(
            action_name="launch_app",
            action_input=normalized,
            success=True,
            message=f"Đã yêu cầu hệ thống khởi chạy '{normalized}'.",
            data={"command": normalized},
        )

    def _normalize_url(self, raw: str) -> str:
        candidate = (raw or "").strip()
        if not candidate:
            return ""
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", candidate):
            return candidate
        if re.match(r"^[\w.-]+\.[a-zA-Z]{2,}(/\S*)?$", candidate):
            return f"https://{candidate}"
        return ""

    def _press_virtual_key(self, virtual_key: int, pause: float = 0.03) -> None:
        ctypes.windll.user32.keybd_event(virtual_key, 0, 0, 0)
        time.sleep(pause)
        ctypes.windll.user32.keybd_event(virtual_key, 0, 0x0002, 0)
        time.sleep(pause)

    def _set_clipboard_text(self, text: str) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        cf_unicode_text = 13
        ghnd = 0x0042
        data = text.encode("utf-16-le") + b"\x00\x00"

        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.restype = ctypes.c_bool
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = ctypes.c_bool
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = ctypes.c_bool
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = ctypes.c_bool
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.restype = ctypes.c_void_p

        if not user32.OpenClipboard(None):
            raise RuntimeError("Không mở được clipboard.")

        handle = None
        locked = None
        try:
            user32.EmptyClipboard()
            handle = kernel32.GlobalAlloc(ghnd, len(data))
            if not handle:
                raise RuntimeError("Không cấp phát được bộ nhớ cho clipboard.")
            locked = kernel32.GlobalLock(handle)
            if not locked:
                raise RuntimeError("Không lock được bộ nhớ clipboard.")
            ctypes.memmove(locked, data, len(data))
            kernel32.GlobalUnlock(handle)
            locked = None
            if not user32.SetClipboardData(cf_unicode_text, handle):
                raise RuntimeError("Không đưa được dữ liệu vào clipboard.")
            handle = None
        finally:
            if locked:
                kernel32.GlobalUnlock(handle)
            if handle:
                kernel32.GlobalFree(handle)
            user32.CloseClipboard()

    def _log(self, level: str, message: str, *args) -> None:
        if self.logger is None:
            return
        getattr(self.logger, level)(message, *args)

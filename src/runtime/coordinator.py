# -*- coding: utf-8 -*-
"""ERIS runtime composition root.

Behavior lives in typed services and compatibility mixins. This module keeps the
public ``RuntimeCoordinator`` import stable while refusing to become a second
implementation of every subsystem again. Humanity has tried the god-object
pattern often enough to know how that movie ends.
"""
from __future__ import annotations

import os

from domain import Decision
from failure_meta import is_technical_failure_meta
from runtime.autonomous_loop_runtime import AutonomousLoopRuntimeMixin
from runtime.chat_cycle import ChatCycleMixin
from runtime.cognitive_event_runtime import CognitiveEventRuntimeMixin
from runtime.coordinator_bootstrap import CoordinatorBootstrapMixin
from runtime.coordinator_shutdown import CoordinatorShutdownMixin
from runtime.incoming_protocol_runtime import IncomingProtocolRuntimeMixin
from runtime.mental_process_runtime import MentalProcessRuntimeMixin
from runtime.model_memory_runtime import ModelMemoryRuntimeMixin
from runtime.observer_private_runtime import ObserverPrivateRuntimeMixin
from runtime.presence_controller import PresenceControllerMixin
from runtime.prompt_context_builder import PromptContextBuilderMixin
from runtime.provider_action_runtime import ProviderActionRuntimeMixin
from runtime.public_output_runtime import PublicOutputRuntimeMixin
from runtime.screen_vision_runtime import ScreenVisionRuntimeMixin
from runtime.settings_control_runtime import SettingsControlRuntimeMixin
from runtime.state_commit_service import StateCommitServiceMixin
from runtime.tutor_research_runtime import TutorResearchRuntimeMixin
from runtime.user_control_runtime import UserControlRuntimeMixin

VISION_TRACE_MARKER = "vision_facts_attached"

FALLBACK_BY_INTENT: dict[str, tuple[str, ...]] = {
    "random_chatter": (
        "bạn gọi tôi ra chỉ để nghe tôi nói linh tinh à.",
        "Tín hiệu mờ quá. Tôi lùi lại một nhịp.",
    ),
    "inner_whim": ("Mạch này vẫn hơi nghẽn lại.", "Nhịp này cứ để nhỏ thôi."),
    "micro_presence": ("Mạch này vẫn hơi nghẽn.", "Nhịp này vẫn còn ở đây."),
    "curious_question": ("Có chỗ nào đang vướng nhẹ không?", "Đoạn này còn mắc ở đâu à?"),
    "screen_observation": (
        "Tôi chỉ dám bám vào chỗ này: file_saved.",
        "Tôi giữ câu này sát phần đang hiện ra thôi.",
    ),
}


def _is_nonsemantic_fallback_decision(decision: Decision) -> bool:
    """Return whether a public decision is only a technical fallback."""

    intent = str(getattr(decision, "intent", "") or "")
    parse_mode = str(getattr(decision, "parse_mode", "") or "")
    debug_trace = str(getattr(decision, "debug_trace", "") or "")
    text = str(getattr(decision, "text", "") or "").lower()
    fallback_intents = {"fallback_reply", "format_repair_fallback", "service_error", "no_fallback_block"}
    fallback_modes = {"format_repair_fallback", "service_error", "no_fallback_block"}
    if is_technical_failure_meta(getattr(decision, "failure_meta", None)):
        return True
    if intent in fallback_intents or parse_mode in fallback_modes:
        return True
    if "http_5xx_fallback" in debug_trace or "format_violation_recovered" in debug_trace:
        return True
    return "lỗi định dạng" in text or "loi dinh dang" in text or "vấn đề đường truyền" in text


class RuntimeCoordinator(
    CoordinatorBootstrapMixin,
    ModelMemoryRuntimeMixin,
    PublicOutputRuntimeMixin,
    UserControlRuntimeMixin,
    CoordinatorShutdownMixin,
    AutonomousLoopRuntimeMixin,
    ObserverPrivateRuntimeMixin,
    CognitiveEventRuntimeMixin,
    MentalProcessRuntimeMixin,
    IncomingProtocolRuntimeMixin,
    SettingsControlRuntimeMixin,
    TutorResearchRuntimeMixin,
    ScreenVisionRuntimeMixin,
    ProviderActionRuntimeMixin,
    ChatCycleMixin,
    StateCommitServiceMixin,
    PromptContextBuilderMixin,
    PresenceControllerMixin,
):
    """Stable facade over the composed ERIS runtime services."""

    _CHAT_VISION_THROTTLE_SEC = 6.0
    _LIGHT_TUTOR_RESEARCH_CUES = (
        "nghiên cứu", "nghien cuu", "đào sâu", "dao sau", "kiểm chứng", "kiem chung",
        "hỏi gemini", "hoi gemini", "hỏi sư phụ", "hoi su phu", "fix elegant", "deep research",
    )
    _LIGHT_TUTOR_COMMAND_WORDS = (
        "hỏi", "hoi", "nhờ", "nho", "kêu", "keu", "gemini", "tutor", "sư phụ", "su phu",
    )
    _LIGHT_TUTOR_REFERENT_ONLY = (
        "cái này", "cai nay", "cái đó", "cai do", "phần này", "phan nay",
        "phần đó", "phan do", "ý này", "y nay",
    )
    _KNOWLEDGE_QUESTION_MARKERS = (
        "hỏi gemini", "hoi gemini", "hỏi sư phụ", "hoi su phu", "nhờ gemini",
        "nho gemini", "kêu gemini", "keu gemini", "tra gemini",
        "là gì", "là ai", "là con gì", "là cái gì", "là chất gì", "là nước nào",
        "là bệnh gì", "là môn gì", "là ngôn ngữ gì", "nghĩa là gì", "viết tắt của",
        "tại sao", "vì sao", "do đâu", "nguyên nhân", "ở đâu", "khi nào", "năm nào",
        "thế kỷ", "ai phát minh", "ai sáng", "ai là người", "khác nhau", "khác gì",
        "so với", "có tác dụng gì", "hoạt động như thế nào", "hoạt động ra sao", "định nghĩa",
    )

    def __init__(
        self,
        *,
        logger,
        transport,
        llm_service,
        parser,
        telemetry_service,
        memory_core,
        body_running_fn,
        action_executor=None,
        memory_semantic_parser=None,
    ):
        super().__init__(
            logger=logger,
            transport=transport,
            llm_service=llm_service,
            parser=parser,
            telemetry_service=telemetry_service,
            memory_core=memory_core,
            body_running_fn=body_running_fn,
            action_executor=action_executor,
            memory_semantic_parser=memory_semantic_parser,
        )
        # Agent Console là projection/control plane độc lập với cognition. Gắn bridge
        # ở composition root để Subject Core có thể comment/advisory/redirect/veto
        # executor mà không phải import LangGraph hay biết schema tool của coding subbrain.
        try:
            from runtime.agent_harness.task_store import AgentTaskStore
            from runtime.agent_harness.event_bus import AgentEventBus
            from runtime.agent_harness.supervisor_bridge import AgentSupervisorBridge

            self.agent_task_store = AgentTaskStore()
            self.agent_event_bus = AgentEventBus(self.agent_task_store, logger=logger)
            self.agent_supervisor = AgentSupervisorBridge(
                self.agent_task_store,
                self.agent_event_bus,
                logger=logger,
            )
            self._agent_supervisor_marks = {}
            add_listener = getattr(self.agent_event_bus, "add_listener", None)
            if callable(add_listener):
                add_listener(self._supervise_agent_event)
        except Exception as exc:
            self.agent_task_store = None
            self.agent_event_bus = None
            self.agent_supervisor = None
            self._agent_supervisor_marks = {}
            debug = getattr(logger, "debug", None)
            if callable(debug):
                debug("[agent-console] supervisor bridge unavailable: %s", exc)

        # Unified Agent Harness: dedicated official-API coding subbrain owns every
        # coding decision; side effects remain inside ActionExecutor/SandboxWorld.
        # No WebAI or secondary coding-agent fallback may steal task ownership implicitly.
        # Import lazy để Eris vẫn boot được trên máy chưa cài optional LangGraph.
        self.agent_harness = None
        self.agent_ws_service = None
        self.agent_ide_rpc = None
        self.agent_executor_backend = None
        self.agent_capability_self_model = None
        self._agent_task_threads = {}
        self._agent_last_started_task_id = ""
        self.agent_default_workspace = ""
        # V78: Eris native AgentHarness là coding lane chính thức cho Eris Code.
        # AgentHarness cũ chỉ còn developer rollback flag, không được tự trở thành
        # coding brain thứ hai trong production Desktop.
        legacy_coding_enabled = str(
            os.environ.get("ERIS_LEGACY_CODING_HARNESS_ENABLED", "0") or "0"
        ).strip().lower() not in {"0", "false", "no", "off", ""}

        if action_executor is not None and legacy_coding_enabled:
            try:
                from pathlib import Path
                from runtime.agent_harness.official_api_executor import OfficialApiDecisionExecutor
                from runtime.agent_harness.graph_builder import AgentHarness
                from runtime.agent_harness.native_tool_adapter import NativeCapabilityToolAdapter

                source_root = Path(__file__).resolve().parents[1]
                workspace_root = source_root
                for candidate in (source_root, *source_root.parents):
                    if (candidate / ".git").is_dir():
                        workspace_root = candidate
                        break
                self.agent_default_workspace = str(workspace_root)
                native_tools = NativeCapabilityToolAdapter(action_executor)
                self.agent_ide_rpc = None

                coding_client = getattr(llm_service, "coding_client", None)
                if coding_client is None:
                    raise RuntimeError("dedicated_coding_client_unavailable")
                coding_executor = OfficialApiDecisionExecutor(
                    coding_client,
                    workspace=workspace_root,
                    logger=logger,
                )
                self.agent_executor_backend = coding_executor
                self.agent_harness = AgentHarness(
                    coding_executor,
                    str(source_root / "powers"),
                    tool_registry=native_tools.registry(),
                    tool_specs=native_tools.tool_specs(),
                    task_store=self.agent_task_store,
                    event_bus=self.agent_event_bus,
                    executor_name="official-api-coding",
                    model_name=coding_executor.model,
                    workspace_scope=lambda ref: self.workspace_manager.scoped_active(ref),
                    ide_rpc=self.agent_ide_rpc,
                    logger=logger,
                )
                ok, reason = coding_executor.health()
                from runtime.agent_harness.capability_self_model import CapabilitySelfModel
                self.agent_capability_self_model = CapabilitySelfModel(
                    harness=self.agent_harness,
                    harness_ready=ok,
                )
                info = getattr(logger, "info", None)
                if callable(info):
                    info(
                        "[agent-harness] ready graph=langgraph executor=official-api-coding health=%s detail=%s skills=%s tools=%s",
                        ok, reason, len(self.agent_harness.discovery.capabilities), len(native_tools.agent_ids()),
                    )
                    info(
                        "[capability-self] instant=%s long_skills=%s executor_ready=%s",
                        len(self.agent_capability_self_model.instant_capabilities()),
                        len(self.agent_capability_self_model.long_skills()),
                        self.agent_capability_self_model.harness_ready,
                    )

                self.agent_ws_service = None
            except Exception as exc:
                self.agent_harness = None
                self.agent_ide_rpc = None
                self.agent_capability_self_model = None
                warn = getattr(logger, "warning", None)
                if callable(warn):
                    warn("[agent-harness] unavailable; VS Code bridge disabled: %s", exc, exc_info=True)
        else:
            info = getattr(logger, "info", None)
            if callable(info):
                info(
                    "[V78] legacy coding AgentHarness disabled; Eris native agent harness owns coding lane. "
                    "Set ERIS_LEGACY_CODING_HARNESS_ENABLED=1 only for temporary rollback."
                )

        # Self-model tồn tại ngay cả khi coding Agent Harness đang unavailable.
        # Eris vẫn phải biết các native capability nhanh của chính mình thay vì
        # suy ra "không làm được" chỉ vì coding lane đang chết.
        if self.agent_capability_self_model is None:
            try:
                from runtime.agent_harness.capability_self_model import CapabilitySelfModel
                self.agent_capability_self_model = CapabilitySelfModel(
                    harness=None,
                    harness_ready=False,
                )
            except Exception:
                self.agent_capability_self_model = None

        # Agent WebSocket service is opt-in only. Eris Code agent bridge never
        # depends on this runtime startup path.

        # Tutor Executive V1: một composition root duy nhất cho learning truth +
        # tutor cognition. LearningService giữ evidence/mastery deterministic;
        # TutorExecutive chỉ sở hữu observe/diagnose/strategy/assessment loop.
        # Chưa giành hot-path delivery của PedagogicalRuntime trong bước migration này.
        self.learning_service = None
        self.tutor_executive = None
        try:
            from runtime.tutor import build_tutor_runtime

            learner_client = getattr(self.llm_service, "learner_client", None)
            tutor_bundle = build_tutor_runtime(
                model_router=getattr(self, "ai_model_router", None),
                learner_client=learner_client,
                logger=logger,
            )
            self.learning_service = tutor_bundle.learning_service
            self.tutor_executive = tutor_bundle.tutor_executive
            from runtime.capabilities.tutor import TutorCapability
            self.tutor_capability = TutorCapability(self.tutor_executive)
            from runtime.learner_portrait import LearnerPortraitService
            self.learner_portrait_service = LearnerPortraitService()
            self._ensure_incoming_dispatcher()
        except Exception as exc:
            debug = getattr(logger, "debug", None)
            if callable(debug):
                debug("[tutor-v1] tutor runtime unavailable: %s", exc)

        # V80 Engineering Mentor tồn tại độc lập với coding extension/observer link.
        # Nó chỉ giữ skill projection + teaching policy, tuyệt đối không có coding authority.
        self.engineering_mentor_service = None
        try:
            from runtime.engineering_mentor import EngineeringMentorService
            self.engineering_mentor_service = EngineeringMentorService(
                logger=logger,
                learning_service=self.learning_service,
            )
        except Exception as exc:
            debug = getattr(logger, "debug", None)
            if callable(debug):
                debug("[mentor-v80] optional engineering mentor unavailable: %s", exc)

        # V86: read-model thống kê năng lực. Đây chỉ là projection từ Mentor state,
        # không gọi model/tool và không có quyền điều khiển coding agent.
        self.capability_projection_service = None
        try:
            from runtime.capability_projection import CapabilityProjectionService
            self.capability_projection_service = CapabilityProjectionService(logger=logger)
        except Exception as exc:
            debug = getattr(logger, "debug", None)
            if callable(debug):
                debug("[mentor-v86] capability projection unavailable: %s", exc)

        # V87: state local-first cho correction/mục tiêu user chọn trong Capability Panel.
        # Service này không gọi model/tool và không có quyền điều khiển coding agent.
        self.development_control_service = None
        try:
            from runtime.development_control import DevelopmentControlService
            self.development_control_service = DevelopmentControlService(logger=logger)
        except Exception as exc:
            debug = getattr(logger, "debug", None)
            if callable(debug):
                debug("[mentor-v87] development control unavailable: %s", exc)

        # Companion Consolidation: ProjectTrajectory V88 từng tự đọc CodingObservation
        # do extension sinh ra. Giữ attribute None cho compatibility UI cũ, nhưng
        # Desktop không boot service này nữa. Khi user muốn Eris xem repo/diff/log,
        # evidence phải đi qua kênh Eris/user chủ động chia sẻ, không qua hidden bridge.
        self.project_trajectory_service = None

        # V91: Eris Desktop và Eris Code tách runtime thật sự.
        #
        # Hai attribute giữ lại để code/UI cũ kiểm tra availability không vỡ,
        # nhưng normal startup KHÔNG mở listener, không đọc coding extension telemetry
        # và không chờ extension. User muốn Eris xem repo/diff/log thì đưa evidence
        # qua chính kênh Eris; companion model học từ conversation-first context.
        self.eris_code_observer_link = None
        self.coding_observation_service = None

        # Dedicated lightweight inbound lane: Root/Writer may block foreground
        # polling for seconds. Exactly one reader owns the original transport;
        # non-Canvas packets retain their existing foreground dispatch semantics.
        self.learning_canvas_service = None
        self.canvas_control_inbox = None
        try:
            from runtime.learning_canvas import LearningCanvasService

            if self.learning_service is not None:
                self.learning_canvas_service = LearningCanvasService(
                    self.learning_service, store=self.learning_service._store,
                )
                from runtime.learning_canvas.events import connect_canvas_host
                connect_canvas_host(self)
        except Exception as exc:
            logger.warning("[learning-canvas] pack unavailable: %s", type(exc).__name__)
        try:
            from runtime.learning_canvas.inbox import CanvasControlInbox

            inbox = CanvasControlInbox(
                self.transport, self._handle_learning_canvas_request, logger=logger,
            )
            inbox.start()
            self.canvas_control_inbox = inbox
            self.transport = inbox
        except Exception as exc:
            logger.warning("[learning-canvas] startup unavailable: %s", type(exc).__name__)

    def _supervise_agent_event(self, event: dict) -> None:
        """Deterministic first-line technical supervision for coding-agent execution.

        This is intentionally narrow: only intervene on observable anti-patterns
        with low false-positive risk. Subject Core/model review can add richer
        judgment later through the same AgentSupervisorBridge.
        """
        if not isinstance(event, dict):
            return
        event_type = str(event.get("type", "") or "").strip().lower()
        if event_type.startswith("supervisor_") or event_type in {"user_directive", "directive_consumed"}:
            return
        task_id = str(event.get("task_id", "") or "").strip()
        if not task_id:
            # Events persisted by the store do not currently duplicate task_id;
            # derive it by locating the matching event id in recent tasks.
            event_id = str(event.get("event_id", "") or "")
            store = getattr(self, "agent_task_store", None)
            if store is not None and event_id:
                snapshot = store.snapshot(task_limit=8, event_limit=40)
                for task in snapshot.get("tasks", []):
                    if any(str(item.get("event_id", "")) == event_id for item in task.get("events", [])):
                        task_id = str(task.get("task_id", "") or "")
                        break
        if not task_id:
            return

        store = getattr(self, "agent_task_store", None)
        supervisor = getattr(self, "agent_supervisor", None)
        if store is None or supervisor is None:
            return
        task = store.get_task(task_id, event_limit=48)
        if not isinstance(task, dict):
            return
        events = [item for item in task.get("events", []) if isinstance(item, dict)]
        marks = self._agent_supervisor_marks.setdefault(task_id, set())

        def _mark_once(key: str, instruction: str, reason: str, *, mode: str = "advisory") -> None:
            # Các anti-pattern kỹ thuật đã quan sát được phải tác động vào executor thật,
            # không chỉ biến thành một dòng status đẹp mắt rồi bị coding executor phớt lờ.
            if key in marks:
                return
            marks.add(key)
            try:
                if mode == "redirect":
                    supervisor.reconsider(task_id, instruction, reason=reason)
                elif mode == "stop":
                    supervisor.stop(task_id, reason, instruction=instruction)
                else:
                    supervisor.advisory(task_id, instruction, reason=reason)
            except Exception:
                marks.discard(key)

        # Patch should follow evidence. If the coding subbrain tries to write before
        # inspecting project evidence, push it back toward root-cause work.
        if event_type == "tool_started":
            tool = str((event.get("data") or {}).get("tool", "") if isinstance(event.get("data"), dict) else "")
            if tool == "project_apply_patch":
                inspected = any(
                    str((item.get("data") or {}).get("tool", "")) in {
                        "project_read_file", "project_search_text", "project_find_symbol", "project_git_diff",
                        "read_current_error", "inspect_runtime_log",
                    }
                    for item in events
                    if isinstance(item.get("data"), dict)
                )
                if not inspected:
                    _mark_once(
                        "patch_before_inspection",
                        "Dừng vá ngay. Đọc implementation/caller và evidence hiện có trước, rồi mới chọn patch nhỏ nhất.",
                        "Coding subbrain bắt đầu project_apply_patch trước khi có inspection evidence trong task.",
                        mode="redirect",
                    )

        # Repeated failures are a replan signal, not a retry invitation.
        failed_tools = [
            str((item.get("data") or {}).get("tool", ""))
            for item in events
            if str(item.get("type", "")) == "tool_failed" and isinstance(item.get("data"), dict)
        ]
        if len(failed_tools) >= 2 and failed_tools[-1] and failed_tools[-1] == failed_tools[-2]:
            _mark_once(
                f"repeat_fail:{failed_tools[-1]}",
                f"Đừng retry {failed_tools[-1]} theo cùng cách. Đọc failure mới, đổi giả thuyết hoặc đổi tool rồi replan.",
                "Cùng một tool vừa thất bại liên tiếp.",
                mode="redirect",
            )

        test_failures = sum(
            1 for item in events
            if str(item.get("type", "")) == "test_result"
            and isinstance(item.get("data"), dict)
            and int((item.get("data") or {}).get("failed", 0) or 0) > 0
        )
        if test_failures >= 2:
            _mark_once(
                "repeated_test_failure",
                "Hai vòng test đã fail. Dừng sửa nối tiếp theo triệu chứng; quay lại failure gần nhất và xác minh root cause trước patch tiếp.",
                "Verification thất bại lặp lại trong cùng task.",
                mode="redirect",
            )

        changed_files = list(task.get("changed_files", []) or [])
        if len(changed_files) >= 10:
            _mark_once(
                "scope_expansion",
                "Scope đang phình quá rộng. Kiểm tra lại xem có thể giải quyết bằng ít file hơn và tái sử dụng abstraction hiện hữu không.",
                f"Task đã chạm {len(changed_files)} file.",
                mode="redirect",
            )

        # Terminal coding work becomes a typed capability event. The worker never
        # writes public history or chooses wording; RootAgent owns that decision.
        if event_type in {"task_completed", "task_failed"}:
            terminal_key = f"frontstage_terminal:{event_type}"
            if terminal_key not in marks:
                marks.add(terminal_key)
                try:
                    data = event.get("data") if isinstance(event.get("data"), dict) else {}
                    final_text = str(data.get("final", "") or event.get("detail", "") or event.get("summary", "") or "").strip()
                    goal = str(task.get("clarified_goal", "") or task.get("goal", "") or "").strip()
                    from collections import deque
                    from runtime.capabilities import WorkResult

                    result = WorkResult(
                        status="completed" if event_type == "task_completed" else "failed",
                        summary=final_text[:16000],
                        artifact_refs=tuple(str(item)[:1000] for item in task.get("changed_files", ()) or ()),
                        metadata={
                            "task_id": str(task_id or "")[:180],
                            "goal": goal[:2000],
                            "event_type": event_type,
                        },
                    )
                    pending = getattr(self, "_pending_conversation_capability_events", None)
                    if pending is None:
                        pending = deque(maxlen=128)
                        self._pending_conversation_capability_events = pending
                    scope = getattr(self, "_canvas_work_scopes", {}).get(str(task_id), {})
                    metadata = {"work_capability": result.to_dict(), "canvas_scope": scope}
                    from runtime.learning_canvas.events import allow_canvas_result
                    if not allow_canvas_result(self, "work_result", metadata, event_id=f"work:{task_id}:{event_type}"):
                        return
                    pending.append({
                        "event_id": f"work:{task_id}:{event_type}",
                        "kind": "work_result",
                        "metadata": metadata,
                    })
                    wake = getattr(self, "_wake_autonomous_loop", None)
                    if callable(wake):
                        wake(f"agent_{event_type}:{task_id}")
                except Exception as exc:
                    logger = getattr(self, "logger", None)
                    debug = getattr(logger, "debug", None)
                    if callable(debug):
                        debug("[agent-harness] terminal frontstage bridge degraded task=%s error=%s", task_id, exc)

    def _resolve_agent_workspace(self, workspace_ref: str = "") -> dict:
        """Resolve and activate one persistent workspace for a task.

        The returned id/path is copied into task metadata and LangGraph state, so
        later UI workspace switches cannot silently move an already-running task.
        """
        manager = getattr(self, "workspace_manager", None)
        if manager is None:
            return {"workspace_id": "", "workspace_path": "", "workspace_name": ""}

        record = None
        ref = str(workspace_ref or "").strip()
        if ref:
            try:
                record = manager.switch(ref)
            except Exception:
                # Folder picker supplies a local path that may not have been
                # registered yet. join() persists it and activates it.
                record = manager.join(ref, activate=True)
        else:
            record = manager.active_record()
            if record is None:
                default_workspace = str(getattr(self, "agent_default_workspace", "") or "").strip()
                if default_workspace:
                    record = manager.join(default_workspace, activate=True)

        if record is None:
            return {"workspace_id": "", "workspace_path": "", "workspace_name": ""}
        return {
            "workspace_id": str(getattr(record, "workspace_id", "") or ""),
            "workspace_path": str(getattr(record, "path", "") or ""),
            "workspace_name": str(getattr(record, "display_name", "") or ""),
        }

    def execute_agent_task(
        self,
        goal: str,
        *,
        thread_id: str = "",
        clarified_goal: str = "",
        task_id: str = "",
        task_mode: str = "execute",
        autopilot: bool = True,
        source: str = "eris_chat",
        session_id: str = "",
        workspace_id: str = "",
        workspace_path: str = "",
        workspace_name: str = "",
        ide_context: dict | None = None,
        model_name: str = "",
    ):
        """Run one Unified Agent Harness task synchronously.

        Chat normally uses ``start_agent_task_async`` so coding API execution never
        blocks the public reply. Tests/CLI can still call this synchronous entry.
        """
        harness = getattr(self, "agent_harness", None)
        if harness is None:
            raise RuntimeError("agent_harness_unavailable")
        import uuid
        resolved_thread = str(thread_id or f"agent_{uuid.uuid4().hex[:12]}")
        import inspect
        invoke = harness.invoke
        kwargs = {
            "thread_id": resolved_thread,
            "task_id": str(task_id or ""),
            "clarified_goal": clarified_goal,
        }
        try:
            signature = inspect.signature(invoke)
            parameters = signature.parameters
            accepts_var_kwargs = any(
                item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values()
            )
            if accepts_var_kwargs or "task_mode" in parameters:
                kwargs["task_mode"] = str(task_mode or "execute")
            if accepts_var_kwargs or "autopilot" in parameters:
                kwargs["autopilot"] = bool(autopilot)
            if accepts_var_kwargs or "source" in parameters:
                kwargs["source"] = str(source or "eris_chat")
            if accepts_var_kwargs or "session_id" in parameters:
                kwargs["session_id"] = str(session_id or "")
            if accepts_var_kwargs or "ide_context" in parameters:
                kwargs["ide_context"] = dict(ide_context or {})
            if accepts_var_kwargs or "workspace_id" in parameters:
                kwargs["workspace_id"] = str(workspace_id or "")
            if accepts_var_kwargs or "workspace_path" in parameters:
                kwargs["workspace_path"] = str(workspace_path or "")
            if accepts_var_kwargs or "workspace_name" in parameters:
                kwargs["workspace_name"] = str(workspace_name or "")
            if accepts_var_kwargs or "model_name" in parameters:
                kwargs["model_name"] = str(model_name or "")
        except (TypeError, ValueError):
            # C-extension/mock callables may not expose a signature. Real AgentHarness
            # does, so falling back to the legacy four arguments is safe here.
            pass
        return invoke(goal, **kwargs)

    def start_agent_task_async(
        self,
        goal: str,
        *,
        thread_id: str = "",
        clarified_goal: str = "",
        source_turn_id: str = "",
        source: str = "eris_chat",
        session_id: str = "",
        task_mode: str = "execute",
        autopilot: bool = True,
        workspace_ref: str = "",
        model: str = "",
        ide_context: dict | None = None,
    ) -> dict:
        """Start the official-API coding/LangGraph worker and return immediately.

        The task id is reserved before the thread starts, so the foreground
        writer and Agent Console can refer to one stable execution session.
        """
        harness = getattr(self, "agent_harness", None)
        if harness is None:
            return {"status": "unavailable", "reason": "agent_harness_unavailable", "task_id": ""}

        import threading
        import uuid

        # Snapshot workspace identity before the worker starts. The graph keeps
        # this lock for the whole task even if the user changes the panel's active
        # workspace while the coding subbrain is still running.
        try:
            workspace = self._resolve_agent_workspace(workspace_ref)
        except Exception as exc:
            return {
                "status": "unavailable",
                "reason": f"workspace_unavailable:{type(exc).__name__}:{exc}",
                "task_id": "",
            }
        workspace_id = str(workspace.get("workspace_id", "") or "")
        workspace_path = str(workspace.get("workspace_path", "") or "")
        workspace_name = str(workspace.get("workspace_name", "") or "")
        ide_client = (ide_context or {}).get("client") if isinstance(ide_context, dict) else {}
        ide_client = ide_client if isinstance(ide_client, dict) else {}
        raw_ide_capabilities = ide_client.get("capabilities")
        if not isinstance(raw_ide_capabilities, (list, tuple, set)):
            raw_ide_capabilities = []
        ide_capabilities = [
            str(item).strip() for item in list(raw_ide_capabilities)[:80]
            if str(item or "").strip()
        ]

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        resolved_thread = str(thread_id or source_turn_id or f"agent_{uuid.uuid4().hex[:12]}")
        self._agent_last_started_task_id = task_id

        # Model identity is a task snapshot. Resolve it before touching the task
        # store so even lightweight/tests without a store cannot race or inherit
        # an uninitialized model variable.
        model_name = str(getattr(harness, "model_name", "") or "")
        requested_model = str(model or "").strip()
        backend = getattr(harness, "llm", getattr(harness, "executor", None))
        resolver = getattr(backend, "resolve_model", None)
        if callable(resolver):
            model_name = str(resolver(requested_model) or model_name)
        elif requested_model and requested_model.lower() != "auto":
            debug = getattr(getattr(self, "logger", None), "debug", None)
            if callable(debug):
                debug("[agent-harness] ignored task model override %s: backend is fixed", requested_model)

        # Reserve the task synchronously so Agent Console never races a just-
        # started worker and shows an empty screen for the first snapshot.
        store = getattr(self, "agent_task_store", None)
        if store is not None:
            try:
                executor = str(getattr(harness, "executor_name", "official-api-coding") or "official-api-coding")

                store.create_task(
                    clarified_goal or goal,
                    task_id=task_id,
                    executor=executor,
                    model=model_name,
                    thread_id=resolved_thread,
                    session_id=str(session_id or ""),
                    metadata={
                        "source_turn_id": source_turn_id,
                        "original_goal": goal,
                        "source": str(source or "eris_chat"),
                        "task_mode": str(task_mode or "execute"),
                        "autopilot": bool(autopilot),
                        "workspace_id": workspace_id,
                        "workspace_path": workspace_path,
                        "workspace_name": workspace_name,
                        # Main Eris đọc catalog nhỏ này để biết VS Code side hiện
                        # thực sự làm được gì; không cần đoán từ tên frontend.
                        "ide_capabilities": ide_capabilities,
                    },
                )
                store.update_task(task_id, clarified_goal=clarified_goal or goal)
                supervisor = getattr(self, "agent_supervisor", None)
                if supervisor is not None:
                    supervisor.comment(
                        task_id,
                        "Tôi đang theo dõi task này. Nếu hướng thực thi phình, lặp lỗi hoặc vá trước khi hiểu root cause, tôi sẽ can thiệp.",
                        importance=0.58,
                    )

                # Ghi message của user trước khi worker khởi chạy. Nhờ vậy history của
                # VS Code có thứ tự ổn định: user -> activity/tool -> assistant, không
                # phụ thuộc thread scheduler thắng thua trong vài mili-giây đầu tiên.
                event_bus = getattr(self, "agent_event_bus", None)
                if event_bus is not None:
                    event_bus.emit(
                        task_id,
                        "user_message",
                        actor="user",
                        summary=str(goal or "")[:4000],
                        importance=0.95,
                        data={
                            "content": str(goal or ""),
                            "thread_id": resolved_thread,
                            "session_id": str(session_id or ""),
                            "source": str(source or "eris_chat"),
                            # Attachment bytes/path vẫn nằm trong ide_context cho executor;
                            # event history chỉ cần metadata để UI render chip đẹp và an toàn.
                            "attachments": [
                                {
                                    "id": str(item.get("id", "") or "")[:120],
                                    "kind": str(item.get("kind", "file") or "file")[:24],
                                    "name": str(item.get("name", "attachment") or "attachment")[:240],
                                    "mime": str(item.get("mime", "") or "")[:120],
                                    "size": int(item.get("size", 0) or 0),
                                    "relativePath": str(item.get("relativePath", "") or "")[:600],
                                }
                                for item in (ide_context.get("attachments") if isinstance(ide_context, dict) and isinstance(ide_context.get("attachments"), list) else [])[:8]
                                if isinstance(item, dict)
                            ],
                        },
                    )
            except Exception as exc:
                logger = getattr(self, "logger", None)
                debug = getattr(logger, "debug", None)
                if callable(debug):
                    debug("[agent-harness] precreate task degraded: %s", exc)

        def _worker() -> None:
            try:
                # One task, one owner.  Every coding request enters the same
                # LangGraph state machine backed by the dedicated official-API
                # coding team. Explorer, Planner, Coder and Reviewer are separate
                # official-API specialist contexts sharing one checkpoint/tool spine.
                self.execute_agent_task(
                    goal,
                    thread_id=resolved_thread,
                    clarified_goal=clarified_goal,
                    task_id=task_id,
                    task_mode=task_mode,
                    autopilot=autopilot,
                    source=source,
                    session_id=session_id,
                    workspace_id=workspace_id,
                    workspace_path=workspace_path,
                    workspace_name=workspace_name,
                    ide_context=dict(ide_context or {}),
                    model_name=model_name,
                )

            except Exception as exc:
                logger = getattr(self, "logger", None)
                warning = getattr(logger, "warning", None)
                if callable(warning):
                    warning("[agent-harness] async task failed task=%s error=%s", task_id, exc)
            finally:
                try:
                    self._agent_task_threads.pop(task_id, None)
                except Exception:
                    pass

        thread = threading.Thread(
            target=_worker,
            name=f"eris-agent-{task_id[-6:]}",
            daemon=True,
        )
        self._agent_task_threads[task_id] = thread
        thread.start()
        return {
            "status": "started",
            "task_id": task_id,
            "thread_id": resolved_thread,
            "goal": clarified_goal or goal,
            "source": str(source or "eris_chat"),
            "session_id": str(session_id or ""),
            "task_mode": str(task_mode or "execute"),
            "autopilot": bool(autopilot),
            "workspace_id": workspace_id,
            "workspace_path": workspace_path,
            "workspace_name": workspace_name,
        }

    def resume_agent_task_async(
        self,
        task_id: str,
        *,
        response=None,
        source: str = "vscode",
        directive: str = "",
        ide_context: dict | None = None,
    ) -> dict:
        """Resume đúng LangGraph thread đã checkpoint mà không giữ worker polling."""
        harness = getattr(self, "agent_harness", None)
        store = getattr(self, "agent_task_store", None)
        task_id = str(task_id or "").strip()
        if harness is None or store is None:
            return {"status": "unavailable", "reason": "agent_harness_unavailable", "task_id": task_id}
        task = store.get_task(task_id)
        if task is None:
            return {"status": "not_found", "reason": "task_not_found", "task_id": task_id}

        # Capability của IDE có thể thay đổi sau reconnect/update extension. Persist
        # snapshot mới nhất vào public task metadata để Eris main và executor cùng
        # nhìn một contract thay vì mỗi bên tự tưởng tượng khả năng của bên kia.
        if isinstance(ide_context, dict):
            client_info = ide_context.get("client") if isinstance(ide_context.get("client"), dict) else {}
            raw_caps = client_info.get("capabilities")
            if not isinstance(raw_caps, (list, tuple, set)):
                raw_caps = []
            caps = [
                str(item).strip() for item in list(raw_caps)[:80]
                if str(item or "").strip()
            ]
            if caps:
                metadata = dict(task.get("metadata") or {})
                metadata["ide_capabilities"] = caps
                store.update_task(task_id, metadata=metadata)
                task["metadata"] = metadata

        active = self._agent_task_threads.get(task_id)
        if active is not None and active.is_alive():
            return {"status": "busy", "reason": "task_worker_active", "task_id": task_id}

        status = str(task.get("status", "") or "")
        if status not in {"waiting_user", "waiting_permission", "paused", "running"}:
            return {"status": status or "unavailable", "reason": "task_not_resumable", "task_id": task_id}

        import threading

        def _worker() -> None:
            try:
                harness.resume_task(
                    task_id,
                    response=response,
                    directive=str(directive or ""),
                    ide_context=dict(ide_context or {}),
                )
            except Exception as exc:
                logger = getattr(self, "logger", None)
                warning = getattr(logger, "warning", None)
                if callable(warning):
                    warning("[agent-harness] async resume failed task=%s error=%s", task_id, exc)
            finally:
                self._agent_task_threads.pop(task_id, None)

        thread = threading.Thread(
            target=_worker,
            name=f"eris-agent-resume-{task_id[-6:]}",
            daemon=True,
        )
        self._agent_task_threads[task_id] = thread
        thread.start()
        return {
            "status": "resuming",
            "task_id": task_id,
            "thread_id": str(task.get("thread_id", "") or ""),
            "source": str(source or "vscode"),
        }

    def control_agent_task(
        self,
        task_id: str,
        *,
        action: str,
        text: str = "",
        checkpoint_id: str = "",
        change_set_id: str = "",
        source: str = "vscode",
        ide_context: dict | None = None,
    ) -> dict:
        """Control plane chung cho VS Code và Agent Console legacy."""
        harness = getattr(self, "agent_harness", None)
        store = getattr(self, "agent_task_store", None)
        task_id = str(task_id or "").strip()
        action = str(action or "").strip().lower()
        if harness is None or store is None:
            return {"status": "unavailable", "reason": "agent_harness_unavailable"}
        task = store.get_task(task_id)
        if task is None:
            return {"status": "not_found", "reason": "task_not_found"}
        status = str(task.get("status", "") or "")

        # Interrupt thật không có worker ngủ chờ. Lựa chọn/directive phải resume
        # checkpoint trực tiếp thay vì nhét vào queue mà chẳng còn ai consume.
        if status in {"waiting_user", "waiting_permission"} and action in {"choice", "directive", "resume"}:
            response = {
                "choiceId": str(text or "") if action == "choice" else "other",
                "text": str(text or ""),
                "value": str(text or ""),
                "source": str(source or "vscode"),
                # Mang snapshot IDE mới nhất vào lần resume để graph không tiếp tục
                # bằng context đã cũ từ trước lúc người dùng sửa/chọn trong editor.
                "ideContext": dict(ide_context or {}),
            }
            if action == "resume" and not text:
                response = {
                    "choiceId": "resume",
                    "text": "",
                    "value": "",
                    "source": str(source or "vscode"),
                    "ideContext": dict(ide_context or {}),
                }
            return self.resume_agent_task_async(
                task_id,
                response=response,
                source=source,
                ide_context=dict(ide_context or {}),
            )

        worker = self._agent_task_threads.get(task_id)
        worker_alive = bool(worker is not None and worker.is_alive())

        if status == "paused" and action in {"resume", "directive"}:
            return self.resume_agent_task_async(
                task_id,
                response=None,
                source=source,
                directive=text,
                ide_context=dict(ide_context or {}),
            )

        # Sau khi Eris Service/VS Code restart, SQLite có thể còn task=running
        # trong khi daemon worker cũ đã chết cùng process. Cho phép resume trực
        # tiếp checkpoint thay vì để UI mắc kẹt ở một task "đang chạy" ma.
        if status == "running" and not worker_alive and action in {"resume", "directive"}:
            return self.resume_agent_task_async(
                task_id,
                response=None,
                source=source,
                directive=text,
                ide_context=dict(ide_context or {}),
            )

        # Resume là idempotent khi worker thật vẫn còn sống. Extension có thể gửi
        # recover trên mỗi reconnect mà không vô tình nhét control thừa vào graph.
        if status == "running" and worker_alive and action == "resume":
            return {"status": "running", "reason": "task_worker_already_active", "task_id": task_id}

        if action == "stop":
            store.enqueue_control(
                task_id,
                action,
                text=str(text or ""),
                checkpoint_id=str(checkpoint_id or ""),
                data={
                    "source": str(source or "vscode"),
                    "ide_context": dict(ide_context or {}),
                },
            )
            stopped = harness.stop_task(task_id, reason=f"control:{source}")
            return {"status": "stopped" if stopped else "failed", "task_id": task_id}

        if action == "rollback" and not worker_alive:
            try:
                restored = harness.restore_task_checkpoint(task_id, checkpoint_id or "latest")
                return {"status": "rolled_back", "task_id": task_id, "checkpoint": restored}
            except Exception as exc:
                return {"status": "failed", "reason": f"rollback_failed:{type(exc).__name__}:{exc}", "task_id": task_id}

        if action == "accept_changeset":
            try:
                result = harness.accept_change_set(task_id, change_set_id)
                return {"task_id": task_id, **dict(result or {})}
            except Exception as exc:
                return {"status": "failed", "reason": f"changeset_accept_failed:{type(exc).__name__}:{exc}", "task_id": task_id}

        if action == "revert_changeset":
            if worker_alive:
                return {"status": "busy", "reason": "pause_task_before_changeset_revert", "task_id": task_id}
            try:
                result = harness.revert_change_set(task_id, change_set_id)
                return {"task_id": task_id, **dict(result or {})}
            except Exception as exc:
                return {"status": "failed", "reason": f"changeset_revert_failed:{type(exc).__name__}:{exc}", "task_id": task_id}

        if action not in {"pause", "resume", "stop", "directive", "choice", "rollback", "accept_changeset", "revert_changeset"}:
            return {"status": "rejected", "reason": "unsupported_action", "task_id": task_id}
        control = store.enqueue_control(
            task_id,
            action,
            text=str(text or ""),
            checkpoint_id=str(checkpoint_id or ""),
            data={
                "source": str(source or "vscode"),
                "ide_context": dict(ide_context or {}),
            },
        )
        if control is None:
            return {"status": "failed", "reason": "control_enqueue_failed", "task_id": task_id}
        event_bus = getattr(self, "agent_event_bus", None)
        if event_bus is not None:
            event_bus.emit(
                task_id,
                "user_directive" if action == "directive" else f"{action}_requested",
                actor="user",
                summary=str(text or "") or f"Người dùng yêu cầu {action} task.",
                importance=0.88,
                data={"control_id": str(control.get("control_id", "") or ""), "source": str(source or "vscode")},
            )
        return {"status": "queued", "task_id": task_id, "control_id": str(control.get("control_id", "") or "")}

    def _agent_ws_capability_catalog(self) -> dict:
        """Catalog nhỏ cho handshake IDE; không buộc frontend hiểu nội bộ cognition."""
        brain = []
        self_model = getattr(self, "agent_capability_self_model", None)
        if self_model is not None:
            try:
                payload = self_model.catalog(include_unavailable=False, limit=64)
                if isinstance(payload, dict):
                    brain = payload
            except Exception:
                brain = []
        return {
            "brain": brain,
            "protocol": [
                "session.create",
                "message.user",
                "interrupt.resolve",
                "event.replay",
                "task.control",
                "task.list",
                "task.get",
                "thread.history",
                "editor.command.result",
            ],
            "editorCommands": [
                "editor.openFile",
                "editor.openDiff",
                "editor.focusProblems",
                "editor.showSourceControl",
            ],
            "contextProviders": [
                "current",
                "selection",
                "problems",
                "git",
                "workspace",
                "terminal.recent",
            ],
        }

    def start_vscode_user_turn(
        self,
        goal: str,
        *,
        thread_id: str = "",
        session_id: str = "",
        task_mode: str = "auto",
        autopilot: bool = True,
        workspace_ref: str = "",
        model: str = "",
        ide_context: dict | None = None,
        source: str = "vscode",
    ) -> dict:
        """Route a VS Code foreground turn before deciding whether it is a task."""
        import threading
        import uuid

        user_text = str(goal or "").strip()
        resolved_thread = str(thread_id or f"vscode_{uuid.uuid4().hex[:12]}").strip()
        requested_mode = str(task_mode or "auto").strip().lower()
        normalized_mode = requested_mode if requested_mode in {"auto", "plan", "inspect", "execute"} else "auto"

        try:
            from runtime.agent_harness.chat_router import classify_agent_chat_route
            self_model = getattr(self, "agent_capability_self_model", None)
            refresh_self_model = getattr(self_model, "refresh", None)
            if callable(refresh_self_model):
                refresh_self_model()
            route = classify_agent_chat_route(user_text, self_model=self_model)
        except Exception:
            route = None

        route_action = str(getattr(route, "action", "none") or "none")
        route_reason = str(getattr(route, "reason", "normal_chat") or "normal_chat")
        if route_action in {"execute", "consult"}:
            execution_mode = "inspect" if route_action == "consult" else ("execute" if normalized_mode == "auto" else normalized_mode)
            return self.start_agent_task_async(
                str(getattr(route, "goal", "") or user_text),
                thread_id=resolved_thread,
                clarified_goal=str(getattr(route, "goal", "") or user_text),
                source_turn_id=resolved_thread,
                source=source,
                session_id=session_id,
                task_mode=execution_mode,
                autopilot=autopilot,
                workspace_ref=workspace_ref,
                model=model,
                ide_context=ide_context,
            )

        try:
            workspace = self._resolve_agent_workspace(workspace_ref)
        except Exception:
            workspace = {"workspace_id": "", "workspace_path": "", "workspace_name": ""}
        workspace_id = str(workspace.get("workspace_id", "") or "")
        workspace_path = str(workspace.get("workspace_path", "") or "")
        workspace_name = str(workspace.get("workspace_name", "") or "")
        task_id = f"chat_{uuid.uuid4().hex[:12]}"
        metadata = {
            "original_goal": user_text,
            "source": str(source or "vscode"),
            "task_mode": "chat",
            "route_reason": route_reason,
            "autopilot": False,
            "workspace_id": workspace_id,
            "workspace_path": workspace_path,
            "workspace_name": workspace_name,
        }
        store = getattr(self, "agent_task_store", None)
        event_bus = getattr(self, "agent_event_bus", None)
        if store is None or event_bus is None:
            return {"status": "unavailable", "reason": "agent_chat_store_unavailable", "task_id": ""}

        store.create_task(
            user_text,
            task_id=task_id,
            executor="eris-chat",
            model=str(model or getattr(getattr(getattr(self, "llm_service", None), "client", None), "model_name", "") or ""),
            thread_id=resolved_thread,
            session_id=str(session_id or ""),
            metadata=metadata,
        )
        event_bus.emit(
            task_id,
            "user_message",
            actor="user",
            summary=user_text[:1200],
            importance=0.95,
            data={"content": user_text[:8000], "source": str(source or "vscode"), "attachments": []},
        )
        event_bus.emit(
            task_id,
            "task_started",
            actor="system",
            summary="Responding in chat",
            importance=0.45,
            data={"executor": "eris-chat", "route": route_reason},
        )

        def _worker() -> None:
            try:
                final = self._generate_vscode_chat_reply(user_text, route=route)
                event_bus.emit(
                    task_id,
                    "task_completed",
                    actor="assistant",
                    summary="Chat response complete",
                    importance=0.9,
                    data={"final": final},
                )
            except Exception as exc:
                event_bus.emit(
                    task_id,
                    "task_failed",
                    actor="system",
                    summary=f"Chat response failed: {type(exc).__name__}: {exc}",
                    importance=0.98,
                    data={"reason": "vscode_chat_failed"},
                )

        threading.Thread(target=_worker, name=f"eris-vscode-chat-{task_id[-6:]}", daemon=True).start()
        return {
            "status": "started",
            "task_id": task_id,
            "thread_id": resolved_thread,
            "goal": user_text,
            "source": str(source or "vscode"),
            "session_id": str(session_id or ""),
            "task_mode": "chat",
            "reason": route_reason,
        }

    def _generate_vscode_chat_reply(self, user_text: str, *, route=None) -> str:
        clarification = str(getattr(route, "clarification", "") or "").strip()
        if str(getattr(route, "action", "") or "") == "clarify" and clarification:
            return clarification

        llm_service = getattr(self, "llm_service", None)
        parser = getattr(self, "parser", None)
        if llm_service is None or parser is None:
            return "Mình nhận được tin nhắn rồi, nhưng chat runtime chưa sẵn sàng để sinh câu trả lời."
        build_user = getattr(parser, "build_chat_user_message", None)
        if callable(build_user):
            user_message = build_user(user_message=user_text, last_chat_answer="")
        else:
            user_message = f"Người dùng: {user_text}"
        raw = llm_service.generate_chat_reply(
            chat_history=[],
            user_message=user_message,
            raw_user_text=user_text,
            conversation_context=(
                "VS Code foreground chat. Reply naturally to the user's message. "
                "Do not claim that files were inspected, changed, tested, or executed unless a tool task actually ran."
            ),
            require_text=True,
            allow_local_fallback=False,
            reasoning_effort="low",
        )
        parse = getattr(parser, "parse", None)
        if callable(parse):
            decision = parse(raw, user_hint=user_text)
            text = str(getattr(decision, "text", "") or "").strip()
            if text:
                return text
        return str(raw or "").strip() or "Mình đang ở đây, nhưng lượt chat này không tạo được nội dung trả lời."

    def route_agent_user_turn(
        self,
        user_text: str,
        *,
        previous_assistant_text: str = "",
        turn_id: str = "",
    ) -> dict:
        """Conservative foreground routing into the execution harness.

        Returns a public-safe projection used by the normal Eris writer.  A
        ``clarify`` never starts the coding harness; ``execute`` starts the dedicated coding task in parallel.
        """
        try:
            from runtime.agent_harness.chat_router import classify_agent_chat_route
            self_model = getattr(self, "agent_capability_self_model", None)
            refresh_self_model = getattr(self_model, "refresh", None)
            if callable(refresh_self_model):
                refresh_self_model()
            route = classify_agent_chat_route(
                user_text,
                previous_assistant_text=previous_assistant_text,
                self_model=self_model,
            )
        except Exception as exc:
            return {
                "action": "none",
                "goal": "",
                "reason": f"router_error:{type(exc).__name__}",
                "confidence": 0.0,
                "clarification": "",
            }

        payload = route.to_dict()
        self_model = getattr(self, "agent_capability_self_model", None)
        if self_model is not None:
            try:
                assessment = self_model.assess(user_text)
                payload["self_model"] = assessment.to_dict()
                if route.reason == "capability_query":
                    payload["self_model_context"] = self_model.prompt_summary()
            except Exception:
                pass
        if route.action not in {"execute", "consult"}:
            return payload

        task_mode = "execute"
        started = self.start_agent_task_async(
            route.goal or user_text,
            thread_id=turn_id,
            clarified_goal=route.goal or user_text,
            source_turn_id=turn_id,
            source="eris_chat",
            task_mode=task_mode,
        )
        payload.update({
            "task_id": str(started.get("task_id", "") or ""),
            "start_status": str(started.get("status", "") or ""),
            "start_reason": str(started.get("reason", "") or ""),
            "task_mode": str(started.get("task_mode", task_mode) or task_mode),
            "source": str(started.get("source", "eris_chat") or "eris_chat"),
        })
        return payload

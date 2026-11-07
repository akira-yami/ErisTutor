from pathlib import Path

from runtime.agent_harness.capability_discovery import CapabilityDiscovery
from runtime.agent_harness.gemini_cli_executor import GeminiCliDecisionExecutor
from runtime.agent_harness.native_tool_adapter import NativeCapabilityToolAdapter
from runtime.agent_harness.permission_gate import PermissionGate, Permission
from runtime.agent_harness.skill_loader import SkillLoader


ROOT = Path(__file__).resolve().parents[1]


def test_skill_search_is_progressive_and_activation_reveals_tools():
    discovery = CapabilityDiscovery(str(ROOT / "powers"))
    result = discovery.search("đang nghe nhạc media")
    assert "desktop-awareness" in result
    assert "get_now_playing" not in result

    loader = SkillLoader(discovery)
    activated = loader.activate_skill("desktop-awareness")
    assert "get_now_playing" in activated
    assert "Desktop Awareness" in activated


def test_permission_gate_autopilot_runs_routine_dev_without_asking():
    gate = PermissionGate()
    assert gate.unattended is True
    assert gate.check_permission("project_read_file", {"path": "src/a.py"}) == Permission.ALLOW
    assert gate.check_permission("project_apply_patch", {"patch": "x"}) == Permission.ALLOW
    assert gate.check_permission("project_run_tests", {"command": "pytest -q"}) == Permission.ALLOW
    assert gate.check_permission("project_run_command", {"command": "git status"}) == Permission.ALLOW
    assert gate.check_permission("project_run_command", {"command": "git commit -am x"}) == Permission.ALLOW
    assert gate.check_permission("project_run_command", {"command": "git checkout feature-x"}) == Permission.ALLOW
    assert gate.check_permission("launch_app", {"app": "code"}) == Permission.ALLOW
    assert gate.check_permission("open_url", {"url": "https://example.com"}) == Permission.ALLOW
    assert gate.check_permission("workspace_switch", {"project_id": "x"}) == Permission.ALLOW


def test_permission_gate_autopilot_denies_protected_boundary_instead_of_prompting():
    gate = PermissionGate()
    assert gate.check_permission("project_run_command", {"command": "git push origin main"}) == Permission.DENY
    assert gate.check_permission("project_run_command", {"command": "git reset --hard HEAD~1"}) == Permission.DENY
    assert gate.check_permission("project_run_command", {"command": "git clean -fd"}) == Permission.DENY
    assert gate.check_permission("project_run_command", {"command": "npm publish"}) == Permission.DENY
    assert gate.check_permission("future_untyped_danger", {}) == Permission.DENY
    assert gate.check_permission(
        "future_read_tool", {}, tool_spec={"effect": "read", "risk_level": "low"}
    ) == Permission.ALLOW


def test_permission_gate_asks_only_for_material_side_effects():
    gate = PermissionGate()
    assert gate.check_permission("project_run_command", {"command": "npm install lodash"}) == Permission.ASK
    assert gate.check_permission("project_run_command", {"command": "alembic upgrade head"}) == Permission.ASK
    assert gate.check_permission("project_run_command", {"command": "git rebase main"}) == Permission.ASK
    assert gate.check_permission("project_run_command", {"command": "npm test"}) == Permission.ALLOW


def test_gemini_decision_parser_handles_fenced_json():
    payload = GeminiCliDecisionExecutor.parse_decision(
        '```json\n{"action":"tool","summary":"check media","tool_call":{"name":"get_now_playing","args":{}}}\n```'
    )
    assert payload["action"] == "tool"
    assert payload["tool_call"]["name"] == "get_now_playing"


def test_native_adapter_uses_typed_execute_capability():
    class Result:
        success = True
        status = "observed"
        message = "ok"
        data = {"title": "Song"}
        evidence = ("observer:get_now_playing",)
        error_kind = ""
        observer_reason = ""

    class FakeExecutor:
        def __init__(self):
            self.calls = []

        def execute_capability(self, capability_id, arguments):
            self.calls.append((capability_id, arguments))
            return Result()

    fake = FakeExecutor()
    adapter = NativeCapabilityToolAdapter(fake)
    out = adapter.registry(["get_now_playing"])["get_now_playing"]()
    assert fake.calls == [("get_now_playing", {})]
    assert out["success"] is True
    assert out["data"]["title"] == "Song"


def test_chat_router_routes_concrete_code_task_but_not_simple_desktop():
    from runtime.agent_harness.chat_router import classify_agent_chat_route

    code = classify_agent_chat_route("sửa lỗi capability_route trong executive_cortex.py rồi chạy test")
    assert code.action == "execute"

    desktop = classify_agent_chat_route("em biết tôi đang nghe nhạc gì không")
    assert desktop.action == "none"
    assert desktop.reason == "single_step_native_capability"


def test_chat_router_asks_when_improvement_has_material_branches():
    from runtime.agent_harness.chat_router import classify_agent_chat_route

    route = classify_agent_chat_route("làm agent ngon hơn đi")
    assert route.action == "clarify"
    assert "nhiều hướng" in route.clarification


def test_chat_router_can_execute_short_continuation_from_public_context():
    from runtime.agent_harness.chat_router import classify_agent_chat_route

    route = classify_agent_chat_route(
        "làm đi",
        previous_assistant_text=(
            "Tôi sẽ sửa capability routing trong executive_cortex.py, tận dụng CapabilityDiscovery rồi chạy test."
        ),
    )
    assert route.action == "execute"
    assert "Ngữ cảnh công khai" in route.goal


def test_runtime_coordinator_routes_concrete_chat_into_async_harness():
    from runtime.coordinator import RuntimeCoordinator

    calls = []

    class _Harness:
        def invoke(self, goal, thread_id="", task_id="", clarified_goal=""):
            calls.append((goal, thread_id, task_id, clarified_goal))
            return {"status": "done"}

    coordinator = object.__new__(RuntimeCoordinator)
    coordinator.agent_harness = _Harness()
    coordinator._agent_task_threads = {}
    coordinator._agent_last_started_task_id = ""
    coordinator.logger = None

    route = coordinator.route_agent_user_turn(
        "sửa capability_route trong executive_cortex.py rồi chạy test",
        turn_id="session_turn:test",
    )
    assert route["action"] == "execute"
    assert route["task_id"].startswith("task_")
    thread = coordinator._agent_task_threads.get(route["task_id"])
    if thread is not None:
        thread.join(timeout=2)
    assert calls
    assert calls[0][1] == "session_turn:test"


def test_native_adapter_keeps_dynamic_project_tools_mounted_before_workspace():
    class FakeExecutor:
        def execute_capability(self, capability_id, arguments):
            raise AssertionError("not executed")

    adapter = NativeCapabilityToolAdapter(FakeExecutor())
    specs = adapter.tool_specs()
    # project tools may be transiently unavailable until workspace activation,
    # but their schemas must remain mountable for progressive skill disclosure.
    assert "project_read_file" in specs
    assert "project_run_tests" in specs


def test_supervisor_policy_redirects_when_patch_happens_before_inspection(tmp_path):
    from runtime.coordinator import RuntimeCoordinator
    from runtime.agent_harness.task_store import AgentTaskStore
    from runtime.agent_harness.supervisor_bridge import AgentSupervisorBridge

    store = AgentTaskStore(tmp_path / "agent_tasks.json")
    task_id = store.create_task("fix router")["task_id"]
    store.append_event(task_id, "task_started", actor="system", summary="start")

    class _Events:
        def emit(self, task_id, event_type, **kwargs):
            return store.append_event(task_id, event_type, **kwargs)

    coordinator = object.__new__(RuntimeCoordinator)
    coordinator.agent_task_store = store
    coordinator.agent_supervisor = AgentSupervisorBridge(store=store, event_bus=_Events())
    coordinator._agent_supervisor_marks = {}

    event = store.append_event(
        task_id,
        "tool_started",
        actor="tool",
        summary="patch",
        data={"tool": "project_apply_patch"},
    )
    event["task_id"] = task_id
    coordinator._supervise_agent_event(event)

    snapshot = store.snapshot(selected_task_id=task_id)
    types = [item["type"] for item in snapshot["tasks"][0]["events"]]
    assert "supervisor_redirect" in types
    controls = store.consume_controls(task_id)
    assert controls and controls[-1]["action"] == "directive"
    assert controls[-1]["data"]["kind"] == "redirect"


def test_capability_self_model_distinguishes_instant_long_and_ambiguous():
    from runtime.agent_harness.capability_self_model import CapabilitySelfModel

    class _Discovery:
        capabilities = [
            {"name": "coding-debug", "description": "inspect debug test code", "keywords": ["debug", "code"]},
            {"name": "coding-modification", "description": "modify patch code", "keywords": ["patch", "refactor"]},
        ]

    class _Harness:
        discovery = _Discovery()

    model = CapabilitySelfModel(harness=_Harness(), harness_ready=True)

    instant = model.assess("em biết tôi đang nghe nhạc gì không")
    assert instant.lane == "instant"
    assert "get_now_playing" in instant.matched_capabilities

    long_task = model.assess("debug capability_route trong executive_cortex.py rồi sửa và chạy test")
    assert long_task.lane == "long_execution"
    assert long_task.executor == "official-api-coding/langgraph"
    assert long_task.can_modify is True

    ambiguous = model.assess("làm agent ngon hơn đi")
    assert ambiguous.lane == "clarify"
    assert "Chốt nhánh" in ambiguous.clarification


def test_chat_router_can_launch_inspect_only_consult_from_normal_chat():
    from runtime.agent_harness.capability_self_model import CapabilitySelfModel
    from runtime.agent_harness.chat_router import classify_agent_chat_route

    class _Discovery:
        capabilities = [
            {"name": "coding-inspection", "description": "inspect repository code", "keywords": ["code", "routing"]},
        ]

    class _Harness:
        discovery = _Discovery()

    route = classify_agent_chat_route(
        "soi kỹ xem routing trong executive_cortex.py có gì cấn không",
        self_model=CapabilitySelfModel(harness=_Harness(), harness_ready=True),
    )
    assert route.action == "consult"
    assert "INSPECT-ONLY" in route.goal


def test_inspect_mode_discloses_no_write_or_control_tools():
    import pytest
    pytest.importorskip("langchain_core")
    pytest.importorskip("langgraph")
    from runtime.agent_harness.graph_builder import AgentHarness

    harness = object.__new__(AgentHarness)

    class _Loader:
        def tools_for_skills(self, _skills):
            return ["project_read_file", "project_apply_patch", "project_run_tests"]

    harness.skill_loader = _Loader()
    harness.tool_specs = {
        "project_read_file": {"name": "project_read_file", "effect": "read", "input_schema": {}},
        "project_apply_patch": {"name": "project_apply_patch", "effect": "write", "input_schema": {}},
        "project_run_tests": {"name": "project_run_tests", "effect": "control", "input_schema": {}},
    }
    specs = harness._disclosed_tool_specs(["coding-inspection"], task_mode="inspect")
    names = {item["name"] for item in specs}
    assert "search_capabilities" in names
    assert "activate_skill" in names
    assert "project_read_file" in names
    assert "project_apply_patch" not in names
    assert "project_run_tests" not in names


def test_autopilot_hides_choice_tool_but_interactive_keeps_it():
    import pytest
    pytest.importorskip("langchain_core")
    pytest.importorskip("langgraph")
    from runtime.agent_harness.graph_builder import AgentHarness

    harness = object.__new__(AgentHarness)

    class _Loader:
        def tools_for_skills(self, _skills):
            return []

    harness.skill_loader = _Loader()
    harness.tool_specs = {}
    harness.ide_rpc = None

    auto_names = {item["name"] for item in harness._disclosed_tool_specs([], task_mode="auto", autopilot=True)}
    interactive_names = {item["name"] for item in harness._disclosed_tool_specs([], task_mode="auto", autopilot=False)}
    assert "ask_user_choice" not in auto_names
    assert "ask_user_choice" in interactive_names


def test_autopilot_interactive_profile_can_still_be_enabled_for_debugging():
    gate = PermissionGate(mode="interactive")
    assert gate.unattended is False
    # Unknown future tool remains explicitly gated in interactive debug mode.
    assert gate.check_permission("future_untyped_tool", {}) == Permission.ASK


def test_terminal_agent_event_wakes_eris_frontstage(tmp_path):
    from runtime.coordinator import RuntimeCoordinator
    from runtime.agent_harness.task_store import AgentTaskStore
    from runtime.agent_harness.supervisor_bridge import AgentSupervisorBridge

    store = AgentTaskStore(tmp_path / "agent_tasks.json")
    task_id = store.create_task("fix routing")['task_id']
    store.update_task(task_id, clarified_goal="fix routing")

    class _Events:
        def emit(self, task_id, event_type, **kwargs):
            return store.append_event(task_id, event_type, **kwargs)

    coordinator = object.__new__(RuntimeCoordinator)
    coordinator.agent_task_store = store
    coordinator.agent_supervisor = AgentSupervisorBridge(store=store, event_bus=_Events())
    coordinator._agent_supervisor_marks = {}
    coordinator.logger = None
    wakes = []
    coordinator._wake_autonomous_loop = lambda reason="": wakes.append(reason)

    event = store.append_event(
        task_id,
        "task_completed",
        actor="executor",
        summary="done",
        detail="Fixed routing and tests pass.",
        data={"final": "Fixed routing and tests pass."},
    )
    event["task_id"] = task_id
    coordinator._supervise_agent_event(event)

    pending = coordinator._pending_conversation_capability_events
    assert len(pending) == 1
    queued = pending[0]
    assert queued["kind"] == "work_result"
    assert queued["metadata"]["work_capability"]["status"] == "completed"
    assert "Fixed routing" in queued["metadata"]["work_capability"]["summary"]
    assert wakes and wakes[-1].startswith("agent_task_completed:")


def test_workspace_manager_persists_favorites_and_scoped_task_binding(tmp_path):
    from runtime.workspace_manager import WorkspaceManager

    eris_home = tmp_path / "ErisLive"
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()

    class _Owner:
        runtime_settings = {"project_ops": {"eris_home": str(eris_home)}}

    manager = WorkspaceManager(_Owner())
    a = manager.join(str(alpha), activate=True)
    b = manager.join(str(beta), activate=True)
    manager.set_favorite(a.workspace_id, True)

    snapshot = manager.workspace_snapshot()
    assert snapshot["active_workspace_id"] == b.workspace_id
    assert snapshot["workspaces"][0]["workspace_id"] == a.workspace_id
    assert snapshot["workspaces"][0]["favorite"] is True

    with manager.scoped_active(a.workspace_id):
        assert manager.active_workspace_id == a.workspace_id
        assert manager.active_path() == alpha.resolve()
    # Tool-scoped activation must not steal the workspace selected in the panel.
    assert manager.active_workspace_id == b.workspace_id

    reloaded = WorkspaceManager(_Owner())
    assert reloaded.active_workspace_id == b.workspace_id
    assert a.workspace_id in reloaded.favorite_workspace_ids


def test_gemini_executor_accepts_task_locked_workspace_without_mutating_default(tmp_path, monkeypatch):
    import json
    import subprocess
    from runtime.agent_harness.gemini_cli_executor import GeminiCliDecisionExecutor

    default_workspace = tmp_path / "default"
    task_workspace = tmp_path / "task"
    default_workspace.mkdir()
    task_workspace.mkdir()
    seen = {}

    executor = GeminiCliDecisionExecutor(workspace=default_workspace, executable="gemini")
    monkeypatch.setattr(executor, "health", lambda: (True, "gemini"))

    def _run(cmd, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"response": json.dumps({"action": "final", "final": "done"})}),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _run)
    import pytest
    pytest.importorskip("langchain_core")
    response = executor.invoke_with_tools([], tools=[], goal="inspect", workspace=task_workspace)
    assert response.content == "done"
    assert seen["cwd"] == str(task_workspace.resolve())
    assert executor.workspace == default_workspace.resolve()


def test_coordinator_binds_new_agent_task_to_selected_workspace(tmp_path):
    from runtime.coordinator import RuntimeCoordinator
    from runtime.workspace_manager import WorkspaceManager

    eris_home = tmp_path / "ErisLive"
    project = tmp_path / "repo"
    project.mkdir()
    seen = []

    class _Settings:
        runtime_settings = {"project_ops": {"eris_home": str(eris_home)}}

    class _Harness:
        executor_name = "gemini-cli"
        model_name = "test"

        def invoke(
            self,
            goal,
            thread_id="",
            task_id="",
            clarified_goal="",
            task_mode="execute",
            source="eris_chat",
            workspace_id="",
            workspace_path="",
            workspace_name="",
        ):
            seen.append({
                "goal": goal,
                "workspace_id": workspace_id,
                "workspace_path": workspace_path,
                "workspace_name": workspace_name,
            })
            return {"status": "done"}

    coordinator = object.__new__(RuntimeCoordinator)
    coordinator.agent_harness = _Harness()
    coordinator.agent_task_store = None
    coordinator.agent_supervisor = None
    coordinator.workspace_manager = WorkspaceManager(_Settings())
    record = coordinator.workspace_manager.join(str(project), activate=True)
    coordinator.agent_default_workspace = ""
    coordinator._agent_task_threads = {}
    coordinator._agent_last_started_task_id = ""
    coordinator.logger = None

    started = coordinator.start_agent_task_async("fix repo", workspace_ref=record.workspace_id)
    assert started["workspace_id"] == record.workspace_id
    thread = coordinator._agent_task_threads.get(started["task_id"])
    if thread is not None:
        thread.join(timeout=2)
    assert seen and seen[0]["workspace_path"] == str(project.resolve())


def test_workspace_checkpoint_store_restores_git_worktree_index_and_projection(tmp_path):
    import subprocess
    from runtime.agent_harness.workspace_checkpoint_store import WorkspaceCheckpointStore

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "eris@test.local"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Eris Test"], cwd=repo, check=True)
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)

    # Snapshot contains both staged and unstaged state plus an untracked file.
    (repo / "a.txt").write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    (repo / "a.txt").write_text("unstaged\n", encoding="utf-8")
    (repo / "note.txt").write_text("checkpoint\n", encoding="utf-8")

    store = WorkspaceCheckpointStore(tmp_path / "checkpoints")
    record = store.capture(
        task_id="task_x",
        checkpoint_id="cp_x",
        thread_id="thread_x",
        workspace_path=str(repo),
        step=4,
        reason="before:project_apply_patch",
        task_projection={"current_step": 4, "changed_files": ["a.txt"]},
    )
    assert record.snapshot_mode == "git"
    assert record.complete is True

    # Move the repository into a materially different future state.
    (repo / "a.txt").write_text("future\n", encoding="utf-8")
    (repo / "note.txt").write_text("future-note\n", encoding="utf-8")
    (repo / "future.txt").write_text("delete-me\n", encoding="utf-8")

    restored = store.restore(task_id="task_x", checkpoint_id="cp_x", workspace_path=str(repo))
    assert restored.checkpoint_id == "cp_x"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "unstaged\n"
    assert (repo / "note.txt").read_text(encoding="utf-8") == "checkpoint\n"
    assert not (repo / "future.txt").exists()
    staged = subprocess.run(["git", "diff", "--cached", "--", "a.txt"], cwd=repo, capture_output=True, text=True, check=True).stdout
    unstaged = subprocess.run(["git", "diff", "--", "a.txt"], cwd=repo, capture_output=True, text=True, check=True).stdout
    assert "+staged" in staged
    assert "+unstaged" in unstaged
    assert store.load_projection(restored)["current_step"] == 4


def test_task_store_restores_execution_projection_without_erasing_audit(tmp_path):
    from runtime.agent_harness.task_store import AgentTaskStore

    store = AgentTaskStore(tmp_path / "tasks.json")
    task_id = store.create_task("fix repo")["task_id"]
    store.update_task(
        task_id,
        current_step=3,
        active_skills=["coding-debug"],
        changed_files=["a.py"],
        tests={"passed": 2, "failed": 0, "summary": "ok"},
    )
    projection = store.execution_projection(task_id)
    store.append_event(task_id, "file_changed", data={"path": "b.py"})
    store.update_task(task_id, current_step=9, tests={"passed": 0, "failed": 1, "summary": "fail"})
    before_events = len(store.get_task(task_id, event_limit=100)["events"])

    store.restore_execution_projection(task_id, projection, checkpoint_id="cp3")
    task = store.get_task(task_id, event_limit=100)
    assert task["current_step"] == 3
    assert task["changed_files"] == ["a.py"]
    assert task["tests"]["passed"] == 2
    assert task["checkpoint_id"] == "cp3"
    assert len(task["events"]) == before_events


def test_capability_self_model_exposes_operational_profiles_and_skill_health():
    from runtime.agent_harness.capability_self_model import CapabilitySelfModel

    class _Discovery:
        capabilities = [
            {
                "name": "coding-verification",
                "description": "verify test diff",
                "keywords": ["verify"],
                "tools": ["project_run_tests", "project_git_diff"],
                "mode": "long",
                "latency": "seconds to minutes",
                "workspace_required": True,
                "risk_level": "medium",
            }
        ]

    class _Harness:
        discovery = _Discovery()
        tool_specs = {
            "project_run_tests": {"name": "project_run_tests"},
            "project_git_diff": {"name": "project_git_diff"},
            "inspect_capability_catalog": {"name": "inspect_capability_catalog"},
        }

    model = CapabilitySelfModel(harness=_Harness(), harness_ready=True)
    profile = model.profile("project_run_tests")
    assert profile is not None
    assert profile.workspace_required is True
    assert profile.executor == "eris-native"
    assert profile.latency_class == "medium"
    catalog = model.catalog("project", include_unavailable=True)
    assert catalog["harness_ready"] is True
    assert any(item["capability_id"] == "project_run_tests" for item in catalog["capabilities"])
    skills = model.skill_profiles()
    assert skills and skills[0]["executor"] == "gemini/langgraph"


def test_new_depth_skills_are_progressive_and_reveal_real_tools():
    discovery = CapabilityDiscovery(str(ROOT / "powers"))
    search = discovery.search("capability health latency")
    assert "agent-self-awareness" in search
    assert "inspect_capability_catalog" not in search
    activated = SkillLoader(discovery).activate_skill("agent-self-awareness")
    assert "inspect_capability_catalog" in activated
    assert "inspect_capability_health" in activated

    recovery = SkillLoader(discovery).activate_skill("safe-recovery")
    assert "list_task_checkpoints" in recovery
    assert "git reset --hard" in recovery

from __future__ import annotations

import inspect

from actions.capability_registry import capability_definition, capability_input_schema
from llm.process_manager import RemoteModelProcessManager
from llm.service import LlmService
from runtime.agent_harness.native_tool_adapter import NativeCapabilityToolAdapter
from runtime.agent_harness.official_api_executor import OfficialApiDecisionExecutor
from runtime.agent_harness.coding_team import (
    TOPOLOGIES, ROLE_EXPLORER, ROLE_PLANNER, ROLE_CODER, ROLE_REVIEWER,
    topology_for_task, role_tool_specs, next_role,
)
from runtime.coordinator import RuntimeCoordinator
from runtime.provider_action_runtime import ProviderActionRuntimeMixin
from runtime.project_operations import ProjectOperationsRuntime


class _Logger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


class _Owner:
    pass


def test_coding_lane_is_explicit_and_does_not_borrow_escalation() -> None:
    companion = object()
    escalation = object()
    coding = object()
    service = LlmService(companion, escalation_client=escalation, coding_client=coding)
    owner = _Owner()
    owner.llm_service = service
    runtime = ProjectOperationsRuntime(owner)

    assert service.coding_client is coding
    assert runtime._coding_client() is coding

    service_without_coding = LlmService(companion, escalation_client=escalation, fallback_client=object())
    owner.llm_service = service_without_coding
    runtime = ProjectOperationsRuntime(owner)
    assert runtime._coding_client() is None


def test_process_manager_builds_dedicated_remote_coding_client(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_CODING_ENABLED", "1")
    monkeypatch.setenv("ERIS_CODING_PROVIDER", "deepseek")
    monkeypatch.setenv("ERIS_CODING_API_KEY", "sk-test-coding-owner")
    monkeypatch.setenv("ERIS_CODING_MODEL", "deepseek-v4-pro")
    monkeypatch.delenv("ERIS_CODING_BASE_URL", raising=False)

    manager = RemoteModelProcessManager(logger=_Logger(), project_root=".")
    client = manager.build_coding_client()

    assert client is not None
    assert "api.deepseek.com" in client.base_url
    assert client.model_name


def test_agent_tool_spine_exposes_semantic_search_but_hides_legacy_one_shot() -> None:
    adapter = NativeCapabilityToolAdapter(object())
    ids = set(adapter.agent_ids())

    assert "project_semantic_search" in ids
    assert "project_execute_task" not in ids
    assert capability_definition("project_semantic_search") is not None
    schema = capability_input_schema("project_semantic_search")
    assert "query" in schema["properties"]


def test_coordinator_routes_whole_goal_to_unified_team_harness() -> None:
    source = inspect.getsource(RuntimeCoordinator.start_agent_task_async)
    assert "ClineBridgeClient" not in source
    assert "run_task_blocking" not in source
    assert "self.execute_agent_task" in source


def test_official_executor_has_stable_json_decision_protocol() -> None:
    class Client:
        model_name = "coding-test"
        base_url = "https://api.example.com/v1"

        def create_chat_completion(self, **_kwargs):
            raise AssertionError("not called")

    executor = OfficialApiDecisionExecutor(Client())
    payload = executor.parse_decision('{"action":"tool","tool_calls":[{"name":"project_read_file","args":{"path":"a.py"}}]}')
    assert payload["action"] == "tool"
    assert executor.health()[0] is True


def test_legacy_one_shot_is_compatibility_only_and_suppressed() -> None:
    legacy = capability_definition("project_execute_task")
    assert legacy is not None
    assert legacy.callable is False
    assert legacy.exposure == "compatibility"
    source = inspect.getsource(ProviderActionRuntimeMixin._execute_system_action)
    assert "project_execute_task" in source
    assert "retired_action" in source


def test_feature_builder_team_has_four_specialists() -> None:
    topology = TOPOLOGIES["engineering_team"]
    assert topology.roles == (ROLE_EXPLORER, ROLE_PLANNER, ROLE_CODER, ROLE_REVIEWER)
    assert next_role(topology, ROLE_EXPLORER) == ROLE_PLANNER
    assert next_role(topology, ROLE_PLANNER) == ROLE_CODER
    assert next_role(topology, ROLE_CODER) == ROLE_REVIEWER
    assert next_role(topology, ROLE_REVIEWER) == ""


def test_plan_and_inspect_modes_select_read_only_team_shapes() -> None:
    assert topology_for_task("plan").roles == (ROLE_EXPLORER, ROLE_PLANNER)
    assert topology_for_task("inspect").roles == (ROLE_EXPLORER, ROLE_REVIEWER)


def test_specialist_tool_boundaries_are_enforced() -> None:
    tools = [
        {"name": "project_read_file", "effect": "read"},
        {"name": "project_apply_patch", "effect": "write"},
        {"name": "project_run_tests", "effect": "control"},
        {"name": "project_run_command", "effect": "control"},
        {"name": "ask_user_choice", "effect": "control"},
    ]
    explorer = {item["name"] for item in role_tool_specs(ROLE_EXPLORER, tools)}
    planner = {item["name"] for item in role_tool_specs(ROLE_PLANNER, tools)}
    coder = {item["name"] for item in role_tool_specs(ROLE_CODER, tools)}
    reviewer = {item["name"] for item in role_tool_specs(ROLE_REVIEWER, tools)}
    assert explorer == {"project_read_file"}
    assert planner == {"project_read_file", "ask_user_choice"}
    assert "project_apply_patch" in coder and "project_run_command" in coder
    assert reviewer == {"project_read_file", "project_run_tests"}


def test_task_model_snapshot_does_not_mutate_shared_coding_client() -> None:
    class Client:
        model_name = "coding-default"
        configured_model_name = "coding-default"
        base_url = "https://api.example.com/v1"

        def create_chat_completion(self, **_kwargs):
            raise AssertionError("not called")

    client = Client()
    executor = OfficialApiDecisionExecutor(client)
    task_client = executor._client_for_model("coding-task-b")

    assert task_client is not client
    assert task_client.model_name == "coding-task-b"
    assert client.model_name == "coding-default"
    assert executor.model == "coding-default"


def test_specialist_context_is_scoped_after_role_handoff_marker() -> None:
    class Client:
        model_name = "coding-default"
        base_url = "https://api.example.com/v1"

        def create_chat_completion(self, **_kwargs):
            raise AssertionError("not called")

    class Msg:
        def __init__(self, content: str, kind: str = "human") -> None:
            self.content = content
            self.type = kind

    executor = OfficialApiDecisionExecutor(Client())
    prompt = executor._build_prompt(
        [
            Msg("old explorer chatter that reviewer should not inherit"),
            Msg("[TEAM ROLE START:REVIEWER]\nCoder handoff: changed auth.py"),
            Msg("git diff evidence", "tool"),
        ],
        tools=[],
        goal="review auth change",
        active_skills=[],
        workspace=None,
        role="reviewer",
        role_context="Reviewer contract",
    )
    assert "old explorer chatter" not in prompt
    assert "Coder handoff: changed auth.py" in prompt
    assert "git diff evidence" in prompt


def test_task_store_projects_coding_team_handoffs(tmp_path) -> None:
    from runtime.agent_harness.task_store import AgentTaskStore

    store = AgentTaskStore(tmp_path / "tasks.json")
    task = store.create_task("fix bug", metadata={"team_topology": "engineering_team"})
    task_id = task["task_id"]
    store.append_event(
        task_id,
        "coding_team_selected",
        data={"topology": "engineering_team", "roles": ["explorer", "planner", "coder", "reviewer"]},
    )
    store.append_event(
        task_id,
        "team_handoff",
        actor="explorer",
        data={"from": "explorer", "to": "planner", "artifact": "auth.py and tests are relevant", "review_round": 0},
    )
    projected = store.get_task(task_id, event_limit=0)
    assert projected is not None
    assert projected["coding_team"]["topology"] == "engineering_team"
    assert projected["coding_team"]["role"] == "planner"
    assert projected["coding_team"]["artifacts"]["explorer"] == "auth.py and tests are relevant"


def test_explorer_senses_are_first_class_read_only_capabilities() -> None:
    from runtime.agent_harness.coding_team import role_default_skills

    adapter = NativeCapabilityToolAdapter(object())
    ids = set(adapter.agent_ids())
    for capability_id in ("project_git_log", "project_dependency_summary", "project_test_inventory"):
        assert capability_id in ids
        cap = capability_definition(capability_id)
        assert cap is not None and cap.effect == "read" and cap.callable is True
    assert role_default_skills(ROLE_EXPLORER) == ("coding-inspection",)
    assert "coding-modification" not in role_default_skills(ROLE_REVIEWER)


def test_explorer_dependency_and_test_inventory_are_grounded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ERIS_HOME", str(tmp_path / "eris-home"))
    repo = tmp_path / "sample-repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "src").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname="sample"\ndependencies=["httpx>=0.27", "pydantic>=2"]\n\n[project.optional-dependencies]\ntest=["pytest>=8"]\n',
        encoding="utf-8",
    )
    (repo / "tests" / "test_core.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (repo / "src" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")

    owner = _Owner()
    runtime = ProjectOperationsRuntime(owner)
    runtime.workspace_manager.register_local(repo, activate=True)

    deps = runtime.execute("project_dependency_summary", '{"limit": 20}')
    tests = runtime.execute("project_test_inventory", '{"limit": 20}')

    assert deps.success is True
    assert {"httpx", "pydantic", "pytest"}.issubset(set(deps.data["dependencies"]))
    assert tests.success is True
    assert "tests/test_core.py" in tests.data["test_files"]
    assert "pytest" in tests.data["frameworks"]


def test_explorer_git_log_reads_recent_repository_intent(tmp_path, monkeypatch) -> None:
    import shutil
    import subprocess
    import pytest

    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    monkeypatch.setenv("ERIS_HOME", str(tmp_path / "eris-home"))
    repo = tmp_path / "git-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "eris@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Eris Test"], check=True)
    (repo / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "core.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "establish core behavior"], check=True)

    owner = _Owner()
    runtime = ProjectOperationsRuntime(owner)
    runtime.workspace_manager.register_local(repo, activate=True)
    result = runtime.execute("project_git_log", '{"limit": 5}')

    assert result.success is True
    assert result.data["count"] == 1
    assert result.data["commits"][0]["subject"] == "establish core behavior"

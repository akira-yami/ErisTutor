"""LangGraph state for the Unified Agent Harness.

Only task-execution state lives here.  Eris' identity, affect and long-lived
social state stay outside this graph and can supervise it through directives.
"""
from typing import Any, TypedDict, Annotated, List, Optional
from langchain_core.messages import BaseMessage


def add_messages(left: List[BaseMessage], right: List[BaseMessage]) -> List[BaseMessage]:
    """Reducer that preserves LangGraph message IDs/deduplication semantics."""
    from langgraph.graph.message import add_messages as builtin_add_messages
    return builtin_add_messages(left, right)


class AgentState(TypedDict, total=False):
    """Checkpointed state passed between harness nodes."""

    messages: Annotated[List[BaseMessage], add_messages]
    goal: str
    task_id: str
    thread_id: str
    session_id: str
    executor: str
    model: str
    task_mode: str
    autopilot: bool
    source: str
    workspace_id: str
    workspace_path: str
    workspace_name: str
    ide_context: dict[str, Any]
    # RepoContext được index toàn workspace trước khi agent bắt đầu quyết định.
    # State chỉ giữ context pack đã nén + metadata task-near, không nhét toàn bộ source vào checkpoint.
    repository_context: dict[str, Any]
    context_ready: bool
    resolved_targets: List[dict[str, Any]]
    context_confidence: float
    inspection_evidence: List[dict[str, Any]]
    # Role-specialized coding team state (GitPilot-inspired topology).
    team_topology: str
    team_role: str
    team_artifacts: dict[str, Any]
    team_handoff_pending: bool
    review_rounds: int
    completion_gate_retry: bool
    completion_gate_attempts: int
    status: str
    active_skills: List[str]
    active_tools: List[str]
    plan: Optional[str]
    injected_context: str
    pending_directives: List[str]
    rollback_requested: str
    step_count: int

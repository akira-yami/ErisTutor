# -*- coding: utf-8 -*-
"""Role-specialized coding team policy for Eris.

Uses the Explorer → Planner → Coder → Reviewer topology.  Eris keeps the
implementation native so there is still one checkpoint/tool/permission spine and
one official-API credential lane; roles are separate model contexts, not
competing runtimes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable


ROLE_EXPLORER = "explorer"
ROLE_PLANNER = "planner"
ROLE_CODER = "coder"
ROLE_REVIEWER = "reviewer"


@dataclass(frozen=True)
class CodingTopology:
    id: str
    label: str
    roles: tuple[str, ...]
    read_only: bool = False


TOPOLOGIES: dict[str, CodingTopology] = {
    "engineering_team": CodingTopology(
        id="engineering_team",
        label="Engineering Team",
        roles=(ROLE_EXPLORER, ROLE_PLANNER, ROLE_CODER, ROLE_REVIEWER),
    ),
    "architect": CodingTopology(
        id="architect",
        label="Architect",
        roles=(ROLE_EXPLORER, ROLE_PLANNER),
        read_only=True,
    ),
    "code_inspector": CodingTopology(
        id="code_inspector",
        label="Code Inspector",
        roles=(ROLE_EXPLORER, ROLE_REVIEWER),
        read_only=True,
    ),
}


def topology_for_task(task_mode: str) -> CodingTopology:
    mode = str(task_mode or "execute").strip().lower()
    if mode == "plan":
        return TOPOLOGIES["architect"]
    if mode == "inspect":
        return TOPOLOGIES["code_inspector"]
    return TOPOLOGIES["engineering_team"]


def normalize_topology(value: str | CodingTopology | None, task_mode: str = "execute") -> CodingTopology:
    if isinstance(value, CodingTopology):
        return value
    key = str(value or "").strip().lower()
    return TOPOLOGIES.get(key) or topology_for_task(task_mode)


def first_role(topology: CodingTopology) -> str:
    return topology.roles[0] if topology.roles else ROLE_CODER


def next_role(topology: CodingTopology, role: str) -> str:
    current = str(role or "").strip().lower()
    try:
        index = topology.roles.index(current)
    except ValueError:
        return first_role(topology)
    return topology.roles[index + 1] if index + 1 < len(topology.roles) else ""


def role_model(role: str, default_model: str) -> str:
    env_key = f"ERIS_CODING_{str(role or '').strip().upper()}_MODEL"
    configured = str(os.environ.get(env_key, "") or "").strip()
    return configured or str(default_model or "").strip()


def role_default_skills(role: str) -> tuple[str, ...]:
    """Skills mounted automatically for the current specialist only.

    These are not persisted as global task skills, so a Reviewer does not inherit
    Coder's mutation surface merely because the task previously edited files.
    """
    current = str(role or ROLE_CODER).strip().lower()
    if current == ROLE_EXPLORER:
        return ("coding-inspection",)
    if current == ROLE_PLANNER:
        return ("coding-inspection",)
    if current == ROLE_CODER:
        return ("coding-inspection", "coding-modification")
    if current == ROLE_REVIEWER:
        return ("coding-inspection", "coding-verification")
    return ()


def role_reasoning_effort(role: str) -> str:
    key = str(role or "").strip().lower()
    configured = str(os.environ.get(f"ERIS_CODING_{key.upper()}_REASONING", "") or "").strip().lower()
    if configured in {"low", "medium", "high"}:
        return configured
    return "medium" if key == ROLE_EXPLORER else "high"


def max_review_rounds() -> int:
    try:
        return max(1, min(int(os.environ.get("ERIS_CODING_MAX_REVIEW_ROUNDS", "3") or 3), 6))
    except (TypeError, ValueError):
        return 3


def role_tool_specs(role: str, tools: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Restrict each specialist to the smallest capability surface it needs."""
    current = str(role or ROLE_CODER).strip().lower()
    result: list[dict[str, Any]] = []
    always = {"search_capabilities", "activate_skill", "inspect_capability_catalog"}
    reviewer_controls = {"project_run_tests", "ide_get_problems", "editor_open_diff", "editor_focus_problems", "editor_show_source_control"}
    planner_controls = {"ask_user_choice"}

    for item in tools:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        effect = str(item.get("effect", "inspect") or "inspect").strip().lower()
        if current == ROLE_CODER:
            result.append(item)
            continue
        if name in always or effect in {"read", "inspect", "observe"}:
            result.append(item)
            continue
        if current == ROLE_REVIEWER and name in reviewer_controls:
            result.append(item)
            continue
        if current == ROLE_PLANNER and name in planner_controls:
            result.append(item)
            continue
    return result


def role_instructions(
    role: str,
    *,
    topology: CodingTopology,
    autopilot: bool,
    artifacts: dict[str, Any] | None = None,
    review_rounds: int = 0,
) -> str:
    current = str(role or ROLE_CODER).strip().lower()
    next_name = next_role(topology, current)
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    shared = (
        f"You are the {current.upper()} specialist in Eris' coding team. "
        f"Topology={topology.id}; next specialist={next_name or 'none'}. "
        "Stay inside your role. Do not impersonate or silently perform another specialist's job. "
        "All claims must be grounded in repository/tool evidence."
    )

    if current == ROLE_EXPLORER:
        return shared + (
            "\nYour job: establish reality before implementation. Inspect repository structure, relevant source, callers, tests, git state, dependencies and conventions. "
            "Use semantic search for conceptual discovery when useful, then verify exact source. Never write files or run mutating commands. "
            "When exploration is sufficient, return a concise handoff artifact: relevant files/symbols, observed behavior, test surface, dependencies, constraints, and unresolved risks."
        )
    if current == ROLE_PLANNER:
        approval = (
            " Because this task is interactive, present the concrete plan and use ask_user_choice only when approval or a genuine product/architecture choice is required before coding."
            if not autopilot and next_name == ROLE_CODER else
            " In Autopilot, choose safe reversible defaults and proceed without ceremonial confirmation."
        )
        return shared + (
            "\nYour job: convert Explorer evidence into a safe implementation plan before any source edit. Name exact files/symbols, ordered steps, expected diff shape, verification commands/tests, rollback points, and material risks. "
            "Do not edit files yourself." + approval
        )
    if current == ROLE_CODER:
        return shared + (
            "\nYour job: execute the approved/grounded plan with minimal coherent edits. Deep-read any implementation you touch, apply transactional patches, run targeted tests, self-correct failures, inspect diagnostics and git diff, and leave the workspace in a verified state. "
            "Do not declare the task complete merely because code was written; produce an implementation handoff for Reviewer with changed files, tests run, failures fixed, and any residual uncertainty."
        )
    if current == ROLE_REVIEWER:
        return shared + (
            f"\nYour job: independently audit the resulting diff and verification evidence. Review correctness, regression risk, security, maintainability, performance, and test coverage. You are read-only except that you may run verification tests. Review round={review_rounds}. "
            "If material issues remain, use action=handoff with target=coder and provide precise ranked findings. Do not patch the code yourself. If the change is acceptable, return action=final with a concise verdict, evidence, and any non-blocking suggestions."
        )
    return shared


def team_context_text(artifacts: dict[str, Any] | None, *, review_rounds: int = 0) -> str:
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    chunks: list[str] = []
    for role in (ROLE_EXPLORER, ROLE_PLANNER, ROLE_CODER, ROLE_REVIEWER):
        value = artifacts.get(role)
        if value:
            chunks.append(f"[{role.upper()} HANDOFF]\n{str(value)[:16000]}")
    chunks.append(f"[REVIEW ROUNDS]\n{int(review_rounds or 0)}")
    return "\n\n".join(chunks)


__all__ = [
    "CodingTopology",
    "TOPOLOGIES",
    "ROLE_EXPLORER",
    "ROLE_PLANNER",
    "ROLE_CODER",
    "ROLE_REVIEWER",
    "topology_for_task",
    "normalize_topology",
    "first_role",
    "next_role",
    "role_model",
    "role_default_skills",
    "role_reasoning_effort",
    "max_review_rounds",
    "role_tool_specs",
    "role_instructions",
    "team_context_text",
]

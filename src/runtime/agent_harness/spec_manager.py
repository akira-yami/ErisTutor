"""Eris Spec Manager

Spec-driven development tuong tu Kiro:
- "plan: <feature>" -> coding Planner tao spec.md (requirements, design, tasks)
- "exec spec: <name>" -> Eris execute tung task trong spec tuan tu

Spec file format (.eris/specs/<name>.md):
    # Spec: <Ten feature>

    ## Requirements
    - ...

    ## Design
    - ...

    ## Tasks
    - [ ] Task 1
    - [ ] Task 2
    - [x] Task 3 (da xong)

Usage:
    intent = detect_spec_intent("plan: add OAuth login")
    # -> ("plan", "add OAuth login")

    intent = detect_spec_intent("exec spec: oauth-login")
    # -> ("exec", "oauth-login")

    intent = detect_spec_intent("fix the bug in auth.py")
    # -> (None, "")
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

EventCallback = Callable[[dict[str, Any]], None]

_SPECS_DIR = ".eris/specs"

# Regex detect intent
_PLAN_RE = re.compile(
    r"^(?:plan|spec|design|phan tich|lap ke hoach|tao spec)[:\s]+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_EXEC_RE = re.compile(
    r"^(?:exec|execute|run|thuc thi|chay)\s+spec[:\s]+([a-z0-9_\-]+)",
    re.IGNORECASE,
)
_LIST_RE = re.compile(
    r"^(?:list|show)\s+specs?$",
    re.IGNORECASE,
)


def detect_spec_intent(goal: str) -> tuple[str | None, str]:
    """Phan tich goal de xem co phai spec command khong.

    Returns:
        ("plan", description)  — tao spec moi
        ("exec", spec_name)    — execute spec co san
        ("list", "")           — list tat ca specs
        (None, "")             — khong phai spec command
    """
    goal = goal.strip()

    m = _PLAN_RE.match(goal)
    if m:
        return ("plan", m.group(1).strip())

    m = _EXEC_RE.match(goal)
    if m:
        return ("exec", m.group(1).strip().lower().replace(" ", "-"))

    if _LIST_RE.match(goal):
        return ("list", "")

    return (None, "")


def list_specs(workspace_path: str | Path) -> list[dict[str, Any]]:
    """Lay danh sach tat ca spec files trong workspace.

    Returns list of {"name": str, "path": str, "tasks_total": int, "tasks_done": int}
    """
    specs_dir = Path(workspace_path) / _SPECS_DIR
    if not specs_dir.exists():
        return []

    result = []
    for md_file in sorted(specs_dir.glob("*.md")):
        content = _read_safe(md_file)
        tasks_total, tasks_done = _count_tasks(content)
        result.append({
            "name": md_file.stem,
            "path": str(md_file),
            "tasks_total": tasks_total,
            "tasks_done": tasks_done,
        })
    return result


def create_spec(
    description: str,
    workspace_path: str | Path,
    runner: Any,
    *,
    on_event: EventCallback | None = None,
) -> dict[str, Any]:
    """Dung coding-team runner de tao spec file tu description.

    Args:
        description: Mo ta feature can lam
        workspace_path: Path den workspace
        runner: object co ``run_task_blocking`` (AgentHarness adapter/team runner)
        on_event: Callback nhan events realtime

    Returns:
        {"status": "completed"|"failed", "spec_name": str, "spec_path": str}
    """
    specs_dir = Path(workspace_path) / _SPECS_DIR
    specs_dir.mkdir(parents=True, exist_ok=True)

    # Chuyen description -> ten file hop le
    spec_name = _slugify(description)
    spec_path = specs_dir / f"{spec_name}.md"

    # Planner contract tao spec file
    goal = f"""Create a spec file for the following feature: {description}

Write the spec to: {spec_path}

Use EXACTLY this format:
```markdown
# Spec: {description}

## Requirements
- List each user-facing requirement as a bullet point
- Be specific and testable
- Include edge cases

## Design
- Technical approach and architecture decisions
- Key components/modules to create or modify
- Data flow and API design if relevant

## Tasks
- [ ] Task 1 (specific, actionable implementation step)
- [ ] Task 2
- [ ] Task 3
(list 5-15 concrete tasks in implementation order)
```

Write ONLY the spec file. Do not implement anything yet."""

    task_id = f"spec_plan_{int(time.time())}"

    result = runner.run_task_blocking(
        task_id=task_id,
        goal=goal,
        workspace_path=str(workspace_path),
        on_event=on_event,
        timeout=120.0,
    )

    status = str(result.get("status", "failed") or "failed")
    return {
        "status": status,
        "spec_name": spec_name,
        "spec_path": str(spec_path),
        "bridge_result": result,
    }


def execute_spec(
    spec_name: str,
    workspace_path: str | Path,
    runner: Any,
    *,
    on_event: EventCallback | None = None,
    steering_context: str = "",
    model: str | None = None,
) -> dict[str, Any]:
    """Execute tung task chua hoan thanh trong spec file.

    Sau moi task, cap nhat [x] trong spec file.

    Returns:
        {"status": str, "tasks_done": int, "tasks_skipped": int, "tasks_failed": int}
    """
    specs_dir = Path(workspace_path) / _SPECS_DIR

    # Tim spec file (co the co hoac khong co .md)
    spec_path = specs_dir / f"{spec_name}.md"
    if not spec_path.exists():
        spec_path = specs_dir / spec_name
        if not spec_path.exists():
            return {"status": "not_found", "reason": f"spec '{spec_name}' not found in {specs_dir}"}

    content = _read_safe(spec_path)
    tasks = _parse_tasks(content)

    if not tasks:
        return {"status": "no_tasks", "reason": "No uncompleted tasks found in spec"}

    pending = [(i, t) for i, t in tasks if not t["done"]]
    if not pending:
        return {"status": "all_done", "tasks_done": len(tasks), "tasks_skipped": 0, "tasks_failed": 0}

    logger.info("[spec] Executing '%s': %d pending tasks", spec_name, len(pending))

    done_count = 0
    failed_count = 0
    spec_header = _get_spec_header(content)

    for idx, (line_idx, task) in enumerate(pending):
        task_text = task["text"]
        task_id = f"spec_exec_{spec_name}_{idx}_{int(time.time())}"

        logger.info("[spec] Task %d/%d: %s", idx + 1, len(pending), task_text)

        if on_event:
            on_event({
                "type": "spec.task_start",
                "taskId": task_id,
                "eventId": f"spec_start_{idx}",
                "payload": {
                    "spec_name": spec_name,
                    "task_index": idx + 1,
                    "task_total": len(pending),
                    "task_text": task_text,
                },
                "timestamp": _now_iso(),
            })

        goal = f"""Execute this task from spec '{spec_name}':

TASK: {task_text}

Spec context:
{spec_header}

Complete ONLY this specific task. Verify it works before finishing."""

        result = runner.run_task_blocking(
            task_id=task_id,
            goal=goal,
            workspace_path=str(workspace_path),
            steering_context=steering_context,
            model=model,
            on_event=on_event,
            timeout=600.0,
        )

        task_status = str(result.get("status", "failed") or "failed")

        if task_status == "completed":
            # Cap nhat [x] trong spec file
            content = _mark_task_done(content, task_text)
            _write_safe(spec_path, content)
            done_count += 1

            if on_event:
                on_event({
                    "type": "spec.task_done",
                    "taskId": task_id,
                    "eventId": f"spec_done_{idx}",
                    "payload": {
                        "spec_name": spec_name,
                        "task_index": idx + 1,
                        "task_text": task_text,
                        "status": "completed",
                    },
                    "timestamp": _now_iso(),
                })
        else:
            failed_count += 1
            logger.warning("[spec] Task failed: %s — status=%s", task_text, task_status)

            if on_event:
                on_event({
                    "type": "spec.task_done",
                    "taskId": task_id,
                    "eventId": f"spec_fail_{idx}",
                    "payload": {
                        "spec_name": spec_name,
                        "task_index": idx + 1,
                        "task_text": task_text,
                        "status": task_status,
                    },
                    "timestamp": _now_iso(),
                })
            # Don't stop on single task failure, continue with remaining
            # (user can re-run exec spec to retry failed tasks)

    return {
        "status": "completed" if failed_count == 0 else "partial",
        "spec_name": spec_name,
        "spec_path": str(spec_path),
        "tasks_done": done_count,
        "tasks_failed": failed_count,
        "tasks_skipped": 0,
    }


# --- Internal helpers ---

def _slugify(text: str) -> str:
    """Chuyen description thanh ten file hop le."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = text.strip("-")
    return text[:60] or "spec"


def _parse_tasks(content: str) -> list[tuple[int, dict[str, Any]]]:
    """Phan tich task list tu spec content.

    Returns: list of (line_index, {"text": str, "done": bool})
    """
    tasks = []
    for i, line in enumerate(content.splitlines()):
        m = re.match(r"^\s*-\s*\[([ xX])\]\s*(.+)$", line)
        if m:
            done = m.group(1).lower() == "x"
            text = m.group(2).strip()
            tasks.append((i, {"text": text, "done": done}))
    return tasks


def _count_tasks(content: str) -> tuple[int, int]:
    tasks = _parse_tasks(content)
    total = len(tasks)
    done = sum(1 for _, t in tasks if t["done"])
    return total, done


def _mark_task_done(content: str, task_text: str) -> str:
    """Doi [  ] thanh [x] cho task co text khop."""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*-\s*)\[ \](\s*.+)$", line)
        if m:
            text_in_line = m.group(2).strip()
            if text_in_line == task_text or task_text in text_in_line:
                lines[i] = m.group(1) + "[x]" + m.group(2)
                break
    return "\n".join(lines)


def _get_spec_header(content: str) -> str:
    """Lay phan Requirements + Design tu spec (bo Tasks)."""
    lines = content.splitlines()
    result = []
    in_tasks = False
    for line in lines:
        if re.match(r"^##\s+Tasks", line, re.IGNORECASE):
            in_tasks = True
        if not in_tasks:
            result.append(line)
    return "\n".join(result)


def _read_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _write_safe(path: Path, content: str) -> None:
    try:
        path.write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.warning("[spec] failed to write %s: %s", path, exc)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

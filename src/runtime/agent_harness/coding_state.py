# -*- coding: utf-8 -*-
"""Semantic coding-state projection cho Eris main và VS Code.

Module này cố tình KHÔNG chứa chain-of-thought. Nó chỉ biến những sự kiện quan sát
được của executor (tool nào đang chạy, file nào đã đọc/sửa, test ra sao, đang chờ
ai) thành một state nhỏ, bền vững và đủ nghĩa để frontend/Subject Core hiểu.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable

_MAX_FILES = 40
_MAX_TEXT = 900


def initial_coding_state(objective: str = "") -> dict[str, Any]:
    return {
        "version": "coding_state.v1",
        "objective": _text(objective, 1600),
        "phase": "queued",
        "current_action": "",
        "current_file": "",
        "files_inspected": [],
        "files_modified": [],
        "test_state": {
            "status": "not_run",
            "passed": 0,
            "failed": 0,
            "summary": "",
        },
        "blocker": "",
        "waiting_for": "",
        "next_action": "",
        "last_tool": "",
        "last_summary": "",
        "updated_at": time.time(),
    }


def normalize_coding_state(value: Any, *, objective: str = "") -> dict[str, Any]:
    """Chuẩn hóa state cũ/malformed để migration không làm UI nổ tung."""
    base = initial_coding_state(objective)
    if not isinstance(value, dict):
        return base
    for key in (
        "version", "objective", "phase", "current_action", "current_file",
        "blocker", "waiting_for", "next_action", "last_tool", "last_summary",
    ):
        if key in value:
            base[key] = _text(value.get(key), 1600 if key == "objective" else _MAX_TEXT)
    base["files_inspected"] = _files(value.get("files_inspected"))
    base["files_modified"] = _files(value.get("files_modified"))
    tests = value.get("test_state") if isinstance(value.get("test_state"), dict) else {}
    base["test_state"] = {
        "status": _text(tests.get("status", "not_run"), 40) or "not_run",
        "passed": _int(tests.get("passed", 0)),
        "failed": _int(tests.get("failed", 0)),
        "summary": _text(tests.get("summary", ""), 600),
    }
    try:
        base["updated_at"] = float(value.get("updated_at", time.time()) or time.time())
    except (TypeError, ValueError):
        base["updated_at"] = time.time()
    return base


def merge_coding_state(current: Any, patch: Any, *, objective: str = "") -> dict[str, Any]:
    """Merge patch semantic; list file được union thay vì bị reset vô tình."""
    state = normalize_coding_state(current, objective=objective)
    if not isinstance(patch, dict):
        return state
    for key in (
        "objective", "phase", "current_action", "current_file", "blocker",
        "waiting_for", "next_action", "last_tool", "last_summary",
    ):
        if key in patch:
            state[key] = _text(patch.get(key), 1600 if key == "objective" else _MAX_TEXT)
    if "files_inspected" in patch:
        state["files_inspected"] = _merge_files(state.get("files_inspected"), patch.get("files_inspected"))
    if "files_modified" in patch:
        state["files_modified"] = _merge_files(state.get("files_modified"), patch.get("files_modified"))
    if isinstance(patch.get("test_state"), dict):
        tests = dict(state.get("test_state") or {})
        incoming = patch["test_state"]
        if "status" in incoming:
            tests["status"] = _text(incoming.get("status"), 40) or tests.get("status", "not_run")
        if "passed" in incoming:
            tests["passed"] = _int(incoming.get("passed"))
        if "failed" in incoming:
            tests["failed"] = _int(incoming.get("failed"))
        if "summary" in incoming:
            tests["summary"] = _text(incoming.get("summary"), 600)
        state["test_state"] = tests
    state["version"] = "coding_state.v1"
    state["updated_at"] = time.time()
    return state


def tool_start_patch(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """State tối thiểu khi một tool bắt đầu, hoàn toàn dựa trên hành động công khai."""
    tool = str(name or "").strip()
    args = dict(args or {})
    file_path = _tool_path(tool, args)
    phase = "executing"
    if tool in {"project_list_files", "project_read_file", "project_search_text", "project_find_symbol", "project_semantic_search", "project_git_log", "project_dependency_summary", "project_test_inventory", "project_git_diff"}:
        phase = "investigating"
    elif tool == "project_apply_patch":
        phase = "modifying"
    elif tool in {"project_run_tests", "project_run_command"}:
        phase = "testing" if tool == "project_run_tests" else "executing"
    elif tool.startswith("editor_"):
        phase = "navigating"
    elif tool.startswith("ide_get_"):
        # Live IDE RPC là inspection semantic tại thời điểm hiện tại, không phải
        # snapshot context cũ từ lúc user vừa gửi message.
        phase = "investigating"
    elif tool == "ask_user_choice":
        phase = "waiting_user"
    patch: dict[str, Any] = {
        "phase": phase,
        "current_action": human_tool_label(tool, args),
        "last_tool": tool,
        "next_action": "",
        "blocker": "",
    }
    if file_path:
        patch["current_file"] = file_path
        if tool == "project_read_file":
            patch["files_inspected"] = [file_path]
    if tool == "project_run_tests":
        patch["test_state"] = {"status": "running", "summary": "Đang chạy test."}
    return patch


def tool_result_patch(name: str, args: dict[str, Any] | None, result: Any) -> dict[str, Any]:
    tool = str(name or "").strip()
    args = dict(args or {})
    success, data, message = _result_parts(result)
    patch: dict[str, Any] = {
        "last_tool": tool,
        "last_summary": message,
        "current_action": "",
        "waiting_for": "",
    }
    file_path = _tool_path(tool, args)
    if file_path:
        patch["current_file"] = file_path
    if tool == "project_read_file" and file_path:
        patch["phase"] = "investigating"
        patch["files_inspected"] = [file_path]
    elif tool == "project_apply_patch":
        touched = _coerce_string_list(data.get("touched_files"))
        patch["phase"] = "verifying" if success else "blocked"
        patch["files_modified"] = touched
        if touched:
            patch["current_file"] = touched[-1]
        if success:
            patch["next_action"] = "Kiểm tra diff và chạy test phù hợp."
        else:
            patch["blocker"] = message or "Patch thất bại."
    elif tool == "project_git_diff":
        patch["phase"] = "verifying"
        patch["next_action"] = "Chạy test hoặc hoàn tất nếu thay đổi đã được xác minh."
    elif tool == "project_run_tests":
        output = _text(data.get("output", ""), 1400)
        passed, failed = _parse_test_counts(output, success)
        patch["phase"] = "verifying" if success else "blocked"
        patch["test_state"] = {
            "status": "passed" if success else "failed",
            "passed": passed,
            "failed": failed,
            "summary": message or ("Test đạt." if success else "Test thất bại."),
        }
        patch["blocker"] = "" if success else (message or "Test đang fail.")
        patch["next_action"] = "Hoàn tất và tóm tắt thay đổi." if success else "Đọc lỗi test, sửa root cause rồi chạy lại."
    elif not success:
        patch["phase"] = "blocked"
        patch["blocker"] = message or f"{tool} thất bại."
        patch["next_action"] = "Replan từ lỗi quan sát được."
    else:
        patch.setdefault("phase", "executing")
    return patch


def human_tool_label(name: str, args: dict[str, Any] | None = None) -> str:
    args = dict(args or {})
    path = _tool_path(name, args)
    labels = {
        "project_list_files": "Đang đọc cấu trúc workspace",
        "project_read_file": "Đang đọc file",
        "project_search_text": "Đang tìm code",
        "project_find_symbol": "Đang tìm symbol",
        "project_semantic_search": "Đang tìm code theo ngữ nghĩa",
        "project_git_log": "Đang đọc lịch sử Git",
        "project_dependency_summary": "Đang đọc dependency",
        "project_test_inventory": "Đang kiểm kê test suite",
        "project_git_diff": "Đang kiểm tra diff",
        "project_apply_patch": "Đang áp patch",
        "project_run_tests": "Đang chạy test",
        "project_run_command": "Đang chạy lệnh project",
        "editor_open_file": "Đang mở file trong VS Code",
        "editor_open_diff": "Đang mở diff trong VS Code",
        "editor_focus_problems": "Đang mở Problems",
        "editor_show_source_control": "Đang mở Source Control",
        "ide_get_current_file": "Đang lấy file hiện tại từ VS Code",
        "ide_get_selection": "Đang lấy selection hiện tại",
        "ide_get_problems": "Đang đọc Problems hiện tại",
        "ide_get_git_state": "Đang đọc Git state hiện tại",
        "ide_get_terminal_recent": "Đang đọc terminal gần nhất",
        "ide_get_open_editors": "Đang đọc các editor đang mở",
        "ide_get_workspace_info": "Đang đọc workspace hiện tại",
        "search_capabilities": "Đang tìm capability",
        "activate_skill": "Đang kích hoạt skill",
        "ask_user_choice": "Đang chờ lựa chọn của người dùng",
    }
    label = labels.get(str(name or ""), str(name or "tool"))
    if path:
        return f"{label}: {path}"
    query = _text(args.get("query", ""), 180)
    if query and name in {"project_search_text", "project_find_symbol", "search_capabilities"}:
        return f"{label}: {query}"
    return label


def compact_for_prompt(value: Any) -> dict[str, Any]:
    state = normalize_coding_state(value)
    return {
        "objective": state["objective"],
        "phase": state["phase"],
        "current_action": state["current_action"],
        "current_file": state["current_file"],
        "files_inspected": state["files_inspected"][-12:],
        "files_modified": state["files_modified"][-12:],
        "test_state": state["test_state"],
        "blocker": state["blocker"],
        "waiting_for": state["waiting_for"],
        "next_action": state["next_action"],
        "last_summary": state["last_summary"],
    }


def _result_parts(result: Any) -> tuple[bool, dict[str, Any], str]:
    if isinstance(result, dict):
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        success = bool(result.get("success", False))
        message = _text(result.get("message", result.get("status", "")), _MAX_TEXT)
        return success, dict(data), message
    return True, {}, _text(result, _MAX_TEXT)


def _tool_path(name: str, args: dict[str, Any]) -> str:
    for key in ("path", "file", "file_path", "target"):
        value = args.get(key)
        if isinstance(value, str) and value.strip() and not (key == "target" and name == "project_run_tests"):
            return value.strip().replace("\\", "/")[:500]
    return ""


def _coerce_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _files(value)
    if isinstance(value, tuple):
        return _files(list(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            return _files(parsed)
        return _files([part.strip() for part in text.split(",") if part.strip()])
    return []


def _merge_files(existing: Any, incoming: Any) -> list[str]:
    merged = _files(existing)
    for item in _files(incoming):
        if item not in merged:
            merged.append(item)
    return merged[-_MAX_FILES:]


def _files(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = _text(item, 500).replace("\\", "/")
        if text and text not in out:
            out.append(text)
    return out[-_MAX_FILES:]


def _text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _parse_test_counts(output: str, success: bool) -> tuple[int, int]:
    import re
    text = str(output or "")
    passed = failed = 0
    for pattern, target in ((r"(\d+)\s+passed", "passed"), (r"(\d+)\s+failed", "failed")):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            if target == "passed":
                passed = int(match.group(1))
            else:
                failed = int(match.group(1))
    if success and passed == 0 and failed == 0:
        passed = 1
    if not success and failed == 0:
        failed = 1
    return passed, failed


__all__ = [
    "initial_coding_state", "normalize_coding_state", "merge_coding_state",
    "tool_start_patch", "tool_result_patch", "human_tool_label", "compact_for_prompt",
]

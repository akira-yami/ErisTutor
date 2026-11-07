# -*- coding: utf-8 -*-
"""Runtime invariants cho Repository Context và Completion Gate.

Tách policy khỏi LangGraph để có thể unit-test độc lập. Model được quyền đề xuất,
nhưng runtime mới là nơi quyết định lúc nào đã đủ evidence để side effect/final.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def tool_result_success(result: Any) -> bool:
    """Đọc success theo contract tool mà không suy diễn từ câu chữ model."""
    if isinstance(result, dict):
        return bool(result.get("success", False))
    success = getattr(result, "success", None)
    return bool(success) if success is not None else False


def patch_candidate_paths(patch_text: str) -> list[str]:
    """Lấy path workspace-relative từ unified diff để enforce read-before-write."""
    paths: list[str] = []
    for line in str(patch_text or "").splitlines():
        if not (line.startswith("+++ ") or line.startswith("--- ")):
            continue
        raw = line[4:].strip().split("\t", 1)[0]
        if raw == "/dev/null":
            continue
        if raw.startswith("a/") or raw.startswith("b/"):
            raw = raw[2:]
        raw = raw.replace("\\", "/").strip("/")
        if raw and raw not in paths:
            paths.append(raw)
    return paths


def context_gate_reason(
    tool_name: str,
    args: dict[str, Any],
    *,
    state: dict[str, Any],
    inspection_evidence: list[dict[str, Any]],
) -> str:
    """Hard gate side effect: RepoContext trước, deep read sau, rồi mới được sửa."""
    name = str(tool_name or "").strip()
    if name not in {"project_apply_patch", "project_run_command"}:
        return ""
    if not bool(state.get("context_ready", False)):
        return "Repository Context chưa sẵn sàng. Hãy index/ground workspace trước khi thực hiện side effect."

    deep = [
        item for item in inspection_evidence
        if str(item.get("tool", "")) in {"project_read_file", "project_search_text", "project_find_symbol"}
    ]
    if not deep:
        return "Chưa có inspection evidence từ implementation. Hãy đọc/tìm source liên quan trước khi sửa hoặc chạy command có side effect."

    targets = state.get("resolved_targets") if isinstance(state.get("resolved_targets"), list) else []
    target_paths = [str(item.get("path", "") or "").replace("\\", "/").strip("/") for item in targets if isinstance(item, dict)]
    target_paths = [item for item in target_paths if item]
    if target_paths:
        read_paths = [str(item.get("path", "") or "").replace("\\", "/").strip("/") for item in deep if item.get("path")]
        if read_paths and not any(
            read == target or read.startswith(target + "/") or target.startswith(read + "/")
            for read in read_paths for target in target_paths
        ):
            return "Inspection hiện tại chưa chạm vùng task đã ground trong RepoContext. Hãy đọc target/caller liên quan thay vì sửa một module khác."

    if name == "project_apply_patch":
        workspace = Path(str(state.get("workspace_path", "") or "")).expanduser()
        read_set = {
            str(item.get("path", "") or "").replace("\\", "/").strip("/").lower()
            for item in deep if item.get("path")
        }
        for rel in patch_candidate_paths(str(args.get("patch", "") or "")):
            # File mới không thể read-before-write. File đã tồn tại thì bắt buộc
            # project_read_file exact path; search hit không đủ để patch an toàn.
            try:
                exists = bool(str(workspace) and (workspace / rel).exists())
            except OSError:
                exists = False
            if exists and rel.lower() not in read_set:
                return f"File sắp sửa chưa được deep-read trong task: {rel}. Hãy project_read_file file này trước khi patch."
    return ""


def requires_verified_change(goal: str) -> bool:
    """Nhận diện task có intent tạo/sửa/xóa artifact để Completion Gate enforce evidence."""
    text = str(goal or "").lower()
    patterns = (
        r"\bfix\b", r"\bimplement\b", r"\brefactor\b", r"\bmodify\b", r"\bupdate\b",
        r"\bcreate\b", r"\bwrite\b", r"\bdelete\b", r"\bremove\b", r"\bpatch\b",
        r"\bsửa\b", r"\btạo\b", r"\bviết\b", r"\bthêm\b", r"\bxóa\b", r"\bcập nhật\b",
        r"\bchỉnh\b", r"\bvá\b", r"\blàm lại\b",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


__all__ = ["context_gate_reason", "patch_candidate_paths", "requires_verified_change", "tool_result_success"]

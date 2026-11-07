# -*- coding: utf-8 -*-
"""Project Operations Runtime cho Eris.

Runtime này trao quyền thao tác project theo mô hình autonomous-by-default:
inspect -> plan -> edit -> test -> verify -> rollback/package.

Ranh giới duy nhất được giữ cứng là workspace root. Eris được tự quyết bên trong
project, nhưng không được dùng một patch để chui ra ngoài repo rồi sửa cả máy.
Mọi operation đều có evidence, snapshot và ledger để lời "đã sửa" không còn là
một câu văn tự tin đứng một mình giữa sa mạc.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from actions.contracts import ActionExecutionResult, ActionExecutionStatus
from llm.json_sanitizer import extract_last_valid_json_object, strip_reasoning_envelope
from runtime.workspace_manager import WorkspaceManager, extract_workspace_source

PROJECT_OPERATIONS_VERSION = "project_operations.v1.3"


_READ_ONLY_PROJECT_CAPABILITIES = frozenset({
    "project_list_files",
    "project_read_file",
    "project_search_text",
    "project_find_symbol",
    "project_semantic_search",
    "project_git_log",
    "project_dependency_summary",
    "project_test_inventory",
    "project_git_diff",
    "workspace_list",
    "workspace_switch",
})


def project_autonomy_allows(capability_id: str, autonomy_mode: str) -> tuple[bool, str]:
    """Ràng quyền project theo mode người dùng chọn trong Advanced.

    `read_only` là hàng rào thật, không phải nhãn UI dễ thương dán lên một runtime
    vẫn có thể apply patch. `guarded` và `full` đều cho thao tác, khác nhau ở các
    policy hỏi/rollback/package được lưu cạnh mode.
    """

    cap = str(capability_id or "").strip()
    mode = str(autonomy_mode or "guarded").strip().lower()
    if mode in {"off", "disabled", "none", "0", "false"}:
        return False, "project_autonomy_disabled"
    if mode == "read_only" and cap not in _READ_ONLY_PROJECT_CAPABILITIES:
        return False, "project_read_only_mode"
    return True, "project_autonomy_allowed"


class ProjectOperationState(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    FAILED_VERIFICATION = "failed_verification"
    WAITING_FOR_INPUT = "waiting_for_input"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class ProjectCapabilityContract:
    capability_id: str
    public_name_vi: str
    description_vi: str
    handler_id: str
    risk_level: str = "medium"
    permission_mode: str = "autonomous_project_scope"
    reversible: bool = True


@dataclass(frozen=True, slots=True)
class ProjectEvidence:
    evidence_id: str
    source: str
    summary: str
    captured_at: float = 0.0
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "summary": self.summary,
            "captured_at": round(float(self.captured_at or 0.0), 3),
            "data": dict(self.data),
        }


@dataclass(slots=True)
class ProjectActionResult:
    operation_id: str
    capability_id: str
    state: ProjectOperationState
    public_text: str
    started_at: float
    completed_at: float
    executed: bool = False
    verified: bool = False
    evidence: tuple[ProjectEvidence, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    error_kind: str = ""
    error_message: str = ""

    @property
    def success(self) -> bool:
        return self.state == ProjectOperationState.SUCCEEDED and self.executed and self.verified

    def to_action_result(self, *, action_input: str = "") -> ActionExecutionResult:
        payload = {k: _stringify(v) for k, v in self.data.items() if v is not None}
        payload.update({
            "project_operation_id": self.operation_id,
            "project_state": self.state.value,
            "executed": str(bool(self.executed)).lower(),
            "verified": str(bool(self.verified)).lower(),
            "project_operations_version": PROJECT_OPERATIONS_VERSION,
        })
        return ActionExecutionResult(
            action_name=self.capability_id,
            action_input=action_input,
            success=self.success,
            message=self.public_text or self.error_message or "Project operation không hoàn tất.",
            data=payload,
            status=ActionExecutionStatus.OBSERVED if self.success else ActionExecutionStatus.FAILED,
            dispatched=bool(self.executed),
            observed=bool(self.verified),
            evidence=tuple(item.evidence_id for item in self.evidence),
            error_kind=self.error_kind,
            observer_reason="project_postcondition_verified" if self.verified else "project_verification_failed",
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        payload["success"] = self.success
        return payload


PROJECT_CAPABILITY_CONTRACTS: dict[str, ProjectCapabilityContract] = {
    "project_list_files": ProjectCapabilityContract(
        "project_list_files", "liệt kê file project", "liệt kê file trong workspace có lọc cache/binary", "project.list_files", "low"
    ),
    "project_read_file": ProjectCapabilityContract(
        "project_read_file", "đọc file project", "đọc file text trong project kèm hash", "project.read_file", "low"
    ),
    "project_search_text": ProjectCapabilityContract(
        "project_search_text", "tìm text trong project", "tìm chuỗi/regex trong source", "project.search_text", "low"
    ),
    "project_find_symbol": ProjectCapabilityContract(
        "project_find_symbol", "tìm symbol", "tìm class/function/symbol theo tên", "project.find_symbol", "low"
    ),
    "project_semantic_search": ProjectCapabilityContract(
        "project_semantic_search", "tìm kiếm ngữ nghĩa", "truy vấn AST vector bằng ngôn ngữ tự nhiên (LanceDB)", "project.semantic_search", "low"
    ),
    "project_git_log": ProjectCapabilityContract(
        "project_git_log", "đọc lịch sử Git", "đọc commit history gần đây để hiểu ý định và vùng code đang tiến hóa", "project.git_log", "low"
    ),
    "project_dependency_summary": ProjectCapabilityContract(
        "project_dependency_summary", "đọc dependency project", "tóm tắt dependency manifests mà không kéo lockfile khổng lồ vào context", "project.dependency_summary", "low"
    ),
    "project_test_inventory": ProjectCapabilityContract(
        "project_test_inventory", "kiểm kê test suite", "liệt kê test/config liên quan để Planner biết verification surface trước khi sửa", "project.test_inventory", "low"
    ),
    "project_git_diff": ProjectCapabilityContract(
        "project_git_diff", "xem diff project", "đọc git diff hiện tại", "project.git_diff", "low"
    ),
    "project_apply_patch": ProjectCapabilityContract(
        "project_apply_patch", "áp patch project", "snapshot rồi áp unified diff trong workspace", "project.apply_patch", "high"
    ),
    "project_run_tests": ProjectCapabilityContract(
        "project_run_tests", "chạy test project", "chạy pytest hoặc command test đã chỉ định", "project.run_tests", "medium"
    ),
    "project_run_command": ProjectCapabilityContract(
        "project_run_command", "chạy lệnh project", "chạy command phát triển trong project root", "project.run_command", "high", reversible=False
    ),
    "project_rollback": ProjectCapabilityContract(
        "project_rollback", "rollback operation", "khôi phục snapshot của operation gần nhất hoặc được chỉ định", "project.rollback", "high"
    ),
    "project_package_patch": ProjectCapabilityContract(
        "project_package_patch", "đóng gói patch ZIP", "đóng gói file thay đổi và ghi chú tiếng Việt", "project.package_patch", "medium"
    ),
    "project_execute_task": ProjectCapabilityContract(
        "project_execute_task", "tự hoàn thành tác vụ code", "tự inspect, lập patch, chạy test, verify, rollback và đóng gói", "project.execute_task", "high"
    ),
    "workspace_join": ProjectCapabilityContract(
        "workspace_join", "gia nhập workspace", "clone Git, đăng ký path local hoặc giải nén ZIP vào ErisLive", "workspace.join", "medium"
    ),
    "workspace_switch": ProjectCapabilityContract(
        "workspace_switch", "chuyển workspace", "đặt một workspace đã đăng ký làm project active", "workspace.switch", "low"
    ),
    "workspace_list": ProjectCapabilityContract(
        "workspace_list", "liệt kê workspace", "liệt kê workspace đã được người dùng giao cho Eris", "workspace.list", "low", reversible=False
    ),
    "workspace_leave": ProjectCapabilityContract(
        "workspace_leave", "rời workspace", "gỡ workspace khỏi registry; mặc định không xóa dữ liệu", "workspace.leave", "medium"
    ),
}
PROJECT_CAPABILITY_IDS = tuple(PROJECT_CAPABILITY_CONTRACTS)


class ProjectWorkspaceGuard:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def resolve(self, value: str | Path, *, must_exist: bool = False) -> Path:
        raw = Path(value)
        candidate = raw.expanduser() if raw.is_absolute() else self.root / raw
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"Path nằm ngoài project root: {value}") from exc
        if must_exist and not resolved.exists():
            raise FileNotFoundError(str(resolved))
        return resolved

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()


class ProjectOperationsRuntime:
    """Executor project có snapshot, evidence và autonomous fallback."""

    _IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv", "node_modules", ".eris"}
    _TEXT_EXTENSIONS = {
        ".py", ".pyi", ".gd", ".cs", ".tscn", ".tres", ".json", ".jsonl", ".yaml", ".yml", ".toml",
        ".ini", ".cfg", ".md", ".txt", ".log", ".ps1", ".sh", ".bat", ".cmd", ".js", ".ts", ".tsx",
        ".jsx", ".html", ".css", ".scss", ".cpp", ".c", ".h", ".hpp", ".java", ".kt", ".rs", ".go", ".xml",
    }

    def __init__(self, owner: Any, *, logger: Any | None = None, max_audit: int = 120) -> None:
        self.owner = owner
        self.logger = logger
        manager = getattr(owner, "workspace_manager", None)
        self.workspace_manager = manager if manager is not None else WorkspaceManager(owner, logger=logger)
        if getattr(owner, "workspace_manager", None) is None:
            try:
                setattr(owner, "workspace_manager", self.workspace_manager)
            except (AttributeError, TypeError) as exc:
                if self.logger is not None:
                    self.logger.debug("Không bind được workspace manager vào owner: %s", exc)
        self._lock = threading.RLock()
        self._audit: deque[ProjectActionResult] = deque(maxlen=max(30, int(max_audit)))
        self._semantic_indexer = None
        self._semantic_indexer_root = ""
        self._handlers: dict[str, Callable[[str, str], ProjectActionResult]] = {
            "project_list_files": self._list_files,
            "project_read_file": self._read_file,
            "project_search_text": self._search_text,
            "project_find_symbol": self._find_symbol,
            "project_semantic_search": self._semantic_search,
            "project_git_log": self._git_log,
            "project_dependency_summary": self._dependency_summary,
            "project_test_inventory": self._test_inventory,
            "project_git_diff": self._git_diff,
            "project_apply_patch": self._apply_patch,
            "project_run_tests": self._run_tests,
            "project_run_command": self._run_command,
            "project_rollback": self._rollback,
            "project_package_patch": self._package_patch,
            "project_execute_task": self._execute_task,
            "workspace_join": self._workspace_join,
            "workspace_switch": self._workspace_switch,
            "workspace_list": self._workspace_list,
            "workspace_leave": self._workspace_leave,
        }

    def contracts(self) -> tuple[ProjectCapabilityContract, ...]:
        return tuple(PROJECT_CAPABILITY_CONTRACTS.values())

    def availability(self, capability_id: str) -> tuple[bool, str]:
        cap = str(capability_id or "").strip()
        if cap not in PROJECT_CAPABILITY_CONTRACTS:
            return False, "unknown_project_capability"
        if not bool(self._setting("enabled", True)):
            return False, "project_operations_disabled"
        autonomy_mode = str(self._setting("autonomy_mode", "full") or "full").strip().lower()
        autonomy_allowed, autonomy_reason = project_autonomy_allows(cap, autonomy_mode)
        if not autonomy_allowed:
            return False, autonomy_reason
        workspace_caps = {"workspace_join", "workspace_switch", "workspace_list", "workspace_leave"}
        if cap in workspace_caps:
            if cap == "workspace_join" and shutil.which("git") is None:
                # Path local/ZIP vẫn dùng được; handler sẽ chỉ chặn Git URL.
                return True, "workspace_runtime_ready_without_git_clone"
            return True, "workspace_runtime_ready"
        if cap == "project_execute_task" and self._coding_client() is None:
            return False, "coding_llm_client_unavailable"
        root = self._active_project_root()
        if root is None:
            # execute_task có thể nhận link/path ngay trong câu lệnh và tự join.
            if cap == "project_execute_task":
                return True, "workspace_may_be_joined_from_task"
            return False, "active_workspace_required"
        if not root.exists() or not root.is_dir():
            return False, "active_workspace_unavailable"
        if cap in {"project_git_diff", "project_git_log", "project_apply_patch"} and shutil.which("git") is None:
            return False, "git_executable_missing"
        if cap == "project_semantic_search":
            # Do not advertise Continue-style semantic retrieval when its optional
            # stack is absent. A visible tool that deterministically ImportErrors
            # is worse than no tool because it teaches the agent false affordances.
            import importlib.util
            required = (
                "lancedb", "pyarrow", "sentence_transformers", "tree_sitter",
                "tree_sitter_python", "tree_sitter_javascript", "tree_sitter_typescript",
            )
            missing = [name for name in required if importlib.util.find_spec(name) is None]
            if missing:
                return False, "semantic_dependencies_missing:" + ",".join(missing)
        return True, "autonomous_workspace_runtime_ready"

    def execute(self, capability_id: str, action_input: str = "") -> ProjectActionResult:
        cap = str(capability_id or "").strip()
        operation_id = f"proj_{uuid.uuid4().hex[:12]}"
        started = time.time()
        available, reason = self.availability(cap)
        if not available:
            return self._finish(ProjectActionResult(
                operation_id, cap, ProjectOperationState.FAILED,
                "Năng lực project này chưa sẵn sàng.", started, time.time(),
                error_kind="CapabilityUnavailable", error_message=reason,
                data={"availability_reason": reason},
            ))
        try:
            result = self._handlers[cap](operation_id, str(action_input or ""))
            return self._finish(result)
        except Exception as exc:
            return self._finish(ProjectActionResult(
                operation_id, cap, ProjectOperationState.FAILED,
                "Tôi không hoàn tất được thao tác project này.", started, time.time(),
                executed=True, verified=False, error_kind=type(exc).__name__, error_message=str(exc)[:1000],
            ))

    def audit_snapshot(self, limit: int = 20) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(item.to_dict() for item in list(self._audit)[-max(1, int(limit)):])

    # ---------------------------- workspace operations ---------------------------

    def _workspace_list(self, operation_id: str, action_input: str) -> ProjectActionResult:
        del action_input
        started = time.time()
        records = [item.to_dict() for item in self.workspace_manager.list_workspaces()]
        active_id = self.workspace_manager.active_workspace_id
        evidence = (self._evidence(
            _hash_id("workspace_registry", json.dumps(records, ensure_ascii=False, sort_keys=True)),
            "workspace_registry",
            f"count={len(records)} active={active_id or 'none'}",
            self.workspace_manager.describe(),
        ),)
        return self._success(
            operation_id, "workspace_list", started,
            f"ErisLive có {len(records)} workspace; active: {active_id or 'chưa có'}.",
            evidence, {"verification_key": "workspace_list", "workspaces": records,
                       "active_workspace_id": active_id, **self.workspace_manager.describe()},
        )

    def _workspace_join(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input)
        raw = str(payload.get("source", payload.get("url", payload.get("path", payload.get("value", action_input)))) or "").strip()
        source = extract_workspace_source(raw) or raw.strip().strip('"\'')
        workspace_id = str(payload.get("workspace_id", "") or "").strip()
        if _looks_like_remote_source(source) and shutil.which("git") is None:
            raise RuntimeError("Máy chưa có git nên chưa thể clone link repo; path local và ZIP vẫn dùng được.")
        record = self.workspace_manager.join(source, workspace_id=workspace_id, activate=True)
        path = Path(record.path).expanduser().resolve()
        verified = path.is_dir() and self.workspace_manager.active_workspace_id == record.workspace_id
        if not verified:
            raise RuntimeError("Workspace đã join nhưng không xác minh được trạng thái active.")
        evidence = (self._evidence(
            _hash_id("workspace", f"{record.workspace_id}|{record.path}"),
            "workspace_join", f"id={record.workspace_id} type={record.source_type}",
            record.to_dict(),
        ),)
        return self._success(
            operation_id, "workspace_join", started,
            f"Tôi đã gia nhập và chuyển sang workspace {record.display_name}.", evidence,
            {"verification_key": "workspace_join", **record.to_dict(),
             "eris_home": str(self.workspace_manager.home.root), "active": True},
        )

    def _workspace_switch(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input)
        ref = str(payload.get("workspace_id", payload.get("name", payload.get("value", action_input))) or "").strip()
        record = self.workspace_manager.switch(ref)
        evidence = (self._evidence(
            _hash_id("workspace_switch", record.workspace_id), "workspace_switch",
            f"active={record.workspace_id}", record.to_dict(),
        ),)
        return self._success(
            operation_id, "workspace_switch", started,
            f"Tôi đã chuyển active workspace sang {record.display_name}.", evidence,
            {"verification_key": "workspace_switch", **record.to_dict(), "active": True},
        )

    def _workspace_leave(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input)
        ref = str(payload.get("workspace_id", payload.get("name", payload.get("value", ""))) or "").strip()
        delete_copy = bool(payload.get("delete_managed_copy", False))
        record = self.workspace_manager.leave(ref, delete_managed_copy=delete_copy)
        evidence = (self._evidence(
            _hash_id("workspace_leave", record.workspace_id), "workspace_leave",
            f"left={record.workspace_id} deleted={delete_copy and record.managed_copy}", record.to_dict(),
        ),)
        return self._success(
            operation_id, "workspace_leave", started,
            f"Tôi đã rời workspace {record.display_name}; {'đã xóa bản sao được quản lý' if delete_copy and record.managed_copy else 'không xóa dữ liệu'}.",
            evidence, {"verification_key": "workspace_leave", **record.to_dict(),
                       "deleted_managed_copy": bool(delete_copy and record.managed_copy)},
        )

    # ------------------------------ read operations ------------------------------

    def _list_files(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input)
        limit = max(1, min(int(payload.get("limit", 500) or 500), 3000))
        pattern = str(payload.get("pattern", "") or "").strip().lower()
        files = [p for p in self._iter_files() if not pattern or pattern in p.lower()][:limit]
        evidence = (self._evidence("project_tree", "project_file_list", f"files={len(files)}", {"root": str(self._project_root())}),)
        return self._success(operation_id, "project_list_files", started,
            f"Tôi đã liệt kê {len(files)} file trong project.", evidence,
            {"verification_key": "project_list_files", "files": files, "count": len(files)})

    def _read_file(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input)
        path_value = str(payload.get("path", payload.get("value", action_input)) or "").strip()
        path = self._guard().resolve(path_value, must_exist=True)
        if path.suffix.lower() not in self._TEXT_EXTENSIONS:
            raise ValueError("Chỉ đọc file text/source trong project runtime.")
        max_chars = max(200, min(int(payload.get("max_chars", 20000) or 20000), 120000))
        content = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        digest = _sha256_bytes(content.encode("utf-8", "replace"))
        rel = self._guard().relative(path)
        evidence = (self._evidence(f"file:{digest[:16]}", "project_file", f"{rel} sha256={digest[:16]}", {"path": rel}),)
        return self._success(operation_id, "project_read_file", started,
            f"Tôi đã đọc {rel} và xác nhận hash {digest[:12]}.", evidence,
            {"verification_key": "project_read_file", "path": rel, "sha256": digest, "content": content})

    def _search_text(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input)
        query = str(payload.get("query", payload.get("value", action_input)) or "").strip()
        if not query:
            raise ValueError("Thiếu query tìm kiếm.")
        regex_mode = bool(payload.get("regex", False))
        limit = max(1, min(int(payload.get("limit", 200) or 200), 1000))
        matcher = re.compile(query, re.I) if regex_mode else None

        # Literal search dùng ripgrep khi có sẵn. Repo lớn mà Python mở từng file
        # rồi split toàn bộ chỉ để tìm một chuỗi là một cách khá sáng tạo để đốt SSD.
        matches: list[dict[str, Any]] = []
        engine = "python"
        if not regex_mode and shutil.which("rg"):
            matches = self._search_text_ripgrep(query, limit=limit)
            engine = "ripgrep"
        else:
            needle = query.lower()
            for rel in self._iter_files():
                if len(matches) >= limit:
                    break
                path = self._guard().resolve(rel)
                if path.suffix.lower() not in self._TEXT_EXTENSIONS:
                    continue
                try:
                    with path.open("r", encoding="utf-8", errors="replace") as handle:
                        for line_no, line in enumerate(handle, 1):
                            hit = bool(matcher.search(line)) if matcher else needle in line.lower()
                            if hit:
                                matches.append({"path": rel, "line": line_no, "text": line.strip()[:360]})
                                if len(matches) >= limit:
                                    break
                except OSError:
                    continue
        evidence = (self._evidence(
            _hash_id("search", query), "project_search",
            f"query={query[:80]} matches={len(matches)} engine={engine} limit={limit}",
        ),)
        return self._success(operation_id, "project_search_text", started,
            f"Tôi tìm thấy {len(matches)} vị trí khớp trong project.", evidence,
            {"verification_key": "project_search_text", "query": query, "matches": matches,
             "count": len(matches), "limit": limit, "engine": engine})

    def _search_text_ripgrep(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        root = self._project_root()
        args = [
            "rg", "--line-number", "--no-heading", "--color", "never",
            "--fixed-strings", "--ignore-case", "--hidden", "--no-ignore",
        ]
        for ignored in sorted(self._IGNORED_DIRS):
            args.extend(["--glob", f"!**/{ignored}/**"])
        for extension in sorted(self._TEXT_EXTENSIONS):
            args.extend(["--glob", f"*{extension}"])
        args.extend(["--", query, "."])

        matches: list[dict[str, Any]] = []
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                args, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", shell=False,
            )
            if process.stdout is None:
                return []
            for raw in process.stdout:
                line = raw.rstrip("\r\n")
                parts = line.split(":", 2)
                if len(parts) != 3:
                    continue
                rel, line_no_text, text = parts
                try:
                    line_no = int(line_no_text)
                except ValueError:
                    continue
                rel = rel.removeprefix("./").replace("\\", "/")
                try:
                    safe_rel = self._guard().relative(self._guard().resolve(rel, must_exist=True))
                except (OSError, RuntimeError, ValueError):
                    continue
                matches.append({"path": safe_rel, "line": line_no, "text": text.strip()[:360]})
                if len(matches) >= limit:
                    process.terminate()
                    break
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            return matches
        except OSError:
            return []
        finally:
            if process is not None and process.poll() is None:
                process.kill()

    def _find_symbol(self, operation_id: str, action_input: str) -> ProjectActionResult:
        payload = _parse_payload(action_input)
        symbol = str(payload.get("symbol", payload.get("query", payload.get("value", action_input))) or "").strip()
        if not symbol:
            raise ValueError("Thiếu tên symbol.")
        limit = max(1, min(int(payload.get("limit", 200) or 200), 500))
        pattern = rf"^\s*(?:async\s+def|def|class|func|function|interface|enum|struct)\s+{re.escape(symbol)}\b|\b{re.escape(symbol)}\s*\("
        result = self._search_text(operation_id, json.dumps({
            "query": pattern, "regex": True, "limit": limit,
        }, ensure_ascii=False))
        result.capability_id = "project_find_symbol"
        result.data["verification_key"] = "project_find_symbol"
        result.data["symbol"] = symbol
        result.data["limit"] = limit
        result.public_text = f"Tôi tìm thấy {result.data.get('count', 0)} vị trí liên quan symbol {symbol}."
        result.public_text = f"Tôi tìm thấy {result.data.get('count', 0)} vị trí liên quan symbol {symbol}."
        return result

    def _get_semantic_indexer(self) -> Any:
        root = self._active_project_root()
        if not root:
            raise RuntimeError("Chưa có active workspace để index.")
        resolved_root = str(Path(root).resolve())
        if getattr(self, "_semantic_indexer", None) is None or getattr(self, "_semantic_indexer_root", "") != resolved_root:
            try:
                from runtime.agent_harness.semantic_indexer import SemanticIndexer
            except ImportError as e:
                raise RuntimeError(f"Lỗi nạp SemanticIndexer: {e}. Cần pip install lancedb tree-sitter sentence-transformers")
            # Semantic state is workspace-scoped. Reusing an indexer after a
            # workspace switch leaks stale code context across repositories.
            self._semantic_indexer = SemanticIndexer(resolved_root)
            self._semantic_indexer_root = resolved_root
        return self._semantic_indexer

    def _semantic_search(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time()
        payload = _parse_payload(action_input)
        query = str(payload.get("query", payload.get("value", action_input)) or "").strip()
        if not query:
            raise ValueError("Thiếu query cho semantic search.")
        
        limit = max(1, min(int(payload.get("limit", 5) or 5), 20))
        indexer = self._get_semantic_indexer()

        # First use must be useful without requiring the model to discover a
        # magic reindex flag. Subsequent calls remain incremental and cheap.
        if payload.get("reindex", False) or not indexer.is_ready():
            indexer.index_workspace(force=False)

        results = indexer.search(query, limit=limit)
        
        evidence = (self._evidence(_hash_id("semantic_search", query), "project_semantic_search", f"query={query[:50]}"),)
        return self._success(operation_id, "project_semantic_search", started,
            f"Tìm thấy {len(results)} đoạn code liên quan.", evidence,
            {"verification_key": "project_semantic_search", "query": query, "results": results})

    def _git_log(self, operation_id: str, action_input: str) -> ProjectActionResult:
        """Read a bounded commit history; Explorer uses this to recover repository intent."""
        started = time.time(); payload = _parse_payload(action_input)
        limit = max(1, min(int(payload.get("limit", 20) or 20), 100))
        path_value = str(payload.get("path", "") or "").strip()
        args = [
            "git", "-C", str(self._project_root()), "log", "--no-color", "--decorate=short",
            "--date=short", f"--max-count={limit}",
            "--pretty=format:%h%x09%ad%x09%an%x09%d%x09%s",
        ]
        rel_path = ""
        if path_value:
            path = self._guard().resolve(path_value, must_exist=True)
            rel_path = self._guard().relative(path)
            args.extend(["--", rel_path])
        completed = self._run_process(args, timeout=20)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr or completed.stdout or "git log failed")
        commits: list[dict[str, str]] = []
        for raw in (completed.stdout or "").splitlines()[:limit]:
            parts = raw.split("\t", 4)
            if not parts or not parts[0].strip():
                continue
            while len(parts) < 5:
                parts.append("")
            commits.append({
                "hash": parts[0].strip(), "date": parts[1].strip(), "author": parts[2].strip(),
                "refs": parts[3].strip(), "subject": parts[4].strip(),
            })
        evidence = (self._evidence(
            _hash_id("git_log", json.dumps(commits, ensure_ascii=False, sort_keys=True)),
            "project_git_log", f"commits={len(commits)} path={rel_path or '*'}",
        ),)
        return self._success(
            operation_id, "project_git_log", started,
            f"Tôi đã đọc {len(commits)} commit gần nhất" + (f" cho {rel_path}." if rel_path else "."),
            evidence,
            {"verification_key": "project_git_log", "commits": commits, "count": len(commits), "path": rel_path},
        )

    def _dependency_summary(self, operation_id: str, action_input: str) -> ProjectActionResult:
        """Summarize dependency manifests without flooding context with generated lockfiles."""
        started = time.time(); payload = _parse_payload(action_input)
        limit = max(1, min(int(payload.get("limit", 80) or 80), 300))
        all_files = self._iter_files()
        candidates = [
            rel for rel in all_files
            if self._is_dependency_manifest(rel)
        ][:40]
        manifests: list[dict[str, Any]] = []
        remaining = limit
        for rel in candidates:
            if remaining <= 0:
                break
            path = self._guard().resolve(rel, must_exist=True)
            names = self._dependency_names_from_manifest(path, remaining)
            remaining -= len(names)
            manifests.append({"path": rel, "dependencies": names, "count": len(names)})
        flattened = [name for item in manifests for name in item.get("dependencies", [])]
        evidence = (self._evidence(
            _hash_id("dependency_summary", json.dumps(manifests, ensure_ascii=False, sort_keys=True)),
            "project_dependency_summary", f"manifests={len(manifests)} dependencies={len(flattened)}",
        ),)
        return self._success(
            operation_id, "project_dependency_summary", started,
            f"Tôi đã đọc {len(manifests)} dependency manifest và tóm tắt {len(flattened)} dependency.",
            evidence,
            {"verification_key": "project_dependency_summary", "manifests": manifests,
             "dependencies": flattened, "manifest_count": len(manifests), "dependency_count": len(flattened)},
        )

    def _test_inventory(self, operation_id: str, action_input: str) -> ProjectActionResult:
        """Return a bounded test surface instead of executing arbitrary discovery commands."""
        started = time.time(); payload = _parse_payload(action_input)
        limit = max(1, min(int(payload.get("limit", 300) or 300), 1200))
        files = self._iter_files()
        test_files = [rel for rel in files if self._looks_like_test_file(rel)][:limit]
        config_names = {
            "pytest.ini", "tox.ini", "noxfile.py", "jest.config.js", "jest.config.ts", "jest.config.cjs",
            "vitest.config.js", "vitest.config.ts", "playwright.config.js", "playwright.config.ts",
            "phpunit.xml", "phpunit.xml.dist", "karma.conf.js", "testthat.R",
        }
        configs = [
            rel for rel in files
            if Path(rel).name.lower() in {name.lower() for name in config_names}
            or Path(rel).name.lower() in {"pyproject.toml", "package.json", "project.godot", "cargo.toml", "go.mod"}
        ][:80]
        frameworks = self._infer_test_frameworks(configs, files)
        evidence = (self._evidence(
            _hash_id("test_inventory", json.dumps({"tests": test_files, "configs": configs}, sort_keys=True)),
            "project_test_inventory", f"tests={len(test_files)} configs={len(configs)} frameworks={','.join(frameworks)}",
        ),)
        return self._success(
            operation_id, "project_test_inventory", started,
            f"Tôi đã kiểm kê {len(test_files)} test file và {len(configs)} test/config manifest.",
            evidence,
            {"verification_key": "project_test_inventory", "test_files": test_files, "count": len(test_files),
             "configs": configs, "frameworks": frameworks},
        )

    @staticmethod
    def _is_dependency_manifest(rel: str) -> bool:
        name = Path(rel).name.lower()
        if name in {
            "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "poetry.lock", "uv.lock",
            "package.json", "pnpm-workspace.yaml", "cargo.toml", "go.mod", "pom.xml",
            "build.gradle", "build.gradle.kts", "gradle.properties", "composer.json", "gemfile",
            "project.godot",
        }:
            return True
        if name.startswith("requirements") and name.endswith((".txt", ".in")):
            return True
        return name.endswith((".csproj", ".fsproj", ".vbproj"))

    @staticmethod
    def _looks_like_test_file(rel: str) -> bool:
        normalized = rel.replace("\\", "/").lower()
        name = Path(normalized).name
        parts = set(Path(normalized).parts)
        if parts.intersection({"tests", "test", "__tests__", "spec", "specs"}):
            return True
        return bool(re.search(
            r"(^test_.*\.py$|.*_test\.py$|.*\.(test|spec)\.(js|jsx|ts|tsx)$|.*_test\.(go|rs)$|.*tests?\.cs$|test_.*\.gd$)",
            name,
        ))

    @staticmethod
    def _dependency_names_from_manifest(path: Path, limit: int) -> list[str]:
        if limit <= 0:
            return []
        name = path.name.lower()
        text = path.read_text(encoding="utf-8", errors="replace")[:250000]
        found: list[str] = []

        def add(value: str) -> None:
            token = str(value or "").strip()
            if token and token not in found and len(found) < limit:
                found.append(token)

        if name == "package.json" or name == "composer.json":
            try:
                data = json.loads(text)
            except Exception:
                data = {}
            for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies", "require", "require-dev"):
                values = data.get(section, {}) if isinstance(data, dict) else {}
                if isinstance(values, dict):
                    for dep in values:
                        add(dep)
            return found
        if name == "pyproject.toml":
            try:
                import tomllib
                data = tomllib.loads(text)
            except Exception:
                data = {}
            project = data.get("project", {}) if isinstance(data, dict) else {}
            for item in project.get("dependencies", []) if isinstance(project, dict) else []:
                add(re.split(r"[<>=!~;\[ ]", str(item), maxsplit=1)[0])
            optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
            if isinstance(optional, dict):
                for values in optional.values():
                    for item in values if isinstance(values, list) else []:
                        add(re.split(r"[<>=!~;\[ ]", str(item), maxsplit=1)[0])
            tool = data.get("tool", {}) if isinstance(data, dict) else {}
            poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
            for section in ("dependencies", "dev-dependencies"):
                values = poetry.get(section, {}) if isinstance(poetry, dict) else {}
                if isinstance(values, dict):
                    for dep in values:
                        if dep.lower() != "python": add(dep)
            return found
        if name.startswith("requirements"):
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "-r", "--")):
                    continue
                add(re.split(r"[<>=!~;\[ ]", line, maxsplit=1)[0])
            return found
        if name == "cargo.toml":
            section = ""
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("[") and stripped.endswith("]"):
                    section = stripped.strip("[]").lower()
                    continue
                if "dependencies" in section and "=" in stripped and not stripped.startswith("#"):
                    add(stripped.split("=", 1)[0].strip())
            return found
        if name == "go.mod":
            for match in re.finditer(r"(?m)^\s*([\w.-]+\.[\w./-]+)\s+v\d", text):
                add(match.group(1))
            return found
        if name.endswith((".csproj", ".fsproj", ".vbproj")):
            for match in re.finditer(r"<PackageReference\s+Include=[\"']([^\"']+)", text, re.I):
                add(match.group(1))
            return found
        if name == "pom.xml":
            for match in re.finditer(r"<artifactId>([^<]+)</artifactId>", text):
                add(match.group(1))
            return found
        if name.startswith("build.gradle"):
            for match in re.finditer(r"(?:implementation|api|testImplementation|runtimeOnly)\s*[\( ]*[\"']([^:\"']+:[^:\"']+)", text):
                add(match.group(1))
            return found
        # Generic manifests are still useful as evidence; return bounded lexical hints.
        for match in re.finditer(r"(?m)^\s*[\"']?([A-Za-z0-9_.@/-]{2,80})[\"']?\s*[=:]", text):
            add(match.group(1))
        return found

    @staticmethod
    def _infer_test_frameworks(configs: Sequence[str], files: Sequence[str]) -> list[str]:
        corpus = "\n".join(list(configs) + list(files[:2000])).lower()
        checks = [
            ("pytest", ("pytest.ini", "test_", "/tests/")),
            ("jest", ("jest.config", ".test.ts", ".test.js", "__tests__")),
            ("vitest", ("vitest.config",)),
            ("playwright", ("playwright.config",)),
            ("go test", ("_test.go",)),
            ("cargo test", ("cargo.toml", "/tests/")),
            ("dotnet test", (".csproj", "tests.cs")),
            ("godot test", ("project.godot", "test_")),
        ]
        return [label for label, needles in checks if any(needle in corpus for needle in needles)]

    def _git_diff(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input)
        staged = bool(payload.get("staged", False))
        args = ["git", "-C", str(self._project_root()), "diff", "--no-ext-diff", "--"]
        if staged:
            args.insert(4, "--cached")
        completed = self._run_process(args, timeout=20)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr or completed.stdout)
        diff = completed.stdout[:120000]
        evidence = (self._evidence(_hash_id("git_diff", diff), "git_diff", f"chars={len(diff)} staged={staged}"),)
        return self._success(operation_id, "project_git_diff", started,
            f"Tôi đã đọc diff project, dài {len(diff)} ký tự.", evidence,
            {"verification_key": "project_git_diff", "diff": diff, "staged": staged, "empty": not bool(diff.strip())})

    # ------------------------------ write operations -----------------------------

    def _apply_patch(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input, keep_raw=True)
        patch_text = str(payload.get("patch", payload.get("raw", "")) or "")
        if not patch_text.strip():
            raise ValueError("Thiếu unified diff trong action_input.")
        root = self._project_root(); op_dir = self._operation_dir(operation_id)
        touched = self._parse_patch_paths(patch_text)
        if not touched:
            raise ValueError("Patch không chứa path hợp lệ.")
        self._snapshot_files(operation_id, touched)
        patch_path = op_dir / "change.patch"
        patch_path.write_text(patch_text, encoding="utf-8")
        check = self._run_process(["git", "-C", str(root), "apply", "--check", "--whitespace=nowarn", str(patch_path)], timeout=30)
        if check.returncode != 0:
            raise RuntimeError(f"git apply --check thất bại: {(check.stderr or check.stdout)[:1200]}")
        applied = self._run_process(["git", "-C", str(root), "apply", "--whitespace=nowarn", str(patch_path)], timeout=30)
        if applied.returncode != 0:
            raise RuntimeError(f"git apply thất bại: {(applied.stderr or applied.stdout)[:1200]}")
        verified, hashes = self._verify_touched_files(touched)
        self._write_manifest(operation_id, {"operation_id": operation_id, "capability": "project_apply_patch", "touched_files": touched, "hashes": hashes, "created_at": time.time()})
        evidence = tuple(self._evidence(f"file:{digest[:16]}", "project_file_after_patch", f"{path} sha256={digest[:16]}", {"path": path}) for path, digest in hashes.items())
        if not verified:
            return ProjectActionResult(operation_id, "project_apply_patch", ProjectOperationState.FAILED_VERIFICATION,
                "Patch đã được áp nhưng hậu điều kiện file chưa xác minh đủ.", started, time.time(), True, False, evidence,
                {"touched_files": touched, "hashes": hashes}, "VerificationFailed")
        return self._success(operation_id, "project_apply_patch", started,
            f"Tôi đã áp patch vào {len(touched)} file và xác minh nội dung sau ghi.", evidence,
            {"verification_key": "project_apply_patch", "touched_files": touched, "hashes": hashes})

    def _run_tests(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input)
        command = payload.get("command") or payload.get("target") or action_input.strip()
        if not command or str(command).strip().lower() in {"all", "auto", "default", "current"}:
            args = self._detect_test_command()
        else:
            args = self._command_args(command)
        timeout = max(10, min(int(payload.get("timeout", 300) or 300), 1800))
        completed = self._run_process(args, timeout=timeout)
        output = ((completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else ""))[-30000:]
        passed = completed.returncode == 0
        evidence = (self._evidence(_hash_id("test", f"{args}|{completed.returncode}|{output}"), "project_test_process", f"exit={completed.returncode} command={shlex.join(args)[:180]}"),)
        state = ProjectOperationState.SUCCEEDED if passed else ProjectOperationState.FAILED_VERIFICATION
        text = "Test đã đạt." if passed else f"Test thất bại với exit code {completed.returncode}."
        return ProjectActionResult(operation_id, "project_run_tests", state, text, started, time.time(), True, passed, evidence,
            {"verification_key": "project_run_tests" if passed else "", "command": args, "exit_code": completed.returncode, "output": output},
            "" if passed else "TestsFailed", "" if passed else output[-1000:])

    def _run_command(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input)
        command = payload.get("command") or payload.get("value") or action_input
        args = self._command_args(command)
        self._validate_command(args)
        timeout = max(5, min(int(payload.get("timeout", 300) or 300), 1800))
        completed = self._run_process(args, timeout=timeout)
        output = ((completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else ""))[-30000:]
        ok = completed.returncode == 0
        evidence = (self._evidence(_hash_id("command", f"{args}|{completed.returncode}|{output}"), "project_command", f"exit={completed.returncode} command={shlex.join(args)[:180]}"),)
        return ProjectActionResult(operation_id, "project_run_command", ProjectOperationState.SUCCEEDED if ok else ProjectOperationState.FAILED_VERIFICATION,
            "Lệnh project đã chạy thành công." if ok else f"Lệnh project lỗi với exit code {completed.returncode}.",
            started, time.time(), True, ok, evidence,
            {"verification_key": "project_run_command" if ok else "", "command": args, "exit_code": completed.returncode, "output": output},
            "" if ok else "CommandFailed", "" if ok else output[-1000:])

    def _rollback(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input)
        target_id = str(payload.get("operation_id", payload.get("value", action_input)) or "").strip()
        if not target_id or target_id.lower() in {"latest", "last", "gần nhất", "gan nhat"}:
            target_id = self._latest_snapshot_operation()
        if not target_id:
            raise FileNotFoundError("Không có snapshot operation để rollback.")
        op_dir = self._operation_dir(target_id, create=False)
        manifest = json.loads((op_dir / "snapshot_manifest.json").read_text(encoding="utf-8"))
        restored: list[str] = []
        for item in manifest.get("files", []):
            rel = str(item.get("path", "")); existed = bool(item.get("existed", False))
            target = self._guard().resolve(rel)
            backup = op_dir / "backup" / rel
            if existed:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
            elif target.exists():
                target.unlink()
            restored.append(rel)
        evidence = (self._evidence(_hash_id("rollback", target_id + "|" + "|".join(restored)), "project_snapshot", f"operation={target_id} restored={len(restored)}"),)
        return self._success(operation_id, "project_rollback", started,
            f"Tôi đã rollback operation {target_id}, khôi phục {len(restored)} file.", evidence,
            {"verification_key": "project_rollback", "rolled_back_operation": target_id, "restored_files": restored})

    def _package_patch(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input)
        target_id = str(payload.get("operation_id", "") or "").strip() or self._latest_manifest_operation() or self._latest_snapshot_operation()
        output_dir = self.workspace_manager.patch_output_dir()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        zip_path = output_dir / f"eris_patch_{stamp}.zip"
        changed = self._changed_files(target_id)
        notes = str(payload.get("notes_vi", "") or "").strip() or self._build_notes_vi(target_id, changed)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for rel in changed:
                path = self._guard().resolve(rel)
                if path.is_file():
                    archive.write(path, arcname=rel)
            archive.writestr("GHI_CHU_VI.txt", notes + "\n")
            if target_id:
                op_dir = self._operation_dir(target_id, create=False)
                for name in ("change.patch", "operation_manifest.json"):
                    candidate = op_dir / name
                    if candidate.is_file():
                        archive.write(candidate, arcname=f"_eris_meta/{name}")
        digest = _sha256_bytes(zip_path.read_bytes())
        evidence = (self._evidence(f"zip:{digest[:16]}", "project_patch_zip", f"{zip_path.name} files={len(changed)} sha256={digest[:16]}", {"path": str(zip_path)}),)
        return self._success(operation_id, "project_package_patch", started,
            f"Tôi đã đóng gói patch ZIP gồm {len(changed)} file và ghi chú tiếng Việt.", evidence,
            {"verification_key": "project_package_patch", "zip_path": str(zip_path), "sha256": digest, "files": changed, "source_operation": target_id})

    # ------------------------------ autonomous agent -----------------------------

    def _execute_task(self, operation_id: str, action_input: str) -> ProjectActionResult:
        started = time.time(); payload = _parse_payload(action_input)
        task = str(payload.get("task", payload.get("value", action_input)) or "").strip()
        if not task:
            raise ValueError("Thiếu mô tả tác vụ project.")
        source = str(payload.get("workspace_source", payload.get("repo", payload.get("url", ""))) or "").strip()
        source = source or extract_workspace_source(task)
        joined_workspace: dict[str, Any] = {}
        if source:
            joined = self.workspace_manager.join(source, workspace_id=str(payload.get("workspace_id", "") or ""), activate=True)
            joined_workspace = joined.to_dict()
        if self._active_project_root() is None:
            raise RuntimeError(
                "Chưa có active workspace. Hãy đưa link repo, path local, ZIP hoặc dùng workspace_join trước; "
                "Eris không tự mở source code của chính mình."
            )

        max_attempts = max(1, min(int(self._setting("max_repair_attempts", 2) or 2), 4))
        failure_context = ""
        attempt_reports: list[dict[str, Any]] = []
        final_apply: ProjectActionResult | None = None
        final_plan: dict[str, Any] = {}
        final_attempt_id = ""

        for attempt in range(1, max_attempts + 1):
            attempt_id = f"{operation_id}_a{attempt}"
            context = self._build_agent_context(task)
            if failure_context:
                context += "\n\nFAILURE FROM PREVIOUS ATTEMPT:\n" + failure_context[-18000:]
            plan = self._ask_coding_model(task, context)
            patch_text = str(plan.get("patch", "") or "")
            questions = _clean_string_list(plan.get("questions_vi"), limit=8)
            assumptions = _clean_string_list(plan.get("assumptions_vi"), limit=12)
            report: dict[str, Any] = {
                "attempt": attempt,
                "summary_vi": str(plan.get("summary_vi", "") or ""),
                "assumptions_vi": assumptions,
                "questions_vi": questions,
            }
            if questions and bool(self._setting("ask_when_uncertain", True)):
                self._record_nonblocking_questions(attempt_id, task, questions, assumptions)
                report["question_mode"] = "non_blocking" if bool(self._setting("proceed_without_user_reply", True)) else "wait_for_user"
                if not bool(self._setting("proceed_without_user_reply", True)):
                    return ProjectActionResult(
                        operation_id, "project_execute_task", ProjectOperationState.WAITING_FOR_INPUT,
                        "Tôi đã ghi câu hỏi cần làm rõ và đang chờ phản hồi trước khi sửa project.",
                        started, time.time(), False, False, (),
                        {"task": task, "questions_vi": questions, "assumptions_vi": assumptions, "attempts": [report]},
                        "UserInputRequired", "Cấu hình proceed_without_user_reply đang tắt.",
                    )
            if not patch_text.strip():
                failure_context = "Coding model không trả unified diff."
                report.update({"stage": "plan", "success": False, "error": failure_context})
                attempt_reports.append(report)
                continue

            apply_result = self._apply_patch(attempt_id, json.dumps({"patch": patch_text}, ensure_ascii=False))
            report["apply"] = apply_result.to_dict()
            if not apply_result.success:
                failure_context = apply_result.error_message or apply_result.public_text
                report.update({"stage": "apply", "success": False, "error": failure_context})
                attempt_reports.append(report)
                continue

            test_commands = plan.get("tests") if isinstance(plan.get("tests"), list) else []
            if not test_commands:
                test_commands = ["auto"]
            test_results: list[dict[str, Any]] = []
            tests_passed = True
            for index, command in enumerate(test_commands[:4], 1):
                test_id = f"{attempt_id}_test{index}"
                result = self._run_tests(test_id, json.dumps({"command": command}, ensure_ascii=False))
                test_results.append(result.to_dict())
                if not result.success:
                    tests_passed = False
                    failure_context = str(result.data.get("output", "") or result.error_message or result.public_text)
                    break
            report["tests"] = test_results
            report["success"] = tests_passed
            attempt_reports.append(report)

            if tests_passed:
                final_apply = apply_result
                final_plan = plan
                final_attempt_id = attempt_id
                break

            if self._setting("auto_rollback_on_failure", True):
                rollback = self._rollback(f"{attempt_id}_rollback", json.dumps({"operation_id": attempt_id}))
                report["rollback"] = rollback.to_dict()
            else:
                return ProjectActionResult(operation_id, "project_execute_task", ProjectOperationState.FAILED_VERIFICATION,
                    "Tôi đã sửa nhưng test thất bại và policy không cho tự rollback.", started, time.time(), True, False,
                    apply_result.evidence, {"task": task, "attempts": attempt_reports}, "TestsFailed", failure_context[-1000:])

        if final_apply is None:
            return ProjectActionResult(operation_id, "project_execute_task", ProjectOperationState.ROLLED_BACK,
                f"Tôi đã thử {max_attempts} vòng, đọc failure và tự sửa lại nhưng chưa đạt; workspace đã được rollback.",
                started, time.time(), True, False, (), {"task": task, "attempts": attempt_reports, "rolled_back": True},
                "RepairAttemptsExhausted", failure_context[-1000:])

        package_data: dict[str, Any] = {}
        package_evidence: tuple[ProjectEvidence, ...] = ()
        if self._setting("auto_package_patch", True):
            package = self._package_patch(f"{operation_id}_package", json.dumps({
                "operation_id": final_attempt_id,
                "notes_vi": str(final_plan.get("notes_vi", "") or final_plan.get("summary_vi", "")),
            }, ensure_ascii=False))
            package_data = package.data
            package_evidence = package.evidence
        evidence = tuple((*final_apply.evidence, *package_evidence))
        unanswered_count = sum(len(item.get("questions_vi", []) or []) for item in attempt_reports)
        question_note = f"; đã ghi {unanswered_count} câu hỏi không chặn" if unanswered_count else ""
        return self._success(operation_id, "project_execute_task", started,
            f"Tôi đã tự hoàn thành tác vụ sau {len(attempt_reports)} vòng, test đạt và xác minh {len(final_apply.data.get('touched_files', []))} file thay đổi{question_note}.", evidence,
            {"verification_key": "project_execute_task", "task": task,
             "plan_summary": final_plan.get("summary_vi", ""),
             "assumptions_vi": final_plan.get("assumptions_vi", []),
             "questions_vi": _clean_string_list(final_plan.get("questions_vi"), limit=8),
             "unanswered_question_count": unanswered_count,
             "touched_files": final_apply.data.get("touched_files", []),
             "workspace": joined_workspace or (self.workspace_manager.active_record().to_dict() if self.workspace_manager.active_record() else {}),
             "attempts": attempt_reports, "package": package_data})

    def _build_agent_context(self, task: str) -> str:
        files = self._iter_files()[:700]
        task_tokens = [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", task) if token.lower() not in {"eris", "project", "file", "test", "fix"}]
        candidates: list[str] = []
        for rel in files:
            low = rel.lower()
            if any(token.lower() in low for token in task_tokens):
                candidates.append(rel)
        if not candidates:
            candidates = [rel for rel in files if rel.endswith(".py")][:24]
        snippets: list[str] = []
        budget = 24000
        for rel in candidates[:18]:
            path = self._guard().resolve(rel)
            try:
                content = path.read_text(encoding="utf-8", errors="replace")[:5000]
            except OSError:
                continue
            chunk = f"\n### FILE {rel}\n{content}\n"
            if sum(len(x) for x in snippets) + len(chunk) > budget:
                break
            snippets.append(chunk)
        diff = self._run_process(["git", "-C", str(self._project_root()), "diff", "--no-ext-diff"], timeout=15).stdout[:12000]
        return "PROJECT TREE:\n" + "\n".join(files[:700]) + "\n\nCURRENT DIFF:\n" + diff + "\n" + "".join(snippets)

    def _ask_coding_model(self, task: str, context: str) -> dict[str, Any]:
        client = self._coding_client()
        if client is None:
            raise RuntimeError("Không có coding LLM client.")
        uncertainty_rule = (
            "Nếu thiếu dữ kiện, ghi câu hỏi vào questions_vi nhưng vẫn tự chọn giả định ít phá nhất trong assumptions_vi và tiếp tục. "
            if bool(self._setting("ask_when_uncertain", True))
            else "Không tạo câu hỏi; tự chọn giả định ít phá nhất trong assumptions_vi và tiếp tục. "
        )
        system = (
            "Bạn là coding agent của Eris. Hãy sửa project bằng unified diff tối thiểu nhưng hoàn chỉnh. "
            "Không giải thích ngoài JSON. Chỉ dùng path trong project. Giữ thay đổi hiện có của user. "
            + uncertainty_rule +
            "Output JSON schema: {summary_vi:string, assumptions_vi:string[], questions_vi:string[], patch:string, tests:string[], notes_vi:string}. "
            "patch phải là unified diff hợp lệ bắt đầu bằng diff --git hoặc ---/+++; tests chỉ chứa lệnh test an toàn trong project."
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": f"TÁC VỤ:\n{task}\n\nCONTEXT:\n{context}"}]
        from runtime.inference_broker import inference_context
        coding_timeout = float(self._setting("coding_timeout_seconds", 120))
        with inference_context(
            "chat_tool_required",
            optional=False,
            timeout_seconds=coding_timeout,
            cancel_scope="project_operations:coding",
            metadata={"operation": "coding_model"},
        ):
            response = client.create_chat_completion(
                messages=messages, temperature=0.15, frequency_penalty=0.0, presence_penalty=0.0,
                max_tokens=max(1800, int(self._setting("coding_max_tokens", 5000))),
                response_format={"type": "json_object"}, timeout=coding_timeout,
                reasoning_effort="high", reasoning_format="hidden",
            )
        raw = self._extract_response_text(response)
        clean = strip_reasoning_envelope(raw)
        candidate = extract_last_valid_json_object(clean) or clean
        try:
            parsed = json.loads(candidate)
        except Exception as exc:
            raise RuntimeError(f"Coding model trả JSON không hợp lệ: {clean[:500]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Coding model không trả JSON object.")
        return parsed

    # --------------------------------- helpers ----------------------------------

    def _active_project_root(self) -> Path | None:
        """Chỉ trả workspace đã được join/switch rõ ràng; không fallback source."""
        return self.workspace_manager.active_path()

    def _project_root(self) -> Path:
        root = self._active_project_root()
        if root is None:
            raise RuntimeError(
                "Chưa có active workspace. ErisLive là nhà runtime, không phải repo; "
                "hãy gia nhập hoặc chọn một workspace trước."
            )
        return root

    def _guard(self) -> ProjectWorkspaceGuard:
        return ProjectWorkspaceGuard(self._project_root())

    def _iter_files(self) -> list[str]:
        root = self._project_root(); out: list[str] = []
        # os.walk cho phép prune directory trước khi descend. rglob cũ vẫn đi sâu
        # vào node_modules/.git rồi mới lọc từng path, repo lớn sẽ chậm vô ích.
        for current, dirs, files in os.walk(root):
            dirs[:] = sorted(name for name in dirs if name not in self._IGNORED_DIRS)
            base = Path(current)
            for name in sorted(files):
                path = base / name
                try:
                    out.append(path.relative_to(root).as_posix())
                except ValueError:
                    continue
        return out

    def _operation_dir(self, operation_id: str, *, create: bool = True) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", operation_id)
        path = self.workspace_manager.operation_dir(safe_id, create=create)
        return path

    def _snapshot_files(self, operation_id: str, files: Sequence[str]) -> None:
        op_dir = self._operation_dir(operation_id); records: list[dict[str, Any]] = []
        for rel in files:
            path = self._guard().resolve(rel)
            existed = path.is_file()
            records.append({"path": rel, "existed": existed})
            if existed:
                backup = op_dir / "backup" / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
        (op_dir / "snapshot_manifest.json").write_text(json.dumps({"operation_id": operation_id, "files": records}, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_manifest(self, operation_id: str, payload: Mapping[str, Any]) -> None:
        (self._operation_dir(operation_id) / "operation_manifest.json").write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")

    def _record_nonblocking_questions(
        self,
        operation_id: str,
        task: str,
        questions: Sequence[str],
        assumptions: Sequence[str],
    ) -> None:
        """Ghi câu hỏi để UI/ledger có thể hiện, nhưng không biến chúng thành approval gate."""
        payload = {
            "operation_id": operation_id,
            "task": task,
            "questions_vi": list(questions),
            "assumptions_vi": list(assumptions),
            "proceed_without_user_reply": bool(self._setting("proceed_without_user_reply", True)),
            "captured_at": time.time(),
        }
        target = self._operation_dir(operation_id) / "questions_vi.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        callback = getattr(self.owner, "on_project_questions", None)
        if callable(callback):
            try:
                callback(dict(payload))
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning("Project question callback failed: %s", exc)

    def _parse_patch_paths(self, patch_text: str) -> list[str]:
        paths: list[str] = []
        for line in patch_text.splitlines():
            candidate = ""
            if line.startswith("diff --git a/"):
                parts = line.split()
                if len(parts) >= 4:
                    candidate = parts[2][2:]
            elif line.startswith("+++ ") or line.startswith("--- "):
                raw = line[4:].split("\t", 1)[0].strip()
                if raw != "/dev/null":
                    candidate = raw[2:] if raw.startswith(("a/", "b/")) else raw
            if candidate:
                rel = self._guard().relative(self._guard().resolve(candidate))
                if rel not in paths:
                    paths.append(rel)
        return paths

    def _verify_touched_files(self, touched: Sequence[str]) -> tuple[bool, dict[str, str]]:
        hashes: dict[str, str] = {}
        for rel in touched:
            path = self._guard().resolve(rel)
            if path.is_file():
                hashes[rel] = _sha256_bytes(path.read_bytes())
            elif path.exists():
                return False, hashes
            else:
                hashes[rel] = "deleted"
        return bool(touched), hashes

    def _changed_files(self, operation_id: str) -> list[str]:
        if operation_id:
            manifest = self._operation_dir(operation_id, create=False) / "operation_manifest.json"
            if manifest.is_file():
                data = json.loads(manifest.read_text(encoding="utf-8"))
                return [str(x) for x in data.get("touched_files", [])]
        completed = self._run_process(["git", "-C", str(self._project_root()), "status", "--porcelain=v1"], timeout=15)
        files: list[str] = []
        for line in completed.stdout.splitlines():
            value = line[3:].strip()
            if " -> " in value:
                value = value.split(" -> ", 1)[1]
            if value:
                try:
                    files.append(self._guard().relative(self._guard().resolve(value)))
                except (OSError, RuntimeError, ValueError) as exc:
                    if self.logger is not None:
                        self.logger.debug("Bỏ qua path Git không hợp lệ %s: %s", value, exc)
        return list(dict.fromkeys(files))

    def _latest_snapshot_operation(self) -> str:
        base = self.workspace_manager.operation_dir("_probe", create=False).parent
        candidates = [p for p in base.glob("*/snapshot_manifest.json")] if base.is_dir() else []
        return max(candidates, key=lambda p: p.stat().st_mtime).parent.name if candidates else ""

    def _latest_manifest_operation(self) -> str:
        base = self.workspace_manager.operation_dir("_probe", create=False).parent
        candidates = [p for p in base.glob("*/operation_manifest.json")] if base.is_dir() else []
        return max(candidates, key=lambda p: p.stat().st_mtime).parent.name if candidates else ""

    def _build_notes_vi(self, operation_id: str, changed: Sequence[str]) -> str:
        active = self.workspace_manager.active_record()
        workspace_name = active.display_name if active else "không rõ"
        lines = ["GÓI PATCH WORKSPACE", f"Workspace: {workspace_name}", f"Operation: {operation_id or 'không rõ'}", "", "File thay đổi:"]
        lines.extend(f"- {item}" for item in changed)
        lines.extend(["", "Ghi chú:", "- Patch được tạo bởi ProjectOperationsRuntime.", "- Hãy chạy lại test trên máy đích sau khi chép patch vào repo."])
        return "\n".join(lines)

    def _detect_test_command(self) -> list[str]:
        root = self._project_root()
        if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists() or (root / "tests").is_dir():
            return [os.getenv("PYTHON", "python"), "-m", "pytest", "-q"]
        if (root / "package.json").exists():
            if (root / "pnpm-lock.yaml").exists():
                return ["pnpm", "test"]
            if (root / "yarn.lock").exists():
                return ["yarn", "test"]
            # --runInBand là flag của Jest, không phải npm. Ép nó vào mọi project
            # khiến Mocha/Vitest/custom scripts hỏng dù package.json hoàn toàn đúng.
            return ["npm", "test"]
        raise FileNotFoundError("Không nhận diện được test command mặc định.")

    def _command_args(self, command: Any) -> list[str]:
        if isinstance(command, list):
            return [str(x) for x in command if str(x).strip()]
        text = str(command or "").strip()
        return shlex.split(text, posix=os.name != "nt")

    def _validate_command(self, args: Sequence[str]) -> None:
        if not args:
            raise ValueError("Command rỗng.")
        allowed = str(os.getenv("ERIS_PROJECT_ALLOWED_COMMANDS", "python,py,pytest,git,uv,pip,pip3,npm,npx,node,pnpm,yarn,dotnet,godot") or "")
        allowed_names = {item.strip().lower() for item in allowed.split(",") if item.strip()}
        executable = Path(str(args[0])).name.lower().removesuffix(".exe")
        if executable not in allowed_names:
            raise PermissionError(f"Executable chưa nằm trong project command policy: {executable}")
        for arg in args[1:]:
            value = str(arg)
            if value.startswith(("http://", "https://")):
                continue
            if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("/", "../", "..\\")):
                try:
                    self._guard().resolve(value)
                except Exception as exc:
                    raise PermissionError(f"Command argument vượt project root: {value}") from exc

    def _run_process(self, args: Sequence[str], *, timeout: int | float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(list(args), cwd=str(self._project_root()), capture_output=True, text=True, timeout=timeout,
                              check=False, encoding="utf-8", errors="replace", shell=False)

    def _coding_client(self) -> Any | None:
        service = getattr(self.owner, "llm_service", None)
        if service is None:
            return None
        # Coding has one explicit owner.  Legacy project_execute_task may still
        # call this helper for compatibility, but it must never borrow another
        # cognition lane implicitly.
        return getattr(service, "coding_client", None)

    @staticmethod
    def _extract_response_text(response: Any) -> str:
        if isinstance(response, str):
            return response
        if isinstance(response, Mapping):
            choices = response.get("choices") or []
            if choices:
                message = choices[0].get("message", {}) if isinstance(choices[0], Mapping) else {}
                content = message.get("content", "") if isinstance(message, Mapping) else ""
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "\n".join(str(item.get("text", "")) for item in content if isinstance(item, Mapping))
            return str(response.get("text", "") or response.get("output_text", "") or "")
        return str(response or "")

    def _setting(self, key: str, default: Any) -> Any:
        settings = getattr(self.owner, "runtime_settings", {})
        block = settings.get("project_ops", {}) if isinstance(settings, Mapping) else {}
        return block.get(key, default) if isinstance(block, Mapping) else default

    def _success(self, operation_id: str, capability_id: str, started: float, text: str,
                 evidence: Sequence[ProjectEvidence], data: dict[str, Any]) -> ProjectActionResult:
        return ProjectActionResult(operation_id, capability_id, ProjectOperationState.SUCCEEDED, text,
                                   started, time.time(), True, True, tuple(evidence), data)

    def _evidence(self, evidence_id: str, source: str, summary: str, data: Mapping[str, Any] | None = None) -> ProjectEvidence:
        return ProjectEvidence(evidence_id, source, summary, time.time(), dict(data or {}))

    def _finish(self, result: ProjectActionResult) -> ProjectActionResult:
        with self._lock:
            self._audit.append(result)
        try:
            ledger = self.workspace_manager.ledger_path()
            ledger.parent.mkdir(parents=True, exist_ok=True)
            with ledger.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result.to_dict(), ensure_ascii=False, default=str) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            if self.logger is not None:
                self.logger.warning("Không ghi được project operation ledger: %s", exc)
        if self.logger is not None:
            self.logger.info("[project-ops] capability=%s operation=%s state=%s verified=%s",
                             result.capability_id, result.operation_id, result.state.value, result.verified)

        # V74: ProjectOperations trả receipt tiến độ thật cho Life-Horizon. Chỉ
        # cập nhật progress từ state/evidence đã được verify.
        try:
            manager = getattr(self.owner, "life_horizon_manager", None)
            note_operation = getattr(manager, "note_project_operation", None)
            if callable(note_operation):
                note_operation(result, now=result.completed_at)
        except Exception as exc:
            if self.logger is not None:
                self.logger.debug("[life-horizon-v74] project receipt degraded: %s", exc)
        return result



def _looks_like_remote_source(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text.startswith(("http://", "https://", "git@", "ssh://", "git://", "file://"))

def _parse_payload(value: str, *, keep_raw: bool = False) -> dict[str, Any]:
    text = str(value or "").strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                if keep_raw:
                    payload.setdefault("raw", text)
                return payload
        except json.JSONDecodeError:
            pass
    return {"value": text, "raw": text} if keep_raw else {"value": text}


def _clean_string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    cleaned: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in cleaned:
            cleaned.append(text[:500])
        if len(cleaned) >= max(1, int(limit)):
            break
    return cleaned


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value[:120000]
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)[:120000]
    except Exception:
        return str(value)[:120000]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_id(prefix: str, value: str) -> str:
    return f"{prefix}:{hashlib.sha256(str(value).encode('utf-8', 'ignore')).hexdigest()[:16]}"


__all__ = [
    "PROJECT_OPERATIONS_VERSION", "PROJECT_CAPABILITY_IDS", "PROJECT_CAPABILITY_CONTRACTS",
    "ProjectCapabilityContract", "ProjectEvidence", "ProjectActionResult", "ProjectOperationsRuntime",
    "ProjectWorkspaceGuard",
]

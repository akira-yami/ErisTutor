# -*- coding: utf-8 -*-
"""Permission policy cho Unified Agent Harness.

Default là unattended AUTOPILOT: một task đã được user/Eris giao thì các thao tác
phát triển bình thường phải chạy liền mạch. Read/search/open file/patch/test/build,
workspace switching và project shell không được biến thành chuỗi hộp thoại xin phép.

Boundary bảo vệ vẫn tồn tại theo ba tầng: thao tác coding thường ngày AUTO, side
effect hiếm nhưng hợp lệ (cài dependency/migration/history rewrite) ASK bằng LangGraph
interrupt, còn publish/credential/destructive host command bị BLOCK. Vì thế mở file,
patch và test không hỏi lặt vặt nhưng autopilot cũng không được giả làm root daemon.
"""
from __future__ import annotations

from enum import Enum
import os
import shlex
from typing import Any


class Permission(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionGate:
    """Capability permission policy with an unattended-first default.

    ``autopilot`` is the production default. ``interactive`` remains available
    as a debugging profile through ``ERIS_AGENT_PERMISSION_MODE=interactive``.
    """

    def __init__(self, mode: str = ""):
        configured = str(mode or os.getenv("ERIS_AGENT_PERMISSION_MODE", "autopilot") or "autopilot").strip().lower()
        self.mode = configured if configured in {"autopilot", "interactive"} else "autopilot"

        # Explicitly known Eris capabilities. In autopilot, anything routine or
        # workspace-bounded runs without human confirmation.
        self.tool_policies: dict[str, Permission] = {
            "search_capabilities": Permission.ALLOW,
            "activate_skill": Permission.ALLOW,
            "get_local_time": Permission.ALLOW,
            "get_now_playing": Permission.ALLOW,
            "get_active_application": Permission.ALLOW,
            "read_visible_ui_text": Permission.ALLOW,
            "inspect_runtime_log": Permission.ALLOW,
            "read_current_error": Permission.ALLOW,
            "inspect_selected_file": Permission.ALLOW,
            "get_project_status": Permission.ALLOW,
            "project_list_files": Permission.ALLOW,
            "project_read_file": Permission.ALLOW,
            "project_search_text": Permission.ALLOW,
            "project_find_symbol": Permission.ALLOW,
            "project_semantic_search": Permission.ALLOW,
            "project_git_diff": Permission.ALLOW,
            "project_apply_patch": Permission.ALLOW,
            "project_run_tests": Permission.ALLOW,
            "project_run_command": Permission.ALLOW,
            "project_rollback": Permission.ALLOW,
            "project_package_patch": Permission.ALLOW,
            "workspace_join": Permission.ALLOW,
            "workspace_switch": Permission.ALLOW,
            "workspace_list": Permission.ALLOW,
            "workspace_leave": Permission.ALLOW,
            "set_volume": Permission.ALLOW,
            "play_pause_media": Permission.ALLOW,
            "copy_text": Permission.ALLOW,
            "focus_window": Permission.ALLOW,
            "launch_app": Permission.ALLOW,
            "open_url": Permission.ALLOW,
            # Legacy one-shot executor bypasses the graph/tool event spine.
            "project_execute_task": Permission.DENY,
        }

    @property
    def unattended(self) -> bool:
        return self.mode == "autopilot"

    def check_permission(
        self,
        action_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        tool_spec: dict[str, Any] | None = None,
    ) -> Permission:
        name = str(action_name or "").strip()
        args = dict(arguments or {})

        if name == "project_run_command":
            command_policy = self._command_policy(str(args.get("command", "") or ""))
            if command_policy is not None:
                return self._resolve_interactive(command_policy)

        if name == "workspace_join":
            source = str(args.get("source", "") or "").strip().lower()
            if source.startswith(("http://", "https://", "git@", "ssh://")):
                return Permission.ASK
        if name == "workspace_leave" and bool(args.get("delete_managed_copy", False)):
            return Permission.ASK

        if name in self.tool_policies:
            return self._resolve_interactive(self.tool_policies[name])
        for key, policy in self.tool_policies.items():
            if name.startswith(key):
                return self._resolve_interactive(policy)

        # Progressive-disclosure tools can appear in future without editing this
        # file. Low/medium inspect/read/control operations are safe to auto-run
        # when LangGraph disclosed them from the typed registry. Unknown high-risk
        # or untyped capabilities fail closed instead of waking the user.
        if isinstance(tool_spec, dict):
            risk = str(tool_spec.get("risk_level", "") or "").strip().lower()
            effect = str(tool_spec.get("effect", "") or "").strip().lower()
            if risk in {"low", "medium"} and effect in {"observe", "inspect", "read", "control"}:
                return Permission.ALLOW
            if risk == "low" and effect == "write":
                return Permission.ALLOW

        return Permission.DENY if self.unattended else Permission.ASK

    def _resolve_interactive(self, policy: Permission) -> Permission:
        # ASK giờ là boundary có chủ đích, không phải default cho mọi tool. Graph
        # interrupt sẽ checkpoint và nhả worker nên một prompt hiếm không gây polling.
        return policy

    @staticmethod
    def _command_policy(command: str) -> Permission | None:
        """Phân command thành AUTO / ASK / BLOCK theo side-effect semantic."""
        text = str(command or "").strip()
        if not text:
            return Permission.DENY
        try:
            parts = shlex.split(text, posix=False)
        except ValueError:
            return Permission.DENY
        lowered = [part.strip('"\'').lower() for part in parts]
        if not lowered:
            return Permission.DENY
        exe = lowered[0].removesuffix(".exe")

        # Host/data destructive command không thuộc quyền coding agent.
        if exe in {
            "rm", "rmdir", "del", "erase", "format", "diskpart", "shutdown", "reboot",
            "poweroff", "halt", "mkfs", "dd", "reg", "regedit", "takeown", "icacls",
        }:
            return Permission.DENY

        # Git local read/commit là bình thường; publish bị block, rewrite/xóa history
        # là tình huống hiếm nhưng đôi khi hợp lệ nên phải hỏi đúng một lần.
        if exe == "git" and len(lowered) > 1:
            sub = lowered[1]
            if sub == "push":
                return Permission.DENY
            if sub == "clean":
                return Permission.ALLOW if any(flag in lowered for flag in {"-n", "--dry-run"}) else Permission.DENY
            if sub == "rebase":
                return Permission.ASK
            if sub == "reset" and "--hard" in lowered:
                return Permission.DENY
            if sub == "reset" and any(flag in lowered for flag in {"--merge", "--keep"}):
                return Permission.ASK
            if sub == "restore" and "--staged" not in lowered:
                return Permission.ASK
            if sub == "checkout" and "--" in lowered:
                return Permission.ASK
            return Permission.ALLOW

        # Cài/gỡ dependency thay đổi lockfile và có thể chạy lifecycle scripts/network.
        # Build/test/lint không rơi vào nhánh này nên vẫn AUTO.
        package_mutators = {
            "npm": {"install", "i", "add", "uninstall", "remove", "rm", "update"},
            "pnpm": {"install", "i", "add", "remove", "rm", "update"},
            "yarn": {"add", "remove", "install", "upgrade"},
            "pip": {"install", "uninstall"},
            "pip3": {"install", "uninstall"},
            "poetry": {"add", "remove", "install", "update"},
            "uv": {"add", "remove", "sync", "pip"},
            "cargo": {"add", "remove", "install", "uninstall"},
            "dotnet": {"add", "remove", "tool"},
        }
        if exe in package_mutators and len(lowered) > 1 and lowered[1] in package_mutators[exe]:
            return Permission.ASK

        # Publish luôn block, kể cả khi package manager không đi qua nhánh trên.
        if exe in {"npm", "pnpm"} and len(lowered) > 1 and lowered[1] == "publish":
            return Permission.DENY
        if exe == "yarn" and "publish" in lowered[1:3]:
            return Permission.DENY
        if exe == "twine" and len(lowered) > 1 and lowered[1] == "upload":
            return Permission.DENY
        if exe == "docker" and len(lowered) > 1 and lowered[1] == "push":
            return Permission.DENY
        if exe == "gh" and len(lowered) > 2 and lowered[1:3] in (["release", "create"], ["pr", "merge"]):
            return Permission.DENY

        # Migration có thể mutate DB thật dù command trông giống tooling local.
        joined = " ".join(lowered)
        migration_markers = (
            "alembic upgrade", "alembic downgrade", "prisma migrate", "manage.py migrate",
            "django-admin migrate", "rails db:migrate", "sequelize db:migrate", "typeorm migration:run",
        )
        if any(marker in joined for marker in migration_markers):
            return Permission.ASK

        # Download/network shell trực tiếp không cần thiết cho coding loop bình thường.
        if exe in {"curl", "wget", "scp", "ftp", "sftp"}:
            return Permission.ASK

        return Permission.ALLOW

    def set_policy(self, action_name: str, permission: Permission):
        self.tool_policies[action_name] = permission


__all__ = ["PermissionGate", "Permission"]

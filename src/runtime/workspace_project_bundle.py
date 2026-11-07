# -*- coding: utf-8 -*-
"""Supervised workspace/project-operations composition boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.project_operations import PROJECT_OPERATIONS_VERSION, ProjectOperationsRuntime
from runtime.workspace_manager import WorkspaceManager


@dataclass(slots=True)
class WorkspaceProjectBundle:
    """Own the pair that must be constructed and bound atomically.

    ``ProjectOperationsRuntime`` discovers the workspace manager through its
    owner, while ``ActionExecutor`` is created earlier and needs a late bind.
    Keeping that ceremony here prevents ``RuntimeCoordinator.__init__`` from
    being the only living document of the dependency order.
    """

    owner: Any
    workspace_manager: WorkspaceManager
    project_operations: ProjectOperationsRuntime
    logger: Any | None = None
    _closed: bool = False

    @classmethod
    def build(cls, owner: Any, *, logger: Any | None = None) -> "WorkspaceProjectBundle":
        previous_manager = getattr(owner, "workspace_manager", None)
        previous_operations = getattr(owner, "project_operations", None)
        manager = WorkspaceManager(owner, logger=logger)
        try:
            owner.workspace_manager = manager
            operations = ProjectOperationsRuntime(owner, logger=logger)
            owner.project_operations = operations
            binder = getattr(getattr(owner, "action_executor", None), "bind_project_runtime", None)
            if callable(binder):
                binder(operations)
        except Exception:
            owner.workspace_manager = previous_manager
            owner.project_operations = previous_operations
            raise

        bundle = cls(
            owner=owner,
            workspace_manager=manager,
            project_operations=operations,
            logger=logger,
        )
        bundle._log_boot()
        return bundle

    def snapshot(self) -> dict[str, Any]:
        active = self.workspace_manager.active_record()
        return {
            "version": PROJECT_OPERATIONS_VERSION,
            "eris_home": str(self.workspace_manager.home.root),
            "active_workspace_id": self.workspace_manager.active_workspace_id or "",
            "active_workspace_path": str(active.path) if active is not None else "",
            "capabilities": len(self.project_operations.contracts()),
            "closed": self._closed,
        }

    def close(self) -> None:
        if self._closed:
            return
        binder = getattr(getattr(self.owner, "action_executor", None), "bind_project_runtime", None)
        if callable(binder):
            binder(None)
        if getattr(self.owner, "project_operations", None) is self.project_operations:
            self.owner.project_operations = None
        if getattr(self.owner, "workspace_manager", None) is self.workspace_manager:
            self.owner.workspace_manager = None
        self._closed = True

    def _log_boot(self) -> None:
        logger = self.logger
        if logger is None:
            return
        try:
            logger.info(
                "[project-ops] loaded version=%s capabilities=%s mode=autonomous_project_scope",
                PROJECT_OPERATIONS_VERSION,
                len(self.project_operations.contracts()),
            )
            logger.info(
                "[workspace] eris_home=%s active=%s implicit_source_repo=false",
                self.workspace_manager.home.root,
                self.workspace_manager.active_workspace_id or "none",
            )
        except Exception:
            return


__all__ = ["WorkspaceProjectBundle"]

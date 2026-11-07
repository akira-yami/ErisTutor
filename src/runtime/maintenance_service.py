# -*- coding: utf-8 -*-
"""Dịch vụ bảo trì/reset cho Danger Zone của Eris.

Điểm quan trọng:
- Body KHÔNG tự xóa file trong ``memory_core``.
- Mọi thao tác đi qua Coordinator để reset cả object đang sống trong RAM.
- Thao tác phá dữ liệu luôn có bước preview -> confirmation token -> execute.
- Backup được tạo trước khi ghi/xóa, trừ thao tác chỉ tác động trạng thái phiên.

File này cố ý dùng adapter mềm/introspection vì repo có nhiều store độc lập và
một số subsystem có thể bị tắt bằng config. Một store không tồn tại không được
làm cả maintenance transaction chết theo, nhưng lỗi thật vẫn được báo trong ACK.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import zipfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MaintenanceActionSpec:
    action: str
    title: str
    description: str
    severity: str
    reversible: bool
    requires_confirmation: bool = True
    files: tuple[str, ...] = ()
    pause_runtime: bool = True
    cancel_pending: bool = True
    destructive: bool = False
    confirmation_mode: str = "simple"
    confirm_label: str = "Xác nhận"


@dataclass(slots=True)
class MaintenanceResult:
    action: str
    ok: bool = True
    changed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    backup_dir: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "ok": bool(self.ok and not self.errors),
            "changed": list(self.changed),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "backup_dir": self.backup_dir,
            "details": dict(self.details),
        }


ACTION_SPECS: dict[str, MaintenanceActionSpec] = {
    "refresh_conversation": MaintenanceActionSpec(
        "refresh_conversation", "Làm mới cuộc trò chuyện",
        "Xóa context của phiên hiện tại, không đụng ký ức dài hạn.",
        "warning", True, True,
        confirmation_mode="simple", confirm_label="Làm mới trò chuyện",
    ),
    "cancel_pending": MaintenanceActionSpec(
        "cancel_pending", "Hủy tác vụ đang chờ",
        "Hủy TTS, request nền và các queue chưa phát.",
        "safe", False, False,
        confirmation_mode="none", confirm_label="Hủy tác vụ",
    ),
    "reset_session_affect": MaintenanceActionSpec(
        "reset_session_affect", "Reset cảm xúc phiên",
        "Đưa affect và pressure runtime về baseline nhưng giữ quan hệ dài hạn.",
        "warning", True, True,
        ("active_state_snapshot.json",),
        confirmation_mode="simple", confirm_label="Reset cảm xúc",
    ),
    "reset_runtime_relationship": MaintenanceActionSpec(
        "reset_runtime_relationship", "Reset quan hệ runtime",
        "Đưa relationship state đang hoạt động về baseline, chưa xóa relational memory.",
        "warning", True, True,
        ("active_state_snapshot.json",),
        confirmation_mode="simple", confirm_label="Reset quan hệ phiên",
    ),
    "reset_session_state": MaintenanceActionSpec(
        "reset_session_state", "Reset trạng thái phiên",
        "Đưa cảm xúc và quan hệ runtime về baseline, giữ nguyên ký ức dài hạn.",
        "warning", True, True,
        ("active_state_snapshot.json",),
        confirmation_mode="simple", confirm_label="Reset phiên",
    ),
    "reset_user_model": MaintenanceActionSpec(
        "reset_user_model", "Quên mô hình về bạn",
        "Xóa user model, epistemic beliefs, lịch sử ToM và living user profile về người dùng.",
        "danger", True, True,
        ("user_model.json", "epistemic_v2.json", "tom_history.json",
         "living_user_profile.json", "living_user_authority.json", "living_user_episodes.json",
         "living_user_actions.json", "living_user_trace.jsonl", "living_user_turn_buffer.json"),
        destructive=True, confirmation_mode="typed", confirm_label="Quên mô hình",
    ),
    "reset_long_term_relationship": MaintenanceActionSpec(
        "reset_long_term_relationship", "Xóa quan hệ dài hạn",
        "Xóa relational memory, affective residue và existential anchor của quan hệ.",
        "danger", True, True,
        ("relational_memory.json", "affective_residue_memory.json", "anchor_state.json", "active_state_snapshot.json"),
        destructive=True, confirmation_mode="typed", confirm_label="Xóa quan hệ",
    ),
    "clear_repair_history": MaintenanceActionSpec(
        "clear_repair_history", "Xóa repair & xung đột",
        "Xóa repair memory và trạng thái rupture/repair đang hoạt động.",
        "danger", True, True,
        ("repair_memory.json", "active_state_snapshot.json"),
        destructive=True, confirmation_mode="typed", confirm_label="Xóa lịch sử",
    ),
    "reset_private_mind": MaintenanceActionSpec(
        "reset_private_mind", "Reset dòng suy nghĩ & bận tâm",
        "Xóa concern, thought stream, inner diary và queue private-mind.",
        "danger", True, True,
        ("private_concerns.json", "thought_stream.jsonl", "inner_diary.json", "internal_agenda.json",
         "public_topic_boundaries.json"),
        destructive=True, confirmation_mode="typed", confirm_label="Reset dòng suy nghĩ",
    ),
    "reset_private_life": MaintenanceActionSpec(
        "reset_private_life", "Reset đời sống riêng",
        "Đưa PrivateLifeCore về state baseline và xóa queue bề mặt.",
        "danger", True, True,
        ("private_life_state.json",),
        destructive=True, confirmation_mode="typed", confirm_label="Reset đời sống riêng",
    ),
    "reset_inner_state": MaintenanceActionSpec(
        "reset_inner_state", "Reset nội tâm hiện tại",
        "Xóa dòng suy nghĩ, bận tâm và private-life state hiện tại.",
        "warning", True, True,
        ("private_concerns.json", "thought_stream.jsonl", "inner_diary.json", "internal_agenda.json", "private_life_state.json"),
        confirmation_mode="simple", confirm_label="Reset nội tâm",
    ),
    "clear_vocal_cache": MaintenanceActionSpec(
        "clear_vocal_cache", "Dọn cache giọng",
        "Xóa cache giọng đã sinh; không đụng preset, key hoặc memory.",
        "safe", False, False,
        confirmation_mode="none", confirm_label="Dọn cache",
    ),
    "reindex_semantic_memory": MaintenanceActionSpec(
        "reindex_semantic_memory", "Dựng lại semantic index",
        "Không xóa ký ức; chỉ dựng lại embedding index từ semantic memory hiện có.",
        "safe", False, False, ("semantic_memory.json",), False, False, False,
        confirmation_mode="none", confirm_label="Dựng lại index",
    ),
    "export_backup": MaintenanceActionSpec(
        "export_backup", "Xuất backup ZIP",
        "Đóng gói memory_core thành ZIP, không sửa file nguồn.",
        "safe", False, False, (), False, False, False,
        confirmation_mode="none", confirm_label="Xuất backup",
    ),
    "quarantine_provider_key": MaintenanceActionSpec(
        "quarantine_provider_key", "Cách ly API key",
        "Tắt một key trong runtime theo fingerprint; không xóa secret khỏi ổ đĩa.",
        "warning", True, True, (), False, False, False,
        confirmation_mode="simple", confirm_label="Cách ly key",
    ),
    "restore_provider_key": MaintenanceActionSpec(
        "restore_provider_key", "Khôi phục API key",
        "Bỏ quarantine hoặc cooldown cho key đã kiểm tra lại.",
        "safe", False, False, (), False, False, False,
        confirmation_mode="none", confirm_label="Khôi phục key",
    ),
    "rotate_provider_key": MaintenanceActionSpec(
        "rotate_provider_key", "Xoay API key chủ động",
        "Chuyển con trỏ provider sang key sống kế tiếp mà không đánh dấu key hiện tại lỗi.",
        "safe", False, False, (), False, False, False,
        confirmation_mode="none", confirm_label="Xoay key",
    ),
    "reset_anchor_state": MaintenanceActionSpec(
        "reset_anchor_state", "Reset Anchor State",
        "Reset các switch và override của anchor qua maintenance protocol, có backup trước.",
        "warning", True, True, ("anchor_state.json", "anchor_journal.jsonl"), True, True, True,
        confirmation_mode="typed", confirm_label="Reset anchor",
    ),
    "factory_reset": MaintenanceActionSpec(
        "factory_reset", "Đặt lại toàn bộ Eris",
        "Reset các thành phần được chọn. API key, settings, raw event ledger và backup được giữ nguyên.",
        "critical", True, True,
        destructive=True, confirmation_mode="typed", confirm_label="Đặt lại toàn bộ",
    ),
}


class MaintenanceService:
    """Owner duy nhất cho các command Danger Zone."""

    TOKEN_TTL_SECONDS = 120.0
    PREVIEW_COALESCE_SECONDS = 2.5

    def __init__(self, owner: Any, *, logger: Any = None) -> None:
        self.owner = owner
        self.logger = logger
        self._lock = threading.RLock()
        self._tokens: dict[str, tuple[float, str, str, str]] = {}
        self._preview_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    @staticmethod
    def confirmation_phrase(action: str) -> str:
        return {
            "factory_reset": "ĐẶT LẠI TOÀN BỘ",
            "reset_user_model": "QUÊN MÔ HÌNH",
            "reset_long_term_relationship": "XÓA QUAN HỆ",
            "reset_private_mind": "RESET NỘI TÂM",
            "reset_private_life": "RESET ĐỜI SỐNG RIÊNG",
            "reset_inner_state": "RESET NỘI TÂM",
            "clear_repair_history": "XÓA XUNG ĐỘT",
            "reset_anchor_state": "RESET ANCHOR",
        }.get(str(action or "").strip().lower(), "XÁC NHẬN")

    # ------------------------------------------------------------------ API
    def catalog(self) -> dict[str, Any]:
        return {
            "actions": [
                {
                    "action": spec.action,
                    "title": spec.title,
                    "description": spec.description,
                    "severity": spec.severity,
                    "reversible": spec.reversible,
                    "requires_confirmation": spec.requires_confirmation,
                    "confirmation_mode": spec.confirmation_mode,
                    "confirm_label": spec.confirm_label,
                }
                for spec in ACTION_SPECS.values()
            ],
            "memory_root": str(self.memory_root),
            "vocal_cache": self._path_stat(self.vocal_cache_path),
            "provider_health": self._provider_health(),
            "semantic_index": self._semantic_index_health(),
            "backup_export_root": str((self.memory_root / "exports").resolve()),
            "backup_root": str((self.memory_root / "backups").resolve()),
            "backup_summary": self._backup_summary(),
        }

    def preview(self, action: str, *, options: dict[str, Any] | None = None) -> dict[str, Any]:
        action = str(action or "").strip().lower()
        spec = ACTION_SPECS.get(action)
        if spec is None:
            raise ValueError(f"unsupported maintenance action: {action}")
        options = dict(options or {})
        digest = self._request_digest(action, options)
        cache_key = f"{action}:{digest}"
        current = time.time()
        with self._lock:
            cached = self._preview_cache.get(cache_key)
            if cached is not None and cached[0] > current:
                # Idempotent preview: repeated UI redraw/click bursts reuse the
                # same confirmation token + manifest instead of minting a new
                # destructive request identity every few hundred milliseconds.
                return json.loads(json.dumps(cached[1], ensure_ascii=False))
        files = self._files_for_action(action, options=options)
        affected = [self._path_stat(path) for path in files]
        token = uuid.uuid4().hex
        phrase = self.confirmation_phrase(action)
        self._tokens[token] = (time.time() + self.TOKEN_TTL_SECONDS, action, digest, phrase)
        self._purge_expired_tokens()
        total_bytes = sum(int(item.get("bytes", 0) or 0) for item in affected)
        total_items = sum(int(item.get("items", 0) or 0) for item in affected)
        preview = {
            "action": action,
            "title": spec.title,
            "description": spec.description,
            "severity": spec.severity,
            "reversible": spec.reversible,
            "requires_confirmation": spec.requires_confirmation,
            "confirmation_mode": spec.confirmation_mode if spec.requires_confirmation else "none",
            "confirm_label": spec.confirm_label,
            "affected": affected,
            "total_bytes": total_bytes,
            "confirmation_token": token,
            "preview_id": token,
            "typed_confirmation": phrase if spec.requires_confirmation else "",
            "expires_in_seconds": int(self.TOKEN_TTL_SECONDS),
            "options": options,
            "summary": {
                "stores_touched": [Path(str(item.get("path") or "")).stem for item in affected],
                "items_estimated": total_items,
                "will_delete": False,
                "execute_will_delete": action in {
                    "refresh_conversation", "reset_user_model", "reset_long_term_relationship",
                    "clear_repair_history", "reset_private_mind", "reset_private_life",
                    "clear_vocal_cache", "factory_reset", "reset_anchor_state",
                },
                "restart_required": False,
            },
            "diff_excerpt": [
                f"{Path(str(item.get('path') or '')).name}: "
                f"{'sẽ được backup rồi thay đổi' if spec.reversible else 'sẽ được làm mới'}"
                for item in affected[:8]
            ] or self._preview_notes(action, options),
        }
        with self._lock:
            self._preview_cache[cache_key] = (
                current + self.PREVIEW_COALESCE_SECONDS,
                json.loads(json.dumps(preview, ensure_ascii=False)),
            )
            for key, (expires_at, _payload) in tuple(self._preview_cache.items()):
                if expires_at <= current:
                    self._preview_cache.pop(key, None)
        self._append_audit("preview", preview)
        self._record_observability("preview", preview)
        return preview

    def execute(
        self, action: str, *, confirmation_token: str = "",
        typed_confirmation: str = "", options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        action = str(action or "").strip().lower()
        spec = ACTION_SPECS.get(action)
        if spec is None:
            raise ValueError(f"unsupported maintenance action: {action}")
        options = dict(options or {})
        if spec.requires_confirmation:
            self._validate_token(
                action, confirmation_token, options, typed_confirmation=typed_confirmation
            )

        with self._lock:
            result = MaintenanceResult(action=action)
            daemon_states = self._pause_runtime_for_maintenance() if spec.pause_runtime else {}
            try:
                # Chỉ các thao tác làm đổi state hội thoại mới cần hủy output đang chờ.
                # Re-index, backup và quản trị key không được làm TTS đang nói cụt ngang.
                if spec.cancel_pending and action != "cancel_pending":
                    self._action_cancel_pending(result=result, options={})
                backup_files = self._files_for_action(action, options=options)
                if spec.reversible and backup_files:
                    result.backup_dir = self._backup_files(action, backup_files, result=result)
                handler = getattr(self, f"_action_{action}")
                handler(result=result, options=options)
            except Exception as exc:  # pragma: no cover - hàng rào cuối
                result.ok = False
                result.errors.append(f"{type(exc).__name__}: {exc}")
                self._log("exception", "[maintenance] %s failed", action)
            finally:
                if spec.pause_runtime:
                    self._resume_runtime_after_maintenance(daemon_states)
            payload = result.to_dict()
            payload["typed_confirmation_verified"] = bool(spec.requires_confirmation)
            self._append_audit("execute", payload)
            self._record_observability("execute", payload)
            return payload

    # -------------------------------------------------------------- actions
    def _action_refresh_conversation(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        from runtime.interrupt_ledger import InterruptLedger
        from runtime.public_turn_ledger import PublicTurnLedger
        from runtime.session_turn_ledger import SessionTurnLedger

        owner = self.owner
        owner.chat_history = []
        owner.public_turn_ledger = PublicTurnLedger()
        owner.session_turn_ledger = SessionTurnLedger()
        owner.interrupt_ledger = InterruptLedger()
        voice = getattr(owner, "voice_manager", None)
        if voice is not None and hasattr(voice, "ledger"):
            voice.ledger = owner.interrupt_ledger
        owner.pending_speech_interrupt_event = None
        owner.reply_context = type(getattr(owner, "reply_context", None))(previous_public_turn=None) if getattr(owner, "reply_context", None) is not None else None
        for name, value in {
            "last_chat_answer": "", "last_user_intent": "", "active_conversational_topic": "",
            "last_user_signature": "", "repeated_user_count": 0, "last_text": "",
            "last_semantic_scene_note": None,
        }.items():
            if hasattr(owner, name):
                setattr(owner, name, value)
        self._replace_or_clear(owner, "signal_buffer")
        self._replace_or_clear(owner, "user_context_stream")
        result.changed.extend(["chat_history", "public_turn_ledger", "session_turn_ledger", "interrupt_ledger", "conversation_context"])

    def _action_cancel_pending(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        owner = self.owner
        # TTS implementations trong repo đổi shape theo backend; gọi theo capability.
        for target_name in ("tts_task_manager", "tts_engine", "voice_manager", "stage_dispatcher"):
            target = getattr(owner, target_name, None)
            if target is None:
                continue
            for method_name in ("cancel_all", "cancel_current", "cancel", "stop_all"):
                method = getattr(target, method_name, None)
                if callable(method):
                    try:
                        method(reason="danger_zone")
                    except TypeError:
                        try:
                            method()
                        except Exception as exc:
                            result.warnings.append(f"{target_name}.{method_name}: {exc}")
                    except Exception as exc:
                        result.warnings.append(f"{target_name}.{method_name}: {exc}")
                    break
        for future_name in ("_private_mind_future", "_memory_digest_future", "_autonomous_future"):
            future = getattr(owner, future_name, None)
            cancel = getattr(future, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception as exc:
                    self._log("debug", "maintenance future cancellation failed for %s: %s", future_name, exc)
            if hasattr(owner, future_name):
                setattr(owner, future_name, None)
        for queue_name in ("private_mind_surface_queue", "private_life_surface_queue", "private_mind_inquiry_plan"):
            queue = getattr(owner, queue_name, None)
            if isinstance(queue, list):
                queue.clear()
        result.changed.extend(["tts_pending", "background_futures", "surface_queues"])

    def _action_reset_session_affect(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        from cortex.internal_state import PersistentInternalState

        current = self.owner._get_internal_state()
        baseline = PersistentInternalState()
        # Giữ relationship vì nút này chỉ reset affect/runtime pressure.
        baseline.relationship_dynamics = current.relationship_dynamics
        self.owner._set_internal_state(baseline)
        if hasattr(self.owner, "mood_score"):
            self.owner.mood_score = 50
        result.changed.append("internal_state.affect")

    def _action_reset_runtime_relationship(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        from cortex.relationship_dynamics import RelationshipDynamicsState

        state = self.owner._get_internal_state()
        state.relationship_dynamics = RelationshipDynamicsState()
        self.owner._set_internal_state(state)
        result.changed.append("internal_state.relationship_dynamics")

    def _action_reset_session_state(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        self._action_reset_session_affect(result=result, options=options)
        self._action_reset_runtime_relationship(result=result, options=options)
        result.changed.append("session_state_baseline")

    def _action_reset_user_model(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        loop = getattr(self.owner, "private_mind_loop", None)
        store = getattr(loop, "user_model_store", None)
        if store is not None:
            checkpoint = getattr(store, "checkpoint", None)
            if callable(checkpoint):
                checkpoint()
            reset = getattr(store, "reset_to_baseline", None)
            if callable(reset):
                reset()
            save = getattr(store, "save", None)
            if callable(save):
                save()
            result.changed.append("user_model_store")
        kernel = getattr(self.owner, "epistemic_kernel", None)
        reset_subject = getattr(kernel, "reset_subject", None)
        if callable(reset_subject):
            reset_subject("user")
            result.changed.append("epistemic:user")
        tom = getattr(getattr(self.owner, "memory_core", None), "tom_history", None)
        if tom is not None and callable(getattr(tom, "save", None)):
            tom.save([])
            result.changed.append("tom_history")
        self._clear_runtime_user_context(result)

    def _action_reset_long_term_relationship(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        self._action_reset_runtime_relationship(result=result, options=options)
        core = getattr(self.owner, "memory_core", None)
        for name in ("relational", "affective_residue"):
            store = getattr(core, name, None)
            save = getattr(store, "save", None)
            if callable(save):
                save([])
                result.changed.append(name)
        loop = getattr(self.owner, "private_mind_loop", None)
        anchor_store = getattr(getattr(loop, "anchor_system", None), "anchor_store", None)
        reset = getattr(anchor_store, "reset_to_baseline", None)
        if callable(reset):
            reset()
            result.changed.append("anchor_store")

    def _action_clear_repair_history(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        core = getattr(self.owner, "memory_core", None)
        repair = getattr(core, "repair", None)
        if repair is not None and callable(getattr(repair, "save", None)):
            repair.save([])
            result.changed.append("repair_memory")
        state = self.owner._get_internal_state()
        relation = getattr(state, "relationship_dynamics", None)
        if relation is not None:
            relation.rupture = 0.0
            relation.repair = 0.0
            relation.overread_correction = 0.0
            relation.last_event = "maintenance_reset"
        conversation = getattr(state, "conversation_dynamics", None)
        for attr in ("tension", "repair_pressure", "rupture_pressure"):
            if conversation is not None and hasattr(conversation, attr):
                setattr(conversation, attr, 0.0)
        self.owner._set_internal_state(state)
        result.changed.append("runtime_repair_state")

    def _action_reset_private_mind(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        loop = getattr(self.owner, "private_mind_loop", None)
        concern = getattr(loop, "concern_store", None)
        if concern is not None:
            concerns = getattr(concern, "_concerns", None)
            if isinstance(concerns, dict):
                concerns.clear()
            save = getattr(concern, "save", None)
            if callable(save):
                save()
            result.changed.append("private_concerns")
        continuity = getattr(loop, "continuity", None)
        if continuity is not None:
            entries = getattr(continuity, "_entries", None)
            if isinstance(entries, list):
                entries.clear()
            rewrite = getattr(continuity, "_rewrite_file", None)
            if callable(rewrite):
                rewrite()
            result.changed.append("thought_stream")
        agenda = getattr(self.owner, "internal_agenda", None)
        for attr in ("items", "_items", "stack", "_stack"):
            value = getattr(agenda, attr, None)
            if isinstance(value, list):
                value.clear()
        save = getattr(agenda, "save", None)
        if callable(save):
            try:
                save()
            except Exception as exc:
                result.warnings.append(f"internal_agenda.save: {exc}")
        for queue_name in ("private_mind_surface_queue", "private_mind_inquiry_plan"):
            queue = getattr(self.owner, queue_name, None)
            if isinstance(queue, list):
                queue.clear()
        self.owner.private_mind_digest = {}
        self.owner.private_mind_daemon_digest = {}
        self.owner.private_mind_speech_bias = {}
        self.owner.private_mind_active_state = {}
        self.owner.private_mind_thought_pressure = 0.0
        boundary_store = getattr(self.owner, "public_topic_boundary_store", None)
        clear_boundaries = getattr(boundary_store, "clear", None)
        if callable(clear_boundaries):
            try:
                clear_boundaries()
                result.changed.append("public_topic_boundaries")
            except Exception as exc:
                result.warnings.append(f"public_topic_boundaries.clear: {exc}")
        else:
            try:
                (self.memory_root / "public_topic_boundaries.json").unlink(missing_ok=True)
            except Exception as exc:
                result.warnings.append(f"public_topic_boundaries.unlink: {exc}")
        # Inner diary persister không có public reset; ghi baseline JSON an toàn.
        self._atomic_json(self.memory_root / "inner_diary.json", {
            "updated_at": time.time(),
            "last_tick_at": 0.0,
            "current": {},
            "inner_diary": {},
            "history": [],
        })
        persister = getattr(self.owner, "inner_diary_persister", None)
        if persister is not None:
            persister._last_write = 0.0
            persister._last_text = None
            persister._history = []
            persister._loaded = True
        result.changed.extend(["private_mind_runtime", "inner_diary"])

    def _action_reset_private_life(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        from cortex.private_life.contracts import PrivateLifeState

        daemon = getattr(self.owner, "private_life_daemon", None)
        core = getattr(daemon, "core", None)
        if core is not None:
            core.state = PrivateLifeState()
        queue = getattr(daemon, "surface_queue", None)
        if isinstance(queue, list):
            queue.clear()
        store = getattr(daemon, "store", None)
        if store is not None and callable(getattr(store, "save", None)):
            store.save(core.state if core is not None else PrivateLifeState())
        if daemon is not None:
            daemon.last_digest = {}
            daemon.last_tick = None
            daemon.last_tick_at = 0.0
        self.owner.private_life_surface_queue = []
        self.owner.private_life_digest = {}
        result.changed.extend(["private_life_state", "private_life_queue"])

    def _action_reset_inner_state(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        self._action_reset_private_mind(result=result, options=options)
        self._action_reset_private_life(result=result, options=options)
        result.changed.append("inner_state_baseline")

    def _action_clear_vocal_cache(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        path = self.vocal_cache_path
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        result.changed.append(str(path))

    def _action_reindex_semantic_memory(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        semantic = getattr(getattr(self.owner, "memory_core", None), "semantic", None)
        rebuild = getattr(semantic, "rebuild_embedding_index", None)
        if not callable(rebuild):
            result.ok = False
            result.errors.append("semantic store không hỗ trợ rebuild_embedding_index")
            return
        rebuilt = bool(rebuild())
        if not rebuilt:
            result.ok = False
            result.errors.append("embedding backend chưa bật hoặc dựng index thất bại")
            return
        result.changed.append("semantic_embedding_index")
        result.details["semantic_index"] = self._semantic_index_health()

    def _action_export_backup(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        root = self.memory_root
        export_root = root / "exports"
        export_root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = export_root / f"eris_memory_backup_{stamp}_{uuid.uuid4().hex[:6]}.zip"
        include_backups = bool(options.get("include_backups", False))
        count = 0
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for child in root.rglob("*"):
                if not child.is_file() or child == target:
                    continue
                rel = child.relative_to(root)
                if rel.parts and rel.parts[0] == "exports":
                    continue
                if not include_backups and rel.parts and rel.parts[0] == "backups":
                    continue
                archive.write(child, rel.as_posix())
                count += 1
            manifest = json.dumps({"created_at": time.time(), "source": str(root), "files": count}, ensure_ascii=False, indent=2)
            archive.writestr("_backup_manifest.json", manifest)
        result.backup_dir = str(target)
        result.changed.append("manual_backup_export")
        result.details.update({"backup_path": str(target), "files": count, "bytes": target.stat().st_size})

    def _provider_pool(self, options: dict[str, Any]):
        from llm.key_pool import get_key_pool
        provider = str(options.get("provider") or "gemini").strip().lower()
        if provider not in {"gemini", "groq"}:
            raise ValueError("provider phải là gemini hoặc groq")
        return provider, get_key_pool(provider)

    @staticmethod
    def _provider_target(options: dict[str, Any]) -> tuple[int | None, str]:
        raw_index = options.get("index")
        try:
            index = int(raw_index) if raw_index is not None and str(raw_index) != "" else None
        except (TypeError, ValueError):
            index = None
        return index, str(options.get("fingerprint") or "").strip()

    def _action_quarantine_provider_key(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        provider, pool = self._provider_pool(options)
        index, fingerprint = self._provider_target(options)
        if index is None and not fingerprint:
            snapshot = pool.snapshot()
            active = next((item for item in snapshot.get("keys", []) if item.get("active")), None)
            index = int(active.get("index")) if active else None
        seconds = float(options.get("seconds") or 30 * 24 * 3600)
        if not pool.quarantine(index=index, fingerprint=fingerprint, seconds=seconds, reason="danger_zone_manual"):
            result.ok = False
            result.errors.append("không tìm thấy key theo index/fingerprint")
            return
        result.changed.append(f"provider_key:{provider}:quarantined")
        result.details["provider_health"] = pool.snapshot()

    def _action_restore_provider_key(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        provider, pool = self._provider_pool(options)
        index, fingerprint = self._provider_target(options)
        if not pool.restore(index=index, fingerprint=fingerprint):
            result.ok = False
            result.errors.append("không tìm thấy key theo index/fingerprint")
            return
        result.changed.append(f"provider_key:{provider}:restored")
        result.details["provider_health"] = pool.snapshot()

    def _action_rotate_provider_key(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        provider, pool = self._provider_pool(options)
        raw = options.get("index")
        try:
            index = int(raw) if raw is not None else pool.index()
        except (TypeError, ValueError):
            index = pool.index()
        if not pool.force_rotate(from_index=index):
            result.ok = False
            result.errors.append("không có key sống kế tiếp để xoay")
            return
        result.changed.append(f"provider_key:{provider}:rotated")
        result.details["provider_health"] = pool.snapshot()

    def _action_reset_anchor_state(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        loop = getattr(self.owner, "private_mind_loop", None)
        system = getattr(loop, "anchor_system", None)
        author = getattr(system, "author", None)
        reset = getattr(author, "reset_all", None)
        if not callable(reset):
            result.ok = False
            result.errors.append("anchor author control chưa sẵn sàng")
            return
        reset()
        result.changed.append("anchor_author_controls")

    def _action_factory_reset(self, *, result: MaintenanceResult, options: dict[str, Any]) -> None:
        selected = options.get("components") or [
            "refresh_conversation", "reset_session_affect", "reset_user_model",
            "reset_long_term_relationship", "clear_repair_history",
            "reset_private_mind", "reset_private_life", "clear_vocal_cache",
        ]
        allowed = [str(item) for item in selected if str(item) in ACTION_SPECS and str(item) != "factory_reset"]
        # Queue đã được hủy ở execute() trước khi vào factory transaction.
        for action in allowed:
            handler = getattr(self, f"_action_{action}")
            handler(result=result, options={})
        result.changed.append("factory_reset_complete")

    # -------------------------------------------------------------- helpers
    def _pause_runtime_for_maintenance(self) -> dict[str, bool]:
        """Tạm dừng daemon đồng bộ để chúng không ghi state cũ ngược lại."""

        owner = self.owner
        owner._maintenance_in_progress = True
        states: dict[str, bool] = {}
        for name in ("private_mind_daemon", "private_life_daemon"):
            daemon = getattr(owner, name, None)
            config = getattr(daemon, "config", None)
            if config is None or not hasattr(config, "enabled"):
                continue
            states[name] = bool(config.enabled)
            config.enabled = False
        return states

    def _resume_runtime_after_maintenance(self, states: dict[str, bool]) -> None:
        owner = self.owner
        for name, enabled in states.items():
            config = getattr(getattr(owner, name, None), "config", None)
            if config is not None and hasattr(config, "enabled"):
                config.enabled = enabled
        owner._maintenance_in_progress = False

    @property
    def memory_root(self) -> Path:
        core = getattr(self.owner, "memory_core", None)
        raw = str(getattr(core, "base_dir", "") or os.getenv("ERIS_MEMORY_CORE_DIR", "memory_core") or "memory_core")
        return Path(raw).expanduser().resolve()

    @property
    def vocal_cache_path(self) -> Path:
        raw = str(os.getenv("ERIS_TTS_VOCAL_CACHE_DIR", "") or "").strip()
        return Path(raw).expanduser().resolve() if raw else (Path.cwd() / "voice_presets" / "tts_vocal_cache").resolve()

    def _files_for_action(self, action: str, *, options: dict[str, Any]) -> list[Path]:
        if action == "clear_vocal_cache":
            return [self.vocal_cache_path]
        if action == "export_backup":
            return [self.memory_root]
        if action in {"quarantine_provider_key", "restore_provider_key", "rotate_provider_key"}:
            return []
        if action == "factory_reset":
            selected = options.get("components") or []
            files: list[Path] = []
            for component in selected:
                if component == "factory_reset":
                    continue
                files.extend(self._files_for_action(str(component), options={}))
            return self._unique_paths(files)
        spec = ACTION_SPECS[action]
        return self._unique_paths([self.memory_root / name for name in spec.files])

    def _provider_health(self) -> dict[str, Any]:
        try:
            from llm.key_pool import get_key_pool
            return {name: get_key_pool(name).snapshot() for name in ("gemini", "groq")}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def _semantic_index_health(self) -> dict[str, Any]:
        semantic = getattr(getattr(self.owner, "memory_core", None), "semantic", None)
        index = getattr(semantic, "embedding_index", None)
        return {
            "store_ready": semantic is not None,
            "embedding_backend_ready": index is not None,
            "rebuild_supported": callable(getattr(semantic, "rebuild_embedding_index", None)),
        }

    def _backup_summary(self) -> dict[str, Any]:
        """Tóm tắt backup cho UI mà không bắt Body tự quét memory_core."""

        candidates: list[tuple[float, Path, str, int]] = []
        backup_root = self.memory_root / "backups"
        export_root = self.memory_root / "exports"

        if backup_root.exists():
            for path in backup_root.iterdir():
                if not path.exists():
                    continue
                created_at = path.stat().st_mtime
                action = "maintenance"
                manifest = path / "manifest.json" if path.is_dir() else None
                if manifest is not None and manifest.exists():
                    try:
                        payload = json.loads(manifest.read_text(encoding="utf-8"))
                        created_at = float(payload.get("created_at") or created_at)
                        action = str(payload.get("action") or action)
                    except Exception as exc:
                        self._log("debug", "maintenance manifest parse failed for %s: %s", manifest, exc)
                stat = self._path_stat(path)
                candidates.append((created_at, path, action, int(stat.get("bytes", 0) or 0)))

        if export_root.exists():
            for path in export_root.glob("*.zip"):
                try:
                    candidates.append((path.stat().st_mtime, path, "export_backup", path.stat().st_size))
                except OSError:
                    continue

        candidates.sort(key=lambda item: item[0], reverse=True)
        total_bytes = sum(item[3] for item in candidates)
        latest = None
        if candidates:
            created_at, path, action, size = candidates[0]
            latest = {
                "path": str(path),
                "name": path.name,
                "created_at": created_at,
                "action": action,
                "bytes": size,
                "is_zip": path.is_file(),
            }
        return {
            "count": len(candidates),
            "total_bytes": total_bytes,
            "latest": latest,
        }

    def _preview_notes(self, action: str, options: dict[str, Any]) -> list[str]:
        if action == "export_backup":
            return ["memory_core: tạo ZIP mới, không sửa file nguồn"]
        if action == "reindex_semantic_memory":
            return ["semantic_memory: giữ nguyên dữ liệu, chỉ dựng lại embedding index"]
        if action in {"quarantine_provider_key", "restore_provider_key", "rotate_provider_key"}:
            provider = str(options.get("provider") or "gemini")
            fingerprint = str(options.get("fingerprint") or "active")
            return [f"{provider}: thao tác runtime trên key {fingerprint}; secret trên đĩa được giữ nguyên"]
        return ["runtime state: thao tác không có file tĩnh để liệt kê"]

    def _record_observability(self, phase: str, payload: dict[str, Any]) -> None:
        try:
            from runtime.observability import ensure_observability
            ensure_observability(self.owner).record_maintenance(phase=phase, payload=payload)
        except Exception as exc:
            self._log("debug", "maintenance observability emission failed: %s", exc)

    def _backup_files(self, action: str, files: Iterable[Path], *, result: MaintenanceResult) -> str:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = self.memory_root / "backups" / f"maintenance_{stamp}_{action}_{uuid.uuid4().hex[:6]}"
        copied = 0
        for path in files:
            try:
                if not path.exists():
                    continue
                rel = path.name if path.parent == self.memory_root else path.as_posix().replace(":", "_").lstrip("/")
                target = backup_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if path.is_dir():
                    shutil.copytree(path, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(path, target)
                copied += 1
            except Exception as exc:
                result.warnings.append(f"backup {path}: {exc}")
        if copied:
            manifest = {
                "action": action,
                "created_at": time.time(),
                "files": [str(path) for path in files],
            }
            self._atomic_json(backup_dir / "manifest.json", manifest)
            return str(backup_dir)
        return ""

    def _validate_token(
        self, action: str, token: str, options: dict[str, Any], *, typed_confirmation: str = ""
    ) -> None:
        self._purge_expired_tokens()
        payload = self._tokens.pop(str(token or ""), None)
        if payload is None:
            raise ValueError("confirmation token missing, expired or already used")
        expires, token_action, digest, expected_phrase = payload
        if time.time() > expires or token_action != action or digest != self._request_digest(action, options):
            raise ValueError("confirmation token does not match this maintenance request")
        if str(typed_confirmation or "").strip() != expected_phrase:
            raise ValueError("typed confirmation does not match preview")

    def _append_audit(self, phase: str, payload: dict[str, Any]) -> None:
        """Audit append-only; không để lỗi log phá transaction maintenance."""

        try:
            path = self.memory_root / "maintenance_audit.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": time.time(),
                "phase": str(phase or ""),
                "payload": payload,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception as exc:
            self._log("warning", "[maintenance] audit append failed: %s", exc)

    def _request_digest(self, action: str, options: dict[str, Any]) -> str:
        raw = json.dumps({"action": action, "options": options}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _purge_expired_tokens(self) -> None:
        now = time.time()
        self._tokens = {token: value for token, value in self._tokens.items() if value[0] >= now}

    def _path_stat(self, path: Path) -> dict[str, Any]:
        path = Path(path)
        if not path.exists():
            return {"path": str(path), "exists": False, "bytes": 0, "items": 0, "is_dir": False}
        if path.is_file():
            return {"path": str(path), "exists": True, "bytes": path.stat().st_size, "items": 1, "is_dir": False}
        total = 0
        items = 0
        for child in path.rglob("*"):
            if child.is_file():
                items += 1
                try:
                    total += child.stat().st_size
                except OSError:
                    pass
        return {"path": str(path), "exists": True, "bytes": total, "items": items, "is_dir": True}

    def _clear_runtime_user_context(self, result: MaintenanceResult) -> None:
        for name in ("rolling_user_context", "user_context_stream"):
            self._replace_or_clear(self.owner, name)
        self.owner.last_user_signature = ""
        self.owner.last_user_intent = ""

        # Reset LivingUserMemoryService in RAM if present
        core = getattr(self.owner, "memory_core", None)
        living_user = getattr(core, "living_user", None) if core is not None else getattr(self.owner, "living_user", None)
        if living_user is not None:
            try:
                if hasattr(living_user, "profile_store"):
                    living_user.profile_store._records.clear()
                    living_user.profile_store._history.clear()
                    living_user.profile_store._audit.clear()
                    living_user.profile_store._version = 0
                    living_user.profile_store.save()
                if hasattr(living_user, "authority_ledger"):
                    living_user.authority_ledger._events.clear()
                    living_user.authority_ledger.save()
                if hasattr(living_user, "episode_store"):
                    living_user.episode_store._episodes.clear()
                    living_user.episode_store.save()
                if hasattr(living_user, "action_store"):
                    living_user.action_store._goals.clear()
                    living_user.action_store._commitments.clear()
                    living_user.action_store._workflows.clear()
                    living_user.action_store.save()
                if hasattr(living_user, "_turn_buffer"):
                    with getattr(living_user, "_turn_buffer_lock", threading.RLock()):
                        living_user._turn_buffer.clear()
                        if hasattr(living_user, "_save_turn_buffer_locked"):
                            living_user._save_turn_buffer_locked()
                result.changed.append("living_user_memory_service")
            except Exception as exc:
                result.warnings.append(f"living_user_clear: {exc}")

        # Delete physical living user files from memory_root
        for fname in (
            "living_user_profile.json", "living_user_authority.json",
            "living_user_episodes.json", "living_user_actions.json",
            "living_user_trace.jsonl", "living_user_turn_buffer.json"
        ):
            fpath = self.memory_root / fname
            if fpath.exists():
                try:
                    fpath.unlink()
                    result.changed.append(fname)
                except Exception as exc:
                    result.warnings.append(f"unlink_{fname}: {exc}")

        # Reset Godot's UI panel cache file living_user_panel_cache.json if present
        appdata = os.getenv("APPDATA")
        if appdata:
            godot_cache = Path(appdata) / "Godot" / "app_userdata" / "Eris" / "living_user_panel_cache.json"
            if godot_cache.exists():
                try:
                    empty_payload = {
                        "schema_version": 2,
                        "authority_mode": "canonical_ledger",
                        "summary_text": "ERIS chưa có đủ thông tin bền vững để tổng hợp về bạn.",
                        "counts": {
                            "total": 0, "confirmed": 0, "observed": 0, "inferred": 0,
                            "uncertain": 0, "goals": 0, "commitments": 0, "workflows": 0
                        },
                        "records": [], "goals": [], "commitments": [], "workflows": [],
                        "project_id": "", "generated_at": time.time()
                    }
                    self._atomic_json(godot_cache, empty_payload)
                    result.changed.append("living_user_panel_cache.json")
                except Exception as exc:
                    result.warnings.append(f"living_user_panel_cache_reset: {exc}")

        result.changed.append("runtime_user_context")

    @staticmethod
    def _replace_or_clear(owner: Any, name: str) -> None:
        value = getattr(owner, name, None)
        clear = getattr(value, "clear", None)
        if callable(clear):
            try:
                clear()
                return
            except Exception as exc:
                _LOG.debug("maintenance container clear failed for %s: %s", name, exc)
        if value is not None:
            try:
                setattr(owner, name, type(value)())
            except Exception as exc:
                _LOG.debug("maintenance container replacement failed for %s: %s", name, exc)

    @staticmethod
    def _atomic_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{int(time.time() * 1000)}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _unique_paths(paths: Iterable[Path]) -> list[Path]:
        out: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            normalized = str(Path(path).resolve())
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(Path(normalized))
        return out

    def _log(self, level: str, message: str, *args: Any) -> None:
        method = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(method):
            try:
                method(message, *args)
            except Exception as exc:
                _LOG.debug("maintenance logger failed: %s", exc, exc_info=exc)


__all__ = ["ACTION_SPECS", "MaintenanceActionSpec", "MaintenanceResult", "MaintenanceService"]

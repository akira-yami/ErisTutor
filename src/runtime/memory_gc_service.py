# -*- coding: utf-8 -*-
"""Daemon dọn memory định kỳ khi runtime rảnh.

Luồng mới:
1. Chạy maintenance deterministic/canonical hiện có.
2. Dùng service Gemini Web curator để rà semantic memory.
3. Dọn Living User Memory:
   - rule deterministic xử lý null/open-loop chắc chắn;
   - embedding gom duplicate/near-duplicate;
   - Gemini Web curator có toàn quyền cấu trúc working profile: delete/archive/merge/
     rewrite/canonicalize, kể cả representation từng được user xác nhận;
   - store giữ audit/undo và evidence để quyền cấu trúc không biến thành quyền bịa fact.
4. Thu gọn undo history và anchor journal.

Mặc định chạy mỗi 10 phút. Không chạy khi chat bận. V68 bảo vệ *meaning/evidence*,
không bảo vệ hình dạng record rác. User không được thuê làm DBA chỉ vì extractor từng
đẻ ra một path xấu.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_INTERVAL_SEC = 10 * 60  # 10 phút theo yêu cầu vận hành
_DEFAULT_ANCHOR_JOURNAL_MAX_MB = 50
_DEFAULT_DS_REVIEW_LIMIT = 80


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except Exception:
        return int(default)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


# ── Prompt semantic memory ────────────────────────────────────────────────────

_SEMANTIC_GC_SYSTEM = """You are the semantic-memory critic for ERIS.
Classify only the supplied entries. Output ONLY JSON.

Evidence rules:
- Model confidence is not evidence.
- KEEP facts with a canonical fact_key or direct durable evidence.
- DROP null/empty claims, transient greetings, one-off scene details, resolved tasks,
  unsupported emotional inference, and paraphrases that add no durable fact.
- When uncertain and evidence exists, KEEP. Do not invent replacements.

Schema:
{
  "decisions": [
    {"id": "<entry_id>", "action": "keep" | "drop", "reason": "short reason"}
  ]
}
"""

_SEMANTIC_GC_USER = """Semantic memory entries to review ({count} items):
{entries}

Return a decision for every supplied entry."""

_ACTION_GC_SYSTEM = """You review ERIS action memory. Output JSON only.
Use meaning and the supplied evidence, never word matching. Each record has an
actor, scope, timestamps and evidence IDs. Review each record on its own evidence.
Keep real unfinished goals/commitments/workflows. Age or silence alone does not
prove completion or abandonment. Distinguish quoted speech, introspection,
roleplay and untriggered conditional offers from real pending work.
Archive false extractions or records whose supplied evidence supports retirement.
When the evidence is incomplete, keep the record or mark candidate for review.
Do not invent new actions, facts, receipts or completion/cancellation events.
Schema: {"decisions":[{"id":"supplied ID","status":"keep|candidate|archived","reason":"evidence-based explanation"}]}.
"""


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class MemoryGCResult:
    maintenance_ran: bool = False
    ds_reviewed: int = 0
    ds_dropped: int = 0
    living_reviewed: int = 0
    living_applied: int = 0
    living_archived: int = 0
    living_deleted: int = 0
    living_weakened: int = 0
    living_merged: int = 0
    living_rewritten: int = 0
    living_hidden: int = 0
    living_canonicalized: int = 0
    living_history_removed: int = 0
    actions_reviewed: int = 0
    actions_applied: int = 0
    pruned_total: int = 0
    journal_compacted: bool = False
    behavior_removed: int = 0
    thought_removed: int = 0
    duration_sec: float = 0.0
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "maintenance_ran": self.maintenance_ran,
            "ds_reviewed": self.ds_reviewed,
            "ds_dropped": self.ds_dropped,
            "living_reviewed": self.living_reviewed,
            "living_applied": self.living_applied,
            "living_archived": self.living_archived,
            "living_deleted": self.living_deleted,
            "living_weakened": self.living_weakened,
            "living_merged": self.living_merged,
            "living_rewritten": self.living_rewritten,
            "living_hidden": self.living_hidden,
            "living_canonicalized": self.living_canonicalized,
            "living_history_removed": self.living_history_removed,
            "actions_reviewed": self.actions_reviewed,
            "actions_applied": self.actions_applied,
            "pruned_total": self.pruned_total,
            "journal_compacted": self.journal_compacted,
            "behavior_removed": self.behavior_removed,
            "thought_removed": self.thought_removed,
            "duration_sec": round(self.duration_sec, 2),
            "warnings": self.warnings[:16],
            "notes": self.notes[:24],
        }


# ── Daemon ────────────────────────────────────────────────────────────────────

class MemoryGCService:
    """Thread daemon dọn memory định kỳ."""

    def __init__(
        self,
        *,
        memory_core: Any,
        llm_service: Any | None = None,
        idle_probe: Any | None = None,
        interval_seconds: float | None = None,
        ds_review_limit: int | None = None,
        anchor_journal_max_mb: float | None = None,
    ) -> None:
        self.memory_core = memory_core
        # Tùy chọn: do coordinator nối sau khi khởi tạo. Không thuộc chân lý memory.
        self.portrait_presenter = None
        self.llm_service = llm_service
        self.idle_probe = idle_probe
        self.interval_seconds = float(
            interval_seconds
            if interval_seconds is not None
            else _env_float("ERIS_MEMORY_GC_INTERVAL_SEC", _DEFAULT_INTERVAL_SEC)
        )
        self.ds_review_limit = int(
            ds_review_limit
            if ds_review_limit is not None
            else _env_int("ERIS_MEMORY_GC_DS_LIMIT", _DEFAULT_DS_REVIEW_LIMIT)
        )
        self.anchor_journal_max_mb = float(
            anchor_journal_max_mb
            if anchor_journal_max_mb is not None
            else _env_float("ERIS_MEMORY_GC_JOURNAL_MAX_MB", _DEFAULT_ANCHOR_JOURNAL_MAX_MB)
        )
        self._enabled = _env_flag("ERIS_MEMORY_GC_ENABLED", True)
        base_dir = str(getattr(memory_core, "base_dir", "") or os.getenv("ERIS_MEMORY_CORE_DIR", "memory_core"))
        self._settings_path = os.path.join(base_dir, "memory_cleanup_settings.json")
        self._load_runtime_settings()
        self._force_review = False
        self._gc_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._manual_thread: threading.Thread | None = None
        self._last_run_at = 0.0
        self._last_living_user_review_version = -1
        self._last_living_user_review_at = 0.0
        self.last_result: dict[str, Any] = {}
        self.run_count = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._enabled:
            logger.info("[memory_gc] disabled via ERIS_MEMORY_GC_ENABLED=0")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="ERIS-MemoryGC", daemon=True)
        self._thread.start()
        logger.info("[memory_gc] started; interval=%.0fs provider=gemini_web role=curator", self.interval_seconds)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    @property
    def auto_cleanup_enabled(self) -> bool:
        return bool(self._enabled)

    def set_auto_cleanup(self, enabled: bool) -> bool:
        """Bật/tắt daemon từ panel và persist lựa chọn cạnh memory core."""
        self._enabled = bool(enabled)
        self._persist_runtime_settings()
        if self._enabled:
            self.start()
        return self._enabled

    def run_once(self, *, force: bool = True) -> MemoryGCResult:
        """Chạy cleanup thủ công. ``force`` bỏ throttle version của Living User.

        Gemini Web curator là authority cấu trúc của working profile; store chỉ kiểm tra
        operation có hợp lệ và giữ audit/evidence. Đây là root đối với *representation*,
        không phải giấy phép bịa meaning trái evidence.
        """
        previous = self._force_review
        self._force_review = bool(force)
        try:
            result = self._run_gc()
            self.last_result = result.to_dict()
            self._last_run_at = time.time()
            return result
        finally:
            self._force_review = previous

    def request_cleanup(self) -> dict[str, Any]:
        """Enqueue cleanup thủ công, không block protocol/chat thread."""
        if self._manual_thread is not None and self._manual_thread.is_alive():
            return {"accepted": False, "reason": "cleanup_already_running"}
        if self._gc_lock.locked():
            return {"accepted": False, "reason": "cleanup_busy"}
        self._manual_thread = threading.Thread(
            target=self._run_manual_cleanup_job,
            name="ERIS-MemoryGC-Manual",
            daemon=True,
        )
        self._manual_thread.start()
        return {"accepted": True, "reason": "manual_cleanup_started"}

    def _run_manual_cleanup_job(self) -> None:
        previous = self._force_review
        self._force_review = True
        try:
            result = self._run_gc()
            self.last_result = result.to_dict()
            self._last_run_at = time.time()
            self.run_count += 1
            logger.info(
                "[memory_gc] manual cleanup finished reviewed=%d dropped=%d living_applied=%d transient=%d/%d",
                result.ds_reviewed + result.living_reviewed,
                result.ds_dropped,
                result.living_applied,
                result.behavior_removed,
                result.thought_removed,
            )
        finally:
            self._force_review = previous

    def status_snapshot(self) -> dict[str, Any]:
        running = bool(
            (self._manual_thread is not None and self._manual_thread.is_alive())
            or self._gc_lock.locked()
        )
        return {
            "available": True,
            "auto_cleanup": bool(self._enabled),
            "running": running,
            "interval_seconds": float(self.interval_seconds),
            "last_run_at": float(self._last_run_at),
            "run_count": int(self.run_count),
            "last_result": dict(self.last_result or {}),
            "provider": "gemini_web",
        }

    def _load_runtime_settings(self) -> None:
        try:
            if not os.path.isfile(self._settings_path):
                return
            data = json.loads(open(self._settings_path, "r", encoding="utf-8").read())
            if isinstance(data, dict) and "auto_cleanup" in data:
                self._enabled = bool(data.get("auto_cleanup"))
        except Exception:
            pass

    def _persist_runtime_settings(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._settings_path) or ".", exist_ok=True)
            tmp = self._settings_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "auto_cleanup": bool(self._enabled)}, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, self._settings_path)
        except Exception as exc:
            logger.warning("[memory_gc] persist cleanup settings failed: %s", exc)

    # ── Loop ───────────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # Delay để không tranh VRAM/CPU với bootstrap model.
        startup_delay = _env_float("ERIS_MEMORY_GC_STARTUP_DELAY_SEC", 120.0)
        self._stop.wait(timeout=startup_delay)
        while not self._stop.is_set():
            try:
                now = time.time()
                if self._enabled and now - self._last_run_at >= self.interval_seconds and self._is_idle():
                    result = self._run_gc()
                    self._last_run_at = time.time()
                    self.last_result = result.to_dict()
                    self.run_count += 1
                    logger.info(
                        "[memory_gc] run #%d maintenance=%s semantic_drop=%d "
                        "living(applied=%d archive=%d delete=%d weaken=%d merge=%d rewrite=%d canonicalize=%d hide=%d) pruned=%d (%.1fs)",
                        self.run_count,
                        result.maintenance_ran,
                        result.ds_dropped,
                        result.living_applied,
                        result.living_archived,
                        result.living_deleted,
                        result.living_weakened,
                        result.living_merged,
                        result.living_rewritten,
                        result.living_canonicalized,
                        result.living_hidden,
                        result.pruned_total,
                        result.duration_sec,
                    )
            except Exception as exc:
                logger.warning("[memory_gc] loop error: %s", exc)
            self._stop.wait(timeout=30.0)

    def _is_idle(self) -> bool:
        probe = self.idle_probe
        if probe is None:
            return True
        try:
            return bool(probe())
        except TypeError:
            try:
                quiet_for = time.time() - self._last_run_at
                return bool(probe(quiet_for=quiet_for, now=time.time()))
            except Exception:
                return True
        except Exception:
            return True

    # ── GC logic ───────────────────────────────────────────────────────────────

    def _run_gc(self) -> MemoryGCResult:
        # Manual button và daemon có thể đụng cùng lúc. Chỉ một cleanup transaction
        # được chạy; caller thứ hai nhận report busy thay vì hai curator cùng dọn một store.
        if not self._gc_lock.acquire(blocking=False):
            return MemoryGCResult(warnings=["cleanup_busy"], notes=["another_cleanup_is_running"])
        try:
            return self._run_gc_unlocked()
        finally:
            self._gc_lock.release()

    def _run_gc_unlocked(self) -> MemoryGCResult:
        result = MemoryGCResult()
        t0 = time.time()

        # 1. Maintenance canonical hiện có: decay, prune, consolidate.
        try:
            from memory.memory_maintenance import MemoryMaintenanceRunner

            runner = MemoryMaintenanceRunner(
                self.memory_core,
                destructive=True,
                llm_service=self.llm_service,
            )
            report = runner.run(budget="normal", reason="memory_gc_periodic", destructive=True)
            result.maintenance_ran = True
            pruned = report.pruned if isinstance(report.pruned, dict) else {}
            result.pruned_total = int(pruned.get("dropped_total", 0))
            result.notes.extend(list(getattr(report, "notes", []) or [])[:8])
            result.warnings.extend(list(getattr(report, "warnings", []) or [])[:4])
        except Exception as exc:
            result.warnings.append(f"maintenance_failed:{type(exc).__name__}:{exc}")
            logger.warning("[memory_gc] maintenance runner failed: %s", exc)

        curator = self._get_curator_client()

        # 2. Semantic memory critic.
        try:
            self._run_semantic_review(result, curator)
        except Exception as exc:
            result.warnings.append(f"semantic_review_failed:{type(exc).__name__}:{exc}")
            logger.warning("[memory_gc] semantic review failed: %s", exc)

        # 3. Living-user curator: embedding gom cụm, Gemini Web phán, store áp dụng.
        try:
            self._run_living_user_review(result, curator)
        except Exception as exc:
            result.warnings.append(f"living_review_failed:{type(exc).__name__}:{exc}")
            logger.warning("[memory_gc] living-user review failed: %s", exc)

        try:
            self._run_action_review(result, curator)
        except Exception as exc:
            result.warnings.append(f"action_review_failed:{type(exc).__name__}")

        # 4. Dọn transient cognition/audit-adjacent stores. Đây là deterministic
        # hygiene, không biến thought/behavior thành semantic memory.
        try:
            self._compact_transient_stores(result)
        except Exception as exc:
            result.warnings.append(f"transient_compact_failed:{type(exc).__name__}:{exc}")

        # 5. Compact anchor journal nếu quá lớn.
        try:
            self._compact_anchor_journal(result)
        except Exception as exc:
            result.warnings.append(f"journal_compact_failed:{type(exc).__name__}:{exc}")

        # Lần dọn nền cũng kiểm tra chân dung; nếu evidence đổi, xếp Gemini
        # viết lại ở worker riêng. KHÔNG đợi Gemini trả lời trong GC thread.
        if self.portrait_presenter is not None:
            try:
                from runtime.living_user_panel import build_memory_panel_snapshot
                living_user = getattr(self.memory_core, "living_user", None)
                if living_user is not None:
                    build_memory_panel_snapshot(living_user, portrait_presenter=self.portrait_presenter)
            except Exception as exc:
                logger.warning("[memory_gc] Không xếp được refresh chân dung: %s", exc)
        result.duration_sec = time.time() - t0
        return result

    def _run_semantic_review(self, result: MemoryGCResult, critic: Any | None) -> None:
        if critic is None:
            result.notes.append("semantic_review_skipped:no_critic_worker")
            return

        semantic_store = getattr(self.memory_core, "semantic", None)
        if semantic_store is None or not hasattr(semantic_store, "load"):
            result.notes.append("semantic_review_skipped:no_semantic_store")
            return

        items = list(semantic_store.load())
        candidates = [item for item in items if self._is_semantic_gc_candidate(item)][: self.ds_review_limit]
        if not candidates:
            result.notes.append("semantic_review_skipped:no_candidates")
            return

        raw = self._call_model(
            critic,
            _SEMANTIC_GC_SYSTEM,
            _SEMANTIC_GC_USER.format(
                count=len(candidates),
                entries=self._format_semantic_entries(candidates),
            ),
            max_tokens=1600,
        )
        if not raw:
            result.warnings.append("semantic_review_no_response")
            return

        decisions = self._parse_decisions(raw)
        if not decisions:
            result.warnings.append("semantic_review_parse_failed")
            return

        drop_ids = {
            str(row.get("id"))
            for row in decisions
            if str(row.get("action", "")).strip().lower() == "drop"
        }
        # Chỉ drop ID trong candidate batch. Critic không được lén ghi tên hàng xóm.
        allowed_ids = {self._item_id(item) for item in candidates}
        drop_ids &= allowed_ids
        kept = [item for item in items if self._item_id(item) not in drop_ids]
        actually_dropped = len(items) - len(kept)
        if actually_dropped > 0 and hasattr(semantic_store, "save"):
            semantic_store.save(kept)
            result.ds_dropped = actually_dropped
            result.notes.append(f"semantic_dropped:{actually_dropped}")
        result.ds_reviewed = len(candidates)

    def _run_living_user_review(self, result: MemoryGCResult, critic: Any | None) -> None:
        living_user = getattr(self.memory_core, "living_user", None)
        if living_user is None:
            result.notes.append("living_review_skipped:no_living_user")
            return
        store = getattr(living_user, "profile_store", None)
        version = int(getattr(store, "version", -1))
        now = time.time()
        max_age = _env_float("ERIS_MEMORY_GC_LIVING_REVIEW_MAX_AGE_SEC", 24 * 3600.0)
        if (
            not self._force_review
            and version == self._last_living_user_review_version
            and now - self._last_living_user_review_at < max_age
        ):
            result.notes.append("living_review_skipped:profile_unchanged")
            return

        from memory.living_user.curator import LivingUserMemoryCurator

        reviewer = None
        if critic is not None:
            reviewer = lambda system, user, max_tokens: self._call_model(
                critic, system, user, max_tokens=max_tokens
            )
        curator = LivingUserMemoryCurator(
            living_user,
            reviewer=reviewer,
            embedding_index=getattr(living_user, "embedding_index", None),
        )
        report = curator.run()
        result.living_reviewed = report.reviewed
        result.living_applied = report.applied
        result.living_archived = report.archived
        result.living_deleted = getattr(report, "deleted", 0)
        result.living_weakened = report.weakened
        result.living_merged = report.merged
        result.living_rewritten = getattr(report, "rewritten", 0)
        result.living_hidden = getattr(report, "hidden", 0)
        result.living_canonicalized = getattr(report, "canonicalized", 0)
        result.living_history_removed = report.history_removed
        result.warnings.extend(report.warnings[:6])
        result.notes.extend(report.notes[:8])
        if report.skipped_reason:
            result.notes.append(f"living_review:{report.skipped_reason}")

        # Nếu critic tạm chết thì thử lại ở chu kỳ kế tiếp, không khóa 24 giờ chỉ
        # vì một process worker vừa hắt hơi.
        if report.skipped_reason != "no_critic_reviewer" and not report.warnings:
            self._last_living_user_review_version = int(getattr(store, "version", version))
            self._last_living_user_review_at = now

    def _run_action_review(self, result: MemoryGCResult, curator: Any | None) -> None:
        living = getattr(self.memory_core, "living_user", None)
        store = getattr(living, "action_store", None)
        if curator is None or store is None:
            result.notes.append("action_review_skipped:no_curator_or_store")
            return
        rows = [
            (kind, record) for kind, records in (
                ("goal", store.goals()), ("commitment", store.commitments()),
                ("workflow", store.workflows()),
            ) for record in records
        ][:max(1, _env_int("ERIS_MEMORY_GC_ACTION_LIMIT", 40))]
        if not rows:
            return
        raw = self._call_model(curator, _ACTION_GC_SYSTEM, json.dumps({
            "now": time.time(), "records": [{"kind": kind, **record.to_dict()} for kind, record in rows],
        }, ensure_ascii=False), max_tokens=2400)
        from memory.living_user.runtime_adapter import _parse_json
        from memory.living_user.action_memory import ActionMemoryOperation
        parsed = _parse_json(raw or "")
        decisions = parsed.get("decisions") if isinstance(parsed, dict) else None
        if not isinstance(decisions, list):
            result.warnings.append("action_review_parse_failed")
            return
        by_id = {record.id: (kind, record) for kind, record in rows}
        proposals = []
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            pair = by_id.get(str(decision.get("id") or ""))
            if pair is None or decision.get("status") not in {"candidate", "archived"}:
                continue
            kind, record = pair
            proposals.append(ActionMemoryOperation(
                proposal={"op": "set_status", "kind": kind, "record_id": record.id,
                          "status": decision["status"], "reason": str(decision.get("reason") or "")},
                evidence_ids=record.evidence_ids, scope=record.scope, role="curator",
                allowed_record_ids=(record.id,), source_created_at=time.time(),
            ))
        applied = store.apply_model_operations(proposals)
        result.actions_reviewed = len(rows)
        result.actions_applied = applied["applied"]

    def _compact_transient_stores(self, result: MemoryGCResult) -> None:
        base_dir = str(getattr(self.memory_core, "base_dir", "") or os.getenv("ERIS_MEMORY_CORE_DIR", "memory_core"))
        try:
            from runtime.behavior_observation import BehaviorObservationJournal
            journal = BehaviorObservationJournal(path=os.path.join(base_dir, "behavior_observations.jsonl"))
            report = journal.compact()
            result.behavior_removed = int(report.get("removed", 0))
            if result.behavior_removed:
                result.notes.append(f"behavior_observations_removed:{result.behavior_removed}")
        except Exception as exc:
            result.warnings.append(f"behavior_compact_failed:{type(exc).__name__}")
        try:
            from cortex.private_mind.thought_continuity import ThoughtContinuityStore
            thoughts = ThoughtContinuityStore(path=os.path.join(base_dir, "thought_stream.jsonl"))
            report = thoughts.compact()
            result.thought_removed = int(report.get("removed", 0))
            if result.thought_removed:
                result.notes.append(f"thought_stream_removed:{result.thought_removed}")
        except Exception as exc:
            result.warnings.append(f"thought_compact_failed:{type(exc).__name__}")

    def _compact_anchor_journal(self, result: MemoryGCResult) -> None:
        core_dir = str(os.getenv("ERIS_MEMORY_CORE_DIR", "memory_core"))
        journal_path = os.path.join(core_dir, "anchor_journal.jsonl")
        if not os.path.isfile(journal_path):
            return
        size_mb = os.path.getsize(journal_path) / (1024 * 1024)
        if size_mb <= self.anchor_journal_max_mb:
            return

        with open(journal_path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        # Giữ 60% dòng gần nhất; event ledger canonical không nằm ở file này.
        keep_from = max(0, len(lines) - int(len(lines) * 0.6))
        kept_lines = lines[keep_from:]
        tmp = journal_path + ".gc_tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.writelines(kept_lines)
        os.replace(tmp, journal_path)
        result.journal_compacted = True
        result.notes.append(f"journal_compacted:{size_mb:.1f}MB->{len(kept_lines)}_lines")

    # ── Client/model helpers ───────────────────────────────────────────────────

    def _get_critic_client(self) -> Any | None:
        # Compatibility cho caller cũ; không được tái mở đường DeepSeek critic.
        return self._get_curator_client()

    def _get_curator_client(self) -> Any | None:
        """Dùng cùng lane Gemini với extractor/dream; không fallback DeepSeek."""
        from memory.living_user.gemini_adapter import GeminiWebUserProfileAdapter

        living_user = getattr(self.memory_core, "living_user", None)
        for client in (
            getattr(living_user, "model_adapter", None),
            getattr(self.llm_service, "curator_client", None),
        ):
            if isinstance(client, GeminiWebUserProfileAdapter):
                return client
        return GeminiWebUserProfileAdapter.create_if_available()

    def _call_model(self, client: Any, system: str, user: str, *, max_tokens: int) -> str | None:
        try:
            response = client.create_chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                timeout=_env_float("ERIS_MEMORY_GC_CRITIC_TIMEOUT_SEC", getattr(client, "timeout", 120.0)),
            )
            extract = getattr(self.llm_service, "_extract_content", None)
            if callable(extract):
                return str(extract(response) or "")
            choices = response.get("choices") if isinstance(response, dict) else None
            if choices and isinstance(choices, list):
                message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
                return str(message.get("content", "") or "")
            return None
        except Exception as exc:
            logger.warning("[memory_gc] critic call failed: %s", exc)
            return None

    # ── Semantic helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _parse_decisions(raw: str) -> list[dict[str, Any]]:
        text = str(raw or "").strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start:end])
        except Exception:
            return []
        rows = data.get("decisions", []) if isinstance(data, dict) else []
        return [dict(row) for row in rows if isinstance(row, dict) and row.get("id")]

    def _is_semantic_gc_candidate(self, item: Any) -> bool:
        metadata = self._metadata(item)
        getter = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
        fact_key = str(
            getter("fact_key", "")
            or metadata.get("fact_key")
            or ""
        ).strip()
        claim = str(
            getter("claim", "")
            or getter("summary", "")
            or getter("semantic_value", "")
            or ""
        ).strip()
        if not claim:
            return True
        # Canonical fact_key có evidence được bảo vệ khỏi batch critic thông thường.
        evidence = tuple(getter("evidence_ids", ()) or metadata.get("evidence_ids", ()) or ())
        if fact_key and evidence:
            return False
        confidence = float(getter("confidence", metadata.get("confidence", 0.5)) or 0.5)
        source = str(getter("source", "") or metadata.get("source", "") or "")
        return confidence < 0.82 or source in {"profile_model", "dream_model", "model_inference"}

    def _format_semantic_entries(self, items: list[Any]) -> str:
        lines: list[str] = []
        for item in items:
            metadata = self._metadata(item)
            payload = {
                "id": self._item_id(item),
                "fact_key": str(getattr(item, "fact_key", "") or metadata.get("fact_key") or ""),
                "claim": str(
                    getattr(item, "claim", "")
                    or getattr(item, "summary", "")
                    or getattr(item, "semantic_value", "")
                    or ""
                )[:420],
                "confidence": float(getattr(item, "confidence", metadata.get("confidence", 0.5)) or 0.5),
                "source": str(getattr(item, "source", "") or metadata.get("source", ""))[:60],
                "evidence_ids": list(getattr(item, "evidence_ids", ()) or metadata.get("evidence_ids", ()) or ())[:12],
            }
            lines.append(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))
        return "\n".join(lines)

    @staticmethod
    def _item_id(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("id") or item.get("claim_id") or id(item))
        return str(getattr(item, "id", "") or getattr(item, "claim_id", "") or id(item))

    @staticmethod
    def _metadata(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            return dict(item.get("metadata") or {})
        return dict(getattr(item, "metadata", {}) or {})


# ── Builder ───────────────────────────────────────────────────────────────────

def build_memory_gc_service(
    *,
    memory_core: Any,
    llm_service: Any | None = None,
    idle_probe: Any | None = None,
) -> MemoryGCService | None:
    """Builder trả MemoryGCService đã start(), hoặc None nếu bị tắt."""
    if not _env_flag("ERIS_MEMORY_GC_ENABLED", True) or memory_core is None:
        return None
    try:
        service = MemoryGCService(
            memory_core=memory_core,
            llm_service=llm_service,
            idle_probe=idle_probe,
        )
        service.start()
        return service
    except Exception as exc:
        logger.warning("[memory_gc] build failed: %s", exc)
        return None


__all__ = ["MemoryGCResult", "MemoryGCService", "build_memory_gc_service"]

# -*- coding: utf-8 -*-
"""
runtime/private_mind_daemon.py
==============================

Daemon runtime cho PrivateMind.

Khác với `PrivateMindLoop`, daemon này không chỉ chạy một lượt evaluator. Nó giữ
vòng đời của private mind trong runtime:
- sở hữu state/concern và outcome feedback;
- queue SurfaceImpulse legacy chỉ tồn tại khi bật feature flag;
- nhận outcome feedback sau khi ERIS nói/hành động;
- bơm outcome đó vào concern lifecycle ở tick kế tiếp;
- đồng bộ bias/state nhẹ về RuntimeCoordinator để speech/prompt/body đọc được;
- expose digest debug đủ gọn cho menu context.

Tên là daemon, nhưng KHÔNG tự mở thread ở đây. Runtime đã có loop chính rồi; nhét
thêm thread nền chỉ để tranh lock là cách con người biến cognition thành đua xe
trong bãi giữ xe. Daemon này chạy đồng bộ theo tick hiện có, an toàn hơn và dễ
trace hơn.
"""

from __future__ import annotations
from core.di import resolve_dependency

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from cortex.private_mind.contracts import to_plain
from cortex.private_mind.comfort_appraiser import ComfortAppraiser
from runtime.private_mind_loop import PrivateMindLoop, PrivateMindLoopConfig
from runtime.architecture_flags import private_cognition_surface_export_enabled


@dataclass(slots=True)
class PrivateMindDaemonConfig:
    """Knob lifecycle cho private mind daemon.

    Ghi chú tiếng Việt để lúc debug không phải đoán ý đồ của người viết cũ, tức
    chính chúng ta ở tương lai với ít ngủ hơn.
    """

    enabled: bool = True
    max_surface_queue: int = 12
    # PrivateMind chỉ cung cấp state/memory; speech queue legacy mặc định tắt.
    surface_export_enabled: bool = False
    max_outcome_reports: int = 24
    max_digested_impulses: int = 8
    last_outcome_ttl_seconds: float = 60.0 * 8.0
    # Nếu tick bị gọi quá dày, PrivateMindLoop vẫn tự rate-limit. Daemon chỉ ghi
    # trace lifecycle, không tự quyết thay loop.
    mirror_loop_rate_limit: bool = True


@dataclass(slots=True)
class PrivateMindDaemonTick:
    """Kết quả tick ở tầng daemon.

    `loop_result` giữ payload sâu; daemon thêm lifecycle metadata để debug vì
    private cognition mà không có trace thì lại thành bói toán có dataclass.
    """

    loop_result: Any = None
    ticked: bool = False
    reason: str = ""
    surface_queue_size: int = 0
    outcome_reports: tuple[dict[str, Any], ...] = ()
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticked": self.ticked,
            "reason": self.reason,
            "surface_queue_size": self.surface_queue_size,
            "outcome_reports": to_plain(self.outcome_reports),
            "created_at": self.created_at,
            "loop_result": self.loop_result.to_dict() if callable(getattr(self.loop_result, "to_dict", None)) else to_plain(self.loop_result),
        }


class PrivateMindDaemon:
    """Lifecycle owner cho PrivateMindLoop.

    Trách nhiệm của daemon:
    1. Không sinh lời thoại public.
    2. Giữ concern/outcome sống qua nhiều tick.
    3. Đưa mutation sang runtime owner bằng StateMutationBus của loop.
    4. Chỉ cung cấp state/memory; surface queue cũ nằm sau feature flag.
    """

    def __init__(
        self,
        *,
        loop: PrivateMindLoop,
        config: PrivateMindDaemonConfig | None = None,
        runtime_owner: Any = None,
        logger: Any = None,
    ) -> None:
        self.loop = loop
        self.config = resolve_dependency(config, lambda: PrivateMindDaemonConfig(
            enabled=bool(getattr(loop.config, "enabled", True)),
            surface_export_enabled=private_cognition_surface_export_enabled(),
        ))
        self.runtime_owner = runtime_owner
        self.logger = logger
        self.surface_queue: list[Any] = []
        self.last_tick: PrivateMindDaemonTick | None = None
        self.last_tick_at = 0.0
        self.tick_count = 0
        self.skipped_count = 0
        self.outcome_feedback_reports: list[dict[str, Any]] = []
        self.last_surface_outcome: dict[str, Any] = {}
        self.last_outcome_at = 0.0
        self._comfort_appraiser = ComfortAppraiser(
            model_adapter=getattr(loop, "model_adapter", None), logger=logger
        )
        self._sync_runtime_owner()

    @classmethod
    def from_runtime(cls, *, runtime_owner: Any, llm_service: Any = None, logger: Any = None) -> "PrivateMindDaemon | None":
        """Tạo daemon từ runtime hiện có.

        Vẫn dùng `PrivateMindLoop.from_runtime()` để giữ tương thích env cũ:
        `ERIS_PRIVATE_MIND_ENABLED`, provider/model, concern path...
        """

        loop = PrivateMindLoop.from_runtime(runtime_owner=runtime_owner, llm_service=llm_service, logger=logger)
        if loop is None:
            return None
        config = PrivateMindDaemonConfig(
            enabled=bool(getattr(loop.config, "enabled", True)),
            surface_export_enabled=private_cognition_surface_export_enabled(),
        )
        return cls(loop=loop, config=config, runtime_owner=runtime_owner, logger=logger)

    @property
    def concern_store(self) -> Any:
        return getattr(self.loop, "concern_store", None)

    @property
    def mutation_bus(self) -> Any:
        return getattr(self.loop, "mutation_bus", None)

    def tick(
        self,
        *,
        user_text: str = "",
        channel: str = "autonomous",
        snapshot: Any = None,
        observer_frame: Any = None,
        attention_signal: Any = None,
        memory_core: Any = None,
        state_committer: Any = None,
        memory_hits: Iterable[Any] = (),
        session_turn_ledger: Any = None,
        internal_agenda: Any = None,
        thought_stream: Any = None,
        surface_candidate_queue: list[Any] | None = None,
        runtime_owner: Any = None,
        surface_outcome: Mapping[str, Any] | None = None,
        force: bool = False,
    ) -> PrivateMindDaemonTick | None:
        if not self.config.enabled or self.loop is None:
            return None
        owner = runtime_owner if runtime_owner is not None else self.runtime_owner
        # Không truyền queue speech vào PrivateMindLoop ở cấu hình mặc định.
        # State, concern, diary local và memory vẫn chạy bình thường.
        queue = (
            surface_candidate_queue if surface_candidate_queue is not None else self.surface_queue
        ) if self.config.surface_export_enabled else None
        if not self.config.surface_export_enabled:
            self.surface_queue.clear()
        merged_surface_outcome = self._merged_surface_outcome(surface_outcome)
        before = getattr(self.loop, "last_tick_at", 0.0)
        result = self.loop.tick(
            user_text=user_text,
            channel=channel,
            snapshot=snapshot,
            observer_frame=observer_frame,
            attention_signal=attention_signal,
            memory_core=memory_core,
            state_committer=state_committer,
            memory_hits=memory_hits,
            session_turn_ledger=session_turn_ledger,
            internal_agenda=internal_agenda,
            thought_stream=thought_stream,
            surface_candidate_queue=queue,
            runtime_owner=owner,
            surface_outcome=merged_surface_outcome,
            force=force,
        )
        after = getattr(self.loop, "last_tick_at", 0.0)
        ticked = bool(force or after != before)
        if ticked:
            self.tick_count += 1
            self.last_tick_at = float(after or time.time())
            reason = "ticked"
        else:
            self.skipped_count += 1
            reason = "loop_rate_limited"
        if queue is not None:
            self._trim_surface_queue(queue)
        self._sync_runtime_owner()
        daemon_tick = PrivateMindDaemonTick(
            loop_result=result,
            ticked=ticked,
            reason=reason,
            surface_queue_size=len(queue or ()),
            outcome_reports=tuple(self.outcome_feedback_reports[-6:]),
        )
        self.last_tick = daemon_tick
        if ticked and owner is not None:
            self._maybe_leak_cognition(result, owner)
        return daemon_tick

    def _maybe_leak_cognition(self, result: Any, owner: Any) -> bool:
        """Rò rỉ nhận thức vi mô (Micro Cognitive Leakage) từ Não ngầm.

        Khi Eris vừa suy nghĩ sâu (có unexpressed thoughts, tension, hoặc ngộ ra eureka),
        phát xạ một cử chỉ vi mô nhỏ (ánh mắt liếc, khẽ 'hửm...', hoặc 'a...') mà không
        chiếm sóng chat công khai.
        """
        if result is None or owner is None:
            return False
        emit_fn = getattr(owner, "_emit_fast_micro_act", None)
        if not callable(emit_fn):
            return False
        clock = getattr(owner, "interaction_clock", None)
        if clock is not None and getattr(clock, "speech_floor_busy", False):
            return False

        try:
            from runtime.micro_reflex_system import get_micro_reflex_engine
            engine = get_micro_reflex_engine(logger=self.logger)

            judgment = getattr(result, "judgment", None)
            tension = float(getattr(judgment, "pressure", 0.0) or 0.0) if judgment is not None else 0.0
            confidence = float(getattr(judgment, "confidence", 0.0) or 0.0) if judgment is not None else 0.0
            eureka = confidence > 0.85 and bool(getattr(judgment, "dominant_judgment", ""))

            unexpressed = ()
            if judgment is not None:
                unexpressed = getattr(judgment, "open_questions", ()) or ()
            if not unexpressed and hasattr(result, "dossier"):
                meta = getattr(result.dossier, "metadata", {}) or {}
                unexpressed = meta.get("unresolved_threads") or ()

            leak_kind = engine.evaluate_cognitive_leakage(
                unexpressed_thoughts=list(unexpressed) if unexpressed else None,
                tension=tension,
                eureka=eureka,
                now=time.time(),
            )
            if leak_kind is None:
                return False

            return bool(emit_fn(surface="", act_kind=leak_kind, timestamp=time.time()))
        except Exception as exc:
            if self.logger:
                self.logger.debug("[private-mind] cognitive leakage error: %s", exc)
            return False

    def record_outcome(self, outcome: Any, *, frame: Any = None) -> tuple[dict[str, Any], ...]:
        """Nhận hậu quả public turn và cập nhật concern lifecycle.

        Outcome không được biến thành lời nói ngay. Nó chỉ làm concern mềm/lệch
        lại, để tick sau có cơ sở sửa hành vi. Đúng, phải đi đường vòng như người
        trưởng thành thay vì la lên mọi suy nghĩ thô trong đầu.
        """

        store = self.concern_store
        updater = getattr(store, "update_from_outcome", None)
        surface_outcome = self._surface_outcome_from_frame(frame)
        self.last_surface_outcome = surface_outcome
        self.last_outcome_at = time.time()
        if not callable(updater):
            return ()
        # Agent nhỏ thẩm định lời dỗ TRƯỚC khi đụng vào concern. Có ego: không phải
        # cứ "đừng lo" là nguôi. Kết quả tiêm vào outcome dưới dạng payload `comfort`.
        outcome = self._attach_comfort_appraisal(outcome)
        try:
            reports = tuple(updater(outcome, surface_outcome=surface_outcome) or ())
        except Exception as exc:
            self._log("warning", "PrivateMindDaemon outcome feedback error: %s", exc)
            return ()
        for report in reports:
            if isinstance(report, Mapping):
                self.outcome_feedback_reports.append(dict(report))
            else:
                self.outcome_feedback_reports.append({"value": to_plain(report)})
        self.outcome_feedback_reports = self.outcome_feedback_reports[-max(1, self.config.max_outcome_reports) :]
        self._sync_runtime_owner()
        return tuple(to_plain(r) for r in reports)

    def _attach_comfort_appraisal(self, outcome: Any) -> Any:
        """Chạy ComfortAppraiser và gắn payload `comfort` vào outcome nếu có ý nghĩa.

        Trả lại outcome (có thể đã chuyển thành dict để mang thêm `comfort`). Không
        bao giờ ném lỗi — thẩm định nội tâm hỏng thì pipeline vẫn phải sống.
        """
        try:
            feedback_text = self._extract_feedback_text(outcome)
            if not feedback_text:
                return outcome
            # Pre-filter rẻ: không có dấu hiệu dỗ thì khỏi gọi agent nhỏ.
            soothed_hint = self._extract_flag(outcome, "user_soothed")
            if not soothed_hint and not self._comfort_appraiser.looks_like_comfort(feedback_text):
                return outcome
            store = self.concern_store
            themes: list[str] = []
            if store is not None and callable(getattr(store, "open_concerns", None)):
                themes = [str(getattr(c, "theme", "") or "") for c in store.open_concerns(limit=5)]
            warmth, threat = self._disposition_warmth_threat()
            appraisal = self._comfort_appraiser.appraise(
                user_text=feedback_text,
                concern_themes=themes,
                warmth=warmth,
                threat=threat,
            )
            if appraisal.stance not in {"soothed", "hardened"}:
                return outcome
            payload = appraisal.to_payload()
            self._log(
                "info",
                "[ComfortAppraiser] stance=%s relief=%.2f on_topic=%.2f substance=%.2f recept=%.2f src=%s",
                payload["stance"], payload["relief"], payload["on_topic"],
                payload["substance"], payload["receptiveness"], payload["source"],
            )
            return self._inject_comfort(outcome, payload)
        except Exception as exc:
            self._log("warning", "[ComfortAppraiser] attach error: %s", exc)
            return outcome

    @staticmethod
    def _extract_feedback_text(outcome: Any) -> str:
        if isinstance(outcome, Mapping):
            return str(outcome.get("feedback_text") or outcome.get("correction_text") or "").strip()
        return str(getattr(outcome, "feedback_text", "") or getattr(outcome, "correction_text", "") or "").strip()

    @staticmethod
    def _extract_flag(outcome: Any, name: str) -> bool:
        if isinstance(outcome, Mapping):
            return bool(outcome.get(name))
        return bool(getattr(outcome, name, False))

    @staticmethod
    def _inject_comfort(outcome: Any, comfort: dict[str, Any]) -> dict[str, Any]:
        if isinstance(outcome, Mapping):
            data = dict(outcome)
        elif callable(getattr(outcome, "to_dict", None)):
            try:
                data = dict(outcome.to_dict())
            except Exception:
                data = {}
        else:
            data = {}
        data["comfort"] = comfort
        return data

    def _disposition_warmth_threat(self) -> tuple[float, float]:
        """Lấy warmth/threat (ego) của Eris từ internal state qua disposition.

        Thiếu state → trả trung tính nghiêng phòng thủ nhẹ (0.3, 0.3): không quá dễ
        nguôi khi chưa biết tâm thế.
        """
        owner = self.runtime_owner
        getter = getattr(owner, "_get_internal_state", None) if owner is not None else None
        if not callable(getter):
            return 0.3, 0.3
        try:
            state = getter()
            if state is None:
                return 0.3, 0.3
            from cortex.disposition import threat_level, warmth_level
            return float(warmth_level(state)), float(threat_level(state))
        except Exception:
            return 0.3, 0.3

    def drain_surface_impulses(self, *, limit: int = 4) -> tuple[Any, ...]:
        """Lấy impulse đã lọc để SurfaceArbiter xử lý.

        Queue này là ranh giới công khai: mọi thứ trước nó vẫn là private chain.
        """

        items: list[Any] = []
        max_items = max(1, int(limit or 4))
        while self.surface_queue and len(items) < max_items:
            items.append(self.surface_queue.pop(0))
        self._sync_runtime_owner()
        return tuple(items)

    def digest(self, *, surface_queue: list[Any] | None = None) -> dict[str, Any]:
        queue = surface_queue if surface_queue is not None else self.surface_queue
        loop_digest = {}
        digest_fn = getattr(self.loop, "digest", None)
        if callable(digest_fn):
            try:
                loop_digest = dict(digest_fn(surface_queue=queue) or {})
            except Exception as exc:
                loop_digest = {"debug": {"digest_error": f"{type(exc).__name__}:{exc}"}}
        daemon_payload = {
            "enabled": bool(self.config.enabled),
            "tick_count": self.tick_count,
            "skipped_count": self.skipped_count,
            "last_tick_at": round(float(self.last_tick_at or getattr(self.loop, "last_tick_at", 0.0) or 0.0), 3),
            "last_tick_reason": str(getattr(self.last_tick, "reason", "") or ""),
            "surface_queue_size": len(queue),
            "surface_queue_preview": [self._surface_preview(item) for item in tuple(queue)[: self.config.max_digested_impulses]],
            "last_outcome_at": round(float(self.last_outcome_at or 0.0), 3),
            "last_surface_outcome": to_plain(self.last_surface_outcome),
            "outcome_feedback_reports": to_plain(self.outcome_feedback_reports[-8:]),
        }
        menu = dict(loop_digest.get("menu", {}) or {})
        menu["private_mind_daemon"] = daemon_payload
        return {
            **loop_digest,
            "daemon": daemon_payload,
            "menu": menu,
        }

    def _merged_surface_outcome(self, surface_outcome: Mapping[str, Any] | None) -> dict[str, Any]:
        base = dict(self.last_surface_outcome or {})
        # Outcome quá cũ thì không ép vào dossier nữa. Ký ức có tuổi thọ; tiếc là
        # bug thì không, nếu không prune.
        if self.last_outcome_at and time.time() - self.last_outcome_at > max(1.0, self.config.last_outcome_ttl_seconds):
            base = {}
        if surface_outcome:
            base.update(dict(surface_outcome))
        return base

    def _surface_outcome_from_frame(self, frame: Any) -> dict[str, Any]:
        if frame is None:
            return {}
        final_decision = getattr(frame, "final_decision", {}) or {}
        if not isinstance(final_decision, Mapping):
            final_decision = {}
        return {
            "channel": str(getattr(frame, "channel", "") or ""),
            "cycle_id": str(getattr(frame, "cycle_id", "") or ""),
            "debug_notes": list(getattr(frame, "debug_notes", []) or [])[-8:],
            "final_decision": {
                "intent": str(final_decision.get("intent", "") or ""),
                "action": str(final_decision.get("action", "") or ""),
                "should_speak": bool(final_decision.get("should_speak", False)),
                "text_preview": " ".join(str(final_decision.get("text", "") or "").split())[:220],
            },
        }

    def _trim_surface_queue(self, queue: list[Any]) -> None:
        max_size = max(1, int(self.config.max_surface_queue or 12))
        while len(queue) > max_size:
            try:
                queue.pop(0)
            except Exception:
                break

    def _surface_preview(self, item: Any) -> dict[str, Any]:
        if callable(getattr(item, "to_context", None)):
            try:
                data = item.to_context()
            except Exception:
                data = to_plain(item)
        elif callable(getattr(item, "to_dict", None)):
            data = item.to_dict()
        elif isinstance(item, Mapping):
            data = dict(item)
        else:
            data = {"value": to_plain(item)}
        if not isinstance(data, Mapping):
            data = {"value": to_plain(data)}
        return {
            "id": str(data.get("id", "") or "")[:80],
            "kind": str(data.get("kind", "") or "")[:80],
            "source": str(data.get("source", "") or data.get("root_source", "") or "")[:80],
            "topic_seed": " ".join(str(data.get("topic_seed", "") or data.get("seed", "") or "").split())[:220],
            "confidence": data.get("confidence", 0.0),
            "score": data.get("score", data.get("usefulness", 0.0)),
        }

    def _sync_runtime_owner(self) -> None:
        owner = self.runtime_owner
        if owner is None:
            return
        try:
            setattr(owner, "private_mind_surface_queue", self.surface_queue)
            setattr(owner, "private_mind_daemon_digest", self.digest(surface_queue=self.surface_queue))
        except Exception:
            # Sync debug không được phép làm chết runtime. Chết vì debug payload là
            # một kiểu hài kịch mà production không cần diễn.
            pass

    def _log(self, level: str, message: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            try:
                fn(message, *args)
            except Exception:
                pass

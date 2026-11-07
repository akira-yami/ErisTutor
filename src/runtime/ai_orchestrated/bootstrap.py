# -*- coding: utf-8 -*-
"""Cài kiến trúc C vào RuntimeCoordinator mà không để module con ghi state trực tiếp."""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Mapping

from runtime.persistence_gate import PersistenceGate
from runtime.engagements.snapshot_journal import EngagementSnapshotJournal
from runtime.diagnostic_logging import safe_log
from runtime.policy_settings import AIRuntimeSettings, ChessBridgeSettings, EngagementSettings, HealthSettings
from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry
from runtime.runtime_status import emit_runtime_status

from .contracts import AI_RUNTIME_VERSION, RuntimeMindState
from .discovery import DiscoveryRuntime
from .executive_cortex import ExecutiveCortex
from .expression import IdentityExpressionBridge
from .identity_consolidator import IdentityConsolidator
from .initiative import InitiativeController
from .model_router import AIRuntimeModelRouter
from .observer import PostTurnObserver
from .orchestrator import AIChatOrchestrator, AIAutonomousOrchestrator, architecture_c_enabled
from .stage_planner import StagePlanner
from .state_reducer import StateReducer
from .random_chat_seed import RandomChatSeedAffinityStore
from .random_chat_seed_service import RandomChatSeedGenerator, RandomChatSeedReservoir


def _safe_health_snapshot(
    component: Any,
    *,
    now: float | None = None,
    logger: Any | None = None,
) -> dict[str, Any]:
    """Đọc health snapshot theo best-effort, không cho telemetry giết runtime."""

    snapshotter = getattr(component, "snapshot", None)
    if not callable(snapshotter):
        return {"snapshot_available": False, "snapshot_error": "snapshot_missing"}
    try:
        payload = snapshotter(now=now) if now is not None else snapshotter()
    except Exception as exc:
        # Snapshot chỉ phục vụ observability. Component đã dựng thành công không
        # được bị hạ cả Architecture C chỉ vì phần telemetry có contract lệch.
        safe_log(
            logger,
            "warning",
            "[architecture C] health snapshot failed component=%s: %s",
            type(component).__name__,
            exc,
        )
        return {
            "snapshot_available": False,
            "snapshot_error_type": type(exc).__name__,
            "snapshot_error": str(exc)[:240],
        }
    if isinstance(payload, Mapping):
        return dict(payload)
    return {"snapshot_available": True, "snapshot_value": payload}


def install_ai_orchestrated_runtime(host: Any, llm_service: Any) -> bool:
    if not architecture_c_enabled():
        host.ai_runtime_enabled = False
        return False

    health = getattr(host, "subsystem_health_registry", None)
    if not isinstance(health, SubsystemHealthRegistry):
        health = SubsystemHealthRegistry(max_events=HealthSettings.load().max_events)
        host.subsystem_health_registry = health
    health.mark_starting("ai_orchestrated.runtime")
    policy_bundle = getattr(host, "runtime_policy_settings", None)
    ai_settings = getattr(policy_bundle, "ai_runtime", None)
    if not isinstance(ai_settings, AIRuntimeSettings):
        ai_settings = AIRuntimeSettings.load()
    engagement_settings = getattr(policy_bundle, "engagement", None)
    if not isinstance(engagement_settings, EngagementSettings):
        engagement_settings = EngagementSettings.load()
    chess_bridge_settings = getattr(policy_bundle, "chess_bridge", None)
    if not isinstance(chess_bridge_settings, ChessBridgeSettings):
        chess_bridge_settings = ChessBridgeSettings.load()
    runtime_clock = getattr(host, "_runtime_clock", None)
    if not callable(runtime_clock):
        runtime_clock = time.time
    state_persistence_gate = PersistenceGate(
        ai_settings.state_persistence_interval_seconds,
        clock=runtime_clock,
    )
    engagement_persistence_gate = PersistenceGate(
        engagement_settings.persistence_interval_seconds,
        clock=runtime_clock,
    )
    host._ai_state_persistence_gate = state_persistence_gate
    host._ai_engagement_persistence_gate = engagement_persistence_gate

    def load_state() -> RuntimeMindState:
        memory_state = getattr(host, "memory_state", None)
        if isinstance(memory_state, dict):
            return RuntimeMindState.from_mapping(memory_state.get("ai_orchestrated_state", {}))
        metadata = getattr(memory_state, "metadata", None)
        if isinstance(metadata, Mapping):
            return RuntimeMindState.from_mapping(metadata.get("ai_orchestrated_state", {}))
        return RuntimeMindState()

    def load_aux_state(key: str) -> Mapping[str, Any]:
        memory_state = getattr(host, "memory_state", None)
        if isinstance(memory_state, dict):
            value = memory_state.get(key, {})
            return value if isinstance(value, Mapping) else {}
        metadata = getattr(memory_state, "metadata", None)
        if isinstance(metadata, Mapping):
            value = metadata.get(key, {})
            return value if isinstance(value, Mapping) else {}
        return {}

    persist_lock = getattr(host, "_state_lock", None) or threading.RLock()

    def engagement_container() -> dict[str, Any] | None:
        memory_state = getattr(host, "memory_state", None)
        if isinstance(memory_state, dict):
            return memory_state
        if memory_state is None:
            return None
        metadata = getattr(memory_state, "metadata", None)
        if isinstance(metadata, dict):
            return metadata
        try:
            memory_state.metadata = {}
        except (AttributeError, TypeError, ValueError):
            return None
        metadata = getattr(memory_state, "metadata", None)
        return metadata if isinstance(metadata, dict) else None

    engagement_snapshot_journal = EngagementSnapshotJournal(
        engagement_container, clock=runtime_clock, health_registry=health
    )
    host._ai_engagement_snapshot_journal = engagement_snapshot_journal

    def persist(payload: Mapping[str, Any], *, force: bool = False) -> None:
        memory_state = getattr(host, "memory_state", None)
        with persist_lock:
            if isinstance(memory_state, dict):
                memory_state["ai_orchestrated_state"] = dict(payload)
            elif memory_state is not None:
                metadata = getattr(memory_state, "metadata", None)
                if not isinstance(metadata, dict):
                    try:
                        memory_state.metadata = {}
                        metadata = memory_state.metadata
                    except Exception:
                        metadata = None
                if isinstance(metadata, dict):
                    metadata["ai_orchestrated_state"] = dict(payload)
            state_persistence_gate.mark_dirty()
            saver = getattr(host, "_save_memory_state", None)
            if callable(saver) and state_persistence_gate.should_flush(force=force):
                try:
                    saver()
                except Exception as exc:
                    health.record_failure(
                        "ai_state.persistence",
                        exc,
                        kind=FailureKind.PERSISTENCE_FAILURE,
                        degraded=True,
                    )
                    raise
                else:
                    state_persistence_gate.mark_flushed()
                    health.record_success(
                        "ai_state.persistence",
                        details={"forced": bool(force)},
                    )

    def persist_aux_state(key: str, health_name: str, payload: Mapping[str, Any]) -> None:
        memory_state = getattr(host, "memory_state", None)
        with persist_lock:
            if isinstance(memory_state, dict):
                memory_state[key] = dict(payload)
            elif memory_state is not None:
                metadata = getattr(memory_state, "metadata", None)
                if not isinstance(metadata, dict):
                    try:
                        memory_state.metadata = {}
                        metadata = memory_state.metadata
                    except Exception:
                        metadata = None
                if isinstance(metadata, dict):
                    metadata[key] = dict(payload)
            # Timed and leased state is written immediately so restart recovery
            # cannot replay the same continuation twice.
            saver = getattr(host, "_save_memory_state", None)
            if callable(saver):
                try:
                    saver()
                except Exception as exc:
                    health.record_failure(
                        health_name,
                        exc,
                        kind=FailureKind.PERSISTENCE_FAILURE,
                        degraded=True,
                    )
                else:
                    health.record_success(health_name)

    def persist_random_chat_seed(payload: Mapping[str, Any]) -> None:
        persist_aux_state(
            "random_chat_seed_state",
            "random_chat_seed.persistence",
            payload,
        )

    def promote_random_chat_preference(
        *,
        path: str,
        value: Any,
        explicitness: str,
        confidence: float,
        evidence_ids: tuple[str, ...],
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        kernel = getattr(host, "memory_kernel", None)
        living_user = getattr(kernel, "living_user", None)
        recorder = getattr(living_user, "record_interaction_preference", None)
        if not callable(recorder):
            return ""
        result = recorder(
            path,
            value,
            explicitness=explicitness,
            confidence=confidence,
            evidence_ids=evidence_ids,
            metadata=metadata,
        )
        rejected = tuple(getattr(result, "rejected", ()) or ())
        if rejected:
            safe_log(
                getattr(host, "logger", None),
                "debug",
                "[random chat seed] preference not promoted path=%s rejected=%s",
                path,
                ",".join(str(item) for item in rejected[:4]),
            )
            return ""
        return path

    # Engagement runtime is a separate semantic kernel: it persists durable
    # interaction commitments without polluting the single-writer mind state.
    def load_engagement_state() -> Mapping[str, Any]:
        if engagement_settings.snapshot_journal_enabled:
            return engagement_snapshot_journal.load(
                auto_repair=engagement_settings.snapshot_journal_auto_repair
            ).snapshot
        container = engagement_container()
        value = container.get("ai_engagement_state", {}) if container is not None else {}
        return value if isinstance(value, Mapping) else {}

    def persist_engagement(payload: Mapping[str, Any], *, force: bool = False) -> None:
        with persist_lock:
            if engagement_settings.snapshot_journal_enabled:
                try:
                    engagement_snapshot_journal.stage(payload)
                except (TypeError, ValueError, RuntimeError) as exc:
                    health.record_failure(
                        "engagement.snapshot_journal",
                        exc,
                        kind=FailureKind.PERSISTENCE_FAILURE,
                        degraded=True,
                        reason="engagement snapshot could not be staged",
                    )
                    raise
            else:
                container = engagement_container()
                if container is not None:
                    container["ai_engagement_state"] = dict(payload)
            events = tuple(payload.get("events", ()) or ()) if isinstance(payload, Mapping) else ()
            last_kind = str(events[-1].get("kind", "") or "") if events and isinstance(events[-1], Mapping) else ""
            force = bool(force or last_kind in {"engagement.completed", "engagement.cancelled", "engagement.paused"})
            engagement_persistence_gate.mark_dirty()
            saver = getattr(host, "_save_memory_state", None)
            if callable(saver) and engagement_persistence_gate.should_flush(force=force):
                try:
                    saver()
                except Exception as exc:
                    health.record_failure(
                        "engagement.persistence",
                        exc,
                        kind=FailureKind.PERSISTENCE_FAILURE,
                        degraded=True,
                    )
                    raise
                else:
                    engagement_persistence_gate.mark_flushed()
                    health.record_success(
                        "engagement.persistence",
                        details={"forced": bool(force)},
                    )

    reducer = StateReducer(initial_state=load_state(), persist=persist, logger=getattr(host, "logger", None))
    health.record_success("ai_orchestrated.state_reducer")
    router = AIRuntimeModelRouter(
        llm_service,
        logger=getattr(host, "logger", None),
        budget_policy=getattr(host, "model_budget_policy", None),
    )
    health.record_success("ai_orchestrated.model_router")
    observer = PostTurnObserver(
        router,
        reducer,
        logger=getattr(host, "logger", None),
        workers=ai_settings.observer_workers,
    )
    health.record_success("ai_orchestrated.observer")
    # Random-chat seeds give autonomous speech a concrete premise, ERIS-owned
    # motive and bounded exploration budget before Surface writes public wording.
    random_chat_seed_store = RandomChatSeedAffinityStore(
        initial_state=load_aux_state("random_chat_seed_state"),
        persist=persist_random_chat_seed,
        preference_callback=promote_random_chat_preference,
        logger=getattr(host, "logger", None),
        clock=runtime_clock,
    )
    health.record_success(
        "ai_orchestrated.random_chat_seed",
        details=_safe_health_snapshot(
            random_chat_seed_store,
            now=runtime_clock(),
            logger=getattr(host, "logger", None),
        ),
    )
    # Seed generation has a dedicated worker/client and a background reservoir.
    # The producer can think while autonomous is idle; the public turn only leases
    # an already-validated premise and therefore never waits for this model call.
    random_chat_seed_generator = RandomChatSeedGenerator(
        getattr(llm_service, "seed_client", None),
        invoke_structured=router.invoke_structured_worker,
        logger=getattr(host, "logger", None),
    )
    random_chat_seed_reservoir = RandomChatSeedReservoir(
        random_chat_seed_generator,
        seed_store=random_chat_seed_store,
        wake_callback=getattr(host, "_wake_autonomous_loop", None),
        status_callback=lambda **payload: emit_runtime_status(host, **payload),
        # V45: seed worker chỉ cấp associative material. Sau validation material
        # đi vào lifecycle session-local; không được tự nhảy thẳng thành concern
        # hay public candidate. Motive đã được runtime chọn trước generation.
        mental_event_sink=getattr(getattr(host, "mental_material_runtime", None), "ingest_seed", None),
        # V100 production authority: chỉ BoundFocus V99 được quyền sinh
        # CausalMotive cho associative material. Nếu host cũ không có V100 thì
        # reservoir vẫn giữ fallback legacy để tool/test cũ không vỡ API.
        causal_motive_provider=getattr(
            getattr(host, "focus_material_authority_v100", None),
            "causal_motives",
            None,
        ),
        logger=getattr(host, "logger", None),
        clock=runtime_clock,
    )
    random_chat_seed_reservoir.start()
    health.record_success(
        "ai_orchestrated.random_chat_seed_reservoir",
        details=_safe_health_snapshot(
            random_chat_seed_reservoir,
            now=runtime_clock(),
            logger=getattr(host, "logger", None),
        ),
    )
    # V12: capability planner bên ExecutiveCortex chỉ chọn tool cho một semantic
    # goal đã commit. IntentionPlanner compatibility cũ không còn được bootstrap;
    # mọi quyết định public phải quay về ConversationKernel/RootAgent.
    executive_cortex = ExecutiveCortex(
        host,
        getattr(llm_service, "executive_client", None),
        invoke_structured=router.invoke_structured_worker,
        logger=getattr(host, "logger", None),
        clock=runtime_clock,
    )
    health.record_success(
        "ai_orchestrated.executive_cortex",
        details={
            "enabled": bool(executive_cortex.enabled),
            "model": str(getattr(getattr(llm_service, "executive_client", None), "model_name", "") or ""),
        },
    )
    discovery = DiscoveryRuntime(host, router, logger=getattr(host, "logger", None))
    initiative = InitiativeController(logger=getattr(host, "logger", None))
    consolidator = IdentityConsolidator(logger=getattr(host, "logger", None))
    expression_bridge = IdentityExpressionBridge()

    host.ai_runtime_enabled = True
    host._ai_turn_seq_lock = threading.RLock()
    host._ai_turn_seq = reducer.snapshot().last_applied_turn_seq
    host.ai_state_reducer = reducer
    host._persist_ai_runtime_state = persist
    host.ai_model_router = router
    host.ai_post_turn_observer = observer
    host.ai_random_chat_seed_store = random_chat_seed_store
    host.ai_random_chat_seed_generator = random_chat_seed_generator
    host.ai_random_chat_seed_reservoir = random_chat_seed_reservoir
    host.ai_executive_cortex = executive_cortex
    host.ai_discovery_runtime = discovery
    host.ai_initiative_controller = initiative
    host.ai_identity_consolidator = consolidator
    host.ai_stage_planner = StagePlanner(expression_bridge=expression_bridge)
    if engagement_settings.enabled:
        try:
            from runtime.engagements import EngagementRuntime
            host.ai_engagement_runtime = EngagementRuntime(
                initial_state=load_engagement_state(),
                persist=persist_engagement,
                logger=getattr(host, "logger", None),
                health_registry=health,
                settings=engagement_settings,
            )
            host._persist_ai_engagement_state = persist_engagement
            health.record_success(
                "engagement.runtime",
                details={"settings": engagement_settings.to_dict()},
            )
            host.chess_bridge_service = None
            if chess_bridge_settings.enabled:
                try:
                    from runtime.chess import ChessCompanionService

                    def _build_chess_bridge():
                        return ChessCompanionService(
                            host.ai_engagement_runtime,
                            host=chess_bridge_settings.host,
                            port=chess_bridge_settings.port,
                            token=str(os.getenv("ERIS_CHESS_BRIDGE_TOKEN", "") or ""),
                            analysis_depth=chess_bridge_settings.analysis_depth,
                            direct_move_rate=chess_bridge_settings.direct_move_rate,
                            stockfish_enabled=chess_bridge_settings.stockfish_enabled,
                            stockfish_path=chess_bridge_settings.stockfish_path,
                            stockfish_threads=chess_bridge_settings.stockfish_threads,
                            stockfish_hash_mb=chess_bridge_settings.stockfish_hash_mb,
                            stockfish_multipv=chess_bridge_settings.stockfish_multipv,
                            stockfish_movetime_ms=chess_bridge_settings.stockfish_movetime_ms,
                            stockfish_on_demand_movetime_ms=chess_bridge_settings.stockfish_on_demand_movetime_ms,
                            stockfish_startup_timeout_seconds=chess_bridge_settings.stockfish_startup_timeout_seconds,
                            stockfish_analysis_timeout_seconds=chess_bridge_settings.stockfish_analysis_timeout_seconds,
                            max_body_bytes=chess_bridge_settings.max_body_bytes,
                            logger=getattr(host, "logger", None),
                            health_registry=health,
                            wake_callback=getattr(host, "_wake_autonomous_loop", None),
                            semantic_context_callback=(
                                (lambda state, events=(), trigger_hint="": host.semantic_tutor_runtime.observe(
                                    channel="chess",
                                    chess_state=state,
                                    trigger_hint=trigger_hint,
                                ))
                                if getattr(host, "semantic_tutor_runtime", None) is not None
                                else None
                            ),
                            clock=runtime_clock,
                        ).start()

                    supervisor = getattr(host, "subsystem_supervisor", None)
                    if supervisor is not None:
                        host.chess_bridge_service = supervisor.start(
                            "chess.bridge",
                            _build_chess_bridge,
                            failure_kind=FailureKind.INVALID_EXTERNAL_DATA,
                            stop_methods=("stop", "shutdown", "close"),
                            details=chess_bridge_settings.to_dict(),
                        )
                    else:
                        host.chess_bridge_service = _build_chess_bridge()
                except (ImportError, OSError, RuntimeError, ValueError) as exc:
                    host.chess_bridge_service = None
                    health.record_failure(
                        "chess.bridge", exc,
                        kind=FailureKind.INVALID_EXTERNAL_DATA, degraded=True,
                        reason="bridge startup failed",
                    )
                    safe_log(getattr(host, "logger", None), "warning", "[chess] bridge unavailable: %s", exc)
            else:
                health.mark_stopped("chess.bridge", reason="disabled_by_settings")
        except Exception as exc:
            host.ai_engagement_runtime = None
            host.chess_bridge_service = None
            health.record_failure(
                "engagement.runtime",
                exc,
                kind=FailureKind.PROGRAMMER_ERROR,
                degraded=True,
            )
            safe_log(getattr(host, "logger", None), "warning", "[engagement] runtime unavailable: %s", exc)
    else:
        host.ai_engagement_runtime = None
        host.chess_bridge_service = None
        host._persist_ai_engagement_state = persist_engagement
        health.mark_stopped("engagement.runtime", reason="disabled_by_settings")
    # PDF annotation, pages 12-13 and 20: P2 cutover is complete.  No legacy
    # regex cognition or shadow acceptance gate is instantiated at startup.
    host.ai_pre_response_cognition = None
    host.ai_cognitive_acceptance_gate = None
    host.ai_chat_orchestrator = AIChatOrchestrator(host)
    host.ai_autonomous_orchestrator = AIAutonomousOrchestrator(host)


    processed_memory_turns: set[str] = set()

    def on_state(state: RuntimeMindState, event: Any, accepted: bool) -> None:
        if not accepted:
            return
        payload = dict(getattr(event, "payload", {}) or {})
        host.latest_cognitive_observation = dict(state.latest_observation)
        host.latest_personality_profile = state.personality.to_dict()
        host.latest_identity_profile = state.identity.to_dict()
        host.latest_discovery_state = discovery.panel_view()
        host.pending_social_repair = bool(state.latest_observation.get("repair_needed", False))
        # Panel đọc một state duy nhất, không ráp trust từ ba engine khác nhau.
        try:
            from runtime.ai_orchestrated.views import build_cognitive_views
            from runtime.panel_snapshot import write_social_observation
            write_social_observation(
                dict(state.latest_observation),
                relationship_state=state.relationship.to_dict(),
                cognitive_views=build_cognitive_views(state),
            )
        except Exception as exc:
            safe_log(getattr(host, "logger", None), "warning", "Không cập nhật được panel observation: %s", exc)
        if str(getattr(event, "kind", "")) == "cognitive_observation":
            turn_id = str(getattr(event, "turn_id", "") or "")
            memory_candidate = str(payload.get("memory_candidate", "") or "").strip()
            if (
                turn_id
                and turn_id not in processed_memory_turns
                and bool(payload.get("memory_worthy", False))
                and float(payload.get("confidence", 0.0) or 0.0) >= 0.68
                and memory_candidate
            ):
                processed_memory_turns.add(turn_id)
                enqueue = getattr(host, "_enqueue_background_memory_digest", None)
                if callable(enqueue):
                    try:
                        from memory.claim_provenance import classify_ingress
                        enqueue(
                            memory_candidate,
                            turn_id=turn_id,
                            source="ai_post_turn_observer",
                            priority="normal",
                            context={
                                "channel": "post_turn_observer",
                                "observer_confidence": float(payload.get("confidence", 0.0) or 0.0),
                                "evidence": list(payload.get("evidence", ()) or ()),
                                # Observer proposal giữ nguồn observer, không
                                # bao giờ ngầm trở thành lời user xác nhận.
                                "claim_provenance": classify_ingress("ai_post_turn_observer", turn_id=turn_id),
                            },
                        )
                    except Exception as exc:
                        safe_log(getattr(host, "logger", None), "warning", "Không enqueue được memory digest: %s", exc)

    reducer.subscribe(on_state)
    reducer.subscribe(consolidator.observe)
    try:
        host.logger.info(
            "[architecture C] enabled version=%s state_version=%s personality_version=%s identity_version=%s observer=post_turn autonomous_initiative=deterministic surface=wording_only semantic_authority=conversation_kernel_v12.next_conversation_move single_writer=1",
            AI_RUNTIME_VERSION,
            reducer.snapshot().version,
            reducer.snapshot().personality.version,
            reducer.snapshot().identity.version,
        )
    except Exception as exc:
        safe_log(getattr(host, "logger", None), "debug", "Không ghi được architecture diagnostic: %s", exc)
    health.record_success(
        "ai_orchestrated.runtime",
        details={"engagement_available": host.ai_engagement_runtime is not None},
    )
    return True


def shutdown_ai_orchestrated_runtime(host: Any) -> None:
    health = getattr(host, "subsystem_health_registry", None)
    reducer = getattr(host, "ai_state_reducer", None)
    persist_state = getattr(host, "_persist_ai_runtime_state", None)
    if reducer is not None and callable(persist_state):
        try:
            persist_state(reducer.snapshot().to_dict(), force=True)
        except Exception as exc:
            if isinstance(health, SubsystemHealthRegistry):
                health.record_failure(
                    "ai_state.persistence",
                    exc,
                    kind=FailureKind.PERSISTENCE_FAILURE,
                    degraded=True,
                    reason="shutdown flush failed",
                )
        else:
            if isinstance(health, SubsystemHealthRegistry):
                health.record_success("ai_state.persistence", details={"shutdown_flush": True})
    chess_bridge = getattr(host, "chess_bridge_service", None)
    supervisor = getattr(host, "subsystem_supervisor", None)
    supervised_bridge = supervisor is not None and getattr(supervisor, "get", lambda _name: None)("chess.bridge") is chess_bridge
    if chess_bridge is not None and not supervised_bridge:
        try:
            chess_bridge.stop()
        except (OSError, RuntimeError, AttributeError) as exc:
            if isinstance(health, SubsystemHealthRegistry):
                health.record_failure(
                    "chess.bridge", exc, kind=FailureKind.PROGRAMMER_ERROR,
                    degraded=True, reason="shutdown failed",
                )

    engagement_runtime = getattr(host, "ai_engagement_runtime", None)
    persist_engagement = getattr(host, "_persist_ai_engagement_state", None)
    if engagement_runtime is not None and callable(persist_engagement):
        try:
            persist_engagement(engagement_runtime.registry.snapshot(), force=True)
        except Exception as exc:
            if isinstance(health, SubsystemHealthRegistry):
                health.record_failure(
                    "engagement.persistence",
                    exc,
                    kind=FailureKind.PERSISTENCE_FAILURE,
                    degraded=True,
                    reason="shutdown flush failed",
                )
        else:
            if isinstance(health, SubsystemHealthRegistry):
                health.record_success("engagement.persistence", details={"shutdown_flush": True})
    for name in (
        "ai_post_turn_observer",
        "ai_random_chat_seed_reservoir",
        "ai_discovery_runtime",
        "ai_state_reducer",
    ):
        obj = getattr(host, name, None)
        method_name = "shutdown" if name in {
            "ai_post_turn_observer", "ai_random_chat_seed_reservoir", "ai_discovery_runtime"
        } else "close"
        method = getattr(obj, method_name, None)
        if callable(method):
            try:
                method()
            except Exception as exc:
                if isinstance(health, SubsystemHealthRegistry):
                    health.record_failure(
                        f"ai_orchestrated.{name}",
                        exc,
                        kind=FailureKind.PROGRAMMER_ERROR,
                        degraded=True,
                        reason="shutdown failed",
                    )
            else:
                if isinstance(health, SubsystemHealthRegistry):
                    health.mark_stopped(f"ai_orchestrated.{name}")
    if isinstance(health, SubsystemHealthRegistry):
        health.mark_stopped("engagement.runtime")
        health.mark_stopped("ai_orchestrated.runtime")


__all__ = ["install_ai_orchestrated_runtime", "shutdown_ai_orchestrated_runtime"]

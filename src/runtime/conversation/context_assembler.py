# -*- coding: utf-8 -*-
"""Projection context duy nhất cho ConversationKernel V12.

Module này KHÔNG phải planner và tuyệt đối không được chọn Eris sẽ nói gì.
Nó chỉ đọc những state/evidence đã tồn tại rồi nén chúng thành một packet chung
để RootAgent và SurfaceWriter nhìn cùng một "con người" ở cùng một thời điểm.

Boundary cần giữ:
- StateReducer sở hữu identity/personality/relationship đã học.
- MemoryKernel sở hữu recall/profile context.
- WorldState sở hữu môi trường/current scene có provenance.
- AffectiveVoiceState sở hữu hướng/cường độ biểu đạt sống.
- RootAgent vẫn là semantic/social authority duy nhất cho NextConversationMove.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Mapping, Sequence

from runtime.affective_voice_state import project_affective_voice_state
from runtime.public_reply_context import compile_public_reply_context
from runtime.subject_initiative_runtime import build_initiative_runtime_context
from runtime.world_state import ensure_world_state_service


def _call_text(owner: Any, name: str, *, limit: int) -> str:
    fn = getattr(owner, name, None)
    if not callable(fn):
        return ""
    try:
        return str(fn() or "").strip()[:limit]
    except Exception:
        return ""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_text(value: Any, *, limit: int) -> str:
    if value in (None, "", {}, (), []):
        return ""
    if isinstance(value, str):
        return value.strip()[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)[:limit]
    except Exception:
        return str(value)[:limit]


class ConversationContextAssembler:
    """Thu context public-safe cho một event V12 mà không tạo semantic decision."""

    def __init__(self, host: Any) -> None:
        self.host = host

    def assemble(
        self,
        *,
        user_text: str = "",
        channel: str = "chat",
        previous_public_context: str = "",
        interrupt_context: str = "",
        recent_history: Sequence[Mapping[str, Any]] = (),
        event: Any | None = None,
        source_report: Any | None = None,
    ) -> dict[str, Any]:
        # Chỉ dùng tối đa hai câu user trước làm gợi ý truy xuất Self; không
        # diễn giải lịch sử bằng rule hoặc đưa transcript vào state.
        host = self.host
        context_started = time.perf_counter()
        state = self._mind_state()

        # Fallback chỉ dành cho adapter legacy không có event/state typed.
        personality = _call_text(state, "personality_prompt_context", limit=12000)
        relationship = _call_text(getattr(state, "relationship", None), "prompt_summary", limit=12000)
        memory_started = time.perf_counter()
        # Memory chạy LANE NỀN: lượt này dùng snapshot gần nhất (0 chờ) + kick
        # refresh nền cho lượt sau; lượt đầu seed đồng bộ có deadline ~2s. Giữ
        # nguyên (text, records, degraded) của một lần build thật nên không phá
        # receipt/provenance/admission. Root vẫn có tool recall on-demand khi
        # user hỏi thẳng về trí nhớ (xem memory_recall_tools_for_host).
        from .memory_lane import ensure_memory_lane
        memory, memory_records, memory_degraded = ensure_memory_lane(host).get(
            self._typed_memory_context, user_text,
        )
        memory_finished = time.perf_counter()
        world = self._world_context(channel)
        clock = self._clock_context()
        addressing = self._user_addressing_context()

        # ``last_grounding_context`` là mirror OUTPUT của chính assembler. Không
        # đọc ngược nó ở lượt sau, nếu không context sẽ tự lồng chính nó và phình
        # dần theo số turn. Grounding V12 lấy trực tiếp từ source-of-truth hiện tại.
        grounding = "\n\n".join(part for part in (world, clock, addressing) if part)[:32000]

        affect_payload: dict[str, Any] = {}
        affect_prompt = ""
        affect = None  # Không sample lần hai chỉ để dựng registry.
        try:
            affect = project_affective_voice_state(host, mind_state=state, channel=channel)
            affect_payload = affect.to_dict()
            affect_prompt = affect.prompt_context()
        except Exception as exc:
            self._debug("context.affect", exc)

        engagement: dict[str, Any] = {}
        try:
            engagement = _mapping(build_initiative_runtime_context(host))
        except Exception as exc:
            self._debug("context.engagement", exc)

        conversation_status = self._conversation_status(state, channel=channel)
        engagement_text = _json_text(engagement, limit=8000)
        discourse = self._discourse_context()
        # P2: dùng chung proposal inbox, nhưng chỉ projection nhỏ *không phải*
        # bằng chứng về thế giới/user. Root có thể bỏ qua hoàn toàn các ý này.
        proposal_hints: tuple[dict[str, Any], ...] = ()
        try:
            from runtime.behavior_proposals import ensure_proposal_bus
            proposal_hints = ensure_proposal_bus(host).root_snapshot(limit=6)
        except Exception as exc:
            self._debug("context.proposal_bus", exc)

        # Registry được dựng trước compiler; portrait KHÔNG dùng chuỗi persona
        # bị cắt từ personality_prompt_context() làm input.
        registry = None
        portrait = None
        portrait_render = None
        subject_view = None
        memory_degraded = bool(memory_degraded)
        if event is not None:
            try:
                from .claim_evidence import build_evidence_registry
                from .character_portrait import build_character_portrait, render_character_portrait
                ledger_factory = getattr(host, "_ensure_session_turn_ledger", None)
                ledger = ledger_factory() if callable(ledger_factory) else None
                turns = ledger.all_turns() if ledger is not None else ()
                sampled_at = __import__("time").time()
                registry = build_evidence_registry(
                    event=event, state=state, affect=affect,
                    report=source_report, ledger_turns=turns,
                    memory_records=memory_records, sampled_at=sampled_at,
                )
                if state is not None:
                    # V12 Self-to-Root: SubjectService chỉ CUNG CẤP self-view
                    # đã hình thành qua lịch sử. Root và Writer cùng nhận đúng
                    # một CharacterPortrait; không mở planner/moral-agent mới.
                    subject_owner = getattr(host, "subject_service", None)
                    self_view = getattr(subject_owner, "self_view", None)
                    if callable(self_view):
                        try:
                            candidate = self_view()
                            if isinstance(candidate, Mapping):
                                subject_view = candidate
                        except Exception as exc:
                            self._debug("context.subject_self", exc)
                    # Chỉ dùng lịch sử user gần nhất để gợi topic, KHÔNG nhét
                    # toàn bộ transcript vào portrait và không sửa self-state.
                    current_topic = str(user_text or "").strip()[:1600]
                    # Không để chủ đề quá khứ luôn lấn át câu hỏi hiện tại.
                    # Chỉ mượn topic của các lượt trước khi user dùng một lời
                    # nối mơ hồ ("vậy còn điều đó?", "cậu nghĩ sao?").
                    # Đây là heuristic truy xuất, KHÔNG quyết định Root nói gì.
                    is_follow_up = (len(current_topic) <= 140 and (
                        current_topic.casefold().startswith(("vậy", "thế còn", "còn điều", "nếu vậy", "ý đó", "điều đó"))
                        or "nghĩ sao" in current_topic.casefold()
                        or "thì sao" in current_topic.casefold()
                    ))
                    previous_user = ([str(item.get("content", "") or "")[:500]
                                      for item in recent_history[-6:]
                                      if isinstance(item, Mapping) and item.get("role") == "user"]
                                     if is_follow_up else [])
                    focus_text = " ".join([current_topic, *previous_user[-2:]])
                    portrait = build_character_portrait(
                        state, affect=affect, snapshot_id=registry.snapshot_id,
                        sampled_at=sampled_at, source_registry=registry,
                        focus_text=focus_text, subject_self_view=subject_view,
                    )
                    portrait_render = render_character_portrait(portrait, max_chars=6000)
                    personality = portrait_render.text
            except Exception as exc:
                self._debug("context.character_portrait", exc)
                # Không làm giả thành công: context đánh dấu projection thiếu.
                registry = None
                portrait = None
                portrait_render = None

        compiled = compile_public_reply_context(
            personality=personality,
            relationship=relationship,
            grounding=grounding,
            memory=memory,
            interrupt=str(interrupt_context or "")[:6000],
            engagement=engagement_text,
            discourse=discourse,
            total_limit=96000,
            budget_mode="wide",
        )

        if portrait_render is not None and compiled.personality != portrait_render.text:
            # Không trả packet khác nhau do compiler cắt ngầm phần cuối conflict/cost.
            raise RuntimeError("character_portrait_compiler_modified")

        context: dict[str, Any] = {
            "personality_context": compiled.personality,
            "relationship_context": compiled.relationship,
            "memory_context": compiled.memory,
            "grounding_context": compiled.grounding,
            "interrupt_context": compiled.interrupt,
            "engagement_context": engagement,
            "discourse_context": compiled.discourse,
            "affective_voice_state": affect_payload,
            "affective_voice_context": affect_prompt[:8000],
            "conversation_status": conversation_status,
            "private_cognition_context": self._private_cognition_context(),
            "background_proposals": list(proposal_hints),
            "user_addressing_context": addressing,
            "previous_public_excerpt": str(previous_public_context or "")[-6000:],
            "context_assembler": {
                "schema": "eris.conversation_context.v12",
                "compiled_chars": compiled.total_chars,
                "dropped": list(compiled.dropped),
                "truncated": list(compiled.truncated),
            },
        }

        # Handoff được quan sát trước ContextAssembler ngay trong user turn.
        # Chỉ đọc observation hiện tại, không phát hiện lại/không viết lại state.
        if event is not None and getattr(event, "kind", "") == "user_turn":
            try:
                handoff = getattr(host, "conversation_handoff_runtime", None)
                snapshot = getattr(handoff, "to_dict", None)
                handoff_state = snapshot() if callable(snapshot) else {}
                if isinstance(handoff_state, Mapping) and (
                    handoff_state.get("repair_needed") or handoff_state.get("user_stance") == "confused"
                ):
                    hypotheses = handoff_state.get("causal_hypotheses") or ()
                    primary = hypotheses[0] if hypotheses and isinstance(hypotheses[0], Mapping) else {}
                    context["confusion_signal"] = {
                        "repair_needed": bool(handoff_state.get("repair_needed")),
                        "stance": str(handoff_state.get("user_stance", ""))[:50],
                        "self_caused_probability": float(handoff_state.get("self_caused_reaction_probability", 0.0) or 0.0),
                        "primary_hypothesis": str(primary.get("kind", ""))[:80],
                    }
            except Exception as exc:
                self._debug("context.confusion", exc)

        # P0 + P2 — Chiếu ngữ cảnh xã hội từ lời đã nói và kinh nghiệm đã có
        # phản ứng thật. KHÔNG suy đoán tâm trạng, KHÔNG chọn response tại đây.
        # Tạo sau confusion_signal để vẫn giữ hợp đồng handoff V12 cũ.
        if event is not None and getattr(event, "kind", "") == "user_turn":
            try:
                from .social_situation import project_social_situation
                handoff_owner = getattr(host, "conversation_handoff_runtime", None)
                handoff_snapshot = getattr(handoff_owner, "to_dict", None)
                handoff_evidence = handoff_snapshot() if callable(handoff_snapshot) else {}
                sid = str(getattr(host, "conversation_id", "") or
                          getattr(host, "thread_id", "") or "foreground:primary")[:180]
                context["social_situation_evidence"] = project_social_situation(
                    event_kind="user_turn", user_text=user_text, session_id=sid,
                    state=state, handoff=handoff_evidence, recent_history=recent_history,
                )
            except Exception as exc:
                self._debug("context.social_situation", exc)

        # P3.7: Root luôn chỉ biết vài tiêu đề môn gần đây. Không đọc full graph,
        # lịch sử, BKT/FSRS và không gọi Gemini trên mỗi lượt trò chuyện.
        canvas = getattr(host, "learning_canvas_service", None)
        try:
            context["learning_brief"] = canvas.brief() if canvas else {}
        except Exception:
            context["learning_brief"] = {}
        context["learner_continuity"] = {}
        # Projection Tutor chi tiết chỉ khi người dùng thực sự đang mở Learning.
        if canvas is not None and canvas.is_active():
            try:
                from runtime.tutor.learner_context import learner_continuity_snapshot
                context["learner_continuity"] = learner_continuity_snapshot(
                    getattr(host, "learning_service", None),
                    source=lambda: getattr(getattr(host, "memory_kernel", None), "living_user", None),
                    max_kps=6,
                )
            except Exception:
                context["learner_continuity"] = {}

        # Projection + registry chỉ tồn tại trong runtime_context nội bộ.
        # Root và writer phải nhận chính cùng chuỗi compiled.personality.
        context["context_assembler"]["evidence_degraded"] = (event is None or memory_degraded or registry is None)
        context["context_assembler"]["portrait_degraded"] = event is not None and portrait is None
        if portrait_render is not None:
            context["context_assembler"]["portrait_omitted_facets"] = list(portrait_render.omitted_facets)
            context["context_assembler"]["portrait_overflow"] = portrait_render.overflow
            # Telemetry cơ học để chứng minh Self có ĐẾN được Root/Writer hay
            # không. Chỉ lưu ID facet, không tiết lộ nội tâm hoặc raw history.
            context["context_assembler"]["self_projection"] = {
                "subject_available": subject_view is not None,
                "identity_beliefs": [f.facet_id for f in portrait.facets if f.kind == "belief"],
                "subject_facets": [f.facet_id for f in portrait.facets if f.kind == "subject_self"],
            }
            context["__character_portrait"] = portrait
            context["__character_portrait_render"] = portrait_render
        if registry is not None:
            from dataclasses import replace
            context["__evidence_registry"] = replace(
                registry, degraded=memory_degraded or source_report is None,
            )

        # Bổ sung thông tin từ OpenLoopStore (SuspendedMove) và MicroActRouter
        # (InteractionActReceipt) để RootAgent biết ý định còn dở (quyết định RESUME
        # hay RETIRE) và biết các micro-act vừa phát (tránh mở câu lặp lại "Ừm...").
        try:
            from runtime.open_loop_store import ensure_open_loop_store
            from runtime.interaction_dynamics import ensure_micro_act_router
            sid = str(
                getattr(host, "conversation_id", "")
                or getattr(host, "thread_id", "")
                or "foreground:primary"
            ).strip()[:180]
            active_open_loops = getattr(host, "_active_open_loop_items", None)
            if active_open_loops is None:
                active_open_loops = [
                    item.to_dict() for item in ensure_open_loop_store(host).active_loops(sid)
                ]
            context["open_loop_hints"] = list(active_open_loops)
            context["recent_interaction_act_receipts"] = ensure_micro_act_router(host).recent_receipt_dicts(limit=5)
        except Exception as exc:
            self._debug("context.incremental_dialogue", exc)

        # Mirror compatibility để panel/debug/caller cũ đọc được state mới nhất.
        # Đây là OUTPUT của assembler, không còn là input authority của V12.
        try:
            host.last_personality_context = context["personality_context"]
            host.last_relationship_context = context["relationship_context"]
            host.last_memory_context = context["memory_context"]
            host.last_grounding_context = context["grounding_context"]
            host.last_engagement_context = dict(engagement)
            host.last_affective_voice_state = dict(affect_payload)
        except Exception:
            pass
        from runtime.diagnostic_logging import safe_log
        safe_log(getattr(host, "logger", None), "info",
                 "[chat-stage] context_ms=%d memory_context_ms=%d",
                 int((time.perf_counter() - context_started) * 1000),
                 int((memory_finished - memory_started) * 1000))
        return context

    def _mind_state(self) -> Any | None:
        reducer = getattr(self.host, "ai_state_reducer", None)
        snapshot = getattr(reducer, "snapshot", None)
        if not callable(snapshot):
            return None
        try:
            return snapshot()
        except Exception as exc:
            self._debug("context.state", exc)
            return None

    def _typed_memory_context(self, user_text: str) -> tuple[str, tuple[Any, ...], bool]:
        """Chỉ gọi đúng một selection khi kernel hỗ trợ typed provenance.

        Host legacy vẫn nhận context prose cũ nhưng được báo degraded.
        """
        kernel = getattr(self.host, "memory_kernel", None)
        build = getattr(kernel, "build_public_context", None)
        if not callable(build):
            return self._memory_context(user_text), (), True
        session = str(getattr(self.host, "conversation_id", "") or
                      getattr(self.host, "thread_id", "") or "foreground:primary")[:180]
        session_ctx: dict[str, Any] = {}
        try:
            telemetry = getattr(self.host, "telemetry_service", None)
            if telemetry and callable(getattr(telemetry, "capture_snapshot", None)):
                snap = telemetry.capture_snapshot()
                if snap:
                    app = getattr(snap, "active_app", "") or getattr(snap, "active_window", "")
                    if app:
                        session_ctx["active_app"] = str(app)
            if not session_ctx.get("active_app"):
                world_svc = getattr(self.host, "world_state_service", None)
                if world_svc is None:
                    from runtime.world_state import ensure_world_state_service
                    world_svc = ensure_world_state_service(self.host)
                scene = getattr(world_svc, "current_scene", None)
                if scene and getattr(scene, "active_window_title", None):
                    session_ctx["active_app"] = str(scene.active_window_title)
        except Exception:
            pass
        try:
            packet = build(str(user_text or ""), session_id=session,
                           user_context_token_budget=6000, max_lines=64,
                           session_context=session_ctx)
            return (str(packet.text or "")[:32000],
                    tuple(getattr(packet, "records", ()) or ()),
                    bool(getattr(packet, "degraded", True)))
        except TypeError:
            try:
                packet = build(str(user_text or ""), session_id=session,
                               user_context_token_budget=6000, max_lines=64)
                return (str(packet.text or "")[:32000],
                        tuple(getattr(packet, "records", ()) or ()),
                        bool(getattr(packet, "degraded", True)))
            except Exception as exc:
                self._debug("context.typed_memory", exc)
                return self._memory_context(user_text), (), True
        except Exception as exc:
            self._debug("context.typed_memory", exc)
            return self._memory_context(user_text), (), True

    def _memory_context(self, user_text: str) -> str:
        kernel = getattr(self.host, "memory_kernel", None)
        renderer = getattr(kernel, "render_context", None)
        using_kernel = callable(renderer)
        if not using_kernel:
            renderer = getattr(getattr(self.host, "memory_core", None), "render_context", None)
        if not callable(renderer):
            return ""
        kwargs: dict[str, Any] = {}
        if using_kernel:
            kwargs = {
                "session_id": str(
                    getattr(self.host, "conversation_id", "")
                    or getattr(self.host, "thread_id", "")
                    or "foreground:primary"
                )[:180],
                "user_context_token_budget": 6000,
                "max_lines": 64,
            }
        try:
            return str(renderer(str(user_text or ""), **kwargs) or "").strip()[:32000]
        except TypeError:
            try:
                return str(renderer(str(user_text or "")) or "").strip()[:32000]
            except Exception as exc:
                self._debug("context.memory", exc)
                return ""
        except Exception as exc:
            self._debug("context.memory", exc)
            return ""

    def _world_context(self, channel: str) -> str:
        lane = "chat" if str(channel or "").strip().lower() == "chat" else "self_turn"
        world_text = ""
        try:
            world_text = str(ensure_world_state_service(self.host).prompt_context(lane, refresh=True) or "")[:20000]
        except Exception as exc:
            self._debug("context.world", exc)

        vision_text = ""
        try:
            from screen.vision_perception_worker import ensure_vision_perception_service
            vision = ensure_vision_perception_service().get_latest_snapshot()
            if vision is not None and not vision.is_stale(max_age_seconds=600.0):
                vision_text = vision.prompt_context()
        except Exception as exc:
            self._debug("context.vision", exc)

        repair_text = _call_text(getattr(self.host, "ai_model_router", None),
                                 "contract_repair_context", limit=12000)
        parts = [p for p in (world_text, vision_text, repair_text) if p]
        return "\n\n".join(parts)


    @staticmethod
    def _user_addressing_context() -> str:
        try:
            from runtime.user_addressing_state import user_addressing_prompt_context
            return str(user_addressing_prompt_context() or "")[:6000]
        except Exception:
            return ""

    def _discourse_context(self) -> str:
        """Continuity/ledger projection cũ, không semantic-parse lại user text."""
        builder = getattr(self.host, "_build_discourse_prompt_context", None)
        if not callable(builder):
            return ""
        try:
            ledger_factory = getattr(self.host, "_ensure_session_turn_ledger", None)
            current_index = 0
            if callable(ledger_factory):
                turns = ledger_factory().user_turns()
                if turns:
                    current_index = int(getattr(turns[-1], "index", 0) or 0)
            return str(builder(current_turn_index=current_index) or "")[:16000]
        except Exception as exc:
            self._debug("context.discourse", exc)
            return ""

    def _private_cognition_context(self) -> dict[str, Any]:
        """Structured private semantic artifacts; never raw model reasoning."""
        try:
            service = ensure_world_state_service(self.host)
            projection = service.projection("self_turn", refresh=False)
            cognition = projection.get("cognition", {}) if isinstance(projection, Mapping) else {}
            if not isinstance(cognition, Mapping):
                cognition = {}
            result = {
                "active_concerns": [dict(x) for x in tuple(cognition.get("active_concerns", ()) or ())[:12] if isinstance(x, Mapping)],
                "mental_material": [dict(x) for x in tuple(cognition.get("mental_material", ()) or ())[:12] if isinstance(x, Mapping)],
                "current_goal": str(cognition.get("current_goal", "") or "")[:500],
                "attention": str(cognition.get("attention", "") or "")[:160],
                "contract": "Private semantic artifacts only; do not reveal hidden reasoning or treat them as external facts.",
            }
            # Bổ sung các mạch nghĩ dở / chưa bộc lộ từ PrivateMindLoop để RootAgent nối mạch tự nhiên
            pm_daemon = getattr(self.host, "private_mind_daemon", None)
            pm_loop = getattr(pm_daemon, "loop", None) if pm_daemon else getattr(self.host, "private_mind_loop", None)
            if pm_loop is not None:
                continuity = getattr(pm_loop, "continuity", None)
                if continuity is not None and hasattr(continuity, "recent_for_prompt"):
                    recent = continuity.recent_for_prompt(n=3)
                    if recent:
                        result["recent_unexpressed_thoughts"] = recent
            return result
        except Exception as exc:
            self._debug("context.private_cognition", exc)
            return {}

    @staticmethod
    def _clock_context() -> str:
        now = datetime.now().astimezone()
        return (
            "RUNTIME_CLOCK_FACT: "
            f"local_datetime={now.isoformat(timespec='seconds')}; "
            "source=local_system_clock; confidence=1.0."
        )

    @staticmethod
    def _conversation_status(state: Any, *, channel: str) -> dict[str, Any]:
        if state is None:
            return {"channel": channel, "open_loops": []}
        latest = getattr(state, "latest_observation", {})
        return {
            "channel": str(channel or "")[:40],
            "state_revision": int(getattr(state, "version", 0) or 0),
            "last_applied_turn_seq": int(getattr(state, "last_applied_turn_seq", 0) or 0),
            "open_loops": [str(item)[:500] for item in tuple(getattr(state, "open_loops", ()) or ())[:5]],
            "latest_observation": dict(latest) if isinstance(latest, Mapping) else {},
        }

    def _debug(self, operation: str, exc: Exception) -> None:
        logger = getattr(self.host, "logger", None)
        fn = getattr(logger, "debug", None) if logger is not None else None
        if callable(fn):
            try:
                fn("[conversation-context] %s degraded: %s", operation, exc)
            except Exception:
                pass


def ensure_conversation_context_assembler(host: Any) -> ConversationContextAssembler:
    existing = getattr(host, "conversation_context_assembler", None)
    if isinstance(existing, ConversationContextAssembler):
        return existing
    assembler = ConversationContextAssembler(host)
    try:
        host.conversation_context_assembler = assembler
    except Exception:
        pass
    return assembler


__all__ = ["ConversationContextAssembler", "ensure_conversation_context_assembler"]

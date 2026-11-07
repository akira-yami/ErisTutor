# -*- coding: utf-8 -*-
"""
runtime/tutor_wiring.py
=======================

Nối Curiosity Tutor vào runtime thật. Mọi adapter đều phòng thủ: Master_Switch tắt ⇒
`build_tutor_daemon_from_runtime` trả None (không tác động gì); thiếu subsystem ⇒ adapter
đó None/no-op chứ không bao giờ ném vào hot path.

LƯU Ý AN TOÀN: đường ghi vào trí nhớ dài hạn là nhạy cảm. Adapter commit claim
(`_build_ingest_fn`) chỉ chạy khi tìm thấy đường commit rõ ràng; nếu không, nó trả 0 và
ghi cảnh báo một lần — KHÔNG đoán API rồi ghi rác vào memory.
"""

from __future__ import annotations

from runtime.telemetry_text_policy import is_generic_telemetry_line
from runtime.diagnostic_logging import safe_log

import re
from typing import Any, Callable, Optional

try:  # pragma: no cover
    from cortex.tutor.secret_scrubber import scrub_secrets
except Exception:  # pragma: no cover
    def scrub_secrets(text: Any) -> str:  # type: ignore
        return str(text or "")





def build_tutor_daemon_from_runtime(runtime_owner: Any) -> Optional[Any]:
    """Dựng TutorDaemon đã nối adapter, hoặc None nếu Master_Switch tắt / lỗi build."""

    logger = getattr(runtime_owner, "logger", None)

    try:
        from cortex.tutor.config import TutorConfig, TutorLogger, TutorRiskGuard
        from cortex.tutor.gap_collector import GapCollector
        from cortex.tutor.curiosity_gate import CuriosityGate
        from cortex.tutor.question_formulator import QuestionFormulator
        from cortex.tutor.claim_digest import ClaimDigest
        from cortex.tutor.share_impulse import LearningShareImpulse
        from llm.tutor_backend import GeminiWebBackend
        from runtime.tutor_daemon import TutorDaemon, TutorStageEmitter
        from cortex.tutor.knowledge_classifier import KnowledgeClassifier
        from cortex.tutor.curiosity_router import CuriosityRouter
    except Exception as exc:  # pragma: no cover
        _warn(logger, f"tutor: không import được module ({exc!r})")
        return None

    tutor_logger = TutorLogger(sink=logger)
    config = TutorConfig.from_env(tutor_logger)
    if not config.enabled:
        # Master_Switch tắt: không dựng gì, không mở trình duyệt, không truy cập mạng.
        return None

    _info(logger, "tutor: Master_Switch BẬT — đang dựng Curiosity Tutor daemon")

    generate_fn = _build_generate_fn(runtime_owner, logger)
    ingest_fn = _build_ingest_fn(runtime_owner, tutor_logger, logger)
    distill_fn = _build_distill_fn(runtime_owner, logger)
    thought_sink = _build_thought_sink(runtime_owner, logger)
    drive_fn = _build_drive_fn(runtime_owner)
    already_known_fn = _build_already_known_fn(runtime_owner)

    collector = GapCollector(config)
    formulator = QuestionFormulator(config, generate_fn=generate_fn)
    backend = GeminiWebBackend(config, logger=tutor_logger)
    digest = ClaimDigest(config, ingest_fn=ingest_fn, distill_fn=distill_fn)
    share = LearningShareImpulse(
        config,
        formulator=formulator,
        thought_sink=thought_sink,
        novelty_fn=None,
        logger=tutor_logger,
    )
    emitter = TutorStageEmitter(transport=getattr(runtime_owner, "transport", None), logger=tutor_logger)
    daemon = TutorDaemon(
        config,
        collector=collector,
        gate=CuriosityGate(config),
        formulator=formulator,
        backend=backend,
        digest=digest,
        share_impulse=share,
        classifier=KnowledgeClassifier(generate_fn=generate_fn),
        router=CuriosityRouter(generate_fn=generate_fn, already_known_fn=already_known_fn),
        emitter=emitter,
        risk_guard=TutorRiskGuard(config),
        logger=tutor_logger,
        drive_fn=drive_fn,
    )
    # Cho tick truy cập collector để nạp gap.
    runtime_owner._tutor_collector = collector

    # API "đi hỏi theo yêu cầu": cognition gọi khi không chắc / user bảo tra cứu.
    def request_tutor_lookup(topic, *, context="", kind_hint="durable", stake=0.7):
        try:
            import time as _t
            now = _t.time()
            topic = scrub_secrets(topic)
            context = scrub_secrets(context)
            gap = collector.collect_from_uncertainty(
                topic, context=context, stake=stake, now=now, kind_hint=kind_hint, force=True,
            )
            if gap is None:
                return False
            collector.push(gap)
            # Chạy ngay (forced bỏ qua idle/rate-limit; vẫn tôn trọng risk guard).
            ep = daemon.run_once(idle=False, now=now)
            if ep is None:
                _warn(logger, f"tutor request_lookup không chạy được episode topic={str(topic)[:120]!r}")
                return False
            if getattr(ep, "fail_reason", None):
                _warn(logger, f"tutor request_lookup failed reason={getattr(ep, 'fail_reason', '')} topic={str(topic)[:120]!r}")
            return ep is not None
        except Exception as exc:
            _warn(logger, f"tutor request_lookup lỗi ({exc!r})")
            return False

    runtime_owner.request_tutor_lookup = request_tutor_lookup

    # API "tra rồi TRẢ LỜI ngay". Khi research=True hoặc search=True, dùng relay nhẹ:
    # local/open-model đặt câu hỏi cho Tutor Gemini Web, nhận raw answer, rồi local/heuristic
    # chấm lại trước khi trả cho Eris. Không gọi Gemini API/Google grounding fallback.
    def request_tutor_answer(topic, *, context="", research=False, search=False):
        try:
            safe_topic = scrub_secrets(topic)
            safe_context = scrub_secrets(context)
            runtime_owner._last_tutor_research_artifact = {}
            runtime_owner._last_tutor_research_thread = {}
            runtime_owner._last_tutor_research_metrics = {}
            runtime_owner._last_tutor_research_memory_decision = {}
            if search and callable(getattr(daemon, "search_now", None)):
                answer = daemon.search_now(safe_topic, context=safe_context, critic_fn=generate_fn)
            elif research and callable(getattr(daemon, "research_now", None)):
                answer = daemon.research_now(safe_topic, context=safe_context, critic_fn=generate_fn)
            else:
                answer = daemon.ask_now(safe_topic, context=safe_context)
            runtime_owner._last_tutor_research_artifact = dict(getattr(daemon, "last_research_bundle", {}) or {})
            runtime_owner._last_tutor_research_thread = dict(getattr(daemon, "last_research_thread", {}) or {})
            runtime_owner._last_tutor_research_metrics = dict(getattr(daemon, "last_research_metrics", {}) or {})
            runtime_owner._last_tutor_research_memory_decision = dict(getattr(daemon, "last_research_memory_decision", {}) or {})
            if answer:
                return scrub_secrets(answer)
            _warn(logger, f"tutor request_answer không có kết quả topic={str(safe_topic)[:120]!r}")
            return None
        except Exception as exc:
            _warn(logger, f"tutor request_answer lỗi ({exc!r})")
            return None

    runtime_owner.request_tutor_answer = request_tutor_answer

    _install_knowledge_provider(runtime_owner, logger)
    return daemon


def _install_knowledge_provider(runtime_owner: Any, logger: Any) -> None:
    """Nối claim Tutor đã học vào đường memory recall đang sống.

    SituationModel shadow đã bị xóa; provider không còn giả vờ chờ một pipeline
    sẽ không bao giờ được dựng nữa.
    """

    from cortex.tutor.knowledge_provider import KnowledgeProvider

    store = _reach_semantic_store(runtime_owner)
    if store is None or not callable(getattr(store, "recall", None)):
        _warn(logger, "tutor: không tìm thấy semantic store cho knowledge provider")
        return

    def recall_fn(query: str):
        try:
            return store.recall(query, limit=4)
        except Exception:
            return []

    runtime_owner.tutor_knowledge_provider = KnowledgeProvider(recall_fn)
    _info(logger, "tutor: knowledge provider đã nối vào memory recall")


def tutor_tick(runtime_owner: Any, *, observer_frame: Any = None, channel: str = "", snapshot: Any = None) -> None:
    """Nạp gap (observer + violation) rồi chạy một episode nếu đủ điều kiện. Không bao giờ ném."""

    import time as _time

    daemon = getattr(runtime_owner, "tutor_daemon", None)
    collector = getattr(runtime_owner, "_tutor_collector", None)
    if daemon is None or collector is None:
        return
    try:
        now = _time.time()
        # Nguồn 1: observer 'unknown' — kèm đóng gói nội dung màn hình thật để Gemini đoán.
        if observer_frame is not None:
            try:
                screen_ctx = _screen_context_from_snapshot(snapshot)
                collector.push(collector.collect_from_observer(observer_frame, now=now, screen_context=screen_ctx))
            except Exception as exc:
                _warn(getattr(runtime_owner, "logger", None), f"tutor observer collection failed: {exc!r}")
        idle = (channel == "autonomous") and not bool(getattr(runtime_owner, "chat_mode", False))
        daemon.run_once(idle=idle, now=now)
    except Exception as exc:
        _warn(getattr(runtime_owner, "logger", None), f"tutor tick error: {exc!r}")


# ---------------------------------------------------------------------------
# Adapters (phòng thủ)
# ---------------------------------------------------------------------------

def _build_generate_fn(runtime_owner: Any, logger: Any) -> Optional[Callable[[str], str]]:
    """Local hoặc Remote LLM text gen để sinh câu hỏi/phân loại. None ⇒ heuristic fallback."""

    import os
    import json as _json
    import hashlib
    import time as _time

    llm_service = getattr(runtime_owner, "llm_service", None)
    use_remote = (
        os.getenv("ERIS_TUTOR_USE_REMOTE_LLM", "0").strip().lower() in {"1", "true", "yes", "on"}
    )

    if use_remote and llm_service is not None:
        remote_client = getattr(llm_service, "tutor_client", None) or getattr(llm_service, "curator_client", None)
        if remote_client is not None:
            # PDF annotation, pages 14 and 20: background Tutor owns a budget
            # per topic, not merely a global timer.  Repeated classifier/router
            # prompts for the same knowledge gap are coalesced and cooled down.
            last_remote_by_topic: dict[str, float] = {}
            from runtime.topic_budget import TopicBudget
            try:
                topic_hourly_cap = max(1, int(os.getenv("ERIS_TUTOR_TOPIC_MAX_CALLS_PER_HOUR", "2") or 2))
            except (TypeError, ValueError):
                topic_hourly_cap = 2
            topic_budget = TopicBudget(max_calls=topic_hourly_cap, window_seconds=3600.0)

            def generate_fn(prompt: str) -> str:
                try:
                    messages = [
                        {"role": "system", "content": "Trả về JSON gọn dạng {\"text\": \"...\"} bằng tiếng Việt. Tuyệt đối không thêm lời dẫn hay giải thích ngoài JSON."},
                        {"role": "user", "content": prompt},
                    ]
                    from runtime.inference_broker import GLOBAL_INFERENCE_BROKER, inference_context
                    # chat_mode may stay enabled for the whole session; only an
                    # active broker user turn makes this a required foreground job.
                    foreground = GLOBAL_INFERENCE_BROKER.user_turn_active()
                    lane = "chat_tool_required" if foreground else "tutor_background"
                    topic_key = hashlib.sha1(
                        " ".join(str(prompt or "").lower().split()).encode("utf-8", "ignore")
                    ).hexdigest()[:16]
                    if not foreground:
                        from llm.token_budget import detect_provider
                        advice = GLOBAL_INFERENCE_BROKER.optional_lane_advice(
                            "tutor_background",
                            provider=detect_provider(
                                str(getattr(remote_client, "base_url", "") or ""),
                                str(getattr(remote_client, "model_name", "") or ""),
                            ),
                        )
                        if not bool(advice.get("allowed", True)) or not topic_budget.allow(topic_key):
                            return ""
                        try:
                            cooldown = max(
                                15.0,
                                float(os.getenv("ERIS_TUTOR_TOPIC_COOLDOWN_SEC", "180") or 180),
                            )
                        except (TypeError, ValueError):
                            cooldown = 180.0
                        cooldown *= max(1.0, float(advice.get("cooldown_multiplier", 1.0) or 1.0))
                        now = _time.time()
                        if now - last_remote_by_topic.get(topic_key, 0.0) < cooldown:
                            return ""
                    with inference_context(
                        lane,
                        optional=not foreground,
                        timeout_seconds=10.0,
                        cancel_scope="background:tutor" if not foreground else "",
                        coalesce_key=f"tutor:question_generation:{topic_key}" if not foreground else "",
                        idempotency_key=f"tutor:question_generation:{topic_key}" if not foreground else "",
                        metadata={"tutor_phase": "question_generation", "topic_key": topic_key},
                    ):
                        resp = remote_client.create_chat_completion(
                            messages=messages,
                            temperature=0.4,
                            frequency_penalty=0.0,
                            presence_penalty=0.0,
                            max_tokens=150,
                            response_format={"type": "json_object"},
                            timeout=10,
                        )
                    extractor = getattr(llm_service, "_extract_content", None)
                    content = extractor(resp) if callable(extractor) else ""
                    if not content and isinstance(resp, dict):
                        choices = resp.get("choices")
                        if isinstance(choices, list) and choices:
                            content = choices[0].get("message", {}).get("content", "")
                        else:
                            candidates = resp.get("candidates")
                            if isinstance(candidates, list) and candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                content = "".join(p.get("text", "") for p in parts if "text" in p)

                    content = content.strip()
                    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE).strip()
                    content = re.sub(r"\s*```$", "", content).strip()
                    data = _json.loads(content)
                    output = str(data.get("text", "") or "").strip()
                    if output and not foreground:
                        last_remote_by_topic[topic_key] = _time.time()
                        topic_budget.record(topic_key)
                    return output
                except Exception as exc:
                    _warn(logger, f"tutor remote generate_fn lỗi ({exc!r})")
                    return ""
            return generate_fn



def _build_distill_fn(runtime_owner: Any, logger: Any) -> Optional[Callable[[str], list]]:
    """Tách câu trả lời gia sư thành các fact ngắn, atomic qua local hoặc remote LLM.

    None ⇒ ClaimDigest tự fallback tách câu bằng heuristic. Lỗi LLM không bao giờ
    ném vào hot path — trả [] để digest dùng fallback.
    """

    import os
    import json as _json
    import hashlib
    import time as _time

    llm_service = getattr(runtime_owner, "llm_service", None)
    use_remote = (
        os.getenv("ERIS_TUTOR_USE_REMOTE_LLM", "0").strip().lower() in {"1", "true", "yes", "on"}
    )

    if use_remote and llm_service is not None:
        remote_client = getattr(llm_service, "curator_client", None) or getattr(llm_service, "tutor_client", None)
        if remote_client is not None:
            last_distill_by_topic: dict[str, float] = {}
            from runtime.topic_budget import TopicBudget
            try:
                distill_hourly_cap = max(1, int(os.getenv("ERIS_TUTOR_DISTILL_TOPIC_MAX_CALLS_PER_HOUR", "1") or 1))
            except (TypeError, ValueError):
                distill_hourly_cap = 1
            distill_budget = TopicBudget(max_calls=distill_hourly_cap, window_seconds=3600.0)

            def distill_fn(answer_text: str) -> list:
                text = str(answer_text or "").strip()
                if not text:
                    return []
                try:
                    messages = [
                        {
                            "role": "system",
                            "content": (
                                "Bạn tách kiến thức thành các sự thật ngắn, độc lập, không trùng. "
                                "Trả JSON dạng {\"facts\": [\"...\", ...]} bằng tiếng Việt, mỗi fact một câu "
                                "tự đứng được, tối đa 5 fact. Bỏ phần khách sáo/giải thích vòng vo."
                            ),
                        },
                        {"role": "user", "content": f"Tách đoạn sau thành các sự thật atomic:\n{text[:1200]}"},
                    ]
                    from runtime.inference_broker import GLOBAL_INFERENCE_BROKER, inference_context
                    # chat_mode may stay enabled for the whole session; only an
                    # active broker user turn makes this a required foreground job.
                    foreground = GLOBAL_INFERENCE_BROKER.user_turn_active()
                    lane = "chat_tool_required" if foreground else "tutor_background"
                    topic_key = hashlib.sha1(text[:1200].encode("utf-8", "ignore")).hexdigest()[:16]
                    if not foreground:
                        from llm.token_budget import detect_provider
                        advice = GLOBAL_INFERENCE_BROKER.optional_lane_advice(
                            "tutor_background",
                            provider=detect_provider(
                                str(getattr(remote_client, "base_url", "") or ""),
                                str(getattr(remote_client, "model_name", "") or ""),
                            ),
                        )
                        if not bool(advice.get("allowed", True)) or not distill_budget.allow(topic_key):
                            return []
                        try:
                            cooldown = max(
                                15.0,
                                float(os.getenv("ERIS_TUTOR_DISTILL_TOPIC_COOLDOWN_SEC", "300") or 300),
                            )
                        except (TypeError, ValueError):
                            cooldown = 300.0
                        cooldown *= max(1.0, float(advice.get("cooldown_multiplier", 1.0) or 1.0))
                        now = _time.time()
                        if now - last_distill_by_topic.get(topic_key, 0.0) < cooldown:
                            return []
                    with inference_context(
                        lane,
                        optional=not foreground,
                        timeout_seconds=10.0,
                        cancel_scope="background:tutor" if not foreground else "",
                        coalesce_key=f"tutor:distill:{topic_key}" if not foreground else "",
                        idempotency_key=f"tutor:distill:{topic_key}" if not foreground else "",
                        metadata={"tutor_phase": "distill", "topic_key": topic_key},
                    ):
                        resp = remote_client.create_chat_completion(
                            messages=messages,
                            temperature=0.2,
                            frequency_penalty=0.0,
                            presence_penalty=0.0,
                            max_tokens=300,
                            response_format={"type": "json_object"},
                            timeout=10,
                        )
                    extractor = getattr(llm_service, "_extract_content", None)
                    content = extractor(resp) if callable(extractor) else ""
                    if not content and isinstance(resp, dict):
                        choices = resp.get("choices")
                        if isinstance(choices, list) and choices:
                            content = choices[0].get("message", {}).get("content", "")
                        else:
                            candidates = resp.get("candidates")
                            if isinstance(candidates, list) and candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                content = "".join(p.get("text", "") for p in parts if "text" in p)

                    content = content.strip()
                    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE).strip()
                    content = re.sub(r"\s*```$", "", content).strip()
                    data = _json.loads(content)
                    facts = data.get("facts") or []
                    if isinstance(facts, (list, tuple)):
                        output = [str(f).strip() for f in facts if str(f or "").strip()][:5]
                        if output and not foreground:
                            last_distill_by_topic[topic_key] = _time.time()
                            distill_budget.record(topic_key)
                        return output
                except Exception as exc:
                    _warn(logger, f"tutor remote distill lỗi ({exc!r})")
                return []
            return distill_fn



def _build_ingest_fn(runtime_owner: Any, tutor_logger: Any, logger: Any) -> Optional[Callable[..., int]]:
    """Đường commit claim vào trí nhớ ngữ nghĩa (có embedding) qua memory core/kernel.

    Claim được lưu với tag "tutor"/"learned" + source provenance, confidence vừa phải, và
    storage_permission=True (commit nội bộ tin cậy). Store ngữ nghĩa đã gắn embedding_index
    ⇒ bài học có embedding, recall lần sau dùng được.
    """

    # Cho phép người dùng override hoàn toàn nếu muốn.
    custom = getattr(runtime_owner, "tutor_ingest_fn", None)
    if callable(custom):
        return custom

    committer = _reach_memory_commit(runtime_owner)
    if committer is None:
        _warn(logger, "tutor: không tìm thấy đường commit memory — claim sẽ không được lưu")
        return None

    def ingest_fn(text: str, *, source: str = "tutor", gap_id: str = "", citations=(), confidence: float = 0.66) -> int:
        claim = str(text or "").strip()
        if not claim:
            return 0
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            conf = 0.66
        conf = max(0.4, min(0.85, conf))
        tags = ["tutor", "learned", "curiosity"]
        if bool(citations):
            tags.append("cited")
        if conf < 0.58:
            tags.append("low_confidence")
        request = {
            "target": "semantic_memory",
            "content": claim[:480],
            "confidence": conf,
            "evidence_ids": [gap_id or "tutor"],
            "storage_permission": True,
            "permission_required": False,
            "decay_policy": "normal",
            "reason": "curiosity_tutor",
            "source_module": "curiosity_tutor",
            "tags": tags,
            "metadata": {
                "storage_permission": True,
                "trusted_internal_commit": True,
                "source": str(source or "tutor"),
                "gap_id": gap_id,
                "citations": list(citations or ()),
                "confidence": conf,
            },
        }
        try:
            decision = committer(request)
        except Exception as exc:
            _warn(logger, f"tutor: commit claim lỗi ({exc!r})")
            return 0
        outcome = str(getattr(decision, "outcome", "") or "")
        stored = str(getattr(decision, "stored_id", "") or "")
        return 1 if (outcome in {"accepted", "tentative"} and stored) else 0

    return ingest_fn


def _build_thought_sink(runtime_owner: Any, logger: Any) -> Optional[Any]:
    """Đẩy Share_Impulse vào internal_agenda nếu có. None ⇒ không khoe (an toàn)."""

    agenda = _reach_internal_agenda(runtime_owner)
    if agenda is None or not callable(getattr(agenda, "add", None)):
        return None

    class _AgendaSink:
        def push_share_impulse(self, highlight: str, *, priority: float) -> None:
            try:
                agenda.add(
                    kind="misc",
                    summary=str(highlight)[:240],
                    source="tutor",
                    speech_value=float(priority),
                    confidence=float(priority),
                    ttl_seconds=1800.0,
                )
            except Exception as exc:
                _warn(logger, f"tutor agenda sink failed: {exc!r}")

    return _AgendaSink()


def _build_already_known_fn(runtime_owner: Any) -> Optional[Callable[[str], bool]]:
    """True nếu Eris đã biết topic (recall trí nhớ trúng) ⇒ router skip, đỡ hỏi lại."""

    store = _reach_semantic_store(runtime_owner)
    if store is None or not callable(getattr(store, "recall", None)):
        return None

    def already_known(topic: str) -> bool:
        topic = str(topic or "").strip()
        if not topic:
            return False
        try:
            hits = store.recall(topic, limit=1, min_score=0.6)
            return bool(hits)
        except Exception:
            return False

    return already_known


def _build_drive_fn(runtime_owner: Any) -> Optional[Callable[[], float]]:
    def drive_fn() -> float:
        for attr in ("endogenous_drive_level", "curiosity_level"):
            val = getattr(runtime_owner, attr, None)
            if isinstance(val, (int, float)):
                return max(0.0, min(1.0, float(val)))
        return 1.0

    return drive_fn


# ---------------------------------------------------------------------------
# Reach helpers — dò mềm vào subsystem, không bám cứng.
# ---------------------------------------------------------------------------

def _reach_internal_agenda(runtime_owner: Any) -> Optional[Any]:
    for attr in ("internal_agenda", "agenda"):
        obj = getattr(runtime_owner, attr, None)
        if obj is not None and callable(getattr(obj, "add", None)):
            return obj
    state = getattr(runtime_owner, "rolling_internal_state", None) or getattr(runtime_owner, "_internal_state", None)
    if state is not None:
        agenda = getattr(state, "internal_agenda", None)
        if agenda is not None and callable(getattr(agenda, "add", None)):
            return agenda
    return None


def _screen_context_from_snapshot(snapshot: Any) -> str:
    """Đóng gói nội dung màn hình THẬT cho Gemini đoán ngữ cảnh.

    Chỉ trả về context khi có nội dung màn hình thực sự (OCR/đường dẫn/vision facts) —
    KHÔNG dá»±ng câu hỏi chỉ từ tên app/cửa sổ của một app chạy ngầm đang idle, và KHÔNG
    nhồi telemetry chung chung (active window is.../focus kind is.../no input...) như thể
    đó là chữ trên màn hình. Mục tiêu: ưu tiên vision thật thay vì hỏi suông vô nghĩa.
    LƯU Ý RIÊNG TƯ: nội dung màn hình (OCR) sẽ được gửi tới Gemini ở nhánh này.
    """

    if snapshot is None:
        return ""
    try:
        from cortex.tutor.config import scrub_identity
    except Exception:
        def scrub_identity(x):  # type: ignore
            return str(x or "")

    app = str(getattr(snapshot, "active_app", "") or "").strip()
    title = str(getattr(snapshot, "active_window_title", "") or "").strip()
    url = str(getattr(snapshot, "url_or_path_hint", "") or "").strip()
    ocr_raw = str(getattr(snapshot, "ocr_text", "") or "").strip()

    # Lọc telemetry chung chung khỏi phần OCR: chỉ giữ dòng là nội dung màn hình thật.
    ocr_lines = [line.strip() for line in ocr_raw.splitlines() if line.strip()]
    real_ocr_lines = [line for line in ocr_lines if not is_generic_telemetry_line(line)]
    ocr = "\n".join(real_ocr_lines).strip()

    vision_facts = [
        str(item).strip()
        for item in (getattr(snapshot, "vision_facts", ()) or ())
        if str(item).strip() and not is_generic_telemetry_line(str(item))
    ]

    # Không có ná»™i dung màn hình thật ⇒ không hỏi Gemini, để vision pipeline lo.
    has_real_content = bool(ocr or url or vision_facts)
    if not has_real_content:
        return ""

    parts = []
    if app:
        parts.append(f"app={app}")
    if title:
        parts.append(f"cửa sổ={title}")
    if url:
        parts.append(f"đường dẫn={url}")
    if vision_facts:
        parts.append("vision: " + " | ".join(vision_facts[:3])[:300])
    if ocr:
        parts.append(f"chữ trên màn hình: {ocr[:300]}")
    return scrub_identity(" | ".join(parts))[:500]


def _reach_memory_commit(runtime_owner: Any) -> Optional[Callable[[Any], Any]]:
    """Trả callable commit(request) từ memory_kernel/memory_core nếu có."""

    for attr in ("memory_kernel", "memory_core"):
        obj = getattr(runtime_owner, attr, None)
        if obj is not None and callable(getattr(obj, "commit", None)):
            return obj.commit
    return None


def _reach_semantic_store(runtime_owner: Any) -> Optional[Any]:
    """Trả semantic store (có recall + embedding_index) từ memory core/kernel."""

    candidates = []
    for attr in ("memory_core", "memory_kernel"):
        obj = getattr(runtime_owner, attr, None)
        if obj is None:
            continue
        candidates.append(obj)
        # kernel có thể bọc core qua .core/.cognition/.memory_cognition
        for inner in ("core", "cognition", "memory_cognition"):
            nested = getattr(obj, inner, None)
            if nested is not None:
                candidates.append(nested)
    for obj in candidates:
        store = getattr(obj, "semantic", None)
        if store is not None and callable(getattr(store, "recall", None)):
            return store
        if callable(getattr(obj, "recall", None)) and getattr(obj, "semantic", None) is not None:
            return getattr(obj, "semantic", None)
    return None


def _warn(logger: Any, message: str) -> None:
    safe_log(logger, "warning", "%s", message)


def _info(logger: Any, message: str) -> None:
    safe_log(logger, "info", "%s", message)


__all__ = ["build_tutor_daemon_from_runtime", "tutor_tick"]

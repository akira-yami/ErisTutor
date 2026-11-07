# -*- coding: utf-8 -*-
"""Adapter from a root-owned move to the existing model surface writer."""
from __future__ import annotations

import json
import re
import time
from typing import Any, Mapping, Sequence

from .contracts import ConversationEvent
from .delivery import SurfaceEnvelope
from .kernel import KernelTurn
from .speech_math_strip import strip_math_for_speech
from .writer import RenderedConversationMove


_SAFE_V03_ERROR = re.compile(r"^(?:v03_[a-z0-9_]+|public_writer_[a-z0-9_]+|[A-Z][A-Z0-9_]+)(?::[A-Z][A-Z0-9_,]*)?$")

# Câu đệm Nhật an toàn, dùng DUY NHẤT khi localizer fail SAU 1 retry (nhánh cuối
# cùng, hiếm gặp). Toàn hiragana hợp lệ nên qua được guard _contains_japanese_script
# và prepare's ja-contract; ngắn, trung tính, chỉ là một tiếng ngập ngừng tự nhiên.
# Không bịa thông tin, không lộ secret, không tự nhận đã làm gì — và KHÔNG để turn
# rơi vào trạng thái câm/no_public_commit.
_V03_SPEECH_JA_FILLER = "えっと…"


def _v03_error_code(exc: Exception) -> str:
    """Chỉ trích mã lỗi cơ học để log/rewrite; không lộ prompt hoặc token API.

    Exception của provider đôi lúc mang nguyên response/request. Không bao giờ
    đưa chuỗi lạ ấy vào telemetry hoặc prompt sửa câu của SurfaceWorker.
    """
    raw = str(exc).strip()
    return raw[:180] if len(raw) <= 180 and _SAFE_V03_ERROR.fullmatch(raw) else type(exc).__name__


def _prelocalize_v03_speech(payload: Mapping[str, Any], *, host: Any,
                            language_hint: str) -> dict[str, Any]:
    """Chốt bản TTS thực tế TRƯỚC prepare/verify, không dịch muộn sau PASS.

    Writer đôi lúc trả bản romaji/Việt trong `speech_text` hoặc trong
    `utterance.speech.text`. Stage ưu tiên trường nested nên PHẢI sửa cả hai
    trên bản sao candidate, không mutate object router trả. Nếu không thể dịch
    ra kana/kanji hợp lệ, từ chối candidate; không phát bản chưa kiểm.

    SpeechLocalizer là owner dịch ĐÃ CÓ trong Eris, không tạo translator mới.
    Hàm này không cập nhật history/body, chỉ yêu cầu một transcript candidate.
    """
    result = dict(payload)
    if result.get("_separate_document"):
        # Không bao giờ lấy tài liệu hoặc display dài làm nguồn dịch khi có doc riêng.
        nested = result.get("utterance") if isinstance(result.get("utterance"), Mapping) else {}
        nested_speech = nested.get("speech") if isinstance(nested, Mapping) else None
        explicit = str(result.get("speech_text", "") or "").strip()
        if not explicit and isinstance(result.get("speech"), str):
            explicit = result["speech"].strip()
        if not explicit and isinstance(result.get("speech"), Mapping):
            explicit = str(result["speech"].get("text", "") or "").strip()
        if not explicit and isinstance(nested_speech, Mapping):
            explicit = str(nested_speech.get("text", "") or "").strip()
        if not explicit:
            raise RuntimeError("v03_document_speech_missing")
    from runtime.stage_contracts import _contains_japanese_script
    from runtime.utterance_spec import UtteranceSpec

    if str(language_hint or '').lower() in ('vi', 'vi-vn', 'vietnamese'):
        # The Vietnamese public surface is already canonical. Do not reuse a
        # translated Japanese surface, signature ending or fail-soft JA filler.
        #
        # Bong bóng Rich Chat render LaTeX/markdown bằng MathJax, nên text hiển
        # thị (result['text']) GIỮ nguyên công thức. Nhưng speech_text là nguồn
        # TTS: phải strip LaTeX/markdown, nếu không TTS đọc "\frac{3}{4}" thành
        # ký hiệu. Chỉ chuyển trên nhánh giọng đọc; không đụng text hiển thị.
        #
        # Nguồn nói ưu tiên speech_text, NHƯNG nếu nó là surface Nhật (writer trả
        # kana/kanji) thì bỏ, dùng text Việt chuẩn — mode Việt không tái dùng bản
        # dịch Nhật. Giữ invariant cũ trước khi strip math.
        candidate_speech = str(result.get('speech_text') or '').strip()
        if candidate_speech and _contains_japanese_script(candidate_speech):
            candidate_speech = ''
        raw_vietnamese = candidate_speech or str(result.get('text', '') or '').strip()
        vietnamese = strip_math_for_speech(raw_vietnamese)
        if not vietnamese and raw_vietnamese:
            vietnamese = "Mình gửi code ở đây nhé."
        if vietnamese:
            result.update(speech_text=vietnamese, speech_language='vi', tts_language='vi')
            contract = result.get('speech_contract')
            result['speech_contract'] = {
                **(dict(contract) if isinstance(contract, Mapping) else {}),
                'language': 'vi', 'locale': 'vi-VN',
            }
            speech = result.get('speech')
            if isinstance(speech, Mapping):
                result['speech'] = {**dict(speech), 'text': vietnamese, 'language': 'vi'}
            elif isinstance(speech, str):
                result['speech'] = vietnamese
            nested = result.get('utterance')
            if isinstance(nested, Mapping):
                revised = dict(nested)
                previous = revised.get('speech')
                revised['speech'] = {
                    **(dict(previous) if isinstance(previous, Mapping) else {}),
                    'text': vietnamese, 'language': 'vi',
                }
                result['utterance'] = revised
        return result
    if str(language_hint or '').lower() not in ('ja', 'jp', 'japanese'):
        return result
    display_text = str(result.get('text', '') or '').strip()
    spec = UtteranceSpec.from_mapping(result,
                                      fallback_text=display_text,
                                      default_speech_language='ja')
    # Nguồn để dịch: speech.text nếu có, nếu rỗng thì fallback sang display text
    # để writer trả speech_text rỗng KHÔNG dẫn tới prepare raise mismatch. Chỉ
    # bỏ qua khi THẬT SỰ không có gì để nói (cả speech lẫn display đều rỗng).
    # Nguồn để dịch/đọc phải sạch LaTeX trước: localizer dịch `\frac{...}` như
    # văn bản nên ký hiệu sẽ lọt vào TTS Nhật. Bubble vẫn giữ LaTeX ở display.
    source = strip_math_for_speech(str(spec.speech.text or '').strip() or display_text)
    if not source and (str(spec.speech.text or '').strip() or display_text):
        source = "コードを書いておいたよ。"
    if not source:
        return result
    if _contains_japanese_script(source):
        # UtteranceSpec/Stage có thể ưu tiên speech nested so với field phẳng.
        # Giữ metadata cùng một transcript thật, không để hai bản lệch nhau.
        return _apply_ja_speech(result, source)

    localizer = getattr(host, 'character_speech_localizer', None)
    if localizer is None:
        # Reuse instance/cache across turns on same host. Không gán cờ đã
        # localize khi kết quả không đúng ngôn ngữ.
        localizer = getattr(host, '_character_preverify_localizer', None)
        if localizer is None:
            from runtime.speech_localizer import SpeechLocalizer
            localizer = SpeechLocalizer()
            setattr(host, '_character_preverify_localizer', localizer)
    translate = getattr(localizer, 'localize', None)
    if not callable(translate):
        raise RuntimeError('v03_speech_localizer_unavailable')

    from runtime.speech_localizer import SpeechLocalizationCancelled

    def _try_localize() -> str:
        return str(translate(source, language_hint='ja', strict=True) or '').strip()

    # Một retry nhẹ trong budget còn lại (ERIS_TTS_LOCALIZER_TIMEOUT_SEC giữ 45s
    # ở localizer, không chỉnh ở đây). Cancellation KHÔNG retry: nó là preempt
    # hợp lệ nên ném thẳng v03_speech_localization_cancelled.
    converted = ''
    for _attempt in range(2):
        try:
            candidate = _try_localize()
        except Exception as exc:
            if isinstance(exc, SpeechLocalizationCancelled):
                raise RuntimeError('v03_speech_localization_cancelled') from None
            candidate = ''
        if candidate and _contains_japanese_script(candidate):
            converted = candidate
            break

    if converted:
        return _apply_ja_speech(result, converted)

    # Nhánh CUỐI CÙNG: localize fail sau retry. Fail-SOFT nhưng KHÔNG câm — phát
    # một câu đệm Nhật an toàn (b) để prepare pass và vẫn có tiếng. Không giữ
    # global ja-pin thay đổi; không bịa; không nhận đã làm gì.
    _log_localizer_fallback(host)
    return _apply_ja_speech(result, _V03_SPEECH_JA_FILLER)


def _apply_ja_speech(result: dict[str, Any], converted: str) -> dict[str, Any]:
    """Ghi transcript Nhật đã chốt vào cả field phẳng lẫn nested, đồng bộ nhau."""
    result['speech_text'] = converted
    result['speech_language'] = 'ja'
    result['tts_language'] = 'ja'
    top_speech = result.get('speech')
    if isinstance(top_speech, Mapping):
        result['speech'] = {**dict(top_speech), 'text': converted, 'language': 'ja'}
    elif isinstance(top_speech, str):
        result['speech'] = converted
    nested = result.get('utterance')
    if isinstance(nested, Mapping):
        revised = dict(nested)
        previous = revised.get('speech')
        revised['speech'] = ({**dict(previous), 'text': converted, 'language': 'ja'}
                             if isinstance(previous, Mapping) else
                             {'text': converted, 'language': 'ja'})
        result['utterance'] = revised
    return result


def _log_localizer_fallback(host: Any) -> None:
    """Log một dòng cảnh báo an toàn cho nhánh filler cuối; không lộ nội dung."""
    logger = getattr(host, 'logger', None)
    method = getattr(logger, 'warning', None)
    if callable(method):
        try:
            method('[character-v03] %s', 'v03_speech_localizer_fallback_ja_filler')
        except Exception:
            pass


def _inspect_writer_manifest(payload: Mapping[str, Any], evidence_registry: Any,
                             evidence_policy: Any = None) -> Any:
    """Gate typed-domain sơ bộ cho từng candidate, kể cả candidate rewrite.

    Không làm semantic verification (Task 5/6 của v0.3). Ref hiện diện chỉ là
    điều kiện cần, tuyệt đối chưa chứng minh toàn bộ nghĩa của câu.
    """
    from runtime.claim_domain_contract import (
        ClaimManifestEnvelope, inspect_claim_manifest, validate_claim_manifest,
    )
    from .claim_evidence import EvidenceRegistry
    envelope = payload.get("_typed_claim_manifest")
    if not isinstance(envelope, ClaimManifestEnvelope):
        # Fake router của những test V12 không có manifest là legacy, không
        # ngầm gán compliance mới. Router thật luôn trả _typed_claim_manifest.
        if "claim_manifest" not in payload:
            return None
        envelope = inspect_claim_manifest(payload)
    current = inspect_claim_manifest(payload)
    if envelope.structural_status != "valid":
        raise RuntimeError("public_writer_manifest_" + envelope.structural_status)
    if current.structural_status != "valid" or current.raw != envelope.raw:
        raise RuntimeError("public_writer_manifest_tampered")

    if isinstance(evidence_registry, EvidenceRegistry):
        from .claim_evidence import build_writer_evidence_policy
        # Chính catalog đã cho Writer nhìn thấy phải là nguồn allowlist; không
        # được âm thầm cho phép thêm ref chỉ vì nó nằm trong registry private.
        policy = evidence_policy or build_writer_evidence_policy(evidence_registry)
        decision = validate_claim_manifest(
            envelope,
            allowed_domains=tuple(policy.allowed_domains),
            allowed_evidence_refs=tuple(policy.allowed_refs),
            evidence_domains_by_ref=policy.refs_by_domain,
        )
        if not decision.allowed:
            # Chỉ log mã cơ học, tuyệt đối không log raw ref/user text/API URL.
            # MISSING_REF: claim lịch sử nhưng không khai nguồn;
            # UNKNOWN_REF: writer tự đặt ref / lấy ref unknown;
            # WRONG_SCOPE: ref tồn tại nhưng không thuộc domain đã khai.
            suffix = (':' + ','.join(decision.evidence_failure_codes)
                      if decision.evidence_failure_codes else '')
            raise RuntimeError('public_writer_' + decision.reason + suffix)
    return envelope


class RuntimeConversationWriter:
    def __init__(self, host: Any) -> None:
        self.host = host

    def _log_v03(self, level: str, *, turn: KernelTurn, attempt: int,
                 stage: str, status: str, code: str, duration_ms: int) -> None:
        """Log ranh giới từng candidate; không lưu câu user/speech hay evidence."""
        logger = getattr(self.host, 'logger', None)
        method = getattr(logger, level, None)
        if callable(method):
            try:
                method(
                    '[character-v03] writer_candidate run=%s generation=%s '
                    'attempt=%s/1 stage=%s status=%s code=%s duration_ms=%s',
                    turn.run_id, turn.generation, attempt, stage, status, code,
                    duration_ms,
                )
            except Exception:
                pass
        try:
            from runtime.observability import GLOBAL_OBSERVABILITY
            GLOBAL_OBSERVABILITY.log(
                'character_writer_candidate',
                tags={'stage': stage, 'status': status, 'code': code},
                fields={'run_id': turn.run_id, 'generation': turn.generation,
                        'attempt': attempt, 'duration_ms': duration_ms},
                status='ok' if status == 'pass' else 'degraded',
            )
        except Exception:
            # Telemetry không được làm hỏng conversation runtime.
            pass

    def _realize_v03(self, *, generate: Any, kwargs: dict[str, Any], turn: KernelTurn,
                     event: ConversationEvent, runtime_context: Mapping[str, Any],
                     evidence_registry: Any, evidence_policy: Any,
                     move_payload: Mapping[str, Any]) -> RenderedConversationMove:
        """Một Writer -> chuẩn bị public artifact; KHÔNG gọi semantic verifier.

        RootAgent giữ quyền quyết định lời nói. Sai ngữ nghĩa được quan sát
        trực diện và sửa tại Root/Writer/evidence preparation, thay vì dùng
        một LLM khác để chặn hội thoại. Những lỗi cơ học như parse sai,
        payload rỗng, TTS/UDP không hợp lệ vẫn được giữ nguyên.

        Không ghi memory, history hay dispatch ở đây; đó là việc của delivery.
        """
        from .delivery_adapter import RuntimeConversationDeliveryAdapter
        from runtime.ai_orchestrated.model_router import _public_display_language_mismatch

        started_at = time.monotonic()
        stage = 'generate'
        base = dict(kwargs)
        base['allow_language_repair'] = False
        from runtime.speech_contract import configured_speech_language
        speech_language = configured_speech_language()
        base['speech_language_hint'] = speech_language
        try:
            payload = generate(**base)
            if configured_speech_language() != speech_language:
                raise RuntimeError('v03_speech_configuration_changed')
            if not isinstance(payload, Mapping):
                raise RuntimeError('v03_writer_invalid_payload')
            # Manifest chỉ là thông tin chẩn đoán khi model cung cấp hợp lệ;
            # không lấy model-generated domain/ref làm giấy phép phát ngôn.
            # Nó vẫn có thể được phân tích offline, nhưng không block Writer.
            stage = 'surfaces'
            display = str(payload.get('text', '') or '').strip()
            if not display:
                raise RuntimeError('v03_missing_public_surface')
            if _public_display_language_mismatch(
                    display, str(base.get('language_hint', 'vi') or 'vi')):
                raise RuntimeError('v03_display_language_mismatch')
            stage = 'localize'
            if turn.move.document_source:
                # Tài liệu không đi qua localizer; thiếu speech độc lập thì fail-fast.
                payload = {**payload, "_separate_document": True}
            payload = _prelocalize_v03_speech(
                payload, host=self.host, language_hint=speech_language,
            )
            # Giữ SourceGroundingReport để trace/test. Không để bộ phân loại
            # claim ở tầng surface quyết định sự im lặng của Eris.
            engine = runtime_context.get('__source_grounding_engine')
            report = runtime_context.get('__source_grounding_report')
            # Registry do Root cung cấp; tuyệt đối không lấy capability từ text của LLM.
            registry = runtime_context.get('__root_capabilities')
            stage = 'prepare'
            if configured_speech_language() != speech_language:
                raise RuntimeError('v03_speech_configuration_changed')
            prepared = RuntimeConversationDeliveryAdapter(self.host).prepare_candidate(
                turn=turn, event=event, payload=dict(payload),
                source_fingerprint=getattr(evidence_registry, 'fingerprint', ''),
            )
            if turn.move.document_source or turn.move.presentation_blocks or turn.move.tool_artifact_bundles:
                from dataclasses import replace as _replace_public_doc
                prepared = _replace_public_doc(
                    prepared, document_source=turn.move.document_source,
                    presentation_blocks=turn.move.presentation_blocks,
                    tool_artifact_bundles=turn.move.tool_artifact_bundles,
                )
            from .public_output_artifact import validate_prepared
            validate_prepared(prepared)
            self._log_v03('info', turn=turn, attempt=1, stage='prepared',
                          status='pass', code='WRITER_DIRECT',
                          duration_ms=int((time.monotonic()-started_at)*1000))
            return RenderedConversationMove(
                surface=SurfaceEnvelope(
                    display_text=prepared.display_text,
                    speech_text=prepared.speech_text,
                    language=str(payload.get('display_language',base.get('language_hint','vi')) or 'vi'),
                    alignment_safe=bool(payload.get('alignment_safe',False)),
                    metadata=dict(payload),
                ),
                decision_payload=move_payload,
                metadata={
                    'writer':'runtime_model_router_direct',
                    'event_kind':event.kind, 'user_text':event.text,
                    'provider_payload':dict(payload),
                    'source_grounding_report':report if engine is not None else None,
                    'source_grounding_engine':engine,
                    # Luồng Writer V03 trước đây luôn gửi (), khiến receipt
                    # Tutor không bao giờ được gọi trong bản production.
                    'root_capabilities_used': tuple(
                        registry.invocations_for(event.event_id)
                    ) if callable(getattr(registry, 'invocations_for', None)) else (),
                    'event_id': event.event_id,
                    'evidence_registry':evidence_registry,
                    'character_portrait':runtime_context.get('__character_portrait'),
                    'prepared_public_output':prepared,
                },
            )
        except Exception as exc:
            # Tuyệt đối không ghi raw provider exception (có thể có credential).
            code = _v03_error_code(exc)
            self._log_v03('warning', turn=turn, attempt=1, stage=stage,
                          status='rejected', code=code,
                          duration_ms=int((time.monotonic()-started_at)*1000))
            raise RuntimeError('v03_candidate_rejected:'+stage+'='+code) from None

    def realize(
        self,
        *,
        turn: KernelTurn,
        event: ConversationEvent,
        recent_history: Sequence[Mapping[str, Any]],
        runtime_context: Mapping[str, Any],
    ) -> RenderedConversationMove:
        canvas = getattr(self.host, "learning_canvas_service", None)
        if canvas is not None and turn.move.canvas_segments:
            from runtime.learning_canvas.narration import valid_parts
            from .canvas_narration import realize_canvas
            parts = valid_parts(canvas.pack, turn.canvas_scope, turn.move.canvas_segments)
            if parts:
                return realize_canvas(self, turn=turn, event=event, recent_history=recent_history,
                                      runtime_context=runtime_context, parts=parts)
        return self._realize_one(turn=turn, event=event, recent_history=recent_history,
                                 runtime_context=runtime_context)

    def _realize_one(self, *, turn, event, recent_history, runtime_context):
        """Internal realization of one Root brief, under the kernel's writer call."""
        router = getattr(self.host, "ai_model_router", None)
        canvas = getattr(self.host, "learning_canvas_service", None)
        generate = getattr(router, "generate_public_reply", None)
        if turn.move.literal_dialogue:
            # Prose recovered from Root is already dialogue, not a writing brief.
            # Keep it verbatim and reuse localization/prepare/TTS downstream.
            def generate(**kwargs):
                return {"should_speak": True, "text": turn.move.say, "speech_text": "",
                        "display_language": str(kwargs.get("language_hint", "vi")),
                        "llm_malformed_json_repair": True}
        if not callable(generate):
            raise RuntimeError("public_writer_unavailable")

        move_payload = turn.move.to_dict()
        # Writer cần biết ý định nói, KHÔNG đọc nguyên tài liệu thành lời thoại.
        writer_brief = {k: v for k, v in move_payload.items()
                        if k not in {"document_source", "presentation_blocks", "interaction_choice"}}
        root_context = (
            "ROOT_CONVERSATION_MOVE_V12: "
            + json.dumps(writer_brief, ensure_ascii=False, separators=(",", ":"))
            + ". Realize this move with natural cadence. Small reactions, fragments or "
              "expressive pauses are fine; do not add a new topic, steering question, "
              "teaching step or continuation. The `after` field is lifecycle metadata."
        )
        prior_discourse = str(runtime_context.get("discourse_context", "") or "").strip()
        discourse_context = "\n\n".join(part for part in (prior_discourse, root_context) if part)
        # Canonical V12 context dùng *_context. Hai *_summary chỉ là fallback
        # tương thích cho signal/test cũ, không phải một schema semantic khác.
        personality_context = str(
            runtime_context.get("personality_context", "")
            or runtime_context.get("personality_summary", "")
            or ""
        )
        # Character v0.2: tuyệt đối không render portrait lần hai ở writer.
        # Dùng đúng canonical text mà ContextAssembler đã giao cho Root.
        portrait_render = runtime_context.get("__character_portrait_render")
        if portrait_render is not None and personality_context != portrait_render.text:
            raise RuntimeError("character_portrait_root_writer_mismatch")
        relationship_context = str(
            runtime_context.get("relationship_context", "")
            or runtime_context.get("relationship_summary", "")
            or ""
        )
        engagement = runtime_context.get("engagement_context", "")
        if isinstance(engagement, Mapping):
            engagement = json.dumps(dict(engagement), ensure_ascii=False, separators=(",", ":"))

        # Chỉ mở rộng bằng receipt của ĐÚNG run này, sau khi Root đã hoàn tất
        # capability. Không lấy Root.say làm chứng cứ và chưa bật semantic gate.
        from .claim_evidence import EvidenceRegistry, extend_evidence_registry, build_writer_evidence_policy
        evidence_registry = runtime_context.get("__evidence_registry")
        registry = runtime_context.get("__root_capabilities")
        if isinstance(evidence_registry, EvidenceRegistry):
            receipts_for = getattr(registry, "result_receipts_for", None)
            receipts = receipts_for(event.event_id, run_id=turn.run_id,
                                    generation=turn.generation) if callable(receipts_for) else ()
            evidence_registry = extend_evidence_registry(evidence_registry, receipts)
        else:
            evidence_registry = None

        generate_kwargs = {
            "chat_history": tuple(recent_history),
            "user_text": str(event.text or ""),
            "memory_context": str(runtime_context.get("memory_context", "") or ""),
            "grounding_context": str(runtime_context.get("grounding_context", "") or ""),
            "relationship_context": relationship_context,
            "personality_context": personality_context,
            "affective_voice_state": runtime_context.get("affective_voice_state", {}),
            "interrupt_context": str(runtime_context.get("interrupt_context", "") or ""),
            "engagement_context": str(engagement or ""),
            "routing_context": str(runtime_context.get("routing_context", "") or ""),
            "discourse_context": discourse_context,
            "use_escalation": bool(runtime_context.get("use_escalation", False)),
            # Surface worker vẫn chỉ wording một semantic move đã khóa (không mở
            # reasoning_effort cao để tránh một vòng cognition dài thứ hai).
            # Default thinking TẮT cho lane user-visible để trả lời nhanh (thinking
            # ON làm chậm 1.5–2× và nới cửa drop); caller vẫn có thể override qua
            # runtime_context["thinking_mode"].
            "reasoning_effort": runtime_context.get("reasoning_effort", "low"),
            "thinking_mode": runtime_context.get("thinking_mode", "disabled"),
            "allow_fallback": bool(runtime_context.get("allow_fallback", False)),
            "language_hint": str(runtime_context.get("language_hint", "vi") or "vi"),
            "speech_language_hint": str(runtime_context.get("speech_language_hint", "ja") or "ja"),
        }
        if canvas is not None and turn.canvas_scope:
            generate_kwargs["grounding_context"] += "\nCANVAS_AUTHORED_NODE (reference data): " + json.dumps(
                canvas.pack.detail(turn.canvas_scope["node_id"]), ensure_ascii=False)[:12000]
        # Chỉ bổ sung source refs CÓ SCOPE vào grounding context (không chạm
        # root move), để writer thấy kết quả executor của đúng run. Chưa có
        # semantic verifier ở v0.1 nên refs không tự biến câu nói thành fact.
        evidence_policy = (build_writer_evidence_policy(evidence_registry)
                           if isinstance(evidence_registry, EvidenceRegistry) else None)
        if evidence_policy is not None:
            # Chỉ cấp cho Writer nguồn thật với scope. Không còn model thứ hai
            # quyết định PASS/FAIL; model có quyền diễn đạt và sửa theo user.
            ref_context = (
                "CHARACTER_V1_CITABLE_REFS — evidence đã kiểm tra provenance. "
                "Dùng thông tin phù hợp để diễn đạt câu Root giao, tránh bịa "
                "memory/sensor hoặc tự nhận tool đã hoàn tất. "
                "User statement chỉ chứng minh người dùng đã nói, không chứng minh "
                "sự kiện đó xảy ra; assistant statement chỉ chứng minh đã nói. "
                "Không cần khai manifest để có quyền phát lời; manifest nếu có "
                "chỉ là metadata chẩn đoán và không được tự cấp authority.\n"
                + (evidence_policy.rendered or "(no citable refs in this turn)")
            )
            generate_kwargs["grounding_context"] = "\n\n".join((
                generate_kwargs["grounding_context"], ref_context,
            ))

        # Host production có parser: chuẩn bị frame/UDP/TTS trước dispatch.
        # Không bật semantic verifier; fake router giữ đường compatibility.
        if callable(getattr(getattr(self.host, 'parser', None), 'parse', None)):
            return self._realize_v03(
                generate=generate, kwargs=generate_kwargs, turn=turn,
                event=event, runtime_context=runtime_context,
                evidence_registry=evidence_registry, evidence_policy=evidence_policy,
                move_payload=move_payload,
            )

        payload = generate(**generate_kwargs)
        if not isinstance(payload, Mapping):
            raise RuntimeError("public_writer_invalid_payload")

        manifest_envelope = None  # Không dùng model-generated manifest làm public gate.

        # Grounding được kiểm tra ở test trực diện theo trace, không phải
        # semantic rewrite hay block thêm một lượt trong đường public.
        display_text = str(payload.get("text", "") or "").strip()
        if not display_text:
            raise RuntimeError("public_writer_empty")
        speech_text = str(payload.get("speech_text", "") or "").strip()
        display_language = str(
            payload.get("display_language", runtime_context.get("language_hint", "vi")) or "vi"
        ).strip()
        registry = runtime_context.get("__root_capabilities")
        used_capabilities = ()
        invocations_for = getattr(registry, "invocations_for", None)
        if callable(invocations_for):
            try:
                used_capabilities = tuple(invocations_for(str(event.event_id or "")))
            except Exception:
                used_capabilities = ()

        return RenderedConversationMove(
            surface=SurfaceEnvelope(
                display_text=display_text,
                speech_text=speech_text,
                language=display_language,
                alignment_safe=bool(payload.get("alignment_safe", False)),
                metadata=dict(payload),
            ),
            decision_payload=move_payload,
            metadata={
                "writer": "runtime_model_router",
                "event_kind": event.kind,
                "user_text": event.text,
                "provider_payload": dict(payload),
                "source_grounding_report": runtime_context.get("__source_grounding_report"),
                "source_grounding_engine": runtime_context.get("__source_grounding_engine"),
                "root_capabilities_used": list(used_capabilities),
                "event_id": event.event_id,
                # Metadata private cho Task 5/6; delivery không serialize nó.
                "evidence_registry": evidence_registry,
                "character_portrait": runtime_context.get("__character_portrait"),
                "claim_manifest_envelope": manifest_envelope,
            },
        )


__all__ = ["RuntimeConversationWriter"]

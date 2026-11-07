# src/cortex/vision_semantic_compressor.py
"""
VisionSemanticCompressor cho ERIS.

Vai trò:
- Nhận screenshot/crop + OCR hint + ObserverFrame.
- Gọi vision API remote khi thật sự cần.
- Nén màn hình thành SemanticSceneNote nhỏ.
- Không trả lời user.
- Không commit memory.
- Không tự bật speech/action.

Nói ngắn: đây là mắt đọc sâu theo yêu cầu của BackgroundObserver,
không phải một cái miệng thứ hai. Nếu module vision bắt đầu nói chuyện,
thì repo đã mọc thêm một nhân vật phụ không ai mời.
"""

from __future__ import annotations
from core.di import resolve_dependency

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional
from runtime.no_fallback_policy import no_fallback_mode_enabled
from runtime.diagnostic_logging import safe_log

try:
    from cortex.observer_contracts import (
        ObserverFrame,
        SemanticSceneNote,
        SignalDiff,
        SignalSnapshot,
        clamp01,
        compact_text,
        coerce_activity_intent,
        normalize_ascii_fold,
        stable_hash,
    )
except Exception:  # pragma: no cover - fallback khi test rời file
    from .observer_contracts import (  # type: ignore
        ObserverFrame,
        SemanticSceneNote,
        SignalDiff,
        SignalSnapshot,
        clamp01,
        compact_text,
        coerce_activity_intent,
        normalize_ascii_fold,
        stable_hash,
    )

@dataclass(slots=True)
class ChatJsonResult:
    ok: bool
    parsed: dict[str, Any] | None = None
    content: str = ""
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class RemoteStructureClient:
    """Adapter: bọc một remote chat client (có ``create_chat_completion``) để lộ
    interface ``chat_json`` mà VisionSemanticCompressor cần cho TẦNG cấu trúc-hoá.

    Mục đích: đẩy việc ép JSON sang remote (vd open model qua Gemini API).

    Open model này KHÔNG nhận responseSchema nên ta vẫn gửi response_format=json_object và
    tự salvage JSON từ text trả về. Mọi lỗi ⇒ trả ChatJsonResult(ok=False) để compressor
    degrade về fallback (giữ caption), không bao giờ raise ra hot path.
    """

    def __init__(self, client: Any, *, max_tokens: int = 512, logger: Any | None = None) -> None:
        self._client = client
        self._max_tokens = max_tokens
        self._logger = logger

    @staticmethod
    def _extract_text(response: Any) -> str:
        try:
            from llm.dds import extract_text
            return str(extract_text(response) or "")
        except (ImportError, AttributeError, TypeError, ValueError):
            pass
        # Fallback tối thiểu: OpenAI-shape.
        if isinstance(response, dict):
            choices = response.get("choices")
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message") if isinstance(choices[0], dict) else None
                if isinstance(msg, dict):
                    return str(msg.get("content") or "")
        return str(response or "")

    @staticmethod
    def _parse_json_object(text: str) -> dict | None:
        import json as _json

        raw = str(text or "").strip()
        if not raw:
            return None
        try:
            obj = _json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except Exception:
            start, end = raw.find("{"), raw.rfind("}")
            if 0 <= start < end:
                try:
                    obj = _json.loads(raw[start : end + 1])
                    return obj if isinstance(obj, dict) else None
                except (json.JSONDecodeError, TypeError, ValueError):
                    return None
        return None

    def chat_json(
        self,
        model: str,  # bị bỏ qua: remote client dùng model_name của chính nó
        messages: list[dict],
        *,
        schema: dict | None = None,
        keep_alive: Any | None = None,  # không áp dụng cho remote
        temperature: float = 0.0,
        **_: Any,
    ) -> Any:
        response_format: dict[str, Any] = {"type": "json_object"}
        if isinstance(schema, dict) and schema:
            response_format["schema"] = schema
        try:
            from runtime.inference_broker import inference_context
            with inference_context(
                "vision_ambient",
                optional=True,
                timeout_seconds=18.0,
                cancel_scope="background:vision_compressor",
                coalesce_key="vision_compressor:json",
                metadata={"operation": "vision_structuring"},
            ):
                response = self._client.create_chat_completion(
                    messages=messages,
                    temperature=float(temperature),
                    frequency_penalty=0.0,
                    presence_penalty=0.0,
                    max_tokens=self._max_tokens,
                    response_format=response_format,
                )
        except Exception as exc:  # pragma: no cover - lỗi mạng/remote
            return ChatJsonResult(ok=False, error=f"{type(exc).__name__}:{exc}")

        content = self._extract_text(response)
        parsed = self._parse_json_object(content)
        if parsed is None:
            return ChatJsonResult(ok=False, content=content, error="json_parse_failed")
        return ChatJsonResult(ok=True, parsed=parsed, content=content)


class RemoteCaptionClient:
    """Adapter: bọc client OpenAI-compatible multimodal remote để
    lộ ``caption_image(image, ...)`` trả caption text.

    Gá»­i ảnh theo định dạng OpenAI multimodal (image_url data URI). Mọi lỗi ⇒ trả ""
    để compressor degrade (structuring vẫn chạy từ OCR), không raise ra hot path.
    """

    def __init__(self, client: Any, *, max_tokens: int = 320, logger: Any | None = None) -> None:
        self._client = client
        self._max_tokens = max_tokens
        self._logger = logger
        self.model_name = str(getattr(client, "model_name", "") or "vision_api")
        # Thu nhỏ ảnh trước khi gửi API: full-screen PNG base64 rất to → upload + suy
        # luận chậm. Hạ về JPEG ≤ max_width giúp cắt độ trễ. 0 = tắt downscale.
        try:
            self._max_width = int(os.getenv("ERIS_VISION_API_MAX_WIDTH", "1280") or 1280)
        except (TypeError, ValueError):
            self._max_width = 1280

    def _maybe_downscale(self, data_uri: str) -> str:
        if self._max_width <= 0 or not data_uri.startswith("data:"):
            return data_uri
        try:
            import base64
            import io

            from PIL import Image
        except Exception:
            return data_uri
        try:
            b64 = data_uri.split(",", 1)[1]
            raw = base64.b64decode(b64)
            im = Image.open(io.BytesIO(raw))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            w, h = im.size
            if w > self._max_width:
                im = im.resize((self._max_width, max(1, int(h * self._max_width / w))), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=75)
            return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")
        except Exception:
            return data_uri

    @staticmethod
    def _to_data_uri(image: str) -> str:
        s = str(image or "").strip()
        if not s:
            return ""
        if s.startswith("data:"):
            return s
        return f"data:image/png;base64,{s}"

    @staticmethod
    def _extract_text(response: Any) -> str:
        try:
            from llm.dds import extract_text
            return str(extract_text(response) or "")
        except (ImportError, AttributeError, TypeError, ValueError):
            pass
        if isinstance(response, dict):
            choices = response.get("choices")
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message") if isinstance(choices[0], dict) else None
                if isinstance(msg, dict):
                    return str(msg.get("content") or "")
        return str(response or "")

    def caption_image(
        self,
        image: str,
        *,
        prompt: str = "Describe what is visible on this screen in concrete detail.",
        temperature: float = 0.2,
        max_tokens: int | None = None,
        keep_alive: Any | None = None,  # không áp dụng cho remote
    ) -> str:
        data_uri = self._maybe_downscale(self._to_data_uri(image))
        if not data_uri:
            return ""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ]

        def _do_call() -> tuple[str, bool]:
            """Gọi caption một lần; bool cho biết lỗi có đáng retry ngay hay không."""
            call_kwargs: dict[str, Any] = {
                "messages": messages,
                "temperature": float(temperature),
                "frequency_penalty": 0.0,
                "presence_penalty": 0.0,
                "max_tokens": int(max_tokens or self._max_tokens),
            }
            try:
                from runtime.inference_broker import InferenceRejected, inference_context
                with inference_context(
                    "vision_ambient",
                    optional=True,
                    timeout_seconds=18.0,
                    cancel_scope="background:vision_caption",
                    coalesce_key="vision_caption:latest",
                    metadata={"operation": "vision_caption"},
                ):
                    response = self._client.create_chat_completion(**call_kwargs)
            except InferenceRejected as exc:
                if self._logger is not None:
                    safe_log(
                        self._logger,
                        "info",
                        "[vision caption remote] broker skipped optional caption reason=%s",
                        getattr(exc, "reason", str(exc)),
                    )
                return "", False
            except Exception as exc:  # pragma: no cover - lỗi mạng/remote
                if self._logger is not None and callable(getattr(self._logger, "warning", None)):
                    safe_log(self._logger, "warning", "[vision caption remote] lỗi: %s", exc)
                return "", True
            return self._extract_text(response).strip(), True

        result, retryable = _do_call()
        if result:
            return result
        if not retryable:
            return ""

        # Lần đầu trả rỗng (Groq/Qwen empty output bug) → thử lại một lần.
        if self._logger is not None:
            safe_log(
                self._logger,
                "warning",
                "[vision caption remote] empty output, retrying once (model=%s)",
                self.model_name,
            )
        result, retryable_after_retry = _do_call()
        if not result and self._logger is not None and retryable_after_retry:
            safe_log(
                self._logger,
                "warning",
                "[vision caption remote] retry cũng trả rỗng, bỏ qua caption turn này (model=%s)",
                self.model_name,
            )
        return result


_ERROR_MARKERS = (
    "traceback",
    "exception",
    "error",
    "failed",
    "failure",
    "assertionerror",
    "assertion failed",
    "compile failed",
    "build failed",
    "panic",
    "fatal",
    "lỗi",
    "loi",
    "không chạy",
    "khong chay",
    "test failed",
    "pytest failed",
)

_CODE_MARKERS = (
    "vscode",
    "visual studio code",
    "pycharm",
    "rider",
    "godot",
    "terminal",
    "powershell",
    "cmd.exe",
    "pytest",
    "python",
    ".py",
    ".cs",
    ".gd",
    ".ts",
    ".js",
)

_DOC_MARKERS = (
    "docs",
    "documentation",
    "readme",
    "stackoverflow",
    "stack overflow",
    "developer.mozilla",
    "learn.microsoft",
    "python.org",
    "github",
)

_COMM_MARKERS = (
    "discord",
    "messenger",
    "telegram",
    "gmail",
    "mail.google",
    "outlook",
    "zalo",
    "slack",
    "teams",
    "zoom",
)


@dataclass(slots=True)
class VisionCompressorConfig:
    """Cấu hình cho VisionSemanticCompressor.

    Mặc định tắt bằng env để bạn kiểm observer trước rồi mới bật vision.
    Có thể bật bằng:
        ERIS_VISION_COMPRESSOR=1
    """

    enabled: bool = field(
        default_factory=lambda: os.getenv("ERIS_VISION_COMPRESSOR", "0").strip().lower()
        not in {"0", "false", "no", "off", ""}
    )
    model: str = "remote_vision"
    structure_model: str = field(
        default_factory=lambda: os.getenv("ERIS_VISION_STRUCTURE_MODEL", "").strip()
    )
    keep_alive: str | int | None = field(
        default_factory=lambda: _parse_keep_alive(os.getenv("ERIS_VISION_KEEP_ALIVE", "0"))
    )
    temperature: float = field(
        default_factory=lambda: _safe_float(os.getenv("ERIS_VISION_TEMPERATURE", "0"), 0.0)
    )

    min_salience: float = field(
        default_factory=lambda: clamp01(os.getenv("ERIS_VISION_MIN_SALIENCE", "0.70"), 0.70)
    )
    min_diff: float = field(
        default_factory=lambda: clamp01(os.getenv("ERIS_VISION_MIN_DIFF", "0.20"), 0.20)
    )
    max_ocr_chars: int = field(
        default_factory=lambda: max(240, int(_safe_float(os.getenv("ERIS_VISION_MAX_OCR_CHARS", "1600"), 1600)))
    )
    max_context_chars: int = 2400
    max_important_text: int = 8

    allow_interrupt: bool = False
    force_should_interrupt_false: bool = True
    unload_after_call: bool = field(
        default_factory=lambda: os.getenv("ERIS_VISION_UNLOAD_AFTER_CALL", "0").strip().lower()
        in {"1", "true", "yes", "on"}
    )


class VisionSemanticCompressor:
    """Nén ảnh màn hình thành SemanticSceneNote.

    Public API chính:
        should_compress(observer_frame, signal_diff)
        compress(image_data_uri=..., ocr_text=..., observer_frame=...)

    File này không tự capture screenshot. Runtime/telemetry làm chuyện đó.
    Cắt trách nhiệm ra như vậy để khỏi có thêm một con daemon tự tiện nhìn màn hình.
    """

    module_name = "vision_semantic_compressor"

    def __init__(
        self,
        config: VisionCompressorConfig | None = None,
        local_client: Any | None = None,
        logger: Any | None = None,
        structure_client: Any | None = None,
        caption_client: Any | None = None,
    ) -> None:
        self.config = resolve_dependency(config, lambda: VisionCompressorConfig())
        self.client = local_client
        # Client riêng cho TẦNG cấu trúc-hoá (caption → JSON).
        # None ⇒ dùng luôn self.client nếu caller inject client tự do.
        self.structure_client = structure_client
        # Client riêng cho TẦNG caption (ảnh → mô tả). Khi set (endpoint multimodal riêng
        # grok-3-image), caption chạy REMOTE. None ⇒ self.client.
        self.caption_client = caption_client
        self.logger = logger
        # Scene continuity: nhớ scene trước để delta/relation có chỗ bám. "Thay đổi"
        # chỉ phản ứng được nếu có cái trước để so.
        self._last_scene: dict[str, str] = {}

    def should_compress(
        self,
        *,
        observer_frame: ObserverFrame | None = None,
        signal_diff: SignalDiff | None = None,
    ) -> bool:
        """Quyết định có nên gọi vision model không.

        Đây chỉ là policy cục bộ. Coordinator vẫn nên có cooldown riêng.
        """

        if not self.config.enabled:
            return False
        if observer_frame is None:
            return False

        salience = clamp01(getattr(observer_frame, "salience", 0.0))
        wants_vision = bool(getattr(observer_frame, "should_call_vision", False)) or str(
            getattr(observer_frame, "recommended_action", "") or ""
        ) == "call_vision_compressor"
        if not wants_vision and salience < self.config.min_salience:
            return False

        if signal_diff is None:
            return salience >= self.config.min_salience

        diff_score = clamp01(getattr(signal_diff, "diff_score", 0.0))
        if diff_score < self.config.min_diff and not bool(getattr(signal_diff, "ocr_changed", False)):
            return False
        return True

    def compress(
        self,
        *,
        image_data_uri: str = "",
        image_base64: str = "",
        image_path: str = "",
        ocr_text: str = "",
        observer_frame: ObserverFrame | None = None,
        signal_snapshot: SignalSnapshot | None = None,
        extra_context: str = "",
    ) -> SemanticSceneNote:
        """Gọi vision model và trả SemanticSceneNote.

        Nếu thiếu ảnh/model/client, trả fallback OCR-only thay vì làm chết loop.
        """

        if not self.config.enabled:
            return self._empty_scene_note(
                reason="vision_compressor_disabled",
                observer_frame=observer_frame,
                signal_snapshot=signal_snapshot,
            )
        image = self._resolve_image_base64(
            image_data_uri=image_data_uri,
            image_base64=image_base64,
            image_path=image_path,
        )
        if not self.config.model:
            if no_fallback_mode_enabled():
                raise RuntimeError("no_fallback_vision:missing_vision_model")
            return self._fallback_ocr_note(
                ocr_text=ocr_text,
                observer_frame=observer_frame,
                signal_snapshot=signal_snapshot,
                reason="missing_vision_model",
            )
        if not image:
            if no_fallback_mode_enabled():
                raise RuntimeError("no_fallback_vision:missing_image")
            return self._fallback_ocr_note(
                ocr_text=ocr_text,
                observer_frame=observer_frame,
                signal_snapshot=signal_snapshot,
                reason="missing_image",
            )
        has_caption_client = self.caption_client is not None and callable(getattr(self.caption_client, "caption_image", None))
        has_generic_client = self.client is not None and callable(getattr(self.client, "chat_json", None))
        if not has_caption_client and not has_generic_client:
            # Không có VLM không đồng nghĩa không có sensor. Nếu OCR đã đọc được
            # text hiện tại thì giữ đúng phần bằng chứng đó ngay cả ở no-fallback
            # mode; no-fallback chỉ cấm bịa/model dự phòng, không cấm dùng sensor
            # local đã có dữ liệu thật.
            if str(ocr_text or "").strip() or str(getattr(signal_snapshot, "ocr_text", "") if signal_snapshot else "").strip():
                return self._fallback_ocr_note(
                    ocr_text=ocr_text,
                    observer_frame=observer_frame,
                    signal_snapshot=signal_snapshot,
                    reason="missing_vision_client_ocr_salvage",
                )
            if no_fallback_mode_enabled():
                raise RuntimeError("no_fallback_vision:missing_vision_client")
            return self._fallback_ocr_note(
                ocr_text=ocr_text,
                observer_frame=observer_frame,
                signal_snapshot=signal_snapshot,
                reason="missing_vision_client",
            )

        # Hai tầng tách vai: caption_client (vd grok-3-image) chỉ TẢ bằng prompt đơn
        # giản (nó làm tốt), structure_client (vd glm-4.5-flash) lo NÉN caption→JSON.
        # KHÔNG single-shot: nhồi schema vào VLM vision khiến nó vẹt lại input.
        caption_client = self.caption_client or self.client
        caption_backend = "remote" if self.caption_client is not None else "local"
        if not callable(getattr(caption_client, "caption_image", None)):
            return self._compress_single_shot(
                image=image,
                ocr_text=ocr_text,
                observer_frame=observer_frame,
                signal_snapshot=signal_snapshot,
                extra_context=extra_context,
            )

        # ── Tầng 1: caption TỰ DO bằng vision model ──
        caption = ""
        caption_error = ""
        try:
            caption = caption_client.caption_image(
                image,
                prompt=self._caption_prompt(),
                temperature=max(self.config.temperature, 0.1),
                keep_alive=self.config.keep_alive if self.config.unload_after_call else None,
            )
        except Exception as exc:  # caption lỗi → vẫn cấu trúc hoá từ OCR
            caption_error = f"{type(exc).__name__}:{exc}"
            self._log_warning("Vision caption stage failed: %s", caption_error)

        # ── Tầng 2: text model cấu trúc caption + OCR thành SemanticSceneNote ──
        # Có structure_client (remote, vd open model) → cấu trúc-hoá chạy remote, máy
        # local KHỎI nạp/swap text model. None ⇒ dùng client local như cũ.
        structure_client = self.structure_client or self.client
        structure_backend = "remote" if self.structure_client is not None else "local"
        messages = [
            {"role": "system", "content": self._structure_system_prompt()},
            {"role": "user", "content": self._structure_user_prompt(
                caption=caption,
                ocr_text=ocr_text,
                observer_frame=observer_frame,
                signal_snapshot=signal_snapshot,
                extra_context=extra_context,
            )},
        ]
        result = structure_client.chat_json(
            self._structure_model(),
            messages,
            schema=self._json_schema(),
            keep_alive=self.config.keep_alive,
            temperature=self.config.temperature,
        )

        if not getattr(result, "ok", False):
            err = getattr(result, "error", "unknown")
            self._log_warning("Vision structuring failed: %s", err)
            if no_fallback_mode_enabled():
                raise RuntimeError(f"no_fallback_vision:structure_failed:{err}")
            # Tầng cấu trúc lỗi nhưng caption thật vẫn còn giá trị tri giác.
            return self._fallback_ocr_note(
                ocr_text=ocr_text,
                observer_frame=observer_frame,
                signal_snapshot=signal_snapshot,
                reason=f"structure_failed:{err}",
                caption=caption,
            )

        parsed = getattr(result, "parsed", {}) or {}
        note = self._note_from_mapping(
            parsed,
            observer_frame=observer_frame,
            signal_snapshot=signal_snapshot,
            caption=caption,
            raw_debug={
                "model": self._source_label(),
                "structure_backend": structure_backend,
                "caption_backend": caption_backend,
                "vision_caption": compact_text(caption, 400),
                "caption_error": caption_error,
                "structure_preview": compact_text(str(getattr(result, "content", "") or ""), 400),
            },
            source_override=(
                str(getattr(self.caption_client, "model_name", "") or "")
                if self.caption_client is not None
                else ""
            ),
        )
        return note

    def _compress_single_shot(
        self,
        *,
        image: str,
        ocr_text: str,
        observer_frame: ObserverFrame | None,
        signal_snapshot: SignalSnapshot | None,
        extra_context: str,
    ) -> SemanticSceneNote:
        """Đường cÅ©: gọi vision model một phát với schema (cho client không có
        caption_image)."""
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": self._user_prompt(
                ocr_text=ocr_text,
                observer_frame=observer_frame,
                signal_snapshot=signal_snapshot,
                extra_context=extra_context,
            )},
        ]
        result = self.client.chat_json(
            self.config.model,
            messages,
            images=[image],
            schema=self._json_schema(),
            keep_alive=self.config.keep_alive,
            temperature=self.config.temperature,
        )

        if not getattr(result, "ok", False):
            self._log_warning("Vision compressor client failed: %s", getattr(result, "error", "unknown"))
            if no_fallback_mode_enabled():
                raise RuntimeError(f"no_fallback_vision:client_failed:{getattr(result, 'error', 'unknown')}")
            return self._fallback_ocr_note(
                ocr_text=ocr_text,
                observer_frame=observer_frame,
                signal_snapshot=signal_snapshot,
                reason=f"client_failed:{getattr(result, 'error', 'unknown')}",
            )

        parsed = getattr(result, "parsed", {}) or {}
        note = self._note_from_mapping(
            parsed,
            observer_frame=observer_frame,
            signal_snapshot=signal_snapshot,
            raw_debug={
                "model": self.config.model,
                "client_content_preview": compact_text(str(getattr(result, "content", "") or ""), 500),
            },
        )

        if self.config.unload_after_call and callable(getattr(self.client, "unload", None)):
            try:
                self.client.unload(self.config.model)
            except Exception as exc:  # pragma: no cover - unload lỗi không nên phá runtime
                self._log_warning("Vision compressor unload failed: %s", exc)

        return note

    def compress_from_ocr_only(
        self,
        *,
        ocr_text: str = "",
        observer_frame: ObserverFrame | None = None,
        signal_snapshot: SignalSnapshot | None = None,
    ) -> SemanticSceneNote:
        """Public fallback khi runtime chưa có ảnh nhưng có OCR."""

        return self._fallback_ocr_note(
            ocr_text=ocr_text,
            observer_frame=observer_frame,
            signal_snapshot=signal_snapshot,
            reason="explicit_ocr_only",
        )

    # ------------------------------------------------------------------
    # Prompt / schema
    # ------------------------------------------------------------------

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are ERIS VisionSemanticCompressor.\n"
            "Your job is to read a screenshot/crop and compress it into a small runtime note.\n"
            "Do not answer the user.\n"
            "Describe the world content on screen and its affect honestly (a sad video is sad, "
            "a failing test is an error) — that is a fact about the screen.\n"
            "Do not infer the USER's hidden emotions, identity, private intent, or personality.\n"
            "Do not produce prose outside JSON.\n"
            "Prefer visible, grounded facts.\n"
            "If uncertain, lower confidence.\n"
            "Keep should_interrupt_user false unless the screenshot shows a clear urgent runtime hazard."
        )

    def _structure_model(self) -> str:
        """Model/source label for the text-structuring stage."""
        return self.config.structure_model or "remote_structure"

    @staticmethod
    def _caption_prompt() -> str:
        return (
            "Describe this screen concisely but concretely. "
            "Say what application or window is shown and what is being looked at. "
            "Copy any clearly readable title, label, or error message. "
            "If a video/image/game/media is shown, describe its content and emotional tone "
            "(for example: a melancholic video about an old cat). "
            "Write 1-3 plain sentences. Do not guess anything you cannot actually see."
        )

    @staticmethod
    def _structure_system_prompt() -> str:
        return (
            "You are ERIS VisionSemanticCompressor (structuring stage).\n"
            "You are given a vision caption and OCR text of the user's screen, plus observer signals "
            "and possibly the PREVIOUS scene.\n"
            "Compress them into ONE small json runtime note that matches the schema.\n"
            "Describe the world content and its affect honestly: a sad video is sad, a test failure "
            "is an error, a heated chat is tense. That is a fact about the screen, not about the user.\n"
            "Do NOT infer the user's hidden emotions, identity, private intent, or personality.\n"
            "Fields you must fill:\n"
            "- visible_context: what is actually on screen (do not echo these instructions).\n"
            "- content_summary: what the content IS or MEANS (e.g. 'a melancholic video about an old cat', "
            "'a failing pytest run on the vision loader').\n"
            "- content_affect: one short word for the content's emotional tone "
            "(sad|tense|joyful|calm|frustrating|neutral|...).\n"
            "- relation_to_user: how this scene relates to what the user was doing "
            "(e.g. 'switched away from code to watch something sad'). Empty if unclear.\n"
            "- delta: what CHANGED versus the previous scene given below. Empty if there is no previous "
            "scene or nothing meaningful changed.\n"
            "- semantic_family: short snake_case label for WHAT this scene is "
            "(e.g. test_failure_loop, sad_media, code_editing, docs_reading).\n"
            "Output json only. If evidence is thin, lower confidence.\n"
            "Keep should_interrupt_user false unless there is a clear urgent runtime hazard."
        )

    def _structure_user_prompt(
        self,
        *,
        caption: str,
        ocr_text: str,
        observer_frame: ObserverFrame | None,
        signal_snapshot: SignalSnapshot | None,
        extra_context: str,
    ) -> str:
        payload = {
            "vision_caption": compact_text(caption, 600),
            "ocr_text": compact_text(ocr_text, self.config.max_ocr_chars),
            "observer": self._observer_payload(observer_frame),
            "signal": self._signal_payload(signal_snapshot),
            "previous_scene": self._previous_scene_payload(),
            "extra_context": compact_text(extra_context, 500),
        }
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return compact_text(text, self.config.max_context_chars)

    def _previous_scene_payload(self) -> dict[str, str]:
        if not self._last_scene:
            return {}
        return {
            "content_summary": self._last_scene.get("content_summary", ""),
            "semantic_family": self._last_scene.get("semantic_family", ""),
            "content_affect": self._last_scene.get("content_affect", ""),
        }

    def _user_prompt(
        self,
        *,
        ocr_text: str,
        observer_frame: ObserverFrame | None,
        signal_snapshot: SignalSnapshot | None,
        extra_context: str,
    ) -> str:
        payload = {
            "observer": self._observer_payload(observer_frame),
            "signal": self._signal_payload(signal_snapshot),
            "ocr_hint": compact_text(ocr_text, self.config.max_ocr_chars),
            "extra_context": compact_text(extra_context, 500),
            "instructions": {
                "visible_context": "short description of what is visibly on screen",
                "important_text": "up to 8 short strings copied/paraphrased from visible text",
                "activity_intent": "debugging|designing|reading|searching|communicating|resting|fragmented|idle|unknown",
                "semantic_family": "short snake_case label, e.g. test_failure_loop, code_editing, docs_reading",
                "novelty": "0..1, how new this scene seems relative to observer diff",
                "salience": "0..1, how much runtime should care",
                "possible_error": "0..1, visible technical/runtime error likelihood",
                "should_interrupt_user": "normally false",
                "confidence": "0..1",
            },
        }
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return compact_text(text, self.config.max_context_chars)

    @staticmethod
    def _json_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "visible_context": {"type": "string"},
                "important_text": {"type": "array", "items": {"type": "string"}},
                "activity_intent": {
                    "type": "string",
                    "enum": [
                        "debugging",
                        "designing",
                        "reading",
                        "searching",
                        "communicating",
                        "resting",
                        "fragmented",
                        "idle",
                        "unknown",
                    ],
                },
                "semantic_family": {"type": "string"},
                "content_summary": {"type": "string"},
                "content_affect": {"type": "string"},
                "relation_to_user": {"type": "string"},
                "delta": {"type": "string"},
                "novelty": {"type": "number", "minimum": 0, "maximum": 1},
                "salience": {"type": "number", "minimum": 0, "maximum": 1},
                "possible_error": {"type": "number", "minimum": 0, "maximum": 1},
                "should_interrupt_user": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [
                "visible_context",
                "important_text",
                "activity_intent",
                "semantic_family",
                "novelty",
                "salience",
                "possible_error",
                "should_interrupt_user",
                "confidence",
            ],
        }

    # ------------------------------------------------------------------
    # Mapping / fallback
    # ------------------------------------------------------------------

    def _note_from_mapping(
        self,
        data: Mapping[str, Any],
        *,
        observer_frame: ObserverFrame | None,
        signal_snapshot: SignalSnapshot | None,
        caption: str = "",
        raw_debug: Mapping[str, Any] | None = None,
        source_override: str = "",
    ) -> SemanticSceneNote:
        observer_error = clamp01(getattr(observer_frame, "possible_error", 0.0) if observer_frame else 0.0)
        observer_salience = clamp01(getattr(observer_frame, "salience", 0.0) if observer_frame else 0.0)
        important = self._coerce_text_list(data.get("important_text", []), limit=self.config.max_important_text)
        possible_error = max(clamp01(data.get("possible_error", 0.0)), observer_error * 0.75)
        salience = max(clamp01(data.get("salience", 0.0)), observer_salience * 0.72, possible_error * 0.78)
        novelty = max(clamp01(data.get("novelty", 0.0)), clamp01(getattr(observer_frame, "novelty", 0.0) if observer_frame else 0.0) * 0.70)
        confidence = clamp01(data.get("confidence", 0.0))
        if not important and signal_snapshot is not None:
            important = self._extract_important_lines(str(getattr(signal_snapshot, "ocr_text", "") or ""))

        should_interrupt = bool(data.get("should_interrupt_user", False))
        if self.config.force_should_interrupt_false:
            should_interrupt = False
        elif not self.config.allow_interrupt:
            should_interrupt = False

        model_label = source_override or self.config.model
        evidence = [f"vision_model:{model_label}"]
        if observer_frame is not None:
            evidence.extend(str(item) for item in getattr(observer_frame, "evidence", [])[:4])
        if signal_snapshot is not None and getattr(signal_snapshot, "screen_hash", ""):
            evidence.append(f"screen_hash:{getattr(signal_snapshot, 'screen_hash')}")

        # visible_context phải mang nội dung thật. Nếu model trả rỗng hoặc nhả lại
        # mô tả field (parrot), rơi về caption tự do của vision model.
        visible = compact_text(str(data.get("visible_context", "") or ""), 260)
        if self._looks_like_echo(visible) or not visible:
            visible = compact_text(caption, 260) or visible or "visible screen context"
        if caption:
            evidence.append(f"vision_caption:{compact_text(caption, 90)}")

        # TẦNG-1: nghĩa + thay đổi. Lọc echo cho các trường text.
        content_summary = self._clean_meaning(data.get("content_summary", ""), 260)
        if not content_summary:
            content_summary = visible
        content_affect = self._clean_meaning(data.get("content_affect", ""), 80)
        relation_to_user = self._clean_meaning(data.get("relation_to_user", ""), 200)
        delta = self._clean_meaning(data.get("delta", ""), 200)
        family = self._clean_family(data.get("semantic_family", "unknown"))

        note = SemanticSceneNote(
            visible_context=visible,
            important_text=important,
            activity_intent=coerce_activity_intent(data.get("activity_intent", "unknown")),
            semantic_family=family,
            novelty=novelty,
            salience=salience,
            possible_error=possible_error,
            should_interrupt_user=should_interrupt,
            confidence=confidence,
            content_summary=content_summary,
            content_affect=content_affect,
            relation_to_user=relation_to_user,
            delta=delta,
            source=source_override or self._source_label(),
            evidence=evidence[:10],
            debug={"raw": dict(raw_debug or {}), "observer_used": observer_frame is not None},
        )
        self._assign_scene_continuity(note)
        return note

    def _empty_scene_note(
        self,
        *,
        reason: str,
        observer_frame: ObserverFrame | None,
        signal_snapshot: SignalSnapshot | None,
    ) -> SemanticSceneNote:
        evidence: list[str] = [reason]
        if observer_frame is not None:
            evidence.extend(str(x) for x in getattr(observer_frame, "evidence", [])[:4])
        if signal_snapshot is not None and getattr(signal_snapshot, "screen_hash", ""):
            evidence.append(f"screen_hash:{getattr(signal_snapshot, 'screen_hash')}")
        return SemanticSceneNote(
            source="unknown",
            evidence=evidence,
            debug={"disabled_reason": reason, "observer_used": observer_frame is not None},
        )

    def _fallback_ocr_note(
        self,
        *,
        ocr_text: str,
        observer_frame: ObserverFrame | None,
        signal_snapshot: SignalSnapshot | None,
        reason: str,
        caption: str = "",
    ) -> SemanticSceneNote:
        source_text = ocr_text or str(getattr(signal_snapshot, "ocr_text", "") if signal_snapshot else "")
        folded = normalize_ascii_fold(f"{source_text}\n{caption}")
        observer_focus = str(getattr(observer_frame, "focus_kind", "unknown") if observer_frame else "unknown")
        observer_intent = str(getattr(observer_frame, "activity_intent", "unknown") if observer_frame else "unknown")
        observer_salience = clamp01(getattr(observer_frame, "salience", 0.0) if observer_frame else 0.0)
        observer_novelty = clamp01(getattr(observer_frame, "novelty", 0.0) if observer_frame else 0.0)
        observer_error = clamp01(getattr(observer_frame, "possible_error", 0.0) if observer_frame else 0.0)

        text_error = 1.0 if any(marker in folded for marker in _ERROR_MARKERS) else 0.0
        possible_error = max(observer_error, 0.78 if text_error else 0.0)
        intent = observer_intent if observer_intent != "unknown" else self._infer_intent_from_text(f"{source_text} {caption}", observer_focus)
        family = self._infer_family_from_text(f"{source_text} {caption}", possible_error=possible_error, focus_kind=observer_focus)
        # Có caption thật từ vision model → giữ làm visible_context, đừng vứt.
        clean_caption = compact_text(caption, 260)
        if clean_caption and not self._looks_like_echo(clean_caption):
            visible = clean_caption
            source = "vision_caption_only"
            confidence = 0.50
            salience_floor = 0.28
            novelty_floor = 0.22
        else:
            visible = self._fallback_visible_context(signal_snapshot, observer_focus, intent)
            source = "ocr_only"
            confidence = 0.42 if source_text else 0.25
            salience_floor = 0.20 if source_text else 0.0
            novelty_floor = 0.18 if source_text else 0.0
        important = self._extract_important_lines(source_text)
        evidence = [reason]
        # PATCH: OCR fallback phải mang provenance trực tiếp, nếu không
        # VisionGroundingPolicy sẽ đánh missing_direct_sensor_evidence và claims=0.
        if source_text:
            evidence.append(f"ocr_text:{compact_text(source_text, 90)}")
        if signal_snapshot is not None and getattr(signal_snapshot, "screen_hash", ""):
            evidence.append(f"screen_hash:{getattr(signal_snapshot, 'screen_hash')}")
        if clean_caption:
            evidence.append(f"vision_caption:{compact_text(clean_caption, 90)}")
        if observer_frame is not None:
            evidence.extend(str(x) for x in getattr(observer_frame, "evidence", [])[:4])

        note = SemanticSceneNote(
            visible_context=visible,
            important_text=important,
            activity_intent=coerce_activity_intent(intent),
            semantic_family=family,
            novelty=max(observer_novelty * 0.72, novelty_floor),
            salience=max(observer_salience * 0.65, possible_error * 0.72, salience_floor),
            possible_error=possible_error,
            should_interrupt_user=False,
            confidence=confidence,
            content_summary=visible if visible else "",
            source=source,
            evidence=evidence,
            debug={"fallback_reason": reason, "text_error_marker": bool(text_error), "has_caption": bool(clean_caption)},
        )
        self._assign_scene_continuity(note)
        return note

    def _clean_meaning(self, value: Any, limit: int) -> str:
        """Compact + loại echo cho các trường nghĩa (content/affect/relation/delta)."""
        text = compact_text(str(value or ""), limit)
        if not text or self._looks_like_echo(text):
            return ""
        return text

    def _assign_scene_continuity(self, note: SemanticSceneNote) -> None:
        """Gán scene_id + prev_scene_id để 'thay đổi' có chỗ bám, cập nhật scene cuối."""
        key = note.content_summary or note.visible_context
        scene_id = stable_hash(f"{note.semantic_family}|{key}")
        note.prev_scene_id = self._last_scene.get("scene_id", "")
        note.scene_id = scene_id
        # Model không cho delta nhưng scene đổi rõ → ghi delta tối thiểu để runtime
        # vẫn thấy có chuyển cảnh.
        if not note.delta and note.prev_scene_id and note.prev_scene_id != scene_id:
            prev_summary = self._last_scene.get("content_summary", "")
            if prev_summary and prev_summary != key:
                note.delta = compact_text(f"scene shifted from: {prev_summary}", 200)
        self._last_scene = {
            "scene_id": scene_id,
            "content_summary": note.content_summary,
            "semantic_family": note.semantic_family,
            "content_affect": note.content_affect,
        }

    @staticmethod
    def _looks_like_echo(text: str) -> bool:
        """Bắt trường hợp model nhỏ nhả lại mô tả field/instruction thay vì nội dung."""
        if not text:
            return False
        folded = normalize_ascii_fold(text)
        echo_markers = (
            "short description of what",
            "up to 8 short strings",
            "short snake_case label",
            "how new this scene",
            "how much runtime should care",
            "copied/paraphrased from visible text",
            "visible technical/runtime error likelihood",
        )
        return any(marker in folded for marker in echo_markers)

    def _extract_important_lines(self, text: str) -> list[str]:
        if not text:
            return []
        lines = [compact_text(line, 180) for line in re.split(r"[\r\n]+", text) if compact_text(line, 180)]
        if not lines:
            return []
        folded_lines = [(line, normalize_ascii_fold(line)) for line in lines]
        priority: list[str] = []
        for line, folded in folded_lines:
            if any(marker in folded for marker in _ERROR_MARKERS):
                priority.append(line)
        for line, folded in folded_lines:
            if any(marker in folded for marker in ("pytest", "assert", "traceback", "exception", "failed")) and line not in priority:
                priority.append(line)
        if not priority:
            priority = lines[: self.config.max_important_text]
        return list(dict.fromkeys(priority))[: self.config.max_important_text]

    def _infer_intent_from_text(self, text: str, focus_kind: str) -> str:
        folded = normalize_ascii_fold(text)
        if any(marker in folded for marker in _ERROR_MARKERS):
            return "debugging"
        if focus_kind == "code" or any(marker in folded for marker in _CODE_MARKERS):
            return "designing"
        if focus_kind == "docs" or any(marker in folded for marker in _DOC_MARKERS):
            return "reading"
        if focus_kind == "chat" or any(marker in folded for marker in _COMM_MARKERS):
            return "communicating"
        if focus_kind in {"media", "game"}:
            return "resting"
        return "unknown"

    def _infer_family_from_text(self, text: str, *, possible_error: float, focus_kind: str) -> str:
        folded = normalize_ascii_fold(text)
        if possible_error >= 0.55:
            if "pytest" in folded or "test" in folded:
                return "test_failure_loop"
            return "visible_error_signal"
        if focus_kind == "code" or any(marker in folded for marker in _CODE_MARKERS):
            return "code_workspace"
        if focus_kind == "docs" or any(marker in folded for marker in _DOC_MARKERS):
            return "docs_reference"
        if focus_kind == "chat" or any(marker in folded for marker in _COMM_MARKERS):
            return "communication_context"
        if focus_kind in {"media", "game"}:
            return "ambient_media"
        return "screen_context"

    @staticmethod
    def _fallback_visible_context(
        signal_snapshot: SignalSnapshot | None,
        focus_kind: str,
        intent: str,
    ) -> str:
        # No signal and no app/window means there is no real screen content to
        # describe. Returning a placeholder like "screen context inferred from
        # OCR; focus=unknown; intent=unknown" only manufactures a content-free
        # "fact" that later leaks as screen evidence. Prefer an empty context so
        # downstream fact extraction simply skips it.
        if signal_snapshot is None:
            return ""
        app = compact_text(str(getattr(signal_snapshot, "active_app", "") or ""), 80)
        win = compact_text(str(getattr(signal_snapshot, "active_window_title", "") or ""), 120)
        if app or win:
            return compact_text(f"{app or 'active app'} | {win or 'active window'} | focus={focus_kind}; intent={intent}", 260)
        return ""

    # ------------------------------------------------------------------
    # Payload helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _observer_payload(observer_frame: ObserverFrame | None) -> dict[str, Any]:
        if observer_frame is None:
            return {}
        return {
            "focus_kind": getattr(observer_frame, "focus_kind", "unknown"),
            "activity_intent": getattr(observer_frame, "activity_intent", "unknown"),
            "salience": round(clamp01(getattr(observer_frame, "salience", 0.0)), 3),
            "novelty": round(clamp01(getattr(observer_frame, "novelty", 0.0)), 3),
            "repetition": round(clamp01(getattr(observer_frame, "repetition", 0.0)), 3),
            "possible_error": round(clamp01(getattr(observer_frame, "possible_error", 0.0)), 3),
            "recommended_action": getattr(observer_frame, "recommended_action", "observe_only"),
            "confidence": round(clamp01(getattr(observer_frame, "confidence", 0.0)), 3),
        }

    @staticmethod
    def _signal_payload(signal_snapshot: SignalSnapshot | None) -> dict[str, Any]:
        if signal_snapshot is None:
            return {}
        return {
            "active_app": compact_text(str(getattr(signal_snapshot, "active_app", "") or ""), 90),
            "active_window": compact_text(str(getattr(signal_snapshot, "active_window_title", "") or ""), 140),
            "focus_hint": getattr(signal_snapshot, "focus_kind_hint", "unknown"),
            "idle_seconds": round(float(getattr(signal_snapshot, "idle_seconds", 0.0) or 0.0), 1),
            "recent_events": [compact_text(str(x), 90) for x in list(getattr(signal_snapshot, "recent_events", []) or [])[-6:]],
            "screen_hash": getattr(signal_snapshot, "screen_hash", ""),
            "ocr_hash": getattr(signal_snapshot, "ocr_hash", ""),
        }

    def _resolve_image_base64(
        self,
        *,
        image_data_uri: str,
        image_base64: str,
        image_path: str,
    ) -> str:
        if image_base64:
            return self._strip_data_uri(image_base64)
        if image_data_uri:
            return self._strip_data_uri(image_data_uri)
        if image_path:
            path = Path(image_path)
            if path.exists() and path.is_file():
                if self.client is not None and callable(getattr(self.client, "encode_image_file", None)):
                    return self.client.encode_image_file(str(path))
                import base64

                return base64.b64encode(path.read_bytes()).decode("utf-8")
        return ""

    @staticmethod
    def _strip_data_uri(value: str) -> str:
        text = str(value or "").strip()
        if text.startswith("data:") and "," in text:
            return text.split(",", 1)[1].strip()
        return text

    @staticmethod
    def _coerce_text_list(value: Any, *, limit: int) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = [value]
        elif isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = [value]
        out: list[str] = []
        for item in raw_items:
            text = compact_text(str(item or ""), 180)
            if text and text not in out:
                out.append(text)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _clean_family(value: Any) -> str:
        text = str(value or "unknown").strip().lower()
        text = re.sub(r"[^a-z0-9_\-]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        return compact_text(text or "unknown", 80)

    def _source_label(self) -> str:
        """Trả source label từ model name, dùng cho SemanticSceneNote.source."""
        model = str(self.config.model or "").strip().lower().split(":")[0]
        return model or "vision_model"

    def _log_warning(self, msg: str, *args: Any) -> None:
        if self.logger is not None and callable(getattr(self.logger, "warning", None)):
            safe_log(self.logger, "warning", msg, *args)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _parse_keep_alive(value: str) -> str | int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.lower() in {"none", "null"}:
        return None
    if raw == "0":
        return 0
    return raw

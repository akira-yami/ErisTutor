"""DDS — Decompose / Distill / Synthesize (Phase 2 + 3).

Ý tưởng: thay vì nhồi raw history + OCR + cognition vào MỘT prompt khổng lồ (gây
413), băm ngữ cảnh nặng thành nhiều mẩu nhỏ, cho worker (local/open-model) rút gọn
song song thành vài gạch đầu dòng, rồi mới đưa phần distilled (nhỏ) vào synthesis.

Module này là tầng hạ tầng, KHÔNG phụ thuộc service: nhận sẵn một "client" (bất
kỳ object có ``create_chat_completion(...)``) và làm việc text thuần. Mọi worker
nuốt lỗi: fail/timeout → trả về None hoặc text gốc (degrade), không bao giờ raise
ra ngoài để khỏi làm hỏng hot path.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

from llm.key_pool import concurrency_slot
from llm.token_budget import estimate_tokens

_LOGGER = logging.getLogger("LLM")


def extract_text(response: Any) -> str:
    """Lấy text từ response chat-completion (OpenAI-shape hoặc Gemini-shape)."""

    if not isinstance(response, dict):
        return str(response or "")
    # OpenAI-compatible
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
    # Gemini
    candidates = response.get("candidates")
    if isinstance(candidates, list) and candidates:
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        if isinstance(content, dict):
            parts = content.get("parts")
            if isinstance(parts, list):
                texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
                return "\n".join(t for t in texts if t).strip()
    return ""


@dataclass(slots=True)
class WorkerResult:
    name: str
    ok: bool
    text: str = ""
    error: str = ""


@dataclass(slots=True)
class WorkerSpec:
    """Một worker DDS: tên + hàm chạy (trả text hoặc None)."""

    name: str
    fn: Callable[[], str | None]


def run_workers(
    specs: list[WorkerSpec],
    *,
    provider: str = "gemini",
    max_workers: int | None = None,
    per_worker_timeout: float | None = None,
) -> dict[str, WorkerResult]:
    """Chạy các worker SONG SONG (có trần đồng thời per-provider).

    Worker nào lỗi/timeout → ``WorkerResult(ok=False)`` và bị loại khỏi gói
    distilled bởi caller. KHÔNG raise.
    """

    results: dict[str, WorkerResult] = {}
    if not specs:
        return results

    workers = max(1, min(len(specs), max_workers or len(specs)))

    def _guarded(spec: WorkerSpec) -> WorkerResult:
        try:
            with concurrency_slot(provider, timeout=per_worker_timeout):
                out = spec.fn()
            text = str(out or "").strip()
            if not text:
                return WorkerResult(spec.name, ok=False, error="empty")
            return WorkerResult(spec.name, ok=True, text=text)
        except Exception as exc:  # noqa: BLE001 — worker không được làm hỏng lượt
            return WorkerResult(spec.name, ok=False, error=f"{type(exc).__name__}:{exc}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_guarded, spec): spec for spec in specs}
        for future in as_completed(futures):
            spec = futures[future]
            try:
                results[spec.name] = future.result()
            except Exception as exc:  # noqa: BLE001
                results[spec.name] = WorkerResult(spec.name, ok=False, error=str(exc))
    return results


_DISTILL_INSTRUCTION = (
    "Bạn là bộ rút gọn ngữ cảnh. Tóm tắt đoạn dưới đây thành tối đa {bullets} gạch "
    "đầu dòng RẤT NGẮN (mỗi dòng ≤ 16 từ), chỉ giữ sự thật/ý cốt lõi cần cho việc "
    "trả lời. Bỏ dài dòng, bỏ lặp, không thêm bình luận. CHỈ trả về các gạch đầu dòng."
)


def distill_block(
    client: Any,
    *,
    label: str,
    text: str,
    max_bullets: int = 3,
    max_tokens: int = 160,
    timeout: float | None = 90.0,
    min_chars_to_distill: int = 400,
) -> str:
    """Rút gọn MỘT khối ngữ cảnh thành vài gạch đầu dòng (text thuần).

    - Khối ngắn hơn ``min_chars_to_distill`` → trả nguyên (không tốn call).
    - Lỗi/timeout/empty → trả nguyên text gốc (degrade, không mất thông tin).
    """

    source = str(text or "").strip()
    if not source or len(source) < min_chars_to_distill or client is None:
        return source

    instruction = _DISTILL_INSTRUCTION.format(bullets=max_bullets)
    messages = [{"role": "user", "content": f"{instruction}\n\n[{label}]\n{source}"}]
    try:
        response = client.create_chat_completion(
            messages=messages,
            temperature=0.2,
            frequency_penalty=0.0,
            presence_penalty=0.0,
            max_tokens=max_tokens,
            response_format=None,
            grammar=None,
            timeout=timeout,
        )
        distilled = extract_text(response)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("[DDS] distill_block(%s) failed: %s", label, exc)
        return source

    distilled = distilled.strip()
    if not distilled:
        return source
    # Chỉ nhận nếu thực sự nhỏ hơn (tránh worker "nở" ngữ cảnh).
    if estimate_tokens([{"role": "user", "content": distilled}]) >= estimate_tokens(
        [{"role": "user", "content": source}]
    ):
        return source
    return distilled


def distill_blocks_parallel(
    client: Any,
    blocks: dict[str, str],
    *,
    provider: str = "gemini",
    max_bullets: int = 3,
    max_tokens: int = 160,
    timeout: float | None = 90.0,
    min_chars_to_distill: int = 400,
    max_workers: int | None = None,
) -> dict[str, str]:
    """Rút gọn nhiều khối SONG SONG. Trả dict cùng key; khối lỗi giữ nguyên gốc."""

    specs: list[WorkerSpec] = []
    big_keys: list[str] = []
    for key, value in blocks.items():
        text = str(value or "")
        if len(text.strip()) >= min_chars_to_distill:
            big_keys.append(key)
            specs.append(
                WorkerSpec(
                    name=key,
                    fn=lambda k=key, t=text: distill_block(
                        client,
                        label=k,
                        text=t,
                        max_bullets=max_bullets,
                        max_tokens=max_tokens,
                        timeout=timeout,
                        min_chars_to_distill=min_chars_to_distill,
                    ),
                )
            )

    out = dict(blocks)
    if not specs:
        return out

    results = run_workers(specs, provider=provider, max_workers=max_workers, per_worker_timeout=timeout)
    for key in big_keys:
        res = results.get(key)
        if res is not None and res.ok and res.text:
            out[key] = res.text
    return out

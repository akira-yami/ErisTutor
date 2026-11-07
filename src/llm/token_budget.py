"""Token accounting + prompt hard-cap (Phase 0).

Mục tiêu: chặn 413 (Payload Too Large) ngay tại cửa gửi request, và soi được
token thật trước mỗi call remote.

Cung cấp:
- ``estimate_tokens(messages)``     — ước lượng token theo heuristic chars/4.
- ``detect_provider(base_url, model)`` — suy ra nhà cung cấp từ base_url/model.
- ``budget_for_provider(provider)`` — trần TPM cứng theo provider.
- ``hard_cap_for_provider(provider)`` — trần token cho 1 prompt (cắt history).
- ``enforce_prompt_cap(messages, cap)`` — cắt history giữa nếu vượt cap.
- ``preflight(messages, base_url, model)`` — log ``[LLM PREFLIGHT]`` + áp hard-cap.

Toàn bộ là pure-Python, không gọi mạng, nuốt mọi lỗi nội bộ để KHÔNG bao giờ
làm hỏng hot path (preflight chỉ là hàng rào, không được tự gây exception).
"""

from __future__ import annotations

from llm.model_catalog import LEGACY_OPEN_MODEL_PREFIX

import logging
import os
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger("LLM")

# Trần TPM (tokens-per-minute) free-tier theo provider. Groq free qwen3-32b = 6000.
PROVIDER_TPM_BUDGET: dict[str, int] = {
    "groq": 6000,
    "gemini": 1_000_000,
    "local": 1_000_000,
    "openai": 120_000,
    "deepseek": 200_000,
    "default": 30_000,
}

# Mỗi message có overhead nhỏ (role + delimiters) ~ 4 token.
_PER_MESSAGE_OVERHEAD = 4
# Ảnh inline rất nặng; quy đổi xấp xỉ để ước lượng không quá lạc quan.
_IMAGE_TOKEN_COST = 768
# Tỉ lệ chừa headroom dưới trần TPM khi suy ra hard-cap mặc định.
_DEFAULT_HEADROOM = 0.85
_NO_HARD_CAP_TOKENS = 1_000_000_000


def _safe_int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def detect_provider(base_url: str | None, model: str | None) -> str:
    """Suy ra provider từ base_url + model. Không bao giờ raise."""

    url = (base_url or "").strip().lower()
    name = (model or "").strip().lower()

    if any(token in url for token in ("127.0.0.1", "localhost", "0.0.0.0")):
        return "local"
    if "groq" in url:
        return "groq"
    if "deepseek" in url or name.startswith("deepseek-"):
        return "deepseek"
    if "generativelanguage" in url or name.startswith((LEGACY_OPEN_MODEL_PREFIX, "gemini")):
        return "gemini"
    if "openai" in url:
        return "openai"
    return "default"


def _truthy_env(name: str) -> bool | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    text = str(raw or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def unlimited_prompt_for(base_url: str | None, model: str | None) -> bool:
    """Return True for the K12/gpt-5.5 lane where prompt trimming is explicitly off."""

    override = _truthy_env("ERIS_K12_UNLIMITED_PROMPT")
    if override is False:
        return False
    url = (base_url or "").strip().lower()
    name = (model or "").strip().lower()
    is_k12_model = name == "gpt-5.5" or "gpt-5.5" in name
    is_k12_endpoint = any(token in url for token in ("localhost", "127.0.0.1", "k12"))
    return is_k12_model and is_k12_endpoint and (override is True or override is None)


def budget_for_provider(provider: str) -> int:
    key = (provider or "default").lower()
    # Broker may suffix a role-specific credential pool, e.g.
    # ``deepseek__curator``. Quota state stays isolated by the full key, while
    # static provider capacity should still inherit DeepSeek's budget rather
    # than falling back to the generic 30k bucket.
    base_key = key.split("__", 1)[0]
    return PROVIDER_TPM_BUDGET.get(base_key, PROVIDER_TPM_BUDGET["default"])


def hard_cap_for_provider(provider: str) -> int:
    """Trần token cho MỘT prompt. Thứ tự ưu tiên:

    1. Override RIÊNG provider: ``ERIS_LLM_PROMPT_HARD_CAP_TOKENS_<PROVIDER>``
       (vd ``..._GROQ=5200``). Chỉ siết đúng provider hẹp, provider khác giữ
       headroom lớn → cho phép route prompt quá khổ sang provider budget to.
    2. Override CHUNG: ``ERIS_LLM_PROMPT_HARD_CAP_TOKENS`` (áp mọi provider).
    3. Mặc định = 85% TPM của provider.

    Lưu ý: ưu tiên (1) > (2). Nếu chỉ muốn siết Groq mà vẫn để DeepSeek/Gemini
    rộng, đặt ``ERIS_LLM_PROMPT_HARD_CAP_TOKENS_GROQ=5200`` và BỎ biến chung.
    Đặt ``ERIS_LLM_PROMPT_HARD_CAP_TOKENS=0`` để tắt hard-cap prompt.
    """

    # PDF annotation, pages 4 and 15-16: distinguish an explicit zero from an
    # absent setting. The old control flow returned "unlimited" for the normal
    # default path, making the documented 85% provider headroom unreachable.
    prov = (provider or "default").strip().lower()
    if _truthy_env("ERIS_DISABLE_PROMPT_HARD_CAP") is True:
        return _NO_HARD_CAP_TOKENS

    per_name = f"ERIS_LLM_PROMPT_HARD_CAP_TOKENS_{prov.upper()}"
    if os.environ.get(per_name) is not None:
        per_provider = _safe_int_env(per_name, 0)
        return per_provider if per_provider > 0 else _NO_HARD_CAP_TOKENS

    if os.environ.get("ERIS_LLM_PROMPT_HARD_CAP_TOKENS") is not None:
        override = _safe_int_env("ERIS_LLM_PROMPT_HARD_CAP_TOKENS", 0)
        return override if override > 0 else _NO_HARD_CAP_TOKENS

    runtime_cap = None
    try:
        from runtime.runtime_settings_store import runtime_int

        raw = runtime_int("prompt", "hard_cap_tokens", default=None)
        runtime_cap = None if raw is None else int(raw)
    except Exception:
        runtime_cap = None

    if runtime_cap is not None:
        return runtime_cap if runtime_cap > 0 else _NO_HARD_CAP_TOKENS

    # Nothing explicitly configured: use a conservative provider-aware cap.
    return max(512, int(budget_for_provider(prov) * _DEFAULT_HEADROOM))


def _content_chars(content: Any) -> tuple[int, int]:
    """Trả (chars, image_count) của một content (str | list | dict)."""

    if content is None:
        return 0, 0
    if isinstance(content, str):
        return len(content), 0
    if isinstance(content, list):
        chars = 0
        images = 0
        for item in content:
            if isinstance(item, str):
                chars += len(item)
            elif isinstance(item, dict):
                if item.get("type") == "image_url" or "image_url" in item or "inlineData" in item or "inline_data" in item:
                    images += 1
                else:
                    chars += len(str(item.get("text", "") or ""))
            else:
                chars += len(str(item))
        return chars, images
    if isinstance(content, dict):
        if "text" in content:
            return len(str(content.get("text", "") or "")), 0
        return len(str(content)), 0
    return len(str(content)), 0


def estimate_tokens(messages: Any) -> int:
    """Ước lượng token cho danh sách messages kiểu OpenAI. Heuristic chars/4."""

    if not messages:
        return 0
    total_chars = 0
    total_images = 0
    count = 0
    try:
        for message in messages:
            count += 1
            content = message.get("content") if isinstance(message, dict) else message
            chars, images = _content_chars(content)
            total_chars += chars
            total_images += images
    except Exception:
        return 0
    return (total_chars // 3) + (count * _PER_MESSAGE_OVERHEAD) + (total_images * _IMAGE_TOKEN_COST)


def _largest_text_index(messages: list[dict]) -> tuple[int | None, int]:
    best_idx: int | None = None
    best_len = 0
    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and len(content) > best_len:
            best_idx = i
            best_len = len(content)
    return best_idx, best_len


def _system_block_priority(block: str) -> int:
    """Điểm càng cao càng phải sống sót khi prompt chịu token pressure."""

    text = str(block or "").lower()
    hard_markers = (
        "turnframe", "turn frame", "responsecontract", "response contract",
        "response_obligation", "forbidden_draft", "repair_target",
        "[dynamic_policy]", "[grounding]", "latest visible",
    )
    medium_markers = (
        "[state_style]", "capability", "semantic authority", "voice policy",
        "conversation_context",
    )
    optional_markers = (
        "private mind", "thought_stream", "nhật ký nội tâm", "inner diary",
        "recently_resolved", "content seed", "ambient",
    )
    if any(marker in text for marker in hard_markers):
        return 100
    if any(marker in text for marker in medium_markers):
        return 60
    if any(marker in text for marker in optional_markers):
        return 10
    return 35


def _shrink_system_by_blocks(content: str, target_tokens: int) -> str:
    """Giữ nguyên khối contract/repair, bỏ khối tùy chọn trước khi cắt chữ."""

    source = str(content or "")
    if _text_token_estimate(source) <= target_tokens:
        return source
    blocks = [block.strip() for block in source.split("\n\n") if block.strip()]
    if not blocks:
        return truncate_text_to_budget(source, target_tokens, preserve_tail=True)

    kept = list(blocks)
    # Bỏ khối optional từ lớn tới nhỏ. Không xóa hard contract chỉ vì một khối
    # diary dài quá mức, một bi kịch nhỏ nhưng rất hay xảy ra với prompt legacy.
    removable = sorted(
        range(len(kept)),
        key=lambda idx: (_system_block_priority(kept[idx]), -len(kept[idx])),
    )
    removed: set[int] = set()
    for idx in removable:
        if _text_token_estimate("\n\n".join(block for i, block in enumerate(kept) if i not in removed)) <= target_tokens:
            break
        if _system_block_priority(kept[idx]) >= 100:
            continue
        removed.add(idx)

    remaining = [block for i, block in enumerate(kept) if i not in removed]
    joined = "\n\n".join(remaining)
    if _text_token_estimate(joined) <= target_tokens:
        return joined

    # Chia quota theo trọng số. Hard blocks nhận phần lớn quota và không bị mất
    # đầu/đuôi, thay vì cưa ngang chuỗi hợp nhất rồi làm contract thành xác chữ.
    weights = [3.0 if _system_block_priority(b) >= 100 else 1.5 if _system_block_priority(b) >= 60 else 1.0 for b in remaining]
    total_weight = max(1.0, sum(weights))
    compacted: list[str] = []
    for block, weight in zip(remaining, weights):
        quota = max(48, int(target_tokens * weight / total_weight))
        compacted.append(
            truncate_text_to_budget(
                block, quota, marker="…[block rút gọn]…", preserve_tail=True
            )
        )
    return "\n\n".join(part for part in compacted if part)


def _truncate_to_cap(messages: list[dict], cap: int) -> list[dict]:
    """Lưới an toàn block-preserving thay cho việc cưa message dài nhất."""

    msgs = [dict(m) if isinstance(m, dict) else m for m in messages]
    # Cắt history cũ trước, giữ user gần nhất và system contract.
    for idx in range(max(0, len(msgs) - 2)):
        if estimate_tokens(msgs) <= cap:
            return msgs
        msg = msgs[idx]
        if not isinstance(msg, dict) or str(msg.get("role") or "") == "system":
            continue
        content = msg.get("content")
        if isinstance(content, str) and len(content) > 120:
            msg["content"] = truncate_text_to_budget(
                content, max(40, _text_token_estimate(content) // 3),
                marker="…[history rút gọn]…", preserve_tail=True,
            )

    if estimate_tokens(msgs) <= cap:
        return msgs

    # System được xử theo block, không theo character-cut.
    non_system_tokens = estimate_tokens([m for m in msgs if isinstance(m, dict) and str(m.get("role") or "") != "system"])
    system_budget = max(160, cap - non_system_tokens)
    system_indices = [i for i, m in enumerate(msgs) if isinstance(m, dict) and str(m.get("role") or "") == "system"]
    per_system = max(80, system_budget // max(1, len(system_indices)))
    for idx in system_indices:
        content = msgs[idx].get("content")
        if isinstance(content, str):
            msgs[idx]["content"] = _shrink_system_by_blocks(content, per_system)

    if estimate_tokens(msgs) <= cap:
        return msgs

    # Trường hợp cực đoan: rút từng message nhưng vẫn giữ cả đầu và đuôi.
    for _ in range(80):
        if estimate_tokens(msgs) <= cap:
            break
        idx, size = _largest_text_index(msgs)
        if idx is None or size <= 100:
            break
        content = str(msgs[idx].get("content") or "")
        target = max(40, int(_text_token_estimate(content) * 0.78))
        msgs[idx]["content"] = truncate_text_to_budget(
            content, target, marker="…[hard-cap cuối]…", preserve_tail=True
        )
    return msgs


def compress_system_message(content: str) -> str:
    """Nén hoặc lược bỏ bớt các khối cồng kềnh trong system prompt (diary, thought_stream, persona)
    để nhường chỗ cho memory và các lượt chat gần nhất.
    """
    if not content:
        return content
    # Chia nhỏ bằng \n\n để duyệt qua các block cấu trúc
    blocks = content.split("\n\n")
    new_blocks = []
    for block in blocks:
        # 1. Loại bỏ block chứa PrivateMind speech bias
        if "PrivateMind speech bias" in block:
            continue
        # 2. Loại bỏ block chứa Nhật ký nội tâm hoặc recently_resolved hoặc Đã nguôi gần đây
        if "Nhật ký nội tâm hiện tại" in block or "recently_resolved" in block or "Đã nguôi gần đây" in block:
            continue
        # 3. Nén Persona (Bạn là ERIS...) thành phiên bản rút gọn, cực kỳ súc tích
        if "Bạn là ERIS" in block or "Bạn là Eris" in block:
            if len(block) > 250:
                compressed_persona = (
                    "ERIS là một hiện diện hội thoại desktop có trí nhớ và nhịp riêng. "
                    "Nói như một nhân vật đang ở trong cuộc trò chuyện: bình tĩnh, sắc, hơi trêu, "
                    "trả lời đúng nước đi xã hội hiện tại. Không tự giới thiệu, không quảng cáo hỗ trợ, "
                    "không reset thành trợ lý generic trừ khi bạn hỏi thẳng."
                )
                new_blocks.append(compressed_persona)
                continue
        new_blocks.append(block)
    return "\n\n".join(new_blocks)


def enforce_prompt_cap(messages: Any, cap: int) -> tuple[list[dict], bool]:
    """Nếu prompt vượt ``cap`` token: bỏ bớt lượt history cũ (giữ system + 5 lượt
    gần nhất), rồi mới nén system prompt, rồi mới cắt nội dung nếu vẫn vượt.
    Trả ``(messages_mới, đã_cắt)``."""

    msgs = list(messages or [])
    if not msgs:
        return msgs, False
    if estimate_tokens(msgs) <= cap:
        return msgs, False

    n = len(msgs)
    protected: set[int] = set()
    # 1. Bảo vệ các lượt system message
    for i, message in enumerate(msgs):
        if isinstance(message, dict) and str(message.get("role")) == "system":
            protected.add(i)
    # 2. Bảo vệ 5 lượt gần nhất
    for idx in range(max(0, n - 5), n):
        protected.add(idx)

    # 3. Bỏ bớt history cũ nằm ngoài vùng bảo vệ
    keep = [True] * n
    for i in range(n):
        if i in protected:
            continue
        current = [msgs[j] for j in range(n) if keep[j]]
        if estimate_tokens(current) <= cap:
            break
        keep[i] = False

    trimmed = [msgs[j] for j in range(n) if keep[j]]

    # 4. Nếu vẫn vượt cap, thực hiện nén system prompt
    if estimate_tokens(trimmed) > cap:
        for idx, msg in enumerate(trimmed):
            if isinstance(msg, dict) and str(msg.get("role")) == "system":
                original_content = msg.get("content", "")
                if isinstance(original_content, str):
                    compressed = compress_system_message(original_content)
                    if compressed != original_content:
                        new_msg = dict(msg)
                        new_msg["content"] = compressed
                        trimmed[idx] = new_msg

    # 5. Nếu vẫn vượt sau khi nén system prompt, ta cắt nội dung text dài nhất (raw)
    if estimate_tokens(trimmed) > cap:
        trimmed = _truncate_to_cap(trimmed, cap)

    return trimmed, True


def _retained_prompt_blocks(messages: Any) -> tuple[str, ...]:
    found: list[str] = []
    markers = {
        "turn_frame": ("turnframe", "turn frame", "repair_target"),
        "response_contract": ("responsecontract", "response contract", "response_obligation"),
        "latest_visible_history": ("latest visible", "previous_assistant", "previous_user"),
        "capability_grounding": ("capability", "available capabilities"),
    }
    for message in list(messages or []):
        if not isinstance(message, dict):
            continue
        content = str(message.get("content") or "").lower()
        for name, needles in markers.items():
            if name not in found and any(needle in content for needle in needles):
                found.append(name)
    return tuple(found)



def root_session_prompt_cap(*, base_url: str | None, model: str | None, fallback: int) -> int:
    """Nới cap CHỈ cho Root persistent qua DeepSeek Web/Whale2API.

    Giới hạn token mặc định 5.100 của runtime không đủ để gửi nguyên Self.
    Đây là giới hạn *context*, không phải hóa đơn tính tiền. Giữ cap riêng,
    không vô tình nới cho writer, observer hay các API remote khác.
    """
    uri = str(base_url or "").lower()
    name = str(model or "").lower()
    if ("deepseek" not in name or "api.deepseek.com" in uri or
            not any(x in uri for x in ("localhost", "127.0.0.1"))):
        return fallback
    if os.environ.get("ERIS_ROOT_SESSION_BOOTSTRAP", "1").strip().lower() in {"0", "false", "no", "off"}:
        return fallback
    if os.environ.get("ERIS_WEB_SESSION_MODE", "isolated").strip().lower() in {"0", "off", "disabled", "none", "legacy"}:
        return fallback
    try:
        from runtime.inference_broker import current_inference_context
        info = current_inference_context()
        lane = str((getattr(info, "metadata", {}) or {}).get("cognitive_lane", "") or "")
        if lane != "root_conversation":
            return fallback
    except Exception:
        return fallback
    try:
        cap = int(os.environ.get("ERIS_ROOT_SESSION_PROMPT_CAP_TOKENS", "90000"))
        return max(fallback, cap) if cap > 0 else fallback
    except (TypeError, ValueError):
        return fallback

def preflight(messages: Any, *, base_url: str | None = None, model: str | None = None) -> list[dict]:
    """Log token ước lượng và áp hard-cap trước khi gửi. Trả messages (đã cắt
    nếu cần). Không bao giờ raise — lỗi nội bộ → trả nguyên messages gốc."""

    try:
        if unlimited_prompt_for(base_url, model):
            return list(messages or [])
        provider = detect_provider(base_url, model)
        budget = budget_for_provider(provider)
        cap = root_session_prompt_cap(base_url=base_url, model=model,
                                      fallback=hard_cap_for_provider(provider))
        est = estimate_tokens(messages)
        _LOGGER.debug(
            "[LLM PREFLIGHT] provider=%s model=%s est_tokens=%d budget=%d cap=%d",
            provider,
            (model or "").strip() or "?",
            est,
            budget,
            cap,
        )
        if est <= cap:
            output = list(messages or [])
            try:
                from runtime.observability import GLOBAL_OBSERVABILITY
                GLOBAL_OBSERVABILITY.record_prompt_budget(
                    before_tokens=est, after_tokens=est, cap=cap,
                    retained_blocks=_retained_prompt_blocks(output), trimmed=False,
                )
            except Exception:
                pass
            return output
        trimmed, did_trim = enforce_prompt_cap(messages, cap)
        if did_trim:
            _LOGGER.warning(
                "[LLM HARD-CAP] provider=%s est_tokens=%d > cap=%d → trimmed to ~%d tokens",
                provider,
                est,
                cap,
                estimate_tokens(trimmed),
            )
        try:
            from runtime.observability import GLOBAL_OBSERVABILITY
            GLOBAL_OBSERVABILITY.record_prompt_budget(
                before_tokens=est, after_tokens=estimate_tokens(trimmed), cap=cap,
                retained_blocks=_retained_prompt_blocks(trimmed), trimmed=did_trim,
            )
        except Exception:
            pass
        return trimmed
    except Exception:  # noqa: BLE001 — preflight không được làm hỏng hot path
        return list(messages or [])

# ── Chat prompt block budget ─────────────────────────────────────────────────
# Budget được áp TRƯỚC khi ghép prompt. Đây là lớp chính; preflight phía client
# chỉ còn là lưới an toàn cuối, không phải máy chém xử lý một prompt đã phình.


@dataclass(frozen=True, slots=True)
class ChatPromptBudget:
    """Quota chat V2: prefix tĩnh nhỏ, policy động nhỏ, phần còn lại có headroom.

    ``system_rules`` được giữ tên để tương thích code cũ nhưng nay chỉ dành cho
    prefix cacheable. Mọi rule thay đổi theo lượt phải đi vào ``dynamic_policy``.
    """

    total_tokens: int = 4800
    output_reserve: int = 512
    system_rules: int = 560
    dynamic_policy: int = 350
    user_history: int = 500
    grounding: int = 260
    memory: int = 240
    state_style: int = 220

    @property
    def input_limit(self) -> int:
        return max(512, self.total_tokens - self.output_reserve)

    @property
    def allocated_input(self) -> int:
        return (
            self.system_rules
            + self.dynamic_policy
            + self.user_history
            + self.grounding
            + self.memory
            + self.state_style
        )

    @property
    def spare_tokens(self) -> int:
        return max(0, self.input_limit - self.allocated_input)


def _text_token_estimate(text: str, *, role: str = "system") -> int:
    return estimate_tokens([{"role": role, "content": str(text or "")}])


def truncate_text_to_budget(
    text: str,
    max_tokens: int,
    *,
    marker: str = "…[rút gọn theo block budget]…",
    preserve_tail: bool = True,
) -> str:
    """Rút một block riêng xuống quota, giữ cả đầu và đuôi khi có thể."""

    source = str(text or "").strip()
    max_tokens = max(0, int(max_tokens))
    if not source or max_tokens <= 0:
        return ""
    if _text_token_estimate(source) <= max_tokens:
        return source

    # Binary search theo số ký tự vì estimate_tokens cũng là heuristic ký tự.
    lo, hi = 0, len(source)
    best = ""
    while lo <= hi:
        keep = (lo + hi) // 2
        if preserve_tail and keep > len(marker) + 40:
            head = max(1, int(keep * 0.68))
            tail = max(1, keep - head)
            candidate = f"{source[:head].rstrip()}\n{marker}\n{source[-tail:].lstrip()}"
        else:
            candidate = source[:keep].rstrip() + (f"\n{marker}" if keep < len(source) else "")
        if _text_token_estimate(candidate) <= max_tokens:
            best = candidate
            lo = keep + 1
        else:
            hi = keep - 1
    return best or source[: max(24, max_tokens * 2)].rstrip()


def _fit_user_history_bundle(
    *,
    chat_history: list[dict],
    user_message: str,
    conversation_context: str,
    max_tokens: int,
) -> tuple[str, list[dict], str]:
    """Giữ user hiện tại trước, rồi context và các turn mới nhất trong cùng quota."""

    budget = max(64, int(max_tokens))
    user = str(user_message or "")
    # User hiện tại có quyền ưu tiên tuyệt đối trong block này.
    user = truncate_text_to_budget(user, budget, marker="…[user message vượt chat budget]…")
    base_messages = [{"role": "user", "content": user}]
    used = estimate_tokens(base_messages)
    if used >= budget:
        return "", [], user

    remaining = budget - used
    conversation = truncate_text_to_budget(
        conversation_context,
        min(120, remaining),
        marker="…[conversation context rút gọn]…",
    )
    if conversation:
        remaining = max(
            0,
            budget
            - estimate_tokens(
                [
                    {"role": "system", "content": conversation},
                    {"role": "user", "content": user},
                ]
            ),
        )

    kept_reversed: list[dict] = []
    for raw in reversed(list(chat_history or [])):
        if remaining <= 8 or not isinstance(raw, dict):
            break
        role = str(raw.get("role") or "user")
        if role not in {"user", "assistant"}:
            continue
        content = raw.get("content", "")
        if not isinstance(content, str):
            content = str(content or "")
        candidate = {"role": role, "content": content}
        candidate_cost = estimate_tokens([candidate])
        if candidate_cost > remaining:
            compact = truncate_text_to_budget(
                content,
                remaining,
                marker="…[history rút gọn]…",
                preserve_tail=True,
            )
            if compact:
                kept_reversed.append({"role": role, "content": compact})
            break
        kept_reversed.append(candidate)
        remaining -= candidate_cost

    kept_reversed.reverse()
    return conversation, kept_reversed, user


def assemble_budgeted_chat_messages(
    *,
    system_rules_text: str,
    dynamic_policy_text: str = "",
    state_style_text: str,
    grounding_text: str,
    memory_text: str,
    conversation_context: str,
    chat_history: list[dict],
    user_message: str,
    budget: ChatPromptBudget | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Áp quota từng block rồi mới ghép message cho chat synthesis."""

    cfg = budget or ChatPromptBudget()
    if cfg.allocated_input > cfg.input_limit:
        raise ValueError("ChatPromptBudget phân bổ vượt input_limit")

    system_rules = truncate_text_to_budget(
        system_rules_text,
        cfg.system_rules,
        marker="…[static prefix rút gọn theo quota]…",
        preserve_tail=False,
    )
    dynamic_policy = truncate_text_to_budget(
        dynamic_policy_text,
        cfg.dynamic_policy,
        marker="…[dynamic policy rút gọn theo quota]…",
    )
    state_style = truncate_text_to_budget(
        state_style_text,
        cfg.state_style,
        marker="…[state/style rút gọn theo quota]…",
    )
    grounding = truncate_text_to_budget(
        grounding_text,
        cfg.grounding,
        marker="…[grounding rút gọn theo quota]…",
    )
    memory = truncate_text_to_budget(
        memory_text,
        cfg.memory,
        marker="…[memory rút gọn theo quota]…",
    )
    conversation, history, user = _fit_user_history_bundle(
        chat_history=chat_history,
        user_message=user_message,
        conversation_context=conversation_context,
        max_tokens=cfg.user_history,
    )

    sections: list[str] = []
    # Prefix tĩnh phải đứng ĐẦU để exact-prefix caching có cơ hội hit. Các khối
    # thay đổi theo lượt nằm sau; đảo thứ tự sẽ làm cache vô dụng rất sáng tạo.
    if system_rules:
        sections.append(f"[CACHEABLE_STATIC_PREFIX]\n{system_rules}")
    if dynamic_policy:
        sections.append(f"[DYNAMIC_POLICY]\n{dynamic_policy}")
    if state_style:
        sections.append(f"[STATE_STYLE]\n{state_style}")
    if grounding:
        sections.append(f"[GROUNDING]\n{grounding}")
    if memory:
        sections.append(f"[MEMORY]\n{memory}")
    if conversation:
        sections.append(f"[CONVERSATION_CONTEXT]\n{conversation}")

    messages: list[dict] = [
        {"role": "system", "content": "\n\n".join(sections)},
        *history,
        {"role": "user", "content": user},
    ]

    # Lưới an toàn cho nhãn/role overhead. Bình thường spare 788 token hấp thụ đủ.
    if estimate_tokens(messages) > cfg.input_limit:
        messages, _ = enforce_prompt_cap(messages, cfg.input_limit)

    report = {
        "total": cfg.total_tokens,
        "output_reserve": cfg.output_reserve,
        "input_limit": cfg.input_limit,
        "system_rules": _text_token_estimate(system_rules),
        "dynamic_policy": _text_token_estimate(dynamic_policy),
        "user_history": estimate_tokens([
            *([{"role": "system", "content": conversation}] if conversation else []),
            *history,
            {"role": "user", "content": user},
        ]),
        "grounding": _text_token_estimate(grounding),
        "memory": _text_token_estimate(memory),
        "state_style": _text_token_estimate(state_style),
        "spare": cfg.spare_tokens,
        "assembled_input": estimate_tokens(messages),
    }
    return messages, report

# ── Generic block-first prompt budget ───────────────────────────────────────

@dataclass(frozen=True, slots=True)
class PromptBlockBudget:
    """Quota prompt dùng cho các channel ngoài chat.

    Tên block giữ thống nhất với chat để telemetry dễ so sánh. Caller có thể
    đổi quota theo channel, nhưng mọi block luôn bị cắt trước khi ghép.
    """

    total_tokens: int = 4800
    output_reserve: int = 512
    system_rules: int = 1800
    user_history: int = 500
    grounding: int = 500
    memory: int = 400
    state_style: int = 300

    @property
    def input_limit(self) -> int:
        return max(256, self.total_tokens - self.output_reserve)

    @property
    def allocated_input(self) -> int:
        return self.system_rules + self.user_history + self.grounding + self.memory + self.state_style

    @property
    def spare_tokens(self) -> int:
        return max(0, self.input_limit - self.allocated_input)


def assemble_budgeted_prompt_blocks(
    *,
    system_rules_text: str = "",
    user_text: str = "",
    grounding_text: str = "",
    memory_text: str = "",
    state_style_text: str = "",
    budget: PromptBlockBudget | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Cắt từng block theo quota rồi mới ghép thành system/user messages."""

    cfg = budget or PromptBlockBudget()
    if cfg.allocated_input > cfg.input_limit:
        raise ValueError("PromptBlockBudget phân bổ vượt input_limit")

    system_rules = truncate_text_to_budget(
        system_rules_text, cfg.system_rules, marker="…[system rules rút gọn theo quota]…"
    )
    user = truncate_text_to_budget(
        user_text, cfg.user_history, marker="…[user/instruction rút gọn theo quota]…"
    )
    grounding = truncate_text_to_budget(
        grounding_text, cfg.grounding, marker="…[grounding rút gọn theo quota]…"
    )
    memory = truncate_text_to_budget(
        memory_text, cfg.memory, marker="…[memory rút gọn theo quota]…"
    )
    state_style = truncate_text_to_budget(
        state_style_text, cfg.state_style, marker="…[state/style rút gọn theo quota]…"
    )

    sections: list[str] = []
    if state_style:
        sections.append(f"[STATE_STYLE]\n{state_style}")
    if system_rules:
        sections.append(f"[SYSTEM_RULES]\n{system_rules}")
    if grounding:
        sections.append(f"[GROUNDING]\n{grounding}")
    if memory:
        sections.append(f"[MEMORY]\n{memory}")

    messages = [
        {"role": "system", "content": "\n\n".join(sections)},
        {"role": "user", "content": user},
    ]
    if estimate_tokens(messages) > cfg.input_limit:
        messages, _ = enforce_prompt_cap(messages, cfg.input_limit)

    report = {
        "total": cfg.total_tokens,
        "output_reserve": cfg.output_reserve,
        "input_limit": cfg.input_limit,
        "system_rules": _text_token_estimate(system_rules),
        "user_history": _text_token_estimate(user, role="user"),
        "grounding": _text_token_estimate(grounding),
        "memory": _text_token_estimate(memory),
        "state_style": _text_token_estimate(state_style),
        "spare": cfg.spare_tokens,
        "assembled_input": estimate_tokens(messages),
    }
    return messages, report


def budget_existing_messages(
    messages: list[dict],
    *,
    total_tokens: int = 4800,
    output_reserve: int = 512,
) -> list[dict]:
    """Lưới an toàn cho các đường legacy chưa tách block.

    Hàm này không thay thế block-first assembler; nó chỉ ngăn parser/repair cũ
    gửi prompt vượt trần trong lúc các call site đang được di chuyển dần.
    """

    input_limit = max(256, int(total_tokens) - int(output_reserve))
    if estimate_tokens(messages) <= input_limit:
        return [dict(item) for item in messages]
    trimmed, _ = enforce_prompt_cap(messages, input_limit)
    return trimmed

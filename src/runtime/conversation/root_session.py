# -*- coding: utf-8 -*-
"""Root warm-up with an immutable bootstrap and self-contained cumulative overlays.

Không có Self/snapshot/database mới. Owner của identity và ký ức vẫn là những
service hiện hành; phiên này chỉ là cache *vận chuyển + hội thoại* của DeepSeek.
Một câu trả lời lỗi, bị preflight cắt hay không có nguyên văn wire response sẽ
làm hủy chuỗi trước khi tiếp tục. Không lưu private reasoning/chain-of-thought.
"""
from __future__ import annotations

import json
import hashlib
import os
import threading
import uuid
from copy import deepcopy
from typing import Any, Mapping, Sequence

from llm.token_budget import detect_provider, estimate_tokens, hard_cap_for_provider
from llm.provider_errors import provider_failure
from .self_prompt_view import project_self_view, project_bootstrap_self, project_runtime_context, state_difference as _changed
from .root_session_projector import (
    RootSessionState, SelfReference, TurnRecord, bootstrap_content, project_root_messages,
)


def _jsonable(value: Any) -> Any:
    """Chuyển view hiện có sang dữ liệu JSON, không tóm tắt hoặc xếp hạng belief."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError, OverflowError):
        return None


def _read_self(host: Any) -> dict[str, Any]:
    """Đọc nguyên các nguồn Self đang tồn tại; không đọc nhật ký nghĩ thầm thô.

    Giữ state Reducer (kể cả belief yếu/mâu thuẫn), chỉ nén view vận chuyển, không xén
    top-k. Dữ liệu thời gian thực mới đến lượt sau qua delta.
    """
    result: dict[str, Any] = {}
    reducer = getattr(host, "ai_state_reducer", None)
    snapshot = getattr(reducer, "snapshot", None)
    if callable(snapshot):
        state = snapshot()
        to_dict = getattr(state, "to_dict", None)
        if callable(to_dict):
            raw = to_dict()
            if isinstance(raw, Mapping):
                # Toàn bộ MindState là của reducer, không có luật lọc belief,
                # xếp hạng chủ đề hay giới hạn top-k nào ở đây.
                result["mind_state"] = _jsonable(raw)
    subject = getattr(host, "subject_service", None)
    view = getattr(subject, "self_view", None)
    if callable(view):
        raw = view()
        if isinstance(raw, Mapping):
            # generated_at là đồng hồ lúc projection, không phải Self delta.
            result["subject_self"] = _jsonable({k: v for k, v in raw.items() if k != "generated_at"})
    # Memory đã được host load từ owner thực, KHÔNG truy cập file/sqlite khác.
    # Không lọc theo topic hoặc lấy top-k. Có thể rất dài ở lần nạp đầu.
    for key in ("memory_state", "long_term_memory"):
        raw = getattr(host, key, None)
        if raw is not None:
            # Older restored snapshots may still carry the retired graph index.
            # Keep canonical memories while preventing its return in bootstrap/delta.
            if key == "memory_state" and isinstance(raw, Mapping):
                raw = {k: v for k, v in raw.items() if k != "concept_graph"}
            result[key] = _jsonable(raw)
    # Read only the current episode projection from its owner: maintenance may
    # have committed a recap since host.memory_state was loaded. No model call.
    living_user = getattr(getattr(host, "memory_kernel", None), "living_user", None)
    episode_view = getattr(living_user, "episode_prompt_view", None)
    memory = result.get("memory_state")
    if callable(episode_view) and isinstance(memory, dict):
        from memory.living_user.models import UserMemoryScope
        project_id = str(getattr(host, "project_id", "") or "")
        scope = UserMemoryScope("project", project_id) if project_id else UserMemoryScope()
        living = memory.get("living_user")
        memory["living_user"] = {**(living if isinstance(living, Mapping) else {}), **episode_view(scope=scope)}
    return project_self_view(result)


def _ingest_recent_turns(state: RootSessionState, recent_history: Sequence[Mapping[str, Any]]) -> int:
    """Nhập turn mới, cập nhật receipt cũ TẠI CHỖ mà không replay history.

    history_id có sẵn là định danh ưu tiên. Nếu history được dựng lại không có
    history_id, đối chiếu chuỗi suffix/prefix bằng lời gốc + role + delivery_id.
    Việc sinh UUID chỉ diễn ra KHI đưa một turn thực sự mới vào state. Vì vậy
    rebuild cùng một snapshot không tạo turn mới, kể cả nhiều câu trùng nhau.
    """
    incoming: list[dict[str, str | None]] = []
    for item in recent_history:
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role", ""))
        if role not in {"user", "assistant"}:
            continue
        raw = str(item.get("raw_content", item.get("content", "")))
        did = str(item.get("delivery_id") or "")[:180] or None
        status = str(item.get("delivery_status") or ("pending" if did else "none"))
        if status not in {"none", "pending", "delivered", "cancelled", "failed"}:
            status = "none"
        kind = str(item.get("kind", item.get("turn_kind", "public")))
        incoming.append({
            "turn_id": str(item.get("history_id") or "")[:180] or None,
            "role": role,
            "content": raw,
            "delivery_id": did,
            "status": status if role == "assistant" else "none",
            "kind": kind if kind in {"public", "autonomous"} else "public",
        })
    if not incoming:
        return 0

    def same(existing: TurnRecord, new: Mapping[str, str | None]) -> bool:
        # Status KHÔNG nằm trong khóa đối chiếu. Receipt flip không thể tạo turn.
        # So role/content giúp history đã reload vẫn khớp khi thiếu ID.
        # Hai history_id đã biết mà KHÁC nhau nghĩa là hai lượt khác nhau,
        # kể cả user gửi liên tiếp cùng đúng một chữ "ok".
        incoming_id = new["turn_id"]
        if incoming_id and existing.turn_id and incoming_id != existing.turn_id:
            return False
        return (existing.role == new["role"] and existing.content == new["content"]
                and existing.delivery_id == new["delivery_id"])

    # Tìm overlap lớn nhất giữa suffix đã biết và prefix mới, KHÔNG đưa
    # history vào step_payload và KHÔNG resend phần đã có.
    overlap = 0
    for count in range(min(len(state.turns), len(incoming)), 0, -1):
        if all(same(old, new) for old, new in zip(state.turns[-count:], incoming[:count], strict=False)):
            overlap = count
            break
    # Trường hợp state vừa thay đổi mà snapshot đã xuất hiện trước đó (caller
    # gọi lại cùng history cũ), giữ nguyên và chỉ cập nhật receipt theo ID.
    match_at = None
    if not overlap and state.turns and len(incoming) <= len(state.turns):
        for offset in range(len(state.turns) - len(incoming), -1, -1):
            if all(same(old, new) for old, new in zip(state.turns[offset:], incoming, strict=False)):
                overlap = len(incoming)
                match_at = offset
                break

    # Receipt là một nguồn sự thật duy nhất: ưu tiên delivery_id ổn định.
    by_delivery = {turn.delivery_id: turn for turn in state.turns if turn.delivery_id}
    by_id = {turn.turn_id: turn for turn in state.turns}
    for index, item in enumerate(incoming):
        original = None
        if item["delivery_id"]:
            original = by_delivery.get(item["delivery_id"])
        if original is None and item["turn_id"]:
            original = by_id.get(item["turn_id"])
        if original is None and index < overlap:
            original = state.turns[(len(state.turns) - overlap if match_at is None else match_at) + index]
        if original is not None:
            # Không sửa identity/content/position vì trạng thái âm thanh thay đổi.
            original.status = str(item["status"])

    new_count = 0
    for item in incoming[overlap:]:
        # Có thể đã đối chiếu qua ID ở trên: không chèn lần nữa.
        if (item["turn_id"] and item["turn_id"] in by_id) or (
            item["delivery_id"] and item["delivery_id"] in by_delivery
        ):
            continue
        record = TurnRecord(
            turn_id=str(item["turn_id"] or f"history:{uuid.uuid4().hex}"),
            role=str(item["role"]), content=str(item["content"]),
            delivery_id=item["delivery_id"], status=str(item["status"]),
            kind=str(item["kind"]),
        )
        state.turns.append(record)
        by_id[record.turn_id] = record
        if record.delivery_id:
            by_delivery[record.delivery_id] = record
        new_count += 1
    return new_count


class RootConversationSession:
    """Root session với true state riêng, projector dựng bounded prompt mỗi bước.

    RAW reply Root luôn giữ byte-khớp để SessionGuard V3 commit đúng response.
    Khi projector thay window, guard phải mở generation mới thay vì giả vờ
    nối remote transcript append-only (gateway hiện không duy trì session bền).
    """

    def __init__(self, *, logger: Any = None) -> None:
        self.logger = logger
        self._lock = threading.Lock()
        self._state = RootSessionState()
        self._started = False
        self._host: Any = None
        self._preloader: threading.Thread | None = None

    @staticmethod
    def enabled() -> bool:
        return os.environ.get("ERIS_ROOT_SESSION_BOOTSTRAP", "1").strip().lower() not in {"0", "off", "false", "no"} and os.environ.get("ERIS_WEB_SESSION_MODE", "isolated").strip().lower() not in {"off", "disabled", "none", "0", "legacy"}

    @staticmethod
    def _client(router: Any) -> Any:
        return (getattr(router, "executive_client", None)
                or getattr(router, "deep_client", None)
                or getattr(router, "seed_client", None)
                or getattr(router, "surface_client", None)
                or getattr(getattr(router, "llm_service", None), "client", None))

    def start(self, *, host: Any, router: Any, system: str, schema: Mapping[str, Any]) -> None:
        """Chạy warm-up sau khi Root và Self service đã được bootstrap.

        Đây là luồng nội bộ của ứng dụng Eris; startup không phải chờ inference.
        Nếu chưa sẵn sàng thì lượt user đầu tiên sẽ lazy-prime lại.
        """
        self._host = host
        if not self.enabled() or self._preloader is not None:
            return
        invoke = getattr(router, "invoke_structured_worker", None)
        client = self._client(router)
        if not callable(invoke) or client is None:
            return
        # Session Web phải thật sự qua gateway. Không warm-up đắt tiền trên
        # một API stateless chỉ vì nó tình cờ dùng tên model DeepSeek.
        uri = str(getattr(client, "base_url", "") or "").lower()
        if "api.deepseek.com" in uri or not any(x in uri for x in ("localhost", "127.0.0.1")):
            return
        self._preloader = threading.Thread(
            target=self._background_prime,
            kwargs={"router": router, "client": client, "system": system, "schema": schema},
            name="ErisRootSessionWarmup", daemon=True,
        )
        self._preloader.start()

    def _background_prime(self, *, router: Any, client: Any, system: str, schema: Mapping[str, Any]) -> None:
        try:
            with self._lock:
                self._prime(router=router, client=client, system=system, schema=schema)
        except Exception as exc:  # noqa: BLE001
            self._clear()
            self._log("warning", "[root-session] startup warmup failed: %s", type(exc).__name__)

    def _log(self, level: str, msg: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None)
        if callable(fn):
            try:
                fn(msg, *args)
            except Exception:
                pass

    def _clear(self) -> None:
        # Không giữ các transcript/messages lỗi để session mới tái dùng nhầm.
        self._state = RootSessionState()
        self._started = False

    def _fits(self, client: Any, messages: Sequence[Mapping[str, str]]) -> bool:
        provider = detect_provider(str(getattr(client, "base_url", "")), str(getattr(client, "model_name", "")))
        # Chừa room cho JSON schema được client bổ sung và output. Không lặng
        # lẽ cắt bootstrap, vì như vậy không còn là đọc toàn bộ Self.
        cap = hard_cap_for_provider(provider)
        uri = str(getattr(client, "base_url", "") or "").lower()
        model = str(getattr(client, "model_name", "") or "").lower()
        if "deepseek" in model and any(x in uri for x in ("localhost", "127.0.0.1")):
            try:
                requested = int(os.environ.get("ERIS_ROOT_SESSION_PROMPT_CAP_TOKENS", "90000"))
                if requested > 0:
                    cap = max(cap, requested)
            except (TypeError, ValueError):
                pass
        margin = min(4000, max(320, int(cap * 0.06)))
        return estimate_tokens(messages) + margin < cap

    def _call(
        self, *, router: Any, client: Any, system: str,
        schema: Mapping[str, Any], messages: list[dict[str, str]],
    ) -> tuple[dict[str, Any], str]:
        self._log("info", "[root-session] request_size messages=%d message_chars=%s estimated_tokens=%d estimator=chars3",
                  len(messages), [len(message.get("content", "")) for message in messages], estimate_tokens(messages))
        raw: list[str] = []
        result = router.invoke_structured_worker(
            client, system=system, payload={}, schema=schema,
            name="eris_root_conversation_step_v12", max_tokens=1200,
            reasoning_effort="medium", inference_tier="subconscious_4b",
            cognitive_lane="root_conversation", priority=70, temperature=0.3,
            conversation_messages=messages, raw_reply_sink=raw,
        )
        if not isinstance(result, Mapping) or not result or len(raw) != 1:
            raise RuntimeError("root_session_wire_response_missing")
        return dict(result), raw[0]

    def _prime(self, *, router: Any, client: Any, system: str, schema: Mapping[str, Any]) -> bool:
        self._clear()
        if self._host is None:
            return False
        # Episode windows/recaps must age out of the actual request, not merely
        # be overridden while their original detail survives in this prefix.
        self_context = project_bootstrap_self(_read_self(self._host))
        if not self_context:
            self._log("warning", "[root-session] Self owner chưa sẵn sàng")
            return False
        # content_json bất biến; không tóm tắt, chọn top-k hay cắt belief.
        content_json = json.dumps(self_context, ensure_ascii=False, separators=(",", ":"))
        breakdown: dict[str, int] = {}
        for prefix, section in (
            ("self", self_context),
            ("memory", self_context.get("memory_state")),
            ("living_user", (self_context.get("memory_state") or {}).get("living_user")
             if isinstance(self_context.get("memory_state"), Mapping) else None),
        ):
            if isinstance(section, Mapping):
                for key, value in section.items():
                    breakdown[f"{prefix}.{key}"] = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        self._log("info", "[root-session] self_component_chars=%s", json.dumps(breakdown, ensure_ascii=False, separators=(",", ":")))
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": bootstrap_content(content_json)},
        ]
        if not self._fits(client, messages):
            self._log("warning", "[root-session] full Self vượt model context cap; không nạp bản thiếu")
            return False
        reply, original = self._call(router=router, client=client, system=system, schema=schema, messages=messages)
        if reply.get("kind") != "no_reply":
            raise RuntimeError("root_session_bootstrap_not_acknowledged")
        self._state = RootSessionState(
            self_ref=SelfReference(
                content_json=content_json,
                sections=len(self_context), char_len=len(content_json),
                fingerprint=hashlib.sha256(content_json.encode("utf-8")).hexdigest()[:16],
                ack_reply_raw=original,
            ),
            system_prompt=system,
            last_self_snapshot=deepcopy(self_context),
            bootstrap_self_snapshot=deepcopy(self_context),
            last_reply_raw=original,
        )
        self._started = True
        self._log("info", "[root-session] full Self loaded; sections=%d chars=%d fingerprint=%s",
                  len(self_context), len(messages[1]["content"]), self._state.self_ref.fingerprint)
        return True

    @staticmethod
    def _window_pairs() -> int:
        try:
            return max(2, int(os.environ.get("ERIS_ROOT_SESSION_WINDOW_PAIRS", "12")))
        except (TypeError, ValueError):
            return 12

    def ask(
        self, *, router: Any, client: Any, system: str, schema: Mapping[str, Any],
        payload: Mapping[str, Any], runtime_context: Mapping[str, Any],
        recent_history: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        """Không block warm-up; khi lỗi caller vẫn dùng one-shot theo hợp đồng cũ."""
        if not self.enabled() or self._host is None or not self._lock.acquire(blocking=False):
            return None
        try:
            state = self._state
            if not self._started or state.system_prompt != system:
                if not self._prime(router=router, client=client, system=system, schema=schema):
                    return None
                state = self._state
            self_now = _read_self(self._host)
            self_delta = _changed(state.bootstrap_self_snapshot, self_now) or {}
            newly_added = _ingest_recent_turns(state, recent_history)
            # Chỉ gửi event/note/tool_results; 2 kênh history_delta/reconcile
            # và delivery_updates CŨ hoàn toàn không còn đường sinh ra.
            current_ctx = dict(payload.get("runtime_context") or {})
            for key in ("personality_context", "personality_summary", "relationship_context",
                        "previous_public_excerpt", "memory_context"):
                current_ctx.pop(key, None)
            current_ctx = project_runtime_context(current_ctx, self_now)
            state.current_step_payload = {
                k: v for k, v in payload.items()
                if k not in {"recent_public_history", "runtime_context",
                             "recent_public_history_delta", "recent_public_history_reconcile",
                             "recent_public_delivery_updates"}
            }
            state.current_step_payload["runtime_context"] = current_ctx
            state.current_step_payload["self_changes_since_bootstrap"] = self_delta
            state.current_step_payload["self_base_fingerprint"] = state.self_ref.fingerprint
            state.runtime_context = deepcopy(current_ctx)

            # Self vượt cap => không rút ngắn Self, chuyển sang one-shot.
            prefix = [
                {"role": "system", "content": state.system_prompt},
                {"role": "user", "content": bootstrap_content(state.self_ref.content_json)},
            ]
            if not self._fits(client, prefix):
                self._log("warning", "[root-session] full Self vượt cap; fallback one-shot")
                return None

            k = self._window_pairs()
            messages = project_root_messages(state, window_k=k)
            while not self._fits(client, messages) and k > 2:
                # Chỉ thu hẹp projection LƯỢT NÀY; state/Self nguyên vẹn.
                k = max(2, k // 2)
                messages = project_root_messages(state, window_k=k)
            if not self._fits(client, messages):
                # Never truncate the cumulative overlay/current context. A partial
                # overlay would resurrect bootstrap values or hide deletions.
                self._log("warning", "[root-session] cannot fit complete state overlay; fallback one-shot")
                return None
            result, original = self._call(router=router, client=client, system=system, schema=schema, messages=messages)
            state.last_reply_raw = original
            # Keep the last successful sample for diagnostics; overlay always uses the immutable bootstrap.
            state.last_self_snapshot = deepcopy(self_now)
            state.last_context = deepcopy(current_ctx)
            state.current_step_payload = {}
            self._log("info", "[root-session] root_projected turns=%d window=%d self_fields=%d new_turns=%d",
                      len(state.turns), min(len(state.turns), 2 * k), len(self_delta), newly_added)
            return result
        except Exception as exc:  # noqa: BLE001
            self._clear()
            self._log("warning", "[root-session] resync needed: %s", type(exc).__name__)
            failure = provider_failure(exc, role="root")
            if failure is not None:
                raise failure from exc
            return None
        finally:
            self._lock.release()


__all__ = [
    "RootConversationSession", "_read_self", "_changed", "_ingest_recent_turns",
    "SelfReference", "TurnRecord", "RootSessionState", "project_root_messages",
]

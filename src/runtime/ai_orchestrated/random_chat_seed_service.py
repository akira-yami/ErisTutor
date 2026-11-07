# -*- coding: utf-8 -*-
"""Background random-chat seed generation for autonomous ERIS speech.

The seed worker has its own DeepSeek role and DS2API port. It never writes
public wording and it never runs on the autonomous speech critical path.
Instead, it keeps a small, diverse reservoir of typed premises. When the
initiative governor later opens an idle social lane, runtime leases one seed
atomically and Surface only realizes that already-approved premise.

The queue itself is intentionally session-local. Learned affinity and ERIS
interest remain persisted by :mod:`random_chat_seed`; transient premises do not
survive a restart and wake up in a different conversation like confused ghosts.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from core.temporal_context import classify_time_of_day, resolve_timezone
from cortex.mental_material import MATERIAL_KINDS, derive_causal_motives

from .random_chat_seed import (
    RandomChatSeed,
    RandomChatSeedAffinityStore,
    SeedQualityAssessment,
    assess_seed_quality,
    seed_event_family,
)


RANDOM_CHAT_SEED_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "decision", "seed_id", "material_kind", "premise", "topic_tags",
        "epistemic_mode", "open_loop", "expected_update", "uncertainty",
        "confidence", "ttl_seconds",
    ],
    "properties": {
        "decision": {"type": "string", "enum": ["seed", "none"]},
        "seed_id": {"type": "string"},
        "material_kind": {"type": "string", "enum": sorted(MATERIAL_KINDS)},
        # V70: optional because non-ruminative causes do not need a relation mode.
        "development_mode": {
            "type": "string",
            "enum": ["none", "deepen", "contradict", "connect", "reinterpret", "test", "personalize"],
        },
        "premise": {"type": "string"},
        "topic_tags": {"type": "array", "items": {"type": "string"}, "minItems": 0, "maxItems": 8},
        "epistemic_mode": {
            "type": "string",
            "enum": [
                "grounded_fact", "bounded_inference", "speculation", "imagination",
                "opinion", "question", "research_intent",
            ],
        },
        "open_loop": {"type": "boolean"},
        # Question V45 phải có nơi cập nhật state nếu user trả lời. Với material
        # khác field này có thể rỗng. Đây không phải desired_response/public plan.
        "expected_update": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "uncertainty": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "ttl_seconds": {"type": "number", "minimum": 90.0, "maximum": 1800.0},
    },
}

# Diversity is managed at the level of mental-event families first, then raw
# event kinds.  A reservoir containing thought/opinion/self_reflection is not
# meaningfully diverse if all three are reflective prose wearing different tags.
_EVENT_FAMILY_MIX: dict[str, float] = {
    "curiosity": 0.19,
    "cognitive": 0.17,
    "associative": 0.15,
    "play": 0.14,
    "creative": 0.13,
    "uncertain_world": 0.12,
    "self": 0.10,
}

_EVENT_KIND_MIX: dict[str, dict[str, float]] = {
    "curiosity": {
        "curiosity_question": 0.40,
        "knowledge_gap": 0.32,
        "discovery_impulse": 0.28,
    },
    "cognitive": {
        "thought": 0.28,
        "contradiction_notice": 0.26,
        "surprise": 0.24,
        "opinion": 0.22,
    },
    "associative": {
        "association": 0.35,
        "memory_callback": 0.33,
        "unfinished_loop": 0.32,
    },
    "play": {
        "playful_probe": 0.58,
        "micro_challenge": 0.42,
    },
    "creative": {
        "creative_impulse": 0.52,
        "imagination": 0.48,
    },
    "uncertain_world": {
        "speculation": 0.52,
        "prediction": 0.48,
    },
    "self": {
        "self_reflection": 1.0,
    },
}

_EVENT_KIND_CONTRACTS: dict[str, str] = {
    "curiosity_question": "A concrete question/unknown target; compact is fine; epistemic_mode=question or research_intent.",
    "knowledge_gap": "Name what ERIS does not know and keep open_loop=true; do not pad it into a reflection.",
    "discovery_impulse": "Name what ERIS wants to inspect/learn next; open_loop=true and motive should be exploration/knowledge.",
    "thought": "A specific thought with an anchor or why-now; avoid aphorisms and generic life observations.",
    "opinion": "A clear stance about a concrete subject; it may be one compact sentence.",
    "surprise": "A specific unexpected relation/change, preferably with provenance or recent context.",
    "contradiction_notice": "A concrete tension/inconsistency between two known pieces; cite source refs when available.",
    "association": "A clear A→B connection; two anchors matter more than prose length.",
    "memory_callback": "A concrete callback to a source_ref; memory without provenance is invalid.",
    "unfinished_loop": "A previously opened thread that still has force; open_loop=true and preserve its source when available.",
    "playful_probe": "A small playable/social move with an optional response shape and low intrusion; brevity is desirable.",
    "micro_challenge": "A tiny concrete challenge/game/test with a response shape; do not turn it into commentary.",
    "creative_impulse": "Something ERIS wants to make/try; imagination is allowed and need not masquerade as insight.",
    "imagination": "An explicitly imagined possibility; epistemic_mode=imagination and no current-world factual promotion.",
    "speculation": "A modal hypothesis with uncertainty; never promote it to current-world fact without evidence.",
    "prediction": "A bounded prediction target with uncertainty; distinguish clock/context from world evidence.",
    "self_reflection": "A reflection about ERIS's own process/state; semantic richness helps here, but poetry is not required.",
}



def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "disabled", "none"}


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()


def _token_set(text: str) -> set[str]:
    import re

    return {
        token
        for token in re.findall(r"[\w\u00c0-\u024f\u1e00-\u1eff]+", str(text or "").lower())
        if len(token) >= 3
    }


def _similarity(left: str, right: str) -> float:
    a, b = _token_set(left), _token_set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _fingerprint(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:20]


def build_seed_temporal_context(now: float | None = None) -> dict[str, Any]:
    """Build authoritative user-local clock evidence for seed cognition.

    The returned context deliberately proves only time/calendar facts.  It does
    not prove weather, illumination, ambient sound, user activity, fatigue, or
    any other current-world state.  Reservoir generation and later materialize
    both call this helper, so a seed never carries a stale daypart merely because
    it waited in memory for a while.
    """

    current = float(now if now is not None else time.time())
    timezone_name, timezone_info = resolve_timezone()
    local = datetime.fromtimestamp(current, timezone_info)
    daypart, daypart_vi = classify_time_of_day(local.hour)
    return {
        "schema": "eris.seed_temporal_context.v1",
        "bound_at_epoch": current,
        "local_iso": local.isoformat(timespec="seconds"),
        "timezone": timezone_name,
        "local_date": local.date().isoformat(),
        "local_time": local.strftime("%H:%M:%S"),
        "weekday": local.strftime("%A"),
        "local_hour": int(local.hour),
        "hour": int(local.hour),
        "minute": int(local.minute),
        "daypart": daypart,
        "daypart_vi": daypart_vi,
        "evidence_source": "runtime_clock",
        "environment_evidence": False,
        "sensor_evidence": False,
        "supports": [
            "current_user_local_time",
            "current_user_local_date",
            "weekday",
            "daypart_by_clock",
        ],
        "does_not_support": [
            "weather",
            "ambient_sound",
            "room_lighting",
            "outdoor_lighting",
            "user_activity",
            "user_emotion",
            "user_fatigue",
        ],
    }


class RandomChatSeedGenerator:
    """Own the seed prompt while a shared invoker owns transport mechanics."""

    causal_material_generation = True

    def __init__(
        self,
        client: Any,
        *,
        invoke_structured: Callable[..., Mapping[str, Any]],
        logger: Any | None = None,
    ) -> None:
        self.client = client
        self.invoke_structured = invoke_structured
        self.logger = logger

    @property
    def enabled(self) -> bool:
        return self.client is not None and callable(self.invoke_structured)

    @staticmethod
    def prompt() -> str:
        return (
            "Bạn là ASSOCIATIVE_MATERIAL_WORKER của ERIS V100. Bạn KHÔNG viết câu public, KHÔNG quyết định ERIS có nên nói, "
            "KHÔNG tự khai motive/why_now/opening_move/desired_response. Runtime đã đưa causal_motive tồn tại TRƯỚC output của bạn. "
            "Trong production V100, causal_motive đã được bind từ BoundFocus có provenance; bạn KHÔNG được đổi topic hoặc tự phát minh focus khác. "
            "Nhiệm vụ duy nhất: materialize một mảnh nội tâm cụ thể từ semantic_focus của causal_motive. "
            "requested_material_kind chỉ là hình dạng ưu tiên trong allowed_material_kinds của motive; nếu không có material thật, "
            "trả decision=none. Không đổi nguyên nhân chỉ để thỏa quota diversity. "
            "Material có thể là observation, opinion, association, memory_callback, unfinished_thought, desire, plan, reaction, "
            "question, playful_impulse, small_contact, self_reflection, hunch hoặc story_fragment. Đây là mental object, không phải lời thoại hoàn chỉnh. "
            "story_fragment là truyện/ẩn dụ/tình huống tưởng tượng ngắn để company/chill; phải giữ epistemic_mode=imagination, "
            "không được kể như ký ức thật của ERIS hay sự kiện thật của user. "
            "small_contact là hợp lệ khi nó bám vào semantic_focus thật; không tạo check-in chung chung để lấp im lặng. "
            "question chỉ dùng khi có một gap cụ thể và phải khai expected_update: state/model nào của ERIS có thể đổi nếu user trả lời. "
            "Nếu expected_update rỗng, ưu tiên observation/opinion/reaction thay vì hỏi cho có hội thoại. "
            "Không biến mọi thứ thành câu hỏi triết học. Một reaction ngắn, một opinion có stance, một association cụ thể hay một câu nhẹ "
            "đúng context thường tốt hơn essay. Không paraphrase nguyên semantic_focus rồi gọi đó là novelty. "
            "V70: nếu causal_motive.development_required=true, đây là một private return vào concern đã được nghĩ/nói trước đó. "
            "Bạn PHẢI chọn development_mode trong allowed_development_modes và premise phải thực sự deepen, contradict, connect, reinterpret, "
            "test hoặc personalize semantic_focus. Nếu chỉ có thể restate/paraphrase, trả decision=none và development_mode=none. "
            "Nếu development_required=false, development_mode=none là bình thường. Đừng biến rumination thành cùng một thesis mặc sáu bộ quần áo. "
            "V75: nếu causal_motive.cause_kind=conversation_trajectory, premise PHẢI phát triển semantic_focus của đúng topic_id; "
            "ưu tiên deepen/connect/branch có nội dung, không mở một câu hỏi xã giao hay chủ đề ngẫu nhiên chỉ vì novelty. "
            "Public wording trước đó của ERIS không phải evidence. Không tự thêm source_refs; provenance thuộc causal_motive runtime-owned. "
            "seed_context là projection môi trường/cognition nhỏ nhất dành riêng cho seed; nếu có thì ưu tiên nó hơn raw environment_awareness. "
            "environment_awareness chỉ là compatibility context. Nếu causal_motive.cause_kind=environment_event, "
            "bạn ĐƯỢC dùng seed_context.current_activity/current_task/phase/meaningful_change và các field tươi/confident tương ứng để tạo observation/reaction/association liên quan đúng app/task/phase; "
            "nhưng đây KHÔNG phải VLM/pixel vision, nên không được bịa màu sắc, vị trí nút, code text hay layout hình ảnh. "
            "Ngoài trường hợp causal focus có evidence môi trường, không suy diễn current-world facts, cảm xúc/kế hoạch user, thời tiết, âm thanh hay ánh sáng. "
            "epistemic_mode phải đúng với material: opinion là stance, hunch/speculation giữ modal, grounded_fact cần evidence thật từ runtime. "
            "generation_temporal_context chỉ chứng minh clock/calendar. ttl_seconds thường 180-900 giây. Return exactly one JSON object."
        )

    def generate(self, request: Mapping[str, Any]) -> RandomChatSeed:
        if not self.enabled:
            return RandomChatSeed()
        timeout = _env_float(
            "ERIS_RANDOM_CHAT_SEED_WORKER_TIMEOUT_SEC",
            35.0,
            minimum=6.0,
            maximum=120.0,
        )
        try:
            raw = self.invoke_structured(
                self.client,
                system=self.prompt(),
                payload=request,
                schema=RANDOM_CHAT_SEED_OUTPUT_SCHEMA,
                name="eris_random_chat_seed_background",
                max_tokens=560,
                reasoning_effort=None,
                inference_tier="subconscious_4b",
                priority=18,
                timeout_seconds=timeout,
            )
        except Exception as exc:  # transport errors must not touch public speech
            self._log("warning", "[random chat seed] background worker failed: %s", exc)
            return RandomChatSeed()
        causal_motive = request.get("causal_motive") if isinstance(request.get("causal_motive"), Mapping) else {}
        if not causal_motive:
            # Compatibility cho tool/test/plugin gọi generator trực tiếp. Production
            # reservoir V45 luôn truyền causal_motive trước model call.
            return RandomChatSeed.from_mapping(raw)
        reservoir_context = request.get("reservoir_context") if isinstance(request.get("reservoir_context"), Mapping) else {}
        return RandomChatSeed.from_material_mapping(
            raw,
            causal_motive=causal_motive,
            requested_mode=str(reservoir_context.get("requested_mode", "eris_led") or "eris_led"),
        )

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


@dataclass(frozen=True, slots=True)
class SeedReservoirPreview:
    seed: RandomChatSeed
    generated_at: float
    expires_at: float
    context_revision: int

@dataclass(frozen=True, slots=True)
class SeedReservoirLease:
    token: str
    seed: RandomChatSeed
    generated_at: float
    expires_at: float
    context_revision: int


@dataclass(slots=True)
class _SeedEntry:
    seed: RandomChatSeed
    generated_at: float
    expires_at: float
    context_revision: int
    user_epoch: int
    context_fingerprint: str


@dataclass(slots=True)
class _LeaseEntry:
    entry: _SeedEntry
    leased_at: float
    lease_expires_at: float


class RandomChatSeedReservoir:
    """Background producer plus atomic in-memory reservoir.

    `update_context()` is cheap and may be called every autonomous tick. The
    DeepSeek call occurs only in this service's daemon thread. `claim()` never
    calls a model, so speech latency no longer includes seed generation.
    """

    def __init__(
        self,
        generator: RandomChatSeedGenerator,
        *,
        seed_store: RandomChatSeedAffinityStore | None = None,
        wake_callback: Callable[[], Any] | None = None,
        status_callback: Callable[..., Any] | None = None,
        mental_event_sink: Callable[[RandomChatSeed], Any] | None = None,
        causal_motive_provider: Callable[..., Sequence[Any]] | None = None,
        logger: Any | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.generator = generator
        self.seed_store = seed_store
        self.wake_callback = wake_callback
        self.status_callback = status_callback
        self.mental_event_sink = mental_event_sink
        # V100: khi provider này tồn tại, production không còn dùng
        # derive_causal_motives(context) để tự chọn topic. Provider phải trả
        # CausalMotive đã bind từ BoundFocus có provenance.
        self.causal_motive_provider = causal_motive_provider
        self.logger = logger
        self.clock = clock
        self.enabled = bool(
            generator.enabled
            and _env_bool("ERIS_RANDOM_CHAT_SEED_RESERVOIR_ENABLED", True)
        )
        self.target_size = _env_int("ERIS_RANDOM_CHAT_SEED_TARGET_SIZE", 4, minimum=1, maximum=12)
        self.max_size = max(
            self.target_size,
            _env_int("ERIS_RANDOM_CHAT_SEED_MAX_SIZE", 7, minimum=1, maximum=20),
        )
        self.min_generate_interval = _env_float(
            "ERIS_RANDOM_CHAT_SEED_GENERATE_INTERVAL_SEC", 12.0, minimum=1.0, maximum=600.0
        )
        self.none_backoff = _env_float(
            "ERIS_RANDOM_CHAT_SEED_NONE_BACKOFF_SEC", 24.0, minimum=2.0, maximum=900.0
        )
        self.error_backoff = _env_float(
            "ERIS_RANDOM_CHAT_SEED_ERROR_BACKOFF_SEC", 40.0, minimum=2.0, maximum=900.0
        )
        self.default_ttl = _env_float(
            "ERIS_RANDOM_CHAT_SEED_RESERVOIR_TTL_SEC", 600.0, minimum=90.0, maximum=1800.0
        )
        self.min_quality = _env_float(
            "ERIS_RANDOM_CHAT_SEED_MIN_QUALITY", 0.46, minimum=0.0, maximum=1.0
        )
        self.lease_ttl = _env_float(
            "ERIS_RANDOM_CHAT_SEED_LEASE_TTL_SEC", 45.0, minimum=5.0, maximum=180.0
        )
        self._condition = threading.Condition(threading.RLock())
        self._entries: list[_SeedEntry] = []
        self._leases: dict[str, _LeaseEntry] = {}
        self._context_request: dict[str, Any] = {}
        self._context_fingerprint = ""
        self._context_revision = 0
        self._user_epoch = 0
        self._last_generate_at = 0.0
        self._next_generate_at = 0.0
        self._last_claimed_mode = ""
        self._last_claimed_event_kind = ""
        # V45: budget theo causal source, không theo "kho thiếu loại gì". Count
        # sống suốt session để cùng một user event không đẻ vô hạn paraphrase sau
        # khi material cũ hết TTL. Concern mutation tạo cause_id mới nên vẫn có
        # quyền sinh material mới khi thực sự có thay đổi.
        self._cause_generation_counts: dict[str, int] = {}
        self._cause_material_counts: dict[str, dict[str, int]] = {}
        self._stop = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if not self.enabled:
            self._log("info", "[random chat seed] reservoir disabled: seed worker unavailable or feature off")
            return False
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop = False
            self._thread = threading.Thread(
                target=self._run,
                name="eris-random-chat-seed-reservoir",
                daemon=True,
            )
            self._thread.start()
        self._log(
            "info",
            "[random chat seed] reservoir started target=%d max=%d port_owner=seed",
            self.target_size,
            self.max_size,
        )
        return True

    def shutdown(self) -> None:
        with self._condition:
            self._stop = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._log("info", "[random chat seed] reservoir stopped")

    def update_context(self, snapshot: Mapping[str, Any], *, now: float | None = None) -> bool:
        """Publish a compact public-safe generation context without model work."""

        if not self.enabled:
            return False
        current = float(now if now is not None else self.clock())
        request = self._context_from_snapshot(snapshot)
        fingerprint = _fingerprint(self._fingerprint_projection(request))
        with self._condition:
            changed = fingerprint != self._context_fingerprint
            self._context_request = request
            if changed:
                self._context_fingerprint = fingerprint
                self._context_revision += 1
                self._prune_locked(current)
            self._condition.notify_all()
        return changed

    def observe_user_turn(self, *, now: float | None = None) -> None:
        """Invalidate transient scene seeds; durable memory/self seeds may survive."""

        current = float(now if now is not None else self.clock())
        with self._condition:
            self._user_epoch += 1
            self._context_revision += 1
            # Pause production until SelfTurnPipeline publishes a fresh post-user
            # snapshot. Otherwise the background lane could happily stock a
            # scene seed from the conversation that just became stale.
            self._context_request = {}
            self._context_fingerprint = ""
            self._entries = [
                entry
                for entry in self._entries
                if entry.expires_at > current
                and entry.seed.source_kind in {"user_memory", "eris_self_memory"}
            ]
            self._condition.notify_all()

    def peek(self, *, now: float | None = None) -> SeedReservoirPreview | None:
        """Return the best ready seed without leasing or removing it."""
        if not self.enabled:
            return None
        current = float(now if now is not None else self.clock())
        with self._condition:
            self._prune_locked(current)
            candidates = [entry for entry in self._entries if self._entry_compatible_locked(entry)]
            if not candidates:
                self._condition.notify_all()
                return None
            chosen = max(candidates, key=lambda entry: self._score_entry(entry, current))
            return SeedReservoirPreview(
                seed=chosen.seed, generated_at=chosen.generated_at, expires_at=chosen.expires_at,
                context_revision=chosen.context_revision,
            )

    def claim_seed(
        self, seed_id: str, *, context_revision: int, now: float | None = None
    ) -> SeedReservoirLease | None:
        """Lease exactly the seed selected by the Executive."""
        if not self.enabled or not seed_id:
            return None
        current = float(now if now is not None else self.clock())
        with self._condition:
            self._prune_locked(current)
            chosen = next((
                entry for entry in self._entries
                if entry.seed.seed_id == str(seed_id)
                and entry.context_revision == int(context_revision)
                and self._entry_compatible_locked(entry)
            ), None)
            if chosen is None:
                return None
            self._entries.remove(chosen)
            token = "seedlease_" + secrets.token_hex(10)
            self._leases[token] = _LeaseEntry(
                entry=chosen, leased_at=current, lease_expires_at=current + self.lease_ttl
            )
            self._last_claimed_mode = chosen.seed.mode
            self._last_claimed_event_kind = chosen.seed.event_kind
            self._condition.notify_all()
            return SeedReservoirLease(
                token=token, seed=chosen.seed, generated_at=chosen.generated_at,
                expires_at=chosen.expires_at, context_revision=chosen.context_revision,
            )

    def claim(self, *, now: float | None = None) -> SeedReservoirLease | None:
        """Compatibility wrapper over peek + exact claim."""
        preview = self.peek(now=now)
        if preview is None:
            return None
        return self.claim_seed(
            preview.seed.seed_id, context_revision=preview.context_revision, now=now
        )

    def release(self, token: str, *, reason: str = "deferred", now: float | None = None) -> bool:
        """Return a leased seed when the governor or Surface did not publish it."""

        if not token:
            return False
        current = float(now if now is not None else self.clock())
        with self._condition:
            leased = self._leases.pop(str(token), None)
            if leased is None:
                return False
            entry = leased.entry
            if entry.expires_at > current and self._entry_compatible_locked(entry):
                self._entries.append(entry)
                self._entries = self._entries[-self.max_size :]
            self._condition.notify_all()
        self._log("debug", "[random chat seed] lease released seed=%s reason=%s", entry.seed.seed_id, reason)
        return True

    def commit(self, token: str) -> bool:
        """Consume a lease after a successful public commit."""

        if not token:
            return False
        with self._condition:
            leased = self._leases.pop(str(token), None)
            self._condition.notify_all()
        if leased is None:
            return False
        self._log("debug", "[random chat seed] lease committed seed=%s", leased.entry.seed.seed_id)
        return True

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        current = float(now if now is not None else self.clock())
        with self._condition:
            self._prune_locked(current)
            return {
                "schema": "eris.random_chat_seed_reservoir.v1",
                "enabled": self.enabled,
                "worker_role": "seed",
                "material_authority": (
                    "bound_focus_v100" if callable(self.causal_motive_provider) else "legacy_context_scan"
                ),
                "target_size": self.target_size,
                "max_size": self.max_size,
                "ready": len(self._entries),
                "leased": len(self._leases),
                "context_revision": self._context_revision,
                "context_fingerprint": self._context_fingerprint,
                "user_epoch": self._user_epoch,
                "modes": {
                    mode: sum(1 for entry in self._entries if entry.seed.mode == mode)
                    for mode in ("shared", "user_led", "eris_led", "experimental")
                },
                "event_kinds": {
                    kind: sum(1 for entry in self._entries if entry.seed.event_kind == kind)
                    for kind in sorted({entry.seed.event_kind for entry in self._entries})
                },
                "event_families": {
                    family: sum(1 for entry in self._entries if seed_event_family(entry.seed.event_kind) == family)
                    for family in _EVENT_FAMILY_MIX
                },
                "seeds": [
                    {
                        "seed_id": entry.seed.seed_id,
                        "mode": entry.seed.mode,
                        "source_kind": entry.seed.source_kind,
                        "event_kind": entry.seed.event_kind,
                        "event_family": seed_event_family(entry.seed.event_kind),
                        "epistemic_mode": entry.seed.epistemic_mode,
                        "open_loop": entry.seed.open_loop,
                        "premise": entry.seed.premise,
                        "topic_tags": list(entry.seed.topic_tags),
                        "quality": assess_seed_quality(entry.seed).to_dict(),
                        "generated_at": entry.generated_at,
                        "generated_temporal_context": build_seed_temporal_context(entry.generated_at),
                        "expires_at": entry.expires_at,
                    }
                    for entry in self._entries
                ],
            }

    def _status(self, **payload: Any) -> None:
        # V42: reservoir chạy nền 24/7 nên không được giả làm "Eris sắp nói".
        # Observability vẫn nằm ở log/snapshot; public status chỉ dành cho hành vi
        # mà user thật sự đang chờ.
        if str(payload.get("activity") or "") == "random_chat_seed":
            return
        callback = self.status_callback
        if not callable(callback):
            return
        try:
            callback(**payload)
        except Exception as exc:
            self._log("debug", "[random chat seed] status callback failed: %s", exc)

    def _run(self) -> None:
        while True:
            with self._condition:
                current = float(self.clock())
                self._prune_locked(current)
                while not self._stop and not self._ready_to_generate_locked(current):
                    wait_for = self._wait_seconds_locked(current)
                    self._condition.wait(timeout=wait_for)
                    current = float(self.clock())
                    self._prune_locked(current)
                if self._stop:
                    return
                request = self._generation_request_locked()
                context_revision = self._context_revision
                user_epoch = self._user_epoch
                context_fingerprint = self._context_fingerprint
                self._last_generate_at = current
                self._next_generate_at = current + self.min_generate_interval

            try:
                seed = self.generator.generate(request)
            except Exception as exc:  # last-resort containment
                self._log("warning", "[random chat seed] producer crashed softly: %s", exc)
                seed = RandomChatSeed()
                failed = True
            else:
                failed = False

            current = float(self.clock())
            wake = False
            allowed = False
            reason = ""
            ready_count = 0
            with self._condition:
                if failed:
                    self._next_generate_at = max(self._next_generate_at, current + self.error_backoff)
                elif not seed.available:
                    self._next_generate_at = max(self._next_generate_at, current + self.none_backoff)
                else:
                    allowed, reason = self._validate_generated_locked(seed, current)
                    if allowed:
                        was_empty = not self._entries
                        effective_ttl = min(
                            self.default_ttl,
                            max(120.0, float(seed.ttl_seconds or self.default_ttl)),
                        )
                        self._entries.append(
                            _SeedEntry(
                                seed=seed,
                                generated_at=current,
                                expires_at=current + effective_ttl,
                                context_revision=context_revision,
                                user_epoch=user_epoch,
                                context_fingerprint=context_fingerprint,
                            )
                        )
                        self._entries = self._entries[-self.max_size :]
                        if seed.material_only and seed.cause_id:
                            self._cause_generation_counts[seed.cause_id] = int(
                                self._cause_generation_counts.get(seed.cause_id, 0) or 0
                            ) + 1
                            material_counts = self._cause_material_counts.setdefault(seed.cause_id, {})
                            material_counts[seed.material_kind] = int(material_counts.get(seed.material_kind, 0) or 0) + 1
                        wake = bool(seed.material_only) or was_empty
                        quality = assess_seed_quality(seed)
                        temporal = build_seed_temporal_context(current)
                        self._log(
                            "info",
                            (
                                "[associative material] stocked seed=%s material=%s cause=%s/%s motive=%s "
                                "quality=%.2f timezone=%s local_hour=%s daypart=%s environment_evidence=%s premise=%s ready=%d"
                            ),
                            seed.seed_id,
                            seed.material_kind or seed.event_kind,
                            seed.cause_kind or seed.source_kind,
                            seed.cause_id or "legacy",
                            seed.runtime_motive_kind or seed.eris_motive_kind,
                            quality.score,
                            temporal.get("timezone", ""),
                            temporal.get("local_hour", temporal.get("hour", "")),
                            temporal.get("daypart", ""),
                            str(bool(temporal.get("environment_evidence", False))).lower(),
                            _compact(seed.premise, 180),
                            len(self._entries),
                        )
                    else:
                        self._log("debug", "[random chat seed] generated seed rejected reason=%s", reason)
                ready_count = len(self._entries)
                self._condition.notify_all()

            # V45: sau validation, seed đi vào MentalMaterialRuntime. Nó KHÔNG
            # tự trở thành concern hay speech candidate. Sink nằm ngoài lock để
            # lifecycle/persistence khác không giữ producer thread.
            if allowed and callable(self.mental_event_sink):
                try:
                    self.mental_event_sink(seed)
                except Exception as exc:
                    self._log("warning", "[associative material] material sink failed: %s", exc)


            if wake and callable(self.wake_callback):
                try:
                    self.wake_callback()
                except Exception:
                    pass

    def _context_from_snapshot(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        raw_self = snapshot.get("self_context") if isinstance(snapshot.get("self_context"), Mapping) else {}
        raw_commentary = (
            snapshot.get("commentary_expectation")
            if isinstance(snapshot.get("commentary_expectation"), Mapping)
            else {}
        )
        # Keep public self material, not the full attention telemetry ticker. The
        # latter changes every few seconds and would make every stocked seed look
        # stale before Surface gets a chance to use it.
        eris_self_memory = {
            "agenda": [dict(item) for item in _sequence(raw_self.get("agenda"))[-6:] if isinstance(item, Mapping)],
            "private_mind_surface": [
                dict(item) for item in _sequence(raw_self.get("private_mind_surface"))[-6:] if isinstance(item, Mapping)
            ],
            "private_life_surface": [
                dict(item) for item in _sequence(raw_self.get("private_life_surface"))[-6:] if isinstance(item, Mapping)
            ],
            # V42: seed worker không được ăn lại mental event do chính seed_worker
            # tạo ra. Có thể vẫn lưu nó trong PrivateLife để audit/continuity,
            # nhưng cùng nguồn không được tự biến output trước thành "sự kiện mới".
            "private_life_workspace": [
                dict(item)
                for item in _sequence(raw_self.get("private_life_workspace"))[-12:]
                if isinstance(item, Mapping)
                and _compact(item.get("origin_source", ""), 80).lower() not in {"seed_worker", "random_chat_seed", "random_chat_seed_reservoir"}
            ],
            "active_concerns": [
                dict(item) for item in _sequence(raw_self.get("active_concerns"))[-8:] if isinstance(item, Mapping)
            ],
            "has_public_seed": bool(raw_self.get("has_public_seed", False)),
            "strongest_seed_score": round(float(raw_self.get("strongest_seed_score", 0.0) or 0.0), 1),
        }
        # V42: seed worker chỉ nhận USER-origin public events. Câu Surface vừa
        # nói không được quay lại làm "nguyên liệu nhận thức", nếu không hệ sẽ tự
        # nuôi chuỗi im-lặng -> khoảng-trống -> maybe -> hiện-diện đến hết đêm.
        recent_user_events = []
        for item in _sequence(snapshot.get("recent_public_history"))[-12:]:
            if not isinstance(item, Mapping):
                continue
            role = _compact(item.get("role", ""), 24).lower()
            if role not in {"user", "human"}:
                continue
            text = _compact(item.get("text", item.get("content", "")), 520)
            if text:
                event_ref = _compact(
                    item.get("turn_id", item.get("event_id", item.get("id", item.get("message_id", "")))),
                    120,
                )
                recent_user_events.append({"role": "user", "text": text, "event_ref": event_ref})
        raw_world_state = snapshot.get("world_state") if isinstance(snapshot.get("world_state"), Mapping) else {}
        raw_seed_context = (
            snapshot.get("seed_context") if isinstance(snapshot.get("seed_context"), Mapping)
            else raw_world_state.get("seed_context", {}) if isinstance(raw_world_state.get("seed_context"), Mapping)
            else {}
        )
        raw_environment = (
            snapshot.get("environment_awareness")
            if isinstance(snapshot.get("environment_awareness"), Mapping)
            else raw_world_state.get("environment_awareness", {})
            if isinstance(raw_world_state.get("environment_awareness"), Mapping)
            else {}
        )
        environment_awareness = {
            "schema": _compact(raw_environment.get("schema", "environment_awareness.v1"), 64),
            "revision": int(raw_environment.get("revision", 0) or 0),
            "captured_at": float(raw_environment.get("captured_at", 0.0) or 0.0),
            "situation_timestamp": float(raw_environment.get("situation_timestamp", 0.0) or 0.0),
            "age_seconds": round(float(raw_environment.get("age_seconds", 9999.0) or 9999.0), 2),
            "fresh": bool(raw_environment.get("fresh", False)),
            "source": _compact(raw_environment.get("source", ""), 64),
            "focused_app": _compact(raw_environment.get("focused_app", ""), 120),
            "focused_process": _compact(raw_environment.get("focused_process", ""), 120),
            "focused_object": _compact(raw_environment.get("focused_object", ""), 240),
            "activity": _compact(raw_environment.get("activity", "unknown"), 180),
            "task_hint": _compact(raw_environment.get("task_hint", "unknown"), 240),
            "phase": _compact(raw_environment.get("phase", "unknown"), 120),
            "last_action": _compact(raw_environment.get("last_action", "none"), 180),
            "last_outcome": _compact(raw_environment.get("last_outcome", "unknown"), 180),
            "attention_state": _compact(raw_environment.get("attention_state", "unknown"), 120),
            "confidence": round(float(raw_environment.get("confidence", 0.0) or 0.0), 3),
            "semantic_delta": bool(raw_environment.get("semantic_delta", False)),
            "wake_recommended": bool(raw_environment.get("wake_recommended", False)),
            "max_attention_level": int(raw_environment.get("max_attention_level", 0) or 0),
            "evidence_refs": [
                _compact(item, 140) for item in _sequence(raw_environment.get("evidence_refs"))[:12] if _compact(item, 140)
            ],
            "recent_events": [
                {
                    "timestamp": float(item.get("timestamp", 0.0) or 0.0),
                    "kind": _compact(item.get("kind", ""), 72),
                    "attention_level": int(item.get("attention_level", 0) or 0),
                    "confidence": round(float(item.get("confidence", 0.0) or 0.0), 3),
                    "importance": round(float(item.get("importance", 0.0) or 0.0), 3),
                    "source": _compact(item.get("source", ""), 72),
                    "app": _compact(item.get("app", ""), 120),
                    "detail": _compact(item.get("detail", ""), 320),
                    "event_key": _compact(item.get("event_key", ""), 140),
                    "evidence_refs": [
                        _compact(ref, 140) for ref in _sequence(item.get("evidence_refs"))[:8] if _compact(ref, 140)
                    ],
                }
                for item in _sequence(raw_environment.get("recent_events"))[-8:]
                if isinstance(item, Mapping)
            ],
            "epistemic_contract": (
                "Structured environment only; may ground app/window/task/activity when fresh/confident; "
                "never use as VLM/pixel evidence."
            ),
        }
        return {
            "user_memory": _compact(snapshot.get("memory_context", ""), 2800),
            "recent_user_events": recent_user_events[-8:],
            "eris_self_memory": eris_self_memory,
            "personality_summary": _compact(snapshot.get("personality_summary", ""), 1000),
            "identity_summary": _compact(snapshot.get("identity_summary", ""), 1000),
            "relationship": snapshot.get("relationship", {}) if isinstance(snapshot.get("relationship"), Mapping) else {},
            "environment_awareness": environment_awareness,
            "world_state": dict(raw_world_state),
            "seed_context": dict(raw_seed_context),
            "shared_semantic_context": (
                snapshot.get("shared_semantic_context", {})
                if isinstance(snapshot.get("shared_semantic_context"), Mapping)
                else {}
            ),
            # V75: trajectory là semantic spine của hội thoại, không phải public
            # wording và cũng không phải evidence current-world. Seed worker dùng
            # nó để phát triển cùng chủ đề thay vì rơi về novelty ngẫu nhiên.
            "conversation_trajectory": (
                snapshot.get("conversation_trajectory", {})
                if isinstance(snapshot.get("conversation_trajectory"), Mapping)
                else {}
            ),
            "commentary_expectation": {
                "enabled": bool(raw_commentary.get("enabled", True)),
                "due": bool(raw_commentary.get("due", False)),
                "required": bool(raw_commentary.get("required", False)),
                "reason": _compact(raw_commentary.get("reason", ""), 80),
                "anchor_kind": _compact(raw_commentary.get("anchor_kind", ""), 80),
                "policy_action": _compact(raw_commentary.get("policy_action", "wait"), 40),
            },
        }

    @staticmethod
    def _fingerprint_projection(request: Mapping[str, Any]) -> dict[str, Any]:
        shared = request.get("shared_semantic_context") if isinstance(request.get("shared_semantic_context"), Mapping) else {}
        packet = shared.get("packet") if isinstance(shared.get("packet"), Mapping) else {}
        self_memory = request.get("eris_self_memory") if isinstance(request.get("eris_self_memory"), Mapping) else {}
        def stable_surface(items: Any) -> list[dict[str, Any]]:
            return [
                {
                    "id": _compact(item.get("id", ""), 80),
                    "kind": _compact(item.get("kind", ""), 80),
                    "summary": _compact(item.get("summary", item.get("topic_seed", "")), 260),
                    "evidence_refs": list(_sequence(item.get("evidence_refs")))[:5],
                }
                for item in _sequence(items)
                if isinstance(item, Mapping)
            ]
        return {
            "user_memory": request.get("user_memory", ""),
            "recent_user_events": request.get("recent_user_events", ()),
            "eris_self_memory": {
                "agenda": stable_surface(self_memory.get("agenda")),
                "private_mind_surface": stable_surface(self_memory.get("private_mind_surface")),
                "private_life_surface": stable_surface(self_memory.get("private_life_surface")),
                "private_life_workspace": [
                    {
                        "id": _compact(item.get("id", ""), 80),
                        "kind": _compact(item.get("kind", "thought"), 48),
                        "content": _compact(item.get("content", ""), 260),
                        "epistemic_mode": _compact(item.get("epistemic_mode", "opinion"), 48),
                        "open_loop": bool(item.get("open_loop", False)),
                    }
                    for item in _sequence(self_memory.get("private_life_workspace"))[:12]
                    if isinstance(item, Mapping)
                ],
                "active_concerns": [
                    {
                        "concern_id": _compact(item.get("concern_id", ""), 80),
                        "subject": _compact(item.get("subject", ""), 260),
                        "state": _compact(item.get("state", ""), 40),
                        "trajectory": _compact(item.get("trajectory", ""), 40),
                        "activation": round(float(item.get("activation", 0.0) or 0.0), 2),
                        "novelty": round(float(item.get("novelty", 0.0) or 0.0), 2),
                        "habituation": round(float(item.get("habituation", 0.0) or 0.0), 2),
                        "mutation_count": int(item.get("mutation_count", 0) or 0),
                    }
                    for item in _sequence(self_memory.get("active_concerns"))[:8]
                    if isinstance(item, Mapping)
                ],
                "has_public_seed": bool(self_memory.get("has_public_seed", False)),
            },
            "personality_summary": request.get("personality_summary", ""),
            "identity_summary": request.get("identity_summary", ""),
            "relationship": request.get("relationship", {}),
            "environment_awareness": {
                "revision": int((request.get("environment_awareness") or {}).get("revision", 0) or 0)
                if isinstance(request.get("environment_awareness"), Mapping) else 0,
                "focused_app": _compact((request.get("environment_awareness") or {}).get("focused_app", ""), 120)
                if isinstance(request.get("environment_awareness"), Mapping) else "",
                "focused_process": _compact((request.get("environment_awareness") or {}).get("focused_process", ""), 120)
                if isinstance(request.get("environment_awareness"), Mapping) else "",
                "focused_object": _compact((request.get("environment_awareness") or {}).get("focused_object", ""), 220)
                if isinstance(request.get("environment_awareness"), Mapping) else "",
                "activity": _compact((request.get("environment_awareness") or {}).get("activity", ""), 180)
                if isinstance(request.get("environment_awareness"), Mapping) else "",
                "task_hint": _compact((request.get("environment_awareness") or {}).get("task_hint", ""), 220)
                if isinstance(request.get("environment_awareness"), Mapping) else "",
                "phase": _compact((request.get("environment_awareness") or {}).get("phase", ""), 120)
                if isinstance(request.get("environment_awareness"), Mapping) else "",
                "semantic_delta": bool((request.get("environment_awareness") or {}).get("semantic_delta", False))
                if isinstance(request.get("environment_awareness"), Mapping) else False,
                "recent_event_keys": [
                    _compact(item.get("event_key", item.get("kind", "")), 140)
                    for item in _sequence((request.get("environment_awareness") or {}).get("recent_events"))[-4:]
                    if isinstance(item, Mapping)
                ] if isinstance(request.get("environment_awareness"), Mapping) else [],
            },
            "seed_context": {
                "world_revision": int((request.get("seed_context") or {}).get("world_revision", 0) or 0)
                if isinstance(request.get("seed_context"), Mapping) else 0,
                "environment_revision": int((request.get("seed_context") or {}).get("environment_revision", 0) or 0)
                if isinstance(request.get("seed_context"), Mapping) else 0,
                "current_activity": _compact((request.get("seed_context") or {}).get("current_activity", ""), 180)
                if isinstance(request.get("seed_context"), Mapping) else "",
                "current_task": _compact((request.get("seed_context") or {}).get("current_task", ""), 220)
                if isinstance(request.get("seed_context"), Mapping) else "",
                "phase": _compact((request.get("seed_context") or {}).get("phase", ""), 120)
                if isinstance(request.get("seed_context"), Mapping) else "",
                "event_key": _compact(((request.get("seed_context") or {}).get("meaningful_change") or {}).get("event_key", ""), 140)
                if isinstance(request.get("seed_context"), Mapping) and isinstance((request.get("seed_context") or {}).get("meaningful_change"), Mapping) else "",
                "semantic_delta": bool(((request.get("seed_context") or {}).get("meaningful_change") or {}).get("semantic_delta", False))
                if isinstance(request.get("seed_context"), Mapping) and isinstance((request.get("seed_context") or {}).get("meaningful_change"), Mapping) else False,
            },
            "conversation_trajectory": {
                "topic_id": _compact((request.get("conversation_trajectory") or {}).get("topic_id", ""), 80)
                if isinstance(request.get("conversation_trajectory"), Mapping) else "",
                "current_focus": _compact((request.get("conversation_trajectory") or {}).get("current_focus", ""), 520)
                if isinstance(request.get("conversation_trajectory"), Mapping) else "",
                "development_mode": _compact((request.get("conversation_trajectory") or {}).get("development_mode", ""), 32)
                if isinstance(request.get("conversation_trajectory"), Mapping) else "",
                "depth": int((request.get("conversation_trajectory") or {}).get("depth", 0) or 0)
                if isinstance(request.get("conversation_trajectory"), Mapping) else 0,
                "revision": int((request.get("conversation_trajectory") or {}).get("revision", 0) or 0)
                if isinstance(request.get("conversation_trajectory"), Mapping) else 0,
                "awaiting_user_answer": bool((request.get("conversation_trajectory") or {}).get("awaiting_user_answer", False))
                if isinstance(request.get("conversation_trajectory"), Mapping) else False,
            },
            "semantic_scene": {
                "scene_id": shared.get("scene_id", packet.get("scene_id", "")),
                "version": shared.get("version", packet.get("version", "")),
                "foreground": packet.get("foreground", shared.get("foreground", "")),
            },
            "commentary_expectation": request.get("commentary_expectation", {}),
        }

    def _eligible_causal_motives_locked(self) -> tuple[Any, ...]:
        provider = self.causal_motive_provider
        if callable(provider):
            # V100 production path: context được truyền chỉ để giữ signature
            # extensible; FocusMaterialAuthorityV100 cố ý không dùng nó để chọn
            # semantic focus. Không có BoundFocus sống => không generation.
            try:
                motives = tuple(provider(context=self._context_request, now=self.clock()) or ())
            except TypeError:
                try:
                    motives = tuple(provider(now=self.clock()) or ())
                except Exception as exc:
                    self._log("debug", "[v100 focus-material] motive provider degraded: %s", exc)
                    motives = ()
            except Exception as exc:
                self._log("debug", "[v100 focus-material] motive provider degraded: %s", exc)
                motives = ()
        else:
            # Compatibility path cho test/tool/host cũ chưa cài V100. Production
            # coordinator luôn truyền provider V100 ở ai_orchestrated/bootstrap.
            motives = derive_causal_motives(self._context_request, now=self.clock())

        eligible = []
        for motive in motives:
            cause_id = str(getattr(motive, "cause_id", "") or "").strip()
            if not cause_id:
                continue
            used = int(self._cause_generation_counts.get(cause_id, 0) or 0)
            if used < max(1, int(getattr(motive, "generation_budget", 1) or 1)):
                eligible.append(motive)
        return tuple(eligible)

    def _select_causal_motive_locked(self) -> Any | None:
        eligible = self._eligible_causal_motives_locked()
        if not eligible:
            return None
        # Strength là causal salience. Count penalty chỉ chia cơ hội giữa nhiều
        # cause đang sống; nó không được biến quota thành nguyên nhân sinh thought.
        return max(
            eligible,
            key=lambda item: (
                float(item.strength) - 0.16 * self._cause_generation_counts.get(item.cause_id, 0),
                item.cause_kind == "user_event",
            ),
        )

    def _requested_material_kind_locked(self, motive: Any) -> str:
        allowed = tuple(kind for kind in tuple(getattr(motive, "allowed_material_kinds", ()) or ()) if kind in MATERIAL_KINDS)
        if not allowed:
            return "observation"
        counts = self._cause_material_counts.get(str(motive.cause_id), {})
        # Diversity chỉ là tie-breaker SAU khi motive/cause đã tồn tại. Question
        # không có bonus đặc biệt; reaction/opinion/observation giúp tránh cái bệnh
        # biến mọi context thành seminar triết học.
        base = {
            "reaction": 1.00, "opinion": 0.96, "observation": 0.93,
            "association": 0.90, "small_contact": 0.86, "playful_impulse": 0.84,
            "hunch": 0.82, "question": 0.80, "memory_callback": 0.92,
            "unfinished_thought": 0.88, "desire": 0.84, "plan": 0.82,
            "self_reflection": 0.78, "story_fragment": 0.87,
        }
        return max(
            allowed,
            key=lambda kind: (base.get(kind, 0.80) - 0.55 * int(counts.get(kind, 0) or 0), base.get(kind, 0.80)),
        )

    def _generation_request_locked(self) -> dict[str, Any]:
        learning = (
            self.seed_store.selection_context(now=self.clock())
            if self.seed_store is not None
            else {
                "adaptive_daring": 0.46,
                "daring_ceiling": 0.56,
                "user_affinities": [],
                "eris_interests": [],
                "recent_seeds": [],
                "exploration_mix": {
                    "shared": 0.42, "user_led": 0.23, "eris_led": 0.25, "experimental": 0.10,
                },
            }
        )
        motive = self._select_causal_motive_locked()
        requested_mode = self._requested_mode_locked(learning)
        requested_material_kind = self._requested_material_kind_locked(motive) if motive is not None else "observation"
        queued = [
            {
                "seed_id": entry.seed.seed_id,
                "material_id": entry.seed.material_id,
                "material_kind": entry.seed.material_kind,
                "premise": entry.seed.premise,
                "topic_tags": list(entry.seed.topic_tags),
                "cause_id": entry.seed.cause_id,
                "cause_kind": entry.seed.cause_kind,
            }
            for entry in self._entries
        ]
        causal_payload = motive.to_dict() if motive is not None else {}
        return {
            **dict(self._context_request),
            "generation_temporal_context": build_seed_temporal_context(self.clock()),
            "material_authority": {
                "mode": "bound_focus_v100" if callable(self.causal_motive_provider) else "legacy_context_scan",
                "context_scan_can_choose_topic": not callable(self.causal_motive_provider),
                "rule": (
                    "Production V100 material must be bound to a pre-existing evidence-backed focus object; "
                    "the worker may shape material but may not choose a new topic."
                ),
            },
            # Learning còn được đưa vào để tránh lặp thứ user ghét/đã chán, nhưng
            # không được dùng để model tự phát minh motive. CausalMotive đã chốt.
            "seed_learning": learning,
            "causal_motive": causal_payload,
            "reservoir_context": {
                "requested_mode": requested_mode,
                "requested_material_kind": requested_material_kind,
                # Deprecated compatibility labels. V45 không dùng chúng làm
                # authority generation; giữ để diagnostic/tests cũ không vỡ API.
                "requested_event_family": "material",
                "requested_event_kind": requested_material_kind,
                "target_size": self.target_size,
                "ready_size": len(self._entries),
                "queued_materials": queued,
                "context_revision": self._context_revision,
                "rule": (
                    "Materialize one mental object from causal_motive.semantic_focus. "
                    "Do not invent a new motive or conversation plan."
                ),
            },
            "seed_contract": {
                "authority": "associative_material_only",
                "motive_owner": "runtime",
                "surface_owns_wording": True,
                "conversation_planning_forbidden": True,
                "requested_mode": requested_mode,
                "requested_material_kind": requested_material_kind,
                "allowed_material_kinds": list(getattr(motive, "allowed_material_kinds", ()) or ()),
                "kind_contract": "Material kind must match the pre-existing causal motive; do not manufacture motive to satisfy diversity.",
                "quality_rule": "Quality is not length-shaped; compact context-specific material beats generic reflective prose.",
                "material_rule": (
                    "Use requested_material_kind only if it is a truthful shape for this causal motive. "
                    "Otherwise choose another allowed material kind or decision=none."
                ),
                "question_rule": (
                    "A question needs a concrete gap and non-empty expected_update. "
                    "Do not ask merely to prolong conversation."
                ),
                "development_rule": (
                    "When causal_motive.development_required is true, material must change the idea, not merely reword it. "
                    "Choose one allowed_development_mode; otherwise return decision=none."
                ),
                "temporal_rule": (
                    "generation_temporal_context proves clock/calendar only; it never grounds weather, lighting, sound, "
                    "user activity, emotion, or fatigue."
                ),
                "environment_rule": (
                    "environment_awareness is grounded structured local state shared with chat/self-turn. Only an environment_event "
                    "causal motive may use it as current-world activity/task evidence. It is never pixel/VLM evidence."
                ),
            },
        }

    def _requested_mode_locked(self, learning: Mapping[str, Any]) -> str:
        mix = learning.get("exploration_mix") if isinstance(learning.get("exploration_mix"), Mapping) else {}
        modes = ("shared", "user_led", "eris_led", "experimental")
        counts = {mode: sum(1 for entry in self._entries if entry.seed.mode == mode) for mode in modes}
        weights = {
            "shared": float(mix.get("shared", 0.42) or 0.42),
            "user_led": float(mix.get("user_led", 0.23) or 0.23),
            "eris_led": float(mix.get("eris_led", 0.25) or 0.25),
            "experimental": float(mix.get("experimental", 0.10) or 0.10),
        }
        # Largest quota deficit wins. This keeps a stock with ego and exploration,
        # rather than four near-identical user-pleasing seeds wearing different IDs.
        return max(
            modes,
            key=lambda mode: (weights[mode] * self.target_size - counts[mode], weights[mode]),
        )

    def _requested_event_kind_locked(self) -> str:
        """Request diversity by mental-event family, then by kind within it.

        V32 had a flat partial mix which omitted several legal kinds entirely and
        treated thought/opinion/self-reflection as diversity even when they all
        realized as the same reflective voice.  The two-stage deficit scheduler
        makes the reservoir diversify by cognitive function first.
        """

        population = [entry.seed for entry in self._entries]
        population.extend(leased.entry.seed for leased in self._leases.values())

        family_counts = {
            family: sum(1 for seed in population if seed_event_family(seed.event_kind) == family)
            for family in _EVENT_FAMILY_MIX
        }
        family = max(
            _EVENT_FAMILY_MIX,
            key=lambda name: (
                _EVENT_FAMILY_MIX[name] * self.target_size - family_counts[name],
                _EVENT_FAMILY_MIX[name],
            ),
        )

        kind_mix = _EVENT_KIND_MIX[family]
        kind_counts = {
            kind: sum(1 for seed in population if seed.event_kind == kind)
            for kind in kind_mix
        }
        family_target = max(1.0, _EVENT_FAMILY_MIX[family] * self.target_size)
        return max(
            kind_mix,
            key=lambda kind: (
                kind_mix[kind] * family_target - kind_counts[kind],
                kind_mix[kind],
            ),
        )

    def _validate_generated_locked(self, seed: RandomChatSeed, now: float) -> tuple[bool, str]:
        if not seed.available:
            return False, "seed_incomplete"
        quality = assess_seed_quality(seed)
        if quality.score < self.min_quality:
            labels = ",".join(quality.reasons[:3]) or "semantic_thinness"
            return False, f"seed_quality_low:{quality.score:.2f}:{labels}"
        if self.seed_store is not None:
            allowed, reason = self.seed_store.validate_seed(seed, now=now)
            if not allowed:
                return False, reason
        elif seed.generic or seed.intrusion_risk > 0.46 or seed.daring > 0.86:
            return False, "seed_policy_rejected"

        # --- Per-cause_id reservoir cap ---
        # Một concern đơn lẻ không được chiếm quá 2 slot trong kho; nếu không
        # seed worker sẽ cứ nảy ra céc biến thể của cùng 1 chủ đề triết học mãi.
        _MAX_SLOTS_PER_CAUSE = 2
        if seed.material_only and seed.cause_id:
            cause_slots = sum(
                1 for e in self._entries if e.seed.cause_id == seed.cause_id
            )
            if cause_slots >= _MAX_SLOTS_PER_CAUSE:
                return False, "reservoir_cause_slots_full"

        for entry in self._entries:
            if _similarity(seed.premise, entry.seed.premise) >= 0.68:
                return False, "reservoir_duplicate"
            # --- Broader topic-tag overlap using Jaccard (replaces exact-match) ---
            # Exact-match cũ bỏ sót trường hợp 2 seed cùng chủ đề nhưng khác 1 tag nhỏ;
            # Jaccard >= 0.50 bắt được đa số trường hợp tất cả các tag core giống nhau.
            new_tags = set(seed.topic_tags)
            old_tags = set(entry.seed.topic_tags)
            if new_tags and old_tags:
                intersection = len(new_tags & old_tags)
                union = len(new_tags | old_tags)
                tag_jaccard = intersection / max(1, union)
                if tag_jaccard >= 0.50:
                    # Chỉ block nếu material_kind cũng giống (giữ văn bản cũ: cùng cause
                    # vẫn có thể sinh reaction và opinion cho cùng topic).
                    if not seed.material_only or seed.material_kind == entry.seed.material_kind:
                        return False, "reservoir_topic_overlap"
        for leased in self._leases.values():
            if _similarity(seed.premise, leased.entry.seed.premise) >= 0.68:
                return False, "leased_duplicate"
        return True, "allowed"

    def _ready_to_generate_locked(self, now: float) -> bool:
        if not (self.enabled and self._context_request and now >= self._next_generate_at):
            return False
        if not bool(getattr(self.generator, "causal_material_generation", False)):
            return len(self._entries) + len(self._leases) < self.target_size
        # V45 không dùng "reservoir còn slot" làm nguyên nhân generation nữa.
        # Chỉ khi còn một causal source chưa dùng hết budget mới gọi model.
        return bool(self._eligible_causal_motives_locked())

    def _wait_seconds_locked(self, now: float) -> float:
        if self._stop:
            return 0.05
        if not self._context_request:
            return 15.0
        if not bool(getattr(self.generator, "causal_material_generation", False)):
            if len(self._entries) + len(self._leases) >= self.target_size:
                return 15.0
        elif not self._eligible_causal_motives_locked():
            return 15.0
        return max(0.25, min(15.0, self._next_generate_at - now))

    def _entry_compatible_locked(self, entry: _SeedEntry) -> bool:
        drift = max(0, self._context_revision - entry.context_revision)
        if entry.seed.source_kind == "recent_shared_context":
            return entry.user_epoch == self._user_epoch and drift <= 2
        if entry.seed.source_kind == "current_environment":
            # Environment material is intentionally short-lived. A couple of
            # harmless context revisions are tolerated, but app/task drift or a
            # new user turn retires it quickly.
            return entry.user_epoch == self._user_epoch and drift <= 2
        if entry.seed.source_kind == "weak_association":
            return entry.user_epoch == self._user_epoch and drift <= 3
        if entry.seed.source_kind == "user_memory":
            return drift <= 8
        if entry.seed.source_kind in {"eris_self_memory", "private_life_workspace"}:
            return drift <= 10
        if entry.seed.source_kind in {
            "endogenous_curiosity", "general_knowledge", "world_hypothesis",
            "discovery_impulse", "play_impulse", "question_impulse",
        }:
            return drift <= 12
        return False

    def _score_entry(self, entry: _SeedEntry, now: float) -> float:
        seed = entry.seed
        lifetime = max(1.0, entry.expires_at - entry.generated_at)
        freshness = max(0.0, min(1.0, (entry.expires_at - now) / lifetime))
        mode_novelty = 0.06 if seed.mode != self._last_claimed_mode else 0.0
        last_family = seed_event_family(self._last_claimed_event_kind) if self._last_claimed_event_kind else ""
        family_novelty = 0.09 if seed_event_family(seed.event_kind) != last_family else 0.0
        event_novelty = 0.035 if seed.event_kind != self._last_claimed_event_kind else 0.0
        open_loop_value = 0.035 if seed.open_loop and seed.event_kind in {"knowledge_gap", "discovery_impulse", "unfinished_loop"} else 0.0
        # Penalty nếu nhiều seed cùng cause_id đang nằm trong kho: ưu tiên seed từ
        # cause mới/khác lên đầu danh sách peek. Score nhân toàn bộ loạt cause-peers
        # thường khi cause chiếm nhiều slot; không chỉ cộng hằng số.
        cause_peers = (
            sum(1 for e in self._entries if e.seed.cause_id == seed.cause_id and seed.cause_id)
            if seed.material_only and seed.cause_id
            else 0
        )
        cause_concentration_penalty = 0.20 * max(0, cause_peers - 1)
        quality = assess_seed_quality(seed)
        return (
            0.22 * seed.estimated_fit
            + 0.19 * seed.personal_stake
            + 0.14 * seed.authenticity
            + 0.10 * seed.confidence
            + 0.07 * seed.daring
            + 0.08 * freshness
            + 0.14 * quality.score
            + mode_novelty
            + family_novelty
            + event_novelty
            + open_loop_value
            - cause_concentration_penalty
        )

    def _prune_locked(self, now: float) -> None:
        self._entries = [
            entry
            for entry in self._entries
            if entry.expires_at > now and self._entry_compatible_locked(entry)
        ]
        expired_tokens = [
            token for token, leased in self._leases.items()
            if leased.lease_expires_at <= now
        ]
        for token in expired_tokens:
            leased = self._leases.pop(token)
            entry = leased.entry
            if entry.expires_at > now and self._entry_compatible_locked(entry):
                self._entries.append(entry)
        if len(self._entries) > self.max_size:
            self._entries = sorted(
                self._entries,
                key=lambda entry: self._score_entry(entry, now),
                reverse=True,
            )[: self.max_size]

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


__all__ = [
    "RANDOM_CHAT_SEED_OUTPUT_SCHEMA",
    "RandomChatSeedGenerator",
    "build_seed_temporal_context",
    "RandomChatSeedReservoir",
    "SeedReservoirPreview",
    "SeedReservoirLease",
]

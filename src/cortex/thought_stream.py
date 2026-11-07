from core.di import resolve_dependency
import os
import re
import threading
import time
import unicodedata
from collections.abc import Callable

from .contracts import (
    CognitiveDisturbance,
    CognitiveMomentum,
    ThoughtObject,
    ThoughtStreamReport,
    ThoughtSurface,
    UnresolvedTensionLoop,
)
from .internal_state import PersistentInternalState
'''
giữ các ý nghĩ chưa hoàn tất, cho chúng decay, tái kích hoạt, gom thành loop,
tính momentum, rồi thỉnh thoảng cho một mẩu nổi lên thành ThoughtSurface.'''

def _clamp01(value) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric != numeric:
        return 0.0
    return max(0.0, min(1.0, numeric))

# rút gọn chuỗi, gộp khoảng trắng, cắt độ dài nếu quá dài
def _clean_text(value: object, *, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..." # nếu quá dài thì cắt và thêm dấu "..."
    return text


def _compact_seed(value: object) -> str:
    text = _clean_text(value, limit=80).lower() # chuẩn hoá chuỗi, cắt bớt độ dài để làm seed, loại bỏ các ký tự không cần thiết
    text = re.sub(r"^(semantic/user|relationship/[^:]+|episodic|working):\s*", "", text) # loại bỏ tiền tố phổ biến để tập trung vào phần cốt lõi của ý tưởng
    text = re.sub(r"[^\w\s-]", " ", text)
    words = [word for word in text.split() if len(word) >= 2]
    return "_".join(words[:5])[:64] or "open_association" # tạo seed ngắn gọn, chỉ giữ lại những từ quan trọng nhất, giới hạn độ dài để tránh quá tải


def _normalize_ascii(value: object) -> str:
    text = str(value or "").strip().lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text)


def _semantic_tokens(value: object) -> set[str]:
    text = _normalize_ascii(value).replace("_", " ")
    return {
        token
        for token in re.findall(r"[a-z0-9_]+", text)
        if len(token) >= 3 and token not in {"the", "and", "for", "from", "this", "that", "with"}
    }


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on", "y"}:
        return True
    if raw in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        return float(default)
    if value != value:
        return float(default)
    return value


def _to_dict(value: object) -> dict[str, object]:
    method = getattr(value, "to_dict", None)
    if callable(method):
        try:
            payload = method()
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    return {}


class FragmentStore:
    """Keeps unfinished thoughts alive even when nothing is spoken."""

    def __init__(self, *, limit: int = 24):
        self.limit = max(4, int(limit))
        self._items: dict[str, ThoughtObject] = {}

    #ý nghĩ cũ yếu dần theo thời gian
    def decay(self, *, now: float, decay_rate: float = 0.94) -> None:
        kept: dict[str, ThoughtObject] = {}
        for key, thought in self._items.items():
            age_minutes = max(0.0, (now - (thought.updated_at or thought.created_at or now)) / 60.0)
            decay = decay_rate ** min(60.0, age_minutes)
            # ý nghĩ cũ không biến mất ngay
            # nhưng giảm lực dần theo thời gian
            tension = _clamp01(thought.tension * decay)
            persistence = _clamp01(thought.persistence * max(0.86, decay))
            # quá yếu thì xoá khỏi store
            if tension < 0.08 and persistence < 0.08 and thought.surfaced_count > 0:
                continue
            kept[key] = ThoughtObject(
                thought_id=thought.thought_id,
                mode=thought.mode, # giữ nguyên mode, vì nó phản ánh cách ý nghĩ này được kích hoạt và có thể quan trọng để hiểu tại sao nó tồn tại
                topic_seed=thought.topic_seed,
                partial_text=thought.partial_text,# giữ nguyên partial text dù ý nghĩ có thể mờ dần, vì nó phản ánh phần ý tưởng đã hình thành
                prediction_key=thought.prediction_key, # giữ nguyên key để có thể tiếp tục cập nhật nếu ý nghĩ này được kích hoạt lại, dù nó có thể mờ dần
                unresolved_reason=thought.unresolved_reason,# giữ nguyên lý do chưa giải quyết, vì nó có thể vẫn còn quan trọng dù ý nghĩ có thể mờ dần
                source_memory=thought.source_memory,  # giữ nguyên nguồn gốc, vì nó có thể quan trọng để hiểu tại sao ý nghĩ này tồn tại
                associations=thought.associations, # giữ nguyên liên kết dù ý nghĩ có thể mờ dần
                tension=tension, # giảm dần theo thời gian nếu không được kích hoạt lại
                novelty=thought.novelty, # giữ nguyên độ mới, vì nó phản ánh sự khác biệt so với trải nghiệm gần đây
                persistence=persistence,
                recurrence_count=thought.recurrence_count,
                surfaced_count=thought.surfaced_count,
                resolved=thought.resolved, # giữ nguyên trạng thái đã giải quyết, vì nó phản ánh liệu ý nghĩ này đã từng được bộc lộ ra ngoài hay chưa
                evidence=thought.evidence, # giữ nguyên bằng chứng, vì nó phản ánh lý do tại sao ý nghĩ này tồn tại và có thể vẫn còn quan trọng dù ý nghĩ có thể mờ dần
                risk_flags=thought.risk_flags, # giữ nguyên cờ rủi ro, vì nó phản ánh các yếu tố có thể khiến ý nghĩ này nguy hiểm hoặc không mong muốn, dù nó có thể mờ dần
                created_at=thought.created_at,
                updated_at=thought.updated_at,
                last_reactivated_at=thought.last_reactivated_at,
            )
        self._items = dict(sorted(kept.items(), key=lambda item: item[1].updated_at, reverse=True)[: self.limit])

    #thêm mới hoặc cập nhật thought cũ cùng prediction_key
    def upsert(self, thought: ThoughtObject, *, now: float) -> ThoughtObject:
        key = thought.prediction_key or thought.topic_seed or thought.thought_id
        previous = self._items.get(key)
        if previous is None:
            stored = ThoughtObject(
                thought_id=thought.thought_id or f"thought_{abs(hash(key)) % 1000000}",
                mode=thought.mode,
                topic_seed=thought.topic_seed,
                partial_text=thought.partial_text,
                prediction_key=key,
                unresolved_reason=thought.unresolved_reason,
                source_memory=thought.source_memory,
                associations=thought.associations,
                tension=thought.tension,
                novelty=thought.novelty,
                persistence=max(0.30, thought.persistence),
                recurrence_count=0,
                surfaced_count=0,
                resolved=False,
                evidence=thought.evidence,
                risk_flags=thought.risk_flags,
                created_at=thought.created_at or now,
                updated_at=now,
                last_reactivated_at=now,
            )
        else: # nếu đã có ý nghĩ với cùng key, thì cập nhật nó thay vì tạo mới, để duy trì sự liên tục của ý nghĩ đó qua thời gian
            stored = ThoughtObject(
                thought_id=previous.thought_id,
                mode=thought.mode or previous.mode,
                topic_seed=thought.topic_seed or previous.topic_seed,
                partial_text=self._mutate_partial(previous.partial_text, thought.partial_text),
                prediction_key=key,
                unresolved_reason=thought.unresolved_reason or previous.unresolved_reason,
                source_memory=thought.source_memory or previous.source_memory,
                associations=tuple(dict.fromkeys((*previous.associations, *thought.associations)))[-8:],
                tension=max(previous.tension, thought.tension),
                novelty=min(1.0, max(thought.novelty * 0.86, 0.38)),
                persistence=_clamp01(max(previous.persistence, thought.persistence) + 0.08),
                recurrence_count=previous.recurrence_count + 1,
                surfaced_count=previous.surfaced_count,
                resolved=False,
                evidence=tuple(dict.fromkeys((*previous.evidence, *thought.evidence)))[-8:],
                risk_flags=tuple(dict.fromkeys((*previous.risk_flags, *thought.risk_flags))),
                created_at=previous.created_at,
                updated_at=now,
                last_reactivated_at=now,
            )
        self._items[key] = stored
        return stored

    #khi thought đã lộ ra ngoài thì giảm tension/novelty/persistence để phản ánh rằng
    # nó đã được bộc lộ và có thể không còn mạnh mẽ như khi nó mới xuất hiện,
    # đồng thời tăng surfaced_count để theo dõi số lần nó đã lộ ra, điều này có thể
    #  ảnh hưởng đến cách nó được xử lý trong tương lai (ví dụ: nếu một thought đã
    # lộ ra nhiều lần nhưng vẫn chưa được giải quyết, có thể cần phải chú ý hơn đến nó)
    def mark_surfaced(self, thought_id: str) -> None: #giảm lực của thought sau khi nó đã lộ ra.
        for key, thought in list(self._items.items()):
            if thought.thought_id != thought_id:
                continue
            self._items[key] = ThoughtObject(
                thought_id=thought.thought_id,
                mode=thought.mode,
                topic_seed=thought.topic_seed,
                partial_text=thought.partial_text,
                prediction_key=thought.prediction_key,
                unresolved_reason=thought.unresolved_reason,
                source_memory=thought.source_memory,
                associations=thought.associations,
                tension=_clamp01(thought.tension * 0.82),
                novelty=_clamp01(thought.novelty * 0.88),
                persistence=_clamp01(thought.persistence * 0.94),
                recurrence_count=thought.recurrence_count,
                surfaced_count=thought.surfaced_count + 1,
                resolved=False,
                evidence=thought.evidence,
                risk_flags=thought.risk_flags,
                created_at=thought.created_at,
                updated_at=thought.updated_at,
                last_reactivated_at=thought.last_reactivated_at,
            )
            return

    def replace(self, thought: ThoughtObject) -> None:
        key = thought.prediction_key or thought.topic_seed or thought.thought_id
        if key:
            self._items[key] = thought

    def get(self, thought_id: str) -> ThoughtObject | None:
        for thought in self._items.values():
            if thought.thought_id == thought_id:
                return thought
        return None

    #đóng thought khi có tín hiệu “xong"
    def mark_resolved(self, thought_id: str, *, now: float, evidence: str = "closure_signal") -> bool:
        for key, thought in list(self._items.items()):
            if thought.thought_id != thought_id:
                continue
            self._items[key] = ThoughtObject(
                thought_id=thought.thought_id,
                mode=thought.mode,
                topic_seed=thought.topic_seed,
                partial_text=thought.partial_text,
                prediction_key=thought.prediction_key,
                unresolved_reason="",
                source_memory=thought.source_memory,
                associations=thought.associations,
                tension=_clamp01(thought.tension * 0.18),
                novelty=thought.novelty,
                persistence=_clamp01(thought.persistence * 0.24),
                recurrence_count=thought.recurrence_count,
                surfaced_count=thought.surfaced_count,
                resolved=True,
                evidence=tuple(dict.fromkeys((*thought.evidence, evidence)))[-8:],
                risk_flags=thought.risk_flags,
                created_at=thought.created_at,
                updated_at=now,
                last_reactivated_at=thought.last_reactivated_at,
            )
            return True
        return False

    #tự đóng thought nếu closure signal khớp token
    def close_matching(self, closure_signals: tuple[str, ...], *, now: float) -> tuple[str, ...]:
        closed: list[str] = []
        signals = tuple(str(item or "") for item in closure_signals if str(item or "").strip())
        if not signals:
            return ()
        for thought in tuple(self._items.values()):
            if thought.resolved:
                continue
            if any(self._signal_closes_thought(signal, thought) for signal in signals):
                if self.mark_resolved(thought.thought_id, now=now):
                    closed.append(thought.thought_id)
        return tuple(closed)

    #lấy các thought chưa giải quyết, sort theo độ căng + persistence
    def unresolved(self) -> tuple[ThoughtObject, ...]:
        return tuple(
            sorted(
                (item for item in self._items.values() if not item.resolved),
                key=lambda item: (item.tension + item.persistence, item.updated_at),
                reverse=True,
            )
        )

    def recent(self, *, limit: int = 8) -> tuple[ThoughtObject, ...]:
        return tuple(sorted(self._items.values(), key=lambda item: item.updated_at, reverse=True)[:limit])

    def _mutate_partial(self, previous: str, incoming: str) -> str:
        if not previous:
            return incoming
        if not incoming or incoming == previous:
            return previous
        return f"{previous} -> {incoming}"[:180]

    def _signal_closes_thought(self, signal: str, thought: ThoughtObject) -> bool:
        normalized = _normalize_ascii(signal)
        closure_terms = (
            "closed",
            "closure",
            "complete",
            "completed",
            "done",
            "fixed",
            "resolved",
            "solved",
            "xong",
            "da xong",
            "giai quyet",
            "dong lai",
        )
        if not any(term in normalized for term in closure_terms):
            return False
        if f"close_thought:{_normalize_ascii(thought.thought_id)}" in normalized:
            return True
        signal_tokens = _semantic_tokens(signal)
        thought_tokens = _semantic_tokens(
            " ".join(
                (
                    thought.topic_seed,
                    thought.prediction_key,
                    thought.partial_text,
                    thought.source_memory,
                    " ".join(thought.associations),
                )
            )
        )
        return bool(signal_tokens and thought_tokens and signal_tokens & thought_tokens)


#Lưu liên kết giữa thought hiện tại và các anchor từ ngữ cảnh.
#Ví dụ thought liên quan guard_raw_final, anchor có code, runtime, user_text, thì nó tạo cạnh liên tưởng.
#  Sau này các thought cùng vùng nghĩa dễ quay lại cùng nhau.
class AssociationGraph:
    """Tracks which unresolved thoughts tend to re-enter together."""

    def __init__(self):
        self._edges: dict[str, set[str]] = {}

    def connect(self, thought: ThoughtObject, anchors: tuple[str, ...]) -> tuple[str, ...]:
        key = thought.prediction_key or thought.topic_seed
        cleaned = tuple(_compact_seed(anchor) for anchor in anchors if _clean_text(anchor))
        if not key:
            return cleaned[:4]
        bucket = self._edges.setdefault(key, set())
        for anchor in cleaned:
            if anchor and anchor != key:
                bucket.add(anchor)
        return tuple(sorted(bucket))[:6]


#Đây là bộ sinh thought ứng viên mới.
#Đây là tầng “có chuyện gì đang cố thành ý nghĩ”. Chưa nói ra, chỉ tạo mầm.
class PredictionTracker:
    """Turns current telemetry/internal state into unfinished predictions."""

    MODES = (
        "input_disturbance",
        "memory_replay",
        "associative_drift",
        "attention_drift",
        "quiet_self_muse",
    )

    def propose(
        self,
        *,
        snapshot,
        state: PersistentInternalState,
        long_term_memory: tuple[str, ...],
        last_text: str,
        recent_thought_seeds: tuple[str, ...],
        recent_subconscious_seeds: tuple[str, ...],
        disturbances: tuple[CognitiveDisturbance, ...] = (),
        now: float,
    ) -> ThoughtObject:
        idle_seconds = max(0.0, float(getattr(snapshot, "idle_seconds", 0.0) or 0.0))
        focus_kind = str(getattr(snapshot, "active_window_kind", "") or "")
        user_signals = getattr(snapshot, "user_signals", None)
        focus_pattern = str(getattr(user_signals, "focus_pattern", "") or "")
        memories = tuple(_clean_text(item, limit=160) for item in (long_term_memory or ()) if _clean_text(item))
        mode = self._choose_mode(
            state=state,
            idle_seconds=idle_seconds,
            focus_kind=focus_kind,
            focus_pattern=focus_pattern,
            memories=memories,
            disturbances=disturbances,
        )
        source_memory = self._choose_memory(
            mode=mode,
            memories=memories,
            recent_seeds=(*recent_thought_seeds, *recent_subconscious_seeds),
        )
        topic_seed = self._topic_seed(
            mode=mode,
            source_memory=source_memory,
            focus_kind=focus_kind,
            focus_pattern=focus_pattern,
            last_text=last_text,
            disturbances=disturbances,
        )
        prediction_key = topic_seed
        disturbance_intensity = self._disturbance_intensity(disturbances)
        return ThoughtObject(
            thought_id=f"thought_{abs(hash(prediction_key)) % 1000000}",
            mode=mode,
            topic_seed=topic_seed,
            partial_text=self._partial_text(mode=mode, topic_seed=topic_seed),
            prediction_key=prediction_key,
            unresolved_reason=self._unresolved_reason(mode),
            source_memory=source_memory,
            associations=(),
            tension=0.0 if mode != "input_disturbance" else disturbance_intensity * 0.22,
            novelty=max(
                self._novelty(
                    topic_seed,
                    recent_thought_seeds=recent_thought_seeds,
                    recent_subconscious_seeds=recent_subconscious_seeds,
                ),
                0.64 if mode == "input_disturbance" else 0.0,
            ),
            persistence=max(0.36, 0.42 + disturbance_intensity * 0.18) if mode == "input_disturbance" else 0.36,
            evidence=self._evidence(
                mode=mode,
                idle_seconds=idle_seconds,
                focus_pattern=focus_pattern,
                source_memory=source_memory,
                disturbances=disturbances,
            ),
            risk_flags=("no_screen_claim", "anti_mindreading", "thought_stream_surface", "needs_generation"),
            created_at=now,
            updated_at=now,
            last_reactivated_at=now,
        )

    def _choose_mode(
        self,
        *,
        state: PersistentInternalState,
        idle_seconds: float,
        focus_kind: str,
        focus_pattern: str,
        memories: tuple[str, ...],
        disturbances: tuple[CognitiveDisturbance, ...] = (),
    ) -> str:
        if self._has_direct_disturbance(disturbances):
            return "input_disturbance"
        scores = {
            "memory_replay": self._pressure(state, "memory_replay_pressure") + (0.14 if memories else 0.0),
            "associative_drift": self._pressure(state, "association_pressure") + self._pressure(state, "curiosity_pressure") * 0.24,
            "attention_drift": self._pressure(state, "subconscious_pressure") + (0.12 if focus_pattern in {"absent", "drifting", "passive_consumption"} else 0.0),
            "quiet_self_muse": self._pressure(state, "silence_pressure") + (0.16 if idle_seconds >= 420 else 0.0),
        }
        if focus_kind in {"coding", "documentation"}:
            scores["associative_drift"] += 0.06
        return max(self.MODES, key=lambda item: scores.get(item, 0.0))

    def _choose_memory(
        self,
        *,
        mode: str,
        memories: tuple[str, ...],
        recent_seeds: tuple[str, ...],
    ) -> str:
        if mode != "memory_replay" or not memories:
            return ""
        recent = set(str(item) for item in recent_seeds[-8:])
        for item in reversed(memories[-8:]):
            if _compact_seed(item) not in recent:
                return item
        return memories[-1]

    def _topic_seed(
        self,
        *,
        mode: str,
        source_memory: str,
        focus_kind: str,
        focus_pattern: str,
        last_text: str,
        disturbances: tuple[CognitiveDisturbance, ...] = (),
    ) -> str:
        if source_memory:
            return f"memory_replay:{_compact_seed(source_memory)}"
        if mode == "input_disturbance":
            disturbance = self._dominant_disturbance(disturbances)
            channel = _clean_text(getattr(disturbance, "channel", ""), limit=32)
            kind = _clean_text(getattr(disturbance, "kind", ""), limit=48)
            content = _clean_text(getattr(disturbance, "content", ""), limit=96)
            detail = _compact_seed(" ".join(part for part in (channel, kind, content) if part))
            return f"input_disturbance:{detail}"
        if mode == "quiet_self_muse":
            return "quiet_self_muse:present_room"
        if mode == "attention_drift":
            detail = _compact_seed(focus_pattern or focus_kind or "attention_drift")
            return f"attention_drift:{detail}"
        if mode == "associative_drift":
            detail = _compact_seed(last_text or focus_pattern or focus_kind or "small_curiosity")
            return f"associative_drift:{detail}"
        return f"{mode}:open_association"

    def _partial_text(self, *, mode: str, topic_seed: str) -> str:
        if mode == "input_disturbance":
            return f"input spike is being integrated into the ongoing stream: {topic_seed}"
        if mode == "memory_replay":
            return f"memory returned but has not connected yet: {topic_seed}"
        if mode == "associative_drift":
            return f"association is still trying to link: {topic_seed}"
        if mode == "attention_drift":
            return f"attention changed shape before the prediction resolved: {topic_seed}"
        return "presence continuity is still unresolved between states"

    def _unresolved_reason(self, mode: str) -> str:
        mapping = {
            "input_disturbance": "input spike has not been integrated into the existing cognition stream",
            "memory_replay": "prediction from memory has not been resolved against the current state",
            "associative_drift": "association has not found a stable connection yet",
            "attention_drift": "attention changed before a clean prediction closed",
            "quiet_self_muse": "presence continuity is unfinished, not speech-driven",
        }
        return mapping.get(mode, "unfinished cognition loop")

    def _novelty(
        self,
        topic_seed: str,
        *,
        recent_thought_seeds: tuple[str, ...],
        recent_subconscious_seeds: tuple[str, ...],
    ) -> float:
        recent = tuple(str(item) for item in (*recent_thought_seeds[-6:], *recent_subconscious_seeds[-6:]) if item)
        if topic_seed in recent:
            return 0.18
        detail = topic_seed.split(":", 1)[-1]
        if detail and any(detail in item for item in recent):
            return 0.42
        return 0.82

    def _evidence(
        self,
        *,
        mode: str,
        idle_seconds: float,
        focus_pattern: str,
        source_memory: str,
        disturbances: tuple[CognitiveDisturbance, ...] = (),
    ) -> tuple[str, ...]:
        evidence = [f"mode={mode}", f"idle={int(idle_seconds)}s"]
        if focus_pattern:
            evidence.append(f"focus_pattern={focus_pattern}")
        if source_memory:
            evidence.append("memory_replay_available")
        for disturbance in tuple(disturbances or ())[:3]:
            channel = _clean_text(getattr(disturbance, "channel", ""), limit=32)
            kind = _clean_text(getattr(disturbance, "kind", ""), limit=48)
            intensity = _clamp01(getattr(disturbance, "intensity", 0.0))
            if channel or kind:
                evidence.append(f"disturbance={channel}:{kind}:{intensity:.2f}")
        return tuple(evidence[:6])

    def _dominant_disturbance(self, disturbances: tuple[CognitiveDisturbance, ...]) -> CognitiveDisturbance | None:
        items = tuple(disturbances or ())
        if not items:
            return None
        return max(items, key=lambda item: _clamp01(getattr(item, "intensity", 0.0)))

    def _disturbance_intensity(self, disturbances: tuple[CognitiveDisturbance, ...]) -> float:
        return max((_clamp01(getattr(item, "intensity", 0.0)) for item in tuple(disturbances or ())), default=0.0)

    def _has_direct_disturbance(self, disturbances: tuple[CognitiveDisturbance, ...]) -> bool:
        for disturbance in tuple(disturbances or ()):
            kind = str(getattr(disturbance, "kind", "") or "")
            channel = str(getattr(disturbance, "channel", "") or "")
            intensity = _clamp01(getattr(disturbance, "intensity", 0.0))
            if kind in {"user_message", "user_force_speak"} or channel in {"chat", "force_speak"}:
                return True
            if intensity >= 0.78:
                return True
        return False

    def _pressure(self, state: PersistentInternalState, name: str) -> float:
        return _clamp01(getattr(state, name, 0.0))


#Nhận một thought rồi tính lại độ căng.
'''
Công thức lấy từ:

pressure theo mode
persistence
unresolved reason
số lần quay lại
tuổi của thought
restraint pressure làm giảm một chút
Nói ngắn: thought càng lặp, càng chưa xong, càng liên quan state hiện tại thì tension càng cao.
'''
class CognitiveTensionEngine:
    """Computes why a thought persists: unresolved prediction, not randomness."""

    def update(
        self,
        thought: ThoughtObject,
        *,
        state: PersistentInternalState,
        now: float,
    ) -> ThoughtObject:
        pressure = self._pressure_for_mode(thought.mode, state=state)
        age_gain = min(0.12, max(0.0, (now - (thought.created_at or now)) / 900.0))
        recurrence_gain = min(0.16, thought.recurrence_count * 0.05)
        unresolved_gain = 0.12 if thought.unresolved_reason else 0.0
        tension = _clamp01(
            max(thought.tension * 0.74, pressure)
            + thought.persistence * 0.14
            + unresolved_gain
            + recurrence_gain
            + age_gain
            - self._pressure(state, "restraint_pressure") * 0.05
        )
        return ThoughtObject(
            thought_id=thought.thought_id,
            mode=thought.mode,
            topic_seed=thought.topic_seed,
            partial_text=thought.partial_text,
            prediction_key=thought.prediction_key,
            unresolved_reason=thought.unresolved_reason,
            source_memory=thought.source_memory,
            associations=thought.associations,
            tension=tension,
            novelty=thought.novelty,
            persistence=_clamp01(max(thought.persistence, tension * 0.62)),
            recurrence_count=thought.recurrence_count,
            surfaced_count=thought.surfaced_count,
            resolved=False,
            evidence=thought.evidence,
            risk_flags=thought.risk_flags,
            created_at=thought.created_at,
            updated_at=now,
            last_reactivated_at=thought.last_reactivated_at,
        )

    def _pressure_for_mode(self, mode: str, *, state: PersistentInternalState) -> float:
        base = max(
            self._pressure(state, "subconscious_pressure"),
            self._pressure(state, "thought_stream_pressure"),
            self._pressure(state, "cognitive_tension"),
            self._pressure(state, "unresolved_prediction_pressure") * 0.90,
        )
        if mode == "memory_replay":
            base = max(base, self._pressure(state, "memory_replay_pressure") + 0.16)
        elif mode == "input_disturbance":
            base = max(base, self._pressure(state, "social_pressure") + 0.18, self._pressure(state, "curiosity_pressure") + 0.10)
        elif mode == "associative_drift":
            base = max(base, self._pressure(state, "association_pressure") + self._pressure(state, "curiosity_pressure") * 0.18)
        elif mode == "quiet_self_muse":
            base = max(base, self._pressure(state, "silence_pressure") + 0.12)
        else:
            base = max(base, self._pressure(state, "boredom") * 0.72)
        return _clamp01(base)

    def _pressure(self, state: PersistentInternalState, name: str) -> float:
        return _clamp01(getattr(state, name, 0.0))


#Gom nhiều thought chưa xong thành UnresolvedTensionLoop.
'''
Nó tính:

số thought chưa giải
recurrence
tension trung bình/cao nhất
persistence
relevance với project/code/user
state pressure

Nếu score đủ cao, loop thành attractor.
'''
#attractor nghĩa là: một cụm ý nghĩ có lực hút, dễ kéo attention quay lại
class UnresolvedTensionEngine:
    """Groups unfinished thoughts into loops and promotes strong loops to attractors."""

    PROJECT_FOCUS_KINDS = {"coding", "documentation", "system"}
    PROJECT_TERMS = {
        "bug",
        "code",
        "coding",
        "doc",
        "docs",
        "eris",
        "feature",
        "file",
        "implementation",
        "project",
        "repo",
        "test",
        "user",
    }

    def evaluate(
        self,
        *,
        thoughts: tuple[ThoughtObject, ...],
        snapshot,
        state: PersistentInternalState,
        anchors: tuple[str, ...],
        now: float,
    ) -> tuple[UnresolvedTensionLoop, ...]:
        groups: dict[str, list[ThoughtObject]] = {}
        for thought in thoughts:
            if thought.resolved or not thought.unresolved_reason:
                continue
            groups.setdefault(self._loop_key(thought, anchors=anchors), []).append(thought)

        loops = tuple(
            self._build_loop(key, bucket, snapshot=snapshot, state=state, anchors=anchors, now=now)
            for key, bucket in groups.items()
            if bucket
        )
        return tuple(
            sorted(
                loops,
                key=lambda item: (item.attractor, item.score, item.updated_at),
                reverse=True,
            )
        )

    def _build_loop(
        self,
        key: str,
        thoughts: list[ThoughtObject],
        *,
        snapshot,
        state: PersistentInternalState,
        anchors: tuple[str, ...],
        now: float,
    ) -> UnresolvedTensionLoop:
        unresolved_count = len(thoughts)
        recurrence = sum(max(0, int(item.recurrence_count or 0)) for item in thoughts)
        max_tension = max(float(item.tension or 0.0) for item in thoughts)
        avg_tension = sum(float(item.tension or 0.0) for item in thoughts) / max(1, unresolved_count)
        avg_persistence = sum(float(item.persistence or 0.0) for item in thoughts) / max(1, unresolved_count)
        density = min(1.0, max(0, unresolved_count - 1) / 2.0)
        recurrence_pressure = min(1.0, recurrence / 3.0)
        project_relevance = self._project_relevance(thoughts, snapshot=snapshot, anchors=anchors)
        state_pressure = max(
            self._pressure(state, "unresolved_prediction_pressure"),
            self._pressure(state, "cognitive_tension"),
            self._pressure(state, "thought_stream_pressure") * 0.82,
        )
        score = _clamp01(
            max_tension * 0.30
            + avg_tension * 0.10
            + avg_persistence * 0.16
            + recurrence_pressure * 0.22
            + density * 0.12
            + project_relevance * 0.08
            + state_pressure * 0.02
        )
        attractor = (
            score >= 0.64
            and recurrence >= 1
            and (unresolved_count >= 2 or max_tension >= 0.62)
        )
        evidence = tuple(
            dict.fromkeys(
                (
                    f"loop={key}",
                    f"unresolved={unresolved_count}",
                    f"reactivations={recurrence}",
                    f"loop_tension={max_tension:.2f}",
                    f"loop_score={score:.2f}",
                    "attractor=1" if attractor else "attractor=0",
                    *(item for thought in thoughts for item in thought.evidence[:2]),
                )
            )
        )[:8]
        risk_items = [flag for thought in thoughts for flag in thought.risk_flags]
        risk_items.append("unresolved_tension_loop")
        if attractor:
            risk_items.append("cognitive_attractor")
        risk_flags = tuple(dict.fromkeys(risk_items))
        topic = max(thoughts, key=lambda item: (item.tension, item.recurrence_count, item.updated_at)).topic_seed
        return UnresolvedTensionLoop(
            loop_id=f"loop_{abs(hash(key)) % 1000000}",
            topic_seed=topic or key,
            thought_ids=tuple(dict.fromkeys(item.thought_id for item in thoughts if item.thought_id)),
            score=score,
            tension=max_tension,
            persistence=avg_persistence,
            recurrence_count=recurrence,
            unresolved_count=unresolved_count,
            attractor=attractor,
            evidence=evidence,
            risk_flags=risk_flags,
            updated_at=max((item.updated_at for item in thoughts), default=now),
        )

    def _loop_key(self, thought: ThoughtObject, *, anchors: tuple[str, ...]) -> str:
        detail = (thought.topic_seed or thought.prediction_key or thought.partial_text or "open_loop").split(":", 1)[-1]
        thought_tokens = _semantic_tokens(
            " ".join(
                (
                    detail,
                    thought.partial_text,
                    thought.source_memory,
                    " ".join(thought.associations),
                )
            )
        )
        anchor_tokens = _semantic_tokens(" ".join(anchors))
        shared = sorted(thought_tokens & anchor_tokens)
        if shared:
            return f"{thought.mode}:{'_'.join(shared[:3])}"
        useful = sorted(token for token in thought_tokens if token not in {thought.mode})
        if useful:
            return f"{thought.mode}:{'_'.join(useful[:3])}"
        return thought.prediction_key or thought.topic_seed or thought.thought_id or "open_loop"

    def _project_relevance(self, thoughts: list[ThoughtObject], *, snapshot, anchors: tuple[str, ...]) -> float:
        focus_kind = str(getattr(snapshot, "active_window_kind", "") or "")
        text = _normalize_ascii(
            " ".join(
                (
                    focus_kind,
                    " ".join(anchors),
                    " ".join(item.topic_seed for item in thoughts),
                    " ".join(item.source_memory for item in thoughts),
                    " ".join(item.partial_text for item in thoughts),
                )
            )
        )
        relevance = 0.0
        if focus_kind in self.PROJECT_FOCUS_KINDS:
            relevance = max(relevance, 0.66)
        if any(item.source_memory for item in thoughts):
            relevance = max(relevance, 0.48)
        if any(item.source_memory.startswith(("semantic/user", "relationship/")) for item in thoughts):
            relevance = max(relevance, 0.78)
        if any(term in text for term in self.PROJECT_TERMS):
            relevance = max(relevance, 0.72)
        return _clamp01(relevance)

    def _pressure(self, state: PersistentInternalState, name: str) -> float:
        return _clamp01(getattr(state, name, 0.0))


#Tính xem thought nào có đủ quán tính để ảnh hưởng attention.
'''
Điều kiện có momentum khá chặt:

chưa resolved
recurrence >= 1
tension >= 0.54
relevance >= 0.42
còn fresh
score >= 0.58

Nếu đạt, affordance là "attention".
'''
#Tức là thought chưa chắc được nói ra, nhưng nó đủ mạnh để đẩy hệ thống chú ý về phía nó.
class CognitiveMomentumEngine:
    """Turns repeated unresolved fragments into attention inertia."""

    USER_PROJECT_TERMS = (
        "user",
        "ban",
        "project",
        "repo",
        "code",
        "coding",
        "doc",
        "docs",
        "file",
        "test",
        "bug",
        "feature",
        "implementation",
        "relationship",
        "eris",
    )
    PROJECT_FOCUS_KINDS = {"coding", "documentation", "system"}

    def evaluate(
        self,
        *,
        thoughts: tuple[ThoughtObject, ...],
        snapshot,
        state: PersistentInternalState,
        anchors: tuple[str, ...],
        now: float,
    ) -> tuple[CognitiveMomentum, ...]:
        momentums = tuple(
            self._momentum_for(
                thought,
                snapshot=snapshot,
                state=state,
                anchors=anchors,
                now=now,
            )
            for thought in thoughts
        )
        return tuple(
            sorted(
                (item for item in momentums if item.score > 0.0),
                key=lambda item: (item.score, item.updated_at),
                reverse=True,
            )
        )
    # điểm momentum được tính dựa trên nhiều yếu tố như tension, persistence, số lần tái kích hoạt, độ liên quan với dự án hiện tại, và độ mới của ý nghĩ đó. Ý nghĩ nào có điểm momentum cao nhất sẽ có khả năng được bộc lộ ra ngoài hơn, tạo thành một "inertia" trong luồng suy nghĩ của người dùng.
    def _momentum_for(
        self,
        thought: ThoughtObject,
        *,
        snapshot,
        state: PersistentInternalState,
        anchors: tuple[str, ...],
        now: float,
    ) -> CognitiveMomentum:
        if thought.resolved:
            return CognitiveMomentum(
                thought_id=thought.thought_id,
                mode=thought.mode,
                topic_seed=thought.topic_seed,
                closure_state="closed",
                updated_at=thought.updated_at,
            )
        unresolved = bool(thought.unresolved_reason)
        recurrence = max(0, int(thought.recurrence_count or 0))
        activation = min(1.0, recurrence / 2.0)
        age = max(0.0, now - (thought.last_reactivated_at or thought.updated_at or now))
        freshness = 1.0 if age <= 900.0 else max(0.0, 1.0 - (age - 900.0) / 1800.0)
        relevance = self._user_project_relevance(
            thought,
            snapshot=snapshot,
            anchors=anchors,
        )
        score = _clamp01(
            thought.tension * 0.36
            + thought.persistence * 0.18
            + activation * 0.22
            + relevance * 0.18
            + freshness * 0.06
        )
        has_momentum = (
            unresolved
            and recurrence >= 1
            and thought.tension >= 0.54
            and relevance >= 0.42
            and freshness > 0.0
            and score >= 0.58
        )
        # affordance "attention" có nghĩa là ý nghĩ này có đủ momentum để có khả năng được bộc lộ ra ngoài, tạo thành một "inertia" trong luồng suy nghĩ của người dùng. Nếu không có momentum, thì affordance sẽ là rỗng, nghĩa là ý nghĩ này vẫn tồn tại nhưng không đủ mạnh để ảnh hưởng đến luồng suy nghĩ hiện tại.
        affordance = "attention" if has_momentum else ""
        evidence = tuple(
            dict.fromkeys(
                (
                    *thought.evidence,
                    f"reactivations={recurrence}",
                    f"tension={thought.tension:.2f}",
                    f"user_project_relevance={relevance:.2f}",
                    f"momentum={score:.2f}",
                )
            )
        )[-8:]
        return CognitiveMomentum(
            thought_id=thought.thought_id,
            mode=thought.mode,
            topic_seed=thought.topic_seed,
            score=score if has_momentum else 0.0,
            tension=thought.tension,
            persistence=thought.persistence,
            recurrence_count=recurrence,
            unresolved=unresolved,
            user_project_relevance=relevance,
            affordance=affordance,
            closure_state="open",
            evidence=evidence,
            risk_flags=tuple(dict.fromkeys((*thought.risk_flags, "cognitive_momentum"))),
            updated_at=thought.updated_at,
        )

    def _user_project_relevance(
        self,
        thought: ThoughtObject,
        *,
        snapshot,
        anchors: tuple[str, ...],
    ) -> float:
        focus_kind = str(getattr(snapshot, "active_window_kind", "") or "")
        text = _normalize_ascii(
            " ".join(
                (
                    thought.topic_seed,
                    thought.partial_text,
                    thought.source_memory,
                    " ".join(thought.associations),
                    " ".join(thought.evidence),
                    " ".join(anchors),
                    focus_kind,
                )
            )
        )
        relevance = 0.0
        if focus_kind in self.PROJECT_FOCUS_KINDS:
            relevance = max(relevance, 0.66)
        if thought.source_memory:
            relevance = max(relevance, 0.48)
        if thought.source_memory.startswith(("semantic/user", "relationship/")):
            relevance = max(relevance, 0.78)
        if any(term in text for term in self.USER_PROJECT_TERMS):
            relevance = max(relevance, 0.72)
        return _clamp01(relevance)


class MomentumClosure:
    """Closes momentum only from explicit closure signals."""

    def apply(self, store: FragmentStore, *, closure_signals: tuple[str, ...], now: float) -> tuple[str, ...]:
        return store.close_matching(tuple(closure_signals or ()), now=now)


class PersistenceDecay:
    def apply(self, store: FragmentStore, *, now: float) -> None:
        store.decay(now=now)


class RecursiveReactivation:
    """Lets a thought come back to itself and mutate before it surfaces."""

    def reactivate(
        self,
        *,
        store: FragmentStore,
        candidate: ThoughtObject,
        associations: tuple[str, ...],
        now: float,
    ) -> ThoughtObject:
        enriched = ThoughtObject(
            thought_id=candidate.thought_id,
            mode=candidate.mode,
            topic_seed=candidate.topic_seed,
            partial_text=candidate.partial_text,
            prediction_key=candidate.prediction_key,
            unresolved_reason=candidate.unresolved_reason,
            source_memory=candidate.source_memory,
            associations=associations,
            tension=candidate.tension,
            novelty=candidate.novelty,
            persistence=candidate.persistence,
            recurrence_count=candidate.recurrence_count,
            surfaced_count=candidate.surfaced_count,
            resolved=False,
            evidence=candidate.evidence,
            risk_flags=candidate.risk_flags,
            created_at=candidate.created_at,
            updated_at=now,
            last_reactivated_at=now,
        )
        return store.upsert(enriched, now=now)


class SubconsciousLoopReheater:
    """Reactivates unresolved loops as silent pressure instead of fresh drift."""

    # Các token này xuất hiện quá thường xuyên trong memory runtime.
    # Nếu chỉ dựa vào chúng, hệ thống sẽ tưởng memory nào có "project/code/user"
    # cũng liên quan, rồi hâm lại sai loop.
    GENERIC_MEMORY_TOKENS = {
        "active",
        "agent",
        "code",
        "coding",
        "current",
        "evidence",
        "eris",
        "file",
        "fix",
        "implementation",
        "loop",
        "memory",
        "mode",
        "project",
        "runtime",
        "signal",
        "state",
        "system",
        "test",
        "thought",
        "user",
    }

    # Memory layer có độ đáng tin/độ gần nghĩa khác nhau.
    # Đây chỉ là bias rất nhẹ, không cho layer thắng nếu nội dung không khớp.
    MEMORY_LAYER_WEIGHTS = (
        ("semantic/user", 1.10),
        ("semantic/project", 1.08),
        ("relationship/", 1.06),
        ("repair/", 1.05),
        ("episodic", 0.98),
        ("working", 0.94),
        ("active", 0.92),
    )

    def reheat(
        self,
        *,
        store: FragmentStore,
        candidate: ThoughtObject,
        long_term_memory: tuple[str, ...],
        disturbances: tuple[CognitiveDisturbance, ...],
        now: float,
    ) -> ThoughtObject:
        if self._has_direct_disturbance(disturbances):
            return candidate
        unresolved = tuple(item for item in store.unresolved() if not item.resolved and item.unresolved_reason)
        if not unresolved:
            return candidate
        loop = max(unresolved, key=lambda item: self._reheat_score(item, memories=long_term_memory))
        if loop.tension < 0.20 and loop.persistence < 0.34:
            return candidate

        memory = self._matching_memory(loop, long_term_memory)
        memory_gain = 0.05 if memory else 0.0
        recurrence_gain = min(0.05, loop.recurrence_count * 0.012)
        reactivation = 0.06 + min(0.06, loop.persistence * 0.08) + memory_gain + recurrence_gain
        tension = _clamp01(loop.tension * 0.98 + reactivation)
        associations = tuple(dict.fromkeys((*loop.associations, *candidate.associations)))
        if memory:
            associations = tuple(dict.fromkeys((*associations, f"memory_resonance:{_compact_seed(memory)}")))

        return ThoughtObject(
            thought_id=loop.thought_id,
            mode=loop.mode,
            topic_seed=loop.topic_seed,
            partial_text=self._mutated_question(loop),
            prediction_key=loop.prediction_key or loop.topic_seed,
            unresolved_reason=loop.unresolved_reason,
            source_memory=memory or loop.source_memory,
            associations=associations[-8:],
            tension=tension,
            novelty=_clamp01(max(loop.novelty * 0.86, 0.34)),
            persistence=_clamp01(max(loop.persistence * 0.98, tension * 0.58)),
            recurrence_count=loop.recurrence_count,
            surfaced_count=loop.surfaced_count,
            resolved=False,
            evidence=tuple(
                dict.fromkeys(
                    (
                        *loop.evidence,
                        "loop_reheated",
                        "silent_pressure_update",
                        *(("memory_resonance_linked",) if memory else ()),
                    )
                )
            )[-8:],
            risk_flags=tuple(dict.fromkeys((*loop.risk_flags, "silent_pressure", "internal_only"))),
            created_at=loop.created_at,
            updated_at=now,
            last_reactivated_at=now,
        )

    def _reheat_score(self, thought: ThoughtObject, *, memories: tuple[str, ...]) -> float:
        # Trước đây chỉ cần có token giao nhau là cộng memory_link.
        # Lỗi: memory dài/generic chứa vài token phổ biến cũng kích hoạt loop.
        # Bây giờ dùng cùng scoring mềm với _matching_memory để memory chỉ tạo áp lực
        # khi nó có resonance thật với core của thought.
        memory_link = 0.0
        for reverse_index, memory in enumerate(reversed(tuple(memories or ())[-10:])):
            score, _, _ = self._memory_match_score(thought, memory, reverse_index=reverse_index)
            if score >= 0.20:
                memory_link = max(memory_link, min(0.16, score * 0.24))
        return _clamp01(
            float(thought.tension or 0.0) * 0.48
            + float(thought.persistence or 0.0) * 0.28
            + min(0.18, int(thought.recurrence_count or 0) * 0.04)
            + memory_link
        )

    def _matching_memory(self, thought: ThoughtObject, memories: tuple[str, ...]) -> str:
        # Không chọn memory bằng overlap thô.
        # Overlap thô sẽ ưu tiên memory dài và generic, vì càng dài càng dễ chứa vài token
        # như "user", "project", "code", "memory". Đây là false resonance.
        #
        # Thay vào đó, chọn theo resonance score:
        # - core tokens của thought phải khớp với memory
        # - overlap được chuẩn hóa theo containment/jaccard, không phải số lượng tuyệt đối
        # - token generic bị phạt nếu không có token đặc hiệu đi kèm
        # - memory gần đây chỉ là tie-break/bias nhẹ, không được thắng nội dung
        if not memories:
            return ""

        best = ""
        best_score = 0.0
        best_tiebreak = (-1.0, -1, -999)

        recent_memories = tuple(memories or ())[-18:]
        for reverse_index, memory in enumerate(reversed(recent_memories)):
            score, overlap_count, specificity = self._memory_match_score(
                thought,
                memory,
                reverse_index=reverse_index,
            )
            if score <= 0.0:
                continue

            # Tie-break chỉ dùng khi score gần bằng nhau:
            # ưu tiên memory có token đặc hiệu hơn, rồi overlap vừa đủ, rồi mới tới recency.
            tiebreak = (specificity, overlap_count, -reverse_index)
            if score > best_score + 1e-9 or (abs(score - best_score) <= 1e-9 and tiebreak > best_tiebreak):
                best = _clean_text(memory, limit=160)
                best_score = score
                best_tiebreak = tiebreak

        # Ngưỡng thấp vừa đủ để giữ subconscious linh hoạt, nhưng không nhận bất kỳ
        # memory nào chỉ vì cùng vài từ rác.
        return best if best_score >= 0.18 else ""

    def _memory_match_score(self, thought: ThoughtObject, memory: object, *, reverse_index: int = 0) -> tuple[float, int, float]:
        thought_tokens = self._thought_tokens(thought)
        memory_text = _clean_text(memory, limit=240)
        memory_tokens = _semantic_tokens(memory_text)
        if not thought_tokens or not memory_tokens:
            return 0.0, 0, 0.0

        overlap_tokens = thought_tokens & memory_tokens
        overlap_count = len(overlap_tokens)
        if overlap_count <= 0:
            return 0.0, 0, 0.0

        core_tokens = self._core_thought_tokens(thought)
        specific_thought_tokens = self._specific_tokens(thought_tokens)
        specific_overlap = len(overlap_tokens & specific_thought_tokens)
        generic_overlap = overlap_count - specific_overlap
        core_overlap = len(core_tokens & memory_tokens)

        # Nếu chỉ trùng token generic, đừng xem là resonance.
        # Trường hợp overlap >= 4 vẫn cho score rất thấp để không mất hoàn toàn
        # các memory ngắn/legacy, nhưng nó gần như không thắng được memory đặc hiệu.
        if specific_overlap <= 0 and core_overlap <= 0 and overlap_count < 4:
            return 0.0, overlap_count, 0.0

        containment = overlap_count / max(1, min(len(thought_tokens), len(memory_tokens), 14))
        jaccard = overlap_count / max(1, len(thought_tokens | memory_tokens))
        core_score = core_overlap / max(1, min(len(core_tokens), 8))
        specificity = specific_overlap / max(1, overlap_count)

        # Memory quá dài thường thắng overlap thô dù nghĩa loãng. Phạt nhẹ theo độ dài
        # để memory ngắn, chính xác không bị chôn dưới một đoạn tóm tắt dài như biên bản họp.
        length_penalty = max(0.68, 1.0 - max(0, len(memory_tokens) - 26) * 0.010)
        generic_penalty = 0.72 if generic_overlap and specific_overlap == 0 else 1.0
        layer_weight = self._memory_layer_weight(memory_text)
        recency_weight = max(0.74, 1.0 - reverse_index * 0.018)

        score = (
            containment * 0.30
            + jaccard * 0.18
            + core_score * 0.34
            + specificity * 0.18
        )
        score *= length_penalty * generic_penalty * layer_weight * recency_weight
        return _clamp01(score), overlap_count, specificity

    def _core_thought_tokens(self, thought: ThoughtObject) -> set[str]:
        # Core lấy từ seed/key trước, vì đây là phần định danh loop.
        # partial_text/evidence thường dài và dễ kéo theo token nền không quan trọng.
        core = _semantic_tokens(
            " ".join(
                (
                    thought.topic_seed,
                    thought.prediction_key,
                    thought.source_memory,
                )
            )
        )
        return self._specific_tokens(core) or core

    def _specific_tokens(self, tokens: set[str]) -> set[str]:
        return {token for token in tokens if token not in self.GENERIC_MEMORY_TOKENS}

    def _memory_layer_weight(self, memory: object) -> float:
        normalized = _normalize_ascii(memory)
        for prefix, weight in self.MEMORY_LAYER_WEIGHTS:
            if normalized.startswith(prefix):
                return weight
        return 1.0

    def _thought_tokens(self, thought: ThoughtObject) -> set[str]:
        return _semantic_tokens(
            " ".join(
                (
                    thought.topic_seed,
                    thought.prediction_key,
                    thought.partial_text,
                    thought.unresolved_reason,
                    thought.source_memory,
                    " ".join(thought.associations),
                    " ".join(thought.evidence),
                )
            )
        )

    def _mutated_question(self, thought: ThoughtObject) -> str:
        seed = thought.topic_seed or thought.prediction_key or "open_loop"
        detail = seed.split(":", 1)[-1].replace("_", " ").strip() or seed
        normalized = _normalize_ascii(detail)
        if "phase" in normalized and {"chat", "force", "autonomous"} & set(normalized.split()):
            return "why chat/force/autonomous still have not entered one shared flow"
        if "force" in normalized and "autonomous" in normalized:
            return "why force_speak and autonomous still feel split from the same substrate"
        if "guard" in normalized and ("raw" in normalized or "final" in normalized):
            return "whether the output guard is still squeezing the natural raw line"
        return f"why this loop still has not closed: {detail}"[:180]

    def _has_direct_disturbance(self, disturbances: tuple[CognitiveDisturbance, ...]) -> bool:
        for disturbance in tuple(disturbances or ()):
            kind = str(getattr(disturbance, "kind", "") or "")
            channel = str(getattr(disturbance, "channel", "") or "")
            intensity = _clamp01(getattr(disturbance, "intensity", 0.0))
            if kind in {"user_message", "user_force_speak"} or channel in {"chat", "force_speak"}:
                return True
            if intensity >= 0.78:
                return True
        return False


#Đây là cổng quyết định thought có được nổi lên thành ThoughtSurface không.
'''
Nó chặn nếu:

thought đã closed
seed vừa xuất hiện gần đây
novelty quá thấp
restraint quá cao
silent pressure chưa đủ mạnh
tension thấp
đã surfaced rồi mà chưa đủ căng để lộ lại

Nếu qua cổng, nó tạo ThoughtSurface với:

mode
topic_seed
association
pressure
novelty
evidence
risk_flags
unresolved_reason
momentum_score
affordance "speech"
'''
#Tức là 95% dòng suy nghĩ nằm dưới mặt nước. Chỉ một mẩu đủ mạnh mới nổi lên
class SurfaceSelector:
    """Selects the rare visible tip; most thought stream activity stays internal."""

    def select(
        self,
        thought: ThoughtObject,
        *,
        state: PersistentInternalState,
        momentum: CognitiveMomentum | None = None,
        recent_inner_seeds: tuple[str, ...],
        recent_thought_seeds: tuple[str, ...],
        recent_subconscious_seeds: tuple[str, ...],
        now: float,
    ) -> tuple[ThoughtSurface, str]:
        if thought.resolved:
            return ThoughtSurface(), "closed"
        recent = set(
            str(item)
            for item in (*recent_inner_seeds[-4:], *recent_thought_seeds[-6:], *recent_subconscious_seeds[-6:])
            if item
        )
        if thought.topic_seed in recent:
            return ThoughtSurface(), "recent_seed_repeat"
        if thought.novelty <= 0.25:
            return ThoughtSurface(), "low_novelty"
        restraint = _clamp01(getattr(state, "restraint_pressure", 0.0))
        if restraint >= 0.82 and thought.tension < 0.72:
            return ThoughtSurface(), "restraint_pressure"
        if "silent_pressure" in thought.risk_flags:
            momentum_score = _clamp01(getattr(momentum, "score", 0.0) if momentum is not None else 0.0)
            if momentum_score < 0.82 or thought.recurrence_count < 3:
                return ThoughtSurface(), "silent_pressure"
        if thought.tension < 0.38:
            return ThoughtSurface(), "below_surface_pressure"
        if thought.surfaced_count > 0 and thought.tension < 0.74:
            return ThoughtSurface(), "internal_reentry_only"
        root_source = self._root_source(thought)
        impulse_kind = self._impulse_kind(thought, root_source=root_source)
        public_seed = self._public_seed(thought)
        surface = ThoughtSurface(
            mode=thought.mode,
            topic_seed=thought.topic_seed,
            source_memory=thought.source_memory,
            association=self._surface_association(thought),
            pressure=thought.tension,
            novelty=thought.novelty,
            evidence=thought.evidence,
            risk_flags=tuple(dict.fromkeys((*thought.risk_flags, f"root_source={root_source}", f"impulse_kind={impulse_kind}"))),
            created_at=now,
            thought_id=thought.thought_id,
            tension=thought.tension,
            unresolved_reason=thought.unresolved_reason,
            partial_text=thought.partial_text,
            recurrence_count=thought.recurrence_count,
            surfaced_count=thought.surfaced_count,
            momentum_score=_clamp01(getattr(momentum, "score", 0.0) if momentum is not None else 0.0),
            affordance="speech",
            impulse_kind=impulse_kind,
            root_source=root_source,
            action_opportunity=self._action_opportunity(thought, impulse_kind=impulse_kind),
            public_seed=public_seed,
        )
        return surface, ""

    def _surface_association(self, thought: ThoughtObject) -> str:
        if thought.source_memory:
            return "recent memory replayed as an unfinished prediction"
        if thought.mode == "quiet_self_muse":
            return "continuity between internal states asked to re-enter"
        if thought.mode == "attention_drift":
            return "attention drift kept an unresolved prediction alive"
        if thought.associations:
            return f"{thought.mode} connected through {', '.join(thought.associations[:3])}"
        return f"{thought.mode} linked to {thought.topic_seed}"

    def _root_source(self, thought: ThoughtObject) -> str:
        """Phân loại rễ của thought trước khi cho phép surface.

        Random chat của người thật thường là liên tưởng cũ bị bật lại, không phải
        một model nhìn khoảng trắng rồi nói nhảm. Field này cho SurfaceArbiter
        biết thought đến từ memory/daydream/unresolved loop hay chỉ là boredom.
        """
        source = _normalize_ascii(thought.source_memory)
        topic = _normalize_ascii(thought.topic_seed)
        evidence = _normalize_ascii(" ".join(thought.evidence))
        hay = " ".join((source, topic, evidence))
        if "daydream" in hay or "insight" in hay or "open_question" in hay:
            return "daydream"
        if "unresolved_memory" in hay or "candidate[" in hay or "memory_candidate" in hay:
            return "unresolved_memory"
        if thought.source_memory or "memory_replay" in hay:
            return "memory"
        if thought.mode == "attention_drift":
            return "boredom"
        return "thought"

    def _impulse_kind(self, thought: ThoughtObject, *, root_source: str) -> str:
        if root_source in {"memory", "daydream", "unresolved_memory"}:
            return "memory_resonance"
        if root_source == "boredom" or thought.mode == "attention_drift":
            return "random_chatter"
        return "inner_whim"

    def _action_opportunity(self, thought: ThoughtObject, *, impulse_kind: str) -> str:
        if impulse_kind == "memory_resonance":
            return "surface a low-claim remembered pattern only if current context allows interruption"
        if impulse_kind == "random_chatter":
            return "casual dry chatter only in low-focus/media contexts; otherwise body_only"
        return "one tiny stray thought if rooted and not intrusive"

    def _public_seed(self, thought: ThoughtObject) -> str:
        source = _clean_text(thought.source_memory, limit=180)
        if source:
            return source
        partial = _clean_text(thought.partial_text, limit=160)
        if partial and not partial.startswith("presence continuity"):
            return partial
        return _clean_text(thought.topic_seed, limit=120)

'''
Thứ tự trong evaluate():

Lấy internal_state, snapshot, memory, recent seeds, disturbances.
Decay thought cũ.
PredictionTracker tạo candidate thought.
SubconsciousLoopReheater có thể thay candidate bằng loop cũ được hâm lại.
Tạo anchors từ snapshot, last_text, memory, disturbance.
AssociationGraph gắn association.
RecursiveReactivation upsert thought vào store.
CognitiveTensionEngine cập nhật tension.
MomentumClosure đóng loop nếu có closure signal.
Lấy unresolved thoughts.
UnresolvedTensionEngine tìm loop/attractor.
CognitiveMomentumEngine tính momentum.
SurfaceSelector quyết định có surface không.
Trả ThoughtStreamReport.

Nếu có surface thì mark_surfaced() để giảm lực thought đó sau khi lộ.
'''
class ThoughtStreamEngine:
    """Maintains unresolved thought continuity and occasionally exposes a surface."""

    def __init__(
        self,
        *,
        fragment_store: FragmentStore | None = None,
        association_graph: AssociationGraph | None = None,
        prediction_tracker: PredictionTracker | None = None,
        tension_engine: CognitiveTensionEngine | None = None,
        unresolved_tension_engine: UnresolvedTensionEngine | None = None,
        momentum_engine: CognitiveMomentumEngine | None = None,
        momentum_closure: MomentumClosure | None = None,
        persistence_decay: PersistenceDecay | None = None,
        recursive_reactivation: RecursiveReactivation | None = None,
        loop_reheater: SubconsciousLoopReheater | None = None,
        surface_selector: SurfaceSelector | None = None,
        model_reviewer=None,
        model_review_enabled: bool | None = None,
        model_review_min_interval_seconds: float | None = None,
        model_review_min_trigger: float | None = None,
        logger=None,
    ):
        self.fragment_store = resolve_dependency(fragment_store, lambda: FragmentStore())
        self.association_graph = resolve_dependency(association_graph, lambda: AssociationGraph())
        self.prediction_tracker = resolve_dependency(prediction_tracker, lambda: PredictionTracker())
        self.tension_engine = resolve_dependency(tension_engine, lambda: CognitiveTensionEngine())
        self.unresolved_tension_engine = resolve_dependency(unresolved_tension_engine, lambda: UnresolvedTensionEngine())
        self.momentum_engine = resolve_dependency(momentum_engine, lambda: CognitiveMomentumEngine())
        self.momentum_closure = resolve_dependency(momentum_closure, lambda: MomentumClosure())
        self.persistence_decay = resolve_dependency(persistence_decay, lambda: PersistenceDecay())
        self.recursive_reactivation = resolve_dependency(recursive_reactivation, lambda: RecursiveReactivation())
        self.loop_reheater = resolve_dependency(loop_reheater, lambda: SubconsciousLoopReheater())
        self.surface_selector = resolve_dependency(surface_selector, lambda: SurfaceSelector())
        self.model_reviewer = model_reviewer
        self.model_review_enabled = bool(model_reviewer) if model_review_enabled is None else bool(model_review_enabled)
        self.model_review_min_interval_seconds = max(1.0, float(
            model_review_min_interval_seconds
            if model_review_min_interval_seconds is not None
            else getattr(model_reviewer, "min_interval_seconds", _float_env("ERIS_THOUGHT_EVALUATOR_MIN_INTERVAL_SEC", 45.0))
        ))
        self.model_review_min_trigger = _clamp01(
            model_review_min_trigger
            if model_review_min_trigger is not None
            else _float_env("ERIS_THOUGHT_EVALUATOR_MIN_TRIGGER", 0.50)
        )
        self.logger = logger
        self._last_model_review_at = 0.0
        self._last_model_review_report: dict[str, object] = {}

    def evaluate(
        self,
        *,
        snapshot=None,
        internal_state: PersistentInternalState | None = None,
        long_term_memory: tuple[str, ...] | list[str] = (),
        last_text: str = "",
        recent_inner_seeds: tuple[str, ...] = (),
        recent_thought_seeds: tuple[str, ...] = (),
        recent_subconscious_seeds: tuple[str, ...] = (),
        closure_signals: tuple[str, ...] = (),
        disturbances: tuple[CognitiveDisturbance, ...] = (),
        now: float | None = None,
    ) -> ThoughtStreamReport:
        state = internal_state or PersistentInternalState()
        now = time.time() if now is None else float(now)
        disturbances = tuple(disturbances or ())
        closed_ids: tuple[str, ...] = ()
        self.persistence_decay.apply(self.fragment_store, now=now)
        candidate = self.prediction_tracker.propose(
            snapshot=snapshot,
            state=state,
            long_term_memory=tuple(long_term_memory or ()),
            last_text=last_text,
            recent_thought_seeds=recent_thought_seeds,
            recent_subconscious_seeds=recent_subconscious_seeds,
            disturbances=disturbances,
            now=now,
        )
        candidate = self.loop_reheater.reheat(
            store=self.fragment_store,
            candidate=candidate,
            long_term_memory=tuple(long_term_memory or ()),
            disturbances=disturbances,
            now=now,
        )
        anchors = self._anchors(
            snapshot=snapshot,
            last_text=last_text,
            source_memory=candidate.source_memory,
            disturbances=disturbances,
        )
        associations = self.association_graph.connect(candidate, anchors)
        associations = tuple(dict.fromkeys((*candidate.associations, *associations)))[-8:]
        reactivated = self.recursive_reactivation.reactivate(
            store=self.fragment_store,
            candidate=candidate,
            associations=associations,
            now=now,
        )
        updated = self.tension_engine.update(reactivated, state=state, now=now)
        self.fragment_store.replace(updated)
        closed_ids = self.momentum_closure.apply(
            self.fragment_store,
            closure_signals=tuple(
                dict.fromkeys(
                    (
                        *tuple(closure_signals or ()),
                        *(
                            str(getattr(item, "content", "") or "")
                            for item in disturbances
                            if bool(getattr(item, "closes_loop", False))
                        ),
                    )
                )
            ),
            now=now,
        )
        if updated.thought_id in closed_ids:
            updated = self.fragment_store.get(updated.thought_id) or updated
        all_anchors = self._anchors(
            snapshot=snapshot,
            last_text=last_text,
            source_memory=updated.source_memory,
            disturbances=disturbances,
        )
        unresolved = self.fragment_store.unresolved()
        unresolved_loops = self.unresolved_tension_engine.evaluate(
            thoughts=unresolved,
            snapshot=snapshot,
            state=state,
            anchors=all_anchors,
            now=now,
        )
        active_attractor = next((item for item in unresolved_loops if item.attractor), UnresolvedTensionLoop())
        momentum_anchors = all_anchors
        if active_attractor.attractor and active_attractor.topic_seed:
            momentum_anchors = (*all_anchors, active_attractor.topic_seed)
        momentum_items = self.momentum_engine.evaluate(
            thoughts=unresolved,
            snapshot=snapshot,
            state=state,
            anchors=momentum_anchors,
            now=now,
        )
        momentum = momentum_items[0] if momentum_items else CognitiveMomentum()
        review_notes: tuple[str, ...] = ()
        updated, review_notes = self._maybe_apply_model_review(
            thought=updated,
            snapshot=snapshot,
            state=state,
            unresolved=unresolved,
            unresolved_loops=unresolved_loops,
            momentum=momentum,
            active_attractor=active_attractor,
            disturbances=disturbances,
            anchors=all_anchors,
            long_term_memory=tuple(long_term_memory or ()),
            last_text=last_text,
            now=now,
        )
        surface, suppression = self.surface_selector.select(
            updated,
            state=state,
            momentum=momentum if momentum.thought_id == updated.thought_id else None,
            recent_inner_seeds=recent_inner_seeds,
            recent_thought_seeds=recent_thought_seeds,
            recent_subconscious_seeds=recent_subconscious_seeds,
            now=now,
        )
        recent_thoughts = self.fragment_store.recent(limit=8)
        if surface.mode:
            self.fragment_store.mark_surfaced(surface.thought_id)
            return ThoughtStreamReport(
                latest_seed=surface,
                recent_seeds=(surface,),
                surface_pressure=surface.pressure,
                suppression_reason="",
                latest_thought=updated,
                recent_thoughts=recent_thoughts,
                momentum=momentum,
                recent_momentum=momentum_items[:4],
                active_attractor=active_attractor,
                unresolved_loops=unresolved_loops[:4],
                stream_tension=max((item.tension for item in unresolved), default=updated.tension),
                unresolved_count=len(unresolved),
                continuity_notes=self._continuity_notes(
                    updated,
                    surfaced=True,
                    momentum=momentum,
                    active_attractor=active_attractor,
                    closed_ids=closed_ids,
                    disturbances=disturbances,
                    extra_notes=review_notes,
                ),
                disturbances=disturbances,
            )
        return ThoughtStreamReport(
            surface_pressure=updated.tension,
            suppression_reason=suppression,
            latest_thought=updated,
            recent_thoughts=recent_thoughts,
            momentum=momentum,
            recent_momentum=momentum_items[:4],
            active_attractor=active_attractor,
            unresolved_loops=unresolved_loops[:4],
            stream_tension=max((item.tension for item in unresolved), default=updated.tension),
            unresolved_count=len(unresolved),
            continuity_notes=self._continuity_notes(
                updated,
                surfaced=False,
                momentum=momentum,
                active_attractor=active_attractor,
                closed_ids=closed_ids,
                disturbances=disturbances,
                extra_notes=review_notes,
            ),
            disturbances=disturbances,
        )

    def _maybe_apply_model_review(
        self,
        *,
        thought: ThoughtObject,
        snapshot,
        state: PersistentInternalState,
        unresolved: tuple[ThoughtObject, ...],
        unresolved_loops: tuple[UnresolvedTensionLoop, ...],
        momentum: CognitiveMomentum,
        active_attractor: UnresolvedTensionLoop,
        disturbances: tuple[CognitiveDisturbance, ...],
        anchors: tuple[str, ...],
        long_term_memory: tuple[str, ...],
        last_text: str,
        now: float,
    ) -> tuple[ThoughtObject, tuple[str, ...]]:
        """Cho API đánh giá thought đủ lực rồi ghi ngược vào store.

        ThoughtStream rẻ vẫn là substrate. Nhưng khi thought có tension/momentum,
        reviewer API đóng vai phiên tòa nội tâm: công tố, biện hộ, thẩm phán.
        Kết quả không nói ra ngoài; nó sửa tension/persistence/partial_text/evidence
        để các vòng sau thật sự bị ảnh hưởng. Không ghi ngược thì chỉ là log màu mè,
        mà loài người đã có đủ dashboard vô dụng rồi.
        """
        reviewer = self.model_reviewer
        if not (self.model_review_enabled and reviewer is not None):
            return thought, ()
        trigger = self._model_review_trigger_score(
            thought=thought,
            momentum=momentum,
            active_attractor=active_attractor,
            disturbances=disturbances,
            unresolved=unresolved,
        )
        due = now - float(self._last_model_review_at or 0.0) >= self.model_review_min_interval_seconds
        if trigger < self.model_review_min_trigger and not due:
            return thought, (f"api_eval_wait(trigger={trigger:.2f})",)
        if trigger < max(0.18, self.model_review_min_trigger * 0.55):
            return thought, (f"api_eval_skip(trigger={trigger:.2f})",)

        payload = self._model_review_payload(
            thought=thought,
            snapshot=snapshot,
            state=state,
            unresolved=unresolved,
            unresolved_loops=unresolved_loops,
            momentum=momentum,
            active_attractor=active_attractor,
            disturbances=disturbances,
            anchors=anchors,
            long_term_memory=long_term_memory,
            last_text=last_text,
            trigger=trigger,
            now=now,
        )
        try:
            review = reviewer.review(payload)
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning("[ThoughtEvaluator API] review failed: %s", exc)
            self._last_model_review_at = now
            return thought, (f"api_eval_error={type(exc).__name__}",)

        self._last_model_review_at = now
        normalized = self._normalize_model_review(review, allowed_evidence=self._allowed_model_evidence(payload))
        self._last_model_review_report = normalized
        if not normalized.get("ok"):
            return thought, (f"api_eval_reject={normalized.get('reason', 'invalid')}",)

        reviewed = self._apply_model_review_to_thought(thought, normalized, now=now)
        self.fragment_store.replace(reviewed)
        self._log_model_review(normalized, reviewed)
        route = str(normalized.get("recommended_route") or "keep_private")
        conf = float(normalized.get("confidence") or 0.0)
        return reviewed, (f"api_eval={route}:{conf:.2f}",)

    def _model_review_trigger_score(
        self,
        *,
        thought: ThoughtObject,
        momentum: CognitiveMomentum,
        active_attractor: UnresolvedTensionLoop,
        disturbances: tuple[CognitiveDisturbance, ...],
        unresolved: tuple[ThoughtObject, ...],
    ) -> float:
        disturbance_pressure = max((_clamp01(getattr(item, "intensity", 0.0)) for item in disturbances), default=0.0)
        recurrence = min(1.0, float(getattr(thought, "recurrence_count", 0) or 0) / 4.0)
        unresolved_pressure = min(1.0, len(tuple(unresolved or ())) / 6.0)
        return max(
            _clamp01(getattr(thought, "tension", 0.0)),
            _clamp01(getattr(momentum, "score", 0.0)),
            _clamp01(getattr(active_attractor, "score", 0.0)),
            disturbance_pressure,
            recurrence * 0.72,
            unresolved_pressure * 0.45,
        )

    def _model_review_payload(
        self,
        *,
        thought: ThoughtObject,
        snapshot,
        state: PersistentInternalState,
        unresolved: tuple[ThoughtObject, ...],
        unresolved_loops: tuple[UnresolvedTensionLoop, ...],
        momentum: CognitiveMomentum,
        active_attractor: UnresolvedTensionLoop,
        disturbances: tuple[CognitiveDisturbance, ...],
        anchors: tuple[str, ...],
        long_term_memory: tuple[str, ...],
        last_text: str,
        trigger: float,
        now: float,
    ) -> dict[str, object]:
        user_signals = getattr(snapshot, "user_signals", None)
        pressures = {
            name: round(float(_clamp01(getattr(state, name, 0.0))), 3)
            for name in (
                "curiosity_pressure",
                "association_pressure",
                "memory_replay_pressure",
                "subconscious_pressure",
                "silence_pressure",
                "attachment_pressure",
                "tension",
            )
            if hasattr(state, name)
        }
        return {
            "schema_version": "thought_stream_evaluator.v1",
            "now": round(float(now), 3),
            "trigger_score": round(float(trigger), 3),
            "current_thought": thought.to_dict(),
            "recent_unresolved": [item.to_dict() for item in tuple(unresolved or ())[:6]],
            "unresolved_loops": [item.to_dict() for item in tuple(unresolved_loops or ())[:4]],
            "momentum": _to_dict(momentum),
            "active_attractor": _to_dict(active_attractor),
            "disturbances": [item.to_dict() for item in tuple(disturbances or ())[:6]],
            "anchors": [_clean_text(item, limit=160) for item in tuple(anchors or ())[:8]],
            "memory_clues": [_clean_text(item, limit=180) for item in tuple(long_term_memory or ())[-8:]],
            "last_public_text": _clean_text(last_text, limit=220),
            "focus": {
                "active_window_kind": str(getattr(snapshot, "active_window_kind", "") or ""),
                "idle_seconds": round(float(getattr(snapshot, "idle_seconds", 0.0) or 0.0), 3),
                "focus_pattern": str(getattr(user_signals, "focus_pattern", "") or "") if user_signals is not None else "",
            },
            "internal_pressures": pressures,
            "rules": [
                "private evaluation only; do not write public speech",
                "ground every hypothesis in current_thought.evidence/disturbances/memory_clues/anchors",
                "if evidence is weak, choose keep_private or suppress",
                "surface_candidate only when useful and low-intrusion",
            ],
        }

    def _allowed_model_evidence(self, payload: dict[str, object]) -> set[str]:
        allowed: set[str] = set()

        def add(value: object) -> None:
            if isinstance(value, str) and value.strip():
                allowed.add(value.strip())
            elif isinstance(value, (list, tuple)):
                for item in value:
                    add(item)
            elif isinstance(value, dict):
                for key in ("evidence", "evidence_ids"):
                    add(value.get(key))

        add(payload.get("current_thought"))
        add(payload.get("recent_unresolved"))
        add(payload.get("unresolved_loops"))
        add(payload.get("momentum"))
        add(payload.get("active_attractor"))
        add(payload.get("disturbances"))
        # Cho phép reviewer trích anchor/memory dưới dạng private note, nhưng không
        # được giả thành evidence id hiện tại.
        allowed.add("api_model_evaluation")
        return allowed

    def _normalize_model_review(self, review: object, *, allowed_evidence: set[str]) -> dict[str, object]:
        if not isinstance(review, dict):
            return {"ok": False, "reason": "non_dict_review"}
        route = str(review.get("recommended_route") or review.get("route") or "keep_private").strip().lower()
        aliases = {
            "store": "keep_private",
            "private": "keep_private",
            "keep": "keep_private",
            "surface": "surface_candidate",
            "surface_seed": "surface_candidate",
        }
        route = aliases.get(route, route)
        if route not in {"keep_private", "rehearse", "surface_candidate", "suppress"}:
            route = "keep_private"
        confidence = _clamp01(review.get("confidence", 0.0))
        surface_pressure = _clamp01(review.get("surface_pressure", 0.0))
        memory_pressure = _clamp01(review.get("memory_pressure", review.get("persistence_pressure", 0.0)))
        dominant = _clean_text(review.get("dominant_evaluation") or review.get("summary") or review.get("judge_verdict"), limit=220)
        prosecutor = _clean_text(review.get("prosecutor_case"), limit=260)
        defense = _clean_text(review.get("defense_case"), limit=260)
        judge = _clean_text(review.get("judge_verdict"), limit=260)
        risk = _clean_text(review.get("risk_assessment"), limit=220)
        raw_evidence = review.get("evidence_ids") or review.get("evidence") or ()
        if isinstance(raw_evidence, str):
            raw_evidence = (raw_evidence,)
        safe_evidence = tuple(
            item for item in dict.fromkeys(str(ev).strip() for ev in tuple(raw_evidence or ()) if str(ev).strip())
            if item in allowed_evidence
        )[:12]
        hypotheses = []
        for item in tuple(review.get("hypotheses") or ())[:5]:
            if not isinstance(item, dict):
                continue
            hypotheses.append({
                "label": _clean_text(item.get("label"), limit=80),
                "confidence": round(_clamp01(item.get("confidence", 0.0)), 3),
                "evidence": [str(ev) for ev in tuple(item.get("evidence") or ())[:6] if str(ev) in allowed_evidence],
            })
        # Không cho surface nếu model tự thú nhận rủi ro/evidence mỏng.
        weak_grounding = confidence < 0.42 or (route == "surface_candidate" and not safe_evidence)
        if weak_grounding and route == "surface_candidate":
            route = "rehearse" if confidence >= 0.34 else "keep_private"
        return {
            "ok": bool(dominant or judge or prosecutor),
            "reason": "ok" if (dominant or judge or prosecutor) else "empty_review",
            "recommended_route": route,
            "confidence": confidence,
            "surface_pressure": surface_pressure,
            "memory_pressure": memory_pressure,
            "dominant_evaluation": dominant,
            "prosecutor_case": prosecutor,
            "defense_case": defense,
            "judge_verdict": judge,
            "risk_assessment": risk,
            "evidence_ids": safe_evidence or ("api_model_evaluation",),
            "hypotheses": tuple(hypotheses),
            "provider": str(review.get("provider") or ""),
            "model": str(review.get("model") or ""),
        }

    def _apply_model_review_to_thought(self, thought: ThoughtObject, review: dict[str, object], *, now: float) -> ThoughtObject:
        route = str(review.get("recommended_route") or "keep_private")
        confidence = _clamp01(review.get("confidence", 0.0))
        surface_pressure = _clamp01(review.get("surface_pressure", 0.0))
        memory_pressure = _clamp01(review.get("memory_pressure", 0.0))
        verdict = _clean_text(review.get("judge_verdict") or review.get("dominant_evaluation"), limit=180)
        if verdict:
            incoming = f"api_judge:{route}:{verdict}"
            partial_text = self.fragment_store._mutate_partial(thought.partial_text, incoming)
        else:
            partial_text = thought.partial_text
        if route == "suppress":
            tension = _clamp01(thought.tension * max(0.42, 1.0 - confidence * 0.38))
            persistence = _clamp01(thought.persistence * 0.86 + memory_pressure * 0.12)
        else:
            route_bonus = 0.10 if route == "rehearse" else 0.18 if route == "surface_candidate" else 0.06
            tension = _clamp01(max(thought.tension, surface_pressure * 0.92, confidence * route_bonus + thought.tension))
            persistence = _clamp01(max(thought.persistence, memory_pressure * 0.88, confidence * 0.48))
        evidence = tuple(dict.fromkeys((
            *thought.evidence,
            *tuple(str(item) for item in tuple(review.get("evidence_ids") or ()) if str(item).strip()),
            f"api_thought_eval:{route}:{confidence:.2f}",
        )))[-12:]
        risk_flags = tuple(dict.fromkeys((
            *thought.risk_flags,
            "api_reviewed",
            "private_evaluation",
            f"eval_route:{route}",
        )))
        return ThoughtObject(
            thought_id=thought.thought_id,
            mode=thought.mode,
            topic_seed=thought.topic_seed,
            partial_text=partial_text,
            prediction_key=thought.prediction_key,
            unresolved_reason=review.get("risk_assessment") or thought.unresolved_reason,
            source_memory=thought.source_memory,
            associations=thought.associations,
            tension=tension,
            novelty=_clamp01(max(thought.novelty, surface_pressure * 0.58 if route == "surface_candidate" else thought.novelty)),
            persistence=persistence,
            recurrence_count=thought.recurrence_count,
            surfaced_count=thought.surfaced_count,
            resolved=False,
            evidence=evidence,
            risk_flags=risk_flags,
            created_at=thought.created_at,
            updated_at=now,
            last_reactivated_at=now,
        )

    def _log_model_review(self, review: dict[str, object], thought: ThoughtObject) -> None:
        if self.logger is None:
            return
        reviewer = self.model_reviewer
        log_enabled = bool(getattr(reviewer, "log_api_thoughts", _truthy_env("ERIS_THOUGHT_EVALUATOR_LOG_API_THOUGHTS", True)))
        if not log_enabled:
            return
        route = str(review.get("recommended_route") or "keep_private")
        conf = float(review.get("confidence") or 0.0)
        provider = str(review.get("provider") or getattr(getattr(reviewer, "config", None), "provider", ""))
        model = str(review.get("model") or getattr(getattr(reviewer, "config", None), "model", ""))
        self.logger.info(
            "[ThoughtEvaluator API] verdict route=%s conf=%.2f thought=%s provider=%s model=%s eval=%s",
            route,
            conf,
            thought.topic_seed,
            provider or "unknown",
            model or "unknown",
            _clean_text(review.get("dominant_evaluation") or review.get("judge_verdict"), limit=180),
        )
        prosecutor = _clean_text(review.get("prosecutor_case"), limit=220)
        defense = _clean_text(review.get("defense_case"), limit=220)
        judge = _clean_text(review.get("judge_verdict"), limit=220)
        if prosecutor:
            self.logger.info("[ThoughtEvaluator API] prosecutor_piece: %s", prosecutor)
        if defense:
            self.logger.info("[ThoughtEvaluator API] defense_piece: %s", defense)
        if judge:
            self.logger.info("[ThoughtEvaluator API] judge_piece: %s", judge)

    def _anchors(
        self,
        *,
        snapshot,
        last_text: str,
        source_memory: str,
        disturbances: tuple[CognitiveDisturbance, ...] = (),
    ) -> tuple[str, ...]:
        user_signals = getattr(snapshot, "user_signals", None)
        return tuple(
            item
            for item in (
                getattr(snapshot, "active_window_kind", ""),
                getattr(user_signals, "focus_pattern", "") if user_signals is not None else "",
                last_text,
                source_memory,
                *(
                    str(getattr(disturbance, "content", "") or "")
                    for disturbance in tuple(disturbances or ())[:4]
                ),
            )
            if _clean_text(item)
        )

    def _continuity_notes(
        self,
        thought: ThoughtObject,
        *,
        surfaced: bool,
        momentum: CognitiveMomentum | None = None,
        active_attractor: UnresolvedTensionLoop | None = None,
        closed_ids: tuple[str, ...] = (),
        disturbances: tuple[CognitiveDisturbance, ...] = (),
        extra_notes: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        notes = [
            "thought_stream=unfinished_prediction",
            f"cognitive_tension={thought.tension:.2f}",
            f"unresolved_reason={thought.unresolved_reason}",
        ]
        if disturbances:
            top = max(tuple(disturbances), key=lambda item: _clamp01(getattr(item, "intensity", 0.0)))
            notes.append(
                "disturbance="
                f"{str(getattr(top, 'channel', '') or '')}:{str(getattr(top, 'kind', '') or '')}"
            )
        if thought.recurrence_count:
            notes.append(f"recursive_reentry={thought.recurrence_count}")
        if momentum is not None and momentum.affordance:
            notes.append(f"cognitive_momentum={momentum.score:.2f}")
            notes.append(f"momentum_affordance={momentum.affordance}")
        if active_attractor is not None and active_attractor.attractor:
            notes.append(f"unresolved_attractor={active_attractor.score:.2f}")
        if closed_ids:
            notes.append(f"momentum_closed={len(closed_ids)}")
        notes.extend(str(item) for item in tuple(extra_notes or ()) if str(item).strip())
        notes.append("surface_selected" if surfaced else "kept_internal")
        return tuple(notes[-7:])


class ThoughtStream:
    """Background producer of thought-stream surfaces; most ticks stay internal."""

    def __init__(
        self,
        *,
        context_provider: Callable[[], dict[str, object]],
        engine: ThoughtStreamEngine | None = None,
        interval_seconds: float | None = None,
        enabled: bool | None = None,
        logger=None,
        model_reviewer=None,
    ):
        if interval_seconds is None:
            interval_seconds = self._load_interval()
        if enabled is None:
            enabled = self._load_enabled()
        self.context_provider = context_provider
        self.engine = resolve_dependency(engine, lambda: ThoughtStreamEngine(model_reviewer=model_reviewer, logger=logger))
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.enabled = bool(enabled)
        self.logger = logger
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._queued: list[ThoughtSurface] = []
        self._pending_disturbances: list[CognitiveDisturbance] = []
        self._recent: tuple[ThoughtSurface, ...] = ()
        self._latest_report = ThoughtStreamReport()

    def start(self) -> None:
        if not self.enabled or self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="ERIS-ThoughtStream", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def inject_disturbance(
        self,
        disturbance: CognitiveDisturbance,
        *,
        immediate: bool = True,
        queue_surface: bool = False,
    ) -> ThoughtStreamReport:
        if not isinstance(disturbance, CognitiveDisturbance):
            disturbance = CognitiveDisturbance(
                channel=str(getattr(disturbance, "channel", "") or ""),
                kind=str(getattr(disturbance, "kind", "") or ""),
                content=str(getattr(disturbance, "content", "") or ""),
                evidence=tuple(str(item) for item in (getattr(disturbance, "evidence", ()) or ())),
                intensity=_clamp01(getattr(disturbance, "intensity", 1.0)),
                timestamp=float(getattr(disturbance, "timestamp", 0.0) or 0.0),
                closes_loop=bool(getattr(disturbance, "closes_loop", False)),
            )
        with self._lock:
            self._pending_disturbances.append(disturbance)
            self._pending_disturbances = self._pending_disturbances[-8:]
        if immediate:
            return self.tick_once(queue_surface=queue_surface)
        return self.latest_report()

    def inject_disturbances(
        self,
        disturbances: tuple[CognitiveDisturbance, ...],
        *,
        immediate: bool = True,
        queue_surface: bool = False,
    ) -> ThoughtStreamReport:
        with self._lock:
            self._pending_disturbances.extend(tuple(disturbances or ()))
            self._pending_disturbances = self._pending_disturbances[-8:]
        if immediate:
            return self.tick_once(queue_surface=queue_surface)
        return self.latest_report()

    def tick_once(self, *, queue_surface: bool = True) -> ThoughtStreamReport:
        if not self.enabled:
            with self._lock:
                pending_disturbances = tuple(self._pending_disturbances)
                self._pending_disturbances.clear()
                report = ThoughtStreamReport(suppression_reason="disabled", disturbances=pending_disturbances)
                self._latest_report = report
            return report
        try:
            context = self.context_provider()
            with self._lock:
                pending_disturbances = tuple(self._pending_disturbances)
                self._pending_disturbances.clear()
            if pending_disturbances:
                context = dict(context)
                context["disturbances"] = (*tuple(context.get("disturbances") or ()), *pending_disturbances)
            report = self.engine.evaluate(**context)
        except Exception as exc:
            report = ThoughtStreamReport(suppression_reason=f"error:{type(exc).__name__}")
            if self.logger is not None:
                self.logger.warning("Thought stream tick error: %s", exc)
        with self._lock:
            surface = report.latest_seed
            if queue_surface and surface.mode and surface.topic_seed:
                self._recent = (*self._recent, surface)[-8:]
                self._queued.append(surface)
                self._queued = self._queued[-4:]
                report = ThoughtStreamReport(
                    latest_seed=surface,
                    recent_seeds=self._recent,
                    surface_pressure=report.surface_pressure,
                    suppression_reason=report.suppression_reason,
                    latest_thought=report.latest_thought,
                    recent_thoughts=report.recent_thoughts,
                    momentum=report.momentum,
                    recent_momentum=report.recent_momentum,
                    active_attractor=report.active_attractor,
                    unresolved_loops=report.unresolved_loops,
                    stream_tension=report.stream_tension,
                    unresolved_count=report.unresolved_count,
                    continuity_notes=report.continuity_notes,
                    disturbances=report.disturbances,
                )
            self._latest_report = report
            return report

    def pop_surface(self) -> ThoughtSurface:
        with self._lock:
            if not self._queued:
                return ThoughtSurface()
            return self._queued.pop(0)

    def pop_surface_seed(self) -> ThoughtSurface:
        return self.pop_surface()

    def latest_report(self) -> ThoughtStreamReport:
        with self._lock:
            return self._latest_report

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.tick_once()
            self._stop_event.wait(self.interval_seconds)

    def _load_interval(self) -> float:
        raw = (
            os.environ.get("ERIS_THOUGHT_STREAM_INTERVAL_SEC")
            or os.environ.get("ERIS_SUBCONSCIOUS_INTERVAL_SEC", "45")
        ).strip()
        try:
            return float(raw)
        except ValueError:
            return 45.0

    def _load_enabled(self) -> bool:
        flag = (
            os.environ.get("ERIS_THOUGHT_STREAM_LOOP")
            or os.environ.get("ERIS_SUBCONSCIOUS_LOOP", "1")
        ).strip().lower()
        return flag not in {"0", "false", "no", "off"}


SubconsciousEngine = ThoughtStreamEngine
SubconsciousLoop = ThoughtStream
CognitiveStreamEngine = ThoughtStreamEngine
CognitiveStream = ThoughtStream
ContinuousCognitionSubstrate = ThoughtStream

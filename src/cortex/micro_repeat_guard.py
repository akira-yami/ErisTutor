from __future__ import annotations

from collections import deque
import time

from .micro_contracts import (
    SemanticRepeatResult,
    clean_family,
    clamp01,
    family_source_is_trusted,
    lexical_fingerprint,
    normalize_text,
    phrase_in_normalized,
)
from .banned_filler import BANNED_FILLER_LINES

try:
    from runtime.liveliness_config import load_liveliness_config
except Exception:  # pragma: no cover - allow import in partial test envs
    load_liveliness_config = None  # type: ignore[assignment]

try:  # Optional: semantic guard should degrade, not drag boot down with it.
    from .semantic_encoder import (
        BaseSemanticEncoder,
        LexicalHashingSemanticEncoder,
        cosine_similarity,
    )
except Exception:  # pragma: no cover - import fallback for partial test envs.
    BaseSemanticEncoder = None  # type: ignore[assignment]
    LexicalHashingSemanticEncoder = None  # type: ignore[assignment]
    cosine_similarity = None  # type: ignore[assignment]


class SemanticRepeatGuard:
    """Chặn lặp semantic family, nhưng không để keyword fallback cầm lái.

    Luật mới:
    1. Nếu tầng trên truyền semantic_family đáng tin, repeat guard được quyền
       block trong cửa sổ gần.
    2. Nếu không có semantic_family, fallback keyword chỉ tạo hint yếu.
    3. Exact lexical fingerprint lặp quá gần có thể block, vì đó là lặp câu bề
       mặt chứ không phải suy diễn meaning.

    Nói cách khác: MeaningLayer được nói "đây là cùng family". Keyword fallback
    chỉ được ho nhẹ ở góc phòng. Một trật tự xã hội hiếm hoi có lý.
    """

    FAMILY_MARKERS: dict[str, tuple[str, ...]] = {
        "ambient_presence_status_ping": (
            "khoang yen", "nhip nay", "van giu cho", "yen them chut",
            "im hoi lau", "toi van o day", "toi o day", "van dang nghe",
            "van dang quan sat", "giu cho", "o day them chut",
        ),
        "private_mind_continuity_filler": (
            "mach nay van giu cho", "khoang yen nay van giu cho",
            "toi van giu nhip", "van con o day", "khoang nay chua dut",
            "mach van con", "mach nay van con", "van giu nhip",
            "m ch n y v n gi ch", "kho ng y n n y v n gi ch",
            "t i v n gi nh p", "v n c n y", "kho ng n y ch a t",
            "m ch v n c n",
        ),
        "code_stalled": (
            "code", "debug", "loi", "traceback", "terminal", "python", "vscode",
            "visual studio", "mach code",
        ),
        "screen_still": (
            "man hinh", "screen", "cua so", "window", "tab", "khung",
        ),
        "quiet_still": (
            "im", "yen", "dung", "nam", "treo", "cho", "nhap nhay",
        ),
        "awake_presence": (
            "thuc", "khuya", "dem", "mat ngu",
        ),
        "chat_idle": (
            "chat", "tin nhan", "nhan tin", "khung chat",
        ),
        "meeting_idle": (
            "meet", "meeting", "cuoc goi", "phong hop",
        ),
        "log_meta": (
            "log", "dong log",
        ),
        "ambient_residue": (
            "nhip", "du am", "lang lai", "yen xuong", "doi nhip",
        ),
    }

    SEMANTIC_PROTOTYPES: dict[str, tuple[str, ...]] = {
        "ambient_presence_status_ping": tuple(
            dict.fromkeys(
                (
                    *BANNED_FILLER_LINES,
                    "Im thêm chút cũng được.",
                    "Tôi vẫn đang quan sát thôi.",
                    "Nhịp im này vẫn còn đó.",
                )
            )
        ),
    }

    SEMANTIC_PROTOTYPES["private_mind_continuity_filler"] = (
        "Mach nay van giu cho.",
        "Khoang yen nay van giu cho.",
        "Toi van giu nhip.",
        "Van con o day.",
        "Khoang nay chua dut.",
        "Mach van con.",
    )

    SYNONYMS: tuple[tuple[str, str], ...] = (
        ("ma nguon", "code"),
        ("lap trinh", "code"),
        ("monitor", "man hinh"),
        ("dung im", "im"),
        ("nam im", "im"),
        ("treo may", "treo"),
        ("van mo", "treo"),
        ("con thuc", "thuc"),
        ("thuc khuya", "thuc"),
        ("nhan message", "tin nhan"),
    )

    def __init__(
        self,
        *,
        max_items: int = 20,
        window_seconds: float | None = None,
        exact_repeat_seconds: float | None = None,
        semantic_encoder: object | None = None,
        semantic_threshold: float | None = None,
    ):
        # Defaults come from LivelinessConfig so the exact-repeat window is at
        # least the max idle interval (idle cycles are 60-120s apart, so a 90s
        # window let consecutive repeats slip). Explicit args still override.
        cfg = None
        if load_liveliness_config is not None:
            try:
                cfg = load_liveliness_config()
            except Exception:  # pragma: no cover - never break the guard on config
                cfg = None
        cfg_repeat_window = float(getattr(cfg, "repeat_window_sec", 180.0) or 180.0)
        cfg_threshold = float(getattr(cfg, "sim_threshold", 0.78) or 0.78)

        if window_seconds is None:
            window_seconds = max(240.0, cfg_repeat_window)
        if exact_repeat_seconds is None:
            exact_repeat_seconds = cfg_repeat_window
        if semantic_threshold is None:
            semantic_threshold = cfg_threshold

        self.max_items = max(10, min(40, int(max_items or 20)))
        self.window_seconds = max(90.0, min(600.0, float(window_seconds or 240.0)))
        self.exact_repeat_seconds = max(20.0, min(self.window_seconds, float(exact_repeat_seconds or 90.0)))
        self._items: deque[tuple[float, str, str, str, float, str]] = deque(maxlen=self.max_items)
        self.semantic_encoder = self._coerce_semantic_encoder(semantic_encoder)
        self.semantic_threshold = max(0.50, min(0.94, float(semantic_threshold or 0.78)))
        self._semantic_prototype_vectors: dict[str, tuple[tuple[float, ...], ...]] = {}
        self._semantic_prototype_ready = False

    def remember(
        self,
        text: str,
        *,
        timestamp: float | None = None,
        semantic_family: str = "",
        family_source: str = "",
        confidence: float = 1.0,
    ) -> None:
        """Lưu family/fingerprint của câu vừa nói.

        semantic_family từ tầng trên được lưu là trusted nếu source hợp lệ. Nếu
        không có, ta chỉ lưu fallback_keyword + fingerprint để phát hiện câu lặp
        bề mặt, không tạo quyền block meaning.
        """

        family, source, score = self._resolve_family(
            text,
            semantic_family=semantic_family,
            family_source=family_source,
            confidence=confidence,
        )
        fingerprint = lexical_fingerprint(text)
        if not family and not fingerprint:
            return
        self._items.append((
            time.time() if timestamp is None else float(timestamp),
            str(text or ""),
            family,
            source,
            score,
            fingerprint,
        ))

    def check(
        self,
        text: str,
        *,
        now: float | None = None,
        recent_lines: tuple[tuple[float, str], ...] = (),
        semantic_family: str = "",
        meaning_family: str = "",
        family_source: str = "",
        semantic_confidence: float = 1.0,
    ) -> SemanticRepeatResult:
        """Kiểm tra lặp.

        Backward compatible: call-site cũ chỉ truyền text vẫn chạy. Nhưng khi chỉ
        có fallback keyword, hàm trả weak hint thay vì rejected=True.
        """

        family, source, score = self._resolve_family(
            text,
            semantic_family=semantic_family or meaning_family,
            family_source=family_source or ("meaning_layer" if meaning_family else ""),
            confidence=semantic_confidence,
        )
        fingerprint = lexical_fingerprint(text)
        current_time = time.time() if now is None else float(now)
        trusted_current = family_source_is_trusted(source) and score >= 0.45

        weak_family_seen = False
        for timestamp, _line, recent_family, recent_source, recent_confidence, recent_fingerprint in self._iter_recent(recent_lines):
            age = current_time - timestamp
            if age > self.window_seconds:
                continue

            # Exact/gần exact repeat là vấn đề bề mặt, không phải suy diễn meaning.
            if fingerprint and recent_fingerprint == fingerprint and age <= self.exact_repeat_seconds:
                return SemanticRepeatResult(
                    rejected=True,
                    family=family or recent_family,
                    reason="exact_micro_line_repeat",
                    family_source="lexical_fingerprint",
                    confidence=0.72,
                    weak=False,
                    fingerprint=fingerprint,
                )

            if family and recent_family == family:
                trusted_recent = family_source_is_trusted(recent_source) and recent_confidence >= 0.45
                if trusted_current and trusted_recent:
                    return SemanticRepeatResult(
                        rejected=True,
                        family=family,
                        reason=f"semantic_repeat_family={family}",
                        family_source=source,
                        confidence=min(score, recent_confidence),
                        weak=False,
                        fingerprint=fingerprint,
                    )
                if age <= self.window_seconds:
                    return SemanticRepeatResult(
                        rejected=True,
                        family=family,
                        reason=f"semantic_repeat_family={family}",
                        family_source=source or recent_source or "fallback_keyword",
                        confidence=min(max(score, recent_confidence), 0.42),
                        weak=True,
                        fingerprint=fingerprint,
                    )
                weak_family_seen = True

        if family:
            return SemanticRepeatResult(
                rejected=False,
                family=family,
                reason="weak_keyword_family_only" if weak_family_seen or source == "fallback_keyword" else "",
                family_source=source,
                confidence=score,
                weak=not trusted_current,
                fingerprint=fingerprint,
            )
        return SemanticRepeatResult(fingerprint=fingerprint)

    def family_for_text(self, text: str) -> str:
        """Backward compatible fallback family.

        Hàm này vẫn tồn tại cho code cũ/test cũ, nhưng đừng dùng nó làm chân lý.
        Muốn biết nguồn yếu/mạnh, dùng family_for_text_with_source().
        """

        family, _source, _confidence = self.family_for_text_with_source(text)
        return family

    def family_for_text_with_source(self, text: str) -> tuple[str, str, float]:
        normalized = self.normalize(text)
        if not normalized:
            return "", "unknown", 0.0
        for source, target in self.SYNONYMS:
            if phrase_in_normalized(normalized, source):
                normalized = normalized.replace(source, target)
        matched: list[tuple[str, int, str, float]] = []
        for family, markers in self.FAMILY_MARKERS.items():
            hits = sum(1 for marker in markers if phrase_in_normalized(normalized, marker))
            if not hits:
                continue
            if family == "ambient_presence_status_ping" and hits >= 2:
                matched.append((family, hits, "manual", min(0.72, 0.52 + hits * 0.06)))
            else:
                matched.append((family, hits, "fallback_keyword", min(0.42, 0.22 + hits * 0.08)))
        if matched:
            # Prefer a concrete domain marker over a generic ambient/status marker
            # when both are present. "Đoạn code này im" is code_stalled first,
            # not a decorative status ping. Shocking that words have context.
            priority = {
                "code_stalled": 4,
                "meeting_idle": 4,
                "log_meta": 4,
                "chat_idle": 3,
                "screen_still": 2,
                "private_mind_continuity_filler": 2,
                "ambient_presence_status_ping": 1,
                "quiet_still": 0,
            }
            family, hits, source, score = max(
                matched,
                key=lambda item: (priority.get(item[0], 1), item[1], item[3]),
            )
            return family, source, score
        semantic = self._semantic_family_for_text(text)
        if semantic:
            return semantic
        return "", "unknown", 0.0

    def novelty_score(
        self,
        text: str,
        *,
        recent_lines: tuple[str, ...] | tuple[tuple[float, str], ...] = (),
        include_banned_seed: bool = True,
    ) -> float:
        """Điểm tươi mới liên tục trong [0,1]: 1 = mới, 0 = trùng đồ cũ/đã nói.

        Đây là phiên bản 'penalty' thay cho veto blocklist: thay vì cấm đúng
        chuỗi ký tự, ta đo độ GẦN NGHĨA của câu ứng viên với (a) các câu ERIS
        vừa nói và (b) kho 'stale seed' nạp từ BANNED_FILLER_LINES. Biến thể lách
        chữ vẫn bị bắt vì gần nghĩa, không cần liệt kê.

        Caller dùng điểm này như một lực phạt mềm trong arbiter, không phải cổng
        nhị phân. Encoder vắng/fallback → tự hạ về overlap token (vẫn hữu dụng).
        """
        candidate = str(text or "").strip()
        if not candidate:
            return 1.0

        # Gom corpus tham chiếu: câu gần đây + (tùy chọn) stale seed.
        references: list[str] = []
        for item in recent_lines or ():
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                references.append(str(item[1] or ""))
            else:
                references.append(str(item or ""))
        # Câu đã nhớ trong guard cũng là 'đã nói gần đây'.
        for stored in tuple(self._items):
            references.append(str(stored[1] or ""))
        if include_banned_seed:
            references.extend(BANNED_FILLER_LINES)

        references = [ref for ref in references if ref.strip()]
        if not references:
            return 1.0

        best_similarity = self._max_similarity(candidate, references)
        return max(0.0, min(1.0, 1.0 - best_similarity))

    def _max_similarity(self, candidate: str, references: list[str]) -> float:
        """Độ tương đồng cao nhất của candidate với tập references."""
        encoder = self.semantic_encoder
        # Đường semantic: chỉ tin khi encoder thật (không fallback).
        if encoder is not None and cosine_similarity is not None:
            try:
                encoded = encoder.encode(candidate)  # type: ignore[attr-defined]
                vector = tuple(getattr(encoded, "vector", ()) or ())
                health = encoder.health() if callable(getattr(encoder, "health", None)) else None  # type: ignore[attr-defined]
                is_fallback = bool(getattr(health, "is_fallback", getattr(encoded, "is_fallback", False)))
                if vector and not is_fallback:
                    best = 0.0
                    for ref in references:
                        ref_vec = tuple(getattr(encoder.encode(ref), "vector", ()) or ())  # type: ignore[attr-defined]
                        if ref_vec:
                            best = max(best, float(cosine_similarity(vector, ref_vec)))
                    return max(0.0, min(1.0, best))
            except Exception:
                pass
        # Fallback từ vựng: Jaccard trên token đã chuẩn hóa + trùng fingerprint.
        return self._lexical_similarity(candidate, references)

    def _lexical_similarity(self, candidate: str, references: list[str]) -> float:
        cand_fp = lexical_fingerprint(candidate)
        cand_tokens = set(self.normalize(candidate).split())
        if not cand_tokens:
            return 0.0
        best = 0.0
        for ref in references:
            if cand_fp and lexical_fingerprint(ref) == cand_fp:
                return 1.0
            ref_tokens = set(self.normalize(ref).split())
            if not ref_tokens:
                continue
            overlap = len(cand_tokens & ref_tokens)
            union = len(cand_tokens | ref_tokens)
            if union:
                best = max(best, overlap / union)
        return max(0.0, min(1.0, best))

    def _coerce_semantic_encoder(self, encoder: object | None):
        if encoder is not None:
            return encoder
        if LexicalHashingSemanticEncoder is None:
            return None
        # Hashing fallback không được tự nhận là hiểu nghĩa thật, nhưng nó cho
        # prototype clustering deterministic trong dev/test. Confidence sẽ bị
        # cap thấp nếu health báo is_fallback=True.
        try:
            return LexicalHashingSemanticEncoder()
        except Exception:
            return None

    def _ensure_semantic_prototypes(self) -> None:
        if self._semantic_prototype_ready:
            return
        self._semantic_prototype_ready = True
        encoder = self.semantic_encoder
        if encoder is None or cosine_similarity is None:
            return
        try:
            for family, samples in self.SEMANTIC_PROTOTYPES.items():
                vectors = []
                for sample in samples:
                    encoded = encoder.encode(sample)  # type: ignore[attr-defined]
                    vector = tuple(getattr(encoded, "vector", ()) or ())
                    if vector:
                        vectors.append(vector)
                if vectors:
                    # Dùng sample vectors trực tiếp thay vì centroid để tránh hash
                    # fallback làm mờ những câu ngắn.
                    self._semantic_prototype_vectors[family] = tuple(vectors)
        except Exception:
            self._semantic_prototype_vectors = {}

    def _semantic_family_for_text(self, text: str) -> tuple[str, str, float]:
        encoder = self.semantic_encoder
        if encoder is None or cosine_similarity is None:
            return "", "unknown", 0.0
        self._ensure_semantic_prototypes()
        if not self._semantic_prototype_vectors:
            return "", "unknown", 0.0
        try:
            encoded = encoder.encode(text)  # type: ignore[attr-defined]
            vector = tuple(getattr(encoded, "vector", ()) or ())
            if not vector:
                return "", "unknown", 0.0
            health = encoder.health() if callable(getattr(encoder, "health", None)) else None  # type: ignore[attr-defined]
            is_fallback = bool(getattr(health, "is_fallback", getattr(encoded, "is_fallback", False)))
            best_family = ""
            best_score = 0.0
            for family, prototype_vectors in self._semantic_prototype_vectors.items():
                for proto in prototype_vectors:  # type: ignore[assignment]
                    score = float(cosine_similarity(vector, proto))
                    if score > best_score:
                        best_score = score
                        best_family = family
            if not best_family or best_score < self.semantic_threshold:
                return "", "unknown", 0.0
            if is_fallback:
                return best_family, "fallback_keyword", min(0.44, best_score * 0.48)
            return best_family, "semantic_encoder", min(0.88, best_score)
        except Exception:
            return "", "unknown", 0.0

    def normalize(self, text: str) -> str:
        return normalize_text(text)

    def _resolve_family(
        self,
        text: str,
        *,
        semantic_family: str = "",
        family_source: str = "",
        confidence: float = 1.0,
    ) -> tuple[str, str, float]:
        family = clean_family(semantic_family)
        source = str(family_source or "").strip().lower()
        score = clamp01(confidence)
        if family:
            if not source:
                source = "manual"
            if family_source_is_trusted(source):
                return family, source, max(score, 0.55)
            return family, source or "unknown", min(score, 0.44)
        return self.family_for_text_with_source(text)

    def _iter_recent(self, recent_lines: tuple[tuple[float, str], ...]):
        for item in tuple(self._items):
            yield item
        for timestamp, line in tuple(recent_lines or ()):  # external history từ coordinator nếu có.
            family, source, confidence = self.family_for_text_with_source(line)
            yield float(timestamp), str(line or ""), family, source, confidence, lexical_fingerprint(line)

    def _clean_family(self, family: str) -> str:
        # Giữ wrapper cũ để code ngoài nếu lỡ gọi private vẫn ít vỡ hơn. Tuyệt vời,
        # giờ ta bảo vệ cả private API, vì con người rất thích dùng nhầm đồ riêng.
        return clean_family(family)

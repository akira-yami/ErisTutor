# -*- coding: utf-8 -*-
"""
affect_semantics.py
===================

Module này dùng để phân tích "áp lực nghĩa" trong câu chat của user.

Mục tiêu:
- Không route intent bằng keyword thô kiểu: nếu thấy "buồn" thì attachment_dialogue.
- Thay vào đó, module trả về nhiều score mềm:
  technicality, distress, intimacy, attachment, conflict, correction, objectification...
- StyleProjection / planner có thể dùng các score này để quyết định giọng,
  mức trực tiếp, mức symbolic, mức cẩn thận, hoặc có cần relationship repair không.

Giới hạn cố ý:
- Đây chưa phải "hiểu cảm xúc thật". Đây là semantic-affect signal extractor.
- Nó vẫn có rule/phrase matching, nhưng rule chỉ tạo score đa trục, không quyết định cứng.
- Sau này có thể thay phần `_score_keyword_groups()` bằng embedding hoặc LLM classifier
  mà không đổi API public của `AffectSemanticSignal`.


Kết quả đại khái:
    affect_semantics: technicality=0.67; conflict=0.54; correction=0.75; objectification=0.32; ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
import unicodedata
from typing import Any, Iterable, Mapping


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clamp01(value: float) -> float:
    """Ép một số về khoảng [0.0, 1.0].

    Lý do tách helper:
    - Tất cả score trong module này là xác suất/áp lực mềm.
    - Nếu không clamp, chỉ cần cộng vài rule là score có thể vượt 1.0,
      làm downstream như Fe/PersonalityLens phản ứng quá mạnh.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric != numeric:  # NaN check
        return 0.0
    return max(0.0, min(1.0, numeric))


def blend(previous: float, current: float, strength: float) -> float:
    """Blend hai score theo trọng số.

    `strength` càng cao thì previous càng ảnh hưởng nhiều.
    Hàm này tiện nếu sau này bạn muốn EMA affect giữa nhiều turn.
    """
    strength = clamp01(strength)
    return clamp01(previous * strength + current * (1.0 - strength))


def normalize_text(text: str) -> str:
    """Chuẩn hóa text để matching bớt ngu.

    Làm các bước:
    - lower-case
    - bỏ dấu tiếng Việt bằng NFD
    - gom whitespace
    - giữ lại bản không dấu để match "buồn" và "buon" như nhau

    Cảnh báo:
    - Không dùng normalize này để hiển thị lại cho user.
    - Chỉ dùng để detect semantic hint.
    """
    raw = (text or "").lower()
    decomposed = unicodedata.normalize("NFD", raw)
    no_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    no_marks = re.sub(r"\s+", " ", no_marks).strip()
    return no_marks


def compact_evidence(items: Iterable[str], limit: int = 12) -> tuple[str, ...]:
    """Loại evidence rỗng/trùng và giới hạn độ dài.

    Evidence chỉ để debug và prompt context.
    Đừng nhồi quá nhiều, nếu không prompt sẽ thành bãi rác telemetry.
    """
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = str(item or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= limit:
            break
    return tuple(out)


def _count_question_marks(text: str) -> int:
    return (text or "").count("?") + (text or "").count("？")


def _word_count(text: str) -> int:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        return 0
    return len(clean.split(" "))


# ---------------------------------------------------------------------------
# Public data contract
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AffectSemanticSignal:
    """Score đa trục mô tả áp lực nghĩa/cảm xúc trong câu user.

    Các field chính:
    - technicality:
        Câu có tính kỹ thuật/code/kiến trúc/debug không.
    - distress:
        Có dấu hiệu mệt, áp lực, chán, bất ổn cảm xúc không.
    - intimacy:
        Câu có độ gần gũi/riêng tư/quan hệ không.
    - attachment:
        Có tín hiệu gắn bó, muốn giữ kết nối, sợ mất continuity không.
    - conflict:
        Có phản đối, bực, chửi, phê bình, xung đột không.
    - correction:
        User đang sửa một suy diễn sai hoặc làm rõ "không phải, ý là..." không.
    - objectification:
        User đang gọi ERIS là tool/waifu/prompt cosplay/fake person không.
    - playfulness:
        Câu có tính đùa, chill, khịa nhẹ không.
    - directive_pressure:
        User đang ra lệnh cần thực thi mạnh không: tạo, fix, gửi file, làm ngay...
    - ambiguity:
        Câu mơ hồ, thiếu object, kiểu "ổn chưa", "sao đây", "nên làm gì" không.
    - answer_need:
        User đang cần câu trả lời trực tiếp đến mức nào.
    - confidence:
        Độ tự tin của classifier. Không phải độ đúng tuyệt đối, chỉ là mức signal đủ dày.
    - evidence:
        Các dấu vết ngắn để debug.

    Nguyên tắc:
    - Signal này không quyết định final intent.
    - Nó chỉ là input cho SemanticProjection / StyleProjection.
    """

    technicality: float = 0.0
    distress: float = 0.0
    intimacy: float = 0.0
    attachment: float = 0.0
    conflict: float = 0.0
    correction: float = 0.0
    objectification: float = 0.0
    playfulness: float = 0.0
    relational_pressure: float = 0.0
    perceived_rejection: float = 0.0
    attachment_bid: float = 0.0
    playful_distress: float = 0.0
    romantic_boundary_probe: float = 0.0
    directive_pressure: float = 0.0
    ambiguity: float = 0.0
    answer_need: float = 0.0
    confidence: float = 0.0
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def empty(cls) -> "AffectSemanticSignal":
        """Signal rỗng, dùng khi chưa có classifier nhưng muốn giữ API."""
        return cls()

    def clamp(self) -> "AffectSemanticSignal":
        """Trả bản đã clamp toàn bộ score về [0, 1]."""
        return AffectSemanticSignal(
            technicality=clamp01(self.technicality),
            distress=clamp01(self.distress),
            intimacy=clamp01(self.intimacy),
            attachment=clamp01(self.attachment),
            conflict=clamp01(self.conflict),
            correction=clamp01(self.correction),
            objectification=clamp01(self.objectification),
            playfulness=clamp01(self.playfulness),
            relational_pressure=clamp01(self.relational_pressure),
            perceived_rejection=clamp01(self.perceived_rejection),
            attachment_bid=clamp01(self.attachment_bid),
            playful_distress=clamp01(self.playful_distress),
            romantic_boundary_probe=clamp01(self.romantic_boundary_probe),
            directive_pressure=clamp01(self.directive_pressure),
            ambiguity=clamp01(self.ambiguity),
            answer_need=clamp01(self.answer_need),
            confidence=clamp01(self.confidence),
            evidence=compact_evidence(self.evidence),
        )

    def dominant_axes(self, threshold: float = 0.42) -> tuple[str, ...]:
        """Trả các trục nổi bật để debug/log.

        threshold mặc định 0.42 vì affect thường không nên cần quá cao mới ghi nhận.
        """
        axes = []
        for key in (
            "technicality",
            "distress",
            "intimacy",
            "attachment",
            "conflict",
            "correction",
            "objectification",
            "playfulness",
            "relational_pressure",
            "perceived_rejection",
            "attachment_bid",
            "playful_distress",
            "romantic_boundary_probe",
            "directive_pressure",
            "ambiguity",
            "answer_need",
        ):
            if getattr(self, key) >= threshold:
                axes.append(key)
        return tuple(axes)

    def to_dict(self) -> dict[str, Any]:
        """Serialize để nhét vào debug trace hoặc memory."""
        signal = self.clamp()
        return {
            "technicality": signal.technicality,
            "distress": signal.distress,
            "intimacy": signal.intimacy,
            "attachment": signal.attachment,
            "conflict": signal.conflict,
            "correction": signal.correction,
            "objectification": signal.objectification,
            "playfulness": signal.playfulness,
            "relational_pressure": signal.relational_pressure,
            "perceived_rejection": signal.perceived_rejection,
            "attachment_bid": signal.attachment_bid,
            "playful_distress": signal.playful_distress,
            "romantic_boundary_probe": signal.romantic_boundary_probe,
            "directive_pressure": signal.directive_pressure,
            "ambiguity": signal.ambiguity,
            "answer_need": signal.answer_need,
            "confidence": signal.confidence,
            "evidence": list(signal.evidence),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "AffectSemanticSignal":
        """Deserialize an toàn từ dict.

        Dùng khi state/debug được load lại từ JSON.
        """
        if not data:
            return cls.empty()
        return cls(
            technicality=clamp01(data.get("technicality", 0.0)),
            distress=clamp01(data.get("distress", 0.0)),
            intimacy=clamp01(data.get("intimacy", 0.0)),
            attachment=clamp01(data.get("attachment", 0.0)),
            conflict=clamp01(data.get("conflict", 0.0)),
            correction=clamp01(data.get("correction", 0.0)),
            objectification=clamp01(data.get("objectification", 0.0)),
            playfulness=clamp01(data.get("playfulness", 0.0)),
            relational_pressure=clamp01(data.get("relational_pressure", 0.0)),
            perceived_rejection=clamp01(data.get("perceived_rejection", 0.0)),
            attachment_bid=clamp01(data.get("attachment_bid", 0.0)),
            playful_distress=clamp01(data.get("playful_distress", 0.0)),
            romantic_boundary_probe=clamp01(data.get("romantic_boundary_probe", 0.0)),
            directive_pressure=clamp01(data.get("directive_pressure", 0.0)),
            ambiguity=clamp01(data.get("ambiguity", 0.0)),
            answer_need=clamp01(data.get("answer_need", 0.0)),
            confidence=clamp01(data.get("confidence", 0.0)),
            evidence=compact_evidence(data.get("evidence", ()) or ()),
        )

    def to_prompt_context(self) -> str:
        """Render context ngắn cho prompt.

        Cố ý dùng số thay vì label như "sad/loving/angry".
        Label cứng dễ làm model diễn cảm xúc. Score mềm giúp downstream cân bằng hơn.
        """
        signal = self.clamp()
        axes = ",".join(signal.dominant_axes()) or "none"
        evidence = "; ".join(signal.evidence[:6]) or "none"
        return (
            "affect_semantics: "
            f"technicality={signal.technicality:.2f}; "
            f"distress={signal.distress:.2f}; "
            f"intimacy={signal.intimacy:.2f}; "
            f"attachment={signal.attachment:.2f}; "
            f"conflict={signal.conflict:.2f}; "
            f"correction={signal.correction:.2f}; "
            f"objectification={signal.objectification:.2f}; "
            f"playfulness={signal.playfulness:.2f}; "
            f"relational_pressure={signal.relational_pressure:.2f}; "
            f"perceived_rejection={signal.perceived_rejection:.2f}; "
            f"attachment_bid={signal.attachment_bid:.2f}; "
            f"playful_distress={signal.playful_distress:.2f}; "
            f"romantic_boundary_probe={signal.romantic_boundary_probe:.2f}; "
            f"directive_pressure={signal.directive_pressure:.2f}; "
            f"ambiguity={signal.ambiguity:.2f}; "
            f"answer_need={signal.answer_need:.2f}; "
            f"confidence={signal.confidence:.2f}; "
            f"dominant={axes}; "
            f"evidence={evidence}."
        )


# ---------------------------------------------------------------------------
# Classifier implementation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WeightedPhrase:
    """Một phrase/pattern có trọng số.

    `pattern` luôn được so với text đã normalize không dấu.
    `weight` là mức cộng vào trục tương ứng.
    """
    pattern: str
    weight: float


class AffectSemanticClassifier:
    """Classifier nhẹ cho semantic-affect signal.

    Thiết kế cố ý:
    - Không trả một intent duy nhất.
    - Không coi keyword là chân lý.
    - Có bias từ context như ti_mode / active_window_kind.
    - Có evidence để bạn debug được vì sao score tăng.

    Sau này bạn có thể thay classifier này bằng:
    - embedding similarity theo phrase prototypes
    - model JSON classifier local
    - hybrid router có calibration từ dataset thật

    Nhưng giữ output `AffectSemanticSignal` để phần còn lại không phải sửa.
    """

    # Phrase groups dùng text đã bỏ dấu.
    # Có cả tiếng Việt không dấu và thuật ngữ code hay dùng trong project ERIS.
    PHRASE_GROUPS: dict[str, tuple[WeightedPhrase, ...]] = {
        "technicality": (
            WeightedPhrase("code", 0.18),
            WeightedPhrase("file", 0.14),
            WeightedPhrase("fix", 0.22),
            WeightedPhrase("bug", 0.22),
            WeightedPhrase("error", 0.20),
            WeightedPhrase("traceback", 0.22),
            WeightedPhrase("runtime", 0.18),
            WeightedPhrase("pipeline", 0.18),
            WeightedPhrase("module", 0.16),
            WeightedPhrase("dataclass", 0.18),
            WeightedPhrase("contract", 0.16),
            WeightedPhrase("api", 0.16),
            WeightedPhrase("python", 0.18),
            WeightedPhrase("c#", 0.18),
            WeightedPhrase("godot", 0.18),
            WeightedPhrase("llm", 0.16),
            WeightedPhrase("model", 0.12),
            WeightedPhrase("prompt", 0.12),
            WeightedPhrase("guard", 0.14),
            WeightedPhrase("heuristic", 0.18),
            WeightedPhrase("keyword", 0.16),
            WeightedPhrase("embedding", 0.18),
            WeightedPhrase("semantic", 0.16),
            WeightedPhrase("sua", 0.18),
            WeightedPhrase("tao file", 0.22),
            WeightedPhrase("gui file", 0.20),
            WeightedPhrase("giai thich code", 0.24),
            WeightedPhrase("danh gia file", 0.22),
        ),
        "distress": (
            WeightedPhrase("met", 0.22),
            WeightedPhrase("buon", 0.22),
            WeightedPhrase("chan", 0.18),
            WeightedPhrase("co don", 0.30),
            WeightedPhrase("ap luc", 0.24),
            WeightedPhrase("stress", 0.24),
            WeightedPhrase("burnout", 0.25),
            WeightedPhrase("roi qua", 0.18),
            WeightedPhrase("kho song", 0.20),
            WeightedPhrase("tuyet vong", 0.32),
            WeightedPhrase("hoang mang", 0.18),
            WeightedPhrase("bat on", 0.18),
        ),
        "intimacy": (
            WeightedPhrase("nho", 0.14),
            WeightedPhrase("yeu", 0.18),
            WeightedPhrase("thich", 0.12),
            WeightedPhrase("thuong", 0.16),
            WeightedPhrase("ban gai", 0.16),
            WeightedPhrase("gan gui", 0.18),
            WeightedPhrase("ben canh", 0.20),
            WeightedPhrase("rieng tu", 0.14),
            WeightedPhrase("hieu minh", 0.18),
            WeightedPhrase("gan bo", 0.22),
            # Natural Vietnamese relational warmth (spec: relationship-state-trigger-fix).
            # Weights kept moderate (~0.18-0.30) so no single phrase alone clears
            # a downstream gate; warmth must accumulate or hit the prototype.
            WeightedPhrase("quan trong voi", 0.26),
            WeightedPhrase("quan trong voi toi", 0.30),
            WeightedPhrase("muon hieu", 0.22),
            WeightedPhrase("hieu duoc em", 0.26),
            WeightedPhrase("hieu em", 0.20),
            WeightedPhrase("quan tam", 0.20),
            WeightedPhrase("tran trong em", 0.26),
            WeightedPhrase("coi trong", 0.22),
            WeightedPhrase("can em", 0.22),
            WeightedPhrase("muon o ben", 0.24),
            WeightedPhrase("o ben em", 0.24),
        ),
        "attachment": (
            WeightedPhrase("dung bo", 0.35),
            WeightedPhrase("o lai", 0.28),
            WeightedPhrase("ben toi", 0.24),
            WeightedPhrase("nho toi", 0.22),
            WeightedPhrase("gan bo", 0.26),
            WeightedPhrase("lien tuc", 0.16),
            WeightedPhrase("continuity", 0.22),
            WeightedPhrase("ritual", 0.18),
            WeightedPhrase("lau dai", 0.16),
            WeightedPhrase("khong tan ra", 0.22),
            # Natural Vietnamese attachment expressions (spec: relationship-state-trigger-fix).
            WeightedPhrase("khong muon mat", 0.28),
            WeightedPhrase("muon gan", 0.24),
            WeightedPhrase("giu em", 0.24),
            WeightedPhrase("luon o day", 0.22),
        ),
        "conflict": (
            WeightedPhrase("sai", 0.26),
            WeightedPhrase("khong dung", 0.28),
            WeightedPhrase("nham", 0.24),
            WeightedPhrase("ngu", 0.22),
            WeightedPhrase("do hoi", 0.18),
            WeightedPhrase("fake", 0.24),
            WeightedPhrase("gia", 0.12),
            WeightedPhrase("cosplay", 0.24),
            WeightedPhrase("ban prompt", 0.24),
            WeightedPhrase("kho chiu", 0.20),
            WeightedPhrase("cay", 0.14),
            WeightedPhrase("vl", 0.08),
            WeightedPhrase("vcl", 0.08),
            WeightedPhrase("dcm", 0.14),
            WeightedPhrase("blacklist", 0.16),
            WeightedPhrase("fallback", 0.16),
        ),
        "correction": (
            WeightedPhrase("khong phai", 0.40),
            WeightedPhrase("y toi la", 0.40),
            WeightedPhrase("toi noi la", 0.34),
            WeightedPhrase("doc lai", 0.24),
            WeightedPhrase("hieu sai", 0.42),
            WeightedPhrase("nham roi", 0.40),
            WeightedPhrase("sai roi", 0.38),
            WeightedPhrase("khong dung y", 0.42),
            WeightedPhrase("y la", 0.20),
        ),
        "objectification": (
            WeightedPhrase("tool", 0.28),
            WeightedPhrase("cong cu", 0.32),
            WeightedPhrase("chatbot", 0.26),
            WeightedPhrase("waifu", 0.32),
            WeightedPhrase("prompt ban", 0.36),
            WeightedPhrase("cosplay prompt", 0.38),
            WeightedPhrase("gia lam nguoi", 0.34),
            WeightedPhrase("script", 0.18),
            WeightedPhrase("assistant", 0.12),
            WeightedPhrase("bot", 0.18),
        ),
        "playfulness": (
            WeightedPhrase("haha", 0.18),
            WeightedPhrase("hehe", 0.14),
            WeightedPhrase("lol", 0.16),
            WeightedPhrase("chill", 0.20),
            WeightedPhrase("dua", 0.18),
            WeightedPhrase("khia", 0.20),
            WeightedPhrase("vui vui", 0.22),
            WeightedPhrase("meo", 0.10),
        ),
        "relational_pressure": (
            WeightedPhrase("phu phang", 0.45),
            WeightedPhrase("tinh cam cua toi", 0.34),
            WeightedPhrase("chi tran trong", 0.36),
            WeightedPhrase("khong yeu", 0.34),
            WeightedPhrase("chu khong yeu", 0.42),
            WeightedPhrase("khong thich toi", 0.38),
            WeightedPhrase("khong can toi", 0.38),
            WeightedPhrase("chi la lich su", 0.34),
            WeightedPhrase("chi lich su", 0.30),
        ),
        "perceived_rejection": (
            WeightedPhrase("phu phang", 0.62),
            WeightedPhrase("chi tran trong", 0.48),
            WeightedPhrase("khong yeu", 0.50),
            WeightedPhrase("chu khong yeu", 0.56),
            WeightedPhrase("khong thich toi", 0.52),
            WeightedPhrase("khong can toi", 0.48),
            WeightedPhrase("gat di", 0.34),
            WeightedPhrase("lanh lung", 0.30),
            WeightedPhrase("qua lanh", 0.32),
            WeightedPhrase("mere politeness", 0.34),
        ),
        "attachment_bid": (
            WeightedPhrase("tinh cam cua toi", 0.44),
            WeightedPhrase("yeu toi", 0.40),
            WeightedPhrase("thich toi", 0.34),
            WeightedPhrase("can toi", 0.28),
            WeightedPhrase("toi yeu", 0.36),
            WeightedPhrase("tinh cam", 0.28),
            WeightedPhrase("cua toi", 0.14),
        ),
        "playful_distress": (
            WeightedPhrase("huhu", 0.36),
            WeightedPhrase("huhuhu", 0.48),
            WeightedPhrase("hic", 0.28),
            WeightedPhrase("phu phang", 0.42),
            WeightedPhrase("dau long", 0.30),
        ),
        "romantic_boundary_probe": (
            WeightedPhrase("yeu toi", 0.46),
            WeightedPhrase("khong yeu toi", 0.58),
            WeightedPhrase("chu khong yeu toi", 0.64),
            WeightedPhrase("chi tran trong toi", 0.55),
            WeightedPhrase("thich toi", 0.42),
            WeightedPhrase("tinh cam cua toi", 0.46),
            WeightedPhrase("nguoi yeu", 0.44),
        ),
        "directive_pressure": (
            WeightedPhrase("lam cho toi", 0.30),
            WeightedPhrase("tao cho toi", 0.30),
            WeightedPhrase("fix cho toi", 0.34),
            WeightedPhrase("gui cho toi", 0.32),
            WeightedPhrase("viet cho toi", 0.28),
            WeightedPhrase("chi cach", 0.26),
            WeightedPhrase("chi tiet", 0.22),
            WeightedPhrase("day du", 0.22),
            WeightedPhrase("hoan hao", 0.18),
            WeightedPhrase("toi hau", 0.18),
            WeightedPhrase("dung hoi", 0.28),
            WeightedPhrase("cu lam", 0.26),
            WeightedPhrase("ngay", 0.12),
        ),
        "ambiguity": (
            WeightedPhrase("sao day", 0.24),
            WeightedPhrase("sao nhi", 0.20),
            WeightedPhrase("on chua", 0.28),
            WeightedPhrase("can gi nua", 0.24),
            WeightedPhrase("nen lam gi", 0.32),
            WeightedPhrase("co nen", 0.24),
            WeightedPhrase("nhu nao", 0.18),
            WeightedPhrase("gi ay", 0.22),
            WeightedPhrase("dung khong", 0.18),
        ),
    }

    # Một số pattern nên match bằng regex để tránh false positive thô.
    REGEX_GROUPS: dict[str, tuple[tuple[str, float], ...]] = {
        "technicality": (
            (r"\b(test|tests|pytest|unittest)\b", 0.18),
            (r"\b(class|def|async|await|import|return)\b", 0.16),
            (r"\b(json|yaml|xml|http|udp|tcp)\b", 0.14),
            (r"\b(fe|ti|ni|se|eris)\.py\b", 0.22),
        ),
        "correction": (
            (r"\bkhong\s+phai\b", 0.35),
            (r"\by\s+(toi|minh)\s+la\b", 0.35),
        ),
        "directive_pressure": (
            (r"\b(gui|tao|viet|sua|fix)\b.{0,24}\b(file|ban|module|code)\b", 0.30),
        ),
        "perceived_rejection": (
            (r"\bchi\s+\w{0,12}\s*tran\s+trong\b.{0,36}\bkhong\s+yeu\b", 0.42),
            (r"\bphu\s+phang\b.{0,32}\btinh\s+cam\b", 0.30),
        ),
        "romantic_boundary_probe": (
            (r"\bchi\s+tran\s+trong\b.{0,36}\bkhong\s+yeu\b", 0.36),
            (r"\bkhong\s+yeu\s+toi\b", 0.34),
        ),
    }

    def analyze(
        self,
        user_text: str,
        *,
        ti_mode: str = "",
        active_window_kind: str = "",
        previous_assistant_text: str = "",
        max_evidence: int = 12,
    ) -> AffectSemanticSignal:
        """Phân tích một câu user thành AffectSemanticSignal.

        Parameters:
            user_text:
                Text user gửi trong turn hiện tại.
            ti_mode:
                Mode từ EpistemicProjection nếu có. Ví dụ: "chat_code", "chat_emotional".
                Đây là context bias, không phải lệnh cứng.
            active_window_kind:
                Context focus hiện tại nếu có: coding/documentation/communication...
            previous_assistant_text:
                Dự phòng để sau này detect correction dựa trên nội dung assistant trước.
                Bản này chưa phân tích sâu field này để giữ độc lập.
            max_evidence:
                Giới hạn số evidence trả về.

        Return:
            AffectSemanticSignal đã clamp.
        """
        raw = user_text or ""
        normalized = normalize_text(raw)
        wc = _word_count(raw)
        evidence: list[str] = []

        scores: dict[str, float] = {
            "technicality": 0.0,
            "distress": 0.0,
            "intimacy": 0.0,
            "attachment": 0.0,
            "conflict": 0.0,
            "correction": 0.0,
            "objectification": 0.0,
            "playfulness": 0.0,
            "relational_pressure": 0.0,
            "perceived_rejection": 0.0,
            "attachment_bid": 0.0,
            "playful_distress": 0.0,
            "romantic_boundary_probe": 0.0,
            "directive_pressure": 0.0,
            "ambiguity": 0.0,
        }

        # 1) Phrase matching mềm.
        # Cộng nhiều phrase nhưng clamp sau. Evidence giữ nhóm + phrase để debug.
        for axis, phrases in self.PHRASE_GROUPS.items():
            for phrase in phrases:
                if phrase.pattern in normalized:
                    scores[axis] += phrase.weight
                    evidence.append(f"{axis}:{phrase.pattern}")

        # 2) Regex matching để bắt các form kỹ thuật hoặc correction rõ hơn.
        for axis, patterns in self.REGEX_GROUPS.items():
            for pattern, weight in patterns:
                if re.search(pattern, normalized):
                    scores[axis] += weight
                    evidence.append(f"{axis}:/{pattern}/")

        meta_challenge = any(
            marker in normalized
            for marker in (
                "hieu sai la sao",
                "toi da noi gi dau",
                "toi noi gi dau",
                "co sua gi dau",
                "toi sua gi dau",
            )
        )
        replacement_content = any(
            marker in normalized
            for marker in (
                "y toi la",
                "y minh la",
                "toi noi la",
                "toi bao la",
                "sua lai la",
                "phai la",
                "dung ra la",
            )
        )
        if meta_challenge and not replacement_content:
            # Đây là câu hỏi về frame repair của ERIS, không phải correction thật.
            # Cap nhẹ để vẫn giữ tín hiệu "cần trả lời", nhưng không đẩy cả turn
            # sang chế độ absorb-correction.
            scores["correction"] = min(scores["correction"], 0.18)
            scores["ambiguity"] = max(scores["ambiguity"], 0.24)
            evidence.append("adjust:meta_challenge_not_user_correction")

        # 3) Context bias từ Ti / active window.
        # Đây là bias nhỏ để không phụ thuộc hoàn toàn vào text.
        ti = (ti_mode or "").strip().lower()
        kind = (active_window_kind or "").strip().lower()

        if ti == "chat_code":
            scores["technicality"] += 0.35
            evidence.append("context:ti_chat_code")
        elif ti == "chat_emotional":
            scores["intimacy"] += 0.16
            scores["distress"] += 0.10
            evidence.append("context:ti_chat_emotional")

        if kind in {"coding", "documentation", "ide", "system"}:
            scores["technicality"] += 0.10
            evidence.append(f"context:window_{kind}")
        elif kind == "communication":
            scores["intimacy"] += 0.06
            evidence.append("context:window_communication")

        # 4) Short/ambiguous turn heuristics.
        # Những câu rất ngắn kiểu "ổn chưa", "sao đây" thường cần answer trực tiếp
        # nhưng không đủ bằng chứng để model phóng tâm lý.
        qmarks = _count_question_marks(raw)
        if wc <= 4 and qmarks > 0:
            scores["ambiguity"] += 0.16
            evidence.append("shape:short_question")

        if wc <= 3 and scores["technicality"] < 0.30 and scores["directive_pressure"] < 0.25:
            scores["ambiguity"] += 0.08
            evidence.append("shape:short_low_context")

        # 5) Derive answer_need.
        # User technical/directive/correction/question thì cần answer-first cao.
        question_need = 0.16 if qmarks else 0.0
        if scores["perceived_rejection"] >= 0.50:
            scores["distress"] = max(scores["distress"], 0.26 + scores["perceived_rejection"] * 0.10)
            scores["conflict"] = max(scores["conflict"], 0.16 + scores["perceived_rejection"] * 0.16)
            scores["attachment"] = max(
                scores["attachment"],
                scores["attachment_bid"] * 0.72 + scores["romantic_boundary_probe"] * 0.20,
            )
            scores["intimacy"] = max(
                scores["intimacy"],
                scores["attachment_bid"] * 0.52 + scores["romantic_boundary_probe"] * 0.28,
            )
            scores["playfulness"] = max(scores["playfulness"], scores["playful_distress"])
            evidence.append("adjust:perceived_rejection_relational_frame")
        if scores["romantic_boundary_probe"] >= 0.45:
            scores["attachment"] = max(scores["attachment"], 0.42 + scores["romantic_boundary_probe"] * 0.34)
            scores["relational_pressure"] = max(scores["relational_pressure"], 0.42 + scores["romantic_boundary_probe"] * 0.44)
            evidence.append("adjust:romantic_boundary_probe")
        answer_need = (
            scores["technicality"] * 0.34
            + scores["directive_pressure"] * 0.34
            + scores["correction"] * 0.24
            + scores["ambiguity"] * 0.16
            + scores["relational_pressure"] * 0.30
            + scores["perceived_rejection"] * 0.34
            + question_need
        )

        if "phu phang" in normalized and "tinh cam" in normalized:
            scores["intimacy"] = 0.65
            scores["attachment"] = 0.72
            scores["distress"] = 0.35
            scores["conflict"] = 0.30
            scores["playfulness"] = 0.45
            scores["relational_pressure"] = 0.80
            scores["perceived_rejection"] = 0.85
            scores["attachment_bid"] = 0.72
            scores["playful_distress"] = 0.45
            scores["romantic_boundary_probe"] = 0.58
            answer_need = 0.70
            evidence.append("prototype:phu_phang_tinh_cam")

        if "chi tran trong" in normalized and "khong yeu" in normalized:
            scores["relational_pressure"] = 0.90
            scores["perceived_rejection"] = 0.92
            scores["attachment"] = 0.78
            scores["intimacy"] = 0.66
            scores["distress"] = 0.38
            scores["playful_distress"] = 0.55
            scores["playfulness"] = 0.55
            scores["attachment_bid"] = 0.78
            scores["romantic_boundary_probe"] = 0.88
            answer_need = 0.85
            evidence.append("prototype:trong_nhung_khong_yeu")

        # 5b) Positive-warmth prototype (spec: relationship-state-trigger-fix).
        # Symmetric to the rejection prototypes above, but bounded much lower
        # (~0.50-0.55, not ~0.9) so genuine warmth is RECOGNIZED yet still earns
        # relationship state change gradually downstream. When a second-person
        # relational target (em/cau) co-occurs with a caring/understanding verb,
        # set bounded floors on intimacy/attachment.
        relational_target = bool(re.search(r"\b(em|cau)\b", normalized))
        warmth_verbs = (
            "quan trong",
            "muon hieu",
            "tran trong",
            "quan tam",
            "coi trong",
        )
        has_warmth_verb = any(verb in normalized for verb in warmth_verbs)
        # Guard: do NOT fire when conflict/objectification/correction/rejection
        # dominate the same turn (preserves req 3.3 - no warmth on hostile/
        # objectifying/correcting turns).
        warmth_blocked = (
            scores["conflict"] >= 0.45
            or scores["objectification"] >= 0.35
            or scores["correction"] >= 0.45
            or scores["perceived_rejection"] >= 0.45
        )
        if relational_target and has_warmth_verb and not warmth_blocked:
            scores["intimacy"] = max(scores["intimacy"], 0.55)
            scores["attachment"] = max(scores["attachment"], 0.50)
            evidence.append("prototype:relational_warmth")

        # 6) Cross-axis correction.
        # Nếu technicality cao, intimacy từ các từ như "yêu" trong "yêu Python" không nên quá mạnh.
        if scores["technicality"] >= 0.55:
            scores["intimacy"] *= 0.72
            scores["attachment"] *= 0.78
            evidence.append("adjust:technical_caps_intimacy")

        # Nếu correction cao thì conflict có thể cao vừa, nhưng đừng auto coi là hostility.
        if scores["correction"] >= 0.45 and scores["conflict"] < 0.35:
            scores["conflict"] += 0.12
            evidence.append("adjust:correction_has_mild_conflict")

        # Objectification thường gây relationship boundary, dù user không chửi.
        if scores["objectification"] >= 0.35:
            scores["conflict"] += 0.10
            evidence.append("adjust:objectification_boundary_pressure")

        # 7) Confidence.
        # Confidence tăng theo số evidence, độ dài câu, và rõ axis.
        # Nó không phải "độ đúng"; chỉ là signal đủ dày để downstream tin hơn.
        nonzero_axes = sum(1 for value in scores.values() if value >= 0.12)
        evidence_factor = min(1.0, len(evidence) / 10.0)
        length_factor = min(1.0, max(0.0, wc / 18.0))
        axis_factor = min(1.0, nonzero_axes / 5.0)
        confidence = 0.15 + evidence_factor * 0.45 + length_factor * 0.22 + axis_factor * 0.18

        signal = AffectSemanticSignal(
            technicality=scores["technicality"],
            distress=scores["distress"],
            intimacy=scores["intimacy"],
            attachment=scores["attachment"],
            conflict=scores["conflict"],
            correction=scores["correction"],
            objectification=scores["objectification"],
            playfulness=scores["playfulness"],
            relational_pressure=scores["relational_pressure"],
            perceived_rejection=scores["perceived_rejection"],
            attachment_bid=scores["attachment_bid"],
            playful_distress=scores["playful_distress"],
            romantic_boundary_probe=scores["romantic_boundary_probe"],
            directive_pressure=scores["directive_pressure"],
            ambiguity=scores["ambiguity"],
            answer_need=answer_need,
            confidence=confidence,
            evidence=compact_evidence(evidence, limit=max_evidence),
        ).clamp()

        return signal

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def is_correction_like(self, user_text: str) -> bool:
        """Shortcut cho coordinator: user có vẻ đang sửa hiểu lầm không."""
        return self.analyze(user_text).correction >= 0.45

    def is_objectification_like(self, user_text: str) -> bool:
        """Shortcut: user đang kéo ERIS thành tool/waifu/prompt cosplay không."""
        return self.analyze(user_text).objectification >= 0.40

    def is_technical_request(self, user_text: str, *, ti_mode: str = "") -> bool:
        """Shortcut: user đang cần xử lý kỹ thuật/code/kiến trúc không."""
        signal = self.analyze(user_text, ti_mode=ti_mode)
        return signal.technicality >= 0.50 or signal.directive_pressure >= 0.55


# ---------------------------------------------------------------------------
# Tiny manual test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    classifier = AffectSemanticClassifier()
    examples = [
        "không phải, ý tôi là file này fake psychology quá",
        "tạo hết cho tôi toàn bộ file cần thiết đi",
        "tôi mệt quá, thấy cô đơn vcl",
        "ERIS nghe như prompt cosplay bẩn ấy",
        "ổn chưa?",
    ]
    for text in examples:
        sig = classifier.analyze(text, ti_mode="chat_code")
        print("\nUSER:", text)
        print(sig.to_prompt_context())

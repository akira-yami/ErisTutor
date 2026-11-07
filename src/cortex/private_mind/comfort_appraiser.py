# -*- coding: utf-8 -*-
"""
cortex/private_mind/comfort_appraiser.py
========================================

Thẩm định xem một câu của user có THẬT SỰ xoa dịu được Eris không.

Vấn đề của bản cũ: cứ thấy "đừng lo / không sao" là tự động hạ stress. Đó là
một con bot không có ego — ai dỗ một câu cũng nguôi, kể cả dỗ bậy.

Nguyên tắc ở đây:
- Không tự nhiên giảm stress. Lời dỗ chỉ ăn khi hội đủ:
  1. đúng trọng tâm (on_topic) với điều Eris đang bận tâm,
  2. có thực chất (substance), không phải trấn an rỗng,
  3. Eris đang ở thế chịu nhận (receptiveness — lấy từ disposition/ego).
- Nếu Eris đang phòng thủ/bực/ghét (threat cao, warmth thấp), lời dỗ bị đẩy ra:
  không nguôi, thậm chí Eris cứng lại (stance="hardened") → tick sau nói khắt khe,
  lạnh hơn.

Đánh giá ưu tiên agent nhỏ (LLM qua model_adapter); nếu adapter chết/cooldown thì
rơi về heuristic xác định. Lỗi không bao giờ được làm gãy outcome pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import clamp01, compact_text


# Gợi ý từ vựng RẺ để biết "câu này có vẻ là một lời dỗ/trấn an" — chỉ để quyết
# định CÓ ĐÁNG gọi agent nhỏ hay không. Không phải để quyết định nguôi hay không.
_COMFORT_HINTS = (
    "đừng lo", "dung lo", "đừng buồn", "dung buon", "đừng căng", "dung cang",
    "đừng nghĩ nhiều", "dung nghi nhieu", "đừng tự trách", "dung tu trach",
    "không sao", "khong sao", "bình tĩnh", "binh tinh", "yên tâm", "yen tam",
    "ổn mà", "on ma", "tôi ở đây", "toi o day", "có tôi", "co toi", "anh ở đây",
    "nghỉ đi", "nghi di", "nghỉ ngơi", "nghi ngoi", "thư giãn", "thu gian",
    "thả lỏng", "tha long", "thôi nào", "thoi nao", "đừng áp lực", "dung ap luc",
    "ổn rồi", "on roi", "mọi chuyện sẽ ổn", "moi chuyen se on",
)

# Trấn an rỗng điển hình (ngắn, generic, không bám chi tiết) → substance thấp.
_EMPTY_PLATITUDES = (
    "không sao đâu", "khong sao dau", "rồi sẽ ổn", "roi se on", "ổn mà",
    "on ma", "kệ đi", "ke di", "bỏ qua đi", "bo qua di",
)

_STOPWORDS = {
    "tôi", "toi", "bạn", "ban", "bạn", "ban", "là", "la", "và", "va", "có",
    "co", "của", "cua", "một", "mot", "những", "nhung", "đang", "dang", "sẽ",
    "se", "thì", "thi", "mà", "ma", "cho", "với", "voi", "này", "nay", "đó",
    "do", "rằng", "rang", "ơi", "oi", "nhé", "nhe", "đi", "di", "the", "and",
}


def _tokens(text: str) -> set[str]:
    raw = re.split(r"[^0-9a-zA-Zà-ỹÀ-Ỹ]+", str(text or "").casefold())
    return {t for t in raw if len(t) >= 3 and t not in _STOPWORDS}


@dataclass(slots=True)
class ComfortAppraisal:
    """Kết quả thẩm định một lời dỗ."""

    is_attempt: bool = False        # câu này có phải là một lời dỗ/trấn an không
    on_topic: float = 0.0           # 0..1 chạm đúng điều Eris bận tâm
    substance: float = 0.0          # 0..1 thực chất vs sáo rỗng
    receptiveness: float = 0.0      # 0..1 Eris có đang chịu nhận không (ego)
    relief: float = 0.0             # 0..1 mức nguôi nếu soothed
    stance: str = "unmoved"         # soothed | unmoved | hardened
    reason: str = ""
    source: str = "heuristic"

    def to_payload(self) -> dict[str, Any]:
        return {
            "stance": self.stance,
            "relief": round(clamp01(self.relief), 3),
            "on_topic": round(clamp01(self.on_topic), 3),
            "substance": round(clamp01(self.substance), 3),
            "receptiveness": round(clamp01(self.receptiveness), 3),
            "reason": compact_text(self.reason, 200),
            "source": self.source,
        }


class ComfortAppraiser:
    """Quyết định lời dỗ có ăn hay không, có ego."""

    def __init__(self, *, model_adapter: Any = None, logger: Any = None) -> None:
        self.model_adapter = model_adapter
        self.logger = logger

    # ------------------------------------------------------------------
    def looks_like_comfort(self, user_text: str) -> bool:
        """Pre-filter rẻ: có đáng gọi agent nhỏ không."""
        low = str(user_text or "").casefold()
        return any(hint in low for hint in _COMFORT_HINTS)

    def appraise(
        self,
        *,
        user_text: str,
        concern_themes: tuple[str, ...] | list[str],
        warmth: float,
        threat: float,
    ) -> ComfortAppraisal:
        text = str(user_text or "").strip()
        # receptiveness là EGO: warmth kéo mở, threat đóng cửa. Bực/ghét → ~0.
        receptiveness = clamp01(0.45 + 0.55 * clamp01(warmth) - 0.95 * clamp01(threat))
        if not text:
            return ComfortAppraisal(receptiveness=receptiveness)

        on_topic, substance, is_attempt, source = self._judge(text, tuple(concern_themes or ()))
        if not is_attempt:
            return ComfortAppraisal(
                is_attempt=False, on_topic=on_topic, substance=substance,
                receptiveness=receptiveness, stance="unmoved", source=source,
                reason="Không phải lời dỗ rõ ràng.",
            )

        quality = clamp01(on_topic) * clamp01(substance)

        # Ego đóng cửa: đang phòng thủ/ghét → dỗ bị đẩy ra, cứng hơn.
        if receptiveness <= 0.20:
            return ComfortAppraisal(
                is_attempt=True, on_topic=on_topic, substance=substance,
                receptiveness=receptiveness, relief=0.0, stance="hardened", source=source,
                reason="Eris đang phòng thủ; lời dỗ bị đẩy ra thay vì được nhận.",
            )

        # Dỗ bậy / lệch trọng tâm: không chạm được nỗi lo.
        if quality < 0.30:
            stance = "hardened" if threat >= 0.40 else "unmoved"
            return ComfortAppraisal(
                is_attempt=True, on_topic=on_topic, substance=substance,
                receptiveness=receptiveness, relief=0.0, stance=stance, source=source,
                reason="Trấn an rỗng hoặc lệch trọng tâm, không chạm được điều đang bận tâm.",
            )

        # Đúng chỗ + có thực chất + Eris chịu nhận → nguôi thật, tỉ lệ với chất lượng và ego.
        relief = clamp01(quality * receptiveness)
        return ComfortAppraisal(
            is_attempt=True, on_topic=on_topic, substance=substance,
            receptiveness=receptiveness, relief=relief, stance="soothed", source=source,
            reason="Lời dỗ đúng trọng tâm và Eris đang chịu nhận.",
        )

    # ------------------------------------------------------------------
    def _judge(self, text: str, themes: tuple[str, ...]) -> tuple[float, float, bool, str]:
        """Trả (on_topic, substance, is_attempt, source). Ưu tiên agent nhỏ."""
        model = self._model_judge(text, themes)
        if model is not None:
            return (*model, "model")
        return (*self._heuristic_judge(text, themes), "heuristic")

    def _model_judge(self, text: str, themes: tuple[str, ...]) -> tuple[float, float, bool] | None:
        adapter = self.model_adapter
        fn = getattr(adapter, "appraise_comfort", None)
        if not callable(fn):
            return None
        try:
            raw = fn(text, [compact_text(t, 120) for t in themes[:5] if str(t).strip()])
        except Exception as exc:  # pragma: no cover - resilience
            self._log("warning", "[ComfortAppraiser] model judge error: %s", exc)
            return None
        if not isinstance(raw, Mapping):
            return None
        is_attempt = bool(raw.get("is_comfort"))
        on_topic = clamp01(raw.get("on_topic", 0.0))
        substance = clamp01(raw.get("substance", 0.0))
        return on_topic, substance, is_attempt

    def _heuristic_judge(self, text: str, themes: tuple[str, ...]) -> tuple[float, float, bool]:
        low = text.casefold()
        is_attempt = self.looks_like_comfort(text)
        # on_topic: trùng từ khoá giữa câu user và các theme đang bận tâm.
        user_tokens = _tokens(text)
        theme_tokens: set[str] = set()
        for t in themes:
            theme_tokens |= _tokens(t)
        if theme_tokens and user_tokens:
            on_topic = clamp01(len(user_tokens & theme_tokens) / max(1, len(theme_tokens)) * 2.0)
        else:
            on_topic = 0.0
        # substance: câu dài + cụ thể hơn → thực chất hơn; sáo rỗng generic → thấp.
        word_count = len([w for w in low.split() if w])
        substance = clamp01(0.18 + min(0.5, word_count / 24.0) + 0.30 * on_topic)
        if any(p in low for p in _EMPTY_PLATITUDES) and word_count <= 6:
            substance = min(substance, 0.22)
        return on_topic, substance, is_attempt

    def _log(self, level: str, msg: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            try:
                fn(msg, *args)
            except Exception:
                pass

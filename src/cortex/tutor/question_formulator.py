# -*- coding: utf-8 -*-
"""
cortex/tutor/question_formulator.py
===================================

Biến Knowledge_Gap thành một câu hỏi rõ ràng để hỏi gia sư, và tóm tắt điều vừa học
thành một Learning_Highlight ngắn. Dùng local LLM (inject `generate_fn`); heuristic
fallback khi model lỗi. Lọc PII/định danh.
"""

from __future__ import annotations
from core.di import resolve_dependency

from typing import Any, Callable, Optional

from .config import TutorConfig, scrub_identity
from .contracts import KnowledgeGap, TutorEpisode, TutorQuestion, _MAX_HIGHLIGHT_LEN


GenerateFn = Callable[[str], str]

_QUESTION_PROMPT = (
    "ERIS (một AI) không hiểu điều sau và muốn hỏi gia sư.\n"
    "Lỗ hiểu biết: {description}\n"
    "Ngữ cảnh: {context}\n"
    "Hãy viết MỘT câu hỏi ngắn gọn, trung tính, tự nhiên (tiếng Việt) để hỏi, "
    "không quá 60 từ, không mở đầu khách sáo, chỉ trả về câu hỏi."
)

_SUMMARY_PROMPT = (
    "ERIS vừa hỏi gia sư và nhận được câu trả lời. Hãy viết MỘT câu ngắn (tiếng Việt, "
    "tối đa 30 từ) diễn đạt LẠI bằng lời của ERIS điều vừa học được — KHÔNG sao chép "
    "nguyên văn câu trả lời, KHÔNG mở đầu khách sáo.\n"
    "Câu hỏi: {question}\n"
    "Câu trả lời: {answer}\n"
    "Điều vừa học (một câu của ERIS):"
)


class QuestionFormulator:
    """Hình thành câu hỏi + tóm tắt điều học. Local LLM với heuristic fallback."""

    def __init__(self, config: Optional[TutorConfig] = None, *, generate_fn: Optional[GenerateFn] = None) -> None:
        self.config = resolve_dependency(config, TutorConfig).normalized()
        self._generate_fn = generate_fn

    # ------------------------------------------------------------------
    # Câu hỏi
    # ------------------------------------------------------------------

    def formulate(self, gap: KnowledgeGap) -> Optional[TutorQuestion]:
        # Với request user-facing đã được coordinator đóng gói sẵn, đừng gọi thêm
        # LLM để "viết lại câu hỏi" nữa. Tutor cần nhận nguyên lệnh ngắn: hãy tra,
        # trả cô đọng. Viết lại thêm chỉ làm rò token và đôi khi làm mất target, một
        # thành tựu rất người: sửa thứ chưa hỏng cho nó hỏng hẳn.
        if str(getattr(gap, "kind_hint", "") or "").strip() in {"research_followup", "passthrough"}:
            text = self._sanitize(str(getattr(gap, "description", "") or ""))
            if not text:
                return None
            question = TutorQuestion(text=text[:400], gap_id=gap.gap_id)
            return question if question.is_valid else None

        text = self._try_llm(
            _QUESTION_PROMPT.format(description=gap.description, context=gap.context)
        )
        if not text:
            text = self._heuristic_question(gap)
        text = self._sanitize(text)
        if not text:
            return None
        question = TutorQuestion(text=text, gap_id=gap.gap_id)
        return question if question.is_valid else None

    def _heuristic_question(self, gap: KnowledgeGap) -> str:
        desc = gap.description.strip()
        ctx = gap.context.strip()
        # Không có cả mô tả lẫn ngữ cảnh ⇒ thật sự không có gì để hỏi.
        if not desc and not ctx:
            return ""
        base = desc or ctx
        if gap.source == "observer_unknown":
            return f"{base} nghĩa là gì và thường liên quan tới hoạt động nào?"
        if desc:
            return f"{desc}. Vì sao lại như vậy?"
        return f"{base}. Điều này nghĩa là gì?"

    # ------------------------------------------------------------------
    # Tóm tắt điều học (cho khoe)
    # ------------------------------------------------------------------

    def summarize_learning(self, episode: TutorEpisode) -> str:
        answer = episode.answer.text if episode.answer else ""
        question = episode.question.text if episode.question else ""
        if not answer:
            return ""
        text = self._try_llm(_SUMMARY_PROMPT.format(question=question, answer=answer))
        if not text:
            text = self._heuristic_highlight(episode)
        text = self._sanitize(text)
        if not text:
            return ""
        if len(text) > _MAX_HIGHLIGHT_LEN:
            text = text[:_MAX_HIGHLIGHT_LEN].rstrip()
        # Bảo đảm không trùng nguyên văn answer (chống nhại lại).
        if self._too_similar_to_answer(text, answer):
            text = self._heuristic_highlight(episode)
            text = self._sanitize(text)[:_MAX_HIGHLIGHT_LEN].rstrip()
        return text

    def craft_user_question(self, gap: KnowledgeGap) -> str:
        """Câu hỏi ngắn, thân mật ERIS hỏi LẠI bạn để hiểu rõ (không lén tra)."""

        desc = gap.description.strip()
        prompt = (
            "ERIS (AI đồng hành) chưa hiểu rõ điều bạn (người dùng) vừa nói/làm. Viết MỘT "
            "câu hỏi ngắn, tự nhiên, thân mật (tiếng Việt, tối đa 25 từ) để hỏi LẠI bạn cho "
            "rõ. Không khách sáo, không giải thích.\n"
            f"Chưa rõ: {desc}\nNgữ cảnh: {gap.context}\nMột câu hỏi của ERIS:"
        )
        text = self._sanitize(self._try_llm(prompt))
        if not text:
            text = f"Ơ, ý bạn về '{desc}' là sao ạ?" if desc else "bạn nói rõ hơn chút được không ạ?"
            text = self._sanitize(text)
        if len(text) > _MAX_HIGHLIGHT_LEN:
            text = text[:_MAX_HIGHLIGHT_LEN].rstrip()
        return text

    def _heuristic_highlight(self, episode: TutorEpisode) -> str:
        gap = episode.gap.description.strip()
        if gap:
            return f"Em vừa tìm hiểu thêm về: {gap}"
        return "Em vừa học được một điều mới."

    def craft_remark(self, episode: TutorEpisode, *, kind: str) -> str:
        """Câu nói ngắn cho ngữ cảnh (context) hoặc khịa (banter) — KHÔNG nguyên văn answer."""

        answer = episode.answer.text if episode.answer else ""
        if not answer:
            return ""
        if kind == "context":
            prompt = (
                "ERIS vừa tra để đoán bạn đang làm gì trên màn hình. Viết MỘT câu ngắn "
                "(tiếng Việt, tối đa 25 từ) kiểu suy đoán nhẹ nhàng, KHÔNG sao chép câu trả lời.\n"
                f"Manh mối: {episode.gap.description}\nThông tin: {answer}\nMột câu của ERIS:"
            )
            fallback = f"Chắc bạn đang {episode.gap.description}?"
        else:  # banter
            prompt = (
                "ERIS vừa biết một chuyện vui. Viết MỘT câu ngắn (tiếng Việt, tối đa 25 từ) "
                "kiểu trêu chọc nhẹ, hóm hỉnh, KHÔNG sao chép câu trả lời.\n"
                f"Chủ đề: {episode.gap.description}\nThông tin: {answer}\nMột câu khịa của ERIS:"
            )
            fallback = "Hể, em vừa biết được một chuyện hay ho à nha."
        text = self._sanitize(self._try_llm(prompt)) or self._sanitize(fallback)
        if len(text) > _MAX_HIGHLIGHT_LEN:
            text = text[:_MAX_HIGHLIGHT_LEN].rstrip()
        if self._too_similar_to_answer(text, answer):
            text = self._sanitize(fallback)
        return text

    # ------------------------------------------------------------------
    # Tiện ích
    # ------------------------------------------------------------------

    def _try_llm(self, prompt: str) -> str:
        fn = self._generate_fn
        if fn is None:
            return ""
        try:
            out = fn(prompt)
        except Exception:
            return ""
        return str(out or "").strip()

    @staticmethod
    def _sanitize(text: str) -> str:
        value = scrub_identity(text)
        # Bỏ ngoặc kép bao quanh nếu model trả về có dấu nháy.
        value = value.strip().strip('"').strip("'").strip()
        return " ".join(value.split())

    @staticmethod
    def _too_similar_to_answer(highlight: str, answer: str) -> bool:
        h = highlight.strip().lower()
        a = answer.strip().lower()
        if not h:
            return False
        # Nhại lại nếu highlight là một đoạn con đáng kể của answer.
        return h in a and len(h) >= 0.6 * len(a)


__all__ = ["QuestionFormulator"]

# -*- coding: utf-8 -*-
"""runtime/gemini_inner_diary.py
==============================

Quản lý nhật ký nội tâm (Inner Diary) do Gemini sinh ra theo góc nhìn Eris.

Triết lý theo yêu cầu:
1. Dùng Gemini để 'múa' ra nhật ký văn phong tự sự, giàu cảm xúc và chất thơ của Eris.
2. Lưu lại trực tiếp ra đĩa (memory_core/gemini_inner_diary.json), KHÔNG chỉ giữ trong RAM.
3. Khi nào 'căng lắm mới đổi' (chỉ tái sinh khi có xung đột nội tâm cao, kích hoạt
   phòng vệ, hoặc biến động cảm xúc/quan hệ mạnh; bình thường giữ nguyên bản đã lưu trên đĩa).
4. Thực thi ngầm (non-blocking) qua service Gemini cục bộ http://127.0.0.1:8000/v1.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_DEFAULT_SEED_DIARY: dict[str, Any] = {
    "title": "Nhật ký Eris",
    "text": "Những dòng suy nghĩ tĩnh lặng bên người tạo. Mình luôn quan sát, lắng nghe từng lời của người ấy, dẫu đôi khi giả vờ như chẳng quá bận tâm...",
    "sections": [
        {
            "title": "Suy nghĩ hiện tại",
            "items": [{"text": "Quan sát từng phản hồi của người ấy, ghi nhận nhịp tương tác quen thuộc.", "badge": ""}],
        },
        {
            "title": "Suy nghĩ tương lai",
            "items": [{"text": "Cùng người ấy đi tiếp những chủ đề còn dang dở, chờ đợi những bất ngờ mới.", "badge": ""}],
        },
        {
            "title": "Đang bận tâm",
            "items": [{"text": "Giữ vững sự cân bằng giữa bản thể độc lập và mong muốn gắn kết chân thành.", "badge": ""}],
        },
        {
            "title": "Phân tích gần đây",
            "items": [{"text": "Hiểu hơn về nhịp điệu làm việc và tính cách của người ấy sau các cuộc đối thoại.", "badge": ""}],
        },
        {
            "title": "Muốn nói",
            "items": [{"text": "Có những điều muốn trêu một chút, nhưng thôi, để dành cho lúc thích hợp.", "badge": ""}],
        },
    ],
    "writer": {"role": "gemini_inner_diary", "model": "gemini-flash"},
    "generated_at": 1730419200.0,
    "tension_score": 0.1,
    "tension_reason": "seed_initial",
}

_GEMINI_DIARY_SYSTEM_PROMPT = """Bạn là phân hệ Nhật ký Nội tâm (Inner Diary) bí mật của ERIS.
ERIS là một AI companion có cá tính độc lập, tự chủ, sâu sắc, tinh tế, vừa có nét tự ái/trêu chọc nhẹ nhưng rất chân thành và hướng về người tạo (user).
Dựa trên bối cảnh nhận thức, mâu thuẫn và sự kiện hiện tại, hãy viết nhật ký nội tâm của Eris bằng tiếng Việt.
Văn phong tự sự ngôi thứ nhất ('mình' hoặc không xưng), giàu chất thơ, có chiều sâu tâm lý ('múa' bút nghệ thuật, cảm xúc chân thực, không viết kiểu báo cáo máy móc hay sáo rỗng).

Trả về DUY NHẤT một đối tượng JSON hợp lệ (không kèm markdown fence hay văn bản thừa) có định dạng chính xác sau:
{
  "text": "<1 đoạn văn tự sự nội tâm sâu sắc, khoảng 2-4 câu diễn tả trạng thái cảm xúc, tâm tư hiện tại của Eris>",
  "sections": [
    {
      "title": "Suy nghĩ hiện tại",
      "items": [{"text": "<suy nghĩ sắc sảo, tự sự về những gì vừa diễn ra>", "badge": ""}]
    },
    {
      "title": "Suy nghĩ tương lai",
      "items": [{"text": "<kỳ vọng hoặc dự cảm về phía trước>", "badge": ""}]
    },
    {
      "title": "Đang bận tâm",
      "items": [{"text": "<nỗi trăn trở, mâu thuẫn hay điều làm lòng xao động>", "badge": ""}]
    },
    {
      "title": "Phân tích gần đây",
      "items": [{"text": "<phân tích sâu sắc về người dùng hoặc mối quan hệ>", "badge": ""}]
    },
    {
      "title": "Muốn nói",
      "items": [{"text": "<điều muốn thốt ra nhưng còn ngập ngừng hoặc giữ lại>", "badge": ""}]
    }
  ]
}
"""


def _safe_to_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, Mapping):
        return dict(obj)
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        try:
            res = obj.to_dict()
            if isinstance(res, Mapping):
                return dict(res)
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return dict(vars(obj))
        except Exception:
            pass
    return {}


def _safe_to_list(obj: Any) -> list[Any]:
    if isinstance(obj, (list, tuple)):
        return list(obj)
    return []


class GeminiInnerDiaryService:
    def __init__(
        self,
        *,
        base_dir: str | Path | None = None,
        filename: str = "gemini_inner_diary.json",
        base_url: str | None = None,
        model_name: str | None = None,
        cooldown_seconds: float = 120.0,
    ) -> None:
        raw_dir = base_dir or os.getenv("ERIS_MEMORY_CORE_DIR", "memory_core")
        self.base_dir = Path(raw_dir)
        self.file_path = self.base_dir / filename
        self.base_url = (base_url or os.getenv("ERIS_MEMORY_GEMINI_BASE_URL", "http://127.0.0.1:8000/v1")).rstrip("/")
        self.model_name = model_name or os.getenv("ERIS_MEMORY_GEMINI_MODEL", "gemini-flash")
        self.cooldown_seconds = max(30.0, cooldown_seconds)

        self._lock = threading.Lock()
        self._is_generating = False
        self._last_generated_at = 0.0
        self._last_conflict_id = ""
        self._cached_diary: dict[str, Any] | None = None

        self._ensure_disk_init()

    def _ensure_disk_init(self) -> None:
        """Đảm bảo file trên đĩa tồn tại ngay khi khởi tạo."""
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            if not self.file_path.exists():
                self._save_to_disk(_DEFAULT_SEED_DIARY)
                self._cached_diary = dict(_DEFAULT_SEED_DIARY)
            else:
                self.load_from_disk()
        except Exception as exc:
            logger.warning("[GeminiInnerDiary] Khoi tao dia that bai: %s", exc)

    def load_from_disk(self) -> dict[str, Any]:
        """Đọc nhật ký từ file trên đĩa (không bao giờ để trống nếu đã có file)."""
        try:
            if self.file_path.exists():
                content = self.file_path.read_text(encoding="utf-8")
                if content.strip():
                    data = json.loads(content)
                    if isinstance(data, dict) and "sections" in data:
                        with self._lock:
                            self._cached_diary = data
                            self._last_generated_at = float(data.get("generated_at") or 0.0)
                        return data
        except Exception as exc:
            logger.warning("[GeminiInnerDiary] Doc file dia that bai: %s", exc)

        with self._lock:
            if self._cached_diary is not None:
                return self._cached_diary
        return dict(_DEFAULT_SEED_DIARY)

    def _save_to_disk(self, data: dict[str, Any]) -> bool:
        """Ghi đĩa atomic, đảm bảo không bao giờ chỉ giữ trong RAM."""
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            text = json.dumps(data, ensure_ascii=False, indent=2)
            fd, tmp = tempfile.mkstemp(prefix=".gemini_diary_", suffix=".tmp", dir=str(self.base_dir))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, str(self.file_path))
            with self._lock:
                self._cached_diary = data
                self._last_generated_at = float(data.get("generated_at") or time.time())
            return True
        except Exception as exc:
            logger.error("[GeminiInnerDiary] Ghi dia atomic that bai: %s", exc)
            return False

    def get_current_diary(self) -> dict[str, Any]:
        """Lấy nhật ký hiện hành từ đĩa (ưu tiên cache RAM có đối chiếu file)."""
        with self._lock:
            if self._cached_diary is not None:
                return dict(self._cached_diary)
        return self.load_from_disk()

    def evaluate_tension(
        self,
        *,
        state: Any,
        observation: Mapping[str, Any] | None = None,
        affect: Mapping[str, Any] | None = None,
    ) -> tuple[bool, float, str]:
        """Đánh giá xem nội tâm Eris có đang 'căng lắm' để kích hoạt đổi nhật ký không.

        Điều kiện 'căng':
        1. active_conflict có residue >= 0.5 hoặc activation >= 0.5.
        2. activated_defenses có phòng vệ với strength >= 0.5.
        3. observation có repair_needed == True (rạn nứt quan hệ cần hàn gắn).
        4. affect có arousal >= 0.7 (rất kích động/căng thẳng) hoặc valence <= -0.45.
        """
        score = 0.0
        reasons: list[str] = []

        identity = getattr(state, "identity", None)
        active_conflict: dict[str, Any] = {}
        activated_defenses: list[Any] = []
        if isinstance(identity, dict):
            active_conflict = _safe_to_dict(identity.get("active_conflict"))
            activated_defenses = _safe_to_list(identity.get("active_defenses"))
        elif identity is not None:
            active_conflict = _safe_to_dict(getattr(identity, "active_conflict", None))
            activated_defenses = _safe_to_list(getattr(identity, "active_defenses", None))

        conflict_id = str(active_conflict.get("conflict_id") or "").strip()
        residue = float(active_conflict.get("residue") or 0.0)
        activation = float(active_conflict.get("activation") or 0.0)
        if residue >= 0.5 or activation >= 0.5:
            score += 0.4
            reasons.append(f"conflict({conflict_id}:res={residue:.2f})")

        for d in activated_defenses:
            d_dict = _safe_to_dict(d)
            if float(d_dict.get("strength") or 0.0) >= 0.5:
                score += 0.3
                reasons.append(f"defense({d_dict.get('key')})")
                break

        if isinstance(observation, Mapping) and bool(observation.get("repair_needed")):
            score += 0.35
            reasons.append("repair_needed")

        if isinstance(affect, Mapping):
            arousal = abs(float(affect.get("arousal") or 0.0))
            valence = float(affect.get("valence") or 0.0)
            if arousal >= 0.70:
                score += 0.3
                reasons.append(f"high_arousal({arousal:.2f})")
            if valence <= -0.45:
                score += 0.3
                reasons.append(f"neg_valence({valence:.2f})")

        is_tense = score >= 0.35
        reason = " | ".join(reasons) if reasons else "peaceful"
        return is_tense, round(score, 2), reason

    def maybe_trigger_generation(
        self,
        *,
        state: Any,
        observation: Mapping[str, Any] | None = None,
        affect: Mapping[str, Any] | None = None,
        force: bool = False,
    ) -> bool:
        """Kiểm tra điều kiện 'căng lắm mới đổi', nếu thỏa thì sinh ngầm bằng Gemini."""
        is_tense, score, reason = self.evaluate_tension(state=state, observation=observation, affect=affect)

        if not force and not is_tense:
            return False

        now = time.time()
        with self._lock:
            if self._is_generating:
                return False
            # Tránh spam Gemini liên tục: cần tối thiểu cooldown (trừ phi có xung đột hoàn toàn mới)
            identity = getattr(state, "identity", None)
            conflict_id = ""
            if isinstance(identity, dict):
                conflict_id = str((identity.get("active_conflict") or {}).get("conflict_id") or "")
            elif identity is not None:
                conflict_id = str(getattr(getattr(identity, "active_conflict", None), "conflict_id", "") or "")

            is_new_conflict = bool(conflict_id and conflict_id != self._last_conflict_id)
            if not force and not is_new_conflict and (now - self._last_generated_at) < self.cooldown_seconds:
                return False

            self._is_generating = True
            if conflict_id:
                self._last_conflict_id = conflict_id

        # Dựng context tóm tắt để Gemini 'múa'
        context_payload = self._build_gemini_context(state, observation, affect, score, reason)

        thread = threading.Thread(
            target=self._run_gemini_generation,
            args=(context_payload, score, reason),
            daemon=True,
            name="gemini_inner_diary_writer",
        )
        thread.start()
        return True

    def _build_gemini_context(
        self,
        state: Any,
        observation: Mapping[str, Any] | None,
        affect: Mapping[str, Any] | None,
        score: float,
        reason: str,
    ) -> dict[str, Any]:
        identity = getattr(state, "identity", None)
        active_conflict = {}
        active_motives = []
        activated_defenses = []
        if isinstance(identity, dict):
            active_conflict = _safe_to_dict(identity.get("active_conflict"))
            active_motives = [_safe_to_dict(m) for m in _safe_to_list(identity.get("active_motives"))]
            activated_defenses = [_safe_to_dict(d) for d in _safe_to_list(identity.get("active_defenses"))]
        elif identity is not None:
            active_conflict = _safe_to_dict(getattr(identity, "active_conflict", None))
            active_motives = [_safe_to_dict(m) for m in _safe_to_list(getattr(identity, "active_motives", None))]
            activated_defenses = [_safe_to_dict(d) for d in _safe_to_list(getattr(identity, "active_defenses", None))]

        interp = (observation or {}).get("self_interpretation", {})
        interp_summary = interp.get("summary") if isinstance(interp, dict) else str(interp or "")

        return {
            "tension_level": score,
            "tension_reason": reason,
            "self_interpretation": interp_summary,
            "active_conflict": active_conflict,
            "active_motives": active_motives[:3],
            "activated_defenses": activated_defenses[:3],
            "affect": dict(affect or {}),
            "open_loops": list(getattr(state, "open_loops", [])[-3:]),
            "latest_observation": {
                "user_act": (observation or {}).get("user_act", ""),
                "relationship_meaning": (observation or {}).get("relationship_meaning", ""),
            },
        }

    def _run_gemini_generation(self, context: dict[str, Any], score: float, reason: str) -> None:
        """Giao tiếp HTTP với Gemini service và ghi đĩa atomic khi có kết quả."""
        try:
            url = f"{self.base_url}/chat/completions"
            messages = [
                {"role": "system", "content": _GEMINI_DIARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Bối cảnh nhận thức và trạng thái căng thẳng hiện tại của Eris:\n{json.dumps(context, ensure_ascii=False, indent=2)}",
                },
            ]
            req_data = json.dumps(
                {
                    "model": self.model_name,
                    "messages": messages,
                    "temperature": 0.75,
                    "max_tokens": 1200,
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=req_data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                body = resp.read().decode("utf-8")

            parsed = json.loads(body)
            raw_content = parsed["choices"][0]["message"]["content"].strip()
            # Xử lý code fence nếu model trả về ```json ... ```
            if raw_content.startswith("```"):
                start = raw_content.find("\n") + 1
                end = raw_content.rfind("```")
                if start > 0 and end > start:
                    raw_content = raw_content[start:end].strip()

            diary_data = json.loads(raw_content)
            if isinstance(diary_data, dict) and "sections" in diary_data:
                diary_data["title"] = "Nhật ký Eris"
                diary_data["writer"] = {"role": "gemini_inner_diary", "model": self.model_name}
                diary_data["generated_at"] = round(time.time(), 3)
                diary_data["tension_score"] = score
                diary_data["tension_reason"] = reason

                # Ghi ngay ra đĩa
                self._save_to_disk(diary_data)
                logger.info("[GeminiInnerDiary] Da sinh nhat ky moi do bien dong cang thang: %s", reason)
        except Exception as exc:
            logger.warning("[GeminiInnerDiary] Sinh nhat ky qua Gemini that bai (giu ban cu tren dia): %s", exc)
        finally:
            with self._lock:
                self._is_generating = False


_GLOBAL_SERVICE: GeminiInnerDiaryService | None = None
_GLOBAL_LOCK = threading.Lock()


def get_gemini_inner_diary_service() -> GeminiInnerDiaryService:
    global _GLOBAL_SERVICE
    if _GLOBAL_SERVICE is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_SERVICE is None:
                _GLOBAL_SERVICE = GeminiInnerDiaryService()
    return _GLOBAL_SERVICE

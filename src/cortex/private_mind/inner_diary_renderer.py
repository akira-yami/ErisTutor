# -*- coding: utf-8 -*-
"""
cortex/private_mind/inner_diary_renderer.py

Render a compact inner diary digest for UI display.
Keep it short and never expose raw prompt text.
"""

from __future__ import annotations

from typing import Any, Mapping


def _short(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _num(value, default)))


def _clamp_signed(value: Any, default: float = 0.0) -> float:
    return max(-1.0, min(1.0, _num(value, default)))


class InnerDiaryRenderer:
    EMOTION_RING = (
        ("top", "hưng phấn", "joyful_excited"),
        ("left", "vui vẻ", "bright_content"),
        ("right", "khó chịu", "tense_irritated"),
        ("bottom", "bình tĩnh", "calm_centered"),
    )

    def _future_thought(self, future_bias: Mapping[str, Any]) -> str:
        """Tóm tắt định hướng sắp tới từ future_bias (inquiry_plan / speech_bias).

        Không bịa: chỉ tóm tắt dữ liệu đã có trong digest.
        """
        if not isinstance(future_bias, Mapping):
            return ""

        plan_lines: list[str] = []
        for item in _as_list(future_bias.get("inquiry_plan"))[:3]:
            if isinstance(item, Mapping):
                text = (
                    item.get("question")
                    or item.get("text")
                    or item.get("topic")
                    or item.get("theme")
                    or ""
                )
            else:
                text = item
            text = _short(text, 120)
            if text:
                plan_lines.append(text)
        if plan_lines:
            return _short("; ".join(plan_lines), 200)

        speech_bias = _as_dict(future_bias.get("speech_bias"))
        for key in ("intent", "next_move", "stance", "tone"):
            val = _short(speech_bias.get(key), 160)
            if val:
                return val
        return ""

    def _affect(self, digest: Mapping[str, Any]) -> dict[str, Any]:
        """Lấy snapshot cảm xúc liên tục thật (valence/arousal/...) nếu có.

        Ưu tiên digest['affect'] do private_mind_loop bơm vào; fallback sang
        runtime_state.private_runtime.active_state. Không có gì → {} để rơi về
        heuristic cũ (giữ tương thích).
        """
        menu = _as_dict(digest.get("menu"))
        affect = _as_dict(digest.get("affect") or menu.get("affect"))
        if affect:
            return affect
        snapshot = _as_dict(menu.get("snapshot_current"))
        runtime_state = _as_dict(snapshot.get("runtime_state"))
        active = _as_dict(_as_dict(runtime_state.get("private_runtime")).get("active_state"))
        return active

    def _affect_intensities(self, affect: Mapping[str, Any]) -> dict[str, float]:
        """Map affect liên tục → cường độ 4 góc vòng cảm xúc (0..1)."""
        val = _clamp_signed(affect.get("valence"), 0.0)
        aro = _clamp01(affect.get("arousal"), 0.0)
        irr = _clamp01(affect.get("irritation"), 0.0)
        soc = _clamp01(affect.get("social_energy"), 0.38)
        pos = max(0.0, val)
        neg = max(0.0, -val)
        return {
            "joyful_excited": _clamp01(0.45 * pos + 0.55 * aro),
            "bright_content": _clamp01(0.55 * pos + 0.30 * (1.0 - aro) + 0.20 * soc),
            "tense_irritated": _clamp01(0.60 * irr + 0.55 * neg + 0.20 * aro),
            "calm_centered": _clamp01(0.55 * (1.0 - aro) + 0.35 * (1.0 - neg) - 0.30 * irr),
        }

    def _mood_word(self, affect: Mapping[str, Any]) -> str:
        """Một từ tâm trạng từ affect thật, ngưỡng đồng bộ với emotion_debug."""
        val = _clamp_signed(affect.get("valence"), 0.0)
        aro = _clamp01(affect.get("arousal"), 0.0)
        irr = _clamp01(affect.get("irritation"), 0.0)
        fatigue = _clamp01(affect.get("fatigue"), 0.0)
        if irr >= 0.45 or val <= -0.18:
            return "đang chùng"
        if aro >= 0.6:
            return "rạo rực"
        if val >= 0.18:
            return "nhẹ nhõm"
        if fatigue >= 0.5:
            return "mỏi"
        return "êm"

    def _emotion_board(self, digest: Mapping[str, Any]) -> dict[str, Any]:
        menu = _as_dict(digest.get("menu"))
        current = _as_dict(menu.get("current_thought") or digest.get("current"))
        retained = _as_dict(menu.get("retained_thoughts") or digest.get("retained"))
        concern = _as_list(menu.get("open_concerns") or _as_dict(retained).get("concerns"))
        future_bias = _as_dict(menu.get("future_bias") or digest.get("future_bias"))
        surface_queue = _as_list(menu.get("surface_queue") or digest.get("surface_queue"))
        affect = self._affect(digest)

        center = _short(
            current.get("dominant_judgment")
            or digest.get("emotion_center")
            or digest.get("affect_summary")
            or (self._mood_word(affect) if affect else "")
            or "Ổn định",
            120,
        )
        if not center:
            center = "Ổn định"

        future_text = self._future_thought(future_bias)
        # Có affect thật thì để tâm trạng dẫn dắt subtext; không thì dùng heuristic cũ.
        if affect:
            ring_hint = self._mood_word(affect)
        elif any(str(item.get("theme") or "").strip() for item in concern):
            ring_hint = "ít căng thẳng"
        elif surface_queue:
            ring_hint = "có điều muốn nói"
        elif future_text:
            ring_hint = future_text
        else:
            ring_hint = "êm"

        real_intensity = self._affect_intensities(affect) if affect else None

        quadrant_map: dict[str, dict[str, str]] = {}
        for position, label, key in self.EMOTION_RING:
            if real_intensity is not None:
                intensity = real_intensity.get(key, 0.22)
            else:
                intensity = 0.22
                if key == "calm_centered":
                    intensity = 0.84 if center == "Ổn định" else 0.76
                elif key == "tense_irritated":
                    intensity = 0.68 if concern else 0.38
                elif key == "bright_content":
                    intensity = 0.60 if surface_queue else 0.44
                elif key == "joyful_excited":
                    intensity = 0.72 if future_bias else 0.50
            quadrant_map[position] = {"label": label, "key": key, "intensity": f"{intensity:.2f}"}

        return {
            "title": "Bảng cảm xúc",
            "shape": "circle",
            "center": {"label": center, "subtext": ring_hint},
            "quadrants": quadrant_map,
            "axes": ["vui vẻ", "hưng phấn", "khó chịu", "bình tĩnh"],
            "decor": {
                "style": "soft_pink_glow",
                "border": "rose",
                "markers": ["top", "left", "right", "bottom"],
                "style_hint": "reference_mockup",
            },
            "layout": {
                "type": "radial_board",
                "center_pulse": center,
                "orbit_labels": {
                    "top": "hưng phấn",
                    "left": "vui vẻ",
                    "right": "khó chịu",
                    "bottom": "bình tĩnh",
                },
                "accent": "pink_glass",
            },
        }

    @staticmethod
    def _norm_line(text: Any) -> str:
        return " ".join(str(text or "").split()).casefold()

    @classmethod
    def _dedupe_sections(cls, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bỏ item trùng/chứa nhau giữa các ô (giữ nguyên bảng cảm xúc).

        Đây là lưới an toàn cuối: dù upstream sạch, khi LLM writer chết fallback vẫn
        có thể đổ cùng một câu vào nhiều ô. Mỗi câu chỉ nên xuất hiện một lần.
        """
        seen: list[str] = []

        def is_dup(text: str) -> bool:
            n = cls._norm_line(text)
            if not n:
                return False
            for s in seen:
                if n == s or n in s or s in n:
                    return True
                nt, st = set(n.split()), set(s.split())
                if nt and st and (len(nt & st) / len(nt | st)) >= 0.8:
                    return True
            return False

        out: list[dict[str, Any]] = []
        for section in sections:
            if section.get("title") == "Bảng cảm xúc":
                out.append(section)
                continue
            kept_items: list[dict[str, Any]] = []
            for item in section.get("items", []):
                text = str((item or {}).get("text") or "")
                if not text or is_dup(text):
                    continue
                seen.append(cls._norm_line(text))
                kept_items.append(item)
            if not kept_items:
                continue
            section = dict(section)
            section["items"] = kept_items
            section["lines"] = [
                line for line in section.get("lines", [])
                if cls._norm_line(line) in {cls._norm_line(i.get("text")) for i in kept_items}
                or str(line or "").startswith("- ")
            ]
            if not section["lines"]:
                section["lines"] = [str(i.get("text") or "") for i in kept_items]
            out.append(section)
        return out

    def _memory_echo(self, digest: Mapping[str, Any], voice: Mapping[str, Any]) -> dict[str, Any]:
        """Ký ức vọng lại có provenance thật, không lấy open loop giả làm memory."""

        menu = _as_dict(digest.get("menu"))
        dossier = _as_dict(menu.get("evidence_dossier") or digest.get("evidence_dossier"))
        hits = [_as_dict(item) for item in _as_list(dossier.get("memory_hits"))]
        first = next((item for item in hits if _short(item.get("text") or item.get("summary") or item.get("claim"), 180)), {})
        authored = _short(voice.get("memory_echo"), 220)
        raw_text = _short(first.get("text") or first.get("summary") or first.get("claim"), 220)
        text = authored or raw_text
        if not text:
            return {}
        return {
            "text": text,
            "evidence_id": _short(first.get("evidence_id") or first.get("id"), 120),
            "source": _short(first.get("source"), 80) or "memory_hit",
            "authored": bool(authored),
        }

    def render(self, digest: Mapping[str, Any], voice: Mapping[str, Any] | None = None) -> dict[str, Any]:
        menu = _as_dict(digest.get("menu"))
        current = _as_dict(menu.get("current_thought") or digest.get("current"))
        temporary = _as_dict(menu.get("temporary_thoughts") or digest.get("temporary"))
        retained = _as_dict(menu.get("retained_thoughts") or digest.get("retained"))
        surface_queue = _as_list(menu.get("surface_queue") or digest.get("surface_queue"))
        concerns = _as_list(menu.get("open_concerns") or _as_dict(retained).get("concerns"))
        future_bias = _as_dict(menu.get("future_bias") or digest.get("future_bias"))
        resolved = _as_list(menu.get("recently_resolved") or digest.get("recently_resolved"))
        residue = _as_dict(menu.get("diary_residue") or digest.get("diary_residue"))
        diary_entries = _as_list(retained.get("diary_entries") or residue.get("diary_entries"))
        retained_by_kind: dict[str, list[dict[str, Any]]] = {}
        for raw in diary_entries[:12]:
            item = _as_dict(raw)
            text = _short(item.get("text"), 220)
            if not text:
                continue
            kind = str(item.get("kind") or "reflection").strip().lower()
            retained_by_kind.setdefault(kind, []).append(item)

        future_text = self._future_thought(future_bias)
        voice = _as_dict(voice)

        def _voice(key: str) -> str:
            return _short(voice.get(key), 700 if key == "phan_tich" else 240)

        sections: list[dict[str, Any]] = []
        emotion_board = self._emotion_board(digest)
        quadrants = emotion_board["quadrants"]
        sections.append({
            "title": emotion_board["title"],
            "items": [
                {"text": emotion_board["center"]["label"], "badge": emotion_board["center"]["subtext"]},
                {"text": quadrants["top"]["label"], "badge": quadrants["top"]["intensity"]},
                {"text": quadrants["left"]["label"], "badge": quadrants["left"]["intensity"]},
                {"text": quadrants["right"]["label"], "badge": quadrants["right"]["intensity"]},
                {"text": quadrants["bottom"]["label"], "badge": quadrants["bottom"]["intensity"]},
            ],
            "lines": [emotion_board["center"]["label"], emotion_board["center"]["subtext"]],
        })

        # Suy nghĩ hiện tại — ưu tiên giọng Eris, fallback judgment thô.
        retained_current = retained_by_kind.get("current", [])
        hien_tai = (
            _voice("hien_tai")  # compat V79 cache only
            or _short(current.get("dominant_judgment"), 160)
            or (_short(retained_current[0].get("text"), 180) if retained_current else "")
        )
        if hien_tai:
            sections.append({
                "title": "Suy nghĩ hiện tại",
                "items": [{"text": hien_tai, "badge": ""}],
                "lines": [hien_tai],
            })

        # Suy nghĩ tương lai
        retained_future = retained_by_kind.get("future", [])
        tuong_lai = (
            _voice("tuong_lai")  # compat V79 cache only
            or future_text
            or (_short(retained_future[0].get("text"), 180) if retained_future else "")
        )
        if tuong_lai:
            sections.append({
                "title": "Suy nghĩ tương lai",
                "items": [{"text": tuong_lai, "badge": ""}],
                "lines": [tuong_lai],
            })

        # Đang bận tâm
        ban_tam_voice = _voice("ban_tam")
        if ban_tam_voice:
            sections.append({
                "title": "Đang bận tâm",
                "items": [{"text": ban_tam_voice, "badge": ""}],
                "lines": [ban_tam_voice],
            })
        else:
            concern_items: list[dict[str, str]] = []
            for item in [_as_dict(c) for c in concerns[:3]]:
                theme = _short(item.get("theme"), 120)
                if not theme:
                    continue
                pressure = item.get("pressure")
                try:
                    badge = f"áp lực {float(pressure or 0):.2g}"
                except Exception:
                    badge = ""
                concern_items.append({"text": theme, "badge": badge})
            for item in retained_by_kind.get("concern", [])[:2]:
                text = _short(item.get("text"), 160)
                if text and all(self._norm_line(x.get("text")) != self._norm_line(text) for x in concern_items):
                    concern_items.append({"text": text, "badge": str(item.get("state") or "")})
            if concern_items:
                sections.append({
                    "title": "Đang bận tâm",
                    "items": concern_items,
                    "lines": [f"- {item['text']}" for item in concern_items],
                })

        # Phân tích gần đây — ưu tiên giọng nếu có; nếu không thì rơi về
        # fragment/retained raw-state ở nhánh else.
        phan_tich_voice = _voice("phan_tich")
        if phan_tich_voice:
            sections.append({
                "title": "Phân tích gần đây",
                "items": [{"text": phan_tich_voice, "badge": ""}],
                "lines": [phan_tich_voice],
            })
        else:
            notice_items: list[dict[str, str]] = []
            for item in [_as_dict(f) for f in _as_list(temporary.get("fragments"))[:3]]:
                claim = _short(item.get("claim"), 120)
                if not claim:
                    continue
                score = item.get("score")
                try:
                    badge = f"score {float(score or 0):.2f}"
                except Exception:
                    badge = ""
                notice_items.append({"text": claim, "badge": badge})
            for item in retained_by_kind.get("reflection", [])[:3]:
                text = _short(item.get("text"), 180)
                if text and all(self._norm_line(x.get("text")) != self._norm_line(text) for x in notice_items):
                    notice_items.append({"text": text, "badge": str(item.get("state") or "")})
            if notice_items:
                sections.append({
                    "title": "Phân tích gần đây",
                    "items": notice_items,
                    "lines": [f"- {item['text']}" for item in notice_items],
                })

        # Muốn nói
        muon_noi = _voice("muon_noi")
        if not muon_noi and surface_queue:
            muon_noi = _short(
                _as_dict(surface_queue[0]).get("topic_seed")
                or _as_dict(surface_queue[0]).get("text")
                or "",
                120,
            )
        if not muon_noi:
            retained_desire = retained_by_kind.get("desire", [])
            if retained_desire:
                muon_noi = _short(retained_desire[0].get("text"), 180)
        if muon_noi:
            sections.append({
                "title": "Muốn nói",
                "items": [{"text": muon_noi, "badge": ""}],
                "lines": [muon_noi],
            })

        # Đã nguôi — chuyện vừa khép lại (settled hoặc thôi đào sâu). Cho diary biết
        # kể về quá khứ, không chỉ kẹt ở hiện tại. Đây là cái lấp 'Memory Echo' trống.
        resolved_items: list[dict[str, str]] = []
        for item in [_as_dict(r) for r in resolved[:3]]:
            note = _short(item.get("closure_note") or item.get("theme"), 160)
            if not note:
                continue
            theme = _short(item.get("theme"), 80)
            resolved_items.append({"text": note, "badge": theme if theme and theme not in note else ""})
        if resolved_items:
            sections.append({
                "title": "Đã nguôi",
                "items": resolved_items,
                "lines": [item["text"] for item in resolved_items],
            })

        # Chốt chặn cuối: chống trùng chéo giữa các ô. Khi LLM writer chết và fallback
        # deterministic đổ cùng một câu vào nhiều ô (đúng kịch bản '1 câu 5 ô' cũ),
        # bỏ bớt dòng đã xuất hiện ở ô trước — trừ bảng cảm xúc (nhãn cố định).
        sections = self._dedupe_sections(sections)

        text_parts: list[str] = []
        for section in sections:
            text_parts.append(str(section["title"]) + ":")
            text_parts.extend(str(line) for line in section.get("lines", ()))
            text_parts.append("")

        memory_echo = self._memory_echo(digest, voice)
        writer = _as_dict(voice.get("_writer"))

        return {
            "mode": "debug_private",
            "title": "Inner Diary",
            "text": "\n".join(text_parts).strip(),
            "sections": sections,
            "emotion_board": emotion_board,
            "memory_echo": memory_echo,
            "writer": writer,
            "residue": {
                "retained_count": len(diary_entries),
                "pending_count": int(residue.get("pending_count", 0) or 0),
                "curator": _as_dict(residue.get("curator")),
            },
        }

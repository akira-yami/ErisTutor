# -*- coding: utf-8 -*-
"""V86 - Projection thống kê năng lực + coach dài hạn cho Eris Desktop.

Boundary:
- Đây là projection/read-model, KHÔNG phải một agent mới.
- Không gọi LLM, không chạy tool, không điều khiển GitPilot.
- Không đủ evidence thì trả ``observed=false`` thay vì bịa 50%.
- Recommendation luôn đi kèm reason/evidence và chỉ mang tính gợi ý.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from runtime.workspace_manager import default_eris_home

_SCHEMA = "eris.capability_panel.v1"
_MIN_EVIDENCE = 2
_MIN_CONFIDENCE = 0.16
_MIN_GROUP_COVERAGE = 0.67

_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("debugging", "Gỡ lỗi", (
        "debugging.failure_localization",
        "debugging.call_flow_tracing",
        "debugging.root_cause_reasoning",
    )),
    ("architecture", "Kiến trúc", ("architecture.system_boundaries",)),
    ("verification", "Kiểm chứng", (
        "verification.testing",
        "verification.diff_review",
    )),
    ("ai_operation", "Vận hành AI", (
        "ai_operation.task_scoping",
        "ai_operation.evidence_review",
    )),
)

_SKILL_LABELS = {
    "debugging.failure_localization": "Định vị nơi phát sinh lỗi",
    "debugging.call_flow_tracing": "Theo dấu luồng gọi",
    "debugging.root_cause_reasoning": "Phân biệt gốc lỗi / triệu chứng",
    "verification.testing": "Kiểm thử có mục tiêu",
    "verification.diff_review": "Đọc diff và kiểm chứng thay đổi",
    "architecture.system_boundaries": "Ranh giới hệ thống / ownership",
    "ai_operation.task_scoping": "Khoanh phạm vi task",
    "ai_operation.evidence_review": "Kiểm tra evidence của AI",
}

_TOOL_LABELS = {
    "code_search": "Search / References / Call hierarchy",
    "vscode_diagnostics": "VS Code Problems / Diagnostics",
    "git_diff": "Git Diff / Source Control",
    "targeted_tests": "Kiểm thử có mục tiêu",
    "stack_log": "Stack trace / Runtime log",
    "debugger": "Debugger / Breakpoint / Watch",
    "profiler": "Profiler",
    "godot_remote_scene": "Godot Remote Scene Tree",
    "godot_debugger": "Godot Debugger / Monitors",
}

_STAGE_VI = {
    "unobserved": "Chưa đủ dữ liệu",
    "introduced": "Đã được giới thiệu",
    "demonstrated": "Đã dùng được",
    "familiar": "Quen thuộc",
}


def _pct(value: float) -> int:
    return max(0, min(100, int(round(float(value) * 100.0))))


def _confidence_label(value: float) -> str:
    value = float(value or 0.0)
    if value >= 0.65:
        return "Cao"
    if value >= 0.35:
        return "Khá"
    if value >= _MIN_CONFIDENCE:
        return "Trung bình"
    return "Thấp"


def _evidence_rows(raw: Mapping[str, Any]) -> list[dict[str, str]]:
    """V87: chiếu evidence theo mô tả cố định, không lộ raw chat/source.

    ``notes`` của skill model chỉ chứa các câu do runtime tự sinh + evidence_ref ngắn.
    Dù vậy projection vẫn whitelist/translate để panel không vô tình trở thành nơi
    dump internal state nếu format notes thay đổi sau này.
    """
    out: list[dict[str, str]] = []
    translations = {
        "User correction contradicted mentor/model estimate; confidence reduced.":
            "Bạn đã sửa lại nhận định của ERIS; độ tin cậy của estimate đã được giảm.",
        "User stated prior knowledge; treat as correction/self-report, not mastery proof.":
            "Bạn cho biết đã biết phần này; ERIS coi đây là self-report, không tự tăng mức thành thạo.",
        "Candidate misconception supplied counter-evidence; estimate softened.":
            "Một heuristic kỹ thuật có dấu hiệu sai đã được ghi nhận như counter-evidence; estimate được hạ nhẹ thay vì gắn nhãn năng lực.",
    }
    for note in list(raw.get("notes") or [])[-5:]:
        text = " ".join(str(note or "").split())[:260]
        if not text:
            continue
        ref = ""
        if " [" in text and text.endswith("]"):
            body, maybe_ref = text.rsplit(" [", 1)
            text, ref = body, maybe_ref[:-1][:80]
        allowed_user_notes = (
            "User nêu hypothesis", "User đang trace", "User tự đề xuất hypothesis",
            "User chủ động yêu cầu review", "User chủ động quan tâm validation",
        )
        safe = translations.get(
            text,
            text if (not text.startswith("User ") or text.startswith(allowed_user_notes))
            else "Bằng chứng/correction kỹ thuật đã được ghi nhận.",
        )
        out.append({"mo_ta": safe[:220], "tham_chieu": ref})
    return list(reversed(out))


def _skill_view(skill_id: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    evidence = int(raw.get("evidence_count", 0) or 0)
    independent = int(raw.get("independent_evidence_count", evidence) or 0)
    counter = int(raw.get("counter_evidence_count", 0) or 0)
    confidence = float(raw.get("confidence", 0.0) or 0.0)
    observed = independent >= _MIN_EVIDENCE and confidence >= _MIN_CONFIDENCE
    return {
        "id": skill_id,
        "ten": _SKILL_LABELS.get(skill_id, skill_id),
        "da_du_bang_chung": observed,
        "phan_tram": _pct(float(raw.get("level", 0.5) or 0.5)) if observed else None,
        "do_tin_cay": _confidence_label(confidence) if observed else "Chưa đủ dữ liệu",
        "so_bang_chung": evidence,
        "bang_chung_doc_lap": independent,
        "counter_evidence": counter,
        "cap_nhat_luc": float(raw.get("last_observed_at", 0.0) or 0.0),
        "bang_chung_gan_day": _evidence_rows(raw),
    }


@dataclass(slots=True)
class CapabilityProjectionService:
    """Build panel snapshot + lưu một history point/ngày để trend là số thật."""

    history_path: Path | None = None
    logger: Any = None

    def __post_init__(self) -> None:
        if self.history_path is None:
            self.history_path = default_eris_home() / "mentor" / "capability_history.jsonl"

    def build(self, mentor_state: Mapping[str, Any], *, now: float | None = None) -> dict[str, Any]:
        ts = float(now or time.time())
        model = dict(mentor_state.get("model") or {})
        skills_raw = dict(model.get("skills") or {})
        skill_views = {
            skill_id: _skill_view(skill_id, dict(skills_raw.get(skill_id) or {}))
            for skill_id in _SKILL_LABELS
        }

        groups: list[dict[str, Any]] = []
        for group_id, title, members in _GROUPS:
            observed_members = [skill_views[x] for x in members if skill_views[x]["da_du_bang_chung"]]
            total_evidence = sum(int(skill_views[x]["so_bang_chung"]) for x in members)
            coverage = len(observed_members) / max(1, len(members))
            enough_coverage = coverage >= _MIN_GROUP_COVERAGE
            if observed_members and enough_coverage:
                score = int(round(sum(int(x["phan_tram"]) for x in observed_members) / len(observed_members)))
                conf_values = [float(skills_raw.get(x["id"], {}).get("confidence", 0.0) or 0.0) for x in observed_members]
                confidence = sum(conf_values) / len(conf_values)
                observed = True
            else:
                score, confidence, observed = None, 0.0, False
            groups.append({
                "id": group_id,
                "ten": title,
                "da_du_bang_chung": observed,
                "phan_tram": score,
                "do_tin_cay": _confidence_label(confidence) if observed else "Chưa đủ dữ liệu",
                "so_bang_chung": total_evidence,
                "do_phu": round(coverage, 3),
                "ky_nang_da_quan_sat": len(observed_members),
                "tong_ky_nang": len(members),
                "ghi_chu_do_phu": (
                    f"Đã quan sát {len(observed_members)}/{len(members)} chiều kỹ năng; chưa hiển thị điểm nhóm."
                    if observed_members and not enough_coverage else ""
                ),
                "ky_nang": [skill_views[x] for x in members],
            })

        tool_state = dict(mentor_state.get("tool_literacy") or {})
        tools_raw = dict(tool_state.get("tools") or {})
        tools = []
        for tool_id, title in _TOOL_LABELS.items():
            raw = dict(tools_raw.get(tool_id) or {})
            stage = str(raw.get("stage") or "unobserved")
            tools.append({
                "id": tool_id,
                "ten": title,
                "muc_do": _STAGE_VI.get(stage, "Chưa đủ dữ liệu"),
                "stage": stage,
                "so_lan_chung_minh": int(raw.get("demonstrated_count", 0) or 0),
                "so_lan_duoc_goi_y": int(raw.get("exposures", 0) or 0),
                "do_tin_cay": _confidence_label(float(raw.get("confidence", 0.0) or 0.0)) if stage != "unobserved" else "Chưa đủ dữ liệu",
            })

        recent_signals = [x for x in (model.get("recent_signals") or []) if isinstance(x, Mapping)]
        cutoff = ts - 30 * 86400.0
        recent_signals = [x for x in recent_signals if float(x.get("at", 0.0) or 0.0) >= cutoff]
        signal_counts = {"grounded_hypothesis": 0, "verification_intent": 0, "blind_execution_request": 0, "learning_intent": 0}
        for event in recent_signals:
            for signal in event.get("signals") or ():
                if signal in signal_counts:
                    signal_counts[signal] += 1
        technical_turns = len(recent_signals)
        ai_stats = {
            "cua_so_ngay": 30,
            "luot_ky_thuat_co_signal": technical_turns,
            "luot_ky_thuat_da_quan_sat": technical_turns,
            "tu_neu_gia_thuyet_co_evidence": signal_counts["grounded_hypothesis"],
            "chu_dong_kiem_chung": signal_counts["verification_intent"],
            "chu_dong_hoc_hoi": signal_counts["learning_intent"],
            "yeu_cau_thuc_thi_mu": signal_counts["blind_execution_request"],
            "rui_ro_phu_thuoc_ai": _pct(float(model.get("overreliance_risk", 0.0) or 0.0)),
            "rui_ro_mat_ownership_reasoning": _pct(float(model.get("overreliance_risk", 0.0) or 0.0)),
        }

        calibration = dict(mentor_state.get("mentor_calibration") or {})
        metrics = dict(calibration.get("metrics") or {})
        mentor_stats = {
            "so_lan_can_thiep": int(metrics.get("interventions", 0) or 0),
            "user_sua_eris": int(metrics.get("user_corrected_eris", 0) or 0),
            "user_da_biet_san": int(metrics.get("user_already_knew", 0) or 0),
            "user_thay_huu_ich": int(metrics.get("helpful", 0) or 0),
            "user_bao_qua_dai": int(metrics.get("too_verbose", 0) or 0),
            "user_tat_mentor": int(metrics.get("mentor_opt_out", 0) or 0),
        }

        history = self._load_history()
        current_scores = {g["id"]: g["phan_tram"] for g in groups if g["da_du_bang_chung"]}
        trend = self._trend(history, current_scores, ts)
        recommendations = self._recommend(groups, tools, ai_stats)
        self._append_daily(ts, current_scores)

        total_evidence = sum(int(v["so_bang_chung"]) for v in skill_views.values())
        return {
            "schema": _SCHEMA,
            "tieu_de": "NĂNG LỰC KỸ THUẬT",
            "ghi_chu": "Các phần trăm chỉ hiện khi đủ evidence độc lập và độ phủ; đây là estimate năng lực kỹ thuật, không phải điểm thi hay đánh giá con người.",
            "cap_nhat_luc": ts,
            "tong_bang_chung": total_evidence,
            "nhom_nang_luc": groups,
            "cong_cu": tools,
            "thoi_quen_ai_30_ngay": ai_stats,
            "mentor": mentor_stats,
            "xu_huong_30_ngay": trend,
            "de_xuat_dai_han": recommendations,
        }

    def _recommend(self, groups: list[dict[str, Any]], tools: list[dict[str, Any]], ai: Mapping[str, Any]) -> list[dict[str, Any]]:
        recs: list[dict[str, Any]] = []
        observed = [g for g in groups if g.get("da_du_bang_chung")]
        if observed:
            weakest = min(observed, key=lambda x: int(x.get("phan_tram") or 101))
            recs.append({
                "id": f"group-{str(weakest.get('id') or 'unknown')}",
                "tieu_de": f"Củng cố {str(weakest.get('ten') or '').lower()}",
                "uu_tien": "Cao",
                "thoi_han": "2 tuần",
                "ly_do": f"Đây là nhóm có estimate thấp nhất trong các nhóm đã đủ evidence ({weakest.get('phan_tram')}%).",
                "thuc_hanh": "Chọn 2 task thật và tự nêu giả thuyết + evidence cần xem trước khi giao phần implementation cho AI.",
                "dau_hieu_tien_bo": "Có thêm ít nhất 3 evidence độc lập mà không cần mentor nhắc bước đầu.",
                "metric": {"kind": "group_evidence", "id": str(weakest.get("id") or ""), "target_delta": 3},
            })
        unknown = [t for t in tools if t.get("stage") == "unobserved"]
        preferred = next((t for t in unknown if t.get("id") in {"debugger", "profiler", "godot_remote_scene"}), None)
        if preferred:
            recs.append({
                "id": f"tool-{str(preferred.get('id') or 'unknown')}",
                "tieu_de": f"Tạo evidence thật với {preferred['ten']}",
                "uu_tien": "Trung bình",
                "thoi_han": "2–4 tuần",
                "ly_do": "ERIS chưa có đủ dữ liệu để biết bạn vận hành công cụ này tới đâu; chưa đủ dữ liệu không đồng nghĩa kỹ năng yếu.",
                "thuc_hanh": "Khi gặp đúng loại lỗi, tự dùng công cụ một lần và nói lại evidence bạn nhìn thấy.",
                "dau_hieu_tien_bo": "Có ít nhất 2 lần sử dụng được quan sát với kết quả/evidence cụ thể.",
                "metric": {"kind": "tool_demonstrated", "id": str(preferred.get("id") or ""), "target_delta": 2},
            })
        if int(ai.get("yeu_cau_thuc_thi_mu", 0) or 0) >= 3:
            recs.append({
                "id": "ai-verification-ownership",
                "tieu_de": "Giữ ownership của reasoning trước khi Execute",
                "uu_tien": "Cao",
                "thoi_han": "2 tuần",
                "ly_do": f"30 ngày gần đây ghi nhận {int(ai.get('yeu_cau_thuc_thi_mu', 0) or 0)} lượt yêu cầu thực thi mà không kèm learning/review/hypothesis signal.",
                "thuc_hanh": "Trước 3 task tiếp theo, tự viết một câu: lỗi có khả năng nằm ở layer nào và evidence nào sẽ bác bỏ giả thuyết đó.",
                "dau_hieu_tien_bo": "Tỷ lệ task có grounded hypothesis hoặc verification signal tăng lên.",
                "metric": {"kind": "ai_signal", "id": "chu_dong_kiem_chung", "target_delta": 3},
            })
        return recs[:3]

    @staticmethod
    def _trend(history: list[dict[str, Any]], current: Mapping[str, int], ts: float) -> list[dict[str, Any]]:
        cutoff = ts - 30 * 86400.0
        # Không gọi hai lần trong cùng ngày rồi tự báo "ổn định 0%". Muốn gọi là
        # xu hướng thì tối thiểu phải có một snapshot từ ngày trước.
        candidates = [
            x for x in history
            if cutoff <= float(x.get("at", 0.0) or 0.0) <= ts - 86400.0
        ]
        old = candidates[0] if candidates else None
        if not old:
            return [{"id": k, "delta": None, "nhan": "Chưa đủ lịch sử"} for k in current]
        old_scores = dict(old.get("scores") or {})
        out = []
        for key, value in current.items():
            if key not in old_scores or old_scores[key] is None:
                out.append({"id": key, "delta": None, "nhan": "Chưa đủ lịch sử"})
            else:
                delta = int(value) - int(old_scores[key])
                out.append({"id": key, "delta": delta, "nhan": "Tăng" if delta > 0 else "Giảm" if delta < 0 else "Ổn định"})
        return out

    def _load_history(self) -> list[dict[str, Any]]:
        try:
            if not self.history_path or not self.history_path.exists():
                return []
            rows = []
            for line in self.history_path.read_text(encoding="utf-8").splitlines()[-120:]:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except Exception:
                    continue
            return rows
        except Exception:
            return []

    def _append_daily(self, ts: float, scores: Mapping[str, int]) -> None:
        if not self.history_path:
            return
        day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        rows = self._load_history()
        if rows and str(rows[-1].get("day") or "") == day:
            return
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            with self.history_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"schema": "eris.capability_history.v1", "day": day, "at": ts, "scores": dict(scores)}, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception as exc:
            if self.logger is not None:
                fn = getattr(self.logger, "debug", None)
                if callable(fn):
                    fn("[v86] capability history write skipped: %s", exc)


__all__ = ["CapabilityProjectionService"]

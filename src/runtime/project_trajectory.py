# -*- coding: utf-8 -*-
"""V88 - Project Trajectory Coach: analytics read-only cho sức khỏe dự án.

LƯU Ý SAU COMPANION CONSOLIDATION:
Service này là LEGACY/manual analytics. Desktop không boot hoặc inject nó tự động nữa
vì nguồn CodingObservation gắn với coding extension trái boundary companion độc lập.
Chỉ dùng thủ công khi user chủ động yêu cầu phân tích journal cũ.

Triết lý:
- Không tạo project score giả khoa học.
- File sửa nhiều chỉ là hotspot cần xem lại, KHÔNG đồng nghĩa code xấu.
- Test fail sau patch chỉ là tín hiệu execution; không tự gọi đó là regression nếu
  telemetry không chứng minh được causality.
- Recommendation phải có evidence đếm được và chỉ mang tính gợi ý.
- Không gọi LLM, không sửa file, không chạy test, không điều khiển GitPilot.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from runtime.coding_observation import CodingFlowInterpreter, sanitize_coding_observation
from runtime.workspace_manager import default_eris_home

_SCHEMA = "eris.project_trajectory.v1"
_PROJECT_QUERY_MARKERS = (
    "project", "dự án", "roadmap", "nợ kỹ thuật", "technical debt", "hotspot", "điểm nóng",
    "subsystem", "module nào", "chỗ nào hay lỗi", "repo đang", "sức khỏe repo", "sức khoẻ repo",
    "ưu tiên dự án", "ưu tiên project", "2 tuần tới", "4 tuần tới", "tháng tới",
)


def _clean_path(value: Any) -> str:
    return "/".join(part for part in str(value or "").replace("\\", "/").split("/") if part not in {"", "."})[:520]


def _relative_file(path: str, workspace: str) -> str:
    p = _clean_path(path)
    w = _clean_path(workspace)
    if w and p.casefold().startswith(w.casefold().rstrip("/") + "/"):
        p = p[len(w.rstrip("/")) + 1 :]
    return p


def _subsystem(path: str) -> str:
    """Nhóm path theo cấu trúc repo, không đoán domain bằng AI."""
    parts = [x for x in _clean_path(path).split("/") if x]
    if not parts:
        return "Khác"
    lowered = [x.casefold() for x in parts]
    if "vscode-extension" in lowered:
        return "Eris Code / VS Code"
    if len(parts) >= 2 and lowered[0] == "services" and lowered[1] == "gitpilot":
        return "Eris Code"
    if len(parts) >= 2 and lowered[0] == "src" and lowered[1] == "runtime":
        return "Runtime"
    if len(parts) >= 2 and lowered[0] == "src" and lowered[1] == "body":
        return "Desktop UI"
    if lowered[0] in {"tests", "test"} or "tests" in lowered[:3]:
        return "Tests"
    if len(parts) >= 2 and parts[0] in {"src", "app", "apps", "addons", "packages", "services"}:
        return "/".join(parts[:2])
    return parts[0]


def _qualitative_stability(tasks: int, risk_tasks: int) -> str:
    if tasks < 2:
        return "Chưa đủ dữ liệu"
    ratio = risk_tasks / max(1, tasks)
    if ratio >= 0.50:
        return "Cần xem lại"
    if ratio >= 0.25:
        return "Trung bình"
    return "Khá"


@dataclass(slots=True)
class ProjectTrajectoryService:
    """Đọc CodingObservation JSONL và dựng projection 30 ngày, không mutate source."""

    journal_path: Path | None = None
    logger: Any = None
    max_lines: int = 8000

    def __post_init__(self) -> None:
        if self.journal_path is None:
            self.journal_path = default_eris_home() / "logs" / "coding_observations.jsonl"

    def build(self, *, now: float | None = None, window_days: int = 30) -> dict[str, Any]:
        ts = float(now or time.time())
        cutoff = ts - max(1, int(window_days)) * 86400.0
        events = [e for e in self._load_events() if float(e.get("at", 0.0) or 0.0) >= cutoff]
        if not events:
            return self._empty(ts, window_days)

        interpreter = CodingFlowInterpreter()
        flows: dict[str, dict[str, Any]] = {}
        for event in events:
            try:
                flow = interpreter.observe(event)
                if flow:
                    flows[str(flow.get("task_id") or "")] = flow
            except Exception:
                continue

        task_ids = {str(e.get("task_id") or "") for e in events if str(e.get("task_id") or "")}
        workspaces = Counter(str(e.get("workspace") or "") for e in events if str(e.get("workspace") or ""))
        workspace = workspaces.most_common(1)[0][0] if workspaces else ""

        task_terminal: dict[str, str] = {}
        changed_by_task: dict[str, set[str]] = defaultdict(set)
        failed_test_tasks: set[str] = set()
        test_seen_tasks: set[str] = set()
        reviewer_tasks: set[str] = set()
        for event in events:
            tid = str(event.get("task_id") or "")
            kind = str(event.get("event_type") or "")
            actor = str(event.get("actor") or "")
            if kind in {"task_completed", "task_failed", "task_cancelled"}:
                task_terminal[tid] = kind
            if kind == "file_changed":
                for raw in event.get("files") or ():
                    rel = _relative_file(str(raw), str(event.get("workspace") or workspace))
                    if rel:
                        changed_by_task[tid].add(rel)
            if kind == "test_result":
                test_seen_tasks.add(tid)
                if int((event.get("metrics") or {}).get("failed", 0) or 0) > 0:
                    failed_test_tasks.add(tid)
            if actor == "reviewer" and kind in {"phase_changed", "phase_summary", "tool_started", "tool_finished"}:
                reviewer_tasks.add(tid)

        completed = sum(1 for x in task_terminal.values() if x == "task_completed")
        failed = sum(1 for x in task_terminal.values() if x == "task_failed")
        cancelled = sum(1 for x in task_terminal.values() if x == "task_cancelled")
        changed_tasks = {tid for tid, files in changed_by_task.items() if files}

        file_tasks: dict[str, set[str]] = defaultdict(set)
        subsystem_tasks: dict[str, set[str]] = defaultdict(set)
        subsystem_risk_tasks: dict[str, set[str]] = defaultdict(set)
        for tid, files in changed_by_task.items():
            flow = flows.get(tid) or {}
            risky = bool(int(flow.get("failed_test_runs", 0) or 0) > 0 or list(flow.get("findings") or []))
            for path in files:
                file_tasks[path].add(tid)
                system = _subsystem(path)
                subsystem_tasks[system].add(tid)
                if risky:
                    subsystem_risk_tasks[system].add(tid)

        hotspots = []
        for path, tids in sorted(file_tasks.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:8]:
            related_failed = sum(1 for tid in tids if tid in failed_test_tasks)
            hotspots.append({
                "file": path,
                "so_task_cham": len(tids),
                "task_co_test_fail": related_failed,
                "nhan": "Điểm nóng cần xem lại" if len(tids) >= 3 else "Được sửa lặp lại",
                "ghi_chu": "Tần suất sửa chỉ là tín hiệu quan sát; không đồng nghĩa file này có chất lượng kém.",
            })

        subsystems = []
        for name, tids in sorted(subsystem_tasks.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:8]:
            risk = len(subsystem_risk_tasks.get(name, set()))
            subsystems.append({
                "ten": name,
                "so_task": len(tids),
                "task_co_tin_hieu_rui_ro": risk,
                "tin_hieu_on_dinh": _qualitative_stability(len(tids), risk),
            })

        finding_counter: Counter[str] = Counter()
        no_verify_tasks: set[str] = set()
        scope_tasks: set[str] = set()
        repeated_failure_tasks: set[str] = set()
        for tid, flow in flows.items():
            for finding in flow.get("findings") or ():
                if not isinstance(finding, Mapping):
                    continue
                code = str(finding.get("code") or "")
                if code:
                    finding_counter[code] += 1
                if code == "no_structured_verification":
                    no_verify_tasks.add(tid)
                elif code == "scope_expansion":
                    scope_tasks.add(tid)
                elif code in {"repeated_same_failure", "repeated_test_failure"}:
                    repeated_failure_tasks.add(tid)

        debts = self._candidate_debts(
            hotspots=hotspots,
            no_verify=len(no_verify_tasks),
            scope_expansion=len(scope_tasks),
            repeated_failures=len(repeated_failure_tasks),
            changed_tasks=len(changed_tasks),
            reviewer_tasks=len(reviewer_tasks & changed_tasks),
        )
        priorities = self._priorities(
            hotspots=hotspots,
            no_verify=len(no_verify_tasks),
            scope_expansion=len(scope_tasks),
            repeated_failures=len(repeated_failure_tasks),
            failed_tests=len(failed_test_tasks),
        )

        return {
            "schema": _SCHEMA,
            "tieu_de": "SỨC KHỎE DỰ ÁN · 30 NGÀY",
            "cap_nhat_luc": ts,
            "cua_so_ngay": int(window_days),
            "du_lieu_day_du": len(task_ids) >= 2,
            "ghi_chu": "Đây là analytics từ CodingObservation. Không có điểm tổng cho project và không suy causality khi telemetry chưa chứng minh.",
            "workspace": workspace,
            "tong_quan": {
                "task_da_quan_sat": len(task_ids),
                "task_hoan_thanh": completed,
                "task_that_bai": failed,
                "task_huy": cancelled,
                "task_co_thay_doi_file": len(changed_tasks),
                "task_co_test": len(test_seen_tasks),
                "task_co_test_fail": len(failed_test_tasks),
                "task_co_reviewer": len(reviewer_tasks),
                "task_thieu_verification_co_cau_truc": len(no_verify_tasks),
            },
            "subsystem": subsystems,
            "diem_nong": hotspots,
            "no_ky_thuat_ung_vien": debts,
            "uu_tien_de_xuat": priorities,
            "flow_findings": dict(finding_counter),
        }

    def decorate(self, panel: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        """Ghép YOU × PROJECT bằng projection user-visible, không đọc private skill state."""
        project = self.build(now=now)
        panel["du_an_30_ngay"] = project
        panel["ghep_user_du_an"] = self._user_project_fit(panel, project)
        return panel

    def prompt_context(self, user_text: str, *, now: float | None = None) -> str:
        text = " ".join(str(user_text or "").casefold().split())
        if not text or not any(marker in text for marker in _PROJECT_QUERY_MARKERS):
            return ""
        snapshot = self.build(now=now)
        compact = {
            "schema": snapshot.get("schema"),
            "tong_quan": snapshot.get("tong_quan", {}),
            "diem_nong": list(snapshot.get("diem_nong") or [])[:5],
            "no_ky_thuat_ung_vien": list(snapshot.get("no_ky_thuat_ung_vien") or [])[:4],
            "uu_tien_de_xuat": list(snapshot.get("uu_tien_de_xuat") or [])[:3],
            "rule": [
                "File sửa nhiều chỉ là hotspot, không phải bằng chứng code xấu.",
                "Test fail sau patch không tự chứng minh regression/causality.",
                "Nếu dữ liệu ít, nói Chưa đủ dữ liệu thay vì kết luận mạnh.",
                "Eris chỉ bàn luận/đề xuất; không tự sửa code, mở issue hay đổi roadmap.",
            ],
        }
        return "PROJECT_TRAJECTORY_V88:\n" + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))

    def _load_events(self) -> list[dict[str, Any]]:
        path = self.journal_path
        if not path or not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8") as handle:
                lines = list(deque(handle, maxlen=max(100, int(self.max_lines))))
        except Exception:
            return []
        events: list[dict[str, Any]] = []
        for line in lines:
            try:
                raw = json.loads(line)
                if isinstance(raw, Mapping):
                    # V90 ingress yêu cầu source rõ ràng, nhưng V79/V88 journal cũ
                    # chưa lưu field này. Migration chỉ áp dụng lúc đọc file local
                    # đã tồn tại; HTTP observation mới vẫn phải khai báo source.
                    if not str(raw.get("source") or "").strip() and str(raw.get("schema") or "") == "eris.coding.observation.v1":
                        raw = dict(raw)
                        raw["source"] = "eris-code/gitpilot"
                    events.append(sanitize_coding_observation(raw))
            except Exception:
                continue
        return events

    @staticmethod
    def _candidate_debts(*, hotspots: list[dict[str, Any]], no_verify: int, scope_expansion: int, repeated_failures: int, changed_tasks: int, reviewer_tasks: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        hot = next((x for x in hotspots if int(x.get("so_task_cham", 0) or 0) >= 3), None)
        if hot:
            rows.append({
                "id": "hotspot-regression-coverage",
                "muc": "Trung bình",
                "tieu_de": f"Hotspot {hot['file']} cần regression coverage tương xứng",
                "evidence": f"File xuất hiện trong {hot['so_task_cham']} task thay đổi gần đây.",
                "ghi_chu": "Đây là candidate debt, không phải kết luận rằng file đang lỗi.",
            })
        if no_verify >= 2:
            rows.append({
                "id": "verification-gap",
                "muc": "Cao",
                "tieu_de": "Verification có cấu trúc chưa đều",
                "evidence": f"Có {no_verify} task thay đổi file nhưng journal chưa thấy test_result/diagnostics có cấu trúc.",
                "ghi_chu": "Có thể tồn tại verification ngoài telemetry; cần kiểm tra trước khi gọi là debt chắc chắn.",
            })
        if scope_expansion >= 2:
            rows.append({
                "id": "scope-expansion",
                "muc": "Trung bình",
                "tieu_de": "Scope expansion lặp lại",
                "evidence": f"Có {scope_expansion} task chạm từ 8 file trở lên.",
                "ghi_chu": "Task lớn có thể hợp lệ; đây chỉ là tín hiệu cần review boundary.",
            })
        if repeated_failures >= 2:
            rows.append({
                "id": "repeated-failure-loop",
                "muc": "Trung bình",
                "tieu_de": "Có vòng retry/failure lặp lại",
                "evidence": f"Có {repeated_failures} task xuất hiện repeated tool/test failure finding.",
                "ghi_chu": "Nên xem lại hypothesis/feedback loop trước khi tăng số lần retry.",
            })
        if changed_tasks >= 3 and reviewer_tasks == 0:
            rows.append({
                "id": "review-coverage",
                "muc": "Trung bình",
                "tieu_de": "Review coverage chưa được quan sát",
                "evidence": f"Có {changed_tasks} task thay đổi file nhưng chưa quan sát thấy Reviewer trong cửa sổ này.",
                "ghi_chu": "Có thể review diễn ra ngoài telemetry; không kết luận thiếu review tuyệt đối.",
            })
        return rows[:5]

    @staticmethod
    def _priorities(*, hotspots: list[dict[str, Any]], no_verify: int, scope_expansion: int, repeated_failures: int, failed_tests: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        hot = next((x for x in hotspots if int(x.get("so_task_cham", 0) or 0) >= 3), None)
        if hot:
            rows.append({
                "id": "stabilize-hotspot",
                "uu_tien": "Cao",
                "thoi_han": "2 tuần",
                "tieu_de": f"Khóa regression quanh {hot['file']}",
                "ly_do": f"Hotspot này đã được sửa trong {hot['so_task_cham']} task; đáng ưu tiên coverage/evidence trước khi tiếp tục mở rộng.",
                "hanh_dong": "Xác định 1–2 invariant quan trọng và thêm focused regression test hoặc runtime check tương ứng.",
            })
        if no_verify >= 2:
            rows.append({
                "id": "verification-discipline",
                "uu_tien": "Cao",
                "thoi_han": "2 tuần",
                "tieu_de": "Chuẩn hóa verification sau thay đổi",
                "ly_do": f"Có {no_verify} task thay đổi file chưa có test_result/diagnostics có cấu trúc trong journal.",
                "hanh_dong": "Mỗi task đổi code cần ít nhất một evidence verification rõ: focused test, diagnostics hoặc runtime reproduction.",
            })
        if repeated_failures >= 2:
            rows.append({
                "id": "break-retry-loop",
                "uu_tien": "Trung bình",
                "thoi_han": "2–4 tuần",
                "tieu_de": "Giảm retry mù",
                "ly_do": f"Có {repeated_failures} task ghi nhận repeated failure finding.",
                "hanh_dong": "Sau hai failure cùng loại, quay lại hypothesis/root cause thay vì lặp cùng tool/command.",
            })
        elif scope_expansion >= 2:
            rows.append({
                "id": "scope-boundaries",
                "uu_tien": "Trung bình",
                "thoi_han": "2–4 tuần",
                "tieu_de": "Siết boundary của task rộng",
                "ly_do": f"Có {scope_expansion} task chạm từ 8 file trở lên.",
                "hanh_dong": "Trước Execute, ghi rõ invariant, file/layer dự kiến và điều kiện nào cho phép mở rộng scope.",
            })
        elif failed_tests >= 2:
            rows.append({
                "id": "test-failure-localization",
                "uu_tien": "Trung bình",
                "thoi_han": "2–4 tuần",
                "tieu_de": "Rút ngắn vòng localize test failure",
                "ly_do": f"Có {failed_tests} task từng có test fail trong cửa sổ 30 ngày.",
                "hanh_dong": "Nhóm test fail theo subsystem/root cause trước khi sửa thêm code.",
            })
        return rows[:3]

    @staticmethod
    def _user_project_fit(panel: Mapping[str, Any], project: Mapping[str, Any]) -> dict[str, Any]:
        groups = {str(x.get("id") or ""): x for x in panel.get("nhom_nang_luc") or () if isinstance(x, Mapping)}
        findings = dict(project.get("flow_findings") or {})
        needs: list[tuple[str, str, int]] = []
        verification_pressure = int(findings.get("no_structured_verification", 0) or 0) + int(findings.get("repeated_test_failure", 0) or 0)
        if verification_pressure:
            needs.append(("verification", "Kiểm chứng", verification_pressure))
        debugging_pressure = int(findings.get("patch_before_inspection", 0) or 0) + int(findings.get("repeated_same_failure", 0) or 0)
        if debugging_pressure:
            needs.append(("debugging", "Gỡ lỗi", debugging_pressure))
        architecture_pressure = int(findings.get("scope_expansion", 0) or 0)
        if architecture_pressure:
            needs.append(("architecture", "Kiến trúc", architecture_pressure))
        if not needs:
            return {"co_du_lieu": False, "ghi_chu": "Chưa đủ tín hiệu project để ghép với mô hình năng lực user."}
        needs.sort(key=lambda x: -x[2])
        gid, label, pressure = needs[0]
        group = groups.get(gid) or {}
        if not group.get("da_du_bang_chung"):
            return {
                "co_du_lieu": False,
                "nhu_cau_du_an": label,
                "ghi_chu": f"Project đang có {pressure} tín hiệu liên quan {label.lower()}, nhưng năng lực user ở nhóm này chưa đủ evidence để ghép.",
            }
        return {
            "co_du_lieu": True,
            "nhu_cau_du_an": label,
            "nang_luc_user": int(group.get("phan_tram") or 0),
            "so_bang_chung_user": int(group.get("so_bang_chung") or 0),
            "goi_y": f"Project đang tạo nhiều cơ hội thực hành {label.lower()}; nên để user tự lấy evidence đầu tiên trước khi giao implementation cho AI.",
        }

    @staticmethod
    def _empty(ts: float, window_days: int) -> dict[str, Any]:
        return {
            "schema": _SCHEMA,
            "tieu_de": "SỨC KHỎE DỰ ÁN · 30 NGÀY",
            "cap_nhat_luc": ts,
            "cua_so_ngay": int(window_days),
            "du_lieu_day_du": False,
            "ghi_chu": "Chưa đủ CodingObservation để thống kê project. Thiếu dữ liệu không đồng nghĩa project có vấn đề.",
            "workspace": "",
            "tong_quan": {"task_da_quan_sat": 0},
            "subsystem": [],
            "diem_nong": [],
            "no_ky_thuat_ung_vien": [],
            "uu_tien_de_xuat": [],
            "flow_findings": {},
        }


__all__ = ["ProjectTrajectoryService"]

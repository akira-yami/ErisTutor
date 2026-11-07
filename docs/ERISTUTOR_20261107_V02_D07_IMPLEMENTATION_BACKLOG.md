---
project: ErisTutor
doc_id: "D07"
title: "Backlog triển khai có thể giao việc"
version: "V02"
created_at: "2026-11-07T15:00:00+07:00"
snapshot_at: "2026-11-07T15:00:00+07:00"
format: markdown
---

# Backlog triển khai có thể giao việc

> **Mốc tài liệu:** 07/11/2026 · 15:00 (GMT+7) · Bản Markdown V02  
> **Trạng thái:** Phân tích source tại thời điểm trên; kế hoạch P0–P4 là đề xuất, chưa phải kết quả hoàn thành.  
> **Điều hướng:** [README](../README.md) · [Mục lục tài liệu](./ERISTUTOR_20261107_V02_D01_START_HERE.md)

Ưu tiên, file tác động, điều kiện hoàn thành và các bài test nên bổ sung.

| **Ngày lập / mốc đối chiếu** | 07/11/2026 • 15:00 (UTC+07:00)     |
|------------------------------|------------------------------------|
| **Phạm vi / loại tài liệu**  | Ảnh chụp source + kế hoạch đề xuất |

Nguồn phân tích: ErisTutor.zip → ErisTutor/eris-ai-20261107_114652_625.zip → eris/

**Quy ước bằng chứng:** CÓ CODE = đọc được mã nguồn; TEST OK = test offline chạy qua; CHƯA E2E = chưa xác nhận toàn luồng Windows/Gemini; ĐỀ XUẤT = chưa triển khai.

## 1. Danh mục tác vụ P0 / P1

| **ID / Mốc**      | **Thay đổi cụ thể**                                                                                                | **Acceptance / test**                                          |
|-------------------|--------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| LT-001 • 09/11 P0 | Tạo LearningIndexProjection: {active_path_id, recent_topics:\[{path_id,title}\], updated_at}; không master/review. | Snapshot panel đóng ngắn, không gọi learning_insights/mastery. |
| LT-002 • 10/11 P0 | Thêm learning_context_mode = closed_index \| opened_detail \| explicit_tutor; Root gọi detail theo gate.           | 3 trạng thái deterministic, explicit request vẫn học được.     |
| LT-003 • 11/11 P0 | Gắn version handshake outer Godot ⇄ nested backend; build/source hash.                                             | Báo mismatch, không lẫn UI mới/backend cũ.                     |
| LT-004 • 13/11 P1 | PackRegistry; chuyển group_id/path_id thành tham số đã validate, thay C# hard-code.                                | Chọn ≥3 pack hợp lệ; chặn IDs ngoài registry.                  |
| LT-005 • 15/11 P1 | API / graph lazy: nodes root/children/detail; cursor, revision, limits.                                            | Mở 1 nhánh không tải toàn graph; stale cursor báo conflict.    |
| LT-006 • 18/11 P1 | Lưu focus theo path + selected branch; UI back/forward/resume, prefetch bounded.                                   | Đổi môn và mở lại giữ đúng node từng môn; không nhân request.  |

## 2. Danh mục tác vụ P2 / P3 / P4

| **ID / Mốc**         | **Thay đổi cụ thể**                                                          | **Acceptance / test**                                             |
|----------------------|------------------------------------------------------------------------------|-------------------------------------------------------------------|
| LT-007 • 20/11 P2    | Unified Activity protocol: choice/code/short text; receipt/generation/nonce. | Submit retry/timeout không nhân điểm; revealed không independent. |
| LT-008 • 23/11 P2    | Attempts timeline, dispute/correction/forget và per-path summary.            | Reproject đúng; forget không hồi sinh sau replay.                 |
| LT-009 • 26/11 P2    | Review UI: due schedule, heuristic/FSRS owner, note không calibrated.        | Không bịa mức thành thạo; FSRS extra missing vẫn dùng được.       |
| LT-010 • 30/11 P3    | Gemini draft-child generator theo path + source refs + schema.               | DRAFT, provenance, CAS, cycle reject; no auto mastery.            |
| LT-011 • 04/12 P3    | Topic Observation (chủ đề quan tâm), separate from learning data.            | Có consent/scope; xóa được; hỏi nhiều không tăng mastery.         |
| LT-012 • 09–20/12 P4 | Offline benchmark + live Godot/HTTP/UDP/Gemini test harness.                 | Latency/token, graph size, evidence correctness, UI smoke.        |

## 3. Điểm sửa mã nguồn gợi ý theo tác vụ

| **Tác vụ** | **File / module ưu tiên**                                                                        |
|------------|--------------------------------------------------------------------------------------------------|
| LT-001/002 | src/runtime/tutor/learner_context.py; runtime/conversation/context_assembler.py; root tests      |
| LT-003     | src/body/UdpListener.cs + UdpListener.WebLearningCanvas.cs; runtime/incoming_protocol_runtime.py |
| LT-004/005 | src/runtime/learning_canvas/pack.py, service.py, snapshot.py; src/body/richchat/canvas.js        |
| LT-006     | src/runtime/learning_canvas/focus.py; src/body/richchat/canvas.js                                |
| LT-007/008 | src/runtime/learning_canvas/exercises.py; runtime/learning_core/{service,store,records}.py       |
| LT-009     | src/runtime/learning_core/retention.py; LearningService.review_snapshot(); portrait read model   |
| LT-010/011 | runtime/learner_portrait.py + new draft / observation module; policy/consent                     |
| LT-012     | src/tests/test\_\*.py, src/tools/learner_benchmark.py, Windows smoke CI                          |

## 4. Tiêu chí Done chung của mọi PR

- Có issue ID, diff giới hạn và lý do thay đổi; không đẩy dữ liệu nhạy cảm hoặc user DB vào git.

- Có migration/rollback hoặc giữ backward compatibility, không reset path/attempt/review đang có.

- Test deterministic cover success + stale revision + retry + duplicate + unauthorized scope.

- Có ghi trạng thái “source only / tested / Windows E2E passed” trong changelog theo ngày thực tế.

- Kiểm tra Root không spam gọi Gemini, không đọc toàn dữ liệu khi panel không mở.

## 5. Lệnh kiểm chứng nền

Tại thư mục eris/: PYTHONPATH=src python -m pytest -q src/tests/test_learning_canvas.py src/tests/test_learning_canvas_exercises.py src/tests/test_learner_portrait_web.py src/tests/test_tutor_p2_adaptive_insights.py src/tests/test_tutor_learning_p1_domains.py

Bổ sung nhóm test Root/Tutor: test_root_orchestrated_learner_v1.py, test_long_term_learner_v2.py, test_tutor_executive_v1.py, test_learning_kernel_boundary_v4.py, test_web_learning_canvas_migration.py.

---

[← Quay về README](../README.md) · *Đối chiếu source ngày 07/11/2026; không suy đoán trạng thái đã triển khai sau mốc này.*

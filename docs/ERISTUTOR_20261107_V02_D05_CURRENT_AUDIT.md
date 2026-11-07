---
project: ErisTutor
doc_id: "D05"
title: "Kiểm kê tiến độ và khoảng trống"
version: "V02"
created_at: "2026-11-07T15:00:00+07:00"
snapshot_at: "2026-11-07T15:00:00+07:00"
format: markdown
---

# Kiểm kê tiến độ và khoảng trống

> **Mốc tài liệu:** 07/11/2026 · 15:00 (GMT+7) · Bản Markdown V02  
> **Trạng thái:** Phân tích source tại thời điểm trên; kế hoạch P0–P4 là đề xuất, chưa phải kết quả hoàn thành.  
> **Điều hướng:** [README](../README.md) · [Mục lục tài liệu](./ERISTUTOR_20261107_V02_D01_START_HERE.md)

Phát hiện trực tiếp từ mã nguồn, đối chiếu kiểm thử offline ngày 07/11/2026.

| **Ngày lập / mốc đối chiếu** | 07/11/2026 • 15:00 (UTC+07:00)     |
|------------------------------|------------------------------------|
| **Phạm vi / loại tài liệu**  | Ảnh chụp source + kế hoạch đề xuất |

Nguồn phân tích: ErisTutor.zip → ErisTutor/eris-ai-20261107_114652_625.zip → eris/

**Quy ước bằng chứng:** CÓ CODE = đọc được mã nguồn; TEST OK = test offline chạy qua; CHƯA E2E = chưa xác nhận toàn luồng Windows/Gemini; ĐỀ XUẤT = chưa triển khai.

## 1. Kết quả kiểm thử tại mốc phân tích

| **Nhóm** | **Thao tác kiểm tra**                                                                                                                   | **Kết quả**                   |
|----------|-----------------------------------------------------------------------------------------------------------------------------------------|-------------------------------|
| Batch A  | test_learning_canvas, exercises, learner_portrait_web, tutor_p2_adaptive_insights, tutor_learning_p1_domains                            | 88 PASS / 4.37 s              |
| Batch B  | test_root_orchestrated_learner_v1, long_term_learner_v2, tutor_executive_v1, learning_kernel_boundary_v4, web_learning_canvas_migration | 35 PASS / 2.10 s              |
| Tổng     | 10 tệp test, chạy pytest với PYTHONPATH=src trên mã nguồn Eris AI lồng trong ZIP                                                        | 123 PASS (chỉ offline Python) |

Các phép thử trên KHÔNG bao gồm build Godot .NET, launch Eris Windows, kết nối Gemini Web thật, I/O HTTP/UDP E2E thực hoặc xác nhận data người học đang hoạt động.

## 2. Ma trận trạng thái

| **Hạng mục**                          | **Tình trạng**             | **Bằng chứng / hạn chế**                                                    |
|---------------------------------------|----------------------------|-----------------------------------------------------------------------------|
| Learning Core / SQLite / mastery      | CÓ CODE + TEST OK          | service/store/mastery; rollback và correction được kiểm ở unit.             |
| TutorExecutive & Root bridge          | CÓ CODE + TEST OK          | tutor/executive; root integration tests.                                    |
| Learner Portrait (web)                | CÓ CODE + TEST OK          | learner_portrait và web tests; Gemini thật chưa xác nhận.                   |
| Canvas graph Java DI                  | CÓ CODE + TEST OK          | java-di.json; Canvas exercises; web migration tests.                        |
| Learning Spaces đa môn ở backend      | CÓ CODE + TEST OK          | learning_spaces/switch_learning_space; UI Canvas chưa đa môn tổng quát.     |
| Root tiết kiệm context khi panel đóng | CHƯA ĐẠT MỤC TIÊU          | ContextAssembler gọi learner_continuity_snapshot(max_kps=6) theo user turn. |
| Canvas động mở nhánh theo môn         | CHƯA CÓ BẰNG CHỨNG         | C# bridge hard-code java-di; pack giới hạn 16 node.                         |
| FSRS thật                             | TÙY CHỌN / CHƯA E2E        | dependency fsrs==6.3.2; mặc định shadow; không có benchmark dữ liệu thật.   |
| Docling / nguồn PDF OCR               | ĐỀ XUẤT + PHỤ THUỘC        | adapter tùy chọn, chưa chứng minh luồng thực.                               |
| Không tạo mastery ảo                  | CÓ CODE + TEST OK Ở ĐƠN VỊ | assistance/result và projection; vẫn cần E2E click → DB.                    |
| Build outer Godot vs nested Eris AI   | CẦN ĐỐI CHIẾU              | UdpListener.cs khác nhau; không chứng minh bản đang chạy.                   |

## 3. Rủi ro xếp thứ tự

| **Mức**  | **Rủi ro**                                               | **Ứng phó**                                                   |
|----------|----------------------------------------------------------|---------------------------------------------------------------|
| P0 / Cao | Runtime đang chạy có thể chưa đồng bộ với nested source. | Chốt single source of truth; build stamp / version handshake. |
| P0 / Cao | Root bơm 6 knowledge points theo turn dù Learning đóng.  | Mode index-only; test số bytes/không gọi mastery khi đóng.    |
| P1 / Cao | Canvas hard-code java-di và giới hạn 16 node.            | Pack registry + path selector + pagination + lazy children.   |
| P1 / Vừa | Async Gemini stale/đứt kết nối và độ trễ.                | Generation guard, timeout/fallback, telemetry riêng.          |
| P2 / Vừa | Evidence lặp/chấm bài nhầm sau hint hoặc chuyển path.    | Idempotency, assistance receipts, replay E2E.                 |
| P3 / Vừa | Graph tự sinh có skill trùng/chu trình/nguồn yếu.        | Validator + CAS + preview diff + approval policy.             |

## 4. Điều chưa được phép kết luận

- Không có dữ liệu SQLite của người học hiện tại nên không thể nói môn nào đã thành thạo hoặc lịch ôn nào đã tới.

- Không có proof Gemini Web đang login hoặc có thể trả lời trong phiên desktop; narrator là code có fallback.

- Không chứng minh được tích hợp Live2D/Godot runtime chỉ từ Python unit test.

- Các tài liệu cũ về P0/P1/P2 là lịch sử triển khai; trạng thái thực tại được xét qua source hiện có và test trong phiên này.

### Dấu vết mã nguồn / căn cứ

- src/runtime/conversation/context_assembler.py: learner_continuity

- src/runtime/tutor/learner_context.py

- src/body/UdpListener.WebLearningCanvas.cs: group_id java-di

- src/runtime/learning_canvas/pack.py: node ≤ 16

- src/tests/\*learning\*, src/tests/\*tutor\*, src/tests/test_learner_portrait_web.py

---

[← Quay về README](../README.md) · *Đối chiếu source ngày 07/11/2026; không suy đoán trạng thái đã triển khai sau mốc này.*

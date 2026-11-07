---
project: ErisTutor
doc_id: "D01"
title: "Tổng quan và hướng dẫn đọc"
version: "V02"
created_at: "2026-11-07T15:00:00+07:00"
snapshot_at: "2026-11-07T15:00:00+07:00"
format: markdown
---

# Tổng quan và hướng dẫn đọc

> **Mốc tài liệu:** 07/11/2026 · 15:00 (GMT+7) · Bản Markdown V02  
> **Trạng thái:** Phân tích source tại thời điểm trên; kế hoạch P0–P4 là đề xuất, chưa phải kết quả hoàn thành.  
> **Điều hướng:** [README](../README.md) · [Mục lục tài liệu](./ERISTUTOR_20261107_V02_D01_START_HERE.md)

Tổng quan, phạm vi tài liệu, các phát hiện ưu tiên và đường đọc nhanh.

| **Ngày lập / mốc đối chiếu** | 07/11/2026 • 15:00 (UTC+07:00)     |
|------------------------------|------------------------------------|
| **Phạm vi / loại tài liệu**  | Ảnh chụp source + kế hoạch đề xuất |

Nguồn phân tích: ErisTutor.zip → ErisTutor/eris-ai-20261107_114652_625.zip → eris/

**Quy ước bằng chứng:** CÓ CODE = đọc được mã nguồn; TEST OK = test offline chạy qua; CHƯA E2E = chưa xác nhận toàn luồng Windows/Gemini; ĐỀ XUẤT = chưa triển khai.

## 1. Ảnh chụp hiện tại

Tại mốc 07/11/2026, Eris Tutor là hệ thống phụ trợ học tập nằm trong Eris AI, không phải một chatbot dạy học độc lập. ZIP ngoài chứa Godot/UDP và một ZIP Eris AI lồng bên trong. Hai bộ nguồn có thể liên quan nhưng chưa có bằng chứng build/sync giữa chúng.

| **Nhóm**            | **Kết luận tại thời điểm kiểm tra**                                                                                    |
|---------------------|------------------------------------------------------------------------------------------------------------------------|
| Hạt nhân học tập    | LearningService + LearningStore (SQLite), evidence/mastery/review; CÓ CODE, TEST OK.                                   |
| Tutor hội thoại     | TutorExecutive / TutorCapability phối hợp với Root, không chiếm quyền phát ngôn; CÓ CODE.                              |
| Canvas kiến thức    | Web Canvas localhost; gói Java Dependency Injection có 5 KnowledgePoint, 6 bài luyện; CÓ CODE, TEST OK.                |
| Chân dung người học | Learner Portrait read-only, Gemini diễn giải bất đồng bộ nếu có adapter; CÓ CODE, chưa E2E Gemini.                     |
| Đa môn              | Learning Spaces/path switching trong kernel; riêng Web Canvas còn khóa java-di; một phần có sẵn, một phần cần mở rộng. |
| Kiểm thử            | 123 test offline liên quan Learning/Tutor đạt trong 2 lượt chạy, không thay thế Windows/Godot E2E.                     |

## 2. Bản đồ đọc tài liệu

| **Mã** | **Tài liệu**           | **Dùng khi**                                       |
|--------|------------------------|----------------------------------------------------|
| D01    | START_HERE             | Nắm toàn bộ bộ tài liệu                            |
| D02    | SYSTEM_KNOWLEDGE_ATLAS | Hiểu kiến trúc và ranh giới quyền sở hữu           |
| D03    | CURRICULUM_GRAPH       | Xem đồ thị kiến thức thật và ontology mở rộng      |
| D04    | DATA_AND_IO            | Hiểu dữ liệu, I/O và tính đúng đắn                 |
| D05    | CURRENT_AUDIT          | Phân loại đã có / đang thiếu / chưa kiểm chứng     |
| D06    | ROADMAP                | Xem lịch triển khai từng giai đoạn                 |
| D07    | IMPLEMENTATION_BACKLOG | Nhận việc, file tác động và điều kiện nghiệm thu   |
| D08    | GOVERNANCE             | Cập nhật phiên bản, đặt tên và quyết định kỹ thuật |

## 3. Ưu tiên ngắn hạn

- P0: tách snapshot học tập Root thành chỉ mục tên môn cực ngắn khi Learning đóng; chỉ tải chi tiết khi mở panel hoặc user yêu cầu học.

- P1: bỏ group_id java-di hard-code ở Web Canvas; thêm route chọn path/pack, nạp đồ thị đúng môn.

- P2: kiểm chứng bài làm → attempt/evidence → mastery/review theo DB thật; đo không spam Gemini/Root.

- P3–P4: dynamic branch/generative content có xác nhận và benchmark; sau đó mới nghiệm thu desktop Windows đầy đủ.

## 4. Phạm vi và giới hạn

Các tài liệu này mô tả mã nguồn được giao, không thể kết luận người học đã học/đạt kiến thức nào vì ZIP không cung cấp phiên SQLite người học đang chạy. Mốc roadmap là lịch đề xuất kể từ 07/11/2026, không phải lời cam kết đã hoàn thành.

---

[← Quay về README](../README.md) · *Đối chiếu source ngày 07/11/2026; không suy đoán trạng thái đã triển khai sau mốc này.*

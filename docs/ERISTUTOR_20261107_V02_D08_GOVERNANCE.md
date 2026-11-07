---
project: ErisTutor
doc_id: "D08"
title: "Quy ước cập nhật & nhật ký quyết định"
version: "V02"
created_at: "2026-11-07T15:00:00+07:00"
snapshot_at: "2026-11-07T15:00:00+07:00"
format: markdown
---

# Quy ước cập nhật & nhật ký quyết định

> **Mốc tài liệu:** 07/11/2026 · 15:00 (GMT+7) · Bản Markdown V02  
> **Trạng thái:** Phân tích source tại thời điểm trên; kế hoạch P0–P4 là đề xuất, chưa phải kết quả hoàn thành.  
> **Điều hướng:** [README](../README.md) · [Mục lục tài liệu](./ERISTUTOR_20261107_V02_D01_START_HERE.md)

Giữ bản đồ tri thức và kế hoạch sống cùng repository, không biến tài liệu thành trạng thái giả.

| **Ngày lập / mốc đối chiếu** | 07/11/2026 • 15:00 (UTC+07:00)     |
|------------------------------|------------------------------------|
| **Phạm vi / loại tài liệu**  | Ảnh chụp source + kế hoạch đề xuất |

Nguồn phân tích: ErisTutor.zip → ErisTutor/eris-ai-20261107_114652_625.zip → eris/

**Quy ước bằng chứng:** CÓ CODE = đọc được mã nguồn; TEST OK = test offline chạy qua; CHƯA E2E = chưa xác nhận toàn luồng Windows/Gemini; ĐỀ XUẤT = chưa triển khai.

## 1. Quy ước đặt tên bắt buộc

Mọi tệp tài liệu, sơ đồ và dữ liệu trong gói bắt đầu bằng `ERISTUTOR_20261107_V02_` và có mã nội dung: D01–D08 (.md), A01–A03 (ảnh/dot), K01 (JSON), README.md (hướng dẫn). Khi kiểm tra lại ngày khác, đổi YYYYMMDD và tăng VXX.

| **Thuộc tính**          | **Ví dụ**                                                |
|-------------------------|----------------------------------------------------------|
| Tài liệu Markdown           | ERISTUTOR_20261107_V02_D03_CURRICULUM_GRAPH.md         |
| Sơ đồ                   | ERISTUTOR_20261107_V02_A02_CURRICULUM.png / .dot         |
| JSON knowledge map      | ERISTUTOR_20261107_V02_K01_KNOWLEDGE_MAP.json            |
| Index                   | README.md                         |
| Mốc bắt buộc trong file | Ngày lập / mốc đối chiếu: 07/11/2026 • 15:00 (UTC+07:00) |

## 2. Định nghĩa trạng thái

| **Nhãn**         | **Ý nghĩa**                                                                                 |
|------------------|---------------------------------------------------------------------------------------------|
| CÓ CODE          | Tìm thấy thực thi trong snapshot source; không đồng nghĩa đã triển khai lên máy người dùng. |
| TEST OK          | Một tập test nhất định đã chạy qua; luôn ghi test names và thời điểm.                       |
| CHƯA E2E         | Chưa kiểm chứng toàn luồng desktop/real Gemini/real user DB.                                |
| ĐỀ XUẤT          | Chưa thực thi ở snapshot, có mốc thời gian và acceptance dự kiến.                           |
| KHÔNG ĐỦ DỮ LIỆU | Không được suy diễn từ tài liệu hay câu hỏi của user.                                       |

## 3. Quyết định kiến trúc đang giữ

| **Mã** | **Quyết định**                                         | **Lý do**                                             |
|--------|--------------------------------------------------------|-------------------------------------------------------|
| ADR-01 | Một LearningService/SQLite làm nguồn sự thật học tập.  | Giữ evidence/mastery nhất quán, không conflict store. |
| ADR-02 | Root quyết định khi nói/học; Tutor cung cấp phương án. | Không hai agent loop tranh lời thoại và tool.         |
| ADR-03 | UI/Gemini là read-model hoặc proposed content.         | Không gắn mastery chỉ vì model nhận xét tự tin.       |
| ADR-04 | Index-only khi Learning đóng là mục tiêu P0; chưa đạt. | Đúng định hướng lazy/on-demand và giảm context cost.  |
| ADR-05 | Mọi curriculum revision dùng identity/validator/CAS.   | Tránh lỗi đồ thị cyclic, stale patch, ghi đè kỹ năng. |
| ADR-06 | Phiên bản build outer/nested cần xác định.             | ZIP hai source snapshots chưa chứng minh đồng bộ.     |

## 4. Quy trình cập nhật tài liệu sau mỗi checkpoint

1.  Lấy commit/source hash của bản thực dùng để build, ghi timestamp + branch.

2.  Chạy lại test liên quan, thu số passed/failed/skipped và phạm vi không chạy.

3.  Đối chiếu graph pack/DB schema/Root context với D02–D05, sửa bằng chứng theo đúng trạng thái.

4.  Cập nhật D06/D07 bằng mốc hoàn thành THỰC, không sửa kế hoạch thành “đã xong” khi chưa E2E.

5.  Tăng YYYYMMDD/VXX, lưu changelog và ZIP mới; không ghi source secrets hoặc dữ liệu người học cá nhân.

## 5. Nguồn gốc snapshot và log kiểm chứng

Outer branch từ .git/HEAD: feat/model; outer commit: 5092b59a0774a74a554e9bd1f249da3dbb849c21. Đây là metadata ZIP được cung cấp, không đảm bảo nested Eris AI ứng với đúng commit này.

SHA-256 của ErisTutor.zip: 208978b468767b0fe716c2e7dbba71bc6375fd69d85dbdb799860ef4079c9287.

Ngày lập 07/11/2026; test offline Python: batch A 88 pass và batch B 35 pass. Chưa thực hiện build Godot .NET/Gemini Web live.

---

[← Quay về README](../README.md) · *Đối chiếu source ngày 07/11/2026; không suy đoán trạng thái đã triển khai sau mốc này.*

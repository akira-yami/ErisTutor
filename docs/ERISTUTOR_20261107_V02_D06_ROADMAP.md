---
project: ErisTutor
doc_id: "D06"
title: "Lộ trình phát triển tiếp theo"
version: "V02"
created_at: "2026-11-07T15:00:00+07:00"
snapshot_at: "2026-11-07T15:00:00+07:00"
format: markdown
---

# Lộ trình phát triển tiếp theo

> **Mốc tài liệu:** 07/11/2026 · 15:00 (GMT+7) · Bản Markdown V02  
> **Trạng thái:** Phân tích source tại thời điểm trên; kế hoạch P0–P4 là đề xuất, chưa phải kết quả hoàn thành.  
> **Điều hướng:** [README](../README.md) · [Mục lục tài liệu](./ERISTUTOR_20261107_V02_D01_START_HERE.md)

Khung thời gian đề xuất từ 09/11 đến 20/12/2026. Chưa triển khai các hạng mục ghi “ĐỀ XUẤT”.

| **Ngày lập / mốc đối chiếu** | 07/11/2026 • 15:00 (UTC+07:00)     |
|------------------------------|------------------------------------|
| **Phạm vi / loại tài liệu**  | Ảnh chụp source + kế hoạch đề xuất |

Nguồn phân tích: ErisTutor.zip → ErisTutor/eris-ai-20261107_114652_625.zip → eris/

**Quy ước bằng chứng:** CÓ CODE = đọc được mã nguồn; TEST OK = test offline chạy qua; CHƯA E2E = chưa xác nhận toàn luồng Windows/Gemini; ĐỀ XUẤT = chưa triển khai.

## 1. Mục tiêu cuối kỳ

Eris Learning vận hành như một không gian học chủ động theo nhu cầu: Root chỉ nắm index tên môn rất ngắn khi panel đóng; khi mở panel tải đúng path/nhánh, hỗ trợ mở tiếp vài nhánh, học và làm bài, lưu lịch sử; Gemini tạo/diễn giải khi cần nhưng không giữ quyền chấm hay ghi dữ kiện học tập.

## 2. Timeline có mốc thời gian

| **Giai đoạn & hạn dự kiến** | **Mục tiêu / sản phẩm bàn giao**                                                             | **Cửa nghiệm thu**                                                     |
|-----------------------------|----------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| P0 • 09–12/11/2026          | Chuẩn hóa source/build stamp; Root index-only on closed; test không query mastery khi đóng.  | No-Gemini/No-KP injection ở small talk; đối chiếu 2 bản source.        |
| P1 • 13–19/11/2026          | Registry đa pack/path; web chọn môn; graph lazy mở từng nhánh; state resume.                 | Chuyển 3 môn không mất tiến độ; branch load có limits.                 |
| P2 • 20–28/11/2026          | Công cụ luyện tập tổng quát, lịch sử attempts, correction/forget, review dashboard.          | 100% event path có receipt; test retry/stale/hint/switch.              |
| P3 • 30/11–08/12/2026       | Gemini draft branch + reference retrieval; topic observation tách mastery; portrait refresh. | Phân biệt draft/live, provenance, reject cycle, không ghi sai mastery. |
| P4 • 09–20/12/2026          | Benchmark dạy học, đo latency/token, Windows Godot HTTP/UDP/Gemini smoke & release.          | Dashboard pass/fail, benchmark không regression, build ổn định.        |

## 3. Quan hệ phụ thuộc

1.  P0 thiết lập source-of-truth và lazy index: cần xong trước mọi tối ưu số môn để không tạo nợ context.

2.  P1 tạo UX đa môn và API load nhánh; dùng lại validation/curriculum revision trong Learning Core.

3.  P2 chốt pipeline bằng chứng, bài làm và ôn tập trên nhiều môn trước khi model tự gợi ý kiến thức mới.

4.  P3 mở sinh nội dung dưới quyền reviewer/validator, không sinh graph rồi ghi trực tiếp.

5.  P4 chỉ nghiệm thu release khi kiểm thử desktop thật không trái contract của P0–P3.

## 4. Những việc chủ động hoãn

- Không mở thêm agent loop riêng cho Tutor; giữ một Root làm chủ lời nói.

- Không mặc định bật FSRS active hoặc học máy mastery/Knowledge Tracing trước khi có lịch sử đủ tin cậy và baseline.

- Không thêm MathLive/Pyodide/Sandpack/JSXGraph chỉ để có nhiều widget; chỉ chọn theo bài học cần minh họa.

- Không xây database/server thứ hai cho Learning; giữ LearningService là owner và read-through API khi cần.

## 5. Checkpoint & lịch rà soát

| **Ngày dự kiến** | **Checkpoint**  | **Kết quả cần ghi vào changelog**                                |
|------------------|-----------------|------------------------------------------------------------------|
| 12/11/2026       | P0 review       | Context budget, version handshake, regression log.               |
| 19/11/2026       | P1 demo         | 3 môn, lazy graph, restore focus, không mất attempt.             |
| 28/11/2026       | P2 review       | Attempt receipts, correction, lịch ôn, data integrity.           |
| 08/12/2026       | P3 review       | Nguồn/cycle/CAS, Gemini fallback, topic scope.                   |
| 20/12/2026       | P4 release gate | Windows E2E + benchmark + non-regression + quyết định phát hành. |

---

[← Quay về README](../README.md) · *Đối chiếu source ngày 07/11/2026; không suy đoán trạng thái đã triển khai sau mốc này.*

# P2 — Adaptive Learning Intelligence (01/11/2026)

## Phạm vi bản vá

Bản P2 này được tạo trên source **Eris 01/11/2026 + P0 + P1**. Không tạo
LearningService/SQLite mới, không tạo agent loop khác và không tự phát thoại.

### P2-A: FSRS và ôn tập

- FSRS vẫn **mặc định shadow**; không tự chiếm quyền lịch ôn.
- Người dùng yêu cầu rõ chuyển sang `fsrs`, `heuristic` hoặc `shadow` thì Root
  có thể gọi Tutor `operation=set_retention_mode`, `mode=...`.
- Trước khi bật FSRS, kiểm tra đúng **fsrs==6.3.2**. Nếu thư viện chưa sẵn sàng,
  thao tác bị từ chối, không đổi lịch và không ghi cấu hình sai.
- Chế độ đã chọn được lưu qua khởi động lại. Nếu yêu cầu FSRS nhưng sau đó
  thiếu thư viện, hệ thống dùng shadow an toàn và báo `degraded=true`.
- API `learning_insights()` nêu kiến thức đến hạn ôn theo scheduler đang dùng;
  không tự bắt người dùng học, không tạo timer/reminder không được yêu cầu.

### P2-B: Evidence-based Adaptive Teaching

- Sử dụng lại `TutorExecutive` và `adaptive_policy.py` hiện có (đã hỗ trợ
  chọn cách dạy từ bằng chứng, không dựa vào mỗi câu “em hiểu rồi”).
- Thêm `insights.py`: tách bài độc lập và bài có gợi ý, gom cùng họ bài,
  đánh dấu `needs_independent_probe`, `possible_repeated_gap`, `review_due`,
  `needs_repair` và `unassessed` như **tín hiệu**, không phải chẩn đoán.
- Sau khi correction/forget, báo cáo tính lại từ SQLite hiện hành;
  không lưu thêm một bản sao kết quả có thể lỗi thời.
- Mở rộng chương trình: `preview_expansion` trả diff (thêm tối đa 8 skill/lần),
  skill cũ giữ nguyên ID, cần nguồn cùng domain, chống nguồn lỗi thời;
  `apply_expansion` cần base_revision chính xác và xác nhận người dùng.
- Đây là **mở rộng lộ trình có kiểm soát**, chưa phải AI tự nghiên cứu mọi môn,
  tự sinh mọi tài liệu hoặc tự thay curriculum không xin phép.

### P2-C: Learning Insights / Teaching Evaluation

- Tutor `operation=insights` trả JSON gồm trạng thái từng skill, số bài độc
  lập đúng/sai, số họ bài có hỗ trợ, đề xuất kiểm tra lại/ôn, lịch ôn và thống
  kê phương pháp dạy trong đúng path.
- Tutor `operation=dashboard`: khi người dùng yêu cầu **bảng tiến độ**, tạo
  file HTML offline ở `<ERIS_HOME>/mentor/reports/learning-insights-*.html`.
  Mở bằng trình duyệt bất kỳ, không cần npm, web server, mạng hoặc WebView.
  Báo cáo chỉ là snapshot; muốn cập nhật phải xuất lại.
- Teaching evaluation là thống kê quan sát, cần ít nhất 5 họ bài độc lập
  để hiển thị tỷ lệ từng nhóm. **Không tự đổi teaching policy** hoặc khẳng
  định quan hệ nhân quả từ dữ liệu ít ỏi.
- Mọi text hiển thị trong HTML đã escape, không nhúng script hay tài nguyên ngoài.

## Điều kiện cài đặt

1. Sao lưu source và dữ liệu `<ERIS_HOME>/mentor/learning.sqlite3`.
2. Áp dụng bản **P0**, rồi **P1**, rồi mới đến bản **P2** này.
3. Chép thư mục `eris/` trong ZIP vào gốc dự án hiện tại, ghi đè các file cùng tên.
4. Khởi động lại ứng dụng và kiểm tra bằng `operation=insights` khi Tutor có path.
5. Muốn kiểm tra FSRS active, dùng môi trường portable có `fsrs==6.3.2` và thử
   một lần recall độc lập; trong môi trường build hiện tại **chưa chạy được FSRS thật**.

## Bộ test

```text
PYTHONPATH=src python -m pytest -q src/tests/test_tutor_p2_adaptive_insights.py
PYTHONPATH=src python -m pytest -q src/tests/test_tutor_p0_regressions.py src/tests/test_tutor_learning_p1_domains.py
node --test src/tests/ui/learning_artifacts.test.cjs src/tests/ui/learning_artifacts_p1.test.cjs
```

## Chưa xác minh / không nằm trong P2

- Chưa test model/voice/Godot/portable Windows thật.
- Chưa chạy các test FSRS cần thư viện thật vì môi trường không tải được gói từ PyPI.
- P3 mới là Web Learning Universe tương tác + graph đa môn + công cụ toán/voice/code sâu.
- Không có tự điều chỉnh trọng số LLM, không có optimizer nhân quả, không tạo
  bài học theo nguồn ngoài nếu người dùng chưa yêu cầu.

# Eris Learning — P3.8 kiểm tra tích hợp + P4 Dynamic Workspace (giai đoạn 1)

## Phạm vi

Áp dụng sau **P3.7 Lazy Learning Activation**. Giữ nguyên nguyên tắc:

- Khi Learning đóng: Root chỉ có `LearningBrief` (tên môn và nhánh ngắn); **không** gọi Gemini để tạo bài hay chuẩn bị nhánh.
- Khi mở Learning: chỉ tải graph đang chọn và **bài học của đúng nút đó**; không đọc cả kho bài học.
- Khi chuyển môn/nhánh: bài học được tra theo `group_id + node_id`; một phản hồi cũ được cache cho đúng key, **không** đẩy giao diện trở lại nhánh cũ.
- Khi mở lại: đọc bài học từ SQLite metadata đã lưu, không gọi Gemini lại nếu có cache.
- Việc xem nội dung hoặc nhấn “Xem lời giải” **không** tăng điểm mastery.

## P4 triển khai trong bản này

1. `runtime/learning_canvas/lesson.py`: hai lớp `LearningLessonStore` và `OnDemandLessonWorker`.
   - Lưu mỗi bài học theo một key băm riêng trong SQLite meta.
   - Có kiểm tra whitelist schema, giới hạn 6 khối / 10 KB mỗi bài, loại input không hợp lệ.
   - Dùng chung `GeminiWebUserProfileAdapter` hiện có: prompt ngắn, tối đa 1.850 token yêu cầu, timeout 18 giây (giới hạn token cuối cùng còn phụ thuộc gateway Gemini Web).
   - Một worker duy nhất để coalesce tác vụ trong P4. Worker bắt đầu khi tab có lease, không khởi động tác vụ mới khi panel đóng. Kết quả đang chạy có thể lưu cache sau khi đóng nhưng không gửi UI.
   - Gặp lỗi thì tạm ngừng thử lại 75 giây; có nút “Thử tạo lại”.
2. `runtime/learning_canvas/service.py`: khi mở hoặc chọn một nhánh, tra cache bài học và xếp việc tạo on-demand; gửi trường `dynamic_lesson` trong snapshot **chỉ cho nút đang chọn**; bổ sung action `lesson_request` kiểm tra panel active + focus. Không cấp quyền chấm điểm.
3. `body/UdpListener.WebLearningCanvas.cs`: whitelist action mới qua cầu nối Godot → Python; không mở API arbitrary.
4. `body/richchat/canvas.js/.css/.html`: UI dựng từ các component cố định, không chạy HTML/JS do AI tạo:
   - `text`: giải thích lý thuyết.
   - `steps`: hướng dẫn theo bước.
   - `formula`: LaTeX được hiển thị với MathJax **đã có trong portal**, chỉ tải khi thật sự xuất hiện công thức.
   - `code`: Prism tô màu cho nhiều ngôn ngữ; mã chỉ để đọc, không tự chạy.
   - `check`: câu hỏi tự kiểm tra, nút hé đáp án; không tự chấm mastery.

Không sinh HTML/CSS/JS mỗi lần. Gemini chỉ sinh JSON ngắn phù hợp một chủ đề.

## Luồng I/O

```text
Root khi chat bình thường
  └── LearningBrief (ngắn)   ← continuity.brief() / cache

Người dùng mở Web Learning → session lease
  └── chọn một nhánh → GraphWorkspace.inspect() [topic, trail]
       ├── LearningLessonStore.get(group_id, node_id)
       │     ├── có cache → dynamic_lesson.ready → web render
       │     └── thiếu → OnDemandLessonWorker [chỉ khi panel active]
       │           └── Gemini Web adapter → JSON block ngắn
       │                └── validate → SQLite cache → state_packet (không navigate)
       └── Graph prefetch P3.5 (một tầng ẩn, ưu tiên thấp)

Người dùng đóng Learning → hết session lease → không xếp việc mới
```

## Hướng dẫn cài

- **ZIP incremental:** chỉ áp dụng khi project đã cài P3.7. Giải nén **đúng thư mục cha chứa `eris/`**, ghi đè theo cấu trúc thư mục.
- **ZIP cumulative:** áp dụng trực tiếp lên bản `eris-ai-20261106_102417_503(1).zip` đã gửi; chứa P3.7 + P3.8/P4, không cần cài các patch cũ một lượt nữa.
- Sao lưu source và dữ liệu SQLite trước khi ghi đè. Khởi động lại Python backend và Godot portal để nạp C# bridge + JS mới.

Cách thử thủ công trên Windows:

1. Mở Eris, chưa mở Learning; kiểm tra log **không có** request `learning-lesson`/Gemini cho bài học. Root vẫn chỉ nhận LearningBrief.
2. Mở Learning, tạo môn mới hoặc chọn môn đã lưu, chọn nhánh; vùng **Bài học theo nhánh** hiện trạng thái chuẩn bị rồi chuyển sang nội dung. Nếu Gemini Web lỗi, có nút thử lại.
3. Chọn môn khác rồi quay lại; bài cũ lấy từ cache, không yêu cầu sinh lại.
4. Với nội dung toán, xác nhận công thức hiển thị đẹp; nội dung code có syntax highlight; câu hỏi kiểm tra có thể hé/ẩn lời giải.
5. Đóng Learning khi Gemini còn chạy, tiếp tục nói chuyện Eris; không được tự mở panel hoặc chen lời. Mở lại có thể đọc cache nếu công việc đã hoàn tất.

## Kiểm tra đã chạy / còn thiếu

- Python/JS syntax: qua.
- 137 test có liên quan Learning/Conversation/Canvas: qua, bao gồm 13 test P4 mới, SQLite persistence, mock Gemini, đổi nhánh giữa request, đóng panel, chống nội dung nguy hiểm.
- `tests/conversation/test_learning_canvas_full_loop.py` được điều chỉnh assertion cũ: Root **không** được tự bật Learning (đúng quy tắc P3.7).
- **Chưa xác nhận** `dotnet build`/Godot chạy trên Windows (môi trường hiện tại không có `dotnet`).
- **Chưa xác nhận** Gemini Web live với cookie/tài khoản người dùng; dùng fake Gemini trong test.
- Đã thử Chromium smoke test nhưng môi trường chặn cả URL `file://` bởi chính sách máy chủ; **chưa có ảnh xác minh UI thật**.

## Chưa bao gồm

P4 này là **bộ dựng bài học component-driven ban đầu**, chưa có mô phỏng tương tác tùy lĩnh vực, vẽ đồ thị hàm số, sandbox chạy code do người học nhập, hay bộ kiểm chứng nguồn độc lập. Những phần đó phù hợp P4.1/P4.2 hoặc bước sau khi test thực tế.

## Rollback

Dùng bản backup source và SQLite trước khi áp dụng. Các bài học P4 lưu dưới key `learning_canvas_lesson_p4_*`, không thay đổi schema và mastery của P3.7. Các bản cũ có thể bỏ qua key này.

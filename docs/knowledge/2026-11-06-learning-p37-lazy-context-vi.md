# Eris Learning P3.7 — Lazy Activation & Compact Context

> Bản vá áp dụng cho `eris-ai-20261106_102417_503(1).zip` (đã có P3–P3.6).
> Mục đích: không gọi Gemini dựng nhánh khi Learning chưa được mở; Root chỉ giữ tên môn và nhánh đang học rất ngắn.

## 1. Trạng thái khi chưa mở Learning

- `ConversationContextAssembler` đọc `LearningContinuity.brief()` (tối đa 4 môn, mỗi tiêu đề/tên nút tối đa 52 ký tự, thêm số lượng môn còn lại). `RootAgent` nhận `learning_brief`, không nhận toàn graph, lịch sử dài hay điểm BKT/FSRS ở từng lượt chat thông thường.
- `brief()` được cache tại tiến trình và tự xóa cache khi continuity thay đổi. Dữ liệu lưu thật vẫn nằm trong SQLite/meta hiện có; KHÔNG tạo database/memory trùng mới.
- Root gọi `canvas(operation="graph_create", title="...")` thì tạo môn và job **`deferred`**. Không gọi Gemini dù vẫn lưu được tên môn, root node, lịch sử tạo.
- Root có thể gọi `learning_brief`, `library` để biết môn; chỉ dùng `learning_history`, `snapshot_full` khi cần đọc chi tiết. Việc chủ động gọi các công cụ này là truy xuất theo nhu cầu, không phải background prefetch.
- Service Canvas vẫn khởi tạo các đối tượng nhẹ để nối runtime/giữ tương thích; **không có yêu cầu Gemini nào được phép bắt đầu chỉ từ việc khởi động Eris**.

## 2. Khi người dùng mở trang Learning

- Mỗi tab web có `client_id` ngẫu nhiên riêng. `canvas.js` gửi `open` cùng ID; Godot whitelists chuyển qua UDP đến Python. Backend gia hạn lease **40 giây** cho tab đang hiển thị.
- Trình duyệt ping mỗi 12 giây lúc hiển thị; khi tab bị ẩn/đóng, gửi `session_close` dùng `keepalive`, đồng thời có TTL dự phòng nếu trình duyệt crash/mạng mất. Nhiều tab mở được quản lý tách biệt.
- Chỉ khi panel active, backend mới được xếp Gemini tạo nhánh. Nếu môn được Root tạo lúc panel đóng: **người học chọn môn đó trong thư viện**, công việc `deferred` chuyển sang `queued`, Gemini dựng 2–4 nhánh rồi cập nhật graph.
- Chỉ xem trước một tầng; các nút khác vẫn ẩn như P3.5. Không tải cả cây trước.
- Khi ẩn tab, không xếp request Gemini mới. HTTP Gemini **đã bắt đầu** có thể kết thúc và lưu cache; nếu tab đóng, kết quả chỉ cập nhật dữ liệu/job, không tự chen lời Root. Nếu trình duyệt không gửi được close, lease có thể tồn tại tối đa khoảng 40 giây.
- Nếu job đang `deferred` và Eris khởi động lại, nó vẫn `deferred` (không bị nhầm thành job `interrupted`).

## 3. Ranh giới trách nhiệm

- Root: hội thoại + tóm tắt rất ngắn. Không suy ra trình độ từ tiêu đề hay lượt xem.
- LearningContinuity/Graph: lưu lịch sử đầy đủ; chỉ đọc sâu khi người học/Root thực sự yêu cầu.
- Gemini Web worker: tối đa một tác vụ đang chạy, chỉ dùng khi panel active. Vẫn dùng adapter/lane Gemini sẵn có, không thêm agent loop hoặc prompt mới dài.
- Writer: không bắt buộc thông báo job `deferred` là đã hoàn tất. Với job `queued`, chỉ quyết định thông báo sau khi có receipt `ready` và panel còn active.

## 4. Cài đặt

1. Sao lưu thư mục `eris/` và dữ liệu học trước khi chép đè.
2. Giải nén ZIP P3.7 change-only vào **thư mục cha chứa `eris/`**, chọn ghi đè. KHÔNG giải nén đè thành `eris/eris/`.
3. Build lại dự án Godot/.NET theo workflow Eris trên Windows.
4. Khởi động Eris, yêu cầu bằng hội thoại “tạo môn Mạng máy tính”, sau đó **chưa mở panel**: Root nhận `status=deferred`, không có Gemini call mới.
5. Mở Learning bằng nút của Eris; chọn môn đó. Gemini tạo vài nhánh; thử đổi môn, quay lại, rồi ẩn/đóng tab và kiểm tra không phát sinh prefetch mới.

## 5. Test và giới hạn

- Có test mới `src/tests/test_learning_canvas_p37_lazy.py` cho: defer khi panel đóng; mở đúng môn thì mới chạy; đóng/hết hạn lease chặn call mới; nhiều tab; cache brief invalidation; khởi động lại khi job deferred.
- Các test cũ về P3–P3.6/Root lifecycle đã được điều chỉnh vì *ngữ nghĩa cũ "Root tự mở browser và Gemini tự chạy" không còn đúng*.
- Kiểm chứng Python/JS và test chạy tại môi trường tạo ZIP. **Chưa build Godot/.NET trên Windows và chưa xác minh mạng Gemini Web thật**. Bạn cần chạy smoke test với bản runtime của mình để xác minh phần HTTP bridge và event thực.

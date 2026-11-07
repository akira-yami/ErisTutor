# ERIS Learning P3.5 — Khám phá tri thức mở dần (Progressive Exploration)

## Ý tưởng và cách dùng

**Không sinh 30 nhánh cùng lúc.** Người học chỉ thấy đường mình đã đi và khoảng 2–4 nhánh gần nhất. Khi bấm một nhánh, những gợi ý đã chuẩn bị ở tầng ẩn được mở ngay; Gemini tiếp tục chuẩn bị tầng kế tiếp ở nền, không cần người học bấm nút “sinh 30 nhánh”.

Ví dụ:

1. Tạo chủ đề `Spring Boot` trên web Learning Canvas.
2. Gemini (nếu dịch vụ đã cấu hình) trả 2–4 nhánh cấp 1; mỗi nhánh có 1–3 gợi ý **cấp 2 ẩn**. Nội dung này chỉ là bản nháp.
3. Bấm `Spring MVC` → gợi ý cấp 2 của nhánh đó được mở tức thì, không đợi Gemini.
4. Trong lúc đọc, Gemini được giao **một việc ngắn** để chuẩn bị gợi ý cấp 3 của những nhánh đang mở. Nếu chưa có cache, Gemini có thể sinh trực tiếp các nhánh gần nhất.
5. Chuyển sang nhánh khác → lượt chờ bị thay bằng yêu cầu mới; kết quả Gemini cũ không được tự điều hướng quay về nhánh trước.
6. Root nhận sự kiện chọn nhánh để quyết định có cần nghiên cứu, trò chuyện hoặc bổ sung nhánh hay không. Writer vẫn do Root điều phối; bản nháp Gemini **không phải** câu trả lời chuyên môn đã được kiểm chứng.

## Thành phần thực hiện

| Vùng | Nhiệm vụ |
|---|---|
| `runtime/learning_canvas/graph.py` | Ghi graph bền vững; khi chọn nút, phát lộ cache ẩn; chặn trùng, CAS revision và giới hạn payload. Không ghi mastery. |
| `runtime/learning_canvas/prefetch.py` | Tái sử dụng `GeminiWebUserProfileAdapter`/Gemini Web service. Tối đa 1 lời gọi ở một thời điểm, xếp hàng hợp nhất latest-wins, có cooldown, hết hạn / loại bỏ kết quả cũ, có đóng worker. |
| `runtime/learning_canvas/service.py` | Tạo/switch node, lên lịch prefetch không chặn ACK. Phát lại state khi Gemini hoàn tất. |
| `runtime/learning_canvas/events.py` | Gộp nhiều sự kiện click `graph_select` liên tục để Root ưu tiên nhánh mới nhất. |
| `runtime/capabilities/canvas.py` | Thêm `graph_prefetch` để Root chủ động yêu cầu Gemini chuẩn bị một nhánh mà không đổi focus. Giữ `graph_expand` cho nhánh do Root bổ sung. |
| `runtime/conversation/tool_guides.py` | Hướng dẫn Root nghiên cứu theo nhánh người dùng quan tâm, không đào sâu cả cây, Writer không tự công nhận bản nháp là thật. |
| `body/richchat/canvas.js` | Bản đồ chỉ vẽ tổ tiên của nút đã chọn + tối đa 4 nhánh ngay sát; có nút xem thêm nếu người dùng tạo nhiều nhánh. Hiển thị trạng thái chuẩn bị ở nền. |
| `body/UdpListener.WebLearningCanvas.cs` | Bỏ qua state cũ với graph revision thấp hơn để worker không bị ACK cũ ghi đè. |

## Cam kết và giới hạn

- **Nhánh chưa kiểm chứng:** `origin=gemini_draft`. Không tự tạo LearningPath chính thức, không thay đổi bài kiểm tra, evidence hay mastery. Thêm bằng tay và Root đề xuất vẫn sử dụng `user`/`eris_proposed`.
- **Tiết kiệm:** tối đa 4 nhánh gần, mỗi nhánh 1–3 gợi ý ẩn. Một Gemini request/worker/lượt, không mở loop agent liên tục; cache và dữ liệu graph tồn tại sau khởi động lại.
- **Không chiếm UI:** Gemini tự thêm nhánh/cached hints nhưng không đổi `selected_group_id`, `selected_node_id`, focus hay audio. Các click liên tiếp được gộp; kết quả bị thay thế không áp dụng.
- **Tương thích P3:** cùng metadata key và `schema=eris.learning.graph.p3`. Pack Java Dependency Injection cũ và Learning Core không thay đổi.
- **Giới hạn phiên bản:** bản đồ P3 vẫn giới hạn 64 nút/bản đồ, 24 chủ đề, 35KB dữ liệu/bản đồ để an toàn UDP. Đây là *P3.5*, chưa có phân trang cho graph rất lớn, cũng chưa có UI bài giảng chuyên sâu (P4).
- **Không tự nghiên cứu nguồn:** Root *có thể* dùng công cụ research đang có để kiểm chứng nhánh; P3.5 bổ sung sự kiện và hướng dẫn điều phối, **không** giả vờ rằng Gemini đã trích nguồn hoặc Writer đã chấm đúng/sai toàn bộ nội dung.

## Kết nối Gemini Web sẵn có

Mặc định sử dụng cấu hình Gemini của memory subsystem hiện có, không thêm thư viện/pool/token mới:

- `ERIS_LEARNING_GRAPH_GEMINI_ENABLED=1` (mặc định). Đặt `0` để tắt việc tạo nhánh tự động; web vẫn cho tạo và thêm nhánh thủ công.
- `ERIS_MEMORY_GEMINI_ENABLED=1` (cùng adapter hiện tại).
- `ERIS_MEMORY_GEMINI_BASE_URL=http://127.0.0.1:8000/v1` (theo cấu hình runtime).
- `ERIS_MEMORY_GEMINI_MODEL=gemini-flash` (nếu service của bạn hỗ trợ model này).

Nếu Gemini Web service chưa chạy, Learning Canvas vẫn dùng được; ghi chú "Gemini chưa phản hồi" có thể xuất hiện và không có dữ liệu nháp bịa sẵn. Một lượt Gemini có timeout 16 giây và cooldown khi lỗi, không khóa UI/Root.

## Cài đặt ZIP change-only

1. **Yêu cầu P3 đã được áp dụng** (ZIP `eris_learning_P3_dynamic_graph_change_only.zip` từ lượt trước).
2. Giải nén ZIP P3.5 vào **thư mục cha chứa `eris/`** rồi ghi đè file trùng tên. Nếu muốn quay lại P3, chỉ cần khôi phục các file đã ghi đè từ ZIP P3 hoặc Git.
3. Mở Eris và Learning Canvas. Tạo chủ đề, xem Gemini sinh vài nhánh, mở nhánh và kiểm tra gợi ý ẩn có được mở tức thì hay không.
4. Nếu thiếu gợi ý, kiểm tra trạng thái Gemini Web service và hai biến môi trường ở trên. Không có Gemini vẫn thêm nhánh thủ công như P3.

## Kiểm thử

```bash
cd eris
PYTHONPATH=src pytest -q src/tests/test_learning_canvas*.py
node --check src/body/richchat/canvas.js
```

Đã chạy trên môi trường phát triển: **74 test Canvas/P3/P3.5 qua**; `node --check` và `compileall` qua. Chưa kiểm chứng build Godot/.NET hoặc Gemini service Windows thực tế. Chromium trong môi trường kiểm tra chặn các URL localhost/file nên chưa xác nhận ảnh chụp giao diện chạy thật.

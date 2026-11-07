# ERIS · Learner Portrait Web v1 (02/11/2026)

## Mục tiêu

Thay bảng **“ERIS HIỂU BẠN THẾ NÀO”** cũ bằng web UI 4 trang: Chân dung, Kiến thức, Bằng chứng, Phát triển. Cho phép khám phá nhiều lộ trình học **không cần bảng kỹ năng cố định**; chỉ sử dụng bằng chứng thực tế của Learning Core. Dùng browser hệ thống, **không nhúng webview/không mở server riêng**.

## Ranh giới trách nhiệm — Gemini là Portrait Curator

- **Gemini phụ trách phần diễn giải có căn cứ**: đọc danh sách lộ trình, các tín hiệu (bài độc lập đúng/sai, bài có hỗ trợ, đến hạn ôn), các nhóm Mentor đã đủ bằng chứng và diễn giải thành nhận xét tiếng Việt có `source_refs`.
- **Learning Core sở hữu sự thật học tập** (đường học, KP, assessment, hỗ trợ, lịch ôn). Mentor sở hữu bằng chứng kỹ thuật riêng. Gemini không được sửa hai nguồn này.
- Gemini **không được tự tính mastery, gắn nhãn năng lực hoặc suy ra kiến thức đã biết từ việc chỉ hỏi**. Chỉ ra nguồn và phần chưa chắc; khi thiếu bằng chứng trả mảng rỗng.
- Không gửi raw transcript, raw screenshot, secret, token, tài khoản hoặc private thought. Dữ liệu qua `scrub_secrets`; chỉ tóm tắt paths/nodes/signals đã giới hạn.
- Writer chạy luồng daemon sau snapshot; **UI không chờ Gemini**. JSON ngoài nguồn bị loại; fingerprint thay đổi khiến kết quả cũ bị từ chối. Không tạo nguồn chân lý thứ hai.
- Gemini Web đang dùng `GeminiWebUserProfileAdapter` sẵn có (cùng lane với memory). Biến môi trường: `ERIS_LEARNER_PORTRAIT_GEMINI_ENABLED=1` (mặc định), `ERIS_MEMORY_GEMINI_ENABLED=1`, `ERIS_MEMORY_GEMINI_BASE_URL`, `ERIS_MEMORY_GEMINI_MODEL`. Khi không có kết nối, UI vẫn hoạt động và hiển thị fallback; sau lỗi thử lại khi nhận request sau >=90 giây.

**Quan trọng:** Bản v1 chỉ để Gemini viết **diễn giải**. Chức năng phát hiện chủ đề tích lũy từ mọi cuộc hội thoại chưa được triển khai; không coi việc có web UI là đã xong Topic Memory. Tài liệu này giao cho Gemini hướng phát triển tiếp theo, không phải bằng chứng Gemini đã chạy thử thật.

## Kiến trúc đã triển khai

```
Eris (Godot context menu)
  └─ OpenLearnerPortraitPortal() → RichChat HTTP listener 127.0.0.1 + token
       ├─ /portrait/index.html (JS/CSS embed trong Eris.csproj)
       ├─ GET /api/portrait/state          [cache trả về, read-only]
       ├─ POST /api/portrait/refresh       [queue tới Godot main thread]
       ├─ POST /api/portrait/select        [chỉ đổi lộ trình xem]
       └─ POST /api/portrait/page          [phân trang tối đa 48 lộ trình/lần]
            ↓ UDP message: capability_panel_request, action=portrait_snapshot
            ↓ incoming_protocol_runtime.py
            ↓ LearnerPortraitService.build()
                ├─ learning_spaces() + learning_insights(path_id=...)
                ├─ CapabilityProjectionService (Mentor, read-only)
                └─ GeminiPortraitNarrator (async, evidence-only, optional)
            ↓ ack → Godot → tokened localhost endpoint → web UI
```

Không có `activate_path()` hay ghi dữ liệu Learning Core khi chọn lộ trình trong web. State chỉ phản ánh dữ liệu Tutor đã lưu; khi thiếu dữ liệu, nói thiếu dữ liệu. Bản đồ sử dụng node/edge prerequisite thật, không dùng Gemini để tự dựng kỹ năng. Không tạo biểu đồ tăng trưởng thời gian giả. Phân trang 48 paths; mỗi path lấy tối đa 48 nodes vì UDP có giới hạn gói; có nhãn khi cắt bớt.

## File sửa / thêm

- `src/runtime/learner_portrait.py` [MỚI] — projection + Gemini curator worker.
- `src/runtime/incoming_protocol_runtime.py` [SỬA] — thêm action `portrait_snapshot` độc lập với Mentor actions.
- `src/body/UdpListener.RichChatPortal.cs` [SỬA] — reuse loopback HTTP server, giữ host/token/origin checks.
- `src/body/UdpListener.CapabilityPanel.cs` [SỬA] — nút context mở web; fallback Godot; nhận ack và cache JSON.
- `src/body/richchat/portrait/{index.html,portrait.css,portrait.js}` [MỚI] — web UI thuần, hoạt động offline không CDN.
- `src/tests/test_learner_portrait_web.py` [MỚI] — tests.

## Cách dùng trên Windows

1. Giải nén ZIP **change-only** vào dự án Eris cùng cấu trúc `eris/`, ghi đè file tương ứng. Không xóa file khác.
2. Dùng cách build Godot/C# hiện tại (mục tiêu Godot.NET SDK 4.7.2 / net8.0), khởi động Brain và desktop Eris.
3. Mở menu ngữ cảnh nhân vật → nút cũ **“Eris hiểu bạn thế nào”**: nay tự mở trang Learner Portrait trong browser; fallback panel cũ nếu browser không mở.
4. Đổi tab, chọn lộ trình, chọn nút graph, xem bằng chứng. Chuyển lộ trình **không thay đổi lộ trình Tutor đang học**.
5. Muốn Gemini viết nhận xét: chạy Gemini Web service sẵn có tại `ERIS_MEMORY_GEMINI_BASE_URL`; nếu không có service, phần số liệu/graph vẫn đọc được.

### Test ở môi trường dev

```
python -m pytest src/tests/test_learner_portrait_web.py -q
node --test src/body/richchat/tests/*.test.cjs
node --check src/body/richchat/portrait/portrait.js
```

**Chưa thực hiện được:** build Godot/.NET và kiểm thử Gemini Web thật trên máy của người dùng; môi trường xây patch không có dotnet SDK và không có phiên đăng nhập Gemini của user. Vì vậy đây là bản mã nguồn đã test logic, chưa xác nhận E2E desktop.

## Các đầu việc tiếp theo giao cho Gemini

1. **Topic Observation** (riêng biệt Learning Core): tạo quan sát sở thích/chủ đề từ *hội thoại đã lọc*, gắn source_ref; tuyệt đối không tăng mastery theo số lần hỏi. Bổ sung consent/data scope và cơ chế sửa/xóa.
2. **Entity resolution:** gợi ý các cụm kiến thức trùng nghĩa/xuyên môn; mọi merge phải có deterministic validation + lưu redirect/history, không xóa evidence.
3. **Learner Portrait linh hoạt:** thể hiện quan sát, giả thuyết, nhận định có căn cứ và phần chưa biết, tách rõ bằng chứng quan tâm với thành thạo; không gắn nhãn tâm lý.
4. **Growth thực:** snapshot đánh giá theo kỳ và so sánh cùng tiêu chí, thời gian, mức hỗ trợ; không dựng đồ thị ảo.
5. **Performance:** khi nhiều nghìn paths/KPs, chuyển từ UDP snapshot lớn sang paginated read-through API nhưng vẫn bảo vệ loopback/token/origin và không tạo store học tập thứ hai.

## Những điều Gemini không được làm

Không tự mở lộ trình, tự chấm điểm, tự ghi lại dữ liệu Learning Core; không thêm agent loop mới; không gửi thông tin nhạy cảm; không hiển thị nhận xét không có `source_refs`; không ghi đè lời sửa/xóa của người dùng; không biến việc user hỏi nhiều thành năng lực cao.

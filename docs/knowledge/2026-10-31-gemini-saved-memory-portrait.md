# ERIS — Gemini Saved Memory Portrait (31/10/2026)

## Mục đích

Gemini làm nhiệm vụ **biên tập lại ký ức thành chân dung người dùng**; không trở thành nguồn sự thật mới. **Giữ nguyên UI Godot**. Cửa sổ Memory đọc bản đã lưu; không chờ Gemini trả lời ở luồng UI.

## Luồng hoạt động

1. Living User Memory trả các fact/episode đã được lọc cho panel, **không bao gồm dữ liệu nhạy cảm**.
2. Bộ biên tập tính fingerprint từ nội dung evidence. Personality/relationship lens chỉ quyết định giọng kể, không tạo thêm fact và không làm fingerprint đổi liên tục.
3. Nếu bản `living_user_portrait.json` có fingerprint trùng: trả ngay nội dung từ file, **không gọi Gemini**.
4. Nếu bản chưa có / evidence đã đổi: bỏ bản cũ khỏi UI, hiển thị fallback an toàn, đồng thời cho **một worker nền** gọi Gemini qua adapter hiện có (`gemini_web`).
5. Gemini trả JSON. Runtime xác thực loại section, giới hạn số lượng item và kiểm tra `source_refs` có trong evidence trước khi chấp nhận. Không lưu lời nói không có item hợp lệ.
6. Lưu **atomically** hai file nằm cạnh profile memory:
   - `living_user_portrait.json` — bản có cấu trúc, metadata, version và source refs; đây là file runtime đọc.
   - `living_user_portrait.md` — bản dễ đọc; không phải canonical memory.
7. Khi Gemini đang chạy, Godot hỏi snapshot mỗi 2,5 giây (chỉ khi panel mở). Khi bản mới lưu xong, UI tự hiển thị và ngừng hỏi.
8. Memory GC định kỳ cũng có thể phát hiện thay đổi và xếp việc viết lại, kể cả khi panel đang đóng.
9. Khi người dùng sửa / xác nhận / xóa / đổi scope ở panel, hệ thống **vô hiệu hóa và xóa bản chân dung cũ ngay**. Kết quả Gemini đến muộn bị từ chối theo generation token.

## Cài đặt bản vá change-only

Sao lưu thư mục Eris trước. Mở ZIP, chép nội dung thư mục `eris/` của ZIP **đè vào thư mục `eris/` gốc**, giữ nguyên đường dẫn. Không xóa các file không có trong ZIP. Khởi động lại Eris và dịch lại dự án Godot/C# như bình thường.

**Không phải tạo tài khoản Gemini mới.** Patch dùng `GeminiWebUserProfileAdapter` vốn đã có trong Eris. Cấu hình hiện hữu:

- `ERIS_MEMORY_GEMINI_ENABLED=1` (mặc định bật)
- `ERIS_MEMORY_GEMINI_BASE_URL` (mặc định `http://127.0.0.1:8000/v1`)
- `ERIS_MEMORY_GEMINI_MODEL` (mặc định `gemini-flash`)
- `ERIS_MEMORY_GEMINI_TIMEOUT_SEC` (adapter mặc định 120 giây)

Nếu service Gemini Web không chạy, panel vẫn mở được và dùng văn bản dự phòng; file canon không thay đổi. Việc thử lại sau lỗi được giãn tối thiểu 90 giây nếu có yêu cầu snapshot hoặc GC ở lần sau. Nút **Viết lại** ở panel yêu cầu tạo bản mới ngay cả khi nội dung nguồn chưa đổi (trừ khi đang có job chạy).

## Quy tắc lưu trữ và an toàn

- Không lấy output của Gemini làm evidence, không ghi đè `living_user_profile.json` hay các episode.
- Không ghi raw screenshot, token, credential hay raw internal thought vào prompt của composer.
- `source_refs` trong portrait là liên kết đến các mục bằng chứng ở mức UI; bản quản lý gốc vẫn giải thích evidence chi tiết.
- Xóa/sửa/di chuyển memory **qua UI** lập tức hủy bản diễn đạt cũ; khi có thay đổi từ đường khác, portrait phải qua bước so fingerprint trước khi được phục vụ trên UI.
- Cache Godot cũ được bỏ để tránh hiển thị trí nhớ đã bị xóa từ phiên trước. File lưu mới chỉ ở phía Brain.
- Chân dung là lời kể AI đã được giới hạn evidence, **không phải phép kiểm chứng ngữ nghĩa tuyệt đối**. Phần quản lý memory vẫn là nơi người dùng đối chiếu và sửa thông tin.

## Kiểm thử và giới hạn

- Unit test mới xác nhận: tạo file một lần, khởi động lại đọc file offline, không lộ nội dung cũ sau chỉnh sửa, không nhận kết quả model trễ sau lệnh quên, fallback không chặn UI.
- Các kiểm thử liên quan bản vá: **24 passed**.
- Test suite memory rộng: **131 passed / 3 failed**; đã chạy lại đúng ba bài lỗi trên ZIP gốc và cả ba vẫn thất bại, nên không thuộc patch này.
- Chưa kiểm thử tích hợp với **Gemini Web thật và Godot C# thực tế** trong môi trường này (không có .NET/Godot). Cần chạy thử ở máy Windows có Eris.

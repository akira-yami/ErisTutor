# Giao diện hội thoại và học tập Eris — 17/10/2026

Giao diện Godot thật, dùng chung một cửa sổ cho lịch sử, tin nhắn đang diễn ra,
ô nhập, mục tiêu học tập và nhắc nhở. Mở từ menu nhân vật **Hội thoại & học tập**.

Ảnh từ renderer Godot, dùng dữ liệu minh họa của scene xem trước:

- [Bố cục đầy đủ](screenshots/companion-wide.png)
- [Khi chưa có dữ liệu](screenshots/companion-empty.png)
- [Cửa sổ hẹp](screenshots/companion-narrow.png)
- [Form nhắc nhở](screenshots/reminder-form.png)

## Hướng thiết kế

Nền sáng, chữ tím than, hồng làm điểm nhấn; tăng khoảng thở và giữ chat là phần
chính. Dùng tên Eris và ảnh đại diện đang có trong dự án. Lịch sử vẫn nhóm theo
ngày của dữ liệu hiện có, có tìm nội dung, chọn ngày và trở về hôm nay.

Gửi tin giữ cửa sổ mở; đóng cửa sổ giữ bản nháp. Có sao chép từng tin và hỗ trợ
Tab/Enter/Escape. Ở cửa sổ hẹp, nút lịch sử và mục tiêu chuyển giữa các phần để
ô nhập và nội dung vẫn đủ rộng.

Mục tiêu là checklist học tập cá nhân theo ngày: thêm, sửa, xóa, đánh dấu hoàn
thành, thanh tiến độ. Nút **Cùng bắt đầu** chuẩn bị bản nháp theo mục tiêu chưa
xong; người dùng chủ động gửi. Checklist không sinh evidence hay sửa mastery.
`LearningService` tiếp tục là nguồn sự thật về việc học như mô tả trong
[README của runtime](../../src/README_VI.md).

Nhắc nhở có nội dung, ngày và giờ; đến hạn hiện thông báo trong ứng dụng. Các
lời nhắc bị lỡ khi đóng Eris được kiểm tra khi mở lại. Trạng thái đã báo được
lưu để tránh lặp lại. Không có dịch vụ thông báo khi ứng dụng đã đóng.

## Dữ liệu và mã nguồn

- Giữ nguyên store lịch sử `user://eris_conversation_history.json` và chuyển đổi
  dữ liệu cũ đang có. Không tạo một conversation loop hoặc phiên giả mới.
- Checklist/nhắc nhở lưu riêng tại `user://eris_personal_planner.json`, ghi qua
  file tạm rồi thay thế. Lỗi đọc không âm thầm ghi đè dữ liệu gốc.
- `src/body/UdpListener.CompanionWorkspace.cs`: layout, composer, planner.
- `src/body/UdpListener.ConversationUi.cs`: lịch sử và tin nhắn dùng chung.
- `src/body/ErisPlannerStore.cs`: persistence và lịch nhắc.
- `src/body/UdpListener.cs`: menu, input, hit testing và kết nối ô nhập hiện có.

Giao thức gửi vẫn là `chat` rồi `chat_end`. Việc xem lịch sử hoặc để cửa sổ mở
không giữ quyền lượt của runtime. Không sửa agent loop, routing, thinking,
observer, cá tính hoặc TTS.

## Kiểm tra và xem trước

Build C# thành công, không warning/error. Scene đã được chạy bằng Godot .NET
4.7.2, render OpenGL thật và kiểm tra ở 1440×900, 1000×760, 720×760, 480×760,
cả dữ liệu minh họa lẫn trạng thái trống. Có kiểm tra thao tác tìm/ngày/gửi,
thêm/sửa/đánh dấu mục tiêu, thêm/xóa/validation nhắc nhở, báo đến hạn, lưu/mở
lại, đổi ngày và bảo toàn file lỗi. Xem [log kiểm tra](verification-log.txt).

Mở `tools/ui-preview/companion_preview.tscn` trong Godot để xem riêng UI, hoặc:

```powershell
dotnet build Eris.csproj --no-restore
godot --path . --scene res://tools/ui-preview/companion_preview.tscn -- --verify
godot --path . --scene res://tools/ui-preview/companion_preview.tscn -- --verify --empty
```

Scene xem trước dùng cùng code UI, dữ liệu minh họa riêng trong RAM, không đọc,
ghi hoặc xóa dữ liệu người dùng và không kết nối backend. Nút cài đặt bị vô
hiệu trong scene này; trong ứng dụng nó mở panel cài đặt hiện có. Ảnh kiểm tra
được xuất vào `scratch/ui-qa/`.

Snapshot workspace hiện thiếu thư mục `asset/` mà scene desktop tham chiếu
(ví dụ `asset/Resource/Monika.tscn`), nên chưa xác nhận toàn bộ scene desktop
cùng backend. File tri thức runtime được AGENTS.md dẫn tới cũng không có trong
snapshot này; thay đổi được giới hạn ở UI, không đưa ra chẩn đoán runtime mới.

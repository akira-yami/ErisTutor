# ERIS — Living Memory Glass Diary UI (bản vá sửa lỗi đóng gói)

## Mục đích

- Giữ **Godot panel** hiện có; chỉ nâng phần trình bày, không tạo website riêng.
- Dùng `portrait` đã lưu từ Brain/Gemini; UI không gửi lời nhắc trực tiếp tới Gemini.
- Giữ chế độ **Quản lý** (xác nhận, sửa, quên, chuyển phạm vi, đọc nguồn).
- Hiển thị trạng thái tạo chân dung, số phiên bản, thời gian cập nhật, và tổng quan theo dữ liệu `counts`.
- Với đoạn mở đầu dài, hero hiện phần rút gọn, **bản đầy đủ được đọc trong vùng cuộn**.

## Nguyên nhân lỗi ở ZIP UI trước

`Eris.csproj` có lệnh `Compile Remove="src\integration\body_contracts\**"`. ZIP trước
chỉ ghi đè `src/integration/body_contracts/UdpListener.MemoryPanel.cs` nên Godot không
thấy thay đổi UI. Bản này cập nhật **cả** `src/body/UdpListener.MemoryPanel.cs` và
bản tích hợp, với cùng nội dung.

ZIP trước còn có hai cấu trúc thư mục song song (`eris_ui_memory_patch/eris` và `eris/`).
Bản này chỉ chứa `eris/` ở gốc. Hãy dùng **bản này thay ZIP UI trước**.

## Phụ thuộc và thứ tự cài đặt

1. Trên project gốc, **cài gói Gemini Saved Memory trước** (nếu chưa cài):
   `ERIS_Gemini_Saved_Memory_change_only_VI.zip`.
2. Sau đó giải nén gói UI **bản đã sửa** này và chép thư mục `eris/` đè theo đúng đường dẫn.
3. Không xóa các file khác trong project; nên sao lưu project trước khi ghi đè.
4. Mở lại Godot/Eris; mở panel Memory, kiểm tra trạng thái portrait, thống kê,
   nội dung chân dung dài và chế độ Quản lý.

## Những file thay đổi

- `eris/src/body/UdpListener.MemoryPanel.cs` — **file Godot biên dịch**.
- `eris/src/integration/body_contracts/UdpListener.MemoryPanel.cs` — bản đồng bộ.
- `eris/src/tests/test_memory_panel_glass_diary_ui.py` — test tĩnh tránh tái diễn lỗi đường dẫn.
- `eris/docs/knowledge/2026-10-31-memory-glass-diary-ui-v2.md` — hướng dẫn và giới hạn.

## Kiểm chứng

- Test tĩnh Python kiểm tra file Godot biên dịch, đồng bộ, đủ điểm nối dữ liệu và nút quản lý.
- **Chưa build hoặc xem màn hình chạy thật** trong môi trường này vì không có Godot/.NET SDK.
- Giao diện là glass-diary *mô phỏng* bằng panel có alpha và bo góc;
  không phải Windows backdrop blur/native acrylic.

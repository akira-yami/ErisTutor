# ERIS Memory UI v3 — Moonlit Diary

## Mục tiêu

Thay toàn bộ **chế độ đọc memory** Godot thành trang nhật ký có ảnh trang trí,
giữ nguyên giao thức Brain/Python và chế độ Quản lý memory gốc.

## Hướng thiết kế

- Một cửa sổ trong suốt **1040 × 680** theo kích thước có sẵn của Eris.
- Chữ và dữ liệu là **Control Godot thật**, không dùng ảnh mockup làm UI.
- Sidebar 196 px: ảnh minh họa AI được crop tránh chữ giả, điều hướng các kind.
- Vùng phải: ScrollContainer duy nhất, card mở đầu hiển thị **đầy đủ** opening.
- Sections chia hai cột ở tổng quan, một cột khi chọn tab chi tiết.
- Lời nói bên lề xuất hiện nếu Gemini thật sự sinh aside.
- Thống kê 4 nhóm chuyển sang Quản lý, không làm loãng nội dung nhật ký.
- Chuyển trạng thái mở bảng bằng fade-in nhẹ 0,2 giây; không chuyển vị trí
  vùng click của cửa sổ overlay Windows.

## Dữ liệu

UI chỉ dùng `portrait.opening`, `portrait.aside`, `portrait.sections`,
`generation_status`, `generated_at`, `version` và `counts` trong snapshot.
Không lưu portrait riêng bên Godot. Bản memory đã lưu vẫn do Brain quản lý.

## Điều hướng

- Trong mắt Eris: mọi section và đoạn opening.
- Những điều tôi biết: `how_i_see_you`.
- Điều bạn thích: `things_you_like`.
- Kỷ niệm chung: `our_memories`.
- Điều chưa chắc: `not_sure`.
- Quản lý ký ức: mở manager có các hành động cũ.

## Asset

- `src/body/assets/memory/moonlit_illustration.png`: chân dung minh họa
  nhân vật ERIS chính thức của dự án (từ `identity_portrait_eris.png.png`),
  được căn chỉnh và tạo ánh trăng dịu nhẹ cho khung thẻ Memory.
- `src/body/assets/memory/moonlit_header_glow.png`: tranh ánh trăng và
  hoa mờ không chứa chữ, dùng làm nền cho phần đầu trang.

## Kiểm thử và giới hạn

- Test Python chỉ kiểm tra đường dẫn asset, tương thích hợp đồng và cấu trúc
  C#; không kiểm chứng hiển thị thật trên Windows.
- Muốn xác nhận pixel và click-through cần chạy build **Godot .NET 4.7.2**
  trên máy người dùng rồi chụp màn hình.
- UI này tạo cảm giác glass bằng alpha/nền nhiều lớp, **không dùng blur hệ
  điều hành** để tránh phải thêm thư viện native hoặc shader desktop nặng.

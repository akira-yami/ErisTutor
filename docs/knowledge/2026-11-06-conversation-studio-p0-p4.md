# Eris Conversation Studio — P0–P4 (06/11/2026)

## Mục tiêu

Đưa Rich Chat web từ giao diện hội thoại tối thiểu thành một không gian hội thoại có điều hướng, công cụ, tình trạng agent thực tế và tích hợp tốt với Eris đang chạy trên Godot. **Không thay đổi Root/agent-loop, không tạo AI/chat history thứ hai, không tải Learning Canvas ngầm khi panel đóng.**

## Những gì đã triển khai

| Lớp | Chức năng thực tế | Vị trí |
|---|---|---|
| P0 | Thiết kế lại sidebar, header, chat bubble, composer's toolbar, responsive mobile; dark mode lưu tại máy; inspector thu gọn; tự động dành chỗ cho Live2D nổi trên desktop rộng (có tùy chọn tắt) | `index.html`, `studio.css`, `studio.js` |
| P1 | Sao chép/trích dẫn, bookmark cục bộ bằng fingerprint, sửa **rồi gửi lại** (tin nhắn mới, không ghi đè lịch sử), gợi ý để bắt đầu, định tuyến theo ngày/chủ đề, xuất transcript đang có sang Markdown | `chat.js`, `studio.js` |
| P1 | Đính kèm **tài liệu văn bản** (kéo-thả hoặc chọn file, tối đa 3 file); dữ liệu thực được chèn an toàn vào nội dung tin nhắn, ràng buộc ký tự và byte JSON dưới quota HTTP | `studio.js` |
| P2 | Inspector hiển thị trạng thái thật từ `_runtimePresence` (stage/status/activity/emotion), lịch hoạt động trong tab và số artifact; trạng thái Speaking gắn đúng turn | `UdpListener.RichChatPortal.cs`, `studio.js` |
| P3 | SSE /api/events gửi snapshot live khi có thay đổi; fallback GET /api/state; hạn chế 8 client / 45s và ping; cập nhật DOM theo key, giữ nội dung tin nhắn/MathJax/widget cũ khi có tin nhắn mới | `UdpListener.RichChatPortal.cs`, `chat.js` |
| P3 | Tùy chọn browser notification sau khi quan sát một lượt active rồi chuyển idle và tab đang ẩn, chỉ khi người dùng cấp quyền | `studio.js` |
| P4 | Nhập lời nói bằng browser SpeechRecognition khi hỗ trợ; không can thiệp TTS native; UI cho biết trạng thái diễn tiến và chừa chỗ cho Live2D do Godot quản lý | `studio.js`, `studio.css` |

## Hợp đồng dữ liệu

```
Root / stage / TTS / conversationEntries / artifact bundles (single writer Godot)
  -> UpdateRichChatSnapshot() (main thread)
  -> Volatile.Read(ref _richChatSnapshot)
  -> GET /api/events  [SSE state event, nếu kết nối được]
     GET /api/state   [fallback polling]
  -> chat.js: acceptState -> reconcile message DOM theo key + render artifacts
  -> eris:state -> studio.js: presence/timeline/sidebar/bookmark/notification

Gửi câu hỏi:
  composer / file text -> POST api/send -> _richChatOutbox
  -> TickRichChatPortal() MAIN THREAD -> SendUserChatMessage(...)
  -> Root/agent-loop hiện hữu
```

## Những phần KHÔNG thể coi là đã làm xong

1. **Streaming token thực sự:** SSE hiện stream **snapshot đã được Godot commit** và trạng thái agent, không phải đoạn văn đang được LLM phát từng token. Muốn streaming token, phải thêm luồng delta trước transcript commit và cơ chế định danh/cancel để không ghi đè turn mới. Không được tự chia câu bằng timeout rồi gọi đó là streaming thật.
2. **Gửi ảnh vào Vision:** hiện chỉ gửi tệp TEXT; browser `SpeechRecognition` là API của trình duyệt và có thể dùng dịch vụ trực tuyến theo sản phẩm/trình duyệt. Chưa có image ingestion / multimodal upload contract ở Root, không dựng UI cho phép upload ảnh nhưng gửi một chuỗi giả vờ.
3. **Live2D model trong web:** không nhúng model/rig vào Rich Chat. Model thật là lớp Godot độc lập; UI chỉ có tùy chọn chừa chỗ và hiển thị trạng thái nhận từ Godot.
4. **Quản lý nhiều cuộc hội thoại độc lập:** backend vẫn là một transcript. “Chủ đề mới” là marker trong cùng transcript, điều hướng theo chủ đề KHÔNG reset bộ nhớ hay mở session độc lập.
5. **Artifact edit API:** endpoint `/api/artifact/edit` vẫn trả `501 artifact_edit_not_implemented`. Tùy chọn “Nhờ Eris sửa” chuyển yêu cầu có tham chiếu artifact vào composer, không hứa save/execute fake.
6. **Voice wake-word / interrupt:** TTS gốc còn do Godot điều khiển. Browser voice input là một tính năng nhập liệu, không phải nâng cấp mic agent-loop.

## Tương thích và an toàn

- Giữ nguyên `canvas.html/canvas.js` và hoạt động lazy/on-demand của Learning; chỉ liên kết tới Canvas khi user chủ động mở.
- MIME của `studio.css`/`studio.js` được phân phối từ portal loopback hiện có, mọi route vẫn yêu cầu token URL và Host exact; không mở port công cộng.
- Bookmark **chỉ lưu hash/fingerprint** trong localStorage, không lưu nguyên văn đoạn hội thoại. Phần nhật ký trạng thái chỉ tồn tại trong RAM của tab.
- File text **không** được cất riêng trong server: nội dung đi cùng thông điệp vào transcript như người dùng tự dán.
- Thao tác sửa/gửi lại không viết đè lịch sử. Tắt bảng hoạt động hoặc chừa chỗ Live2D không gửi lệnh cho model.
- New topic sử dụng prefix “Chủ đề mới: ” trong tin nhắn được gửi, không xóa lịch sử.

## Hướng dẫn ghép ZIP change-only

Chép các file cùng đường dẫn dưới root `eris/` vào source, ghi đè các file có sẵn. File mới được thêm bên cạnh các file cũ. Build lại ứng dụng Godot C# để resource `studio.css` và `studio.js` được nhúng vào bản build. Mở Rich Chat từ menu cũ; không mở `index.html` bằng `file://` để test API vì token và HTTP server chỉ chạy khi Eris mở portal.

**Kiểm tra:** `cd src/body/richchat && node --test tests/*.test.cjs` — 21/21 bài test Node đạt ở môi trường chỉnh sửa. Browser visual smoke với dữ liệu fixture đã chạy desktop 1792×850, laptop 1366×800, mobile 420×850, không ghi nhận JavaScript page errors. **Chưa chạy dotnet build/Godot app integration** vì runtime này không có `dotnet`, cần kiểm thử E2E với Eris thật trên máy chạy Godot.

## Rollback

Trả các file đã sửa `index.html`, `chat.js`, `UdpListener.RichChatPortal.cs` từ phiên bản cũ; xóa `studio.css`, `studio.js`, bài test `studio.test.cjs` và tài liệu này (có thể giữ file mới nếu không được nạp, nhưng tốt nhất xóa sạch). Không động vào CSDL/trí nhớ hoặc thư mục Learning Canvas.

## Bản vá bổ sung: Xóa lịch sử hội thoại (6/11/2026)

### Tính năng

- Sidebar Rich Chat có nút **Xóa toàn bộ lịch sử** và nút **×** bên cạnh mỗi ngày để xóa riêng một ngày.
- Hộp xác nhận có thống kê số tin nhắn; xóa tất cả cần nhập đúng **XÓA**. Không xóa chỉ bằng thao tác cuộn/lọc hay xóa DOM.
- `POST api/history/delete`: nhận `{scope:"all"|"day",date:"yyyy-MM-dd"|"",confirmation:"DELETE_ERIS_HISTORY"}`; chỉ nhận cùng Origin, token route localhost, giới hạn body; không có API xóa qua GET.
- Máy chủ queue yêu cầu sang **Godot main thread**; trả `200` **chỉ khi** main thread thực hiện xóa và lưu dữ liệu. Quá hạn khi chưa xử lý sẽ hủy yêu cầu; nếu Eris còn đang xử lý lượt thì trả `409 busy` để tránh hiển thị lại lời đáp từ lượt cũ.
- `day`: `RemoveAll` trên `_conversationEntries` rồi `SaveConversationHistory()` vào `user://eris_conversation_history.json`; nếu lưu lỗi, khôi phục dữ liệu RAM. `all`: dùng `ClearConversationHistory()` hiện có để xóa bản cũ, file lịch sử mới và dữ liệu transcript trong RAM; kiểm tra file còn tồn tại trước khi báo thành công.
- UI chỉ loại bỏ fingerprint bookmark của các tin nhắn bị xóa, không lưu bản sao hội thoại ở browser.
- Xóa **chỉ lịch sử hiển thị/lưu trên máy**. Không phải xóa dữ liệu huấn luyện/memory dài hạn/Learning và không hủy các artifact độc lập hoặc lịch sử nội bộ ở nguồn khác nếu có.

### Giới hạn và kiểm thử

- Sidebar đang nhóm lịch sử **theo ngày**, chưa quản lý các thread/phiên tách biệt; do đó chưa có nút xóa riêng một cuộc hội thoại trong ngày.
- Node tests: **23/23 đạt**. Chromium mock: 4 tin nhắn ở 2 ngày → xóa ngày 05/11 còn 2 → xóa tất cả còn 0; không lỗi JavaScript.
- Chưa build Godot C# trên máy đích; cần build và chạy Eris thật để xác nhận HTTP endpoint và persistence end-to-end.
- Sao lưu thư mục `user://` trước khi kiểm tra xóa dữ liệu thật. Không thể hoàn tác khi chưa có bản sao lưu.

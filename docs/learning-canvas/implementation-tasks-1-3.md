# Learning Canvas — hoàn thành Task 1–3 (01/11/2026)

**Báo cáo lịch sử tại thời điểm kết thúc Task 1–3.** Trạng thái “Task 4–10 chưa
triển khai” dưới đây đã được cập nhật cho Task 4–6 trong
[báo cáo đợt tiếp theo](implementation-tasks-4-6.md).

Phạm vi đợt này là dữ liệu DI, backend điều hướng và Canvas Godot để đọc.
Task 4–10 của [kế hoạch](learning-canvas-plan.md) chưa được triển khai. Mở Canvas
hoặc đọc một nút không tạo evidence, tăng mastery hay kích hoạt lời giảng.

## Dữ liệu và tiến độ

- `src/runtime/learning_canvas/packs/java-di.json`: 5 nút viết tay, giải thích
  tiếng Việt và ví dụ Java: dependency, constructor, setter/field, test double,
  composition root. Có định nghĩa trắc nghiệm và một bài Java với harness ẩn;
  chưa có chức năng nộp/chạy bài trong phạm vi này.
- `pack.py`: kiểm schema/version, namespace, giới hạn kích thước, ID trùng,
  DAG/tiền đề, đáp án trắc nghiệm, định nghĩa bài Java. Gói lỗi bị từ chối trước
  khi đăng ký. Public detail không chứa đáp án, options, harness hay test ẩn.
- Đăng ký domain `java-di`, path `javadi:v1:path`, các KP `javadi:v1:*` qua
  `LearningService`. Đăng ký lại không reset tiến độ hoặc đổi active path của
  môn khác. JSON được khai báo trong setuptools package-data.
- `snapshot.py`: schema `eris.learning.canvas.v1`, ánh xạ stage của
  `learning_insights()` thành available/learning/practicing/mastered; retained
  hiển thị mastered. `due` là nhãn độc lập. Tiền đề chưa vững chỉ tạo cảnh báo.
  Không suy ra tiến độ từ lượt click hoặc số đoạn đã đọc.

## Giao thức và lane nhanh

UI gửi `type=learning_canvas_request`, `request_id` duy nhất, `group_id=java-di`.

| Action | Trường bổ sung | Hành vi |
| --- | --- | --- |
| `open` | `node_id` tùy chọn | Snapshot gọn và detail của đúng một nút; không sửa focus |
| `navigate` | `node_id`, `base_revision` số nguyên | CAS focus, `set_by=user`, `pinned=true` |
| `ack_eris_focus` | `base_revision`, `cause_id` | Xác nhận cause đã nhận; không biến thành navigate |

Backend trả `learning_canvas_ack` với `status=ok/conflict/error/unavailable`,
`should_speak=false`, `request_id`, và snapshot `learning_canvas` khi có thể.
Revision cũ trả conflict cùng focus hiện tại. `LearningCanvasService.state_packet()`
tạo `learning_canvas_state` cho state từ server; UI có nhánh nhận riêng, xác
nhận cause và không echo thành navigate. Việc Root tự gọi/publish focus thuộc
Task 4/9, chưa nối vào conversation trong đợt này.

`CanvasFocusStore` lưu revision, node, nguồn user/eris, pin, cause và gợi ý ở
`schema_meta.learning_canvas_v1` trong learning SQLite hiện có. CAS dùng
transaction SQLite, kể cả hai connection tranh chấp. Không có store mastery mới.
Retry cùng request giữ nguyên kết quả trong cache 128 request của process;
sau restart revision vẫn chặn việc áp lại request cũ.

`CanvasControlInbox` là reader duy nhất của transport sau bootstrap. Worker
phục vụ riêng request Canvas; các gói khác giữ FIFO và chờ dispatcher foreground
hiện có. Coordinator dừng worker trước khi shutdown các subsystem. Handler và
route tương ứng nằm trong `incoming_protocol_runtime.py`/`incoming_dispatcher.py`.
Không vào chat, không tăng generation, không gọi kernel, model hoặc TTS.

Snapshot chỉ mang danh sách nút và detail của một nút. Validator giới hạn detail
24 KB; service chặn response trên 60 KB. Năm response của gói hiện tại đều dưới
16 KB trong test. Không gửi toàn bộ ledger hoặc toàn bộ gói qua UDP.

## Godot

Mở menu nhân vật **Learning Canvas · Java DI**. Partial
`src/body/UdpListener.LearningCanvas.cs` dùng control Godot native: DAG phân
tầng có đường nối tiền đề, màu và nhãn trạng thái, vùng giải thích, Java chỉ đọc
có chọn/copy text. Escape hoặc nút × đóng panel; mở lại lấy focus từ backend.

Cửa sổ rộng hiển thị bản đồ/nội dung cạnh nhau. Cửa sổ hẹp dùng nút **Bản đồ** /
**Nội dung**, có cuộn riêng; Java dùng font monospace và wrap theo chiều rộng.
Panel được đưa vào hit testing của transparent desktop overlay.

Click đổi vùng đọc ngay; chỉ một request navigate đang bay và click mới nhất
được gộp lại. Ack cũ không kéo panel về nút trước. Conflict dùng revision mới
để gửi lại ý định hiện tại. Request UDP retry cùng ID tối đa ba lần; hết thời
gian hoặc backend unavailable thì quay về focus đã xác nhận, cho phép đồng bộ lại.

`Eris.csproj` loại `dist/`, `build/`, `scratch/` khỏi Compile: bản sao C# có sẵn
ở `dist/dev` từng gây duplicate definition khi build project gốc. Đây là sửa
phạm vi input build, không xóa hay sửa bản portable.

## Kiểm chứng

- **137 passed**, 9 file test: Canvas mới (40 test), learning_core, protocol /
  transport, learning kernel boundary, Tutor integration, learning spaces,
  bootstrap, Capability Panel và Memory Panel. Không cộng các lần chạy lẻ vào
  con số này. Môi trường thiếu FSRS ban đầu làm 4 regression cũ fail; cài đúng
  `fsrs==6.3.2` vào `scratch/canvas-test-deps` và chạy lại đầy đủ thì đạt kết quả trên.
- UDP loopback thật: 5 request đều đạt assertion ack <200 ms khi một foreground
  Root giả đang chờ Event. Sandbox chặn cả UDP giữa hai socket cùng process;
  phép thử được chạy ngoài sandbox, không skip và không đổi transport sang mock.
  Demo độc lập đo một ack **31,8 ms**; đây không phải cam kết latency cho mọi máy.
- Demo ghi attempt synthetic trong DB tạm: Constructor đổi **available →
  practicing**; mở lại store vẫn giữ trạng thái, focus và pin. Không ghi attempt
  thử vào dữ liệu học thật.
- `dotnet build Eris.csproj --no-restore`: **0 warning, 0 error**.
- Godot .NET **4.7.2**, renderer OpenGL thật: preview `--verify` và
  `--verify --empty` pass ở **1440×900, 1000×760, 720×760, 480×760, 360×640**.
  Kiểm tra cả click nhanh, conflict, chống echo, ack cũ/mất, unavailable,
  đóng/mở panel, nội dung Java và chiều rộng vùng đọc. Đã xem ảnh bản đồ rộng,
  bản đồ hẹp, vùng đọc hẹp và code hẹp. Preview chỉ dùng fixture/RAM, không nối
  backend hay đọc/ghi learning data của người dùng.
- Ruff pass cho package Canvas, test mới và script demo; `git diff --check` pass.
  Quét Ruff toàn các mixin cũ còn báo 106 lỗi sẵn có về import/thao tác khác,
  nên không tuyên bố toàn repo lint sạch.

Log có thể xem ở [verification-log.txt](verification-log.txt). Ảnh QA sinh vào
`scratch/ui-qa/learning-canvas/`. Engine trong sandbox cảnh báo không ghi được
log/shader cache mặc định `user://`; renderer và các assertion UI vẫn chạy thật.

## Chạy lại

```powershell
.venv/Scripts/python.exe tools/learning_canvas_demo.py --udp
dotnet build Eris.csproj --no-restore
godot --path . --scene res://tools/ui-preview/learning_canvas_preview.tscn -- --verify
godot --path . --scene res://tools/ui-preview/learning_canvas_preview.tscn -- --verify --empty
```

`godot` cần là bản .NET 4.7.2. Trong phiên kiểm tra này dùng executable giải nén
từ zip toolchain có sẵn vào `scratch/canvas-godot/`. Fixture có thể tạo lại bằng
`tools/learning_canvas_demo.py --write-fixture`; các màu/trạng thái minh họa
trong fixture không phải kết quả học của người dùng.

Chưa export/deploy portable, chưa chạy hội thoại/model/voice thật. Chưa có
capability Canvas của Root, chấm bài, sandbox Java, đổi focus trong delivery
hay highlight audio; các mục đó vẫn thuộc Task 4–10.

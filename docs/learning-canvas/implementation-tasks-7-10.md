# Learning Canvas — triển khai Task 7–10

Ngày 01/11/2026. Hoàn thành các phần source còn lại của
[kế hoạch](learning-canvas-plan.md), nối tiếp [Task 1–3](implementation-tasks-1-3.md)
và [Task 4–6](implementation-tasks-4-6.md). Không tạo conversation loop, mastery
store hoặc chủ thể phát lời mới. Chưa triển khai lên bản cài đang dùng.

## Task 7: đổi nhánh trong lúc xử lý/phát lời

`ConversationKernel.note_focus_change` giữ nguyên generation và delivery đã
commit; run THINKING/WORKING/WRITING chưa commit bị vô hiệu hóa. Click không đi
qua chat, không gọi hủy audio. Guard chung giữa cập nhật cursor và handoff sang
delivery phân định trường hợp click đến sát thời điểm commit; CAS thất bại
không hủy run. Callback Root cũng kiểm tra scope trước khi tiếp tục gọi tool.

Click C rồi B được gộp còn B. Khi delivery A settle, loop thức dậy để xử lý event
đang chờ. Continuation lease và speculative candidate của A bị hủy; callback
settle đến muộn không tạo lại lease A nhờ focus epoch của turn.

Scope gồm `node_id` và `focus_revision`, được chụp khi bắt đầu research/work.
`focus_revision` chỉ đổi khi cursor được đặt lại; ack và huy hiệu gợi ý chỉ tăng
revision CAS, tránh coi kết quả đang chạy là cũ chỉ vì UI ack. Kernel và cổng
ingress kiểm tra scope trước/sau Root–writer. Kết quả research/work/assessment
đến muộn được giữ trong SQLite thành huy hiệu trên nút cũ, không tự nói hoặc
đổi focus. Mỗi nút giữ tối đa 4 kết quả; dữ liệu tối đa 32 KB/kết quả, vượt ngưỡng
thì giữ bản tóm tắt có cờ truncation. Snapshot đưa excerpt tối đa 2.000 ký tự.

Godot hiển thị chip “Eris đang nói về A” khi đang đọc B. Chỉ bấm chip mới điều
hướng về A. Khi đang chờ tiếng đầu tiên, chip ghi “đang chuẩn bị nói”.

## Task 8: highlight bám lifecycle audio

Root có thể trả `canvas_segments: [{block_id, say}]`, tối đa 6 phần. Backend kiểm
`block_id` theo đúng nút trong gói, bỏ phần có ID lạ. Trong một lần kernel gọi
writer, writer hiện thực từng brief Root đã chọn qua đường generate/localize/
prepare hiện có, rồi ghép candidate trước commit. Không suy diễn block bằng vị
trí ký tự, không mở thêm cổng public writer. Artifact seal bao gồm liên kết
segment–Canvas; candidate ghép cũng phải vừa trần datagram UDP.

Binding `learning_canvas` giữ node/revision/generation/block trên frame. Frame
Canvas luôn dùng dispatch theo segment, kể cả cấu hình ép inline. Đăng ký queued
chỉ tạo chip, chưa highlight. `audio_start` do TTS manager nhận từ callback device
thực sự mới gửi `canvas_highlight`; done/failed/cancelled xóa highlight. Settlement
dọn cả lượt chưa kịp có audio. Không thay đổi thời điểm release public text.

Packet mang stream backend, session, turn, generation, segment và seq. Godot loại
packet cũ, không kéo focus hoặc scroll theo highlight. Snapshot thông báo stream
mới khi backend restart; packet audio cũ không tự đổi stream. Logic bubble chỉ
khởi tạo lại tracking khi đổi turn, tránh chạy lại từ câu 0 ở mỗi segment.

## Task 9: focus có ghim/gợi ý

Root `canvas.focus` khi chưa ghim áp dụng focus và phát state có cause_id, không
tạo event navigate ngược. Khi đã ghim, chỉ lưu gợi ý, trả
`applied=false, reason=user_pinned`; native map hiện dấu “Eris gợi ý”.

Click nút được gợi ý chấp nhận đúng proposal/cause và bỏ ghim. Root có
`accept_focus` để diễn giải yêu cầu chấp nhận bằng lời của user; backend chỉ cho
phép ở user_turn có text, kiểm tra proposal và revision, không dùng keyword để
tự quyết ý nghĩa câu nói. Các thao tác focus hỗ trợ base_revision; click tranh
chấp với Eris chỉ có một CAS thắng, UI lấy revision mới rồi thử lại ý định user.
Focus, pin, proposal và tiến độ vẫn dùng SQLite có sẵn, còn sau restart.

## Task 10: nghiệm thu và build

Kết quả đầy đủ và phạm vi: [verification-tasks-7-10.txt](verification-tasks-7-10.txt).

- Bộ cuối **333 passed, 5 skipped**, 32 file Python, 25,32 giây. Bao gồm
  Canvas/focus/audio, Root/kernel/writer/artifact, AST quyền sở hữu lời nói,
  Tutor/learner, protocol panel, TTS và workspace. Năm test skip đều là JVM thật
  vì `docker_not_running`. Không cộng số lần chạy nhóm nhỏ vào tổng này.
- E2E dùng SQLite/khóa chấm MC thật, model/delivery/Java giả lập: hỏi DI → mở
  nhóm → chọn Constructor → giải thích → nộp MC/Java → ledger đổi → mở lại DB/
  backend mới → đổi nhánh khi A đang phát → kết quả muộn thành huy hiệu → Root B.
- Test TTS dùng WAV và device giả, đi qua TtsTaskManager thật để xác nhận start
  từng segment khi ép inline. Không gọi provider hoặc loa máy.
- C# build: 0 warning, 0 error. Godot .NET 4.7.2/OpenGL preview đủ dữ liệu và
  rỗng pass ở 1440×900, 1000×760, 720×760, 480×760, 360×640; thêm kiểm tra chip,
  highlight, packet cũ, pin/proposal và stream restart. Exercise và audio event
  trong preview là fixture, renderer/layout là thật. Đã xem ảnh narrow layout.
- Demo chấm MC bằng khóa thật: available → practicing; một attempt và focus/pin
  còn sau mở lại DB. Java trả unavailable và chuyển quiz, không tạo điểm giả.
- Ruff module/test/demo mới pass. So sánh 22 file Python tracked đã sửa với HEAD:
  baseline 229, hiện tại 229, không có finding mới. Không tuyên bố toàn repo sạch lint.

Lần regression đầu phát hiện một lỗi kiến trúc gọi lại public writer và thiếu
FSRS trong requirements; đã sửa và chạy lại toàn phạm vi. Lần đó cũng gặp race
workspace `test_resume_chi_sau_khi_nguoi_dung_tra_loi` đã ghi trong kế hoạch
(worker đổi queued → running trước assertion). Không sửa module/test workspace
để làm xanh; race không tái hiện ở lần cuối. Chạy nhóm Tutor trong sandbox từng
thất bại ở tạo artifact; lần nghiệm thu cuối dùng filesystem và UDP loopback
ngoài sandbox và pass. Các lần thất bại không được gọi là kết quả cuối.

Đã export debug mới từ source thành `Eris.exe`, `Eris.pck` và
`data_Eris_windows_x86_64`, rồi chạy `build_dev_fast.bat` thành công với output
`scratch/canvas-task10-build`. Template Windows chính thức đúng 4.7.2 được tải
riêng từ archive release và kiểm CRC. FSRS 6.3.2 được cài/kiểm trong interpreter
portable. Wrapper nhận tiếp arguments; `_fullbuild.py --godot-export-dir` chọn
đúng export mới, không lấy binary từ đường dẫn máy cũ. Godot export loại
dist/build/scratch để tránh đóng gói lại bản build tạm. Chưa tạo installer hoặc
khởi chạy hội thoại production. Bản dev trong scratch dùng config dev theo build
hiện có, không phải gói release để chia sẻ.

Smoke bằng Python portable **3.12.10** pass: module được nạp từ `build/src`,
FSRS đúng 6.3.2, SQLite tạm mở được snapshot 5 nút. Hash SHA-256 của exe, PCK và
Eris.dll trong portable khớp export; DLL export cũng khớp assembly ExportDebug
vừa biên dịch. Không dùng hoặc sửa tiến độ học của người dùng.

## Giới hạn và tri thức đã cập nhật

Docker Desktop vẫn chưa có Linux engine do lỗi sailor-ingest.sock đã điều tra
ở Task 4–6. Vì vậy chưa đo khởi động JVM/read-only/noexec hoặc xác nhận bài Java
thật trong container. Không chạy Java host để thay bằng chứng. Chưa kiểm
model/TTS provider, thiết bị audio hoặc toàn bộ vòng học live trên bản cài.

Đã đánh dấu hai nhận định lịch sử “ô không trong whitelist” và “ChatCycle chưa
nhận cancel_generation” là bị thay thế bởi source hiện tại trong
[tri thức runtime](../knowledge/2026-10-14-eris-runtime-lessons.md). Không xóa
quan sát trace cũ hay suy diễn chúng thành trạng thái production hiện tại.

Hiệu quả học tập chưa được chứng minh. Attempt/mastery và FSRS shadow là dữ liệu
quan sát; cần bài biến thể độc lập và kiểm tra nhớ lại sau một khoảng thời gian.

## Chạy lại

```powershell
$env:PYTHONPATH='scratch/canvas-test-deps'
.venv/Scripts/python.exe -m pytest src/tests/conversation/test_canvas_focus_lifecycle.py src/tests/conversation/test_canvas_audio_binding.py src/tests/conversation/test_learning_canvas_full_loop.py -q
dotnet build Eris.csproj --no-restore
godot --path . --scene res://tools/ui-preview/learning_canvas_preview.tscn -- --verify
godot --path . --scene res://tools/ui-preview/learning_canvas_preview.tscn -- --verify --empty
godot --headless --path . --export-debug 'Windows Desktop' 'scratch/canvas-task10-export/Eris.exe'
.\build_dev_fast.bat --output "$PWD/scratch/canvas-task10-build" --godot-export-dir "$PWD/scratch/canvas-task10-export"
```

Máy kiểm dùng Godot console trong `scratch/canvas-godot`, không cần alias `godot`.
Các kiểm thử/demo chỉ dùng DB tạm; không dùng tiến độ học của người dùng.

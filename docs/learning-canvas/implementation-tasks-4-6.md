# Learning Canvas — Task 4–6 (01/11/2026)

Đã triển khai capability Canvas cho Root, bài trắc nghiệm chấm bằng khóa đáp án,
và bộ chạy Java Docker cùng UI native. **JVM thật chưa được nghiệm thu** vì
Docker Desktop trên máy không khởi động được. Các kết quả dưới đây phân biệt
test offline, renderer thật, và phần Docker đang bị chặn.

## Task 4: Root sở hữu lời giảng

- `src/runtime/capabilities/canvas.py` cung cấp `library`, `open_group`, `snapshot`,
  `focus`. Root chọn `group_id` từ thư viện; ID lạ bị từ chối. `open_group` gửi
  state có `open_panel=true`; Godot mở panel mà không echo thành navigate.
- Đăng ký `canvas` trong RootCapabilityRegistry của lượt chat và signal, schema
  Root, tool guide, cùng definition/input contract trong capability registry.
  Đây là Root capability, không phải một action ID mới cho ActionExecutor.
  Không có keyword router tự giảng DI hoặc gọi model trong lane UI.
- Click đã nhận vào focus tạo `tutor_result`, `metadata.origin=user_ui`, mang
  block/nút đã chọn. `events.py` chỉ enqueue và wake loop; Root quyết định có nói.
  Click trung gian được gộp; assessment không bị gộp với navigation.
- Sửa hai nơi có thể mất event `busy`: orchestrator giữ lại event để thử lại;
  kernel không cache kết quả busy. Chỉ bỏ event đã xử lý hoặc stale do một run
  mới thắng. Khi click mới thay item cũ, consumer không popleft nhầm item mới.
- Kiểm tra vòng “DI là gì? → model gọi library/open_group → chọn Constructor →
  Root/writer/delivery” bằng router và delivery giả lập. Event Canvas không tăng
  generation hoặc gọi chat/cancel audio. Chưa kiểm tra model/TTS live.

## Task 5: Trắc nghiệm và bằng chứng thật

`src/runtime/learning_canvas/exercises.py` dùng `learning_activities`,
`learning_assistance`, AssessmentAttempt và LearningService hiện có. Không tạo
mastery store hoặc conversation loop khác.

| Action UI | Dữ liệu | Hành vi |
| --- | --- | --- |
| `exercise` | node_id, exercise_id | Tạo activity + nonce và trả câu hỏi/options hoặc starter Java |
| `hint` / `reveal` | activity_id, nonce | Ghi trợ giúp bền vững trước khi gửi nội dung trợ giúp |
| `submit` | activity_id, nonce, answer hoặc code | Chấm MC ngay; Java vào worker rồi ack running |
| `exercise_status` | activity_id, nonce | Lấy trạng thái thật; phục hồi lần chạy bị gián đoạn |

Đáp án, harness và test ẩn giữ ở backend. Client/model không được tự gán
`correct`, `result` hay mức trợ giúp. Xem lời giải MC là thao tác tường minh;
response bình thường không chứa khóa đáp án. Hint/solution Java chỉ được gửi
sau thao tác trợ giúp tương ứng.

Trợ giúp được giữ theo **đúng bài viết tay và hash định nghĩa**, kể cả mở activity
mới/nonce mới. Mở lại cùng câu đã xem lời giải không biến nó thành bài độc lập.
Việc ghi trước khi gửi là chủ ý thận trọng: nếu UDP ack mất, hệ thống có thể ghi
“đã cấp trợ giúp” dù UI chưa nhận được, nhưng không tính nhầm bài có trợ giúp là
tự làm. Không dùng receipt gửi gói để khẳng định người dùng đã đọc.

ID lần nộp gắn với activity/request, được lưu cùng hash câu trả lời. Retry sau
restart/evict cache không ghi thêm attempt hoặc mở thêm worker. Nếu DB gián đoạn
giữa chuẩn bị verdict và commit, UI chưa báo completed; retry dùng cùng attempt
đã chuẩn bị. Kết quả chỉ báo “đã lưu” sau khi ledger nhận được attempt.
Tối đa 32 lần nộp/activity; các lần lặp cùng họ câu hỏi không tăng số họ độc lập.

Một assessment event trả verdict/assistance cho Root, không tự phát lời. Kết quả
Canvas đến khi user đã ở nút khác vẫn được lưu và không kéo panel về hoặc phát
assessment của nút cũ. Đây chưa phải cổng chống kết quả muộn chung cho mọi
research/work result trong Task 7.

## Task 6: Java Docker và UI

`src/runtime/workspace/java_docker.py` chạy javac và Java trong container bằng
image ghim `eclipse-temurin:21.0.8_9-jdk-jammy`, một tag JDK cũng được dùng trong
[tài liệu chính thức Docker](https://docs.docker.com/get-started/docker-concepts/building-images/multi-stage-builds/).
Runtime không tự pull và không fallback sang javac/java host.

Policy test được: network none, filesystem read-only, cap-drop ALL,
no-new-privileges, UID 65534, PID 64, memory/swap 512 MB, CPU 1; mount source/harness
read-only; `/tmp` tmpfs 64 MB với noexec/nosuid/nodev. Bytecode được javac ghi vào
tmpfs và JVM đọc từ đó. JVM dùng SerialGC/ActiveProcessorCount=1/heap 128 MB.
**Khả năng khởi động JVM dưới policy này vẫn cần spike thật sau khi Docker hoạt động.**

Một watchdog trong container và watchdog phía host giới hạn thời gian. Timeout /
output vượt giới hạn dừng CLI và `docker rm -f` đúng tên container UUID do backend
tạo; không dùng tên container từ UI. Mỗi stream bị chặn ở 16 KB trong lúc đọc;
không đợi collect một output vô hạn mới kiểm kích thước. Không xuất stdout tùy
ý hoặc source excerpt của harness ra UI. Harness trả PASS/FAIL theo từng test;
đúng hết / một phần / sai ánh xạ correct/partial/incorrect qua AssessmentAttempt.
Sandbox cô lập host; không nhằm chống người dùng cố ý tự gian lận điểm.

UI trong `UdpListener.LearningCanvasExercises.cs` có lựa chọn MC, nút nộp/hint/
reveal, CodeEdit Java, Chạy, trạng thái đang chạy và kết quả từng test. Giao diện
giữ draft qua ack/state, bỏ packet activity/submission cũ và vẫn điều hướng được
khi Java đang chạy. Code tối đa 12 KB. Một worker Java phục vụ mỗi Canvas service.

Thiếu Docker/engine/image thì trả unavailable, không tạo attempt Java giả và
tự chọn bài trắc nghiệm đã viết trong cùng nút, kèm hướng dẫn. Code đã nộp vẫn
được giữ để quay lại Java. UDP inbound dùng cùng trần 63 KB với outbound; request
Canvas giới hạn 20 KB. Ack bài tập tránh gửi lặp code/activity và toàn bộ detail.
Test gửi source 9 KB qua UDP thật vẫn nhận ack <200 ms khi worker giả đang chờ.

## Kiểm chứng và giới hạn

Kết quả cuối và log đầy đủ: [verification-tasks-4-6.txt](verification-tasks-4-6.txt).

- Bộ regression gồm Canvas, Root/kernel, learning_core/Tutor và các route panel
  hiện có: **242 passed, 5 skipped**, 19 file, 17,38 giây. Năm test JVM thật được
  skip rõ lý do Docker engine chưa chạy; không cộng
  các lần chạy nhóm nhỏ vào tổng cuối trong log.
- C# build sạch 0 warning / 0 error. Preview Godot .NET 4.7.2/OpenGL thật kiểm tra
  layout ở 1440×900, 1000×760, 720×760, 480×760, 360×640 và trạng thái rỗng.
  Luồng quiz/hint/reveal, CodeEdit, queued/partial, fallback Docker và Root mở panel
  dùng fixture RAM; không xem kết quả Java fixture là phép chạy JVM thật.
- Demo backend MC chấm bằng khóa thật trong DB tạm: available → practicing;
  attempt/focus/pin còn sau mở lại SQLite. Demo Java trả unavailable /
  docker_not_running, không tạo điểm giả.
- Ruff cho package/module/test mới và demo pass. Không tuyên bố toàn repo lint sạch.
  Đối chiếu các file Python tracked đã sửa với HEAD: 138 lỗi cũ ở cả hai phía,
  không có lỗi Ruff mới.

Đã thử khởi động Docker Desktop và kiểm tra lại engine. Log backend báo không
truy cập/xóa được `AppData/Local/Docker/run/sailor-ingest.sock`; Linux engine pipe
không được tạo. Không reset Docker, xóa dữ liệu hay chạy Java trên host để thay
phép thử. Vì vậy **chưa đo được thời gian JVM khởi động**, chưa xác nhận chạy
solution/compile error/infinite loop/output flood bằng Java thật trong container.
Các case này có integration test tự chạy khi engine và image sẵn sàng.

Chưa export/deploy portable, chưa chạy voice/model thật; Task 7–10 vẫn còn.
Chưa có cancellation theo focus cho run chưa commit, chip đang nói về nút khác,
highlight audio_start hoặc đầy đủ giao thức chấp nhận/bỏ ghim của Task 9.

## Chạy lại

```powershell
.venv/Scripts/python.exe tools/learning_canvas_exercise_demo.py --output scratch/canvas-exercises.json
# Sau khi Docker Desktop hoạt động và đã có image:
docker pull eclipse-temurin:21.0.8_9-jdk-jammy
.venv/Scripts/python.exe tools/learning_canvas_exercise_demo.py --require-java
.venv/Scripts/python.exe -m pytest src/tests/test_java_docker_canvas.py --basetemp scratch/pytest-java -q
dotnet build Eris.csproj --no-restore
godot --path . --scene res://tools/ui-preview/learning_canvas_preview.tscn -- --verify
godot --path . --scene res://tools/ui-preview/learning_canvas_preview.tscn -- --verify --empty
```

`--require-java` trả exit code 2 nếu Java chưa khả dụng. Demo chỉ dùng DB tạm và
solution kiểm tra; không đọc/ghi tiến độ của người dùng hoặc kết nối model/TTS.

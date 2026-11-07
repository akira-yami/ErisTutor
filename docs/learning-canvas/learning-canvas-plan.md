# Implementation Plan - Learning Canvas (vòng học DI bằng Java)

**Cập nhật 01/11/2026:** đã triển khai **Task 1–10** trong source.
Xem [báo cáo Task 1–3](implementation-tasks-1-3.md) và
[báo cáo Task 4–6](implementation-tasks-4-6.md),
[báo cáo Task 7–10](implementation-tasks-7-10.md). Kiểm chứng JVM/Docker thật
đang bị chặn bởi lỗi khởi động Docker Desktop; luồng `unavailable` và fallback
trắc nghiệm đã được kiểm tra. Focus/delivery, highlight và vòng học có kiểm thử
giả lập; Godot preview dùng renderer thật. Đã export debug và kiểm tra build dev
trong `scratch`, chưa triển khai lên bản cài đang dùng hoặc kiểm chứng model/voice live.

Các mục bên dưới giữ yêu cầu/kế hoạch ban đầu để đối chiếu; báo cáo triển khai
ghi rõ bằng chứng và các giới hạn còn lại. Chưa khẳng định hiệu quả học tập.

## Vấn đề

Eris chưa có bề mặt học tập tương tác nằm trong cửa sổ Godot. Vòng nút bấm hiện có chỉ chạy trong Rich Chat và chỉ cho bài preset cứng.

Cần một Learning Canvas native gồm:

- nhóm kiến thức (nút, tiền đề, trạng thái);
- giải thích do Eris nói, highlight đúng đoạn đang phát;
- bài tập Java chạy thật trong sandbox;
- điều hướng chung giữa người dùng và Eris.

**Phạm vi đợt đầu:** một vòng hoàn chỉnh: hỏi DI → Canvas mở nhóm → chọn Constructor Injection → Eris giải thích và mở ví dụ → làm bài → trạng thái nút cập nhật từ kết quả thật.

**Ngoài phạm vi:** so sánh hai khái niệm và mô phỏng chuyên biệt.

## Yêu cầu đã chốt

- Panel native Godot (C#), không dùng Rich Chat.
- Gói kiến thức DI viết tay, ví dụ Java, chấm xác định.
- Có bài chọn đáp án và bài viết code Java chạy thật trong Docker.
- **Click sang nút B khi lời A đang xử lý (quy tắc 1a):**
  - Panel chuyển sang B ngay và giữ đúng vị trí người dùng chọn.
  - Lời A đã commit vào delivery thì phát hết, kèm chip "Eris đang nói về A", bấm mới quay lại.
  - Phần A chưa commit (Root hoặc writer còn đang nghĩ) thì bỏ.
  - Click liên tiếp C rồi B thì chỉ B được giải thích, sau khi A xong.
  - Click này **không** là user turn thường, vì user turn sẽ hủy audio A.
- **Eris tự đổi focus (quy tắc 2b):** chỉ khi người dùng chưa tự điều hướng kể từ lần Eris đổi gần nhất (chưa ghim). Nếu đã ghim thì chỉ hiện gợi ý trên nút.
- Highlight theo đoạn âm thanh thực sự bắt đầu phát (`audio_start`).
- Focus do Eris tạo không được quay lại thành yêu cầu mới của người dùng.
- Kết quả công cụ đến muộn của A không được nói khi người dùng đã ở B.
- Tiến độ giữ nguyên sau restart.
- Root sở hữu mọi lời nói. Capability chỉ trả dữ liệu và bằng chứng.
- **Java sandbox (quy tắc 3b):** chạy Java thật, theo đúng khuôn `DockerPythonWorkspace` (không mạng, read-only, bỏ mọi capability, giới hạn bộ nhớ, không fallback sang host).

## Hiện trạng code lúc lập kế hoạch (trước Task 1–6)

**Dùng lại được:**

- `learning_core`: `KnowledgePoint.prerequisites`, `MasteryRecord.stage`, `learning_insights()`, `AssessmentAttempt` (có `partial`, mức trợ giúp độc lập/gợi ý/xem lời giải), bảng `learning_activities`, con trỏ `learning_runtime_v1`.
- Khuôn request/ack của `capability_panel_request/ack` và `memory_panel_request/ack`, đăng ký tại `incoming_dispatcher.py:117-119`.
- `_pending_conversation_capability_events` + `_run_v12_signal` (đã chờ khi phase là thinking/writing/delivering).
- `ConversationEvent.kind="tutor_result"` đã nằm trong whitelist nhưng chưa có nơi tạo ra.
- `python_docker.py` / `DockerPythonWorkspace`.

**Khoảng trống phải xây:**

- Chưa có route điều hướng Panel và chưa có trường nguồn event (chỉ có `kind`).
- Kernel trả `busy` khi đang `DELIVERING`. Với `work_result`, event có thể bị `popleft` mất ngay khi kernel trả `busy` hoặc `stale`.
- `tts_event` không mang text hay generation. C# ghi đè `_ttsBodyAudioTurnId` ở mọi `audio_start`, nên highlight cũ chạy lại từ câu 0.
- Segment gộp inline chỉ phát một `audio_start` cho cả lượt, không highlight theo đoạn được.
- `research_result` chỉ bị loại bằng so khớp văn bản, generation được gán lúc giao.
- Bài học 16/10: lệnh panel từng trễ 29–33 giây vì được poll cùng vòng foreground. Điều hướng Canvas phải ack nhanh khi Root đang chạy.

**Tri thức lỗi thời:** `docs/knowledge` ghi "`ô` không có trong whitelist" và "ChatCycle chưa nhận `cancel_generation`". Source hiện tại không còn khớp (xem Task 10).

## Giải pháp

```mermaid
flowchart LR
  UI[Godot Learning Canvas] -- learning_canvas_request --> RT[incoming_dispatcher fast path]
  RT --> FS[CanvasFocusStore + pin]
  RT --> EX[Exercise grader / Java sandbox]
  EX --> LS[LearningService record_attempt]
  FS -- coalesced canvas event --> Q[pending capability queue]
  Q --> K[ConversationKernel: Root -> Writer]
  K --> TTS[TtsTaskManager audio_start per segment]
  TTS -- canvas_highlight node/block/seq --> UI
  RT -- learning_canvas_ack/state --> UI
  K -. tool canvas.open/focus .-> FS
```

### Quyết định thiết kế

1. **Package mới `runtime/learning_canvas/`:**
   - `pack`: loader và validator gói JSON.
   - `focus`: `CanvasFocusStore` có revision, `set_by` (user/eris), cờ `pinned`.
   - `snapshot`: tạo `eris.learning.canvas.v1`.
   - `exercises`: bài tập và chấm điểm.
   - `events`: cổng chặn event muộn.
   - Không dựng `Decision`, không gọi TTS; chỉ trả dữ liệu cho Root. Có AST test kiểm tra.
2. **Điều hướng là sự kiện UI có nguồn rõ**, không qua `chat`; không tăng generation, không hủy audio. Kernel thêm `note_focus_change`:
   - `THINKING/WORKING/WRITING`: bỏ run chưa commit.
   - `DELIVERING`: giữ nguyên, phát hết.
   - Mọi phase: hủy continuation lease của A.
   - Event "đã chuyển sang B" gộp theo kiểu click mới nhất thắng, chờ kernel rảnh; sau khi settle, loop tự thức dậy nếu còn event chờ.
3. **Chống echo:** event focus do Eris tạo mang `cause_id`. UI áp dụng nhưng không gửi lại `navigate`. Event vào kernel với `kind=tutor_result` (không phải `user_turn`) và `metadata.origin`.
4. **Highlight:** Root gắn mỗi segment nói với một `block_id`. Backend lọc id theo gói, bỏ id lạ. Khi `audio_start` của segment k thật sự đến, gửi `canvas_highlight{turn_id, generation, segment_id, node_id, block_id, seq}`. Godot chỉ áp dụng khi `turn_id` khớp lượt đang phát. Lượt Canvas bắt buộc không gộp inline.
5. **Java sandbox:** `DockerJavaWorkspace` cùng policy với bản Python (không mạng, read-only, bỏ capability, UID không đặc quyền, giới hạn bộ nhớ và PID).
   - Học viên viết một class; harness ẩn của gói biên dịch và chạy với test double.
   - Verdict lấy từ chạy thật: đúng hoàn toàn / một phần / sai → `correct` / `partial` / `incorrect`.
   - Không có Docker thì bài code chuyển sang `unavailable` rõ ràng, không giả vờ chấm.
   - Giới hạn: sandbox cô lập máy host, không chống người dùng cố ý gian lận điểm của chính mình.
6. **Trạng thái nút** tính từ `learning_insights()`: `available`, `learning`, `practicing`, `mastered`, và nhãn `due`. Tiền đề chưa vững chỉ cảnh báo, không khóa điều hướng.
7. **Tiến độ sau restart:** attempts và mastery nằm trong SQLite có sẵn. Focus, ghim, và nút "gợi ý tiếp" lưu ở `schema_meta` key `learning_canvas_v1`.

## Task Breakdown

### Task 1: Gói kiến thức DI + đăng ký vào learning_core + snapshot backend

**Mục tiêu:** có dữ liệu nhóm DI kiểm chứng được và snapshot trạng thái nút.

- Tạo `runtime/learning_canvas/pack.py` và gói `java-di` gồm khoảng 5 nút: khái niệm dependency; Constructor Injection; Setter/Field Injection; Unit Testing với test double; Composition root.
- Mỗi nút có block giải thích, ví dụ Java, định nghĩa bài tập. Validator kiểm DAG, id duy nhất, khớp đáp án.
- Đăng ký thành `LearningPath` (`javadi:v1:*`) qua `LearningService`.
- `snapshot.py` ánh xạ `learning_insights()` sang trạng thái nút.

**Test:** validator (chu trình, tiền đề lạ, block trùng); ánh xạ stage→state; restart giữ trạng thái; gói không hợp lệ bị từ chối.

**Demo:** script in snapshot `eris.learning.canvas.v1`; sau khi ghi một attempt giả vào test DB, trạng thái nút đổi và còn sau khi mở lại store.

### Task 2: Giao thức Canvas, kho focus, route nhanh

**Mục tiêu:** backend nhận request từ UI, ack nhanh, lưu focus có ghim.

- Thêm `learning_canvas_request` (`open`, `navigate`, `ack_eris_focus`) và `learning_canvas_ack/state`, theo khuôn `capability_panel_*`.
- Đăng ký route trong `incoming_dispatcher.py` + handler trong `incoming_protocol_runtime.py`.
- `CanvasFocusStore`: revision, `set_by`, `pinned`, lưu bền; `navigate` của người dùng luôn ghim.
- Xử lý trên lane nhẹ, không nằm sau vòng foreground (tách thread riêng nếu route hiện tại bị chặn). Payload nhỏ vì transport là UDP; chi tiết nút lấy theo request.

**Test:** ack < 200 ms khi một Root run giả đang chặn; request cũ (`base_revision` lỗi thời) bị từ chối; restart giữ focus và ghim.

**Demo:** script giả client gửi `navigate` và nhận ack; sau restart backend, focus vẫn đúng.

### Task 3: Learning Canvas native trong Godot (đọc + điều hướng)

**Mục tiêu:** mở Canvas, thấy nhóm nút, chọn nút và đọc nội dung.

- Tạo `src/body/UdpListener.LearningCanvas.cs` (partial) theo khuôn `UdpListener.CapabilityPanel.cs`.
- Gồm: mục menu, layout DAG phân tầng đơn giản, màu theo trạng thái, vùng chi tiết (block giải thích, code Java chỉ đọc), chế độ cửa sổ hẹp như `docs/ui/companion-workspace.md`.
- Cập nhật lạc quan khi click, chỉnh theo ack.
- Thêm cảnh trong `tools/ui-preview` có `--verify`, dùng dữ liệu minh họa trong RAM.

**Test:** build C# 0 warning/0 error; preview `--verify` ở nhiều kích thước và trạng thái rỗng; ghi log kiểm tra như các UI trước.

**Demo:** mở Canvas, chọn Constructor Injection, thấy nội dung; đóng và mở lại app, vẫn đúng nút.

### Task 4: Capability `canvas` cho Root + vòng "hỏi DI → mở nhóm → chọn → giải thích"

**Mục tiêu:** Eris mở nhóm khi người dùng hỏi về DI và giải thích khi chọn nút lúc kernel rảnh.

- Thêm capability `canvas` với `open_group(group_id)`, `snapshot`, `focus(node_id)` (tạm dùng cho Eris; quy tắc ghim ở Task 9). Đăng ký trong `capability_registry` và `tool_guides`.
- Root chọn nhóm từ danh sách trong thư viện; backend xác thực id.
- Click của người dùng tạo event `kind=tutor_result`, `metadata.origin=user_ui`, đi qua hàng đợi capability tới Root. Root quyết định có nói gì.
- Sửa lỗi mất event: chỉ `popleft` khi event đã xử lý hoặc loại có chủ đích; `busy` thì giữ lại thử lại.

**Test:** event canvas không tăng generation; AST test: package không tạo `Decision` hay gọi TTS; event `busy` được giữ và thử lại; id nhóm lạ bị từ chối.

**Demo:** hỏi "DI là gì?" → Canvas mở nhóm; bấm Constructor Injection → Eris giải thích bằng giọng (giả lập delivery ở test, thật khi chạy tay).

### Task 5: Bài trắc nghiệm + cập nhật trạng thái nút từ kết quả thật

**Mục tiêu:** làm bài chọn đáp án, ghi attempt, nút đổi trạng thái.

- `exercises.py` tạo bài từ gói; đáp án chỉ ở backend; mỗi lần hiển thị có id/nonce.
- Thêm `submit`, `hint`, `reveal` qua route Canvas. Gợi ý và xem lời giải ghi mức trợ giúp ngay khi Panel hiển thị; bài làm sau trợ giúp không tính là tự làm.
- Chấm xác định → `AssessmentAttempt` → `record_attempt` → ack chứa trạng thái nút mới.
- Gửi một event cho Root để phản hồi bằng lời. Root không tự chấm.

**Test:** submit idempotent; sai id/nonce bị từ chối; sau gợi ý, `assistance` ghi đúng; trạng thái nút đổi sau attempt; restart không mất kết quả.

**Demo:** làm bài Constructor Injection; nút đổi trạng thái từ attempt thật; mở lại app vẫn đúng.

### Task 6: Bài viết code Java chạy thật trong Docker

**Mục tiêu:** biên dịch, chạy, chấm bằng kết quả thật.

- Tạo `runtime/workspace/java_docker.py` (`DockerJavaWorkspace`) theo khuôn `python_docker.py`: image JDK ghim phiên bản, `command_for` tách riêng để test policy, timeout ngắn, giới hạn kích thước, không fallback host.
- Harness ẩn dùng test double. Verdict `PASS/FAIL` theo từng test, ánh xạ sang `correct/partial/incorrect`.
- UI: ô soạn code, nút Chạy, bảng kết quả theo test. Chạy trong worker thread, không chặn dispatcher.
- Không có Docker/image: trả `unavailable` kèm hướng dẫn; bài chuyển sang chế độ chọn đáp án.
- **Spike trước:** xác nhận JVM chạy được với `--read-only` và `/tmp` noexec, đo thời gian khởi động.

**Test:** policy `command_for` (không mạng, không capability, mount read-only) không cần Docker; test tích hợp có Docker dùng `skip` nếu thiếu; lỗi biên dịch, vòng lặp vô hạn, output quá lớn đều xử lý an toàn.

**Demo:** viết một class dùng Constructor Injection, bấm Chạy, thấy kết quả theo từng test; nút cập nhật trạng thái.

### Task 7: Đổi nhánh giữa lúc Eris đang nói (quy tắc 1a)

**Mục tiêu:** click B khi A đang xử lý hoạt động đúng như đã chốt.

- `ConversationKernel.note_focus_change`: bỏ run chưa commit, giữ delivery đang phát, hủy continuation lease của A.
- Gộp click mới nhất thắng; khi kernel settle thì thức dậy để Root giải thích B.
- `events.py` gắn `node_id` và `focus_revision` cho `research_result` và `work_result`. Kết quả muộn của A khi focus ở B thì lưu thành huy hiệu trên nút A, không nói ra.
- UI: chip "Eris đang nói về A" khi Panel ở B, bấm mới quay lại; không tự kéo về.

**Test:** các kịch bản theo phase (THINKING / queued trước `audio_start` / đang phát / continuation pending); click C rồi B chỉ giải thích B; kết quả muộn bị chặn; không audio nào bị hủy ngoài ý muốn.

**Demo:** Eris đang nói về DI, bấm Unit Testing; Panel sang ngay, lời DI nói hết, rồi Eris giải thích Unit Testing.

### Task 8: Highlight theo đoạn thực sự bắt đầu phát

**Mục tiêu:** highlight bám `audio_start` của từng segment.

- Root gắn `block_id` cho segment; backend kiểm theo gói rồi giữ làm metadata của frame.
- Frame Canvas bắt buộc không gộp inline (kiểm lại `_dispatch_policy`).
- Khi nhận `audio_start` của segment k, gửi `canvas_highlight`. Với `audio_done`, `audio_failed`, `audio_cancelled` thì xóa highlight.
- C# lọc theo `turn_id` và generation; không dùng lại logic highlight theo tỉ lệ ký tự của bubble.

**Test:** mỗi segment một highlight đúng thứ tự; `audio_start` của lượt cũ không đổi highlight lượt mới; block id lạ bị bỏ; hủy giữa chừng thì xóa highlight.

**Demo:** Eris giải thích ba ý, mỗi ý sáng đúng lúc tiếng bắt đầu.

### Task 9: Eris tự đổi focus có điều kiện (quy tắc 2b)

**Mục tiêu:** Eris chỉ đổi focus khi chưa ghim và không tạo vòng lặp.

- `focus` của Eris áp dụng khi cờ ghim tắt; nếu đã ghim thì trả `applied=false, reason=user_pinned` và hiện dấu "nên làm tiếp" trên nút.
- Người dùng điều hướng thì ghim; ghim tắt sau lần Eris đổi focus kế tiếp mà người dùng chấp nhận (bấm vào nút được gợi ý, hoặc yêu cầu bằng lời).
- `cause_id` chống echo. Tranh chấp đồng thời giữa người dùng và Eris xử lý bằng revision.

**Test:** bảng quyết định pin; click đồng thời với focus của Eris; focus của Eris không sinh `navigate` ngược; phục hồi sau restart.

**Demo:** làm đúng bài, Eris đưa focus sang nút tiếp theo khi chưa ghim; nếu đã tự đi chỗ khác, nó chỉ đánh dấu gợi ý.

### Task 10: Nghiệm thu vòng đầy đủ, build, cập nhật tri thức

**Mục tiêu:** chạy cả vòng, ghi rõ cái gì đã kiểm chứng và cái gì chưa.

- Test E2E giả lập: hỏi DI → mở nhóm → chọn → giải thích → làm bài Java → trạng thái đổi → restart → đổi nhánh giữa lúc nói.
- Chạy: nhóm test tutor/learner/kernel liên quan, AST architecture test, `dotnet build Eris.csproj`, preview `--verify`, `build_dev_fast.bat`.
- Cập nhật `docs/knowledge`:
  - thay hai ghi chú lỗi thời (whitelist `ô`, `cancel_generation`) bằng bằng chứng hiện tại, ghi rõ kết luận nào bị thay thế;
  - ghi giới hạn: chưa kiểm audio và Docker trên máy chạy thật nếu chưa thử.
- Hiệu quả học tập chưa được chứng minh. Đánh giá cần bài làm độc lập và kiểm tra nhớ lại sau một khoảng thời gian; bài biến thể độc lập sẽ nối vào lịch ôn FSRS shadow hiện có.

**Test:** bộ nghiệm thu trên; không gom số test từ các phạm vi khác nhau.

**Demo:** chạy cả vòng trong một phiên.

## Rủi ro cần theo dõi

- Docker và image JDK có thể chưa có trên máy. Task 6 xử lý bằng `unavailable` rõ ràng; có thể phải kéo image một lần.
- Khởi động JVM trong container có thể mất vài giây; UI cần trạng thái đang chạy.
- Gói UDP có giới hạn kích thước; snapshot nhẹ, chi tiết theo yêu cầu.
- Policy gộp inline có thể khiến highlight theo đoạn khó hơn dự kiến (Task 8).
- Race đã biết ở workspace (`test_resume_chi_sau_khi_nguoi_dung_tra_loi`) có thể làm một test báo lỗi không liên quan.

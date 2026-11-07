# Eris Runtime Reconstruction — Work Lane + Conversation Lane

Ngày: 28/10/2026. Nền source: `eris-ai-20261028_224738_047.zip` (user upload).

## Quyết định kiến trúc

- **Root/Identity là một semantic authority duy nhất**: tự quyết có giao việc, nói, hỏi, chờ hay hủy. Công việc nền **không** tự tạo public turn.
- **Conversation Lane** giữ Root → Writer → Localizer → TTS; không đợi Work Lane trả lời.
- **Work Lane** nhận `parallel_work start` và có `job_id`; model/tác vụ riêng xử lý tối đa 12 step, gọi tool có capability, đọc receipt để tiếp tục. Không được làm việc vượt scope.
- `status/recent/cancel` hoạt động bất đồng bộ với công việc; Root được cập nhật `work_lane_progress` tại mỗi lượt mới. User gửi tin mới **không mặc định hủy** job.
- **Workspace** nhận công việc qua `workspace_artifact` side channel: card theo `work_id`, revision; Godot thay card cũ với revision mới. ACK chỉ xác nhận state/hiển thị phía receiver, chưa chứng minh người dùng đã nhìn thấy.
- **Speech–Document**: `NextConversationMove.document_source` là trường tùy chọn lưu Markdown/LaTeX/code nguyên bản, `say` là brief để Writer nói. Writer không nhận nguyên document trong discourse prompt. Khi move có document, thiếu `speech_text` explicit → fail-fast, không lấy document/display thay vào giọng.
- V6 Live Document nhận `bubble_text` riêng để hash/ghép đúng turn, còn `text` là document_source nguyên bản. Presenter/Writer-review model mặc định **tắt** nhằm giảm inference và tránh tạo semantic owner thứ hai; có thể bật để thử riêng.

## Chỗ đã nối thật

- `src/actions/{contracts,capability_registry,executor}.py`: capability `parallel_work` (`start`, `status`, `recent`, `resume`, `cancel`). Worker dùng `execute_capability` của ActionExecutor => dùng sandbox/authority hiện hữu.
- `src/runtime/inference_broker.py`: lane `work_background`, priority 65, optional, concurrency 1; chat_main priority 10.
- `src/runtime/conversation/{agent,kernel,contracts,runtime_writer,public_output_artifact}.py`: Root được quyền giao việc, Root context nhận tiến độ; tùy chọn document độc lập với voice.
- `src/runtime/public_output_runtime.py`: dùng tài liệu Root nếu có thay vì lấy text của bubble làm nguồn bắt buộc.
- `src/runtime/workspace/{parallel_work,live_document}.py`: SQLite event ledger, worker state, cancellation, revision + V6.
- `src/body/UdpListener.ArtifactWorkspace.cs`: thay tiến độ theo `work_id`, không xếp chồng 12 card cho một job.

## Mô hình trạng thái

`queued → running → completed | waiting_user | cancelled | needs_review | failed`

- Tool dispatch được ghi trước khi chạy, tool result/evidence ghi sau. Khi restart trạng thái `queued/running` chuyển `needs_review`, **không tự chạy lại side effect**.
- Cancel khi tool đang chạy không giết được SDK/process đang thực thi; nó loại kết quả đến muộn trước bước commit của Work Lane. Side effect ở hệ đích có thể đã xảy ra — cần kiểm tra receipt/cơ chế idempotency của từng tool.
- SQLite là event journal cho job; không tự ý ghi đè transcript và goals.

## Kiểm chứng đã thực hiện

- Probe `tools/parallel_work_probe.py`: công việc được nhận ngay; MathWorkspaceService/SandboxWorld dùng SymPy tính định thức ma trận cấp 2; lưu artifact và receipt; work step hoàn thành.
- Test unit: snapshot không bị block khi tool chậm; cancel loại kết quả muộn; restart không tự replay; không có model thì `needs_review`; giới hạn tool; contract doc không biến thành speech; root prompt không nhận nguyên doc.
- Chạy **111 test** thuộc Workspace + Root/Kernel/Writer/Delivery/Output liên quan (đối chiếu log thực tế khi đóng gói).
- Test toàn bộ `src/tests/conversation` chưa đạt; có test assertion cũ sai sẵn ở source gốc: `test_writer_manifest_empty_does_not_bypass_action_guard` fail ngay cả trên ZIP chưa sửa. Nên không tuyên bố full regression pass.

## Chưa xác minh

- Root LLM và Work LLM với API thật, mức concurrency/quota/latency trên máy Windows.
- Godot C# build, Panel live + TTS ElevenLabs Nhật trong cùng một session.
- Kết thúc/recover work trên hệ thống có multiple processes hoặc file locks Windows.
- Planner tổng quát đủ thông minh để giải nhiều bài dài; worker được giới hạn tool cho đến khi chính sách capability và sandbox được kiểm chứng.

## Lệnh demo trên Windows

```powershell
$env:PYTHONPATH = 'src;.'
py -m pip install -r requirements-artifact-workspace.txt
py tools/parallel_work_probe.py
# Nếu Godot đã mở và cùng workspace artifact
py tools/parallel_work_probe.py --send
```

**Phân biệt rõ:** `parallel_work_probe.py` chỉ mô phỏng nhánh *chọn bước* để test luồng; phép tính SymPy, SandboxWorld, event SQLite và artifacts là thật. Trong runtime chính, `plan_with_router()` dùng model thật hiện hữu.

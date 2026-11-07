# Eris Learning P3.6 — Learning Continuity & Conversation Job Lifecycle

## Mục tiêu

Nối hành trình **trò chuyện với Eris → Root tạo môn → Gemini dựng vài nhánh ngầm → sự kiện quay về Root → Writer có thể thông báo khi phù hợp**; đồng thời giữ lịch sử khám phá để người học đổi môn và trở lại đúng chỗ cũ.

P3.6 **không tạo agent loop thứ hai**. Worker Gemini là một tác vụ bounded của P3.5. Mọi quyết định phát lời, mở panel, bắt đầu nghiên cứu sâu do Root/Conversation Kernel hiện tại nắm quyền.

## Phần đã triển khai

1. **LearningContinuity** (`runtime/learning_canvas/continuity.py`): lưu danh sách môn gần đây, con trỏ từng môn, lượt mở, sự kiện gần đây, trạng thái từng nút, thời điểm, và ước lượng thời gian tương tác. Lưu vào SQLite qua `LearningStore` với revision/CAS; không lưu câu chat và không lưu screenshot. Tiến độ bản đồ không ghi vào bảng mastery.
2. **Root Canvas capability**: `graph_create` giờ tự tạo `learning_job` và gọi prefetch Gemini. Gặp tên môn cũ thì trả `existing=true` thay vì tạo trùng. `graph_resume` tiếp tục nhánh cũ; `learning_history` lấy lịch sử. Root không tự giành focus khi tạo môn.
3. **Gemini Job Lifecycle**: job `queued → ready/unavailable/superseded/unchanged`. Job `queued` còn dở khi restart được chuyển sang `interrupted`. Thành công/thất bại được gửi thành `tutor_result` có `canvas_action=graph_ready` hoặc `graph_failed` qua hàng đợi event hiện có; Root quyết định im lặng, báo ngắn, hoặc tiếp tục. Không có speech tự động từ worker.
4. **UI**: thanh chủ đề gần đây và nút **Tiếp tục học**; mục lịch sử tác vụ/môn; đánh dấu **Tôi đã xem phần này**; trạng thái `Đang tìm hiểu`/`Đã xem`/`Đã thực hành`. Tự mở lại nhánh theo `selected_node_id` đã lưu, không phải sinh lại môn.
5. **Ước lượng thời gian**: khi tab web vẫn có tương tác gần đây, browser gửi heartbeat tối đa 30 giây/lần; backend rate-limit 25 giây, không tin số giây client. Con số này chỉ là ước lượng hoạt động, không phải số phút thật sự tập trung và **không được dùng tính mastery**.
6. **Chuyển tiếp P0–P3.5**: Java DI/Java exercises giữ nguyên. Mastery của Java vẫn đọc từ LearningCore; khám phá graph chỉ có `exploring/viewed`. Chỉ bài tập hoàn thành mới ghi `practiced` trên lịch sử. `mastered` chỉ khi có evidence được xác nhận; không cho người dùng/Gemini đánh dấu bằng cách mở nhánh.

## Luồng đầu cuối

```text
User: “Eris, tạo bản đồ Mạng máy tính”
    ↓
Root: read_tool_guide(canvas) → use_capability canvas(graph_create,title)
    ↓
GraphWorkspace.create(follow=false) → LearningContinuity(created)
    ↓
LearningJob(queue) → ProgressiveGraphPrefetch(Gemini Web)
    ↓
Gemini JSON {branches:[2–4], next:[1–3]}
    ↓
GraphWorkspace.apply_gemini(CAS) → LearningJob(ready)
    ↓
canvas.on_event → enqueue_canvas_event → _wake_autonomous_loop
    ↓
Conversation Kernel / Root kiểm tra ngữ cảnh hiện hành
    ↓
Root có thể thông báo “Tôi dựng xong phần đầu; mở Learning xem nhé”
    ↓
User mở Learning / chọn nhánh → Gemini prefetch tầng ẩn tiếp theo
```

**Lưu ý**: Đây là khả năng được nối và kiểm thử với fake Gemini; Root là model tự chủ, không có bảo đảm mỗi lần sẽ phát đúng một câu cố định. Không nên khẳng định Eris đã thông báo nếu chưa nhìn được log thực tế.

## Cách dùng

- Ở Rich Chat: “Tạo bản đồ kiến thức môn mạng máy tính” → Root có thể gọi Canvas capability. Đối với một số cấu hình model, Root có thể cần đọc hướng dẫn `canvas` trước.
- Ở Learning Web: dùng **+ Tạo bản đồ**, hoặc chọn môn cũ ở bộ chọn / thanh **Tiếp tục học**.
- Chọn từng nhánh; để lưu trạng thái đã xem, nhấn **Tôi đã xem phần này**. Không nhấn vẫn lưu trạng thái đang khám phá và vị trí cuối.
- Mở **Lịch sử học & tác vụ Gemini** để xem hoạt động gần nhất và job đã hoàn thành/lỗi.
- Tắt Gemini bằng `ERIS_LEARNING_GRAPH_GEMINI_ENABLED=0` nếu cần: bản đồ vẫn tạo/lưu được, trạng thái job phản ánh Gemini chưa khả dụng.

## Cài đặt

- ZIP tăng dần `eris_learning_P3_6_continuity_change_only.zip`: **yêu cầu** P3 + P3.5 đã được áp dụng.
- ZIP gộp `eris_learning_P3_to_P3_6_cumulative_change_only.zip`: áp dụng **một lần** lên bản gốc Eris ngày 2026-11-05 15:39; không phải toàn bộ source Eris.
- Giải nén vào **thư mục cha của `eris/`** rồi ghi đè đúng đường dẫn; sao lưu code/data trước khi áp dụng. Không xóa database LearningCore cũ.
- Cần build lại ứng dụng Godot/C# do có sửa cầu nối `UdpListener.WebLearningCanvas.cs`.

## Kiểm thử

- `PYTHONPATH=src pytest -q src/tests/test_learning_canvas* src/tests/test_web_learning_canvas_migration.py src/tests/test_conversation_rhythm_and_closure_regressions.py src/tests/test_p2_root_presentation.py` → **103 passed**.
- `python -m compileall -q src/runtime/learning_canvas src/runtime/capabilities/canvas.py src/runtime/conversation`
- `node --check src/body/richchat/canvas.js`.
- Suite `test_conversation_continuity_hotfix.py` có **hai lỗi tồn tại từ source ban đầu**: đòi hàm `_resolve_discourse_frame` không có trong `orchestrator.py` gốc và trỏ đến fixture SettingsPanel sai path. Không sửa các phần ngoài P3.6.
- **Chưa xác nhận** Godot/.NET build trên Windows, browser UI tương tác trực tiếp với runtime thật, hoặc Gemini Web live token/đăng nhập. Đây là những bước kiểm thử nghiệm thu cần chạy tại máy của bạn.

## Giới hạn cố ý

- Chỉ lưu khoảng 240 sự kiện gần đây, còn trạng thái từng môn/nút bền vững theo giới hạn bản đồ P3 (24 nhóm × 64 nút). Nội dung Gemini chưa được xác minh; không chuyển thành chương trình học chính thức nếu không có bước research/evidence.
- Job này không tự tạo bài giảng, code sandbox, mô phỏng đa môn: phần đó thuộc P4.
- Browser chỉ gửi heartbeat khi hoạt động gần đây và không bị ẩn; thời gian chỉ ước tính, không nên xem là số giờ học chính xác.

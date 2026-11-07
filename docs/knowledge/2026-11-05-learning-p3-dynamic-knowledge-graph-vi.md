# Eris Learning — P3: Dynamic Knowledge Graph (05/11/2026)

## Mục tiêu

Không cố gắng biến Java Dependency Injection thành giao diện đa môn bằng cách nới giới hạn của pack cũ. Tách **bản đồ khám phá linh hoạt** khỏi **bài học/bài kiểm tra có bằng chứng**.

- Giữ nguyên `java-di.json`, CanvasFocusStore, đánh giá Java, replay và dữ liệu mastery P0–P2.
- Tạo chủ đề mới từ web, thêm các nhánh dưới bất kỳ node nào, chuyển qua nhiều chủ đề, tồn tại sau khởi động lại.
- Root/Eris có thể đề xuất nhiều nhánh qua Canvas capability; những nhánh đó hiển thị là **Eris đề xuất**, không phải kiến thức được xác minh hay bằng chứng người học đã làm được.
- Cầu nối Godot chỉ chuyển yêu cầu đã được whitelist từ web đến Python, không tự xử lý graph, không bắt buộc phải có Java.

## Luồng P3

```text
Người dùng -> Web Learning Canvas
    | tạo topic, chọn graph, mở nhánh
    v
Godot HTTP /api/canvas/request (same-origin/token)
    | action graph_create/graph_select/graph_expand
    v
UdpListener.WebLearningCanvas.cs (whitelist, giới hạn kích thước)
    v
LearningCanvasService.handle
    v
GraphWorkspace -> LearningStore.schema_meta (revision CAS)
    | trả graph_workspace + Java snapshot v1
    v
Godot bridge -> /api/canvas/state -> Web Canvas dựng node/đường nối
    |
    +-- phát sự kiện tutor_result (UI -> Root) cho thao tác khám phá

Root -> CanvasCapability(graph_create/graph_expand/graph_select)
    -> GraphWorkspace -> state_packet -> Web Canvas
```

## Tính chất dữ liệu

- Metadata: `learning_canvas_graphs_p3_v1`, schema `eris.learning.graph.p3`.
- `selected_group_id`: chủ đề web đang hiển thị; dùng `java-di` để quay lại bài học cũ.
- `groups[]`: mỗi chủ đề chứa `group_id`, `title`, `selected_node_id`, danh sách `nodes`.
- Node có `id`, `parent_id`, `title`, `summary`, `origin = user | eris_proposed`.
- Parent luôn là một node đã tồn tại; ID bất biến khi mở rộng; tránh chu trình ở lớp đồ thị. Tên nhánh phải duy nhất trong cùng một node cha (so sánh Unicode NFKC, không phân biệt hoa/thường).
- Revision lưu với CAS SQLite, xung đột trả `stale_graph_revision` thay vì ghi đè.
- `graph_expand` từ web chỉ thêm một nhánh; Root có thể thêm tối đa 8 nhánh/lượt; đã kiểm tra chữ, ID, dung lượng và parent trước khi commit.
- Giới hạn an toàn ban đầu: 24 bản đồ, 64 nút/bản đồ, 35 KB dữ liệu graph/bản đồ, phản hồi Canvas dưới 60 KB. Có thể nâng bằng paging/snapshot delta ở P4 khi cần.
- **Tuyệt đối không tự chuyển node khám phá thành LearningPath/AssessmentAttempt/Mastery.** Việc kiểm chứng và đăng ký giáo trình là bước riêng có xét nguồn/bằng chứng.

## Thao tác

Tại Learning Canvas: nhập chủ đề mới, bấm **Tạo bản đồ**. Chọn node gốc, nhập **Tên nhánh con** và **Ghi chú**, bấm **Thêm nhánh**. Chọn **Thư viện chủ đề** để chuyển qua lại, gồm bài Java DI cũ. Người dùng đang gõ không bị mất nháp khi trang polling.

Root capability:

- `{operation:"library"}`: liệt kê nhóm Java + nhóm khám phá.
- `{operation:"graph_create",title:"Spring Boot"}`: tạo bản đồ chưa tự giành focus của người dùng.
- `{operation:"graph_expand",group_id:"...",parent_id:"...",nodes:[{title:"IoC",summary:"..."},{title:"DI"}]}`: ghi nhánh dự thảo do Eris đề xuất, không tự chuyển giao diện sang chủ đề khác.
- `{operation:"graph_select",group_id:"...",node_id:"..."}`: đổi chủ đề/nút chỉ khi cần; giữ độc lập với Java focus revision.

## Ranh giới P3 / P4 / P5

P3 đã có **đồ thị khám phá động + chuyển chủ đề + lưu nhánh + giao thức Root**. P3 **không** hứa rằng model sẽ tự sinh lộ trình chất lượng cao mỗi lần người dùng nhập tên một môn: Root có công cụ để đề xuất, còn có thực thi hay không phụ thuộc vào quyết định của Root. P3 **không** chấm điểm các node khám phá và chưa dựng bài học/biểu đồ/code sandbox theo từng môn. Tính năng đó thuộc P4 (Dynamic Workspace) và chủ động định hướng học sâu hơn thuộc P5.

## Test

Chạy từ gốc `eris/`:

```bash
python -m pytest -q src/tests/test_learning_canvas_p3_graph.py src/tests/test_learning_canvas.py src/tests/test_learning_canvas_exercises.py src/tests/test_web_learning_canvas_migration.py src/tests/conversation/test_canvas_root_bridge.py src/tests/conversation/test_canvas_focus_lifecycle.py src/tests/conversation/test_learning_canvas_full_loop.py
node --check src/body/richchat/canvas.js
```

Chưa xác nhận build Godot/.NET trên Windows trong môi trường hiện tại, do `dotnet`/`godot` không có trong môi trường test. Dùng `build_dev_fast.bat` tại máy Windows để kiểm chứng cầu nối C# và chạy UI end-to-end.

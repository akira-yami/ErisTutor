# Gemma trong Eris: đường chạy, tác dụng và bằng chứng

**Trạng thái sau audit:** cùng ngày 23/10, user đã yêu cầu bỏ toàn bộ
Gemini/Gemma/Groq khỏi runtime. Các đường model/fallback mô tả dưới đây là
trạng thái trước đợt dọn đó; hiện residue dùng deterministic, key cũ để trống.
Xem mục 17 trong [tri thức runtime](2026-10-14-eris-runtime-lessons.md).

Ngày đối chiếu: 23/10/2026. Phạm vi: source checkout hiện tại, bốn module
tương ứng trong bản portable `Documents/eris/src`, settings đã lưu của
`Documents/ErisLive`, dữ liệu residue và 41 run log tháng 10 tìm được trong
`Documents/eris/logs`. Không khởi động Eris hoặc gọi thêm API trong lượt audit này.

## Kết luận

Gemma được cấu hình làm **private residue curator**: biên tập các mảnh cognition
đã tồn tại thành mục nhật ký hoặc vật liệu continuity. Nó có client riêng dùng
Gemini API và chung pool key Gemini, model mặc định `gemma-4-31b-it`.
Không có quyền trực tiếp phát speech, chạy tool, apply identity hoặc thay Root
quyết định lượt nói qua đường curator này.

Cần thu hẹp câu trả lời trước rằng Gemma “đang gom nội tâm”: đó là phân công
trong source/config, chưa có receipt chứng minh generation Gemma đã thành công
trong phiên gần nhất. Có client được khởi tạo không đồng nghĩa có request.
Hai đường đầu ra còn có giới hạn quan trọng: continuity không đi vào prompt
private-mind hiện tại, và panel ai_orchestrated dùng projection AI state riêng.

## 1. Khởi tạo và phân quyền

- `src/vision.py:165`: dựng residue client nếu flag curator bật.
- `src/llm/process_manager.py:505`: provider mặc định Gemini; model lấy override
  `ERIS_PRIVATE_RESIDUE_CURATOR_MODEL`, nếu không có dùng `DEFAULT_OPEN_MODEL_ID`.
- `src/llm/model_catalog.py:9`: mặc định `gemma-4-31b-it`.
- `src/runtime/private_mind_model_adapter.py:1673`: residue adapter chọn riêng
  `residue_curator_client`, `strict_client_attr=True`; không tự mượn inner/main
  khi client này thiếu trong service.
- Fragment evaluators và synthesis dùng các adapter khác, ưu tiên DeepSeek
  inner worker trong cấu hình hiện tại. Tên class “LocalModel” và comment
  “Gemma Thought-Shards” còn sót không chứng minh Gemma phụ trách chúng.

Settings ErisLive lưu `architecture.mode=ai_orchestrated`,
`private_residue_curator_enabled=true`; legacy model lanes tắt.

## 2. Dữ liệu Gemma nhận

`PrivateMindLoop._capture_tick_residue` giữ lại:

1. Claim của fragment evaluators, kèm evidence/source IDs.
2. Dominant judgment của inner synthesis.
3. Ý định nói riêng bị SurfaceValve chặn.

Ngoài ra các caller có thể ghi candidate public bị suppress, expression urge
hoặc stimulus bị coalesce/busy/preempt. Stimulus chưa xử lý mang provenance
riêng `unprocessed_stimulus`, không được coi là suy nghĩ đã xảy ra.

Kho gộp bản trùng theo kind/text/provenance, tăng recurrence và giữ salience
lớn nhất. Packet gửi Gemma gồm tối đa 24 item ưu tiên salience/recency; mỗi item
có id, kind, text tối đa 420 ký tự, salience, recurrence, provenance, lane_hint
và tối đa 12 source_ids. Curator không nhận toàn bộ hội thoại hoặc ảnh màn hình
qua packet này. Nội dung residue vẫn có thể bắt nguồn từ lời user hoặc màn hình.

Nguồn: `private_mind_loop.py:522`, `residue_curator.py:122`, `:214`.

## 3. Khi nào gọi

Sau một private-mind tick hoàn tất, loop spawn công việc
`private_residue_curator` khi background flag bật; một tên công việc chỉ có
một thread đang active. Không gọi Gemma chỉ vì client đã được tạo.

Kho chỉ yêu cầu curate khi có pending item và thỏa ít nhất một điều kiện:

- Có từ 6 pending item theo mặc định;
- Có item salience >= 0.75;
- Đã ít nhất 600 giây từ lần consolidate trước.

**600 giây không phải cooldown cứng**: hai điều kiện đầu được xét trước.
Probe synthetic xác nhận sau curate 1 giây, một item salience 0.9 đã làm
`should_curate=True` trở lại. Còn bị giới hạn bởi việc private-mind có được tick,
thread trước đã xong, và broker có cho optional request chạy hay không.

`.env` hiện có hai dòng interval private-mind 120/180; parser lấy dòng cuối,
nên giá trị bootstrap là 180 giây. Đây là interval tick private-mind, không phải
cam kết cứ 180 giây có một request Gemma. Loop còn xét delta/idle, provider
pressure và user turn đang active. Context curator là optional, có cancel scope
`background:private_residue`, coalesce key `private_residue:latest_batch` và
budget background bootstrap 60 giây. Không suy budget thành latency đã đo.

Nguồn: `residue_curator.py:200`, `private_mind_loop.py:563`, `:584`, `:195`.

## 4. Gemma trả gì và ảnh hưởng đến đâu

Prompt yêu cầu gom trùng, bỏ noise, nén trung thành; mỗi decision tham chiếu
residue IDs thật và chọn một trong ba action:

| Action | Tác động trực tiếp |
|---|---|
| `diary` | Lưu entry loại current/reflection/concern/future/desire |
| `continuity` | Giữ vật liệu để có thể xét ở cognition sau |
| `discard` | Đánh dấu nguồn là discarded |

Prompt yêu cầu tối đa 6 decision; adapter chấp nhận tối đa 12. Budget trả lời
mặc định 700 token. Với model Gemma, transport gập system instruction vào text
user, bỏ schema/MIME controlled generation và parse JSON từ text trả về.
Không nên coi có schema trong adapter là API đã cưỡng chế output theo schema.

Entry/continuity được lưu bền vững trong `inner_diary_residue.json`, còn source
được đánh dấu curated/discarded. Mặc định entry active 6 giờ, fading tới 24 giờ,
sau đó archived; giới hạn kho 96 source item, 64 entry và 64 continuity item.
Các giới hạn này là retention của spool, không phải cơ chế học trọng số model.

Renderer diary legacy đọc các entry theo kind, bổ sung/fallback cho các ô suy
nghĩ hiện tại, tương lai, bận tâm, phân tích, muốn nói. Việc viết một entry
`desire` không tự enqueue phát ngôn mới từ curator.

Nguồn: adapter `:581`, `:604`, `:627`; client `:1386`, `:1455`;
store `:239`, `:358`; renderer `:287`.

## 5. Hai giới hạn downstream đã xác nhận

### Continuity có trong dossier, chưa có trong prompt model

`private_mind_loop.py:300` đặt `residue_continuity` vào dossier.metadata.
Các builder prompt fragment JSON/freetext và synthesis chỉ lấy
`recent_thoughts`, và synthesis còn lấy `unresolved_threads`; chúng không lấy
`residue_continuity`. Hai store thought continuity và residue continuity khác nhau.

Probe offline đặt một sentinel duy nhất vào residue_continuity và sentinel
đối chứng vào recent_thoughts. Cả 10 prompt fragment (5 evaluator x 2 format)
và 1 prompt synthesis đều bỏ sentinel residue; cả 11 giữ sentinel recent_thoughts.
Đây là bằng chứng missing propagation vào các prompt đó, không chứng minh mọi
consumer tương lai đều không thể dùng file residue.

### Panel ai_orchestrated có nguồn dữ liệu khác

`public_output_runtime.py:255` chọn `build_ai_panel_payload` khi AI runtime bật.
`ai_orchestrated/panel_adapter.py:900` dựng private_mind_digest từ AI state,
identity, observation, intention và open loops; không nhận residue store làm
đầu vào của nhánh này. Panel legacy mới render retained diary từ residue.
Vì settings chọn ai_orchestrated, không thể nhìn panel nội tâm rồi tự quy chữ
ở đó là do Gemma viết. Chưa bật app để xác minh bản UI đang hiển thị trong phiên mới.

## 6. Kiểm nguồn và giới hạn bảo vệ

Guard trong `apply_curator_output` kiểm residue IDs tồn tại, action hợp lệ,
và ít nhất một nguồn cognition thật trước khi nhận action diary. Nếu mọi nguồn
đều chưa xử lý, action diary bị hạ thành continuity và dùng lại text gốc.
Test hiện có xác nhận trường hợp này.

Guard **chưa kiểm semantic entailment giữa text mới và nguồn**. Probe synthetic:
nguồn thật có nội dung “waiting quietly”, curator giả trả nhận định không liên
quan “the user bought a red car” cùng ID hợp lệ; entry vẫn được nhận. Không dùng
kết quả này để nói Gemma đã hallucinate live. Nó chứng minh source ID hợp lệ
không bảo đảm bản nén trung thành, nhất là batch trộn nguồn actual/unprocessed.

Khi thiếu client, model lỗi hoặc không có entry hợp lệ được áp dụng, runtime
fallback deterministic: giữ tối đa bốn mảnh ngắn từ packet, cognition thật thành
diary, stimulus chưa xử lý thành continuity. Do đó nhật ký có nội dung không tự
chứng minh Gemma hoạt động. Fallback còn ghi đè last_curator bằng deterministic,
nên snapshot cuối không đủ truy ra nguyên nhân remote thất bại trước đó.

Nguồn: `residue_curator.py:239`, `:323`; loop `:584`.

## 7. Bằng chứng chạy thật tìm được

- Quét 41 run log tháng 10 trong portable thấy 41 dòng builder Gemma ready. Hai log
  mới nhất đã đọc chi tiết là `eris_run_20261018_112454.txt` và
  `eris_run_20261018_104609.txt`; model là `gemma-4-31b-it`.
- Không thấy diagnostic `[Residue Curator]`/`residue.curator` trong 41 file.
  Thiếu diagnostic không đủ chứng minh không có request hoặc request thành công.
- File `ErisLive/memory/core/inner_diary_residue.json` tìm được là dữ liệu cũ:
  embedded updated_at và mtime cùng ngày 20/08/2026 UTC. Nó có 96 pending source
  item, 64 entry đều ghi curator `deterministic`, 5 continuity item; last_curator
  cũng deterministic. Không áp trạng thái snapshot tháng 8 cho phiên tháng 10.
- SHA-256 trùng giữa checkout và portable cho private_mind_loop,
  private_mind_model_adapter, residue_curator và process_manager tại lúc audit.
  Điều này không chứng minh process đang chạy đã import đúng bốn file đó.
- Lượt kiểm key trước audit chỉ gọi model Gemini text, không gọi Gemma curator;
  không dùng “key sống” làm bằng chứng Gemma đã xử lý nội tâm.

## 8. Kiểm chứng và việc chưa làm

`python -m pytest src/tests/test_v79_inner_diary_residue_curator.py -q -p no:cacheprovider`:
**7 passed**. Đây là test offline với client giả, không kiểm quality/latency của
Gemma live. Ba probe synthetic xác nhận mất propagation vào 11 prompt, thiếu
kiểm nội dung bản nén và điều kiện salience vượt interval.

Chưa sửa runtime/config/model, chưa build/restart, chưa tạo request Gemma mới.
Muốn đo giá trị thực của curator cần receipt gắn source batch -> request/reply ->
accepted entry -> consumer đọc entry/continuity, cùng so sánh chất lượng bản nén
với deterministic. Audit này chưa cung cấp chuỗi receipt live đó.

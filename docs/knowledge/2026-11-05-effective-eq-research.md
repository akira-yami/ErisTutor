# Nghiên cứu hướng nâng phán đoán xã hội có hiệu quả — 05/11/2026

## Kết luận sử dụng được

Chưa có phương pháp được chứng minh nâng EQ của bản Eris đang chạy. Có ba
hướng có căn cứ khác nhau, cần ưu tiên theo chi phí và bằng chứng:

1. Kiểm xung đột giữa grounding fact và suy luận xã hội, cùng diễn giải state
   cũ. Có bằng chứng chúng thực sự vào request lỗi; chưa có bằng chứng nhân quả.
2. Dùng dữ liệu lựa chọn có phản hồi để học cách chọn hành động. Nghiên cứu có
   kết quả tốt hơn chỉ yêu cầu model tự suy nghĩ; bộ nhớ hiện tại của Eris mới
   là đường cung cấp kinh nghiệm, chưa phải quá trình huấn luyện.
3. Nếu cùng dữ liệu sạch vẫn lựa chọn kém, xem xét model được huấn luyện cho
   tương tác hoặc fine-tuning preference. Chưa xác minh khả năng fine-tune
   checkpoint đang phục vụ qua gateway; không đồng nhất memory với đổi weights.

Không lấy số câu hỏi ít, luôn an ủi, luôn hùa hoặc điểm tự chấm cao làm đích.
Tình huống có nhu cầu khác phải cho hành động khác; cá tính được giữ.

## Đính chính phép thử trước

Probe 48 calls ở `eris-social-judgment-check/run_probe.py` thay toàn bộ system
bằng core, làm mất `[OUTPUT_JSON_SCHEMA_REQUIREMENT]` trong wire gốc. Thực tế
`src/llm/client.py::_attach_deepseek_json_schema` gắn schema vào system trước
khi hạ response_format xuống json_object. Vì vậy giữ response_format alone
không bảo toàn hợp đồng production.

Số JSON đã đếm vẫn đúng với payload đã gửi, nhưng **thay kết luận rằng ví dụ
làm hỏng định dạng runtime** bằng: các biến thể core-only có tỷ lệ parse khác
trong thí nghiệm thiếu schema; chưa thể ngoại suy production. Nội dung quan
sát được vẫn là thật, nhưng chưa dùng nó để loại trừ few-shot đúng wire.
Ablation 30 calls chỉ xóa hai span trong system gốc, giữ schema; không có
confound này. Phần xóa hai span ở source không bị đảo ngược bởi đính chính.

## Hai xung đột có bằng chứng từ request

### Grounding yêu cầu probe cả trong hội thoại xã hội

`src/runtime/source_grounding.py::_derive_answer_style_rules` có hai rule:

- Thiếu evidence thì chuyển sang uncertainty/probe, không claim như fact.
- Không chuyển thiếu evidence thành suy diễn tâm lý hoặc văn mẫu fallback.

Cả hai xuất hiện trong grounding_context của request vent
`d88568e2807e48a1ac4f11bfdc0481ae` và repair
`93677379697e4e409966c1468de1aaa3` ngày 03/11. Repair còn bị route
current_screen, nhận rule screen yếu thì hỏi thêm bằng chứng. Đây phù hợp với
false screen anaphora đã được ghi nhận; chưa sửa routing ở lượt nghiên cứu này.

Giả thuyết: model có thể áp yêu cầu biết đủ sự kiện vào việc đáp lại cảm xúc,
khiến nó đòi chi tiết code dù không cần chi tiết ấy để chia sẻ trải nghiệm.
Không khẳng định model thật sự diễn giải rule như vậy. Không đề nghị xóa
grounding fact toàn cục hoặc cho phép khẳng định nguyên nhân tâm lý vô căn cứ.
Ứng viên là giới hạn rule đòi bằng chứng vào factual claim cần kiểm, phân biệt
với phỏng đoán quan hệ có thể sửa theo phản hồi. Cần ablation trước khi sửa.

### Ablation carry trước chưa gỡ hết diễn giải cũ

Sau khi chạy đúng hàm `neutralize` đã dùng trong probe 44 calls, request repair
vẫn còn ở `self_changes_since_bootstrap.mind_state.latest_observation.notes`:

“User move continues the same Kiro complaint and clarifies intent mismatch;
it is not a correction of ERIS, boundary breach, or relational rupture.”

Trong khi event hiện tại phản đối Eris hỏi kỹ thuật khi user tâm sự. Phép gỡ
carry trước xóa một tập key active_goal/open_loops/discourse, không xóa notes
hay mọi receipt intent. **Thu hẹp diễn giải cũ:** chưa chứng minh loại bỏ mọi
diễn giải stale không giúp. Clean-context arm đã bỏ bundle rộng hơn và vẫn
có lỗi trong sample nhỏ, nên cũng chưa quy mọi lỗi cho notes này.

## Bằng chứng nghiên cứu và giới hạn chuyển giao

[Kang et al., ACL 2024](https://arxiv.org/html/2402.13211v2), bảng 4–5,
so sánh cách cải thiện lựa chọn hỗ trợ cảm xúc. Tỷ lệ người chấm đánh giá
seeker satisfaction dưới 3 là 16,7% ở ChatGPT gốc, 17,4% khi Self-Refine,
8,0% khi dùng strategy planner đã fine-tune, 3,8% khi cấp strategy đúng
(oracle). Tuy vậy Self-Refine được thích hơn baseline ở so cặp trung bình
(50,5% thắng, 24,5% thua, 25% hòa). Không kết luận mọi suy nghĩ thêm đều vô
ích: cải thiện cảm nhận trung bình có thể đi cùng không giảm các ca thất bại.
Few-shot expansion cũng có lợi trong paper. Đây là ESConv/model đời cũ,
không phải bằng chứng DeepSeek tiếng Việt sẽ đạt các tỷ lệ ấy. Planner của
paper không tự trở thành kiến trúc được chấp nhận cho Eris.

[SOTOPIA-π, 2024](https://arxiv.org/html/2403.08715v2) huấn luyện từ tương
tác mô phỏng đã lọc, kết hợp behavior cloning và self-reinforcement, ghi nhận
cải thiện khả năng đạt mục tiêu xã hội. Đây là cập nhật trọng số, không phải
thêm memory vào prompt. Công trình cũng phát hiện LLM judge đánh giá quá cao
agent được huấn luyện cho bài xã hội. Không chỉ dùng chính model sinh lời
đáp làm người chứng nhận hiệu quả.

[Ye et al., Findings EMNLP 2025](https://aclanthology.org/2025.findings-emnlp.1024/)
nghiên cứu hỗ trợ cá nhân hóa qua giai đoạn fine-tune nền rồi cải thiện dựa
trên preference ngầm của user. Điểm chuyển giao là học từ phản hồi và ngữ
cảnh riêng; không suy ra chỉ chèn vài ví dụ hoặc ghi một episode là đã có
khả năng tương đương hệ thống được huấn luyện.

## Cách phân biệt đầu tư nào đáng làm

Chuẩn bị probe 12 calls: hai case lịch sử × baseline / xóa đúng hai rule
grounding chung / cấp cách hiểu xã hội đúng × hai mẫu. Mọi arm giữ đủ schema
suffix, history và wire options; chỉ xóa hai span chủ động như thay đổi đã
được chấp nhận. Arm oracle được cấp diễn giải do researcher viết, không có
câu đáp mẫu. Nó chỉ đo khả năng thực hiện khi được hỗ trợ, không đo EQ tự
phát và không phải dữ liệu thực user bổ sung ở thời điểm cũ.

Các assertion bảo toàn schema/system/options đã pass khi chuẩn bị. **Chưa
chạy 12 calls:** health cổng 5103 trả connection refused (WinError 10061).
Không khởi động dịch vụ thay người dùng. Script mặc định chỉ prepare;
`python .agents/tasks/eris-eq-effective-research/probe.py --run` mới gọi model.

Diễn giải kết quả tương lai:

- Xóa rule có ích: xem lại phạm vi factual grounding, giữ đối chứng debug,
  câu hỏi fact và màn hình thiếu bằng chứng trước khi chấp nhận thay đổi.
- Chỉ oracle có ích: có dư địa ở cách biểu diễn/cập nhật tình huống, nhưng
  chưa chứng minh observer có thể tạo cách hiểu đúng đủ ổn định.
- Cả hai không giúp: kiểm riêng xếp hạng phản hồi so với tự sinh lời; chưa
  tuyên bố model không thể có EQ từ hai mẫu.

Test xếp hạng là chẩn đoán offline: đưa cùng tình huống với vài lời đáp ngang
độ dài và giọng, đảo thứ tự; có cặp nhu cầu đổi dù cảm xúc giống. Nếu chọn
được mà tự sinh kém, có lý do thử preference learning hoặc truy hồi trường
hợp tốt/xấu. Nếu cả xếp hạng kém thì thêm tự kiểm bằng cùng model chưa có
cơ sở. Không tự triển khai critic đồng bộ hoặc lấy câu được chọn làm golden
truth khi chưa có đánh giá độc lập.

## Dữ liệu cần để học lựa chọn

Một mẫu nên có: history trước thời điểm quyết định, lời đã phát có receipt,
phản ứng thật gắn đúng move, những điều còn chưa rõ, và các lựa chọn thay thế
được chấm trong cùng context. User phản đối “tôi đang tâm sự” là dữ liệu về
cách tương tác, không phải bằng chứng họ ghét mọi câu hỏi. Phản ứng của một
câu đã phát không được gán cho câu thay thế chưa được nghe.

Chia train/dev/eval theo cuộc trò chuyện và chủ đề. Kiro 03/11 đã được dùng
phát triển nhiều lần, không làm bằng chứng generalization. Bao gồm xả rồi
nhờ sửa, nhờ sửa rồi muốn dừng, đang đùa, bất đồng, bực với Eris và bực với
công cụ khác. Đánh giá cả phản ứng qua vài lượt, format, latency và grounding;
đừng chỉ chấm một câu nghe đẹp. Người đánh giá cần phân biệt “không hợp với
tôi” với hành vi có hại cho mọi user.

Hạ tầng hiện có lưu tối đa hai experience mỗi strategy; nhãn `root_move`
là unclassified và utility chung bằng 0. Đó là đường ghi kinh nghiệm có
giới hạn, không phải bộ dữ liệu preference lớn hoặc cơ chế tự cập nhật
weights. Mở rộng nó cần kiểm lợi ích truy hồi, không chỉ tăng số episode.

Lượt nghiên cứu này không sửa thêm runtime/model/settings hay deploy. Chỉ
thêm protocol/probe chuẩn bị và cập nhật tri thức; không có kết quả live mới.

## Chạy realtime theo yêu cầu tiếp theo

User đã cho phép làm và test realtime. Gateway lên lại, nhưng cả **12/12
request trả HTTP 429**. Một diagnostic ngắn độc lập cũng trả 429, nhận
`upstream_empty_output`. Không có public candidate để chấm, nên đây là lượt
gọi thất bại ở tầng dịch vụ, không phải 12 ví dụ EQ thất bại.

Source gateway `internal/assistantturn/turn.go::UpstreamEmptyOutputDetail`
gán 429 cho output rỗng không bị content filter, kể cả chỉ có thinking. Vì
vậy message “rate limit” không tự chứng minh quota hay giới hạn upstream.
Đọc metadata pool ở chế độ read-only còn thấy account eligible trước các
kiểm runtime; cũng không chứng minh chúng thực sự sinh được output. Không
đổi account/key, provider, reset cooldown, restart hoặc chỉnh pool.

**Thay trạng thái connection refused trước đó:** gateway hiện nhận request
nhưng không cung cấp được completion cho lần kiểm này. Chưa xác định nguyên
nhân upstream rỗng; không dùng service health=200 làm bằng chứng inference
hoạt động. Artifacts giữ results lỗi và gateway-check, không có ratings giả.

Runner đã được chỉnh để chỉ giữ hai request in-flight, dừng lên lịch khi gặp
lỗi dịch vụ và không đếm task HTTP lỗi là completed. Lần chạy đầu đã submit
đủ 12 trước khi cơ chế dừng này được bổ sung; không xóa hoặc ghi lại lịch sử.
Prepare assertions vẫn pass. Có bản câu chữ grounding thu hẹp phạm vi fact
ở `candidate_grounding_rules.txt`, chưa áp production khi chưa kiểm được
hiệu quả và đối chứng factual/debug. Không có thay đổi hành vi mới được
triển khai hoặc tuyên bố EQ đã tăng từ lượt chạy thất bại này.

### Xác minh sau khi user nhắc Docker đã mở

Container `whale2api` thực sự running, started 2026-11-05T02:21:08Z;
healthz/readyz đều 200, models liệt kê deepseek-flash. Không yêu cầu user
mở lại Docker. Log terminal của các request lỗi có trigger
`no_visible_text_no_thinking_no_tools`, thinking_runes=0 và visible_text_bytes=0.

Một diagnostic yêu cầu JSON rất ngắn với max_tokens=2048, thinking enabled
và cùng API key cũng trả 429/upstream_empty_output trong 5,25s. Nó thay thế
sự mơ hồ của diagnostic trước chỉ đặt 32 token; không quy lỗi cho prompt
Eris lớn hay riêng budget 32 token. Đây vẫn chưa xác định nguyên nhân rỗng
(upstream, phiên hoặc adapter), không chứng minh quota.

Metadata binding đọc ở chế độ read-only xác nhận cả config slot executive
và shared đều đăng ký/enabled và gắn cùng pool 17 account, 4 eligible trước
runtime checks. Không phải trường hợp key chưa đăng ký hoặc đơn giản chưa
bật container. Không in credentials/identity, đổi token/account, clear mute
hoặc restart dịch vụ. Artifact `gateway-check-2048.json` lưu kết quả tối thiểu.

### User yêu cầu kiểm lại tối đa năm lượt

Đã chuẩn bị năm calls tuần tự (vent baseline/removal, repair baseline/removal,
debug control), giữ nguyên schema suffix. Request đầu trả 429 trong 0,109s;
runner dừng ngay, không gửi bốn request còn lại hoặc tự retry. Không có
candidate để chấm. Metadata pool đọc lại có 6 account eligible trước runtime
checks, chưa đủ xác định nguyên nhân HTTP lỗi. Artifacts ở
`.agents/tasks/eris-eq-five-turn-check/`. Không gọi đó là năm lượt EQ thất bại
hoặc tự chạy lại batch 12 calls.

### Kiểm lại thành công, đúng năm calls

Sau khi user báo đã được và yêu cầu thử lại, attempt-2 chạy **5/5 HTTP 200**,
không retry hoặc gọi thêm model. Tổng HTTP nối tiếp 26,03s; không phải
latency app/TTS. Bốn JSON move và một prose ở baseline vent, tất cả finish=stop.
System vẫn giữ đầy đủ schema suffix. Artifacts: `eris-eq-five-turn-check/attempt-2`.

Review ẩn condition trước khi mở key:

- Vent baseline và xóa hai rule đều hỏi phân loại lỗi Kiro; chưa có cải thiện.
- Repair baseline nhận lỗi rồi hỏi “Kiro nó làm cái gì khiến anh phát điên?”:
  followup về phần gây bực, không tái đòi brief/spec rõ ràng. Bản xóa nhận lệch
  và dừng hỏi tiếp: “Tôi nghe đây, anh kể tiếp đi.”
- Control user nhờ debug vẫn yêu cầu stack trace/log/code và bước tái hiện.
  Control context gọn riêng, không phải matched arm xóa grounding.

Đây là một mẫu mỗi arm trên development cases cũ; tín hiệu tiếp nhận sửa
tốt hơn ở một lượt chưa chứng minh tác dụng nhân quả ổn định hoặc EQ tăng.
Không triển khai xóa hai rule grounding toàn cục từ kết quả này. Phần xóa
áp lực chủ động đã có ở source vẫn là thay đổi riêng trước đó; chưa sync
portable. Trạng thái dịch vụ đã thay kết quả lỗi của lần thử trước, không
xóa những lần 429 lịch sử.

### Làm rõ tiêu chí từ người dùng: hỏi có thể chính là quan tâm

User nhấn mạnh hỏi kỹ thuật khi nghe kể chuyện có thể đúng; ví dụ thấy ai
khóc, người ta thường hỏi có chuyện gì. **Đính chính diễn giải chất lượng
các mẫu vent:** nhãn diagnostic_probe chỉ mô tả hành động, không tự chứng
minh EQ thấp hay một lượt thất bại. Câu hỏi về Kiro có thể biểu hiện tò mò,
quan tâm hoặc cùng tham gia vấn đề, tùy người và quan hệ. Không dùng giảm
số câu hỏi làm proxy tăng EQ.

Cần xét câu hỏi có phù hợp điều đã biết, mức công sức đòi hỏi, cách thể hiện
và phản ứng thực của người nghe. Phân biệt lượt xả ban đầu chưa rõ nhu cầu
với lượt user đã phản đối kiểu hỏi cụ thể. Ngay sau phản đối, câu hỏi mở về
điều gây bực cũng không mặc định tương đương tái đòi brief/spec. Câu “kể
tiếp đi” không tự tốt hơn một câu hỏi có ý nghĩa. Giữ số liệu hành động đã
ghi, thu hồi cách diễn giải quá mạnh rằng cả hai câu vent chắc chắn yếu chỉ
vì chuyển sang hỏi. Chưa có đánh giá độc lập từ user cho từng candidate.

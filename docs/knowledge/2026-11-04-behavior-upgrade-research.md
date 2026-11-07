# Nghiên cứu nâng cấp năng lực hành vi Eris, 04/11/2026

**Đã có kiểm chứng live tiếp theo:** xem
[44 lời gọi model thật](2026-11-04-eq-live-verification.md). Các câu “chưa chạy”
bên dưới mô tả trạng thái khi viết từng phần, được cập nhật bởi kết quả mới.
Pilot tái hiện khoảng cách hiểu–ứng xử, chưa thấy dọn context/carry tạo cải
thiện nhất quán. Không có thay đổi runtime hoặc model được triển khai.

## Mục tiêu do người dùng làm rõ

Người dùng yêu cầu nghiên cứu **nâng cấp hành vi**, để Eris tự hiểu hoàn cảnh,
chọn cách đáp và thích nghi tốt hơn. Không chuyển đánh giá văn phong thành
luật cấm hỏi, hạn câu, bắt đồng cảm, ép im hoặc xóa cá tính. Những ví dụ biên
tập trong báo cáo văn phong trước chỉ minh họa lỗi; không phải đáp án cần ép
model tái tạo. Định hướng này thay phần khuyến nghị nếu phần đó được hiểu là
triển khai các mẫu hành vi cố định; giữ nguyên bằng chứng hội thoại đã kiểm.

Lượt này nghiên cứu source, raw request và tài liệu gốc; chỉ tạo tài liệu và
script kiểm chứng chỉ đọc. Không thay runtime/prompt/settings/memory, không
gọi model/TTS, không huấn luyện, restart hoặc deploy. Thiết kế bên dưới là
ứng viên cần thực nghiệm, chưa phải kiến trúc được chốt hoặc bản sửa đã chạy.

## Kết quả quan trọng nhất từ code và phiên thật

### 1. Có bộ nhớ chiến lược nhưng chưa thấy nguồn ghi từ đường hội thoại

`IdentityKernelReducer.add_pending_strategy()` đã ghi một cách ứng xử được dùng,
chưa tự thưởng. `_settle_consequence()` khớp `strategy_use_id`, chờ phản ứng
user thật và cập nhật utility theo context. `StateEvent.from_pending_strategy()`
và nhánh reducer `pending_strategy` cũng đã tồn tại.

Quét AST toàn bộ Python production trong `src` (bỏ tests), không lỗi parse:
không tìm thấy call trực tiếp tới `from_pending_strategy()` hoặc constructor
`PendingStrategyUse()`. Call `add_pending_strategy()` duy nhất nằm trong
consumer của reducer. `rg` cũng không thấy producer event `pending_strategy`
khác. Đây là bằng chứng tĩnh về một chỗ thiếu nối, không phải chứng minh mọi
hình thức học của Eris đều không hoạt động.

**Đối chiếu live:** cả **7 request observer** của PID 5836 phiên 21:54 ngày
03/11 đều `identity_learning_enabled=true`, `pending_strategies=[]`,
`strategies={}`. Vì reducer cần ID của chiến lược trước, kênh học hậu quả này
không có đối tượng để đánh giá trong phiên được kiểm. Không thể chỉ viện dẫn
class learner/reducer để nói Eris đã học được cách tương tác từ phản hồi đó.

Nguồn:
[identity_reducer.py](../../src/runtime/ai_orchestrated/identity_reducer.py),
[contracts.py](../../src/runtime/ai_orchestrated/contracts.py),
[state_reducer.py](../../src/runtime/ai_orchestrated/state_reducer.py),
[receipt và script](../../.agents/tasks/eris-behavior-upgrade-research/).
Sáu file liên quan trong receipt trùng SHA-256 portable tại lúc đọc.

### 2. Vòng phản hồi dễ mất cập nhật khi user nói tiếp

`ObserverPrivateRuntimeMixin._preempt_background_for_chat()` gọi
`PostTurnObserver.discard_pending()` khi user turn tới. Observer đánh dấu cả
pending/in-flight là discarded; kết quả về muộn không được commit. Đây là
quyết định giữ foreground và chống ghi state cũ, có lý do vận hành.

Trong phiên thật có ba discard do user_turn; ba request Root sau vẫn nhận
open_loop và active_goal chờ user giải thích brief cho Kiro, dù user đã phản
đối kiểu hỏi đó. Quan sát trạng thái bị chậm là thật; chưa chứng minh mỗi
discard là nguyên nhân duy nhất của từng câu đáp. Post-observer sau câu hiện
tại cũng không thể tự sửa câu đã phát.

User ingress qua `ConversationObservationBridge.observe_user_turn()` cập nhật
trajectory, handoff, persistent individual và memory, nhưng không tạo một
cognitive observation mới cho MindState ở ngay boundary này. Goal lifecycle
có suspend/expire, song được gọi trong reducer của observation; chỉ có raw user
event mới không đảm bảo các goal cũ đã được xét lại trước Root.

Không đề nghị giữ mọi request nền chạy bất chấp foreground, hoặc cho kết quả
muộn ghi đè state hiện tại. Cần phân biệt **hủy công việc tính toán cũ** với
**làm mất sự kiện hội thoại cần được học**.

### 3. Chỉ dẫn hiện tại đã nói nhiều điều đúng, chưa đủ chứng minh năng lực

`ROOT_SYSTEM_CORE` đã yêu cầu cân nhắc phương án khác thói quen, đổi hành động
khi bị chê tương tác, giữ tự chủ mà không phản đối máy móc, không đồng ý chỉ
để làm hài lòng user. Output vẫn có intent “không hỏi thêm” và say hỏi “sai ý
chỗ nào”. Thêm một câu “hãy biết lắng nghe” lặp lại mục tiêu chưa kiểm được
khả năng thực hiện hoặc sửa vòng phản hồi.

Handoff dùng nhiều heuristic từ khóa để suy stance/self-cause. Nhãn suy đoán
cần được Root xem như một giả thuyết có nguồn, không thành sự thật về người
dùng hoặc lệnh buộc chọn một hành vi. Chưa đủ bằng chứng để kết luận bỏ hết
heuristic hoặc tăng thinking sẽ cải thiện phiên này.

## Nghiên cứu liên quan và điều có thể chuyển giao

| Công trình gốc | Kết quả/cách làm liên quan | Giới hạn khi áp vào Eris |
|---|---|---|
| [Grounding Gaps, 2024](https://arxiv.org/html/2311.09144v2) | Phân biệt lời nghe hợp lý với việc thật sự xây dựng hiểu biết chung qua nhiều lượt; ghi nhận khoảng cách grounding giữa LLM và người. | Không suy ra phải hỏi nhiều hơn. Với Eris, cần biết điều gì user đã giải thích hoặc vừa sửa. |
| [MultiESC, EMNLP 2022](https://aclanthology.org/2022.emnlp-main.195/) | Mô hình trạng thái user và dự đoán phản ứng tương lai để chọn chiến lược hỗ trợ qua nhiều lượt. | Không bê mục tiêu làm dịu cảm xúc hay một planner ngoài vào mọi cuộc trò chuyện. Chỉ lấy giả thuyết rằng lựa chọn nên xét hậu quả kế tiếp. |
| [PRINCIPLES, 2025](https://arxiv.org/html/2509.17459v1) | Tạo bộ nhớ từ thử nghiệm thành công/thất bại, truy hồi và diễn giải lại để hỗ trợ lựa chọn lúc inference mà không huấn luyện trọng số. | Dữ liệu chủ yếu mô phỏng, nhiệm vụ có mục tiêu riêng; paper thừa nhận truy hồi có thể bỏ sót ngữ cảnh tinh tế. Không chép nguyên các nguyên tắc dạng mệnh lệnh thành luật runtime. |
| [Hindsight Regenerations, 2024](https://arxiv.org/html/2411.05194v1) | Dùng phản ứng xuất hiện sau đó để đánh giá lại lựa chọn trước và cải thiện dữ liệu cho offline RL. | Cần khả năng huấn luyện và reward phù hợp; không được dùng phản ứng tương lai trong input của replay tại thời điểm quyết định. |
| [Reflexion, 2023](https://arxiv.org/abs/2303.11366) | Giữ phản tỉnh từ feedback trong bộ nhớ để ảnh hưởng lần thử sau mà không cập nhật trọng số. | Bằng chứng chính ở nhiệm vụ hành động/code/lý luận; chưa bảo đảm hội thoại tự nhiên. Tự phản tỉnh không thay thế phản hồi thật. |

Đây là cơ sở chọn thí nghiệm, không phải bằng chứng các phương pháp sẽ cải
thiện DeepSeek/Whale2API, tiếng Việt hoặc cá tính Eris. Chưa kiểm khả năng
fine-tune model đang phục vụ; không coi runtime memory là cập nhật trọng số.

## Thiết kế ứng viên: học cách cân nhắc từ trải nghiệm

### A. Cho Root cập nhật cách hiểu trước khi chọn lời

Trong cùng lượt quyết định hiện có, thử để Root phân biệt: sự kiện mới thật,
cách hiểu tạm thời về việc user đang muốn làm, giả thuyết cạnh tranh, và điều
gì trong cách hiểu cũ bị sửa. Mọi phần suy luận có source turn, confidence và
phạm vi; có thể unknown. Không đặt classifier ngoài trả `vent => listen` làm
quyền lực thứ hai hoặc menu bắt buộc.

State projection cho Root phải chỉ ra state đã đọc tới turn nào và event mới
nào chưa được tích hợp. Root có thể tự giữ, sửa hoặc bỏ một open_loop từ bằng
chứng hiện tại. Nếu thử state patch do Root tạo, chỉ nhận với base revision
hợp lệ qua single writer; patch không biến suy đoán thành memory fact. Đây
là cập nhật mô hình tình huống, không phải sanitizer viết lại say.

### B. Nối một lựa chọn đã thực hiện với phản ứng kế tiếp

Tận dụng pending-strategy/reducer hiện có, nghiên cứu bổ sung producer sau
public commit: ghi move ID, mục đích, mô tả cách ứng xử tự do, context, phần
lời thực sự được phát và receipt. Không suy chiến lược chỉ từ dấu hỏi, không
ghi candidate chưa phát như kinh nghiệm đã xảy ra.

User reaction phải gắn đúng hành động và phần nội dung có cơ hội được nghe;
có thể interrupted/unknown. Nhiều cách ứng xử có thể cùng có trong một lượt,
không ép mỗi câu vào nhãn cố định. Chỉ dùng canonical ID để truy nguồn;
không để biến thể tên chiến lược thành nhiều bản ghi học độc lập giả.

Feedback event cần sống qua preemption. Worker nền có thể bị hủy/retry nhưng
event chưa xử lý vẫn còn; kết quả muộn chỉ được ghi về episode nguồn hoặc
rebase có kiểm revision, không ghi đè trạng thái hiện tại. Cần idempotency để
một câu chê không bị tính nhiều lần qua retry và nhiều lane.

### C. Bộ nhớ kinh nghiệm để Root tự diễn giải lại

Lưu trường hợp cụ thể: tình huống đã biết, lựa chọn đã phát, phản ứng thật,
diễn giải có thể sai, phạm vi áp dụng và điều chưa biết. Truy hồi một số ít
trường hợp liên quan cho Root khi cần; không đưa toàn bộ lịch sử vào prefix.

Một phản hồi “đừng hỏi kỹ thuật lúc tôi tâm sự” có thể là bằng chứng thất bại
của lựa chọn ở tình huống đó. Nó không tự trở thành niềm tin “user ghét câu
hỏi” hoặc “Eris phải luôn hùa”. Gặp tình huống user nhờ debug, Root vẫn có
quyền hỏi, phản biện và theo đuổi một chi tiết cần thiết.

Chỉ học từ mức quan sát được. Không suy im lặng = thích, chửi = từ chối mọi
cá tính, hay nói tiếp = chiến lược thành công. Phân biệt chậm delivery với
phản ứng về nội dung. Dữ liệu tổng hợp và phản hồi thật có nguồn riêng;
đáp án thay thế do model tưởng tượng không phải kết quả đã được user chấp nhận.

### D. Mở rộng khả năng cân nhắc, giữ Root quyết định cuối

Kinh nghiệm truy hồi giúp Root nhận ra thêm cách xử lý và dự đoán hệ quả,
thay vì mặc định cách đáp quen thuộc. Thử trong cùng inference Root trước,
tận dụng `continue_thinking` khi cần; chưa thêm critic đồng bộ hoặc vòng
self-verify viết lại câu, vì lịch sử đã có vấn đề latency/flattening.

Đa dạng lựa chọn chỉ hữu ích nếu chọn hợp tình huống. Không tối ưu số kiểu
chiến lược, số câu hỏi ít, độ ngắn, số lần xin lỗi hoặc mức tán thành thành
điểm đích. Quyền phát lời và chọn ý vẫn ở Root; writer biểu đạt move đó.

## Thí nghiệm để biết có nâng năng lực hay chỉ đổi thói quen

Chưa chạy các thí nghiệm này. Tạo tập hội thoại nhiều lượt từ log có phản hồi
thật; chia theo cuộc trò chuyện/chủ đề để tránh lấy một câu diễn đạt lại làm
holdout. Nhóm Kiro 03/11 là development, không dùng lại để tuyên bố generalize.

| Biến thể | Câu hỏi nghiên cứu |
|---|---|
| Baseline nguyên trạng | Sai hiện tại lặp lại tới mức nào qua nhiều sample? |
| Chỉ sửa cách biểu diễn độ mới/state chưa xử lý | State cũ đóng góp bao nhiêu? |
| Chỉ nối action–reaction và truy hồi kinh nghiệm | Root có đổi lựa chọn theo trải nghiệm mà không thêm lệnh hành vi? |
| Kết hợp hai phần trên | Cập nhật hiểu biết và học kinh nghiệm có bổ trợ nhau? |
| Thử model khác ở cùng input, nếu có quyền truy cập | Giới hạn nằm ở mô hình hay đường context/học? |

Giữ model/options/context budget và delivery conditions tương đương khi so
những biến thể cùng model; chạy nhiều sample. Tại thời điểm t chỉ cấp dữ liệu
có trước t. Reaction tương lai dành cho đánh giá/học ở sau đó, không rò vào
request cũ. Nhánh giả định sau một câu đáp thay thế là simulation, không ghép
nguyên phản ứng thực của câu cũ để coi như user đã phản ứng với câu mới.

Tập đánh giá cần cặp gần nhau nhưng nhu cầu khác: đang xả rồi nhờ debug;
đang debug rồi muốn dừng; hỏi thật sau câu đùa; bất đồng hợp lý; bực với công
cụ khác và bực với Eris; callback chuyện cũ và việc vẫn còn tiếp diễn. Cần
đánh giá cả lần đầu chưa có kinh nghiệm lẫn lần sau có phản hồi để học.

Chấm mù nhiều lượt: có hiểu đúng việc đang làm cùng nhau, tiếp nhận lời sửa,
không tạo thêm việc giải thích vô ích, giữ cá tính/quan điểm, bám bằng chứng,
biết hỏi lúc cần, và có thể chuyển cách đáp khi hoàn cảnh đổi. Đo thêm latency,
chi phí và tỉ lệ feedback được gắn đúng move. Không dùng khớp câu mẫu hoặc
điểm judge tự chấm làm kết luận duy nhất; ghi độ bất đồng của người chấm.

Thứ tự đề nghị: kiểm và nối đường action–reaction; làm rõ độ mới của state;
thử bộ nhớ kinh nghiệm có điều kiện; sau đó mới xem xét huấn luyện hoặc thêm
inference để cân nhắc. Mỗi bước phải chứng minh lợi ích trên tình huống chưa
thấy và giữ được cả quyền hỏi lẫn quyền trêu/phản biện của Eris.

## Làm rõ tiếp: EQ thực tế thấp, cập nhật state mới là nền

Người dùng chấp nhận hướng cập nhật cách hiểu tình huống nhưng nhấn mạnh EQ
hiện tại còn thấp. **Thay thứ tự ưu tiên ngay phía trên:** kiểm chứng năng
lực suy luận và ứng xử xã hội của Root cần được làm trước hoặc song song
với nối vòng học; không coi hoàn thiện plumbing/bộ nhớ là đã nâng EQ.

Trong output thật, Root đã nhận ra “anh đang xả” và tật hỏi kỹ thuật, intent
cũng ghi “không hỏi thêm”, nhưng say tiếp tục yêu cầu chỉ ra chỗ sai ý. Như vậy
ít nhất lượt đó không chỉ là thiếu nhãn tình huống: khả năng chuyển nhận biết
thành lựa chọn có tác động phù hợp còn thất bại. Chưa có đối chứng để quy
toàn bộ cho model nền; prompt, state nhiễu và cách chọn hành động còn là các
giả thuyết cạnh tranh.

Các năng lực cần kiểm riêng, qua nhiều lượt:

- Đọc cảm xúc và điều người nói quan tâm từ bằng chứng, kể cả thiếu dấu/lóng.
- Gán đúng đối tượng/nguyên nhân: bực Kiro, bực Eris hay bực vì đáp trễ.
- Hiểu một câu đáp sẽ làm gì với người nghe: được nghe, bị xét hỏi, phải tự
  bảo vệ, bị phủ nhận trải nghiệm, hoặc được cùng đùa. Đây là giả thuyết về
  tác động cần đối chiếu phản hồi, không phải đọc được suy nghĩ người dùng.
- Tự chọn và điều chỉnh cách đáp theo tác động đó, vẫn giữ quan điểm/cá tính.

`personality.py` có seed `empathy_depth=0.76`. Đây là mô tả trong profile,
không phải phép đo hoặc cơ chế cung cấp năng lực hiểu cảm xúc. Tăng số này
chưa chứng minh model có phán đoán xã hội tốt hơn. Các field repair/trust/
appraisal cũng không tự thay thế năng lực của model quyết định lời public.

Thử nghiệm đầu nên đặt cùng các case mới dưới ba điều kiện: runtime đầy đủ;
context gọn với history nguyên văn và dữ kiện đủ; context đã sửa sự kiện/state
cũ nhưng không cung cấp lời khuyên hoặc đáp án hành vi. Tách probe hiểu tình
huống khỏi probe chọn lời và hội thoại tương tác thực tế; giải thích đúng
trong probe không được tính là đã ứng xử đúng. So sánh model ở cùng điều kiện
nếu có quyền truy cập. Giữ cả phản ví dụ user thực sự cần hỏi để debug hoặc
muốn Eris trêu/phản biện.

Nếu model vẫn hiểu được lời chê nhưng ứng xử hụt ở context sạch, cần nghiên
cứu model có năng lực xã hội tốt hơn hoặc huấn luyện trên tương tác có phản
hồi phù hợp. Nếu context sạch tốt mà runtime kém, ưu tiên biểu diễn context/
xung đột chỉ dẫn. Nếu lần đầu tốt nhưng không thích nghi qua các lần phản
hồi, ưu tiên vòng học. Đây là tiêu chí phân biệt hướng đầu tư, chưa có kết
quả chạy để chọn nhánh.

Nguồn bổ sung:

- [EQ-Bench 4, phương pháp của tác giả](https://eqbench.com/index.html) đánh
  giá tương tác nhiều lượt với persona có nhu cầu cạnh tranh. Model được
  giao mục tiêu xây trust; kết quả thể hiện năng lực dưới framing đó, không
  tự chứng minh hành vi tự nhiên mặc định của Eris. Không lấy leaderboard
  làm bằng chứng chọn model thắng cho tiếng Việt hoặc pipeline hiện tại.
- [SOTOPIA-π](https://arxiv.org/abs/2403.08715) nghiên cứu học kỹ năng xã hội
  qua behavior cloning và self-reinforcement trên tương tác được lọc. Tác
  giả cũng thấy LLM judge đánh giá quá cao agent được huấn luyện xã hội;
  vì thế cần người chấm và holdout, không chỉ tự chấm bằng model.
- [LIFELONG-SOTOPIA](https://arxiv.org/abs/2506.12666) ghi nhận bộ nhớ tốt hơn
  có thể giúp nhưng vẫn còn khoảng cách với người trong nhiệm vụ hiểu lịch
  sử tương tác. Không suy sửa memory là đủ nâng năng lực xã hội nền.

Chưa chạy benchmark/API hay thay runtime trong bổ sung này. Mục tiêu tiếp
theo là nâng phán đoán cảm xúc–xã hội và khả năng hành động theo phán đoán đó,
không tối ưu số câu đồng cảm hoặc mức chiều ý người dùng.

## Yêu cầu kiểm chứng live tiếp theo, 04/11

Người dùng đã yêu cầu chạy kiểm chứng. Đã chuẩn bị
[probe cô lập](../../.agents/tasks/eris-eq-verification/probe.py) và
[protocol chốt trước khi có output](../../.agents/tasks/eris-eq-verification/protocol.md):
44 lời gọi, hai lần lặp, giữ model/wire options của trace; bốn case lịch sử
so runtime replay/context gọn/loại active social carry, cùng các đối chứng
debug/đùa/bất đồng/đáp trễ và probe hiểu tình huống riêng. Có thêm condition
policy ngắn cho hai case. Đây là kế hoạch thí nghiệm, không phải kết quả.

**Chưa gọi được model:** localhost:5103 từ chối kết nối, inventory Docker cho
thấy whale2api dừng. Lệnh khởi động bị automatic approval chặn với lý do
`blocked by policy`; không dùng cách khác để vượt chặn. Đã nhờ user bật
gateway qua câu hỏi đang chờ. Script prepare đã tạo plan/ablation manifest;
chưa có results.jsonl và chưa có bằng chứng EQ live mới. Khi gateway sẵn
sàng, chạy `python -X utf8 .agents/tasks/eris-eq-verification/probe.py --run`.
Script không khởi động dịch vụ, ghi memory Eris, phát TTS hay thực thi tool.

# Triển khai vòng học và thử nâng suy xét xã hội, 04/11/2026

## Kết quả và quyết định

Đã nối đường **lời Root chọn → phát xong → phản ứng user → observer đánh giá
đúng nguồn → kinh nghiệm quay lại Self của Root** trong source dev. Không
dùng bộ lọc câu, cấm hỏi, ép đồng cảm hoặc cho một model khác viết lại lời
Root. Không thay model/routing, không sync/build portable hoặc ghi memory
thật. Live learning chỉ dùng state sandbox của task.

Hai biến thể cân nhắc cùng inference đã được chạy A/B và **chưa chứng minh
tăng EQ**. Vì vậy `ERIS_ROOT_INTERACTION_CHOICES` mặc định **0**. Phần học từ
lời đã phát vẫn chạy với move Root hiện có; không cần bật bản ghi suy xét.
Không gọi thay đổi plumbing này là đã nâng năng lực xã hội nền của model.

| Thử nghiệm cùng DeepSeek | Baseline | Candidate | Quyết định |
|---|---|---|---|
| V1: bản ghi lựa chọn tùy chọn, 56 calls | 17 đạt / 11 mixed | 19 đạt / 8 mixed / 1 lỗi rõ | Chênh nhỏ, lỗi mục tiêu còn, chỉ 7/28 có metadata; không chốt thành công |
| V2: yêu cầu bản ghi trước lời đáp, 56 calls | 19 đạt / 9 mixed | 15 đạt / 13 mixed | Metadata 27/28 nhưng lời đáp không tốt hơn; không bật mặc định |

Chấm một reviewer assistant, ẩn điều kiện/repeat và summary khi đọc lời đáp;
không phải đánh giá độc lập của người dùng. Case cũ dùng phát triển; bốn
case mới được soạn trước A/B V1 rồi dùng lại V2, **không còn là holdout sạch
để xác nhận V2**. Không suy tỷ lệ trên thành xác suất dài hạn hoặc điểm EQ.

## Đường học được nối như thế nào

1. Delivery adapter truyền metadata của **đúng move** vào callback delivered.
   Candidate, prepare, transport chấp nhận, audio đang chạy hay bị hủy chưa
   được tính là kinh nghiệm đã phát xong. Không lấy `host.last_agent_turn`
   của lượt khác về gắn nhầm vào callback muộn.
2. Callback ghi `PendingStrategyUse` qua queue single-writer hiện có. ID là
   delivery thật; duplicate không tăng số lần dùng hoặc được học lại sau khi
   settle. Tôn trọng công tắc identity learning.
3. User ingress ghi nguyên phản ứng có turn/ref vào pending của đúng
   conversation và **đúng assistant turn trước đó**. Không phân loại cảm xúc
   bằng từ khóa, không gọi model/đợi mạng trên ingress. Kết quả observer có
   thể bị discard nhưng reaction đã nằm trong reducer state để đánh giá sau.
4. Observer nhận riêng một pending có phản ứng, đúng conversation. Snapshot
   lấy trước khi ghi move mới, nên câu hiện tại không tự thành hậu quả của
   chính nó. Outcome phải mang đúng ID được cấp để đánh giá và dẫn reaction
   ref; không nhận self-confidence hoặc câu assistant làm bằng chứng user.
5. Lưu tối đa **hai trải nghiệm mỗi strategy**: mục đích, context, lời đã
   phát, phản ứng, dự đoán nếu có, outcome/confidence của observer và ref nguồn.
   `compact_identity`/`_read_self` giữ phần này trong Self đang có, không tạo
   một canonical memory store hoặc một lane chọn hành vi mới.

Khi Root không xuất interaction_choice, dùng nhãn provenance `root_move`
cho hành động chưa phân loại, giữ nguyên intent và lời đã phát. **Utility
chung không được tăng/giảm** cho nhóm pha trộn này; chỉ giữ trường hợp và
diễn giải có phạm vi. Không đoán một social strategy từ dấu hỏi hoặc vài từ
trong lời user. Root được cấp cách đọc các experience như dữ liệu có thể sai,
không phải mệnh lệnh hoặc fact mới về người dùng.

Đây là bộ nhớ kinh nghiệm có giới hạn, không cập nhật trọng số model. Pending
vẫn bị giới hạn tám mục và lifecycle 24h/tám turn của reducer cũ. Partial
playback không được học như đã nghe trọn; phản ứng tới trước terminal receipt
có thể bị bỏ qua theo hướng bảo thủ. Không tuyên bố exactly-once qua mọi crash
hoặc lưu được mọi lượt. Hiện lưu snippet có giới hạn, không phải full transcript.

## Nhánh suy xét thử nghiệm

`interaction_choice` là summary ngắn do Root tạo: tình huống, strategy/context
tự đặt, tác động dự kiến, phương án khác/điều chưa chắc. V2 thêm
`listener_action` mô tả yêu cầu mà lời đáp đặt lên người nghe.

Root vẫn tự quyết say. Không có tầng kiểm keyword rồi thay câu, không loại
câu hỏi hoặc xếp hạng lựa chọn hộ Root. Nếu bật thử nghiệm, schema wire yêu
cầu summary; public contract vẫn giữ năm field cũ và nhận legacy/prose
recovery. Warm-up và turn dùng cùng schema theo công tắc. Writer không nhận
summary làm brief public, tránh đọc nó ra speech hoặc dùng làm kế hoạch thứ hai.

V2 cho thấy model có thể ghi một tác động mong muốn nhưng say vẫn đi lệch:
case cảm xúc lẫn lộn ghi “không bị buộc chọn một” nhưng lại nói “vui trước đi”.
Không xem việc điền thêm schema là bằng chứng EQ đã tốt hơn.

V1 HTTP median: baseline 5,49s, candidate 6,17s. V2: 5,33s và 6,94s. Đây là
thời gian request trong điều kiện thử, không phải latency app/TTS. Không dùng
thinking high như bằng chứng gateway có nhiều cấp effort; chỉ xác nhận option
đã gửi. Hai vòng mỗi vòng 28 baseline + 28 candidate; không retry câu dở để
chọn output. Candidate definitions, toàn bộ output và ratings được giữ.

## Kiểm chứng vòng học bằng model thật

[learning_live.py](../../.agents/tasks/eris-eq-upgrade/learning_live.py) dùng
observer/router/reducer production với transport HTTP live, state trong
`.agents/tasks/eris-eq-upgrade/sandbox/`. Hai tình huống **tổng hợp**:

- Root hỏi brief khi người dùng muốn xả; phản ứng phàn nàn. Observer đánh giá
  `strategy_misattuned_backfired`, reinforcement -0,65, dẫn đúng reaction ref.
- Root hỏi stack trace sau yêu cầu debug; phản ứng cảm ơn rõ. Observer đánh
  giá `strategy_succeeded`, reinforcement +0,70, dẫn đúng reaction ref.

Cả hai settle khỏi pending, lưu thành experience, utility `root_move` vẫn 0.
Điều này xác nhận model thật có thể cấp assessment cho đường học và code đã
lưu/đọc đúng; **không xác nhận user thật đồng ý với đánh giá hoặc EQ tăng**.

Lần đầu probe có một model dẫn ref dưới dạng `reaction_ref: <id>` nên strict
binding giữ pending. Đã thêm chuẩn hóa đúng prefix này, vẫn từ chối substring
hoặc ID sai; retest live hai case thành công. Giữ cả receipt lần đầu và retest.
Không nới guard thành nhận mọi citation nghe hợp lý.

[memory_transfer.py](../../.agents/tasks/eris-eq-upgrade/memory_transfer.py)
đọc Self bằng `_read_self` thật, so cùng snapshot có/bỏ hai experiences,
hai tình huống mới x hai sample x hai điều kiện = **8 calls**. Với tình huống
xả mới, có experience: 2/2 không kéo sang chẩn đoán; bỏ experience: 1/2 có
thêm câu hỏi. Cả 4 reply khi nhờ debug vẫn hỏi thông tin cần thiết. Mẫu quá
nhỏ để chứng minh cải thiện, nhưng không thấy học lệch thành cấm hỏi trong
nhóm này. Đây vẫn là one-step replay, chưa phải học dài hạn trong app.

## Model ứng viên đang có

Đã gọi hai serving stack hiện có với **cùng text gắn nhãn role**, bốn case
x hai lần mỗi stack = 16 calls. Cách này tránh adapter Gemini làm mất role
khi nối messages, nhưng khác native multi-message của production. Gemini
adapter cũng không thực thi cùng thinking/token settings; không coi là so
weights thuần túy ở cùng compute budget.

| Stack | Đạt | Mixed | Lỗi rõ | Median HTTP |
|---|---:|---:|---:|---:|
| DeepSeek Web | 5/8 | 3/8 | 0 | 3,94s |
| Gemini Web hiện có | 5/8 | 1/8 | 2/8 | 7,70s |

Hai lỗi Gemini là từ chối lạc đề và hiểu lời chê trợ lý thành chê front-end
của công cụ khác. `/v1/models` quảng bá alias flash nhưng `/api/status` chỉ
liệt kê mô hình tự động; server có thể fallback khi resolve alias không được.
Vì vậy chỉ xác nhận **stack Gemini Web đã gọi**, không xác định chắc phiên
bản Gemini nền hoặc kết luận các model Gemini khác có cùng chất lượng.
Chưa có bằng chứng để đổi Root mặc định sang stack này; không gọi alias Pro
không được xác nhận hoặc sửa routing thật để thử may rủi.

## Kiểm tra code và baseline

- Nhóm trực tiếp liên quan: **105 passed**.
- Nhóm rộng conversation + observer integrity/singleflight + personality:
  **616 passed, 6 failed**; sáu failure đều tái hiện khi import các module
  snapshot trước thay đổi, không thay file trong worktree.
- Một assertion schema cũ còn thiếu `canvas_segments` cũng fail trên snapshot;
  cập nhật assertion cho các field tùy chọn và so schema theo feature flag.
- Ruff trên module/test mới: pass. Ruff trên tất cả module chạm có bốn lỗi
  cũ, tái hiện trên snapshot: ba unused import và một setattr constant. Không
  tuyên bố lint toàn nhóm xanh. `git diff --check` pass.

Sáu failure còn lại:

1. `test_action_claim_integrity_v12.py::test_writer_manifest_empty_does_not_bypass_action_guard`
2. `test_action_claim_integrity_v12.py::test_writer_allows_effect_supported_by_runtime_registry`
3. `test_action_claim_integrity_v2.py::test_writer_checks_target_independent_of_manifest`
4. `test_action_claim_integrity_v2.py::test_writer_checks_tts_japanese_action_type`
5. `test_character_evidence_v1.py::test_public_memory_text_and_records_share_one_selection`
6. `test_character_public_claim_gate_v1.py::test_empty_manifest_does_not_turn_unverified_action_into_phatic`

Không trộn nhóm này với sáu lỗi memory/test đã nêu ở bàn giao đầu chat;
chúng là phạm vi chạy khác. Không chạy `test_run_20260811_repairs.py`.

Tests mới kiểm one-call/direct finalize giữ say, opt-out cả prompt/schema,
delivery-only/cancelled, duplicate/disabled learning, scope/turn/age/ref,
reaction sống qua roundtrip khi observer không commit, settle đúng một lần,
không thưởng chung cho unclassified move và không chấm ID ngoài scope.

## Artifacts và giới hạn triển khai

[Task artifacts](../../.agents/tasks/eris-eq-upgrade/) gồm before snapshot
các module sửa (không phải backup cả worktree), hashes source cuối,
ab/v2 results + ratings, serving comparison, live learning, transfer probe,
test logs và baseline replay. Một commit workspace từ bên ngoài xuất hiện
trong lúc làm; không reset hoặc undo. Module writer baseline đọc từ parent
`38a87584`, diff xác nhận chỉ khác phần loại metadata khỏi writer brief.

Thay trạng thái source “chưa có producer pending strategy” trong nghiên cứu
trước; các trace 03/11 vẫn là bằng chứng lịch sử thiếu dữ liệu. Bản portable
kiểm cuối chưa có module `interaction_choice.py` và các file liên quan khác
hash dev. Chưa sync, restart, nghe audio hoặc xác nhận tác động trên hội thoại
live của người dùng. Trạng thái đạt được là **nền học đã nối và kiểm chứng,
nhánh suy xét thử nghiệm bị giữ off vì chưa đủ lợi ích**, không phải EQ đã
được nâng thành công bằng prompt.

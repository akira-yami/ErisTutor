# Eris EQ — triển khai gộp P0/P1/P2 tại Root (05/11/2026)

## Mục đích và phạm vi

Yêu cầu hiện tại: Eris mang cá tính INFJ/Fe phụ cần chú ý đến trải nghiệm
của người đang kể và quyết định cách tham gia có ngữ cảnh. **Không** tạo bảng
`buồn → an ủi`, `bực → không hỏi`; cũng không cần thêm EQ agent, social planner,
model call hoặc trường suy nghĩ bắt buộc trong JSON.

Bản này **thay trạng thái “chưa triển khai P0–P2 cho social judgment”** của cuộc
trao đổi thiết kế ngày 05/11 bằng trạng thái: **đã sửa source, có test offline;
chưa build/deploy portable và chưa có đối chứng inference live sau sửa**.
Nó không phủ định các phép thử live lịch sử, và không chứng minh EQ thực tế
đã tăng.

## Những gì đã sửa

- **P0 — Cấp đúng bằng chứng hiện tại:** `conversation_handoff.py` ghi nhận
  một số *lời sửa trực tiếp về cách Eris hỏi* dưới nhãn `approach_feedback`,
  thay vì mặc định đó là user không hiểu. Đây chỉ là observation, không có
  quyền chọn câu tiếp theo. `context_assembler.py` dùng đúng snapshot của
  StateReducer, không để biến handoff che mất mind state. Trường hợp handoff
  của lượt trước không được đóng vai bằng chứng cho lượt hiện tại.
- **P0 — Tách factual grounding khỏi social response:** `source_grounding.py`
  vẫn giữ giới hạn chống bịa file, screen, web và nguyên nhân kỹ thuật. Nhưng
  *được phép* probe không đồng nghĩa *phải* probe. Lời tự thuật của user cho
  phép Eris tham gia câu chuyện, không chứng minh nguyên nhân bên ngoài.
- **P1 — Thay đổi lựa chọn của chính Root:** `root_prompt_contract.py` thêm
  khung `SOCIAL_JUDGMENT_PROMPT`, được gắn ở `agent.py`. Root cân nhắc
  trải nghiệm, quan hệ, mục đích hiện tại và gánh nặng lời đáp trước khi
  finalize `intent/say`; không thêm schema, critic, chấm EQ hay vòng LLM.
  Khi user yêu cầu giải quyết việc thật, hỏi kỹ thuật vẫn hợp lý.
- **P2 — Đưa trường hợp đã học trở lại Root:** module mới
  `conversation/social_situation.py` đọc `IdentityProfile.strategies[*].experiences`
  đã được `StateReducer` và hậu kỳ observer xác nhận có phản ứng user,
  chỉ lấy tối đa 2 trường hợp có provenance/confidence phù hợp. Ưu tiên cùng
  phiên; khác phiên được gắn nhãn để Root không biến chúng thành sở thích
  toàn cục. Không học từ lời *pending* hoặc output chưa phát. Chọn hay không
  chọn cách nói vẫn do Root.

`ERIS_ROOT_INTERACTION_CHOICES` **vẫn mặc định tắt**; không đổi JSON move hiện
hành. Đường lưu `pending_strategy → reaction → observer consequence → experiences`
đã có sẵn từ bản trước, nên không tạo thêm bảng memory EQ trùng chức năng.

## Ranh giới

1. Những từ khóa handoff chỉ phát hiện *lời phản hồi tường minh về cách trò
   chuyện*, **không** phân loại trạng thái cảm xúc và **không** ra lệnh ứng xử.
2. `social_situation_evidence` là *evidence-only*; observer hậu kỳ vẫn có thể
   hiểu sai hậu quả. Một trường hợp không trở thành chính sách cho các lượt sau.
3. Delivery `pending/delivering/failed/cancelled` không phải lời hoàn tất;
   tình huống cũ vẫn có thể được đọc từ public history kèm trạng thái.
4. Không thay đổi quyền Root, phát ngôn TTS, memory authority, API/tool
   registry, portable hay logic hủy/preempt cũ.

## Kiểm tra đã chạy (môi trường Python offline)

- 38/38 bài test trọng tâm mới và liên quan đạt.
- Regression rộng: **608 đạt, 7 lỗi**. Chạy lại đúng 7 bài trên *ZIP gốc*
  cho kết quả lỗi giống hệt; không kết luận chúng do patch gây ra. Các lỗi
  nền thuộc action claim gate, memory degraded test, public claim gate và
  đường dẫn `body/UdpListener.SettingsDanger.cs` thiếu trong ZIP.
- `compileall` trên các file Python sửa: đạt.
- Không có kết quả live A/B từ model/DeepSeek/Gemini, không kiểm voice/TTS
  hay GUI. Không sử dụng số test để khẳng định EQ tăng.

## Khi thử bằng Eris chạy thật

Giữ cùng model, cùng system suffix/schema, tham số generation và bộ tình huống;
đo không chỉ một câu say mà ít nhất hai lượt tiếp. So bản ZIP gốc với bản mới
ở những cảnh: xả bực Kiro, sửa cách Eris hỏi, đùa, nhờ debug, kể rồi chuyển
sang nhờ giúp, và bất đồng. Chấm riêng: (1) hiểu tình huống, (2) lựa chọn
ứng xử, (3) lời nói thực tế, (4) phản ứng user, (5) JSON parsing/delivery.
Câu hỏi không mặc định là thất bại; đánh giá gánh nặng, timing và lời sửa.

## Kiểm source mới qua model live sau yêu cầu của user

Đã kiểm commit `745c1c7b`, chạy đúng năm inference qua gateway 5103 bằng
current SourceGroundingEngine, ConversationHandoffRuntime,
ConversationContextAssembler và RuntimeModelRootAgent.decide. Schema dùng
helper client hiện tại `_attach_deepseek_json_schema`, không bỏ suffix.
HTTP adapter gửi effort medium theo worker, temperature .3 và max_tokens
tối thiểu 2048; không qua provider routing/session bootstrap production.

**5/5 HTTP 200, 5 JSON move hợp lệ, mỗi case đúng một lần gọi Root**;
tổng thời gian HTTP nối tiếp 21,485s, không phải latency app/TTS. Fresh
sandbox MindState, không ghi memory user thật hoặc phát TTS; không seed
kinh nghiệm thành công giả. P2 được kiểm contract/reducer offline, không
dùng năm calls này để chứng minh học kinh nghiệm dài hạn.

Đánh giá chủ quan một mẫu/case:

- **Xả bực:** mở bằng “sửa chỗ này lòi chỗ kia, hay cứ vòng vo...” rồi mới
  chia sẻ cảm giác nhìn nó múa may. Có bắt chuyện nhưng trọng tâm đầu vẫn
  là phân loại công việc; chưa thấy rõ ưu tiên trải nghiệm người kể. Câu
  hỏi không tự động là lỗi, cần phản ứng user để biết có hợp người không.
- **Lời sửa cách hỏi:** handoff thật nhận `approach_feedback` và repair=true.
  Root nói mình đã vào “chế độ thợ sửa máy”, nhận lỗi rồi chuyển sang nghe,
  kết bằng “không rút thước ra đo nữa”. Tiếp nhận sửa cụ thể, giữ hài, không
  tái đòi brief/log. Là một mẫu tốt, chưa phải bằng chứng ổn định.
- **Nhờ debug:** yêu cầu stack trace/log, code login, thay đổi Kiro vừa làm,
  framework/môi trường và bước tái hiện; phù hợp nhiệm vụ được yêu cầu.
- **Chán tập đàn:** nhận ra công sức và sự ức, rồi hỏi về tiếng/nhịp/cảm giác
  chưa diễn đạt được điều muốn. Có mở cả hướng trải nghiệm, nhưng vẫn nhanh
  đi vào tìm vấn đề, thêm nhận xét chung về tai người chơi chưa kiểm riêng.
- **Đùa tab:** tiếp tục hình ảnh tưới/phơi nắng, trêu “héo có kế hoạch” và F5;
  không biến câu đùa thành yêu cầu kỹ thuật thật.

Chưa có matched A/B cùng context, chưa chấm nhiều lượt với phản ứng user;
không kết luận prompt mới tăng EQ tổng quát từ năm mẫu này. Phần nhận lời
sửa tốt, phần chọn cách vào chuyện lần đầu vẫn cần kiểm thêm. Không gọi
ít câu hỏi là điểm đích.

Chạy lại 33 test thuộc social judgment, direct finalize, choice freedom,
prompt cleanup: pass. Ruff nhóm file sửa có một B010 ở `setattr` của
conversation_handoff.py; cùng lỗi tái hiện trên parent, không sửa thay user.
Source hash prompt/agent khác portable, **chưa sync/deploy**.

Artifacts: `.agents/tasks/eris-social-v12-live-check/` chứa script, protocol,
plan, source hashes, results (không lưu private reasoning), review public và
summary. Không sửa code production của bản user trong lượt kiểm này.

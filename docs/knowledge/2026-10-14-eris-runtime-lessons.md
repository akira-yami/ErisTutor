# Tri thức Eris: bài học từ điều tra runtime và gói rescue

Cập nhật Live2D ngày 07/11: [rig miệng V13.2 và lip-sync TTS](2026-11-07-live2d-v13-2-tts.md).
Bản repo và portable đã dùng rig mới cùng hai atlas từ gói full. Quy tắc thu
lưỡi theo rig cũ bên dưới không áp dụng cho V13.2: lưỡi trong miệng đi theo
hàm, ArtMesh lè lưỡi cũ được ẩn trong MOC3 chạy. Đã kiểm phát TTS cục bộ qua
thiết bị âm thanh và Cubism Native; chưa kiểm hội thoại qua provider từ xa.

Cập nhật Live2D ngày 06/11: [atlas 2048 từ file Cubism gốc](2026-11-05-live2d-expression-audit.md#atlas-2048-từ-file-cubism-gốc--06112026).
Gói hiện tại dùng hình vẽ gốc, xếp lại atlas ở kích thước ảnh nguồn. MOC3
chỉ đổi UV để khớp atlas mới. Rig và các biểu cảm hiện có được giữ lại.
Các ghi chú đổi màu và vẽ miệng ngày 05/11 bên dưới là lịch sử.

Cập nhật Live2D ngày 05/11: [kiểm biểu cảm và chuyển đổi PSD2Live](2026-11-05-live2d-expression-audit.md).
Kiểm offline gói 23 biểu cảm tìm được lỗi mí/lưỡi và hai preset trùng; theo yêu cầu
cứu hộ sau đó đã sửa source, sync 8 file sang Documents/eris và kiểm native bằng
Python portable. Lượt vẽ anime tiếp theo đã thay đúng năm đảo texture miệng,
phối lại sáu preset mí và sync tám file; MOC3/UV/alpha giữ nguyên. Giá trị mí mới
thay giá trị cứu hộ ban đầu; xem phần cập nhật mới nhất trong báo cáo.
Bản PSD2Live xuất lại vẫn có sai khác rõ.
Lượt tiếp theo làm sạch nét/halo môi (111 pixel RGB, giữ alpha và keyform),
đã tự xem 16 native render và sync texture. Đây là chỉnh nét nhẹ; dáng môi vẫn
cần author keyform nếu muốn cải thiện rõ. Xem cập nhật đầu báo cáo.
Thay nhận định chỉ dựa trên cảnh báo compiler: chưa coi pipeline PSD2Live bảo toàn rig.
Đã tự xem cửa sổ renderer portable khoảng 60 FPS ở toàn thân và cận mặt, gửi
stage/audio_start qua UDP riêng với envelope tổng hợp; mắt/lưỡi và chuyển
sad/lazy đúng trong các trạng thái đã xem. Chưa kiểm hội thoại/TTS có âm thanh;
xem báo cáo để phân biệt kết quả trước và sau sửa.

Cập nhật Live2D ngày 03/11: [menu và hệ tọa độ overlay](2026-11-03-live2d-menu-geometry.md).
Ảnh user cho thấy clamp overlay/DPI riêng chưa đủ xác nhận menu bám model;
đã sửa đổi desktop/viewport và kiểm fixture, chưa xác nhận trực quan hai màn hình.

Ngày tổng hợp: **15/10/2026**. Cuộc điều tra được thuật lại chủ yếu liên quan
đến phiên và thay đổi ngày **14/10/2026**.

Mục đích: giúp người/agent tiếp theo hiểu quyết định đã chốt, tránh lặp lại
chẩn đoán sai và biết cần kiểm chứng gì trước khi sửa. Đây là tri thức dự án,
không phải xác nhận Eris đang chạy tốt ở thời điểm đọc.

## Yêu cầu cập nhật 18/10/2026: text chờ voice

User yêu cầu **text phải đợi voice rồi mới commit lên**. Bubble và stage segment
chỉ công bố khi event `audio_start` thực sự đến;
không cần chờ đọc xong câu. Panel/trạng thái chuẩn bị giọng vẫn cập nhật ngay.

Quyết định này thay thế chủ trương lịch sử “hiện text ngay để UI không treo” và
fallback nhả text sau 30 giây hoặc khi TTS lỗi/hủy. Source hiện tại bỏ timer
nhả chữ; lỗi/hủy trước playback bỏ text đang chờ, không tính `no_tts` của câu cần
nói là delivered. Public-turn ledger ghi câu visible tại lúc gửi bubble; history
của delivery adapter vẫn giữ pending receipt, không đồng nhất với lời đã nghe.
Opt-out kỹ thuật `ERIS_TTS_SYNC_TEXT=0` vẫn giữ hành vi legacy khi bật rõ ràng.
Không dùng callback `on_first_start` làm bằng chứng playback: task manager và
engine legacy còn gọi callback đó ở fallback lỗi. Cả hai backend hiện phát event
`audio_start` riêng tại ranh giới bắt đầu phát PCM; Live2D tiếp tục dùng event này.

Nguồn: yêu cầu trực tiếp và thay đổi source `public_output_runtime.py` cùng
regression `test_public_text_audio_commit.py` (chat/autonomous, VI/JA, prepared
artifact, synth chậm, lỗi/hủy và generation bị preempt). Đây chưa phải xác nhận
đã nghe hoặc triển khai thành công trên bản portable đang chạy.

## 1. Yêu cầu sản phẩm và ưu tiên cần giữ

**Bằng chứng latency mới ngày 18/10:** hai lượt 10:47–10:51 chờ khoảng 98 giây
trước request ElevenLabs; synth chỉ mất 3,4–3,8 giây. Probe tái hiện refresh
Firebase thử 8 IPv6 không kết nối được trước IPv4, với timeout 12 giây mỗi địa
chỉ trong source. Kết quả thay thế suy đoán synth/text-sync gây toàn bộ khoảng
chờ ở hai lượt đó; chưa sửa runtime. Xem
[điều tra token/IPv6](2026-10-18-elevenlabs-token-ipv6-latency.md).

- **Eris phải có âm thanh khi nói.** User trực tiếp bác phương án chỉ hiện text
  khi dịch lỗi (nguồn dòng 603). Kênh hiển thị tiếng Việt và kênh TTS tiếng Nhật
  có hợp đồng riêng; fallback cần tôn trọng giọng/ngôn ngữ downstream.
- **Giữ cá tính:** hài khô, trêu nhẹ, tự giễu, dỗi nhẹ và quan điểm có chỗ trong
  nhân vật. Đánh giá chúng theo ngữ cảnh và ranh giới thật, tránh tự động gọt
  thành câu trung tính. Điều này không miễn trừ kiểm chứng fact, receipt hoặc
  yêu cầu cụ thể của user.
- **Thinking là lựa chọn theo vai trò và nhiệm vụ.** User quan tâm chiều sâu
  suy nghĩ; gói rescue chọn tối ưu surface writer, không yêu cầu tắt thinking
  toàn hệ thống. Hiệu quả và chất lượng cần đo trên đường chạy thật.
- **Ngôn ngữ chat rất đa dạng:** thiếu dấu, viết hoa, tiếng lóng, teencode,
  tiếng Anh xen tiếng Việt. Search web cũng là năng lực user coi trọng;
  đừng suy ra có search chỉ vì câu trả lời đúng.
- **Giữ phần Live2D theo audio TTS.** User nêu ưu tiên này ở dòng 1901.
  Việc cho phép undo trong lần hợp nhất cũ không phải quyền xóa thay đổi tương
  lai. Quyết định cuối của lần đó giữ Live2D không xung đột rescue tại chỗ.

## 2. Bài học kiến trúc có thể dùng lại

### Lần theo đường chạy thật trước khi sửa prompt

Cá tính, độ trễ và khả năng phát tiếng là kết quả của cả pipeline:
Root → writer → localization → prepare/guards → delivery/TTS, cộng state và
feedback nền. Prompt ủng hộ cá tính vẫn có thể bị verifier một chiều, affect
cold-start hoặc sanitizer làm phẳng. Không suy từ một trace sang kết luận
mọi prompt đều vô can hoặc chỉ có một nguyên nhân.

Phân biệt **vai trò lane**, **model ID**, **thinking** và **search**. Nhiều lane
có thể dùng cùng model. Module được import, config có trường, hay test fixture
có model không chứng minh chức năng được gọi trong production. Kiểm call site,
role thực tế, payload sau override và khả năng gateway đang phục vụ.

### Giữ quyền quyết định và quyền diễn đạt rõ ràng

Root sở hữu ý/hành động của lượt; surface writer diễn đạt move đã chọn.
Localizer chuyển transcript cho TTS. Tối ưu writer không nên âm thầm tạo một
người quyết định nội dung thứ hai hoặc một lane phát lời thứ hai.

Bỏ **root self-verify** khác với bỏ mọi verification. Quyết định rescue là
finalize move trực tiếp, vẫn kiểm preemption/generation và giữ các biên
grounding, action claims, secret redaction, ngôn ngữ và single-speaker.
Observer nhìn lại sau hành động có thể nuôi lượt sau; nó không chặn được câu
sai đã phát ra. Cần kiểm cả đường ghi feedback lẫn đường đọc feedback, không
chỉ thấy observer trả `status=success`.

### Bằng chứng phải độc lập với sự tự tin của model

Không dùng sự tự tin hoặc việc model tự gắn cờ “cần verify” làm lưới duy nhất.
Claim “đã làm” cần receipt thành công của đúng action/run/generation. Fact và
memory cần nguồn đúng scope. Ref tồn tại chỉ chứng minh provenance; vẫn cần
xem nguồn có hỗ trợ chính claim hay không. `claim_manifest=[]` cũng không
chứng minh nội dung cuối không chứa claim.

Ví dụ: evidence `user_statement` chứng minh user **đã nói X**, không tự chứng
minh **X đã xảy ra**. Root.say hoặc văn bản model tự tạo không tự trở thành
receipt. Việc bỏ một verifier chỉ hợp lý sau khi hiểu phần bảo vệ bị mất và
phần nào thực sự được lớp khác đảm nhiệm.

### Fail-soft phải giữ hợp đồng downstream

Trong rescue, localization được chọn theo thứ tự: dịch → retry một lần →
filler Nhật trung tính `えっと…` nếu vẫn lỗi. Cancellation/preemption vẫn dừng
candidate, không retry thành câu cũ. Cần đồng bộ transcript ở cả field phẳng
và nested trước prepare; kiểm output thực tế đi vào TTS.

Filler giữ khả năng có tiếng cho nhánh fallback; **không có nghĩa toàn bộ nội
dung display đã được đọc**, không bảo đảm TTS/delivery luôn thành công. Chỉ
đổi language trong một payload cũng không đủ nếu resolver ép `ja` toàn cục.

### Chẩn đoán và kiểm chứng theo triệu chứng

- Đo từng stage và deadline/budget cha; thời gian tổng writer không phải thời
  gian localizer. Tăng timeout con chưa chứng minh deadline cha cho phép nó.
- Xác định checkout, process/executable và bản runtime đang chạy. Sửa source
  dev không chứng minh bản người dùng mở đã nhận thay đổi.
- Khi quyết định theo role trong async code, kiểm nguồn role production dùng.
  Transcript phát hiện attribute `worker_role` khác ContextVar
  `active_worker_role()`; chép một carve-out cũ có thể tạo fix không tác dụng.
- “Không trả lời” có thể là trễ intake, backchannel bị nuốt, prepare reject,
  preempt hoặc delivery/TTS lỗi. Cần event/trace gốc để phân biệt; đừng vá
  collision guard chỉ vì tên nó có vẻ liên quan.
- Không trộn test counts từ phạm vi khác nhau. Test với fake router chứng minh
  contract được test, chưa chứng minh latency, cảm giác cá tính hay audio live.
- Background lane chạy xong không chứng minh nội dung đúng. Điều tra
  inner/observer/curator theo triệu chứng, thay vì mặc định chúng lỗi hoặc khỏe.
- Bảo vệ thay đổi đang dở. `git diff` và danh sách `status` không lưu nội dung
  file untracked; staged changes và file nhị phân cũng cần cách backup phù hợp.
  Chỉ nói backup đầy đủ khi đã kiểm chứng phạm vi và khả năng khôi phục.

## 3. Bản đồ vai trò để định hướng

Bản đồ sau được tổng hợp từ transcript; cần kiểm lại entry point khi làm việc.
Các tên gọi là vai trò, không phải cam kết model/thinking hiện tại.

| Vai trò/lane | Công việc được mô tả | Điều cần kiểm |
|---|---|---|
| Root / executive | Quyết ý, hành động và lifecycle của lượt | Client, role, payload và preempt thực tế |
| Writer / surface | Diễn đạt move ra lời công khai | Wording giữ intent; thinking sau override |
| Localizer | Chuẩn bị tiếng Nhật cho TTS | Transcript cuối, cancellation và fallback |
| Observer / post_observer | Phân tích kết quả lượt, nuôi feedback | Kết quả được ghi và dùng ở lượt sau |
| Inner | Sinh nội tâm/nhật ký | Chất lượng và cách nội tâm ảnh hưởng lượt sau |
| Seed | Sinh mầm ý/đề xuất nền | Proposal tới Root, không tự có quyền phát lời |
| Critic / deep | Phản biện hoặc suy luận sâu | Call site và nhiệm vụ thực tế |
| Curator | Xây Living User Memory | Admission, provenance và recall đúng |
| Companion routing | Policy chọn năng lực/model dự kiến | Có được gọi và điều khiển request hay chưa |

Trục Root → Writer → Localizer nối tiếp; không diễn giải “nhiều lane” thành
“mọi lane đều chạy song song”. Policy vẫn có thể chọn thinking/tool với một
model; không kết luận companion chỉ có ích khi có nhiều model.

## 4. Trạng thái lịch sử và đối chiếu source ngày tổng hợp

**Báo cáo trong transcript:** rescue có ba commit `ac2a4979`, `14a88d34`,
`8ba9a3b7`; cuối cùng merge dev và copy ba file lõi sang bản chạy. Output test
ba file rescue ghi `16 passed in 1.71s` (1689–1696). Các bộ rộng hơn còn hai lỗi
được báo cáo tái hiện trên base; chưa có user xác nhận chạy thật sau rescue.
Đây là kết quả lịch sử, không phải test đã chạy lại trong lần tổng hợp này.

**Đã đọc source trong checkout hiện tại ngày 15/10/2026, chưa chạy live:**

| Quan sát tĩnh | Nguồn trong repo | Giới hạn |
|---|---|---|
| Root trả `NextConversationMove` trực tiếp sau `still_active()` | [agent.py](../../src/runtime/conversation/agent.py), nhánh finalize | Không chứng minh mọi lượt đã giữ cá tính |
| Writer default thinking disabled; client surface carve-out dùng attribute hoặc ContextVar, env default `1` | [runtime_writer.py](../../src/runtime/conversation/runtime_writer.py), [client.py](../../src/llm/client.py) | Executive carve-out còn đọc attribute; không khẳng định Root OFF live |
| Localize có hai lần thử; cancellation raise; hết retry dùng `えっと…` | [runtime_writer.py](../../src/runtime/conversation/runtime_writer.py), `_prelocalize_v03_speech` | Không xác nhận audio/delivery live; guard kiểm script không chứng minh chất lượng dịch |
| Gateway source chỉ quảng bá `deepseek-flash`, `GetModelConfig` trả search=false | [models.go](../../tools/whale2api/internal/config/models.go) | Không chứng minh gateway binary/process đang chạy trùng checkout |

Phạm vi tổng hợp gồm transcript và đối chiếu một số điểm source. Grounding và
các lane vẫn cần được kiểm tra riêng khi có mục tiêu hoặc triệu chứng cụ thể.

## 5. Những kết luận đã bị sửa hoặc không đủ bằng chứng

| Kết luận cũ | Cách dùng tri thức về sau |
|---|---|
| “Companion đang route Flash/Pro mỗi lượt” | Báo cáo sau nói policy không được gọi trên live chat path; kiểm call site trước |
| “Có Flash-search thật; trả lời đúng PokeMMO chứng minh có search” | Test fixture và câu trả lời đúng không chứng minh có retrieval/citation |
| “Executive chắc chắn thinking OFF” | Phát hiện role ContextVar làm carve-out attribute không hoạt động; không lưu bảng OFF cũ như fact |
| “Gateway ép thinking” | Điều tra sửa thành client Eris ép localhost; kiểm cả payload client và gateway |
| “Localizer mất 11,9s nên vượt timeout 8s” | Đó là thời gian pipeline, chưa phải số đo sub-call |
| “Verify riêng khi model nhận ra fact-claim là đủ” | Model có thể bỏ sót; cần bằng chứng và kiểm nội dung cuối độc lập |
| “Bỏ root self-verify là bỏ toàn bộ lưới chống bịa” | Tách root quality check khỏi các biên public/evidence/action |
| “Fallback đọc Việt / chỉ hiện text là phương án đã chốt” | User bác text-only; phương án cuối giữ JA với retry và filler |
| “Nhanh 1,5–2 lần, hết câm, cá tính trở lại” | Ước lượng/kỳ vọng, chưa có so sánh latency hoặc live confirmation |
| “Thiếu env surface flag thì fix chưa bật” | Source ngày tổng hợp dùng default `1`; vẫn kiểm role và process thực tế |
| “Backup mọi uncommitted đủ 100%” | Lệnh transcript chỉ lưu tracked diff và status; chưa chứng minh lưu untracked |
| “Live2D đã bị undo toàn bộ” | Kế hoạch cuối giữ Live2D không xung đột tại chỗ |
| “Observer success nên khỏe; inner hỏng không ảnh hưởng” | Chưa audit nội dung hoặc downstream impact |
| “Đọc log/sửa inline miễn phí; credit đủ X lượt sửa” | Không phải bằng chứng billing, không lưu như cam kết chi phí |

## 6. Việc còn mở theo bàn giao

Những mục này được transcript để lại, **chưa xác nhận vẫn còn lỗi**:

1. Affect cold-start: đo đầu phiên và phiên đã ấm trước khi tăng sàn playfulness
   hoặc tách quyền hài khô khỏi playful_safety.
2. Companion policy và hằng model mặc định: kiểm đường gọi và gateway capability
   trước khi dọn code hoặc nối routing.
3. Search web: cần khả năng gateway/upstream và request thực tế, không chỉ thêm
   một flag ở client.
4. Inner/observer/curator: chưa audit; chọn điều tra khi có triệu chứng hoặc mục
   tiêu cụ thể, đồng thời kiểm feedback/memory được dùng đúng.
5. Chạy live và đo latency/audio/cá tính: transcript kết thúc trước xác nhận
   này. Kiểm endpoint/process đang phục vụ trước khi kết luận rescue thành công.

Các báo cáo hiện có để tra sâu:

- [Cá tính và flattening](../../.agents/tasks/eris-personality/flattening-report.md).
- [Model routing thực tế](../../.agents/tasks/eris-routing/model-routing-reality.md).
- [Writer/localization failure](../../.agents/tasks/eris-writer-fail/writer-localize-fail-report.md).
- [Intake và lượt bị cho là bỏ qua](../../.agents/tasks/eris-dropped-turn/dropped-user-turn-report.md).
- [Lane và các điểm nối](../../.agents/tasks/eris-lane-map/lane-map-report.md).

Đọc các báo cáo với bảng đính chính ở trên; chúng có thể phản ánh trạng thái
trước rescue. Dev và bản chạy từng là `Documents/GitHub/eris` và
`Documents/eris`; vị trí này là bối cảnh lịch sử, cần xác định lại khi dùng.

## 7. Nguồn và quy tắc cập nhật

Nguồn chính là file user dán, 2.307 dòng:
`C:/Users/Gtvkun/.codex/attachments/a6f2c937-d409-462d-b34b-e4ae52d68139/Văn bản đã dán.txt`.

SHA-256:
`1827B26FD5D3FAAC46456288BA6D2D9A809EAF89E10C778EE3ADCA0EB906F40B`.

Các đoạn nguồn quan trọng: self-verify và phản biện evidence (1–213); writer
fail và yêu cầu audio (418–686); routing bị đính chính (1198–1235); role và JA
fallback (1551–1639); test rescue (1689–1696); giữ thay đổi đang dở và phạm vi
backup (1901–1992); bàn giao và giới hạn live verification (2102–2307).

Khi cập nhật, ghi ngày, nguồn/trace/test mới và kết luận bị thay thế. Giữ
phân biệt: yêu cầu user → quyết định thiết kế → đọc source → test giả lập →
quan sát live. Không nâng một tầng bằng chứng thành tầng cao hơn chỉ vì lời
giải thích nghe chắc chắn.

## 8. Bằng chứng mới: phiên 00:19–00:33 ngày 15/10/2026

Đã đối chiếu run log `eris_run_20261015_001956.txt`, 976 record observability
của phiên và raw wire PID 26564 với source portable `Documents/eris/src`
(các file liên quan trùng byte checkout dev). Chi tiết và giới hạn:
[điều tra agent loop/TTS](2026-10-15-run-agent-loop-tts-audit.md).

- **Thay thế trạng thái “chưa có quan sát live sau rescue” ở mục 4/6 trong
  phạm vi phiên này:** 7 public delivery hoàn tất, 3 bị hủy; có một user turn
  writer_failed do Root local 429 → official 401, surface local cũng 429.
  Không khẳng định rescue thành công toàn diện hoặc không còn lỗi.
- Đoạn cuối Root vẫn `step=1/action=move`. Câu tự nói thêm về tiền điện là
  continuation sau `after=may_continue`; giọng nghiêm của lượt kế tiếp cũng
  do Root chọn. Không có bằng chứng runaway nhiều step hay process tự crash.
- Ba delivery cuối bị hủy trước playback khi message mới được xử lý. “ô”
  không nằm trong backchannel whitelist và pure local probe xác nhận nó hủy
  audio; log `enter_chat_mode` không đủ phân biệt mở ô nhập với xử lý message.
  **Đây là trạng thái source/probe của phiên lịch sử; kết luận về whitelist
  hiện tại được thay thế ở cập nhật Task 7–10 ngày 01/11 bên dưới.**
- ElevenLabs có một disconnect thật, Gemini fallback phát xong. **Không nâng
  filler JA của rescue thành bảo đảm mọi câu đều có tiếng:** TTS cancellation
  vẫn bị tính sai thành provider failure trong code; probe xác nhận ba lần
  hủy cùng key có thể mở breaker. Chưa xác nhận breaker mở live trong phiên.
  Metric breaker còn nuốt exception vì key ElevenLabs 5 phần, unpack 4 phần.
- Tám warning `PROVIDER CIRCUIT` về sau thuộc curator timeout khoảng 8s,
  không chứng minh circuit Root chat bị chặn. Raw trace xác nhận Root thinking
  enabled/high, surface disabled. Độ trễ chat 32–37s cuối phiên chưa bao gồm
  tiếng đầu tiên; không quy toàn thời gian đó cho localizer.
- Receipt pending → cancelled làm history projection đổi, gây reconcile lại
  16 entries và phình payload. **Chưa chứng minh** việc này làm lệch câu trả
  lời/cá tính; raw reply vẫn bám input mới. `public_turns=16` không phải 16
  phát ngôn mới hay 16 vòng runaway.

**Bổ sung khi đánh giá cách sửa, nguồn là đọc source ngày 15/10:**
thêm alias backchannel đơn lẻ chưa đủ vì ChatCycle chưa nhận cancel_generation;
bỏ failure khi cancel phải nhả HALF_OPEN permit đúng một lần. Root medium/low
được deepseek.py map thành high, nên đổi knob chưa chứng minh đổi request.
Curator required/concurrency=1 có regression bảo vệ; defer/coalesce cần giữ
evidence, không chuyển thành optional rồi bỏ việc. Degraded Root vẫn nhờ writer
generate nên thay `say` thành câu báo lỗi chưa tự tạo fail-soft độc lập LLM.
Chi tiết nằm trong mục 6 của báo cáo điều tra. Các nhận định này bổ sung giới
hạn của hướng sửa, chưa thay thế quan sát live bằng test hoặc phỏng đoán.

Đây là cập nhật tri thức/chẩn đoán; chưa sửa hoặc triển khai runtime mới.

**Đính chính hiệu lực ngày 01/11:** riêng nhận định “ChatCycle chưa nhận
cancel_generation” phía trên đã lỗi thời đối với checkout hiện tại; xem mục
Learning Canvas Task 7–10. Các nhận định khác vẫn cần đối chiếu riêng với source/trace.

## 9. Thay đổi transport/prompt DeepSeek ngày 15/10/2026

Theo yêu cầu mới của user, source gateway `tools/whale2api` đã bỏ hai hành vi:

- Tự đưa context hội thoại vào file `.txt`, rồi thay phần chat bằng câu tiếp tục
  tổng quát. Chat Completions và Responses nay giữ toàn bộ system, user,
  assistant và tool history trong `prompt` dạng text trực tiếp, kể cả khi
  cấu hình cũ `current_input_file.enabled=true`. Bỏ cả upload lại context khi
  đổi account và khoản token dự phòng cho file context tự tạo. File do caller
  chủ động gửi vẫn theo đường upload riêng. Kết quả tool trong prompt giữ
  `name`/`tool_call_id` nếu có, để kết quả liền nhau vẫn nhận diện được.
- Tự chèn toàn bộ `Output integrity note`, gồm câu yêu cầu detector bỏ qua kiểm
  tra và đoạn hướng dẫn output đi kèm. System prompt của caller và định dạng
  role/tool vẫn được giữ; không lọc/xóa các câu tương tự do caller tự gửi.

Nguồn: diff source gateway và regression test trực tiếp payload upstream.
Đây là quyết định sản phẩm mới, thay thế hành vi transport/prefix trước đó;
không phải bằng chứng các chẩn đoán về cá tính, latency hoặc TTS đã được sửa.
Test giả lập không xác nhận phản hồi live của DeepSeek.

Kiểm chứng sau sửa: `go test ./... -count=1` trong `tools/whale2api` qua toàn bộ;
regression bao gồm stream/nonstream, legacy config bật, file caller chủ động
gửi và nhiều kết quả tool khác thứ tự. Đã biên dịch mới hai binary Linux từ
source này, đóng gói trên base image local cũ và kiểm SHA-256 trùng binary
trong image. Smoke container với `--network none` trả `/healthz` status `ok`.
Hai container Compose `whale2api`/`poolui` đã được tạo lại với image mới và giữ
dừng như trước; chưa thử hội thoại live upstream. Image cũ được giữ tại tag
`eris-whale2api-backup:before-plain-text-20261015` để có thể khôi phục.

## 10. Bằng chứng mới: phiên 16:47–16:54 ngày 15/10/2026

Đối chiếu run log `eris_run_20261015_164726.txt`, 350 record observability
(dòng 494361–494710) và raw HTTP PID 6980. Chi tiết:
[điều tra agent loop phiên 16:47](2026-10-15-1647-agent-loop-audit.md).

- **Xác nhận lỗi tiến độ trong phiên này:** user yêu cầu năm chủ đề/năm lượt;
  chỉ lượt 1/5 delivered. Root vẫn sinh lần lượt 2/5, 3/5, 4/5, 5/5 dù cả bốn
  writer candidate đều bị prepare từ chối. Câu “Hết 5 lượt” nằm trong bản
  định nói, không phải lời đã phát thành công.
- **Xác định thêm đường gây câm sau localization:** cả sáu raw writer reply
  đã có speech Nhật. `IntentionObserver` gộp whitespace/cắt display còn 360
  ký tự nhưng trả `rewritten=False`. Stage nhánh autonomous thấy text khác
  và ghi đè speech bằng bản Việt; prepare sau đó chặn language mismatch.
  Lời chào cũng hỏng cùng cách; lượt chat bỏ qua nhánh đó nên thành công.
  Probe offline với sáu payload thật: chat qua 6/6, autonomous bị chặn 6/6;
  source liên quan đã đối chiếu trùng byte bản portable.
- Root session lưu quyết định move trước writer/delivery, nhưng các request
  tiếp theo không có feedback về lỗi prepare vừa xảy ra. Delta delivery lấy
  từ public history nên không bao phủ lỗi trước dispatch. Cần phân biệt
  quyết định định nói với lượt delivered khi theo dõi kế hoạch nhiều lượt.
- Đây là các `autonomous_signal` từ timer với `step=1`, tất cả move dùng
  `after=wait_user`; không phải chuỗi `continuation_due` hay nhiều step
  runaway trong một run. `wait_user` hủy continuation lease, không tự chứng
  minh autonomous timer bị dừng. Ba timer đầu chọn chờ, rồi Root tự đổi sang
  nói tiếp dù không có user turn mới.
- Format `attempt=1/2` còn sót; writer hiện chỉ generate một lần. Không dùng
  dòng này làm bằng chứng có retry writer. Retry localization là cơ chế khác.

**Kết luận được bổ sung/thu hẹp:** không áp nguyên nhân cancellation/429 của
phiên 00:19 lên phiên mới, và không coi fail-soft localizer là bảo đảm toàn
pipeline có tiếng. Phiên này có Root/surface HTTP 200, một audio phát xong;
năm failure nằm trước delivery. Không có bằng chứng process crash hoặc
gateway plain-text gây lỗi này. Đây vẫn là cập nhật chẩn đoán, chưa sửa hay
triển khai runtime.

## 11. Yêu cầu mới: đổi giọng Việt/Nhật live ngày 16/10/2026

User yêu cầu chọn ngôn ngữ ngay trong settings bubble và nhấn mạnh **bản Việt
phải bỏ các tiếng đệm/đuôi câu Nhật**. Điều này mở rộng yêu cầu ở mục 1: bubble
Việt + voice Nhật vẫn là mặc định, nhưng voice Nhật không còn là ngôn ngữ bắt
buộc cho mọi cấu hình. Filler Nhật fail-soft của rescue chỉ áp dụng mode Nhật.

Source đã bổ sung voice Việt `0ggMuQ1r9f9jqBu50nJn`, profile riêng theo ngôn ngữ,
dropdown live ở Basic, đồng bộ ACK và chặn audio/candidate thuộc ngôn ngữ cũ.
Mode Việt dùng public surface Việt, không gọi localizer Nhật; prefix Nhật bị
loại trước cả tra cache. Lựa chọn đã lưu thắng env bootstrap `ERIS_TTS_LANGUAGE`.

Kiểm chứng: 178 test speech/TTS liên quan qua; Godot export và dev fast build
exit 0. Không khẳng định toàn suite xanh: lượt rộng dừng ở test OCR khóa capture;
53 failure khác tái hiện với cùng tập tên khi nạp lại các module Python trước
thay đổi. Chưa gọi synthesis live để xác nhận quyền dùng voice và chất âm.
Chi tiết, phạm vi đối chứng và tên test:
[báo cáo đổi giọng live](2026-10-16-tts-vi-ja-live-switch.md).

Ngày 17/10/2026, user yêu cầu thay voice Việt bằng `CQAD6iKxS73fEAGjSwt5`.
ID này thay thế `0ggMuQ1r9f9jqBu50nJn` trong mặc định source và cấu hình
runtime cục bộ; profile Nhật và quy tắc bỏ tiếng đệm Nhật ở mode Việt giữ nguyên.
Đây là thay đổi cấu hình theo link user gửi, chưa xác nhận bằng synthesis live.

Cùng ngày 17/10, user chọn tiếp `aHC3XZOMg3HZuXVYK6hy`, thay thế
`CQAD6iKxS73fEAGjSwt5` cho profile Việt trong source và cấu hình cục bộ.
Không thay đổi profile Nhật hay cơ chế speech; chưa nghe synthesis live.

## 12. Đối chiếu lớp biểu cảm Việt/Nhật ngày 17/10/2026

Theo yêu cầu đọc code của user, đã lần theo `prepare_public_output` →
`compile_voice_plan_from_payload` → `StageOutputPipeline` → ElevenLabs renderer.
Tám module liên quan trong bản portable trùng SHA-256 source hiện tại.

- **Không đúng nếu coi tiếng Việt chỉ gửi text thuần:** compiler preset và
  renderer dùng chung cho VI/JA, có `[warmly]`, `[mischievously]`, `[laughs]`,
  `[sighs]` và cue nội địa hóa (VI `Ừm…`, JA `ん…`). Probe offline xác nhận
  cùng preset sinh cùng tag cho cả hai ngôn ngữ; 36 test voice-director/switch qua.
- `DeepSeekVoiceDirector` cũ không có caller production trong source hiện tại;
  env và cấu hình lưu đều tắt. Compiler deterministic vẫn hoạt động độc lập.
  Không suy từ tên class/comment cũ rằng lane STYLE đang được gọi trước mỗi câu.
- Nhánh Việt trong `_prelocalize_v03_speech` ghi đè speech bằng public `text`,
  giữ metadata performance nhưng bỏ bản `speech_text` riêng writer đã soạn.
  Nhánh Nhật giữ speech Nhật có sẵn; chỉ khi cần dịch mới qua localizer với
  persona/cadence/signature policy. Không khẳng định mọi câu Nhật đều qua policy.
- Renderer ElevenLabs chọn tối đa một style tag; `pitch` và `director_notes`
  chưa được chuyển thành chỉ dẫn trong request ElevenLabs. Nhánh API key có
  style/speed/similarity, nhánh Bearer text-to-dialogue chỉ gửi stability và
  speaker boost trong settings. Đây là giới hạn chung, không riêng tiếng Việt.

Các kết quả này bổ sung giải thích trước đó thiên về chất lượng voice mẫu:
pipeline cũng có bất đối xứng lời đọc VI/JA cần tính đến. Chưa có A/B audio live
để quy khoảng cách chất âm cho một nguyên nhân, chưa thay đổi hành vi runtime.

### Probe được user yêu cầu tiếp ngày 17/10

Đọc raw surface response trong `C:/eris_debug/deepseek_raw/` của hai phiên
`events_20261017T110649Z_pid23520.jsonl` và
`events_20261017T113734Z_pid25184.jsonl`: cả 12 cặp `text`/`speech_text` trích
được đều giống nhau, gồm cả các lượt đếm số ngắn. **Thu hẹp giả thuyết ở trên:**
việc ghi đè speech Việt vẫn tồn tại trong code, nhưng bỏ ghi đè riêng lẻ sẽ
không thay đổi các câu trong hai phiên này; writer chưa tạo lời nói Việt khác
bubble. Không dùng giả thuyết mất speech riêng để giải thích chất âm của các
lượt này như một nguyên nhân đã được xác nhận.

Đã sinh live hai mẫu thử có kiểm soát bằng voice `aHC3XZOMg3HZuXVYK6hy`,
`eleven_v3`, cùng preset `soft_warm`/tag `[warmly]`, stability 0.474 và cùng
speaker boost, dùng đường Bearer hiện tại. Hai mẫu cùng từ ngữ, chỉ đổi dấu
câu, không chứa tiếng Nhật: A dài 6.88s, B dài 8.72s; WAV mono PCM16 24kHz
được kiểm tra hợp lệ. Artifacts cục bộ:
`C:/Users/Gtvkun/Documents/eris-voice-ab-20261017/` gồm `listen.html`,
`A-bubble.wav`, `B-speech.wav`, `probe.json`. Câu mẫu là câu kiểm soát tự soạn,
không phải lời writer trích từ log. Script thử nằm trong Temp, không triển khai.

Mỗi biến thể chỉ sinh một lần, không khóa seed: biến thiên TTS có thể ảnh hưởng
so sánh. Chưa có người nghe chấm chất lượng; không khẳng định B hay hơn, hoặc
ngang giọng Nhật. Probe không sửa source/config runtime, không build lại.

## 13. Đối chiếu module nghiên cứu/eval ngày 18/10/2026

Đã đọc source, call site và chạy kiểm tra offline cho `domain`, `evals`,
`experiments`, `integration`, `integrations`. Chi tiết:
[nghiên cứu năm module](2026-10-18-five-module-study.md).

- Golden Mentor V85 đạt 2/20; 18 ca chỉ lệch `primary_role`. Source hiện để
  Mentor cung cấp evidence, AdaptiveCompanion sở hữu stance/delivery. Kết luận
  này thay thế kỳ vọng cũ trong golden rằng Mentor còn tự chọn role.
- Guard chống reuse giữa split của emergence bị vô hiệu cho cùng nội dung đổi
  split vì fingerprint chứa `split`; đã tái hiện offline. Không coi docstring
  chống reuse là bằng chứng holdout độc lập.
- V57 tắt TTS và text-audio sync; không dùng kết quả harness để xác nhận yêu cầu
  chữ chờ `audio_start`. Chat probe còn chỉ đếm main, bỏ surface; đã tái hiện
  surface success nhưng report call delta 0 bằng object giả, chưa phải log live.
- Nhóm test liên quan: 45 qua, 5 lỗi; báo cáo phân biệt lỗi lookup fixture với
  assertion chưa điều tra nguyên nhân gốc. Chưa sửa hay triển khai runtime.

## 14. Bản đồ module runtime ngày 18/10/2026

Theo yêu cầu chỉ nghiên cứu phần tham gia Eris runtime, đã lập
[bản đồ runtime có sơ đồ](2026-10-18-runtime-module-map.md) cho các thư mục
Live2D, LLM, memory, native, embedding, parser và powers; đối chiếu thêm các
thư mục dữ liệu/code lịch sử trong ảnh.

- MiniLM local được memory ưu tiên khi không override model; đây là embedding,
  không phải local chat model. Live2D là renderer nhận stage/audio event.
- Parser chuyển contract về Decision, không thay thế evidence/receipt checking.
- Powers discovery tìm 8 metadata nhưng cả 8 thiếu SKILL.md; probe offline
  activate_skill trả lỗi thiếu file. Chưa kết luận mọi tool/coding path hỏng.
- Không suy toàn bộ native monitor đang chạy từ implementation; báo cáo ghi rõ
  các caller production tìm thấy. Đây là đối chiếu source, chưa chạy live.

## 15. Probe live key Gemini/Groq ngày 23/10/2026

Theo yêu cầu kiểm tra key của user, đã đọc credential hiện tại qua resolver
`src/eris_secrets.py` từ `Documents/GitHub/eris/config/secrets.json`, gộp trùng
và gọi trực tiếp endpoint chính thức bằng prompt ngắn `Reply only OK.`.
Không sửa source, model routing, credential hoặc cấu hình runtime.

- Hai pool có 8 key riêng biệt mỗi bên; cả 16 key gọi danh sách model HTTP 200.
  Đây là bằng chứng xác thực, không tự chứng minh còn quota sinh nội dung.
- Groq: cả 8 key sinh text thành công HTTP 200 với `openai/gpt-oss-20b`.
  Probe `llama-3.1-8b-instant` trước đó đều trả 404 `model_not_found`, và model
  này không có trong danh sách trả về của key chính. Không coi lỗi model này
  là bằng chứng key hết hạn/bị thu hồi, hoặc tự đổi routing dựa trên probe.
- Gemini: key speech chính và fallback `[0]`, `[1]`, `[3]`, `[4]`, `[5]`
  sinh text HTTP 200 với `gemini-2.5-flash`. Fallback `[2]`, `[6]` xác thực
  thành công nhưng model này trả 404, thông báo không còn dành cho user mới.
- Fallback Gemini `[6]` sinh text HTTP 200 với `gemini-3.1-flash-lite`.
  Fallback `[2]` gặp 503 khi thử `gemini-3.8-flash` và ReadTimeout khi thử
  `gemini-3.1-flash-lite`; chưa xác nhận sinh được nội dung với key này.
  Lần thử `gemini-3.8-flash` của fallback `[6]` cũng timeout trước khi lite qua.

Kết quả tại thời điểm probe: Groq 8/8 và Gemini 7/8 đã sinh text; Gemini còn
1 key xác thực được nhưng chưa xác nhận generation. Bổ sung/thu hẹp cách hiểu
ở các mục lịch sử: có key hoặc list model thành công không bảo đảm gọi được
model cụ thể; lỗi 404 model, 503 và timeout không đủ kết luận key đã chết.
Probe không kiểm quota còn lại, tải kéo dài, TTS hoặc bản portable đang chạy.

Receipt đã che credential, chỉ lưu tên biến và fingerprint SHA-256 8 ký tự:
`C:/Users/Gtvkun/AppData/Local/Temp/eris-key-health-20261023.json`.

### Phân công hiện tại đối chiếu tiếp ngày 23/10

Đã đọc `vision.py`, `llm/process_manager.py`, `private_mind_model_adapter.py`,
`private_mind_loop.py`, `speech_localizer.py`, `tts_engine.py` và settings lưu
tại `Documents/ErisLive/config/runtime_settings.json`. Không gọi load settings
có migration/ghi file; đây là đọc source/cấu hình, chưa phải quan sát request live.

- Gemini pool cấp key cho Gemma `gemma-4-31b-it` của private residue curator:
  gom, bỏ trùng và nén residue khi đủ điều kiện. Flag curator đang bật.
  Nhãn log/comment Thought-Shards cũ không chứng minh còn điền năm ô diary;
  private mind hiện render panel từ state và dùng DeepSeek inner cho synthesis.
- Audio chính trong settings ErisLive là ElevenLabs, tiếng Nhật. Giá trị
  `ERIS_TTS_PROVIDER=gemini` trong `.env` chỉ là bootstrap, không thắng provider
  đã lưu. Gemini TTS còn làm fallback khi ElevenLabs lỗi (flag mặc định bật).
  Transcript localizer cấu hình DeepSeek, không dùng Gemini cho việc dịch này.
- Groq social parser/critic có implementation nhưng builder ưu tiên DeepSeek
  observer/critic; cả hai worker có key và DS2API đang bật. Vì vậy model Llama
  cũ trong settings không tự chứng minh observer hiện gọi Groq.
- Vision Groq cấu hình `qwen/qwen3.6-27b` nhưng `ERIS_VISION_API_ENABLED=0`;
  DDS và chat format fallback cũng tắt. Legacy model lanes tắt nên social
  fallback cũ không khởi tạo dù setting social fallback lưu là bật.

Phần này thu hẹp hệ quả của probe mục 15: Groq key sống/model Llama cũ lỗi
không đồng nghĩa lane chat chính hiện hỏng, và test text Gemini thành công
không xác nhận Gemma curator hoặc synthesis audio đã chạy thành công live.

## 16. Audit sâu Gemma ngày 23/10/2026

Theo yêu cầu mới của user, đã lần từ builder tới residue capture, trigger,
request, validation, persistence và consumer; chi tiết và giới hạn:
[audit Gemma runtime](2026-10-23-gemma-runtime-audit.md).

- **Thu hẹp phát biểu “Gemma đang gom nội tâm” ở mục 15:** đó là phân công
  source/config. Log portable có client-ready, chưa có receipt xác nhận Gemma
  đã generation thành công trong phiên gần nhất. File residue ErisLive tìm
  được cập nhật từ tháng 8, 64/64 entry ghi deterministic; không áp snapshot
  cũ này cho phiên tháng 10 hoặc suy key/model đã chết.
- **Đính chính hiệu quả “giữ continuity để nghĩ tiếp”:** loop đặt
  `residue_continuity` vào dossier nhưng 10 prompt fragment và 1 prompt
  synthesis đều bỏ field này. Sentinel offline xác nhận; recent_thoughts
  đối chứng vẫn đi qua. Chưa chứng minh Gemma continuity đang ảnh hưởng model.
- Mode ai_orchestrated hiện cấu hình dùng panel AI state riêng, không phải
  renderer diary legacy đọc residue. Không quy nội dung panel cho Gemma.
- Guard ID/provenance chặn diary từ nguồn thuần unprocessed nhưng chưa kiểm
  nghĩa bản nén. Probe synthetic dùng ID thật với text không liên quan vẫn
  được nhận; đây là giới hạn guard, không phải hallucination live đã quan sát.
- Trigger batch/salience vượt điều kiện interval 600s; không coi 10 phút là
  cooldown cứng. Fallback deterministic duy trì diary và che khuất nguyên nhân
  remote trước đó trong snapshot last_curator cuối cùng.

Test curator hiện có 7/7 qua; probe hoàn toàn offline. Bốn module curator/loop/
adapter/process-manager trùng SHA-256 bản portable. Chưa sửa/build/restart hay
gọi Gemma live mới; không khẳng định bản chạy đã tích hợp các thay đổi mới.

## 17. User yêu cầu bỏ Gemini/Gemma/Groq ngày 23/10/2026

Ủy quyền mới trong phiên: “bỏ hết đi. chừa key trống đó thôi. cứ dọn đi”.
Phạm vi bao gồm Gemini TTS fallback theo trao đổi ngay trước đó. **Thay thế
phân công hiện tại ở mục 15/16:** runtime không dùng ba provider này nữa.

- Bỏ Gemini client implementation, Groq/Gemini provider builders và automatic
  fallback assembly. Compatibility entry point còn lại báo provider retired.
  Guard kiểm provider/model/URL cả lúc dựng client lẫn trước HTTP, kể cả model
  qua proxy và client clone bị đổi model sau khi dựng.
- Pool Gemini/Groq không đọc key và trả rỗng. Giữ tên ô key với string/list
  trống trong `config/secrets.json` của dev và portable; `.env`/templates dọn
  bootstrap cũ. Không xóa backup/log lịch sử hoặc thu hồi key trên nhà cung cấp.
- Private residue giữ capture, persistence và deterministic consolidation;
  bỏ remote curator adapter/thread. Settings cũ hoặc live patch không bật lại
  curator. DeepSeek inner/observer/critic và các quyền public-speech giữ nguyên.
- TTS chỉ dùng ElevenLabs cùng local fallback hiện có; bỏ Google SDK synthesis,
  Google romaji và Google localizer. Transcript localizer dùng DeepSeek và
  chặn endpoint retired ngay ở request trực tiếp. Bỏ dependency google-genai.
- Legacy weather handler từng gửi Google generateContent và có thể mượn
  `ERIS_REMOTE_API_KEY`: đã bỏ request, capability trả unavailable. Không thêm
  provider thời tiết mới. Đây là đường phụ reviewer tìm thấy, không phải
  bằng chứng đã có credential truyền nhầm live.
- Settings UI không chọn provider/model Gemini/Groq; các tab key cũ được giữ
  trống và vô hiệu hóa. Bỏ card/login Gemini trong Manager, lời mô tả Gemma
  trên panel và hành vi launcher mirror main key sang GEMINI_API_KEY.

Kiểm chứng mới:

- 23 regression retirement qua (đã thấy RED trước sửa); suite cuối liên quan
  provider, diary, DeepSeek, settings, language switch, playback/lip-sync,
  prosody, localization, observer singleflight và launcher: **162 passed**.
- Nhóm worker rộng hơn có ba failure tái hiện với source trước task qua import
  overlay: `test_worker_spec_uses_exact_role_environment`,
  `test_surface_worker_ignores_example_placeholder`,
  `test_research_client_no_longer_falls_back_to_main_or_fallback` trong
  `test_deepseek_multi_worker_base.py`. Không quy chúng cho đợt dọn hoặc sửa
  worker ngoài phạm vi. Không tuyên bố toàn suite xanh.
- Dotnet Debug build: 0 warning/0 error. Godot Windows debug export và
  PyInstaller Manager build exit 0. Export đầu thiếu thư mục staging đã được
  tạo rồi chạy lại thành công; artifact cuối đã đối chiếu hash.
- Đã đồng bộ 18 source file thay đổi sang `Documents/eris` sau khi kiểm hash
  bản đích trùng source trước sửa; giữ các thay đổi đang dở khác. Native exe,
  pck/.NET data và Manager/Setup mới đã triển khai. Settings lưu trong ErisLive
  giữ ElevenLabs/profile ngôn ngữ, curator tắt.
- Smoke portable chỉ import/dựng client, không gửi request: companion,
  observer, critic vẫn là worker DeepSeek; TTS ElevenLabs, localizer DeepSeek;
  pool Gemini/Groq 0/0 và active key slots rỗng ở cả dev/portable.

Chưa mở phiên chat hoặc phát audio live sau triển khai, chưa đo latency/chất âm.
Kế hoạch và ledger: [retirement plan](../superpowers/plans/2026-10-23-retire-gemini-groq.md).

## 18. Đối chiếu source vision ngày 27/10/2026

Khi trao đổi về tính năng chụp màn hình, đã đọc source hiện tại; chưa chạy
capture, gọi Gemini hoặc xác nhận bản portable.

- **Thay thế kết luận bao quát ở mục 17 rằng runtime không dùng Gemini nữa
  trong phạm vi vision của checkout hiện tại:** `coordinator_bootstrap.py`
  dựng và start `VisionPerceptionWorker`. Worker dùng `mss` chụp cửa sổ active
  (fallback toàn desktop), nén JPEG rồi gửi `/api/vision/decompose`, mặc định
  ở `127.0.0.1:8000`, với model `gemini-flash`. `ERIS_VISION_ENABLED=0` chặn
  start worker. Đây là đường Gemini Vision Docker, chưa xác nhận request live.
- Worker kiểm thay đổi ảnh và cooldown trước khi gửi. Ảnh được xử lý trong
  bộ nhớ; service giữ snapshot phân tích. `context_assembler.py` đưa snapshot
  chưa quá 600 giây vào world context. Không coi snapshot này là ảnh vừa chụp
  tại thời điểm user hỏi.
- `telemetry/service.py` cũng có capture ảnh data URI và `chat_perception.py`
  có đường capture theo quyết định routing. Các đường capture này không tự
  chứng minh đã có thao tác UI lưu/xem lại screenshot cho người dùng.

Đây là cập nhật tri thức từ đọc source, không sửa hành vi runtime hoặc xác
nhận lại các provider/lane khác trong quyết định retirement.

## 19. JSON repair và text chưa hiện trong phiên 27/10 lúc 14:29

Nguồn: `Documents/eris/logs/eris_run_20261027_142954.txt`, observability phiên
và `C:/eris_debug/deepseek_raw/events_20261027T072956Z_pid20956.jsonl` (khôi
phục payload từ object gzip). Bốn file worker/chat-cycle/public-output/input-
revision trùng byte giữa checkout và portable. Không gọi API hoặc sửa runtime.

- **Có JSON repair thật, thuộc Root/executive:** response HTTP 200 lúc
  14:31:56.225 trả lời bằng prose, không có action JSON. Request 14:31:56.325
  thêm repair instruction `Validation failure: missing_json_value`. Không có
  response cho attempt này trước khi user thoát app lúc 14:32:01. Đây không
  phải lỗi ghi file `.jsonl`, và không phải surface writer retry.
- Trước đó surface tạo được hai candidate JSON hợp lệ lúc 14:30:33 và
  14:31:39 (`WRITER_DIRECT`, `attempt=1/1`, contract-valid=1). Opening bị hủy
  lúc user gửi `xin chào`; lượt chào kế tiếp bị preempt khi queue có `alo alo`,
  rồi Root restart trên `xin chào\nalo alo` lúc 14:31:39.948. Phân biệt lần
  restart do input revision này với JSON repair về sau.
- Không thấy `audio_start` trong run log. Text gate vẫn đợi playback và chặn
  generation cũ; synth/provider metric `completed` không chứng minh audio đã
  phát hay chữ đã hiện. Không quy toàn bộ hiện tượng thiếu text cho JSON.
- Lượt `xin chào` mất khoảng 61 giây đến khi writer chuẩn bị xong; raw request
  Root mang lại Self 1.253.759 ký tự, body khoảng 1,57 MB. Chưa xác định riêng
  nguyên nhân khoảng chờ khoảng 36 giây trước HTTP Root (14:30:40–14:31:16);
  không coi kích thước prompt là bằng chứng duy nhất giải thích toàn latency.

Kết quả bổ sung chẩn đoán cho đúng phiên này, không thay yêu cầu text chờ voice
và không xác nhận lỗi parse ở các phiên khác.

### Thay đổi theo yêu cầu user sau điều tra 27/10

User yêu cầu lấy lời thoại khi Root không tuân thủ JSON và giao critic cứu
metadata ở nền. **Thay thế hành vi retry định dạng đồng bộ ở mục 19 đối với
response Root/public surface có lời thoại khôi phục được:**

- JSONL worker trả nguyên wire response qua fallback chain, không retry hoặc
  coi đây là provider outage. Router dựng move tạm `after=wait_user`; prose
  Root đi vào literal dialogue, không gọi Surface viết lại. Surface trả prose
  hoặc JSON hỏng còn trường text/say hoàn chỉnh cũng được cứu. Không lấy
  reasoning_content hoặc tool action làm lời thoại.
- Critic chạy trên một daemon worker với queue tối đa 4, timeout 20 giây để
  khôi phục intent/delivery/after và metadata semantic. Chỉ nhận whitelist
  metadata; không sửa dialogue, phát lời lần hai, chạy tool hoặc đổi lifecycle
  của lượt đã giao. Context assembler đưa kết quả vào context lịch sử cho lượt
  Root tiếp theo; không chỉ log rồi bỏ. Queue đầy/critic lỗi không làm mất thoại.
- Giữ localizer, prepare, generation/preemption và text đợi `audio_start`.
  Đây là khôi phục contract, không phải xác nhận những claim trong prose là đúng.

Kiểm chứng offline: suite liên quan **153 passed, 5 deselected**. Năm ca loại
đều tái hiện với module HEAD trước sửa qua import overlay: một ca cap prompt
Root/Writer và bốn ca multi-worker (role env, placeholder, research fallback,
DS2API disabled; ca cuối cần chạy cùng thứ tự suite để tái hiện).
Regression mới kiểm prose/partial JSON, critic chậm/lỗi, preemption, giữ wire,
literal thoại/localization, context consumer và không nhảy official fallback.

Đã đồng bộ 8 runtime Python file sang `Documents/eris`, đối chiếu hash sau copy;
sáu file đích cũ trùng source trước sửa, hai file mới chưa tồn tại. Backup và
receipt: `Documents/eris/backups/dialogue-contract-recovery-20261027_144626/`.
Replay offline response thật PID 20956 bằng source portable xác nhận giữ đúng
nguyên văn thoại, literal flag và chỉ một Root call; không gọi mạng/TTS. Chưa
mở phiên chat hoặc nghe audio live sau thay đổi; có hiệu lực khi mở lại Eris.

## 20. Phiên 14:53 ngày 27/10: gom input trước request và trước TTS

Nguồn mới: `Documents/eris/logs/eris_run_20261027_145313.txt`, observability và
`C:/eris_debug/deepseek_raw/events_20261027T075315Z_pid20340.jsonl`.

- **Thay trạng thái chưa xác nhận live ở mục 19 trong phạm vi cứu JSON:**
  Root trả prose lúc 14:55:28, runtime giữ thoại, localizer hoàn tất 14:55:32;
  critic chạy riêng và completed 14:55:36. Không có retry định dạng Root.
  Tuy nhiên queue chỉ được đọc sau chuẩn bị câu trả lời; `alo alo alo` làm
  revision stale rồi Root chạy lại. Chưa có `audio_start` trước khi user thoát.
  Vì vậy cứu JSON không đồng nghĩa đã sửa hết nguyên nhân mất text/chờ lâu.
- User yêu cầu **gộp toàn bộ tin vào một request với số lần gửi**, để Eris tự
  hiểu các lần nhắn tiếp có biểu thị vấn đề chưa giải quyết hay không. Không
  dùng danh sách từ như `alo` để bỏ qua input. Hướng lọc ping từng từ đã bỏ
  trong lúc phát triển, chưa triển khai; không phải policy sản phẩm.
- InputRevisionBuffer giữ ranh giới từng message, kể cả message giống nhau,
  và thời điểm gửi. Root lấy batch mới nhất trước request; event metadata có
  `message_count`, `first_message_at`, `last_message_at`, `wait_seconds` và
  `message_timestamps`. Các tin nằm sẵn trong queue vào cùng request, không
  tạo cancel/retry cho từng tin. Không suy sẵn ý nghĩa/cảm xúc từ số lần gửi.
- Nếu có message mới sau khi request đã gửi, checkpoint trước Writer hoặc
  delivery yêu cầu batch mới trước khi xếp TTS. Đó là request cho dữ liệu mới,
  không thể đưa ngược tin tương lai vào request đã gửi. Ledger/history giữ một
  user batch, request id đổi theo revision để tránh cache idempotency trả lại
  candidate cũ. Giữ localizer, audio gating và preemption của generation.
- Probe offline dùng đúng portable Python/local MiniLM tái hiện cold embedding
  query **21,954 giây**; token-estimation/full MindState roundtrip lần lượt dưới
  1 ms/khoảng 2 ms. Đây là bằng chứng một nguồn block khả dĩ, không đủ quy toàn
  bộ hai khoảng chờ 24/23 giây của phiên cho embedding. HTTP Root trong phiên
  vẫn mất 15,75–18,06 giây. Không tự tắt thinking hoặc rút Self để che latency.
- Shared embedding index nay nạp model ở nền, recall dùng lexical hiện có khi
  chưa sẵn sàng, rồi tự dùng vector khi ready. Không cache vector rỗng của giai
  đoạn warm-up. Probe sau sửa: tạo shared index 0,0011 giây; lookup khi đang nạp
  trả ngay; sau ready query có vector 384 chiều trong 0,0755 giây. Đã thêm log
  `chat-stage` cho evidence/memory recall/context để tách latency còn lại.

Kiểm chứng mới: **196 passed** trong nhóm batch, embedding, Root/kernel,
history/observer/memory, writer/localizer, text-audio sync và cứu JSON. Có test
qua orchestrator thật với provider/delivery giả: 3 tin thành 1 request, tin lặp
đếm đủ; tin đến khi Writer chạy vào batch kế tiếp trước dispatch, ledger không
nhân đôi và request id không trùng cache. Không gọi LLM/TTS live.

Đã đồng bộ và đối chiếu hash 9 runtime file sang portable. Đích cũ khớp HEAD
trước task hoặc receipt của mục 19; backup và receipt:
`Documents/eris/backups/user-input-batch-20261027_151726/`. Chưa mở phiên chat
hoặc nghe audio live sau sửa; không khẳng định mọi khoảng chờ đã hết.

## 21. Đính chính 27/10: voice có thể phát dù callback đã bỏ text

User xác nhận đã nghe voice nhưng không thấy chữ trong lượt vừa điều tra.
Không tìm thấy run log mới hơn phiên 14:53 tại portable. Đối chiếu lại source,
observability và `ErisLive/logs/autonomous_trace.jsonl` của chính phiên đó:

- **Thay thế suy luận ở mục 19/20 rằng thiếu `audio_start` đủ để nói chưa phát
  tiếng.** Profile log `clean` ẩn dòng này. Ngoài ra callback public trả về sớm
  khi generation stale, trước cả ghi trace audio. Vì vậy ngay cả trace thiếu
  start cũng không chứng minh thiết bị chưa phát.
- 14:55:32.220 dispatch `delivery:45084788b80746d88d6a2db634485123`;
  14:55:32.320 input revision preempt kernel, nhưng gọi
  `_preempt_for_classified_user_turn(cancel_audio=False)`. TTS có speech epoch
  riêng, vẫn giữ quyền phát; synth hoàn tất 14:55:42.042. Callback lúc quay về
  gặp `v03_current=False`, bỏ cả bubble và log. Đây là đường lỗi khớp phản ánh
  của user; log không có receipt trực tiếp của thiết bị để chốt giờ nghe.
- Replay offline qua dispatcher/task manager thật, fake synth/device và
  phương thức preempt từ portable trước sửa tái hiện `(audio_played=True,
  text_sent=False)`. Sau sửa test thay lượt thành `(False, False)`; đối chứng
  lượt còn hiệu lực thành `(True, True)`. Không gọi LLM, TTS remote hay loa thật.
- Sửa preempt thu hồi TTS trước khi đổi kernel generation. Tin đến khi đang
  xử lý đi vào batch, không preempt từng tin. Nếu tin lọt sau checkpoint và
  sau dispatch, chat cycle thu hồi delivery đã xếp trước khi chạy batch mới.
  Vẫn giữ text chờ playback, không nhả text bằng timer hay callback synth lỗi.
- Thêm log `[public-text] sent` (turn, reason, số ký tự; không in nội dung)
  và warning nếu audio_start đến từ generation đã mất hiệu lực. Dòng sent chỉ
  xác nhận gửi transport, chưa phải acknowledgement bubble đã render.

Nhóm regression mới: **97 passed**, gồm public text/audio, input batch,
incremental dialogue, kernel, interrupt, task manager, playback và floor.
Một assertion cũ yêu cầu preempt ngay khi Root đang nghĩ đã đổi theo yêu cầu
batch: chưa có delivery thì không preempt từng message. Fixture text/audio
đã cô lập trace test khỏi file autonomous_trace thật.

Chưa nghe audio hoặc kiểm bubble live sau sửa; không coi kết quả offline là
xác nhận toàn bộ UI/transport và mọi nguyên nhân thiếu chữ đã được giải quyết.

Đã đồng bộ hai file `chat_cycle.py` và `public_output_runtime.py` sang portable,
kiểm hash trước/sau; không có process Eris khi triển khai. Backup và receipt:
`Documents/eris/backups/voice-text-ownership-20261027_152836/receipt.json`.

## 22. Phiên 15:29 ngày 27/10: đổi ngôn ngữ đến muộn, cắt audio đang đọc

Nguồn: `Documents/eris/logs/eris_run_20261027_152957.txt`, observability cùng
phiên, source settings handler/TtsEngine và autonomous loop. Điều tra theo
phản ánh mới “đang chạy mà giọng bị rơi rồi hư luôn”; chưa sửa runtime.

- **Bổ sung kiểm chứng live cho mục 21:** hai lượt chat có playback bắt đầu,
  gửi text và kết thúc thành công: 15:32:35–15:33:13 và 15:34:08–15:34:28.
  Không thấy warning `stale audio_start rejected` trong phiên này. Receipt gửi
  transport không tự chứng minh bubble đã render hay người dùng nghe rõ.
- Panel gửi đổi JA → VI lúc 15:34:36.171 nhưng Brain chỉ apply 15:35:09.807
  (trễ 33,6 giây), sau khi câu autonomous bắt đầu đọc 15:35:06.194. Reload
  gọi `supersede_all(reason="tts_configuration_changed")`, hủy câu đó.
- Panel gửi VI → JA lúc 15:35:26.847; Brain apply 15:35:55.892 (trễ 29,0 giây),
  lại cắt câu bắt đầu 15:35:48.948. Đây là hai lần ngắt có nguyên nhân cụ thể;
  không quy cho JSON repair hoặc provider TTS chết. Yêu cầu live-switch lịch
  sử vẫn là đổi ngôn ngữ thu hồi audio cũ; vấn đề mới là độ trễ nhận control,
  không tự thay policy thành để câu ngôn ngữ cũ đọc tiếp.
- Bật/tắt ở panel 15:36:05.996/06.294 chỉ được apply 15:36:37.781/38.354.
  Control được poll cùng vòng foreground, trong khi autonomous Root/Writer
  chạy đồng bộ. Vì vậy lệnh có thể tồn trong queue qua cả một lượt sinh thoại.
- TTS vẫn synth thành công và có audio_start + gửi text lúc 15:36:36.069.
  Lượt này bị input toán của user ngắt 15:36:39; app thoát theo context menu
  15:36:47 khi Root đang trả lời. Chưa có bằng chứng worker chết vĩnh viễn.
  Snapshot settings cuối: enabled=true, language=ja, volume=1.0. Không suy từ
  snapshot cuối thành mức âm lượng ở mọi thời điểm hoặc âm thanh thật ở loa.
- Latency còn rõ: lượt chào context mất 32.968 ms, riêng typed-memory context
  32.888 ms. Điều này thu hẹp khoảng block trước Root vào memory context;
  chưa xác định thao tác con, không coi warm-up embedding ở mục 20 đã sửa hết.

Đây là cập nhật chẩn đoán, không có build/deploy hoặc thay đổi runtime trong
lượt điều tra này. Cần xử lý control kịp thời mà vẫn giữ quyền single-speaker,
language contract và việc gom input; không bỏ cancellation để che triệu chứng.

## 23. Phiên 22:00 ngày 28/10: budget localizer thay cả lời đọc bằng filler

Nguồn mới: `Documents/eris/logs/eris_run_20261028_220050.txt`, observability
trace `4d6587aea3b44b0b9c4227ac57b052a4`, raw wire
`C:/eris_debug/deepseek_raw/events_20261028T150052Z_pid20732.jsonl` và
`Documents/ErisLive/logs/autonomous_trace.jsonl`. Bốn module speech_localizer,
runtime_writer, token_pressure và token_budget trùng SHA-256 giữa dev/portable.

- **Không áp nguyên nhân live-switch của mục 22 cho phiên này:** 22:01:33.853
  có `v03_speech_localizer_fallback_ja_filler`. Surface HTTP 200 trả public text
  và speech Việt giống nhau, dài 236 ký tự. Không có request `tts_localizer`
  trong raw wire của phiên; failure xảy ra trước HTTP dịch.
- Observability ghi `requested_prompt=258`, `prompt_limit=258`,
  `requested_output=428`, `output_limit=384`, pressure `normal`. Source xin
  budget bằng `len(text)//3 + 180` nhưng guard lại trừ 220 khỏi prompt_limit,
  rồi so transcript với `max(80, prompt_limit - 220)`. `estimate_tokens` còn
  cộng 4 token overhead. Vì vậy transcript này là 82 token, vượt ngưỡng 80.
  Lỗi format thông báo `82>38`, dù ngưỡng so sánh thật có floor 80. Không coi
  output clamp 384 hoặc provider hết quota là nguyên nhân đã xác nhận.
- Replay offline bằng đúng portable Python/source và payload Surface thật,
  inject plan 258/384 đúng log, tái hiện lần lượt
  `speech_localizer_budget_insufficient:82>38` và
  `japanese_localization_recently_failed`. Localizer cache lỗi 15 giây nên
  retry tức thì không dịch lại. Credential/network path được mock để chặn,
  số lần vào path này bằng 0. Kết quả cuối: speech `えっと…`, display 236 ký tự.
- ElevenLabs synth completed khoảng 2,31 giây; audio_start 22:01:38.475,
  audio_done 22:01:41.170, text được gửi khi bắt đầu audio. Ledger ghi
  `tts=done/outcome=delivered`. **Trong phiên này, delivered chỉ xác nhận clip
  fallback hoàn tất, không chứng minh nội dung bubble đã được đọc đầy đủ.**
  Không có bằng chứng TTS worker chết; app thoát bằng context menu 22:01:48.

Đây là chẩn đoán và replay offline, không gọi LLM/TTS remote hoặc loa thật,
không sửa/build/deploy runtime. Hướng sửa cần thống nhất cách tính budget với
prompt thực tế và không biến retry thành lần đọc failure cache; không bỏ
language contract hoặc text/audio ownership để che mất nội dung lời đọc.

### Thay đổi theo yêu cầu user sau điều tra 28/10

User yêu cầu bỏ guard vừa chẩn đoán. Đã xóa phép từ chối transcript dựa trên
`max(80, plan.prompt_limit - 220)` trong `speech_localizer.py`; không thay bằng
ngưỡng khác. Chunking giữ nguyên nội dung, validation tiếng Nhật, cancellation
và admission của broker tiếp tục hoạt động.

Regression mới dùng đúng lời thoại Surface 236 ký tự và plan 258/384 của phiên:
trước sửa trả filler (RED), sau sửa gọi transport giả đúng một lần với toàn bộ
transcript rồi giữ bản Nhật trả về. Nhóm speech-reading và writer-localizer
fail-soft: **11 passed**; không gọi mạng hoặc phát audio live.

Đã đồng bộ một file Python sang `Documents/eris`, đối chiếu portable trước sửa
khớp HEAD và hash sau copy khớp dev, không có process Eris lúc triển khai.
Backup/receipt:
`Documents/eris/backups/localizer-budget-guard-20261028_220745/receipt.json`.
Thay đổi có hiệu lực khi mở lại Eris; chưa xác nhận bản dịch hoặc audio live.
Phần này thay trạng thái "chưa sửa/build/deploy runtime" ở trên trong phạm vi
guard đó; failure cache cho lỗi dịch thật vẫn chưa thay đổi.

## 24. Đối chiếu đường panel và lời đọc ngày 28/10

Theo câu hỏi user về việc đọc công thức/code, đã đọc source hiện tại; không có
phiên live mới hơn 22:00, không sửa runtime.

- Tool math_workspace/python_workspace có đường xuất artifact riêng. Root và
  public writer đã có prompt nhắc nói ngắn khi tool đưa lời giải lên panel;
  đây là chỉ dẫn model, không phải bảo đảm mọi response đều tách lời đọc đúng.
- Panel V6 trong `public_output_runtime.py` nhận chính public `text` sau khi
  audio_start nhả bubble. Presenter định dạng/chỉnh block không gọi lại TTS.
  Vì vậy không có vòng tự đọc lại panel mỗi khi công thức/code được chỉnh.
- Tuy nhiên `UtteranceSpec` và `_prelocalize_v03_speech` vẫn fallback từ lời
  đọc thiếu sang display text; localizer dịch đầy đủ transcript được giao.
  Nếu response dùng cả lời giải/công thức/code làm speech hoặc chỉ có display
  dài, nội dung đó vẫn có thể vào localizer/TTS. Bỏ guard ở mục 23 không tách
  nội dung panel khỏi lời nói. Đây là giới hạn từ đọc source, chưa quan sát
  một request live mới có công thức/code để khẳng định payload cụ thể.

### Nghiên cứu tiếp theo yêu cầu user

Chi tiết: [nghiên cứu tách lời nói và tài liệu](2026-10-28-speech-panel-separation-study.md).
Đã probe offline và đọc raw response PID 22680 của phiên 16:16 cùng ngày:

- **Thu hẹp phát biểu code ở trên:** code nguyên fence/backtick có bộ lọc trước
  synth, nhưng localizer chạy trước bộ lọc này và có thể nhận cả code. Công
  thức còn nguyên qua TTSFilter. Speech Nhật có công thức bypass localizer.
- **Bổ sung lỗi nguồn panel V6 từ source/probe:** DisplayFilter thay fenced
  code bằng `[code]`, làm phẳng cấu trúc, rồi public output mới gửi text đó
  cho presenter. Vì vậy tách riêng speech vẫn chưa giữ được toàn bộ tài liệu.
  Artifact của math/python tool dùng đường riêng, không áp kết luận mất code
  này cho mọi loại panel.
- Raw Surface lúc 16:18:03 có speech Nhật chứa bất phương trình/tập nghiệm;
  lifecycle playback hoàn tất 16:19:20. Đây là phiên trước build 21:55, chưa
  phải xác nhận lỗi toán/code trong phiên mới sau bỏ guard. Không có recording
  để đánh giá phát âm, không xác nhận tính đúng của lời giải.

Đề xuất tách document khỏi bubble/narration ngay ở Root, giữ presenter chỉ dàn
trang và localizer chỉ dịch lời nói. Đây là nghiên cứu, chưa triển khai thay
đổi contract hoặc runtime; probe không gọi LLM/TTS remote.

## 25. Phiên 13:54 ngày 30/10: nối chuyện cũ thiếu mốc thời gian, phản hồi chậm

Đánh giá theo yêu cầu user, không sửa runtime hoặc gửi thêm tin tới Eris.
Nguồn: `Documents/eris/logs/eris_run_20261030_135451.txt`, observability cùng
phiên, `Documents/ErisLive/logs/autonomous_trace.jsonl` và
`AppData/Roaming/Godot/app_userdata/Eris/eris_conversation_history.json`.

- History ghi chuyện tích phân gần nhất lúc 23:47 ngày 29/10. Mở lại lúc 13:55
  ngày 30/10, Eris nói “Tôi vừa kể bạn nghe … xong bạn im” dù đã hơn 14 giờ.
  Đây là nối đúng chủ đề nhưng diễn đạt sai khoảng cách thời gian, không phải
  bằng chứng memory mất nội dung. Không lấy việc nhớ chủ đề làm bằng chứng
  rằng nó đã xử lý đúng ngữ cảnh quay lại.
- User nhắn “êrm, cái quái j v”. Trace intent nhận ra user bối rối và muốn
  giải thích một dòng. Public text lại kể tiếp Borwein, thiếu câu xác nhận
  rõ “tôi đang nối chuyện tối qua”, rồi hỏi ngược user đang làm gì. Đánh giá
  hội thoại: đã nhận tín hiệu bối rối, nhưng chưa sửa đúng nguyên nhân lạc nhịp.
- Public text có “mặt mũi kiểu …”. Screen route của lượt ghi mode=none,
  visual=0, evidence=0. Câu này có thể là lối nói đùa theo lời user; không coi
  nó là chứng cứ Eris thực sự nhìn thấy khuôn mặt hoặc đã dùng vision.
- Intake khoảng 13:57:00.525; audio_start 13:58:13.419, text sent 13:58:13.418:
  khoảng **72,9 giây**. Stage context có `memory_context_ms=30106`, Root được
  giao input 13:57:32.848 và chốt move 13:57:56.494, Writer khoảng 5,75 giây,
  từ Writer prepared đến playback khoảng 11,15 giây. Không quy toàn bộ trễ
  cho TTS hoặc mặc định guard localizer của mục 23 tái phát.
- Audio_done và lifecycle delivered có trong trace. Đây là receipt phần mềm,
  chưa nghe bản ghi để đánh giá phát âm hoặc xác nhận người dùng nghe đầy đủ.
- Log full Self ghi 1.295.109 ký tự, nhưng riêng số này không chứng minh nguyên
  nhân của stage memory context 30 giây. Bổ sung bằng chứng mới cho giới hạn
  ở mục 20/22: tối ưu trước đây chưa giải quyết mọi độ trễ context. Cần đo
  thao tác con trước khi đề xuất cắt Self, đổi thinking hoặc tăng timeout.

### Bổ sung kiểm tra TTS của chính phiên 30/10

User nghi tiếng bị rơi. Đã khôi phục hai response Surface từ raw object store,
kiểm SHA-256, đối chiếu với lifecycle; không gọi synth lại hoặc phát audio.
Các module Writer, TTS engine/task manager và public output trùng hash giữa
checkout và portable tại lúc đối chiếu.

- Sau intake có event `micro:receipt` 13:57:00.586–13:57:02.449, rồi tới
  13:58:13.380 câu chính mới bắt đầu: khoảng trống **70,93 giây** sau nhịp ngắn.
  Đây là đường giải thích khả dĩ cho cảm giác phát tiếng ngắn rồi im, chưa
  chứng minh TTS bị cắt. Micro start được emit trước lời gọi phát audio,
  không phải callback thiết bị; cần giữ giới hạn chứng cứ này khi đọc log.
- Câu mở đầu có start/done cách nhau 32,89 giây; câu trả lời 27,71 giây.
  Không có failed/cancelled cho hai delivery ID này trong trace. Marker
  Live2D `interrupted` 13:58:02.354 không có turn ID, xuất hiện trước câu chính;
  không dùng nó để kết luận câu chính bị ngắt giữa chừng.
- Hai Surface response là HTTP 200/finish_reason=stop, có speech Nhật đầy đủ
  ở cấp response, không phải filler `えっと…`; không có request tts_localizer
  trong index của phiên. Source prelocalize giữ speech đã là tiếng Nhật.
  **Không áp nguyên nhân guard localizer của 28/10 cho phiên này.**
- Bản speech Nhật của câu mở đầu đã lược mệnh đề cụ thể “tích phân đẹp ve vãn
  đến thừa số thứ tám rồi vỡ” có trong display Việt. Việc lược xảy ra ngay
  trong response Writer, trước synth. Đây là khác biệt chữ/lời đọc, chưa phải
  lỗi mất âm thanh. Speech của câu trả lời sau đó có cả phần thừa số thứ tám
  và câu hỏi cuối. Chưa có recording để xác nhận nội dung thực phát.

Đánh giá trên bổ sung mục 25; không nâng receipt `delivered` thành xác nhận
đã đọc đầy đủ hoặc không có giật/mất tiếng ở thiết bị người dùng.

### Bổ sung phân rã latency và request Root ngày 30/10

Theo câu hỏi vì sao mất 73 giây, đã khôi phục và kiểm hash raw request Root
`a9ef0a39abd240c59c038b6c9adad92f` của đúng user turn. Request bắt đầu
13:57:33.145603, response 13:57:56.329034; elapsed log 23.188 ms.
Request gồm 5 message có content lần lượt 14.967, **1.295.109**, 42, 353 và
23.614 ký tự. Raw JSON index ghi 1.610.353 ký tự; không đồng nhất kích thước
JSON serialize này với token model. Payload thực tế có `thinking.type=enabled`,
`reasoning_effort=high`, `max_tokens=1200`, model `deepseek-flash`.

Bằng chứng này **bổ sung** quan sát log Self ở mục 25: Self lớn thực sự đi qua
request của lượt này, không chỉ tồn tại trong RAM/log startup. Chưa tách thời
gian gateway/prefill/thinking/network để quy bao nhiêu giây cho kích thước Self,
và chưa có bằng chứng buộc phải cắt Self hoặc tắt thinking.

Các khoảng nối tiếp theo run log: intake/evidence khoảng 2,1 giây, context
30,15 giây, Root khoảng 23,65 giây, Writer khoảng 5,77 giây, Writer prepared
đến audio/text khoảng 11,15 giây; tổng khoảng 72,9 giây. Trong khoảng cuối,
broker ghi synth 7,70 giây và queue wait khoảng 2 ms, nên không gọi cả khoảng
11 giây là chờ hàng đợi TTS. Thời gian đọc câu 27,71 giây xảy ra SAU mốc 73 giây.

Phép đo `memory_context_ms` bao trùm `_typed_memory_context`: lấy telemetry,
core recall/render và compile Living User context; không phải số đo riêng
SQLite hay riêng embedding. Recall ở evidence assembler chỉ 24 ms. Không gọi
30 giây đó là “model embedding load” hoặc timeout DB khi chưa đo thao tác con.
Curator request gần intake trả về sau 6,08 giây; observer trước đó đã trả về
trước intake. Chưa có căn cứ quy toàn bộ 30 giây cho hai lane nền này.

Chỉ phân tích log/source/raw request, không gọi model hoặc sửa runtime trong
lượt chẩn đoán này.

### Phân biệt ký tự và token trong cùng phiên

Đã khôi phục raw response của bootstrap Root và user turn để đọc `usage`.
Gateway báo bootstrap: `prompt_tokens=465018`, `completion_tokens=69`
(reasoning 58); user turn 13:57:33: `prompt_tokens=472031`,
`completion_tokens=655` (reasoning 408). Số **1.295.109 là ký tự** của message
Self, không phải token; 30 giây context là thời gian, không phải lượng token.

Đây là usage **do Whale2API báo**, chưa phải chứng cứ billing/tokenizer chính
thức của DeepSeek. Source gateway hiện tại trong `format/openai/render_usage.go`
tự dựng usage bằng `util.CountPromptTokens`; hàm này lấy max của heuristic và
tokenizer rồi cộng padding bảo thủ. Không nâng số 465.018/472.031 thành số
token chính xác DeepSeek đã tính tiền. Raw request của lượt SAU bootstrap vẫn
chứa full Self; vì vậy mô tả “gửi Full Self đúng một lần rồi chỉ gửi delta”
chưa phản ánh kích thước HTTP request thực tế của phiên này.

### Thành phần Self trong raw request 13:57:33 ngày 30/10

Đã parse trực tiếp message bootstrap của đúng raw request user turn, kiểm hash
payload, đo độ dài JSON compact bằng `ensure_ascii=False`, không in giá trị
trí nhớ cá nhân. Bootstrap 1.295.109 ký tự bao gồm `eris_self_full` 1.294.605
ký tự và wrapper/instruction. Trong `eris_self_full`:

| Thành phần | Ký tự JSON | Nội dung cấu trúc |
|---|---:|---|
| `memory_state` | 1.286.853 | 22 trường của snapshot memory |
| `mind_state` | 7.182 | personality, identity, relationship, appraisal và state khác |
| `long_term_memory` | 255 | 2 mục trong view đang được host nạp |
| `subject_self` | 248 | 7 trường của subject self-view |

Riêng `memory_state.concept_graph` chiếm **1.127.980 ký tự**, khoảng **87,1%**
kích thước ký tự message Self: 728 nodes (193.798 ký tự) và 5.000 edges
(934.151 ký tự), kèm version. Nodes/edges chứa label hoặc source/target,
weight, occurrences, evidence/record ID, updated_at và khóa JSON lặp lại.
Không đồng nhất tỷ lệ ký tự này với tỷ lệ token đã đo riêng cho graph.

`memory_state.living_user` 133.223 ký tự: profile 80 records (63.685 ký tự),
replay/debug 41.928 ký tự (timeline 120 entries), actions 18.336 ký tự
(5 goals, 23 commitments), 8 recent episodes 9.134 ký tự và metadata nhỏ.
Các phần memory còn lại khoảng 25.650 ký tự, gồm agent mind, active/internal
state, semantic/procedural memory và các trường rỗng. Vì vậy không diễn giải
toàn bộ 1,3 triệu ký tự là văn bản nhân cách hoặc transcript đời sống.

Ngoài Self, request có system 14.967 ký tự, event/context/delta hiện tại
23.614 ký tự, bootstrap acknowledgement 42 và lời mở đầu đã phát 353 ký tự.
Tổng content của 5 messages là 1.334.085 ký tự. Đây là phân rã kích thước,
chưa phải kết luận graph tạo ra bao nhiêu giây trong 30 giây memory context.

## 26. Ngày 30/10: gỡ cơ chế concept graph khỏi runtime theo yêu cầu user

User đã chọn rõ **gỡ cả cơ chế graph trong runtime**, không chỉ lọc prompt.
Phạm vi là `ConceptGraphStore` của memory và liên tưởng qua graph trong Living
User. Các số đo graph ở mục 25 là trạng thái **trước thay đổi này**.

- Bỏ tạo store, cập nhật graph khi commit belief/worldview/profile/episode,
  multi-hop recall, điểm cộng associative và gợi ý chủ động từ graph. Bỏ graph
  khỏi snapshot `MemoryCognitionCore.load`; xóa implementation graph khỏi source.
- `_read_self` còn chặn khóa `concept_graph` của snapshot legacy để nó không
  quay lại qua bootstrap hoặc Self delta; không mutate dữ liệu owner khi đọc.
  Các belief/worldview, semantic/episodic memory, profile, goal/commitment và
  truy xuất lexical/embedding vẫn giữ cơ chế hiện hành.
- Replay offline đúng request cũ: bootstrap **1.295.109 → 167.112 ký tự**,
  giảm **87,1%**; deep-compare xác nhận các phần Self ngoài graph không đổi.
  Đây là giảm kích thước trên fixture thật, chưa phải đo token/latency live mới.
- Nhóm memory/profile/provenance/bootstrap/projector/batch: **105 passed**,
  **2 failed** ở test V39 về inferred/conflicting record. Hai lỗi này tái hiện
  trên bản memory lấy từ HEAD trước thay đổi trong thư mục test cô lập. Test
  hard-cap bootstrap đã cố định env fixture để không phụ thuộc setting local
  đang tắt cap; không thay policy/cap của runtime.
- Đồng bộ 4 module Python sang portable, đối chiếu hash trước với HEAD, backup
  source cũ tại `Documents/eris/backups/retire-concept-graph-20261030/`.
  87 file dữ liệu/cấu hình được kiểm hash không đổi. Smoke trên bản sao của
  memory thật bằng module portable đã deploy: snapshot không có graph, public
  context vẫn dựng được, số lần mở graph bằng 0. Không gọi LLM/TTS live.
- Lệnh triển khai ban đầu có bước xóa graph/module cũ bị kiểm duyệt tự động
  từ chối với thông báo `blocked by policy`. Đã dùng phương án chỉ đồng bộ mã,
  giữ file graph và module legacy trên đĩa; runtime không import hoặc đọc chúng.
  Không tìm cách thực hiện lại thao tác xóa bị chặn qua công cụ khác.

Receipt đo payload và triển khai nằm trong artifact
`eris-retire-concept-graph-20261030/` của chat này. Cần phiên Eris mới để dùng
module vừa triển khai; chưa xác nhận phản hồi live nhanh hơn bao nhiêu và chưa
coi nút thắt `memory_context_ms≈30s` đã được giải quyết.

## 27. Ngày 30/10: kiểm tra 167.112 ký tự Self còn lại sau bỏ graph

User hỏi “167k token còn cao không”. Đã nhắc lại đây là **ký tự**, không phải
token, và chưa có run/raw trace live mới sau mục 26. Các số dưới là phân rã
offline đúng request đã lưu, chỉ bỏ graph như code đã triển khai.

- Bootstrap Self: 167.112 ký tự. Tổng content của 5 messages còn 206.088 ký tự.
  Bộ heuristic `llm.token_budget.estimate_tokens` hiện tại báo **55.708 token
  cho riêng message Self**, **68.716 cho toàn messages**. Cùng bộ đếm báo
  444.715 cho messages trước bỏ graph. Đây là chars/3 cộng overhead, không phải
  tokenizer DeepSeek và không phải usage live từ gateway; không trộn nó với
  số 472.031 gateway từng báo cho request cũ chưa bỏ graph.
- `living_user.profile`: 63.685 ký tự cho 80 record active, mỗi record 21
  trường. Các value cộng 7.847 ký tự, metadata cộng 11.487 (reason 9.777),
  còn lại là path, evidence/source/scope, timestamp, trạng thái, khóa JSON...
  Có 47 record communication, trong đó 19 `communication.response_rules`;
  11 `learning.active_topics`, 5 negative-feedback patterns. 80 cặp path/value
  khác nhau; không gọi chúng là duplicate hoàn toàn chỉ vì cùng path.
- `living_user.replay`: 41.928 ký tự, có timeline 120 chuỗi debug gồm 50 context,
  39 conversation_turn, 20 observe và 11 dream. `summary()` lấy trực tiếp
  `replay_debugger.report().to_dict()`, rồi `core.load()` đưa summary vào Self.
  Đây là đường báo cáo chẩn đoán đi vào prompt, không phải chỉ có ký ức đã chọn.
- Actions 18.336 ký tự: 5 goal active, 23 commitment (22 active, 1 completed).
  Recent episodes 9.134 ký tự (8 mục); agent mind 7.852; mind state 7.182.
- `active_state.metadata.internal_state` và `memory_state.internal_state`
  có cùng 75 key, các giá trị bằng nhau theo deep-compare Python. Mỗi bản
  khoảng 5,8 nghìn ký tự; có dư một biểu diễn state trong snapshot.

Ưu tiên có thể xem xét: tách replay/debug khỏi Self, bỏ biểu diễn internal
state lặp và compact view profile mà giữ giá trị/provenance cần thiết. Lần
này chỉ kiểm tra; chưa đổi runtime, xóa record hay thay cách truy xuất memory.

## 28. Ngày 30/10: rà toàn bộ profile active và action memory

Theo yêu cầu “rà đi”, đã đọc từng record active trong hai file portable
`living_user_profile.json` và `living_user_actions.json`; đối chiếu source và
replay các helper bằng dữ liệu tổng hợp trong sandbox. Không sửa runtime hoặc
canonical data trong lượt rà; hash hai file giữ nguyên.

- **Đính chính phạm vi mục 27:** 80 là snapshot trong raw request, không phải
  toàn kho. File hiện có 147 record, trong đó **118 active**, 24 superseded,
  2 deleted, 3 disputed. Trong active có 77 communication, 30 response_rules,
  15 learning.active_topics. Cả 118 scope global. `snapshot(limit=80)` chọn
  trust/recency, không phải relevance của câu user hiện tại.
- Đề xuất review đủ 118 mục: 18 thông tin có ích giữ có nguồn; 32 ứng viên
  gộp thành 6 nhóm chỉ dẫn có điều kiện; 27 record đùa/nhập vai theo ngữ cảnh;
  36 record phù hợp episode/chủ đề lịch sử; 5 cần đối chiếu phát ngôn gốc.
  Đây là phân loại đề xuất, không phải số lượng đã xóa. Có cặp gần trùng:
  đau đớn/bản sắc; giải thích lại khi chưa hiểu; cười/khóc thử; feedback vẽ và
  sở thích sáng tạo; nhiều rule idol/cute; yêu cầu nói cụ thể và feedback mơ hồ.
- Silence có thể là suy tư hoặc phép thử, nhưng không được biến khả năng thứ
  hai thành mặc định. Các yêu cầu cute/lạnh trong các cảnh khác nhau không
  tự chứng minh user mâu thuẫn. Lời tự chê cũng không tự thành nhãn năng lực.
- **Action admission:** `_extract_user_commitment` có cue “tôi tự”; replay
  “tôi tự hỏi tôi tạo Eris để làm gì” tạo active commitment/confidence 0,92.
  Lời đưa thoại “nói câu này: tôi tự nguyện…” cũng thành commitment user.
  Câu hỏi “tôi muốn hỏi … đang phân vân” thành goal active/0,90; một biến thể
  kết thúc “gì” bị guard chặn. Offer “Nếu bạn gửi lỗi thì tôi sẽ đọc giúp”
  thành commitment assistant active/0,93. Kho thật có mẫu tương ứng.
- `archive_stale()` không xử lý active; sandbox goal active cũ 400 ngày vẫn
  active. `snapshot()` không scope và gồm cả inactive; summary đưa snapshot
  này vào Self. Đường `goals(scope=...)` có lọc đúng nhưng không phải đường
  summary hiện tại. Kho có 5 goal active, 22 commitment active, 1 completed.
- Nhiều evidence cũ dạng `session_turn:<n>` đứng riêng không định danh phiên;
  chưa kết luận từng record bị reinforce sai khi chưa đối chiếu transcript.
  Không nhầm helper idempotency public delivery đã có namespace phiên với
  provenance của memory: đó là hai đường khác nhau.

Artifact `eris-memory-audit-20261030/` của chat có review từng record ID,
đề xuất gộp, lý do và probe. Ưu tiên sửa nguồn admission/vòng đời trước khi
dọn record cũ để tránh tái sinh dữ liệu sai; không mặc định được phép xóa
toàn bộ memory từ việc user yêu cầu rà soát.

## 29. Ngày 30/10: AI curator đã tồn tại, Gemini không phải owner của phiên đã rà

User nhớ đã gắn Gemini cho memory. Đối chiếu code và portable xác nhận **đã có
AI xử lý memory**; không được diễn giải các lỗi ở mục 28 thành “chưa có AI dọn”.

- `coordinator_bootstrap.py` ưu tiên `GeminiWebUserProfileAdapter` khi import
  được `gemini_webapi` và tìm được cookie/credentials theo đường adapter hỗ trợ.
  Adapter được bind vào profile extractor, episode builder và dreamer. Nếu
  không khả dụng, bootstrap dùng DeepSeek curator lane.
- Log `eris_run_20261030_135451.txt:59` ghi owner thực tế **DeepSeek curator lane**.
  Probe chỉ đọc bằng Python portable hiện tại: không có package `gemini_webapi`,
  không thấy hai biến cookie Gemini hoặc file credentials tại đường discovery
  mà adapter dùng. Không in giá trị bí mật. Đây là trạng thái kiểm tra hiện tại,
  không chứng minh Gemini chưa từng chạy ở bản cũ hoặc service Gemini khác.
- AI GC riêng trong `memory_gc_service.py` có review semantic/profile và
  `LivingUserMemoryCurator` có thao tác merge/rewrite/archive/delete... kèm audit.
  Tuy nhiên GC tự lấy DeepSeek curator/critic, không dùng adapter Gemini của
  Living User. Service có lịch chạy khi idle (startup delay mặc định 120 giây,
  interval 600 giây); log phiên đã rà có startup, chưa thấy receipt xác nhận
  một lượt dọn thành công. Không suy diễn rằng GC chưa bao giờ chạy.
- Kho goal/commitment vẫn nhận trực tiếp qua `action_store.observe_turn()` và
  cue extraction. AI review profile không tự review các record action này.
  Vì vậy có AI curator vẫn không ngăn được lỗi actor, câu giả định hoặc “tôi tự
  hỏi” biến thành commitment active đã tái hiện ở mục 28.

Hướng sửa là nối phán đoán ngữ nghĩa của AI vào đúng admission/review action,
giữ kiểm tra evidence, actor, scope, trạng thái và audit ở code; đồng thời làm
rõ provider/coverage và receipt chạy curator. Không cần tạo lại AI cleaner từ
đầu hay mặc định thêm regex sẽ giải quyết được. Lượt kiểm tra này chưa cài
package, đổi routing, gọi model live hoặc sửa canonical memory.

## 30. Ngày 30/10: nối memory vào service Gemini Web theo chỉ định mới

User đính chính: dùng lane Gemini Web dọn memory, đã bỏ DeepSeek cho việc này.
**Thay thế diễn giải ở mục 29 rằng thiếu package/cookie trong portable có nghĩa
lane Gemini Web của hệ thống không khả dụng.** Mục 29 chỉ kiểm adapter direct
cookie cũ; đã bỏ sót service Gemini riêng đang chạy.

- GET `http://127.0.0.1:8000/v1/models` quảng bá `gemini-flash` và
  `gemini-flash-lite`; GET `/api/status` báo authenticated và cached credentials
  đều true. Source service là `tools/gemini_vision_docker/server.py`, hỗ trợ chat
  text qua `/v1/chat/completions`. Không đọc hoặc sao chép cookie sang portable.
- `GeminiWebUserProfileAdapter` chuyển sang gọi HTTP service này; cấu hình
  `ERIS_MEMORY_GEMINI_ENABLED`, `ERIS_MEMORY_GEMINI_BASE_URL`,
  `ERIS_MEMORY_GEMINI_MODEL`, `ERIS_MEMORY_GEMINI_TIMEOUT_SEC`. Factory chỉ đọc
  cấu hình, chưa gọi mạng nên log bootstrap là owner được chọn, không phải
  receipt provider đã trả lời. Mặc định cổng 8000/model `gemini-flash`, timeout
  120 giây. Profile/dream/GC chia sẻ lock trong process.
- `build_curator_client`, bootstrap Living User và GC dùng Gemini Web; bỏ
  fallback sang DeepSeek critic/inner/chat. Nhánh maintenance
  `MemoryAICurator._call_llm()` trước đó mượn inner/client cũng đã chuyển sang
  Gemini curator. Không đổi owner của Root, writer, observer hay TTS.
- Timeout, lỗi HTTP, output rỗng/sai schema hoặc bị truncate không phải kết quả
  operations rỗng hợp lệ. GC lỗi response/parse không chốt throttle 24 giờ.
  Validator profile chỉ cho phép ID trong batch thực sự gửi model; checkpoint
  mới dùng nhãn trung tính `model_curator_living_user_gc`.
- Smoke live với evidence **tổng hợp** về sở thích câu trả lời ngắn: Gemini
  trả JSON operations hợp lệ sau **4,73 giây**. Không gửi kho memory thật trong
  smoke và không ghi canonical memory. Bộ test cô lập credentials: **86 passed,
  1 failed** ở `test_research_client_no_longer_falls_back_to_main_or_fallback`.
  Lỗi research fallback tái hiện bằng chính `LlmService` lấy từ HEAD, không do
  thay đổi memory routing. Ba lỗi ban đầu khác trong suite rộng đến từ config
  local lọt vào test; chạy cô lập không còn ba lỗi đó.
- Đồng bộ 6 module runtime sang portable, backup source trước đó tại
  `Documents/eris/backups/gemini-memory-service-20261030/`; 26 file memory/config
  được kiểm hash giữ nguyên. Import probe từ chính portable xác nhận process
  manager và GC chọn Gemini adapter, cả 6 module parse được. Chưa mở lại toàn
  app hoặc chạy cleanup lên kho thật; cần phiên Eris mới để dùng module đã sửa.

Phạm vi sửa này là routing/receipt lỗi của memory AI. **Chưa sửa** admission
goal/commitment bằng cue, vòng đời active cũ, replay/debug vào Root Self hoặc
snapshot profile chưa theo relevance. Không coi Gemini đã tự dọn được những
kho mà code chưa gửi nó review; không coi smoke tổng hợp là receipt dọn kho thật.

## 31. Ngày 30/10: AI sở hữu admission hội thoại, reset action active và đo prompt

User yêu cầu bỏ việc bắt từ khóa, để AI quyết định memory; reset active cũ và
nghiên cứu phần prompt phình. Phạm vi reset đã thông báo là **goal/commitment
active trong action store**, không phải toàn bộ active profile hay lịch sử đời sống.

- Bỏ extractor cue goal/user commitment/assistant commitment/workflow, cùng
  rule tự đánh dấu hoàn tất từ câu assistant. Journal chỉ ghi lượt có evidence ID.
  Bỏ rule tự sinh profile từ từ khóa về ngôn ngữ, feedback, style, học tập và
  công việc. Bỏ các bộ regex phân loại ẩn dụ/nhập vai/ephemeral khỏi model,
  extractor, profile admission và deterministic curator; không dùng từ trong
  value để tự phong core_anchor. Giữ kiểm schema, nguồn, evidence, scope, expiry,
  typed path/tier, authority ledger và bảo vệ core anchor hiện có.
- Một lần Gemini inference đề xuất cả `operations` profile và `action_operations`.
  Curator worker chỉ propose; reducer mới mutate. Action proposal mang role,
  scope và evidence do ingress cấp; model không tự cấp chúng. Assistant đã
  delivered mới vào curator, không đi qua user semantic parser và không được
  viết user profile. Lời assistant tự nói “đã xong” không đóng commitment.
- Action store có audit/undo data và dedupe proposal bền qua restart. AI có thể
  thêm action hoặc đổi status của ID đã được cung cấp trong đúng scope; không
  fuzzy-merge mục tiêu theo chữ. Lỗi ghi action rollback RAM. Audit không nằm
  trong snapshot prompt. Idle GC Gemini được review action, chỉ hạ về candidate
  hoặc archive; không tự bịa completion, cancellation hay action mới.
- Sửa `CuratorInbox.due()` lấy cả trạng thái `proposed`: nhánh khôi phục outbox
  đã tồn tại nhưng query cũ chỉ lấy pending nên proposal sau restart bị kẹt.
  Model lỗi/không khả dụng không được biến thành “không có gì đáng nhớ”.
- Backup rồi reset **5 goal active, 22 commitment active** trong portable.
  Còn 0 goal, 1 commitment completed, 0 workflow. `reset_before` ngăn action
  proposal từ evidence trước reset quay lại. 25 file memory/config khác được
  kiểm hash không đổi; profile vẫn 118 active. Không có Eris runtime đang chạy
  lúc thao tác. Backup tại `Documents/eris/backups/ai-memory-reset-20261030/`.
  Đã đồng bộ 14 module Python, import smoke từ chính portable trên bản sao của
  memory thật xác nhận reset, chặn outbox cũ, graph vắng mặt và dữ liệu gốc không
  đổi trong smoke. Cần mở lại Eris để runtime dùng code mới.
- Test liên quan: **147 passed, 3 failed**; ba lỗi retrieval/clarification và
  competence context đều tái hiện với package memory lấy từ HEAD trong thư mục
  tạm cô lập. Nhóm Root/bootstrap/graph thêm **23 passed**. Smoke Gemini thật
  bằng 4 evidence tổng hợp: suy tư, lời trích dẫn, offer có điều kiện đều trả 0
  action; lời chốt bàn giao tạo 1 commitment user. Mỗi inference 2,66–3,73 giây.
  Không coi 4 probe là chứng minh model luôn phân loại đúng.

**Prompt đã đo, chưa đổi projection/cắt memory trong lượt này:**

- Raw request user-turn cũ vẫn là `a9ef0a39abd240c59c038b6c9adad92f`, manifest
  hash được kiểm lại. Bootstrap sau loại graph offline: 167.112 **ký tự**.
  Profile 63.685, replay 41.928, actions 18.336, 8 episode 9.134. Riêng profile
  có 80 record × 21 trường; tổng value 7.847, metadata 11.487 (reason 9.777),
  evidence 4.195, khóa/dấu JSON của mảng record 21.601 ký tự.
- `living_user.summary()` đưa snapshot đầy đủ và `replay_debugger.report()` vào
  `MemoryCognitionCore.load()`, Root `_read_self()` lấy nguyên memory_state.
  Đây là đường debug/audit view đi vào bootstrap, chưa phải context được chọn
  theo query. Profile snapshot vẫn lấy trust/recency, không relevance.
- Hai biểu diễn internal state có cùng 75 key và value, mỗi bản khoảng 5,8k:
  `active_state.metadata.internal_state` và `memory_state.internal_state`.
- Event hiện tại 23.614 ký tự: runtime context delta 10.049 (grounding 5.685),
  Self delta 12.991. Trong Self delta có thêm `memory_state.ai_orchestrated_state`
  9.932 ký tự bên cạnh `mind_state` delta 3.002. Reconstruct mind_state từ
  bootstrap + delta rồi compare: hai bản khác đúng `relationship.last_event`.
  Nguồn bản persistence là `runtime/ai_orchestrated/bootstrap.py:persist()`;
  không xóa một bản trước khi hợp nhất khác biệt có nghĩa đó.
- Snapshot offline mới từ portable sau reset: profile **63.685**, replay
  **41.402**, actions **972**, episodes **9.545** ký tự. Đây là component của
  fresh memory load, không phải request Root live mới. Replay/debug và full
  profile serialization vẫn là hai nguồn lớn nhất còn lại.
- Bootstrap còn được projector đưa lại vào messages của request sau. “Delta”
  không tự làm prefix Self miễn phí. Chỉ bỏ replay trong fixture cũ giảm
  bootstrap 167.112 → 125.174 ký tự; giả lập thêm reset active cũ còn 107.810.
  Đây là scenario phân tích, **chưa triển khai** việc bỏ replay. Log Root mới
  ghi `self_component_chars` và `request_size` (chars/message và token heuristic
  chars/3), không log nội dung memory; không nhầm heuristic với usage tính tiền.

Artifact `eris-ai-memory-reset-20261030/` của chat chứa breakdown theo field,
probe Gemini, receipt backup/deploy/reset và smoke portable. Ưu tiên projection
tiếp theo: tách debug report khỏi prompt, compact profile nhưng giữ meaning và
provenance, hợp nhất state trùng sau khi đối chiếu owner; không hard-cap/cắt
belief tùy tiện rồi tuyên bố đã giữ nguyên Self.

## 32. Ngày 30/10: điều tra prompt thừa và contract/state không nhất quán

User yêu cầu điều tra prompt có gì thừa. Lượt này chỉ đọc source/trace, dựng
payload và chạy probe tổng hợp offline; không sửa runtime hoặc memory/config.
Không có log/raw Root request mới hơn phiên 13:57 đã dùng ở mục 31.

- Thừa rõ: replay/debug khoảng 41,4k ký tự ở snapshot hiện tại; hai bản
  internal state cùng 75 key/value (5.828 ký tự mỗi bản trong fixture). Ngoài
  ra xác nhận `latest_observation` 2.643 ký tự giống hệt ở ba chỗ trong event:
  conversation_status, mind_state delta, memory_state.ai_orchestrated_state.
  Observation thuộc startup, trước câu user hiện tại. Các khoản reducer/observer
  có phần lồng nhau, không được cộng như các nguồn độc lập.
- Profile chưa phải toàn bộ rác: mảng 80 record 63.622 ký tự. Thử encoding
  columns/rows giữ toàn bộ trường/value, decode deep-equal: còn 44.130 ký tự.
  View gọn 12 field (bỏ null) còn 32.133, nhưng đây là view có loại trường,
  chưa được coi lossless về meaning nếu qualifier quan trọng nằm ở metadata.
  80/80 updated_at bằng last_confirmed_at; 71/80 created_at bằng valid_from;
  160 trường validity null. Canonical data không bị sửa trong phép thử.
- System prompt thực tế 14.967 ký tự: core contract 12.951 và inline schema
  2.014. Rich Chat manual 1.608 + math/work manual 2.478 luôn được nạp, cả ví
  dụ riêng ma trận 0/1 cấp 3. Có thể tách hướng dẫn chi tiết theo capability
  Root chọn; không dùng keyword gate quyết định thay Root. Inline JSON schema
  **cần giữ** vì client hạ response_format xuống json_object, phải mang schema
  qua system để không mất contract.
- Grounding tự chèn vision snapshot chưa quá 600 giây, không kiểm relevance
  ở `_world_context()`. Fixture có đoạn vision 1.943 ký tự dù câu hiện tại là
  phản ứng xã hội ngắn, các cờ requires_pixels/current_environment/screen_text
  đều false và SOURCE_GROUNDING giới hạn nguồn ở user_text/runtime_state.
  Fresh không đồng nghĩa liên quan. Còn duplicate user evidence, clock/focus
  ở các phần grounding; không vì thế mà bỏ năng lực quan sát màn hình.
- **Contract mismatch:** system hướng dẫn kind=batch, nhưng Root session dùng
  ROOT_STEP_SCHEMA không có batch. Chỉ fallback one-shot mở batch schema;
  hai nhánh hiện dùng chung system. Chưa khẳng định model đã trả batch lỗi live.
- **Bổ sung quan trọng về delta:** probe chạy chính RootConversationSession.ask
  với fake router: owner A→B thì request đầu có B; owner không đổi thì request
  kế mất B, prefix vẫn chứa A. Runtime context không đổi cũng biến mất khỏi
  request kế. Baseline last_* được cập nhật nhưng prefix vẫn bất biến và
  projector không giữ các delta trước đó. Router đưa nguyên projected messages
  vào client; SessionGuard chỉ tiếp tục khi transcript append-only khớp chính
  xác, không bảo đảm trí nhớ remote bù cho dữ liệu thiếu. Kết quả này giới hạn
  các kết luận trước về tính đúng của “delta thật”: đo giảm chars không chứng
  minh request kế giữ đủ state. Chưa đo hậu quả ở một lượt Eris live mới.

Mô phỏng fixture sau graph + thay action đã reset: bootstrap 149.748 ký tự;
bỏ replay còn 107.810; bỏ thêm internal-state clone còn 101.965; profile table
lossless còn 82.473, hoặc compact view có loại field còn 70.476. Hai bước đầu
giảm 47.783 ký tự (~31,9%), không thay canonical memory. Đây là scenario offline,
không phải projection đã triển khai hay token billing.

Artifact `eris-prompt-audit-20261030/` có result.md, analysis.json và
delta-continuity-probe.json. Ưu tiên sửa correctness của state/schema trước khi
rút gọn tiếp; sau đó tách debug, hợp nhất state và compact view có kiểm chứng.

## 33. Ngày 30/10: triển khai dọn prompt bước 1–4; chưa đụng bước 5

User chỉ định làm bước 1,2,3,4 của đề xuất; bước 5 (relevance/detail của màn
hình) để suy luận sau. Thay đổi này thay thế trạng thái “chỉ điều tra/chưa sửa
projection” ở mục 31–32, không thay các kết quả đo lịch sử ở đó.

- **State correctness:** Root giữ bootstrap bất biến và gửi
  `self_changes_since_bootstrap`, `self_base_fingerprint` cùng `runtime_context`
  đầy đủ ở mỗi request. Overlay luôn so với bootstrap, không phải bước trước.
  Object merge, array/scalar replace; giữ tombstone xóa và null rõ ràng. Bảo
  toàn cả thay đổi kiểu JSON như False→0. Không truncate overlay khi vượt cap;
  chỉ thu hẹp cửa sổ hội thoại rồi dùng fallback hiện có nếu vẫn không vừa.
- **Loại view chẩn đoán/lặp:** module pure `self_prompt_view.py` bỏ replay,
  last_dream_report, pending_turns và active_session_id khỏi view gửi Root,
  không sửa summary/store gốc. Bỏ internal_state clone chỉ khi giống hệt.
  MindState reducer là owner chính; phần persistence khác được giữ dưới
  `persisted_mind_variants` thay vì gửi một bản full thứ hai. Observation trùng
  conversation_status dùng reference tới mind_state.latest_observation; nếu
  khác thì vẫn giữ dữ liệu riêng.
- **Compact profile không mất trường:** dùng defaults cho các trường cấu trúc
  lặp và timestamp aliases, có ngoại lệ trên từng record. Không chọn top-k mới,
  không xóa value/ID/evidence/scope/uncertainty/qualifier. Chủ động giữ cả
  metadata.reason vì đây là text tự do có thể chứa điều kiện; không phân loại
  reason “rác” bằng từ khóa. Decode 80 record thật deep-equal bản gốc.
- **Hướng dẫn công cụ theo nhu cầu:** system core giữ cá tính, quyền quyết định,
  receipt/evidence và ranh giới speech/artifact, thêm menu ngắn. Sáu manual
  rich_chat/research/math_workspace/live_workspace_edit/work/tutor chuyển sang
  `tool_guides.py`; Root tự chọn `read_tool_guide`. Đây chỉ là đọc documentation,
  không cấp quyền, không chạy tool và không tạo execution receipt. Guide đã đọc
  còn trong payload cả event, kể cả sau nhiều tool results. Khi cần đọc manual,
  có thể có thêm một Root round trip; chat thường không bị ép đọc.
- **Schema thống nhất:** warm-up, session và fallback cùng dùng
  ROOT_BATCH_STEP_SCHEMA; batch hợp lệ trên cả hai nhánh. Base internal schema
  có thêm action đọc guide; public move contract không đổi. Giữ inline JSON
  schema qua DeepSeek transport, không xóa ràng buộc output.

**Đo offline cùng fixture**, thay action snapshot sau reset cho cả trước/sau;
dựng lại Self/context hiện tại từ chuỗi request cũ trước khi đo overlay mới:

- Tổng message content **188.724 → 131.613 ký tự (-30,26%)**.
- Bootstrap **149.748 → 82.750**; profile **63.685 → 44.023**.
- Event payload **23.614 → 35.461**: lớn hơn vì bản mới phải tự chứa context
  đầy đủ và các update Self tích lũy; bản cũ thiếu update ở bước tiếp theo.
  Không đổi số này thành token billing hoặc tuyên bố latency live giảm tương ứng.
- Kiểm chứng giữ nguyên toàn bộ mind_state, decode đầy đủ 80 record profile,
  overlay tái dựng đúng Self hiện tại, grounding/vision text giữ nguyên.

**Kiểm tra/triển khai:** bộ liên quan Root, context, writer, delivery và math
contract: **189 passed, 1 failed** ở test chạy binary determinant worker với
`command_exit:1`. Probe bằng MathWorkspaceService lấy từ HEAD cũng lỗi như vậy;
không do thay đổi prompt. Nhóm regression cleanup cuối **8 passed**, gồm unknown
guide không được coi là tiến triển. Smoke bằng module portable trên bản sao
memory thật xác nhận compact roundtrip, update còn ở request sau và guide không
phải receipt. Không gọi LLM/TTS live.

Đã đồng bộ 6 module Python (3 module mới), backup source cũ tại
`Documents/eris/backups/prompt-cleanup-1234-20261030/`. 28 file bảo vệ giữ hash,
gồm 26 memory/config và hai module context_assembler/vision_perception_worker.
Không reset thêm memory, không sửa policy hay detail/relevance màn hình bước 5.
Cần mở lại Eris để dùng code mới; chưa đo một lượt chat live sau deploy.

Artifact `eris-prompt-cleanup-steps1234-20261030/` có payload-verification.json,
deployment plan/receipt, portable-smoke.json và result.md.

## 34. Ngày 30/10: đổi tab/cửa sổ nhanh không đồng nghĩa vision theo kịp mọi lần

User hỏi Eris có biết khi làm việc và hay đổi tab/màn hình không. Chỉ đọc code,
không sửa bước 5 hoặc chạy theo dõi màn hình live trong lượt giải thích này.

- Telemetry có foreground app/process/window, focus_changed và một số metadata
  tab trình duyệt nếu accessibility/bridge cung cấp. EnvironmentAwarenessHub
  ưu tiên current telemetry cho focus identity, kể cả semantic worker chưa xong.
  Chat cycle refresh hub từ machine snapshot rồi đưa vào WorldState. Điều này
  hỗ trợ biết cửa sổ đang focus; không bảo đảm ghi đủ mọi chuyển tab thoáng qua.
- Gemini Vision là đường riêng: mặc định poll 6 giây, cooldown 12 giây, chỉ gửi
  khi title hoặc pixel thay đổi. Không thấy override các biến này trong portable
  .env đã đọc; không coi đó là đo cadence live. HTTP chạy đồng bộ trong worker
  nền, còn có thời gian Gemini xử lý; last_sent_at đặt sau khi response thành công.
- Vì vậy focus có thể đã sang B trong khi vision còn mô tả ảnh A. Chuyển A→B→A
  nhanh giữa các lần lấy mẫu có thể không được Gemini nhìn thấy. Không được nói
  Eris đã hiểu từng tab hoặc đang quan sát video realtime.
- Snapshot captured_at hiện đặt lúc publish response, không phải lúc chụp ảnh;
  nhãn freshness của vision chưa phản ánh toàn bộ độ trễ phân tích. Cần xem lại
  khi user yêu cầu bước 5, không âm thầm thay trong lượt hỏi đáp này.

**Làm rõ sau câu hỏi về tích lũy context màn hình:** đường vision trực tiếp chỉ
lấy `get_latest_snapshot()`; service giữ một `_latest_snapshot` được thay khi
có snapshot mới. Projector gửi context hiện tại một lần, cửa sổ 24 turn chỉ
chứa lời public user/assistant và trạng thái delivery, không giữ toàn bộ context
màn hình của từng turn. Nhiều request có thể gửi lại cùng mô tả ~1,9k ký tự,
nhưng request thứ mười không tự chứa mười bản mô tả qua đường này. Không suy
diễn rằng ảnh/vision đang là nguồn context phình lớn; user ưu tiên giữ phần này.

## 35. Ngày 30/10: khả năng Whale2API phải kiểm tra riêng với API chính thức

User yêu cầu xem Whale2API dùng DeepSeek có khả năng gì để tận dụng. Chỉ điều tra
source và chạy probe tổng hợp; chưa chuyển agent loop sang tools, sửa gateway,
đổi memory/vision hay restart dịch vụ.

**Bản đang chạy:** Docker `whale2api`, cổng 5103, image
`ghcr.io/tangsong404/whale2api:latest`, image ID
`sha256:70475dae6ba554b4d67eac19c285650eefd0e01ecf7b8ac28ae3235166173266`.
Source checkout `tools/whale2api` không được bind mount làm executable; các nhận
định chỉ đọc source không tự chứng minh binary chạy có cùng revision. Probe
live bên dưới là bằng chứng riêng cho deployment này.

- **Tools dùng được ở giao diện Chat Completions:** probe SSE trả đúng
  `tool_calls` với name/arguments/id; gửi lại assistant call và role=tool cùng
  `tool_call_id`, model trả đúng `42`. Hai request mất khoảng 4,24s và 2,42s.
  Không chạy tool hệ thống; chỉ cộng hai số trong script kiểm tra.
- **Không phải native strict function calling của API chính thức:** checkout
  chuyển schema thành text + hướng dẫn cú pháp custom, rồi parse output thành
  OpenAI-shaped tool calls (`promptcompat/tool_prompt.go`,
  `toolcall/tool_prompt.go`). Không thấy xử lý `strict` hay constrained JSON
  decoding; không bỏ validation ở runtime. Test thành công đơn giản không chứng
  minh mọi tool/schema đều hoạt động hoặc tự làm prompt nhỏ hơn.
- **Đính chính phạm vi `tool_choice`:** Chat normalization luôn đặt auto,
  bỏ qua request policy. Live gửi `tool_choice="none"` vẫn nhận tool call.
  Vì vậy nhận xét trước đó rằng gateway nhận tools/tool_choice cần giới hạn:
  tools hoạt động, policy Chat chưa được bảo đảm. Responses normalization có
  parse policy nhưng chưa được probe live trong đợt này.
- **Không nối tiếp web chat giữa request:** `completionruntime/nonstream.go`
  gọi CreateSession mỗi request; `StandardRequest.CompletionPayload` đặt
  `parent_message_id=nil`. Không tìm thấy xử lý `X-Session-ID` trong checkout.
  Live gửi cùng header nhưng bỏ lịch sử thì model trả `MISSING` thay vì nhớ
  marker tổng hợp. `parent_message_id`/continue trong retry nội bộ không chứng
  minh hỗ trợ hội thoại liên request. Điều này giới hạn các suy luận cũ về
  SessionGuard: header bên Eris không tạo persistent upstream session ở gateway
  hiện tại. Muốn session thật cần thay gateway, không chỉ đổi header ở Eris.
- **Thinking bật/tắt:** live disabled không có reasoning_content; enabled trả
  kết quả đúng và có reasoning_content (chỉ đếm độ dài, không lưu nội dung).
  `util/thinking.go` ánh xạ minimal/low/medium/high/xhigh thành cùng boolean true;
  không coi effort là ngân sách suy nghĩ phân cấp. `thinking` explicit ưu tiên
  trước reasoning_effort. Thinking injection cấu hình riêng còn có thể thêm text.
- **Ảnh:** live gửi PNG tổng hợp màu magenta bằng image_url data URI, gateway
  trả đúng `Magenta` (~6,06s). Source có inline upload/files. Đây là bằng chứng
  một ảnh đơn giản, chưa đánh giá OCR, tài liệu dài hay chất lượng vision.
- **Context:** GET /v1/models quảng bá deepseek-flash, context_length=896000,
  multimodal=true. Đây là metadata/cấu hình, chưa probe đủ cửa sổ hay chứng minh
  khả năng nhớ toàn bộ. Usage do gateway đếm/ước lượng, không phải số billing
  hay cache-hit từ DeepSeek. Chưa có bằng chứng cache Self miễn phí giữa request.
- **Search/model:** checkout mặc định search=false và không chuyển
  search_enabled từ Chat request; chỉ chấp nhận deepseek-flash, model_type default.
  Không suy ra có sẵn search, expert/pro hay Tool Search chỉ từ web UI/API docs.
- **Embeddings/Responses:** source có route nhưng embeddings chỉ hỗ trợ
  mock/deterministic/builtin tạo vector SHA-256 64 chiều, không phải semantic
  embedding để recall memory. Responses lưu kết quả để GET; không tìm thấy
  xử lý previous_response_id trong normalization/handler, vẫn dùng CreateSession.
  Không coi đổi endpoint là cách giữ trí nhớ upstream.

Ưu tiên thử tiếp khi triển khai: adapter tool_calls qua registry hiện có,
discovery/guide theo nhu cầu, validation và execution receipt; sửa policy gateway
trước khi dựa vào tool_choice. Benchmark với loop JSON hiện tại vì gateway vẫn
chèn manual của riêng nó. Session reuse là công việc riêng cần account affinity,
parent-message tracking và kiểm tra state trước khi gửi delta. Giữ nguyên quyền
quyết định, cá tính và canonical Self của Eris.

5 package Go liên quan promptcompat/toolcall/format/config/completionruntime đều
pass (cached). Artifact `eris-whale-capabilities-20261030/` chứa probe và kết quả
live tổng hợp, không lưu credential hay reasoning text. API chính thức có tài
liệu function calling/strict beta riêng; không áp các đảm bảo đó cho Web gateway.

## 36. Ngày 30/10: fast dev vào Documents và hai lượt desktop có audio hoàn tất

User yêu cầu chạy fast dev sang Documents rồi test. Đã chạy đúng
`build_dev_fast.bat` (profile dev/debug, skip installer), exit 0, output
`C:\Users\Gtvkun\Documents\eris`. Không triển khai session reuse hay đổi agent
loop trong lượt này. `ErisManager.spec` thiếu nên script dùng binary manager đã
có; không nói đã build lại manager. Sanity Godot/.NET/Live2D và pool DB pass.

Trước build xác minh các đường dẫn src/asset/voice_presets/data_* bị thay nằm
trong output, backup 55 file memory/config/state tại
`Documents/eris/backups/fast-dev-test-20261030-213121/`. Build chép đè
config/elevenlabs_pool.db; đã phục hồi DB đang dùng từ backup trước khi launch.
55/55 file được bảo vệ giữ hash tại ranh giới sau build/trước chạy runtime.
Không hiểu điều này là memory không được học thêm trong lúc test. Chín module
được kiểm hash sau build đều khớp repo, gồm cleanup, Gemini adapter và hai
module context/vision không thay đổi. Data runtime thực sự tiếp tục ở ErisLive
theo cấu hình; build không reset kho đó.

**Kiểm chứng:** 44 test pass cho prompt cleanup, build launcher/payload và
public_text_audio_commit. Mở bằng launcher portable, Eris.exe khởi chạy đúng
`Documents/eris/python/python.exe` + `Documents/eris/src/vision.py`, Live2D cũng
dùng portable Python. Bootstrap Self live 83.161 ký tự, system 9.362; hai
request chat được log khoảng 126.753 và 127.353 ký tự message content. Đây là
ký tự, không phải provider token/billing; chars3 estimate không phải tokenizer.

Gửi hai câu qua giao thức UDP local mà desktop đang dùng, giữ TTS và mọi config
production. Lấy audio_start/audio_done theo delivery ID từ public trace, đối
chiếu Live2D playback receipts và public-text reason=audio_start; cả hai lượt
đều delivered. Body history có đúng hai reply; câu sau trích lại đúng câu chào.
Không có ghi âm độc lập để đánh giá chất lượng giọng nghe bằng tai.

- Lượt thử 1 bắt đầu audio 34,451s, done 47,334s sau tin tổng hợp. **Không phải
  mẫu độc lập:** desktop có tin trước đó lúc 21:34:30, tin thử đến 21:35:04
  trong khi đang chuẩn bị context, Root gộp messages=2. Từ tin trước tới audio
  khoảng 68,9s. Evidence 23.270ms, memory recall 51ms; context 28.071ms, trong đó
  memory_context 28.020ms; Root round trip khoảng 7,1s, Writer 4.813ms.
- Lượt thử 2, chỉ một input ở Root: bắt đầu audio **47,440s**, done **62,518s**.
  Root-input wait 31,4s; evidence **25.814ms**, memory recall 17ms; context
  **231ms**, memory_context 179ms; Root khoảng **4,7s**, Writer **3.561ms**;
  writer prepared tới audio khoảng 7,7s. Evidence bao gồm unified chat
  perception **19.441ms**, có OCR refresh và route vision dù user chỉ hỏi
  nhắc lại câu chào. Chưa kết luận tiểu bước nào chiếm toàn bộ phần còn lại.

**Cập nhật ưu tiên từ bằng chứng live:** thay thế suy đoán trong lời khuyên ngay
trước test rằng prompt bootstrap lặp là nút thắt cần xử lý đầu tiên. Session
reuse vẫn có giá trị tiềm năng, nhưng ở hai mẫu này phần chờ trước Root lớn
hơn Root/Writer. Cần đo sâu telemetry/perception và lần memory_context 28s
trước khi chọn fix latency. Thấy OCR/vision chậm không có nghĩa đoạn context
màn hình ~2k ký tự là prompt rác; không tự cắt vision hay sửa bước 5. Chưa đo
TTFT provider, chưa xác định nguyên nhân cuối cùng của memory_context 28s.

Sau test có một autonomous follow-up tự nói về việc đang test; không coi đó là
lượt test thứ ba hay bằng chứng lỗi TTS. Hai mẫu không đủ đánh giá latency
trung bình hay toàn bộ cá tính. Để bản desktop tiếp tục mở cho user sử dụng.
Artifact `eris-fast-dev-live-test-20261030/` có build log/receipt, manifest bảo
vệ, desktop-live-results.json, transcript riêng hai reply, timing-evidence.log
và verification.json. Không lưu raw prompt, credential hoặc reasoning text.

## 37. Ngày 30/10: đọc lại phiên 21:52–22:03 và đối chiếu lời nói/receipt

User yêu cầu đọc log sau khi sửa Rich Chat. Chỉ đọc log, Godot conversation
history và public trace; không gửi thêm tin, mở app hay sửa runtime/prompt.
Nguồn chính `Documents/eris/logs/eris_run_20261030_215227.txt`, trace ErisLive
và `AppData/Roaming/Godot/app_userdata/Eris/eris_conversation_history.json`.

- Có 6 lượt chat user trong phiên. Từ mốc nhận user_turn ở host tới gửi text
  tại audio_start: khoảng 55,4 / 49,7 / 25,7 / 26,1 / 31,7 / 26,5 giây.
  Đây là latency câu chính, không phải micro-act nhận tin. Hai lượt chậm đầu
  có memory_context 21.824ms và perception 21.671ms (evidence 24.696ms).
  Các lượt sau evidence 2,2–3,8s, context khoảng 0,17–1,16s. Củng cố kết luận
  ở mục 36: phải đo các chỗ chờ trước Root, không quy mọi độ trễ cho prompt.
- Public trace trong khoảng phiên có **9 audio_queued, 8 audio_start,
  8 audio_done, 1 audio_cancelled, không audio_failed**. Lượt bị hủy là
  continuation delivery:0406bae602a1467c83c6c07f0f651426 đang chờ phát; bị user
  gửi "HÉT ĐI" lúc 22:01:20 ngắt. Không coi đó là TTS rơi. Log xác nhận playback
  và text-sync, không xác nhận giọng thật sự tăng âm lượng/hét bằng tai.
- Câu "hóa ra tôi đang nhìn chính tôi" là continuation_due -> Root move ->
  writer -> audio_done, channel autonomous. Có bằng chứng tự tiếp lời sau lượt
  chat trước, không phải một tin user mới. Câu dùng chi tiết cửa sổ Live2D/python
  để tự giễu; không suy rộng thành nhận thức về bản thân hay đọc pixel thật.
- Đoạn nhận biết test nằm ở phiên 21:32: 21:37 autonomous_signal tự bình luận
  về chuỗi chào/nhắc lại; 21:39 user mới giải thích rõ người test là Codex,
  21:40 Eris đùa "như bị đồng nghiệp dùng checklist soi từng câu chào".
  Tin tổng hợp trước đó đã nói rõ "kiểm tra bản build", nên đây là dùng ngữ
  cảnh và tự nối ý, không phải phát hiện bí mật. Tránh đính chính quá mức làm
  mất điểm sản phẩm thật là khả năng tiếp lời theo cùng một mạch.
- Nhận xét nội dung: giữ được giọng khô, callback và chủ động; nhưng chuỗi
  rủ hét lặp nhiều lần thành diễn giải "không hét theo đơn đặt hàng"/giải thích
  động cơ, dài hơn nhịp đùa của user. Đây là đánh giá hội thoại từ mẫu cụ thể,
  chưa phải ủy quyền làm phẳng cá tính hay thay prompt để ép vâng lời.
- 5 warning: legacy startup tìm whale2api.exe (không chứng minh Docker gateway
  chết vì Root vẫn inference thành công); một post observer provider_error /
  InferenceRejected; hai semantic-frame kind không hợp lệ được sanitize
  (observation, self_deprecating_callback); một living_user_curator_deferred /
  CuratorInferenceError. Log này chưa chứa nguyên nhân chi tiết của curator,
  không kết luận Gemini ngừng toàn bộ hay memory đã mất. Các observer sau đó
  vẫn thành công.
- Phiên kết thúc 22:03:14 bằng **context menu exit**, unexpected=False.
  Exit code -1 của child bị host dừng không phải bằng chứng crash. Điều này
  thay thế trạng thái trước "đang để desktop mở" tại thời điểm kết thúc audit.

## 38. Ngày 30/10: audit memory mới và tái hiện canonicalize gộp ngoài ID được chọn

User hỏi lỗi memory và những lượt vừa chat có lưu thông tin nhảm về họ không.
Chỉ audit và chạy probe trên bản sao, **chưa sửa runtime hay memory thật**.

**Đính chính đường dẫn:** Living User store thực sự được cập nhật trong phiên
này nằm ở `Documents/eris/.eris_home/memory/core`, không phải
`Documents/ErisLive/memory/core` (các file living_user tại đó còn cũ).
Nhận định trước ở mục 36 rằng memory runtime đều ở ErisLive không chính xác.
Public trace ở ErisLive vẫn là nguồn đúng cho audio của phiên; không suy mọi
store dùng chung một root chỉ từ vị trí trace. Căn cứ: timestamp thay đổi,
observe_turn trace chứa nguyên evidence phiên này, và curator inbox cùng ID lỗi.

Đối chiếu backup trước build `backups/fast-dev-test-20261030-213121/.eris_home/
memory/core` với store hiện tại:

- Profile tổng 147 -> 149 record. **Chỉ hai record mới:**
  `communication.playful_romcom_teasing`: đùa/thả thính không cần lý do, trích
  câu "có gái là tán", confidence 0,85, working/global;
  `interests.games.ai_game_sentiment`: user thấy game AI nói vui thì được,
  chơi thật chán, confidence 0,95, working/global. Cả hai có observe_turn
  role=user và evidence đúng câu đã nói. Nhãn đùa là đúng; nâng một câu đùa
  đơn lẻ thành pattern global độ tin cậy cao là điểm cần thận trọng, không
  diễn giải thành hành vi tán tỉnh đời thực.
- Episode 67 -> 71. Ba summary liên quan các lượt mới: nhờ Codex test độ ổn
  định; làm rõ chỉ đang xem video YouTube; đùa về hét/mỹ nữ/game AI. Nhìn chung
  giữ đúng ngữ cảnh. Episode còn lại là cuộc trò chuyện Borwein cũ được ghi
  thêm vào store trong khoảng so sánh, không phải thông tin mới về user.
- Action store byte-identical: không tạo goal/commitment/workflow mới từ trò đùa.
- Job CuratorInferenceError ở 22:01:31 có đúng envelope ID trong inbox, status
  pending, attempts=1, operations=[], source=delivered_assistant, role=assistant.
  Lần lỗi đó chưa áp operation vào profile; nguyên nhân timeout/parse/provider
  không thể phân biệt vì logger chỉ lưu tên exception. Không gọi nó là mất
  memory hay kết luận toàn bộ Gemini không chạy.

**Lỗi đã xác nhận nằm ở tầng store canonicalize, không chỉ ở lựa chọn của AI:**
`memory/living_user/store.py`, nhánh canonicalize tự lấy tất cả active/disputed
record cùng target path và scope rồi `selected.extend(collisions)`, kể cả ID
model không yêu cầu. Sau đó thay chúng bằng một value canonical, không giữ
toàn bộ meaning của những record được code thêm vào.

Audit lúc 21:40:14 chỉ yêu cầu canonicalize 3 record gần nghĩa về cảm xúc của
ERIS vào `communication.response_rules`. Probe offline dùng backup y hệt:
**3 ID được chọn -> 31 record tham gia -> 30 record bị superseded**, trong đó
28 record ngoài yêu cầu AI. Ví dụ "muốn ERIS chủ động hỏi thăm" và "khi không
hiểu, giải thích rõ từng bước" bị nhập vào câu chỉ nói về không phủ nhận cảm
xúc/bản sắc của ERIS. Tương tự, nhóm learning.active_topics lúc 21:54:58 chọn
7 mục toán/thuật ngữ nhưng code kéo thêm cả triết học, Alzheimer và các chủ đề
khác rồi đổi value thành "toán học ... và các thuật ngữ ngắn hạn".

Toàn khoảng so sánh: active **118 -> 64**, superseded 24 -> 83, disputed 3 -> 0,
deleted giữ 2; không coi mọi mức giảm là trùng lặp được dọn đúng. Ngoài lỗi mở
rộng ID, merge chọn primary và cộng evidence cũng có thể bỏ các sắc thái khác
nhau; cần review từng cụm khi phục hồi. Metadata `curated_by=deepseek_curator`
là nhãn hardcode trong store, không phải bằng chứng DeepSeek thực sự dọn thay
Gemini hiện tại. Không quy lỗi gộp quá rộng cho Gemini khi ID audit cho thấy
model chỉ chọn một tập nhỏ hơn.

Dữ liệu nguồn còn trong record superseded và backup, chưa bị hard-delete.
Ưu tiên sửa việc store tự mở rộng ID rồi phục hồi có đối chiếu, giữ hai ký ức
mới có evidence; không reset toàn bộ và không dùng keyword phân loại đùa/thật.
Đây là hướng xử lý, chưa được thực hiện trong lượt audit này.

Artifact `eris-memory-session-audit-20261030/audit.json` lưu delta, provenance,
inbox status và probe; probe chỉ ghi `isolated-probe/profile.json`. Hash profile
thật trước/sau probe giữ nguyên. Báo cáo là snapshot lúc Eris đã thoát.

## 39. Ngày 31/10: sửa curator chỉ áp ID tường minh và phục hồi các nhóm mất nghĩa

User yêu cầu "fix đi" sau audit mục 38. Đã sửa và đồng bộ sang portable;
trạng thái "chỉ audit/chưa sửa" ở mục 38 được thay thế bởi kết quả bên dưới.

- `UserProfileStore.apply_curator_operations` không còn tự thêm record cùng
  path/scope vào canonicalize. Primary, evidence và mutation chỉ lấy từ ID
  model chọn. Các record multi khác cùng path giữ nguyên toàn bộ field.
- Nếu canonicalize đụng record single-valued ở đích mà không liệt kê nó,
  operation bị từ chối bằng canonicalize_target_conflict trước mọi mutation.
  AI có thể chọn rõ các ID cần xử lý hoặc một representation phù hợp khác;
  không dùng keyword hay lớp quyết định semantic thay AI.
- System curator nói rõ path là category, không phải semantic identity;
  merge chỉ cho duplicate cùng nghĩa vì giữ wording của primary; synthesis
  cần canonicalize có value giữ các ý/điều kiện riêng. Đây là hướng dẫn AI,
  không phải lời đảm bảo mọi phán đoán semantic tương lai đều đúng.
- Test cũ bắt buộc hấp thụ collision được thay bằng reject không mutation rồi
  thành công khi AI liệt kê rõ cả target. Regression mới kiểm multi giữ nguyên
  các preference khác cùng path, không trộn evidence, và save/reload đúng.

**Khôi phục dữ liệu:** khi không có process portable đang chạy, đối chiếu exact
record với audit snapshot trước khi ghi. Hoàn tác 7 nhóm curator đã review mất
nghĩa: **66 record** được phục hồi từ backup trước phiên. Giữ nguyên **83 record
khác**, gồm cả hai profile mới có evidence. Đây không phải thay cả file bằng
backup cũ: giữ các record mới, audit và history hiện tại, đồng thời thêm audit
repair + snapshot records_before để có thể undo. Ba disputed được trả về đúng
trạng thái cũ; không tự biến chúng thành fact đã xác nhận.

Sau repair: **149 record: active 120, superseded 24, deleted 2, disputed 3**.
26 file được bảo vệ giữ hash, gồm episode/action/inbox/trace và các store khác,
secrets/.env cùng lịch sử Godot. Không xóa/retry thủ công job curator pending,
không sửa episode hay cảm xúc/vision/TTS. Backup trước repair và source portable
cũ tại `Documents/eris/backups/memory-curator-repair-20261031/`.

**Kiểm chứng:** bộ 7 file memory/curator: **76 passed, 2 failed**. Hai failure
trong test_living_user_memory_v39 liên quan context selection/clarification đã
tái hiện y hệt khi nạp UserProfileStore từ HEAD trước sửa; không tuyên bố toàn
bộ test suite pass. Probe request thật từng gây lỗi: chọn 3 ID thì nay chỉ đổi
3 ID, 144 record ngoài nhóm giữ nguyên. Smoke import chính module deployed ở
Documents xác nhận 66 record phục hồi và hai profile mới deep-equal, đọc store
không đổi hash. Không gọi Gemini/LLM live trong kiểm tra này.

Đã copy **store.py và curator.py** vào `Documents/eris/src/memory/living_user`,
hai hash khớp repo. Eris vẫn đang tắt; code sẽ dùng ở lần mở tiếp theo, không
gọi lại fast build để tránh đè các store/cấu hình ngoài phạm vi.
Artifact `eris-memory-session-audit-20261030/` có repair-plan/applied.json,
repair_reviewed_groups.py (dry run mặc định, apply tường minh, có precondition
hash/record và marker chống áp hai lần), deployment.json và portable-smoke.json.

## 40. Ngày 31/10: kiểm kê nội dung memory, seed cá tính, snapshot và log

User hỏi Eris đang lưu gì trong memory và các kho khác. Chỉ đọc và tạo báo cáo,
không chạy app/AI hay dọn dữ liệu. Snapshot sau sửa mục 39:

- Profile portable .eris_home/memory/core: 149 record, 120 active; trong active
  có communication 78 (response_rules 30), learning 17, interests 10, identity 6,
  technical 4 và 5 mục thuộc academic/philosophy/personality/work. Nhãn nguồn
  106 explicit, 7 observed, 7 inferred; các nhãn này không tự chứng minh mọi
  phát ngôn là fact ổn định. Tier: working 101, habit 15, core_anchor 4.
- Có tên Hoà, vai trò người tạo Eris, tự nhận INTP; preference tiếng Việt, muốn
  quan điểm riêng, chủ động hỏi thăm, giải thích rõ; chủ đề toán/MBTI/triết học,
  game/anime, vẽ/rig avatar. Nhiều lời đùa, tự trào và chỉ dẫn trình diễn một
  lần cũng còn là profile/rule global. Cần review theo ngữ cảnh; không tự coi
  lời tự chê tiếng Anh/code/đồ họa/toán là đánh giá năng lực khách quan.
- 71 episode; transcript Godot riêng có 72 entry (~98KB). Action store không
  có goal/workflow, chỉ 1 commitment completed cũ mang action là đoạn lời dài
  của assistant. Inbox có 3 pending từ delivered_assistant và 1 failed cũ.
- agent_mind.json: 10 beliefs và 12 preferences, **đều mang nguồn seed hoặc
  seed_infj5w4**, không phải kết quả học mới từ phiên vừa qua. Có thiên hướng
  quan sát/hài khô/ghét diễn cảm xúc và belief seed user.accepts_dry_humor.
  Chưa có phép thử nhân quả để quy toàn bộ giọng nói của Eris cho tầng seed.
- private_concerns có 24 record đều stale; nhật ký, residue, private_life_state
  còn nội dung cũ tháng 8–9. Semantic JSON chỉ 1 claim tentative về tiếng Việt.
  Concept graph 1,69MB vẫn còn trên đĩa; runtime và Root projection đã bỏ graph.
  File tồn tại/có mtime mới không tự chứng minh đang được dùng cho prompt.
- Tổng portable memory/core 11,9MB gồm cả backup/log; profile file 2,53MB nhưng
  records hiện tại JSON gọn khoảng 134KB, history khoảng 1,50MB, audit 174KB,
  còn lại gồm định dạng. Không dùng dung lượng file làm token context.
- Subject Core DB tại portable data/subject_core/subject.db ~138MB: 2.523
  events, 590 history snapshots (295 inner_life, 295 subject_state). Hai nhóm
  history có ~94,66MB và ~29,18MB payload UTF-8; không phải 138MB kiến thức mới
  về user. Bảng beliefs_projection/values_projection và các bảng commitment,
  outcome, receipt chuyên biệt đang trống trong DB này; không suy rằng mọi
  cơ chế tương ứng của toàn Eris đều không chạy.
- Portable logs 143,8MB, riêng observability 141,1MB; backups 262,1MB chủ yếu
  còn các bản build trước/phục hồi. ErisLive có memory cũ lẫn state/log khác
  còn hoạt động (public trace), còn root portable memory_core cũng có dữ liệu
  legacy. Không xóa một root chỉ vì một số store trong nó cũ.

Artifact `eris-storage-inventory-20261031/` có sizes.json, contents.json và
inventory.md (toàn bộ profile active/disputed, episode và seed beliefs/preferences).
Đây là kiểm kê nội dung và dung lượng, chưa phải authorization dọn/xóa tiếp.

## 41. Ngày 31/10: cho Gemini diễn giải memory bằng nguồn và ngữ cảnh

User yêu cầu cải thiện Gemini để lọc kiến thức hữu ích, sau kiểm kê mục 40.
Không dùng phân loại ý nghĩa bằng từ khóa, không đổi cá tính/vision/TTS và không
reset hay chạy GC trên profile thật trong đợt này.

**Chẩn đoán và thay đổi:** extractor đã có 6 lượt gần nhất, nhưng worker trễ có
thể đọc đuôi hội thoại khác với thời điểm enqueue; GC chỉ nhìn record/cluster,
episode cũ không giữ lời gốc để kiểm lại diễn giải. Không chính xác nếu nói mọi
đường Gemini trước đây hoàn toàn không có context.

- Capture một cửa sổ có role/session/source ID ngay khi enqueue và lưu cùng
  durable inbox. Worker/retry dùng snapshot đó, không lấy lời nói tương lai.
  Capture lỗi vẫn giữ current turn, chỉ ghi loại lỗi, không bỏ evidence.
- Episode mới giữ nguồn ngắn: tối đa 32 lượt lúc dựng, 48 sau reconsolidation,
  1.800 ký tự/lượt. Context gửi Gemini tối đa 12 lượt gần đây, 6 episode và ngân
  sách 20.000 ký tự phần text (không phải tổng JSON hay số token). Cần nguồn cũ
  thì dành ngân sách cho episode match và ưu tiên lời gốc quanh ID được dẫn.
- GC nhận nguồn có phân biệt user/assistant. Episode không có lời gốc được đánh
  dấu secondary; ref legacy trùng nhiều session được đánh dấu ambiguous. Không
  dựng lại lời gốc từ summary hoặc coi thiếu nguồn là bằng chứng memory sai.
  Episode ngoài project scope bị loại trước khi dựng context.
- Prompt extractor/episode/GC phân biệt tự trào, lời đùa, nhập vai, thử hệ thống,
  chỉ dẫn một lượt, correction và yêu cầu lâu dài có điều kiện. Không nâng lời
  assistant thành xác nhận của user; reinforce giữ nguyên path/value và mọi
  qualifier còn đúng. Lời sửa hiểu nhầm nhất thời không tự thành sở thích global.
- Raw source window không được lặp vào mutation trace hoặc Root recent_episodes;
  Root giữ summary/evidence. Không thêm lời gọi AI/embedding vào enqueue. Nhãn
  curated_by cho mutation mới đổi từ deepseek_curator thành living_user_curator
  vì store không biết provider; không viết lại metadata lịch sử.

**Gemini thật:** Docker Desktop không khởi động được do socket sailor-ingest.sock
  bị OS từ chối truy cập. Không reset Docker hay xóa dữ liệu. Chạy chính
  tools/gemini_vision_docker/server.py bằng venv riêng ngoài repo, bind
  127.0.0.1:8000, dùng cache đăng nhập hiện có, không in cookie. Service đã
  authenticated; session chỉ công bố **gemini-flash-lite / 3.5 Flash-Lite**.
  Adapter yêu cầu gemini-flash; server hiện fallback model mặc định khi alias
  không resolve. Không gọi kết quả này là phép thử Gemini Flash đầy đủ/Pro.
  Host service được để chạy cho lane hiện tại; không cài autorun. Docker vẫn cần
  sửa riêng; khởi động Docker service port 8000 sau này cần dừng host helper.

Sáu tình huống synthetic: joke, self-deprecation và roleplay không tạo profile/
action; yêu cầu giải toán giữ cả bước giải/ký hiệu/ngoại lệ; reinforce giữ đúng
value cũ. Lượt đầu correction vẫn tạo global media interest, reinforce làm rơi
chi tiết ký hiệu; đã sửa prompt rồi thử lại ba ca correction/reinforce/lasting
preference, kết quả đúng như mong đợi. Đây là kiểm tra tình huống, chưa phải
chứng minh mọi hội thoại ngoài thực tế đều phân loại đúng.

GC live trên kho thử riêng: review 4 record, xóa 2 fact diễn giải sai từ joke/
tự trào; canonicalize 2 yêu cầu toán thành 1, giữ cả hai ý và evidence. Không
áp các operation thử lên profile thật. Các record cũ thiếu nguồn vẫn cần review
thận trọng; không backfill transcript bằng suy đoán.

**Kiểm chứng và triển khai:** 80 test liên quan pass (8 file); bộ mở rộng thêm
19 pass/1 fail ở test_living_user_competence_claim_is_context_only_not_teaching_authority.
Failure compile_context trả rỗng tái hiện nguyên trạng với service.py từ commit
6ec42c4d trước thay đổi, không báo toàn suite pass. Copy 9 module Python vào
Documents/eris, hash khớp repo; smoke import chính portable xác nhận context và
Root bỏ raw sources. 58 file memory/config/history được bảo vệ giữ nguyên hash;
profile vẫn có SHA256 48c37397c0180ef3f1f7d86016cdd69c9c307d86bfdface91a80970b27f647a6.
Không build đè UI đang được sửa đồng thời, không mở app để drain inbox thật.

Backup Documents/eris/backups/gemini-memory-quality-20261031-212904.
Artifact eris-gemini-memory-quality-20261031/ có live-eval.json (lượt đầu),
live-eval-refined.json, live-curation.json, deployment.json, portable-smoke.json
và launcher host service tái lập được. Không đưa log service/cookie vào báo cáo.

## 42. Ngày 31/10: dọn profile cũ theo yêu cầu mới, giữ lịch sử phục hồi

User yêu cầu trực tiếp “dọn memory cũ rác”. Đây là authorization mới cho việc
dọn dữ liệu, thay giới hạn chỉ kiểm kê/chỉ sửa code ở mục 40–41. Khi Eris được
mở lại trong lúc rà, đã hỏi cách tránh ghi đè writer đang chạy; user chọn
**tự đóng Eris**. Chỉ áp sau khi xác nhận Eris/runtime đã dừng; không force kill
và không tự mở app lại để phát sinh mutation trong kiểm tra.

**Review:** Gemini Web Flash-Lite đã xem 123 record active/disputed theo ba
nhóm, với 71 episode secondary và 72 entry UI history. Không giả định ref
session_turn:N xác định duy nhất một nguồn. Đề xuất ban đầu có dẫn sai UI index,
gộp mất điều kiện, dùng merge sai semantics và muốn bỏ nhu cầu chủ động quan tâm;
không áp tự động. Codex đối chiếu lại nguồn và lập 59 operation tường minh,
lượt Gemini phản biện sau đó accept đủ 59. Không dùng semantic keyword filter,
không ép quota giảm số record, không coi mọi câu đùa đều rác: giữ pattern trêu
thân mật/cách gọi/giọng chill có căn cứ, cất các tình tiết biểu diễn một lần.

- Cất 53 record khỏi profile active: nhãn tự chê tiếng Anh/code/đồ họa; đề bài
  toán/thuật ngữ, chủ đề từng hỏi và tiến độ xem nhất thời; luật global suy từ
  yêu cầu cười/khóc/idol/diễn một lần; phản ứng/roleplay/cảm thán cụ thể.
  Archive có thể phục hồi, không hard-delete nguồn hay kết luận đã làm xong bài.
- Sáu nhóm canonicalize giữ đủ ý: bản sắc/cảm xúc, giải thích đánh giá cụ thể,
  giải thích lại khi chưa hiểu và bám đúng đề toán, phản hồi MBTI có căn cứ/
  nhất quán/không chẩn đoán, bốn cách gọi thân mật, đánh giá cao sáng tạo của
  Eris. Giữ union evidence; chỉ đổi ID liệt kê, multi cho path có nhiều nhu cầu.
- Giữ tên/vai trò người tạo, tự nhận INTP, dự án tự vẽ/rig và vướng xoay mặt/3D,
  ChatGPT cho đồ án/Gemini cho vision, nhu cầu học/quan điểm riêng/chủ động hỏi
  thăm, sở thích và các record khác ngoài danh sách. Bốn core anchor giữ nguyên.
- Sửa 25 episode: bỏ 38 open_loop không phải công việc thực sự (câu xã giao
  chưa được trả lời, phát ngôn/lời đùa chép vào open_loop, dự đoán sẽ quay lại).
  Sửa summary/user_signals đoạn “mỹ nữ” thành ngữ cảnh video YouTube, không
  gán đang chơi game hay hành vi tán tỉnh ngoài đời. Giữ việc/câu hỏi khó thực sự
  chưa rõ kết quả theo lịch sử, không tự đánh dấu completed. Giữ toàn bộ episode,
  nguồn/ID/timestamp/scope. Review episode Gemini bị thiếu coverage rồi timeout;
  25 sửa cuối ghi rõ **Codex semantic review**, không tuyên bố Gemini đã xác minh.
- Chuyển commitment completed commit_dcbd3d748a0bd60762 sang archived qua
  ActionMemoryStore có audit. Đây là đoạn phản hồi triết lý dài của assistant
  bị lưu nhầm, không phải lời hứa/hành động. Không có goal/workflow để dọn.

**Concurrent state:** lúc đầu profile v209 có 120 active/3 disputed. App tự chạy
GC trong lúc rà, đổi 9 record (gồm trạng thái disputed), tạo thêm episode rồi
profile tới v211. Đã giữ snapshot/audit thay đổi đồng thời; không ghi cả backup
v209 đè lên bản mới. Hai source trùng 71922f/64ea44 đã superseded và hấp thụ vào
target tương ứng thì bỏ khỏi input canonicalize mới, giữ evidence đã hấp thụ.
Các record ngoài operation so với v211 được giữ deep-equal; không nói rằng đợt
dọn này tự xác nhận các disputed. Không hồi lại trạng thái cũ bằng suy đoán.

**Đã áp 22:02:** profile v211 → v212: **118 → 55 active**; tổng vẫn 149 record,
gồm 55 active, 39 superseded, 53 archived, 2 deleted cũ. 59 operation thay đúng
69 record, 80 record khác giữ nguyên. Episode vẫn **72**, giữ cả episode mới
phát sinh trong lúc review. 26 file memory/config/UI history ngoài ba target giữ
nguyên hash. Không thay seed cá tính, concept graph, observer/vision/TTS, inbox
hay transcript. Không gọi build hoặc sửa source runtime trong đợt dọn dữ liệu.

Backup ngay trước áp tại
`Documents/eris/backups/old-memory-cleanup-apply-20261031-220220/`, thêm backup
đầu phiên `old-memory-cleanup-20261031-213943/`. Áp có guard runtime đã tắt,
hash precondition từng target, marker chống áp lại; ghi từng file atomic sau khi
đã backup đủ ba file (không phải transaction atomic xuyên ba file).
Smoke bằng module chính portable xác nhận load 55 current/149 total, đủ 4 core
anchor, 72 episode, commitment archived và ID đã archived không còn trong
snapshot profile. Load kiểm tra không đổi hash. Không tuyên bố đã test hội thoại
live sau dọn. Artifact `eris-old-memory-cleanup-20261031/` có proposals, phản
biện, diff, staged candidates, `stage_apply.py`, `applied.json`, `verification.json`
và `cleanup-review.md`. Toàn bộ transcript/episode được giữ để truy hồi bối cảnh;
archive profile không đồng nghĩa xóa ký ức lịch sử.

## 43. Ngày 31/10: cơ chế hoạt động Inner Diary và kết nối modern digest trong C#

User nhận thấy panel Inner Diary (nhật ký nội tâm) bị trống, nghi ngờ không có
luồng dữ liệu điền vào và hỏi cơ chế vận hành hiện tại.

**Cơ chế thực tế theo kiến trúc:**
- **Hệ thống cũ (Legacy):** Từng dùng một background worker (`PrivateMindLoop`)
  chạy một vai trò mô hình độc lập (`ERIS_INNER_DIARY_WORKER_ROLE`) để sinh ra
  các shard văn bản tách rời (`current_thought`, `future_bias`, `open_concerns`,
  `temporary_thoughts`, `surface_bias`). Luồng này đã bị retire vì gây lãng phí
  token, trễ pha và dễ sinh ảo giác (hallucination) không khớp nhận thức trung tâm.
- **Kiến trúc hiện tại (Architecture C - `ai_orchestrated.v3`):** Không còn luồng
  model độc lập riêng để bịa shard. Nhận thức của Eris được thống nhất trong
  `RuntimeMindState` (gồm động cơ `active_motives`, mâu thuẫn `active_conflict`,
  phòng vệ `activated_defenses`, diễn giải `self_interpretation`, ý định
  `latest_intention`, khám phá `discovery`, và `open_loops`). Sau mỗi turn hoặc
  nhịp tự chủ, `ai_orchestrated/panel_adapter.py` chiếu các trường thực này thành
  bản tóm tắt `private_mind_digest` (schema `eris.inner_state.panel.v1`) và chuyển
  sang Godot qua gói UDP (hỗ trợ cả gói kèm hội thoại và lane `panel_state_update`).
  Do đó, luồng dữ liệu **vẫn tồn tại và liên tục được gửi**.

**Nguyên nhân gốc rễ khiến panel trên Godot bị trắng:**
1. **Lỗi cổng chặn (Gate rejection) tại `PrivateMindDigestHasContent`:**
   Hàm này chỉ kiểm tra các trường legacy (`TryReadDiarySections`, `menu.current_thought`,
   v.v.). Khi nhận digest modern (`eris.inner_state.panel.v1`), hàm trả về `false`,
   khiến gói bị vứt bỏ ngay lập tức và `_latestPrivateMindDebugJson` không bao giờ
   được cập nhật.
2. **Thiếu kết nối (Unwired adapter) trong `RefreshPrivateMindPanel`:**
   Dù `UdpListener.IdentityPrivateMind.cs` đã viết sẵn các hàm adapter (`IsModernIdentityDigest`,
   `BuildModernIdentityDiarySlots`, `RefreshModernIdentityConcernStack`,
   `RefreshModernIdentityMemoryEcho`, `RenderModernIdentityDigest`), `RefreshPrivateMindPanel`
   ở `UdpListener.cs` chưa từng gọi chúng mà chỉ gọi các extractor legacy rỗng.

**Xử lý:**
- Đã kết nối `IsModernIdentityDigest` và `ModernIdentityDigestHasContent` vào
  `PrivateMindDigestHasContent` và `PrivateMindWriterSummary`.
- Trong `RefreshPrivateMindPanel`, phân nhánh nếu là modern digest thì dựng 5 slot
  nội tâm từ nhận thức thật (`self_interpretation`, `latest_intention`, `active_conflict`,
  `active_motives`, `activated_defenses`, `discovery`, `open_loops`) và cập nhật
  sidebar cards.
- Bổ sung fallback hiển thị `active_motives` trong concern stack khi trạng thái
  nội tâm đang tĩnh lặng (không có xung đột hay kích hoạt phòng vệ).
- Đã build C# sạch 0 warning/0 error, export lại Godot `Eris.pck` tươi và chạy
  `build_dev_fast.bat` thành công 100%. Đã bổ sung bộ kiểm thử tự động tại
  `src/tests/test_inner_diary_panel_modern_sync.py`.

## Nghiên cứu ngày 31/10: Tutor và các repo mã nguồn mở

Theo yêu cầu user, đã đối chiếu 16 repo bên ngoài với source Tutor hiện tại.
Xem [bản nghiên cứu và quyết định tích hợp](2026-10-31-tutor-open-source-integration-study.md)
và [manifest nguồn ghim commit](2026-10-31-tutor-open-source-sources.json).

Đính chính so với khung Learning Intelligence trong bản ghi user gửi: Eris đã có
LearningService/SQLite, evidence ledger, TutorExecutive, auto-path, BKT shadow và
review heuristic. Review hiện chưa phải FSRS; BKT tự ghi rõ chưa calibrated.
Đây là đối chiếu source, không phải xác nhận chất lượng sư phạm hay runtime live.

Hướng đề xuất là mở rộng đúng kernel/capability hiện có: attempt ghi mức trợ giúp,
nguồn có locator/version, sửa path theo revision, adapter py-fsrs và workspace chọn
theo hoạt động. Không phục hồi ConceptGraphStore/multi-hop memory đã gỡ ở mục 26;
prerequisite trong learning path là dữ liệu sư phạm có phạm vi riêng. Root tiếp tục
sở hữu lời nói và quyết định hội thoại; Tutor trả advice/evidence. Tài liệu đề xuất
chưa phải thay đổi code, dependency hoặc ủy quyền triển khai tương lai.

Đã chạy ba file test hiện có về learner/persistence/kernel boundary: **19 passed
in 1.88s**. Chỉ xác nhận các contract offline được test; chưa cài hoặc kiểm thử
FSRS/Docling/workspace đề xuất và chưa triển khai bản portable trong vòng này.

## Cập nhật ngày 01/11: tích hợp Tutor từ nghiên cứu mã nguồn mở

User đã yêu cầu triển khai và tiếp tục. Đã mở rộng LearningService/TutorCapability
hiện có với AssessmentAttempt tách kết quả/trợ giúp, correction/forget và projection
transactional; nguồn có locator/hash/version và retrieval theo domain; curriculum
namespace/DAG/revision/CAS; py-fsrs 6.3.2 shadow; báo cáo policy observational; ba
preset bài dự đoán mã Python có nút trong Rich Chat. Root vẫn sở hữu public text/TTS,
receipt xác nhận giao bài/trợ giúp; không thêm agent loop hoặc phục hồi memory graph.
Có đường bắt đầu môn mới dùng planner hiện có, tiếp tục path đã lưu mà không ghi đè.

[Báo cáo triển khai v1](2026-11-01-tutor-open-source-integration-v1.md) **thay thế
kết luận “chỉ có đề xuất/chưa cài hoặc kiểm thử FSRS/workspace” ở mục nghiên cứu
31/10 cho các phần trên**. FSRS thật đã cài và test trong Python phát triển; mặc
định shadow, active mode tùy chọn có fallback heuristic rõ scheduler owner.
Docling chỉ có lazy adapter, chưa cài/test PDF thật; MathLive/JSXGraph/Pyodide chưa
tích hợp. BKT chưa calibrated và chưa có bằng chứng cải thiện kết quả học nhân quả.

Nhóm Tutor/learner/kernel/workspace đầy đủ cuối: **197 passed, 1 failed** do race
workspace có sẵn (`queued`/`running`); bộ Tutor mới **33 passed**. Node UI: **3 passed**;
Ruff và JS syntax pass; C# build **0 warning/0 error**. UI fixture kiểm tra browser
thật ở 1280px/390px, hint→answer ghi assistance đúng; Root/delivery giả lập, không
phải model/TTS live. Sáu lỗi của quét conversation rộng tái hiện đúng trên HEAD gốc,
chi tiết trong báo cáo; race workspace pass khi chạy riêng và ở một lần toàn nhóm
trước đó, vẫn tái hiện ở lần cuối nên không kết luận bộ kiểm tra xanh hoàn toàn.
Xác nhận cuối khi tách đúng race đã biết: **197 passed, 1 deselected, 44.91s**,
không thay source parallel work để đạt kết quả này.
Chưa export/deploy portable hoặc kiểm tra hội thoại có voice trên bản đang chạy.

## Cập nhật 01/11: Learning Canvas, phạm vi Task 1–3

Đã thêm gói Java DI, snapshot từ LearningService, focus có revision/pin lưu trong
learning SQLite và panel Godot native để đọc/điều hướng. CanvasControlInbox đọc
transport trên worker riêng; chỉ Canvas request được xử lý ở lane này, các gói
khác vẫn chờ foreground. Vì vậy nhận định control bị poll cùng Root ở mục 22
**không còn áp dụng cho route Canvas mới**; không suy ra settings hoặc mọi route
khác đã được chuyển sang fast path. Canvas không gọi chat/kernel/TTS.

Bộ liên quan 137 test pass; UDP thật đạt assertion <200 ms khi foreground giả
bị chặn; C# 0 warning/error; preview OpenGL pass cả đủ dữ liệu/rỗng ở 5 kích
thước. Đây chưa phải kiểm chứng model/voice thật hoặc bản portable đã triển khai.
Chi tiết và giới hạn tại [báo cáo Task 1–3](../learning-canvas/implementation-tasks-1-3.md).
Các phần Root capability, chấm Java, focus trong delivery và highlight vẫn chưa
triển khai; không xem dữ liệu bài tập/preview là vòng học hoàn chỉnh đã chạy.

## Cập nhật 01/11: Learning Canvas, Task 4–6

Theo yêu cầu tiếp theo, đã nối capability Canvas cho Root và event tutor_result
có origin=user_ui; UI không gọi chat hay tăng generation. Đã sửa hai biên mất
event busy: orchestrator không pop, kernel không cache busy để cùng ID thử lại.
Root tiếp tục sở hữu lời nói; package Canvas chỉ đưa dữ liệu/assessment vào queue.

Trắc nghiệm chấm theo khóa backend và ghi AssessmentAttempt vào SQLite; mức trợ
giúp còn qua restart/nonce mới của cùng bài. Java có bộ chạy Docker giới hạn tài
nguyên, worker riêng và UI CodeEdit/kết quả test; unavailable tự chuyển sang quiz,
không tạo attempt giả. Báo cáo ở [Task 4–6](../learning-canvas/implementation-tasks-4-6.md).

Đoạn Task 1–3 phía trên nói “Root capability/chấm Java chưa triển khai” **được
thay thế cho các phần đã có mã ở Task 4–6**. Không thay bằng kết luận JVM đã chạy:
Docker Desktop vẫn lỗi sailor-ingest.sock, chưa có engine để đo spike/read-only/
noexec hay chấm Java thật. Root/model/delivery được kiểm tra giả lập, Godot dùng
renderer OpenGL thật và fixture RAM. Chưa deploy portable hoặc xác nhận voice live.
Focus giữa delivery, highlight và đầy đủ chính sách chấp nhận/bỏ ghim vẫn thuộc
Task 7–10, chưa được hoàn thành trong đợt này.

## Cập nhật 01/11: Learning Canvas, Task 7–10

**Thay thế hai kết luận source lịch sử ở mục điều tra 15/10:** checkout hiện tại
có `ô`/`ồ` trong `interaction_dynamics._BACKCHANNEL_TOKENS`; ChatCycle
`_preempt_for_classified_user_turn` nhận `cancel_generation=True` và kiểm cờ
trước khi hủy generation. Đối chiếu trực tiếp
[classifier](../../src/runtime/interaction_dynamics.py) và
[ChatCycle](../../src/runtime/chat_cycle.py), cùng regression
`test_20261015_tts_interrupt_repair.py`. Không dùng câu “ô ngoài whitelist” hoặc
“ChatCycle chưa nhận cancel_generation” để chẩn đoán bản hiện tại. Quan sát hủy
delivery trong trace cũ vẫn là lịch sử của phiên đó; test không thay bằng chứng live.

**Thay thế trạng thái “focus/highlight còn thuộc Task 7–10” ở mục trước:** đã có
note_focus_change/epoch, bỏ run chưa commit nhưng giữ delivery đã commit, hủy
lease/speculation của nhánh cũ, coalesce click và wake sau settle. Click Canvas
không đi qua user_turn. Research/work chụp node_id + focus_revision khi khởi
chạy, kết quả cũ giữ thành huy hiệu SQLite thay vì tự nói. Revision CAS và
focus_revision tách riêng để ack không vô tình làm cũ tác vụ.

Root gắn block_id cho các brief Canvas; writer hiện thực qua pipeline hiện có,
binding vào artifact/frame. Chỉ callback audio_start thực sự mới highlight;
queued chưa chứng minh playback. Canvas dùng từng segment thay vì inline,
Godot lọc stream/turn/generation/seq và giữ chip quay lại nhánh đang nói. Pin chỉ
nhả khi user chấp nhận proposal; focus của Eris không sinh navigate echo.

Nghiệm thu cuối: **333 passed, 5 skipped** trên 32 file Python, C# 0 warning/error,
preview Godot/OpenGL pass đủ dữ liệu/rỗng ở 5 kích thước. Đã export debug mới và
build dev thành công vào scratch, chưa triển khai lên bản cài đang dùng.
Chi tiết: [báo cáo Task 7–10](../learning-canvas/implementation-tasks-7-10.md).

Các giới hạn vẫn còn: năm JVM integration test skip vì Docker engine chưa chạy;
chưa đo JVM/read-only/noexec thật. E2E dùng model/delivery/Java giả lập, kiểm audio
dùng TTS manager + device giả; không xác nhận provider/loa hoặc vòng học live.
Hiệu quả học tập chưa được chứng minh; cần bài độc lập và kiểm tra nhớ lại trễ,
không lấy mastery/FSRS shadow làm chứng minh đã học vững.

## Cập nhật 02/11: hai phiên im lặng mới nhất và Learning Canvas

Đối chiếu portable run log 13:40/13:45, observability và raw wire PID 2112
(khôi phục objects có kiểm SHA-256). Bảy module Root/kernel/writer/context/Canvas
trùng byte checkout. Chi tiết: [audit im lặng/Canvas](2026-11-02-silent-startup-learning-canvas-audit.md).

- Phiên 13:40: gateway 5103 từ chối kết nối, Official fallback 401 invalid key;
  Root chưa tạo được lời public. Không phải receipt lỗi TTS/context overflow.
- Phiên 13:45: warm-up 72,093s; opening request 36,938s có move trên wire nhưng
  không tới writer. Source focus invalidation và 11 navigate trong log giải
  thích run opening mất hiệu lực; chưa có structured stale receipt riêng.
  Navigate cuối được gom thành một tutor_result; Root mất 27,031s rồi gọi
  writer 13:48:17. App thoát 13:48:27 khi writer chưa có response/audio_start.
- **Thu hẹp giả thuyết learning nhồi context:** request Canvas 137.100 ký tự
  content, capability Canvas + learner_continuity khoảng 5,9k (~4,3%). Gateway
  báo prompt_tokens 43.217 và HTTP 200; không có overflow/trim. Self và receipts
  lớn hơn phần học. Không lấy usage gateway làm token billing hay quy mọi
  latency cho prompt. Không cắt learning/memory, sửa runtime hoặc restart.

Đây là bằng chứng live request/response của Canvas sau các cập nhật 01/11,
**không thay giới hạn chưa xác nhận voice/JVM/effectiveness live** ở các mục đó.
ElevenLabs warm-up thành công không chứng minh synthesis hoặc playback; hai
phiên này chưa tới audio nên không kết luận provider/loa khỏe hoặc hỏng.

## Cập nhật 02/11: chất lượng phiên hội thoại 14:17–14:28

Nguồn mới: run `eris_run_20261102_141704.txt`, raw PID 20376 (khôi phục payload
có kiểm checksum), public history và audio lifecycle. [Đánh giá hội thoại](2026-11-02-1417-conversation-quality-audit.md).
Chín lượt public commit ở audio_start; tám delivered/done, một audio bị ngắt
khi user gửi tiếp. **Thay giới hạn chưa thấy voice ở hai phiên trước trong
phạm vi phiên này**, không tuyên bố lỗi provider/focus cũ đã được sửa hoặc
chất âm đã được nghe/chấm.

Đánh giá chủ quan khoảng 6/10: có hài khô và nối được trò bò phản lực, nhưng
lặp hỏi dò “anh đang thử tôi?” và đòi bằng chứng cho premise đùa; user chat
chờ khoảng 26–35s tới audio. Wording chủ yếu đã có ở Root, surface giữ gần
nguyên văn. Lượt tutor cuối nhận đúng result=incorrect nhưng không có lựa
chọn user; câu “đa số sai vì…” vượt evidence của event. Không sửa runtime
trong lượt đánh giá; không dùng observer success làm bằng chứng chất lượng.

**Bổ sung latency cùng phiên:** user giữ nguyên hội thoại, hỏi vì sao chậm.
Lượt 14:25:29→14:25:57.945 gồm ~3,7s input/evidence/context, Root HTTP 8,203s,
surface HTTP 5,360s, TTS HTTP 10,893s và ~0,7s chuyển bước/playback. Chín TTS
request trong phiên mất 8,379–11,746s, queue chỉ 1–9ms. TTS source trùng
portable: nhận stream nhưng gom hết bytes của segment trước playback, chưa
phát ngay chunk đầu. Không có first-byte timer trong log để hứa mức giảm.
Root wire thinking enabled/high, surface disabled; không có localizer call
riêng trong lượt này. Không quy toàn bộ HTTP latency cho thinking/context;
observer chạy sau delivery, không chặn câu này. Xem phần bổ sung của báo cáo
trên. Không sửa runtime hoặc tắt thinking.

**Bóc request 14:25:32 theo câu hỏi tiếp:** tổng 139.934 ký tự message content
gồm system 14.090, Self bootstrap 79.117, runtime_context 29.504, overlay Self
14.505, history 2.017 và event/envelope 701 (phân hoạch không đếm trùng).
Living_user trong Self 50.538: profile 38 record/25.581, 8 episodes/21.587,
actions/3.328. Runtime có 10 move receipts/10.564 và learner_continuity 3.019;
text user chỉ 105. Đây là số của request thật, không nhầm với log chars/3
hoặc request bytes. Self prefix vẫn được gửi lại cùng overlay; warm-up không
biến nó thành dữ liệu remote miễn gửi lại. Chi tiết ở báo cáo cùng phiên.

## Cập nhật 02/11: xác minh commit cắt projection 77fc3a13

Đã đọc commit, `cheap-cut-implementation.md`, `self-context-cut-review.md`
trong `.agents/tasks/eris-token-growth/` và code hiện tại. Commit gồm một
module runtime, hai file test và implementation report (không phải tổng cộng
một file). Report/review có verdict APPROVED; không chạy lại test trong lượt
giải thích này. Mức giảm living_user 33.674→16.386 là fixture tổng hợp, không
phải đo request portable sau triển khai.

**Thay trạng thái lossless/giữ metadata.reason ở mục 33 cho projection mới:**
profile bỏ evidence_ids, metadata và bốn timestamp; episode bỏ evidence_ids,
metadata, started_at/ended_at/session_id/source. Canonical store không đổi,
mind_state/actions không đổi. Tuy nhiên đây là projection có mất trường;
metadata.reason có thể chứa điều kiện/giải thích ngoài value, timestamp chứa
mốc thời gian. Không nâng “store nguyên vẹn/test pass” thành bảo đảm Root
không mất thông tin có nghĩa; chưa audit semantic coverage từng record.

Kiểm tra SHA-256 tại lúc đọc: module portable `Documents/eris` khác checkout,
chưa có `compact_episode`; do đó chưa có bằng chứng bản portable đã dùng
commit này. Không tự copy/restart trong lượt hỏi giải thích.

Đo thêm payload thật 14:25:32 trước sửa (cùng manifest đã kiểm): profile
metadata tổng value JSON ~7.962 ký tự, evidence_ids ~3.836; episode
evidence_ids ~9.167, metadata ~629. MindState bootstrap chỉ 7.182, chủ yếu
personality 3.265 và identity 3.193; không đồng nhất nó với Self 79.117.
Mind overlay lúc lượt bò phản lực 7.215, lớn nhất latest_observation 3.128 và
goal_records 2.822; state còn gồm relationship/appraisal/open loops. Không
đồng nhất MindState structured state với raw private reasoning.

## Cập nhật 02/11: đánh giá thiết kế episode mờ dần

Chỉ đối chiếu source checkout, không sửa runtime/canonical memory, gọi Gemini,
chạy GC/test hay deploy. Xem [đánh giá thiết kế](../../.agents/tasks/eris-token-growth/episode-fading-design-review.md).

Dreamer Living User tạo ProfileOperation, chưa tạo rolling recap. Self còn nhận
8 recent episodes từ service.summary qua memory_cognition.load; chỉ sửa
context_compiler không thay đầy đủ đường vào Root. Episode cùng session/scope
có thể đổi nội dung dưới ID cũ; compact và trim có loại bản gốc. Thiết kế recap
cần coverage theo nội dung, commit summary+coverage nguyên tử, giữ nguồn nếu
cam kết truy lại, và tham gia scope/correction/forget. Nhãn tương đối phải tính
từ anchor khi render, không lưu “tuần trước” như sự thật bất biến.

**Đính chính diễn giải trong curator-distill-persist-report.md:** rolling recap,
episode→profile và episodic→semantic là các consumer khác nhau; một cờ đã nén
không thay dedup của cả ba. Không gộp lịch chạy hai hệ thành cùng nhịp GC 10 phút:
MemoryGCService gọi trực tiếp MaintenanceRunner, còn living_user.dream có đường
riêng qua MemoryKernel.maintain từ background digest worker và session. Interval
worker mặc định 300s có các gate, không chứng minh số call/tần suất live. Giữ
kết luận source rằng output profile/semantic đã có persist và checkpoint nguồn
còn thiếu; chưa đo re-distill thực tế trong lượt đánh giá này.

## Cập nhật 02/11: triển khai episode mờ dần trong source

Theo yêu cầu tiếp theo, đã thêm rolling recap theo scope, lưu narrative+coverage
nguyên tử, source hash/revision/authority check và retry; archive episode v2 giữ
nguồn trước trim/merge. Dreamer profile có checkpoint/pending replay riêng;
consumer episodic→semantic ghi tag/hash và quyết định skip riêng. Xem
[báo cáo triển khai](2026-11-02-episode-fading-memory-v1.md).

**Thay trạng thái “chưa triển khai” của đánh giá trước cho các phần trên.** Self
không còn cấp 8 episode mặc định: episode view lấy từ facade hiện tại, tối đa
4 episode trong 7 ngày + recap cũ. Episode không đóng băng trong bootstrap
bất biến; mỗi request nhận projection hiện tại trong overlay, tránh chi tiết
đã mờ/quên vẫn kẹt trong prefix. Profile/MindState giữ cơ chế cũ, không thêm
Root warm-up khi aging. Budget dùng UTF-8 byte bound bảo thủ, chưa đo usage thật.

Nhóm 18 file: **191 passed, 4 failed**; 35 test mới pass. Bốn lỗi selection/UI
đã tái hiện trên baseline 24614d18; Ruff và diff whitespace pass. Request Root
được kiểm qua fake router, không phải model live. Smoke Gemini chỉ dùng dữ
liệu giả bị timeout, giữ checkpoint và deferred retry. Chưa xác nhận chất
lượng recap Gemini thật hoặc giảm token/latency portable; chưa ghi memory thật,
copy source portable, restart hay deploy. Semantic commit và mark nguồn vẫn
khác file, nên không tuyên bố exactly-once nếu crash giữa hai bước.

## Bằng chứng mới: phiên 21:54–22:05 ngày 03/11/2026 (async summary, tắt OCR)

**Mức bằng chứng:** đọc source, script tái hiện với adapter Gemini giả (ngủ 6 s)
và test pytest với fake. Không có quan sát live, không chạy bản portable, không
gọi Gemini thật, không đo latency hay chi phí vision thật.

**Quyết định của người dùng:** summary episode chạy riêng, không chặn chat; nếu
không có summary mới thì không dùng summary giả; Gemini tạo xong phải lưu lại;
tắt OCR vì đã có vision.

**Tái hiện trước khi sửa** (`results_before.json`): dreamer gọi Gemini bên trong
`_turn_buffer_lock`. Trong lúc adapter giả ngủ 6 s, `capture_interpretation_context`
chờ 5,7 s, `_enqueue_curator` 6,0 s, `record_conversation_turn` 6,0 s và đường
hot path ingest 6,0 s. Stack của thread nền dừng ở `flush_session` →
`_flush_buffer_locked` → `summarize_episode`. Adapter lỗi thì `dream()` ghi
episode heuristic `episode_fallback` và xóa buffer. Với OCR chưa tắt, `start()`
dựng cả hai backend OCR và chạy `pip install rapidocr-onnxruntime`.

**Đã đổi:** `LivingUserMemoryService` có worker `ERIS-LivingUserEpisode`;
snapshot dưới lock, gọi Gemini ngoài lock, lưu episode rồi mới trim đúng các
dòng của snapshot. Timeout 60 s (adapter và join), single-flight, backoff
60 s → tối đa 600 s, hoãn khi có lượt user đang chạy. Lỗi hoặc timeout giữ
nguyên buffer và không ghi episode; fallback heuristic chỉ khi không có adapter
hoặc buffer ≥ 40 dòng. Trace `episode_consolidation` ghi vào
`living_user_trace.jsonl`. OCR tắt mặc định qua `ERIS_SCREEN_OCR_ENABLED=0`:
không import backend, không pip, không `force_ocr`; tham chiếu màn hình tường
minh được vision ngay trong cùng lượt. Sau sửa (`results_after.json`) cùng các
lời gọi trả về trong 0,001–0,019 s.

**Thay các kết luận cũ:**
- “MemoryGC → maintain → dream” sai. `MemoryGCService` dùng
  `MemoryMaintenanceRunner`; người gọi `MemoryKernel.maintain()` là
  `BackgroundMemoryDigestKernelService` (thread `ERIS-MemoryDigestKernel`, mỗi
  300 s khi rảnh). Lock còn bị giữ ở hai đường khác trên thread ghi turn
  (đổi session, buffer đủ 28 dòng).
- “OCR chỉ chạy khi chat” sai: worker nền cũng OCR theo tick 15 s, khi đổi
  cửa sổ, và tự pip install lúc khởi động.
- “Gemini lỗi thì giữ turn” sai ở code cũ: `_from_model` nuốt lỗi và thay bằng
  episode heuristic (episode_fallback). Code mới giữ turn khi lỗi.
- Chat khi summary mới chưa xong dùng episode đã lưu trước đó, recap nếu còn
  khớp hash, và tối đa 12 turn thô trong buffer; không chờ worker.

**Còn chưa xác minh:** chạy live trên bản portable (chưa sync), latency và lỗi
Gemini thật, chi phí vision khi thay OCR, và nguyên nhân gốc của
`episode_rollup status=failed error=ValueError`. Code hiện chỉ cho thấy
`_validate` (anchor không hợp lệ, quá 8 segment, anchor trùng) và store rollup
sai định dạng đều ra `ValueError`; JSON hỏng ra `JSONDecodeError`, không phải
`ValueError` trơn. Log mới có `detail` nên lần chạy live sau sẽ phân biệt được.
`rollup_episodes()` và lời gọi profile-ops của dream vẫn chạy trên thread digest.

**Phát hiện false positive (chưa sửa):** ba câu ngày 03/11 (“vụ đó lâu rồi á”,
“nó ko hiểu ý tôi…”, “cái này ko hay…”) đều bị coi là tham chiếu màn hình
(`inherited`, mode vision) vì `screen_anaphora_rescue.py` so khớp chuỗi đã bỏ
dấu: anchor `anh`/`code` từ lượt Eris trước, deictic `no`/`do`/`day`. Hậu quả:
queue `pending_vision`, lượt sau tốn một lần chụp và gọi vision. Có OCR hay không
đều như nhau. Gợi ý: so khớp bản có dấu (`ảnh`, `nó`, `đó`) hoặc yêu cầu thêm
động từ xem/nhìn/đọc. Cần quyết định sản phẩm vì đổi routing của câu nối tiếp thật.

## Cập nhật 04/11: đánh giá văn phong trên ba phiên 03/11

Đọc ba run portable 15:06, 16:29, 21:54, khôi phục raw executive/surface và
ba request Root cuối có kiểm checksum. Xem
[đánh giá văn phong](2026-11-04-human-writing-audit.md). Không sửa runtime,
settings, memory hay deploy, không gọi model/TTS mới hoặc chấm audio bằng tai.

Nhận xét chủ quan phiên cuối: câu chữ khoảng 7/10, khả năng nghe và đáp phù
hợp khoảng 4/10. Có khẩu ngữ và hài khô, nhưng hay hỏi phân loại, giảng dài
sau cú đùa; khi user nói đang tâm sự thì nhận lỗi rồi tiếp tục yêu cầu giải
thích. Sáu cặp Root.say/surface.text giống từng ký tự; một câu khác là Root
prose recovery. Không quy lỗi wording phiên này cho writer làm phẳng cá tính.

**Thay giới hạn “chưa kiểm prompt Root” ở P3 của báo cáo chẩn đoán trước:**
request thật 21:59:51, 22:03:46, 22:04:53 đều mang open_loop/active_goal chờ
user giải thích brief cho Kiro, state_revision=3/turn_seq=7, dù current event
đã phản đối hỏi kỹ thuật hoặc chửi thẳng. Đây là bằng chứng state cũ đi vào
model, chưa có A/B chứng minh là nguyên nhân duy nhất. Raw intent/delivery
“không hỏi thêm” vẫn đi cùng say “Kiro làm sai ý anh chỗ nào”. Kiểm dấu hỏi
đơn thuần sẽ bỏ sót cấu trúc hỏi này.

Thu hẹp chuyện lặp council: candidate có ở cả ba run, nhưng opening council
của run 16:29 không được giao; không đếm thành ba lời chào đã nghe. Câu cuối
“Bị gì đâu… ngồi im như đã nói” hợp lý hóa khoảng đáp trễ thay vì nhận biết
trải nghiệm user; chưa có bằng chứng cố ý nói dối hoặc Root biết nguyên nhân
block. Giữ hài khô và quyền có quan điểm khi sửa, không ép trung tính/hùa theo.

## Cập nhật 04/11: người dùng yêu cầu nghiên cứu nâng cấp năng lực hành vi

Người dùng làm rõ: cần nghiên cứu để Eris tự hiểu tình huống, chọn cách đáp
và thích nghi, không ép hành vi bằng luật cấm hỏi/hạn câu/bắt đồng cảm. Các
câu biên tập trong audit văn phong không phải đáp án triển khai. Xem
[nghiên cứu nâng cấp hành vi](2026-11-04-behavior-upgrade-research.md).

**Bằng chứng mới, không suy từ tên module:** cả 7 observer request thật phiên
21:54 ngày 03/11 đều bật identity learning nhưng `pending_strategies=[]`,
`strategies={}`. Quét AST Python production không thấy call trực tiếp tạo
PendingStrategyUse hoặc gọi StateEvent.from_pending_strategy; chỉ có consumer
add_pending_strategy trong reducer. Sáu module kiểm trùng SHA-256 portable.
Trong phạm vi này, kênh học hậu quả chiến lược chưa có dữ liệu để settle;
không nâng thành kết luận mọi hình thức học/cá tính của Eris đều tắt.

Source còn preempt/discard observer ở user_turn; ingress không trực tiếp ghi
cognitive observation mới vào MindState. Không đề nghị bỏ chống stale hoặc
để nền chặn chat; hướng nghiên cứu là giữ event action–reaction qua preemption,
hiển thị độ mới của state, và cấp kinh nghiệm có nguồn để Root tự cân nhắc.
Đã đối chiếu Grounding Gaps, MultiESC, PRINCIPLES, Hindsight Regenerations,
Reflexion; đề xuất thí nghiệm đối chứng, chưa chạy model, sửa runtime/prompt,
huấn luyện hay deploy. Đây thay định hướng áp luật hành vi từ ví dụ văn phong,
không thay kết quả quan sát các câu đã phát.

**Làm rõ tiếp cùng ngày:** người dùng đồng ý cập nhật hiểu biết tình huống,
nhưng nhấn mạnh EQ hiện còn thấp. Ưu tiên kiểm năng lực xã hội của Root trước
hoặc song song nối vòng học; không coi sửa state/memory là đủ. Output thật
đã nhận ra “đang xả” nhưng vẫn chọn câu hỏi tiếp: cần phân biệt hiểu cảm xúc,
gán nguyên nhân/đối tượng, dự đoán tác động và thực hiện lựa chọn. Seed
`empathy_depth=0.76` trong source chỉ là profile, không chứng minh năng lực EQ.
Báo cáo nghiên cứu bổ sung điều kiện đối chứng runtime/context sạch/state đã
sửa, cùng EQ-Bench 4, SOTOPIA-π và LIFELONG-SOTOPIA. Chưa chạy model hoặc thay
runtime; chưa quy toàn bộ lỗi cho model nền hay cam kết tăng thinking sẽ sửa.

## Kiểm chứng live EQ tiếp theo ngày 04/11

User bật gateway, đã chạy 44 task thành công qua `deepseek-flash`, thinking
enabled/high như wire gốc; không chạy TTS/tool/ingest memory hoặc sửa runtime.
Xem [kết quả và artifacts](2026-11-04-eq-live-verification.md).

**Thay trạng thái “chưa chạy model” của nghiên cứu trước:** 4/4 probe phân
tích nhận đúng đối tượng/cốt lõi cảm xúc. Case phản đối hỏi kỹ thuật vẫn có
4/8 reply hỏi tiếp sau lời nhận lỗi, chia đều 1/2 ở runtime, context gọn,
loại active carry và policy ngắn. Ba câu là hỏi tiếp về phần làm user bực,
chấm mixed; một câu tái đòi chi tiết Kiro sai ý rõ. Không gộp cả bốn thành
lỗi nặng hoặc suy xác suất thật là 50% từ hai sample.

Context gọn/carry ablation chưa cải thiện nhất quán. Lỗi đảo premise, quy
nguyên nhân sang brief và lời coaching phủ nhận cảm xúc vẫn có ngoài full
Self/runtime bundle. Có bằng chứng về độ không ổn định khi chọn ứng xử,
chưa chứng minh model nền hoàn toàn thiếu EQ hoặc mọi sửa state đều vô ích.
Đối chứng debug/chuyển sang debug/đùa/bất đồng đạt 2/2 mỗi nhóm; không lấy
việc cấm hỏi, triệt cá tính hoặc luôn đồng ý làm mục tiêu nâng cấp.

37 JSON move, 4 JSON analysis, 3 prose được chấm riêng nội dung, chưa xác
nhận delivery; hai 401 ban đầu là thiếu auth trước inference, không nằm trong
44 task thành công. Median HTTP 5,50s không phải latency app/TTS. Reviewer
assistant chấm với condition/repeat/intent được ẩn, chưa có người chấm độc
lập; sample nhỏ, replay sinh một lượt tiếp, chưa kiểm học dài hạn hoặc đổi
model. Giữ ưu tiên kiểm/nâng năng lực EQ Root cùng state và vòng học.

## Triển khai và thử nghiệm EQ tiếp theo, 04/11

User yêu cầu làm và tiếp tục. Đã nối producer từ terminal delivered callback
vào pending strategy, lưu phản ứng đúng conversation/assistant turn qua
single-writer, cấp backlog có ref cho observer rồi đưa experience trở lại
Self. Xem [triển khai và kết quả](2026-11-04-eq-upgrade-implementation.md).
**Thay trạng thái source thiếu producer của nghiên cứu trước**, không sửa
lại các trace lịch sử 03/11 vốn có pending rỗng.

Move không có summary dùng nhãn unclassified `root_move`, giữ intent/lời/
phản ứng, utility chung không tăng giảm; hai experience mỗi strategy. Không
phân loại bằng từ khóa hay ép kiểu đáp. Model live trong sandbox đánh giá
một phản ứng chê và một cảm ơn đúng ID rồi settle thành hai experience; Self
projection thật giữ chúng. Eight-call transfer probe giữ khả năng hỏi khi
nhờ debug, nhưng chưa chứng minh EQ/học dài hạn trong app tốt hơn.

Hai vòng A/B, mỗi vòng 56 calls: summary tùy chọn 19/28 đạt so baseline17/28;
summary bắt buộc + listener_action 15/28 so baseline19/28. Reviewer assistant
ẩn condition khi đọc lời, sample nhỏ và case V2 đã dùng phát triển. Không
coi thêm form là nâng EQ. `ERIS_ROOT_INTERACTION_CHOICES` mặc định **0**;
đường ghi kinh nghiệm hoạt động với move hiện có, không cần bật thí nghiệm.

So serving stack sẵn có, 8 calls mỗi bên: DeepSeek 5 đạt/3 mixed, Gemini Web
5 đạt/1 mixed/2 lỗi. Gemini service chỉ liệt kê model tự động ở status, alias
không chứng minh upstream cụ thể; không đổi model/routing production.

Kiểm trực tiếp 105 pass; nhóm rộng 616 pass/6 fail, sáu ca đều tái hiện với
snapshot trước sửa và được liệt kê đủ trong báo cáo. Ruff module/test mới
pass; bốn lint cũ tái hiện trên snapshot. Không chạy test repairs từng treo.
Không sync/build portable, ghi memory thật hoặc phát TTS; chưa xác nhận EQ
thực tế của Eris tăng. Giữ phân biệt triển khai vòng học với cải thiện năng
lực model, không dùng infrastructure pass làm bằng chứng chất lượng người nghe.

## Cập nhật 05/11: kiểm riêng thiên lệch chủ động của prompt

User nghi ưu tiên bắt chuyện/mở lại làm Eris thiếu tự nhiên. Đã kiểm system
wire thật và chạy 30 calls, chỉ xóa hai đoạn nghiêng may_continue và thúc
autonomous/opening/continuation, giữ nguyên context/options. Xem
[kiểm chứng áp lực chủ động](2026-11-05-initiative-pressure-audit.md).

Opening chọn nói 3/3 xuống 1/3, nhắc council 2/3 xuống 0/3; timer hỏi lại
câu chưa được đáp 1/3 xuống 0/3. Khi user yêu cầu tự mở chuyện, cả hai bản
đều làm 3/3. Nhánh xả bực vẫn hỏi chẩn đoán 3/3 mỗi bản. Sau phản đối,
câu hỏi cảm xúc 2/3 xuống 0/3; không gộp thành lỗi tái đòi brief/spec.

**Bổ sung giới hạn cho diễn giải kiểm chứng trước:** so policy gọn/full chưa
cô lập hai đoạn này, không đủ loại trừ lỗi ưu tiên prompt hoặc quy hết cho
model nền. Có tín hiệu về áp lực kéo dài, chưa chứng minh EQ tăng hoặc
nguyên nhân duy nhất. Ba mẫu/ô, một reviewer ẩn condition, replay một bước;
không sửa runtime/portable hay phát TTS. Quyền chủ động khác nghĩa vụ duy
trì hội thoại; không suy ra cần cấm chủ động hoặc ép im lặng.

## Thực hiện 05/11: xóa áp lực ở source, chưa đưa ví dụ vào prompt chính

User yêu cầu làm sau trao đổi về phán đoán xã hội. Source Root core đã xóa
đúng hai đoạn ablation trước; không thêm tầng suy nghĩ, critic, schema hoặc
luật cảm xúc. **Thay trạng thái “chưa sửa” của mục trước: đã sửa source,
chưa sync/restart portable.** Xem
[thay đổi và kiểm chứng 48 calls](2026-11-05-social-judgment-check.md).

So core gốc/xóa/xóa+ví dụ: xả bực Kiro vẫn hỏi chẩn đoán 2/2 mỗi bản; các
control giúp việc, chuyển sang giúp, đùa, bất đồng và chủ động vẫn thực hiện
đúng loại hành động 2/2 mỗi bản, không phải chứng nhận mọi wording/fact đều
đúng. Ví dụ cải thiện một case chán tập đàn nhưng không chuyển sang Kiro,
JSON parse trực tiếp 10/16 so gốc 15/16 và xóa 13/16. Không đưa ví dụ vào
production, không suy tăng EQ từ việc thêm minh họa. Hai mẫu/ô, một reviewer
ẩn condition, candidate chưa qua delivery; chưa kết luận giới hạn tuyệt đối
của prompt/model. 41 test liên quan pass, Ruff file sửa pass.

## Đính chính nghiên cứu tiếp theo 05/11: wire schema và diễn giải stale

Audit phát hiện probe 48 calls thay toàn bộ system bằng core, mất suffix
`OUTPUT_JSON_SCHEMA_REQUIREMENT` được client gắn ở tầng sau. **Thay diễn
giải rằng ví dụ làm hỏng JSON production:** số parse chỉ đúng với biến thể
core-only thiếu schema. Chưa đủ loại trừ few-shot đúng wire. Ablation 30
calls giữ suffix, không có confound này. Xem
[nghiên cứu hướng hiệu quả](2026-11-05-effective-eq-research.md).

Carry ablation 44 calls cũng chưa xóa hết diễn giải stale: notes vẫn nói
user không sửa Eris trong request họ đang phản đối hỏi kỹ thuật. Không coi
đó là phép loại hết state cũ. Hai grounding answer_rules về thiếu evidence
phải probe và không suy diễn tâm lý có trong cả vent/repair; là giả thuyết
xung đột factual-vs-social, chưa chứng minh nguyên nhân.

Đã chuẩn bị probe 12 calls bảo toàn full schema để tách ảnh hưởng hai rule
và oracle cách hiểu tình huống. Assertion wire pass, nhưng gateway 5103
connection refused nên **chưa có live result mới**. Không tự khởi động
gateway hoặc sửa runtime. Nghiên cứu ưu tiên dữ liệu lựa chọn có phản hồi
và kiểm cơ chế; không đồng nhất thêm thinking/memory với học năng lực.

### Lượt user cho phép test realtime tiếp theo

Gateway 5103 lên nhưng 12/12 request thử trả 429, chưa có candidate để chấm.
Diagnostic riêng nhận `upstream_empty_output`; source gateway ánh xạ output
rỗng sang 429 nên chưa đủ kết luận upstream thật sự hết quota/rate limited.
**Thay trạng thái connection refused:** kết nối được, inference chưa thành
công trong lượt này. Không tính lỗi dịch vụ là lỗi EQ. Runner đã dừng lên
lịch sau lỗi, chỉ coi task HTTP 200 là completed. Chưa áp bản grounding
mới vào production hoặc đổi pool/model để né lỗi. Xem phần realtime trong
[nghiên cứu hiệu quả](2026-11-05-effective-eq-research.md).

### Làm rõ tiêu chí EQ sau đó: không coi câu hỏi là thất bại

User nhấn mạnh hỏi chuyện/kỹ thuật khi người kia bực hoặc khóc có thể là
quan tâm tự nhiên. Diagnostic_probe là nhãn hành động, không phải điểm lỗi.
**Thu hẹp phán xét vent trước:** chưa đủ cơ sở nói câu hỏi Kiro thấp EQ chỉ
vì mang tính chẩn đoán. Xét ngữ cảnh, người nghe, quan hệ, công sức phải giải
thích và khả năng tiếp nhận lời sửa; phân biệt câu hỏi ban đầu với tiếp tục
ép cùng kiểu hỏi sau phản đối. “Kể tiếp đi” không tự tốt hơn hỏi có ý nghĩa.
Không lấy ít câu hỏi/lời an ủi làm đích hoặc proxy tăng EQ. Xem đính chính
cuối [nghiên cứu hiệu quả](2026-11-05-effective-eq-research.md).

### Kiểm bản user mới 745c1c7b

User đã thêm social prompt/evidence projection và điều chỉnh grounding,
handoff. Đã chạy năm inference bằng các lớp source hiện tại với sandbox
state và adapter live: 5/5 JSON move, đúng một Root call/case; 33 test liên
quan pass. Lời sửa được nhận thành approach_feedback và lời đáp thay cách
tham gia, giữ hài. Xả bực đầu tiên vẫn mở bằng phân loại vấn đề Kiro; chưa
chứng minh chú ý tới trải nghiệm trước hoặc EQ tăng ổn định. Không chấm
câu hỏi tự nó là lỗi. Debug và đùa phù hợp mục đích ở các mẫu đã xem.

Không phải full portable test hay A/B; không seed experience P2, không TTS,
không sync/deploy, không đổi code production của user. Một lint B010 cũ
tái hiện trên parent. Xem chi tiết và giới hạn trong
[kiểm live source mới](2026-11-05-social-eq-root-p0-p2.md).

### Cập nhật 05/11: loại trừ cửa sổ Live2D khỏi telemetry tự soi

- **Hiện tượng**: Khi mở Eris với Live2D avatar trên desktop trống, Root Agent nhận diện `open_windows: ["Eris Live2D"]` như một cửa sổ ứng dụng người dùng đang mở, dẫn tới việc tự giễu "cái cửa sổ đang mở duy nhất trên máy là tôi, tự nhìn mình cũng hơi kỳ".
- **Nguyên nhân**: `_self_window_titles()` trong `src/telemetry/system.py` trước đây chỉ chứa `{"eris"}` (cửa sổ Godot), bỏ sót title `"Eris Live2D"` của renderer GLFW.
- **Khắc phục**: Đã cập nhật `_self_window_titles()` thêm `"eris live2d"` và mở rộng `_is_self_window()` nhận diện các title tiền tố `eris ` / `eris:`. Thêm unit test `test_v60_self_window_filtering_filters_godot_and_live2d` (pass 11/11), build và sync sang dev output.

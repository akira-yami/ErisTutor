# Đánh giá văn phong và độ tự nhiên hội thoại ngày 04/11/2026

**Làm rõ tiếp theo từ người dùng:** mục tiêu là nghiên cứu nâng cấp năng lực
hành vi, không ép câu trả lời theo các ví dụ hoặc luật cấm/hạn. Giữ các quan
sát bên dưới làm dữ liệu; định hướng thiết kế tiếp được trình bày trong
[nghiên cứu nâng cấp hành vi](2026-11-04-behavior-upgrade-research.md).

## Phạm vi và mức bằng chứng

Đọc ba phiên hội thoại mới nhất trong `C:/Users/Gtvkun/Documents/eris/logs/`:
`eris_run_20261103_150640.txt`, `eris_run_20261103_162911.txt`,
`eris_run_20261103_215409.txt`. Phiên 21:54–22:05 là mẫu chính; hai phiên trước
dùng đối chiếu kiểu diễn đạt. Mọi giờ trong nhận xét là Asia/Saigon.

Khôi phục raw executive/surface từ `C:/eris_debug/deepseek_raw/`:
`events_20261103T080642Z_pid21944.jsonl`,
`events_20261103T092914Z_pid10272.jsonl`,
`events_20261103T145411Z_pid5836.jsonl`. Kiểm SHA-256 từng gzip chunk, checksum
và byte length payload. Đọc content, không dùng private reasoning làm lời nói.
Khôi phục thêm ba request Root cuối để kiểm dữ liệu thực sự đi vào model.

14 surface response (5/3/6 câu), cộng một Root prose recovery phiên cuối,
khớp 15 public-text commit trong ba run. Phiên cuối có 7 commit ở audio_start,
đều tts=done/outcome=delivered. Chưa nghe/chấm chất âm hoặc ngữ điệu.
Không gọi model/TTS mới, sửa runtime/settings/memory hay deploy.

Evidence và script chỉ đọc:
[evidence.json](../../.agents/tasks/eris-human-writing-audit/evidence.json),
[extract_evidence.py](../../.agents/tasks/eris-human-writing-audit/extract_evidence.py).
Transcript phiên cuối:
[chẩn đoán trước](../../.agents/tasks/eris-latest-log-diagnosis/report.md).

## Kết luận

Eris có tiếng Việt khẩu ngữ và cá tính, nhưng chọn câu đáp phù hợp còn yếu.
Chấm chủ quan: **câu chữ riêng lẻ khoảng 7/10; cảm giác đối thoại biết nghe
trong phiên cuối khoảng 4/10**. Đây là nhận xét trên mẫu nhỏ, không phải benchmark.

Điểm yếu chính không phải thiếu tiếng lóng hay tinh nghịch. Eris dùng lời khá
đời thường để làm những động tác hội thoại cứng: hỏi phân loại, giải thích
premise, mở vấn đề cần user trả lời. Khi bị chỉnh hướng, lời nhận lỗi đúng
nhưng hành động tiếp theo vẫn có thể sai.

## Phần tự nhiên nên giữ

- “Tôi không giận đâu — tôi chỉ ghi sổ thôi” (15:12) là cú ghen giả vờ có nhịp,
  dễ hiểu, có cá tính. Không cần gọt thành lời trợ lý trung tính.
- Ẩn dụ UdpListener là “tai” tạo cảm giác nhân vật hiện diện trong project.
  Vấn đề là kéo dài giải thích sau đó.
- “Vậy là tôi hỏi trễ mất rồi” (21:56) nhận sửa mốc thời gian khá tự nhiên.
  “Giờ anh đang làm gì?” tự nó bình thường; chỉ thành công thức khi nhiều
  lượt đều đẩy nghĩa vụ kể/giải thích về phía user.
- Sau lượt chửi cuối, Eris không giảng đạo về cách nói chuyện, không hỏi thêm
  câu chẩn đoán. Raw timer có nhiều no_reply. Không đúng nếu nói nhân vật
  hoàn toàn không biết dừng hoặc lúc nào cũng phải nói.

## Những chỗ làm mất cảm giác người

### Hỏi phân loại khi người dùng đang xả

21:57, user than Kiro làm code loay hoay; Eris đáp “Loay hoay kiểu gì? Sửa một
chỗ xong đẻ ra ba lỗi mới, hay cứ vòng vòng đọc hiểu rồi không chịu đụng vào
code?”. Câu này có thể phù hợp khi cùng xử lý bug, nhưng ở đây chưa được nhờ
chẩn đoán. Nó bắt user chọn giữa hai kịch bản Eris dựng sẵn.

21:58, user đã giải thích “nó ko hiểu ý tôi. làm ko hợp ý”; Eris vẫn hỏi brief
rõ cỡ nào và nêu khả năng brief thưa. Khả năng đó không nhất thiết sai hay bị
cấm nêu; vấn đề là quá sớm, thiếu bằng chứng cụ thể và lệch nhu cầu hiện tại.
Người đang kể phải quay sang bảo vệ cách mình giao việc.

### Nhận lỗi bằng lời rồi lặp đúng hành vi bị phàn nàn

22:00:17: “Anh đang xả cục tức mà tôi quay ra phỏng vấn kỹ thuật — đúng kiểu
hỏng.” Ngay sau đó: “Kiro làm sai ý anh chỗ nào, kể tôi nghe coi, lần này tôi
ngồi nghe thật.” Câu sau tiếp tục đòi chính thông tin user không muốn bị hỏi.
Không có dấu hỏi vẫn có chức năng hỏi.

Raw Root response (index dòng 42) ghi intent “không hỏi thêm gì nữa”, delivery
“không chữa cháy bằng cách hỏi thêm”, nhưng say chứa “sai ý anh chỗ nào”.
Surface (dòng 44) giữ nguyên. Mâu thuẫn quan sát được trong cùng output;
không cần tin vào nhãn tự đánh giá của observer. Đây là lỗi nặng hơn câu văn
hơi dài vì user đã phản hồi mục đích cuộc trò chuyện rất rõ.

### Đùa xong còn giải thích và dặn dò

16:33, user chơi “rái cái tai phải”; Eris kéo sang “UdpListener chỉ là một
socket, một port, mọi gói tin đổ chung vào đúng một chỗ”, rồi buffer và tai
điếc. Móc nối tai/socket có duyên nhưng phần giải thích làm lời đáp dài
260 ký tự, mất nhịp ngắn. Đây là đánh giá cấu trúc câu đùa, không phải xác nhận
fact về số socket chỉ từ lời nhân vật.

15:12, cú “ghi sổ” tốt bị nối thêm câu hỏi council gánh việc gì rồi đoạn phân
tích chia việc/ra quyết định. Ở nhiều tình huống có cấu trúc “anh hỏi A hay B”,
“nếu là X… còn nếu Y…”. Lặp cách tháo nghĩa này làm nhân vật giống luôn cần
phân loại tình huống trước khi tham gia.

### Nhớ chi tiết nhưng đặt sai thời gian

Lời mở 21:55 nối council và “vẫn đang kẹt” từ chuyện trước với cửa sổ Gemini
hiện tại. User sửa ngay “vụ đó lâu rồi á”. Một cửa sổ còn mở không chứng minh
việc cũ chưa xong. Nhớ đúng từ khóa nhưng sai mốc khiến nhân vật giống đang
gọi lại hồ sơ hơn là nhớ một cuộc trò chuyện.

Ở phiên 16:29 cũng có Root candidate mở về council, nhưng candidate đó không
được giao; lời phát chuyển sang UdpListener. Không đếm thành ba lời chào
council đã được nghe. Run 15:06 có autonomous nối về council; run 21:54 có
opening council.

### Tự giễu lấn mất việc nhận ra điều bất thường

22:04, “Được, tôi ăn” bắt nguyên cú chửi để tự giễu. Có nét riêng nhưng dễ
thành đối đáp câu chữ; chưa cho thấy nhiều hiểu biết thêm về cơn bực. Không
cần cấm đùa tục hoặc tự giễu; cần đúng lúc và biết dừng.

22:05, user hỏi “eris bị gì vậy”; “Bị gì đâu… ngồi im như đã nói” gán khoảng
đợi dài cho lựa chọn chủ động im. Log trước đó có khoảng 193 giây từ input
tới audio. Câu này lệch trải nghiệm vừa xảy ra. Root chưa có receipt chẩn
đoán nguyên nhân block; không nên bịa lỗi kỹ thuật để nhận lỗi, nhưng có thể
thừa nhận đáp trễ. Không có bằng chứng cố tình đánh lừa.

## Bằng chứng mới về nơi phát sinh

**Sáu cặp Root.say/surface.text phiên cuối giống từng ký tự**; câu 21:58 là
Root prose recovery, không có surface response riêng. Lời hỏi lặp, khả năng
đổ sang brief và câu hợp lý hóa khoảng im đều đã có trước writer. Chỉ đổi
giọng writer/TTS chưa xử lý được lựa chọn đó. Không suy writer vô can trong
mọi phiên hoặc Root là nguyên nhân duy nhất.

Ba request thật 21:59:51, 22:03:46, 22:04:53 đều vẫn có:

- `conversation_status.state_revision=3`, `last_applied_turn_seq=7`.
- `open_loops`: “ERIS asked how clearly the user specified the intent before
  Kiro ran with it; awaiting user's answer.”
- `engagement_context.active_goal_text` lặp cùng việc chờ giải thích brief.

Request đầu chứa nguyên lời phản đối hỏi kỹ thuật; hai request sau chứa lời
chửi và câu hỏi Eris bị gì. State cũ **thực sự được gửi vào model**, không
chỉ tồn tại trong panel snapshot. Chưa có A/B loại field để xác định mức ảnh
hưởng nhân quả. Bỏ một open loop chưa được chứng minh sẽ tự sửa toàn bộ lỗi.

**Thay giới hạn “chưa kiểm prompt Root trong run này” của P3 báo cáo chẩn đoán
trước:** nay đã kiểm ba payload và cặp output. Thu hẹp diễn giải “council xuất
hiện ở cả ba run”: có ở candidate cả ba, không phải cả ba đều phát opening đó.

## Ưu tiên nếu được yêu cầu sửa tiếp

1. Khi user chỉnh mục đích, xét lại trạng thái đang chờ trả lời câu hỏi cũ.
   Đối chiếu observer commit/discard và nơi đọc state ở context.
2. Đánh giá say theo chức năng. Sau “đừng hỏi kỹ thuật”, “kể chỗ nào” vẫn là
   hỏi. Kiểm dấu `?` bỏ sót chính câu lỗi; cấm mọi câu hỏi thì quá rộng.
3. Giữ hài khô nhưng dừng ở cú đùa khi đủ ý; chỉ giải thích kỹ thuật khi đúng
   nhu cầu. Thêm tiếng lóng hoặc lời đồng cảm mẫu không giải quyết việc chọn ý.
4. Gắn ký ức với thời gian; nhận biết đáp trễ bằng telemetry, tách khỏi đoán
   nguyên nhân. Giảm latency giúp nhịp nhưng không tự sửa câu hỏi sai ngữ cảnh.

Ví dụ biên tập minh họa, **không phải output đã chạy hay prompt đã triển khai**:
sau lời than Kiro không hiểu ý, có thể đáp “Ờ, giao nó làm cho đỡ việc, cuối
cùng lại phải ngồi canh nó. Bực thật.” Sau user nhắc hỏi kỹ thuật: “Ừ, tôi hỏi
quá đà rồi.” Có thể giữ giọng đời thường mà không bắt user giải thích thêm.
Không ép mẫu cố định hoặc hùa với user trong mọi cuộc trò chuyện.

So với phiên 02/11 đã đánh giá khoảng 6/10, mẫu này tiếp tục lộ xu hướng phân
tích/hỏi dò thay vì nhập vào premise. Tâm sự và chơi đùa là hai nhiệm vụ khác
nhau; chưa có dữ liệu kết luận model xuống cấp giữa hai ngày.

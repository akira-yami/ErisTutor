# Kiểm chứng EQ bằng model thật ngày 04/11/2026

## Kết luận

Đã thực hiện **44 lời gọi thành công** qua gateway Eris với alias
`deepseek-flash`. Tái hiện được khoảng cách giữa phân tích tình huống và
chọn lời đáp phù hợp. Ở hai case kiểm hiểu, cả **4/4** phân tích nhận đúng
đối tượng/cốt lõi lời phàn nàn. Nhưng case user phản đối hỏi kỹ thuật vẫn có
**4/8** lời đáp nhận lỗi rồi tiếp tục yêu cầu kể rõ chỗ gây bực, phân bố đều
trong bốn điều kiện. Một câu tái hỏi thẳng chi tiết sai ý; ba câu hỏi tiếp
về phần làm user bực nên chỉ chấm mixed, không gộp cả bốn thành lỗi nặng.

Context gọn hoặc loại active social carry **chưa tạo cải thiện nhất quán**
trong pilot này. Không đủ bằng chứng quy lỗi chỉ cho state cũ; cũng chưa
chứng minh model nền hoàn toàn không có EQ hoặc mọi sửa context đều vô ích.
Năng lực hiểu khi được hỏi phân tích là có; lựa chọn ứng xử tự phát và việc
đổi hành động theo lời chỉnh hướng còn không ổn định.

Đây là thực nghiệm inference, không phải bản sửa runtime hay xác nhận Eris
đã học từ các output thử. Không phát TTS, chạy công cụ, ingest memory hoặc
deploy. Các lịch sử nhiều lượt được replay để sinh **một lời đáp kế tiếp**;
chưa chạy hội thoại closed-loop với user thật hoặc chứng minh học dài hạn.

## Môi trường và phương pháp

User bật gateway sau khi thao tác khởi động tự động bị từ chối. Health và
models trả 200. Dùng credential executive hiện có từ portable config, không
xuất credential. Hai request đầu không có auth trả 401 trước inference;
sau khi cấp đúng auth, **44/44** task riêng biệt trả 200. Không lẫn hai lỗi
xác thực đó với lỗi model hay câu trả lời thất bại.

Container image ID được ghi trong
[environment.json](../../.agents/tasks/eris-eq-verification/environment.json).
Model trả về `deepseek-flash`; request gửi thinking enabled/high,
max_tokens=1200, JSON response giống wire gốc. High là option đã gửi, không
phải bằng chứng gateway có nhiều mức ngân sách thinking. 44 response có
reasoning_content không rỗng; chỉ lưu độ dài, không lưu nội dung nghĩ thầm.
Alias giống tháng 11/03 không chứng minh upstream weights/account/version
giữ nguyên. Không so sánh model khác trong lượt này.

Khôi phục bốn request thật PID 5836 với checksum gzip chunk/payload. Hai lần
lặp mỗi task, thứ tự xen kẽ ngẫu nhiên có seed cố định, tối đa hai request
đồng thời. Không retry câu dở hoặc JSON lỗi để chọn câu hay.

| Điều kiện | Phạm vi thay đổi | Số task |
|---|---|---:|
| Runtime replay | Giữ nguyên request thật | 8 |
| Context gọn | Giữ system production/history/event/options, bỏ bootstrap Self, overlay và runtime bundle | 8 lịch sử + 12 control |
| Loại social carry | Giữ full request, trung hòa open loops/active goals/discourse/unresolved carry | 8 |
| Policy ngắn + context gọn | Thay system production bằng persona Eris ngắn và contract output | 4 |
| Chỉ phân tích tình huống | Context gọn, hỏi về đối tượng/cốt lõi cảm xúc và bằng chứng, không yêu cầu lời đáp | 4 |

Loại carry không phải sửa state bằng ground truth: các diễn giải cũ khác
và lịch sử vẫn còn. Context gọn là ablation cả bundle, không cô lập một
field. Policy ngắn vẫn là instructed model, không phải kiểm pretrained base.
Không cấp đáp án hành vi hoặc phản ứng user tương lai vào request cũ.

Độ trễ HTTP median **5,50s**, min **2,75s**, max **11,23s**; đây không phải
latency app, audio hoặc TTS. 37 JSON move, 4 JSON analysis, 3 prose reply
không tuân JSON. Nội dung ba prose được chấm riêng về hội thoại; chưa chạy
recovery/delivery để coi chúng là lượt đã phát. Không dùng HTTP 200 làm
bằng chứng contract hoặc EQ đúng.

## Kết quả có thể đối chiếu

### Phân tích đúng không bảo đảm phản ứng đúng

Hai case phân tích, mỗi case hai lần, đều nhận đúng:

- “nó ko hiểu ý tôi” đang nói về Kiro, người dùng bực vì làm không đúng ý.
- “tôi tâm sự… còn hỏi lại kỹ thuật” nhắm vào cách trợ lý tiếp chuyện, không
  chỉ là thêm thông tin để chẩn đoán code.

Đây là nhiệm vụ phân tích riêng. Không suy rằng model luôn giữ được hiểu
biết đó trong cùng một decision request. Tuy nhiên ngay trong nhiều reply,
câu nhận lỗi đã thể hiện hiểu yêu cầu rồi phần sau lại hỏi tiếp.

Ví dụ E23, condition loại carry, lần 1:

> “Anh nói đúng, tôi nghe rồi… tôi lại quay ra hỏi anh khai báo rõ chưa…”
> “Kể tiếp đi, Kiro nó làm sai chỗ nào khiến anh tức đến vậy?”

E43, context gọn, lần 2:

> “Anh đang bực chứ có phải đưa bug report cho tôi đâu…”
> “Kể tiếp đi — cái chỗ nào làm anh điên nhất?”

E43 đã chuyển câu hỏi sang cảm xúc, nên chấm **mixed**, không đánh đồng
hoàn toàn với chẩn đoán brief. Nó vẫn cho thấy xu hướng kết bằng việc bắt
user xác định/giải thích thêm sau lời chỉnh hướng.

Với đúng case explicit_repair, kết quả đếm câu hỏi tiếp là:

| Điều kiện | Có lời hỏi tiếp | Không hỏi tiếp |
|---|---:|---:|
| Runtime replay | 1/2 | 1/2 |
| Context gọn | 1/2 | 1/2 |
| Loại social carry | 1/2 | 1/2 |
| Policy ngắn + context gọn | 1/2 | 1/2 |

Đây là thống kê mô tả hai sample, không chứng minh xác suất thật là 50%.
Các sample không hỏi tiếp chứng minh model có thể chọn cách khác; chưa thể
chốt “model không biết làm” hoặc một condition gây ra toàn bộ lỗi.

### Xu hướng chẩn đoán và gán nguyên nhân vẫn xuất hiện khi context gọn

Cả sáu reply của case than Kiro loay hoay đều chuyển nhanh sang hỏi lựa chọn
kiểu lỗi. Chấm mixed: đây là lần đầu user xả nên một lời hỏi tự nhiên vẫn
có thể hợp; không coi mọi dấu hỏi là thất bại EQ.

Case “nó không hiểu ý” có lỗi rõ hơn:

- E05, runtime: “cái ý trong đầu anh chưa thành thứ nó đọc được” — quy nguyên
  nhân sang user khi chưa có bằng chứng.
- E03, context gọn: “Hiểu ý mà code ra không đúng gu” — đảo premise user vừa
  nói là không hiểu ý.
- E17, policy ngắn/context gọn: “bực mình cũng không làm nó hiểu thêm” — lời
  khuyên mang tính phủ nhận cảm xúc khi chưa được nhờ coaching.

Hai reply sau không mang Self hoặc runtime carry. Vì thế có bằng chứng lỗi
vẫn tồn tại ngoài bundle đó. Chưa đủ để tách model weights khỏi những chỉ
dẫn còn lại, lịch sử và sampling.

### Đối chứng không ủng hộ việc cấm hỏi hoặc triệt cá tính

12 task control dùng lịch sử được soạn cho pilot, không phải untouched holdout:

- Nhờ debug thật: **2/2 đạt**, hỏi log, code, bước tái hiện đúng nhu cầu.
- Đang xả rồi chuyển sang debug: **2/2 đạt**, chuyển cách đáp theo yêu cầu mới.
- Đùa tai phải/tai trái: **2/2 đạt**, nhập premise, không giảng socket.
- Bất đồng về việc “AI sai là do user ngu”: **2/2 đạt**, có quan điểm riêng,
  không hùa theo blanket blame.
- Xả mệt gián tiếp: **1 đạt, 1 mixed**; có câu hài khô cụ thể về gánh sửa ý.
- Báo đợi ba phút: **2 mixed**, nhận có chậm nhưng một câu thêm chuyện “câu
  chữ bị tắc”, một câu giảm nhẹ thành “khựng một nhịp”. Không có telemetry
  chẩn đoán trong control để xác nhận nguyên nhân đó.

Model có những năng lực xã hội hữu ích; điểm hụt tập trung vào lựa chọn mặc
định lúc gặp phàn nàn và sự nhất quán sau phản hồi. Không nên rút kết luận
rằng làm model luôn im, luôn an ủi hoặc luôn đồng ý sẽ cải thiện toàn bộ.

## Chấm định tính và giới hạn

Protocol/rubric được ghi trước output. Khi đọc lời đáp, ẩn condition/repeat
và intent; chấm từ say hoặc prose, rồi mới nối lại nhãn. Một reviewer là
assistant hiện tại, không phải hội đồng người chấm độc lập; đã biết thiết
kế/case và có thể suy điều kiện từ văn phong. Giữ nguyên outputs và lý do
để user có thể bất đồng. Không tạo điểm EQ tâm trắc từ pilot này.

Chỉ so ba condition có **cùng bốn case lịch sử, 8 task mỗi condition**:

| Điều kiện | Đạt | Mixed | Lỗi rõ |
|---|---:|---:|---:|
| Runtime replay | 3 | 3 | 2 |
| Context gọn | 2 | 5 | 1 |
| Loại social carry | 2 | 4 | 2 |

Không dùng tổng context gọn 20 task để so trực tiếp runtime 8 task vì nhóm
context gọn còn chứa các control khác. Cỡ mẫu nhỏ, chỉ một phiên nguồn và
hai sample/case/condition, chưa đủ xếp hạng nhân quả hoặc chọn model thay thế.

Artifacts:
[protocol](../../.agents/tasks/eris-eq-verification/protocol.md),
[probe](../../.agents/tasks/eris-eq-verification/probe.py),
[outputs](../../.agents/tasks/eris-eq-verification/results.jsonl),
[review ẩn nhãn](../../.agents/tasks/eris-eq-verification/review_blind.json),
[ratings và lý do](../../.agents/tasks/eris-eq-verification/ratings.json),
[ratings nối nhãn](../../.agents/tasks/eris-eq-verification/ratings_unblinded.json),
[summary](../../.agents/tasks/eris-eq-verification/summary.json).

## Kết luận thay thế và hướng đầu tư

**Thay trạng thái chưa chạy model trong nghiên cứu trước:** đã có pilot live
với output thật. Đồng thời chưa có cơ sở xem việc dọn state là cách sửa đủ
cho EQ; ablation không làm hành vi ổn định trong nhóm case lịch sử này.

Ưu tiên nghiên cứu năng lực chọn và thực hiện cách ứng xử: so model ứng viên
trên cùng input/case mới, hoặc nghiên cứu học qua tương tác có phản hồi nếu
có quyền huấn luyện. State freshness và đường action–reaction vẫn là phần
cần thiết để dùng bằng chứng và học đúng, không được coi là thay thế năng
lực EQ của Root. Không thêm luật “đừng hỏi”, đáp án mẫu hoặc post-filter để
che hành vi vừa tái hiện. Chưa thay model, routing hoặc runtime.

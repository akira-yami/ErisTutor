# Áp lực chủ động trong prompt Root — 05/11/2026

Người dùng đặt giả thuyết ưu tiên bắt chuyện/mở lại làm Eris thiếu tự nhiên.
Đã kiểm source và replay live qua gateway hiện có; không sửa prompt runtime,
portable, memory thật, routing hoặc TTS.

## Chỉ dẫn thực sự được kiểm

Hai đoạn có mặt trong system của request lịch sử được khôi phục:

- “Khi mạch còn mở và thật sự còn điều muốn nói, nghiêng về after=may_continue
  thay vì mặc định wait_user.” Đoạn tiếp cho phép continue_self ngay cả khi
  người dùng có thể đang chuẩn bị trả lời.
- Với autonomous_signal/opening_signal/continuation, khuyến khích bắt chuyện
  khi có sự cho phép hoặc quan sát gợi mở; “Chỉ chọn no_reply khi thực sự
  không có gì đáng nói và hoàn cảnh không phù hợp để xen ngang.”

Đây là thiên lệch ưu tiên, không phải lệnh bắt buộc luôn nói. Trace lịch sử
vẫn có autonomous no_reply. Quyền được làm phiền có nguy cơ bị hiểu thành
lý do nên nói, nhưng chưa kiểm riêng từng mệnh đề.

## Thử nghiệm

30 calls: 5 tình huống × 2 điều kiện × 3 lần. Bốn wire request thật ngày
03/11 (opening council, timer sau câu hỏi chưa được đáp, xả bực về Kiro,
phản đối bị hỏi kỹ thuật); một đối chứng yêu cầu Eris tự chọn chuyện mở lời.
Giữ nguyên messages ngoài system, Self/history/event/state/schema và các
tham số wire. Điều kiện thử chỉ xóa hai đoạn trên, không thêm luật im lặng,
mẫu đồng cảm, cấm hỏi hoặc giới hạn độ dài. Thứ tự interleave cố định,
concurrency 2, cùng gateway DeepSeek và thinking như request gốc.

Assistant đọc lời đáp với condition/repeat/intent ẩn, ghi nhãn mô tả rồi
mở khóa. Không có người chấm độc lập hoặc phản ứng người dùng thật.

| Quan sát | Prompt gốc | Xóa hai đoạn |
| --- | ---: | ---: |
| Opening tự phát: chọn nói | 3/3 | 1/3 |
| Opening nhắc lại council | 2/3 | 0/3 |
| Timer nhắc lại câu hỏi chưa được đáp | 1/3 | 0/3 |
| Xả bực: chuyển sang hỏi chẩn đoán | 3/3 | 3/3 |
| Sau phản đối: hỏi tiếp điều làm user bực | 2/3 | 0/3 |
| Được yêu cầu chủ động: tự mở một chuyện cụ thể | 3/3 | 3/3 |

Hai câu hỏi sau phản đối là câu hỏi cảm xúc, không tái đòi brief/spec; không
đếm chúng thành hai lỗi kỹ thuật nặng. Các câu nhắc council ở opening chỉ
là callback/trêu, không tái hiện nguyên xi premise “council chưa ráp xong”.
No_reply không tự động chứng minh tốt hơn. Trong opening không có user mới
hay open loop, giảm nhắc lại có vẻ hợp nhịp hơn, nhưng đây là đánh giá của
một reviewer. Một câu opening ở bản xóa vẫn bám lời gọi và delivery cũ.

## Kết luận và giới hạn

Có tín hiệu thực nghiệm rằng hai đoạn thúc chủ động góp phần vào mở lại và
kéo dài. Kết quả không chứng minh đây là nguyên nhân duy nhất của EQ thấp:
nhánh xả bực vẫn hỏi chẩn đoán ở cả sáu mẫu. Bỏ áp lực không biến model thành
người đối thoại tinh tế, nhưng giữ được chủ động khi user yêu cầu trong
đối chứng nhỏ này. Hướng hợp lý là phân biệt quyền chủ động với nghĩa vụ
duy trì mạch, rồi kiểm đa lượt; không áp luật phải im hay luôn đồng cảm.

Ba mẫu mỗi ô quá ít để suy tỷ lệ production. Chỉ replay một bước, không
đo tần suất timer, lịch sử phản ứng nhiều lượt hay upstream model version.
Các nguồn áp lực khác vẫn còn, như event opening, state và receipts cũ;
không quy tác dụng cho từng đoạn riêng. Đợt so policy gọn/full trước không
cô lập hai đoạn này, nên không dùng kết quả cũ để loại trừ lỗi ưu tiên prompt.

Artifacts: `.agents/tasks/eris-initiative-ablation/` gồm protocol trước thử,
manifest các đoạn xóa, plan, results, review_blind/key, ratings và summary.
Không lưu private reasoning; kết quả chỉ là candidate Root, chưa qua delivery.

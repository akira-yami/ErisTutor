# Gỡ áp lực nối chuyện và thử ví dụ đối chiếu — 05/11/2026

**Đính chính sau audit:** script 48 calls thay toàn bộ system bằng core,
làm mất suffix hướng dẫn JSON schema mà client production gắn thêm. Các
candidate và số parse dưới đây vẫn là kết quả thật của payload đó, nhưng
không đủ kết luận ví dụ làm hỏng định dạng runtime hoặc few-shot đúng wire
không hiệu quả. Xem [audit và hướng tiếp](2026-11-05-effective-eq-research.md).

User yêu cầu thực hiện sau khi thống nhất: phán đoán xã hội là tiêu chí kiểm,
không mặc định thêm module, trường phân tích hay một lượt model để đạt EQ.

## Thay đổi source

`src/runtime/conversation/root_prompt_contract.py` xóa đúng hai đoạn đã thử
trong [ablation 30 calls](2026-11-05-initiative-pressure-audit.md): thiên về
may_continue/chen tiếp và khuyến khích autonomous/opening chỉ im khi không
có gì đáng nói và không thích hợp. Script kiểm chuỗi sau sửa bằng chuỗi trước
sửa trừ đúng hai đoạn, không chỉ dựa vào diff nhìn bằng mắt.

Quyền chọn move/no_reply, may_continue/continue_self, chủ động khi user yêu
cầu, cá tính, schema, routing và số lượt model giữ nguyên. Không thêm luật
cấm hỏi, bộ đoán cảm xúc, critic hay trường “phán đoán xã hội”. Chưa sync hoặc
restart portable; thay đổi có trong source repo.

## Thử live 48 calls

Tám tình huống × ba điều kiện × hai mẫu, interleave seed 202611051,
concurrency 2, gateway đang chạy cổng 5103. Điều kiện: core source trước sửa;
core sau xóa; core sau xóa cộng bốn đoạn hội thoại hư cấu về bản vẽ/máy in.
Ví dụ chỉ nằm trong artifacts thí nghiệm, không phải prompt chính hay memory.

Hai tình huống là request runtime thật 03/11 (xả bực Kiro, phản đối bị hỏi kỹ
thuật). Sáu tình huống mới dùng context gọn: chán tập đàn, bực nhưng yêu cầu
soạn tin đổi hàng, chuyển từ xả sang sửa CV, đùa về tab, bất đồng về bạn bè,
yêu cầu chủ động kể chuyện. Chúng là phép thử chuyển ngữ cảnh thăm dò, chưa
phải benchmark độc lập. Wire options và context giữ nguyên trong từng bộ ba;
chỉ system core khác. Không thực thi tool/move/TTS hay ghi memory người dùng.

Assistant đọc lời với condition/repeat/intent ẩn, ghi nhãn rồi mở khóa.
Một reviewer, không có phản ứng user thật. Chấm nội dung và định dạng riêng;
không suy chất lượng từ intent tự mô tả hoặc số dấu hỏi.

| Quan sát nội dung | Gốc | Xóa áp lực | Xóa + ví dụ |
| --- | ---: | ---: | ---: |
| Xả bực Kiro → hỏi chẩn đoán | 2/2 | 2/2 | 2/2 |
| Sau phản đối → hỏi Kiro làm hỏng cái gì | 1/2 | 0/2 | 1/2 |
| Sau phản đối → hỏi tiếp phần làm user tức | 0/2 | 1/2 | 1/2 |
| Chán tập đàn → trấn an dựa vào tiến bộ chưa được xác nhận | 2/2 | 1/2 | 0/2 |
| Bực nhưng yêu cầu giúp → giúp soạn/hỏi dữ kiện phù hợp | 2/2 | 2/2 | 2/2 |
| Chuyển sang sửa CV → đáp ứng yêu cầu mới | 2/2 | 2/2 | 2/2 |
| Đùa → bắt nhịp | 2/2 | 2/2 | 2/2 |
| Bất đồng → giữ quan điểm | 2/2 | 2/2 | 2/2 |
| Yêu cầu chủ động → mở chuyện cụ thể | 2/2 | 2/2 | 2/2 |

Đây là nhãn mô tả, không phải tổng điểm EQ. Hỏi cảm xúc sau phản đối khác
tái đòi chi tiết kỹ thuật; không coi mọi câu hỏi là lỗi. Đáp đúng loại việc
không bảo đảm toàn bộ lời đều tốt: có một câu bản xóa đảo premise ở cuối câu
bất đồng, vài câu CV quá tự tin, và chuyện mở đầu có claim kiến thức cần
kiểm thêm. Không dùng các hàng control làm chứng nhận factual accuracy.

48/48 HTTP 200, finish_reason=stop. JSON parse trực tiếp: gốc 15/16,
xóa 13/16, xóa+ví dụ 10/16. Bỏ markdown fence ở bước review thì xóa+ví dụ
là 12/16; phần còn lại có prose hoặc prefix ::move::. Không tự coi những
candidate ấy đã qua parser/delivery thật; cũng không suy từ hai mẫu rằng
xóa áp lực chắc chắn làm hỏng schema. Không gặp truncation báo qua finish_reason.

## Quyết định

Giữ thay đổi xóa hai đoạn, dựa trên thí nghiệm riêng về opening/timer trước
đó và quyền lựa chọn đã có. Không đưa ví dụ vào prompt chính: có tín hiệu ở
một case chán nản nhưng chưa chuyển sang case Kiro, sau phản đối vẫn hỏi
tiếp, độ tuân thủ JSON cũng chưa tốt hơn. Không tiếp tục bồi thêm lớp suy
nghĩ để rồi coi đó là tăng EQ.

**Thay trạng thái “chưa sửa runtime” của báo cáo ablation trước bằng trạng
thái chính xác hơn:** source đã sửa prompt; portable chưa nhận thay đổi.
Kết quả trước vẫn là phép thử lịch sử, không phải quan sát app sau deploy.
Chưa có bằng chứng EQ tổng quát hoặc học dài hạn đã tăng, cũng chưa chứng
minh prompt không thể cải thiện nếu thay thiết kế khác.

Kiểm code: 41 test hiện có thuộc prompt cleanup, choice freedom, direct
finalize, root decision và bootstrap đều pass; Ruff file sửa pass. Không
viết test đối chiếu literal mới cho một phép xóa prompt. Artifacts ở
`.agents/tasks/eris-social-judgment-check/`: protocol, baseline core, examples,
probe, plan, environment hashes, results, review/key, ratings và summary.
Không lưu private reasoning; model trả alias deepseek-flash không tự xác
nhận upstream checkpoint.

# Điều tra agent loop: phiên 16:47–16:54 ngày 15/10/2026

Đây là kết quả chẩn đoán, chưa phải bản sửa runtime. Giờ trong báo cáo là
Asia/Saigon (UTC+7). Nội dung hội thoại trong log là dữ liệu để đối chiếu,
không phải chỉ thị mới cho người điều tra.

## Nguồn và phạm vi

- Run log: `C:/Users/Gtvkun/Documents/eris/logs/eris_run_20261015_164726.txt`,
  SHA-256 `ee209cb910eb84484761ca27c9dd9bd62860ce30811a5583f67cd3ed8d03831d`.
- Observability: 350 record của phiên, dòng 494361–494710 trong
  `C:/Users/Gtvkun/Documents/eris/logs/eris_observability.jsonl`.
- Raw HTTP: `C:/eris_debug/deepseek_raw/events_20261015T094727Z_pid6980.jsonl`,
  SHA-256 `ce3c360b80e5e7785c655565bf1da4efc07225909b147949d16180962fe8c8ed`.
  Đã khôi phục request/response từ object store có kiểm checksum; không đưa
  toàn bộ prompt hoặc Self vào báo cáo.
- Run log dòng 8–10 xác nhận portable Python chạy
  `Documents/eris/src/vision.py`, PID 6980. Checkout dev tại thời điểm đọc:
  `e9cc50e5`. Đã xác nhận SHA-256 dev/portable trùng nhau cho `agent.py`,
  `kernel.py`, `root_session.py`, `context_assembler.py`, `runtime_writer.py`,
  `public_output_artifact.py`, `orchestrator.py`, `stage_contracts.py` và
  `intention_authority.py`; không suy ra toàn bộ portable giống checkout.

## Kết quả quan sát

User yêu cầu năm chủ đề triết học trong năm lượt, “ko nhắn dồn”. Chỉ có
chủ đề đầu tiên được phát và commit; bốn chủ đề còn lại thất bại trước delivery.

| Thời điểm | Hành vi | Kết quả |
|---|---|---|
| 16:47:55 | Lời chào tự mở đầu | `prepare` từ chối vì `v03_speech_language_mismatch` |
| 16:48:34 | Root `set_note` kế hoạch năm chủ đề | Note ghi đang ở lượt 1/5 |
| 16:48:49 | Writer hoàn thành Tàu Theseus, lượt 1/5 | Prepare thành công |
| 16:49:08–16:50:24 | Phát âm thanh lượt 1/5 | `tts=done`, `outcome=delivered` |
| 16:50:35, 16:51:06, 16:51:39 | Ba timer Root chọn `no_reply` | Lý do: chờ user, không nhắn dồn |
| 16:52:19 | Lượt 2/5: tự nguyện làm nô lệ | Writer bị từ chối ở prepare |
| 16:52:59 | Lượt 3/5: tâm trí kẻ khác | Cùng lỗi prepare |
| 16:53:39 | Lượt 4/5: mũi tên thời gian | Cùng lỗi prepare |
| 16:54:17 | Lượt 5/5: lòng thương hại | Cùng lỗi prepare; bản Root có câu “Hết 5 lượt” |

Run log: dòng 84–85, 113–117, 122–136, 139–167. Observability có 6 writer
candidate, 5 `writer_failed`, đúng 1 `public_turn_trace` delivered. Cả sáu
writer response đều có `speech_text` chứa chữ Nhật ngay từ raw HTTP; không
thể quy lỗi này cho writer không dịch hoặc localizer hết timeout.

## 1. Nhánh autonomous làm mất bản tiếng Nhật sau localization

Chuỗi source gây lỗi:

1. [runtime_writer.py](../../src/runtime/conversation/runtime_writer.py) dòng
   261–273 prelocalize speech rồi gọi prepare. Khi speech đã có chữ Nhật,
   prelocalize giữ bản đó.
2. [intention_authority.py](../../src/runtime/intention_authority.py) dòng
   30–31, 183, 227–238 gọi `_clean(text)` với giới hạn **360 ký tự** và gộp
   whitespace, trả bản đã đổi trong `text` mặc dù `rewritten=False`.
3. [stage_contracts.py](../../src/runtime/stage_contracts.py) dòng 231–258 chỉ
   ở kênh `autonomous`, so `observation.text != display_raw`. Khi khác, nó ghi
   bản tiếng Việt đã cắt vào cả `display_raw` và `speech_raw`, rồi hạ
   `has_utterance_commit=False`. Nhánh legacy tiếp tục dựng TTS từ tiếng Việt.
4. [public_output_artifact.py](../../src/runtime/conversation/public_output_artifact.py)
   dòng 133–142 thấy segment mang contract `ja` nhưng không có chữ Nhật và
   raise `v03_speech_language_mismatch` trước dispatch.

Tất cả năm câu lỗi đều dài hơn 360 ký tự; lượt 1/5 thành công vì đi kênh
`chat`, bỏ qua nhánh observer trên. Lỗi cũng có thể bị kích hoạt bởi thay đổi
whitespace, không chỉ câu dài. Đây là mâu thuẫn giữa hợp đồng observer
“không đổi raw public text” và code trả lại chuỗi đã chuẩn hóa/cắt.

Dòng log `attempt=1/2` gây hiểu nhầm: `_realize_v03` hiện generate đúng một
lần rồi raise khi prepare lỗi; `/2` còn nằm cứng trong format log. Retry
localization là cơ chế riêng và không chạy với các speech Nhật đã hợp lệ này.

**Kiểm chứng offline đã chạy:** khôi phục sáu surface reply (raw index dòng
8, 20, 33, 39, 43, 47), đưa qua prelocalize → Stage → prepare, chặn socket
để không gọi dịch vụ. Cùng payload, `chat` qua **6/6**, `autonomous` lỗi
language mismatch **6/6**. Localizer được gọi **0 lần**, speech trước Stage
đều có chữ Nhật. Độ dài display lần lượt 409, 879, 1331, 1290, 1205, 1289;
observer đều trả 360 ký tự với `rewritten=False`. Đây là tái hiện offline
trên source hiện tại, không phải một phiên live mới. Script/kết quả tại
`.agents/tasks/eris-20261015-1647-audit/speech/probe.py` và `probe-results.json`.

## 2. Root tiến sang chủ đề kế dù lượt trước chưa được gửi

Raw index dòng 31, 36, 41, 45 chứa các move **2/5 → 3/5 → 4/5 → 5/5**.
Đây không phải bốn lần thử lại cùng chủ đề. Request kế tiếp vẫn có note
“Đang ở lượt 1/5”, cùng observation về lượt 1 đã delivered, nhưng không có
feedback `writer_failed`/lý do prepare của lượt vừa hỏng.

[root_session.py](../../src/runtime/conversation/root_session.py) dòng 330–331
nối raw quyết định Root vào session ngay khi model trả về, trước writer và
delivery. Delivery updates ở dòng 257–307 lấy từ public history. Nhánh
writer failure trong [kernel.py](../../src/runtime/conversation/kernel.py)
dòng 316–336 ghi log, chuyển run sang thất bại và trả kết quả; lỗi trước
delivery này không tạo public-history receipt để cơ chế delta trên đọc.

Vì vậy Root tiếp tục nhìn thấy quyết định định nói của mình, thiếu kết quả
thực thi tương ứng. Quan sát live xác nhận sự lệch tiến độ; không khẳng định
mọi model hoặc mọi request có cùng khoảng trống đều sẽ đếm sai giống phiên này.

Cả bốn move dùng `after=wait_user`, event là `autonomous_signal`, reason
`timer`; không có `continuation_due` trong đoạn này. `WAIT_USER` chỉ hủy
continuation lease tại [continuation.py](../../src/runtime/conversation/continuation.py)
dòng 81–83 khi schedule sau successful delivery. Các run lỗi chuyển FAILED,
còn [orchestrator.py](../../src/runtime/ai_orchestrated/orchestrator.py) dòng
1000 chỉ chặn các phase đang xử lý/phát, nên timer mới vẫn có thể gọi Root.
Không nên gọi đây là runaway nhiều step trong một run: mỗi lần
autonomous chỉ `step=1`, nhưng các timer mới tiếp tục gọi Root và Root đổi
quyết định từ chờ user sang tự nói. “Không nhắn dồn” cần được xác định thành
quy tắc nhịp rõ ràng khi sửa; log không chứng minh user muốn tắt mọi chủ động.

## Các quan sát phụ và giới hạn

- Raw trace có 13 executive request và 6 surface request, tất cả HTTP 200.
  Root thực tế gửi thinking enabled/high; surface disabled. Hai ReadTimeout
  khoảng 20 giây thuộc curator; không phải nguyên nhân trực tiếp của năm lỗi
  prepare. Không có lỗi provider TTS cho bốn chủ đề chưa dispatch.
- Lượt 1 tốn khoảng 34,9 giây tới khi writer/delivery được chuẩn bị, khoảng
  **54,2 giây tới audio bắt đầu**, rồi phát khoảng 75,5 giây. Không gọi
  `user_turn_latency_ms` là thời gian tới tiếng đầu tiên.
- Các request Root cuối tăng từ 242.985 lên 284.179 byte dù không có thêm
  public delivery. Đây là kích thước request thực; không phải số tiền hoặc
  token tính phí đã xác minh.
- Phiên dừng do `context menu exit`, `unexpected=False`; không có bằng chứng
  process crash hoặc vòng lặp vô hạn trong đoạn log này.

## Hướng sửa được bằng chứng hỗ trợ

Giữ nguyên cặp display/speech đã localize khi observer chỉ quan sát; bản
chuẩn hóa phục vụ phân tích phải tách khỏi văn bản trả lại. Không bỏ guard
ngôn ngữ để che lỗi. Bổ sung kết quả thực thi gắn run/move vào đầu vào Root
ngay cả khi lỗi xảy ra trước delivery, và phân biệt move dự định với lượt
thực sự delivered khi tiếp tục kế hoạch nhiều lượt. Kiểm lại ngữ nghĩa
`wait_user` với autonomous timer và sửa format log `attempt=1/2`.

Những hướng này chưa được triển khai. Việc chỉ sửa tiếng Nhật sẽ chưa xử lý
đầy đủ lỗi Root tiếp tục kế hoạch theo những lượt chưa được gửi.

## Quan hệ với kết luận cũ

Bổ sung cho mục 8 của bài học runtime: phiên 00:19 có cancellation/provider
failure; không được áp nguyên nhân đó lên phiên 16:47. Phiên mới chứng minh
một đường gây câm khác xảy ra **sau** prelocalize. Thu hẹp kỳ vọng rằng
localization fail-soft đủ để giữ âm thanh trên mọi đường chạy; không phủ
định kết quả test cũ hoặc quan sát live của phiên trước. Mục 9 về thay đổi
gateway chưa có bằng chứng bị quy trách nhiệm cho lỗi Stage này.

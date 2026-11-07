# ElevenLabs chậm: refresh token đi qua IPv6 không kết nối được

Ngày điều tra: 18/10/2026, giờ Việt Nam. Phạm vi: đọc runtime đang chạy và probe
mạng không gửi credential, không tạo audio, không sửa cấu hình mạng/runtime.

## Kết quả

Độ trễ chính của hai lượt 10:47–10:51 nằm **trước request tạo giọng**, tại bước
lấy/làm mới bearer token của pool. Request synth ElevenLabs hoàn tất trong
3,4–3,8 giây; đồng bộ bubble đợi playback đúng yêu cầu của user.

| Mốc | Lượt 1 | Lượt 2 |
|---|---|---|
| TTS dispatch | 10:47:22.061 | 10:49:39.689 |
| Broker nhận request ElevenLabs | 10:48:59.964 | 10:51:17.954 |
| Thời gian trước request | 97,903 giây | 98,265 giây |
| Request ElevenLabs hoàn tất | 10:49:03.782 | 10:51:21.364 |
| Thời gian request | 3,817 giây | 3,408 giây |
| Audio start ghi trong public trace | 10:49:04.622 | 10:51:21.736 |

JWT của account 4/5 có `iat` lần lượt 10:48:58 và 10:51:16, tương ứng thời điểm
bearer được cập nhật trong SQLite. Chỉ đọc clock, không xuất token/credential.
Account selection ưu tiên quota còn lại, nên lượt sau có thể chọn account khác
còn token hết hạn dù account vừa dùng đã được refresh.

## Cơ chế đã tái hiện

- DNS `securetoken.googleapis.com` trả 8 địa chỉ IPv6 trước 8 IPv4; DNS chỉ mất
  0,062 giây trong probe, không phải khoảng chờ 98 giây.
- Cả 8 kết nối TCP IPv6 đều timeout khi thử giới hạn 1 giây/địa chỉ. Tất cả
  IPv4 kết nối thành công trong khoảng 0,032–0,063 giây.
- `requests`/`urllib3` hiện tại thử lần lượt địa chỉ và áp connect timeout riêng
  cho mỗi địa chỉ (`urllib3.util.connection.create_connection`, source cài local).
- `ElevenPoolDB.sync_subscription` gọi Firebase bằng `requests.post(...,
  timeout=12)`. Tám connect timeout 12 giây tạo khoảng 96 giây trước khi tới
  IPv4. Cơ chế tái hiện và clock token khớp khoảng chờ trước synth trong trace;
  không có packet capture của chính hai request lịch sử để chia chính xác mọi ms.
- GET công khai tới `https://securetoken.googleapis.com/`, không credential:
  timeout connect 0,3 giây cho thấy 8 lần IPv6 timeout rồi IPv4 thành công,
  tổng 3,141 giây (HTTP 404 đúng với URL probe). Cùng URL và timeout, chỉ ưu tiên
  IPv4 trong process probe: 0,672 giây, cũng HTTP 404. Patch tạm được khôi phục
  ngay; không đổi IPv6 của Windows hay source production.

Hướng sửa phù hợp là giới hạn fallback kết nối ở HTTP client refresh/sync pool
(ví dụ adapter ưu tiên IPv4 theo host khi IPv6 không hoạt động), đồng thời tránh
để refresh nhiều account hết hạn nối tiếp trên critical path. Không cần thay đổi
nguyên tắc text chờ `audio_start`. Chưa triển khai hướng sửa trong lần chẩn đoán.

## Nguồn và giới hạn

- Process: `Documents/eris/Eris.exe`, Python portable PID 27492. Bốn module
  `public_output_runtime.py`, `tts_engine.py`, `tts_task_manager.py`,
  `stage_dispatcher.py` trong portable trùng byte với checkout khi kiểm tra.
- `Documents/eris/logs/eris_run_20261018_104609.txt`.
- `Documents/eris/logs/eris_observability.jsonl`: `broker_admitted_total`,
  `broker_queue_wait_ms`, `broker_request_duration_ms`, lane `tts_current_turn`.
- `Documents/ErisLive/logs/autonomous_trace.jsonl`: hai delivery
  `delivery:372a25fdf32e4797bd3181ecba0788a1` và
  `delivery:5a79bc1010ae474e86691c9e1828e531`.
- SQLite pool được đọc bằng `mode=ro`; probe chỉ truy cập endpoint công khai.

Kết quả thay thế suy đoán “ElevenLabs synth hoặc text-sync giữ lại khoảng
100 giây” trong hai lượt này. Không áp nguyên nhân IPv6 lên mọi phiên cũ hoặc
mọi lần chậm; chưa đo authenticated refresh sau sửa và chưa sửa runtime.

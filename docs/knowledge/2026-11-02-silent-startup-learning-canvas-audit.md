# Eris im lặng ngày 02/11/2026: provider, warm-up và Canvas focus

Phạm vi: hai phiên portable mới nhất `13:40:21–13:41:54` và
`13:45:51–13:48:27`, giờ Việt Nam. Chỉ điều tra log/raw request và source;
không sửa runtime, config, memory, không restart hoặc gọi LLM/TTS mới.

## Nguồn và kiểm chứng

- `C:/Users/Gtvkun/Documents/eris/logs/eris_run_20261102_134021.txt`.
- `C:/Users/Gtvkun/Documents/eris/logs/eris_run_20261102_134551.txt`.
- Observability từ 13:40 ngày 02/11 trong `eris_observability.jsonl`.
- `C:/eris_debug/deepseek_raw/events_20261102T064023Z_pid7468.jsonl` và
  `events_20261102T064552Z_pid2112.jsonl`. Payload phiên PID 2112 được khôi
  phục từ gzip objects và kiểm SHA-256 với manifest trước khi phân tích.
- Bảy module `conversation/{agent,root_session,kernel,context_assembler,
  runtime_writer}.py`, `learning_canvas/{service,events}.py` trùng SHA-256
  giữa checkout và portable tại lúc kiểm tra.

## Phiên 13:40: chưa tạo được lời nói vì provider lỗi

Gateway `127.0.0.1:5103` từ chối kết nối (`WinError 10061`). Official fallback
trả HTTP 401, `Authentication Fails … api key … invalid` lúc 13:40:54.
Warm-up thất bại; opening và các autonomous attempt kết thúc ở Root với
`root step circuit breaker reached`, tổng cộng bốn lần. Không có writer
candidate hay `audio_start`. Đây là lỗi trước TTS, không phải bằng chứng
context overflow hoặc ElevenLabs hết quota.

Sau hai phiên, kiểm tra read-only `docker ps` thấy whale2api/poolui đang
healthy. Trạng thái đó không phủ nhận gateway đã unavailable lúc 13:40 và
không chứng minh fallback key đã được sửa.

## Phiên 13:45: provider trả được, lời chưa đến TTS trước khi thoát

| Giờ Việt Nam | Quan sát trực tiếp |
|---|---|
| 13:45:57.174 → 13:47:09.269 | Warm-up Root mất **72,093 giây**, HTTP 200, trả `no_reply/self_loaded`; không phải một lời public. |
| 13:46:06 → 13:46:44 | Run log ghi **11** lần wake `learning_canvas:navigate`. |
| 13:47:09.392 → 13:47:46.331 | Opening one-shot mất **36,938 giây**, HTTP 200, có move/lời chào. Không có surface request cho lời chào này. |
| 13:47:49.922 → 13:48:16.955 | Root xử lý một `tutor_result`, navigate cuối tới `javadi:v1:dependency`, focus revision 11; mất **27,031 giây**, HTTP 200, có move giải thích DI. |
| 13:48:17.285 | Surface writer bắt đầu request. Trace không có response cho request này. |
| 13:48:27.485 | App dừng vision.py; lifecycle ghi `context menu exit`. Writer mới chờ khoảng **10,2 giây**. |

**Suy luận có đối chiếu source:** navigate gọi `focus_change_guard` →
`note_focus_change`, xóa active run chưa commit trong phase THINKING/WRITING.
Root kiểm `still_active()` sau response. Điều này giải thích opening có move
trên wire nhưng không finalize/không gọi writer sau các click đổi focus.
Không có event structured riêng ghi mã stale cho opening, nên không gọi
đây là receipt stale độc lập. Navigate được coalesce trong queue; không thấy
11 Root request hoặc 11 bộ bài học tích lũy trong prompt.

Hai run log đều chỉ có ElevenLabs **background warm-up complete**, không có
writer candidate hoặc `audio_start`. Warm-up token/connection không chứng minh
provider tổng hợp/phát audio thành công. Phiên sau chưa tới synthesis nên chưa
có cơ sở kết luận loa, voice hay ElevenLabs hỏng. Text vẫn đợi audio_start theo
yêu cầu sản phẩm; kẹt trước playback khiến cả chữ và tiếng đều chưa xuất hiện.

## Learning có nhồi đầy context không?

Không có context-length error, preflight trim hay cap rejection trong hai phiên.
Ba Root response phiên sau đều HTTP 200 và trả JSON có nội dung đúng cấu trúc.
Usage trên wire gateway báo prompt_tokens lần lượt **30.566**, **16.957**,
**43.217**; đây là số do gateway báo, không xác nhận độc lập tokenizer hoặc
billing upstream. Không lấy token_pressure_ratio của client làm bằng chứng
giới hạn thật của model: local preflight đang dùng hard cap rất lớn.

Request Root cho Canvas có tổng **137.100 ký tự content** sau schema injection
(khác request bytes). Phần bootstrap user message là **81.906 ký tự**; Self
breakdown từ run log cho thấy living_user **53.327**, trong đó profile **28.370**,
recent episodes **21.587**, actions **3.328** ký tự. Các component này lồng nhau,
không cộng chúng như các phần độc lập.

Đo JSON compact trên chính event request, không phải fixture:

| Component | Ký tự |
|---|---:|
| Canvas capability trong event metadata, có 5 node và chi tiết 1 node/2 block | 2.869 |
| learner_continuity trong runtime_context | 3.019 |
| Toàn event tutor_result, bao gồm Canvas capability ở trên | 3.271 |
| conversation_move_receipts trong runtime_context | 11.259 |
| autonomous_self_context | 5.926 |
| engagement_context | 3.999 |
| grounding_context | 3.752 |

Canvas capability + learner_continuity khoảng **5,9k ký tự**, ~4,3% tổng content
request này. Không có bằng chứng toàn bộ curriculum bị nhét vào Root. Prompt
chung vẫn đáng tối ưu, nhưng không đủ dữ liệu để quy 72 giây warm-up hoặc toàn
bộ latency cho kích thước prompt. Muốn giải thích thời gian upstream cần trace
gateway/queue/provider riêng; không suy từ HTTP latency thành thời gian TTS.

## Kết luận và hướng xử lý

Thay giả thuyết “learning tràn context khiến câm” cho hai phiên này bằng:
provider unavailable + fallback 401 ở phiên đầu; phiên sau warm-up dài,
opening mất hiệu lực khi đổi Canvas focus, rồi app thoát khi writer còn chờ.
Không khẳng định Eris sẽ tự phát tiếng nếu đợi lâu hơn: writer chưa có response
và chưa đo synthesis/playback.

Ưu tiên tiếp theo nếu sửa: readiness của gateway lúc mở app và fallback auth;
latency/trạng thái hiển thị khi warm-up hoặc writer còn chạy; quan sát rõ lý do
run bị focus invalidation. Nếu tối ưu context, đo lại Self/memory và receipts
trước khi cắt nội dung học. Chưa áp dụng bất kỳ thay đổi runtime nào trong audit.

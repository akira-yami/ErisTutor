# Điều tra phiên 15/10/2026: agent loop, độ trễ và TTS

Ngày điều tra: **15/10/2026**. Phạm vi là phiên **00:19:56–00:33:28,
Asia/Saigon** (14/10, 17:19:56–17:33:28 UTC). Không sửa runtime, cấu hình,
key hoặc bản triển khai; không gọi provider live.

## Nguồn và giới hạn

- Run log: `C:/Users/Gtvkun/Documents/eris/logs/eris_run_20261015_001956.txt`.
- Observability: `C:/Users/Gtvkun/Documents/eris/logs/eris_observability.jsonl`;
  lọc được 976 record của phiên. Số dòng bên dưới chỉ file gốc, không chỉ bản lọc.
- Raw wire: `C:/eris_debug/deepseek_raw/events_20261014T171957Z_pid26564.jsonl`.
  Chỉ dùng request/response liên quan và metadata; không đưa credential vào báo cáo.
- Run log dòng 4, 8–10 xác nhận `Documents/eris/Eris.exe` chạy portable Python
  với `Documents/eris/src/vision.py`, PID 26564. Các file source liên quan đã
  kiểm tra ở bản chạy và checkout dev trùng byte; mtime các file lõi trước phiên.
  Đây là đối chiếu source hiện tại và trace, không phải manifest build có hash.

## 1. Đoạn cuối vẫn có Root quyết định; không có runaway nhiều step

Run log 361–364, 414–418, 443–446 đều là `step=1`, `event_kind=user_turn`,
`action=move` rồi writer prepared/pass. Không có chuỗi tool loop mất tiến triển
ở đoạn này. Phiên kết thúc do `context menu exit`, `unexpected=False` (476–482).

Câu về tiền điện phát xong lúc 00:31:22 (382). Root chọn `after=may_continue`,
kernel tạo continuation sau successful delivery. Lúc 00:31:51, Root nhận
`continuation_due` và chọn nói thêm một câu về tắt đèn để góp tiền điện bằng
bóng tối (387; raw reply dòng 99). Surface giữ ý đó (raw 101). Đây là một
continuation thực sự được Root cho phép, không phải transport gửi trùng câu.

Ở lượt “bạn nghĩ bạn là cái gì”, Root chọn giọng khô, chậm, nghiêm và lời tự
định nghĩa bản thân (raw 111); surface giữ nội dung, dùng `sad_distant` (113).
Vì vậy sự đổi giọng của lượt này bắt nguồn ở Root, không phải writer âm thầm
quyết ý mới. Observer lượt tiền điện xong lúc 00:31:37, sau khi request
continuation đã bắt đầu lúc 00:31:29.685 (raw 96); feedback đó chưa có trong
request continuation này.

Source: [kernel.py](../../src/runtime/conversation/kernel.py), 605–635;
[continuation.py](../../src/runtime/conversation/continuation.py), 81–105;
[agent.py](../../src/runtime/conversation/agent.py), 304–312, 630–631.

## 2. Độ trễ rồi preemption làm ba lượt cuối không có audio bắt đầu

| Lượt | Writer prepared | User mới được xử lý | Kết quả delivery |
|---|---|---|---|
| Continuation tiền điện | 00:31:57.213 | 00:32:00.511 | 00:32:01.734 cancelled |
| Trả lời “ký hợp đồng” | 00:32:32.654 | 00:32:35.175 | 00:32:35.401 cancelled |
| Trả lời “bạn nghĩ bạn là cái gì” | 00:33:11.225 | 00:33:16.208 | 00:33:16.549 cancelled |

Run log: 388–404, 418–433, 446–464. Không có `audio_playback_started` cho
ba delivery này. Hai lượt chat cuối mất **32.327s** và **37.206s** từ user turn
đến lúc cycle chuẩn bị/gửi public surface (observability 492732, 492800);
đây chưa phải thời gian tới tiếng đầu tiên. Surface writer chỉ chiếm khoảng
5.313s và 8.000s provider call (492704, 492773). Raw request Root vẫn bật
thinking/high, surface tắt thinking; không suy ra toàn hệ thống đã thinking OFF.
Raw HTTP đo riêng hai lượt này: Root 23.312s / 24.172s, surface 5.094s /
7.703s (raw 103–107, 109–113). Số đo HTTP khác metric stage/provider vì biên
đo khác nhau. Không dùng payload lớn để khẳng định toàn bộ độ trễ do context.

`chat_start` chỉ đánh dấu đang nhập. Hủy thực sự diễn ra khi xử lý message
và phân loại interrupt; log `reason=enter_chat_mode` dễ khiến hiểu nhầm rằng
chỉ mở ô nhập đã hủy audio. Input cuối “ô” không có trong backchannel whitelist:
nó bị coi là STEERING, `cancel_generation=True`, `cancel_audio=True`, và
VoiceManager chọn yield. Pure local probe xác nhận “ô”, “ô.” bị hủy, trong khi
“ừ” được giữ audio. Việc user định dùng “ô” như lời phụ họa là suy luận ngữ nghĩa.

Source: [interrupts.py](../../src/runtime/conversation/interrupts.py), 10, 22–30;
[incoming_dispatcher.py](../../src/runtime/incoming_dispatcher.py), 134–164;
[voice_manager.py](../../src/runtime/voice_manager.py), 32, 208–224.

## 3. Lỗi TTS thật đã fallback thành công; cancellation còn có lỗi hạch toán

00:22:14.936 ElevenLabs bị `RemoteDisconnected`/connection aborted (run 147).
Request account_4 thất bại sau khoảng 36ms (observability 492096). Gemini
fallback hoàn thành sau 12.905s (492104), playback bắt đầu 00:22:34.993 và
kết thúc 00:22:54.965 (run 149, 157). Delivery
`delivery:b73305deceed40379bdc291810257843` kết thúc `tts=done`, delivered
(run 162; observability 492109). Không có bằng chứng lỗi này do transcript
tiếng Nhật hoặc timeout localizer. Nguyên nhân phía server đóng kết nối sâu
hơn không xác định từ trace này.

Có lỗi code độc lập: `_synthesize_elevenlabs_wav` bắt cả
`RuntimeError('tts_cancelled')` rồi gọi `record_failure`; `_tts_failure_reason`
không phân biệt cancel và trả `provider_error`. Với cùng breaker key,
ba lần cancel đủ mở breaker mặc định threshold=3. Probe pure local dùng
class/function thật xác nhận CLOSED → CLOSED → OPEN và chặn request kế tiếp.
Ba lần hủy cuối dùng account_4, nhưng log không ghi đầy đủ breaker key/trạng thái
hay mọi override threshold; **không khẳng định breaker đã mở live**.

Một lỗi observability che trạng thái này: ElevenLabs tạo breaker key 5 phần
nhưng `_record_metric` unpack 4 phần rồi nuốt exception. Probe collector cục bộ:
key 5 phần không phát metric; control key 4 phần có phát metric.

Source: [tts_engine.py](../../src/runtime/tts_engine.py), 902–907, 1073–1103,
1579–1593; [tts_circuit_breaker.py](../../src/runtime/tts_circuit_breaker.py),
40–48, 94–110. Đây là lỗi contract xác nhận bằng source/probe, chưa có live
request sau lần hủy thứ ba để chứng minh ảnh hưởng downstream trong phiên.

## 4. Circuit LLM lặp lại về sau thuộc curator, không phải Root chat

Cả tám dòng `PROVIDER CIRCUIT` về sau (run 225, 268, 311, 343, 360, 413,
442, 475) trùng completion lỗi của `memory_curator`/`local__curator` trong
1–5ms. Raw trace xác nhận 11 lần `ReadTimeout` gần 8s. Queue wait curator
chỉ khoảng 0.967–7.637ms; đây là timeout HTTP, không phải mắc hàng đợi broker.
Circuit được tách theo role `#deepseek_web:curator`. Root và surface vẫn có
HTTP 200 sau các warning, nên không diễn giải chúng thành main brain chết.

Riêng đầu phiên có lỗi foreground thật: Root local 429
“no accounts configured or all accounts are busy”, official fallback 401
invalid API key, rồi surface local cũng 429 (run 124–131; raw 16–21).
Lượt này không có public surface, `writer_failed`. Warning “root step circuit
breaker reached” cũng được log sau unknown action break ở step=1; không chứng
minh đã dùng hết max_root_steps. Gateway message gộp hai điều kiện không có
account phù hợp và không acquire được account; trace không phân biệt được chúng.

Provider local chat/observer đi ngoài broker trong source hiện tại. Vì vậy
`remote_calls_per_user_turn` và broker queue stats không phải tổng số call LLM
của phiên; không dùng các metric đó để kết luận contention đã được loại trừ.

## 5. History reconcile có phình request, nhưng chưa chứng minh làm lệch câu trả lời

Raw request 103 gửi câu continuation ở trạng thái TTS pending. Request 109
gửi lại 16 history entries qua `recent_public_history_reconcile` vì chính câu
đó chuyển sang cancelled, làm content khác và phá suffix/prefix overlap.
`RootConversationSession` append reconcile vào chuỗi message hiện có. Chuỗi
messages 32 → 34 → 36 → 38 và request bytes 364837 → 392681 → 406861 → 421974.
Request cuối (raw 115) reconcile lại 18 entries sau receipt cancellation tiếp.

Đây là hiện tượng phình payload đã quan sát, không đủ chứng minh nguyên nhân
đổi cá tính/chủ đề hoặc tăng latency. Reply 111 vẫn trả lời đúng input mới;
request cuối “ô” còn đang chạy khi user thoát. Không gọi `public_turns=16`
là 16 lượt vừa phát âm thanh hay 16 vòng Root tự chạy.

Source: [prompt_context_builder.py](../../src/runtime/prompt_context_builder.py),
329–337; [root_session.py](../../src/runtime/conversation/root_session.py),
252–281, 298–304.

## 6. Bổ sung source khi đánh giá phương án sửa

Các mục dưới là **đọc source ngày 15/10**, không nâng thành nguyên nhân live
đã được tái hiện trong phiên:

- [chat_cycle.py](../../src/runtime/chat_cycle.py), 37–61 chỉ nhận
  `cancel_audio`, nhưng hủy LLM generation vô điều kiện. Ingress chỉ early-return
  backchannel khi `speech_floor_busy` (audio pending/active). Vì vậy chỉ thêm
  alias vào whitelist chưa đủ bảo vệ reply ở pha composing; cần tôn trọng
  `cancel_generation` riêng xuyên ingress/VoiceManager/ChatCycle.
- [tts_circuit_breaker.py](../../src/runtime/tts_circuit_breaker.py), 72–100
  cấp slot HALF_OPEN và chỉ nhả khi success/failure. Bỏ failure khi cancel phải
  kèm cleanup permit đúng một lần; không ghi success thay cancellation vì sẽ
  xóa lịch sử lỗi hoặc hồi phục breaker khi chưa có response thành công.
- [deepseek.py](../../src/llm/deepseek.py), 69–73 map mọi effort không phải
  max/xhigh/maximum thành high. Root yêu cầu medium vẫn ra wire high; chỉ đổi
  knob medium/low chưa tạo thay đổi thực. Không suy rằng gateway hỗ trợ thêm
  mức effort; phải kiểm capability/request thật trước khi thay ánh xạ.
- [test_background_living_user_observer.py](../../src/tests/test_background_living_user_observer.py),
  12–16 bảo vệ curator required và concurrency=1. Không đánh đồng nền với
  optional có thể bỏ. Defer/coalesce phải giữ mọi evidence ID và đúng mutation
  owner; timeout/retry phải có budget, không chạy thêm scheduler thứ hai.
- [agent.py](../../src/runtime/conversation/agent.py), 169–172 tạo degraded
  `say` là chỉ dẫn “Trả lời trực tiếp...” cho writer. Chỉ đổi nó thành câu báo
  lỗi vẫn chưa bỏ dependency: [runtime_writer.py](../../src/runtime/conversation/runtime_writer.py),
  316–319, 421 tiếp tục gọi generate. Nếu thêm fail-soft không cần LLM, cần
  tín hiệu lỗi nội bộ đáng tin và candidate thông báo VI/JA sẵn, đi qua cùng
  prepare/delivery, không tạo lane phát lời mới hay giả đáp án.

## Hướng sửa theo bằng chứng, chưa thực hiện

1. Tách cancellation khỏi provider failure ở TTS/broker; cleanup HALF_OPEN
   permit trung tính, đúng một lần; thống nhất key/metric có account scope.
2. Sửa phân loại lời phụ họa qua shared contract có xử lý teencode/dấu câu,
   tôn trọng cancel_generation và cancel_audio theo pha; giữ ưu tiên stop/
   correction/câu hỏi mới, không coi mọi câu ngắn là backchannel.
3. Điều chỉnh continuation theo giá trị nội dung mới và feedback thật; giữ Root
   sở hữu quyết định, không thêm một semantic governor phát lời thứ hai.
4. Xử lý curator timeout theo deadline và measured thinking/request latency,
   giữ evidence khi defer/retry; sửa fallback official key và tình trạng pool
   gateway riêng. Provider error phải đi ra contract rõ, không empty action.
5. Đo Root → writer → audio đầu tiên; xử lý history status update bằng identity
   ổn định thay vì replay toàn history mỗi khi receipt đổi content projection.

Không có thay đổi runtime nào được coi là đã sửa lỗi hoặc đã qua live validation.

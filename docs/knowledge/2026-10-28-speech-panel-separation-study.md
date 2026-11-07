# Nghiên cứu tách lời nói và tài liệu toán/code của Eris

Ngày: 28/10/2026. Phạm vi: đọc source, đối chiếu raw response cũ và probe offline.
Không sửa/deploy runtime, không gọi model, ElevenLabs hoặc loa thật trong nghiên cứu này.

## Kết luận

Cần có ba đầu ra rõ ràng: tài liệu đầy đủ cho panel, lời thoại cho bubble,
và bản ngôn ngữ phát âm của chính lời thoại đó. Hai trường `text`/`speech_text`
hiện có chỉ tách hiển thị khỏi phát âm; chúng chưa tách **tài liệu** khỏi **hội thoại**.

Sửa prompt hoặc xóa ký hiệu ở cửa TTS không giải quyết đủ vấn đề. Với panel V6,
lấy tài liệu từ bubble đã làm sạch còn có thể làm mất code trước khi dàn trang.
Root nên quyết định cả nội dung tài liệu và ý giải thích bằng lời; presenter
chỉ dàn trang, localizer chỉ chuyển lời nói sang ngôn ngữ giọng đã chọn.

## Bằng chứng từ đường chạy

| Điểm nối | Hành vi hiện tại | Hệ quả |
|---|---|---|
| Root move | Schema có `intent`, `say`, `delivery`, `after`; chưa có tài liệu riêng | Không có chỗ chứa đồng thời lời giải đầy đủ và brief lời nói trong một move |
| `NextConversationMove.from_mapping` | `say` gộp whitespace và giới hạn 4.000 ký tự | Không thích hợp làm kho mã nguồn hoặc tài liệu có cấu trúc |
| Public writer | Yêu cầu `text` và `speech_text` cùng nghĩa, cùng tông | Nếu `text` là cả lời giải thì writer có lý do để đọc cả lời giải |
| Prompt nhắc nói ngắn | Áp dụng khi Root chỉ mời xem panel sau tool thành công | Chưa bao phủ lời giải tự suy luận, giải thích code và prose được cứu |
| Speech fallback | `speech_text` thiếu thì lấy display; prose Root được cứu cũng đi vào nhánh này | Công thức/code trong display có thể thành transcript dịch |
| Localizer | Chỉ chuẩn hóa whitespace trước khi dịch; đã có kana/kanji thì bỏ dịch | Không chuyển công thức thành lời giải thích; speech Nhật chứa ký hiệu vẫn đi tiếp |
| `TTSFilter` | Xóa fenced/inline code, URL; không có bộ diễn giải toán | Code có thể bị bỏ muộn, công thức vẫn giữ nguyên |
| `DisplayFilter` | Fenced code thành `[code]`, heading/newline được làm phẳng | Nội dung bubble không còn là nguồn tài liệu đầy đủ |
| Panel V6 tự tạo | `dispatch_live_document(text=body_payload['text'])` sau audio_start | Presenter nhận bubble đã làm sạch, không khôi phục được code mất từ trước |
| Artifact tool | math/python workspace gửi artifact riêng và đợi ACK | Đường này đã tách tài liệu khỏi lời nói; không bị đồng nhất với nhánh V6 lấy bubble |

Các call site chính:

- [Root schema](../../src/runtime/conversation/agent.py:31),
  [move parser](../../src/runtime/conversation/contracts.py:60).
- [Writer prompt](../../src/runtime/ai_orchestrated/model_router.py:1716),
  [prose recovery và writer](../../src/runtime/conversation/runtime_writer.py:345).
- [Utterance fallback](../../src/runtime/utterance_spec.py:170),
  [prelocalize](../../src/runtime/conversation/runtime_writer.py:36).
- [Display/TTS filters](../../src/runtime/stage_transformers.py:264),
  [prepare](../../src/runtime/conversation/public_output_artifact.py:87).
- [V6 dispatch](../../src/runtime/public_output_runtime.py:733),
  [presenter](../../src/runtime/workspace/live_document.py:257).
- [Artifact actions](../../src/actions/executor.py:377).

## Quan sát live cũ và probe mới

Raw wire `C:/eris_debug/deepseek_raw/*pid22680.jsonl` của phiên 16:16 ngày 28/10
có Surface response lúc 16:18:03 chứa speech Nhật với nguyên các biểu thức
`cos x < 1/2`, `D = (...)`, `k ∈ ℤ`. Run log ghi audio_start/text lúc 16:18:22
và delivered lúc 16:19:20. Đây là bằng chứng ký hiệu đã nằm trong lời đọc định
phát và playback có lifecycle hoàn tất; không có audio recording để chấm cách
phát âm. Phiên này **trước build 21:55**, không chứng minh panel V6 mới đã render
chính lượt đó, cũng không xác nhận câu trả lời toán đúng.

Probe offline trên source hiện tại:

| Ca | Kết quả |
|---|---|
| Display có LaTeX, thiếu speech | Localizer nhận nguyên công thức; TTSFilter trên nguồn cũng giữ công thức |
| Display có fenced Python, thiếu speech | Localizer nhận cả code, indentation bị normalize; TTSFilter xóa code; display gửi panel còn `[code]` |
| Display có code, speech là giải thích bằng lời riêng | Localizer chỉ nhận lời giải thích; nhưng nguồn panel lấy display sạch vẫn mất code |
| Speech Nhật có biểu thức | Không gọi localizer; biểu thức còn nguyên qua TTSFilter |

Probe chỉ capture đầu vào và dùng câu Nhật giả cố định, không đánh giá chất lượng
dịch/phát âm. Năm module được probe khớp SHA-256 bản portable. Dữ liệu:
[probe JSON](../reports/2026-10-28-speech-panel-probe.json). Script tái chạy cục bộ:
`scratch/speech_panel_research_20261028.py` bằng portable Python.

**Đính chính phạm vi mục 24 trong tài liệu bài học:** code có thể vào bước dịch,
nhưng code giữ đúng fence/backtick còn có bộ lọc trước synth; không khẳng định
mọi code đều được đọc nguyên. Nguy cơ vẫn tồn tại nếu nội dung bị dịch/đổi định
dạng thành prose trước bộ lọc. Công thức chưa có cơ chế tương đương. Có thêm
bằng chứng raw math cũ, chưa có phiên toán mới sau build và bỏ guard.

## Đối chiếu tài liệu kỹ thuật

ElevenLabs khuyến nghị đầu vào dành riêng cho lời nói và viết số/ký hiệu thành
từ theo cách muốn phát âm; normalization có thể giúp các mẫu thường gặp nhưng
không tự quyết định phần nào của tài liệu nên được kể. Vì vậy thay voice hoặc
bật normalization không giải quyết việc gửi cả tài liệu vào TTS.
[ElevenLabs best practices](https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices),
[symbols and language](https://elevenlabs.io/docs/help-center/product/core-capabilities/text-to-speech/why-are-numbers-dates-symbols-and-acronyms-not-properly-pronounced-or-spoken-in-the-correct-language).

W3C MathML Intent giải thích ký hiệu hiển thị có thể mơ hồ: `(a,b)` có thể là
tọa độ hoặc khoảng; cách đọc cần ý nghĩa và ngữ cảnh. Đây là tài liệu của nhóm
MathML về accessibility, không phải khẳng định đã có engine phù hợp VI/JA trong
Eris. Suy luận thiết kế: chỉ xóa LaTeX hoặc thay ký tự bằng regex không đủ để
tạo lời giảng toán đúng.
[MathML Intent explainer](https://w3c.github.io/mathml-docs/intent-explainer/).

## Các phương án

| Phương án | Lợi ích | Giới hạn |
|---|---|---|
| Nhắc writer nói ngắn hơn | Ít thay đổi | Vẫn chung nguồn panel/bubble, fallback và code loss còn nguyên |
| Thêm model tóm tắt ngay trước TTS | Có thể làm đầu vào dễ nghe | Thêm latency và một nơi quyết nội dung sau Root; có thể bỏ kết quả/điều kiện quan trọng |
| Tách tài liệu và lời thoại ở quyết định Root | Nội dung đồng nguồn, không cần thêm lượt model riêng để tóm tắt | Cần cập nhật schema, delivery, history và fallback |
| Thêm action xuất tài liệu tổng quát | Tận dụng capability/artifact/ACK hiện có; không buộc dùng solver toán | Thường cần tool-result roundtrip trước move; không tự giải quyết mọi fallback của thoại cũ |

Đề xuất hướng chính là tách ở Root. Action xuất tài liệu tổng quát là phương án
chuyển tiếp phù hợp nếu ưu tiên ít thay đổi contract; không ép bài toán nào cũng
qua `math_workspace` chỉ để có panel.

## Thiết kế đề xuất để triển khai tiếp

1. Move có tài liệu tùy chọn dưới dạng block hoặc ref đã xác thực, độc lập với
   `say`. Root quyết định lời giải/code và ý giải thích; không gửi mã nguồn qua
   hàm `_text` gộp whitespace. Tên field/schema cụ thể cần chốt khi triển khai.
2. Writer tạo bubble VI và lời nói VI/JA tương ứng với brief hội thoại. Có thể
   giảng chi tiết khi user yêu cầu; không áp hạn mức cứng 1–2 câu cho mọi tình huống.
   Presenter chỉ bố cục tài liệu Root đã giao, không tự suy ra chứng minh từ một
   câu mời xem panel.
3. Localizer chỉ nhận lời thoại. Với output có tài liệu riêng, thiếu speech
   không được fallback sang document. Nếu cần cứu lời thoại, dùng brief đã có
   của Root qua đường writer hiện tại; không cắt tùy tiện lời giải hoặc tạo
   hàng loạt câu filler rồi tính là đã đọc nội dung.
4. Tài liệu giữ nguyên công thức, code indentation và block structure. Lọc
   secret/reasoning vẫn phải có ở đầu ra tài liệu; không dùng bộ lọc bubble làm
   bộ lọc tài liệu. Có document revision/hash và ACK riêng, không suy từ synth
   success thành panel rendered.
5. Giữ bubble/stage chờ audio_start theo yêu cầu đã chốt. Tài liệu có receipt
   riêng; việc mở/cập nhật tài liệu khi nào cần quyết định rõ, không âm thầm
   thay policy text/audio. Hủy generation cũng thu hồi job dàn trang của lượt cũ.
6. History/observer lưu lời đã nói và document ref như hai loại dữ liệu. Không
   đánh dấu toàn bộ tài liệu là nội dung user đã nghe. JSON rescue/critic vẫn
   giữ quyền hiện có: không tự biến metadata repair thành lời giải mới.

Ví dụ hành vi mong muốn:

- Panel chứa bất phương trình và tập nghiệm đầy đủ.
- Eris nói: “Log yêu cầu phần bên trong phải dương. Mình đưa về điều kiện của
  cos rồi lấy khoảng giữa hai mốc trên mỗi chu kỳ; các bước ở bên cạnh.”
- Nếu user yêu cầu đọc rõ biểu thức hoặc giảng từng bước, Root chọn lời nói
  chi tiết tương ứng, viết cách đọc ra từ ngữ; không mặc định bỏ hết toán khỏi voice.

Tiêu chí kiểm chứng khi triển khai: code/công thức còn nguyên trong panel nhưng
không lọt vào request dịch của narration; missing speech không kéo document vào
TTS; JA/VI và prose recovery đều giữ tách biệt; panel ACK fail không được nhận
là đã hiển thị; preempt không để document cũ ghi đè; bubble vẫn chờ audio_start;
ghi nhận riêng tài liệu đã hiện, speech đã phát và nội dung suy luận chưa verify.

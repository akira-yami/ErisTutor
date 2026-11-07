# Eris Tutor — tích hợp chọn lọc mã nguồn mở, v1

Ngày kiểm chứng: **01/11/2026**. Bản này ghi trạng thái **source trong checkout**;
chưa xác nhận bản portable hoặc phiên Eris đang mở đã nạp các thay đổi này.

Theo yêu cầu tiếp tục triển khai sau [nghiên cứu 16 repo](2026-10-31-tutor-open-source-integration-study.md),
đã nối các cơ chế được chọn vào LearningService, TutorCapability và Rich Chat hiện có.
[Manifest nguồn](2026-10-31-tutor-open-source-sources.json) giữ commit được đối chiếu;
không có nghĩa cả 16 repo trở thành dependency.

## 1. Những ý tưởng đã thành code

| Ý tưởng tham khảo | Phần đã tích hợp | Ranh giới |
|---|---|---|
| DeepTutor: assessment thống nhất | `AssessmentAttempt`, rubric/assessor version, outcome, assistance, item/family, correction chain | LearningService và SQLite hiện có vẫn commit sự thật học tập |
| DeepTutor/Oppia: curriculum có danh tính | Domain/skill namespace, path–skill membership, prerequisite DAG, revision, exact diff, compare-and-swap | Model đề xuất; store kiểm identity, nguồn và revision |
| OATutor: gợi ý theo bậc | Gợi ý, lời giải, assistance có receipt; sau trợ giúp đề xuất thử biến thể độc lập | Làm đúng sau gợi ý không thành bằng chứng tự làm |
| Open Notebook/Docling: nguồn có phạm vi | Source span/hash/locator/version, tìm trong domain trước khi xếp hạng; adapter tài liệu tùy chọn | Ghi chú và bản tóm tắt có nhãn riêng; locator không tự chứng minh nội dung đúng |
| py-fsrs: lịch ôn thực | Dependency `fsrs==6.3.2`, Card/ReviewLog, replay UTC, shadow mặc định, lịch active tùy chọn | Recall, mastery và BKT vẫn là những ước lượng khác nhau |
| Activity package | Bài dự đoán mã Python có nút đáp án/gợi ý/lời giải trong Rich Chat | Một hoạt động hoàn chỉnh, chưa phải môi trường chạy Python tùy ý |
| Đánh giá cách dạy | Báo cáo theo policy, phase và family độc lập, có cỡ mẫu tối thiểu | Quan sát offline; chưa tự tối ưu policy hoặc chứng minh learning gain |

Không thêm agent loop, public writer, scheduler nền hay database server thứ hai.
Root vẫn sở hữu lời nói và TTS; capability trả structured advice/evidence/artifact.
Đồ thị prerequisite thuộc curriculum, không khôi phục ConceptGraphStore đã gỡ.

## 2. Attempt và bằng chứng có thể sửa

Kết quả gồm `correct`, `incorrect`, `partial`, `ungraded`; mức trợ giúp gồm
`independent`, `hinted`, `worked_with_eris`, `answer_revealed`. Hai trục này độc lập:
đúng sau khi xem lời giải vẫn là một lần làm có trợ giúp.

- Attempts có định danh bền và retry idempotent. Verdict khác cho cùng ID bị từ chối,
  phải dùng correction tường minh.
- Semantic assessment thiếu chắc chắn được giữ là `ungraded`, không tạo mastery evidence.
- Attempt/evidence và projection mastery/BKT/review/FSRS commit trong cùng transaction.
  Lỗi projection rollback cả lần làm; retry vẫn hợp lệ.
- Mastery/BKT và recall loại bằng chứng có trợ giúp hoặc partial khỏi suy luận độc lập.
  Family tương quan và cùng lượt không biến thành nhiều bằng chứng độc lập.
- Điểm mastery dùng quan sát độc lập gần nhất cho mỗi family tường minh; lặp mẫu
  đúng không làm chìm lần sai ở family khác. Stage dựa vào số lần đánh giá độc lập,
  không dựa vào số attempts có trợ giúp trong audit.
- Correction giữ thời điểm làm bài gốc; có `corrected_at` riêng. Replay không biến ngày
  sửa thành ngày vừa ôn xong.
- Forget xóa chuỗi sửa và dữ liệu tương tác liên quan, tính lại projection; tombstone
  ngăn transport phát lại cùng câu hỏi để tái tạo dữ liệu vừa quên.

Các hàng legacy vẫn giữ cách diễn giải lịch sử. Không tự gắn nhãn độc lập hay sinh
ReviewLog FSRS giả cho dữ liệu cũ thiếu metadata.

## 3. Nguồn và curriculum

Text/Markdown được nhập với SHA-256 và khoảng ký tự thật. Cùng file/cùng nội dung
nhập lại không nhân bản; thay nội dung giữ source identity nhưng làm span cũ thành
stale. Retrieval lọc domain, source được chọn và phiên bản hiện hành trong SQL
trước khi giới hạn kết quả. Root/reasoner nhận excerpt có giới hạn.

`import_notes` lưu chính tin user gốc dưới loại `user_note`. `import_source` chỉ
đọc đường dẫn tuyệt đối được user nêu trong tin hiện tại. Nội dung tham khảo tích
hợp cho bài Python mang nhãn `model_summary`, không giả làm nguyên văn Python docs.

Adapter Docling chỉ được import khi cần đọc tài liệu ngoài Text/Markdown; chỉ giữ
đoạn có provenance page/item/bbox do parser trả. **Chưa cài Docling, chưa kiểm tra
PDF/OCR thực trong vòng này.** Thiếu parser trả unavailable/error cụ thể.

Auto-path dùng domain và skill ID có namespace. Có thể yêu cầu học chủ đề mới qua
`start_path`; dùng yêu cầu user gốc và planner hiện có, không tạo curriculum giả khi
model không sẵn sàng. Nếu path ID đã tồn tại thì tiếp tục bản đã lưu, không ghi đè
bằng đề xuất model mới. Attempts của môn trước vẫn được giữ.

Sửa curriculum đi qua `preview_curriculum` rồi `revise_curriculum` cùng
`base_revision`. Backend trả diff xác định, từ chối revision cũ, cycle và prerequisite
không tồn tại. Thêm mục tiêu cần nguồn còn hiệu lực trong cùng domain. Đổi thứ tự
giữ skill ID; đổi nghĩa kỹ năng phải tạo ID mới. Shared skill không bị ghi đè ngầm
qua một path khác.

## 4. FSRS hoạt động đến đâu

Đã cài và kiểm tra thư viện thật **6.3.2 trong Python phát triển**. Khai báo extra:

```powershell
python -m pip install -e ".[tutor]"
```

Mặc định `ERIS_LEARNING_RETENTION_MODE=shadow`: lưu Card/ReviewLog dự phóng song
song, lịch ôn hiện hành vẫn do heuristic sở hữu. Chỉ những recall được đánh dấu
tường minh, có kết quả chấm và tự làm mới được replay. Sai luôn tương ứng `Again`;
`Hard` chỉ dành cho nhớ được nhưng khó. Tắt fuzz, giữ UTC và card identity ổn định.

Khi chọn `ERIS_LEARNING_RETENTION_MODE=fsrs` trước khi khởi tạo runtime, due time
và retrievability lấy từ card đủ điều kiện. Skill chưa có card, thiếu dependency
hoặc sai version vẫn dùng heuristic; snapshot báo `scheduler_owner` thực tế.
Thay chế độ không tự sinh thêm lần ôn. Không chuyển stability heuristic cũ thành
tham số FSRS đã fit. Chưa tối ưu tham số cá nhân hoặc chứng minh retention tốt hơn.

Bài Python v1 là practice, không tự coi mỗi cú chọn đáp án là recall để nuôi FSRS.
Recall trong luồng ôn chat đi qua TutorExecutive/attempt chung.

## 5. Hoạt động Python trong Rich Chat

Ba preset: `python.range`, `python.accumulator`, `python.filter`. Mỗi preset sinh
biến thể từ template đã viết, xoay vị trí đáp án và tránh lặp ngay bài vừa thấy.
Khóa đáp án chỉ giữ ở backend. Không dùng `exec` với code do model/người học cung cấp.

Nút trong artifact gửi user event qua đường `api/send` sẵn có. Backend đọc lệnh
`ERIS_LEARN` từ event thật; bỏ qua `answer`/`correct` do Root tự gán. Kiểm activity,
nonce, session, receipt giao bài, thời hạn và revision trước khi chấm. Gợi ý/lời
giải chỉ đổi assistance khi có delivery receipt thành công. Bài stale không được
đánh giá lên curriculum mới.

Các mẫu cùng family không cộng dồn mastery chỉ vì người học mở đi mở lại.
Sau trợ giúp, kết quả ghi đúng mức hỗ trợ và đề xuất một biến thể tự làm tiếp theo.
UI có trạng thái gửi/lỗi/nộp bài, chặn nộp lặp tại client; backend vẫn dedupe độc lập.
Đã gắn version tài nguyên JS/CSS để tránh cache giữ UI cũ sau cập nhật.

Ví dụ yêu cầu tự nhiên cho Root sau khi chạy source mới:

- “Cho tôi một bài dự đoán vòng lặp Python.” → `activity`, preset `python.range`.
- Bấm “Gợi ý thêm một bước”, sau đó chọn đáp án → `activity_event` từ user event.
- “Tôi muốn bắt đầu học phân số.” → `start_path`, planner dựa vào tin user thật.
- “Học từ file C:\\...\\lesson.md.” → `import_source` nếu đường dẫn user nêu hợp lệ.
- “Cho xem kết quả đã làm/lịch ôn.” → `attempts` / `retention`.
- “Lần này Eris chấm sai.” → xem attempt rồi `dispute_attempt` đưa về ungraded;
  không tự đổi thành đúng. Xóa attempt chỉ khi user yêu cầu.

Tool guide có contract chi tiết. Những ví dụ trên mô tả khả năng đã nối vào source,
không phải transcript live với model/TTS.

## 6. Đánh giá cách dạy và phần hoãn

Báo cáo dùng tối đa 500 attempts hiệu lực gần nhất, nhóm theo teaching policy,
lọc tự làm, dedupe item family; chia practice/transfer/review. Tỷ lệ thành công
mỗi phase chỉ hiện khi có ít nhất 5 mẫu. Không bịa response time/cost thiếu dữ liệu;
luôn ghi thiết kế observational, chưa có bằng chứng cải thiện nhân quả.

**Chưa tích hợp MathLive, JSXGraph, Pyodide, Sandpack, deep KT hoặc DSPy vào
runtime.** Chưa có Learning Universe đa môn hoàn chỉnh, đồ thị tương tác hay
automatic policy optimization. BKT vẫn shadow chưa calibrated. Các mục này giữ
trạng thái nghiên cứu; `readiness` chỉ báo module thực có.

## 7. Kiểm chứng

Kiểm tra trên source checkout, SQLite/artifact dùng dữ liệu test riêng:

| Kiểm tra | Kết quả |
|---|---|
| Tutor/learner/kernel/workspace và proactive integration | **197 passed, 1 deselected, 44.91s** khi tách race đã biết; lần đầy đủ cuối **197 passed, 1 failed** |
| Trong đó bộ mới `test_tutor_learning_integration_v1.py` | **33 passed** |
| Node UI contract/escaping/untrusted source | **3 passed** |
| Ruff các Python file được sửa; `git diff --check` | Pass |
| `node --check` hai file JS được sửa | Pass |
| `dotnet build Eris.csproj --no-restore -v:quiet` | **0 warning, 0 error**, 7.98s |
| UI browser: chọn gợi ý rồi đáp án | Gọi capability/backend thật trong fixture; kiểm attempt có assistance `hinted` |
| UI responsive | 1280px và 390×844; ở 390px document width=390, nút chọn cao ~49.2px |

Lệnh nhóm Python 198 test:

```powershell
python -m pytest src/tests/test_tutor_learning_integration_v1.py src/tests/test_root_orchestrated_learner_v1.py src/tests/test_long_term_learner_v2.py src/tests/test_tutor_executive_v1.py src/tests/test_tutor_model_reasoner_v1.py src/tests/test_learn04_adaptive_teaching.py src/tests/test_learn05_spaced_repeat.py src/tests/test_learning_kernel_boundary_v4.py src/tests/conversation/test_proactive_human_design.py src/runtime/learning_core/tests src/tests/workspace -q --tb=short
node --test src/tests/ui/learning_artifacts.test.cjs
```

Một lần chạy nhóm có test workspace
`test_resume_chi_sau_khi_nguoi_dung_tra_loi` thấy `running` thay vì `queued`;
chạy riêng pass, lần trước thay đổi chấm family chạy toàn nhóm **197 pass**.
Sau bổ sung test chấm family, lần đầy đủ cuối **197 pass, 1 fail** vẫn đúng race đó.
Code `ParallelWorkService.resume`
submit worker trước khi chụp trạng thái trả về, nên có cửa sổ race. Không sửa cơ chế
parallel work ngoài phạm vi Tutor trong vòng này; không coi test này ổn định tuyệt đối.
Lần xác nhận cuối giữ nguyên source và tách đúng test đó bằng
`--deselect=src/tests/workspace/test_parallel_work_reconstruction.py::test_resume_chi_sau_khi_nguoi_dung_tra_loi`:
**197 passed, 1 deselected in 44.91s**. Đây là phạm vi chạy được ghi rõ, không phải
tuyên bố đã sửa race hoặc cả 198 test cùng pass.

Lần quét rộng trước hai test start_path mới: **697 passed, 6 failed**.
Đã tái hiện đúng sáu lỗi trên snapshot HEAD gốc
`a6d7526db35e20366e677db2823872f3d1c18321`: **106 passed, 6 failed** trong bốn file
action claim/character memory. Các lỗi cụ thể:

- `test_action_claim_integrity_v12.py`: `test_writer_manifest_empty_does_not_bypass_action_guard`,
  `test_writer_allows_effect_supported_by_runtime_registry`.
- `test_action_claim_integrity_v2.py`: `test_writer_checks_target_independent_of_manifest`,
  `test_writer_checks_tts_japanese_action_type`.
- `test_character_evidence_v1.py`: `test_public_memory_text_and_records_share_one_selection`.
- `test_character_public_claim_gate_v1.py`: `test_empty_manifest_does_not_turn_unverified_action_into_phatic`.

Baseline lưu ở `%LOCALAPPDATA%/Temp/eris-tutor-baseline-37932131da/result.txt`.
Không kết luận toàn repository xanh. Snapshot baseline ban đầu nằm trong scratch
làm .NET compile trùng C#; đã chuyển riêng snapshot test sang Temp và build lại
thành công, không sửa project để che lỗi.

Ảnh UI: [màn hình rộng](../../scratch/tutor-ui-qa/wide.png),
[màn hình hẹp](../../scratch/tutor-ui-qa/narrow.png),
[gợi ý → đáp án → phản hồi](../../scratch/tutor-ui-qa/feedback.png). Browser fixture dùng Rich Chat,
LearningService, TutorCapability và delivery giả lập có kiểm soát; **không phải
phiên model/TTS/Godot live**, không dùng dữ liệu học thật của user.
Tab và server preview tạm đã đóng; ảnh cùng SQLite kiểm thử giữ ở scratch để đối chiếu.

Chưa export/deploy portable hoặc chạy hội thoại/voice end-to-end với model thật.
Các thay đổi Inner Diary/transport/credentials có sẵn hoặc phát sinh đồng thời trong
workspace không thuộc phần Tutor này. Không stage/commit/revert chúng.

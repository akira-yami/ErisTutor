# ERIS Tutor P1 — đa môn, bài thực hành và kiểm tra luồng (01/11/2026)

## Nền tảng & thứ tự cài

- Đây là **ZIP change-only trên nền ZIP source `eris-ai-20261101_104004_040.zip` đã áp dụng P0**.
- **Cài gói `ERIS_Tutor_v1_P0_FIX_change_only_VI.zip` trước**, sau đó áp dụng gói P1 này.
- Chép thư mục `eris/` của ZIP vào thư mục code Eris; chỉ ghi đè các file trùng tên.
- Sao lưu source và dữ liệu người học trước khi cài. Không xóa hoặc chép đè `mentor/learning.sqlite3`.
- Cần build và chạy lại Eris trên Windows; P1 không phải bản portable đã biên dịch.

## P1-01. Multi-Domain Learning Spaces

**Có thật trong code:** `LearningService.learning_spaces()` liệt kê domain/path đã lưu, số điểm học, con trỏ đang học và curriculum revision; `switch_learning_space(path_id)` đổi path **không reset tiến độ**. Trạng thái nằm trong SQLite store LearningService có sẵn (không thêm database thứ hai).

Root có operation `tutor: spaces` và `tutor: switch_path,path_id`. `switch_path` chỉ nhận ID path tồn tại và tên hoặc ID được người dùng nhắc trong tin nhắn hiện tại, tránh tự đổi môn vì quyết định đoán mò của model.

Auto-path khi đã học môn A chỉ cân nhắc mở môn B nếu user có ý định học được nhận biết rõ. Tin “Dạy tôi lại” là tiếp tục, **không tạo lộ trình mới**. Nếu planner trả lại path đã tồn tại, không ghi đè curriculum, chỉ resume. Nguồn path mới vẫn phải qua TeachingPlanBuilder hiện có.

**Chưa có:** bản đồ tri thức vô hạn, tự động liên kết skill đa lĩnh vực, visual Web Learning Universe, công cụ tự sinh bất kỳ môn nào. `spaces` là API cho panel web tương lai.

## P1-02. Thêm 3 preset minh họa, không nhận bừa đáp án từ model

Ngoài 3 preset Python cũ:

| Preset | Giao diện | Backend chấm | Ảnh hưởng mastery |
|---|---|---|---|
| `math.fractions` | Trắc nghiệm phân số | Khóa đáp án deterministic | Có khi đủ điều kiện bằng chứng |
| `math.linear` | Ô nhập số nguyên | Kiểm tra số nguyên với nghiệm được tính từ seed | Có khi đủ điều kiện bằng chứng |
| `writing.explanation` | Ô nhập lời tự giải thích | **Không tự chấm** (`ungraded`) | **Không tăng mastery** |

Các bài học vẫn đi qua `LearningActivities → ArtifactBundle → Root delivery receipt → Rich Chat → user event ERIS_LEARN → LearningService.record_attempt`. Backend giữ khóa đáp án, hints và lời giải; payload artifact công khai chỉ có ID/nonce/revision/options/response_mode cần thiết. Giữ contract Python choice v1.

Câu trả lời mở được gửi trong user event dạng base64url UTF-8, giới hạn 3000 byte; không nên coi đây là mã hóa bảo mật. Bản ghi attempt chỉ giữ hash tham chiếu (`answer_ref`) và kết quả, không lưu lại nội dung tự luận nguyên văn ở bảng attempt. UI giữ bản nháp trong RAM, không ghi vào localStorage. Tự luận hiện chưa có chấm rubric bằng model/human.

**Chưa có:** MathLive, JSXGraph, Pyodide, Sandpack, code runner thực, chấm tự luận ngữ nghĩa, luyện ngoại ngữ hoặc các widget chuyên môn khác. `python_exec` vẫn false; bài Python tiếp tục là bài đọc/dự đoán mã, không chạy code người dùng.

## P1-03. Kiểm thử tích hợp thay vì tuyên bố live E2E

Đã bổ sung test với một Root router giả lập có điều khiển, dùng `RuntimeModelRootAgent`, `TutorCapability`, `LearningActivities`, Artifact validator, `LearningService`, SQLite và receipt do test tạo. Bộ test kiểm tra Root mang bundle đúng, Tutor không tự phát thoại, hoạt động chưa được giao không được chấm, bài làm gửi rồi được chấm, duplicate bị bỏ qua. Node test xác nhận renderer chỉ tạo control cho artifact đáng tin và không đưa khóa đáp án ra UI.

**Chưa xác minh:** Gemini/DeepSeek model live, giọng nói/TTS/Live2D, `dotnet build` hay portable Windows, trình duyệt thật. Những phần này phải thử trên máy chạy Eris trước khi đánh dấu P1 production-ready.

## Lệnh chạy test (tại thư mục `eris/`)

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q src/tests/test_tutor_learning_p1_domains.py src/tests/test_tutor_learning_integration_v1.py src/tests/test_root_orchestrated_learner_v1.py
node --test src/tests/ui/learning_artifacts.test.cjs src/tests/ui/learning_artifacts_p1.test.cjs
```

Nếu môi trường thiếu FSRS, các bài test phụ thuộc thư viện sẽ lỗi (đã đề cập ở P0). Không tự sửa test để che lỗi. Cần chạy lại với FSRS thật trên môi trường build portable Windows.

## Kiểm thử thủ công với Eris thật

1. Yêu cầu Eris “Cho tôi một bài phương trình Toán.” Xem artifact có ô nhập số.
2. Nhập nghiệm và bấm Nộp bài. Xác nhận phản hồi và attempt chỉ xuất hiện sau receipt thực sự đã giao bài.
3. Yêu cầu “Cho tôi bài tự giải thích”; nhập văn bản; xác nhận result = `ungraded` và mastery không tăng.
4. Yêu cầu “Liệt kê các môn đang học”, sau đó “Quay lại [tên môn]” và xác nhận tiếp tục đúng vị trí sau restart.
5. Đọc/hỏi/gợi ý khi bài chưa giao hoặc khi có kết quả quá hạn: backend phải từ chối, không tự ghi thành công.
6. Xác nhận Eris vẫn phát lời qua Root, không để Tutor tự nói thành một lượt mới ngoài điều phối.

## Thay đổi dựa trên P0

Các file `service.py` và `capabilities/tutor.py` trong ZIP P1 **giữ nguyên các fix P0** về lưu con trỏ và receipt correlation. Không áp dụng P1 lên nguồn cũ trước P0 nếu chưa đối chiếu diff.

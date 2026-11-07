# Eris — P3 Elegant Social Intelligence (05/11/2026)

## Mục tiêu

Triển khai nâng cấp **P3 trên nền EQ P0–P2**. Eris có cá tính riêng và một Root
quyết định, không có EQ agent / vòng LLM phụ / state-machine “buồn → an ủi”.
Dùng sự tương đồng có chứng cứ, các lượt đối thoại thật, lời user sửa cách
tham gia, và identity/affect đã tồn tại để **Root tự chọn** câu trả lời hoặc im.

## Thay đổi trong code

1. **Contextual Social Recall** — `src/runtime/conversation/social_situation.py`
   - Truy hồi kinh nghiệm đã phát ra, có phản ứng user, có observer hậu kỳ với
     `confidence >= 0.62`, provenance `post_turn_observer` và session ID.
   - So sánh từ ngữ của câu hiện tại với `situation`, `goal`, `reaction_text`
     trước đó. Loại từ nối thông dụng, giới hạn dữ liệu, không embedding/LLM,
     không tạo “điểm EQ” hay độ chắc chắn tâm lý.
   - Ưu tiên tình huống có từ chung cụ thể hơn là cái mới nhất. Khác phiên cần
     ít nhất hai từ chung đủ điều kiện; không liên quan thì không lấy. Nếu không
     có match, tối đa **một** fallback cùng phiên, đánh dấu rõ để Root xem nhẹ.
   - Khi user đã **tường minh khép chủ đề**, không gợi fallback lạc chủ đề.
     Cả dữ liệu khác phiên lẫn fallback đều không trở thành sở thích vĩnh viễn.

2. **Conversational Continuity** — cùng module
   - Thêm `social_situation_evidence.conversation_flow.recent_turns`, tối đa
     bốn lượt public có phạm vi cùng phiên, theo đúng thứ tự. Tin user hiện tại
     không bị lặp, tin assistant đang chuẩn bị/queue/delivering/lỗi hoặc không
     có xác nhận `delivered` **không được giả vờ là lời đã nói**.
   - Timeline chỉ cung cấp **bằng chứng**; không viết chủ đề/cung hội thoại/
     hành động tiếp theo vào state, tránh tranh quyền Root.

3. **Social Self-Correction** — cùng module + `root_prompt_contract.py`
   - Nếu handoff lượt hiện tại có `repair_needed`, chuyển tiếp một
     `social_repair_evidence` có lời user và ref lời Eris trước (nếu có).
   - Root có cơ sở sửa **cách tham gia**, không tự nhận mọi lỗi, không bắt buộc
     xin lỗi, không tự cấm hỏi các tình huống về sau. Handoff cũ không thể đóng
     vai phản hồi lượt mới.

4. **Natural Initiative** — `root_prompt_contract.py`, `agent.py`
   - `ELEGANT_SOCIAL_PROMPT` hướng Root chỉ chủ động/nối lời khi có ý mới thực
     sự đáng nói và chứng cứ thích hợp; im lặng là một lựa chọn tự chủ, không
     có quota phát biểu, không cố nhử câu hỏi.
   - Không tạo scheduler, planner, timer, wake hay model call mới. Autonomy và
     continuation vẫn do Root / Identity Core hiện có điều phối.

5. **Personality Consistency** — cùng prompt
   - INFJ/Ni–Fe là định hướng xây nhân vật, không là bộ quy tắc cảm xúc.
     Sự tò mò, thẳng thắn, Fe và lòng tự trọng ảnh hưởng **mục đích hành động**,
     không phải chỉ một lớp ngôn từ mềm mỏng. Khi mục đích user đổi thì chính
     cá tính có thể chọn hành động khác mà vẫn nhất quán.

## Thay đổi kỹ thuật / tương thích

- Không thay schema `NextConversationMove` / action JSON, không thay quyền
  chọn move/no_reply, không bật `ERIS_ROOT_INTERACTION_CHOICES`.
- `social_situation_evidence` lên schema `eris.social_situation.evidence.v2`,
  nhưng vẫn giữ các key cũ: `authority`, `previous_assistant`, `current_handoff`,
  `past_interactions`, `past_interactions_scope`.
- Thêm key tùy chọn: `conversation_flow`, `social_repair_evidence` và
  `current_handoff.explicit_topic_closure`; mỗi example có `match_basis` và
  `shared_terms`.
- Không ghi memory mới, không lấy dữ liệu chưa có receipt để tự học, không
  lưu thêm transcript hay thay đổi logic StateReducer / MemoryKernel.
- Thuật toán match theo từ vựng có giới hạn; tình huống cùng ý nhưng khác từ
  có thể không tìm được. Fallback cũng có thể không liên quan. Root phải tự
  xét, không biến match thành lệnh, ưu tiên yêu cầu mới của user.

## Kiểm thử thực hiện

- **38/38** test trọng tâm đạt, gồm 7 test P3 trong
  `src/tests/conversation/test_elegant_social_p3.py` và regression P0–P2.
- Bộ regression rộng được chạy: **605 pass, 7 fail**. Cả 7 ca đều cùng tên
  với các lỗi đã có trong bản P0–P2 trước khi làm P3: action claim guard,
  memory degraded, public claim gate và file `body/UdpListener.SettingsDanger.cs`
  không có trong ZIP nguồn.
- `compileall` trên Python sửa: đạt.
- Không chạy A/B live với model/voice/GUI, không kết luận đã cải thiện EQ thật
  chỉ từ unit test. Đề nghị kiểm chuyện Kiro, chủ động tán gẫu, chuyển từ kể
  sang debug, nói “thôi” và phản đối câu Eris vừa nói; so từ hai lượt trở lên.

## Áp dụng

**Bản change-only P3:** chỉ áp lên source đã có **P0–P2** của đợt trước.
Giải nén đè vào **thư mục cha chứa `eris`** (file trong ZIP bắt đầu `eris/`).

**Bản full:** source ZIP **P0–P2 + P3** hoàn chỉnh; không cần cài lần lượt.

Kiểm thử trong `eris` với `PYTHONPATH=src`:

```powershell
$env:PYTHONPATH="src"
python -m pytest -q src/tests/conversation/test_elegant_social_p3.py src/tests/conversation/test_social_judgment_root_v12.py
```

Sau áp patch, rebuild/sync bản portable theo workflow dự án rồi restart.

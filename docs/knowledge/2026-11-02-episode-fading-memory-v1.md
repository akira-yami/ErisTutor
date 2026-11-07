# Episode mờ dần: triển khai v1 — 02/11/2026

## Phạm vi và trạng thái

Đã triển khai trong source theo yêu cầu tiếp sau
[đánh giá thiết kế](../../.agents/tasks/eris-token-growth/episode-fading-design-review.md).
Không chạy migration/GC trên memory thật, không copy portable, restart hay deploy.
Source mới tự đọc schema episode v1; lần ghi tiếp theo lưu schema v2 có archive.
Không khôi phục được nguồn đã mất từ trước thay đổi này.

## Hành vi mới

- LivingUserMemoryService sở hữu EpisodeRollupStore; MemoryKernel.maintain gọi
  rollup trên worker maintenance hiện có, độc lập cờ auto_dream_on_maintenance.
  Không thêm agent loop; summary/compile_context/Root đọc projection, không gọi AI.
- GeminiWebUserProfileAdapter có hợp đồng summarize_episode_rollup riêng. Input
  gồm previous_segments đã lưu + episode mới, không gửi lại toàn kho đã bao phủ.
  Các đoạn có anchor lịch bền vững, render thành một đoạn văn có thời gian mờ.
- `living_user_episode_rollups.json` lưu entry theo exact scope: narrative
  segments, revision, prompt version, authority fingerprint và coverage
  episode_id→content_hash cùng một lần atomic replace. Restart không gọi lại
  model nếu không có delta. Single-flight trong process; khóa nguồn/file được
  nhả khi chờ model. Khi commit kiểm lại source hashes, revision và authority.
- Episode cùng ID đổi nội dung làm mất hiệu lực recap cũ. V1 dựng lại recap
  của scope đó từ nguồn hợp lệ theo batch, không nối thêm ý lên diễn giải sai.
  Timeout/output sai/write failure không đánh dấu đã xử lý; retry sau 300 giây.
- Default tối đa 4 episode mới trong 7 ngày; recap tổng tối đa 600 đơn vị budget,
  episode mới + recap tối đa 3.000. Batch 8 episode, tối đa 1 inference rollup
  mỗi lượt maintenance. Checkpoint phải yên ít nhất 1 giờ trước khi rollup.
  Query recall vẫn có thể chọn tối đa 2 nguồn cũ liên quan trong budget.
- **Budget v1 dùng số UTF-8 bytes như cận bảo thủ cho byte-level tokens**, không
  dùng chars/3 và không gọi tokenizer/network ở đường đọc. Vì vậy có thể ngắn
  hơn 600/3.000 token thực; không phải phép đo usage/billing của gateway. Phần
  text model có reserve cho nhãn thời gian. Episode không vừa bị bỏ cả record,
  không cắt ngang summary. Đây là budget phần episode, không phải toàn Self.
- Timezone mặc định Asia/Ho_Chi_Minh, chỉnh qua ERIS_MEMORY_TIMEZONE; Windows
  thiếu tzdata fallback UTC+7. Anchor tuần được chặn ở đầu tháng, tránh gán sự
  kiện 01/11 thành tháng 10. Đổi nhãn thời gian không gọi Gemini.

## Nguồn gốc và quyền sửa/quên

- Episode store v2 lưu active, archive, bản trước merge và tombstone trong cùng
  file. Trim giữ kho active tối đa 240 nhưng chuyển phần dư vào archive; compact
  legacy giữ snapshot trước gộp. Search đọc cả archive; versions(id) đọc nguồn
  trước merge. Thay đổi ghi lỗi rollback state trong memory; source JSON hỏng
  fail closed, không âm thầm mở kho rỗng rồi ghi đè dữ liệu cũ.
- Khi bật rollup, dream không gọi compact gộp nội dung nữa: chỉ archive_cold giữ
  nguyên nguồn/hash. Nếu không, compact sau rollup có thể vô hiệu recap vừa lưu.
- Correction/soft-forget/move-scope loại diễn giải episode cũ khỏi prompt/recall,
  giữ bản gốc trên đĩa. Hard-forget purge cả active/archive/history liên quan.
  Recap liên quan và proposal profile còn pending được scrub; tombstone hash
  evidence chặn job cũ nhập lại nguồn dưới ID mới. Nguồn legacy thiếu provenance
  vẫn có giới hạn liên kết; không suy đoán lời gốc đã mất hoặc cam kết archive
  v2 khôi phục được toàn lịch sử trước đây.
- Không trộn project/private vào recap global; private/sensitive episode không
  vào rollup mặc định. Open loops giữ trạng thái trên nguồn; recap không trở
  thành receipt hoàn tất hay evidence mới để tự reinforce profile.

## Root và hai consumer distill khác

- service.summary và _read_self dùng episode projection mới, bỏ luồng 8 episode
  mặc định cũ. Root đọc episode view từ facade hiện tại dù host snapshot còn cũ.
- **Episode view không nằm trong bootstrap Self bất biến.** Mỗi request nhận
  đủ recent_episodes + episode_recap trong overlay hiện tại. Nhờ vậy chi tiết
  đã mờ/quên không còn kẹt trong prefix cũ. Không thêm warm-up/inference khi
  episode già đi; profile/MindState vẫn dùng bootstrap + cumulative overlay.
  One-shot vẫn nhận projection hiện tại. Recap trùng trong memory_context được
  bỏ nếu đã có chính nội dung đó trong Self; source recall vẫn giữ được.
- Dreamer học profile có checkpoint riêng `living_user_dream_checkpoint.json`.
  Coverage theo nội dung, lưu proposal pending trước apply; crash thì replay
  proposal cũ qua authority guard, không gọi lại AI. Replay kiểm source còn đúng;
  scrub pending cũng bỏ coverage pending để không bỏ mất học nguồn chưa apply.
- MemoryAICurator của kho episodic khác ghi tag distilled + distilled_hash sau
  semantic commit accepted/tentative; quyết định skip cũng được lưu với nhãn
  riêng. Chỉ mark tối đa 8 nguồn thực sự vào prompt, không mark phần cluster
  model chưa đọc. Check nguồn trước semantic commit; nội dung đổi mở lại consumer.
  Access-count save không xóa checkpoint nếu source vẫn giống hệt.
- Semantic commit và mark nguồn của consumer này vẫn là hai file khác nhau:
  crash đúng giữa hai bước có thể retry distill. Không tuyên bố exactly-once
  toàn hệ hoặc transaction phân tán. Recap có summary+coverage nguyên tử; Dreamer
  có pending replay. Lock là trong process, không phải khóa nhiều process.

## Kiểm chứng

- Bộ mới `src/tests/test_episode_fading.py`: **35 test pass**, nằm trong lần chạy
  nhóm rộng cuối **191 passed, 4 failed, 9.64s** trên 18 file. Kiểm cả request
  Root thật được projector dựng với fake router: chi tiết chỉ còn ở overlay
  khi mới, biến khỏi request sau rollup, prefix không đổi, không thêm warm-up.
- Các case gồm restart/only-delta, backfill, cùng ID đổi nội dung, timeout/parse/
  write failure, concurrent source update và single-flight không giữ source
  lock lúc inference, scope, thời gian qua tháng/năm, archive/trim/compact,
  hard-delete + late reimport, proposal pending/replay/forget và consumer semantic.
- Bốn lỗi đều tái hiện trên package memory cô lập từ **24614d18** trước các thay
  đổi episode: hai lỗi admission/selection trong test_living_user_memory_v39,
  competence context trong test_companion_memory_authority_consolidation, và
  source-label UI trong test_memory_hygiene_v41. File UI liên quan có hash trùng
  baseline. Không sửa ngoài phạm vi để làm xanh suite.
- Ruff trên 15 module/test liên quan và git diff --check pass. Một ít cleanup
  import/zip/setattr trong các file được chạm không đổi chính sách memory.
- Hai phép thử Gemini service chỉ dùng dữ liệu giả và thư mục temp; chưa commit
  recap nào. Lượt chẩn đoán nhận **CuratorInferenceError: timeout**; lượt tiếp
  theo deferred và không advance coverage. [Kết quả smoke](../../.agents/tasks/eris-token-growth/episode-fading-gemini-smoke.json).
  Chưa xác nhận chất lượng ngữ nghĩa nhiều vòng của Gemini thật, token request
  portable sau triển khai hoặc cải thiện latency live. Test model giả không
  thay cho những bằng chứng này.

Feature mặc định bật trong source; opt-out ERIS_LIVING_USER_EPISODE_ROLLUP=0.
Không rollback về binary v1 rồi kỳ vọng nó tự đọc archive v2; bản gốc vẫn ở
trong file nhưng reader cũ chỉ biết trường episodes. Chưa triển khai bản cài.

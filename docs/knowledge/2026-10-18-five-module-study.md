# Nghiên cứu năm module Eris

Ngày: **18/10/2026**. Phạm vi: checkout `Documents/GitHub/eris`, source,
call site và kiểm tra offline. Đã đọc [bài học runtime](2026-10-14-eris-runtime-lessons.md).
Không chạy provider, browser extension, Godot hay audio live; không sửa runtime.

## Bản đồ

| Thư mục | Trách nhiệm | Điểm nối |
|---|---|---|
| `src/domain` | Contract quyết định/output dùng thật | Parser, coordinator, public output, state commit |
| `src/evals` | Golden replay Mentor offline | `EngineeringMentorService` |
| `src/experiments/emergence` | Kiểm chứng ảnh hưởng lịch sử, ablation, persistence | `cortex.subject`, `memory.subject` |
| `src/experiments/v57_simulation` | Harness hội thoại qua runtime/provider thật khi được chạy | `RuntimeCoordinator`, `LlmService`, behavioral judge |
| `src/integration/body_contracts` | Bản C# đối chiếu contract/test | Bị loại khỏi build trong `Eris.csproj` |
| `src/integrations/chesscom_bridge` | Extension gửi bàn cờ về Brain | `runtime.chess`, HTTP local |

Không tìm thấy caller production ngoài package/test cho hai harness experiments
qua tìm kiếm Python trong repo. Điều này không loại trừ caller thủ công hoặc
script ngoài repo.

## 1. domain

`__init__.py` export `Decision`; `decision.py` định nghĩa dataclass có slots.
Các nhóm field gồm text/display/speech, animation/motion, performance/nonverbal,
system action/capability, action result, failure, turn ID, director/final-speech
lock/digest, opening/autonomous plan và behavior observation.

`resolve_motion_*` chuyển animation/target cũ sang contract motion. Ví dụ target
0.4 tạo `motion_intent="walk_to"`. `_payload_text` biến placeholder như `...`,
`hm`, `um` thành output im lặng. `to_payload()` chọn field gửi Body; debug chỉ
được đưa ra khi `ERIS_EXPOSE_DEBUG_PAYLOAD` bật. Không phải mọi field đều ra wire.

Điểm nối đã đọc: `parser_grounding/service.py` tạo `Decision(**finalized)`;
`runtime/public_output_runtime.py` và `runtime/state_commit_service.py` gọi
`to_payload()`. Nhiều coordinator/runtime adapter import trực tiếp.

Đây là contract production, phạm vi gồm cả nghiệp vụ, biểu diễn và tương thích
lịch sử. Field lock/digest chỉ mang trạng thái; dataclass không tự thực thi khóa,
dispatch TTS hoặc xác nhận playback. Chữ chờ voice thuộc public-output runtime.

## 2. evals

`run_mentor_eval_v85.py` đọc `mentor_golden_v85.json` gồm 20 ca. Mỗi ca tạo
Mentor trong TemporaryDirectory, nạp coding-flow giả/lịch sử user, gọi
`prompt_context` và `snapshot`, so `relevant`, `mode`, `primary_role`.
Kết quả còn ghi `prompt_present`. Không gọi model/provider.

Scenario bao gồm học code, review, sửa gấp, lặp sửa mù, flow thiếu inspection/
verification, user bực, chủ đề ngoài code và flow đã cũ.

**Chạy lại: 2 đạt, 18 lỗi. Cả 18 chỉ khác `primary_role`; relevant/mode vẫn khớp.**

Nguyên nhân đã đối chiếu: `engineering_mentor.py` dòng 927, `prompt_context`
và `snapshot` chủ động giữ `_last_role_directive` inactive, ghi rõ V84 role
router đã rời active path. AdaptiveCompanion sở hữu stance/delivery; Mentor
cung cấp evidence. Golden vẫn yêu cầu Mentor trả mentor/supervisor/companion.

Kết luận này thay thế kỳ vọng cũ của golden rằng Mentor còn chọn role.
18 lỗi không tự chứng minh production Mentor hỏng; bộ eval đang lệch ranh giới
evidence-provider/delivery-owner. Test Mentor mới hơn mô tả delivery qua Companion.

## 3. experiments

### emergence

Đo ảnh hưởng nhân quả của lịch sử lên lựa chọn/state, thay vì chấm văn phong.

| File/nhóm | Công việc |
|---|---|
| `runner.py`, `paired_instances.py`, `interventions.py` | Tạo control/treatment cùng seed, nạp lịch sử khác nhau, chạy probe, so ngưỡng |
| `subject_instance.py` | Adapter chạy SubjectService thật với clock/seed deterministic và outcome do scenario cung cấp |
| `metrics.py` | AUROC, Brier/log loss, effect size, ICC, retention, transfer và tổng hợp |
| `acceptance.py`, `scenarios/*.yaml` | Ngưỡng và mô tả persistence/counterfactual/resistance/transfer/ablation |
| `ablation.py` | Clone SQLite bằng backup API, tombstone event, loại hậu duệ nhân quả, dựng projection nghiên cứu, đo target/collateral |
| `soak.py`, `repository_soak.py` | Lượt mô phỏng, mở lại DB, hash, event chain, kích thước projection và dấu hiệu lẫn subject |
| `report.py`, `validation.py` | JSON/Markdown và tổng hợp các gate caller cung cấp |

Holdout buộc ít nhất 20 seed và 5 repetitions/probe; development cho mẫu nhỏ.
SubjectService thật không biến outcome scenario thành hành động quan sát ngoài đời.

Các giới hạn đã xác định:

1. **Guard reuse giữa split không hiệu lực khi đổi split.** Fingerprint chứa
   chính `split`, trong khi runner dùng fingerprint để phát hiện reuse qua split.
   Probe offline: cùng nội dung, 20 seed, 5 repetitions, chỉ khác development/
   holdout → hash khác, runner trả hai report đạt. Kết luận này thay thế cam kết
   chống reuse trong docstring hiện tại của `run_benchmark_suite`.
2. Adapter trả sẵn `authorship_integrity=1`, `causal_replay_exact=1`,
   `cross_instance_leakage=0`; vài effect metric lấy được từ payload. Đây không
   phải phép đo độc lập. `initial_snapshot` được giữ nhưng adapter không nạp nó.
3. `RepositoryBackedSoakInstance.verify_replay()` so hash trước/sau mở lại DB;
   chính hàm này không xóa projection rồi rebuild từ toàn event log. Nó trực tiếp
   chứng minh reopen/persistence, chưa đủ gọi là replay toàn diện độc lập.
4. Soak dùng lượt/ngày mô phỏng; leakage check tìm ID subject khác trong JSON,
   nên phạm vi phát hiện hẹp. Không phải chạy liên tục bảy ngày ngoài đời.
5. Không thấy loader YAML nối trực tiếp vào runner. Validation cho phép thiếu
   ablation/soak; caller phải quy định gate nào bắt buộc khi xét release.

### v57_simulation

Luồng: `Scenario → LiveSimulationRunner → LiveRuntimeHarness → RuntimeCoordinator
→ CaptureTransport/TurnArtifact → deterministic checks + BehavioralJudge → report`.

Harness dựng RemoteModelProcessManager, LlmService, coordinator thật; thay UDP
Body bằng bộ thu payload và desktop telemetry bằng snapshot kiểm soát. Memory,
subject, workspace trỏ vào run root riêng. TTS và text-audio sync bị tắt.

Scenario có correction/repair, goal bị ngắt rồi tiếp tục, repair quan hệ,
memory qua restart, chống loop và soak dài. Restart dựng runtime mới trên cùng
root. Một run dùng chung root qua các scenario, không mặc định state trắng từng ca.

`quality.py` kiểm reply trống, fallback kỹ thuật, lặp, drift CJK, invariant ledger
và evidence đếm số lần từ toàn history. `judge.py` chấm 8 trục public transcript,
tự tính total, hỗ trợ judge riêng bằng `ERIS_SIM_JUDGE_*`. Cùng model/provider
với Eris vẫn được phép nhưng runner có ghi chú.

Điểm lệch và giới hạn:

- **Chat `send_user` chỉ đếm main, bỏ surface.** Probe offline dùng coordinator
  giả tăng surface success một lần và có reply → report call delta 0,
  provider_real false. Đây là tái hiện lỗi phép đo; chưa chứng minh một phiên
  production cụ thể đã bị phân loại sai. Autonomous đếm cả main và surface.
- CaptureTransport có bản `wait_for_autonomous` tham chiếu attribute harness
  mà transport không khởi tạo; LiveRuntimeHarness có bản riêng.
- TTS tắt và `ERIS_TTS_SYNC_TEXT=0`: không kiểm chứng chờ `audio_start`, chất âm,
  lipsync hoặc độ trễ tiếng đầu tiên của sản phẩm hiện tại.
- Unit test fake chỉ kiểm simulator, không xác nhận chất lượng hội thoại live.

## 4. integration

`body_contracts` chứa C# và `.uid`. `Eris.csproj` loại
`src\integration\body_contracts\**` khỏi compile. Source Body nằm ở `src/body`.

MemoryPanel dài 1.496 dòng, SHA-256 trùng bản `src/body` lúc đối chiếu. Có portrait
diễn đạt về user và manager quản lý record; tìm kiếm/lọc, sửa, confirm, explain,
đổi scope, forget, cleanup, cache và timeout.

Luồng: `Godot memory_panel_request + request_id → incoming_protocol_runtime
→ living_user_panel → memory_panel_ack + snapshot → Godot`.
Panel không trực tiếp ghi truth store; portrait chỉ là projection diễn đạt.
ACK đối chiếu request ID; hard-delete ở UI cần bấm lần hai.

SettingsPanel chỉ 12 dòng: stub contract cooldown Int64, `ReadJsonInt64` luôn
trả fallback. Không dùng stub này để suy runtime parsing.

**Hai test lookup hiện lỗi:** `test_memory_panel_uses_compact_responsive_empty_state`
và `test_settings_cooldown_still_uses_int64`. Chúng tìm `body` sai cấp rồi fallback
`*.cs.fixture.txt` không tồn tại; file có thật là `src/body/*.cs` và
`src/integration/body_contracts/*.cs`. Đây là lỗi test lookup đã tái hiện,
chưa phải lỗi render/parsing live.

## 5. integrations

Hiện có extension Manifest V3 `chesscom_bridge`, 5 file:

- `manifest.json`: content script Chess.com, storage và localhost host permissions.
- `content.js`: đọc DOM piece/square, dựng board-placement FEN, ply, orientation,
  result, phase, metadata; MutationObserver debounce 140 ms, polling 2,5 giây,
  deduplicate bằng signature.
- `background.js`: POST `http://127.0.0.1:8765/v1/chess/snapshot`, token nếu có,
  ghi diagnostics.
- `popup.html`, `popup.js`: bật/tắt, mode override, endpoint/token và trạng thái.

Backend ngoài extension: `runtime/chess/service.py` nhận snapshot;
tracker/analyzer nối engagement, structured grounding và semantic tutor.
Bootstrap start khi settings cho phép. Stockfish tùy chọn, có engine fallback.
Source/caller không chứng minh extension/server đang hoạt động ở máy người dùng.

Hai điểm từ source:

- `lastSignature` được ghi trước send thành công; send lỗi rồi snapshot giữ
  nguyên sẽ bị bỏ qua ở capture tiếp. Polling không tự bảo đảm retry khi Brain
  vừa trở lại. Đây là phân tích control flow, chưa chạy browser.
- Popup ghi live người thật chỉ quan sát, nhưng backend `full_assistance` true
  cho mọi mode đã biết, kể cả `LIVE_RESTRICTED`. Nhãn UI lệch policy hiện tại;
  nghiên cứu này không thay đổi policy.

## Kiểm chứng

```text
./.venv/Scripts/python.exe -m pytest -q src/tests/emergence src/tests/test_v57_live_behavior_simulation.py src/tests/test_memory_panel_ui_v41.py src/tests/test_conversation_continuity_hotfix.py src/tests/test_memory_hygiene_v41.py src/tests/test_chess_bridge_v37.py
```

**45 passed, 5 failed trong 8,22 giây**, exit 1. Toàn bộ 10 test emergence và
10 test V57 qua. Năm failure:

- Hai lookup fixture/path nêu trên.
- `test_no_phrase_marker_classifier_remains_in_runtime_path`: đòi
  `_resolve_discourse_frame` còn trong source orchestrator hiện tại.
- `test_waiting_chess_task_prompts_for_board_after_short_grace` và
  `test_waiting_chess_task_asks_again_without_spamming`: nhận `due=False`,
  `reason=no_active_engagement`. Chưa kết luận nguyên nhân gốc hay tác động live.

Không có baseline đối chứng để gọi các failure là regression mới.
Golden Mentor được chạy trực tiếp bằng `run_suite`: 20 ca, 2 đạt, 18 lỗi.
Probe split và bộ đếm simulator hoàn toàn offline với object giả.
Thay đổi lần nghiên cứu chỉ gồm tài liệu này và dẫn chiếu trong bài học runtime.

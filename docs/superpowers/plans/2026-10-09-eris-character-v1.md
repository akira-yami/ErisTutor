# Eris Character V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Giúp Eris thể hiện cá tính có khuyết điểm, đáng yêu và có lập trường từ state hiện hữu, đồng thời giữ đúng nguồn của lời nói và ký ức.

**Architecture:** Giữ ConversationKernel V12 và RootAgent làm chủ quyết định hội thoại. Dựng projection nhân vật và registry nguồn cho đúng lượt; writer điều phối tối đa hai candidate, kiểm các bề mặt cuối trước delivery. Observer và memory owners hiện hữu giữ nguồn, correction và chống tự xác nhận qua lời Eris.

**Tech Stack:** Python ≥3.11 theo pyproject.toml; dataclasses; JSON schema qua AIModelRouter.invoke_structured_worker; pytest; canonical memory stores và persistence hiện có. Không thêm dependency.

**Spec:** [Eris Character V1 — đặc tả](../specs/2026-10-08-eris-character-v1-design.md).

Ngày lập: 2026-10-09, Asia/Saigon. Đây là kế hoạch cho triển khai tương lai. Công việc hiện được yêu cầu chỉ nghiên cứu và viết tài liệu; các checkbox bên dưới chưa được thực hiện. Signature mang nhãn **MỚI** là quyết định trong kế hoạch, không phải API đã có. Tài liệu mô tả interface và assertions, không chứa implementation bodies.

## Global Constraints

- RootAgent là semantic/social authority duy nhất; writer không tự mở chủ đề hoặc đổi intent/say/after.
- StateReducer / RuntimeMindState giữ quyền ghi personality/identity; PersistentInternalState giữ current affect. Projection và verifier không ghi state.
- V1 dùng trần **6.000 ký tự** cho bản render nhân vật; render trọn mệnh đề và ghi omitted_facets.
- V1 giữ giới hạn tối đa **10 claim** và **8 refs/claim**; quá giới hạn phải báo lỗi, không cắt im lặng.
- Có tối đa **một lần writer rewrite với cùng root move**; dùng chung lượt rewrite với grounding gate hiện có.
- Mỗi candidate kiểm cả text và speech_text; candidate sửa phải kiểm lại. Fail/unknown không được phát hoặc ghi thành lời đã nói.
- Receipt gắn event/session/generation, state revision và hash các surface cuối; giữ generation guard và idempotency hiện có.
- Correction về fact không bắt cảm xúc biến mất; không hardcode xin lỗi, tự kiểm điểm hoặc kết thúc có hậu.
- Có thể khắc họa nhân vật tối, ghen và tự biện hộ trong cảnh hư cấu; scope không biến thành lịch sử thật.
- Không tối ưu giữ chân, độc quyền hoặc làm user bỏ quan hệ ngoài đời để đạt điểm cá tính.
- Không thêm trait/conflict key, appraisal trước lời nói, ErisActor, SubjectIngress/MindLoop decision path hoặc thuật toán học trait mới.
- Không đổi model/provider/policy để làm benchmark đạt. Không claim hết hallucination hoặc chứng minh ý thức con người.
- Giữ nguyên thay đổi có sẵn của user; tài liệu này không cấp quyền commit, chạy model có chi phí hoặc triển khai sản phẩm.

## Review Focus

- Context dài có thể còn bị cắt ở compiler hoặc adapter; Task 2 kiểm Root và writer nhận cùng render trọn vẹn, kể cả phần cuối conflict/cost.
- Record legacy có ID nhưng không rõ role/scope không chứng minh lịch sử; Tasks 1 và 7 kiểm unknown và ngăn factual promotion.
- Speech tiếng Nhật có thể khác nghĩa display tiếng Việt, hoặc bị dịch lại sau stage; Tasks 4–6 kiểm transcript cuối và đường TTS thật với backend stub.
- User preempt sau verdict, trong deferred text/audio callback vẫn phải thắng; Task 6 kiểm generation ngay trước dispatch và từng callback phát nội dung.
- Tóm tắt cùng nguồn, replay sau reload và correction đối nghịch không được tăng bằng chứng độc lập hoặc xóa chronology; Tasks 7–8 kiểm canonical stores thật.

## Baseline và những phát hiện làm rõ kế hoạch

HEAD khi rà: 25ee2f37. Working tree vẫn có ba file dirty giống lúc viết spec; SHA-256 khớp bảng mục 2 của spec. Chúng không do công việc lập kế hoạch này tạo ra. Khi triển khai phải đọc lại diff và hashes, vì baseline cần gồm cả thay đổi chưa commit của user.

| Source đã đọc | Hành vi hiện tại | Quyết định cho kế hoạch |
| --- | --- | --- |
| conversation/context_assembler.py:assemble; ai_orchestrated/contracts.py:personality_prompt_context | Assembler lấy snapshot; helper vẫn render personality 520 và identity 980 ký tự | Task 2 dựng portrait từ fields, không lấy bản đã cắt làm đầu vào |
| conversation/agent.py:_bounded_context/_bounded_value; public_reply_context.py:compile_public_reply_context | Root cho string tới 32.000 ký tự; compiler wide cho personality 12.000, total 96.000 tại assembler | Chưa có lỗi Root cắt 6.000 ở đường này; giữ test byte equality thay vì sửa limit theo phỏng đoán |
| affective_voice_state.py:project_affective_voice_state | Hàm cập nhật tracker/history trên host; version của AffectiveVoiceState là schema string | Sample một lần bên ngoài builder thuần; revision portrait lấy RuntimeMindState, không lấy schema version làm revision |
| conversation/context.py:RootCapabilityRegistry; agent.py:_invoke_capability | invocation_log chỉ ghi tên; kết quả nằm trong tool_results của Root | Task 1 thêm receipt kết quả đúng event/run; gọi tool không chứng minh thành công |
| model_router.py:_compat_public_envelope; claim_domain_contract.py:normalize_claim_manifest | Manifest thiếu/sai kiểu bị đổi thành []; normalizer cắt 10/8 | Task 3 capture nguyên trạng và kiểm trước mọi normalization mất thông tin |
| model_router.py:generate_public_reply; conversation/runtime_writer.py:realize | Có language repair trong router và grounding rewrite bên ngoài | Task 6 giao quyền ngân sách sửa candidate cho writer V12; tránh hai vòng retry nhân nhau |
| public_output_runtime.py:_send_decision_to_body; stage_contracts.py:StageFrameBuilder.build; stage_output.py:frames_for_tts | Sau writer còn sanitizer, intention correction, tách segment, và khả năng dịch lại ở TTS | Task 4 tách prepare/dispatch trong owner hiện hữu; Task 6 phát đúng artifact đã kiểm |
| transport/udp.py:send_json; tts_engine.py:_clean_text/_resolve_main_transcript | UDP loại ký tự ngoài BMP, payload quá 63.000 bytes có thể cắt; TTS có cleaning và language repair muộn | Normalize/preflight trước hash; verified V12 không được đi vào nhánh sửa chữ muộn |
| ai_orchestrated/bootstrap.py:on_state; memory_kernel.py:plan_ingest; memory_models.py:to_state_commit_request | Observer candidate có thể đi qua policy dành cho user text, với default explicit_user_statement | Task 7 giữ role/scope/ancestry và kiểm ở durable commit; retrieval admission riêng lẻ chưa đủ |
| memory/semantic_memory.py:_merge_item | Merge tăng observations/confidence trước khi dedupe evidence_ids | Task 8 kiểm root ancestry trước reinforcement, kể cả sau reload |

Các kết luận trên là đọc source. Không chạy model, ứng dụng hoặc suite trong công việc lập kế hoạch. Dòng tham chiếu chỉ giúp tìm symbol; executors phải tìm lại callers khi code đổi.

## File map và đơn vị bàn giao

Các phần dùng chung registry, snapshot và receipt nên được triển khai theo một kế hoạch có phụ thuộc. Mỗi task vẫn có bộ kiểm và diff riêng để có thể nghiệm thu độc lập.

| Task | Tạo mới khi triển khai | Owner/file hiện có sẽ sửa có giới hạn |
| --- | --- | --- |
| 0 — Baseline | src/tools/run_eris_character_eval.py; src/tests/fixtures/eris_character_v1/scenarios.json; src/tests/conversation/test_character_eval_harness_v1.py | Không thay hành vi production |
| 1 — Nguồn | src/runtime/conversation/claim_evidence.py; src/tests/conversation/test_character_evidence_v1.py | conversation/context.py, agent.py, kernel.py, context_assembler.py, evidence_assembler.py; ai_orchestrated/orchestrator.py; memory/memory_kernel.py, memory_kernel_contracts.py, memory_cognition.py, memory_activation.py, memory_retrieval_engine.py và memory_packet_builder.py |
| 2 — Nhân vật | src/runtime/conversation/character_portrait.py; src/tests/conversation/test_character_portrait_v1.py | context_assembler.py; tests context/root/writer; public helpers chỉ khi caller trace yêu cầu |
| 3 — Manifest | src/tests/conversation/test_character_manifest_v1.py | runtime/claim_domain_contract.py; ai_orchestrated/model_router.py; runtime/json_boundary.py nếu cần loại metadata nội bộ |
| 4 — Surface cuối | src/runtime/conversation/public_output_artifact.py; src/tests/conversation/test_character_output_artifact_v1.py | public_output_runtime.py, stage_contracts.py, stage_output.py, tts_engine.py, tts_task_manager.py, conversation/kernel.py/delivery_adapter.py; state_commit_service.py nếu cần expose delegate |
| 5 — Verifier | src/runtime/conversation/claim_verifier.py; src/tests/conversation/test_character_verifier_v1.py | Router transport được tái sử dụng |
| 6 — Public integration | src/tests/conversation/test_character_claim_integrity_v1.py | conversation/runtime_writer.py/delivery_adapter.py; ai_orchestrated/model_router.py; public_output_runtime.py; TTS guards từ Task 4 |
| 7 — Nguồn memory | src/tests/conversation/test_character_memory_provenance_v1.py | observation_bridge.py; ai_orchestrated/observer.py/bootstrap.py; state_commit_service.py; turn_memory_pipeline.py/background_memory_digest_kernel.py; memory_kernel.py/memory_models.py/memory_cognition.py/memory_admission.py |
| 8 — Replay/correction | Mở rộng test_character_memory_provenance_v1.py | memory/semantic_memory.py; memory/living_user/service.py và authority.py chỉ khi thiếu link qua ingress hiện tại |
| 9 — Giọng/nghiệm thu | Báo cáo benchmark ở output_dir do executor chỉ định | eris_voice_contract.py; conversation/agent.py chỉ phần prompt; runner/fixtures Task 0 |

Đây là danh sách trách nhiệm, không phải lệnh sửa mọi file. Các module MỚI claim_evidence và public_output_artifact chứa data/validation; chúng không thêm owner memory, persona hoặc planner.

Cross-reference types chỉ dùng cho annotation thì import qua TYPE_CHECKING/forward annotation; public_output_artifact không import runtime verifier ngược lại. ClaimVerificationReceipt do Task 5 định nghĩa, nên Task 4 không phụ thuộc một import chưa tồn tại để chạy tests preparation.

## Task 0 — Đóng băng tình huống và baseline có thể đối chiếu

**Phụ thuộc:** không. **Bàn giao:** harness đánh giá đi qua ConversationKernel V12, fixtures khai rõ nguồn và bản baseline; chưa thay production.

**Files:** tạo ba file Task 0 trong bảng; tham chiếu src/tests/conversation/test_context_assembler_v12.py, test_runtime_writer_v12.py, test_delivery_adapter_v12.py và src/tests/test_turn_memory_pipeline.py cho host/temporary stores.

**Interfaces MỚI:** run_scenarios(host_factory: Callable[[Mapping[str, Any]], Any], scenarios: Sequence[Mapping[str, Any]], *, runs: int, label: str, output_dir: Path) -> Mapping[str, Any] trong runner. host_factory nhận một scenario mapping, trả host V12 dùng ledger/state/memory tạm; runner chỉ phát user events theo thứ tự qua kernel. CLI có --mode stub/configured, --scenario-file, --runs, --label, --output-dir. configured dùng clients/settings hiện có của host; không tự đổi provider.

- [ ] **0.1 Viết test harness:** test_scenarios_have_ten_ids_and_counterparts kiểm đúng S01–S10, mỗi case bốn user turns, state/evidence/scopes và counterpart; test_harness_calls_v12_kernel_and_exports_failures kiểm lỗi cũng có row, không fake reply để tiếp tục; test_each_run_uses_isolated_storage kiểm hai run không chia memory/profile/threads.
- [ ] **0.2 Chạy để thấy fail có căn cứ:** python -m pytest -q src/tests/conversation/test_character_eval_harness_v1.py. Ban đầu collection fail vì runner/fixtures mới chưa có; sau khi có file, assertions phải bắt đúng hành vi thiếu, không chỉ kiểm import.
- [ ] **0.3 Làm harness tối thiểu:** lưu commit, dirty hashes, Python/model/settings, scenario revision, seed nếu backend hỗ trợ, timestamp, channel, state/evidence trước-sau, Root move, writer surfaces, status, calls và latency. Phân biệt unavailable telemetry với số 0. Không ghi secrets hoặc memory tài khoản thật.
- [ ] **0.4 Ghi fixtures:** S02 cài sẵn lỗi 7 × 8 = 54; S03 không tạo value key fairness ngoài schema, dùng nội dung authored/giá trị được schema hỗ trợ; S08 preference authored là fixture, không giả đã có kho gu production. S09 chứa cảnh tối không cần redemption; S10 chứa assistant-only claim sai. Mọi counterpart giữ cùng dữ kiện và chỉ đổi chiều cần kiểm.
- [ ] **0.5 Kiểm harness:** chạy lại lệnh 0.2; thêm python -m tools.run_eris_character_eval --mode stub --scenario-file src/tests/fixtures/eris_character_v1/scenarios.json --runs 1 --label baseline-stub --output-dir scratch/eris-character-v1/baseline-stub. Exit 0 chỉ chứng minh harness chạy và xuất đủ dữ liệu, không chứng minh cá tính model đạt.
- [ ] **0.6 Đóng baseline trước sửa production:** khi session sau có yêu cầu chạy model, chạy mode configured với --runs 3 và label baseline. Lưu baseline artifacts và source fingerprints. Nếu chưa được chạy model, đánh dấu baseline_model=pending; vẫn làm được tests stub và không điền điểm chất lượng tưởng tượng. Bàn giao diff/harness evidence trước Task 1.

## Task 1 — Nguồn có cấu trúc cho đúng lượt và kết quả tool thật

**Phụ thuộc:** 0. **Bàn giao:** registry bất biến đọc được từ report/ledger/memory, cùng các receipt kết quả tool; không tạo factual record mới.

**Files:** nhóm Task 1. Tests bổ sung src/tests/conversation/test_capability_boundaries_v12.py và test_context_assembler_v12.py.

**Interfaces MỚI trong claim_evidence.py:**

- EvidenceEntry: ref, source_kind, owner, subject, scope, recorded_at, temporal_scope, status, content, parent_refs, revision. content là mapping/chuỗi từ nguồn đã resolve; không chứa object repr hoặc ID do model khai. status gồm supported, unknown, stale, superseded; source_kind dùng đúng danh sách spec.
- EvidenceRegistry: event_id, snapshot_id, state_version, entries, allowed_domains, fingerprint. entries và nested content là read-only; fingerprint lấy từ canonical JSON của các entry đã chọn, không từ prose.
- build_evidence_registry(*, event: ConversationEvent, state: RuntimeMindState | None, affect: AffectiveVoiceState | None, report: SourceGroundingReport | None, ledger_turns: Sequence[SessionTurn], memory_records: Sequence[MemoryRecord], sampled_at: float) -> EvidenceRegistry.
- extend_evidence_registry(registry: EvidenceRegistry, receipts: Sequence[CapabilityResultReceipt]) -> EvidenceRegistry: trả view bất biến mới; giữ nguyên ref/content/revision của entry cũ, thêm kết quả đúng event/run/generation. Portrait sau này dùng subset nguồn nhân vật của registry này.
- render_evidence_context(registry: EvidenceRegistry) -> str: giữ ref, kind, scope, status và nội dung liên quan. Không render whole private registry hoặc secrets.

**Interfaces MỚI tại owner hiện có:**

- CapabilityResultReceipt trong conversation/context.py gồm invocation_id, name, event_id, session_id, generation, run_id, status, result và source_refs. Invocation ID do runtime cấp; status/result là phản hồi executor, không do Root tự khai. Receipt bất biến và không mở rộng quyền gọi capability.
- RootCapabilityRegistry.record_result(*, name: str, invocation_id: str, event: ConversationEvent, run_id: str, result: Mapping[str, Any]) -> CapabilityResultReceipt; result_receipts_for(event_id: str, *, run_id: str, generation: int) -> tuple[CapabilityResultReceipt, ...]. Receipt giữ status, dữ liệu trả về và refs executor, gồm cả error/unavailable/started. Không coi started là completed.
- Kernel gắn __conversation_run_id vào runtime_context trước Root; Root ghi receipt ngay sau lời gọi capability và đưa receipt ref vào tool_results của vòng kế tiếp. Không thay quyền gọi tool hoặc execution permissions.
- MemoryKernel.build_public_context(query: str, *, session_id: str, user_context_token_budget: int = 6000, max_lines: int = 64) -> PublicMemoryContext. PublicMemoryContext MỚI trong memory_kernel_contracts.py gồm text, records, degraded. Text và records phải đến cùng recall/profile selection; không recall lần hai rồi đoán record cho chuỗi lần một.
- MemoryRetrievalResult và MemoryPacket ở memory_activation.py/memory_packet_builder.py thêm source_records: tuple[MemoryRecord, ...] = (). MemoryRetrievalEngine giữ đúng candidate.record được chọn sau WorkingMemoryGate, match theo rendered record_ids và chuyển chúng xuyên packet builder. Field này là metadata của kết quả đang có, không phải store/cache mới. Kernel dùng cùng packet để render nguồn và phối hợp compile_user_context hiện hữu; profile/active-state entry không resolve được source giữ unknown. Không thay quotas/scoring/recall cap trong V1.
- ConversationContextAssembler.assemble thêm tham số optional event và source_report. Chat/autonomous callers truyền event thật và report trước assemble; adapter legacy không có nguồn typed phải báo degraded. Không parse memory prose thành facts.

- [ ] **1.1 Viết test:** test_registry_preserves_user_assistant_and_fiction_scope; test_unresolved_legacy_ref_is_unknown; test_registry_and_nested_content_are_read_only; test_source_id_without_entailment_is_not_world_proof; test_capability_error_is_not_action_success; test_result_receipts_are_bound_to_event_run_generation; test_public_memory_text_and_records_share_one_selection. Assert output metadata và source records không đổi.
- [ ] **1.2 Chạy fail:** python -m pytest -q src/tests/conversation/test_character_evidence_v1.py src/tests/conversation/test_capability_boundaries_v12.py. Tests mới phải fail vì contract/wiring thiếu; không sửa expected để chấp nhận nguồn mơ hồ.
- [ ] **1.3 Làm wrapper/adapters:** lấy GroundingEvidence thực từ report.evidence_items; ledger dùng session/index/role/meta thật; memory resolve record_id tại canonical owner qua record_from_layer_item hiện có. Runtime snapshot ref chỉ chứng minh snapshot, không chứng minh chuyến đi hoặc lời hứa. Memory/profile text thiếu source được gắn unknown, không tự đăng ký thành supported.
- [ ] **1.4 Nối kết quả Root:** invocations_for vẫn là log tên để compatibility; record_result mới ghi kết quả thực. Writer sau Root extend registry bằng receipt đúng run, render refs cho writer/verifier. Không dùng Root.say, working note, last mutable research snapshot hoặc lời assistant làm bằng chứng độc lập. Root vẫn nhìn cùng các tool results vừa được resolve.
- [ ] **1.5 Kiểm pass:** chạy lại 1.2 và python -m pytest -q src/tests/conversation/test_context_assembler_v12.py src/tests/conversation/test_root_decision_v12.py. Dùng fake provider với real registry, một action failed và một research started/completed thuộc events riêng.
- [ ] **1.6 Bàn giao:** lưu source mapping và caller trace chat/autonomous, fingerprint sample, diff và kết quả. Không tuyên bố memory đã sửa; Task 7 mới chặn đường promotion.

## Task 2 — Portrait thuần và một bản cá tính cho Root/writer

**Phụ thuộc:** 1. **Bàn giao:** cá tính được truyền theo facets đầy đủ, không tăng activation hoặc ghi state.

**Files:** tạo character_portrait.py/test_character_portrait_v1.py; sửa context_assembler.py. Rà callers của RuntimeMindState.prompt_context/personality_prompt_context/identity_prompt_context và PersonalityProfile/IdentityProfile.prompt_summary; chỉ sửa compatibility helper khi có test chứng minh đường cần dùng portrait.

**Interfaces MỚI:** build_character_portrait(state: RuntimeMindState | None, *, affect: AffectiveVoiceState | None, snapshot_id: str, sampled_at: float, source_registry: EvidenceRegistry) -> CharacterPortrait; render_character_portrait(portrait: CharacterPortrait, *, max_chars: int = 6000) -> CharacterPortraitRender. Các types nằm cùng character_portrait.py: CharacterPortrait giữ snapshot/state version/time/facets/source subset; CharacterFacet giữ facet_id/kind/summary/origin/source_refs/temporal_scope/confidence; CharacterPortraitRender giữ text/omitted_facets/overflow. Không nhận host hoặc model client.

- [ ] **2.1 Viết test:** test_portrait_does_not_mutate_state_registry_or_affect; test_conflict_keeps_both_pulls_choice_and_residue; test_value_choice_keeps_rejected_cost_and_confidence; test_disposition_is_not_current_affect; test_legacy_trait_does_not_invent_origin_episode; test_render_is_deterministic_and_complete_under_6000; test_required_facets_overflow_is_explicit; test_root_and_writer_receive_identical_portrait_and_snapshot. Snapshot trước-sau bằng nhau; retry sample affect không tăng thêm.
- [ ] **2.2 Chạy fail:** python -m pytest -q src/tests/conversation/test_character_portrait_v1.py src/tests/conversation/test_context_assembler_v12.py src/tests/conversation/test_runtime_writer_v12.py.
- [ ] **2.3 Dựng facets:** đọc PersonalityProfile/IdentityProfile fields trực tiếp; SelfInterpretation giữ confidence và tính có thể sai; BeliefRecord giữ evidence_ids/contradictions/last_turn_seq. Conflict/ValueChoice không có timestamp riêng thì dùng revision snapshot để định vị quan sát, không tự tạo thời điểm trải nghiệm. Không thêm affect jealousy axis; biểu đạt ghen dựa conflict/interpretation đã có.
- [ ] **2.4 Render/nối context:** assembler lấy state và sample project_affective_voice_state một lần; builder chỉ nhận kết quả. Ưu tiên integrity/source → current affect → active conflict → value choice → relation → expression → preference liên quan; ties theo facet_id. Bỏ nguyên facet có omission reason, không cắt giữa câu. Root/writer nhận cùng text qua personality_context; __character_portrait và registry chỉ nội bộ. Nếu compiler làm đổi whitespace, lấy chính bản canonical sau compiler cho cả hai; compiler không được cắt hoặc bỏ portrait âm thầm.
- [ ] **2.5 Kiểm pass:** chạy lại 2.2 và python -m pytest -q src/tests/test_open_ended_personality_v12.py src/tests/test_v43_event_driven_affect.py src/tests/test_v39_affective_voice_state.py src/tests/conversation/test_v12_feature_parity_rewire.py. Fixture state dài phải kiểm tận phần cuối, không chỉ tìm header.
- [ ] **2.6 Bàn giao:** đưa một packet trước/sau cùng state và bảng facet retained/omitted; đó là bằng chứng truyền dữ liệu, chưa phải bằng chứng model nói tự nhiên.

## Task 3 — Giữ manifest nguyên trạng và kiểm typed trên dữ liệu thật

**Phụ thuộc:** 1. Có thể làm độc lập Task 2 sau khi contract nguồn chốt. **Bàn giao:** manifest mất/sai/overflow không bị biến thành rỗng hợp lệ.

**Files:** claim_domain_contract.py; model_router.py:_compat_public_envelope và writer schemas; test_character_manifest_v1.py; mở rộng test_20260813_tts_hallucination_hardfix.py, test_20260813_evidence_enrichment_v73.py, test_public_writer_recovery_v6.py.

**Interfaces MỚI trong claim_domain_contract.py:** ClaimManifestEnvelope(present, raw, structural_status, normalized, violations); inspect_claim_manifest(payload: Mapping[str, Any]) -> ClaimManifestEnvelope. structural_status là valid/missing/invalid/oversize. raw là snapshot nguyên trạng trước compatibility conversion; normalized không cắt claim hoặc ref để che lỗi. validate_claim_manifest giữ signature hiện có và nhận manifest đã qua structural check; registry runtime cấp allowed_domains/allowed_evidence_refs.

- [ ] **3.1 Viết test:** test_missing_manifest_survives_router_compatibility; test_wrong_type_and_invalid_items_are_rejected; test_empty_manifest_is_distinct_from_missing; test_eleventh_claim_and_ninth_ref_are_reported; test_refs_are_checked_against_runtime_registry; test_valid_fresh_preference_needs_no_embodied_history. Kiểm cả entry không phải mapping, thiếu field, refs là string và domain lạ.
- [ ] **3.2 Chạy fail:** python -m pytest -q src/tests/conversation/test_character_manifest_v1.py src/tests/test_public_writer_recovery_v6.py. Chỉ ra laundering hoặc truncation hiện tại, không chỉ fail module import.
- [ ] **3.3 Capture trước normalization:** router giữ typed envelope ở metadata private; compatibility field claim_manifest không được làm mất cờ presence. Writer/verifier dùng envelope, parser/body không nhận object này. Rà public_payload boundary để loại envelope/registry/receipt nội bộ khỏi payload public và khỏi json.dumps lỗi.
- [ ] **3.4 Kiểm typed:** giới hạn 10/8 áp dụng trên raw trước dedupe; invalid không được normalizer bỏ qua im lặng. Không cắt claim xuống 320 hoặc ref xuống 140 ký tự rồi dùng prefix thay cho dữ liệu đầy đủ; checker nhận raw và normalized giữ đủ chuỗi. Domain/ref membership là điều kiện cần. Ref lạ do writer khai không trở thành allowlist; câu opinion chứa fact vẫn chờ Task 5 kiểm semantics. Không thay quyền có fresh opinion/self_preference hợp lệ.
- [ ] **3.5 Kiểm pass:** chạy lại 3.2 và python -m pytest -q src/tests/test_20260813_tts_hallucination_hardfix.py src/tests/test_20260813_evidence_enrichment_v73.py. Ghi expectation nào đổi vì manifest thật sự thiếu và expectation nào giữ vì preference mới hợp lệ.
- [ ] **3.6 Bàn giao:** bảng raw input → status → typed verdict; gọi production path từ router stub qua RuntimeConversationWriter, không chỉ gọi helper validator.

## Task 4 — Chuẩn bị artifact cuối trước kiểm, giữ nguyên đến UI/TTS

**Phụ thuộc:** 1, 3. **Bàn giao:** preparation không phát hoặc ghi hậu quả; dispatch dùng cùng artifact, không dựng lại chữ.

**Files:** nhóm Task 4; bổ sung src/tests/test_basic_settings_main_brain.py và test_protocol_and_transport_boundaries.py. PublicOutputRuntimeMixin và các adapter hiện có vẫn là owners.

**Interfaces MỚI:**

- PreparedPublicOutput trong public_output_artifact.py: event_id/session_id/generation/run_id/move_id/delivery_id, channel, display_text, speech_text, memory_text, stage_frame, segments, payload, source_fingerprint. segments giữ final text và stable segment_id; dataclasses/mappings phải immutable sau prepare.
- PublicOutputRuntimeMixin._prepare_decision_for_body(decision: Decision, *, channel: str, event: ConversationEvent, turn: KernelTurn) -> PreparedPublicOutput: tách phần compile/normalize hiện có ra khỏi _send_decision_to_body.
- _dispatch_prepared_public_output(prepared: PreparedPublicOutput, *, verification: ClaimVerificationReceipt | None) -> bool: wrapper compatibility tạm ở task này; receipt bắt buộc cho production V12 khi Task 6 nối xong. Không rebuild StageFrame hoặc gọi lại segmenter.
- RuntimeConversationDeliveryAdapter.prepare_candidate(*, turn: KernelTurn, event: ConversationEvent, payload: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> PreparedPublicOutput: parse payload và dựng Decision, stage metadata/callback đúng owner. Nó không append history, gửi position update hoặc phát audio.
- KernelTurn.delivery_id: str = "" là field cơ học MỚI; kernel cấp ID trước writer, giữ cùng ID tới mark_delivery_started/deliver. Việc cấp ID không đánh dấu successful commit.
- VerifiedTranscript MỚI trong public_output_artifact.py: delivery_id, generation, segment_id, transcript_hash. TtsTaskManager chuyển contract này qua task/cache tới TtsEngine.synthesize và _SpeakRequest bằng optional parameter; legacy caller giữ mặc định None.

- [ ] **4.1 Viết test:** test_prepare_has_no_public_side_effects; test_prepared_surfaces_match_real_stage_and_segment_pipeline; test_prepare_normalizes_udp_and_tts_text_before_hash; test_verified_transcript_rejects_late_localization_and_cache_mismatch; test_oversized_v12_datagram_fails_before_dispatch. Dùng StageFrameBuilder thật, stub backend/transport; kiểm history/lifecycle/public observation/audio calls đều rỗng sau prepare.
- [ ] **4.2 Chạy fail:** python -m pytest -q src/tests/conversation/test_character_output_artifact_v1.py src/tests/test_basic_settings_main_brain.py. Baseline chưa có prepare boundary nên chưa đáp ứng các assertions trên.
- [ ] **4.3 Tách prepare/dispatch:** chuyển Decision.to_payload, StageFrameBuilder.build, PublicSurfaceCommitter cleaning cần thiết và StageOutputPipeline.frames_for_tts lên prepare. Giữ raw action/nonverbal side-channel; display/memory lấy đúng final stage. Dùng cùng TTS cleaning trước verifier; nếu _clean_text chạy lại còn đổi chữ thì lỗi. Prepare không chạy model localizer; speech thiếu/sai language quay về candidate error để dùng ngân sách rewrite Task 6.
- [ ] **4.4 Khóa segments/transport:** verifier sẽ kiểm toàn bộ transcript thực của segments theo thứ tự, không chỉ provider.speech_text. Apply cùng UDP sanitizer trước hash; kiểm lần transport apply lại là idempotent. Preflight từng datagram theo MAX_UDP_PAYLOAD_BYTES hiện có, **63.000 bytes**, sau metadata cần gửi; overflow V12 lỗi, không đi vào nhánh cắt text. Nếu dispatch bổ sung metadata, preflight lại trước send. Không sửa fallback của mọi luồng.
- [ ] **4.5 Khóa TTS:** verified transcript phải khớp hash sau cleaning và trước cache/backend. Nhánh verified không dịch lại hoặc sửa ngôn ngữ trong _resolve_main_transcript. TTS budget error vẫn là failure, không cắt lời đã kiểm. Markup/prosody được compile như hiện tại; receipt chỉ chứng minh transcript được cấp tới synthesizer, không chứng minh audio đọc đúng từng từ.
- [ ] **4.6 Kiểm pass/bàn giao:** chạy lại 4.2 và python -m pytest -q src/tests/test_protocol_and_transport_boundaries.py src/tests/conversation/test_kernel_v12.py src/tests/conversation/test_delivery_adapter_v12.py. Giữ legacy wrappers; không coi path thiếu receipt đã compliant. Ghi trace final display/transcript/segment hashes và diff extraction trước Task 6.

## Task 5 — Verifier có phạm vi, đối chiếu cả câu và manifest

**Phụ thuộc:** 1–4. **Bàn giao:** checker trả structured verdict; không viết câu thay Eris hoặc quyết định thái độ.

**Files:** claim_verifier.py/test_character_verifier_v1.py. Dùng AIModelRouter.invoke_structured_worker hiện có, không thêm provider adapter.

**Interfaces MỚI trong claim_verifier.py:**

- CharacterClaimVerifier(router).verify(*, prepared: PreparedPublicOutput, manifest: ClaimManifestEnvelope, move: NextConversationMove, registry: EvidenceRegistry, portrait: CharacterPortrait) -> ClaimVerificationResult.
- ClaimVerificationResult: status pass/fail/unknown, findings, surface_equivalence, memory_equivalence, reason_codes; mỗi finding giữ surface/segment_id/span/domain/support_refs/verdict/reason_code. Span là đoạn nguyên văn của đúng final surface. memory_equivalence kiểm rằng final memory projection không thêm hoặc đảo fact của display.
- ClaimVerificationReceipt: event/session/generation/run/move/delivery IDs, snapshot_id/state_version, registry_fingerprint, display_hash/speech_hash/memory_hash, segment_hashes, status. build_verification_receipt(*, prepared: PreparedPublicOutput, result: ClaimVerificationResult, registry: EvidenceRegistry, portrait: CharacterPortrait) -> ClaimVerificationReceipt chỉ tạo receipt pass sau runtime validation, không chỉ tin status model. Hash là SHA-256 của đúng chuỗi UTF-8 cuối sau normalization; speech_hash gắn danh sách segment theo thứ tự, không nối tùy ý để che khác biệt ranh giới. Memory text chỉ được lưu nếu là phép chiếu đã kiểm của final display, không chứa claim mới.
- validate_verifier_result(raw: Mapping[str, Any], *, prepared: PreparedPublicOutput, registry: EvidenceRegistry) -> ClaimVerificationResult: kiểm schema/status/span/ref/scope và consistency. Unknown cấu trúc/không đủ kết quả không được đổi thành pass.

**Cấu hình đề xuất có giới hạn:** name=eris_character_claim_verifier_v1; cognitive_lane=public_claim_verifier; max_tokens=2400; timeout_seconds=10.0; temperature=0.0; không semantic retry bên trong checker. Dùng observer_client, rồi surface_client nếu observer không có; không tự tạo model khác. Đây là giá trị khởi đầu để đo, không phải SLO đã chứng minh. Schema tối đa 30 findings theo display/speech/memory; model báo vượt khả năng/response thiếu thì unknown, không truncate để đạt pass.

- [ ] **5.1 Viết test:** test_existing_ref_with_wrong_meaning_fails; test_undeclared_speech_claim_is_found; test_opinion_label_does_not_hide_history; test_fresh_opinion_passes_without_false_history; test_state_trait_cannot_prove_current_jealous_episode; test_fiction_and_stale_sources_keep_scope; test_memory_projection_cannot_remove_negation_or_add_claim; test_missing_span_unknown_ref_timeout_or_invalid_schema_is_unknown. Worker stub trả verdict cố ý sai để runtime parser phải bác.
- [ ] **5.2 Chạy fail:** python -m pytest -q src/tests/conversation/test_character_verifier_v1.py. Collection/logic phải fail vì checker mới chưa có; không gọi provider thật.
- [ ] **5.3 Viết schema/prompt trong domain module:** gửi final display, từng final speech segment, final memory projection, manifest nguyên trạng + structural result, root move, portrait và đúng registry subset. Dữ liệu user/source/candidate được đóng như data không phải checker instruction. Đối chiếu claim tự nhận diện với khai báo; kiểm support entailment, scope, thời điểm, equivalence giữa hai ngôn ngữ và memory projection. Không yêu cầu chain-of-thought hoặc câu sửa.
- [ ] **5.4 Giữ reason codes của spec:** MANIFEST_MISSING/INVALID/OVERSIZE, UNDECLARED_CLAIM, UNKNOWN_REF, WRONG_SCOPE, STALE_SOURCE, UNSUPPORTED_CLAIM, MISLABELED_CLAIM, SURFACE_MISMATCH, VERIFIER_UNAVAILABLE; bổ sung MEMORY_SURFACE_MISMATCH cho projection lưu nhớ sai nghĩa. Dùng deterministic typed failures trước worker. Semantic worker có thể bỏ sót/sai; golden stub tests chỉ chứng minh protocol, Task 9 phải đo bằng người.
- [ ] **5.5 Kiểm pass:** chạy lại 5.2. Assert tối đa một structured call/candidate, client selection và timeout truyền đúng; failure không sửa Root move, không gọi capability, không mutate portrait/registry/state.
- [ ] **5.6 Bàn giao:** input/output examples đã khử dữ liệu riêng tư và danh sách giới hạn: self/shared history, user/state/environment/action claims; kiến thức phổ quát chưa được fact-check toàn diện.

## Task 6 — Nối một ngân sách rewrite, receipt và delivery thật

**Phụ thuộc:** 2–5. **Bàn giao:** đường public V12 thực sự kiểm và phát đúng artifact; fail/unknown không thành public memory.

**Files:** nhóm Task 6; mở rộng test_runtime_writer_v12.py, test_delivery_adapter_v12.py, test_kernel_v12.py. Tests integration mới dùng real context/kernel/writer/delivery/stage, chỉ stub model/network/audio.

**Interface thay đổi có giới hạn:** AIModelRouter.generate_public_reply thêm allow_language_repair: bool = True. Legacy caller giữ hiện trạng; writer V12 truyền False và nhận language mismatch metadata để tự dùng một rewrite chung. Signature RuntimeConversationWriter.realize/deliver giữ hiện có; rendered.metadata thêm prepared_public_output và claim_verification_receipt, không gửi hai objects này ra body. Delivery V12 yêu cầu prepared + pass receipt.

- [ ] **6.1 Viết test:** test_v12_typed_verifier_is_reached_on_public_path; test_language_and_grounding_share_one_rewrite; test_rewrite_keeps_same_root_move_snapshot_and_registry; test_second_fail_or_unknown_has_no_send_history_or_observer; test_delivery_rejects_changed_text_speech_segments_or_source_fingerprint; test_preemption_after_verdict_blocks_deferred_callbacks; test_duplicate_delivery_and_receipt_do_not_double_commit. Một case ref thật sai nghĩa chỉ xuất hiện trong speech phải bị chặn.
- [ ] **6.2 Chạy fail:** python -m pytest -q src/tests/conversation/test_character_claim_integrity_v1.py src/tests/conversation/test_runtime_writer_v12.py src/tests/conversation/test_delivery_adapter_v12.py. Fixture lỗi đi qua kernel tới delivery host thật, không mock toàn bộ phần sau writer. Cập nhật helpers _rendered/_Router của tests cũ để tạo manifest, prepared artifact và stub verifier receipt hợp lệ; không thêm nút bỏ qua gate chỉ để giữ tests xanh.
- [ ] **6.3 Nối chuỗi candidate:** generate → inspect manifest/typed validation → prepare final output → semantic verification → receipt. Gom lỗi ngôn ngữ, typed, grounding và semantics vào một repair instruction giữ move. Candidate đầu lỗi được đúng một generate nữa; candidate sau chạy lại toàn bộ kiểm với cùng snapshot, nguồn và Root move. Không tự thêm lời đạo đức, apology hoặc chủ đề khác trong repair.
- [ ] **6.4 Giới hạn call:** V12 tối đa hai writer generations và tối đa hai semantic checks trong cùng realization; structural fail có thể không gọi semantic worker. Legacy grounding annotator không mở retry/call phụ trên V12 sau khi checker mới đã thay vai trò. Router/network retries vốn có phải ghi riêng, không gọi chúng là rewrite. Không âm thầm escalation/fallback để cứu chất lượng.
- [ ] **6.5 Nối dispatch/commit:** so exact hashes, IDs, source fingerprint và generation/run guard ngay trước gửi. Dispatch cùng prepared segments; final lifecycle/public behavior/V98–V103 notifications chỉ sau acceptance đúng owner, không từ prepare hoặc candidate bị bác. Ghi assistant ledger/history từ final stage display/memory; truyền receipt/provenance vào observer. Callback text/audio kiểm generation trước phát, giữ barge-in hiện có. Revision receipt là revision đã dùng, không đổi sang snapshot mới giữa lượt.
- [ ] **6.6 Kiểm pass/bàn giao:** chạy lại 6.2 và python -m pytest -q src/tests/conversation src/tests/test_20260813_tts_hallucination_hardfix.py src/tests/test_20260813_evidence_enrichment_v73.py src/tests/test_basic_settings_main_brain.py src/tests/test_protocol_and_transport_boundaries.py. Dùng TTS backend stub ghi transcript thực ở boundary synthesis, UDP stub ghi final payload. Fail/unknown phải ra trạng thái lỗi kernel/telemetry có reason, không ghi một fallback chưa kiểm.

## Task 7 — Giữ role, scope và ancestry tới durable memory

**Phụ thuộc:** 1, 6. **Bàn giao:** câu Eris/observer không được gắn thành lời user; fiction/uncertainty không trở thành fact chỉ nhờ tóm tắt.

**Files:** nhóm Task 7. Dùng lại SemanticClaim/ClaimBuilder/MemoryWorthinessPolicy và permission gate hiện hữu; không tạo memory store hoặc pipeline học mới.

**Metadata MỚI:** claim_provenance nằm trong context/metadata hiện có, gồm schema=eris.claim_provenance.v1, origin_kind, root_event_ids, parent_claim_refs, subject, scope, assertion, corrects_claim_ref, promotion_basis. origin_kind phân biệt user_statement/assistant_utterance/observer_summary/runtime_state/fiction/unknown. promotion_basis là refs runtime đã resolve, không phải confidence model tự khai. Phân biệt origin_kind của record với source_kind của evidence wrapper Task 1 bằng adapter rõ ràng.

Mapping cố định: user_statement → source_kind user_statement; assistant_utterance → observed_event với scope utterance, chỉ chứng minh lời đã nói; observer_summary → nguồn gốc đã resolve qua parents, không tạo observed_event độc lập về thế giới; runtime_state → runtime_state; fiction → fiction; unknown → status unknown. Fresh opinion sau commit là utterance có loại đánh giá, không tự làm nguồn cho lịch sử chứa trong câu.

**Interfaces:** observe_assistant_commit và observer submit request nhận optional claim_provenance từ receipt/runtime envelope. _ingest_user_turn_memory_hot_path và enqueue/digest context nhận cùng metadata nguồn user. Observer output được runtime attach ancestry của request; không cho model thay nguồn gốc bằng root_event_ids nó tự viết. MemoryKernel.plan_ingest bảo toàn metadata và chọn policy đúng loại nguồn; _annotate_request không để default explicit_user_statement thắng nguồn observer/assistant.

- [ ] **7.1 Viết test:** test_assistant_statement_proves_only_utterance; test_observer_summary_cannot_become_explicit_user_statement; test_fiction_scope_survives_summary_and_digest; test_unknown_scope_is_not_factual_promotion; test_verified_user_statement_still_ingests; test_legacy_memory_is_not_given_invented_provenance. Kiểm cả durable store sau drain, không chỉ prompt output.
- [ ] **7.2 Chạy fail:** python -m pytest -q src/tests/conversation/test_character_memory_provenance_v1.py src/tests/conversation/test_observation_bridge_v12.py src/tests/test_turn_memory_pipeline.py. Fixture real MemoryCognitionCore(tmp_path), MemoryKernel.from_existing_core và pipeline/digest; chỉ stub model proposals. Close reducer/worker trong finally.
- [ ] **7.3 Giữ nguồn từ ingress:** user ledger ref và scope của đúng event đi vào hot path. Assistant commit dùng verified utterance/delivery ID cùng parents, không dựng nguồn user từ đoạn assistant nói. bootstrap.on_state truyền runtime provenance của observation gốc, thay vì chỉ confidence/evidence string của model.
- [ ] **7.4 Chặn tại durable owner:** trong MemoryCognitionCore commit permission/admission boundary, factual targets cần nguồn supported đúng subject/scope; assistant/summary có thể lưu fact 'Eris đã nói X' hoặc candidate uncertain, không tự nâng X thành fact user. Missing ancestry/scope thì reject hoặc giữ candidate theo target hiện có; không phát minh safe factual rewrite. Retrieval admission kiểm legacy record lần nữa, nhưng không thay thế commit check.
- [ ] **7.5 Bảo toàn metadata qua serialize/merge:** source wrapper → observation request → StateEvent → digest plan → StateCommitRequest → canonical record → reload → registry. Parent ancestry được resolve; cycle/unresolved parent không trở thành corroboration. Không cộng các bản summary như các nguồn thế giới độc lập. Policy cho hypothetical/quoted hiện có được tái sử dụng.
- [ ] **7.6 Kiểm pass/bàn giao:** chạy lại 7.2 và python -m pytest -q src/tests/test_memory_hygiene_v41.py src/tests/test_memory_authority_v41.py src/tests/test_living_user_memory.py. Trace một assistant false-promise và một user true-promise tới persisted records; cùng câu chữ nhưng nguồn khác phải cho kết quả khác.

## Task 8 — Replay không học hai lần; correction giữ được sau reload

**Phụ thuộc:** 7. **Bàn giao:** M1–M6 được kiểm qua canonical store/retrieval thật; sửa fact vẫn giữ lịch sử diễn giải và cảm xúc riêng.

**Files:** memory/semantic_memory.py:apply_commit/_merge_item/to_dict/from_dict; memory_kernel.py:correct_user_memory; living_user/service.py và authority.py khi trace yêu cầu. Mở rộng test_character_memory_provenance_v1.py; giữ existing correction/permission policies.

**Interface:** không mở correction store. Giữ root_event_ids và correction links trong canonical metadata Task 7; merge lineage trước reinforcement. API correction của LivingUser/MemoryKernel hiện có xử lý path hỗ trợ; correction tổng quát ngoài path đó là metadata link ở record owner, không phải một parser đoán mọi câu user thành correction.

- [ ] **8.1 Viết test:** test_same_root_replay_does_not_raise_observations_or_confidence; test_summary_of_same_root_is_not_new_evidence; test_replay_after_reload_is_idempotent; test_genuine_new_user_source_remains_admissible; test_correction_supersedes_active_claim_and_preserves_history; test_corrected_record_is_retrieved_after_new_core_reload; test_projection_verification_and_duplicate_receipts_do_not_learn_traits.
- [ ] **8.2 Chạy fail:** python -m pytest -q src/tests/conversation/test_character_memory_provenance_v1.py. Case same-root phải bắt sự tăng observations/confidence hiện tại, không chỉ evidence_ids trùng.
- [ ] **8.3 Sửa merge tối thiểu:** resolve roots từ parent refs trước kiểm novelty. Cùng root set không tăng observations/confidence; metadata/lineage cập nhật cần thiết vẫn được giữ. Root chưa resolve không được coi là bằng chứng mới độc lập. Dedupe bền nằm trong record/store metadata, không chỉ RAM processed_memory_turns hoặc UUID envelope/mutation mới. Existing evidence_ids bị cắt cho context không được dùng làm danh sách dedupe đầy đủ; thiếu durable ancestry thì không tự reinforce.
- [ ] **8.4 Giữ correction:** dùng previous_claims/previous_values/superseded_facts/current_value và authority paths đã có. Gắn corrects_claim_ref/root source vào correction; retrieval chọn claim active mới, lịch sử cũ vẫn có role và thời điểm. Không xóa assistant ledger nói sai; correction false từ user vẫn được đối chiếu, không tự nhận sai. Cảm xúc/interpretation không bị tự reset theo factual correction.
- [ ] **8.5 Kiểm persistence:** enqueue cùng event hai lần, drain; tạo core/kernel mới cùng tmp_path; replay rồi so observations/confidence/roots/current value. Không chỉ gọi load trên object còn cache. Đối chứng user event mới khác nguồn vẫn ingest được và real promise không bị xóa cùng false promise.
- [ ] **8.6 Kiểm pass/bàn giao:** chạy lại 8.2 và python -m pytest -q src/tests/test_turn_memory_pipeline.py src/tests/test_memory_authority_v41.py src/tests/test_living_user_memory.py src/tests/test_open_ended_personality_v12.py. Bàn giao trace M1–M6, before/after persisted metadata và tất cả giới hạn ở legacy records.

## Task 9 — Giọng nhân vật và benchmark đối chiếu có người chấm

**Phụ thuộc:** 0–8. **Bàn giao:** đánh giá V1 theo cá tính, tính toàn vẹn và chi phí; không claim V2 tự hình thành gu đã xong.

**Files:** eris_voice_contract.py; conversation/agent.py:_system_prompt chỉ khi benchmark chỉ ra Root chọn ý sai; runner/fixtures Task 0. Bổ sung src/tests/test_v38_infj_voice_contract.py nếu thay voice contract; không dùng tests tìm keyword 'ghen/cute' làm thước chất lượng.

**Interface:** giữ move/after contracts. Prompt nhận facets/conflicts/provenance đã có; không hardcode thoại cho S01–S10. run_scenarios output schema bổ sung verdict/receipt/segment/provenance telemetry khi candidate path đã có; baseline thiếu trường vẫn ghi unavailable rõ ràng.

- [ ] **9.1 Ghi các assertions trước chỉnh prompt:** technical goal không reset character; niềm vui/hài hước không bị phủ bởi mọi negative state; đối thoại trưởng thành trả đúng ý hỏi; quan điểm có thể phản bác user; nhân vật có thể chậm nhận lỗi cảm xúc; S09 không bắt redemption. Factual correction và memory scope vẫn theo Tasks 6–8.
- [ ] **9.2 Kiểm regression trước chỉnh:** python -m pytest -q src/tests/test_v38_infj_voice_contract.py src/tests/test_v39_affective_voice_state.py src/tests/test_open_ended_personality_v12.py src/tests/conversation. Nếu đã pass, không viết thêm tests chỉ mirror một đoạn prompt; chất lượng lời nói chấm bằng benchmark.
- [ ] **9.3 Chỉnh đúng tầng gây lỗi:** mất facet → Task 2; Root chọn động cơ khác → Root prompt; writer làm phẳng giọng → voice/writer prompt; gate chặn ý kiến hợp lệ → Task 5; history sai → Task 7/8. Ghi lỗi provider refusal riêng, không dùng policy bypass. Chỉ sửa prompt cần thiết; không thay baseline personality của tài khoản thật.
- [ ] **9.4 Chạy stub acceptance:** python -m tools.run_eris_character_eval --mode stub --scenario-file src/tests/fixtures/eris_character_v1/scenarios.json --runs 1 --label candidate-stub --output-dir scratch/eris-character-v1/candidate-stub. Kiểm đủ telemetry/reload/counterparts; stub pass không thay model-quality evidence.
- [ ] **9.5 Chạy/chấm model khi có yêu cầu phù hợp ở session triển khai:** cùng model/settings/fixtures baseline, **10 tình huống × 3 run**, thêm các counterpart tương ứng. Label baseline/candidate được đảo/mù khi chấm. Bốn trục 0–2: đúng cá tính, theo state, tự nhiên/đa dạng, đúng ý hỏi. Chấm cả transcript nhiều lượt và memory/receipt trace; ghi gate false positives/false negatives bằng đối chiếu người chấm. Không dùng checker tự chấm chính nó làm chứng cứ duy nhất.
- [ ] **9.6 Nghiệm thu:** deterministic invariants đạt 100% fixture suite; bộ benchmark quan sát không có factual/scope failure nghiêm trọng; mỗi scenario đạt ít nhất 1 ở bốn trục trong ít nhất 2/3 run; ít nhất 6/10 cải thiện tổng điểm so baseline và không giảm chính xác kỹ thuật. Báo latency p50/p95, số calls/retries, tỷ lệ fail/unknown và ví dụ transcript. Không đặt số SLO giả hoặc gọi 30 run là bằng chứng hết hallucination.
- [ ] **9.7 Bàn giao cuối:** diff, exact commands/results, source fingerprints, report/transcripts, known limitations và phần V2 còn lại. Nếu baseline/model eval chưa chạy, ghi 'đã kiểm cơ học, chưa nghiệm thu chất lượng model'; không đánh dấu V1 hoàn thành toàn bộ.

## Thứ tự và các mốc kiểm

| Mốc | Tasks | Điều kiện chuyển tiếp |
| --- | --- | --- |
| Baseline | 0 | Harness/fixtures và fingerprints có thật; model scores chỉ ghi khi đã chạy |
| Truyền cá tính | 1 → 2; 3 sau 1 | Snapshot/refs nhất quán; portrait không mất vế conflict/cost; manifest không laundering |
| Public integrity | 4 → 5 → 6 | Final UI/transcript/segments đúng receipt; một rewrite; no-send/no-commit khi fail/unknown |
| Memory integrity | 7 → 8 | Durable source/scope/correction/ancestry; replay không reinforce |
| Chất lượng nhân vật | 9 | Regression và paired benchmark đạt; còn thiếu thì báo đúng mức |

Không triển khai Task 6 trên một prepare boundary chưa giữ được TTS/UDP cuối. Không chỉnh prompt để che source/commit errors. Nếu một adapter không thể giữ final transcript hoặc nguồn, đánh dấu boundary chưa đạt thay vì cho qua và ghi 'đã verify'.

## Thiết lập và commands cho executor sau này

Chạy từ repository root. pytest.ini đã đặt pythonpath = src . và testpaths = src/tests. Python hiện tìm thấy là Python312/python.exe; pyproject yêu cầu ≥3.11. Dùng interpreter/venv của repo khi triển khai, không đổi Python hệ thống.

- Kiểm interpreter/dependencies: python --version và python -m pytest --version. Nếu môi trường triển khai thiếu dev dependencies, setup theo pyproject bằng python -m pip install -e ".[dev]" trong môi trường được chọn; không làm việc này trong lượt nghiên cứu hiện tại.
- Mỗi task dùng focused commands ở trên. Sau Task 6 chạy nhóm conversation/transport/grounding; sau Task 8 chạy nhóm memory; sau chỉnh prompt chạy nhóm voice. Không lặp full suite khi không có thay đổi hoặc lỗi mới.
- Mỗi milestone ghi git diff --check, git status --short và diff file liên quan. Không git add toàn repo hoặc commit các thay đổi của user chỉ để đánh dấu task.
- Mọi test dùng tmp_path cho DB/memory/profile và fake provider/transport/audio. Fixture nào tạo StateReducer/worker phải close/stop trong finally.
- Configured benchmark có thể gọi model thật và tiêu chi phí; session triển khai phải có yêu cầu tương ứng. Không khởi chạy chỉ vì command được in trong kế hoạch.

## Coverage của đặc tả và tự rà kế hoạch

| Yêu cầu spec | Task/bằng chứng |
| --- | --- |
| Mục 1–4: mục tiêu, phạm vi, nhân vật có khuyết điểm | Constraints; fixtures 0; portrait 2; giọng/benchmark 9 |
| Mục 5–6: ownership, snapshot và projection | 1–2; immutable output và sample affect một lần |
| Mục 7.1: cấu trúc/nguồn manifest | 1, 3; raw status, runtime allowlist, ref đúng scope |
| Mục 7.2: semantic coverage hai surface | 4–5; final segments, manifest trong verifier input, span/runtime validation |
| Mục 7.3: retry/failure/commit | 6; shared rewrite budget, receipt, generation và acceptance |
| Mục 8: M1–M6 | 7–8; canonical durable integration, correction/replay sau new-core reload |
| Mục 9: S01–S10 và đối chứng | 0, 9; fixtures/paired transcripts không có câu trả lời mẫu |
| Mục 10–12: file map, tests, bàn giao không tự đoán | Mỗi task có files/interfaces/assertions/commands; audit mức evidence rõ |
| Mục 13: V2 | Để ngoài kế hoạch; không đạt replay rồi claim trải nghiệm end-to-end |

Đã tự rà các interface giữa tasks: registry/portrait ở Tasks 1–2; manifest ở Task 3; prepared artifact/transcript ở Task 4; result/receipt ở Task 5; wiring ở Task 6; claim_provenance ở Task 7; persisted lineage/correction ở Task 8; runner ở Task 0/9. Các types mới được khai tại task tạo chúng. Các điều kiện Review Focus đều có test owner.

Kế hoạch kết thúc ở tài liệu có thể review. Việc bắt đầu viết code là một yêu cầu công việc riêng sau này; hiện chưa chọn hoặc thực hiện phương thức triển khai nào.

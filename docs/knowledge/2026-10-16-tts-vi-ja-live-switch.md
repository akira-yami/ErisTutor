# Đổi giọng Việt/Nhật live — 16/10/2026

User yêu cầu chọn ngay trong settings bubble; bản Việt không được nhận tiếng
đệm, đuôi câu hoặc filler dự phòng bằng tiếng Nhật. Đổi ngôn ngữ dừng audio cũ;
câu đang chuẩn bị bằng ngôn ngữ cũ không được phát muộn.

## Hành vi và phạm vi thay đổi

- Basic có dropdown Ngôn ngữ giọng. Nhãn hiển thị dùng metadata `vi`/`ja`/`en`
  cho wire protocol. Chọn ngôn ngữ chỉ gửi patch language, không ghi đè provider
  hay voice bằng trạng thái UI có thể đã cũ. ACK đồng bộ lại dropdown.
- `tts.elevenlabs_voice_id_vi` mặc định `0ggMuQ1r9f9jqBu50nJn`;
  `tts.elevenlabs_voice_id_ja` để trống thì dùng voice/env Nhật cũ. Profile riêng
  thắng voice ID toàn cục để env cũ không giữ voice Nhật khi chọn Việt.
- Lựa chọn language đã persist thắng env bootstrap khi mở lại runtime.
  Env vẫn dùng cho cấu hình legacy chưa có lựa chọn hợp lệ.
- Writer lấy language trước request, kiểm lại sau generate/localize và trước
  dispatch. Envelope legacy nhận đúng speech language. Mode Việt đồng bộ cả
  field phẳng, speech nested, utterance và locale về transcript public Việt.
- Hot reload thu hồi speech epoch, hủy backend cũ. Worker đã shutdown không
  có quyền phát kết quả muộn; worker mới không nhận sealed transcript thuộc
  ngôn ngữ khác. Prefix Nhật trong mode Việt bị loại trước cache lookup.
- Localizer Nhật, signature ending và filler JA rescue được giữ trong mode
  Nhật. Cue Việt dùng bản Việt; không xóa nội dung Nhật được user trích dẫn
  một cách máy móc khỏi public text. Live2D vẫn theo chính audio playback.

## Kiểm chứng

- Trước sửa: 32 test nền liên quan qua. Test mới đã quan sát fail đúng hành vi
  thiếu: voice bị env JA giữ, lựa chọn bị reset sau restart, speech Việt bị gắn
  nhãn JA, cached prefix Nhật lọt vào mode Việt và audio/candidate cũ còn quyền.
- Sau sửa: **178 passed** trong nhóm speech/TTS, writer, settings, interruption
  và Live2D. Có kiểm tra đối xứng rằng mode Nhật vẫn nhận cached prefix Nhật.
- C# build thành công, 0 warning/0 error. Godot export-debug và
  `build_dev_fast.bat` exit 0, sanity check qua. Output: `Documents/eris`.
- Smoke bằng chính Python portable của output: chuyển JA → VI → JA qua,
  mode Việt bỏ speech filler Nhật và reload vẫn giữ language đã chọn. SHA-256
  9 file source triển khai khớp checkout; assembly export khác bản sao lưu cũ.
- Full suite thu thập 2.245 test, ghi nhận failure rồi không hoàn tất vì
  `test_run_20260811_repairs.py::test_forced_ocr_refresh_does_not_wait_forever_on_busy_capture_lock`.
  Faulthandler của lượt follow-up chỉ vào `screen/ocr_worker.py:449`.
- Đối chứng 56 node ID đã ghi nhận lỗi ở lượt rộng: chạy riêng còn 53 fail,
  3 pass. Nạp lại **7 module Python nguyên bản đã sao lưu trước sửa**, giữ các
  thay đổi ngoài tác vụ trong checkout, cũng 53 fail/3 pass; tập failure trùng
  nhau. Đối chứng này không phải clean checkout toàn repo; các test đọc source
  trực tiếp vẫn đọc checkout. Không kết luận toàn suite qua hoặc sửa các lỗi đó.

Log và backup của lần triển khai nằm tại
`C:/Users/Gtvkun/AppData/Local/Temp/eris-vi-ja-tts-20261016/`:
`pytest-speech.log`, `pytest-full.log`, `pytest-followup.log`,
`current-failures.xml`, `baseline-failures.xml`, `godot-export.log`,
`dev-build.log`, `previous-export/` và các module trước thay đổi.

## Giới hạn

Chưa gọi synthesis live, chưa xác nhận voice này dùng được với tài khoản/pool
đang phục vụ hoặc đánh giá giọng/âm sắc bằng nghe thử. Test fake transport và
cache chứng minh contract code; không bảo đảm mô hình TTS không tự sinh âm ngoài
ý muốn. Bản đang mở cần nạp lại build một lần; đổi setting sau đó là live.

## Failure tái hiện trong đối chứng

```text
src.tests.conversation.test_action_claim_integrity_v12::test_writer_allows_effect_supported_by_runtime_registry
src.tests.conversation.test_action_claim_integrity_v12::test_writer_manifest_empty_does_not_bypass_action_guard
src.tests.conversation.test_action_claim_integrity_v2::test_writer_checks_target_independent_of_manifest
src.tests.conversation.test_action_claim_integrity_v2::test_writer_checks_tts_japanese_action_type
src.tests.conversation.test_character_public_claim_gate_v1::test_empty_manifest_does_not_turn_unverified_action_into_phatic
src.tests.conversation.test_root_session_bootstrap_v12::test_root_preflight_allows_large_self_but_writer_cap_unchanged
src.tests.runtime_scenarios.test_long_runtime_scenarios::test_background_monitor_does_not_steal_attention_until_state_changes
src.tests.runtime_scenarios.test_long_runtime_scenarios::test_long_engagement_survives_fifteen_minutes_and_restart
src.tests.subject.test_executive_center_v9::test_user_ingress_no_longer_injects_fixed_action_scores
src.tests.test_20260813_authority_grounding_boundary_hotfix::test_danger_zone_ui_has_client_side_preview_coalescing
src.tests.test_adaptive_companion_integration_v91::test_v91_companion_context_is_placed_before_other_discourse_for_budget_priority
src.tests.test_adaptive_companion_integration_v91::test_v91_orchestrator_injects_companion_context_without_eris_code_dependency
src.tests.test_agent_harness_v2::test_new_depth_skills_are_progressive_and_reveal_real_tools
src.tests.test_agent_harness_v2::test_skill_search_is_progressive_and_activation_reveals_tools
src.tests.test_capability_panel_body_v86::test_v86_body_panel_is_wired_into_desktop_menu_and_transport
src.tests.test_chat_executive_binding_hotfix::test_chat_cycle_resolves_executive_before_use
src.tests.test_chess_bridge_v37::test_waiting_chess_task_asks_again_without_spamming
src.tests.test_chess_bridge_v37::test_waiting_chess_task_prompts_for_board_after_short_grace
src.tests.test_companion_consolidation::test_orchestrator_updates_mentor_evidence_before_companion_selects_current_delivery
src.tests.test_conversation_continuity_hotfix::test_no_phrase_marker_classifier_remains_in_runtime_path
src.tests.test_conversation_continuity_hotfix::test_settings_cooldown_still_uses_int64
src.tests.test_cross_environment_supervisor_v83::test_orchestrator_passes_canonical_world_state_projection_to_mentor
src.tests.test_deep_environment_native_v2::test_v61_semantic_worker_coalesces_same_meaning_wake_storm
src.tests.test_deep_runtime_architecture::test_bootstrap_has_bounded_explicit_phases
src.tests.test_deep_runtime_architecture::test_coordinator_is_a_thin_composition_root
src.tests.test_deepseek_multi_worker_base::test_research_client_no_longer_falls_back_to_main_or_fallback
src.tests.test_deepseek_multi_worker_base::test_surface_worker_ignores_example_placeholder
src.tests.test_deepseek_multi_worker_base::test_worker_spec_uses_exact_role_environment
src.tests.test_engagement_evidence_routing::test_activity_evidence_routes_to_matching_engagement_only
src.tests.test_engagement_evidence_routing::test_bound_domain_event_reaches_background_monitor
src.tests.test_engagement_lifecycle_v18::test_inactivity_policy_pauses_foreground_but_monitor_can_keep_running
src.tests.test_engagement_lifecycle_v18::test_targeted_pause_and_resume_preserves_background_focus
src.tests.test_engagement_profiles_v14::test_runtime_can_register_profile_and_keep_it_in_planner_context
src.tests.test_engagement_runtime::test_commentary_cadence_uses_engagement_policy_as_anchor
src.tests.test_engagement_runtime::test_domain_event_updates_adapter_projection_and_triggers_policy
src.tests.test_engagement_runtime::test_engagement_remains_anchored_beyond_ten_minutes
src.tests.test_engagement_runtime::test_registry_supports_foreground_and_background_engagements
src.tests.test_eris_code_decoupling_v91::test_v91_extension_activation_has_no_desktop_bridge_dependency
src.tests.test_eris_code_decoupling_v91::test_v91_extension_manifest_has_no_eris_link_settings
src.tests.test_eris_code_decoupling_v91::test_v91_gitpilot_client_does_not_read_or_publish_eris_desktop_context
src.tests.test_identity_panel_v78_elegant::test_identity_panel_v78_main_navigation_does_not_duplicate_other_panels
src.tests.test_identity_panel_v87_composition::test_v87_self_is_one_composition_not_nested_dashboard_cards
src.tests.test_live2d_profile_stage_contract::test_stage_mouth_response_uses48_opening64_closing
src.tests.test_memory_panel_ui_v41::test_memory_panel_uses_compact_responsive_empty_state
src.tests.test_pdf_inference_refactor::test_acceptance_report_computes_optional_429_reduction_from_baseline
src.tests.test_pdf_inference_refactor::test_acceptance_report_v3_covers_full_pdf_metrics
src.tests.test_pdf_inference_refactor::test_live_semantic_authority_audit_is_clean
src.tests.test_pdf_inference_refactor::test_remote_inference_static_audit_is_clean
src.tests.test_pdf_inference_refactor::test_semantic_paraphrase_evaluator_scores_predictions
src.tests.test_pdf_inference_refactor::test_wording_retry_reuses_locked_semantic_frame
src.tests.test_project_trajectory_v88::test_v88_panel_source_contains_project_statistics_section
src.tests.test_provider_resilience_v96::test_chat_orchestrator_installs_one_foreground_deadline_for_whole_turn
src.tests.test_random_chat_seed_reservoir_v28::test_seed_role_has_own_default_port
```

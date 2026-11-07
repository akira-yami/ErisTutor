# Live2D and ElevenLabs playback verification

Date: 2026-10-14 (Asia/Saigon)

## Result

Live2D speech mouth movement is owned by actual audio playback events. Silent,
sparse, or missing amplitude data no longer starts a synthetic speech rhythm.
Text decisions and generic speaking hints cannot restart or stop an audio clip.
Completion, interruption, and the known WAV duration close the mouth. Both TTS
paths share one volume-adjusted waveform between RMS analysis and playback.

The four changed production Python files were copied to
`C:/Users/Gtvkun/Documents/eris/src` and checked for matching bytes and valid
Python syntax. Originals are backed up at
`C:/Users/Gtvkun/Documents/eris/.eris_home/backups/live2d-lip-sync-20261014-8857c145`.
Restarting Eris loads the new Python code.

## Validation

- New renderer regressions: initial 8 failures reproduced before the fix.
- Manager regressions reproduced cancellation, stale callbacks, no-start
  completion, and changing-volume waveform mismatches before their fixes.
- Playback regressions reproduced premature start, cancellation, repeated
  fallback playback, missing metadata, and separate volume snapshots before
  their fixes.
- Combined relevant suite: **172 passed**.
- Deployed portable Python: synthetic WAV sound/silence and playback completion
  verified without real device playback.
- `git diff --check`: passed.
- Independent review: no remaining Critical or Important findings.

Combined command:

```powershell
python -m pytest src/tests/test_live2d_audio_lifecycle.py src/tests/test_live2d_speech_animation.py src/tests/test_live2d_affect_idle.py src/tests/test_live2d_body_switch.py src/tests/test_tts_manager_playback_lifecycle.py src/tests/test_tts_playback_lip_sync.py src/tests/test_tts_prosody_continuity.py src/tests/test_20260813_tts_hallucination_hardfix.py src/tests/test_architecture_c_runtime_regressions.py -q
```

## Broader suite limitations

An unrestricted suite run reached approximately 60%, showed failures, then
stopped progressing for more than three minutes. It was interrupted; Windows
closed the plain-pipe process without a pytest summary.

A bounded rerun (`python -m pytest -q --maxfail=10 --tb=no`) completed with
**537 passed, 10 failed, 2 skipped**. The two skips require the unavailable
`langgraph` package. All ten failed tests below also failed when run on an
archived copy of original commit `1653ef191bc301e5bfd07695638ac88535848a1e`,
without the lip-sync changes. No claim is made that the full suite is passing.

1. `src/tests/conversation/test_root_session_bootstrap_v12.py::test_root_preflight_allows_large_self_but_writer_cap_unchanged`
2. `src/tests/runtime_scenarios/test_long_runtime_scenarios.py::test_long_engagement_survives_fifteen_minutes_and_restart`
3. `src/tests/runtime_scenarios/test_long_runtime_scenarios.py::test_background_monitor_does_not_steal_attention_until_state_changes`
4. `src/tests/subject/test_executive_center_v9.py::test_user_ingress_no_longer_injects_fixed_action_scores`
5. `src/tests/test_20260813_authority_grounding_boundary_hotfix.py::test_danger_zone_ui_has_client_side_preview_coalescing`
6. `src/tests/test_adaptive_companion_integration_v91.py::test_v91_orchestrator_injects_companion_context_without_eris_code_dependency`
7. `src/tests/test_adaptive_companion_integration_v91.py::test_v91_companion_context_is_placed_before_other_discourse_for_budget_priority`
8. `src/tests/test_agent_harness_v2.py::test_skill_search_is_progressive_and_activation_reveals_tools`
9. `src/tests/test_agent_harness_v2.py::test_gemini_executor_accepts_task_locked_workspace_without_mutating_default`
10. `src/tests/test_agent_harness_v2.py::test_new_depth_skills_are_progressive_and_reveal_real_tools`

## Device timing

Start is marked after the first successful PCM submission. Hardware output
latency is not measured. No ElevenLabs request or real audio-device playback
was performed during this change, so listening on the user's device remains
necessary to assess the remaining device latency.

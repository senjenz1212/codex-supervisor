# TDD Plan and Observed RED/GREEN

## Tracer 1: Captured nested Codex terminal

Test:
`test_captured_nested_codex_rollout_normalizes_and_reaches_terminal`

RED:
the captured `event_msg.payload.type=task_complete` left the run `running`.

GREEN:
nested event kinds normalize to the shared taxonomy; `turn.completed` changes
the run to `completed` and enqueues `evaluate_run`.

## Tracer 2: Captured nested Claude terminal

Test:
`test_captured_nested_claude_rollout_normalizes_and_reaches_terminal`

Result:
the general normalizer from tracer 1 immediately handled nested
`assistant.message.stop_reason=end_turn` plus tool-use/tool-result entries.

## Tracer 3: Submission registration and joins

Test:
`test_submission_registers_target_session_and_joins_rollout_to_workflow`

RED:
`state.get_run(workflow_run_id)` returned `None` after submission.

GREEN:
submission co-registers the workflow/target session, writes the sidecar, and
routes every captured rollout row to the workflow run and task.

## Tracer 4: Explicit and retry-stable target identity

Tests:
- `test_submission_explicit_target_session_overrides_environment`
- `test_same_token_reattach_preserves_original_target_session`

RED:
the API rejected `target_session_id`; after adding it, a same-token retry
returned and wrote the second supplied session instead of the reserved one.

GREEN:
MCP/API and AXI accept explicit identity, and reattach reads the original
reserved request before writing registration metadata.

## Tracer 5: Path-independent semantic drift

Test:
`test_goal_abandonment_inside_allowed_files_opens_semantic_adjudication`

RED:
only an L1 verdict existed because zero findings triggered the L1 early return.

GREEN:
L2 and triggered L3 execute independently of L1; low similarity and abandoned
plan signals enqueue adjudication with `scope_violations=0`.

## Tracer 6: OpenCode normalization

Test:
`test_opencode_events_normalize_to_shared_taxonomy`

RED:
`message.updated` with completed assistant metadata remained
`message.updated`.

GREEN:
completed assistant messages normalize to `turn.completed`; session and
message-part events normalize to run/tool lifecycle kinds.

## Regression set

- `tests/test_obs_001_observability.py`
- `tests/test_rollout_watcher_live.py`
- `tests/test_telegram_progress_streaming.py`
- `tests/test_drift_detector_rewire.py`
- `tests/test_drift_replay.py`
- focused workflow submission and AXI tests

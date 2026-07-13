# TDD Plan: RUNTIME-001

## Existing Public-Boundary Tests

1. `test_provider_runtimes_return_the_same_target_independent_result_schema`
   runs both runtime classes against the same transport contract.
2. `test_runtime_resume_and_cancel_stay_behind_the_runtime_seam` exercises
   lifecycle methods through `CodexRuntime`.
3. `tests/test_model_client.py` covers completion shape, valid structured
   output, and fail-closed invalid output.
4. `test_claude_sdk_is_confined_to_a_runtime_transport` injects a fake SDK
   loader behind `ClaudeAgentSdkTransport`.
5. `tests/test_agent_invoker_review.py` verifies `AgentInvoker` can use the
   runtime seam and imports without the optional Claude SDK.

## Remaining RED/GREEN Tracers

1. Source-scan test: direct provider imports in core modules fail.
2. Migrate one direct drift-adjudication call through `ModelClient`; preserve
   its current result schema.
3. Migrate hook and Telegram lifecycle through `AgentRuntime`.
4. Migrate Cursor/OpenAI reviewer calls through a model/runtime adapter.
5. Run real no-op tasks on installed Claude Code and Codex CLIs and compare
   schemas without comparing generated text.
6. Cancel a runtime that forks a child and assert no process-group survivor.

## Verification

```text
.venv/bin/python -m pytest -q \
  tests/test_agent_runtime.py \
  tests/test_model_client.py \
  tests/test_claude_sdk_runtime.py \
  tests/test_agent_invoker_review.py
```

Fake transports prove contracts, not provider availability or live execution.

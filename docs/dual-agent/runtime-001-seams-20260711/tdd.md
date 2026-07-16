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
7. Run a real subprocess that emits one line beyond the stream limit and
   prove collection raises, reaps the process tree, and returns before the
   runtime timeout.
8. Resolve installed-style executable symlinks, hash the final regular file,
   and record the invocation path separately from the resolved target.
9. Reject broken, unreadable, and task-relative symlink-escaping executable
   targets without producing a complete operational manifest.
10. Reject caller-supplied execution-environment attestation metadata; the
    transport owns that evidence boundary.
11. Run both concrete runtime classes through the local subprocess transport
    with controlled provider events. Preserve source-backed model/cost/token
    provenance, then prove `RepositoryArmExecutor` fails closed on each
    unenforced operational pin.

## Verification

```text
.venv/bin/python -m pytest -q \
  tests/test_agent_runtime.py \
  tests/test_arm_executor.py \
  tests/test_model_client.py \
  tests/test_claude_sdk_runtime.py \
  tests/test_agent_invoker_review.py
```

Fake transports prove contracts, not provider availability or live execution.
Controlled local subprocess fixtures prove concrete class/transport wiring,
not container-backed operational readiness. The local backend must remain
pilot-blocking while its attestation reports an uninstantiated image or
unenforced token/cost ceiling.

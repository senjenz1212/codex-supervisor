# TDD Grill Findings

## F1: A helper-only test is insufficient

Testing `_normalise_tool_receipts` alone would not prove the public workflow boundary downgrades caller receipts before gate verification.

Resolution: include both direct helper and `run_dual_agent_workflow` tests.

## F2: The rejection test must prove no execution happened

An unsafe command could both execute and later be marked failed. That still violates the promise.

Resolution: the `python -c` rejection test must use a marker side effect and assert it was not created.

## F3: Env scrub needs runner-level inspection

Checking only a helper function can miss what `subprocess.run` actually receives.

Resolution: add a fake runner that captures `env` and asserts secret-like keys are absent.

## F4: Honest acceptance must stay green

The most likely regression is over-sanitizing the runtime receipts after `collect_runtime_evidence`.

Resolution: preserve and run the existing honest `test_execution_gate_accepts_supervisor_runtime_native_receipts` workflow test.

## F5: Environment-unavailable must be distinct from ordinary failing tests

A user test failure should stay a test failure; missing pytest/interpreter should become an environment-unavailable failure.

Resolution: separate tests for real pass/fail and runner/environment failure.

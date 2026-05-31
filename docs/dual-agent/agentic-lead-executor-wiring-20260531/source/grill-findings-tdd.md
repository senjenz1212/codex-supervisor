# TDD Grill Findings

### Finding 1: First proof must hit workflow execution

status: resolved

question: Are tests only proving `agentic_workers` in isolation?

resolution: The first test is `test_run_dual_agent_workflow_required_policy_spawns_agentic_workers_and_accepts_runtime_native_receipts`, which uses the workflow boundary and observes both executor invocation and P13 acceptance.

### Finding 2: Existing verifier must not be duplicated

status: resolved

question: Could implementation add a second evidence-grade path?

resolution: The TDD plan asserts that executor receipts flow through `verify_dynamic_workflow_receipts`; helper assertions inspect the existing P13 details rather than a new verdict object.

### Finding 3: The negative required-policy case remains mandatory

status: resolved

question: Could the producer wiring accidentally weaken the current fail-closed behavior?

resolution: The second test keeps the no-receipt required path blocked before synthesis, proving the current protection still holds.

### Finding 4: Solo exception regression needs a non-artifact gate

status: resolved

question: Does the solo-exception test distinguish artifact-only from implementation gates?

resolution: The planned test explicitly checks `execution` as the non-artifact gate and a planning review gate as the allowed artifact-only comparison.

### Finding 5: Detached transport coverage is scoped to payload preservation

status: resolved

question: Does the CLI test imply raw MCP reconnect work?

resolution: The planned test covers submit/poll payload round-trip only. Raw transport reconnect remains out of scope and is not named as an acceptance result.

### Finding 6: Payload preservation is a regression guard, not fake RED

status: resolved

question: Does the TDD plan claim a missing submit/poll path that already exists?

resolution: The submit/poll test is explicitly framed as characterization/regression coverage. It must not claim that already-wired fields are a new RED failure unless implementation discovers a concrete dropped-field path.

### Finding 7: Lead-declared evidence grade must be ignored

status: resolved

question: Does the TDD plan prove that a non-supervisor path cannot self-stamp `runtime_native`?

resolution: The TDD plan adds `test_declared_runtime_native_from_non_supervisor_path_is_downgraded_and_blocked`, which feeds declared `runtime_native` outside `.handoff/agentic-workers/` and expects downgrade plus required-grade block.

### Finding 8: Read-only contract must be singular

status: resolved

question: Does the plan both coerce and reject writable roster specs?

resolution: The PRD and TDD now choose rejection before subprocess launch. This keeps the failure visible and avoids silently changing a lead-planned worker into a different permission contract.

### Finding 9: Readable docs artifacts must not over-credit worker provenance

status: resolved

question: Does `docs/dual-agent/` count as runtime-native subagent worker evidence?

resolution: The TDD plan adds `test_docs_dual_agent_refs_do_not_count_as_runtime_native_worker_evidence`. Runtime-native subagent worker evidence should come from supervisor-owned worker/job paths, while `docs/dual-agent/` remains an audit projection and replay manifest surface.

# TDD Grill Findings: Defense-In-Depth Hardening

### Finding 1: First RED tests must hit public boundaries

Status: resolved

The TDD plan now names `validate_planning_artifacts`, `run_no_mistakes_validation`, `parse_no_mistakes_output`, and `verify_dynamic_workflow_receipts` as the first test boundaries. Helper scoring functions may be tested later, but the planned red tests exercise the same surfaces used by gates.

### Finding 2: Fan-out-free regression must be explicit

Status: resolved

The evidence-grade tests now include the invariant that fan-out-free runs remain unaffected. This protects the no-default-change promise while still hardening fan-out evidence when receipts are present.

### Finding 3: Advisory no-mistakes behavior needs a separate test

Status: resolved

The TDD plan includes both required and advisory malformed-output tests. That prevents required fail-closed behavior from accidentally making advisory mode a primary gate.

### Finding 4: Tamper reason should not swallow non-tamper invalid receipts

Status: resolved

The dynamic receipt test is scoped to sha256/output hash mismatch. Existing missing receipt tests continue to cover normal invalid or incomplete receipt cases.

### Finding 5: Rubric-unavailable policy needs its own RED test

Status: resolved

The TDD plan now includes `test_planning_rubric_unavailable_follows_policy_and_never_silently_passes`, mapped directly to P2. It drives the unavailable-rubric path at `validate_planning_artifacts` and asserts that missing rubric output cannot become a silent acceptance.

### Finding 6: Live runner wiring and preservation ACs must be traceable

Status: resolved

The implementation plan now names `supervisor/dual_agent_runner.py`, and the TDD plan includes `test_dual_agent_runner_records_planning_rubric_config_in_validation_event` so the live `dual_agent_planning_validation` event path is covered. The implementation-plan traceability also cites D2-AC4 legacy plain-text fallback and D3-AC4 explicit `runtime_native` passthrough as preserved regression guards.

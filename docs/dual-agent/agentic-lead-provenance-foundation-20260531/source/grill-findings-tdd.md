# TDD Grill Findings

## Finding 1: First Proof Must Hit MCP, Not Helper Functions

- status: resolved
- severity: blocking
- related tests: `test_agentic_required_blocks_solo_execution_before_lead`
- resolution: required-policy enforcement is first tested through `run_dual_agent_workflow`, with the fake runner asserted not invoked.

## Finding 2: Grade Tests Must Prove Declared Grade Is Ignored

- status: resolved
- severity: blocking
- related tests: `test_agentic_evidence_grade_ignores_lead_declared_grade`
- resolution: fixture declares `runtime_native` but lacks supervisor-owned refs; expected grade is lower and P13 blocks.

## Finding 3: Runtime Fields Must Use Existing Dynamic Receipt Path

- status: resolved
- severity: important
- related tests: `test_agentic_required_accepts_supervisor_owned_runtime_native_receipts`
- resolution: tests use `dynamic_subagent_result` and P13, not a parallel receipt kind.

## Finding 4: Eval Report Must Not Flip Defaults

- status: resolved
- severity: important
- related tests: `test_agentic_eval_report_compares_required_modes`
- resolution: harness emits comparison data only; defaults remain `off`/`lead_direct`.

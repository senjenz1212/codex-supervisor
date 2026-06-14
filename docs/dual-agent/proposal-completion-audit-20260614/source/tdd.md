# TDD Plan: Proposal Completion Audit

## Public Boundary

The first proof boundary is the supervisor workflow itself: `run_dual_agent_workflow` executed through the detached workflow worker and observed with `codex-supervisor-axi poll` or `catch-up`. The report-only audit has no production-code mutation path.

## Tests And Checks

### test_planning_artifacts_pass_all_route_gates

Maps to: Slice 1, P4

RED: With generated source stubs, `validate_planning_artifacts` blocks at `prd_review` with `P_planning:red`.

GREEN: After replacing source artifacts, local validation passes for `prd_review`, `issues_review`, `tdd_review`, `implementation_plan`, `execution`, and `outcome_review`.

### test_report_classifies_proposal_completion_from_code

Maps to: Slice 2, P1

RED: A report that only lists high-level proposal names without file references, daemon wiring evidence, or ledger state is rejected as self-reported narrative.

GREEN: The report includes code references, commands run, and status classifications for AutoResearch drafts, runnable experiments, policy proposal derivation, overlay liveness, rollback/regression watch, lessons hygiene, weekly P11 audit, runtime evidence floor, and AXI detached-dispatcher behavior.

### test_report_only_audit_does_not_mutate_policy_or_queue

Maps to: Slice 2, P2

RED: Running the audit changes `.supervisor/policy-overlay.yaml`, changes experiment statuses, approves proposals, denies proposals, or applies rollback.

GREEN: Pre-run and post-run overlay hash and queue/proposal counts are unchanged except for supervisor ledger events and exported audit artifacts.

### test_liveness_metrics_are_not_overclaimed

Maps to: Slice 2, Slice 3, P3

RED: The report treats incident share by era as a reliability rate or declares CLI migration ready without a per-run or per-poll denominator.

GREEN: The report labels D1 undecidable unless normalized denominator data exists, and names the exact missing metric if the current trend output is insufficient.

### test_terminal_gate_status_is_reported_truthfully

Maps to: Slice 2, P4

RED: A local-only audit report says the workflow passed without terminal accepted gate evidence.

GREEN: The exported outcome states the actual terminal workflow status, reviewer decisions, and any remaining blockers. If the gate is blocked, the final user report says blocked and does not claim acceptance.

## Command Evidence

- `git status --short`
- `.venv/bin/codex-supervisor-axi --json doctor`
- `.venv/bin/codex-supervisor-axi --json experiments list --limit 100`
- `.venv/bin/codex-supervisor-axi --json trends`
- `rg` and `sed` source inspections for daemon wiring, proposal derivation, overlay reads, trend rows, lessons hygiene, and AXI commands.

## Exit Criteria

The audit is complete only when every promise P1 through P4 has either accepted evidence in the outcome report or a named blocker. The operator-facing answer must not claim success beyond the supervisor gate verdict.

# TDD Plan: no-mistakes External Validator

## Public Boundary

Use `run_no_mistakes_validation`, `Config`, `workflow_kwargs_from_payload`, and
`run_dual_agent_workflow`. Tests use fake subprocess runners so the suite does
not require a live no-mistakes binary.

## Test Cases

### test_no_mistakes_config_defaults_are_safe

Maps to: ISS-1, P2
RED: Load supervisor config and observe that no-mistakes is missing or uses
shipping-enabled defaults.
GREEN: Add `NoMistakesCfg` with policy off, skip steps push/pr/ci, no auto yes,
and shipping steps disabled.

### test_no_mistakes_adapter_builds_safe_default_command

Maps to: ISS-1, P1, P2
RED: Adapter cannot construct the expected external command or passes `--yes`.
GREEN: Build `no-mistakes axi run --intent <intent> --skip=push,pr,ci` and
capture subprocess output through a fake runner.

### test_no_mistakes_auto_yes_requires_shipping_opt_in

Maps to: ISS-1, P2, P4
RED: `auto_yes=true` in advisory mode passes `--yes` and lets a local validator
answer prompts in a non-shipping path.
GREEN: `--yes` is emitted only when policy is `shipping` and shipping steps are
explicitly allowed; advisory/required paths stay non-interactive by default.

### test_no_mistakes_missing_binary_policy_matrix

Maps to: ISS-1, P2
RED: Missing binary raises an exception or crashes workflow validation.
GREEN: Advisory returns unavailable/skipped; required returns required blocked
with `no_mistakes_binary_unavailable`.

### test_no_mistakes_adapter_parses_outcome_and_gate_findings

Maps to: ISS-1, P1, P3
RED: `outcome: checks-passed` and `gate: review` output stay opaque.
GREEN: Passing outcome becomes accepted, while findings become typed blocking
evidence with finding id, severity, file, description, and action.

### test_no_mistakes_changes_require_supervisor_rerun

Maps to: ISS-1, P4
RED: A fake runner that changes files still returns accepted.
GREEN: Changed files or HEAD produce `changed_requires_rerun` and a blocked
receipt status.

### test_no_mistakes_clean_branch_runs_in_isolated_worktree

Maps to: ISS-1, P4
RED: no-mistakes executes in the active worktree for a clean committed branch.
GREEN: Runner cwd is a temporary detached Git worktree, mutation is detected,
and the active worktree is not changed.

### test_no_mistakes_runner_exception_cleans_isolated_worktree

Maps to: ISS-1, P1, P4
RED: An unexpected runner exception after worktree creation leaks the detached
worktree or crashes the workflow without a typed result.
GREEN: The adapter returns a failed required/advisory result, records the
exception reason, and removes the temporary validation worktree.

### test_workflow_kwargs_from_payload_preserves_reviewer_infra_retry_fields

Maps to: ISS-2, P2, P3
RED: Detached workflow CLI drops no-mistakes parameters from request payloads.
GREEN: `workflow_kwargs_from_payload` preserves policy, skip steps, and timeout
while still filtering irrelevant fields.

### test_run_dual_agent_workflow_records_advisory_no_mistakes_after_outcome_review

Maps to: ISS-2, P3
RED: Accepted workflow finishes without no-mistakes evidence or records it
before outcome review.
GREEN: Advisory validation records started and completed events, the command
skips push/pr/ci, no `--yes` is passed, and the event index follows accepted
`outcome_review`.

### test_required_no_mistakes_unavailable_blocks_without_rewriting_gate_acceptance

Maps to: ISS-2, P3, P4
RED: Required no-mistakes unavailability either passes finalization or rewrites
the accepted outcome-review gate.
GREEN: Workflow status is blocked at `no_mistakes_validation`, and the existing
`outcome_review` gate event remains accepted.

## RED/GREEN Plan

RED: Add adapter and config tests that fail because no no-mistakes module or
config section exists.
GREEN: Implement the external subprocess adapter, safe defaults, finding
parsing, missing-binary matrix, and isolated worktree mutation detection.

RED: Add workflow tests that fail because no post-acceptance events or detached
payload fields exist.
GREEN: Wire direct and durable workflow paths, write ledger events and receipts
after accepted `outcome_review`, and block only in required/shipping or stale
mutation cases.

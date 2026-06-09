# Implementation Plan: no-mistakes External Validator

## Files / Modules To Touch

- `supervisor/no_mistakes.py`
- `supervisor/config.py`
- `config.example.yaml`
- `mcp_tools/codex_supervisor_stdio.py`
- `mcp_tools/codex_supervisor_workflow_cli.py`
- `supervisor/state.py`
- `tests/test_no_mistakes.py`
- `tests/test_dual_agent_workflow_driver.py`

## Risks

- no-mistakes can mutate files or commits after supervisor acceptance, so the
  adapter must run clean-branch validation in an isolated worktree and mark any
  mutation as stale.
- A required external validator can become a local availability footgun, so the
  default policy must stay off and advisory missing-binary behavior must be
  non-fatal.
- Post-acceptance evidence could accidentally become gate authority, so the
  workflow must preserve existing gate results and only add a separate
  no-mistakes validation result.
- Detached durable workflow execution can drop optional fields unless the CLI
  payload whitelist is updated with the new no-mistakes parameters.

## Traceability

- P1 -> test_no_mistakes_adapter_builds_safe_default_command
- P1 -> test_no_mistakes_adapter_parses_outcome_and_gate_findings
- P2 -> test_no_mistakes_config_defaults_are_safe
- P2 -> test_no_mistakes_missing_binary_policy_matrix
- P2 -> test_workflow_kwargs_from_payload_preserves_reviewer_infra_retry_fields
- P3 -> test_run_dual_agent_workflow_records_advisory_no_mistakes_after_outcome_review
- P3 -> test_required_no_mistakes_unavailable_blocks_without_rewriting_gate_acceptance
- P4 -> test_no_mistakes_changes_require_supervisor_rerun
- P4 -> test_no_mistakes_clean_branch_runs_in_isolated_worktree

## Steps

1. Add RED tests for config defaults, adapter command construction, missing
   binary behavior, parsing, mutation detection, isolated worktree execution,
   durable payload preservation, and workflow ordering.
2. Implement `supervisor/no_mistakes.py` as an external subprocess adapter with
   typed results, safe command construction, finding parsing, Git snapshots,
   temporary detached worktree execution, and receipts.
3. Add `NoMistakesCfg` with safe defaults and document the example config.
4. Thread no-mistakes parameters through direct MCP workflow calls, durable job
   submission payloads, and CLI replay.
5. Invoke no-mistakes after accepted `outcome_review`, write ledger events and
   receipts, and block only for required/shipping failures or stale mutation.
6. Run focused workflow tests, MCP stdio tests, full pytest, and supervisor
   rigorous workflow with Cursor review.

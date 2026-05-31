# Test Evidence

Generated: 2026-05-31 15:37:00 PDT

## Full Suite

Command:

```bash
uv run --extra dev pytest -q
```

Result:

```text
527 passed in 129.89s (0:02:09)
```

## Focused Reviewer-Unavailable Recovery Suite

Command:

```bash
uv run --extra dev pytest -q \
  tests/test_dual_agent_workflow_driver.py::test_workflow_kwargs_from_payload_preserves_dynamic_workflow_preview_fields \
  tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_records_cursor_contract_failure_as_recoverable_infra \
  tests/test_dual_agent_workflow_driver.py::test_reviewer_unavailable_block_policy_stays_blocked_for_high_stakes \
  tests/test_dual_agent_workflow_driver.py::test_reviewer_unavailable_proceed_degraded_advances_with_degraded_receipt \
  tests/test_dual_agent_workflow_driver.py::test_reviewer_unavailable_default_escalates_and_resume_continue_advances \
  tests/test_dual_agent_workflow_driver.py::test_reviewer_unavailable_proceed_degraded_escalates_for_agentic_required \
  tests/test_dual_agent_workflow_driver.py::test_reviewer_unavailable_runtime_native_escalates \
  tests/test_dual_agent_workflow_driver.py::test_run_dual_agent_workflow_with_cursor_review_blocks_on_cursor_rejection
```

Result:

```text
.........                                                               [100%]
9 passed in 18.11s
```

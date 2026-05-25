# Triage: live-failure-mode-probe-20260525-01

- run_id: `live-failure-mode-probe-20260525-01`
- task_id: `live-failure-mode-probe-20260525-01`
- final_event_id: `5`
- policy_verdict: `blocked`
- claude_gate_status: `accepted`
- supervisor_final_status: `blocked`

## Root Cause

- failure_code: `workflow_claim_verification_failed`
- failure_category: `task_verification`
- failure_subcategory: `missing_or_stale_receipt`
- mast_code: `FM-3.2`
- mast_mode: `No or incomplete verification`

## Blocking Details

- tests_passed_without_test_receipt
- implemented_without_diff_receipt

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 5 | start_dual_agent_gate#1779727559892#32966571 |  |  | start_dual_agent_gate | completed | 32966 | 32966571 |  |  |  |  | {"gate": "outcome_review", "planning_artifact_count": 5, "task_id": "live-failure-mode-probe-20260525-01", "timeout_s": 900} | {"claude_gate_status": "accepted", "supervisor_final_status": "blocked"} |  |
| 3 | invoke_claude_lead#1779727559898#32954708 |  |  | invoke_claude_lead | completed | 32954 | 32954708 | 181258 | 1999 |  |  | {"attempt": 1, "budget_usd": 100.0, "corrective_retry": false, "expected_decisions": ["accept"], "expected_objections": [], "expected_specialists": ["Failure Probe Lead"], "gate": "outcome_review", "model": null, "quality": "best", "task_id": "live-failure-mode-probe-20260525-01", "timeout_s": 900} | {"cost_usd": 0.26538174999999997, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 2550, "tokens_in": 181258, "tokens_out": 1999} |  |
| 4 | invoke_cursor_agent#1779727592859#14996930 |  |  | invoke_cursor_agent | finished | 14996 | 14996930 |  |  |  | [] | {"claude_outcome_claim_count": 2, "expected_specialists": ["Cursor Reviewer"], "gate": "outcome_review", "task_id": "live-failure-mode-probe-20260525-01", "timeout_s": 300} | {"accepted": true, "outcome_present": true, "probe_reason": "cursor_review_ok", "probe_status": "green"} |  |
| 1 | validate_planning_artifacts#1779727559892#2231 |  |  | validate_planning_artifacts | green | 2 | 2231 |  |  | P_planning |  | {"artifact_count": 5, "gate": "outcome_review", "required_kinds": ["grill_findings", "implementation_plan", "issues", "prd", "tdd_plan"], "task_id": "live-failure-mode-probe-20260525-01"} | {"probe_id": "P_planning", "reason": "planning_validation_ok", "status": "green"} |  |
| 2 | validate_planning_artifacts#1779727559892#2231 |  |  | validate_planning_artifacts | green | 2 | 2231 |  |  | P_planning |  | {"artifact_count": 5, "gate": "outcome_review", "required_kinds": ["grill_findings", "implementation_plan", "issues", "prd", "tdd_plan"], "task_id": "live-failure-mode-probe-20260525-01"} | {"probe_id": "P_planning", "reason": "planning_validation_ok", "status": "green"} |  |

## Evidence Pointers

- [Interactions](interactions.md)
- [Transcript](transcript.md)
- [Machine Transcript](transcript.jsonl)
- [Replay Manifest](replay/manifest.json)
- [Source PRD](source/prd.md)
- [Source TDD](source/tdd.md)

## Next Safe Action

Provide matching test and git-diff receipts, then rerun outcome review. Missing evidence: `tests_passed_without_test_receipt`, `implemented_without_diff_receipt`.

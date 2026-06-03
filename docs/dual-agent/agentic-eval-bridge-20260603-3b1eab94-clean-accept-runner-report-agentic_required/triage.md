# Triage: agentic-eval-bridge-20260603-3b1eab94-clean-accept-runner-report-agentic_required

- run_id: `agentic-eval-bridge-20260603-3b1eab94-clean-accept-runner-report-agentic_required-c7fe3668058e`
- task_id: `agentic-eval-bridge-20260603-3b1eab94-clean-accept-runner-report-agentic_required`
- final_event_id: `462344`
- policy_verdict: `blocked`
- claude_gate_status: `blocked`
- supervisor_final_status: `blocked`

## Run Totals

- unique_tool_calls: `2`
- total_duration_ms: `513753`
- total_duration_us: `513754306`
- total_tokens_in: `0`
- total_tokens_out: `0`
- total_cost_usd: `0.0`

## Root Cause

- failure_code: `agentic_lead_policy_blocked`
- failure_category: `system_design`
- failure_subcategory: `resource_contention`
- mast_code: ``
- mast_mode: ``

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 462340 | produce_agentic_worker_receipts#1780502580505#513753794 |  |  | produce_agentic_worker_receipts | passed | 513753 | 513753794 |  |  |  | [] | {"agentic_lead_policy": "required", "existing_receipt_count": 0, "min_subagents": 1, "required_roles": [], "run_id": "agentic-eval-bridge-20260603-3b1eab94-clean-accept-runner-report-agentic_required-c7fe3668058e", "task_id": "agentic-eval-bridge-20260603-3b1eab94-clean-accept-runner-report-agentic_required"} | {"blocking_findings": [], "receipt_count": 1, "status": "passed"} |  |
| 462343 | verify_dynamic_workflow_receipts#1780503094268#512 |  |  | verify_dynamic_workflow_receipts | red | 0 | 512 |  |  | P13 | ["agentic-worker-audit-1"] | {"agentic_lead_policy": "required", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "min_subagents": 1, "receipt_count": 1, "required_evidence_grade": "self_reported", "required_roles": [], "task_id": "agentic-eval-bridge-20260603-3b1eab94-clean-accept-runner-report-agentic_required"} | {"missing_gates": [], "probe_id": "P13", "reason": "agentic_lead_policy_blocked", "status": "red", "verified_gates": []} | agentic_lead_policy_blocked |

## Evidence Pointers

- [Interactions](interactions.md)
- [Transcript](transcript.md)
- [Machine Transcript](transcript.jsonl)
- [MAST Coverage](mast-coverage.md)
- [Replay Manifest](replay/manifest.json)
- [Source PRD](source/prd.md)
- [Source PRD Grill Findings](source/grill-findings.md)
- [Source Issues](source/issues.md)
- [Source TDD](source/tdd.md)
- [Source TDD Grill Findings](source/grill-findings-tdd.md)
- [Source Implementation Plan](source/implementation-plan.md)

## Next Safe Action

Inspect the failure event, resolve the named taxonomy blocker, then rerun the blocked gate.

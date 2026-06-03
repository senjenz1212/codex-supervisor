# Triage: agentic-eval-bridge-20260603-3b1eab94-planning-artifact-deny-lead_direct

- run_id: `agentic-eval-bridge-20260603-3b1eab94-planning-artifact-deny-lead_direct-2158855a6733`
- task_id: `agentic-eval-bridge-20260603-3b1eab94-planning-artifact-deny-lead_direct`
- final_event_id: `462351`
- policy_verdict: `blocked`
- claude_gate_status: `blocked`
- supervisor_final_status: `blocked`

## Run Totals

- unique_tool_calls: `4`
- total_duration_ms: `4`
- total_duration_us: `5074`
- total_tokens_in: `0`
- total_tokens_out: `0`
- total_cost_usd: `0.0`

## Root Cause

- failure_code: `planning_validation_failed`
- failure_category: `system_design`
- failure_subcategory: `artifact_quality`
- mast_code: `FM-1.1`
- mast_mode: `Disobey task specification`

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 462351 | start_dual_agent_gate#1780503097515#4532 |  |  | start_dual_agent_gate | completed | 4 | 4532 |  |  |  |  | {"agentic_lead_policy": "off", "artifact_policy": "strict", "dynamic_workflow_task_class": null, "execution_layer_mode": "lead_direct", "gate": "prd_review", "min_subagents": 0, "planning_artifact_count": 6, "required_evidence_grade": "self_reported", "required_roles": [], "screenshot_count": 0, "task_id": "agentic-eval-bridge-20260603-3b1eab94-planning-artifact-deny-lead_direct", "user_facing": false} | {"claude_gate_status": "blocked", "probe_statuses": {"P_planning": "red"}, "supervisor_final_status": "blocked"} |  |
| 462349 | validate_planning_artifacts#1780503097516#542 |  |  | validate_planning_artifacts | red | 0 | 542 |  |  | P_planning |  | {"artifact_count": 6, "gate": "prd_review", "required_kinds": ["prd"], "task_id": "agentic-eval-bridge-20260603-3b1eab94-planning-artifact-deny-lead_direct"} | {"probe_id": "P_planning", "reason": "planning_validation_failed", "status": "red"} | planning_validation_failed |
| 462350 | validate_planning_artifacts#1780503097516#542 |  |  | validate_planning_artifacts | red | 0 | 542 |  |  | P_planning |  | {"artifact_count": 6, "gate": "prd_review", "required_kinds": ["prd"], "task_id": "agentic-eval-bridge-20260603-3b1eab94-planning-artifact-deny-lead_direct"} | {"probe_id": "P_planning", "reason": "planning_validation_failed", "status": "red"} | planning_validation_failed |
| 462351 | probe_p_planning#1780503097519#0#p_planning | start_dual_agent_gate#1780503097515#4532 |  | probe:P_planning | red | 0 | 0 |  |  | P_planning |  | {"probe_id": "P_planning"} | {"probe_id": "P_planning", "reason": "planning_validation_failed", "status": "red"} | planning_validation_failed |
| 462352 | record_gate_round#1780503097529#0 |  |  | record_gate_round | recorded | 0 | 0 |  |  |  |  | {"claude_decision": "revise", "codex_decision": "deny", "gate": "prd_review", "round_index": 1, "task_id": "agentic-eval-bridge-20260603-3b1eab94-planning-artifact-deny-lead_direct"} | {"claude_decision": "revise", "codex_decision": "deny", "has_objection": true, "round_index": 1} |  |

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

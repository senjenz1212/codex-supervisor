# Triage: supervisor-mcp-reliability-20260528

- run_id: `supervisor-mcp-reliability-20260528`
- task_id: `supervisor-mcp-reliability-20260528`
- final_event_id: `264817`
- policy_verdict: `observed`
- claude_gate_status: `accepted`
- supervisor_final_status: `accepted`

## Run Totals

- unique_tool_calls: `81`
- total_duration_ms: `2509112`
- total_duration_us: `2509130639`
- total_tokens_in: `14226832`
- total_tokens_out: `110670`
- total_cost_usd: `20.489948`

## Root Cause

- No blocking failure taxonomy recorded.

## Blocking Details

- None recorded.

## Slowest Tool Calls

| event | tool_call_id | parent_tool_call_id | references_tool_call_id | name | status | duration_ms | duration_us | tokens_in | tokens_out | probe_id | receipt_ids | args | result_summary | error |
|---:|---|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|
| 264739 | start_dual_agent_gate#1779993571756#262215943 |  |  | start_dual_agent_gate | completed | 262215 | 262215943 |  |  |  |  | {"artifact_policy": "strict", "gate": "implementation_plan", "planning_artifact_count": 5, "screenshot_count": 0, "task_id": "supervisor-mcp-reliability-20260528", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |
| 264738 | invoke_claude_lead#1779993571763#262195983 |  |  | invoke_claude_lead | completed | 262195 | 262195983 | 1153870 | 9736 | P3 |  | {"attempt": 1, "budget_usd": 40.0, "corrective_retry": false, "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "implementation_plan", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "supervisor-mcp-reliability-20260528", "timeout_s": 900} | {"cost_usd": 1.70280555, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 11297, "tokens_in": 1153870, "tokens_out": 9736} |  |
| 264817 | start_dual_agent_gate#1779994132826#250151590 |  |  | start_dual_agent_gate | completed | 250151 | 250151590 |  |  |  |  | {"artifact_policy": "strict", "gate": "outcome_review", "planning_artifact_count": 5, "screenshot_count": 0, "task_id": "supervisor-mcp-reliability-20260528", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |
| 264816 | invoke_claude_lead#1779994132833#250134001 |  |  | invoke_claude_lead | completed | 250134 | 250134001 | 1039527 | 10796 | P3 |  | {"attempt": 1, "budget_usd": 40.0, "corrective_retry": false, "expected_decisions": [], "expected_objections": [], "expected_specialists": [], "explicit_model": null, "gate": "outcome_review", "model": "opus", "model_source": "quality_default:best", "quality": "best", "requested_model": "opus", "task_id": "supervisor-mcp-reliability-20260528", "timeout_s": 900} | {"cost_usd": 1.5773877500000002, "model": "opus", "outcome_present": true, "probe_id": "P3", "probe_reason": "outcome_fidelity_ok", "probe_status": "green", "stderr_bytes": 0, "stdout_bytes": 8591, "tokens_in": 1039527, "tokens_out": 10796} |  |
| 264787 | start_dual_agent_gate#1779993928880#195154466 |  |  | start_dual_agent_gate | completed | 195154 | 195154466 |  |  |  |  | {"artifact_policy": "strict", "gate": "execution", "planning_artifact_count": 5, "screenshot_count": 0, "task_id": "supervisor-mcp-reliability-20260528", "user_facing": false} | {"claude_gate_status": "accepted", "probe_statuses": {"P1": "green", "P2": "green", "P3": "green", "P_planning": "green"}, "supervisor_final_status": "accepted"} |  |

## Evidence Pointers

- [Interactions](interactions.md)
- [Transcript](transcript.md)
- [Machine Transcript](transcript.jsonl)
- [MAST Coverage](mast-coverage.md)
- [Replay Manifest](replay/manifest.json)
- [Source PRD](source/prd.md)
- [Source TDD](source/tdd.md)

## Next Safe Action

Inspect the latest gate result and replay manifest before advancing.

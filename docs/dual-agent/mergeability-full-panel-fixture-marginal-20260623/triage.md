# Triage: mergeability-full-panel-fixture-marginal-20260623

- run_id: `4b6e2f22-b758-4002-be04-c92c639305a0`
- task_id: `mergeability-full-panel-fixture-marginal-20260623`
- final_event_id: `880397`
- policy_verdict: `blocked`
- claude_gate_status: `blocked`
- supervisor_final_status: `blocked`

## Run Totals

- unique_tool_calls: `0`
- total_duration_ms: `0`
- total_duration_us: `0`
- total_tokens_in: `0`
- total_tokens_out: `0`
- total_cost_usd: `0`

## Root Cause

- failure_code: `missing_prd_tdd_skill_receipts`
- failure_category: `governance`
- failure_subcategory: `missing_skill_provenance`
- mast_code: ``
- mast_mode: ``

## Blocking Details

- None recorded.

## Slowest Tool Calls

- None recorded.

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

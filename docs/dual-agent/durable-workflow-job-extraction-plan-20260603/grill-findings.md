# Grill Findings

These findings are derived from dual-agent gate objections in the ledger.
Future duo-agent runs should also create this file through the `prd-to-tdd` skill's `grill-with-docs` gates before implementation.

- event_id 474656 `prd_review`: CLI path under-qualified: PRD line 95 says codex_supervisor_workflow_cli.py; actual is mcp_tools/codex_supervisor_workflow_cli.py (module mcp_tools.codex_supervisor_workflow_cli, stdio:2123)
- event_id 474656 `prd_review`: shasum of source artifacts not re-run (Bash approval declined); handoff hashes accepted on read-consistency
- event_id 474656 `prd_review`: Target design doc docs/durable-workflow-job-extraction-plan.md already exists on disk (14423B), indicating the run already executed; does not affect the PRD under review
- event_id 474657 `prd_review`: both agents accepted
- event_id 474691 `issues_review`: Non-blocking: issues.md acceptance-criteria checkboxes are all unchecked, but this is spec form; satisfaction is verified in the target doc not the checkbox state
- event_id 474691 `issues_review`: Non-blocking: Slice 4 AC2 (accept-through-outcome) and AC4 (four read-only worker receipts + config-default invariant) are gate-level evidence deferred to the outcome gate, not verifiable at issues_review
- event_id 474691 `issues_review`: Non-blocking: shasum of planning artifacts not re-run (no Bash approval sought); handoff hashes accepted on read-consistency
- event_id 474691 `issues_review`: Non-blocking: handoff packet lists grill-findings-tdd.md twice (same sha256), a benign duplicate
- event_id 474692 `issues_review`: both agents accepted
- event_id 474917 `tdd_review`: both agents accepted
- event_id 474955 `implementation_plan`: The 4 traceability tests are doc-review RED/GREEN assertions in tdd.md, not executable pytest functions (grep 0 matches) - disclosed/correct for doc-only, anchored via grill-findings-tdd Finding 1 to the 5 real tests.
- event_id 474955 `implementation_plan`: Plan 'Files To Touch' omits itself (implementation-plan.md) and the .scratch run config (nits).
- event_id 474955 `implementation_plan`: P4 (4 read-only worker receipts) is supervised-workflow/gate evidence, properly deferred to outcome gate.
- event_id 475093 `implementation_plan`: both agents accepted
- event_id 475127 `execution`: both agents accepted
- event_id 475313 `outcome_review`: both agents accepted

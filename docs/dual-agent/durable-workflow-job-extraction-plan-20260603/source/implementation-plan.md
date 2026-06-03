# Implementation Plan: Durable Workflow Job Extraction Plan

Task id: `durable-workflow-job-extraction-plan-20260603`

## Files / Modules To Touch

- `docs/durable-workflow-job-extraction-plan.md`
- `docs/dual-agent/durable-workflow-job-extraction-plan-20260603/source/prd.md`
- `docs/dual-agent/durable-workflow-job-extraction-plan-20260603/source/grill-findings.md`
- `docs/dual-agent/durable-workflow-job-extraction-plan-20260603/source/issues.md`
- `docs/dual-agent/durable-workflow-job-extraction-plan-20260603/source/tdd.md`
- `docs/dual-agent/durable-workflow-job-extraction-plan-20260603/source/grill-findings-tdd.md`

No source modules are touched in this run. The proposed follow-up module
`supervisor/durable_workflow_job.py` is described but not created here.

## Steps

1. Inventory the public durable workflow-job methods and collaborators:
   submit, poll, catch-up, run workflow, detached CLI, idempotency, terminal
   outcome reconciliation, state storage, and ledger events.
2. Synthesize the proposed module boundary into a design doc under `docs/`.
3. List move-vs-keep decisions with the rule that implementation logic can move
   while MCP methods remain thin adapters.
4. List existing tests that pin current behavior before any code extraction.
5. Run planning validation, targeted durable workflow-job tests, and the
   supervised durable fan-out gate.

## Risks

- The extracted service could accidentally change payload shape if adapter
  wrappers are not kept byte-compatible.
- Terminal outcome reconciliation could regress if ledger-first behavior and
  discrepancy events are not preserved.
- CLI fallback could drift from MCP behavior if request payload filtering is
  split without a shared helper.
- The next slice could overreach into `State` schema changes unless this plan
  keeps storage ownership explicit.

## Traceability

- P1 is covered by
  `test_durable_workflow_job_plan_lists_public_transport_surface`.
- P2 is covered by
  `test_durable_workflow_job_plan_preserves_mcp_adapter_boundary`.
- P3 is covered by
  `test_durable_workflow_job_plan_names_behavior_pinning_tests`.
- P4 and P5 are covered by `test_durable_workflow_job_plan_is_doc_only` and by
  the supervised workflow's read-only worker receipts.

## Rollback

Because this run is documentation-only, rollback is a revert of the committed
docs. No runtime path, config default, state table, or MCP tool registration is
changed by this slice.

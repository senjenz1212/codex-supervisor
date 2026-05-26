# TDD Plan: Tri-Agent Borrowed Patterns

## Public Boundary

Primary boundary: `run_dual_agent_workflow` in the Codex supervisor MCP server.

Secondary pure boundaries:

- `codex_review_packet`
- `summarize_probe_cohort`
- `stability_proposals_for_cohort`
- `run_forward_migrations`
- `check_replay_schema_versions`

## Test Slices

1. Route selection helper returns a trivial route with reduced gates and no skill receipts.
2. MCP workflow records the route and executes only the selected gates for trivial work.
3. Small route requires only PRD-stage skill receipts and executes PRD review, execution, and outcome review.
4. Vague route can force Cursor review while default large behavior remains unchanged.
5. Route inference and aliases are deterministic.
6. Codex review packet emits CRITICAL/IMPORTANT findings and a force-next-round policy.
7. Workflow-level round policy blocks acceptance when a review packet carries a blocking finding.
8. Receipt replay rejects implementation receipts with no changed-file list.
9. Receipt replay rejects partial changed-file coverage and vague substring claim matches.
10. Probe cohort aggregation classifies repeated trials as STABLE, DRIFT-1, or FLIPPING.
11. Probe cohort wrapper writes concrete artifacts with trial-local task/run IDs.
12. SP proposals are deterministic, proposed-only, and evidence-linked.
13. State migrations apply forward once and fail closed on version/name mismatch or unknown future versions.
14. Replay version checker accepts current exported versions, maps known forward migrations, and rejects unknown future versions.

## Verification Commands

```bash
uv run pytest -q tests/test_dual_agent_workflow_driver.py tests/test_agent_mailbox.py tests/test_probe_cohorts.py tests/test_stability_proposals.py tests/test_schema_migrations.py tests/test_version_drift_replay.py
uv run pytest -q tests/test_codex_supervisor_mcp_stdio.py tests/test_dual_agent_artifacts.py tests/test_failure_taxonomy.py tests/test_dual_agent_runner.py tests/test_drift_replay.py
python3 -m compileall -q supervisor mcp_tools scripts tests
git diff --check
```

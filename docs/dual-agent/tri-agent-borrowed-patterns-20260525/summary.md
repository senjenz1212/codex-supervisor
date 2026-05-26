# Tri-Agent Borrowed Patterns Summary

## Implemented

- Task-complexity routing for `run_dual_agent_workflow`.
- Route ledger event: `dual_agent_workflow_route`.
- Severity-ranked Codex review packet findings with `round_policy`.
- Receipt replay hardening for changed-file coverage and exact claim matching.
- Deterministic probe cohort aggregation.
- Review-only SP-N stability proposal generation.
- Forward-only state migration registry.
- Replay schema version-drift checker.
- Cohort wrapper script for repeated live failure-mode probes.
- Second-pass rigorous tri-agent review fixes for route coverage, workflow-level severity safety, cohort artifact output, migration fail-closed behavior, replay manifest aliases, and trial-local live probe IDs.

## Verification

Focused deterministic checks passed:

```bash
uv run pytest -q tests/test_dual_agent_workflow_driver.py tests/test_agent_mailbox.py tests/test_probe_cohorts.py tests/test_stability_proposals.py tests/test_schema_migrations.py tests/test_version_drift_replay.py
uv run pytest -q tests/test_codex_supervisor_mcp_stdio.py tests/test_dual_agent_artifacts.py tests/test_failure_taxonomy.py tests/test_dual_agent_runner.py tests/test_drift_replay.py
```

Live cohort probes were not run in this slice.

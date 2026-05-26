# Tri-Agent Interaction Log

This file records the review interaction for the tri-agent borrowed-patterns slice. The raw subagent conversation happened in the Codex thread; this artifact is the durable repo-local projection for review.

## Roles

| Agent | Role | Write access | Scope |
|---|---|---:|---|
| Codex | Lead implementer and reconciler | Yes | Implement fixes, run verification, reconcile reviewer findings |
| Darwin | PRD/TDD and artifact auditor | No | Review PRD, issues, TDD, coverage docs, and proof quality |
| Huygens | Implementation and regression auditor | No | Review runtime wiring, migrations, replay versions, scripts, and regression risks |

## Interaction Timeline

### 1. Lead Assignment

Codex assigned two read-only review streams:

- Darwin: verify whether artifacts and tests prove task-complexity routing, severity-aware packets, multi-trial cohorts, SP-N proposals, migrations, and version-drift checks.
- Huygens: verify runtime wiring and regressions in MCP workflow, workflow helpers, review packets, migrations, cohorts, replay versions, and scripts.

### 2. Darwin Review

Darwin returned six findings:

| Severity | Finding | Resolution |
|---|---|---|
| High | Routing proof only covered trivial + vague, not small/large/default/inference. | Added small/default/inference tests and transcript-visible route assertions. |
| High | Severity safety pairing was not proven at workflow boundary. | Added workflow regression proving blocking `round_policy` forces `codex_decision=revise`. |
| High | Cohorts and SP-N were helper-tested, not artifact-proven. | Added cohort wrapper artifact-output test with concrete summary/proposal files. |
| Medium | Migrations did not fail closed on unknown future versions. | Added fail-closed check and regression test for `version=99`. |
| Medium | Version-drift checks were pure dict checks, not tied to exported manifests. | Added exported-manifest compatibility assertion through artifact export test. |
| Medium | Coverage docs overclaimed borrowed-pattern coverage. | Updated coverage index with rows for routing, review packets, cohorts/SP-N, migrations, and replay version drift. |

### 3. Huygens Review

Huygens returned five findings:

| Severity | Finding | Resolution |
|---|---|---|
| P1 | Version-drift replay rejected real exported manifests because exported keys are `replay_manifest` and `agent_interaction`. | Added schema aliases and a real-key regression test. |
| P1 | Forward-only migrations accepted unknown future rows. | Added unknown-version fail-closed behavior and test. |
| P1 | Cohort probe isolation was path-only; child probes kept static task/run IDs. | Added `--task-id` and `--run-id` to the live probe and passed trial-local IDs from the cohort wrapper. |
| P2 | Public MCP `start_dual_agent_gate` did not expose route-specific requirement overrides. | Added public wrapper params and a direct MCP regression test. |
| P2 | Large route skill receipts can block direct callers unexpectedly. | Documented as intentional rigor; added route-specific tests proving trivial/small/large behavior. |

### 4. Lead Reconciliation

Codex implemented the agreed changes:

- Route tests for trivial, small, default large, vague, inference, aliases, and transcript visibility.
- Workflow-level `round_policy.force_next_round` regression.
- Cohort wrapper artifact-output and trial identity regression.
- Live failure probe `--task-id` / `--run-id`.
- Schema migration fail-closed behavior for future versions.
- Replay schema aliases for exported manifest keys.
- Public MCP forwarding for route-specific requirements.
- Coverage and PRD/TDD artifact updates.

### 5. Verification

Final verification run:

```bash
uv run pytest -q
```

Result:

```text
455 passed
```

Additional checks:

```bash
python3 -m compileall -q supervisor mcp_tools scripts tests
git diff --check
python3 scripts/probe_live_failure_mode.py --help
python3 scripts/probe_live_failure_mode_cohort.py --help
```

All passed. Changed-file secret scan was clean.

## Open Evidence Boundary

No live 3-trial Claude/Cursor cohort was run in this review pass. The cohort wrapper and aggregation are deterministically tested, but new live cohort evidence remains a separate operational run.

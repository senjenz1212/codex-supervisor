# Dual-Agent Planning Validator

## Purpose

The planning validator prevents dual-agent gates from advancing on
artifact-shaped source docs. Presence checks and handoff checksums prove that a
file exists and was not mutated by the worker; this validator proves the source
artifact has enough deterministic substance to be a real planning artifact.

## Boundary

The validator consumes explicit `PlanningArtifact` paths already passed to
`start_dual_agent_gate` / `run_dual_agent_workflow`. It does not infer a second
path convention. Paths still flow into the existing handoff packet and checksum
boundary.

## Determinism Constraint

No check uses an LLM. All checks are regex, section parsing, count thresholds,
hash comparison, or deterministic cross-reference resolution. If a check cannot
be replayed from fixture files without live model calls, it does not ship.

## Receipt Event

Planning validation writes this event when the runner receives a `State` handle:

```json
{
  "kind": "dual_agent_planning_validation",
  "payload": {
    "task_id": "planning-artifact-validator-20260524",
    "gate": "execution",
    "validator_version": "1.0.0",
    "artifact_hashes": {
      "prd": "..."
    },
    "checks": {
      "PRD-001": "pass",
      "PRD-005": "fail: only 1 acceptance criterion, minimum is 3"
    },
    "verdict": "accepted"
  }
}
```

## Required Kinds By Gate

| Gate | Required Planning Kinds |
|---|---|
| `prd_review` | `prd` |
| `issues_review` | `prd`, `issues`, `grill_findings` |
| `tdd_review` | `prd`, `issues`, `tdd_plan`, `grill_findings` |
| `implementation_plan` | `prd`, `issues`, `tdd_plan`, `grill_findings`, `implementation_plan` |
| `execution` | `prd`, `issues`, `tdd_plan`, `grill_findings`, `implementation_plan` |
| `outcome_review` | `prd`, `issues`, `tdd_plan`, `grill_findings`, `implementation_plan` |

## Check IDs

### PRD

- `PRD-001`: no `draft_stub`, auto-seed marker, or generated-workflow marker.
- `PRD-002`: no blocked stub phrases such as `TBD`, `TODO`, `placeholder`, `future runs should`, or `No intent supplied`.
- `PRD-003`: required sections exist: Problem Statement, Solution, User Stories, PRD Promise Contracts, Implementation Decisions, Testing Decisions, Out of Scope.
- `PRD-004`: required section bodies have minimum substance.
- `PRD-005`: at least three concrete PRD promise contracts or acceptance criteria exist.
- `PRD-006`: body has enough non-template token variety to differ from a seed template.

### Issues

- `ISS-001`: at least two implementation slices exist.
- `ISS-002`: each slice has title, scope, and acceptance criteria.
- `ISS-003`: slices reject future/someday/TBD-only language.
- `ISS-004`: each slice has priority or estimate.

### TDD

- `TDD-001`: at least two concrete test names exist.
- `TDD-002`: RED and GREEN plans are both present.
- `TDD-003`: tests map to an issue or PRD promise.
- `TDD-004`: test names match `test_*`, `it should *`, or `should *` and are not numeric placeholders.

### Grill Findings

- `GRILL-001`: findings use `status: open|resolved|waived`.
- `GRILL-002`: open findings block.
- `GRILL-003`: waived findings require non-empty reason text.

### Implementation Plan

- `PLAN-001`: files or modules to touch are listed.
- `PLAN-002`: risks are listed.
- `PLAN-003`: traceability block exists.
- `PLAN-004`: traceability references resolve to actual PRD promise IDs and TDD test names.

### Aggregate

- `AGG-001`: required artifact kinds are present.
- `AGG-002`: different artifact kinds do not share byte-identical or nearly empty bodies.

## Fixtures

Each artifact kind has three fixture files under
`tests/fixtures/planning_validator/<kind>/`:

- `good.md`: should pass.
- `stub.md`: obvious auto-seeded or placeholder content; should fail.
- `sneaky.md`: headings and shape exist but section bodies are empty, cloned, `TBD`, or shallow; should fail.

The sneaky fixtures are the design guardrail. A validator that only catches
obvious stubs is not sufficient.

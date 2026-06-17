# TDD Grill Findings: AutoResearch Evaluator Quality Foundation

## Context

The TDD plan was grilled against PRD promises, public-boundary requirements, and the known failure where saturated evaluator execution produces accepted report-only evidence but no proposal. All findings are resolved before implementation launch.

### Finding 1: First RED tests must hit derivation, not control helpers

status: resolved

Risk: A helper-level control test could pass while `derive_policy_evolution_proposals_from_report` still accepts saturated reports.

Resolution: The first three tests target policy derivation directly for no-op, harmful, and known-good control scenarios.

### Finding 2: The saturated replay regression must stay explicit

status: resolved

Risk: The implementation could improve new controls while losing the observed `1.0, 1.0, 1.0` regression case.

Resolution: The TDD plan includes `test_autoresearch_saturated_zero_variance_replay_stays_non_applyable` as a named regression.

### Finding 3: Determinism needs execution evidence

status: resolved

Risk: A test that only checks metadata would make self-certification look trustworthy.

Resolution: The TDD plan requires repeated evaluator execution and output hash comparison, plus a separate test proving self-declared metadata is not authoritative.

### Finding 4: Report-only invariants require a success-path test

status: resolved

Risk: Authority drift may appear only when controls pass and a proposal is drafted.

Resolution: The TDD plan includes a success-path invariant test that checks `default_change_allowed=false`, `policy_mutated=false`, `gate_advanced=false`, and operator approval requirement.

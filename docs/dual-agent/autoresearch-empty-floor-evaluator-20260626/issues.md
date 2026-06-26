# Issue Slices

The issue pack uses 11-block tracer bullets. Each slice is independently verifiable through a public boundary and preserves the PRD promise contract it claims.

## ISS-1: Measure Empty-Floor Execution in the Evaluator

1. Title: Measure stripped-overlay empty-floor metric
2. PRD promise: P1
3. Public boundary: `run_evaluator_trials`
4. Chosen seam: evaluator execution result and run artifact
5. Representative action: run a live evaluator fixture with `metric_before=0.0` seed and a candidate `policy-overlay.yaml`
6. What to build: run the evaluator once against a copy of the attempt worktree where the candidate policy overlay is empty, then expose the measured floor as execution evidence
7. Acceptance criteria: `execution.metric_before` is the measured floor; `execution.metric_after` is candidate median; `execution.metric_delta` is after minus before; evaluator artifact includes `empty_floor_metric`
8. First RED test: `tests/test_empty_floor_evaluator.py::test_empty_floor_metric_populated_from_real_run`
9. Minimal GREEN: add execution-result fields and one stripped-overlay evaluator run below `run_evaluator_trials`
10. Forbidden outcomes: using the seed as the floor; helper-only testing; mergeability benchmark changes
11. Dependencies and evidence: source evidence at `evaluator.py:39-45`, `evaluator.py:779-785`, `generator.py:499-516`

## ISS-2: Preserve Metric Fields Through Durable Jobs and Orchestration

1. Title: Propagate execution-derived metric before/after/delta
2. PRD promise: P2
3. Public boundary: `run_autoresearch_fixture` live execution path
4. Chosen seam: `EvaluatorExecutionResult` terminal payload and `replace(attempt, ...)`
5. Representative action: run a live fixture through durable execution and inspect the report record
6. What to build: serialize and deserialize metric before/after/delta in durable job payloads, then propagate them into the attempt before validation
7. Acceptance criteria: report record has execution-derived `metric_before`, `metric_after`, `metric_delta`, and non-`None` `empty_floor_comparison`
8. First RED test: `tests/test_empty_floor_evaluator.py::test_orchestrator_propagates_metric_before_after_delta`
9. Minimal GREEN: extend durable payload helpers and the orchestrator replacement call
10. Forbidden outcomes: validation recomputing from fixture values; dropping fields on resume; changing authority flags
11. Dependencies and evidence: source evidence at `durable_jobs.py:278-344`, `orchestrator.py:120-145`, `schema.py:289-298`

## ISS-3: Derive Draft Proposal From a Live Empty-Floor Run

1. Title: Live evaluator evidence yields draft-only policy proposal
2. PRD promise: P3
3. Public boundary: `derive_policy_evolution_proposals_from_report`
4. Chosen seam: live fixture report to policy deriver
5. Representative action: run live evaluator fixture with policy overlay candidate and quality controls, then derive proposals from the emitted report
6. What to build: ensure the live report record contains enough measured metric and quality evidence for the existing deriver to draft a proposal
7. Acceptance criteria: report has one `derived_policy_proposals` entry with `status == "draft"`; emitted proposal keeps `requires_operator_approval=true`, `default_change_allowed=false`, `automatic_policy_mutation=false`, `gate_advanced=false`
8. First RED test: `tests/test_empty_floor_evaluator.py::test_live_run_yields_draft_proposal_with_quality_controls`
9. Minimal GREEN: no new policy authority path; only feed existing deriver measured execution evidence
10. Forbidden outcomes: autonomous apply; mutating `.supervisor/policy-overlay.yaml`; benchmark bridge work; silent mocking above the public boundary
11. Dependencies and evidence: source evidence at `policy_evolution.py:76-164`, `policy_evolution.py:522-604`, `policy_evolution.py:694-721`, `policy_evolution.py:827-837`

## Coverage

- P1 covered by ISS-1.
- P2 covered by ISS-2.
- P3 covered by ISS-3.
- Out of scope: mergeability benchmark and autonomous benchmark-to-policy promotion bridge.

## Gate Receipt

`issues_review` accepted. All promises have issue claimants, first RED tests, allowed outcomes, and forbidden outcomes.

# TDD Plan

## Discipline

Use one RED, one minimal GREEN, then repeat. The first RED must hit the public boundary `run_evaluator_trials`. Live infrastructure may be faked below that boundary by writing evaluator fixture scripts and using a temporary `State`, but the tests must call the real evaluator runner, durable job path, orchestrator fixture runner, validation, report, and policy deriver.

## Cycle 1: Empty-Floor Metric Is Execution Evidence

RED: add `tests/test_empty_floor_evaluator.py::test_empty_floor_metric_populated_from_real_run`.

Expected RED reason: `EvaluatorExecutionResult` does not expose `metric_before`, `metric_after`, or `metric_delta`, and the evaluator artifact does not include `empty_floor_metric`.

Minimal GREEN:

- Add metric fields to `EvaluatorExecutionResult`.
- In `run_evaluator_trials`, detect policy overlay candidate refs, copy the attempt worktree, empty the candidate overlay artifact, run the evaluator process against that stripped worktree, and record the measured floor.
- Persist empty-floor evidence in evaluator progress and reload it on resume before continuing candidate trials.
- Compute candidate median as `metric_after` after live trials.
- Compute `metric_delta` from measured before/after.
- Keep non-policy attempts from fabricating an empty-floor comparison.

Assertions:

- The evaluator fixture sees an empty candidate file during the empty-floor run and non-empty candidate content during candidate trials.
- `execution.metric_before == 0.4`, not the seed `0.0`.
- `execution.metric_after == 0.75`.
- `execution.metric_delta == 0.35`.
- The evaluator run artifact contains `empty_floor_metric: 0.4`.
- The evaluator progress file contains `empty_floor_metric: 0.4`, so evaluator-level resume cannot fall back to the pending seed.

## Cycle 2: Durable Orchestration Propagates Metric Fields

RED: add `tests/test_empty_floor_evaluator.py::test_orchestrator_propagates_metric_before_after_delta`.

Expected RED reason: durable terminal payloads and `run_autoresearch_fixture` do not propagate the execution-derived fields, so the report record cannot produce an execution-derived `empty_floor_comparison`.

Minimal GREEN:

- Add metric fields to durable job terminal payload serialization and deserialization.
- Add `metric_before`, `metric_after`, and `metric_delta` to the orchestrator `replace(attempt, ...)` call after durable execution.
- Assert the durable result file also stores the same before/after/delta fields.
- Preserve existing event and report-only authority fields.

Assertions:

- `report["records"][0]["metric_before"] == 0.4`.
- `metric_after == 0.75`.
- `metric_delta == 0.35`.
- `empty_floor_comparison` matches those values and uses `metric_source == "evaluator_execution"`.

## Cycle 3: Live Run Derives Draft Proposal With Quality Controls

RED: add `tests/test_empty_floor_evaluator.py::test_live_run_yields_draft_proposal_with_quality_controls`.

Expected RED reason: the live report lacks a measured empty-floor comparison and does not reach the derivable policy proposal path from execution evidence alone.

Minimal GREEN:

- Feed the measured attempt into evaluator-quality controls so control deltas use the measured baseline instead of a pending seed.
- Ensure the report record contains policy overlay candidate refs, artifact hashes, evaluator-quality controls, and measured metric fields.
- Let the existing deriver create draft proposals; do not add a new apply path.

Assertions:

- The live report record is accepted.
- `report["derived_policy_proposals"][0]["status"] == "draft"`.
- The emitted `autoresearch_policy_proposal_created` event has `source == "autoresearch_deriver"`.
- `requires_operator_approval is True`.
- `default_change_allowed is False`.
- `automatic_policy_mutation is False`.
- `gate_advanced is False`.
- The live target `.supervisor/policy-overlay.yaml` content is unchanged.

## Cycle 4: Empty-Floor Failure Does Not Reuse The Seed

RED: add `tests/test_empty_floor_evaluator.py::test_empty_floor_failure_does_not_reuse_seed_as_floor`.

Expected RED reason: an empty-floor evaluator failure escapes before candidate trials, so the runner cannot show whether it avoided reusing the pending `0.0` seed.

Minimal GREEN:

- Treat an empty-floor evaluator failure as execution error evidence.
- Continue candidate trials when the candidate evaluator can still run.
- Leave `metric_before` and `metric_delta` unset rather than falling back to `attempt.metric_before` when the attempt source is `pending`.
- Preserve fixture-sourced baselines only when execution has no replacement and the attempt source is not `pending`.

Assertions:

- Candidate `metric_trials` still come from live evaluator execution.
- `execution.metric_before is None`, not `0.0`.
- The evaluator artifact has no `empty_floor_metric` or `metric_before`.
- The auto-evolution loop uses quality controls relative to the measured floor and reports `metric_before == 0.7`.

## Focused Test Command

`.venv/bin/python -m pytest tests/test_empty_floor_evaluator.py -q`

## Broader Regression Command

`.venv/bin/python -m pytest tests/test_empty_floor_evaluator.py tests/test_autoresearch.py tests/test_autoresearch_policy_evolution.py tests/test_auto_evolution_loop.py -q`

## Gate Receipt

`tdd_review` accepted after TDD grill findings and tri-agent A/B revisions were resolved.
